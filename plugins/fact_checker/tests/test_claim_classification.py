"""
Tests for Stage 2: Claim Classification (providers.py classification functions).

Covers:
- _parse_classification_response: valid claim JSON, non-claim JSON, invalid JSON, missing key
- CLASSIFICATION_FALLBACK_ORDER: ordering and membership
- classify_claim: returns None when no providers have API keys
"""

import sys
import os

# Ensure the plugin root is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import json
import pytest

from providers import (
    CLASSIFICATION_FALLBACK_ORDER,
    _parse_classification_response,
    classify_claim,
)


# ─── _parse_classification_response ─────────────────────────────────────────

class TestParseClassificationResponse:
    def test_valid_claim_json(self):
        raw = json.dumps({
            "is_claim": True,
            "extracted_claim": "The unemployment rate is 3.5%",
            "search_query": "US unemployment rate 2024",
        })
        result = _parse_classification_response(raw)
        assert result is not None
        assert result["is_claim"] is True
        assert result["extracted_claim"] == "The unemployment rate is 3.5%"
        assert result["search_query"] == "US unemployment rate 2024"

    def test_non_claim_json(self):
        raw = json.dumps({
            "is_claim": False,
            "extracted_claim": None,
            "search_query": None,
        })
        result = _parse_classification_response(raw)
        assert result is not None
        assert result["is_claim"] is False
        assert result["extracted_claim"] is None
        assert result["search_query"] is None

    def test_invalid_json_returns_none(self):
        result = _parse_classification_response("not valid JSON {{{")
        assert result is None

    def test_missing_is_claim_key_returns_none(self):
        raw = json.dumps({
            "extracted_claim": "some claim",
            "search_query": "some query",
        })
        result = _parse_classification_response(raw)
        assert result is None

    def test_markdown_fenced_json_is_parsed(self):
        """_parse_verdict_json strips markdown fences — verify it works end-to-end."""
        raw = '```json\n{"is_claim": true, "extracted_claim": "claim", "search_query": "q"}\n```'
        result = _parse_classification_response(raw)
        assert result is not None
        assert result["is_claim"] is True

    def test_empty_string_extracted_claim_normalised_to_none(self):
        """Empty strings for extracted_claim should be normalised to None."""
        raw = json.dumps({
            "is_claim": False,
            "extracted_claim": "",
            "search_query": "",
        })
        result = _parse_classification_response(raw)
        assert result is not None
        assert result["extracted_claim"] is None
        assert result["search_query"] is None


# ─── CLASSIFICATION_FALLBACK_ORDER ──────────────────────────────────────────

class TestClassificationFallbackOrder:
    def test_starts_with_cerebras(self):
        assert CLASSIFICATION_FALLBACK_ORDER[0] == "cerebras"

    def test_contains_expected_providers(self):
        expected = {"cerebras", "github_models", "mistral", "openrouter", "gemini_flash_lite"}
        assert expected == set(CLASSIFICATION_FALLBACK_ORDER)

    def test_is_list(self):
        assert isinstance(CLASSIFICATION_FALLBACK_ORDER, list)

    def test_no_duplicates(self):
        assert len(CLASSIFICATION_FALLBACK_ORDER) == len(set(CLASSIFICATION_FALLBACK_ORDER))


# ─── classify_claim ──────────────────────────────────────────────────────────

class TestClassifyClaim:
    def test_returns_none_when_no_providers_have_keys(self):
        """With an empty settings dict no provider has an API key — must return None."""
        result = classify_claim("The sky is blue.", settings={})
        assert result is None

    def test_returns_none_with_keys_for_unknown_providers_only(self):
        """Keys for providers not in the fallback order produce no valid provider."""
        settings = {
            "providers": {
                "nonexistent_provider": {"api_key": "some-key"},
            }
        }
        result = classify_claim("Test statement.", settings=settings)
        assert result is None

    def test_preferred_provider_without_key_is_skipped(self):
        """preferred_provider with no key is gracefully skipped."""
        result = classify_claim(
            "The GDP grew by 2%.",
            settings={},
            preferred_provider="cerebras",
        )
        assert result is None
