# Global Dictation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add system-wide push-to-talk dictation that injects transcribed speech as typed text into any focused application via a lightweight system tray app.

**Architecture:** A new `tray_dictation.py` connects to the existing server's dictation WebSocket, captures global hotkeys via `pynput`, and injects received text word-by-word using OS keystroke simulation. The existing launcher gains minimize-to-tray via `pystray`. Both share settings through `launcher_settings.json`.

**Tech Stack:** Python 3.11, pystray (tray icon), pynput (global hotkeys + keystroke injection), websocket-client (sync WS), Pillow (tray icon images), tkinter (overlay)

**Spec:** `docs/superpowers/specs/2026-04-29-global-dictation-design.md`

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `tray_dictation.py` | Create | Tray app: WS client, global hotkey, text injection, overlay, server auto-start |
| `launcher.pyw` | Modify | Add minimize-to-tray with pystray, close_to_tray setting |
| `requirements.txt` | Modify | Add pystray, pynput, Pillow, websocket-client |
| `build/windows/build.bat` | Modify | Add new deps to both venv installs |
| `build/windows/installer.iss` | Modify | Add "LinguaTaxi Dictation" Start Menu shortcut |

---

### Task 1: Add new dependencies to requirements.txt and build system

**Files:**
- Modify: `requirements.txt`
- Modify: `build/windows/build.bat`

- [ ] **Step 1: Add dependencies to requirements.txt**

Add after the `onnxruntime` line (line 8) and before the `# Optional` comment:

```
pystray>=0.19.0,<1.0
pynput>=1.7.0,<2.0
Pillow>=10.0.0,<12.0
websocket-client>=1.6.0,<2.0
```

- [ ] **Step 2: Add dependencies to build.bat venv installs**

In `build/windows/build.bat`, find the lite venv section where offline translation packages are installed (around line 154). After the offline translation install block for the lite venv, add:

```bat
echo   Installing tray dictation packages...
"%VENV_LITE%\Scripts\pip.exe" install pystray pynput Pillow websocket-client >> "%SCRIPT_DIR%build_log.txt" 2>&1
if !ERRORLEVEL! NEQ 0 (
    echo   [FAIL] Tray dictation package installation failed. See build_log.txt
    exit /b 1
)
```

Find the same location for the full venv (after offline translation install for full venv, around line 213) and add the identical block but with `%VENV_FULL%`:

```bat
echo   Installing tray dictation packages...
"%VENV_FULL%\Scripts\pip.exe" install pystray pynput Pillow websocket-client >> "%SCRIPT_DIR%build_log.txt" 2>&1
if !ERRORLEVEL! NEQ 0 (
    echo   [FAIL] Tray dictation package installation failed. See build_log.txt
    exit /b 1
)
```

- [ ] **Step 3: Commit**

```bash
git add requirements.txt build/windows/build.bat
git commit -m "[deps] add pystray, pynput, Pillow, websocket-client for global dictation"
```

---

### Task 2: Create tray_dictation.py — settings, server auto-start, and tray icon skeleton

**Files:**
- Create: `tray_dictation.py`

This task creates the file with settings management, server auto-start capability, and the basic pystray tray icon with context menu. No hotkey or WS logic yet — those come in later tasks.

- [ ] **Step 1: Create tray_dictation.py with settings and tray skeleton**

```python
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
```

- [ ] **Step 2: Verify the tray icon appears**

Run: `python tray_dictation.py`

Expected: A grey circle icon with "LT" appears in the system tray. Right-click shows the context menu with PTT Key, Set Global Hotkey, Mode Hold/Toggle, Quit. Clicking Quit exits.

- [ ] **Step 3: Commit**

```bash
git add tray_dictation.py
git commit -m "[feat] create tray_dictation.py — settings, server auto-start, tray icon skeleton"
```

---

### Task 3: Add WebSocket client loop to tray_dictation.py

**Files:**
- Modify: `tray_dictation.py` (replace `_ws_loop` placeholder)

