"""Model Pre-Download — detects backend and downloads appropriate models.

Detects which speech backend is installed and downloads the appropriate model
so the user does not wait on first launch.

When run from the CLI, emits machine-parseable lines::

    PROGRESS:<key>:<status>:<pct>:<message>
    DONE:<key>:<ok|error>:<message>

These are consumed by :mod:`linguataxi.models.manager` for installer progress
reporting.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import sys
from pathlib import Path
from typing import Any

APP_DIR: Path = Path(__file__).resolve().parent.parent.parent
MODELS_DIR: Path = APP_DIR / "models"
MODELS_DIR.mkdir(exist_ok=True)

# CLI mode flag -- when True, emit machine-parseable PROGRESS/DONE lines
_cli_mode: bool = False

# Multi-language Vosk model mapping: language code -> (model dir name, download URL)
VOSK_MODEL_MAP: dict[str, tuple[str, str]] = {
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


def download_whisper_model(models_dir: str | Path | None = None) -> bool:
    """Pre-download faster-whisper large-v3-turbo model to local models dir.

    Args:
        models_dir: Override the models directory. Defaults to ``APP_DIR / "models"``.

    Returns:
        True if the model is available after this call.
    """
    try:
        import faster_whisper  # noqa: F401 -- verify package is installed
    except ImportError:
        return False

    if models_dir is None:
        models_dir = MODELS_DIR
    else:
        models_dir = Path(models_dir)
        models_dir.mkdir(exist_ok=True, parents=True)

    model_name = "large-v3-turbo"
    local_dir = models_dir / f"faster-whisper-{model_name}"

    task_key = "whisper"

    # Already downloaded locally?
    if (local_dir / "model.bin").exists():
        print(f"\n  [OK] Whisper model already present: {local_dir.name}")
        if _cli_mode:
            print(f"DONE:{task_key}:ok:Already downloaded", flush=True)
        return True

    print(f"\n  Downloading Whisper model: {model_name}")
    print(f"  This is ~1.6 GB and may take several minutes...\n")
    if _cli_mode:
        print(f"PROGRESS:{task_key}:downloading:0:Starting download", flush=True)

    try:
        from huggingface_hub import snapshot_download

        snapshot_download(
            "Systran/faster-whisper-large-v3-turbo",
            local_dir=str(local_dir),
            allow_patterns=["*.bin", "*.json", "*.txt"],
        )

        if (local_dir / "model.bin").exists():
            print(f"\n  [OK] Whisper model '{model_name}' ready!")
            if _cli_mode:
                print(f"DONE:{task_key}:ok:Model ready", flush=True)
            return True
        else:
            print(f"\n  [WARNING] Download completed but model.bin not found.")
            if _cli_mode:
                print(f"DONE:{task_key}:error:model.bin not found after download", flush=True)
            return False

    except Exception as e:
        print(f"\n  [WARNING] Whisper model download failed: {e}")
        print(f"  The model will download automatically on first server start.")
        if _cli_mode:
            print(f"DONE:{task_key}:error:{e}", flush=True)
        return False


def download_vosk_model(
    models_dir: str | Path | None = None,
    lang: str = "en",
) -> bool:
    """Pre-download Vosk model for a specific language.

    Args:
        models_dir: Override the models directory. Defaults to ``APP_DIR / "models"``.
        lang: Language code (e.g. ``"en"``, ``"de"``, ``"fr"``). Defaults to ``"en"``.

    Returns:
        True if the model is available after this call.
    """
    import urllib.request
    import zipfile

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

    task_key = f"vosk-{lang}"

    if model_path.exists():
        print(f"\n  [OK] Vosk model already downloaded: {model_dir_name}")
        if _cli_mode:
            print(f"DONE:{task_key}:ok:Already downloaded", flush=True)
        return True

    try:
        print(f"\n  Downloading Vosk model for language '{lang}': {model_dir_name}")
        print(f"  URL: {download_url}\n")
        if _cli_mode:
            print(f"PROGRESS:{task_key}:downloading:0:Starting download", flush=True)

        req = urllib.request.urlopen(download_url, timeout=120)
        total_size = int(req.headers.get('Content-Length', 0))
        downloaded = 0
        last_pct = -1
        with open(str(zip_path), 'wb') as f:
            while True:
                chunk = req.read(65536)
                if not chunk:
                    break
                f.write(chunk)
                downloaded += len(chunk)
                if total_size > 0:
                    pct = min(99, downloaded * 100 // total_size)
                    mb = downloaded / (1024 * 1024)
                    total_mb = total_size / (1024 * 1024)
                    print(f"\r  {mb:.0f} / {total_mb:.0f} MB ({pct}%)", end="", flush=True)
                    if _cli_mode and pct != last_pct:
                        print(f"\nPROGRESS:{task_key}:downloading:{pct}:{mb:.0f}/{total_mb:.0f} MB", flush=True)
                        last_pct = pct
        print()  # newline after progress

        print(f"  Extracting...")
        if _cli_mode:
            print(f"PROGRESS:{task_key}:extracting:99:Extracting archive", flush=True)

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
        if _cli_mode:
            print(f"DONE:{task_key}:ok:Model ready", flush=True)
        return True

    except Exception as e:
        print(f"\n  [WARNING] Vosk model download failed: {e}")
        print(f"  The model will download automatically on first server start.")
        if _cli_mode:
            print(f"DONE:{task_key}:error:{e}", flush=True)
        zip_path.unlink(missing_ok=True)
        # Clean up partial extraction
        if model_path.exists():
            shutil.rmtree(model_path, ignore_errors=True)
        return False


def main(backend: str = "auto", models_dir: str | Path | None = None) -> None:
    """Main entry point for model pre-download.

    Args:
        backend: Which backend to download for (``"whisper"``, ``"vosk"``, or ``"auto"``).
        models_dir: Override the models directory.
    """
    task_key = "update_models"

    print("=" * 50)
    print("  LinguaTaxi — Model Pre-Download")
    print("=" * 50)
    if _cli_mode:
        print(f"PROGRESS:{task_key}:checking:0:Detecting backends", flush=True)

    has_whisper = importlib.util.find_spec("faster_whisper") is not None
    has_vosk = importlib.util.find_spec("vosk") is not None

    ok = False
    if backend == "whisper":
        if has_whisper:
            print("\n  Backend: faster-whisper (GPU/CPU)")
            if _cli_mode:
                print(f"PROGRESS:{task_key}:downloading:10:Downloading Whisper model", flush=True)
            ok = download_whisper_model(models_dir)
        else:
            print("\n  [WARNING] faster-whisper not installed!")

    elif backend == "vosk":
        if has_vosk:
            print("\n  Backend: Vosk (CPU)")
            if _cli_mode:
                print(f"PROGRESS:{task_key}:downloading:10:Downloading Vosk model", flush=True)
            ok = download_vosk_model(models_dir)
        else:
            print("\n  [WARNING] vosk not installed!")

    else:
        # Auto-detect
        if has_whisper:
            print("\n  Backend: faster-whisper (GPU/CPU)")
            if _cli_mode:
                print(f"PROGRESS:{task_key}:downloading:10:Downloading Whisper model", flush=True)
            ok = download_whisper_model(models_dir)
            if not ok and has_vosk:
                print("\n  Whisper failed, trying Vosk as backup...")
                if _cli_mode:
                    print(f"PROGRESS:{task_key}:downloading:50:Downloading Vosk model", flush=True)
                ok = download_vosk_model(models_dir)
        elif has_vosk:
            print("\n  Backend: Vosk (CPU)")
            if _cli_mode:
                print(f"PROGRESS:{task_key}:downloading:10:Downloading Vosk model", flush=True)
            ok = download_vosk_model(models_dir)
        else:
            print("\n  [WARNING] No speech backend found!")
            print("  Run 'Repair Dependencies' from Start Menu to fix.")

    print("\n" + "=" * 50)
    print("  Model setup complete!")
    print("=" * 50)

    if _cli_mode:
        if ok:
            print(f"DONE:{task_key}:ok:Models up to date", flush=True)
        else:
            print(f"DONE:{task_key}:error:Some models could not be verified", flush=True)


if __name__ == "__main__":
    _cli_mode = True

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
        models_dir_path = Path(args.models_dir) if args.models_dir else APP_DIR / "models"
        print("=" * 50)
        print("  LinguaTaxi — Vosk Model Download")
        print("=" * 50)
        download_vosk_model(models_dir_path, lang=args.vosk_lang)
        print("\n" + "=" * 50)
        print("  Download complete!")
        print("=" * 50)
    else:
        models_dir_path = Path(args.models_dir) if args.models_dir else None
        main(args.backend, models_dir_path)
