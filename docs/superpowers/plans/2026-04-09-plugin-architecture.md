# Plugin Architecture Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Transform LinguaTaxi into a plugin-capable platform where features like the fact checker are self-contained drop-in folders, with auto-discovery, event dispatch, and dynamic operator panel injection.

**Architecture:** A `plugin_loader.py` module handles discovery and lifecycle. A `PluginDispatcher` fires events to registered plugins via a dedicated thread pool. The operator page is assembled dynamically at request time, injecting each plugin's panel HTML/CSS/JS at marked locations. The fact checker becomes the first plugin, moved from its current hardcoded integration into `plugins/fact_checker/`.

**Tech Stack:** Python 3.11, FastAPI, Starlette StaticFiles, vanilla JavaScript (IIFE → plugin registration pattern)

**Spec:** `docs/superpowers/specs/2026-04-09-plugin-architecture-design.md`

---

## File Map

### New Files

| File | Responsibility |
|------|----------------|
| `plugin_loader.py` | Plugin discovery, manifest parsing, loading, `PluginDispatcher` class, plugin API routes |
| `static/plugin_dispatcher.js` | Frontend `window.LinguaTaxi.plugins` registry, `fire()`, `register()`, panel toggle/settings |
| `static/plugin_panel.css` | Standard `.plugin-panel` wrapper styles (header, indicator, toggle, settings, collapse) |
| `plugins/fact_checker/manifest.json` | Fact checker plugin identity and hook declarations |
| `plugins/fact_checker/routes.py` | Moved from `fact_checker_routes.py`, adds `handle_event()` |
| `plugins/fact_checker/panel.html` | Inner panel content extracted from operator.html lines 265-268 |
| `plugins/fact_checker/panel.js` | Adapted from `static/fact_checker.js` to use `plugins.register()` |
| `plugins/fact_checker/panel.css` | Moved from `static/fact_checker.css` unchanged |

### Modified Files

| File | What Changes |
|------|-------------|
| `server.py` | Remove fact_checker import/mount. Import `plugin_loader`. Add `plugin_dispatcher.fire()` at 7 event points. Change `o_index` to dynamic HTML assembly. Mount plugin static routes. |
| `operator.html` | Remove hardcoded fact checker (CSS link, panel HTML, JS script, onTranscript call). Add 3 injection markers. Add generic `LinguaTaxi.plugins.fire()` calls in handleMsg. |
| `build/windows/installer.iss` | Replace `fact_checker_routes.py` + `static/fact_checker.*` entries with `plugins/` directory and `plugin_loader.py`. |
| `build/mac/build.sh` | Replace fact checker file copies with `plugins/` directory copy. |

### Deleted Files

| File | Moved To |
|------|----------|
| `fact_checker_routes.py` | `plugins/fact_checker/routes.py` |
| `static/fact_checker.js` | `plugins/fact_checker/panel.js` |
| `static/fact_checker.css` | `plugins/fact_checker/panel.css` |

---

## Task 1: Create plugin_loader.py — Discovery and Dispatcher

**Files:**
- Create: `plugin_loader.py`

- [ ] **Step 1: Create `plugin_loader.py` with manifest parsing and discovery**

