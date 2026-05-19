"""Offline Translation Engine using OPUS-MT and M2M-100.

Provides offline machine translation using OPUS-MT (per-language-pair) and
M2M-100 (multilingual fallback). Models run on CPU via CTranslate2, leaving
the GPU free for Whisper speech recognition.

OPUS-MT: ~310 MB download per pair, ~75 MB installed, ~35-50ms/sentence --
best for European languages.

M2M-100 1.2B: ~4.8 GB download, ~1.2 GB installed, ~150-300ms/sentence --
covers 100 languages.

Requires: ctranslate2, sentencepiece, huggingface_hub
For download+conversion: also needs transformers, torch (CPU)
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import sys
import tempfile
import threading
from pathlib import Path
from typing import Any, Callable


def get_default_cores() -> int:
    """Default CPU cores for translation: system cores / 4, min 1.

    Returns:
        Number of default translation threads.
    """
    return max(1, (os.cpu_count() or 4) // 4)

def get_max_cores() -> int:
    """Maximum allowed cores for translation: system cores - 1, min 1.

    Returns:
        Maximum number of translation threads.
    """
    return max(1, (os.cpu_count() or 4) - 1)


_intra_threads: int = get_default_cores()


def set_threads(n: int) -> None:
    """Set the number of intra-op threads for translation models.

    Clears the model cache so the next call re-creates translators.

    Args:
        n: Number of threads (clamped to 1..max_cores).
    """
    global _intra_threads
    _intra_threads = max(1, min(n, get_max_cores()))
    reload_models()


def reload_models() -> None:
    """Clear cached models so they reload with current thread settings."""
    with _models_lock:
        _loaded_models.clear()
    log.info(f"Offline translation models unloaded (will reload with intra_threads={_intra_threads})")


log: logging.Logger = logging.getLogger("livecaption")

# CLI mode flag -- when True, _set_progress also prints machine-parseable lines
_cli_mode: bool = False


def _short_hf_cache() -> str:
    """Return a unique short temp path for HuggingFace downloads.

    Windows MAX_PATH (260 chars) breaks with long HF cache paths.
    Each call returns a unique directory to avoid race conditions.

    Returns:
        Path to a temporary directory.
    """
    if sys.platform == "win32":
        base = Path("C:/tmp")
        base.mkdir(parents=True, exist_ok=True)
        d = tempfile.mkdtemp(prefix="lt_hf_", dir=str(base))
        return d
    return tempfile.mkdtemp(prefix="lt_hf_")

# -- OPUS-MT Model Registry --
# Maps DeepL target lang code -> HuggingFace repo and metadata
OPUS_MODELS: dict[str, dict[str, Any]] = {
    "ES":    {"hf_repo": "Helsinki-NLP/opus-mt-en-es", "name": "Spanish",     "size_mb": 310},
    "FR":    {"hf_repo": "Helsinki-NLP/opus-mt-en-fr", "name": "French",      "size_mb": 310},
    "DE":    {"hf_repo": "Helsinki-NLP/opus-mt-en-de", "name": "German",      "size_mb": 310},
    "IT":    {"hf_repo": "Helsinki-NLP/opus-mt-en-it", "name": "Italian",     "size_mb": 310},
    "NL":    {"hf_repo": "Helsinki-NLP/opus-mt-en-nl", "name": "Dutch",       "size_mb": 310},
    "RU":    {"hf_repo": "Helsinki-NLP/opus-mt-en-ru", "name": "Russian",     "size_mb": 310},
    "PL":    {"hf_repo": "Helsinki-NLP/opus-mt-en-pl", "name": "Polish",      "size_mb": 310},
    "SV":    {"hf_repo": "Helsinki-NLP/opus-mt-en-sv", "name": "Swedish",     "size_mb": 310},
    "DA":    {"hf_repo": "Helsinki-NLP/opus-mt-en-da", "name": "Danish",      "size_mb": 310},
    "FI":    {"hf_repo": "Helsinki-NLP/opus-mt-en-fi", "name": "Finnish",     "size_mb": 310},
    "PT-BR": {"hf_repo": "Helsinki-NLP/opus-mt-en-ROMANCE", "name": "Portuguese (BR)", "size_mb": 310},
    "PT-PT": {"hf_repo": "Helsinki-NLP/opus-mt-en-ROMANCE", "name": "Portuguese (PT)", "size_mb": 310},
    "RO":    {"hf_repo": "Helsinki-NLP/opus-mt-en-ro", "name": "Romanian",    "size_mb": 310},
    "BG":    {"hf_repo": "Helsinki-NLP/opus-mt-en-bg", "name": "Bulgarian",   "size_mb": 310},
    "CS":    {"hf_repo": "Helsinki-NLP/opus-mt-en-cs", "name": "Czech",       "size_mb": 310},
    "ET":    {"hf_repo": "Helsinki-NLP/opus-mt-en-et", "name": "Estonian",     "size_mb": 310},
    "HU":    {"hf_repo": "Helsinki-NLP/opus-mt-en-hu", "name": "Hungarian",   "size_mb": 310},
    "LT":    {"hf_repo": "Helsinki-NLP/opus-mt-en-lt", "name": "Lithuanian",  "size_mb": 310},
    "LV":    {"hf_repo": "Helsinki-NLP/opus-mt-en-lv", "name": "Latvian",     "size_mb": 310},
    "SK":    {"hf_repo": "Helsinki-NLP/opus-mt-en-sk", "name": "Slovak",      "size_mb": 310},
    "SL":    {"hf_repo": "Helsinki-NLP/opus-mt-en-sl", "name": "Slovenian",   "size_mb": 310},
    "EL":    {"hf_repo": "Helsinki-NLP/opus-mt-en-el", "name": "Greek",       "size_mb": 310},
    "TR":    {"hf_repo": "Helsinki-NLP/opus-mt-en-tr", "name": "Turkish",     "size_mb": 310},
    "UK":    {"hf_repo": "Helsinki-NLP/opus-mt-en-uk", "name": "Ukrainian",   "size_mb": 310},
}

# ROMANCE model target language prefixes (required for multi-target OPUS-MT models)
OPUS_ROMANCE_PREFIX: dict[str, str] = {
    "PT-BR": ">>pt<<", "PT-PT": ">>pt<<",
    "ES": ">>es<<", "FR": ">>fr<<", "IT": ">>it<<", "RO": ">>ro<<",
}

# Only download files needed for CTranslate2 conversion
_HF_ALLOW_OPUS: list[str] = [
    "*.json", "*.yml", "*.txt", "*.spm",
    "pytorch_model.bin", "model.safetensors",
]
_HF_ALLOW_M2M: list[str] = [
    "*.json", "*.txt", "*.model",
    "sentencepiece*",
    "pytorch_model*.bin", "model*.safetensors", "model-*.safetensors",
]

# -- M2M-100 config --
M2M_MODEL: dict[str, Any] = {
    "hf_repo": "facebook/m2m100_1.2B",
    "name": "M2M-100 Multilingual (100 languages)",
    "size_mb": 4800,
}

# DeepL target code -> M2M-100 language code
DEEPL_TO_M2M: dict[str, str] = {
    "AR": "ar", "BG": "bg", "CS": "cs", "DA": "da", "DE": "de",
    "EL": "el", "EN-GB": "en", "EN-US": "en", "ES": "es", "ET": "et",
    "FI": "fi", "FR": "fr", "HU": "hu", "ID": "id", "IT": "it",
    "JA": "ja", "KO": "ko", "LT": "lt", "LV": "lv", "NB": "no",
    "NL": "nl", "PL": "pl", "PT-BR": "pt", "PT-PT": "pt", "RO": "ro",
    "RU": "ru", "SK": "sk", "SL": "sl", "SV": "sv", "TR": "tr",
    "UK": "uk", "ZH-HANS": "zh", "ZH-HANT": "zh", "ZH": "zh",
}

# DeepL source code -> M2M-100 source language code (subset)
DEEPL_SRC_TO_M2M: dict[str, str] = {
    "AR": "ar", "BG": "bg", "CS": "cs", "DA": "da", "DE": "de",
    "EL": "el", "EN": "en", "ES": "es", "ET": "et", "FI": "fi",
    "FR": "fr", "HU": "hu", "ID": "id", "IT": "it", "JA": "ja",
    "KO": "ko", "LT": "lt", "LV": "lv", "NB": "no", "NL": "nl",
    "PL": "pl", "PT": "pt", "RO": "ro", "RU": "ru", "SK": "sk",
    "SL": "sl", "SV": "sv", "TR": "tr", "UK": "uk", "ZH": "zh",
}

# Languages where M2M-100 is preferred over OPUS-MT (better quality for these)
M2M_PREFERRED: set[str] = {"AR", "JA", "KO", "ZH", "ZH-HANS", "ZH-HANT", "ID"}

# -- Progress tracking --
_progress: dict[str, dict[str, Any]] = {}
_progress_lock: threading.Lock = threading.Lock()


def _set_progress(key: str, status: str, pct: int = 0, message: str = "") -> None:
    """Update download progress state.

    Args:
        key: Progress key (e.g. ``"opus-ES"`` or ``"m2m100"``).
        status: Status string.
        pct: Percentage complete (0-100).
        message: Human-readable status message.
    """
    with _progress_lock:
        _progress[key] = {"status": status, "pct": pct, "message": message}
    if _cli_mode:
        print(f"PROGRESS:{key}:{status}:{pct}:{message}", flush=True)


def get_progress(key: str) -> dict[str, Any]:
    """Get current download progress.

    Args:
        key: Progress key to query.

    Returns:
        Dict with ``status``, ``pct``, and ``message`` keys.
    """
    with _progress_lock:
        return _progress.get(key, {"status": "idle", "pct": 0, "message": ""})


# -- Path Management --

def get_translate_dir(models_dir: str | Path) -> Path:
    """Return the offline translation models base directory.

    Args:
        models_dir: Base models directory.

    Returns:
        Path to the ``translate/`` subdirectory.
    """
    d = Path(models_dir) / "translate"
    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass  # Directory may not be writable (e.g. Program Files)
    return d


def _opus_dir_name(lang_code: str) -> str:
    """Canonical directory name for an OPUS-MT model.

    ROMANCE targets (PT-BR, PT-PT) share a single model directory.

    Args:
        lang_code: DeepL target language code.

    Returns:
        Directory name string.
    """
    info = OPUS_MODELS.get(lang_code.upper(), {})
    if "ROMANCE" in info.get("hf_repo", ""):
        return "opus-mt-en-romance"
    return f"opus-mt-en-{lang_code.lower()}"


def get_opus_model_path(models_dir: str | Path, lang_code: str) -> Path:
    """Return path to a converted CTranslate2 OPUS-MT model.

    Args:
        models_dir: Base models directory.
        lang_code: DeepL target language code.

    Returns:
        Path to the OPUS-MT model directory.
    """
    return get_translate_dir(models_dir) / _opus_dir_name(lang_code)


def get_m2m_model_path(models_dir: str | Path) -> Path:
    """Return path to the converted CTranslate2 M2M-100 model.

    Args:
        models_dir: Base models directory.

    Returns:
        Path to the M2M-100 model directory.
    """
    return get_translate_dir(models_dir) / "m2m100-1.2b"


def is_opus_available(models_dir: str | Path, lang_code: str) -> bool:
    """Check if an OPUS-MT model is downloaded and converted.

    Args:
        models_dir: Base models directory.
        lang_code: DeepL target language code.

    Returns:
        True if the model's ``model.bin`` exists.
    """
    mp = get_opus_model_path(models_dir, lang_code)
    return (mp / "model.bin").exists()


def is_m2m_available(models_dir: str | Path) -> bool:
    """Check if M2M-100 is downloaded and converted.

    Args:
        models_dir: Base models directory.

    Returns:
        True if the model's ``model.bin`` exists.
    """
    mp = get_m2m_model_path(models_dir)
    return (mp / "model.bin").exists()


def has_opus_model(lang_code: str) -> bool:
    """Check if OPUS-MT has a model registered for this language code.

    Args:
        lang_code: DeepL target language code.

    Returns:
        True if a model is registered.
    """
    return lang_code.upper() in OPUS_MODELS


def get_all_status(models_dir: str | Path) -> dict[str, Any]:
    """Return status of all offline translation models.

    Args:
        models_dir: Base models directory.

    Returns:
        Dict with ``opus`` and ``m2m100`` keys containing model statuses.
    """
    models_dir = str(models_dir)
    result: dict[str, Any] = {"opus": {}, "m2m100": {}}

    for lang, info in OPUS_MODELS.items():
        prog = get_progress(f"opus-{lang}")
        result["opus"][lang] = {
            "name": info["name"],
            "size_mb": info["size_mb"],
            "available": is_opus_available(models_dir, lang),
            "download_status": prog["status"],
            "download_pct": prog["pct"],
            "download_message": prog["message"],
        }

    prog = get_progress("m2m100")
    result["m2m100"] = {
        "name": M2M_MODEL["name"],
        "size_mb": M2M_MODEL["size_mb"],
        "available": is_m2m_available(models_dir),
        "download_status": prog["status"],
        "download_pct": prog["pct"],
        "download_message": prog["message"],
    }

    return result


# -- Dependency Check --

def _check_converter_deps() -> list[str]:
    """Check if conversion dependencies are installed.

    Returns:
        List of missing package names.
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


