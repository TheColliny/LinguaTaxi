// ════════════════════════════════════════════════════════════════════════════
// AUDIENCE DISPLAY GRID
// Reads the operator-pushed grid layout for THIS display (main or extended)
// and renders each cell. The "live_captions" plugin gets the caption rendering;
// translation slots map to translation plugins; other plugins get their panel.
// ════════════════════════════════════════════════════════════════════════════

const DISPLAY_TARGET = (document.querySelector('meta[name="display-target"]')?.content || 'main').trim();
const TRANSLATION_PREFIX = 'translation_'; // prefix for synthetic translation slot tiles

let ws = null, recon = 0, cfg = {};
let currentGrid = {};       // cellKey -> { pluginId, row, col, colSpan, rowSpan }
let captionState = { lines: [], lastSpeaker: null }; // for live_captions tile
let translationStates = {}; // slotIndex -> { lines: [], lastSpeaker: null }
let _i18n = {}, _i18nEn = {};

const elGrid = document.getElementById('audGrid');

// ── i18n ──
function t(key, vars) {
  let s = _i18n[key] || _i18nEn[key] || key;
  if (vars) Object.entries(vars).forEach(([k,v]) => { s = s.replaceAll('{'+k+'}', v); });
  return s;
}
async function loadTranslations(lang) {
  try {
    if (!Object.keys(_i18nEn).length) {
      const r = await fetch('/api/locales/en');
      if (r.ok) _i18nEn = await r.json();
    }
    const r2 = await fetch('/api/locales/' + lang.toLowerCase());
    if (r2.ok) _i18n = await r2.json();
    else _i18n = { ..._i18nEn };
  } catch (e) { _i18n = { ..._i18nEn }; }
  document.querySelectorAll('[data-i18n]').forEach(el => { el.textContent = t(el.getAttribute('data-i18n')); });
  const L = String(lang || '').toUpperCase();
  document.documentElement.lang = L.toLowerCase();
  document.documentElement.dir = L === 'AR' ? 'rtl' : 'ltr';
}

// ── Grid rendering ──
function getPluginPanels() {
  // Panels may be in #pluginPanelsHidden OR currently mounted in a grid cell;
  // search the whole document so we find them either way.
  return Array.from(document.querySelectorAll('.plugin-panel')).map(el => ({
    id: el.dataset.pluginId,
    el: el,
    title: el.querySelector('.plugin-title')?.textContent || el.dataset.pluginId,
  }));
}

