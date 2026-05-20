let ws = null, recon = 0;
let interimStr = '';
let transcribing = false;
let pttMode = localStorage.getItem('lt_dictation_mode') || 'toggle';
let pttCode = localStorage.getItem('lt_ptt_key') || '';
let pttDisplayName = localStorage.getItem('lt_ptt_key_display') || '';
let capturing = false;
let graceTimer = null;
const GRACE_MS = 750;

// i18n helpers — must be declared before any code that calls t()
let _i18n={}, _i18nEn={};
function t(key,vars){
    let s=_i18n[key]||_i18nEn[key]||key;
    if(vars)Object.entries(vars).forEach(([k,v])=>{s=s.replaceAll('{'+k+'}',v)});
    return s;
}

let BLOCKED_KEYS = {};
function rebuildBlockedKeys() {
  BLOCKED_KEYS = {
    'Enter':  t('dictation.ptt_blocked_enter'),
    'Tab':    t('dictation.ptt_blocked_tab'),
    'Escape': t('dictation.ptt_blocked_escape'),
  };
}
rebuildBlockedKeys();
const MODIFIER_CODES = ['ShiftLeft','ShiftRight','ControlLeft','ControlRight','AltLeft','AltRight','MetaLeft','MetaRight'];

function setMode(m) {
  if (m === 'toggle' && pttMode === 'hold' && transcribing) {
    if (graceTimer) { clearTimeout(graceTimer); graceTimer = null; }
    transcribing = false;
    if (ws && ws.readyState === 1) {
      ws.send(JSON.stringify({ type: 'set_dictation_active', active: false }));
    }
    updateGoBtn();
    updateInsertHint();
  } else if (m === 'toggle' && graceTimer) {
    clearTimeout(graceTimer);
    graceTimer = null;
  }
  pttMode = m;
  localStorage.setItem('lt_dictation_mode', m);
  document.getElementById('mtToggle').classList.toggle('active', m === 'toggle');
  document.getElementById('mtHold').classList.toggle('active', m === 'hold');
}

function updatePttDisplay() {
  const disp = document.getElementById('pttKeyDisplay');
  const btn = document.getElementById('pttBtn');
  if (pttCode) {
    disp.textContent = pttDisplayName || pttCode;
    btn.textContent = t('dictation.ptt_change');
    btn.setAttribute('data-i18n', 'dictation.ptt_change');
  } else {
    disp.textContent = t('dictation.ptt_none');
    btn.textContent = t('dictation.ptt_set');
    btn.setAttribute('data-i18n', 'dictation.ptt_set');
  }
}

function showPttMsg(msg) {
  const el = document.getElementById('pttMsg');
  el.textContent = msg;
  el.classList.add('show');
  setTimeout(() => el.classList.remove('show'), 3000);
}

let _captureListener = null;

function startCapture() {
  if (capturing) {
    // Cancel capture
    capturing = false;
    document.getElementById('pttBtn').classList.remove('btn-capture');
    if (_captureListener) {
      document.removeEventListener('keydown', _captureListener, true);
      _captureListener = null;
    }
    updatePttDisplay();
    return;
  }
  capturing = true;
  const btn = document.getElementById('pttBtn');
  btn.textContent = t('dictation.ptt_capture_prompt');
  btn.classList.add('btn-capture');

  function onCapture(e) {
    e.preventDefault();
    e.stopPropagation();

    if (MODIFIER_CODES.includes(e.code)) {
      showPttMsg(t('dictation.ptt_blocked_modifier'));
      return;
    }

    if (BLOCKED_KEYS[e.key]) {
      showPttMsg(BLOCKED_KEYS[e.key]);
      return;
    }

    pttCode = e.code;
    pttDisplayName = e.key === ' ' ? 'Space' : e.key;
    localStorage.setItem('lt_ptt_key', pttCode);
    localStorage.setItem('lt_ptt_key_display', pttDisplayName);
    capturing = false;
    btn.classList.remove('btn-capture');
    _captureListener = null;
    updatePttDisplay();

    document.removeEventListener('keydown', onCapture, true);
  }

  _captureListener = onCapture;
  document.addEventListener('keydown', onCapture, true);
}

