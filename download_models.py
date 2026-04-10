#!/usr/bin/env python3
"""
LinguaTaxi — Model Pre-Download
Detects which speech backend is installed and downloads the appropriate model
so the user doesn't wait on first launch.
"""

import importlib.util, os, shutil, sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
MODELS_DIR = APP_DIR / "models"
MODELS_DIR.mkdir(exist_ok=True)

# Multi-language Vosk model mapping: language code -> (model dir name, download URL)
VOSK_MODEL_MAP = {
    "en": ("vosk-model-small-en-us-0.15", "https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip"),
    "de": ("vosk-model-small-de-0.15", "https://alphacephei.com/vosk/models/vosk-model-small-de-0.15.zip"),
    "fr": ("vosk-model-small-fr-0.22", "https://alphacephei.com/vosk/models/vosk-model-small-fr-0.22.zip"),
    "es": ("vosk-model-small-es-0.42", "https://alphacephei.com/vosk/models/vosk-model-small-es-0.42.zip"),
    "ru": ("vosk-model-small-ru-0.22", "https://alphacephei.com/vosk/models/vosk-model-small-ru-0.22.zip"),
    "it": ("vosk-model-small-it-0.22", "https://alphacephei.com/vosk/models/vosk-model-small-it-0.22.zip"),
    "ja": ("vosk-model-small-ja-0.22", "https://alphacephei.com/vosk/models/vosk-model-small-ja-0.22.zip"),
    "zh": ("vosk-model-small-cn-0.22", "https://alphacephei.com/vosk/models/vosk-model-small-cn-0.22.zip"),
    "ar": ("vosk-model-ar-mgb2-0.4", "https://alphacephei.com/vosk/models/vosk-model-ar-mgb2-0.4.zip"),
    "pt": ("vosk-model-small-pt-0.3", "https://alphacephei.com/vosk/models/vosk-model-small-pt-0.3.zip"),
    "tr": ("vosk-model-small-tr-0.3", "https://alphacephei.com/vosk/models/vosk-model-small-tr-0.3.zip"),
    "ko": ("vosk-model-small-ko-0.22", "https://alphacephei.com/vosk/models/vosk-model-small-ko-0.22.zip"),
}


def download_whisper_model(models_dir=None):
    """Pre-download faster-whisper large-v3-turbo model to local models dir."""
    try:
        import faster_whisper  # noqa: F401 — verify package is installed
    except ImportError:
        return False

    if models_dir is None:
        models_dir = MODELS_DIR
    else:
        models_dir = Path(models_dir)
        models_dir.mkdir(exist_ok=True, parents=True)

    model_name = "large-v3-turbo"
    local_dir = models_dir / f"faster-whisper-{model_name}"

    # Already downloaded locally?
    if (local_dir / "model.bin").exists():
        print(f"\n  [OK] Whisper model already present: {local_dir.name}")
        return True

    print(f"\n  Downloading Whisper model: {model_name}")
    print(f"  This is ~1.5 GB and may take several minutes...\n")

    try:
        from huggingface_hub import snapshot_download

        snapshot_download(
            "Systran/faster-whisper-large-v3-turbo",
            local_dir=str(local_dir),
            allow_patterns=["*.bin", "*.json", "*.txt"],
        )

        if (local_dir / "model.bin").exists():
            print(f"\n  [OK] Whisper model '{model_name}' ready!")
            return True
        else:
            print(f"\n  [WARNING] Download completed but model.bin not found.")
            return False

    except Exception as e:
        print(f"\n  [WARNING] Whisper model download failed: {e}")
        print(f"  The model will download automatically on first server start.")
        return False


