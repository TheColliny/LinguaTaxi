# LinguaTaxi Plugin API — Quick Reference

A one-page cheat sheet for building LinguaTaxi plugins. For the full walkthrough, tutorials, and best practices, see [`PLUGIN_DEVELOPMENT_GUIDE.md`](./PLUGIN_DEVELOPMENT_GUIDE.md).

---

## Minimum Viable Plugin

```
plugins/my_plugin/
├── manifest.json     (required)
├── routes.py         (optional — backend)
├── panel.html        (optional — UI fragment)
├── panel.js          (optional — frontend logic)
└── panel.css         (optional — styles)
```

---

## `manifest.json`

```json
{
  "id": "my_plugin",
  "name": "My Plugin",
  "version": "1.0.0",
  "description": "What it does.",
  "author": "Your Name",
  "hooks": ["on_final", "on_config_change", "on_shutdown"],
  "has_routes": true,
  "has_panel": true,
  "route_prefix": "/api/my-plugin",
  "settings_schema": {
    "api_key":   { "type": "password", "label": "API Key",       "default": "" },
    "threshold": { "type": "number",   "label": "Threshold",     "default": 50 },
    "enabled":   { "type": "toggle",   "label": "Fancy mode",    "default": false },
    "label":     { "type": "text",     "label": "Display label", "default": "X" }
  }
}
```

- **`id`** must match `^[a-z0-9_-]+$`.
- **Setting types:** `text`, `password`, `number`, `toggle`.
- **Hooks** you don't list aren't dispatched to your backend.

---

## `routes.py`

```python
import logging
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

log = logging.getLogger("livecaption")
router = APIRouter(prefix="/api")    # routes mount on operator_app (port 3001)
_settings = {}

@router.get("/my-plugin/status")
async def status():
    # plugin_api is injected by the loader — read your own settings any time
    return JSONResponse({"ok": True, "settings": plugin_api.read_settings()})

def handle_event(event_name, data, settings):
    """Optional. Called for each hook listed in manifest. Also fired once at
    plugin load time with event_name='on_config_change' so you see saved
    settings from previous sessions immediately."""
    global _settings
    _settings = settings
    if event_name == "on_final":
        log.info(f"[my_plugin] {data.get('speaker')}: {data['text']}")
```

**Rules:**
- Name the router `router` (exact name).
- Use `prefix="/api"` and namespace sub-paths under your plugin id.
- `handle_event` runs on a shared 4-worker thread pool — keep it fast.
- Exceptions are caught, logged, and shown as `!` badges in the panel.
- `plugin_api` is injected into module globals — use `plugin_api.read_settings()`, `plugin_api.get_setting(k, default)`, `plugin_api.update_setting(k, v)`, `plugin_api.write_settings(dict)` to read/write your own settings from anywhere.

---

## `panel.js`

```javascript
(function() {
  document.addEventListener('DOMContentLoaded', () => {
    // wire up your UI
  });

  window.LinguaTaxi.plugins.register('my_plugin', {
    on_final:        (d) => { /* {text, speaker, line_id, detected_lang} */ },
    on_interim:      (d) => { /* {text, speaker} */ },
    on_translation:  (d) => { /* {translated, lang, slot, speaker, line_id} */ },
    on_enabled:      ()  => { /* plugin turned on */ },
    on_disabled:     ()  => { /* plugin turned off */ },
    on_session_start:()  => {},
    on_session_stop: ()  => {},
    on_auto_speaker_change: (d) => { /* {speaker, previous, confidence} */ },
  });
})();
```

---

## Event Hooks

| Event | Backend payload | Frontend payload | Fires when |
|---|---|---|---|
| `on_interim` | `{text, speaker, ...}` | `{text, speaker}` | Interim transcript (frequent). |
| `on_final` | `{text, speaker, color, source_id, line_id, detected_lang}` | `{text, speaker, line_id, detected_lang}` | Final caption emitted. |
| `on_translation` | `{translated, lang, slot, speaker, line_id, source_lang}` | `{translated, lang, slot, speaker, line_id}` | Translation produced. |
| `on_session_start` | `{}` | `{}` | Captioning resumed. |
| `on_session_stop` | `{timestamp}` | `{}` | Captioning paused. |
| `on_speaker_enrolled` | `{speaker, source_id}` | — | Voice ID enrolled a new speaker. |
| `on_auto_speaker_change` | `{speaker, previous, confidence, source_id}` | `{speaker, previous, confidence}` | Voice ID auto-switched speaker. |
| `on_config_change` | `{config}` or `{plugin_id}` | — | Config saved. |
| `on_enabled` / `on_disabled` | — | `{pluginId}` | Operator toggled plugin. |
| `on_shutdown` | `{}` | — | Server shutting down. |