function buildGrid() {
  if (!elGrid) return;
  // Move any plugin panels currently mounted in cells back to hidden container
  // before tearing down the grid. This preserves DOM identity (IDs, listeners).
  const hidden = document.getElementById('pluginPanelsHidden');
  if (hidden) {
    elGrid.querySelectorAll('.plugin-panel').forEach(panel => hidden.appendChild(panel));
  }
  elGrid.innerHTML = '';
  // Reset translation states for current grid (we'll repopulate as cells are built)
  const newTranslationStates = {};

  // Clamp + filter cells: silently skip any with out-of-bounds row/col
  const GCOLS = 10, GROWS = 10;
  const cells = Object.values(currentGrid || {}).filter(c =>
    c && typeof c.row === 'number' && typeof c.col === 'number' &&
    c.row >= 0 && c.row < GROWS && c.col >= 0 && c.col < GCOLS
  );
  if (cells.length === 0) {
    elGrid.innerHTML = '<div class="empty-grid" style="grid-column:span ' + GCOLS + ';grid-row:span ' + GROWS + '">' +
      'No layout configured for this display.<br>' +
      'Open the operator panel and drag plugins into the grid.</div>';
    return;
  }

  const pluginMap = {};
  getPluginPanels().forEach(p => { pluginMap[p.id] = p; });

  cells.forEach(cell => {
    const pid = cell.pluginId;
    const colSpan = Math.max(1, Math.min(GCOLS - cell.col, cell.colSpan || 1));
    const rowSpan = Math.max(1, Math.min(GROWS - cell.row, cell.rowSpan || 1));
    const div = document.createElement('div');
    div.className = 'aud-cell';
    div.style.gridColumn = (cell.col + 1) + ' / span ' + colSpan;
    div.style.gridRow = (cell.row + 1) + ' / span ' + rowSpan;
    div.dataset.pluginId = pid;

    if (pid === 'live_captions') {
      div.innerHTML =
        '<div class="aud-cell-title"><span class="dot" id="lc-dot"></span>' +
          '<span>' + esc(cfg.input_lang_name || 'Captions') + '</span></div>' +
        '<div class="aud-cell-body"><div class="lc-scroll" id="lc-scroll"><div class="lc-inner" id="lc-inner">' +
          '<div class="lc-line interim" id="lc-interim" style="display:none"></div>' +
        '</div></div></div>';
      // Replay current caption state
      replayCaptions();
    } else if (pid && pid.startsWith(TRANSLATION_PREFIX)) {
      // Translation slot tile (synthetic)
      const slotIdx = parseInt(pid.substring(TRANSLATION_PREFIX.length), 10);
      const tr = (cfg.translations || [])[slotIdx];
      const label = tr ? (tr.name || tr.lang) : ('Translation ' + (slotIdx + 1));
      const color = tr ? (tr.color || '#FFD54F') : '#FFD54F';
      div.innerHTML =
        '<div class="aud-cell-title"><span class="dot" id="tr-dot-' + slotIdx + '"></span>' +
          '<span>' + esc(label) + '</span></div>' +
        '<div class="aud-cell-body"><div class="tr-scroll" id="tr-scroll-' + slotIdx + '"><div class="tr-inner" id="tr-inner-' + slotIdx + '" style="--slot-color:' + esc(color) + '">' +
          '<div class="tr-line interim" id="tr-interim-' + slotIdx + '" style="display:none;color:' + esc(color) + '"></div>' +
        '</div></div></div>';
      newTranslationStates[slotIdx] = translationStates[slotIdx] || { lines: [], lastSpeaker: null, color };
      newTranslationStates[slotIdx].color = color;
    } else if (pluginMap[pid]) {
      const p = pluginMap[pid];
      const chromeless = pid === 'window_capture';
      if (chromeless) {
        div.className = 'aud-cell aud-cell--chromeless';
        div.innerHTML = '<div class="aud-cell-body plugin-shell" data-plugin-id="' + esc(pid) + '"></div>';
      } else {
        const title = p.title || pid;
        div.innerHTML =
          '<div class="aud-cell-title">' + esc(title) + '</div>' +
          '<div class="aud-cell-body plugin-shell" data-plugin-id="' + esc(pid) + '"></div>';
      }
      const shell = div.querySelector('.plugin-shell');
      const ph = p.el.querySelector('.plugin-header');
      if (ph) ph.style.display = 'none';
      const pt = p.el.querySelector('.plugin-title');
      if (pt) pt.style.display = 'none';
      shell.appendChild(p.el);
    } else {
      // Unknown plugin id — placeholder
      div.innerHTML =
        '<div class="aud-cell-title">' + esc(pid || 'unknown') + '</div>' +
        '<div class="aud-cell-body" style="display:flex;align-items:center;justify-content:center;color:rgba(255,255,255,.25)">' +
          'Plugin not loaded</div>';
    }

    elGrid.appendChild(div);
  });

  translationStates = newTranslationStates;
  // Replay any pending translation lines for the now-visible slots
  Object.entries(translationStates).forEach(([slotIdx, st]) => replayTranslation(parseInt(slotIdx, 10)));
}

// ── Live captions: render captionState into the live_captions cell if visible ──
function liveCaptionsCellPresent() {
  return Object.values(currentGrid).some(c => c.pluginId === 'live_captions');
}

