# File Transcription Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add audio file transcription with two modes — batch (offline processing with translations saved to text files) and live playback (feed file as mic input for testing/troubleshooting).

**Architecture:** A new `transcribe_file.py` module handles audio loading, resampling, silence-based segmentation, batch transcription, and live playback. Four new API endpoints on the operator app expose these functions. The launcher gets a "Transcribe File" button that opens a file picker then a mode-selection dialog.

**Tech Stack:** Python, numpy, soundfile, pydub (optional, for mp3/m4a/webm), customtkinter, FastAPI

---

## File Map

| File | Role |
|------|------|
| `transcribe_file.py` (create) | Audio loading, resampling, segmentation, batch pipeline, live playback |
| `server.py` (modify, ~L2508) | 4 new API endpoints + progress state |
| `launcher.pyw` (modify, ~L447) | "Transcribe File" button + mode selection dialog |

---

### Task 1: Create `transcribe_file.py` — Audio Loading and Segmentation

**Files:**
- Create: `transcribe_file.py`

- [ ] **Step 1: Create `transcribe_file.py` with `load_audio()` and `_resample()`**

Create the file at the project root (same directory as `server.py`):

```python
"""File transcription — audio loading, segmentation, batch processing, live playback."""
import logging
import threading
import time
import numpy as np
from pathlib import Path

log = logging.getLogger("livecaption")

SAMPLE_RATE = 16000


def _resample(samples, orig_sr, target_sr=SAMPLE_RATE):
    """Resample audio using linear interpolation."""
    if orig_sr == target_sr:
        return samples
    ratio = target_sr / orig_sr
    new_length = int(len(samples) * ratio)
    indices = np.arange(new_length) / ratio
    indices = np.clip(indices, 0, len(samples) - 1)
    left = np.floor(indices).astype(int)
    right = np.minimum(left + 1, len(samples) - 1)
    frac = indices - left
    return (samples[left] * (1 - frac) + samples[right] * frac).astype(np.float32)


def load_audio(file_path):
    """Load audio file, resample to 16kHz mono float32.
    Returns (samples, duration_sec). Raises ValueError for unsupported formats."""
    p = Path(file_path)
    ext = p.suffix.lower()
    native_exts = {".wav", ".flac", ".ogg"}
    extended_exts = {".mp3", ".m4a", ".webm"}

    if ext in native_exts:
        import soundfile as sf
        data, sr = sf.read(str(p), dtype="float32", always_2d=True)
        samples = data[:, 0]  # mono: take first channel
    elif ext in extended_exts:
        try:
            from pydub import AudioSegment
        except ImportError:
            raise ValueError(
                "MP3/M4A/WebM support requires pydub and ffmpeg. "
                "Install them or convert to WAV."
            )
        seg = AudioSegment.from_file(str(p))
        seg = seg.set_channels(1)
        sr = seg.frame_rate
        raw = np.array(seg.get_array_of_samples(), dtype=np.float32)
        samples = raw / 32768.0  # normalize 16-bit to float32
    else:
        raise ValueError(f"Unsupported audio format: {ext}")

    if len(samples) == 0:
        raise ValueError("Could not read audio file")

    samples = _resample(samples, sr)
    duration = len(samples) / SAMPLE_RATE
    return samples, duration
```

- [ ] **Step 2: Add `segment_audio()` function**

Append below `load_audio` in the same file. This uses the same silence detection algorithm as `server.py`'s `_buffer_audio_loop`:

