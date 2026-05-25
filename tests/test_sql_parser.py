"""Tests for SQL parsing, validation, and conversion."""

import pytest

from chain_db.models.transaction import TxType
from chain_db.sql.converter import SQLConverter
from chain_db.sql.parser import ParsedSQL, SQLParser, SQLType, WhereCondition
from chain_db.sql.validator import SQLValidator, ValidationError
from chain_db.storage.table_registry import ColumnDef, TableMeta, TableRegistry


class TestSQLParser:
    """Test suite for SQLParser."""

    def setup_method(self) -> None:
        """Set up test fixtures."""
        self.parser = SQLParser()

    def test_parse_insert(self) -> None:
        """Test parsing an INSERT statement."""
        result = self.parser.parse("INSERT INTO users (name, age) VALUES ('Alice', 30)")
        assert result.sql_type == SQLType.INSERT
        assert result.table_name == "users"
        assert result.data["columns"] == ["name", "age"]
        assert result.data["values"] == ["Alice", 30]

    def test_parse_update(self) -> None:
        """Test parsing an UPDATE statement."""
        result = self.parser.parse("UPDATE users SET name='Bob' WHERE id=1")
        assert result.sql_type == SQLType.UPDATE
        assert result.table_name == "users"
        assert len(result.data["set"]) == 1
        assert result.data["set"][0]["column"] == "name"
        assert result.data["set"][0]["value"] == "Bob"
        assert len(result.where_clause) == 1
        assert result.where_clause[0].column == "id"
        assert result.where_clause[0].operator == "="
        assert result.where_clause[0].value == 1

    def test_parse_delete(self) -> None:
        """Test parsing a DELETE statement."""
        result = self.parser.parse("DELETE FROM users WHERE id=1")
        assert result.sql_type == SQLType.DELETE
        assert result.table_name == "users"
        assert len(result.where_clause) == 1
        assert result.where_clause[0].column == "id"

    def test_parse_create_table(self) -> None:
        """Test parsing a CREATE TABLE statement."""
        result = self.parser.parse(
            "CREATE TABLE users (id INTEGER NOT NULL, name TEXT, age INTEGER)"
        )
        assert result.sql_type == SQLType.CREATE_TABLE
        assert result.table_name == "users"
        assert len(result.data["columns"]) == 3
        assert result.data["columns"][0]["name"] == "id"
        assert result.data["columns"][0]["data_type"] == "INTEGER"
        assert result.data["columns"][0]["nullable"] is False

    def test_parse_drop_table(self) -> None:
        """Test parsing a DROP TABLE statement."""
        result = self.parser.parse("DROP TABLE users")
        assert result.sql_type == SQLType.DROP_TABLE
        assert result.table_name == "users"

    def test_parse_alter_table_add(self) -> None:
        """Test parsing ALTER TABLE ADD COLUMN."""
        result = self.parser.parse("ALTER TABLE users ADD email TEXT")
        assert result.sql_type == SQLType.ALTER_TABLE
        assert result.table_name == "users"
        assert result.data["action"] == "ADD"
        assert result.data["column"]["name"] == "email"

    def test_parse_alter_table_drop(self) -> None:
        """Test parsing ALTER TABLE DROP COLUMN."""
        result = self.parser.parse("ALTER TABLE users DROP email")
        assert result.sql_type == SQLType.ALTER_TABLE
        assert result.data["action"] == "DROP"
        assert result.data["column_name"] == "email"

    def test_parse_where_and_conditions(self) -> None:
        """Test parsing WHERE with AND conditions."""
        result = self.parser.parse("DELETE FROM users WHERE id=1 AND name='Alice'")
        assert len(result.where_clause) == 2
        assert result.where_clause[0].column == "id"
        assert result.where_clause[1].column == "name"

    def test_parse_where_comparison_operators(self) -> None:
        """Test parsing WHERE with various comparison operators."""
        result = self.parser.parse("DELETE FROM t WHERE age>18 AND score>=90 AND name!='test'")
        assert result.where_clause[0].operator == ">"
        assert result.where_clause[1].operator == ">="
        assert result.where_clause[2].operator == "!="

    def test_parse_unsupported_type(self) -> None:
        """Test that unsupported SQL types raise ValueError."""
        with pytest.raises(ValueError, match="Unsupported SQL type"):
            self.parser.parse("GRANT ALL ON users TO admin")

    def test_parse_invalid_insert(self) -> None:
        """Test that invalid INSERT format raises ValueError."""
        with pytest.raises(ValueError):
            self.parser.parse("INSERT INTO users VALUES (1)")

    def test_validate_returns_true_for_valid(self) -> None:
        """Test that validate returns True for valid SQL."""
        assert self.parser.validate("INSERT INTO t (a) VALUES (1)") is True

    def test_validate_returns_false_for_invalid(self) -> None:
        """Test that validate returns False for invalid SQL."""
        assert self.parser.validate("NOT A SQL") is False