```python
"""
LinguaTaxi — Plugin Loader
Discovers, loads, and dispatches events to drop-in plugins from the plugins/ directory.
"""

import importlib
import json
import logging
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

log = logging.getLogger("livecaption")


class PluginManifest:
    """Parsed plugin manifest."""
    __slots__ = ("id", "name", "version", "description", "author",
                 "hooks", "has_routes", "has_panel", "route_prefix",
                 "settings_schema", "path")

    def __init__(self, data: dict, path: Path):
        self.id = data["id"]
        self.name = data["name"]
        self.version = data["version"]
        self.description = data.get("description", "")
        self.author = data.get("author", "")
        self.hooks = data.get("hooks", [])
        self.has_routes = data.get("has_routes", False)
        self.has_panel = data.get("has_panel", False)
        self.route_prefix = data.get("route_prefix", f"/api/plugins/{self.id}")
        self.settings_schema = data.get("settings_schema", {})
        self.path = path


class LoadedPlugin:
    """A fully loaded plugin with its module, panel content, and manifest."""
    __slots__ = ("manifest", "module", "panel_html", "panel_js_path",
                 "panel_css_path", "error")

    def __init__(self, manifest: PluginManifest):
        self.manifest = manifest
        self.module = None       # imported routes.py module
        self.panel_html = ""     # contents of panel.html
        self.panel_js_path = ""  # URL path to panel.js
        self.panel_css_path = "" # URL path to panel.css
        self.error = None        # load error message, if any


class PluginDispatcher:
    """Discovers plugins, loads enabled ones, dispatches events."""

    def __init__(self, plugins_dir: Path, config: dict):
        self.plugins_dir = plugins_dir
        self.config = config
        self._plugins: dict[str, LoadedPlugin] = {}   # id → LoadedPlugin
        self._manifests: dict[str, PluginManifest] = {}  # id → manifest (all discovered)
        self._hooks: dict[str, list[LoadedPlugin]] = {}  # event_name → [plugins]
        self._pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="plugin")
        self._lock = threading.Lock()

    # ── Discovery ──

    def discover(self) -> list[PluginManifest]:
        """Scan plugins/ for folders with manifest.json. Returns all manifests."""
        self._manifests.clear()
        if not self.plugins_dir.is_dir():
            return []
        for folder in sorted(self.plugins_dir.iterdir()):
            if not folder.is_dir():
                continue
            manifest_path = folder / "manifest.json"
            if not manifest_path.exists():
                continue
            try:
                with open(manifest_path) as f:
                    data = json.load(f)
                for field in ("id", "name", "version"):
                    if field not in data:
                        raise ValueError(f"Missing required field: {field}")
                m = PluginManifest(data, folder)
                self._manifests[m.id] = m
            except Exception as e:
                log.warning(f"Plugin manifest error in {folder.name}: {e}")
        return list(self._manifests.values())

    def get_all_manifests(self) -> list[PluginManifest]:
        """Return all discovered manifests (enabled and disabled)."""
        return list(self._manifests.values())

    def is_enabled(self, plugin_id: str) -> bool:
        """Check if a plugin is enabled in config."""
        enabled = self.config.get("plugins_enabled", {})
        return enabled.get(plugin_id, True)  # default: enabled if not explicitly disabled

    # ── Loading ──

    def load_enabled(self, operator_app=None):
        """Load all enabled plugins: import routes, read panel files, register hooks."""
        for pid, manifest in self._manifests.items():
            lp = LoadedPlugin(manifest)
            if not self.is_enabled(pid):
                self._plugins[pid] = lp
                continue
            try:
                self._load_plugin(lp, operator_app)
            except Exception as e:
                lp.error = str(e)[:300]
                log.error(f"Plugin '{pid}' failed to load: {e}")
            self._plugins[pid] = lp

    def _load_plugin(self, lp: LoadedPlugin, operator_app):
        """Load a single plugin."""
        m = lp.manifest
        plugin_dir = m.path

        # Add lib/ to sys.path for bundled dependencies
        lib_dir = plugin_dir / "lib"
        if lib_dir.is_dir():
            lib_str = str(lib_dir)
            if lib_str not in sys.path:
                sys.path.insert(0, lib_str)

        # Import routes.py if declared
        if m.has_routes:
            routes_file = plugin_dir / "routes.py"
            if routes_file.exists():
                spec = importlib.util.spec_from_file_location(
                    f"plugin_{m.id}_routes", str(routes_file))
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                lp.module = mod
                # Mount FastAPI router if present
                if operator_app and hasattr(mod, "router"):
                    operator_app.include_router(mod.router)
                    log.info(f"Plugin '{m.id}': mounted router at {m.route_prefix}")

        # Read panel files if declared
        if m.has_panel:
            html_file = plugin_dir / "panel.html"
            if html_file.exists():
                lp.panel_html = html_file.read_text(encoding="utf-8")
            js_file = plugin_dir / "panel.js"
            if js_file.exists():
                lp.panel_js_path = f"/plugins/{m.id}/panel.js"
            css_file = plugin_dir / "panel.css"
            if css_file.exists():
                lp.panel_css_path = f"/plugins/{m.id}/panel.css"

        # Register hooks
        for hook in m.hooks:
            if hook not in self._hooks:
                self._hooks[hook] = []
            self._hooks[hook].append(lp)

        log.info(f"Plugin '{m.id}' v{m.version} loaded (hooks: {m.hooks})")

    # ── Event Dispatch ──

    def fire(self, event_name: str, data: dict):
        """Fire an event to all plugins subscribed to it. Non-blocking."""
        listeners = self._hooks.get(event_name, [])
        for lp in listeners:
            if not self.is_enabled(lp.manifest.id):
                continue
            if lp.module and hasattr(lp.module, "handle_event"):
                settings = self.get_settings(lp.manifest.id)
                self._pool.submit(self._safe_call, lp, event_name, data, settings)

    def _safe_call(self, lp: LoadedPlugin, event_name: str, data: dict, settings: dict):
        """Call plugin's handle_event in a try/except."""
        try:
            lp.module.handle_event(event_name, data, settings)
        except Exception as e:
            lp.error = f"{event_name}: {str(e)[:200]}"
            log.error(f"Plugin '{lp.manifest.id}' error on {event_name}: {e}")

    # ── Settings ──

    def get_settings(self, plugin_id: str) -> dict:
        """Get a plugin's settings from config."""
        return self.config.get("plugin_settings", {}).get(plugin_id, {})

    def save_settings(self, plugin_id: str, settings: dict):
        """Save a plugin's settings to config."""
        if "plugin_settings" not in self.config:
            self.config["plugin_settings"] = {}
        self.config["plugin_settings"][plugin_id] = settings

    def set_enabled(self, plugin_id: str, enabled: bool):
        """Enable or disable a plugin in config."""
        if "plugins_enabled" not in self.config:
            self.config["plugins_enabled"] = {}
        self.config["plugins_enabled"][plugin_id] = enabled

    # ── HTML Assembly ──

    def get_css_links(self) -> str:
        """Return CSS <link> tags for all enabled plugins with panels."""
        lines = []
        for pid, lp in self._plugins.items():
            if self.is_enabled(pid) and lp.panel_css_path:
                lines.append(f'<link rel="stylesheet" href="{lp.panel_css_path}">')
        return "\n".join(lines)

    def get_panel_html(self) -> str:
        """Return wrapped panel HTML for all enabled plugins."""
        panels = []
        for pid, lp in self._plugins.items():
            m = lp.manifest
            enabled_class = " plugin-enabled" if self.is_enabled(pid) else ""
            error_badge = ""
            if lp.error:
                error_badge = f' <span class="plugin-error-badge" title="{lp.error}">!</span>'
            settings_btn = ""
            if m.settings_schema:
                settings_btn = f'<button class="plugin-settings-btn" onclick="event.stopPropagation(); LinguaTaxi.plugins.openSettings(\'{pid}\')">&#9881;</button>'

            panel = f'''<div class="plugin-panel{enabled_class}" data-plugin-id="{pid}">
  <div class="plugin-header" onclick="LinguaTaxi.plugins.togglePanel('{pid}')">
    <div class="plugin-header-left">
      <span class="plugin-indicator" id="plugin-indicator-{pid}"></span>
      <span class="plugin-title">{m.name}</span>{error_badge}
    </div>
    <div class="plugin-header-right">
      {settings_btn}
      <button class="plugin-toggle-btn" id="plugin-toggle-{pid}" onclick="event.stopPropagation(); LinguaTaxi.plugins.toggleEnabled('{pid}')">{("Disable" if self.is_enabled(pid) else "Enable")}</button>
      <span class="plugin-chevron" id="plugin-chevron-{pid}">&#x25BE;</span>
    </div>
  </div>
  <div class="plugin-body" id="plugin-body-{pid}">
    {lp.panel_html}
  </div>
</div>'''
            panels.append(panel)
        return "\n".join(panels)

    def get_js_scripts(self) -> str:
        """Return <script> tags for all enabled plugins with panels."""
        lines = []
        for pid, lp in self._plugins.items():
            if self.is_enabled(pid) and lp.panel_js_path:
                lines.append(f'<script src="{lp.panel_js_path}"></script>')
        return "\n".join(lines)

    def get_settings_schemas(self) -> dict:
        """Return {plugin_id: settings_schema} for all plugins."""
        return {pid: lp.manifest.settings_schema
                for pid, lp in self._plugins.items()
                if lp.manifest.settings_schema}

    # ── Cleanup ──

    def shutdown(self):
        """Fire shutdown event and clean up thread pool."""
        self.fire("on_shutdown", {})
        self._pool.shutdown(wait=False)
```

