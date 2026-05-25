"""Native acceleration bridge for ChainDB consensus hot paths.

Priority order:
1. chainsql_core (Rust/PyO3 C extension) — fastest
2. Pure Python fallback — always available

Usage:
    from chain_db.native import fast_hash, merkle_root, check_consensus
"""

from __future__ import annotations

import hashlib
from typing import Sequence

# Try to import the Rust extension
_USE_RUST = False
try:
    from chainsql_core import (  # type: ignore[import-untyped]
        fast_hash as _rust_fast_hash,
        merkle_root as _rust_merkle_root,
        check_consensus as _rust_check_consensus,
        consensus_threshold as _rust_consensus_threshold,
        compute_tx_hash as _rust_compute_tx_hash,
        compute_block_hash as _rust_compute_block_hash,
        batch_hash as _rust_batch_hash,
    )
    _USE_RUST = True
except ImportError:
    pass


# ─── Pure Python fallbacks ──────────────────────────────────────────


def _py_fast_hash(input_str: str) -> str:
    """Pure Python SHA-256 hex digest."""
    return hashlib.sha256(input_str.encode("utf-8")).hexdigest()


def _py_batch_hash(inputs: list[str]) -> list[str]:
    """Pure Python batch SHA-256."""
    return [_py_fast_hash(s) for s in inputs]


def _py_merkle_root(hashes: Sequence[str]) -> str:
    """Pure Python binary Merkle tree root."""
    if not hashes:
        return "0" * 64
    if len(hashes) == 1:
        return hashes[0]

    level = list(hashes)
    while len(level) > 1:
        next_level: list[str] = []
        i = 0
        while i < len(level):
            left = level[i]
            right = level[i + 1] if i + 1 < len(level) else left
            combined = f"{left}{right}"
            next_level.append(hashlib.sha256(combined.encode("utf-8")).hexdigest())
            i += 2
        level = next_level

    return level[0]


def _py_check_consensus(approve_count: int, total_nodes: int) -> bool:
    """Pure Python consensus check: ceil(2N/3) approvals required."""
    if total_nodes == 0:
        return False
    required = (2 * total_nodes + 2) // 3  # ceil(2N/3)
    return approve_count >= required


def _py_consensus_threshold(total_nodes: int) -> int:
    """Pure Python: minimum approvals for consensus."""
    if total_nodes == 0:
        return 0
    return (2 * total_nodes + 2) // 3


def _py_compute_tx_hash(tx_type: str, account: str, sequence: int, payload_json: str, raw_sql: str) -> str:
    """Pure Python transaction hash: SHA-256(tx_type + account + seq + payload + sql)."""
    raw = f"{tx_type}{account}{sequence}{payload_json}{raw_sql}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _py_compute_block_hash(block_number: int, prev_hash: str, tx_root: str, state_root: str, timestamp: int) -> str:
    """Pure Python block hash: SHA-256(num + prev + tx_root + state_root + ts)."""
    raw = f"{block_number}{prev_hash}{tx_root}{state_root}{timestamp}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# ─── Public API (auto-selects Rust or Python) ───────────────────────

if _USE_RUST:
    fast_hash = _rust_fast_hash
    batch_hash = _rust_batch_hash
    merkle_root = _rust_merkle_root
    check_consensus = _rust_check_consensus
    consensus_threshold = _rust_consensus_threshold
    compute_tx_hash = _rust_compute_tx_hash
    compute_block_hash = _rust_compute_block_hash
else:
    fast_hash = _py_fast_hash
    batch_hash = _py_batch_hash
    merkle_root = _py_merkle_root
    check_consensus = _py_check_consensus
    consensus_threshold = _py_consensus_threshold
    compute_tx_hash = _py_compute_tx_hash
    compute_block_hash = _py_compute_block_hash


def is_rust_accelerated() -> bool:
    """Check if the Rust C extension is loaded."""
    return _USE_RUST


__all__ = [
    "fast_hash",
    "batch_hash",
    "merkle_root",
    "check_consensus",
    "consensus_threshold",
    "compute_tx_hash",
    "compute_block_hash",
    "is_rust_accelerated",
]
