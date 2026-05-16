#!/usr/bin/env python3
"""
LinguaTaxi — Global Dictation Tray App
System-wide push-to-talk speech-to-text via system tray.
Connects to the LinguaTaxi server's dictation WebSocket and injects
transcribed text into the focused application using OS keystroke simulation.
"""

import atexit, json, logging, os, subprocess, sys, threading, time
from pathlib import Path

# ── Paths (same as launcher.pyw) ──

IS_WIN = sys.platform == "win32"

if os.environ.get("LINGUATAXI_APP_DIR"):
    APP_DIR = Path(os.environ["LINGUATAXI_APP_DIR"])
elif getattr(sys, "frozen", False):
    APP_DIR = Path(sys.executable).parent
else:
    APP_DIR = Path(__file__).resolve().parent

SERVER_PY = APP_DIR / "server.py"

if IS_WIN:
    SETTINGS_DIR = Path(os.environ.get("APPDATA", Path.home())) / "LinguaTaxi"
else:
    SETTINGS_DIR = Path.home() / ".config" / "linguataxi"

SETTINGS_FILE = SETTINGS_DIR / "launcher_settings.json"

DEFAULT_TRANSCRIPTS = Path.home() / "Documents" / "LinguaTaxi Transcripts"

DICTATION_PORT = 3005
GRACE_MS = 750

# ── Settings ──

def load_settings():
    try:
        if SETTINGS_FILE.exists():
            with open(SETTINGS_FILE, "r") as f:
                return json.load(f)
    except Exception:
        pass
    return {}

def save_settings(cfg):
    try:
        SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
        with open(SETTINGS_FILE, "w") as f:
            json.dump(cfg, f, indent=2)
    except Exception:
        pass

def get_setting(key, default=None):
    return load_settings().get(key, default)

def set_setting(key, value):
    cfg = load_settings()
    cfg[key] = value
    save_settings(cfg)

# ── Tray Icon Images ──

def _make_icon(color):
    """Generate a 64x64 tray icon with a colored circle."""
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse([8, 8, 56, 56], fill=color)
    # "LT" text in center
    try:
        from PIL import ImageFont
        font = ImageFont.truetype("arial.ttf", 20)
    except Exception:
        font = ImageFont.load_default()
    draw.text((32, 32), "LT", fill="white", anchor="mm", font=font)
    return img

ICON_GREY = None
ICON_GREEN = None
ICON_RED = None

def _init_icons():
    global ICON_GREY, ICON_GREEN, ICON_RED
    ICON_GREY = _make_icon((128, 128, 128, 255))
    ICON_GREEN = _make_icon((76, 175, 80, 255))
    ICON_RED = _make_icon((244, 67, 54, 255))

# ── Server Auto-Start ──

_server_proc = None
_server_job = None  # Windows Job Object handle

def _find_python():
    """Find python executable — same logic as launcher."""
    venv_python = APP_DIR / "venv" / "Scripts" / "python.exe"
    if venv_python.exists():
        return str(venv_python)
    venv_python_unix = APP_DIR / "venv" / "bin" / "python"
    if venv_python_unix.exists():
        return str(venv_python_unix)
    return sys.executable

def _is_server_running():
    """Check if the dictation server is already responding."""
    import urllib.request
    try:
        r = urllib.request.urlopen(f"http://localhost:{DICTATION_PORT}/api/dictation-config", timeout=2)
        return r.status == 200
    except Exception:
        return False

def _create_win_job(proc):
    """Create a Windows Job Object that auto-kills the server when we die."""
    if not IS_WIN:
        return None
    try:
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.windll.kernel32

        class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64),
                        ("PerJobUserTimeLimit", ctypes.c_int64),
                        ("LimitFlags", wintypes.DWORD),
                        ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t),
                        ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.POINTER(ctypes.c_ulong)),
                        ("PriorityClass", wintypes.DWORD),
                        ("SchedulingClass", wintypes.DWORD)]

        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [("ReadOperationCount", ctypes.c_uint64),
                        ("WriteOperationCount", ctypes.c_uint64),
                        ("OtherOperationCount", ctypes.c_uint64),
                        ("ReadTransferCount", ctypes.c_uint64),
                        ("WriteTransferCount", ctypes.c_uint64),
                        ("OtherTransferCount", ctypes.c_uint64)]

        class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
                        ("IoInfo", IO_COUNTERS),
                        ("ProcessMemoryLimit", ctypes.c_size_t),
                        ("JobMemoryLimit", ctypes.c_size_t),
                        ("PeakProcessMemoryUsed", ctypes.c_size_t),
                        ("PeakJobMemoryUsed", ctypes.c_size_t)]

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
        return None

