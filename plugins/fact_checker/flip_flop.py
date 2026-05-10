"""
LinguaTaxi Fact Checker — Flip-Flop Detection Extension

Caches speaker dossiers (public statement history) upfront so live fact-checks
can instantly compare current statements to past positions without running a
fresh web search per check.

Architecture:
  - One-time AI-powered web search per speaker builds a dossier of past statements
  - Dossiers persisted to disk (survive restarts) with configurable TTL
  - At fact-check time, the speaker's dossier is injected into the prompt
  - AI returns a `flip_flop` field with contradictions detected

Public API:
  - get_dossier(name) -> dict | None
  - has_dossier(name) -> bool
  - prefetch_speakers(names, fetch_fn, api_key) — queue background fetches
  - dossier_status() -> {cached: [...], pending: [...], in_progress: [...]}
  - format_dossier_for_prompt(dossier) -> str
  - parse_flip_flop_from_result(result) -> enriched flip_flop field
"""

import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

log = logging.getLogger("livecaption")

# ── Persistence ──
_DATA_DIR = Path(__file__).parent / "data"
_DOSSIERS_FILE = _DATA_DIR / "dossiers.json"

# ── State ──
_dossiers: dict[str, dict] = {}   # name -> {fetched_at, statements, positions}
_dossiers_lock = threading.Lock()
_in_progress: set[str] = set()    # names currently being fetched
_failed: dict[str, float] = {}    # name -> last failure time (monotonic)
_failed_cooldown = 600.0          # 10 min cooldown after failure

_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="flipflop-prefetch")

# Default dossier TTL: 7 days (political positions don't change hourly)
DEFAULT_TTL_HOURS = 168


# ── System prompt for dossier building ──

_DOSSIER_PROMPT = """You are building a concise dossier of public political statements by a specific person.

Use web search to find their public statements from the past 5 years across:
- Twitter/X posts (current and archived)
- Speeches and press conferences
- Interviews (TV, print, podcasts)
- Official press releases and policy papers
- Congressional records and voting positions (if applicable)

Return ONLY valid JSON — no markdown, no backticks, no preamble:
{
  "statements": [
    {
      "date": "YYYY-MM or YYYY-MM-DD",
      "topic": "short topic label (e.g., 'border security', 'healthcare', 'tariffs')",
      "quote": "direct quote or close paraphrase, 25 words max",
      "source": "Twitter | Press conference | CNN interview | etc.",
      "url": "direct URL to source or null"
    }
  ],
  "positions": {
    "topic": "2-3 sentence summary of their stated position on this topic over time, noting any shifts"
  }
}

Rules:
- Aim for 20-30 of their most significant statements covering their main policy positions
- Prefer dated, sourced statements over paraphrases
- Include statements that have changed over time (flip-flops) — these are specifically what we're looking for
- Cover at least: foreign policy, economy, healthcare, immigration, social issues, any signature issues
- If you cannot verify a quote, exclude it — never fabricate
- If the person has no significant public political profile, return {"statements": [], "positions": {}}
"""


# ── Persistence ──