```python
def segment_audio(samples, silence_threshold=0.008,
                  silence_duration=0.7, max_segment_duration=8.0):
    """Split audio into segments using silence detection.
    Same algorithm as server.py's _buffer_audio_loop."""
    chunk_size = int(SAMPLE_RATE * 0.5)  # 0.5s chunks, same as CHUNK_DURATION
    segments = []
    buf = np.empty(0, dtype=np.float32)
    is_speech = False
    silence_start = None
    seg_start_sample = 0

    for i in range(0, len(samples), chunk_size):
        chunk = samples[i:i + chunk_size]
        buf = np.concatenate([buf, chunk])
        rms = float(np.sqrt(np.mean(chunk ** 2)))
        pos_sec = i / SAMPLE_RATE

        if rms >= silence_threshold:
            if not is_speech:
                is_speech = True
                silence_start = None
            else:
                silence_start = None
            dur = len(buf) / SAMPLE_RATE
            if dur >= max_segment_duration:
                if len(buf) > 0:
                    segments.append(buf.copy())
                buf = np.empty(0, dtype=np.float32)
                is_speech = True
                silence_start = None
        else:
            if is_speech:
                if silence_start is None:
                    silence_start = pos_sec
                elif (pos_sec - silence_start) >= silence_duration:
                    if len(buf) / SAMPLE_RATE >= 0.3:  # MIN_SPEECH_DURATION
                        segments.append(buf.copy())
                    buf = np.empty(0, dtype=np.float32)
                    is_speech = False
                    silence_start = None

    # Flush remaining buffer
    if len(buf) > 0 and len(buf) / SAMPLE_RATE >= 0.3:
        segments.append(buf.copy())

    return segments
```

- [ ] **Step 3: Verify module loads**

Run: `python -c "import transcribe_file; print('OK')"`

Expected: `OK` with no import errors.

- [ ] **Step 4: Commit**

```bash
git add transcribe_file.py
git commit -m "[feat] add transcribe_file.py with load_audio and segment_audio"
```

---

### Task 2: Add `batch_transcribe()` to `transcribe_file.py`

**Files:**
- Modify: `transcribe_file.py`

- [ ] **Step 1: Add progress state and `batch_transcribe()` function**

Add these at the end of `transcribe_file.py`:

```python
# ── Progress state (read by server API) ──
_progress = {"status": "idle", "pct": 0, "message": ""}
_progress_lock = threading.Lock()


def get_progress():
    with _progress_lock:
        return dict(_progress)


def _set_progress(status, pct=0, message=""):
    with _progress_lock:
        _progress["status"] = status
        _progress["pct"] = pct
        _progress["message"] = message


def _transcribe_segment_vosk(segment, vosk_model):
    """Transcribe a single segment using a temporary Vosk KaldiRecognizer."""
    import vosk
    rec = vosk.KaldiRecognizer(vosk_model, SAMPLE_RATE)
    pcm = (segment * 32768).astype(np.int16).tobytes()
    chunk_size = 8000  # 0.5s of 16-bit mono at 16kHz
    for i in range(0, len(pcm), chunk_size):
        rec.AcceptWaveform(pcm[i:i + chunk_size])
    import json
    result = json.loads(rec.FinalResult())
    return result.get("text", "").strip()


def batch_transcribe(file_path, stt_backend, translate_fn, translations,
                     transcripts_dir, source_lang, progress_callback=None):
    """Full batch pipeline: load → segment → transcribe → translate → save.
    Returns {lines, duration_sec, output_dir, languages}.

    Args:
        stt_backend: The active SpeechBackend instance (WhisperBackend or VoskBackend).
        translate_fn: The translate_text(text, target_lang, source_lang, mode) function.
        translations: List of translation slot dicts from config, e.g. [{"lang":"ES","mode":"deepl"},...].
        transcripts_dir: Path to transcripts output directory.
        source_lang: Source language code (e.g. "EN").
        progress_callback: Optional callable(pct: int, message: str).
    """
    _set_progress("processing", 0, "Loading audio file...")
    try:
        samples, duration = load_audio(file_path)
    except ValueError as e:
        _set_progress("error", 0, str(e))
        return None

    _set_progress("processing", 5, "Segmenting audio...")
    segments = segment_audio(samples)
    if not segments:
        _set_progress("error", 0, "No speech detected in audio file")
        return None

    total = len(segments)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    out_dir = Path(transcripts_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Determine if this is a Vosk backend
    is_vosk = hasattr(stt_backend, '_model') and not hasattr(stt_backend, '_transcribe')
    # Actually, both have _model. Check by class name or method.
    vosk_model = None
    if hasattr(stt_backend, '_vosk_source_loop'):
        is_vosk = True
        vosk_model = stt_backend._model

    # Collect transcribed lines: [(timestamp_str, text)]
    lines = []
    elapsed_samples = 0

    for idx, seg in enumerate(segments):
        pct = 10 + int(70 * idx / total)  # 10-80% for transcription
        ts_sec = elapsed_samples / SAMPLE_RATE
        ts_str = f"{int(ts_sec // 3600):02d}:{int((ts_sec % 3600) // 60):02d}:{int(ts_sec % 60):02d}"
        _set_progress("processing", pct, f"Transcribing segment {idx + 1}/{total}...")
        if progress_callback:
            progress_callback(pct, f"Transcribing {idx + 1}/{total}")

        if is_vosk and vosk_model:
            text = _transcribe_segment_vosk(seg, vosk_model)
        else:
            text = stt_backend._transcribe(
                seg.reshape(-1, 1),  # reshape to (N,1) as expected by _buffer_audio_loop
                lang=None
            )

        if text and text.strip():
            lines.append((ts_str, text.strip()))
        elapsed_samples += len(seg)

    if not lines:
        _set_progress("done", 100, "No speech found in file")
        return {"lines": 0, "duration_sec": duration, "output_dir": str(out_dir), "languages": []}

    # Save source language transcript
    src_fn = f"file_{stamp}_{source_lang}.txt"
    with open(out_dir / src_fn, "w", encoding="utf-8") as f:
        for ts, text in lines:
            f.write(f"[{ts}] {text}\n")

    languages = [source_lang]

    # Translate and save for each active translation slot
    for slot_idx, t in enumerate(translations):
        tgt_lang = t["lang"]
        mode = t.get("mode", "deepl")
        tgt_base = tgt_lang.split("-")[0]
        src_base = source_lang.split("-")[0]

        pct = 80 + int(18 * slot_idx / max(len(translations), 1))
        _set_progress("processing", pct, f"Translating to {tgt_lang}...")
        if progress_callback:
            progress_callback(pct, f"Translating to {tgt_lang}")

        tgt_fn = f"file_{stamp}_{tgt_lang}.txt"
        with open(out_dir / tgt_fn, "w", encoding="utf-8") as f:
            for ts, text in lines:
                if src_base == tgt_base:
                    translated = text
                else:
                    translated = translate_fn(text, tgt_lang, source_lang, mode)
                f.write(f"[{ts}] {translated}\n")
        languages.append(tgt_lang)

    _set_progress("done", 100, f"Transcribed {len(lines)} lines")
    return {
        "lines": len(lines),
        "duration_sec": round(duration, 1),
        "output_dir": str(out_dir),
        "languages": languages,
    }
```

