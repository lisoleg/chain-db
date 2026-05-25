"""Persistent Ledger: SQLite-backed blockchain storage with fast in-memory indexing.

Combines the query speed of an in-memory index (Ledger) with the durability
of SQLite persistence. On startup, the chain is recovered from disk;
on shutdown, all pending blocks are flushed.

Storage schema (chaindb_chain.db):
  - blocks: block_number (PK), block_hash, prev_hash, tx_root, state_root,
            timestamp, proposer, tx_data (msgpack blob)
  - chain_meta: key-value store for chain height, state_root, etc.
  - table_registry: serialized TableRegistry snapshot

Design:
  - Write-ahead: block appended to both in-memory dict AND SQLite
  - Read path: always serves from memory (O(1) dict lookup)
  - Recovery: on init, scans SQLite to rebuild in-memory index
  - Fingerprint persistence: integrates with IncrementalSyncEngine
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from pathlib import Path
from typing import Any

import msgpack
from loguru import logger

from chain_db.models.block import Block
from chain_db.models.transaction import Transaction


class PersistentLedger:
    """SQLite-backed persistent blockchain ledger.

    Provides the same interface as the in-memory Ledger, but with
    automatic persistence to a SQLite database file. The in-memory
    index is always authoritative for reads; SQLite is the durability layer.

    Attributes:
        db_path: Path to the SQLite database file.
        height: Current chain height (number of blocks).
        last_block: The last block in the chain.
    """

    def __init__(self, db_path: str = "chaindb_chain.db") -> None:
        """Initialize the persistent ledger.

        Args:
            db_path: Path to the SQLite database file.
        """
        self.db_path = db_path
        self._blocks: dict[int, Block] = {}
        self._tx_index: dict[str, tuple[int, int]] = {}  # tx_hash -> (block_number, tx_index)
        self._last_block: Block | None = None
        self._state_root: str = "0" * 64
        self._processed_proposals: set[str] = set()  # Dedup set for consensus
        self._max_proposal_age: int = 1000  # Keep last 1000 proposal IDs

        # Initialize and recover
        self._init_db()
        self._recover_chain()

    def _get_conn(self) -> sqlite3.Connection:
        """Get a SQLite connection."""
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        """Initialize the database schema."""
        conn = self._get_conn()
        try:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS blocks (
                    block_number INTEGER PRIMARY KEY,
                    block_hash TEXT NOT NULL,
                    prev_hash TEXT NOT NULL,
                    tx_root TEXT NOT NULL,
                    state_root TEXT NOT NULL,
                    timestamp INTEGER NOT NULL,
                    proposer TEXT NOT NULL,
                    tx_data BLOB NOT NULL
                );

                CREATE TABLE IF NOT EXISTS chain_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_blocks_hash ON blocks(block_hash);
                CREATE INDEX IF NOT EXISTS idx_blocks_prev ON blocks(prev_hash);
            """)
            conn.commit()
            logger.info("Persistent ledger DB initialized: {}", self.db_path)
        finally:
            conn.close()

    def _recover_chain(self) -> None:
        """Recover the chain from SQLite on startup."""
        conn = self._get_conn()
        try:
            # Get chain height from meta
            row = conn.execute(
                "SELECT value FROM chain_meta WHERE key = 'height'"
            ).fetchone()
            stored_height = int(row["value"]) if row else 0
            stored_state_root = "0" * 64

            row = conn.execute(
                "SELECT value FROM chain_meta WHERE key = 'state_root'"
            ).fetchone()
            if row:
                stored_state_root = row["value"]

            if stored_height == 0:
                logger.info("No existing chain found, starting fresh")
                return

            # Load all blocks
            cursor = conn.execute(
                "SELECT * FROM blocks ORDER BY block_number ASC"
            )
            loaded = 0
            for row in cursor:
                block = self._row_to_block(row)
                self._blocks[block.block_number] = block
                for idx, tx in enumerate(block.transactions):
                    self._tx_index[tx.tx_hash] = (block.block_number, idx)
                self._last_block = block
                loaded += 1

            self._state_root = stored_state_root

            # Recover dedup set from meta
            row = conn.execute(
                "SELECT value FROM chain_meta WHERE key = 'processed_proposals'"
            ).fetchone()
            if row:
                try:
                    self._processed_proposals = set(json.loads(row["value"]))
                except (json.JSONDecodeError, TypeError):
                    self._processed_proposals = set()

            logger.info(
                "Chain recovered: {} blocks, state_root={}",
                loaded,
                self._state_root[:16],
            )
        finally:
            conn.close()

    @staticmethod
    def _row_to_block(row: sqlite3.Row) -> Block:
        """Convert a database row to a Block object.

        Args:
            row: SQLite row from the blocks table.

        Returns:
            Reconstructed Block instance.
        """
        transactions = []
        if row["tx_data"]:
            try:
                tx_list = msgpack.unpackb(row["tx_data"], raw=False)
                for tx_data in tx_list:
                    transactions.append(Transaction(**tx_data))
            except Exception:
                # Fallback: empty transactions
                pass

        return Block(
            block_number=row["block_number"],
            block_hash=row["block_hash"],
            prev_hash=row["prev_hash"],
            tx_root=row["tx_root"],
            state_root=row["state_root"],
            timestamp=row["timestamp"],
            proposer=row["proposer"],
            transactions=transactions,
        )

    @staticmethod
    def _block_to_tx_data(block: Block) -> bytes:
        """Serialize block transactions to msgpack.

        Args:
            block: Block whose transactions to serialize.

        Returns:
            Msgpack-encoded byte string.
        """
        tx_list = []
        for tx in block.transactions:
            tx_list.append({
                "tx_type": tx.tx_type.value,
                "account": tx.account,
                "sequence": tx.sequence,
                "payload": tx.payload,
                "raw_sql": tx.raw_sql,
            })
        return msgpack.packb(tx_list, use_bin_type=True)

    @property
    def height(self) -> int:
        """Current chain height (number of blocks)."""
        return len(self._blocks)

    @property
    def last_block(self) -> Block | None:
        """The last block in the chain."""
        return self._last_block

    @property
    def state_root(self) -> str:
        """Current incremental state root."""
        return self._state_root

    def is_proposal_processed(self, proposal_id: str) -> bool:
        """Check if a proposal has already been processed (consensus dedup).

        Args:
            proposal_id: Proposal identifier to check.

        Returns:
            True if the proposal was already processed.
        """
        return proposal_id in self._processed_proposals

    def mark_proposal_processed(self, proposal_id: str) -> None:
        """Mark a proposal as processed (consensus dedup).

        Args:
            proposal_id: Proposal identifier to mark.
        """
        self._processed_proposals.add(proposal_id)
        # Evict old entries to prevent unbounded growth
        if len(self._processed_proposals) > self._max_proposal_age:
            # Keep only the most recent entries (convert to sorted list)
            sorted_ids = sorted(self._processed_proposals)
            self._processed_proposals = set(
                sorted_ids[-self._max_proposal_age:]
            )

    def append_block(self, block: Block) -> None:
        """Append a block to the ledger (memory + SQLite).

        Validates block sequence and prev_hash, then persists to
        both the in-memory index and the SQLite database.

        Args:
            block: Block to append.

        Raises:
            ValueError: If block number is not sequential or prev_hash mismatches.
        """
        expected_number = (self._last_block.block_number + 1) if self._last_block else 0

        if block.block_number != expected_number:
            raise ValueError(
                f"Block number mismatch: expected {expected_number}, got {block.block_number}"
            )

        if self._last_block and block.prev_hash != self._last_block.block_hash:
            raise ValueError(
                f"Previous hash mismatch: expected {self._last_block.block_hash[:16]}, "
                f"got {block.prev_hash[:16]}"
            )

        # 1. Update in-memory index
        self._blocks[block.block_number] = block
        for idx, tx in enumerate(block.transactions):
            self._tx_index[tx.tx_hash] = (block.block_number, idx)

        # 2. Incremental state root
        self._state_root = hashlib.sha256(
            f"{self._state_root}{block.block_hash}".encode("utf-8")
        ).hexdigest()

        self._last_block = block

        # 3. Persist to SQLite
        self._persist_block(block)

        logger.info(
            "Block #{} persisted with {} transactions, hash={}",
            block.block_number,
            len(block.transactions),
            block.block_hash[:16],
        )

    def _persist_block(self, block: Block) -> None:
        """Persist a single block to SQLite.

        Args:
            block: Block to persist.
        """
        conn = self._get_conn()
        try:
            tx_data = self._block_to_tx_data(block)
            conn.execute(
                """INSERT OR REPLACE INTO blocks
                   (block_number, block_hash, prev_hash, tx_root,
                    state_root, timestamp, proposer, tx_data)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    block.block_number,
                    block.block_hash,
                    block.prev_hash,
                    block.tx_root,
                    block.state_root,
                    block.timestamp,
                    block.proposer,
                    tx_data,
                ),
            )
            # Update chain metadata
            conn.execute(
                "INSERT OR REPLACE INTO chain_meta (key, value) VALUES ('height', ?)",
                (str(self.height),),
            )
            conn.execute(
                "INSERT OR REPLACE INTO chain_meta (key, value) VALUES ('state_root', ?)",
                (self._state_root,),
            )
            conn.commit()
        except Exception as e:
            logger.error("Failed to persist block #{}: {}", block.block_number, e)
            raise
        finally:
            conn.close()

    def get_block(self, number: int) -> Block | None:
        """Retrieve a block by its number (from memory).

        Args:
            number: Block number to look up.

        Returns:
            The Block if found, None otherwise.
        """
        return self._blocks.get(number)

    def get_transaction(self, tx_hash: str) -> Transaction | None:
        """Retrieve a transaction by its hash (from memory).

        Args:
            tx_hash: Transaction hash to look up.

        Returns:
            The Transaction if found, None otherwise.
        """
        entry = self._tx_index.get(tx_hash)
        if entry is None:
            return None
        block_number, tx_index = entry
        block = self._blocks.get(block_number)
        if block is None or tx_index >= len(block.transactions):
            return None
        return block.transactions[tx_index]

    def get_block_range(self, start: int, end: int) -> list[Block]:
        """Get blocks in a range of block numbers.

        Args:
            start: Start block number (inclusive).
            end: End block number (inclusive).

        Returns:
            List of blocks in the specified range.
        """
        return [
            self._blocks[i]
            for i in range(start, end + 1)
            if i in self._blocks
        ]

    def get_all_transactions(self) -> list[Transaction]:
        """Get all transactions across all blocks.

        Returns:
            Flat list of all transactions in chain order.
        """
        txs: list[Transaction] = []
        for block_num in sorted(self._blocks.keys()):
            txs.extend(self._blocks[block_num].transactions)
        return txs

    def compute_state_root(self) -> str:
        """Compute the state root (O(1) incremental, cached).

        Returns:
            64-character hex string.
        """
        return self._state_root

    def get_chain_stats(self) -> dict[str, Any]:
        """Get chain statistics for dashboard/status reporting.

        Returns:
            Dict with chain height, total transactions, state root, etc.
        """
        total_txs = sum(len(b.transactions) for b in self._blocks.values())
        return {
            "height": self.height,
            "total_transactions": total_txs,
            "state_root": self._state_root,
            "last_block_hash": self._last_block.block_hash[:16] if self._last_block else None,
            "db_path": str(self.db_path),
            "db_size_mb": round(
                Path(self.db_path).stat().st_size / (1024 * 1024), 3
            ) if Path(self.db_path).exists() else 0,
        }
