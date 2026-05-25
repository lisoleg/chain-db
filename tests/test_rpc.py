"""Tests for the RPC API endpoints."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from chain_db.api.handlers import RPCHandler
from chain_db.api.rpc import create_rpc_router
from chain_db.config import NodeConfig
from chain_db.main import create_app
from chain_db.models.block import Block
from chain_db.models.ledger import Ledger
from chain_db.models.transaction import Mempool
from chain_db.sql.converter import SQLConverter
from chain_db.storage.database import Database
from chain_db.storage.query import QueryExecutor
from chain_db.storage.table_registry import TableRegistry


@pytest.fixture
async def db(tmp_path):
    """Create a temporary database."""
    database = Database(db_path=str(tmp_path / "test.sqlite"))
    await database.initialize()
    yield database
    await database.close()


@pytest.fixture
def handler(db):
    """Create an RPCHandler with test dependencies."""
    ledger = Ledger()
    mempool = Mempool()
    registry = TableRegistry()
    converter = SQLConverter(registry)
    query_executor = QueryExecutor(db, registry)

    # Add genesis block
    genesis = Block.create_genesis()
    ledger.append_block(genesis)

    return RPCHandler(
        converter=converter,
        mempool=mempool,
        query_executor=query_executor,
        ledger=ledger,
        registry=registry,
    )


@pytest.mark.asyncio
async def test_sql_submit(handler):
    """Test submitting a SQL statement via RPC."""
    # CREATE TABLE should succeed (skip_validation)
    result = await handler.create_table({
        "sql": "CREATE TABLE test_users (id INTEGER NOT NULL, name TEXT)",
        "account": "test_user",
    })
    assert result["code"] == 0
    assert "tx_hash" in result["data"]
    assert result["data"]["queued"] is True


@pytest.mark.asyncio
async def test_sql_query_error(handler):
    """Test querying a non-existent table."""
    result = await handler.sql_query({"sql": "SELECT * FROM nonexistent"})
    assert result["code"] != 0


@pytest.mark.asyncio
async def test_block_query(handler):
    """Test querying a block by number."""
    result = await handler.block_query({"block_number": 0})
    assert result["code"] == 0
    assert result["data"]["block_number"] == 0


@pytest.mark.asyncio
async def test_block_query_not_found(handler):
    """Test querying a non-existent block."""
    result = await handler.block_query({"block_number": 999})
    assert result["code"] != 0


@pytest.mark.asyncio
async def test_tx_query_not_found(handler):
    """Test querying a non-existent transaction."""
    result = await handler.tx_query({"tx_hash": "nonexistent"})
    assert result["code"] != 0


@pytest.mark.asyncio
async def test_get_status(handler):
    """Test getting node status."""
    result = await handler.get_status({})
    assert result["code"] == 0
    assert result["data"]["ledger_height"] == 1  # genesis block


@pytest.mark.asyncio
async def test_sql_submit_empty(handler):
    """Test submitting empty SQL."""
    result = await handler.sql_submit({"sql": ""})
    assert result["code"] != 0


@pytest.mark.asyncio
async def test_audit_record(handler):
    """Test auditing a table's change history."""
    result = await handler.audit_record({"table": "test_table"})
    assert result["code"] == 0
    assert result["data"]["total_changes"] == 0
