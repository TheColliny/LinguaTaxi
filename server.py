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
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, UploadFile, File, Form
from fastapi.responses import FileResponse, JSONResponse

# ── Paths ──
BASE_DIR = Path(__file__).parent
CONFIG_PATH = BASE_DIR / "config.json"
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

# ── DeepL ──
def get_deepl_url(key):
    return "https://api-free.deepl.com/v2/translate" if key.strip().endswith(":fx") else "https://api.deepl.com/v2/translate"

def translate_text(text, target_lang, source_lang=None):
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
        log.error(f"Translation error: {e}")
        return ""

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
audio_queue = queue.Queue()
display_clients = set()
extended_clients = set()
operator_clients = set()
dictation_clients = set()
shutdown_event = threading.Event()
silence_threshold = SILENCE_THRESHOLD
translation_paused = True
captioning_paused = True
save_transcripts = True
_session_stamp = time.strftime("%Y%m%d_%H%M%S")
current_speaker = ""
_speaker_change_pending = None  # {"name": str, "time": float} or None
_speaker_lock = threading.Lock()



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

def _check_speaker_change(transcribe_fn, buf, seg_start, loop):
    """Check for pending speaker change. Split buffer 0.5s before the button press.
    Finalizes old speaker's portion, returns remaining buffer for new speaker."""
    global current_speaker, _speaker_change_pending
    with _speaker_lock:
        sc = _speaker_change_pending
        if sc:
            _speaker_change_pending = None
    if not sc:
        return buf, seg_start, False

    old_speaker = current_speaker
    new_speaker = sc["name"]
    log.info(f"Speaker: {old_speaker or '(none)'} -> {new_speaker or '(none)'}")

    # Finalize any buffered audio under the OLD speaker label
    if len(buf) > 0 and seg_start:
        split_time = sc["time"] - 0.5  # 0.5s retroactive
        split_samples = max(0, int((split_time - seg_start) * SAMPLE_RATE))
        if split_samples > int(MIN_SPEECH_DURATION * SAMPLE_RATE) and split_samples < len(buf):
            # Split: old speaker gets audio before the split point
            old_buf = buf[:split_samples]
            current_speaker = old_speaker  # keep old label for this segment
            text = transcribe_fn(old_buf)
            if text:
                _broadcast_final(text, loop)
            current_speaker = new_speaker
            return buf[split_samples:], split_time, True
        elif split_samples >= len(buf):
            # All buffered audio belongs to old speaker
            current_speaker = old_speaker
            text = transcribe_fn(buf)
            if text:
                _broadcast_final(text, loop)
            current_speaker = new_speaker
            return np.empty((0,1), dtype=np.float32), sc["time"], True
        # split_samples <= 0: change was before segment start, just relabel

    current_speaker = new_speaker
    return buf, seg_start or sc["time"], True


def _buffer_audio_loop(transcribe_fn, loop):
    """Shared audio processing loop for buffer-based backends (Whisper, MLX).
    Handles speaker changes with 0.5s retroactive buffer splitting."""
    global current_speaker
    buf = np.empty((0,1), dtype=np.float32)
    is_speech = False; silence_start = None; seg_start = None; last_interim = 0
    while not shutdown_event.is_set():
        try:
            chunk = audio_queue.get(timeout=0.5)
        except queue.Empty:
            continue

        # ── Skip processing when captioning is paused ──
        if captioning_paused:
            buf = np.empty((0,1), dtype=np.float32)
            is_speech = False; silence_start = None; seg_start = None; last_interim = 0
            continue

        # ── Check for pending speaker change ──
        buf, seg_start, changed = _check_speaker_change(transcribe_fn, buf, seg_start, loop)
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
                text = transcribe_fn(buf)
                if text:
                    _bc(loop, {"type":"interim","text":text,"speaker":current_speaker})
                    _translate_all(text, "interim_translation", loop, max_slots=2)
        else:
            if is_speech:
                if silence_start is None:
                    silence_start = now
                elif (now - silence_start) >= SILENCE_DURATION:
                    if len(buf) / SAMPLE_RATE >= MIN_SPEECH_DURATION:
                        text = transcribe_fn(buf)
                        if text:
                            _broadcast_final(text, loop)
                    buf = np.empty((0,1), dtype=np.float32)
                    is_speech = False; silence_start = None; seg_start = None; last_interim = 0
                    _bc(loop, {"type":"status","state":"silence"})
        if is_speech and seg_start and (now - seg_start) >= MAX_SEGMENT_DURATION:
            text = transcribe_fn(buf)
            if text:
                _broadcast_final(text, loop)
            buf = np.empty((0,1), dtype=np.float32)
            is_speech = False; silence_start = None; seg_start = now; last_interim = 0