async function loadTranslations(lang){
    try{
        if(!Object.keys(_i18nEn).length){const r=await fetch('/api/locales/en');if(r.ok)_i18nEn=await r.json()}
        const r2=await fetch('/api/locales/'+lang.toLowerCase());
        if(r2.ok)_i18n=await r2.json();else _i18n={..._i18nEn};
    }catch(e){_i18n={..._i18nEn}}
    document.querySelectorAll('[data-i18n]').forEach(el=>{
        if(el.hasAttribute('data-i18n-ph')) el.placeholder=t(el.getAttribute('data-i18n'));
        else el.textContent=t(el.getAttribute('data-i18n'));
    });
    document.documentElement.lang=lang.toLowerCase();
    document.documentElement.dir=lang==='AR'?'rtl':'ltr';
    rebuildBlockedKeys();
    updatePttDisplay();
}

const editor = document.getElementById('editor');
const interimBar = document.getElementById('interimBar');
const interimTextEl = document.getElementById('interimText');
const dot = document.getElementById('dot');
const statusEl = document.getElementById('status');
const wordcountEl = document.getElementById('wordcount');
const insertHint = document.getElementById('insertHint');

// Track whether cursor is at end (for insert-at-cursor hint)
function isCursorAtEnd() {
  return editor.selectionStart === editor.value.length &&
         editor.selectionEnd === editor.value.length;
}

function updateInsertHint() {
  if (editor.value.length > 0 && !isCursorAtEnd() &&
      document.activeElement === editor && transcribing) {
    insertHint.style.opacity = '1';
    insertHint.textContent = t('dictation.insert_at_cursor_position', {pos: editor.selectionStart});
  } else {
    insertHint.style.opacity = '0';
  }
}

editor.addEventListener('click', updateInsertHint);
editor.addEventListener('keyup', updateInsertHint);
editor.addEventListener('focus', updateInsertHint);
editor.addEventListener('blur', () => { insertHint.style.opacity = '0'; });

function insertTextAtCursor(text) {
  const start = editor.selectionStart;
  const end = editor.selectionEnd;
  const val = editor.value;

  // Determine if we need a space before the inserted text
  let prefix = '';
  if (start > 0 && val.length > 0) {
    const before = val[start - 1];
    if (before !== '\n' && before !== ' ' && before !== '\t') {
      prefix = ' ';
    }
  }

  const insert = prefix + text;
  // Use execCommand for undo support where available, fall back to direct manipulation
  editor.focus();
  editor.setSelectionRange(start, end);
  if (!document.execCommand('insertText', false, insert)) {
    // Fallback
    editor.value = val.slice(0, start) + insert + val.slice(end);
  }
  const newPos = start + insert.length;
  editor.setSelectionRange(newPos, newPos);
  // Scroll to cursor position, not document bottom
  const lineHeight = parseInt(getComputedStyle(editor).lineHeight) || 20;
  const lines = editor.value.substring(0, editor.selectionStart).split('\n').length;
  editor.scrollTop = Math.max(0, (lines * lineHeight) - editor.clientHeight / 2);
  updateWordCount();
  updateInsertHint();
}

function appendText(text) {
  const val = editor.value;
  let prefix = '';
  if (val.length > 0 && !val.endsWith('\n') && !val.endsWith(' ')) {
    prefix = ' ';
  }
  // Move cursor to end and use insertTextAtCursor for undo support
  editor.selectionStart = editor.selectionEnd = editor.value.length;
  const fullText = prefix + text;
  editor.focus();
  editor.setSelectionRange(editor.value.length, editor.value.length);
  if (!document.execCommand('insertText', false, fullText)) {
    editor.value += fullText;
  }
  const len = editor.value.length;
  editor.setSelectionRange(len, len);
  editor.scrollTop = editor.scrollHeight;
  updateWordCount();
}

