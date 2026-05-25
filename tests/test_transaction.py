"""Tests for Transaction model: serialization, hashing, and roundtrip."""

import pytest

from chain_db.models.transaction import Mempool, Transaction, TxType


class TestTransaction:
    """Test suite for Transaction model."""

    def test_create_sql_statement_tx(self) -> None:
        """Test creating a SQL_STATEMENT transaction."""
        tx = Transaction(
            tx_type=TxType.SQL_STATEMENT,
            account="alice",
            sequence=1,
            payload={"sql_type": "INSERT", "table": "users"},
            raw_sql="INSERT INTO users (name) VALUES ('Bob')",
        )
        assert tx.tx_type == TxType.SQL_STATEMENT
        assert tx.account == "alice"
        assert tx.sequence == 1
        assert len(tx.tx_hash) == 64

    def test_create_table_list_set_tx(self) -> None:
        """Test creating a TABLE_LIST_SET transaction."""
        tx = Transaction(
            tx_type=TxType.TABLE_LIST_SET,
            account="admin",
            sequence=1,
            payload={"operation": "create", "table": "accounts"},
            raw_sql="",
        )
        assert tx.tx_type == TxType.TABLE_LIST_SET
        assert tx.raw_sql == ""

    def test_create_sql_transaction_tx(self) -> None:
        """Test creating a SQL_TRANSACTION (batch) transaction."""
        tx = Transaction(
            tx_type=TxType.SQL_TRANSACTION,
            account="bob",
            sequence=2,
            payload={
                "statements": [
                    "INSERT INTO users (name) VALUES ('Alice')",
                    "UPDATE users SET name='Bob' WHERE name='Alice'",
                ]
            },
            raw_sql="",
        )
        assert tx.tx_type == TxType.SQL_TRANSACTION
        assert len(tx.payload["statements"]) == 2

    def test_hash_is_deterministic(self) -> None:
        """Test that hash computation is deterministic."""
        tx1 = Transaction(
            tx_type=TxType.SQL_STATEMENT,
            account="alice",
            sequence=1,
            payload={"key": "value"},
            raw_sql="SELECT 1",
        )
        tx2 = Transaction(
            tx_type=TxType.SQL_STATEMENT,
            account="alice",
            sequence=1,
            payload={"key": "value"},
            raw_sql="SELECT 1",
        )
        assert tx1.tx_hash == tx2.tx_hash

    def test_hash_differs_for_different_content(self) -> None:
        """Test that different transactions have different hashes."""
        tx1 = Transaction(
            tx_type=TxType.SQL_STATEMENT,
            account="alice",
            sequence=1,
            payload={"key": "value1"},
            raw_sql="SELECT 1",
        )
        tx2 = Transaction(
            tx_type=TxType.SQL_STATEMENT,
            account="alice",
            sequence=2,
            payload={"key": "value2"},
            raw_sql="SELECT 2",
        )
        assert tx1.tx_hash != tx2.tx_hash

    def test_serialize_deserialize_roundtrip(self) -> None:
        """Test that serialize→deserialize produces identical transaction."""
        original = Transaction(
            tx_type=TxType.SQL_STATEMENT,
            account="charlie",
            sequence=5,
            payload={"sql_type": "UPDATE", "table": "orders"},
            raw_sql="UPDATE orders SET status='done' WHERE id=1",
        )
        data = original.serialize()
        restored = Transaction.deserialize(data)

        assert restored.tx_type == original.tx_type
        assert restored.account == original.account
        assert restored.sequence == original.sequence
        assert restored.payload == original.payload
        assert restored.raw_sql == original.raw_sql
        assert restored.tx_hash == original.tx_hash

    def test_serialize_deserialize_all_types(self) -> None:
        """Test roundtrip for all transaction types."""
        for tx_type in TxType:
            tx = Transaction(
                tx_type=tx_type,
                account="test",
                sequence=1,
                payload={"test": True},
                raw_sql="test" if tx_type == TxType.SQL_STATEMENT else "",
            )
            restored = Transaction.deserialize(tx.serialize())
            assert restored.tx_type == tx_type
            assert restored.tx_hash == tx.tx_hash

    def test_verify_hash(self) -> None:
        """Test that verify_hash returns True for valid transactions."""
        tx = Transaction(
            tx_type=TxType.SQL_STATEMENT,
            account="dave",
            sequence=3,
            payload={"key": "val"},
            raw_sql="DELETE FROM temp",
        )
        assert tx.verify_hash() is True


class TestMempool:
    """Test suite for Mempool."""

    def test_add_and_get_all(self) -> None:
        """Test adding transactions and retrieving them."""
        pool = Mempool()
        tx = Transaction(
            tx_type=TxType.SQL_STATEMENT,
            account="alice",
            sequence=1,
            payload={},
            raw_sql="SELECT 1",
        )
        pool.add(tx)
        assert pool.size == 1
        assert tx.tx_hash in [t.tx_hash for t in pool.get_all()]

    def test_remove(self) -> None:
        """Test removing a transaction from the mempool."""
        pool = Mempool()
        tx = Transaction(
            tx_type=TxType.SQL_STATEMENT,
            account="alice",
            sequence=1,
            payload={},
            raw_sql="SELECT 1",
        )
        pool.add(tx)
        pool.remove(tx.tx_hash)
        assert pool.size == 0

    def test_clear(self) -> None:
        """Test clearing the mempool."""
        pool = Mempool()
        for i in range(5):
            tx = Transaction(
                tx_type=TxType.SQL_STATEMENT,
                account="alice",
                sequence=i,
                payload={},
                raw_sql=f"SELECT {i}",
            )
            pool.add(tx)
        pool.clear()
        assert pool.size == 0

    def test_contains(self) -> None:
        """Test checking if a transaction is in the mempool."""
        pool = Mempool()
        tx = Transaction(
            tx_type=TxType.SQL_STATEMENT,
            account="alice",
            sequence=1,
            payload={},
            raw_sql="SELECT 1",
        )
        pool.add(tx)
        assert pool.contains(tx.tx_hash) is True
        assert pool.contains("nonexistent") is False
