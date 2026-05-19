"""Vosk (CPU-optimized) speech backend with streaming recognition."""

from __future__ import annotations

import asyncio
import json
import logging
import queue
import sys
import threading
import time
import urllib.request
import zipfile
from pathlib import Path
from typing import Any, Optional

import numpy as np

from linguataxi.constants import (
    DEEPL_TO_WHISPER,
    SAMPLE_RATE,
    VOSK_DIR_LANGS,
)
from linguataxi.settings import MODELS_DIR
from .base import SpeechBackend

log: logging.Logger = logging.getLogger("livecaption")


def _load_vosk_bidir_model(lang_code: str) -> Any:
    """Load a Vosk model for the given DeepL language code.

    Searches the models directory for a Vosk model matching the target
    language and returns a ``vosk.Model`` instance, or ``None`` if no
    matching model is found.

    Args:
        lang_code: A DeepL language code (e.g. 'DE', 'ES').

    Returns:
        A ``vosk.Model`` instance, or ``None`` if not found.
    """
    target_lang = DEEPL_TO_WHISPER.get(lang_code, lang_code.lower())
    for d in MODELS_DIR.glob("vosk-model-*"):
        if not d.is_dir():
            continue
        name = d.name.lower()
        for pattern, code in VOSK_DIR_LANGS.items():
            if f"-{pattern}" in name and code == target_lang:
                import vosk

                vosk.SetLogLevel(-1)
                log.info(f"Loading Vosk model for {lang_code}: {d.name}")
                return vosk.Model(str(d))
    log.error(f"No Vosk model found for language: {lang_code}")
    return None