def start_server_if_needed():
    global _server_proc, _server_job
    if _is_server_running():
        return True

    python = _find_python()
    cmd = [python, str(SERVER_PY)]

    settings = load_settings()
    backend = settings.get("backend", "auto")
    if backend and backend != "auto":
        cmd.extend(["--backend", backend])

    source_indices = settings.get("source_indices", [-1])
    if source_indices:
        cmd.extend(["--sources", ",".join(str(i) for i in source_indices)])

    tdir = settings.get("transcripts_dir", str(DEFAULT_TRANSCRIPTS))
    if tdir:
        cmd.extend(["--transcripts-dir", tdir])

    models_dir = APP_DIR / "models"
    cmd.extend(["--models-dir", str(models_dir)])

    try:
        kwargs = {}
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
    except Exception:
        return False

def _request_server_shutdown():
    """Ask the server to shut down gracefully via HTTP."""
    import urllib.request
    try:
        req = urllib.request.Request(
            f"http://localhost:{DICTATION_PORT}/api/shutdown", method="POST",
            data=b"", headers={"Content-Length": "0"})
        urllib.request.urlopen(req, timeout=3)
        return True
    except Exception:
        return False

def stop_server():
    global _server_proc, _server_job
    if _server_proc:
        pid = _server_proc.pid
        _request_server_shutdown()
        try:
            _server_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                _server_proc.kill()
            except Exception:
                pass
        except Exception:
            try:
                _server_proc.kill()
            except Exception:
                pass
        # Kill any orphan child processes (Windows process tree)
        if IS_WIN and pid:
            try:
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(pid)],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    timeout=5, creationflags=subprocess.CREATE_NO_WINDOW)
            except Exception:
                pass
        _server_proc = None
        _server_job = None

# ── Tray App ──

_tray_icon = None
_ws = None
_ws_connected = False
_dictation_active = False

def _build_menu():
    import pystray
    settings = load_settings()
    mode = settings.get("global_dictation_mode", "hold")
    hotkey = settings.get("global_dictation_hotkey", None)
    hotkey_display = hotkey["display"] if hotkey else "Not set"

    return pystray.Menu(
        pystray.MenuItem(f"PTT Key: {hotkey_display}", _on_set_hotkey),
        pystray.MenuItem("Set Global Hotkey...", _on_set_hotkey),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Mode: Hold", _on_set_hold,
                         checked=lambda item: get_setting("global_dictation_mode", "hold") == "hold"),
        pystray.MenuItem("Mode: Toggle", _on_set_toggle,
                         checked=lambda item: get_setting("global_dictation_mode", "hold") == "toggle"),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit", _on_quit),
    )

def _on_set_hotkey(icon, item):
    threading.Thread(target=_show_hotkey_dialog, daemon=True).start()

def _on_set_hold(icon, item):
    set_setting("global_dictation_mode", "hold")
    _reload_hotkey_cache()
    icon.update_menu()

def _on_set_toggle(icon, item):
    set_setting("global_dictation_mode", "toggle")
    _reload_hotkey_cache()
    icon.update_menu()

def _on_quit(icon, item):
    global _grace_timer

    def _force_exit():
        time.sleep(15)
        os._exit(1)
    threading.Thread(target=_force_exit, daemon=True).start()

    if _grace_timer:
        _grace_timer.cancel()
    if _hotkey_listener:
        _hotkey_listener.stop()
    if _ws:
        try:
            _ws.close()
        except Exception:
            pass
    if _overlay_tk:
        _overlay_tk.after(0, _overlay_tk.quit)
    stop_server()
    try:
        icon.stop()
    except Exception:
        pass
    os._exit(0)

