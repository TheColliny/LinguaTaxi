"""File transcription — audio loading, segmentation, batch processing, live playback."""
import logging
import threading
import time
import numpy as np
from pathlib import Path
import re

AUDIO_EXTS = {".wav", ".mp3", ".flac", ".m4a", ".ogg", ".webm"}
TEXT_EXTS = {".txt", ".srt", ".vtt", ".md"}
MAX_TEXT_FILE_SIZE = 10 * 1024 * 1024  # 10MB
MAX_LINE_LENGTH = 5000


def _sanitize_text_line(line):
    """Strip null bytes and control characters (keep newlines/tabs), cap length."""
    line = line.replace("\x00", "")
    line = re.sub(r"[\x01-\x08\x0b\x0c\x0e-\x1f\x7f]", "", line)
    return line[:MAX_LINE_LENGTH]


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


# ── Progress state (read by server API) ──
_progress = {
    "status": "idle", "pct": 0, "message": "",
    "current_file": "", "files_done": 0, "files_total": 0,
}
_progress_lock = threading.Lock()


def get_progress():
    with _progress_lock:
        return dict(_progress)


def _set_progress(status, pct=0, message="", current_file="",
                  files_done=0, files_total=0):
    with _progress_lock:
        _progress["status"] = status
        _progress["pct"] = pct
        _progress["message"] = message
        _progress["current_file"] = current_file
        _progress["files_done"] = files_done
        _progress["files_total"] = files_total


def batch_translate_text(file_path, translate_fn, translations,
                         output_dir, source_lang, progress_callback=None,
                         _emit_done=True):
    """Translate a text file to multiple languages.
    Returns {lines, languages, output_dir} or None on error."""
    p = Path(file_path)
    _set_progress("processing", 0, f"Reading {p.name}...")

    try:
        size = p.stat().st_size
    except OSError as e:
        _set_progress("error", 0, f"Cannot access {p.name}: {e}")
        return None
    if size > MAX_TEXT_FILE_SIZE:
        _set_progress("error", 0, f"Skipped {p.name} (exceeds 10MB limit)")
        return None

    try:
        raw = p.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        _set_progress("error", 0, f"Cannot read {p.name}: {e}")
        return None

    lines = raw.splitlines()
    if not lines:
        _set_progress("error", 0, f"Skipped {p.name} (empty file)")
        return None

    lines = [_sanitize_text_line(ln) for ln in lines]
    lines = [ln for ln in lines if ln.strip()]
    if not lines:
        _set_progress("error", 0, f"Skipped {p.name} (no text after sanitization)")
        return None

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    stem = p.stem
    ext = p.suffix
    languages = []
    total_slots = len(translations)

    for slot_idx, t in enumerate(translations):
        tgt_lang = t["lang"]
        mode = t.get("mode", "deepl")
        tgt_base = tgt_lang.split("-")[0]
        src_base = source_lang.split("-")[0]

        pct = int(100 * slot_idx / max(total_slots, 1))
        _set_progress("processing", pct, f"Translating to {tgt_lang}...")
        if progress_callback:
            progress_callback(pct, f"Translating to {tgt_lang}")

        out_name = f"{stem}_{tgt_lang}{ext}"
        with open(out / out_name, "w", encoding="utf-8") as f:
            for line in lines:
                if src_base == tgt_base:
                    translated = line
                else:
                    translated = translate_fn(line, tgt_lang, source_lang, mode)
                f.write(translated + "\n")
        languages.append(tgt_lang)

    if _emit_done:
        _set_progress("done", 100, f"Translated {len(lines)} lines to {len(languages)} language(s)")
    return {
        "lines": len(lines),
        "languages": languages,
        "output_dir": str(out),
    }


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
                     transcripts_dir, source_lang, progress_callback=None,
                     _emit_done=True):
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
    if stt_backend is None:
        _set_progress("error", 0, "No speech backend loaded — check model installation")
        return None

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

    if _emit_done:
        _set_progress("done", 100, f"Transcribed {len(lines)} lines")
    return {
        "lines": len(lines),
        "duration_sec": round(duration, 1),
        "output_dir": str(out_dir),
        "languages": languages,
    }


def batch_folder(folder_path, recursive, stt_backend, translate_fn,
                 translations, output_dir, source_lang,
                 progress_callback=None):
    """Process all supported files in a folder.
    Returns {files_processed, files_skipped, total_lines, languages, output_dir}."""
    root = Path(folder_path)
    if not root.is_dir():
        _set_progress("error", 0, f"Not a directory: {folder_path}")
        return None

    files = []
    pattern = root.rglob("*") if recursive else root.glob("*")
    for fp in sorted(pattern):
        if not fp.is_file():
            continue
        ext = fp.suffix.lower()
        if ext in AUDIO_EXTS or ext in TEXT_EXTS:
            files.append(fp)

    if not files:
        _set_progress("error", 0, "No audio or text files found in folder")
        return None

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    total = len(files)
    processed = 0
    skipped = 0
    total_lines = 0
    languages_set = set()

    for idx, fp in enumerate(files):
        ext = fp.suffix.lower()
        file_pct = int(100 * idx / total)
        _set_progress("processing", file_pct,
                      f"Processing {fp.name} ({idx + 1}/{total})",
                      current_file=fp.name, files_done=idx, files_total=total)

        if ext in TEXT_EXTS:
            if not translations:
                log.warning(f"Skipping text file {fp.name} — no translation configured")
                skipped += 1
                continue
            result = batch_translate_text(
                str(fp), translate_fn, translations,
                str(out), source_lang, progress_callback=None,
                _emit_done=False,
            )
        elif ext in AUDIO_EXTS:
            result = batch_transcribe(
                str(fp), stt_backend, translate_fn, translations,
                transcripts_dir=str(out), source_lang=source_lang,
                progress_callback=None, _emit_done=False,
            )
        else:
            continue

        # Reset progress to folder-level to prevent sub-function "done" from
        # being visible to polling clients between files
        _set_progress("processing", file_pct,
                      f"Processing {fp.name} ({idx + 1}/{total})",
                      current_file=fp.name, files_done=idx, files_total=total)

        if result:
            total_lines += result["lines"]
            languages_set.update(result["languages"])
            processed += 1
        else:
            skipped += 1

    msg = f"Done: {processed} files processed"
    if skipped:
        msg += f", {skipped} skipped"
    _set_progress("done", 100, msg,
                  current_file="", files_done=total, files_total=total)
    return {
        "files_processed": processed,
        "files_skipped": skipped,
        "total_lines": total_lines,
        "languages": sorted(languages_set),
        "output_dir": str(out),
    }


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
