"""
test_tray.py — Standalone test for tray icon and menu.

Usage:
    python -m app.test_tray

Cycles through connection states every 3 seconds so you can verify
icon changes and tooltip updates. Press Ctrl+C or use "Quit" menu to exit.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from app.config import AppConfig
from app.tray_app import TrayApp


def main() -> None:
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)  # Keep running when no windows open

    config = AppConfig()
    tray = TrayApp(config)

    # Wire up signals for testing
    tray.open_settings_requested.connect(
        lambda: print("[test] Settings requested")
    )
    tray.quit_requested.connect(app.quit)
    tray.reconnect_requested.connect(
        lambda: print("[test] Reconnect requested")
    )
    tray.toggle_connection_requested.connect(
        lambda on: print(f"[test] Connection toggle: {'ON' if on else 'OFF'}")
    )

    tray.show()

    # Cycle through states for visual testing
    states = [
        ("IDLE", ""),
        ("INITIALIZING", ""),
        ("READY", ""),
        ("CONNECTED", "iPhone (68:EF:DC:CE:8C:F9)"),
        ("STREAMING", "iPhone (68:EF:DC:CE:8C:F9)"),
    ]
    state_idx = [0]

    def cycle_state() -> None:
        name, info = states[state_idx[0]]
        print(f"[test] State → {name}")
        tray.update_state(name, info)
        state_idx[0] = (state_idx[0] + 1) % len(states)

    timer = QTimer()
    timer.timeout.connect(cycle_state)
    timer.start(3000)
    cycle_state()  # Start immediately

    print("Tray test running. Check system tray. Quit via menu or Ctrl+C.")
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
