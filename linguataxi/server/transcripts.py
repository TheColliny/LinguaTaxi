"""Transcript saving and final caption broadcast."""

from __future__ import annotations

import asyncio
import collections
import logging
import threading
import time
from typing import Any, Optional

from linguataxi.constants import DEEPL_SOURCE_LANGS, DEEPL_TARGET_LANGS
from linguataxi.settings import TRANSCRIPTS_DIR

log: logging.Logger = logging.getLogger("livecaption")

# ── Module-level globals ──

_session_stamp: str = time.strftime("%Y%m%d_%H%M%S")
_line_id: int = 0
_line_id_lock: threading.Lock = threading.Lock()
_RECENT_LINES_MAX: int = 50
_recent_lines: collections.deque[dict[str, Any]] = collections.deque(maxlen=_RECENT_LINES_MAX)


def _save_line(lang_code: str, text: str) -> None:
    """Append a timestamped line to the transcript file for this language.

    Args:
        lang_code: DeepL language code (e.g. ``"EN"``, ``"ES"``).
        text: The transcript line to save.
    """
    import server as _srv

    if not _srv.save_transcripts or not text.strip():
        return
    try:
        name: str = DEEPL_TARGET_LANGS.get(
            lang_code, DEEPL_SOURCE_LANGS.get(lang_code, lang_code)
        )
        safe_name: str = (
            "".join(c if c.isalnum() or c in " -_" else "" for c in name)
            .strip()
            .replace(" ", "_")
        )
        fn: str = f"transcript_{_session_stamp}_{safe_name}_{lang_code}.txt"
        ts: str = time.strftime("%H:%M:%S")
        with open(TRANSCRIPTS_DIR / fn, "a", encoding="utf-8") as f:
            f.write(f"[{ts}] {text}\n")
    except Exception as e:
        log.warning(f"Transcript save error: {e}")


def _next_line_id() -> int:
    """Return the next monotonically increasing line ID (thread-safe).

    Returns:
        The new line ID.
    """
    global _line_id
    with _line_id_lock:
        _line_id += 1
        return _line_id


def _store_recent_line(
    lid: int,
    text: str,
    speaker: str,
    src_lang: str,
) -> None:
    """Store a finalised line in the recent-lines buffer.

    Args:
        lid: The line ID.
        text: Finalised transcript text.
        speaker: Speaker label.
        src_lang: Source language code.
    """
    with _line_id_lock:
        _recent_lines.append(
            {"id": lid, "text": text, "speaker": speaker, "src_lang": src_lang}
        )


def _broadcast_final(
    text: str,
    loop: asyncio.AbstractEventLoop,
    source: Optional[Any] = None,
    detected_lang: Optional[str] = None,
) -> None:
    """Broadcast final source text with speaker, save transcript, trigger translations.

    In dictation-only mode, only sends to dictation clients (no
    translations or transcripts).

    Args:
        text: Finalised transcript text.
        loop: Asyncio event loop for broadcasting.
        source: AudioSource instance (speaker/color/source_id; defaults
            to empty strings if None).
        detected_lang: Detected language code, or None.
    """
    from linguataxi.server.websocket import _bc, broadcast_dictation
    from linguataxi.server.translation import _translate_all
    import server as _srv

    speaker: str = source.speaker if source else ""
    color: str = source.color if source else ""
    source_id: int = source.id if source else 0
    lid: int = _next_line_id()

    if _srv.captioning_paused and _srv.dictation_active:
        # Dictation-only mode: just send final text to dictation clients
        dl = _srv._dictation_loop or loop
        asyncio.run_coroutine_threadsafe(
            broadcast_dictation(
                {
                    "type": "final",
                    "text": text,
                    "speaker": speaker,
                    "color": color,
                    "source_id": source_id,
                    "line_id": lid,
                    "detected_lang": detected_lang,
                }
            ),
            dl,
        )
        log.info(f"   DICTATION: {text}")
        return

    _bc(
        loop,
        {
            "type": "final",
            "text": text,
            "speaker": speaker,
            "color": color,
            "source_id": source_id,
            "line_id": lid,
            "detected_lang": detected_lang,
            "is_translation": False,
        },
    )
    prefix: str = f"{speaker}: " if speaker else ""
    log.info(f"   IN: {prefix}{text}")
    src: str = _srv.config.get("input_lang", "EN")
    _save_line(src, f"{prefix}{text}")
    _store_recent_line(lid, text, speaker, src)
    _translate_all(text, "final_translation", loop, line_id=lid, source_lang=detected_lang)
    _srv.plugin_dispatcher.fire(
        "on_final",
        {
            "text": text,
            "speaker": speaker,
            "color": color,
            "source_id": source_id,
            "line_id": lid,
            "detected_lang": detected_lang,
        },
    )