// Build a single caption line DOM with consistent speaker-tag logic.
// Tracks `lastSpeakerRef.value` across calls to suppress repeated tags.
function buildCaptionLine(text, speaker, lineId, color, lastSpeakerRef) {
  const line = document.createElement('div');
  line.className = 'lc-line';
  if (lineId !== undefined) line.dataset.lineId = lineId;
  if (speaker && speaker !== lastSpeakerRef.value) {
    const tag = document.createElement('span');
    tag.className = 'lc-speaker-tag';
    tag.textContent = speaker + ': ';
    tag.style.color = color || 'inherit';
    line.appendChild(tag);
    lastSpeakerRef.value = speaker;
  } else if (!speaker && lastSpeakerRef.value) {
    // No speaker on this line — clear so next line with a speaker shows the tag
    lastSpeakerRef.value = null;
  }
  const txt = document.createTextNode(text);
  line.appendChild(txt);
  line._textNode = txt;
  return line;
}

function replayCaptions() {
  const inner = document.getElementById('lc-inner');
  const interimEl = document.getElementById('lc-interim');
  if (!inner || !interimEl) return;
  // Clear existing rendered finals (keep interim el)
  inner.querySelectorAll('.lc-line:not(#lc-interim)').forEach(el => el.remove());
  const ref = { value: null };
  captionState.lines.forEach(l => {
    inner.insertBefore(buildCaptionLine(l.text, l.speaker, l.lineId, l.color, ref), interimEl);
  });
  captionState.lastSpeaker = ref.value;
  scrollCaptionsToBottom();
}

function scrollCaptionsToBottom() {
  const sb = document.getElementById('lc-scroll');
  if (sb) requestAnimationFrame(() => { sb.scrollTop = sb.scrollHeight; });
}

function appendCaptionFinal(text, speaker, lineId, color) {
  captionState.lines.push({ text, speaker, lineId, color });
  while (captionState.lines.length > 200) captionState.lines.shift();
  // Render immediately if the cell is visible
  const inner = document.getElementById('lc-inner');
  const interimEl = document.getElementById('lc-interim');
  if (!inner || !interimEl) return;
  // Use the same builder as replay for consistent speaker-tag logic
  const ref = { value: captionState.lastSpeaker };
  const line = buildCaptionLine(text, speaker, lineId, color, ref);
  captionState.lastSpeaker = ref.value;
  inner.insertBefore(line, interimEl);
  interimEl.style.display = 'none';
  interimEl.innerHTML = '';
  // Trim DOM
  const finals = inner.querySelectorAll('.lc-line:not(#lc-interim)');
  if (finals.length > 200) finals[0].remove();
  scrollCaptionsToBottom();
}

function setCaptionInterim(text, speaker, color) {
  const interimEl = document.getElementById('lc-interim');
  if (!interimEl) return;
  if (text) {
    interimEl.innerHTML = '';
    if (speaker && speaker !== captionState.lastSpeaker) {
      const tag = document.createElement('span');
      tag.className = 'lc-speaker-tag';
      tag.textContent = speaker + ': ';
      tag.style.color = color || '';
      interimEl.appendChild(tag);
    }
    interimEl.appendChild(document.createTextNode(text));
    interimEl.style.display = 'block';
  } else {
    interimEl.style.display = 'none';
    interimEl.innerHTML = '';
  }
  scrollCaptionsToBottom();
}

function correctCaption(lineId, newText) {
  // Update state
  for (let i = captionState.lines.length - 1; i >= 0; i--) {
    if (String(captionState.lines[i].lineId) === String(lineId)) {
      captionState.lines[i].text = newText;
      break;
    }
  }
  // Update DOM if visible
  const inner = document.getElementById('lc-inner');
  if (!inner) return;
  const lines = inner.querySelectorAll('.lc-line:not(#lc-interim)');
  for (let i = lines.length - 1; i >= 0; i--) {
    if (String(lines[i].dataset.lineId) === String(lineId)) {
      const el = lines[i];
      if (el._textNode) {
        el._textNode.textContent = newText;
      } else {
        const tag = el.querySelector('.lc-speaker-tag');
        el.textContent = '';
        if (tag) el.appendChild(tag);
        const txt = document.createTextNode(newText);
        el.appendChild(txt);
        el._textNode = txt;
      }
      return;
    }
  }
}

