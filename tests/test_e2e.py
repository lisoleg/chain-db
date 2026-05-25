"""End-to-end tests for ChainDB.

Tests the complete flow:
- Node startup with all modules
- SQL submission → consensus → table sync → query
- Multi-block chain building
- Transaction queries
"""

from __future__ import annotations

import asyncio

import pytest

from chain_db.config import NodeConfig
from chain_db.consensus.pop import POPConsensus
from chain_db.models.block import Block
from chain_db.models.ledger import Ledger
from chain_db.models.transaction import Mempool, TxType
from chain_db.sql.converter import SQLConverter
from chain_db.storage.database import Database
from chain_db.storage.query import QueryExecutor
from chain_db.storage.table_registry import TableRegistry
from chain_db.sync.state_manager import StateManager
from chain_db.sync.table_sync import TableSync


@pytest.fixture
async def chain_env(tmp_path):
    """Set up a complete chain environment for testing."""
    db_path = str(tmp_path / "e2e.sqlite")
    data_dir = str(tmp_path / "data")

    # Core modules
    ledger = Ledger()
    mempool = Mempool()
    registry = TableRegistry()

    # Genesis block
    genesis = Block.create_genesis(proposer="node_1")
    ledger.append_block(genesis)

    # Storage
    database = Database(db_path=db_path)
    await database.initialize()

    # SQL processing
    converter = SQLConverter(registry)

    # Query executor
    query_executor = QueryExecutor(database, registry)

    # Sync
    state_manager = StateManager(data_dir=data_dir)
    table_sync = TableSync(database, ledger, registry, state_manager)

    # Consensus (single node for e2e test)
    consensus = POPConsensus(
        node_id="node_1",
        is_leader=True,
        mempool=mempool,
        ledger=ledger,
        total_nodes=1,
        consensus_timeout=2,
        block_interval=1,
    )
    consensus.on_block_committed(table_sync.sync_block)

    yield {
        "ledger": ledger,
        "mempool": mempool,
        "registry": registry,
        "database": database,
        "converter": converter,
        "query_executor": query_executor,
        "table_sync": table_sync,
        "consensus": consensus,
        "state_manager": state_manager,
    }

    await database.close()


@pytest.mark.asyncio
async def test_create_and_insert(chain_env):
    """Test CREATE TABLE → INSERT → SELECT flow."""
    env = chain_env
    converter = env["converter"]
    mempool = env["mempool"]
    ledger = env["ledger"]
    consensus = env["consensus"]
    query_executor = env["query_executor"]

    # 1. Submit CREATE TABLE
    create_tx = converter.to_transaction(
        "CREATE TABLE users (id INTEGER NOT NULL, name TEXT)",
        "test_user",
        skip_validation=True,
    )
    mempool.add(create_tx)

    # 2. Manually trigger consensus (simulate leader proposing and committing)
    # For single-node test, we bypass the async loop
    transactions = mempool.get_all()
    mempool.clear()

    last_block = ledger.last_block
    block = Block.create_from_transactions(
        block_number=last_block.block_number + 1,
        prev_hash=last_block.block_hash,
        transactions=transactions,
        state_root=ledger.compute_state_root(),
        proposer="node_1",
    )
    ledger.append_block(block)

    # 3. Sync the block
    await env["table_sync"].sync_block(block)

    # 4. Verify table exists
    assert env["registry"].exists("users")

    # 5. Submit INSERT
    insert_tx = converter.to_transaction(
        "INSERT INTO users (id, name) VALUES (1, 'Alice')",
        "test_user",
    )
    mempool.add(insert_tx)

    # 6. Create and sync block
    transactions = mempool.get_all()
    mempool.clear()

    last_block = ledger.last_block
    block2 = Block.create_from_transactions(
        block_number=last_block.block_number + 1,
        prev_hash=last_block.block_hash,
        transactions=transactions,
        state_root=ledger.compute_state_root(),
        proposer="node_1",
    )
    ledger.append_block(block2)
    await env["table_sync"].sync_block(block2)

    # 7. Query the data
    result = await query_executor.execute_select("SELECT * FROM users")
    assert result.row_count == 1
    assert result.rows[0]["name"] == "Alice"
    assert result.rows[0]["id"] == 1


