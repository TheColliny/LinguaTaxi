"""
FEC (Federal Election Commission) source provider for Donor Cloud.

Implements BaseSource using the FEC public API (api.open.fec.gov/v1).
No API key required — uses the built-in DEMO_KEY.

Auto-registers on import via register_source(FECSource()).
"""
from __future__ import annotations

import asyncio
import importlib.util
import logging
import os
import sys
import threading
import time
from typing import Optional

# ---------------------------------------------------------------------------
# Load donor_cloud_sources (data models + registry) via importlib.
# This is the same pattern used by all plugin files so that they work
# whether the sources/ directory is on sys.path or not.
# ---------------------------------------------------------------------------
_sources_dir = os.path.dirname(__file__)

if "donor_cloud_sources" not in sys.modules:
    _spec = importlib.util.spec_from_file_location(
        "donor_cloud_sources",
        os.path.join(_sources_dir, "__init__.py"),
    )
    _mod = importlib.util.module_from_spec(_spec)
    sys.modules["donor_cloud_sources"] = _mod
    _spec.loader.exec_module(_mod)

from donor_cloud_sources import (  # noqa: E402
    BaseSource,
    Candidate,
    Contributor,
    FinancialSummary,
    register_source,
)

log = logging.getLogger("livecaption")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_FEC_BASE = "https://api.open.fec.gov/v1"
_FEC_KEY = "DEMO_KEY"

_CACHE_TTL = 3600       # seconds — successful response
_CACHE_TTL_FAIL = 60    # seconds — failed/None response

_SKIP_EMPLOYERS: frozenset[str] = frozenset({
    "N/A", "NOT EMPLOYED", "SELF-EMPLOYED", "SELF EMPLOYED", "SELF",
    "RETIRED", "NONE", "NULL", "", "NOT APPLICABLE",
    "INFORMATION REQUESTED", "INFORMATION REQUESTED PER BEST EFFORTS",
    "HOMEMAKER", "STUDENT", "UNEMPLOYED", "DISABLED",
})


# ---------------------------------------------------------------------------
# FECSource
# ---------------------------------------------------------------------------

