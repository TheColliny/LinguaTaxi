# Plugin Marketplace & Hot-Loading Design

## Overview

Transform LinguaTaxi's plugin system from bundled-in-source to a downloadable marketplace. Plugins are hosted on GitHub, browsable from both the operator panel and launcher GUI, and hot-loaded/unloaded at runtime without server restart. The launcher GUI is also modernized with CustomTkinter to match the operator panel's dark aesthetic.

## Goals

1. Plugins distributed via a dedicated GitHub repo, not bundled in the installer
2. In-app plugin browser with install/update/uninstall
3. Hot-load plugins when placed on the operator grid (no restart)
4. Graceful unload with state retention for session continuity
5. Modern dark-themed launcher GUI consistent with operator panel design language

---

## 1. GitHub Plugin Repository

**Repo:** `TheColliny/linguataxi-plugins`

### Structure

```
linguataxi-plugins/
├── registry.json          ← plugin catalog (source of truth)
├── fact_checker/          ← source subfolder (development reference)
│   ├── manifest.json
│   ├── routes.py
│   ├── panel.html
│   ├── panel.js
│   ├── panel.css
│   └── ...
├── donor_cloud/
├── live_dial/
└── polls_checker/
```

### Registry Schema (`registry.json`)

```json
[
  {
    "id": "fact_checker",
    "name": "Fact Checker",
    "description": "Real-time fact-checking with MBFC source credibility, MAGI multi-provider consensus, and flip-flop detection.",
    "version": "1.4.0",
    "author": "LinguaTaxi",
    "min_app_version": "1.0.2",
    "compatibility": "cpu+gpu",
    "download_size": "2.1 MB",
    "asset_filename": "fact_checker-v1.4.0.zip"
  }
]
```

**Field definitions:**
- `id`: Plugin identifier, matches folder name. Pattern: `[a-z0-9_-]+`
- `version`: Semver string of the plugin release
- `min_app_version`: Minimum LinguaTaxi version required to run this plugin
- `compatibility`: One of `cpu+gpu`, `gpu_only`, `cpu_only`
- `download_size`: Human-readable size string for display
- `asset_filename`: Filename of the zip asset attached to the GitHub Release

### Releases

Each plugin version is published as a zip file attached to a GitHub Release on the plugins repo. The zip contains the plugin folder contents (not a nested directory — extracts directly into `plugins/{id}/`).

Release naming convention: tag `v{plugin_id}-{version}` (e.g., `v-fact_checker-1.4.0`).

---

## 2. Plugin Registry Module

**New file:** `plugin_registry.py`

### Responsibilities

- Fetch `registry.json` from GitHub (raw content URL)
- Compare installed plugin versions against registry
- Download plugin zip from GitHub Release assets
- Extract zip to `plugins/` directory
- Delete plugin folder on uninstall
- Cache registry locally for offline/error resilience

### API

```python
class PluginRegistry:
    def __init__(self, plugins_dir: Path, github_repo: str):
        ...

    async def fetch_registry(self) -> list[dict]:
        """Fetch full registry from GitHub. Caches result."""

    def get_installed(self) -> dict[str, str]:
        """Scan plugins/ dir, return {id: version} for installed plugins."""

    def check_updates(self) -> list[dict]:
        """Compare installed vs registry. Returns entries with available updates."""

    async def install_plugin(self, plugin_id: str) -> Path:
        """Download and extract plugin zip. Returns plugin directory path."""

    async def update_plugin(self, plugin_id: str) -> Path:
        """Download new version, replace existing files."""

    def uninstall_plugin(self, plugin_id: str) -> bool:
        """Delete plugin directory. Returns True if successful."""

    def is_compatible(self, entry: dict) -> bool:
        """Check if plugin is compatible with current app version and edition."""
```

### GitHub Communication

- **Registry fetch:** `GET https://raw.githubusercontent.com/TheColliny/linguataxi-plugins/main/registry.json`
- **Zip download:** GitHub Releases API → asset download URL
- **Timeout:** 10s for registry, 60s for zip downloads
- **Error handling:** network failures return cached registry (if available) or empty list
- **Rate limiting:** GitHub API allows 60 requests/hour unauthenticated (sufficient for this use case)

### Boot Update Check

