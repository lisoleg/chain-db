"""SQL Converter: transforms parsed SQL statements into Transaction objects.

Supports:
- Single SQL → sqlStatement Transaction
- Batch SQL → sqlTransaction Transaction
- CREATE TABLE → tableListSet Transaction
"""

from __future__ import annotations

from typing import Any

from loguru import logger

from chain_db.models.transaction import Transaction, TxType
from chain_db.sql.parser import ParsedSQL, SQLParser, SQLType
from chain_db.sql.validator import SQLValidator, ValidationError
from chain_db.storage.table_registry import TableRegistry


class SQLConverter:
    """Converts SQL strings into Transaction objects.

    Pipeline: SQL → Parse → Validate → Transaction

    Attributes:
        parser: SQLParser instance for parsing SQL strings.
        validator: SQLValidator instance for validating parsed SQL.
    """

    def __init__(self, registry: TableRegistry) -> None:
        """Initialize the converter with a table registry.

        Args:
            registry: TableRegistry for validation lookups.
        """
        self.parser = SQLParser()
        self.validator = SQLValidator(registry)
        self._sequence_counters: dict[str, int] = {}

    def to_transaction(self, sql: str, account: str, skip_validation: bool = False) -> Transaction:
        """Convert a single SQL string into a Transaction.

        Args:
            sql: SQL string to convert.
            account: Account submitting the SQL.
            skip_validation: If True, skip SQL validation (e.g., for CREATE TABLE).

        Returns:
            A Transaction object.

        Raises:
            ValueError: If parsing or validation fails.
        """
        parsed = self.parser.parse(sql)

        if not skip_validation:
            self.validator.validate(parsed)

        sequence = self._next_sequence(account)

        # Determine tx_type based on SQL type
        if parsed.sql_type == SQLType.CREATE_TABLE:
            tx_type = TxType.TABLE_LIST_SET
        elif parsed.sql_type == SQLType.ALTER_TABLE:
            tx_type = TxType.TABLE_LIST_SET
        elif parsed.sql_type == SQLType.DROP_TABLE:
            tx_type = TxType.TABLE_LIST_SET
        else:
            tx_type = TxType.SQL_STATEMENT

        # Build payload
        payload = self._build_payload(parsed)

        tx = Transaction(
            tx_type=tx_type,
            account=account,
            sequence=sequence,
            payload=payload,
            raw_sql=sql,
        )

        logger.debug("Converted SQL to tx: type={}, hash={}", tx_type.value, tx.tx_hash[:16])
        return tx

    def to_transaction_batch(self, sqls: list[str], account: str) -> Transaction:
        """Convert a batch of SQL strings into a single sqlTransaction.

        All SQLs are parsed and validated individually, then combined
        into a single SQL_TRANSACTION transaction for atomic execution.

        Args:
            sqls: List of SQL strings.
            account: Account submitting the SQLs.

        Returns:
            A single SQL_TRANSACTION Transaction containing all SQLs.

        Raises:
            ValueError: If any SQL parsing or validation fails.
        """
        parsed_list: list[ParsedSQL] = []
        for sql in sqls:
            parsed = self.parser.parse(sql)
            self.validator.validate(parsed)
            parsed_list.append(parsed)

        sequence = self._next_sequence(account)

        # Build combined payload
        statements: list[str] = []
        payloads: list[dict[str, Any]] = []
        for parsed in parsed_list:
            statements.append(parsed.raw_sql)
            payloads.append(self._build_payload(parsed))

        tx = Transaction(
            tx_type=TxType.SQL_TRANSACTION,
            account=account,
            sequence=sequence,
            payload={
                "statements": statements,
                "payloads": payloads,
            },
            raw_sql="; ".join(sqls),
        )

        logger.debug("Converted batch of {} SQLs to tx: {}", len(sqls), tx.tx_hash[:16])
        return tx

    def _build_payload(self, parsed: ParsedSQL) -> dict[str, Any]:
        """Build a transaction payload from a parsed SQL.

        Args:
            parsed: ParsedSQL object.

        Returns:
            Payload dictionary.
        """
        payload: dict[str, Any] = {
            "sql_type": parsed.sql_type.value,
            "table_name": parsed.table_name,
        }

        if parsed.sql_type == SQLType.CREATE_TABLE:
            payload["columns"] = parsed.data.get("columns", [])
        elif parsed.sql_type == SQLType.ALTER_TABLE:
            payload["action"] = parsed.data.get("action", "")
            if "column" in parsed.data:
                payload["column"] = parsed.data["column"]
            if "column_name" in parsed.data:
                payload["column_name"] = parsed.data["column_name"]
        elif parsed.sql_type == SQLType.INSERT:
            payload["columns"] = parsed.data.get("columns", [])
            payload["values"] = parsed.data.get("values", [])
        elif parsed.sql_type == SQLType.UPDATE:
            payload["set"] = parsed.data.get("set", [])
            if parsed.where_clause:
                payload["where"] = [c.model_dump() for c in parsed.where_clause]
        elif parsed.sql_type == SQLType.DELETE:
            if parsed.where_clause:
                payload["where"] = [c.model_dump() for c in parsed.where_clause]

        return payload

    def _next_sequence(self, account: str) -> int:
        """Get the next sequence number for an account.

        Args:
            account: Account identifier.

        Returns:
            Next sequence number.
        """
        current = self._sequence_counters.get(account, 0)
        self._sequence_counters[account] = current + 1
        return current + 1
