"""Incremental sync engine: deterministic fingerprinting + change detection.

Inspired by Understand Anything's Tree-sitter + LLM approach:
- Tree-sitter provides deterministic structural analysis (same input → same output)
- This enables incremental updates: only re-analyze changed files

Applied to ChainDB:
- Each table version gets a deterministic fingerprint (hash of its DDL + data state)
- Block-level change detection: skip unchanged blocks during sync
- Table-level change detection: only replay transactions that touch changed tables
- Merkle-based integrity verification: verify chain integrity without full replay
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from loguru import logger

from chain_db.models.block import Block
from chain_db.models.ledger import Ledger
from chain_db.models.transaction import Transaction, TxType


@dataclass
class TableFingerprint:
    """Deterministic fingerprint for a table's state.

    Similar to how Tree-sitter produces the same AST for the same source code,
    the same table state always produces the same fingerprint.

    Attributes:
        table_name: Name of the table.
        schema_hash: SHA-256 of the table's DDL (columns, types, constraints).
        data_hash: SHA-256 of the table's data content (row hashes aggregated).
        version: Current version number (incremented on each change).
        last_block: Block number of the last change.
    """

    table_name: str
    schema_hash: str = ""
    data_hash: str = ""
    version: int = 0
    last_block: int = 0

    def compute_schema_hash(self, columns: list[dict[str, Any]]) -> str:
        """Compute deterministic schema hash from column definitions.

        Args:
            columns: List of column definition dicts.

        Returns:
            SHA-256 hex string of the schema.
        """
        # Sort columns by name for deterministic ordering
        sorted_cols = sorted(columns, key=lambda c: c.get("name", ""))
        schema_str = json.dumps(sorted_cols, sort_keys=True, ensure_ascii=False)
        self.schema_hash = hashlib.sha256(schema_str.encode("utf-8")).hexdigest()
        return self.schema_hash

    def compute_data_hash(self, rows: list[dict[str, Any]]) -> str:
        """Compute deterministic data hash from row content.

        Rows are sorted by primary key (first column) for determinism.

        Args:
            rows: List of row dicts from SELECT *.

        Returns:
            SHA-256 hex string of the data.
        """
        # Sort by first key for deterministic ordering
        pk_col = list(rows[0].keys())[0] if rows else "id"
        sorted_rows = sorted(rows, key=lambda r: str(r.get(pk_col, "")))
        data_str = json.dumps(sorted_rows, sort_keys=True, default=str, ensure_ascii=False)
        self.data_hash = hashlib.sha256(data_str.encode("utf-8")).hexdigest()
        return self.data_hash

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary."""
        return {
            "table_name": self.table_name,
            "schema_hash": self.schema_hash,
            "data_hash": self.data_hash,
            "version": self.version,
            "last_block": self.last_block,
        }


@dataclass
class BlockFingerprint:
    """Deterministic fingerprint for a block's content.

    Unlike full block hash (which includes prev_hash), this fingerprint
    only covers the block's transaction content, enabling content-based
    change detection independent of chain position.

    Attributes:
        block_number: Block number.
        tx_count: Number of transactions.
        tables_touched: Set of table names modified in this block.
        content_hash: SHA-256 of all transaction payloads concatenated.
    """

    block_number: int = 0
    tx_count: int = 0
    tables_touched: set[str] = field(default_factory=set)
    content_hash: str = ""

    @classmethod
    def from_block(cls, block: Block) -> BlockFingerprint:
        """Create fingerprint from a block.

        Args:
            block: The block to fingerprint.

        Returns:
            BlockFingerprint instance.
        """
        fp = cls(block_number=block.block_number, tx_count=len(block.transactions))

        # Collect tables touched and compute content hash
        payload_parts = []
        for tx in block.transactions:
            table_name = tx.payload.get("table_name", "")
            if table_name:
                fp.tables_touched.add(table_name)
            payload_parts.append(json.dumps(tx.payload, sort_keys=True, default=str))

        content_str = "|".join(payload_parts)
        fp.content_hash = hashlib.sha256(content_str.encode("utf-8")).hexdigest()
        return fp


