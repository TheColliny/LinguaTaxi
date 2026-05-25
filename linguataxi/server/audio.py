"""Audio capture, source management, and silence detection.

Handles multi-source audio input via sounddevice, speaker change
detection with retroactive buffer splitting, voice ID enrollment
and identification, and the shared buffer-based processing loop
used by Whisper and MLX backends.
"""

from __future__ import annotations

import asyncio
import logging
import queue
import sys
import threading
import time
from typing import Any, Callable, Optional

import numpy as np
import sounddevice as sd

from linguataxi.constants import (
    CHUNK_DURATION,
    CHANNELS,
    DTYPE,
    INTERIM_INTERVAL,
    MAX_SEGMENT_DURATION,
    MIN_SPEECH_DURATION,
    SAMPLE_RATE,
    SILENCE_DURATION,
)

log: logging.Logger = logging.getLogger("livecaption")


# ══════════════════════════════════════════════
# Audio Source
# ══════════════════════════════════════════════


class AudioSource:
    """Represents one audio input source with its own capture stream and speaker state.

    Attributes:
        id: Unique integer identifier (auto-incremented).
        device_index: Sounddevice device index, or None for system default.
        name: Human-readable display name.
        speaker: Current speaker label (empty string when unset).
        color: CSS colour for this source (empty = use default text colour).
        speaker_change_pending: Pending speaker change dict, or None.
        speaker_lock: Lock protecting speaker_change_pending.
        queue: Audio chunk queue fed by the sounddevice callback.
        stream: Active sd.InputStream, or None.
        capture_thread: Thread running start_source_capture.
        buffer_thread: Thread running the buffer processing loop.
        active: Whether this source is alive and should keep capturing.
        restart_event: Set to signal the capture thread to reopen the stream.
        current_lang: Detected language code (set by the buffer loop).
        voice_id_enroll_pending: Speaker name awaiting voice-print enrollment, or None.
    """

    _next_id: int = 0

    def __init__(
        self,
        device_index: Optional[int] = None,
        name: Optional[str] = None,
    ) -> None:
        self.id: int = AudioSource._next_id
        AudioSource._next_id += 1
        self.device_index: Optional[int] = device_index
        self.name: str = name or f"Source {self.id + 1}"
        self.speaker: str = ""
        self.color: str = ""
        self.speaker_change_pending: Optional[dict[str, Any]] = None
        self.speaker_lock: threading.Lock = threading.Lock()
        self.queue: queue.Queue[np.ndarray] = queue.Queue()
        self.stream: Optional[Any] = None  # sd.InputStream
        self.capture_thread: Optional[threading.Thread] = None
        self.buffer_thread: Optional[threading.Thread] = None
        self.active: bool = True
        self.restart_event: threading.Event = threading.Event()
        self.current_lang: Optional[str] = None
        self.voice_id_enroll_pending: Optional[str] = None


# ══════════════════════════════════════════════
# Source Registry (module-level globals)
# ══════════════════════════════════════════════

_sources: list[AudioSource] = []
_sources_lock: threading.Lock = threading.Lock()
_transcription_queue: queue.Queue = queue.Queue(maxsize=16)


def get_source(source_id: int) -> Optional[AudioSource]:
    """Look up an AudioSource by its integer ID.

    Args:
        source_id: The unique ID of the source.

    Returns:
        The matching AudioSource, or None if not found.
    """
    with _sources_lock:
        for s in _sources:
            if s.id == source_id:
                return s
    return None


def add_source(
    device_index: Optional[int] = None,
    name: Optional[str] = None,
) -> Optional[AudioSource]:
    """Create and register a new AudioSource.

    Args:
        device_index: Sounddevice device index, or None for default.
        name: Human-readable name.

    Returns:
        The new AudioSource, or None if the 8-source limit is reached.
    """
    with _sources_lock:
        if len(_sources) >= 8:
            return None
        src = AudioSource(device_index, name)
        _sources.append(src)
    return src


