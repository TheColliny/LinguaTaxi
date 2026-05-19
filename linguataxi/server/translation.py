"""Translation management with DeepL and offline model support."""

from __future__ import annotations

import concurrent.futures
import logging
import threading
from typing import Any, Optional

import asyncio
import requests

from linguataxi.constants import DEEPL_SOURCE_LANGS
from linguataxi.settings import MODELS_DIR

log: logging.Logger = logging.getLogger("livecaption")

# ── Thread pool and generation tracking ──

_translate_pool: concurrent.futures.ThreadPoolExecutor = (
    concurrent.futures.ThreadPoolExecutor(max_workers=10, thread_name_prefix="translate")
)
_translate_gen: dict[int, int] = {}
_translate_gen_lock: threading.Lock = threading.Lock()


def get_deepl_url(key: str) -> str:
    """Construct the DeepL API endpoint URL based on the API key type.

    Args:
        key: DeepL API key (free keys end with ``:fx``).

    Returns:
        The appropriate DeepL API URL.
    """
    if key.strip().endswith(":fx"):
        return "https://api-free.deepl.com/v2/translate"
    return "https://api.deepl.com/v2/translate"


def _translate_deepl(
    text: str,
    target_lang: str,
    source_lang: Optional[str] = None,
) -> str:
    """Translate text using the DeepL API.

    Args:
        text: Source text to translate.
        target_lang: Target language code (e.g. ``"ES"``, ``"DE"``).
        source_lang: Source language code, or None to use config default.

    Returns:
        Translated text, or empty string on failure.
    """
    import server as _srv

    api_key: str = _srv.config.get("deepl_api_key", "")
    if not text.strip() or not api_key:
        return ""
    src: str = source_lang or _srv.config.get("input_lang", "EN")
    # Strip region from source (DeepL source doesn't use regions)
    if "-" in src and src not in DEEPL_SOURCE_LANGS:
        src = src.split("-")[0]
    try:
        r = requests.post(
            get_deepl_url(api_key),
            headers={
                "Authorization": f"DeepL-Auth-Key {api_key}",
                "Content-Type": "application/json",
            },
            json={"text": [text], "source_lang": src, "target_lang": target_lang},
            timeout=10,
        )
        result = r.json()
        if "translations" in result and result["translations"]:
            return result["translations"][0]["text"]
        return ""
    except Exception as e:
        log.error(f"DeepL translation error: {e}")
        return ""


def translate_text(
    text: str,
    target_lang: str,
    source_lang: Optional[str] = None,
    mode: str = "deepl",
) -> str:
    """Translate text using DeepL or offline models.

    Args:
        text: Source text to translate.
        target_lang: Target language code.
        source_lang: Source language code, or None to use config default.
        mode: Translation engine — ``"deepl"``, ``"offline-auto"``,
            ``"offline-opus"``, or ``"offline-m2m"``.

    Returns:
        Translated text, or empty string on failure.
    """
    import server as _srv
    import offline_translate

    if not text.strip():
        return ""
    if mode == "deepl":
        return _translate_deepl(text, target_lang, source_lang)
    # Offline translation
    engine: str = "auto"
    if mode == "offline-opus":
        engine = "opus-mt"
    elif mode == "offline-m2m":
        engine = "m2m100"
    src: str = source_lang or _srv.config.get("input_lang", "EN")
    result: str = offline_translate.translate_offline(
        text, src, target_lang, str(MODELS_DIR), engine=engine
    )
    if not result:
        log.debug(
            f"Offline translate ({engine}) {src}->{target_lang}: no result "
            f"(model may not be downloaded)"
        )
    return result


