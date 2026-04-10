"""
LinguaTaxi — Plugin Loader
Discovers, loads, and dispatches events to drop-in plugins from the plugins/ directory.
"""

import importlib
import importlib.util
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
        self.module = None
        self.panel_html = ""
        self.panel_js_path = ""
        self.panel_css_path = ""
        self.error = None


class PluginDispatcher:
    """Discovers plugins, loads enabled ones, dispatches events."""

    def __init__(self, plugins_dir: Path, config: dict):
        self.plugins_dir = plugins_dir
        self.config = config
        self._plugins: dict[str, LoadedPlugin] = {}
        self._manifests: dict[str, PluginManifest] = {}
        self._hooks: dict[str, list[LoadedPlugin]] = {}
        self._pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="plugin")
        self._lock = threading.Lock()

    def discover(self) -> list[PluginManifest]:
        """Scan plugins/ for folders with manifest.json."""
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
        return list(self._manifests.values())

    def is_enabled(self, plugin_id: str) -> bool:
        enabled = self.config.get("plugins_enabled", {})
        return enabled.get(plugin_id, True)

    def load_enabled(self, operator_app=None):
        """Load all enabled plugins."""
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
        m = lp.manifest
        plugin_dir = m.path

        lib_dir = plugin_dir / "lib"
        if lib_dir.is_dir():
            lib_str = str(lib_dir)
            if lib_str not in sys.path:
                sys.path.insert(0, lib_str)

        if m.has_routes:
            routes_file = plugin_dir / "routes.py"
            if routes_file.exists():
                spec = importlib.util.spec_from_file_location(
                    f"plugin_{m.id}_routes", str(routes_file))
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                lp.module = mod
                if operator_app and hasattr(mod, "router"):
                    operator_app.include_router(mod.router)
                    log.info(f"Plugin '{m.id}': mounted router at {m.route_prefix}")

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

        for hook in m.hooks:
            if hook not in self._hooks:
                self._hooks[hook] = []
            self._hooks[hook].append(lp)

        log.info(f"Plugin '{m.id}' v{m.version} loaded (hooks: {m.hooks})")

    def fire(self, event_name: str, data: dict):
        """Fire an event to all subscribed plugins. Non-blocking."""
        listeners = self._hooks.get(event_name, [])
        for lp in listeners:
            if not self.is_enabled(lp.manifest.id):
                continue
            if lp.module and hasattr(lp.module, "handle_event"):
                settings = self.get_settings(lp.manifest.id)
                self._pool.submit(self._safe_call, lp, event_name, data, settings)

    def _safe_call(self, lp: LoadedPlugin, event_name: str, data: dict, settings: dict):
        try:
            lp.module.handle_event(event_name, data, settings)
        except Exception as e:
            lp.error = f"{event_name}: {str(e)[:200]}"
            log.error(f"Plugin '{lp.manifest.id}' error on {event_name}: {e}")

    def get_settings(self, plugin_id: str) -> dict:
        return self.config.get("plugin_settings", {}).get(plugin_id, {})

    def save_settings(self, plugin_id: str, settings: dict):
        if "plugin_settings" not in self.config:
            self.config["plugin_settings"] = {}
        self.config["plugin_settings"][plugin_id] = settings

    def set_enabled(self, plugin_id: str, enabled: bool):
        if "plugins_enabled" not in self.config:
            self.config["plugins_enabled"] = {}
        self.config["plugins_enabled"][plugin_id] = enabled

    def get_css_links(self) -> str:
        lines = []
        for pid, lp in self._plugins.items():
            if self.is_enabled(pid) and lp.panel_css_path:
                lines.append(f'<link rel="stylesheet" href="{lp.panel_css_path}">')
        return "\n".join(lines)

    def get_panel_html(self) -> str:
        panels = []
        for pid, lp in self._plugins.items():
            m = lp.manifest
            error_badge = ""
            if lp.error:
                error_badge = f' <span class="plugin-error-badge" title="{lp.error}">!</span>'
            settings_btn = ""
            if m.settings_schema:
                settings_btn = f'<button class="plugin-settings-btn" onclick="event.stopPropagation(); LinguaTaxi.plugins.openSettings(\'{pid}\')">&#9881;</button>'

            panel = f'''<div class="plugin-panel" data-plugin-id="{pid}">
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
        lines = []
        for pid, lp in self._plugins.items():
            if self.is_enabled(pid) and lp.panel_js_path:
                lines.append(f'<script src="{lp.panel_js_path}"></script>')
        return "\n".join(lines)

    def get_settings_schemas(self) -> dict:
        return {pid: lp.manifest.settings_schema
                for pid, lp in self._plugins.items()
                if lp.manifest.settings_schema}

    def shutdown(self):
        """Fire shutdown event and clean up thread pool."""
        self.fire("on_shutdown", {})
        self._pool.shutdown(wait=False)
