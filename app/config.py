"""
config.py — Persistent configuration for Bluetooth Media Bridge.

Stores user preferences as JSON. Provides typed access with defaults.
Config file location: same directory as the app package.
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Default config path: project_root/config.json
_DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.json"

# Build directory default (same as process_manager)
_DEFAULT_BUILD_DIR = (
    Path(__file__).resolve().parent.parent
    / "bluetooth_bridge" / "btstack" / "port" / "windows-winusb" / "build"
)

DEFAULTS: dict[str, Any] = {
    # Connection
    "auto_connect": True,
    "build_dir": str(_DEFAULT_BUILD_DIR),

    # Audio
    "preferred_codec": "SBC",  # "SBC" or "AAC" (AAC not yet supported)

    # UI / Behavior
    "start_minimized": True,
    "launch_at_startup": False,
    "show_notifications": False,

    # Window geometry (saved on close)
    "window_x": -1,
    "window_y": -1,
    "window_width": 380,
    "window_height": 520,

    # Debug
    "debug_log_enabled": False,
    "log_max_lines": 5000,
}


class AppConfig:
    """
    Read/write JSON configuration with typed defaults.

    Usage:
        cfg = AppConfig()              # loads from default path
        codec = cfg["preferred_codec"] # -> "SBC"
        cfg["preferred_codec"] = "AAC"
        cfg.save()
    """

    def __init__(self, path: Path | str | None = None) -> None:
        self._path = Path(path) if path else _DEFAULT_CONFIG_PATH
        self._data: dict[str, Any] = dict(DEFAULTS)
        self.load()

    @property
    def path(self) -> Path:
        return self._path

    # -- dict-like access ----------------------------------------------------

    def __getitem__(self, key: str) -> Any:
        return self._data.get(key, DEFAULTS.get(key))

    def __setitem__(self, key: str, value: Any) -> None:
        self._data[key] = value

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default if default is not None else DEFAULTS.get(key))

    # -- persistence ---------------------------------------------------------

    def load(self) -> None:
        """Load config from disk. Missing keys get defaults."""
        if not self._path.is_file():
            logger.info("Config file not found, using defaults: %s", self._path)
            return
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                self._data.update(loaded)
                logger.info("Config loaded: %s", self._path)
            else:
                logger.warning("Config file has unexpected format, using defaults")
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Failed to load config (%s), using defaults", e)

    def save(self) -> None:
        """Write current config to disk."""
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._path, "w", encoding="utf-8") as f:
                json.dump(self._data, f, indent=2, ensure_ascii=False)
            logger.debug("Config saved: %s", self._path)
        except OSError as e:
            logger.error("Failed to save config: %s", e)

    def reset(self) -> None:
        """Reset all settings to defaults."""
        self._data = dict(DEFAULTS)

    # -- startup registration (Windows) --------------------------------------

    def set_launch_at_startup(self, enabled: bool) -> None:
        """Register/unregister from Windows startup via registry."""
        self["launch_at_startup"] = enabled
        if sys.platform != "win32":
            logger.warning("Startup registration only supported on Windows")
            return
        try:
            import winreg
            key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
            app_name = "BluetoothMediaBridge"

            if enabled:
                # Get the Python executable and main module path
                exe = sys.executable
                main_module = str(
                    Path(__file__).resolve().parent / "main.py"
                )
                cmd = f'"{exe}" "{main_module}"'
                with winreg.OpenKey(
                    winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE
                ) as key:
                    winreg.SetValueEx(key, app_name, 0, winreg.REG_SZ, cmd)
                logger.info("Startup registration added: %s", cmd)
            else:
                with winreg.OpenKey(
                    winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE
                ) as key:
                    try:
                        winreg.DeleteValue(key, app_name)
                        logger.info("Startup registration removed")
                    except FileNotFoundError:
                        pass
        except Exception:
            logger.exception("Failed to update startup registration")

    # -- repr ----------------------------------------------------------------

    def __repr__(self) -> str:
        return f"AppConfig(path={self._path}, keys={list(self._data.keys())})"
