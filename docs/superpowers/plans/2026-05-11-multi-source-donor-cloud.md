# Multi-Source Donor Cloud Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Utah state/county campaign finance data to the donor cloud plugin via a modular source provider architecture with persistent caching, event roster, and employer/individual toggle.

**Architecture:** Each data source (FEC, Utah) becomes a module in `sources/` implementing a `BaseSource` ABC. A shared `cache.py` handles disk persistence. Routes dispatch to the correct source. The panel gains roster management, view toggle, and source badges.

**Tech Stack:** Python/FastAPI, httpx (async HTTP), BeautifulSoup4 (HTML parsing), JSON disk cache, vanilla JS panel

---

## File Map

| File | Action | Responsibility |
|------|--------|---------------|
| `plugins/donor_cloud/sources/__init__.py` | CREATE | BaseSource ABC, Candidate/Contributor/FinancialSummary dataclasses, source registry |
| `plugins/donor_cloud/sources/fec.py` | CREATE | FEC source — extracted from routes.py |
| `plugins/donor_cloud/sources/utah.py` | CREATE | Utah disclosures scraper (state + municipal) |
| `plugins/donor_cloud/cache.py` | CREATE | Disk-backed JSON cache with staleness checking |
| `plugins/donor_cloud/routes.py` | MODIFY | Refactor to dispatch to source providers, add roster + cache endpoints |
| `plugins/donor_cloud/panel.html` | MODIFY | Add roster section, view toggle, source badge |
| `plugins/donor_cloud/panel.js` | MODIFY | Roster management, toggle, multi-source search, auto-trigger from roster |
| `plugins/donor_cloud/panel.css` | MODIFY | Roster styles, toggle styles, source badge styles |
| `plugins/donor_cloud/manifest.json` | MODIFY | Bump to v3.0.0, add staleness_hours setting, update hooks |
| `plugins/donor_cloud/data/roster.json` | CREATE | Default empty roster |
| `requirements.txt` | MODIFY | Add httpx, beautifulsoup4 |
| `plugins/donor_cloud/tests/test_sources.py` | CREATE | Tests for data models and source registry |
| `plugins/donor_cloud/tests/test_cache.py` | CREATE | Tests for cache read/write/staleness |
| `plugins/donor_cloud/tests/test_fec_source.py` | CREATE | Tests for FEC source (mocked HTTP) |

---

### Task 1: Data Models and BaseSource ABC

**Files:**
- Create: `plugins/donor_cloud/sources/__init__.py`
- Create: `plugins/donor_cloud/tests/__init__.py`
- Create: `plugins/donor_cloud/tests/test_sources.py`

- [ ] **Step 1: Write the failing test**

Create `plugins/donor_cloud/tests/__init__.py` (empty file) and `plugins/donor_cloud/tests/test_sources.py`:

```python
"""Tests for donor_cloud source data models and registry."""
import os
import sys
import importlib.util as _ilu

_PLUGIN_DIR = os.path.join(os.path.dirname(__file__), "..")
_SOURCES_DIR = os.path.join(_PLUGIN_DIR, "sources")

_spec = _ilu.spec_from_file_location(
    "donor_cloud_sources",
    os.path.join(_SOURCES_DIR, "__init__.py"),
)
_mod = _ilu.module_from_spec(_spec)
sys.modules["donor_cloud_sources"] = _mod
_spec.loader.exec_module(_mod)

Candidate = _mod.Candidate
Contributor = _mod.Contributor
FinancialSummary = _mod.FinancialSummary
SOURCE_REGISTRY = _mod.SOURCE_REGISTRY
register_source = _mod.register_source
get_source = _mod.get_source
get_all_sources = _mod.get_all_sources


def test_candidate_dataclass():
    c = Candidate(
        id="P80000722", name="Joe Biden", party="Democratic",
        office="President", state="US", source_id="fec",
    )
    assert c.id == "P80000722"
    assert c.name == "Joe Biden"
    assert c.source_id == "fec"


def test_contributor_dataclass():
    c = Contributor(
        name="Google", total=50000, count=120,
        type="employer", employer_name="",
    )
    assert c.total == 50000
    assert c.type == "employer"


def test_contributor_individual():
    c = Contributor(
        name="Jane Smith", total=2800, count=1,
        type="individual", employer_name="Google",
    )
    assert c.type == "individual"
    assert c.employer_name == "Google"


def test_financial_summary_dataclass():
    s = FinancialSummary(
        candidate="Joe Biden", candidate_id="P80000722", cycle="2024",
        total_raised=1000000, total_spent=800000, cash_on_hand=200000,
        debt=0, party="Democratic", state="US", office="President",
        source_id="fec",
    )
    assert s.total_raised == 1000000
    assert s.source_id == "fec"


def test_source_registry_empty():
    assert isinstance(SOURCE_REGISTRY, dict)


def test_get_source_missing():
    result = get_source("nonexistent")
    assert result is None
```

- [ ] **Step 2: Create the sources directory**

```bash
mkdir -p plugins/donor_cloud/sources
mkdir -p plugins/donor_cloud/tests
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m pytest plugins/donor_cloud/tests/test_sources.py -v`
Expected: FAIL — `__init__.py` does not exist yet

- [ ] **Step 4: Write the implementation**

Create `plugins/donor_cloud/sources/__init__.py`:

```python
"""
Donor Cloud — Source Provider Framework
Base classes, shared data models, and source registry.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, asdict

__all__ = [
    "BaseSource", "Candidate", "Contributor", "FinancialSummary",
    "SOURCE_REGISTRY", "register_source", "get_source", "get_all_sources",
]


@dataclass
class Candidate:
    id: str
    name: str
    party: str
    office: str
    state: str
    source_id: str

    def to_dict(self):
        return asdict(self)


@dataclass
class Contributor:
    name: str
    total: int
    count: int
    type: str
    employer_name: str

    def to_dict(self):
        return asdict(self)


@dataclass
class FinancialSummary:
    candidate: str
    candidate_id: str
    cycle: str
    total_raised: int
    total_spent: int
    cash_on_hand: int
    debt: int
    party: str
    state: str
    office: str
    source_id: str

    def to_dict(self):
        return asdict(self)


class BaseSource(ABC):
    source_id: str = ""
    display_name: str = ""

    @abstractmethod
    async def search(self, name: str, year: str) -> list[Candidate]:
        ...

    @abstractmethod
    async def fetch_contributors(
        self, candidate_id: str, year: str, view: str = "employer"
    ) -> list[Contributor]:
        ...

    @abstractmethod
    async def fetch_summary(
        self, candidate_id: str, year: str
    ) -> FinancialSummary | None:
        ...


SOURCE_REGISTRY: dict[str, BaseSource] = {}


def register_source(source: BaseSource):
    SOURCE_REGISTRY[source.source_id] = source


def get_source(source_id: str) -> BaseSource | None:
    return SOURCE_REGISTRY.get(source_id)


def get_all_sources() -> list[BaseSource]:
    return list(SOURCE_REGISTRY.values())
```

Create empty `plugins/donor_cloud/tests/__init__.py`:

```python
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest plugins/donor_cloud/tests/test_sources.py -v`
Expected: All 6 tests PASS

- [ ] **Step 6: Commit**

```bash
git add plugins/donor_cloud/sources/__init__.py plugins/donor_cloud/tests/__init__.py plugins/donor_cloud/tests/test_sources.py
git commit -m "[feat] add donor cloud source provider data models and registry"
```

---

### Task 2: Persistent Disk Cache

**Files:**
- Create: `plugins/donor_cloud/cache.py`
- Create: `plugins/donor_cloud/tests/test_cache.py`
- Create: `plugins/donor_cloud/data/cache/.gitkeep`

- [ ] **Step 1: Write the failing test**

Create `plugins/donor_cloud/tests/test_cache.py`:

