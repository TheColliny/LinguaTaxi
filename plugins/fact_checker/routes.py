"""
LinguaTaxi — Fact Checker Plugin Routes
POST /api/fact-check        — analyze a statement for accuracy
GET  /api/fact-check/status — health check + provider status

Multi-provider fact checking:
  - Gemini (free)  : Google Gemini 2.5 Flash with Google Search grounding
  - Groq   (free)  : Llama 4 via Groq + Brave Search API
  - Claude (paid)  : Claude Sonnet 4 or Opus 4 with web search tool
  - MAGI   (multi) : All available providers in parallel with weighted consensus

Provider weights (for MAGI consensus):
  Claude Opus  = 1.00  (gold standard)
  Claude Sonnet= 0.75
  Gemini Flash = 0.70
  Groq/Llama   = 0.30  (unreliable — negative verdicts suppressed unless corroborated)

Sources are cross-referenced against Media Bias Fact Check (MBFC)
for credibility scoring. Sources below the threshold are flagged;
sources not in MBFC are marked as unverified.
"""

import asyncio
import collections
import importlib.util
import json
import logging
import os
import requests
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

log = logging.getLogger("livecaption")

# ── Load mbfc_data from same directory (plugin loaded via importlib) ──
_mbfc_spec = importlib.util.spec_from_file_location(
    "mbfc_data", str(Path(__file__).parent / "mbfc_data.py")
)
_mbfc_mod = importlib.util.module_from_spec(_mbfc_spec)
_mbfc_spec.loader.exec_module(_mbfc_mod)
lookup_domain = _mbfc_mod.lookup_domain
mbfc_ensure_loaded = _mbfc_mod.ensure_loaded
mbfc_is_loaded = _mbfc_mod.is_loaded
mbfc_source_count = _mbfc_mod.source_count
mbfc_extract_domain = _mbfc_mod._extract_domain
MBFC_DEFAULT_THRESHOLD = _mbfc_mod.DEFAULT_THRESHOLD

# ── Load flip_flop (speaker history cache) from same directory ──
_ff_spec = importlib.util.spec_from_file_location(
    "flip_flop", str(Path(__file__).parent / "flip_flop.py")
)
_ff_mod = importlib.util.module_from_spec(_ff_spec)
_ff_spec.loader.exec_module(_ff_mod)
flip_flop = _ff_mod

# ── Load claim_filter (local pre-filter) from same directory ──
_cf_spec = importlib.util.spec_from_file_location(
    "claim_filter", str(Path(__file__).parent / "claim_filter.py")
)
_cf_mod = importlib.util.module_from_spec(_cf_spec)
_cf_spec.loader.exec_module(_cf_mod)
claim_filter = _cf_mod

router = APIRouter(prefix="/api")

# ── Dedicated thread pool ──
_fc_pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix="factcheck")

# ── Singleton Anthropic client ──
_client = None
_client_lock = threading.Lock()
_client_key = None


def _get_client(api_key):
    """Get or create a singleton Anthropic client. Recreates if key changes."""
    global _client, _client_key
    if _client and _client_key == api_key:
        return _client
    with _client_lock:
        if _client and _client_key == api_key:
            return _client
        try:
            import anthropic
        except ImportError:
            raise RuntimeError(
                "anthropic package not installed. Run: pip install anthropic"
            )
        _client = anthropic.Anthropic(api_key=api_key)
        _client_key = api_key
        return _client


# ── Server-side rate limiter ──
_rate_lock = threading.Lock()
_rate_timestamps: collections.deque = collections.deque()
_RATE_WINDOW = 60


def _check_rate_limit():
    """Token bucket rate limiter. Returns True if request is allowed."""
    now = time.monotonic()
    limit = _plugin_settings.get("rate_limit", 10)
    try:
        limit = int(limit)
    except (ValueError, TypeError):
        limit = 10
    with _rate_lock:
        # Prune expired timestamps from front of deque
        while _rate_timestamps and now - _rate_timestamps[0] >= _RATE_WINDOW:
            _rate_timestamps.popleft()
        if len(_rate_timestamps) >= limit:
            return False
        _rate_timestamps.append(now)
        return True


# ── Plugin settings ──
_plugin_settings = {}

# ── Claude model IDs ──
CLAUDE_MODELS = {
    "sonnet": "claude-sonnet-4-20250514",
    "opus":   "claude-opus-4-20250514",
}


def _get_provider():
    """Get configured provider: 'gemini', 'groq', 'claude', or 'magi'."""
    p = _plugin_settings.get("provider", "gemini").strip().lower()
    return p if p in ("gemini", "groq", "claude", "magi") else "gemini"


def _get_claude_model():
    """Get configured Claude model ID."""
    m = _plugin_settings.get("claude_model", "sonnet").strip().lower()
    return CLAUDE_MODELS.get(m, CLAUDE_MODELS["sonnet"])


def _get_anthropic_key():
    """Get Anthropic API key from plugin settings or environment."""
    key = _plugin_settings.get("anthropic_api_key", "")
    if key:
        return key
    return os.environ.get("ANTHROPIC_API_KEY", "")


