"""
Tests for plugins/fact_checker/providers.py

Run with:
    python -m pytest tests/test_providers_registry.py -v
or:
    python tests/test_providers_registry.py
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

# Load providers.py via importlib (mirrors how the plugin system loads it).
# We register the module in sys.modules before exec so that @dataclass can
# resolve the module's __dict__ correctly (needed by Python 3.12+).
_spec = _ilu.spec_from_file_location(
    "providers",
    os.path.join(_PLUGIN_DIR, "providers.py"),
)
_mod = _ilu.module_from_spec(_spec)
sys.modules["providers"] = _mod
_spec.loader.exec_module(_mod)

ProviderConfig = _mod.ProviderConfig
ProviderResult = _mod.ProviderResult
PROVIDER_REGISTRY = _mod.PROVIDER_REGISTRY
get_provider_config = _mod.get_provider_config
get_enabled_providers = _mod.get_enabled_providers
get_provider_api_key = _mod.get_provider_api_key
get_provider_weight = _mod.get_provider_weight
get_brave_api_key = _mod.get_brave_api_key
needs_brave_search = _mod.needs_brave_search


# ════════════════════════════════════════════════════════════════════════════
# Registry completeness
# ════════════════════════════════════════════════════════════════════════════

def test_registry_has_exactly_16_providers():
    assert len(PROVIDER_REGISTRY) == 16, (
        f"Expected 16 providers, got {len(PROVIDER_REGISTRY)}: "
        f"{list(PROVIDER_REGISTRY.keys())}"
    )


def test_registry_has_8_free_and_8_paid():
    free = [p for p in PROVIDER_REGISTRY.values() if p.category == "free"]
    paid = [p for p in PROVIDER_REGISTRY.values() if p.category == "paid"]
    assert len(free) == 8, f"Expected 8 free providers, got {len(free)}: {[p.provider_id for p in free]}"
    assert len(paid) == 8, f"Expected 8 paid providers, got {len(paid)}: {[p.provider_id for p in paid]}"


def test_all_expected_provider_ids_present():
    expected = {
        # free
        "gemini_flash_lite", "cerebras", "mistral", "github_models",
        "cohere", "openrouter", "ovhcloud", "huggingface",
        # paid
        "claude_sonnet", "claude_opus", "perplexity", "openai_gpt55",
        "openai_gpt54_mini", "openai_gpt54_nano", "openai_gpt5_nano", "gemini_pro",
    }
    assert set(PROVIDER_REGISTRY.keys()) == expected


# ════════════════════════════════════════════════════════════════════════════
# Field validity for every provider
# ════════════════════════════════════════════════════════════════════════════

VALID_SEARCH_METHODS = {"native", "brave"}
VALID_SPEEDS = {"fast", "normal", "slow"}
VALID_CATEGORIES = {"free", "paid"}
VALID_API_STYLES = {"openai", "gemini", "anthropic", "cohere", "perplexity", "openai_native"}


def test_all_providers_have_valid_required_fields():
    errors = []
    for pid, cfg in PROVIDER_REGISTRY.items():
        # provider_id must match dict key
        if cfg.provider_id != pid:
            errors.append(f"{pid}: provider_id mismatch ({cfg.provider_id!r})")

        if not cfg.display_name:
            errors.append(f"{pid}: display_name is empty")

        if not cfg.model_id:
            errors.append(f"{pid}: model_id is empty")

        if not cfg.base_url.startswith("https://"):
            errors.append(f"{pid}: base_url invalid ({cfg.base_url!r})")

        if not (0.0 < cfg.default_weight <= 1.0):
            errors.append(f"{pid}: default_weight out of range ({cfg.default_weight})")

        if cfg.search_method not in VALID_SEARCH_METHODS:
            errors.append(f"{pid}: search_method invalid ({cfg.search_method!r})")

        if cfg.speed not in VALID_SPEEDS:
            errors.append(f"{pid}: speed invalid ({cfg.speed!r})")

        if cfg.category not in VALID_CATEGORIES:
            errors.append(f"{pid}: category invalid ({cfg.category!r})")

        if not cfg.auth_header:
            errors.append(f"{pid}: auth_header is empty")

        if cfg.api_style not in VALID_API_STYLES:
            errors.append(f"{pid}: api_style invalid ({cfg.api_style!r})")

        if cfg.timeout <= 0:
            errors.append(f"{pid}: timeout must be positive ({cfg.timeout})")

        if not cfg.signup_url.startswith("https://"):
            errors.append(f"{pid}: signup_url invalid ({cfg.signup_url!r})")

        if cfg.rate_limit_rpm < 0:
            errors.append(f"{pid}: rate_limit_rpm negative")

        if cfg.rate_limit_rpd < 0:
            errors.append(f"{pid}: rate_limit_rpd negative")

    assert not errors, "Provider field validation failed:\n" + "\n".join(errors)


def test_free_providers_have_empty_cost_info():
    for pid, cfg in PROVIDER_REGISTRY.items():
        if cfg.category == "free":
            assert cfg.cost_info == "", (
                f"{pid} is free but has non-empty cost_info: {cfg.cost_info!r}"
            )


def test_paid_providers_have_cost_info():
    for pid, cfg in PROVIDER_REGISTRY.items():
        if cfg.category == "paid":
            assert cfg.cost_info, f"{pid} is paid but cost_info is empty"


# ════════════════════════════════════════════════════════════════════════════
# get_provider_config
# ════════════════════════════════════════════════════════════════════════════

def test_get_provider_config_returns_correct_object():
    cfg = get_provider_config("gemini_flash_lite")
    assert cfg is not None
    assert cfg.provider_id == "gemini_flash_lite"
    assert cfg.category == "free"
    assert cfg.api_style == "gemini"


def test_get_provider_config_returns_none_for_unknown():
    assert get_provider_config("nonexistent_provider") is None
    assert get_provider_config("") is None
    assert get_provider_config("GEMINI_FLASH_LITE") is None  # case-sensitive


# ════════════════════════════════════════════════════════════════════════════
# get_enabled_providers
# ════════════════════════════════════════════════════════════════════════════

def test_get_enabled_providers_returns_only_enabled_with_key():
    settings = {
        "providers": {
            "gemini_flash_lite": {"enabled": True, "api_key": "key-abc"},
            "cerebras": {"enabled": True, "api_key": ""},      # key missing → excluded
            "mistral": {"enabled": False, "api_key": "key-xyz"},  # disabled → excluded
            "claude_sonnet": {"enabled": True, "api_key": "sk-ant-123"},
        }
    }
    enabled = get_enabled_providers(settings)
    enabled_ids = {p.provider_id for p in enabled}
    assert "gemini_flash_lite" in enabled_ids
    assert "claude_sonnet" in enabled_ids
    assert "cerebras" not in enabled_ids   # no key
    assert "mistral" not in enabled_ids    # disabled


def test_get_enabled_providers_empty_settings():
    result = get_enabled_providers({})
    assert result == []


def test_get_enabled_providers_returns_provider_config_objects():
    settings = {
        "providers": {
            "perplexity": {"enabled": True, "api_key": "pplx-key"},
        }
    }
    result = get_enabled_providers(settings)
    assert len(result) == 1
    assert isinstance(result[0], ProviderConfig)
    assert result[0].provider_id == "perplexity"


# ════════════════════════════════════════════════════════════════════════════
# get_provider_weight
# ════════════════════════════════════════════════════════════════════════════

def test_get_provider_weight_returns_default_when_no_custom():
    cfg = get_provider_config("claude_sonnet")
    weight = get_provider_weight("claude_sonnet", {})
    assert weight == cfg.default_weight


def test_get_provider_weight_uses_custom_weight_from_settings():
    settings = {"weights": {"claude_sonnet": 0.5}}
    weight = get_provider_weight("claude_sonnet", settings)
    assert weight == 0.5


def test_get_provider_weight_clamps_to_minimum():
    settings = {"weights": {"mistral": -5.0}}
    weight = get_provider_weight("mistral", settings)
    assert weight == 0.01


def test_get_provider_weight_clamps_to_maximum():
    settings = {"weights": {"mistral": 9.99}}
    weight = get_provider_weight("mistral", settings)
    assert weight == 1.0


def test_get_provider_weight_unknown_provider_returns_minimum():
    weight = get_provider_weight("does_not_exist", {})
    assert weight == 0.01


def test_get_provider_weight_invalid_custom_value_falls_back_to_default():
    settings = {"weights": {"cerebras": "not-a-number"}}
    weight = get_provider_weight("cerebras", settings)
    assert weight == get_provider_config("cerebras").default_weight


# ════════════════════════════════════════════════════════════════════════════
# get_provider_api_key
# ════════════════════════════════════════════════════════════════════════════

def test_get_provider_api_key_returns_key():
    settings = {"providers": {"claude_sonnet": {"api_key": "sk-ant-test"}}}
    assert get_provider_api_key("claude_sonnet", settings) == "sk-ant-test"


def test_get_provider_api_key_returns_empty_when_missing():
    assert get_provider_api_key("claude_sonnet", {}) == ""
    assert get_provider_api_key("nonexistent", {"providers": {}}) == ""


# ════════════════════════════════════════════════════════════════════════════
# get_brave_api_key
# ════════════════════════════════════════════════════════════════════════════

def test_get_brave_api_key_returns_key():
    settings = {"brave_api_key": "BSA-test-key"}
    assert get_brave_api_key(settings) == "BSA-test-key"


def test_get_brave_api_key_returns_empty_when_missing():
    assert get_brave_api_key({}) == ""


# ════════════════════════════════════════════════════════════════════════════
# needs_brave_search
# ════════════════════════════════════════════════════════════════════════════

def test_needs_brave_search_true_for_brave_providers():
    assert needs_brave_search("cerebras") is True
    assert needs_brave_search("mistral") is True
    assert needs_brave_search("claude_sonnet") is True
    assert needs_brave_search("huggingface") is True


def test_needs_brave_search_false_for_native_providers():
    assert needs_brave_search("gemini_flash_lite") is False
    assert needs_brave_search("cohere") is False
    assert needs_brave_search("perplexity") is False
    assert needs_brave_search("gemini_pro") is False


def test_needs_brave_search_false_for_unknown_provider():
    assert needs_brave_search("unknown_provider") is False


# ════════════════════════════════════════════════════════════════════════════
# ProviderResult construction
# ════════════════════════════════════════════════════════════════════════════

def test_provider_result_can_be_created_with_minimal_args():
    result = ProviderResult(
        provider_id="gemini_flash_lite",
        verdict="TRUE",
        accuracy_score=92.0,
        assessment="The claim is well supported.",
        claim="GDP grew 3% last year",
    )
    assert result.provider_id == "gemini_flash_lite"
    assert result.verdict == "TRUE"
    assert result.accuracy_score == 92.0
    assert result.sources == []
    assert result.error is None
    assert result.latency_ms == 0
    assert result.result_type == "fact_claim"


def test_provider_result_can_be_created_with_error():
    result = ProviderResult(
        provider_id="mistral",
        verdict=None,
        accuracy_score=None,
        assessment=None,
        claim=None,
        error="API timeout",
    )
    assert result.error == "API timeout"
    assert result.verdict is None


def test_provider_result_supports_all_fields():
    result = ProviderResult(
        provider_id="claude_sonnet",
        verdict="MOSTLY TRUE",
        accuracy_score=78.5,
        assessment="Mostly supported with minor caveats.",
        claim="Unemployment fell to 3.7%",
        sources=[{"url": "https://bls.gov/news", "title": "BLS Report"}],
        language_signals="fell, 3.7%",
        error=None,
        latency_ms=1240,
        result_type="fact_claim",
    )
    assert result.latency_ms == 1240
    assert len(result.sources) == 1
    assert result.language_signals == "fell, 3.7%"


# ════════════════════════════════════════════════════════════════════════════
# Specific provider value spot-checks
# ════════════════════════════════════════════════════════════════════════════

def test_gemini_flash_lite_config():
    cfg = get_provider_config("gemini_flash_lite")
    assert cfg.rate_limit_rpm == 15
    assert cfg.rate_limit_rpd == 500
    assert cfg.auth_prefix == ""   # key goes directly in header, no "Bearer "
    assert cfg.auth_header == "x-goog-api-key"


def test_claude_sonnet_config():
    cfg = get_provider_config("claude_sonnet")
    assert cfg.auth_header == "x-api-key"
    assert cfg.auth_prefix == ""
    assert cfg.api_style == "anthropic"
    assert cfg.default_weight == 0.95


def test_ovhcloud_has_extended_timeout():
    cfg = get_provider_config("ovhcloud")
    assert cfg.timeout == 30
    assert cfg.rate_limit_rpm == 2


def test_huggingface_has_extended_timeout():
    cfg = get_provider_config("huggingface")
    assert cfg.timeout == 30
    assert cfg.default_weight == 0.60


def test_openai_providers_share_base_url():
    openai_ids = ["openai_gpt55", "openai_gpt54_mini", "openai_gpt54_nano", "openai_gpt5_nano"]
    for pid in openai_ids:
        cfg = get_provider_config(pid)
        assert cfg.base_url == "https://api.openai.com/v1/chat/completions", (
            f"{pid} has unexpected base_url: {cfg.base_url}"
        )
        assert cfg.api_style == "openai_native"


def test_claude_providers_share_base_url_and_auth():
    for pid in ["claude_sonnet", "claude_opus"]:
        cfg = get_provider_config(pid)
        assert cfg.base_url == "https://api.anthropic.com/v1/messages"
        assert cfg.auth_header == "x-api-key"
        assert cfg.auth_prefix == ""


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
