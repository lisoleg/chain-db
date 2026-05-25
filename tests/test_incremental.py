"""Tests for the incremental sync engine.

Validates deterministic fingerprinting, change detection,
incremental sync planning, and chain integrity verification.
"""

from __future__ import annotations

import pytest

from chain_db.models.block import Block
from chain_db.models.ledger import Ledger
from chain_db.models.transaction import Mempool, Transaction, TxType
from chain_db.sync.incremental import (
    BlockFingerprint,
    IncrementalSyncEngine,
    TableFingerprint,
)


class TestTableFingerprint:
    """Tests for TableFingerprint deterministic hashing."""

    def test_schema_hash_deterministic(self) -> None:
        """Same columns → same schema hash (deterministic)."""
        fp1 = TableFingerprint(table_name="users")
        fp2 = TableFingerprint(table_name="users")
        cols = [{"name": "id", "data_type": "INTEGER"}, {"name": "name", "data_type": "TEXT"}]
        assert fp1.compute_schema_hash(cols) == fp2.compute_schema_hash(cols)

    def test_schema_hash_differs_for_different_schemas(self) -> None:
        """Different columns → different schema hash."""
        fp1 = TableFingerprint(table_name="users")
        fp2 = TableFingerprint(table_name="orders")
        cols1 = [{"name": "id", "data_type": "INTEGER"}]
        cols2 = [{"name": "id", "data_type": "TEXT"}]
        assert fp1.compute_schema_hash(cols1) != fp2.compute_schema_hash(cols2)

    def test_schema_hash_order_independent(self) -> None:
        """Column order doesn't affect hash (sorted internally)."""
        fp1 = TableFingerprint(table_name="t")
        fp2 = TableFingerprint(table_name="t")
        cols_a = [{"name": "b", "data_type": "TEXT"}, {"name": "a", "data_type": "INT"}]
        cols_b = [{"name": "a", "data_type": "INT"}, {"name": "b", "data_type": "TEXT"}]
        assert fp1.compute_schema_hash(cols_a) == fp2.compute_schema_hash(cols_b)

    def test_data_hash_deterministic(self) -> None:
        """Same rows → same data hash."""
        fp1 = TableFingerprint(table_name="users")
        fp2 = TableFingerprint(table_name="users")
        rows = [{"id": 1, "name": "alice"}, {"id": 2, "name": "bob"}]
        assert fp1.compute_data_hash(rows) == fp2.compute_data_hash(rows)

    def test_to_dict_roundtrip(self) -> None:
        """Serialization preserves key fields."""
        fp = TableFingerprint(table_name="products", schema_hash="abc", version=3, last_block=5)
        d = fp.to_dict()
        assert d["table_name"] == "products"
        assert d["version"] == 3
        assert d["last_block"] == 5


class TestBlockFingerprint:
    """Tests for BlockFingerprint content-based hashing."""

    def test_from_block_extracts_tables(self) -> None:
        """Fingerprint correctly identifies tables touched in a block."""
        ledger = Ledger()
        mempool = Mempool()

        tx1 = Transaction(
            tx_type=TxType.TABLE_LIST_SET,
            account="alice",
            sequence=1,
            payload={"table_name": "users", "sql_type": "CREATE_TABLE", "columns": []},
            raw_sql="CREATE TABLE users (id INT)",
        )
        tx2 = Transaction(
            tx_type=TxType.SQL_STATEMENT,
            account="bob",
            sequence=1,
            payload={"table_name": "orders", "sql_type": "INSERT"},
            raw_sql="INSERT INTO orders (id) VALUES (1)",
        )

        genesis = Block.create_genesis(proposer="test")
        ledger.append_block(genesis)

        block = Block(
            block_number=1,
            prev_hash=genesis.block_hash,
            transactions=[tx1, tx2],
            proposer="test",
        )
        ledger.append_block(block)

        fp = BlockFingerprint.from_block(block)
        assert "users" in fp.tables_touched
        assert "orders" in fp.tables_touched
        assert fp.tx_count == 2
        assert fp.content_hash  # Non-empty

    def test_same_block_same_fingerprint(self) -> None:
        """Deterministic: same block always produces same fingerprint."""
        ledger = Ledger()
        genesis = Block.create_genesis(proposer="test")
        ledger.append_block(genesis)

        tx = Transaction(
            tx_type=TxType.SQL_STATEMENT,
            account="alice",
            sequence=1,
            payload={"table_name": "t", "sql_type": "INSERT"},
            raw_sql="INSERT INTO t VALUES (1)",
        )
        block = Block(
            block_number=1,
            prev_hash=genesis.block_hash,
            transactions=[tx],
            proposer="test",
        )

        fp1 = BlockFingerprint.from_block(block)
        fp2 = BlockFingerprint.from_block(block)
        assert fp1.content_hash == fp2.content_hash


