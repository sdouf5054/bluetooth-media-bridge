"""
test_gui.py — Full GUI test with tray, settings window, and log viewer.

Usage:
    python -m app.test_gui

Simulates connection state changes. Exercises all UI components together.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from app.config import AppConfig
from app.tray_app import TrayApp
from app.settings_window import SettingsWindow
from app.log_window import LogWindow


def main() -> None:
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    config = AppConfig()

    # Create components
    tray = TrayApp(config)
    settings_win = SettingsWindow(config)
    log_win = LogWindow(max_lines=config.get("log_max_lines", 5000))

    # Wire tray signals
    tray.open_settings_requested.connect(settings_win.show_and_raise)
    tray.quit_requested.connect(app.quit)
    tray.reconnect_requested.connect(
        lambda: log_win.append_line("[test] Reconnect requested")
    )
    tray.toggle_connection_requested.connect(
        lambda on: log_win.append_line(
            f"[test] Connection toggle: {'ON' if on else 'OFF'}"
        )
    )

    # Wire settings signals
    settings_win.open_log_requested.connect(log_win.show_and_raise)
    settings_win.volume_changed.connect(
        lambda v: log_win.append_line(f"[test] Volume slider → {v}%")
    )
    settings_win.connection_toggled.connect(
        lambda on: log_win.append_line(
            f"[test] Connection toggled: {'ON' if on else 'OFF'}"
        )
    )

    # Show components
    tray.show()
    settings_win.show()

    # Simulate state cycle
    states = [
        ("IDLE",         "",                         "", "", ""),
        ("INITIALIZING", "",                         "", "", ""),
        ("READY",        "",                         "", "", ""),
        ("CONNECTED",    "iPhone (68:EF:DC:CE:8C:F9)", "iPhone", "68:EF:DC:CE:8C:F9", "SBC"),
        ("STREAMING",    "iPhone (68:EF:DC:CE:8C:F9)", "iPhone", "68:EF:DC:CE:8C:F9", "SBC"),
    ]
    idx = [0]

    def cycle() -> None:
        state, tray_info, dev, addr, codec = states[idx[0]]
        log_win.append_line(f"[sim] State → {state}")

        tray.update_state(state, tray_info)
        settings_win.update_connection_state(state, tray_info)

        if state in ("CONNECTED", "STREAMING"):
            settings_win.update_device_info(dev, addr, codec)
            settings_win.update_volume(72)
            if state == "CONNECTED":
            pass
        elif state == "IDLE":
            settings_win.clear_device_info()

        idx[0] = (idx[0] + 1) % len(states)

    timer = QTimer()
    timer.timeout.connect(cycle)
    timer.start(4000)
    cycle()

    # Seed some log lines
    for i in range(5):
        log_win.append_line(f"[boot] Initialization message {i + 1}")

    print("GUI test running. Tray icon + settings window active.")
    print("Click tray icon → settings. 'Debug log' button → log viewer.")
    print("States cycle every 4 seconds. Quit via tray menu.")
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