function clearCaptions() {
  captionState.lines = [];
  captionState.lastSpeaker = null;
  const inner = document.getElementById('lc-inner');
  const interimEl = document.getElementById('lc-interim');
  if (inner) inner.querySelectorAll('.lc-line:not(#lc-interim)').forEach(l => l.remove());
  if (interimEl) { interimEl.style.display = 'none'; interimEl.innerHTML = ''; }
}

// ── Translation rendering ──
// Shared builder for translation lines — same speaker-tag logic as captions
function buildTranslationLine(text, speaker, lineId, color, baseColor, lastSpeakerRef) {
  const line = document.createElement('div');
  line.className = 'tr-line';
  line.style.color = baseColor || '#FFD54F';
  if (lineId !== undefined) line.dataset.lineId = lineId;
  if (speaker && speaker !== lastSpeakerRef.value) {
    const tag = document.createElement('span');
    tag.className = 'lc-speaker-tag';
    tag.textContent = speaker + ': ';
    tag.style.color = color || baseColor;
    line.appendChild(tag);
    lastSpeakerRef.value = speaker;
  } else if (!speaker && lastSpeakerRef.value) {
    lastSpeakerRef.value = null;
  }
  const txt = document.createTextNode(text);
  line.appendChild(txt);
  line._textNode = txt;
  return line;
}

function replayTranslation(slotIdx) {
  const inner = document.getElementById('tr-inner-' + slotIdx);
  const interimEl = document.getElementById('tr-interim-' + slotIdx);
  if (!inner || !interimEl) return;
  const st = translationStates[slotIdx];
  if (!st) return;
  inner.querySelectorAll('.tr-line:not(.interim)').forEach(el => el.remove());
  const ref = { value: null };
  st.lines.forEach(l => {
    inner.insertBefore(buildTranslationLine(l.text, l.speaker, l.lineId, l.color, st.color, ref), interimEl);
  });
  st.lastSpeaker = ref.value;
  const sb = document.getElementById('tr-scroll-' + slotIdx);
  if (sb) requestAnimationFrame(() => { sb.scrollTop = sb.scrollHeight; });
}

function appendTranslationFinal(slotIdx, text, speaker, lineId, color) {
  if (!translationStates[slotIdx]) translationStates[slotIdx] = { lines: [], lastSpeaker: null, color: color || '#FFD54F' };
  const st = translationStates[slotIdx];
  st.lines.push({ text, speaker, lineId, color });
  while (st.lines.length > 200) st.lines.shift();
  const inner = document.getElementById('tr-inner-' + slotIdx);
  const interimEl = document.getElementById('tr-interim-' + slotIdx);
  if (!inner || !interimEl) return;
  const ref = { value: st.lastSpeaker };
  const line = buildTranslationLine(text, speaker, lineId, color, st.color, ref);
  st.lastSpeaker = ref.value;
  inner.insertBefore(line, interimEl);
  interimEl.style.display = 'none';
  interimEl.innerHTML = '';
  const finals = inner.querySelectorAll('.tr-line:not(.interim)');
  if (finals.length > 200) finals[0].remove();
  const sb = document.getElementById('tr-scroll-' + slotIdx);
  if (sb) requestAnimationFrame(() => { sb.scrollTop = sb.scrollHeight; });
}

function setTranslationInterim(slotIdx, text, speaker, color) {
  const st = translationStates[slotIdx];
  const interimEl = document.getElementById('tr-interim-' + slotIdx);
  if (!interimEl) return;
  if (text) {
    interimEl.innerHTML = '';
    interimEl.style.color = (st && st.color) || color || '#FFD54F';
    if (speaker && st && speaker !== st.lastSpeaker) {
      const tag = document.createElement('span');
      tag.className = 'lc-speaker-tag';
      tag.textContent = speaker + ': ';
      tag.style.color = color || (st && st.color) || '';
      interimEl.appendChild(tag);
    }
    interimEl.appendChild(document.createTextNode(text));
    interimEl.style.display = 'block';
  } else {
    interimEl.style.display = 'none';
    interimEl.innerHTML = '';
  }
}