# -- Download & Conversion --

def download_opus_model(
    models_dir: str | Path,
    lang_code: str,
    on_complete: Callable[[str, bool, str], None] | None = None,
) -> threading.Thread | None:
    """Download an OPUS-MT model and convert to CTranslate2 format.

    Runs in a background thread. Call ``get_progress(f'opus-{lang}')`` to track.

    Args:
        models_dir: Base models directory.
        lang_code: DeepL target language code.
        on_complete: Optional callback ``(key, success, error_msg)``.

    Returns:
        The background thread, or ``None`` if already available.
    """
    lang_code = lang_code.upper()
    key = f"opus-{lang_code}"

    if lang_code not in OPUS_MODELS:
        _set_progress(key, "error", 0, f"No OPUS-MT model for {lang_code}")
        return None

    info = OPUS_MODELS[lang_code]
    output_path = get_opus_model_path(models_dir, lang_code)

    if is_opus_available(models_dir, lang_code):
        _set_progress(key, "ready", 100, "Model already available")
        if on_complete:
            on_complete(key, True, "")
        return None

    def _worker() -> None:
        hf_cache = None  # init so the except handler can reference it safely
        try:
            missing = _check_converter_deps()
            if missing:
                msg = f"Missing: {', '.join(missing)}"
                _set_progress(key, "error", 0, msg)
                if on_complete:
                    on_complete(key, False, msg)
                return

            _set_progress(key, "downloading", 10,
                          f"Downloading OPUS-MT {info['name']}...")
            log.info(f"Downloading OPUS-MT for {lang_code}: {info['hf_repo']}")

            from huggingface_hub import snapshot_download

            # Use short cache path to avoid Windows MAX_PATH (260 char) errors
            hf_cache = _short_hf_cache()

            # Pick up an HF token if the user has set one (handles rate limits + gated models)
            hf_token = (os.environ.get("HUGGING_FACE_HUB_TOKEN")
                        or os.environ.get("HF_TOKEN")
                        or None)

            try:
                hf_local = snapshot_download(
                    repo_id=info["hf_repo"],
                    cache_dir=hf_cache,
                    allow_patterns=_HF_ALLOW_OPUS,
                    token=hf_token,
                )
            except Exception as dl_err:
                err_str = str(dl_err)
                if "401" in err_str or "403" in err_str or "Unauthorized" in err_str:
                    raise RuntimeError(
                        f"HuggingFace blocked the download (401/403). This usually means "
                        f"rate limiting or a gated model. Workaround: create a free HuggingFace "
                        f"account at huggingface.co/join, generate a read token at "
                        f"huggingface.co/settings/tokens, then set the environment variable "
                        f"HF_TOKEN=<your_token> and restart LinguaTaxi. Original error: {err_str[:120]}"
                    )
                raise

            _set_progress(key, "converting", 60,
                          "Converting to CTranslate2 (int8)...")

            if output_path.exists():
                shutil.rmtree(output_path)
            output_path.mkdir(parents=True, exist_ok=True)

            try:
                from ctranslate2.converters import OpusMTConverter
                converter = OpusMTConverter(hf_local)
            except Exception:
                # OpusMTConverter not available in newer ctranslate2 -- fall back
                from ctranslate2.converters import TransformersConverter
                converter = TransformersConverter(
                    hf_local, copy_files=["source.spm", "target.spm"])
            converter.convert(str(output_path), quantization="int8", force=True)

            # Ensure .spm tokenizer files are in output (some converters don't copy them)
            for spm_name in ("source.spm", "target.spm"):
                spm_src = Path(hf_local) / spm_name
                spm_dst = output_path / spm_name
                if spm_src.exists() and not spm_dst.exists():
                    shutil.copy2(str(spm_src), str(spm_dst))

            if not (output_path / "model.bin").exists():
                _set_progress(key, "error", 0, "Conversion produced no model.bin")
                if on_complete:
                    on_complete(key, False, "Conversion produced no model.bin")
                return

            _set_progress(key, "ready", 100, f"OPUS-MT {info['name']} ready")
            log.info(f"OPUS-MT model ready for {lang_code}: {output_path}")

            # Clean up HF cache
            try:
                shutil.rmtree(hf_cache)
            except Exception:
                pass

            if on_complete:
                on_complete(key, True, "")

        except Exception as e:
            # Clean up empty/partial output directory
            if output_path.exists() and not (output_path / "model.bin").exists():
                try:
                    shutil.rmtree(output_path)
                except Exception:
                    pass
            # Clean up HF cache on failure too
            try:
                if hf_cache:
                    shutil.rmtree(hf_cache, ignore_errors=True)
            except Exception:
                pass
            error_msg = str(e)[:200]
            _set_progress(key, "error", 0, error_msg)
            log.error(f"OPUS-MT download failed for {lang_code}: {e}")
            if on_complete:
                on_complete(key, False, error_msg)

    _set_progress(key, "starting", 0, "Starting download...")
    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()
    return thread


