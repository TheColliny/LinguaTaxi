"""FastAPI application creation, plugin integration, and server-wide state."""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import FastAPI
from starlette.staticfiles import StaticFiles

from linguataxi.settings import (
    MODELS_DIR, UPLOADS_DIR, TRANSCRIPTS_DIR,
    load_config, save_config,
)
from linguataxi.server.audio import (
    AudioSource, _sources, _sources_lock,
)

# ── Logging ──
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log: logging.Logger = logging.getLogger("livecaption")

# ── Project root ──
BASE_DIR: Path = Path(__file__).resolve().parent.parent.parent

# ── Server-wide configuration ──
config: Dict[str, Any] = load_config()

# ── Apply configured translation cores ──
from linguataxi.models import offline_translate  # noqa: E402 — must happen after config load

_configured_cores: int = config.get("translate_cores", 0)
if _configured_cores > 0:
    offline_translate.set_threads(_configured_cores)

# ── FastAPI app instances ──
display_app: FastAPI = FastAPI()
extended_app: FastAPI = FastAPI()
operator_app: FastAPI = FastAPI()
dictation_app: FastAPI = FastAPI()

# ── Speech backend (set during main() startup) ──
stt_backend: Optional[Any] = None

# ── Server state flags ──
shutdown_event: threading.Event = threading.Event()
mic_restart_event: threading.Event = threading.Event()
current_mic_index: Optional[int] = None
silence_threshold: float = 0.0  # overwritten from constants at import, then CLI
translation_paused: bool = True
captioning_paused: bool = True
dictation_active: bool = False
_dictation_loop: Optional[Any] = None  # asyncio loop for dictation app
save_transcripts: bool = True

# ── Import the default threshold so the initial value is correct ──
from linguataxi.constants import SILENCE_THRESHOLD  # noqa: E402

silence_threshold = SILENCE_THRESHOLD

# ── Plugin System ──
from linguataxi.plugins.loader import PluginDispatcher  # noqa: E402
from linguataxi.plugins.registry import PluginRegistry  # noqa: E402

PLUGINS_DIR: Path = BASE_DIR / "plugins"
PLUGINS_DIR.mkdir(exist_ok=True)
plugin_dispatcher: PluginDispatcher = PluginDispatcher(PLUGINS_DIR, config)

# ── Plugin Registry (marketplace) ──
_plugin_registry: Optional[PluginRegistry] = None
_edition_file: Path = BASE_DIR / "edition.txt"
EDITION: str = _edition_file.read_text().strip() if _edition_file.exists() else "Dev"


def _get_registry() -> PluginRegistry:
    """Lazy-initialize the plugin registry singleton.

    Returns:
        The global :class:`PluginRegistry` instance.
    """
    global _plugin_registry
    if _plugin_registry is None:
        plugins_dir = BASE_DIR / "plugins"
        _version_data: Dict[str, Any] = json.loads(
            (BASE_DIR / "version.json").read_text()
        )
        _plugin_registry = PluginRegistry(
            plugins_dir=plugins_dir,
            github_repo="TheColliny/linguataxi-plugins",
            app_version=_version_data.get("version", "0.0.0"),
            edition=EDITION,
        )
    return _plugin_registry


# ── Speaker config persistence ──

def _save_speaker_config() -> None:
    """Save speaker names, colors, and assignments to config."""
    with _sources_lock:
        speaker_config: Dict[str, Dict[str, Any]] = {}
        for s in _sources:
            key = str(s.device_index) if s.device_index is not None else "default"
            speaker_config[key] = {
                "name": s.name, "speaker": s.speaker, "color": s.color
            }
    config["speaker_config"] = speaker_config
    save_config(config)


def _load_speaker_config() -> None:
    """Restore speaker names and colors from config after sources are created."""
    sc: Dict[str, Any] = config.get("speaker_config", {})
    with _sources_lock:
        for s in _sources:
            key = str(s.device_index) if s.device_index is not None else "default"
            if key in sc:
                s.name = sc[key].get("name", s.name)
                s.speaker = sc[key].get("speaker", s.speaker)
                s.color = sc[key].get("color", "")


# ── Plugin discovery and loading ──
plugin_dispatcher.discover()
plugin_dispatcher.load_enabled(operator_app)

# Serve core static files on all 3 apps
_static_dir: Path = BASE_DIR / "static"
if _static_dir.is_dir():
    operator_app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")
    display_app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static_d")
    extended_app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static_e")

# Import route registration + plugin file handler
from linguataxi.server.routes.operator import (  # noqa: E402
    register_operator_routes, _make_plugin_file_handler,
)
from linguataxi.server.routes.display import register_display_routes  # noqa: E402
from linguataxi.server.routes.dictation import register_dictation_routes  # noqa: E402
from linguataxi.server.routes.transcribe import register_transcribe_routes  # noqa: E402

# Serve each plugin's static files on all 3 apps
for _m in plugin_dispatcher.get_all_manifests():
    _plugin_static = _m.path
    if _plugin_static.is_dir():
        _handler = _make_plugin_file_handler(_plugin_static)
        operator_app.get(f"/plugins/{_m.id}/{{filename}}")(_handler)
        display_app.get(f"/plugins/{_m.id}/{{filename}}")(_handler)
        extended_app.get(f"/plugins/{_m.id}/{{filename}}")(_handler)

# Register all route handlers
register_display_routes(display_app, extended_app)
register_operator_routes(operator_app)
register_dictation_routes(dictation_app)
register_transcribe_routes(operator_app)
