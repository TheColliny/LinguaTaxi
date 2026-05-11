"""
Tests for the FECSource provider.

Uses importlib to load modules, matching how the plugin system loads sources
dynamically at runtime.
"""
from __future__ import annotations

import asyncio
import os
import sys
import importlib.util as _ilu
from unittest.mock import patch, MagicMock


# ---------------------------------------------------------------------------
# Load donor_cloud_sources (BaseSource, Candidate, etc.) via importlib
# ---------------------------------------------------------------------------
_SOURCES_DIR = os.path.join(os.path.dirname(__file__), "..", "sources")

if "donor_cloud_sources" not in sys.modules:
    _spec = _ilu.spec_from_file_location(
        "donor_cloud_sources",
        os.path.join(_SOURCES_DIR, "__init__.py"),
    )
    _mod = _ilu.module_from_spec(_spec)
    sys.modules["donor_cloud_sources"] = _mod
    _spec.loader.exec_module(_mod)

_sources_mod = sys.modules["donor_cloud_sources"]
Candidate = _sources_mod.Candidate
Contributor = _sources_mod.Contributor
FinancialSummary = _sources_mod.FinancialSummary
BaseSource = _sources_mod.BaseSource
SOURCE_REGISTRY = _sources_mod.SOURCE_REGISTRY


# ---------------------------------------------------------------------------
# Load FECSource via importlib
# ---------------------------------------------------------------------------
_FEC_PATH = os.path.join(_SOURCES_DIR, "fec.py")

# Remove cached module if present (allows re-running in same process cleanly)
if "donor_cloud_sources.fec" in sys.modules:
    del sys.modules["donor_cloud_sources.fec"]

_fec_spec = _ilu.spec_from_file_location("donor_cloud_sources.fec", _FEC_PATH)
_fec_mod = _ilu.module_from_spec(_fec_spec)
sys.modules["donor_cloud_sources.fec"] = _fec_mod
_fec_spec.loader.exec_module(_fec_mod)

FECSource = _fec_mod.FECSource


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run(coro):
    """Run an async coroutine in a synchronous test."""
    return asyncio.run(coro)


def _make_fec_instance() -> FECSource:
    """Return a fresh FECSource with an empty cache."""
    src = FECSource()
    src.clear_cache()
    return src


# ---------------------------------------------------------------------------
# Tests: identity
# ---------------------------------------------------------------------------

class TestFECSourceIdentity:
    def test_source_id(self):
        src = FECSource()
        assert src.source_id == "fec"

    def test_display_name(self):
        src = FECSource()
        assert src.display_name == "FEC"

    def test_is_base_source(self):
        src = FECSource()
        assert isinstance(src, BaseSource)


# ---------------------------------------------------------------------------
# Tests: _format_name
# ---------------------------------------------------------------------------

class TestFormatName:
    def test_last_comma_first(self):
        src = FECSource()
        result = src._format_name("SMITH, JOHN")
        assert result == "John Smith"

    def test_last_comma_first_with_middle(self):
        src = FECSource()
        result = src._format_name("DOE, JANE MARIE")
        assert result == "Jane Marie Doe"

    def test_no_comma_titlecases(self):
        src = FECSource()
        result = src._format_name("JOHN SMITH")
        assert result == "John Smith"

    def test_already_titled(self):
        src = FECSource()
        result = src._format_name("Jane Doe")
        assert result == "Jane Doe"

    def test_empty_string(self):
        src = FECSource()
        result = src._format_name("")
        assert result == ""


# ---------------------------------------------------------------------------
# Tests: _should_skip_employer
# ---------------------------------------------------------------------------

class TestShouldSkipEmployer:
    def test_retired_is_skipped(self):
        src = FECSource()
        assert src._should_skip_employer("RETIRED") is True

    def test_na_is_skipped(self):
        src = FECSource()
        assert src._should_skip_employer("N/A") is True

    def test_not_employed_is_skipped(self):
        src = FECSource()
        assert src._should_skip_employer("NOT EMPLOYED") is True

    def test_self_employed_is_skipped(self):
        src = FECSource()
        assert src._should_skip_employer("SELF-EMPLOYED") is True

    def test_empty_string_is_skipped(self):
        src = FECSource()
        assert src._should_skip_employer("") is True

    def test_google_is_not_skipped(self):
        src = FECSource()
        assert src._should_skip_employer("Google") is False

    def test_real_company_not_skipped(self):
        src = FECSource()
        assert src._should_skip_employer("MICROSOFT CORPORATION") is False

    def test_none_skipped_is_skipped(self):
        src = FECSource()
        assert src._should_skip_employer("NONE") is True

    def test_homemaker_is_skipped(self):
        src = FECSource()
        assert src._should_skip_employer("HOMEMAKER") is True


