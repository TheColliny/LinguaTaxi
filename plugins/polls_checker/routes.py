"""
LinguaTaxi — Polls Checker Plugin Routes
POST /api/polls/check   — search for polling data on an opinion claim
GET  /api/polls/status  — health check + provider status

When a speaker claims "Americans want X" or "most people think Y", this plugin
searches for actual polling data from recognized, non-partisan polling organizations
and returns what the polls really say.

Uses Gemini (free) or Claude (paid) with web search to find recent polls.
"""

import asyncio
import collections
import json
import logging
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

log = logging.getLogger("livecaption")

router = APIRouter(prefix="/api")

# ── Thread pool ──
_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="polls")

# ── Plugin settings ──
_plugin_settings = {}

# ── Rate limiter ──
_rate_lock = threading.Lock()
_rate_timestamps: collections.deque = collections.deque()
_RATE_WINDOW = 60


def _check_rate_limit():
    now = time.monotonic()
    limit = _plugin_settings.get("rate_limit", 8)
    try:
        limit = int(limit)
    except (ValueError, TypeError):
        limit = 8
    with _rate_lock:
        while _rate_timestamps and now - _rate_timestamps[0] >= _RATE_WINDOW:
            _rate_timestamps.popleft()
        if len(_rate_timestamps) >= limit:
            return False
        _rate_timestamps.append(now)
        return True


# ── Settings helpers ──

def _get_provider():
    p = _plugin_settings.get("provider", "gemini").strip().lower()
    return p if p in ("gemini", "claude") else "gemini"


def _get_gemini_key():
    key = _plugin_settings.get("gemini_api_key", "")
    return key or os.environ.get("GEMINI_API_KEY", "")


def _get_anthropic_key():
    key = _plugin_settings.get("anthropic_api_key", "")
    return key or os.environ.get("ANTHROPIC_API_KEY", "")


def _auto_check_enabled():
    val = _plugin_settings.get("auto_check", "true")
    return str(val).lower() in ("true", "1", "yes", "on")


# ── Pollster credibility ratings ──
# Based on FiveThirtyEight pollster ratings + general reputation
# A+ = gold standard non-partisan, A = strong, B = decent, C = partisan lean

POLLSTER_RATINGS = {
    # A+ tier — non-partisan gold standard
    "pew research": "A+", "pew": "A+",
    "gallup": "A+",
    "ap-norc": "A+", "ap norc": "A+",
    "marist": "A+", "marist college": "A+",
    "monmouth": "A+", "monmouth university": "A+",
    "quinnipiac": "A+", "quinnipiac university": "A+",
    "siena college": "A+",
    "marquette law school": "A+",
    # A tier — strong methodology
    "abc news": "A", "abc/washington post": "A", "abc/ipsos": "A",
    "cbs news": "A", "cbs/yougov": "A",
    "nbc news": "A", "nbc/wall street journal": "A",
    "cnn": "A", "cnn/ssrs": "A",
    "fox news": "A", "fox news/beacon": "A",
    "npr": "A", "npr/pbs": "A", "npr/marist": "A+",
    "yougov": "A", "the economist/yougov": "A",
    "ipsos": "A", "reuters/ipsos": "A",
    "morning consult": "A",
    "kaiser family foundation": "A", "kff": "A",
    # B tier — decent but some methodology concerns
    "rasmussen": "B", "rasmussen reports": "B",
    "emerson": "B", "emerson college": "B",
    "trafalgar": "B", "trafalgar group": "B",
    "suffolk university": "B",
    "harris poll": "B", "harris": "B",
    "zogby": "B",
    "grinnell college": "B",
    # C tier — known partisan lean or poor methodology
    "heritage foundation": "C",
    "daily kos": "C",
    "democracy corps": "C",
    "mclaughlin": "C",
}

RATING_LABELS = {
    "A+": "Gold Standard",
    "A": "Highly Rated",
    "B": "Moderate",
    "C": "Partisan Lean",
}


def _rate_pollster(org_name: str) -> dict:
    """Look up a polling organization's credibility rating.
    Uses word-boundary matching to avoid false positives from short substrings."""
    name_lower = org_name.lower().strip()
    if not name_lower:
        return {"rating": "?", "label": "Unrated"}
    # 1. Exact match
    if name_lower in POLLSTER_RATINGS:
        r = POLLSTER_RATINGS[name_lower]
        return {"rating": r, "label": RATING_LABELS[r]}
    # 2. Word-boundary match (prevents short-name false positives)
    import re as _re
    for key, rating in POLLSTER_RATINGS.items():
        if _re.search(r'\b' + _re.escape(key) + r'\b', name_lower):
            return {"rating": rating, "label": RATING_LABELS[rating]}
    return {"rating": "?", "label": "Unrated"}


