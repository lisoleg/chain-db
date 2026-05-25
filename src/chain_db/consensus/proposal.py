"""Proposal data model for the POP consensus protocol.

A Proposal is created by the leader node and contains a set of
transactions to be included in the next block.
"""

from __future__ import annotations

import hashlib
import time
from typing import Any

from pydantic import BaseModel, Field

from chain_db.models.transaction import Transaction


class Proposal(BaseModel):
    """A consensus proposal containing a set of transactions.

    The leader creates proposals from the mempool and broadcasts
    them to peers for voting.

    Attributes:
        proposal_id: Unique identifier for this proposal.
        proposer: Node ID of the proposal creator.
        transactions: List of transactions to include.
        round: Consensus round number.
        timestamp: Unix timestamp when the proposal was created.
    """

    proposal_id: str = Field(description="Unique proposal identifier")
    proposer: str = Field(description="Leader node ID")
    transactions: list[Transaction] = Field(default_factory=list, description="Transactions in this proposal")
    round: int = Field(default=0, description="Consensus round number")
    timestamp: int = Field(default=0, description="Unix timestamp (seconds)")

    def compute_id(self) -> str:
        """Compute a deterministic proposal ID.

        Returns:
            64-character hex string.
        """
        tx_hashes = "".join(tx.tx_hash for tx in self.transactions)
        raw = f"{self.proposer}{self.round}{tx_hashes}{self.timestamp}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @classmethod
    def create_from_mempool(
        cls,
        proposer: str,
        transactions: list[Transaction],
        round_num: int = 0,
    ) -> Proposal:
        """Create a new proposal from mempool transactions.

        Args:
            proposer: Leader node ID.
            transactions: Transactions from the mempool.
            round_num: Consensus round number.

        Returns:
            A new Proposal instance.
        """
        timestamp = int(time.time())
        proposal_id = hashlib.sha256(
            f"{proposer}{round_num}{timestamp}".encode("utf-8")
        ).hexdigest()

        return cls(
            proposal_id=proposal_id,
            proposer=proposer,
            transactions=transactions,
            round=round_num,
            timestamp=timestamp,
        )
