"""Faster-whisper (NVIDIA CUDA) speech backend."""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any, Optional

import numpy as np

from linguataxi.constants import DEEPL_TO_WHISPER, SAMPLE_RATE
from linguataxi.settings import MODELS_DIR
from .base import SpeechBackend

log: logging.Logger = logging.getLogger("livecaption")


class WhisperBackend(SpeechBackend):
    """GPU-accelerated speech recognition via faster-whisper (NVIDIA CUDA).

    Loads a Whisper model (from a bundled local path or HuggingFace hub)
    and transcribes audio segments submitted to the shared transcription
    queue by per-source buffer loops.
    """

    def __init__(self, model_name: str, device: str, compute_type: str) -> None:
        """Initialise the faster-whisper backend.

        Args:
            model_name: Whisper model identifier (e.g. 'large-v3-turbo').
            device: Compute device ('cuda' or 'cpu').
            compute_type: Precision type ('float16', 'int8', etc.).
        """
        from faster_whisper import WhisperModel

        self._model_name: str = model_name
        self._device: str = device
        self._compute_type: str = compute_type
        self._tuned_lang: Optional[str] = None  # tracks which language's tuned model is loaded

        # Check for bundled model first (e.g. models/faster-whisper-large-v3-turbo/)
        local_path = MODELS_DIR / f"faster-whisper-{model_name}"
        if local_path.exists() and (local_path / "model.bin").exists():
            log.info(f"Using bundled Whisper model: {local_path}")
            self._model = WhisperModel(str(local_path), device=device, compute_type=compute_type)
        else:
            self._model = WhisperModel(model_name, device=device, compute_type=compute_type)

    @property
    def name(self) -> str:
        """Human-readable backend description."""
        return f"whisper ({self._model_name}, {self._compute_type}, {self._device})"

    def _transcribe(self, buf: np.ndarray, lang: Optional[str] = None) -> str:
        """Transcribe an audio buffer using faster-whisper.

        Args:
            buf: Audio samples (16 kHz mono float32).
            lang: Optional Whisper language code override.

        Returns:
            Transcribed text, or empty string on failure.
        """
        # Deferred import: config lives in server.py for now
        import server as _srv

        if lang:
            whisper_lang = lang
        else:
            whisper_lang = DEEPL_TO_WHISPER.get(_srv.config.get("input_lang", "EN"), "en")
        try:
            segs, _ = self._model.transcribe(
                buf.flatten().astype(np.float32),
                language=whisper_lang,
                beam_size=3,
                vad_filter=True,
                vad_parameters=dict(min_silence_duration_ms=400, speech_pad_ms=150),
            )
            return " ".join(s.text.strip() for s in segs)
        except Exception as e:
            log.error(f"Whisper error: {e}")
            return ""

    def process_audio_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Start transcription worker and per-source buffer loops.

        Args:
            loop: The asyncio event loop for broadcasting results.
        """
        from linguataxi.server.audio import (
            _sources, _sources_lock, _buffer_audio_loop, _transcription_worker,
        )

        threading.Thread(
            target=_transcription_worker,
            args=(self._transcribe, loop),
            daemon=True,
        ).start()

        with _sources_lock:
            for src in _sources:
                t = threading.Thread(
                    target=_buffer_audio_loop,
                    args=(self._transcribe, loop, src),
                    daemon=True,
                )
                t.start()
                src.buffer_thread = t