# ── Opinion claim detection (lightweight regex pre-filter) ──

_OPINION_PATTERNS = [
    r'\b(?:americans?|people|voters?|public|citizens?|majority|most)\b.*\b(?:want|think|believe|support|oppose|favor|prefer|agree|demand|feel)\b',
    r'\b(?:want|think|believe|support|oppose|favor|prefer|agree|demand|feel)\b.*\b(?:americans?|people|voters?|public|citizens?|majority|most)\b',
    r'\b(?:polls?|surveys?|polling)\b.*\b(?:show|say|indicate|suggest|find|reveal|demonstrate)\b',
    r'\b(?:popular|unpopular|approval|disapproval)\b',
    r'\b(?:percent|%)\b.*\b(?:americans?|people|voters?|support|oppose|favor|approve)\b',
    r'\bnobody\s+wants\b',
    r'\beverybody\s+(?:knows|wants|thinks|agrees)\b',
]
_OPINION_RE = re.compile('|'.join(_OPINION_PATTERNS), re.IGNORECASE)


def _is_opinion_claim(text: str) -> bool:
    """Quick regex check for opinion/polling claims. False positives OK — AI verifies."""
    return bool(_OPINION_RE.search(text))


# ── System prompt ──

_SYSTEM_PROMPT = """You are a polling data analyst. When given a statement about public opinion,
search the web for the most recent and relevant polling data from recognized organizations.

First determine: is this actually a claim about public opinion (what people think, want, support,
oppose, or believe)? If not, return {"is_opinion_claim": false}.

If it IS an opinion claim, search for actual polling data. Prioritize:
1. Recent polls (last 12 months preferred)
2. Non-partisan organizations (Pew, Gallup, AP-NORC, Marist, Quinnipiac, etc.)
3. Polls with clear methodology (sample size, dates)

Return ONLY valid JSON — no markdown, no backticks, no preamble:
{
  "is_opinion_claim": true,
  "topic": "brief topic description in 8 words or less",
  "polls": [
    {
      "organization": "polling org name",
      "date": "Month Year or date range",
      "question": "the poll question (paraphrased if needed)",
      "results": {"option1": percent, "option2": percent},
      "sample_size": number or null,
      "methodology": "phone/online/mixed/unknown",
      "url": "source URL"
    }
  ],
  "summary": "2-3 sentence summary of what polls actually show vs what was claimed",
  "claim_vs_data": "supported" | "contradicted" | "mixed" | "no_data"
}

Rules:
- Include 2-5 polls if available, from different organizations
- Always include the poll date — recency matters
- If you cannot find real polling data, set claim_vs_data to "no_data" with empty polls array
- Never fabricate poll numbers — only report real data found via web search
- results should be percentage numbers (no % sign), keys should be the response options
"""


# ── Request/Response models ──

class PollResult(BaseModel):
    organization: str
    date: str | None = None
    question: str | None = None
    results: dict | None = None
    sample_size: int | None = None
    methodology: str | None = None
    url: str | None = None
    rating: dict | None = None  # populated server-side from POLLSTER_RATINGS


class PollsCheckRequest(BaseModel):
    statement: str = Field(..., max_length=2000)
    speaker: str | None = Field(None, max_length=200)


class PollsCheckResponse(BaseModel):
    is_opinion_claim: bool
    topic: str | None = None
    polls: list[PollResult] | None = None
    summary: str | None = None
    claim_vs_data: str | None = None
    error: str | None = None
    provider: str | None = None


# ── JSON parsing ──

def _parse_json(raw_text: str) -> dict | None:
    raw = raw_text.strip()
    if raw.startswith("```json"):
        raw = raw[7:]
    elif raw.startswith("```"):
        raw = raw[3:]
    if raw.endswith("```"):
        raw = raw[:-3]
    raw = raw.strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


# ── Providers ──

_GEMINI_ENDPOINT = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "gemini-2.5-flash:generateContent"
)


