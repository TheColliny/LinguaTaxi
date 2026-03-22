"""
Silero Language Classifier 95 — ONNX wrapper for spoken language detection.
MIT licensed, 4.7MB model, <1ms inference, 95 languages.
"""

import logging
import json
import threading
from pathlib import Path

import numpy as np

log = logging.getLogger("lang_detect")

# Default model storage alongside other models
_MODELS_DIR = Path(__file__).parent / "models"
_MODEL_SUBDIR = "silero-lang-detect"

# Silero lang_detector_95 ONNX model — original URL is dead (removed from silero-vad repo).
# The model can still be obtained from archived copies or manual placement.
_MODEL_URL = "https://models.silero.ai/models/langs/lang_detector_95.onnx"
_LANG_DICT_URL = "https://raw.githubusercontent.com/snakers4/silero-vad/master/files/lang_dict_95.json"

_session = None  # ONNX InferenceSession (lazy loaded)
_lang_dict = None  # {index: lang_code} mapping
_load_lock = threading.Lock()
_load_failed = False  # Set True after download/load failure to prevent retry spam


def set_models_dir(path):
    """Override the default models directory. Must be called before first use."""
    global _MODELS_DIR
    _MODELS_DIR = Path(path)


def _model_dir():
    return _MODELS_DIR / _MODEL_SUBDIR


def download_model(models_dir=None):
    """Download the Silero lang_detector_95 ONNX model if not present."""
    import requests

    d = Path(models_dir) / _MODEL_SUBDIR if models_dir else _model_dir()
    d.mkdir(parents=True, exist_ok=True)

    onnx_path = d / "lang_detector_95.onnx"
    dict_path = d / "lang_dict_95.json"

    if not onnx_path.exists():
        log.info(f"Downloading Silero language detection model to {d}")
        r = requests.get(_MODEL_URL, timeout=60)
        r.raise_for_status()
        onnx_path.write_bytes(r.content)
        log.info(f"Downloaded {onnx_path.name} ({len(r.content) / 1e6:.1f} MB)")

    if not dict_path.exists():
        r = requests.get(_LANG_DICT_URL, timeout=30)
        r.raise_for_status()
        dict_path.write_text(r.text, encoding="utf-8")
        log.info(f"Downloaded {dict_path.name}")

    return onnx_path, dict_path


def _load():
    """Lazy-load the ONNX model and language dictionary."""
    global _session, _lang_dict, _load_failed

    if _session is not None:
        return

    if _load_failed:
        raise RuntimeError("Silero language detection model unavailable (previous load failed)")

    with _load_lock:
        if _session is not None:
            return  # another thread loaded while we waited
        if _load_failed:
            raise RuntimeError("Silero language detection model unavailable (previous load failed)")

        onnx_path = _model_dir() / "lang_detector_95.onnx"
        dict_path = _model_dir() / "lang_dict_95.json"

        if not onnx_path.exists():
            try:
                download_model()
            except Exception as e:
                _load_failed = True
                log.warning(f"Silero language detection model download failed: {e} "
                            "— falling back to Whisper built-in detection. "
                            "To use Silero, manually place lang_detector_95.onnx in "
                            f"{_model_dir()}")
                raise

        try:
            import onnxruntime as ort
            sess = ort.InferenceSession(str(onnx_path), providers=["CPUExecutionProvider"])
        except Exception as e:
            _load_failed = True
            log.warning(f"Failed to load Silero ONNX model: {e}")
            raise

        with open(dict_path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        lang_dict = {int(k): v for k, v in raw.items()}

        # Assign both atomically — _session last so the guard only passes when fully ready
        _lang_dict = lang_dict
        _session = sess

        log.info(f"Silero language detector loaded ({len(_lang_dict)} languages)")


def detect_language(audio, candidates=None):
    """
    Detect spoken language from audio.

    Args:
        audio: numpy float32 array, 16kHz mono
        candidates: optional list of language codes (e.g., ["en", "ar"])
                    to restrict detection to — boosts effective accuracy

    Returns:
        (lang_code, confidence) — e.g., ("en", 0.94)
    """
    _load()

    # Ensure float32, flatten
    audio = np.asarray(audio, dtype=np.float32).flatten()

    # Silero expects batched input: (batch, samples)
    inp = audio.reshape(1, -1)
    output = _session.run(None, {"input": inp})
    probs = output[0][0]  # shape: (num_languages,)

    if candidates:
        # Build index→code for candidates only
        candidate_set = set(c.lower() for c in candidates)
        candidate_indices = [
            (i, _lang_dict[i]) for i in range(len(probs))
            if i in _lang_dict and _lang_dict[i].lower() in candidate_set
        ]
        if candidate_indices:
            best_i, best_lang = max(candidate_indices, key=lambda x: probs[x[0]])
            # Normalize confidence among candidates only
            total = sum(probs[i] for i, _ in candidate_indices)
            conf = probs[best_i] / total if total > 0 else probs[best_i]
            return best_lang, float(conf)

    if candidates:
        log.warning("lang_detect: none of %s matched model vocabulary, using unrestricted detection", candidates)
    # Unrestricted: pick global best
    best_i = int(np.argmax(probs))
    return _lang_dict.get(best_i, "unknown"), float(probs[best_i])


def is_available():
    """Check if the ONNX model file exists (without loading it)."""
    return (_model_dir() / "lang_detector_95.onnx").exists()
