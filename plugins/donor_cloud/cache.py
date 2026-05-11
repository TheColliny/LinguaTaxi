"""
Persistent disk cache for donor cloud candidate data.

Stores each (source_id, candidate_id) pair as a JSON file in a configurable
cache directory. Provides staleness checking so callers can decide whether to
re-fetch data from the upstream source.
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import sys
import threading
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# Load donor_cloud_sources via importlib so this module works whether it is
# imported directly during tests or loaded by the plugin system.
# ---------------------------------------------------------------------------
_sources_dir = os.path.join(os.path.dirname(__file__), "sources")
if "donor_cloud_sources" not in sys.modules:
    _spec = importlib.util.spec_from_file_location(
        "donor_cloud_sources",
        os.path.join(_sources_dir, "__init__.py"),
    )
    _mod = importlib.util.module_from_spec(_spec)
    sys.modules["donor_cloud_sources"] = _mod
    _spec.loader.exec_module(_mod)
from donor_cloud_sources import Candidate, Contributor, FinancialSummary  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_filename(source_id: str, candidate_id: str) -> str:
    """Return a safe JSON filename for a (source_id, candidate_id) pair.

    All non-alphanumeric characters (including hyphens, spaces, slashes) in
    both components are replaced with underscores before joining with ``_``.

    Example:
        _safe_filename("utah", "UT-S-12345") -> "utah_UT_S_12345.json"
    """
    safe_source = re.sub(r"[^A-Za-z0-9]", "_", source_id)
    safe_candidate = re.sub(r"[^A-Za-z0-9]", "_", candidate_id)
    return f"{safe_source}_{safe_candidate}.json"


# ---------------------------------------------------------------------------
# DiskCache
# ---------------------------------------------------------------------------

class DiskCache:
    """Stores candidate donor data as JSON files on disk.

    Parameters
    ----------
    cache_dir:
        Directory where JSON cache files will be written.  Created
        automatically if it does not exist.
    staleness_hours:
        Age threshold in hours.  A cached entry whose ``last_updated``
        timestamp is *older than or equal to* this threshold is considered
        stale.  Default is 24.0 hours.
    """

    def __init__(self, cache_dir: str, staleness_hours: float = 24.0) -> None:
        self.cache_dir = cache_dir
        self.staleness_hours = staleness_hours
        os.makedirs(cache_dir, exist_ok=True)
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def save(
        self,
        candidate: Candidate,
        contributors: list[Contributor],
        summary: FinancialSummary | None,
    ) -> None:
        """Persist a candidate's data to disk.

        Writes a JSON file containing the candidate dict, contributor list,
        financial summary dict (or ``null``), and the current UTC timestamp.
        """
        payload = {
            "candidate": candidate.to_dict(),
            "contributors": [c.to_dict() for c in contributors],
            "summary": summary.to_dict() if summary is not None else None,
            "last_updated": datetime.now(timezone.utc).isoformat(),
        }
        filename = _safe_filename(candidate.source_id, candidate.id)
        path = os.path.join(self.cache_dir, filename)
        with self._lock:
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=2)

    def load(self, source_id: str, candidate_id: str) -> dict | None:
        """Read a cached entry from disk.

        Returns the parsed dict on success, or ``None`` if the file does not
        exist or cannot be parsed (corrupt JSON).
        """
        filename = _safe_filename(source_id, candidate_id)
        path = os.path.join(self.cache_dir, filename)
        with self._lock:
            if not os.path.isfile(path):
                return None
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    return json.load(fh)
            except (json.JSONDecodeError, OSError):
                return None

    def is_stale(self, source_id: str, candidate_id: str) -> bool:
        """Return ``True`` if the cached entry is missing, has no timestamp,
        or its age is >= ``staleness_hours``.

        A freshly saved entry with ``staleness_hours=0`` is immediately stale
        because its age (>= 0) equals the threshold.
        """
        data = self.load(source_id, candidate_id)
        if data is None:
            return True
        ts_str = data.get("last_updated")
        if not ts_str:
            return True
        try:
            saved_at = datetime.fromisoformat(ts_str)
        except ValueError:
            return True
        now = datetime.now(timezone.utc)
        age_hours = (now - saved_at).total_seconds() / 3600.0
        return age_hours >= self.staleness_hours

    def load_all(self) -> list[dict]:
        """Return all valid cached entries from the cache directory.

        Files that fail to parse are silently skipped.  Non-.json files are
        ignored entirely.
        """
        results: list[dict] = []
        with self._lock:
            try:
                entries = os.listdir(self.cache_dir)
            except OSError:
                return results
        for entry in entries:
            if not entry.endswith(".json"):
                continue
            path = os.path.join(self.cache_dir, entry)
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                results.append(data)
            except (json.JSONDecodeError, OSError):
                pass
        return results
