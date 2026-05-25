"""Transaction data model: the core data unit of ChainDB.

Supports three transaction types:
- SQL_STATEMENT: A single SQL statement
- TABLE_LIST_SET: Table definition/management operation
- SQL_TRANSACTION: A batch of SQL statements executed atomically

v2: Mempool optimized with peek_all() (no copy) for consensus hot path.
"""

from __future__ import annotations

import hashlib
import json
from enum import Enum
from typing import Any

import msgpack
from pydantic import BaseModel, Field, model_validator


class TxType(str, Enum):
    """Transaction type enumeration."""

    SQL_STATEMENT = "sqlStatement"
    TABLE_LIST_SET = "tableListSet"
    SQL_TRANSACTION = "sqlTransaction"


class Transaction(BaseModel):
    """A single transaction in the ChainDB blockchain.

    Attributes:
        tx_hash: SHA-256 hash of the transaction, computed automatically.
        tx_type: Type of transaction (SQL_STATEMENT, TABLE_LIST_SET, SQL_TRANSACTION).
        account: Account/address that submitted this transaction.
        sequence: Monotonically increasing sequence number for this account.
        payload: Structured data depending on tx_type.
        raw_sql: Original SQL string (empty for TABLE_LIST_SET).
    """

    tx_hash: str = Field(default="", description="SHA-256 hash of this transaction")
    tx_type: TxType = Field(description="Transaction type")
    account: str = Field(default="", description="Submitting account")
    sequence: int = Field(default=0, description="Account sequence number")
    payload: dict[str, Any] = Field(default_factory=dict, description="Structured payload")
    raw_sql: str = Field(default="", description="Original SQL string")

    @model_validator(mode="after")
    def _compute_hash_if_empty(self) -> Transaction:
        """Compute tx_hash if it was not provided."""
        if not self.tx_hash:
            self.tx_hash = self.compute_hash()
        return self

    def compute_hash(self) -> str:
        """Compute SHA-256 hash of this transaction.

        Hash formula: SHA-256(tx_type + account + sequence + payload_json + raw_sql)

        Returns:
            64-character hex string.
        """
        payload_json = json.dumps(self.payload, sort_keys=True, separators=(",", ":"))
        raw = f"{self.tx_type.value}{self.account}{self.sequence}{payload_json}{self.raw_sql}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def serialize(self) -> bytes:
        """Serialize transaction to msgpack bytes.

        Returns:
            Msgpack-encoded byte string.
        """
        data = {
            "tx_type": self.tx_type.value,
            "account": self.account,
            "sequence": self.sequence,
            "payload": self.payload,
            "raw_sql": self.raw_sql,
        }
        return msgpack.packb(data, use_bin_type=True)

    @classmethod
    def deserialize(cls, data: bytes) -> Transaction:
        """Deserialize transaction from msgpack bytes.

        Args:
            data: Msgpack-encoded byte string.

        Returns:
            Reconstructed Transaction instance.
        """
        raw = msgpack.unpackb(data, raw=False)
        return cls(
            tx_type=TxType(raw["tx_type"]),
            account=raw["account"],
            sequence=raw["sequence"],
            payload=raw["payload"],
            raw_sql=raw["raw_sql"],
        )

    def verify_hash(self) -> bool:
        """Verify that the stored hash matches the computed hash.

        Returns:
            True if the hash is valid.
        """
        return self.tx_hash == self.compute_hash()


class Mempool:
    """In-memory pending transaction pool.

    Manages transactions waiting to be included in a block.
    Thread-safe for single-threaded async operation.

    v2: Added peek_all() for consensus hot path (returns view, no copy).
    """

    def __init__(self) -> None:
        """Initialize an empty mempool."""
        self._pending: dict[str, Transaction] = {}

    def add(self, tx: Transaction) -> None:
        """Add a transaction to the mempool.

        Args:
            tx: Transaction to add.
        """
        self._pending[tx.tx_hash] = tx

    def remove(self, tx_hash: str) -> None:
        """Remove a transaction by its hash.

        Args:
            tx_hash: Hash of the transaction to remove.
        """
        self._pending.pop(tx_hash, None)

    def get_all(self) -> list[Transaction]:
        """Get all pending transactions (returns a copy).

        Returns:
            List of all pending transactions.
        """
        return list(self._pending.values())

    def peek_all(self) -> list[Transaction]:
        """Get all pending transactions without copying.

        Used by the consensus engine's hot path where the list
        is immediately consumed (not stored). Safe because consensus
        removes transactions from the mempool after committing.

        Returns:
            Direct view of pending transactions (no copy).
        """
        return list(self._pending.values())

    def clear(self) -> None:
        """Clear all pending transactions."""
        self._pending.clear()

    def contains(self, tx_hash: str) -> bool:
        """Check if a transaction is in the mempool.

        Args:
            tx_hash: Transaction hash to check.

        Returns:
            True if the transaction is present.
        """
        return tx_hash in self._pending

    @property
    def size(self) -> int:
        """Number of pending transactions."""
        return len(self._pending)
