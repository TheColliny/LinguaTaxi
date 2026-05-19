"""Model Download Manager — orchestrates model downloads with unified progress reporting.

Used by the Inno Setup installer (polls the JSON status file) and importable by the
runtime for model update checks.

CLI (installer mode)::

    python model_manager.py download --plan plan.json --progress status.json
    python model_manager.py check-updates --models-dir ./models [--progress status.json]

Plan JSON::

    { "models_dir": "...", "venv_dir": "...", "downloads": [
        {"type": "opus", "lang": "ES"},
        {"type": "m2m100"},
        {"type": "tuned", "lang": "FR"},
        {"type": "vosk_lang", "lang": "de"},
        {"type": "update_models"}
    ]}

Status JSON (written continuously, polled by Inno Setup)::

    { "state": "downloading", "task_label": "...", "overall_pct": 45,
      "current_pct": 72, "completed": [...], "failed": [...],
      "total": 5, "done_count": 2, "errors": [],
      "updates_available": [] }
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any
from urllib.request import urlopen
from urllib.error import URLError

MANIFEST_URL: str = (
    "https://raw.githubusercontent.com/TheColliny/LinguaTaxi/main/models-manifest.json"
)
APP_DIR: Path = Path(__file__).resolve().parent.parent.parent

# ---------------------------------------------------------------------------
# Status file management
# ---------------------------------------------------------------------------

_status: dict[str, Any] = {
    "state": "idle",
    "task_label": "",
    "overall_pct": 0,
    "current_pct": 0,
    "completed": [],
    "failed": [],
    "total": 0,
    "done_count": 0,
    "errors": [],
    "updates_available": [],
}
_lock: threading.Lock = threading.Lock()
_progress_path: str | None = None


def _flush() -> None:
    """Write the current status dict to the progress JSON file atomically."""
    if not _progress_path:
        return
    try:
        tmp = _progress_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_status, f)
        os.replace(tmp, _progress_path)
    except Exception:
        pass


def _set(**kw: Any) -> None:
    """Update the status dict and flush to disk."""
    with _lock:
        _status.update(kw)
        _flush()


def _calc_overall() -> int:
    """Calculate the overall progress percentage across all download tasks."""
    total = _status["total"]
    if total == 0:
        return 0
    base = int((_status["done_count"] / total) * 100)
    slice_pct = int((_status["current_pct"] / 100) * (100 / total))
    return min(99, base + slice_pct)


# ---------------------------------------------------------------------------
# Subprocess runner — launches existing download scripts and streams progress
# ---------------------------------------------------------------------------

def _run_download_script(
    python: str,
    script_args: list[str],
    label: str,
    task_id: str,
) -> None:
    """Run a download script as a subprocess, parse its ``PROGRESS:`` lines,
    and update the shared status dict.

    Args:
        python: Path to the Python interpreter.
        script_args: Command-line arguments for the script.
        label: Human-readable label for the current task.
        task_id: Unique identifier for this download task.
    """
    _set(task_label=label, current_pct=0, state="downloading",
         overall_pct=_calc_overall())

    cmd = [python] + script_args
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"

    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1, env=env,
        )
        for line in proc.stdout:
            line = line.strip()
            if line.startswith("PROGRESS:"):
                parts = line.split(":", 4)
                if len(parts) >= 4:
                    try:
                        pct = int(parts[3])
                        _set(current_pct=pct, overall_pct=_calc_overall())
                    except ValueError:
                        pass
            elif line.startswith("DONE:"):
                parts = line.split(":", 3)
                if len(parts) >= 3 and parts[2] == "ok":
                    _set(current_pct=100, overall_pct=_calc_overall())

        proc.wait(timeout=7200)
        success = proc.returncode == 0

    except Exception as e:
        success = False
        with _lock:
            _status["errors"].append(f"{task_id}: {e}")

    with _lock:
        if success:
            _status["completed"].append(task_id)
        else:
            _status["failed"].append(task_id)
        _status["done_count"] = len(_status["completed"]) + len(_status["failed"])
        _status["current_pct"] = 0
        _status["overall_pct"] = _calc_overall()
        _flush()


# ---------------------------------------------------------------------------
# Download plan execution
# ---------------------------------------------------------------------------

def run_plan(plan: dict[str, Any], progress_path: str | None = None) -> None:
    """Execute a download plan, processing each entry sequentially.

    Args:
        plan: Dict with ``models_dir``, ``venv_dir``, ``app_dir``,
            and ``downloads`` list.
        progress_path: Path to the JSON file for progress reporting.
    """
    global _progress_path
    _progress_path = progress_path

    models_dir = plan.get("models_dir", str(APP_DIR / "models"))
    venv_dir = plan.get("venv_dir", "")
    app_dir = plan.get("app_dir", str(APP_DIR))
    downloads = plan.get("downloads", [])

    if not downloads:
        _set(state="complete", overall_pct=100)
        return

    python = os.path.join(venv_dir, "Scripts", "python.exe") if venv_dir else sys.executable
    if not os.path.isfile(python):
        python = sys.executable

    _set(state="downloading", total=len(downloads), done_count=0,
         overall_pct=0, completed=[], failed=[], errors=[])

    for dl in downloads:
        dtype = dl.get("type", "")
        lang = dl.get("lang", "")

        if dtype == "opus":
            task_id = f"opus_{lang.lower()}"
            label = f"Downloading {lang} OPUS-MT translation model..."
            script = [os.path.join(app_dir, "offline_translate.py"),
                      "--download-opus", lang.upper(),
                      "--models-dir", models_dir]

        elif dtype == "m2m100":
            task_id = "m2m100"
            label = "Downloading M2M-100 multilingual model (this may take 30-60 min)..."
            script = [os.path.join(app_dir, "offline_translate.py"),
                      "--download-m2m",
                      "--models-dir", models_dir]

        elif dtype == "tuned":
            task_id = f"tuned_{lang.lower()}"
            label = f"Downloading {lang} tuned voice model..."
            script = [os.path.join(app_dir, "tuned_models.py"),
                      "--download", lang.upper(),
                      "--models-dir", models_dir]

        elif dtype == "vosk_lang":
            task_id = f"vosk_{lang.lower()}"
            label = f"Downloading {lang} Vosk language model..."
            script = [os.path.join(app_dir, "download_models.py"),
                      "--vosk-lang", lang.lower(),
                      "--models-dir", models_dir]

        elif dtype == "update_models":
            task_id = "update_models"
            label = "Checking for updated voice recognition models..."
            script = [os.path.join(app_dir, "download_models.py"),
                      "--models-dir", models_dir]

        else:
            with _lock:
                _status["failed"].append(f"unknown_{dtype}")
                _status["errors"].append(f"Unknown download type: {dtype}")
                _status["done_count"] += 1
                _flush()
            continue

        _run_download_script(python, script, label, task_id)

    final_state = "complete" if not _status["failed"] else "complete_with_errors"
    _set(state=final_state, overall_pct=100, task_label="All downloads finished.",
         current_pct=0)


# ---------------------------------------------------------------------------
# Version checking
# ---------------------------------------------------------------------------

def _load_remote_manifest(timeout: int = 10) -> dict[str, Any] | None:
    """Fetch the remote models-manifest.json from GitHub.

    Args:
        timeout: HTTP request timeout in seconds.

    Returns:
        Parsed manifest dict, or ``None`` on failure.
    """
    try:
        resp = urlopen(MANIFEST_URL, timeout=timeout)
        return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None


def _load_local_manifest() -> dict[str, Any] | None:
    """Load the local models-manifest.json bundled with the app.

    Returns:
        Parsed manifest dict, or ``None`` if not found.
    """
    p = APP_DIR / "models-manifest.json"
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
    return None


def _read_installed_version(model_dir: Path) -> str | None:
    """Read the installed version from a model's ``_version.json``.

    Args:
        model_dir: Path to the model directory.

    Returns:
        Version string, or ``None`` if not found.
    """
    vf = model_dir / "_version.json"
    if vf.exists():
        try:
            return json.loads(vf.read_text(encoding="utf-8")).get("version")
        except Exception:
            pass
    return None


def _write_installed_version(model_dir: Path, version: str) -> None:
    """Write a ``_version.json`` file to record the installed model version.

    Args:
        model_dir: Path to the model directory.
        version: Version string to record.
    """
    vf = model_dir / "_version.json"
    try:
        vf.write_text(json.dumps({"version": version}), encoding="utf-8")
    except Exception:
        pass


def _model_dir_for_id(models_dir: Path, model_id: str) -> Path:
    """Map a manifest model ID to its on-disk directory.

    Args:
        models_dir: Base models directory.
        model_id: Model identifier from the manifest.

    Returns:
        Path to the model directory.
    """
    if model_id.startswith("opus-mt-"):
        return models_dir / "translate" / model_id
    if model_id == "m2m100-1.2b":
        return models_dir / "translate" / "m2m100-1.2b"
    if model_id.startswith("tuned-"):
        lang = model_id.replace("tuned-", "")
        return models_dir / "tuned" / lang
    return models_dir / model_id


def check_updates(
    models_dir: str,
    progress_path: str | None = None,
) -> list[dict[str, Any]]:
    """Compare installed models against the remote manifest.

    Args:
        models_dir: Path to the models directory.
        progress_path: Optional path to write progress JSON.

    Returns:
        List of dicts with ``model_id``, ``installed_version``,
        ``available_version``, and ``size_mb``.
    """
    global _progress_path
    _progress_path = progress_path

    models_path = Path(models_dir)
    _set(state="checking", task_label="Checking for model updates...")

    remote = _load_remote_manifest()
    if not remote:
        local = _load_local_manifest()
        if not local:
            _set(state="complete", task_label="No manifest available (offline?).")
            return []
        remote = local

    updates: list[dict[str, Any]] = []
    for model_id, info in remote.get("models", {}).items():
        d = _model_dir_for_id(models_path, model_id)
        check_file = info.get("check_file", "model.bin")

        if not (d / check_file).exists():
            continue

        installed_ver = _read_installed_version(d)
        remote_ver = info.get("version", "")

        if not installed_ver:
            continue

        if remote_ver and installed_ver != remote_ver:
            updates.append({
                "model_id": model_id,
                "installed_version": installed_ver,
                "available_version": remote_ver,
                "size_mb": info.get("size_mb", 0),
            })

    _set(state="complete", task_label=f"Found {len(updates)} update(s).",
         updates_available=updates, overall_pct=100)
    return updates


def stamp_installed_models(models_dir: str) -> None:
    """Write ``_version.json`` for all installed models that lack one.

    Run after a fresh install or upgrade so future update checks have a baseline.

    Args:
        models_dir: Path to the models directory.
    """
    models_path = Path(models_dir)
    manifest = _load_local_manifest()
    if not manifest:
        return

    for model_id, info in manifest.get("models", {}).items():
        d = _model_dir_for_id(models_path, model_id)
        check_file = info.get("check_file", "model.bin")
        if (d / check_file).exists() and not (d / "_version.json").exists():
            _write_installed_version(d, info.get("version", "1.0.0"))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    """CLI entry point for the model download manager."""
    parser = argparse.ArgumentParser(
        description="LinguaTaxi — Model Download Manager")
    sub = parser.add_subparsers(dest="command")

    dl = sub.add_parser("download", help="Run a download plan")
    dl.add_argument("--plan", required=True, help="Path to plan JSON file")
    dl.add_argument("--progress", help="Path to write progress JSON")

    cu = sub.add_parser("check-updates",
                        help="Check for newer model versions")
    cu.add_argument("--models-dir", required=True)
    cu.add_argument("--progress", help="Path to write progress JSON")

    st = sub.add_parser("stamp",
                        help="Write version stamps for installed models")
    st.add_argument("--models-dir", required=True)

    args = parser.parse_args()

    if args.command == "download":
        plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
        run_plan(plan, args.progress)
        done_path = args.progress + ".done" if args.progress else None
        if done_path:
            Path(done_path).write_text("done", encoding="utf-8")
        if _status["failed"]:
            print(f"Completed with {len(_status['failed'])} error(s):",
                  _status["errors"], file=sys.stderr)
            sys.exit(1)

    elif args.command == "check-updates":
        updates = check_updates(args.models_dir, args.progress)
        if updates:
            print(json.dumps(updates, indent=2))
        else:
            print("All models are up to date.")
        done_path = args.progress + ".done" if args.progress else None
        if done_path:
            Path(done_path).write_text("done", encoding="utf-8")

    elif args.command == "stamp":
        stamp_installed_models(args.models_dir)
        print("Version stamps written.")

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
