/**
 * LinguaTaxi — Donor Cloud Plugin Panel
 * Fetches candidate donor data from FEC.gov via plugin API
 * and renders a word cloud sized by donation amount.
 */

(function() {
  let currentCID = '';

  const KNOWN_CANDIDATES = {
    'joe biden':        'P80000722',
    'biden':            'P80000722',
    'donald trump':     'P80001571',
    'trump':            'P80001571',
    'kamala harris':    'P80007381',
    'harris':           'P80007381',
    'bernie sanders':   'P60007168',
    'sanders':          'P60007168',
    'mitch mcconnell':  'S2KY00012',
    'mcconnell':        'S2KY00012',
    'nancy pelosi':     'H8CA05035',
    'pelosi':           'H8CA05035',
    'chuck schumer':    'S8NY00082',
    'schumer':          'S8NY00082',
    'ted cruz':         'S4TX00107',
    'cruz':             'S4TX00107',
    'marco rubio':      'S0FL00338',
    'rubio':            'S0FL00338',
    'aoc':              'H8NY15148',
    'alexandria ocasio-cortez': 'H8NY15148',
    'josh hawley':      'S8MO00172',
    'hawley':           'S8MO00172',
    'elizabeth warren': 'S2MA00170',
    'warren':           'S2MA00170',
    'jd vance':         'S2OH00378',
    'vance':            'S2OH00378',
  };

  const CLOUD_COLORS = [
    '#4FC3F7', '#81C784', '#FFD54F', '#FF8A80', '#CE93D8',
    '#FFAB91', '#80DEEA', '#C5E1A5', '#F48FB1', '#90CAF9',
    '#A5D6A7', '#FFF176', '#EF9A9A', '#B39DDB',
  ];

  let elSearch, elSearchBtn, elCycle, elSearchResults;
  let elCandidateInfo, elCloudContainer, elEmpty, elLegend;

  function $(id) { return document.getElementById(id); }

  document.addEventListener('DOMContentLoaded', () => {
    elSearch        = $('dc-search');
    elSearchBtn     = $('dc-search-btn');
    elCycle         = $('dc-cycle');
    elSearchResults = $('dc-search-results');
    elCandidateInfo = $('dc-candidate-info');
    elCloudContainer= $('dc-cloud-container');
    elEmpty         = $('dc-empty');
    elLegend        = $('dc-legend');

    if (elSearchBtn) elSearchBtn.addEventListener('click', doSearch);
    if (elSearch) elSearch.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') doSearch();
    });
    if (elCycle) elCycle.addEventListener('change', () => {
      if (currentCID) loadContributors(currentCID);
    });
  });

  let _searchPending = false;

  function doSearch() {
    if (_searchPending) return;
    _searchPending = true;
    setTimeout(() => { _searchPending = false; }, 300);
    if (!elSearch) return;
    const query = elSearch.value.trim();
    if (!query) return;

    // FEC IDs start with H, S, or P followed by alphanumeric
    if (/^[HSP]\w{8,}$/i.test(query)) {
      loadContributors(query.toUpperCase());
      return;
    }

    const known = KNOWN_CANDIDATES[query.toLowerCase()];
    if (known) {
      loadContributors(known);
      return;
    }

    searchAPI(query);
  }

  async function searchAPI(name) {
    if (!elSearchResults) return;
    elSearchResults.innerHTML = '<div class="dc-searching">Searching FEC...</div>';

    try {
      const resp = await fetch(`/api/donor-cloud/search?name=${encodeURIComponent(name)}`);
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({ detail: resp.statusText }));
        elSearchResults.innerHTML = `<div class="dc-error">${esc(err.detail || 'Search failed')}</div>`;
        return;
      }
      const data = await resp.json();

      if (data.candidates && data.candidates.length > 0) {
        elSearchResults.innerHTML = data.candidates.map(c =>
          `<div class="dc-result-item" onclick="window._dcSelectCandidate('${esc(c.cid)}')">
            <span class="dc-result-name">${esc(c.name)}</span>
            <span class="dc-result-meta">${esc(c.party)} — ${esc(c.state)}</span>
            <span class="dc-result-cid">${esc(c.cid)}</span>
          </div>`
        ).join('');
      } else {
        elSearchResults.innerHTML =
          `<div class="dc-no-results">No results for "${esc(name)}". Try a full name or FEC ID.</div>`;
      }
    } catch(e) {
      elSearchResults.innerHTML = `<div class="dc-error">${esc(e.message)}</div>`;
    }
  }

  window._dcSelectCandidate = function(cid) {
    loadContributors(cid);
  };

  async function loadContributors(cid) {
    currentCID = cid;
    if (elSearchResults) elSearchResults.innerHTML = '';
    if (elCloudContainer) elCloudContainer.innerHTML = '<div class="dc-loading">Loading donor data from FEC...</div>';

    const cycle = elCycle ? elCycle.value : '2024';

    try {
      const resp = await fetch(`/api/donor-cloud/contributors?cid=${encodeURIComponent(cid)}&cycle=${cycle}`);
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({ detail: resp.statusText }));
        showError(err.detail || 'Failed to load contributors');
        return;
      }
      const data = await resp.json();

      if (elCandidateInfo && data.candidate) {
        elCandidateInfo.innerHTML =
          `<div class="dc-cand-name">${esc(data.candidate)}</div>
           <div class="dc-cand-meta">${esc(data.cid)} — ${esc(data.cycle)} cycle</div>`;
        elCandidateInfo.style.display = 'block';
      }

      if (!data.contributors || data.contributors.length === 0) {
        showError('No contributor data found for this candidate/cycle.');
        return;
      }

      renderCloud(data.contributors);

    } catch(e) {
      showError(e.message);
    }
  }

  function renderCloud(contributors) {
    if (!elCloudContainer) return;
    if (elEmpty) elEmpty.style.display = 'none';

    const amounts = contributors.map(c => c.total);
    const maxAmt = Math.max(...amounts);
    const minAmt = Math.min(...amounts);
    const range = maxAmt - minAmt || 1;

    const MIN_FONT = 12;
    const MAX_FONT = 48;

    const words = contributors.map((c, i) => {
      const ratio = (c.total - minAmt) / range;
      const fontSize = MIN_FONT + ratio * (MAX_FONT - MIN_FONT);
      const color = CLOUD_COLORS[i % CLOUD_COLORS.length];
      const amount = formatMoney(c.total);

      return `<span class="dc-word" style="font-size:${fontSize.toFixed(1)}px;color:${color}"
                    title="${esc(c.name)}\nTotal: ${amount}\nContributions: ${c.count || '—'}"
                    data-amount="${c.total}">${esc(c.name)}</span>`;
    });

    shuffle(words);

    elCloudContainer.innerHTML = '<div class="dc-cloud">' + words.join('') + '</div>';

    if (elLegend) {
      const totalAll = contributors.reduce((s, c) => s + c.total, 0);
      elLegend.innerHTML =
        `<span class="dc-legend-item">Top ${contributors.length} donors</span>
         <span class="dc-legend-item">Total: ${formatMoney(totalAll)}</span>
         <span class="dc-legend-item">Source: FEC.gov</span>`;
      elLegend.style.display = 'flex';
    }
  }

  function showError(msg) {
    if (elCloudContainer) {
      elCloudContainer.innerHTML = `<div class="dc-error">${esc(msg)}</div>`;
    }
  }

  function formatMoney(n) {
    if (n >= 1_000_000) return '$' + (n / 1_000_000).toFixed(1) + 'M';
    if (n >= 1_000) return '$' + (n / 1_000).toFixed(0) + 'K';
    return '$' + n.toLocaleString();
  }

  function shuffle(arr) {
    for (let i = arr.length - 1; i > 0; i--) {
      const j = Math.floor(Math.random() * (i + 1));
      [arr[i], arr[j]] = [arr[j], arr[i]];
    }
  }

  function esc(str) {
    return String(str)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  let _dcEnabled = true;
  window.LinguaTaxi.plugins.register('donor_cloud', {
    on_enabled: () => { _dcEnabled = true; },
    on_disabled: () => { _dcEnabled = false; },
    on_auto_speaker_change: (data) => {
      if (!_dcEnabled) return;
      const name = (data.speaker || '').toLowerCase();
      const cid = KNOWN_CANDIDATES[name];
      if (cid && cid !== currentCID) {
        loadContributors(cid);
        if (elSearch) elSearch.value = data.speaker;
      }
    },
    on_session_start: () => {
      currentCID = '';
      if (elCloudContainer) elCloudContainer.innerHTML = '';
      if (elCandidateInfo) { elCandidateInfo.innerHTML = ''; elCandidateInfo.style.display = 'none'; }
      if (elLegend) { elLegend.innerHTML = ''; elLegend.style.display = 'none'; }
      if (elEmpty) elEmpty.style.display = 'block';
    }
  });
})();
