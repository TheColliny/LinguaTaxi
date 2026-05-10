/**
 * LinguaTaxi — Polls Checker Plugin Panel
 * Auto-detects opinion claims from transcripts and searches for real polling data.
 */

(function() {
  const MIN_LENGTH     = 25;
  const MAX_QUEUE      = 8;
  const MAX_RESULTS    = 12;
  const MAX_CONCURRENT = 2;

  // Lightweight client-side pre-filter for opinion claims.
  // Server (routes.py _OPINION_PATTERNS) does a more exhaustive check — this just
  // avoids sending obvious non-claims to the API. Client regex is intentionally
  // broader (one false positive costs only an API call; one false negative misses a claim).
  const OPINION_RE = /\b(?:americans?|people|voters?|public|citizens?|majority|most|everybody|nobody)\b.*\b(?:want|think|believe|support|oppose|favor|prefer|agree|demand|feel|knows)\b|\b(?:polls?|surveys?|polling)\b.*\b(?:show|say|indicate|suggest|find)\b|\b(?:popular|unpopular|approval|disapproval)\b|\b(?:percent|%)\b.*\b(?:americans?|people|voters?|support|oppose|approve)\b/i;

  let enabled     = false;
  let queue       = [];
  let activeCount = 0;
  let results     = [];

  let elCount, elEmpty, elResults, elQueueStatus, elProviderBadge;

  function $(id) { return document.getElementById(id); }

  document.addEventListener('DOMContentLoaded', () => {
    elCount         = $('pc-count');
    elEmpty         = $('pc-empty');
    elResults       = $('pc-results');
    elQueueStatus   = $('pc-queue-status');
    elProviderBadge = $('pc-provider-badge');
    renderState();
    fetchStatus();
  });

  // ── Transcript handler ──

  function onTranscript(text, speaker) {
    if (!enabled) return;
    if (!text || text.trim().length < MIN_LENGTH) return;
    // Pre-filter: only queue if it looks like an opinion claim
    if (!OPINION_RE.test(text)) return;

    if (queue.length >= MAX_QUEUE) queue.shift();
    queue.push({ text: text.trim(), speaker: speaker || '' });
    updateQueueStatus();
    processNext();
  }

  // ── Processing ──

  function processNext() {
    while (activeCount < MAX_CONCURRENT && queue.length > 0) {
      const item = queue.shift();
      activeCount++;
      updateQueueStatus();
      runCheck(item).finally(() => {
        activeCount--;
        updateQueueStatus();
        processNext();
      });
    }
  }

  async function runCheck(item) {
    const phId = 'pc-ph-' + Date.now() + '-' + Math.random().toString(36).slice(2);
    appendPlaceholder(phId, item.text, item.speaker);

    try {
      const res = await fetch('/api/polls/check', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ statement: item.text, speaker: item.speaker })
      });

      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: res.statusText }));
        replacePlaceholder(phId, {
          statement: item.text, speaker: item.speaker,
          is_opinion_claim: false, error: err.detail || 'Server error'
        });
        return;
      }

      const data = await res.json();

      // If not an opinion claim, remove the placeholder quietly
      if (!data.is_opinion_claim && !data.error) {
        removePlaceholder(phId);
        return;
      }

      const result = { statement: item.text, speaker: item.speaker, ...data };
      results.unshift(result);
      if (results.length > MAX_RESULTS) results.pop();
      replacePlaceholder(phId, result);
      updateCount();
    } catch (e) {
      replacePlaceholder(phId, {
        statement: item.text, speaker: item.speaker,
        is_opinion_claim: false, error: e.message
      });
    }
  }

  // ── Status ──

  async function fetchStatus() {
    if (!elProviderBadge) return;
    try {
      const resp = await fetch('/api/polls/status');
      if (!resp.ok) return;
      const s = await resp.json();
      const provider = s.provider || 'gemini';
      const label = provider === 'claude' ? 'Claude' : 'Gemini Flash';
      const cost = provider === 'claude' ? 'paid' : 'free';
      const keyOk = provider === 'claude' ? s.claude_key_set : s.gemini_key_set;
      const cls = keyOk ? 'pc-prov--ok' : 'pc-prov--nokey';
      elProviderBadge.innerHTML =
        `<span class="pc-prov ${cls}">${esc(label)}</span>` +
        `<span class="pc-prov-cost">${cost}</span>` +
        (!keyOk ? '<span class="pc-prov-warn">no API key</span>' : '');
    } catch(e) { /* ignore */ }
  }

  // ── Rendering ──

  function renderState() {
    if (elEmpty) {
      // Count actual rendered cards, not just results[] (handles error cards + placeholders)
      const domCount = elResults ? elResults.querySelectorAll('.pc-card').length : 0;
      elEmpty.style.display = (domCount === 0) ? 'block' : 'none';
      if (!enabled && domCount === 0) {
        elEmpty.textContent = 'Polls checker is off. Enable to auto-check opinion claims against real polling data.';
      } else if (enabled && domCount === 0) {
        elEmpty.textContent = 'Listening for opinion claims\u2026 polling data will appear when relevant statements are detected.';
      }
    }
  }

  function updateCount() {
    if (!elCount) return;
    const n = results.filter(r => r.is_opinion_claim && r.polls && r.polls.length > 0).length;
    elCount.textContent = n > 0 ? `${n} polled` : '';
  }

  function updateQueueStatus() {
    if (!elQueueStatus) return;
    if (activeCount === 0 && queue.length === 0) { elQueueStatus.textContent = ''; return; }
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
    el.className = 'pc-card pc-card--loading';
    el.innerHTML = `
      <div class="pc-card-meta">
        ${speaker ? `<span class="pc-speaker">${esc(speaker)}</span>` : ''}
        <span class="pc-badge pc-badge--checking">searching polls\u2026</span>
      </div>
      <div class="pc-statement">\u201C${esc(text)}\u201D</div>
    `;
    elResults.insertBefore(el, elResults.firstChild);
    trimResults();
  }

  function replacePlaceholder(id, result) {
    const el = document.getElementById(id);
    if (!el) return;
    el.className = 'pc-card';
    el.innerHTML = buildCardHTML(result);
  }

  function removePlaceholder(id) {
    const el = document.getElementById(id);
    if (el) el.remove();
  }

  function buildCardHTML(r) {
    let html = '';

    // Meta row
    html += '<div class="pc-card-meta">';
    if (r.speaker) html += `<span class="pc-speaker">${esc(r.speaker)}</span>`;
    if (r.claim_vs_data) {
      const cls = r.claim_vs_data === 'supported' ? 'pc-badge--supported'
                : r.claim_vs_data === 'contradicted' ? 'pc-badge--contradicted'
                : r.claim_vs_data === 'mixed' ? 'pc-badge--mixed'
                : 'pc-badge--nodata';
      const label = r.claim_vs_data === 'no_data' ? 'No data' : r.claim_vs_data;
      html += `<span class="pc-badge ${cls}">${esc(label)}</span>`;
    }
    if (r.error) html += `<span class="pc-badge pc-badge--error">error</span>`;
    if (r.provider) {
      const provCls = r.provider === 'claude' ? 'pc-badge--claude' : 'pc-badge--gemini';
      html += `<span class="pc-badge ${provCls}" style="margin-left:auto">${esc(r.provider)}</span>`;
    }
    html += '</div>';

    // Statement
    html += `<div class="pc-statement">\u201C${esc(r.statement)}\u201D</div>`;

    // Topic
    if (r.topic) {
      html += `<div class="pc-topic">Topic: ${esc(r.topic)}</div>`;
    }

    // Summary
    if (r.summary) {
      html += `<div class="pc-summary">${esc(r.summary)}</div>`;
    }

    // Poll cards
    if (r.polls && r.polls.length > 0) {
      html += '<div class="pc-polls">';
      r.polls.forEach(poll => {
        html += buildPollHTML(poll);
      });
      html += '</div>';
    }

    // Error
    if (r.error) {
      html += `<div class="pc-error">${esc(r.error)}</div>`;
    }

    return html;
  }

  function buildPollHTML(poll) {
    const rating = poll.rating || {};
    const ratingCls = rating.rating === 'A+' ? 'pc-rating--gold'
                    : rating.rating === 'A' ? 'pc-rating--good'
                    : rating.rating === 'B' ? 'pc-rating--mid'
                    : rating.rating === 'C' ? 'pc-rating--low'
                    : 'pc-rating--unknown';

    let html = `<div class="pc-poll ${ratingCls}">`;

    // Header: org + rating + date
    html += '<div class="pc-poll-header">';
    html += `<span class="pc-poll-org">${esc(poll.organization || 'Unknown')}</span>`;
    if (rating.rating) {
      html += `<span class="pc-poll-rating">${esc(rating.rating)}</span>`;
    }
    if (poll.date) {
      html += `<span class="pc-poll-date">${esc(poll.date)}</span>`;
    }
    html += '</div>';

    // Question
    if (poll.question) {
      html += `<div class="pc-poll-question">${esc(poll.question)}</div>`;
    }

    // Results bars
    if (poll.results && typeof poll.results === 'object') {
      html += '<div class="pc-poll-results">';
      // Coerce percentages to numbers — LLM may return "45", "45%", or 45
      const entries = Object.entries(poll.results)
        .map(([k, v]) => [k, toNum(v)])
        .filter(([, v]) => !isNaN(v))
        .sort((a, b) => b[1] - a[1]);
      const maxVal = Math.max(...entries.map(e => e[1]), 1);
      entries.forEach(([label, pct], i) => {
        const width = Math.max(2, (pct / maxVal) * 100);
        const colors = ['#4FC3F7', '#FF8A80', '#FFD54F', '#81C784', '#CE93D8'];
        const color = colors[i % colors.length];
        html += `<div class="pc-bar-row">
          <span class="pc-bar-label">${esc(label)}</span>
          <div class="pc-bar-track">
            <div class="pc-bar-fill" style="width:${width}%;background:${color}"></div>
          </div>
          <span class="pc-bar-pct">${Math.round(pct)}%</span>
        </div>`;
      });
      html += '</div>';
    }

    // Footer: methodology + sample + source
    html += '<div class="pc-poll-footer">';
    if (poll.sample_size) html += `<span class="pc-poll-meta">n=${poll.sample_size.toLocaleString()}</span>`;
    if (poll.methodology) html += `<span class="pc-poll-meta">${esc(poll.methodology)}</span>`;
    if (poll.url && /^https?:\/\//i.test(poll.url)) {
      html += `<a href="${esc(poll.url)}" target="_blank" rel="noopener" class="pc-poll-link">source</a>`;
    }
    html += '</div>';

    html += '</div>';
    return html;
  }

  function trimResults() {
    if (!elResults) return;
    // Exclude loading placeholders from the trim count — they're not permanent results
    const cards = elResults.querySelectorAll('.pc-card:not(.pc-card--loading)');
    cards.forEach((c, i) => { if (i >= MAX_RESULTS) c.remove(); });
  }

  // ── Helpers ──

  function esc(str) {
    return String(str)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  // Coerce a percentage value (number, "45", "45%", "45.5 %") to a number
  function toNum(v) {
    if (typeof v === 'number') return v;
    if (v == null) return NaN;
    return parseFloat(String(v).replace(/[^\d.\-]/g, ''));
  }

  // ── Plugin registration ──
  window.LinguaTaxi.plugins.register('polls_checker', {
    on_final: (data) => onTranscript(data.text, data.speaker || ''),
    on_enabled: () => { enabled = true; renderState(); },
    on_disabled: () => { enabled = false; queue = []; updateQueueStatus(); renderState(); },
    on_session_start: () => { queue = []; results = []; activeCount = 0; if(elResults) elResults.innerHTML = ''; renderState(); }
  });
})();