- [ ] **Step 1: Replace the `_ws_loop` placeholder**

Replace:
```python
def _ws_loop():
    pass
```

With:

```python
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
```

Also add the `_ws` global variable near the other globals at the top of the tray app section:

```python
_ws = None
```

And add the WS callback functions right before `_ws_loop`:

```python
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
```

And add placeholder functions for injection and overlay (implemented in later tasks):

```python
def _inject_text(text):
    pass

def _show_overlay():
    pass

def _hide_overlay():
    pass
```

- [ ] **Step 2: Verify WS connection**

1. Start the LinguaTaxi server via launcher
2. Run: `python tray_dictation.py`
3. Expected: tray icon turns green (connected). Server log should show a new WS client connected.

- [ ] **Step 3: Commit**

```bash
git add tray_dictation.py
git commit -m "[feat] add WebSocket client loop to tray dictation app"
```

---

### Task 4: Add global hotkey capture and hotkey configuration dialog

**Files:**
- Modify: `tray_dictation.py` (replace `_start_hotkey_listener` and `_show_hotkey_dialog` placeholders)

- [ ] **Step 1: Replace the `_start_hotkey_listener` placeholder**

Replace:
```python
def _start_hotkey_listener():
    pass
```

With:

```python
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
```

- [ ] **Step 2: Replace the `_show_hotkey_dialog` placeholder**

Replace:
```python
def _show_hotkey_dialog():
    pass
```

With:

```python
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
```

- [ ] **Step 3: Verify hotkey capture**

1. Start server, run `python tray_dictation.py`
2. Right-click tray icon → "Set Global Hotkey..."
3. Press F8 → dialog closes, menu shows "PTT Key: F8"
4. Try pressing Enter → error message appears, stays in dialog
5. Press Ctrl+Shift+D → dialog closes, menu shows "PTT Key: Ctrl+Shift+D"

- [ ] **Step 4: Commit**

```bash
git add tray_dictation.py
git commit -m "[feat] add global hotkey listener and capture dialog to tray app"
```

---

### Task 5: Add word-by-word text injection

**Files:**
- Modify: `tray_dictation.py` (replace `_inject_text` placeholder)

- [ ] **Step 1: Replace the `_inject_text` placeholder**

Replace:
```python
def _inject_text(text):
    pass
```

With:

```python
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
```

- [ ] **Step 2: Verify text injection**

1. Start server, run `python tray_dictation.py`
2. Set hotkey to F8, set mode to Hold
3. Open Notepad (or any text editor)
4. Click in Notepad so it has focus
5. Hold F8 and speak
6. Expected: transcribed words appear in Notepad word by word
7. Release F8 → dictation stops after ~750ms

- [ ] **Step 3: Commit**

```bash
git add tray_dictation.py
git commit -m "[feat] add word-by-word text injection via pynput"
```

---

### Task 6: Add floating overlay indicator

**Files:**
- Modify: `tray_dictation.py` (replace `_show_overlay` and `_hide_overlay` placeholders)

- [ ] **Step 1: Replace overlay placeholders**

Replace:
```python
def _show_overlay():
    pass

def _hide_overlay():
    pass
```

With:

```python
_overlay_root = None
_overlay_visible = False

def _ensure_overlay():
    """Create the overlay window (once, on the main-ish thread)."""
    global _overlay_root
    if _overlay_root is not None:
        return

    import tkinter as tk

    _overlay_root = tk.Tk()
    _overlay_root.withdraw()
    _overlay_root.overrideredirect(True)
    _overlay_root.attributes("-topmost", True)
    _overlay_root.attributes("-alpha", 0.85)
    _overlay_root.configure(bg="#1a1a2e")

    # Prevent focus stealing
    if IS_WIN:
        import ctypes
        hwnd = int(_overlay_root.frame(), 16)
        WS_EX_NOACTIVATE = 0x08000000
        WS_EX_TOOLWINDOW = 0x00000080
        GWL_EXSTYLE = -20
        ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE,
            ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW)

    frame = tk.Frame(_overlay_root, bg="#1a1a2e", padx=12, pady=6)
    frame.pack()

    dot = tk.Canvas(frame, width=12, height=12, bg="#1a1a2e", highlightthickness=0)
    dot.create_oval(2, 2, 10, 10, fill="#f44336", outline="")
    dot.pack(side="left", padx=(0, 6))

    label = tk.Label(frame, text="Listening...", fg="#e0e0e0", bg="#1a1a2e",
                     font=("Segoe UI", 11, "bold"))
    label.pack(side="left")

    _overlay_root.update_idletasks()
    w = _overlay_root.winfo_reqwidth() + 24
    h = _overlay_root.winfo_reqheight() + 12

    # Position: bottom-right, above taskbar
    screen_w = _overlay_root.winfo_screenwidth()
    screen_h = _overlay_root.winfo_screenheight()
    x = screen_w - w - 20
    y = screen_h - h - 60
    _overlay_root.geometry(f"{w}x{h}+{x}+{y}")

def _show_overlay():
    global _overlay_visible
    if _overlay_visible:
        return
    _overlay_visible = True

    def _do_show():
        _ensure_overlay()
        if _overlay_root:
            _overlay_root.deiconify()
            _overlay_root.lift()

    if _overlay_root:
        _overlay_root.after(0, _do_show)
    else:
        threading.Thread(target=_run_overlay_mainloop, daemon=True).start()

def _hide_overlay():
    global _overlay_visible
    _overlay_visible = False
    if _overlay_root:
        _overlay_root.after(0, lambda: _overlay_root.withdraw() if _overlay_root else None)

def _run_overlay_mainloop():
    """Run the tkinter mainloop for the overlay on its own thread."""
    _ensure_overlay()
    if _overlay_root:
        _overlay_root.deiconify()
        _overlay_root.lift()
        _overlay_root.mainloop()
```

- [ ] **Step 2: Initialize overlay on startup**

In the `_startup` function inside `run_tray()`, add after the `_start_hotkey_listener()` call:

```python
        threading.Thread(target=_run_overlay_mainloop, daemon=True).start()
```

Wait — the overlay mainloop should be started once during startup, not every time we show it. Update `_show_overlay` to just call `deiconify` and `_hide_overlay` to call `withdraw`. The mainloop thread is started once.

Revise `_show_overlay`:

```python
def _show_overlay():
    global _overlay_visible
    if _overlay_visible:
        return
    _overlay_visible = True
    if _overlay_root:
        _overlay_root.after(0, lambda: (_overlay_root.deiconify(), _overlay_root.lift()))

def _hide_overlay():
    global _overlay_visible
    _overlay_visible = False
    if _overlay_root:
        _overlay_root.after(0, lambda: _overlay_root.withdraw() if _overlay_root else None)
```

And in `_startup` inside `run_tray()`, start the overlay mainloop once:

```python
        threading.Thread(target=_run_overlay_mainloop, daemon=True).start()
```