```python
"""Tests for donor cloud disk cache."""
import json
import os
import sys
import tempfile
import importlib.util as _ilu

_PLUGIN_DIR = os.path.join(os.path.dirname(__file__), "..")

# Load sources first (cache depends on data models)
_SOURCES_DIR = os.path.join(_PLUGIN_DIR, "sources")
_src_spec = _ilu.spec_from_file_location(
    "donor_cloud_sources", os.path.join(_SOURCES_DIR, "__init__.py"))
_src_mod = _ilu.module_from_spec(_src_spec)
sys.modules["donor_cloud_sources"] = _src_mod
_src_spec.loader.exec_module(_src_mod)

# Load cache module
_cache_spec = _ilu.spec_from_file_location(
    "donor_cloud_cache", os.path.join(_PLUGIN_DIR, "cache.py"))
_cache_mod = _ilu.module_from_spec(_cache_spec)
sys.modules["donor_cloud_cache"] = _cache_mod
_cache_spec.loader.exec_module(_cache_mod)

DiskCache = _cache_mod.DiskCache
Candidate = _src_mod.Candidate
Contributor = _src_mod.Contributor
FinancialSummary = _src_mod.FinancialSummary


def _make_candidate():
    return Candidate(
        id="P80000722", name="Joe Biden", party="Democratic",
        office="President", state="US", source_id="fec",
    )


def _make_contributors():
    return [
        Contributor(name="Google", total=50000, count=120,
                    type="employer", employer_name=""),
        Contributor(name="Microsoft", total=30000, count=80,
                    type="employer", employer_name=""),
    ]


def _make_summary():
    return FinancialSummary(
        candidate="Joe Biden", candidate_id="P80000722", cycle="2024",
        total_raised=1000000, total_spent=800000, cash_on_hand=200000,
        debt=0, party="Democratic", state="US", office="President",
        source_id="fec",
    )


def test_save_and_load():
    with tempfile.TemporaryDirectory() as tmpdir:
        cache = DiskCache(tmpdir)
        cand = _make_candidate()
        contribs = _make_contributors()
        summary = _make_summary()
        cache.save(cand, contribs, summary)
        entry = cache.load("fec", "P80000722")
        assert entry is not None
        assert entry["candidate"]["name"] == "Joe Biden"
        assert len(entry["contributors"]) == 2
        assert entry["summary"]["total_raised"] == 1000000
        assert "last_updated" in entry


def test_load_missing():
    with tempfile.TemporaryDirectory() as tmpdir:
        cache = DiskCache(tmpdir)
        entry = cache.load("fec", "DOESNOTEXIST")
        assert entry is None


def test_is_stale_default():
    with tempfile.TemporaryDirectory() as tmpdir:
        cache = DiskCache(tmpdir, staleness_hours=0)
        cand = _make_candidate()
        cache.save(cand, _make_contributors(), _make_summary())
        assert cache.is_stale("fec", "P80000722") is True


def test_is_stale_fresh():
    with tempfile.TemporaryDirectory() as tmpdir:
        cache = DiskCache(tmpdir, staleness_hours=24)
        cand = _make_candidate()
        cache.save(cand, _make_contributors(), _make_summary())
        assert cache.is_stale("fec", "P80000722") is False


def test_load_all():
    with tempfile.TemporaryDirectory() as tmpdir:
        cache = DiskCache(tmpdir)
        c1 = Candidate(id="P1", name="A", party="X", office="Y",
                        state="UT", source_id="fec")
        c2 = Candidate(id="P2", name="B", party="X", office="Y",
                        state="UT", source_id="utah")
        cache.save(c1, [], None)
        cache.save(c2, [], None)
        all_entries = cache.load_all()
        assert len(all_entries) == 2


def test_cache_filename_sanitized():
    with tempfile.TemporaryDirectory() as tmpdir:
        cache = DiskCache(tmpdir)
        cand = Candidate(id="UT-S-12345", name="Test", party="X",
                         office="Y", state="UT", source_id="utah")
        cache.save(cand, [], None)
        entry = cache.load("utah", "UT-S-12345")
        assert entry is not None
```

- [ ] **Step 2: Create the data/cache directory**

```bash
mkdir -p plugins/donor_cloud/data/cache
touch plugins/donor_cloud/data/cache/.gitkeep
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m pytest plugins/donor_cloud/tests/test_cache.py -v`
Expected: FAIL — `cache.py` does not exist yet

- [ ] **Step 4: Write the implementation**

Create `plugins/donor_cloud/cache.py`:

```python
"""
Donor Cloud — Persistent Disk Cache
Stores candidate donor data as JSON files on disk.
"""

import json
import logging
import os
import re
import threading
from datetime import datetime, timezone

log = logging.getLogger("livecaption")

# Import data models — at runtime these are available from the loaded sources module.
# When loaded via importlib in routes.py, we import relatively from the sources package.
import importlib.util
import sys

_sources_dir = os.path.join(os.path.dirname(__file__), "sources")
if "donor_cloud_sources" not in sys.modules:
    _spec = importlib.util.spec_from_file_location(
        "donor_cloud_sources", os.path.join(_sources_dir, "__init__.py"))
    _mod = importlib.util.module_from_spec(_spec)
    sys.modules["donor_cloud_sources"] = _mod
    _spec.loader.exec_module(_mod)

from donor_cloud_sources import Candidate, Contributor, FinancialSummary


def _safe_filename(source_id: str, candidate_id: str) -> str:
    raw = f"{source_id}_{candidate_id}"
    return re.sub(r'[^\w\-.]', '_', raw) + ".json"


class DiskCache:
    def __init__(self, cache_dir: str, staleness_hours: float = 24.0):
        self._dir = cache_dir
        self._staleness_hours = staleness_hours
        self._lock = threading.Lock()
        os.makedirs(self._dir, exist_ok=True)

    def _path(self, source_id: str, candidate_id: str) -> str:
        return os.path.join(self._dir, _safe_filename(source_id, candidate_id))

    def save(self, candidate: Candidate, contributors: list[Contributor],
             summary: FinancialSummary | None) -> None:
        entry = {
            "candidate": candidate.to_dict(),
            "contributors": [c.to_dict() for c in contributors],
            "summary": summary.to_dict() if summary else None,
            "last_updated": datetime.now(timezone.utc).isoformat(),
        }
        path = self._path(candidate.source_id, candidate.id)
        with self._lock:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(entry, f, indent=2)

    def load(self, source_id: str, candidate_id: str) -> dict | None:
        path = self._path(source_id, candidate_id)
        if not os.path.exists(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            log.warning(f"Cache read error for {source_id}/{candidate_id}: {e}")
            return None

    def is_stale(self, source_id: str, candidate_id: str) -> bool:
        entry = self.load(source_id, candidate_id)
        if entry is None:
            return True
        last = entry.get("last_updated")
        if not last:
            return True
        try:
            updated = datetime.fromisoformat(last)
            age_hours = (datetime.now(timezone.utc) - updated).total_seconds() / 3600
            return age_hours >= self._staleness_hours
        except (ValueError, TypeError):
            return True

    def load_all(self) -> list[dict]:
        results = []
        if not os.path.isdir(self._dir):
            return results
        for fname in os.listdir(self._dir):
            if not fname.endswith(".json"):
                continue
            path = os.path.join(self._dir, fname)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    results.append(json.load(f))
            except (json.JSONDecodeError, OSError):
                continue
        return results
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest plugins/donor_cloud/tests/test_cache.py -v`
Expected: All 6 tests PASS

- [ ] **Step 6: Commit**

```bash
git add plugins/donor_cloud/cache.py plugins/donor_cloud/tests/test_cache.py plugins/donor_cloud/data/cache/.gitkeep
git commit -m "[feat] add donor cloud persistent disk cache"
```

---

### Task 3: FEC Source Provider (Extract from routes.py)

**Files:**
- Create: `plugins/donor_cloud/sources/fec.py`
- Create: `plugins/donor_cloud/tests/test_fec_source.py`

- [ ] **Step 1: Write the failing test**

Create `plugins/donor_cloud/tests/test_fec_source.py`:

```python
"""Tests for FEC source provider."""
import os
import sys
import importlib.util as _ilu
from unittest.mock import patch, MagicMock

_PLUGIN_DIR = os.path.join(os.path.dirname(__file__), "..")
_SOURCES_DIR = os.path.join(_PLUGIN_DIR, "sources")

# Load sources framework
_src_spec = _ilu.spec_from_file_location(
    "donor_cloud_sources", os.path.join(_SOURCES_DIR, "__init__.py"))
_src_mod = _ilu.module_from_spec(_src_spec)
sys.modules["donor_cloud_sources"] = _src_mod
_src_spec.loader.exec_module(_src_mod)

# Load FEC source
_fec_spec = _ilu.spec_from_file_location(
    "donor_cloud_fec", os.path.join(_SOURCES_DIR, "fec.py"))
_fec_mod = _ilu.module_from_spec(_fec_spec)
sys.modules["donor_cloud_fec"] = _fec_mod
_fec_spec.loader.exec_module(_fec_mod)

FECSource = _fec_mod.FECSource
Candidate = _src_mod.Candidate
Contributor = _src_mod.Contributor


def test_fec_source_id():
    src = FECSource()
    assert src.source_id == "fec"
    assert src.display_name == "FEC"


def test_format_name_last_first():
    src = FECSource()
    assert src._format_name("BIDEN, JOSEPH R JR") == "Joseph R Jr Biden"


def test_format_name_normal():
    src = FECSource()
    assert src._format_name("JOE BIDEN") == "Joe Biden"


def test_skip_employers_filtered():
    src = FECSource()
    assert src._should_skip_employer("RETIRED") is True
    assert src._should_skip_employer("N/A") is True
    assert src._should_skip_employer("Google") is False


import asyncio

def test_search_returns_candidates():
    src = FECSource()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {
        "results": [{
            "candidate_id": "P80000722",
            "name": "BIDEN, JOSEPH R JR",
            "party_full": "Democratic Party",
            "state": "US",
            "office_full": "President",
        }]
    }
    with patch("requests.get", return_value=mock_resp):
        results = asyncio.run(src.search("biden", "2024"))
    assert len(results) == 1
    assert results[0].id == "P80000722"
    assert results[0].source_id == "fec"
    assert isinstance(results[0], Candidate)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest plugins/donor_cloud/tests/test_fec_source.py -v`