def remove_source(source_id: int) -> bool:
    """Stop and remove an AudioSource by ID.

    Args:
        source_id: The unique ID of the source to remove.

    Returns:
        True if the source was found and removed, False otherwise.
    """
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
            log.debug("Error closing stream during source removal", exc_info=True)
    with _sources_lock:
        _sources[:] = [s for s in _sources if s.id != source_id]
    return True


# ══════════════════════════════════════════════
# Speaker Change Detection
# ══════════════════════════════════════════════


def _check_speaker_change(
    source: AudioSource,
    transcribe_fn: Callable[..., str],
    buf: np.ndarray,
    seg_start: Optional[float],
    loop: asyncio.AbstractEventLoop,
) -> tuple[np.ndarray, Optional[float], bool]:
    """Check for a pending speaker change and split the audio buffer.

    Finalises the old speaker's portion (0.5 s before the button press),
    then returns the remaining buffer for the new speaker.

    Args:
        source: The AudioSource to check.
        transcribe_fn: Backend transcription function.
        buf: Current audio buffer.
        seg_start: Segment start timestamp, or None.
        loop: Asyncio event loop for broadcasting.

    Returns:
        Tuple of (new_buf, new_seg_start, changed).
    """
    # Late imports for cross-module references
    from linguataxi.server.transcripts import _broadcast_final

    with source.speaker_lock:
        sc = source.speaker_change_pending
        if sc:
            source.speaker_change_pending = None
    if not sc:
        return buf, seg_start, False

    old_speaker = source.speaker
    new_speaker: str = sc["name"]
    new_color: str = sc.get("color", source.color)
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
            return np.empty((0, 1), dtype=np.float32), sc["time"], True
        # split_samples <= 0: change was before segment start, just relabel

    source.speaker = new_speaker
    source.color = new_color
    return buf, seg_start or sc["time"], True


# ══════════════════════════════════════════════
# Transcription Worker
# ══════════════════════════════════════════════


def _transcription_worker(
    transcribe_fn: Callable[..., str],
    loop: asyncio.AbstractEventLoop,
) -> None:
    """Single worker that processes transcription requests from all sources.

    Pulls (source, buffer, detected_lang) tuples from the shared
    ``_transcription_queue`` and runs them through the backend's transcribe
    function under the model lock.

    Args:
        transcribe_fn: Backend transcription function.
        loop: Asyncio event loop for broadcasting results.
    """
    # Late imports for cross-module references
    import server as _srv
    from linguataxi.server.transcripts import _broadcast_final
    from linguataxi.server.backends import model_lock as _model_lock
    from linguataxi.server.backends.whisper import WhisperBackend

    while not _srv.shutdown_event.is_set():
        try:
            source, buf, detected_lang = _transcription_queue.get(timeout=0.5)
            if not source.active:
                continue
            with _model_lock:
                if (_srv.config.get("bidirectional_tuned_swap") and
                        detected_lang and
                        isinstance(_srv.stt_backend, WhisperBackend)):
                    current_model_lang = getattr(_srv.stt_backend, '_tuned_lang', None)
                    if current_model_lang != detected_lang:
                        from linguataxi.models.tuned import TUNED_MODELS, get_model_path
                        from faster_whisper import WhisperModel
                        if detected_lang in TUNED_MODELS:
                            model_path = get_model_path(_srv.MODELS_DIR, detected_lang)
                            if model_path and model_path.exists():
                                log.info(f"Swapping to tuned model for {detected_lang}")
                                import os as _os
                                _prev_hf = _os.environ.get("HF_HUB_OFFLINE")
                                _os.environ["HF_HUB_OFFLINE"] = "1"
                                try:
                                    _srv.stt_backend._model = WhisperModel(
                                        str(model_path),
                                        device=_srv.stt_backend._device,
                                        compute_type=_srv.stt_backend._compute_type
                                    )
                                    _srv.stt_backend._tuned_lang = detected_lang
                                finally:
                                    if _prev_hf is None:
                                        _os.environ.pop("HF_HUB_OFFLINE", None)
                                    else:
                                        _os.environ["HF_HUB_OFFLINE"] = _prev_hf
                text = transcribe_fn(buf, lang=detected_lang)
            if text and text.strip():
                _broadcast_final(text.strip(), loop, source, detected_lang=detected_lang)
        except queue.Empty:
            continue
        except Exception as e:
            log.error(f"Transcription error: {e}")


