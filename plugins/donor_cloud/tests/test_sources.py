"""
Tests for donor_cloud source provider data models and registry.

Uses importlib to load the module, matching how the plugin system loads
sources dynamically at runtime.
"""
import os
import sys
import importlib.util as _ilu
from typing import Optional

# --- importlib load (required: plugins are not on sys.path) -----------------
_SOURCES_DIR = os.path.join(os.path.dirname(__file__), "..", "sources")
_spec = _ilu.spec_from_file_location(
    "donor_cloud_sources",
    os.path.join(_SOURCES_DIR, "__init__.py"),
)
_mod = _ilu.module_from_spec(_spec)
sys.modules["donor_cloud_sources"] = _mod   # register BEFORE exec (Py 3.14+)
_spec.loader.exec_module(_mod)

# Pull names into test module scope for readability
Candidate = _mod.Candidate
Contributor = _mod.Contributor
FinancialSummary = _mod.FinancialSummary
BaseSource = _mod.BaseSource
register_source = _mod.register_source
get_source = _mod.get_source
get_all_sources = _mod.get_all_sources
SOURCE_REGISTRY = _mod.SOURCE_REGISTRY
# ---------------------------------------------------------------------------


class TestCandidateModel:
    def test_creation(self):
        c = Candidate(
            id="S123",
            name="Jane Doe",
            party="DEM",
            office="Senate",
            state="UT",
            source_id="fec",
        )
        assert c.id == "S123"
        assert c.name == "Jane Doe"
        assert c.party == "DEM"
        assert c.office == "Senate"
        assert c.state == "UT"
        assert c.source_id == "fec"

    def test_to_dict(self):
        c = Candidate(
            id="H456",
            name="John Smith",
            party="REP",
            office="House",
            state="CA",
            source_id="fec",
        )
        d = c.to_dict()
        assert isinstance(d, dict)
        assert d["id"] == "H456"
        assert d["name"] == "John Smith"
        assert d["party"] == "REP"
        assert d["office"] == "House"
        assert d["state"] == "CA"
        assert d["source_id"] == "fec"


class TestContributorModel:
    def test_employer_type(self):
        c = Contributor(
            name="ACME Corp",
            total=50000.00,
            count=10,
            type="employer",
            employer_name="ACME Corp",
        )
        assert c.name == "ACME Corp"
        assert c.total == 50000.00
        assert c.count == 10
        assert c.type == "employer"
        assert c.employer_name == "ACME Corp"

    def test_individual_type(self):
        c = Contributor(
            name="Alice Johnson",
            total=2800.00,
            count=2,
            type="individual",
            employer_name="Self-Employed",
        )
        assert c.type == "individual"
        assert c.name == "Alice Johnson"
        assert c.total == 2800.00
        assert c.count == 2

    def test_to_dict(self):
        c = Contributor(
            name="Big Corp",
            total=100000.00,
            count=20,
            type="employer",
            employer_name="Big Corp",
        )
        d = c.to_dict()
        assert isinstance(d, dict)
        assert d["name"] == "Big Corp"
        assert d["total"] == 100000.00
        assert d["count"] == 20
        assert d["type"] == "employer"
        assert d["employer_name"] == "Big Corp"


class TestFinancialSummaryModel:
    def test_creation(self):
        fs = FinancialSummary(
            candidate="Jane Doe",
            candidate_id="S123",
            cycle=2024,
            total_raised=1_000_000.00,
            total_spent=750_000.00,
            cash_on_hand=250_000.00,
            debt=0.00,
            party="DEM",
            state="UT",
            office="Senate",
            source_id="fec",
        )
        assert fs.candidate == "Jane Doe"
        assert fs.candidate_id == "S123"
        assert fs.cycle == 2024
        assert fs.total_raised == 1_000_000.00
        assert fs.total_spent == 750_000.00
        assert fs.cash_on_hand == 250_000.00
        assert fs.debt == 0.00
        assert fs.party == "DEM"
        assert fs.state == "UT"
        assert fs.office == "Senate"
        assert fs.source_id == "fec"

    def test_to_dict(self):
        fs = FinancialSummary(
            candidate="John Smith",
            candidate_id="H456",
            cycle=2022,
            total_raised=500_000.00,
            total_spent=480_000.00,
            cash_on_hand=20_000.00,
            debt=5_000.00,
            party="REP",
            state="CA",
            office="House",
            source_id="fec",
        )
        d = fs.to_dict()
        assert isinstance(d, dict)
        assert d["candidate_id"] == "H456"
        assert d["cycle"] == 2022
        assert d["source_id"] == "fec"


class TestRegistry:
    def test_registry_starts_empty_or_clean(self):
        # Clear any state from previous test runs within this process
        SOURCE_REGISTRY.clear()
        assert get_all_sources() == []

    def test_get_source_returns_none_for_missing(self):
        SOURCE_REGISTRY.clear()
        result = get_source("nonexistent_source")
        assert result is None

    def test_register_and_retrieve_source(self):
        SOURCE_REGISTRY.clear()

        class MockSource(BaseSource):
            source_id = "mock"
            display_name = "Mock Source"

            def search(self, name: str, year: int):
                return []

            def fetch_contributors(self, candidate_id: str, year: int, view: str = "employer"):
                return []

            def fetch_summary(self, candidate_id: str, year: int):
                return None

        mock = MockSource()
        register_source(mock)

        retrieved = get_source("mock")
        assert retrieved is mock

        all_sources = get_all_sources()
        assert len(all_sources) == 1
        assert all_sources[0] is mock

        # Cleanup
        SOURCE_REGISTRY.clear()

    def test_register_multiple_sources(self):
        SOURCE_REGISTRY.clear()

        class SourceA(BaseSource):
            source_id = "src_a"
            display_name = "Source A"
            def search(self, name, year): return []
            def fetch_contributors(self, cid, year, view="employer"): return []
            def fetch_summary(self, cid, year): return None

        class SourceB(BaseSource):
            source_id = "src_b"
            display_name = "Source B"
            def search(self, name, year): return []
            def fetch_contributors(self, cid, year, view="employer"): return []
            def fetch_summary(self, cid, year): return None

        register_source(SourceA())
        register_source(SourceB())

        assert get_source("src_a") is not None
        assert get_source("src_b") is not None
        assert len(get_all_sources()) == 2

        # Cleanup
        SOURCE_REGISTRY.clear()