class WhisperBackend(SpeechBackend):
    def __init__(self, model_name, device, compute_type):
        from faster_whisper import WhisperModel
        self._model_name = model_name
        self._device = device
        self._compute_type = compute_type
        self._model = WhisperModel(model_name, device=device, compute_type=compute_type)

    @property
    def name(self):
        return f"whisper ({self._model_name}, {self._compute_type}, {self._device})"

    def _transcribe(self, buf):
        whisper_lang = DEEPL_TO_WHISPER.get(config.get("input_lang", "EN"), "en")
        try:
            segs, _ = self._model.transcribe(buf.flatten().astype(np.float32),
                language=whisper_lang, beam_size=3, vad_filter=True,
                vad_parameters=dict(min_silence_duration_ms=400, speech_pad_ms=150))
            return " ".join(s.text.strip() for s in segs)
        except Exception as e:
            log.error(f"Whisper error: {e}"); return ""

    def process_audio_loop(self, loop):
        _buffer_audio_loop(self._transcribe, loop)


class VoskBackend(SpeechBackend):
    MODELS = {
        "small": {"url":"https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip",
                  "dir":"vosk-model-small-en-us-0.15","size":"~40 MB"},
        "large": {"url":"https://alphacephei.com/vosk/models/vosk-model-en-us-0.22.zip",
                  "dir":"vosk-model-en-us-0.22","size":"~1.8 GB"},
    }
    def __init__(self, model_size="large"):
        import vosk; vosk.SetLogLevel(-1)
        info = self.MODELS.get(model_size, self.MODELS["large"])
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
        except Exception as e:
            if zp.exists(): zp.unlink()
            print(f"\n  Download failed: {e}"); sys.exit(1)

    @property
    def name(self): return f"vosk ({self._name})"

    def process_audio_loop(self, loop):
        global current_speaker, _speaker_change_pending
        import vosk
        rec = vosk.KaldiRecognizer(self._model, SAMPLE_RATE)
        last_partial = ""; last_pt = 0; in_speech = False
        while not shutdown_event.is_set():
            try:
                chunk = audio_queue.get(timeout=0.5)
            except queue.Empty:
                continue

            # ── Skip processing when captioning is paused ──
            if captioning_paused:
                if in_speech:
                    rec.FinalResult()  # reset recognizer state
                    last_partial = ""; in_speech = False
                continue

            # ── Speaker change: force-finalize current recognition ──
            with _speaker_lock:
                sc = _speaker_change_pending
                if sc:
                    _speaker_change_pending = None
            if sc:
                old_speaker = current_speaker
                new_speaker = sc["name"]
                log.info(f"Speaker: {old_speaker or '(none)'} -> {new_speaker or '(none)'}")
                # Force Vosk to finalize whatever it has buffered
                result = json.loads(rec.FinalResult())
                text = result.get("text", "").strip()
                if text:
                    # Keep old speaker label for this segment
                    _broadcast_final(text, loop)
                current_speaker = new_speaker
                last_partial = ""; in_speech = False

            audio_bytes = (chunk.flatten()*32767).astype(np.int16).tobytes()
            rms = float(np.sqrt(np.mean(chunk**2)))
            if rms >= silence_threshold and not in_speech:
                in_speech = True; _bc(loop, {"type":"status","state":"speech"})
            if rec.AcceptWaveform(audio_bytes):
                text = json.loads(rec.Result()).get("text","").strip()
                if text:
                    _broadcast_final(text, loop)
                last_partial = ""; in_speech = False
                _bc(loop, {"type":"status","state":"silence"})
            else:
                pt = json.loads(rec.PartialResult()).get("partial","").strip()
                if pt and pt != last_partial:
                    last_partial = pt
                    _bc(loop, {"type":"interim","text":pt,"speaker":current_speaker})
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

    def _transcribe(self, buf):
        import mlx_whisper
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
        _buffer_audio_loop(self._transcribe, loop)


