"""
Tests for donor_cloud DiskCache.

Uses importlib to load modules, matching how the plugin system loads
code dynamically at runtime.
"""
import os
import sys
import importlib.util as _ilu
import tempfile
import time

# --- importlib load: donor_cloud_sources ------------------------------------
_PLUGIN_DIR = os.path.join(os.path.dirname(__file__), "..")
_SOURCES_DIR = os.path.join(_PLUGIN_DIR, "sources")

if "donor_cloud_sources" not in sys.modules:
    _src_spec = _ilu.spec_from_file_location(
        "donor_cloud_sources",
        os.path.join(_SOURCES_DIR, "__init__.py"),
    )
    _src_mod = _ilu.module_from_spec(_src_spec)
    sys.modules["donor_cloud_sources"] = _src_mod   # register BEFORE exec
    _src_spec.loader.exec_module(_src_mod)
else:
    _src_mod = sys.modules["donor_cloud_sources"]

# --- importlib load: donor_cloud_cache --------------------------------------
if "donor_cloud_cache" not in sys.modules:
    _cache_spec = _ilu.spec_from_file_location(
        "donor_cloud_cache",
        os.path.join(_PLUGIN_DIR, "cache.py"),
    )
    _cache_mod = _ilu.module_from_spec(_cache_spec)
    sys.modules["donor_cloud_cache"] = _cache_mod   # register BEFORE exec
    _cache_spec.loader.exec_module(_cache_mod)
else:
    _cache_mod = sys.modules["donor_cloud_cache"]

Candidate = _src_mod.Candidate
Contributor = _src_mod.Contributor
FinancialSummary = _src_mod.FinancialSummary
DiskCache = _cache_mod.DiskCache
# ---------------------------------------------------------------------------


def _make_candidate(source_id="fec", candidate_id="S123") -> Candidate:
    return Candidate(
        id=candidate_id,
        name="Jane Doe",
        party="DEM",
        office="Senate",
        state="UT",
        source_id=source_id,
    )


def _make_contributors() -> list:
    return [
        Contributor(
            name="ACME Corp",
            total=50_000.00,
            count=10,
            type="employer",
            employer_name="ACME Corp",
        ),
        Contributor(
            name="Alice Johnson",
            total=2_800.00,
            count=2,
            type="individual",
            employer_name="Self-Employed",
        ),
    ]


def _make_summary(source_id="fec", candidate_id="S123") -> FinancialSummary:
    return FinancialSummary(
        candidate="Jane Doe",
        candidate_id=candidate_id,
        cycle=2024,
        total_raised=1_000_000.00,
        total_spent=750_000.00,
        cash_on_hand=250_000.00,
        debt=0.00,
        party="DEM",
        state="UT",
        office="Senate",
        source_id=source_id,
    )