def _translate_all(
    text: str,
    msg_type: str,
    loop: asyncio.AbstractEventLoop,
    max_slots: int = 99,
    line_id: Optional[int] = None,
    speaker_override: Optional[str] = None,
    source_lang: Optional[str] = None,
) -> None:
    """Translate a line into all configured language slots.

    Submits each slot to the thread pool.  Same-language slots receive
    a direct copy instead of an API call.

    Args:
        text: Source text to translate.
        msg_type: WebSocket message type (e.g. ``"final_translation"``).
        loop: Asyncio event loop for broadcasting results.
        max_slots: Maximum number of slots to process.
        line_id: Line ID for correlation, or None.
        speaker_override: Override the speaker label, or None.
        source_lang: Source language override, or None.
    """
    import server as _srv
    from linguataxi.server.websocket import _bc

    if _srv.translation_paused:
        return
    if _srv.captioning_paused and _srv.dictation_active:
        return  # Dictation-only mode: no translations

    translations = _srv.config.get("translations", [])
    effective_src: str = source_lang or _srv.config.get("input_lang", "EN")

    for i, t in enumerate(translations):
        if i >= max_slots:
            break

        tgt_base: str = t["lang"].split("-")[0]
        src_base: str = effective_src.split("-")[0]
        if src_base == tgt_base:
            # Same language as source -- copy caption text directly to this
            # slot so it stays in its fixed screen position (no API call)
            speaker: str = speaker_override if speaker_override is not None else ""
            msg: dict[str, Any] = {
                "type": msg_type,
                "translated": text,
                "lang": t["lang"],
                "slot": i,
                "speaker": speaker,
                "is_translation": True,
            }
            if line_id is not None:
                msg["line_id"] = line_id
            _bc(loop, msg)
            continue

        gen: Optional[int] = None
        if msg_type == "interim_translation":
            with _translate_gen_lock:
                gen = _translate_gen.get(i, 0) + 1
                _translate_gen[i] = gen
        _translate_pool.submit(
            _do_translate,
            text,
            t["lang"],
            i,
            msg_type,
            loop,
            line_id,
            speaker_override,
            source_lang,
            gen,
        )


def _do_translate(
    text: str,
    lang: str,
    slot: int,
    msg_type: str,
    loop: asyncio.AbstractEventLoop,
    line_id: Optional[int] = None,
    speaker_override: Optional[str] = None,
    source_lang: Optional[str] = None,
    generation: Optional[int] = None,
) -> None:
    """Translate one language slot and broadcast the result.

    Stale interim results are discarded using the generation counter.

    Args:
        text: Source text to translate.
        lang: Target language code.
        slot: Translation slot index.
        msg_type: WebSocket message type.
        loop: Asyncio event loop for broadcasting.
        line_id: Line ID for correlation, or None.
        speaker_override: Override the speaker label, or None.
        source_lang: Source language override, or None.
        generation: Generation counter for stale-result detection, or None.
    """
    import server as _srv
    import offline_translate
    from linguataxi.server.websocket import _bc
    from linguataxi.server.transcripts import _save_line

    if generation is not None:
        with _translate_gen_lock:
            if _translate_gen.get(slot, 0) != generation:
                return

    translations = _srv.config.get("translations", [])
    mode: str = "deepl"
    if slot < len(translations):
        mode = translations[slot].get("mode", "deepl")
    try:
        translated: str = translate_text(text, lang, source_lang=source_lang, mode=mode)
    except Exception as e:
        engine: str = "offline" if mode.startswith("offline") else "DeepL"
        log.error(f"   [{slot}] {lang} ({engine}) translation error: {e}")
        return

    if not translated:
        if msg_type == "final_translation" and mode.startswith("offline"):
            # Get the specific failure reason recorded by offline_translate
            engine_key: str = {"offline-opus": "opus-mt", "offline-m2m": "m2m100"}.get(
                mode, "auto"
            )
            reason: str = offline_translate.get_last_offline_error(
                source_lang or _srv.config.get("input_lang", "EN"), lang, engine_key
            )
            if not reason:
                reason = "unknown (check model downloads + ctranslate2 install)"
            log.warning(
                f"   [{slot}] {lang} offline translation returned empty "
                f"(mode={mode}) — {reason}"
            )
            # Surface the error to the operator panel so the user sees it
            _bc(
                loop,
                {
                    "type": "offline_translate_error",
                    "slot": slot,
                    "lang": lang,
                    "mode": mode,
                    "reason": reason,
                },
            )
        return

    speaker: str = speaker_override if speaker_override is not None else ""
    msg: dict[str, Any] = {
        "type": msg_type,
        "translated": translated,
        "lang": lang,
        "slot": slot,
        "speaker": speaker,
        "is_translation": True,
    }
    if line_id is not None:
        msg["line_id"] = line_id
    _bc(loop, msg)
    _srv.plugin_dispatcher.fire(
        "on_translation",
        {
            "translated": translated,
            "lang": lang,
            "slot": slot,
            "speaker": speaker_override or "",
            "line_id": line_id,
            "source_lang": source_lang,
        },
    )
    if msg_type == "final_translation":
        prefix: str = f"{speaker}: " if speaker else ""
        engine = "offline" if mode.startswith("offline") else "DeepL"
        log.info(f"   [{slot}] {lang} ({engine}): {prefix}{translated}")
        _save_line(lang, f"{prefix}{translated}")
    elif msg_type == "correct_translation":
        prefix = f"{speaker}: " if speaker else ""
        log.info(f"   [{slot}] {lang} CORRECTED: {prefix}{translated}")
        _save_line(lang, f"[corrected] {prefix}{translated}")
