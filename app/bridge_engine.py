"""
bridge_engine.py — Central orchestrator for Bluetooth Media Bridge.

Responsibilities:
  - Coordinate ProcessManager (exe lifecycle) and IPCClient (comms)
  - Maintain authoritative state (connection, metadata, playback, codec)
  - Resolve cover art file paths (build_dir relative → absolute)
  - Expose a clean async API for upper layers (SMTC, GUI)
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import Any, Callable, Optional

from .ipc_client import IPCClient
from .process_manager import ProcessManager, ProcessState
from .smtc_manager import SMTCManager, MediaAction

logger = logging.getLogger(__name__)

# Callback type for engine events
EngineCallback = Callable[..., None]


class ConnectionState(Enum):
    IDLE = auto()          # exe not running
    INITIALIZING = auto()  # exe running, waiting for 'ready'
    READY = auto()         # btstack initialized, discoverable
    CONNECTED = auto()     # iPhone connected (A2DP established, audio ready)
    STREAMING = auto()     # A2DP audio streaming


class PlaybackStatus(Enum):
    STOPPED = "stopped"
    PLAYING = "playing"
    PAUSED = "paused"
    SEEKING = "seeking"
    UNKNOWN = "unknown"


@dataclass
class MediaMetadata:
    title: str = ""
    artist: str = ""
    album: str = ""
    genre: str = ""
    cover_art_handle: str = ""
    track_id: int = 0

    def is_empty(self) -> bool:
        return not self.title and not self.artist

    def summary(self) -> str:
        parts = []
        if self.artist:
            parts.append(self.artist)
        if self.title:
            parts.append(self.title)
        return " — ".join(parts) if parts else "(no track info)"


@dataclass
class BridgeState:
    """Snapshot of the entire bridge state."""
    connection: ConnectionState = ConnectionState.IDLE
    playback: PlaybackStatus = PlaybackStatus.UNKNOWN
    metadata: MediaMetadata = field(default_factory=MediaMetadata)
    device_addr: str = ""        # connected iPhone address
    local_addr: str = ""         # dongle BT address
    cover_art_path: Optional[Path] = None
    codec: str = ""              # active codec: "SBC", "AAC", or ""
    avrcp_ready: bool = False    # True once AVRCP control channel is up


class BridgeEngine:
    """
    High-level async controller.

    Usage:
        engine = BridgeEngine(build_dir=Path("..."))
        engine.on("metadata", my_handler)
        await engine.start()
        ...
        await engine.stop()
    """

    # Delay between exe start and first IPC connect attempt
    IPC_CONNECT_DELAY = 1.0
    IPC_CONNECT_MAX_ATTEMPTS = 15

    def __init__(
        self,
        build_dir: Optional[Path] = None,
        on_log: Optional[Callable[[str], None]] = None,
        enable_smtc: bool = True,
        auto_reconnect: bool = True,
        preferred_codec: str = "both",
    ) -> None:
        self._build_dir = Path(build_dir) if build_dir else None
        self._on_log = on_log
        self._enable_smtc = enable_smtc
        self._auto_reconnect = auto_reconnect
        self._preferred_codec = preferred_codec

        self._process = ProcessManager(
            build_dir=self._build_dir,
            on_log=self._handle_process_log,
            on_exit=self._handle_process_exit,
            preferred_codec=self._preferred_codec,
        )
        self._ipc = IPCClient(auto_reconnect=True)
        self._state = BridgeState()
        self._smtc: Optional[SMTCManager] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None

        # Engine-level event callbacks: event_name -> [handlers]
        self._callbacks: dict[str, list[EngineCallback]] = {}

        # Register IPC event handlers
        self._ipc.on("ready", self._on_ready)
        self._ipc.on("a2dp_connected", self._on_a2dp_connected)
        self._ipc.on("connected", self._on_bt_connected)
        self._ipc.on("disconnected", self._on_bt_disconnected)
        self._ipc.on("metadata", self._on_metadata)
        self._ipc.on("playback", self._on_playback)
        self._ipc.on("cover_art", self._on_cover_art)
        self._ipc.on("stream_started", self._on_stream_started)
        self._ipc.on("stream_stopped", self._on_stream_stopped)
        self._ipc.on("codec", self._on_codec)
        self._ipc.on("_disconnected", self._on_ipc_disconnected)
        self._ipc.on("_connected", self._on_ipc_connected)

    # -- public properties ---------------------------------------------------

    @property
    def state(self) -> BridgeState:
        return self._state

    @property
    def build_dir(self) -> Path:
        return self._process.build_dir

    @property
    def preferred_codec(self) -> str:
        return self._preferred_codec

    @preferred_codec.setter
    def preferred_codec(self, value: str) -> None:
        """Set preferred codec. Requires restart to take effect."""
        self._preferred_codec = value
        self._process.preferred_codec = value

    # -- event registration --------------------------------------------------

    def on(self, event_name: str, callback: EngineCallback) -> None:
        """
        Register for engine events.

        Events emitted:
          state_changed(connection: ConnectionState)
          metadata(meta: MediaMetadata)
          playback(status: PlaybackStatus)
          cover_art(path: Path)
          codec(name: str)
          stream_started()
          stream_stopped()
          log(line: str)
          process_exit(code: int | None)
        """
        self._callbacks.setdefault(event_name, []).append(callback)

    def off(self, event_name: str, callback: EngineCallback) -> None:
        handlers = self._callbacks.get(event_name, [])
        if callback in handlers:
            handlers.remove(callback)

    # -- lifecycle -----------------------------------------------------------

    async def start(self) -> None:
        """Start bt_bridge.exe and connect IPC."""
        logger.info("Engine starting")
        self._loop = asyncio.get_event_loop()
        await self._process.start()
        self._set_connection(ConnectionState.INITIALIZING)

        # Initialize SMTC if enabled
        if self._enable_smtc:
            await self._init_smtc()

        # Give the exe a moment to start its TCP server
        await asyncio.sleep(self.IPC_CONNECT_DELAY)

        try:
            await self._ipc.connect_with_retry(
                max_attempts=self.IPC_CONNECT_MAX_ATTEMPTS
            )
        except ConnectionError:
            logger.error("Failed to connect IPC after exe start")
            await self._process.stop()
            self._set_connection(ConnectionState.IDLE)
            raise

    # Grace period for bt_bridge to complete HCI shutdown
    SHUTDOWN_GRACE_PERIOD = 1.5

    async def stop(self) -> None:
        """Shut down everything cleanly."""
        logger.info("Engine stopping")

        # Tell bt_bridge to power off HCI (clean BT disconnect)
        if self._ipc.connected:
            try:
                await self._ipc.send_command("shutdown")
                logger.info("Shutdown command sent, waiting for HCI power off")
                await asyncio.sleep(self.SHUTDOWN_GRACE_PERIOD)
            except Exception:
                logger.warning("Failed to send shutdown command")

        await self._ipc.disconnect()
        await self._process.stop()
        if self._smtc:
            self._smtc.shutdown()
            self._smtc = None
        self._state = BridgeState()
        self._set_connection(ConnectionState.IDLE)

    async def restart(self) -> None:
        """Full restart cycle."""
        await self.stop()
        await self.start()

    # -- media controls (forwarded to bt_bridge via IPC) ---------------------

    async def play(self) -> None:
        await self._ipc.send_command("play")

    async def pause(self) -> None:
        await self._ipc.send_command("pause")

    async def stop_playback(self) -> None:
        await self._ipc.send_command("stop")

    async def next_track(self) -> None:
        await self._ipc.send_command("next")

    async def prev_track(self) -> None:
        await self._ipc.send_command("prev")

    async def disconnect_bt(self) -> None:
        """Disconnect the BT device but keep the engine running (discoverable)."""
        await self._ipc.send_command("disconnect")

    async def connect_bt(self) -> None:
        """Manually trigger connection to the last known device."""
        await self._ipc.send_command("connect")

    async def request_metadata(self) -> None:
        await self._ipc.send_command("get_metadata")

    # -- IPC-only mode (connect to already-running bt_bridge) ----------------

    async def connect_ipc_only(self) -> None:
        """Connect IPC without managing the process (for testing)."""
        logger.info("Engine connecting IPC only (no process management)")
        self._loop = asyncio.get_event_loop()
        self._set_connection(ConnectionState.INITIALIZING)
        if self._enable_smtc:
            await self._init_smtc()
        await self._ipc.connect_with_retry(max_attempts=self.IPC_CONNECT_MAX_ATTEMPTS)

    # -- internal: IPC event handlers ----------------------------------------

    def _on_ready(self, _type: str, data: dict[str, Any]) -> None:
        self._state.local_addr = data.get("addr", "")
        self._set_connection(ConnectionState.READY)
        logger.info("btstack ready, local addr: %s", self._state.local_addr)

    def _on_a2dp_connected(self, _type: str, data: dict[str, Any]) -> None:
        """Handle A2DP-level connection (fires before AVRCP).

        This is the earliest point where audio can flow. Transition to
        CONNECTED immediately so the GUI reflects the phone's state without
        waiting for the slower AVRCP handshake.
        """
        self._state.device_addr = data.get("addr", "")
        self._state.avrcp_ready = False
        self._set_connection(ConnectionState.CONNECTED)
        logger.info("A2DP connected: %s (AVRCP pending)", self._state.device_addr)

    def _on_bt_connected(self, _type: str, data: dict[str, Any]) -> None:
        """Handle AVRCP-level connection (fires after A2DP).

        At this point media controls (play/pause/next/prev) become available.
        If we're already CONNECTED or STREAMING from the A2DP event, just
        mark AVRCP as ready — no state transition needed.
        """
        addr = data.get("addr", "")
        self._state.avrcp_ready = True

        if self._state.connection in (ConnectionState.CONNECTED, ConnectionState.STREAMING):
            # Already connected via a2dp_connected — just log AVRCP arrival
            logger.info("AVRCP connected: %s (media controls ready)", addr)
        else:
            # Fallback: if a2dp_connected was somehow missed, connect now
            self._state.device_addr = addr
            self._set_connection(ConnectionState.CONNECTED)
            logger.info("AVRCP connected (fallback): %s", addr)

    def _on_bt_disconnected(self, _type: str, _data: dict[str, Any]) -> None:
        self._state.device_addr = ""
        self._state.metadata = MediaMetadata()
        self._state.playback = PlaybackStatus.UNKNOWN
        self._state.cover_art_path = None
        self._state.codec = ""
        self._state.avrcp_ready = False
        self._set_connection(ConnectionState.READY)
        if self._smtc:
            self._smtc.clear_display()
            self._smtc.update_playback_status("stopped")
        logger.info("iPhone disconnected")

    def _on_metadata(self, _type: str, data: dict[str, Any]) -> None:
        self._state.metadata = MediaMetadata(
            title=data.get("title", ""),
            artist=data.get("artist", ""),
            album=data.get("album", ""),
            genre=data.get("genre", ""),
            cover_art_handle=data.get("cover_art_handle", ""),
            track_id=data.get("track_id", 0),
        )
        logger.info("Metadata: %s", self._state.metadata.summary())
        if self._smtc:
            self._smtc.update_metadata(
                title=self._state.metadata.title,
                artist=self._state.metadata.artist,
                album=self._state.metadata.album,
            )
        self._emit("metadata", self._state.metadata)

    def _on_playback(self, _type: str, data: dict[str, Any]) -> None:
        status_str = data.get("status", "unknown")
        try:
            self._state.playback = PlaybackStatus(status_str)
        except ValueError:
            self._state.playback = PlaybackStatus.UNKNOWN
        logger.info("Playback: %s", self._state.playback.value)
        if self._smtc:
            self._smtc.update_playback_status(self._state.playback.value)
        self._emit("playback", self._state.playback)

    def _on_cover_art(self, _type: str, data: dict[str, Any]) -> None:
        rel_path = data.get("path", "")
        if not rel_path:
            return
        abs_path = self._process.build_dir / rel_path
        if abs_path.is_file():
            self._state.cover_art_path = abs_path
            logger.info(
                "Cover art ready: %s (%d bytes)",
                abs_path.name, data.get("size", 0),
            )
            if self._smtc and self._loop:
                self._loop.create_task(self._smtc.update_thumbnail(abs_path))
            self._emit("cover_art", abs_path)
        else:
            logger.warning("Cover art file not found: %s", abs_path)

    def _on_codec(self, _type: str, data: dict[str, Any]) -> None:
        """Handle codec event from bt_bridge: {"type":"codec","name":"AAC"}"""
        codec_name = data.get("name", "")
        self._state.codec = codec_name
        logger.info("Codec: %s", codec_name)
        self._emit("codec", codec_name)

    def _on_stream_started(self, _type: str, _data: dict[str, Any]) -> None:
        if self._state.connection == ConnectionState.CONNECTED:
            self._set_connection(ConnectionState.STREAMING)
        self._emit("stream_started")

    def _on_stream_stopped(self, _type: str, _data: dict[str, Any]) -> None:
        if self._state.connection == ConnectionState.STREAMING:
            self._set_connection(ConnectionState.CONNECTED)
        self._emit("stream_stopped")

    def _on_ipc_connected(self, _type: str, _data: dict[str, Any]) -> None:
        logger.info("IPC connection established")
        if self._loop:
            self._loop.create_task(
                self._ipc.send_command(
                    "set_auto_reconnect",
                    enabled=self._auto_reconnect,
                )
            )

    def _on_ipc_disconnected(self, _type: str, _data: dict[str, Any]) -> None:
        logger.warning("IPC connection lost")

    # -- internal: process event handlers ------------------------------------

    def _handle_process_log(self, line: str) -> None:
        self._emit("log", line)
        if self._on_log:
            self._on_log(line)

    def _handle_process_exit(self, code: int | None) -> None:
        logger.warning("bt_bridge.exe exited (code=%s)", code)
        self._state = BridgeState()
        self._set_connection(ConnectionState.IDLE)
        self._emit("process_exit", code)

    # -- internal: SMTC integration -------------------------------------------

    async def _init_smtc(self) -> None:
        """Initialize SMTC manager."""
        self._smtc = SMTCManager(on_media_key=self._handle_media_key)
        await self._smtc.initialize()
        if self._smtc.initialized:
            logger.info("SMTC integration active")
        else:
            logger.warning("SMTC integration unavailable")
            self._smtc = None

    def _handle_media_key(self, action: MediaAction) -> None:
        """Handle media key presses from SMTC."""
        if not self._loop:
            return

        cmd_map = {
            MediaAction.PLAY: "play",
            MediaAction.PAUSE: "pause",
            MediaAction.STOP: "stop",
            MediaAction.NEXT: "next",
            MediaAction.PREVIOUS: "prev",
        }
        cmd = cmd_map.get(action)
        if cmd:
            logger.info("Media key → %s", cmd)
            asyncio.run_coroutine_threadsafe(
                self._ipc.send_command(cmd), self._loop
            )

    # -- internal: state & events --------------------------------------------

    def _set_connection(self, new_state: ConnectionState) -> None:
        if self._state.connection != new_state:
            old = self._state.connection
            self._state.connection = new_state
            logger.debug("Connection: %s → %s", old.name, new_state.name)
            self._emit("state_changed", new_state)

    def _emit(self, event_name: str, *args: Any) -> None:
        for handler in self._callbacks.get(event_name, []):
            try:
                handler(*args)
            except Exception:
                logger.exception("Error in engine handler for '%s'", event_name)