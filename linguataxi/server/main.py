"""Server entry point: startup, shutdown, GPU detection, and backend selection."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

import sounddevice as sd
import uvicorn

from linguataxi.constants import (
    DEEPL_SOURCE_LANGS, SILENCE_THRESHOLD,
)
from linguataxi.settings import (
    MODELS_DIR as _default_models_dir,
    TRANSCRIPTS_DIR as _default_transcripts_dir,
    save_config,
)

log: logging.Logger = logging.getLogger("livecaption")


# ══════════════════════════════════════════════
# CUDA library path setup
# ══════════════════════════════════════════════

def _setup_cuda_paths() -> list[str]:
    """Register CUDA library directories for DLL loading.

    Scans common CUDA install paths and nvidia pip packages in the
    active venv, calling :func:`os.add_dll_directory` on Windows and
    prepending to ``PATH`` as a fallback.

    Returns:
        The list of candidate library paths that were registered.
    """
    cuda_lib_paths: list[str] = [
        os.path.dirname(os.path.abspath(__file__)),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "cuda_libs"),
        r"C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.8\bin",
        r"C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.6\bin",
        r"C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.4\bin",
        r"C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.3\bin",
        r"C:\Program Files\Blackmagic Design\DaVinci Resolve",
    ]

    # Also include the project root (where server.py lives)
    _project_root = str(Path(__file__).resolve().parent.parent.parent)
    if _project_root not in cuda_lib_paths:
        cuda_lib_paths.insert(0, _project_root)
    _project_cuda = os.path.join(_project_root, "cuda_libs")
    if _project_cuda not in cuda_lib_paths:
        cuda_lib_paths.insert(1, _project_cuda)

    # Scan nvidia pip packages in venv (bundled in Full installer)
    if sys.platform == "win32":
        nvidia_pkg_dir = os.path.join(sys.prefix, "Lib", "site-packages", "nvidia")
        if os.path.isdir(nvidia_pkg_dir):
            for pkg in os.listdir(nvidia_pkg_dir):
                bin_dir = os.path.join(nvidia_pkg_dir, pkg, "bin")
                if os.path.isdir(bin_dir):
                    cuda_lib_paths.append(bin_dir)

    for p in cuda_lib_paths:
        if os.path.isdir(p):
            # Python 3.8+ on Windows: PATH no longer affects DLL search.
            if sys.platform == "win32" and hasattr(os, "add_dll_directory"):
                try:
                    os.add_dll_directory(p)
                except OSError:
                    pass
            # Also set PATH as fallback for older Python / subprocess calls
            if p not in os.environ.get("PATH", ""):
                os.environ["PATH"] = p + os.pathsep + os.environ.get("PATH", "")

    return cuda_lib_paths


# Run CUDA setup at import time (same as original server.py top-of-file behavior)
_cuda_lib_paths: list[str] = _setup_cuda_paths()


# ══════════════════════════════════════════════
# GPU / platform detection
# ══════════════════════════════════════════════

def detect_gpu() -> Dict[str, Any]:
    """Detect NVIDIA GPU presence and CUDA library availability.

    Returns:
        Dict with keys ``has_nvidia``, ``has_cuda``, ``gpu`` (name string),
        and ``vram`` (MB integer).
    """
    r: Dict[str, Any] = {"has_nvidia": False, "has_cuda": False, "gpu": "None", "vram": 0}
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=name,memory.total",
             "--format=csv,noheader,nounits"],
            stderr=subprocess.DEVNULL, timeout=5,
        ).decode().strip()
        if out:
            parts = out.split(",")
            r["has_nvidia"] = True
            r["gpu"] = parts[0].strip()
            if len(parts) > 1:
                r["vram"] = int(parts[1].strip())
    except Exception:
        return r
    if sys.platform == "win32":
        for p in _cuda_lib_paths:
            if os.path.isdir(p):
                for f in os.listdir(p):
                    if "cublas64_12" in f.lower():
                        r["has_cuda"] = True
                        return r
    else:
        try:
            if "libcublas" in subprocess.check_output(
                ["ldconfig", "-p"],
                stderr=subprocess.DEVNULL, timeout=5,
            ).decode():
                r["has_cuda"] = True
        except Exception:
            pass
    return r


def detect_apple_silicon() -> bool:
    """Check if running on Apple Silicon Mac.

    Returns:
        True if the host is an Apple Silicon Mac, False otherwise.
    """
    if sys.platform != "darwin":
        return False
    try:
        out = subprocess.check_output(
            ["sysctl", "-n", "machdep.cpu.brand_string"],
            stderr=subprocess.DEVNULL, timeout=5,
        ).decode().strip()
        return "Apple" in out
    except Exception:
        pass
    try:
        import platform
        return platform.machine() == "arm64" and sys.platform == "darwin"
    except Exception:
        return False


def resolve_backend(req: str) -> str:
    """Choose the best available speech-to-text backend.

    Args:
        req: Requested backend — ``"auto"``, ``"whisper"``, ``"vosk"``,
            or ``"mlx"``.

    Returns:
        The resolved backend name (``"whisper"``, ``"vosk"``, or ``"mlx"``).
        Calls :func:`sys.exit` if no engine is available.
    """
    if req in ("whisper", "vosk", "mlx"):
        return req
    # Apple Silicon Mac -> prefer mlx-whisper
    if detect_apple_silicon():
        try:
            import mlx_whisper  # noqa: F401
            print("  Apple Silicon detected — using MLX Whisper (Metal GPU)")
            return "mlx"
        except ImportError:
            print("  Apple Silicon detected but mlx-whisper not installed")
            print("  Install with: pip install mlx-whisper")
            print("  Falling back to other engines...")
    # NVIDIA GPU -> faster-whisper
    gpu = detect_gpu()
    if gpu["has_nvidia"] and gpu["has_cuda"]:
        print(f"  GPU: {gpu['gpu']} ({gpu['vram']} MB) + CUDA found")
        return "whisper"
    if gpu["has_nvidia"]:
        print(f"  GPU: {gpu['gpu']} found but CUDA libs missing")
    elif sys.platform != "darwin":
        print("  No NVIDIA GPU detected")
    # CPU fallbacks
    try:
        import vosk  # noqa: F401
        print("  Using Vosk (CPU streaming)")
        return "vosk"
    except ImportError:
        pass
    try:
        import faster_whisper  # noqa: F401
        print("  Using Whisper on CPU")
        return "whisper"
    except ImportError:
        pass
    print("  ERROR: No speech engine installed. Run the setup script for your OS.")
    sys.exit(1)


# ══════════════════════════════════════════════
# Microphone enumeration
# ══════════════════════════════════════════════

def list_mics() -> None:
    """Print available microphone input devices to stdout."""
    print("\n  Microphones:")
    devs = sd.query_devices()
    for i, d in enumerate(devs):
        if d["max_input_channels"] > 0:
            m = " <-- DEFAULT" if i == sd.default.device[0] else ""
            print(f"  [{i}] {d['name']} ({d['max_input_channels']}ch){m}")
    print()


# ══════════════════════════════════════════════
# Startup / lifespan events
# ══════════════════════════════════════════════

def setup_events(app: Any, role: str) -> None:
    """Attach startup and shutdown event handlers to a FastAPI app.

    Args:
        app: A :class:`FastAPI` application instance.
        role: One of ``"display"``, ``"extended"``, ``"operator"``,
            or ``"dictation"``.
    """
    from linguataxi.server.app import (  # deferred to avoid circular import
        _dictation_loop, shutdown_event, stt_backend, plugin_dispatcher,
    )
    from linguataxi.server.audio import (
        _sources, _sources_lock, start_source_capture,
    )
    import linguataxi.server.app as _app_mod

    @app.on_event("startup")
    async def startup() -> None:
        if role == "dictation":
            _app_mod._dictation_loop = asyncio.get_event_loop()
        if role == "display":
            loop = asyncio.get_event_loop()
            # Start capture threads for all registered sources
            with _sources_lock:
                for src in _sources:
                    t = threading.Thread(
                        target=start_source_capture, args=(src,), daemon=True,
                    )
                    t.start()
                    src.capture_thread = t
            # Start processing (creates per-source buffer threads + shared worker)
            threading.Thread(
                target=_app_mod.stt_backend.process_audio_loop,
                args=(loop,),
                daemon=True,
            ).start()

    @app.on_event("shutdown")
    async def shutdown() -> None:
        _app_mod.shutdown_event.set()
        _app_mod.plugin_dispatcher.shutdown()


# ══════════════════════════════════════════════
# Server runner
# ══════════════════════════════════════════════

def run_server(app: Any, host: str, port: int, name: str) -> None:
    """Start a uvicorn server for the given FastAPI app.

    Args:
        app: The FastAPI application to serve.
        host: Bind address.
        port: Bind port.
        name: Human-readable server name (for logging).
    """
    uvicorn.run(app, host=host, port=port, log_level="warning")


# ══════════════════════════════════════════════
# Graceful shutdown
# ══════════════════════════════════════════════

def _graceful_shutdown() -> None:
    """Clean up all resources: audio streams, thread pool, then exit."""
    from linguataxi.server.app import (
        shutdown_event, plugin_dispatcher,
    )
    from linguataxi.server.audio import _sources, _sources_lock
    from linguataxi.server.translation import _translate_pool

    shutdown_event.set()
    # Close all audio streams so the microphone is released
    with _sources_lock:
        for src in _sources:
            src.active = False
            src.restart_event.set()
            if src.stream:
                try:
                    src.stream.stop()
                    src.stream.close()
                except Exception:
                    pass
                src.stream = None
            # Drain the audio queue to unblock any waiting threads
            if hasattr(src, "queue"):
                try:
                    while not src.queue.empty():
                        src.queue.get_nowait()
                except Exception:
                    pass
    # Stop all sounddevice activity globally (releases PortAudio resources)
    try:
        sd.stop()
    except Exception:
        pass
    _translate_pool.shutdown(wait=False)
    plugin_dispatcher.shutdown()
    log.info("Graceful shutdown complete")


def _shutdown_and_exit() -> None:
    """Called from ``/api/shutdown`` — clean up then force-exit the process."""
    # Start a watchdog that force-exits after 8 s no matter what
    def _force_exit() -> None:
        time.sleep(8)
        os._exit(1)

    threading.Thread(target=_force_exit, daemon=True).start()

    _graceful_shutdown()
    time.sleep(0.5)
    os._exit(0)


# ══════════════════════════════════════════════
# CLI entry point
# ══════════════════════════════════════════════

def main() -> None:
    """Parse CLI arguments, initialize the speech backend, and start servers."""
    import linguataxi.server.app as _app
    from linguataxi.server.app import (
        display_app, operator_app, extended_app, dictation_app,
        config, _load_speaker_config,
    )
    from linguataxi.server.audio import add_source
    from linguataxi.server.backends.whisper import WhisperBackend
    from linguataxi.server.backends.vosk import VoskBackend
    from linguataxi.server.backends.mlx_whisper import MLXWhisperBackend
    import linguataxi.settings as _settings
    import voice_id

    parser = argparse.ArgumentParser(description="Live Caption Server")
    parser.add_argument("--backend", default="auto",
                        choices=["auto", "whisper", "vosk", "mlx"])
    parser.add_argument("--model", default="large-v3-turbo")
    parser.add_argument("--compute-type", default="float16",
                        choices=["float16", "int8", "int8_float16", "float32"])
    parser.add_argument("--device", default="cuda",
                        choices=["cuda", "cpu", "auto"])
    parser.add_argument("--vosk-model", default="auto",
                        choices=["auto", "small", "large"])
    parser.add_argument("--mic", type=int, default=None)
    parser.add_argument("--sources", type=str, default=None,
                        help="Comma-separated device indices (-1 for default)")
    parser.add_argument("--list-mics", action="store_true")
    parser.add_argument("--display-port", type=int, default=3000)
    parser.add_argument("--operator-port", type=int, default=3001)
    parser.add_argument("--extended-port", type=int, default=3002)
    parser.add_argument("--dictation-port", type=int, default=3005)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--threshold", type=float, default=SILENCE_THRESHOLD)
    parser.add_argument(
        "--transcripts-dir", type=str, default=None,
        help="Directory for transcript files "
             "(default: ~/Documents/LinguaTaxi Transcripts)",
    )
    parser.add_argument(
        "--models-dir", type=str, default=None,
        help="Directory for speech/translation models (default: <app-dir>/models)",
    )
    args = parser.parse_args()

    if args.list_mics:
        list_mics()
        sys.exit(0)

    _app.silence_threshold = args.threshold

    if args.models_dir:
        _settings.MODELS_DIR = Path(args.models_dir)
        _settings.MODELS_DIR.mkdir(parents=True, exist_ok=True)
    if args.transcripts_dir:
        _settings.TRANSCRIPTS_DIR = Path(args.transcripts_dir)
    _settings.TRANSCRIPTS_DIR.mkdir(parents=True, exist_ok=True)

    display_app.state.mic_index = args.mic

    # Create audio sources from --sources or --mic
    def _device_name(dev_idx: Optional[int]) -> Optional[str]:
        """Look up human-readable name for a sounddevice index.

        Args:
            dev_idx: Device index, or None for the system default.

        Returns:
            Human-readable device name, or None on error.
        """
        try:
            if dev_idx is None:
                return sd.query_devices(sd.default.device[0])["name"]
            return sd.query_devices(dev_idx)["name"]
        except Exception:
            return None

    if args.sources:
        seen_devices: set[Optional[int]] = set()
        for idx_str in args.sources.split(","):
            idx = int(idx_str.strip())
            dev: Optional[int] = None if idx == -1 else idx
            if dev in seen_devices:
                log.warning(f"Skipping duplicate audio device: {dev}")
                continue
            seen_devices.add(dev)
            add_source(dev, _device_name(dev))
    elif args.mic is not None:
        add_source(args.mic, _device_name(args.mic))
    else:
        add_source(None, _device_name(None))  # system default

    _load_speaker_config()

    print("\n  +-- Live Caption Server --+\n")
    bc: str = resolve_backend(args.backend)
    if bc == "whisper":
        dev = args.device
        if dev == "cuda":
            g = detect_gpu()
            if not (g["has_nvidia"] and g["has_cuda"]):
                dev = "cpu"
                print("  Falling back to CPU")
        print(f"  Loading Whisper {args.model} ({args.compute_type}, {dev})...")
        try:
            _app.stt_backend = WhisperBackend(args.model, dev, args.compute_type)
        except Exception as e:
            print(f"  Failed: {e}")
            sys.exit(1)
    elif bc == "mlx":
        print(f"  Loading MLX Whisper {args.model} (Apple Metal GPU)...")
        try:
            _app.stt_backend = MLXWhisperBackend(args.model)
        except Exception as e:
            print(f"  Failed: {e}")
            sys.exit(1)
    elif bc == "vosk":
        print(f"  Loading Vosk ({args.vosk_model})...")
        try:
            _app.stt_backend = VoskBackend(args.vosk_model)
        except Exception as e:
            print(f"  Failed: {e}")
            sys.exit(1)

    # Initialize Silero language detection if available
    try:
        import lang_detect
        lang_detect.set_models_dir(_settings.MODELS_DIR)
        if not lang_detect.is_available():
            log.info(
                "Silero language detection model not found "
                "— will download on first use"
            )
    except ImportError:
        log.info(
            "onnxruntime not installed — Silero language detection unavailable"
        )

    # Initialize Voice ID speaker identification
    voice_id.set_models_dir(_settings.MODELS_DIR)
    voice_id.registry.set_threshold(config.get("voice_id_threshold", 0.65))
    if not voice_id.is_available():
        log.info(
            "Voice ID model not found "
            "— will download on first use when a speaker is enrolled"
        )

    config["backend"] = bc
    save_config(config)
    tc: int = config.get("translation_count", 1)
    ext_needed: bool = tc > 2

    print(f"  Engine: {_app.stt_backend.name}")
    if args.mic is not None:
        print(f"  Mic: [{args.mic}] {sd.query_devices(args.mic)['name']}")
    else:
        print(f"  Mic: [default] {sd.query_devices(sd.default.device[0])['name']}")
    print(
        f"  DeepL: "
        f"{'Yes' if config.get('deepl_api_key') else 'No (set in operator panel)'}"
    )
    print(
        f"  Input: "
        f"{DEEPL_SOURCE_LANGS.get(config.get('input_lang', 'EN'), 'English')}"
    )
    print(f"  Translations: {tc}")
    print(f"\n  Display:   http://localhost:{args.display_port}")
    print(f"  Operator:  http://localhost:{args.operator_port}")
    if ext_needed:
        print(f"  Extended:  http://localhost:{args.extended_port}")
    print(f"  Dictation: http://localhost:{args.dictation_port}")
    print("\n  Ctrl+C to stop.\n")

    # Attach lifespan events to all apps
    setup_events(display_app, "display")
    setup_events(extended_app, "extended")
    setup_events(operator_app, "operator")
    setup_events(dictation_app, "dictation")

    threads = [
        threading.Thread(
            target=run_server,
            args=(display_app, args.host, args.display_port, "display"),
            daemon=True,
        ),
        threading.Thread(
            target=run_server,
            args=(operator_app, args.host, args.operator_port, "operator"),
            daemon=True,
        ),
        threading.Thread(
            target=run_server,
            args=(extended_app, args.host, args.extended_port, "extended"),
            daemon=True,
        ),
        threading.Thread(
            target=run_server,
            args=(dictation_app, args.host, args.dictation_port, "dictation"),
            daemon=True,
        ),
    ]
    for t in threads:
        t.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n  Shutting down...")
        _graceful_shutdown()
