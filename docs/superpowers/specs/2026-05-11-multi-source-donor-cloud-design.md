# Multi-Source Donor Cloud — Design Spec

**Date:** 2026-05-11
**Status:** Approved
**Plugin:** donor_cloud (v2.0.0 → v3.0.0)

## Problem

The donor cloud plugin only pulls from FEC (federal candidates). Upcoming events feature candidates for Salt Lake County District Attorney, Utah House, and Utah Senate — none of which have FEC data. Only congressional races (e.g., SL County District 1) appear in FEC. We need state, county, and city level campaign finance data, starting with Utah.

## Goals

1. Add Utah campaign finance data (state + municipal) via web scraping of disclosures.utah.gov
2. Modular source architecture so adding new states is just adding a new file
3. Persistent disk cache — donor data survives server restarts, checks for updates on plugin start
4. Event roster — pre-load specific candidates before events via config file + operator panel
5. Toggle between employer-aggregated and individual donor views in the word cloud

## Non-Goals

- Bulk download of entire state databases
- Real-time streaming updates (polling on startup is sufficient)
- Other states beyond Utah (architecture supports it, but only Utah is implemented now)

---

## Architecture

### Source Provider Pattern

Each data source is a Python module in `plugins/donor_cloud/sources/` implementing a common `BaseSource` interface.

```
plugins/donor_cloud/sources/
├── __init__.py        # BaseSource ABC, source registry, shared data models
├── fec.py             # Existing FEC logic, refactored out of routes.py
├── utah.py            # Utah disclosures scraper (state + municipal)
```

### BaseSource Interface

```python
class BaseSource(ABC):
    source_id: str  # e.g. "fec", "utah"

    @abstractmethod
    async def search(self, name: str, year: str) -> list[Candidate]: ...

    @abstractmethod
    async def fetch_contributors(self, candidate_id: str, year: str) -> list[Contributor]: ...

    @abstractmethod
    async def fetch_summary(self, candidate_id: str, year: str) -> FinancialSummary: ...
```

### Shared Data Models

Defined in `sources/__init__.py`, used by all sources:

```python
@dataclass
class Candidate:
    id: str              # Source-specific ID (FEC: "P80000722", Utah: "UT-S-12345")
    name: str            # "First Last"
    party: str           # Full party name
    office: str          # Office title
    state: str           # State code
    source_id: str       # "fec" or "utah"

@dataclass
class Contributor:
    name: str            # Donor name (individual) or employer name
    total: int           # Total donated in dollars
    count: int           # Number of contributions
    type: str            # "employer" or "individual"
    employer_name: str   # Employer (for individual type); empty for employer type

@dataclass
class FinancialSummary:
    candidate: str       # Candidate name
    candidate_id: str
    cycle: str           # Election year
    total_raised: int
    total_spent: int
    cash_on_hand: int
    debt: int
    party: str
    state: str
    office: str
    source_id: str
```

### Route Dispatch

`routes.py` searches all registered sources in parallel for ad-hoc lookups. When a specific candidate is selected (from roster or search results), it dispatches to the correct source based on the candidate's `source_id`.

---

## Persistent Cache Layer

**Module:** `plugins/donor_cloud/cache.py`

**Storage location:** `plugins/donor_cloud/data/cache/`

**File format:** One JSON file per candidate: `{source_id}_{candidate_id}.json`

```json
{
  "candidate": { "id": "...", "name": "...", "party": "...", "office": "...", "state": "...", "source_id": "..." },
  "contributors": [ { "name": "...", "total": 0, "count": 0, "type": "...", "employer_name": "..." } ],
  "summary": { "total_raised": 0, "total_spent": 0, "cash_on_hand": 0, "debt": 0 },
  "last_updated": "2026-05-11T12:00:00Z",
  "source_metadata": {}
}
```

**Lifecycle:**

| Event | Behavior |
|-------|----------|
| Plugin start | Load all cached candidates. Re-fetch roster candidates from source. Re-fetch non-roster candidates only if older than staleness threshold. |
| Search/fetch | Write results to disk cache after successful source fetch. |
| Staleness threshold | Configurable, default 24 hours. Data older than this is re-fetched on next startup or manual refresh. |
| Source unavailable | Return cached data with a `stale: true` flag. Log warning. |

**In-memory cache (existing):** The current 1-hour in-memory cache remains as a hot layer for repeated queries during a live session. The disk cache is the cold-start fallback.

---

## Event Roster System

**Config file:** `plugins/donor_cloud/data/roster.json`

```json
{
  "events": [
    {
      "name": "SL County District 1 Debate",
      "date": "2026-05-25",
      "candidates": [
        { "name": "Jane Smith", "source": "fec", "candidate_id": "H4UT01234" },
        { "name": "John Doe", "source": "utah", "candidate_id": "UT-S-56789" }
      ]
    }
  ]
}
```

### Behavior

- On plugin start, all roster candidates across all events get their data fetched/refreshed via the cache layer (only hits the source if stale or missing).
- Multiple events can be configured simultaneously.
- Past events (date < today) are hidden from the operator panel dropdown but their cached data is retained.

