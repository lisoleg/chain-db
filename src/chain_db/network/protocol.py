"""Communication protocol definitions for ChainDB P2P network.

Defines message types, message format, and msgpack encoding/decoding.
"""

from __future__ import annotations

import time
from enum import Enum
from typing import Any

import msgpack
from pydantic import BaseModel, Field


class MsgType(str, Enum):
    """Message type enumeration for P2P communication."""

    PROPOSAL = "PROPOSAL"
    VOTE_TXSET = "VOTE_TXSET"
    VOTE_BLOCK = "VOTE_BLOCK"
    NEW_BLOCK = "NEW_BLOCK"
    PEER_DISCOVERY = "PEER_DISCOVERY"
    SYNC_REQUEST = "SYNC_REQUEST"


class Message(BaseModel):
    """A P2P network message.

    Attributes:
        msg_type: Type of message.
        payload: Message payload dictionary.
        sender: Node ID of the sender.
        timestamp: Unix timestamp when the message was created.
    """

    msg_type: MsgType = Field(description="Message type")
    payload: dict[str, Any] = Field(default_factory=dict, description="Message payload")
    sender: str = Field(default="", description="Sender node ID")
    timestamp: int = Field(default=0, description="Unix timestamp (seconds)")

    def __init__(self, **data: Any) -> None:
        """Initialize with auto-set timestamp if not provided."""
        if "timestamp" not in data or data["timestamp"] == 0:
            data["timestamp"] = int(time.time())
        super().__init__(**data)

    def encode(self) -> bytes:
        """Encode the message to msgpack bytes.

        Returns:
            Msgpack-encoded byte string.
        """
        data = {
            "msg_type": self.msg_type.value,
            "payload": self.payload,
            "sender": self.sender,
            "timestamp": self.timestamp,
        }
        return msgpack.packb(data, use_bin_type=True)

    @classmethod
    def decode(cls, data: bytes) -> Message:
        """Decode a message from msgpack bytes.

        Args:
            data: Msgpack-encoded byte string.

        Returns:
            Decoded Message instance.
        """
        raw = msgpack.unpackb(data, raw=False)
        return cls(
            msg_type=MsgType(raw["msg_type"]),
            payload=raw["payload"],
            sender=raw["sender"],
            timestamp=raw["timestamp"],
        )
