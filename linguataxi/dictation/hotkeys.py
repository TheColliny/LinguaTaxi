"""Hotkey listener and key matching for dictation activation."""

from __future__ import annotations

import json
import logging
import threading
from typing import Any

from linguataxi.constants import DICTATION_PORT, GRACE_MS
from linguataxi.settings import get_setting, set_setting

_log: logging.Logger = logging.getLogger("tray")

# ── Module-level state ──

_hotkey_listener: Any | None = None
_pressed_modifiers: set[str] = set()
_grace_timer: threading.Timer | None = None
_cached_hotkey: dict[str, str] | None = None
_cached_mode: str | None = None
_dictation_active: bool = False
_server_confirmed_active: bool = False

# Keys that must not be used as a hotkey on their own (conflict with typing)
BLOCKED_SINGLE_KEYS: set[str] = {
    "space", "backspace", "enter", "return", "tab", "escape",
    "delete", "insert", "home", "end", "page_up", "page_down",
    "up", "down", "left", "right",
}

# ── Callbacks filled in by tray.py at startup ──

_update_tray_icon_cb: Any | None = None
_show_overlay_cb: Any | None = None
_hide_overlay_cb: Any | None = None
_build_menu_cb: Any | None = None
_tray_icon_ref: Any | None = None


def set_tray_callbacks(
    update_icon: Any,
    show_overlay: Any,
    hide_overlay: Any,
    build_menu: Any,
    tray_icon: Any,
) -> None:
    """Register callbacks from the tray module to avoid circular imports."""
    global _update_tray_icon_cb, _show_overlay_cb, _hide_overlay_cb
    global _build_menu_cb, _tray_icon_ref
    _update_tray_icon_cb = update_icon
    _show_overlay_cb = show_overlay
    _hide_overlay_cb = hide_overlay
    _build_menu_cb = build_menu
    _tray_icon_ref = tray_icon


# ── Key helpers ──


def _is_modifier(key: Any) -> bool:
    """Return True if *key* is a modifier (shift, ctrl, alt, cmd)."""
    from pynput.keyboard import Key
    return key in (
        Key.shift, Key.shift_l, Key.shift_r,
        Key.ctrl, Key.ctrl_l, Key.ctrl_r,
        Key.alt, Key.alt_l, Key.alt_r, Key.alt_gr,
        Key.cmd, Key.cmd_l, Key.cmd_r,
    )


def _key_to_code(key: Any) -> str:
    """Convert a pynput key to a string code for storage."""
    from pynput.keyboard import Key, KeyCode
    if isinstance(key, Key):
        return key.name
    if isinstance(key, KeyCode):
        if key.vk and key.char is None:
            return f"vk_{key.vk}"
        return key.char if key.char else f"vk_{key.vk}"
    return str(key)


def _key_to_display(key: Any) -> str:
    """Convert a pynput key to a human-readable display string."""
    from pynput.keyboard import Key, KeyCode
    if isinstance(key, Key):
        name: str = key.name.replace("_", " ").title()
        return name.replace("Cmd", "Win")
    if isinstance(key, KeyCode):
        if key.char:
            return key.char.upper()
        return f"Key {key.vk}"
    return str(key)


def _reload_hotkey_cache() -> None:
    """Reload hotkey config from settings into memory."""
    global _cached_hotkey, _cached_mode
    _cached_hotkey = get_setting("global_dictation_hotkey")
    _cached_mode = get_setting("global_dictation_mode", "hold")


def _match_hotkey(key: Any) -> bool:
    """Check if a key event matches the configured hotkey (including modifier combos)."""
    if not _cached_hotkey:
        return False
    stored_code: str = _cached_hotkey.get("code", "")
    parts: list[str] = stored_code.split("+")
    trigger_key: str = parts[-1]
    required_mods: set[str] = set(parts[:-1]) if len(parts) > 1 else set()

    code: str = _key_to_code(key)
    if code != trigger_key:
        return False
    if required_mods and not required_mods.issubset(_pressed_modifiers):
        return False
    return True


# ── Hotkey listener ──


