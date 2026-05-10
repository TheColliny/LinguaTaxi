/**
 * LinguaTaxi — Plugin Grid Manager
 *
 * 4×4 CSS grid where the operator drags plugins from a palette into cells,
 * resizes cells by dragging bottom-right corner, rearranges by drag-and-drop
 * between cells. Layouts saved as named profiles (localStorage).
 *
 * Profile schema:
 * {
 *   grid: { "<cellKey>": {pluginId, colSpan, rowSpan, row, col} },
 *   plugin_enabled: { pluginId: bool },
 *   display_config: { bg_color, font_family, font_size, ... }  // optional
 * }
 *
 * Storage keys:
 *   lt_profiles          — dict of profile name -> profile object
 *   lt_current_profile   — name of currently-loaded profile
 */

(function(){
  const GRID_COLS = 4;
  const GRID_ROWS = 4;

  // State
  let currentProfile = localStorage.getItem('lt_current_profile') || 'Default';
  let activeDisplay = localStorage.getItem('lt_active_display') || 'main';  // 'main' | 'extended'
  let profiles = {};
  try { profiles = JSON.parse(localStorage.getItem('lt_profiles') || '{}'); } catch(e) {}

  // Migrate older profile format (single grid) to new (per-display grids)
  function migrateProfile(p) {
    if (!p) return { grids: { main: {}, extended: {} }, plugin_enabled: {}, display_config: null };
    if (p.grid && !p.grids) {
      p.grids = { main: p.grid, extended: {} };
      delete p.grid;
    }
    if (!p.grids) p.grids = { main: {}, extended: {} };
    if (!p.grids.main) p.grids.main = {};
    if (!p.grids.extended) p.grids.extended = {};
    if (!p.plugin_enabled) p.plugin_enabled = {};
    return p;
  }
  Object.keys(profiles).forEach(name => { profiles[name] = migrateProfile(profiles[name]); });
  if (!profiles[currentProfile]) {
    profiles[currentProfile] = { grids: { main: {}, extended: {} }, plugin_enabled: {}, display_config: null };
  }

  // Fields captured into display_config when explicitly saving a profile.
  // Excludes API keys (sensitive, stay on the machine) and backend (hardware-dependent).
  const PROFILE_CONFIG_FIELDS = [
    'session_title', 'input_lang', 'translation_count', 'translations',
    'speakers', 'speaker_langs', 'speaker_config',
    'font_size', 'max_lines', 'bg_color', 'font_family', 'caption_color',
    'ui_language',
    'bidirectional_enabled', 'bidirectional_langs', 'bidirectional_tuned_swap',
    'voice_id_enabled', 'voice_id_threshold',
  ];

  let grid = {};             // cellKey "r-c" -> { pluginId, colSpan, rowSpan, row, col } — points to grids[activeDisplay]
  let pluginEnabled = {};    // pluginId -> bool (mirrors server enable state)
  let dirty = false;         // true when current state differs from saved profile
  let draggedFromPalette = null;  // pluginId being dragged from palette
  let draggedFromCell = null;     // cellKey being dragged from grid
  let resizingCell = null;        // cellKey being resized

  // ── DOM refs ──
  let elGrid, elPalette, elPaletteItems, elProfileSel, elUnsavedDot, elDisplayTab;

  // Switch the active grid view (main vs extended). Saves current grid back to its slot first.
  function setActiveDisplay(which) {
    if (which !== 'main' && which !== 'extended') return;
    if (which === activeDisplay) return;
    // Persist current grid back to its slot
    profiles[currentProfile].grids[activeDisplay] = JSON.parse(JSON.stringify(grid));
    activeDisplay = which;
    localStorage.setItem('lt_active_display', which);
    // Load the new view's grid
    grid = JSON.parse(JSON.stringify(profiles[currentProfile].grids[activeDisplay] || {}));
    saveAutoBackup();
    buildAll();
    updateDisplayTabUI();
  }
  window.setActiveDisplay = setActiveDisplay;

  function updateDisplayTabUI() {
    if (!elDisplayTab) return;
    elDisplayTab.querySelectorAll('button').forEach(b => {
      b.classList.toggle('act', b.dataset.display === activeDisplay);
    });
  }

  // ── Virtual "live_captions" pseudo-plugin ──
  // Not loaded from the plugins/ directory — it's a built-in tile that mirrors
  // the live caption preview (the right-side preview panel) into a grid cell.
  function ensureLiveCaptionsPanel() {
    const hidden = document.getElementById('pluginPanelsHidden');
    if (!hidden) return;
    if (hidden.querySelector('[data-plugin-id="live_captions"]')) return;
    const panel = document.createElement('div');
    panel.className = 'plugin-panel';
    panel.dataset.pluginId = 'live_captions';
    panel.innerHTML =
      '<div class="plugin-title" style="display:none">Live Captions</div>' +
      '<div class="plugin-body">' +
        '<div id="lc-mirror" style="font-size:14px;line-height:1.5;color:rgba(255,255,255,.85);' +
          'overflow-y:auto;height:100%;padding:4px 2px"></div>' +
      '</div>';
    hidden.appendChild(panel);
  }

  // Mirror caption updates into the live_captions tile (works whether tile is in grid or not).
  // Uses LinguaTaxi.plugins event hooks fired by the operator's WebSocket handler.
  function wireLiveCaptionMirror() {
    if (!window.LinguaTaxi || !window.LinguaTaxi.plugins) return;
    const lines = [];
    const MAX_LINES = 12;
    function append(speaker, text, isInterim) {
      const mirror = document.getElementById('lc-mirror');
      if (!mirror) return;
      if (isInterim) {
        // replace last line if it was interim, else append
        if (lines.length && lines[lines.length-1].interim) lines.pop();
        lines.push({ speaker, text, interim: true });
      } else {
        // replace last interim if present, else append final
        if (lines.length && lines[lines.length-1].interim) lines.pop();
        lines.push({ speaker, text, interim: false });
      }
      while (lines.length > MAX_LINES) lines.shift();
      mirror.innerHTML = lines.map(l => {
        const sp = l.speaker ? '<span style="color:rgba(255,255,255,.45);font-weight:600;font-size:11px;text-transform:uppercase;letter-spacing:.05em;margin-right:6px">'+escHtml(l.speaker)+'</span>' : '';
        const cls = l.interim ? 'opacity:.5' : '';
        return '<div style="margin-bottom:4px;'+cls+'">' + sp + escHtml(l.text) + '</div>';
      }).join('');
      mirror.scrollTop = mirror.scrollHeight;
    }
    function escHtml(s) {
      return String(s == null ? '' : s)
        .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
    }
    window.LinguaTaxi.plugins.register('live_captions', {
      on_final:   (d) => append(d.speaker || '', d.text || '', false),
      on_interim: (d) => append(d.speaker || '', d.text || '', true),
      on_session_start: () => { lines.length = 0; const m=document.getElementById('lc-mirror'); if(m) m.innerHTML=''; },
    });
  }

  // ── Plugin discovery from server-injected HTML ──
  // Also injects synthetic tiles for live_captions and each translation slot.
  let _cachedTranslations = [];
  async function refreshTranslations() {
    try {
      const r = await fetch('/api/config');
      if (r.ok) {
        const c = await r.json();
        _cachedTranslations = c.translations || [];
      }
    } catch (e) {}
  }

  function ensureTranslationPanels() {
    const hidden = document.getElementById('pluginPanelsHidden');
    if (!hidden) return;
    const translations = _cachedTranslations || [];
    const existing = new Set(
      Array.from(hidden.querySelectorAll('.plugin-panel'))
        .map(el => el.dataset.pluginId)
        .filter(id => id && id.startsWith('translation_'))
    );
    // Add any missing translation tiles
    translations.forEach((tr, idx) => {
      const pid = 'translation_' + idx;
      if (existing.has(pid)) { existing.delete(pid); return; }
      const panel = document.createElement('div');
      panel.className = 'plugin-panel';
      panel.dataset.pluginId = pid;
      const label = tr.name || tr.lang || ('Translation ' + (idx + 1));
      panel.innerHTML =
        '<div class="plugin-title" style="display:none">' + label + '</div>' +
        '<div class="plugin-body"><div style="padding:6px;color:rgba(255,255,255,.5);font-size:11px">' +
          'Translation: ' + label + ' (rendered on audience display)' +
        '</div></div>';
      hidden.appendChild(panel);
    });
    // Remove orphaned translation tiles (translation removed in config)
    existing.forEach(pid => {
      const el = hidden.querySelector('[data-plugin-id="' + pid + '"]');
      if (el) el.remove();
    });
  }

  function getPluginPanels() {
    const hidden = document.getElementById('pluginPanelsHidden');
    if (!hidden) return [];
    ensureLiveCaptionsPanel();
    ensureTranslationPanels();
    // Search the whole document — panels may be in #pluginPanelsHidden OR
    // currently mounted in a grid cell (we move them around as the grid changes).
    return Array.from(document.querySelectorAll('.plugin-panel')).map(el => {
      const id = el.dataset.pluginId;
      let title;
      if (id === 'live_captions') {
        title = 'Live Captions';
      } else if (id.startsWith('translation_')) {
        const idx = parseInt(id.substring('translation_'.length), 10);
        const tr = (_cachedTranslations || [])[idx];
        title = tr ? ('Translation: ' + (tr.name || tr.lang)) : id;
      } else {
        title = el.querySelector('.plugin-title')?.textContent || id;
      }
      return { id, el, title };
    });
  }

  function getPluginById(id) {
    return getPluginPanels().find(p => p.id === id);
  }

  // ── State management ──
  async function loadCurrentProfile() {
    let p = profiles[currentProfile];
    if (!p) return;
    p = migrateProfile(p);
    profiles[currentProfile] = p;
    grid = JSON.parse(JSON.stringify(p.grids[activeDisplay] || {}));
    pluginEnabled = JSON.parse(JSON.stringify(p.plugin_enabled || {}));
    // Apply plugin enable states to server
    applyPluginEnables();
    // Apply display config (speakers, translations, styling) if the profile has one
    if (p.display_config) {
      await applyServerConfig(p.display_config);
    }
    // Push BOTH grids to the server so both audience displays update
    syncAllGridsToServer();
    dirty = false;
    buildAll();
    updateDisplayTabUI();
  }

  function markDirty() {
    dirty = true;
    if (elUnsavedDot) elUnsavedDot.style.display = '';
  }

  function clearDirty() {
    dirty = false;
    if (elUnsavedDot) elUnsavedDot.style.display = 'none';
  }

  // Auto-save current state back to the named profile on every change.
  // Debounced (250ms) to coalesce rapid drag/resize events — avoids both
  // localStorage thrashing (5MB quota) and server POST spam.
  let _saveTimer = null;
  function saveAutoBackup() {
    // Update in-memory profile immediately so reads see fresh state
    const p = migrateProfile(profiles[currentProfile] || {});
    p.grids[activeDisplay] = JSON.parse(JSON.stringify(grid));
    p.plugin_enabled = JSON.parse(JSON.stringify(pluginEnabled));
    profiles[currentProfile] = p;
    // Debounce the actual writes
    clearTimeout(_saveTimer);
    _saveTimer = setTimeout(() => {
      _saveTimer = null;
      try {
        localStorage.setItem('lt_profiles', JSON.stringify(profiles));
        localStorage.setItem('lt_current_profile', currentProfile);
        localStorage.setItem('lt_active_display', activeDisplay);
      } catch (e) {
        console.warn('Profile localStorage save failed (quota?):', e);
      }
    }, 250);
    // Push the active display's grid to the server so the audience display updates
    syncGridToServer();
  }

  // Debounced server sync — multiple rapid changes (drag, resize) collapse to one POST
  let _syncTimer = null;
  function syncGridToServer() {
    clearTimeout(_syncTimer);
    _syncTimer = setTimeout(() => {
      _syncTimer = null;
      fetch('/api/display-grids', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ display: activeDisplay, grid: grid }),
      }).catch(() => {});
    }, 200);
  }

  // Also push BOTH grids when a profile loads (so both audience displays update)
  function syncAllGridsToServer() {
    const p = profiles[currentProfile];
    if (!p || !p.grids) return;
    fetch('/api/display-grids', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ main: p.grids.main || {}, extended: p.grids.extended || {} }),
    }).catch(() => {});
  }

  // ── Auto-place new plugins ──
  // When a real plugin exists but has never been placed in the current grid
  // and has no entry in pluginEnabled (meaning the profile has never seen it),
  // auto-place it in the first available cell so the user can see its toggle
  // button. Placed as DISABLED so it doesn't auto-start (e.g. API calls).
  function autoPlaceNewPlugins() {
    const allPanels = getPluginPanels();
    const assigned = new Set(Object.values(grid).map(c => c.pluginId));
    allPanels.forEach(p => {
      // Skip synthetic tiles and already-placed plugins
      if (p.id === 'live_captions' || p.id.startsWith('translation_')) return;
      if (assigned.has(p.id)) return;
      // Skip if the profile already knows about this plugin (user deliberately removed it)
      if (pluginEnabled.hasOwnProperty(p.id)) return;
      // Find first empty cell
      for (let r = 0; r < GRID_ROWS; r++) {
        for (let c = 0; c < GRID_COLS; c++) {
          if (!isCellOccupied(r, c)) {
            const key = cellKey(r, c);
            grid[key] = { pluginId: p.id, row: r, col: c, colSpan: 1, rowSpan: 1 };
            pluginEnabled[p.id] = false;
            setServerPluginEnabled(p.id, false);
            saveAutoBackup();
            assigned.add(p.id);
            return; // placed — move to next plugin
          }
        }
      }
      // Grid full — plugin stays in palette (user can still drag it in)
    });
  }

  // ── Grid manipulation ──
  function cellKey(row, col) { return row + '-' + col; }

  function isCellOccupied(row, col, ignoreKey) {
    for (const [key, cell] of Object.entries(grid)) {
      if (key === ignoreKey) continue;
      const r0 = cell.row, c0 = cell.col;
      const r1 = r0 + cell.rowSpan - 1, c1 = c0 + cell.colSpan - 1;
      if (row >= r0 && row <= r1 && col >= c0 && col <= c1) return key;
    }
    return null;
  }

  function placePlugin(pluginId, row, col, colSpan = 1, rowSpan = 1) {
    // Clamp to valid grid bounds (defends against off-grid values from old profiles)
    row = Math.max(0, Math.min(GRID_ROWS - 1, row));
    col = Math.max(0, Math.min(GRID_COLS - 1, col));
    colSpan = Math.max(1, Math.min(GRID_COLS - col, colSpan));
    rowSpan = Math.max(1, Math.min(GRID_ROWS - row, rowSpan));
    // Remove any existing placement of this plugin
    removePluginFromGrid(pluginId);
    // Remove any cell that would overlap
    for (let r = row; r < row + rowSpan && r < GRID_ROWS; r++) {
      for (let c = col; c < col + colSpan && c < GRID_COLS; c++) {
        const occKey = isCellOccupied(r, c);
        if (occKey) delete grid[occKey];
      }
    }
    const key = cellKey(row, col);
    grid[key] = { pluginId, row, col, colSpan, rowSpan };
    pluginEnabled[pluginId] = true;
    setServerPluginEnabled(pluginId, true);
    markDirty();
    saveAutoBackup();
  }

  // Validate grid invariant: key === cellKey(cell.row, cell.col).
  // Run after any mutation to ensure no key/data drift.
  function rekeyGrid() {
    const fixed = {};
    for (const [key, cell] of Object.entries(grid)) {
      const expectedKey = cellKey(cell.row, cell.col);
      if (key !== expectedKey) {
        // Drift detected — reseat under the correct key
        fixed[expectedKey] = cell;
      } else {
        fixed[key] = cell;
      }
    }
    if (Object.keys(fixed).length !== Object.keys(grid).length ||
        Object.keys(fixed).some(k => fixed[k] !== grid[k])) {
      // Replace contents (preserve identity of grid object itself)
      Object.keys(grid).forEach(k => delete grid[k]);
      Object.assign(grid, fixed);
    }
  }

  function removePluginFromGrid(pluginId) {
    for (const [key, cell] of Object.entries(grid)) {
      if (cell.pluginId === pluginId) {
        delete grid[key];
      }
    }
    pluginEnabled[pluginId] = false;
    setServerPluginEnabled(pluginId, false);
    markDirty();
    saveAutoBackup();
  }

  function resizeCell(key, newColSpan, newRowSpan) {
    const cell = grid[key];
    if (!cell) return;
    newColSpan = Math.max(1, Math.min(GRID_COLS - cell.col, newColSpan));
    newRowSpan = Math.max(1, Math.min(GRID_ROWS - cell.row, newRowSpan));
    // Displace any cells that would be covered
    for (let r = cell.row; r < cell.row + newRowSpan; r++) {
      for (let c = cell.col; c < cell.col + newColSpan; c++) {
        if (r === cell.row && c === cell.col) continue;
        const occKey = isCellOccupied(r, c, key);
        if (occKey) delete grid[occKey];
      }
    }
    cell.colSpan = newColSpan;
    cell.rowSpan = newRowSpan;
    markDirty();
    saveAutoBackup();
  }

  function setServerPluginEnabled(pluginId, enabled) {
    // Skip server call for synthetic plugin IDs (live_captions and translation_N
    // are virtual tiles, not real plugins registered with the server)
    const isSynthetic = pluginId === 'live_captions' || pluginId.startsWith('translation_');
    if (!isSynthetic) {
      const fd = new FormData();
      fd.append('enabled', enabled ? 'true' : 'false');
      fetch('/api/plugins/' + pluginId + '/enabled', { method: 'POST', body: fd }).catch(()=>{});
    }
    // Fire plugin event so panels can react
    if (window.LinguaTaxi && window.LinguaTaxi.plugins) {
      window.LinguaTaxi.plugins.fire(enabled ? 'on_enabled' : 'on_disabled', { pluginId });
    }
  }

  function applyPluginEnables() {
    // Push the profile's plugin enable states to the server
    Object.entries(pluginEnabled).forEach(([pid, en]) => {
      setServerPluginEnabled(pid, en);
    });
  }

  // ── Server config capture & apply (for profiles) ──
  async function captureServerConfig() {
    try {
      const resp = await fetch('/api/config');
      if (!resp.ok) return null;
      const cfg = await resp.json();
      const snap = {};
      PROFILE_CONFIG_FIELDS.forEach(k => {
        if (cfg[k] !== undefined) snap[k] = cfg[k];
      });
      return snap;
    } catch (e) {
      console.warn('captureServerConfig failed:', e);
      return null;
    }
  }

  async function applyServerConfig(snap) {
    if (!snap || typeof snap !== 'object') return;
    try {
      const fd = new FormData();
      // Map snapshot keys to the POST /api/config Form field names
      if (snap.session_title != null) fd.append('session_title', snap.session_title);
      if (snap.input_lang != null) fd.append('input_lang', snap.input_lang);
      if (snap.translation_count != null) fd.append('translation_count', String(snap.translation_count));
      if (snap.translations != null) fd.append('translations_json', JSON.stringify(snap.translations));
      if (snap.speakers != null) fd.append('speakers', JSON.stringify(snap.speakers));
      if (snap.font_size != null) fd.append('font_size', String(snap.font_size));
      if (snap.max_lines != null) fd.append('max_lines', String(snap.max_lines));
      if (snap.bg_color != null) fd.append('bg_color', snap.bg_color);
      if (snap.font_family != null) fd.append('font_family', snap.font_family);
      if (snap.caption_color != null) fd.append('caption_color', snap.caption_color);
      if (snap.ui_language != null) fd.append('ui_language', snap.ui_language);
      if (snap.speaker_langs != null) fd.append('speaker_langs', JSON.stringify(snap.speaker_langs));
      if (snap.bidirectional_enabled != null) fd.append('bidirectional_enabled', String(snap.bidirectional_enabled));
      if (snap.bidirectional_langs != null) fd.append('bidirectional_langs', JSON.stringify(snap.bidirectional_langs));
      if (snap.bidirectional_tuned_swap != null) fd.append('bidirectional_tuned_swap', String(snap.bidirectional_tuned_swap));
      await fetch('/api/config', { method: 'POST', body: fd });
      // Give the frontend a chance to reflect the new config
      setTimeout(() => {
        if (typeof window.loadCfg === 'function') window.loadCfg();
      }, 200);
    } catch (e) {
      console.warn('applyServerConfig failed:', e);
    }
  }

  // ── Rendering ──
  function buildAll() {
    buildGrid();
    buildPalette();
    buildProfileSelector();
  }

  function buildGrid() {
    if (!elGrid) return;
    // Defensive: ensure cell keys match their stored row/col (catches drift)
    rekeyGrid();
    // Before tearing down cells, return any plugin panels currently mounted
    // in cells back to the hidden container so we can re-mount them fresh.
    // This preserves DOM identity (IDs, event listeners, panel.js refs).
    const hidden = document.getElementById('pluginPanelsHidden');
    if (hidden) {
      elGrid.querySelectorAll('.plugin-panel').forEach(panel => hidden.appendChild(panel));
    }
    elGrid.innerHTML = '';

    // Track which cells are occupied for rendering empty placeholders
    const occupied = new Set();
    Object.values(grid).forEach(cell => {
      for (let r = cell.row; r < cell.row + cell.rowSpan; r++) {
        for (let c = cell.col; c < cell.col + cell.colSpan; c++) {
          occupied.add(cellKey(r, c));
        }
      }
    });

    // Render empty cells first (as drop targets)
    for (let r = 0; r < GRID_ROWS; r++) {
      for (let c = 0; c < GRID_COLS; c++) {
        if (occupied.has(cellKey(r, c))) continue;
        const cell = document.createElement('div');
        cell.className = 'pg-cell pg-cell--empty';
        cell.style.gridColumn = (c + 1) + ' / span 1';
        cell.style.gridRow = (r + 1) + ' / span 1';
        cell.dataset.row = r;
        cell.dataset.col = c;
        cell.innerHTML = '<div class="pg-empty-hint">Drop<br>plugin</div>';
        attachDropHandlers(cell, r, c);
        elGrid.appendChild(cell);
      }
    }

    // Render occupied cells
    Object.entries(grid).forEach(([key, cell]) => {
      const plugin = getPluginById(cell.pluginId);
      if (!plugin) {
        delete grid[key];
        return;
      }
      const div = document.createElement('div');
      div.className = 'pg-cell pg-cell--occupied';
      div.style.gridColumn = (cell.col + 1) + ' / span ' + cell.colSpan;
      div.style.gridRow = (cell.row + 1) + ' / span ' + cell.rowSpan;
      div.dataset.cellKey = key;
      div.dataset.row = cell.row;
      div.dataset.col = cell.col;
      div.draggable = true;

      // Header
      const header = document.createElement('div');
      header.className = 'pg-cell-header';
      const isEnabled = pluginEnabled[cell.pluginId] !== false;
      const toggleLabel = isEnabled ? 'Disable' : 'Enable';
      const toggleCls = isEnabled ? 'pg-toggle-btn pg-toggle-btn--on' : 'pg-toggle-btn';
      header.innerHTML =
        '<span class="pg-cell-handle" title="Drag to move">⋮⋮</span>' +
        '<span class="pg-cell-title">' + esc(plugin.title) + '</span>' +
        '<span class="pg-cell-actions">' +
          '<button class="' + toggleCls + '" onclick="event.stopPropagation();window._pgToggleEnabled(\'' + cell.pluginId + '\')" title="' + toggleLabel + ' this plugin">' + toggleLabel + '</button>' +
          '<button onclick="event.stopPropagation();window.LinguaTaxi.plugins.openSettings(\'' + plugin.id + '\')" title="Settings">⚙</button>' +
          '<button onclick="event.stopPropagation();window._pgRemoveCell(\'' + key + '\')" title="Remove from grid">✕</button>' +
        '</span>';
      div.appendChild(header);

      // Body: move the entire .plugin-panel element into the cell.
      // This preserves DOM identity (so panel.js's getElementById refs stay valid)
      // and allows non-destructive rebuild — buildGrid moves panels back to
      // #pluginPanelsHidden first, then re-mounts them in their new cells.
      const body = document.createElement('div');
      body.className = 'pg-cell-body';
      // Append the whole panel element (panel.body inside it)
      body.appendChild(plugin.el);
      // Hide the panel's own header/title since the cell has its own
      const t = plugin.el.querySelector('.plugin-title');
      if (t) t.style.display = 'none';
      const ph = plugin.el.querySelector('.plugin-header');
      if (ph) ph.style.display = 'none';
      div.appendChild(body);

      // Drag/drop for rearranging
      attachCellDragHandlers(div, key);
      attachDropHandlers(div, cell.row, cell.col);

      elGrid.appendChild(div);
    });
  }

  function buildPalette() {
    if (!elPaletteItems) return;
    const assigned = new Set(Object.values(grid).map(c => c.pluginId));
    const available = getPluginPanels().filter(p => !assigned.has(p.id));

    elPaletteItems.innerHTML = '';
    if (available.length === 0) {
      elPaletteItems.innerHTML = '<div class="pg-palette-empty">All plugins in use</div>';
      return;
    }
    available.forEach(p => {
      const tile = document.createElement('div');
      tile.className = 'pg-tile';
      tile.draggable = true;
      tile.dataset.pluginId = p.id;
      // Pull description from manifest panel title attribute if set
      tile.innerHTML =
        '<div class="pg-tile-name">' + esc(p.title) + '</div>' +
        '<div class="pg-tile-desc">Drag to a cell</div>';
      attachPaletteDragHandlers(tile, p.id);
      elPaletteItems.appendChild(tile);
    });
  }

  function buildProfileSelector() {
    if (!elProfileSel) return;
    elProfileSel.innerHTML = '';
    Object.keys(profiles).sort().forEach(name => {
      const opt = document.createElement('option');
      opt.value = name;
      opt.textContent = name;
      if (name === currentProfile) opt.selected = true;
      elProfileSel.appendChild(opt);
    });
  }

  // ── Drag and Drop: Palette → Grid ──
  function attachPaletteDragHandlers(tile, pluginId) {
    tile.addEventListener('dragstart', (e) => {
      draggedFromPalette = pluginId;
      draggedFromCell = null;
      e.dataTransfer.effectAllowed = 'move';
      e.dataTransfer.setData('text/plain', 'palette:' + pluginId);
      tile.classList.add('pg-tile--dragging');
    });
    tile.addEventListener('dragend', () => {
      tile.classList.remove('pg-tile--dragging');
      draggedFromPalette = null;
    });
  }

  // ── Drag and Drop: Cell → Cell ──
  function attachCellDragHandlers(cellEl, key) {
    cellEl.addEventListener('dragstart', (e) => {
      // Only start drag if user grabbed the header, not a button inside the body
      if (e.target.closest('.pg-cell-body') || e.target.closest('button')) {
        e.preventDefault();
        return;
      }
      draggedFromCell = key;
      draggedFromPalette = null;
      e.dataTransfer.effectAllowed = 'move';
      e.dataTransfer.setData('text/plain', 'cell:' + key);
      cellEl.classList.add('pg-cell--dragging');
    });
    cellEl.addEventListener('dragend', () => {
      cellEl.classList.remove('pg-cell--dragging');
      draggedFromCell = null;
    });
  }

  // ── Drag and Drop: Target cells ──
  function attachDropHandlers(cellEl, row, col) {
    cellEl.addEventListener('dragover', (e) => {
      e.preventDefault();
      e.dataTransfer.dropEffect = 'move';
      cellEl.classList.add('pg-cell--dragover');
    });
    cellEl.addEventListener('dragleave', () => {
      cellEl.classList.remove('pg-cell--dragover');
    });
    cellEl.addEventListener('drop', (e) => {
      e.preventDefault();
      cellEl.classList.remove('pg-cell--dragover');
      if (draggedFromPalette) {
        placePlugin(draggedFromPalette, row, col, 1, 1);
      } else if (draggedFromCell) {
        const src = grid[draggedFromCell];
        if (src && (src.row !== row || src.col !== col)) {
          // Read everything we need into locals BEFORE deleting (avoids the
          // self-overwrite bug where the displaced cell gets wiped by the next delete)
          const occKey = isCellOccupied(row, col, draggedFromCell);
          const occCopy = occKey ? { ...grid[occKey] } : null;
          const srcCopy = { ...src };

          // Now delete BOTH old keys
          delete grid[draggedFromCell];
          if (occKey) delete grid[occKey];

          // Place the dragged plugin at the new position
          grid[cellKey(row, col)] = {
            ...srcCopy, row, col,
            colSpan: Math.min(srcCopy.colSpan, GRID_COLS - col),
            rowSpan: Math.min(srcCopy.rowSpan, GRID_ROWS - row),
          };
          // Place the displaced plugin (if any) at the source's old position
          if (occCopy) {
            grid[cellKey(srcCopy.row, srcCopy.col)] = {
              ...occCopy, row: srcCopy.row, col: srcCopy.col,
              colSpan: Math.min(occCopy.colSpan, GRID_COLS - srcCopy.col),
              rowSpan: Math.min(occCopy.rowSpan, GRID_ROWS - srcCopy.row),
            };
          }
          markDirty();
          saveAutoBackup();
        }
      }
      buildAll();
    });
  }

  // ── Resize ──
  function attachResizeHandlers(handle, key) {
    handle.addEventListener('mousedown', (e) => {
      e.preventDefault();
      e.stopPropagation();
      const cell = grid[key];
      if (!cell) return;
      const cellEl = handle.parentElement;
      if (!cellEl) return;
      resizingCell = key;
      const startX = e.clientX, startY = e.clientY;
      const startColSpan = cell.colSpan, startRowSpan = cell.rowSpan;
      // Compute cell size based on grid width
      const gridRect = elGrid.getBoundingClientRect();
      const cellW = gridRect.width / GRID_COLS;
      const cellH = (gridRect.height - 6 * (GRID_ROWS - 1)) / GRID_ROWS;
      let lastCols = startColSpan, lastRows = startRowSpan;

      function onMove(ev) {
        const dx = ev.clientX - startX;
        const dy = ev.clientY - startY;
        const newColSpan = Math.max(1, Math.min(GRID_COLS - cell.col,
                                                Math.round(startColSpan + dx / cellW)));
        const newRowSpan = Math.max(1, Math.min(GRID_ROWS - cell.row,
                                                Math.round(startRowSpan + dy / cellH)));
        if (newColSpan !== lastCols || newRowSpan !== lastRows) {
          // Live preview: just update the cell's grid CSS — no rebuild during drag
          cellEl.style.gridColumn = (cell.col + 1) + ' / span ' + newColSpan;
          cellEl.style.gridRow = (cell.row + 1) + ' / span ' + newRowSpan;
          lastCols = newColSpan;
          lastRows = newRowSpan;
        }
      }
      function onUp() {
        document.removeEventListener('mousemove', onMove);
        document.removeEventListener('mouseup', onUp);
        resizingCell = null;
        // Commit final size — single resizeCell call + single rebuild on release
        if (lastCols !== startColSpan || lastRows !== startRowSpan) {
          resizeCell(key, lastCols, lastRows);
          buildAll();
        }
      }
      document.addEventListener('mousemove', onMove);
      document.addEventListener('mouseup', onUp);
    });
  }

  // ── Public actions (exposed to inline onclick) ──
  window._pgRemoveCell = function(key) {
    const cell = grid[key];
    if (!cell) return;
    // Remove from grid (also disables server-side)
    removePluginFromGrid(cell.pluginId);
    buildAll();
  };

  window._pgToggleEnabled = function(pluginId) {
    const enabling = pluginEnabled[pluginId] === false;
    pluginEnabled[pluginId] = enabling;
    setServerPluginEnabled(pluginId, enabling);
    markDirty();
    saveAutoBackup();
    buildAll();
  };

  window.clearGrid = function() {
    if (Object.keys(grid).length === 0) return;
    if (!confirm('Remove all plugins from the grid? (They will be disabled.)')) return;
    Object.values(grid).forEach(cell => {
      setServerPluginEnabled(cell.pluginId, false);
      pluginEnabled[cell.pluginId] = false;
    });
    grid = {};
    markDirty();
    saveAutoBackup();
    buildAll();
  };

  window.togglePalette = function() {
    if (!elPalette) return;
    elPalette.classList.toggle('pg-palette--hidden');
  };

  // ── Profile management ──
  window.loadProfile = async function(name) {
    if (!profiles[name]) return;
    if (dirty && currentProfile !== name) {
      if (!confirm('Current layout has unsaved changes. Load "' + name + '" anyway? Changes will be lost.')) {
        // Reset selector
        if (elProfileSel) elProfileSel.value = currentProfile;
        return;
      }
    }
    currentProfile = name;
    localStorage.setItem('lt_current_profile', name);
    await loadCurrentProfile();
  };

  window.saveCurrentProfile = async function() {
    const snap = await captureServerConfig();
    // Persist current view's grid back to its slot before saving
    const p = migrateProfile(profiles[currentProfile] || {});
    p.grids[activeDisplay] = JSON.parse(JSON.stringify(grid));
    p.plugin_enabled = JSON.parse(JSON.stringify(pluginEnabled));
    p.display_config = snap;
    profiles[currentProfile] = p;
    localStorage.setItem('lt_profiles', JSON.stringify(profiles));
    localStorage.setItem('lt_current_profile', currentProfile);
    clearDirty();
    flashButton(document.querySelector('.pg-btn-p'), 'Saved ✓');
  };

  window.saveAsNewProfile = async function() {
    const name = prompt('Profile name:', '');
    if (!name) return;
    const trimmed = name.trim();
    if (!trimmed) return;
    if (profiles[trimmed] && !confirm('Profile "' + trimmed + '" exists. Overwrite?')) return;
    const snap = await captureServerConfig();
    // Build new profile preserving both display grids from current
    const cur = migrateProfile(profiles[currentProfile] || {});
    const newGrids = {
      main: activeDisplay === 'main' ? JSON.parse(JSON.stringify(grid)) : (cur.grids.main || {}),
      extended: activeDisplay === 'extended' ? JSON.parse(JSON.stringify(grid)) : (cur.grids.extended || {}),
    };
    profiles[trimmed] = {
      grids: newGrids,
      plugin_enabled: JSON.parse(JSON.stringify(pluginEnabled)),
      display_config: snap,
    };
    currentProfile = trimmed;
    localStorage.setItem('lt_profiles', JSON.stringify(profiles));
    localStorage.setItem('lt_current_profile', currentProfile);
    clearDirty();
    buildProfileSelector();
  };

  window.deleteCurrentProfile = async function() {
    const names = Object.keys(profiles);
    if (names.length <= 1) {
      alert('Cannot delete the last profile.');
      return;
    }
    if (!confirm('Delete profile "' + currentProfile + '"? This cannot be undone.')) return;
    delete profiles[currentProfile];
    // Switch to any remaining profile
    currentProfile = Object.keys(profiles).sort()[0];
    localStorage.setItem('lt_profiles', JSON.stringify(profiles));
    localStorage.setItem('lt_current_profile', currentProfile);
    await loadCurrentProfile();
  };

  function flashButton(btn, text) {
    if (!btn) return;
    const orig = btn.textContent;
    btn.textContent = text;
    setTimeout(() => { btn.textContent = orig; }, 1200);
  }

  // ── Helpers ──
  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  // ── Init ──
  window.addEventListener('load', async () => {
    elGrid = document.getElementById('pluginGrid');
    elPalette = document.getElementById('pgPalette');
    elPaletteItems = document.getElementById('pgPaletteItems');
    elProfileSel = document.getElementById('pgProfileSel');
    elUnsavedDot = document.getElementById('pgUnsavedDot');
    elDisplayTab = document.getElementById('pgDisplayTab');

    // Set up the virtual live_captions tile + mirror
    ensureLiveCaptionsPanel();
    wireLiveCaptionMirror();
    // Fetch translations so we can build the synthetic tiles for them
    await refreshTranslations();

    // Load initial profile state (applies grid + plugin enables + display config)
    await loadCurrentProfile();
    // Auto-place any newly discovered plugins into the grid so their
    // enable/disable toggle is visible without manual drag-and-drop
    autoPlaceNewPlugins();
    buildAll();

    // Rebuild the grid/palette when plugins arrive (handles late registration)
    setTimeout(buildAll, 500);
  });

  // Expose for external rebuild triggers (e.g. config_update WS handler)
  window.buildPluginGrid = buildAll;
  window.refreshPluginPalette = async function() {
    await refreshTranslations();
    buildAll();
  };

  // ── API for Grid Layout Editor ──
  window._pgGetGridState = function() {
    const result = {};
    for (const [key, cell] of Object.entries(grid)) {
      const panels = getPluginPanels();
      const panel = panels.find(p => p.id === cell.pluginId);
      result[key] = {
        pluginId: cell.pluginId,
        name: panel ? panel.title : cell.pluginId,
        row: cell.row, col: cell.col,
        rowSpan: cell.rowSpan || 1,
        colSpan: cell.colSpan || 1
      };
    }
    return result;
  };

  window._pgGetAvailablePlugins = function() {
    return getPluginPanels().map(p => ({ id: p.id, name: p.title }));
  };

  window._pgPlacePlugin = function(pluginId, row, col, colSpan, rowSpan) {
    placePlugin(pluginId, row, col, colSpan || 1, rowSpan || 1);
    setServerPluginEnabled(pluginId, true);
    pluginEnabled[pluginId] = true;
    buildAll();
  };

  window._pgMovePlugin = function(fromKey, toKey) {
    const cell = grid[fromKey];
    if (!cell) return;
    const [r, c] = toKey.split('-').map(Number);
    const pluginId = cell.pluginId;
    const cs = cell.colSpan || 1, rs = cell.rowSpan || 1;
    delete grid[fromKey];
    placePlugin(pluginId, r, c, cs, rs);
    buildAll();
  };

  window._pgRemovePlugin = function(key) {
    const cell = grid[key];
    if (!cell) return;
    removePluginFromGrid(cell.pluginId);
    buildAll();
  };

  window._pgResizePlugin = function(key, colSpan, rowSpan) {
    resizeCell(key, colSpan, rowSpan);
    buildAll();
  };

  window._pgSetGridState = function(layout) {
    // Clear current grid
    for (const key of Object.keys(grid)) delete grid[key];
    // Apply new layout
    for (const [key, info] of Object.entries(layout)) {
      placePlugin(info.pluginId, info.row, info.col, info.colSpan || 1, info.rowSpan || 1);
      setServerPluginEnabled(info.pluginId, true);
      pluginEnabled[info.pluginId] = true;
    }
    buildAll();
  };
})();
