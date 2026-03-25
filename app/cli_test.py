"""
cli_test.py — CLI tool for testing the Bluetooth Media Bridge engine.

Usage:
    # Full mode: start bt_bridge.exe + connect IPC
    python -m app.cli_test

    # IPC-only mode: connect to already-running bt_bridge.exe
    python -m app.cli_test --ipc-only

    # Custom build directory
    python -m app.cli_test --build-dir "C:\\path\\to\\build"

Commands (type and press Enter):
    play, pause, stop, next, prev
    vol+, vol-
    meta        — request metadata refresh
    state       — print current engine state
    quit / q    — shutdown
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import datetime
from pathlib import Path

# Ensure the parent package is importable when run as `python -m app.cli_test`
if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.bridge_engine import (
    BridgeEngine,
    BridgeState,
    ConnectionState,
    MediaMetadata,
    PlaybackStatus,
)


def _ts() -> str:
    return datetime.now().strftime("%H:%M:%S.%f")[:-3]


def make_engine(args: argparse.Namespace) -> BridgeEngine:
    build_dir = Path(args.build_dir) if args.build_dir else None
    return BridgeEngine(
        build_dir=build_dir,
        on_log=lambda line: print(f"  [{_ts()}] [exe] {line}"),
    )


def register_display_handlers(engine: BridgeEngine) -> None:
    """Hook up engine events to console output."""

    def on_state(state: ConnectionState) -> None:
        print(f"  [{_ts()}] STATE → {state.name}")

    def on_metadata(meta: MediaMetadata) -> None:
        print(f"  [{_ts()}] TRACK → {meta.summary()}")
        if meta.album:
            print(f"           album: {meta.album}")
        if meta.genre:
            print(f"           genre: {meta.genre}")

    def on_playback(status: PlaybackStatus) -> None:
        icon = {"playing": "▶", "paused": "⏸", "stopped": "⏹"}.get(status.value, "?")
        print(f"  [{_ts()}] {icon} {status.value}")

    def on_volume(percent: int) -> None:
        bar = "█" * (percent // 5) + "░" * (20 - percent // 5)
        print(f"  [{_ts()}] VOL [{bar}] {percent}%")

    def on_cover_art(path: Path) -> None:
        size_kb = path.stat().st_size / 1024
        print(f"  [{_ts()}] COVER ART → {path.name} ({size_kb:.1f} KB)")

    def on_stream_started() -> None:
        print(f"  [{_ts()}] 🔊 Audio stream started")

    def on_stream_stopped() -> None:
        print(f"  [{_ts()}] 🔇 Audio stream stopped")

    def on_exit(code: int | None) -> None:
        print(f"  [{_ts()}] ⚠ bt_bridge.exe exited (code={code})")

    engine.on("state_changed", on_state)
    engine.on("metadata", on_metadata)
    engine.on("playback", on_playback)
    engine.on("volume", on_volume)
    engine.on("cover_art", on_cover_art)
    engine.on("stream_started", on_stream_started)
    engine.on("stream_stopped", on_stream_stopped)
    engine.on("process_exit", on_exit)


def print_state(engine: BridgeEngine) -> None:
    s = engine.state
    print(f"\n  === Bridge State ===")
    print(f"  Connection : {s.connection.name}")
    print(f"  Playback   : {s.playback.value}")
    print(f"  Local addr : {s.local_addr or '(unknown)'}")
    print(f"  Device addr: {s.device_addr or '(none)'}")
    print(f"  Track      : {s.metadata.summary()}")
    print(f"  Volume     : {s.volume_percent}%")
    print(f"  Cover art  : {s.cover_art_path or '(none)'}")
    print()


COMMANDS = {
    "play":  lambda e: e.play(),
    "pause": lambda e: e.pause(),
    "stop":  lambda e: e.stop_playback(),
    "next":  lambda e: e.next_track(),
    "prev":  lambda e: e.prev_track(),
    "vol+":  lambda e: e.volume_up(),
    "vol-":  lambda e: e.volume_down(),
    "meta":  lambda e: e.request_metadata(),
}


async def input_loop(engine: BridgeEngine) -> None:
    """Read commands from stdin asynchronously."""
    loop = asyncio.get_event_loop()
    while True:
        try:
            line = await loop.run_in_executor(None, sys.stdin.readline)
        except EOFError:
            break
        cmd = line.strip().lower()
        if not cmd:
            continue
        if cmd in ("quit", "q", "exit"):
            break
        if cmd == "state":
            print_state(engine)
            continue
        if cmd == "help":
            print("  Commands: play, pause, stop, next, prev, vol+, vol-, meta, state, quit")
            continue
        action = COMMANDS.get(cmd)
        if action:
            await action(engine)
        else:
            print(f"  Unknown command: '{cmd}'. Type 'help' for commands.")


async def main() -> None:
    parser = argparse.ArgumentParser(description="Bluetooth Media Bridge CLI Test")
    parser.add_argument(
        "--ipc-only",
        action="store_true",
        help="Connect to an already-running bt_bridge.exe (don't launch it)",
    )
    parser.add_argument(
        "--build-dir",
        type=str,
        default=None,
        help="Path to bt_bridge build directory",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable debug logging",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    engine = make_engine(args)
    register_display_handlers(engine)

    print("=" * 50)
    print("  Bluetooth Media Bridge — CLI Test")
    print("=" * 50)

    try:
        if args.ipc_only:
            print("  Mode: IPC-only (connecting to existing bt_bridge.exe)")
            await engine.connect_ipc_only()
        else:
            print(f"  Mode: Full (launching bt_bridge.exe)")
            print(f"  Build dir: {engine.build_dir}")
            await engine.start()

        print("  Ready! Type 'help' for commands.\n")
        await input_loop(engine)

    except FileNotFoundError as e:
        print(f"\n  ERROR: {e}")
        print("  Make sure bt_bridge.exe is built. See CONTEXT.md §7.")
    except ConnectionError as e:
        print(f"\n  ERROR: {e}")
        print("  Is bt_bridge.exe running and IPC server listening on port 9876?")
    except KeyboardInterrupt:
        pass
    finally:
        print("\n  Shutting down...")
        await engine.stop()
        print("  Done.")


if __name__ == "__main__":
    asyncio.run(main())