def _get_gemini_key():
    """Get Google AI API key from plugin settings or environment."""
    key = _plugin_settings.get("gemini_api_key", "")
    if key:
        return key
    return os.environ.get("GEMINI_API_KEY", "")


def _get_groq_key():
    """Get Groq API key from plugin settings or environment."""
    key = _plugin_settings.get("groq_api_key", "")
    if key:
        return key
    return os.environ.get("GROQ_API_KEY", "")


def _get_brave_key():
    """Get Brave Search API key from plugin settings or environment."""
    key = _plugin_settings.get("brave_api_key", "")
    if key:
        return key
    return os.environ.get("BRAVE_API_KEY", "")


# ── Provider weights for MAGI consensus ──
PROVIDER_WEIGHTS = {
    "claude_opus":   1.00,
    "claude_sonnet": 0.75,
    "gemini":        0.70,
    "groq":          0.30,
}


def _get_threshold():
    """Get MBFC credibility threshold from plugin settings."""
    try:
        val = int(_plugin_settings.get("credibility_threshold", MBFC_DEFAULT_THRESHOLD))
        return max(0, min(100, val))
    except (ValueError, TypeError):
        return MBFC_DEFAULT_THRESHOLD


def _flip_flop_enabled() -> bool:
    """Is flip-flop detection enabled in plugin settings?"""
    val = _plugin_settings.get("flip_flop_enabled", "false")
    return str(val).strip().lower() in ("true", "1", "yes", "on")


# ── Shared system prompt ──

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

class SourceInfo(BaseModel):
    url: str
    title: str | None = None
    page_age: str | None = None
    domain: str | None = None
    mbfc: dict | None = None
    credible: bool | None = None

class FactCheckRequest(BaseModel):
    statement: str
    speaker: str | None = None
    recheck: bool = False
    previous_verdict: str | None = None      # e.g. "MOSTLY TRUE"
    previous_assessment: str | None = None    # the prior assessment text
    previous_score: float | None = None       # the prior accuracy_score

class FlipFlopInfo(BaseModel):
    detected: bool = False
    confidence: float | None = None
    type: str | None = None  # "reversal" | "evolution" | "qualification" | "consistent"
    past_statements: list[dict] | None = None
    summary: str | None = None


class FactCheckResponse(BaseModel):
    type: str
    claim: str | None = None
    accuracy_score: float | None = None
    verdict: str | None = None
    assessment: str | None = None
    language_signals: str | None = None
    error: str | None = None
    flip_flop: FlipFlopInfo | None = None
    sources: list[SourceInfo] | None = None
    flagged_sources: list[SourceInfo] | None = None
    provider: str | None = None
    magi_consensus: str | None = None
    magi_nodes: dict | None = None


# ── Prompt construction ──

def _build_user_prompt(statement: str, recheck: bool = False,
                       previous_verdict: str | None = None,
                       previous_assessment: str | None = None,
                       previous_score: float | None = None,
                       speaker: str | None = None) -> str:
    """Build the user-facing prompt. For rechecks, includes prior result context.
    If flip-flop is enabled and a cached dossier exists for the speaker, the
    dossier is appended so the AI can check for contradictions with past positions."""

    if not recheck:
        prompt = f'Analyze this statement: "{statement}"'
    else:
        parts = [
            f'RECHECK REQUEST — A human operator has flagged the previous fact-check of this '
            f'statement as potentially inaccurate and is requesting an independent re-analysis.',
            f'',
            f'Statement: "{statement}"',
            f'',
            f'Previous analysis:',
        ]
        if previous_verdict:
            parts.append(f'  Verdict: {previous_verdict}')
        if previous_score is not None:
            parts.append(f'  Accuracy score: {previous_score}')
        if previous_assessment:
            parts.append(f'  Assessment: {previous_assessment}')
        parts.extend([
            '',
            'The operator believes this result may be wrong. Do NOT simply repeat the previous '
            'verdict. Conduct a fresh, independent web search with different search queries. '
            'Critically examine whether the previous assessment missed context, used outdated data, '
            'or misinterpreted the claim. If after thorough re-investigation you reach the same '
            'conclusion, that is fine — but you must arrive there independently through new evidence, '
            'not by deferring to the prior result.',
        ])
        prompt = '\n'.join(parts)

    # Append speaker dossier for flip-flop detection (if enabled + cached)
    if speaker and _flip_flop_enabled():
        dossier = flip_flop.get_dossier(speaker)
        if dossier:
            prompt += flip_flop.format_dossier_for_factcheck(dossier, speaker)

    return prompt


# ── Source enrichment (shared by both providers) ──

def _enrich_sources(sources: list[dict], threshold: int) -> list[dict]:
    """Cross-reference sources against MBFC and score credibility."""
    enriched = []
    for src in sources:
        url = src["url"]
        entry = {**src, "domain": mbfc_extract_domain(url)}
        mbfc = lookup_domain(url)
        if mbfc:
            entry["mbfc"] = mbfc
            score = mbfc.get("credibility_score")
            entry["credible"] = score is not None and score >= threshold
        else:
            entry["mbfc"] = None
            entry["credible"] = None
        enriched.append(entry)
    return enriched


def _split_sources(enriched: list[dict]) -> tuple[list[dict], list[dict]]:
    """Split into credible+unverified vs flagged (below threshold)."""
    credible = [s for s in enriched if s["credible"] is not False]
    flagged = [s for s in enriched if s["credible"] is False]
    return credible, flagged


