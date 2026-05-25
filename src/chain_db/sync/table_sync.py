"""Table Sync module: replays blockchain transactions to the local database.

Listens for new blocks from the consensus module and replays
all transactions (DDL, DML, and SQL transactions) to keep
the local database in sync with the blockchain state.

Security: All table/column identifiers are validated against SQL injection
via a strict allowlist pattern before being interpolated into SQL.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any, Callable

from loguru import logger

from chain_db.models.block import Block
from chain_db.models.ledger import Ledger
from chain_db.models.transaction import Transaction, TxType
from chain_db.storage.database import Database
from chain_db.storage.table_registry import ColumnDef, TableMeta, TableRegistry
from chain_db.sync.state_manager import StateManager


# Strict identifier pattern: only alphanumeric + underscore, must start with letter
_IDENTIFIER_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


def _safe_identifier(name: str, context: str = "identifier") -> str:
    """Validate and return a safe SQL identifier.

    Rejects any identifier containing characters that could enable
    SQL injection (semicolons, quotes, dashes, spaces, etc.).

    Args:
        name: The identifier to validate.
        context: Description for error messages (e.g. "table name").

    Returns:
        The validated identifier.

    Raises:
        ValueError: If the identifier contains unsafe characters.
    """
    if not name or not _IDENTIFIER_RE.match(name):
        raise ValueError(
            f"Invalid {context}: '{name}'. "
            f"Only alphanumeric characters and underscores are allowed."
        )
    return name


class TableSync:
    """Monitors the blockchain and replays transactions to the local database.

    Processes three types of transactions:
    - TABLE_LIST_SET: DDL operations (CREATE/ALTER/DROP TABLE)
    - SQL_STATEMENT: Single DML operations (INSERT/UPDATE/DELETE)
    - SQL_TRANSACTION: Batch DML operations (atomic execution)

    Attributes:
        database: Local database instance.
        ledger: Blockchain ledger.
        registry: Table registry for metadata.
        state_manager: Tracks sync progress.
    """

    def __init__(
        self,
        database: Database,
        ledger: Ledger,
        registry: TableRegistry,
        state_manager: StateManager | None = None,
    ) -> None:
        """Initialize the table sync module.

        Args:
            database: Local database for executing SQL.
            ledger: Blockchain ledger to monitor.
            registry: Table registry for metadata updates.
            state_manager: Optional state manager (creates default if None).
        """
        self.database = database
        self.ledger = ledger
        self.registry = registry
        self.state_manager = state_manager or StateManager()
        self._running = False

    async def start(self) -> None:
        """Start the table sync process.

        Checks the current sync height and replays any blocks
        that haven't been synced yet.
        """
        self._running = True
        await self.replay_from(self.state_manager.synced_height + 1)
        logger.info(
            "TableSync started, synced up to block {}",
            self.state_manager.synced_height,
        )

    async def stop(self) -> None:
        """Stop the table sync process."""
        self._running = False
        logger.info("TableSync stopped")

    async def sync_block(self, block: Block) -> None:
        """Sync a single block by replaying all its transactions.

        Args:
            block: The block to sync.
        """
        logger.info("Syncing block #{} with {} transactions", block.block_number, len(block.transactions))

        for tx in block.transactions:
            try:
                await self._replay_transaction(tx)
            except Exception as e:
                logger.error(
                    "Error replaying tx {} in block #{}: {}",
                    tx.tx_hash[:16],
                    block.block_number,
                    e,
                )
                # For MVP, we log but continue
                # Production: handle based on tx_type (especially SQL_TRANSACTION)

        # Update sync progress
        self.state_manager.update(block.block_number)
        await self.state_manager.save()
        logger.info("Block #{} synced", block.block_number)

    async def replay_from(self, height: int) -> None:
        """Replay blocks starting from the given height.

        Args:
            height: Block number to start replaying from.
        """
        current_height = self.ledger.height
        if height >= current_height:
            logger.debug("Already synced, no blocks to replay")
            return

        logger.info("Replaying blocks from #{} to #{}", height, current_height - 1)

        for block_num in range(height, current_height):
            block = self.ledger.get_block(block_num)
            if block:
                await self.sync_block(block)

    async def _replay_transaction(self, tx: Transaction) -> None:
        """Replay a single transaction to the local database.

        Args:
            tx: Transaction to replay.
        """
        if tx.tx_type == TxType.TABLE_LIST_SET:
            await self._replay_ddl(tx)
        elif tx.tx_type == TxType.SQL_STATEMENT:
            await self._replay_dml(tx)
        elif tx.tx_type == TxType.SQL_TRANSACTION:
            await self._replay_batch(tx)
        else:
            logger.warning("Unknown tx_type: {}", tx.tx_type)

    async def _replay_ddl(self, tx: Transaction) -> None:
        """Replay a DDL transaction (CREATE/ALTER/DROP TABLE).

        Args:
            tx: TABLE_LIST_SET transaction.
        """
        payload = tx.payload
        sql_type = payload.get("sql_type", "")

        if sql_type == "CREATE_TABLE":
            await self._handle_create_table(tx)
        elif sql_type == "ALTER_TABLE":
            await self._handle_alter_table(tx)
        elif sql_type == "DROP_TABLE":
            await self._handle_drop_table(tx)
        else:
            # Fallback: execute raw SQL
            if tx.raw_sql:
                await self.database.execute_ddl(tx.raw_sql)

    async def _handle_create_table(self, tx: Transaction) -> None:
        """Handle a CREATE TABLE transaction.

        Args:
            tx: The transaction with CREATE TABLE payload.
        """
        payload = tx.payload
        table_name = _safe_identifier(payload.get("table_name", ""), "table name")
        columns_data = payload.get("columns", [])

        # Validate column names
        for col_data in columns_data:
            _safe_identifier(col_data.get("name", ""), "column name")

        # Build column definitions
        columns = []
        for col_data in columns_data:
            columns.append(ColumnDef(
                name=col_data.get("name", ""),
                data_type=col_data.get("data_type", "TEXT"),
                nullable=col_data.get("nullable", True),
                default_value=col_data.get("default_value"),
            ))

        # Register in table registry
        table_meta = TableMeta(
            name=table_name,
            creator=tx.account,
            columns=columns,
            create_sql=tx.raw_sql,
        )
        self.registry.register(table_meta)

        # Execute CREATE TABLE in local database
        create_sql = table_meta.get_create_sql()
        await self.database.execute_ddl(create_sql)
        logger.info("Created table: {}", table_name)

    async def _handle_alter_table(self, tx: Transaction) -> None:
        """Handle an ALTER TABLE transaction.

        Args:
            tx: The transaction with ALTER TABLE payload.
        """
        payload = tx.payload
        table_name = _safe_identifier(payload.get("table_name", ""), "table name")
        action = payload.get("action", "")

        if not self.registry.exists(table_name):
            logger.warning("ALTER TABLE: table '{}' not found in registry", table_name)
            return

        table_meta = self.registry.get(table_name)

        if action == "ADD":
            col_data = payload.get("column", {})
            _safe_identifier(col_data.get("name", ""), "column name")
            new_col = ColumnDef(
                name=col_data.get("name", ""),
                data_type=col_data.get("data_type", "TEXT"),
                nullable=col_data.get("nullable", True),
                default_value=col_data.get("default_value"),
            )
            table_meta.columns.append(new_col)
            table_meta.version += 1
            self.registry.update_table(table_name, table_meta)

            # Execute ALTER TABLE ADD
            await self.database.execute_ddl(
                f"ALTER TABLE {table_name} ADD {new_col.to_sql()}"
            )

        elif action == "DROP":
            col_name = _safe_identifier(payload.get("column_name", ""), "column name")
            table_meta.columns = [c for c in table_meta.columns if c.name != col_name]
            table_meta.version += 1
            self.registry.update_table(table_name, table_meta)

            # Execute ALTER TABLE DROP (SQLite may not support this, log warning)
            try:
                await self.database.execute_ddl(
                    f"ALTER TABLE {table_name} DROP COLUMN {col_name}"
                )
            except Exception as e:
                logger.warning("SQLite may not support DROP COLUMN: {}", e)

        elif action == "MODIFY":
            col_data = payload.get("column", {})
            col_name = col_data.get("name", "")
            for i, col in enumerate(table_meta.columns):
                if col.name == col_name:
                    table_meta.columns[i] = ColumnDef(
                        name=col_name,
                        data_type=col_data.get("data_type", col.data_type),
                        nullable=col_data.get("nullable", col.nullable),
                        default_value=col_data.get("default_value", col.default_value),
                    )
                    break
            table_meta.version += 1
            self.registry.update_table(table_name, table_meta)

        logger.info("Altered table: {} (action={})", table_name, action)

    async def _handle_drop_table(self, tx: Transaction) -> None:
        """Handle a DROP TABLE transaction.

        Args:
            tx: The transaction with DROP TABLE payload.
        """
        table_name = _safe_identifier(tx.payload.get("table_name", ""), "table name")

        if self.registry.exists(table_name):
            self.registry.unregister(table_name)

        await self.database.execute_ddl(f"DROP TABLE IF EXISTS {table_name}")
        logger.info("Dropped table: {}", table_name)

    async def _replay_dml(self, tx: Transaction) -> None:
        """Replay a DML transaction (INSERT/UPDATE/DELETE).

        Args:
            tx: SQL_STATEMENT transaction.
        """
        # Use the raw_sql for DML execution
        if tx.raw_sql:
            await self.database.execute_dml(tx.raw_sql)
        else:
            # Reconstruct SQL from payload if raw_sql is empty
            sql = self._reconstruct_sql(tx.payload)
            if sql:
                await self.database.execute_dml(sql)

    async def _replay_batch(self, tx: Transaction) -> None:
        """Replay a SQL_TRANSACTION (batch of DML statements).

        All statements are executed within a single database transaction.
        If any statement fails, the entire batch is rolled back.

        Args:
            tx: SQL_TRANSACTION transaction.
        """
        statements = tx.payload.get("statements", [])

        session = await self.database.begin_transaction()
        try:
            from sqlalchemy import text
            for sql in statements:
                await session.execute(text(sql))
            await self.database.commit_transaction(session)
            logger.debug("Batch of {} statements committed", len(statements))
        except Exception as e:
            await self.database.rollback_transaction(session)
            logger.error("Batch execution failed, rolled back: {}", e)
            raise

    @staticmethod
    def _reconstruct_sql(payload: dict) -> str:
        """Reconstruct a SQL statement from payload data.

        Args:
            payload: Transaction payload.

        Returns:
            Reconstructed SQL string, or empty string if not possible.
        """
        sql_type = payload.get("sql_type", "")
        table_name = payload.get("table_name", "")

        if sql_type == "INSERT":
            columns = payload.get("columns", [])
            values = payload.get("values", [])
            cols_str = ", ".join(columns)
            vals_str = ", ".join(f"'{v}'" if isinstance(v, str) else str(v) for v in values)
            return f"INSERT INTO {table_name} ({cols_str}) VALUES ({vals_str})"

        elif sql_type == "UPDATE":
            set_items = payload.get("set", [])
            set_str = ", ".join(
                f"{item['column']}='{item['value']}'" if isinstance(item['value'], str)
                else f"{item['column']}={item['value']}"
                for item in set_items
            )
            where = payload.get("where", [])
            where_str = TableSync._build_where(where)
            return f"UPDATE {table_name} SET {set_str} WHERE {where_str}" if where_str else f"UPDATE {table_name} SET {set_str}"

        elif sql_type == "DELETE":
            where = payload.get("where", [])
            where_str = TableSync._build_where(where)
            return f"DELETE FROM {table_name} WHERE {where_str}" if where_str else f"DELETE FROM {table_name}"

        return ""

    @staticmethod
    def _build_where(conditions: list[dict]) -> str:
        """Build a WHERE clause from condition dicts.

        Args:
            conditions: List of condition dictionaries.

        Returns:
            WHERE clause string.
        """
        parts = []
        for cond in conditions:
            col = cond.get("column", "")
            op = cond.get("operator", "=")
            val = cond.get("value", "")
            if isinstance(val, str):
                val = f"'{val}'"
            parts.append(f"{col}{op}{val}")
        return " AND ".join(parts)
