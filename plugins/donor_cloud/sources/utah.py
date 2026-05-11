"""
Utah (disclosures.utah.gov) source provider for Donor Cloud.

Scrapes campaign finance data from the Utah Lieutenant Governor's campaign
finance disclosure portal, which is an ASP.NET MVC site with AJAX-loaded
search results.  Supports both state-level and municipal candidates.

Candidate IDs are prefixed:
  "UT-S-<entity_id>"  — state-level
  "UT-M-<entity_id>"  — municipal

Auto-registers on import via register_source(UtahSource()) when httpx and
bs4 are available.
"""
from __future__ import annotations

import asyncio
import importlib.util
import logging
import os
import re
import sys
import time
from typing import Optional

# ---------------------------------------------------------------------------
# Load donor_cloud_sources (data models + registry) via importlib.
# Same pattern as fec.py so this works regardless of sys.path state.
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
# Optional scraping dependencies
# ---------------------------------------------------------------------------
try:
    import httpx
    from bs4 import BeautifulSoup
    _SCRAPER_AVAILABLE = True
except ImportError:
    _SCRAPER_AVAILABLE = False

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_BASE_URL = "https://disclosures.utah.gov"
_USER_AGENT = "LinguaTaxi/1.0 (Campaign Finance Research)"
_TIMEOUT = 15.0
_RATE_LIMIT_DELAY = 1.0   # seconds between requests
_MAX_RETRIES = 3

_SKIP_EMPLOYERS: frozenset[str] = frozenset({
    "N/A", "NOT EMPLOYED", "SELF-EMPLOYED", "SELF EMPLOYED", "SELF",
    "RETIRED", "NONE", "NULL", "", "NOT APPLICABLE",
    "INFORMATION REQUESTED", "INFORMATION REQUESTED PER BEST EFFORTS",
    "HOMEMAKER", "STUDENT", "UNEMPLOYED", "DISABLED",
})

# Tracks last-request timestamp for rate limiting (module-level, shared
# across all httpx client sessions within a process).
_last_request_time: float = 0.0


# ---------------------------------------------------------------------------
# Standalone helpers
# ---------------------------------------------------------------------------

def _parse_money(text: str) -> int:
    """Strip non-numeric characters and return an integer cent value.

    Handles formats like "$1,234.56" → 1234, "-$500" → -500.
    Returns 0 on parse failure.
    """
    if not text:
        return 0
    # Keep digits, minus sign, and decimal point
    clean = re.sub(r"[^\d.\-]", "", str(text).strip())
    if not clean or clean in ("-", "."):
        return 0
    try:
        return int(float(clean))
    except ValueError:
        return 0


async def _rate_limited_get(
    client: "httpx.AsyncClient",
    url: str,
    **kwargs,
) -> "httpx.Response":
    """GET url with enforced 1 req/s rate limit and exponential-backoff retries."""
    global _last_request_time
    for attempt in range(_MAX_RETRIES):
        # Enforce rate limit
        now = time.monotonic()
        elapsed = now - _last_request_time
        if elapsed < _RATE_LIMIT_DELAY:
            await asyncio.sleep(_RATE_LIMIT_DELAY - elapsed)
        _last_request_time = time.monotonic()

        resp = await client.get(url, **kwargs)
        if resp.status_code == 429:
            wait = _RATE_LIMIT_DELAY * (2 ** attempt)
            log.warning("Utah 429 on GET %s — sleeping %.1fs", url, wait)
            await asyncio.sleep(wait)
            continue
        resp.raise_for_status()
        return resp

    # Final attempt after exhausting retries
    resp = await client.get(url, **kwargs)
    resp.raise_for_status()
    return resp


async def _rate_limited_post(
    client: "httpx.AsyncClient",
    url: str,
    **kwargs,
) -> "httpx.Response":
    """POST url with enforced 1 req/s rate limit and exponential-backoff retries."""
    global _last_request_time
    for attempt in range(_MAX_RETRIES):
        now = time.monotonic()
        elapsed = now - _last_request_time
        if elapsed < _RATE_LIMIT_DELAY:
            await asyncio.sleep(_RATE_LIMIT_DELAY - elapsed)
        _last_request_time = time.monotonic()

        resp = await client.post(url, **kwargs)
        if resp.status_code == 429:
            wait = _RATE_LIMIT_DELAY * (2 ** attempt)
            log.warning("Utah 429 on POST %s — sleeping %.1fs", url, wait)
            await asyncio.sleep(wait)
            continue
        resp.raise_for_status()
        return resp

    resp = await client.post(url, **kwargs)
    resp.raise_for_status()
    return resp


