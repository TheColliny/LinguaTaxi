"""Internationalization helpers for the LinguaTaxi launcher."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

IS_WIN = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"

_strings: dict[str, str] = {}
_strings_en: dict[str, str] = {}


def _load_translations(lang_code: str, app_dir: Path | None = None) -> None:
    """Load translation strings for *lang_code*, with English fallback."""
    global _strings, _strings_en
    if app_dir is None:
        from linguataxi.launcher.app import APP_DIR
        app_dir = APP_DIR
    en_path = app_dir / "locales" / "en.json"
    if en_path.exists():
        _strings_en.update(json.loads(en_path.read_text(encoding="utf-8")))
    lang_path = app_dir / "locales" / f"{lang_code.lower()}.json"
    if lang_path.exists():
        _strings.update(json.loads(lang_path.read_text(encoding="utf-8")))
    else:
        _strings.update(_strings_en)


def _t(key: str, **kwargs: Any) -> str:
    """Translate a string key with optional ``{variable}`` substitution."""
    text = _strings.get(key) or _strings_en.get(key, key)
    if kwargs:
        for k, v in kwargs.items():
            text = text.replace(f"{{{k}}}", str(v))
    return text


def detect_os_language() -> str:
    """Detect the OS UI language and return a language code."""
    try:
        if IS_WIN:
            import ctypes

            lcid = ctypes.windll.kernel32.GetUserDefaultUILanguage()
            primary = lcid & 0x3FF
            lcid_map = {
                0x01: "AR", 0x02: "BG", 0x05: "CS", 0x06: "DA", 0x07: "DE",
                0x08: "EL", 0x09: "EN", 0x0A: "ES", 0x25: "ET", 0x0B: "FI",
                0x0C: "FR", 0x0E: "HU", 0x21: "ID", 0x10: "IT", 0x11: "JA",
                0x12: "KO", 0x27: "LT", 0x26: "LV", 0x14: "NB", 0x13: "NL",
                0x15: "PL", 0x16: "PT", 0x18: "RO", 0x19: "RU", 0x1B: "SK",
                0x24: "SL", 0x1D: "SV", 0x1F: "TR", 0x22: "UK", 0x04: "ZH",
            }
            return lcid_map.get(primary, "EN")
        elif IS_MAC:
            result = subprocess.check_output(
                ["defaults", "read", ".GlobalPreferences", "AppleLanguages"],
                text=True,
                timeout=5,
            )
            for line in result.splitlines():
                line = line.strip().strip('",() ')
                if len(line) >= 2 and line[0].isalpha():
                    return line[:2].upper()
            return "EN"
        else:
            lang = os.environ.get("LANG", "en_US.UTF-8")
            return lang[:2].upper()
    except Exception:
        logger.debug("OS language detection failed", exc_info=True)
        return "EN"


def load_language_list(app_dir: Path) -> dict[str, dict[str, Any]]:
    """Load language metadata from ``locales/languages.json``."""
    lpath = app_dir / "locales" / "languages.json"
    if lpath.exists():
        return json.loads(lpath.read_text(encoding="utf-8"))
    return {"EN": {"name": "English", "native": "English", "flag": "", "rtl": False}}