def download_m2m_model(
    models_dir: str | Path,
    on_complete: Callable[[str, bool, str], None] | None = None,
) -> threading.Thread | None:
    """Download M2M-100 1.2B and convert to CTranslate2 format.

    Runs in a background thread. Call ``get_progress('m2m100')`` to track.

    Args:
        models_dir: Base models directory.
        on_complete: Optional callback ``(key, success, error_msg)``.

    Returns:
        The background thread, or ``None`` if already available.
    """
    key = "m2m100"
    output_path = get_m2m_model_path(models_dir)

    if is_m2m_available(models_dir):
        _set_progress(key, "ready", 100, "Model already available")
        if on_complete:
            on_complete(key, True, "")
        return None

    def _worker() -> None:
        hf_cache = None  # init so the except handler can reference it safely
        try:
            missing = _check_converter_deps()
            if missing:
                msg = f"Missing: {', '.join(missing)}"
                _set_progress(key, "error", 0, msg)
                if on_complete:
                    on_complete(key, False, msg)
                return

            size_gb = M2M_MODEL["size_mb"] / 1000
            _set_progress(key, "downloading", 5,
                          f"Downloading M2M-100 1.2B (~{size_gb:.1f} GB)...")
            log.info(f"Downloading M2M-100: {M2M_MODEL['hf_repo']}")

            from huggingface_hub import snapshot_download

            # Use short cache path to avoid Windows MAX_PATH (260 char) errors
            hf_cache = _short_hf_cache()

            # Pick up an HF token if the user has set one (handles rate limits + gated models)
            hf_token = (os.environ.get("HUGGING_FACE_HUB_TOKEN")
                        or os.environ.get("HF_TOKEN")
                        or None)

            try:
                hf_local = snapshot_download(
                    repo_id=M2M_MODEL["hf_repo"],
                    cache_dir=hf_cache,
                    allow_patterns=_HF_ALLOW_M2M,
                    token=hf_token,
                )
            except Exception as dl_err:
                err_str = str(dl_err)
                if "401" in err_str or "403" in err_str or "Unauthorized" in err_str:
                    raise RuntimeError(
                        f"HuggingFace blocked the download (401/403). This usually means "
                        f"rate limiting or a gated model. Workaround: create a free HuggingFace "
                        f"account at huggingface.co/join, generate a read token at "
                        f"huggingface.co/settings/tokens, then set the environment variable "
                        f"HF_TOKEN=<your_token> and restart LinguaTaxi. Original error: {err_str[:120]}"
                    )
                raise

            _set_progress(key, "converting", 50,
                          "Converting to CTranslate2 (int8) — this may take 10-20 min...")

            if output_path.exists():
                shutil.rmtree(output_path)
            output_path.mkdir(parents=True, exist_ok=True)

            # M2M100Converter was removed in ctranslate2 >=4.x; use TransformersConverter
            try:
                from ctranslate2.converters import M2M100Converter
                converter = M2M100Converter(hf_local)
            except ImportError:
                from ctranslate2.converters import TransformersConverter
                converter = TransformersConverter(
                    hf_local, copy_files=["sentencepiece.bpe.model"])
            converter.convert(str(output_path), quantization="int8", force=True)

            # Normalize tokenizer filename to `sentencepiece.model`
            sp_dst = output_path / "sentencepiece.model"
            if not sp_dst.exists():
                sp_dst_bpe = output_path / "sentencepiece.bpe.model"
                sp_src_bpe = Path(hf_local) / "sentencepiece.bpe.model"
                if sp_dst_bpe.exists():
                    shutil.move(str(sp_dst_bpe), str(sp_dst))
                elif sp_src_bpe.exists():
                    shutil.copy2(str(sp_src_bpe), str(sp_dst))

            if not (output_path / "model.bin").exists():
                _set_progress(key, "error", 0, "Conversion produced no model.bin")
                if on_complete:
                    on_complete(key, False, "Conversion produced no model.bin")
                return

            _set_progress(key, "ready", 100, "M2M-100 ready")
            log.info(f"M2M-100 model ready: {output_path}")

            try:
                shutil.rmtree(hf_cache)
            except Exception:
                pass

            if on_complete:
                on_complete(key, True, "")

        except Exception as e:
            # Clean up empty/partial output directory
            if output_path.exists() and not (output_path / "model.bin").exists():
                try:
                    shutil.rmtree(output_path)
                except Exception:
                    pass
            # Clean up HF cache on failure too
            try:
                if hf_cache:
                    shutil.rmtree(hf_cache, ignore_errors=True)
            except Exception:
                pass
            error_msg = str(e)[:200]
            _set_progress(key, "error", 0, error_msg)
            log.error(f"M2M-100 download failed: {e}")
            if on_complete:
                on_complete(key, False, error_msg)

    _set_progress(key, "starting", 0, "Starting download...")
    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()
    return thread


