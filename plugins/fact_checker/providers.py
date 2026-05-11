"""
LinguaTaxi — Fact Checker Plugin: Provider Registry
Centralised data-driven registry for all 16 LLM providers (8 free + 8 paid).

All subsequent fact-checking tasks import from here for provider configuration,
API key resolution, and weight lookups.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import logging
import time
import requests

log = logging.getLogger("livecaption")


# ════════════════════════════════════════════════════════════════════════════
# Data Models
# ════════════════════════════════════════════════════════════════════════════

@dataclass
class ProviderConfig:
    """Complete configuration for a single LLM fact-checking provider."""

    # Identity
    provider_id: str
    display_name: str
    model_id: str
    base_url: str

    # Weighting / routing
    default_weight: float
    search_method: str          # "native" | "brave"
    speed: str                  # "fast" | "normal" | "slow"
    category: str               # "free" | "paid"

    # Auth
    auth_header: str            # e.g. "Authorization", "x-goog-api-key", "x-api-key"
    auth_prefix: str            # e.g. "Bearer " or "" for headerless key

    # Rate limits (0 = unlimited / not published)
    rate_limit_rpm: int         # requests per minute
    rate_limit_rpd: int         # requests per day (0 = not specified)

    # Request behaviour
    timeout: int                # seconds

    # Signup / cost info shown in the UI
    signup_url: str
    cost_info: str              # empty string for free providers

    # API call style determines request/response shape
    api_style: str              # "openai" | "gemini" | "anthropic" | "cohere" |
                                # "perplexity" | "openai_native"

    # Optional
    notes: str = ""


@dataclass
class ProviderResult:
    """Result object returned by any provider's fact-check call."""

    provider_id: str
    verdict: str | None
    accuracy_score: float | None
    assessment: str | None
    claim: str | None
    sources: list[dict] = field(default_factory=list)
    language_signals: str | None = None
    error: str | None = None
    latency_ms: int = 0
    result_type: str = "fact_claim"


# ════════════════════════════════════════════════════════════════════════════
# Provider Registry — 8 free + 8 paid = 16 total
# ════════════════════════════════════════════════════════════════════════════

