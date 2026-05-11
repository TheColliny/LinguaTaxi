# Multi-Provider Consensus Fact Checker — Design Spec

## Goal

Rework the fact checker plugin to support multiple LLM providers running in parallel, producing a weighted consensus verdict with progressive result delivery. Replace the single-provider model (Gemini/Claude/Groq) with a configurable multi-provider architecture. Remove Groq (closing free access May 15, 2026).

## Architecture Overview

```
Transcribed text (claim)
  │
  ├─► Stage 1: Local ONNX claim filter (150-300ms, free, offline)
  │     Filters obvious non-claims at ≥99.5% confidence
  │     Non-claims → return "opinion" immediately
  │
  ├─► Stage 2: Free LLM claim classification (300-800ms, free)
  │     Run via Cerebras (fastest free provider, ~2,600 tok/s)
  │     Purpose: (A) extract clean verifiable claim for better search
  │              (B) secondary filter — catch local model false positives
  │     If not a claim → return "opinion" (saves paid API calls)
  │     If claim → output clean extracted claim text
  │
  ├─► Stage 3: ONE Brave Search call using extracted claim
  │     Results shared to ALL Brave-dependent providers
  │     Providers with native search (Gemini, Cohere, Perplexity, OpenAI) skip this
  │
  ├─► Stage 4: All enabled providers queried in parallel
  │     Each returns: verdict, score, assessment, sources
  │
  ├─► Stage 5: Progressive consensus
  │     • 1 provider enabled → return result directly (no consensus needed)
  │     • 2 providers enabled → first result = "initial", second = "final"
  │     • 3+ providers → threshold = max(2, ceil(enabled_count / 3))
  │       - Threshold met → emit "initial fact check"
  │       - All responded → emit "final fact check"
  │       - If verdict changed → explain what changed and why
  │       - If verdict same → relabel "initial" to "final"
  │
  └─► MBFC source credibility scoring applied to cited sources
```

## Provider Roster

### Free Providers

| # | Provider | Model | Search Method | Speed | Rate Limits | Default Weight | Signup |
|---|----------|-------|--------------|-------|-------------|---------------|--------|
| 1 | Gemini 3.1 Flash Lite | gemini-3.1-flash-lite | Native Google Search | Fast (1-2s) | 15 RPM, 500 RPD | **0.75** | Google account, free |
| 2 | Cerebras | gpt-oss-120b | Brave Search | Fast (< 1s) | 30 RPM, 14,400 RPD | **0.78** | Free, no credit card |
| 3 | Mistral AI | mistral-large-3 | Brave Search | Normal (3-6s) | ~60 RPM, ~1B tokens/mo | **0.80** | Free "Experiment" plan |
| 4 | GitHub Models | gpt-4.1-mini | Brave Search | Normal (3-6s) | 15 RPM, 150 RPD | **0.85** | Free for GitHub users |
| 5 | Cohere | command-a | Native web search | Normal (3-6s) | 20 RPM, 1,000 calls/mo | **0.83** | Free trial key, non-commercial |
| 6 | OpenRouter | llama-3.3-70b-instruct:free | Brave Search | Normal (3-6s) | 20 RPM, 200 RPD | **0.78** | Free account |
| 7 | OVHcloud | Llama-3.3-70B-Instruct | Brave Search | Slow (7+s) | 2 RPM (anonymous) | **0.85** | No signup needed |
| 8 | Hugging Face | Mixtral-8x7B-Instruct-v0.1 | Brave Search | Slow (7+s) | ~1,000 RPD | **0.60** | Free account |

### Paid Providers

