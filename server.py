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
from abc import ABC, abstractmethod
from pathlib import Path

import numpy as np, requests, sounddevice as sd, uvicorn
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect, UploadFile, File, Form
from fastapi.responses import FileResponse, JSONResponse
import tuned_models
import offline_translate

# ── Paths ──
BASE_DIR = Path(__file__).parent
if sys.platform == "win32":
    _config_dir = Path(os.environ.get("APPDATA", Path.home())) / "LinguaTaxi"
elif sys.platform == "darwin":
    _config_dir = Path.home() / "Library" / "Application Support" / "LinguaTaxi"
else:
    _config_dir = Path.home() / ".config" / "linguataxi"
_config_dir.mkdir(parents=True, exist_ok=True)
CONFIG_PATH = _config_dir / "config.json"
UPLOADS_DIR = BASE_DIR / "uploads"
MODELS_DIR = BASE_DIR / "models"
TRANSCRIPTS_DIR = Path(os.environ.get("LINGUATAXI_TRANSCRIPTS",
    str(Path.home() / "Documents" / "LinguaTaxi Transcripts")))
UPLOADS_DIR.mkdir(exist_ok=True)
MODELS_DIR.mkdir(exist_ok=True)
TRANSCRIPTS_DIR.mkdir(exist_ok=True)

# ── Language data ──
DEEPL_SOURCE_LANGS = {
    "AR":"Arabic","BG":"Bulgarian","CS":"Czech","DA":"Danish","DE":"German",
    "EL":"Greek","EN":"English","ES":"Spanish","ET":"Estonian","FI":"Finnish",
    "FR":"French","HU":"Hungarian","ID":"Indonesian","IT":"Italian","JA":"Japanese",
    "KO":"Korean","LT":"Lithuanian","LV":"Latvian","NB":"Norwegian","NL":"Dutch",
    "PL":"Polish","PT":"Portuguese","RO":"Romanian","RU":"Russian","SK":"Slovak",
    "SL":"Slovenian","SV":"Swedish","TR":"Turkish","UK":"Ukrainian","ZH":"Chinese",
}
DEEPL_TARGET_LANGS = {
    "AR":"Arabic","BG":"Bulgarian","CS":"Czech","DA":"Danish","DE":"German",
    "EL":"Greek","EN-GB":"English (UK)","EN-US":"English (US)","ES":"Spanish",
    "ET":"Estonian","FI":"Finnish","FR":"French","HU":"Hungarian","ID":"Indonesian",
    "IT":"Italian","JA":"Japanese","KO":"Korean","LT":"Lithuanian","LV":"Latvian",
    "NB":"Norwegian","NL":"Dutch","PL":"Polish","PT-BR":"Portuguese (BR)",
    "PT-PT":"Portuguese (PT)","RO":"Romanian","RU":"Russian","SK":"Slovak",
    "SL":"Slovenian","SV":"Swedish","TR":"Turkish","UK":"Ukrainian",
    "ZH-HANS":"Chinese (Simplified)","ZH-HANT":"Chinese (Traditional)",
}
# DeepL code → Whisper language code
DEEPL_TO_WHISPER = {
    "AR":"ar","BG":"bg","CS":"cs","DA":"da","DE":"de","EL":"el","EN":"en",
    "ES":"es","ET":"et","FI":"fi","FR":"fr","HU":"hu","ID":"id","IT":"it",
    "JA":"ja","KO":"ko","LT":"lt","LV":"lv","NB":"no","NL":"nl","PL":"pl",
    "PT":"pt","RO":"ro","RU":"ru","SK":"sk","SL":"sl","SV":"sv","TR":"tr",
    "UK":"uk","ZH":"zh",
}
# DeepL source code → preferred DeepL target code (for languages with regional variants)
DEEPL_TARGET_DEFAULTS = {
    "EN": "EN-US", "PT": "PT-BR", "ZH": "ZH-HANS",
}

COLOR_PALETTE = [
    {"id":"white","hex":"#FFFFFF","name":"White"},
    {"id":"cream","hex":"#FFF8E1","name":"Cream"},
    {"id":"gold","hex":"#FFD54F","name":"Gold"},
    {"id":"cyan","hex":"#4FC3F7","name":"Cyan"},
    {"id":"mint","hex":"#81C784","name":"Mint"},
    {"id":"coral","hex":"#FF8A80","name":"Coral"},
    {"id":"peach","hex":"#FFAB91","name":"Peach"},
    {"id":"lavender","hex":"#CE93D8","name":"Lavender"},
    {"id":"sky","hex":"#90CAF9","name":"Sky Blue"},
    {"id":"lime","hex":"#C5E1A5","name":"Lime"},
    {"id":"rose","hex":"#F48FB1","name":"Rose"},
    {"id":"aqua","hex":"#80DEEA","name":"Aqua"},
]

BG_OPTIONS = [
    {"id":"navy","hex":"#00004D","name":"Deep Navy"},
    {"id":"indigo","hex":"#1B1B3A","name":"Dark Indigo"},
    {"id":"midnight","hex":"#0D1B2A","name":"Midnight"},
    {"id":"charcoal","hex":"#2D2D2D","name":"Charcoal"},
]

FONT_OPTIONS = [
    {"id":"atkinson","name":"Atkinson Hyperlegible","css":"'Atkinson Hyperlegible Next', sans-serif",
     "note":"Maximum legibility (Latin)"},
    {"id":"noto","name":"Noto Sans","css":"'Noto Sans', 'Noto Sans SC', 'Noto Sans JP', 'Noto Sans KR', 'Noto Sans Arabic', sans-serif",
     "note":"Universal (150+ scripts incl. CJK/Arabic)"},
    {"id":"ibm","name":"IBM Plex Sans","css":"'IBM Plex Sans', 'Noto Sans', sans-serif",
     "note":"Clear, professional, multilingual"},
    {"id":"source","name":"Source Sans 3","css":"'Source Sans 3', 'Noto Sans', sans-serif",
     "note":"Excellent readability, wide support"},
    {"id":"inter","name":"Inter","css":"'Inter', 'Noto Sans', sans-serif",
     "note":"Modern, clean, wide support"},
]

# ── Config ──
DEFAULT_CONFIG = {
    "deepl_api_key": "",
    "session_title": "Live Captioning",
    "input_lang": "EN",
    "translation_count": 1,
    "translations": [{"lang": "ES", "color": "#FFD54F"}],
    "speakers": [],
    "footer_image": None,
    "font_size": 42,
    "max_lines": 3,
    "bg_color": "#00004D",
    "font_family": "atkinson",
    "caption_color": "#FFFFFF",
    "backend": "auto",
    "ui_language": "EN",
    "bidirectional_enabled": False,
    "bidirectional_langs": [],        # e.g., ["EN", "AR"]
    "bidirectional_tuned_swap": False,
}

def load_config():
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, "r") as f:
                return {**DEFAULT_CONFIG, **json.load(f)}
        except Exception:
            pass
    return dict(DEFAULT_CONFIG)

def save_config(cfg):
    with open(CONFIG_PATH, "w") as f:
        json.dump(cfg, f, indent=2)

config = load_config()

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

# ── DeepL ──
def get_deepl_url(key):
    return "https://api-free.deepl.com/v2/translate" if key.strip().endswith(":fx") else "https://api.deepl.com/v2/translate"

def _translate_deepl(text, target_lang, source_lang=None):
    """Translate using DeepL API."""
    api_key = config.get("deepl_api_key", "")
    if not text.strip() or not api_key:
        return ""
    src = source_lang or config.get("input_lang", "EN")
    # Strip region from source (DeepL source doesn't use regions)
    if "-" in src and src not in DEEPL_SOURCE_LANGS:
        src = src.split("-")[0]
    try:
        r = requests.post(get_deepl_url(api_key),
            headers={"Authorization": f"DeepL-Auth-Key {api_key}", "Content-Type": "application/json"},
            json={"text": [text], "source_lang": src, "target_lang": target_lang}, timeout=10)
        result = r.json()
        if "translations" in result and result["translations"]:
            return result["translations"][0]["text"]
        return ""
    except Exception as e:
        log.error(f"DeepL translation error: {e}")
        return ""