def _update_tray_icon(state):
    """Update tray icon: 'disconnected', 'idle', 'active'."""
    global _tray_icon
    if not _tray_icon:
        return
    if state == "disconnected":
        _tray_icon.icon = ICON_GREY
        _tray_icon.title = "LinguaTaxi Dictation — Disconnected"
    elif state == "idle":
        _tray_icon.icon = ICON_GREEN
        _tray_icon.title = "LinguaTaxi Dictation — Ready"
    elif state == "active":
        _tray_icon.icon = ICON_RED
        _tray_icon.title = "LinguaTaxi Dictation — Listening..."

def run_tray():
    import pystray
    import logging
    _log = logging.getLogger("tray")
    global _tray_icon

    _log.info("Initializing icons")
    _init_icons()

    _log.info("Creating pystray.Icon")
    _tray_icon = pystray.Icon(
        "LinguaTaxi Dictation",
        ICON_GREY,
        "LinguaTaxi Dictation — Starting...",
        menu=_build_menu(),
    )

    def _startup(icon):
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
        _start_hotkey_listener()
        threading.Thread(target=_run_overlay_mainloop, daemon=True).start()

        _log.info("_startup: checking server")
        started = start_server_if_needed()
        _log.info(f"_startup: server ready={started}")
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

def _reconnect_loop():
    """Periodically try to connect if server wasn't available at startup."""
    while True:
        time.sleep(10)
        if _ws_connected:
            return
        if _is_server_running():
            _update_tray_icon("idle")
            threading.Thread(target=_ws_loop, daemon=True).start()
            return

# ── WebSocket ──

def _on_ws_open(ws):
    global _ws_connected
    _ws_connected = True
    _update_tray_icon("idle")
    logging.getLogger("tray").info("Websocket connected")

_server_confirmed_active = False

def _on_ws_message(ws, message):
    _log = logging.getLogger("tray")
    global _server_confirmed_active
    try:
        msg = json.loads(message)
    except Exception:
        return

    _log.debug(f"WS recv: {msg.get('type')} | {str(message)[:200]}")

    if msg.get("type") == "final" and msg.get("text"):
        _log.info(f"FINAL text received: {msg['text'][:100]}")
        _inject_text(msg["text"])
    elif msg.get("type") == "interim" and msg.get("text"):
        _log.debug(f"INTERIM: {msg['text'][:80]}")
    elif msg.get("type") == "dictation_active":
        global _dictation_active
        active = msg.get("active", False)
        _dictation_active = active
        _server_confirmed_active = active
        _log.info(f"Server confirmed dictation_active={active}")
        _update_tray_icon("active" if active else "idle")
        if active:
            _show_overlay()
        else:
            _hide_overlay()
    elif msg.get("type") == "status":
        if msg.get("dictation_active") is not None:
            active = msg["dictation_active"]
            _dictation_active = active
            _server_confirmed_active = active
            _update_tray_icon("active" if active else "idle")
            if not active:
                _hide_overlay()

def _on_ws_close(ws, close_status_code, close_msg):
    global _ws_connected
    _ws_connected = False
    _update_tray_icon("disconnected")

def _on_ws_error(ws, error):
    logging.getLogger("tray").error(f"WS error: {error}")

def _send_ws(msg_dict):
    """Send a JSON message to the server WebSocket. Returns True if sent."""
    _log = logging.getLogger("tray")
    global _ws
    if not _ws or not _ws_connected:
        _log.warning(f"_send_ws DROPPED (not connected): {msg_dict.get('type')}")
        return False
    try:
        _ws.send(json.dumps(msg_dict))
        _log.info(f"_send_ws OK: {msg_dict}")
        return True
    except Exception as e:
        _log.error(f"_send_ws FAILED: {e}")
        return False

def _ws_loop():
    """Connect to the dictation WebSocket and handle messages."""
    import websocket
    global _ws_connected, _ws

    url = f"ws://localhost:{DICTATION_PORT}/ws"

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
        except Exception:
            pass
        _ws_connected = False
        _update_tray_icon("disconnected")
        time.sleep(5)

_kb_controller = None

