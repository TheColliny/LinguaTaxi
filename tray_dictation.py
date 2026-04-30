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

def _ws_loop():
    pass

def _start_hotkey_listener():
    pass

def _show_hotkey_dialog():
    pass


# ── Entry Point ──

if __name__ == "__main__":
    run_tray()