class TestIncrementalSyncEngine:
    """Tests for the incremental sync engine."""

    def _make_ledger_with_blocks(self, n: int) -> Ledger:
        """Create a ledger with n blocks, each touching specific tables."""
        ledger = Ledger()
        genesis = Block.create_genesis(proposer="test")
        ledger.append_block(genesis)

        for i in range(n):
            tx = Transaction(
                tx_type=TxType.SQL_STATEMENT,
                account="alice",
                sequence=i + 1,
                payload={"table_name": "users" if i % 2 == 0 else "orders", "sql_type": "INSERT"},
                raw_sql=f"INSERT INTO {'users' if i % 2 == 0 else 'orders'} VALUES ({i})",
            )
            prev = ledger.last_block
            block = Block(
                block_number=i + 1,
                prev_hash=prev.block_hash,
                transactions=[tx],
                proposer="test",
            )
            ledger.append_block(block)

        return ledger

    def test_get_blocks_touching_table(self) -> None:
        """Correctly identifies blocks that touch a specific table."""
        ledger = self._make_ledger_with_blocks(6)
        engine = IncrementalSyncEngine(ledger)

        user_blocks = engine.get_blocks_touching_table("users")
        order_blocks = engine.get_blocks_touching_table("orders")

        # Blocks 1,3,5 touch users; blocks 2,4,6 touch orders
        assert 1 in user_blocks
        assert 3 in user_blocks
        assert 5 in user_blocks
        assert 2 in order_blocks
        assert 4 in order_blocks
        assert 6 in order_blocks

    def test_get_tables_in_block(self) -> None:
        """Correctly identifies tables in a specific block."""
        ledger = self._make_ledger_with_blocks(3)
        engine = IncrementalSyncEngine(ledger)

        tables = engine.get_tables_in_block(1)
        assert "users" in tables

        tables = engine.get_tables_in_block(2)
        assert "orders" in tables

    def test_compute_incremental_plan(self) -> None:
        """Incremental plan only includes blocks after the last synced block."""
        ledger = self._make_ledger_with_blocks(4)
        engine = IncrementalSyncEngine(ledger)

        # Simulate users table already synced up to block 2
        fp = TableFingerprint(table_name="users", version=1, last_block=2)
        engine.table_fingerprints["users"] = fp

        plan = engine.compute_incremental_plan(["users"])
        # Should only include blocks > 2 that touch users (i.e., block 3)
        assert all(b > 2 for b in plan["users"])

    def test_change_summary(self) -> None:
        """Change summary correctly counts changes since a block."""
        ledger = self._make_ledger_with_blocks(4)
        engine = IncrementalSyncEngine(ledger)

        summary = engine.get_change_summary(since_block=2)
        assert summary["blocks_scanned"] == 3  # blocks 2,3,4 (genesis=0)
        assert summary["change_count"] > 0

    def test_verify_chain_integrity(self) -> None:
        """Chain integrity verification passes for unmodified chain."""
        ledger = self._make_ledger_with_blocks(3)
        engine = IncrementalSyncEngine(ledger)

        # Build fingerprints
        for i in range(ledger.height):
            engine._get_or_compute_block_fp(i)

        result = engine.verify_chain_integrity()
        assert result["is_valid"] is True

    def test_export_import_fingerprints(self) -> None:
        """Fingerprint export/import roundtrip preserves data."""
        ledger = self._make_ledger_with_blocks(2)
        engine = IncrementalSyncEngine(ledger)

        # Build some fingerprints
        engine._get_or_compute_block_fp(0)
        engine._get_or_compute_block_fp(1)

        exported = engine.export_fingerprints()

        # Import into a new engine
        engine2 = IncrementalSyncEngine(ledger)
        engine2.import_fingerprints(exported)

        assert engine2.synced_block == engine.synced_block
        assert len(engine2.block_fingerprints) == len(engine.block_fingerprints)
