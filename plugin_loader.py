"""
LinguaTaxi — Plugin Loader
Discovers, loads, and dispatches events to drop-in plugins from the plugins/ directory.
"""

import asyncio
import importlib
import importlib.util
import json
import logging
import sys
import time
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
        self._plugin_state: dict[str, dict] = {}  # retained state across unload/reload
        self._active_requests: dict[str, int] = {}  # per-plugin in-flight request counter

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

    # ------------------------------------------------------------------
    # Hot-loading / hot-unloading at runtime
    # ------------------------------------------------------------------

    def _create_plugin_subapp(self, plugin_id: str, router):
        """Create a FastAPI sub-app with request counting middleware."""
        from fastapi import FastAPI, Request
        from starlette.middleware.base import BaseHTTPMiddleware

        sub_app = FastAPI()
        sub_app.include_router(router)

        @sub_app.middleware("http")
        async def track_requests(request: Request, call_next):
            self._active_requests[plugin_id] = self._active_requests.get(plugin_id, 0) + 1
            try:
                response = await call_next(request)
                return response
            finally:
                self._active_requests[plugin_id] -= 1

        return sub_app

    async def _drain_plugin(self, plugin_id: str, timeout: float = 5.0):
        """Wait for in-flight requests to complete, up to timeout."""
        start = time.monotonic()
        while self._active_requests.get(plugin_id, 0) > 0:
            if time.monotonic() - start > timeout:
                log.warning(
                    "Plugin %s: drain timeout (%d active), forcing unload",
                    plugin_id, self._active_requests.get(plugin_id, 0))
                break
            await asyncio.sleep(0.1)

    def _unmount_plugin(self, plugin_id: str, app):
        """Remove a mounted sub-app from the parent app."""
        manifest = self._manifests.get(plugin_id)
        if not manifest:
            return
        prefix = manifest.route_prefix
        app.routes[:] = [
            r for r in app.routes
            if not (hasattr(r, 'path') and r.path == prefix)
        ]

    def fire_sync(self, plugin_id: str, event_name: str, data: dict):
        """Fire event to a specific plugin synchronously, capturing return value."""
        lp = self._plugins.get(plugin_id)
        if not lp or not lp.module:
            return None
        handler = getattr(lp.module, "handle_event", None)
        if handler:
            try:
                return handler(event_name, data, self.get_settings(plugin_id))
            except Exception as e:
                log.error(f"Plugin '{plugin_id}' error on {event_name}: {e}")
        return None

    def hot_load(self, plugin_id: str, app) -> bool:
        """
        Load a plugin at runtime and mount its routes as a sub-app.

        Args:
            plugin_id: The plugin identifier
            app: The FastAPI app to mount the sub-app on (operator_app)

        Returns: True if successful
        """
        manifest = self._manifests.get(plugin_id)
        if not manifest:
            log.error(f"hot_load: plugin '{plugin_id}' not found in discovered manifests")
            return False

        lp = LoadedPlugin(manifest)
        plugin_dir = manifest.path

        # Add lib/ to path if present
        lib_dir = plugin_dir / "lib"
        if lib_dir.is_dir():
            lib_str = str(lib_dir)
            if lib_str not in sys.path:
                sys.path.insert(0, lib_str)

        try:
            # Import plugin module
            if manifest.has_routes:
                routes_file = plugin_dir / "routes.py"
                if routes_file.exists():
                    module_name = f"plugin_{manifest.id}_routes"
                    # Remove old module if cached
                    if module_name in sys.modules:
                        del sys.modules[module_name]
                    spec = importlib.util.spec_from_file_location(
                        module_name, str(routes_file))
                    mod = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(mod)
                    lp.module = mod

                    # Mount as sub-app with request tracking
                    if app and hasattr(mod, "router"):
                        sub_app = self._create_plugin_subapp(plugin_id, mod.router)
                        app.mount(manifest.route_prefix, sub_app)
                        log.info(f"Plugin '{plugin_id}': hot-mounted sub-app at {manifest.route_prefix}")

            # Load panel assets
            if manifest.has_panel:
                html_file = plugin_dir / "panel.html"
                if html_file.exists():
                    lp.panel_html = html_file.read_text(encoding="utf-8")
                js_file = plugin_dir / "panel.js"
                if js_file.exists():
                    lp.panel_js_path = f"/plugins/{manifest.id}/panel.js"
                css_file = plugin_dir / "panel.css"
                if css_file.exists():
                    lp.panel_css_path = f"/plugins/{manifest.id}/panel.css"

            # Inject plugin_api if the module expects it
            if lp.module and hasattr(lp.module, "plugin_api"):
                # plugin_api injection is handled externally by the caller
                pass

            # Fire on_load with retained state
            retained = self._plugin_state.get(plugin_id)
            load_data = {"retained_state": retained} if retained else {}
            self.fire_sync(plugin_id, "on_load", load_data)

            # Fire on_config_change to initialize
            self.fire_sync(plugin_id, "on_config_change", self.get_settings(plugin_id))

            # Register hooks
            with self._lock:
                for hook in manifest.hooks:
                    if hook not in self._hooks:
                        self._hooks[hook] = []
                    if lp not in self._hooks[hook]:
                        self._hooks[hook].append(lp)

            # Mark as loaded
            self._plugins[plugin_id] = lp
            self._active_requests.setdefault(plugin_id, 0)
            log.info(f"Plugin '{plugin_id}' v{manifest.version} hot-loaded")
            return True

        except Exception as e:
            lp.error = str(e)[:300]
            log.error(f"Plugin '{plugin_id}' hot_load failed: {e}")
            self._plugins[plugin_id] = lp
            return False

    async def hot_unload(self, plugin_id: str, app) -> dict | None:
        """
        Unload a plugin at runtime. Graceful drain then unmount.

        Args:
            plugin_id: The plugin identifier
            app: The FastAPI app to unmount from

        Returns: Retained state dict (or None)
        """
        lp = self._plugins.get(plugin_id)
        if not lp:
            log.warning(f"hot_unload: plugin '{plugin_id}' not loaded")
            return None

        # Fire on_disable event
        self.fire_sync(plugin_id, "on_disable", {})

        # Wait for active requests to drain (5s timeout)
        await self._drain_plugin(plugin_id, timeout=5.0)

        # Fire on_unload — capture return value as retained state
        retained_state = self.fire_sync(plugin_id, "on_unload", {})
        if retained_state is not None and not isinstance(retained_state, dict):
            retained_state = {"_value": retained_state}

        # Unmount sub-app from main app
        self._unmount_plugin(plugin_id, app)

        # Unregister event hooks
        with self._lock:
            for hook_list in self._hooks.values():
                hook_list[:] = [p for p in hook_list if p is not lp]

        # Remove module from sys.modules
        if lp.module:
            module_name = f"plugin_{plugin_id}_routes"
            if module_name in sys.modules:
                del sys.modules[module_name]

        # Store retained state
        if retained_state is not None:
            self._plugin_state[plugin_id] = retained_state

        # Mark as unloaded — keep entry but clear module
        lp.module = None
        log.info(f"Plugin '{plugin_id}' hot-unloaded (state retained: {retained_state is not None})")

        return retained_state

    def shutdown(self):
        """Fire shutdown event and clean up thread pool."""
        if getattr(self, '_shutdown_done', False):
            return
        self._shutdown_done = True
        self.fire("on_shutdown", {})
        self._pool.shutdown(wait=False)