On server start, a background thread:
1. Fetches `registry.json`
2. Compares only installed plugin versions
3. Stores update list in memory
4. Operator panel and launcher can query this without re-fetching

---

## 3. Plugin Loader Hot-Loading

**Enhanced file:** `plugin_loader.py`

### Sub-Application Architecture

Each plugin's routes are mounted as an independent FastAPI sub-app rather than included as a router on the main app. This enables runtime mount/unmount.

```python
# Current (router inclusion — can't remove at runtime):
operator_app.include_router(plugin_router)

# New (sub-app mounting — can mount/unmount dynamically):
plugin_app = FastAPI()
plugin_app.include_router(plugin_router)
operator_app.mount(f"/api/{plugin_id}", plugin_app)
```

### Lifecycle States

```
NOT_INSTALLED → DOWNLOADED → LOADED → UNLOADED (state retained) → RELOADED
                                ↑                                      │
                                └──────────────────────────────────────┘
```

**State transitions:**
- `DOWNLOADED`: Plugin files exist in `plugins/` but module not loaded
- `LOADED`: Plugin placed on operator grid → module imported, sub-app mounted, events firing
- `UNLOADED`: Plugin removed from grid → graceful drain → unmount → resources freed → state retained
- `RELOADED`: Plugin placed back on grid → remount, restore state from retention store

### Hot-Load Process

```python
def hot_load(self, plugin_id: str) -> bool:
    """Load a plugin at runtime. Called when placed on operator grid."""
    # 1. Import plugin module (routes.py)
    # 2. Create FastAPI sub-app with plugin's router
    # 3. Mount sub-app at plugin's route prefix
    # 4. Inject plugin_api with read/write settings access
    # 5. Restore retained state if any (pass via on_load event)
    # 6. Fire on_config_change to initialize plugin
    # 7. Register event hooks
    # 8. Load panel HTML/JS/CSS (push to connected operator clients via WebSocket)
```

### Graceful Unload Process

```python
def hot_unload(self, plugin_id: str) -> bool:
    """Unload a plugin at runtime. Called when removed from operator grid."""
    # 1. Fire on_disable event
    # 2. Wait for in-flight requests to complete (5s timeout)
    # 3. Fire on_unload event — plugin returns state to retain
    # 4. Unmount sub-app from operator_app
    # 5. Unregister event hooks (stop firing on_final etc.)
    # 6. Free heavy resources (plugin is responsible in on_unload handler)
    # 7. Store returned state in _plugin_state[plugin_id]
    # 8. Unload module from sys.modules
```

### State Retention

```python
_plugin_state: dict[str, dict] = {}
# Key: plugin_id
# Value: arbitrary dict returned by plugin's on_unload handler

# Plugin contract:
def handle_event(event_name, data, settings):
    if event_name == "on_unload":
        # Return state to retain across unload/reload
        return {"results": results, "cache": cache_data}
    elif event_name == "on_load":
        # Restore retained state
        restored = data.get("retained_state")
        if restored:
            results = restored.get("results", [])
```

State is held in memory for the session lifetime. Discarded on program exit. Plugins that need cross-session persistence write to their own files (e.g., `data/` subfolder).

### Dual-Path Plugin Scanning

```python
def _get_plugin_dirs(self) -> list[Path]:
    """Return plugin directories to scan, in priority order."""
    dirs = [APP_DIR / "plugins"]
    # Fallback if primary is not writable (e.g., installed to Program Files)
    if not os.access(dirs[0], os.W_OK):
        fallback = APPDATA_DIR / "plugins"
        fallback.mkdir(parents=True, exist_ok=True)
        dirs.insert(0, fallback)
    return dirs
```

### In-Flight Request Draining

When unloading, the sub-app needs to finish active requests:

```python
async def _drain_plugin(self, plugin_id: str, timeout: float = 5.0):
    """Wait for in-flight requests to complete, then force-stop."""
    plugin = self._loaded[plugin_id]
    start = time.monotonic()
    while plugin.active_requests > 0:
        if time.monotonic() - start > timeout:
            log.warning("Plugin %s: drain timeout, forcing unload", plugin_id)
            break
        await asyncio.sleep(0.1)
```

Plugins track active requests via middleware on their sub-app.