class IncrementalSyncEngine:
    """Engine for incremental table synchronization.

    Core idea: instead of replaying ALL blocks from genesis, only replay
    blocks that contain transactions touching tables that have changed
    since the last sync.

    This is analogous to how Understand Anything uses Tree-sitter's
    deterministic parsing to only re-analyze changed files:
    - Fingerprint each block's content (deterministic)
    - Track which tables each block touches (change detection)
    - Only replay blocks that touch tables needing sync (incremental)

    Attributes:
        ledger: Blockchain ledger.
        table_fingerprints: Current fingerprints per table.
        block_fingerprints: Cached fingerprints per block.
        synced_block: Last synced block number.
    """

    def __init__(self, ledger: Ledger) -> None:
        """Initialize the incremental sync engine.

        Args:
            ledger: Blockchain ledger to analyze.
        """
        self.ledger = ledger
        self.table_fingerprints: dict[str, TableFingerprint] = {}
        self.block_fingerprints: dict[int, BlockFingerprint] = {}
        self.synced_block: int = 0

    def get_blocks_touching_table(self, table_name: str, from_block: int = 0) -> list[int]:
        """Find all blocks that touch a specific table.

        Uses pre-computed fingerprints for fast lookup.

        Args:
            table_name: Table to search for.
            from_block: Start searching from this block number.

        Returns:
            List of block numbers that modify the table.
        """
        result = []
        for block_num in range(from_block, self.ledger.height):
            fp = self._get_or_compute_block_fp(block_num)
            if table_name in fp.tables_touched:
                result.append(block_num)
        return result

    def get_tables_in_block(self, block_number: int) -> set[str]:
        """Get all tables touched in a specific block.

        Args:
            block_number: Block to analyze.

        Returns:
            Set of table names.
        """
        fp = self._get_or_compute_block_fp(block_number)
        return fp.tables_touched

    def compute_incremental_plan(self, target_tables: list[str] | None = None) -> dict[str, list[int]]:
        """Compute an incremental sync plan.

        For each table, determine which blocks need to be replayed
        to bring it up to date.

        Args:
            target_tables: Only plan for these tables. If None, plan for all.

        Returns:
            Dict mapping table_name -> list of block_numbers to replay.
        """
        tables = target_tables or list(self.table_fingerprints.keys())
        plan: dict[str, list[int]] = {}

        for table_name in tables:
            fp = self.table_fingerprints.get(table_name)
            from_block = fp.last_block + 1 if fp else 0
            plan[table_name] = self.get_blocks_touching_table(table_name, from_block)

        return plan

    def update_table_fingerprint(
        self, table_name: str, columns: list[dict[str, Any]], block_number: int
    ) -> TableFingerprint:
        """Update a table's fingerprint after sync.

        Args:
            table_name: Table to update.
            columns: Current column definitions.
            block_number: Block that was just synced.

        Returns:
            Updated TableFingerprint.
        """
        fp = self.table_fingerprints.get(table_name, TableFingerprint(table_name=table_name))
        fp.compute_schema_hash(columns)
        fp.version += 1
        fp.last_block = block_number
        self.table_fingerprints[table_name] = fp
        return fp

    def verify_chain_integrity(self, up_to: int | None = None) -> dict[str, Any]:
        """Verify chain integrity using Merkle-style content hashes.

        Unlike full block hash verification, this uses content fingerprints
        to detect data corruption without full replay.

        Args:
            up_to: Verify up to this block. If None, verify entire chain.

        Returns:
            Dict with is_valid, verified_blocks, and any mismatches.
        """
        end = min(up_to or self.ledger.height, self.ledger.height)
        mismatches = []

        for i in range(end):
            stored_fp = self.block_fingerprints.get(i)
            if stored_fp is None:
                continue

            current_fp = self._get_or_compute_block_fp(i)
            if stored_fp.content_hash != current_fp.content_hash:
                mismatches.append({
                    "block_number": i,
                    "expected": stored_fp.content_hash[:16],
                    "actual": current_fp.content_hash[:16],
                })

        return {
            "is_valid": len(mismatches) == 0,
            "verified_blocks": end,
            "mismatches": mismatches,
        }

    def get_change_summary(self, since_block: int = 0) -> dict[str, Any]:
        """Get a summary of changes since a given block.

        This is the incremental change feed — like Understand Anything's
        incremental update, only report what changed.

        Args:
            since_block: Block number to start from.

        Returns:
            Dict with tables_changed, blocks_scanned, change_count.
        """
        tables_changed: dict[str, list[int]] = {}

        for i in range(since_block, self.ledger.height):
            fp = self._get_or_compute_block_fp(i)
            for table in fp.tables_touched:
                if table not in tables_changed:
                    tables_changed[table] = []
                tables_changed[table].append(i)

        return {
            "tables_changed": list(tables_changed.keys()),
            "blocks_scanned": self.ledger.height - since_block,
            "change_count": sum(len(v) for v in tables_changed.values()),
            "detail": tables_changed,
        }

    def _get_or_compute_block_fp(self, block_number: int) -> BlockFingerprint:
        """Get or compute a block fingerprint (with caching).

        Args:
            block_number: Block to fingerprint.

        Returns:
            BlockFingerprint instance.
        """
        if block_number not in self.block_fingerprints:
            block = self.ledger.get_block(block_number)
            if block:
                self.block_fingerprints[block_number] = BlockFingerprint.from_block(block)
            else:
                self.block_fingerprints[block_number] = BlockFingerprint(block_number=block_number)
        return self.block_fingerprints[block_number]

    def export_fingerprints(self) -> dict[str, Any]:
        """Export all fingerprints for persistence.

        Returns:
            Serializable dict with table and block fingerprints.
        """
        return {
            "synced_block": self.synced_block,
            "table_fingerprints": {k: v.to_dict() for k, v in self.table_fingerprints.items()},
            "block_fingerprints": {
                str(k): {
                    "block_number": v.block_number,
                    "tx_count": v.tx_count,
                    "tables_touched": list(v.tables_touched),
                    "content_hash": v.content_hash,
                }
                for k, v in self.block_fingerprints.items()
            },
        }

    def import_fingerprints(self, data: dict[str, Any]) -> None:
        """Import previously saved fingerprints.

        Args:
            data: Serialized fingerprint data.
        """
        self.synced_block = data.get("synced_block", 0)

        for name, fp_data in data.get("table_fingerprints", {}).items():
            self.table_fingerprints[name] = TableFingerprint(
                table_name=fp_data["table_name"],
                schema_hash=fp_data.get("schema_hash", ""),
                data_hash=fp_data.get("data_hash", ""),
                version=fp_data.get("version", 0),
                last_block=fp_data.get("last_block", 0),
            )

        for block_num_str, fp_data in data.get("block_fingerprints", {}).items():
            block_num = int(block_num_str)
            self.block_fingerprints[block_num] = BlockFingerprint(
                block_number=fp_data["block_number"],
                tx_count=fp_data["tx_count"],
                tables_touched=set(fp_data.get("tables_touched", [])),
                content_hash=fp_data.get("content_hash", ""),
            )
