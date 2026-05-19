"""Server subprocess lifecycle management for LinguaTaxi launcher."""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Optional

if TYPE_CHECKING:
    import queue

logger = logging.getLogger(__name__)

IS_WIN = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"


def create_win_job(proc: subprocess.Popen) -> Optional[object]:
    """Create a Windows Job Object that auto-kills the child when we die.

    Returns the job handle, or ``None`` on failure / non-Windows.
    """
    if not IS_WIN or not proc:
        return None
    try:
        import ctypes
        from ctypes import wintypes

        k32 = ctypes.windll.kernel32

        class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.POINTER(ctypes.c_ulong)),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount", ctypes.c_uint64),
                ("WriteOperationCount", ctypes.c_uint64),
                ("OtherOperationCount", ctypes.c_uint64),
                ("ReadTransferCount", ctypes.c_uint64),
                ("WriteTransferCount", ctypes.c_uint64),
                ("OtherTransferCount", ctypes.c_uint64),
            ]

        class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
                ("IoInfo", IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        job = k32.CreateJobObjectW(None, None)
        if not job:
            return None
        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        k32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info))
        h = k32.OpenProcess(0x1FFFFF, False, proc.pid)  # PROCESS_ALL_ACCESS
        if h:
            k32.AssignProcessToJobObject(job, h)
            k32.CloseHandle(h)
        return job
    except Exception:
        logger.debug("Failed to create Windows Job Object", exc_info=True)
        return None


class ServerManager:
    """Manages the LinguaTaxi server subprocess lifecycle.

    Parameters
    ----------
    app_dir:
        Root directory containing ``server.py`` and ``venv/``.
    on_log:
        Callback ``(msg_type, data)`` pushed to the UI log queue.
        *msg_type* is one of ``"output"``, ``"backend"``, ``"ready"``,
        ``"stopped"``.
    """

    def __init__(self, app_dir: Path, on_log: Callable[[str, str], None]) -> None:
        self.app_dir = app_dir
        self._on_log = on_log
        self.server_proc: Optional[subprocess.Popen] = None
        self._server_job: Optional[object] = None
        self.running: bool = False
        self.ready: bool = False

    # ── Python / command helpers ──────────────────────────────────────

    def find_python(self) -> str:
        """Find the Python executable for running scripts."""
        if IS_WIN:
            venv_py = self.app_dir / "venv" / "Scripts" / "python.exe"
        else:
            venv_py = self.app_dir / "venv" / "bin" / "python3"
        if venv_py.exists():
            return str(venv_py)
        return sys.executable

    def build_server_cmd(
        self,
        backend: str,
        source_indices: list[int],
        transcripts_dir: str,
    ) -> list[str]:
        """Construct the command-line to launch ``server.py``.

        Parameters
        ----------
        backend:
            Speech backend key (``"auto"``, ``"whisper"``, ``"vosk"``, etc.).
        source_indices:
            List of audio device indices (``-1`` = system default).
        transcripts_dir:
            Path to the transcript output directory.
        """
        python = self.find_python()
        server_py = self.app_dir / "server.py"
        cmd = [python, str(server_py)]

        if backend and backend != "auto":
            cmd.extend(["--backend", backend])

        if source_indices:
            cmd.extend(["--sources", ",".join(str(i) for i in source_indices)])

        if transcripts_dir:
            cmd.extend(["--transcripts-dir", transcripts_dir])

        models_dir = self.app_dir / "models"
        cmd.extend(["--models-dir", str(models_dir)])

        return cmd

    # ── Start / Stop ─────────────────────────────────────────────────

    def start(
        self,
        cmd: list[str],
        transcripts_dir: str,
        operator_port: int = 3001,
    ) -> None:
        """Launch the server subprocess.

        Raises
        ------
        FileNotFoundError
            If the Python interpreter cannot be found.
        """
        kwargs: dict = {}
        if IS_WIN:
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

        self.server_proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=1,
            universal_newlines=True,
            cwd=str(self.app_dir),
            env={
                **os.environ,
                "PYTHONUNBUFFERED": "1",
                "LINGUATAXI_TRANSCRIPTS": transcripts_dir,
            },
            **kwargs,
        )
        self._server_job = create_win_job(self.server_proc)
        self.running = True
        self.ready = False

        # Start log reader thread
        t = threading.Thread(target=self._read_server_output, daemon=True)
        t.start()

        # Start HTTP readiness check (backup for log detection)
        threading.Thread(
            target=self._check_server_readiness,
            args=(operator_port,),
            daemon=True,
        ).start()

    def stop(self, operator_port: int = 3001) -> None:
        """Gracefully stop the server, falling back to forced kill."""
        if not self.running or not self.server_proc:
            return

        pid = self.server_proc.pid

        # Try graceful shutdown via HTTP first (releases mic cleanly)
        try:
            req = urllib.request.Request(
                f"http://localhost:{operator_port}/api/shutdown",
                method="POST",
                data=b"",
                headers={"Content-Length": "0"},
            )
            urllib.request.urlopen(req, timeout=2)
        except Exception:
            logger.debug("Graceful HTTP shutdown request failed", exc_info=True)

        try:
            try:
                self.server_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                if IS_WIN:
                    self.server_proc.terminate()
                else:
                    self.server_proc.send_signal(signal.SIGINT)
                try:
                    self.server_proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    self.server_proc.kill()
                    self.server_proc.wait(timeout=3)
        except Exception:
            logger.warning("Error during server stop sequence", exc_info=True)
            try:
                self.server_proc.kill()
            except Exception:
                logger.debug("Force-kill also failed", exc_info=True)

        # Kill any orphan child processes (Windows process tree)
        if IS_WIN and pid:
            try:
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(pid)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=5,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
            except Exception:
                logger.debug("taskkill orphan cleanup failed", exc_info=True)

        self.running = False
        self.ready = False
        self.server_proc = None
        self._server_job = None

    # ── Background threads ───────────────────────────────────────────

    def _read_server_output(self) -> None:
        """Read server stdout in a background thread and post log events."""
        proc = self.server_proc
        try:
            for line in iter(proc.stdout.readline, ""):
                if not line or not self.running:
                    break
                line = line.rstrip("\n\r")
                if line:
                    self._on_log("output", line)

                    # Detect backend info
                    if "Backend:" in line or "backend:" in line.lower():
                        self._on_log("backend", line)
                    # Detect ready state
                    if "Ctrl+C to stop" in line or "Uvicorn running" in line:
                        self._on_log("ready", True)
        except Exception:
            logger.debug("Server output reader exception", exc_info=True)
        finally:
            # Server exited -- include PID to avoid race with rapid stop/start
            if self.running:
                self._on_log("stopped", proc.pid)

    def _check_server_readiness(self, port: int) -> None:
        """Background HTTP check to confirm server is accepting connections."""
        for _ in range(60):  # Up to 60 seconds
            if not self.running or self.ready:
                return
            try:
                urllib.request.urlopen(f"http://localhost:{port}", timeout=2)
                self._on_log("ready", True)
                return
            except Exception:
                time.sleep(1)
