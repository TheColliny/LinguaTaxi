"""
LinguaTaxi — Fact Checker Plugin Routes
POST /api/fact-check        — analyze a statement for accuracy
GET  /api/fact-check/status — health check + provider status

Multi-provider consensus fact checking:
  All enabled providers are queried in parallel and their verdicts are merged
  via a weighted consensus engine (see consensus.py). Sources are cross-referenced
  against Media Bias Fact Check (MBFC) for credibility scoring.
"""

import asyncio
import collections
import concurrent.futures
import importlib.util
import json
import logging
import sys
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

# ── Load providers registry ──
_providers_spec = importlib.util.spec_from_file_location(
    "providers", str(Path(__file__).parent / "providers.py")
)
_providers_mod = importlib.util.module_from_spec(_providers_spec)
sys.modules[_providers_spec.name] = _providers_mod
_providers_spec.loader.exec_module(_providers_mod)
providers = _providers_mod

# ── Load consensus engine ──
_consensus_spec = importlib.util.spec_from_file_location(
    "consensus", str(Path(__file__).parent / "consensus.py")
)
_consensus_mod = importlib.util.module_from_spec(_consensus_spec)
sys.modules[_consensus_spec.name] = _consensus_mod
_consensus_spec.loader.exec_module(_consensus_mod)
consensus = _consensus_mod

router = APIRouter(prefix="/api")

# ── Dedicated thread pool ──
_fc_pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="factcheck")

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
    provider: str | None = None             # Deprecated — backward compat
    magi_consensus: str | None = None       # Deprecated — backward compat
    magi_nodes: dict | None = None          # Deprecated — backward compat
    consensus_stage: str | None = None      # "initial", "final", "direct"
    consensus_providers: int | None = None
    consensus_total: int | None = None
    consensus_changed: bool | None = None
    consensus_reason: str | None = None
    provider_breakdown: list[dict] | None = None


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


# ── Source enrichment (shared by all providers) ──

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


# ═══════════════════════════════════════════════════════════════════════════
# Multi-provider consensus pipeline
# ═══════════════════════════════════════════════════════════════════════════