def download_vosk_model(models_dir=None, lang="en"):
    """Pre-download Vosk model for a specific language.

    Args:
        models_dir: Override the models directory (defaults to APP_DIR / "models")
        lang: Language code (e.g., "en", "de", "fr"). Defaults to "en".
    """
    import urllib.request, zipfile

    if models_dir is None:
        models_dir = MODELS_DIR
    else:
        models_dir = Path(models_dir)
        models_dir.mkdir(exist_ok=True, parents=True)

    # Look up model info from VOSK_MODEL_MAP
    if lang not in VOSK_MODEL_MAP:
        print(f"\n  [ERROR] Unsupported language code: '{lang}'")
        print(f"  Supported languages: {', '.join(sorted(VOSK_MODEL_MAP.keys()))}")
        return False

    model_dir_name, download_url = VOSK_MODEL_MAP[lang]
    model_path = models_dir / model_dir_name
    zip_path = models_dir / (model_dir_name + ".zip")

    if model_path.exists():
        print(f"\n  [OK] Vosk model already downloaded: {model_dir_name}")
        return True

    try:
        print(f"\n  Downloading Vosk model for language '{lang}': {model_dir_name}")
        print(f"  URL: {download_url}\n")

        def progress_hook(block_num, block_size, total_size):
            downloaded = block_num * block_size
            if total_size > 0:
                pct = min(100, downloaded * 100 // total_size)
                mb = downloaded / (1024 * 1024)
                total_mb = total_size / (1024 * 1024)
                print(f"\r  {mb:.0f} / {total_mb:.0f} MB ({pct}%)", end="", flush=True)

        req = urllib.request.urlopen(download_url, timeout=120)
        total_size = int(req.headers.get('Content-Length', 0))
        downloaded = 0
        with open(str(zip_path), 'wb') as f:
            while True:
                chunk = req.read(65536)
                if not chunk:
                    break
                f.write(chunk)
                downloaded += len(chunk)
                if total_size > 0:
                    pct = min(100, downloaded * 100 // total_size)
                    mb = downloaded / (1024 * 1024)
                    total_mb = total_size / (1024 * 1024)
                    print(f"\r  {mb:.0f} / {total_mb:.0f} MB ({pct}%)", end="", flush=True)
        print()  # newline after progress

        print(f"  Extracting...")
        with zipfile.ZipFile(str(zip_path), "r") as z:
            # Validate paths to prevent zip-slip (path traversal)
            for member in z.namelist():
                member_path = (models_dir / member).resolve()
                if not str(member_path).startswith(str(models_dir.resolve())):
                    raise ValueError(f"Zip contains unsafe path: {member}")
            z.extractall(str(models_dir))

        zip_path.unlink(missing_ok=True)

        # Verify
        import vosk
        model = vosk.Model(str(model_path))
        del model

        print(f"\n  [OK] Vosk model '{model_dir_name}' ready!")
        return True

    except Exception as e:
        print(f"\n  [WARNING] Vosk model download failed: {e}")
        print(f"  The model will download automatically on first server start.")
        zip_path.unlink(missing_ok=True)
        # Clean up partial extraction
        if model_path.exists():
            shutil.rmtree(model_path, ignore_errors=True)
        return False


def main(backend="auto"):
    print("=" * 50)
    print("  LinguaTaxi — Model Pre-Download")
    print("=" * 50)

    has_whisper = importlib.util.find_spec("faster_whisper") is not None
    has_vosk = importlib.util.find_spec("vosk") is not None

    if backend == "whisper":
        if has_whisper:
            print("\n  Backend: faster-whisper (GPU/CPU)")
            download_whisper_model()
        else:
            print("\n  [WARNING] faster-whisper not installed!")

    elif backend == "vosk":
        if has_vosk:
            print("\n  Backend: Vosk (CPU)")
            download_vosk_model()
        else:
            print("\n  [WARNING] vosk not installed!")

    else:
        # Auto-detect
        if has_whisper:
            print("\n  Backend: faster-whisper (GPU/CPU)")
            ok = download_whisper_model()
            if not ok and has_vosk:
                print("\n  Whisper failed, trying Vosk as backup...")
                download_vosk_model()
        elif has_vosk:
            print("\n  Backend: Vosk (CPU)")
            download_vosk_model()
        else:
            print("\n  [WARNING] No speech backend found!")
            print("  Run 'Repair Dependencies' from Start Menu to fix.")

    print("\n" + "=" * 50)
    print("  Model setup complete!")
    print("=" * 50)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="LinguaTaxi — Model Pre-Download")
    parser.add_argument("--backend", choices=["whisper", "vosk", "auto"], default="auto",
                        help="Which backend model to download (default: auto-detect)")
    parser.add_argument("--vosk-lang", type=str, default=None,
                        help="Download Vosk model for this language code (e.g., de, fr, ar)")
    parser.add_argument("--models-dir", type=str, default=None,
                        help="Override models directory path")
    args = parser.parse_args()

    # If --vosk-lang is specified, download that language and exit
    if args.vosk_lang:
        models_dir = Path(args.models_dir) if args.models_dir else APP_DIR / "models"
        print("=" * 50)
        print("  LinguaTaxi — Vosk Model Download")
        print("=" * 50)
        download_vosk_model(models_dir, lang=args.vosk_lang)
        print("\n" + "=" * 50)
        print("  Download complete!")
        print("=" * 50)
    else:
        main(args.backend)
