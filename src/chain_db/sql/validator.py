"""SQL Validator for ChainDB.

Validates parsed SQL statements against table registry metadata:
- Table existence checks for INSERT/UPDATE/DELETE
- Column matching for INSERT/UPDATE
- Duplicate table name checks for CREATE TABLE
- Drop table existence checks for DROP TABLE
"""

from __future__ import annotations

from loguru import logger

from chain_db.sql.parser import ParsedSQL, SQLType
from chain_db.storage.table_registry import TableRegistry


class ValidationError(Exception):
    """Raised when SQL validation fails."""

    pass


class SQLValidator:
    """Validates parsed SQL statements against the current table registry.

    Checks:
    - INSERT/UPDATE/DELETE: target table must exist
    - INSERT: columns must match the table schema
    - UPDATE: SET columns and WHERE columns must exist in the table
    - CREATE TABLE: table must not already exist
    - DROP TABLE: table must exist
    - ALTER TABLE: table must exist
    """

    def __init__(self, registry: TableRegistry) -> None:
        """Initialize validator with a table registry.

        Args:
            registry: The table registry to validate against.
        """
        self._registry = registry

    def validate(self, parsed: ParsedSQL) -> None:
        """Validate a parsed SQL statement.

        Args:
            parsed: ParsedSQL to validate.

        Raises:
            ValidationError: If the statement is invalid.
        """
        validators = {
            SQLType.INSERT: self.validate_insert,
            SQLType.UPDATE: self.validate_update,
            SQLType.DELETE: self.validate_delete,
            SQLType.CREATE_TABLE: self.validate_create,
            SQLType.DROP_TABLE: self.validate_drop,
            SQLType.ALTER_TABLE: self.validate_alter,
        }

        validator = validators.get(parsed.sql_type)
        if validator is None:
            raise ValidationError(f"Unsupported SQL type: {parsed.sql_type}")

        validator(parsed)

    def validate_insert(self, parsed: ParsedSQL) -> None:
        """Validate an INSERT statement.

        Checks:
        - Table exists
        - Inserted columns exist in the table

        Args:
            parsed: ParsedSQL for INSERT.

        Raises:
            ValidationError: If validation fails.
        """
        table_name = parsed.table_name
        if not self._registry.exists(table_name):
            raise ValidationError(f"Table does not exist: {table_name}")

        # Check column existence
        columns = parsed.data.get("columns", [])
        if columns:
            table_meta = self._registry.get(table_name)
            existing_cols = {col.name for col in table_meta.columns}
            for col in columns:
                if col not in existing_cols:
                    raise ValidationError(
                        f"Column '{col}' does not exist in table '{table_name}'"
                    )

    def validate_update(self, parsed: ParsedSQL) -> None:
        """Validate an UPDATE statement.

        Checks:
        - Table exists
        - SET columns exist in the table
        - WHERE columns exist in the table

        Args:
            parsed: ParsedSQL for UPDATE.

        Raises:
            ValidationError: If validation fails.
        """
        table_name = parsed.table_name
        if not self._registry.exists(table_name):
            raise ValidationError(f"Table does not exist: {table_name}")

        table_meta = self._registry.get(table_name)
        existing_cols = {col.name for col in table_meta.columns}

        # Check SET columns
        set_items = parsed.data.get("set", [])
        for item in set_items:
            if item["column"] not in existing_cols:
                raise ValidationError(
                    f"Column '{item['column']}' does not exist in table '{table_name}'"
                )

        # Check WHERE columns
        for cond in parsed.where_clause:
            if cond.column not in existing_cols:
                raise ValidationError(
                    f"WHERE column '{cond.column}' does not exist in table '{table_name}'"
                )

    def validate_delete(self, parsed: ParsedSQL) -> None:
        """Validate a DELETE statement.

        Checks:
        - Table exists
        - WHERE columns exist in the table

        Args:
            parsed: ParsedSQL for DELETE.

        Raises:
            ValidationError: If validation fails.
        """
        table_name = parsed.table_name
        if not self._registry.exists(table_name):
            raise ValidationError(f"Table does not exist: {table_name}")

        table_meta = self._registry.get(table_name)
        existing_cols = {col.name for col in table_meta.columns}

        for cond in parsed.where_clause:
            if cond.column not in existing_cols:
                raise ValidationError(
                    f"WHERE column '{cond.column}' does not exist in table '{table_name}'"
                )

    def validate_create(self, parsed: ParsedSQL) -> None:
        """Validate a CREATE TABLE statement.

        Checks:
        - Table does not already exist

        Args:
            parsed: ParsedSQL for CREATE TABLE.

        Raises:
            ValidationError: If validation fails.
        """
        table_name = parsed.table_name
        if self._registry.exists(table_name):
            raise ValidationError(f"Table already exists: {table_name}")

    def validate_drop(self, parsed: ParsedSQL) -> None:
        """Validate a DROP TABLE statement.

        Checks:
        - Table exists

        Args:
            parsed: ParsedSQL for DROP TABLE.

        Raises:
            ValidationError: If validation fails.
        """
        table_name = parsed.table_name
        if not self._registry.exists(table_name):
            raise ValidationError(f"Table does not exist: {table_name}")

    def validate_alter(self, parsed: ParsedSQL) -> None:
        """Validate an ALTER TABLE statement.

        Checks:
        - Table exists
        - For DROP/MODIFY: column exists in the table

        Args:
            parsed: ParsedSQL for ALTER TABLE.

        Raises:
            ValidationError: If validation fails.
        """
        table_name = parsed.table_name
        if not self._registry.exists(table_name):
            raise ValidationError(f"Table does not exist: {table_name}")

        action = parsed.data.get("action", "")
        table_meta = self._registry.get(table_name)
        existing_cols = {col.name for col in table_meta.columns}

        if action == "DROP":
            col_name = parsed.data.get("column_name", "")
            if col_name not in existing_cols:
                raise ValidationError(
                    f"Column '{col_name}' does not exist in table '{table_name}'"
                )
        elif action == "MODIFY":
            col_def = parsed.data.get("column", {})
            col_name = col_def.get("name", "") if isinstance(col_def, dict) else ""
            if col_name and col_name not in existing_cols:
                raise ValidationError(
                    f"Column '{col_name}' does not exist in table '{table_name}'"
                )
