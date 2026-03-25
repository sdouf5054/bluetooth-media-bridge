"""
ipc_client.py — Async TCP client for bt_bridge IPC protocol.

Responsibilities:
  - Connect to bt_bridge's TCP server (127.0.0.1:9876)
  - Parse newline-delimited JSON messages
  - Buffer partial reads correctly
  - Send commands as JSON
  - Auto-reconnect with exponential backoff
  - Dispatch events to registered callbacks
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

# Type alias for event callbacks: fn(event_type: str, data: dict)
EventCallback = Callable[[str, dict[str, Any]], None]


class IPCClient:
    """Async TCP client for the bt_bridge IPC protocol."""

    DEFAULT_HOST = "127.0.0.1"
    DEFAULT_PORT = 9876

    # Reconnect backoff: 0.5s, 1s, 2s, 4s, 4s, ...
    BACKOFF_BASE = 0.5
    BACKOFF_MAX = 4.0
    MAX_RECONNECT_ATTEMPTS = 0  # 0 = unlimited

    def __init__(
        self,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        auto_reconnect: bool = True,
    ) -> None:
        self._host = host
        self._port = port
        self._auto_reconnect = auto_reconnect

        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        self._read_task: Optional[asyncio.Task] = None
        self._reconnect_task: Optional[asyncio.Task] = None
        self._connected = False
        self._closing = False  # True when disconnect() was called intentionally
        self._buffer = ""

        # Callbacks: type -> list of handlers
        # Special type "*" matches all events
        self._callbacks: dict[str, list[EventCallback]] = {}

    # -- public properties ---------------------------------------------------

    @property
    def connected(self) -> bool:
        return self._connected

    # -- callback registration -----------------------------------------------

    def on(self, event_type: str, callback: EventCallback) -> None:
        """Register a callback for a specific event type, or '*' for all."""
        self._callbacks.setdefault(event_type, []).append(callback)

    def off(self, event_type: str, callback: EventCallback) -> None:
        """Unregister a callback."""
        handlers = self._callbacks.get(event_type, [])
        if callback in handlers:
            handlers.remove(callback)

    # -- connection lifecycle ------------------------------------------------

    async def connect(self, timeout: float = 5.0) -> None:
        """Connect to the IPC server. Raises on failure."""
        self._closing = False
        try:
            self._reader, self._writer = await asyncio.wait_for(
                asyncio.open_connection(self._host, self._port),
                timeout=timeout,
            )
        except (OSError, asyncio.TimeoutError) as e:
            logger.error("IPC connect failed: %s", e)
            raise ConnectionError(f"Cannot connect to {self._host}:{self._port}") from e

        self._connected = True
        self._buffer = ""
        logger.info("IPC connected to %s:%d", self._host, self._port)
        self._dispatch("_connected", {"host": self._host, "port": self._port})

        # Start reading
        self._read_task = asyncio.create_task(self._read_loop())

    async def disconnect(self) -> None:
        """Cleanly close the connection."""
        self._closing = True
        self._cancel_reconnect()
        await self._close_connection()

    async def connect_with_retry(self, max_attempts: int = 10) -> None:
        """Try connecting repeatedly until success or max attempts."""
        backoff = self.BACKOFF_BASE
        for attempt in range(1, max_attempts + 1):
            try:
                await self.connect()
                return
            except ConnectionError:
                if attempt == max_attempts:
                    raise
                logger.info(
                    "IPC connect attempt %d/%d failed, retrying in %.1fs",
                    attempt, max_attempts, backoff,
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, self.BACKOFF_MAX)

    # -- sending commands ----------------------------------------------------

    async def send_command(self, cmd: str) -> None:
        """Send a command to bt_bridge. e.g. send_command('play')."""
        if not self._connected or self._writer is None:
            logger.warning("Cannot send command '%s' — not connected", cmd)
            return
        payload = json.dumps({"cmd": cmd}) + "\n"
        try:
            self._writer.write(payload.encode("utf-8"))
            await self._writer.drain()
            logger.debug("IPC sent: %s", cmd)
        except (ConnectionError, OSError) as e:
            logger.error("IPC send failed: %s", e)
            await self._handle_disconnect()

    # -- internals: reading --------------------------------------------------

    async def _read_loop(self) -> None:
        """Continuously read from socket, parse JSON lines, dispatch events."""
        try:
            while self._connected and self._reader is not None:
                data = await self._reader.read(4096)
                if not data:
                    # Server closed connection
                    break
                self._buffer += data.decode("utf-8", errors="replace")
                self._process_buffer()
        except asyncio.CancelledError:
            return
        except (ConnectionError, OSError) as e:
            logger.error("IPC read error: %s", e)

        # If we reach here, connection was lost
        if not self._closing:
            await self._handle_disconnect()

    def _process_buffer(self) -> None:
        """Extract complete JSON lines from the buffer and dispatch them."""
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                logger.warning("IPC: invalid JSON: %s", line[:200])
                continue

            event_type = event.get("type", "unknown")
            logger.debug("IPC event: %s", event)
            self._dispatch(event_type, event)

    # -- internals: dispatch -------------------------------------------------

    def _dispatch(self, event_type: str, data: dict[str, Any]) -> None:
        """Call registered handlers for this event type and wildcard '*'."""
        for handler in self._callbacks.get(event_type, []):
            try:
                handler(event_type, data)
            except Exception:
                logger.exception("Error in IPC handler for '%s'", event_type)
        for handler in self._callbacks.get("*", []):
            try:
                handler(event_type, data)
            except Exception:
                logger.exception("Error in IPC wildcard handler")

    # -- internals: reconnect ------------------------------------------------

    async def _handle_disconnect(self) -> None:
        """Handle an unexpected disconnection."""
        was_connected = self._connected
        await self._close_connection()

        if was_connected:
            self._dispatch("_disconnected", {})

        if self._auto_reconnect and not self._closing:
            self._schedule_reconnect()

    async def _close_connection(self) -> None:
        """Close socket and cancel read task."""
        self._connected = False
        if self._read_task and not self._read_task.done():
            self._read_task.cancel()
            try:
                await self._read_task
            except asyncio.CancelledError:
                pass
        self._read_task = None

        if self._writer:
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except Exception:
                pass
        self._writer = None
        self._reader = None
        self._buffer = ""

    def _schedule_reconnect(self) -> None:
        """Schedule an automatic reconnect attempt."""
        if self._reconnect_task and not self._reconnect_task.done():
            return
        self._reconnect_task = asyncio.create_task(self._reconnect_loop())

    def _cancel_reconnect(self) -> None:
        if self._reconnect_task and not self._reconnect_task.done():
            self._reconnect_task.cancel()
        self._reconnect_task = None

    async def _reconnect_loop(self) -> None:
        """Repeatedly attempt to reconnect with exponential backoff."""
        backoff = self.BACKOFF_BASE
        attempt = 0
        while not self._closing:
            attempt += 1
            logger.info("IPC reconnect attempt %d (in %.1fs)", attempt, backoff)
            await asyncio.sleep(backoff)
            try:
                await self.connect()
                logger.info("IPC reconnected after %d attempt(s)", attempt)
                return
            except ConnectionError:
                backoff = min(backoff * 2, self.BACKOFF_MAX)
                if 0 < self.MAX_RECONNECT_ATTEMPTS <= attempt:
                    logger.error("IPC reconnect gave up after %d attempts", attempt)
                    self._dispatch("_reconnect_failed", {"attempts": attempt})
                    return