# ======================================================
# INFERENCE ENGINE
# ======================================================

# Cache loaded models: {model_path_str: (translator, tokenizer)}
_loaded_models: dict[str, Any] = {}
_models_lock: threading.Lock = threading.Lock()


def _load_opus_model(model_path: str | Path) -> tuple:
    """Load a CTranslate2 OPUS-MT model + sentencepiece tokenizer.

    Args:
        model_path: Path to the model directory.

    Returns:
        Tuple of ``(translator, source_sp, target_sp)``.
    """
    import ctranslate2
    import sentencepiece as spm

    model_path = str(model_path)
    with _models_lock:
        if model_path in _loaded_models:
            return _loaded_models[model_path]

        translator = ctranslate2.Translator(model_path, device="cpu",
                                             compute_type="int8",
                                             inter_threads=1,
                                             intra_threads=_intra_threads)
        sp_path = os.path.join(model_path, "source.spm")
        sp = spm.SentencePieceProcessor()
        sp.Load(sp_path)

        # Target tokenizer (some OPUS-MT models have a separate target.spm)
        tgt_sp_path = os.path.join(model_path, "target.spm")
        tgt_sp = None
        if os.path.exists(tgt_sp_path):
            tgt_sp = spm.SentencePieceProcessor()
            tgt_sp.Load(tgt_sp_path)

        entry = (translator, sp, tgt_sp)
        _loaded_models[model_path] = entry
        return entry


