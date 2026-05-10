"""
LinguaTaxi — Media Bias Fact Check (MBFC) Data Loader

Loads the MBFC credibility database at startup and provides fast domain
lookup for source credibility scoring.

Data source: drmikecrowe/mbfcext browser extension (MIT licensed).
Bundled fallback in data/mbfc_sources.json; live update from GitHub on startup.
"""

import json
import logging
import threading
from pathlib import Path
from urllib.parse import urlparse

log = logging.getLogger("livecaption")

_MBFC_DATA: dict = {}       # domain -> raw entry
_loaded = False
_load_lock = threading.Lock()

_DATA_DIR = Path(__file__).parent / "data"
_BUNDLED_FILE = _DATA_DIR / "mbfc_sources.json"
_GITHUB_URL = (
    "https://raw.githubusercontent.com/drmikecrowe/mbfcext"
    "/master/docs/v2/csources.json"
)

# Map MBFC reporting codes to 0-100 credibility scores
RATING_SCORES = {
    "VH": 100,   # Very High
    "H":  75,    # High
    "MF": 55,    # Mostly Factual
    "M":  35,    # Mixed
    "L":  15,    # Low
    "VL":  5,    # Very Low
}

RATING_LABELS = {
    "VH": "Very High",
    "H":  "High",
    "MF": "Mostly Factual",
    "M":  "Mixed",
    "L":  "Low",
    "VL": "Very Low",
}

BIAS_LABELS = {
    "C":  "Least Biased",
    "LC": "Left-Center",
    "RC": "Right-Center",
    "L":  "Left",
    "R":  "Right",
    "FN": "Questionable",
    "CP": "Conspiracy-Pseudoscience",
    "PS": "Pro-Science",
    "S":  "Satire",
}

DEFAULT_THRESHOLD = 32


def ensure_loaded():
    """Load MBFC data if not already loaded. Thread-safe, called once."""
    global _loaded
    if _loaded:
        return
    with _load_lock:
        if _loaded:
            return
        _load_from_github()
        if not _MBFC_DATA:
            _load_from_bundled()
        _loaded = True
        log.info(f"MBFC data loaded: {len(_MBFC_DATA)} sources")


def _load_from_github():
    """Try fetching fresh MBFC data from GitHub."""
    global _MBFC_DATA
    try:
        import requests
        resp = requests.get(_GITHUB_URL, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, dict) and len(data) > 100:
            _MBFC_DATA = data
            # Update bundled copy for offline use
            try:
                _DATA_DIR.mkdir(parents=True, exist_ok=True)
                _BUNDLED_FILE.write_text(
                    json.dumps(data, separators=(",", ":")),
                    encoding="utf-8",
                )
            except Exception:
                pass
            log.info(f"MBFC data fetched from GitHub: {len(data)} entries")
    except Exception as e:
        log.warning(f"MBFC GitHub fetch failed, using bundled data: {e}")


def _load_from_bundled():
    """Load MBFC data from the bundled fallback file."""
    global _MBFC_DATA
    if not _BUNDLED_FILE.exists():
        log.error("MBFC bundled data file not found — source credibility disabled")
        return
    try:
        with open(_BUNDLED_FILE, encoding="utf-8") as f:
            data = json.load(f)
        _MBFC_DATA = data
        log.info(f"MBFC data loaded from bundled file: {len(data)} entries")
    except Exception as e:
        log.error(f"MBFC bundled data load failed: {e}")


def _extract_domain(url: str) -> str:
    """Extract clean domain from a URL, stripping www. prefix."""
    try:
        parsed = urlparse(url)
        domain = parsed.hostname or ""
        if domain.startswith("www."):
            domain = domain[4:]
        return domain.lower()
    except Exception:
        return ""


def lookup_domain(url: str) -> dict | None:
    """Look up a URL's domain in the MBFC database.

    Returns enriched dict with credibility score, or None if not found.
    Tries the full subdomain first, then falls back to base domain
    (e.g., politics.reuters.com -> reuters.com).
    """
    ensure_loaded()
    domain = _extract_domain(url)
    if not domain:
        return None

    # Try exact domain match (may include subdomain like "news.bbc.co.uk")
    entry = _MBFC_DATA.get(domain)

    # Fallback: strip first subdomain level and retry
    # e.g., "politics.reuters.com" -> "reuters.com"
    if not entry:
        parts = domain.split(".")
        if len(parts) > 2:
            base = ".".join(parts[-2:])
            entry = _MBFC_DATA.get(base)
            # Handle .co.uk, .com.au style TLDs
            if not entry and len(parts) > 3:
                base = ".".join(parts[-3:])
                entry = _MBFC_DATA.get(base)

    if not entry:
        return None

    reporting = entry.get("r", "")
    bias = entry.get("b", "")
    score = RATING_SCORES.get(reporting)

    return {
        "name": entry.get("n", domain),
        "domain": entry.get("d", domain),
        "bias": bias,
        "bias_label": BIAS_LABELS.get(bias, bias),
        "reporting": reporting,
        "reporting_label": RATING_LABELS.get(reporting, reporting),
        "credibility_score": score,
        "mbfc_url": f"https://mediabiasfactcheck.com/{entry.get('u', '')}/"
            if entry.get("u") else None,
    }


def is_loaded() -> bool:
    """Check if MBFC data has been loaded."""
    return _loaded


def source_count() -> int:
    """Return the number of loaded MBFC sources."""
    return len(_MBFC_DATA)