| # | Provider | Model | Search Method | Speed | Cost (In/Out per 1M) | Default Weight | Notes |
|---|----------|-------|--------------|-------|--------------------|---------------|-------|
| 1 | Claude Sonnet 4.6 | claude-sonnet-4-6 | Brave Search | Normal (1-2s) | $3.00 / $15.00 | **0.95** | Lowest hallucination rate (34%) |
| 2 | Claude Opus 4.6 | claude-opus-4-6 | Brave Search | Normal (1-3s) | $5.00 / $25.00 | **0.88** | Deepest reasoning, higher hallucination (60%) |
| 3 | Perplexity Sonar Pro | sonar-pro | Native web search + citations | Normal (2-4s) | $3.00 / $15.00 + search fees | **0.95** | 85.8% F-score, purpose-built for fact verification |
| 4 | OpenAI GPT-5.5 | gpt-5.5 | Native web search | Normal (2-4s) | $5.00 / $30.00 | **0.93** | Latest frontier, 1.05M context, reasoning |
| 5 | OpenAI GPT-5.4-mini | gpt-5.4-mini | Native web search | Fast (1-2s) | $0.75 / $4.50 | **0.82** | Reasoning + search, good value |
| 6 | OpenAI GPT-5.4-nano | gpt-5.4-nano | Native web search | Fast (< 1s) | $0.20 / $1.25 | **0.72** | Ultra-cheap, structured outputs |
| 7 | OpenAI GPT-5-nano | gpt-5-nano | Native web search | Fast (< 1s) | $0.05 / $0.40 | **0.68** | Near-free (~2,500 checks per $1 input) |
| 8 | Google Gemini 3.1 Pro | gemini-3.1-pro | Native Google Search | Normal (3-6s) | $1.25 / $10.00 | **0.90** | Top parametric factuality + grounding |

### Weight Rationale

Weights are based on published hallucination rates (Vectara, AA-Omniscience), TruthfulQA, SimpleQA benchmarks, and model size/capability:

- **0.95 (Claude Sonnet 4.6):** 34% hallucination rate — lowest of any frontier model. Refuses rather than fabricates.
- **0.95 (Perplexity Sonar Pro):** 85.8% F-score on grounded fact-checking. Purpose-built for this exact task.
- **0.93 (GPT-5.5):** Latest OpenAI frontier with native search and reasoning.
- **0.90 (Gemini 3.1 Pro):** Top parametric factuality (SimpleQA 50.8%) + Google Search grounding.
- **0.88 (Claude Opus 4.6):** Best reasoning depth but paradoxically hallucinates more than Sonnet (60%).
- **0.85 (GitHub gpt-4.1-mini, OVHcloud Llama 3.3 70B):** Strong instruction following; Llama 3.3 has 4.1% hallucination (best open-weight).
- **0.83 (Cohere):** RAG-optimized with native web search connector.
- **0.82 (GPT-5.4-mini):** Good reasoning + native search at low cost.
- **0.80 (Mistral Large 3):** Frontier-class but 23.8% SimpleQA — needs search to compensate.
- **0.78 (Cerebras, OpenRouter):** Large models but less battle-tested on factuality benchmarks.
- **0.75 (Gemini Flash Lite):** "Lite" tier compensated by native Google Search.
- **0.72 (GPT-5.4-nano):** Tiny but has reasoning + search capabilities.
- **0.68 (GPT-5-nano):** Smallest, oldest GPT-5 variant.
- **0.60 (HF Mixtral-8x7B):** Dated MoE (2023), 73.9% TruthfulQA, smallest effective model.

### Search Method Details

**Native search providers** (handle their own web queries):
- Gemini 3.1 Flash Lite / Pro: `"tools": [{"google_search": {}}]` in API payload
- Cohere Command A: web search connector in API call
- Perplexity Sonar Pro: always searches, returns citations
- OpenAI GPT-5.x / 5.4.x / 5-nano: `web_search` tool in API payload

**Brave Search providers** (receive shared search results injected into prompt):
- Cerebras, Mistral, GitHub Models, OpenRouter, OVHcloud, Hugging Face, Claude Sonnet/Opus
- ONE Brave Search call → results formatted as numbered snippets → appended to each provider's prompt

### Brave Search Sharing

```python
# Single call, shared results
search_results = brave_search(extracted_claim, count=5)
search_context = format_search_snippets(search_results)

# Injected into each Brave-dependent provider's prompt
for provider in brave_dependent_providers:
    provider.prompt = f"{system_prompt}\n{user_prompt}\n\nWeb search results:\n{search_context}"
```

