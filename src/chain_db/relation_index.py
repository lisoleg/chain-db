"""Relation Index: "Information resides in associations, not entities".

Based on the insight that intelligence emerges from the SPEED and BREADTH
of establishing associations (Ftel), not from the volume of stored data.

The RelationIndex maintains a dynamic graph of associations between:
  - Tables (table-table relationships via FK/column overlap)
  - Transactions (tx-tx causal and reference chains)
  - Accounts (account-table, account-tx activity patterns)
  - Blocks (block-table, block-account temporal patterns)

Key metrics:
  - Ftel (Association Throughput): rate of new associations per unit time
  - Centrality: which entities serve as association hubs
  - Density: overall connectedness of the relation graph
  - Entropy: information-theoretic diversity of association types

This module is the realization of the "Relation Index Library" paradigm
described in the ChainDB paper: entities degrade to index pointers,
associations become first-class citizens.
"""

from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from loguru import logger

from chain_db.models.block import Block
from chain_db.models.transaction import Transaction, TxType


class RelationType(str, Enum):
    """Types of associations between entities."""

    TABLE_TOUCHES_BLOCK = "table_touches_block"
    TABLE_REFERENCES_TABLE = "table_ref_table"
    TX_CAUSES_TX = "tx_causes_tx"
    TX_TOUCHES_TABLE = "tx_touches_table"
    ACCOUNT_OWNS_TABLE = "account_owns_table"
    ACCOUNT_SUBMITS_TX = "account_submits_tx"
    BLOCK_CONTAINS_TX = "block_contains_tx"


@dataclass
class Relation:
    """A single association between two entities.

    Attributes:
        source: Source entity identifier.
        target: Target entity identifier.
        rel_type: Type of the association.
        weight: Association strength (default 1.0).
        block_number: Block when this relation was established.
        timestamp: Unix timestamp when this relation was created.
        metadata: Additional context about the relation.
    """

    source: str
    target: str
    rel_type: RelationType
    weight: float = 1.0
    block_number: int = 0
    timestamp: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class EntityProfile:
    """Profile of an entity in the relation graph.

    Tracks association statistics for a single entity.

    Attributes:
        entity_id: Unique identifier.
        relation_count: Total number of outgoing relations.
        incoming_count: Total number of incoming relations.
        first_seen_block: Block when first observed.
        last_seen_block: Block when last observed.
        types_touched: Set of entity types this entity connects to.
    """

    entity_id: str
    relation_count: int = 0
    incoming_count: int = 0
    first_seen_block: int = 0
    last_seen_block: int = 0
    types_touched: set[str] = field(default_factory=set)


@dataclass
class FtelMetrics:
    """Ftel (Association Throughput) metrics.

    Measures the speed and breadth of association establishment.

    Attributes:
        ftel_rate: New associations per second (sliding window).
        total_relations: Total number of unique relations.
        graph_density: Ratio of actual edges to possible edges.
        avg_centrality: Average degree centrality across entities.
        relation_entropy: Shannon entropy of relation type distribution.
        hub_entities: Top-k entities by outgoing relation count.
    """

    ftel_rate: float = 0.0
    total_relations: int = 0
    graph_density: float = 0.0
    avg_centrality: float = 0.0
    relation_entropy: float = 0.0
    hub_entities: list[tuple[str, int]] = field(default_factory=list)
    timestamp: float = 0.0


