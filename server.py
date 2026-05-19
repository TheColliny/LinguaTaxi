#!/usr/bin/env python3
"""
Live Caption — Real-time Speech Captioning & Translation Server

Supports two speech backends:
  - whisper : GPU-accelerated (faster-whisper), ~95-97% accuracy
  - vosk    : CPU-optimized (Vosk/Kaldi), ~85-90% accuracy, streaming

Three web interfaces:
  Display  (audience):   http://localhost:3000  — caption + up to 2 translations
  Extended (overflow):   http://localhost:3002  — up to 3 more translations
  Operator (controls):   http://localhost:3001  — full control panel
"""

import os, sys

_cuda_lib_paths = [
    os.path.dirname(os.path.abspath(__file__)),
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "cuda_libs"),
    r"C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.8\bin",
    r"C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.6\bin",
    r"C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.4\bin",
    r"C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.3\bin",
    r"C:\Program Files\Blackmagic Design\DaVinci Resolve",
]

# Scan nvidia pip packages in venv (bundled in Full installer)
if sys.platform == "win32":
    _nvidia_pkg_dir = os.path.join(sys.prefix, "Lib", "site-packages", "nvidia")
    if os.path.isdir(_nvidia_pkg_dir):
        for _pkg in os.listdir(_nvidia_pkg_dir):
            _bin = os.path.join(_nvidia_pkg_dir, _pkg, "bin")
            if os.path.isdir(_bin):
                _cuda_lib_paths.append(_bin)

for p in _cuda_lib_paths:
    if os.path.isdir(p):
        # Python 3.8+ on Windows: PATH no longer affects DLL search.
        # Must use os.add_dll_directory() for ctypes/extension module loading.
        if sys.platform == "win32" and hasattr(os, "add_dll_directory"):
            try:
                os.add_dll_directory(p)
            except OSError:
                pass
        # Also set PATH as fallback for older Python / subprocess calls
        if p not in os.environ.get("PATH", ""):
            os.environ["PATH"] = p + os.pathsep + os.environ.get("PATH", "")

import argparse, asyncio, json, logging, queue, shutil, subprocess, threading, time
import urllib.request, zipfile
from pathlib import Path

import numpy as np, sounddevice as sd, uvicorn
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect, UploadFile, File, Form
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel
from starlette.staticfiles import StaticFiles
import tuned_models
import offline_translate
import transcribe_file
import voice_id
from typing import List, Optional
from plugin_loader import PluginDispatcher
from plugin_registry import PluginRegistry

# ── Extracted constants & settings ──
from linguataxi.constants import (
    DEEPL_SOURCE_LANGS, DEEPL_TARGET_LANGS, DEEPL_TO_WHISPER,
    DEEPL_TARGET_DEFAULTS, VOSK_DIR_LANGS, COLOR_PALETTE, BG_OPTIONS,
    FONT_OPTIONS, DEFAULT_CONFIG, SAMPLE_RATE, CHANNELS, DTYPE,
    CHUNK_DURATION, SILENCE_THRESHOLD, SILENCE_DURATION,
    MAX_SEGMENT_DURATION, INTERIM_INTERVAL, MIN_SPEECH_DURATION,
)
from linguataxi.settings import (
    CONFIG_PATH, UPLOADS_DIR, MODELS_DIR, TRANSCRIPTS_DIR,
    load_config, save_config,
)
from linguataxi.server.websocket import (
    display_clients, extended_clients, operator_clients, dictation_clients,
    broadcast_all, broadcast_dictation, _bc,
)
from linguataxi.server.transcripts import (
    _session_stamp, _line_id_lock, _recent_lines,
    _save_line, _next_line_id, _store_recent_line, _broadcast_final,
)
from linguataxi.server.translation import (
    get_deepl_url, _translate_deepl, translate_text,
    _translate_pool, _translate_gen, _translate_gen_lock,
    _translate_all, _do_translate,
)

BASE_DIR = Path(__file__).parent

config = load_config()
_configured_cores = config.get("translate_cores", 0)
if _configured_cores > 0:
    offline_translate.set_threads(_configured_cores)

# ── Plugin System ──
PLUGINS_DIR = BASE_DIR / "plugins"
PLUGINS_DIR.mkdir(exist_ok=True)
plugin_dispatcher = PluginDispatcher(PLUGINS_DIR, config)

def _save_speaker_config():
    """Save speaker names, colors, assignments to config."""
    with _sources_lock:
        speaker_config = {}
        for s in _sources:
            key = str(s.device_index) if s.device_index is not None else "default"
            speaker_config[key] = {
                "name": s.name, "speaker": s.speaker, "color": s.color
            }
    config["speaker_config"] = speaker_config
    save_config(config)


