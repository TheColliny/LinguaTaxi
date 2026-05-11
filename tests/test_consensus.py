"""
Tests for plugins/fact_checker/consensus.py

Run with:
    python -m pytest tests/test_consensus.py -v
or:
    python tests/test_consensus.py
"""

import sys
import os

# ── sys.path fixup so the plugin module is importable without installing ──
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_PLUGIN_DIR = os.path.join(_REPO_ROOT, "plugins", "fact_checker")
for _p in (_REPO_ROOT, _PLUGIN_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import importlib.util as _ilu

# Load consensus.py via importlib (same approach as test_providers_registry.py).
_spec = _ilu.spec_from_file_location(
    "consensus",
    os.path.join(_PLUGIN_DIR, "consensus.py"),
)
_mod = _ilu.module_from_spec(_spec)
sys.modules["consensus"] = _mod
_spec.loader.exec_module(_mod)

ConsensusResult = _mod.ConsensusResult
get_threshold = _mod.get_threshold
calculate_consensus = _mod.calculate_consensus
check_verdict_changed = _mod.check_verdict_changed
_VERDICT_RANK = _mod._VERDICT_RANK


# ════════════════════════════════════════════════════════════════════════════
# Minimal stub — duck-typed ProviderResult substitute
# ════════════════════════════════════════════════════════════════════════════

class _FakeResult:
    """Lightweight stand-in for ProviderResult; no import from providers.py."""

    def __init__(
        self,
        provider_id: str,
        verdict=None,
        accuracy_score=None,
        assessment=None,
        error=None,
        sources=None,
    ):
        self.provider_id = provider_id
        self.verdict = verdict
        self.accuracy_score = accuracy_score
        self.assessment = assessment
        self.error = error
        self.sources = sources or []


# ════════════════════════════════════════════════════════════════════════════
# get_threshold
# ════════════════════════════════════════════════════════════════════════════

def test_threshold_1():
    assert get_threshold(1) == 1


def test_threshold_2():
    assert get_threshold(2) == 1


def test_threshold_3():
    assert get_threshold(3) == 2


def test_threshold_4():
    assert get_threshold(4) == 2


def test_threshold_6():
    assert get_threshold(6) == 2


def test_threshold_9():
    assert get_threshold(9) == 3


def test_threshold_12():
    assert get_threshold(12) == 4


def test_threshold_16():
    assert get_threshold(16) == 6


# ════════════════════════════════════════════════════════════════════════════
# calculate_consensus — stage determination
# ════════════════════════════════════════════════════════════════════════════

def test_single_provider_stage_is_direct():
    results = [
        _FakeResult("p1", verdict="TRUE", accuracy_score=90.0, assessment="Solid evidence."),
    ]
    weights = {"p1": 1.0}
    cr = calculate_consensus(results, weights, total_enabled=1)
    assert cr.stage == "direct"
    assert cr.verdict == "TRUE"
    assert cr.providers_reporting == 1
    assert cr.providers_total == 1


def test_two_providers_first_result_stage_is_initial():
    """Simulate receiving the first of two expected providers."""
    results = [
        _FakeResult("p1", verdict="TRUE", accuracy_score=85.0, assessment="Looks true."),
    ]
    weights = {"p1": 1.0, "p2": 1.0}
    cr = calculate_consensus(results, weights, total_enabled=2)
    assert cr.stage == "initial"


def test_two_providers_both_results_stage_is_final():
    """Simulate receiving both providers."""
    results = [
        _FakeResult("p1", verdict="TRUE", accuracy_score=85.0, assessment="Looks true."),
        _FakeResult("p2", verdict="TRUE", accuracy_score=90.0, assessment="Confirmed."),
    ]
    weights = {"p1": 0.8, "p2": 0.9}
    cr = calculate_consensus(results, weights, total_enabled=2)
    assert cr.stage == "final"
    assert cr.verdict == "TRUE"


# ════════════════════════════════════════════════════════════════════════════
# calculate_consensus — weighted score and verdict
# ════════════════════════════════════════════════════════════════════════════

def test_three_providers_agreement_weighted_score():
    """Three providers agree; verify weighted average and verdict."""
    results = [
        _FakeResult("p1", verdict="MOSTLY TRUE", accuracy_score=70.0, assessment="Mostly ok."),
        _FakeResult("p2", verdict="MOSTLY TRUE", accuracy_score=80.0, assessment="Looks good."),
        _FakeResult("p3", verdict="MOSTLY TRUE", accuracy_score=90.0, assessment="Strong evidence."),
    ]
    weights = {"p1": 0.5, "p2": 1.0, "p3": 2.0}
    cr = calculate_consensus(results, weights, total_enabled=3)

    # Weighted average: (70*0.5 + 80*1.0 + 90*2.0) / (0.5 + 1.0 + 2.0)
    # = (35 + 80 + 180) / 3.5 = 295 / 3.5 ≈ 84.3
    assert cr.verdict == "MOSTLY TRUE"
    assert cr.accuracy_score == round((70 * 0.5 + 80 * 1.0 + 90 * 2.0) / 3.5, 1)
    assert cr.stage == "final"
    assert cr.providers_reporting == 3


def test_weighted_verdict_uses_weight_not_count():
    """Higher-weighted minority verdict should win over lower-weighted majority."""
    results = [
        _FakeResult("low1", verdict="FALSE", accuracy_score=30.0, assessment="Debunked."),
        _FakeResult("low2", verdict="FALSE", accuracy_score=35.0, assessment="Wrong."),
        _FakeResult("high", verdict="TRUE",  accuracy_score=95.0, assessment="Verified true."),
    ]
    # low1 + low2 weights = 0.2 each → 0.4 total for FALSE
    # high weight = 0.9 → TRUE wins
    weights = {"low1": 0.2, "low2": 0.2, "high": 0.9}
    cr = calculate_consensus(results, weights, total_enabled=3)
    assert cr.verdict == "TRUE"


def test_assessment_comes_from_highest_weighted_provider():
    results = [
        _FakeResult("cheap", verdict="TRUE", accuracy_score=80.0, assessment="Cheap provider says true."),
        _FakeResult("premium", verdict="TRUE", accuracy_score=85.0, assessment="Premium provider confirms."),
    ]
    weights = {"cheap": 0.5, "premium": 0.95}
    cr = calculate_consensus(results, weights, total_enabled=2)
    assert cr.assessment == "Premium provider confirms."


# ════════════════════════════════════════════════════════════════════════════
# calculate_consensus — split verdict
# ════════════════════════════════════════════════════════════════════════════

def test_three_providers_split_verdict_prefix():
    """Verdicts that differ by >1 rank → 'SPLIT VERDICT' prefix in assessment."""
    results = [
        _FakeResult("p1", verdict="TRUE",  accuracy_score=92.0, assessment="Clearly true."),
        _FakeResult("p2", verdict="FALSE", accuracy_score=20.0, assessment="Clearly false."),
        _FakeResult("p3", verdict="MIXED", accuracy_score=55.0, assessment="It depends."),
    ]
    # TRUE=5, FALSE=1, MIXED=3 → spread of 4, clearly > 1
    weights = {"p1": 0.8, "p2": 0.8, "p3": 0.8}
    cr = calculate_consensus(results, weights, total_enabled=3)
    assert "SPLIT VERDICT" in cr.assessment


def test_adjacent_verdicts_do_not_trigger_split():
    """TRUE(5) and MOSTLY TRUE(4) differ by 1 → NOT a split."""
    results = [
        _FakeResult("p1", verdict="TRUE",        accuracy_score=95.0, assessment="True."),
        _FakeResult("p2", verdict="MOSTLY TRUE",  accuracy_score=80.0, assessment="Mostly true."),
    ]
    weights = {"p1": 0.9, "p2": 0.9}
    cr = calculate_consensus(results, weights, total_enabled=2)
    assert "SPLIT VERDICT" not in cr.assessment


# ════════════════════════════════════════════════════════════════════════════
# calculate_consensus — all providers errored
# ════════════════════════════════════════════════════════════════════════════

def test_all_providers_errored_returns_failed_result():
    results = [
        _FakeResult("p1", error="API timeout"),
        _FakeResult("p2", error="Rate limit exceeded"),
        _FakeResult("p3", error="Network error"),
    ]
    weights = {"p1": 1.0, "p2": 1.0, "p3": 1.0}
    cr = calculate_consensus(results, weights, total_enabled=3)

    assert cr.verdict is None
    assert cr.accuracy_score is None
    assert cr.stage == "final"
    assert cr.providers_reporting == 0
    assert cr.providers_total == 3
    # Error messages should be mentioned
    assert "API timeout" in cr.assessment
    assert "Rate limit exceeded" in cr.assessment
    assert "Network error" in cr.assessment


def test_partial_failure_still_produces_result():
    """If some providers fail but at least one succeeds, consensus still works."""
    results = [
        _FakeResult("p1", verdict="TRUE", accuracy_score=88.0, assessment="Well supported."),
        _FakeResult("p2", error="API timeout"),
    ]
    weights = {"p1": 0.9, "p2": 0.9}
    cr = calculate_consensus(results, weights, total_enabled=2)

    assert cr.verdict == "TRUE"
    assert cr.providers_reporting == 1
    assert cr.providers_total == 2
    # Has at least one failure → initial (not all providers reported)
    assert cr.stage == "initial"


# ════════════════════════════════════════════════════════════════════════════
# calculate_consensus — sources aggregation
# ════════════════════════════════════════════════════════════════════════════

def test_sources_are_aggregated_from_all_successful_providers():
    results = [
        _FakeResult(
            "p1", verdict="TRUE", accuracy_score=90.0, assessment="True.",
            sources=[{"url": "https://example.com/a", "title": "Source A"}],
        ),
        _FakeResult(
            "p2", verdict="TRUE", accuracy_score=85.0, assessment="Confirmed.",
            sources=[{"url": "https://example.com/b", "title": "Source B"}],
        ),
    ]
    weights = {"p1": 0.9, "p2": 0.8}
    cr = calculate_consensus(results, weights, total_enabled=2)
    assert len(cr.all_sources) == 2
    urls = {s["url"] for s in cr.all_sources}
    assert "https://example.com/a" in urls
    assert "https://example.com/b" in urls


# ════════════════════════════════════════════════════════════════════════════
# ConsensusResult dataclass defaults
# ════════════════════════════════════════════════════════════════════════════

def test_consensus_result_defaults():
    cr = ConsensusResult(
        stage="final",
        verdict="TRUE",
        accuracy_score=88.0,
        assessment="Test assessment.",
        providers_reporting=2,
        providers_total=3,
    )
    assert cr.changed_from_initial is False
    assert cr.change_reason is None
    assert cr.provider_results == []
    assert cr.all_sources == []
    assert cr.flagged_sources == []


# ════════════════════════════════════════════════════════════════════════════
# check_verdict_changed
# ════════════════════════════════════════════════════════════════════════════

def _make_cr(verdict, score):
    """Helper: build a minimal ConsensusResult."""
    return ConsensusResult(
        stage="final",
        verdict=verdict,
        accuracy_score=score,
        assessment="",
        providers_reporting=1,
        providers_total=1,
    )


def test_same_verdict_same_score_returns_false():
    initial = _make_cr("TRUE", 85.0)
    final   = _make_cr("TRUE", 87.0)   # <10 pt shift
    changed, reason = check_verdict_changed(initial, final)
    assert changed is False
    assert reason is None


def test_different_verdict_returns_true_with_reason():
    initial = _make_cr("TRUE",  85.0)
    final   = _make_cr("FALSE", 30.0)
    changed, reason = check_verdict_changed(initial, final)
    assert changed is True
    assert reason is not None
    assert "TRUE" in reason
    assert "FALSE" in reason


def test_score_shift_greater_than_10_returns_true():
    initial = _make_cr("MIXED", 60.0)
    final   = _make_cr("MIXED", 71.5)   # 11.5 pt shift, same verdict
    changed, reason = check_verdict_changed(initial, final)
    assert changed is True
    assert reason is not None


def test_score_shift_exactly_10_returns_false():
    """Boundary: exactly 10 is NOT greater than 10."""
    initial = _make_cr("MIXED", 60.0)
    final   = _make_cr("MIXED", 70.0)   # exactly 10 pt
    changed, reason = check_verdict_changed(initial, final)
    assert changed is False
    assert reason is None


def test_none_verdict_does_not_trigger_change():
    """If either side has None verdict, verdict-change should not fire."""
    initial = _make_cr(None, 50.0)
    final   = _make_cr("TRUE", 90.0)
    changed, reason = check_verdict_changed(initial, final)
    # Only score shift matters here (40 pts → should trigger score change)
    assert changed is True


def test_both_none_verdict_no_score_returns_false():
    initial = _make_cr(None, None)
    final   = _make_cr(None, None)
    changed, reason = check_verdict_changed(initial, final)
    assert changed is False
    assert reason is None


# ════════════════════════════════════════════════════════════════════════════
# _VERDICT_RANK sanity
# ════════════════════════════════════════════════════════════════════════════

def test_verdict_rank_values():
    assert _VERDICT_RANK["TRUE"] == 5
    assert _VERDICT_RANK["MOSTLY TRUE"] == 4
    assert _VERDICT_RANK["MIXED"] == 3
    assert _VERDICT_RANK["MOSTLY FALSE"] == 2
    assert _VERDICT_RANK["FALSE"] == 1
    assert _VERDICT_RANK["UNVERIFIABLE"] == 0


def test_verdict_rank_has_exactly_6_entries():
    assert len(_VERDICT_RANK) == 6


# ════════════════════════════════════════════════════════════════════════════
# Standalone runner (no pytest required)
# ════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import traceback

    test_functions = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = 0
    failed = 0

    for fn in test_functions:
        try:
            fn()
            print(f"  PASS  {fn.__name__}")
            passed += 1
        except Exception:
            print(f"  FAIL  {fn.__name__}")
            traceback.print_exc()
            failed += 1

    print(f"\n{passed} passed, {failed} failed")
    sys.exit(0 if failed == 0 else 1)
