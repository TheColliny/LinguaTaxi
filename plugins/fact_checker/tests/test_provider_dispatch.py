"""
Tests for provider dispatch — verifies _CALLERS coverage and call_provider routing.

Run from the repo root:
    python -m pytest plugins/fact_checker/tests/test_provider_dispatch.py -v
"""

from __future__ import annotations

import sys
import os

# Ensure the plugin directory is importable without an installed package
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import providers as p


# ────────────────────────────────────────────────────────────────────────────
# 1. Every api_style in the registry must have a _CALLERS entry
# ────────────────────────────────────────────────────────────────────────────

def test_all_api_styles_have_callers():
    """Every distinct api_style used in PROVIDER_REGISTRY must map to a caller."""
    styles_in_registry = {cfg.api_style for cfg in p.PROVIDER_REGISTRY.values()}
    missing = styles_in_registry - set(p._CALLERS.keys())
    assert not missing, (
        f"api_styles in registry but not in _CALLERS: {sorted(missing)}"
    )


# ────────────────────────────────────────────────────────────────────────────
# 2. _CALLERS has exactly 6 entries with the expected keys
# ────────────────────────────────────────────────────────────────────────────

def test_callers_has_exactly_six_entries():
    """_CALLERS must contain exactly 6 entries."""
    assert len(p._CALLERS) == 6, (
        f"Expected 6 entries in _CALLERS, got {len(p._CALLERS)}: {list(p._CALLERS)}"
    )


def test_callers_keys_match_expected_api_styles():
    """_CALLERS must contain exactly the six known api_style keys."""
    expected = {"openai", "gemini", "anthropic", "cohere", "perplexity", "openai_native"}
    assert set(p._CALLERS.keys()) == expected, (
        f"Mismatch: got {set(p._CALLERS.keys())!r}, expected {expected!r}"
    )


def test_callers_values_are_callable():
    """Every value in _CALLERS must be callable."""
    for style, fn in p._CALLERS.items():
        assert callable(fn), f"_CALLERS[{style!r}] is not callable: {fn!r}"


# ────────────────────────────────────────────────────────────────────────────
# 3. call_provider returns an error ProviderResult for unknown provider
# ────────────────────────────────────────────────────────────────────────────

def test_call_provider_unknown_provider():
    """call_provider with an unknown provider_id must return an error result."""
    result = p.call_provider(
        provider_id="nonexistent_provider_xyz",
        claim="The sky is green.",
        search_context="",
        settings={},
    )
    assert isinstance(result, p.ProviderResult)
    assert result.error is not None
    assert "nonexistent_provider_xyz" in result.error or "Unknown" in result.error
    assert result.verdict is None
    assert result.accuracy_score is None


# ────────────────────────────────────────────────────────────────────────────
# 4. call_provider returns an error when API key is missing (no network hit)
# ────────────────────────────────────────────────────────────────────────────

_SAMPLE_SETTINGS_NO_KEYS: dict = {
    "providers": {
        pid: {"enabled": True, "api_key": ""}
        for pid in p.PROVIDER_REGISTRY
    }
}


def test_call_provider_missing_api_key_cerebras():
    """call_provider for 'cerebras' with empty API key must return error without network call."""
    result = p.call_provider(
        provider_id="cerebras",
        claim="Water boils at 100 degrees Celsius.",
        search_context="",
        settings=_SAMPLE_SETTINGS_NO_KEYS,
    )
    assert isinstance(result, p.ProviderResult)
    assert result.error is not None
    assert "key" in result.error.lower() or "configured" in result.error.lower()
    assert result.verdict is None


def test_call_provider_missing_api_key_gemini():
    """call_provider for 'gemini_flash_lite' with empty API key must return error."""
    result = p.call_provider(
        provider_id="gemini_flash_lite",
        claim="The Earth orbits the Sun.",
        search_context="",
        settings=_SAMPLE_SETTINGS_NO_KEYS,
    )
    assert isinstance(result, p.ProviderResult)
    assert result.error is not None
    assert result.verdict is None


