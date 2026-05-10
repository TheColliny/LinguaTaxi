"""
LinguaTaxi — Local Claim Detection Pre-Filter
Uses Factiverse claim_detection model (XLM-RoBERTa-Large) quantized to INT8 ONNX
to classify statements as check-worthy or not before sending to cloud APIs.

Model: ~560MB on disk, ~1.5GB RAM at inference, 150-300ms per sentence on CPU.
Output: binary — "Check-worthy" (fact claim) or "Not check-worthy" (opinion/ambiguous).
"""

import logging
import threading
from pathlib import Path

import numpy as np

log = logging.getLogger("livecaption")

_PLUGIN_DIR = Path(__file__).parent
_MODEL_DIR = _PLUGIN_DIR / "models" / "claim_detection"
_ONNX_PATH = _MODEL_DIR / "model_quantized.onnx"
_TOKENIZER_DIR = _MODEL_DIR

_session = None
_tokenizer = None
_load_lock = threading.Lock()
_loaded = False
_load_error = None


def is_available() -> bool:
    return _ONNX_PATH.exists()


def is_loaded() -> bool:
    return _loaded


def get_load_error() -> str | None:
    return _load_error


def ensure_loaded() -> bool:
    global _session, _tokenizer, _loaded, _load_error
    if _loaded:
        return True
    if not is_available():
        _load_error = "Model not downloaded"
        return False

    with _load_lock:
        if _loaded:
            return True
        try:
            import onnxruntime as ort
            from transformers import AutoTokenizer

            sess_opts = ort.SessionOptions()
            sess_opts.inter_op_num_threads = 2
            sess_opts.intra_op_num_threads = 4
            sess_opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL

            _session = ort.InferenceSession(
                str(_ONNX_PATH),
                sess_options=sess_opts,
                providers=["CPUExecutionProvider"],
            )
            _tokenizer = AutoTokenizer.from_pretrained(str(_TOKENIZER_DIR))
            _loaded = True
            _load_error = None
            log.info("Claim detection model loaded from %s", _MODEL_DIR)
            return True
        except ImportError as e:
            _load_error = f"Missing dependency: {e.name}. Install: pip install onnxruntime transformers"
            log.warning("Claim filter unavailable: %s", _load_error)
            return False
        except Exception as e:
            _load_error = str(e)
            log.warning("Failed to load claim detection model: %s", e)
            return False


def classify(text: str) -> dict:
    """
    Classify a statement as check-worthy or not.

    Returns dict with:
      - is_claim: bool — True if check-worthy (should be sent to API)
      - confidence: float — model confidence (0-1)
      - label: str — "check_worthy" or "not_check_worthy"
    """
    if not _loaded:
        if not ensure_loaded():
            return {"is_claim": True, "confidence": 0.0, "label": "unknown"}

    inputs = _tokenizer(
        text,
        return_tensors="np",
        max_length=128,
        truncation=True,
        padding="max_length",
    )

    input_feed = {
        name: inputs[name]
        for name in [inp.name for inp in _session.get_inputs()]
        if name in inputs
    }

    logits = _session.run(None, input_feed)[0]

    probs = _softmax(logits[0])
    predicted_class = int(np.argmax(probs))

    # Factiverse model: class 0 = Not check-worthy, class 1 = Check-worthy
    is_claim = predicted_class == 1
    confidence = float(probs[predicted_class])

    return {
        "is_claim": is_claim,
        "confidence": confidence,
        "label": "check_worthy" if is_claim else "not_check_worthy",
    }


def _softmax(x):
    e = np.exp(x - np.max(x))
    return e / e.sum()


def shutdown():
    global _session, _tokenizer, _loaded
    _session = None
    _tokenizer = None
    _loaded = False
