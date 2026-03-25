"""
log_window.py — Debug log viewer for Bluetooth Media Bridge.

Responsibilities:
  - Display bt_bridge.exe stdout/stderr and engine log messages
  - Auto-scroll to bottom (unless user scrolled up to read)
  - Copy to clipboard / save to file
  - Clear log
  - Max line limit to prevent memory growth
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, Slot, QTimer
from PySide6.QtGui import QCloseEvent, QFont, QTextCursor
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

logger = logging.getLogger(__name__)

DEFAULT_MAX_LINES = 5000


class LogWindow(QWidget):
    """
    Separate window displaying debug log output.

    Usage:
        log_win = LogWindow(max_lines=5000)
        log_win.append_line("[exe] btstack ready")
        log_win.show()
    """

    def __init__(
        self,
        max_lines: int = DEFAULT_MAX_LINES,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._max_lines = max_lines
        self._auto_scroll = True
        self._line_count = 0

        self.setWindowTitle("Debug Log — Bluetooth Media Bridge")
        self.setMinimumSize(600, 400)
        self.resize(720, 480)
        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.WindowCloseButtonHint
            | Qt.WindowType.WindowMinimizeButtonHint
            | Qt.WindowType.WindowMaximizeButtonHint
        )

        self._build_ui()

    # ── UI construction ─────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        # Log text area
        self._text = QPlainTextEdit()
        self._text.setReadOnly(True)
        self._text.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self._text.setMaximumBlockCount(self._max_lines)

        log_font = QFont("Consolas", 9)
        log_font.setStyleHint(QFont.StyleHint.Monospace)
        self._text.setFont(log_font)

        self._text.setStyleSheet("""
            QPlainTextEdit {
                background-color: palette(base);
                color: palette(text);
                border: 1px solid palette(mid);
                border-radius: 4px;
                padding: 4px;
            }
        """)

        # Detect when user scrolls manually
        self._text.verticalScrollBar().valueChanged.connect(self._on_scroll)

        root.addWidget(self._text, 1)

        # Button row
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        self._auto_scroll_btn = QPushButton("Auto-scroll: ON")
        self._auto_scroll_btn.setCheckable(True)
        self._auto_scroll_btn.setChecked(True)
        self._auto_scroll_btn.clicked.connect(self._toggle_auto_scroll)
        btn_row.addWidget(self._auto_scroll_btn)

        btn_row.addStretch()

        copy_btn = QPushButton("Copy all")
        copy_btn.clicked.connect(self._copy_to_clipboard)
        btn_row.addWidget(copy_btn)

        save_btn = QPushButton("Save to file...")
        save_btn.clicked.connect(self._save_to_file)
        btn_row.addWidget(save_btn)

        clear_btn = QPushButton("Clear")
        clear_btn.clicked.connect(self._clear_log)
        btn_row.addWidget(clear_btn)

        root.addLayout(btn_row)

    # ── Public API ──────────────────────────────────────────────────────────

    @Slot(str)
    def append_line(self, line: str) -> None:
        """Append a single line to the log."""
        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        self._text.appendPlainText(f"[{ts}] {line}")
        self._line_count += 1

        if self._auto_scroll:
            self._scroll_to_bottom()

    @Slot(str)
    def append_raw(self, text: str) -> None:
        """Append text without timestamp (for pre-formatted output)."""
        self._text.appendPlainText(text)
        if self._auto_scroll:
            self._scroll_to_bottom()

    # ── Internal ────────────────────────────────────────────────────────────

    def _scroll_to_bottom(self) -> None:
        sb = self._text.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _on_scroll(self, value: int) -> None:
        """Detect if user manually scrolled away from bottom."""
        sb = self._text.verticalScrollBar()
        at_bottom = value >= sb.maximum() - 10
        if self._auto_scroll and not at_bottom:
            # User scrolled up — pause auto-scroll
            self._auto_scroll = False
            self._auto_scroll_btn.setChecked(False)
            self._auto_scroll_btn.setText("Auto-scroll: OFF")

    def _toggle_auto_scroll(self, checked: bool) -> None:
        self._auto_scroll = checked
        self._auto_scroll_btn.setText(
            "Auto-scroll: ON" if checked else "Auto-scroll: OFF"
        )
        if checked:
            self._scroll_to_bottom()

    def _copy_to_clipboard(self) -> None:
        from PySide6.QtWidgets import QApplication
        clipboard = QApplication.clipboard()
        if clipboard:
            clipboard.setText(self._text.toPlainText())
            logger.info("Log copied to clipboard (%d lines)", self._line_count)

    def _save_to_file(self) -> None:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        default_name = f"bt_bridge_log_{ts}.txt"
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save debug log",
            default_name,
            "Text files (*.txt);;All files (*)",
        )
        if not path:
            return
        try:
            Path(path).write_text(
                self._text.toPlainText(), encoding="utf-8"
            )
            logger.info("Log saved to %s", path)
        except OSError as e:
            logger.error("Failed to save log: %s", e)

    def _clear_log(self) -> None:
        self._text.clear()
        self._line_count = 0

    # ── Window events ───────────────────────────────────────────────────────

    def closeEvent(self, event: QCloseEvent) -> None:
        """Just hide, don't destroy."""
        self.hide()
        event.ignore()

    def show_and_raise(self) -> None:
        """Show, bring to front."""
        self.show()
        self.setWindowState(
            self.windowState() & ~Qt.WindowState.WindowMinimized
        )
        self.raise_()
        self.activateWindow()
