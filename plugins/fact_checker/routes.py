"""
LinguaTaxi — Fact Checker Plugin Routes
POST /api/fact-check  — analyze a statement for accuracy
GET  /api/fact-check/status — health check + key status

Uses Claude with web search to fact-check live transcriptions.
Requires: pip install anthropic
Requires: ANTHROPIC_API_KEY environment variable (or plugin settings)
"""

import asyncio
import json
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

log = logging.getLogger("livecaption")

router = APIRouter(prefix="/api")

# ── Dedicated thread pool (MAGI condition #2) ──
# Isolated from the default asyncio executor to avoid starving
# the operator app's event loop during slow web-search calls.
_fc_pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix="factcheck")

# ── Singleton Anthropic client (MAGI condition #4) ──
_client = None
_client_lock = threading.Lock()
_client_key = None  # track which key the client was created with


def _get_client(api_key):
    """Get or create a singleton Anthropic client. Recreates if key changes."""
    global _client, _client_key
    if _client and _client_key == api_key:
        return _client
    with _client_lock:
        if _client and _client_key == api_key:
            return _client
        try:
            import anthropic  # lazy import (MAGI condition #3)
        except ImportError:
            raise RuntimeError(
                "anthropic package not installed. Run: pip install anthropic"
            )
        _client = anthropic.Anthropic(api_key=api_key)
        _client_key = api_key
        return _client


# ── Server-side rate limiter (MAGI condition #1) ──
_rate_lock = threading.Lock()
_rate_timestamps = []  # timestamps of recent requests
_RATE_LIMIT = 10       # max requests per minute
_RATE_WINDOW = 60      # seconds


def _check_rate_limit():
    """Token bucket rate limiter. Returns True if request is allowed."""
    now = time.monotonic()
    with _rate_lock:
        # Prune old timestamps
        _rate_timestamps[:] = [t for t in _rate_timestamps if now - t < _RATE_WINDOW]
        if len(_rate_timestamps) >= _RATE_LIMIT:
            return False
        _rate_timestamps.append(now)
        return True


# ── Plugin settings ──
_plugin_settings = {}


def _get_api_key():
    """Get Anthropic API key from plugin settings or environment."""
    key = _plugin_settings.get("anthropic_api_key", "")
    if key:
        return key
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if key:
        return key
    return ""


# ── System prompt ──

_SYSTEM_PROMPT = """You are a precise fact-checking assistant for live political speech.
Analyze the given statement and return ONLY a valid JSON object — no markdown, no backticks, no preamble.

Classify the statement as:
  "fact_claim"  — makes a verifiable assertion (statistics, historical events, named entities with
                  properties, numeric comparisons, claims about current/past state of the world)
  "opinion"     — expresses a viewpoint, preference, belief, moral judgment, or subjective evaluation
  "ambiguous"   — too vague, incomplete, or mixed to classify confidently

For fact claims, use web search to research and verify before scoring.

Return exactly this JSON structure:
{
  "type": "fact_claim" | "opinion" | "ambiguous",
  "claim": "core verifiable claim in 12 words or less",
  "accuracy_score": number 0-100 or null,
  "verdict": "TRUE" | "MOSTLY TRUE" | "MIXED" | "MOSTLY FALSE" | "FALSE" | "UNVERIFIABLE" | null,
  "assessment": "1-2 sentence explanation of accuracy or why classified as opinion/ambiguous",
  "language_signals": "specific words or phrases that drove the fact vs opinion classification"
}

Rules:
- accuracy_score and verdict must be null for opinions and ambiguous statements
- For UNVERIFIABLE claims, set accuracy_score to null and verdict to "UNVERIFIABLE"
- If the statement is a sentence fragment, greeting, or filler phrase (<20 meaningful chars), return type "ambiguous"
- Never fabricate sources; if you cannot verify via web search, use verdict "UNVERIFIABLE"
"""


# ── Request/Response models ──

class FactCheckRequest(BaseModel):
    statement: str
    speaker: str | None = None


class FactCheckResponse(BaseModel):
    type: str
    claim: str | None = None
    accuracy_score: float | None = None
    verdict: str | None = None
    assessment: str | None = None
    language_signals: str | None = None
    error: str | None = None


# ── Routes ──

@router.get("/fact-check/status")
async def fact_check_status():
    """Health check — confirms API key availability and package status."""
    has_key = bool(_get_api_key())
    try:
        import anthropic  # noqa: F401
        has_pkg = True
    except ImportError:
        has_pkg = False
    return {"status": "ok", "api_key_set": has_key, "package_installed": has_pkg}


@router.post("/fact-check")
async def fact_check(req: FactCheckRequest):
    """Analyze a transcribed statement for accuracy using Claude + web search."""
    api_key = _get_api_key()
    if not api_key:
        raise HTTPException(
            status_code=503,
            detail="Anthropic API key not set. Add it in the operator panel or set ANTHROPIC_API_KEY environment variable.",
        )

    if not req.statement or len(req.statement.strip()) < 10:
        return FactCheckResponse(
            type="ambiguous",
            assessment="Statement too short to analyze.",
        )

    # Server-side rate limiting (MAGI condition #1)
    if not _check_rate_limit():
        return FactCheckResponse(
            type="ambiguous",
            error=f"Rate limited — max {_RATE_LIMIT} checks per minute",
            assessment="Too many requests. Please wait before checking more statements.",
        )

    try:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            _fc_pool,  # dedicated pool (MAGI condition #2)
            _run_fact_check,
            req.statement.strip(),
            api_key,
        )
        return result
    except Exception as exc:
        return FactCheckResponse(
            type="ambiguous",
            error=str(exc)[:200],
            assessment="Analysis failed — see error field for details.",
        )


def _run_fact_check(statement: str, api_key: str) -> dict:
    """Synchronous Anthropic call (run in dedicated thread pool)."""
    try:
        client = _get_client(api_key)
    except RuntimeError as e:
        return {"type": "ambiguous", "error": str(e)}

    try:
        message = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1000,
            system=_SYSTEM_PROMPT,
            tools=[{"type": "web_search_20250305", "name": "web_search"}],
            messages=[{"role": "user", "content": f'Analyze this statement: "{statement}"'}],
        )
    except Exception as e:
        error_msg = str(e)[:200]
        log.error(f"Fact-check API error: {error_msg}")
        return {"type": "ambiguous", "error": error_msg}

    text_block = next((b for b in message.content if b.type == "text"), None)
    if not text_block:
        return {"type": "ambiguous", "error": "No text response from API"}

    raw = text_block.text.strip()
    # Strip accidental markdown code fences
    if raw.startswith("```"):
        raw = raw.lstrip("`json").lstrip("`").rstrip("`").strip()

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {
            "type": "ambiguous",
            "error": f"JSON parse error. Raw (truncated): {raw[:200]}",
        }


def handle_event(event_name, data, settings):
    """Plugin event handler called by PluginDispatcher."""
    global _plugin_settings
    _plugin_settings = settings
    if event_name == "on_shutdown":
        _fc_pool.shutdown(wait=False)
