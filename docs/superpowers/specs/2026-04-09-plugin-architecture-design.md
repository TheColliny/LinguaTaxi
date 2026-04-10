# LinguaTaxi Plugin Architecture — Design Spec

**Date:** 2026-04-09
**Status:** Draft
**Author:** TheColliny + Claude

## Overview

LinguaTaxi becomes a plugin-capable platform. Features like the fact checker are packaged as self-contained plugin folders that users drop into a `plugins/` directory. No editing of core files, no pip commands, no internet required. The operator panel dynamically loads plugin UI panels, and a backend event dispatcher routes transcription events to registered plugins.

## Goals

1. **Zero-friction install** — copy a folder, restart server, done
2. **Offline transferable** — copy `plugins/` to USB, paste on another machine
3. **Isolation** — a broken plugin never crashes the captioning pipeline
4. **Consistent UI** — every plugin gets the same collapsible panel look automatically
5. **Maximum flexibility** — plugins can hook into all system events and provide both backend logic and operator UI

## Non-Goals

- No plugin marketplace or auto-update system
- No hot-reload (server restart required to pick up new plugins)
- No inter-plugin communication (plugins are independent)

---

## 1. Plugin Folder Structure

Each plugin lives in `plugins/<plugin_id>/`:

```
plugins/
  fact_checker/
    manifest.json        # required — identity, hooks, UI declarations
    routes.py            # optional — FastAPI router for backend API
    panel.html           # optional — operator panel inner HTML
    panel.js             # optional — client-side JavaScript
    panel.css            # optional — scoped styles
    lib/                 # optional — bundled Python dependencies
    assets/              # optional — images, icons, etc.
```

### manifest.json (required)

The only required file. Declares everything about the plugin:

```json
{
  "id": "fact_checker",
  "name": "Fact Checker",
  "version": "1.0.0",
  "description": "Real-time political statement fact-checking with Claude AI",
  "author": "LinguaTaxi",
  "hooks": ["on_final", "on_config_change"],
  "has_routes": true,
  "has_panel": true,
  "route_prefix": "/api/fact-check",
  "settings_schema": {
    "anthropic_api_key": {
      "type": "password",
      "label": "Anthropic API Key",
      "default": ""
    }
  }
}
```

**Required fields:** `id`, `name`, `version`

**Optional fields:**
- `description` — shown in operator panel plugin list
- `author` — attribution
- `hooks` — array of event names to subscribe to (see Section 3)
- `has_routes` — whether to load `routes.py` and mount its router
- `has_panel` — whether to inject `panel.html`/`panel.js`/`panel.css` into the operator page
- `route_prefix` — URL prefix for the plugin's API routes (default: `/api/plugins/<id>`)
- `settings_schema` — key-value pairs defining operator-configurable settings. Each entry has `type` (`text`, `password`, `number`, `toggle`), `label`, and `default`

### Bundled Dependencies (lib/)

Plugin authors bundle their Python dependencies into `lib/` using:

```bash
pip install anthropic -t plugins/fact_checker/lib/
```

The plugin loader prepends `lib/` to `sys.path` before importing the plugin's `routes.py`. Users never run pip — they just copy the folder.

---

## 2. Plugin Loader & Lifecycle

### Discovery (at server startup, before Uvicorn)

1. Scan `plugins/` directory for subfolders containing `manifest.json`
2. Parse and validate each manifest (require `id`, `name`, `version`)
3. Check enabled/disabled state from `config.json` → `plugins_enabled` dict
4. Skip disabled plugins entirely — no code loaded, no routes mounted

### Loading (for each enabled plugin)

1. If `lib/` exists, prepend it to `sys.path` so bundled deps are importable
2. If `has_routes: true`, import `routes.py` and mount its `router` on `operator_app` under `route_prefix`
3. If `has_panel: true`, read `panel.html`, `panel.js`, `panel.css` for later injection
4. Register the plugin's declared hooks with the event dispatcher
5. Serve plugin static files at `/plugins/<plugin_id>/`

### Error Isolation

Every plugin load and every hook call is wrapped in try/except. A broken plugin:
- Logs the error
- Shows a warning badge in its operator panel
- Never blocks or crashes the core captioning pipeline

### Shutdown

All plugins receive an `on_shutdown` event to clean up resources (close thread pools, flush buffers, etc.).

---

## 3. Event Dispatch System

### Backend Events

Fired in `server.py`, received by each plugin's `routes.py` via a `handle_event(event_name, data, settings)` function:

| Event | When | Data |
|-------|------|------|
| `on_final` | Final transcription ready | `{text, speaker, color, source_id, line_id, detected_lang}` |
| `on_interim` | Partial transcription update | `{text, speaker, source_id}` |
| `on_translation` | Translation complete | `{translated, lang, slot, speaker, line_id, source_lang}` |
| `on_config_change` | Operator changes settings | `{config}` |
| `on_session_start` | GO LIVE pressed | `{timestamp, backend, sources}` |
| `on_session_stop` | Captioning stopped | `{timestamp}` |
| `on_shutdown` | Server shutting down | `{}` |