Expected: FAIL — `fec.py` does not exist yet

- [ ] **Step 3: Write the implementation**

Create `plugins/donor_cloud/sources/fec.py`:

```python
"""
Donor Cloud — FEC Source Provider
Fetches candidate/donor data from the FEC (Federal Election Commission) public API.
"""

import asyncio
import logging
import threading
import time

import requests

log = logging.getLogger("livecaption")

import importlib.util
import os
import sys

_sources_dir = os.path.dirname(__file__)
if "donor_cloud_sources" not in sys.modules:
    _spec = importlib.util.spec_from_file_location(
        "donor_cloud_sources", os.path.join(_sources_dir, "__init__.py"))
    _mod = importlib.util.module_from_spec(_spec)
    sys.modules["donor_cloud_sources"] = _mod
    _spec.loader.exec_module(_mod)

from donor_cloud_sources import (
    BaseSource, Candidate, Contributor, FinancialSummary, register_source,
)

_FEC_BASE = "https://api.open.fec.gov/v1"
_FEC_KEY = "DEMO_KEY"

_cache = {}
_cache_lock = threading.Lock()
_CACHE_TTL = 3600
_CACHE_TTL_FAIL = 60

_SKIP_EMPLOYERS = frozenset({
    "N/A", "NOT EMPLOYED", "SELF-EMPLOYED", "SELF EMPLOYED", "SELF",
    "RETIRED", "NONE", "NULL", "", "NOT APPLICABLE",
    "INFORMATION REQUESTED", "INFORMATION REQUESTED PER BEST EFFORTS",
    "HOMEMAKER", "STUDENT", "UNEMPLOYED", "DISABLED",
})


class FECSource(BaseSource):
    source_id = "fec"
    display_name = "FEC"

    def _fec_request(self, cache_key: str, path: str, params: dict) -> dict | None:
        now = time.monotonic()
        with _cache_lock:
            expired = [k for k, (_, ts) in _cache.items() if now - ts >= _CACHE_TTL]
            for k in expired:
                del _cache[k]
            if cache_key in _cache:
                data, ts = _cache[cache_key]
                ttl = _CACHE_TTL_FAIL if data is None else _CACHE_TTL
                if now - ts < ttl:
                    return data

        params["api_key"] = _FEC_KEY
        url = _FEC_BASE + path
        try:
            resp = requests.get(url, params=params, timeout=20)
            resp.raise_for_status()
            data = resp.json()
            with _cache_lock:
                _cache[cache_key] = (data, now)
            return data
        except Exception as e:
            log.error(f"FEC API error ({path}): {e}")
            with _cache_lock:
                _cache[cache_key] = (None, now)
            return None

    def _format_name(self, raw: str) -> str:
        if "," in raw:
            parts = raw.split(",", 1)
            return (parts[1].strip() + " " + parts[0].strip()).title()
        return raw.title()

    def _should_skip_employer(self, employer: str) -> bool:
        return employer.strip().upper() in _SKIP_EMPLOYERS

    def _get_principal_committee(self, candidate_id: str) -> str | None:
        data = self._fec_request(
            f"committee:{candidate_id}",
            f"/candidate/{candidate_id}/committees/",
            {"designation": "P", "per_page": 1},
        )
        if not data:
            return None
        results = data.get("results", [])
        return results[0].get("committee_id") if results else None

    async def search(self, name: str, year: str) -> list[Candidate]:
        def _do():
            data = self._fec_request(
                f"search:{name.lower()}",
                "/candidates/search/",
                {"q": name, "sort": "-receipts", "per_page": 15,
                 "is_active_candidate": "true"},
            )
            if not data:
                return []
            results = []
            for c in data.get("results", []):
                cid = c.get("candidate_id", "")
                raw_name = c.get("name", "")
                if not cid or not raw_name:
                    continue
                results.append(Candidate(
                    id=cid,
                    name=self._format_name(raw_name),
                    party=c.get("party_full") or c.get("party") or "",
                    state=c.get("state") or "",
                    office=c.get("office_full") or c.get("office") or "",
                    source_id="fec",
                ))
            return results

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, _do)

    async def fetch_contributors(
        self, candidate_id: str, year: str, view: str = "employer"
    ) -> list[Contributor]:
        def _do():
            committee_id = self._get_principal_committee(candidate_id)
            if not committee_id:
                return []

            if view == "individual":
                return self._fetch_individual_contributors(committee_id, year)
            return self._fetch_employer_contributors(committee_id, year)

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, _do)

    def _fetch_employer_contributors(self, committee_id: str, year: str) -> list[Contributor]:
        data = self._fec_request(
            f"contrib_emp:{committee_id}:{year}",
            "/schedules/schedule_a/by_employer/",
            {"committee_id": committee_id, "cycle": year,
             "sort": "-total", "per_page": 50},
        )
        if not data:
            return []
        contributors = []
        for entry in data.get("results", []):
            employer = (entry.get("employer") or "").strip().upper()
            if self._should_skip_employer(employer):
                continue
            total = entry.get("total", 0)
            count = entry.get("count", 0)
            if total > 0 and employer:
                contributors.append(Contributor(
                    name=employer.title(), total=int(total), count=count,
                    type="employer", employer_name="",
                ))
            if len(contributors) >= 20:
                break
        return contributors

    def _fetch_individual_contributors(self, committee_id: str, year: str) -> list[Contributor]:
        data = self._fec_request(
            f"contrib_ind:{committee_id}:{year}",
            "/schedules/schedule_a/",
            {"committee_id": committee_id, "two_year_transaction_period": year,
             "sort": "-contribution_receipt_amount", "per_page": 50,
             "is_individual": "true"},
        )
        if not data:
            return []
        contributors = []
        seen = {}
        for entry in data.get("results", []):
            name = (entry.get("contributor_name") or "").strip()
            if not name:
                continue
            employer = (entry.get("contributor_employer") or "").strip()
            amount = int(entry.get("contribution_receipt_amount", 0))
            if name in seen:
                seen[name]["total"] += amount
                seen[name]["count"] += 1
            else:
                seen[name] = {"total": amount, "count": 1, "employer": employer}
        for name, info in sorted(seen.items(), key=lambda x: -x[1]["total"]):
            contributors.append(Contributor(
                name=self._format_name(name),
                total=info["total"], count=info["count"],
                type="individual", employer_name=info["employer"].title(),
            ))
            if len(contributors) >= 20:
                break
        return contributors

    async def fetch_summary(self, candidate_id: str, year: str) -> FinancialSummary | None:
        def _do():
            data = self._fec_request(
                f"totals:{candidate_id}:{year}",
                f"/candidate/{candidate_id}/totals/",
                {"cycle": year, "per_page": 1},
            )
            if not data:
                return None
            results = data.get("results", [])
            if not results:
                return None
            s = results[0]

            cand_data = self._fec_request(
                f"cand:{candidate_id}", f"/candidate/{candidate_id}/", {})
            cand_name = party = state = office = ""
            if cand_data and cand_data.get("results"):
                c = cand_data["results"][0]
                cand_name = self._format_name(c.get("name", ""))
                party = c.get("party_full") or c.get("party") or ""
                state = c.get("state") or ""
                office = c.get("office_full") or ""

            return FinancialSummary(
                candidate=cand_name, candidate_id=candidate_id, cycle=year,
                total_raised=int(s.get("receipts", 0)),
                total_spent=int(s.get("disbursements", 0)),
                cash_on_hand=int(s.get("cash_on_hand_end_period", 0)),
                debt=int(s.get("debts_owed_by_committee", 0)),
                party=party, state=state, office=office, source_id="fec",
            )

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, _do)

    def clear_cache(self):
        with _cache_lock:
            _cache.clear()


register_source(FECSource())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest plugins/donor_cloud/tests/test_fec_source.py -v`
Expected: All 5 tests PASS

- [ ] **Step 5: Commit**

```bash
git add plugins/donor_cloud/sources/fec.py plugins/donor_cloud/tests/test_fec_source.py
git commit -m "[feat] extract FEC source provider from routes.py"
```

---

### Task 4: Utah Scraper Source Provider

**Files:**
- Create: `plugins/donor_cloud/sources/utah.py`