def _inject_text(text):
    """Inject text into the currently focused application via clipboard paste."""
    _log = logging.getLogger("tray")
    _log.info(f"_inject_text called: '{text[:100]}' | len={len(text)}")
    if not text.strip():
        return
    inject = text + " "
    try:
        if IS_WIN:
            import ctypes
            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32

            CF_UNICODETEXT = 13
            GMEM_MOVEABLE = 0x0002

            data = inject.encode("utf-16-le") + b"\x00\x00"
            h = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
            p = kernel32.GlobalLock(h)
            ctypes.memmove(p, data, len(data))
            kernel32.GlobalUnlock(h)

            user32.OpenClipboard(0)
            user32.EmptyClipboard()
            user32.SetClipboardData(CF_UNICODETEXT, h)
            user32.CloseClipboard()

            global _kb_controller
            if _kb_controller is None:
                from pynput.keyboard import Controller
                _kb_controller = Controller()
            from pynput.keyboard import Key
            _kb_controller.press(Key.ctrl)
            _kb_controller.press("v")
            _kb_controller.release("v")
            _kb_controller.release(Key.ctrl)
            _log.info("_inject_text completed OK (clipboard paste)")
        else:
            if _kb_controller is None:
                from pynput.keyboard import Controller
                _kb_controller = Controller()
            words = inject.split()
            for i, word in enumerate(words):
                if i > 0:
                    _kb_controller.type(" ")
                _kb_controller.type(word)
            _log.info("_inject_text completed OK (keyboard type)")
    except Exception as e:
        _log.error(f"_inject_text FAILED: {e}", exc_info=True)

_overlay_tk = None
_overlay_win = None
_overlay_visible = False
_overlay_dot = None
_overlay_dot_id = None
_pulse_on = [True]

def _ensure_overlay():
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
        hwnd = int(_overlay_win.frame(), 16)
        WS_EX_NOACTIVATE = 0x08000000
        WS_EX_TOOLWINDOW = 0x00000080
        GWL_EXSTYLE = -20
        ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE,
            ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW)

    frame = tk.Frame(_overlay_win, bg="#1a1a2e", padx=12, pady=6)
    frame.pack()

    _overlay_dot = tk.Canvas(frame, width=12, height=12, bg="#1a1a2e", highlightthickness=0)
    _overlay_dot_id = _overlay_dot.create_oval(2, 2, 10, 10, fill="#f44336", outline="")
    _overlay_dot.pack(side="left", padx=(0, 6))

    label = tk.Label(frame, text="Listening...", fg="#e0e0e0", bg="#1a1a2e",
                     font=("Segoe UI", 11, "bold"))
    label.pack(side="left")

    _overlay_win.update_idletasks()
    w = _overlay_win.winfo_reqwidth() + 24
    h = _overlay_win.winfo_reqheight() + 12

    screen_w = _overlay_win.winfo_screenwidth()
    screen_h = _overlay_win.winfo_screenheight()
    x = screen_w - w - 20
    y = screen_h - h - 60
    _overlay_win.geometry(f"{w}x{h}+{x}+{y}")

    _pulse_dot()

def _pulse_dot():
    """Animate the red dot by cycling between bright and dim."""
    if not _overlay_dot or not _overlay_tk:
        return
    _pulse_on[0] = not _pulse_on[0]
    color = "#f44336" if _pulse_on[0] else "#7a1a14"
    _overlay_dot.itemconfig(_overlay_dot_id, fill=color)
    _overlay_tk.after(600, _pulse_dot)

def _show_overlay():
    global _overlay_visible
    if _overlay_visible:
        return
    _overlay_visible = True
    if _overlay_win:
        _overlay_tk.after(0, lambda: (_overlay_win.deiconify(), _overlay_win.lift()))

def _hide_overlay():
    global _overlay_visible
    _overlay_visible = False
    if _overlay_win:
        _overlay_tk.after(0, lambda: _overlay_win.withdraw() if _overlay_win else None)

def _run_overlay_mainloop():
    """Run the tkinter mainloop for the overlay on its own thread."""
    _ensure_overlay()
    if _overlay_tk:
        _overlay_tk.mainloop()

_hotkey_listener = None

BLOCKED_SINGLE_KEYS = {
    "space", "backspace", "enter", "return", "tab", "escape",
    "delete", "insert", "home", "end", "page_up", "page_down",
    "up", "down", "left", "right",
}