PROVIDER_REGISTRY: dict[str, ProviderConfig] = {

    # ── Free Providers ────────────────────────────────────────────────────

    "gemini_flash_lite": ProviderConfig(
        provider_id="gemini_flash_lite",
        display_name="Gemini 3.1 Flash Lite",
        model_id="gemini-3.1-flash-lite",
        base_url=(
            "https://generativelanguage.googleapis.com/v1beta/models/"
            "gemini-3.1-flash-lite:generateContent"
        ),
        default_weight=0.75,
        search_method="native",
        speed="fast",
        category="free",
        auth_header="x-goog-api-key",
        auth_prefix="",
        rate_limit_rpm=15,
        rate_limit_rpd=500,
        timeout=20,
        signup_url="https://aistudio.google.com",
        cost_info="",
        api_style="gemini",
        notes="Google Search grounding via native tools; 15 RPM / 500 RPD free tier.",
    ),

    "cerebras": ProviderConfig(
        provider_id="cerebras",
        display_name="Cerebras (gpt-oss-120b)",
        model_id="gpt-oss-120b",
        base_url="https://api.cerebras.ai/v1/chat/completions",
        default_weight=0.78,
        search_method="brave",
        speed="fast",
        category="free",
        auth_header="Authorization",
        auth_prefix="Bearer ",
        rate_limit_rpm=30,
        rate_limit_rpd=14400,
        timeout=20,
        signup_url="https://cloud.cerebras.ai",
        cost_info="",
        api_style="openai",
        notes="Wafer-scale inference; very fast. Uses Brave for web context.",
    ),

    "mistral": ProviderConfig(
        provider_id="mistral",
        display_name="Mistral Large 3",
        model_id="mistral-large-3",
        base_url="https://api.mistral.ai/v1/chat/completions",
        default_weight=0.80,
        search_method="brave",
        speed="normal",
        category="free",
        auth_header="Authorization",
        auth_prefix="Bearer ",
        rate_limit_rpm=60,
        rate_limit_rpd=0,
        timeout=20,
        signup_url="https://console.mistral.ai",
        cost_info="",
        api_style="openai",
        notes="Free tier with 60 RPM; no published daily cap.",
    ),

    "github_models": ProviderConfig(
        provider_id="github_models",
        display_name="GitHub Models (GPT-4.1 mini)",
        model_id="gpt-4.1-mini",
        base_url="https://models.inference.ai.azure.com/chat/completions",
        default_weight=0.85,
        search_method="brave",
        speed="normal",
        category="free",
        auth_header="Authorization",
        auth_prefix="Bearer ",
        rate_limit_rpm=15,
        rate_limit_rpd=150,
        timeout=20,
        signup_url="https://github.com/marketplace/models",
        cost_info="",
        api_style="openai",
        notes="Requires GitHub personal access token. 15 RPM / 150 RPD.",
    ),

    "cohere": ProviderConfig(
        provider_id="cohere",
        display_name="Cohere Command-A",
        model_id="command-a",
        base_url="https://api.cohere.com/v2/chat",
        default_weight=0.83,
        search_method="native",
        speed="normal",
        category="free",
        auth_header="Authorization",
        auth_prefix="Bearer ",
        rate_limit_rpm=20,
        rate_limit_rpd=1000,
        timeout=20,
        signup_url="https://dashboard.cohere.com",
        cost_info="",
        api_style="cohere",
        notes="Native web search connector. 20 RPM / 1000 RPD free trial.",
    ),

    "openrouter": ProviderConfig(
        provider_id="openrouter",
        display_name="OpenRouter (Llama 3.3 70B free)",
        model_id="meta-llama/llama-3.3-70b-instruct:free",
        base_url="https://openrouter.ai/api/v1/chat/completions",
        default_weight=0.78,
        search_method="brave",
        speed="normal",
        category="free",
        auth_header="Authorization",
        auth_prefix="Bearer ",
        rate_limit_rpm=20,
        rate_limit_rpd=200,
        timeout=20,
        signup_url="https://openrouter.ai",
        cost_info="",
        api_style="openai",
        notes="Free :free variant; rate-limited. Uses Brave for web context.",
    ),

    "ovhcloud": ProviderConfig(
        provider_id="ovhcloud",
        display_name="OVHcloud (Llama 3.3 70B)",
        model_id="Llama-3.3-70B-Instruct",
        base_url=(
            "https://oai.endpoints.kepler.ai.cloud.ovh.net/v1/chat/completions"
        ),
        default_weight=0.85,
        search_method="brave",
        speed="slow",
        category="free",
        auth_header="Authorization",
        auth_prefix="Bearer ",
        rate_limit_rpm=2,
        rate_limit_rpd=0,
        timeout=30,
        signup_url="https://endpoints.ai.cloud.ovh.net",
        cost_info="",
        api_style="openai",
        notes="Very low rate limit (2 RPM); use as fallback only.",
    ),

    "huggingface": ProviderConfig(
        provider_id="huggingface",
        display_name="HuggingFace (Mixtral 8x7B)",
        model_id="mistralai/Mixtral-8x7B-Instruct-v0.1",
        base_url=(
            "https://api-inference.huggingface.co/models/"
            "mistralai/Mixtral-8x7B-Instruct-v0.1/v1/chat/completions"
        ),
        default_weight=0.60,
        search_method="brave",
        speed="slow",
        category="free",
        auth_header="Authorization",
        auth_prefix="Bearer ",
        rate_limit_rpm=0,
        rate_limit_rpd=1000,
        timeout=30,
        signup_url="https://huggingface.co/settings/tokens",
        cost_info="",
        api_style="openai",
        notes="HuggingFace Inference API; can be cold-start slow. 1000 RPD cap.",
    ),

    # ── Paid Providers ────────────────────────────────────────────────────

    "claude_sonnet": ProviderConfig(
        provider_id="claude_sonnet",
        display_name="Claude Sonnet 4.6",
        model_id="claude-sonnet-4-6-20250514",
        base_url="https://api.anthropic.com/v1/messages",
        default_weight=0.95,
        search_method="brave",
        speed="fast",
        category="paid",
        auth_header="x-api-key",
        auth_prefix="",
        rate_limit_rpm=0,
        rate_limit_rpd=0,
        timeout=20,
        signup_url="https://console.anthropic.com",
        cost_info="$3/$15 per 1M tokens",
        api_style="anthropic",
        notes="High-accuracy paid provider; uses Brave for web context.",
    ),

    "claude_opus": ProviderConfig(
        provider_id="claude_opus",
        display_name="Claude Opus 4.6",
        model_id="claude-opus-4-6-20250514",
        base_url="https://api.anthropic.com/v1/messages",
        default_weight=0.88,
        search_method="brave",
        speed="normal",
        category="paid",
        auth_header="x-api-key",
        auth_prefix="",
        rate_limit_rpm=0,
        rate_limit_rpd=0,
        timeout=30,
        signup_url="https://console.anthropic.com",
        cost_info="$15/$75 per 1M tokens",
        api_style="anthropic",
        notes="Highest-quality reasoning; slower and more expensive than Sonnet.",
    ),

    "perplexity": ProviderConfig(
        provider_id="perplexity",
        display_name="Perplexity Sonar Pro",
        model_id="sonar-pro",
        base_url="https://api.perplexity.ai/chat/completions",
        default_weight=0.95,
        search_method="native",
        speed="normal",
        category="paid",
        auth_header="Authorization",
        auth_prefix="Bearer ",
        rate_limit_rpm=0,
        rate_limit_rpd=0,
        timeout=20,
        signup_url="https://www.perplexity.ai/settings/api",
        cost_info="$3/$15 + search",
        api_style="perplexity",
        notes="Built-in real-time web search; high factuality rating.",
    ),

    "openai_gpt55": ProviderConfig(
        provider_id="openai_gpt55",
        display_name="OpenAI GPT-5.5",
        model_id="gpt-5.5",
        base_url="https://api.openai.com/v1/chat/completions",
        default_weight=0.93,
        search_method="native",
        speed="normal",
        category="paid",
        auth_header="Authorization",
        auth_prefix="Bearer ",
        rate_limit_rpm=0,
        rate_limit_rpd=0,
        timeout=20,
        signup_url="https://platform.openai.com",
        cost_info="$5/$30",
        api_style="openai_native",
        notes="Native web search via Responses API.",
    ),

    "openai_gpt54_mini": ProviderConfig(
        provider_id="openai_gpt54_mini",
        display_name="OpenAI GPT-5.4 mini",
        model_id="gpt-5.4-mini",
        base_url="https://api.openai.com/v1/chat/completions",
        default_weight=0.82,
        search_method="native",
        speed="fast",
        category="paid",
        auth_header="Authorization",
        auth_prefix="Bearer ",
        rate_limit_rpm=0,
        rate_limit_rpd=0,
        timeout=20,
        signup_url="https://platform.openai.com",
        cost_info="$0.75/$4.50",
        api_style="openai_native",
        notes="Efficient mid-tier model with native search.",
    ),

    "openai_gpt54_nano": ProviderConfig(
        provider_id="openai_gpt54_nano",
        display_name="OpenAI GPT-5.4 nano",
        model_id="gpt-5.4-nano",
        base_url="https://api.openai.com/v1/chat/completions",
        default_weight=0.72,
        search_method="native",
        speed="fast",
        category="paid",
        auth_header="Authorization",
        auth_prefix="Bearer ",
        rate_limit_rpm=0,
        rate_limit_rpd=0,
        timeout=20,
        signup_url="https://platform.openai.com",
        cost_info="$0.20/$1.25",
        api_style="openai_native",
        notes="Low-cost fast option with native search.",
    ),

    "openai_gpt5_nano": ProviderConfig(
        provider_id="openai_gpt5_nano",
        display_name="OpenAI GPT-5 nano",
        model_id="gpt-5-nano",
        base_url="https://api.openai.com/v1/chat/completions",
        default_weight=0.68,
        search_method="native",
        speed="fast",
        category="paid",
        auth_header="Authorization",
        auth_prefix="Bearer ",
        rate_limit_rpm=0,
        rate_limit_rpd=0,
        timeout=20,
        signup_url="https://platform.openai.com",
        cost_info="$0.05/$0.40",
        api_style="openai_native",
        notes="Ultra-low-cost; suitable for high-volume preliminary checks.",
    ),

    "gemini_pro": ProviderConfig(
        provider_id="gemini_pro",
        display_name="Gemini 3.1 Pro",
        model_id="gemini-3.1-pro",
        base_url=(
            "https://generativelanguage.googleapis.com/v1beta/models/"
            "gemini-3.1-pro:generateContent"
        ),
        default_weight=0.90,
        search_method="native",
        speed="normal",
        category="paid",
        auth_header="x-goog-api-key",
        auth_prefix="",
        rate_limit_rpm=0,
        rate_limit_rpd=0,
        timeout=20,
        signup_url="https://aistudio.google.com",
        cost_info="$1.25/$10",
        api_style="gemini",
        notes="Paid Gemini tier with higher quality than Flash Lite.",
    ),
}


