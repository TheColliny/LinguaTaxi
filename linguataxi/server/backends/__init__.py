"""Speech-to-text backend implementations.

This package provides the abstract base class and concrete backends for
speech recognition.  Use :func:`create_backend` to instantiate a backend
by name without importing the concrete class directly.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from .base import SpeechBackend

log: logging.Logger = logging.getLogger("livecaption")

# Protects stt_backend._model hot-swap (e.g. tuned-model switching).
model_lock: threading.RLock = threading.RLock()


def create_backend(name: str, **kwargs: Any) -> SpeechBackend:
    """Factory that lazily imports and instantiates a speech backend.

    This avoids importing heavy optional dependencies (faster-whisper,
    vosk, mlx_whisper) until they are actually needed.

    Args:
        name: Backend identifier.  One of ``'whisper'``, ``'vosk'``,
              or ``'mlx-whisper'``.
        **kwargs: Forwarded to the backend constructor.

    Returns:
        An initialised :class:`SpeechBackend` instance.

    Raises:
        ValueError: If *name* is not a recognised backend.
    """
    if name == "whisper":
        from .whisper import WhisperBackend

        return WhisperBackend(**kwargs)
    elif name == "vosk":
        from .vosk import VoskBackend

        return VoskBackend(**kwargs)
    elif name == "mlx-whisper":
        from .mlx_whisper import MLXWhisperBackend

        return MLXWhisperBackend(**kwargs)
    else:
        raise ValueError(f"Unknown speech backend: {name!r}")


__all__ = [
    "SpeechBackend",
    "create_backend",
    "model_lock",
]