function correctTranslation(slotIdx, lineId, newText) {
  const st = translationStates[slotIdx];
  if (st) {
    for (let i = st.lines.length - 1; i >= 0; i--) {
      if (String(st.lines[i].lineId) === String(lineId)) {
        st.lines[i].text = newText;
        break;
      }
    }
  }
  const inner = document.getElementById('tr-inner-' + slotIdx);
  if (!inner) return;
  const lines = inner.querySelectorAll('.tr-line:not(.interim)');
  for (let i = lines.length - 1; i >= 0; i--) {
    if (String(lines[i].dataset.lineId) === String(lineId)) {
      const el = lines[i];
      if (el._textNode) el._textNode.textContent = newText;
      return;
    }
  }
}

// ── Style application ──
function applyStyle(c) {
  const r = document.documentElement.style;
  if (c.bg_color) r.setProperty('--bg', c.bg_color);
  if (c.font_size !== undefined) r.setProperty('--fs', c.font_size + 'px');
  if (c.max_lines !== undefined) r.setProperty('--max-lines', c.max_lines);
  if (c.font_css) r.setProperty('--font', c.font_css);
  if (c.caption_color) r.setProperty('--caption-color', c.caption_color);
  if (c.session_title) {
    document.getElementById('title').textContent = c.session_title;
    document.title = c.session_title;
  }
  const fw = document.getElementById('footerWrap'), fi = document.getElementById('footerImg');
  const ft = document.getElementById('footerText');
  const hasImage = !!c.footer_image;
  const hasText = !!(c.footer_text && c.footer_text.trim());
  if (hasImage) {
    fi.src = '/uploads/' + c.footer_image + '?t=' + Date.now();
    fi.style.display = '';
  } else {
    fi.style.display = 'none';
  }
  if (hasText) {
    ft.textContent = c.footer_text;
    ft.style.display = '';
  } else {
    ft.textContent = '';
    ft.style.display = 'none';
  }
  if (hasImage || hasText) { fw.classList.add('vis'); } else { fw.classList.remove('vis'); }
  if (c.footer_position !== undefined) {
    const pct = Math.max(0, Math.min(100, c.footer_position));
    const pad = 2;
    const left = pad + (pct / 100) * (100 - 2 * pad);
    fi.style.left = left + '%';
    ft.style.left = left + '%';
  }
}

// ── Status dot updates (only relevant when caption tile is in grid) ──
function setCaptionDot(state) {
  const dot = document.getElementById('lc-dot');
  if (dot) dot.className = 'dot ' + state;
}
function setTranslationDot(slotIdx, state) {
  const dot = document.getElementById('tr-dot-' + slotIdx);
  if (dot) dot.className = 'dot ' + state;
}

