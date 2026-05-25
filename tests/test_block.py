"""Tests for Block model: construction, hashing, genesis block, and Merkle root."""

import pytest

from chain_db.models.block import Block
from chain_db.models.transaction import Transaction, TxType


class TestBlock:
    """Test suite for Block model."""

    def test_create_genesis_block(self) -> None:
        """Test creating a genesis block."""
        genesis = Block.create_genesis(proposer="node_1")
        assert genesis.block_number == 0
        assert genesis.prev_hash == "0" * 64
        assert len(genesis.transactions) == 0
        assert len(genesis.block_hash) == 64
        assert genesis.proposer == "node_1"
        assert genesis.timestamp > 0

    def test_genesis_block_hash_is_deterministic(self) -> None:
        """Test that genesis block hash is deterministic (same inputs → same hash)."""
        # Create two blocks with the same data
        b1 = Block(
            block_number=0,
            prev_hash="0" * 64,
            transactions=[],
            state_root="0" * 64,
            timestamp=1000,
            proposer="test",
        )
        b2 = Block(
            block_number=0,
            prev_hash="0" * 64,
            transactions=[],
            state_root="0" * 64,
            timestamp=1000,
            proposer="test",
        )
        assert b1.block_hash == b2.block_hash

    def test_create_block_with_transactions(self) -> None:
        """Test creating a block with transactions."""
        tx1 = Transaction(
            tx_type=TxType.SQL_STATEMENT,
            account="alice",
            sequence=1,
            payload={"sql_type": "INSERT"},
            raw_sql="INSERT INTO t1 VALUES (1)",
        )
        tx2 = Transaction(
            tx_type=TxType.SQL_STATEMENT,
            account="bob",
            sequence=1,
            payload={"sql_type": "UPDATE"},
            raw_sql="UPDATE t1 SET x=2 WHERE x=1",
        )
        block = Block.create_from_transactions(
            block_number=1,
            prev_hash="a" * 64,
            transactions=[tx1, tx2],
            state_root="b" * 64,
            proposer="node_1",
        )
        assert block.block_number == 1
        assert len(block.transactions) == 2
        assert block.prev_hash == "a" * 64

    def test_tx_root_computation(self) -> None:
        """Test that tx_root is computed correctly."""
        # Empty block should have zero hash
        genesis = Block.create_genesis()
        assert genesis.tx_root == "0" * 64

        # Block with transactions should have a non-zero tx_root
        tx = Transaction(
            tx_type=TxType.SQL_STATEMENT,
            account="alice",
            sequence=1,
            payload={},
            raw_sql="SELECT 1",
        )
        block = Block.create_from_transactions(
            block_number=1,
            prev_hash="0" * 64,
            transactions=[tx],
            state_root="0" * 64,
            proposer="node_1",
        )
        assert block.tx_root != "0" * 64
        assert len(block.tx_root) == 64

    def test_tx_root_is_deterministic(self) -> None:
        """Test that tx_root computation is deterministic."""
        tx = Transaction(
            tx_type=TxType.SQL_STATEMENT,
            account="alice",
            sequence=1,
            payload={},
            raw_sql="SELECT 1",
        )
        b1 = Block(
            block_number=1,
            prev_hash="0" * 64,
            transactions=[tx],
            state_root="0" * 64,
            timestamp=2000,
            proposer="test",
        )
        b2 = Block(
            block_number=1,
            prev_hash="0" * 64,
            transactions=[tx],
            state_root="0" * 64,
            timestamp=2000,
            proposer="test",
        )
        assert b1.tx_root == b2.tx_root

    def test_block_hash_changes_with_content(self) -> None:
        """Test that changing block content changes the hash."""
        b1 = Block(
            block_number=1,
            prev_hash="0" * 64,
            transactions=[],
            state_root="0" * 64,
            timestamp=1000,
            proposer="test",
        )
        b2 = Block(
            block_number=1,
            prev_hash="0" * 64,
            transactions=[],
            state_root="0" * 64,
            timestamp=2000,  # Different timestamp
            proposer="test",
        )
        assert b1.block_hash != b2.block_hash

    def test_block_with_multiple_transactions(self) -> None:
        """Test block with multiple transactions has correct tx_root."""
        txs = [
            Transaction(
                tx_type=TxType.SQL_STATEMENT,
                account=f"user_{i}",
                sequence=1,
                payload={},
                raw_sql=f"INSERT INTO t VALUES ({i})",
            )
            for i in range(4)
        ]
        block = Block.create_from_transactions(
            block_number=1,
            prev_hash="0" * 64,
            transactions=txs,
            state_root="0" * 64,
            proposer="node_1",
        )
        assert len(block.transactions) == 4
        assert block.tx_root != "0" * 64
