// ── Plugin Store Modal ──
async function openPluginStore() {
  // Remove any existing overlay
  closePluginStore();

  let registry = [];
  let installed = [];

  try {
    const [regResp, instResp] = await Promise.all([
      fetch('/api/plugins/registry'),
      fetch('/api/plugins')
    ]);
    if (regResp.ok) { const d = await regResp.json(); registry = d.plugins || d; }
    if (instResp.ok) installed = await instResp.json();
  } catch (e) {
    console.warn('Plugin store fetch failed:', e);
  }

  const installedMap = {};
  (Array.isArray(installed) ? installed : []).forEach(p => {
    installedMap[p.id] = p;
  });

  const availableCount = registry.length;
  const installedCount = Object.keys(installedMap).length;

  // Build overlay
  const overlay = document.createElement('div');
  overlay.className = 'ps-overlay';
  overlay.id = 'psOverlay';
  overlay.addEventListener('click', (e) => { if (e.target === overlay) closePluginStore(); });

  const dialog = document.createElement('div');
  dialog.className = 'ps-dialog';

  // Header
  const header = document.createElement('div');
  header.className = 'ps-header';
  header.innerHTML =
    '<div>' +
      '<div class="ps-title">Plugin Store</div>' +
      '<div class="ps-subtitle">' + availableCount + ' available &middot; ' + installedCount + ' installed</div>' +
    '</div>' +
    '<div style="display:flex;align-items:center;gap:8px">' +
      '<button class="ps-refresh" onclick="openPluginStore()">&#x21bb; Refresh</button>' +
      '<button class="ps-close" onclick="closePluginStore()">&times;</button>' +
    '</div>';
  dialog.appendChild(header);

  // Body
  const body = document.createElement('div');
  body.className = 'ps-body';

  if (registry.length === 0) {
    body.innerHTML = '<div style="text-align:center;color:rgba(255,255,255,0.4);padding:32px 0;font-size:12px">No plugins available in the registry.<br>Check your connection or try refreshing.</div>';
  } else {
    registry.forEach(plugin => {
      const inst = installedMap[plugin.id];
      const isInstalled = plugin.installed || !!inst;
      const instVer = plugin.installed_version || (inst && inst.version);
      const hasUpdate = isInstalled && plugin.version && instVer && plugin.version !== instVer;

      const card = document.createElement('div');
      card.className = 'ps-card' + (isInstalled ? ' ps-card--installed' : '');

      // Badges
      let badges = '';
      if (isInstalled) {
        badges += '<span class="ps-badge ps-badge--installed">INSTALLED</span>';
      }
      if (hasUpdate) {
        badges += '<span class="ps-badge ps-badge--update">UPDATE v' + escPs(plugin.version) + '</span>';
      }
      if (plugin.version) {
        badges += '<span class="ps-badge ps-badge--version">v' + escPs(isInstalled ? (instVer || plugin.version) : plugin.version) + '</span>';
      }

      // Compatibility badge
      let compatHtml = '';
      if (plugin.compatibility) {
        const cls = plugin.compatibility === 'gpu_only' ? 'ps-badge--compat-gpu' : 'ps-badge--compat-both';
        const label = plugin.compatibility === 'gpu_only' ? 'GPU only' : 'CPU+GPU';
        compatHtml = '<span class="' + cls + '">' + escPs(label) + '</span>';
      }

      // Meta row
      let metaParts = [];
      if (plugin.author) metaParts.push(escPs(plugin.author));
      if (plugin.min_app_version) metaParts.push('Requires v' + escPs(plugin.min_app_version) + '+');
      if (compatHtml) metaParts.push(compatHtml);
      if (plugin.download_size) metaParts.push(escPs(plugin.download_size));

      // Action button
      let actionBtn = '';
      if (hasUpdate) {
        actionBtn = '<button class="ps-btn ps-btn--update" onclick="updatePlugin(\'' + escPs(plugin.id) + '\',this)">Update</button>';
      } else if (isInstalled) {
        actionBtn = '<button class="ps-btn ps-btn--uninstall" onclick="uninstallPlugin(\'' + escPs(plugin.id) + '\',this)">Uninstall</button>';
      } else {
        actionBtn = '<button class="ps-btn ps-btn--install" onclick="installPlugin(\'' + escPs(plugin.id) + '\',this)">Install</button>';
      }

      card.innerHTML =
        '<div class="ps-card-info">' +
          '<div class="ps-card-name">' + escPs(plugin.name || plugin.id) + ' ' + badges + '</div>' +
          '<div class="ps-card-desc">' + escPs(plugin.description || '') + '</div>' +
          '<div class="ps-card-meta">' + metaParts.join(' &middot; ') + '</div>' +
        '</div>' +
        actionBtn;

      body.appendChild(card);
    });
  }

  dialog.appendChild(body);
  overlay.appendChild(dialog);
  document.body.appendChild(overlay);
}

