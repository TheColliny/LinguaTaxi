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

  const PROVIDER_META = {
    gemini_flash_lite: { name: 'Gemini 3.1 Flash Lite', speed: 'Fast', cost: 'Free', search: 'Google Search', signup: 'aistudio.google.com', category: 'free' },
    cerebras:          { name: 'Cerebras', speed: 'Fast', cost: 'Free', search: 'Brave Search', signup: 'cerebras.ai', category: 'free' },
    mistral:           { name: 'Mistral AI', speed: 'Normal', cost: 'Free', search: 'Brave Search', signup: 'console.mistral.ai', category: 'free' },
    github_models:     { name: 'GitHub Models', speed: 'Normal', cost: 'Free', search: 'Brave Search', signup: 'github.com/marketplace/models', category: 'free' },
    cohere:            { name: 'Cohere', speed: 'Normal', cost: 'Free', search: 'Built-in Search', signup: 'dashboard.cohere.com', category: 'free' },
    openrouter:        { name: 'OpenRouter', speed: 'Normal', cost: 'Free', search: 'Brave Search', signup: 'openrouter.ai', category: 'free' },
    ovhcloud:          { name: 'OVHcloud', speed: 'Slow', cost: 'Free', search: 'Brave Search', signup: 'endpoints.ai.cloud.ovh.net', category: 'free' },
    huggingface:       { name: 'Hugging Face', speed: 'Slow', cost: 'Free', search: 'Brave Search', signup: 'huggingface.co', category: 'free' },
    claude_sonnet:     { name: 'Claude Sonnet 4.6', speed: 'Fast', cost: 'Paid', search: 'Brave Search', signup: 'console.anthropic.com', costInfo: '$3/$15 per 1M tokens', category: 'paid' },
    claude_opus:       { name: 'Claude Opus 4.6', speed: 'Normal', cost: 'Paid', search: 'Brave Search', signup: 'console.anthropic.com', costInfo: '$5/$25 per 1M tokens', category: 'paid' },
    perplexity:        { name: 'Perplexity Sonar Pro', speed: 'Normal', cost: 'Paid', search: 'Built-in Search', signup: 'docs.perplexity.ai', costInfo: '$3/$15 + search', category: 'paid' },
    openai_gpt55:      { name: 'OpenAI GPT-5.5', speed: 'Normal', cost: 'Paid', search: 'Built-in Search', signup: 'platform.openai.com', costInfo: '$5/$30 per 1M tokens', category: 'paid' },
    openai_gpt54_mini: { name: 'OpenAI GPT-5.4-mini', speed: 'Fast', cost: 'Paid', search: 'Built-in Search', signup: 'platform.openai.com', costInfo: '$0.75/$4.50', category: 'paid' },
    openai_gpt54_nano: { name: 'OpenAI GPT-5.4-nano', speed: 'Fast', cost: 'Paid', search: 'Built-in Search', signup: 'platform.openai.com', costInfo: '$0.20/$1.25', category: 'paid' },
    openai_gpt5_nano:  { name: 'OpenAI GPT-5-nano', speed: 'Fast', cost: 'Paid', search: 'Built-in Search', signup: 'platform.openai.com', costInfo: '$0.05/$0.40', category: 'paid' },
    gemini_pro:        { name: 'Google Gemini 3.1 Pro', speed: 'Normal', cost: 'Paid', search: 'Google Search', signup: 'aistudio.google.com', costInfo: '$1.25/$10', category: 'paid' },
  };

  let enabled     = false;
  let panelOpen   = true;
  let mode        = 'auto'; // 'auto' or 'manual'
  let queue       = [];     // { text, speaker }
  let activeCount = 0;      // number of in-flight requests
  let results     = [];     // accumulated result objects
  let isOperatorPage = null; // true = operator (has API), false = audience (WS only)
  let history     = [];     // { text, speaker, ts } — recent transcripts for manual mode
  let providerSettings = {};

  // ── DOM refs (resolved after DOMContentLoaded) ───────────────────────────
  let elCount, elEmpty, elResults, elQueueStatus, elProviderBadge;
  let elModeToggle, elModeAuto, elModeManual, elManualControls;
  let elSettingsPanel, elSettingsChevron, elProviderList, elBraveSection;
  let elAdvancedPanel, elAdvancedChevron, elWeightsWarning;

  function $(id) { return document.getElementById(id); }

  document.addEventListener('DOMContentLoaded', () => {
    elCount           = $('fc-count');
    elEmpty           = $('fc-empty');
    elResults         = $('fc-results');
    elQueueStatus     = $('fc-queue-status');
    elProviderBadge   = $('fc-provider-badge');
    elModeToggle      = $('fc-mode-toggle');
    elModeAuto        = $('fc-mode-auto');
    elModeManual      = $('fc-mode-manual');
    elManualControls  = $('fc-manual-controls');
    elSettingsPanel   = $('fc-settings-panel');
    elSettingsChevron = $('fc-settings-chevron');
    elProviderList    = $('fc-provider-list');
    elBraveSection    = $('fc-brave-section');
    elAdvancedPanel   = $('fc-advanced-panel');
    elAdvancedChevron = $('fc-advanced-chevron');
    elWeightsWarning  = $('fc-weights-warning');
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
    if (isOperatorPage) {
      fetchProviderStatus();
      loadProviderSettings();
    }
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
      const s = await resp.json();

      // Build badge from new status fields (provider_count, providers_enabled, provider_details, brave_key_set)
      // Falls back gracefully to old single-provider fields for backward compat
      let label, keyOk, costClass;

      if (s.provider_count != null) {
        // New multi-provider status
        const count = s.providers_enabled || 0;
        const total = s.provider_count || 0;
        if (count === 0) {
          label = 'No providers';
          keyOk = false;
          costClass = 'nokey';
        } else {
          // Show first enabled provider name or count
          const details = s.provider_details || {};
          const activeNames = Object.entries(details)
            .filter(([, d]) => d.enabled)
            .map(([pid]) => (PROVIDER_META[pid] || {}).name || pid);
          label = activeNames.length === 1
            ? activeNames[0]
            : `${count} provider${count !== 1 ? 's' : ''}`;
          keyOk = true;
          costClass = 'ok';
        }
      } else {
        // Legacy single-provider fields
        const provider = s.provider || 'gemini';
        if (provider === 'claude') {
          label = `Claude ${s.claude_model || 'sonnet'}`;
          keyOk = !!s.claude_key_set;
        } else if (provider === 'groq') {
          label = 'Groq/Llama';
          keyOk = !!(s.groq_key_set && s.brave_key_set);
        } else if (provider === 'magi') {
          label = 'MAGI';
          keyOk = !!(s.claude_key_set || s.gemini_key_set || (s.groq_key_set && s.brave_key_set));
        } else {
          label = 'Gemini Flash';
          keyOk = !!s.gemini_key_set;
        }
        costClass = keyOk ? 'ok' : 'nokey';
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
        if (result.type !== 'opinion') broadcastToAudience(result);
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
    if (data.type === 'ambiguous' || data.type === 'opinion' || data.error) return;

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

  // ── Provider settings ────────────────────────────────────────────────────

  async function loadProviderSettings() {
    try {
      const resp = await fetch('/api/plugins/fact_checker/settings');
      if (!resp.ok) return;
      const data = await resp.json();
      const vals = data.values || {};

      // providers and weights may arrive as JSON strings from form data storage
      let providers = vals.providers || {};
      if (typeof providers === 'string') {
        try { providers = JSON.parse(providers); } catch(e) { providers = {}; }
      }

      let weights = vals.weights || {};
      if (typeof weights === 'string') {
        try { weights = JSON.parse(weights); } catch(e) { weights = {}; }
      }

      providerSettings = { ...vals, providers, weights };
      renderBraveSection();
      renderProviderList();
      renderWeightsWarning();
    } catch(e) { /* ignore */ }
  }

  function renderWeightsWarning() {
    if (!elWeightsWarning) return;
    const weights = providerSettings.weights || {};
    const hasCustom = Object.keys(weights).length > 0;
    elWeightsWarning.style.display = hasCustom ? 'block' : 'none';
  }

  function renderProviderList() {
    if (!elProviderList) return;
    const providers = providerSettings.providers || {};
    const freeProviders = [];
    const paidProviders = [];

    Object.entries(PROVIDER_META).forEach(([pid, meta]) => {
      if (meta.category === 'paid') {
        paidProviders.push([pid, meta]);
      } else {
        freeProviders.push([pid, meta]);
      }
    });

    let html = '';

    if (freeProviders.length > 0) {
      html += '<div class="fc-prov-section-title">Free Providers</div>';
      freeProviders.forEach(([pid, meta]) => {
        const cfg = providers[pid] || {};
        html += buildProviderRow(pid, meta, cfg);
      });
    }

    if (paidProviders.length > 0) {
      html += '<div class="fc-prov-section-title">Paid Providers</div>';
      paidProviders.forEach(([pid, meta]) => {
        const cfg = providers[pid] || {};
        html += buildProviderRow(pid, meta, cfg);
      });
    }

    elProviderList.innerHTML = html;
  }

  function buildProviderRow(pid, meta, cfg) {
    const enabled = !!cfg.enabled;
    const apiKey  = cfg.api_key || '';
    const keySet  = apiKey.length > 0;

    const speedCls = meta.speed === 'Fast'   ? 'fc-tag--fast'
                   : meta.speed === 'Slow'   ? 'fc-tag--slow'
                   : 'fc-tag--normal';
    const costCls  = meta.cost === 'Free'    ? 'fc-tag--free' : 'fc-tag--paid';

    const statusIndicator = enabled
      ? (keySet ? '<span class="fc-prov-ok">&#10003;</span>' : '<span class="fc-prov-warn">&#9888; needs key</span>')
      : '';

    const signupHtml = `<a class="fc-prov-signup" href="https://${esc(meta.signup)}" target="_blank" rel="noopener">${esc(meta.signup)}</a>`;
    const costInfoHtml = meta.costInfo ? `<span class="fc-prov-cost-info">${esc(meta.costInfo)}</span>` : '';

    const keyRowHtml = `
      <div class="fc-prov-keyrow" id="fc-prov-keyrow-${esc(pid)}" style="display:${enabled ? 'flex' : 'none'}">
        <input class="fc-prov-key" type="password" placeholder="API key"
          value="${esc(apiKey)}"
          onchange="window._fcSetProviderKey('${esc(pid)}', this.value)"
          oninput="window._fcSetProviderKey('${esc(pid)}', this.value)"
          autocomplete="off" spellcheck="false" />
        ${signupHtml}
        ${costInfoHtml}
      </div>`;

    return `
      <div class="fc-prov-row" id="fc-prov-row-${esc(pid)}">
        <div class="fc-prov-header">
          <label class="fc-prov-check">
            <input type="checkbox" ${enabled ? 'checked' : ''}
              onchange="window._fcToggleProvider('${esc(pid)}', this.checked)" />
            <span class="fc-prov-name">${esc(meta.name)}</span>
          </label>
          <div class="fc-prov-tags">
            <span class="fc-tag ${speedCls}">${esc(meta.speed)}</span>
            <span class="fc-tag ${costCls}">${esc(meta.cost)}</span>
            <span class="fc-tag">${esc(meta.search)}</span>
            ${statusIndicator}
          </div>
        </div>
        ${keyRowHtml}
      </div>`;
  }

  function renderBraveSection() {
    if (!elBraveSection) return;
    const braveKey = providerSettings.brave_api_key || '';
    const keySet   = braveKey.length > 0;

    // Check if any provider uses Brave Search
    const needsBrave = Object.entries(PROVIDER_META).some(([pid, meta]) => {
      const cfg = (providerSettings.providers || {})[pid] || {};
      return cfg.enabled && meta.search === 'Brave Search';
    });

    const statusHtml = keySet
      ? '<span class="fc-prov-ok">&#10003; Key set</span>'
      : (needsBrave ? '<span class="fc-prov-warn">&#9888; Required for enabled providers</span>' : '');

    elBraveSection.innerHTML = `
      <div class="fc-brave-title">Brave Search API
        ${statusHtml}
      </div>
      <div class="fc-brave-note">Required by Cerebras, Mistral, GitHub Models, OpenRouter, OVHcloud, Hugging Face, Claude providers.</div>
      <div class="fc-brave-keyrow">
        <input class="fc-prov-key" type="password" placeholder="Brave Search API key"
          value="${esc(braveKey)}"
          onchange="window._fcSetBraveKey(this.value)"
          oninput="window._fcSetBraveKey(this.value)"
          autocomplete="off" spellcheck="false" />
        <a class="fc-prov-signup" href="https://brave.com/search/api/" target="_blank" rel="noopener">brave.com/search/api</a>
      </div>`;
  }

  async function saveProviderSettings() {
    try {
      // Read current settings first to preserve non-provider keys (mode, etc.)
      const resp = await fetch('/api/plugins/fact_checker/settings');
      const data = resp.ok ? await resp.json() : { values: {} };
      const vals = { ...(data.values || {}) };

      // Serialize providers and weights as JSON strings for FormData transport
      vals.providers  = JSON.stringify(providerSettings.providers  || {});
      vals.weights    = JSON.stringify(providerSettings.weights    || {});
      vals.brave_api_key = providerSettings.brave_api_key || '';

      const fd = new FormData();
      Object.entries(vals).forEach(([k, v]) => fd.append(k, String(v)));
      fetch('/api/plugins/fact_checker/settings', { method: 'POST', body: fd }).catch(() => {});
    } catch(e) { /* ignore */ }
  }

  window._fcToggleProvider = function(pid, enabled) {
    if (!providerSettings.providers) providerSettings.providers = {};
    if (!providerSettings.providers[pid]) providerSettings.providers[pid] = {};
    providerSettings.providers[pid].enabled = enabled;

    // Show/hide key row
    const keyRow = document.getElementById('fc-prov-keyrow-' + pid);
    if (keyRow) keyRow.style.display = enabled ? 'flex' : 'none';

    // Refresh brave section warning in case Brave-dependent providers changed
    renderBraveSection();

    saveProviderSettings();
    // Refresh badge after short delay to let server process
    setTimeout(fetchProviderStatus, 800);
  };

  window._fcSetProviderKey = function(pid, key) {
    if (!providerSettings.providers) providerSettings.providers = {};
    if (!providerSettings.providers[pid]) providerSettings.providers[pid] = {};
    providerSettings.providers[pid].api_key = key;
    saveProviderSettings();
    setTimeout(fetchProviderStatus, 800);
  };

  window._fcSetBraveKey = function(key) {
    providerSettings.brave_api_key = key;
    renderBraveSection();
    saveProviderSettings();
    setTimeout(fetchProviderStatus, 800);
  };

  window._fcToggleSettings = function() {
    if (!elSettingsPanel || !elSettingsChevron) return;
    const open = elSettingsPanel.style.display === 'none';
    elSettingsPanel.style.display = open ? 'block' : 'none';
    elSettingsChevron.innerHTML   = open ? '&#9660;' : '&#9654;';
    if (open) loadProviderSettings(); // refresh on expand
  };

  window._fcToggleAdvanced = function() {
    if (!elAdvancedPanel || !elAdvancedChevron) return;
    const open = elAdvancedPanel.style.display === 'none';
    elAdvancedPanel.style.display = open ? 'block' : 'none';
    elAdvancedChevron.innerHTML   = open ? '&#9660;' : '&#9654;';
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
