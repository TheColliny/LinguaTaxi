"""System tray icon, overlay window, and WebSocket connection management."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from linguataxi.constants import APP_DIR, IS_WIN, DICTATION_PORT
from linguataxi.settings import (
    load_settings, get_setting, set_setting,
)
from linguataxi.dictation.injection import _inject_text
from linguataxi.dictation import hotkeys as _hotkeys

_log: logging.Logger = logging.getLogger("tray")

# ── Paths ──

SERVER_PY: Path = APP_DIR / "server.py"
DEFAULT_TRANSCRIPTS: Path = Path.home() / "Documents" / "LinguaTaxi Transcripts"

# ── Tray icon images (initialised lazily via _init_icons) ──

ICON_GREY: Any | None = None
ICON_GREEN: Any | None = None
ICON_RED: Any | None = None


def _make_icon(color: tuple[int, int, int, int]) -> Any:
    """Generate a 64x64 tray icon with a coloured circle and 'LT' label."""
    from PIL import Image, ImageDraw

    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse([8, 8, 56, 56], fill=color)
    try:
        from PIL import ImageFont
        font = ImageFont.truetype("arial.ttf", 20)
    except Exception:
        from PIL import ImageFont
        font = ImageFont.load_default()
    draw.text((32, 32), "LT", fill="white", anchor="mm", font=font)
    return img


def _init_icons() -> None:
    """Create the three tray icon colour variants."""
    global ICON_GREY, ICON_GREEN, ICON_RED
    ICON_GREY = _make_icon((128, 128, 128, 255))
    ICON_GREEN = _make_icon((76, 175, 80, 255))
    ICON_RED = _make_icon((244, 67, 54, 255))


# ── Server auto-start ──

_server_proc: subprocess.Popen[bytes] | None = None
_server_job: Any | None = None  # Windows Job Object handle


def _find_python() -> str:
    """Find python executable -- same logic as launcher."""
    venv_python: Path = APP_DIR / "venv" / "Scripts" / "python.exe"
    if venv_python.exists():
        return str(venv_python)
    venv_python_unix: Path = APP_DIR / "venv" / "bin" / "python"
    if venv_python_unix.exists():
        return str(venv_python_unix)
    return sys.executable


def _is_server_running() -> bool:
    """Check if the dictation server is already responding."""
    import urllib.request

    try:
        r = urllib.request.urlopen(
            f"http://localhost:{DICTATION_PORT}/api/dictation-config", timeout=2,
        )
        return r.status == 200
    except Exception:
        return False


def _create_win_job(proc: subprocess.Popen[bytes]) -> Any | None:
    """Create a Windows Job Object that auto-kills the server when we die."""
    if not IS_WIN:
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
        # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        info.BasicLimitInformation.LimitFlags = 0x2000
        k32.SetInformationJobObject(
            job, 9, ctypes.byref(info), ctypes.sizeof(info),
        )
        h = k32.OpenProcess(0x1FFFFF, False, proc.pid)  # PROCESS_ALL_ACCESS
        if h:
            k32.AssignProcessToJobObject(job, h)
            k32.CloseHandle(h)
        return job
    except Exception as exc:
        _log.debug("Failed to create Win32 Job Object: %s", exc)
        return None


def start_server_if_needed() -> bool:
    """Start the LinguaTaxi server subprocess if it is not already running."""
    global _server_proc, _server_job

    if _is_server_running():
        return True

    python: str = _find_python()
    cmd: list[str] = [python, str(SERVER_PY)]

    settings: dict[str, Any] = load_settings()
    backend: str = settings.get("backend", "auto")
    if backend and backend != "auto":
        cmd.extend(["--backend", backend])

    source_indices: list[int] = settings.get("source_indices", [-1])
    if source_indices:
        cmd.extend(["--sources", ",".join(str(i) for i in source_indices)])

    tdir: str = settings.get("transcripts_dir", str(DEFAULT_TRANSCRIPTS))
    if tdir:
        cmd.extend(["--transcripts-dir", tdir])

    models_dir: Path = APP_DIR / "models"
    cmd.extend(["--models-dir", str(models_dir)])

    try:
        kwargs: dict[str, Any] = {}
        if IS_WIN:
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

        _server_proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            cwd=str(APP_DIR),
            env={**os.environ, "PYTHONUNBUFFERED": "1",
                 "LINGUATAXI_TRANSCRIPTS": tdir},
            **kwargs,
        )
        _server_job = _create_win_job(_server_proc)
        # Wait for server to become ready
        for _ in range(30):
            time.sleep(1)
            if _is_server_running():
                return True
        return False
    except Exception as exc:
        _log.error("Failed to start server: %s", exc)
        return False


def _request_server_shutdown() -> bool:
    """Ask the server to shut down gracefully via HTTP."""
    import urllib.request

    try:
        req = urllib.request.Request(
            f"http://localhost:{DICTATION_PORT}/api/shutdown",
            method="POST", data=b"",
            headers={"Content-Length": "0"},
        )
        urllib.request.urlopen(req, timeout=3)
        return True
    except Exception:
        return False


def stop_server() -> None:
    """Stop the server subprocess and clean up."""
    global _server_proc, _server_job

    if _server_proc:
        pid: int | None = _server_proc.pid
        _request_server_shutdown()
        try:
            _server_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                _server_proc.kill()
            except Exception as exc:
                _log.debug("kill() after timeout failed: %s", exc)
        except Exception:
            try:
                _server_proc.kill()
            except Exception as exc:
                _log.debug("kill() fallback failed: %s", exc)
        # Kill any orphan child processes (Windows process tree)
        if IS_WIN and pid:
            try:
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(pid)],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    timeout=5, creationflags=subprocess.CREATE_NO_WINDOW,
                )
            except Exception as exc:
                _log.debug("taskkill orphan cleanup failed: %s", exc)
        _server_proc = None
        _server_job = None


# ── Tray app state ──

_tray_icon: Any | None = None
_ws: Any | None = None
_ws_connected: bool = False


# ── Overlay window ──

_overlay_tk: Any | None = None
_overlay_win: Any | None = None
_overlay_visible: bool = False
_overlay_dot: Any | None = None
_overlay_dot_id: int | None = None
_pulse_on: list[bool] = [True]


def _ensure_overlay() -> None:
    """Create the overlay window (once, on the overlay thread)."""
    global _overlay_tk, _overlay_win, _overlay_dot, _overlay_dot_id

    if _overlay_tk is not None:
        return

    import tkinter as tk

    _overlay_tk = tk.Tk()
    _overlay_tk.withdraw()

    _overlay_win = tk.Toplevel(_overlay_tk)
    _overlay_win.withdraw()
    _overlay_win.overrideredirect(True)
    _overlay_win.attributes("-topmost", True)
    _overlay_win.attributes("-alpha", 0.85)
    _overlay_win.configure(bg="#1a1a2e")

    if IS_WIN:
        import ctypes
        hwnd: int = int(_overlay_win.frame(), 16)
        WS_EX_NOACTIVATE: int = 0x08000000
        WS_EX_TOOLWINDOW: int = 0x00000080
        GWL_EXSTYLE: int = -20
        ctypes.windll.user32.SetWindowLongW(
            hwnd, GWL_EXSTYLE,
            ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW,
        )

    frame = tk.Frame(_overlay_win, bg="#1a1a2e", padx=12, pady=6)
    frame.pack()

    _overlay_dot = tk.Canvas(
        frame, width=12, height=12, bg="#1a1a2e", highlightthickness=0,
    )
    _overlay_dot_id = _overlay_dot.create_oval(2, 2, 10, 10, fill="#f44336", outline="")
    _overlay_dot.pack(side="left", padx=(0, 6))

    label = tk.Label(
        frame, text="Listening...", fg="#e0e0e0", bg="#1a1a2e",
        font=("Segoe UI", 11, "bold"),
    )
    label.pack(side="left")

    _overlay_win.update_idletasks()
    w: int = _overlay_win.winfo_reqwidth() + 24
    h: int = _overlay_win.winfo_reqheight() + 12

    screen_w: int = _overlay_win.winfo_screenwidth()
    screen_h: int = _overlay_win.winfo_screenheight()
    x: int = screen_w - w - 20
    y: int = screen_h - h - 60
    _overlay_win.geometry(f"{w}x{h}+{x}+{y}")

    _pulse_dot()


def _pulse_dot() -> None:
    """Animate the red dot by cycling between bright and dim."""
    if not _overlay_dot or not _overlay_tk:
        return
    _pulse_on[0] = not _pulse_on[0]
    color: str = "#f44336" if _pulse_on[0] else "#7a1a14"
    _overlay_dot.itemconfig(_overlay_dot_id, fill=color)
    _overlay_tk.after(600, _pulse_dot)


def _show_overlay() -> None:
    """Show the 'Listening...' overlay window."""
    global _overlay_visible
    if _overlay_visible:
        return
    _overlay_visible = True
    if _overlay_win:
        _overlay_tk.after(0, lambda: (_overlay_win.deiconify(), _overlay_win.lift()))


def _hide_overlay() -> None:
    """Hide the 'Listening...' overlay window."""
    global _overlay_visible
    _overlay_visible = False
    if _overlay_win:
        _overlay_tk.after(0, lambda: _overlay_win.withdraw() if _overlay_win else None)


def _run_overlay_mainloop() -> None:
    """Run the tkinter mainloop for the overlay on its own thread."""
    _ensure_overlay()
    if _overlay_tk:
        _overlay_tk.mainloop()


# ── Tray icon ──


def _update_tray_icon(state: str) -> None:
    """Update tray icon: 'disconnected', 'idle', 'active'."""
    if not _tray_icon:
        return
    if state == "disconnected":
        _tray_icon.icon = ICON_GREY
        _tray_icon.title = "LinguaTaxi Dictation \u2014 Disconnected"
    elif state == "idle":
        _tray_icon.icon = ICON_GREEN
        _tray_icon.title = "LinguaTaxi Dictation \u2014 Ready"
    elif state == "active":
        _tray_icon.icon = ICON_RED
        _tray_icon.title = "LinguaTaxi Dictation \u2014 Listening..."


# ── Tray menu ──


def _build_menu() -> Any:
    """Build the pystray menu for the tray icon."""
    import pystray

    settings: dict[str, Any] = load_settings()
    hotkey: dict[str, str] | None = settings.get("global_dictation_hotkey", None)
    hotkey_display: str = hotkey["display"] if hotkey else "Not set"

    return pystray.Menu(
        pystray.MenuItem(f"PTT Key: {hotkey_display}", _on_set_hotkey),
        pystray.MenuItem("Set Global Hotkey...", _on_set_hotkey),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem(
            "Mode: Hold", _on_set_hold,
            checked=lambda item: get_setting("global_dictation_mode", "hold") == "hold",
        ),
        pystray.MenuItem(
            "Mode: Toggle", _on_set_toggle,
            checked=lambda item: get_setting("global_dictation_mode", "hold") == "toggle",
        ),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit", _on_quit),
    )


def _on_set_hotkey(icon: Any, item: Any) -> None:
    """Open the hotkey configuration dialog in a daemon thread."""
    threading.Thread(target=_hotkeys._show_hotkey_dialog, daemon=True).start()


def _on_set_hold(icon: Any, item: Any) -> None:
    """Switch to hold-to-talk mode."""
    set_setting("global_dictation_mode", "hold")
    _hotkeys._reload_hotkey_cache()
    icon.update_menu()


def _on_set_toggle(icon: Any, item: Any) -> None:
    """Switch to toggle mode."""
    set_setting("global_dictation_mode", "toggle")
    _hotkeys._reload_hotkey_cache()
    icon.update_menu()


def _on_quit(icon: Any, item: Any) -> None:
    """Clean up and exit the tray application."""

    def _force_exit() -> None:
        time.sleep(15)
        os._exit(1)
    threading.Thread(target=_force_exit, daemon=True).start()

    if _hotkeys._grace_timer:
        _hotkeys._grace_timer.cancel()
    if _hotkeys._hotkey_listener:
        _hotkeys._hotkey_listener.stop()
    if _ws:
        try:
            _ws.close()
        except Exception as exc:
            _log.debug("WS close on quit failed: %s", exc)
    if _overlay_tk:
        _overlay_tk.after(0, _overlay_tk.quit)
    stop_server()
    try:
        icon.stop()
    except Exception as exc:
        _log.debug("icon.stop() failed: %s", exc)
    os._exit(0)


# ── WebSocket ──

_server_confirmed_active: bool = False


def _on_ws_open(ws: Any) -> None:
    """Handle WebSocket open event."""
    global _ws_connected
    _ws_connected = True
    _update_tray_icon("idle")
    _log.info("Websocket connected")


def _on_ws_message(ws: Any, message: str) -> None:
    """Handle incoming WebSocket messages (final text, status, etc.)."""
    global _server_confirmed_active

    try:
        msg: dict[str, Any] = json.loads(message)
    except Exception:
        return

    _log.debug("WS recv: %s | %s", msg.get("type"), str(message)[:200])

    if msg.get("type") == "final" and msg.get("text"):
        _log.info("FINAL text received: %s", msg["text"][:100])
        _inject_text(msg["text"])
    elif msg.get("type") == "interim" and msg.get("text"):
        _log.debug("INTERIM: %s", msg["text"][:80])
    elif msg.get("type") == "dictation_active":
        active: bool = msg.get("active", False)
        _hotkeys._dictation_active = active
        _server_confirmed_active = active
        _hotkeys._server_confirmed_active = active
        _log.info("Server confirmed dictation_active=%s", active)
        _update_tray_icon("active" if active else "idle")
        if active:
            _show_overlay()
        else:
            _hide_overlay()
    elif msg.get("type") == "status":
        if msg.get("dictation_active") is not None:
            active = msg["dictation_active"]
            _hotkeys._dictation_active = active
            _server_confirmed_active = active
            _hotkeys._server_confirmed_active = active
            _update_tray_icon("active" if active else "idle")
            if not active:
                _hide_overlay()


def _on_ws_close(ws: Any, close_status_code: int | None, close_msg: str | None) -> None:
    """Handle WebSocket close event."""
    global _ws_connected
    _ws_connected = False
    _update_tray_icon("disconnected")


def _on_ws_error(ws: Any, error: Exception) -> None:
    """Handle WebSocket error event."""
    _log.error("WS error: %s", error)


def _send_ws(msg_dict: dict[str, Any]) -> bool:
    """Send a JSON message to the server WebSocket. Returns True if sent."""
    if not _ws or not _ws_connected:
        _log.warning("_send_ws DROPPED (not connected): %s", msg_dict.get("type"))
        return False
    try:
        _ws.send(json.dumps(msg_dict))
        _log.info("_send_ws OK: %s", msg_dict)
        return True
    except Exception as exc:
        _log.error("_send_ws FAILED: %s", exc)
        return False


def _ws_loop() -> None:
    """Connect to the dictation WebSocket and handle messages."""
    import websocket
    global _ws_connected, _ws

    url: str = f"ws://localhost:{DICTATION_PORT}/ws"

    while True:
        try:
            _ws = websocket.WebSocketApp(
                url,
                on_open=_on_ws_open,
                on_message=_on_ws_message,
                on_close=_on_ws_close,
                on_error=_on_ws_error,
            )
            _ws.run_forever(ping_interval=30, ping_timeout=10)
        except Exception as exc:
            _log.debug("WS loop exception: %s", exc)
        _ws_connected = False
        _update_tray_icon("disconnected")
        time.sleep(5)


def _reconnect_loop() -> None:
    """Periodically try to connect if server was not available at startup."""
    while True:
        time.sleep(10)
        if _ws_connected:
            return
        if _is_server_running():
            _update_tray_icon("idle")
            threading.Thread(target=_ws_loop, daemon=True).start()
            return


# ── Main entry ──


def run_tray() -> None:
    """Start the system tray icon and all background threads."""
    import pystray
    global _tray_icon

    _log.info("Initializing icons")
    _init_icons()

    _log.info("Creating pystray.Icon")
    _tray_icon = pystray.Icon(
        "LinguaTaxi Dictation",
        ICON_GREY,
        "LinguaTaxi Dictation \u2014 Starting...",
        menu=_build_menu(),
    )

    # Wire up hotkey callbacks now that _tray_icon exists
    _hotkeys.set_tray_callbacks(
        update_icon=_update_tray_icon,
        show_overlay=_show_overlay,
        hide_overlay=_hide_overlay,
        build_menu=_build_menu,
        tray_icon=_tray_icon,
    )

    def _startup(icon: Any) -> None:
        try:
            _log.info("_startup: setting visible=True")
            icon.visible = True
            _log.info("_startup: icon visible set OK")
        except Exception:
            _log.exception("_startup: icon.visible failed")

        try:
            icon.notify("LinguaTaxi Dictation is running", "Global Dictation")
        except Exception:
            _log.exception("_startup: icon.notify failed")

        _log.info("_startup: starting hotkey listener")
        _hotkeys._start_hotkey_listener()
        threading.Thread(target=_run_overlay_mainloop, daemon=True).start()

        _log.info("_startup: checking server")
        started: bool = start_server_if_needed()
        _log.info("_startup: server ready=%s", started)
        if started:
            _update_tray_icon("idle")
            threading.Thread(target=_ws_loop, daemon=True).start()
        else:
            _update_tray_icon("disconnected")
            threading.Thread(target=_reconnect_loop, daemon=True).start()
        _log.info("_startup: complete")

    _log.info("Calling icon.run()")
    _tray_icon.run(setup=_startup)
    _log.info("icon.run() returned (should not happen normally)")