# ── Broadcasting ──
def _bc(loop, msg):
    asyncio.run_coroutine_threadsafe(broadcast_all(msg), loop)

async def broadcast_all(msg):
    data = json.dumps(msg)
    for cs in [display_clients, extended_clients, operator_clients, dictation_clients]:
        dead = set()
        for ws in cs:
            try: await ws.send_text(data)
            except: dead.add(ws)
        cs.difference_update(dead)

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

def _broadcast_final(text, loop):
    """Broadcast final source text with speaker, save transcript, trigger translations."""
    speaker = current_speaker
    _bc(loop, {"type":"final","text":text,"speaker":speaker})
    prefix = f"{speaker}: " if speaker else ""
    log.info(f"   IN: {prefix}{text}")
    src = config.get("input_lang", "EN")
    _save_line(src, f"{prefix}{text}")
    _translate_all(text, "final_translation", loop)

def _translate_all(text, msg_type, loop, max_slots=99):
    if translation_paused:
        return
    translations = config.get("translations", [])
    for i, t in enumerate(translations):
        if i >= max_slots: break
        threading.Thread(target=_do_translate,
            args=(text, t["lang"], i, msg_type, loop), daemon=True).start()

def _do_translate(text, lang, slot, msg_type, loop):
    translated = translate_text(text, lang)
    if translated:
        speaker = current_speaker
        _bc(loop, {"type": msg_type, "translated": translated, "lang": lang, "slot": slot, "speaker": speaker})
        if msg_type == "final_translation":
            prefix = f"{speaker}: " if speaker else ""
            log.info(f"   [{slot}] {lang}: {prefix}{translated}")
            _save_line(lang, f"{prefix}{translated}")


# ── Audio Capture ──
def audio_callback(indata, frames, ti, status):
    if status: log.warning(f"Audio: {status}")
    audio_queue.put(indata.copy())

def start_audio_capture(dev_idx=None):
    bs = int(SAMPLE_RATE * CHUNK_DURATION)
    try:
        s = sd.InputStream(samplerate=SAMPLE_RATE, channels=CHANNELS, dtype=DTYPE,
                           blocksize=bs, device=dev_idx, callback=audio_callback)
        s.start(); log.info(f"Audio capture started (device: {dev_idx or 'default'})")
        while not shutdown_event.is_set(): shutdown_event.wait(0.5)
        s.stop(); s.close()
    except Exception as e: log.error(f"Audio capture error: {e}")


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

@display_app.websocket("/ws")
async def d_ws(ws: WebSocket):
    await ws.accept(); display_clients.add(ws)
    await ws.send_text(json.dumps({"type":"status","state":"connected"}))
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
    await ws.send_text(json.dumps({"type":"status","state":"connected"}))
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
    return JSONResponse({
        **_style_config(),
        "deepl_api_key": config.get("deepl_api_key",""),
        "has_api_key": bool(config.get("deepl_api_key","")),
        "backend": stt_backend.name if stt_backend else "loading...",
        "translation_count": config.get("translation_count", 1),
        "translations": config.get("translations", []),
        "font_css": _font_css(config.get("font_family","atkinson")),
        "source_langs": DEEPL_SOURCE_LANGS,
        "target_langs": DEEPL_TARGET_LANGS,
        "color_palette": COLOR_PALETTE,
        "bg_options": BG_OPTIONS,
        "font_options": FONT_OPTIONS,
    })

