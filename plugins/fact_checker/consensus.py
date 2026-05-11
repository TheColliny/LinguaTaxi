"""
LinguaTaxi — Fact Checker Plugin: Consensus Engine

Weighted consensus calculation with progressive delivery support.
Works with any objects that expose .provider_id, .verdict, .accuracy_score,
.assessment, and .error (duck typing — no import from providers.py).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field


# ════════════════════════════════════════════════════════════════════════════
# Data Model
# ════════════════════════════════════════════════════════════════════════════

@dataclass
class ConsensusResult:
    """Aggregated result from one or more provider fact-check calls."""

    stage: str                       # "initial" | "final" | "direct"
    verdict: str | None              # winning verdict, or None if all failed
    accuracy_score: float | None     # weighted average, rounded to 1 decimal
    assessment: str                  # human-readable summary
    providers_reporting: int         # number of successful providers
    providers_total: int             # total enabled providers attempted

    changed_from_initial: bool = False
    change_reason: str | None = None

    provider_results: list = field(default_factory=list)
    all_sources: list[dict] = field(default_factory=list)
    flagged_sources: list[dict] = field(default_factory=list)


# ════════════════════════════════════════════════════════════════════════════
# Verdict Ranking
# ════════════════════════════════════════════════════════════════════════════

_VERDICT_RANK: dict[str, int] = {
    "TRUE": 5,
    "MOSTLY TRUE": 4,
    "MIXED": 3,
    "MOSTLY FALSE": 2,
    "FALSE": 1,
    "UNVERIFIABLE": 0,
}


# ════════════════════════════════════════════════════════════════════════════
# Threshold Logic
# ════════════════════════════════════════════════════════════════════════════

def get_threshold(enabled_count: int) -> int:
    """Return the minimum number of provider results required to emit a result.

    Rules:
      - 1 provider  → 1  (always direct, no waiting)
      - 2 providers → 1  (first result = initial, second = final)
      - 3+          → max(2, ceil(enabled_count / 3))

    Expected: 1→1, 2→1, 3→2, 4→2, 6→2, 9→3, 12→4, 16→6
    """
    if enabled_count <= 1:
        return 1
    if enabled_count == 2:
        return 1
    return max(2, math.ceil(enabled_count / 3))


# ════════════════════════════════════════════════════════════════════════════
# Core Consensus Calculation
# ════════════════════════════════════════════════════════════════════════════

def calculate_consensus(
    results: list,
    weights: dict[str, float],
    total_enabled: int,
) -> ConsensusResult:
    """Calculate weighted consensus from a list of provider result objects.

    Parameters
    ----------
    results:
        List of objects with attributes: .provider_id, .verdict,
        .accuracy_score, .assessment, .error.
    weights:
        Mapping of provider_id → weight (float).
    total_enabled:
        Total number of enabled providers (including those that may not have
        reported yet or have failed).

    Returns
    -------
    ConsensusResult
    """
    # ── Separate successful from errored ─────────────────────────────────────
    successful = [r for r in results if not r.error]
    errored = [r for r in results if r.error]

    # ── All failed ───────────────────────────────────────────────────────────
    if not successful:
        error_lines = "; ".join(
            f"{r.provider_id}: {r.error}" for r in errored
        )
        assessment = f"All providers failed. Errors: {error_lines}" if error_lines else "No provider results available."
        return ConsensusResult(
            stage="final",
            verdict=None,
            accuracy_score=None,
            assessment=assessment,
            providers_reporting=0,
            providers_total=total_enabled,
            provider_results=list(results),
        )

    # ── Determine stage ──────────────────────────────────────────────────────
    if total_enabled == 1:
        stage = "direct"
    elif len(successful) < total_enabled:
        stage = "initial"
    else:
        stage = "final"

    # ── Weighted accuracy score ──────────────────────────────────────────────
    scored = [r for r in successful if r.accuracy_score is not None]
    if scored:
        total_weight = sum(weights.get(r.provider_id, 1.0) for r in scored)
        weighted_sum = sum(
            r.accuracy_score * weights.get(r.provider_id, 1.0) for r in scored
        )
        accuracy_score: float | None = round(weighted_sum / total_weight, 1)
    else:
        accuracy_score = None

    # ── Weighted verdict ─────────────────────────────────────────────────────
    verdict_weights: dict[str, float] = {}
    for r in successful:
        if r.verdict is not None:
            w = weights.get(r.provider_id, 1.0)
            verdict_weights[r.verdict] = verdict_weights.get(r.verdict, 0.0) + w

    if verdict_weights:
        winning_verdict: str | None = max(verdict_weights, key=lambda v: verdict_weights[v])
    else:
        winning_verdict = None

    # ── Assessment — pick from highest-weighted successful provider ──────────
    def _provider_weight(r) -> float:
        return weights.get(r.provider_id, 1.0)

    best_provider = max(successful, key=_provider_weight)
    base_assessment: str = best_provider.assessment or ""

    # ── Split verdict detection ──────────────────────────────────────────────
    unique_verdicts = [v for v in verdict_weights if v in _VERDICT_RANK]
    split = False
    if len(unique_verdicts) >= 2:
        ranks = sorted(_VERDICT_RANK[v] for v in unique_verdicts)
        if ranks[-1] - ranks[0] > 1:
            split = True

    if split:
        parts = []
        for r in successful:
            score_str = f"{int(r.accuracy_score)}%" if r.accuracy_score is not None else "N/A"
            parts.append(f"{r.provider_id}: {r.verdict} ({score_str})")
        split_prefix = "SPLIT VERDICT — " + "; ".join(parts)
        assessment = f"{split_prefix}\n{base_assessment}" if base_assessment else split_prefix
    else:
        assessment = base_assessment

    # ── Collect sources from all successful results ──────────────────────────
    all_sources: list[dict] = []
    for r in successful:
        sources = getattr(r, "sources", []) or []
        all_sources.extend(sources)

    return ConsensusResult(
        stage=stage,
        verdict=winning_verdict,
        accuracy_score=accuracy_score,
        assessment=assessment,
        providers_reporting=len(successful),
        providers_total=total_enabled,
        provider_results=list(results),
        all_sources=all_sources,
    )


# ════════════════════════════════════════════════════════════════════════════
# Change Detection
# ════════════════════════════════════════════════════════════════════════════

def check_verdict_changed(
    initial: ConsensusResult,
    final: ConsensusResult,
) -> tuple[bool, str | None]:
    """Compare an initial consensus result to a final one.

    Returns
    -------
    (True, reason)  if the verdict changed OR the accuracy score shifted >10 pts
    (False, None)   otherwise
    """
    verdict_changed = (
        initial.verdict is not None
        and final.verdict is not None
        and initial.verdict != final.verdict
    )

    score_shifted = False
    if (
        initial.accuracy_score is not None
        and final.accuracy_score is not None
        and abs(final.accuracy_score - initial.accuracy_score) > 10
    ):
        score_shifted = True

    if verdict_changed and score_shifted:
        reason = (
            f"Verdict changed from {initial.verdict} to {final.verdict} "
            f"and score shifted from {initial.accuracy_score} to {final.accuracy_score}"
        )
        return True, reason

    if verdict_changed:
        reason = f"Verdict changed from {initial.verdict} to {final.verdict}"
        return True, reason

    if score_shifted:
        reason = (
            f"Score shifted from {initial.accuracy_score} "
            f"to {final.accuracy_score} (>{10} point change)"
        )
        return True, reason

    return False, None