---

## 4. Launcher GUI Modernization

### Dependency

```
pip install customtkinter>=5.2
```

CustomTkinter provides modern dark-themed widgets as drop-in replacements for tkinter/ttk. Added to the project's requirements and bundled in the installer venv.

### Visual Design Tokens

| Token | Value | Usage |
|-------|-------|-------|
| Background | `#0d0d1a` | Window and frame backgrounds |
| Card | `#12122a` | Section card backgrounds |
| Border | `rgba(255,255,255,0.08)` → `#1f1f2e` | Card borders |
| Accent | `#4FC3F7` | Primary buttons, active states |
| Text Primary | `#ffffff` | Headings, important text |
| Text Secondary | `#a0a0a0` | Descriptions, labels |
| Text Muted | `#606060` | Hints, timestamps |
| Success | `#8BC34A` | Installed badges, running status |
| Warning | `#FFD54F` | Update available badges |
| Error | `#ff6b6b` | Stop button, uninstall |
| Font Family | Segoe UI (Win), SF Pro (Mac) | All text |
| Border Radius | 6-8px | Cards, buttons, inputs |

### Widget Mapping

| Current (ttk) | New (CustomTkinter) |
|----------------|-------------------|
| `ttk.Button` | `ctk.CTkButton` |
| `ttk.Label` | `ctk.CTkLabel` |
| `ttk.Entry` | `ctk.CTkEntry` |
| `ttk.Combobox` | `ctk.CTkOptionMenu` or `ctk.CTkComboBox` |
| `ttk.Frame` | `ctk.CTkFrame` |
| `ttk.LabelFrame` | `ctk.CTkFrame` with label |
| `ttk.Scrollbar` | `ctk.CTkScrollbar` |
| `tk.Text` | `ctk.CTkTextbox` |
| `tk.Canvas` (scrollable) | `ctk.CTkScrollableFrame` |
| `ttk.Progressbar` | `ctk.CTkProgressBar` |
| `ttk.Checkbutton` | `ctk.CTkCheckBox` |

### Layout Structure (unchanged logic, modernized look)

```
Window (CTk, 680×740, dark mode)
├── Header: App name, edition, version, language selector
├── Server Control card: status dot, label, Start/Stop buttons
├── Main Controls card: Operator, Main Display, Extended Display buttons
├── Extended Features card: Dictation, Bidirectional buttons
├── Settings card:
│   ├── Audio source dropdown
│   ├── Backend dropdown
│   ├── Model buttons (2×2 grid)
│   └── Download Plugins button (with update badge)
├── Server Log card: CTkTextbox with colored output
├── Transcript Location card: entry + Browse + Open
└── Footer: About button, version label
```

### Download Plugins Dialog

Opens as a `CTkToplevel` window (modal). Card-based list matching the operator panel mockup:

- Each plugin: name, description, version, compatibility badge, size, status
- Action buttons: Install (cyan), Update (yellow), Uninstall (red)
- Progress indicator during downloads
- Refresh button to re-fetch registry
- "Last checked" timestamp in footer

### Model Download Dialogs (modernized)

All existing dialogs (Tuned Models, Offline Translation, Vosk Models, Model Manager) converted to the same card-based dark style:

- Checkbox list with model name, size, installed badge
- "Download Selected" button
- Progress bar during download
- Consistent with plugin browser styling

---

## 5. Operator Panel Plugin Browser

### Entry Point

New button in the plugin grid header (`pg-header`):

```html
<button class="pg-btn" onclick="openPluginStore()">Plugins</button>
```

### Modal Overlay

Opens a full-panel overlay (same z-index pattern as settings dialog) with:

- Header: "Plugin Store" title, plugin count, Refresh button, Close button
- Scrollable card list (one card per registry entry)
- Per-card: name, description, metadata row (author, version requirement, compatibility, size)
- Status badges: INSTALLED (green), UPDATE (yellow), version tag (gray)
- Action buttons: Install, Update, Uninstall
- Progress state during download (spinner replaces button)

### Hot-Load on Install

When a plugin is installed and the operator immediately places it on the grid:
1. Install completes → plugin files in `plugins/`
2. Plugin appears in palette (available to drag)
3. User drags to grid cell → triggers `hot_load()`
4. Plugin is live — no restart needed