### Operator Panel Management

- "Event Roster" section in the panel below the existing search area.
- Event selector dropdown to pick the active event.
- Add candidates: search for a candidate → click "+" to add to the selected event.
- Remove candidates: click "×" next to a candidate in the roster list.
- Create/rename/delete events.
- All changes write back to `roster.json` immediately.
- "Refresh All" button to force re-fetch all roster candidate data from sources.

### Auto-Trigger Update

The existing hardcoded `KNOWN_CANDIDATES` lookup in `panel.js` is replaced by the roster. When a speaker name matches a roster candidate's name, their donor cloud loads automatically. Matching is case-insensitive, supports "First Last" and "Last, First" formats.

---

## Employer / Individual Toggle

### Toggle Control

A segmented button in the operator panel above the word cloud:
- **By Employer** (default) — aggregates contributions by employer name
- **By Individual** — shows individual donor names

### Data Handling

| Source | Employer View | Individual View |
|--------|--------------|-----------------|
| FEC | `/schedule_a/by_employer/` endpoint (existing) | `/schedule_a/` endpoint (individual itemized contributions) |
| Utah | Aggregate scraped records by employer field | Show individual contribution records directly |

Both views derive from cached data. The toggle changes how `renderCloud()` groups and displays contributors.

### Word Cloud Display

| Mode | Word | Size | Tooltip |
|------|------|------|---------|
| By Employer | Employer name | Total from that employer | "Employer — $X (N contributions)" |
| By Individual | Donor name | Their total donations | "Donor Name — $X — works at Employer" |

### Source Badge

The candidate info area displays a badge showing the data source ("FEC" or "Utah") so the operator knows where the data originated.

---

## Utah Scraper

**Module:** `plugins/donor_cloud/sources/utah.py`

### Two Sub-Scrapers

1. **State-level** — disclosures.utah.gov main site (legislature, statewide offices, DA races). Candidate IDs prefixed `UT-S-`.
2. **Municipal** — disclosures.utah.gov/Municipal/ (county/city candidates). Candidate IDs prefixed `UT-M-`.

Both are behind the same `utah` source_id.

### Technical Approach

- **HTTP client:** `httpx` (async, already in FastAPI ecosystem)
- **HTML parsing:** `BeautifulSoup4`
- **Primary strategy:** Reverse-engineer the AJAX endpoints the site uses for search and data retrieval (ASP.NET MVC pattern — likely POST requests to controller actions)
- **Fallback strategy:** Form submission + HTML table parsing if AJAX endpoints aren't stable
- **Rate limiting:** 1 request/second with exponential backoff retry (max 3 retries)
- **User-Agent:** `LinguaTaxi/1.0 (Campaign Finance Research)`
- **Request timeout:** 15 seconds per request

### Search Flow

1. `search("Jane Smith", "2026")` → POST to disclosures.utah.gov search endpoint with name and year
2. Parse results → extract candidate name, entity ID, office, party, status
3. `fetch_contributors(entity_id, "2026")` → navigate to entity's disclosure page, parse contribution records
4. Each contribution record contains: donor name, employer, amount, date, contribution type (individual/PAC/corporate)

### Error Handling

| Scenario | Behavior |
|----------|----------|
| Site down / unreachable | Return cached data with `stale: true` flag, log warning |
| Scrape structure changed (parsing fails) | Return empty results with error message, log error, don't crash |
| Request timeout (>15s) | Retry up to 3 times with backoff, then fail gracefully |
| Rate limited (429) | Back off and retry after delay |

---

## File Structure (Final)

```
plugins/donor_cloud/
├── manifest.json          # Updated to v3.0.0
├── routes.py              # Refactored: dispatches to source providers
├── cache.py               # NEW: persistent disk cache layer
├── panel.html             # Updated: roster section, toggle, source badge
├── panel.js               # Updated: roster management, toggle, multi-source search
├── panel.css              # Updated: roster styles, toggle styles
├── sources/
│   ├── __init__.py        # NEW: BaseSource ABC, data models, source registry
│   ├── fec.py             # NEW: FEC logic extracted from routes.py
│   └── utah.py            # NEW: Utah disclosures scraper
├── data/
│   ├── cache/             # NEW: persistent candidate cache files
│   └── roster.json        # NEW: event roster config
```

## Dependencies

- `httpx` — async HTTP client for Utah scraper (install via pip, add to requirements.txt)
- `beautifulsoup4` — HTML parsing for Utah scraper (install via pip, add to requirements.txt)

If these are not already in the project's requirements.txt, they must be added. This means a **full build** is required (not a patch) since pip dependencies changed.

---

## Migration

- Existing FEC-only behavior is preserved — the FEC source is just refactored into its own module
- No configuration migration needed — the `cycle` setting in manifest.json still applies globally
- The hardcoded `KNOWN_CANDIDATES` in panel.js is removed in favor of the roster system
- Existing in-memory cache logic moves to `cache.py` and is extended with disk persistence