def test_call_provider_missing_api_key_claude():
    """call_provider for 'claude_sonnet' with empty API key must return error."""
    result = p.call_provider(
        provider_id="claude_sonnet",
        claim="Pluto is a planet.",
        search_context="",
        settings=_SAMPLE_SETTINGS_NO_KEYS,
    )
    assert isinstance(result, p.ProviderResult)
    assert result.error is not None
    assert result.verdict is None


def test_call_provider_missing_api_key_cohere():
    """call_provider for 'cohere' with empty API key must return error."""
    result = p.call_provider(
        provider_id="cohere",
        claim="The Moon is made of cheese.",
        search_context="",
        settings=_SAMPLE_SETTINGS_NO_KEYS,
    )
    assert isinstance(result, p.ProviderResult)
    assert result.error is not None
    assert result.verdict is None


def test_call_provider_missing_api_key_perplexity():
    """call_provider for 'perplexity' with empty API key must return error."""
    result = p.call_provider(
        provider_id="perplexity",
        claim="Mount Everest is the tallest mountain.",
        search_context="",
        settings=_SAMPLE_SETTINGS_NO_KEYS,
    )
    assert isinstance(result, p.ProviderResult)
    assert result.error is not None
    assert result.verdict is None


def test_call_provider_missing_api_key_openai_native():
    """call_provider for 'openai_gpt55' with empty API key must return error."""
    result = p.call_provider(
        provider_id="openai_gpt55",
        claim="The Internet was invented in 1989.",
        search_context="",
        settings=_SAMPLE_SETTINGS_NO_KEYS,
    )
    assert isinstance(result, p.ProviderResult)
    assert result.error is not None
    assert result.verdict is None


# ────────────────────────────────────────────────────────────────────────────
# 5. Structural integrity — every registry entry has a valid api_style
# ────────────────────────────────────────────────────────────────────────────

def test_all_registry_providers_have_known_api_style():
    """Every ProviderConfig in PROVIDER_REGISTRY must declare a recognised api_style."""
    known_styles = set(p._CALLERS.keys())
    for pid, cfg in p.PROVIDER_REGISTRY.items():
        assert cfg.api_style in known_styles, (
            f"Provider {pid!r} has api_style={cfg.api_style!r} "
            f"which is not in _CALLERS: {sorted(known_styles)}"
        )


# ────────────────────────────────────────────────────────────────────────────
# 6. Caller functions all have the expected signature shape
# ────────────────────────────────────────────────────────────────────────────

def test_caller_functions_accept_four_positional_args():
    """All caller functions in _CALLERS must accept (provider_id, claim, search_context, settings)."""
    import inspect
    for style, fn in p._CALLERS.items():
        sig = inspect.signature(fn)
        params = list(sig.parameters.keys())
        assert len(params) == 4, (
            f"_CALLERS[{style!r}] = {fn.__name__} has {len(params)} params, expected 4: {params}"
        )
        expected_names = ["provider_id", "claim", "search_context", "settings"]
        assert params == expected_names, (
            f"_CALLERS[{style!r}] param names {params!r} != expected {expected_names!r}"
        )


# ────────────────────────────────────────────────────────────────────────────
# 7. _error_result produces a well-formed ProviderResult
# ────────────────────────────────────────────────────────────────────────────

def test_error_result_structure():
    """_error_result must return a ProviderResult with error set and nulled fields."""
    result = p._error_result("test_provider", "Something went wrong")
    assert isinstance(result, p.ProviderResult)
    assert result.provider_id == "test_provider"
    assert result.error == "Something went wrong"
    assert result.verdict is None
    assert result.accuracy_score is None
    assert result.assessment is None
    assert result.claim is None
    assert result.sources == []
    assert result.latency_ms == 0
