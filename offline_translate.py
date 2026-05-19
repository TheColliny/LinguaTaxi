"""Offline Translation Engine entry point — delegates to linguataxi.models.offline_translate."""

from linguataxi.models.offline_translate import (  # noqa: F401
    OPUS_MODELS,
    M2M_MODEL,
    DEEPL_TO_M2M,
    DEEPL_SRC_TO_M2M,
    M2M_PREFERRED,
    get_progress,
    get_translate_dir,
    get_opus_model_path,
    get_m2m_model_path,
    is_opus_available,
    is_m2m_available,
    has_opus_model,
    get_all_status,
    download_opus_model,
    download_m2m_model,
    translate_offline,
    get_last_offline_error,
    diagnose_offline,
    delete_opus_model,
    delete_m2m_model,
    get_model_disk_size,
    unload_all,
    set_threads,
    reload_models,
    get_default_cores,
    get_max_cores,
)

if __name__ == "__main__":
    from linguataxi.models import offline_translate as _mod
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
                _mod._set_progress(key, "error", 0, f"No OPUS-MT model for {lang}")
                print(f"DONE:{key}:error:No OPUS-MT model for {lang}", flush=True)
                _any_failed = True
                continue

            if is_opus_available(str(models_dir), lang):
                _mod._set_progress(key, "ready", 100, "Already downloaded")
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
            _mod._set_progress(key, "ready", 100, "Already downloaded")
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