def _is_modifier(key):
    from pynput.keyboard import Key
    return key in (Key.shift, Key.shift_l, Key.shift_r,
                   Key.ctrl, Key.ctrl_l, Key.ctrl_r,
                   Key.alt, Key.alt_l, Key.alt_r, Key.alt_gr,
                   Key.cmd, Key.cmd_l, Key.cmd_r)

def _key_to_code(key):
    """Convert a pynput key to a string code for storage."""
    from pynput.keyboard import Key, KeyCode
    if isinstance(key, Key):
        return key.name
    elif isinstance(key, KeyCode):
        if key.vk and key.char is None:
            return f"vk_{key.vk}"
        return key.char if key.char else f"vk_{key.vk}"
    return str(key)

def _key_to_display(key):
    """Convert a pynput key to a human-readable display string."""
    from pynput.keyboard import Key, KeyCode
    if isinstance(key, Key):
        name = key.name.replace("_", " ").title()
        return name.replace("Cmd", "Win")
    elif isinstance(key, KeyCode):
        if key.char:
            return key.char.upper()
        return f"Key {key.vk}"
    return str(key)

_pressed_modifiers = set()
_grace_timer = None
_cached_hotkey = None
_cached_mode = None

def _reload_hotkey_cache():
    """Reload hotkey config from settings into memory."""
    global _cached_hotkey, _cached_mode
    _cached_hotkey = get_setting("global_dictation_hotkey")
    _cached_mode = get_setting("global_dictation_mode", "hold")

def _match_hotkey(key):
    """Check if a key event matches the configured hotkey (including modifier combos)."""
    if not _cached_hotkey:
        return False
    stored_code = _cached_hotkey.get("code", "")
    parts = stored_code.split("+")
    trigger_key = parts[-1]
    required_mods = set(parts[:-1]) if len(parts) > 1 else set()

    code = _key_to_code(key)
    if code != trigger_key:
        return False
    if required_mods and not required_mods.issubset(_pressed_modifiers):
        return False
    return True

def _start_hotkey_listener():
    from pynput import keyboard
    global _hotkey_listener

    _reload_hotkey_cache()

    def _http_set_active(active: bool):
        """Set dictation active state via HTTP POST (thread-safe)."""
        _log = logging.getLogger("tray")
        import urllib.request, urllib.error
        url = f"http://localhost:{DICTATION_PORT}/api/dictation-active"
        data = json.dumps({"active": active}).encode()
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=3) as resp:
                _log.info(f"HTTP dictation-active={active} -> {resp.status}")
                return True
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode()
            except Exception:
                pass
            _log.error(f"HTTP dictation-active={active} FAILED: {e.code} {body}")
            return False
        except Exception as e:
            _log.error(f"HTTP dictation-active={active} FAILED: {e}")
            return False

    def _activate_dictation():
        """Send activation to server via HTTP POST."""
        global _server_confirmed_active
        _log = logging.getLogger("tray")
        _server_confirmed_active = False
        sent = _http_set_active(True)
        if not sent:
            _log.warning("Activation HTTP failed, will retry in 2s")
            def _retry():
                if not _server_confirmed_active and _dictation_active:
                    _log.warning("Retrying activation via HTTP")
                    _http_set_active(True)
            threading.Timer(2.0, _retry).start()

    def _deactivate_dictation():
        """Send deactivation to server via HTTP POST."""
        global _server_confirmed_active
        _server_confirmed_active = False
        _http_set_active(False)

    def on_press(key):
        global _dictation_active, _grace_timer
        _log = logging.getLogger("tray")

        if _is_modifier(key):
            _pressed_modifiers.add(_key_to_code(key))
            return

        if not _match_hotkey(key):
            return

        mode = _cached_mode or "hold"

        if mode == "hold" and _dictation_active:
            return

        _log.info(f"Hotkey PRESS detected: mode={mode}, active={_dictation_active}, ws_connected={_ws_connected}")

        if mode == "toggle":
            if _dictation_active:
                _dictation_active = False
                _deactivate_dictation()
                _update_tray_icon("idle")
                _hide_overlay()
            else:
                _dictation_active = True
                _activate_dictation()
                _update_tray_icon("active")
                _show_overlay()
        else:
            if _grace_timer:
                _grace_timer.cancel()
                _grace_timer = None
            _dictation_active = True
            _activate_dictation()
            _update_tray_icon("active")
            _show_overlay()

    def on_release(key):
        global _dictation_active, _grace_timer
        _log = logging.getLogger("tray")

        if _is_modifier(key):
            _pressed_modifiers.discard(_key_to_code(key))
            return

        if not _match_hotkey(key):
            return

        mode = _cached_mode or "hold"
        if mode != "hold":
            return

        _log.info(f"Hotkey RELEASE: active={_dictation_active}")

        if _dictation_active:
            def _grace_expired():
                global _dictation_active, _grace_timer
                _grace_timer = None
                if _dictation_active:
                    _dictation_active = False
                    _deactivate_dictation()
                    _update_tray_icon("idle")
                    _hide_overlay()

            _grace_timer = threading.Timer(GRACE_MS / 1000.0, _grace_expired)
            _grace_timer.start()

    _hotkey_listener = keyboard.Listener(on_press=on_press, on_release=on_release)
    _hotkey_listener.start()