# ══════════════════════════════════════════════
# Language Detection Helpers
# ══════════════════════════════════════════════


def _get_speaker_lang(source: AudioSource) -> Optional[str]:
    """Check if the current speaker has an assigned language override.

    Args:
        source: The AudioSource to check.

    Returns:
        DeepL language code if the speaker has a language override, else None.
    """
    import server as _srv

    if not source.speaker:
        return None
    speaker_langs: dict[str, str] = _srv.config.get("speaker_langs", {})
    return speaker_langs.get(source.speaker)


def _detect_segment_lang(
    source: AudioSource,
    buf: np.ndarray,
) -> Optional[str]:
    """Detect the language of an audio segment for bi-directional mode.

    NOTE: Auto-detection disabled -- bi-directional uses manual operator
    swap only.

    Args:
        source: The AudioSource for this segment.
        buf: Audio buffer to analyse.

    Returns:
        Detected DeepL language code, or None if detection is disabled.
    """
    return None


# ══════════════════════════════════════════════
# Voice ID Helpers
# ══════════════════════════════════════════════


def _voice_id_try_enroll(
    source: AudioSource,
    buf: np.ndarray,
    loop: Optional[asyncio.AbstractEventLoop] = None,
) -> None:
    """If enrollment is pending and we have enough audio, enroll the voiceprint.

    Args:
        source: The AudioSource with a pending enrollment.
        buf: Accumulated audio buffer.
        loop: Asyncio event loop for broadcasting status (optional).
    """
    import server as _srv
    from linguataxi.models import voice_id
    from linguataxi.server.websocket import _bc

    pending = source.voice_id_enroll_pending
    if not pending:
        return
    min_samples = int(voice_id.MIN_ENROLL_SECONDS * voice_id.SAMPLE_RATE)
    if len(buf) < min_samples:
        return  # not enough audio yet -- will try again on next segment
    try:
        # Use the last N seconds of the buffer for enrollment
        enroll_buf = buf[-min_samples:] if len(buf) > min_samples else buf
        emb = voice_id.extract_embedding(enroll_buf)
        voice_id.registry.enroll(pending, emb)
        source.voice_id_enroll_pending = None
        _srv.plugin_dispatcher.fire("on_speaker_enrolled", {
            "speaker": pending, "source_id": source.id
        })
        if loop is not None:
            _bc(loop, {"type": "voice_id_enrolled", "speaker": pending,
                       "source_id": source.id})
    except RuntimeError as e:
        # Model unavailable (e.g. download failed) -- give up to avoid retry spam
        log.warning(f"[Voice ID] Enrollment aborted for '{pending}': {e}")
        source.voice_id_enroll_pending = None
        if loop is not None:
            _bc(loop, {"type": "voice_id_error", "speaker": pending,
                       "error": str(e)[:200]})
    except Exception as e:
        # Transient errors: log and retry on next segment
        log.debug(f"[Voice ID] Enrollment attempt failed for '{pending}': {e}")


