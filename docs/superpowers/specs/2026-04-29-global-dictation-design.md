# Global Dictation — System-Wide Speech-to-Text

**Date:** 2026-04-29
**Status:** Approved

---

## Overview

Add system-wide push-to-talk dictation that injects transcribed speech as typed text into any focused application. A lightweight tray app captures global hotkeys, connects to the existing LinguaTaxi server via WebSocket, and injects received text word-by-word using OS-level keystroke simulation. The existing launcher gains minimize-to-tray capability.

---

## Section 1: Architecture Overview

### Components

1. **`tray_dictation.py`** (new) — Lightweight tray app + global dictation client
   - System tray icon via `pystray` with context menu
   - Global hotkey capture via `pynput.keyboard.Listener`
   - WebSocket client connecting to `ws://localhost:3005/ws`
   - Text injection via `pynput.keyboard.Controller` (word-by-word keystroke simulation)
   - Floating overlay window (tkinter `Toplevel`, always-on-top, no taskbar entry)
   - Reads/writes settings from the shared `launcher_settings.json`

2. **`launcher.pyw`** (modified) — Add minimize-to-tray capability
   - "Minimize to tray" option on window close (configurable)
   - Uses `pystray` for tray icon
   - Tray context menu: Show Window, Start/Stop Server, Open Operator, Open Dictation, Quit
   - When minimized to tray, launcher window hides but server keeps running

3. **Server (`server.py`)** — No changes needed
   - Existing dictation WebSocket endpoint, `set_dictation_active`, `broadcast_dictation()`, and `final` messages provide everything the tray app needs

### Data Flow

```
[User holds global PTT key]
  -> tray_dictation.py sends {type: "set_dictation_active", active: true} via WS
  -> server.py starts processing mic audio (only when captioning is paused)
  -> server.py sends {type: "final", text: "hello world"} via WS
  -> tray_dictation.py receives text, injects "hello" [space] "world" into focused app
[User releases key]
  -> (750ms grace period)
  -> tray_dictation.py sends {type: "set_dictation_active", active: false}
```

### Constraint

Global dictation only works when captioning is paused — same as in-browser dictation. The server's existing `dictation_active` flag gates this.

---

## Section 2: Tray App — Global Hotkey & Modes

### Global hotkey configuration

- Stored in `launcher_settings.json` under key `global_dictation_hotkey` (e.g., `{"code": "F8", "display": "F8"}` or `{"code": "ctrl+shift+d", "display": "Ctrl+Shift+D"}`)
- Supports single keys (F1-F12, etc.) and modifier combos (Ctrl+Shift+D, Alt+G, etc.)
- Configured via the tray context menu: "Set Global Hotkey..." opens a small capture dialog (press-any-key pattern, allows modifier combos)
- Default: no hotkey assigned — user must set one on first use

### Toggle vs Hold mode

- Switchable from tray context menu: "Mode: Hold" / "Mode: Toggle" (checkmark on active)
- Hold mode: dictation active while key is held, 750ms grace period on release
- Toggle mode: first press starts, second press stops
- Stored in `launcher_settings.json` under `global_dictation_mode` (`"hold"` or `"toggle"`)

### Blocked keys

- No blocking for modifier combos (Ctrl+Shift+X can't conflict with normal typing)
- For single keys: block letters, numbers, space, Enter, Tab, Escape, Backspace — anything that would conflict with normal typing in any app
- Only function keys (F1-F12), PrintScreen, ScrollLock, Pause, and modifier combos are allowed as single-key global hotkeys
- Error message shown in the capture dialog if a blocked key is pressed

---

## Section 3: Text Injection & Overlay

### Word-by-word injection

- When a `final` WebSocket message arrives with text, split on whitespace
- For each word: type the characters rapidly via `pynput.keyboard.Controller.type()`, then type a space
- `pynput`'s `type()` uses `SendInput` under the hood — each word appears near-instantly, with words arriving at natural speech cadence (as the server produces them)
- If the focused app changes mid-injection, remaining words go to whatever now has focus — expected behavior for simulated input

### Floating overlay indicator

- Small tkinter `Toplevel` window: ~200x40px, semi-transparent background
- Shows "Listening..." with a pulsing red dot when active
- Positioned in the bottom-right corner of the screen, above the taskbar
- Always-on-top (`topmost`), no taskbar entry (`overrideredirect`), click-through (non-focusable)
- Fades in on dictation start, fades out on stop
- Does NOT steal focus from the current app — critical for text injection to work

### Tray icon states

- Grey icon: server not connected / dictation unavailable
- Green icon: connected, idle, ready
- Red icon: dictation active (listening)
- Icon changes via `pystray` icon swap (pre-generated PIL images)

---

## Section 4: Launcher Minimize-to-Tray

### Window close behavior

- New setting in `launcher_settings.json`: `"close_to_tray": true` (default `true`)
- When user clicks the X button:
  - If `close_to_tray` is true: window hides, tray icon stays, server keeps running
  - If `close_to_tray` is false: existing behavior (stop server, quit)
- Setting accessible from launcher UI — a checkbox: "Minimize to tray on close"

### Launcher tray icon

- Uses `pystray`, same library as `tray_dictation.py` but separate process
- Context menu: Show Window, Start/Stop Server, Open Operator, Open Display, Open Dictation, separator, Quit
- "Quit" from tray menu fully stops the server and exits
- Double-click tray icon reopens the launcher window

### Coexistence with tray_dictation.py

- Both can run simultaneously — independent processes
- Launcher tray manages the server lifecycle
- Tray dictation manages global PTT
- They share `launcher_settings.json` for hotkey/mode config
- If the server isn't running when tray_dictation.py starts, it shows the grey icon and retries connection on a timer

### Start Menu shortcuts (installer)

- "LinguaTaxi" -> launches `launcher.pyw` (full GUI)
- "LinguaTaxi Dictation" -> launches `tray_dictation.py` (tray-only, starts server automatically if not running)
- `tray_dictation.py` can start the server subprocess itself (same logic as launcher) so it works standalone

---

## New Dependencies

| Package | Purpose | License |
|---------|---------|---------|
| `pystray` | System tray icon + context menu | LGPL-3.0 |
| `pynput` | Global hotkey capture + keystroke injection | LGPL-3.0 |
| `Pillow` | Icon image generation for tray (required by pystray) | MIT-like (HPND) |
| `websocket-client` | Sync WebSocket client for tray app | Apache-2.0 |

Note: `Pillow` may already be an indirect dependency. `websocket-client` is the sync counterpart to the `websockets` package already used server-side.

---

## Files to Create or Modify

| File | Action | Responsibility |
|------|--------|----------------|
| `tray_dictation.py` | Create | Tray app: hotkey, WS client, text injection, overlay |
| `launcher.pyw` | Modify | Add minimize-to-tray, pystray integration |
| `launcher_settings.json` | Modify (schema) | New keys: `global_dictation_hotkey`, `global_dictation_mode`, `close_to_tray` |
| `build/windows/build.bat` | Modify | Add `pystray`, `pynput`, `Pillow`, `websocket-client` to venv installs |
| `build/windows/installer.iss` | Modify | Add "LinguaTaxi Dictation" Start Menu shortcut |
| `requirements.txt` | Modify | Add new dependencies |

---

## Out of Scope

- macOS support for global hotkeys (requires accessibility permissions — separate effort)
- Linux support (different tray APIs — separate effort)
- Interim/partial text display in overlay (only final text is injected)
- Global dictation while captioning is active (uses shared audio pipeline)
- Configuration UI beyond tray context menu (no settings window for v1)
- Per-application hotkey profiles
