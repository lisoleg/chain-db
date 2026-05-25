"""ChainDB entry point: FastAPI application factory and server startup.

Integrates all ChainDB modules:
- Ledger + Mempool (blockchain state)
- Database + TableRegistry (local storage)
- SQL Parser + Converter (SQL processing)
- Consensus (POP engine)
- P2P Network (inter-node communication)
- TableSync (blockchain → database sync)
- IncrementalSyncEngine (fingerprinting + change detection)
- RPC API (JSON-RPC interface)
- Dashboard (interactive web visualization)
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any

import uvicorn
from fastapi import FastAPI
from loguru import logger

from chain_db.api.handlers import RPCHandler
from chain_db.api.rpc import create_rpc_router
from chain_db.config import NodeConfig, load_config
from chain_db.consensus.pop import POPConsensus
from chain_db.models.block import Block
from chain_db.models.ledger import Ledger
from chain_db.models.transaction import Mempool
from chain_db.network.p2p import P2PNetwork
from chain_db.network.protocol import Message, MsgType
from chain_db.network.router import Router
from chain_db.sql.converter import SQLConverter
from chain_db.storage.database import Database
from chain_db.storage.query import QueryExecutor
from chain_db.storage.table_registry import TableRegistry
from chain_db.sync.state_manager import StateManager
from chain_db.sync.table_sync import TableSync
from chain_db.sync.incremental import IncrementalSyncEngine


def create_app(config: NodeConfig | None = None) -> FastAPI:
    """Create and configure the FastAPI application with all modules.

    Args:
        config: Optional NodeConfig. If None, loads from environment.

    Returns:
        Configured FastAPI application instance.
    """
    cfg = config or load_config()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        """Application lifespan: startup and shutdown."""
        # 1. Core state
        ledger = Ledger()
        mempool = Mempool()
        registry = TableRegistry()

        # 2. Storage layer
        database = Database(db_path=cfg.db_path)
        await database.initialize()

        # 3. SQL processing
        converter = SQLConverter(registry)

        # 4. Query executor
        query_executor = QueryExecutor(database, registry)

        # 5. Table sync
        state_manager = StateManager(data_dir=cfg.data_dir)
        table_sync = TableSync(database, ledger, registry, state_manager)

        # 5.5. Incremental sync engine (fingerprinting + change detection)
        incremental_engine = IncrementalSyncEngine(ledger)

        # 6. Create genesis block if chain is empty
        if ledger.height == 0:
            genesis = Block.create_genesis(proposer=cfg.node_id)
            ledger.append_block(genesis)
            logger.info("Genesis block created: {}", genesis.block_hash[:16])

        # 7. Consensus engine
        consensus = POPConsensus(
            node_id=cfg.node_id,
            is_leader=cfg.is_leader,
            mempool=mempool,
            ledger=ledger,
            total_nodes=max(len(cfg.peers) + 1, 3),
            consensus_timeout=cfg.consensus_timeout,
            block_interval=cfg.block_interval,
        )

        # Register table_sync callback on consensus
        consensus.on_block_committed(table_sync.sync_block)

        # 8. P2P Network
        network = P2PNetwork(node_id=cfg.node_id, host=cfg.host, port=cfg.port)

        # 9. Message router
        router = Router()

        async def handle_proposal(message: Message) -> None:
            """Handle incoming proposal messages."""
            from chain_db.consensus.proposal import Proposal
            proposal = Proposal(**message.payload)
            await consensus.on_proposal(proposal)

        async def handle_vote_txset(message: Message) -> None:
            """Handle incoming TXSET vote messages."""
            from chain_db.consensus.vote import Vote
            vote = Vote(**message.payload)
            await consensus.on_vote(vote, phase="txset")

        async def handle_vote_block(message: Message) -> None:
            """Handle incoming BLOCK vote messages."""
            from chain_db.consensus.vote import Vote
            vote = Vote(**message.payload)
            await consensus.on_vote(vote, phase="block")

        async def handle_new_block(message: Message) -> None:
            """Handle incoming new block messages (for followers)."""
            block_data = message.payload
            if "block" in block_data:
                await consensus.on_block(block_data["block"])
            else:
                await consensus.on_block(block_data)

        async def handle_sync_request(message: Message) -> None:
            """Handle sync request messages."""
            # MVP: log and ignore
            logger.debug("Sync request received from {}", message.sender)

        router.register(MsgType.PROPOSAL, handle_proposal)
        router.register(MsgType.VOTE_TXSET, handle_vote_txset)
        router.register(MsgType.VOTE_BLOCK, handle_vote_block)
        router.register(MsgType.NEW_BLOCK, handle_new_block)
        router.register(MsgType.SYNC_REQUEST, handle_sync_request)

        # Set up P2P message callback → router
        network.on_message(router.route)

        # Set up consensus broadcast via P2P
        async def broadcast_message(msg_dict: dict[str, Any]) -> None:
            """Broadcast consensus messages to peers."""
            msg_type_str = msg_dict.get("msg_type", "PROPOSAL")
            try:
                msg_type = MsgType(msg_type_str)
            except ValueError:
                msg_type = MsgType.PROPOSAL

            message = Message(
                msg_type=msg_type,
                payload=msg_dict.get("payload", {}),
                sender=cfg.node_id,
            )
            await network.broadcast(message)

        consensus.set_send_message(broadcast_message)

        # 10. Start services
        try:
            await network.start()
            # Connect to peers (with retry delay for startup ordering)
            if cfg.peers:
                await asyncio.sleep(1)
                await network.connect_to_peers(cfg.peers)

            await consensus.start()
            await table_sync.start()

            logger.info("ChainDB node {} started (leader={})", cfg.node_id, cfg.is_leader)
        except Exception as e:
            logger.error("Failed to start services: {}", e)

        # Store references on app.state for access in routes
        app.state.config = cfg
        app.state.ledger = ledger
        app.state.mempool = mempool
        app.state.registry = registry
        app.state.database = database
        app.state.converter = converter
        app.state.query_executor = query_executor
        app.state.consensus = consensus
        app.state.network = network
        app.state.table_sync = table_sync
        app.state.incremental_engine = incremental_engine

        yield

        # Shutdown
        logger.info("ChainDB node {} shutting down", cfg.node_id)
        await consensus.stop()
        await network.stop()
        await database.close()
        await state_manager.save()

    app = FastAPI(
        title="ChainDB",
        version="0.1.0",
        description="A blockchain database system inspired by ChainSQL",
        lifespan=lifespan,
    )

    # Health check endpoint
    @app.get("/health")
    async def health_check() -> dict[str, Any]:
        """Health check endpoint."""
        return {"code": 0, "data": {"status": "ok"}, "message": "success"}

    # Register RPC routes
    # We need to create the handler lazily since it depends on app.state
    # Use a dependency injection pattern
    @app.on_event("startup")
    async def setup_rpc():
        """Set up RPC routes after lifespan has initialized modules."""
        handler = RPCHandler(
            converter=app.state.converter,
            mempool=app.state.mempool,
            query_executor=app.state.query_executor,
            ledger=app.state.ledger,
            registry=app.state.registry,
            table_sync=app.state.table_sync,
            incremental_engine=app.state.incremental_engine,
        )
        rpc_router = create_rpc_router(handler)
        app.include_router(rpc_router)

        # Dashboard routes
        from chain_db.dashboard.routes import create_dashboard_router
        dashboard_router = create_dashboard_router()
        app.include_router(dashboard_router)

        # Serve dashboard static files
        from fastapi.staticfiles import StaticFiles
        from pathlib import Path
        dashboard_dir = Path(__file__).parent / "dashboard" / "static"
        if dashboard_dir.exists():
            app.mount("/dashboard", StaticFiles(directory=str(dashboard_dir), html=True), name="dashboard")

        logger.info("Dashboard available at http://{}:{}/dashboard/index.html", cfg.host, cfg.rpc_port)

    return app


def run_server(config: NodeConfig | None = None) -> None:
    """Run the ChainDB server.

    Args:
        config: Optional NodeConfig instance.
    """
    cfg = config or load_config()
    app = create_app(cfg)
    uvicorn.run(app, host=cfg.host, port=cfg.rpc_port)


if __name__ == "__main__":
    run_server()
