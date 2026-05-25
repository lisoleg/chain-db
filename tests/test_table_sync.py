"""Tests for the TableSync module."""

from __future__ import annotations

import pytest

from chain_db.models.block import Block
from chain_db.models.ledger import Ledger
from chain_db.models.transaction import Transaction, TxType
from chain_db.storage.database import Database
from chain_db.storage.table_registry import ColumnDef, TableMeta, TableRegistry
from chain_db.sync.state_manager import StateManager
from chain_db.sync.table_sync import TableSync


@pytest.fixture
async def db(tmp_path):
    """Create a temporary database."""
    database = Database(db_path=str(tmp_path / "test.sqlite"))
    await database.initialize()
    yield database
    await database.close()


@pytest.fixture
def registry():
    """Create an empty table registry."""
    return TableRegistry()


@pytest.fixture
def ledger():
    """Create a ledger with genesis block."""
    ledger = Ledger()
    genesis = Block.create_genesis()
    ledger.append_block(genesis)
    return ledger


@pytest.fixture
def state_manager(tmp_path):
    """Create a state manager."""
    return StateManager(data_dir=str(tmp_path))


@pytest.fixture
async def table_sync(db, ledger, registry, state_manager):
    """Create a TableSync instance."""
    return TableSync(db, ledger, registry, state_manager)


@pytest.mark.asyncio
async def test_create_table_sync(table_sync, ledger, registry, db):
    """Test syncing a CREATE TABLE transaction."""
    tx = Transaction(
        tx_type=TxType.TABLE_LIST_SET,
        account="test_user",
        payload={
            "sql_type": "CREATE_TABLE",
            "table_name": "users",
            "columns": [
                {"name": "id", "data_type": "INTEGER", "nullable": False, "default_value": None},
                {"name": "name", "data_type": "TEXT", "nullable": True, "default_value": None},
            ],
        },
        raw_sql="CREATE TABLE users (id INTEGER NOT NULL, name TEXT)",
    )

    # Create a block with this transaction
    block = Block.create_from_transactions(
        block_number=1,
        prev_hash=ledger.last_block.block_hash,
        transactions=[tx],
        state_root="0" * 64,
        proposer="node_1",
    )
    ledger.append_block(block)

    # Sync the block
    await table_sync.sync_block(block)

    # Verify table was registered
    assert registry.exists("users")

    # Verify table was created in database
    rows = await db.execute_query("SELECT name FROM sqlite_master WHERE type='table' AND name='users'")
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_insert_sync(table_sync, ledger, registry, db):
    """Test syncing an INSERT transaction."""
    # First create the table
    create_tx = Transaction(
        tx_type=TxType.TABLE_LIST_SET,
        account="test_user",
        payload={
            "sql_type": "CREATE_TABLE",
            "table_name": "scores",
            "columns": [
                {"name": "id", "data_type": "INTEGER", "nullable": False, "default_value": None},
                {"name": "score", "data_type": "REAL", "nullable": True, "default_value": None},
            ],
        },
        raw_sql="CREATE TABLE scores (id INTEGER NOT NULL, score REAL)",
    )

    block1 = Block.create_from_transactions(
        block_number=1,
        prev_hash=ledger.last_block.block_hash,
        transactions=[create_tx],
        state_root="0" * 64,
        proposer="node_1",
    )
    ledger.append_block(block1)
    await table_sync.sync_block(block1)

    # Now insert
    insert_tx = Transaction(
        tx_type=TxType.SQL_STATEMENT,
        account="test_user",
        payload={"sql_type": "INSERT", "table_name": "scores"},
        raw_sql="INSERT INTO scores (id, score) VALUES (1, 95.5)",
    )

    block2 = Block.create_from_transactions(
        block_number=2,
        prev_hash=ledger.last_block.block_hash,
        transactions=[insert_tx],
        state_root="0" * 64,
        proposer="node_1",
    )
    ledger.append_block(block2)
    await table_sync.sync_block(block2)

    # Verify data
    rows = await db.execute_query("SELECT * FROM scores")
    assert len(rows) == 1
    assert rows[0]["id"] == 1
    assert rows[0]["score"] == 95.5


@pytest.mark.asyncio
async def test_batch_sync(table_sync, ledger, registry, db):
    """Test syncing a SQL_TRANSACTION (batch) transaction."""
    # Create table first
    create_tx = Transaction(
        tx_type=TxType.TABLE_LIST_SET,
        account="test_user",
        payload={
            "sql_type": "CREATE_TABLE",
            "table_name": "items",
            "columns": [
                {"name": "id", "data_type": "INTEGER", "nullable": False, "default_value": None},
                {"name": "value", "data_type": "TEXT", "nullable": True, "default_value": None},
            ],
        },
        raw_sql="CREATE TABLE items (id INTEGER NOT NULL, value TEXT)",
    )

    block1 = Block.create_from_transactions(
        block_number=1,
        prev_hash=ledger.last_block.block_hash,
        transactions=[create_tx],
        state_root="0" * 64,
        proposer="node_1",
    )
    ledger.append_block(block1)
    await table_sync.sync_block(block1)

    # Batch insert
    batch_tx = Transaction(
        tx_type=TxType.SQL_TRANSACTION,
        account="test_user",
        payload={
            "statements": [
                "INSERT INTO items (id, value) VALUES (1, 'alpha')",
                "INSERT INTO items (id, value) VALUES (2, 'beta')",
            ],
        },
        raw_sql="INSERT INTO items (id, value) VALUES (1, 'alpha'); INSERT INTO items (id, value) VALUES (2, 'beta')",
    )

    block2 = Block.create_from_transactions(
        block_number=2,
        prev_hash=ledger.last_block.block_hash,
        transactions=[batch_tx],
        state_root="0" * 64,
        proposer="node_1",
    )
    ledger.append_block(block2)
    await table_sync.sync_block(block2)

    # Verify both rows were inserted
    rows = await db.execute_query("SELECT * FROM items ORDER BY id")
    assert len(rows) == 2
    assert rows[0]["value"] == "alpha"
    assert rows[1]["value"] == "beta"


@pytest.mark.asyncio
async def test_state_manager_persistence(tmp_path):
    """Test that StateManager persists and reloads state."""
    sm1 = StateManager(data_dir=str(tmp_path))
    sm1.update(5)
    await sm1.save()

    sm2 = StateManager(data_dir=str(tmp_path))
    assert sm2.synced_height == 5
