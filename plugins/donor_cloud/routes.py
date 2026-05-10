"""
LinguaTaxi — Donor Cloud Plugin Routes
GET  /api/donor-cloud/status       — health check
GET  /api/donor-cloud/search       — search candidates by name
GET  /api/donor-cloud/contributors — top contributors for a candidate
GET  /api/donor-cloud/summary      — candidate funding summary

Uses the FEC (Federal Election Commission) public API with the built-in
DEMO_KEY — no signup or API key required.
"""

import asyncio
import logging
import threading
import time
from fastapi import APIRouter, HTTPException, Query

log = logging.getLogger("livecaption")

router = APIRouter(prefix="/api")

_plugin_settings = {}

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


def _get_cycle():
    cycle = _plugin_settings.get("cycle", "")
    if not cycle:
        cycle = "2024"
    try:
        return str(int(cycle))
    except (ValueError, TypeError):
        return "2024"


def _fec_request(cache_key: str, path: str, params: dict) -> dict | None:
    import requests as req

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
        resp = req.get(url, params=params, timeout=20)
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


def _get_principal_committee(candidate_id: str) -> str | None:
    data = _fec_request(
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


def _format_name(raw: str) -> str:
    """Convert 'LAST, FIRST' FEC format to 'First Last'."""
    if "," in raw:
        parts = raw.split(",", 1)
        return (parts[1].strip() + " " + parts[0].strip()).title()
    return raw.title()


# ── Routes ──

@router.get("/donor-cloud/status")
def donor_cloud_status():
    return {
        "status": "ok",
        "data_source": "FEC (api.open.fec.gov)",
        "api_key_required": False,
        "cycle": _get_cycle(),
    }


@router.get("/donor-cloud/search")
async def search_candidates(name: str = Query(..., min_length=2)):
    def _do_search():
        data = _fec_request(
            f"search:{name.lower()}",
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
        results = []
        for c in data.get("results", []):
            cid = c.get("candidate_id", "")
            raw_name = c.get("name", "")
            if not cid or not raw_name:
                continue
            results.append({
                "cid": cid,
                "name": _format_name(raw_name),
                "party": (c.get("party_full") or c.get("party") or ""),
                "state": c.get("state") or "",
                "office": c.get("office_full") or c.get("office") or "",
            })
        return results

    loop = asyncio.get_running_loop()
    candidates = await loop.run_in_executor(None, _do_search)
    return {"candidates": candidates, "query": name}


@router.get("/donor-cloud/contributors")
def get_contributors(
    cid: str = Query(..., min_length=5, description="FEC Candidate ID"),
    cycle: str | None = Query(None, description="Election cycle year"),
):
    use_cycle = cycle or _get_cycle()

    committee_id = _get_principal_committee(cid)
    if not committee_id:
        raise HTTPException(status_code=404, detail=f"No principal committee found for {cid}")

    data = _fec_request(
        f"contrib:{committee_id}:{use_cycle}",
        "/schedules/schedule_a/by_employer/",
        {
            "committee_id": committee_id,
            "cycle": use_cycle,
            "sort": "-total",
            "per_page": 50,
        },
    )
    if not data:
        raise HTTPException(status_code=502, detail="Failed to fetch contributor data from FEC.")

    contributors = []
    for entry in data.get("results", []):
        employer = (entry.get("employer") or "").strip().upper()
        if employer in _SKIP_EMPLOYERS:
            continue
        total = entry.get("total", 0)
        count = entry.get("count", 0)
        if total > 0 and employer:
            contributors.append({
                "name": employer.title(),
                "total": int(total),
                "count": count,
                "pacs": 0,
                "indivs": int(total),
            })
        if len(contributors) >= 20:
            break

    cand_data = _fec_request(f"cand:{cid}", f"/candidate/{cid}/", {})
    cand_name = ""
    if cand_data and cand_data.get("results"):
        cand_name = _format_name(cand_data["results"][0].get("name", ""))

    return {
        "candidate": cand_name,
        "cid": cid,
        "cycle": use_cycle,
        "contributors": contributors,
    }


@router.get("/donor-cloud/summary")
def get_summary(
    cid: str = Query(..., min_length=5, description="FEC Candidate ID"),
    cycle: str | None = Query(None, description="Election cycle year"),
):
    use_cycle = cycle or _get_cycle()
    data = _fec_request(
        f"totals:{cid}:{use_cycle}",
        f"/candidate/{cid}/totals/",
        {"cycle": use_cycle, "per_page": 1},
    )
    if not data:
        raise HTTPException(status_code=502, detail="Failed to fetch summary from FEC.")

    results = data.get("results", [])
    if not results:
        raise HTTPException(status_code=404, detail="No financial data found for this candidate/cycle.")

    s = results[0]
    cand_data = _fec_request(f"cand:{cid}", f"/candidate/{cid}/", {})
    cand_name = ""
    party = ""
    state = ""
    chamber = ""
    if cand_data and cand_data.get("results"):
        c = cand_data["results"][0]
        cand_name = _format_name(c.get("name", ""))
        party = c.get("party_full") or c.get("party") or ""
        state = c.get("state") or ""
        chamber = c.get("office_full") or ""

    return {
        "candidate": cand_name,
        "cid": cid,
        "cycle": use_cycle,
        "total": int(s.get("receipts", 0)),
        "spent": int(s.get("disbursements", 0)),
        "cash_on_hand": int(s.get("cash_on_hand_end_period", 0)),
        "debt": int(s.get("debts_owed_by_committee", 0)),
        "party": party,
        "state": state,
        "chamber": chamber,
    }


def handle_event(event_name, data, settings):
    global _plugin_settings
    if event_name == "on_config_change":
        _plugin_settings = settings
    elif event_name == "on_shutdown":
        _plugin_settings = settings
        with _cache_lock:
            _cache.clear()
