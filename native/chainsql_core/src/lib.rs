//! chainsql-core: ChainDB consensus engine acceleration module
//!
//! This Rust extension replaces the hot paths of the Python consensus module:
//! - SHA-256 hash computation (batch & single)
//! - Merkle tree root computation
//! - Transaction serialization (msgpack-compatible)
//! - Vote consensus threshold checking (O(1))
//!
//! Build: `maturin develop --release`
//! Usage: `from chainsql_core import fast_hash, merkle_root, check_consensus`

use pyo3::prelude::*;
use sha2::{Sha256, Digest};
use std::collections::HashMap;

// ─── SHA-256 Fast Hash ─────────────────────────────────────────────

/// Compute SHA-256 hex digest of a string.
#[pyfunction]
fn fast_hash(input: &str) -> String {
    let mut hasher = Sha256::new();
    hasher.update(input.as_bytes());
    format!("{:x}", hasher.finalize())
}

/// Batch SHA-256: compute hashes for multiple inputs in parallel.
#[pyfunction]
fn batch_hash(inputs: Vec<String>) -> Vec<String> {
    use rayon::prelude::*;
    inputs
        .par_iter()
        .map(|input| {
            let mut hasher = Sha256::new();
            hasher.update(input.as_bytes());
            format!("{:x}", hasher.finalize())
        })
        .collect()
}

// ─── Merkle Tree ────────────────────────────────────────────────────

/// Compute a binary Merkle tree root from a list of hex-encoded hashes.
///
/// If the list is empty, returns "0" * 64 (zero hash).
/// If odd number of leaves, the last leaf is duplicated.
#[pyfunction]
fn merkle_root(hashes: Vec<String>) -> String {
    if hashes.is_empty() {
        return "0".repeat(64);
    }
    if hashes.len() == 1 {
        return hashes[0].clone();
    }

    let mut level = hashes;

    while level.len() > 1 {
        let mut next_level = Vec::with_capacity((level.len() + 1) / 2);
        let mut i = 0;
        while i < level.len() {
            let left = &level[i];
            let right = if i + 1 < level.len() {
                &level[i + 1]
            } else {
                left // duplicate last if odd
            };
            let combined = format!("{}{}", left, right);
            let mut hasher = Sha256::new();
            hasher.update(combined.as_bytes());
            next_level.push(format!("{:x}", hasher.finalize()));
            i += 2;
        }
        level = next_level;
    }

    level.into_iter().next().unwrap()
}

// ─── Consensus Threshold ───────────────────────────────────────────

/// Check whether consensus has been reached given approve_count and total_nodes.
///
/// Consensus requires ceil(2N/3) approvals where N = total_nodes.
#[pyfunction]
fn check_consensus(approve_count: usize, total_nodes: usize) -> bool {
    if total_nodes == 0 {
        return false;
    }
    let required = (2 * total_nodes + 2) / 3; // ceil(2N/3)
    approve_count >= required
}

/// Compute the minimum required approvals for consensus.
#[pyfunction]
fn consensus_threshold(total_nodes: usize) -> usize {
    if total_nodes == 0 {
        return 0;
    }
    (2 * total_nodes + 2) / 3 // ceil(2N/3)
}

// ─── Transaction Hash ──────────────────────────────────────────────

/// Compute a transaction hash matching the Python formula:
/// SHA-256(tx_type + account + sequence + payload_json + raw_sql)
#[pyfunction]
fn compute_tx_hash(tx_type: &str, account: &str, sequence: u64, payload_json: &str, raw_sql: &str) -> String {
    let raw = format!("{}{}{}{}{}", tx_type, account, sequence, payload_json, raw_sql);
    let mut hasher = Sha256::new();
    hasher.update(raw.as_bytes());
    format!("{:x}", hasher.finalize())
}

/// Compute a block hash matching the Python formula:
/// SHA-256(block_number + prev_hash + tx_root + state_root + timestamp)
#[pyfunction]
fn compute_block_hash(block_number: u64, prev_hash: &str, tx_root: &str, state_root: &str, timestamp: u64) -> String {
    let raw = format!("{}{}{}{}{}", block_number, prev_hash, tx_root, state_root, timestamp);
    let mut hasher = Sha256::new();
    hasher.update(raw.as_bytes());
    format!("{:x}", hasher.finalize())
}

// ─── Module Definition ─────────────────────────────────────────────

#[pymodule]
fn chainsql_core(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(fast_hash, m)?)?;
    m.add_function(wrap_pyfunction!(batch_hash, m)?)?;
    m.add_function(wrap_pyfunction!(merkle_root, m)?)?;
    m.add_function(wrap_pyfunction!(check_consensus, m)?)?;
    m.add_function(wrap_pyfunction!(consensus_threshold, m)?)?;
    m.add_function(wrap_pyfunction!(compute_tx_hash, m)?)?;
    m.add_function(wrap_pyfunction!(compute_block_hash, m)?)?;
    Ok(())
}

// ─── Tests ──────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_fast_hash_deterministic() {
        let h1 = fast_hash("hello");
        let h2 = fast_hash("hello");
        assert_eq!(h1, h2);
        assert_eq!(h1.len(), 64);
    }

    #[test]
    fn test_merkle_root_single() {
        let h = vec!["abc123".to_string()];
        assert_eq!(merkle_root(h.clone()), h[0]);
    }

    #[test]
    fn test_merkle_root_empty() {
        assert_eq!(merkle_root(vec![]), "0".repeat(64));
    }

    #[test]
    fn test_consensus_3_nodes() {
        // 3 nodes: ceil(6/3) = 2
        assert!(!check_consensus(1, 3));
        assert!(check_consensus(2, 3));
    }

    #[test]
    fn test_consensus_5_nodes() {
        // 5 nodes: ceil(10/3) = 4
        assert!(!check_consensus(3, 5));
        assert!(check_consensus(4, 5));
    }
}