### Dispatcher Implementation

A `PluginDispatcher` class in a new `plugin_loader.py` module:

```python
plugin_dispatcher.fire("on_final", {
    "text": text, "speaker": speaker, "line_id": lid,
    "detected_lang": detected_lang, "source_id": source_id
})
```

Hook calls run in a dedicated thread pool so slow plugins cannot block the audio pipeline. Each call has a 30-second timeout — exceeded calls are abandoned and logged as warnings.

Plugins only receive events they declared in `manifest.json` → `hooks`.

### Frontend Events

Fired in `operator.html`, received by each plugin's `panel.js`:

**Generic dispatch replaces hardcoded plugin calls:**

```javascript
// In handleMsg — one line per event, not one per plugin
window.LinguaTaxi.plugins.fire('on_final', {text: m.text, speaker: m.speaker, line_id: m.line_id});
```

**Plugin registration in panel.js:**

```javascript
window.LinguaTaxi.plugins.register('fact_checker', {
    on_final: (data) => { /* process transcript */ },
    on_session_start: () => { /* reset state */ }
});
```

This replaces the current `if(typeof FactChecker !== 'undefined')` pattern with a single `fire()` call that fans out to all registered plugins.

### Frontend Event Table

| Event | When | Data |
|-------|------|------|
| `on_final` | Final caption received via WebSocket | `{text, speaker, line_id, detected_lang}` |
| `on_interim` | Interim caption received | `{text, speaker}` |
| `on_translation` | Translation received | `{translated, lang, slot, speaker, line_id}` |
| `on_config_change` | Config update message received | `{config}` |
| `on_session_start` | Captioning resumed | `{}` |
| `on_session_stop` | Captioning paused | `{}` |

---

## 4. Operator Panel Integration

### Dynamic HTML Assembly

The operator page is assembled at request time rather than served as a static file. When the operator requests `/`:

1. Read `operator.html` as a base template
2. Find the `<!-- PLUGIN_CSS -->` marker in `<head>` — inject each plugin's CSS link
3. Find the `<!-- PLUGIN_PANELS -->` marker in `<body>` — inject each plugin's panel HTML (wrapped in standard container)
4. Find the `<!-- PLUGIN_JS -->` marker before `</body>` — inject each plugin's JS script tag and the plugin dispatcher script
5. Return the assembled HTML

### Standard Panel Wrapper

Every plugin panel is automatically wrapped in a consistent collapsible container. The plugin author only writes the **inner content** in `panel.html`:

```html
<!-- fact_checker/panel.html — only inner content -->
<div class="fc-results" id="fc-results"></div>
<div class="fc-queue-status" id="fc-queue-status"></div>
```

The loader generates the outer wrapper using manifest metadata:

```html
<div class="plugin-panel" data-plugin-id="fact_checker">
  <div class="plugin-header" onclick="LinguaTaxi.plugins.togglePanel('fact_checker')">
    <div class="plugin-header-left">
      <span class="plugin-indicator" id="plugin-indicator-fact_checker"></span>
      <span class="plugin-title">Fact Checker</span>
    </div>
    <div class="plugin-header-right">
      <button class="plugin-settings-btn" onclick="event.stopPropagation(); LinguaTaxi.plugins.openSettings('fact_checker')">&#9881;</button>
      <button class="plugin-toggle-btn" onclick="event.stopPropagation(); LinguaTaxi.plugins.toggleEnabled('fact_checker')">Enable</button>
      <span class="plugin-chevron">&#x25BE;</span>
    </div>
  </div>
  <div class="plugin-body">
    <!-- panel.html content injected here -->
  </div>
</div>
```

This guarantees visual consistency: every plugin has the same indicator dot, title, enable/disable button, settings gear, and collapse behavior.

### Plugin Static File Serving

Each plugin's files are served at `/plugins/<plugin_id>/`:
- `/plugins/fact_checker/panel.js`
- `/plugins/fact_checker/panel.css`
- `/plugins/fact_checker/assets/icon.png`

### Auto-Generated Settings Form

If the manifest declares `settings_schema`, clicking the gear icon shows a form auto-generated from the schema:

```json
"settings_schema": {
  "anthropic_api_key": {"type": "password", "label": "Anthropic API Key", "default": ""},
  "rate_limit": {"type": "number", "label": "Max checks per minute", "default": 10}
}
```

Produces a form with a password input and a number input. Values are saved to `config.json` and passed to the plugin via `on_config_change`.

---

## 5. Config Storage

Plugin state lives in the existing `config.json` under two new top-level keys:

