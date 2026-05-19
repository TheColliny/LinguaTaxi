"""Batch and live file transcription route handlers.

Routes for file-based transcription and translation, including batch
processing of audio/text files and live audio playback through the
captioning pipeline.
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi import Form
from pydantic import BaseModel

from linguataxi.settings import TRANSCRIPTS_DIR
from linguataxi.server.translation import translate_text
from linguataxi.server.audio import _sources, _sources_lock

log: logging.Logger = logging.getLogger("livecaption")

# ── Lock for file transcription operations ──
_file_transcribe_lock: threading.Lock = threading.Lock()


class BatchTranslationSlot(BaseModel):
    """A single translation slot for batch processing.

    Attributes:
        lang: Target language code (e.g. ``"ES"``, ``"FR"``).
        mode: Translation mode — ``"deepl"`` or ``"offline"``.
    """
    lang: str
    mode: str = "deepl"


class BatchRequest(BaseModel):
    """Request body for batch file transcription.

    Attributes:
        file_path: Path to a single file to transcribe.
        folder_path: Path to a folder for batch processing.
        recursive: Whether to recurse into subdirectories.
        translations: List of translation slots to apply.
        output_dir: Output directory for transcripts.
        source_lang: Source language code override.
    """
    file_path: Optional[str] = None
    folder_path: Optional[str] = None
    recursive: bool = False
    translations: List[BatchTranslationSlot] = []
    output_dir: Optional[str] = None
    source_lang: Optional[str] = None


def register_transcribe_routes(app: FastAPI) -> None:
    """Register file transcription routes on the operator app.

    Args:
        app: The operator FastAPI application (port 3001).
    """
    import transcribe_file

    @app.post("/api/transcribe-file/batch")
    async def o_transcribe_batch(req: BatchRequest) -> JSONResponse:
        """Start batch file transcription/translation in a background thread.

        Args:
            req: Batch request with file/folder path and translation slots.
        """
        import server as _srv

        progress = transcribe_file.get_progress()
        if progress["status"] in ("processing", "playing"):
            return JSONResponse(
                {"error": "File transcription already in progress"},
                status_code=409,
            )

        has_file = req.file_path and Path(req.file_path).exists()
        has_folder = req.folder_path and Path(req.folder_path).is_dir()
        if not has_file and not has_folder:
            return JSONResponse(
                {"error": "File or folder not found"},
                status_code=400,
            )

        if has_file:
            ext = Path(req.file_path).suffix.lower()
            supported = transcribe_file.AUDIO_EXTS | transcribe_file.TEXT_EXTS
            if ext not in supported:
                return JSONResponse(
                    {"error": f"Unsupported file type: {ext}"},
                    status_code=400,
                )
            is_text = ext in transcribe_file.TEXT_EXTS
        else:
            is_text = False

        translations = [{"lang": t.lang, "mode": t.mode} for t in req.translations]
        if has_file and is_text and not translations:
            return JSONResponse(
                {"error": "Text files require translation — select a language"},
                status_code=400,
            )

        src_lang = req.source_lang or _srv.config.get("input_lang", "EN")
        output_dir = req.output_dir or str(TRANSCRIPTS_DIR)
        file_path = req.file_path
        folder_path = req.folder_path
        recursive = req.recursive

        def run() -> None:
            target = folder_path or file_path
            log.info(f"Batch transcription starting: {target}")
            try:
                # Pause mic input to avoid GPU contention
                with _sources_lock:
                    for src in _sources:
                        if src.stream is not None:
                            try:
                                src.stream.stop()
                            except Exception:
                                log.debug("Failed to stop source stream during batch", exc_info=True)
                time.sleep(1.0)

                with _file_transcribe_lock:
                    if has_folder:
                        transcribe_file.batch_folder(
                            folder_path=folder_path,
                            recursive=recursive,
                            stt_backend=_srv.stt_backend,
                            translate_fn=translate_text,
                            translations=translations,
                            output_dir=output_dir,
                            source_lang=src_lang,
                        )
                    elif is_text:
                        transcribe_file.batch_translate_text(
                            file_path=file_path,
                            translate_fn=translate_text,
                            translations=translations,
                            output_dir=output_dir,
                            source_lang=src_lang,
                        )
                    else:
                        transcribe_file.batch_transcribe(
                            file_path=file_path,
                            stt_backend=_srv.stt_backend,
                            translate_fn=translate_text,
                            translations=translations,
                            transcripts_dir=output_dir,
                            source_lang=src_lang,
                        )
            except Exception as e:
                log.exception("Batch transcription thread crashed")
                transcribe_file._set_progress("error", 0, f"Internal error: {e}")
            finally:
                with _sources_lock:
                    for src in _sources:
                        if src.stream is not None:
                            try:
                                src.stream.start()
                            except Exception:
                                log.debug("Failed to restart source stream after batch", exc_info=True)
                log.info("Batch transcription complete, mic resumed")

        threading.Thread(target=run, daemon=True).start()
        return JSONResponse({"status": "started"})

    @app.post("/api/transcribe-file/live")
    async def o_transcribe_live(file_path: str = Form(...)) -> JSONResponse:
        """Start live file playback — pauses mic, feeds file as audio input.

        Args:
            file_path: Path to the audio file to play.
        """
        if not Path(file_path).exists():
            return JSONResponse({"error": "File not found"}, status_code=400)

        progress = transcribe_file.get_progress()
        if progress["status"] in ("processing", "playing"):
            return JSONResponse(
                {"error": "File transcription already in progress"},
                status_code=409,
            )

        # Pause mic streams
        with _sources_lock:
            for src in _sources:
                if src.stream is not None:
                    try:
                        src.stream.stop()
                    except Exception:
                        log.debug("Failed to stop source stream for live playback", exc_info=True)

        # Use first source for playback
        with _sources_lock:
            if not _sources:
                return JSONResponse(
                    {"error": "No audio source available"},
                    status_code=400,
                )
            source = _sources[0]

        def on_complete() -> None:
            with _sources_lock:
                for src in _sources:
                    if src.stream is not None:
                        try:
                            src.stream.start()
                        except Exception:
                            log.debug("Failed to restart source stream after playback", exc_info=True)
            log.info("File playback complete, mic resumed")

        try:
            samples, duration = transcribe_file.load_audio(file_path)
            transcribe_file.start_live_playback(
                file_path, source, on_complete=on_complete
            )
            return JSONResponse({
                "status": "playing",
                "duration_sec": round(duration, 1),
            })
        except ValueError as e:
            on_complete()
            return JSONResponse({"error": str(e)}, status_code=400)

    @app.post("/api/transcribe-file/stop")
    async def o_transcribe_stop() -> JSONResponse:
        """Stop live playback if active, resume mic."""
        transcribe_file.stop_live_playback()
        with _sources_lock:
            for src in _sources:
                if src.stream is not None:
                    try:
                        src.stream.start()
                    except Exception:
                        log.debug("Failed to restart source stream after stop", exc_info=True)
        return JSONResponse({"status": "stopped"})

    @app.get("/api/transcribe-file/progress")
    async def o_transcribe_progress() -> JSONResponse:
        """Get current file transcription progress."""
        return JSONResponse(transcribe_file.get_progress())