def _load_m2m_model(model_path: str | Path) -> tuple:
    """Load a CTranslate2 M2M-100 model + sentencepiece tokenizer.

    Args:
        model_path: Path to the model directory.

    Returns:
        Tuple of ``(translator, sentencepiece_processor)``.
    """
    import ctranslate2
    import sentencepiece as spm

    model_path = str(model_path)
    with _models_lock:
        if model_path in _loaded_models:
            return _loaded_models[model_path]

        translator = ctranslate2.Translator(model_path, device="cpu",
                                             compute_type="int8",
                                             inter_threads=1,
                                             intra_threads=_intra_threads)
        sp_path = os.path.join(model_path, "sentencepiece.model")
        sp = spm.SentencePieceProcessor()
        sp.Load(sp_path)

        entry = (translator, sp)
        _loaded_models[model_path] = entry
        return entry


def _translate_opus(
    text: str,
    model_path: str | Path,
    target_lang: str | None = None,
) -> str:
    """Translate using an OPUS-MT model.

    For multi-target models (ROMANCE), prepends the target language prefix.

    Args:
        text: Text to translate.
        model_path: Path to the OPUS-MT model directory.
        target_lang: Target language code for prefix selection.

    Returns:
        Translated text.
    """
    translator, src_sp, tgt_sp = _load_opus_model(model_path)
    # Prepend ROMANCE target prefix if needed
    if target_lang and target_lang.upper() in OPUS_ROMANCE_PREFIX:
        text = OPUS_ROMANCE_PREFIX[target_lang.upper()] + " " + text
    tokens = src_sp.Encode(text, out_type=str)
    results = translator.translate_batch([tokens], beam_size=1,
                                          no_repeat_ngram_size=3,
                                          repetition_penalty=1.2)
    output_tokens = results[0].hypotheses[0]
    # Decode with target tokenizer if available, else source
    decoder = tgt_sp if tgt_sp else src_sp
    return decoder.Decode(output_tokens)