@operator_app.post("/api/config")
async def o_update(
    session_title: str = Form(None), deepl_api_key: str = Form(None),
    input_lang: str = Form(None), translation_count: int = Form(None),
    translations_json: str = Form(None), speakers: str = Form(None),
    font_size: int = Form(None), max_lines: int = Form(None),
    bg_color: str = Form(None), font_family: str = Form(None),
    caption_color: str = Form(None),
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
    save_config(config)

    update_msg = {
        "type": "config_update",
        **_style_config(),
        "translation_count": config.get("translation_count",1),
        "all_translations": config.get("translations",[]),
        "font_css": _font_css(config.get("font_family","atkinson")),
    }
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

@operator_app.websocket("/ws")
async def o_ws(ws: WebSocket):
    await ws.accept(); operator_clients.add(ws)
    await ws.send_text(json.dumps({"type":"status","state":"connected",
        "model": stt_backend.name if stt_backend else "loading"}))
    try:
        while True:
            msg = json.loads(await ws.receive_text())
            if msg.get("type") == "set_threshold":
                global silence_threshold
                silence_threshold = float(msg.get("value", SILENCE_THRESHOLD))
            elif msg.get("type") == "set_speaker":
                new_name = msg.get("speaker", "")
                global _speaker_change_pending
                with _speaker_lock:
                    _speaker_change_pending = {"name": new_name, "time": time.time()}
                await broadcast_all({"type":"speaker_change","speaker":new_name})
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
    global captioning_paused
    await ws.accept(); dictation_clients.add(ws)
    await ws.send_text(json.dumps({"type":"status","state":"connected",
        "captioning_paused": captioning_paused}))
    try:
        while True:
            msg = json.loads(await ws.receive_text())
            if msg.get("type") == "set_captioning_paused":
                captioning_paused = bool(msg.get("paused", False))
                log.info(f"Captioning {'PAUSED' if captioning_paused else 'LIVE'}")
                await broadcast_all({"type":"captioning_paused","paused":captioning_paused})
    except WebSocketDisconnect: dictation_clients.discard(ws)
    except Exception: dictation_clients.discard(ws)


# ── Startup ──
def setup_events(app, role):
    @app.on_event("startup")
    async def startup():
        if role == "display":
            loop = asyncio.get_event_loop()
            threading.Thread(target=stt_backend.process_audio_loop, args=(loop,), daemon=True).start()
            mic = getattr(app.state, "mic_index", None)
            threading.Thread(target=start_audio_capture, args=(mic,), daemon=True).start()
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
    parser.add_argument("--vosk-model", default="large", choices=["small","large"])
    parser.add_argument("--mic", type=int, default=None)
    parser.add_argument("--list-mics", action="store_true")
    parser.add_argument("--display-port", type=int, default=3000)
    parser.add_argument("--operator-port", type=int, default=3001)
    parser.add_argument("--extended-port", type=int, default=3002)
    parser.add_argument("--dictation-port", type=int, default=3005)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--threshold", type=float, default=SILENCE_THRESHOLD)
    parser.add_argument("--transcripts-dir", type=str, default=None,
        help="Directory for transcript files (default: ~/Documents/LinguaTaxi Transcripts)")
    args = parser.parse_args()
    if args.list_mics: list_mics(); sys.exit(0)
    global silence_threshold; silence_threshold = args.threshold
    global TRANSCRIPTS_DIR
    if args.transcripts_dir:
        TRANSCRIPTS_DIR = Path(args.transcripts_dir)
    TRANSCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
    display_app.state.mic_index = args.mic

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
