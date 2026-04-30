# Dictation Bug Fixes & Push-to-Talk Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix dictation mode's broken WebSocket/i18n and add push-to-talk with configurable hotkey, toggle/hold modes, and a 750ms grace period.

**Architecture:** Add missing `/api/config` and `/api/locales/{lang}` endpoints to `dictation_app` in server.py (mirrors operator_app). All PTT logic lives in dictation.html — mode toggle, key capture, hold-to-talk state machine, blocked-key validation. Settings persist to localStorage.

**Tech Stack:** Python/FastAPI (server endpoints), vanilla JS (PTT frontend), JSON (locale files)

**Spec:** `docs/superpowers/specs/2026-04-29-dictation-ptt-design.md`

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `server.py` | Modify (lines ~2188-2245) | Add `/api/config` and `/api/locales/{lang}` to `dictation_app` |
| `dictation.html` | Modify | Add PTT toolbar UI, key capture, hold-to-talk runtime, mode toggle |
| `locales/en.json` | Modify | Add ~15 new PTT i18n keys |
| `locales/*.json` (27 files) | Modify | Add same keys with English fallback values |

---

### Task 1: Add missing endpoints to dictation_app

**Files:**
- Modify: `server.py:2188-2245` (dictation app section)

- [ ] **Step 1: Add `/api/config` endpoint to dictation_app**

Insert after line 2195 (after the existing `dict_config` function), before `@dictation_app.post("/api/dictation-config")`:

```python
@dictation_app.get("/api/config")
async def dict_main_config():
    """Return main config for dictation page (ui_language, etc.)."""
    return JSONResponse({
        "ui_language": config.get("ui_language", "EN"),
        "session_title": config.get("session_title", ""),
    })
```

This is a minimal subset — dictation only needs `ui_language` to load translations. Don't return the full operator config (API keys, translation slots, etc.) since dictation doesn't use them.

- [ ] **Step 2: Add `/api/locales/{lang}` endpoint to dictation_app**

Insert directly after the new `dict_main_config` function:

```python
@dictation_app.get("/api/locales/{lang}")
async def dict_get_locale(lang: str):
    """Serve translation JSON for a language."""
    locale_path = BASE_DIR / "locales" / f"{lang.lower()}.json"
    if locale_path.exists():
        return JSONResponse(json.loads(locale_path.read_text(encoding="utf-8")))
    en_path = BASE_DIR / "locales" / "en.json"
    if en_path.exists():
        return JSONResponse(json.loads(en_path.read_text(encoding="utf-8")))
    return JSONResponse({})
```

This is identical to the operator_app version at line 1529.

- [ ] **Step 3: Verify manually**

1. Start the server: `python server.py`
2. Open `http://localhost:3005` in a browser
3. Verify: toolbar buttons show "Start", "Stop", "Copy", etc. (not raw keys like "dictation.start_btn")
4. Verify: clicking Start shows red dot and "Listening..." status, stays connected
5. Verify: WebSocket stays open (no reconnecting flicker)

- [ ] **Step 4: Commit**

```bash
git add server.py
git commit -m "[fix] add /api/config and /api/locales to dictation_app

Dictation page fetches these on load for i18n. Without them,
translations fail silently (showing raw keys) and the WS
connection timing gets disrupted."
```

---

### Task 2: Add PTT i18n keys to en.json

**Files:**
- Modify: `locales/en.json` (after line 352, the last `dictation.*` key)

- [ ] **Step 1: Add new PTT keys to en.json**

Insert these entries after the `"dictation.insert_at_cursor_position"` line (line 352) and before the blank line / `"installer.*"` block:

```json
  "dictation.mode_toggle": "Toggle",
  "dictation.mode_hold": "Hold",
  "dictation.ptt_label": "PTT:",
  "dictation.ptt_none": "none",
  "dictation.ptt_set": "Set",
  "dictation.ptt_change": "Change",
  "dictation.ptt_capture_prompt": "Press any key...",
  "dictation.ptt_blocked_enter": "Can't use Enter — it's already used for setting the save directory",
  "dictation.ptt_blocked_tab": "Can't use Tab — it's used for browser focus navigation",
  "dictation.ptt_blocked_escape": "Can't use Escape — it's used for browser cancel/close",
  "dictation.ptt_blocked_modifier": "Modifier keys can't be used alone as a PTT key",
  "dictation.ptt_blocked_generic": "Can't use {key} — it's reserved",
  "dictation.status_nothing_to_copy": "Nothing to copy",
  "dictation.status_nothing_to_save": "Nothing to save",
```