class TestSQLValidator:
    """Test suite for SQLValidator."""

    def setup_method(self) -> None:
        """Set up test fixtures."""
        self.registry = TableRegistry()
        self.registry.register(TableMeta(
            name="users",
            creator="admin",
            columns=[
                ColumnDef(name="id", data_type="INTEGER", nullable=False),
                ColumnDef(name="name", data_type="TEXT"),
                ColumnDef(name="age", data_type="INTEGER"),
            ],
        ))
        self.validator = SQLValidator(self.registry)

    def test_validate_insert_existing_table(self) -> None:
        """Test INSERT validation with existing table."""
        parsed = SQLParser().parse("INSERT INTO users (name) VALUES ('Alice')")
        self.validator.validate_insert(parsed)  # Should not raise

    def test_validate_insert_nonexistent_table(self) -> None:
        """Test INSERT validation with non-existent table raises error."""
        parsed = SQLParser().parse("INSERT INTO orders (id) VALUES (1)")
        with pytest.raises(ValidationError, match="Table does not exist"):
            self.validator.validate_insert(parsed)

    def test_validate_insert_nonexistent_column(self) -> None:
        """Test INSERT validation with non-existent column raises error."""
        parsed = SQLParser().parse("INSERT INTO users (nonexistent) VALUES ('x')")
        with pytest.raises(ValidationError, match="Column.*does not exist"):
            self.validator.validate_insert(parsed)

    def test_validate_update_existing_table(self) -> None:
        """Test UPDATE validation with existing table."""
        parsed = SQLParser().parse("UPDATE users SET name='Bob' WHERE id=1")
        self.validator.validate_update(parsed)  # Should not raise

    def test_validate_update_nonexistent_table(self) -> None:
        """Test UPDATE validation with non-existent table raises error."""
        parsed = SQLParser().parse("UPDATE orders SET status='done' WHERE id=1")
        with pytest.raises(ValidationError, match="Table does not exist"):
            self.validator.validate_update(parsed)

    def test_validate_delete_existing_table(self) -> None:
        """Test DELETE validation with existing table."""
        parsed = SQLParser().parse("DELETE FROM users WHERE id=1")
        self.validator.validate_delete(parsed)  # Should not raise

    def test_validate_create_duplicate_table(self) -> None:
        """Test CREATE TABLE validation with duplicate name raises error."""
        parsed = SQLParser().parse("CREATE TABLE users (id INTEGER)")
        with pytest.raises(ValidationError, match="Table already exists"):
            self.validator.validate_create(parsed)

    def test_validate_create_new_table(self) -> None:
        """Test CREATE TABLE validation with new name passes."""
        parsed = SQLParser().parse("CREATE TABLE orders (id INTEGER)")
        self.validator.validate_create(parsed)  # Should not raise

    def test_validate_drop_existing_table(self) -> None:
        """Test DROP TABLE validation with existing table passes."""
        parsed = SQLParser().parse("DROP TABLE users")
        self.validator.validate_drop(parsed)  # Should not raise

    def test_validate_drop_nonexistent_table(self) -> None:
        """Test DROP TABLE validation with non-existent table raises error."""
        parsed = SQLParser().parse("DROP TABLE nonexistent")
        with pytest.raises(ValidationError, match="Table does not exist"):
            self.validator.validate_drop(parsed)