- [ ] **Step 2: Verify it compiles**

Run: `python -c "import py_compile; py_compile.compile('plugin_loader.py', doraise=True); print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add plugin_loader.py
git commit -m "[feat] add plugin_loader.py — discovery, loading, event dispatch"
```

---

## Task 2: Create Frontend Plugin Dispatcher

**Files:**
- Create: `static/plugin_dispatcher.js`
- Create: `static/plugin_panel.css`

- [ ] **Step 1: Create `static/plugin_dispatcher.js`**

```javascript
/**
 * LinguaTaxi — Plugin Dispatcher
 * Provides window.LinguaTaxi.plugins API for plugin registration and event dispatch.
 * Injected into operator.html by the plugin loader.
 */
window.LinguaTaxi = window.LinguaTaxi || {};
window.LinguaTaxi.plugins = (() => {
  const _registry = {};  // pluginId → {on_final: fn, on_interim: fn, ...}
  const _panels = {};    // pluginId → {open: bool}

  function register(pluginId, handlers) {
    _registry[pluginId] = handlers;
  }

  function fire(eventName, data) {
    Object.entries(_registry).forEach(([pid, handlers]) => {
      if (typeof handlers[eventName] === 'function') {
        try {
          handlers[eventName](data);
        } catch (e) {
          console.error(`Plugin '${pid}' error on ${eventName}:`, e);
        }
      }
    });
  }

  function togglePanel(pluginId) {
    const body = document.getElementById('plugin-body-' + pluginId);
    const chevron = document.getElementById('plugin-chevron-' + pluginId);
    if (!body) return;
    const open = body.style.display !== 'none';
    body.style.display = open ? 'none' : 'block';
    if (chevron) chevron.textContent = open ? '\u25B8' : '\u25BE';
  }

  function toggleEnabled(pluginId) {
    const btn = document.getElementById('plugin-toggle-' + pluginId);
    const indicator = document.getElementById('plugin-indicator-' + pluginId);
    if (!btn) return;
    const enabling = btn.textContent.trim() === 'Enable';
    // Save to server
    const fd = new FormData();
    fd.append('enabled', enabling ? 'true' : 'false');
    fetch('/api/plugins/' + pluginId + '/enabled', { method: 'POST', body: fd });
    // Update UI
    btn.textContent = enabling ? 'Disable' : 'Enable';
    if (indicator) indicator.className = 'plugin-indicator' + (enabling ? ' plugin-indicator--on' : '');
    // Notify plugin
    fire(enabling ? 'on_enabled' : 'on_disabled', { pluginId });
  }

  function openSettings(pluginId) {
    fetch('/api/plugins/' + pluginId + '/settings')
      .then(r => r.json())
      .then(data => {
        _showSettingsDialog(pluginId, data.schema || {}, data.values || {});
      });
  }

  function _showSettingsDialog(pluginId, schema, values) {
    // Remove existing dialog
    const existing = document.getElementById('plugin-settings-dlg');
    if (existing) existing.remove();

    const overlay = document.createElement('div');
    overlay.id = 'plugin-settings-dlg';
    overlay.className = 'plugin-settings-overlay';
    overlay.onclick = (e) => { if (e.target === overlay) overlay.remove(); };

    const dlg = document.createElement('div');
    dlg.className = 'plugin-settings-dialog';

    let html = '<div class="plugin-settings-title">' + pluginId.replace(/_/g, ' ') + ' Settings</div>';
    html += '<div class="plugin-settings-fields">';

    Object.entries(schema).forEach(([key, def]) => {
      const val = values[key] !== undefined ? values[key] : (def.default || '');
      const type = def.type === 'password' ? 'password' : def.type === 'number' ? 'number' : 'text';
      if (def.type === 'toggle') {
        html += `<label class="plugin-settings-label">${def.label}
          <input type="checkbox" data-key="${key}" ${val ? 'checked' : ''}></label>`;
      } else {
        html += `<label class="plugin-settings-label">${def.label}
          <input type="${type}" data-key="${key}" value="${String(val).replace(/"/g, '&quot;')}" class="plugin-settings-input"></label>`;
      }
    });

    html += '</div>';
    html += '<div class="plugin-settings-btns">';
    html += '<button class="plugin-settings-save" id="ps-save">Save</button>';
    html += '<button class="plugin-settings-cancel" onclick="this.closest(\'.plugin-settings-overlay\').remove()">Cancel</button>';
    html += '</div>';
    dlg.innerHTML = html;
    overlay.appendChild(dlg);
    document.body.appendChild(overlay);

    document.getElementById('ps-save').onclick = () => {
      const fd = new FormData();
      dlg.querySelectorAll('[data-key]').forEach(el => {
        fd.append(el.dataset.key, el.type === 'checkbox' ? el.checked : el.value);
      });
      fetch('/api/plugins/' + pluginId + '/settings', { method: 'POST', body: fd })
        .then(() => overlay.remove());
    };
  }

  function getSettings(pluginId) {
    return fetch('/api/plugins/' + pluginId + '/settings')
      .then(r => r.json())
      .then(d => d.values || {});
  }

  return { register, fire, togglePanel, toggleEnabled, openSettings, getSettings };
})();
```

- [ ] **Step 2: Create `static/plugin_panel.css`**

```css
/* LinguaTaxi — Plugin Panel Standard Styles
   Provides the wrapper for all plugin panels: header, indicator, toggle, settings, collapse */

