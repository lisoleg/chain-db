"""P2P Network layer for ChainDB.

Implements asyncio-based TCP server/client for inter-node communication.
Supports peer connections, message broadcasting, and direct messaging.
"""

from __future__ import annotations

import asyncio
from typing import Callable, Any

from loguru import logger

from chain_db.network.protocol import Message, MsgType


class PeerConnection:
    """Represents a connection to a remote peer.

    Attributes:
        node_id: Remote peer's node ID.
        reader: StreamReader for the connection.
        writer: StreamWriter for the connection.
    """

    def __init__(
        self,
        node_id: str,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Initialize a peer connection.

        Args:
            node_id: Remote peer's node ID.
            reader: Async stream reader.
            writer: Async stream writer.
        """
        self.node_id = node_id
        self.reader = reader
        self.writer = writer

    async def send(self, message: Message) -> None:
        """Send a message to this peer.

        Args:
            message: Message to send.
        """
        data = message.encode()
        length = len(data)
        header = length.to_bytes(4, byteorder="big")
        self.writer.write(header + data)
        await self.writer.drain()

    async def receive(self) -> Message | None:
        """Receive a message from this peer.

        Returns:
            Received Message, or None if the connection is closed.
        """
        try:
            header = await self.reader.readexactly(4)
            length = int.from_bytes(header, byteorder="big")
            data = await self.reader.readexactly(length)
            return Message.decode(data)
        except (asyncio.IncompleteReadError, ConnectionError):
            return None

    async def close(self) -> None:
        """Close the connection."""
        try:
            self.writer.close()
            await self.writer.wait_closed()
        except Exception:
            pass


class P2PNetwork:
    """P2P network layer using asyncio TCP.

    Manages connections to peer nodes and provides message
    broadcasting and direct messaging capabilities.

    Attributes:
        node_id: This node's identifier.
        host: Bind address for the TCP server.
        port: Bind port for the TCP server.
    """

    def __init__(self, node_id: str, host: str = "127.0.0.1", port: int = 5000) -> None:
        """Initialize the P2P network.

        Args:
            node_id: This node's unique identifier.
            host: Server bind address.
            port: Server bind port.
        """
        self.node_id = node_id
        self.host = host
        self.port = port

        self._server: asyncio.Server | None = None
        self._peers: dict[str, PeerConnection] = {}
        self._peer_addresses: dict[str, str] = {}  # node_id -> host:port
        self._on_message: Callable[[Message], Any] | None = None
        self._running = False

    def on_message(self, callback: Callable[[Message], Any]) -> None:
        """Register a callback for received messages.

        Args:
            callback: Callable that receives a Message.
        """
        self._on_message = callback

    async def start(self) -> None:
        """Start the TCP server and begin accepting connections."""
        self._running = True
        self._server = await asyncio.start_server(
            self._handle_connection,
            self.host,
            self.port,
        )
        logger.info("P2P server started on {}:{}", self.host, self.port)

    async def stop(self) -> None:
        """Stop the TCP server and close all peer connections."""
        self._running = False

        # Close all peer connections
        for peer in list(self._peers.values()):
            await peer.close()
        self._peers.clear()

        # Close server
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

        logger.info("P2P server stopped")

    async def connect_to_peer(self, address: str) -> bool:
        """Connect to a remote peer.

        Args:
            address: Peer address in host:port format.

        Returns:
            True if connection was successful.
        """
        try:
            parts = address.split(":")
            host = parts[0]
            port = int(parts[1])

            reader, writer = await asyncio.open_connection(host, port)

            # Send our node ID for identification
            hello = Message(
                msg_type=MsgType.PEER_DISCOVERY,
                payload={"node_id": self.node_id},
                sender=self.node_id,
            )
            header = len(hello.encode()).to_bytes(4, byteorder="big")
            writer.write(header + hello.encode())
            await writer.drain()

            # Wait for peer's identification
            resp_header = await reader.readexactly(4)
            length = int.from_bytes(resp_header, byteorder="big")
            data = await reader.readexactly(length)
            peer_msg = Message.decode(data)

            peer_id = peer_msg.payload.get("node_id", address)
            peer_conn = PeerConnection(peer_id, reader, writer)
            self._peers[peer_id] = peer_conn
            self._peer_addresses[peer_id] = address

            # Start listening for messages from this peer
            asyncio.create_task(self._listen_peer(peer_conn))

            logger.info("Connected to peer: {} ({})", peer_id, address)
            return True

        except Exception as e:
            logger.warning("Failed to connect to peer {}: {}", address, e)
            return False

    async def connect_to_peers(self, addresses: list[str]) -> None:
        """Connect to multiple peers.

        Args:
            addresses: List of peer addresses (host:port).
        """
        for addr in addresses:
            await self.connect_to_peer(addr)

    async def broadcast(self, message: Message) -> None:
        """Broadcast a message to all connected peers.

        Args:
            message: Message to broadcast.
        """
        message.sender = self.node_id
        disconnected: list[str] = []

        for peer_id, peer in self._peers.items():
            try:
                await peer.send(message)
            except Exception as e:
                logger.warning("Failed to send to peer {}: {}", peer_id, e)
                disconnected.append(peer_id)

        # Clean up disconnected peers
        for peer_id in disconnected:
            peer = self._peers.pop(peer_id, None)
            if peer:
                await peer.close()

    async def send_to(self, node_id: str, message: Message) -> bool:
        """Send a message to a specific peer.

        Args:
            node_id: Target peer's node ID.
            message: Message to send.

        Returns:
            True if the message was sent successfully.
        """
        message.sender = self.node_id
        peer = self._peers.get(node_id)
        if peer is None:
            logger.warning("Peer not found: {}", node_id)
            return False

        try:
            await peer.send(message)
            return True
        except Exception as e:
            logger.warning("Failed to send to peer {}: {}", node_id, e)
            return False

    @property
    def connected_peers(self) -> list[str]:
        """List of connected peer node IDs."""
        return list(self._peers.keys())

    @property
    def peer_count(self) -> int:
        """Number of connected peers."""
        return len(self._peers)

    async def _handle_connection(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Handle an incoming peer connection.

        Args:
            reader: Stream reader for the connection.
            writer: Stream writer for the connection.
        """
        try:
            # Read the peer's identification message
            header = await reader.readexactly(4)
            length = int.from_bytes(header, byteorder="big")
            data = await reader.readexactly(length)
            hello = Message.decode(data)

            peer_id = hello.payload.get("node_id", "unknown")

            # Respond with our identification
            response = Message(
                msg_type=MsgType.PEER_DISCOVERY,
                payload={"node_id": self.node_id},
                sender=self.node_id,
            )
            resp_data = response.encode()
            resp_header = len(resp_data).to_bytes(4, byteorder="big")
            writer.write(resp_header + resp_data)
            await writer.drain()

            peer_conn = PeerConnection(peer_id, reader, writer)
            self._peers[peer_id] = peer_conn

            logger.info("Peer connected: {}", peer_id)

            # Listen for messages from this peer
            await self._listen_peer_messages(peer_conn)

        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        except Exception as e:
            logger.error("Error handling connection: {}", e)

    async def _listen_peer(self, peer: PeerConnection) -> None:
        """Listen for messages from a connected peer (outbound connections).

        Args:
            peer: Peer connection to listen on.
        """
        await self._listen_peer_messages(peer)

    async def _listen_peer_messages(self, peer: PeerConnection) -> None:
        """Listen for and process messages from a peer.

        Args:
            peer: Peer connection to listen on.
        """
        try:
            while self._running:
                message = await peer.receive()
                if message is None:
                    break

                if self._on_message:
                    try:
                        result = self._on_message(message)
                        if asyncio.iscoroutine(result):
                            await result
                    except Exception as e:
                        logger.error("Message handler error: {}", e)
        except Exception as e:
            logger.debug("Peer {} disconnected: {}", peer.node_id, e)
        finally:
            self._peers.pop(peer.node_id, None)
            logger.info("Peer disconnected: {}", peer.node_id)