---

## Built-in HTTP Endpoints

| Method | Path | Purpose |
|---|---|---|
| `GET`  | `/api/plugins` | List all plugins (id, name, version, enabled, schema). |
| `GET`  | `/api/plugins/{id}/settings` | Returns `{schema, values}`. |
| `POST` | `/api/plugins/{id}/settings` | Form-encoded; fires `on_config_change`. |
| `POST` | `/api/plugins/{id}/enabled` | Form field `enabled=true\|false`. |
| `GET`  | `/plugins/{id}/{filename}` | Static file (allowlisted extensions). Available on all three apps. |
| `GET`  | `/api/display-grids` | Grid layout for main + extended displays. |
| `POST` | `/api/display-grids` | Operator-only. Broadcasts `display_grid_change` WS event. |

---

## Frontend Dispatcher API (`window.LinguaTaxi.plugins`)

```javascript
LinguaTaxi.plugins.register(pluginId, handlersObj)   // subscribe to events
LinguaTaxi.plugins.fire(eventName, dataObj)          // manually fire (rare)
LinguaTaxi.plugins.togglePanel(pluginId)             // collapse/expand panel
LinguaTaxi.plugins.toggleEnabled(pluginId)           // enable/disable
LinguaTaxi.plugins.openSettings(pluginId)            // open settings dialog
await LinguaTaxi.plugins.getSettings(pluginId)       // fetch current values
```

---

## File Paths and Injection Markers

| Source file | Served/injected at |
|---|---|
| `plugins/{id}/panel.html` | `<!-- PLUGIN_PANELS -->` in `operator.html` |
| `plugins/{id}/panel.js` | `<script src="/plugins/{id}/panel.js">` |
| `plugins/{id}/panel.css` | `<link rel="stylesheet" href="/plugins/{id}/panel.css">` |
| `plugins/{id}/<anything>` | `GET /plugins/{id}/<anything>` (allowlisted suffixes) |
| `plugins/{id}/lib/*.py` | Import as `plugin_{id}_lib.<module>` |

---

## Plugin Storage

| What | Where |
|---|---|
| Settings values | `config.json` → `plugin_settings.{id}` (auto) |
| Enabled state | `config.json` → `plugins_enabled.{id}` (auto) |
| Runtime data/cache | `plugins/{id}/data/` (you manage) |
| Helper modules | `plugins/{id}/lib/` (auto-namespaced) |

---

## Thread Safety

- **`async def` routes** → Uvicorn event loop.
- **`handle_event`** → shared 4-worker `ThreadPoolExecutor`.
- **Audio/transcription** → their own threads; fire events non-blockingly.
- Lock shared module state with `threading.Lock`.
- Clean up your own pools/tasks in `on_shutdown`.

---

## Checklist

- [ ] `manifest.json` valid (`id` matches `^[a-z0-9_-]+$`).
- [ ] `hooks` lists every event your backend consumes.
- [ ] `has_routes: true` if using `routes.py`; `has_panel: true` if using `panel.html`.
- [ ] `router = APIRouter(prefix="/api")` in `routes.py`.
- [ ] `handle_event` reads `settings` on every call (settings hot-reload).
- [ ] Panel CSS scoped with a unique class prefix (e.g. `.myplugin-*`).
- [ ] `window.LinguaTaxi.plugins.register(...)` called at end of `panel.js`.
- [ ] Secrets only in `settings_schema` (persisted to gitignored `config.json`) — **never** in tracked files.
- [ ] Server restarted after code changes (no Python hot-reload).

---

## Reference Plugins

| Plugin | Teaches |
|---|---|
| `fact_checker/` | Multi-provider AI dispatch, rate limiting, bundled datasets, sibling-file imports. |
| `donor_cloud/` | External API proxy, TTL cache, startup prefetch, env-var fallbacks. |
| `polls_checker/` | Provider routing (free/paid), static credibility database. |
| `live_dial/` | Plugin-owned WebSockets, subprocess tunnels, cross-thread async broadcasts. |

For deeper explanations of any of the above, see [`PLUGIN_DEVELOPMENT_GUIDE.md`](./PLUGIN_DEVELOPMENT_GUIDE.md).
