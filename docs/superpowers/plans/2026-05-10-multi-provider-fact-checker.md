# Multi-Provider Consensus Fact Checker — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the single-provider fact checker (Gemini/Groq/Claude/MAGI) with a configurable multi-provider consensus architecture supporting 8 free + 8 paid LLM providers running in parallel with weighted scoring and progressive result delivery.

**Architecture:** 5-stage pipeline — local ONNX filter → free LLM claim classification → shared Brave Search → parallel provider queries → progressive weighted consensus. Providers are registered in a data-driven registry. Each returns a structured `ProviderResult`. The consensus engine calculates weighted verdicts with initial/final progressive delivery. The frontend renders provider checkboxes, weight editors, and consensus display.

**Tech Stack:** Python 3.11+, FastAPI, requests, threading/concurrent.futures, Pydantic. Vanilla JS frontend (no framework). APIs: Google Generative AI, Anthropic, Cerebras, Mistral, GitHub Models, OpenRouter, OVHcloud, Hugging Face, Cohere, Perplexity, OpenAI, Brave Search.

**Design Spec:** `docs/superpowers/specs/2026-05-10-multi-provider-fact-checker-design.md`

---

## File Structure

| Action | File | Responsibility |
|--------|------|---------------|
| Create | `plugins/fact_checker/providers.py` | Provider registry, config dataclass, all 16 API callers, Brave Search sharing, response parsing |
| Create | `plugins/fact_checker/consensus.py` | ConsensusResult dataclass, weighted scoring, progressive delivery thresholds |
| Modify | `plugins/fact_checker/routes.py` | 5-stage pipeline, claim classification, config accessors, status endpoint, response models |
| Modify | `plugins/fact_checker/manifest.json` | New settings_schema with structured provider config |
| Modify | `plugins/fact_checker/panel.html` | Provider settings layout, advanced settings section |
| Modify | `plugins/fact_checker/panel.js` | Provider checkboxes, weight editors, consensus display, settings persistence |
| Modify | `plugins/fact_checker/panel.css` | Styles for provider list, advanced settings, consensus labels |

---

### Task 1: Provider Registry and Data Models

**Files:**
- Create: `plugins/fact_checker/providers.py`

This task creates the provider registry — the data-driven core that every subsequent task depends on. No API calls yet, just configuration and data models.

- [ ] **Step 1: Write test script for provider registry**

Create a quick test to verify the registry loads correctly:

```python
# tests/test_providers_registry.py
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from plugins.fact_checker.providers import (
    ProviderConfig, ProviderResult, PROVIDER_REGISTRY,
    get_enabled_providers, get_provider_config,
)

# Registry should have all 16 providers
assert len(PROVIDER_REGISTRY) == 16, f"Expected 16 providers, got {len(PROVIDER_REGISTRY)}"

# All providers have required fields
for pid, cfg in PROVIDER_REGISTRY.items():
    assert isinstance(cfg, ProviderConfig), f"{pid} is not ProviderConfig"
    assert cfg.provider_id == pid, f"{pid} id mismatch"
    assert cfg.model_id, f"{pid} missing model_id"
    assert cfg.default_weight > 0, f"{pid} weight must be > 0"
    assert cfg.search_method in ("native", "brave"), f"{pid} invalid search_method"
    assert cfg.speed in ("fast", "normal", "slow"), f"{pid} invalid speed"
    assert cfg.category in ("free", "paid"), f"{pid} invalid category"

# get_enabled_providers filters correctly
settings = {
    "providers": {
        "gemini_flash_lite": {"enabled": True, "api_key": "test-key"},
        "cerebras": {"enabled": False, "api_key": ""},
    }
}
enabled = get_enabled_providers(settings)
assert len(enabled) == 1
assert enabled[0].provider_id == "gemini_flash_lite"

# get_provider_config returns None for unknown
assert get_provider_config("nonexistent") is None
assert get_provider_config("gemini_flash_lite") is not None

# ProviderResult can be created
pr = ProviderResult(
    provider_id="gemini_flash_lite",
    verdict="TRUE",
    accuracy_score=95.0,
    assessment="Test assessment",
    claim="Test claim",
    sources=[],
    latency_ms=150,
)
assert pr.verdict == "TRUE"
assert pr.error is None

print("All provider registry tests passed!")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd C:/Users/Laptop/Documents/LinguaTaxi && python tests/test_providers_registry.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'plugins.fact_checker.providers'`

- [ ] **Step 3: Create providers.py with registry and data models**

```python
"""
LinguaTaxi — Fact Checker Provider Registry

Data-driven registry of all 16 LLM providers (8 free + 8 paid).
Each provider has a ProviderConfig describing its API, model, rate limits,
default weight, and search method. API callers are added in subsequent tasks.
"""

import json
import logging
import time
from dataclasses import dataclass, field

log = logging.getLogger("livecaption")


@dataclass
class ProviderConfig:
    provider_id: str
    display_name: str
    model_id: str
    base_url: str
    default_weight: float
    search_method: str          # "native" or "brave"
    speed: str                  # "fast", "normal", "slow"
    category: str               # "free" or "paid"
    auth_header: str            # e.g. "Authorization", "x-goog-api-key", "x-api-key"
    auth_prefix: str            # e.g. "Bearer ", "" (for bare key)
    rate_limit_rpm: int
    rate_limit_rpd: int
    timeout: int                # seconds
    signup_url: str
    cost_info: str              # e.g. "$3/$15 per 1M tokens" or "Free"
    api_style: str              # "openai", "gemini", "anthropic", "cohere", "perplexity"
    notes: str = ""


@dataclass
class ProviderResult:
    provider_id: str
    verdict: str | None = None
    accuracy_score: float | None = None
    assessment: str | None = None
    claim: str | None = None
    sources: list[dict] = field(default_factory=list)
    language_signals: str | None = None
    error: str | None = None
    latency_ms: int = 0
    result_type: str = "fact_claim"


PROVIDER_REGISTRY: dict[str, ProviderConfig] = {
    # ── Free Providers ──
    "gemini_flash_lite": ProviderConfig(
        provider_id="gemini_flash_lite",
        display_name="Gemini 3.1 Flash Lite",
        model_id="gemini-3.1-flash-lite",
        base_url="https://generativelanguage.googleapis.com/v1beta/models/gemini-3.1-flash-lite:generateContent",
        default_weight=0.75,
        search_method="native",
        speed="fast",
        category="free",
        auth_header="x-goog-api-key",
        auth_prefix="",
        rate_limit_rpm=15,
        rate_limit_rpd=500,
        timeout=15,
        signup_url="aistudio.google.com",
        cost_info="Free",
        api_style="gemini",
    ),
    "cerebras": ProviderConfig(
        provider_id="cerebras",
        display_name="Cerebras",
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
        timeout=15,
        signup_url="cerebras.ai",
        cost_info="Free",
        api_style="openai",
    ),
    "mistral": ProviderConfig(
        provider_id="mistral",
        display_name="Mistral AI",
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
        timeout=15,
        signup_url="console.mistral.ai",
        cost_info="Free (Experiment plan)",
        api_style="openai",
        notes="~1B tokens/mo on free Experiment plan",
    ),
    "github_models": ProviderConfig(
        provider_id="github_models",
        display_name="GitHub Models",
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
        timeout=15,
        signup_url="github.com/marketplace/models",
        cost_info="Free for GitHub users",
        api_style="openai",
    ),
    "cohere": ProviderConfig(
        provider_id="cohere",
        display_name="Cohere",
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
        timeout=15,
        signup_url="dashboard.cohere.com",
        cost_info="Free trial (non-commercial)",
        api_style="cohere",
    ),
    "openrouter": ProviderConfig(
        provider_id="openrouter",
        display_name="OpenRouter",
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
        timeout=15,
        signup_url="openrouter.ai",
        cost_info="Free",
        api_style="openai",
    ),
    "ovhcloud": ProviderConfig(
        provider_id="ovhcloud",
        display_name="OVHcloud",
        model_id="Llama-3.3-70B-Instruct",
        base_url="https://oai.endpoints.kepler.ai.cloud.ovh.net/v1/chat/completions",
        default_weight=0.85,
        search_method="brave",
        speed="slow",
        category="free",
        auth_header="Authorization",
        auth_prefix="Bearer ",
        rate_limit_rpm=2,
        rate_limit_rpd=0,
        timeout=30,
        signup_url="endpoints.ai.cloud.ovh.net",
        cost_info="Free (anonymous, 2 RPM)",
        api_style="openai",
        notes="No signup needed for anonymous access",
    ),
    "huggingface": ProviderConfig(
        provider_id="huggingface",
        display_name="Hugging Face",
        model_id="mistralai/Mixtral-8x7B-Instruct-v0.1",
        base_url="https://api-inference.huggingface.co/models/mistralai/Mixtral-8x7B-Instruct-v0.1/v1/chat/completions",
        default_weight=0.60,
        search_method="brave",
        speed="slow",
        category="free",
        auth_header="Authorization",
        auth_prefix="Bearer ",
        rate_limit_rpm=0,
        rate_limit_rpd=1000,
        timeout=30,
        signup_url="huggingface.co",
        cost_info="Free",
        api_style="openai",
    ),
    # ── Paid Providers ──
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
        rate_limit_rpm=50,
        rate_limit_rpd=0,
        timeout=15,
        signup_url="console.anthropic.com",
        cost_info="$3/$15 per 1M tokens",
        api_style="anthropic",
        notes="Lowest hallucination rate (34%)",
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
        rate_limit_rpm=50,
        rate_limit_rpd=0,
        timeout=15,
        signup_url="console.anthropic.com",
        cost_info="$5/$25 per 1M tokens",
        api_style="anthropic",
        notes="Deepest reasoning, higher hallucination (60%)",
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
        rate_limit_rpm=50,
        rate_limit_rpd=0,
        timeout=15,
        signup_url="docs.perplexity.ai",
        cost_info="$3/$15 per 1M tokens + search",
        api_style="perplexity",
        notes="85.8% F-score, purpose-built for fact verification",
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
        rate_limit_rpm=500,
        rate_limit_rpd=0,
        timeout=15,
        signup_url="platform.openai.com",
        cost_info="$5/$30 per 1M tokens",
        api_style="openai_native",
        notes="Latest frontier, 1.05M context, reasoning",
    ),
    "openai_gpt54_mini": ProviderConfig(
        provider_id="openai_gpt54_mini",
        display_name="OpenAI GPT-5.4-mini",
        model_id="gpt-5.4-mini",
        base_url="https://api.openai.com/v1/chat/completions",
        default_weight=0.82,
        search_method="native",
        speed="fast",
        category="paid",
        auth_header="Authorization",
        auth_prefix="Bearer ",
        rate_limit_rpm=500,
        rate_limit_rpd=0,
        timeout=15,
        signup_url="platform.openai.com",
        cost_info="$0.75/$4.50 per 1M tokens",
        api_style="openai_native",
        notes="Reasoning + search, good value",
    ),
    "openai_gpt54_nano": ProviderConfig(
        provider_id="openai_gpt54_nano",
        display_name="OpenAI GPT-5.4-nano",
        model_id="gpt-5.4-nano",
        base_url="https://api.openai.com/v1/chat/completions",
        default_weight=0.72,
        search_method="native",
        speed="fast",
        category="paid",
        auth_header="Authorization",
        auth_prefix="Bearer ",
        rate_limit_rpm=1000,
        rate_limit_rpd=0,
        timeout=15,
        signup_url="platform.openai.com",
        cost_info="$0.20/$1.25 per 1M tokens",
        api_style="openai_native",
        notes="Ultra-cheap, structured outputs",
    ),
    "openai_gpt5_nano": ProviderConfig(
        provider_id="openai_gpt5_nano",
        display_name="OpenAI GPT-5-nano",
        model_id="gpt-5-nano",
        base_url="https://api.openai.com/v1/chat/completions",
        default_weight=0.68,
        search_method="native",
        speed="fast",
        category="paid",
        auth_header="Authorization",
        auth_prefix="Bearer ",
        rate_limit_rpm=1000,
        rate_limit_rpd=0,
        timeout=15,
        signup_url="platform.openai.com",
        cost_info="$0.05/$0.40 per 1M tokens",
        api_style="openai_native",
        notes="Near-free (~2,500 checks per $1 input)",
    ),
    "gemini_pro": ProviderConfig(
        provider_id="gemini_pro",
        display_name="Google Gemini 3.1 Pro",
        model_id="gemini-3.1-pro",
        base_url="https://generativelanguage.googleapis.com/v1beta/models/gemini-3.1-pro:generateContent",
        default_weight=0.90,
        search_method="native",
        speed="normal",
        category="paid",
        auth_header="x-goog-api-key",
        auth_prefix="",
        rate_limit_rpm=15,
        rate_limit_rpd=0,
        timeout=15,
        signup_url="aistudio.google.com",
        cost_info="$1.25/$10 per 1M tokens",
        api_style="gemini",
        notes="Top parametric factuality + grounding",
    ),
}


def get_provider_config(provider_id: str) -> ProviderConfig | None:
    return PROVIDER_REGISTRY.get(provider_id)


def get_enabled_providers(settings: dict) -> list[ProviderConfig]:
    providers_cfg = settings.get("providers", {})
    enabled = []
    for pid, cfg in PROVIDER_REGISTRY.items():
        pcfg = providers_cfg.get(pid, {})
        if pcfg.get("enabled") and pcfg.get("api_key"):
            enabled.append(cfg)
    return enabled


def get_provider_api_key(provider_id: str, settings: dict) -> str:
    return settings.get("providers", {}).get(provider_id, {}).get("api_key", "")


def get_provider_weight(provider_id: str, settings: dict) -> float:
    cfg = PROVIDER_REGISTRY.get(provider_id)
    if not cfg:
        return 0.5
    custom_weights = settings.get("weights", {})
    if provider_id in custom_weights:
        try:
            w = float(custom_weights[provider_id])
            return max(0.01, min(1.0, w))
        except (ValueError, TypeError):
            pass
    return cfg.default_weight


def get_brave_api_key(settings: dict) -> str:
    return settings.get("brave_api_key", "")


def needs_brave_search(provider_id: str) -> bool:
    cfg = PROVIDER_REGISTRY.get(provider_id)
    return cfg is not None and cfg.search_method == "brave"
```