def _load_speaker_config():
    """Restore speaker names, colors from config after sources are created."""
    sc = config.get("speaker_config", {})
    with _sources_lock:
        for s in _sources:
            key = str(s.device_index) if s.device_index is not None else "default"
            if key in sc:
                s.name = sc[key].get("name", s.name)
                s.speaker = sc[key].get("speaker", s.speaker)
                s.color = sc[key].get("color", "")

# ── Audio (constants imported from linguataxi.constants) ──

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("livecaption")

# ── Globals ──
display_app = FastAPI()
extended_app = FastAPI()
operator_app = FastAPI()
dictation_app = FastAPI()
stt_backend = None
shutdown_event = threading.Event()
mic_restart_event = threading.Event()   # signal audio capture to restart
current_mic_index = None                # active mic device index (None = default)
silence_threshold = SILENCE_THRESHOLD
translation_paused = True
captioning_paused = True
dictation_active = False
_dictation_loop = None  # asyncio loop for dictation app (set during startup)
save_transcripts = True

# ── Plugin Registry (marketplace) ──
_plugin_registry = None
_edition_file = BASE_DIR / "edition.txt"
EDITION = _edition_file.read_text().strip() if _edition_file.exists() else "Dev"

def _get_registry():
    """Lazy-initialize the plugin registry singleton."""
    global _plugin_registry
    if _plugin_registry is None:
        plugins_dir = BASE_DIR / "plugins"
        _version_data = json.loads((BASE_DIR / "version.json").read_text())
        _plugin_registry = PluginRegistry(
            plugins_dir=plugins_dir,
            github_repo="TheColliny/linguataxi-plugins",
            app_version=_version_data.get("version", "0.0.0"),
            edition=EDITION,
        )
    return _plugin_registry


# ── Multi-Source Audio (extracted → linguataxi.server.audio) ──
from linguataxi.server.audio import (
    AudioSource, get_source, add_source, remove_source,
    start_source_capture, start_audio_capture,
    _sources, _sources_lock, _transcription_queue,
    _buffer_audio_loop, _transcription_worker,
    _check_speaker_change, _make_audio_callback, _open_input_stream,
    _get_speaker_lang, _detect_segment_lang,
    _voice_id_try_enroll, _voice_id_try_identify,
)


# ══════════════════════════════════════════════
# SPEECH BACKENDS  (extracted → linguataxi.server.backends)
# ══════════════════════════════════════════════

from linguataxi.server.backends import SpeechBackend, create_backend, model_lock as _model_lock
from linguataxi.server.backends.whisper import WhisperBackend
from linguataxi.server.backends.vosk import VoskBackend, _load_vosk_bidir_model
from linguataxi.server.backends.mlx_whisper import MLXWhisperBackend


# ── Broadcasting, transcripts, translation: extracted → linguataxi.server.websocket,
# ── linguataxi.server.transcripts, linguataxi.server.translation (see imports above)


# ══════════════════════════════════════════════
# ROUTE REGISTRATION (extracted → linguataxi.server.routes)
# ══════════════════════════════════════════════

from linguataxi.server.routes.display import register_display_routes
from linguataxi.server.routes.operator import (
    register_operator_routes, _make_plugin_file_handler,
)
from linguataxi.server.routes.dictation import register_dictation_routes
from linguataxi.server.routes.transcribe import register_transcribe_routes

# ── Plugin System: discover and load ──
plugin_dispatcher.discover()
plugin_dispatcher.load_enabled(operator_app)

# Serve core static files on all 3 apps (operator + display + extended need plugin assets)
_static_dir = BASE_DIR / "static"
if _static_dir.is_dir():
    operator_app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")
    display_app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static_d")
    extended_app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static_e")

# Serve each plugin's static files on all 3 apps
for _m in plugin_dispatcher.get_all_manifests():
    _plugin_static = _m.path
    if _plugin_static.is_dir():
        _handler = _make_plugin_file_handler(_plugin_static)
        operator_app.get(f"/plugins/{_m.id}/{{filename}}")(_handler)
        display_app.get(f"/plugins/{_m.id}/{{filename}}")(_handler)
        extended_app.get(f"/plugins/{_m.id}/{{filename}}")(_handler)

# Register all route handlers
register_display_routes(display_app, extended_app)
register_operator_routes(operator_app)
register_dictation_routes(dictation_app)
register_transcribe_routes(operator_app)


