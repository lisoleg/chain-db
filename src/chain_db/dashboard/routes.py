"""Dashboard API routes: REST endpoints for the web dashboard.

Provides JSON APIs consumed by the static frontend for:
- Chain/block overview
- Sync progress tracking
- Audit trail queries
- Table relationship graph
- Data lineage tracking
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from loguru import logger


def create_dashboard_router() -> APIRouter:
    """Create the dashboard API router.

    Returns:
        FastAPI APIRouter with dashboard endpoints.
    """
    router = APIRouter(prefix="/dashboard", tags=["dashboard"])

    @router.get("/overview")
    async def overview(request: Request) -> dict[str, Any]:
        """Get chain overview: height, block count, tx count, tables."""
        ledger = request.app.state.ledger
        registry = request.app.state.registry
        mempool = request.app.state.mempool

        total_tx = 0
        for i in range(ledger.height):
            block = ledger.get_block(i)
            if block:
                total_tx += len(block.transactions)

        return {
            "chain_height": ledger.height,
            "total_transactions": total_tx,
            "mempool_size": mempool.size,
            "tables": registry.list_tables(),
            "last_block_hash": ledger.last_block.block_hash[:16] if ledger.last_block else None,
        }

    @router.get("/blocks")
    async def list_blocks(request: Request, limit: int = 20, offset: int = 0) -> dict[str, Any]:
        """List recent blocks with summary info."""
        ledger = request.app.state.ledger

        blocks = []
        start = max(0, ledger.height - offset - limit)
        end = ledger.height - offset

        for i in range(end - 1, start - 1, -1):
            block = ledger.get_block(i)
            if block:
                blocks.append({
                    "block_number": block.block_number,
                    "block_hash": block.block_hash[:16],
                    "tx_count": len(block.transactions),
                    "proposer": block.proposer,
                    "timestamp": block.timestamp,
                    "state_root": block.state_root[:16] if block.state_root else None,
                })

        return {"blocks": blocks, "total": ledger.height}

    @router.get("/blocks/{block_number}")
    async def get_block_detail(request: Request, block_number: int) -> dict[str, Any]:
        """Get detailed block info with transaction list."""
        ledger = request.app.state.ledger
        block = ledger.get_block(block_number)

        if not block:
            return {"error": "Block not found", "block_number": block_number}

        txs = []
        for tx in block.transactions:
            txs.append({
                "tx_hash": tx.tx_hash,
                "tx_type": tx.tx_type.value,
                "account": tx.account,
                "raw_sql": tx.raw_sql,
                "table_name": tx.payload.get("table_name", ""),
                "sql_type": tx.payload.get("sql_type", ""),
            })

        return {
            "block_number": block.block_number,
            "block_hash": block.block_hash,
            "prev_hash": block.prev_hash,
            "tx_root": block.tx_root,
            "state_root": block.state_root,
            "timestamp": block.timestamp,
            "proposer": block.proposer,
            "transactions": txs,
        }

    @router.get("/sync-status")
    async def sync_status(request: Request) -> dict[str, Any]:
        """Get table sync progress."""
        table_sync = request.app.state.table_sync
        ledger = request.app.state.ledger

        synced_height = table_sync.state_manager.synced_height if table_sync else 0
        table_versions = table_sync.state_manager.table_versions if table_sync else {}

        return {
            "synced_height": synced_height,
            "chain_height": ledger.height,
            "progress_pct": round(synced_height / max(ledger.height, 1) * 100, 1),
            "table_versions": table_versions,
            "is_synced": synced_height >= ledger.height - 1,
        }

    @router.get("/audit/{table_name}")
    async def audit_table(request: Request, table_name: str) -> dict[str, Any]:
        """Get audit trail for a specific table."""
        ledger = request.app.state.ledger

        audit_trail = []
        for i in range(ledger.height):
            block = ledger.get_block(i)
            if not block:
                continue
            for tx in block.transactions:
                if tx.payload.get("table_name") == table_name:
                    audit_trail.append({
                        "block_number": block.block_number,
                        "tx_hash": tx.tx_hash,
                        "tx_type": tx.tx_type.value,
                        "sql_type": tx.payload.get("sql_type", ""),
                        "account": tx.account,
                        "raw_sql": tx.raw_sql,
                        "timestamp": block.timestamp,
                    })

        return {"table": table_name, "audit_trail": audit_trail, "total_changes": len(audit_trail)}

    @router.get("/graph/tables")
    async def table_graph(request: Request) -> dict[str, Any]:
        """Get table relationship graph (knowledge graph view).

        Returns nodes (tables) and edges (foreign key / reference relationships).
        Inspired by Understand Anything's knowledge graph visualization.
        """
        registry = request.app.state.registry
        ledger = request.app.state.ledger

        # Build table nodes
        nodes = []
        table_names = registry.list_tables()
        for name in table_names:
            meta = registry.get(name)
            if meta:
                # Count operations per table
                op_count = 0
                for i in range(ledger.height):
                    block = ledger.get_block(i)
                    if block:
                        for tx in block.transactions:
                            if tx.payload.get("table_name") == name:
                                op_count += 1

                nodes.append({
                    "id": name,
                    "label": name,
                    "columns": [c.name for c in meta.columns],
                    "creator": meta.creator,
                    "version": meta.version,
                    "operation_count": op_count,
                })

        # Build edges from SQL patterns (INSERT/UPDATE referencing multiple tables)
        edges = []
        for i in range(ledger.height):
            block = ledger.get_block(i)
            if not block:
                continue
            for tx in block.transactions:
                payload = tx.payload
                if tx.tx_type.value == "SQL_TRANSACTION":
                    # Batch transactions may reference multiple tables
                    statements = payload.get("statements", [])
                    referenced_tables = set()
                    for sql in statements:
                        for tbl in table_names:
                            if tbl in sql.upper():
                                referenced_tables.add(tbl)
                    # Create edges between co-referenced tables
                    ref_list = list(referenced_tables)
                    for j in range(len(ref_list)):
                        for k in range(j + 1, len(ref_list)):
                            edges.append({
                                "source": ref_list[j],
                                "target": ref_list[k],
                                "type": "co_referenced",
                                "block_number": block.block_number,
                            })

        # Deduplicate edges
        seen = set()
        unique_edges = []
        for e in edges:
            key = (e["source"], e["target"], e["type"])
            if key not in seen:
                seen.add(key)
                unique_edges.append(e)

        return {"nodes": nodes, "edges": unique_edges}

    @router.get("/graph/lineage/{table_name}")
    async def data_lineage(request: Request, table_name: str) -> dict[str, Any]:
        """Get data lineage for a specific table.

        Traces how data flows into and out of a table through
        the blockchain transaction history.
        """
        ledger = request.app.state.ledger
        registry = request.app.state.registry

        lineage_nodes = [{"id": table_name, "label": table_name, "type": "target"}]
        lineage_edges = []

        # Find all accounts that wrote to this table
        writers = set()
        for i in range(ledger.height):
            block = ledger.get_block(i)
            if not block:
                continue
            for tx in block.transactions:
                if tx.payload.get("table_name") == table_name:
                    if tx.account not in [n["id"] for n in lineage_nodes]:
                        lineage_nodes.append({
                            "id": tx.account,
                            "label": tx.account,
                            "type": "writer",
                        })
                    writers.add(tx.account)

        for writer in writers:
            lineage_edges.append({
                "source": writer,
                "target": table_name,
                "type": "writes_to",
            })

        # Find tables referenced in the same transactions (upstream sources)
        upstream_tables = set()
        for i in range(ledger.height):
            block = ledger.get_block(i)
            if not block:
                continue
            for tx in block.transactions:
                if tx.tx_type.value == "SQL_TRANSACTION":
                    stmts = tx.payload.get("statements", [])
                    touches_target = any(table_name in s for s in stmts)
                    if touches_target:
                        for other_tbl in registry.list_tables():
                            if other_tbl != table_name and any(other_tbl in s for s in stmts):
                                upstream_tables.add(other_tbl)

        for tbl in upstream_tables:
            lineage_nodes.append({"id": tbl, "label": tbl, "type": "upstream"})
            lineage_edges.append({"source": tbl, "target": table_name, "type": "feeds_into"})

        return {"nodes": lineage_nodes, "edges": lineage_edges}

    @router.get("/changes")
    async def recent_changes(request: Request, limit: int = 50) -> dict[str, Any]:
        """Get recent changes across all tables (incremental change feed).

        Inspired by Understand Anything's incremental update model:
        instead of full rescan, only report changes since the last query.
        """
        ledger = request.app.state.ledger

        changes = []
        start = max(0, ledger.height - limit)

        for i in range(ledger.height - 1, start - 1, -1):
            block = ledger.get_block(i)
            if not block:
                continue
            for tx in block.transactions:
                changes.append({
                    "block_number": block.block_number,
                    "tx_hash": tx.tx_hash,
                    "tx_type": tx.tx_type.value,
                    "table_name": tx.payload.get("table_name", ""),
                    "sql_type": tx.payload.get("sql_type", ""),
                    "account": tx.account,
                    "timestamp": block.timestamp,
                })

        return {"changes": changes, "total": len(changes)}

    return router