### Update Flow

When updating an installed plugin that's currently on the grid:
1. User clicks Update
2. System calls `hot_unload()` (graceful drain, state retained)
3. Old files replaced with new zip contents
4. System calls `hot_load()` with retained state
5. Plugin resumes with no data loss

---

## 6. Installer Changes

### Inno Setup Modifications

```ini
[Dirs]
; Ensure plugins directory exists but is never cleaned
Name: "{app}\plugins"; Flags: uninsneveruninstall

[UninstallDelete]
; Only delete plugins if user chooses to
; (handled via custom uninstall page)

[Code]
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usPostUninstall then
  begin
    if MsgBox('Delete downloaded plugins and their data?',
              mbConfirmation, MB_YESNO) = IDYES then
    begin
      DelTree(ExpandConstant('{app}\plugins'), True, True, True);
    end;
  end;
end;
```

### Migration from Bundled Plugins

Current installs have plugins bundled in the source tree. For the transition:
- Existing users who upgrade retain their `plugins/` folder (installer preserves it)
- New installs start with an empty `plugins/` folder
- The installer no longer includes plugin files — users download them from the plugin browser on first launch
- A first-run prompt suggests popular plugins when `plugins/` is empty

### New Dependencies in Venv

```
customtkinter>=5.2
onnxruntime>=1.14  (for claim filter model)
```

These are pre-installed in the build venv and shipped with the installer.

---

## 7. Update Notifications

### Boot Check (background)

```python
# In server.py startup or plugin_registry.py
def _boot_update_check():
    """Background thread: check for plugin updates on server start."""
    registry = plugin_registry.fetch_registry()  # sync, in thread
    updates = plugin_registry.check_updates(registry)
    if updates:
        _pending_updates = updates  # stored for UI queries
```

### Launcher Badge

The launcher's "Download Plugins" button shows a badge:
```
Download Plugins  [1 update]
```

Queries the server's `/api/plugins/updates` endpoint (returns pending update count).

### Operator Panel Badge

The palette or plugin grid header shows a small notification dot/badge when updates are available. Clicking opens the plugin browser with updates highlighted.

### API Endpoint

```python
@operator_app.get("/api/plugins/updates")
async def get_plugin_updates():
    """Return available updates for installed plugins."""
    return {"updates": _pending_updates or []}
```

---

## 8. Server API Additions

### New Endpoints

```python
GET  /api/plugins/registry         → fetch full registry (triggers GitHub call if stale)
GET  /api/plugins/updates          → return pending updates (from boot check cache)
POST /api/plugins/install/{id}     → download and install plugin
POST /api/plugins/update/{id}      → update installed plugin
DELETE /api/plugins/uninstall/{id}  → uninstall plugin (unload first if active)
POST /api/plugins/load/{id}        → hot-load plugin (mount sub-app)
POST /api/plugins/unload/{id}      → hot-unload plugin (graceful drain)
```

### Existing Endpoints (unchanged)

```python
GET  /api/plugins                          → list all installed manifests + enabled status
GET  /api/plugins/{plugin_id}/settings     → get settings schema + current values
POST /api/plugins/{plugin_id}/settings     → save settings
POST /api/plugins/{plugin_id}/enabled      → toggle enabled/disabled
```

---

## 9. Plugin Developer Contract

### New Event Hooks

| Event | Data | Return | When |
|-------|------|--------|------|
| `on_load` | `{"retained_state": dict\|None}` | — | Plugin mounted to grid |
| `on_unload` | `{}` | `dict` (state to retain) | Plugin removed from grid |

### Migration from Current System

Existing plugins need minimal changes:
1. Add `on_load` handler (optional — for state restoration)
2. Add `on_unload` handler (optional — for state export and resource cleanup)
3. Existing `on_shutdown` still fires on program exit (for final cleanup)
4. Existing `on_config_change`, `on_enabled`, `on_disabled` unchanged

### Sub-App Isolation

Plugins can no longer share state via module-level globals that persist across unload/reload (modules are removed from `sys.modules`). Persistent state must go through:
- `plugin_api.write_settings()` for config
- Return value of `on_unload` for session state
- File-based storage in plugin's `data/` subfolder for cross-session persistence
