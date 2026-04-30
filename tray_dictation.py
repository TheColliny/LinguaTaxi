#!/usr/bin/env python3
"""
LinguaTaxi — Global Dictation Tray App
System-wide push-to-talk speech-to-text via system tray.
Connects to the LinguaTaxi server's dictation WebSocket and injects
transcribed text into the focused application using OS keystroke simulation.
"""

import json, os, subprocess, sys, threading, time
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

def start_server_if_needed():
    global _server_proc
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
        # Wait for server to become ready
        for _ in range(30):
            time.sleep(1)
            if _is_server_running():
                return True
        return False
    except Exception:
        return False

def stop_server():
    global _server_proc
    if _server_proc:
        try:
            _server_proc.terminate()
            _server_proc.wait(timeout=5)
        except Exception:
            try:
                _server_proc.kill()
            except Exception:
                pass
        _server_proc = None

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
    icon.update_menu()

def _on_set_toggle(icon, item):
    set_setting("global_dictation_mode", "toggle")
    icon.update_menu()

def _on_quit(icon, item):
    stop_server()
    icon.stop()

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
    global _tray_icon

    _init_icons()

    _tray_icon = pystray.Icon(
        "LinguaTaxi Dictation",
        ICON_GREY,
        "LinguaTaxi Dictation — Starting...",
        menu=_build_menu(),
    )

    # Start server + WS + hotkey in background threads
    def _startup(icon):
        icon.visible = True
        started = start_server_if_needed()
        if started:
            _update_tray_icon("idle")
            threading.Thread(target=_ws_loop, daemon=True).start()
            _start_hotkey_listener()
        else:
            _update_tray_icon("disconnected")
            # Retry connection periodically
            threading.Thread(target=_reconnect_loop, daemon=True).start()

    _tray_icon.run(setup=_startup)

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

# ── Placeholder functions (implemented in later tasks) ──

def _on_ws_open(ws):
    global _ws_connected
    _ws_connected = True
    _update_tray_icon("idle")

def _on_ws_message(ws, message):
    try:
        msg = json.loads(message)
    except Exception:
        return

    if msg.get("type") == "final" and msg.get("text"):
        _inject_text(msg["text"])
    elif msg.get("type") == "dictation_active":
        global _dictation_active
        _dictation_active = msg.get("active", False)
        _update_tray_icon("active" if _dictation_active else "idle")
        if _dictation_active:
            _show_overlay()
        else:
            _hide_overlay()
    elif msg.get("type") == "status":
        if msg.get("dictation_active") is not None:
            _dictation_active = msg["dictation_active"]
            _update_tray_icon("active" if _dictation_active else "idle")

def _on_ws_close(ws, close_status_code, close_msg):
    global _ws_connected
    _ws_connected = False
    _update_tray_icon("disconnected")

def _on_ws_error(ws, error):
    pass

def _send_ws(msg_dict):
    """Send a JSON message to the server WebSocket."""
    global _ws
    if _ws and _ws_connected:
        try:
            _ws.send(json.dumps(msg_dict))
        except Exception:
            pass

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

def _inject_text(text):
    """Inject text word-by-word into the currently focused application."""
    from pynput.keyboard import Controller

    kb = Controller()
    words = text.split()
    for i, word in enumerate(words):
        if i > 0:
            kb.type(" ")
        kb.type(word)
    # Trailing space after the chunk so the next chunk doesn't merge
    kb.type(" ")

def _show_overlay():
    pass

def _hide_overlay():
    pass

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

def _start_hotkey_listener():
    from pynput import keyboard
    global _hotkey_listener

    def on_press(key):
        global _dictation_active, _grace_timer

        if _is_modifier(key):
            _pressed_modifiers.add(_key_to_code(key))
            return

        hotkey_cfg = get_setting("global_dictation_hotkey")
        if not hotkey_cfg:
            return

        code = _key_to_code(key)
        if code != hotkey_cfg.get("code"):
            return

        mode = get_setting("global_dictation_mode", "hold")

        if mode == "toggle":
            if _dictation_active:
                _dictation_active = False
                _send_ws({"type": "set_dictation_active", "active": False})
                _update_tray_icon("idle")
                _hide_overlay()
            else:
                _dictation_active = True
                _send_ws({"type": "set_dictation_active", "active": True})
                _update_tray_icon("active")
                _show_overlay()
        else:
            # Hold mode — start dictation
            if _grace_timer:
                _grace_timer.cancel()
                _grace_timer = None
            if not _dictation_active:
                _dictation_active = True
                _send_ws({"type": "set_dictation_active", "active": True})
                _update_tray_icon("active")
                _show_overlay()

    def on_release(key):
        global _dictation_active, _grace_timer

        if _is_modifier(key):
            _pressed_modifiers.discard(_key_to_code(key))
            return

        hotkey_cfg = get_setting("global_dictation_hotkey")
        if not hotkey_cfg:
            return

        code = _key_to_code(key)
        if code != hotkey_cfg.get("code"):
            return

        mode = get_setting("global_dictation_mode", "hold")
        if mode != "hold":
            return

        if _dictation_active:
            def _grace_expired():
                global _dictation_active, _grace_timer
                _grace_timer = None
                if _dictation_active:
                    _dictation_active = False
                    _send_ws({"type": "set_dictation_active", "active": False})
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
    run_tray()