- [ ] **Step 4: Create test directory and run test**

Run: `mkdir -p tests && cd C:/Users/Laptop/Documents/LinguaTaxi && python tests/test_providers_registry.py`
Expected: PASS — "All provider registry tests passed!"

- [ ] **Step 5: Commit**

```bash
git add plugins/fact_checker/providers.py tests/test_providers_registry.py
git commit -m "[feat] add provider registry with 16 LLM providers and data models"
```

---

### Task 2: Brave Search Sharing and OpenAI-Compatible Callers

**Files:**
- Modify: `plugins/fact_checker/providers.py`

Implement the shared Brave Search call and the generic OpenAI-compatible chat completions caller used by 6 free providers (Cerebras, Mistral, GitHub Models, OpenRouter, OVHcloud, Hugging Face).

- [ ] **Step 1: Write test for Brave Search formatting**

```python
# tests/test_brave_search.py
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from plugins.fact_checker.providers import format_search_snippets

# Test snippet formatting
results = [
    {"url": "https://example.com/1", "title": "Article One", "snippet": "First result text"},
    {"url": "https://example.com/2", "title": "Article Two", "snippet": "Second result text"},
]
formatted = format_search_snippets(results)
assert "[1]" in formatted
assert "[2]" in formatted
assert "Article One" in formatted
assert "First result text" in formatted

# Empty results
assert format_search_snippets([]) == ""

print("Brave Search formatting tests passed!")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python tests/test_brave_search.py`
Expected: FAIL — `ImportError: cannot import name 'format_search_snippets'`

- [ ] **Step 3: Add Brave Search and OpenAI-compatible caller to providers.py**

Add after the existing helper functions at the bottom of `providers.py`:

```python
import requests

# ── Shared system prompt (same as existing in routes.py) ──
SYSTEM_PROMPT = """You are a precise fact-checking assistant for live political speech.
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


# ── Brave Search ──

_BRAVE_SEARCH_URL = "https://api.search.brave.com/res/v1/web/search"


def brave_search(query: str, brave_key: str, count: int = 5) -> list[dict]:
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


def format_search_snippets(results: list[dict]) -> str:
    if not results:
        return ""
    lines = []
    for i, r in enumerate(results, 1):
        lines.append(f"[{i}] {r.get('title', '')} — {r.get('snippet', '')}")
    return "\n".join(lines)


# ── Response parsing ──

def _parse_verdict_json(raw_text: str) -> dict | None:
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


def _build_provider_result(provider_id: str, parsed: dict, sources: list[dict],
                           latency_ms: int) -> ProviderResult:
    return ProviderResult(
        provider_id=provider_id,
        verdict=parsed.get("verdict"),
        accuracy_score=parsed.get("accuracy_score"),
        assessment=parsed.get("assessment"),
        claim=parsed.get("claim"),
        sources=sources,
        language_signals=parsed.get("language_signals"),
        latency_ms=latency_ms,
        result_type=parsed.get("type", "fact_claim"),
    )


def _error_result(provider_id: str, error: str) -> ProviderResult:
    return ProviderResult(provider_id=provider_id, error=error, result_type="ambiguous")


# ── OpenAI-compatible caller ──
# Used by: Cerebras, Mistral, GitHub Models, OpenRouter, OVHcloud, Hugging Face

def call_openai_compatible(provider_id: str, claim: str, search_context: str,
                           settings: dict) -> ProviderResult:
    cfg = PROVIDER_REGISTRY.get(provider_id)
    if not cfg:
        return _error_result(provider_id, f"Unknown provider: {provider_id}")

    api_key = get_provider_api_key(provider_id, settings)
    if not api_key:
        return _error_result(provider_id, f"{cfg.display_name} API key not set")

    user_prompt = f'Analyze this statement: "{claim}"'
    if search_context:
        user_prompt += (
            f"\n\nWeb search results for context (use these to verify the claim):\n"
            f"{search_context}"
        )

    payload = {
        "model": cfg.model_id,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.1,
        "max_tokens": 1000,
    }

    headers = {
        cfg.auth_header: f"{cfg.auth_prefix}{api_key}",
        "Content-Type": "application/json",
    }

    t0 = time.time()
    try:
        resp = requests.post(cfg.base_url, headers=headers, json=payload, timeout=cfg.timeout)
    except Exception as e:
        return _error_result(provider_id, str(e)[:200])

    latency_ms = int((time.time() - t0) * 1000)

    if resp.status_code != 200:
        try:
            err = resp.json().get("error", {}).get("message", resp.text[:200])
        except Exception:
            err = resp.text[:200]
        return _error_result(provider_id, f"HTTP {resp.status_code}: {err}")

    data = resp.json()
    choices = data.get("choices", [])
    if not choices:
        return _error_result(provider_id, "No response choices")

    text = choices[0].get("message", {}).get("content", "")
    parsed = _parse_verdict_json(text)
    if not parsed:
        return _error_result(provider_id, f"JSON parse error: {text[:150]}")

    # Sources come from Brave Search context (already shared), not from the response
    brave_sources = []
    if search_context:
        brave_key = get_brave_api_key(settings)
        # Sources were already collected during the shared Brave call — they'll be
        # merged at the consensus level. Just return empty here.
    return _build_provider_result(provider_id, parsed, brave_sources, latency_ms)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python tests/test_brave_search.py`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add plugins/fact_checker/providers.py tests/test_brave_search.py
git commit -m "[feat] add Brave Search sharing and OpenAI-compatible provider caller"
```

---

### Task 3: Native Search and Claude Provider Callers

**Files:**
- Modify: `plugins/fact_checker/providers.py`

Implement callers for providers with native web search (Gemini, Cohere, Perplexity, OpenAI) and Claude (Anthropic API).

- [ ] **Step 1: Write test for provider caller dispatch**

```python
# tests/test_provider_dispatch.py
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from plugins.fact_checker.providers import (
    PROVIDER_REGISTRY, call_provider, _error_result
)

# All api_styles have a registered caller
api_styles = set(cfg.api_style for cfg in PROVIDER_REGISTRY.values())
expected = {"openai", "gemini", "anthropic", "cohere", "perplexity", "openai_native"}
assert api_styles == expected, f"Missing callers for: {expected - api_styles}"

# call_provider returns error for missing key
settings = {"providers": {"cerebras": {"enabled": True, "api_key": ""}}}
result = call_provider("cerebras", "test claim", "", settings)
assert result.error is not None
assert "API key" in result.error

# call_provider returns error for unknown provider
result = call_provider("nonexistent", "test", "", {})
assert result.error is not None

print("Provider dispatch tests passed!")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python tests/test_provider_dispatch.py`
Expected: FAIL — `ImportError: cannot import name 'call_provider'`

- [ ] **Step 3: Add native search callers and call_provider dispatch**

Append to `providers.py`:

```python
# ── Gemini caller (Flash Lite + Pro) ──

def _extract_gemini_sources(response_data: dict) -> list[dict]:
    sources = []
    seen_urls = set()
    candidates = response_data.get("candidates", [])
    if not candidates:
        return sources
    metadata = candidates[0].get("groundingMetadata", {})
    for chunk in metadata.get("groundingChunks", []):
        web = chunk.get("web", {})
        url = web.get("uri", "")
        if url and url not in seen_urls:
            seen_urls.add(url)
            sources.append({"url": url, "title": web.get("title", "")})
    return sources


def call_gemini(provider_id: str, claim: str, search_context: str,
                settings: dict) -> ProviderResult:
    cfg = PROVIDER_REGISTRY.get(provider_id)
    if not cfg:
        return _error_result(provider_id, f"Unknown provider: {provider_id}")

    api_key = get_provider_api_key(provider_id, settings)
    if not api_key:
        return _error_result(provider_id, f"{cfg.display_name} API key not set")

    prompt = f'{SYSTEM_PROMPT}\n\nAnalyze this statement: "{claim}"'

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "tools": [{"google_search": {}}],
        "generationConfig": {"temperature": 0.1, "maxOutputTokens": 1000},
    }
    headers = {"x-goog-api-key": api_key, "Content-Type": "application/json"}

    t0 = time.time()
    try:
        resp = requests.post(cfg.base_url, headers=headers, json=payload, timeout=cfg.timeout)
    except Exception as e:
        return _error_result(provider_id, str(e)[:200])

    # Grounding rate limit fallback: retry without tools
    if resp.status_code == 429:
        payload.pop("tools", None)
        try:
            resp = requests.post(cfg.base_url, headers=headers, json=payload, timeout=cfg.timeout)
        except Exception as e:
            return _error_result(provider_id, str(e)[:200])

    latency_ms = int((time.time() - t0) * 1000)

    if resp.status_code != 200:
        try:
            err = resp.json().get("error", {}).get("message", resp.text[:200])
        except Exception:
            err = resp.text[:200]
        return _error_result(provider_id, f"HTTP {resp.status_code}: {err}")

    data = resp.json()
    raw_sources = _extract_gemini_sources(data)

    candidates = data.get("candidates", [])
    if not candidates:
        return _error_result(provider_id, "No response from Gemini")
    parts = candidates[0].get("content", {}).get("parts", [])
    if not parts:
        return _error_result(provider_id, "Empty Gemini response")

    text = parts[0].get("text", "")
    parsed = _parse_verdict_json(text)
    if not parsed:
        return _error_result(provider_id, f"JSON parse error: {text[:150]}")

    return _build_provider_result(provider_id, parsed, raw_sources, latency_ms)


# ── Cohere caller ──