# ════════════════════════════════════════════════════════════════════════════
# Helper Functions
# ════════════════════════════════════════════════════════════════════════════

def get_provider_config(provider_id: str) -> ProviderConfig | None:
    """Return the ProviderConfig for provider_id, or None if not found."""
    return PROVIDER_REGISTRY.get(provider_id)


def get_enabled_providers(settings: dict) -> list[ProviderConfig]:
    """Return ProviderConfig objects for all providers that are both enabled
    AND have an API key set in settings.

    settings structure expected:
        settings["providers"][provider_id]["enabled"] — truthy
        settings["providers"][provider_id]["api_key"]  — non-empty string
    """
    providers_cfg = settings.get("providers", {})
    enabled = []
    for pid, config in PROVIDER_REGISTRY.items():
        provider_section = providers_cfg.get(pid, {})
        is_enabled = provider_section.get("enabled", False)
        api_key = provider_section.get("api_key", "")
        if is_enabled and api_key:
            enabled.append(config)
    return enabled


def get_provider_api_key(provider_id: str, settings: dict) -> str:
    """Return the API key for a provider from settings, or empty string."""
    providers_cfg = settings.get("providers", {})
    provider_section = providers_cfg.get(provider_id, {})
    return provider_section.get("api_key", "")


def get_provider_weight(provider_id: str, settings: dict) -> float:
    """Return the effective weight for a provider.

    Priority:
    1. settings["weights"][provider_id] if present
    2. ProviderConfig.default_weight
    Result is clamped to [0.01, 1.0].
    """
    config = PROVIDER_REGISTRY.get(provider_id)
    if config is None:
        return 0.01

    weights_cfg = settings.get("weights", {})
    custom = weights_cfg.get(provider_id)
    try:
        weight = float(custom) if custom is not None else config.default_weight
    except (TypeError, ValueError):
        weight = config.default_weight

    return max(0.01, min(1.0, weight))


def get_brave_api_key(settings: dict) -> str:
    """Return the shared Brave Search API key from settings."""
    return settings.get("brave_api_key", "")


def needs_brave_search(provider_id: str) -> bool:
    """Return True if this provider uses Brave for web context."""
    config = PROVIDER_REGISTRY.get(provider_id)
    if config is None:
        return False
    return config.search_method == "brave"


# ════════════════════════════════════════════════════════════════════════════
# Shared Fact-Checking System Prompt
# ════════════════════════════════════════════════════════════════════════════

SYSTEM_PROMPT = """\
You are a precise fact-checking assistant for live political speech.
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
- Never fabricate sources; if you cannot verify via web search, use verdict "UNVERIFIABLE"\
"""


# ════════════════════════════════════════════════════════════════════════════
# Brave Search
# ════════════════════════════════════════════════════════════════════════════

