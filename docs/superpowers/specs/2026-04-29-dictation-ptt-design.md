# Dictation Mode — Bug Fixes & Push-to-Talk

**Date:** 2026-04-29
**Status:** Approved

---

## Overview

Fix two bugs in dictation standalone mode (broken WebSocket connection, missing i18n translations) and add a push-to-talk feature with configurable hotkey and toggle/hold modes.

---

## Section 1: Bug Fixes

### Bug 1 — WebSocket disconnects immediately after "Start Dictation"

**Root cause:** `dictation.html` fetches `/api/config` on load (line 355) to get `ui_language` before calling `connect()`. The `dictation_app` in `server.py` does not have a `/api/config` endpoint. The fetch fails, falls to the `.catch(() => connect())` path, but by this time the timing/state is off. More critically, the `dictation_app` also lacks `/api/locales/{lang}` — so `loadTranslations()` fails silently, leaving `_i18n` empty.

**Fix:** Add two missing endpoints to `dictation_app` in `server.py`:

1. `GET /api/config` — returns the same config dict that `operator_app`'s `/api/config` returns (reads from the shared `cfg` dict).
2. `GET /api/locales/{lang}` — serves locale JSON files from the `locales/` directory, identical to the existing endpoint on `operator_app`.

These are read-only endpoints that return shared state — no new logic needed.

### Bug 2 — UI shows raw i18n keys ("Dictation.Start_BTN")

**Root cause:** Same as Bug 1. Without `/api/locales/{lang}`, `loadTranslations()` fails and the `t()` function falls back to returning the raw key string. The `data-i18n` attributes on toolbar buttons never get their text replaced.

**Fix:** Resolved by Bug 1 fix. Once `/api/locales/{lang}` works, translations load correctly and `t()` returns proper text.

---

## Section 2: Toggle/Hold Mode Switch

### Toolbar layout

Add a mode toggle directly in the toolbar (not buried in settings), positioned right after the Start/Stop button:

```
[Start] [Toggle ⇄ Hold] [PTT: Space] [Change]  |  Status...  |  0 words  [Copy] [Save] [Clear]
```

### Mode toggle behavior

- **Toggle mode** (default, current behavior): Click Start to begin dictation, click Stop to end. Push-to-talk key acts as a shortcut for the Start/Stop button.
- **Hold mode**: Dictation is active only while the PTT key is held down. Releasing the key stops dictation (after grace period).

### UI element

- A segmented button or toggle switch labeled "Toggle" / "Hold"
- Styled consistently with existing `.btn` classes
- Current mode stored in `localStorage` key `lt_dictation_mode` (values: `"toggle"` or `"hold"`)
- Mode change takes effect immediately — no restart needed

---

## Section 3: Hotkey Capture & Display

### Key assignment flow

1. User clicks **[Change]** button next to the PTT key label
2. Button text changes to "Press any key..." with a pulsing accent border
3. User presses any key — captured via `keydown` event
4. If key is blocked (conflicts with hardcoded shortcuts), show inline message: _"Can't use [key] because it's already used for [function]"_ — stay in capture mode
5. If key is valid, save it and update the display label

### Persistent key display

- The assigned key is always visible in the toolbar: `PTT: Space` (or whatever key is assigned)
- Uses `KeyboardEvent.key` for display (human-readable: "Space", "F5", "Control")
- Uses `KeyboardEvent.code` for storage and matching (unambiguous: "Space", "F5", "ControlLeft")
- Stored in `localStorage` key `lt_ptt_key` (stores the `.code` value)
- Default: no key assigned — shows `PTT: [none]` with the **[Set]** button

### Blocked keys with explanations

Keys that conflict with existing dictation.html functionality must be blocked. There are no global keyboard shortcuts in dictation mode currently, but the following are reserved:

| Key | Reason |
|-----|--------|
| Enter | "Used for setting the save directory" |
| Tab | "Browser focus navigation" |
| Escape | "Browser standard — cancel/close" |

Additionally, modifier-only keys (Shift, Control, Alt, Meta alone) are blocked: _"Modifier keys can't be used alone as a PTT key"_

The blocked-key list and messages are defined as a JS object for easy maintenance.

### Key combinations

Single keys only for PTT — no modifier combinations (Ctrl+X, Alt+F, etc.). If user presses a key while a modifier is held, capture only the non-modifier key.

---

## Section 4: Hold-to-Talk Runtime

### Core mechanic

When mode is "Hold" and a PTT key is assigned:

- **keydown** (PTT key): If not already active, send `set_dictation_active: true` via WebSocket. Update UI to show red/live state.
- **keyup** (PTT key): Start a 750ms grace timer. If the timer expires without another keydown, send `set_dictation_active: false`. Update UI back to green/idle.
- **keydown during grace period**: Cancel the timer, stay active. This handles key repeat and brief finger lifts.

### Grace period (~750ms)

- Prevents dictation from cutting off during brief finger lifts or key-repeat gaps
- Implemented as a `setTimeout` / `clearTimeout` pair
- Duration: 750ms (not configurable in v1)
- Visual: UI stays in "live" state during grace period — no flicker

### Toggle mode with PTT key

When mode is "Toggle" and a PTT key is assigned, the key simply acts as a toggle shortcut — equivalent to clicking the Start/Stop button. No grace period in toggle mode.

### Focus behavior

- PTT key events are captured at the `document` level
- When the editor textarea has focus, PTT key events must call `e.preventDefault()` to prevent the key character from being typed
- Exception: if no PTT key is assigned, all keys pass through to the editor normally

### State diagram

```
IDLE ──[keydown]──► ACTIVE ──[keyup]──► GRACE (750ms timer)
 ▲                                         │
 │                    ▲                    │
 │                    └──[keydown]─────────┘
 │                                         │
 └────────────[timer expires]──────────────┘
```

---

## Files to modify

| File | Changes |
|------|---------|
| `server.py` | Add `GET /api/config` and `GET /api/locales/{lang}` to `dictation_app` |
| `dictation.html` | Add mode toggle UI, PTT key display/capture, hold-to-talk runtime logic, blocked key validation |
| `locales/en.json` | Add new i18n keys for PTT UI elements |
| `locales/*.json` | Add placeholder keys (English fallback) for all other locale files |

---

## Out of scope

- PTT key configuration UI in operator.html (dictation is standalone)
- Audio-level PTT indicator / visual waveform
- Configurable grace period duration
- Multi-key combinations for PTT
- Server-side PTT key storage (localStorage only for v1)