Note: `status_nothing_to_copy` and `status_nothing_to_save` are already referenced in dictation.html (lines 200, 209) but were missing from en.json. Adding them here fixes those fallback strings too.

- [ ] **Step 2: Commit**

```bash
git add locales/en.json
git commit -m "[i18n] add PTT and missing dictation keys to en.json"
```

---

### Task 3: Add PTT i18n keys to all other locale files

**Files:**
- Modify: All 27 non-English locale files in `locales/` (ar, bg, cs, da, de, el, es, et, fi, fr, hu, id, it, ja, ko, lt, lv, nb, nl, pl, pt, ro, ru, sk, sl, sv, tr)

- [ ] **Step 1: Add the same keys with English fallback values to every locale file**

For each locale file, find the last `"dictation.*"` key and insert the same block after it. Use the English text as placeholders — they'll be translated later. The keys to add are the same 14 entries from Task 2, Step 1.

The most efficient approach: write a small Python script that reads each JSON file, inserts the keys, and writes it back preserving the existing structure.

```python
import json, glob, os

NEW_KEYS = {
    "dictation.mode_toggle": "Toggle",
    "dictation.mode_hold": "Hold",
    "dictation.ptt_label": "PTT:",
    "dictation.ptt_none": "none",
    "dictation.ptt_set": "Set",
    "dictation.ptt_change": "Change",
    "dictation.ptt_capture_prompt": "Press any key...",
    "dictation.ptt_blocked_enter": "Can't use Enter — it's already used for setting the save directory",
    "dictation.ptt_blocked_tab": "Can't use Tab — it's used for browser focus navigation",
    "dictation.ptt_blocked_escape": "Can't use Escape — it's used for browser cancel/close",
    "dictation.ptt_blocked_modifier": "Modifier keys can't be used alone as a PTT key",
    "dictation.ptt_blocked_generic": "Can't use {key} — it's reserved",
    "dictation.status_nothing_to_copy": "Nothing to copy",
    "dictation.status_nothing_to_save": "Nothing to save",
}

for path in sorted(glob.glob("locales/*.json")):
    if os.path.basename(path) == "languages.json":
        continue
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    for k, v in NEW_KEYS.items():
        if k not in data:
            data[k] = v
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
```

Run from project root: `python -c "<paste script above>"`

Then delete the script — it's one-shot.

- [ ] **Step 2: Verify a sample file**

Open `locales/fr.json` and confirm the new keys appear after the existing dictation keys. All values should be English (placeholders).

- [ ] **Step 3: Commit**

```bash
git add locales/
git commit -m "[i18n] add PTT placeholder keys to all locale files"
```

---

### Task 4: Add mode toggle and PTT key display to dictation.html toolbar

**Files:**
- Modify: `dictation.html` (toolbar HTML at lines 52-60, and CSS at lines 10-48)

- [ ] **Step 1: Add PTT CSS styles**

Insert before the closing `</style>` tag (line 48):

```css
.mode-toggle{display:inline-flex;border:1px solid var(--border);border-radius:5px;overflow:hidden;margin:0 4px}
.mode-toggle .mt-opt{padding:5px 10px;font-size:10px;font-weight:600;text-transform:uppercase;letter-spacing:.04em;color:var(--dim);background:transparent;border:none;cursor:pointer;font-family:inherit;transition:.15s}
.mode-toggle .mt-opt.active{background:var(--accent);color:#0f0f1a}
.mode-toggle .mt-opt:not(.active):hover{background:rgba(255,255,255,.08)}
.ptt-group{display:flex;align-items:center;gap:4px;margin:0 4px;font-size:11px}
.ptt-key{color:var(--accent);font-weight:600;font-variant-numeric:tabular-nums}
.ptt-msg{position:fixed;top:56px;left:50%;transform:translateX(-50%);background:#f44336;color:#fff;padding:6px 16px;border-radius:6px;font-size:12px;font-weight:500;z-index:100;opacity:0;transition:opacity .2s;pointer-events:none;white-space:nowrap}
.ptt-msg.show{opacity:1}
.btn-capture{animation:capturePulse 1.2s ease-in-out infinite;border-color:var(--accent)!important}
@keyframes capturePulse{0%,100%{box-shadow:0 0 0 0 rgba(79,195,247,.4)}50%{box-shadow:0 0 0 6px rgba(79,195,247,0)}}
```