- [ ] **Step 2: Verify module loads with new function**

Run: `python -c "from transcribe_file import batch_transcribe, get_progress; print('OK')"`

Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add transcribe_file.py
git commit -m "[feat] add batch_transcribe with progress tracking and Vosk support"
```

---

### Task 3: Add Live Playback to `transcribe_file.py`

**Files:**
- Modify: `transcribe_file.py`

- [ ] **Step 1: Add `start_live_playback()` and `stop_live_playback()`**

Append at the end of `transcribe_file.py`:

```python
# ── Live playback state ──
_playback_stop = None   # threading.Event or None
_playback_thread = None


def start_live_playback(file_path, source, on_complete=None):
    """Feed audio file into source.queue at real-time pace.
    Returns a stop_event that can be set to cancel playback.

    Args:
        source: An AudioSource instance whose .queue receives numpy chunks.
        on_complete: Optional callback when playback finishes (called from playback thread).
    """
    global _playback_stop, _playback_thread

    if _playback_stop is not None and not _playback_stop.is_set():
        raise RuntimeError("File transcription already in progress")

    samples, duration = load_audio(file_path)
    stop_event = threading.Event()
    _playback_stop = stop_event

    chunk_size = int(SAMPLE_RATE * 0.5)  # 8000 samples = 0.5s, matches CHUNK_DURATION
    total_chunks = max(1, len(samples) // chunk_size)

    def feed():
        _set_progress("playing", 0, f"Playing file ({duration:.0f}s)...")
        try:
            for i in range(0, len(samples), chunk_size):
                if stop_event.is_set():
                    break
                chunk = samples[i:i + chunk_size]
                # Reshape to (N,1) to match mic input format
                source.queue.put(chunk.reshape(-1, 1))
                pct = min(100, int(100 * i / len(samples)))
                elapsed = i / SAMPLE_RATE
                _set_progress("playing", pct,
                              f"{int(elapsed)}s / {int(duration)}s")
                # Wait real-time before sending next chunk
                if not stop_event.wait(0.5):
                    pass  # timeout expired = continue
                else:
                    break  # stop_event was set
        finally:
            _set_progress("idle", 0, "")
            if on_complete:
                on_complete()

    t = threading.Thread(target=feed, daemon=True)
    t.start()
    _playback_thread = t
    _set_progress("playing", 0, f"Playing file ({duration:.0f}s)...")
    return stop_event


def stop_live_playback():
    """Stop any active live playback and signal completion."""
    global _playback_stop
    if _playback_stop is not None:
        _playback_stop.set()
        _playback_stop = None
    _set_progress("idle", 0, "")
```

- [ ] **Step 2: Verify module loads with all functions**

Run: `python -c "from transcribe_file import start_live_playback, stop_live_playback; print('OK')"`

Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add transcribe_file.py
git commit -m "[feat] add live playback functions to transcribe_file.py"
```

---

### Task 4: Add API Endpoints to `server.py`

**Files:**
- Modify: `server.py:~2508` (after the last `dictation_app` endpoint, before the `if __name__` block)

- [ ] **Step 1: Add `import transcribe_file` near the top of `server.py`**

Find the existing import block near the top of `server.py` (around line 1-30) and add:

```python
import transcribe_file
```

Add it alongside the other local imports (near `import offline_translate`).

- [ ] **Step 2: Add all four file transcription endpoints**

Add these endpoints on the `operator_app` after the last existing `operator_app` endpoint (find the last `@operator_app` route — search for `o_offline_reload` which is around line 1998-2004). Insert after that endpoint:

```python
# ── File transcription endpoints ──

_file_transcribe_lock = threading.Lock()

@operator_app.post("/api/transcribe-file/batch")
async def o_transcribe_batch(file_path: str = Form(...)):
    """Start batch file transcription in a background thread."""
    if not Path(file_path).exists():
        return JSONResponse({"error": "File not found"}, status_code=400)

    progress = transcribe_file.get_progress()
    if progress["status"] in ("processing", "playing"):
        return JSONResponse({"error": "File transcription already in progress"}, status_code=409)

    translations = config.get("translations", [])
    src_lang = config.get("input_lang", "EN")

    def run():
        with _file_transcribe_lock:
            transcribe_file.batch_transcribe(
                file_path=file_path,
                stt_backend=stt_backend,
                translate_fn=translate_text,
                translations=translations,
                transcripts_dir=str(TRANSCRIPTS_DIR),
                source_lang=src_lang,
            )

    threading.Thread(target=run, daemon=True).start()
    return JSONResponse({"status": "started"})


@operator_app.post("/api/transcribe-file/live")
async def o_transcribe_live(file_path: str = Form(...)):
    """Start live file playback — pauses mic, feeds file as audio input."""
    if not Path(file_path).exists():
        return JSONResponse({"error": "File not found"}, status_code=400)

    progress = transcribe_file.get_progress()
    if progress["status"] in ("processing", "playing"):
        return JSONResponse({"error": "File transcription already in progress"}, status_code=409)

    # Pause mic streams
    with _sources_lock:
        for src in _sources:
            if src.stream is not None:
                try:
                    src.stream.stop()
                except Exception:
                    pass

    # Use first source for playback
    with _sources_lock:
        if not _sources:
            return JSONResponse({"error": "No audio source available"}, status_code=400)
        source = _sources[0]

    def on_complete():
        # Resume mic streams
        with _sources_lock:
            for src in _sources:
                if src.stream is not None:
                    try:
                        src.stream.start()
                    except Exception:
                        pass
        log.info("File playback complete, mic resumed")

    try:
        samples, duration = transcribe_file.load_audio(file_path)
        transcribe_file.start_live_playback(file_path, source, on_complete=on_complete)
        return JSONResponse({"status": "playing", "duration_sec": round(duration, 1)})
    except ValueError as e:
        on_complete()  # resume mic on error
        return JSONResponse({"error": str(e)}, status_code=400)


@operator_app.post("/api/transcribe-file/stop")
async def o_transcribe_stop():
    """Stop live playback if active, resume mic."""
    transcribe_file.stop_live_playback()
    with _sources_lock:
        for src in _sources:
            if src.stream is not None:
                try:
                    src.stream.start()
                except Exception:
                    pass
    return JSONResponse({"status": "stopped"})


@operator_app.get("/api/transcribe-file/progress")
async def o_transcribe_progress():
    """Get current file transcription progress."""
    return JSONResponse(transcribe_file.get_progress())
```

- [ ] **Step 3: Verify server starts with new endpoints**

Run the server and confirm no import errors. Then test the progress endpoint:

```bash
curl http://localhost:3001/api/transcribe-file/progress
```

Expected: `{"status":"idle","pct":0,"message":""}`

- [ ] **Step 4: Commit**

```bash
git add server.py
git commit -m "[feat] add file transcription API endpoints to server.py"
```

---

### Task 5: Add "Transcribe File" Button and Dialog to `launcher.pyw`

**Files:**
- Modify: `launcher.pyw:~447` (after the Stop button, before Main Controls section)

- [ ] **Step 1: Add "Transcribe File" button**

In `launcher.pyw`, find the Stop button at line ~447:

```python
        self.stop_btn.pack(side="right", expand=True, fill="x", padx=(4, 0))
```

Add a new row right after this line (before the `# ── Main Controls ──` comment at line ~449):

```python
        # ── Transcribe File button ──
        tf_row = ctk.CTkFrame(srv_inner, fg_color="transparent")
        tf_row.pack(fill="x", pady=(6, 0))

        self.transcribe_btn = ctk.CTkButton(
            tf_row, text="Transcribe File",
            fg_color="#7E57C2", hover_color="#9575CD",
            text_color="#fff", font=("Segoe UI", 11, "bold"),
            height=34, command=self._transcribe_file,
            state="disabled"
        )
        self.transcribe_btn.pack(fill="x")
```

- [ ] **Step 2: Enable/disable the button based on server state**

Find the place where `self.start_btn` and `self.stop_btn` states are toggled. In `_start_server` (around line ~2129 where `self._server_running = True`), add:

```python
        self.transcribe_btn.configure(state="normal")
```

In `_stop_server` (around line ~2191 where `self._server_running = False`), add:

```python
        self.transcribe_btn.configure(state="disabled")
```

- [ ] **Step 3: Add the `_transcribe_file()` method and mode selection dialog**

Add this method to the launcher class (after `_stop_server` or in a logical location near the server control methods):

```python
    def _transcribe_file(self):
        """Open file picker, then show mode selection dialog."""
        if not self._server_running:
            return

        file_path = filedialog.askopenfilename(
            title="Select Audio File",
            filetypes=[
                ("Audio Files", "*.wav *.mp3 *.flac *.m4a *.ogg *.webm"),
                ("WAV", "*.wav"), ("MP3", "*.mp3"), ("FLAC", "*.flac"),
                ("M4A", "*.m4a"), ("OGG", "*.ogg"), ("WebM", "*.webm"),
                ("All Files", "*.*"),
            ]
        )
        if not file_path:
            return

        self._show_transcribe_dialog(file_path)

    def _show_transcribe_dialog(self, file_path):
        """Show mode selection dialog for file transcription."""
        dlg = ctk.CTkToplevel(self)
        dlg.title("Transcribe File")
        dlg.geometry("440x320")
        dlg.resizable(False, False)
        dlg.configure(fg_color=self.BG)
        dlg.transient(self)
        dlg.grab_set()

        dlg.update_idletasks()
        px = self.winfo_x() + (self.winfo_width() - 440) // 2
        py = self.winfo_y() + (self.winfo_height() - 320) // 2
        dlg.geometry(f"+{px}+{py}")

        f = ctk.CTkFrame(dlg, fg_color="transparent")
        f.pack(fill="both", expand=True, padx=16, pady=12)

        # Filename display
        fname = Path(file_path).name
        if len(fname) > 45:
            fname = fname[:42] + "..."
        ctk.CTkLabel(f, text=fname,
                     font=("Segoe UI", 11, "bold"),
                     text_color=self.ACCENT).pack(anchor="w", pady=(0, 10))

        # Mode selection
        mode_var = tk.StringVar(value="batch")

        ctk.CTkRadioButton(
            f, text="Batch Transcribe — Process offline, save text files",
            variable=mode_var, value="batch",
            font=("Segoe UI", 11), text_color=self.FG
        ).pack(anchor="w", pady=(0, 4))

        ctk.CTkRadioButton(
            f, text="Play as Live Input — Feed into live captioning pipeline",
            variable=mode_var, value="live",
            font=("Segoe UI", 11), text_color=self.FG
        ).pack(anchor="w", pady=(0, 12))

        # Status area
        status_var = tk.StringVar(value="")
        status_lbl = ctk.CTkLabel(f, textvariable=status_var,
                                  font=("Segoe UI", 10), text_color=self.FG2,
                                  wraplength=400)
        status_lbl.pack(anchor="w", pady=(0, 4))

        progress = ctk.CTkProgressBar(f, width=400, mode="determinate")
        progress.pack(pady=(0, 8))
        progress.set(0)

        # Button frame
        btn_frame = ctk.CTkFrame(f, fg_color="transparent")
        btn_frame.pack(fill="x", pady=(4, 0))

        port = self.settings.get("operator_port", 3001)
        base_url = f"http://localhost:{port}"
        polling = [False]  # mutable flag for polling loop

        def start_batch():
            start_btn.configure(state="disabled")
            status_var.set("Starting batch transcription...")
            progress.configure(mode="indeterminate")
            progress.start(15)
            try:
                data = urllib.parse.urlencode({"file_path": file_path}).encode()
                req = urllib.request.Request(f"{base_url}/api/transcribe-file/batch", data=data)
                resp = urllib.request.urlopen(req, timeout=10)
                result = json.loads(resp.read())
                if "error" in result:
                    status_var.set(f"Error: {result['error']}")
                    start_btn.configure(state="normal")
                    progress.stop()
                    progress.configure(mode="determinate")
                    progress.set(0)
                    return
            except Exception as e:
                status_var.set(f"Error: {e}")
                start_btn.configure(state="normal")
                progress.stop()
                progress.configure(mode="determinate")
                progress.set(0)
                return

            progress.stop()
            progress.configure(mode="determinate")
            polling[0] = True
            cancel_btn.configure(text="Close")
            poll_progress()

        def start_live():
            start_btn.configure(state="disabled")
            status_var.set("Starting live playback...")
            try:
                data = urllib.parse.urlencode({"file_path": file_path}).encode()
                req = urllib.request.Request(f"{base_url}/api/transcribe-file/live", data=data)
                resp = urllib.request.urlopen(req, timeout=10)
                result = json.loads(resp.read())
                if "error" in result:
                    status_var.set(f"Error: {result['error']}")
                    start_btn.configure(state="normal")
                    return
            except Exception as e:
                status_var.set(f"Error: {e}")
                start_btn.configure(state="normal")
                return

            start_btn.pack_forget()
            stop_btn = ctk.CTkButton(btn_frame, text="Stop Playback",
                                     fg_color=self.RED, hover_color="#EF5350",
                                     text_color="#fff", font=("Segoe UI", 11, "bold"),
                                     height=34, command=lambda: stop_playback(stop_btn))
            stop_btn.pack(side="left", expand=True, fill="x", padx=(0, 4))
            polling[0] = True
            poll_progress()

        def stop_playback(btn):
            btn.configure(state="disabled")
            try:
                req = urllib.request.Request(f"{base_url}/api/transcribe-file/stop",
                                            data=b"", method="POST")
                urllib.request.urlopen(req, timeout=5)
            except Exception:
                pass
            polling[0] = False
            status_var.set("Playback stopped, mic resumed")
            progress.set(0)
            dlg.after(1500, dlg.destroy)

        def poll_progress():
            if not polling[0]:
                return
            try:
                resp = urllib.request.urlopen(f"{base_url}/api/transcribe-file/progress", timeout=3)
                p = json.loads(resp.read())
                status_var.set(p.get("message", ""))
                progress.set(p.get("pct", 0) / 100.0)

                if p["status"] == "done":
                    polling[0] = False
                    progress.set(1.0)
                    status_var.set(p.get("message", "Complete"))
                    cancel_btn.configure(text="Close")
                    # Add Open Folder button for batch
                    if mode_var.get() == "batch":
                        import subprocess
                        transcripts_dir = Path.home() / "Documents" / "LinguaTaxi Transcripts"
                        open_btn = ctk.CTkButton(
                            btn_frame, text="Open Folder",
                            fg_color=self.GREEN, hover_color="#9CCC65",
                            text_color="#000", font=("Segoe UI", 11, "bold"),
                            height=34,
                            command=lambda: subprocess.Popen(
                                ["explorer", str(transcripts_dir)]
                            )
                        )
                        open_btn.pack(side="left", expand=True, fill="x", padx=(0, 4))
                    return
                elif p["status"] == "error":
                    polling[0] = False
                    status_var.set(f"Error: {p.get('message', 'Unknown error')}")
                    start_btn.configure(state="normal")
                    cancel_btn.configure(text="Close")
                    return
                elif p["status"] == "idle":
                    polling[0] = False
                    progress.set(0)
                    dlg.after(500, dlg.destroy)
                    return
            except Exception:
                pass

            dlg.after(500, poll_progress)

        def on_start():
            if mode_var.get() == "batch":
                threading.Thread(target=start_batch, daemon=True).start()
            else:
                threading.Thread(target=start_live, daemon=True).start()

        def on_cancel():
            polling[0] = False
            dlg.destroy()

        start_btn = ctk.CTkButton(btn_frame, text="Start",
                                  fg_color=self.GREEN, hover_color="#9CCC65",
                                  text_color="#000", font=("Segoe UI", 11, "bold"),
                                  height=34, command=on_start)
        start_btn.pack(side="left", expand=True, fill="x", padx=(0, 4))

        cancel_btn = ctk.CTkButton(btn_frame, text="Cancel",
                                   fg_color=self.BG3, hover_color="#555",
                                   text_color="#fff", font=("Segoe UI", 11),
                                   height=34, command=on_cancel)
        cancel_btn.pack(side="right", expand=True, fill="x", padx=(4, 0))
```

- [ ] **Step 4: Add missing imports at the top of `launcher.pyw`**

Find the import section at the top of the file. Ensure these are present (most likely `json` and `urllib.parse` may need adding — check first):

```python
import json
import urllib.parse
```

`urllib.request` and `threading` should already be imported. `from pathlib import Path` should also already be present. Verify and add only what's missing.

- [ ] **Step 5: Test end-to-end**

1. Start the server from the launcher
2. Verify "Transcribe File" button appears and is enabled
3. Click it, select a .wav file
4. Test batch mode — verify progress updates and transcript files are created
5. Test live mode — verify audio plays through the captioning pipeline
6. Test stopping live playback — verify mic resumes
7. Stop the server — verify the button becomes disabled

- [ ] **Step 6: Commit**

```bash
git add launcher.pyw
git commit -m "[feat] add Transcribe File button and dialog to launcher"
```

---

## Self-Review Checklist

**Spec coverage:**
- [x] `load_audio()` with native (.wav/.flac/.ogg) and extended (.mp3/.m4a/.webm) format support — Task 1
- [x] `segment_audio()` with same silence detection parameters as server.py — Task 1
- [x] `batch_transcribe()` with transcription, translation, and file saving — Task 2
- [x] Vosk handling with temporary KaldiRecognizer — Task 2
- [x] `start_live_playback()` feeding chunks at real-time pace — Task 3
- [x] `stop_live_playback()` — Task 3
- [x] `POST /api/transcribe-file/batch` — Task 4
- [x] `POST /api/transcribe-file/live` with mic pause/resume — Task 4
- [x] `POST /api/transcribe-file/stop` — Task 4
- [x] `GET /api/transcribe-file/progress` — Task 4
- [x] "Transcribe File" button disabled when server not running — Task 5
- [x] File picker with correct format filters — Task 5
- [x] Mode selection dialog with radio buttons — Task 5
- [x] Batch progress bar with polling — Task 5
- [x] Live playback stop button — Task 5
- [x] Open Folder button on batch completion — Task 5
- [x] Error handling for missing pydub/ffmpeg — Task 1 (ValueError)
- [x] "Already in progress" rejection — Task 4 (409 status)
- [x] All edge cases from spec covered

**Placeholder scan:** No TBD, TODO, or vague steps found.

**Type consistency:** Function signatures match between tasks (load_audio returns tuple, batch_transcribe returns dict, start_live_playback returns stop_event).
