"""MLX Whisper (Apple Metal GPU) speech backend."""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any, Optional

import numpy as np

from linguataxi.constants import DEEPL_TO_WHISPER, SAMPLE_RATE
from .base import SpeechBackend

log: logging.Logger = logging.getLogger("livecaption")


class MLXWhisperBackend(SpeechBackend):
    """Apple Silicon GPU-accelerated speech recognition via mlx-whisper (Metal).

    Uses the ``mlx_whisper`` library to run Whisper models on Apple's
    Metal GPU.  Shares the same buffer-loop / transcription-queue
    architecture as :class:`WhisperBackend`.
    """

    MODEL_MAP: dict[str, str] = {
        "large-v3-turbo": "mlx-community/whisper-large-v3-turbo",
        "large-v3": "mlx-community/whisper-large-v3-mlx",
        "medium": "mlx-community/whisper-medium-mlx",
        "small": "mlx-community/whisper-small-mlx",
        "base": "mlx-community/whisper-base-mlx",
        "tiny": "mlx-community/whisper-tiny-mlx",
    }

    def __init__(self, model_name: str = "large-v3-turbo") -> None:
        """Initialise the MLX Whisper backend.

        Loads the model and runs a warm-up transcription to ensure
        the Metal compute pipeline is ready.

        Args:
            model_name: Whisper model identifier (e.g. 'large-v3-turbo').
        """
        import mlx_whisper

        self._model_name: str = model_name
        self._repo: str = self.MODEL_MAP.get(model_name, model_name)
        log.info(f"Loading MLX Whisper: {self._repo} (Apple Metal GPU)...")
        # Warm-up transcription to initialise the Metal pipeline
        test_audio = np.zeros(SAMPLE_RATE, dtype=np.float32)
        mlx_whisper.transcribe(
            test_audio, path_or_hf_repo=self._repo, language="en", word_timestamps=False
        )
        log.info("MLX Whisper model ready")

    @property
    def name(self) -> str:
        """Human-readable backend description."""
        return f"mlx-whisper ({self._model_name}, Apple Metal)"

    def _transcribe(self, buf: np.ndarray, lang: Optional[str] = None) -> str:
        """Transcribe an audio buffer using mlx-whisper.

        Args:
            buf: Audio samples (16 kHz mono float32).
            lang: Optional Whisper language code override.

        Returns:
            Transcribed text, or empty string on failure.
        """
        import mlx_whisper

        # Deferred import: config lives in server.py for now
        import server as _srv

        if lang:
            whisper_lang = lang
        else:
            whisper_lang = DEEPL_TO_WHISPER.get(_srv.config.get("input_lang", "EN"), "en")
        try:
            result = mlx_whisper.transcribe(
                buf.flatten().astype(np.float32),
                path_or_hf_repo=self._repo,
                language=whisper_lang,
                word_timestamps=False,
            )
            return (result.get("text", "") or "").strip()
        except Exception as e:
            log.error(f"MLX Whisper error: {e}")
            return ""

    def process_audio_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Start transcription worker and per-source buffer loops.

        Args:
            loop: The asyncio event loop for broadcasting results.
        """
        # Deferred imports: these functions still live in server.py
        import server as _srv

        threading.Thread(
            target=_srv._transcription_worker,
            args=(self._transcribe, loop),
            daemon=True,
        ).start()

        with _srv._sources_lock:
            for src in _srv._sources:
                t = threading.Thread(
                    target=_srv._buffer_audio_loop,
                    args=(self._transcribe, loop, src),
                    daemon=True,
                )
                t.start()
                src.buffer_thread = t