- [ ] **Step 2: Add PTT toolbar HTML elements**

Replace the toolbar `<div>` (lines 52-60) with:

```html
<div class="toolbar">
  <div class="title"><span class="dot" id="dot"></span> <span data-i18n="dictation.toolbar_title">Dictation</span></div>
  <button class="btn btn-go" id="goBtn" onclick="toggleTranscribing()" data-i18n="dictation.start_btn">Start</button>
  <div class="mode-toggle" id="modeToggle">
    <button class="mt-opt active" id="mtToggle" onclick="setMode('toggle')" data-i18n="dictation.mode_toggle">Toggle</button>
    <button class="mt-opt" id="mtHold" onclick="setMode('hold')" data-i18n="dictation.mode_hold">Hold</button>
  </div>
  <div class="ptt-group">
    <span class="ptt-key" id="pttLabel" data-i18n="dictation.ptt_label">PTT:</span>
    <span class="ptt-key" id="pttKeyDisplay">none</span>
    <button class="btn" id="pttBtn" onclick="startCapture()" style="padding:3px 8px;font-size:10px" data-i18n="dictation.ptt_set">Set</button>
  </div>
  <span class="status" id="status" data-i18n="dictation.status_connecting">Connecting...</span>
  <span class="wordcount" id="wordcount" data-i18n="dictation.word_count">0 words</span>
  <button class="btn btn-accent" onclick="copyAll()" title="Copy all text to clipboard" data-i18n="dictation.copy_btn">Copy</button>
  <button class="btn btn-accent" onclick="saveFile()" title="Save as .txt file" data-i18n="dictation.save_btn">Save</button>
  <button class="btn btn-danger" onclick="clearAll()" title="Clear all text" data-i18n="dictation.clear_btn">Clear</button>
</div>
<div class="ptt-msg" id="pttMsg"></div>
```

- [ ] **Step 3: Commit**

```bash
git add dictation.html
git commit -m "[feat] add PTT mode toggle and key display to dictation toolbar"
```

---

### Task 5: Add mode toggle and PTT key persistence logic

**Files:**
- Modify: `dictation.html` (inside `<script>` block, at the top near line 80)

- [ ] **Step 1: Add PTT state variables and mode toggle logic**

Insert after line 82 (`let transcribing = false;`):

```javascript
let pttMode = localStorage.getItem('lt_dictation_mode') || 'toggle';
let pttCode = localStorage.getItem('lt_ptt_key') || '';
let pttDisplayName = localStorage.getItem('lt_ptt_key_display') || '';
let capturing = false;
let graceTimer = null;
const GRACE_MS = 750;

let BLOCKED_KEYS = {};
function rebuildBlockedKeys() {
  BLOCKED_KEYS = {
    'Enter':  t('dictation.ptt_blocked_enter'),
    'Tab':    t('dictation.ptt_blocked_tab'),
    'Escape': t('dictation.ptt_blocked_escape'),
  };
}
rebuildBlockedKeys();
const MODIFIER_CODES = ['ShiftLeft','ShiftRight','ControlLeft','ControlRight','AltLeft','AltRight','MetaLeft','MetaRight'];

function setMode(m) {
  pttMode = m;
  localStorage.setItem('lt_dictation_mode', m);
  document.getElementById('mtToggle').classList.toggle('active', m === 'toggle');
  document.getElementById('mtHold').classList.toggle('active', m === 'hold');
  if (m === 'toggle' && graceTimer) { clearTimeout(graceTimer); graceTimer = null; }
}

function updatePttDisplay() {
  const disp = document.getElementById('pttKeyDisplay');
  const btn = document.getElementById('pttBtn');
  if (pttCode) {
    disp.textContent = pttDisplayName || pttCode;
    btn.textContent = t('dictation.ptt_change');
    btn.setAttribute('data-i18n', 'dictation.ptt_change');
  } else {
    disp.textContent = t('dictation.ptt_none');
    btn.textContent = t('dictation.ptt_set');
    btn.setAttribute('data-i18n', 'dictation.ptt_set');
  }
}

function showPttMsg(msg) {
  const el = document.getElementById('pttMsg');
  el.textContent = msg;
  el.classList.add('show');
  setTimeout(() => el.classList.remove('show'), 3000);
}
```

