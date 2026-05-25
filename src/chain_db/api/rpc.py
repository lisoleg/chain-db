"""JSON-RPC route handler for ChainDB.

Provides a unified /rpc endpoint that accepts JSON-RPC 2.0 requests
and dispatches them to the appropriate RPCHandler methods.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from loguru import logger
from pydantic import BaseModel, Field

from chain_db.api.handlers import RPCHandler


class JSONRPCRequest(BaseModel):
    """JSON-RPC 2.0 request format."""

    jsonrpc: str = Field(default="2.0", description="JSON-RPC version")
    method: str = Field(description="Method name to invoke")
    params: dict[str, Any] = Field(default_factory=dict, description="Method parameters")
    id: int | str | None = Field(default=None, description="Request ID")


class JSONRPCResponse(BaseModel):
    """JSON-RPC 2.0 response format."""

    jsonrpc: str = "2.0"
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    id: int | str | None = None


def create_rpc_router(handler: RPCHandler) -> APIRouter:
    """Create a FastAPI router with JSON-RPC endpoint.

    Args:
        handler: RPCHandler instance for processing requests.

    Returns:
        FastAPI APIRouter with /rpc endpoint.
    """
    router = APIRouter()

    # Map method names to handler functions
    method_map = {
        "chaindb_submitSql": handler.sql_submit,
        "chaindb_submitTransaction": handler.sql_submit_batch,
        "chaindb_createTable": handler.create_table,
        "chaindb_querySql": handler.sql_query,
        "chaindb_getBlock": handler.block_query,
        "chaindb_getTransaction": handler.tx_query,
        "chaindb_syncTable": handler.sync_table,
        "chaindb_auditRecord": handler.audit_record,
        "chaindb_getStatus": handler.get_status,
        "chaindb_incrementalPlan": handler.incremental_plan,
        "chaindb_changeFeed": handler.change_feed,
    }

    @router.post("/rpc")
    async def rpc_endpoint(request: JSONRPCRequest) -> JSONRPCResponse:
        """JSON-RPC 2.0 endpoint.

        Accepts JSON-RPC requests and dispatches to the appropriate handler.
        """
        handler_fn = method_map.get(request.method)
        if handler_fn is None:
            logger.warning("Unknown RPC method: {}", request.method)
            return JSONRPCResponse(
                error={
                    "code": -32601,
                    "message": f"Method not found: {request.method}",
                },
                id=request.id,
            )

        try:
            result = handler_fn(request.params)
            if hasattr(result, "__await__"):
                result = await result

            return JSONRPCResponse(
                result=result,
                id=request.id,
            )
        except Exception as e:
            logger.error("RPC error: method={}, error={}", request.method, e)
            return JSONRPCResponse(
                error={
                    "code": -32603,
                    "message": str(e),
                },
                id=request.id,
            )

    return router