def _translate_m2m(
    text: str,
    source_lang: str,
    target_lang: str,
    model_path: str | Path,
) -> str:
    """Translate using M2M-100.

    Args:
        text: Text to translate.
        source_lang: Source language code (DeepL format).
        target_lang: Target language code (DeepL format).
        model_path: Path to the M2M-100 model directory.

    Returns:
        Translated text.
    """
    translator, sp = _load_m2m_model(model_path)
    # M2M-100 uses __lang__ prefix tokens
    src_code = DEEPL_SRC_TO_M2M.get(source_lang, "en")
    tgt_code = DEEPL_TO_M2M.get(target_lang, target_lang.lower().split("-")[0])

    tokens = sp.Encode(text, out_type=str)
    # Source prefix: __src_lang__
    source_tokens = [f"__{src_code}__"] + tokens + ["</s>"]
    target_prefix = [[f"__{tgt_code}__"]]

    results = translator.translate_batch([source_tokens],
                                          target_prefix=target_prefix,
                                          beam_size=1,
                                          no_repeat_ngram_size=3,
                                          repetition_penalty=1.2)
    output_tokens = results[0].hypotheses[0]
    # Remove the language prefix token from output
    if output_tokens and output_tokens[0].startswith("__"):
        output_tokens = output_tokens[1:]
    return sp.Decode(output_tokens)


