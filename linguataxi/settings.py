"""Settings persistence — single source of truth for config and launcher settings."""

from __future__ import annotations

import json
import os
import sys
import threading
from pathlib import Path
from typing import Any

from linguataxi.constants import DEFAULT_CONFIG, IS_WIN, IS_MAC

# ── Directories ──

BASE_DIR: Path = Path(__file__).resolve().parent.parent  # project root

if IS_WIN:
    SETTINGS_DIR: Path = Path(os.environ.get("APPDATA", str(Path.home()))) / "LinguaTaxi"
elif IS_MAC:
    SETTINGS_DIR = Path.home() / "Library" / "Application Support" / "LinguaTaxi"
else:
    SETTINGS_DIR = Path.home() / ".config" / "linguataxi"

SETTINGS_FILE: Path = SETTINGS_DIR / "launcher_settings.json"

if IS_WIN:
    _config_dir: Path = Path(os.environ.get("APPDATA", str(Path.home()))) / "LinguaTaxi"
elif IS_MAC:
    _config_dir = Path.home() / "Library" / "Application Support" / "LinguaTaxi"
else:
    _config_dir = Path.home() / ".config" / "linguataxi"
_config_dir.mkdir(parents=True, exist_ok=True)

CONFIG_PATH: Path = _config_dir / "config.json"
UPLOADS_DIR: Path = BASE_DIR / "uploads"
MODELS_DIR: Path = BASE_DIR / "models"
TRANSCRIPTS_DIR: Path = Path(os.environ.get(
    "LINGUATAXI_TRANSCRIPTS",
    str(Path.home() / "Documents" / "LinguaTaxi Transcripts"),
))

# Ensure data directories exist
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
MODELS_DIR.mkdir(parents=True, exist_ok=True)
TRANSCRIPTS_DIR.mkdir(parents=True, exist_ok=True)

# ── Config lock (server-side) ──
_config_lock: threading.Lock = threading.Lock()

# ── Launcher Settings ──


def load_settings() -> dict[str, Any]:
    """Load launcher settings from disk, returning empty dict on failure."""
    try:
        if SETTINGS_FILE.exists():
            with open(SETTINGS_FILE, "r") as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def save_settings(data: dict[str, Any]) -> None:
    """Persist launcher settings to disk."""
    try:
        SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
        with open(SETTINGS_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass


def get_setting(key: str, default: Any = None) -> Any:
    """Read a single launcher setting."""
    return load_settings().get(key, default)


def set_setting(key: str, value: Any) -> None:
    """Write a single launcher setting."""
    cfg = load_settings()
    cfg[key] = value
    save_settings(cfg)


# ── Server Config ──


def load_config() -> dict[str, Any]:
    """Load server config from disk, merged with defaults."""
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                return {**DEFAULT_CONFIG, **json.load(f)}
        except Exception:
            pass
    return dict(DEFAULT_CONFIG)


def save_config(cfg: dict[str, Any]) -> None:
    """Persist server config to disk (thread-safe)."""
    with _config_lock:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)


def save_speaker_config(sources: list, config: dict[str, Any], config_lock: threading.Lock) -> None:
    """Save speaker names, colors, assignments to config.

    Args:
        sources: list of AudioSource objects (accessed under config_lock).
        config: the live config dict to update.
        config_lock: lock protecting the sources list.
    """
    with config_lock:
        speaker_config: dict[str, Any] = {}
        for s in sources:
            key = str(s.device_index) if s.device_index is not None else "default"
            speaker_config[key] = {
                "name": s.name, "speaker": s.speaker, "color": s.color,
            }
    config["speaker_config"] = speaker_config
    save_config(config)


def load_speaker_config(sources: list, config: dict[str, Any], config_lock: threading.Lock) -> None:
    """Restore speaker names, colors from config after sources are created.

    Args:
        sources: list of AudioSource objects to update.
        config: the live config dict to read from.
        config_lock: lock protecting the sources list.
    """
    sc = config.get("speaker_config", {})
    with config_lock:
        for s in sources:
            key = str(s.device_index) if s.device_index is not None else "default"
            if key in sc:
                s.name = sc[key].get("name", s.name)
                s.speaker = sc[key].get("speaker", s.speaker)
                s.color = sc[key].get("color", "")