# ── Startup ──
def setup_events(app, role):
    @app.on_event("startup")
    async def startup():
        if role == "dictation":
            global _dictation_loop
            _dictation_loop = asyncio.get_event_loop()
        if role == "display":
            loop = asyncio.get_event_loop()
            # Start capture threads for all registered sources
            with _sources_lock:
                for src in _sources:
                    t = threading.Thread(target=start_source_capture, args=(src,), daemon=True)
                    t.start()
                    src.capture_thread = t
            # Start processing (creates per-source buffer threads + shared worker)
            threading.Thread(target=stt_backend.process_audio_loop, args=(loop,), daemon=True).start()
    @app.on_event("shutdown")
    async def shutdown():
        shutdown_event.set()
        plugin_dispatcher.shutdown()

setup_events(display_app, "display")
setup_events(extended_app, "extended")
setup_events(operator_app, "operator")
setup_events(dictation_app, "dictation")


# ── GPU Detection ──
def detect_gpu():
    r = {"has_nvidia":False,"has_cuda":False,"gpu":"None","vram":0}
    try:
        out = subprocess.check_output(["nvidia-smi","--query-gpu=name,memory.total",
            "--format=csv,noheader,nounits"], stderr=subprocess.DEVNULL, timeout=5).decode().strip()
        if out:
            parts = out.split(","); r["has_nvidia"]=True; r["gpu"]=parts[0].strip()
            if len(parts)>1: r["vram"]=int(parts[1].strip())
    except Exception: return r
    if sys.platform=="win32":
        for p in _cuda_lib_paths:
            if os.path.isdir(p):
                for f in os.listdir(p):
                    if "cublas64_12" in f.lower(): r["has_cuda"]=True; return r
    else:
        try:
            if "libcublas" in subprocess.check_output(["ldconfig","-p"],
                stderr=subprocess.DEVNULL, timeout=5).decode(): r["has_cuda"]=True
        except Exception: pass
    return r

def detect_apple_silicon():
    """Check if running on Apple Silicon Mac."""
    if sys.platform != "darwin":
        return False
    try:
        out = subprocess.check_output(["sysctl", "-n", "machdep.cpu.brand_string"],
            stderr=subprocess.DEVNULL, timeout=5).decode().strip()
        return "Apple" in out
    except Exception:
        pass
    try:
        import platform
        return platform.machine() == "arm64" and sys.platform == "darwin"
    except Exception:
        return False

def resolve_backend(req):
    if req in ("whisper","vosk","mlx"): return req
    # Apple Silicon Mac → prefer mlx-whisper
    if detect_apple_silicon():
        try:
            import mlx_whisper
            print("  Apple Silicon detected — using MLX Whisper (Metal GPU)"); return "mlx"
        except ImportError:
            print("  Apple Silicon detected but mlx-whisper not installed")
            print("  Install with: pip install mlx-whisper")
            print("  Falling back to other engines...")
    # NVIDIA GPU → faster-whisper
    gpu = detect_gpu()
    if gpu["has_nvidia"] and gpu["has_cuda"]:
        print(f"  GPU: {gpu['gpu']} ({gpu['vram']} MB) + CUDA found"); return "whisper"
    if gpu["has_nvidia"]:
        print(f"  GPU: {gpu['gpu']} found but CUDA libs missing")
    elif sys.platform != "darwin":
        print(f"  No NVIDIA GPU detected")
    # CPU fallbacks
    try: import vosk; print("  Using Vosk (CPU streaming)"); return "vosk"
    except ImportError: pass
    try: import faster_whisper; print("  Using Whisper on CPU"); return "whisper"
    except ImportError: pass
    print("  ERROR: No speech engine installed. Run the setup script for your OS."); sys.exit(1)


# ── CLI ──
def list_mics():
    print("\n  Microphones:"); devs = sd.query_devices()
    for i, d in enumerate(devs):
        if d["max_input_channels"]>0:
            m = " <-- DEFAULT" if i==sd.default.device[0] else ""
            print(f"  [{i}] {d['name']} ({d['max_input_channels']}ch){m}")
    print()

def run_server(app, host, port, name):
    uvicorn.run(app, host=host, port=port, log_level="warning")