def _run_gemini(statement: str) -> dict:
    import requests
    api_key = _get_gemini_key()
    if not api_key:
        return {"is_opinion_claim": False, "error": "Gemini API key not set."}

    prompt = f'{_SYSTEM_PROMPT}\n\nAnalyze this statement: "{statement}"'
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "tools": [{"google_search": {}}],
        "generationConfig": {"temperature": 0.1, "maxOutputTokens": 1500},
    }
    try:
        resp = requests.post(
            _GEMINI_ENDPOINT,
            headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
            json=payload, timeout=30,
        )
    except Exception as e:
        return {"is_opinion_claim": False, "error": str(e)[:200]}

    if resp.status_code != 200:
        try:
            detail = resp.json().get("error", {}).get("message", resp.text[:200])
        except Exception:
            detail = resp.text[:200]
        return {"is_opinion_claim": False, "error": f"Gemini {resp.status_code}: {detail}"}

    data = resp.json()
    candidates = data.get("candidates", [])
    if not candidates:
        return {"is_opinion_claim": False, "error": "No response from Gemini"}

    text = candidates[0].get("content", {}).get("parts", [{}])[0].get("text", "")
    result = _parse_json(text)
    if result is None:
        return {"is_opinion_claim": False, "error": f"JSON parse error: {text[:200]}"}

    # Extract source URLs from grounding metadata
    sources = []
    metadata = candidates[0].get("groundingMetadata", {})
    for chunk in metadata.get("groundingChunks", []):
        web = chunk.get("web", {})
        if web.get("uri"):
            sources.append(web["uri"])
    result["_grounding_urls"] = sources

    return result


def _run_claude(statement: str) -> dict:
    try:
        import anthropic
    except ImportError:
        return {"is_opinion_claim": False, "error": "anthropic package not installed."}

    api_key = _get_anthropic_key()
    if not api_key:
        return {"is_opinion_claim": False, "error": "Anthropic API key not set."}

    try:
        client = anthropic.Anthropic(api_key=api_key)
        message = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1500,
            system=_SYSTEM_PROMPT,
            tools=[{"type": "web_search_20250305", "name": "web_search"}],
            messages=[{"role": "user", "content": f'Analyze this statement: "{statement}"'}],
        )
    except Exception as e:
        return {"is_opinion_claim": False, "error": str(e)[:200]}

    text_block = next((b for b in message.content if b.type == "text"), None)
    if not text_block:
        return {"is_opinion_claim": False, "error": "No text response from Claude"}

    result = _parse_json(text_block.text)
    if result is None:
        return {"is_opinion_claim": False, "error": f"JSON parse error: {text_block.text[:200]}"}

    return result


# ── Main dispatcher ──

def _run_poll_check(statement: str) -> dict:
    provider = _get_provider()
    if provider == "claude":
        result = _run_claude(statement)
    else:
        result = _run_gemini(statement)

    result.pop("_grounding_urls", None)
    result["provider"] = provider

    # Enrich polls with pollster credibility ratings
    if result.get("polls"):
        for poll in result["polls"]:
            if isinstance(poll, dict) and poll.get("organization"):
                poll["rating"] = _rate_pollster(poll["organization"])
            # Validate URL
            url = poll.get("url", "")
            if url and not re.match(r'^https?://', url):
                poll["url"] = None

    return result


# ── Routes ──

@router.get("/polls/status")
async def polls_status():
    provider = _get_provider()
    has_gemini = bool(_get_gemini_key())
    has_claude = bool(_get_anthropic_key())
    return {
        "status": "ok",
        "provider": provider,
        "gemini_key_set": has_gemini,
        "claude_key_set": has_claude,
        "auto_check": _auto_check_enabled(),
    }


@router.post("/polls/check")
async def polls_check(req: PollsCheckRequest):
    provider = _get_provider()
    if provider == "claude" and not _get_anthropic_key():
        raise HTTPException(status_code=503, detail="Anthropic API key not set.")
    if provider == "gemini" and not _get_gemini_key():
        raise HTTPException(status_code=503, detail="Gemini API key not set.")

    if not req.statement or len(req.statement.strip()) < 10:
        return PollsCheckResponse(is_opinion_claim=False)

    if not _check_rate_limit():
        return PollsCheckResponse(
            is_opinion_claim=False,
            error="Rate limited. Please wait.",
        )

    try:
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(_pool, _run_poll_check, req.statement.strip())
        return PollsCheckResponse.model_validate(result)
    except Exception as exc:
        return PollsCheckResponse(
            is_opinion_claim=False,
            error=str(exc)[:200],
        )


def handle_event(event_name, data, settings):
    global _plugin_settings
    if event_name == "on_config_change":
        _plugin_settings = settings
    elif event_name == "on_shutdown":
        _pool.shutdown(wait=False)