def _parse_verdict_json(raw_text: str) -> dict | None:
    """Parse the JSON verdict from model response text."""
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


# ═══════════════════════════════════════════════════════════════════════════
# Provider: Claude (Anthropic API — paid)
# ═══════════════════════════════════════════════════════════════════════════

def _extract_claude_sources(message) -> list[dict]:
    """Extract source URLs from Claude's web_search_tool_result blocks and citations."""
    sources = []
    seen_urls = set()

    for block in message.content:
        if getattr(block, "type", None) == "web_search_tool_result":
            for result in getattr(block, "content", []):
                if getattr(result, "type", None) == "web_search_result":
                    url = getattr(result, "url", "")
                    if url and url not in seen_urls:
                        seen_urls.add(url)
                        sources.append({
                            "url": url,
                            "title": getattr(result, "title", ""),
                            "page_age": getattr(result, "page_age", None),
                        })

    for block in message.content:
        if getattr(block, "type", None) == "text":
            for citation in getattr(block, "citations", []) or []:
                url = getattr(citation, "url", "")
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    sources.append({
                        "url": url,
                        "title": getattr(citation, "title", ""),
                    })

    return sources


def _run_claude_check(statement: str, user_prompt: str) -> dict:
    """Fact-check using Claude (Sonnet or Opus) with web search."""
    api_key = _get_anthropic_key()
    if not api_key:
        return {"type": "ambiguous", "error": "Anthropic API key not set."}

    model_id = _get_claude_model()
    try:
        client = _get_client(api_key)
    except RuntimeError as e:
        return {"type": "ambiguous", "error": str(e)}

    try:
        message = client.messages.create(
            model=model_id,
            max_tokens=1000,
            system=_SYSTEM_PROMPT,
            tools=[{"type": "web_search_20250305", "name": "web_search"}],
            messages=[{"role": "user", "content": user_prompt}],
        )
    except Exception as e:
        error_msg = str(e)[:200]
        log.error(f"Claude fact-check API error: {error_msg}")
        return {"type": "ambiguous", "error": error_msg}

    raw_sources = _extract_claude_sources(message)

    text_block = next((b for b in message.content if b.type == "text"), None)
    if not text_block:
        return {"type": "ambiguous", "error": "No text response from Claude"}

    result = _parse_verdict_json(text_block.text)
    if result is None:
        raw = text_block.text.strip()[:200]
        return {"type": "ambiguous", "error": f"JSON parse error. Raw: {raw}"}

    return {**result, "_sources": raw_sources}


# ═══════════════════════════════════════════════════════════════════════════
# Provider: Gemini (Google AI — free tier)
# ═══════════════════════════════════════════════════════════════════════════

_GEMINI_ENDPOINT = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "gemini-2.5-flash:generateContent"
)


def _extract_gemini_sources(response_data: dict) -> list[dict]:
    """Extract source URLs from Gemini's groundingMetadata."""
    sources = []
    seen_urls = set()

    candidates = response_data.get("candidates", [])
    if not candidates:
        return sources

    metadata = candidates[0].get("groundingMetadata", {})
    chunks = metadata.get("groundingChunks", [])

    for chunk in chunks:
        web = chunk.get("web", {})
        url = web.get("uri", "")
        if url and url not in seen_urls:
            seen_urls.add(url)
            sources.append({
                "url": url,
                "title": web.get("title", ""),
            })

    return sources


