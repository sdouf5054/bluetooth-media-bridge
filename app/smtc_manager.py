"""
smtc_manager.py — Windows System Media Transport Controls integration.

Responsibilities:
  - Create and own a SystemMediaTransportControls session
  - Update display (title, artist, album, thumbnail) from bridge metadata
  - Sync playback status (playing/paused/stopped)
  - Receive media key events (play/pause/next/prev) and forward via callback
  - Manage thumbnail lifecycle (write temp file for SMTC RandomAccessStreamReference)

Requires: pip install winsdk
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import tempfile
from enum import Enum, auto
from pathlib import Path
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

# ── winsdk imports (Windows-only) ──────────────────────────────────────────
# These will fail on non-Windows; callers should gate on platform.
try:
    from winsdk.windows.media import (
        MediaPlaybackStatus,
        MediaPlaybackType,
        SystemMediaTransportControls,
        SystemMediaTransportControlsDisplayUpdater,
    )
    from winsdk.windows.media import (
        SystemMediaTransportControlsButton,
        SystemMediaTransportControlsButtonPressedEventArgs,
    )
    from winsdk.windows.storage import StorageFile
    from winsdk.windows.storage.streams import RandomAccessStreamReference

    _WINSDK_AVAILABLE = True
except ImportError:
    _WINSDK_AVAILABLE = False
    logger.warning("winsdk not available — SMTC features disabled")


# Media key actions that the bridge can handle
class MediaAction(Enum):
    PLAY = auto()
    PAUSE = auto()
    STOP = auto()
    NEXT = auto()
    PREVIOUS = auto()


# Callback type: fn(action: MediaAction)
MediaKeyCallback = Callable[[MediaAction], None]


class SMTCManager:
    """
    Manage a Windows SMTC session for the Bluetooth Media Bridge.

    Usage:
        smtc = SMTCManager(on_media_key=my_handler)
        await smtc.initialize()
        smtc.update_metadata(title="Song", artist="Artist", album="Album")
        smtc.update_playback_status("playing")
        await smtc.update_thumbnail(Path("cover.jpg"))
        ...
        smtc.shutdown()
    """

    def __init__(self, on_media_key: Optional[MediaKeyCallback] = None) -> None:
        self._on_media_key = on_media_key
        self._smtc: Optional[SystemMediaTransportControls] = None
        self._updater: Optional[SystemMediaTransportControlsDisplayUpdater] = None
        self._initialized = False
        # Temp directory for thumbnail copies (SMTC needs a StorageFile)
        self._temp_dir: Optional[Path] = None
        self._current_thumbnail: Optional[Path] = None

    @property
    def available(self) -> bool:
        return _WINSDK_AVAILABLE

    @property
    def initialized(self) -> bool:
        return self._initialized

    # ── lifecycle ──────────────────────────────────────────────────────────

    async def initialize(self) -> None:
        """Create the SMTC session. Must be called from an async context."""
        if not _WINSDK_AVAILABLE:
            logger.warning("SMTC: winsdk not available, skipping initialization")
            return

        if self._initialized:
            return

        try:
            # Get the SMTC instance for the current app
            # For desktop (non-UWP) apps we use the background approach:
            # CommandManager provides SMTC for win32 processes.
            from winsdk.windows.media.playback import MediaPlayer

            self._player = MediaPlayer()
            self._smtc = self._player.system_media_transport_controls
            self._smtc.is_enabled = True
            self._smtc.is_play_enabled = True
            self._smtc.is_pause_enabled = True
            self._smtc.is_stop_enabled = True
            self._smtc.is_next_enabled = True
            self._smtc.is_previous_enabled = True

            # Set playback type to music
            self._updater = self._smtc.display_updater
            self._updater.type = MediaPlaybackType.MUSIC

            # Register button press handler
            self._smtc.add_button_pressed(self._on_button_pressed)

            # Create temp directory for thumbnails
            self._temp_dir = Path(tempfile.mkdtemp(prefix="btbridge_smtc_"))

            self._initialized = True
            logger.info("SMTC: Initialized successfully")

        except Exception:
            logger.exception("SMTC: Failed to initialize")
            self._initialized = False

    def shutdown(self) -> None:
        """Disable SMTC and clean up resources."""
        if not self._initialized:
            return

        try:
            if self._smtc:
                self._smtc.is_enabled = False
            if self._updater:
                self._updater.clear_all()
                self._updater.update()
        except Exception:
            logger.exception("SMTC: Error during shutdown")

        # Clean up temp directory
        if self._temp_dir and self._temp_dir.exists():
            try:
                shutil.rmtree(self._temp_dir)
            except Exception:
                pass

        self._smtc = None
        self._updater = None
        self._player = None
        self._initialized = False
        logger.info("SMTC: Shut down")

    # ── metadata updates ───────────────────────────────────────────────────

    def update_metadata(
        self,
        title: str = "",
        artist: str = "",
        album: str = "",
    ) -> None:
        """Update the SMTC display with track metadata."""
        if not self._initialized or not self._updater:
            return

        try:
            props = self._updater.music_properties
            props.title = title
            props.artist = artist
            props.album_title = album
            self._updater.update()
            logger.debug("SMTC: Metadata updated — %s — %s", artist, title)
        except Exception:
            logger.exception("SMTC: Failed to update metadata")

    def update_playback_status(self, status: str) -> None:
        """
        Update SMTC playback status.

        Args:
            status: One of "playing", "paused", "stopped", "unknown"
        """
        if not self._initialized or not self._smtc:
            return

        status_map = {
            "playing": MediaPlaybackStatus.PLAYING,
            "paused": MediaPlaybackStatus.PAUSED,
            "stopped": MediaPlaybackStatus.STOPPED,
            "seeking": MediaPlaybackStatus.CHANGING,
            "unknown": MediaPlaybackStatus.CLOSED,
        }

        smtc_status = status_map.get(status, MediaPlaybackStatus.CLOSED)
        try:
            self._smtc.playback_status = smtc_status
            logger.debug("SMTC: Playback status → %s", status)
        except Exception:
            logger.exception("SMTC: Failed to update playback status")

    async def update_thumbnail(self, image_path: Path) -> None:
        """
        Set the SMTC thumbnail from a JPEG file.

        Copies the file to a temp location (SMTC holds a reference to the file),
        then creates a RandomAccessStreamReference from it.
        """
        if not self._initialized or not self._updater:
            return
        if not image_path.is_file():
            logger.warning("SMTC: Thumbnail file not found: %s", image_path)
            return

        try:
            # Copy to temp dir so the original can be safely overwritten
            # by bt_bridge on the next track change
            thumb_copy = self._temp_dir / f"thumb_{image_path.stat().st_mtime_ns}.jpg"
            shutil.copy2(image_path, thumb_copy)

            # Clean up previous thumbnail copy
            if self._current_thumbnail and self._current_thumbnail != thumb_copy:
                try:
                    self._current_thumbnail.unlink(missing_ok=True)
                except Exception:
                    pass

            self._current_thumbnail = thumb_copy

            # Create StorageFile → RandomAccessStreamReference
            storage_file = await StorageFile.get_file_from_path_async(
                str(thumb_copy)
            )
            stream_ref = RandomAccessStreamReference.create_from_file(storage_file)
            self._updater.thumbnail = stream_ref
            self._updater.update()
            logger.debug("SMTC: Thumbnail updated from %s", image_path.name)

        except Exception:
            logger.exception("SMTC: Failed to update thumbnail")

    def clear_display(self) -> None:
        """Clear all SMTC display info (e.g. on disconnect)."""
        if not self._initialized or not self._updater:
            return
        try:
            self._updater.clear_all()
            self._updater.type = MediaPlaybackType.MUSIC
            self._updater.update()
            logger.debug("SMTC: Display cleared")
        except Exception:
            logger.exception("SMTC: Failed to clear display")

    # ── media key handling ─────────────────────────────────────────────────

    def _on_button_pressed(
        self,
        sender: SystemMediaTransportControls,
        args: SystemMediaTransportControlsButtonPressedEventArgs,
    ) -> None:
        """Handle SMTC media key button presses."""
        button = args.button

        action_map = {
            SystemMediaTransportControlsButton.PLAY: MediaAction.PLAY,
            SystemMediaTransportControlsButton.PAUSE: MediaAction.PAUSE,
            SystemMediaTransportControlsButton.STOP: MediaAction.STOP,
            SystemMediaTransportControlsButton.NEXT: MediaAction.NEXT,
            SystemMediaTransportControlsButton.PREVIOUS: MediaAction.PREVIOUS,
        }

        action = action_map.get(button)
        if action and self._on_media_key:
            logger.debug("SMTC: Media key → %s", action.name)
            try:
                self._on_media_key(action)
            except Exception:
                logger.exception("SMTC: Error in media key callback")
