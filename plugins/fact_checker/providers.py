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
