"""
Tests for Brave Search helpers and OpenAI-compatible caller
(plugins/fact_checker/providers.py — Task 2 additions)

Run with:
    python -m pytest tests/test_brave_search.py -v
or:
    python tests/test_brave_search.py
"""

import sys
import os
from unittest.mock import patch, MagicMock

# ── sys.path fixup ────────────────────────────────────────────────────────
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_PLUGIN_DIR = os.path.join(_REPO_ROOT, "plugins", "fact_checker")
for _p in (_REPO_ROOT, _PLUGIN_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import importlib.util as _ilu

_spec = _ilu.spec_from_file_location(
    "providers",
    os.path.join(_PLUGIN_DIR, "providers.py"),
)
_mod = _ilu.module_from_spec(_spec)
sys.modules["providers"] = _mod
_spec.loader.exec_module(_mod)

ProviderResult = _mod.ProviderResult
format_search_snippets = _mod.format_search_snippets
_parse_verdict_json = _mod._parse_verdict_json
_error_result = _mod._error_result
_build_provider_result = _mod._build_provider_result
call_openai_compatible = _mod.call_openai_compatible
SYSTEM_PROMPT = _mod.SYSTEM_PROMPT


# ════════════════════════════════════════════════════════════════════════════
# format_search_snippets
# ════════════════════════════════════════════════════════════════════════════

def test_format_search_snippets_empty_returns_empty_string():
    assert format_search_snippets([]) == ""


def test_format_search_snippets_single_result():
    results = [{"url": "https://example.com", "title": "Example", "snippet": "Some text here."}]
    output = format_search_snippets(results)
    assert output == "[1] Example — Some text here."


def test_format_search_snippets_multiple_results_numbered():
    results = [
        {"url": "https://a.com", "title": "Alpha", "snippet": "First snippet."},
        {"url": "https://b.com", "title": "Beta", "snippet": "Second snippet."},
        {"url": "https://c.com", "title": "Gamma", "snippet": "Third snippet."},
    ]
    output = format_search_snippets(results)
    lines = output.split("\n")
    assert len(lines) == 3
    assert lines[0].startswith("[1]")
    assert lines[1].startswith("[2]")
    assert lines[2].startswith("[3]")
    assert "Alpha" in lines[0]
    assert "Beta" in lines[1]
    assert "Gamma" in lines[2]


def test_format_search_snippets_format_is_title_dash_snippet():
    results = [{"url": "https://x.com", "title": "My Title", "snippet": "My snippet text."}]
    output = format_search_snippets(results)
    assert "My Title — My snippet text." in output


def test_format_search_snippets_missing_fields_handled_gracefully():
    # Should not raise even with missing keys
    results = [{}]
    output = format_search_snippets(results)
    assert output == "[1]  — "


# ════════════════════════════════════════════════════════════════════════════
# _parse_verdict_json
# ════════════════════════════════════════════════════════════════════════════

_VALID_VERDICT = {
    "type": "fact_claim",
    "claim": "GDP grew 3% last year",
    "accuracy_score": 72,
    "verdict": "MOSTLY TRUE",
    "assessment": "Largely supported by official statistics.",
    "language_signals": "grew, 3%",
}

_VALID_JSON_STR = """\
{
  "type": "fact_claim",
  "claim": "GDP grew 3% last year",
  "accuracy_score": 72,
  "verdict": "MOSTLY TRUE",
  "assessment": "Largely supported by official statistics.",
  "language_signals": "grew, 3%"
}"""


def test_parse_verdict_json_valid_plain_json():
    result = _parse_verdict_json(_VALID_JSON_STR)
    assert result is not None
    assert result["verdict"] == "MOSTLY TRUE"
    assert result["accuracy_score"] == 72
    assert result["type"] == "fact_claim"


def test_parse_verdict_json_strips_json_fenced_markdown():
    fenced = "```json\n" + _VALID_JSON_STR + "\n```"
    result = _parse_verdict_json(fenced)
    assert result is not None
    assert result["verdict"] == "MOSTLY TRUE"


def test_parse_verdict_json_strips_plain_fenced_markdown():
    fenced = "```\n" + _VALID_JSON_STR + "\n```"
    result = _parse_verdict_json(fenced)
    assert result is not None
    assert result["claim"] == "GDP grew 3% last year"


def test_parse_verdict_json_handles_whitespace_around_json():
    padded = "   \n\n" + _VALID_JSON_STR + "\n\n   "
    result = _parse_verdict_json(padded)
    assert result is not None
    assert result["verdict"] == "MOSTLY TRUE"


def test_parse_verdict_json_returns_none_for_invalid_json():
    assert _parse_verdict_json("this is not json at all") is None


def test_parse_verdict_json_returns_none_for_empty_string():
    assert _parse_verdict_json("") is None


def test_parse_verdict_json_returns_none_for_json_array():
    # Must be a dict, not an array
    assert _parse_verdict_json('["a", "b"]') is None


def test_parse_verdict_json_opinion_with_nulls():
    raw = """{
  "type": "opinion",
  "claim": "Speaker believes taxes are too high",
  "accuracy_score": null,
  "verdict": null,
  "assessment": "Expresses a subjective value judgment.",
  "language_signals": "believe, too high"
}"""
    result = _parse_verdict_json(raw)
    assert result is not None
    assert result["type"] == "opinion"
    assert result["accuracy_score"] is None
    assert result["verdict"] is None


# ════════════════════════════════════════════════════════════════════════════
# _error_result
# ════════════════════════════════════════════════════════════════════════════

def test_error_result_returns_provider_result():
    r = _error_result("cerebras", "API timeout")
    assert isinstance(r, ProviderResult)


def test_error_result_has_correct_provider_id():
    r = _error_result("mistral", "some error")
    assert r.provider_id == "mistral"


def test_error_result_has_error_field_set():
    r = _error_result("cerebras", "API timeout")
    assert r.error == "API timeout"


def test_error_result_verdict_and_score_are_none():
    r = _error_result("openrouter", "HTTP 429")
    assert r.verdict is None
    assert r.accuracy_score is None
    assert r.assessment is None
    assert r.claim is None


def test_error_result_sources_is_empty_list():
    r = _error_result("github_models", "key missing")
    assert r.sources == []


def test_error_result_latency_is_zero():
    r = _error_result("huggingface", "timeout")
    assert r.latency_ms == 0


# ════════════════════════════════════════════════════════════════════════════
# _build_provider_result
# ════════════════════════════════════════════════════════════════════════════

def test_build_provider_result_populates_fields():
    sources = [{"url": "https://example.com", "title": "Source", "snippet": "Text"}]
    r = _build_provider_result("cerebras", _VALID_VERDICT, sources, 850)
    assert r.provider_id == "cerebras"
    assert r.verdict == "MOSTLY TRUE"
    assert r.accuracy_score == 72
    assert r.assessment == "Largely supported by official statistics."
    assert r.claim == "GDP grew 3% last year"
    assert r.language_signals == "grew, 3%"
    assert r.latency_ms == 850
    assert r.sources == sources
    assert r.error is None
    assert r.result_type == "fact_claim"


def test_build_provider_result_defaults_result_type_to_fact_claim_when_missing():
    d = dict(_VALID_VERDICT)
    del d["type"]
    r = _build_provider_result("mistral", d, [], 100)
    assert r.result_type == "fact_claim"


# ════════════════════════════════════════════════════════════════════════════
# call_openai_compatible — error path when API key missing
# ════════════════════════════════════════════════════════════════════════════

def test_call_openai_compatible_error_when_key_missing():
    settings = {
        "providers": {
            "cerebras": {"enabled": True, "api_key": ""},
        }
    }
    result = call_openai_compatible("cerebras", "GDP grew 3%", "", settings)
    assert isinstance(result, ProviderResult)
    assert result.error is not None
    assert "key" in result.error.lower() or "api" in result.error.lower()
    assert result.verdict is None


def test_call_openai_compatible_error_for_unknown_provider():
    result = call_openai_compatible("does_not_exist", "some claim", "", {})
    assert isinstance(result, ProviderResult)
    assert result.error is not None
    assert result.provider_id == "does_not_exist"


def test_call_openai_compatible_error_when_settings_empty():
    result = call_openai_compatible("mistral", "inflation is 9%", "", {})
    assert isinstance(result, ProviderResult)
    assert result.error is not None


# ════════════════════════════════════════════════════════════════════════════
# call_openai_compatible — success path (mocked HTTP)
# ════════════════════════════════════════════════════════════════════════════

_MOCK_RESPONSE_BODY = {
    "choices": [
        {
            "message": {
                "content": """{
  "type": "fact_claim",
  "claim": "Inflation hit 9%",
  "accuracy_score": 85,
  "verdict": "MOSTLY TRUE",
  "assessment": "CPI peaked at 9.1% in June 2022.",
  "language_signals": "hit, 9%"
}"""
            }
        }
    ]
}


def test_call_openai_compatible_success_with_mocked_response():
    settings = {
        "providers": {
            "cerebras": {"enabled": True, "api_key": "test-key-123"},
        }
    }
    mock_resp = MagicMock()
    mock_resp.raise_for_status.return_value = None
    mock_resp.json.return_value = _MOCK_RESPONSE_BODY

    with patch.object(_mod.requests, "post", return_value=mock_resp) as mock_post:
        result = call_openai_compatible("cerebras", "Inflation hit 9%", "", settings)

    assert isinstance(result, ProviderResult)
    assert result.error is None
    assert result.verdict == "MOSTLY TRUE"
    assert result.accuracy_score == 85
    assert result.claim == "Inflation hit 9%"
    assert result.provider_id == "cerebras"
    assert result.latency_ms >= 0


def test_call_openai_compatible_appends_search_context_to_user_message():
    settings = {
        "providers": {
            "mistral": {"enabled": True, "api_key": "test-key-456"},
        }
    }
    search_ctx = "[1] BLS Report — Inflation peaked at 9.1% in 2022."
    mock_resp = MagicMock()
    mock_resp.raise_for_status.return_value = None
    mock_resp.json.return_value = _MOCK_RESPONSE_BODY

    captured_payload = {}

    def capture_post(url, headers, json, timeout):
        captured_payload.update(json)
        return mock_resp

    with patch.object(_mod.requests, "post", side_effect=capture_post):
        call_openai_compatible("mistral", "Inflation hit 9%", search_ctx, settings)

    user_msg = captured_payload["messages"][1]["content"]
    assert "Web search results for context" in user_msg
    assert search_ctx in user_msg


def test_call_openai_compatible_no_search_context_omits_web_prefix():
    settings = {
        "providers": {
            "mistral": {"enabled": True, "api_key": "test-key-789"},
        }
    }
    mock_resp = MagicMock()
    mock_resp.raise_for_status.return_value = None
    mock_resp.json.return_value = _MOCK_RESPONSE_BODY

    captured_payload = {}

    def capture_post(url, headers, json, timeout):
        captured_payload.update(json)
        return mock_resp

    with patch.object(_mod.requests, "post", side_effect=capture_post):
        call_openai_compatible("mistral", "Inflation hit 9%", "", settings)

    user_msg = captured_payload["messages"][1]["content"]
    assert "Web search results" not in user_msg
    assert user_msg == "Inflation hit 9%"


def test_call_openai_compatible_uses_correct_model_id():
    settings = {
        "providers": {
            "github_models": {"enabled": True, "api_key": "gh-key-xyz"},
        }
    }
    mock_resp = MagicMock()
    mock_resp.raise_for_status.return_value = None
    mock_resp.json.return_value = _MOCK_RESPONSE_BODY

    captured_payload = {}

    def capture_post(url, headers, json, timeout):
        captured_payload.update(json)
        return mock_resp

    with patch.object(_mod.requests, "post", side_effect=capture_post):
        call_openai_compatible("github_models", "test claim", "", settings)

    assert captured_payload["model"] == "gpt-4.1-mini"


def test_call_openai_compatible_handles_http_error():
    import requests as req_lib

    settings = {
        "providers": {
            "cerebras": {"enabled": True, "api_key": "test-key"},
        }
    }
    mock_resp = MagicMock()
    mock_resp.status_code = 429
    mock_resp.text = "Too Many Requests"
    http_error = req_lib.exceptions.HTTPError(response=mock_resp)
    mock_resp.raise_for_status.side_effect = http_error

    with patch.object(_mod.requests, "post", return_value=mock_resp):
        result = call_openai_compatible("cerebras", "some claim", "", settings)

    assert result.error is not None
    assert result.verdict is None


def test_call_openai_compatible_handles_timeout():
    import requests as req_lib

    settings = {
        "providers": {
            "ovhcloud": {"enabled": True, "api_key": "test-key"},
        }
    }
    with patch.object(
        _mod.requests,
        "post",
        side_effect=req_lib.exceptions.Timeout("timed out"),
    ):
        result = call_openai_compatible("ovhcloud", "some claim", "", settings)

    assert result.error is not None
    assert "timed out" in result.error.lower() or "timeout" in result.error.lower()
    assert result.verdict is None


# ════════════════════════════════════════════════════════════════════════════
# SYSTEM_PROMPT sanity checks
# ════════════════════════════════════════════════════════════════════════════

def test_system_prompt_is_non_empty_string():
    assert isinstance(SYSTEM_PROMPT, str)
    assert len(SYSTEM_PROMPT) > 100


def test_system_prompt_contains_key_instructions():
    assert "fact_claim" in SYSTEM_PROMPT
    assert "opinion" in SYSTEM_PROMPT
    assert "ambiguous" in SYSTEM_PROMPT
    assert "JSON" in SYSTEM_PROMPT
    assert "verdict" in SYSTEM_PROMPT


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
