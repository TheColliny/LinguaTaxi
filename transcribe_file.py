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
