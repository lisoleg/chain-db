# chainsql-core: Rust C Extension for ChainDB

ChainDB 共识引擎的 Rust 加速模块，通过 PyO3 暴露给 Python。

## 编译前提

- Rust toolchain: `rustup default stable`
- maturin: `pip install maturin`

## 编译 & 安装

```bash
# 开发模式（快速编译，调试符号）
cd native/chainsql_core
maturin develop

# 生产模式（LTO 优化，最快速）
maturin develop --release
```

## 验证

```python
from chain_db.native import is_rust_accelerated
print(is_rust_accelerated())  # True = Rust extension loaded
```

## 提供的函数

| 函数 | 说明 | 对比 Python |
|------|------|------------|
| `fast_hash(input)` | SHA-256 单次哈希 | ~3-5x |
| `batch_hash(inputs)` | SHA-256 批量并行哈希 | ~5-10x |
| `merkle_root(hashes)` | 二叉 Merkle 树根 | ~3-5x |
| `check_consensus(approve_count, total_nodes)` | O(1) 共识判断 | ~2x |
| `consensus_threshold(total_nodes)` | 最小共识票数 | O(1) |
| `compute_tx_hash(tx_type, account, seq, payload_json, raw_sql)` | 交易哈希 | ~3-5x |
| `compute_block_hash(num, prev_hash, tx_root, state_root, ts)` | 区块哈希 | ~3-5x |

## 架构设计

```
chain_db/src/chain_db/native/__init__.py  ← Python 桥接层
  ├── 优先导入 chainsql_core (Rust C 扩展)
  └── Fallback: 纯 Python 实现（零依赖）
  
chain_db/native/chainsql_core/src/lib.rs   ← Rust 加速实现
  ├── sha2 crate: 硬件加速 SHA-256
  ├── rayon crate: 并行批量哈希
  └── PyO3: Python ↔ Rust FFI
```

## Fallback 策略

如果 Rust 扩展未编译或加载失败，所有函数自动降级到纯 Python 实现。
**功能完全等价，只是性能不同。** 这保证了 ChainDB 在任何环境都能运行。

## Benchmark

```
当前（纯 Python fallback）:
  Consensus Check O(1) vs O(N): 3.1x speedup
  State Root Incremental vs Full: 46x speedup
  SHA-256 / Merkle: native bridge ≈ Python (需 Rust 编译后才有加速)

编译 Rust 后（预期）:
  SHA-256: ~3-5x
  Batch Hash: ~5-10x
  Merkle Root: ~3-5x
```
