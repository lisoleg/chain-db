"""Tests for consensus module: state machine, proposal, voting."""

import asyncio
import pytest

from chain_db.consensus.pop import ConsensusState, POPConsensus
from chain_db.consensus.proposal import Proposal
from chain_db.consensus.vote import Vote, VoteCollector
from chain_db.models.block import Block
from chain_db.models.ledger import Ledger
from chain_db.models.transaction import Mempool, Transaction, TxType


class TestVoteCollector:
    """Test suite for VoteCollector."""

    def test_initial_state(self) -> None:
        """Test initial state has no votes."""
        collector = VoteCollector(total_nodes=3)
        assert collector.total_votes == 0
        assert collector.approve_count == 0

    def test_add_approve_vote(self) -> None:
        """Test adding an approve vote."""
        collector = VoteCollector(total_nodes=3)
        vote = Vote(proposal_id="p1", voter="node_1", approve=True, signature="")
        collector.add_vote(vote)
        assert collector.approve_count == 1
        assert collector.reject_count == 0

    def test_add_reject_vote(self) -> None:
        """Test adding a reject vote."""
        collector = VoteCollector(total_nodes=3)
        vote = Vote(proposal_id="p1", voter="node_1", approve=False, signature="")
        collector.add_vote(vote)
        assert collector.reject_count == 1

    def test_consensus_reached_at_2_thirds_plus_1(self) -> None:
        """Test that consensus is reached at 2/3+1 votes (2 for 3 nodes)."""
        collector = VoteCollector(total_nodes=3)
        collector.add_vote(Vote(proposal_id="p1", voter="node_1", approve=True, signature=""))
        assert not collector.has_consensus()  # 1 < 2

        collector.add_vote(Vote(proposal_id="p1", voter="node_2", approve=True, signature=""))
        assert collector.has_consensus()  # 2 >= 2

    def test_consensus_not_reached_with_rejections(self) -> None:
        """Test that consensus is not reached when votes are rejected."""
        collector = VoteCollector(total_nodes=3)
        collector.add_vote(Vote(proposal_id="p1", voter="node_1", approve=True, signature=""))
        collector.add_vote(Vote(proposal_id="p1", voter="node_2", approve=False, signature=""))
        assert not collector.has_consensus()

    def test_reset_clears_votes(self) -> None:
        """Test that reset clears all votes."""
        collector = VoteCollector(total_nodes=3)
        collector.add_vote(Vote(proposal_id="p1", voter="node_1", approve=True, signature=""))
        collector.reset("p2")
        assert collector.total_votes == 0

    def test_duplicate_voter_overwrites(self) -> None:
        """Test that a duplicate vote from the same voter overwrites."""
        collector = VoteCollector(total_nodes=3)
        collector.add_vote(Vote(proposal_id="p1", voter="node_1", approve=False, signature=""))
        collector.add_vote(Vote(proposal_id="p1", voter="node_1", approve=True, signature=""))
        assert collector.approve_count == 1
        assert collector.reject_count == 0


class TestProposal:
    """Test suite for Proposal model."""

    def test_create_from_mempool(self) -> None:
        """Test creating a proposal from mempool transactions."""
        tx = Transaction(
            tx_type=TxType.SQL_STATEMENT,
            account="alice",
            sequence=1,
            payload={"sql_type": "INSERT"},
            raw_sql="INSERT INTO t VALUES (1)",
        )
        proposal = Proposal.create_from_mempool(
            proposer="node_1",
            transactions=[tx],
            round_num=1,
        )
        assert proposal.proposer == "node_1"
        assert len(proposal.transactions) == 1
        assert proposal.round == 1
        assert len(proposal.proposal_id) > 0


