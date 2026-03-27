"""
settings_window.py — Compact settings window for Bluetooth Media Bridge.

Responsibilities:
  - Show connection status and device info (engine start/stop + connect/disconnect)
  - Codec selection (SBC/AAC), startup, minimize, auto-reconnect options
  - "Debug log" button opens LogWindow
  - Close button → hide to tray (not quit)
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional
import sys
from pathlib import Path

from PySide6.QtCore import Qt, Signal, Slot, QSize
from PySide6.QtGui import QCloseEvent, QIcon, QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

if TYPE_CHECKING:
    from .config import AppConfig

logger = logging.getLogger(__name__)


class _StatusDot(QWidget):
    """Tiny colored circle used as a connection status indicator."""

    _COLORS = {
        "IDLE":         "#888780",
        "INITIALIZING": "#BA7517",
        "READY":        "#5F5E5A",
        "CONNECTED":    "#378ADD",
        "STREAMING":    "#1D9E75",
        "ERROR":        "#E24B4A",
    }

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFixedSize(10, 10)
        self._state = "IDLE"
        self._update_style()

    def set_state(self, state: str) -> None:
        self._state = state
        self._update_style()

    def _update_style(self) -> None:
        color = self._COLORS.get(self._state, self._COLORS["IDLE"])
        self.setStyleSheet(
            f"background-color: {color}; border-radius: 5px; border: none;"
        )


class SettingsWindow(QWidget):
    """
    Compact settings window.

    Signals:
      - codec_changed(str): user selected a different codec ("SBC" or "AAC")
      - connection_toggled(bool): engine start (True) / stop (False)
      - connect_requested: manually trigger BT connection
      - disconnect_requested: disconnect BT only, keep engine
      - open_log_requested: user clicked "Debug log"
      - closed: window was closed (hidden to tray)
    """

    codec_changed = Signal(str)
    connection_toggled = Signal(bool)       # engine start (True) / stop (False)
    connect_requested = Signal()            # manually trigger BT connection
    disconnect_requested = Signal()         # disconnect BT only, keep engine
    open_log_requested = Signal()
    closed = Signal()

    VERSION = "0.2.0"

    def __init__(
        self,
        config: "AppConfig",
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._config = config
        self._is_connected = False
        self._current_state = "IDLE"

        self.setWindowTitle("Bluetooth Media Bridge")
        # 윈도우(작업표시줄) 아이콘
        # _app_dir = Path(__file__).resolve().parent
        icon_path = Path(__file__).resolve().parent / "assets" / "simple" / "ico.ico"
        if icon_path.is_file():
            self.setWindowIcon(QIcon(str(icon_path)))
        self.setFixedWidth(380)
        self.setMinimumHeight(400)
        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.WindowCloseButtonHint
            | Qt.WindowType.WindowMinimizeButtonHint
        )

        self._build_ui()
        self._apply_stylesheet()
        self._load_config()

        # Set initial button state
        self.update_connection_state("IDLE")

        # Restore window position
        x, y = config.get("window_x", -1), config.get("window_y", -1)
        if x >= 0 and y >= 0:
            self.move(x, y)

    # ── UI construction ────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 12)
        root.setSpacing(12)

        # -- Header ----------------------------------------------------------
        header = QHBoxLayout()
        header.setSpacing(8)

        self._status_dot = _StatusDot()
        header.addWidget(self._status_dot)

        title = QLabel("Bluetooth Media Bridge")
        title_font = QFont()
        title_font.setPointSize(11)
        title_font.setWeight(QFont.Weight.DemiBold)
        title.setFont(title_font)
        header.addWidget(title)

        header.addStretch()
        root.addLayout(header)

        # -- Connection section ----------------------------------------------
        conn_group = QGroupBox("Connection")
        conn_layout = QVBoxLayout(conn_group)
        conn_layout.setContentsMargins(12, 12, 12, 12)
        conn_layout.setSpacing(8)

        # Button row: Engine toggle + Connect/Disconnect
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        self._engine_btn = QPushButton("▶  Start")
        self._engine_btn.setObjectName("engineBtn")
        self._engine_btn.setFixedHeight(32)
        self._engine_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._engine_btn.clicked.connect(self._on_engine_toggle)
        btn_row.addWidget(self._engine_btn, 1)

        self._conn_action_btn = QPushButton("Connect")
        self._conn_action_btn.setObjectName("connActionBtn")
        self._conn_action_btn.setFixedHeight(32)
        self._conn_action_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._conn_action_btn.setVisible(False)
        self._conn_action_btn.clicked.connect(self._on_conn_action)
        btn_row.addWidget(self._conn_action_btn, 1)

        conn_layout.addLayout(btn_row)

        # Status label
        self._status_label = QLabel("Idle")
        self._status_label.setObjectName("statusLabel")
        self._status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        conn_layout.addWidget(self._status_label)

        # Info grid
        self._info_frame = QFrame()
        self._info_frame.setObjectName("infoFrame")
        info_layout = QVBoxLayout(self._info_frame)
        info_layout.setContentsMargins(10, 8, 10, 8)
        info_layout.setSpacing(4)

        self._device_row = self._make_info_row("Device", "—")
        self._addr_row = self._make_info_row("Address", "—")
        self._codec_row = self._make_info_row("Codec", "—")

        for label, value in [self._device_row, self._addr_row, self._codec_row]:
            row = QHBoxLayout()
            row.addWidget(label)
            row.addStretch()
            row.addWidget(value)
            info_layout.addLayout(row)

        conn_layout.addWidget(self._info_frame)
        root.addWidget(conn_group)

        # -- Options section -------------------------------------------------
        opt_group = QGroupBox("Options")
        opt_layout = QVBoxLayout(opt_group)
        opt_layout.setContentsMargins(12, 12, 12, 12)
        opt_layout.setSpacing(8)

        # Codec selector
        codec_row = QHBoxLayout()
        codec_row.addWidget(QLabel("Preferred codec"))
        codec_row.addStretch()
        self._codec_combo = QComboBox()
        self._codec_combo.addItems(["SBC", "AAC"])
        self._codec_combo.setFixedWidth(90)
        self._codec_combo.setToolTip(
            "SBC: mandatory codec, always works\n"
            "AAC: higher quality, iPhone preferred\n"
            "Changing codec requires engine restart"
        )
        self._codec_combo.currentTextChanged.connect(self._on_codec_changed)
        codec_row.addWidget(self._codec_combo)
        opt_layout.addLayout(codec_row)

        # Launch at startup
        self._startup_cb = QCheckBox("Launch at startup")
        self._startup_cb.toggled.connect(self._on_startup_toggled)
        opt_layout.addWidget(self._startup_cb)

        # Start minimized
        self._minimized_cb = QCheckBox("Start minimized to tray")
        self._minimized_cb.toggled.connect(self._on_minimized_toggled)
        opt_layout.addWidget(self._minimized_cb)

        # Auto-reconnect to last device
        self._auto_reconnect_cb = QCheckBox("Auto-connect to last device on startup")
        self._auto_reconnect_cb.toggled.connect(self._on_auto_reconnect_toggled)
        opt_layout.addWidget(self._auto_reconnect_cb)

        root.addWidget(opt_group)

        # -- Footer ----------------------------------------------------------
        root.addStretch()

        footer = QHBoxLayout()
        self._log_btn = QPushButton("Debug log")
        self._log_btn.clicked.connect(self.open_log_requested.emit)
        footer.addWidget(self._log_btn)

        footer.addStretch()

        version_label = QLabel(f"v{self.VERSION}")
        version_label.setObjectName("versionLabel")
        footer.addWidget(version_label)

        root.addLayout(footer)

    def _make_info_row(self, label: str, value: str) -> tuple[QLabel, QLabel]:
        lbl = QLabel(label)
        lbl.setObjectName("infoKey")
        val = QLabel(value)
        val.setObjectName("infoValue")
        val.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        return lbl, val

    # ── Stylesheet ──────────────────────────────────────────────────────────

    def _apply_stylesheet(self) -> None:
        self.setStyleSheet("""
            QWidget {
                font-size: 12px;
            }
            QGroupBox {
                font-size: 12px;
                font-weight: 600;
                border: 1px solid palette(mid);
                border-radius: 6px;
                margin-top: 14px;
                padding-top: 14px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 4px;
            }
            #infoFrame {
                background-color: palette(base);
                border: 1px solid palette(mid);
                border-radius: 4px;
            }
            #infoKey {
                color: palette(dark);
                font-size: 11px;
            }
            #infoValue {
                font-size: 11px;
            }
            #statusLabel {
                font-size: 11px;
                color: palette(dark);
            }
            #versionLabel {
                font-size: 10px;
                color: palette(dark);
            }
            QPushButton {
                padding: 5px 14px;
                border: 1px solid palette(mid);
                border-radius: 4px;
                background-color: palette(button);
            }
            QPushButton:hover {
                background-color: palette(midlight);
            }
            #engineBtn, #connActionBtn {
                font-weight: 600;
                font-size: 12px;
                border-radius: 6px;
                padding: 5px 12px;
                border: none;
                color: white;
            }
            QComboBox {
                padding: 3px 8px;
                border: 1px solid palette(mid);
                border-radius: 4px;
            }
        """)

    # ── Config load/save ────────────────────────────────────────────────────

    def _load_config(self) -> None:
        """Populate UI from config."""
        # Codec: map config value to UI display
        codec_pref = self._config.get("preferred_codec", "both")
        if codec_pref == "both" or codec_pref == "AAC":
            self._codec_combo.setCurrentText("AAC")
        else:
            self._codec_combo.setCurrentText("SBC")

        self._startup_cb.setChecked(self._config.get("launch_at_startup", False))
        self._minimized_cb.setChecked(self._config.get("start_minimized", True))
        self._auto_reconnect_cb.setChecked(self._config.get("auto_reconnect_last_device", True))

    def _save_config(self) -> None:
        """Persist current settings."""
        self._config["launch_at_startup"] = self._startup_cb.isChecked()
        self._config["start_minimized"] = self._minimized_cb.isChecked()
        self._config["auto_reconnect_last_device"] = self._auto_reconnect_cb.isChecked()

        pos = self.pos()
        self._config["window_x"] = pos.x()
        self._config["window_y"] = pos.y()

        self._config.save()

    # ── Public update slots (called by main.py) ─────────────────────────────

    @Slot(str, str)
    def update_connection_state(
        self,
        state_name: str,
        device_info: str = "",
    ) -> None:
        """Update all connection-related UI elements."""
        self._status_dot.set_state(state_name)
        self._current_state = state_name

        is_connected = state_name in ("CONNECTED", "STREAMING")
        is_running = state_name not in ("IDLE",)
        self._is_connected = is_connected

        # Status text
        status_map = {
            "IDLE": "Idle",
            "INITIALIZING": "Initializing...",
            "READY": "Waiting for device",
            "CONNECTED": "Connected",
            "STREAMING": "Streaming",
        }
        self._status_label.setText(status_map.get(state_name, state_name))

        # ── Engine button ──
        if is_running:
            self._engine_btn.setText("■  Stop")
            self._engine_btn.setStyleSheet("""
                #engineBtn {
                    background-color: #6B7280; color: white;
                    border: none; border-radius: 6px;
                    font-weight: 600; font-size: 12px; padding: 5px 12px;
                }
                #engineBtn:hover { background-color: #5B636E; }
            """)
        else:
            self._engine_btn.setText("▶  Start")
            self._engine_btn.setStyleSheet("""
                #engineBtn {
                    background-color: #2D8C5A; color: white;
                    border: none; border-radius: 6px;
                    font-weight: 600; font-size: 12px; padding: 5px 12px;
                }
                #engineBtn:hover { background-color: #248A50; }
            """)

        # ── Connect / Disconnect button ──
        if state_name == "IDLE":
            self._conn_action_btn.setVisible(False)
        elif is_connected:
            self._conn_action_btn.setText("Disconnect")
            self._conn_action_btn.setVisible(True)
            self._conn_action_btn.setEnabled(True)
            self._conn_action_btn.setStyleSheet("""
                #connActionBtn {
                    background-color: #DC4C4C; color: white;
                    border: none; border-radius: 6px;
                    font-weight: 600; font-size: 12px; padding: 5px 12px;
                }
                #connActionBtn:hover { background-color: #C43E3E; }
            """)
        else:
            self._conn_action_btn.setText("Connect")
            self._conn_action_btn.setVisible(True)
            self._conn_action_btn.setEnabled(state_name == "READY")
            if state_name == "READY":
                self._conn_action_btn.setStyleSheet("""
                    #connActionBtn {
                        background-color: #378ADD; color: white;
                        border: none; border-radius: 6px;
                        font-weight: 600; font-size: 12px; padding: 5px 12px;
                    }
                    #connActionBtn:hover { background-color: #2E78C4; }
                """)
            else:
                self._conn_action_btn.setStyleSheet("""
                    #connActionBtn {
                        background-color: #A0AEC0; color: white;
                        border: none; border-radius: 6px;
                        font-weight: 600; font-size: 12px; padding: 5px 12px;
                    }
                """)

    @Slot(str, str, str)
    def update_device_info(
        self,
        device_name: str = "—",
        device_addr: str = "—",
        codec: str = "—",
    ) -> None:
        """Update the connection info card."""
        self._device_row[1].setText(device_name or "—")
        self._addr_row[1].setText(device_addr or "—")
        self._codec_row[1].setText(codec or "—")

    @Slot(int)
    def clear_device_info(self) -> None:
        """Reset device info to defaults (on disconnect)."""
        self.update_device_info("—", "—", "—")

    # ── Internal handlers ───────────────────────────────────────────────────

    def _on_engine_toggle(self) -> None:
        is_running = getattr(self, '_current_state', 'IDLE') != 'IDLE'
        self.connection_toggled.emit(not is_running)

    def _on_conn_action(self) -> None:
        if self._is_connected:
            self.disconnect_requested.emit()
        else:
            self.connect_requested.emit()

    def _on_codec_changed(self, text: str) -> None:
        self.codec_changed.emit(text)

    def _on_startup_toggled(self, checked: bool) -> None:
        self._config.set_launch_at_startup(checked)
        self._config.save()

    def _on_minimized_toggled(self, checked: bool) -> None:
        self._config["start_minimized"] = checked
        self._config.save()

    def _on_auto_reconnect_toggled(self, checked: bool) -> None:
        self._config["auto_reconnect_last_device"] = checked
        self._config.save()

    # ── Window events ───────────────────────────────────────────────────────

    def closeEvent(self, event: QCloseEvent) -> None:
        """Hide to tray instead of closing."""
        self._save_config()
        self.hide()
        self.closed.emit()
        event.ignore()

    def show_and_raise(self) -> None:
        """Show window, bring to front, and restore if minimized."""
        self.show()
        self.setWindowState(
            self.windowState() & ~Qt.WindowState.WindowMinimized
        )
        self.raise_()
        self.activateWindow()