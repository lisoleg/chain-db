"""Table Registry: manages table metadata for ChainDB.

The TableRegistry tracks all tables created through the blockchain,
storing their schemas (column definitions), creators, and versions.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field
from loguru import logger


class ColumnDef(BaseModel):
    """Column definition for a table.

    Attributes:
        name: Column name.
        data_type: SQL data type (TEXT, INTEGER, REAL, etc.).
        nullable: Whether the column allows NULL values.
        default_value: Default value for the column.
    """

    name: str
    data_type: str = "TEXT"
    nullable: bool = True
    default_value: Any = None

    def to_sql(self) -> str:
        """Convert to SQL column definition string.

        Returns:
            SQL fragment like 'name TEXT NOT NULL DEFAULT value'.
        """
        parts = [f"{self.name} {self.data_type}"]
        if not self.nullable:
            parts.append("NOT NULL")
        if self.default_value is not None:
            if isinstance(self.default_value, str):
                parts.append(f"DEFAULT '{self.default_value}'")
            else:
                parts.append(f"DEFAULT {self.default_value}")
        return " ".join(parts)


class TableMeta(BaseModel):
    """Metadata for a registered table.

    Attributes:
        name: Table name.
        creator: Account that created the table.
        columns: List of column definitions.
        version: Schema version number.
        create_sql: Original CREATE TABLE SQL.
    """

    name: str
    creator: str = ""
    columns: list[ColumnDef] = Field(default_factory=list)
    version: int = 1
    create_sql: str = ""

    def get_create_sql(self) -> str:
        """Generate the CREATE TABLE SQL from column definitions.

        Returns:
            CREATE TABLE SQL string.
        """
        if self.create_sql:
            return self.create_sql

        col_defs = ", ".join(col.to_sql() for col in self.columns)
        return f"CREATE TABLE {self.name} ({col_defs})"


class TableRegistry:
    """Registry of table metadata.

    Manages table creation, deletion, and lookup.
    Tables are registered when CREATE TABLE transactions are processed.
    """

    def __init__(self) -> None:
        """Initialize an empty table registry."""
        self._tables: dict[str, TableMeta] = {}

    def register(self, table_meta: TableMeta) -> None:
        """Register a new table.

        Args:
            table_meta: Table metadata to register.

        Raises:
            ValueError: If a table with the same name already exists.
        """
        if table_meta.name in self._tables:
            raise ValueError(f"Table already registered: {table_meta.name}")
        self._tables[table_meta.name] = table_meta
        logger.info("Table registered: {} ({} columns)", table_meta.name, len(table_meta.columns))

    def unregister(self, table_name: str) -> None:
        """Unregister a table (DROP TABLE).

        Args:
            table_name: Name of the table to unregister.

        Raises:
            KeyError: If the table does not exist.
        """
        if table_name not in self._tables:
            raise KeyError(f"Table not found: {table_name}")
        del self._tables[table_name]
        logger.info("Table unregistered: {}", table_name)

    def get(self, table_name: str) -> TableMeta:
        """Get table metadata by name.

        Args:
            table_name: Table name to look up.

        Returns:
            TableMeta for the specified table.

        Raises:
            KeyError: If the table does not exist.
        """
        if table_name not in self._tables:
            raise KeyError(f"Table not found: {table_name}")
        return self._tables[table_name]

    def exists(self, table_name: str) -> bool:
        """Check if a table exists in the registry.

        Args:
            table_name: Table name to check.

        Returns:
            True if the table exists.
        """
        return table_name in self._tables

    def list_tables(self) -> list[str]:
        """List all registered table names.

        Returns:
            List of table names.
        """
        return list(self._tables.keys())

    def update_table(self, table_name: str, table_meta: TableMeta) -> None:
        """Update an existing table's metadata (e.g., after ALTER TABLE).

        Args:
            table_name: Table name to update.
            table_meta: New table metadata.

        Raises:
            KeyError: If the table does not exist.
        """
        if table_name not in self._tables:
            raise KeyError(f"Table not found: {table_name}")
        self._tables[table_name] = table_meta
        logger.info("Table updated: {} (v{})", table_name, table_meta.version)