Free tier: 1,000 queries/month. At one call per fact check (shared across all providers), this supports ~33 checks/day.

## Pre-Processing Pipeline

### Stage 1: Local ONNX Claim Filter (existing)

- Model: XLM-RoBERTa Large (INT8 ONNX, ~560MB)
- Threshold: ≥0.995 confidence to filter as non-claim
- Latency: 150-300ms on CPU
- Cost: $0 (local inference)
- Non-claims return immediately: `{"type": "opinion", "assessment": "Filtered locally"}`

### Stage 2: Free LLM Claim Classification (new)

- Provider: Cerebras (gpt-oss-120b) — fastest free inference at ~2,600 tok/s
- Fallback: GitHub Models (gpt-4.1-mini) if Cerebras unavailable
- Latency: 100-300ms on Cerebras
- Cost: $0

**Purpose:**
1. **Extract clean claim:** Turn "I heard someone say that the earth is like 4 billion years old or something" into "The Earth is approximately 4 billion years old"
2. **Secondary filter:** Catch claims the local model passed at 0.995 that aren't actually verifiable facts
3. **Better search queries:** The extracted claim produces better Brave Search results than the raw transcript

**Prompt template:**
```
Analyze this transcribed statement. Respond in JSON only.
If it contains a verifiable factual claim, extract it clearly.
If it does not contain a verifiable claim, mark it as not_a_claim.

Statement: "{statement}"

Response format:
{"is_claim": true/false, "extracted_claim": "clean claim text or null", "search_query": "optimized search query or null"}
```

**If `is_claim` is false:** Return `{"type": "opinion"}` immediately — no further processing, no paid API calls consumed.

**If `is_claim` is true:** Pass `extracted_claim` to Brave Search and `search_query` as the Brave query. Continue to Stage 3.

### Stage 2 Provider Configuration

The claim classification provider should be configurable in advanced settings. Default: Cerebras (fastest). User can select any enabled free provider. If the selected provider is unavailable, fall through to the next available free provider in order: Cerebras → GitHub Models → Mistral → OpenRouter → Gemini Flash Lite.

## Consensus Engine

### Weighted Scoring

Each provider returns a verdict and accuracy score (0-100). The consensus combines them:

```python
def calculate_consensus(results: list[ProviderResult], weights: dict) -> ConsensusResult:
    total_weight = 0
    weighted_score = 0
    verdicts = {}  # verdict -> total_weight

    for r in results:
        w = weights.get(r.provider_id, 0.5)
        total_weight += w
        weighted_score += r.accuracy_score * w

        if r.verdict not in verdicts:
            verdicts[r.verdict] = 0
        verdicts[r.verdict] += w

    consensus_score = weighted_score / total_weight
    consensus_verdict = max(verdicts, key=verdicts.get)

    return ConsensusResult(
        score=consensus_score,
        verdict=consensus_verdict,
        providers_reporting=len(results),
        providers_total=total_enabled,
    )
```

### Progressive Delivery

```python
threshold = max(2, math.ceil(enabled_count / 3))

# Special cases
if enabled_count == 1:
    threshold = 1  # no consensus needed, direct result
elif enabled_count == 2:
    threshold = 1  # first = initial, second = final

on_provider_result(result):
    results.append(result)

    if len(results) == threshold and not initial_emitted:
        emit("initial_fact_check", calculate_consensus(results))
        initial_emitted = True

    if len(results) == enabled_count:
        final = calculate_consensus(results)
        if initial_verdict != final.verdict or abs(initial_score - final.score) > 10:
            emit("final_fact_check", final, changed=True,
                 reason=f"Score shifted from {initial_score} to {final.score} "
                        f"after {enabled_count - threshold} additional providers reported")
        else:
            emit("final_fact_check", final, changed=False)
```

### Threshold Examples

