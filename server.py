"""LinguaTaxi server entry point.

This module is kept as a thin shell for backward compatibility with the
installer and other entry points that ``import server``.  All real logic
lives in :mod:`linguataxi.server.app` (FastAPI apps and state) and
:mod:`linguataxi.server.main` (CLI, startup, shutdown).

Mutable scalar state (``stt_backend``, ``captioning_paused``, etc.) is
proxied to :mod:`linguataxi.server.app` via a custom module class so that
``import server as _srv; _srv.stt_backend`` always reads the live value
even after ``main()`` reassigns it in :mod:`linguataxi.server.app`.
"""

from __future__ import annotations

import sys
import types
from typing import Any

# ── Import the canonical modules first (triggers CUDA setup via main.py) ──
from linguataxi.server.main import main  # noqa: F401 — CLI entry point

import linguataxi.server.app as _app_mod
import linguataxi.settings as _settings_mod

from linguataxi.server.app import (  # noqa: F401
    # FastAPI app instances (identity-stable objects)
    display_app,
    extended_app,
    operator_app,
    dictation_app,
    # Config dict — mutated in place, same object everywhere
    config,
    # Threading events — identity-stable objects
    shutdown_event,
    mic_restart_event,
    # Path constants
    BASE_DIR,
    EDITION,
    # Plugin system — identity-stable objects
    plugin_dispatcher,
    # Functions
    _get_registry,
    _save_speaker_config,
    _load_speaker_config,
    log,
)

from linguataxi.server.main import (  # noqa: F401
    detect_gpu,
    _shutdown_and_exit,
    _graceful_shutdown,
)

from linguataxi.server.audio import (  # noqa: F401
    _detect_segment_lang,
    _voice_id_try_enroll,
    _voice_id_try_identify,
)


# ── Mutable scalar names that are reassigned at runtime ──
# These MUST be proxied so that ``import server as _srv; _srv.X``
# reads/writes the live value in the canonical module.

_MUTABLE_APP_ATTRS: frozenset[str] = frozenset({
    "stt_backend",
    "current_mic_index",
    "silence_threshold",
    "translation_paused",
    "captioning_paused",
    "dictation_active",
    "_dictation_loop",
    "save_transcripts",
})

_MUTABLE_SETTINGS_ATTRS: frozenset[str] = frozenset({
    "MODELS_DIR",
})


class _ServerModule(types.ModuleType):
    """Custom module type that proxies mutable state to canonical locations."""

    def __getattr__(self, name: str) -> Any:
        if name in _MUTABLE_APP_ATTRS:
            return getattr(_app_mod, name)
        if name in _MUTABLE_SETTINGS_ATTRS:
            return getattr(_settings_mod, name)
        raise AttributeError(f"module 'server' has no attribute {name!r}")

    def __setattr__(self, name: str, value: Any) -> None:
        if name in _MUTABLE_APP_ATTRS:
            setattr(_app_mod, name, value)
            return
        if name in _MUTABLE_SETTINGS_ATTRS:
            setattr(_settings_mod, name, value)
            return
        super().__setattr__(name, value)


# Replace this module in sys.modules with the proxy version.
# This preserves all already-imported names while adding the proxy behavior.
_this = sys.modules[__name__]
_proxy = _ServerModule(__name__, __doc__)
_proxy.__dict__.update({
    k: v for k, v in _this.__dict__.items()
    if k not in _MUTABLE_APP_ATTRS and k not in _MUTABLE_SETTINGS_ATTRS
})
_proxy.__file__ = _this.__file__
_proxy.__spec__ = _this.__spec__
_proxy.__path__ = getattr(_this, "__path__", None)  # type: ignore[assignment]
_proxy.__package__ = _this.__package__
sys.modules[__name__] = _proxy


if __name__ == "__main__":
    main()