def translate_text(text, target_lang, source_lang=None, mode="deepl"):
    """Translate text using DeepL or offline models.

    Args:
        mode: "deepl", "offline-auto", "offline-opus", or "offline-m2m"
    """
    if not text.strip():
        return ""
    if mode == "deepl":
        return _translate_deepl(text, target_lang, source_lang)
    # Offline translation
    engine = "auto"
    if mode == "offline-opus":
        engine = "opus-mt"
    elif mode == "offline-m2m":
        engine = "m2m100"
    src = source_lang or config.get("input_lang", "EN")
    result = offline_translate.translate_offline(text, src, target_lang,
                                                 str(MODELS_DIR), engine=engine)
    if not result:
        log.debug(f"Offline translate ({engine}) {src}->{target_lang}: no result "
                   f"(model may not be downloaded)")
    return result

# ── Audio ──
SAMPLE_RATE = 16000; CHANNELS = 1; DTYPE = "float32"; CHUNK_DURATION = 0.5
SILENCE_THRESHOLD = 0.008; SILENCE_DURATION = 0.7; MAX_SEGMENT_DURATION = 8
INTERIM_INTERVAL = 1.5; MIN_SPEECH_DURATION = 0.3

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("livecaption")

# ── Globals ──
display_app = FastAPI()
extended_app = FastAPI()
operator_app = FastAPI()
dictation_app = FastAPI()
stt_backend = None
display_clients = set()
extended_clients = set()
operator_clients = set()
dictation_clients = set()
shutdown_event = threading.Event()
mic_restart_event = threading.Event()   # signal audio capture to restart
current_mic_index = None                # active mic device index (None = default)
silence_threshold = SILENCE_THRESHOLD
translation_paused = True
captioning_paused = True
dictation_active = False
save_transcripts = True
_session_stamp = time.strftime("%Y%m%d_%H%M%S")
_line_id = 0               # monotonic counter for final lines
_line_id_lock = threading.Lock()
_recent_lines = []          # last N final lines: [{id, text, speaker, src_lang}]
_RECENT_LINES_MAX = 50


# ── Multi-Source Audio ──

class AudioSource:
    """Represents one audio input source with its own capture stream and speaker state."""
    _next_id = 0

    def __init__(self, device_index=None, name=None):
        self.id = AudioSource._next_id
        AudioSource._next_id += 1
        self.device_index = device_index
        self.name = name or f"Source {self.id + 1}"
        self.speaker = ""
        self.color = ""  # empty = use default text color
        self.speaker_change_pending = None  # {"name": str, "time": float}
        self.speaker_lock = threading.Lock()
        self.queue = queue.Queue()
        self.stream = None  # sd.InputStream
        self.capture_thread = None
        self.buffer_thread = None
        self.active = True
        self.restart_event = threading.Event()
        self.current_lang = None  # detected language (set by lang detection in buffer loop)

# Thread-safe source registry
_sources = []  # List[AudioSource]
_sources_lock = threading.Lock()
_transcription_queue = queue.Queue(maxsize=16)  # shared for Whisper/MLX


def get_source(source_id):
    """Get an AudioSource by ID."""
    with _sources_lock:
        for s in _sources:
            if s.id == source_id:
                return s
    return None


def add_source(device_index=None, name=None):
    """Create and register a new AudioSource."""
    if len(_sources) >= 8:
        return None
    src = AudioSource(device_index, name)
    with _sources_lock:
        _sources.append(src)
    return src


def remove_source(source_id):
    """Stop and remove an AudioSource."""
    src = get_source(source_id)
    if not src:
        return False
    src.active = False
    src.restart_event.set()
    if src.stream:
        try:
            src.stream.stop()
            src.stream.close()
        except Exception:
            pass
    with _sources_lock:
        _sources[:] = [s for s in _sources if s.id != source_id]
    return True


# ══════════════════════════════════════════════
# SPEECH BACKENDS
# ══════════════════════════════════════════════

class SpeechBackend(ABC):
    @property
    @abstractmethod
    def name(self): ...
    @abstractmethod
    def process_audio_loop(self, loop): ...


# ── Shared buffer-based audio loop (Whisper + MLX) ──

def _check_speaker_change(source, transcribe_fn, buf, seg_start, loop):
    """Check for pending speaker change on a source. Split buffer 0.5s before the button press.
    Finalizes old speaker's portion, returns remaining buffer for new speaker."""
    with source.speaker_lock:
        sc = source.speaker_change_pending
        if sc:
            source.speaker_change_pending = None
    if not sc:
        return buf, seg_start, False

    old_speaker = source.speaker
    new_speaker = sc["name"]
    new_color = sc.get("color", source.color)
    log.info(f"[{source.name}] Speaker: {old_speaker or '(none)'} -> {new_speaker or '(none)'}")

    # Finalize any buffered audio under the OLD speaker label
    if len(buf) > 0 and seg_start:
        split_time = sc["time"] - 0.5  # 0.5s retroactive
        split_samples = max(0, int((split_time - seg_start) * SAMPLE_RATE))
        if split_samples > int(MIN_SPEECH_DURATION * SAMPLE_RATE) and split_samples < len(buf):
            # Split: old speaker gets audio before the split point
            old_buf = buf[:split_samples]
            source.speaker = old_speaker  # keep old label for this segment
            text = transcribe_fn(old_buf, lang=source.current_lang)
            if text:
                _broadcast_final(text, loop, source, detected_lang=source.current_lang)
            source.speaker = new_speaker
            source.color = new_color
            return buf[split_samples:], split_time, True
        elif split_samples >= len(buf):
            # All buffered audio belongs to old speaker
            source.speaker = old_speaker
            text = transcribe_fn(buf, lang=source.current_lang)
            if text:
                _broadcast_final(text, loop, source, detected_lang=source.current_lang)
            source.speaker = new_speaker
            source.color = new_color
            return np.empty((0,1), dtype=np.float32), sc["time"], True
        # split_samples <= 0: change was before segment start, just relabel

    source.speaker = new_speaker
    source.color = new_color
    return buf, seg_start or sc["time"], True


def _transcription_worker(transcribe_fn, loop):
    """Single worker that processes transcription requests from all sources."""
    while not shutdown_event.is_set():
        try:
            source, buf, detected_lang = _transcription_queue.get(timeout=0.5)
            if not source.active:
                continue
            text = transcribe_fn(buf, lang=detected_lang)
            if text and text.strip():
                _broadcast_final(text.strip(), loop, source, detected_lang=detected_lang)
        except queue.Empty:
            continue
        except Exception as e:
            log.error(f"Transcription error: {e}")


def _get_speaker_lang(source):
    """Check if the current speaker has an assigned language override."""
    if not source.speaker:
        return None
    speaker_langs = config.get("speaker_langs", {})
    return speaker_langs.get(source.speaker)


def _detect_segment_lang(source, buf):
    """Detect the language of an audio segment for bi-directional mode.
    Returns detected DeepL language code, or None if detection is disabled/unavailable."""
    if not config.get("bidirectional_enabled"):
        return None
    bidir_langs = config.get("bidirectional_langs", [])
    if len(bidir_langs) != 2:
        return None

    # Check speaker-level language override first
    speaker_lang = _get_speaker_lang(source)
    if speaker_lang:
        return speaker_lang

    detected_lang = None
    try:
        import lang_detect
        candidates = [DEEPL_TO_WHISPER.get(l, l.lower()) for l in bidir_langs]
        audio_for_detect = buf[:SAMPLE_RATE].flatten()
        lang, conf = lang_detect.detect_language(audio_for_detect, candidates=candidates)
        if conf >= 0.6:
            whisper_to_deepl = {v: k for k, v in DEEPL_TO_WHISPER.items()}
            detected_lang = whisper_to_deepl.get(lang, lang.upper())
        else:
            detected_lang = source.current_lang
    except ImportError:
        if hasattr(stt_backend, '_model') and hasattr(stt_backend._model, 'detect_language'):
            try:
                audio_flat = buf[:SAMPLE_RATE].flatten().astype(np.float32)
                _, lang_probs = stt_backend._model.detect_language(audio_flat)
                if lang_probs:
                    best = max(lang_probs, key=lambda x: x[1])
                    whisper_to_deepl = {v: k for k, v in DEEPL_TO_WHISPER.items()}
                    detected_lang = whisper_to_deepl.get(best[0], best[0].upper())
            except Exception:
                detected_lang = source.current_lang
        else:
            log.warning("Language detection unavailable: install onnxruntime")
            detected_lang = source.current_lang
    except Exception as e:
        log.warning(f"Language detection failed: {e}")
        detected_lang = source.current_lang

    if detected_lang:
        source.current_lang = detected_lang
    return detected_lang