async function installPlugin(id, btn) {
  if (btn) { btn.disabled = true; btn.textContent = 'Installing...'; }
  try {
    const resp = await fetch('/api/plugins/install/' + encodeURIComponent(id), { method: 'POST' });
    if (!resp.ok) {
      const err = await resp.text();
      alert('Install failed: ' + err);
    }
  } catch (e) {
    alert('Install failed: ' + e.message);
  }
  openPluginStore();
}

async function updatePlugin(id, btn) {
  if (btn) { btn.disabled = true; btn.textContent = 'Updating...'; }
  try {
    const resp = await fetch('/api/plugins/update/' + encodeURIComponent(id), { method: 'POST' });
    if (!resp.ok) {
      const err = await resp.text();
      alert('Update failed: ' + err);
    }
  } catch (e) {
    alert('Update failed: ' + e.message);
  }
  openPluginStore();
}

async function uninstallPlugin(id, btn) {
  if (!confirm('Uninstall this plugin? Its data may be removed.')) return;
  if (btn) { btn.disabled = true; btn.textContent = 'Removing...'; }
  try {
    const resp = await fetch('/api/plugins/uninstall/' + encodeURIComponent(id), { method: 'DELETE' });
    if (!resp.ok) {
      const err = await resp.text();
      alert('Uninstall failed: ' + err);
    }
  } catch (e) {
    alert('Uninstall failed: ' + e.message);
  }
  openPluginStore();
}

function closePluginStore() {
  const el = document.getElementById('psOverlay');
  if (el) el.remove();
}

