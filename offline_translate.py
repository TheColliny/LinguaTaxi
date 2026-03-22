#!/usr/bin/env python3
"""
LinguaTaxi — Offline Translation Engine

Provides offline machine translation using OPUS-MT (per-language-pair) and
M2M-100 (multilingual fallback). Models run on CPU via CTranslate2, leaving
the GPU free for Whisper speech recognition.

OPUS-MT: ~310 MB download per pair, ~75 MB installed, ~35-50ms/sentence — best for European languages
M2M-100 1.2B: ~4.8 GB download, ~1.2 GB installed, ~150-300ms/sentence — covers 100 languages

Requires: ctranslate2, sentencepiece, huggingface_hub
For download+conversion: also needs transformers, torch (CPU)
"""

import argparse
import json
import logging
import os
import shutil
import sys
import tempfile
import threading
from pathlib import Path

log = logging.getLogger("livecaption")

# CLI mode flag — when True, _set_progress also prints machine-parseable lines
_cli_mode = False


def _short_hf_cache():
    """Return a short temp path for HuggingFace downloads.
    Windows MAX_PATH (260 chars) breaks with long HF cache paths like
    C:\\Users\\...\\Temp\\linguataxi_hf_XXXX\\models--Helsinki-NLP--opus-mt-en-es\\snapshots\\<hash>\\...
    Using C:\\tmp\\lt_hf keeps total paths well under the limit."""
    if sys.platform == "win32":
        d = Path("C:/tmp/lt_hf")
        d.mkdir(parents=True, exist_ok=True)
        return str(d)
    return tempfile.mkdtemp(prefix="lt_hf_")