def _show_hotkey_dialog():
    """Show a small tkinter dialog to capture a global hotkey."""
    import tkinter as tk
    from pynput import keyboard

    result = {"key": None, "code": None, "display": None}
    listener = [None]

    root = tk.Tk()
    root.title("Set Global Dictation Hotkey")
    root.geometry("360x150")
    root.resizable(False, False)
    root.attributes("-topmost", True)
    root.configure(bg="#1a1a2e")

    label = tk.Label(root, text="Press any key or key combo...",
                     fg="#4FC3F7", bg="#1a1a2e", font=("Segoe UI", 14, "bold"))
    label.pack(pady=20)

    msg_label = tk.Label(root, text="", fg="#f44336", bg="#1a1a2e",
                         font=("Segoe UI", 10))
    msg_label.pack(pady=5)

    def _is_blocked_single(key):
        code = _key_to_code(key)
        if code.lower() in BLOCKED_SINGLE_KEYS:
            return True
        if hasattr(key, 'char') and key.char and key.char.isalnum():
            return True
        return False

    def on_press(key):
        if _is_modifier(key):
            return

        display = _key_to_display(key)
        code = _key_to_code(key)

        # If no modifiers held, check if it's a blocked single key
        if not _pressed_modifiers and _is_blocked_single(key):
            root.after(0, lambda: msg_label.configure(
                text=f"Can't use {display} alone — conflicts with typing.\n"
                     f"Use a function key (F1-F12) or add a modifier (Ctrl+Shift+...)"))
            return

        # Build combo string if modifiers are held
        if _pressed_modifiers:
            mod_names = sorted(_pressed_modifiers)
            mod_display = "+".join(m.replace("_l","").replace("_r","").replace("_gr","").title()
                                   for m in mod_names)
            code = "+".join(mod_names) + "+" + code
            display = mod_display + "+" + display

        result["code"] = code
        result["display"] = display

        # Stop listener and close dialog
        if listener[0]:
            listener[0].stop()
        root.after(0, root.destroy)

    listener[0] = keyboard.Listener(on_press=on_press)
    listener[0].start()

    root.protocol("WM_DELETE_WINDOW", lambda: (listener[0].stop() if listener[0] else None, root.destroy()))
    root.mainloop()

    if result["code"]:
        set_setting("global_dictation_hotkey", {"code": result["code"], "display": result["display"]})
        if _tray_icon:
            _tray_icon.menu = _build_menu()
            _tray_icon.update_menu()

        # Restart hotkey listener with new key
        global _hotkey_listener
        if _hotkey_listener:
            _hotkey_listener.stop()
        _start_hotkey_listener()


# ── Entry Point ──

if __name__ == "__main__":
    import logging
    SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.DEBUG, format="%(asctime)s [%(levelname)s] %(message)s",
        filename=str(SETTINGS_DIR / "tray_debug.log"), filemode="w",
    )
    _log = logging.getLogger("tray")

    atexit.register(stop_server)

    if IS_WIN:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("LinguaTaxi.Dictation")
        _log.info("AppUserModelID set to LinguaTaxi.Dictation")

    try:
        run_tray()
    except Exception:
        _log.exception("run_tray crashed")
        raise
    finally:
        stop_server()