This task requires reverse-engineering the disclosures.utah.gov AJAX endpoints. The implementer should:
1. Open `https://disclosures.utah.gov/Search/PublicSearch` in a browser
2. Open DevTools → Network tab
3. Search for a candidate name and observe the XHR/Fetch requests
4. Note the URLs, HTTP methods, request/response formats

The implementation below uses the expected ASP.NET MVC patterns. If the actual endpoints differ, adapt the URLs accordingly.

- [ ] **Step 1: Write the implementation**

Create `plugins/donor_cloud/sources/utah.py`:

```python
"""
Donor Cloud — Utah Source Provider
Scrapes campaign finance data from disclosures.utah.gov (state + municipal).
"""

import asyncio
import logging
import re
import time

log = logging.getLogger("livecaption")

import importlib.util
import os
import sys

_sources_dir = os.path.dirname(__file__)
if "donor_cloud_sources" not in sys.modules:
    _spec = importlib.util.spec_from_file_location(
        "donor_cloud_sources", os.path.join(_sources_dir, "__init__.py"))
    _mod = importlib.util.module_from_spec(_spec)
    sys.modules["donor_cloud_sources"] = _mod
    _spec.loader.exec_module(_mod)

from donor_cloud_sources import (
    BaseSource, Candidate, Contributor, FinancialSummary, register_source,
)

try:
    import httpx
    from bs4 import BeautifulSoup
    _SCRAPER_AVAILABLE = True
except ImportError:
    _SCRAPER_AVAILABLE = False
    log.warning("Utah scraper disabled: install httpx and beautifulsoup4")

_BASE_URL = "https://disclosures.utah.gov"
_USER_AGENT = "LinguaTaxi/1.0 (Campaign Finance Research)"
_TIMEOUT = 15.0
_RATE_LIMIT_DELAY = 1.0
_MAX_RETRIES = 3

_last_request_time = 0.0


async def _rate_limited_get(client: "httpx.AsyncClient", url: str,
                            **kwargs) -> "httpx.Response":
    global _last_request_time
    now = time.monotonic()
    wait = _RATE_LIMIT_DELAY - (now - _last_request_time)
    if wait > 0:
        await asyncio.sleep(wait)

    last_err = None
    for attempt in range(_MAX_RETRIES):
        try:
            _last_request_time = time.monotonic()
            resp = await client.get(url, timeout=_TIMEOUT, **kwargs)
            if resp.status_code == 429:
                delay = 2 ** (attempt + 1)
                log.warning(f"Utah rate limited, backing off {delay}s")
                await asyncio.sleep(delay)
                continue
            resp.raise_for_status()
            return resp
        except Exception as e:
            last_err = e
            if attempt < _MAX_RETRIES - 1:
                await asyncio.sleep(2 ** attempt)
    raise last_err


async def _rate_limited_post(client: "httpx.AsyncClient", url: str,
                             **kwargs) -> "httpx.Response":
    global _last_request_time
    now = time.monotonic()
    wait = _RATE_LIMIT_DELAY - (now - _last_request_time)
    if wait > 0:
        await asyncio.sleep(wait)

    last_err = None
    for attempt in range(_MAX_RETRIES):
        try:
            _last_request_time = time.monotonic()
            resp = await client.post(url, timeout=_TIMEOUT, **kwargs)
            if resp.status_code == 429:
                delay = 2 ** (attempt + 1)
                log.warning(f"Utah rate limited, backing off {delay}s")
                await asyncio.sleep(delay)
                continue
            resp.raise_for_status()
            return resp
        except Exception as e:
            last_err = e
            if attempt < _MAX_RETRIES - 1:
                await asyncio.sleep(2 ** attempt)
    raise last_err


def _parse_money(text: str) -> int:
    cleaned = re.sub(r'[^\d.]', '', text.strip())
    try:
        return int(float(cleaned))
    except (ValueError, TypeError):
        return 0


class UtahSource(BaseSource):
    source_id = "utah"
    display_name = "Utah"

    async def search(self, name: str, year: str) -> list[Candidate]:
        if not _SCRAPER_AVAILABLE:
            return []
        candidates = []
        try:
            async with httpx.AsyncClient(
                headers={"User-Agent": _USER_AGENT},
                follow_redirects=True,
            ) as client:
                # State-level search
                state_results = await self._search_state(client, name, year)
                candidates.extend(state_results)

                # Municipal search
                muni_results = await self._search_municipal(client, name, year)
                candidates.extend(muni_results)

        except Exception as e:
            log.error(f"Utah search error: {e}")
        return candidates

    async def _search_state(self, client, name: str, year: str) -> list[Candidate]:
        results = []
        try:
            # Attempt AJAX endpoint first (ASP.NET MVC pattern)
            resp = await _rate_limited_post(
                client, f"{_BASE_URL}/Search/GetEntityList",
                data={"searchName": name, "entityType": "CAN",
                      "reportYear": year},
            )
            # If JSON response, parse structured data
            if "application/json" in resp.headers.get("content-type", ""):
                data = resp.json()
                for item in (data if isinstance(data, list) else data.get("results", [])):
                    entity_id = str(item.get("EntityId", item.get("entityId", "")))
                    if not entity_id:
                        continue
                    results.append(Candidate(
                        id=f"UT-S-{entity_id}",
                        name=item.get("EntityName", item.get("entityName", "")).strip(),
                        party=item.get("Party", item.get("party", "")),
                        office=item.get("Office", item.get("office", "")),
                        state="UT",
                        source_id="utah",
                    ))
                return results

            # Fallback: parse HTML table
            results = self._parse_search_html(resp.text, prefix="UT-S-")

        except Exception as e:
            log.error(f"Utah state search failed: {e}")
            # Try HTML form fallback
            try:
                resp = await _rate_limited_get(
                    client,
                    f"{_BASE_URL}/Search/PublicSearch",
                    params={"name": name},
                )
                results = self._parse_search_html(resp.text, prefix="UT-S-")
            except Exception as e2:
                log.error(f"Utah state search HTML fallback failed: {e2}")
        return results

    async def _search_municipal(self, client, name: str, year: str) -> list[Candidate]:
        results = []
        try:
            resp = await _rate_limited_post(
                client, f"{_BASE_URL}/Municipal/GetEntityList",
                data={"searchName": name, "reportYear": year},
            )
            if "application/json" in resp.headers.get("content-type", ""):
                data = resp.json()
                for item in (data if isinstance(data, list) else data.get("results", [])):
                    entity_id = str(item.get("EntityId", item.get("entityId", "")))
                    if not entity_id:
                        continue
                    results.append(Candidate(
                        id=f"UT-M-{entity_id}",
                        name=item.get("EntityName", item.get("entityName", "")).strip(),
                        party=item.get("Party", item.get("party", "")),
                        office=item.get("Office", item.get("office", "")),
                        state="UT",
                        source_id="utah",
                    ))
                return results

            results = self._parse_search_html(resp.text, prefix="UT-M-")
        except Exception as e:
            log.error(f"Utah municipal search failed: {e}")
        return results

    def _parse_search_html(self, html: str, prefix: str) -> list[Candidate]:
        candidates = []
        try:
            soup = BeautifulSoup(html, "html.parser")
            rows = soup.select("table tr, .entity-row, .search-result")
            for row in rows:
                cells = row.find_all("td") if row.name == "tr" else []
                link = row.find("a", href=True)
                if link and cells and len(cells) >= 2:
                    href = link.get("href", "")
                    entity_match = re.search(r'/(\d+)', href)
                    entity_id = entity_match.group(1) if entity_match else ""
                    if not entity_id:
                        continue
                    name = cells[0].get_text(strip=True)
                    party = cells[2].get_text(strip=True) if len(cells) > 2 else ""
                    office = cells[1].get_text(strip=True) if len(cells) > 1 else ""
                    candidates.append(Candidate(
                        id=f"{prefix}{entity_id}", name=name, party=party,
                        office=office, state="UT", source_id="utah",
                    ))
        except Exception as e:
            log.error(f"Utah HTML parse error: {e}")
        return candidates

    async def fetch_contributors(
        self, candidate_id: str, year: str, view: str = "employer"
    ) -> list[Contributor]:
        if not _SCRAPER_AVAILABLE:
            return []

        entity_id = candidate_id.replace("UT-S-", "").replace("UT-M-", "")
        is_municipal = candidate_id.startswith("UT-M-")

        try:
            async with httpx.AsyncClient(
                headers={"User-Agent": _USER_AGENT},
                follow_redirects=True,
            ) as client:
                base = f"{_BASE_URL}/Municipal" if is_municipal else _BASE_URL

                # Try AJAX contribution endpoint
                try:
                    resp = await _rate_limited_post(
                        client, f"{base}/GetContributions",
                        data={"entityId": entity_id, "reportYear": year},
                    )
                    if "application/json" in resp.headers.get("content-type", ""):
                        return self._parse_contributions_json(resp.json(), view)
                except Exception:
                    pass

                # Fallback: scrape the entity detail page
                resp = await _rate_limited_get(
                    client, f"{base}/Entity/{entity_id}",
                    params={"year": year},
                )
                return self._parse_contributions_html(resp.text, view)

        except Exception as e:
            log.error(f"Utah fetch_contributors error: {e}")
            return []

    def _parse_contributions_json(self, data, view: str) -> list[Contributor]:
        items = data if isinstance(data, list) else data.get("results", data.get("contributions", []))
        raw = []
        for item in items:
            name = item.get("ContributorName", item.get("Name", "")).strip()
            employer = item.get("Employer", item.get("employer", "")).strip()
            amount = int(float(item.get("Amount", item.get("amount", 0))))
            if not name or amount <= 0:
                continue
            raw.append({"name": name, "employer": employer, "amount": amount})
        return self._aggregate_contributors(raw, view)

    def _parse_contributions_html(self, html: str, view: str) -> list[Contributor]:
        raw = []
        try:
            soup = BeautifulSoup(html, "html.parser")
            table = soup.find("table", {"id": re.compile(r"contrib", re.I)})
            if not table:
                tables = soup.find_all("table")
                for t in tables:
                    headers = [th.get_text(strip=True).lower() for th in t.find_all("th")]
                    if any("contrib" in h or "donor" in h or "name" in h for h in headers):
                        table = t
                        break
            if not table:
                return []

            headers = [th.get_text(strip=True).lower() for th in table.find_all("th")]
            name_col = next((i for i, h in enumerate(headers) if "name" in h), 0)
            emp_col = next((i for i, h in enumerate(headers) if "employer" in h), -1)
            amt_col = next((i for i, h in enumerate(headers) if "amount" in h or "total" in h), -1)

            for row in table.find_all("tr")[1:]:
                cells = row.find_all("td")
                if len(cells) <= max(name_col, amt_col):
                    continue
                name = cells[name_col].get_text(strip=True)
                employer = cells[emp_col].get_text(strip=True) if emp_col >= 0 and emp_col < len(cells) else ""
                amount = _parse_money(cells[amt_col].get_text()) if amt_col >= 0 and amt_col < len(cells) else 0
                if name and amount > 0:
                    raw.append({"name": name, "employer": employer, "amount": amount})
        except Exception as e:
            log.error(f"Utah HTML contributions parse error: {e}")
        return self._aggregate_contributors(raw, view)

    def _aggregate_contributors(self, raw: list[dict], view: str) -> list[Contributor]:
        if view == "individual":
            aggregated = {}
            for r in raw:
                key = r["name"]
                if key in aggregated:
                    aggregated[key]["total"] += r["amount"]
                    aggregated[key]["count"] += 1
                else:
                    aggregated[key] = {
                        "total": r["amount"], "count": 1,
                        "employer": r["employer"],
                    }
            result = []
            for name, info in sorted(aggregated.items(), key=lambda x: -x[1]["total"]):
                result.append(Contributor(
                    name=name, total=info["total"], count=info["count"],
                    type="individual", employer_name=info["employer"],
                ))
                if len(result) >= 20:
                    break
            return result
        else:
            aggregated = {}
            for r in raw:
                employer = (r.get("employer") or "Unknown").strip()
                if not employer or employer.upper() in ("N/A", "NONE", "", "SELF", "RETIRED"):
                    continue
                key = employer.upper()
                if key in aggregated:
                    aggregated[key]["total"] += r["amount"]
                    aggregated[key]["count"] += 1
                else:
                    aggregated[key] = {
                        "display": employer.title(),
                        "total": r["amount"], "count": 1,
                    }
            result = []
            for key, info in sorted(aggregated.items(), key=lambda x: -x[1]["total"]):
                result.append(Contributor(
                    name=info["display"], total=info["total"],
                    count=info["count"], type="employer", employer_name="",
                ))
                if len(result) >= 20:
                    break
            return result

    async def fetch_summary(self, candidate_id: str, year: str) -> FinancialSummary | None:
        if not _SCRAPER_AVAILABLE:
            return None

        entity_id = candidate_id.replace("UT-S-", "").replace("UT-M-", "")
        is_municipal = candidate_id.startswith("UT-M-")

        try:
            async with httpx.AsyncClient(
                headers={"User-Agent": _USER_AGENT},
                follow_redirects=True,
            ) as client:
                base = f"{_BASE_URL}/Municipal" if is_municipal else _BASE_URL
                resp = await _rate_limited_get(
                    client, f"{base}/Entity/{entity_id}",
                    params={"year": year},
                )
                soup = BeautifulSoup(resp.text, "html.parser")

                name = ""
                party = ""
                office = ""
                name_el = soup.find(class_=re.compile(r"entity.?name", re.I))
                if name_el:
                    name = name_el.get_text(strip=True)

                total_raised = total_spent = cash = debt = 0
                for label_el in soup.find_all(string=re.compile(
                    r"total.*receipt|total.*contribut|total.*raised", re.I
                )):
                    parent = label_el.find_parent()
                    if parent:
                        val = parent.find_next_sibling() or parent.find_next()
                        if val:
                            total_raised = _parse_money(val.get_text())
                            break

                for label_el in soup.find_all(string=re.compile(
                    r"total.*expend|total.*spent|total.*disburs", re.I
                )):
                    parent = label_el.find_parent()
                    if parent:
                        val = parent.find_next_sibling() or parent.find_next()
                        if val:
                            total_spent = _parse_money(val.get_text())
                            break

                return FinancialSummary(
                    candidate=name, candidate_id=candidate_id, cycle=year,
                    total_raised=total_raised, total_spent=total_spent,
                    cash_on_hand=cash, debt=debt,
                    party=party, state="UT", office=office,
                    source_id="utah",
                )
        except Exception as e:
            log.error(f"Utah fetch_summary error: {e}")
            return None


if _SCRAPER_AVAILABLE:
    register_source(UtahSource())
```