def _voice_id_try_identify(
    source: AudioSource,
    buf: np.ndarray,
    loop: asyncio.AbstractEventLoop,
) -> bool:
    """Try to auto-identify the speaker from the audio segment.

    Rate-limited to once per 1.5 s per source.

    Args:
        source: The AudioSource to identify.
        buf: Audio buffer for identification.
        loop: Asyncio event loop for broadcasting results.

    Returns:
        True if the speaker was auto-switched.
    """
    import server as _srv
    from linguataxi.models import voice_id
    from linguataxi.server.websocket import _bc

    if not _srv.config.get("voice_id_enabled", True):
        return False
    if voice_id.registry.count < 2:
        return False  # need at least 2 enrolled speakers to auto-switch
    min_samples = int(voice_id.MIN_IDENTIFY_SECONDS * voice_id.SAMPLE_RATE)
    if len(buf) < min_samples:
        return False
    # Per-source rate limit: skip if we identified within the last 1.5s
    now_t = time.monotonic()
    last_t: float = getattr(source, "_voice_id_last_check", 0.0)
    if now_t - last_t < 1.5:
        return False
    source._voice_id_last_check = now_t  # type: ignore[attr-defined]
    try:
        # Use the last 3 seconds for identification (best signal-to-noise)
        id_samples = int(3.0 * voice_id.SAMPLE_RATE)
        id_buf = buf[-id_samples:] if len(buf) > id_samples else buf
        emb = voice_id.extract_embedding(id_buf)
        match = voice_id.registry.identify(emb)
        if match is None:
            return False
        name, confidence = match
        if name == source.speaker:
            return False  # same speaker, no change needed
        old_speaker = source.speaker
        source.speaker = name
        # Try to restore the speaker's colour from config
        sc = _srv.config.get("speaker_config", {})
        for key, info in sc.items():
            if info.get("name") == name:
                source.color = info.get("color", "")
                break
        log.info(f"[Voice ID] Auto-detected '{name}' (confidence: {confidence:.2f}, "
                 f"was '{old_speaker}') on [{source.name}]")
        _bc(loop, {"type": "speaker_change", "speaker": name, "auto": True,
                    "confidence": round(confidence, 2), "previous": old_speaker})
        _srv.plugin_dispatcher.fire("on_auto_speaker_change", {
            "speaker": name, "previous": old_speaker,
            "confidence": confidence, "source_id": source.id
        })
        _srv._save_speaker_config()
        return True
    except Exception as e:
        log.debug(f"[Voice ID] Identification failed: {e}")
        return False


# ══════════════════════════════════════════════
# Buffer-Based Audio Processing Loop
# ══════════════════════════════════════════════