def _start_hotkey_listener() -> None:
    """Start the global hotkey listener using pynput."""
    from pynput import keyboard
    global _hotkey_listener

    _reload_hotkey_cache()

    def _http_set_active(active: bool) -> bool:
        """Set dictation active state via HTTP POST (thread-safe)."""
        import urllib.request
        import urllib.error

        url: str = f"http://localhost:{DICTATION_PORT}/api/dictation-active"
        data: bytes = json.dumps({"active": active}).encode()
        req = urllib.request.Request(
            url, data=data, headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=3) as resp:
                _log.info("HTTP dictation-active=%s -> %s", active, resp.status)
                return True
        except urllib.error.HTTPError as exc:
            body: str = ""
            try:
                body = exc.read().decode()
            except Exception:
                pass
            _log.error(
                "HTTP dictation-active=%s FAILED: %s %s", active, exc.code, body,
            )
            return False
        except Exception as exc:
            _log.error("HTTP dictation-active=%s FAILED: %s", active, exc)
            return False

    def _activate_dictation() -> None:
        """Send activation to server via HTTP POST."""
        global _server_confirmed_active
        _server_confirmed_active = False
        sent: bool = _http_set_active(True)
        if not sent:
            _log.warning("Activation HTTP failed, will retry in 2s")

            def _retry() -> None:
                if not _server_confirmed_active and _dictation_active:
                    _log.warning("Retrying activation via HTTP")
                    _http_set_active(True)
            threading.Timer(2.0, _retry).start()

    def _deactivate_dictation() -> None:
        """Send deactivation to server via HTTP POST."""
        global _server_confirmed_active
        _server_confirmed_active = False
        _http_set_active(False)

    def on_press(key: Any) -> None:
        """Handle key-press events for the global hotkey."""
        global _dictation_active, _grace_timer

        if _is_modifier(key):
            _pressed_modifiers.add(_key_to_code(key))
            return

        if not _match_hotkey(key):
            return

        mode: str = _cached_mode or "hold"

        if mode == "hold" and _dictation_active:
            return

        _log.info(
            "Hotkey PRESS detected: mode=%s, active=%s",
            mode, _dictation_active,
        )

        if mode == "toggle":
            if _dictation_active:
                _dictation_active = False
                _deactivate_dictation()
                if _update_tray_icon_cb:
                    _update_tray_icon_cb("idle")
                if _hide_overlay_cb:
                    _hide_overlay_cb()
            else:
                _dictation_active = True
                _activate_dictation()
                if _update_tray_icon_cb:
                    _update_tray_icon_cb("active")
                if _show_overlay_cb:
                    _show_overlay_cb()
        else:
            if _grace_timer:
                _grace_timer.cancel()
                _grace_timer = None
            _dictation_active = True
            _activate_dictation()
            if _update_tray_icon_cb:
                _update_tray_icon_cb("active")
            if _show_overlay_cb:
                _show_overlay_cb()

    def on_release(key: Any) -> None:
        """Handle key-release events for the global hotkey."""
        global _dictation_active, _grace_timer

        if _is_modifier(key):
            _pressed_modifiers.discard(_key_to_code(key))
            return

        if not _match_hotkey(key):
            return

        mode: str = _cached_mode or "hold"
        if mode != "hold":
            return

        _log.info("Hotkey RELEASE: active=%s", _dictation_active)

        if _dictation_active:
            def _grace_expired() -> None:
                global _dictation_active, _grace_timer
                _grace_timer = None
                if _dictation_active:
                    _dictation_active = False
                    _deactivate_dictation()
                    if _update_tray_icon_cb:
                        _update_tray_icon_cb("idle")
                    if _hide_overlay_cb:
                        _hide_overlay_cb()

            _grace_timer = threading.Timer(GRACE_MS / 1000.0, _grace_expired)
            _grace_timer.start()

    _hotkey_listener = keyboard.Listener(on_press=on_press, on_release=on_release)
    _hotkey_listener.start()


# ── Hotkey configuration dialog ──


def _show_hotkey_dialog() -> None:
    """Show a small tkinter dialog to capture a global hotkey."""
    import tkinter as tk
    from pynput import keyboard
    global _hotkey_listener

    result: dict[str, str | None] = {"key": None, "code": None, "display": None}
    listener: list[Any] = [None]

    root = tk.Tk()
    root.title("Set Global Dictation Hotkey")
    root.geometry("360x150")
    root.resizable(False, False)
    root.attributes("-topmost", True)
    root.configure(bg="#1a1a2e")

    label = tk.Label(
        root, text="Press any key or key combo...",
        fg="#4FC3F7", bg="#1a1a2e", font=("Segoe UI", 14, "bold"),
    )
    label.pack(pady=20)

    msg_label = tk.Label(
        root, text="", fg="#f44336", bg="#1a1a2e",
        font=("Segoe UI", 10),
    )
    msg_label.pack(pady=5)

    def _is_blocked_single(key: Any) -> bool:
        """Return True if *key* must not be used alone as a hotkey."""
        code: str = _key_to_code(key)
        if code.lower() in BLOCKED_SINGLE_KEYS:
            return True
        if hasattr(key, "char") and key.char and key.char.isalnum():
            return True
        return False

    def on_press(key: Any) -> None:
        if _is_modifier(key):
            return

        display: str = _key_to_display(key)
        code: str = _key_to_code(key)

        # If no modifiers held, check if it is a blocked single key
        if not _pressed_modifiers and _is_blocked_single(key):
            root.after(0, lambda: msg_label.configure(
                text=f"Can't use {display} alone \u2014 conflicts with typing.\n"
                     f"Use a function key (F1-F12) or add a modifier (Ctrl+Shift+...)",
            ))
            return

        # Build combo string if modifiers are held
        if _pressed_modifiers:
            mod_names: list[str] = sorted(_pressed_modifiers)
            mod_display: str = "+".join(
                m.replace("_l", "").replace("_r", "").replace("_gr", "").title()
                for m in mod_names
            )
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

    root.protocol(
        "WM_DELETE_WINDOW",
        lambda: (listener[0].stop() if listener[0] else None, root.destroy()),
    )
    root.mainloop()

    if result["code"]:
        set_setting(
            "global_dictation_hotkey",
            {"code": result["code"], "display": result["display"]},
        )
        if _tray_icon_ref and _build_menu_cb:
            _tray_icon_ref.menu = _build_menu_cb()
            _tray_icon_ref.update_menu()

        # Restart hotkey listener with new key
        if _hotkey_listener:
            _hotkey_listener.stop()
        _start_hotkey_listener()