def _buffer_audio_loop(transcribe_fn, loop, source):
    """Per-source audio processing loop for buffer-based backends (Whisper, MLX).
    Handles speaker changes with 0.5s retroactive buffer splitting.
    Submits completed segments to the shared _transcription_queue."""
    buf = np.empty((0,1), dtype=np.float32)
    is_speech = False; silence_start = None; seg_start = None; last_interim = 0
    while not shutdown_event.is_set() and source.active:
        try:
            chunk = source.queue.get(timeout=0.5)
        except queue.Empty:
            continue

        # ── Skip processing when captioning is paused (unless dictation is active) ──
        if captioning_paused and not dictation_active:
            buf = np.empty((0,1), dtype=np.float32)
            is_speech = False; silence_start = None; seg_start = None; last_interim = 0
            continue

        # ── Check for pending speaker change ──
        buf, seg_start, changed = _check_speaker_change(source, transcribe_fn, buf, seg_start, loop)
        if changed:
            last_interim = 0
            if len(buf) == 0:
                is_speech = False; silence_start = None; seg_start = None

        buf = np.concatenate([buf, chunk])
        rms = float(np.sqrt(np.mean(chunk**2))); now = time.time()
        if rms >= silence_threshold:
            if not is_speech:
                is_speech = True; seg_start = seg_start or now; silence_start = None
                _bc(loop, {"type":"status","state":"speech"})
            else:
                silence_start = None
            dur = len(buf) / SAMPLE_RATE
            if (now - last_interim) >= INTERIM_INTERVAL and dur >= 1.0:
                last_interim = now
                text = transcribe_fn(buf, lang=source.current_lang)
                if text:
                    _bc(loop, {"type":"interim","text":text,"speaker":source.speaker,
                               "color":source.color,"source_id":source.id})
                    _translate_all(text, "interim_translation", loop, max_slots=2, source_lang=source.current_lang)
        else:
            if is_speech:
                if silence_start is None:
                    silence_start = now
                elif (now - silence_start) >= SILENCE_DURATION:
                    if len(buf) / SAMPLE_RATE >= MIN_SPEECH_DURATION:
                        detected_lang = _detect_segment_lang(source, buf)
                        try:
                            _transcription_queue.put_nowait((source, buf.copy(), detected_lang))
                        except queue.Full:
                            log.warning(f"Transcription queue full, dropping segment from [{source.name}]")
                    buf = np.empty((0,1), dtype=np.float32)
                    is_speech = False; silence_start = None; seg_start = None; last_interim = 0
                    _bc(loop, {"type":"status","state":"silence"})
        if is_speech and seg_start and (now - seg_start) >= MAX_SEGMENT_DURATION:
            detected_lang = _detect_segment_lang(source, buf)
            try:
                _transcription_queue.put_nowait((source, buf.copy(), detected_lang))
            except queue.Full:
                log.warning(f"Transcription queue full, dropping segment from [{source.name}]")
            buf = np.empty((0,1), dtype=np.float32)
            is_speech = False; silence_start = None; seg_start = now; last_interim = 0


class WhisperBackend(SpeechBackend):
    def __init__(self, model_name, device, compute_type):
        from faster_whisper import WhisperModel
        self._model_name = model_name
        self._device = device
        self._compute_type = compute_type
        # Check for bundled model first (e.g. models/faster-whisper-large-v3-turbo/)
        local_path = MODELS_DIR / f"faster-whisper-{model_name}"
        if local_path.exists() and (local_path / "model.bin").exists():
            log.info(f"Using bundled Whisper model: {local_path}")
            self._model = WhisperModel(str(local_path), device=device, compute_type=compute_type)
        else:
            self._model = WhisperModel(model_name, device=device, compute_type=compute_type)

    @property
    def name(self):
        return f"whisper ({self._model_name}, {self._compute_type}, {self._device})"

    def _transcribe(self, buf, lang=None):
        if lang:
            whisper_lang = lang
        else:
            whisper_lang = DEEPL_TO_WHISPER.get(config.get("input_lang", "EN"), "en")
        try:
            segs, _ = self._model.transcribe(buf.flatten().astype(np.float32),
                language=whisper_lang, beam_size=3, vad_filter=True,
                vad_parameters=dict(min_silence_duration_ms=400, speech_pad_ms=150))
            return " ".join(s.text.strip() for s in segs)
        except Exception as e:
            log.error(f"Whisper error: {e}"); return ""

    def process_audio_loop(self, loop):
        # Start shared transcription worker
        threading.Thread(target=_transcription_worker,
                         args=(self._transcribe, loop), daemon=True).start()
        # Start buffer loops for all registered sources
        with _sources_lock:
            for src in _sources:
                t = threading.Thread(target=_buffer_audio_loop,
                                     args=(self._transcribe, loop, src), daemon=True)
                t.start()
                src.buffer_thread = t