def call_cohere(provider_id: str, claim: str, search_context: str,
                settings: dict) -> ProviderResult:
    cfg = PROVIDER_REGISTRY.get(provider_id)
    if not cfg:
        return _error_result(provider_id, f"Unknown provider: {provider_id}")

    api_key = get_provider_api_key(provider_id, settings)
    if not api_key:
        return _error_result(provider_id, f"{cfg.display_name} API key not set")

    user_prompt = f'{SYSTEM_PROMPT}\n\nAnalyze this statement: "{claim}"'

    payload = {
        "model": cfg.model_id,
        "messages": [{"role": "user", "content": user_prompt}],
        "connectors": [{"id": "web-search"}],
        "temperature": 0.1,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    t0 = time.time()
    try:
        resp = requests.post(cfg.base_url, headers=headers, json=payload, timeout=cfg.timeout)
    except Exception as e:
        return _error_result(provider_id, str(e)[:200])

    latency_ms = int((time.time() - t0) * 1000)

    if resp.status_code != 200:
        try:
            err = resp.json().get("error", {}).get("message", resp.text[:200])
        except Exception:
            err = resp.text[:200]
        return _error_result(provider_id, f"HTTP {resp.status_code}: {err}")

    data = resp.json()
    # Cohere returns text in message.content[0].text
    text = ""
    msg = data.get("message", {})
    for block in msg.get("content", []):
        if block.get("type") == "text":
            text = block.get("text", "")
            break

    # Extract sources from citations
    sources = []
    seen = set()
    for doc in data.get("message", {}).get("citations", {}).get("documents", []):
        url = doc.get("url", "")
        if url and url not in seen:
            seen.add(url)
            sources.append({"url": url, "title": doc.get("title", "")})

    parsed = _parse_verdict_json(text)
    if not parsed:
        return _error_result(provider_id, f"JSON parse error: {text[:150]}")

    return _build_provider_result(provider_id, parsed, sources, latency_ms)


# ── Perplexity caller ──

def call_perplexity(provider_id: str, claim: str, search_context: str,
                    settings: dict) -> ProviderResult:
    cfg = PROVIDER_REGISTRY.get(provider_id)
    if not cfg:
        return _error_result(provider_id, f"Unknown provider: {provider_id}")

    api_key = get_provider_api_key(provider_id, settings)
    if not api_key:
        return _error_result(provider_id, f"{cfg.display_name} API key not set")

    payload = {
        "model": cfg.model_id,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f'Analyze this statement: "{claim}"'},
        ],
        "temperature": 0.1,
        "max_tokens": 1000,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    t0 = time.time()
    try:
        resp = requests.post(cfg.base_url, headers=headers, json=payload, timeout=cfg.timeout)
    except Exception as e:
        return _error_result(provider_id, str(e)[:200])

    latency_ms = int((time.time() - t0) * 1000)

    if resp.status_code != 200:
        try:
            err = resp.json().get("error", {}).get("message", resp.text[:200])
        except Exception:
            err = resp.text[:200]
        return _error_result(provider_id, f"HTTP {resp.status_code}: {err}")

    data = resp.json()
    choices = data.get("choices", [])
    if not choices:
        return _error_result(provider_id, "No response")

    text = choices[0].get("message", {}).get("content", "")
    # Perplexity citations in response metadata
    sources = []
    seen = set()
    for cite in data.get("citations", []):
        if isinstance(cite, str) and cite not in seen:
            seen.add(cite)
            sources.append({"url": cite, "title": ""})
        elif isinstance(cite, dict):
            url = cite.get("url", "")
            if url and url not in seen:
                seen.add(url)
                sources.append({"url": url, "title": cite.get("title", "")})

    parsed = _parse_verdict_json(text)
    if not parsed:
        return _error_result(provider_id, f"JSON parse error: {text[:150]}")

    return _build_provider_result(provider_id, parsed, sources, latency_ms)


# ── OpenAI native search caller (GPT-5.x series) ──

def call_openai_native(provider_id: str, claim: str, search_context: str,
                       settings: dict) -> ProviderResult:
    cfg = PROVIDER_REGISTRY.get(provider_id)
    if not cfg:
        return _error_result(provider_id, f"Unknown provider: {provider_id}")

    api_key = get_provider_api_key(provider_id, settings)
    if not api_key:
        return _error_result(provider_id, f"{cfg.display_name} API key not set")

    payload = {
        "model": cfg.model_id,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f'Analyze this statement: "{claim}"'},
        ],
        "tools": [{"type": "web_search"}],
        "temperature": 0.1,
        "max_tokens": 1000,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    t0 = time.time()
    try:
        resp = requests.post(cfg.base_url, headers=headers, json=payload, timeout=cfg.timeout)
    except Exception as e:
        return _error_result(provider_id, str(e)[:200])

    latency_ms = int((time.time() - t0) * 1000)

    if resp.status_code != 200:
        try:
            err = resp.json().get("error", {}).get("message", resp.text[:200])
        except Exception:
            err = resp.text[:200]
        return _error_result(provider_id, f"HTTP {resp.status_code}: {err}")

    data = resp.json()
    choices = data.get("choices", [])
    if not choices:
        return _error_result(provider_id, "No response")

    text = choices[0].get("message", {}).get("content", "")
    parsed = _parse_verdict_json(text)
    if not parsed:
        return _error_result(provider_id, f"JSON parse error: {text[:150]}")

    return _build_provider_result(provider_id, parsed, [], latency_ms)


# ── Claude (Anthropic) caller ──

_anthropic_client = None
_anthropic_client_lock = __import__("threading").Lock()
_anthropic_client_key = None


def _get_anthropic_client(api_key: str):
    global _anthropic_client, _anthropic_client_key
    if _anthropic_client and _anthropic_client_key == api_key:
        return _anthropic_client
    with _anthropic_client_lock:
        if _anthropic_client and _anthropic_client_key == api_key:
            return _anthropic_client
        try:
            import anthropic
        except ImportError:
            raise RuntimeError("anthropic package not installed. Run: pip install anthropic")
        _anthropic_client = anthropic.Anthropic(api_key=api_key)
        _anthropic_client_key = api_key
        return _anthropic_client


def _extract_claude_sources(message) -> list[dict]:
    sources = []
    seen = set()
    for block in message.content:
        if getattr(block, "type", None) == "web_search_tool_result":
            for result in getattr(block, "content", []):
                if getattr(result, "type", None) == "web_search_result":
                    url = getattr(result, "url", "")
                    if url and url not in seen:
                        seen.add(url)
                        sources.append({
                            "url": url,
                            "title": getattr(result, "title", ""),
                            "page_age": getattr(result, "page_age", None),
                        })
    for block in message.content:
        if getattr(block, "type", None) == "text":
            for citation in getattr(block, "citations", []) or []:
                url = getattr(citation, "url", "")
                if url and url not in seen:
                    seen.add(url)
                    sources.append({"url": url, "title": getattr(citation, "title", "")})
    return sources


def call_claude(provider_id: str, claim: str, search_context: str,
                settings: dict) -> ProviderResult:
    cfg = PROVIDER_REGISTRY.get(provider_id)
    if not cfg:
        return _error_result(provider_id, f"Unknown provider: {provider_id}")

    api_key = get_provider_api_key(provider_id, settings)
    if not api_key:
        return _error_result(provider_id, f"{cfg.display_name} API key not set")

    user_prompt = f'Analyze this statement: "{claim}"'
    if search_context:
        user_prompt += (
            f"\n\nWeb search results for context (use these to verify the claim):\n"
            f"{search_context}"
        )

    t0 = time.time()
    try:
        client = _get_anthropic_client(api_key)
        message = client.messages.create(
            model=cfg.model_id,
            max_tokens=1000,
            system=SYSTEM_PROMPT,
            tools=[{"type": "web_search_20250305", "name": "web_search"}],
            messages=[{"role": "user", "content": user_prompt}],
        )
    except Exception as e:
        return _error_result(provider_id, str(e)[:200])

    latency_ms = int((time.time() - t0) * 1000)

    raw_sources = _extract_claude_sources(message)
    text_block = next((b for b in message.content if b.type == "text"), None)
    if not text_block:
        return _error_result(provider_id, "No text response from Claude")

    parsed = _parse_verdict_json(text_block.text)
    if not parsed:
        return _error_result(provider_id, f"JSON parse error: {text_block.text[:150]}")

    return _build_provider_result(provider_id, parsed, raw_sources, latency_ms)


# ── Dispatch ──

_CALLERS = {
    "openai": call_openai_compatible,
    "gemini": call_gemini,
    "anthropic": call_claude,
    "cohere": call_cohere,
    "perplexity": call_perplexity,
    "openai_native": call_openai_native,
}


def call_provider(provider_id: str, claim: str, search_context: str,
                  settings: dict) -> ProviderResult:
    cfg = PROVIDER_REGISTRY.get(provider_id)
    if not cfg:
        return _error_result(provider_id, f"Unknown provider: {provider_id}")

    caller = _CALLERS.get(cfg.api_style)
    if not caller:
        return _error_result(provider_id, f"No caller for api_style: {cfg.api_style}")

    try:
        return caller(provider_id, claim, search_context, settings)
    except Exception as e:
        log.error(f"Provider {provider_id} error: {e}")
        return _error_result(provider_id, str(e)[:200])
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python tests/test_provider_dispatch.py`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add plugins/fact_checker/providers.py tests/test_provider_dispatch.py
git commit -m "[feat] add all provider callers: Gemini, Cohere, Perplexity, OpenAI, Claude + dispatch"
```

---

### Task 4: Consensus Engine

**Files:**
- Create: `plugins/fact_checker/consensus.py`

Weighted consensus with progressive delivery. Handles 1-provider direct, 2-provider initial/final, and N-provider threshold logic.

- [ ] **Step 1: Write test for consensus calculation**

```python
# tests/test_consensus.py
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from plugins.fact_checker.providers import ProviderResult
from plugins.fact_checker.consensus import (
    calculate_consensus, get_threshold, ConsensusResult,
)

# Test threshold calculation
assert get_threshold(1) == 1
assert get_threshold(2) == 1
assert get_threshold(3) == 2
assert get_threshold(4) == 2
assert get_threshold(6) == 2
assert get_threshold(9) == 3
assert get_threshold(12) == 4
assert get_threshold(16) == 6

# Test single provider (direct result)
r1 = ProviderResult(provider_id="cerebras", verdict="TRUE", accuracy_score=90.0,
                    assessment="Test", claim="claim", sources=[], latency_ms=100)
c = calculate_consensus([r1], {"cerebras": 0.78}, total_enabled=1)
assert c.stage == "direct"
assert c.verdict == "TRUE"
assert c.accuracy_score == 90.0

# Test two providers agreeing
r2 = ProviderResult(provider_id="gemini_flash_lite", verdict="TRUE", accuracy_score=85.0,
                    assessment="Test2", claim="claim", sources=[], latency_ms=200)
weights = {"cerebras": 0.78, "gemini_flash_lite": 0.75}

c_initial = calculate_consensus([r1], weights, total_enabled=2)
assert c_initial.stage == "initial"

c_final = calculate_consensus([r1, r2], weights, total_enabled=2)
assert c_final.stage == "final"
assert c_final.verdict == "TRUE"

# Test disagreement
r3 = ProviderResult(provider_id="mistral", verdict="FALSE", accuracy_score=20.0,
                    assessment="Disagree", claim="claim", sources=[], latency_ms=300)
weights3 = {"cerebras": 0.78, "gemini_flash_lite": 0.75, "mistral": 0.80}
c3 = calculate_consensus([r1, r2, r3], weights3, total_enabled=3)
assert c3.stage == "final"
# TRUE should win (0.78 + 0.75 = 1.53 vs 0.80)
assert c3.verdict == "TRUE"

# Test weighted score
expected_score = (90.0 * 0.78 + 85.0 * 0.75 + 20.0 * 0.80) / (0.78 + 0.75 + 0.80)
assert abs(c3.accuracy_score - round(expected_score, 1)) < 0.2

print("All consensus tests passed!")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python tests/test_consensus.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'plugins.fact_checker.consensus'`

- [ ] **Step 3: Create consensus.py**