def _run_gemini_check(statement: str, user_prompt: str) -> dict:
    """Fact-check using Gemini 2.5 Flash with Google Search grounding."""
    api_key = _get_gemini_key()
    if not api_key:
        return {"type": "ambiguous",
                "error": "Google AI API key not set. Get a free key at aistudio.google.com"}

    prompt = (
        f'{_SYSTEM_PROMPT}\n\n'
        f'{user_prompt}'
    )

    payload = {
        "contents": [
            {"parts": [{"text": prompt}]}
        ],
        "tools": [
            {"google_search": {}}
        ],
        "generationConfig": {
            "temperature": 0.1,
            "maxOutputTokens": 1000,
        },
    }

    try:
        resp = requests.post(
            _GEMINI_ENDPOINT,
            headers={
                "x-goog-api-key": api_key,
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=30,
        )
    except Exception as e:
        error_msg = str(e)[:200]
        log.error(f"Gemini fact-check request error: {error_msg}")
        return {"type": "ambiguous", "error": error_msg}

    if resp.status_code != 200:
        try:
            err_detail = resp.json().get("error", {}).get("message", resp.text[:200])
        except Exception:
            err_detail = resp.text[:200]
        log.error(f"Gemini API {resp.status_code}: {err_detail}")
        return {"type": "ambiguous", "error": f"Gemini API error ({resp.status_code}): {err_detail}"}

    data = resp.json()

    # Extract sources from grounding metadata
    raw_sources = _extract_gemini_sources(data)

    # Extract text response
    candidates = data.get("candidates", [])
    if not candidates:
        return {"type": "ambiguous", "error": "No response from Gemini"}

    parts = candidates[0].get("content", {}).get("parts", [])
    if not parts:
        return {"type": "ambiguous", "error": "Empty response from Gemini"}

    text = parts[0].get("text", "")
    result = _parse_verdict_json(text)
    if result is None:
        return {"type": "ambiguous", "error": f"JSON parse error. Raw: {text[:200]}"}

    return {**result, "_sources": raw_sources}


# ═══════════════════════════════════════════════════════════════════════════
# Provider: Groq + Brave Search (free tier, low weight)
# ═══════════════════════════════════════════════════════════════════════════

_BRAVE_SEARCH_URL = "https://api.search.brave.com/res/v1/web/search"
_GROQ_CHAT_URL = "https://api.groq.com/openai/v1/chat/completions"
_GROQ_MODEL = "meta-llama/llama-4-scout-17b-16e-instruct"


def _brave_search(query: str, brave_key: str, count: int = 5) -> list[dict]:
    """Run a Brave web search, return list of {url, title, snippet}."""
    try:
        resp = requests.get(
            _BRAVE_SEARCH_URL,
            headers={"Accept": "application/json", "X-Subscription-Token": brave_key},
            params={"q": query, "count": count},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        results = []
        for item in data.get("web", {}).get("results", []):
            results.append({
                "url": item.get("url", ""),
                "title": item.get("title", ""),
                "snippet": item.get("description", ""),
            })
        return results
    except Exception as e:
        log.warning(f"Brave Search error: {e}")
        return []


def _run_groq_check(statement: str, user_prompt: str) -> dict:
    """Fact-check using Groq (Llama 4) with Brave Search for web context."""
    groq_key = _get_groq_key()
    brave_key = _get_brave_key()
    if not groq_key:
        return {"type": "ambiguous", "error": "Groq API key not set."}
    if not brave_key:
        return {"type": "ambiguous", "error": "Brave Search API key not set."}

    # Step 1: Search the web for context on the claim
    search_results = _brave_search(statement, brave_key, count=5)
    raw_sources = [{"url": r["url"], "title": r["title"]} for r in search_results if r["url"]]

    # Step 2: Build context from search snippets
    if search_results:
        context_lines = []
        for i, r in enumerate(search_results, 1):
            context_lines.append(f"[{i}] {r['title']} — {r['snippet']}")
        search_context = "\n".join(context_lines)
        augmented_prompt = (
            f"{user_prompt}\n\n"
            f"Web search results for context (use these to verify the claim):\n"
            f"{search_context}"
        )
    else:
        augmented_prompt = user_prompt

    # Step 3: Call Groq with the augmented prompt
    payload = {
        "model": _GROQ_MODEL,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": augmented_prompt},
        ],
        "temperature": 0.1,
        "max_tokens": 1000,
    }

    try:
        resp = requests.post(
            _GROQ_CHAT_URL,
            headers={
                "Authorization": f"Bearer {groq_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=15,
        )
    except Exception as e:
        error_msg = str(e)[:200]
        log.error(f"Groq API request error: {error_msg}")
        return {"type": "ambiguous", "error": error_msg}

    if resp.status_code != 200:
        try:
            err_detail = resp.json().get("error", {}).get("message", resp.text[:200])
        except Exception:
            err_detail = resp.text[:200]
        log.error(f"Groq API {resp.status_code}: {err_detail}")
        return {"type": "ambiguous", "error": f"Groq API error ({resp.status_code}): {err_detail}"}

    data = resp.json()
    choices = data.get("choices", [])
    if not choices:
        return {"type": "ambiguous", "error": "No response from Groq"}

    text = choices[0].get("message", {}).get("content", "")
    result = _parse_verdict_json(text)
    if result is None:
        return {"type": "ambiguous", "error": f"JSON parse error. Raw: {text[:200]}"}

    return {**result, "_sources": raw_sources}


# ═══════════════════════════════════════════════════════════════════════════
# Main dispatcher
# ═══════════════════════════════════════════════════════════════════════════

def _run_fact_check(statement: str, recheck: bool = False,
                    previous_verdict: str | None = None,
                    previous_assessment: str | None = None,
                    previous_score: float | None = None,
                    speaker: str | None = None) -> dict:
    """Dispatch to the configured provider, enrich sources with MBFC."""
    mbfc_ensure_loaded()
    threshold = _get_threshold()
    provider = _get_provider()

    user_prompt = _build_user_prompt(
        statement, recheck, previous_verdict, previous_assessment, previous_score,
        speaker=speaker,
    )

    if provider == "magi":
        return _run_magi_check(statement, user_prompt, threshold)
    elif provider == "claude":
        result = _run_claude_check(statement, user_prompt)
    elif provider == "groq":
        result = _run_groq_check(statement, user_prompt)
    else:
        result = _run_gemini_check(statement, user_prompt)

    # Pull out raw sources, enrich with MBFC, split credible vs flagged
    raw_sources = result.pop("_sources", [])
    enriched = _enrich_sources(raw_sources, threshold)
    credible, flagged = _split_sources(enriched)

    result["sources"] = credible
    result["flagged_sources"] = flagged
    result["provider"] = provider

    return result


# ═══════════════════════════════════════════════════════════════════════════
# MAGI — weighted multi-provider consensus
#
# Weights:  Claude Opus 1.0 | Claude Sonnet 0.75 | Gemini 0.70 | Groq 0.30
#
# Groq suppression rule: a negative verdict from Groq alone carries no
# weight — it only counts if at least one other provider agrees.
# ═══════════════════════════════════════════════════════════════════════════

_VERDICT_RANK = {
    "TRUE": 5, "MOSTLY TRUE": 4, "MIXED": 3,
    "MOSTLY FALSE": 2, "FALSE": 1, "UNVERIFIABLE": 0,
}

# Verdicts ranked <= 2 are considered "negative"
_NEGATIVE_THRESHOLD = 2


def _run_magi_check(statement: str, user_prompt: str, threshold: int) -> dict:
    """Run all available providers in parallel, merge with weighted consensus."""
    # Determine which providers have keys configured
    has_claude = bool(_get_anthropic_key())
    has_gemini = bool(_get_gemini_key())
    has_groq = bool(_get_groq_key()) and bool(_get_brave_key())

    if not has_claude and not has_gemini and not has_groq:
        return {"type": "ambiguous", "error": "MAGI requires at least one provider API key."}

    # Launch all available providers in parallel
    futures = {}
    with ThreadPoolExecutor(max_workers=3, thread_name_prefix="magi") as pool:
        if has_claude:
            claude_model = _plugin_settings.get("claude_model", "sonnet").strip().lower()
            weight_key = "claude_opus" if claude_model == "opus" else "claude_sonnet"
            futures["claude"] = {
                "future": pool.submit(_run_claude_check, statement, user_prompt),
                "weight": PROVIDER_WEIGHTS[weight_key],
                "label": f"Claude {claude_model.title()}",
            }
        if has_gemini:
            futures["gemini"] = {
                "future": pool.submit(_run_gemini_check, statement, user_prompt),
                "weight": PROVIDER_WEIGHTS["gemini"],
                "label": "Gemini Flash",
            }
        if has_groq:
            futures["groq"] = {
                "future": pool.submit(_run_groq_check, statement, user_prompt),
                "weight": PROVIDER_WEIGHTS["groq"],
                "label": "Groq/Llama",
            }

        # Collect results (45s timeout prevents permanent deadlock if a provider hangs)
        provider_results = {}
        for name, info in futures.items():
            try:
                provider_results[name] = {
                    "result": info["future"].result(timeout=45),
                    "weight": info["weight"],
                    "label": info["label"],
                }
            except TimeoutError:
                provider_results[name] = {
                    "result": {"type": "ambiguous", "error": f"{info['label']} timed out"},
                    "weight": info["weight"],
                    "label": info["label"],
                }
            except Exception as e:
                provider_results[name] = {
                    "result": {"type": "ambiguous", "error": str(e)[:200]},
                    "weight": info["weight"],
                    "label": info["label"],
                }

    # Merge all sources, deduplicate
    all_sources = []
    for pr in provider_results.values():
        all_sources.extend(pr["result"].pop("_sources", []))
    seen = set()
    deduped = []
    for s in all_sources:
        if s["url"] not in seen:
            seen.add(s["url"])
            deduped.append(s)
    enriched = _enrich_sources(deduped, threshold)
    credible, flagged = _split_sources(enriched)

    # Separate successful from failed
    successful = {}
    failed = {}
    for name, pr in provider_results.items():
        if pr["result"].get("error"):
            failed[name] = pr
        else:
            successful[name] = pr

    # If all failed, surface ALL errors (not just the first)
    if not successful:
        combined_errors = "; ".join(
            f"{pr['label']}: {pr['result'].get('error', 'unknown')}"
            for pr in provider_results.values()
        )
        # Failed nodes get effective weight 0 (they didn't contribute)
        return {
            "type": "ambiguous",
            "error": f"All MAGI providers failed — {combined_errors}",
            "sources": credible,
            "flagged_sources": flagged,
            "provider": "magi",
            "magi_consensus": "all_failed",
            "magi_nodes": {n: _summarize_node(pr, 0.0) for n, pr in provider_results.items()},
        }

    # If only one succeeded, use it. Failed nodes get effective weight 0.
    if len(successful) == 1:
        name, pr = next(iter(successful.items()))
        result = pr["result"]
        result["sources"] = credible
        result["flagged_sources"] = flagged
        result["provider"] = "magi"
        result["magi_consensus"] = f"{name}_only"
        result["magi_nodes"] = {
            n: _summarize_node(p, p["weight"] if n == name else 0.0)
            for n, p in provider_results.items()
        }
        return result

    # Multiple succeeded — compute weighted consensus
    # Build effective weights (copy so we don't mutate provider_results)
    effective_weights = {name: pr["weight"] for name, pr in successful.items()}

    # Apply Groq suppression: if Groq's verdict is negative and no other
    # provider agrees, set Groq's effective weight to 0
    groq_pr = successful.get("groq")
    if groq_pr:
        groq_verdict = groq_pr["result"].get("verdict")
        groq_rank = _VERDICT_RANK.get(groq_verdict, -1)
        if groq_rank >= 0 and groq_rank <= _NEGATIVE_THRESHOLD:
            # Groq says negative — check if anyone else agrees
            others_agree = False
            for name, pr in successful.items():
                if name == "groq":
                    continue
                other_rank = _VERDICT_RANK.get(pr["result"].get("verdict"), -1)
                if other_rank >= 0 and other_rank <= _NEGATIVE_THRESHOLD:
                    others_agree = True
                    break
            if not others_agree:
                effective_weights["groq"] = 0.0  # suppress uncorroborated negative
                log.info("MAGI: Groq negative verdict suppressed (no corroboration)")

    # Weighted average of accuracy scores
    weighted_score_sum = 0.0
    weight_sum = 0.0
    for name, pr in successful.items():
        score = pr["result"].get("accuracy_score")
        w = effective_weights[name]
        if score is not None and w > 0:
            weighted_score_sum += score * w
            weight_sum += w
    weighted_score = round(weighted_score_sum / weight_sum, 1) if weight_sum > 0 else None

    # Weighted verdict: pick the verdict with highest total weight behind it
    verdict_weights = {}
    for name, pr in successful.items():
        v = pr["result"].get("verdict")
        w = effective_weights[name]
        if v and w > 0:
            verdict_weights[v] = verdict_weights.get(v, 0) + w
    if verdict_weights:
        weighted_verdict = max(verdict_weights, key=verdict_weights.get)
    else:
        weighted_verdict = None

    # Determine consensus level
    verdicts_set = set()
    for name, pr in successful.items():
        v = pr["result"].get("verdict")
        if v and effective_weights[name] > 0:
            verdicts_set.add(v)

    if len(verdicts_set) <= 1:
        consensus = "agree"
    else:
        ranks = [_VERDICT_RANK.get(v, -1) for v in verdicts_set if _VERDICT_RANK.get(v, -1) >= 0]
        if ranks and (max(ranks) - min(ranks)) <= 1:
            consensus = "close"
        else:
            consensus = "disagree"

    # Pick best assessment (from highest-weighted successful provider)
    best_provider = max(successful.items(), key=lambda item: effective_weights[item[0]])[1]
    best_result = best_provider["result"]

    if consensus in ("agree", "close"):
        result = {
            "type": best_result.get("type", "fact_claim"),
            "claim": best_result.get("claim"),
            "accuracy_score": weighted_score,
            "verdict": weighted_verdict,
            "assessment": best_result.get("assessment", ""),
            "language_signals": best_result.get("language_signals"),
        }
    else:
        # Disagreement — build split verdict summary
        parts = []
        for name, pr in successful.items():
            w = effective_weights[name]
            if w > 0:
                v = pr["result"].get("verdict", "N/A")
                s = pr["result"].get("accuracy_score")
                s_str = f"{s}%" if s is not None else "N/A"
                parts.append(f"{pr['label']}: {v} ({s_str}, weight {w})")
        split_detail = "; ".join(parts)

        result = {
            "type": best_result.get("type", "fact_claim"),
            "claim": best_result.get("claim"),
            "accuracy_score": weighted_score,
            "verdict": weighted_verdict,
            "assessment": f"SPLIT VERDICT — {split_detail}. {best_result.get('assessment', '')}",
            "language_signals": best_result.get("language_signals"),
        }

    result["sources"] = credible
    result["flagged_sources"] = flagged
    result["provider"] = "magi"
    result["magi_consensus"] = consensus
    # Failed providers are not in effective_weights — they get 0 (didn't contribute)
    result["magi_nodes"] = {
        n: _summarize_node(pr, effective_weights.get(n, 0.0))
        for n, pr in provider_results.items()
    }

    return result


def _summarize_node(pr: dict, effective_weight: float) -> dict:
    """Create a compact summary of one MAGI node's result for the frontend."""
    r = pr["result"]
    return {
        "label": pr["label"],
        "weight": effective_weight,
        "verdict": r.get("verdict"),
        "accuracy_score": r.get("accuracy_score"),
        "assessment": r.get("assessment"),
        "error": r.get("error"),
    }


# ═══════════════════════════════════════════════════════════════════════════
# Dossier building (flip-flop prefetch)
# ═══════════════════════════════════════════════════════════════════════════

def _fetch_dossier_for(name: str) -> dict | None:
    """Fetch a speaker dossier using the configured provider. One AI call per speaker.
    Runs in the flip_flop._pool background thread (see flip_flop.queue_prefetch)."""
    provider = _get_provider()
    prompt = f"{flip_flop.get_dossier_prompt()}\n\nSubject: {name}"

    # MAGI mode would be overkill for a dossier; fall back to best available single provider
    if provider == "magi":
        if _get_anthropic_key():
            provider = "claude"
        elif _get_gemini_key():
            provider = "gemini"
        elif _get_groq_key() and _get_brave_key():
            provider = "groq"
        else:
            return None

    try:
        if provider == "claude":
            result = _run_claude_check(name, prompt)
        elif provider == "groq":
            result = _run_groq_check(name, prompt)
        else:
            result = _run_gemini_check(name, prompt)
    except Exception as e:
        log.error(f"[Flip-Flop] Dossier fetch error for '{name}': {e}")
        return None

    if result.get("error"):
        log.warning(f"[Flip-Flop] Dossier for '{name}': {result['error']}")
        return None

    # Providers return {type, claim, ...} wrapping — if dossier data is in _sources or embedded,
    # we need to parse it. But our prompt asks for the dossier JSON directly in the response text.
    # The provider's _parse_verdict_json returned whatever was in the text block. If the AI followed
    # instructions, result will contain "statements" and "positions" keys instead of verdict fields.
    if "statements" in result or "positions" in result:
        return {
            "statements": result.get("statements", []) or [],
            "positions": result.get("positions", {}) or {},
        }
    return None


class PrefetchRequest(BaseModel):
    speakers: list[str]


# ── Routes ──

@router.get("/fact-check/status")
async def fact_check_status():
    """Health check — provider status, keys, MBFC data."""
    provider = _get_provider()
    has_claude_key = bool(_get_anthropic_key())
    has_gemini_key = bool(_get_gemini_key())
    has_groq_key = bool(_get_groq_key())
    has_brave_key = bool(_get_brave_key())

    try:
        import anthropic  # noqa: F401
        has_anthropic_pkg = True
    except ImportError:
        has_anthropic_pkg = False

    return {
        "status": "ok",
        "provider": provider,
        "claude_model": _plugin_settings.get("claude_model", "sonnet"),
        "claude_key_set": has_claude_key,
        "anthropic_pkg_installed": has_anthropic_pkg,
        "gemini_key_set": has_gemini_key,
        "groq_key_set": has_groq_key,
        "brave_key_set": has_brave_key,
        "mbfc_loaded": mbfc_is_loaded(),
        "mbfc_sources": mbfc_source_count(),
        "credibility_threshold": _get_threshold(),
        "claim_filter_available": claim_filter.is_available(),
        "claim_filter_loaded": claim_filter.is_loaded(),
        "claim_filter_error": claim_filter.get_load_error(),
    }


@router.post("/fact-check")
async def fact_check(req: FactCheckRequest):
    """Analyze a transcribed statement for accuracy."""
    provider = _get_provider()

    # Validate that the selected provider has keys
    if provider == "claude" and not _get_anthropic_key():
        raise HTTPException(
            status_code=503,
            detail="Anthropic API key not set. Add it in plugin settings or switch to Gemini (free).",
        )
    if provider == "gemini" and not _get_gemini_key():
        raise HTTPException(
            status_code=503,
            detail="Google AI API key not set. Get a free key at aistudio.google.com",
        )
    if provider == "groq" and (not _get_groq_key() or not _get_brave_key()):
        raise HTTPException(
            status_code=503,
            detail="Groq and Brave Search API keys both required. Free at groq.com and brave.com/search/api",
        )
    if provider == "magi" and not (_get_anthropic_key() or _get_gemini_key() or (_get_groq_key() and _get_brave_key())):
        raise HTTPException(
            status_code=503,
            detail="MAGI requires at least one provider API key configured.",
        )

    if not req.statement or len(req.statement.strip()) < 10:
        return FactCheckResponse(
            type="ambiguous",
            assessment="Statement too short to analyze.",
        )

    if not _check_rate_limit():
        rate_limit = _plugin_settings.get("rate_limit", 10)
        return FactCheckResponse(
            type="ambiguous",
            error=f"Rate limited — max {rate_limit} checks per minute",
            assessment="Too many requests. Please wait.",
        )

    # Local claim detection pre-filter: skip non-claims before hitting the API.
    # Bypassed for rechecks (operator explicitly wants analysis) and when model unavailable.
    local_filter_on = _plugin_settings.get("local_filter", "true").lower() in ("true", "1", "on")
    if local_filter_on and not req.recheck and claim_filter.is_loaded():
        cf_result = claim_filter.classify(req.statement.strip())
        if not cf_result["is_claim"] and cf_result["confidence"] >= 0.995:
            return FactCheckResponse(
                type="opinion",
                assessment="Filtered locally — not a verifiable factual claim.",
                claim=req.statement.strip(),
            )

    try:
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            _fc_pool,
            lambda: _run_fact_check(
                req.statement.strip(),
                recheck=req.recheck,
                previous_verdict=req.previous_verdict,
                previous_assessment=req.previous_assessment,
                previous_score=req.previous_score,
                speaker=req.speaker,
            ),
        )
        # Validate through Pydantic model — strips unexpected LLM keys, ensures schema
        return FactCheckResponse.model_validate(result)
    except Exception as exc:
        return FactCheckResponse(
            type="ambiguous",
            error=str(exc)[:200],
            assessment="Analysis failed — see error field for details.",
        )


# ── Flip-Flop Dossier Endpoints ──

@router.get("/fact-check/dossier/status")
async def dossier_status():
    """Return current state of flip-flop dossier cache."""
    return {
        "enabled": _flip_flop_enabled(),
        **flip_flop.status(),
    }


@router.post("/fact-check/dossier/prefetch")
async def dossier_prefetch(req: PrefetchRequest):
    """Queue dossier prefetch for a list of speakers. Returns names that were queued."""
    if not _flip_flop_enabled():
        raise HTTPException(status_code=400, detail="Flip-flop detection is disabled.")
    # Ensure at least one provider has a key for dossier fetch
    if not (_get_anthropic_key() or _get_gemini_key() or (_get_groq_key() and _get_brave_key())):
        raise HTTPException(status_code=503, detail="No AI provider API key configured.")
    queued = flip_flop.queue_prefetch(req.speakers, _fetch_dossier_for)
    return {"queued": queued, "status": flip_flop.status()}


@router.post("/fact-check/dossier/refresh/{name}")
async def dossier_refresh(name: str):
    """Force-refresh a specific speaker's dossier."""
    if not _flip_flop_enabled():
        raise HTTPException(status_code=400, detail="Flip-flop detection is disabled.")
    flip_flop.remove_dossier(name)
    queued = flip_flop.queue_prefetch([name], _fetch_dossier_for)
    return {"queued": queued}


@router.get("/fact-check/dossier/{name}")
async def dossier_get(name: str):
    """Return a cached dossier for inspection."""
    d = flip_flop.get_dossier(name)
    if not d:
        raise HTTPException(status_code=404, detail=f"No dossier cached for '{name}'")
    return d


@router.delete("/fact-check/dossier/{name}")
async def dossier_delete(name: str):
    """Remove a cached dossier."""
    removed = flip_flop.remove_dossier(name)
    return {"removed": removed, "name": name}


@router.delete("/fact-check/dossier")
async def dossier_clear_all():
    """Clear all cached dossiers."""
    flip_flop.clear_all()
    return {"status": "cleared"}


# ── Claim filter model management ──────────────────────────────────────────

@router.get("/fact-check/filter/status")
async def filter_status():
    """Claim filter model status."""
    return {
        "available": claim_filter.is_available(),
        "loaded": claim_filter.is_loaded(),
        "error": claim_filter.get_load_error(),
    }


@router.post("/fact-check/filter/download")
async def filter_download():
    """Download and quantize the Factiverse claim detection model."""
    if claim_filter.is_available():
        return {"status": "already_downloaded"}

    loop = asyncio.get_running_loop()
    try:
        result = await loop.run_in_executor(
            _fc_pool, _download_claim_filter_model
        )
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def _download_claim_filter_model() -> dict:
    """Download Factiverse model from HuggingFace, export to ONNX, quantize to INT8."""
    model_dir = Path(__file__).parent / "models" / "claim_detection"
    model_dir.mkdir(parents=True, exist_ok=True)

    try:
        from optimum.onnxruntime import ORTModelForSequenceClassification, ORTQuantizer
        from optimum.onnxruntime.configuration import AutoQuantizationConfig
        from transformers import AutoTokenizer
    except ImportError as e:
        raise RuntimeError(
            f"Missing dependency: {e.name}. Install: pip install optimum[onnxruntime] transformers"
        )

    log.info("Downloading Factiverse claim detection model...")
    hf_model_id = "Factiverse/claim_detection_unquantized"

    # Download tokenizer
    tokenizer = AutoTokenizer.from_pretrained(hf_model_id)
    tokenizer.save_pretrained(str(model_dir))

    # Export to ONNX
    log.info("Exporting model to ONNX...")
    ort_model = ORTModelForSequenceClassification.from_pretrained(
        hf_model_id, export=True
    )
    ort_model.save_pretrained(str(model_dir))

    # Quantize to INT8
    log.info("Quantizing to INT8...")
    quantizer = ORTQuantizer.from_pretrained(str(model_dir))
    qconfig = AutoQuantizationConfig.avx2(is_static=False)
    quantizer.quantize(save_dir=str(model_dir), quantization_config=qconfig)

    # Rename quantized model to expected path
    quantized_path = model_dir / "model_quantized.onnx"
    if not quantized_path.exists():
        # optimum may output as model_optimized.onnx or model.onnx
        for candidate in ["model_quantized.onnx", "model_optimized.onnx", "model.onnx"]:
            p = model_dir / candidate
            if p.exists() and p != quantized_path:
                p.rename(quantized_path)
                break

    # Clean up unquantized ONNX to save disk
    unquantized = model_dir / "model.onnx"
    if unquantized.exists() and quantized_path.exists() and unquantized != quantized_path:
        unquantized.unlink()

    log.info("Claim detection model ready at %s", model_dir)
    return {"status": "downloaded", "path": str(model_dir)}


@router.delete("/fact-check/filter/model")
async def filter_delete():
    """Delete the downloaded claim filter model."""
    import shutil
    model_dir = Path(__file__).parent / "models" / "claim_detection"
    if model_dir.exists():
        shutil.rmtree(model_dir)
    claim_filter.shutdown()
    return {"status": "deleted"}


def handle_event(event_name, data, settings):
    """Plugin event handler called by PluginDispatcher."""
    global _plugin_settings
    if event_name == "on_config_change":
        _plugin_settings = settings
        if claim_filter.is_available() and not claim_filter.is_loaded():
            threading.Thread(target=claim_filter.ensure_loaded, daemon=True).start()
    elif event_name == "on_speaker_enrolled":
        _plugin_settings = settings
        if _flip_flop_enabled() and isinstance(data, dict):
            name = data.get("speaker")
            if name:
                flip_flop.queue_prefetch([name], _fetch_dossier_for)
    elif event_name == "on_shutdown":
        _plugin_settings = settings
        _fc_pool.shutdown(wait=False)
        flip_flop.shutdown()
        claim_filter.shutdown()