- [ ] **Step 2: Add initialization call**

Find the init block at the bottom of the `<script>` (lines 351-358). Insert before `fetch('/api/config')`:

```javascript
// Restore PTT state from localStorage
setMode(pttMode);
updatePttDisplay();
```

`BLOCKED_KEYS` uses `t()` which returns raw keys before translations load. The `rebuildBlockedKeys()` function (defined in Step 1) handles this — call it again after translations load. Add these two lines at the end of the `loadTranslations` function (after line 101, `document.documentElement.dir=...`):

```javascript
    rebuildBlockedKeys();
    updatePttDisplay();
```

- [ ] **Step 3: Commit**

```bash
git add dictation.html
git commit -m "[feat] add PTT mode and key persistence logic"
```

---

### Task 6: Add key capture flow

**Files:**
- Modify: `dictation.html` (inside `<script>` block, after the functions from Task 5)

- [ ] **Step 1: Add the startCapture and key capture handler**

Insert after the `showPttMsg` function from Task 5:

```javascript
function startCapture() {
  capturing = true;
  const btn = document.getElementById('pttBtn');
  btn.textContent = t('dictation.ptt_capture_prompt');
  btn.classList.add('btn-capture');

  function onCapture(e) {
    e.preventDefault();
    e.stopPropagation();

    // Ignore modifier-only presses
    if (MODIFIER_CODES.includes(e.code)) {
      showPttMsg(t('dictation.ptt_blocked_modifier'));
      return;
    }

    // Check blocked keys (use e.key for matching since BLOCKED_KEYS uses key names)
    if (BLOCKED_KEYS[e.key]) {
      showPttMsg(BLOCKED_KEYS[e.key]);
      return;
    }

    // Valid key — save it
    pttCode = e.code;
    pttDisplayName = e.key === ' ' ? 'Space' : e.key;
    localStorage.setItem('lt_ptt_key', pttCode);
    localStorage.setItem('lt_ptt_key_display', pttDisplayName);
    capturing = false;
    btn.classList.remove('btn-capture');
    updatePttDisplay();

    document.removeEventListener('keydown', onCapture, true);
  }

  document.addEventListener('keydown', onCapture, true);
}
```

Key details:
- Uses capture phase (`true` third arg) so it fires before any other handler
- `e.key === ' '` → displays as "Space" (more readable than a blank)
- Stores both `.code` (for matching at runtime) and `.key` (for display)
- Blocked keys stay in capture mode so user can try another key
- Valid key exits capture mode immediately

- [ ] **Step 2: Verify manually**

1. Start server, open `http://localhost:3005`
2. Click "Set" button — should show "Press any key..." with pulsing border
3. Press Enter — should show red error message "Can't use Enter..."
4. Press Tab — should show "Can't use Tab..."
5. Press Space — should show "PTT: Space" and button changes to "Change"
6. Refresh page — PTT key should persist as "Space"

- [ ] **Step 3: Commit**

```bash
git add dictation.html
git commit -m "[feat] add PTT key capture with blocked-key validation"
```

---

### Task 7: Add hold-to-talk and toggle-PTT runtime

**Files:**
- Modify: `dictation.html` (inside `<script>` block, after the capture functions)

- [ ] **Step 1: Add the document-level keydown/keyup handlers for PTT**

Insert after the `startCapture` function from Task 6:

```javascript
document.addEventListener('keydown', function(e) {
  if (capturing) return;
  if (!pttCode || e.code !== pttCode) return;
  if (['INPUT','SELECT','TEXTAREA'].includes(e.target.tagName) && e.target !== editor) return;

  e.preventDefault();

  if (pttMode === 'toggle') {
    if (e.repeat) return;
    toggleTranscribing();
  } else {
    // Hold mode
    if (graceTimer) { clearTimeout(graceTimer); graceTimer = null; }
    if (!transcribing && ws && ws.readyState === 1) {
      transcribing = true;
      ws.send(JSON.stringify({ type: 'set_dictation_active', active: true }));
      updateGoBtn();
      updateInsertHint();
    }
  }
});

document.addEventListener('keyup', function(e) {
  if (capturing) return;
  if (!pttCode || e.code !== pttCode) return;
  if (pttMode !== 'hold') return;

  if (transcribing) {
    graceTimer = setTimeout(function() {
      graceTimer = null;
      if (transcribing && ws && ws.readyState === 1) {
        transcribing = false;
        ws.send(JSON.stringify({ type: 'set_dictation_active', active: false }));
        updateGoBtn();
        updateInsertHint();
      }
    }, GRACE_MS);
  }
});
```

