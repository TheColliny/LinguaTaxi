"""
LinguaTaxi — Voice ID: Speaker Identification via Voice Embeddings

Uses a WeSpeaker ResNet34 ONNX model (~25MB) to extract 256-dimensional
speaker embeddings from audio. Enrolled speakers are identified by cosine
similarity matching against their stored voiceprints.

Zero additional dependencies — uses onnxruntime (already installed) + numpy.
Model is auto-downloaded from HuggingFace on first use.

Usage:
    import voice_id

    # Extract embedding from audio (16kHz mono float32)
    emb = voice_id.extract_embedding(audio_array)

    # Enroll a speaker
    voice_id.registry.enroll("Joe Biden", emb)

    # Identify a speaker
    match = voice_id.registry.identify(emb)
    if match:
        name, confidence = match
"""

import logging
import threading
from pathlib import Path

import numpy as np

log = logging.getLogger("voice_id")

# ── Model paths ──
_MODELS_DIR = Path(__file__).parent / "models"
_MODEL_SUBDIR = "wespeaker-resnet34"
_MODEL_FILENAME = "speaker_model.onnx"

_MODEL_URL = (
    "https://huggingface.co/onnx-community/wespeaker-voxceleb-resnet34-LM/"
    "resolve/main/onnx/model.onnx"
)

# ── ONNX session (lazy loaded) ──
_session = None
_load_lock = threading.Lock()
_load_failed = False
_load_failed_time = 0.0  # monotonic time of last failure
_LOAD_RETRY_COOLDOWN = 300.0  # retry load after 5 minutes

# ── Audio config ──
SAMPLE_RATE = 16000
MIN_ENROLL_SECONDS = 3.0   # minimum audio for enrollment
MIN_IDENTIFY_SECONDS = 1.5  # minimum audio for identification


def set_models_dir(path):
    """Override the default models directory. Must be called before first use."""
    global _MODELS_DIR
    _MODELS_DIR = Path(path)


def _model_dir():
    return _MODELS_DIR / _MODEL_SUBDIR


def download_model(models_dir=None):
    """Download the WeSpeaker ONNX model if not present."""
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


import time as _time


def _load():
    """Lazy-load the ONNX model. Retries after cooldown if previous load failed."""
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
        audio: numpy float32 array, 16kHz mono (1D or 2D column vector)

    Returns:
        L2-normalized 256-d embedding vector (float32)
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

    # Output is typically shape (1, 256) — the speaker embedding
    embedding = outputs[0].flatten().astype(np.float32)

    # L2-normalize for cosine similarity
    norm = np.linalg.norm(embedding)
    if norm > 0:
        embedding = embedding / norm

    return embedding


def is_available():
    """Check if the ONNX model file exists (without loading it)."""
    return (_model_dir() / _MODEL_FILENAME).exists()


# ═══════════════════════════════════════════════════════════════════════════
# Speaker Registry — enrollment + cosine similarity matching
# ═══════════════════════════════════════════════════════════════════════════

class SpeakerRegistry:
    """Thread-safe registry of enrolled speaker voiceprints."""

    def __init__(self, threshold: float = 0.65):
        self._speakers: dict[str, np.ndarray] = {}  # name -> embedding
        self._enrollment_counts: dict[str, int] = {}  # name -> number of enrollments
        self._lock = threading.Lock()
        self.threshold = threshold

    def enroll(self, name: str, embedding: np.ndarray):
        """Enroll a speaker. If already enrolled, updates with running average
        for improved accuracy across multiple enrollments."""
        embedding = embedding.flatten().astype(np.float32)
        norm = np.linalg.norm(embedding)
        if norm > 0:
            embedding = embedding / norm

        with self._lock:
            if name in self._speakers:
                # Running average: blend old and new embeddings
                count = self._enrollment_counts.get(name, 1)
                old_emb = self._speakers[name]
                # Weighted average favoring newer samples slightly
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
        """Remove a speaker's voiceprint."""
        with self._lock:
            if name in self._speakers:
                del self._speakers[name]
                self._enrollment_counts.pop(name, None)
                log.info(f"[Voice ID] Unenrolled '{name}'")
                return True
            return False

    def identify(self, embedding: np.ndarray) -> tuple[str, float] | None:
        """Identify a speaker by comparing embedding against all enrolled voiceprints.

        Returns:
            (speaker_name, confidence) if best match exceeds threshold, else None.
            Confidence is cosine similarity (0.0-1.0).
        """
        embedding = embedding.flatten().astype(np.float32)
        norm = np.linalg.norm(embedding)
        if norm > 0:
            embedding = embedding / norm

        with self._lock:
            if not self._speakers:
                return None

            best_name = None
            best_score = -1.0
            second_score = -1.0

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

        # Suppress if top two are too close (ambiguous — likely cross-talk or noise)
        if second_score >= 0 and (best_score - second_score) < 0.05:
            log.debug(f"[Voice ID] Ambiguous: {best_name} ({best_score:.3f}) vs "
                      f"runner-up ({second_score:.3f}) — suppressing")
            return None

        return (best_name, best_score)

    def get_enrolled(self) -> list[dict]:
        """Return list of enrolled speakers with metadata."""
        with self._lock:
            return [
                {"name": name, "enrollments": self._enrollment_counts.get(name, 1)}
                for name in self._speakers
            ]

    def clear(self):
        """Remove all enrolled voiceprints."""
        with self._lock:
            self._speakers.clear()
            self._enrollment_counts.clear()
        log.info("[Voice ID] All voiceprints cleared")

    def set_threshold(self, value: float):
        """Set the cosine similarity threshold for identification."""
        self.threshold = max(0.0, min(1.0, value))
        log.info(f"[Voice ID] Threshold set to {self.threshold:.2f}")

    @property
    def count(self) -> int:
        with self._lock:
            return len(self._speakers)


# ── Global registry instance ──
registry = SpeakerRegistry()