class TestDiskCacheSaveLoad:
    def test_save_and_load(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = DiskCache(tmp)
            candidate = _make_candidate()
            contributors = _make_contributors()
            summary = _make_summary()

            cache.save(candidate, contributors, summary)

            result = cache.load("fec", "S123")
            assert result is not None
            assert result["candidate"]["id"] == "S123"
            assert result["candidate"]["name"] == "Jane Doe"
            assert len(result["contributors"]) == 2
            assert result["contributors"][0]["name"] == "ACME Corp"
            assert result["summary"]["total_raised"] == 1_000_000.00
            assert "last_updated" in result

    def test_save_with_none_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = DiskCache(tmp)
            candidate = _make_candidate()
            contributors = _make_contributors()

            cache.save(candidate, contributors, None)

            result = cache.load("fec", "S123")
            assert result is not None
            assert result["summary"] is None
            assert result["candidate"]["id"] == "S123"

    def test_load_missing_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = DiskCache(tmp)
            result = cache.load("fec", "DOESNOTEXIST")
            assert result is None

    def test_load_corrupt_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = DiskCache(tmp)
            # Write a corrupt JSON file directly
            corrupt_path = os.path.join(tmp, "fec_CORRUPT.json")
            with open(corrupt_path, "w") as f:
                f.write("{ this is not valid json }")
            result = cache.load("fec", "CORRUPT")
            assert result is None


class TestDiskCacheStaleness:
    def test_is_stale_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = DiskCache(tmp, staleness_hours=24.0)
            assert cache.is_stale("fec", "NONEXISTENT") is True

    def test_is_stale_zero_hours(self):
        """With 0 hours staleness, data is immediately stale after saving."""
        with tempfile.TemporaryDirectory() as tmp:
            cache = DiskCache(tmp, staleness_hours=0.0)
            candidate = _make_candidate()
            cache.save(candidate, [], None)
            # Even freshly saved, 0 hours means it's already stale
            assert cache.is_stale("fec", "S123") is True

    def test_is_stale_24_hours_fresh(self):
        """With 24 hours staleness, freshly saved data is not stale."""
        with tempfile.TemporaryDirectory() as tmp:
            cache = DiskCache(tmp, staleness_hours=24.0)
            candidate = _make_candidate()
            cache.save(candidate, [], None)
            assert cache.is_stale("fec", "S123") is False

    def test_is_stale_no_timestamp(self):
        """A cache file with no last_updated field is treated as stale."""
        import json
        with tempfile.TemporaryDirectory() as tmp:
            cache = DiskCache(tmp, staleness_hours=24.0)
            # Write a file without last_updated
            path = os.path.join(tmp, "fec_S123.json")
            with open(path, "w") as f:
                json.dump({"candidate": {}, "contributors": [], "summary": None}, f)
            assert cache.is_stale("fec", "S123") is True


class TestDiskCacheLoadAll:
    def test_load_all_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = DiskCache(tmp)
            assert cache.load_all() == []

    def test_load_all_multiple_entries(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = DiskCache(tmp)

            for cid in ("S001", "S002", "H003"):
                candidate = _make_candidate(candidate_id=cid)
                summary = _make_summary(candidate_id=cid)
                cache.save(candidate, _make_contributors(), summary)

            results = cache.load_all()
            assert len(results) == 3
            ids = {r["candidate"]["id"] for r in results}
            assert ids == {"S001", "S002", "H003"}

    def test_load_all_ignores_non_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = DiskCache(tmp)
            # Put a non-JSON file in the directory
            with open(os.path.join(tmp, "README.txt"), "w") as f:
                f.write("not json")
            candidate = _make_candidate()
            cache.save(candidate, [], None)
            results = cache.load_all()
            assert len(results) == 1


class TestDiskCacheFilenamesSanitized:
    def test_hyphenated_id_safe_filename(self):
        """IDs like 'UT-S-12345' should produce safe filenames."""
        with tempfile.TemporaryDirectory() as tmp:
            cache = DiskCache(tmp)
            candidate = _make_candidate(source_id="utah", candidate_id="UT-S-12345")
            cache.save(candidate, [], None)

            # Verify the file was created with hyphens replaced
            files = os.listdir(tmp)
            json_files = [f for f in files if f.endswith(".json")]
            assert len(json_files) == 1
            # Should NOT contain hyphens in filename
            assert "-" not in json_files[0]
            # Should be loadable
            result = cache.load("utah", "UT-S-12345")
            assert result is not None
            assert result["candidate"]["id"] == "UT-S-12345"

    def test_special_chars_in_id(self):
        """IDs with spaces or slashes should be sanitized."""
        with tempfile.TemporaryDirectory() as tmp:
            cache = DiskCache(tmp)
            candidate = _make_candidate(source_id="test", candidate_id="ID 123/456")
            cache.save(candidate, [], None)

            result = cache.load("test", "ID 123/456")
            assert result is not None
            assert result["candidate"]["id"] == "ID 123/456"