```python
"""
LinguaTaxi — Fact Checker Consensus Engine

Calculates weighted consensus from multiple provider results.
Supports progressive delivery: initial (threshold met) → final (all responded).
"""

import math
from dataclasses import dataclass, field

from plugins.fact_checker.providers import ProviderResult


@dataclass
class ConsensusResult:
    stage: str                      # "initial", "final", "direct"
    verdict: str | None
    accuracy_score: float | None
    assessment: str
    providers_reporting: int
    providers_total: int
    changed_from_initial: bool = False
    change_reason: str | None = None
    provider_results: list[ProviderResult] = field(default_factory=list)
    all_sources: list[dict] = field(default_factory=list)
    flagged_sources: list[dict] = field(default_factory=list)


_VERDICT_RANK = {
    "TRUE": 5, "MOSTLY TRUE": 4, "MIXED": 3,
    "MOSTLY FALSE": 2, "FALSE": 1, "UNVERIFIABLE": 0,
}


def get_threshold(enabled_count: int) -> int:
    if enabled_count <= 2:
        return 1
    return max(2, math.ceil(enabled_count / 3))


def calculate_consensus(results: list[ProviderResult], weights: dict[str, float],
                        total_enabled: int) -> ConsensusResult:
    successful = [r for r in results if not r.error]
    if not successful:
        errors = "; ".join(f"{r.provider_id}: {r.error}" for r in results if r.error)
        return ConsensusResult(
            stage="final",
            verdict=None,
            accuracy_score=None,
            assessment=f"All providers failed: {errors}",
            providers_reporting=0,
            providers_total=total_enabled,
            provider_results=list(results),
        )

    # Determine stage
    threshold = get_threshold(total_enabled)
    if total_enabled == 1:
        stage = "direct"
    elif len(successful) < total_enabled:
        stage = "initial"
    else:
        stage = "final"

    # Weighted score
    total_weight = 0.0
    weighted_score = 0.0
    for r in successful:
        w = weights.get(r.provider_id, 0.5)
        if r.accuracy_score is not None and w > 0:
            weighted_score += r.accuracy_score * w
            total_weight += w
    avg_score = round(weighted_score / total_weight, 1) if total_weight > 0 else None

    # Weighted verdict
    verdict_weights: dict[str, float] = {}
    for r in successful:
        w = weights.get(r.provider_id, 0.5)
        if r.verdict and w > 0:
            verdict_weights[r.verdict] = verdict_weights.get(r.verdict, 0) + w
    consensus_verdict = max(verdict_weights, key=verdict_weights.get) if verdict_weights else None

    # Pick best assessment from highest-weighted provider
    best = max(successful, key=lambda r: weights.get(r.provider_id, 0.5))
    assessment = best.assessment or ""

    # If split verdict, build detailed assessment
    unique_verdicts = set(r.verdict for r in successful if r.verdict and weights.get(r.provider_id, 0.5) > 0)
    if len(unique_verdicts) > 1:
        ranks = [_VERDICT_RANK.get(v, -1) for v in unique_verdicts if _VERDICT_RANK.get(v, -1) >= 0]
        if ranks and (max(ranks) - min(ranks)) > 1:
            parts = []
            for r in successful:
                w = weights.get(r.provider_id, 0.5)
                if w > 0:
                    s_str = f"{r.accuracy_score}%" if r.accuracy_score is not None else "N/A"
                    parts.append(f"{r.provider_id}: {r.verdict} ({s_str})")
            assessment = f"SPLIT VERDICT — {'; '.join(parts)}. {assessment}"

    return ConsensusResult(
        stage=stage,
        verdict=consensus_verdict,
        accuracy_score=avg_score,
        assessment=assessment,
        providers_reporting=len(successful),
        providers_total=total_enabled,
        provider_results=list(results),
    )


def check_verdict_changed(initial: ConsensusResult, final: ConsensusResult) -> tuple[bool, str | None]:
    if initial.verdict != final.verdict:
        reason = (
            f"Verdict changed from {initial.verdict} to {final.verdict} "
            f"after {final.providers_reporting - initial.providers_reporting} additional providers reported"
        )
        return True, reason
    if (initial.accuracy_score is not None and final.accuracy_score is not None
            and abs(initial.accuracy_score - final.accuracy_score) > 10):
        reason = (
            f"Score shifted from {initial.accuracy_score} to {final.accuracy_score} "
            f"after {final.providers_reporting - initial.providers_reporting} additional providers reported"
        )
        return True, reason
    return False, None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python tests/test_consensus.py`
Expected: PASS — "All consensus tests passed!"

- [ ] **Step 5: Commit**

```bash
git add plugins/fact_checker/consensus.py tests/test_consensus.py
git commit -m "[feat] add weighted consensus engine with progressive delivery thresholds"
```

---

### Task 5: Claim Classification Stage (Stage 2)

**Files:**
- Modify: `plugins/fact_checker/providers.py`

Add Stage 2: free LLM claim classification that extracts clean claims and filters false positives before expensive provider calls.

- [ ] **Step 1: Write test for claim classification**

```python
# tests/test_claim_classification.py
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from plugins.fact_checker.providers import (
    _parse_classification_response,
    CLASSIFICATION_FALLBACK_ORDER,
)

# Test valid claim response parsing
response = '{"is_claim": true, "extracted_claim": "The Earth is 4.5 billion years old", "search_query": "Earth age billion years"}'
result = _parse_classification_response(response)
assert result is not None
assert result["is_claim"] is True
assert "Earth" in result["extracted_claim"]
assert result["search_query"] is not None

# Test non-claim response
response2 = '{"is_claim": false, "extracted_claim": null, "search_query": null}'
result2 = _parse_classification_response(response2)
assert result2 is not None
assert result2["is_claim"] is False

# Test fallback order
assert CLASSIFICATION_FALLBACK_ORDER[0] == "cerebras"
assert "github_models" in CLASSIFICATION_FALLBACK_ORDER

print("Claim classification tests passed!")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python tests/test_claim_classification.py`
Expected: FAIL — `ImportError: cannot import name '_parse_classification_response'`

- [ ] **Step 3: Add claim classification to providers.py**

Add after the existing `call_provider` function:

```python
# ── Stage 2: Claim classification ──

_CLASSIFICATION_PROMPT = """Analyze this transcribed statement. Respond in JSON only.
If it contains a verifiable factual claim, extract it clearly.
If it does not contain a verifiable claim, mark it as not_a_claim.

Statement: "{statement}"

Response format:
{{"is_claim": true/false, "extracted_claim": "clean claim text or null", "search_query": "optimized search query or null"}}"""

CLASSIFICATION_FALLBACK_ORDER = [
    "cerebras", "github_models", "mistral", "openrouter", "gemini_flash_lite",
]


def _parse_classification_response(raw: str) -> dict | None:
    parsed = _parse_verdict_json(raw)
    if not parsed:
        return None
    if "is_claim" not in parsed:
        return None
    return {
        "is_claim": bool(parsed.get("is_claim")),
        "extracted_claim": parsed.get("extracted_claim"),
        "search_query": parsed.get("search_query"),
    }


def classify_claim(statement: str, settings: dict,
                   preferred_provider: str | None = None) -> dict | None:
    """Stage 2: Use a free LLM to classify and extract the claim.
    Returns {"is_claim": bool, "extracted_claim": str|None, "search_query": str|None}
    or None if all providers fail."""
    order = list(CLASSIFICATION_FALLBACK_ORDER)
    if preferred_provider and preferred_provider in order:
        order.remove(preferred_provider)
        order.insert(0, preferred_provider)

    prompt = _CLASSIFICATION_PROMPT.format(statement=statement)

    for pid in order:
        api_key = get_provider_api_key(pid, settings)
        if not api_key:
            continue

        cfg = PROVIDER_REGISTRY.get(pid)
        if not cfg:
            continue

        # Use OpenAI-compatible call for classification (no search needed)
        payload = {
            "model": cfg.model_id,
            "messages": [
                {"role": "system", "content": "You classify statements as factual claims or non-claims. Respond only in JSON."},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.1,
            "max_tokens": 300,
        }

        # Gemini uses a different format
        if cfg.api_style == "gemini":
            payload = {
                "contents": [{"parts": [{"text": f"You classify statements as factual claims or non-claims. Respond only in JSON.\n\n{prompt}"}]}],
                "generationConfig": {"temperature": 0.1, "maxOutputTokens": 300},
            }

        headers = {
            cfg.auth_header: f"{cfg.auth_prefix}{api_key}",
            "Content-Type": "application/json",
        }

        try:
            resp = requests.post(cfg.base_url, headers=headers, json=payload, timeout=10)
            if resp.status_code != 200:
                log.warning(f"Classification via {pid} failed: HTTP {resp.status_code}")
                continue

            data = resp.json()
            # Extract text based on api_style
            if cfg.api_style == "gemini":
                candidates = data.get("candidates", [])
                if not candidates:
                    continue
                text = candidates[0].get("content", {}).get("parts", [{}])[0].get("text", "")
            else:
                choices = data.get("choices", [])
                if not choices:
                    continue
                text = choices[0].get("message", {}).get("content", "")

            result = _parse_classification_response(text)
            if result is not None:
                return result

        except Exception as e:
            log.warning(f"Classification via {pid} error: {e}")
            continue

    return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python tests/test_claim_classification.py`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add plugins/fact_checker/providers.py tests/test_claim_classification.py
git commit -m "[feat] add Stage 2 claim classification with provider fallback chain"
```

---

### Task 6: Pipeline Integration — Rework routes.py

**Files:**
- Modify: `plugins/fact_checker/routes.py`

Replace the old `_run_fact_check()` / `_run_magi_check()` with the new 5-stage pipeline. Update response models and status endpoint. Keep backward compatibility.

- [ ] **Step 1: Update imports and data models in routes.py**

At the top of `routes.py`, replace the existing provider-specific imports and add new ones:

Replace lines 1-21 (the module docstring):
```python
"""
LinguaTaxi — Fact Checker Plugin Routes
POST /api/fact-check        — analyze a statement for accuracy
GET  /api/fact-check/status — health check + provider status

Multi-provider consensus fact checking with progressive delivery.
Supports 8 free + 8 paid LLM providers running in parallel.
See design spec: docs/superpowers/specs/2026-05-10-multi-provider-fact-checker-design.md
"""
```

Add new imports after the existing ones (keep existing imports for mbfc_data, flip_flop, claim_filter):
```python
import importlib.util
from pathlib import Path

# Load providers and consensus from same directory
_providers_spec = importlib.util.spec_from_file_location(
    "providers", str(Path(__file__).parent / "providers.py")
)
_providers_mod = importlib.util.module_from_spec(_providers_spec)
_providers_spec.loader.exec_module(_providers_mod)
providers = _providers_mod

_consensus_spec = importlib.util.spec_from_file_location(
    "consensus", str(Path(__file__).parent / "consensus.py")
)
_consensus_mod = importlib.util.module_from_spec(_consensus_spec)
_consensus_spec.loader.exec_module(_consensus_mod)
consensus = _consensus_mod
```

- [ ] **Step 2: Update FactCheckResponse model**

Replace the existing `FactCheckResponse` class (around line 257) with:

```python
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
    provider: str | None = None             # Deprecated — kept for backward compat
    magi_consensus: str | None = None       # Deprecated — kept for backward compat
    magi_nodes: dict | None = None          # Deprecated — kept for backward compat
    # New consensus fields
    consensus_stage: str | None = None      # "initial", "final", "direct", None
    consensus_providers: int | None = None
    consensus_total: int | None = None
    consensus_changed: bool | None = None
    consensus_reason: str | None = None
    provider_breakdown: list[dict] | None = None