class FECSource(BaseSource):
    """Source provider that fetches campaign-finance data from the FEC API."""

    source_id: str = "fec"
    display_name: str = "FEC"

    def __init__(self) -> None:
        self._cache: dict[str, tuple[dict | None, float]] = {}
        self._cache_lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public helpers (also tested directly)
    # ------------------------------------------------------------------

    def _format_name(self, raw: str) -> str:
        """Convert 'LAST, FIRST' FEC format to 'First Last'."""
        if "," in raw:
            parts = raw.split(",", 1)
            return (parts[1].strip() + " " + parts[0].strip()).title()
        return raw.title()

    def _should_skip_employer(self, employer: str) -> bool:
        """Return True if the employer string should be filtered out."""
        return employer.strip().upper() in _SKIP_EMPLOYERS

    # ------------------------------------------------------------------
    # Internal: HTTP + cache
    # ------------------------------------------------------------------

    def _fec_request(
        self,
        cache_key: str,
        path: str,
        params: dict,
    ) -> dict | None:
        """Make a cached GET request to the FEC API.

        Thread-safe in-memory cache with separate TTLs for success/failure.
        Returns the parsed JSON dict, or None on error.
        """
        import requests

        now = time.monotonic()
        with self._cache_lock:
            # Evict expired entries
            expired = [
                k for k, (_, ts) in self._cache.items()
                if now - ts >= _CACHE_TTL
            ]
            for k in expired:
                del self._cache[k]

            # Return from cache if still fresh
            if cache_key in self._cache:
                data, ts = self._cache[cache_key]
                ttl = _CACHE_TTL_FAIL if data is None else _CACHE_TTL
                if now - ts < ttl:
                    return data

        params = dict(params)  # don't mutate caller's dict
        params["api_key"] = _FEC_KEY
        url = _FEC_BASE + path
        try:
            resp = requests.get(url, params=params, timeout=20)
            resp.raise_for_status()
            data = resp.json()
            with self._cache_lock:
                self._cache[cache_key] = (data, now)
            return data
        except Exception as exc:
            log.error("FEC API error (%s): %s", path, exc)
            with self._cache_lock:
                self._cache[cache_key] = (None, now)
            return None

    def _get_principal_committee(self, candidate_id: str) -> str | None:
        """Return the principal committee ID for a candidate, or None."""
        data = self._fec_request(
            f"committee:{candidate_id}",
            f"/candidate/{candidate_id}/committees/",
            {"designation": "P", "per_page": 1},
        )
        if not data:
            return None
        results = data.get("results", [])
        if results:
            return results[0].get("committee_id")
        return None

    # ------------------------------------------------------------------
    # BaseSource interface
    # ------------------------------------------------------------------

    async def search(self, name: str, year: int) -> list[Candidate]:
        """Search FEC for candidates by name.

        Runs the blocking HTTP call in a thread-pool executor so the event
        loop is not blocked.
        """
        def _do_search() -> list[Candidate]:
            data = self._fec_request(
                f"search:{name.lower()}:{year}",
                "/candidates/search/",
                {
                    "q": name,
                    "sort": "-receipts",
                    "per_page": 15,
                    "is_active_candidate": "true",
                },
            )
            if not data:
                return []

            candidates: list[Candidate] = []
            for c in data.get("results", []):
                cid = c.get("candidate_id", "")
                raw_name = c.get("name", "")
                if not cid or not raw_name:
                    continue
                candidates.append(
                    Candidate(
                        id=cid,
                        name=self._format_name(raw_name),
                        party=c.get("party_full") or c.get("party") or "",
                        state=c.get("state") or "",
                        office=c.get("office_full") or c.get("office") or "",
                        source_id=self.source_id,
                    )
                )
            return candidates

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, _do_search)

    async def fetch_contributors(
        self,
        candidate_id: str,
        year: int,
        view: str = "employer",
    ) -> list[Contributor]:
        """Fetch top-20 contributors for a candidate.

        ``view="employer"`` aggregates by employer via FEC's
        ``/schedules/schedule_a/by_employer/`` endpoint.

        ``view="individual"`` fetches individual itemized receipts via
        ``/schedules/schedule_a/`` and aggregates by contributor name in
        Python (top-20 by total).
        """
        def _do_fetch() -> list[Contributor]:
            committee_id = self._get_principal_committee(candidate_id)
            if not committee_id:
                return []

            if view == "employer":
                return self._fetch_by_employer(committee_id, candidate_id, year)
            else:
                return self._fetch_by_individual(committee_id, candidate_id, year)

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, _do_fetch)

    def _fetch_by_employer(
        self,
        committee_id: str,
        candidate_id: str,
        year: int,
    ) -> list[Contributor]:
        """Aggregate contributions by employer (top 20)."""
        data = self._fec_request(
            f"contrib:employer:{committee_id}:{year}",
            "/schedules/schedule_a/by_employer/",
            {
                "committee_id": committee_id,
                "cycle": str(year),
                "sort": "-total",
                "per_page": 50,
            },
        )
        if not data:
            return []

        contributors: list[Contributor] = []
        for entry in data.get("results", []):
            employer_raw = (entry.get("employer") or "").strip().upper()
            if self._should_skip_employer(employer_raw):
                continue
            total = entry.get("total", 0)
            count = entry.get("count", 0)
            if total > 0 and employer_raw:
                contributors.append(
                    Contributor(
                        name=employer_raw.title(),
                        total=float(total),
                        count=count,
                        type="employer",
                        employer_name=employer_raw.title(),
                    )
                )
            if len(contributors) >= 20:
                break

        return contributors

    def _fetch_by_individual(
        self,
        committee_id: str,
        candidate_id: str,
        year: int,
    ) -> list[Contributor]:
        """Aggregate contributions by individual contributor name (top 20)."""
        data = self._fec_request(
            f"contrib:individual:{committee_id}:{year}",
            "/schedules/schedule_a/",
            {
                "committee_id": committee_id,
                "two_year_transaction_period": str(year),
                "sort": "-contribution_receipt_amount",
                "per_page": 100,
            },
        )
        if not data:
            return []

        # Aggregate by contributor name
        agg: dict[str, dict] = {}
        for entry in data.get("results", []):
            contributor_name = (entry.get("contributor_name") or "").strip()
            if not contributor_name:
                continue
            amount = float(entry.get("contribution_receipt_amount") or 0)
            employer = (entry.get("contributor_employer") or "").strip()
            key = contributor_name.upper()
            if key not in agg:
                agg[key] = {
                    "name": contributor_name.title(),
                    "total": 0.0,
                    "count": 0,
                    "employer": employer,
                }
            agg[key]["total"] += amount
            agg[key]["count"] += 1

        # Sort by total descending, take top 20
        sorted_agg = sorted(agg.values(), key=lambda x: x["total"], reverse=True)[:20]
        return [
            Contributor(
                name=entry["name"],
                total=entry["total"],
                count=entry["count"],
                type="individual",
                employer_name=entry["employer"],
            )
            for entry in sorted_agg
        ]

    async def fetch_summary(
        self,
        candidate_id: str,
        year: int,
    ) -> Optional[FinancialSummary]:
        """Fetch financial summary for a candidate and cycle.

        Returns None if no data is found.
        """
        def _do_fetch() -> Optional[FinancialSummary]:
            data = self._fec_request(
                f"totals:{candidate_id}:{year}",
                f"/candidate/{candidate_id}/totals/",
                {"cycle": str(year), "per_page": 1},
            )
            if not data:
                return None

            results = data.get("results", [])
            if not results:
                return None

            s = results[0]

            # Fetch candidate metadata for name/party/state/office
            cand_data = self._fec_request(
                f"cand:{candidate_id}",
                f"/candidate/{candidate_id}/",
                {},
            )
            cand_name = ""
            party = ""
            state = ""
            office = ""
            if cand_data and cand_data.get("results"):
                c = cand_data["results"][0]
                cand_name = self._format_name(c.get("name", ""))
                party = c.get("party_full") or c.get("party") or ""
                state = c.get("state") or ""
                office = c.get("office_full") or ""

            return FinancialSummary(
                candidate=cand_name,
                candidate_id=candidate_id,
                cycle=int(year),
                total_raised=float(s.get("receipts", 0)),
                total_spent=float(s.get("disbursements", 0)),
                cash_on_hand=float(s.get("cash_on_hand_end_period", 0)),
                debt=float(s.get("debts_owed_by_committee", 0)),
                party=party,
                state=state,
                office=office,
                source_id=self.source_id,
            )

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, _do_fetch)

    # ------------------------------------------------------------------
    # Cache management
    # ------------------------------------------------------------------

    def clear_cache(self) -> None:
        """Clear all entries from the in-memory response cache."""
        with self._cache_lock:
            self._cache.clear()


# ---------------------------------------------------------------------------
# Auto-register on import
# ---------------------------------------------------------------------------
register_source(FECSource())
