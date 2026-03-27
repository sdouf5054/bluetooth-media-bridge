"""
tray_app.py — System tray icon and menu for Bluetooth Media Bridge.

Responsibilities:
  - Display connection state via tray icon color + tooltip
  - Provide right-click context menu with:
    - Status display
    - Engine start/stop toggle
    - Disconnect BT (active only when device connected)
    - Reconnect
    - Settings
    - Quit
  - Show Windows toast notifications on connect/disconnect
  - Manage tray icon lifecycle
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from PySide6.QtCore import QObject, Signal, Slot, QTimer
from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

if TYPE_CHECKING:
    from .config import AppConfig

logger = logging.getLogger(__name__)

# Icon resource directory
_ICON_DIR = Path(__file__).resolve().parent / "assets/round"


class TrayApp(QObject):
    """
    System tray icon with context menu.

    Signals:
      - open_settings_requested: user clicked "Settings" or double-clicked tray
      - quit_requested: user clicked "Quit"
      - reconnect_requested: user clicked "Reconnect"
      - toggle_connection_requested(bool): engine start (True) / stop (False)
      - disconnect_requested: disconnect BT device only, keep engine running
    """

    open_settings_requested = Signal()
    quit_requested = Signal()
    reconnect_requested = Signal()
    toggle_connection_requested = Signal(bool)
    connect_requested = Signal()
    disconnect_requested = Signal()

    # Map ConnectionState enum names to icon filenames and tooltips
    _STATE_ICONS = {
        "IDLE":         ("tray_idle",       "Idle — not running"),
        "INITIALIZING": ("tray_idle",       "Initializing..."),
        "READY":        ("tray_ready",      "Ready — waiting for device"),
        "CONNECTED":    ("tray_connected",  "Connected"),
        "STREAMING":    ("tray_connected",  "Streaming audio"),
    }

    def __init__(
        self,
        config: "AppConfig",
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self._config = config
        self._current_state = "IDLE"
        self._device_info = ""

        # Load icons
        self._icons: dict[str, QIcon] = {}
        self._load_icons()

        # Create tray icon
        self._tray = QSystemTrayIcon(self)
        self._tray.setIcon(self._get_icon("tray_idle"))
        self._tray.setToolTip("Bluetooth Media Bridge")
        self._tray.activated.connect(self._on_tray_activated)

        # Build context menu
        self._menu = QMenu()
        self._build_menu()
        self._tray.setContextMenu(self._menu)

    # -- public interface ----------------------------------------------------

    def show(self) -> None:
        """Show the tray icon."""
        self._tray.show()
        logger.info("Tray icon shown")

    def hide(self) -> None:
        """Hide the tray icon."""
        self._tray.hide()

    @Slot(str, str)
    def update_state(self, state_name: str, device_info: str = "") -> None:
        """
        Update tray icon and tooltip based on connection state.

        Args:
            state_name: ConnectionState enum name (e.g. "CONNECTED")
            device_info: Optional device description for tooltip
        """
        self._current_state = state_name
        self._device_info = device_info

        icon_name, base_tooltip = self._STATE_ICONS.get(
            state_name, ("tray_idle", state_name)
        )
        self._tray.setIcon(self._get_icon(icon_name))

        tooltip = f"Bluetooth Media Bridge\n{base_tooltip}"
        if device_info and state_name in ("CONNECTED", "STREAMING"):
            tooltip += f"\n{device_info}"
        self._tray.setToolTip(tooltip)

        # Update menu items
        self._refresh_menu_state()

    def notify(self, title: str, message: str, icon_type: str = "info") -> None:
        """Show a Windows toast notification via the tray icon."""
        if not self._config.get("show_notifications", True):
            return
        icon_map = {
            "info": QSystemTrayIcon.MessageIcon.Information,
            "warning": QSystemTrayIcon.MessageIcon.Warning,
            "error": QSystemTrayIcon.MessageIcon.Critical,
        }
        msg_icon = icon_map.get(icon_type, QSystemTrayIcon.MessageIcon.Information)
        self._tray.showMessage(title, message, msg_icon, 3000)

    # -- icon loading --------------------------------------------------------

    def _load_icons(self) -> None:
        """Pre-load all state icons."""
        for state_name, (icon_base, _) in self._STATE_ICONS.items():
            if icon_base not in self._icons:
                self._icons[icon_base] = self._load_icon_file(icon_base)
        self._icons["tray_error"] = self._load_icon_file("tray_error")

    def _load_icon_file(self, name: str) -> QIcon:
        """Load an icon from the assets directory."""
        for ext in (".ico", ".png"):
            path = _ICON_DIR / f"{name}{ext}"
            if path.is_file():
                return QIcon(str(path))
        logger.warning("Icon not found: %s", name)
        return QIcon()

    def _get_icon(self, name: str) -> QIcon:
        return self._icons.get(name, QIcon())

    # -- menu construction ---------------------------------------------------

    def _build_menu(self) -> None:
        """Build the right-click context menu."""
        # Status line (disabled, just for display)
        self._status_action = QAction("Idle — not running", self._menu)
        self._status_action.setEnabled(False)
        self._menu.addAction(self._status_action)

        # Device info line (hidden when not connected)
        self._device_action = QAction("", self._menu)
        self._device_action.setEnabled(False)
        self._device_action.setVisible(False)
        self._menu.addAction(self._device_action)

        self._menu.addSeparator()

        # Engine start/stop
        self._engine_action = QAction("Start Engine", self._menu)
        self._engine_action.triggered.connect(self._on_engine_toggle)
        self._menu.addAction(self._engine_action)

        # Connect Device (when engine running but not connected)
        self._connect_action = QAction("Connect Device", self._menu)
        self._connect_action.triggered.connect(self.connect_requested.emit)
        self._connect_action.setVisible(False)
        self._menu.addAction(self._connect_action)

        # Disconnect BT (only when connected)
        self._disconnect_action = QAction("Disconnect Device", self._menu)
        self._disconnect_action.triggered.connect(self.disconnect_requested.emit)
        self._disconnect_action.setVisible(False)
        self._menu.addAction(self._disconnect_action)

        # Reconnect
        self._reconnect_action = QAction("Reconnect", self._menu)
        self._reconnect_action.triggered.connect(self.reconnect_requested.emit)
        self._reconnect_action.setVisible(False)
        self._menu.addAction(self._reconnect_action)

        self._menu.addSeparator()

        # Settings
        settings_action = QAction("Settings...", self._menu)
        settings_action.triggered.connect(self.open_settings_requested.emit)
        self._menu.addAction(settings_action)

        self._menu.addSeparator()

        # Quit
        quit_action = QAction("Quit", self._menu)
        quit_action.triggered.connect(self.quit_requested.emit)
        self._menu.addAction(quit_action)

    def _refresh_menu_state(self) -> None:
        """Update menu items based on current connection state."""
        _, tooltip = self._STATE_ICONS.get(
            self._current_state, ("tray_idle", self._current_state)
        )
        self._status_action.setText(tooltip)

        is_connected = self._current_state in ("CONNECTED", "STREAMING")
        is_running = self._current_state not in ("IDLE",)
        is_ready = self._current_state == "READY"

        # Device info
        if is_connected and self._device_info:
            self._device_action.setText(self._device_info)
            self._device_action.setVisible(True)
        else:
            self._device_action.setVisible(False)

        # Engine start/stop
        self._engine_action.setText("Stop Engine" if is_running else "Start Engine")

        # Connect — visible when engine running but not connected
        self._connect_action.setVisible(is_running and not is_connected)
        self._connect_action.setEnabled(is_ready)

        # Disconnect — visible only when connected
        self._disconnect_action.setVisible(is_connected)

        # Reconnect — visible only when engine is running
        self._reconnect_action.setVisible(is_running)

    # -- event handlers ------------------------------------------------------

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        """Handle tray icon activation (click, double-click)."""
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self.open_settings_requested.emit()
        elif reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.open_settings_requested.emit()

    def _on_engine_toggle(self) -> None:
        """Handle Start/Stop Engine menu action."""
        is_running = self._current_state not in ("IDLE",)
        self.toggle_connection_requested.emit(not is_running)