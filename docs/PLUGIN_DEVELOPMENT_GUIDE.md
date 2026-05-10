# LinguaTaxi Plugin Development Guide

A complete guide to building drop-in plugins for LinguaTaxi — the real-time speech captioning and translation platform.

---

## Table of Contents

1. [What is a LinguaTaxi Plugin?](#what-is-a-linguataxi-plugin)
2. [Plugin Anatomy](#plugin-anatomy)
3. [Hello World: Your First Plugin](#hello-world-your-first-plugin)
4. [The `manifest.json` File](#the-manifestjson-file)
5. [Backend: `routes.py`](#backend-routespy)
6. [Frontend: `panel.html` / `panel.js` / `panel.css`](#frontend-panelhtml--paneljs--panelcss)
7. [Event System (Hooks)](#event-system-hooks)
8. [Plugin Settings](#plugin-settings)
9. [The Plugin Static File Server](#the-plugin-static-file-server)
10. [The 4x4 Audience Display Grid](#the-4x4-audience-display-grid)
11. [Plugin-Scoped Library Code (`lib/`)](#plugin-scoped-library-code-lib)
12. [Thread Safety and Performance](#thread-safety-and-performance)
13. [Persisting Plugin Data](#persisting-plugin-data)
14. [WebSocket Usage Inside Plugins](#websocket-usage-inside-plugins)
15. [Testing and Debugging](#testing-and-debugging)
16. [Distribution and Packaging](#distribution-and-packaging)
17. [Reference Plugin Walkthroughs](#reference-plugin-walkthroughs)
18. [Common Pitfalls](#common-pitfalls)
19. [API Surface Cheat Sheet](#api-surface-cheat-sheet)

---

## What is a LinguaTaxi Plugin?

A **plugin** is a self-contained folder dropped into `plugins/` that extends LinguaTaxi with new functionality — things like fact checking, donor data, polling overlays, audience dial tests, etc.

Each plugin may ship:

- A **FastAPI router** mounted on the operator server (port 3001).
- A **panel UI** injected into the operator's control panel.
- Static assets (JS/CSS/images/etc.) served to operator, main display, and extended display.
- A **manifest** describing its capabilities and configurable settings.
- An **event handler** that receives real-time transcription, translation, and session events.

Plugins are discovered at startup, loaded into an isolated module namespace, and hot-reloaded on settings changes via an event hook. You never need to edit the core server to ship a plugin.

---

## Plugin Anatomy

A minimal plugin directory:

```
plugins/
└── my_plugin/
    ├── manifest.json       (required — declares plugin metadata + hooks)
    ├── routes.py           (optional — FastAPI router + event handler)
    ├── panel.html          (optional — HTML fragment for operator panel)
    ├── panel.js            (optional — JS served at /plugins/my_plugin/panel.js)
    ├── panel.css           (optional — CSS served at /plugins/my_plugin/panel.css)
    ├── lib/                (optional — helper modules, auto-namespaced)
    │   └── helpers.py
    └── data/               (optional — runtime data/cache files)
```

**Required:** only `manifest.json`. Everything else is opt-in.

**Plugin ID rules:** the `id` must match `^[a-z0-9_-]+$`. Lowercase letters, digits, underscores, and hyphens only. This is enforced at discovery time; invalid IDs are rejected with a warning.

---

## Hello World: Your First Plugin

Let's build the smallest possible plugin that logs every final transcript line.

### 1. Create `plugins/hello_world/manifest.json`

```json
{
  "id": "hello_world",
  "name": "Hello World",
  "version": "1.0.0",
  "description": "Logs every final transcript line to the console.",
  "author": "Your Name",
  "hooks": ["on_final", "on_shutdown"],
  "has_routes": true,
  "has_panel": true,
  "settings_schema": {
    "greeting": {
      "type": "text",
      "label": "Greeting to prefix log messages",
      "default": "Hello"
    }
  }
}
```

### 2. Create `plugins/hello_world/routes.py`

```python
"""Hello World plugin — logs every final transcript line."""
import logging
from fastapi import APIRouter
from fastapi.responses import JSONResponse

log = logging.getLogger("livecaption")
router = APIRouter(prefix="/api")

_plugin_settings = {}
_count = 0


@router.get("/hello/status")
async def status():
    return JSONResponse({
        "greeting": _plugin_settings.get("greeting", "Hello"),
        "lines_seen": _count,
    })


def handle_event(event_name, data, settings):
    """Called by the PluginDispatcher for every subscribed hook."""
    global _plugin_settings, _count
    _plugin_settings = settings

    if event_name == "on_final":
        _count += 1
        greeting = settings.get("greeting", "Hello")
        speaker = data.get("speaker") or "Unknown"
        text = data.get("text", "")
        log.info(f"[hello_world] {greeting}, {speaker}! You said: {text!r}")

    elif event_name == "on_shutdown":
        log.info(f"[hello_world] Shutting down. Processed {_count} lines.")
```

### 3. Create `plugins/hello_world/panel.html`

```html
<div class="hw-body">
  <p id="hw-count">Waiting for transcripts...</p>
  <button id="hw-refresh">Refresh Count</button>
</div>
```

### 4. Create `plugins/hello_world/panel.js`

```javascript
(function() {
  let count = 0;

  document.addEventListener('DOMContentLoaded', () => {
    const btn = document.getElementById('hw-refresh');
    if (btn) btn.addEventListener('click', refreshCount);
  });

  async function refreshCount() {
    const r = await fetch('/api/hello/status');
    const s = await r.json();
    const el = document.getElementById('hw-count');
    if (el) el.textContent = `Lines seen: ${s.lines_seen}`;
  }

  window.LinguaTaxi.plugins.register('hello_world', {
    on_final: (data) => {
      count++;
      const el = document.getElementById('hw-count');
      if (el) el.textContent = `Lines seen (local): ${count}`;
    },
    on_enabled:  () => console.log('[hello_world] enabled'),
    on_disabled: () => console.log('[hello_world] disabled'),
  });
})();
```

### 5. Restart the server

Stop and relaunch LinguaTaxi. In the operator panel you will see the **Hello World** panel with a toggle, a settings gear (for the `greeting` field), and your button. Check the server logs after speaking a sentence.

That's it — a fully working plugin in four small files.

---

## The `manifest.json` File

This is the plugin descriptor. It is parsed at discovery time and cached as a `PluginManifest` object.

### Required fields

| Field | Type | Description |
|---|---|---|
| `id` | string | Unique identifier. Must match `^[a-z0-9_-]+$`. Used in URLs, module names, DOM attrs. |
| `name` | string | Human-readable display name (shown in the panel header). |
| `version` | string | Semantic version, e.g. `1.0.0`. |

### Optional fields

| Field | Type | Default | Description |
|---|---|---|---|
| `description` | string | `""` | Shown in plugin listings. |
| `author` | string | `""` | Author/attribution string. |
| `hooks` | array | `[]` | List of event names the backend subscribes to (see [Event System](#event-system-hooks)). |
| `has_routes` | boolean | `false` | If `true`, LinguaTaxi will import `routes.py` and mount its `router`. |
| `has_panel` | boolean | `false` | If `true`, LinguaTaxi will inject `panel.html` into the operator page. |
| `route_prefix` | string | `/api/plugins/{id}` | Informational only — the actual prefix comes from your `APIRouter(prefix=...)`. Used in listings for clarity. |
| `settings_schema` | object | `{}` | Schema for the settings UI (see [Plugin Settings](#plugin-settings)). |

### Example

```json
{
  "id": "my_plugin",
  "name": "My Plugin",
  "version": "1.0.0",
  "description": "Does something useful.",
  "author": "Jane Doe",
  "hooks": ["on_final", "on_config_change", "on_shutdown"],
  "has_routes": true,
  "has_panel": true,
  "route_prefix": "/api/myplugin",
  "settings_schema": {
    "api_key": { "type": "password", "label": "API Key", "default": "" },
    "rate_limit": { "type": "number", "label": "Max calls/min", "default": 10 }
  }
}
```

---

## Backend: `routes.py`

Your backend file must define a module-level `router` of type `fastapi.APIRouter`. LinguaTaxi imports the file via `importlib` under the module name `plugin_{id}_routes` and calls `operator_app.include_router(router)`.

### Basic skeleton

```python
"""My plugin — what it does."""
import logging
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

log = logging.getLogger("livecaption")

# Prefix ALL routes with /api — LinguaTaxi mounts the router on operator_app as-is.
router = APIRouter(prefix="/api")

# Per-plugin settings, kept in sync via handle_event(on_config_change).
_plugin_settings = {}


@router.get("/myplugin/status")
async def status():
    return JSONResponse({"ok": True})


@router.post("/myplugin/do-thing")
async def do_thing(request: Request):
    data = await request.json()
    # ... do work ...
    return JSONResponse({"result": "done"})


def handle_event(event_name, data, settings):
    """Optional. Called by PluginDispatcher for each subscribed hook."""
    global _plugin_settings
    _plugin_settings = settings  # keep settings fresh on every call

    if event_name == "on_final":
        text = data.get("text", "")
        speaker = data.get("speaker") or ""
        # ... react to transcript ...

    elif event_name == "on_shutdown":
        # Clean up thread pools, close files, cancel tasks, etc.
        pass
```

### Key points

- **Always use `APIRouter(prefix="/api")`**, then add the sub-prefix inside each route (e.g. `/myplugin/status`). The operator app does **not** add anything automatically.
- **Routes run on the operator app (port 3001).** Audience displays are intentionally read-only — don't try to mount on them.
- **`handle_event` is optional.** It is only called for hooks listed in `manifest.json["hooks"]`.
- **`handle_event` runs on a shared thread pool** (4 workers, `thread_name_prefix="plugin"`). Keep it snappy or offload to your own pool for long work.
- **Exceptions in `handle_event` are caught and logged**, and the error message is attached to the plugin (shown as a `!` badge in the panel). Your plugin won't crash the server.

### Importing modules from your plugin folder

Because plugins are loaded via `importlib`, `from .helpers import ...` won't work. Use one of these patterns:

**Pattern A — Sibling file with `importlib.util`:**

```python
import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "helpers", str(Path(__file__).parent / "helpers.py"))
_helpers = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_helpers)
do_math = _helpers.do_math
```

**Pattern B — `lib/` directory (preferred for multi-file code):**

Drop files in `plugins/my_plugin/lib/`. LinguaTaxi registers this as a namespace package named `plugin_my_plugin_lib`. Import with:

```python
from plugin_my_plugin_lib.helpers import do_math
from plugin_my_plugin_lib.utils import something_else
```

This keeps each plugin's helpers isolated — two plugins can both have `lib/utils.py` without colliding.

---

## Frontend: `panel.html` / `panel.js` / `panel.css`

### `panel.html`

An HTML **fragment** — **not** a full document. LinguaTaxi wraps it in a panel container:

```html
<div class="plugin-panel" data-plugin-id="my_plugin">
  <div class="plugin-header" onclick="LinguaTaxi.plugins.togglePanel('my_plugin')">
    <div class="plugin-header-left">
      <span class="plugin-indicator" id="plugin-indicator-my_plugin"></span>
      <span class="plugin-title">My Plugin</span>
    </div>
    <div class="plugin-header-right">
      <!-- settings gear (if settings_schema present) -->
      <!-- enable/disable toggle -->
      <!-- collapse chevron -->
    </div>
  </div>
  <div class="plugin-body" id="plugin-body-my_plugin">
    <!-- YOUR panel.html CONTENTS GO HERE -->
  </div>
</div>
```

Keep your fragment small — a few divs, inputs, buttons, result containers. The header and settings dialog are rendered for you.

### `panel.js`

Served at `/plugins/{id}/panel.js` on operator, main display, and extended display. On the operator page it is included via a `<script src="...">` tag. Your script must wait for DOM ready before touching elements, and register with `window.LinguaTaxi.plugins`:

```javascript
(function() {
  // ... your state ...

  document.addEventListener('DOMContentLoaded', () => {
    // attach listeners, initial render, etc.
  });

  window.LinguaTaxi.plugins.register('my_plugin', {
    on_final:        (data) => {/* new final transcript */},
    on_interim:      (data) => {/* interim transcript */},
    on_translation:  (data) => {/* new translation */},
    on_enabled:      ()     => {/* plugin turned on */},
    on_disabled:     ()     => {/* plugin turned off */},
    on_session_start:()     => {/* captioning started */},
    on_session_stop: ()     => {/* captioning paused */},
    on_auto_speaker_change: (data) => {/* voice ID switch */},
  });
})();
```

The object passed to `register` is a map of event names to handler functions. Any unlisted event is simply not dispatched to your plugin. Exceptions inside a handler are caught and logged to the browser console — they won't break the page.

### `panel.css`

Served at `/plugins/{id}/panel.css`. Included on the operator page with a `<link rel="stylesheet">`. Scope your rules to a unique prefix (e.g. `.myplugin-*`) so you don't collide with LinguaTaxi's core styles or other plugins.

The panel shell (header, toggle, chevron, settings gear) is already styled by `static/plugin_panel.css` using the generic `.plugin-*` classes — don't restyle those unless you really have to.

---

## Event System (Hooks)

Events fire in two places: **backend** (Python, via `handle_event`) and **frontend** (JavaScript, via `LinguaTaxi.plugins.register`). Both sides see the same event names, but payloads may differ slightly (backend payloads carry more detail like `source_id` and `detected_lang`).

### Backend hooks — declared in `manifest.json["hooks"]`

| Event | Payload | When it fires |
|---|---|---|
| `on_final` | `{text, speaker, color, source_id, line_id, detected_lang}` | A final transcript line is ready (after silence cutoff or buffer flush). |
| `on_interim` | `{text, speaker, ...}` | An interim (partial) transcript update. Fires frequently. |
| `on_translation` | `{translated, lang, slot, speaker, line_id, source_lang}` | A translation has just been produced for a caption line. |
| `on_config_change` | `{config}` or `{plugin_id}` | Main config saved, or this plugin's settings were just saved. Use this to refresh cached settings. |
| `on_session_start` | `{}` | Operator pressed "Go Live" (captioning resumed). |
| `on_session_stop` | `{timestamp}` | Operator paused captioning. |
| `on_speaker_enrolled` | `{speaker, source_id}` | Voice ID registered a new speaker's voice profile. |
| `on_auto_speaker_change` | `{speaker, previous, confidence, source_id}` | Voice ID auto-switched the current speaker. |
| `on_shutdown` | `{}` | Server is shutting down — flush state, close files, join threads. |

Backend event dispatch is **asynchronous** — events run on a 4-worker `ThreadPoolExecutor` so they don't block the audio loop. Don't assume they fire in the same order you might expect, and don't block for long inside them.

### Frontend hooks — registered via `LinguaTaxi.plugins.register(...)`

| Event | Payload | When it fires |
|---|---|---|
| `on_final` | `{text, speaker, line_id, detected_lang}` | Operator WS received a final caption. |
| `on_interim` | `{text, speaker}` | Operator WS received an interim caption. |
| `on_translation` | `{translated, lang, slot, speaker, line_id}` | Operator WS received a translation. |
| `on_session_start` | `{}` | Captioning resumed. |
| `on_session_stop` | `{}` | Captioning paused. |
| `on_auto_speaker_change` | `{speaker, previous, confidence}` | Voice ID switched speaker. |
| `on_enabled` | `{pluginId}` | Operator toggled the plugin **on**. |
| `on_disabled` | `{pluginId}` | Operator toggled the plugin **off**. |

Frontend events are synchronous — they run in a try/catch loop over all registered plugins.

### Which side should listen?

- **Need to call APIs (OpenAI, Anthropic, OpenSecrets, etc.) or touch the filesystem?** Use the backend hook.
- **Need to update the operator panel UI?** Use the frontend hook.
- **Need both?** Listen on the backend to do the work, then broadcast results to connected operator panels (via your own WebSocket or by exposing a `GET /api/myplugin/results` endpoint that the frontend polls or fetches on demand).

---

## Plugin Settings

Declare settings in `manifest.json["settings_schema"]`. The operator will see a gear icon in your panel header; clicking it opens a modal with auto-generated inputs.

### Supported field types

| `type` | Rendered as | Value type in Python |
|---|---|---|
| `text` | `<input type="text">` | `str` |
| `password` | `<input type="password">` | `str` |
| `number` | `<input type="number">` | `int` or `float` |
| `toggle` | `<input type="checkbox">` | `bool` |

### Schema shape

```json
"settings_schema": {
  "api_key": {
    "type": "password",
    "label": "My Service API Key",
    "default": ""
  },
  "enabled_feature": {
    "type": "toggle",
    "label": "Enable the fancy feature",
    "default": false
  },
  "threshold": {
    "type": "number",
    "label": "Alert threshold (0–100)",
    "default": 50
  }
}
```

### How settings are delivered to your plugin

- **Backend — `handle_event`:** Called with the current settings dict on every event (the dispatcher fetches them fresh from `config.get("plugin_settings", {}).get(plugin_id, {})`). You also receive an initial `on_config_change` event at plugin load time so startup code sees saved values. Save to a module-global if you want cached access:

  ```python
  _plugin_settings = {}

  def handle_event(event_name, data, settings):
      global _plugin_settings
      _plugin_settings = settings
      # ...
  ```

- **Backend — `plugin_api` (read + write):** The loader injects a `plugin_api` object into your module's globals. Use it any time to read or update your plugin's own settings without waiting for an event:

  ```python
  # Read
  api_key = plugin_api.get_setting("api_key", "")
  full    = plugin_api.read_settings()

  # Write (persists to config.json and fires on_config_change)
  plugin_api.update_setting("last_run", time.time())
  plugin_api.write_settings({"api_key": "...", "rate_limit": 10})
  ```

  `plugin_api` is scoped to your plugin — it can only read/write your own settings, not another plugin's. Writes persist to `config.json` atomically and fire `on_config_change` so any listeners (including your own `handle_event`) see the update.

- **Frontend:** Call `await LinguaTaxi.plugins.getSettings('my_plugin')` to fetch the current values dict. This returns `{}` if nothing is saved yet.

### How settings are persisted

Settings live in `config.json` under `plugin_settings.{plugin_id}`. They survive restarts. LinguaTaxi writes to this file atomically whenever an operator hits "Save" in the settings dialog.

### Server-side endpoints

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/plugins/{id}/settings` | Returns `{schema, values}`. |
| `POST` | `/api/plugins/{id}/settings` | Form-encoded body; updates values and fires `on_config_change`. |
| `POST` | `/api/plugins/{id}/enabled` | Form field `enabled=true|false`. |
| `GET` | `/api/plugins` | List all plugins + manifests + enabled state. |

Numeric fields are auto-cast on POST (int if whole, float otherwise), and toggles are coerced from `"true"|"1"|"on"` to booleans.

---

## The Plugin Static File Server

Every file inside your plugin folder (other than dotfiles and `__pycache__`) is reachable at:

```
/plugins/{plugin_id}/{filename}
```

**Served on all three apps** — operator (3001), main display (3000), extended display (3002) — so you can load plugin assets on audience displays too.

### Security

The static handler only serves files whose **suffix** is in the allowlist (`_PLUGIN_STATIC_EXTS` in `server.py`), and uses `Path.resolve()` + prefix check to prevent directory traversal. If you need a new file type served (e.g. `.webmanifest`), propose an addition to `server.py` via PR.

### Example use cases

- Fonts, icons, audio clips: `/plugins/my_plugin/assets/sound.mp3`
- Additional JS modules loaded by your panel: `<script src="/plugins/my_plugin/helpers.js">`
- A standalone HTML page (e.g. mobile audience page): your router can return `HTMLResponse(Path("audience.html").read_text())` and reference `/plugins/my_plugin/audience.css` for styling.

---

## The 4x4 Audience Display Grid

LinguaTaxi's main and extended audience displays render a **4×4 tile grid**. Each tile can be a built-in widget (live captions, translation slot) or a plugin panel. The operator designs the grid in the operator panel and saves layouts as named profiles.

### How a plugin becomes a grid tile

By default, every enabled plugin with `has_panel: true` appears in the operator's plugin palette. The operator drags it into any grid cell on main or extended, and the plugin's `.plugin-panel` element is **moved** (not cloned — DOM identity is preserved) into that cell on the audience display. When the plugin is removed from the grid, the node moves back to its hidden container.

### Implications for your panel UI

- **Don't assume a fixed parent.** Your panel's root `.plugin-panel` will be relocated between containers. Use relative positioning; don't rely on `document.querySelector(...)` against a specific ancestor selector.
- **Keep operator-only controls distinguishable.** If your panel has buttons that only make sense on the operator page (e.g. "Start Tunnel"), you can detect the audience context by checking `window.location.port` or a `data-audience-mode` attribute set on the grid cell.
- **Style responsively.** Grid tiles vary in size based on how the operator resizes them. Favor flex/grid layouts, relative units, and overflow handling.

### Display grid API

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/display-grids` | Returns `{main: {...}, extended: {...}}`. Available on all three apps. |
| `POST` | `/api/display-grids` | Operator-only. Updates grids, persists to config, broadcasts `display_grid_change` WS event on both display apps. |

The WebSocket message looks like:

```json
{"type": "display_grid_change", "display": "main", "grid": { ... }}
```

Subscribe to this on the display side to re-render when the operator changes the layout.

---

## Plugin-Scoped Library Code (`lib/`)

If you have more than one or two helper files, drop them in `plugins/my_plugin/lib/`. LinguaTaxi will register that directory as a Python namespace package named **`plugin_{id}_lib`**.

```
plugins/my_plugin/
├── manifest.json
├── routes.py
└── lib/
    ├── parser.py
    └── cache.py
```

In `routes.py`:

```python
from plugin_my_plugin_lib.parser import parse_text
from plugin_my_plugin_lib.cache import Cache
```

**Why this matters:** if two plugins both had a file called `utils.py`, plain `importlib` would create two modules called `utils` — the second to load would overwrite the first. The `plugin_{id}_lib` prefix makes each plugin's helpers a unique module tree.

---

## Thread Safety and Performance

### What runs on which thread

- **FastAPI routes** (`@router.get`/`@router.post`) run on the Uvicorn event loop. Use `async def` freely; use `run_in_executor` for blocking work.
- **`handle_event` callbacks** run on a shared `ThreadPoolExecutor` with **4 workers** (`thread_name_prefix="plugin"`). They are **not** on the event loop.
- **Audio capture/transcription loops** run on their own threads. They call `plugin_dispatcher.fire(...)` non-blockingly.

### Guidelines

- **Never call blocking I/O directly on the event loop.** Wrap with `await asyncio.get_event_loop().run_in_executor(None, blocking_fn)`.
- **Lock shared state.** If `handle_event` mutates module-globals and your routes read them, use `threading.Lock` or `threading.RLock`.
- **Create your own thread pool for heavy work.** The shared 4-worker pool is for light callbacks. For fact-check API calls, speech analysis, etc., spin up a dedicated `ThreadPoolExecutor` sized to your workload.
- **Clean up on `on_shutdown`.** Call `pool.shutdown(wait=False)`, cancel timers, flush any pending writes.

### Rate limiting

There's no built-in rate limiter — you are responsible for yours. The reference plugins use a token-bucket deque of timestamps:

```python
import collections, time, threading

_rate_lock = threading.Lock()
_rate_timestamps = collections.deque()
_RATE_WINDOW = 60

def _check_rate_limit(limit):
    now = time.monotonic()
    with _rate_lock:
        while _rate_timestamps and now - _rate_timestamps[0] > _RATE_WINDOW:
            _rate_timestamps.popleft()
        if len(_rate_timestamps) >= limit:
            return False
        _rate_timestamps.append(now)
        return True
```

---

## Persisting Plugin Data

- **Config values** (API keys, thresholds, toggles): store in `settings_schema`. Automatically persisted in `config.json`.
- **Cache files / datasets** (MBFC DB, speaker dossiers, etc.): create a `data/` folder inside your plugin and read/write there. Keep files small; respect the user's disk.
- **Large downloaded models**: prefer the LinguaTaxi `models/` directory at the repo root. Coordinate with the maintainers if you need bundling support in the Windows/macOS installer.

### Example: JSON cache inside the plugin folder

```python
from pathlib import Path
import json, threading

_DATA_DIR = Path(__file__).parent / "data"
_DATA_DIR.mkdir(exist_ok=True)
_CACHE_FILE = _DATA_DIR / "cache.json"
_cache_lock = threading.Lock()

def _load_cache():
    if _CACHE_FILE.exists():
        try:
            return json.loads(_CACHE_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}

def _save_cache(cache):
    with _cache_lock:
        _CACHE_FILE.write_text(json.dumps(cache), encoding="utf-8")
```

---

## WebSocket Usage Inside Plugins

FastAPI's `@router.websocket(...)` works normally. Use this when you need push-based two-way streams — e.g. an audience mobile page sending live slider values.

```python
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter(prefix="/api")
_clients = []
_lock = threading.Lock()

@router.websocket("/myplugin/stream")
async def stream_ws(ws: WebSocket):
    await ws.accept()
    with _lock:
        _clients.append(ws)
    try:
        while True:
            msg = await ws.receive_json()
            # handle msg
    except WebSocketDisconnect:
        pass
    finally:
        with _lock:
            if ws in _clients:
                _clients.remove(ws)
```

**Broadcasting from a non-async thread** (e.g. from `handle_event`):

```python
import asyncio
_loop = None  # capture the event loop on first WS connection

async def _broadcast(msg):
    dead = []
    for ws in list(_clients):
        try:
            await ws.send_json(msg)
        except Exception:
            dead.append(ws)
    for ws in dead:
        try: _clients.remove(ws)
        except ValueError: pass

# from worker thread:
asyncio.run_coroutine_threadsafe(_broadcast({"type": "update"}), _loop)
```

See `plugins/live_dial/routes.py` for a complete reference implementation including SSH tunnels, per-second aggregation, and cross-thread broadcasting.

---

## Testing and Debugging

### Log everything

Use the shared logger:

```python
import logging
log = logging.getLogger("livecaption")
log.info("...")
log.warning("...")
log.error("...")
```

Messages appear in the launcher's log pane and in the server's stdout.

### Panel error badges

If your `handle_event` raises, the exception message is truncated to 300 chars and stored on the plugin. A red `!` badge appears in the panel header with the error as its tooltip. This makes it easy to see when something is broken without digging through logs.

### Manual endpoint tests

```
# List plugins
curl http://localhost:3001/api/plugins

# Get your settings
curl http://localhost:3001/api/plugins/my_plugin/settings

# Save a setting
curl -X POST -F 'greeting=Howdy' http://localhost:3001/api/plugins/my_plugin/settings

# Enable / disable
curl -X POST -F 'enabled=false' http://localhost:3001/api/plugins/my_plugin/enabled

# Hit your own route
curl http://localhost:3001/api/myplugin/status
```

### Hot-reloading?

There is no hot reload. You must **restart the server** for code changes to take effect. Settings changes (via the UI or the POST endpoint) are hot — your plugin gets an `on_config_change` event without a restart.

### Disabled plugins

When an operator toggles the plugin off:
- Backend routes **stay mounted** — the plugin just stops getting events.
- `on_enabled` and `on_disabled` fire **only on the frontend** (from the in-page dispatcher). The backend does not receive them.
- If you need to know enabled-state on the backend, query `config.get("plugins_enabled", {}).get(plugin_id, True)` — but better to design so that unmounting the panel is enough.

---

## Distribution and Packaging

For now, LinguaTaxi plugins are distributed as folders committed to the repo under `plugins/`. To share a plugin:

1. Keep your plugin self-contained (no edits to core `server.py`, `launcher.pyw`, `operator.html`).
2. Pin any Python dependencies in a top-level `requirements.txt` patch (or document them in the plugin README).
3. Ship required data files inside the plugin folder (`data/`, `assets/`). Large model files should be downloaded lazily on first use.
4. If your plugin needs to be bundled in the Windows/macOS installer, coordinate with the build system (`build/windows/installer.iss`, `build/mac/build.sh`) to include your `plugins/your_plugin/` folder and any data files.

A future `plugin.zip` import flow is on the roadmap — for now, drop the folder into `plugins/` and restart.

---

## Reference Plugin Walkthroughs

The best way to learn is to read the bundled plugins. Here's what each one demonstrates.

### `plugins/fact_checker/`
**Complexity:** Advanced.
**Shows off:**
- Multi-provider dispatch (Claude + Gemini + Groq with weighted MAGI consensus).
- Rate limiting with a deque-based token bucket.
- A dedicated `ThreadPoolExecutor` for outbound API calls.
- Loading sibling Python files (`mbfc_data.py`, `flip_flop.py`) via `importlib.util`.
- Bundled dataset (Media Bias Fact Check) with remote fetch + local fallback.
- Long-running background prefetching triggered by `on_speaker_enrolled`.
- Pydantic response models for type-safe API responses.

### `plugins/donor_cloud/`
**Complexity:** Intermediate.
**Shows off:**
- External API proxy (OpenSecrets.org CRP API).
- In-memory TTL cache with different durations for success vs. failure.
- Startup prefetch of known entities, re-fetching if key or cycle changes.
- Thorough environment-variable fallbacks (`OPENSECRETS_API_KEY`, `OPENSECRETS_CYCLE`).

### `plugins/polls_checker/`
**Complexity:** Intermediate.
**Shows off:**
- Multi-provider AI routing (Gemini free / Claude paid).
- Pollster credibility tier database baked into the plugin.
- Minimal state — mostly a stateless API wrapper.

### `plugins/live_dial/`
**Complexity:** Advanced.
**Shows off:**
- Dedicated audience-facing WebSocket endpoint.
- Spawning and supervising subprocesses (SSH tunnel to `serveo.net`).
- Cross-thread async broadcasting with `asyncio.run_coroutine_threadsafe`.
- Standalone mobile HTML page served from the plugin folder.
- Per-second aggregation loop with supervisor.

---

## Common Pitfalls

**"My routes aren't getting hit"**
- Check `has_routes: true` in manifest.
- Check `router = APIRouter(prefix="/api")` — not `prefix="/"`.
- Check the server log for a line like `Plugin 'my_plugin' loaded (hooks: [...])`. If you see `Plugin 'my_plugin' failed to load: <error>`, fix that.
- Check `GET /api/plugins` — your plugin should be listed.

**"My panel doesn't render"**
- Check `has_panel: true` in manifest.
- Check `panel.html` exists and is UTF-8.
- Reload the operator page (Ctrl-Shift-R).

**"My panel.js `document.getElementById('…')` returns null"**
- Wrap DOM access in `document.addEventListener('DOMContentLoaded', () => { ... })`.

**"My settings aren't being saved"**
- Check the manifest's `settings_schema` syntax — each key must have `type`, `label`, `default`.
- `type` must be one of `text`, `password`, `number`, `toggle`. Typos silently fall back to text.

**"My handle_event isn't called"**
- Add the event name to `manifest.json["hooks"]` — hooks not listed aren't dispatched.
- Restart the server after changing the manifest.

**"Two plugins with the same helper filename conflict"**
- Put helpers in `lib/` and import as `plugin_{id}_lib.helpers`.

**"Changes to routes.py don't take effect"**
- There is no hot reload for Python. Restart the server.

**"Plugin ID not accepted"**
- IDs must match `^[a-z0-9_-]+$`. No uppercase, spaces, or dots.

**"I mutated the `config` dict directly — is that OK?"**
- No. Use the plugin settings API. The core server owns `config.json` and mutates it under a lock.

**"I broadcast from `handle_event` but got `RuntimeError: no running event loop`"**
- `handle_event` runs on a worker thread — there's no event loop. Capture the loop reference during WS accept, then `asyncio.run_coroutine_threadsafe(coro, loop)` from the worker.

---

## API Surface Cheat Sheet

| Surface | Who uses it | Entry point |
|---|---|---|
| `manifest.json` | LinguaTaxi loader | Plugin discovery |
| `routes.py` → `router` | Operator app | FastAPI `include_router` |
| `routes.py` → `handle_event(name, data, settings)` | Plugin dispatcher | Hook subscription |
| `routes.py` → `lib/` directory | Your own code | `plugin_{id}_lib.*` import |
| `panel.html` | Operator UI | Injected at `<!-- PLUGIN_PANELS -->` |
| `panel.js` | Operator UI | `<script src="/plugins/{id}/panel.js">` |
| `panel.css` | Operator UI | `<link rel="stylesheet" href="/plugins/{id}/panel.css">` |
| `window.LinguaTaxi.plugins.register(id, handlers)` | panel.js | Subscribe to frontend events |
| `window.LinguaTaxi.plugins.fire(name, data)` | panel.js | Manually trigger an event (rare) |
| `window.LinguaTaxi.plugins.getSettings(id)` | panel.js | Fetch current settings |
| `window.LinguaTaxi.plugins.openSettings(id)` | panel.js | Open settings dialog |
| `GET /api/plugins` | Any client | List all plugins |
| `GET /api/plugins/{id}/settings` | Any client | Get schema + values |
| `POST /api/plugins/{id}/settings` | Any client | Update settings (fires `on_config_change`) |
| `POST /api/plugins/{id}/enabled` | Any client | Enable/disable |
| `GET /plugins/{id}/{filename}` | Any client | Static file (allowlisted extensions) |
| `GET /api/display-grids` | Display apps | Audience grid layout |
| `POST /api/display-grids` | Operator | Update + broadcast grid layout |

---

**Happy hacking.** Start with `hello_world`, study the reference plugins when you're ready for more, and file issues or PRs when you hit rough edges.
