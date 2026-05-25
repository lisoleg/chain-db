"""Local database management using SQLAlchemy async engine.

Provides async access to SQLite via aiosqlite, with methods for
executing raw DDL/DML and managing transactions.
"""

from __future__ import annotations

from typing import Any

from loguru import logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine


class Database:
    """Async database manager for local SQLite storage.

    Provides methods for executing DDL/DML statements and
    managing database transactions.

    Attributes:
        engine: SQLAlchemy async engine instance.
    """

    def __init__(self, db_path: str = "chain_db.sqlite") -> None:
        """Initialize the database manager.

        Args:
            db_path: Path to the SQLite database file.
        """
        self._db_path = db_path
        self._engine: AsyncEngine | None = None
        self._session_factory: async_sessionmaker[AsyncSession] | None = None

    async def initialize(self) -> None:
        """Initialize the async engine and session factory.

        Must be called before any other database operations.
        """
        self._engine = create_async_engine(
            f"sqlite+aiosqlite:///{self._db_path}",
            echo=False,
            pool_pre_ping=True,
        )
        self._session_factory = async_sessionmaker(
            self._engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )
        logger.info("Database initialized: {}", self._db_path)

    async def close(self) -> None:
        """Close the database engine and release connections."""
        if self._engine:
            await self._engine.dispose()
            self._engine = None
            self._session_factory = None
            logger.info("Database closed")

    @property
    def engine(self) -> AsyncEngine:
        """Get the async engine instance.

        Returns:
            The AsyncEngine.

        Raises:
            RuntimeError: If the database has not been initialized.
        """
        if self._engine is None:
            raise RuntimeError("Database not initialized. Call initialize() first.")
        return self._engine

    def create_session(self) -> AsyncSession:
        """Create a new async session.

        Returns:
            A new AsyncSession instance.

        Raises:
            RuntimeError: If the database has not been initialized.
        """
        if self._session_factory is None:
            raise RuntimeError("Database not initialized. Call initialize() first.")
        return self._session_factory()

    async def execute_raw(self, sql: str, params: dict[str, Any] | None = None) -> Any:
        """Execute a raw SQL statement.

        Args:
            sql: SQL string to execute.
            params: Optional parameters for parameterized queries.

        Returns:
            The execution result.
        """
        async with self.create_session() as session:
            result = await session.execute(text(sql), params or {})
            await session.commit()
            return result

    async def execute_ddl(self, sql: str) -> None:
        """Execute a DDL statement (CREATE TABLE, ALTER TABLE, DROP TABLE).

        Args:
            sql: DDL SQL string.
        """
        async with self.create_session() as session:
            await session.execute(text(sql))
            await session.commit()
            logger.debug("DDL executed: {}", sql[:80])

    async def execute_dml(self, sql: str, params: dict[str, Any] | None = None) -> int:
        """Execute a DML statement (INSERT, UPDATE, DELETE).

        Args:
            sql: DML SQL string.
            params: Optional parameters.

        Returns:
            Number of affected rows.
        """
        async with self.create_session() as session:
            result = await session.execute(text(sql), params or {})
            await session.commit()
            rowcount = result.rowcount
            logger.debug("DML executed: {} rows affected", rowcount)
            return rowcount

    async def execute_query(self, sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Execute a SELECT query and return results as list of dicts.

        Args:
            sql: SELECT SQL string.
            params: Optional parameters.

        Returns:
            List of row dictionaries.
        """
        async with self.create_session() as session:
            result = await session.execute(text(sql), params or {})
            columns = list(result.keys())
            rows = [dict(zip(columns, row)) for row in result.fetchall()]
            return rows

    async def begin_transaction(self) -> AsyncSession:
        """Begin a new database transaction.

        Returns:
            An AsyncSession with an active transaction.
        """
        session = self.create_session()
        await session.begin()
        return session

    async def commit_transaction(self, session: AsyncSession) -> None:
        """Commit the current transaction.

        Args:
            session: The session with the active transaction.
        """
        await session.commit()
        await session.close()

    async def rollback_transaction(self, session: AsyncSession) -> None:
        """Rollback the current transaction.

        Args:
            session: The session with the active transaction.
        """
        await session.rollback()
        await session.close()