```

- [ ] **Step 3: Replace _run_fact_check with new pipeline**

Remove the old `_run_fact_check()`, `_run_magi_check()`, `_run_gemini_check()`, `_run_claude_check()`, `_run_groq_check()`, `_brave_search()` functions (lines ~364-941) and the old provider-specific constants/helpers (`_GEMINI_ENDPOINT`, `_GROQ_CHAT_URL`, `_GROQ_MODEL`, `PROVIDER_WEIGHTS`, `_VERDICT_RANK`, etc.).

Keep: `_build_user_prompt()`, `_enrich_sources()`, `_split_sources()`, `_parse_verdict_json()`, `_SYSTEM_PROMPT`, `_get_threshold()`, `_flip_flop_enabled()`, all MBFC/flip_flop/claim_filter imports, rate limiter, and `_extract_claude_sources()` (moved to providers.py, so this one can be removed).

Replace with the new pipeline:

```python
def _run_consensus_pipeline(statement: str, recheck: bool = False,
                            previous_verdict: str | None = None,
                            previous_assessment: str | None = None,
                            previous_score: float | None = None,
                            speaker: str | None = None) -> dict:
    """5-stage multi-provider consensus pipeline."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    mbfc_ensure_loaded()
    threshold = _get_threshold()
    settings = _plugin_settings

    enabled = providers.get_enabled_providers(settings)
    if not enabled:
        return {"type": "ambiguous", "error": "No providers enabled. Configure at least one in settings."}

    # ── Stage 2: Claim classification (if not recheck) ──
    extracted_claim = statement
    search_query = statement
    if not recheck and len(enabled) > 1:
        classification_provider = settings.get("classification_provider", "cerebras")
        classification = providers.classify_claim(statement, settings, classification_provider)
        if classification is not None:
            if not classification["is_claim"]:
                return {"type": "opinion", "assessment": "Not a verifiable factual claim."}
            if classification["extracted_claim"]:
                extracted_claim = classification["extracted_claim"]
            if classification["search_query"]:
                search_query = classification["search_query"]

    # ── Stage 3: Shared Brave Search ──
    search_context = ""
    brave_sources = []
    brave_dependent = [p for p in enabled if providers.needs_brave_search(p.provider_id)]
    if brave_dependent:
        brave_key = providers.get_brave_api_key(settings)
        if brave_key:
            brave_results = providers.brave_search(search_query, brave_key, count=5)
            brave_sources = [{"url": r["url"], "title": r["title"]} for r in brave_results if r.get("url")]
            search_context = providers.format_search_snippets(brave_results)

    # ── Stage 4: Query all enabled providers in parallel ──
    weights = {p.provider_id: providers.get_provider_weight(p.provider_id, settings) for p in enabled}
    provider_results = []

    with ThreadPoolExecutor(max_workers=min(len(enabled), 8), thread_name_prefix="consensus") as pool:
        futures = {}
        for p in enabled:
            sc = search_context if providers.needs_brave_search(p.provider_id) else ""
            futures[pool.submit(providers.call_provider, p.provider_id, extracted_claim, sc, settings)] = p

        for future in as_completed(futures, timeout=45):
            try:
                result = future.result(timeout=1)
                provider_results.append(result)
            except Exception as e:
                p = futures[future]
                provider_results.append(providers._error_result(p.provider_id, str(e)[:200]))

    # ── Stage 5: Consensus ──
    total_enabled = len(enabled)
    con = consensus.calculate_consensus(provider_results, weights, total_enabled)

    # Merge all sources (Brave + native from providers)
    all_sources = list(brave_sources)
    for pr in provider_results:
        for s in pr.sources:
            if s.get("url") and s["url"] not in {x["url"] for x in all_sources}:
                all_sources.append(s)

    enriched = _enrich_sources(all_sources, threshold)
    credible, flagged = _split_sources(enriched)

    # Build provider breakdown for frontend
    breakdown = []
    for pr in provider_results:
        cfg = providers.get_provider_config(pr.provider_id)
        breakdown.append({
            "provider_id": pr.provider_id,
            "display_name": cfg.display_name if cfg else pr.provider_id,
            "verdict": pr.verdict,
            "accuracy_score": pr.accuracy_score,
            "assessment": pr.assessment,
            "error": pr.error,
            "latency_ms": pr.latency_ms,
            "weight": weights.get(pr.provider_id, 0.5),
        })

    result = {
        "type": con.verdict and "fact_claim" or "ambiguous",
        "claim": extracted_claim if extracted_claim != statement else None,
        "accuracy_score": con.accuracy_score,
        "verdict": con.verdict,
        "assessment": con.assessment,
        "sources": credible,
        "flagged_sources": flagged,
        "provider": "consensus",
        "consensus_stage": con.stage,
        "consensus_providers": con.providers_reporting,
        "consensus_total": con.providers_total,
        "consensus_changed": con.changed_from_initial,
        "consensus_reason": con.change_reason,
        "provider_breakdown": breakdown,
    }

    # Backward compat: populate magi_nodes for old frontends
    result["magi_consensus"] = "agree" if len(set(pr.verdict for pr in provider_results if pr.verdict)) <= 1 else "disagree"
    result["magi_nodes"] = {pr.provider_id: {
        "label": (providers.get_provider_config(pr.provider_id) or type('', (), {"display_name": pr.provider_id})).display_name,
        "weight": weights.get(pr.provider_id, 0),
        "verdict": pr.verdict,
        "accuracy_score": pr.accuracy_score,
        "assessment": pr.assessment,
        "error": pr.error,
    } for pr in provider_results}

    return result
```

- [ ] **Step 4: Update the /fact-check endpoint**

Replace the API key validation block and dispatcher call in the `fact_check()` endpoint (lines ~1060-1100) with:

```python
@router.post("/fact-check")
async def fact_check(req: FactCheckRequest):
    """Analyze a transcribed statement for accuracy."""
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

    # Stage 1: Local claim detection pre-filter
    local_filter_on = _plugin_settings.get("local_filter", "true").lower() in ("true", "1", "on")
    if local_filter_on and not req.recheck and claim_filter.is_loaded():
        cf_result = claim_filter.classify(req.statement.strip())
        if not cf_result["is_claim"] and cf_result["confidence"] >= 0.995:
            return FactCheckResponse(
                type="opinion",
                assessment="Filtered locally — not a verifiable factual claim.",
                claim=req.statement.strip(),
            )

    # Validate at least one provider is enabled
    enabled = providers.get_enabled_providers(_plugin_settings)
    if not enabled:
        raise HTTPException(
            status_code=503,
            detail="No providers enabled. Configure at least one provider with an API key in settings.",
        )

    try:
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            _fc_pool,
            lambda: _run_consensus_pipeline(
                req.statement.strip(),
                recheck=req.recheck,
                previous_verdict=req.previous_verdict,
                previous_assessment=req.previous_assessment,
                previous_score=req.previous_score,
                speaker=req.speaker,
            ),
        )
        return FactCheckResponse.model_validate(result)
    except Exception as exc:
        return FactCheckResponse(
            type="ambiguous",
            error=str(exc)[:200],
            assessment="Analysis failed — see error field for details.",
        )
```

- [ ] **Step 5: Update the /fact-check/status endpoint**

Replace the existing `fact_check_status()` with:

```python
@router.get("/fact-check/status")
async def fact_check_status():
    """Health check — provider status, enabled count, MBFC data."""
    enabled = providers.get_enabled_providers(_plugin_settings)
    enabled_ids = [p.provider_id for p in enabled]

    provider_statuses = {}
    for pid, cfg in providers.PROVIDER_REGISTRY.items():
        has_key = bool(providers.get_provider_api_key(pid, _plugin_settings))
        pcfg = _plugin_settings.get("providers", {}).get(pid, {})
        provider_statuses[pid] = {
            "display_name": cfg.display_name,
            "enabled": pcfg.get("enabled", False),
            "has_key": has_key,
            "category": cfg.category,
            "speed": cfg.speed,
            "search_method": cfg.search_method,
            "weight": providers.get_provider_weight(pid, _plugin_settings),
        }

    return {
        "status": "ok",
        "provider_count": len(enabled),
        "providers_enabled": enabled_ids,
        "provider_details": provider_statuses,
        "brave_key_set": bool(providers.get_brave_api_key(_plugin_settings)),
        "mbfc_loaded": mbfc_is_loaded(),
        "mbfc_sources": mbfc_source_count(),
        "credibility_threshold": _get_threshold(),
        "claim_filter_available": claim_filter.is_available(),
        "claim_filter_loaded": claim_filter.is_loaded(),
        "claim_filter_error": claim_filter.get_load_error(),
        "classification_provider": _plugin_settings.get("classification_provider", "cerebras"),
    }
```

- [ ] **Step 6: Update config accessors**

Remove the old per-provider key getters (`_get_anthropic_key`, `_get_gemini_key`, `_get_groq_key`, `_get_brave_key`, `_get_provider`, `_get_claude_model`, `CLAUDE_MODELS`, `PROVIDER_WEIGHTS`). Replace with simpler accessors:

```python
def _get_threshold():
    """Get MBFC credibility threshold from plugin settings."""
    try:
        val = int(_plugin_settings.get("credibility_threshold", MBFC_DEFAULT_THRESHOLD))
        return max(0, min(100, val))
    except (ValueError, TypeError):
        return MBFC_DEFAULT_THRESHOLD
```

(Keep `_flip_flop_enabled`, `_check_rate_limit` as they are.)

- [ ] **Step 7: Update dossier fetch to use new providers**

Replace `_fetch_dossier_for()` to use the providers module:

```python
def _fetch_dossier_for(name: str) -> dict | None:
    """Fetch a speaker dossier using the best available provider."""
    enabled = providers.get_enabled_providers(_plugin_settings)
    if not enabled:
        return None

    # Pick the best single provider for dossier (prefer highest weight)
    best = max(enabled, key=lambda p: providers.get_provider_weight(p.provider_id, _plugin_settings))
    prompt = f"{flip_flop.get_dossier_prompt()}\n\nSubject: {name}"

    try:
        result = providers.call_provider(best.provider_id, prompt, "", _plugin_settings)
    except Exception as e:
        log.error(f"[Flip-Flop] Dossier fetch error for '{name}': {e}")
        return None

    if result.error:
        log.warning(f"[Flip-Flop] Dossier for '{name}': {result.error}")
        return None

    # Try to parse dossier-shaped response
    if result.assessment and ("statements" in result.assessment or "positions" in result.assessment):
        parsed = _parse_verdict_json(result.assessment)
        if parsed and ("statements" in parsed or "positions" in parsed):
            return {
                "statements": parsed.get("statements", []),
                "positions": parsed.get("positions", {}),
            }
    return None
```

- [ ] **Step 8: Update ThreadPoolExecutor size**

Change `_fc_pool` from `max_workers=3` to `max_workers=8` to handle parallel provider queries:

```python
_fc_pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="factcheck")
```

- [ ] **Step 9: Test the pipeline manually**

Run: `cd C:/Users/Laptop/Documents/LinguaTaxi && python -c "from plugins.fact_checker import routes; print('routes.py loads OK')"`
Expected: Module loads without import errors.

- [ ] **Step 10: Commit**

```bash
git add plugins/fact_checker/routes.py
git commit -m "[feat] replace single-provider pipeline with 5-stage multi-provider consensus"
```

---

### Task 7: Update Manifest and Settings Schema

**Files:**
- Modify: `plugins/fact_checker/manifest.json`

Replace the old per-provider key settings with the new structured provider configuration.

- [ ] **Step 1: Update manifest.json**

```json
{
  "id": "fact_checker",
  "name": "Fact Checker",
  "version": "2.0.0",
  "description": "Multi-provider consensus fact-checking with progressive delivery, MBFC source credibility, and flip-flop detection.",
  "author": "LinguaTaxi",
  "hooks": ["on_config_change", "on_speaker_enrolled", "on_shutdown"],
  "has_routes": true,
  "has_panel": true,
  "route_prefix": "/api/fact-check",
  "settings_schema": {
    "brave_api_key": {
      "type": "password",
      "label": "Brave Search API Key (required by most providers — free at brave.com/search/api)",
      "default": ""
    },
    "rate_limit": {
      "type": "number",
      "label": "Max checks per minute",
      "default": 10
    },
    "credibility_threshold": {
      "type": "number",
      "label": "MBFC credibility threshold (0-100)",
      "default": 32
    },
    "flip_flop_enabled": {
      "type": "text",
      "label": "Flip-Flop Detection (true/false)",
      "default": "false"
    },
    "mode": {
      "type": "text",
      "label": "Mode: auto or manual",
      "default": "auto"
    },
    "local_filter": {
      "type": "text",
      "label": "Local pre-filter (true/false)",
      "default": "true"
    },
    "classification_provider": {
      "type": "text",
      "label": "Claim classification provider (cerebras, github_models, etc.)",
      "default": "cerebras"
    }
  }
}
```

Note: Individual provider enable/disable and API keys are managed by custom UI in panel.js, not through the generic settings form. The `providers` and `weights` objects in config.json are read/written directly by the panel JS via the settings API.

- [ ] **Step 2: Commit**

```bash
git add plugins/fact_checker/manifest.json
git commit -m "[feat] update manifest to v2.0.0 with multi-provider consensus settings"
```

---

### Task 8: Frontend — Provider Settings UI

**Files:**
- Modify: `plugins/fact_checker/panel.html`
- Modify: `plugins/fact_checker/panel.js`
- Modify: `plugins/fact_checker/panel.css`

Rework the settings area to show provider checkboxes with API key fields, tags (speed/cost/search method), and Brave Search key section. The old provider badge is replaced with a provider count + enabled list.

- [ ] **Step 1: Update panel.html with provider settings sections**

Replace the entire contents of `panel.html`:

```html
<div class="fc-status-row">
  <span class="fc-count" id="fc-count"></span>
  <div class="fc-mode-toggle" id="fc-mode-toggle">
    <button class="fc-mode-btn fc-mode-btn--active" id="fc-mode-auto" onclick="window._fcSetMode('auto')">Auto</button>
    <button class="fc-mode-btn" id="fc-mode-manual" onclick="window._fcSetMode('manual')">Manual</button>
  </div>
  <div class="fc-provider-badge" id="fc-provider-badge"></div>
</div>
<div class="fc-manual-controls" id="fc-manual-controls" style="display:none">
  <button class="fc-manual-btn" id="fc-check-last" onclick="window._fcCheckLast()">Check Last Statement</button>
  <button class="fc-manual-btn" id="fc-check-30s" onclick="window._fcCheck30s()">Check Last 30s</button>
</div>

<!-- Provider settings (collapsible) -->
<div class="fc-settings-toggle" id="fc-settings-toggle" onclick="window._fcToggleSettings()">
  <span class="fc-settings-chevron" id="fc-settings-chevron">&#9654;</span>
  <span>Provider Settings</span>
</div>
<div class="fc-settings-panel" id="fc-settings-panel" style="display:none">
  <!-- Brave Search key -->
  <div class="fc-brave-section" id="fc-brave-section"></div>
  <!-- Provider list -->
  <div class="fc-provider-list" id="fc-provider-list"></div>
  <!-- Advanced settings -->
  <div class="fc-advanced-toggle" onclick="window._fcToggleAdvanced()">
    <span class="fc-advanced-chevron" id="fc-advanced-chevron">&#9654;</span>
    <span>Advanced Settings</span>
  </div>
  <div class="fc-advanced-panel" id="fc-advanced-panel" style="display:none"></div>
</div>

<!-- Modified weights warning -->
<div class="fc-weights-warning" id="fc-weights-warning" style="display:none">
  &#9888; Fact checking model weights have been modified from defaults
</div>

<div class="fc-empty" id="fc-empty">Fact checker is off. Click Enable to analyze statements as they are transcribed.</div>
<div class="fc-results" id="fc-results"></div>
<div class="fc-queue-status" id="fc-queue-status"></div>
```

- [ ] **Step 2: Add provider settings rendering to panel.js**

Add after the existing `detectPage()` function in `panel.js`. This builds the provider list UI dynamically from the status endpoint:

```javascript
// ── Provider registry (mirrors providers.py) ──
const PROVIDER_META = {
  gemini_flash_lite: { name: 'Gemini 3.1 Flash Lite', speed: 'Fast', cost: 'Free', search: 'Google Search', signup: 'aistudio.google.com', category: 'free' },
  cerebras:          { name: 'Cerebras', speed: 'Fast', cost: 'Free', search: 'Brave Search', signup: 'cerebras.ai', category: 'free' },
  mistral:           { name: 'Mistral AI', speed: 'Normal', cost: 'Free', search: 'Brave Search', signup: 'console.mistral.ai', category: 'free' },
  github_models:     { name: 'GitHub Models', speed: 'Normal', cost: 'Free', search: 'Brave Search', signup: 'github.com/marketplace/models', category: 'free' },
  cohere:            { name: 'Cohere', speed: 'Normal', cost: 'Free', search: 'Built-in Search', signup: 'dashboard.cohere.com', category: 'free' },
  openrouter:        { name: 'OpenRouter', speed: 'Normal', cost: 'Free', search: 'Brave Search', signup: 'openrouter.ai', category: 'free' },
  ovhcloud:          { name: 'OVHcloud', speed: 'Slow', cost: 'Free', search: 'Brave Search', signup: 'endpoints.ai.cloud.ovh.net', category: 'free' },
  huggingface:       { name: 'Hugging Face', speed: 'Slow', cost: 'Free', search: 'Brave Search', signup: 'huggingface.co', category: 'free' },
  claude_sonnet:     { name: 'Claude Sonnet 4.6', speed: 'Fast', cost: 'Paid', search: 'Brave Search', signup: 'console.anthropic.com', costInfo: '$3/$15 per 1M tokens', category: 'paid' },
  claude_opus:       { name: 'Claude Opus 4.6', speed: 'Normal', cost: 'Paid', search: 'Brave Search', signup: 'console.anthropic.com', costInfo: '$5/$25 per 1M tokens', category: 'paid' },
  perplexity:        { name: 'Perplexity Sonar Pro', speed: 'Normal', cost: 'Paid', search: 'Built-in Search', signup: 'docs.perplexity.ai', costInfo: '$3/$15 + search', category: 'paid' },
  openai_gpt55:      { name: 'OpenAI GPT-5.5', speed: 'Normal', cost: 'Paid', search: 'Built-in Search', signup: 'platform.openai.com', costInfo: '$5/$30 per 1M tokens', category: 'paid' },
  openai_gpt54_mini: { name: 'OpenAI GPT-5.4-mini', speed: 'Fast', cost: 'Paid', search: 'Built-in Search', signup: 'platform.openai.com', costInfo: '$0.75/$4.50', category: 'paid' },
  openai_gpt54_nano: { name: 'OpenAI GPT-5.4-nano', speed: 'Fast', cost: 'Paid', search: 'Built-in Search', signup: 'platform.openai.com', costInfo: '$0.20/$1.25', category: 'paid' },
  openai_gpt5_nano:  { name: 'OpenAI GPT-5-nano', speed: 'Fast', cost: 'Paid', search: 'Built-in Search', signup: 'platform.openai.com', costInfo: '$0.05/$0.40', category: 'paid' },
  gemini_pro:        { name: 'Google Gemini 3.1 Pro', speed: 'Normal', cost: 'Paid', search: 'Google Search', signup: 'aistudio.google.com', costInfo: '$1.25/$10', category: 'paid' },
};

let providerSettings = {}; // { providers: {}, weights: {}, brave_api_key: '' }

async function loadProviderSettings() {
  try {
    const resp = await fetch('/api/plugins/fact_checker/settings');
    if (!resp.ok) return;
    const data = await resp.json();
    const vals = data.values || {};
    providerSettings = {
      providers: {},
      weights: {},
      brave_api_key: vals.brave_api_key || '',
      classification_provider: vals.classification_provider || 'cerebras',
    };
    // Parse provider settings from flat keys or structured object
    if (typeof vals.providers === 'string') {
      try { providerSettings.providers = JSON.parse(vals.providers); } catch(e) {}
    } else if (typeof vals.providers === 'object') {
      providerSettings.providers = vals.providers || {};
    }
    if (typeof vals.weights === 'string') {
      try { providerSettings.weights = JSON.parse(vals.weights); } catch(e) {}
    } else if (typeof vals.weights === 'object') {
      providerSettings.weights = vals.weights || {};
    }
  } catch(e) {}
  renderProviderList();
  renderBraveSection();
  renderAdvancedSettings();
  checkModifiedWeights();
}

function renderProviderList() {
  const el = document.getElementById('fc-provider-list');
  if (!el) return;
  let html = '<div class="fc-prov-section-title">Free Providers</div>';
  const free = Object.entries(PROVIDER_META).filter(([,m]) => m.category === 'free');
  const paid = Object.entries(PROVIDER_META).filter(([,m]) => m.category === 'paid');

  for (const [pid, meta] of free) {
    html += buildProviderRow(pid, meta);
  }
  html += '<div class="fc-prov-section-title">Paid Providers</div>';
  for (const [pid, meta] of paid) {
    html += buildProviderRow(pid, meta);
  }
  el.innerHTML = html;
}

function buildProviderRow(pid, meta) {
  const pcfg = providerSettings.providers[pid] || {};
  const isEnabled = !!pcfg.enabled;
  const hasKey = !!pcfg.api_key;
  const speedCls = meta.speed === 'Fast' ? 'fc-tag--fast' : meta.speed === 'Slow' ? 'fc-tag--slow' : 'fc-tag--normal';
  const costCls = meta.cost === 'Paid' ? 'fc-tag--paid' : 'fc-tag--free';

  let html = `<div class="fc-prov-row" data-pid="${pid}">`;
  html += `<div class="fc-prov-header">`;
  html += `<label class="fc-prov-check"><input type="checkbox" ${isEnabled ? 'checked' : ''} onchange="window._fcToggleProvider('${pid}', this.checked)"><span>${esc(meta.name)}</span></label>`;
  html += `<span class="fc-tag ${speedCls}">${meta.speed}</span>`;
  html += `<span class="fc-tag ${costCls}">${meta.cost}</span>`;
  html += `<span class="fc-tag">${meta.search}</span>`;
  if (!isEnabled && !hasKey) {
    // nothing
  } else if (isEnabled && !hasKey) {
    html += `<span class="fc-prov-warn">&#9888; no key</span>`;
  } else if (isEnabled && hasKey) {
    html += `<span class="fc-prov-ok">&#10003;</span>`;
  }
  html += `</div>`;

  // API key field (shown when enabled)
  if (isEnabled) {
    const masked = hasKey ? '\u2022'.repeat(12) + (pcfg.api_key || '').slice(-2) : '';
    html += `<div class="fc-prov-keyrow">`;
    html += `<input type="password" class="fc-prov-key" placeholder="API Key" value="${esc(pcfg.api_key || '')}" onchange="window._fcSetProviderKey('${pid}', this.value)">`;
    html += `<a href="https://${esc(meta.signup)}" target="_blank" rel="noopener" class="fc-prov-signup">Get key &rarr; ${esc(meta.signup)}</a>`;
    if (meta.costInfo) {
      html += `<span class="fc-prov-cost-info">${esc(meta.costInfo)}</span>`;
    }
    html += `</div>`;
  }

  html += `</div>`;
  return html;
}

function renderBraveSection() {
  const el = document.getElementById('fc-brave-section');
  if (!el) return;
  const key = providerSettings.brave_api_key || '';
  const hasKey = !!key;
  el.innerHTML = `
    <div class="fc-brave-title">Brave Search <span class="fc-brave-note">(required by most providers)</span></div>
    <div class="fc-brave-keyrow">
      <input type="password" class="fc-prov-key" placeholder="Brave Search API Key" value="${esc(key)}" onchange="window._fcSetBraveKey(this.value)">
      <a href="https://brave.com/search/api" target="_blank" rel="noopener" class="fc-prov-signup">Get free key &rarr; brave.com/search/api</a>
      ${hasKey ? '<span class="fc-prov-ok">&#10003;</span>' : ''}
    </div>
  `;
}

// ── Settings persistence ──

async function saveProviderSettings() {
  try {
    const resp = await fetch('/api/plugins/fact_checker/settings');
    if (!resp.ok) return;
    const data = await resp.json();
    const vals = data.values || {};
    vals.providers = JSON.stringify(providerSettings.providers);
    vals.weights = JSON.stringify(providerSettings.weights);
    vals.brave_api_key = providerSettings.brave_api_key;
    vals.classification_provider = providerSettings.classification_provider;
    const fd = new FormData();
    Object.entries(vals).forEach(([k, v]) => fd.append(k, String(v)));
    fetch('/api/plugins/fact_checker/settings', { method: 'POST', body: fd });
  } catch(e) {}
}

window._fcToggleProvider = function(pid, enabled) {
  if (!providerSettings.providers[pid]) providerSettings.providers[pid] = {};
  providerSettings.providers[pid].enabled = enabled;
  saveProviderSettings();
  renderProviderList();
  fetchProviderStatus();
};

window._fcSetProviderKey = function(pid, key) {
  if (!providerSettings.providers[pid]) providerSettings.providers[pid] = {};
  providerSettings.providers[pid].api_key = key;
  providerSettings.providers[pid].enabled = true;
  saveProviderSettings();
  renderProviderList();
  fetchProviderStatus();
};

window._fcSetBraveKey = function(key) {
  providerSettings.brave_api_key = key;
  saveProviderSettings();
  renderBraveSection();
};

window._fcToggleSettings = function() {
  const panel = document.getElementById('fc-settings-panel');
  const chev = document.getElementById('fc-settings-chevron');
  if (!panel) return;
  const open = panel.style.display !== 'none';
  panel.style.display = open ? 'none' : 'block';
  if (chev) chev.innerHTML = open ? '&#9654;' : '&#9660;';
  if (!open) loadProviderSettings();
};

window._fcToggleAdvanced = function() {
  const panel = document.getElementById('fc-advanced-panel');
  const chev = document.getElementById('fc-advanced-chevron');
  if (!panel) return;
  const open = panel.style.display !== 'none';
  panel.style.display = open ? 'none' : 'block';
  if (chev) chev.innerHTML = open ? '&#9654;' : '&#9660;';
};
```

- [ ] **Step 3: Update fetchProviderStatus for new status endpoint**

Replace the existing `fetchProviderStatus()`:

```javascript
async function fetchProviderStatus() {
  if (!elProviderBadge) return;
  try {
    const resp = await fetch('/api/fact-check/status');
    if (!resp.ok) return;
    const s = await resp.json();
    const count = s.provider_count || 0;
    const names = (s.providers_enabled || []).map(pid => {
      const m = PROVIDER_META[pid];
      return m ? m.name : pid;
    });

    let filterHtml = '';
    if (s.claim_filter_loaded) {
      filterHtml = '<span class="fc-filter-badge fc-filter--on">Filter ON</span>';
    } else if (s.claim_filter_available) {
      filterHtml = '<span class="fc-filter-badge fc-filter--loading">Filter loading\u2026</span>';
    } else {
      filterHtml = '<span class="fc-filter-badge fc-filter--off" onclick="window._fcDownloadFilter(this)">Download Filter</span>';
    }

    if (count === 0) {
      elProviderBadge.innerHTML =
        '<span class="fc-prov fc-prov--nokey">No providers</span>' +
        '<span class="fc-prov-warn">configure in settings</span>' +
        filterHtml;
    } else {
      const label = count === 1 ? names[0] : `${count} providers`;
      elProviderBadge.innerHTML =
        `<span class="fc-prov fc-prov--ok">${esc(label)}</span>` +
        (count > 1 ? `<span class="fc-prov-cost" title="${esc(names.join(', '))}">${s.brave_key_set ? 'Brave\u2713' : ''}</span>` : '') +
        filterHtml;
    }
  } catch(e) {}
}
```

- [ ] **Step 4: Test — start server and verify provider settings UI renders**

Run: `cd C:/Users/Laptop/Documents/LinguaTaxi && python server.py`
Open `http://localhost:3001` in browser. Expand "Provider Settings" in fact checker panel. Verify:
- Provider list shows 8 free + 8 paid with checkboxes
- Checking a provider shows API key field
- Tags (Fast/Normal/Slow, Free/Paid, search method) display correctly
- Brave Search section is visible

- [ ] **Step 5: Commit**

```bash
git add plugins/fact_checker/panel.html plugins/fact_checker/panel.js
git commit -m "[feat] add provider settings UI with checkboxes, API keys, and tags"
```

---

### Task 9: Frontend — Advanced Settings and Consensus Display

**Files:**
- Modify: `plugins/fact_checker/panel.js`
- Modify: `plugins/fact_checker/panel.css`

Add the advanced settings section (weights editor, classification provider dropdown) and update result cards to show initial/final consensus labels.

- [ ] **Step 1: Add renderAdvancedSettings and checkModifiedWeights to panel.js**

Add to `panel.js`:

```javascript
function renderAdvancedSettings() {
  const el = document.getElementById('fc-advanced-panel');
  if (!el) return;

  // Weight editor — only show enabled providers
  const enabledPids = Object.entries(providerSettings.providers)
    .filter(([, cfg]) => cfg.enabled && cfg.api_key)
    .map(([pid]) => pid);

  let html = '<div class="fc-adv-section"><div class="fc-adv-title">Model Weights</div>';
  html += '<div class="fc-adv-note">Only showing enabled providers. Clear a field to reset to default.</div>';

  for (const pid of enabledPids) {
    const meta = PROVIDER_META[pid];
    if (!meta) continue;
    const defaultWeight = getDefaultWeight(pid);
    const currentWeight = providerSettings.weights[pid] != null ? providerSettings.weights[pid] : defaultWeight;
    html += `<div class="fc-weight-row">`;
    html += `<span class="fc-weight-name">${esc(meta.name)}</span>`;
    html += `<input type="number" class="fc-weight-input" min="0.01" max="1.00" step="0.01" value="${currentWeight}" onchange="window._fcSetWeight('${pid}', this.value)" onblur="window._fcWeightBlur('${pid}', this)">`;
    html += `<span class="fc-weight-default">(default: ${defaultWeight})</span>`;
    html += `</div>`;
  }
  html += '<div class="fc-adv-note">Weights are saved between sessions.</div></div>';

  // Classification provider
  html += '<div class="fc-adv-section"><div class="fc-adv-title">Claim Classification</div>';
  html += '<div class="fc-adv-note">Uses a free provider to extract claims before sending to all fact-check providers.</div>';
  html += '<select class="fc-adv-select" onchange="window._fcSetClassificationProvider(this.value)">';
  const classOrder = ['cerebras', 'github_models', 'mistral', 'openrouter', 'gemini_flash_lite'];
  for (const pid of classOrder) {
    const meta = PROVIDER_META[pid];
    if (!meta) continue;
    const sel = providerSettings.classification_provider === pid ? ' selected' : '';
    html += `<option value="${pid}"${sel}>${esc(meta.name)}</option>`;
  }
  html += '</select></div>';

  el.innerHTML = html;
}

const DEFAULT_WEIGHTS = {
  gemini_flash_lite: 0.75, cerebras: 0.78, mistral: 0.80, github_models: 0.85,
  cohere: 0.83, openrouter: 0.78, ovhcloud: 0.85, huggingface: 0.60,
  claude_sonnet: 0.95, claude_opus: 0.88, perplexity: 0.95,
  openai_gpt55: 0.93, openai_gpt54_mini: 0.82, openai_gpt54_nano: 0.72,
  openai_gpt5_nano: 0.68, gemini_pro: 0.90,
};

function getDefaultWeight(pid) {
  return DEFAULT_WEIGHTS[pid] || 0.50;
}

function checkModifiedWeights() {
  const el = document.getElementById('fc-weights-warning');
  if (!el) return;
  let modified = false;
  for (const [pid, w] of Object.entries(providerSettings.weights || {})) {
    const def = getDefaultWeight(pid);
    if (Math.abs(parseFloat(w) - def) > 0.001) {
      modified = true;
      break;
    }
  }
  el.style.display = modified ? 'block' : 'none';
}

window._fcSetWeight = function(pid, val) {
  if (val === '' || val == null) {
    delete providerSettings.weights[pid];
  } else {
    const n = parseFloat(val);
    if (!isNaN(n)) {
      providerSettings.weights[pid] = Math.max(0.01, Math.min(1.0, n));
    }
  }
  saveProviderSettings();
  checkModifiedWeights();
};

window._fcWeightBlur = function(pid, input) {
  if (input.value === '') {
    input.value = getDefaultWeight(pid);
    delete providerSettings.weights[pid];
    saveProviderSettings();
    checkModifiedWeights();
  }
};

window._fcSetClassificationProvider = function(pid) {
  providerSettings.classification_provider = pid;
  saveProviderSettings();
};
```

- [ ] **Step 2: Update buildCardHTML for consensus display**

In the `buildCardHTML` function, replace the provider badge section and add consensus info. After the existing provider badge line, add:

```javascript
// ── Consensus stage label ──
if (r.consensus_stage) {
  const stageCls = r.consensus_stage === 'final' ? 'fc-badge--consensus-final'
                 : r.consensus_stage === 'initial' ? 'fc-badge--consensus-initial'
                 : 'fc-badge--consensus-direct';
  const stageLabel = r.consensus_stage === 'direct' ? 'Fact Check'
                   : r.consensus_stage === 'initial' ? 'Initial Fact Check'
                   : 'Final Fact Check';
  html += `<span class="fc-badge ${stageCls}">${stageLabel}</span>`;
  if (r.consensus_providers != null && r.consensus_total != null && r.consensus_total > 1) {
    html += `<span class="fc-consensus-count">${r.consensus_providers}/${r.consensus_total} providers</span>`;
  }
}
if (r.consensus_changed && r.consensus_reason) {
  html += `<div class="fc-consensus-change">${esc(r.consensus_reason)}</div>`;
}
```

Also update the provider breakdown section to replace the old `magi_nodes` rendering. Replace the existing MAGI consensus section (`if (r.magi_consensus && r.magi_nodes)` block) with:

```javascript
// ── Provider breakdown (new consensus) ──
if (r.provider_breakdown && r.provider_breakdown.length > 1) {
  const hasDisagreement = new Set(r.provider_breakdown.filter(p => p.verdict).map(p => p.verdict)).size > 1;
  const conCls = hasDisagreement ? 'fc-magi--disagree' : 'fc-magi--agree';
  const conLabel = hasDisagreement ? 'SPLIT VERDICT' : 'CONSENSUS';
  html += `<div class="fc-magi ${conCls}">`;
  html += `<div class="fc-magi-header"><span class="fc-magi-label">${conLabel}</span></div>`;
  html += `<div class="fc-magi-nodes">`;
  for (const node of r.provider_breakdown) {
    const dimmed = node.error || node.weight === 0;
    html += `<div class="fc-magi-node${dimmed ? ' fc-magi-node--dim' : ''}">`;
    html += `<span class="fc-magi-prov">${esc(node.display_name || node.provider_id)}</span>`;
    html += `<span class="fc-magi-weight">${Math.round((node.weight || 0) * 100)}%</span>`;
    if (node.error) {
      html += `<span class="fc-magi-verdict fc-magi-verdict--err">error</span>`;
    } else if (node.verdict) {
      html += `<span class="fc-magi-verdict">${esc(node.verdict)}</span>`;
    }
    if (node.accuracy_score != null) {
      html += `<span class="fc-magi-score">${Math.round(node.accuracy_score)}%</span>`;
    }
    if (node.latency_ms) {
      html += `<span class="fc-magi-latency">${(node.latency_ms / 1000).toFixed(1)}s</span>`;
    }
    html += `</div>`;
  }
  html += `</div></div>`;
}
// Backward compat: old magi_nodes format
else if (r.magi_consensus && r.magi_nodes) {
  // ... keep existing magi_nodes rendering as fallback ...
}
```

- [ ] **Step 3: Add CSS for new settings and consensus components**

Append to `panel.css`:

```css
/* ── Provider settings ─────────────────────────────────────────────── */
.fc-settings-toggle, .fc-advanced-toggle {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 8px 12px;
  cursor: pointer;
  color: rgba(255,255,255,0.5);
  font-size: 11px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  border-top: 1px solid rgba(255,255,255,0.06);
}
.fc-settings-toggle:hover, .fc-advanced-toggle:hover {
  color: rgba(255,255,255,0.8);
}
.fc-settings-chevron, .fc-advanced-chevron {
  font-size: 8px;
  transition: transform 0.15s;
}
.fc-settings-panel { padding: 0 12px 12px; }

.fc-prov-section-title {
  font-size: 10px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: rgba(255,255,255,0.35);
  padding: 10px 0 4px;
  border-bottom: 1px solid rgba(255,255,255,0.06);
}
.fc-prov-row {
  padding: 6px 0;
  border-bottom: 1px solid rgba(255,255,255,0.04);
}
.fc-prov-header {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}
.fc-prov-check {
  display: flex;
  align-items: center;
  gap: 6px;
  cursor: pointer;
  color: rgba(255,255,255,0.8);
  font-size: 12px;
  font-weight: 500;
  flex: 1;
  min-width: 160px;
}
.fc-prov-check input[type="checkbox"] {
  accent-color: #4FC3F7;
}
.fc-tag {
  font-size: 9px;
  font-weight: 600;
  padding: 1px 5px;
  border-radius: 3px;
  text-transform: uppercase;
  letter-spacing: 0.03em;
  background: rgba(255,255,255,0.06);
  color: rgba(255,255,255,0.4);
}
.fc-tag--fast { background: rgba(76,175,80,0.15); color: #81C784; }
.fc-tag--normal { background: rgba(255,193,7,0.12); color: #FFD54F; }
.fc-tag--slow { background: rgba(244,67,54,0.12); color: #EF9A9A; }
.fc-tag--free { background: rgba(76,175,80,0.12); color: #81C784; }
.fc-tag--paid { background: rgba(156,39,176,0.12); color: #CE93D8; }

.fc-prov-warn {
  font-size: 10px;
  color: #FFB74D;
  margin-left: auto;
}
.fc-prov-ok {
  font-size: 12px;
  color: #81C784;
  margin-left: auto;
}

.fc-prov-keyrow {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 4px 0 2px 24px;
  flex-wrap: wrap;
}
.fc-prov-key {
  flex: 1;
  min-width: 180px;
  max-width: 300px;
  padding: 4px 8px;
  background: rgba(0,0,0,0.3);
  border: 1px solid rgba(255,255,255,0.1);
  border-radius: 4px;
  color: rgba(255,255,255,0.8);
  font-size: 11px;
  font-family: monospace;
}
.fc-prov-key:focus {
  border-color: rgba(79,195,247,0.5);
  outline: none;
}
.fc-prov-signup {
  font-size: 10px;
  color: rgba(79,195,247,0.7);
  text-decoration: none;
}
.fc-prov-signup:hover { color: #4FC3F7; text-decoration: underline; }
.fc-prov-cost-info {
  font-size: 9px;
  color: rgba(255,255,255,0.3);
}

/* Brave section */
.fc-brave-section {
  padding: 8px 0;
  border-bottom: 1px solid rgba(255,255,255,0.08);
}
.fc-brave-title {
  font-size: 12px;
  font-weight: 600;
  color: rgba(255,255,255,0.7);
  margin-bottom: 4px;
}
.fc-brave-note { font-weight: 400; color: rgba(255,255,255,0.35); font-size: 10px; }
.fc-brave-keyrow {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

/* Advanced settings */
.fc-advanced-panel { padding: 0 0 8px; }
.fc-adv-section {
  padding: 8px 0;
  border-bottom: 1px solid rgba(255,255,255,0.04);
}
.fc-adv-title {
  font-size: 11px;
  font-weight: 600;
  color: rgba(255,255,255,0.6);
  margin-bottom: 4px;
}
.fc-adv-note {
  font-size: 10px;
  color: rgba(255,255,255,0.3);
  margin-bottom: 6px;
}
.fc-weight-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 2px 0;
}
.fc-weight-name {
  flex: 1;
  font-size: 11px;
  color: rgba(255,255,255,0.7);
}
.fc-weight-input {
  width: 55px;
  padding: 3px 6px;
  background: rgba(0,0,0,0.3);
  border: 1px solid rgba(255,255,255,0.1);
  border-radius: 3px;
  color: rgba(255,255,255,0.8);
  font-size: 11px;
  font-family: monospace;
  text-align: center;
}
.fc-weight-input:focus {
  border-color: rgba(79,195,247,0.5);
  outline: none;
}
.fc-weight-default {
  font-size: 9px;
  color: rgba(255,255,255,0.25);
}
.fc-adv-select {
  padding: 4px 8px;
  background: rgba(0,0,0,0.3);
  border: 1px solid rgba(255,255,255,0.1);
  border-radius: 4px;
  color: rgba(255,255,255,0.8);
  font-size: 11px;
}

/* Weights warning */
.fc-weights-warning {
  padding: 6px 12px;
  background: rgba(255,152,0,0.1);
  border-left: 3px solid #FF9800;
  color: #FFB74D;
  font-size: 11px;
}

/* ── Consensus display ──────────────────────────────────────────────── */
.fc-badge--consensus-initial {
  background: rgba(255,193,7,0.15);
  color: #FFD54F;
}
.fc-badge--consensus-final {
  background: rgba(76,175,80,0.15);
  color: #81C784;
}
.fc-badge--consensus-direct {
  background: rgba(79,195,247,0.12);
  color: #4FC3F7;
}
.fc-consensus-count {
  font-size: 9px;
  color: rgba(255,255,255,0.35);
  margin-left: 4px;
}
.fc-consensus-change {
  font-size: 10px;
  color: #FFD54F;
  padding: 4px 0 2px;
  font-style: italic;
}
.fc-magi-latency {
  font-size: 9px;
  color: rgba(255,255,255,0.25);
  margin-left: auto;
}
```

- [ ] **Step 4: Test — start server and verify full UI**

Run: `cd C:/Users/Laptop/Documents/LinguaTaxi && python server.py`
Open `http://localhost:3001`. Verify:
- Provider Settings toggle expands/collapses
- Brave Search key section visible
- Provider list with checkboxes and tags
- Advanced Settings has weight editor and classification dropdown
- Enabling a provider shows API key field
- Weight warning banner appears when weights modified

- [ ] **Step 5: Commit**

```bash
git add plugins/fact_checker/panel.js plugins/fact_checker/panel.css plugins/fact_checker/panel.html
git commit -m "[feat] add advanced settings (weights, classification) and consensus display"
```

---

### Task 10: End-to-End Integration Test

**Files:**
- No new files — manual integration testing

Verify the full pipeline works end-to-end with at least one provider configured.

- [ ] **Step 1: Start server and configure Gemini**

Run: `cd C:/Users/Laptop/Documents/LinguaTaxi && python server.py`
Open `http://localhost:3001`. In Fact Checker settings:
1. Expand "Provider Settings"
2. Check "Gemini 3.1 Flash Lite"
3. Enter API key
4. Verify green checkmark appears

- [ ] **Step 2: Test fact check via curl**

```bash
curl -X POST http://localhost:3000/api/fact-check \
  -H "Content-Type: application/json" \
  -d '{"statement": "The Earth is approximately 4.5 billion years old"}'
```

Expected: JSON response with `consensus_stage: "direct"` (single provider), verdict, score, sources.

- [ ] **Step 3: Test status endpoint**

```bash
curl http://localhost:3000/api/fact-check/status | python -m json.tool
```

Expected: `provider_count: 1`, `providers_enabled: ["gemini_flash_lite"]`, `provider_details` with all 16 providers listed.

- [ ] **Step 4: Test with multiple providers (if keys available)**

Enable a second provider. Send a fact check. Verify:
- Response has `consensus_stage: "final"`
- `consensus_providers: 2`, `consensus_total: 2`
- `provider_breakdown` has 2 entries with verdicts, scores, latencies

- [ ] **Step 5: Test non-claim filtering**

```bash
curl -X POST http://localhost:3000/api/fact-check \
  -H "Content-Type: application/json" \
  -d '{"statement": "I think pizza is the best food"}'
```

Expected: `type: "opinion"` — filtered by local model or Stage 2 classification.

- [ ] **Step 6: Commit any integration fixes**

```bash
git add -u
git commit -m "[fix] integration fixes from end-to-end testing"
```

---

### Task 11: Cleanup and Backward Compatibility

**Files:**
- Modify: `plugins/fact_checker/routes.py`

Remove dead code from the old provider system while maintaining backward compatibility.

- [ ] **Step 1: Remove old provider functions**

Delete from `routes.py`:
- `_get_client()` and `_client`/`_client_lock`/`_client_key` globals
- `_get_provider()`, `_get_claude_model()`, `_get_anthropic_key()`, `_get_gemini_key()`, `_get_groq_key()`, `_get_brave_key()`
- `CLAUDE_MODELS`, `PROVIDER_WEIGHTS`, `_VERDICT_RANK`, `_NEGATIVE_THRESHOLD`
- `_GEMINI_ENDPOINT`, `_BRAVE_SEARCH_URL`, `_GROQ_CHAT_URL`, `_GROQ_MODEL`
- `_extract_claude_sources()`, `_extract_gemini_sources()`
- `_run_claude_check()`, `_run_gemini_check()`, `_run_groq_check()`
- `_run_magi_check()`, `_summarize_node()`
- `_run_fact_check()`

Keep: `_SYSTEM_PROMPT` (referenced by `_build_user_prompt()`), `_parse_verdict_json()`, `_build_user_prompt()`, `_enrich_sources()`, `_split_sources()`, `_check_rate_limit()`, `_get_threshold()`, `_flip_flop_enabled()`, all MBFC/flip_flop/claim_filter imports, all route handlers, `_fetch_dossier_for()`, `handle_event()`.

- [ ] **Step 2: Update handle_event for new settings format**

The existing `handle_event` is fine — it stores the full settings dict in `_plugin_settings`. The new provider settings are passed through as JSON strings in the form data, which get stored and round-tripped correctly.

Add parsing of the providers/weights JSON strings in the config accessor if needed:

```python
def _get_parsed_settings() -> dict:
    """Parse provider settings — handles JSON strings from form data."""
    settings = dict(_plugin_settings)
    for key in ("providers", "weights"):
        val = settings.get(key)
        if isinstance(val, str):
            try:
                settings[key] = json.loads(val)
            except (json.JSONDecodeError, TypeError):
                settings[key] = {}
    return settings
```

Update `_run_consensus_pipeline` and other functions that read `_plugin_settings` to use `_get_parsed_settings()`.

- [ ] **Step 3: Verify module loads cleanly**

Run: `python -c "from plugins.fact_checker import routes; print(f'routes.py: {len(open(\"plugins/fact_checker/routes.py\").readlines())} lines, loads OK')"`

- [ ] **Step 4: Commit**

```bash
git add plugins/fact_checker/routes.py
git commit -m "[refactor] remove old single-provider code, add settings JSON parsing"
```

---

## Summary

| Task | Description | New/Modified Files |
|------|-------------|-------------------|
| 1 | Provider registry + data models | Create `providers.py` |
| 2 | Brave Search + OpenAI-compatible callers | Modify `providers.py` |
| 3 | Native search + Claude callers + dispatch | Modify `providers.py` |
| 4 | Consensus engine | Create `consensus.py` |
| 5 | Claim classification (Stage 2) | Modify `providers.py` |
| 6 | Pipeline integration (routes.py) | Modify `routes.py` |
| 7 | Manifest settings update | Modify `manifest.json` |
| 8 | Frontend provider settings UI | Modify `panel.html`, `panel.js`, `panel.css` |
| 9 | Frontend advanced settings + consensus | Modify `panel.js`, `panel.css` |
| 10 | End-to-end integration test | Manual testing |
| 11 | Cleanup + backward compat | Modify `routes.py` |