function updateWordCount() {
  const text = editor.value.trim();
  const count = text ? text.split(/\s+/).length : 0;
  if (count === 1) {
    wordcountEl.textContent = t('dictation.word_count_singular', {count: count});
  } else {
    wordcountEl.textContent = t('dictation.word_count', {count: count});
  }
}

// Listen for manual edits too
editor.addEventListener('input', updateWordCount);

function copyAll() {
  const text = editor.value.trim();
  if (!text) { statusEl.textContent = t('dictation.status_nothing_to_copy') || 'Nothing to copy'; setTimeout(() => { statusEl.textContent = transcribing ? t('dictation.status_listening') : t('dictation.status_stopped'); }, 1500); return; }
  navigator.clipboard.writeText(text).then(() => {
    statusEl.textContent = t('dictation.status_copied');
    setTimeout(() => { statusEl.textContent = transcribing ? t('dictation.status_listening') : t('dictation.status_stopped'); }, 1500);
  });
}

function saveFile() {
  const text = editor.value.trim();
  if (!text) { statusEl.textContent = t('dictation.status_nothing_to_save') || 'Nothing to save'; setTimeout(() => { statusEl.textContent = transcribing ? t('dictation.status_listening') : t('dictation.status_stopped'); }, 1500); return; }
  const fd = new FormData();
  fd.append('text', text);
  fetch('/api/dictation-save', { method: 'POST', body: fd })
    .then(r => r.json())
    .then(r => {
      if (r.path) {
        statusEl.textContent = t('dictation.status_saved');
        setTimeout(() => { statusEl.textContent = transcribing ? t('dictation.status_listening') : t('dictation.status_stopped'); }, 2000);
      } else {
        statusEl.textContent = t('dictation.status_save_error', {error: r.error || 'unknown'});
      }
    })
    .catch(() => { statusEl.textContent = t('dictation.status_save_failed'); });
}

function updateDir() {
  const dir = document.getElementById('saveDir').value.trim();
  if (!dir) return;
  const fd = new FormData();
  fd.append('dictation_dir', dir);
  fetch('/api/dictation-config', { method: 'POST', body: fd })
    .then(r => r.json())
    .then(r => {
      if (r.error) {
        statusEl.textContent = t('dictation.status_invalid_path', {error: r.error});
      } else {
        document.getElementById('saveDir').value = r.dictation_dir;
        statusEl.textContent = t('dictation.status_directory_updated');
        setTimeout(() => { statusEl.textContent = transcribing ? t('dictation.status_listening') : t('dictation.status_stopped'); }, 1500);
      }
    });
}

function clearAll() {
  editor.value = '';
  interimStr = '';
  interimBar.style.display = 'none';
  updateWordCount();
  updateInsertHint();
}

function toggleTranscribing() {
  if (ws && ws.readyState === 1) {
    transcribing = !transcribing;
    ws.send(JSON.stringify({ type: 'set_dictation_active', active: transcribing }));
    updateGoBtn();
    updateInsertHint();
  }
}

function updateGoBtn() {
  const btn = document.getElementById('goBtn');
  if (transcribing) {
    btn.textContent = t('dictation.stop_btn');
    btn.classList.add('active');
  } else {
    btn.textContent = t('dictation.start_btn');
    btn.classList.remove('active');
  }
}