- [ ] **Step 2: Manually test against live site**

Open a Python REPL and test search:
```python
import asyncio, sys, os
sys.path.insert(0, "plugins/donor_cloud/sources")
# Load sources
import importlib.util
spec = importlib.util.spec_from_file_location("donor_cloud_sources", "plugins/donor_cloud/sources/__init__.py")
mod = importlib.util.module_from_spec(spec)
sys.modules["donor_cloud_sources"] = mod
spec.loader.exec_module(mod)
# Load utah
spec2 = importlib.util.spec_from_file_location("utah", "plugins/donor_cloud/sources/utah.py")
mod2 = importlib.util.module_from_spec(spec2)
sys.modules["utah"] = mod2
spec2.loader.exec_module(mod2)
src = mod2.UtahSource()
results = asyncio.run(src.search("smith", "2026"))
print(results)
```

If the AJAX endpoints return 404 or HTML instead of JSON, the fallback HTML parser should still work. Adjust the endpoint URLs in `_search_state` and `_search_municipal` based on what browser DevTools shows.

- [ ] **Step 3: Commit**

```bash
git add plugins/donor_cloud/sources/utah.py
git commit -m "[feat] add Utah disclosures scraper source provider"
```

---

### Task 5: Refactor routes.py to Use Source Providers

**Files:**
- Modify: `plugins/donor_cloud/routes.py`

- [ ] **Step 1: Rewrite routes.py**

Replace the entire contents of `plugins/donor_cloud/routes.py` with:

```python
"""
LinguaTaxi — Donor Cloud Plugin Routes
Multi-source donor data: FEC (federal) + Utah (state/county/city).

GET  /api/donor-cloud/status          — health check + source info
GET  /api/donor-cloud/search          — search candidates across all sources
GET  /api/donor-cloud/contributors    — top contributors for a candidate
GET  /api/donor-cloud/summary         — candidate funding summary
GET  /api/donor-cloud/roster          — get event roster
POST /api/donor-cloud/roster/event    — create/update/delete events
POST /api/donor-cloud/roster/candidate — add/remove candidates from events
POST /api/donor-cloud/roster/refresh  — force refresh event candidates
"""

import asyncio
import importlib.util
import json
import logging
import os
import sys
import threading
import time
from pathlib import Path
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse

log = logging.getLogger("livecaption")

router = APIRouter(prefix="/api")

_plugin_dir = Path(__file__).parent
_data_dir = _plugin_dir / "data"
_cache_dir = _data_dir / "cache"
_roster_path = _data_dir / "roster.json"

# ── Load source providers via importlib (plugin loaded dynamically) ──

_sources_dir = _plugin_dir / "sources"

_src_spec = importlib.util.spec_from_file_location(
    "donor_cloud_sources", str(_sources_dir / "__init__.py"))
_src_mod = importlib.util.module_from_spec(_src_spec)
sys.modules["donor_cloud_sources"] = _src_mod
_src_spec.loader.exec_module(_src_mod)

Candidate = _src_mod.Candidate
Contributor = _src_mod.Contributor
FinancialSummary = _src_mod.FinancialSummary
get_source = _src_mod.get_source
get_all_sources = _src_mod.get_all_sources

# Load FEC source (registers itself)
_fec_spec = importlib.util.spec_from_file_location(
    "donor_cloud_fec", str(_sources_dir / "fec.py"))
_fec_mod = importlib.util.module_from_spec(_fec_spec)
sys.modules["donor_cloud_fec"] = _fec_mod
_fec_spec.loader.exec_module(_fec_mod)

# Load Utah source (registers itself if httpx/bs4 available)
_utah_spec = importlib.util.spec_from_file_location(
    "donor_cloud_utah", str(_sources_dir / "utah.py"))
_utah_mod = importlib.util.module_from_spec(_utah_spec)
sys.modules["donor_cloud_utah"] = _utah_mod
_utah_spec.loader.exec_module(_utah_mod)

# Load cache
_cache_spec = importlib.util.spec_from_file_location(
    "donor_cloud_cache", str(_plugin_dir / "cache.py"))
_cache_mod = importlib.util.module_from_spec(_cache_spec)
sys.modules["donor_cloud_cache"] = _cache_mod
_cache_spec.loader.exec_module(_cache_mod)

DiskCache = _cache_mod.DiskCache

# ── State ──

_plugin_settings = {}
_disk_cache: DiskCache | None = None
_mem_cache = {}
_mem_cache_lock = threading.Lock()
_MEM_CACHE_TTL = 3600


def _get_cycle():
    cycle = _plugin_settings.get("cycle", "")
    if not cycle:
        cycle = "2024"
    try:
        return str(int(cycle))
    except (ValueError, TypeError):
        return "2024"


def _get_staleness_hours():
    try:
        return float(_plugin_settings.get("staleness_hours", 24))
    except (ValueError, TypeError):
        return 24.0


def _init_cache():
    global _disk_cache
    os.makedirs(str(_cache_dir), exist_ok=True)
    _disk_cache = DiskCache(str(_cache_dir), _get_staleness_hours())


def _load_roster() -> dict:
    if not _roster_path.exists():
        return {"events": []}
    try:
        return json.loads(_roster_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"events": []}


def _save_roster(roster: dict):
    os.makedirs(str(_data_dir), exist_ok=True)
    _roster_path.write_text(
        json.dumps(roster, indent=2), encoding="utf-8")


# ── Mem cache helper ──

def _mem_get(key: str):
    with _mem_cache_lock:
        if key in _mem_cache:
            data, ts = _mem_cache[key]
            if time.monotonic() - ts < _MEM_CACHE_TTL:
                return data
            del _mem_cache[key]
    return None


def _mem_set(key: str, data):
    with _mem_cache_lock:
        _mem_cache[key] = (data, time.monotonic())


# ── Routes ──

@router.get("/donor-cloud/status")
def donor_cloud_status():
    sources = []
    for s in get_all_sources():
        sources.append({"id": s.source_id, "name": s.display_name})
    return {"status": "ok", "sources": sources, "cycle": _get_cycle()}


@router.get("/donor-cloud/search")
async def search_candidates(
    name: str = Query(..., min_length=2),
    source: str | None = Query(None, description="Filter by source ID"),
):
    if source:
        src = get_source(source)
        if not src:
            raise HTTPException(404, f"Unknown source: {source}")
        results = await src.search(name, _get_cycle())
    else:
        tasks = [s.search(name, _get_cycle()) for s in get_all_sources()]
        all_results = await asyncio.gather(*tasks, return_exceptions=True)
        results = []
        for r in all_results:
            if isinstance(r, list):
                results.extend(r)

    return {
        "candidates": [c.to_dict() for c in results],
        "query": name,
    }


@router.get("/donor-cloud/contributors")
async def get_contributors(
    cid: str = Query(..., min_length=2, description="Candidate ID"),
    source: str = Query(..., description="Source ID (fec, utah)"),
    cycle: str | None = Query(None),
    view: str = Query("employer", description="employer or individual"),
):
    use_cycle = cycle or _get_cycle()

    # Check mem cache
    cache_key = f"contrib:{source}:{cid}:{use_cycle}:{view}"
    cached = _mem_get(cache_key)
    if cached:
        return cached

    src = get_source(source)
    if not src:
        raise HTTPException(404, f"Unknown source: {source}")

    contributors = await src.fetch_contributors(cid, use_cycle, view)

    if not contributors and _disk_cache:
        entry = _disk_cache.load(source, cid)
        if entry and entry.get("contributors"):
            contributors = [
                Contributor(**c) for c in entry["contributors"]
                if c.get("type", "employer") == ("individual" if view == "individual" else "employer")
            ] or [Contributor(**c) for c in entry["contributors"]]

    summary = await src.fetch_summary(cid, use_cycle)
    cand_name = summary.candidate if summary else ""

    if _disk_cache and contributors:
        cand = Candidate(
            id=cid, name=cand_name, party=summary.party if summary else "",
            office=summary.office if summary else "", state=summary.state if summary else "",
            source_id=source,
        )
        _disk_cache.save(cand, contributors, summary)

    response = {
        "candidate": cand_name,
        "cid": cid,
        "source": source,
        "cycle": use_cycle,
        "view": view,
        "contributors": [c.to_dict() for c in contributors],
        "stale": False,
    }
    _mem_set(cache_key, response)
    return response


@router.get("/donor-cloud/summary")
async def get_summary(
    cid: str = Query(..., min_length=2),
    source: str = Query(...),
    cycle: str | None = Query(None),
):
    use_cycle = cycle or _get_cycle()
    src = get_source(source)
    if not src:
        raise HTTPException(404, f"Unknown source: {source}")

    summary = await src.fetch_summary(cid, use_cycle)
    if not summary:
        if _disk_cache:
            entry = _disk_cache.load(source, cid)
            if entry and entry.get("summary"):
                return {**entry["summary"], "stale": True}
        raise HTTPException(404, "No financial data found.")

    return {**summary.to_dict(), "stale": False}


# ── Roster API ──

@router.get("/donor-cloud/roster")
def get_roster():
    roster = _load_roster()
    return roster


@router.post("/donor-cloud/roster/event")
async def manage_event(request: Request):
    body = await request.json()
    action = body.get("action", "create")
    roster = _load_roster()

    if action == "create":
        name = body.get("name", "").strip()
        date = body.get("date", "")
        if not name:
            raise HTTPException(400, "Event name required")
        roster["events"].append({"name": name, "date": date, "candidates": []})

    elif action == "rename":
        idx = body.get("index")
        name = body.get("name", "").strip()
        if idx is not None and 0 <= idx < len(roster["events"]):
            roster["events"][idx]["name"] = name

    elif action == "delete":
        idx = body.get("index")
        if idx is not None and 0 <= idx < len(roster["events"]):
            roster["events"].pop(idx)

    _save_roster(roster)
    return {"ok": True, "roster": roster}


@router.post("/donor-cloud/roster/candidate")
async def manage_roster_candidate(request: Request):
    body = await request.json()
    action = body.get("action", "add")
    event_idx = body.get("event_index", 0)
    roster = _load_roster()

    if event_idx < 0 or event_idx >= len(roster["events"]):
        raise HTTPException(400, "Invalid event index")
    event = roster["events"][event_idx]

    if action == "add":
        candidate = body.get("candidate", {})
        name = candidate.get("name", "")
        source = candidate.get("source", "")
        cid = candidate.get("candidate_id", "")
        if not name or not source or not cid:
            raise HTTPException(400, "candidate must have name, source, candidate_id")

        already = any(
            c["candidate_id"] == cid and c["source"] == source
            for c in event["candidates"]
        )
        if already:
            return {"ok": True, "message": "Already in roster", "roster": roster}

        event["candidates"].append({
            "name": name, "source": source, "candidate_id": cid,
        })
        _save_roster(roster)

        # Immediately fetch and cache data for the new candidate
        src = get_source(source)
        if src and _disk_cache:
            try:
                cycle = _get_cycle()
                contribs = await src.fetch_contributors(cid, cycle, "employer")
                summary = await src.fetch_summary(cid, cycle)
                cand_obj = Candidate(
                    id=cid, name=name,
                    party=summary.party if summary else "",
                    office=summary.office if summary else "",
                    state=summary.state if summary else "",
                    source_id=source,
                )
                _disk_cache.save(cand_obj, contribs, summary)
            except Exception as e:
                log.warning(f"Failed to pre-cache roster candidate {name}: {e}")

    elif action == "remove":
        cid = body.get("candidate_id", "")
        source = body.get("source", "")
        event["candidates"] = [
            c for c in event["candidates"]
            if not (c["candidate_id"] == cid and c["source"] == source)
        ]
        _save_roster(roster)

    return {"ok": True, "roster": roster}


@router.post("/donor-cloud/roster/refresh")
async def refresh_roster(request: Request):
    body = await request.json()
    event_idx = body.get("event_index", 0)
    roster = _load_roster()

    if event_idx < 0 or event_idx >= len(roster["events"]):
        raise HTTPException(400, "Invalid event index")

    event = roster["events"][event_idx]
    cycle = _get_cycle()
    refreshed = 0

    for cand_entry in event["candidates"]:
        src = get_source(cand_entry["source"])
        if not src or not _disk_cache:
            continue
        cid = cand_entry["candidate_id"]

        if not _disk_cache.is_stale(cand_entry["source"], cid):
            continue

        try:
            contribs = await src.fetch_contributors(cid, cycle, "employer")
            summary = await src.fetch_summary(cid, cycle)
            cand_obj = Candidate(
                id=cid, name=cand_entry["name"],
                party=summary.party if summary else "",
                office=summary.office if summary else "",
                state=summary.state if summary else "",
                source_id=cand_entry["source"],
            )
            _disk_cache.save(cand_obj, contribs, summary)
            refreshed += 1
        except Exception as e:
            log.warning(f"Roster refresh failed for {cand_entry['name']}: {e}")

    return {"ok": True, "refreshed": refreshed, "total": len(event["candidates"])}


@router.post("/donor-cloud/roster/load-event")
async def load_event(request: Request):
    """Called when operator selects an event — checks for stale data."""
    body = await request.json()
    event_idx = body.get("event_index", 0)
    roster = _load_roster()

    if event_idx < 0 or event_idx >= len(roster["events"]):
        raise HTTPException(400, "Invalid event index")

    event = roster["events"][event_idx]
    cycle = _get_cycle()
    updated = 0

    for cand_entry in event["candidates"]:
        src = get_source(cand_entry["source"])
        if not src or not _disk_cache:
            continue
        cid = cand_entry["candidate_id"]

        if not _disk_cache.is_stale(cand_entry["source"], cid):
            continue

        try:
            contribs = await src.fetch_contributors(cid, cycle, "employer")
            summary = await src.fetch_summary(cid, cycle)
            cand_obj = Candidate(
                id=cid, name=cand_entry["name"],
                party=summary.party if summary else "",
                office=summary.office if summary else "",
                state=summary.state if summary else "",
                source_id=cand_entry["source"],
            )
            _disk_cache.save(cand_obj, contribs, summary)
            updated += 1
        except Exception as e:
            log.warning(f"Event load refresh failed for {cand_entry['name']}: {e}")

    return {"ok": True, "event": event, "updated": updated}


# ── Event hooks ──

def handle_event(event_name, data, settings):
    global _plugin_settings
    if event_name == "on_config_change":
        _plugin_settings = settings
        if _disk_cache:
            _disk_cache._staleness_hours = _get_staleness_hours()
    elif event_name == "on_shutdown":
        _plugin_settings = settings
        with _mem_cache_lock:
            _mem_cache.clear()
    elif event_name == "on_enabled":
        _init_cache()
    elif event_name == "on_startup":
        _init_cache()


_init_cache()
```

