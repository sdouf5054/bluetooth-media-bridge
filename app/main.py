"""
main.py — Entry point for Bluetooth Media Bridge.

Responsibilities:
  - Initialize QApplication + asyncio event loop (via qasync)
  - Create and wire: BridgeEngine, TrayApp, SettingsWindow, LogWindow
  - Bridge engine callbacks (plain Python) → Qt signals (thread-safe)
  - Handle startup options (--minimized, --startup, --ipc-only, --build-dir)
  - Graceful shutdown on quit
"""

from __future__ import annotations

import argparse
import asyncio
import ctypes
import logging
import sys
from pathlib import Path

from PySide6.QtCore import QObject, Signal, Slot, QTimer
from PySide6.QtWidgets import QApplication

# ── Ensure package is importable ───────────────────────────────────────────
if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.bridge_engine import (
    BridgeEngine,
    ConnectionState,
    MediaMetadata,
    PlaybackStatus,
)
from app.config import AppConfig, verify_startup_path
from app.log_window import LogWindow
from app.settings_window import SettingsWindow
from app.tray_app import TrayApp

logger = logging.getLogger(__name__)

# App identity for Windows SMTC / taskbar grouping
APP_USER_MODEL_ID = "BluetoothMediaBridge.App.1"


def _set_app_id() -> None:
    """Set Windows AppUserModelID so SMTC and taskbar show our name."""
    if sys.platform == "win32":
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                APP_USER_MODEL_ID
            )
        except Exception:
            pass


# ── Engine-to-Qt signal bridge ─────────────────────────────────────────────

