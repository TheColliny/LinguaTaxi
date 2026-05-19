"""Voice ID -- Speaker Identification via Voice Embeddings.

Uses a WeSpeaker ResNet34 ONNX model (~25MB) to extract 256-dimensional
speaker embeddings from audio. Enrolled speakers are identified by cosine
similarity matching against their stored voiceprints.

Zero additional dependencies -- uses onnxruntime (already installed) + numpy.
Model is auto-downloaded from HuggingFace on first use.

Usage::

    from linguataxi.models import voice_id

    # Extract embedding from audio (16kHz mono float32)
    emb = voice_id.extract_embedding(audio_array)

    # Enroll a speaker
    voice_id.registry.enroll("Joe Biden", emb)

    # Identify a speaker
    match = voice_id.registry.identify(emb)
    if match:
        name, confidence = match
"""

from __future__ import annotations

import logging
import threading
import time as _time
from pathlib import Path
from typing import Any

import numpy as np

log: logging.Logger = logging.getLogger("voice_id")

# -- Model paths --
_MODELS_DIR: Path = Path(__file__).parent.parent.parent / "models"
_MODEL_SUBDIR: str = "wespeaker-resnet34"
_MODEL_FILENAME: str = "speaker_model.onnx"

_MODEL_URL: str = (
    "https://huggingface.co/onnx-community/wespeaker-voxceleb-resnet34-LM/"
    "resolve/main/onnx/model.onnx"
)

# -- ONNX session (lazy loaded) --
_session: Any = None
_load_lock: threading.Lock = threading.Lock()
_load_failed: bool = False
_load_failed_time: float = 0.0  # monotonic time of last failure
_LOAD_RETRY_COOLDOWN: float = 300.0  # retry load after 5 minutes

# -- Audio config --
SAMPLE_RATE: int = 16000
MIN_ENROLL_SECONDS: float = 3.0   # minimum audio for enrollment
MIN_IDENTIFY_SECONDS: float = 1.5  # minimum audio for identification


def set_models_dir(path: str | Path) -> None:
    """Override the default models directory. Must be called before first use.

    Args:
        path: New models directory path.
    """
    global _MODELS_DIR
    _MODELS_DIR = Path(path)


def _model_dir() -> Path:
    """Return the path to the voice ID model subdirectory.

    Returns:
        Path to the model directory.
    """
    return _MODELS_DIR / _MODEL_SUBDIR


def download_model(models_dir: str | Path | None = None) -> Path:
    """Download the WeSpeaker ONNX model if not present.

    Args:
        models_dir: Override models directory (uses default if ``None``).

    Returns:
        Path to the downloaded ONNX model file.
    """
    import requests

    d = Path(models_dir) / _MODEL_SUBDIR if models_dir else _model_dir()
    d.mkdir(parents=True, exist_ok=True)

    onnx_path = d / _MODEL_FILENAME
    if not onnx_path.exists():
        log.info(f"Downloading WeSpeaker speaker embedding model to {d}")
        r = requests.get(_MODEL_URL, timeout=120, allow_redirects=True)
        r.raise_for_status()
        onnx_path.write_bytes(r.content)
        log.info(f"Downloaded {onnx_path.name} ({len(r.content) / 1e6:.1f} MB)")

    return onnx_path


def _load() -> None:
    """Lazy-load the ONNX model. Retries after cooldown if previous load failed.

    Raises:
        RuntimeError: If the model is unavailable and retry cooldown has not elapsed.
    """
    global _session, _load_failed, _load_failed_time

    if _session is not None:
        return

    # Check if we should retry after the cooldown period
    if _load_failed and (_time.monotonic() - _load_failed_time) < _LOAD_RETRY_COOLDOWN:
        raise RuntimeError("Voice ID model unavailable (previous load failed, retry pending)")

    with _load_lock:
        if _session is not None:
            return
        if _load_failed and (_time.monotonic() - _load_failed_time) < _LOAD_RETRY_COOLDOWN:
            raise RuntimeError("Voice ID model unavailable (previous load failed, retry pending)")

        # Reset failure state for this retry attempt
        _load_failed = False

        onnx_path = _model_dir() / _MODEL_FILENAME
        if not onnx_path.exists():
            try:
                download_model()
            except Exception as e:
                _load_failed = True
                _load_failed_time = _time.monotonic()
                log.warning(f"Voice ID model download failed: {e} (will retry in {_LOAD_RETRY_COOLDOWN:.0f}s)")
                raise

        try:
            import onnxruntime as ort
            _session = ort.InferenceSession(
                str(onnx_path), providers=["CPUExecutionProvider"]
            )
        except Exception as e:
            _load_failed = True
            _load_failed_time = _time.monotonic()
            log.warning(f"Failed to load Voice ID ONNX model: {e} (will retry in {_LOAD_RETRY_COOLDOWN:.0f}s)")
            raise

        log.info("Voice ID speaker embedding model loaded (WeSpeaker ResNet34)")