class VoskBackend(SpeechBackend):
    MODELS = {
        "small": {"url":"https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip",
                  "dir":"vosk-model-small-en-us-0.15","size":"~40 MB"},
        "large": {"url":"https://alphacephei.com/vosk/models/vosk-model-en-us-0.22.zip",
                  "dir":"vosk-model-en-us-0.22","size":"~1.8 GB"},
    }
    def __init__(self, model_size="auto"):
        import vosk; vosk.SetLogLevel(-1)
        if model_size == "auto":
            # Use best available model
            if (MODELS_DIR / self.MODELS["large"]["dir"]).exists():
                model_size = "large"
            elif (MODELS_DIR / self.MODELS["small"]["dir"]).exists():
                model_size = "small"
            else:
                model_size = "small"  # default for download attempt
        info = self.MODELS.get(model_size, self.MODELS["small"])
        mp = MODELS_DIR / info["dir"]
        if not mp.exists(): self._dl(info, mp)
        self._model = vosk.Model(str(mp)); self._name = info["dir"]

    def _dl(self, info, mp):
        zp = MODELS_DIR / (info["dir"] + ".zip")
        print(f"\n  Downloading Vosk model ({info['size']})...")
        def prog(bn, bs, ts):
            if ts > 0:
                pct = min(100, bn*bs*100//ts)
                print(f"\r  [{'#'*(pct//3)}{'-'*(33-pct//3)}] {pct}%", end="", flush=True)
        try:
            urllib.request.urlretrieve(info["url"], str(zp), prog)
            print("\n  Extracting...")
            with zipfile.ZipFile(str(zp),"r") as z: z.extractall(str(MODELS_DIR))
            zp.unlink(); print(f"  Model ready\n")
        except PermissionError:
            if zp.exists():
                try: zp.unlink()
                except Exception: pass
            print(f"\n  Cannot write to models directory (permission denied).")
            print(f"  Re-run the installer or use 'Download Tuned Languages' in the launcher.")
            sys.exit(1)
        except Exception as e:
            if zp.exists():
                try: zp.unlink()
                except Exception: pass
            print(f"\n  Download failed: {e}"); sys.exit(1)

    @property
    def name(self): return f"vosk ({self._name})"

    def process_audio_loop(self, loop):
        """Start a Vosk processing loop for each audio source."""
        with _sources_lock:
            for src in _sources:
                t = threading.Thread(target=self._vosk_source_loop,
                                     args=(loop, src), daemon=True)
                t.start()
                src.buffer_thread = t

    def _vosk_source_loop(self, loop, source):
        """Per-source Vosk recognition loop. Each source gets its own KaldiRecognizer."""
        import vosk
        rec = vosk.KaldiRecognizer(self._model, SAMPLE_RATE)
        last_partial = ""; last_pt = 0; in_speech = False
        while not shutdown_event.is_set() and source.active:
            try:
                chunk = source.queue.get(timeout=0.5)
            except queue.Empty:
                continue

            # ── Skip processing when captioning is paused (unless dictation is active) ──
            if captioning_paused and not dictation_active:
                if in_speech:
                    rec.FinalResult()  # reset recognizer state
                    last_partial = ""; in_speech = False
                continue

            # ── Speaker change: force-finalize current recognition ──
            with source.speaker_lock:
                sc = source.speaker_change_pending
                if sc:
                    source.speaker_change_pending = None
            if sc:
                old_speaker = source.speaker
                new_speaker = sc["name"]
                log.info(f"[src {source.id}] Speaker: {old_speaker or '(none)'} -> {new_speaker or '(none)'}")
                # Force Vosk to finalize whatever it has buffered
                result = json.loads(rec.FinalResult())
                text = result.get("text", "").strip()
                if text:
                    # Keep old speaker label for this segment
                    _broadcast_final(text, loop, source)
                source.speaker = new_speaker
                last_partial = ""; in_speech = False

            audio_bytes = (chunk.flatten()*32767).astype(np.int16).tobytes()
            rms = float(np.sqrt(np.mean(chunk**2)))
            if rms >= silence_threshold and not in_speech:
                in_speech = True; _bc(loop, {"type":"status","state":"speech"})
            if rec.AcceptWaveform(audio_bytes):
                text = json.loads(rec.Result()).get("text","").strip()
                if text:
                    _broadcast_final(text, loop, source)
                last_partial = ""; in_speech = False
                _bc(loop, {"type":"status","state":"silence"})
            else:
                pt = json.loads(rec.PartialResult()).get("partial","").strip()
                if pt and pt != last_partial:
                    last_partial = pt
                    _bc(loop, {"type":"interim","text":pt,"speaker":source.speaker,
                               "color":source.color,"source_id":source.id})
                    now = time.time()
                    if (now - last_pt) >= 2.0 and len(pt) > 20:
                        last_pt = now; _translate_all(pt, "interim_translation", loop, max_slots=2)


class MLXWhisperBackend(SpeechBackend):
    """Apple Silicon GPU-accelerated speech recognition via mlx-whisper (Metal)."""
    MODEL_MAP = {
        "large-v3-turbo": "mlx-community/whisper-large-v3-turbo",
        "large-v3": "mlx-community/whisper-large-v3-mlx",
        "medium": "mlx-community/whisper-medium-mlx",
        "small": "mlx-community/whisper-small-mlx",
        "base": "mlx-community/whisper-base-mlx",
        "tiny": "mlx-community/whisper-tiny-mlx",
    }

    def __init__(self, model_name="large-v3-turbo"):
        import mlx_whisper
        self._model_name = model_name
        self._repo = self.MODEL_MAP.get(model_name, model_name)
        log.info(f"Loading MLX Whisper: {self._repo} (Apple Metal GPU)...")
        test_audio = np.zeros(SAMPLE_RATE, dtype=np.float32)
        mlx_whisper.transcribe(test_audio, path_or_hf_repo=self._repo,
                               language="en", word_timestamps=False)
        log.info("MLX Whisper model ready")

    @property
    def name(self):
        return f"mlx-whisper ({self._model_name}, Apple Metal)"

    def _transcribe(self, buf, lang=None):
        import mlx_whisper
        if lang:
            whisper_lang = lang
        else:
            whisper_lang = DEEPL_TO_WHISPER.get(config.get("input_lang", "EN"), "en")
        try:
            result = mlx_whisper.transcribe(
                buf.flatten().astype(np.float32),
                path_or_hf_repo=self._repo,
                language=whisper_lang,
                word_timestamps=False,
            )
            return (result.get("text", "") or "").strip()
        except Exception as e:
            log.error(f"MLX Whisper error: {e}"); return ""

    def process_audio_loop(self, loop):
        # Start shared transcription worker
        threading.Thread(target=_transcription_worker,
                         args=(self._transcribe, loop), daemon=True).start()
        # Start buffer loops for all registered sources
        with _sources_lock:
            for src in _sources:
                t = threading.Thread(target=_buffer_audio_loop,
                                     args=(self._transcribe, loop, src), daemon=True)
                t.start()
                src.buffer_thread = t


# ── Broadcasting ──
def _bc(loop, msg):
    """Broadcast to appropriate clients based on mode.
    In dictation-only mode (captioning paused but dictation active),
    only send to dictation clients."""
    if captioning_paused and dictation_active:
        asyncio.run_coroutine_threadsafe(broadcast_dictation(msg), loop)
    else:
        asyncio.run_coroutine_threadsafe(broadcast_all(msg), loop)

async def broadcast_all(msg):
    data = json.dumps(msg)
    for cs in [display_clients, extended_clients, operator_clients, dictation_clients]:
        dead = set()
        for ws in cs:
            try: await ws.send_text(data)
            except: dead.add(ws)
        cs.difference_update(dead)

async def broadcast_dictation(msg):
    """Broadcast only to dictation clients."""
    data = json.dumps(msg)
    dead = set()
    for ws in dictation_clients:
        try: await ws.send_text(data)
        except: dead.add(ws)
    dictation_clients.difference_update(dead)

def _save_line(lang_code, text):
    """Append a timestamped line to the transcript file for this language."""
    if not save_transcripts or not text.strip():
        return
    try:
        name = DEEPL_TARGET_LANGS.get(lang_code, DEEPL_SOURCE_LANGS.get(lang_code, lang_code))
        safe_name = "".join(c if c.isalnum() or c in " -_" else "" for c in name).strip().replace(" ","_")
        fn = f"transcript_{_session_stamp}_{safe_name}_{lang_code}.txt"
        ts = time.strftime("%H:%M:%S")
        with open(TRANSCRIPTS_DIR / fn, "a", encoding="utf-8") as f:
            f.write(f"[{ts}] {text}\n")
    except Exception as e:
        log.warning(f"Transcript save error: {e}")

def _next_line_id():
    global _line_id
    with _line_id_lock:
        _line_id += 1
        return _line_id

def _store_recent_line(lid, text, speaker, src_lang):
    with _line_id_lock:
        _recent_lines.append({"id": lid, "text": text, "speaker": speaker, "src_lang": src_lang})
        while len(_recent_lines) > _RECENT_LINES_MAX:
            _recent_lines.pop(0)

def _broadcast_final(text, loop, source=None, detected_lang=None):
    """Broadcast final source text with speaker, save transcript, trigger translations.
    In dictation-only mode, only sends to dictation clients (no translations/transcripts).
    source: AudioSource instance (required for speaker/color/source_id; defaults to empty strings if None)."""
    speaker = source.speaker if source else ""
    color = source.color if source else ""
    source_id = source.id if source else 0
    lid = _next_line_id()
    if captioning_paused and dictation_active:
        # Dictation-only mode: just send final text to dictation clients
        asyncio.run_coroutine_threadsafe(
            broadcast_dictation({"type":"final","text":text,"speaker":speaker,
                                 "color":color,"source_id":source_id,"line_id":lid,
                                 "detected_lang":detected_lang}), loop)
        log.info(f"   DICTATION: {text}")
        return
    _bc(loop, {"type":"final","text":text,"speaker":speaker,"color":color,
               "source_id":source_id,"line_id":lid,"detected_lang":detected_lang})
    prefix = f"{speaker}: " if speaker else ""
    log.info(f"   IN: {prefix}{text}")
    src = config.get("input_lang", "EN")
    _save_line(src, f"{prefix}{text}")
    _store_recent_line(lid, text, speaker, src)
    _translate_all(text, "final_translation", loop, line_id=lid, source_lang=detected_lang)

def _translate_all(text, msg_type, loop, max_slots=99, line_id=None, speaker_override=None, source_lang=None):
    if translation_paused:
        return
    if captioning_paused and dictation_active:
        return  # Dictation-only mode: no translations
    translations = config.get("translations", [])
    for i, t in enumerate(translations):
        if i >= max_slots: break
        threading.Thread(target=_do_translate,
            args=(text, t["lang"], i, msg_type, loop, line_id, speaker_override, source_lang), daemon=True).start()

def _do_translate(text, lang, slot, msg_type, loop, line_id=None, speaker_override=None, source_lang=None):
    translations = config.get("translations", [])
    mode = "deepl"
    if slot < len(translations):
        mode = translations[slot].get("mode", "deepl")
    try:
        translated = translate_text(text, lang, source_lang=source_lang, mode=mode)
    except Exception as e:
        engine = "offline" if mode.startswith("offline") else "DeepL"
        log.error(f"   [{slot}] {lang} ({engine}) translation error: {e}")
        return
    if not translated:
        if msg_type == "final_translation" and mode.startswith("offline"):
            log.warning(f"   [{slot}] {lang} offline translation returned empty "
                        f"(mode={mode}) — check model availability")
        return
    speaker = speaker_override if speaker_override is not None else ""
    msg = {"type": msg_type, "translated": translated, "lang": lang, "slot": slot, "speaker": speaker}
    if line_id is not None:
        msg["line_id"] = line_id
    _bc(loop, msg)
    if msg_type == "final_translation":
        prefix = f"{speaker}: " if speaker else ""
        engine = "offline" if mode.startswith("offline") else "DeepL"
        log.info(f"   [{slot}] {lang} ({engine}): {prefix}{translated}")
        _save_line(lang, f"{prefix}{translated}")
    elif msg_type == "correct_translation":
        prefix = f"{speaker}: " if speaker else ""
        log.info(f"   [{slot}] {lang} CORRECTED: {prefix}{translated}")
        _save_line(lang, f"[corrected] {prefix}{translated}")


# ── Audio Capture ──
def _make_audio_callback(source):
    """Create a callback bound to a specific AudioSource's queue."""
    def callback(indata, frames, ti, status):
        if status:
            log.warning(f"Audio [{source.name}]: {status}")
        source.queue.put(indata.copy())
    return callback


def start_source_capture(source):
    """Open audio stream for a single AudioSource. Runs in its own thread."""
    bs = int(SAMPLE_RATE * CHUNK_DURATION)
    while source.active and not shutdown_event.is_set():
        source.restart_event.clear()
        try:
            cb = _make_audio_callback(source)
            s = sd.InputStream(samplerate=SAMPLE_RATE, channels=CHANNELS, dtype=DTYPE,
                               blocksize=bs, device=source.device_index, callback=cb)
            source.stream = s
            s.start()
            log.info(f"Audio capture started for [{source.name}] (device: {source.device_index or 'default'})")
            while source.active and not shutdown_event.is_set() and not source.restart_event.is_set():
                shutdown_event.wait(0.3)
            s.stop()
            s.close()
            source.stream = None
            if source.restart_event.is_set() and source.active:
                log.info(f"Restarting capture for [{source.name}]")
                continue
            break
        except Exception as e:
            log.error(f"Audio capture error [{source.name}]: {e}")
            source.stream = None
            break


def start_audio_capture(dev_idx=None):
    """Legacy single-source capture. Creates Source 0 if not exists."""
    src = get_source(0)
    if not src:
        src = add_source(dev_idx, "Microphone")
    start_source_capture(src)


# ══════════════════════════════════════════════
# SHARED CONFIG BUILDER
# ══════════════════════════════════════════════

def _style_config():
    """Common style config for all display clients."""
    return {
        "session_title": config.get("session_title", "Live Captioning"),
        "input_lang": config.get("input_lang", "EN"),
        "input_lang_name": DEEPL_SOURCE_LANGS.get(config.get("input_lang","EN"), "English"),
        "footer_image": config.get("footer_image"),
        "font_size": config.get("font_size", 42),
        "max_lines": config.get("max_lines", 3),
        "bg_color": config.get("bg_color", "#00004D"),
        "font_family": config.get("font_family", "atkinson"),
        "caption_color": config.get("caption_color", "#FFFFFF"),
        "speakers": config.get("speakers", []),
    }

def _font_css(fid):
    for f in FONT_OPTIONS:
        if f["id"] == fid: return f["css"]
    return FONT_OPTIONS[0]["css"]

def _translations_for_slots(slot_start, slot_end):
    """Return translation configs for given slot range."""
    all_t = config.get("translations", [])
    result = []
    for i in range(slot_start, min(slot_end + 1, len(all_t))):
        t = all_t[i]
        lang_name = DEEPL_TARGET_LANGS.get(t["lang"], t["lang"])
        result.append({"lang": t["lang"], "name": lang_name, "color": t.get("color","#FFD54F"), "slot": i})
    return result


# ══════════════════════════════════════════════
# DISPLAY APP (Port 3000) — caption + slots 0-1
# ══════════════════════════════════════════════

@display_app.get("/")
async def d_index(): return FileResponse(BASE_DIR / "display.html")

@display_app.get("/uploads/{fn}")
async def d_uploads(fn: str):
    p = UPLOADS_DIR / fn
    return FileResponse(p) if p.exists() else JSONResponse({"error":"not found"}, 404)

@display_app.get("/api/config")
async def d_config():
    sc = _style_config()
    sc["translations"] = _translations_for_slots(0, 1)
    sc["show_caption"] = True
    sc["font_css"] = _font_css(config.get("font_family","atkinson"))
    return JSONResponse(sc)

@display_app.get("/api/locales/{lang}")
async def d_get_locale(lang: str):
    """Serve translation JSON for a language."""
    locale_path = BASE_DIR / "locales" / f"{lang.lower()}.json"
    if locale_path.exists():
        data = json.loads(locale_path.read_text(encoding="utf-8"))
        return JSONResponse(data)
    # Fallback to English
    en_path = BASE_DIR / "locales" / "en.json"
    if en_path.exists():
        return JSONResponse(json.loads(en_path.read_text(encoding="utf-8")))
    return JSONResponse({})

@display_app.websocket("/ws")
async def d_ws(ws: WebSocket):
    await ws.accept(); display_clients.add(ws)
    await ws.send_text(json.dumps({"type":"status","state":"connected",
        "ui_language": config.get("ui_language", "EN")}))
    # Send source list
    with _sources_lock:
        source_list = [{"id": s.id, "name": s.name, "speaker": s.speaker,
                        "color": s.color} for s in _sources]
    await ws.send_json({"type": "source_list", "sources": source_list})
    try:
        while True: await ws.receive_text()
    except WebSocketDisconnect: display_clients.discard(ws)


# ══════════════════════════════════════════════
# EXTENDED APP (Port 3002) — slots 2-4
# ══════════════════════════════════════════════

@extended_app.get("/")
async def e_index(): return FileResponse(BASE_DIR / "display.html")

@extended_app.get("/uploads/{fn}")
async def e_uploads(fn: str):
    p = UPLOADS_DIR / fn
    return FileResponse(p) if p.exists() else JSONResponse({"error":"not found"}, 404)

@extended_app.get("/api/config")
async def e_config():
    sc = _style_config()
    sc["translations"] = _translations_for_slots(2, 4)
    sc["show_caption"] = True
    sc["font_css"] = _font_css(config.get("font_family","atkinson"))
    return JSONResponse(sc)

@extended_app.websocket("/ws")
async def e_ws(ws: WebSocket):
    await ws.accept(); extended_clients.add(ws)
    await ws.send_text(json.dumps({"type":"status","state":"connected",
        "ui_language": config.get("ui_language", "EN")}))
    # Send source list
    with _sources_lock:
        source_list = [{"id": s.id, "name": s.name, "speaker": s.speaker,
                        "color": s.color} for s in _sources]
    await ws.send_json({"type": "source_list", "sources": source_list})
    try:
        while True: await ws.receive_text()
    except WebSocketDisconnect: extended_clients.discard(ws)


# ══════════════════════════════════════════════
# OPERATOR APP (Port 3001)
# ══════════════════════════════════════════════

@operator_app.get("/")
async def o_index(): return FileResponse(BASE_DIR / "operator.html")

@operator_app.get("/uploads/{fn}")
async def o_uploads(fn: str):
    p = UPLOADS_DIR / fn
    return FileResponse(p) if p.exists() else JSONResponse({"error":"not found"}, 404)

@operator_app.get("/api/config")
async def o_config():
    try:
        try:
            tuned_info = tuned_models.get_all_status(MODELS_DIR)
        except Exception:
            tuned_info = {}
        try:
            is_whisper = isinstance(stt_backend, WhisperBackend) if stt_backend else False
        except Exception:
            is_whisper = False
        active_tuned = ""
        try:
            if is_whisper and stt_backend and stt_backend._model_name.startswith("tuned-"):
                active_tuned = stt_backend._model_name.replace("tuned-", "").upper()
        except Exception:
            pass

        try:
            offline_info = offline_translate.get_all_status(MODELS_DIR)
        except Exception:
            offline_info = {"opus": {}, "m2m100": {}}

        return JSONResponse({
            **_style_config(),
            "deepl_api_key": config.get("deepl_api_key",""),
            "has_api_key": bool(config.get("deepl_api_key","")),
            "backend": stt_backend.name if stt_backend else "loading...",
            "is_whisper": is_whisper,
            "translation_count": config.get("translation_count", 1),
            "translations": config.get("translations", []),
            "font_css": _font_css(config.get("font_family","atkinson")),
            "source_langs": DEEPL_SOURCE_LANGS,
            "target_langs": DEEPL_TARGET_LANGS,
            "color_palette": COLOR_PALETTE,
            "bg_options": BG_OPTIONS,
            "font_options": FONT_OPTIONS,
            "tuned_models": tuned_info,
            "active_tuned_lang": active_tuned,
            "offline_translate": offline_info,
            "bidirectional_enabled": config.get("bidirectional_enabled", False),
            "bidirectional_langs": config.get("bidirectional_langs", []),
            "bidirectional_tuned_swap": config.get("bidirectional_tuned_swap", False),
            "speaker_langs": config.get("speaker_langs", {}),
        })
    except Exception as e:
        # Fallback: return at minimum the essential data so dropdowns populate
        log.error(f"Config endpoint error: {e}")
        return JSONResponse({
            **_style_config(),
            "has_api_key": bool(config.get("deepl_api_key","")),
            "backend": "error",
            "is_whisper": False,
            "translation_count": config.get("translation_count", 1),
            "translations": config.get("translations", []),
            "font_css": _font_css(config.get("font_family","atkinson")),
            "source_langs": DEEPL_SOURCE_LANGS,
            "target_langs": DEEPL_TARGET_LANGS,
            "color_palette": COLOR_PALETTE,
            "bg_options": BG_OPTIONS,
            "font_options": FONT_OPTIONS,
            "tuned_models": {},
            "active_tuned_lang": "",
            "offline_translate": {"opus": {}, "m2m100": {}},
            "bidirectional_enabled": config.get("bidirectional_enabled", False),
            "bidirectional_langs": config.get("bidirectional_langs", []),
            "bidirectional_tuned_swap": config.get("bidirectional_tuned_swap", False),
            "speaker_langs": config.get("speaker_langs", {}),
        })

@operator_app.get("/api/locales/{lang}")
async def o_get_locale(lang: str):
    """Serve translation JSON for a language."""
    locale_path = BASE_DIR / "locales" / f"{lang.lower()}.json"
    if locale_path.exists():
        data = json.loads(locale_path.read_text(encoding="utf-8"))
        return JSONResponse(data)
    # Fallback to English
    en_path = BASE_DIR / "locales" / "en.json"
    if en_path.exists():
        return JSONResponse(json.loads(en_path.read_text(encoding="utf-8")))
    return JSONResponse({})

@operator_app.post("/api/config")
async def o_update(
    session_title: str = Form(None), deepl_api_key: str = Form(None),
    input_lang: str = Form(None), translation_count: int = Form(None),
    translations_json: str = Form(None), speakers: str = Form(None),
    font_size: int = Form(None), max_lines: int = Form(None),
    bg_color: str = Form(None), font_family: str = Form(None),
    caption_color: str = Form(None), ui_language: str = Form(None),
    bidirectional_enabled: str = Form(None),
    bidirectional_langs: str = Form(None),
    bidirectional_tuned_swap: str = Form(None),
    speaker_langs: str = Form(None),
):
    if session_title is not None: config["session_title"] = session_title
    if deepl_api_key is not None: config["deepl_api_key"] = deepl_api_key
    if input_lang is not None: config["input_lang"] = input_lang
    if translation_count is not None: config["translation_count"] = translation_count
    if translations_json is not None:
        try: config["translations"] = json.loads(translations_json)
        except: pass
    if speakers is not None:
        try: config["speakers"] = json.loads(speakers)
        except: pass
    if font_size is not None: config["font_size"] = max(24, min(960, font_size))
    if max_lines is not None: config["max_lines"] = max(1, min(8, max_lines))
    if bg_color is not None: config["bg_color"] = bg_color
    if font_family is not None: config["font_family"] = font_family
    if caption_color is not None: config["caption_color"] = caption_color
    if ui_language is not None: config["ui_language"] = ui_language

    # ── Bi-directional config ──
    if speaker_langs is not None:
        try: config["speaker_langs"] = json.loads(speaker_langs)
        except: pass
    if bidirectional_tuned_swap is not None:
        config["bidirectional_tuned_swap"] = bidirectional_tuned_swap.lower() in ("true", "1", "yes")
    if bidirectional_langs is not None:
        try: config["bidirectional_langs"] = json.loads(bidirectional_langs)
        except: pass
    if bidirectional_enabled is not None:
        new_enabled = bidirectional_enabled.lower() in ("true", "1", "yes")
        was_enabled = config.get("bidirectional_enabled", False)
        config["bidirectional_enabled"] = new_enabled

        if new_enabled and not was_enabled:
            # Toggled ON: auto-create 2 translation slots at positions 0-1
            bidir_langs = config.get("bidirectional_langs", [])
            if len(bidir_langs) == 2:
                existing = config.get("translations", [])
                auto_slots = []
                for bl in bidir_langs:
                    target = DEEPL_TARGET_DEFAULTS.get(bl, bl)
                    auto_slots.append({"lang": target, "color": "#FFD54F", "auto_bidir": True})
                config["translations"] = auto_slots + existing
                config["translation_count"] = len(config["translations"])
        elif was_enabled and not new_enabled:
            # Toggled OFF: remove auto-managed slots 0-1, shift user slots back
            existing = config.get("translations", [])
            user_slots = [t for t in existing if not t.get("auto_bidir")]
            config["translations"] = user_slots
            config["translation_count"] = len(user_slots)

    save_config(config)

    update_msg = {
        "type": "config_update",
        **_style_config(),
        "translation_count": config.get("translation_count",1),
        "font_css": _font_css(config.get("font_family","atkinson")),
        "ui_language": config.get("ui_language", "EN"),
    }
    # Only trigger section rebuild on display when translations actually changed
    if (translations_json is not None or translation_count is not None
            or input_lang is not None or bidirectional_enabled is not None):
        update_msg["all_translations"] = config.get("translations",[])
    await broadcast_all(update_msg)
    return JSONResponse({"status":"ok"})

@operator_app.post("/api/upload-footer")
async def o_upload_footer(file: UploadFile = File(...)):
    ext = Path(file.filename).suffix.lower()
    if ext not in [".png",".jpg",".jpeg",".gif",".svg",".webp"]:
        return JSONResponse({"error":"bad type"}, 400)
    fn = f"footer{ext}"; dest = UPLOADS_DIR / fn
    with open(dest,"wb") as f: shutil.copyfileobj(file.file, f)
    config["footer_image"] = fn; save_config(config)
    await broadcast_all({"type":"config_update", **_style_config(),
        "font_css": _font_css(config.get("font_family","atkinson")),
        "translation_count": config.get("translation_count",1),
        "all_translations": config.get("translations",[])})
    return JSONResponse({"status":"ok","filename":fn})

@operator_app.post("/api/remove-footer")
async def o_rm_footer():
    config["footer_image"] = None; save_config(config)
    await broadcast_all({"type":"config_update", **_style_config(),
        "font_css": _font_css(config.get("font_family","atkinson")),
        "translation_count": config.get("translation_count",1),
        "all_translations": config.get("translations",[])})
    return JSONResponse({"status":"ok"})

# ── Tuned Models API ──

@operator_app.get("/api/tuned-models")
async def o_tuned_models():
    """List all tuned models with download/availability status."""
    return JSONResponse(tuned_models.get_all_status(MODELS_DIR))


@operator_app.post("/api/tuned-models/download")
async def o_tuned_download(lang: str = Form(...)):
    """Start downloading and converting a tuned model for a language."""
    lang = lang.upper()
    if lang not in tuned_models.TUNED_MODELS:
        return JSONResponse({"error": f"No tuned model for {lang}"}, 400)

    if tuned_models.is_available(MODELS_DIR, lang):
        return JSONResponse({"status": "already_available"})

    prog = tuned_models.get_progress(lang)
    if prog["status"] in ("downloading", "converting", "starting"):
        return JSONResponse({"status": "already_in_progress"})

    # Detect VRAM for quantization
    gpu = detect_gpu()
    vram = gpu.get("vram", 0)

    tuned_models.download_and_convert(MODELS_DIR, lang, vram_mb=vram)
    return JSONResponse({"status": "started", "vram": vram})


@operator_app.get("/api/tuned-models/progress/{lang}")
async def o_tuned_progress(lang: str):
    """Get download/conversion progress for a language."""
    return JSONResponse(tuned_models.get_progress(lang.upper()))


@operator_app.post("/api/tuned-models/switch")
async def o_tuned_switch(lang: str = Form(...)):
    """Hot-swap the active Whisper model to a tuned model for this language.
    Pauses captioning briefly (~2-5s) during the swap."""
    global stt_backend, captioning_paused
    lang = lang.upper()

    if not isinstance(stt_backend, WhisperBackend):
        return JSONResponse({"error": "Model switching only works with Whisper backend"}, 400)

    model_path = tuned_models.get_model_path(MODELS_DIR, lang)
    if not tuned_models.is_available(MODELS_DIR, lang):
        return JSONResponse({"error": f"Tuned model for {lang} not downloaded"}, 400)

    was_paused = captioning_paused
    try:
        # Step 1: Pause captioning to let audio loop drain
        captioning_paused = True
        await broadcast_all({"type": "captioning_paused", "paused": True})
        await asyncio.sleep(1.5)  # let audio loop drain its queue

        # Step 2: Swap the model
        log.info(f"Hot-swapping to tuned model for {lang}: {model_path}")
        from faster_whisper import WhisperModel
        old_device = stt_backend._device
        old_compute = stt_backend._compute_type

        new_model = WhisperModel(str(model_path), device=old_device,
                                 compute_type=old_compute)
        stt_backend._model = new_model
        stt_backend._model_name = f"tuned-{lang.lower()}"
        log.info(f"Model swapped to tuned-{lang.lower()} ({old_compute}, {old_device})")

        # Step 3: Resume captioning
        if not was_paused:
            captioning_paused = False
            await broadcast_all({"type": "captioning_paused", "paused": False})

        # Notify all clients of new model
        await broadcast_all({"type": "status", "model": stt_backend.name})

        return JSONResponse({"status": "ok", "model": stt_backend.name})

    except Exception as e:
        log.error(f"Hot-swap failed: {e}")
        if not was_paused:
            captioning_paused = False
            await broadcast_all({"type": "captioning_paused", "paused": False})
        return JSONResponse({"error": str(e)}, 500)


@operator_app.post("/api/tuned-models/revert")
async def o_tuned_revert():
    """Revert to the default Whisper model (large-v3-turbo)."""
    global stt_backend, captioning_paused

    if not isinstance(stt_backend, WhisperBackend):
        return JSONResponse({"error": "Model switching only works with Whisper backend"}, 400)

    was_paused = captioning_paused
    try:
        captioning_paused = True
        await broadcast_all({"type": "captioning_paused", "paused": True})
        await asyncio.sleep(1.5)

        log.info("Reverting to default Whisper model")
        from faster_whisper import WhisperModel
        old_device = stt_backend._device
        old_compute = stt_backend._compute_type
        default_model = "large-v3-turbo"

        new_model = WhisperModel(default_model, device=old_device,
                                 compute_type=old_compute)
        stt_backend._model = new_model
        stt_backend._model_name = default_model
        log.info(f"Reverted to {default_model} ({old_compute}, {old_device})")

        if not was_paused:
            captioning_paused = False
            await broadcast_all({"type": "captioning_paused", "paused": False})

        await broadcast_all({"type": "status", "model": stt_backend.name})
        return JSONResponse({"status": "ok", "model": stt_backend.name})

    except Exception as e:
        log.error(f"Revert failed: {e}")
        if not was_paused:
            captioning_paused = False
            await broadcast_all({"type": "captioning_paused", "paused": False})
        return JSONResponse({"error": str(e)}, 500)


# ── Offline Translation API ──

@operator_app.get("/api/offline-translate/status")
async def o_offline_status():
    """Get status of all offline translation models."""
    return JSONResponse(offline_translate.get_all_status(MODELS_DIR))

@operator_app.post("/api/offline-translate/download-opus")
async def o_offline_download_opus(lang: str = Form(...)):
    """Start downloading an OPUS-MT model for a language."""
    lang = lang.upper()
    if not offline_translate.has_opus_model(lang):
        return JSONResponse({"error": f"No OPUS-MT model for {lang}"}, 400)
    if offline_translate.is_opus_available(str(MODELS_DIR), lang):
        return JSONResponse({"status": "already_available"})
    key = f"opus-{lang}"
    prog = offline_translate.get_progress(key)
    if prog["status"] in ("downloading", "converting", "starting"):
        return JSONResponse({"status": "already_in_progress"})
    offline_translate.download_opus_model(str(MODELS_DIR), lang)
    return JSONResponse({"status": "started"})

@operator_app.post("/api/offline-translate/download-m2m")
async def o_offline_download_m2m():
    """Start downloading M2M-100 1.2B model."""
    if offline_translate.is_m2m_available(str(MODELS_DIR)):
        return JSONResponse({"status": "already_available"})
    prog = offline_translate.get_progress("m2m100")
    if prog["status"] in ("downloading", "converting", "starting"):
        return JSONResponse({"status": "already_in_progress"})
    offline_translate.download_m2m_model(str(MODELS_DIR))
    return JSONResponse({"status": "started"})

@operator_app.get("/api/offline-translate/progress/{key}")
async def o_offline_progress(key: str):
    """Get download progress for a model (e.g. 'opus-ES', 'm2m100')."""
    return JSONResponse(offline_translate.get_progress(key))


@operator_app.get("/api/mics")
async def o_list_mics():
    """List available microphones."""
    devs = sd.query_devices()
    default_idx = sd.default.device[0]
    mics = []
    for i, d in enumerate(devs):
        if d["max_input_channels"] > 0:
            mics.append({"index": i, "name": d["name"],
                         "is_default": i == default_idx})
    return JSONResponse({"mics": mics, "current": current_mic_index})

@operator_app.post("/api/set-mic")
async def o_set_mic(request: Request):
    """Change the active microphone without restarting the server."""
    global current_mic_index
    form = await request.form()
    raw = form.get("mic_index", "")
    new_idx = int(raw) if raw not in ("", "null", "None") else None
    if new_idx == current_mic_index:
        return JSONResponse({"status": "ok", "changed": False,
                             "mic_index": current_mic_index})
    current_mic_index = new_idx
    mic_restart_event.set()
    name = sd.query_devices(new_idx)["name"] if new_idx is not None else "default"
    log.info(f"Mic change requested: [{new_idx or 'default'}] {name}")
    return JSONResponse({"status": "ok", "changed": True,
                         "mic_index": current_mic_index, "mic_name": name})

@operator_app.get("/api/sources")
async def api_list_sources():
    """List all active audio sources."""
    with _sources_lock:
        return JSONResponse([{
            "id": s.id, "name": s.name, "speaker": s.speaker,
            "color": s.color, "device_index": s.device_index
        } for s in _sources])

@operator_app.post("/api/sources/add")
async def api_add_source(request: Request):
    """Add a new audio source at runtime."""
    data = await request.json()
    dev_idx = data.get("device_index")
    name = data.get("name")
    src = add_source(dev_idx, name)
    if not src:
        return JSONResponse({"error": "Maximum 8 sources"}, status_code=400)
    # Start capture thread
    t = threading.Thread(target=start_source_capture, args=(src,), daemon=True)
    t.start()
    src.capture_thread = t
    await broadcast_all({"type": "source_added", "source": {
        "id": src.id, "name": src.name, "speaker": src.speaker, "color": src.color}})
    return JSONResponse({"id": src.id, "name": src.name})

@operator_app.post("/api/sources/remove")
async def api_remove_source(request: Request):
    """Remove an audio source at runtime."""
    data = await request.json()
    source_id = data.get("source_id")
    if remove_source(source_id):
        await broadcast_all({"type": "source_removed", "source_id": source_id})
        return JSONResponse({"ok": True})
    return JSONResponse({"error": "Source not found"}, status_code=404)

@operator_app.post("/api/speakers/reset")
async def api_reset_speakers():
    """Reset all speaker names and colors to defaults."""
    with _sources_lock:
        for s in _sources:
            s.speaker = ""
            s.color = ""
    _save_speaker_config()
    # Send updated source list to all clients
    with _sources_lock:
        source_list = [{"id": s.id, "name": s.name, "speaker": s.speaker,
                        "color": s.color} for s in _sources]
    await broadcast_all({"type": "source_list", "sources": source_list})
    return JSONResponse({"ok": True})

@operator_app.websocket("/ws")
async def o_ws(ws: WebSocket):
    await ws.accept(); operator_clients.add(ws)
    await ws.send_text(json.dumps({"type":"status","state":"connected",
        "model": stt_backend.name if stt_backend else "loading",
        "ui_language": config.get("ui_language", "EN")}))
    # Send source list
    with _sources_lock:
        source_list = [{"id": s.id, "name": s.name, "speaker": s.speaker,
                        "color": s.color} for s in _sources]
    await ws.send_json({"type": "source_list", "sources": source_list})
    try:
        while True:
            msg = json.loads(await ws.receive_text())
            if msg.get("type") == "set_threshold":
                global silence_threshold
                silence_threshold = float(msg.get("value", SILENCE_THRESHOLD))
            elif msg.get("type") == "set_speaker":
                new_name = msg.get("speaker", "")
                source_id = msg.get("source_id")  # None = all sources
                change = {"name": new_name, "time": time.time()}
                # Propagate speaker change to all matching sources (Whisper/MLX/Vosk)
                with _sources_lock:
                    for src in _sources:
                        if source_id is None or src.id == source_id:
                            with src.speaker_lock:
                                src.speaker_change_pending = dict(change)
                await broadcast_all({"type":"speaker_change","speaker":new_name})
                _save_speaker_config()
            elif msg.get("type") == "clear_captions":
                await broadcast_all({"type":"clear_captions"})
            elif msg.get("type") == "set_translation_paused":
                global translation_paused
                translation_paused = bool(msg.get("paused", False))
                log.info(f"Translation {'PAUSED' if translation_paused else 'RESUMED'}")
                await broadcast_all({"type":"translation_paused","paused":translation_paused})
            elif msg.get("type") == "set_captioning_paused":
                global captioning_paused
                captioning_paused = bool(msg.get("paused", False))
                log.info(f"Captioning {'PAUSED' if captioning_paused else 'LIVE'}")
                await broadcast_all({"type":"captioning_paused","paused":captioning_paused})
            elif msg.get("type") == "set_save_transcripts":
                global save_transcripts
                save_transcripts = bool(msg.get("enabled", True))
                log.info(f"Transcript saving {'ON' if save_transcripts else 'OFF'}")
                await broadcast_all({"type":"save_transcripts","enabled":save_transcripts})
            elif msg.get("type") == "correct_caption":
                lid = msg.get("line_id")
                new_text = msg.get("text", "").strip()
                if lid is not None and new_text:
                    # Find the original line
                    original = None
                    with _line_id_lock:
                        for ln in _recent_lines:
                            if ln["id"] == lid:
                                original = dict(ln)
                                ln["text"] = new_text  # update buffer
                                break
                    if original:
                        speaker = original["speaker"]
                        src_lang = original["src_lang"]
                        log.info(f"   CORRECTION [{lid}]: {new_text}")
                        # Broadcast corrected caption to all clients
                        await broadcast_all({"type":"correct_line","line_id":lid,
                            "text":new_text,"speaker":speaker})
                        # Save corrected line to transcript
                        prefix = f"{speaker}: " if speaker else ""
                        _save_line(src_lang, f"[corrected] {prefix}{new_text}")
                        # Re-translate in background threads
                        loop = asyncio.get_event_loop()
                        _translate_all(new_text, "correct_translation", loop,
                                       line_id=lid, speaker_override=speaker)
    except WebSocketDisconnect: operator_clients.discard(ws)
    except Exception: operator_clients.discard(ws)


# ══════════════════════════════════════════════
# DICTATION APP (Port 3005) — plain voice-to-text
# ══════════════════════════════════════════════

@dictation_app.get("/")
async def dict_index(): return FileResponse(BASE_DIR / "dictation.html")

@dictation_app.get("/api/dictation-config")
async def dict_config():
    d = config.get("dictation_dir", str(TRANSCRIPTS_DIR))
    return JSONResponse({"dictation_dir": d})

@dictation_app.post("/api/dictation-config")
async def dict_update_config(dictation_dir: str = Form(None)):
    if dictation_dir is not None:
        p = Path(dictation_dir)
        try:
            p.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            return JSONResponse({"error": str(e)}, 400)
        config["dictation_dir"] = str(p)
        save_config(config)
    return JSONResponse({"status": "ok", "dictation_dir": config.get("dictation_dir", str(TRANSCRIPTS_DIR))})

@dictation_app.post("/api/dictation-save")
async def dict_save(text: str = Form(...), filename: str = Form(None)):
    d = Path(config.get("dictation_dir", str(TRANSCRIPTS_DIR)))
    d.mkdir(parents=True, exist_ok=True)
    if not filename:
        filename = f"dictation_{time.strftime('%Y%m%d_%H%M%S')}.txt"
    # Sanitize filename
    filename = "".join(c if c.isalnum() or c in ".-_ " else "" for c in filename).strip()
    if not filename.endswith(".txt"):
        filename += ".txt"
    fp = d / filename
    with open(fp, "w", encoding="utf-8") as f:
        f.write(text)
    log.info(f"Dictation saved: {fp}")
    return JSONResponse({"status": "ok", "path": str(fp)})

@dictation_app.websocket("/ws")
async def dict_ws(ws: WebSocket):
    global dictation_active
    await ws.accept(); dictation_clients.add(ws)
    await ws.send_text(json.dumps({"type":"status","state":"connected",
        "dictation_active": dictation_active}))
    try:
        while True:
            msg = json.loads(await ws.receive_text())
            if msg.get("type") == "set_dictation_active":
                dictation_active = bool(msg.get("active", False))
                log.info(f"Dictation {'ACTIVE' if dictation_active else 'STOPPED'}")
                await broadcast_dictation({"type":"dictation_active","active":dictation_active})
    except WebSocketDisconnect:
        dictation_clients.discard(ws)
        if not dictation_clients:
            dictation_active = False
    except Exception:
        dictation_clients.discard(ws)
        if not dictation_clients:
            dictation_active = False


# ── Startup ──
def setup_events(app, role):
    @app.on_event("startup")
    async def startup():
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
    async def shutdown(): shutdown_event.set()

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
    except: return r
    if sys.platform=="win32":
        for p in _cuda_lib_paths:
            if os.path.isdir(p):
                for f in os.listdir(p):
                    if "cublas64_12" in f.lower(): r["has_cuda"]=True; return r
    else:
        try:
            if "libcublas" in subprocess.check_output(["ldconfig","-p"],
                stderr=subprocess.DEVNULL, timeout=5).decode(): r["has_cuda"]=True
        except: pass
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
    if args.sources:
        seen_devices = set()
        for idx_str in args.sources.split(","):
            idx = int(idx_str.strip())
            dev = None if idx == -1 else idx
            if dev in seen_devices:
                log.warning(f"Skipping duplicate audio device: {dev}")
                continue
            seen_devices.add(dev)
            add_source(dev)
    elif args.mic is not None:
        add_source(args.mic)
    else:
        add_source(None)  # system default

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
    except KeyboardInterrupt: print("\n  Shutting down..."); shutdown_event.set()

if __name__ == "__main__": main()