# ── OPUS-MT Model Registry ──
# Maps DeepL target lang code → HuggingFace repo and metadata
# Only includes pairs where OPUS-MT has good quality (European + common)
OPUS_MODELS = {
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

# Only download files needed for CTranslate2 conversion
_HF_ALLOW_OPUS = [
    "*.json", "*.yml", "*.txt", "*.spm",
    "pytorch_model.bin", "model.safetensors",
]
_HF_ALLOW_M2M = [
    "*.json", "*.txt", "*.model",
    "sentencepiece*",
    "pytorch_model*.bin", "model*.safetensors", "model-*.safetensors",
]

# ── M2M-100 config ──
M2M_MODEL = {
    "hf_repo": "facebook/m2m100_1.2B",
    "name": "M2M-100 Multilingual (100 languages)",
    "size_mb": 4800,
}

# DeepL target code → M2M-100 language code
DEEPL_TO_M2M = {
    "AR": "ar", "BG": "bg", "CS": "cs", "DA": "da", "DE": "de",
    "EL": "el", "EN-GB": "en", "EN-US": "en", "ES": "es", "ET": "et",
    "FI": "fi", "FR": "fr", "HU": "hu", "ID": "id", "IT": "it",
    "JA": "ja", "KO": "ko", "LT": "lt", "LV": "lv", "NB": "no",
    "NL": "nl", "PL": "pl", "PT-BR": "pt", "PT-PT": "pt", "RO": "ro",
    "RU": "ru", "SK": "sk", "SL": "sl", "SV": "sv", "TR": "tr",
    "UK": "uk", "ZH-HANS": "zh", "ZH-HANT": "zh", "ZH": "zh",
}

# DeepL source code → M2M-100 source language code (subset)
DEEPL_SRC_TO_M2M = {
    "AR": "ar", "BG": "bg", "CS": "cs", "DA": "da", "DE": "de",
    "EL": "el", "EN": "en", "ES": "es", "ET": "et", "FI": "fi",
    "FR": "fr", "HU": "hu", "ID": "id", "IT": "it", "JA": "ja",
    "KO": "ko", "LT": "lt", "LV": "lv", "NB": "no", "NL": "nl",
    "PL": "pl", "PT": "pt", "RO": "ro", "RU": "ru", "SK": "sk",
    "SL": "sl", "SV": "sv", "TR": "tr", "UK": "uk", "ZH": "zh",
}

# Languages where M2M-100 is preferred over OPUS-MT (better quality for these)
M2M_PREFERRED = {"AR", "JA", "KO", "ZH", "ZH-HANS", "ZH-HANT", "ID"}

# ── Progress tracking ──
_progress = {}
_progress_lock = threading.Lock()


def _set_progress(key, status, pct=0, message=""):
    with _progress_lock:
        _progress[key] = {"status": status, "pct": pct, "message": message}
    if _cli_mode:
        print(f"PROGRESS:{key}:{status}:{pct}:{message}", flush=True)


def get_progress(key):
    with _progress_lock:
        return _progress.get(key, {"status": "idle", "pct": 0, "message": ""})


# ── Path Management ──

def get_translate_dir(models_dir):
    """Return the offline translation models base directory."""
    d = Path(models_dir) / "translate"
    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass  # Directory may not be writable (e.g. Program Files)
    return d


def _opus_dir_name(lang_code):
    """Canonical directory name for an OPUS-MT model."""
    # Normalize: PT-BR → pt-br, ES → es
    return f"opus-mt-en-{lang_code.lower()}"


def get_opus_model_path(models_dir, lang_code):
    """Return path to a converted CTranslate2 OPUS-MT model."""
    return get_translate_dir(models_dir) / _opus_dir_name(lang_code)


def get_m2m_model_path(models_dir):
    """Return path to the converted CTranslate2 M2M-100 model."""
    return get_translate_dir(models_dir) / "m2m100-1.2b"


def is_opus_available(models_dir, lang_code):
    """Check if an OPUS-MT model is downloaded and converted."""
    mp = get_opus_model_path(models_dir, lang_code)
    return (mp / "model.bin").exists()


def is_m2m_available(models_dir):
    """Check if M2M-100 is downloaded and converted."""
    mp = get_m2m_model_path(models_dir)
    return (mp / "model.bin").exists()


def has_opus_model(lang_code):
    """Check if OPUS-MT has a model for this language code."""
    return lang_code.upper() in OPUS_MODELS


def get_all_status(models_dir):
    """Return status of all offline translation models."""
    models_dir = str(models_dir)
    result = {"opus": {}, "m2m100": {}}

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


# ── Dependency Check ──

def _check_converter_deps():
    """Check if conversion dependencies are installed."""
    missing = []
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


# ── Download & Conversion ──

def download_opus_model(models_dir, lang_code, on_complete=None):
    """Download an OPUS-MT model and convert to CTranslate2 format.
    Runs in a background thread. Call get_progress(f'opus-{lang}') to track.
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

    def _worker():
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

            hf_local = snapshot_download(
                repo_id=info["hf_repo"],
                cache_dir=hf_cache,
                allow_patterns=_HF_ALLOW_OPUS,
            )

            _set_progress(key, "converting", 60,
                          "Converting to CTranslate2 (int8)...")

            if output_path.exists():
                shutil.rmtree(output_path)
            output_path.mkdir(parents=True, exist_ok=True)

            try:
                from ctranslate2.converters import OpusMTConverter
                converter = OpusMTConverter(hf_local)
            except (ImportError, Exception):
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
            error_msg = str(e)[:200]
            _set_progress(key, "error", 0, error_msg)
            log.error(f"OPUS-MT download failed for {lang_code}: {e}")
            if on_complete:
                on_complete(key, False, error_msg)

    _set_progress(key, "starting", 0, "Starting download...")
    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()
    return thread


def download_m2m_model(models_dir, on_complete=None):
    """Download M2M-100 1.2B and convert to CTranslate2 format.
    Runs in a background thread. Call get_progress('m2m100') to track.
    """
    key = "m2m100"
    output_path = get_m2m_model_path(models_dir)

    if is_m2m_available(models_dir):
        _set_progress(key, "ready", 100, "Model already available")
        if on_complete:
            on_complete(key, True, "")
        return None

    def _worker():
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

            hf_local = snapshot_download(
                repo_id=M2M_MODEL["hf_repo"],
                cache_dir=hf_cache,
                allow_patterns=_HF_ALLOW_M2M,
            )

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
                    hf_local, copy_files=["sentencepiece.model"])
            converter.convert(str(output_path), quantization="int8", force=True)

            # Ensure sentencepiece.model is in output (some converters don't copy it)
            sp_src = Path(hf_local) / "sentencepiece.model"
            sp_dst = output_path / "sentencepiece.model"
            if sp_src.exists() and not sp_dst.exists():
                shutil.copy2(str(sp_src), str(sp_dst))

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
            error_msg = str(e)[:200]
            _set_progress(key, "error", 0, error_msg)
            log.error(f"M2M-100 download failed: {e}")
            if on_complete:
                on_complete(key, False, error_msg)

    _set_progress(key, "starting", 0, "Starting download...")
    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()
    return thread


# ══════════════════════════════════════════════
# INFERENCE ENGINE
# ══════════════════════════════════════════════

# Cache loaded models: {model_path_str: (translator, tokenizer)}
_loaded_models = {}
_models_lock = threading.Lock()


def _load_opus_model(model_path):
    """Load a CTranslate2 OPUS-MT model + sentencepiece tokenizer."""
    import ctranslate2
    import sentencepiece as spm

    model_path = str(model_path)
    with _models_lock:
        if model_path in _loaded_models:
            return _loaded_models[model_path]

    translator = ctranslate2.Translator(model_path, device="cpu",
                                         compute_type="int8")
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
    with _models_lock:
        _loaded_models[model_path] = entry
    return entry


def _load_m2m_model(model_path):
    """Load a CTranslate2 M2M-100 model + sentencepiece tokenizer."""
    import ctranslate2
    import sentencepiece as spm

    model_path = str(model_path)
    with _models_lock:
        if model_path in _loaded_models:
            return _loaded_models[model_path]

    translator = ctranslate2.Translator(model_path, device="cpu",
                                         compute_type="int8")
    sp_path = os.path.join(model_path, "sentencepiece.model")
    sp = spm.SentencePieceProcessor()
    sp.Load(sp_path)

    entry = (translator, sp)
    with _models_lock:
        _loaded_models[model_path] = entry
    return entry


def _translate_opus(text, model_path):
    """Translate using an OPUS-MT model."""
    translator, src_sp, tgt_sp = _load_opus_model(model_path)
    tokens = src_sp.Encode(text, out_type=str)
    results = translator.translate_batch([tokens])
    output_tokens = results[0].hypotheses[0]
    # Decode with target tokenizer if available, else source
    decoder = tgt_sp if tgt_sp else src_sp
    return decoder.Decode(output_tokens)


def _translate_m2m(text, source_lang, target_lang, model_path):
    """Translate using M2M-100."""
    translator, sp = _load_m2m_model(model_path)
    # M2M-100 uses __lang__ prefix tokens
    src_code = DEEPL_SRC_TO_M2M.get(source_lang, "en")
    tgt_code = DEEPL_TO_M2M.get(target_lang, target_lang.lower().split("-")[0])

    tokens = sp.Encode(text, out_type=str)
    # Source prefix: __src_lang__
    source_tokens = [f"__{src_code}__"] + tokens + ["</s>"]
    target_prefix = [[f"__{tgt_code}__"]]

    results = translator.translate_batch([source_tokens],
                                          target_prefix=target_prefix)
    output_tokens = results[0].hypotheses[0]
    # Remove the language prefix token from output
    if output_tokens and output_tokens[0].startswith("__"):
        output_tokens = output_tokens[1:]
    return sp.Decode(output_tokens)


def translate_offline(text, source_lang, target_lang, models_dir, engine="auto"):
    """Translate text using a local model.

    Args:
        text: Text to translate
        source_lang: DeepL source language code (e.g. "EN")
        target_lang: DeepL target language code (e.g. "ES", "ZH-HANS")
        models_dir: Path to models/ directory
        engine: "auto", "opus-mt", or "m2m100"

    Returns:
        Translated text, or "" on failure
    """
    if not text.strip():
        return ""

    target_upper = target_lang.upper()
    source_upper = source_lang.upper().split("-")[0]  # Strip region for source

    try:
        # Decide which engine to use
        use_opus = False
        use_m2m = False

        if engine == "opus-mt":
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
                    return ""

        if use_opus:
            return _translate_opus(text, model_path)
        elif use_m2m:
            model_path = get_m2m_model_path(models_dir)
            if not (model_path / "model.bin").exists():
                return ""
            return _translate_m2m(text, source_upper, target_upper, model_path)
        else:
            return ""

    except Exception as e:
        log.error(f"Offline translation error ({target_lang}): {e}")
        return ""


def delete_opus_model(models_dir, lang_code):
    """Delete a downloaded OPUS-MT model. Returns True if deleted."""
    mp = get_opus_model_path(models_dir, lang_code)
    if mp.exists():
        # Unload if cached
        mp_str = str(mp)
        with _models_lock:
            _loaded_models.pop(mp_str, None)
        shutil.rmtree(mp)
        log.info(f"Deleted OPUS-MT model for {lang_code}: {mp}")
        return True
    return False


def delete_m2m_model(models_dir):
    """Delete the downloaded M2M-100 model. Returns True if deleted."""
    mp = get_m2m_model_path(models_dir)
    if mp.exists():
        mp_str = str(mp)
        with _models_lock:
            _loaded_models.pop(mp_str, None)
        shutil.rmtree(mp)
        log.info(f"Deleted M2M-100 model: {mp}")
        return True
    return False


def get_model_disk_size(path):
    """Return disk size in bytes of a model directory."""
    path = Path(path)
    if not path.exists():
        return 0
    total = 0
    for f in path.rglob("*"):
        if f.is_file():
            total += f.stat().st_size
    return total


def unload_all():
    """Unload all cached models to free memory."""
    with _models_lock:
        _loaded_models.clear()
    log.info("All offline translation models unloaded")


# ══════════════════════════════════════════════
# CLI MODE
# ══════════════════════════════════════════════

if __name__ == "__main__":
    _cli_mode = True
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s",
                        datefmt="%H:%M:%S")

    parser = argparse.ArgumentParser(
        description="LinguaTaxi — Offline Translation Model Manager")
    parser.add_argument("--list", action="store_true",
                        help="List all models and their status as JSON")
    parser.add_argument("--download-opus", nargs="+", metavar="LANG",
                        help="Download OPUS-MT models for given languages (e.g. ES FR DE)")
    parser.add_argument("--download-m2m", action="store_true",
                        help="Download M2M-100 1.2B multilingual model")
    parser.add_argument("--delete-opus", nargs="+", metavar="LANG",
                        help="Delete OPUS-MT models for given languages")
    parser.add_argument("--delete-m2m", action="store_true",
                        help="Delete M2M-100 model")
    parser.add_argument("--models-dir",
                        default=str(Path(__file__).parent / "models"),
                        help="Models directory")
    parser.add_argument("--test", metavar="TEXT",
                        help="Test translate TEXT from EN to ES (or --target)")
    parser.add_argument("--target", default="ES",
                        help="Target language for --test (default: ES)")
    args = parser.parse_args()

    models_dir = Path(args.models_dir)
    models_dir.mkdir(parents=True, exist_ok=True)

    if args.list:
        print(json.dumps(get_all_status(str(models_dir))))
        sys.exit(0)

    _any_failed = False

    if args.download_opus:
        for lang in args.download_opus:
            lang = lang.upper()
            key = f"opus-{lang}"
            if lang not in OPUS_MODELS:
                _set_progress(key, "error", 0, f"No OPUS-MT model for {lang}")
                print(f"DONE:{key}:error:No OPUS-MT model for {lang}", flush=True)
                _any_failed = True
                continue

            if is_opus_available(str(models_dir), lang):
                _set_progress(key, "ready", 100, "Already downloaded")
                print(f"DONE:{key}:ok:Already downloaded", flush=True)
                continue

            t = download_opus_model(str(models_dir), lang)
            if t:
                t.join()

            prog = get_progress(key)
            if prog["status"] == "ready":
                print(f"DONE:{key}:ok:{prog['message']}", flush=True)
            else:
                print(f"DONE:{key}:error:{prog['message']}", flush=True)
                _any_failed = True

    if args.download_m2m:
        key = "m2m100"
        if is_m2m_available(str(models_dir)):
            _set_progress(key, "ready", 100, "Already downloaded")
            print(f"DONE:{key}:ok:Already downloaded", flush=True)
        else:
            t = download_m2m_model(str(models_dir))
            if t:
                t.join()

            prog = get_progress(key)
            if prog["status"] == "ready":
                print(f"DONE:{key}:ok:{prog['message']}", flush=True)
            else:
                print(f"DONE:{key}:error:{prog['message']}", flush=True)
                _any_failed = True

    if args.delete_opus:
        for lang in args.delete_opus:
            lang = lang.upper()
            if delete_opus_model(str(models_dir), lang):
                print(f"DONE:opus-{lang}:deleted:OK", flush=True)
            else:
                print(f"DONE:opus-{lang}:not_found:Model not installed", flush=True)

    if args.delete_m2m:
        if delete_m2m_model(str(models_dir)):
            print("DONE:m2m100:deleted:OK", flush=True)
        else:
            print("DONE:m2m100:not_found:Model not installed", flush=True)

    if args.test:
        result = translate_offline(args.test, "EN", args.target,
                                    str(models_dir))
        if result:
            print(f"Translation ({args.target}): {result}")
        else:
            print("Translation failed — no model available")

    has_action = (args.list or args.download_opus or args.download_m2m
                  or args.delete_opus or args.delete_m2m or args.test)
    if not has_action:
        parser.print_help()

    if _any_failed:
        sys.exit(1)