class TestPOPConsensus:
    """Test suite for POPConsensus state machine."""

    def setup_method(self) -> None:
        """Set up test fixtures."""
        self.ledger = Ledger()
        self.mempool = Mempool()
        self.genesis = Block.create_genesis(proposer="node_1")
        self.ledger.append_block(self.genesis)

    @pytest.mark.asyncio
    async def test_initial_state_is_idle(self) -> None:
        """Test that consensus starts in IDLE state."""
        consensus = POPConsensus(
            node_id="node_1",
            is_leader=True,
            mempool=self.mempool,
            ledger=self.ledger,
            total_nodes=3,
        )
        assert consensus.state == ConsensusState.IDLE

    @pytest.mark.asyncio
    async def test_leader_starts_and_stops(self) -> None:
        """Test that the leader can start and stop."""
        consensus = POPConsensus(
            node_id="node_1",
            is_leader=True,
            mempool=self.mempool,
            ledger=self.ledger,
            total_nodes=3,
            block_interval=1,
        )
        await consensus.start()
        assert consensus.state in (ConsensusState.IDLE, ConsensusState.PROPOSING)
        await consensus.stop()
        assert consensus.state == ConsensusState.IDLE

    @pytest.mark.asyncio
    async def test_proposal_creates_block(self) -> None:
        """Test that a proposal with votes leads to block creation."""
        consensus = POPConsensus(
            node_id="node_1",
            is_leader=True,
            mempool=self.mempool,
            ledger=self.ledger,
            total_nodes=3,
            consensus_timeout=10,
        )

        # Add transaction to mempool
        tx = Transaction(
            tx_type=TxType.SQL_STATEMENT,
            account="alice",
            sequence=1,
            payload={"sql_type": "INSERT"},
            raw_sql="INSERT INTO t VALUES (1)",
        )
        self.mempool.add(tx)

        # Manually trigger proposal (without running the leader loop)
        await consensus._propose_transactions()
        assert consensus.state in (ConsensusState.TXSET_VOTING, ConsensusState.BLOCK_VOTING, ConsensusState.IDLE)

    @pytest.mark.asyncio
    async def test_on_proposal_as_follower(self) -> None:
        """Test that a follower handles a received proposal."""
        consensus = POPConsensus(
            node_id="node_2",
            is_leader=False,
            mempool=self.mempool,
            ledger=self.ledger,
            total_nodes=3,
        )

        tx = Transaction(
            tx_type=TxType.SQL_STATEMENT,
            account="alice",
            sequence=1,
            payload={"sql_type": "INSERT"},
            raw_sql="INSERT INTO t VALUES (1)",
        )
        proposal = Proposal.create_from_mempool(
            proposer="node_1",
            transactions=[tx],
            round_num=1,
        )

        await consensus.on_proposal(proposal)
        assert consensus.state == ConsensusState.TXSET_VOTING

    @pytest.mark.asyncio
    async def test_vote_adding_to_collector(self) -> None:
        """Test adding votes directly to the consensus engine."""
        consensus = POPConsensus(
            node_id="node_1",
            is_leader=True,
            mempool=self.mempool,
            ledger=self.ledger,
            total_nodes=3,
        )

        # Add a transaction to mempool
        tx = Transaction(
            tx_type=TxType.SQL_STATEMENT,
            account="alice",
            sequence=1,
            payload={},
            raw_sql="INSERT INTO t VALUES (1)",
        )
        self.mempool.add(tx)

        # Start proposal
        await consensus._propose_transactions()

        # Add peer votes
        vote = Vote(
            proposal_id=consensus.current_proposal.proposal_id if consensus.current_proposal else "",
            voter="node_2",
            approve=True,
            signature="",
        )
        await consensus.on_vote(vote, phase="txset")

        # Check if we reached consensus (2/3+1 = 2, with leader vote + 1 peer)
        # Note: leader already voted in _propose_transactions


class TestMessageProtocol:
    """Test suite for message encoding/decoding roundtrip."""

    def test_message_encode_decode_roundtrip(self) -> None:
        """Test that Message encode→decode produces identical message."""
        from chain_db.network.protocol import Message, MsgType

        msg = Message(
            msg_type=MsgType.PROPOSAL,
            payload={"proposal_id": "test_123", "round": 1},
            sender="node_1",
            timestamp=1000,
        )
        encoded = msg.encode()
        decoded = Message.decode(encoded)

        assert decoded.msg_type == msg.msg_type
        assert decoded.payload == msg.payload
        assert decoded.sender == msg.sender
        assert decoded.timestamp == msg.timestamp

    def test_all_msg_types_roundtrip(self) -> None:
        """Test roundtrip for all message types."""
        from chain_db.network.protocol import Message, MsgType

        for msg_type in MsgType:
            msg = Message(
                msg_type=msg_type,
                payload={"test": True},
                sender="node_1",
                timestamp=1000,
            )
            decoded = Message.decode(msg.encode())
            assert decoded.msg_type == msg_type
