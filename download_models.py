"""Model Pre-Download entry point — delegates to linguataxi.models.downloader."""

from linguataxi.models.downloader import (  # noqa: F401
    download_whisper_model,
    download_vosk_model,
    VOSK_MODEL_MAP,
    main,
)

if __name__ == "__main__":
    from linguataxi.models import downloader as _mod
    _mod._cli_mode = True

    import argparse
    from pathlib import Path

    parser = argparse.ArgumentParser(description="LinguaTaxi — Model Pre-Download")
    parser.add_argument("--backend", choices=["whisper", "vosk", "auto"], default="auto",
                        help="Which backend model to download (default: auto-detect)")
    parser.add_argument("--vosk-lang", type=str, default=None,
                        help="Download Vosk model for this language code (e.g., de, fr, ar)")
    parser.add_argument("--models-dir", type=str, default=None,
                        help="Override models directory path")
    args = parser.parse_args()

    if args.vosk_lang:
        models_dir = Path(args.models_dir) if args.models_dir else _mod.APP_DIR / "models"
        print("=" * 50)
        print("  LinguaTaxi — Vosk Model Download")
        print("=" * 50)
        download_vosk_model(models_dir, lang=args.vosk_lang)
        print("\n" + "=" * 50)
        print("  Download complete!")
        print("=" * 50)
    else:
        models_dir = Path(args.models_dir) if args.models_dir else None
        main(args.backend, models_dir)