class TestSQLConverter:
    """Test suite for SQLConverter."""

    def setup_method(self) -> None:
        """Set up test fixtures."""
        self.registry = TableRegistry()
        self.registry.register(TableMeta(
            name="users",
            creator="admin",
            columns=[
                ColumnDef(name="id", data_type="INTEGER", nullable=False),
                ColumnDef(name="name", data_type="TEXT"),
            ],
        ))
        self.converter = SQLConverter(self.registry)

    def test_convert_insert_to_sql_statement(self) -> None:
        """Test converting INSERT to SQL_STATEMENT transaction."""
        tx = self.converter.to_transaction(
            "INSERT INTO users (name) VALUES ('Alice')", "alice"
        )
        assert tx.tx_type == TxType.SQL_STATEMENT
        assert tx.account == "alice"
        assert tx.payload["sql_type"] == "INSERT"

    def test_convert_create_table_to_table_list_set(self) -> None:
        """Test converting CREATE TABLE to TABLE_LIST_SET transaction."""
        tx = self.converter.to_transaction(
            "CREATE TABLE orders (id INTEGER NOT NULL, total REAL)",
            "admin",
            skip_validation=True,
        )
        assert tx.tx_type == TxType.TABLE_LIST_SET
        assert tx.payload["table_name"] == "orders"

    def test_convert_drop_table_to_table_list_set(self) -> None:
        """Test converting DROP TABLE to TABLE_LIST_SET transaction."""
        tx = self.converter.to_transaction(
            "DROP TABLE users", "admin"
        )
        assert tx.tx_type == TxType.TABLE_LIST_SET

    def test_convert_update_to_sql_statement(self) -> None:
        """Test converting UPDATE to SQL_STATEMENT transaction."""
        tx = self.converter.to_transaction(
            "UPDATE users SET name='Bob' WHERE id=1", "alice"
        )
        assert tx.tx_type == TxType.SQL_STATEMENT
        assert tx.payload["sql_type"] == "UPDATE"

    def test_convert_delete_to_sql_statement(self) -> None:
        """Test converting DELETE to SQL_STATEMENT transaction."""
        tx = self.converter.to_transaction(
            "DELETE FROM users WHERE id=1", "alice"
        )
        assert tx.tx_type == TxType.SQL_STATEMENT
        assert tx.payload["sql_type"] == "DELETE"

    def test_convert_batch_to_sql_transaction(self) -> None:
        """Test converting a batch of SQLs to SQL_TRANSACTION."""
        sqls = [
            "INSERT INTO users (name) VALUES ('Alice')",
            "UPDATE users SET name='Bob' WHERE name='Alice'",
        ]
        tx = self.converter.to_transaction_batch(sqls, "alice")
        assert tx.tx_type == TxType.SQL_TRANSACTION
        assert len(tx.payload["statements"]) == 2

    def test_sequence_increments(self) -> None:
        """Test that sequence numbers increment per account."""
        tx1 = self.converter.to_transaction(
            "INSERT INTO users (name) VALUES ('A')", "alice"
        )
        tx2 = self.converter.to_transaction(
            "INSERT INTO users (name) VALUES ('B')", "alice"
        )
        assert tx2.sequence == tx1.sequence + 1

    def test_convert_with_invalid_sql_raises(self) -> None:
        """Test that converting invalid SQL raises an error."""
        from chain_db.sql.validator import ValidationError
        with pytest.raises((ValueError, ValidationError)):
            self.converter.to_transaction(
                "INSERT INTO nonexistent (id) VALUES (1)", "alice"
            )
