/* ═══════════════════════════════════════════════
   Bidirectional Display — split-screen / single-language caption viewer
   ═══════════════════════════════════════════════ */

let ws = null, cfg = {}, recon = 0;
const zonesEl = document.getElementById('zones');

// Parse URL parameters
const params = new URLSearchParams(location.search);
const singleLang = (params.get('lang') || '').toUpperCase() || null;
const splitDir = (params.get('split') || '').toLowerCase();
const mode = singleLang ? 'single' : (params.get('mode') || 'split');

// Zone state: keyed by language code (e.g. "EN", "AR")
// Each zone: {el, scrollBox, inner, interimEl, dotEl, lines[], lastSpeaker}
const zones = {};
let bidirLangs = []; // ["EN", "AR"] from config

function esc(t) {
  const d = document.createElement('div');
  d.textContent = t;
  return d.innerHTML;
}

/* ─── Build zones ─── */
function buildZones(c) {
  zonesEl.innerHTML = '';
  Object.keys(zones).forEach(k => delete zones[k]);

  bidirLangs = c.bidirectional_langs || [];
  const inputLang = (c.input_lang || 'EN').toUpperCase();

  // Determine which language(s) to show
  let langsToShow = [];
  if (mode === 'single' && singleLang) {
    langsToShow = [singleLang];
  } else if (bidirLangs.length === 2) {
    langsToShow = [...bidirLangs];
  } else {
    // Fallback: use input lang + first translation if bidirectional not configured
    langsToShow = [inputLang];
    const trans = c.translations || [];
    if (trans.length > 0) {
      langsToShow.push(trans[0].lang);
    }
  }

  if (langsToShow.length === 0) {
    zonesEl.innerHTML = '<div class="no-config">No languages configured for bidirectional display.<br>Enable bidirectional mode in the operator panel.</div>';
    return;
  }

  // Configure layout
  if (langsToShow.length === 1) {
    zonesEl.classList.add('single-zone');
    zonesEl.classList.remove('vertical');
  } else {
    zonesEl.classList.remove('single-zone');
    if (splitDir === 'v') {
      zonesEl.classList.add('vertical');
    } else {
      zonesEl.classList.remove('vertical');
    }
  }

  langsToShow.forEach(lang => {
    const zone = document.createElement('div');
    zone.className = 'zone';

    // Header with dot and language label
    const hdr = document.createElement('div');
    hdr.className = 'zone-hdr';
    const dot = document.createElement('span');
    dot.className = 'dot';
    hdr.appendChild(dot);
    const langName = _langName(lang, c);
    hdr.appendChild(document.createTextNode(' ' + langName));
    zone.appendChild(hdr);

    // Scrolling caption box
    const box = document.createElement('div');
    box.className = 'scroll-box';
    const inner = document.createElement('div');
    inner.className = 'scroll-inner';

    // Interim line (always at end)
    const interimLine = document.createElement('div');
    interimLine.className = 'line interim';
    interimLine.style.color = 'var(--primary-color)';
    interimLine.style.display = 'none';
    inner.appendChild(interimLine);

    box.appendChild(inner);
    zone.appendChild(box);
    zonesEl.appendChild(zone);

    zones[lang] = {
      el: zone,
      scrollBox: box,
      inner: inner,
      interimEl: interimLine,
      dotEl: dot,
      lines: [],
      lastSpeaker: null
    };
  });
}

function _langName(code, c) {
  // Try to find a readable name from config translations or DEEPL lang names
  const trans = c.translations || [];
  for (const t of trans) {
    if (t.lang === code) return t.name || code;
  }
  const inputLang = (c.input_lang || 'EN').toUpperCase();
  if (code === inputLang) return (c.input_lang_name || code) + ' (Source)';
  return code;
}

/* ─── Add a final line to a zone ─── */
function addFinalLine(langCode, text, speaker, lineId, isTranslation, color) {
  const z = zones[langCode];
  if (!z) return;

  const line = document.createElement('div');
  line.className = 'line' + (isTranslation ? ' translated' : '');
  line.style.color = isTranslation ? 'var(--secondary-color)' : 'var(--primary-color)';
  if (lineId !== undefined) line.dataset.lineId = lineId;
  if (isTranslation) line.dataset.isTranslation = '1';

  // Speaker label on change
  if (speaker && speaker !== z.lastSpeaker) {
    const tag = document.createElement('span');
    tag.className = 'speaker-tag';
    tag.textContent = speaker + ': ';
    tag.style.color = color || 'var(--primary-color)';
    line.appendChild(tag);
    z.lastSpeaker = speaker;
  } else if (!speaker && z.lastSpeaker) {
    z.lastSpeaker = null;
  }

  const txt = document.createTextNode(text);
  line.appendChild(txt);
  line._textNode = txt;

  z.inner.insertBefore(line, z.interimEl);
  z.interimEl.style.display = 'none';
  z.interimEl.innerHTML = '';
  z.lines.push(line);

  while (z.lines.length > 200) { z.lines.shift().remove(); }
  requestAnimationFrame(() => { z.scrollBox.scrollTop = z.scrollBox.scrollHeight; });
}

