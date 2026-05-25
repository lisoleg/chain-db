"""Query Executor: executes SELECT queries against the local database.

Provides a high-level interface for running SELECT queries
and returning structured QueryResult objects.
"""

from __future__ import annotations

from typing import Any

from loguru import logger

from chain_db.storage.database import Database
from chain_db.storage.table_registry import TableRegistry


class QueryResult:
    """Result of a SELECT query execution.

    Attributes:
        columns: List of column names in the result.
        rows: List of row dictionaries.
        row_count: Number of rows returned.
    """

    def __init__(self, columns: list[str], rows: list[dict[str, Any]]) -> None:
        """Initialize a query result.

        Args:
            columns: Column names.
            rows: Row data as list of dicts.
        """
        self.columns = columns
        self.rows = rows
        self.row_count = len(rows)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary for API response.

        Returns:
            Dictionary with columns, rows, and row_count.
        """
        return {
            "columns": self.columns,
            "rows": self.rows,
            "row_count": self.row_count,
        }

    def __repr__(self) -> str:
        """String representation."""
        return f"QueryResult(columns={self.columns}, rows={self.row_count})"


class QueryExecutor:
    """Executes SELECT queries against the local database.

    Validates that the target table exists before executing,
    then runs the query and returns structured results.

    Attributes:
        database: Database instance for executing queries.
        registry: TableRegistry for table existence checks.
    """

    def __init__(self, database: Database, registry: TableRegistry) -> None:
        """Initialize the query executor.

        Args:
            database: Database instance.
            registry: TableRegistry for validation.
        """
        self.database = database
        self.registry = registry

    async def execute_select(self, sql: str) -> QueryResult:
        """Execute a SELECT query and return structured results.

        Args:
            sql: SELECT SQL string.

        Returns:
            QueryResult with columns and rows.

        Raises:
            ValueError: If the SQL is not a SELECT statement.
            KeyError: If the target table does not exist.
        """
        # Basic validation: must be a SELECT
        stripped = sql.strip().upper()
        if not stripped.startswith("SELECT"):
            raise ValueError("Only SELECT queries are supported by execute_select")

        # Extract table name from simple SELECT (FROM table_name)
        table_name = self._extract_table_name(sql)
        if table_name and not self.registry.exists(table_name):
            raise KeyError(f"Table does not exist: {table_name}")

        # Execute the query
        rows = await self.database.execute_query(sql)

        # Determine column names from the first row or query
        columns: list[str] = list(rows[0].keys()) if rows else []

        result = QueryResult(columns=columns, rows=rows)
        logger.debug("SELECT executed: {} rows returned", result.row_count)
        return result

    @staticmethod
    def _extract_table_name(sql: str) -> str | None:
        """Extract table name from a simple SELECT statement.

        Supports:
        - SELECT ... FROM table_name ...
        - SELECT ... FROM table_name

        Args:
            sql: SELECT SQL string.

        Returns:
            Table name or None if not found.
        """
        import re

        # Match FROM table_name
        match = re.search(r"\bFROM\s+(\w+)", sql, re.IGNORECASE)
        if match:
            return match.group(1)
        return None