def _buffer_audio_loop(
    transcribe_fn: Callable[..., str],
    loop: asyncio.AbstractEventLoop,
    source: AudioSource,
) -> None:
    """Per-source audio processing loop for buffer-based backends (Whisper, MLX).

    Accumulates audio chunks, detects speech/silence boundaries, handles
    speaker changes with 0.5 s retroactive buffer splitting, and submits
    completed segments to the shared ``_transcription_queue``.

    Args:
        transcribe_fn: Backend transcription function.
        loop: Asyncio event loop for broadcasting results.
        source: The AudioSource to process.
    """
    import server as _srv
    from linguataxi.server.websocket import _bc
    from linguataxi.server.translation import _translate_all

    buf = np.empty((0, 1), dtype=np.float32)
    is_speech: bool = False
    silence_start: Optional[float] = None
    seg_start: Optional[float] = None
    last_interim: float = 0

    while not _srv.shutdown_event.is_set() and source.active:
        try:
            chunk = source.queue.get(timeout=0.5)
        except queue.Empty:
            continue

        # Skip processing when captioning is paused (unless dictation is active)
        if _srv.captioning_paused and not _srv.dictation_active:
            buf = np.empty((0, 1), dtype=np.float32)
            is_speech = False
            silence_start = None
            seg_start = None
            last_interim = 0
            continue

        # Check for pending speaker change
        buf, seg_start, changed = _check_speaker_change(source, transcribe_fn, buf, seg_start, loop)
        if changed:
            last_interim = 0
            if len(buf) == 0:
                is_speech = False
                silence_start = None
                seg_start = None

        # Voice ID: try enrollment if pending (needs accumulated audio)
        if source.voice_id_enroll_pending and len(buf) > 0:
            _voice_id_try_enroll(source, buf, loop)

        buf = np.concatenate([buf, chunk])
        rms: float = float(np.sqrt(np.mean(chunk**2)))
        now: float = time.time()

        if rms >= _srv.silence_threshold:
            if not is_speech:
                is_speech = True
                seg_start = seg_start or now
                silence_start = None
                _bc(loop, {"type": "status", "state": "speech"})
            else:
                silence_start = None
            dur = len(buf) / SAMPLE_RATE
            if (now - last_interim) >= INTERIM_INTERVAL and dur >= 1.0:
                last_interim = now
                text = transcribe_fn(buf, lang=source.current_lang)
                if text:
                    _bc(loop, {"type": "interim", "text": text, "speaker": source.speaker,
                               "color": source.color, "source_id": source.id})
                    _srv.plugin_dispatcher.fire("on_interim", {
                        "text": text, "speaker": source.speaker, "source_id": source.id
                    })
                    _translate_all(text, "interim_translation", loop, max_slots=2,
                                   source_lang=source.current_lang)
        else:
            if is_speech:
                if silence_start is None:
                    silence_start = now
                elif (now - silence_start) >= SILENCE_DURATION:
                    if len(buf) / SAMPLE_RATE >= MIN_SPEECH_DURATION:
                        # Voice ID: identify speaker before transcription
                        _voice_id_try_identify(source, buf, loop)
                        detected_lang = _detect_segment_lang(source, buf)
                        try:
                            _transcription_queue.put_nowait((source, buf.copy(), detected_lang))
                        except queue.Full:
                            log.warning(f"Transcription queue full, dropping segment from [{source.name}]")
                    buf = np.empty((0, 1), dtype=np.float32)
                    is_speech = False
                    silence_start = None
                    seg_start = None
                    last_interim = 0
                    _bc(loop, {"type": "status", "state": "silence"})

        if is_speech and seg_start and (now - seg_start) >= MAX_SEGMENT_DURATION:
            # Voice ID: identify speaker before transcription
            _voice_id_try_identify(source, buf, loop)
            detected_lang = _detect_segment_lang(source, buf)
            try:
                _transcription_queue.put_nowait((source, buf.copy(), detected_lang))
            except queue.Full:
                log.warning(f"Transcription queue full, dropping segment from [{source.name}]")
            buf = np.empty((0, 1), dtype=np.float32)
            is_speech = False
            silence_start = None
            seg_start = now
            last_interim = 0


# ══════════════════════════════════════════════
# Stream Management
# ══════════════════════════════════════════════


def _make_audio_callback(source: AudioSource) -> Callable[..., None]:
    """Create a sounddevice callback bound to a specific AudioSource's queue.

    Args:
        source: The AudioSource whose queue will receive audio chunks.

    Returns:
        A callback suitable for ``sd.InputStream``.
    """
    def callback(indata: np.ndarray, frames: int, ti: Any, status: Any) -> None:
        if status:
            log.warning(f"Audio [{source.name}]: {status}")
        source.queue.put(indata.copy())
    return callback