def _run_consensus_pipeline(statement: str, recheck: bool = False,
                            previous_verdict: str | None = None,
                            previous_assessment: str | None = None,
                            previous_score: float | None = None,
                            speaker: str | None = None) -> dict:
    """5-stage consensus pipeline: filter → classify → search → query → merge."""

    # ── Stage 0: Setup ──────────────────────────────────────────────────────
    mbfc_ensure_loaded()
    threshold = _get_threshold()
    settings = _get_parsed_settings()

    enabled = providers.get_enabled_providers(settings)
    if not enabled:
        return {"type": "ambiguous", "error": "No providers enabled or configured with API keys."}

    # Build the user prompt for the LLM calls
    user_prompt = _build_user_prompt(
        statement, recheck, previous_verdict, previous_assessment, previous_score,
        speaker=speaker,
    )

    # ── Stage 2: Claim classification (skip for rechecks or single provider) ──
    search_query = statement  # default search query is the raw statement
    if not recheck and len(enabled) > 1:
        classification_provider = settings.get("classification_provider")
        classification = providers.classify_claim(statement, settings, classification_provider)
        if classification is not None:
            if not classification["is_claim"]:
                return {"type": "opinion"}
            # Use extracted claim as the basis for further stages
            if classification.get("extracted_claim"):
                user_prompt = _build_user_prompt(
                    classification["extracted_claim"], recheck,
                    previous_verdict, previous_assessment, previous_score,
                    speaker=speaker,
                )
            if classification.get("search_query"):
                search_query = classification["search_query"]

    # ── Stage 3: Brave Search (one shared call for providers that need it) ──
    search_context = ""
    brave_results: list[dict] = []
    brave_key = providers.get_brave_api_key(settings)
    any_needs_brave = any(providers.needs_brave_search(cfg.provider_id) for cfg in enabled)
    if any_needs_brave and brave_key:
        brave_results = providers.brave_search(search_query, brave_key, count=5)
        search_context = providers.format_search_snippets(brave_results)

    # ── Stage 4: Query all enabled providers in parallel ────────────────────
    max_workers = min(len(enabled), 8)
    futures_map: dict[concurrent.futures.Future, providers.ProviderConfig] = {}

    with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="consensus") as pool:
        for cfg in enabled:
            future = pool.submit(
                providers.call_provider,
                cfg.provider_id,
                user_prompt,
                search_context if providers.needs_brave_search(cfg.provider_id) else "",
                settings,
            )
            futures_map[future] = cfg

        # Collect all results (45s timeout per future)
        provider_results = []
        for future in concurrent.futures.as_completed(futures_map, timeout=60):
            cfg = futures_map[future]
            try:
                result = future.result(timeout=45)
                provider_results.append(result)
            except TimeoutError:
                provider_results.append(
                    providers._error_result(cfg.provider_id, f"{cfg.display_name} timed out")
                )
            except Exception as e:
                provider_results.append(
                    providers._error_result(cfg.provider_id, str(e)[:200])
                )

    # ── Stage 5: Consensus calculation ──────────────────────────────────────
    weights = {
        cfg.provider_id: providers.get_provider_weight(cfg.provider_id, settings)
        for cfg in enabled
    }

    consensus_result = consensus.calculate_consensus(
        provider_results, weights, total_enabled=len(enabled)
    )

    # ── Source merging: brave + native provider sources ──────────────────────
    all_sources = []
    seen_urls = set()

    # Add brave search sources first (they have URLs)
    for r in brave_results:
        url = r.get("url", "")
        if url and url not in seen_urls:
            seen_urls.add(url)
            all_sources.append({"url": url, "title": r.get("title", "")})

    # Add provider-native sources
    for pr in provider_results:
        for src in getattr(pr, "sources", []) or []:
            url = src.get("url", "")
            if url and url not in seen_urls:
                seen_urls.add(url)
                all_sources.append(src)

    # Enrich with MBFC and split
    enriched = _enrich_sources(all_sources, threshold)
    credible, flagged = _split_sources(enriched)

    # ── Build provider_breakdown for frontend ───────────────────────────────
    provider_breakdown = []
    for pr in provider_results:
        provider_breakdown.append({
            "provider_id": pr.provider_id,
            "display_name": (
                providers.get_provider_config(pr.provider_id).display_name
                if providers.get_provider_config(pr.provider_id) else pr.provider_id
            ),
            "verdict": pr.verdict,
            "accuracy_score": pr.accuracy_score,
            "assessment": pr.assessment,
            "error": pr.error,
            "latency_ms": pr.latency_ms,
            "weight": weights.get(pr.provider_id, 0.0),
        })

    # ── Build backward-compatible magi_nodes dict ───────────────────────────
    magi_nodes = {}
    for pr in provider_results:
        cfg = providers.get_provider_config(pr.provider_id)
        magi_nodes[pr.provider_id] = {
            "label": cfg.display_name if cfg else pr.provider_id,
            "weight": weights.get(pr.provider_id, 0.0),
            "verdict": pr.verdict,
            "accuracy_score": pr.accuracy_score,
            "assessment": pr.assessment,
            "error": pr.error,
        }

    # ── Determine backward-compat magi_consensus string ─────────────────────
    successful = [pr for pr in provider_results if not pr.error]
    if not successful:
        magi_consensus = "all_failed"
    elif len(successful) == 1:
        magi_consensus = f"{successful[0].provider_id}_only"
    else:
        unique_verdicts = set(pr.verdict for pr in successful if pr.verdict)
        if len(unique_verdicts) <= 1:
            magi_consensus = "agree"
        else:
            magi_consensus = "disagree"

    # ── Determine result_type from consensus or first successful result ──────
    result_type = "fact_claim"
    if successful:
        result_type = getattr(successful[0], "result_type", "fact_claim") or "fact_claim"

    return {
        "type": result_type,
        "claim": consensus_result.assessment.split("\n")[0][:200] if not successful else (
            getattr(max(successful, key=lambda r: weights.get(r.provider_id, 0.0)), "claim", None)
        ),
        "accuracy_score": consensus_result.accuracy_score,
        "verdict": consensus_result.verdict,
        "assessment": consensus_result.assessment,
        "language_signals": (
            getattr(max(successful, key=lambda r: weights.get(r.provider_id, 0.0)), "language_signals", None)
            if successful else None
        ),
        "sources": credible,
        "flagged_sources": flagged,
        # Backward compat
        "provider": "consensus",
        "magi_consensus": magi_consensus,
        "magi_nodes": magi_nodes,
        # New consensus fields
        "consensus_stage": consensus_result.stage,
        "consensus_providers": consensus_result.providers_reporting,
        "consensus_total": consensus_result.providers_total,
        "consensus_changed": consensus_result.changed_from_initial,
        "consensus_reason": consensus_result.change_reason,
        "provider_breakdown": provider_breakdown,
    }


