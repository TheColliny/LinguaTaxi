/**
 * LinguaTaxi — Fact Checker Plugin Panel
 * Registers with plugin system via window.LinguaTaxi.plugins.register().
 * Events: on_final (transcript), on_enabled, on_disabled, on_session_start.
 */

(function() {
  const MIN_LENGTH    = 20;  // chars — skip fragments shorter than this
  const MAX_QUEUE     = 12;  // drop oldest if queue exceeds this
  const MAX_RESULTS   = 15;  // keep last N results in view
  const MAX_CONCURRENT = 3;  // simultaneous API requests (safe for Sonnet + web search rate limits)
  const HISTORY_WINDOW = 30; // seconds of transcript to keep for "Check Last 30s"
  const MAX_HISTORY   = 50;  // max transcript entries retained

  let enabled     = false;
  let panelOpen   = true;
  let mode        = 'auto'; // 'auto' or 'manual'
  let queue       = [];     // { text, speaker }
  let activeCount = 0;      // number of in-flight requests
  let results     = [];     // accumulated result objects
  let isOperatorPage = null; // true = operator (has API), false = audience (WS only)
  let history     = [];     // { text, speaker, ts } — recent transcripts for manual mode

  // ── DOM refs (resolved after DOMContentLoaded) ───────────────────────────
  let elCount, elEmpty, elResults, elQueueStatus, elProviderBadge;
  let elModeToggle, elModeAuto, elModeManual, elManualControls;

  function $(id) { return document.getElementById(id); }

  document.addEventListener('DOMContentLoaded', () => {
    elCount         = $('fc-count');
    elEmpty         = $('fc-empty');
    elResults       = $('fc-results');
    elQueueStatus   = $('fc-queue-status');
    elProviderBadge = $('fc-provider-badge');
    elModeToggle    = $('fc-mode-toggle');
    elModeAuto      = $('fc-mode-auto');
    elModeManual    = $('fc-mode-manual');
    elManualControls = $('fc-manual-controls');
    renderState();
    detectPage();
    fetchMode();
  });

  async function detectPage() {
    try {
      const resp = await fetch('/api/fact-check/status');
      isOperatorPage = resp.ok;
    } catch(e) {
      isOperatorPage = false;
    }
    if (isOperatorPage) fetchProviderStatus();
    renderState();
  }

  // ── Public ────────────────────────────────────────────────────────────────

  function onTranscript(text, speaker) {
    if (!enabled) return;
    if (!isOperatorPage) return;
    if (!text || text.trim().length < MIN_LENGTH) return;

    history.push({ text: text.trim(), speaker: speaker || '', ts: Date.now() });
    if (history.length > MAX_HISTORY) history.shift();

    if (mode === 'manual') return;

    if (queue.length >= MAX_QUEUE) queue.shift(); // drop oldest
    queue.push({ text: text.trim(), speaker: speaker || '' });
    updateQueueStatus();
    processNext();
  }

  // ── Provider status ─────────────────────────────────────────────────────

  async function fetchProviderStatus() {
    if (!elProviderBadge) return;
    try {
      const resp = await fetch('/api/fact-check/status');
      if (!resp.ok) return;
      const s = resp.json ? await resp.json() : {};
      const provider = s.provider || 'gemini';
      let label, cost, keyOk;
      if (provider === 'claude') {
        label = `Claude ${s.claude_model || 'sonnet'}`;
        cost = 'paid';
        keyOk = s.claude_key_set;
      } else if (provider === 'groq') {
        label = 'Groq/Llama';
        cost = 'free';
        keyOk = s.groq_key_set && s.brave_key_set;
      } else if (provider === 'magi') {
        label = 'MAGI';
        cost = 'multi';
        keyOk = s.claude_key_set || s.gemini_key_set || (s.groq_key_set && s.brave_key_set);
      } else {
        label = 'Gemini Flash';
        cost = 'free';
        keyOk = s.gemini_key_set;
      }
      const cls = keyOk ? 'fc-prov--ok' : 'fc-prov--nokey';
      let filterHtml = '';
      if (s.claim_filter_loaded) {
        filterHtml = '<span class="fc-filter-badge fc-filter--on">Filter ON</span>';
      } else if (s.claim_filter_available) {
        filterHtml = '<span class="fc-filter-badge fc-filter--loading">Filter loading\u2026</span>';
      } else {
        filterHtml = '<span class="fc-filter-badge fc-filter--off" onclick="window._fcDownloadFilter(this)">Download Filter</span>';
      }
      elProviderBadge.innerHTML =
        `<span class="fc-prov ${cls}">${esc(label)}</span>` +
        `<span class="fc-prov-cost">${cost}</span>` +
        (!keyOk ? '<span class="fc-prov-warn">no API key</span>' : '') +
        filterHtml;
    } catch(e) { /* ignore */ }
  }

  window._fcDownloadFilter = async function(el) {
    el.textContent = 'Downloading\u2026';
    el.style.pointerEvents = 'none';
    try {
      const resp = await fetch('/api/fact-check/filter/download', { method: 'POST' });
      if (resp.ok) {
        el.textContent = 'Downloaded \u2014 loading\u2026';
        setTimeout(fetchProviderStatus, 3000);
      } else {
        const err = await resp.json().catch(() => ({}));
        el.textContent = 'Failed';
        el.title = err.detail || 'Download failed';
      }
    } catch(e) {
      el.textContent = 'Failed';
      el.title = e.message;
    }
  };

  // ── Processing ───────────────────────────────────────────────────────────

  // Drain the queue up to MAX_CONCURRENT simultaneous requests.
  // Called whenever an item is added or a request finishes.
  function processNext() {
    while (activeCount < MAX_CONCURRENT && queue.length > 0) {
      const item = queue.shift();
      activeCount++;
      updateQueueStatus();
      runCheck(item).finally(() => {
        activeCount--;
        updateQueueStatus();
        processNext(); // refill the freed slot
      });
    }
  }

  async function runCheck(item) {
    const placeholderId = 'fc-ph-' + Date.now() + '-' + Math.random().toString(36).slice(2);
    appendPlaceholder(placeholderId, item.text, item.speaker);

    try {
      const res = await fetch('/api/fact-check', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ statement: item.text, speaker: item.speaker })
      });

      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: res.statusText }));
        replacePlaceholder(placeholderId, {
          statement: item.text, speaker: item.speaker,
          type: 'ambiguous', error: err.detail || 'Server error'
        });
      } else {
        const data = await res.json();
        const result = { statement: item.text, speaker: item.speaker, ...data };
        results.unshift(result);
        if (results.length > MAX_RESULTS) results.pop();
        replacePlaceholder(placeholderId, result);
        updateCount();
        broadcastToAudience(result);
      }
    } catch (e) {
      replacePlaceholder(placeholderId, {
        statement: item.text, speaker: item.speaker,
        type: 'ambiguous', error: e.message
      });
    }
  }

  // ── Audience broadcast & receive ──────────────────────────────────────────

  function broadcastToAudience(result) {
    if (!isOperatorPage) return;
    fetch('/api/fact-check-broadcast', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(result)
    }).catch(() => {});
  }

  function onFactCheckResult(data) {
    if (!enabled) return;
    if (isOperatorPage) return;
    if (data.type === 'ambiguous' || data.error) return;

    results.unshift(data);
    if (results.length > MAX_RESULTS) results.pop();

    if (!elResults) return;
    if (elEmpty) elEmpty.style.display = 'none';
    const el = document.createElement('div');
    el.className = 'fc-card';
    el.innerHTML = buildCardHTML(data);
    elResults.insertBefore(el, elResults.firstChild);
    trimResults();
    updateCount();
  }

  // ── Rendering ────────────────────────────────────────────────────────────

  function renderState() {
    if (elEmpty) {
      elEmpty.style.display = (results.length === 0) ? 'block' : 'none';
      if (!enabled && results.length === 0) {
        elEmpty.textContent = isOperatorPage === false
          ? 'Waiting for fact-check results\u2026'
          : 'Fact checker is off. Click Enable to analyze statements as they are transcribed.';
      } else if (enabled && results.length === 0) {
        if (isOperatorPage === false) {
          elEmpty.textContent = 'Waiting for fact-check results\u2026';
        } else if (mode === 'manual') {
          elEmpty.textContent = 'Manual mode \u2014 use the buttons above to check statements.';
        } else {
          elEmpty.textContent = 'Listening\u2026 results will appear as statements are transcribed.';
        }
      }
    }
  }

  function updateCount() {
    if (!elCount) return;
    const facts = results.filter(r => r.type === 'fact_claim' && r.accuracy_score != null);
    if (facts.length === 0) { elCount.textContent = ''; return; }
    const avg = Math.round(facts.reduce((s, r) => s + r.accuracy_score, 0) / facts.length);
    elCount.textContent = `avg ${avg}%`;
    elCount.style.color = scoreColor(avg);
  }

  function updateQueueStatus() {
    if (!elQueueStatus) return;
    if (activeCount === 0 && queue.length === 0) {
      elQueueStatus.textContent = '';
      return;
    }
    const parts = [];
    if (activeCount > 0) parts.push(`${activeCount} checking`);
    if (queue.length > 0) parts.push(`${queue.length} queued`);
    elQueueStatus.textContent = parts.join(' \u00B7 ');
  }

  function appendPlaceholder(id, text, speaker) {
    if (!elResults) return;
    if (elEmpty) elEmpty.style.display = 'none';
    const el = document.createElement('div');
    el.id = id;
    el.className = 'fc-card fc-card--loading';
    el.innerHTML = `
      <div class="fc-card-meta">
        ${speaker ? `<span class="fc-speaker">${esc(speaker)}</span>` : ''}
        <span class="fc-badge fc-badge--checking">checking\u2026</span>
      </div>
      <div class="fc-statement">\u201C${esc(text)}\u201D</div>
    `;
    elResults.insertBefore(el, elResults.firstChild);
    trimResults();
  }

  function replacePlaceholder(id, result) {
    const el = document.getElementById(id);
    if (!el) return;
    el.className = 'fc-card';
    el.innerHTML = buildCardHTML(result);
  }

  function buildCardHTML(r) {
    const typeMeta = typeInfo(r.type);
    const vm       = r.verdict ? verdictInfo(r.verdict) : null;
    const hasScore = r.accuracy_score != null;

    let html = `<div class="fc-card-meta">`;
    if (r.speaker) html += `<span class="fc-speaker">${esc(r.speaker)}</span>`;
    html += `<span class="fc-badge ${typeMeta.cls}">${typeMeta.label}</span>`;
    if (vm) html += `<span class="fc-badge ${vm.cls}">${esc(r.verdict.replace(/_/g, ' '))}</span>`;
    if (r.error) html += `<span class="fc-badge fc-badge--error">error</span>`;
    if (r.flip_flop && r.flip_flop.detected) {
      const ffCls = r.flip_flop.type === 'reversal' ? 'fc-badge--flipflop-reversal'
                  : r.flip_flop.type === 'evolution' ? 'fc-badge--flipflop-evolution'
                  : 'fc-badge--flipflop';
      const ffLabel = r.flip_flop.type === 'reversal' ? 'FLIP-FLOP'
                    : r.flip_flop.type === 'evolution' ? 'POSITION EVOLVED'
                    : r.flip_flop.type === 'qualification' ? 'QUALIFIED'
                    : 'FLIP-FLOP';
      html += `<span class="fc-badge ${ffCls}">${esc(ffLabel)}</span>`;
    }
    if (r.provider) {
      const provCls = r.provider === 'magi' ? 'fc-badge--magi'
                    : r.provider === 'claude' ? 'fc-badge--claude'
                    : r.provider === 'groq' ? 'fc-badge--groq'
                    : 'fc-badge--gemini';
      html += `<span class="fc-badge ${provCls}" style="margin-left:auto">${esc(r.provider)}</span>`;
    }
    html += `</div>`;

    html += `<div class="fc-statement">\u201C${esc(r.statement)}\u201D</div>`;

    if (hasScore) {
      const pct = Math.round(r.accuracy_score);
      const col = scoreColor(pct);
      html += `
        <div class="fc-meter">
          <div class="fc-meter-labels">
            <span>Accuracy</span>
            <span style="color:${col};font-family:monospace;font-weight:500">${pct}%</span>
          </div>
          <div class="fc-meter-track">
            <div class="fc-meter-fill" style="width:${pct}%;background:${col}"></div>
          </div>
        </div>`;
    }

    if (r.assessment) {
      html += `<div class="fc-assessment">${esc(r.assessment)}</div>`;
    }

    if (r.language_signals) {
      html += `<div class="fc-signals">Signals: ${esc(r.language_signals)}</div>`;
    }

    if (r.error) {
      html += `<div class="fc-error-msg">${esc(r.error)}</div>`;
    }

    // ── MAGI consensus section ──
    if (r.magi_consensus && r.magi_nodes) {
      const conCls = r.magi_consensus === 'agree' ? 'fc-magi--agree'
                   : r.magi_consensus === 'close' ? 'fc-magi--close'
                   : r.magi_consensus === 'disagree' ? 'fc-magi--disagree'
                   : 'fc-magi--partial';
      const conLabel = r.magi_consensus === 'agree' ? 'CONSENSUS'
                     : r.magi_consensus === 'close' ? 'NEAR CONSENSUS'
                     : r.magi_consensus === 'disagree' ? 'SPLIT VERDICT'
                     : r.magi_consensus.replace(/_/g, ' ').toUpperCase();
      html += `<div class="fc-magi ${conCls}">`;
      html += `<div class="fc-magi-header"><span class="fc-magi-label">${conLabel}</span></div>`;
      html += `<div class="fc-magi-nodes">`;
      for (const [name, node] of Object.entries(r.magi_nodes)) {
        const weightPct = Math.round((node.weight || 0) * 100);
        const dimmed = node.error || node.weight === 0;
        html += `<div class="fc-magi-node${dimmed ? ' fc-magi-node--dim' : ''}">`;
        html += `<span class="fc-magi-prov">${esc(node.label || name)}</span>`;
        html += `<span class="fc-magi-weight">${weightPct}%</span>`;
        if (node.error) {
          html += `<span class="fc-magi-verdict fc-magi-verdict--err">error</span>`;
        } else if (node.verdict) {
          html += `<span class="fc-magi-verdict">${esc(node.verdict)}</span>`;
        }
        if (node.accuracy_score != null) {
          html += `<span class="fc-magi-score">${Math.round(node.accuracy_score)}%</span>`;
        }
        if (node.weight === 0 && !node.error) {
          html += `<span class="fc-magi-suppressed">suppressed</span>`;
        }
        html += `</div>`;
      }
      html += `</div></div>`;
    }

    // ── Source credibility section ──
    const hasSources = r.sources && r.sources.length > 0;
    const hasFlagged = r.flagged_sources && r.flagged_sources.length > 0;

    if (hasSources || hasFlagged) {
      html += '<div class="fc-sources">';
      html += '<div class="fc-sources-title">Sources</div>';

      // Credible + unverified sources
      if (hasSources) {
        r.sources.forEach(src => {
          html += buildSourceHTML(src);
        });
      }

      // Flagged sources (below threshold) — shown dimmed with warning
      if (hasFlagged) {
        html += '<div class="fc-flagged-divider">Below credibility threshold</div>';
        r.flagged_sources.forEach(src => {
          html += buildSourceHTML(src);
        });
      }

      html += '</div>';
    }

    // ── Flip-Flop section ──
    if (r.flip_flop) {
      html += buildFlipFlopHTML(r.flip_flop);
    }

    // ── Recheck button (operator only) ──
    if (r.statement && isOperatorPage) {
      const recheckData = b64Encode(JSON.stringify({
        statement: r.statement,
        speaker: r.speaker || '',
        previous_verdict: r.verdict || null,
        previous_assessment: r.assessment || null,
        previous_score: r.accuracy_score != null ? r.accuracy_score : null,
      }));
      html += `<div class="fc-recheck-row">`;
      if (r._recheck_count) {
        html += `<span class="fc-recheck-count">checked ${r._recheck_count + 1}x</span>`;
      }
      html += `<button class="fc-recheck-btn" onclick="window._fcRecheck(this, '${recheckData}')">Recheck</button>`;
      html += `</div>`;
    }

    return html;
  }

  function buildFlipFlopHTML(ff) {
    if (!ff || !ff.detected) return '';
    const cls = ff.type === 'reversal' ? 'fc-ff--reversal'
              : ff.type === 'evolution' ? 'fc-ff--evolution'
              : ff.type === 'qualification' ? 'fc-ff--qualification'
              : 'fc-ff';
    const header = ff.type === 'reversal' ? 'Speaker contradicts earlier position'
                 : ff.type === 'evolution' ? 'Position has evolved'
                 : ff.type === 'qualification' ? 'Qualified prior position'
                 : 'Prior statements on this topic';
    let h = `<div class="fc-flipflop ${cls}">`;
    h += `<div class="fc-ff-header">${esc(header)}`;
    if (ff.confidence != null) {
      h += `<span class="fc-ff-confidence">${Math.round(ff.confidence * 100)}% confidence</span>`;
    }
    h += `</div>`;
    if (ff.summary) {
      h += `<div class="fc-ff-summary">${esc(ff.summary)}</div>`;
    }
    if (ff.past_statements && ff.past_statements.length > 0) {
      h += `<div class="fc-ff-past">`;
      ff.past_statements.forEach(p => {
        const date = p.date || '?';
        const source = p.source || '';
        const quote = p.quote || '';
        const url = p.url && /^https?:\/\//i.test(p.url) ? p.url : null;
        h += `<div class="fc-ff-stmt">`;
        h += `<span class="fc-ff-date">${esc(date)}</span>`;
        if (source) h += `<span class="fc-ff-source">${esc(source)}</span>`;
        h += `<div class="fc-ff-quote">\u201C${esc(quote)}\u201D</div>`;
        if (url) {
          h += `<a href="${esc(url)}" target="_blank" rel="noopener" class="fc-ff-link">source</a>`;
        }
        h += `</div>`;
      });
      h += `</div>`;
    }
    h += `</div>`;
    return h;
  }

  function buildSourceHTML(src) {
    const domain = src.domain || extractDomain(src.url);
    const safeUrl = /^https?:\/\//i.test(src.url) ? src.url : '#';
    const mbfc = src.mbfc;
    let cls, badges;

    if (mbfc) {
      const score = mbfc.credibility_score;
      if (src.credible) {
        cls = 'fc-src--credible';
        badges = `<span class="fc-src-score fc-src-score--good">${score}</span>`
               + `<span class="fc-src-bias">${esc(mbfc.bias_label)}</span>`
               + `<span class="fc-src-reporting">${esc(mbfc.reporting_label)}</span>`;
      } else {
        cls = 'fc-src--flagged';
        badges = `<span class="fc-src-score fc-src-score--bad">${score}</span>`
               + `<span class="fc-src-bias">${esc(mbfc.bias_label)}</span>`
               + `<span class="fc-src-reporting">${esc(mbfc.reporting_label)}</span>`;
      }
    } else {
      cls = 'fc-src--unverified';
      badges = '<span class="fc-src-unverified">(unverified based on internet source)</span>';
    }

    return `<div class="fc-source ${cls}">
      <div class="fc-src-row">
        <a href="${esc(safeUrl)}" target="_blank" rel="noopener" class="fc-src-link">${esc(src.title || domain)}</a>
      </div>
      <div class="fc-src-row">
        <span class="fc-src-domain">${esc(domain)}</span>
        ${badges}
      </div>
    </div>`;
  }

  function extractDomain(url) {
    try {
      const h = new URL(url).hostname;
      return h.startsWith('www.') ? h.slice(4) : h;
    } catch(e) { return url; }
  }

  // ── Recheck handler ──────────────────────────────────────────────────────

  window._fcRecheck = async function(btn, encoded) {
    const prev = JSON.parse(b64Decode(encoded));
    const statement = prev.statement;
    const speaker   = prev.speaker || '';

    const card = btn.closest('.fc-card');
    if (!card) return;

    const existing = results.find(r => r.statement === statement);
    const recheckCount = existing ? (existing._recheck_count || 0) + 1 : 1;

    card.classList.add('fc-card--loading');
    btn.disabled = true;
    btn.textContent = 'Rechecking\u2026';

    try {
      const res = await fetch('/api/fact-check', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          statement,
          speaker,
          recheck: true,
          previous_verdict: prev.previous_verdict,
          previous_assessment: prev.previous_assessment,
          previous_score: prev.previous_score,
        })
      });

      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: res.statusText }));
        card.classList.remove('fc-card--loading');
        card.innerHTML = buildCardHTML({
          statement, speaker,
          type: 'ambiguous', error: err.detail || 'Recheck failed',
          _recheck_count: recheckCount,
        });
        return;
      }

      const data = await res.json();
      const result = { statement, speaker, ...data, _recheck_count: recheckCount };

      const idx = results.findIndex(r => r.statement === statement);
      if (idx >= 0) results[idx] = result;

      card.classList.remove('fc-card--loading');
      card.innerHTML = buildCardHTML(result);
      updateCount();
    } catch(e) {
      card.classList.remove('fc-card--loading');
      card.innerHTML = buildCardHTML({
        statement, speaker,
        type: 'ambiguous', error: e.message,
        _recheck_count: recheckCount,
      });
    }
  };

  function trimResults() {
    if (!elResults) return;
    const cards = elResults.querySelectorAll('.fc-card');
    cards.forEach((c, i) => { if (i >= MAX_RESULTS) c.remove(); });
  }

  // ── Helpers ───────────────────────────────────────────────────────────────

  function b64Encode(str) { return btoa(unescape(encodeURIComponent(str))); }
  function b64Decode(str) { return decodeURIComponent(escape(atob(str))); }

  function esc(str) {
    return String(str)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function scoreColor(score) {
    if (score >= 80) return '#639922';
    if (score >= 60) return '#1D9E75';
    if (score >= 40) return '#BA7517';
    if (score >= 20) return '#993C1D';
    return '#A32D2D';
  }

  function typeInfo(type) {
    if (type === 'fact_claim') return { cls: 'fc-badge--fact',    label: 'Fact claim' };
    if (type === 'opinion')    return { cls: 'fc-badge--opinion', label: 'Opinion' };
    return                            { cls: 'fc-badge--ambig',   label: 'Ambiguous' };
  }

  function verdictInfo(verdict) {
    const map = {
      'TRUE':         'fc-badge--true',
      'MOSTLY TRUE':  'fc-badge--mostly-true',
      'MIXED':        'fc-badge--mixed',
      'MOSTLY FALSE': 'fc-badge--mostly-false',
      'FALSE':        'fc-badge--false',
      'UNVERIFIABLE': 'fc-badge--ambig',
    };
    return { cls: map[verdict] || 'fc-badge--ambig' };
  }

  // ── Mode management ──────────────────────────────────────────────────────

  async function fetchMode() {
    try {
      const resp = await fetch('/api/plugins/fact_checker/settings');
      if (!resp.ok) return;
      const data = await resp.json();
      const m = (data.values && data.values.mode) || 'auto';
      setModeUI(m);
    } catch(e) { /* ignore */ }
  }

  function setModeUI(m) {
    mode = m;
    if (elModeAuto) elModeAuto.className = 'fc-mode-btn' + (m === 'auto' ? ' fc-mode-btn--active' : '');
    if (elModeManual) elModeManual.className = 'fc-mode-btn' + (m === 'manual' ? ' fc-mode-btn--active' : '');
    if (elManualControls) elManualControls.style.display = (m === 'manual' && enabled) ? 'flex' : 'none';
    renderState();
  }

  window._fcSetMode = async function(m) {
    setModeUI(m);
    try {
      const resp = await fetch('/api/plugins/fact_checker/settings');
      if (!resp.ok) return;
      const data = await resp.json();
      const vals = data.values || {};
      vals.mode = m;
      const fd = new FormData();
      Object.entries(vals).forEach(([k, v]) => fd.append(k, v));
      fetch('/api/plugins/fact_checker/settings', { method: 'POST', body: fd });
    } catch(e) { /* ignore */ }
  };

  // ── Manual check handlers ───────────────────────────────────────────────

  window._fcCheckLast = function() {
    if (!enabled || !isOperatorPage) return;
    if (history.length === 0) return;
    const last = history[history.length - 1];
    queue.push({ text: last.text, speaker: last.speaker });
    updateQueueStatus();
    processNext();
  };

  window._fcCheck30s = function() {
    if (!enabled || !isOperatorPage) return;
    const cutoff = Date.now() - (HISTORY_WINDOW * 1000);
    const recent = history.filter(h => h.ts >= cutoff);
    if (recent.length === 0) return;

    const speaker = recent[recent.length - 1].speaker;
    const combined = recent.map(h => h.text).join(' ');
    if (combined.trim().length < MIN_LENGTH) return;

    queue.push({ text: combined.trim(), speaker });
    updateQueueStatus();
    processNext();
  };

  // Register with plugin system
  window.LinguaTaxi.plugins.register('fact_checker', {
    on_final: (data) => onTranscript(data.text, data.speaker || ''),
    on_enabled: () => { enabled = true; setModeUI(mode); renderState(); },
    on_disabled: () => { enabled = false; queue = []; history = []; updateQueueStatus(); renderState(); if (elManualControls) elManualControls.style.display = 'none'; },
    on_session_start: () => { queue = []; results = []; history = []; activeCount = 0; if(elResults) elResults.innerHTML = ''; renderState(); },
    on_fact_check_result: (data) => onFactCheckResult(data),
  });
})();
