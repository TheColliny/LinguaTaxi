#!/usr/bin/env python3
"""
LinguaTaxi — Model Pre-Download
Detects which speech backend is installed and downloads the appropriate model
so the user doesn't wait on first launch.
"""

import importlib.util, os, sys, time
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
MODELS_DIR = APP_DIR / "models"
MODELS_DIR.mkdir(exist_ok=True)


def download_whisper_model():
    """Pre-download faster-whisper large-v3-turbo model."""
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        return False

    model_name = "large-v3-turbo"
    print(f"\n  Downloading Whisper model: {model_name}")
    print(f"  This is ~1.5 GB and may take several minutes...\n")

    # Always download using CPU mode — just caching the model files.
    # GPU detection happens at runtime in server.py, not here.
    try:
        model = WhisperModel(model_name, device="cpu", compute_type="int8")
        del model
        print(f"\n  [OK] Whisper model '{model_name}' ready!")
        return True
    except Exception as e:
        print(f"\n  [WARNING] Whisper model download failed: {e}")
        print(f"  The model will download automatically on first server start.")
        return False


def download_vosk_model():
    """Pre-download Vosk model."""
    import urllib.request, zipfile

    # Download large model by default (better quality)
    models = {
        "large": {
            "url": "https://alphacephei.com/vosk/models/vosk-model-en-us-0.22.zip",
            "dir": "vosk-model-en-us-0.22",
            "size": "~1.8 GB",
        },
        "small": {
            "url": "https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip",
            "dir": "vosk-model-small-en-us-0.15",
            "size": "~40 MB",
        },
    }

    # Use small model to keep install fast; user can switch to large later
    info = models["small"]

    model_path = MODELS_DIR / info["dir"]
    if model_path.exists():
        print(f"\n  [OK] Vosk model already downloaded: {info['dir']}")
        return True

    zip_path = MODELS_DIR / (info["dir"] + ".zip")

    try:
        print(f"\n  Downloading Vosk model ({info['size']})...")
        print(f"  URL: {info['url']}\n")

        def progress_hook(block_num, block_size, total_size):
            downloaded = block_num * block_size
            if total_size > 0:
                pct = min(100, downloaded * 100 // total_size)
                mb = downloaded / (1024 * 1024)
                total_mb = total_size / (1024 * 1024)
                print(f"\r  {mb:.0f} / {total_mb:.0f} MB ({pct}%)", end="", flush=True)

        urllib.request.urlretrieve(info["url"], str(zip_path), reporthook=progress_hook)
        print()  # newline after progress

        print(f"  Extracting...")
        with zipfile.ZipFile(str(zip_path), "r") as z:
            z.extractall(str(MODELS_DIR))

        zip_path.unlink(missing_ok=True)

        # Verify
        import vosk
        model = vosk.Model(str(model_path))
        del model

        print(f"\n  [OK] Vosk model '{info['dir']}' ready!")
        return True

    except Exception as e:
        print(f"\n  [WARNING] Vosk model download failed: {e}")
        print(f"  The model will download automatically on first server start.")
        zip_path.unlink(missing_ok=True)
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
    args = parser.parse_args()
    main(args.backend)