/* ─── Correct an existing line ─── */
function correctLine(langCode, lineId, newText, isTranslation) {
  const z = zones[langCode];
  if (!z) return;
  for (let i = z.lines.length - 1; i >= 0; i--) {
    const el = z.lines[i];
    if (el.dataset.lineId == lineId) {
      // For corrections, match on translation status too when both exist
      if (isTranslation !== undefined) {
        const elIsTrans = el.dataset.isTranslation === '1';
        if (elIsTrans !== isTranslation) continue;
      }
      if (el._textNode) {
        el._textNode.textContent = newText;
      } else {
        const tag = el.querySelector('.speaker-tag');
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

/* ─── Set interim text in a zone ─── */
function setInterim(langCode, text, speaker, isTranslation, color) {
  const z = zones[langCode];
  if (!z) return;

  if (text) {
    z.interimEl.innerHTML = '';
    z.interimEl.style.color = isTranslation ? 'var(--secondary-color)' : 'var(--primary-color)';
    if (isTranslation) {
      z.interimEl.classList.add('translated');
    } else {
      z.interimEl.classList.remove('translated');
    }

    const showSp = speaker && speaker !== z.lastSpeaker;
    if (showSp) {
      const tag = document.createElement('span');
      tag.className = 'speaker-tag';
      tag.textContent = speaker + ': ';
      tag.style.color = color || '';
      z.interimEl.appendChild(tag);
    }
    z.interimEl.appendChild(document.createTextNode(text));
    z.interimEl.style.display = 'block';
  } else {
    z.interimEl.style.display = 'none';
    z.interimEl.innerHTML = '';
  }
  requestAnimationFrame(() => { z.scrollBox.scrollTop = z.scrollBox.scrollHeight; });
}

/* ─── Clear all zones ─── */
function clearAll() {
  Object.values(zones).forEach(z => {
    z.lines.forEach(l => l.remove());
    z.lines = [];
    z.interimEl.style.display = 'none';
    z.interimEl.innerHTML = '';
    z.lastSpeaker = null;
  });
}

/* ─── Determine target zone language for a translation slot ─── */
function slotToLang(slot) {
  // Translation slots 0 and 1 correspond to bidirectional language pairs.
  // Slot 0 translates into bidirLangs[0], slot 1 into bidirLangs[1].
  const trans = cfg.translations || [];
  for (const t of trans) {
    if (t.slot === slot) return t.lang;
  }
  // Fallback: if bidirLangs are set, slot indices map directly
  if (slot < bidirLangs.length) return bidirLangs[slot];
  return null;
}

/* ─── Apply style from operator panel ─── */
function applyStyle(c) {
  const r = document.documentElement.style;
  if (c.bg_color) r.setProperty('--bg', c.bg_color);
  if (c.font_size) r.setProperty('--fs', c.font_size + 'px');
  if (c.max_lines) r.setProperty('--max-lines', c.max_lines);
  if (c.font_css) r.setProperty('--font', c.font_css);
  if (c.caption_color) r.setProperty('--primary-color', c.caption_color);
  if (c.session_title) {
    document.getElementById('title').textContent = c.session_title;
    document.title = c.session_title;
  }
  const fw = document.getElementById('footerWrap'), fi = document.getElementById('footerImg');
  if (c.footer_image) {
    fi.src = '/uploads/' + c.footer_image + '?t=' + Date.now();
    fw.classList.add('vis');
  } else {
    fw.classList.remove('vis');
  }
}

/* ─── Check if a language matches for single-language mode ─── */
function langMatches(lang) {
  if (!singleLang) return true;
  if (!lang) return false;
  return lang.toUpperCase().split('-')[0] === singleLang.split('-')[0];
}

/* ─── Message handler ─── */
function handleMsg(msg) {
  switch (msg.type) {
    case 'status':
      if (msg.state === 'speech') {
        Object.values(zones).forEach(z => z.dotEl.className = 'dot live');
      } else if (msg.state === 'silence') {
        Object.values(zones).forEach(z => z.dotEl.className = 'dot conn');
      }
      break;

    case 'interim': {
      // Original speech interim — route by detected_lang
      const lang = (msg.detected_lang || cfg.input_lang || 'EN').toUpperCase();
      if (mode === 'single') {
        if (langMatches(lang)) {
          setInterim(singleLang, msg.text || '', msg.speaker || '', false, msg.color);
        }
      } else if (zones[lang]) {
        setInterim(lang, msg.text || '', msg.speaker || '', false, msg.color);
        zones[lang].dotEl.className = 'dot live';
      }
      break;
    }

    case 'final': {
      // Original speech final — route by detected_lang
      const lang = (msg.detected_lang || cfg.input_lang || 'EN').toUpperCase();
      if (mode === 'single') {
        if (langMatches(lang)) {
          setInterim(singleLang, '');
          if (msg.text) addFinalLine(singleLang, msg.text, msg.speaker || '', msg.line_id, false, msg.color);
        }
      } else if (zones[lang]) {
        setInterim(lang, '');
        if (msg.text) addFinalLine(lang, msg.text, msg.speaker || '', msg.line_id, false, msg.color);
      }
      break;
    }

    case 'interim_translation': {
      // Translation interim — route by target lang
      if (msg.slot === undefined) break;
      const targetLang = slotToLang(msg.slot);
      if (!targetLang) break;
      if (mode === 'single') {
        if (langMatches(targetLang)) {
          setInterim(singleLang, msg.translated || '', msg.speaker || '', true, msg.color);
        }
      } else if (zones[targetLang]) {
        setInterim(targetLang, msg.translated || '', msg.speaker || '', true, msg.color);
        zones[targetLang].dotEl.className = 'dot live';
      }
      break;
    }

    case 'final_translation': {
      // Translation final — route by target lang
      if (msg.slot === undefined) break;
      const targetLang = slotToLang(msg.slot);
      if (!targetLang) break;
      if (mode === 'single') {
        if (langMatches(targetLang)) {
          setInterim(singleLang, '');
          if (msg.translated) addFinalLine(singleLang, msg.translated, msg.speaker || '', msg.line_id, true, msg.color);
        }
      } else if (zones[targetLang]) {
        setInterim(targetLang, '');
        if (msg.translated) addFinalLine(targetLang, msg.translated, msg.speaker || '', msg.line_id, true, msg.color);
      }
      break;
    }

    case 'correct_line': {
      // Correct original text — find which zone by detected_lang
      if (msg.line_id === undefined || !msg.text) break;
      const lang = (msg.detected_lang || cfg.input_lang || 'EN').toUpperCase();
      if (mode === 'single') {
        if (langMatches(lang)) correctLine(singleLang, msg.line_id, msg.text, false);
      } else {
        // Try all zones — the line_id will only match in the right one
        Object.keys(zones).forEach(k => correctLine(k, msg.line_id, msg.text, false));
      }
      break;
    }

    case 'correct_translation': {
      if (msg.line_id === undefined || msg.slot === undefined || !msg.translated) break;
      const targetLang = slotToLang(msg.slot);
      if (!targetLang) break;
      if (mode === 'single') {
        if (langMatches(targetLang)) correctLine(singleLang, msg.line_id, msg.translated, true);
      } else if (zones[targetLang]) {
        correctLine(targetLang, msg.line_id, msg.translated, true);
      }
      break;
    }

    case 'speaker_change':
      // Speaker labels appear inline — no separate handling needed.
      break;

    case 'clear_captions':
      clearAll();
      break;

    case 'config_update':
      applyStyle(msg);
      if (msg.all_translations !== undefined || msg.bidirectional_enabled !== undefined) {
        fetch('/api/config').then(r => r.json()).then(c => {
          cfg = c;
          buildZones(c);
          applyStyle(c);
        });
      }
      break;
  }
}

/* ─── WebSocket connection with reconnect ─── */
function connect() {
  ws = new WebSocket(`${location.protocol === 'https:' ? 'wss:' : 'ws:'}//${location.host}/ws`);
  ws.onopen = () => {
    recon = 0;
    Object.values(zones).forEach(z => z.dotEl.className = 'dot conn');
  };
  ws.onmessage = e => handleMsg(JSON.parse(e.data));
  ws.onclose = () => {
    Object.values(zones).forEach(z => z.dotEl.className = 'dot');
    if (recon < 50) setTimeout(connect, Math.min(1000 * Math.pow(1.5, recon++), 10000));
  };
  ws.onerror = () => {};
}

/* ─── Init ─── */
fetch('/api/config').then(r => r.json()).then(c => {
  cfg = c;
  buildZones(c);
  applyStyle(c);
  connect();
}).catch(() => connect());
