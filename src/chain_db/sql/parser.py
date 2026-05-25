"""SQL Parser for ChainDB.

Parses SQL-92 core subset statements into structured ParsedSQL objects.
Supports: INSERT, UPDATE, DELETE, CREATE TABLE, ALTER TABLE, DROP TABLE.

WHERE clauses are parsed into simple condition lists (AND-connected, =/>/</>=/<=/!=/<> comparisons).
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Any

import sqlparse
from pydantic import BaseModel, Field
from loguru import logger


class SQLType(str, Enum):
    """Enumeration of supported SQL statement types."""

    INSERT = "INSERT"
    UPDATE = "UPDATE"
    DELETE = "DELETE"
    CREATE_TABLE = "CREATE_TABLE"
    ALTER_TABLE = "ALTER_TABLE"
    DROP_TABLE = "DROP_TABLE"


class WhereCondition(BaseModel):
    """A single WHERE condition.

    Attributes:
        column: Column name in the condition.
        operator: Comparison operator (=, >, <, >=, <=, !=, <>).
        value: Value to compare against.
    """

    column: str
    operator: str
    value: Any


class ParsedSQL(BaseModel):
    """Structured representation of a parsed SQL statement.

    Attributes:
        sql_type: Type of SQL statement.
        table_name: Name of the target table.
        data: Extracted data (columns/values for INSERT, SET clauses for UPDATE, etc.).
        where_clause: List of WHERE conditions (AND-connected).
        raw_sql: Original SQL string.
    """

    sql_type: SQLType
    table_name: str = ""
    data: dict[str, Any] = Field(default_factory=dict)
    where_clause: list[WhereCondition] = Field(default_factory=list)
    raw_sql: str = ""


class SQLParser:
    """Parser for SQL-92 core subset statements.

    Parses SQL strings into ParsedSQL objects with structured data.
    Supports: INSERT, UPDATE, DELETE, CREATE TABLE, ALTER TABLE, DROP TABLE.
    """

    def parse(self, sql: str) -> ParsedSQL:
        """Parse a SQL string into a structured ParsedSQL object.

        Args:
            sql: SQL string to parse.

        Returns:
            ParsedSQL with extracted structured data.

        Raises:
            ValueError: If the SQL type is unsupported or parsing fails.
        """
        normalized = sql.strip()
        if not normalized:
            raise ValueError("Empty SQL statement")

        # Determine SQL type from the first keyword
        first_word = normalized.split()[0].upper()

        parsers = {
            "INSERT": self._parse_insert,
            "UPDATE": self._parse_update,
            "DELETE": self._parse_delete,
            "CREATE": self._parse_create_table,
            "ALTER": self._parse_alter_table,
            "DROP": self._parse_drop_table,
        }

        parser_fn = parsers.get(first_word)
        if parser_fn is None:
            raise ValueError(f"Unsupported SQL type: {first_word}")

        return parser_fn(normalized)

    def validate(self, sql: str) -> bool:
        """Check if a SQL string is parseable.

        Args:
            sql: SQL string to validate.

        Returns:
            True if the SQL can be parsed without errors.
        """
        try:
            self.parse(sql)
            return True
        except (ValueError, IndexError):
            return False

    def _parse_insert(self, sql: str) -> ParsedSQL:
        """Parse an INSERT statement.

        Format: INSERT INTO table (col1, col2) VALUES (val1, val2)
        """
        # Match INSERT INTO table (cols) VALUES (vals)
        pattern = r"INSERT\s+INTO\s+(\w+)\s*\(([^)]+)\)\s*VALUES\s*\(([^)]+)\)"
        match = re.match(pattern, sql, re.IGNORECASE)
        if not match:
            raise ValueError(f"Invalid INSERT format: {sql}")

        table_name = match.group(1)
        columns = [c.strip().strip("'\"") for c in match.group(2).split(",")]
        values = [self._parse_value(v.strip()) for v in self._split_values(match.group(3))]

        if len(columns) != len(values):
            raise ValueError(f"Column count ({len(columns)}) != value count ({len(values)})")

        data = {"columns": columns, "values": values}

        return ParsedSQL(
            sql_type=SQLType.INSERT,
            table_name=table_name,
            data=data,
            raw_sql=sql,
        )

    def _parse_update(self, sql: str) -> ParsedSQL:
        """Parse an UPDATE statement.

        Format: UPDATE table SET col1=val1, col2=val2 WHERE condition
        """
        # Match UPDATE table SET col=val, ... [WHERE ...]
        pattern = r"UPDATE\s+(\w+)\s+SET\s+(.+?)(?:\s+WHERE\s+(.+))?$"
        match = re.match(pattern, sql, re.IGNORECASE)
        if not match:
            raise ValueError(f"Invalid UPDATE format: {sql}")

        table_name = match.group(1)
        set_clause = match.group(2)
        where_str = match.group(3)

        # Parse SET clause
        set_items = self._parse_set_clause(set_clause)
        data = {"set": set_items}

        # Parse WHERE clause
        where_clause = self._parse_where(where_str) if where_str else []

        return ParsedSQL(
            sql_type=SQLType.UPDATE,
            table_name=table_name,
            data=data,
            where_clause=where_clause,
            raw_sql=sql,
        )

    def _parse_delete(self, sql: str) -> ParsedSQL:
        """Parse a DELETE statement.

        Format: DELETE FROM table WHERE condition
        """
        pattern = r"DELETE\s+FROM\s+(\w+)(?:\s+WHERE\s+(.+))?$"
        match = re.match(pattern, sql, re.IGNORECASE)
        if not match:
            raise ValueError(f"Invalid DELETE format: {sql}")

        table_name = match.group(1)
        where_str = match.group(2)
        where_clause = self._parse_where(where_str) if where_str else []

        return ParsedSQL(
            sql_type=SQLType.DELETE,
            table_name=table_name,
            data={},
            where_clause=where_clause,
            raw_sql=sql,
        )

    def _parse_create_table(self, sql: str) -> ParsedSQL:
        """Parse a CREATE TABLE statement.

        Format: CREATE TABLE table (col1 type1, col2 type2, ...)
        """
        pattern = r"CREATE\s+TABLE\s+(\w+)\s*\((.+)\)"
        match = re.match(pattern, sql, re.IGNORECASE)
        if not match:
            raise ValueError(f"Invalid CREATE TABLE format: {sql}")

        table_name = match.group(1)
        columns_str = match.group(2)
        columns = self._parse_column_defs(columns_str)

        data = {"columns": columns}

        return ParsedSQL(
            sql_type=SQLType.CREATE_TABLE,
            table_name=table_name,
            data=data,
            raw_sql=sql,
        )

    def _parse_alter_table(self, sql: str) -> ParsedSQL:
        """Parse an ALTER TABLE statement.

        Format: ALTER TABLE table ADD/DROP/MODIFY column_def
        """
        pattern = r"ALTER\s+TABLE\s+(\w+)\s+(ADD|DROP|MODIFY)\s+(.+)"
        match = re.match(pattern, sql, re.IGNORECASE)
        if not match:
            raise ValueError(f"Invalid ALTER TABLE format: {sql}")

        table_name = match.group(1)
        action = match.group(2).upper()
        definition = match.group(3).strip()

        if action == "ADD":
            col_def = self._parse_single_column_def(definition)
            data = {"action": "ADD", "column": col_def}
        elif action == "DROP":
            col_name = definition.strip().strip("'\"")
            data = {"action": "DROP", "column_name": col_name}
        elif action == "MODIFY":
            col_def = self._parse_single_column_def(definition)
            data = {"action": "MODIFY", "column": col_def}
        else:
            raise ValueError(f"Unsupported ALTER action: {action}")

        return ParsedSQL(
            sql_type=SQLType.ALTER_TABLE,
            table_name=table_name,
            data=data,
            raw_sql=sql,
        )

    def _parse_drop_table(self, sql: str) -> ParsedSQL:
        """Parse a DROP TABLE statement.

        Format: DROP TABLE table
        """
        pattern = r"DROP\s+TABLE\s+(\w+)"
        match = re.match(pattern, sql, re.IGNORECASE)
        if not match:
            raise ValueError(f"Invalid DROP TABLE format: {sql}")

        table_name = match.group(1)

        return ParsedSQL(
            sql_type=SQLType.DROP_TABLE,
            table_name=table_name,
            data={},
            raw_sql=sql,
        )

    def _parse_where(self, where_str: str) -> list[WhereCondition]:
        """Parse a WHERE clause into a list of conditions.

        Supports AND-connected simple comparisons: col op value

        Args:
            where_str: WHERE clause string (without the WHERE keyword).

        Returns:
            List of WhereCondition objects.
        """
        conditions: list[WhereCondition] = []
        # Split by AND
        parts = re.split(r"\s+AND\s+", where_str, flags=re.IGNORECASE)

        for part in parts:
            part = part.strip()
            # Match: column operator value
            pattern = r"(\w+)\s*(!=|<>|>=|<=|>|<|=)\s*(.+)"
            match = re.match(pattern, part)
            if not match:
                raise ValueError(f"Cannot parse WHERE condition: {part}")

            column = match.group(1)
            operator = match.group(2)
            value = self._parse_value(match.group(3).strip())

            conditions.append(WhereCondition(column=column, operator=operator, value=value))

        return conditions

    def _parse_set_clause(self, set_str: str) -> list[dict[str, Any]]:
        """Parse a SET clause into a list of {column, value} dicts.

        Args:
            set_str: SET clause string (without the SET keyword).

        Returns:
            List of {"column": str, "value": Any} dicts.
        """
        items: list[dict[str, Any]] = []
        # Split by comma, but be careful about values containing commas in strings
        parts = self._split_set_clause(set_str)

        for part in parts:
            part = part.strip()
            match = re.match(r"(\w+)\s*=\s*(.+)", part)
            if not match:
                raise ValueError(f"Cannot parse SET clause item: {part}")
            column = match.group(1)
            value = self._parse_value(match.group(2).strip())
            items.append({"column": column, "value": value})

        return items

    @staticmethod
    def _parse_value(val_str: str) -> Any:
        """Parse a value string into a Python value.

        Handles: integers, floats, strings (quoted), NULL.

        Args:
            val_str: String representation of the value.

        Returns:
            Parsed Python value.
        """
        val_str = val_str.strip()
        if val_str.upper() == "NULL":
            return None
        if val_str.upper() == "TRUE":
            return True
        if val_str.upper() == "FALSE":
            return False
        # String values (quoted)
        if (val_str.startswith("'") and val_str.endswith("'")) or (
            val_str.startswith('"') and val_str.endswith('"')
        ):
            return val_str[1:-1]
        # Numeric values
        try:
            if "." in val_str:
                return float(val_str)
            return int(val_str)
        except ValueError:
            return val_str

    @staticmethod
    def _split_values(values_str: str) -> list[str]:
        """Split a VALUES clause by commas, respecting quoted strings.

        Args:
            values_str: Comma-separated values string.

        Returns:
            List of individual value strings.
        """
        result: list[str] = []
        current = ""
        in_quote = False
        quote_char = ""

        for char in values_str:
            if char in ("'", '"') and not in_quote:
                in_quote = True
                quote_char = char
                current += char
            elif char == quote_char and in_quote:
                in_quote = False
                quote_char = ""
                current += char
            elif char == "," and not in_quote:
                result.append(current)
                current = ""
            else:
                current += char

        if current.strip():
            result.append(current)

        return result

    @staticmethod
    def _split_set_clause(set_str: str) -> list[str]:
        """Split a SET clause by commas, respecting quoted strings.

        Args:
            set_str: Comma-separated SET items.

        Returns:
            List of individual SET item strings.
        """
        result: list[str] = []
        current = ""
        in_quote = False
        quote_char = ""

        for char in set_str:
            if char in ("'", '"') and not in_quote:
                in_quote = True
                quote_char = char
                current += char
            elif char == quote_char and in_quote:
                in_quote = False
                quote_char = ""
                current += char
            elif char == "," and not in_quote:
                result.append(current)
                current = ""
            else:
                current += char

        if current.strip():
            result.append(current)

        return result

    @staticmethod
    def _parse_column_defs(columns_str: str) -> list[dict[str, Any]]:
        """Parse column definitions from a CREATE TABLE statement.

        Args:
            columns_str: Column definitions string.

        Returns:
            List of column definition dicts.
        """
        columns: list[dict[str, Any]] = []
        # Simple split by comma (doesn't handle complex types with commas)
        parts = [p.strip() for p in columns_str.split(",")]

        for part in parts:
            col_def = SQLParser._parse_single_column_def(part)
            columns.append(col_def)

        return columns

    @staticmethod
    def _parse_single_column_def(col_str: str) -> dict[str, Any]:
        """Parse a single column definition.

        Format: column_name TYPE [NOT NULL] [DEFAULT value]

        Args:
            col_str: Column definition string.

        Returns:
            Dict with name, data_type, nullable, default_value.
        """
        tokens = col_str.strip().split()
        if not tokens:
            raise ValueError(f"Empty column definition: {col_str}")

        name = tokens[0].strip("'\"")
        data_type = tokens[1].upper() if len(tokens) > 1 else "TEXT"

        nullable = True
        default_value = None

        rest = " ".join(tokens[2:]).upper()
        if "NOT NULL" in rest:
            nullable = False

        # Check for DEFAULT
        default_match = re.search(r"DEFAULT\s+(\S+)", col_str, re.IGNORECASE)
        if default_match:
            default_value = SQLParser._parse_value(default_match.group(1))

        return {
            "name": name,
            "data_type": data_type,
            "nullable": nullable,
            "default_value": default_value,
        }