def brave_search(query: str, brave_key: str, count: int = 5) -> list[dict]:
    """Call Brave Search API and return a list of result dicts.

    Each dict contains: url, title, snippet.
    Returns an empty list on any error.
    """
    url = "https://api.search.brave.com/res/v1/web/search"
    headers = {
        "Accept": "application/json",
        "Accept-Encoding": "gzip",
        "X-Subscription-Token": brave_key,
    }
    params = {"q": query, "count": count}
    try:
        resp = requests.get(url, headers=headers, params=params, timeout=10)
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
    except Exception as exc:
        log.warning("brave_search error: %s", exc)
        return []


def format_search_snippets(results: list[dict]) -> str:
    """Format Brave search results as numbered lines.

    Returns "" for empty results.
    Format: [N] Title — Snippet
    """
    if not results:
        return ""
    lines = []
    for i, r in enumerate(results, start=1):
        title = r.get("title", "")
        snippet = r.get("snippet", "")
        lines.append(f"[{i}] {title} — {snippet}")
    return "\n".join(lines)


# ════════════════════════════════════════════════════════════════════════════
# JSON parsing helpers
# ════════════════════════════════════════════════════════════════════════════

def _parse_verdict_json(raw_text: str) -> dict | None:
    """Strip markdown fences and parse JSON from raw LLM response text.

    Returns the parsed dict on success, or None if parsing fails.
    """
    text = raw_text.strip()
    # Strip common markdown code fences (```json ... ``` or ``` ... ```)
    if text.startswith("```"):
        # Remove opening fence line
        first_newline = text.find("\n")
        if first_newline != -1:
            text = text[first_newline + 1:]
        # Remove closing fence
        if text.endswith("```"):
            text = text[: text.rfind("```")].rstrip()
    text = text.strip()
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
        return None
    except (json.JSONDecodeError, ValueError) as exc:
        log.debug("_parse_verdict_json failed: %s", exc)
        return None


# ════════════════════════════════════════════════════════════════════════════
# ProviderResult builder helpers
# ════════════════════════════════════════════════════════════════════════════

def _build_provider_result(
    provider_id: str,
    parsed_dict: dict,
    sources: list[dict],
    latency_ms: int,
) -> ProviderResult:
    """Convert a parsed JSON verdict dict into a ProviderResult."""
    return ProviderResult(
        provider_id=provider_id,
        verdict=parsed_dict.get("verdict"),
        accuracy_score=parsed_dict.get("accuracy_score"),
        assessment=parsed_dict.get("assessment"),
        claim=parsed_dict.get("claim"),
        sources=sources,
        language_signals=parsed_dict.get("language_signals"),
        error=None,
        latency_ms=latency_ms,
        result_type=parsed_dict.get("type", "fact_claim"),
    )


def _error_result(provider_id: str, error_msg: str) -> ProviderResult:
    """Create a ProviderResult representing a failed/errored call."""
    return ProviderResult(
        provider_id=provider_id,
        verdict=None,
        accuracy_score=None,
        assessment=None,
        claim=None,
        sources=[],
        language_signals=None,
        error=error_msg,
        latency_ms=0,
        result_type="fact_claim",
    )


# ════════════════════════════════════════════════════════════════════════════
# OpenAI-Compatible Caller
# ════════════════════════════════════════════════════════════════════════════

def call_openai_compatible(
    provider_id: str,
    claim: str,
    search_context: str,
    settings: dict,
) -> ProviderResult:
    """Call any OpenAI-compatible API (Cerebras, Mistral, GitHub Models,
    OpenRouter, OVHcloud, HuggingFace) and return a ProviderResult.

    Parameters
    ----------
    provider_id:     Key in PROVIDER_REGISTRY.
    claim:           The raw claim text to fact-check.
    search_context:  Pre-formatted Brave search snippets (or "").
    settings:        Full plugin settings dict (providers, brave_api_key, …).
    """
    cfg = PROVIDER_REGISTRY.get(provider_id)
    if cfg is None:
        return _error_result(provider_id, f"Unknown provider: {provider_id!r}")

    api_key = get_provider_api_key(provider_id, settings)
    if not api_key:
        return _error_result(provider_id, "API key not configured")

    # Build user message
    user_content = claim
    if search_context:
        user_content = (
            f"{claim}\n\nWeb search results for context:\n{search_context}"
        )

    payload = {
        "model": cfg.model_id,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        "temperature": 0.1,
        "max_tokens": 1000,
    }

    auth_value = f"{cfg.auth_prefix}{api_key}"
    headers = {
        "Content-Type": "application/json",
        cfg.auth_header: auth_value,
    }

    t0 = time.monotonic()
    try:
        resp = requests.post(
            cfg.base_url,
            headers=headers,
            json=payload,
            timeout=cfg.timeout,
        )
        resp.raise_for_status()
        data = resp.json()
    except requests.exceptions.Timeout:
        return _error_result(provider_id, "Request timed out")
    except requests.exceptions.HTTPError as exc:
        return _error_result(provider_id, f"HTTP {exc.response.status_code}: {exc.response.text[:200]}")
    except Exception as exc:
        return _error_result(provider_id, f"Request error: {exc}")

    latency_ms = int((time.monotonic() - t0) * 1000)

    try:
        raw_text = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        return _error_result(provider_id, f"Unexpected response shape: {exc}")

    parsed = _parse_verdict_json(raw_text)
    if parsed is None:
        return _error_result(
            provider_id,
            f"Failed to parse JSON from response: {raw_text[:200]}",
        )

    # Sources come from Brave search context (no inline citations in openai-compat)
    sources: list[dict] = []
    if search_context:
        # Reconstruct minimal source list from whatever was searched
        # (full source objects are available to caller if they passed them in;
        #  here we just record that web context was used)
        sources = [{"url": "", "title": "Brave Search", "snippet": ""}]

    return _build_provider_result(provider_id, parsed, sources, latency_ms)