# ═══════════════════════════════════════════════════════════════════════════
# Dossier building (flip-flop prefetch)
# ═══════════════════════════════════════════════════════════════════════════

def _fetch_dossier_for(name: str) -> dict | None:
    """Fetch a speaker dossier using a single enabled provider. One AI call per speaker.
    Runs in the flip_flop._pool background thread (see flip_flop.queue_prefetch)."""
    settings = _get_parsed_settings()
    enabled = providers.get_enabled_providers(settings)

    if not enabled:
        return None

    # Pick a single provider for the dossier (first enabled is fine; no need for consensus)
    cfg = enabled[0]
    prompt = f"{flip_flop.get_dossier_prompt()}\n\nSubject: {name}"

    try:
        result = providers.call_provider(cfg.provider_id, prompt, "", settings)
    except Exception as e:
        log.error(f"[Flip-Flop] Dossier fetch error for '{name}': {e}")
        return None

    if result.error:
        log.warning(f"[Flip-Flop] Dossier for '{name}': {result.error}")
        return None

    # The provider returns a ProviderResult. The AI was asked for dossier JSON, so
    # parse the assessment (or look for dossier-specific keys in the parsed output).
    # call_provider internally parses the JSON — but dossier format doesn't match
    # the fact-check schema. We need to re-parse the raw response. Since call_provider
    # already parsed into ProviderResult fields, check if the claim/assessment contain
    # dossier data. The best approach: the dossier prompt asks for statements/positions,
    # which won't map to verdict fields. Check if the provider returned them.
    # Since ProviderResult maps claim/assessment/verdict from the parsed JSON, and the
    # dossier response has different keys (statements, positions), the parsed dict
    # won't have those. We need the raw text — but call_provider doesn't expose it.
    # Fallback: if the assessment looks like a JSON string, try parsing it.
    if result.assessment:
        parsed = providers._parse_verdict_json(result.assessment)
        if parsed and ("statements" in parsed or "positions" in parsed):
            return {
                "statements": parsed.get("statements", []) or [],
                "positions": parsed.get("positions", {}) or {},
            }

    return None


class PrefetchRequest(BaseModel):
    speakers: list[str]


# ── Routes ──

@router.get("/fact-check/status")
async def fact_check_status():
    """Health check — provider status, keys, MBFC data."""
    settings = _get_parsed_settings()
    enabled = providers.get_enabled_providers(settings)
    brave_key = providers.get_brave_api_key(settings)

    # Build per-provider details
    provider_details = {}
    for pid, cfg in providers.PROVIDER_REGISTRY.items():
        has_key = bool(providers.get_provider_api_key(pid, settings))
        providers_cfg = settings.get("providers", {})
        is_enabled = providers_cfg.get(pid, {}).get("enabled", False)
        provider_details[pid] = {
            "display_name": cfg.display_name,
            "category": cfg.category,
            "speed": cfg.speed,
            "search_method": cfg.search_method,
            "has_key": has_key,
            "enabled": bool(is_enabled),
            "weight": providers.get_provider_weight(pid, settings),
            "cost_info": cfg.cost_info,
            "signup_url": cfg.signup_url,
        }

    return {
        "status": "ok",
        "provider_count": len(providers.PROVIDER_REGISTRY),
        "providers_enabled": [cfg.provider_id for cfg in enabled],
        "provider_details": provider_details,
        "brave_key_set": bool(brave_key),
        "classification_provider": settings.get("classification_provider"),
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

    # Validate that at least one provider is enabled and has keys
    enabled = providers.get_enabled_providers(_get_parsed_settings())
    if not enabled:
        raise HTTPException(
            status_code=503,
            detail="No providers enabled with API keys. Configure at least one provider in plugin settings.",
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
    settings = _get_parsed_settings()
    enabled = providers.get_enabled_providers(settings)
    if not enabled:
        raise HTTPException(status_code=503, detail="No AI provider configured with API keys.")
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
