"""Language-Tuned Whisper Model Manager entry point — delegates to linguataxi.models.tuned."""

from linguataxi.models.tuned import (  # noqa: F401
    TUNED_MODELS,
    get_progress,
    get_tuned_dir,
    get_model_path,
    is_available,
    get_all_status,
    delete_model,
    get_model_disk_size,
    pick_quantization,
    detect_vram,
    download_and_convert,
)

if __name__ == "__main__":
    from linguataxi.models import tuned as _mod
    _mod._cli_mode = True

    import argparse
    import json
    import logging
    import sys
    from pathlib import Path

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s",
                        datefmt="%H:%M:%S")

    parser = argparse.ArgumentParser(
        description="LinguaTaxi — Language-Tuned Model Manager")
    parser.add_argument("--list", action="store_true",
                        help="List available models as JSON")
    parser.add_argument("--download", nargs="+", metavar="LANG",
                        help="Download models for given languages (e.g. ES FR DE)")
    parser.add_argument("--delete", nargs="+", metavar="LANG",
                        help="Delete tuned models for given languages (e.g. ES FR)")
    parser.add_argument("--models-dir",
                        default=str(Path(__file__).parent / "models"),
                        help="Models directory")
    args = parser.parse_args()

    models_dir = Path(args.models_dir)
    models_dir.mkdir(parents=True, exist_ok=True)

    if args.list:
        print(json.dumps(get_all_status(models_dir)))
        sys.exit(0)

    if args.download:
        vram = detect_vram()
        print(f"  VRAM detected: {vram} MB", flush=True)
        _any_failed = False

        for lang in args.download:
            lang = lang.upper()
            if lang not in TUNED_MODELS:
                _mod._set_progress(lang, "error", 0, f"Unknown language code: {lang}")
                print(f"DONE:{lang}:error:Unknown language code: {lang}", flush=True)
                _any_failed = True
                continue

            if is_available(models_dir, lang):
                _mod._set_progress(lang, "ready", 100, "Already downloaded")
                print(f"DONE:{lang}:ok:Already downloaded", flush=True)
                continue

            t = download_and_convert(models_dir, lang, vram_mb=vram)
            if t:
                t.join()

            prog = get_progress(lang)
            if prog["status"] == "ready":
                print(f"DONE:{lang}:ok:{prog['message']}", flush=True)
            else:
                print(f"DONE:{lang}:error:{prog['message']}", flush=True)
                _any_failed = True

        sys.exit(1 if _any_failed else 0)

    if args.delete:
        for lang in args.delete:
            lang = lang.upper()
            if delete_model(models_dir, lang):
                print(f"DONE:{lang}:deleted:OK", flush=True)
            else:
                print(f"DONE:{lang}:not_found:Model not installed", flush=True)
        sys.exit(0)

    if not args.list and not args.download and not args.delete:
        parser.print_help()