Remove the `threading.Thread` call from `_show_overlay` (it's no longer needed since the mainloop is always running).

- [ ] **Step 3: Verify overlay**

1. Start server, run `python tray_dictation.py`
2. Set hotkey, hold it → overlay appears bottom-right with "Listening..." and red dot
3. Release → overlay disappears
4. Verify: overlay does NOT steal focus from the current app

- [ ] **Step 4: Commit**

```bash
git add tray_dictation.py
git commit -m "[feat] add floating overlay indicator for global dictation"
```

---

### Task 7: Add minimize-to-tray to launcher.pyw

**Files:**
- Modify: `launcher.pyw`

- [ ] **Step 1: Add `close_to_tray` to DEFAULT_SETTINGS**

In `launcher.pyw`, find `DEFAULT_SETTINGS` dict (line 63). Add:

```python
    "close_to_tray": True,
```

- [ ] **Step 2: Add the tray icon setup method to LinguaTaxiApp**

Add this method to the `LinguaTaxiApp` class, after the `__init__` method:

```python
    def _setup_tray(self):
        """Set up system tray icon for minimize-to-tray."""
        try:
            import pystray
            from PIL import Image
        except ImportError:
            self._tray_icon = None
            return

        icon_path = APP_DIR / "assets" / "linguataxi.png"
        if icon_path.exists():
            image = Image.open(str(icon_path)).resize((64, 64))
        else:
            image = Image.new("RGBA", (64, 64), (79, 195, 247, 255))

        def _show_window(icon, item):
            self.after(0, self._restore_from_tray)

        def _start_srv(icon, item):
            self.after(0, self._start_server)

        def _stop_srv(icon, item):
            self.after(0, self._stop_server)

        def _open_op(icon, item):
            self.after(0, self._open_operator)

        def _open_disp(icon, item):
            self.after(0, self._open_main)

        def _open_dict(icon, item):
            self.after(0, self._open_dictation)

        def _quit(icon, item):
            self.after(0, self._quit_from_tray)

        self._tray_icon = pystray.Icon(
            "LinguaTaxi",
            image,
            "LinguaTaxi",
            menu=pystray.Menu(
                pystray.MenuItem("Show Window", _show_window, default=True),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Start Server", _start_srv),
                pystray.MenuItem("Stop Server", _stop_srv),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Open Operator", _open_op),
                pystray.MenuItem("Open Display", _open_disp),
                pystray.MenuItem("Open Dictation", _open_dict),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Quit", _quit),
            ),
        )

    def _minimize_to_tray(self):
        """Hide window and show tray icon."""
        if not hasattr(self, '_tray_icon') or not self._tray_icon:
            return False
        self.withdraw()
        threading.Thread(target=self._tray_icon.run, daemon=True).start()
        return True

    def _restore_from_tray(self):
        """Show window and hide tray icon."""
        if hasattr(self, '_tray_icon') and self._tray_icon:
            try:
                self._tray_icon.stop()
            except Exception:
                pass
        self.deiconify()
        self.lift()
        self.focus_force()

    def _quit_from_tray(self):
        """Full quit from tray: stop server, destroy window, exit."""
        if self._server_running:
            self._stop_server()
        if hasattr(self, '_tray_icon') and self._tray_icon:
            try:
                self._tray_icon.stop()
            except Exception:
                pass
        self.destroy()
```

- [ ] **Step 3: Call `_setup_tray` in `__init__`**

In `__init__`, add after `self._build_ui()` (line 214):

```python
        self._setup_tray()
```

- [ ] **Step 4: Modify `_on_close` to support minimize-to-tray**

Replace the existing `_on_close` method (line 2790) with:

```python
    def _on_close(self):
        self._closing = True
        self._save_current_settings()

        # Check if we should minimize to tray instead of closing
        if self.settings.get("close_to_tray", True) and self._server_running:
            if self._minimize_to_tray():
                self._closing = False
                return

        if self._server_running:
            if messagebox.askyesno(_t("launcher.dialog_quit_title"),
                _t("launcher.dialog_quit_message")):
                self._stop_server()
            else:
                self._closing = False
                self._poll_log_queue()
                return

        self.destroy()
```

- [ ] **Step 5: Add "close to tray" checkbox to the UI**

Find the settings area in `_build_ui`. Look for where other checkboxes or settings are added. Add a checkbox for close-to-tray. The exact location depends on the UI layout — find a suitable place in the settings section and add:

```python
        self.close_tray_var = tk.BooleanVar(value=self.settings.get("close_to_tray", True))
        ttk.Checkbutton(settings_frame, text=_t("launcher.close_to_tray"),
                        variable=self.close_tray_var,
                        command=lambda: self._update_setting("close_to_tray", self.close_tray_var.get())
                        ).pack(anchor="w", padx=10, pady=2)
```

Add the helper if it doesn't exist:

```python
    def _update_setting(self, key, value):
        self.settings[key] = value
```

- [ ] **Step 6: Verify minimize-to-tray**

1. Run `python launcher.pyw`
2. Start the server
3. Click the X button → window hides, LinguaTaxi icon appears in tray
4. Right-click tray icon → context menu shows
5. Click "Show Window" → launcher window reappears
6. Click "Quit" from tray → server stops, app exits

- [ ] **Step 7: Commit**

```bash
git add launcher.pyw
git commit -m "[feat] add minimize-to-tray with pystray to launcher"
```

---

### Task 8: Add "LinguaTaxi Dictation" Start Menu shortcut to installer

**Files:**
- Modify: `build/windows/installer.iss`

- [ ] **Step 1: Add the Start Menu shortcut**

Find the `[Icons]` section (line 249). Add after the existing launcher shortcut (line 250):

```ini
Name: "{group}\{#MyAppShortName} Dictation"; Filename: "{app}\venv\Scripts\pythonw.exe"; Parameters: """{app}\tray_dictation.py"""; WorkingDir: "{app}"; IconFilename: "{app}\assets\linguataxi.ico"; Comment: "LinguaTaxi Global Dictation (tray)"
```

- [ ] **Step 2: Verify the [Icons] section looks correct**

The section should now have:
1. LinguaTaxi (launcher)
2. LinguaTaxi Dictation (tray app)
3. Uninstall LinguaTaxi
4. Desktop shortcut (optional)

- [ ] **Step 3: Commit**

```bash
git add build/windows/installer.iss
git commit -m "[build] add LinguaTaxi Dictation Start Menu shortcut to installer"
```

---

### Task 9: Integration test

- [ ] **Step 1: Full end-to-end test — tray dictation standalone**

1. Run `python tray_dictation.py` (no launcher running)
2. Wait for server auto-start → tray icon turns green
3. Right-click → "Set Global Hotkey..." → press F8 → shows "PTT Key: F8"
4. Open Notepad, click in it
5. Hold F8 → overlay appears bottom-right "Listening...", tray icon turns red
6. Speak a sentence → words appear in Notepad
7. Release F8 → overlay disappears after ~750ms, tray icon turns green
8. Switch to Toggle mode from tray menu
9. Press F8 → starts dictation, press F8 again → stops
10. Right-click → Quit → server stops, tray icon disappears

- [ ] **Step 2: Full end-to-end test — launcher minimize-to-tray**

1. Run `python launcher.pyw`
2. Start server from launcher
3. Close launcher (X button) → window minimizes to tray
4. Tray icon context menu works (Show Window, Open Operator, etc.)
5. "Quit" fully exits

- [ ] **Step 3: Coexistence test**

1. Run launcher, start server
2. Minimize to tray
3. Run `python tray_dictation.py` separately
4. Both tray icons appear
5. Global dictation works while launcher is in tray
6. Quit tray dictation → launcher still in tray
7. Restore launcher from tray → still running

- [ ] **Step 4: Test in VS Code**

1. Open VS Code with a file
2. Click in the editor
3. Hold F8 → speak → words appear in VS Code editor
4. Release → dictation stops

- [ ] **Step 5: Commit any fixes**

If any issues found, fix and commit with descriptive messages.

---

## Summary

| Task | Description | Files |
|------|-------------|-------|
| 1 | Add dependencies to requirements.txt and build.bat | `requirements.txt`, `build/windows/build.bat` |
| 2 | Create tray_dictation.py skeleton (settings, server auto-start, tray icon) | `tray_dictation.py` |
| 3 | Add WebSocket client loop | `tray_dictation.py` |
| 4 | Add global hotkey listener and capture dialog | `tray_dictation.py` |
| 5 | Add word-by-word text injection | `tray_dictation.py` |
| 6 | Add floating overlay indicator | `tray_dictation.py` |
| 7 | Add minimize-to-tray to launcher | `launcher.pyw` |
| 8 | Add Start Menu shortcut to installer | `build/windows/installer.iss` |
| 9 | Integration test | all |
