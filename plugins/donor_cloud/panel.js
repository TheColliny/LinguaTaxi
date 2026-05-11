/**
 * LinguaTaxi — Donor Cloud Plugin Panel
 * Multi-source: FEC (federal) + Utah (state/county)
 * Roster management, employer/individual toggle, source badges.
 */

(function() {
  let currentCID = '';
  let currentSource = '';
  let currentView = 'employer';
  let roster = { events: [] };
  let activeEventIdx = -1;

  const CLOUD_COLORS = [
    '#4FC3F7', '#81C784', '#FFD54F', '#FF8A80', '#CE93D8',
    '#FFAB91', '#80DEEA', '#C5E1A5', '#F48FB1', '#90CAF9',
    '#A5D6A7', '#FFF176', '#EF9A9A', '#B39DDB',
  ];

  let elSearch, elSearchBtn, elCycle, elSearchResults;
  let elCandidateInfo, elCloudContainer, elEmpty, elLegend;
  let elRosterSection, elRosterSelect, elRosterCandidates;
  let elRosterNewEvent, elRosterRefresh, elRosterDeleteEvent;
  let elToggleEmployer, elToggleIndividual;

  function $(id) { return document.getElementById(id); }

  document.addEventListener('DOMContentLoaded', () => {
    elSearch          = $('dc-search');
    elSearchBtn       = $('dc-search-btn');
    elCycle           = $('dc-cycle');
    elSearchResults   = $('dc-search-results');
    elCandidateInfo   = $('dc-candidate-info');
    elCloudContainer  = $('dc-cloud-container');
    elEmpty           = $('dc-empty');
    elLegend          = $('dc-legend');
    elRosterSection   = $('dc-roster-section');
    elRosterSelect    = $('dc-roster-event-select');
    elRosterCandidates= $('dc-roster-candidates');
    elRosterNewEvent  = $('dc-roster-new-event');
    elRosterRefresh   = $('dc-roster-refresh');
    elRosterDeleteEvent = $('dc-roster-delete-event');
    elToggleEmployer  = $('dc-toggle-employer');
    elToggleIndividual= $('dc-toggle-individual');

    if (elSearchBtn) elSearchBtn.addEventListener('click', doSearch);
    if (elSearch) elSearch.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') doSearch();
    });
    if (elCycle) elCycle.addEventListener('change', () => {
      if (currentCID) loadContributors(currentCID, currentSource);
    });

    // Toggle handlers
    if (elToggleEmployer) elToggleEmployer.addEventListener('click', () => setView('employer'));
    if (elToggleIndividual) elToggleIndividual.addEventListener('click', () => setView('individual'));

    // Roster handlers
    if (elRosterNewEvent) elRosterNewEvent.addEventListener('click', createEvent);
    if (elRosterRefresh) elRosterRefresh.addEventListener('click', refreshRoster);
    if (elRosterDeleteEvent) elRosterDeleteEvent.addEventListener('click', deleteEvent);
    if (elRosterSelect) elRosterSelect.addEventListener('change', onEventSelected);

    loadRoster();
  });

  // ── View toggle ──

  function setView(view) {
    currentView = view;
    if (elToggleEmployer) elToggleEmployer.classList.toggle('dc-toggle-btn--active', view === 'employer');
    if (elToggleIndividual) elToggleIndividual.classList.toggle('dc-toggle-btn--active', view === 'individual');
    if (currentCID) loadContributors(currentCID, currentSource);
  }

  // ── Search ──

  let _searchPending = false;

  function doSearch() {
    if (_searchPending) return;
    _searchPending = true;
    setTimeout(() => { _searchPending = false; }, 300);
    if (!elSearch) return;
    const query = elSearch.value.trim();
    if (!query) return;
    searchAPI(query);
  }

  async function searchAPI(name) {
    if (!elSearchResults) return;
    elSearchResults.innerHTML = '<div class="dc-searching">Searching all sources...</div>';

    try {
      const resp = await fetch(`/api/donor-cloud/search?name=${encodeURIComponent(name)}`);
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({ detail: resp.statusText }));
        elSearchResults.innerHTML = `<div class="dc-error">${esc(err.detail || 'Search failed')}</div>`;
        return;
      }
      const data = await resp.json();

      if (data.candidates && data.candidates.length > 0) {
        elSearchResults.innerHTML = data.candidates.map(c => {
          const srcClass = c.source_id === 'utah' ? 'dc-roster-source--utah' : 'dc-roster-source--fec';
          const srcLabel = (c.source_id || 'fec').toUpperCase();
          return `<div class="dc-result-item">
            <span class="dc-result-name" onclick="window._dcSelect('${esc(c.id)}','${esc(c.source_id)}')">${esc(c.name)}</span>
            <span class="dc-result-meta">${esc(c.party)} — ${esc(c.state)}</span>
            <span class="dc-roster-source ${srcClass}">${srcLabel}</span>
            <span class="dc-result-add" onclick="window._dcAddToRoster('${esc(c.id)}','${esc(c.source_id)}','${esc(c.name)}')" title="Add to roster">+</span>
          </div>`;
        }).join('');
      } else {
        elSearchResults.innerHTML =
          `<div class="dc-no-results">No results for "${esc(name)}".</div>`;
      }
    } catch(e) {
      elSearchResults.innerHTML = `<div class="dc-error">${esc(e.message)}</div>`;
    }
  }

  window._dcSelect = function(cid, source) {
    loadContributors(cid, source);
  };

  window._dcAddToRoster = async function(cid, source, name) {
    if (activeEventIdx < 0) {
      alert('Create an event first, then add candidates.');
      return;
    }
    try {
      const resp = await fetch('/api/donor-cloud/roster/candidate', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          action: 'add',
          event_index: activeEventIdx,
          candidate: { name: name, source: source, candidate_id: cid },
        }),
      });
      const data = await resp.json();
      if (data.roster) {
        roster = data.roster;
        renderRosterCandidates();
      }
    } catch(e) {
      console.error('Add to roster failed:', e);
    }
  };

  // ── Load contributors ──

  async function loadContributors(cid, source) {
    currentCID = cid;
    currentSource = source || 'fec';
    if (elSearchResults) elSearchResults.innerHTML = '';
    if (elCloudContainer) elCloudContainer.innerHTML = '<div class="dc-loading">Loading donor data...</div>';

    const cycle = elCycle ? elCycle.value : '2024';

    try {
      const url = `/api/donor-cloud/contributors?cid=${encodeURIComponent(cid)}&source=${encodeURIComponent(currentSource)}&cycle=${cycle}&view=${currentView}`;
      const resp = await fetch(url);
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({ detail: resp.statusText }));
        showError(err.detail || 'Failed to load contributors');
        return;
      }
      const data = await resp.json();

      if (elCandidateInfo && data.candidate) {
        const srcClass = data.source === 'utah' ? 'dc-source-badge--utah' : 'dc-source-badge--fec';
        const srcLabel = (data.source || 'fec').toUpperCase();
        const staleTag = data.stale ? ' <span style="color:#FFD54F;font-size:9px">(cached)</span>' : '';
        elCandidateInfo.innerHTML =
          `<div class="dc-cand-name">${esc(data.candidate)}<span class="dc-source-badge ${srcClass}">${srcLabel}</span>${staleTag}</div>
           <div class="dc-cand-meta">${esc(data.cid)} — ${esc(data.cycle)} cycle</div>`;
        elCandidateInfo.style.display = 'block';
      }

      if (!data.contributors || data.contributors.length === 0) {
        showError('No contributor data found for this candidate/cycle.');
        return;
      }

      renderCloud(data.contributors, data.source);

    } catch(e) {
      showError(e.message);
    }
  }

  function renderCloud(contributors, source) {
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

      let tooltip;
      if (c.type === 'individual' && c.employer_name) {
        tooltip = `${c.name}\nTotal: ${amount}\nEmployer: ${c.employer_name}`;
      } else {
        tooltip = `${c.name}\nTotal: ${amount}\nContributions: ${c.count || '\u2014'}`;
      }

      return `<span class="dc-word" style="font-size:${fontSize.toFixed(1)}px;color:${color}"
                    title="${esc(tooltip)}"
                    data-amount="${c.total}">${esc(c.name)}</span>`;
    });

    shuffle(words);

    elCloudContainer.innerHTML = '<div class="dc-cloud">' + words.join('') + '</div>';

    if (elLegend) {
      const totalAll = contributors.reduce((s, c) => s + c.total, 0);
      const srcLabel = (source || 'fec').toUpperCase();
      const viewLabel = currentView === 'individual' ? 'individual donors' : 'employers';
      elLegend.innerHTML =
        `<span class="dc-legend-item">Top ${contributors.length} ${viewLabel}</span>
         <span class="dc-legend-item">Total: ${formatMoney(totalAll)}</span>
         <span class="dc-legend-item">Source: ${esc(srcLabel)}</span>`;
      elLegend.style.display = 'flex';
    }
  }

  // ── Roster management ──

  async function loadRoster() {
    try {
      const resp = await fetch('/api/donor-cloud/roster');
      roster = await resp.json();
    } catch(e) {
      roster = { events: [] };
    }
    renderRosterEvents();
  }

  function renderRosterEvents() {
    if (!elRosterSelect) return;
    const today = new Date().toISOString().split('T')[0];
    const visibleEvents = roster.events
      .map((ev, i) => ({ ...ev, _idx: i }))
      .filter(ev => !ev.date || ev.date >= today || ev._idx === activeEventIdx);

    elRosterSelect.innerHTML = visibleEvents.length === 0
      ? '<option value="-1">No events</option>'
      : visibleEvents.map(ev =>
          `<option value="${ev._idx}"${ev._idx === activeEventIdx ? ' selected' : ''}>${esc(ev.name)}</option>`
        ).join('');

    if (activeEventIdx < 0 && visibleEvents.length > 0) {
      activeEventIdx = visibleEvents[0]._idx;
      elRosterSelect.value = activeEventIdx;
    }
    renderRosterCandidates();
  }

  function renderRosterCandidates() {
    if (!elRosterCandidates) return;
    if (activeEventIdx < 0 || activeEventIdx >= roster.events.length) {
      elRosterCandidates.innerHTML = '';
      return;
    }
    const event = roster.events[activeEventIdx];
    if (!event.candidates || event.candidates.length === 0) {
      elRosterCandidates.innerHTML = '<div class="dc-no-results">No candidates. Search and click + to add.</div>';
      return;
    }
    elRosterCandidates.innerHTML = event.candidates.map(c => {
      const srcClass = c.source === 'utah' ? 'dc-roster-source--utah' : 'dc-roster-source--fec';
      const srcLabel = (c.source || 'fec').toUpperCase();
      return `<div class="dc-roster-item" onclick="window._dcSelect('${esc(c.candidate_id)}','${esc(c.source)}')">
        <span class="dc-roster-name">${esc(c.name)}</span>
        <span class="dc-roster-source ${srcClass}">${srcLabel}</span>
        <span class="dc-roster-remove" onclick="event.stopPropagation();window._dcRemoveFromRoster('${esc(c.candidate_id)}','${esc(c.source)}')" title="Remove">&times;</span>
      </div>`;
    }).join('');
  }

  function onEventSelected() {
    activeEventIdx = parseInt(elRosterSelect.value, 10);
    renderRosterCandidates();
    if (activeEventIdx >= 0) {
      fetch('/api/donor-cloud/roster/load-event', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ event_index: activeEventIdx }),
      }).catch(() => {});
    }
  }

  async function createEvent() {
    const name = prompt('Event name:');
    if (!name) return;
    const date = prompt('Event date (YYYY-MM-DD, optional):', '');
    try {
      const resp = await fetch('/api/donor-cloud/roster/event', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ action: 'create', name: name, date: date || '' }),
      });
      const data = await resp.json();
      if (data.roster) {
        roster = data.roster;
        activeEventIdx = roster.events.length - 1;
        renderRosterEvents();
      }
    } catch(e) {
      console.error('Create event failed:', e);
    }
  }

  async function deleteEvent() {
    if (activeEventIdx < 0) return;
    const ev = roster.events[activeEventIdx];
    if (!confirm(`Delete event "${ev.name}"?`)) return;
    try {
      const resp = await fetch('/api/donor-cloud/roster/event', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ action: 'delete', index: activeEventIdx }),
      });
      const data = await resp.json();
      if (data.roster) {
        roster = data.roster;
        activeEventIdx = roster.events.length > 0 ? 0 : -1;
        renderRosterEvents();
      }
    } catch(e) {
      console.error('Delete event failed:', e);
    }
  }

  async function refreshRoster() {
    if (activeEventIdx < 0) return;
    if (elRosterRefresh) elRosterRefresh.textContent = '...';
    try {
      const resp = await fetch('/api/donor-cloud/roster/refresh', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ event_index: activeEventIdx }),
      });
      const data = await resp.json();
      if (elRosterRefresh) elRosterRefresh.innerHTML = '&#8635;';
    } catch(e) {
      console.error('Refresh failed:', e);
      if (elRosterRefresh) elRosterRefresh.innerHTML = '&#8635;';
    }
  }

  window._dcRemoveFromRoster = async function(cid, source) {
    if (activeEventIdx < 0) return;
    try {
      const resp = await fetch('/api/donor-cloud/roster/candidate', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          action: 'remove',
          event_index: activeEventIdx,
          candidate_id: cid,
          source: source,
        }),
      });
      const data = await resp.json();
      if (data.roster) {
        roster = data.roster;
        renderRosterCandidates();
      }
    } catch(e) {
      console.error('Remove from roster failed:', e);
    }
  };

  // ── Helpers ──

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

  // ── Plugin registration ──

  let _dcEnabled = true;
  window.LinguaTaxi.plugins.register('donor_cloud', {
    on_enabled: () => { _dcEnabled = true; loadRoster(); },
    on_disabled: () => { _dcEnabled = false; },
    on_auto_speaker_change: (data) => {
      if (!_dcEnabled) return;
      const speakerName = (data.speaker || '').toLowerCase().trim();
      if (!speakerName) return;

      // Match against roster candidates
      for (const ev of roster.events) {
        for (const c of (ev.candidates || [])) {
          const rosterName = (c.name || '').toLowerCase().trim();
          // Match "First Last" or "Last, First"
          const parts = rosterName.split(/\s+/);
          const reversed = parts.length >= 2
            ? (parts[parts.length - 1] + ', ' + parts.slice(0, -1).join(' ')).toLowerCase()
            : '';
          if (speakerName === rosterName || speakerName === reversed) {
            if (c.candidate_id !== currentCID || c.source !== currentSource) {
              loadContributors(c.candidate_id, c.source);
              if (elSearch) elSearch.value = data.speaker;
            }
            return;
          }
        }
      }
    },
    on_session_start: () => {
      currentCID = '';
      currentSource = '';
      if (elCloudContainer) elCloudContainer.innerHTML = '';
      if (elCandidateInfo) { elCandidateInfo.innerHTML = ''; elCandidateInfo.style.display = 'none'; }
      if (elLegend) { elLegend.innerHTML = ''; elLegend.style.display = 'none'; }
      if (elEmpty) elEmpty.style.display = 'block';
      loadRoster();
    }
  });
})();