def extract_embedding(audio: np.ndarray) -> np.ndarray:
    """Extract a 256-dimensional speaker embedding from audio.

    Args:
        audio: numpy float32 array, 16kHz mono (1D or 2D column vector).

    Returns:
        L2-normalized 256-d embedding vector (float32).

    Raises:
        ValueError: If the audio is too short for embedding extraction.
    """
    _load()

    # Ensure float32, flatten to 1D
    audio = np.asarray(audio, dtype=np.float32).flatten()

    # Minimum length check
    min_samples = int(MIN_IDENTIFY_SECONDS * SAMPLE_RATE)
    if len(audio) < min_samples:
        raise ValueError(
            f"Audio too short for embedding: {len(audio)/SAMPLE_RATE:.1f}s "
            f"(need >= {MIN_IDENTIFY_SECONDS}s)"
        )

    # WeSpeaker ONNX expects shape (1, num_samples)
    inp = audio.reshape(1, -1)

    # Get input name from model
    input_name = _session.get_inputs()[0].name
    outputs = _session.run(None, {input_name: inp})

    # Output is typically shape (1, 256) -- the speaker embedding
    embedding = outputs[0].flatten().astype(np.float32)

    # L2-normalize for cosine similarity
    norm = np.linalg.norm(embedding)
    if norm > 0:
        embedding = embedding / norm

    return embedding


def is_available() -> bool:
    """Check if the ONNX model file exists (without loading it).

    Returns:
        True if the model file exists on disk.
    """
    return (_model_dir() / _MODEL_FILENAME).exists()


# ===================================================================
# Speaker Registry -- enrollment + cosine similarity matching
# ===================================================================

class SpeakerRegistry:
    """Thread-safe registry of enrolled speaker voiceprints."""

    def __init__(self, threshold: float = 0.65) -> None:
        self._speakers: dict[str, np.ndarray] = {}  # name -> embedding
        self._enrollment_counts: dict[str, int] = {}  # name -> number of enrollments
        self._lock: threading.Lock = threading.Lock()
        self.threshold: float = threshold

    def enroll(self, name: str, embedding: np.ndarray) -> None:
        """Enroll a speaker. If already enrolled, updates with running average.

        Args:
            name: Speaker name.
            embedding: 256-d speaker embedding vector.
        """
        embedding = embedding.flatten().astype(np.float32)
        norm = np.linalg.norm(embedding)
        if norm > 0:
            embedding = embedding / norm

        with self._lock:
            if name in self._speakers:
                # Running average: blend old and new embeddings
                count = self._enrollment_counts.get(name, 1)
                old_emb = self._speakers[name]
                new_emb = (old_emb * count + embedding) / (count + 1)
                # Re-normalize
                new_norm = np.linalg.norm(new_emb)
                if new_norm > 0:
                    new_emb = new_emb / new_norm
                self._speakers[name] = new_emb
                self._enrollment_counts[name] = count + 1
                log.info(f"[Voice ID] Updated enrollment for '{name}' "
                         f"(sample {count + 1})")
            else:
                self._speakers[name] = embedding
                self._enrollment_counts[name] = 1
                log.info(f"[Voice ID] Enrolled '{name}'")

    def unenroll(self, name: str) -> bool:
        """Remove a speaker's voiceprint.

        Args:
            name: Speaker name to remove.

        Returns:
            True if the speaker was found and removed.
        """
        with self._lock:
            if name in self._speakers:
                del self._speakers[name]
                self._enrollment_counts.pop(name, None)
                log.info(f"[Voice ID] Unenrolled '{name}'")
                return True
            return False

    def identify(self, embedding: np.ndarray) -> tuple[str, float] | None:
        """Identify a speaker by comparing embedding against all enrolled voiceprints.

        Args:
            embedding: 256-d speaker embedding vector.

        Returns:
            ``(speaker_name, confidence)`` if best match exceeds threshold,
            else ``None``. Confidence is cosine similarity (0.0-1.0).
        """
        embedding = embedding.flatten().astype(np.float32)
        norm = np.linalg.norm(embedding)
        if norm > 0:
            embedding = embedding / norm

        with self._lock:
            if not self._speakers:
                return None

            best_name: str | None = None
            best_score: float = -1.0
            second_score: float = -1.0

            for name, enrolled_emb in self._speakers.items():
                # Cosine similarity (both are L2-normalized, so dot product = cosine sim)
                score = float(np.dot(embedding, enrolled_emb))
                if score > best_score:
                    second_score = best_score
                    best_score = score
                    best_name = name
                elif score > second_score:
                    second_score = score

        if best_score < self.threshold:
            return None

        # Suppress if top two are too close (ambiguous)
        if second_score >= 0 and (best_score - second_score) < 0.05:
            log.debug(f"[Voice ID] Ambiguous: {best_name} ({best_score:.3f}) vs "
                      f"runner-up ({second_score:.3f}) — suppressing")
            return None

        return (best_name, best_score)

    def get_enrolled(self) -> list[dict[str, Any]]:
        """Return list of enrolled speakers with metadata.

        Returns:
            List of dicts with ``name`` and ``enrollments`` keys.
        """
        with self._lock:
            return [
                {"name": name, "enrollments": self._enrollment_counts.get(name, 1)}
                for name in self._speakers
            ]

    def clear(self) -> None:
        """Remove all enrolled voiceprints."""
        with self._lock:
            self._speakers.clear()
            self._enrollment_counts.clear()
        log.info("[Voice ID] All voiceprints cleared")

    def set_threshold(self, value: float) -> None:
        """Set the cosine similarity threshold for identification.

        Args:
            value: Threshold value (clamped to 0.0-1.0).
        """
        self.threshold = max(0.0, min(1.0, value))
        log.info(f"[Voice ID] Threshold set to {self.threshold:.2f}")

    @property
    def count(self) -> int:
        """Number of enrolled speakers."""
        with self._lock:
            return len(self._speakers)


# -- Global registry instance --
registry: SpeakerRegistry = SpeakerRegistry()