# ---------------------------------------------------------------------------
# HTML/JSON parsing helpers (module-level so they're unit-testable)
# ---------------------------------------------------------------------------

def _parse_search_html(html: str, prefix: str) -> list[Candidate]:
    """Parse candidate rows from a Utah disclosure search results HTML page.

    Looks for a results table or entity links and extracts id/name/party/office.
    prefix should be "UT-S-" or "UT-M-".
    """
    candidates: list[Candidate] = []
    try:
        soup = BeautifulSoup(html, "html.parser")

        # Primary strategy: find a table with candidate rows
        table = soup.find("table", {"id": re.compile(r"entity|result|candidate", re.I)})
        if table is None:
            # Fallback: first table that has a header row
            tables = soup.find_all("table")
            for t in tables:
                headers = [th.get_text(strip=True).lower() for th in t.find_all("th")]
                if any(h in ("name", "entity", "candidate") for h in headers):
                    table = t
                    break

        if table:
            rows = table.find_all("tr")[1:]  # skip header
            for row in rows:
                cells = row.find_all(["td", "th"])
                if len(cells) < 2:
                    continue
                # Try to find an anchor with entity id
                link = row.find("a", href=True)
                entity_id = ""
                if link:
                    m = re.search(r"[?&/](?:entityId|id|EntityId)=?(\d+)", link["href"])
                    if m:
                        entity_id = m.group(1)
                if not entity_id:
                    # Try data attributes
                    for cell in cells:
                        val = cell.get("data-id") or cell.get("data-entity-id")
                        if val:
                            entity_id = str(val)
                            break
                if not entity_id:
                    continue

                text_values = [c.get_text(strip=True) for c in cells]
                name = text_values[0] if text_values else ""
                party = ""
                office = ""
                for i, h in enumerate(
                    [th.get_text(strip=True).lower() for th in (table.find_all("th") or [])]
                ):
                    if i < len(text_values):
                        if "party" in h:
                            party = text_values[i]
                        elif "office" in h or "position" in h:
                            office = text_values[i]

                if name and entity_id:
                    candidates.append(
                        Candidate(
                            id=f"{prefix}{entity_id}",
                            name=name.title(),
                            party=party,
                            office=office,
                            state="UT",
                            source_id="utah",
                        )
                    )

        # Secondary strategy: look for entity links directly in page
        if not candidates:
            for link in soup.find_all("a", href=True):
                m = re.search(r"[?&/](?:entityId|EntityId|id)=?(\d+)", link["href"])
                if m:
                    entity_id = m.group(1)
                    name = link.get_text(strip=True)
                    if name and entity_id:
                        candidates.append(
                            Candidate(
                                id=f"{prefix}{entity_id}",
                                name=name.title(),
                                party="",
                                office="",
                                state="UT",
                                source_id="utah",
                            )
                        )

    except Exception as exc:
        log.error("Utah _parse_search_html error (%s): %s", prefix, exc)

    return candidates


def _parse_contributions_json(data: list | dict, view: str) -> list[Contributor]:
    """Parse contributors from a JSON response (AJAX endpoint).

    data may be a list of contribution dicts or a dict wrapping a list.
    """
    rows: list[dict] = []
    if isinstance(data, list):
        rows = data
    elif isinstance(data, dict):
        for key in ("data", "results", "contributions", "items"):
            if isinstance(data.get(key), list):
                rows = data[key]
                break
        if not rows and isinstance(data.get("d"), list):
            rows = data["d"]

    return _aggregate_contributors(rows, view)


