"""Shared constants, language maps, and default configuration."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

# ── Platform ──

IS_WIN: bool = sys.platform == "win32"
IS_MAC: bool = sys.platform == "darwin"

# ── App Identity ──

APP_NAME: str = "LinguaTaxi"
APP_FULL: str = "LinguaTaxi \u2014 Live Caption & Translation"
VERSION: str = "1.0.3b"
GITHUB_REPO: str = "TheColliny/LinguaTaxi"

# ── App Directory ──

if os.environ.get("LINGUATAXI_APP_DIR"):
    APP_DIR: Path = Path(os.environ["LINGUATAXI_APP_DIR"])
elif getattr(sys, "frozen", False):
    APP_DIR = Path(sys.executable).parent
else:
    APP_DIR = Path(__file__).resolve().parent.parent  # up from linguataxi/

# ── Network Ports ──

DISPLAY_PORT: int = 3000
OPERATOR_PORT: int = 3001
EXTENDED_PORT: int = 3002
DICTATION_PORT: int = 3005

# ── Audio ──

SAMPLE_RATE: int = 16000
CHANNELS: int = 1
DTYPE: str = "float32"
CHUNK_DURATION: float = 0.5
SILENCE_THRESHOLD: float = 0.008
SILENCE_DURATION: float = 0.7
MAX_SEGMENT_DURATION: int = 8
INTERIM_INTERVAL: float = 1.5
MIN_SPEECH_DURATION: float = 0.3

# ── Dictation ──

GRACE_MS: int = 750

# ── DeepL Language Maps ──

DEEPL_SOURCE_LANGS: dict[str, str] = {
    "AR": "Arabic", "BG": "Bulgarian", "CS": "Czech", "DA": "Danish", "DE": "German",
    "EL": "Greek", "EN": "English", "ES": "Spanish", "ET": "Estonian", "FI": "Finnish",
    "FR": "French", "HU": "Hungarian", "ID": "Indonesian", "IT": "Italian", "JA": "Japanese",
    "KO": "Korean", "LT": "Lithuanian", "LV": "Latvian", "NB": "Norwegian", "NL": "Dutch",
    "PL": "Polish", "PT": "Portuguese", "RO": "Romanian", "RU": "Russian", "SK": "Slovak",
    "SL": "Slovenian", "SV": "Swedish", "TR": "Turkish", "UK": "Ukrainian", "ZH": "Chinese",
}

DEEPL_TARGET_LANGS: dict[str, str] = {
    "AR": "Arabic", "BG": "Bulgarian", "CS": "Czech", "DA": "Danish", "DE": "German",
    "EL": "Greek", "EN-GB": "English (UK)", "EN-US": "English (US)", "ES": "Spanish",
    "ET": "Estonian", "FI": "Finnish", "FR": "French", "HU": "Hungarian", "ID": "Indonesian",
    "IT": "Italian", "JA": "Japanese", "KO": "Korean", "LT": "Lithuanian", "LV": "Latvian",
    "NB": "Norwegian", "NL": "Dutch", "PL": "Polish", "PT-BR": "Portuguese (BR)",
    "PT-PT": "Portuguese (PT)", "RO": "Romanian", "RU": "Russian", "SK": "Slovak",
    "SL": "Slovenian", "SV": "Swedish", "TR": "Turkish", "UK": "Ukrainian",
    "ZH-HANS": "Chinese (Simplified)", "ZH-HANT": "Chinese (Traditional)",
}

DEEPL_TO_WHISPER: dict[str, str] = {
    "AR": "ar", "BG": "bg", "CS": "cs", "DA": "da", "DE": "de", "EL": "el", "EN": "en",
    "ES": "es", "ET": "et", "FI": "fi", "FR": "fr", "HU": "hu", "ID": "id", "IT": "it",
    "JA": "ja", "KO": "ko", "LT": "lt", "LV": "lv", "NB": "no", "NL": "nl", "PL": "pl",
    "PT": "pt", "RO": "ro", "RU": "ru", "SK": "sk", "SL": "sl", "SV": "sv", "TR": "tr",
    "UK": "uk", "ZH": "zh",
}

DEEPL_TARGET_DEFAULTS: dict[str, str] = {
    "EN": "EN-US", "PT": "PT-BR", "ZH": "ZH-HANS",
}

# ── Vosk Language Map ──

VOSK_DIR_LANGS: dict[str, str] = {
    "en-us": "en", "en-in": "en", "de": "de", "fr": "fr", "es": "es",
    "ru": "ru", "it": "it", "ja": "ja", "cn": "zh", "zh": "zh",
    "ar": "ar", "pt": "pt", "tr": "tr", "ko": "ko", "nl": "nl",
    "uk": "uk", "pl": "pl", "hi": "hi", "fa": "fa", "ca": "ca",
}

# ── Batch Translation Language Maps (launcher) ──

BATCH_DEEPL_LANGS: dict[str, str] = {
    "AR": "Arabic", "BG": "Bulgarian", "CS": "Czech", "DA": "Danish",
    "DE": "German", "EL": "Greek", "EN-GB": "English (UK)",
    "EN-US": "English (US)", "ES": "Spanish", "ET": "Estonian",
    "FI": "Finnish", "FR": "French", "HU": "Hungarian", "ID": "Indonesian",
    "IT": "Italian", "JA": "Japanese", "KO": "Korean", "LT": "Lithuanian",
    "LV": "Latvian", "NB": "Norwegian", "NL": "Dutch", "PL": "Polish",
    "PT-BR": "Portuguese (BR)", "PT-PT": "Portuguese (PT)", "RO": "Romanian",
    "RU": "Russian", "SK": "Slovak", "SL": "Slovenian", "SV": "Swedish",
    "TR": "Turkish", "UK": "Ukrainian", "ZH-HANS": "Chinese (Simplified)",
    "ZH-HANT": "Chinese (Traditional)",
}

BATCH_OPUS_LANGS: dict[str, str] = {
    "ES": "Spanish", "FR": "French", "DE": "German", "IT": "Italian",
    "NL": "Dutch", "RU": "Russian", "PL": "Polish", "SV": "Swedish",
    "DA": "Danish", "FI": "Finnish", "PT-BR": "Portuguese (BR)",
    "PT-PT": "Portuguese (PT)", "RO": "Romanian", "BG": "Bulgarian",
    "CS": "Czech", "ET": "Estonian", "HU": "Hungarian", "LT": "Lithuanian",
    "LV": "Latvian", "SK": "Slovak", "SL": "Slovenian", "EL": "Greek",
    "TR": "Turkish", "UK": "Ukrainian",
}

BATCH_M2M_LANGS: dict[str, str] = {
    "AR": "Arabic", "BG": "Bulgarian", "CS": "Czech", "DA": "Danish",
    "DE": "German", "EL": "Greek", "EN-GB": "English (UK)",
    "EN-US": "English (US)", "ES": "Spanish", "ET": "Estonian",
    "FI": "Finnish", "FR": "French", "HU": "Hungarian", "ID": "Indonesian",
    "IT": "Italian", "JA": "Japanese", "KO": "Korean", "LT": "Lithuanian",
    "LV": "Latvian", "NB": "Norwegian", "NL": "Dutch", "PL": "Polish",
    "PT-BR": "Portuguese (BR)", "PT-PT": "Portuguese (PT)", "RO": "Romanian",
    "RU": "Russian", "SK": "Slovak", "SL": "Slovenian", "SV": "Swedish",
    "TR": "Turkish", "UK": "Ukrainian", "ZH-HANS": "Chinese (Simplified)",
    "ZH-HANT": "Chinese (Traditional)",
}

BATCH_ENGINE_NAMES: dict[str, str] = {
    "none": "No Translation",
    "deepl": "DeepL (Online)",
    "offline-opus": "Offline (Language Specific)",
    "offline-m2m": "Offline (M2M 100+ Languages)",
}

# ── Display Styling ──

COLOR_PALETTE: list[dict[str, str]] = [
    {"id": "white", "hex": "#FFFFFF", "name": "White"},
    {"id": "cream", "hex": "#FFF8E1", "name": "Cream"},
    {"id": "gold", "hex": "#FFD54F", "name": "Gold"},
    {"id": "cyan", "hex": "#4FC3F7", "name": "Cyan"},
    {"id": "mint", "hex": "#81C784", "name": "Mint"},
    {"id": "coral", "hex": "#FF8A80", "name": "Coral"},
    {"id": "peach", "hex": "#FFAB91", "name": "Peach"},
    {"id": "lavender", "hex": "#CE93D8", "name": "Lavender"},
    {"id": "sky", "hex": "#90CAF9", "name": "Sky Blue"},
    {"id": "lime", "hex": "#C5E1A5", "name": "Lime"},
    {"id": "rose", "hex": "#F48FB1", "name": "Rose"},
    {"id": "aqua", "hex": "#80DEEA", "name": "Aqua"},
]

BG_OPTIONS: list[dict[str, str]] = [
    {"id": "navy", "hex": "#00004D", "name": "Deep Navy"},
    {"id": "indigo", "hex": "#1B1B3A", "name": "Dark Indigo"},
    {"id": "midnight", "hex": "#0D1B2A", "name": "Midnight"},
    {"id": "charcoal", "hex": "#2D2D2D", "name": "Charcoal"},
]

FONT_OPTIONS: list[dict[str, str]] = [
    {"id": "atkinson", "name": "Atkinson Hyperlegible",
     "css": "'Atkinson Hyperlegible Next', sans-serif",
     "note": "Maximum legibility (Latin)"},
    {"id": "noto", "name": "Noto Sans",
     "css": "'Noto Sans', 'Noto Sans SC', 'Noto Sans JP', 'Noto Sans KR', 'Noto Sans Arabic', sans-serif",
     "note": "Universal (150+ scripts incl. CJK/Arabic)"},
    {"id": "ibm", "name": "IBM Plex Sans",
     "css": "'IBM Plex Sans', 'Noto Sans', sans-serif",
     "note": "Clear, professional, multilingual"},
    {"id": "source", "name": "Source Sans 3",
     "css": "'Source Sans 3', 'Noto Sans', sans-serif",
     "note": "Excellent readability, wide support"},
    {"id": "inter", "name": "Inter",
     "css": "'Inter', 'Noto Sans', sans-serif",
     "note": "Modern, clean, wide support"},
]

# ── Default Server Config ──

DEFAULT_CONFIG: dict[str, Any] = {
    "deepl_api_key": "",
    "session_title": "Live Captioning",
    "input_lang": "EN",
    "translation_count": 1,
    "translations": [{"lang": "ES", "color": "#FFD54F"}],
    "speakers": [],
    "footer_image": None,
    "footer_text": "",
    "font_size": 42,
    "max_lines": 3,
    "bg_color": "#00004D",
    "font_family": "atkinson",
    "caption_color": "#FFFFFF",
    "backend": "auto",
    "ui_language": "EN",
    "bidirectional_enabled": False,
    "bidirectional_langs": [],
    "bidirectional_tuned_swap": False,
    "voice_id_enabled": True,
    "voice_id_threshold": 0.65,
    "translate_cores": 0,
    "collapsed_sections": ["languages"],
    "footer_position": 50,
    "display_grids": {"main": {}, "extended": {}},
}
