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

  let enabled     = false;
  let panelOpen   = true;
  let queue       = [];     // { text, speaker }
  let activeCount = 0;      // number of in-flight requests
  let results     = [];     // accumulated result objects

  // ── DOM refs (resolved after DOMContentLoaded) ───────────────────────────
  let elIndicator, elCount, elToggleBtn, elChevron;
  let elBody, elEmpty, elResults, elQueueStatus;

  function $(id) { return document.getElementById(id); }

  document.addEventListener('DOMContentLoaded', () => {
    elIndicator   = $('fc-indicator');
    elCount       = $('fc-count');
    elToggleBtn   = $('fc-toggle-btn');
    elChevron     = $('fc-chevron');
    elBody        = $('fc-body');
    elEmpty       = $('fc-empty');
    elResults     = $('fc-results');
    elQueueStatus = $('fc-queue-status');
    renderState();
  });

  // ── Public ────────────────────────────────────────────────────────────────

  function onTranscript(text, speaker) {
    if (!enabled) return;
    if (!text || text.trim().length < MIN_LENGTH) return;

    if (queue.length >= MAX_QUEUE) queue.shift(); // drop oldest
    queue.push({ text: text.trim(), speaker: speaker || '' });
    updateQueueStatus();
    processNext();
  }

  function toggleEnabled() {
    enabled = !enabled;
    if (!enabled) {
      queue = [];
      // activeCount drains naturally — in-flight requests finish on their own
      updateQueueStatus();
    }
    renderState();
  }

  function togglePanel() {
    panelOpen = !panelOpen;
    renderState();
  }

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
      }
    } catch (e) {
      replacePlaceholder(placeholderId, {
        statement: item.text, speaker: item.speaker,
        type: 'ambiguous', error: e.message
      });
    }
  }

  // ── Rendering ────────────────────────────────────────────────────────────

  function renderState() {
    if (!elBody) return;

    // Panel open/close
    elBody.style.display = panelOpen ? 'block' : 'none';
    if (elChevron) elChevron.textContent = panelOpen ? '\u25BE' : '\u25B8';

    // Enable/disable button
    if (elToggleBtn) {
      elToggleBtn.textContent = enabled ? 'Disable' : 'Enable';
      elToggleBtn.classList.toggle('fc-toggle-btn--active', enabled);
    }

    // Indicator dot
    if (elIndicator) {
      elIndicator.className = 'fc-indicator' + (enabled ? ' fc-indicator--on' : '');
    }

    // Empty state
    if (elEmpty) {
      elEmpty.style.display = (results.length === 0) ? 'block' : 'none';
      if (!enabled && results.length === 0) {
        elEmpty.textContent = 'Fact checker is off. Click Enable to analyze statements as they are transcribed.';
      } else if (enabled && results.length === 0) {
        elEmpty.textContent = 'Listening\u2026 results will appear as statements are transcribed.';
      }
    }
  }

  function updateCount() {
    if (!elCount) return;
    const factCount = results.filter(r => r.type === 'fact_claim' && r.accuracy_score != null).length;
    if (factCount === 0) { elCount.textContent = ''; return; }
    const avg = Math.round(
      results
        .filter(r => r.type === 'fact_claim' && r.accuracy_score != null)
        .reduce((s, r) => s + r.accuracy_score, 0) / factCount
    );
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
      <div class="fc-statement">"\u201C${esc(text)}\u201D"</div>
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
    html += `</div>`;

    html += `<div class="fc-statement">"\u201C${esc(r.statement)}\u201D"</div>`;

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

    return html;
  }

  function trimResults() {
    if (!elResults) return;
    const cards = elResults.querySelectorAll('.fc-card');
    cards.forEach((c, i) => { if (i >= MAX_RESULTS) c.remove(); });
  }

  // ── Helpers ───────────────────────────────────────────────────────────────

  function esc(str) {
    return String(str)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
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

  // Register with plugin system
  window.LinguaTaxi.plugins.register('fact_checker', {
    on_final: (data) => onTranscript(data.text, data.speaker || ''),
    on_enabled: () => { enabled = true; renderState(); },
    on_disabled: () => { enabled = false; queue = []; updateQueueStatus(); renderState(); },
    on_session_start: () => { queue = []; results = []; if(elResults) elResults.innerHTML = ''; renderState(); }
  });
})();
