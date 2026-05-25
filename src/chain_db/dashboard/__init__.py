"""ChainDB Dashboard: interactive visualization and monitoring.

Provides a web-based dashboard for:
- Block explorer (chain visualization)
- Sync status monitoring
- Audit trail browsing
- Table relationship graph (knowledge graph view)
- Data lineage tracking
"""

from chain_db.dashboard.routes import create_dashboard_router

__all__ = ["create_dashboard_router"]