class _EngineBridge(QObject):
    """
    Bridges BridgeEngine callbacks (called from asyncio) to Qt signals.
    """

    state_changed = Signal(str, str)     # (state_name, device_info)
    metadata_changed = Signal(str, str, str)  # (title, artist, album)
    playback_changed = Signal(str)       # status string
    cover_art_ready = Signal(str)        # file path as string
    codec_changed = Signal(str)          # codec name ("SBC", "AAC")
    stream_started = Signal()
    stream_stopped = Signal()
    log_line = Signal(str)
    process_exited = Signal(str)         # exit code as string (avoids int overflow on Windows)

    def __init__(self, engine: BridgeEngine, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._engine = engine
        self._device_info = ""

        # Register engine callbacks
        engine.on("state_changed", self._on_state)
        engine.on("metadata", self._on_metadata)
        engine.on("playback", self._on_playback)
        engine.on("cover_art", self._on_cover_art)
        engine.on("codec", self._on_codec)
        engine.on("stream_started", self._on_stream_started)
        engine.on("stream_stopped", self._on_stream_stopped)
        engine.on("log", self._on_log)
        engine.on("process_exit", self._on_exit)

    def _on_state(self, state: ConnectionState) -> None:
        name = state.name
        if state in (ConnectionState.CONNECTED, ConnectionState.STREAMING):
            addr = self._engine.state.device_addr
            self._device_info = f"Device ({addr})" if addr else ""
        elif state in (ConnectionState.IDLE, ConnectionState.READY):
            self._device_info = ""
        self.state_changed.emit(name, self._device_info)

    def _on_metadata(self, meta: MediaMetadata) -> None:
        self.metadata_changed.emit(meta.title, meta.artist, meta.album)

    def _on_playback(self, status: PlaybackStatus) -> None:
        self.playback_changed.emit(status.value)

    def _on_cover_art(self, path: Path) -> None:
        self.cover_art_ready.emit(str(path))

    def _on_codec(self, name: str) -> None:
        self.codec_changed.emit(name)

    def _on_stream_started(self) -> None:
        self.stream_started.emit()

    def _on_stream_stopped(self) -> None:
        self.stream_stopped.emit()

    def _on_log(self, line: str) -> None:
        self.log_line.emit(line)

    def _on_exit(self, code: int | None) -> None:
        self.process_exited.emit(str(code) if code is not None else "-1")


# ── Application controller ─────────────────────────────────────────────────

class Application:
    """
    Top-level application controller.
    """

    def __init__(self, args: argparse.Namespace) -> None:
        self._args = args
        self._loop: asyncio.AbstractEventLoop | None = None
        self._engine_running = False

        # Config
        self._config = AppConfig()

        # Engine
        build_dir = Path(args.build_dir) if args.build_dir else None
        self._engine = BridgeEngine(
            build_dir=build_dir,
            enable_smtc=not args.no_smtc,
            auto_reconnect=self._config.get("auto_reconnect_last_device", True),
            preferred_codec=self._config.get("preferred_codec", "both"),
        )

        # Qt components
        self._tray = TrayApp(self._config)
        self._settings = SettingsWindow(self._config)
        self._log_win = LogWindow(
            max_lines=self._config.get("log_max_lines", 5000)
        )

        # Engine → Qt bridge
        self._bridge = _EngineBridge(self._engine)

        # Wire everything
        self._connect_signals()

    def _connect_signals(self) -> None:
        """Connect all signals between components."""
        bridge = self._bridge
        tray = self._tray
        settings = self._settings
        log_win = self._log_win

        # Engine bridge → Tray
        bridge.state_changed.connect(tray.update_state)

        # Engine bridge → Settings window
        bridge.state_changed.connect(settings.update_connection_state)
        bridge.state_changed.connect(self._on_state_for_device_info)
        bridge.codec_changed.connect(self._on_codec_changed)

        # Engine bridge → Log window
        bridge.log_line.connect(log_win.append_line)
        bridge.state_changed.connect(
            lambda s, d: log_win.append_line(f"[engine] State → {s}" + (f" ({d})" if d else ""))
        )
        bridge.metadata_changed.connect(
            lambda t, a, al: log_win.append_line(f"[engine] Track: {a} — {t}" + (f" [{al}]" if al else ""))
        )
        bridge.playback_changed.connect(
            lambda s: log_win.append_line(f"[engine] Playback → {s}")
        )
        bridge.codec_changed.connect(
            lambda c: log_win.append_line(f"[engine] Codec → {c}")
        )
        bridge.process_exited.connect(
            lambda c: log_win.append_line(f"[engine] Process exited (code={c})")
        )

        # Tray → Actions
        tray.open_settings_requested.connect(settings.show_and_raise)
        tray.quit_requested.connect(self._quit)
        tray.reconnect_requested.connect(self._reconnect)
        tray.toggle_connection_requested.connect(self._toggle_connection)
        tray.connect_requested.connect(self._connect_bt)
        tray.disconnect_requested.connect(self._disconnect_bt)

        # Settings → Actions
        settings.open_log_requested.connect(log_win.show_and_raise)
        settings.connection_toggled.connect(self._toggle_connection)
        settings.connect_requested.connect(self._connect_bt)
        settings.disconnect_requested.connect(self._disconnect_bt)
        settings.codec_changed.connect(self._on_codec_preference_changed)

    # ── Lifecycle ──────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Start the application: show tray, optionally start engine."""
        self._loop = asyncio.get_event_loop()

        self._tray.show()

        start_minimized = (
            self._args.startup
            or self._args.minimized
            or self._config.get("start_minimized", False)
        )
        if not start_minimized:
            self._settings.show()

        if self._config.get("auto_connect", True):
            await self._start_engine()

    async def _start_engine(self) -> None:
        """Start or connect to bt_bridge."""
        try:
            if self._args.ipc_only:
                self._log_win.append_line("[app] Connecting IPC only...")
                await self._engine.connect_ipc_only()
            else:
                self._log_win.append_line("[app] Starting bt_bridge...")
                await self._engine.start()
            self._engine_running = True
        except FileNotFoundError as e:
            self._log_win.append_line(f"[app] ERROR: {e}")
            self._tray.update_state("IDLE")
            self._settings.update_connection_state("IDLE")
        except ConnectionError as e:
            self._log_win.append_line(f"[app] ERROR: {e}")
            self._tray.update_state("IDLE")
            self._settings.update_connection_state("IDLE")

    async def _stop_engine(self) -> None:
        """Stop engine gracefully."""
        if self._engine_running:
            self._log_win.append_line("[app] Stopping engine...")
            await self._engine.stop()
            self._engine_running = False
            self._settings.clear_device_info()
            self._settings.update_connection_state("IDLE", "")
            self._tray.update_state("IDLE", "")

    # ── Slot handlers (schedule async work from Qt signals) ─────────────────

    def _toggle_connection(self, connect: bool) -> None:
        if self._loop is None:
            return
        if connect:
            asyncio.ensure_future(self._start_engine(), loop=self._loop)
        else:
            asyncio.ensure_future(self._stop_engine(), loop=self._loop)

    def _reconnect(self) -> None:
        if self._loop is None:
            return

        async def _do_reconnect() -> None:
            self._log_win.append_line("[app] Reconnecting...")
            await self._stop_engine()
            await self._start_engine()

        asyncio.ensure_future(_do_reconnect(), loop=self._loop)

    def _disconnect_bt(self) -> None:
        if self._loop is None or not self._engine_running:
            return
        self._log_win.append_line("[app] Disconnecting BT device...")
        asyncio.ensure_future(
            self._engine.disconnect_bt(), loop=self._loop
        )

    def _connect_bt(self) -> None:
        if self._loop is None or not self._engine_running:
            return
        self._log_win.append_line("[app] Connecting to last device...")
        asyncio.ensure_future(
            self._engine.connect_bt(), loop=self._loop
        )

    def _on_state_for_device_info(self, state_name: str, _device_info: str) -> None:
        """Update settings window device info based on engine state."""
        if state_name in ("CONNECTED", "STREAMING"):
            s = self._engine.state
            self._settings.update_device_info(
                device_name=f"Device ({s.device_addr[:8]}...)" if s.device_addr else "Unknown",
                device_addr=s.device_addr or "—",
                codec=s.codec or "—",
            )
        elif state_name in ("IDLE", "READY", "INITIALIZING"):
            self._settings.clear_device_info()

    def _on_codec_changed(self, codec_name: str) -> None:
        """Update device info when codec is reported by bt_bridge."""
        if self._engine.state.connection in (ConnectionState.CONNECTED, ConnectionState.STREAMING):
            s = self._engine.state
            self._settings.update_device_info(
                device_name=f"Device ({s.device_addr[:8]}...)" if s.device_addr else "Unknown",
                device_addr=s.device_addr or "—",
                codec=codec_name or "—",
            )

    def _on_codec_preference_changed(self, codec: str) -> None:
        """Handle codec selection change from settings UI."""
        # Map UI values to bt_bridge --codec values
        codec_map = {"SBC": "SBC", "AAC": "both"}
        bt_codec = codec_map.get(codec, "both")
        self._engine.preferred_codec = bt_codec
        self._config["preferred_codec"] = bt_codec
        self._config.save()
        self._log_win.append_line(
            f"[app] Codec preference changed to {codec} (bt_bridge: --codec {bt_codec}). "
            f"Restart engine to apply."
        )

    def _quit(self) -> None:
        """Graceful shutdown."""
        if self._loop is None:
            QApplication.quit()
            return

        async def _do_quit() -> None:
            self._log_win.append_line("[app] Shutting down...")
            await self._stop_engine()
            self._config.save()
            QApplication.quit()

        asyncio.ensure_future(_do_quit(), loop=self._loop)


# ── Entry point ────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Bluetooth Media Bridge")
    parser.add_argument(
        "--ipc-only",
        action="store_true",
        help="Connect to already-running bt_bridge.exe (don't launch it)",
    )
    parser.add_argument(
        "--build-dir",
        type=str,
        default=None,
        help="Path to bt_bridge build directory",
    )
    parser.add_argument(
        "--minimized",
        action="store_true",
        help="Start minimized to system tray",
    )
    parser.add_argument(
        "--startup",
        action="store_true",
        help="Launched by Windows startup (implies --minimized)",
    )
    parser.add_argument(
        "--no-smtc",
        action="store_true",
        help="Disable SMTC integration",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable debug logging",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if args.startup:
        args.minimized = True

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    verify_startup_path()
    _set_app_id()

    qt_app = QApplication(sys.argv)
    qt_app.setApplicationName("Bluetooth Media Bridge")
    qt_app.setQuitOnLastWindowClosed(False)

    try:
        import qasync
    except ImportError:
        logger.error(
            "qasync is required: pip install qasync\n"
            "It integrates asyncio with the Qt event loop."
        )
        sys.exit(1)

    loop = qasync.QEventLoop(qt_app)
    asyncio.set_event_loop(loop)

    app_ctrl = Application(args)

    with loop:
        loop.run_until_complete(app_ctrl.start())
        loop.run_forever()


if __name__ == "__main__":
    main()