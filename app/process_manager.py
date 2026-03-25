"""
process_manager.py — bt_bridge.exe subprocess lifecycle management.

Responsibilities:
  - Start / stop / restart bt_bridge.exe
  - Capture stdout/stderr via async readers, forward to callbacks
  - Monitor process health, emit events on unexpected exit
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
from enum import Enum, auto
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)


class ProcessState(Enum):
    STOPPED = auto()
    STARTING = auto()
    RUNNING = auto()
    STOPPING = auto()


# Default: project_root / bluetooth_bridge / btstack / port / windows-winusb / build
_DEFAULT_BUILD_DIR = (
    Path(__file__).resolve().parent.parent
    / "bluetooth_bridge" / "btstack" / "port" / "windows-winusb" / "build"
)


class ProcessManager:
    """Manage the bt_bridge.exe child process."""

    def __init__(
        self,
        build_dir: Optional[Path] = None,
        exe_name: str = "bt_bridge.exe",
        on_log: Optional[Callable[[str], None]] = None,
        on_exit: Optional[Callable[[int | None], None]] = None,
    ) -> None:
        self.build_dir = Path(build_dir) if build_dir else _DEFAULT_BUILD_DIR
        self.exe_path = self.build_dir / exe_name
        self._on_log = on_log
        self._on_exit = on_exit
        self._process: Optional[asyncio.subprocess.Process] = None
        self._state = ProcessState.STOPPED
        self._monitor_task: Optional[asyncio.Task] = None
        self._reader_tasks: list[asyncio.Task] = []

    # -- public properties ---------------------------------------------------

    @property
    def state(self) -> ProcessState:
        return self._state

    @property
    def pid(self) -> Optional[int]:
        return self._process.pid if self._process else None

    # -- lifecycle -----------------------------------------------------------

    async def start(self) -> None:
        """Launch bt_bridge.exe. Raises if already running or exe missing."""
        if self._state in (ProcessState.RUNNING, ProcessState.STARTING):
            logger.warning("Process already running (pid=%s)", self.pid)
            return

        if not self.exe_path.is_file():
            raise FileNotFoundError(f"Executable not found: {self.exe_path}")

        self._state = ProcessState.STARTING
        logger.info("Starting %s (cwd=%s)", self.exe_path.name, self.build_dir)

        self._process = await asyncio.create_subprocess_exec(
            str(self.exe_path),
            cwd=str(self.build_dir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            # On Windows, CREATE_NEW_PROCESS_GROUP allows graceful termination
            creationflags=(
                getattr(signal, "CREATE_NEW_PROCESS_GROUP", 0)
                if os.name == "nt"
                else 0
            ),
        )

        self._state = ProcessState.RUNNING
        logger.info("Process started (pid=%s)", self._process.pid)

        # Spawn log readers and health monitor
        self._reader_tasks = [
            asyncio.create_task(self._read_stream(self._process.stdout, "stdout")),
            asyncio.create_task(self._read_stream(self._process.stderr, "stderr")),
        ]
        self._monitor_task = asyncio.create_task(self._monitor())

    async def stop(self, timeout: float = 5.0) -> None:
        """Gracefully stop the process. Falls back to kill after timeout."""
        if self._state == ProcessState.STOPPED or self._process is None:
            return

        self._state = ProcessState.STOPPING
        logger.info("Stopping process (pid=%s)", self._process.pid)

        self._process.terminate()
        try:
            await asyncio.wait_for(self._process.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            logger.warning("Process did not exit in %.1fs, killing", timeout)
            self._process.kill()
            await self._process.wait()

        await self._cleanup()

    async def restart(self, timeout: float = 5.0) -> None:
        """Stop then start."""
        await self.stop(timeout=timeout)
        await self.start()

    # -- internals -----------------------------------------------------------

    async def _read_stream(
        self, stream: Optional[asyncio.StreamReader], label: str
    ) -> None:
        """Read lines from stdout/stderr and forward to the log callback."""
        if stream is None:
            return
        try:
            while True:
                line_bytes = await stream.readline()
                if not line_bytes:
                    break
                line = line_bytes.decode("utf-8", errors="replace").rstrip()
                if line:
                    logger.debug("[%s] %s", label, line)
                    if self._on_log:
                        self._on_log(line)
        except asyncio.CancelledError:
            pass

    async def _monitor(self) -> None:
        """Wait for the process to exit and emit the exit event."""
        if self._process is None:
            return
        try:
            returncode = await self._process.wait()
        except asyncio.CancelledError:
            return

        if self._state == ProcessState.STOPPING:
            # Expected shutdown — don't treat as crash
            await self._cleanup()
            return

        logger.warning("Process exited unexpectedly (code=%s)", returncode)
        await self._cleanup()
        if self._on_exit:
            self._on_exit(returncode)

    async def _cleanup(self) -> None:
        """Cancel reader tasks and reset state."""
        for task in self._reader_tasks:
            task.cancel()
        self._reader_tasks.clear()
        if self._monitor_task and not self._monitor_task.done():
            self._monitor_task.cancel()
        self._monitor_task = None
        self._process = None
        self._state = ProcessState.STOPPED