# Last offline translation failure reason, keyed by (src, tgt, engine).
_last_error: dict[tuple[str, str, str], str] = {}
_last_error_lock: threading.Lock = threading.Lock()


def _record_error(source_upper: str, target_upper: str, engine: str, reason: str) -> None:
    """Record a translation failure for diagnostic purposes.

    Args:
        source_upper: Source language code (uppercase).
        target_upper: Target language code (uppercase).
        engine: Translation engine name.
        reason: Failure reason string.
    """
    key = (source_upper, target_upper, engine)
    with _last_error_lock:
        _last_error[key] = reason
    log.warning(f"Offline translate ({engine}) {source_upper}->{target_upper}: {reason}")


def get_last_offline_error(
    source_lang: str,
    target_lang: str,
    engine: str = "auto",
) -> str:
    """Return the most recent failure reason for this src/tgt/engine, or empty string.

    Args:
        source_lang: Source language code.
        target_lang: Target language code.
        engine: Translation engine (``"auto"``, ``"opus-mt"``, ``"m2m100"``).

    Returns:
        Error reason string, or ``""`` if no error recorded.
    """
    src = source_lang.upper().split("-")[0]
    tgt = target_lang.upper()
    with _last_error_lock:
        return _last_error.get((src, tgt, engine), "")


def diagnose_offline(
    source_lang: str,
    target_lang: str,
    models_dir: str | Path,
    engine: str = "auto",
) -> str:
    """Return a user-readable reason why offline translation would fail.

    Does NOT actually translate -- just checks prerequisites.

    Args:
        source_lang: Source language code.
        target_lang: Target language code.
        models_dir: Base models directory.
        engine: Translation engine to check.

    Returns:
        Diagnostic message, or ``""`` if everything looks OK.
    """
    src = source_lang.upper().split("-")[0]
    tgt = target_lang.upper()
    missing = _check_converter_deps()
    # ctranslate2 + sentencepiece are required for inference, not just conversion
    try:
        import sentencepiece  # noqa: F401
    except ImportError:
        missing.append("sentencepiece")
    if "ctranslate2" in missing or "sentencepiece" in missing:
        return (f"Required packages missing: "
                f"{', '.join(x for x in missing if x in ('ctranslate2','sentencepiece'))}. "
                f"Run: pip install ctranslate2 sentencepiece")
    if engine == "opus-mt":
        if src != "EN":
            return f"OPUS-MT only supports English source, got {src}"
        if tgt not in OPUS_MODELS:
            return f"No OPUS-MT model registered for target {tgt}"
        if not is_opus_available(models_dir, tgt):
            return f"OPUS-MT model for {tgt} is not downloaded"
    elif engine == "m2m100":
        if not is_m2m_available(models_dir):
            return "M2M-100 model is not downloaded"
    else:  # auto
        opus_ok = src == "EN" and tgt in OPUS_MODELS and is_opus_available(models_dir, tgt)
        m2m_ok = is_m2m_available(models_dir)
        if not (opus_ok or m2m_ok):
            hint: list[str] = []
            if src == "EN" and tgt in OPUS_MODELS:
                hint.append(f"OPUS-MT {tgt}")
            hint.append("M2M-100")
            return f"No offline model available for {src}->{tgt}. Download one of: {', '.join(hint)}"
    return ""