// ── WebSocket message handler ──
function handleMsg(msg) {
  switch (msg.type) {
    case 'status':
      if (msg.state === 'speech') setCaptionDot('live');
      else if (msg.state === 'silence' || msg.state === 'connected') setCaptionDot('conn');
      break;
    case 'captioning_paused':
      setCaptionDot(msg.paused ? 'paused' : 'live');
      break;
    case 'interim':
      setCaptionInterim(msg.text || '', msg.speaker || '', msg.color);
      setCaptionDot('live');
      // Fire to plugins (so plugin tiles in the grid see live data)
      if (window.LinguaTaxi && window.LinguaTaxi.plugins) {
        window.LinguaTaxi.plugins.fire('on_interim', { text: msg.text || '', speaker: msg.speaker || '' });
      }
      break;
    case 'final':
      setCaptionInterim('');
      if (msg.text) appendCaptionFinal(msg.text, msg.speaker || '', msg.line_id, msg.color);
      if (window.LinguaTaxi && window.LinguaTaxi.plugins) {
        window.LinguaTaxi.plugins.fire('on_final', { text: msg.text, speaker: msg.speaker || '', line_id: msg.line_id, detected_lang: msg.detected_lang });
      }
      break;
    case 'interim_translation':
      if (msg.slot !== undefined) {
        setTranslationInterim(msg.slot, msg.translated || '', msg.speaker || '', msg.color);
        setTranslationDot(msg.slot, 'live');
      }
      break;
    case 'final_translation':
      if (msg.slot !== undefined) {
        setTranslationInterim(msg.slot, '');
        if (msg.translated) appendTranslationFinal(msg.slot, msg.translated, msg.speaker || '', msg.line_id, msg.color);
      }
      break;
    case 'correct_line':
      if (msg.line_id !== undefined && msg.text) correctCaption(msg.line_id, msg.text);
      break;
    case 'correct_translation':
      if (msg.line_id !== undefined && msg.slot !== undefined && msg.translated)
        correctTranslation(msg.slot, msg.line_id, msg.translated);
      break;
    case 'speaker_change': break;
    case 'clear_captions':
      clearCaptions();
      Object.keys(translationStates).forEach(k => {
        translationStates[k].lines = [];
        translationStates[k].lastSpeaker = null;
        const inner = document.getElementById('tr-inner-' + k);
        if (inner) inner.querySelectorAll('.tr-line:not(.interim)').forEach(l => l.remove());
      });
      break;
    case 'fact_check_result':
      if (window.LinguaTaxi && window.LinguaTaxi.plugins) {
        window.LinguaTaxi.plugins.fire('on_fact_check_result', msg.result || msg);
      }
      break;
    case 'config_update':
      applyStyle(msg);
      if (msg.ui_language) loadTranslations(msg.ui_language);
      // Any translation-affecting config change → refetch full config + rebuild grid
      // so translation_N tiles reflect new slot count, languages, and colors.
      if (msg.all_translations !== undefined ||
          msg.translations !== undefined ||
          msg.translation_count !== undefined) {
        fetch('/api/config').then(r => r.json()).then(c => { cfg = c; buildGrid(); applyStyle(c); });
      }
      break;
    case 'display_grid_change':
      // Operator updated the grid layout for our display target
      if (msg.display === DISPLAY_TARGET && msg.grid) {
        currentGrid = msg.grid;
        buildGrid();
      }
      break;
    case 'wc_init':
      if (window.LinguaTaxi && window.LinguaTaxi.plugins) {
        window.LinguaTaxi.plugins.fire('on_binary', msg.mime);
      }
      break;
    default: break;
  }
}

// ── WebSocket lifecycle ──
function connect() {
  ws = new WebSocket(`${location.protocol === 'https:' ? 'wss:' : 'ws:'}//${location.host}/ws`);
  ws.onopen = () => { recon = 0; setCaptionDot('conn'); };
  ws.onmessage = e => {
    if (typeof e.data !== 'string') {
      if (window.LinguaTaxi && window.LinguaTaxi.plugins) {
        window.LinguaTaxi.plugins.fire('on_binary', e.data);
      }
      return;
    }
    try { handleMsg(JSON.parse(e.data)); } catch (err) {}
  };
  ws.onclose = () => {
    setCaptionDot('');
    if (recon < 50) setTimeout(connect, Math.min(1000 * Math.pow(1.5, recon++), 10000));
  };
  ws.onerror = () => { ws.close(); };
}
window.addEventListener('beforeunload', () => { if (ws) ws.close(); });

function esc(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

// ── Init: fetch config + grid + start WS ──
async function init() {
  try {
    const cfgResp = await fetch('/api/config');
    if (cfgResp.ok) {
      cfg = await cfgResp.json();
      applyStyle(cfg);
      if (cfg.ui_language) await loadTranslations(cfg.ui_language);
    }
  } catch (e) {}
  try {
    const gResp = await fetch('/api/display-grids');
    if (gResp.ok) {
      const grids = await gResp.json();
      currentGrid = grids[DISPLAY_TARGET] || {};
    }
  } catch (e) {}
  buildGrid();
  connect();
}
init();
