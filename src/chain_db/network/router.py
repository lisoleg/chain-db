"""Message Router: dispatches incoming P2P messages to the appropriate handler.

Routes messages based on their MsgType to either the consensus module
or the sync module.
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable

from loguru import logger

from chain_db.network.protocol import Message, MsgType


class Router:
    """Routes incoming P2P messages to the appropriate handler.

    Messages are dispatched based on their msg_type:
    - PROPOSAL / VOTE_TXSET / VOTE_BLOCK → consensus handler
    - NEW_BLOCK / SYNC_REQUEST → sync handler
    - PEER_DISCOVERY → connection handler

    Attributes:
        handlers: Mapping of MsgType to handler functions.
    """

    def __init__(self) -> None:
        """Initialize the router with empty handlers."""
        self._handlers: dict[MsgType, Callable[[Message], Any]] = {}

    def register(self, msg_type: MsgType, handler: Callable[[Message], Any]) -> None:
        """Register a handler for a specific message type.

        Args:
            msg_type: Message type to handle.
            handler: Callable that receives the Message.
        """
        self._handlers[msg_type] = handler
        logger.debug("Registered handler for {}", msg_type.value)

    def unregister(self, msg_type: MsgType) -> None:
        """Unregister a handler for a message type.

        Args:
            msg_type: Message type to unregister.
        """
        self._handlers.pop(msg_type, None)

    async def route(self, message: Message) -> None:
        """Route a message to its registered handler.

        Args:
            message: The Message to route.
        """
        handler = self._handlers.get(message.msg_type)
        if handler is None:
            logger.warning("No handler for message type: {}", message.msg_type.value)
            return

        try:
            result = handler(message)
            if asyncio.iscoroutine(result):
                await result
        except Exception as e:
            logger.error("Error handling message {}: {}", message.msg_type.value, e)

    def get_registered_types(self) -> list[MsgType]:
        """Get list of registered message types.

        Returns:
            List of MsgType values with registered handlers.
        """
        return list(self._handlers.keys())