# ════════════════════════════════════════════════════════════════════════════
# Gemini Caller (gemini_flash_lite + gemini_pro)
# ════════════════════════════════════════════════════════════════════════════

def call_gemini(
    provider_id: str,
    claim: str,
    search_context: str,
    settings: dict,
) -> ProviderResult:
    """Call the Google Gemini generateContent API with native Google Search
    grounding enabled.  Falls back to no-tools call on HTTP 429.

    Parameters
    ----------
    provider_id:     Key in PROVIDER_REGISTRY ("gemini_flash_lite" or "gemini_pro").
    claim:           The raw claim text to fact-check.
    search_context:  Pre-formatted Brave snippets (ignored — Gemini uses native search).
    settings:        Full plugin settings dict.
    """
    cfg = PROVIDER_REGISTRY.get(provider_id)
    if cfg is None:
        return _error_result(provider_id, f"Unknown provider: {provider_id!r}")

    api_key = get_provider_api_key(provider_id, settings)
    if not api_key:
        return _error_result(provider_id, "API key not configured")

    user_prompt = f"{SYSTEM_PROMPT}\n\n{claim}"
    if search_context:
        user_prompt = f"{user_prompt}\n\nWeb search results for context:\n{search_context}"

    payload = {
        "contents": [{"parts": [{"text": user_prompt}]}],
        "tools": [{"google_search": {}}],
        "generationConfig": {"temperature": 0.1, "maxOutputTokens": 1000},
    }

    headers = {
        "Content-Type": "application/json",
        cfg.auth_header: api_key,   # x-goog-api-key, no prefix
    }

    url = cfg.base_url  # model is already baked into the URL

    t0 = time.monotonic()
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=cfg.timeout)

        # Grounding fallback: retry without tools on 429 (quota exceeded for grounding)
        if resp.status_code == 429:
            log.warning("call_gemini: 429 on %s — retrying without grounding tools", provider_id)
            payload_no_tools = {k: v for k, v in payload.items() if k != "tools"}
            resp = requests.post(url, headers=headers, json=payload_no_tools, timeout=cfg.timeout)

        resp.raise_for_status()
        data = resp.json()
    except requests.exceptions.Timeout:
        return _error_result(provider_id, "Request timed out")
    except requests.exceptions.HTTPError as exc:
        return _error_result(provider_id, f"HTTP {exc.response.status_code}: {exc.response.text[:200]}")
    except Exception as exc:
        return _error_result(provider_id, f"Request error: {exc}")

    latency_ms = int((time.monotonic() - t0) * 1000)

    # Extract text
    try:
        raw_text = data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError, TypeError) as exc:
        return _error_result(provider_id, f"Unexpected response shape: {exc}")

    # Extract grounding sources
    sources: list[dict] = []
    try:
        chunks = (
            data["candidates"][0]
            .get("groundingMetadata", {})
            .get("groundingChunks", [])
        )
        for chunk in chunks:
            web = chunk.get("web", {})
            uri = web.get("uri", "")
            title = web.get("title", "")
            if uri:
                sources.append({"url": uri, "title": title, "snippet": ""})
    except Exception:
        pass  # sources remain empty; non-fatal

    parsed = _parse_verdict_json(raw_text)
    if parsed is None:
        return _error_result(
            provider_id,
            f"Failed to parse JSON from response: {raw_text[:200]}",
        )

    return _build_provider_result(provider_id, parsed, sources, latency_ms)


# ════════════════════════════════════════════════════════════════════════════
# Cohere Caller
# ════════════════════════════════════════════════════════════════════════════

def call_cohere(
    provider_id: str,
    claim: str,
    search_context: str,
    settings: dict,
) -> ProviderResult:
    """Call the Cohere Chat v2 API with the built-in web-search connector.

    Parameters
    ----------
    provider_id:     Key in PROVIDER_REGISTRY ("cohere").
    claim:           The raw claim text to fact-check.
    search_context:  Pre-formatted Brave snippets (ignored — Cohere uses native search).
    settings:        Full plugin settings dict.
    """
    cfg = PROVIDER_REGISTRY.get(provider_id)
    if cfg is None:
        return _error_result(provider_id, f"Unknown provider: {provider_id!r}")

    api_key = get_provider_api_key(provider_id, settings)
    if not api_key:
        return _error_result(provider_id, "API key not configured")

    user_content = f"{SYSTEM_PROMPT}\n\n{claim}"
    if search_context:
        user_content = f"{user_content}\n\nWeb search results for context:\n{search_context}"

    payload = {
        "model": cfg.model_id,
        "messages": [{"role": "user", "content": user_content}],
        "connectors": [{"id": "web-search"}],
        "temperature": 0.1,
    }

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }

    t0 = time.monotonic()
    try:
        resp = requests.post(cfg.base_url, headers=headers, json=payload, timeout=cfg.timeout)
        resp.raise_for_status()
        data = resp.json()
    except requests.exceptions.Timeout:
        return _error_result(provider_id, "Request timed out")
    except requests.exceptions.HTTPError as exc:
        return _error_result(provider_id, f"HTTP {exc.response.status_code}: {exc.response.text[:200]}")
    except Exception as exc:
        return _error_result(provider_id, f"Request error: {exc}")

    latency_ms = int((time.monotonic() - t0) * 1000)

    # Extract text
    try:
        raw_text = data["message"]["content"][0]["text"]
    except (KeyError, IndexError, TypeError) as exc:
        return _error_result(provider_id, f"Unexpected response shape: {exc}")

    # Extract sources from citations.documents (may not exist)
    sources: list[dict] = []
    try:
        docs = data["message"]["citations"]["documents"]
        for doc in docs:
            url = doc.get("url", "")
            title = doc.get("title", "")
            if url:
                sources.append({"url": url, "title": title, "snippet": ""})
    except Exception:
        pass  # sources remain empty; non-fatal

    parsed = _parse_verdict_json(raw_text)
    if parsed is None:
        return _error_result(
            provider_id,
            f"Failed to parse JSON from response: {raw_text[:200]}",
        )

    return _build_provider_result(provider_id, parsed, sources, latency_ms)