| Providers Enabled | Threshold | Behavior |
|-------------------|-----------|----------|
| 1 | 1 | Direct result, no "initial"/"final" distinction |
| 2 | 1 | First result = "initial", second result = "final" |
| 3 | 2 | Wait for 2, then initial; 3rd makes final |
| 4 | 2 | Wait for 2, then initial; all 4 make final |
| 6 | 2 | Wait for 2, then initial; all 6 make final |
| 9 | 3 | Wait for 3, then initial; all 9 make final |
| 12 | 4 | Wait for 4, then initial; all 12 make final |
| 16 | 6 | Wait for 6, then initial; all 16 make final |

### Display States

**When 1 provider enabled:**
- Result arrives → show as "Fact Check" (no initial/final labeling)

**When 2+ providers enabled:**
- Before threshold → "Checking..." with spinner
- Threshold met → "Initial Fact Check" with verdict, score, provider count
- All complete, same verdict → relabel to "Final Fact Check" with full provider count
- All complete, changed verdict → "Final Fact Check" with explanation:
  > "Updated from [MOSTLY TRUE, 78] to [TRUE, 91] — 4 additional providers confirmed accuracy. Initial result was based on 2 providers; final includes all 6."

### Timeout Handling

- Per-provider timeout: 15 seconds (normal), 30 seconds (slow providers: OVHcloud, HF)
- If a provider times out, exclude it from consensus and reduce `enabled_count`
- If all remaining providers needed for threshold have timed out, emit what we have as "final" with note: "Some providers timed out"

## Settings UI

### Provider List (main settings area)

Each provider shows as a collapsible row:

```
☑ Gemini 3.1 Flash Lite                    [Fast · Free · Google Search]
  API Key: [••••••••••••••fo]  ✓
  Get a free key → aistudio.google.com

☐ Claude Sonnet 4.6                        [Normal · Paid · Brave Search]
  API Key: [                    ]
  Get key → console.anthropic.com — $3/$15 per 1M tokens

☑ Cerebras                                 [Fast · Free · Brave Search]
  API Key: [••••••••••••••xx]  ✓
  Get a free key → cerebras.ai

☐ Perplexity Sonar Pro                     [Normal · Paid · Built-in Search]
  API Key: [                    ]
  Get key → docs.perplexity.ai — $3/$15 per 1M tokens + search fees
```

**Tags shown next to each provider:**
- Speed: `Fast` (1-2s) / `Normal` (3-6s) / `Slow` (7+s)
- Cost: `Free` / `Paid`
- Search: `Google Search` / `Brave Search` / `Built-in Search`

**Checkbox behavior:**
- Unchecked → API key field hidden
- Checked without API key → warning icon, provider excluded from consensus
- Checked with valid API key → green checkmark, provider active

### Brave Search Key (separate section)

```
─── Brave Search (required by most providers) ────────────
API Key: [••••••••••••••yy]  ✓
Get a free key → brave.com/search/api — Free (1,000 queries/month)
```

### Advanced Settings (collapsible)

```
▸ Advanced Settings

┌─ Model Weights ─────────────────────────────────────────┐
│ Only showing enabled providers                          │
│                                                         │
│ Gemini 3.1 Flash Lite    [0.75]  (default: 0.75)      │
│ Cerebras                 [0.78]  (default: 0.78)      │
│ Claude Sonnet 4.6        [0.95]  (default: 0.95)      │
│                                                         │
│ Weights are saved between sessions.                     │
│ Clear a field to reset to default.                      │
└─────────────────────────────────────────────────────────┘

┌─ Claim Classification ──────────────────────────────────┐
│ Classification provider: [Cerebras ▼]                   │
│ (Uses a free provider to extract claims before          │
│  sending to all fact-check providers)                   │
└─────────────────────────────────────────────────────────┘
```

**Weight field behavior:**
- Shows current value (editable text input)
- "(default: X.XX)" shown next to field
- If user clears the field (empty) → resets to default value on blur
- Modified weights are saved to `config.json` and persist across sessions
- Values clamped to 0.01–1.00

**Modified weights warning:**
At plugin load, if any weight differs from its default, show a banner in the operator panel:
> "⚠ Fact checking model weights have been modified from defaults"