function handleMsg(msg) {
  switch (msg.type) {
    case 'status':
      if (msg.dictation_active !== undefined) {
        transcribing = msg.dictation_active;
        updateGoBtn();
        if (!transcribing) { statusEl.textContent = t('dictation.status_stopped'); break; }
      }
      if (msg.state === 'speech') {
        dot.className = 'dot live';
        statusEl.textContent = t('dictation.status_listening');
      } else if (msg.state === 'silence') {
        dot.className = 'dot conn';
        statusEl.textContent = transcribing ? t('dictation.status_ready') : t('dictation.status_stopped');
      }
      break;
    case 'interim':
      interimStr = msg.text || '';
      if (interimStr) {
        interimTextEl.textContent = interimStr;
        interimBar.style.display = 'flex';
      } else {
        interimBar.style.display = 'none';
      }
      break;
    case 'final':
      if (msg.text) {
        // Insert at cursor if user has positioned it mid-text, else append
        if (document.activeElement === editor && !isCursorAtEnd()) {
          insertTextAtCursor(msg.text);
        } else {
          appendText(msg.text);
        }
      }
      interimStr = '';
      interimBar.style.display = 'none';
      break;
    case 'dictation_active':
      transcribing = msg.active;
      updateGoBtn();
      if (!msg.active) {
        statusEl.textContent = t('dictation.status_stopped');
        dot.className = 'dot';
        interimBar.style.display = 'none';
      } else {
        statusEl.textContent = t('dictation.status_listening');
        dot.className = 'dot conn';
      }
      updateInsertHint();
      break;
    case 'config_update':
      if (msg.ui_language) loadTranslations(msg.ui_language);
      break;
    case 'captioning_paused':
    case 'clear_captions':
      break; // Ignore — dictation is independent
    case 'interim_translation':
    case 'final_translation':
      break; // Ignore — dictation is independent
  }
}

function connect() {
  ws = new WebSocket((location.protocol === 'https:' ? 'wss:' : 'ws:') + '//' + location.host + '/ws');
  ws.onopen = () => {
    recon = 0;
    dot.className = 'dot conn';
    statusEl.textContent = t('dictation.status_ready');
  };
  ws.onmessage = e => { try { handleMsg(JSON.parse(e.data)); } catch(err) {} };
  ws.onclose = () => {
    dot.className = 'dot';
    statusEl.textContent = t('dictation.status_reconnecting');
    if (recon < 50) setTimeout(connect, Math.min(1000 * Math.pow(1.5, recon++), 10000));
    else statusEl.textContent = 'Connection lost — please refresh the page';
  };
  ws.onerror = () => { ws.close(); };
}
window.addEventListener('beforeunload', () => { if(ws) ws.close(); });

document.addEventListener('keydown', function(e) {
  if (capturing) return;
  if (!pttCode || e.code !== pttCode) return;
  if (['INPUT','SELECT','TEXTAREA'].includes(e.target.tagName) && e.target !== editor) return;

  e.preventDefault();

  if (pttMode === 'toggle') {
    if (e.repeat) return;
    toggleTranscribing();
  } else {
    if (graceTimer) { clearTimeout(graceTimer); graceTimer = null; }
    if (!transcribing && ws && ws.readyState === 1) {
      transcribing = true;
      ws.send(JSON.stringify({ type: 'set_dictation_active', active: true }));
      updateGoBtn();
      updateInsertHint();
    }
  }
});

document.addEventListener('keyup', function(e) {
  if (capturing) return;
  if (!pttCode || e.code !== pttCode) return;
  if (pttMode !== 'hold') return;

  if (transcribing) {
    graceTimer = setTimeout(function() {
      graceTimer = null;
      if (transcribing && ws && ws.readyState === 1) {
        transcribing = false;
        ws.send(JSON.stringify({ type: 'set_dictation_active', active: false }));
        updateGoBtn();
        updateInsertHint();
      }
    }, GRACE_MS);
  }
});

document.addEventListener('visibilitychange', function() {
  if (document.hidden && pttMode === 'hold' && transcribing && !graceTimer) {
    graceTimer = setTimeout(function() {
      graceTimer = null;
      if (transcribing && ws && ws.readyState === 1) {
        transcribing = false;
        ws.send(JSON.stringify({ type: 'set_dictation_active', active: false }));
        updateGoBtn();
        updateInsertHint();
      }
    }, GRACE_MS);
  }
});

// Init PTT state from localStorage
setMode(pttMode);
updatePttDisplay();

// Load initial config and translations
fetch('/api/dictation-config').then(r=>r.json()).then(c=>{
  document.getElementById('saveDir').value = c.dictation_dir || '';
});
fetch('/api/config').then(r=>r.json()).then(c=>{
  if(c.ui_language) loadTranslations(c.ui_language);
  connect();
}).catch(()=>connect());