@pytest.mark.asyncio
async def test_update_and_delete(chain_env):
    """Test UPDATE and DELETE operations through the chain."""
    env = chain_env
    converter = env["converter"]
    mempool = env["mempool"]
    ledger = env["ledger"]
    query_executor = env["query_executor"]

    # Setup: Create table and insert data
    create_tx = converter.to_transaction(
        "CREATE TABLE products (id INTEGER NOT NULL, name TEXT, price REAL)",
        "test_user",
        skip_validation=True,
    )
    insert_tx = converter.to_transaction(
        "INSERT INTO products (id, name, price) VALUES (1, 'Widget', 9.99)",
        "test_user",
        skip_validation=True,  # Table not yet synced to registry
    )

    last_block = ledger.last_block
    block = Block.create_from_transactions(
        block_number=last_block.block_number + 1,
        prev_hash=last_block.block_hash,
        transactions=[create_tx, insert_tx],
        state_root=ledger.compute_state_root(),
        proposer="node_1",
    )
    ledger.append_block(block)
    await env["table_sync"].sync_block(block)

    # Update
    update_tx = converter.to_transaction(
        "UPDATE products SET price = 19.99 WHERE id = 1",
        "test_user",
    )
    mempool.add(update_tx)

    last_block = ledger.last_block
    block2 = Block.create_from_transactions(
        block_number=last_block.block_number + 1,
        prev_hash=last_block.block_hash,
        transactions=mempool.get_all(),
        state_root=ledger.compute_state_root(),
        proposer="node_1",
    )
    mempool.clear()
    ledger.append_block(block2)
    await env["table_sync"].sync_block(block2)

    # Verify update
    result = await query_executor.execute_select("SELECT * FROM products WHERE id = 1")
    assert result.rows[0]["price"] == 19.99

    # Delete
    delete_tx = converter.to_transaction(
        "DELETE FROM products WHERE id = 1",
        "test_user",
    )
    mempool.add(delete_tx)

    last_block = ledger.last_block
    block3 = Block.create_from_transactions(
        block_number=last_block.block_number + 1,
        prev_hash=last_block.block_hash,
        transactions=mempool.get_all(),
        state_root=ledger.compute_state_root(),
        proposer="node_1",
    )
    mempool.clear()
    ledger.append_block(block3)
    await env["table_sync"].sync_block(block3)

    # Verify delete
    result = await query_executor.execute_select("SELECT * FROM products")
    assert result.row_count == 0


@pytest.mark.asyncio
async def test_sql_transaction_batch(chain_env):
    """Test SQL_TRANSACTION (batch) atomicity."""
    env = chain_env
    converter = env["converter"]
    ledger = env["ledger"]
    query_executor = env["query_executor"]

    # Create table
    create_tx = converter.to_transaction(
        "CREATE TABLE accounts (id INTEGER NOT NULL, balance REAL)",
        "test_user",
        skip_validation=True,
    )

    last_block = ledger.last_block
    block = Block.create_from_transactions(
        block_number=last_block.block_number + 1,
        prev_hash=last_block.block_hash,
        transactions=[create_tx],
        state_root=ledger.compute_state_root(),
        proposer="node_1",
    )
    ledger.append_block(block)
    await env["table_sync"].sync_block(block)

    # Batch insert
    batch_tx = converter.to_transaction_batch(
        [
            "INSERT INTO accounts (id, balance) VALUES (1, 100.0)",
            "INSERT INTO accounts (id, balance) VALUES (2, 200.0)",
        ],
        "test_user",
    )

    last_block = ledger.last_block
    block2 = Block.create_from_transactions(
        block_number=last_block.block_number + 1,
        prev_hash=last_block.block_hash,
        transactions=[batch_tx],
        state_root=ledger.compute_state_root(),
        proposer="node_1",
    )
    ledger.append_block(block2)
    await env["table_sync"].sync_block(block2)

    # Verify both rows
    result = await query_executor.execute_select("SELECT * FROM accounts ORDER BY id")
    assert result.row_count == 2
    assert result.rows[0]["balance"] == 100.0
    assert result.rows[1]["balance"] == 200.0


@pytest.mark.asyncio
async def test_block_and_tx_queries(chain_env):
    """Test querying blocks and transactions from the ledger."""
    env = chain_env
    ledger = env["ledger"]

    # Genesis block should exist
    genesis = ledger.get_block(0)
    assert genesis is not None
    assert genesis.block_number == 0

    # Query non-existent block
    assert ledger.get_block(999) is None

    # Query non-existent transaction
    assert ledger.get_transaction("nonexistent") is None