```json
{
  "deepl_api_key": "...",

  "plugins_enabled": {
    "fact_checker": true,
    "sentiment_analysis": false
  },

  "plugin_settings": {
    "fact_checker": {
      "anthropic_api_key": "sk-ant-...",
      "rate_limit": 10
    }
  }
}
```

### How Plugins Access Settings

- **Backend:** `handle_event(event_name, data, settings)` receives the plugin's settings dict as the third argument. The plugin never reads `config.json` directly.
- **Frontend:** `window.LinguaTaxi.plugins.getSettings('fact_checker')` fetches from `/api/plugins/<id>/settings`.
- **Saving:** POST to `/api/plugins/<id>/settings` updates `config.json` and fires `on_config_change` to that plugin.

### What Moves Out of Core Config

The current `anthropic_api_key` in the core config moves to `plugin_settings.fact_checker.anthropic_api_key`. The fact checker becomes a regular plugin with no special treatment in the core config or server.py imports.

---

## 6. New Files & Modified Files

### New Files

| File | Purpose |
|------|---------|
| `plugin_loader.py` | Discovery, loading, `PluginDispatcher`, settings API routes |
| `static/plugin_panel.css` | Standard panel wrapper styles (`.plugin-panel`, `.plugin-header`, etc.) |
| `static/plugin_dispatcher.js` | Frontend `window.LinguaTaxi.plugins` registry and event dispatch |
| `plugins/fact_checker/manifest.json` | Fact checker plugin manifest |
| `plugins/fact_checker/routes.py` | Fact checker backend (moved from `fact_checker_routes.py`) |
| `plugins/fact_checker/panel.html` | Fact checker panel inner HTML (extracted from `operator.html`) |
| `plugins/fact_checker/panel.js` | Fact checker JS (moved from `static/fact_checker.js`, adapted to use plugin registration) |
| `plugins/fact_checker/panel.css` | Fact checker styles (moved from `static/fact_checker.css`) |
| `plugins/fact_checker/lib/` | Bundled `anthropic` package + dependencies |

### Modified Files

| File | Changes |
|------|---------|
| `server.py` | Remove hardcoded fact checker import/mount. Add `plugin_loader` import. Add `plugin_dispatcher.fire()` calls at each event point. Change operator `/` route to assemble HTML dynamically. Add plugin API routes (`/api/plugins/...`). |
| `operator.html` | Remove hardcoded fact checker panel HTML, CSS link, JS script, and `FactChecker.onTranscript()` call. Add three markers: `<!-- PLUGIN_CSS -->`, `<!-- PLUGIN_PANELS -->`, `<!-- PLUGIN_JS -->`. Add single `LinguaTaxi.plugins.fire()` call in handleMsg. |
| `config.json` | Remove `anthropic_api_key`. Add `plugins_enabled` and `plugin_settings` keys. |
| `build/windows/installer.iss` | Add `plugins/` directory to [Files]. Remove `fact_checker_routes.py` and `static/fact_checker.*` entries. |
| `build/mac/build.sh` | Copy `plugins/` directory to app bundle. Remove standalone fact checker file copies. |

### Deleted Files (moved into plugin)

| File | Moved to |
|------|----------|
| `fact_checker_routes.py` | `plugins/fact_checker/routes.py` |
| `static/fact_checker.js` | `plugins/fact_checker/panel.js` |
| `static/fact_checker.css` | `plugins/fact_checker/panel.css` |

---

## 7. Migration Path

The refactoring happens in this order:

1. **Create plugin infrastructure** — `plugin_loader.py`, `plugin_dispatcher.js`, `plugin_panel.css`
2. **Add markers to operator.html** — the three injection points, replace hardcoded FactChecker call with generic `fire()`
3. **Wire up server.py** — import plugin_loader, add `fire()` calls at each event point, change operator `/` to dynamic assembly
4. **Move fact checker into plugin folder** — relocate files, create manifest, adapt JS to use `plugins.register()` instead of global IIFE
5. **Bundle fact checker dependencies** — `pip install anthropic -t plugins/fact_checker/lib/`
6. **Clean up** — remove old hardcoded fact checker code from server.py, operator.html, and build scripts
7. **Update build system** — bundle `plugins/` directory in installers

---

## 8. Plugin Author Guide (Summary)

To create a plugin:

1. Create `plugins/my_plugin/manifest.json` with `id`, `name`, `version`, and desired `hooks`
2. Add `routes.py` with a FastAPI `router` and `handle_event(event_name, data, settings)` function for backend logic
3. Add `panel.html` with your panel's inner HTML content (the wrapper is generated for you)
4. Add `panel.js` that calls `window.LinguaTaxi.plugins.register('my_plugin', { on_final: (data) => {...} })`
5. Add `panel.css` with styles scoped to your plugin's CSS prefix
6. Bundle any Python dependencies: `pip install <package> -t plugins/my_plugin/lib/`
7. Drop the folder into any LinguaTaxi installation's `plugins/` directory and restart