.plugin-panel {
  margin: 12px 0;
  border: 1px solid rgba(255,255,255,0.12);
  border-radius: 8px;
  overflow: hidden;
  background: rgba(0,0,0,0.25);
  font-family: inherit;
}

.plugin-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 9px 14px;
  cursor: pointer;
  user-select: none;
  background: rgba(255,255,255,0.04);
  transition: background 0.15s;
}
.plugin-header:hover { background: rgba(255,255,255,0.07); }

.plugin-header-left  { display: flex; align-items: center; gap: 8px; }
.plugin-header-right { display: flex; align-items: center; gap: 10px; }

.plugin-title {
  font-size: 13px;
  font-weight: 500;
  color: rgba(255,255,255,0.8);
  letter-spacing: 0.01em;
}

.plugin-indicator {
  width: 7px; height: 7px; border-radius: 50%;
  background: rgba(255,255,255,0.2);
  flex-shrink: 0; transition: background 0.2s;
}
.plugin-indicator--on { background: #EF9F27; }

.plugin-error-badge {
  display: inline-block; width: 16px; height: 16px; line-height: 16px;
  text-align: center; border-radius: 50%; background: #A32D2D;
  color: #fff; font-size: 10px; font-weight: 700; cursor: help;
}

.plugin-chevron { font-size: 12px; color: rgba(255,255,255,0.4); }

.plugin-toggle-btn {
  font-size: 11px; padding: 4px 10px; border-radius: 5px;
  border: 1px solid rgba(255,255,255,0.2); background: transparent;
  color: rgba(255,255,255,0.6); cursor: pointer; font-family: inherit;
  transition: background 0.15s, color 0.15s;
}
.plugin-toggle-btn:hover { background: rgba(255,255,255,0.08); color: rgba(255,255,255,0.9); }

.plugin-settings-btn {
  font-size: 14px; background: none; border: none;
  color: rgba(255,255,255,0.35); cursor: pointer; padding: 2px 4px;
  transition: color 0.15s;
}
.plugin-settings-btn:hover { color: rgba(255,255,255,0.8); }

.plugin-body { padding: 10px 12px 12px; }

/* Settings Dialog */
.plugin-settings-overlay {
  position: fixed; top: 0; left: 0; right: 0; bottom: 0;
  background: rgba(0,0,0,0.6); z-index: 9999;
  display: flex; align-items: center; justify-content: center;
}
.plugin-settings-dialog {
  background: #1a1a2e; border: 1px solid rgba(255,255,255,0.15);
  border-radius: 10px; padding: 20px; min-width: 340px; max-width: 480px;
}
.plugin-settings-title {
  font-size: 15px; font-weight: 600; color: #fff;
  margin-bottom: 16px; text-transform: capitalize;
}
.plugin-settings-fields { display: flex; flex-direction: column; gap: 12px; }
.plugin-settings-label {
  font-size: 12px; color: rgba(255,255,255,0.6);
  display: flex; flex-direction: column; gap: 4px;
}
.plugin-settings-input {
  padding: 6px 10px; border-radius: 5px;
  border: 1px solid rgba(255,255,255,0.15);
  background: rgba(255,255,255,0.05); color: #fff;
  font-family: inherit; font-size: 13px;
}
.plugin-settings-input:focus { outline: none; border-color: #4FC3F7; }
.plugin-settings-btns {
  display: flex; gap: 8px; justify-content: flex-end; margin-top: 16px;
}
.plugin-settings-save, .plugin-settings-cancel {
  padding: 6px 16px; border-radius: 5px; font-family: inherit;
  font-size: 12px; cursor: pointer; border: 1px solid rgba(255,255,255,0.15);
}
.plugin-settings-save {
  background: #4FC3F7; color: #000; border-color: #4FC3F7; font-weight: 600;
}
.plugin-settings-cancel { background: transparent; color: rgba(255,255,255,0.5); }
```

- [ ] **Step 3: Commit**

```bash
git add static/plugin_dispatcher.js static/plugin_panel.css
git commit -m "[feat] add frontend plugin dispatcher and panel styles"
```

---

## Task 3: Create Fact Checker Plugin Folder

**Files:**
- Create: `plugins/fact_checker/manifest.json`
- Create: `plugins/fact_checker/panel.html`
- Move: `fact_checker_routes.py` → `plugins/fact_checker/routes.py` (with `handle_event` added)
- Move: `static/fact_checker.js` → `plugins/fact_checker/panel.js` (adapted to use `plugins.register()`)
- Move: `static/fact_checker.css` → `plugins/fact_checker/panel.css`

- [ ] **Step 1: Create directory structure**

```bash
mkdir -p plugins/fact_checker
```

- [ ] **Step 2: Create `plugins/fact_checker/manifest.json`**

```json
{
  "id": "fact_checker",
  "name": "Fact Checker",
  "version": "1.0.0",
  "description": "Real-time political statement fact-checking with Claude AI and web search",
  "author": "LinguaTaxi",
  "hooks": ["on_final", "on_config_change", "on_shutdown"],
  "has_routes": true,
  "has_panel": true,
  "route_prefix": "/api/fact-check",
  "settings_schema": {
    "anthropic_api_key": {
      "type": "password",
      "label": "Anthropic API Key",
      "default": ""
    },
    "rate_limit": {
      "type": "number",
      "label": "Max checks per minute",
      "default": 10
    }
  }
}
```

- [ ] **Step 3: Create `plugins/fact_checker/panel.html`**

This is the inner content only — the wrapper is generated by the plugin loader:

```html
<div class="fc-empty" id="fc-empty">Fact checker is off. Click Enable to analyze statements as they are transcribed.</div>
<div class="fc-results" id="fc-results"></div>
<div class="fc-queue-status" id="fc-queue-status"></div>
```

- [ ] **Step 4: Move and adapt `fact_checker_routes.py` → `plugins/fact_checker/routes.py`**

Copy the existing file, then add a `handle_event()` function at the bottom. The routes remain the same — `handle_event` is the new plugin interface that the dispatcher calls, but the fact checker does its work via HTTP from the frontend, so `handle_event` is minimal:

```python
# Add at the bottom of the file, after the existing routes:

def handle_event(event_name, data, settings):
    """Plugin event handler called by PluginDispatcher.
    The fact checker's main work is driven by frontend JS calling /api/fact-check,
    but we use events for settings sync and cleanup."""
    if event_name == "on_config_change":
        # If API key changed in plugin settings, update the singleton client
        new_key = settings.get("anthropic_api_key", "")
        if new_key:
            os.environ["ANTHROPIC_API_KEY"] = new_key
    elif event_name == "on_shutdown":
        _fc_pool.shutdown(wait=False)
```

Also update `_get_api_key()` to accept settings from the dispatcher — add a module-level `_plugin_settings` dict that gets updated via `handle_event`:

Replace the existing `_get_api_key()` function with:

```python
_plugin_settings = {}

def _get_api_key():
    """Get Anthropic API key from plugin settings, environment, or server config."""
    # 1. Plugin settings (set via operator panel gear icon)
    key = _plugin_settings.get("anthropic_api_key", "")
    if key:
        return key
    # 2. Environment variable
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if key:
        return key
    return ""
```

And update `handle_event` to sync settings:

```python
def handle_event(event_name, data, settings):
    global _plugin_settings
    _plugin_settings = settings
    if event_name == "on_shutdown":
        _fc_pool.shutdown(wait=False)
```

Remove the old config.json fallback code from `_get_api_key` (the plugin settings system replaces it).

- [ ] **Step 5: Move and adapt `static/fact_checker.js` → `plugins/fact_checker/panel.js`**

Change the IIFE pattern to use plugin registration. Replace the opening and closing:

**Old opening (line 11):**
```javascript
const FactChecker = (() => {
```

**New opening:**
```javascript
(function() {
```

**Old closing (lines 282-284):**
```javascript
  return { onTranscript, toggleEnabled, togglePanel };
})();
```

**New closing:**
```javascript
  // Register with plugin system
  window.LinguaTaxi.plugins.register('fact_checker', {
    on_final: (data) => onTranscript(data.text, data.speaker || ''),
    on_session_start: () => { queue = []; results = []; if(elResults) elResults.innerHTML = ''; renderState(); }
  });
})();
```

Remove the `toggleEnabled` and `togglePanel` from the public API — these are now handled by the standard plugin wrapper via `plugin_dispatcher.js`.

Update the `toggleEnabled()` function to listen for plugin system events instead:

```javascript
// Replace the old toggleEnabled function:
function toggleEnabled() {
  enabled = !enabled;
  if (!enabled) {
    queue = [];
    updateQueueStatus();
  }
  renderState();
}

// Add listener for plugin enable/disable:
window.LinguaTaxi.plugins.register('fact_checker', {
  on_final: (data) => onTranscript(data.text, data.speaker || ''),
  on_enabled: () => { enabled = true; renderState(); },
  on_disabled: () => { enabled = false; queue = []; updateQueueStatus(); renderState(); },
  on_session_start: () => { queue = []; results = []; if(elResults) elResults.innerHTML = ''; renderState(); }
});
```

- [ ] **Step 6: Move CSS**

```bash
cp static/fact_checker.css plugins/fact_checker/panel.css
```

No changes needed to the CSS — all selectors are already `.fc-*` scoped.

- [ ] **Step 7: Commit**

```bash
git add plugins/fact_checker/
git commit -m "[feat] create fact_checker plugin folder with manifest, routes, panel"
```

---

## Task 4: Wire Plugin System into server.py

**Files:**
- Modify: `server.py`

- [ ] **Step 1: Replace fact checker imports with plugin loader**

Find and replace (line 60):
```python
from fact_checker_routes import router as fact_checker_router
```

Replace with:
```python
from plugin_loader import PluginDispatcher
```

- [ ] **Step 2: Add plugin dispatcher initialization after config load**

Find (around line 187):
```python
config = load_config()
```

Add after it:
```python
# ── Plugin System ──
PLUGINS_DIR = BASE_DIR / "plugins"
PLUGINS_DIR.mkdir(exist_ok=True)
plugin_dispatcher = PluginDispatcher(PLUGINS_DIR, config)
```

- [ ] **Step 3: Replace hardcoded fact checker mount with plugin loading**

Find (lines 1180-1183):
```python
operator_app.include_router(fact_checker_router)
_static_dir = BASE_DIR / "static"
if _static_dir.is_dir():
    operator_app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")
```

Replace with:
```python
# ── Plugin System: discover and load ──
plugin_dispatcher.discover()
plugin_dispatcher.load_enabled(operator_app)

# Serve core static files (plugin_dispatcher.js, plugin_panel.css)
_static_dir = BASE_DIR / "static"
if _static_dir.is_dir():
    operator_app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")

# Serve each plugin's static files
for _m in plugin_dispatcher.get_all_manifests():
    _plugin_static = _m.path
    if _plugin_static.is_dir():
        operator_app.mount(f"/plugins/{_m.id}", StaticFiles(directory=str(_plugin_static)), name=f"plugin_{_m.id}")
```

- [ ] **Step 4: Change `o_index` to dynamic HTML assembly**

Find (lines 1185-1186):
```python
@operator_app.get("/")
async def o_index(): return FileResponse(BASE_DIR / "operator.html")
```

Replace with:
```python
@operator_app.get("/")
async def o_index():
    html = (BASE_DIR / "operator.html").read_text(encoding="utf-8")
    html = html.replace("<!-- PLUGIN_CSS -->", plugin_dispatcher.get_css_links())
    html = html.replace("<!-- PLUGIN_PANELS -->", plugin_dispatcher.get_panel_html())
    html = html.replace("<!-- PLUGIN_JS -->", plugin_dispatcher.get_js_scripts())
    return HTMLResponse(html)
```

Add `HTMLResponse` to the imports (line 56):
```python
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
```

- [ ] **Step 5: Add plugin API routes for settings and enable/disable**

Add after the `o_index` route:

```python
# ── Plugin API Routes ──

@operator_app.get("/api/plugins")
async def api_plugins_list():
    """List all discovered plugins with their status."""
    result = []
    for m in plugin_dispatcher.get_all_manifests():
        result.append({
            "id": m.id, "name": m.name, "version": m.version,
            "description": m.description, "author": m.author,
            "enabled": plugin_dispatcher.is_enabled(m.id),
            "has_panel": m.has_panel, "has_routes": m.has_routes,
            "settings_schema": m.settings_schema,
        })
    return JSONResponse(result)

@operator_app.get("/api/plugins/{plugin_id}/settings")
async def api_plugin_settings_get(plugin_id: str):
    manifests = {m.id: m for m in plugin_dispatcher.get_all_manifests()}
    m = manifests.get(plugin_id)
    if not m:
        return JSONResponse({"error": "Plugin not found"}, 404)
    return JSONResponse({
        "schema": m.settings_schema,
        "values": plugin_dispatcher.get_settings(plugin_id),
    })

@operator_app.post("/api/plugins/{plugin_id}/settings")
async def api_plugin_settings_post(plugin_id: str, request: Request):
    form = await request.form()
    settings = dict(form)
    # Convert checkbox string values to booleans
    manifests = {m.id: m for m in plugin_dispatcher.get_all_manifests()}
    m = manifests.get(plugin_id)
    if m:
        for key, schema in m.settings_schema.items():
            if schema.get("type") == "toggle" and key in settings:
                settings[key] = settings[key] in ("true", "True", "1", "on")
            elif schema.get("type") == "number" and key in settings:
                try:
                    settings[key] = float(settings[key])
                except ValueError:
                    pass
    plugin_dispatcher.save_settings(plugin_id, settings)
    save_config(config)
    plugin_dispatcher.fire("on_config_change", {"plugin_id": plugin_id})
    return JSONResponse({"ok": True})

@operator_app.post("/api/plugins/{plugin_id}/enabled")
async def api_plugin_enabled(plugin_id: str, request: Request):
    form = await request.form()
    enabled = form.get("enabled", "true") in ("true", "True", "1")
    plugin_dispatcher.set_enabled(plugin_id, enabled)
    save_config(config)
    return JSONResponse({"ok": True, "enabled": enabled})
```

- [ ] **Step 6: Add `plugin_dispatcher.fire()` calls at all 7 event points**

**6a. on_final** — in `_broadcast_final()`, after the `_translate_all` call (around line 934):
```python
    _translate_all(text, "final_translation", loop, line_id=lid, source_lang=detected_lang)
    plugin_dispatcher.fire("on_final", {
        "text": text, "speaker": speaker, "color": color,
        "source_id": source_id, "line_id": lid, "detected_lang": detected_lang
    })
```

**6b. on_interim** — after each `_bc(loop, {"type":"interim",...})` call (lines ~523 and ~796):
```python
    # After the _bc() call for interim:
    plugin_dispatcher.fire("on_interim", {
        "text": text, "speaker": source.speaker, "source_id": source.id
    })
```

**6c. on_translation** — in `_do_translate()`, after the broadcast of final_translation (search for `_bc(loop,` with `final_translation`):
```python
    plugin_dispatcher.fire("on_translation", {
        "translated": result, "lang": lang, "slot": slot,
        "speaker": speaker_override or "", "line_id": line_id, "source_lang": source_lang
    })
```

**6d. on_config_change** — at the end of `o_update()`, after `save_config(config)`:
```python
    plugin_dispatcher.fire("on_config_change", {"config": config})
```

**6e. on_session_start** — where `captioning_paused` is set to `False` in the WebSocket handler (line ~1736):
```python
    # After: await broadcast_all({"type":"captioning_paused","paused":False})
    if not captioning_paused:
        plugin_dispatcher.fire("on_session_start", {
            "timestamp": time.time(),
            "backend": stt_backend.name if stt_backend else "unknown",
        })
```

**6f. on_session_stop** — where `captioning_paused` is set to `True` (same handler):
```python
    if captioning_paused:
        plugin_dispatcher.fire("on_session_stop", {"timestamp": time.time()})
```

**6g. on_shutdown** — in the shutdown event handler:
```python
    plugin_dispatcher.shutdown()
```

- [ ] **Step 7: Remove `anthropic_api_key` from core config**

In `DEFAULT_CONFIG`, remove:
```python
    "anthropic_api_key": "",
```

In `o_config` GET response, remove:
```python
            "anthropic_api_key": config.get("anthropic_api_key",""),
            "has_anthropic_key": bool(config.get("anthropic_api_key","") or os.environ.get("ANTHROPIC_API_KEY","")),
```

In `o_update` POST handler, remove the `anthropic_api_key` form parameter and the line that saves it.

- [ ] **Step 8: Verify compilation**

Run: `python -c "import py_compile; py_compile.compile('server.py', doraise=True); print('OK')"`
Expected: `OK`

- [ ] **Step 9: Commit**

```bash
git add server.py
git commit -m "[feat] wire plugin system into server.py — dispatcher, dynamic HTML, plugin API"
```

---

## Task 5: Update operator.html with Plugin Markers

**Files:**
- Modify: `operator.html`

- [ ] **Step 1: Replace hardcoded fact checker CSS link with plugin marker**

Find (line 135):
```html
<link rel="stylesheet" href="/static/fact_checker.css">
```

Replace with:
```html
<link rel="stylesheet" href="/static/plugin_panel.css">
<!-- PLUGIN_CSS -->
```

- [ ] **Step 2: Replace hardcoded fact checker panel with plugin marker**

Find and remove the entire block (lines 253-270):
```html
  <!-- Fact Checker Panel -->
  <div id="fc-panel" class="fc-panel">
    ...
  </div>
```

Replace with:
```html
  <!-- PLUGIN_PANELS -->
```

- [ ] **Step 3: Replace hardcoded FactChecker.onTranscript with generic dispatch**

Find (line 1619):
```javascript
        if(typeof FactChecker!=='undefined') FactChecker.onTranscript(m.text, m.speaker||'');
```

Replace with:
```javascript
        if(window.LinguaTaxi&&window.LinguaTaxi.plugins) LinguaTaxi.plugins.fire('on_final',{text:m.text,speaker:m.speaker||'',line_id:m.line_id,detected_lang:m.detected_lang});
```

- [ ] **Step 4: Add generic plugin dispatch for other events in handleMsg**

In the interim handler:
```javascript
    else if(m.type==='interim'){
      prevSetInterim('caption', m.text||'', m.speaker||'');
      if(window.LinguaTaxi&&window.LinguaTaxi.plugins) LinguaTaxi.plugins.fire('on_interim',{text:m.text||'',speaker:m.speaker||''});
    }
```

In the final_translation handler:
```javascript
    else if(m.type==='final_translation'){
      // ... existing code ...
      if(window.LinguaTaxi&&window.LinguaTaxi.plugins) LinguaTaxi.plugins.fire('on_translation',{translated:m.translated,lang:m.lang,slot:m.slot,speaker:m.speaker||'',line_id:m.line_id});
    }
```

In the captioning_paused handler:
```javascript
    else if(m.type==='captioning_paused'){
      // ... existing code ...
      if(window.LinguaTaxi&&window.LinguaTaxi.plugins) LinguaTaxi.plugins.fire(m.paused?'on_session_stop':'on_session_start',{});
    }
```

- [ ] **Step 5: Replace hardcoded fact checker JS with plugin markers**

Find (line 1746):
```html
<script src="/static/fact_checker.js"></script>
```

Replace with:
```html
<script src="/static/plugin_dispatcher.js"></script>
<!-- PLUGIN_JS -->
```

- [ ] **Step 6: Commit**

```bash
git add operator.html
git commit -m "[feat] replace hardcoded fact checker with plugin markers in operator.html"
```

---

## Task 6: Clean Up Old Files and Update Build System

**Files:**
- Delete: `fact_checker_routes.py`
- Delete: `static/fact_checker.js`
- Delete: `static/fact_checker.css`
- Modify: `build/windows/installer.iss`
- Modify: `build/mac/build.sh`

- [ ] **Step 1: Delete old hardcoded files**

```bash
rm fact_checker_routes.py
rm static/fact_checker.js
rm static/fact_checker.css
```

- [ ] **Step 2: Update `build/windows/installer.iss`**

Find and remove:
```
Source: "..\..\fact_checker_routes.py"; DestDir: "{app}"; Flags: ignoreversion
```

Find and remove:
```
Source: "..\..\static\*"; DestDir: "{app}\static"; Flags: ignoreversion recursesubdirs createallsubdirs
```

Replace those with:
```
Source: "..\..\plugin_loader.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\..\static\*"; DestDir: "{app}\static"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\..\plugins\*"; DestDir: "{app}\plugins"; Flags: ignoreversion recursesubdirs createallsubdirs
```

- [ ] **Step 3: Update `build/mac/build.sh`**

Find and remove:
```bash
cp "$PROJECT_DIR/fact_checker_routes.py" "$RESOURCES/"
cp -r "$PROJECT_DIR/static" "$RESOURCES/static"
```

Replace with:
```bash
cp "$PROJECT_DIR/plugin_loader.py" "$RESOURCES/"
cp -r "$PROJECT_DIR/static" "$RESOURCES/static"
cp -r "$PROJECT_DIR/plugins" "$RESOURCES/plugins"
```

- [ ] **Step 4: Verify compilation of all files**

```bash
python -c "
import py_compile
for f in ['server.py', 'plugin_loader.py', 'plugins/fact_checker/routes.py', 'launcher.pyw']:
    py_compile.compile(f, doraise=True)
    print(f'{f}: OK')
print('All files compile clean.')
"
```

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "[refactor] move fact checker to plugin, clean up old files, update build system"
```

---

## Task 7: End-to-End Verification

- [ ] **Step 1: Verify plugin discovery**

```bash
python -c "
from plugin_loader import PluginDispatcher
from pathlib import Path
d = PluginDispatcher(Path('plugins'), {})
manifests = d.discover()
for m in manifests:
    print(f'{m.id} v{m.version} — hooks: {m.hooks}, panel: {m.has_panel}, routes: {m.has_routes}')
"
```

Expected: `fact_checker v1.0.0 — hooks: ['on_final', 'on_config_change', 'on_shutdown'], panel: True, routes: True`

- [ ] **Step 2: Start server and verify operator panel**

```bash
python server.py
```

Open `http://localhost:3001` in browser. Verify:
- Fact Checker panel appears with standard plugin wrapper (indicator dot, Enable button, gear icon)
- Clicking Enable activates the indicator
- Clicking gear opens settings dialog with API key field
- No console errors

- [ ] **Step 3: Verify fact checking works**

1. Set ANTHROPIC_API_KEY via the plugin settings gear icon
2. Click Enable on the fact checker
3. Go Live and speak a factual statement
4. Verify results appear in the panel

- [ ] **Step 4: Verify plugin disable**

1. Click Disable on the fact checker
2. Speak more — verify no new fact checks are queued
3. Restart server — verify disabled state persists

- [ ] **Step 5: Final commit**

```bash
git add -A
git commit -m "[feat] plugin architecture complete — fact checker is first plugin"
```
