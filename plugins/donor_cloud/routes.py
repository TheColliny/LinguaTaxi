"""
LinguaTaxi — Donor Cloud Plugin Routes (v3)

Multi-source dispatch to registered source providers.
Manages an in-memory TTL cache on top of the persistent disk cache.

GET  /api/donor-cloud/status              — sources list + current cycle
GET  /api/donor-cloud/search?name=X&source=Y  — search candidates (all or one source)
GET  /api/donor-cloud/contributors?cid=X&source=Y&cycle=Z&view=employer|individual
GET  /api/donor-cloud/summary?cid=X&source=Y&cycle=Z
GET  /api/donor-cloud/roster              — return roster.json contents
POST /api/donor-cloud/roster/event        — create/rename/delete events
POST /api/donor-cloud/roster/candidate    — add/remove candidates
POST /api/donor-cloud/roster/refresh      — force-refresh stale candidates for an event
POST /api/donor-cloud/roster/load-event   — check for stale data on an event's candidates
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import logging
import sys
import threading
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request

log = logging.getLogger("livecaption")

# ---------------------------------------------------------------------------
# Dynamic imports — plugin is loaded by importlib, not regular import chain
# ---------------------------------------------------------------------------

_plugin_dir = Path(__file__).parent
_sources_dir = _plugin_dir / "sources"

# Load sources framework (__init__.py — data models + registry)
_src_spec = importlib.util.spec_from_file_location(
    "donor_cloud_sources", str(_sources_dir / "__init__.py")
)
_src_mod = importlib.util.module_from_spec(_src_spec)
sys.modules["donor_cloud_sources"] = _src_mod
_src_spec.loader.exec_module(_src_mod)

# Load FEC source (auto-registers itself)
_fec_spec = importlib.util.spec_from_file_location(
    "donor_cloud_fec", str(_sources_dir / "fec.py")
)
_fec_mod = importlib.util.module_from_spec(_fec_spec)
sys.modules["donor_cloud_fec"] = _fec_mod
_fec_spec.loader.exec_module(_fec_mod)

# Load Utah source (auto-registers if httpx/bs4 available)
_utah_spec = importlib.util.spec_from_file_location(
    "donor_cloud_utah", str(_sources_dir / "utah.py")
)
_utah_mod = importlib.util.module_from_spec(_utah_spec)
sys.modules["donor_cloud_utah"] = _utah_mod
_utah_spec.loader.exec_module(_utah_mod)

# Load cache
_cache_spec = importlib.util.spec_from_file_location(
    "donor_cloud_cache", str(_plugin_dir / "cache.py")
)
_cache_mod = importlib.util.module_from_spec(_cache_spec)
sys.modules["donor_cloud_cache"] = _cache_mod
_cache_spec.loader.exec_module(_cache_mod)

# Aliases for cleaner usage
from donor_cloud_sources import (  # noqa: E402
    Candidate, Contributor, FinancialSummary,
    get_all_sources, get_source, SOURCE_REGISTRY,
)
from donor_cloud_cache import DiskCache  # noqa: E402

# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

router = APIRouter(prefix="/api")

# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

_plugin_settings: dict = {}

_disk_cache: DiskCache | None = None
_disk_cache_lock = threading.Lock()

# In-memory TTL cache: key -> (payload_dict, timestamp)
_mem_cache: dict[str, tuple[dict, float]] = {}
_mem_lock = threading.Lock()
_MEM_TTL = 3600  # seconds

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_cycle() -> str:
    cycle = _plugin_settings.get("cycle", "")
    if not cycle:
        return "2024"
    try:
        return str(int(cycle))
    except (ValueError, TypeError):
        return "2024"


def _get_staleness_hours() -> float:
    try:
        return float(_plugin_settings.get("staleness_hours", 24))
    except (ValueError, TypeError):
        return 24.0


def _mem_get(key: str) -> dict | None:
    now = time.monotonic()
    with _mem_lock:
        entry = _mem_cache.get(key)
        if entry is None:
            return None
        payload, ts = entry
        if now - ts >= _MEM_TTL:
            del _mem_cache[key]
            return None
        return payload


def _mem_set(key: str, payload: dict) -> None:
    with _mem_lock:
        _mem_cache[key] = (payload, time.monotonic())


def _mem_clear() -> None:
    with _mem_lock:
        _mem_cache.clear()


def _init_cache() -> None:
    global _disk_cache
    cache_dir = str(_plugin_dir / "data" / "cache")
    staleness = _get_staleness_hours()
    with _disk_cache_lock:
        _disk_cache = DiskCache(cache_dir=cache_dir, staleness_hours=staleness)


# ---------------------------------------------------------------------------
# Roster helpers
# ---------------------------------------------------------------------------

_ROSTER_PATH = _plugin_dir / "data" / "roster.json"


def _load_roster() -> dict:
    try:
        if _ROSTER_PATH.exists():
            return json.loads(_ROSTER_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        pass
    return {"events": []}


def _save_roster(roster: dict) -> None:
    _ROSTER_PATH.parent.mkdir(parents=True, exist_ok=True)
    _ROSTER_PATH.write_text(json.dumps(roster, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/donor-cloud/status")
def donor_cloud_status():
    sources = [
        {"id": s.source_id, "name": s.display_name}
        for s in get_all_sources()
    ]
    return {
        "status": "ok",
        "sources": sources,
        "cycle": _get_cycle(),
    }


@router.get("/donor-cloud/search")
async def search_candidates(
    name: str = Query(..., min_length=2),
    source: str | None = Query(None),
):
    year = int(_get_cycle())

    if source:
        src = get_source(source)
        if not src:
            raise HTTPException(status_code=404, detail=f"Unknown source: {source}")
        results = await src.search(name, year)
    else:
        tasks = [s.search(name, year) for s in get_all_sources()]
        gathered = await asyncio.gather(*tasks, return_exceptions=True)
        results = []
        for r in gathered:
            if isinstance(r, list):
                results.extend(r)

    return {
        "candidates": [c.to_dict() for c in results],
        "query": name,
    }


@router.get("/donor-cloud/contributors")
async def get_contributors(
    cid: str = Query(..., min_length=3, description="Candidate ID"),
    source: str = Query(..., description="Source ID (e.g. 'fec' or 'utah')"),
    cycle: str | None = Query(None, description="Election cycle year"),
    view: str = Query("employer", description="'employer' or 'individual'"),
):
    use_cycle = cycle or _get_cycle()
    mem_key = f"contributors:{source}:{cid}:{use_cycle}:{view}"

    cached = _mem_get(mem_key)
    if cached:
        return cached

    src = get_source(source)
    contributors: list[Contributor] | None = None

    if src:
        try:
            contributors = await src.fetch_contributors(cid, int(use_cycle), view)
        except Exception as e:
            log.error("Donor Cloud fetch_contributors error (%s/%s): %s", source, cid, e)

    # Fall back to disk cache if source failed or unavailable
    if contributors is None and _disk_cache:
        disk_data = _disk_cache.load(source, cid)
        if disk_data:
            payload = {
                "candidate": disk_data.get("candidate", {}),
                "cid": cid,
                "cycle": use_cycle,
                "source": source,
                "contributors": disk_data.get("contributors", []),
                "_from_cache": True,
            }
            _mem_set(mem_key, payload)
            return payload

    if contributors is None:
        raise HTTPException(
            status_code=502,
            detail=f"Failed to fetch contributor data from source '{source}'.",
        )

    # Persist to disk on success
    if _disk_cache and contributors is not None:
        try:
            cand_obj = Candidate(
                id=cid, name="", party="", office="", state="", source_id=source
            )
            _disk_cache.save(cand_obj, contributors, None)
        except Exception as e:
            log.warning("Donor Cloud disk_cache.save failed: %s", e)

    payload = {
        "cid": cid,
        "cycle": use_cycle,
        "source": source,
        "contributors": [c.to_dict() for c in contributors],
    }
    _mem_set(mem_key, payload)
    return payload


@router.get("/donor-cloud/summary")
async def get_summary(
    cid: str = Query(..., min_length=3, description="Candidate ID"),
    source: str = Query(..., description="Source ID"),
    cycle: str | None = Query(None, description="Election cycle year"),
):
    use_cycle = cycle or _get_cycle()
    mem_key = f"summary:{source}:{cid}:{use_cycle}"

    cached = _mem_get(mem_key)
    if cached:
        return cached

    src = get_source(source)
    summary: FinancialSummary | None = None

    if src:
        try:
            summary = await src.fetch_summary(cid, int(use_cycle))
        except Exception as e:
            log.error("Donor Cloud fetch_summary error (%s/%s): %s", source, cid, e)

    # Fall back to disk cache
    if summary is None and _disk_cache:
        disk_data = _disk_cache.load(source, cid)
        if disk_data and disk_data.get("summary"):
            payload = {**disk_data["summary"], "_from_cache": True}
            _mem_set(mem_key, payload)
            return payload

    if summary is None:
        raise HTTPException(
            status_code=404,
            detail=f"No financial data found for candidate '{cid}' (source: {source}, cycle: {use_cycle}).",
        )

    payload = summary.to_dict()
    _mem_set(mem_key, payload)
    return payload


# ---------------------------------------------------------------------------
# Roster routes
# ---------------------------------------------------------------------------

@router.get("/donor-cloud/roster")
def get_roster():
    return _load_roster()


@router.post("/donor-cloud/roster/event")
async def roster_event(request: Request):
    """Create, rename, or delete an event.

    Body: {action: "create"|"rename"|"delete", name: str, date: str, index: int}
    """
    body = await request.json()
    action = body.get("action", "")
    roster = _load_roster()
    events = roster.setdefault("events", [])

    if action == "create":
        name = body.get("name", "").strip()
        date = body.get("date", "").strip()
        if not name:
            raise HTTPException(status_code=400, detail="Event name is required.")
        events.append({"name": name, "date": date, "candidates": []})
        _save_roster(roster)
        return {"ok": True, "events": events}

    elif action == "rename":
        idx = body.get("index")
        name = body.get("name", "").strip()
        if idx is None or not isinstance(idx, int) or idx < 0 or idx >= len(events):
            raise HTTPException(status_code=400, detail="Invalid event index.")
        if not name:
            raise HTTPException(status_code=400, detail="Event name is required.")
        events[idx]["name"] = name
        if "date" in body:
            events[idx]["date"] = body["date"]
        _save_roster(roster)
        return {"ok": True, "events": events}

    elif action == "delete":
        idx = body.get("index")
        if idx is None or not isinstance(idx, int) or idx < 0 or idx >= len(events):
            raise HTTPException(status_code=400, detail="Invalid event index.")
        events.pop(idx)
        _save_roster(roster)
        return {"ok": True, "events": events}

    else:
        raise HTTPException(status_code=400, detail=f"Unknown action: {action!r}")


@router.post("/donor-cloud/roster/candidate")
async def roster_candidate(request: Request):
    """Add or remove a candidate from an event.

    Body: {
        action: "add"|"remove",
        event_index: int,
        candidate: {name, source, candidate_id}
    }
    On add, immediately fetches and caches the candidate's contributors/summary.
    """
    body = await request.json()
    action = body.get("action", "")
    roster = _load_roster()
    events = roster.setdefault("events", [])

    event_idx = body.get("event_index")
    if event_idx is None or not isinstance(event_idx, int) or event_idx < 0 or event_idx >= len(events):
        raise HTTPException(status_code=400, detail="Invalid event_index.")

    event = events[event_idx]
    candidates = event.setdefault("candidates", [])

    if action == "add":
        cand = body.get("candidate", {})
        cand_name = cand.get("name", "").strip()
        source_id = cand.get("source", "").strip()
        candidate_id = cand.get("candidate_id", "").strip()
        if not cand_name or not source_id or not candidate_id:
            raise HTTPException(
                status_code=400,
                detail="Candidate requires name, source, and candidate_id.",
            )

        candidates.append({
            "name": cand_name,
            "source": source_id,
            "candidate_id": candidate_id,
        })
        _save_roster(roster)

        # Prefetch and cache candidate data in the background
        use_cycle = int(_get_cycle())
        src = get_source(source_id)
        if src and _disk_cache:
            try:
                contributors = await src.fetch_contributors(candidate_id, use_cycle)
                summary = await src.fetch_summary(candidate_id, use_cycle)
                cand_obj = Candidate(
                    id=candidate_id,
                    name=cand_name,
                    party="",
                    office="",
                    state="",
                    source_id=source_id,
                )
                _disk_cache.save(cand_obj, contributors or [], summary)
                log.info(
                    "Donor Cloud: pre-cached %s/%s for event '%s'",
                    source_id, candidate_id, event["name"],
                )
            except Exception as e:
                log.warning(
                    "Donor Cloud: pre-cache failed for %s/%s: %s",
                    source_id, candidate_id, e,
                )

        return {"ok": True, "event": event}

    elif action == "remove":
        cand = body.get("candidate", {})
        candidate_id = cand.get("candidate_id", "").strip()
        source_id = cand.get("source", "").strip()
        before = len(candidates)
        event["candidates"] = [
            c for c in candidates
            if not (c.get("candidate_id") == candidate_id and c.get("source") == source_id)
        ]
        _save_roster(roster)
        return {"ok": True, "removed": before - len(event["candidates"]), "event": event}

    else:
        raise HTTPException(status_code=400, detail=f"Unknown action: {action!r}")


@router.post("/donor-cloud/roster/refresh")
async def roster_refresh(request: Request):
    """Force re-fetch all candidates for an event.

    Body: {event_index: int}
    """
    body = await request.json()
    roster = _load_roster()
    events = roster.get("events", [])

    event_idx = body.get("event_index")
    if event_idx is None or not isinstance(event_idx, int) or event_idx < 0 or event_idx >= len(events):
        raise HTTPException(status_code=400, detail="Invalid event_index.")

    event = events[event_idx]
    refreshed = []
    failed = []
    use_cycle = int(_get_cycle())

    for cand in event.get("candidates", []):
        source_id = cand.get("source", "")
        candidate_id = cand.get("candidate_id", "")
        cand_name = cand.get("name", "")
        src = get_source(source_id)
        if not src:
            failed.append(candidate_id)
            continue
        try:
            contributors = await src.fetch_contributors(candidate_id, use_cycle)
            summary = await src.fetch_summary(candidate_id, use_cycle)
            if _disk_cache:
                cand_obj = Candidate(
                    id=candidate_id,
                    name=cand_name,
                    party="",
                    office="",
                    state="",
                    source_id=source_id,
                )
                _disk_cache.save(cand_obj, contributors or [], summary)
            # Invalidate mem cache for this candidate
            for view in ("employer", "individual"):
                _mem_cache.pop(
                    f"contributors:{source_id}:{candidate_id}:{use_cycle}:{view}", None
                )
            _mem_cache.pop(f"summary:{source_id}:{candidate_id}:{use_cycle}", None)
            refreshed.append(candidate_id)
        except Exception as e:
            log.warning("Donor Cloud refresh failed (%s/%s): %s", source_id, candidate_id, e)
            failed.append(candidate_id)

    return {"ok": True, "refreshed": refreshed, "failed": failed}


@router.post("/donor-cloud/roster/load-event")
async def roster_load_event(request: Request):
    """Check for stale candidates in an event; re-fetch any that are stale.

    Body: {event_index: int}
    """
    body = await request.json()
    roster = _load_roster()
    events = roster.get("events", [])

    event_idx = body.get("event_index")
    if event_idx is None or not isinstance(event_idx, int) or event_idx < 0 or event_idx >= len(events):
        raise HTTPException(status_code=400, detail="Invalid event_index.")

    event = events[event_idx]
    refreshed = []
    already_fresh = []
    failed = []
    use_cycle = int(_get_cycle())

    for cand in event.get("candidates", []):
        source_id = cand.get("source", "")
        candidate_id = cand.get("candidate_id", "")
        cand_name = cand.get("name", "")

        if _disk_cache and not _disk_cache.is_stale(source_id, candidate_id):
            already_fresh.append(candidate_id)
            continue

        src = get_source(source_id)
        if not src:
            failed.append(candidate_id)
            continue
        try:
            contributors = await src.fetch_contributors(candidate_id, use_cycle)
            summary = await src.fetch_summary(candidate_id, use_cycle)
            if _disk_cache:
                cand_obj = Candidate(
                    id=candidate_id,
                    name=cand_name,
                    party="",
                    office="",
                    state="",
                    source_id=source_id,
                )
                _disk_cache.save(cand_obj, contributors or [], summary)
            refreshed.append(candidate_id)
        except Exception as e:
            log.warning(
                "Donor Cloud load-event refresh failed (%s/%s): %s",
                source_id, candidate_id, e,
            )
            failed.append(candidate_id)

    return {
        "ok": True,
        "refreshed": refreshed,
        "already_fresh": already_fresh,
        "failed": failed,
    }


# ---------------------------------------------------------------------------
# Event hooks
# ---------------------------------------------------------------------------

def handle_event(event_name: str, data: dict, settings: dict) -> None:
    global _plugin_settings

    if event_name in ("on_config_change", "on_startup", "on_enabled"):
        _plugin_settings = settings

    if event_name == "on_startup":
        _init_cache()

    elif event_name == "on_enabled":
        _init_cache()

    elif event_name == "on_config_change":
        # Re-init cache if staleness setting changed
        if _disk_cache is not None:
            new_staleness = _get_staleness_hours()
            if _disk_cache.staleness_hours != new_staleness:
                _init_cache()

    elif event_name == "on_shutdown":
        _mem_clear()
