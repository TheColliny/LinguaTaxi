"""Language-Tuned Whisper Model Manager.

Downloads fine-tuned Whisper models from HuggingFace, converts them to
CTranslate2 format (used by faster-whisper), and manages hot-swapping
at runtime.

Requires: transformers, torch (CPU ok), huggingface_hub
These are only needed for download+conversion, not for inference.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from typing import Any, Callable

log: logging.Logger = logging.getLogger("livecaption")

# CLI mode flag -- when True, _set_progress also prints machine-parseable lines
_cli_mode: bool = False


def _short_hf_cache() -> str:
    """Return a unique short temp path for HuggingFace downloads.

    Windows MAX_PATH (260 chars) breaks with long HF cache paths.
    Each call returns a unique directory to avoid race conditions
    between concurrent downloads.

    Returns:
        Path to a temporary directory for HF downloads.
    """
    if sys.platform == "win32":
        base = Path("C:/tmp")
        base.mkdir(parents=True, exist_ok=True)
        d = tempfile.mkdtemp(prefix="lt_hf_", dir=str(base))
        return d
    return tempfile.mkdtemp(prefix="lt_hf_")

# Only download files needed for CTranslate2 conversion (skip training checkpoints,
# optimizer states, TF/Flax weights, etc. -- some repos have 30+ GB of extras)
_HF_ALLOW: list[str] = [
    "config.json", "preprocessor_config.json", "generation_config.json",
    "tokenizer.json", "tokenizer_config.json", "vocab.json", "merges.txt",
    "added_tokens.json", "special_tokens_map.json", "normalizer.json",
    "model.safetensors", "model.safetensors.index.json", "model-*.safetensors",
    "pytorch_model.bin", "pytorch_model.bin.index.json", "pytorch_model-*.bin",
]

# -- Tuned model registry --
# Maps DeepL language code -> HuggingFace model info
TUNED_MODELS: dict[str, dict[str, Any]] = {
    "ES": {
        "name": "Spanish (Turbo)",
        "hf_repo": "adriszmar/whisper-large-v3-turbo-es",
        "base": "turbo",
        "size_gb": 1.6,
    },
    "FR": {
        "name": "French",
        "hf_repo": "bofenghuang/whisper-large-v3-french",
        "base": "large",
        "size_gb": 3.1,
    },
    "DE": {
        "name": "German",
        "hf_repo": "primeline/whisper-large-v3-german",
        "base": "large",
        "size_gb": 3.1,
    },
    "AR": {
        "name": "Arabic",
        "hf_repo": "Byne/whisper-large-v3-arabic",
        "base": "large",
        "size_gb": 3.1,
    },
    "JA": {
        "name": "Japanese",
        "hf_repo": "kotoba-tech/kotoba-whisper-v2.0",
        "base": "distil",
        "size_gb": 1.5,
    },
    "ZH": {
        "name": "Chinese",
        "hf_repo": "BELLE-2/Belle-whisper-large-v3-zh",
        "base": "large",
        "size_gb": 3.1,
    },
}

# -- Progress tracking --
# {lang_code: {"status": str, "pct": int, "message": str}}
_progress: dict[str, dict[str, Any]] = {}
_progress_lock: threading.Lock = threading.Lock()


def _set_progress(lang: str, status: str, pct: int = 0, message: str = "") -> None:
    """Update progress state for a language model download.

    Args:
        lang: Language code (e.g. ``"ES"``).
        status: Status string (``"downloading"``, ``"converting"``, ``"ready"``, ``"error"``).
        pct: Percentage complete (0-100).
        message: Human-readable status message.
    """
    with _progress_lock:
        _progress[lang] = {"status": status, "pct": pct, "message": message}
    if _cli_mode:
        print(f"PROGRESS:{lang}:{status}:{pct}:{message}", flush=True)


def get_progress(lang: str) -> dict[str, Any]:
    """Get current download progress for a language.

    Args:
        lang: Language code to query.

    Returns:
        Dict with ``status``, ``pct``, and ``message`` keys.
    """
    with _progress_lock:
        return _progress.get(lang, {"status": "idle", "pct": 0, "message": ""})


def get_tuned_dir(models_dir: str | Path) -> Path:
    """Return the tuned models base directory.

    Args:
        models_dir: Base models directory.

    Returns:
        Path to the ``tuned/`` subdirectory.
    """
    d = Path(models_dir) / "tuned"
    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass  # Directory may not be writable (e.g. Program Files)
    return d


def get_model_path(models_dir: str | Path, lang_code: str) -> Path:
    """Return path to a converted CTranslate2 model for a language.

    Args:
        models_dir: Base models directory.
        lang_code: DeepL language code (e.g. ``"ES"``).

    Returns:
        Path to the model directory.
    """
    return get_tuned_dir(models_dir) / lang_code.lower()


def is_available(models_dir: str | Path, lang_code: str) -> bool:
    """Check if a tuned model is downloaded and converted for this language.

    Args:
        models_dir: Base models directory.
        lang_code: DeepL language code.

    Returns:
        True if the model's ``model.bin`` exists.
    """
    mp = get_model_path(models_dir, lang_code)
    return (mp / "model.bin").exists()


def get_all_status(models_dir: str | Path) -> dict[str, dict[str, Any]]:
    """Return status of all tuned models.

    Args:
        models_dir: Base models directory.

    Returns:
        Dict mapping language codes to status dicts.
    """
    result: dict[str, dict[str, Any]] = {}
    for lang, info in TUNED_MODELS.items():
        prog = get_progress(lang)
        result[lang] = {
            "name": info["name"],
            "hf_repo": info["hf_repo"],
            "base": info["base"],
            "size_gb": info["size_gb"],
            "available": is_available(models_dir, lang),
            "download_status": prog["status"],
            "download_pct": prog["pct"],
            "download_message": prog["message"],
        }
    return result


def delete_model(models_dir: str | Path, lang_code: str) -> bool:
    """Delete a downloaded tuned model.

    Args:
        models_dir: Base models directory.
        lang_code: DeepL language code.

    Returns:
        True if the model was deleted.
    """
    mp = get_model_path(models_dir, lang_code)
    if mp.exists():
        shutil.rmtree(mp)
        log.info(f"Deleted tuned model for {lang_code}: {mp}")
        return True
    return False


def get_model_disk_size(models_dir: str | Path, lang_code: str) -> int:
    """Return disk size in bytes of a tuned model directory.

    Args:
        models_dir: Base models directory.
        lang_code: DeepL language code.

    Returns:
        Total size in bytes, or 0 if not found.
    """
    mp = get_model_path(models_dir, lang_code)
    if not mp.exists():
        return 0
    total = 0
    for f in mp.rglob("*"):
        if f.is_file():
            total += f.stat().st_size
    return total


def pick_quantization(vram_mb: int, base_type: str) -> str:
    """Choose quantization based on VRAM and model base type.

    Target: ~75% VRAM usage to leave room for OS + other apps.

    Args:
        vram_mb: GPU VRAM in megabytes.
        base_type: Model base type (``"turbo"``, ``"large"``, ``"distil"``).

    Returns:
        Quantization string (``"float16"``, ``"int8_float16"``, or ``"int8"``).
    """
    if base_type == "turbo":
        # Turbo models are smaller (~1.6GB)
        if vram_mb >= 6000:
            return "float16"
        elif vram_mb >= 4000:
            return "int8_float16"
        else:
            return "int8"
    else:
        # Large/distil models (~3.1GB or ~1.5GB)
        if vram_mb >= 10000:
            return "float16"
        elif vram_mb >= 6000:
            return "int8_float16"
        else:
            return "int8"


def detect_vram() -> int:
    """Detect NVIDIA GPU VRAM in MB.

    Returns:
        VRAM in megabytes, or 0 if no GPU detected.
    """
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
            stderr=subprocess.DEVNULL, timeout=5
        ).decode().strip()
        if not out:
            return 0
        # Use max VRAM across all GPUs in multi-GPU systems
        return max(int(line.strip()) for line in out.split("\n") if line.strip())
    except Exception:
        return 0


def _check_converter_deps() -> list[str]:
    """Check if conversion dependencies are installed.

    Returns:
        List of missing package names (empty if all present).
    """
    missing: list[str] = []
    try:
        import ctranslate2  # noqa: F401
    except ImportError:
        missing.append("ctranslate2")
    try:
        import transformers  # noqa: F401
    except ImportError:
        missing.append("transformers")
    try:
        import torch  # noqa: F401
    except ImportError:
        missing.append("torch")
    try:
        import huggingface_hub  # noqa: F401
    except ImportError:
        missing.append("huggingface_hub")
    return missing


def download_and_convert(
    models_dir: str | Path,
    lang_code: str,
    vram_mb: int = 0,
    on_complete: Callable[[str, bool, str], None] | None = None,
) -> threading.Thread | None:
    """Download a tuned model from HuggingFace and convert to CTranslate2 format.

    Runs in a background thread. Call :func:`get_progress` to track.

    Args:
        models_dir: Path to ``models/`` directory.
        lang_code: DeepL language code (e.g. ``"ES"``, ``"AR"``).
        vram_mb: GPU VRAM in MB (for quantization selection), 0 = use int8.
        on_complete: Optional callback ``(lang_code, success, error_msg)``.

    Returns:
        The background thread, or ``None`` if already available.
    """
    if lang_code not in TUNED_MODELS:
        _set_progress(lang_code, "error", 0, f"Unknown language: {lang_code}")
        return None

    info = TUNED_MODELS[lang_code]
    output_path = get_model_path(models_dir, lang_code)

    # Already available?
    if is_available(models_dir, lang_code):
        _set_progress(lang_code, "ready", 100, "Model already available")
        if on_complete:
            on_complete(lang_code, True, "")
        return None

    def _worker() -> None:
        try:
            # Check dependencies
            missing = _check_converter_deps()
            if missing:
                msg = f"Missing packages: {', '.join(missing)}. Install with: pip install {' '.join(missing)}"
                _set_progress(lang_code, "error", 0, msg)
                log.error(f"Tuned model download failed for {lang_code}: {msg}")
                if on_complete:
                    on_complete(lang_code, False, msg)
                return

            # Step 1: Download from HuggingFace
            _set_progress(lang_code, "downloading", 5,
                          f"Downloading {info['name']} ({info['size_gb']} GB)...")
            log.info(f"Downloading tuned model for {lang_code}: {info['hf_repo']}")

            from huggingface_hub import snapshot_download

            # Use short cache path to avoid Windows MAX_PATH (260 char) errors
            hf_cache_dir = _short_hf_cache()

            hf_local = snapshot_download(
                repo_id=info["hf_repo"],
                cache_dir=hf_cache_dir,
                allow_patterns=_HF_ALLOW,
            )

            _set_progress(lang_code, "converting", 60,
                          "Converting to CTranslate2 format...")
            log.info(f"Converting {lang_code} model to CTranslate2...")

            # Step 2: Convert to CTranslate2 format
            quant = pick_quantization(vram_mb, info["base"]) if vram_mb > 0 else "int8"

            # Clean output dir if partial
            if output_path.exists():
                shutil.rmtree(output_path)
            output_path.mkdir(parents=True, exist_ok=True)

            _set_progress(lang_code, "converting", 70,
                          f"Converting with {quant} quantization...")

            # Use CTranslate2 Python API for conversion
            try:
                from ctranslate2.converters import TransformersConverter
                converter = TransformersConverter(hf_local)
                converter.convert(str(output_path), quantization=quant, force=True)
                # Copy tokenizer files needed by faster-whisper at inference time
                for tok_file in ("tokenizer.json", "tokenizer_config.json",
                                 "vocab.json", "merges.txt", "added_tokens.json",
                                 "special_tokens_map.json", "preprocessor_config.json",
                                 "normalizer.json"):
                    src = Path(hf_local) / tok_file
                    dst = output_path / tok_file
                    if src.exists() and not dst.exists():
                        shutil.copy2(str(src), str(dst))
            except Exception as conv_err:
                # Clean up partial output
                if output_path.exists():
                    shutil.rmtree(output_path)
                error_msg = str(conv_err)[:200]
                _set_progress(lang_code, "error", 0, error_msg)
                log.error(f"CTranslate2 conversion failed for {lang_code}: {conv_err}")
                if on_complete:
                    on_complete(lang_code, False, error_msg)
                return

            # Verify the output
            if not (output_path / "model.bin").exists():
                _set_progress(lang_code, "error", 0, "Conversion produced no model.bin")
                if on_complete:
                    on_complete(lang_code, False, "Conversion produced no model.bin")
                return

            _set_progress(lang_code, "ready", 100,
                          f"{info['name']} model ready ({quant})")
            log.info(f"Tuned model ready for {lang_code}: {output_path} ({quant})")

            # Clean up HF cache to save disk space
            try:
                shutil.rmtree(hf_cache_dir)
            except Exception:
                pass

            if on_complete:
                on_complete(lang_code, True, "")

        except Exception as e:
            # Clean up partial output directory
            if output_path.exists() and not (output_path / "model.bin").exists():
                try:
                    shutil.rmtree(output_path, ignore_errors=True)
                except Exception:
                    pass
            # Clean up HF cache on failure
            try:
                if hf_cache_dir:
                    shutil.rmtree(hf_cache_dir, ignore_errors=True)
            except Exception:
                pass
            error_msg = str(e)[:200]
            _set_progress(lang_code, "error", 0, error_msg)
            log.error(f"Tuned model download/convert failed for {lang_code}: {e}")
            if on_complete:
                on_complete(lang_code, False, error_msg)

    _set_progress(lang_code, "starting", 0, "Starting download...")
    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()
    return thread
