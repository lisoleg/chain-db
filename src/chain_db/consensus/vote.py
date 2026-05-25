"""Vote data model and collector for the POP consensus protocol.

A Vote represents a node's approval or rejection of a proposal.
The VoteCollector tracks votes and determines when consensus is reached.

v2 Optimizations:
- O(1) consensus check using approve/reject counters (no iteration)
- Delegates threshold computation to native bridge (Rust/Python)
- Thread-safe for single-threaded async operation
"""

from __future__ import annotations

from pydantic import BaseModel, Field
from loguru import logger

from chain_db.native import check_consensus as _native_check_consensus


class Vote(BaseModel):
    """A vote on a consensus proposal.

    Attributes:
        proposal_id: The proposal being voted on.
        voter: Node ID of the voter.
        approve: True to approve, False to reject.
        signature: Optional signature for authentication (MVP: empty).
    """

    proposal_id: str = Field(description="Proposal identifier")
    voter: str = Field(description="Voter node ID")
    approve: bool = Field(default=True, description="True=approve, False=reject")
    signature: str = Field(default="", description="Optional signature")


class VoteCollector:
    """Collects and tallies votes for a consensus round.

    v2: Uses O(1) counter-based consensus check instead of iterating
    over all votes each time. Delegates threshold computation to
    the native acceleration bridge (Rust C extension if available,
    pure Python fallback otherwise).

    Attributes:
        total_nodes: Total number of participating nodes.
        proposal_id: The proposal being voted on.
    """

    def __init__(self, total_nodes: int = 3) -> None:
        """Initialize the vote collector.

        Args:
            total_nodes: Total number of participating nodes.
        """
        self.total_nodes = total_nodes
        self.proposal_id: str = ""
        self._votes: dict[str, Vote] = {}  # voter -> Vote
        # v2: O(1) counters (replaces iteration in has_consensus)
        self._approve_count: int = 0
        self._reject_count: int = 0

    def reset(self, proposal_id: str) -> None:
        """Reset the collector for a new proposal.

        Args:
            proposal_id: The new proposal ID.
        """
        self.proposal_id = proposal_id
        self._votes.clear()
        self._approve_count = 0
        self._reject_count = 0

    def add_vote(self, vote: Vote) -> None:
        """Add a vote to the collector.

        Duplicate votes from the same voter are overwritten.
        Counters are updated incrementally (O(1)).

        Args:
            vote: The Vote to add.
        """
        # If this voter already voted, undo their previous vote
        existing = self._votes.get(vote.voter)
        if existing is not None:
            if existing.approve:
                self._approve_count -= 1
            else:
                self._reject_count -= 1

        # Record the new vote
        self._votes[vote.voter] = vote
        if vote.approve:
            self._approve_count += 1
        else:
            self._reject_count += 1

        logger.debug(
            "Vote received: voter={}, approve={}, total_votes={}, approve_count={}",
            vote.voter,
            vote.approve,
            len(self._votes),
            self._approve_count,
        )

    @property
    def approve_count(self) -> int:
        """Number of approve votes (O(1))."""
        return self._approve_count

    @property
    def reject_count(self) -> int:
        """Number of reject votes (O(1))."""
        return self._reject_count

    @property
    def total_votes(self) -> int:
        """Total number of votes received."""
        return len(self._votes)

    def has_consensus(self) -> bool:
        """Check if 2/3+1 of nodes have approved (O(1)).

        Uses native acceleration bridge (Rust or Python fallback)
        for threshold computation.

        Returns:
            True if consensus has been reached.
        """
        return _native_check_consensus(self._approve_count, self.total_nodes)

    def is_rejected(self) -> bool:
        """Check if the proposal is definitively rejected.

        A proposal is rejected if more than 1/3 of nodes have voted against.

        Returns:
            True if the proposal is rejected.
        """
        reject_threshold = self.total_nodes // 3 + 1
        return self._reject_count >= reject_threshold

    def get_votes(self) -> list[Vote]:
        """Get all collected votes.

        Returns:
            List of all votes.
        """
        return list(self._votes.values())
