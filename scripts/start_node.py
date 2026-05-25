"""CLI script to start a ChainDB node."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from chain_db.config import MULTI_NODE_CONFIGS, NodeConfig, load_config
from chain_db.main import run_server


def main() -> None:
    """Parse CLI arguments and start a ChainDB node."""
    parser = argparse.ArgumentParser(description="Start a ChainDB node")
    parser.add_argument(
        "--config",
        type=str,
        default=None,
        help="Path to JSON configuration file",
    )
    parser.add_argument(
        "--node",
        type=str,
        default=None,
        help="Node template name (node_1, node_2, node_3)",
    )
    parser.add_argument(
        "--genesis",
        action="store_true",
        default=False,
        help="Initialize genesis block on startup",
    )
    parser.add_argument(
        "--host",
        type=str,
        default=None,
        help="Override bind address",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="Override P2P port",
    )
    parser.add_argument(
        "--rpc-port",
        type=int,
        default=None,
        help="Override RPC port",
    )
    parser.add_argument(
        "--leader",
        action="store_true",
        default=False,
        help="Set this node as the consensus leader",
    )

    args = parser.parse_args()

    # Load config: --config file > --node template > environment
    if args.config:
        config = load_config(args.config)
    elif args.node:
        template = MULTI_NODE_CONFIGS.get(args.node)
        if template is None:
            print(f"Unknown node template: {args.node}. Available: {list(MULTI_NODE_CONFIGS.keys())}")
            sys.exit(1)
        config = NodeConfig.from_dict(template)
    else:
        config = NodeConfig.from_env()

    # Apply CLI overrides
    if args.host:
        config.host = args.host
    if args.port:
        config.port = args.port
    if args.rpc_port:
        config.rpc_port = args.rpc_port
    if args.leader:
        config.is_leader = True

    # Ensure data directory exists
    Path(config.data_dir).mkdir(parents=True, exist_ok=True)

    print(f"Starting ChainDB node: {config.node_id}")
    print(f"  P2P: {config.host}:{config.port}")
    print(f"  RPC: {config.host}:{config.rpc_port}")
    print(f"  Leader: {config.is_leader}")
    print(f"  Peers: {config.peers}")

    run_server(config)


if __name__ == "__main__":
    main()