# ════════════════════════════════════════════════════════════════════════════
# Perplexity Caller
# ════════════════════════════════════════════════════════════════════════════

def call_perplexity(
    provider_id: str,
    claim: str,
    search_context: str,
    settings: dict,
) -> ProviderResult:
    """Call the Perplexity Sonar API (OpenAI-shaped with native search + citations).

    Parameters
    ----------
    provider_id:     Key in PROVIDER_REGISTRY ("perplexity").
    claim:           The raw claim text to fact-check.
    search_context:  Pre-formatted Brave snippets (ignored — Perplexity searches natively).
    settings:        Full plugin settings dict.
    """
    cfg = PROVIDER_REGISTRY.get(provider_id)
    if cfg is None:
        return _error_result(provider_id, f"Unknown provider: {provider_id!r}")

    api_key = get_provider_api_key(provider_id, settings)
    if not api_key:
        return _error_result(provider_id, "API key not configured")

    user_content = claim
    if search_context:
        user_content = f"{claim}\n\nWeb search results for context:\n{search_context}"

    payload = {
        "model": cfg.model_id,  # sonar-pro (from registry)
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        "temperature": 0.1,
        "max_tokens": 1000,
    }

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }

    t0 = time.monotonic()
    try:
        resp = requests.post(cfg.base_url, headers=headers, json=payload, timeout=cfg.timeout)
        resp.raise_for_status()
        data = resp.json()
    except requests.exceptions.Timeout:
        return _error_result(provider_id, "Request timed out")
    except requests.exceptions.HTTPError as exc:
        return _error_result(provider_id, f"HTTP {exc.response.status_code}: {exc.response.text[:200]}")
    except Exception as exc:
        return _error_result(provider_id, f"Request error: {exc}")

    latency_ms = int((time.monotonic() - t0) * 1000)

    # Extract text
    try:
        raw_text = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        return _error_result(provider_id, f"Unexpected response shape: {exc}")

    # Extract citations — can be strings (URLs) or dicts with url/title
    sources: list[dict] = []
    try:
        for citation in data.get("citations", []):
            if isinstance(citation, str):
                sources.append({"url": citation, "title": "", "snippet": ""})
            elif isinstance(citation, dict):
                sources.append({
                    "url": citation.get("url", ""),
                    "title": citation.get("title", ""),
                    "snippet": "",
                })
    except Exception:
        pass  # sources remain empty; non-fatal

    parsed = _parse_verdict_json(raw_text)
    if parsed is None:
        return _error_result(
            provider_id,
            f"Failed to parse JSON from response: {raw_text[:200]}",
        )

    return _build_provider_result(provider_id, parsed, sources, latency_ms)


# ════════════════════════════════════════════════════════════════════════════
# OpenAI Native Web Search Caller (GPT-5.x series)
# ════════════════════════════════════════════════════════════════════════════

def call_openai_native(
    provider_id: str,
    claim: str,
    search_context: str,
    settings: dict,
) -> ProviderResult:
    """Call OpenAI GPT-5.x models with the native web_search tool.

    Parameters
    ----------
    provider_id:     Key in PROVIDER_REGISTRY (e.g. "openai_gpt55").
    claim:           The raw claim text to fact-check.
    search_context:  Pre-formatted Brave snippets (ignored — OpenAI searches natively).
    settings:        Full plugin settings dict.
    """
    cfg = PROVIDER_REGISTRY.get(provider_id)
    if cfg is None:
        return _error_result(provider_id, f"Unknown provider: {provider_id!r}")

    api_key = get_provider_api_key(provider_id, settings)
    if not api_key:
        return _error_result(provider_id, "API key not configured")

    user_content = claim
    if search_context:
        user_content = f"{claim}\n\nWeb search results for context:\n{search_context}"

    payload = {
        "model": cfg.model_id,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        "tools": [{"type": "web_search"}],
        "temperature": 0.1,
        "max_tokens": 1000,
    }

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }

    t0 = time.monotonic()
    try:
        resp = requests.post(cfg.base_url, headers=headers, json=payload, timeout=cfg.timeout)
        resp.raise_for_status()
        data = resp.json()
    except requests.exceptions.Timeout:
        return _error_result(provider_id, "Request timed out")
    except requests.exceptions.HTTPError as exc:
        return _error_result(provider_id, f"HTTP {exc.response.status_code}: {exc.response.text[:200]}")
    except Exception as exc:
        return _error_result(provider_id, f"Request error: {exc}")

    latency_ms = int((time.monotonic() - t0) * 1000)

    # Extract text
    try:
        raw_text = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        return _error_result(provider_id, f"Unexpected response shape: {exc}")

    parsed = _parse_verdict_json(raw_text)
    if parsed is None:
        return _error_result(
            provider_id,
            f"Failed to parse JSON from response: {raw_text[:200]}",
        )

    # No structured citation extraction for OpenAI native; sources come from model internals
    return _build_provider_result(provider_id, parsed, [], latency_ms)