- [ ] **Step 2: Verify the server starts**

Run: `python server.py`
Check logs for: `Plugin 'donor_cloud': mounted router at /api/donor-cloud`
No import errors. Stop the server.

- [ ] **Step 3: Test search endpoint manually**

```bash
curl "http://localhost:3001/api/donor-cloud/search?name=biden"
```

Expected: JSON response with candidates array, each having `source_id: "fec"`

```bash
curl "http://localhost:3001/api/donor-cloud/status"
```

Expected: JSON with `sources` array listing `fec` and `utah` (if httpx installed)

- [ ] **Step 4: Commit**

```bash
git add plugins/donor_cloud/routes.py
git commit -m "[feat] refactor donor cloud routes to multi-source provider dispatch"
```

---

### Task 6: Default Roster File and Manifest Update

**Files:**
- Create: `plugins/donor_cloud/data/roster.json`
- Modify: `plugins/donor_cloud/manifest.json`

- [ ] **Step 1: Create default roster**

Create `plugins/donor_cloud/data/roster.json`:

```json
{
  "events": []
}
```

- [ ] **Step 2: Update manifest.json**

Replace contents of `plugins/donor_cloud/manifest.json`:

```json
{
  "id": "donor_cloud",
  "name": "Donor Cloud",
  "version": "3.0.0",
  "description": "Multi-source candidate donor word cloud — FEC (federal) + Utah state/county disclosures",
  "author": "LinguaTaxi",
  "hooks": ["on_config_change", "on_shutdown", "on_enabled", "on_startup"],
  "has_routes": true,
  "has_panel": true,
  "route_prefix": "/api/donor-cloud",
  "settings_schema": {
    "cycle": {
      "type": "text",
      "label": "Election cycle (e.g. 2024)",
      "default": "2024"
    },
    "staleness_hours": {
      "type": "text",
      "label": "Cache staleness threshold (hours)",
      "default": "24"
    }
  }
}
```

- [ ] **Step 3: Commit**

```bash
git add plugins/donor_cloud/data/roster.json plugins/donor_cloud/manifest.json
git commit -m "[feat] update donor cloud manifest to v3.0.0 with multi-source config"
```

---

### Task 7: Panel HTML — Roster Section, Toggle, Source Badge

**Files:**
- Modify: `plugins/donor_cloud/panel.html`

- [ ] **Step 1: Rewrite panel.html**

Replace contents of `plugins/donor_cloud/panel.html`:

```html
<div class="dc-controls">
  <div class="dc-search-row">
    <input type="text" id="dc-search" class="dc-input" placeholder="Candidate name or ID">
    <button class="dc-btn dc-btn--search" id="dc-search-btn">Search</button>
  </div>
  <div class="dc-cycle-row">
    <label class="dc-label">Cycle:</label>
    <select id="dc-cycle" class="dc-select">
      <option value="2026">2026</option>
      <option value="2024" selected>2024</option>
      <option value="2022">2022</option>
      <option value="2020">2020</option>
      <option value="2018">2018</option>
      <option value="2016">2016</option>
    </select>
  </div>
  <div class="dc-search-results" id="dc-search-results"></div>
</div>

<!-- Event Roster -->
<div class="dc-roster-section" id="dc-roster-section">
  <div class="dc-roster-header">
    <span class="dc-section-title">Event Roster</span>
    <button class="dc-btn dc-btn--small" id="dc-roster-new-event" title="New Event">+ Event</button>
  </div>
  <div class="dc-roster-event-row">
    <select id="dc-roster-event-select" class="dc-select dc-select--wide"></select>
    <button class="dc-btn dc-btn--icon" id="dc-roster-refresh" title="Refresh All">&#8635;</button>
    <button class="dc-btn dc-btn--icon dc-btn--danger" id="dc-roster-delete-event" title="Delete Event">&#10005;</button>
  </div>
  <div class="dc-roster-candidates" id="dc-roster-candidates"></div>
</div>

<!-- View Toggle -->
<div class="dc-toggle-row" id="dc-toggle-row">
  <button class="dc-toggle-btn dc-toggle-btn--active" id="dc-toggle-employer" data-view="employer">By Employer</button>
  <button class="dc-toggle-btn" id="dc-toggle-individual" data-view="individual">By Individual</button>
</div>

<!-- Candidate Info with Source Badge -->
<div class="dc-candidate-info" id="dc-candidate-info"></div>

<div class="dc-empty" id="dc-empty">Search for a candidate or select from an event roster. Data from FEC.gov + Utah disclosures.</div>
<div class="dc-cloud-container" id="dc-cloud-container"></div>
<div class="dc-legend" id="dc-legend"></div>
```

- [ ] **Step 2: Commit**

```bash
git add plugins/donor_cloud/panel.html
git commit -m "[feat] add roster section, view toggle, and source badge to donor cloud panel"
```

---

### Task 8: Panel CSS — Roster and Toggle Styles

**Files:**
- Modify: `plugins/donor_cloud/panel.css`

- [ ] **Step 1: Append new styles to panel.css**

Add the following to the end of `plugins/donor_cloud/panel.css`:

```css

/* ── Roster section ────────────────────────────────────────────────────── */
.dc-roster-section {
  margin: 10px 0;
  padding: 8px 0;
  border-top: 1px solid rgba(255,255,255,0.06);
  border-bottom: 1px solid rgba(255,255,255,0.06);
}

.dc-roster-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 6px;
}

.dc-section-title {
  font-size: 10px;
  font-weight: 700;
  color: rgba(255,255,255,0.5);
  text-transform: uppercase;
  letter-spacing: 0.08em;
}

.dc-roster-event-row {
  display: flex;
  gap: 4px;
  margin-bottom: 6px;
  align-items: center;
}

.dc-select--wide { flex: 1; }

.dc-btn--small {
  padding: 3px 8px;
  font-size: 10px;
  background: rgba(79,195,247,0.15);
  color: #4FC3F7;
  border: 1px solid rgba(79,195,247,0.3);
  border-radius: 4px;
}
.dc-btn--small:hover { background: rgba(79,195,247,0.25); }

.dc-btn--icon {
  padding: 4px 7px;
  font-size: 13px;
  background: rgba(255,255,255,0.05);
  color: rgba(255,255,255,0.5);
  border: 1px solid rgba(255,255,255,0.1);
  border-radius: 4px;
  cursor: pointer;
  line-height: 1;
}
.dc-btn--icon:hover { background: rgba(255,255,255,0.1); color: #fff; }

.dc-btn--danger:hover { color: #F09595; border-color: rgba(240,149,149,0.3); }

.dc-roster-candidates {
  max-height: 120px;
  overflow-y: auto;
}

.dc-roster-item {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 4px 6px;
  border-radius: 4px;
  cursor: pointer;
  transition: background 0.15s;
}
.dc-roster-item:hover { background: rgba(255,255,255,0.06); }

.dc-roster-name {
  font-size: 12px;
  color: rgba(255,255,255,0.8);
  flex: 1;
}

.dc-roster-source {
  font-size: 9px;
  padding: 1px 5px;
  border-radius: 3px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}
.dc-roster-source--fec { background: rgba(79,195,247,0.2); color: #4FC3F7; }
.dc-roster-source--utah { background: rgba(129,199,132,0.2); color: #81C784; }

.dc-roster-remove {
  font-size: 12px;
  color: rgba(255,255,255,0.25);
  cursor: pointer;
  padding: 0 4px;
}
.dc-roster-remove:hover { color: #F09595; }

/* ── View toggle ───────────────────────────────────────────────────────── */
.dc-toggle-row {
  display: flex;
  gap: 0;
  margin: 8px 0;
  border-radius: 5px;
  overflow: hidden;
  border: 1px solid rgba(255,255,255,0.1);
}

.dc-toggle-btn {
  flex: 1;
  padding: 5px 0;
  border: none;
  background: rgba(255,255,255,0.03);
  color: rgba(255,255,255,0.4);
  font-size: 10px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  cursor: pointer;
  transition: 0.15s;
  font-family: inherit;
}
.dc-toggle-btn:hover { background: rgba(255,255,255,0.06); color: rgba(255,255,255,0.6); }
.dc-toggle-btn--active {
  background: rgba(79,195,247,0.15);
  color: #4FC3F7;
}

/* ── Source badge in candidate info ────────────────────────────────────── */
.dc-source-badge {
  display: inline-block;
  font-size: 9px;
  padding: 1px 6px;
  border-radius: 3px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  margin-left: 6px;
  vertical-align: middle;
}
.dc-source-badge--fec { background: rgba(79,195,247,0.2); color: #4FC3F7; }
.dc-source-badge--utah { background: rgba(129,199,132,0.2); color: #81C784; }

/* ── Add-to-roster button in search results ────────────────────────────── */
.dc-result-add {
  font-size: 14px;
  color: rgba(79,195,247,0.6);
  cursor: pointer;
  padding: 0 4px;
  margin-left: 4px;
  font-weight: 700;
}
.dc-result-add:hover { color: #4FC3F7; }
```

- [ ] **Step 2: Commit**

```bash
git add plugins/donor_cloud/panel.css
git commit -m "[feat] add roster and toggle styles to donor cloud panel"
```

---

### Task 9: Panel JS — Multi-Source Search, Roster, Toggle, Auto-Trigger

**Files:**
- Modify: `plugins/donor_cloud/panel.js`

- [ ] **Step 1: Rewrite panel.js**

Replace the entire contents of `plugins/donor_cloud/panel.js`:

```javascript
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
    // Trigger stale check for this event's candidates
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
```

- [ ] **Step 2: Start server and test in browser**

1. Start: `python server.py`
2. Open `http://localhost:3001` (operator panel)
3. Open the Donor Cloud plugin panel
4. Verify:
   - Search field works — returns results with source badges (FEC/UTAH)
   - Clicking a result loads the word cloud
   - "By Employer" / "By Individual" toggle switches the view and reloads
   - Source badge appears next to candidate name
   - "Event Roster" section visible with "+ Event" button
   - Can create an event, search candidates, click "+" to add
   - Roster candidates appear with source badges and "×" remove
   - Clicking a roster candidate loads their donor cloud
   - Refresh button works
   - Delete event works

- [ ] **Step 3: Commit**

```bash
git add plugins/donor_cloud/panel.js
git commit -m "[feat] rewrite donor cloud panel JS for multi-source, roster, and view toggle"
```

---

### Task 10: Add Dependencies to requirements.txt

**Files:**
- Modify: `requirements.txt`

- [ ] **Step 1: Add httpx and beautifulsoup4**

Add to the end of `requirements.txt`, before the speech recognition section:

```
# Utah campaign finance scraper (donor cloud plugin)
httpx>=0.27.0,<1.0
beautifulsoup4>=4.12.0,<5.0
```

- [ ] **Step 2: Install locally**

```bash
pip install httpx beautifulsoup4
```

- [ ] **Step 3: Commit**

```bash
git add requirements.txt
git commit -m "[feat] add httpx and beautifulsoup4 for Utah campaign finance scraper"
```

---

### Task 11: Integration Testing — Full End-to-End

**Files:** No new files — testing existing code together.

- [ ] **Step 1: Run all unit tests**

```bash
python -m pytest plugins/donor_cloud/tests/ -v
```

Expected: All tests PASS

- [ ] **Step 2: Start server and test FEC flow**

1. Start: `python server.py`
2. Open operator panel → Donor Cloud
3. Search "biden" → should see FEC results with FEC badge
4. Click result → word cloud loads (By Employer view)
5. Toggle to "By Individual" → cloud reloads with individual names
6. Toggle back to "By Employer" → cloud shows employers again

- [ ] **Step 3: Test Utah flow**

1. Search "smith" → should see Utah results (if any) with UTAH badge alongside FEC results
2. If Utah results appear, click one → word cloud loads with UTAH badge
3. If scraping fails gracefully, verify no crash and an error message appears

- [ ] **Step 4: Test roster flow**

1. Click "+ Event" → enter "Test Event"
2. Search "biden" → click "+" on a result
3. Candidate appears in roster with FEC badge
4. Click the roster candidate → loads their cloud
5. Click "×" to remove → candidate removed
6. Click refresh button → no crash
7. Click event delete → confirm → event removed

- [ ] **Step 5: Test cache persistence**

1. Load a candidate's donor data
2. Check `plugins/donor_cloud/data/cache/` — JSON file should exist
3. Restart server
4. Load same candidate → should load faster (from cache)

- [ ] **Step 6: Test auto-trigger**

1. Create an event with "Joe Biden" in roster
2. If live session triggers speaker change with "Joe Biden", cloud should auto-load

- [ ] **Step 7: Commit any fixes**

```bash
git add -A plugins/donor_cloud/
git commit -m "[fix] integration fixes for multi-source donor cloud"
```

---

## Self-Review

### Spec Coverage

| Spec Requirement | Task |
|-----------------|------|
| Source provider architecture (BaseSource ABC) | Task 1 |
| Shared data models (Candidate, Contributor, FinancialSummary) | Task 1 |
| Persistent disk cache | Task 2 |
| FEC source extracted from routes.py | Task 3 |
| Utah scraper (state + municipal) | Task 4 |
| Route dispatch to sources | Task 5 |
| Event roster (config file + API) | Task 5, 6 |
| Employer/individual toggle | Tasks 5, 7, 8, 9 |
| Source badge display | Tasks 7, 8, 9 |
| Roster panel management (create/delete events, add/remove candidates) | Tasks 7, 9 |
| Auto-trigger from roster | Task 9 |
| Event-scoped refresh (not global) | Task 5 (load-event endpoint) |
| Candidate added → immediate fetch | Task 5 (roster/candidate endpoint) |
| Dependencies (httpx, bs4) | Task 10 |
| Integration testing | Task 11 |

### Placeholder Scan

No TBD, TODO, or placeholder items found.

### Type Consistency

- `Candidate.to_dict()` used in Tasks 1, 5 ✓
- `Contributor.to_dict()` used in Tasks 1, 5 ✓
- `FinancialSummary.to_dict()` used in Tasks 1, 5 ✓
- `BaseSource.fetch_contributors(cid, year, view)` — 3 params — consistent across Tasks 1, 3, 4, 5 ✓
- `DiskCache.save(candidate, contributors, summary)` — consistent across Tasks 2, 5 ✓
- `DiskCache.is_stale(source_id, candidate_id)` — consistent across Tasks 2, 5 ✓
- JS `loadContributors(cid, source)` — 2 params — consistent across all JS calls in Task 9 ✓