def _parse_contributions_html(html: str, view: str) -> list[Contributor]:
    """Parse contribution rows from an HTML entity detail/contributions page."""
    rows: list[dict] = []
    try:
        soup = BeautifulSoup(html, "html.parser")

        # Find the contribution table by looking at header text
        contrib_table = None
        for table in soup.find_all("table"):
            headers = [th.get_text(strip=True).lower() for th in table.find_all("th")]
            # Need at minimum a name/contributor column and an amount column
            has_name = any(h in ("name", "contributor", "donor") for h in headers)
            has_amount = any(h in ("amount", "total", "contribution") for h in headers)
            if has_name and has_amount:
                contrib_table = table
                break

        if contrib_table is None:
            return []

        # Map column indices from header text
        header_els = contrib_table.find_all("th")
        col_map: dict[str, int] = {}
        for i, th in enumerate(header_els):
            h = th.get_text(strip=True).lower()
            if h in ("name", "contributor", "donor"):
                col_map["name"] = i
            elif h in ("employer", "organization"):
                col_map["employer"] = i
            elif h in ("amount", "total", "contribution amount", "contribution"):
                col_map["amount"] = i

        name_col = col_map.get("name", 0)
        employer_col = col_map.get("employer")
        amount_col = col_map.get("amount", -1)

        for tr in contrib_table.find_all("tr")[1:]:
            cells = tr.find_all(["td", "th"])
            if len(cells) < 2:
                continue
            try:
                name = cells[name_col].get_text(strip=True) if name_col < len(cells) else ""
                employer = cells[employer_col].get_text(strip=True) if (
                    employer_col is not None and employer_col < len(cells)
                ) else ""
                amount_text = cells[amount_col].get_text(strip=True) if (
                    0 <= amount_col < len(cells)
                ) else "0"
                amount = _parse_money(amount_text)
                if name and amount:
                    rows.append({
                        "name": name,
                        "employer": employer,
                        "amount": amount,
                    })
            except (IndexError, AttributeError):
                continue

    except Exception as exc:
        log.error("Utah _parse_contributions_html error: %s", exc)

    return _aggregate_contributors(rows, view)


def _aggregate_contributors(
    rows: list[dict],
    view: str,
) -> list[Contributor]:
    """Aggregate raw contribution rows into top-20 Contributor objects.

    For view="employer": group by employer field, skip known non-employers.
    For view="individual": group by contributor name.
    """
    agg: dict[str, dict] = {}

    for row in rows:
        try:
            # Normalize field names — JSON keys vary by endpoint
            name = str(
                row.get("ContributorName")
                or row.get("contributorName")
                or row.get("name")
                or row.get("Name")
                or ""
            ).strip()

            employer = str(
                row.get("Employer")
                or row.get("employer")
                or row.get("ContributorEmployer")
                or row.get("contributorEmployer")
                or ""
            ).strip()

            amount = float(
                row.get("Amount")
                or row.get("amount")
                or row.get("ContributionAmount")
                or row.get("contributionAmount")
                or 0
            )

            if view == "employer":
                key_raw = employer.upper()
                if not key_raw or key_raw in _SKIP_EMPLOYERS:
                    continue
                key = key_raw
                display = employer.title()
            else:
                key_raw = name.upper()
                if not key_raw:
                    continue
                key = key_raw
                display = name.title()

            if key not in agg:
                agg[key] = {
                    "name": display,
                    "total": 0.0,
                    "count": 0,
                    "employer": employer,
                }
            agg[key]["total"] += amount
            agg[key]["count"] += 1
        except (ValueError, TypeError):
            continue

    sorted_agg = sorted(agg.values(), key=lambda x: x["total"], reverse=True)[:20]
    return [
        Contributor(
            name=entry["name"],
            total=entry["total"],
            count=entry["count"],
            type=view,
            employer_name=entry["employer"],
        )
        for entry in sorted_agg
    ]


# ---------------------------------------------------------------------------
# UtahSource
# ---------------------------------------------------------------------------