class RelationIndex:
    """Dynamic association graph for ChainDB.

    Instead of building an "entity database", the RelationIndex builds
    a "relation index library" where:
    - Entities are lightweight index pointers
    - Associations (edges) are first-class citizens with metadata
    - Intelligence is measured by Ftel (association throughput)

    Usage:
        ri = RelationIndex()
        ri.process_block(block)  # Extract associations from a block
        metrics = ri.compute_ftel()  # Get intelligence metrics
        neighbors = ri.get_neighbors("users")  # Get related entities
    """

    def __init__(self) -> None:
        """Initialize an empty relation index."""
        # Adjacency lists: source -> [(target, Relation)]
        self._outgoing: dict[str, list[Relation]] = defaultdict(list)
        # Reverse index: target -> [source_ids]
        self._incoming: dict[str, set[str]] = defaultdict(set)
        # Entity profiles
        self._profiles: dict[str, EntityProfile] = {}
        # Time-series for Ftel computation
        self._relation_timestamps: list[float] = []
        self._ftel_window_seconds: float = 60.0  # 1-minute sliding window
        self._start_time: float = time.time()

    def process_block(self, block: Block) -> int:
        """Extract and index all associations from a block.

        This is the primary ingestion point. Called when a block is
        committed to the ledger.

        Args:
            block: The committed block to process.

        Returns:
            Number of new relations extracted.
        """
        new_relations = 0

        for tx in block.transactions:
            # 1. Transaction touches table
            table_name = tx.payload.get("table_name", "")
            if table_name:
                rel = self._add_relation(
                    source=f"tx:{tx.tx_hash[:16]}",
                    target=f"table:{table_name}",
                    rel_type=RelationType.TX_TOUCHES_TABLE,
                    block_number=block.block_number,
                    metadata={"tx_type": tx.tx_type.value, "raw_sql": tx.raw_sql[:100]},
                )
                if rel:
                    new_relations += 1

            # 2. Account submits transaction
            if tx.account:
                rel = self._add_relation(
                    source=f"account:{tx.account}",
                    target=f"tx:{tx.tx_hash[:16]}",
                    rel_type=RelationType.ACCOUNT_SUBMITS_TX,
                    block_number=block.block_number,
                    metadata={"tx_type": tx.tx_type.value},
                )
                if rel:
                    new_relations += 1

            # 3. Block contains transaction
            rel = self._add_relation(
                source=f"block:{block.block_number}",
                target=f"tx:{tx.tx_hash[:16]}",
                rel_type=RelationType.BLOCK_CONTAINS_TX,
                block_number=block.block_number,
            )
            if rel:
                new_relations += 1

            # 4. DDL: account owns table
            if tx.tx_type == TxType.TABLE_LIST_SET and table_name:
                rel = self._add_relation(
                    source=f"account:{tx.account}",
                    target=f"table:{table_name}",
                    rel_type=RelationType.ACCOUNT_OWNS_TABLE,
                    block_number=block.block_number,
                )
                if rel:
                    new_relations += 1

            # 5. Table touches block (reverse direction)
            if table_name:
                rel = self._add_relation(
                    source=f"table:{table_name}",
                    target=f"block:{block.block_number}",
                    rel_type=RelationType.TABLE_TOUCHES_BLOCK,
                    block_number=block.block_number,
                )
                if rel:
                    new_relations += 1

        if new_relations > 0:
            logger.debug(
                "RelationIndex: {} new relations from block #{}",
                new_relations,
                block.block_number,
            )

        return new_relations

    def _add_relation(
        self,
        source: str,
        target: str,
        rel_type: RelationType,
        block_number: int = 0,
        weight: float = 1.0,
        metadata: dict[str, Any] | None = None,
    ) -> Relation | None:
        """Add a relation if it doesn't already exist.

        Args:
            source: Source entity ID.
            target: Target entity ID.
            rel_type: Type of association.
            block_number: Block when established.
            weight: Association strength.
            metadata: Additional context.

        Returns:
            The new Relation, or None if it already existed.
        """
        # Dedup: check if this exact relation already exists
        for existing in self._outgoing.get(source, []):
            if existing.target == target and existing.rel_type == rel_type:
                # Update weight (reinforce)
                existing.weight += weight
                existing.block_number = max(existing.block_number, block_number)
                return None

        rel = Relation(
            source=source,
            target=target,
            rel_type=rel_type,
            weight=weight,
            block_number=block_number,
            timestamp=time.time(),
            metadata=metadata or {},
        )

        self._outgoing[source].append(rel)
        self._incoming[target].add(source)
        self._relation_timestamps.append(rel.timestamp)

        # Update source profile
        self._get_or_create_profile(source, block_number)
        self._profiles[source].relation_count += 1
        self._profiles[source].types_touched.add(target.split(":")[0])

        # Update target profile
        self._get_or_create_profile(target, block_number)
        self._profiles[target].incoming_count += 1
        self._profiles[target].types_touched.add(source.split(":")[0])

        return rel

    def _get_or_create_profile(self, entity_id: str, block_number: int) -> EntityProfile:
        """Get or create an entity profile.

        Args:
            entity_id: Entity identifier.
            block_number: Current block number.

        Returns:
            EntityProfile instance.
        """
        if entity_id not in self._profiles:
            self._profiles[entity_id] = EntityProfile(
                entity_id=entity_id,
                first_seen_block=block_number,
            )
        self._profiles[entity_id].last_seen_block = max(
            self._profiles[entity_id].last_seen_block, block_number
        )
        return self._profiles[entity_id]

    def get_neighbors(
        self,
        entity_id: str,
        rel_type: RelationType | None = None,
        direction: str = "outgoing",
    ) -> list[Relation]:
        """Get related entities for a given entity.

        Args:
            entity_id: Entity to look up.
            rel_type: Filter by relation type. If None, return all.
            direction: "outgoing" (this entity -> others) or "incoming" (others -> this).

        Returns:
            List of Relations matching the query.
        """
        if direction == "incoming":
            # Return outgoing relations from all incoming sources
            results = []
            for source_id in self._incoming.get(entity_id, set()):
                for rel in self._outgoing.get(source_id, []):
                    if rel.target == entity_id:
                        if rel_type is None or rel.rel_type == rel_type:
                            results.append(rel)
            return results

        relations = self._outgoing.get(entity_id, [])
        if rel_type is None:
            return list(relations)
        return [r for r in relations if r.rel_type == rel_type]

    def get_entity_profile(self, entity_id: str) -> EntityProfile | None:
        """Get the profile of an entity.

        Args:
            entity_id: Entity identifier.

        Returns:
            EntityProfile or None if not found.
        """
        return self._profiles.get(entity_id)

    def get_table_relationships(self, table_name: str) -> dict[str, Any]:
        """Get all relationships involving a specific table.

        This is the key API for the "relation index" paradigm:
            instead of querying table data, query table associations.

        Args:
            table_name: Name of the table.

        Returns:
            Dict with accounts, transactions, blocks, and related tables.
        """
        table_key = f"table:{table_name}"

        # Accounts that own this table
        owners = [
            r.source.replace("account:", "")
            for r in self.get_neighbors(table_key, RelationType.ACCOUNT_OWNS_TABLE, "incoming")
        ]

        # Blocks that modified this table
        blocks = [
            r.target.replace("block:", "")
            for r in self.get_neighbors(table_key, RelationType.TABLE_TOUCHES_BLOCK)
        ]

        # Transactions that touched this table
        txs = [
            {
                "tx_hash": r.source.replace("tx:", "") + "...",
                "tx_type": r.metadata.get("tx_type", "unknown"),
                "block": r.block_number,
            }
            for r in self.get_neighbors(table_key, RelationType.TX_TOUCHES_TABLE, "incoming")
        ]

        # Co-occurring tables (tables modified in the same blocks)
        related_tables: dict[str, int] = defaultdict(int)
        for block_id in blocks:
            block_key = f"block:{block_id}"
            for rel in self.get_neighbors(block_key, RelationType.BLOCK_CONTAINS_TX):
                tx_key = rel.target
                for tx_rel in self.get_neighbors(tx_key, RelationType.TX_TOUCHES_TABLE, "incoming"):
                    other_table = tx_rel.source.replace("table:", "")
                    if other_table != table_name:
                        related_tables[other_table] += 1

        return {
            "table": table_name,
            "owners": owners,
            "blocks_modified": blocks,
            "transaction_count": len(txs),
            "recent_transactions": txs[-20:],  # Last 20
            "related_tables": dict(sorted(related_tables.items(), key=lambda x: -x[1])[:10]),
        }

    def compute_ftel(self) -> FtelMetrics:
        """Compute Ftel (Association Throughput) metrics.

        Ftel measures the intelligence of the system as the speed and
        breadth of association establishment.

        Returns:
            FtelMetrics with computed values.
        """
        now = time.time()

        # 1. Ftel rate: new associations in the sliding window
        window_start = now - self._ftel_window_seconds
        recent = [t for t in self._relation_timestamps if t >= window_start]
        window_duration = max(now - self._start_time, 1.0)
        ftel_rate = len(recent) / self._ftel_window_seconds

        # 2. Total unique relations
        total_relations = sum(len(rels) for rels in self._outgoing.values())

        # 3. Graph density
        n_entities = len(self._profiles)
        if n_entities > 1:
            max_edges = n_entities * (n_entities - 1)
            density = total_relations / max_edges
        else:
            density = 0.0

        # 4. Average centrality (degree)
        if n_entities > 0:
            avg_centrality = total_relations / n_entities
        else:
            avg_centrality = 0.0

        # 5. Relation type entropy (Shannon entropy)
        type_counts: dict[str, int] = defaultdict(int)
        for rels in self._outgoing.values():
            for r in rels:
                type_counts[r.rel_type.value] += 1

        entropy = 0.0
        if total_relations > 0:
            for count in type_counts.values():
                p = count / total_relations
                if p > 0:
                    entropy -= p * (p.bit_length() if p > 0 else 0)
                    # Use natural log
                    import math
                    entropy = -sum(
                        (c / total_relations) * math.log(c / total_relations)
                        for c in type_counts.values()
                        if c > 0
                    )

        # 6. Hub entities (top 10 by outgoing relations)
        hubs = sorted(
            [(eid, p.relation_count) for eid, p in self._profiles.items()],
            key=lambda x: -x[1],
        )[:10]

        return FtelMetrics(
            ftel_rate=round(ftel_rate, 4),
            total_relations=total_relations,
            graph_density=round(density, 6),
            avg_centrality=round(avg_centrality, 2),
            relation_entropy=round(entropy, 4),
            hub_entities=hubs,
            timestamp=now,
        )

    def get_graph_data(self) -> dict[str, Any]:
        """Export the relation graph for visualization (D3.js force-directed graph).

        Returns:
            Dict with nodes and edges for graph rendering.
        """
        nodes = []
        for eid, profile in self._profiles.items():
            entity_type = eid.split(":")[0]
            nodes.append({
                "id": eid,
                "type": entity_type,
                "label": eid.split(":", 1)[1],
                "relations": profile.relation_count,
                "group": entity_type,
            })

        edges = []
        for source, rels in self._outgoing.items():
            for rel in rels:
                edges.append({
                    "source": rel.source,
                    "target": rel.target,
                    "type": rel.rel_type.value,
                    "weight": rel.weight,
                })

        return {"nodes": nodes, "edges": edges}

    def get_stats(self) -> dict[str, Any]:
        """Get index statistics.

        Returns:
            Dict with entity count, relation count, and metrics summary.
        """
        ftel = self.compute_ftel()
        return {
            "entity_count": len(self._profiles),
            "total_relations": ftel.total_relations,
            "ftel_rate": ftel.ftel_rate,
            "graph_density": ftel.graph_density,
            "avg_centrality": ftel.avg_centrality,
            "relation_entropy": ftel.relation_entropy,
            "top_hubs": ftel.hub_entities[:5],
        }

    def export_index(self) -> dict[str, Any]:
        """Export the full relation index for persistence.

        Returns:
            Serializable dict with all relations and profiles.
        """
        relations = []
        for source, rels in self._outgoing.items():
            for rel in rels:
                relations.append({
                    "source": rel.source,
                    "target": rel.target,
                    "rel_type": rel.rel_type.value,
                    "weight": rel.weight,
                    "block_number": rel.block_number,
                    "metadata": rel.metadata,
                })

        profiles = {}
        for eid, p in self._profiles.items():
            profiles[eid] = {
                "relation_count": p.relation_count,
                "incoming_count": p.incoming_count,
                "first_seen_block": p.first_seen_block,
                "last_seen_block": p.last_seen_block,
                "types_touched": list(p.types_touched),
            }

        return {
            "relations": relations,
            "profiles": profiles,
            "timestamp": time.time(),
        }