def main():
    global stt_backend
    parser = argparse.ArgumentParser(description="Live Caption Server")
    parser.add_argument("--backend", default="auto", choices=["auto","whisper","vosk","mlx"])
    parser.add_argument("--model", default="large-v3-turbo")
    parser.add_argument("--compute-type", default="float16", choices=["float16","int8","int8_float16","float32"])
    parser.add_argument("--device", default="cuda", choices=["cuda","cpu","auto"])
    parser.add_argument("--vosk-model", default="auto", choices=["auto","small","large"])
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
    parser.add_argument("--transcripts-dir", type=str, default=None,
        help="Directory for transcript files (default: ~/Documents/LinguaTaxi Transcripts)")
    parser.add_argument("--models-dir", type=str, default=None,
        help="Directory for speech/translation models (default: <app-dir>/models)")
    args = parser.parse_args()
    if args.list_mics: list_mics(); sys.exit(0)
    global silence_threshold; silence_threshold = args.threshold
    global MODELS_DIR
    if args.models_dir:
        MODELS_DIR = Path(args.models_dir)
        MODELS_DIR.mkdir(parents=True, exist_ok=True)
    global TRANSCRIPTS_DIR
    if args.transcripts_dir:
        TRANSCRIPTS_DIR = Path(args.transcripts_dir)
    TRANSCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
    display_app.state.mic_index = args.mic

    # Create audio sources from --sources or --mic
    def _device_name(dev_idx):
        """Look up human-readable name for a sounddevice index."""
        try:
            if dev_idx is None:
                return sd.query_devices(sd.default.device[0])["name"]
            return sd.query_devices(dev_idx)["name"]
        except Exception:
            return None

    if args.sources:
        seen_devices = set()
        for idx_str in args.sources.split(","):
            idx = int(idx_str.strip())
            dev = None if idx == -1 else idx
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
    bc = resolve_backend(args.backend)
    if bc == "whisper":
        dev = args.device
        if dev == "cuda":
            g = detect_gpu()
            if not (g["has_nvidia"] and g["has_cuda"]): dev = "cpu"; print("  Falling back to CPU")
        print(f"  Loading Whisper {args.model} ({args.compute_type}, {dev})...")
        try: stt_backend = WhisperBackend(args.model, dev, args.compute_type)
        except Exception as e: print(f"  Failed: {e}"); sys.exit(1)
    elif bc == "mlx":
        print(f"  Loading MLX Whisper {args.model} (Apple Metal GPU)...")
        try: stt_backend = MLXWhisperBackend(args.model)
        except Exception as e: print(f"  Failed: {e}"); sys.exit(1)
    elif bc == "vosk":
        print(f"  Loading Vosk ({args.vosk_model})...")
        try: stt_backend = VoskBackend(args.vosk_model)
        except Exception as e: print(f"  Failed: {e}"); sys.exit(1)

    # Initialize Silero language detection if available
    try:
        import lang_detect
        lang_detect.set_models_dir(MODELS_DIR)
        if not lang_detect.is_available():
            log.info("Silero language detection model not found — will download on first use")
    except ImportError:
        log.info("onnxruntime not installed — Silero language detection unavailable")

    # Initialize Voice ID speaker identification
    voice_id.set_models_dir(MODELS_DIR)
    voice_id.registry.set_threshold(config.get("voice_id_threshold", 0.65))
    if not voice_id.is_available():
        log.info("Voice ID model not found — will download on first use when a speaker is enrolled")

    config["backend"] = bc; save_config(config)
    tc = config.get("translation_count", 1)
    ext_needed = tc > 2

    print(f"  Engine: {stt_backend.name}")
    if args.mic is not None: print(f"  Mic: [{args.mic}] {sd.query_devices(args.mic)['name']}")
    else: print(f"  Mic: [default] {sd.query_devices(sd.default.device[0])['name']}")
    print(f"  DeepL: {'Yes' if config.get('deepl_api_key') else 'No (set in operator panel)'}")
    print(f"  Input: {DEEPL_SOURCE_LANGS.get(config.get('input_lang','EN'),'English')}")
    print(f"  Translations: {tc}")
    print(f"\n  Display:   http://localhost:{args.display_port}")
    print(f"  Operator:  http://localhost:{args.operator_port}")
    if ext_needed: print(f"  Extended:  http://localhost:{args.extended_port}")
    print(f"  Dictation: http://localhost:{args.dictation_port}")
    print(f"\n  Ctrl+C to stop.\n")

    threads = [
        threading.Thread(target=run_server, args=(display_app, args.host, args.display_port, "display"), daemon=True),
        threading.Thread(target=run_server, args=(operator_app, args.host, args.operator_port, "operator"), daemon=True),
        threading.Thread(target=run_server, args=(extended_app, args.host, args.extended_port, "extended"), daemon=True),
        threading.Thread(target=run_server, args=(dictation_app, args.host, args.dictation_port, "dictation"), daemon=True),
    ]
    for t in threads: t.start()
    try:
        while True: time.sleep(1)
    except KeyboardInterrupt: print("\n  Shutting down..."); _graceful_shutdown()

def _graceful_shutdown():
    """Clean up all resources: audio streams, thread pool, then exit."""
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
            if hasattr(src, 'queue'):
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

def _shutdown_and_exit():
    """Called from /api/shutdown — clean up then force-exit the process."""
    # Start a watchdog that force-exits after 8s no matter what
    def _force_exit():
        time.sleep(8)
        os._exit(1)
    threading.Thread(target=_force_exit, daemon=True).start()

    _graceful_shutdown()
    time.sleep(0.5)
    os._exit(0)

if __name__ == "__main__": main()
