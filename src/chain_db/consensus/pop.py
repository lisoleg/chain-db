"""POP Consensus engine: two-phase consensus state machine.

Phases:
1. TXSET_VOTING: Leader proposes a set of transactions, peers vote
2. BLOCK_VOTING: After TXSET accepted, leader builds block, peers vote

State transitions:
IDLE → PROPOSING → TXSET_VOTING → BLOCK_VOTING → COMMITTING → IDLE

Performance optimizations (v2):
- asyncio.Event replaces polling (eliminates 100ms latency per round)
- O(1) consensus check via native bridge (Rust or Python fallback)
- Incremental state root computation
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from enum import Enum
from typing import Callable, Any

from loguru import logger

from chain_db.consensus.proposal import Proposal
from chain_db.consensus.vote import Vote, VoteCollector
from chain_db.models.block import Block
from chain_db.models.ledger import Ledger
from chain_db.models.transaction import Mempool, Transaction
from chain_db.native import check_consensus as _check_consensus


class ConsensusState(str, Enum):
    """Consensus state machine states."""

    IDLE = "IDLE"
    PROPOSING = "PROPOSING"
    TXSET_VOTING = "TXSET_VOTING"
    BLOCK_VOTING = "BLOCK_VOTING"
    COMMITTING = "COMMITTING"


class POPConsensus:
    """POP (Proof-of-Participation) two-phase consensus engine.

    Coordinates the consensus process:
    1. Leader proposes transactions from the mempool
    2. Peers vote on the transaction set (TXSET_VOTING)
    3. If 2/3+1 approve, leader builds a block
    4. Peers vote on the block (BLOCK_VOTING)
    5. If 2/3+1 approve, block is committed to the ledger

    v2 Optimizations:
    - Uses asyncio.Event instead of polling (saves ~100ms per round)
    - Delegates consensus threshold to native (Rust/Python) module
    - Incremental state root cache avoids full recomputation

    Attributes:
        node_id: This node's identifier.
        is_leader: Whether this node is the consensus leader.
        mempool: Transaction mempool.
        ledger: Blockchain ledger.
        state: Current consensus state.
        total_nodes: Total number of participating nodes.
        consensus_timeout: Seconds before consensus round times out.
        block_interval: Seconds between block proposals.
    """

    def __init__(
        self,
        node_id: str,
        is_leader: bool,
        mempool: Mempool,
        ledger: Ledger,
        total_nodes: int = 3,
        consensus_timeout: int = 5,
        block_interval: int = 3,
    ) -> None:
        """Initialize the consensus engine.

        Args:
            node_id: This node's identifier.
            is_leader: Whether this node is the leader.
            mempool: Transaction mempool reference.
            ledger: Blockchain ledger reference.
            total_nodes: Total number of participating nodes.
            consensus_timeout: Seconds before consensus times out.
            block_interval: Seconds between block proposals.
        """
        self.node_id = node_id
        self.is_leader = is_leader
        self.mempool = mempool
        self.ledger = ledger
        self.total_nodes = total_nodes
        self.consensus_timeout = consensus_timeout
        self.block_interval = block_interval

        self.state = ConsensusState.IDLE
        self.current_round = 0
        self.current_proposal: Proposal | None = None
        self.current_block: Block | None = None
        self.txset_collector = VoteCollector(total_nodes)
        self.block_collector = VoteCollector(total_nodes)

        # v2: Event-driven consensus (replaces polling)
        self._txset_event = asyncio.Event()
        self._block_event = asyncio.Event()

        self._running = False
        self._task: asyncio.Task | None = None
        self._on_block_committed: list[Callable[[Block], Any]] = []
        self._send_message: Callable | None = None

        # v2: Incremental state root cache
        self._state_root_cache: str = "0" * 64

    def set_send_message(self, fn: Callable) -> None:
        """Set the function for sending network messages.

        Args:
            fn: Async callable(message_dict) for sending messages.
        """
        self._send_message = fn

    def on_block_committed(self, callback: Callable[[Block], Any]) -> None:
        """Register a callback for when a block is committed.

        Args:
            callback: Callable that receives the committed Block.
        """
        self._on_block_committed.append(callback)

    async def start(self) -> None:
        """Start the consensus engine.

        If this node is the leader, starts the proposal loop.
        """
        self._running = True
        if self.is_leader:
            self._task = asyncio.create_task(self._leader_loop())
            logger.info("Consensus started as LEADER (node_id={})", self.node_id)
        else:
            logger.info("Consensus started as FOLLOWER (node_id={})", self.node_id)

    async def stop(self) -> None:
        """Stop the consensus engine."""
        self._running = False
        # Wake up any waiting coroutines so they can exit
        self._txset_event.set()
        self._block_event.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        self.state = ConsensusState.IDLE
        logger.info("Consensus stopped (node_id={})", self.node_id)

    async def _leader_loop(self) -> None:
        """Leader's main loop: propose blocks at regular intervals."""
        while self._running:
            await asyncio.sleep(self.block_interval)

            if self.state != ConsensusState.IDLE:
                continue

            if self.mempool.size == 0:
                continue

            await self._propose_transactions()

    async def _propose_transactions(self) -> None:
        """Leader: propose a set of transactions from the mempool."""
        self.state = ConsensusState.PROPOSING
        self.current_round += 1

        # v2: Use mempool snapshot (avoid list() copy overhead)
        transactions = self.mempool.peek_all()

        # Build proposal
        self.current_proposal = Proposal(
            proposal_id=f"{self.node_id}_{self.current_round}_{int(time.time())}",
            proposer=self.node_id,
            transactions=transactions,
            round=self.current_round,
            timestamp=int(time.time()),
        )

        logger.info(
            "Leader proposing {} transactions (round={})",
            len(transactions),
            self.current_round,
        )

        # Move to TXSET voting
        self.state = ConsensusState.TXSET_VOTING
        self._txset_event.clear()
        self.txset_collector.reset(self.current_proposal.proposal_id)

        # Leader auto-votes for its own proposal
        leader_vote = Vote(
            proposal_id=self.current_proposal.proposal_id,
            voter=self.node_id,
            approve=True,
            signature="",
        )
        self.txset_collector.add_vote(leader_vote)

        # Check if self-vote already reaches consensus (single-node edge case)
        if self.txset_collector.has_consensus():
            self._txset_event.set()

        # Broadcast proposal to peers
        if self._send_message:
            await self._send_message({
                "msg_type": "PROPOSAL",
                "payload": self.current_proposal.model_dump(),
            })

        # v2: Event-driven wait (replaces polling)
        await self._wait_for_txset_consensus()

    async def _wait_for_txset_consensus(self) -> None:
        """Wait for TXSET consensus or timeout (event-driven, no polling)."""
        try:
            await asyncio.wait_for(
                self._txset_event.wait(),
                timeout=self.consensus_timeout,
            )
            await self._on_txset_approved()
        except asyncio.TimeoutError:
            logger.warning("TXSET consensus timeout (round={})", self.current_round)
            self.state = ConsensusState.IDLE

    async def _on_txset_approved(self) -> None:
        """Handle TXSET approval: build block and start block voting."""
        if not self.current_proposal:
            self.state = ConsensusState.IDLE
            return

        self.state = ConsensusState.BLOCK_VOTING

        # Build the block
        last_block = self.ledger.last_block
        block_number = (last_block.block_number + 1) if last_block else 0
        prev_hash = last_block.block_hash if last_block else "0" * 64

        # v2: Incremental state root (hash previous root + new block data)
        state_root = self._compute_incremental_state_root()

        self.current_block = Block.create_from_transactions(
            block_number=block_number,
            prev_hash=prev_hash,
            transactions=self.current_proposal.transactions,
            state_root=state_root,
            proposer=self.node_id,
        )

        self._block_event.clear()
        self.block_collector.reset(self.current_proposal.proposal_id)

        # Leader auto-votes for its own block
        block_vote = Vote(
            proposal_id=self.current_proposal.proposal_id,
            voter=self.node_id,
            approve=True,
            signature="",
        )
        self.block_collector.add_vote(block_vote)

        # Check if self-vote already reaches consensus
        if self.block_collector.has_consensus():
            self._block_event.set()

        # Broadcast block for voting
        if self._send_message:
            await self._send_message({
                "msg_type": "VOTE_TXSET",
                "payload": {
                    "block": self.current_block.model_dump(),
                    "proposal_id": self.current_proposal.proposal_id,
                },
            })

        # v2: Event-driven wait
        await self._wait_for_block_consensus()

    async def _wait_for_block_consensus(self) -> None:
        """Wait for BLOCK consensus or timeout (event-driven, no polling)."""
        try:
            await asyncio.wait_for(
                self._block_event.wait(),
                timeout=self.consensus_timeout,
            )
            await self._commit_block()
        except asyncio.TimeoutError:
            logger.warning("BLOCK consensus timeout (round={})", self.current_round)
            self.state = ConsensusState.IDLE

    def _compute_incremental_state_root(self) -> str:
        """Compute state root incrementally (O(1) instead of O(N)).

        Hash = SHA-256(prev_state_root + new_block_data_hash)

        Returns:
            64-character hex string.
        """
        if not self.current_proposal:
            return self._state_root_cache

        # Hash all transaction hashes in the proposal
        tx_data = "".join(tx.tx_hash for tx in self.current_proposal.transactions)
        block_data_hash = hashlib.sha256(tx_data.encode("utf-8")).hexdigest()

        # Incremental: hash(previous_state_root + new_block_data_hash)
        combined = f"{self._state_root_cache}{block_data_hash}"
        return hashlib.sha256(combined.encode("utf-8")).hexdigest()

    async def _commit_block(self) -> None:
        """Commit the current block to the ledger."""
        if not self.current_block:
            self.state = ConsensusState.IDLE
            return

        self.state = ConsensusState.COMMITTING

        # Append block to ledger
        self.ledger.append_block(self.current_block)

        # v2: Update incremental state root cache
        self._state_root_cache = self.current_block.state_root

        # Remove committed transactions from mempool
        for tx in self.current_block.transactions:
            self.mempool.remove(tx.tx_hash)

        logger.info(
            "Block #{} committed with {} transactions",
            self.current_block.block_number,
            len(self.current_block.transactions),
        )

        # Notify callbacks (e.g., TableSync)
        for callback in self._on_block_committed:
            try:
                result = callback(self.current_block)
                if asyncio.iscoroutine(result):
                    await result
            except Exception as e:
                logger.error("Block committed callback error: {}", e)

        # Broadcast new block
        if self._send_message:
            await self._send_message({
                "msg_type": "NEW_BLOCK",
                "payload": self.current_block.model_dump(),
            })

        # Reset state
        self.current_proposal = None
        self.current_block = None
        self.state = ConsensusState.IDLE

    async def on_proposal(self, proposal: Proposal) -> None:
        """Handle a received proposal from the leader.

        Args:
            proposal: The proposal received.
        """
        if self.state != ConsensusState.IDLE:
            logger.debug("Ignoring proposal: not in IDLE state")
            return

        self.state = ConsensusState.TXSET_VOTING
        self.current_proposal = proposal
        self.current_round = proposal.round
        self._txset_event.clear()
        self.txset_collector.reset(proposal.proposal_id)

        # Auto-approve for MVP (no complex validation)
        vote = Vote(
            proposal_id=proposal.proposal_id,
            voter=self.node_id,
            approve=True,
            signature="",
        )

        # Send vote back
        if self._send_message:
            await self._send_message({
                "msg_type": "VOTE_TXSET",
                "payload": vote.model_dump(),
            })

        logger.info("Voted APPROVE on proposal {}", proposal.proposal_id[:16])

    async def on_vote(self, vote: Vote, phase: str = "txset") -> None:
        """Handle a received vote.

        Args:
            vote: The vote received.
            phase: Which voting phase ("txset" or "block").
        """
        if phase == "txset":
            self.txset_collector.add_vote(vote)
            if self.txset_collector.has_consensus():
                self._txset_event.set()  # v2: Signal immediately (no polling)
        elif phase == "block":
            self.block_collector.add_vote(vote)
            if self.block_collector.has_consensus():
                self._block_event.set()  # v2: Signal immediately (no polling)

    async def on_block(self, block_data: dict) -> None:
        """Handle a received block (for followers).

        Args:
            block_data: Block data dictionary.
        """
        try:
            block = Block(**block_data)
            self.ledger.append_block(block)

            # v2: Update incremental state root cache
            self._state_root_cache = block.state_root

            # Remove committed transactions from mempool
            for tx in block.transactions:
                self.mempool.remove(tx.tx_hash)

            # Notify callbacks
            for callback in self._on_block_committed:
                try:
                    result = callback(block)
                    if asyncio.iscoroutine(result):
                        await result
                except Exception as e:
                    logger.error("Block committed callback error: {}", e)

            self.state = ConsensusState.IDLE
            logger.info("Received and committed block #{}", block.block_number)
        except Exception as e:
            logger.error("Error processing received block: {}", e)