This alerts a new operator that weights have been customized.

## Provider API Integration

### Providers with Native Web Search (no Brave needed)

**Google Gemini (Flash Lite / Pro):**
```python
payload = {
    "contents": [{"parts": [{"text": prompt}]}],
    "tools": [{"google_search": {}}],
    "generationConfig": {"temperature": 0.1, "maxOutputTokens": 1000},
}
# POST https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent
# Header: x-goog-api-key
```

**Cohere Command A:**
```python
payload = {
    "model": "command-a",
    "messages": [{"role": "user", "content": prompt}],
    "connectors": [{"id": "web-search"}],
    "temperature": 0.1,
}
# POST https://api.cohere.com/v2/chat
# Header: Authorization: Bearer {key}
```

**Perplexity Sonar Pro:**
```python
payload = {
    "model": "sonar-pro",
    "messages": [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ],
    "temperature": 0.1,
    "max_tokens": 1000,
}
# POST https://api.perplexity.ai/chat/completions
# Header: Authorization: Bearer {key}
# Citations returned in response metadata
```

**OpenAI GPT-5.x series:**
```python
payload = {
    "model": model_id,  # "gpt-5.5", "gpt-5.4-mini", "gpt-5.4-nano", "gpt-5-nano"
    "messages": [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ],
    "tools": [{"type": "web_search"}],
    "temperature": 0.1,
    "max_tokens": 1000,
}
# POST https://api.openai.com/v1/chat/completions
# Header: Authorization: Bearer {key}
```

### Providers Using Shared Brave Search Results

**Cerebras, Mistral, GitHub Models, OpenRouter, OVHcloud, Hugging Face:**
All use OpenAI-compatible chat completions API:
```python
payload = {
    "model": model_id,
    "messages": [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": augmented_prompt},  # includes Brave search snippets
    ],
    "temperature": 0.1,
    "max_tokens": 1000,
}
```

Base URLs:
- Cerebras: `https://api.cerebras.ai/v1/chat/completions`
- Mistral: `https://api.mistral.ai/v1/chat/completions`
- GitHub Models: `https://models.inference.ai.azure.com/chat/completions`
- OpenRouter: `https://openrouter.ai/api/v1/chat/completions`
- OVHcloud: `https://oai.endpoints.kepler.ai.cloud.ovh.net/v1/chat/completions`
- Hugging Face: `https://api-inference.huggingface.co/models/mistralai/Mixtral-8x7B-Instruct-v0.1/v1/chat/completions`

**Claude Sonnet / Opus:**
```python
payload = {
    "model": model_id,  # "claude-sonnet-4-6-20250514" / "claude-opus-4-6-20250514"
    "system": system_prompt,
    "messages": [{"role": "user", "content": augmented_prompt}],
    "temperature": 0.1,
    "max_tokens": 1000,
}
# POST https://api.anthropic.com/v1/messages
# Headers: x-api-key, anthropic-version: 2023-06-01
```

### Grounding Fallback

For Gemini providers: if the `google_search` grounding call returns HTTP 429, retry without the `tools` key (same pattern already implemented for Flash Lite).

## Data Models

### ProviderResult
```python
@dataclass
class ProviderResult:
    provider_id: str        # "gemini_flash_lite", "cerebras", etc.
    verdict: str            # "TRUE", "FALSE", "MOSTLY TRUE", "MOSTLY FALSE", "AMBIGUOUS"
    accuracy_score: float   # 0-100
    assessment: str         # Human-readable explanation
    claim: str              # The claim as understood by the provider
    sources: list[dict]     # [{"url": ..., "title": ...}]
    language_signals: str | None
    error: str | None
    latency_ms: int         # How long this provider took
```

### ConsensusResult
```python
@dataclass
class ConsensusResult:
    stage: str              # "initial" | "final" | "direct" (1 provider)
    verdict: str
    accuracy_score: float   # Weighted average
    assessment: str         # Generated summary
    providers_reporting: int
    providers_total: int
    changed_from_initial: bool
    change_reason: str | None
    provider_results: list[ProviderResult]
    all_sources: list[dict]         # Merged + deduplicated
    flagged_sources: list[dict]     # MBFC-flagged
```