# ---------------------------------------------------------------------------
# Tests: search (mocked HTTP)
# ---------------------------------------------------------------------------

_MOCK_SEARCH_RESPONSE = {
    "results": [
        {
            "candidate_id": "S0UT00058",
            "name": "LEE, MIKE",
            "party_full": "Republican Party",
            "party": "REP",
            "state": "UT",
            "office_full": "Senate",
            "office": "S",
        },
        {
            "candidate_id": "H4UT02019",
            "name": "CURTIS, JOHN",
            "party_full": "Republican Party",
            "party": "REP",
            "state": "UT",
            "office_full": "House",
            "office": "H",
        },
    ]
}


class TestFECSearch:
    def test_search_returns_candidates(self):
        src = _make_fec_instance()

        mock_resp = MagicMock()
        mock_resp.json.return_value = _MOCK_SEARCH_RESPONSE
        mock_resp.raise_for_status.return_value = None

        with patch("requests.get", return_value=mock_resp):
            candidates = _run(src.search("mike lee", 2024))

        assert isinstance(candidates, list)
        assert len(candidates) == 2

    def test_search_returns_candidate_objects(self):
        src = _make_fec_instance()

        mock_resp = MagicMock()
        mock_resp.json.return_value = _MOCK_SEARCH_RESPONSE
        mock_resp.raise_for_status.return_value = None

        with patch("requests.get", return_value=mock_resp):
            candidates = _run(src.search("mike lee", 2024))

        for c in candidates:
            assert isinstance(c, Candidate)

    def test_search_formats_names(self):
        src = _make_fec_instance()

        mock_resp = MagicMock()
        mock_resp.json.return_value = _MOCK_SEARCH_RESPONSE
        mock_resp.raise_for_status.return_value = None

        with patch("requests.get", return_value=mock_resp):
            candidates = _run(src.search("lee", 2024))

        names = [c.name for c in candidates]
        assert "Mike Lee" in names
        assert "John Curtis" in names

    def test_search_sets_source_id(self):
        src = _make_fec_instance()

        mock_resp = MagicMock()
        mock_resp.json.return_value = _MOCK_SEARCH_RESPONSE
        mock_resp.raise_for_status.return_value = None

        with patch("requests.get", return_value=mock_resp):
            candidates = _run(src.search("lee", 2024))

        for c in candidates:
            assert c.source_id == "fec"

    def test_search_returns_empty_on_api_failure(self):
        src = _make_fec_instance()

        with patch("requests.get", side_effect=Exception("connection error")):
            candidates = _run(src.search("nobody", 2024))

        assert candidates == []

    def test_search_returns_empty_on_empty_results(self):
        src = _make_fec_instance()

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"results": []}
        mock_resp.raise_for_status.return_value = None

        with patch("requests.get", return_value=mock_resp):
            candidates = _run(src.search("xyzzy", 2024))

        assert candidates == []

    def test_search_skips_entries_missing_id(self):
        src = _make_fec_instance()

        bad_data = {
            "results": [
                # missing candidate_id
                {"name": "SMITH, BOB", "party": "REP", "state": "CA"},
                # valid entry
                {"candidate_id": "H1CA01000", "name": "DOE, JANE",
                 "party": "DEM", "state": "CA", "office_full": "House"},
            ]
        }

        mock_resp = MagicMock()
        mock_resp.json.return_value = bad_data
        mock_resp.raise_for_status.return_value = None

        with patch("requests.get", return_value=mock_resp):
            candidates = _run(src.search("smith doe", 2024))

        assert len(candidates) == 1
        assert candidates[0].id == "H1CA01000"


# ---------------------------------------------------------------------------
# Tests: clear_cache
# ---------------------------------------------------------------------------

class TestClearCache:
    def test_clear_cache_empties_internal_cache(self):
        src = _make_fec_instance()

        mock_resp = MagicMock()
        mock_resp.json.return_value = _MOCK_SEARCH_RESPONSE
        mock_resp.raise_for_status.return_value = None

        with patch("requests.get", return_value=mock_resp):
            _run(src.search("lee", 2024))

        src.clear_cache()
        # Should not raise; internal _cache should be empty (no assertion needed
        # beyond the method not failing, but we can verify by checking the
        # underlying attribute if accessible).
        if hasattr(src, "_cache"):
            assert src._cache == {}


# ---------------------------------------------------------------------------
# Tests: registration
# ---------------------------------------------------------------------------

class TestFECRegistration:
    def test_fec_registered_in_source_registry(self):
        """Module-level register_source() call should have put "fec" in the
        SOURCE_REGISTRY when fec.py was imported above."""
        assert "fec" in SOURCE_REGISTRY

    def test_registered_instance_is_fec_source(self):
        assert isinstance(SOURCE_REGISTRY.get("fec"), FECSource)
