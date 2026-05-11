"""
Donor Cloud source provider data models and registry.

Each source provider (FEC, Utah, etc.) implements BaseSource and registers
itself via register_source(). The plugin loads sources dynamically.
"""
from __future__ import annotations

import dataclasses
from abc import ABC, abstractmethod
from typing import Optional


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class Candidate:
    id: str
    name: str
    party: str
    office: str
    state: str
    source_id: str

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


@dataclasses.dataclass
class Contributor:
    name: str
    total: float
    count: int
    type: str          # "employer" | "individual" | etc.
    employer_name: str

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


@dataclasses.dataclass
class FinancialSummary:
    candidate: str
    candidate_id: str
    cycle: int
    total_raised: float
    total_spent: float
    cash_on_hand: float
    debt: float
    party: str
    state: str
    office: str
    source_id: str

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


# ---------------------------------------------------------------------------
# Abstract base source
# ---------------------------------------------------------------------------

class BaseSource(ABC):
    """Abstract base class all source providers must implement."""

    source_id: str       # unique machine-readable ID, e.g. "fec" or "utah"
    display_name: str    # human-readable label shown in UI

    @abstractmethod
    def search(self, name: str, year: int) -> list[Candidate]:
        """Search for candidates by name and election year."""
        ...

    @abstractmethod
    def fetch_contributors(
        self,
        candidate_id: str,
        year: int,
        view: str = "employer",
    ) -> list[Contributor]:
        """Fetch contributor list for a candidate."""
        ...

    @abstractmethod
    def fetch_summary(
        self,
        candidate_id: str,
        year: int,
    ) -> Optional[FinancialSummary]:
        """Fetch financial summary for a candidate, or None if not found."""
        ...


# ---------------------------------------------------------------------------
# Source registry
# ---------------------------------------------------------------------------

SOURCE_REGISTRY: dict[str, BaseSource] = {}


def register_source(source: BaseSource) -> None:
    """Register a source provider instance by its source_id."""
    SOURCE_REGISTRY[source.source_id] = source


def get_source(source_id: str) -> Optional[BaseSource]:
    """Return a registered source by ID, or None if not found."""
    return SOURCE_REGISTRY.get(source_id)


def get_all_sources() -> list[BaseSource]:
    """Return all registered source provider instances."""
    return list(SOURCE_REGISTRY.values())