class VoskBackend(SpeechBackend):
    """CPU-optimized streaming speech recognition via Vosk/Kaldi.

    Unlike the buffer-based Whisper backends, Vosk uses a stateful
    ``KaldiRecognizer`` that processes audio incrementally and produces
    partial/final results as a stream.  Speaker changes force-finalize
    the current recognition rather than splitting the audio buffer.
    """

    MODELS: dict[str, dict[str, str]] = {
        "small": {
            "url": "https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip",
            "dir": "vosk-model-small-en-us-0.15",
            "size": "~68 MB",
        },
        "large": {
            "url": "https://alphacephei.com/vosk/models/vosk-model-en-us-0.22.zip",
            "dir": "vosk-model-en-us-0.22",
            "size": "~1.8 GB",
        },
    }

    def __init__(self, model_size: str = "auto") -> None:
        """Initialise the Vosk backend, downloading the model if needed.

        Args:
            model_size: One of 'small', 'large', or 'auto' (picks the
                        best available model on disk).
        """
        import vosk

        vosk.SetLogLevel(-1)

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
        if not mp.exists():
            self._dl(info, mp)
        self._model = vosk.Model(str(mp))
        self._name: str = info["dir"]
        self._bidir_model: Any = None  # secondary Vosk model for bi-directional mode
        self._bidir_lang: Optional[str] = None  # DeepL lang code of secondary model

    def _dl(self, info: dict[str, str], mp: Path) -> None:
        """Download and extract a Vosk model.

        Args:
            info: Model metadata dict with 'url', 'dir', 'size' keys.
            mp: Target directory path for the extracted model.
        """
        zp = MODELS_DIR / (info["dir"] + ".zip")
        print(f"\n  Downloading Vosk model ({info['size']})...")

        def prog(bn: int, bs: int, ts: int) -> None:
            if ts > 0:
                pct = min(100, bn * bs * 100 // ts)
                print(f"\r  [{'#' * (pct // 3)}{'-' * (33 - pct // 3)}] {pct}%", end="", flush=True)

        try:
            urllib.request.urlretrieve(info["url"], str(zp), prog)
            print("\n  Extracting...")
            with zipfile.ZipFile(str(zp), "r") as z:
                z.extractall(str(MODELS_DIR))
            zp.unlink()
            print(f"  Model ready\n")
        except PermissionError:
            if zp.exists():
                try:
                    zp.unlink()
                except Exception:
                    log.debug("Failed to clean up partial download after PermissionError")
            print(f"\n  Cannot write to models directory (permission denied).")
            print(f"  Re-run the installer or use 'Download Tuned Languages' in the launcher.")
            sys.exit(1)
        except Exception as e:
            if zp.exists():
                try:
                    zp.unlink()
                except Exception:
                    log.debug("Failed to clean up partial download after error")
            print(f"\n  Download failed: {e}")
            sys.exit(1)

    @property
    def name(self) -> str:
        """Human-readable backend description."""
        return f"vosk ({self._name})"

    def process_audio_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Start a Vosk processing loop for each registered audio source.

        Args:
            loop: The asyncio event loop for broadcasting results.
        """
        # Deferred import: source registry still lives in server.py
        import server as _srv

        with _srv._sources_lock:
            for src in _srv._sources:
                t = threading.Thread(
                    target=self._vosk_source_loop,
                    args=(loop, src),
                    daemon=True,
                )
                t.start()
                src.buffer_thread = t

    def _vosk_source_loop(self, loop: asyncio.AbstractEventLoop, source: Any) -> None:
        """Per-source Vosk recognition loop.

        Each source gets its own ``KaldiRecognizer``.  Supports dual
        recognizers for bi-directional mode (two languages simultaneously).

        Args:
            loop: The asyncio event loop for broadcasting results.
            source: An ``AudioSource`` instance to read audio chunks from.
        """
        # Deferred imports: globals still live in server.py
        import server as _srv
        import vosk

        rec = vosk.KaldiRecognizer(self._model, SAMPLE_RATE)
        bidir_rec: Any = None  # secondary recognizer (lazy-loaded)
        active_rec = rec  # currently active recognizer
        bidir_was_on: bool = False  # track toggle state
        last_partial: str = ""
        last_pt: float = 0
        in_speech: bool = False
        lang_detect_buf = np.empty((0, 1), dtype=np.float32)
        voice_id_buf = np.empty((0, 1), dtype=np.float32)
        last_lang_check: float = 0

        while not _srv.shutdown_event.is_set() and source.active:
            try:
                chunk = source.queue.get(timeout=0.5)
            except queue.Empty:
                continue

            # Skip processing when captioning is paused (unless dictation is active)
            if _srv.captioning_paused and not _srv.dictation_active:
                if in_speech:
                    active_rec.FinalResult()  # reset recognizer state
                    last_partial = ""
                    in_speech = False
                lang_detect_buf = np.empty((0, 1), dtype=np.float32)
                continue

            # Bi-directional toggle handling
            bidir_on = _srv.config.get("bidirectional_enabled", False)
            bidir_langs = _srv.config.get("bidirectional_langs", [])
            if bidir_on and len(bidir_langs) == 2 and not bidir_was_on:
                # Toggled ON: load secondary model
                primary_lang = _srv.config.get("input_lang", "EN")
                secondary_lang = bidir_langs[1] if bidir_langs[0] == primary_lang else bidir_langs[0]
                if self._bidir_model is None or self._bidir_lang != secondary_lang:
                    self._bidir_model = _load_vosk_bidir_model(secondary_lang)
                    self._bidir_lang = secondary_lang
                if self._bidir_model:
                    bidir_rec = vosk.KaldiRecognizer(self._bidir_model, SAMPLE_RATE)
                    log.info(
                        f"[src {source.id}] Vosk bi-directional enabled: "
                        f"{primary_lang} + {secondary_lang}"
                    )
                else:
                    bidir_rec = None
                bidir_was_on = True
            elif not bidir_on and bidir_was_on:
                # Toggled OFF: free secondary model, revert to primary recognizer
                if active_rec is bidir_rec and bidir_rec is not None:
                    result = json.loads(bidir_rec.FinalResult())
                    text = result.get("text", "").strip()
                    if text:
                        _srv._broadcast_final(text, loop, source, detected_lang=source.current_lang)
                    active_rec = rec
                bidir_rec = None
                self._bidir_model = None
                self._bidir_lang = None
                source.current_lang = None
                bidir_was_on = False
                lang_detect_buf = np.empty((0, 1), dtype=np.float32)
                log.info(f"[src {source.id}] Vosk bi-directional disabled")

            # Speaker change: force-finalize current recognition
            with source.speaker_lock:
                sc = source.speaker_change_pending
                if sc:
                    source.speaker_change_pending = None
            if sc:
                old_speaker = source.speaker
                new_speaker = sc["name"]
                log.info(
                    f"[src {source.id}] Speaker: {old_speaker or '(none)'} -> "
                    f"{new_speaker or '(none)'}"
                )
                result = json.loads(active_rec.FinalResult())
                text = result.get("text", "").strip()
                if text:
                    _srv._broadcast_final(text, loop, source, detected_lang=source.current_lang)
                source.speaker = new_speaker
                last_partial = ""
                in_speech = False
                lang_detect_buf = np.empty((0, 1), dtype=np.float32)
                # Voice ID enrollment for Vosk
                if new_speaker and _srv.config.get("voice_id_enabled", True):
                    source.voice_id_enroll_pending = new_speaker
                voice_id_buf = np.empty((0, 1), dtype=np.float32)

            audio_bytes = (chunk.flatten() * 32767).astype(np.int16).tobytes()
            # Accumulate audio for Voice ID
            voice_id_buf = np.concatenate([voice_id_buf, chunk])
            rms = float(np.sqrt(np.mean(chunk**2)))
            if rms >= _srv.silence_threshold and not in_speech:
                in_speech = True
                _srv._bc(loop, {"type": "status", "state": "speech"})

            # Language detection for bi-directional mode
            if bidir_on and bidir_rec is not None and rms >= _srv.silence_threshold:
                lang_detect_buf = np.concatenate([lang_detect_buf, chunk])
                now_ld = time.time()
                if len(lang_detect_buf) >= SAMPLE_RATE and (now_ld - last_lang_check) >= 1.0:
                    last_lang_check = now_ld
                    old_lang = source.current_lang
                    detected_lang = _srv._detect_segment_lang(source, lang_detect_buf)
                    lang_detect_buf = np.empty((0, 1), dtype=np.float32)
                    if detected_lang and old_lang and detected_lang != old_lang:
                        result = json.loads(active_rec.FinalResult())
                        text = result.get("text", "").strip()
                        if text:
                            _srv._broadcast_final(text, loop, source, detected_lang=old_lang)
                        last_partial = ""
                        in_speech = False
                        primary_lang = _srv.config.get("input_lang", "EN")
                        det_base = detected_lang.split("-")[0]
                        pri_base = primary_lang.split("-")[0]
                        if det_base == pri_base:
                            active_rec = rec
                        else:
                            active_rec = bidir_rec
                        log.info(
                            f"[src {source.id}] Vosk lang switch: "
                            f"{old_lang} -> {detected_lang}"
                        )

            if active_rec.AcceptWaveform(audio_bytes):
                # Voice ID: try enrollment if pending
                if source.voice_id_enroll_pending and len(voice_id_buf) > 0:
                    _srv._voice_id_try_enroll(source, voice_id_buf, loop)
                # Voice ID: identify speaker before broadcasting
                _srv._voice_id_try_identify(source, voice_id_buf, loop)
                text = json.loads(active_rec.Result()).get("text", "").strip()
                if text:
                    _srv._broadcast_final(text, loop, source, detected_lang=source.current_lang)
                last_partial = ""
                in_speech = False
                _srv._bc(loop, {"type": "status", "state": "silence"})
                lang_detect_buf = np.empty((0, 1), dtype=np.float32)
                voice_id_buf = np.empty((0, 1), dtype=np.float32)
            else:
                pt = json.loads(active_rec.PartialResult()).get("partial", "").strip()
                if pt and pt != last_partial:
                    last_partial = pt
                    _srv._bc(
                        loop,
                        {
                            "type": "interim",
                            "text": pt,
                            "speaker": source.speaker,
                            "color": source.color,
                            "source_id": source.id,
                        },
                    )
                    _srv.plugin_dispatcher.fire(
                        "on_interim",
                        {"text": pt, "speaker": source.speaker, "source_id": source.id},
                    )
                    now = time.time()
                    if (now - last_pt) >= 2.0 and len(pt) > 20:
                        last_pt = now
                        _srv._translate_all(
                            pt,
                            "interim_translation",
                            loop,
                            max_slots=2,
                            source_lang=source.current_lang,
                        )