def _load_from_disk():
    """Load cached dossiers from disk on startup."""
    if not _DOSSIERS_FILE.exists():
        return
    try:
        with open(_DOSSIERS_FILE, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            with _dossiers_lock:
                _dossiers.clear()
                _dossiers.update(data)
            log.info(f"[Flip-Flop] Loaded {len(data)} dossiers from disk")
    except Exception as e:
        log.warning(f"[Flip-Flop] Failed to load dossiers from disk: {e}")


def _save_to_disk():
    """Persist dossiers to disk (atomic via temp + rename)."""
    try:
        _DATA_DIR.mkdir(parents=True, exist_ok=True)
        tmp = _DOSSIERS_FILE.with_suffix(".tmp")
        with _dossiers_lock:
            snapshot = dict(_dossiers)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(snapshot, f, indent=2, ensure_ascii=False)
        tmp.replace(_DOSSIERS_FILE)
    except Exception as e:
        log.warning(f"[Flip-Flop] Failed to save dossiers: {e}")


# Load on module import
_load_from_disk()


# ── Dossier lookups ──

def has_dossier(name: str, ttl_hours: int = DEFAULT_TTL_HOURS) -> bool:
    """Return True if a fresh dossier exists for this speaker."""
    if not name:
        return False
    with _dossiers_lock:
        d = _dossiers.get(name)
    if not d:
        return False
    age_hours = (time.time() - d.get("fetched_at", 0)) / 3600
    return age_hours < ttl_hours


def get_dossier(name: str) -> dict | None:
    """Return dossier dict or None if not cached."""
    if not name:
        return None
    with _dossiers_lock:
        return _dossiers.get(name)


def get_all_dossiers() -> dict[str, dict]:
    """Return a snapshot of all cached dossiers (metadata only)."""
    with _dossiers_lock:
        return {
            name: {
                "fetched_at": d.get("fetched_at", 0),
                "statement_count": len(d.get("statements", [])),
                "topics": list(d.get("positions", {}).keys()),
            }
            for name, d in _dossiers.items()
        }


def store_dossier(name: str, data: dict):
    """Store a freshly fetched dossier with timestamp."""
    if not name or not isinstance(data, dict):
        return
    entry = {
        "fetched_at": time.time(),
        "statements": data.get("statements", []) or [],
        "positions": data.get("positions", {}) or {},
    }
    with _dossiers_lock:
        _dossiers[name] = entry
        _failed.pop(name, None)
    _save_to_disk()
    log.info(f"[Flip-Flop] Stored dossier for '{name}' "
             f"({len(entry['statements'])} statements, {len(entry['positions'])} topics)")


def remove_dossier(name: str) -> bool:
    """Remove a cached dossier. Returns True if one was removed."""
    with _dossiers_lock:
        removed = _dossiers.pop(name, None) is not None
    if removed:
        _save_to_disk()
    return removed


def clear_all():
    """Remove all cached dossiers."""
    with _dossiers_lock:
        _dossiers.clear()
    _save_to_disk()


# ── Prefetch orchestration ──

def status() -> dict:
    """Return current status of dossier cache + prefetch queue."""
    with _dossiers_lock:
        cached = list(_dossiers.keys())
        in_progress = list(_in_progress)
    return {
        "cached": cached,
        "in_progress": in_progress,
        "cached_count": len(cached),
        "dossiers": get_all_dossiers(),
    }


def queue_prefetch(names: list[str], fetch_fn, ttl_hours: int = DEFAULT_TTL_HOURS):
    """Queue background prefetching for a list of speaker names.

    Args:
        names: speaker display names
        fetch_fn: callable(name) -> dict | None  — does the actual AI call
        ttl_hours: consider existing dossiers fresh if within this age
    """
    names = [n for n in (names or []) if n and isinstance(n, str)]
    queued = []
    for name in names:
        with _dossiers_lock:
            if has_dossier(name, ttl_hours):
                continue  # fresh enough
            if name in _in_progress:
                continue
            # Check failure cooldown
            last_fail = _failed.get(name, 0)
            if last_fail and (time.monotonic() - last_fail) < _failed_cooldown:
                continue
            _in_progress.add(name)
        queued.append(name)
        _pool.submit(_fetch_one, name, fetch_fn)
    return queued


def _fetch_one(name: str, fetch_fn):
    """Worker: fetch one dossier and store it."""
    try:
        log.info(f"[Flip-Flop] Fetching dossier for '{name}'...")
        result = fetch_fn(name)
        if result and isinstance(result, dict) and "statements" in result:
            store_dossier(name, result)
        else:
            with _dossiers_lock:
                _failed[name] = time.monotonic()
            log.warning(f"[Flip-Flop] Dossier fetch for '{name}' returned no data")
    except Exception as e:
        with _dossiers_lock:
            _failed[name] = time.monotonic()
        log.error(f"[Flip-Flop] Dossier fetch for '{name}' failed: {e}")
    finally:
        with _dossiers_lock:
            _in_progress.discard(name)


def shutdown():
    """Shutdown the prefetch pool."""
    _pool.shutdown(wait=False)


# ── Prompt integration ──

def get_dossier_prompt() -> str:
    """Return the system prompt used when building a dossier."""
    return _DOSSIER_PROMPT


def format_dossier_for_factcheck(dossier: dict, speaker_name: str, max_statements: int = 25) -> str:
    """Format a speaker's dossier into a prompt snippet for flip-flop detection."""
    if not dossier or not speaker_name:
        return ""
    statements = dossier.get("statements", [])[:max_statements]
    positions = dossier.get("positions", {})

    lines = [f"\n--- SPEAKER HISTORY: {speaker_name} ---"]

    if positions:
        lines.append("Known positions:")
        for topic, summary in positions.items():
            lines.append(f"  - {topic}: {summary}")

    if statements:
        lines.append("\nPast statements:")
        for s in statements:
            date = s.get("date", "?")
            topic = s.get("topic", "?")
            quote = s.get("quote", "")
            source = s.get("source", "?")
            lines.append(f"  [{date}] ({topic}, {source}): \"{quote}\"")

    lines.append("--- END SPEAKER HISTORY ---\n")
    lines.append(
        "In addition to fact-checking the current statement, compare it to the speaker's past "
        "positions. If the current statement contradicts, evolves from, or materially changes their "
        "earlier position, include a 'flip_flop' field in your JSON response:\n"
        '{\n'
        '  "flip_flop": {\n'
        '    "detected": true | false,\n'
        '    "confidence": 0-1,\n'
        '    "type": "reversal" | "evolution" | "qualification" | "consistent",\n'
        '    "past_statements": [ { "date", "quote", "source", "url" } ],\n'
        '    "summary": "1-2 sentence explanation of how current differs from past"\n'
        '  }\n'
        '}\n'
        'Be conservative — only flag a flip-flop when there is a clear contradiction or material '
        'position change. Legitimate position evolution based on new facts is "evolution", not "reversal". '
        'If no past statements are relevant to the current one, set detected=false with type="consistent".'
    )
    return "\n".join(lines)
