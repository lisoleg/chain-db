"""RPC Handler: implements the business logic for all JSON-RPC methods.

Each handler method receives a params dict and returns a result dict.
The standard response format is: {code: int, data: Any, message: str}
where code=0 indicates success.
"""

from __future__ import annotations

from typing import Any

from loguru import logger

from chain_db.models.block import Block
from chain_db.models.ledger import Ledger
from chain_db.models.transaction import Mempool, Transaction, TxType
from chain_db.sql.converter import SQLConverter
from chain_db.sql.validator import ValidationError
from chain_db.storage.query import QueryExecutor
from chain_db.storage.table_registry import TableRegistry
from chain_db.sync.incremental import IncrementalSyncEngine
from chain_db.sync.table_sync import TableSync


class RPCHandler:
    """Handles all JSON-RPC method implementations.

    Aggregates references to the core ChainDB modules and provides
    high-level methods for the RPC endpoint to call.

    Attributes:
        converter: SQLConverter for SQL -> Transaction conversion.
        mempool: Transaction mempool.
        query_executor: QueryExecutor for SELECT queries.
        ledger: Blockchain ledger.
        table_sync: TableSync for triggering sync operations.
    """

    def __init__(
        self,
        converter: SQLConverter,
        mempool: Mempool,
        query_executor: QueryExecutor,
        ledger: Ledger,
        registry: TableRegistry,
        table_sync: TableSync | None = None,
        incremental_engine: IncrementalSyncEngine | None = None,
    ) -> None:
        """Initialize the RPC handler.

        Args:
            converter: SQLConverter instance.
            mempool: Mempool instance.
            query_executor: QueryExecutor instance.
            ledger: Ledger instance.
            registry: TableRegistry instance.
            table_sync: Optional TableSync instance.
            incremental_engine: Optional IncrementalSyncEngine instance.
        """
        self.converter = converter
        self.mempool = mempool
        self.query_executor = query_executor
        self.ledger = ledger
        self.registry = registry
        self.table_sync = table_sync
        self.incremental_engine = incremental_engine

    def _success(self, data: Any = None, message: str = "success") -> dict[str, Any]:
        """Build a success response.

        Args:
            data: Response data.
            message: Success message.

        Returns:
            Standard response dict.
        """
        return {"code": 0, "data": data, "message": message}

    def _error(self, code: int, message: str) -> dict[str, Any]:
        """Build an error response.

        Args:
            code: Error code (non-zero).
            message: Error message.

        Returns:
            Standard response dict.
        """
        return {"code": code, "data": None, "message": message}

    async def sql_submit(self, params: dict[str, Any]) -> dict[str, Any]:
        """Submit a SQL statement to the blockchain.

        Args:
            params: Must contain 'sql' and optionally 'account'.

        Returns:
            Response with tx_hash and queued status.
        """
        sql = params.get("sql", "")
        account = params.get("account", "default")

        if not sql:
            return self._error(1, "Missing required parameter: sql")

        try:
            # Determine if we should skip validation (DDL statements)
            from chain_db.sql.parser import SQLParser
            parser = SQLParser()
            parsed = parser.parse(sql)
            skip_validation = parsed.sql_type.value in ("CREATE_TABLE", "ALTER_TABLE", "DROP_TABLE")

            tx = self.converter.to_transaction(sql, account, skip_validation=skip_validation)
            self.mempool.add(tx)

            logger.info("SQL submitted: type={}, tx_hash={}", tx.tx_type.value, tx.tx_hash[:16])
            return self._success({
                "tx_hash": tx.tx_hash,
                "tx_type": tx.tx_type.value,
                "queued": True,
            })
        except ValidationError as e:
            return self._error(3, f"Validation error: {e}")
        except ValueError as e:
            return self._error(2, f"Parse error: {e}")
        except Exception as e:
            return self._error(5, f"Internal error: {e}")

    async def sql_submit_batch(self, params: dict[str, Any]) -> dict[str, Any]:
        """Submit a batch of SQL statements as a transaction.

        Args:
            params: Must contain 'sqls' (list) and optionally 'account'.

        Returns:
            Response with tx_hash and queued status.
        """
        sqls = params.get("sqls", [])
        account = params.get("account", "default")

        if not sqls:
            return self._error(1, "Missing required parameter: sqls")

        try:
            tx = self.converter.to_transaction_batch(sqls, account)
            self.mempool.add(tx)

            logger.info("Batch SQL submitted: {} statements, tx_hash={}", len(sqls), tx.tx_hash[:16])
            return self._success({
                "tx_hash": tx.tx_hash,
                "tx_type": tx.tx_type.value,
                "statement_count": len(sqls),
                "queued": True,
            })
        except ValidationError as e:
            return self._error(3, f"Validation error: {e}")
        except ValueError as e:
            return self._error(2, f"Parse error: {e}")
        except Exception as e:
            return self._error(5, f"Internal error: {e}")

    async def create_table(self, params: dict[str, Any]) -> dict[str, Any]:
        """Submit a CREATE TABLE statement.

        Args:
            params: Must contain 'sql' (CREATE TABLE statement) and optionally 'account'.

        Returns:
            Response with tx_hash and queued status.
        """
        # create_table is essentially sql_submit with skip_validation
        sql = params.get("sql", "")
        account = params.get("account", "default")

        if not sql:
            return self._error(1, "Missing required parameter: sql")

        try:
            tx = self.converter.to_transaction(sql, account, skip_validation=True)
            self.mempool.add(tx)

            logger.info("CREATE TABLE submitted: tx_hash={}", tx.tx_hash[:16])
            return self._success({
                "tx_hash": tx.tx_hash,
                "tx_type": tx.tx_type.value,
                "queued": True,
            })
        except ValueError as e:
            return self._error(2, f"Parse error: {e}")
        except Exception as e:
            return self._error(5, f"Internal error: {e}")

    async def sql_query(self, params: dict[str, Any]) -> dict[str, Any]:
        """Execute a SELECT query on the local database.

        Args:
            params: Must contain 'sql' (SELECT statement).

        Returns:
            Response with query results (columns, rows, row_count).
        """
        sql = params.get("sql", "")

        if not sql:
            return self._error(1, "Missing required parameter: sql")

        try:
            result = await self.query_executor.execute_select(sql)
            return self._success(result.to_dict())
        except KeyError as e:
            return self._error(3, f"Table not found: {e}")
        except ValueError as e:
            return self._error(2, f"Invalid query: {e}")
        except Exception as e:
            return self._error(5, f"Internal error: {e}")

    async def block_query(self, params: dict[str, Any]) -> dict[str, Any]:
        """Query a block by block number.

        Args:
            params: Must contain 'block_number' (int).

        Returns:
            Response with block data.
        """
        block_number = params.get("block_number")
        if block_number is None:
            return self._error(1, "Missing required parameter: block_number")

        try:
            block_number = int(block_number)
        except (TypeError, ValueError):
            return self._error(1, "block_number must be an integer")

        block = self.ledger.get_block(block_number)
        if block is None:
            return self._error(3, f"Block not found: {block_number}")

        return self._success({
            "block_number": block.block_number,
            "block_hash": block.block_hash,
            "prev_hash": block.prev_hash,
            "tx_root": block.tx_root,
            "state_root": block.state_root,
            "timestamp": block.timestamp,
            "proposer": block.proposer,
            "transaction_count": len(block.transactions),
            "transactions": [
                {
                    "tx_hash": tx.tx_hash,
                    "tx_type": tx.tx_type.value,
                    "account": tx.account,
                    "raw_sql": tx.raw_sql,
                }
                for tx in block.transactions
            ],
        })

    async def tx_query(self, params: dict[str, Any]) -> dict[str, Any]:
        """Query a transaction by its hash.

        Args:
            params: Must contain 'tx_hash' (str).

        Returns:
            Response with transaction data.
        """
        tx_hash = params.get("tx_hash", "")
        if not tx_hash:
            return self._error(1, "Missing required parameter: tx_hash")

        # Search in mempool first
        for tx in self.mempool.get_all():
            if tx.tx_hash == tx_hash:
                return self._success({
                    "tx_hash": tx.tx_hash,
                    "tx_type": tx.tx_type.value,
                    "account": tx.account,
                    "sequence": tx.sequence,
                    "payload": tx.payload,
                    "raw_sql": tx.raw_sql,
                    "status": "pending",
                })

        # Search in ledger
        tx = self.ledger.get_transaction(tx_hash)
        if tx is None:
            return self._error(3, f"Transaction not found: {tx_hash}")

        return self._success({
            "tx_hash": tx.tx_hash,
            "tx_type": tx.tx_type.value,
            "account": tx.account,
            "sequence": tx.sequence,
            "payload": tx.payload,
            "raw_sql": tx.raw_sql,
            "status": "committed",
        })

    async def sync_table(self, params: dict[str, Any]) -> dict[str, Any]:
        """Trigger table sync from a specific block.

        Args:
            params: Optionally contains 'from_block' (int) to start sync from.

        Returns:
            Response with sync status.
        """
        if self.table_sync is None:
            return self._error(5, "Table sync not available")

        from_block = params.get("from_block", 0)
        try:
            await self.table_sync.replay_from(from_block)
            return self._success({
                "synced_height": self.table_sync.state_manager.synced_height,
                "status": "synced",
            })
        except Exception as e:
            return self._error(5, f"Sync error: {e}")

    async def audit_record(self, params: dict[str, Any]) -> dict[str, Any]:
        """Audit the change history of a specific record.

        Args:
            params: Must contain 'table' and optionally 'record_id'.

        Returns:
            Response with audit trail.
        """
        table_name = params.get("table", "")
        if not table_name:
            return self._error(1, "Missing required parameter: table")

        # Search all transactions that touch this table
        audit_trail: list[dict[str, Any]] = []
        for block_num in range(self.ledger.height):
            block = self.ledger.get_block(block_num)
            if block is None:
                continue
            for tx in block.transactions:
                payload = tx.payload
                if payload.get("table_name") == table_name:
                    audit_trail.append({
                        "block_number": block.block_number,
                        "tx_hash": tx.tx_hash,
                        "tx_type": tx.tx_type.value,
                        "sql_type": payload.get("sql_type", ""),
                        "account": tx.account,
                        "raw_sql": tx.raw_sql,
                        "timestamp": block.timestamp,
                    })

        return self._success({
            "table": table_name,
            "audit_trail": audit_trail,
            "total_changes": len(audit_trail),
        })

    async def get_status(self, params: dict[str, Any]) -> dict[str, Any]:
        """Get the current node status.

        Args:
            params: No parameters required.

        Returns:
            Response with node status information.
        """
        return self._success({
            "ledger_height": self.ledger.height,
            "mempool_size": self.mempool.size,
            "tables": self.registry.list_tables(),
            "last_block_hash": self.ledger.last_block.block_hash[:16] if self.ledger.last_block else None,
        })

    async def incremental_plan(self, params: dict[str, Any]) -> dict[str, Any]:
        """Compute incremental sync plan for specific tables.

        Inspired by Understand Anything's incremental update model:
        only sync tables that have changed since the last checkpoint.

        Args:
            params: Optionally contains 'tables' (list of table names).

        Returns:
            Response with sync plan per table.
        """
        if self.incremental_engine is None:
            return self._error(5, "Incremental sync engine not available")

        tables = params.get("tables")
        plan = self.incremental_engine.compute_incremental_plan(tables)
        summary = self.incremental_engine.get_change_summary()

        return self._success({
            "plan": {k: v for k, v in plan.items()},
            "summary": summary,
        })

    async def change_feed(self, params: dict[str, Any]) -> dict[str, Any]:
        """Get incremental change feed since a given block.

        Instead of scanning the entire chain, only report changes
        since the specified block number.

        Args:
            params: Optionally contains 'since_block' (int).

        Returns:
            Response with change summary.
        """
        if self.incremental_engine is None:
            return self._error(5, "Incremental sync engine not available")

        since_block = params.get("since_block", 0)
        summary = self.incremental_engine.get_change_summary(since_block)

        return self._success(summary)