# ════════════════════════════════════════════════════════════════════════════
# Claude Caller (Anthropic Python SDK — claude_sonnet + claude_opus)
# ════════════════════════════════════════════════════════════════════════════

import threading as _threading

_anthropic_client = None
_anthropic_client_key: str = ""
_anthropic_client_lock = _threading.Lock()


def _get_anthropic_client(api_key: str):
    """Return a singleton Anthropic client, recreating it if the key changes.

    Thread-safe via _anthropic_client_lock.
    """
    import anthropic  # lazy import — package may not be installed

    global _anthropic_client, _anthropic_client_key  # noqa: PLW0603

    with _anthropic_client_lock:
        if _anthropic_client is None or _anthropic_client_key != api_key:
            _anthropic_client = anthropic.Anthropic(api_key=api_key)
            _anthropic_client_key = api_key
        return _anthropic_client


def _extract_claude_sources(message) -> list[dict]:
    """Extract URLs from a Claude message that used the web_search tool.

    Inspects ``web_search_tool_result`` content blocks and any URL references
    found in text blocks.  Returns a list of {url, title, snippet} dicts.
    """
    sources: list[dict] = []
    seen: set[str] = set()

    try:
        for block in message.content:
            block_type = getattr(block, "type", None)

            if block_type == "web_search_tool_result":
                # Each result block contains a list of search result entries
                for entry in getattr(block, "content", []):
                    url = getattr(entry, "url", "") or ""
                    title = getattr(entry, "title", "") or ""
                    if url and url not in seen:
                        seen.add(url)
                        sources.append({"url": url, "title": title, "snippet": ""})

            elif block_type == "text":
                # Some models embed [url] or citation markers in the text block
                # We skip inline parsing here — structured grounding covers it
                pass
    except Exception as exc:
        log.debug("_extract_claude_sources: %s", exc)

    return sources


def call_claude(
    provider_id: str,
    claim: str,
    search_context: str,
    settings: dict,
) -> ProviderResult:
    """Call Anthropic Claude via the official Python SDK with native web search.

    Parameters
    ----------
    provider_id:     Key in PROVIDER_REGISTRY ("claude_sonnet" or "claude_opus").
    claim:           The raw claim text to fact-check.
    search_context:  Pre-formatted Brave snippets appended to the user prompt if provided.
    settings:        Full plugin settings dict.
    """
    cfg = PROVIDER_REGISTRY.get(provider_id)
    if cfg is None:
        return _error_result(provider_id, f"Unknown provider: {provider_id!r}")

    api_key = get_provider_api_key(provider_id, settings)
    if not api_key:
        return _error_result(provider_id, "API key not configured")

    try:
        client = _get_anthropic_client(api_key)
    except ImportError:
        return _error_result(provider_id, "anthropic SDK not installed — run: pip install anthropic")
    except Exception as exc:
        return _error_result(provider_id, f"Failed to create Anthropic client: {exc}")

    user_prompt = claim
    if search_context:
        user_prompt = f"{claim}\n\nWeb search results for context:\n{search_context}"

    t0 = time.monotonic()
    try:
        message = client.messages.create(
            model=cfg.model_id,
            max_tokens=1000,
            system=SYSTEM_PROMPT,
            tools=[{"type": "web_search_20250305", "name": "web_search"}],
            messages=[{"role": "user", "content": user_prompt}],
        )
    except Exception as exc:
        return _error_result(provider_id, f"Anthropic API error: {exc}")

    latency_ms = int((time.monotonic() - t0) * 1000)

    # Extract the final text block
    try:
        raw_text = next(
            b.text for b in message.content if getattr(b, "type", None) == "text"
        )
    except StopIteration:
        return _error_result(provider_id, "No text block in Claude response")
    except Exception as exc:
        return _error_result(provider_id, f"Unexpected response shape: {exc}")

    sources = _extract_claude_sources(message)

    parsed = _parse_verdict_json(raw_text)
    if parsed is None:
        return _error_result(
            provider_id,
            f"Failed to parse JSON from response: {raw_text[:200]}",
        )

    return _build_provider_result(provider_id, parsed, sources, latency_ms)


# ════════════════════════════════════════════════════════════════════════════
# Provider Dispatch
# ════════════════════════════════════════════════════════════════════════════

# Maps api_style → caller function.  All callers share the same signature:
#   (provider_id: str, claim: str, search_context: str, settings: dict) -> ProviderResult
_CALLERS: dict[str, object] = {
    "openai": call_openai_compatible,
    "gemini": call_gemini,
    "anthropic": call_claude,
    "cohere": call_cohere,
    "perplexity": call_perplexity,
    "openai_native": call_openai_native,
}