### Updated FactCheckResponse (extends existing)
```python
class FactCheckResponse(BaseModel):
    type: str
    claim: str | None
    accuracy_score: float | None
    verdict: str | None
    assessment: str | None
    language_signals: str | None
    error: str | None
    flip_flop: dict | None
    sources: list[dict]
    flagged_sources: list[dict]
    provider: str | None               # Deprecated — kept for backward compat
    # New consensus fields
    consensus_stage: str | None        # "initial", "final", "direct", None
    consensus_providers: int | None    # How many providers contributed
    consensus_total: int | None        # How many total were queried
    consensus_changed: bool | None     # Did verdict change from initial?
    consensus_reason: str | None       # Why it changed
    provider_breakdown: list[dict] | None  # Per-provider verdicts/scores
```

## Settings Persistence

### Config Structure (config.json)
```json
{
    "plugin_settings": {
        "fact_checker": {
            "providers": {
                "gemini_flash_lite": {"enabled": true, "api_key": "AIza..."},
                "cerebras": {"enabled": true, "api_key": "csk-..."},
                "claude_sonnet": {"enabled": false, "api_key": ""},
                ...
            },
            "brave_api_key": "BSA...",
            "weights": {
                "gemini_flash_lite": 0.75,
                "cerebras": 0.78,
                ...
            },
            "classification_provider": "cerebras",
            "local_filter": "true",
            "rate_limit": 15,
            "credibility_threshold": 32,
            "flip_flop_enabled": "false",
            "mode": "auto"
        }
    }
}
```

### Manifest Settings Schema Update

The manifest `settings_schema` will be simplified — individual provider API key fields replaced by structured `providers` object. The settings UI will be custom-rendered by the plugin's panel JS rather than using the generic key-value settings form.

## Backward Compatibility

- The existing `provider` field in `FactCheckResponse` is kept but deprecated
- When only one provider is enabled, behavior is identical to current single-provider mode
- The existing `/api/fact-check` endpoint signature does not change
- The `/api/fact-check/status` endpoint will return expanded provider status

## Provider Order Expectations

Typical response order (fastest first):
1. Cerebras (~0.5-1s)
2. Gemini Flash Lite (~1-2s)
3. OpenAI nano models (~1s)
4. OpenAI mini models (~1-2s)
5. Claude Sonnet (~1-2s)
6. GitHub Models (~3-4s)
7. Mistral (~3-5s)
8. Cohere (~3-5s)
9. Perplexity Sonar Pro (~3-5s)
10. Claude Opus (~3-5s)
11. Gemini Pro (~4-6s)
12. OpenRouter (~4-6s)
13. OVHcloud (~7-10s)
14. Hugging Face (~7-15s)

With 3+ providers enabled, the "initial" result typically arrives within 1-2 seconds (from Cerebras + Gemini or a nano model).

## Speed Labels for UI

- **Fast** (1-2s): Cerebras, Gemini Flash Lite, OpenAI nano/mini models, Claude Sonnet
- **Normal** (3-6s): Mistral, GitHub Models, Cohere, Perplexity, Claude Opus, OpenRouter, OpenAI GPT-5.5, Gemini Pro
- **Slow** (7+s): OVHcloud, Hugging Face

## Files to Create/Modify

- **Modify:** `plugins/fact_checker/routes.py` — major rework of fact-check pipeline
- **Create:** `plugins/fact_checker/providers.py` — provider registry, API call implementations
- **Create:** `plugins/fact_checker/consensus.py` — weighted consensus engine
- **Modify:** `plugins/fact_checker/panel.js` — new settings UI with provider checkboxes
- **Modify:** `plugins/fact_checker/panel.html` — settings layout changes
- **Modify:** `plugins/fact_checker/manifest.json` — updated settings schema
- **Modify:** `plugins/fact_checker/panel.css` — styles for new settings components