Key details:
- `e.target !== editor` check: if user is typing in the save-dir input, PTT key passes through normally. But in the editor textarea, we prevent the PTT character from being typed via `preventDefault()`.
- `e.repeat` guard in toggle mode prevents rapid-fire toggling from key repeat.
- Hold mode: keydown starts dictation immediately (no delay). keyup starts the 750ms grace timer. Another keydown during grace cancels the timer.
- Both modes check `ws.readyState === 1` (OPEN) before sending.

- [ ] **Step 2: Verify hold mode manually**

1. Start server, open `http://localhost:3005`
2. Assign a PTT key (e.g., Space)
3. Switch to "Hold" mode
4. Hold Space — should show red dot, "Listening..."
5. Release Space — after ~750ms, should go back to green "Ready"
6. Tap Space quickly twice — should stay in listening mode (grace period bridges the gap)

- [ ] **Step 3: Verify toggle mode manually**

1. Switch to "Toggle" mode
2. Press Space once — should start dictation
3. Press Space again — should stop dictation
4. Click in the editor, type normally — PTT key character should NOT appear in text

- [ ] **Step 4: Verify editor focus behavior**

1. Click in editor textarea
2. Press PTT key — dictation toggles/holds but the key character does NOT appear in the text
3. Click in save-dir input, press PTT key — key character appears normally in the input (no interception)

- [ ] **Step 5: Commit**

```bash
git add dictation.html
git commit -m "[feat] add hold-to-talk and toggle-PTT runtime with 750ms grace period"
```

---

### Task 8: Final integration test and cleanup

- [ ] **Step 1: Full end-to-end test**

Start the server and test the complete flow:

1. Open `http://localhost:3005`
2. **i18n check:** All buttons show English text ("Start", "Copy", "Save", "Clear"), not raw keys
3. **Connection check:** Status shows "Ready" (not "Reconnecting...")
4. **Click Start:** Red dot appears, "Listening..." status, speaking produces text
5. **Click Stop:** Green dot, "Stopped" status
6. **Set PTT key:** Click Set → press F1 → shows "PTT: F1", button says "Change"
7. **Blocked key test:** Click Change → press Enter → error message, stays in capture mode → press F2 → saves "F2"
8. **Toggle mode PTT:** Press F2 → starts, press F2 → stops
9. **Hold mode PTT:** Switch to Hold → hold F2 → listens → release → stops after ~750ms
10. **Grace period:** Hold F2, quickly release and re-press → dictation continues without interruption
11. **Persistence:** Refresh page → mode and key still set correctly
12. **Editor typing:** While dictating, click in editor, type normal text — PTT key doesn't appear in editor

- [ ] **Step 2: Check for regressions**

1. Open operator panel (`http://localhost:3001`) — verify it still works normally
2. Open display (`http://localhost:3000`) — verify captions still show
3. Verify dictation's existing features: Copy, Save, Clear, save-dir change, word count, insert-at-cursor

- [ ] **Step 3: Commit any final fixes**

If any issues found during testing, fix and commit with descriptive message.

---

## Summary

| Task | Description | Files |
|------|-------------|-------|
| 1 | Fix missing dictation_app endpoints | `server.py` |
| 2 | Add PTT i18n keys to en.json | `locales/en.json` |
| 3 | Add PTT i18n keys to all locale files | `locales/*.json` |
| 4 | Add toolbar HTML/CSS for mode toggle + PTT display | `dictation.html` |
| 5 | Add mode toggle + PTT key persistence logic | `dictation.html` |
| 6 | Add key capture flow with blocked-key validation | `dictation.html` |
| 7 | Add hold-to-talk + toggle-PTT runtime | `dictation.html` |
| 8 | End-to-end integration test | all |