function escPs(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

// ── Grid Layout Editor ──
function openGridLayoutEditor() {
  let overlay = document.getElementById('gleOverlay');
  if (!overlay) {
    overlay = document.createElement('div');
    overlay.className = 'gle-overlay';
    overlay.id = 'gleOverlay';
    overlay.onclick = e => { if (e.target === overlay) closeGridLayoutEditor(); };
    document.body.appendChild(overlay);
  }
  gleRefreshContent();
}

function gleRefreshContent() {
  const overlay = document.getElementById('gleOverlay');
  if (!overlay) return;

  const currentGrid = (typeof window._pgGetGridState === 'function') ? window._pgGetGridState() : {};
  const availPlugins = (typeof window._pgGetAvailablePlugins === 'function') ? window._pgGetAvailablePlugins() : [];

  const occupied = {};
  const spanned = {};
  for (const [key, info] of Object.entries(currentGrid)) {
    const rs = info.rowSpan || 1, cs = info.colSpan || 1;
    occupied[key] = { pluginId: info.pluginId, name: info.name || info.pluginId, rowSpan: rs, colSpan: cs };
    for (let dr = 0; dr < rs; dr++) {
      for (let dc = 0; dc < cs; dc++) {
        if (dr === 0 && dc === 0) continue;
        spanned[(info.row + dr) + '-' + (info.col + dc)] = key;
      }
    }
  }

  const placedIds = new Set(Object.values(occupied).map(o => o.pluginId));

  let html = '<div class="gle-modal"><div class="gle-title"><span>Adjust Grid Layout <small style="opacity:.4;font-weight:400">(p9)</small></span><button onclick="closeGridLayoutEditor()">&times;</button></div>';
  html += '<div class="gle-body">';

  html += '<div style="flex:1"><div class="gle-grid" id="gleGrid">';
  for (let r = 0; r < 4; r++) {
    for (let c = 0; c < 4; c++) {
      const key = r + '-' + c;
      if (spanned[key]) continue;
      const occ = occupied[key];
      if (occ) {
        const rs = occ.rowSpan || 1, cs = occ.colSpan || 1;
        const style = (rs > 1 || cs > 1)
          ? ` style="grid-row:span ${rs};grid-column:span ${cs}"`
          : '';
        html += `<div class="gle-cell gle-cell--occupied" data-key="${key}" data-plugin-id="${esc(occ.pluginId)}"${style}>`
          + `<span>${esc(occ.name)}</span>`
          + `<button class="gle-rm" onclick="gleRemove('${key}')" title="Remove">&times;</button>`
          + `<div class="gle-resize-handle"></div>`
          + `</div>`;
      } else {
        html += `<div class="gle-cell" data-key="${key}"><span>+</span></div>`;
      }
    }
  }
  html += '</div></div>';

  html += '<div class="gle-avail"><div class="gle-avail-title">Available Plugins</div>';
  if (availPlugins.length === 0) {
    html += '<div style="font-size:10px;color:rgba(255,255,255,.2);padding:8px;font-style:italic">No plugins found</div>';
  } else {
    for (const p of availPlugins) {
      const placed = placedIds.has(p.id);
      const cls = placed ? 'gle-avail-item gle-avail-item--placed' : 'gle-avail-item';
      const badge = placed ? ' <span style="float:right;font-size:9px;opacity:.6">&#10003;</span>' : '';
      html += `<div class="${cls}" ${placed?'':'draggable="true" '}data-plugin-id="${esc(p.id)}" data-plugin-name="${esc(p.name)}">${esc(p.name)}${badge}</div>`;
    }
  }
  html += '</div></div>';

  // Footer controls
  const curFooterText = document.getElementById('iFooterText')?.value || '';
  const curFooterPos = document.getElementById('iFooterPos')?.value || '50';
  const curFooterImg = document.getElementById('footerPrevImg')?.src || '';
  const hasFooterImg = curFooterImg && !curFooterImg.endsWith('/') && document.getElementById('footerPrev')?.style.display !== 'none';
  html += '<div class="gle-section"><div class="gle-section-title">Footer</div>';
  html += '<div style="display:flex;gap:6px;align-items:center">';
  html += `<input type="text" id="gleFooterText" value="${escPs(curFooterText)}" placeholder="Footer text (e.g. company name)" oninput="gleUpdateFooterText()" style="flex:1;padding:4px 8px;background:rgba(255,255,255,.08);border:1px solid rgba(255,255,255,.1);border-radius:4px;color:#fff;font-size:11px">`;
  html += '</div>';
  // Footer image
  html += '<div style="display:flex;gap:6px;align-items:center;margin-top:6px">';
  html += '<label style="font-size:10px;color:rgba(255,255,255,.4);white-space:nowrap">Logo</label>';
  html += '<input type="file" id="gleFooterImg" accept="image/*" onchange="gleUploadFooterImg(this)" style="font-size:10px;flex:1;color:rgba(255,255,255,.6)">';
  html += '</div>';
  if (hasFooterImg) {
    html += '<div class="gle-footer-img-row" id="gleFooterImgPreview">';
    html += `<img src="${escPs(curFooterImg)}">`;
    html += '<button class="gle-rm-img" onclick="gleRemoveFooterImg()">Remove</button>';
    html += '</div>';
  }
  // Position presets
  html += '<div style="display:flex;gap:4px;margin-top:6px">';
  html += '<button class="gle-pos-btn" onclick="gleSetFooterPos(0)">Left</button>';
  html += '<button class="gle-pos-btn" onclick="gleSetFooterPos(25)">C-Left</button>';
  html += '<button class="gle-pos-btn" onclick="gleSetFooterPos(50)">Center</button>';
  html += '<button class="gle-pos-btn" onclick="gleSetFooterPos(75)">C-Right</button>';
  html += '<button class="gle-pos-btn" onclick="gleSetFooterPos(100)">Right</button>';
  html += '</div>';
  html += `<input type="range" id="gleFooterSlider" min="0" max="100" value="${escPs(curFooterPos)}" oninput="gleSetFooterPos(parseInt(this.value))" style="margin-top:6px;width:100%">`;
  html += '</div>';

  html += '<div class="gle-footer">';
  html += '<button class="btn btn-g" onclick="closeGridLayoutEditor()">Close</button>';
  html += '<button class="btn btn-p" onclick="gleApply()">Apply Layout</button>';
  html += '</div></div>';

  overlay.innerHTML = html;
  gleInitDragDrop();
}

function gleInitDragDrop() {
  const grid = document.getElementById('gleGrid');
  if (!grid) return;

  document.querySelectorAll('.gle-avail-item').forEach(item => {
    item.addEventListener('dragstart', e => {
      e.dataTransfer.setData('text/plain', JSON.stringify({
        type: 'new', pluginId: item.dataset.pluginId, name: item.dataset.pluginName
      }));
    });
  });

  // Pointer-based drag-to-reorder occupied cells
  grid.querySelectorAll('.gle-cell--occupied').forEach(cell => {
    cell.addEventListener('pointerdown', e => {
      if (e.target.closest('.gle-resize-handle') || e.target.closest('.gle-rm')) return;
      if (e.button !== 0) return;
      e.preventDefault();
      const fromKey = cell.dataset.key;
      if (!fromKey) return;
      const startX = e.clientX, startY = e.clientY;
      let dragging = false;

      function onMove(ev) {
        const dx = ev.clientX - startX, dy = ev.clientY - startY;
        if (!dragging && Math.abs(dx) + Math.abs(dy) > 5) {
          dragging = true;
          cell.style.opacity = '0.4';
          cell.style.pointerEvents = 'none';
        }
        if (dragging) {
          grid.querySelectorAll('.gle-cell--dragover').forEach(c => c.classList.remove('gle-cell--dragover'));
          const el = document.elementFromPoint(ev.clientX, ev.clientY);
          const target = el?.closest('.gle-cell');
          if (target && target !== cell && target.dataset.key) {
            target.classList.add('gle-cell--dragover');
          }
        }
      }
      function onUp(ev) {
        document.removeEventListener('pointermove', onMove);
        document.removeEventListener('pointerup', onUp);
        cell.style.opacity = '';
        cell.style.pointerEvents = '';
        grid.querySelectorAll('.gle-cell--dragover').forEach(c => c.classList.remove('gle-cell--dragover'));
        if (dragging) {
          const el = document.elementFromPoint(ev.clientX, ev.clientY);
          const target = el?.closest('.gle-cell');
          if (target && target !== cell && target.dataset.key) {
            gleMovePlugin(fromKey, target.dataset.key);
          }
        }
      }
      document.addEventListener('pointermove', onMove);
      document.addEventListener('pointerup', onUp);
    });
  });

  // Drop handlers on ALL cells — accepts sidebar item drops
  grid.querySelectorAll('.gle-cell').forEach(cell => {
    cell.addEventListener('dragover', e => { e.preventDefault(); cell.classList.add('gle-cell--dragover'); });
    cell.addEventListener('dragleave', () => cell.classList.remove('gle-cell--dragover'));
    cell.addEventListener('drop', e => {
      e.preventDefault();
      cell.classList.remove('gle-cell--dragover');
      try {
        const data = JSON.parse(e.dataTransfer.getData('text/plain'));
        const targetKey = cell.dataset.key;
        if (!targetKey) return;
        if (data.type === 'new' && !cell.classList.contains('gle-cell--occupied')) {
          glePlacePlugin(targetKey, data.pluginId, data.name);
        }
      } catch(ex) { console.error('GLE drop error:', ex); }
    });

    if (!cell.classList.contains('gle-cell--occupied')) {
      cell.addEventListener('click', () => {
        const items = document.querySelectorAll('.gle-avail-item:not(.gle-avail-item--placed)');
        if (items.length > 0) {
          const first = items[0];
          glePlacePlugin(cell.dataset.key, first.dataset.pluginId, first.dataset.pluginName);
        }
      });
    }
  });

  // Drag-to-resize on occupied cells — pure pointer events, no HTML5 drag
  document.querySelectorAll('.gle-resize-handle').forEach(handle => {
    handle.addEventListener('pointerdown', e => {
      e.preventDefault();
      e.stopPropagation();
      handle.setPointerCapture(e.pointerId);
      const cellEl = handle.closest('.gle-cell');
      const key = cellEl?.dataset.key;
      if (!key) return;
      const [r, c] = key.split('-').map(Number);
      const gridEl = document.getElementById('gleGrid');
      const gridRect = gridEl.getBoundingClientRect();
      const cellW = gridRect.width / 4;
      const cellH = gridRect.height / 4;
      const startX = e.clientX, startY = e.clientY;
      const startCS = parseInt(cellEl.style.gridColumn?.match(/span (\d+)/)?.[1] || '1');
      const startRS = parseInt(cellEl.style.gridRow?.match(/span (\d+)/)?.[1] || '1');
      let lastCS = startCS, lastRS = startRS;

      function onMove(ev) {
        const dx = ev.clientX - startX;
        const dy = ev.clientY - startY;
        const newCS = Math.max(1, Math.min(4 - c, Math.round(startCS + dx / cellW)));
        const newRS = Math.max(1, Math.min(4 - r, Math.round(startRS + dy / cellH)));
        if (newCS !== lastCS || newRS !== lastRS) {
          cellEl.style.gridColumn = `span ${newCS}`;
          cellEl.style.gridRow = `span ${newRS}`;
          lastCS = newCS;
          lastRS = newRS;
        }
      }
      function onUp() {
        handle.removeEventListener('pointermove', onMove);
        handle.removeEventListener('pointerup', onUp);
        if (lastCS !== startCS || lastRS !== startRS) {
          if (typeof window._pgResizePlugin === 'function') {
            window._pgResizePlugin(key, lastCS, lastRS);
          }
          gleRefreshContent();
        }
      }
      handle.addEventListener('pointermove', onMove);
      handle.addEventListener('pointerup', onUp);
    });
  });
}

function glePlacePlugin(key, pluginId, name) {
  if (typeof window._pgPlacePlugin === 'function') {
    const [r, c] = key.split('-').map(Number);
    window._pgPlacePlugin(pluginId, r, c);
  }
  gleRefreshContent();
}

function gleMovePlugin(fromKey, toKey) {
  if (typeof window._pgMovePlugin === 'function') {
    window._pgMovePlugin(fromKey, toKey);
  }
  gleRefreshContent();
}

function gleRemove(key) {
  if (typeof window._pgRemovePlugin === 'function') {
    window._pgRemovePlugin(key);
  }
  gleRefreshContent();
}

function gleSetFooterPos(pct) {
  const slider = document.getElementById('gleFooterSlider');
  if (slider) slider.value = pct;
  if (typeof setFooterPos === 'function') setFooterPos(pct);
}

function gleUpdateFooterText() {
  const gleInput = document.getElementById('gleFooterText');
  const mainInput = document.getElementById('iFooterText');
  if (gleInput && mainInput) {
    mainInput.value = gleInput.value;
    if (typeof debouncedSaveAll === 'function') debouncedSaveAll();
  }
}

function gleUploadFooterImg(inp) {
  if (!inp.files || !inp.files[0]) return;
  const fd = new FormData();
  fd.append('file', inp.files[0]);
  fetch('/api/upload-footer', {method:'POST', body:fd}).then(r=>r.json()).then(r=>{
    if (r.filename) {
      const u = '/uploads/' + r.filename + '?t=' + Date.now();
      document.getElementById('footerPrevImg').src = u;
      document.getElementById('footerPrev').style.display = 'block';
      document.getElementById('pFooterImg').src = u;
      document.getElementById('pFooter').style.display = 'block';
      gleRefreshContent();
    }
  });
}

function gleRemoveFooterImg() {
  fetch('/api/remove-footer', {method:'POST'}).then(() => {
    document.getElementById('footerPrev').style.display = 'none';
    document.getElementById('pFooter').style.display = 'none';
    gleRefreshContent();
  });
}

function gleApply() {
  const grid = document.getElementById('gleGrid');
  if (!grid) { closeGridLayoutEditor(); return; }

  const cells = grid.querySelectorAll('.gle-cell--occupied');
  const layout = {};
  cells.forEach(cell => {
    const key = cell.dataset.key;
    const pluginId = cell.dataset.pluginId;
    if (!key || !pluginId) return;
    const [r, c] = key.split('-').map(Number);
    const rs = parseInt(cell.style.gridRow?.match(/span (\d+)/)?.[1] || '1');
    const cs = parseInt(cell.style.gridColumn?.match(/span (\d+)/)?.[1] || '1');
    layout[key] = { pluginId, row: r, col: c, rowSpan: rs, colSpan: cs };
  });

  if (typeof window._pgSetGridState === 'function') {
    window._pgSetGridState(layout);
  }
  closeGridLayoutEditor();
}

function closeGridLayoutEditor() {
  const el = document.getElementById('gleOverlay');
  if (el) el.remove();
}