def call_provider(
    provider_id: str,
    claim: str,
    search_context: str,
    settings: dict,
) -> ProviderResult:
    """Dispatch a fact-check call to the correct provider caller.

    Looks up the ProviderConfig for *provider_id*, selects the caller function
    from ``_CALLERS`` by ``cfg.api_style``, and delegates.  Any unhandled
    exception is caught and returned as an error ProviderResult.

    Parameters
    ----------
    provider_id:     Key in PROVIDER_REGISTRY.
    claim:           The raw claim text to fact-check.
    search_context:  Pre-formatted Brave search snippets (or "").
    settings:        Full plugin settings dict.
    """
    cfg = PROVIDER_REGISTRY.get(provider_id)
    if cfg is None:
        return _error_result(provider_id, f"Unknown provider: {provider_id!r}")

    caller = _CALLERS.get(cfg.api_style)
    if caller is None:
        return _error_result(
            provider_id,
            f"No caller registered for api_style {cfg.api_style!r}",
        )

    try:
        return caller(provider_id, claim, search_context, settings)  # type: ignore[operator]
    except Exception as exc:
        log.exception("call_provider: unexpected error for %s", provider_id)
        return _error_result(provider_id, f"Unexpected error: {exc}")


# ════════════════════════════════════════════════════════════════════════════
# Claim Classification Stage (Stage 2)
# ════════════════════════════════════════════════════════════════════════════

_CLASSIFICATION_PROMPT = """\
Analyze this transcribed statement. Respond in JSON only.
If it contains a verifiable factual claim, extract it clearly.
If it does not contain a verifiable claim, mark it as not_a_claim.

Statement: "{statement}"

Response format:
{{"is_claim": true/false, "extracted_claim": "clean claim text or null", "search_query": "optimized search query or null"}}\
"""

# Ordered list of providers to try for classification (fast/free-tier first)
CLASSIFICATION_FALLBACK_ORDER: list[str] = [
    "cerebras",
    "github_models",
    "mistral",
    "openrouter",
    "gemini_flash_lite",
]


def _parse_classification_response(raw: str) -> dict | None:
    """Parse a classification JSON response from an LLM.

    Uses _parse_verdict_json for fence-stripping and JSON decoding.
    Returns None if parse fails or "is_claim" key is absent.

    Returns a normalised dict:
        {"is_claim": bool, "extracted_claim": str|None, "search_query": str|None}
    """
    parsed = _parse_verdict_json(raw)
    if parsed is None:
        return None
    if "is_claim" not in parsed:
        return None
    return {
        "is_claim": bool(parsed["is_claim"]),
        "extracted_claim": parsed.get("extracted_claim") or None,
        "search_query": parsed.get("search_query") or None,
    }


def classify_claim(
    statement: str,
    settings: dict,
    preferred_provider: str | None = None,
) -> dict | None:
    """Use a fast LLM to classify whether *statement* contains a verifiable claim.

    Tries providers in CLASSIFICATION_FALLBACK_ORDER, with *preferred_provider*
    moved to the front if specified.  Only providers that have an API key
    configured in *settings* are tried.

    Returns a dict on success:
        {"is_claim": bool, "extracted_claim": str|None, "search_query": str|None}

    Returns None if all providers fail or no API keys are configured.
    """
    order = list(CLASSIFICATION_FALLBACK_ORDER)
    if preferred_provider and preferred_provider in order:
        order.remove(preferred_provider)
        order.insert(0, preferred_provider)
    elif preferred_provider and preferred_provider not in order:
        order.insert(0, preferred_provider)

    prompt = _CLASSIFICATION_PROMPT.format(statement=statement)
    system_msg = "You classify statements as verifiable claims or not. Respond in JSON only."

    for pid in order:
        api_key = get_provider_api_key(pid, settings)
        if not api_key:
            continue

        cfg = get_provider_config(pid)
        if cfg is None:
            continue

        # Build lightweight request (no search context needed)
        if cfg.api_style == "gemini":
            payload: dict = {
                "contents": [{"parts": [{"text": system_msg + "\n\n" + prompt}]}],
                "generationConfig": {"temperature": 0.1, "maxOutputTokens": 300},
            }
        else:
            # OpenAI-compatible shape covers: openai, perplexity, openai_native,
            # cohere (v2 accepts messages), anthropic — all accept messages array
            payload = {
                "model": cfg.model_id,
                "messages": [
                    {"role": "system", "content": system_msg},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.1,
                "max_tokens": 300,
            }

        auth_value = f"{cfg.auth_prefix}{api_key}"
        headers = {
            "Content-Type": "application/json",
            cfg.auth_header: auth_value,
        }

        try:
            resp = requests.post(
                cfg.base_url,
                headers=headers,
                json=payload,
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            log.debug("classify_claim: %s failed: %s", pid, exc)
            continue

        # Extract response text based on api_style
        try:
            if cfg.api_style == "gemini":
                raw_text = data["candidates"][0]["content"]["parts"][0]["text"]
            else:
                raw_text = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            log.debug("classify_claim: unexpected response shape from %s: %s", pid, exc)
            continue

        result = _parse_classification_response(raw_text)
        if result is not None:
            return result

        log.debug("classify_claim: failed to parse response from %s: %s", pid, raw_text[:200])

    return None
