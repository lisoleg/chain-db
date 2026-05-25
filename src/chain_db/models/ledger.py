"""Ledger: in-memory blockchain index for efficient block and transaction lookups.

The Ledger maintains:
- An ordered list of blocks by block_number
- A transaction index by tx_hash for O(1) lookup
- The current chain height
- Incremental state root computation (v2)

v2 Optimizations:
- Incremental state root: O(1) per block instead of O(N) full recompute
- Uses native acceleration bridge for Merkle root when available
"""

from __future__ import annotations

import hashlib
from typing import Any

from loguru import logger

from chain_db.models.block import Block
from chain_db.models.transaction import Transaction


class Ledger:
    """In-memory blockchain ledger with block and transaction indexing.

    The Ledger provides:
    - append_block: Add a new block to the chain
    - get_block: Retrieve a block by number
    - get_transaction: Retrieve a transaction by hash
    - compute_state_root: O(1) incremental hash (v2)

    v2: State root is now computed incrementally:
        new_root = SHA-256(prev_root + block_hash)
    This avoids recomputing the full chain hash on every block.
    """

    def __init__(self) -> None:
        """Initialize an empty ledger."""
        self._blocks: dict[int, Block] = {}
        self._tx_index: dict[str, tuple[int, int]] = {}  # tx_hash -> (block_number, tx_index)
        self._last_block: Block | None = None
        # v2: Incremental state root cache
        self._state_root: str = "0" * 64

    @property
    def height(self) -> int:
        """Current chain height (number of blocks)."""
        return len(self._blocks)

    @property
    def last_block(self) -> Block | None:
        """The last block in the chain."""
        return self._last_block

    def append_block(self, block: Block) -> None:
        """Append a block to the ledger.

        Validates that the block number is sequential and the prev_hash
        matches the last block's hash. Updates incremental state root.

        Args:
            block: Block to append.

        Raises:
            ValueError: If block number is not sequential or prev_hash mismatches.
        """
        expected_number = (self._last_block.block_number + 1) if self._last_block else 0

        if block.block_number != expected_number:
            raise ValueError(
                f"Block number mismatch: expected {expected_number}, got {block.block_number}"
            )

        if self._last_block and block.prev_hash != self._last_block.block_hash:
            raise ValueError(
                f"Previous hash mismatch: expected {self._last_block.block_hash[:16]}, "
                f"got {block.prev_hash[:16]}"
            )

        # Index the block
        self._blocks[block.block_number] = block

        # Index all transactions in the block
        for idx, tx in enumerate(block.transactions):
            self._tx_index[tx.tx_hash] = (block.block_number, idx)

        # v2: Incremental state root update
        self._state_root = hashlib.sha256(
            f"{self._state_root}{block.block_hash}".encode("utf-8")
        ).hexdigest()

        self._last_block = block
        logger.info(
            "Block #{} appended with {} transactions, hash={}",
            block.block_number,
            len(block.transactions),
            block.block_hash[:16],
        )

    def get_block(self, number: int) -> Block | None:
        """Retrieve a block by its number.

        Args:
            number: Block number to look up.

        Returns:
            The Block if found, None otherwise.
        """
        return self._blocks.get(number)

    def get_transaction(self, tx_hash: str) -> Transaction | None:
        """Retrieve a transaction by its hash.

        Args:
            tx_hash: Transaction hash to look up.

        Returns:
            The Transaction if found, None otherwise.
        """
        entry = self._tx_index.get(tx_hash)
        if entry is None:
            return None
        block_number, tx_index = entry
        block = self._blocks.get(block_number)
        if block is None or tx_index >= len(block.transactions):
            return None
        return block.transactions[tx_index]

    def compute_state_root(self) -> str:
        """Compute the state root (O(1) incremental, v2).

        In v2, the state root is maintained incrementally on each
        append_block() call. This method returns the cached value.

        Returns:
            64-character hex string.
        """
        return self._state_root

    def get_block_range(self, start: int, end: int) -> list[Block]:
        """Get blocks in a range of block numbers.

        Args:
            start: Start block number (inclusive).
            end: End block number (inclusive).

        Returns:
            List of blocks in the specified range.
        """
        return [
            self._blocks[i]
            for i in range(start, end + 1)
            if i in self._blocks
        ]

    def get_all_transactions(self) -> list[Transaction]:
        """Get all transactions across all blocks.

        Returns:
            Flat list of all transactions in chain order.
        """
        txs: list[Transaction] = []
        for block_num in sorted(self._blocks.keys()):
            txs.extend(self._blocks[block_num].transactions)
        return txs