def translate_offline(
    text: str,
    source_lang: str,
    target_lang: str,
    models_dir: str | Path,
    engine: str = "auto",
) -> str:
    """Translate text using a local model.

    Args:
        text: Text to translate.
        source_lang: DeepL source language code (e.g. ``"EN"``).
        target_lang: DeepL target language code (e.g. ``"ES"``, ``"ZH-HANS"``).
        models_dir: Path to ``models/`` directory.
        engine: ``"auto"``, ``"opus-mt"``, or ``"m2m100"``.

    Returns:
        Translated text, or ``""`` on failure. Callers can inspect the reason
        via :func:`get_last_offline_error`.
    """
    if not text.strip():
        return ""

    target_upper = target_lang.upper()
    source_upper = source_lang.upper().split("-")[0]  # Strip region for source

    try:
        # Decide which engine to use
        use_opus = False
        use_m2m = False
        model_path = None

        if engine == "opus-mt":
            if source_upper != "EN":
                _record_error(source_upper, target_upper, engine,
                              f"OPUS-MT only supports EN source, got {source_upper}; falling back to M2M-100")
                use_m2m = is_m2m_available(models_dir)
                if not use_m2m:
                    _record_error(source_upper, target_upper, engine,
                                  "M2M-100 not downloaded (required fallback for non-EN source)")
                    return ""
            else:
                use_opus = True
        elif engine == "m2m100":
            use_m2m = True
        else:
            # Auto: OPUS-MT for European, M2M-100 for Asian/Arabic, fallback chain
            if source_upper == "EN" and target_upper in OPUS_MODELS:
                if target_upper not in M2M_PREFERRED and is_opus_available(models_dir, target_upper):
                    use_opus = True
                elif is_m2m_available(models_dir):
                    use_m2m = True
                elif is_opus_available(models_dir, target_upper):
                    use_opus = True  # Fallback to OPUS even for M2M-preferred
            elif is_m2m_available(models_dir):
                use_m2m = True
            elif source_upper == "EN" and target_upper in OPUS_MODELS and is_opus_available(models_dir, target_upper):
                use_opus = True

        if use_opus:
            model_path = get_opus_model_path(models_dir, target_upper)
            if not (model_path / "model.bin").exists():
                # Fallback to M2M if available
                if is_m2m_available(models_dir):
                    use_opus = False
                    use_m2m = True
                else:
                    _record_error(source_upper, target_upper, engine,
                                  f"OPUS-MT model for {target_upper} not downloaded and M2M-100 unavailable")
                    return ""

        if use_opus and model_path:
            return _translate_opus(text, model_path, target_lang=target_upper)
        elif use_m2m:
            model_path = get_m2m_model_path(models_dir)
            if not (model_path / "model.bin").exists():
                _record_error(source_upper, target_upper, engine,
                              "M2M-100 model not downloaded")
                return ""
            return _translate_m2m(text, source_upper, target_upper, model_path)
        else:
            _record_error(source_upper, target_upper, engine,
                          f"No offline model available for {source_upper}->{target_upper} "
                          f"(auto mode found neither OPUS-MT nor M2M-100)")
            return ""

    except ImportError as e:
        _record_error(source_upper, target_upper, engine,
                      f"Missing Python package: {e}. Run: pip install ctranslate2 sentencepiece")
        log.error(f"Offline translation import error ({target_lang}): {e}")
        return ""
    except Exception as e:
        _record_error(source_upper, target_upper, engine, str(e)[:200])
        log.error(f"Offline translation error ({target_lang}): {e}")
        return ""


def delete_opus_model(models_dir: str | Path, lang_code: str) -> bool:
    """Delete a downloaded OPUS-MT model.

    Args:
        models_dir: Base models directory.
        lang_code: DeepL target language code.

    Returns:
        True if the model was deleted.
    """
    mp = get_opus_model_path(models_dir, lang_code)
    if mp.exists():
        mp_str = str(mp)
        with _models_lock:
            _loaded_models.pop(mp_str, None)
            try:
                shutil.rmtree(mp)
            except PermissionError:
                log.warning(f"Could not delete {mp} — model may be in use")
                return False
        log.info(f"Deleted OPUS-MT model for {lang_code}: {mp}")
        return True
    return False


def delete_m2m_model(models_dir: str | Path) -> bool:
    """Delete the downloaded M2M-100 model.

    Args:
        models_dir: Base models directory.

    Returns:
        True if the model was deleted.
    """
    mp = get_m2m_model_path(models_dir)
    if mp.exists():
        mp_str = str(mp)
        with _models_lock:
            _loaded_models.pop(mp_str, None)
            try:
                shutil.rmtree(mp)
            except PermissionError:
                log.warning(f"Could not delete {mp} — model may be in use")
                return False
        log.info(f"Deleted M2M-100 model: {mp}")
        return True
    return False


def get_model_disk_size(path: str | Path) -> int:
    """Return disk size in bytes of a model directory.

    Args:
        path: Path to the model directory.

    Returns:
        Total size in bytes, or 0 if not found.
    """
    path = Path(path)
    if not path.exists():
        return 0
    total = 0
    for f in path.rglob("*"):
        if f.is_file():
            total += f.stat().st_size
    return total


def unload_all() -> None:
    """Unload all cached models to free memory."""
    with _models_lock:
        _loaded_models.clear()
    log.info("All offline translation models unloaded")
