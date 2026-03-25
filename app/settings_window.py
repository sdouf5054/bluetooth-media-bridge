"""
settings_window.py — Compact settings window for Bluetooth Media Bridge.

Responsibilities:
  - Show connection status and device info
  - Volume slider (synced with BT device volume)
  - Codec selection, startup, minimize options
  - "Debug log" button opens LogWindow
  - Close button → hide to tray (not quit)

Layout sections (top to bottom):
  1. Title bar with app name + connection indicator
  2. Connection card (status, device, address, codec)
  3. Volume slider
  4. Options (codec, launch at startup, start minimized)
  5. Footer (debug log button, version)
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional

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
    QSlider,
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
      - volume_changed(int): user moved volume slider (0-100)
      - codec_changed(str): user selected a different codec
      - connection_toggled(bool): user toggled connect/disconnect
      - open_log_requested: user clicked "Debug log"
      - closed: window was closed (hidden to tray)
    """

    volume_changed = Signal(int)
    codec_changed = Signal(str)
    connection_toggled = Signal(bool)
    open_log_requested = Signal()
    closed = Signal()

    VERSION = "0.1.0"

    def __init__(
        self,
        config: "AppConfig",
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._config = config
        self._is_connected = False

        self.setWindowTitle("Bluetooth Media Bridge")
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
        conn_layout.setSpacing(6)

        # Toggle + status row
        toggle_row = QHBoxLayout()
        self._conn_toggle = QPushButton("Connect")
        self._conn_toggle.setFixedWidth(100)
        self._conn_toggle.setCheckable(True)
        self._conn_toggle.clicked.connect(self._on_connection_toggle)
        toggle_row.addWidget(self._conn_toggle)
        toggle_row.addStretch()

        self._status_label = QLabel("Idle")
        self._status_label.setObjectName("statusLabel")
        toggle_row.addWidget(self._status_label)
        conn_layout.addLayout(toggle_row)

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

        # -- Volume section --------------------------------------------------
        vol_group = QGroupBox("Volume")
        vol_layout = QHBoxLayout(vol_group)
        vol_layout.setContentsMargins(12, 12, 12, 12)

        self._vol_slider = QSlider(Qt.Orientation.Horizontal)
        self._vol_slider.setRange(0, 100)
        self._vol_slider.setValue(0)
        self._vol_slider.setTickPosition(QSlider.TickPosition.NoTicks)
        self._vol_slider.valueChanged.connect(self._on_volume_slider)
        vol_layout.addWidget(self._vol_slider, 1)

        self._vol_label = QLabel("—")
        self._vol_label.setFixedWidth(36)
        self._vol_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        vol_layout.addWidget(self._vol_label)

        root.addWidget(vol_group)

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
        self._codec_combo.setEnabled(False)  # AAC not yet supported
        self._codec_combo.setToolTip("AAC support coming soon")
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
            QPushButton:checked {
                background-color: palette(highlight);
                color: palette(highlighted-text);
                border-color: palette(highlight);
            }
            QSlider::groove:horizontal {
                height: 4px;
                background: palette(mid);
                border-radius: 2px;
            }
            QSlider::handle:horizontal {
                background: palette(highlight);
                width: 14px;
                height: 14px;
                margin: -5px 0;
                border-radius: 7px;
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
        codec = self._config.get("preferred_codec", "SBC")
        idx = self._codec_combo.findText(codec)
        if idx >= 0:
            self._codec_combo.setCurrentIndex(idx)

        self._startup_cb.setChecked(self._config.get("launch_at_startup", False))
        self._minimized_cb.setChecked(self._config.get("start_minimized", True))

    def _save_config(self) -> None:
        """Persist current settings."""
        self._config["preferred_codec"] = self._codec_combo.currentText()
        self._config["launch_at_startup"] = self._startup_cb.isChecked()
        self._config["start_minimized"] = self._minimized_cb.isChecked()

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

        # Toggle button
        self._conn_toggle.setChecked(is_running)
        self._conn_toggle.setText("Disconnect" if is_running else "Connect")

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
    def update_volume(self, percent: int) -> None:
        """Update volume slider from engine (without re-emitting signal)."""
        self._vol_slider.blockSignals(True)
        self._vol_slider.setValue(percent)
        self._vol_slider.blockSignals(False)
        self._vol_label.setText(f"{percent}%")

    def clear_device_info(self) -> None:
        """Reset device info to defaults (on disconnect)."""
        self.update_device_info("—", "—", "—")
        self.update_volume(0)
        self._vol_label.setText("—")

    # ── Internal handlers ───────────────────────────────────────────────────

    def _on_connection_toggle(self, checked: bool) -> None:
        self.connection_toggled.emit(checked)

    def _on_volume_slider(self, value: int) -> None:
        self._vol_label.setText(f"{value}%")
        self.volume_changed.emit(value)

    def _on_codec_changed(self, text: str) -> None:
        self._config["preferred_codec"] = text
        self._config.save()
        self.codec_changed.emit(text)

    def _on_startup_toggled(self, checked: bool) -> None:
        self._config.set_launch_at_startup(checked)
        self._config.save()

    def _on_minimized_toggled(self, checked: bool) -> None:
        self._config["start_minimized"] = checked
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