class UtahSource(BaseSource):
    """Source provider that scrapes disclosures.utah.gov."""

    source_id: str = "utah"
    display_name: str = "Utah"

    def _make_client(self) -> "httpx.AsyncClient":
        """Create a configured httpx async client."""
        return httpx.AsyncClient(
            headers={"User-Agent": _USER_AGENT},
            timeout=_TIMEOUT,
            follow_redirects=True,
        )

    # ------------------------------------------------------------------
    # Search helpers
    # ------------------------------------------------------------------

    async def _search_segment(
        self,
        client: "httpx.AsyncClient",
        name: str,
        year: int,
        entity_type: str,
        prefix: str,
    ) -> list[Candidate]:
        """Search one segment (state or municipal) and return candidates."""
        url = f"{_BASE_URL}/Search/GetEntityList"
        form_data = {
            "searchName": name,
            "entityType": "CAN",
            "reportYear": str(year),
            "municipality": "1" if entity_type == "municipal" else "",
        }
        candidates: list[Candidate] = []

        try:
            resp = await _rate_limited_post(client, url, data=form_data)
            ct = resp.headers.get("content-type", "")

            if "json" in ct:
                data = resp.json()
                items: list[dict] = []
                if isinstance(data, list):
                    items = data
                elif isinstance(data, dict):
                    for key in ("data", "results", "entities", "d"):
                        if isinstance(data.get(key), list):
                            items = data[key]
                            break

                for item in items:
                    entity_id = str(
                        item.get("EntityId") or item.get("entityId") or item.get("id") or ""
                    ).strip()
                    cand_name = str(
                        item.get("EntityName") or item.get("entityName") or item.get("name") or ""
                    ).strip()
                    if not entity_id or not cand_name:
                        continue
                    party = str(item.get("Party") or item.get("party") or "").strip()
                    office = str(item.get("Office") or item.get("office") or "").strip()
                    candidates.append(
                        Candidate(
                            id=f"{prefix}{entity_id}",
                            name=cand_name.title(),
                            party=party,
                            office=office,
                            state="UT",
                            source_id=self.source_id,
                        )
                    )
            else:
                # Fallback: parse HTML
                candidates = _parse_search_html(resp.text, prefix)

        except Exception as exc:
            log.error("Utah search (%s, %s, %s) error: %s", entity_type, name, year, exc)

        return candidates

    # ------------------------------------------------------------------
    # BaseSource interface
    # ------------------------------------------------------------------

    async def search(self, name: str, year: int) -> list[Candidate]:
        """Search disclosures.utah.gov for candidates by name and year.

        Searches both state-level and municipal simultaneously.
        Returns an empty list if scraping dependencies are unavailable.
        """
        if not _SCRAPER_AVAILABLE:
            return []

        async with self._make_client() as client:
            state_task = self._search_segment(client, name, year, "state", "UT-S-")
            # Small gap between the two requests to respect rate limiting
            state_results = await state_task
            municipal_results = await self._search_segment(
                client, name, year, "municipal", "UT-M-"
            )

        return state_results + municipal_results

    async def fetch_contributors(
        self,
        candidate_id: str,
        year: int,
        view: str = "employer",
    ) -> list[Contributor]:
        """Fetch top-20 contributors for a Utah candidate.

        candidate_id must carry the UT-S- or UT-M- prefix assigned during search.
        Returns empty list if scraping dependencies unavailable or ID is malformed.
        """
        if not _SCRAPER_AVAILABLE:
            return []

        # Decode prefix
        if candidate_id.startswith("UT-S-"):
            entity_id = candidate_id[5:]
            segment = "state"
        elif candidate_id.startswith("UT-M-"):
            entity_id = candidate_id[5:]
            segment = "municipal"
        else:
            log.warning("UtahSource.fetch_contributors: unrecognised id %s", candidate_id)
            return []

        if not entity_id.isdigit():
            log.warning("UtahSource.fetch_contributors: non-numeric entity_id %s", entity_id)
            return []

        async with self._make_client() as client:
            # Primary: AJAX contributions endpoint
            try:
                url = f"{_BASE_URL}/GetContributions"
                form_data = {
                    "entityId": entity_id,
                    "reportYear": str(year),
                    "municipality": "1" if segment == "municipal" else "",
                }
                resp = await _rate_limited_post(client, url, data=form_data)
                ct = resp.headers.get("content-type", "")
                if "json" in ct:
                    return _parse_contributions_json(resp.json(), view)
                # JSON parse attempt even if content-type is wrong
                try:
                    return _parse_contributions_json(resp.json(), view)
                except Exception:
                    # Fall through to HTML fallback
                    pass
            except Exception as exc:
                log.warning(
                    "Utah primary contributions fetch failed (entity %s): %s — trying HTML",
                    entity_id, exc,
                )

            # Fallback: parse entity detail page
            try:
                detail_url = f"{_BASE_URL}/Entity/Details/{entity_id}"
                resp = await _rate_limited_get(client, detail_url)
                return _parse_contributions_html(resp.text, view)
            except Exception as exc:
                log.error(
                    "Utah HTML contributions fallback failed (entity %s): %s",
                    entity_id, exc,
                )

        return []

    async def fetch_summary(
        self,
        candidate_id: str,
        year: int,
    ) -> Optional[FinancialSummary]:
        """Fetch financial summary for a Utah candidate by scraping the detail page.

        Returns None if unavailable or on error.
        """
        if not _SCRAPER_AVAILABLE:
            return None

        if candidate_id.startswith("UT-S-"):
            entity_id = candidate_id[5:]
        elif candidate_id.startswith("UT-M-"):
            entity_id = candidate_id[5:]
        else:
            return None

        if not entity_id.isdigit():
            return None

        async with self._make_client() as client:
            try:
                url = f"{_BASE_URL}/Entity/Details/{entity_id}"
                resp = await _rate_limited_get(client, url)
                return self._parse_summary_html(resp.text, candidate_id, year)
            except Exception as exc:
                log.error("Utah fetch_summary (entity %s): %s", entity_id, exc)
                return None

    def _parse_summary_html(
        self,
        html: str,
        candidate_id: str,
        year: int,
    ) -> Optional[FinancialSummary]:
        """Extract financial summary labels from the entity detail page."""
        try:
            soup = BeautifulSoup(html, "html.parser")

            # Extract candidate name from page heading or title
            name = ""
            for tag in ("h1", "h2", "h3"):
                el = soup.find(tag)
                if el:
                    name = el.get_text(strip=True)
                    break
            if not name:
                title_el = soup.find("title")
                name = title_el.get_text(strip=True) if title_el else ""

            # Extract party and office from definition-list or labeled spans
            party = ""
            office = ""
            for label_el in soup.find_all(["dt", "th", "label", "strong"]):
                label_text = label_el.get_text(strip=True).lower()
                sibling = label_el.find_next_sibling()
                value = sibling.get_text(strip=True) if sibling else ""
                if "party" in label_text:
                    party = value
                elif "office" in label_text or "position" in label_text:
                    office = value

            # Financial totals — look for labeled elements
            total_raised = 0.0
            total_spent = 0.0
            cash_on_hand = 0.0
            debt = 0.0

            _label_map = {
                "total contributions": "raised",
                "total receipts": "raised",
                "total raised": "raised",
                "total expenditures": "spent",
                "total disbursements": "spent",
                "total spent": "spent",
                "cash on hand": "cash",
                "ending balance": "cash",
                "total debt": "debt",
                "loans": "debt",
            }

            for label_el in soup.find_all(["dt", "th", "td", "label", "strong", "span"]):
                label_text = label_el.get_text(strip=True).lower()
                for key, field in _label_map.items():
                    if key in label_text:
                        sibling = label_el.find_next_sibling()
                        if sibling:
                            val = float(_parse_money(sibling.get_text(strip=True)))
                            if field == "raised":
                                total_raised = val
                            elif field == "spent":
                                total_spent = val
                            elif field == "cash":
                                cash_on_hand = val
                            elif field == "debt":
                                debt = val
                        break

            if not name and not total_raised:
                return None

            return FinancialSummary(
                candidate=name,
                candidate_id=candidate_id,
                cycle=int(year),
                total_raised=total_raised,
                total_spent=total_spent,
                cash_on_hand=cash_on_hand,
                debt=debt,
                party=party,
                state="UT",
                office=office,
                source_id=self.source_id,
            )

        except Exception as exc:
            log.error("Utah _parse_summary_html error: %s", exc)
            return None


# ---------------------------------------------------------------------------
# Auto-register on import (only when scraping deps are present)
# ---------------------------------------------------------------------------
if _SCRAPER_AVAILABLE:
    register_source(UtahSource())
