"""
config.py — Persistent configuration for Bluetooth Media Bridge.

Stores user preferences as JSON. Provides typed access with defaults.
Config file location: same directory as the app package.

Startup registration:
  - Frozen (exe): registers exe path directly with --startup flag
  - Script mode: uses pythonw.exe (if available) to avoid console window
  - verify_startup_path(): auto-fixes registry if exe was moved
"""

from __future__ import annotations

import json
import logging
import os
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
    "auto_reconnect_last_device": True,  # auto-connect to last paired device on startup
    "build_dir": str(_DEFAULT_BUILD_DIR),

    # Audio / Codec
    "preferred_codec": "both",  # "SBC", "AAC", or "both" (let iPhone choose)

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


# ── Windows startup registration helpers ───────────────────────────────────

_REG_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_REG_NAME = "BluetoothMediaBridge"


def _get_startup_command() -> str:
    """Build the correct startup command for the current environment."""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" --startup'
    else:
        python_dir = os.path.dirname(sys.executable)
        pythonw = os.path.join(python_dir, "pythonw.exe")
        if not os.path.exists(pythonw):
            pythonw = sys.executable
        main_py = str(Path(__file__).resolve().parent / "main.py")
        return f'"{pythonw}" "{main_py}" --startup'


def _is_startup_registered() -> bool:
    """Check if the app is registered in Windows startup."""
    if sys.platform != "win32":
        return False
    try:
        import winreg
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, _REG_KEY, 0, winreg.KEY_READ
        ) as key:
            winreg.QueryValueEx(key, _REG_NAME)
            return True
    except Exception:
        return False


def _register_startup() -> bool:
    """Register the app in Windows startup."""
    if sys.platform != "win32":
        return False
    try:
        import winreg
        cmd = _get_startup_command()
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, _REG_KEY, 0, winreg.KEY_SET_VALUE
        ) as key:
            winreg.SetValueEx(key, _REG_NAME, 0, winreg.REG_SZ, cmd)
        logger.info("Startup registration added: %s", cmd)
        return True
    except Exception:
        logger.exception("Failed to register startup")
        return False


def _unregister_startup() -> None:
    """Remove the app from Windows startup."""
    if sys.platform != "win32":
        return
    try:
        import winreg
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, _REG_KEY, 0, winreg.KEY_SET_VALUE
        ) as key:
            winreg.DeleteValue(key, _REG_NAME)
        logger.info("Startup registration removed")
    except FileNotFoundError:
        pass
    except Exception:
        logger.exception("Failed to unregister startup")


def verify_startup_path() -> None:
    """Auto-fix startup registry if the exe/script path has changed."""
    if sys.platform != "win32":
        return
    if not _is_startup_registered():
        return

    try:
        import winreg
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, _REG_KEY, 0, winreg.KEY_READ
        ) as key:
            registered_cmd, _ = winreg.QueryValueEx(key, _REG_NAME)
    except Exception:
        return

    expected_cmd = _get_startup_command()
    if registered_cmd.strip('"').lower() != expected_cmd.strip('"').lower():
        logger.info(
            "Startup path changed, updating: %s → %s",
            registered_cmd, expected_cmd,
        )
        _register_startup()


# ── AppConfig class ────────────────────────────────────────────────────────

class AppConfig:
    """
    Read/write JSON configuration with typed defaults.

    Usage:
        cfg = AppConfig()              # loads from default path
        codec = cfg["preferred_codec"] # -> "both"
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

    # -- startup registration ------------------------------------------------

    def set_launch_at_startup(self, enabled: bool) -> None:
        """Register/unregister from Windows startup."""
        self["launch_at_startup"] = enabled
        if enabled:
            if not _register_startup():
                logger.error("Failed to register startup")
        else:
            _unregister_startup()

    @staticmethod
    def is_startup_registered() -> bool:
        """Check if currently registered in Windows startup."""
        return _is_startup_registered()

    # -- repr ----------------------------------------------------------------

    def __repr__(self) -> str:
        return f"AppConfig(path={self._path}, keys={list(self._data.keys())})"