def _open_input_stream(
    source: AudioSource,
    callback: Callable[..., None],
) -> sd.InputStream:
    """Open an sd.InputStream for *source*, handling sample-rate negotiation.

    Some devices (notably Stereo Mix / loopback on Windows) reject 16 kHz and
    only accept their native rate.  When the initial open at SAMPLE_RATE fails
    we query the device's default_samplerate and open at that rate instead,
    wrapping the callback so it resamples on the fly.

    Args:
        source: The AudioSource to open a stream for.
        callback: Audio callback (from _make_audio_callback).

    Returns:
        An open and started sd.InputStream.

    Raises:
        Exception: If neither the target rate nor the native rate works.
    """
    bs: int = int(SAMPLE_RATE * CHUNK_DURATION)

    wasapi_kw: dict[str, Any] = {}
    if sys.platform == "win32":
        try:
            wasapi_kw["extra_settings"] = sd.WasapiSettings(exclusive=False, auto_convert=True)
        except Exception:
            pass

    first_err: Exception | None = None

    # First try: open at the target 16 kHz
    try:
        s = sd.InputStream(samplerate=SAMPLE_RATE, channels=CHANNELS, dtype=DTYPE,
                           blocksize=bs, device=source.device_index, callback=callback,
                           **wasapi_kw)
        s.start()
        log.info(f"Audio stream opened for [{source.name}] at {SAMPLE_RATE} Hz "
                 f"(device: {source.device_index or 'default'})")
        return s
    except Exception as e:
        first_err = e
        log.debug(f"[{source.name}] cannot open at {SAMPLE_RATE} Hz: {first_err}")

    # Fallback: open at the device's native rate and resample
    try:
        dev_info = sd.query_devices(source.device_index)
        native_rate = int(dev_info["default_samplerate"])
    except Exception:
        raise first_err  # can't determine native rate -- re-raise original error

    if native_rate == SAMPLE_RATE:
        raise first_err  # native rate is already what we tried

    native_bs: int = int(native_rate * CHUNK_DURATION)

    # Build a resampling callback that converts native_rate -> SAMPLE_RATE
    def _resample_cb(indata: np.ndarray, frames: int, ti: Any, status: Any) -> None:
        if status:
            log.warning(f"Audio [{source.name}]: {status}")
        # Simple linear interpolation resample (good enough for speech)
        ratio = SAMPLE_RATE / native_rate
        n_out = int(len(indata) * ratio)
        indices = np.linspace(0, len(indata) - 1, n_out).astype(np.float32)
        idx_floor = indices.astype(np.intp)
        idx_ceil = np.minimum(idx_floor + 1, len(indata) - 1)
        frac = (indices - idx_floor).reshape(-1, 1)
        resampled = indata[idx_floor] * (1 - frac) + indata[idx_ceil] * frac
        source.queue.put(resampled.astype(np.float32))

    s = sd.InputStream(samplerate=native_rate, channels=CHANNELS, dtype=DTYPE,
                       blocksize=native_bs, device=source.device_index,
                       callback=_resample_cb, **wasapi_kw)
    s.start()
    log.info(f"Audio stream opened for [{source.name}] at {native_rate} Hz "
             f"(native rate, resampling to {SAMPLE_RATE} Hz) "
             f"(device: {source.device_index or 'default'})")
    return s


def start_source_capture(source: AudioSource) -> None:
    """Open audio stream for a single AudioSource and keep it alive.

    Runs in its own thread.  Retries on failure with exponential backoff
    up to 30 s, so a temporarily unavailable device can recover without
    killing the source.

    Args:
        source: The AudioSource to capture from.
    """
    import server as _srv

    retry_delay: int = 2
    while source.active and not _srv.shutdown_event.is_set():
        source.restart_event.clear()
        try:
            cb = _make_audio_callback(source)
            s = _open_input_stream(source, cb)
            source.stream = s
            retry_delay = 2  # reset on successful open
            while source.active and not _srv.shutdown_event.is_set() and not source.restart_event.is_set():
                _srv.shutdown_event.wait(0.3)
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
            if not source.active or _srv.shutdown_event.is_set():
                break
            log.info(f"[{source.name}] retrying in {retry_delay}s...")
            _srv.shutdown_event.wait(retry_delay)
            retry_delay = min(retry_delay * 2, 30)


def start_audio_capture(dev_idx: Optional[int] = None) -> None:
    """Legacy single-source capture entry point.

    Creates Source 0 if it does not already exist, then starts capturing.

    Args:
        dev_idx: Sounddevice device index, or None for default.
    """
    src = get_source(0)
    if not src:
        src = add_source(dev_idx, "Microphone")
    start_source_capture(src)
