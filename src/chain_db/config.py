"""Global configuration for ChainDB nodes."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any


@dataclass
class NodeConfig:
    """Configuration for a single ChainDB node.

    Attributes:
        node_id: Unique identifier for this node.
        host: Bind address for P2P and RPC.
        port: P2P listening port.
        rpc_port: JSON-RPC HTTP port.
        peers: List of peer addresses (host:port format).
        is_leader: Whether this node is the consensus leader.
        consensus_timeout: Seconds before consensus round times out.
        block_interval: Seconds between block proposals.
        db_path: Path to the local SQLite database file.
        data_dir: Directory for persistent state files.
    """

    node_id: str = "node_1"
    host: str = "127.0.0.1"
    port: int = 5000
    rpc_port: int = 8000
    peers: list[str] = field(default_factory=list)
    is_leader: bool = False
    consensus_timeout: int = 5
    block_interval: int = 3
    db_path: str = "chain_db.sqlite"
    data_dir: str = "./data"

    def to_dict(self) -> dict[str, Any]:
        """Serialize config to dictionary."""
        return {
            "node_id": self.node_id,
            "host": self.host,
            "port": self.port,
            "rpc_port": self.rpc_port,
            "peers": self.peers,
            "is_leader": self.is_leader,
            "consensus_timeout": self.consensus_timeout,
            "block_interval": self.block_interval,
            "db_path": self.db_path,
            "data_dir": self.data_dir,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> NodeConfig:
        """Deserialize config from dictionary."""
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})

    @classmethod
    def from_env(cls) -> NodeConfig:
        """Load configuration from environment variables.

        Environment variables use CHAIN_DB_ prefix, e.g. CHAIN_DB_NODE_ID.
        """
        peers_str = os.getenv("CHAIN_DB_PEERS", "")
        peers = [p.strip() for p in peers_str.split(",") if p.strip()] if peers_str else []
        return cls(
            node_id=os.getenv("CHAIN_DB_NODE_ID", "node_1"),
            host=os.getenv("CHAIN_DB_HOST", "127.0.0.1"),
            port=int(os.getenv("CHAIN_DB_PORT", "5000")),
            rpc_port=int(os.getenv("CHAIN_DB_RPC_PORT", "8000")),
            peers=peers,
            is_leader=os.getenv("CHAIN_DB_IS_LEADER", "false").lower() == "true",
            consensus_timeout=int(os.getenv("CHAIN_DB_CONSENSUS_TIMEOUT", "5")),
            block_interval=int(os.getenv("CHAIN_DB_BLOCK_INTERVAL", "3")),
            db_path=os.getenv("CHAIN_DB_DB_PATH", "chain_db.sqlite"),
            data_dir=os.getenv("CHAIN_DB_DATA_DIR", "./data"),
        )


# Multi-node configuration templates for quick setup
MULTI_NODE_CONFIGS: dict[str, dict[str, Any]] = {
    "node_1": {
        "node_id": "node_1",
        "host": "127.0.0.1",
        "port": 5001,
        "rpc_port": 8001,
        "peers": ["127.0.0.1:5002", "127.0.0.1:5003"],
        "is_leader": True,
        "consensus_timeout": 5,
        "block_interval": 3,
        "db_path": "data/node_1/chain_db.sqlite",
        "data_dir": "data/node_1",
    },
    "node_2": {
        "node_id": "node_2",
        "host": "127.0.0.1",
        "port": 5002,
        "rpc_port": 8002,
        "peers": ["127.0.0.1:5001", "127.0.0.1:5003"],
        "is_leader": False,
        "consensus_timeout": 5,
        "block_interval": 3,
        "db_path": "data/node_2/chain_db.sqlite",
        "data_dir": "data/node_2",
    },
    "node_3": {
        "node_id": "node_3",
        "host": "127.0.0.1",
        "port": 5003,
        "rpc_port": 8003,
        "peers": ["127.0.0.1:5001", "127.0.0.1:5002"],
        "is_leader": False,
        "consensus_timeout": 5,
        "block_interval": 3,
        "db_path": "data/node_3/chain_db.sqlite",
        "data_dir": "data/node_3",
    },
}


def load_config(config_path: str | None = None) -> NodeConfig:
    """Load configuration from file or environment.

    Args:
        config_path: Optional path to a YAML/JSON config file.

    Returns:
        Loaded NodeConfig instance.
    """
    if config_path:
        import json
        from pathlib import Path

        path = Path(config_path)
        if not path.exists():
            raise FileNotFoundError(f"Config file not found: {config_path}")
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return NodeConfig.from_dict(data)

    return NodeConfig.from_env()
