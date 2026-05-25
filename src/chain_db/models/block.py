"""Block data model: a container of transactions in the ChainDB blockchain.

Each block includes:
- A sequential block number
- A hash of its contents
- A reference to the previous block (prev_hash)
- A Merkle root of all transactions (tx_root)
- A state root hash (state_root)
- A timestamp and proposer identifier
"""

from __future__ import annotations

import hashlib
import time
from typing import Any

from pydantic import BaseModel, Field, model_validator

from chain_db.models.transaction import Transaction


class Block(BaseModel):
    """A block in the ChainDB blockchain.

    Attributes:
        block_number: Sequential block number (0 = genesis).
        block_hash: SHA-256 hash of this block.
        prev_hash: Hash of the previous block.
        transactions: List of transactions in this block.
        tx_root: Merkle root hash of all transactions.
        state_root: Hash of the global state after this block.
        timestamp: Unix timestamp (seconds) when the block was created.
        proposer: Node ID of the block proposer.
    """

    block_number: int = Field(ge=0, description="Sequential block number")
    block_hash: str = Field(default="", description="SHA-256 hash of this block")
    prev_hash: str = Field(default="0" * 64, description="Hash of the previous block")
    transactions: list[Transaction] = Field(default_factory=list, description="Transactions in this block")
    tx_root: str = Field(default="", description="Merkle root of transactions")
    state_root: str = Field(default="0" * 64, description="State root after this block")
    timestamp: int = Field(default=0, description="Unix timestamp (seconds)")
    proposer: str = Field(default="", description="Block proposer node ID")

    @model_validator(mode="after")
    def _compute_hashes_if_empty(self) -> Block:
        """Compute block_hash and tx_root if they were not provided."""
        if not self.tx_root:
            self.tx_root = self.compute_tx_root()
        if not self.block_hash:
            self.block_hash = self.compute_hash()
        return self

    def compute_tx_root(self) -> str:
        """Compute the Merkle root hash of all transactions in this block.

        For simplicity, uses a binary Merkle tree. If no transactions,
        returns a zero hash.

        Returns:
            64-character hex string.
        """
        if not self.transactions:
            return "0" * 64

        hashes = [tx.tx_hash for tx in self.transactions]

        # Simple Merkle tree: repeatedly hash pairs until one root remains
        while len(hashes) > 1:
            next_level: list[str] = []
            for i in range(0, len(hashes), 2):
                left = hashes[i]
                right = hashes[i + 1] if i + 1 < len(hashes) else left
                combined = hashlib.sha256(f"{left}{right}".encode("utf-8")).hexdigest()
                next_level.append(combined)
            hashes = next_level

        return hashes[0]

    def compute_hash(self) -> str:
        """Compute the SHA-256 hash of this block.

        Hash formula: SHA-256(block_number + prev_hash + tx_root + state_root + timestamp)

        Returns:
            64-character hex string.
        """
        raw = f"{self.block_number}{self.prev_hash}{self.tx_root}{self.state_root}{self.timestamp}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @classmethod
    def create_genesis(cls, proposer: str = "genesis") -> Block:
        """Create the genesis block (block number 0).

        The genesis block has no transactions, a zero prev_hash,
        and the current timestamp.

        Args:
            proposer: Node ID that creates the genesis block.

        Returns:
            The genesis Block.
        """
        return cls(
            block_number=0,
            prev_hash="0" * 64,
            transactions=[],
            state_root="0" * 64,
            timestamp=int(time.time()),
            proposer=proposer,
        )

    @classmethod
    def create_from_transactions(
        cls,
        block_number: int,
        prev_hash: str,
        transactions: list[Transaction],
        state_root: str,
        proposer: str,
    ) -> Block:
        """Create a new block from a list of transactions.

        Args:
            block_number: Sequential block number.
            prev_hash: Hash of the previous block.
            transactions: List of transactions to include.
            state_root: Computed state root after applying transactions.
            proposer: Node ID of the block proposer.

        Returns:
            A new Block instance.
        """
        return cls(
            block_number=block_number,
            prev_hash=prev_hash,
            transactions=transactions,
            state_root=state_root,
            timestamp=int(time.time()),
            proposer=proposer,
        )
