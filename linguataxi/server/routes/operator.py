"""Operator panel route handlers.

Routes for the operator control panel (port 3001) — the largest route
module, covering config management, plugins, tuned models, offline
translation, mic/source management, voice ID, display grids, and the
operator WebSocket.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import threading
import time
from pathlib import Path, PurePosixPath
from typing import Any

from fastapi import (
    FastAPI, HTTPException, Request, UploadFile, WebSocket,
    WebSocketDisconnect, File, Form,
)
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from starlette.responses import Response
from starlette.staticfiles import StaticFiles

import sounddevice as sd

from linguataxi.constants import (
    BG_OPTIONS, COLOR_PALETTE, DEEPL_SOURCE_LANGS, DEEPL_TARGET_DEFAULTS,
    DEEPL_TARGET_LANGS, FONT_OPTIONS, SILENCE_THRESHOLD, VOSK_DIR_LANGS,
)
from linguataxi.settings import (
    MODELS_DIR, UPLOADS_DIR, _config_lock, save_config,
)
from linguataxi.server.websocket import (
    broadcast_all, display_clients, extended_clients, operator_clients,
)
from linguataxi.server.transcripts import (
    _line_id_lock, _recent_lines, _save_line,
)
from linguataxi.server.translation import _translate_all
from linguataxi.server.audio import (
    AudioSource, add_source, get_source, remove_source,
    start_source_capture, _buffer_audio_loop, _sources, _sources_lock,
)

log: logging.Logger = logging.getLogger("livecaption")

# ── Allowed static file extensions for plugin assets ──
_PLUGIN_STATIC_EXTS: frozenset[str] = frozenset({
    ".js", ".css", ".png", ".jpg", ".jpeg", ".svg",
    ".gif", ".ico", ".woff", ".woff2", ".html",
})


def _style_config() -> dict[str, Any]:
    """Build the common style config dict for display clients.

    Returns:
        Dict of style configuration values.
    """
    import server as _srv

    config = _srv.config
    return {
        "session_title": config.get("session_title", "Live Captioning"),
        "input_lang": config.get("input_lang", "EN"),
        "input_lang_name": DEEPL_SOURCE_LANGS.get(
            config.get("input_lang", "EN"), "English"
        ),
        "footer_image": config.get("footer_image"),
        "footer_text": config.get("footer_text", ""),
        "footer_position": config.get("footer_position", 50),
        "font_size": config.get("font_size", 42),
        "max_lines": config.get("max_lines", 3),
        "bg_color": config.get("bg_color", "#00004D"),
        "font_family": config.get("font_family", "atkinson"),
        "caption_color": config.get("caption_color", "#FFFFFF"),
        "speakers": config.get("speakers", []),
    }


def _font_css(fid: str) -> str:
    """Look up the CSS for a font by its ID.

    Args:
        fid: Font identifier string.

    Returns:
        CSS string for the font, falling back to the first option.
    """
    for f in FONT_OPTIONS:
        if f["id"] == fid:
            return f["css"]
    return FONT_OPTIONS[0]["css"]


def _make_plugin_file_handler(plugin_dir: Path):
    """Create a static file handler for a plugin directory.

    Args:
        plugin_dir: Root directory of the plugin.

    Returns:
        Async handler function that serves allowed static files.
    """
    async def _serve(filename: str) -> FileResponse:
        """Serve a static file from the plugin directory.

        Args:
            filename: Relative file path within the plugin.
        """
        if ".." in filename or "\\" in filename:
            raise HTTPException(status_code=404)
        safe = PurePosixPath(filename)
        if safe.suffix.lower() not in _PLUGIN_STATIC_EXTS:
            raise HTTPException(status_code=404)
        fpath = (plugin_dir / filename).resolve()
        if not str(fpath).startswith(str(plugin_dir.resolve())):
            raise HTTPException(status_code=404)
        if not fpath.is_file():
            raise HTTPException(status_code=404)
        return FileResponse(str(fpath))
    return _serve


def register_operator_routes(app: FastAPI) -> None:
    """Register all operator panel routes.

    Args:
        app: The operator FastAPI application (port 3001).
    """
    from linguataxi.models import tuned as tuned_models
    from linguataxi.models import offline_translate
    from linguataxi.models import voice_id
    from linguataxi.server.backends.whisper import WhisperBackend
    from linguataxi.server.backends import model_lock as _model_lock

    # ── Operator index ──

    @app.get("/")
    async def o_index() -> HTMLResponse:
        """Serve the operator panel HTML page."""
        import server as _srv

        html = (_srv.BASE_DIR / "templates" / "operator.html").read_text(encoding="utf-8")
        html = html.replace("<!-- PLUGIN_CSS -->", _srv.plugin_dispatcher.get_css_links())
        html = html.replace("<!-- PLUGIN_PANELS -->", _srv.plugin_dispatcher.get_panel_html())
        html = html.replace("<!-- PLUGIN_JS -->", _srv.plugin_dispatcher.get_js_scripts())
        return HTMLResponse(html)

    # ── Plugin API Routes ──

    @app.get("/api/plugins")
    async def api_plugins_list() -> JSONResponse:
        """List all installed plugins with their metadata."""
        import server as _srv

        result: list[dict[str, Any]] = []
        for m in _srv.plugin_dispatcher.get_all_manifests():
            result.append({
                "id": m.id, "name": m.name, "version": m.version,
                "description": m.description, "author": m.author,
                "enabled": _srv.plugin_dispatcher.is_enabled(m.id),
                "has_panel": m.has_panel, "has_routes": m.has_routes,
                "settings_schema": m.settings_schema,
            })
        return JSONResponse(result)

    @app.get("/api/plugins/{plugin_id}/settings")
    async def api_plugin_settings_get(plugin_id: str) -> JSONResponse:
        """Get settings schema and values for a plugin.

        Args:
            plugin_id: The plugin identifier.
        """
        import server as _srv

        manifests = {m.id: m for m in _srv.plugin_dispatcher.get_all_manifests()}
        m = manifests.get(plugin_id)
        if not m:
            return JSONResponse({"error": "Plugin not found"}, 404)
        return JSONResponse({
            "schema": m.settings_schema,
            "values": _srv.plugin_dispatcher.get_settings(plugin_id),
        })

    @app.post("/api/plugins/{plugin_id}/settings")
    async def api_plugin_settings_post(plugin_id: str, request: Request) -> JSONResponse:
        """Update settings for a plugin.

        Args:
            plugin_id: The plugin identifier.
            request: Request with form data containing setting values.
        """
        import server as _srv

        form = await request.form()
        settings = dict(form)
        manifests = {m.id: m for m in _srv.plugin_dispatcher.get_all_manifests()}
        m = manifests.get(plugin_id)
        if m:
            for key, schema in m.settings_schema.items():
                if schema.get("type") == "toggle" and key in settings:
                    settings[key] = settings[key] in ("true", "True", "1", "on")
                elif schema.get("type") == "number" and key in settings:
                    try:
                        val = float(settings[key])
                        settings[key] = int(val) if val == int(val) else val
                    except (ValueError, OverflowError):
                        pass
        _srv.plugin_dispatcher.save_settings(plugin_id, settings)
        save_config(_srv.config)
        _srv.plugin_dispatcher.fire("on_config_change", {"plugin_id": plugin_id})
        return JSONResponse({"ok": True})

    @app.post("/api/plugins/{plugin_id}/enabled")
    async def api_plugin_enabled(plugin_id: str, request: Request) -> JSONResponse:
        """Toggle plugin enabled/disabled state.

        Args:
            plugin_id: The plugin identifier.
            request: Request with form data containing ``enabled`` flag.
        """
        import server as _srv

        form = await request.form()
        enabled = form.get("enabled", "true") in ("true", "True", "1")
        _srv.plugin_dispatcher.set_enabled(plugin_id, enabled)
        save_config(_srv.config)
        return JSONResponse({"ok": True, "enabled": enabled})

    @app.post("/api/fact-check-broadcast")
    async def api_fact_check_broadcast(request: Request) -> JSONResponse:
        """Relay a fact-check result from operator to display/extended clients.

        Args:
            request: Request with JSON body containing fact-check data.
        """
        try:
            data = await request.json()
        except Exception:
            return JSONResponse({"error": "invalid JSON"}, 400)
        msg = json.dumps({"type": "fact_check_result", "result": data})
        for clients in [display_clients, extended_clients]:
            dead: set = set()
            for ws in list(clients):
                try:
                    await ws.send_text(msg)
                except Exception:
                    dead.add(ws)
            clients.difference_update(dead)
        return JSONResponse({"ok": True})

    @app.get("/uploads/{fn}")
    async def o_uploads(fn: str) -> Response:
        """Serve uploaded files.

        Args:
            fn: Filename to serve.
        """
        p = (UPLOADS_DIR / fn).resolve()
        if not str(p).startswith(str(UPLOADS_DIR.resolve())):
            return JSONResponse({"error": "invalid path"}, 400)
        return FileResponse(p) if p.exists() else JSONResponse({"error": "not found"}, 404)

    # ── Config endpoints ──

    @app.get("/api/config")
    async def o_config() -> JSONResponse:
        """Return full operator config including all panel options."""
        import server as _srv

        try:
            try:
                tuned_info = tuned_models.get_all_status(MODELS_DIR)
            except Exception:
                tuned_info = {}
            try:
                is_whisper = isinstance(_srv.stt_backend, WhisperBackend) if _srv.stt_backend else False
            except Exception:
                is_whisper = False
            active_tuned = ""
            try:
                if is_whisper and _srv.stt_backend and _srv.stt_backend._model_name.startswith("tuned-"):
                    active_tuned = _srv.stt_backend._model_name.replace("tuned-", "").upper()
            except Exception:
                pass

            try:
                offline_info = offline_translate.get_all_status(MODELS_DIR)
            except Exception:
                offline_info = {"opus": {}, "m2m100": {}}

            return JSONResponse({
                **_style_config(),
                "deepl_api_key": _srv.config.get("deepl_api_key", ""),
                "has_api_key": bool(_srv.config.get("deepl_api_key", "")),
                "backend": _srv.stt_backend.name if _srv.stt_backend else "loading...",
                "is_whisper": is_whisper,
                "translation_count": _srv.config.get("translation_count", 1),
                "translations": _srv.config.get("translations", []),
                "font_css": _font_css(_srv.config.get("font_family", "atkinson")),
                "source_langs": DEEPL_SOURCE_LANGS,
                "target_langs": DEEPL_TARGET_LANGS,
                "color_palette": COLOR_PALETTE,
                "bg_options": BG_OPTIONS,
                "font_options": FONT_OPTIONS,
                "tuned_models": tuned_info,
                "active_tuned_lang": active_tuned,
                "offline_translate": offline_info,
                "bidirectional_enabled": _srv.config.get("bidirectional_enabled", False),
                "bidirectional_langs": _srv.config.get("bidirectional_langs", []),
                "bidirectional_tuned_swap": _srv.config.get("bidirectional_tuned_swap", False),
                "speaker_langs": _srv.config.get("speaker_langs", {}),
                "collapsed_sections": _srv.config.get("collapsed_sections", ["languages"]),
                "footer_position": _srv.config.get("footer_position", 50),
                "system_cpu_count": os.cpu_count() or 4,
                "translate_cores": _srv.config.get("translate_cores", 0),
                "translate_cores_default": offline_translate.get_default_cores(),
            })
        except Exception as e:
            log.error(f"Config endpoint error: {e}")
            return JSONResponse({
                **_style_config(),
                "has_api_key": bool(_srv.config.get("deepl_api_key", "")),
                "backend": "error",
                "is_whisper": False,
                "translation_count": _srv.config.get("translation_count", 1),
                "translations": _srv.config.get("translations", []),
                "font_css": _font_css(_srv.config.get("font_family", "atkinson")),
                "source_langs": DEEPL_SOURCE_LANGS,
                "target_langs": DEEPL_TARGET_LANGS,
                "color_palette": COLOR_PALETTE,
                "bg_options": BG_OPTIONS,
                "font_options": FONT_OPTIONS,
                "tuned_models": {},
                "active_tuned_lang": "",
                "offline_translate": {"opus": {}, "m2m100": {}},
                "bidirectional_enabled": _srv.config.get("bidirectional_enabled", False),
                "bidirectional_langs": _srv.config.get("bidirectional_langs", []),
                "bidirectional_tuned_swap": _srv.config.get("bidirectional_tuned_swap", False),
                "speaker_langs": _srv.config.get("speaker_langs", {}),
                "collapsed_sections": _srv.config.get("collapsed_sections", ["languages"]),
                "footer_position": _srv.config.get("footer_position", 50),
                "system_cpu_count": os.cpu_count() or 4,
                "translate_cores": _srv.config.get("translate_cores", 0),
                "translate_cores_default": offline_translate.get_default_cores(),
            })

    @app.get("/api/status")
    async def o_status() -> JSONResponse:
        """Return current captioning and translation pause state."""
        import server as _srv
        return JSONResponse({
            "captioning_paused": _srv.captioning_paused,
            "translation_paused": _srv.translation_paused,
        })

    @app.post("/api/shutdown")
    async def o_shutdown() -> JSONResponse:
        """Graceful server shutdown via operator API."""
        import server as _srv

        log.info("Shutdown requested via operator API")
        threading.Thread(target=_srv._shutdown_and_exit, daemon=True).start()
        return JSONResponse({"status": "shutting_down"})

    @app.get("/api/locales/{lang}")
    async def o_get_locale(lang: str) -> JSONResponse:
        """Serve translation JSON for a language.

        Args:
            lang: Language code (e.g. ``"en"``, ``"es"``).
        """
        import server as _srv

        locale_path = _srv.BASE_DIR / "locales" / f"{lang.lower()}.json"
        if locale_path.exists():
            data = json.loads(locale_path.read_text(encoding="utf-8"))
            return JSONResponse(data)
        en_path = _srv.BASE_DIR / "locales" / "en.json"
        if en_path.exists():
            return JSONResponse(json.loads(en_path.read_text(encoding="utf-8")))
        return JSONResponse({})

    @app.post("/api/config")
    async def o_update(
        session_title: str = Form(None),
        deepl_api_key: str = Form(None),
        input_lang: str = Form(None),
        translation_count: int = Form(None),
        translations_json: str = Form(None),
        speakers: str = Form(None),
        font_size: int = Form(None),
        max_lines: int = Form(None),
        bg_color: str = Form(None),
        font_family: str = Form(None),
        caption_color: str = Form(None),
        ui_language: str = Form(None),
        bidirectional_enabled: str = Form(None),
        bidirectional_langs: str = Form(None),
        bidirectional_tuned_swap: str = Form(None),
        speaker_langs: str = Form(None),
        collapsed_sections: str = Form(None),
        footer_position: int = Form(None),
        footer_text: str = Form(None),
        translate_cores: int = Form(None),
    ) -> JSONResponse:
        """Update operator config (accepts partial updates via form data)."""
        import server as _srv

        config = _srv.config
        if session_title is not None:
            config["session_title"] = session_title
        if deepl_api_key is not None:
            config["deepl_api_key"] = deepl_api_key
        if input_lang is not None:
            config["input_lang"] = input_lang
        if translation_count is not None:
            config["translation_count"] = translation_count
        if translations_json is not None:
            try:
                config["translations"] = json.loads(translations_json)
            except Exception:
                log.warning("Failed to parse translations_json")
        if speakers is not None:
            try:
                config["speakers"] = json.loads(speakers)
            except Exception:
                log.warning("Failed to parse speakers JSON")
        if font_size is not None:
            config["font_size"] = max(24, min(960, font_size))
        if max_lines is not None:
            config["max_lines"] = max(1, min(8, max_lines))
        if bg_color is not None:
            config["bg_color"] = bg_color
        if font_family is not None:
            config["font_family"] = font_family
        if caption_color is not None:
            config["caption_color"] = caption_color
        if ui_language is not None:
            config["ui_language"] = ui_language

        # Bi-directional config
        if speaker_langs is not None:
            try:
                config["speaker_langs"] = json.loads(speaker_langs)
            except Exception:
                log.warning("Failed to parse speaker_langs JSON")
        if bidirectional_tuned_swap is not None:
            config["bidirectional_tuned_swap"] = bidirectional_tuned_swap.lower() in (
                "true", "1", "yes"
            )
        if bidirectional_langs is not None:
            try:
                parsed = json.loads(bidirectional_langs)
                if isinstance(parsed, list) and all(isinstance(x, str) for x in parsed):
                    config["bidirectional_langs"] = parsed
            except Exception:
                log.warning("Failed to parse bidirectional_langs JSON")
        if bidirectional_enabled is not None:
            new_enabled = bidirectional_enabled.lower() in ("true", "1", "yes")
            was_enabled = config.get("bidirectional_enabled", False)
            config["bidirectional_enabled"] = new_enabled

            if new_enabled and not was_enabled:
                bidir_langs = config.get("bidirectional_langs", [])
                if len(bidir_langs) == 2:
                    config["input_lang"] = bidir_langs[0]
                    existing = config.get("translations", [])
                    user_slots = [t for t in existing if not t.get("auto_bidir")]
                    auto_slots = [
                        {
                            "lang": DEEPL_TARGET_DEFAULTS.get(bidir_langs[0], bidir_langs[0]),
                            "color": "#FFD54F",
                            "auto_bidir": True,
                        },
                        {
                            "lang": DEEPL_TARGET_DEFAULTS.get(bidir_langs[1], bidir_langs[1]),
                            "color": "#FFD54F",
                            "auto_bidir": True,
                        },
                    ]
                    config["translations"] = auto_slots + user_slots
                    config["translation_count"] = len(config["translations"])
            elif was_enabled and not new_enabled:
                existing = config.get("translations", [])
                user_slots = [t for t in existing if not t.get("auto_bidir")]
                config["translations"] = user_slots
                config["translation_count"] = len(user_slots)

        if collapsed_sections is not None:
            try:
                parsed = json.loads(collapsed_sections)
                if isinstance(parsed, list):
                    config["collapsed_sections"] = parsed
            except (json.JSONDecodeError, TypeError):
                pass
        if footer_position is not None:
            config["footer_position"] = max(0, min(100, footer_position))
        if footer_text is not None:
            config["footer_text"] = footer_text
        if translate_cores is not None:
            clamped = max(1, min(translate_cores, offline_translate.get_max_cores()))
            config["translate_cores"] = clamped
            offline_translate.set_threads(clamped)

        save_config(config)

        update_msg: dict[str, Any] = {
            "type": "config_update",
            **_style_config(),
            "translation_count": config.get("translation_count", 1),
            "font_css": _font_css(config.get("font_family", "atkinson")),
            "ui_language": config.get("ui_language", "EN"),
        }
        if (
            translations_json is not None
            or translation_count is not None
            or input_lang is not None
            or bidirectional_enabled is not None
        ):
            update_msg["all_translations"] = config.get("translations", [])
        if bidirectional_enabled is not None or bidirectional_langs is not None:
            update_msg["bidirectional_enabled"] = config.get("bidirectional_enabled", False)
            update_msg["bidirectional_langs"] = config.get("bidirectional_langs", [])
        await broadcast_all(update_msg)
        _srv.plugin_dispatcher.fire("on_config_change", {"config": config})
        return JSONResponse({"status": "ok"})

    # ── Footer management ──

    @app.post("/api/upload-footer")
    async def o_upload_footer(file: UploadFile = File(...)) -> JSONResponse:
        """Upload a footer image for the display.

        Args:
            file: The uploaded image file.
        """
        import server as _srv

        ext = Path(file.filename).suffix.lower()
        if ext not in [".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp"]:
            return JSONResponse({"error": "bad type"}, 400)
        fn = f"footer{ext}"
        dest = UPLOADS_DIR / fn
        with open(dest, "wb") as f:
            shutil.copyfileobj(file.file, f)
        _srv.config["footer_image"] = fn
        save_config(_srv.config)
        await broadcast_all({
            "type": "config_update",
            **_style_config(),
            "font_css": _font_css(_srv.config.get("font_family", "atkinson")),
            "translation_count": _srv.config.get("translation_count", 1),
            "all_translations": _srv.config.get("translations", []),
        })
        return JSONResponse({"status": "ok", "filename": fn})

    @app.post("/api/remove-footer")
    async def o_rm_footer() -> JSONResponse:
        """Remove the footer image from the display."""
        import server as _srv

        _srv.config["footer_image"] = None
        save_config(_srv.config)
        await broadcast_all({
            "type": "config_update",
            **_style_config(),
            "font_css": _font_css(_srv.config.get("font_family", "atkinson")),
            "translation_count": _srv.config.get("translation_count", 1),
            "all_translations": _srv.config.get("translations", []),
        })
        return JSONResponse({"status": "ok"})

    # ── Tuned Models API ──

    @app.get("/api/tuned-models")
    async def o_tuned_models() -> JSONResponse:
        """List all tuned models with download/availability status."""
        return JSONResponse(tuned_models.get_all_status(MODELS_DIR))

    @app.post("/api/tuned-models/download")
    async def o_tuned_download(lang: str = Form(...)) -> JSONResponse:
        """Start downloading and converting a tuned model for a language.

        Args:
            lang: Language code (e.g. ``"ES"``, ``"DE"``).
        """
        import server as _srv

        lang = lang.upper()
        if lang not in tuned_models.TUNED_MODELS:
            return JSONResponse({"error": f"No tuned model for {lang}"}, 400)

        if tuned_models.is_available(MODELS_DIR, lang):
            return JSONResponse({"status": "already_available"})

        prog = tuned_models.get_progress(lang)
        if prog["status"] in ("downloading", "converting", "starting"):
            return JSONResponse({"status": "already_in_progress"})

        gpu = _srv.detect_gpu()
        vram = gpu.get("vram", 0)

        tuned_models.download_and_convert(MODELS_DIR, lang, vram_mb=vram)
        return JSONResponse({"status": "started", "vram": vram})

    @app.get("/api/tuned-models/progress/{lang}")
    async def o_tuned_progress(lang: str) -> JSONResponse:
        """Get download/conversion progress for a language.

        Args:
            lang: Language code.
        """
        return JSONResponse(tuned_models.get_progress(lang.upper()))

    @app.post("/api/tuned-models/switch")
    async def o_tuned_switch(lang: str = Form(...)) -> JSONResponse:
        """Hot-swap the active Whisper model to a tuned model.

        Pauses captioning briefly (~2-5s) during the swap.

        Args:
            lang: Language code for the tuned model to activate.
        """
        import server as _srv

        lang = lang.upper()

        if not isinstance(_srv.stt_backend, WhisperBackend):
            return JSONResponse(
                {"error": "Model switching only works with Whisper backend"}, 400
            )

        model_path = tuned_models.get_model_path(MODELS_DIR, lang)
        if not tuned_models.is_available(MODELS_DIR, lang):
            return JSONResponse(
                {"error": f"Tuned model for {lang} not downloaded"}, 400
            )

        was_paused = _srv.captioning_paused
        try:
            _srv.captioning_paused = True
            await broadcast_all({"type": "captioning_paused", "paused": True})
            await asyncio.sleep(1.5)

            log.info(f"Hot-swapping to tuned model for {lang}: {model_path}")
            from faster_whisper import WhisperModel

            old_device = _srv.stt_backend._device
            old_compute = _srv.stt_backend._compute_type

            prev_hf = os.environ.get("HF_HUB_OFFLINE")
            os.environ["HF_HUB_OFFLINE"] = "1"
            try:
                new_model = WhisperModel(
                    str(model_path), device=old_device, compute_type=old_compute
                )
            finally:
                if prev_hf is None:
                    os.environ.pop("HF_HUB_OFFLINE", None)
                else:
                    os.environ["HF_HUB_OFFLINE"] = prev_hf
            with _model_lock:
                _srv.stt_backend._model = new_model
                _srv.stt_backend._model_name = f"tuned-{lang.lower()}"
            log.info(f"Model swapped to tuned-{lang.lower()} ({old_compute}, {old_device})")

            if not was_paused:
                _srv.captioning_paused = False
                await broadcast_all({"type": "captioning_paused", "paused": False})

            await broadcast_all({"type": "status", "model": _srv.stt_backend.name})
            return JSONResponse({"status": "ok", "model": _srv.stt_backend.name})

        except Exception as e:
            log.error(f"Hot-swap failed: {e}")
            if not was_paused:
                _srv.captioning_paused = False
                await broadcast_all({"type": "captioning_paused", "paused": False})
            return JSONResponse({"error": str(e)}, 500)

    @app.post("/api/tuned-models/revert")
    async def o_tuned_revert() -> JSONResponse:
        """Revert to the default Whisper model (large-v3-turbo)."""
        import server as _srv

        if not isinstance(_srv.stt_backend, WhisperBackend):
            return JSONResponse(
                {"error": "Model switching only works with Whisper backend"}, 400
            )

        was_paused = _srv.captioning_paused
        try:
            _srv.captioning_paused = True
            await broadcast_all({"type": "captioning_paused", "paused": True})
            await asyncio.sleep(1.5)

            log.info("Reverting to default Whisper model")
            from faster_whisper import WhisperModel

            old_device = _srv.stt_backend._device
            old_compute = _srv.stt_backend._compute_type
            default_model = "large-v3-turbo"

            local_path = MODELS_DIR / f"faster-whisper-{default_model}"
            model_id = str(local_path) if local_path.exists() else default_model

            prev_hf = os.environ.get("HF_HUB_OFFLINE")
            os.environ["HF_HUB_OFFLINE"] = "1"
            try:
                new_model = WhisperModel(
                    model_id, device=old_device, compute_type=old_compute
                )
            finally:
                if prev_hf is None:
                    os.environ.pop("HF_HUB_OFFLINE", None)
                else:
                    os.environ["HF_HUB_OFFLINE"] = prev_hf
            with _model_lock:
                _srv.stt_backend._model = new_model
                _srv.stt_backend._model_name = default_model
            log.info(f"Reverted to {default_model} ({old_compute}, {old_device})")

            if not was_paused:
                _srv.captioning_paused = False
                await broadcast_all({"type": "captioning_paused", "paused": False})

            await broadcast_all({"type": "status", "model": _srv.stt_backend.name})
            return JSONResponse({"status": "ok", "model": _srv.stt_backend.name})

        except Exception as e:
            log.error(f"Revert failed: {e}")
            if not was_paused:
                _srv.captioning_paused = False
                await broadcast_all({"type": "captioning_paused", "paused": False})
            return JSONResponse({"error": str(e)}, 500)

    # ── Offline Translation API ──

    @app.get("/api/offline-translate/status")
    async def o_offline_status() -> JSONResponse:
        """Get status of all offline translation models."""
        return JSONResponse(offline_translate.get_all_status(MODELS_DIR))

    @app.post("/api/offline-translate/download-opus")
    async def o_offline_download_opus(lang: str = Form(...)) -> JSONResponse:
        """Start downloading an OPUS-MT model for a language.

        Args:
            lang: Target language code.
        """
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

    @app.post("/api/offline-translate/download-m2m")
    async def o_offline_download_m2m() -> JSONResponse:
        """Start downloading M2M-100 1.2B model."""
        if offline_translate.is_m2m_available(str(MODELS_DIR)):
            return JSONResponse({"status": "already_available"})
        prog = offline_translate.get_progress("m2m100")
        if prog["status"] in ("downloading", "converting", "starting"):
            return JSONResponse({"status": "already_in_progress"})
        offline_translate.download_m2m_model(str(MODELS_DIR))
        return JSONResponse({"status": "started"})

    @app.get("/api/offline-translate/progress/{key}")
    async def o_offline_progress(key: str) -> JSONResponse:
        """Get download progress for a model.

        Args:
            key: Progress key (e.g. ``"opus-ES"``, ``"m2m100"``).
        """
        return JSONResponse(offline_translate.get_progress(key))

    @app.post("/api/offline-translate/reload")
    async def o_offline_reload() -> JSONResponse:
        """Clear cached translation models so they reload with current settings."""
        import server as _srv

        offline_translate.reload_models()
        cores = _srv.config.get("translate_cores", 0)
        effective = cores if cores > 0 else offline_translate.get_default_cores()
        return JSONResponse({"status": "reloaded", "intra_threads": effective})

    # ── Mic management ──

    @app.get("/api/mics")
    async def o_list_mics() -> JSONResponse:
        """List available microphones."""
        import server as _srv

        devs = sd.query_devices()
        default_idx = sd.default.device[0]
        mics: list[dict[str, Any]] = []
        for i, d in enumerate(devs):
            if d["max_input_channels"] > 0:
                mics.append({
                    "index": i,
                    "name": d["name"],
                    "is_default": i == default_idx,
                })
        return JSONResponse({"mics": mics, "current": _srv.current_mic_index})

    @app.post("/api/set-mic")
    async def o_set_mic(request: Request) -> JSONResponse:
        """Change the active microphone without restarting the server.

        Args:
            request: Request with form data containing ``mic_index``.
        """
        import server as _srv

        form = await request.form()
        raw = form.get("mic_index", "")
        try:
            new_idx = int(raw) if raw not in ("", "null", "None") else None
        except ValueError:
            return JSONResponse({"error": f"Invalid mic_index: {raw}"}, 400)
        if new_idx == _srv.current_mic_index:
            return JSONResponse({
                "status": "ok",
                "changed": False,
                "mic_index": _srv.current_mic_index,
            })
        _srv.current_mic_index = new_idx
        name = sd.query_devices(new_idx)["name"] if new_idx is not None else "default"
        log.info(f"Mic change requested: [{new_idx or 'default'}] {name}")
        src = get_source(0)
        if src:
            src.device_index = new_idx
            src.name = name
            src.restart_event.set()
        else:
            _srv.mic_restart_event.set()
        return JSONResponse({
            "status": "ok",
            "changed": True,
            "mic_index": _srv.current_mic_index,
            "mic_name": name,
        })

    # ── Source management ──

    @app.get("/api/sources")
    async def api_list_sources() -> JSONResponse:
        """List all active audio sources."""
        with _sources_lock:
            return JSONResponse([{
                "id": s.id, "name": s.name, "speaker": s.speaker,
                "color": s.color, "device_index": s.device_index,
            } for s in _sources])

    @app.post("/api/sources/add")
    async def api_add_source(request: Request) -> JSONResponse:
        """Add a new audio source at runtime.

        Args:
            request: Request with JSON body containing ``device_index`` and optional ``name``.
        """
        import server as _srv

        data = await request.json()
        dev_idx = data.get("device_index")
        name = data.get("name")
        if not name and dev_idx is not None:
            try:
                name = sd.query_devices(dev_idx)["name"]
            except Exception:
                log.debug(f"Could not look up device name for index {dev_idx}")
        src = add_source(dev_idx, name)
        if not src:
            return JSONResponse({"error": "Maximum 8 sources"}, status_code=400)
        t = threading.Thread(target=start_source_capture, args=(src,), daemon=True)
        t.start()
        src.capture_thread = t
        # Get the display app's event loop (used by audio processing threads)
        ev_loop = asyncio.get_event_loop()
        if _srv.stt_backend and hasattr(_srv.stt_backend, '_transcribe'):
            bt = threading.Thread(
                target=_buffer_audio_loop,
                args=(_srv.stt_backend._transcribe, ev_loop, src),
                daemon=True,
            )
            bt.start()
            src.buffer_thread = bt
        elif _srv.stt_backend and hasattr(_srv.stt_backend, '_vosk_source_loop'):
            bt = threading.Thread(
                target=_srv.stt_backend._vosk_source_loop,
                args=(ev_loop, src),
                daemon=True,
            )
            bt.start()
            src.buffer_thread = bt
        await broadcast_all({
            "type": "source_added",
            "source": {
                "id": src.id, "name": src.name,
                "speaker": src.speaker, "color": src.color,
            },
        })
        return JSONResponse({"id": src.id, "name": src.name})

    @app.post("/api/sources/remove")
    async def api_remove_source(request: Request) -> JSONResponse:
        """Remove an audio source at runtime.

        Args:
            request: Request with JSON body containing ``source_id``.
        """
        data = await request.json()
        source_id = data.get("source_id")
        if remove_source(source_id):
            await broadcast_all({"type": "source_removed", "source_id": source_id})
            return JSONResponse({"ok": True})
        return JSONResponse({"error": "Source not found"}, status_code=404)

    @app.post("/api/speakers/reset")
    async def api_reset_speakers() -> JSONResponse:
        """Reset all speaker names and colors to defaults."""
        import server as _srv

        with _sources_lock:
            for s in _sources:
                s.speaker = ""
                s.color = ""
        _srv._save_speaker_config()
        with _sources_lock:
            source_list = [
                {"id": s.id, "name": s.name, "speaker": s.speaker, "color": s.color}
                for s in _sources
            ]
        await broadcast_all({"type": "source_list", "sources": source_list})
        return JSONResponse({"ok": True})

    # ── Vosk models ──

    @app.get("/api/vosk-models")
    async def get_vosk_models() -> dict:
        """List available Vosk language models in the models directory."""
        models: list[dict[str, str]] = []
        for d in MODELS_DIR.glob("vosk-model-*"):
            if d.is_dir():
                name = d.name.lower()
                lang = None
                for pattern, code in VOSK_DIR_LANGS.items():
                    if f"-{pattern}" in name:
                        lang = code
                        break
                if lang:
                    models.append({"path": d.name, "lang": lang})
        return {"models": models}

    # ── Voice ID API ──

    @app.get("/api/voice-id/status")
    async def voice_id_status() -> dict:
        """Get voice ID system status."""
        import server as _srv

        return {
            "enabled": _srv.config.get("voice_id_enabled", True),
            "threshold": _srv.config.get("voice_id_threshold", 0.65),
            "enrolled": voice_id.registry.get_enrolled(),
            "model_available": voice_id.is_available(),
        }

    @app.post("/api/voice-id/toggle")
    async def voice_id_toggle(request: Request) -> dict:
        """Toggle voice ID enabled/disabled.

        Args:
            request: Request with JSON body containing ``enabled`` flag.
        """
        import server as _srv

        body = await request.json()
        if "enabled" not in body:
            raise HTTPException(status_code=400, detail="Missing 'enabled' field")
        _srv.config["voice_id_enabled"] = bool(body["enabled"])
        save_config(_srv.config)
        return {"enabled": _srv.config["voice_id_enabled"]}

    @app.post("/api/voice-id/threshold")
    async def voice_id_threshold(request: Request) -> dict:
        """Set voice ID similarity threshold.

        Args:
            request: Request with JSON body containing ``value`` (0.0-1.0).
        """
        import server as _srv

        body = await request.json()
        val = max(0.0, min(1.0, float(body.get("value", 0.65))))
        _srv.config["voice_id_threshold"] = val
        voice_id.registry.set_threshold(val)
        save_config(_srv.config)
        return {"threshold": val}

    @app.post("/api/voice-id/unenroll")
    async def voice_id_unenroll(request: Request) -> dict:
        """Remove a speaker from voice ID enrollment.

        Args:
            request: Request with JSON body containing ``name``.
        """
        body = await request.json()
        name = body.get("name", "")
        removed = voice_id.registry.unenroll(name)
        return {"removed": removed, "name": name}

    @app.post("/api/voice-id/clear")
    async def voice_id_clear() -> dict:
        """Clear all voice ID enrollments."""
        voice_id.registry.clear()
        return {"status": "cleared"}

    # ── Plugin Marketplace API ──

    @app.get("/api/plugins/registry")
    async def api_plugins_registry() -> dict:
        """Fetch full plugin registry from GitHub."""
        import server as _srv

        ev_loop = asyncio.get_running_loop()
        try:
            reg = _srv._get_registry()
            entries = await ev_loop.run_in_executor(None, reg.fetch_registry)
        except Exception as e:
            raise HTTPException(status_code=503, detail=f"Failed to fetch registry: {e}")
        installed = reg.get_installed()
        for entry in entries:
            entry["installed"] = entry["id"] in installed
            entry["installed_version"] = installed.get(entry["id"])
            entry["compatible"] = reg.is_compatible(entry)
        return {"plugins": entries, "cached": reg.is_cached()}

    @app.get("/api/plugins/updates")
    async def api_plugins_updates() -> dict:
        """Return available updates for installed plugins."""
        import server as _srv

        ev_loop = asyncio.get_running_loop()
        try:
            reg = _srv._get_registry()
            updates = await ev_loop.run_in_executor(None, reg.check_updates)
        except Exception as e:
            raise HTTPException(status_code=503, detail=f"Failed to check updates: {e}")
        return {"updates": updates}

    @app.post("/api/plugins/install/{plugin_id}")
    async def api_plugins_install(plugin_id: str) -> dict:
        """Download and install a plugin from the registry.

        Args:
            plugin_id: The plugin identifier to install.
        """
        import server as _srv

        ev_loop = asyncio.get_running_loop()
        reg = _srv._get_registry()

        if not reg.is_cached():
            try:
                await ev_loop.run_in_executor(None, reg.fetch_registry)
            except Exception as e:
                raise HTTPException(
                    status_code=503, detail=f"Cannot reach registry: {e}"
                )

        entry = reg.find_plugin(plugin_id)
        if entry is None:
            raise HTTPException(
                status_code=404,
                detail=f"Plugin '{plugin_id}' not found in registry",
            )
        if reg.is_installed(plugin_id):
            raise HTTPException(
                status_code=409,
                detail=f"Plugin '{plugin_id}' is already installed",
            )
        if not reg.is_compatible(entry):
            raise HTTPException(
                status_code=409,
                detail=f"Plugin '{plugin_id}' is not compatible with this version",
            )

        try:
            path = await ev_loop.run_in_executor(None, reg.install_plugin, plugin_id)
        except Exception as e:
            raise HTTPException(
                status_code=503, detail=f"Download/install failed: {e}"
            )
        return {"status": "installed", "path": str(path)}

    @app.post("/api/plugins/update/{plugin_id}")
    async def api_plugins_update(plugin_id: str) -> dict:
        """Update an installed plugin to latest version.

        Args:
            plugin_id: The plugin identifier to update.
        """
        import server as _srv

        ev_loop = asyncio.get_running_loop()
        reg = _srv._get_registry()

        if not reg.is_installed(plugin_id):
            raise HTTPException(
                status_code=404,
                detail=f"Plugin '{plugin_id}' is not installed",
            )

        try:
            path = await ev_loop.run_in_executor(None, reg.update_plugin, plugin_id)
        except Exception as e:
            raise HTTPException(
                status_code=503, detail=f"Update failed: {e}"
            )
        return {"status": "updated", "path": str(path)}

    @app.delete("/api/plugins/uninstall/{plugin_id}")
    async def api_plugins_uninstall(plugin_id: str) -> dict:
        """Uninstall a plugin.

        Args:
            plugin_id: The plugin identifier to uninstall.
        """
        import server as _srv

        ev_loop = asyncio.get_running_loop()
        reg = _srv._get_registry()

        if not reg.is_installed(plugin_id):
            raise HTTPException(
                status_code=404,
                detail=f"Plugin '{plugin_id}' is not installed",
            )

        try:
            await ev_loop.run_in_executor(None, reg.uninstall_plugin, plugin_id)
        except Exception as e:
            raise HTTPException(
                status_code=503, detail=f"Uninstall failed: {e}"
            )
        return {"status": "uninstalled"}

    # ── Display grids ──

    @app.get("/api/display-grids")
    async def get_display_grids() -> dict:
        """Get display grid layout config."""
        import server as _srv

        src = _srv.config.get("display_grids", {"main": {}, "extended": {}}) or {}
        return {
            "main": dict(src.get("main") or {}),
            "extended": dict(src.get("extended") or {}),
        }

    @app.post("/api/display-grids")
    async def set_display_grids(request: Request) -> dict:
        """Push grid layout for both displays.

        Body shape: ``{"main": {...}, "extended": {...}}`` or
        ``{"display": "main"|"extended", "grid": {...}}``.
        On update the new layout is broadcast to all display + extended WS clients.

        Args:
            request: Request with JSON body.
        """
        import server as _srv

        body = await request.json()

        with _config_lock:
            existing = _srv.config.get("display_grids", {"main": {}, "extended": {}}) or {}
            new_grids = {
                "main": dict(existing.get("main") or {}),
                "extended": dict(existing.get("extended") or {}),
            }
            if "main" in body or "extended" in body:
                if "main" in body and isinstance(body["main"], dict):
                    new_grids["main"] = body["main"]
                if "extended" in body and isinstance(body["extended"], dict):
                    new_grids["extended"] = body["extended"]
            elif "display" in body and "grid" in body:
                which = body["display"]
                if which in ("main", "extended") and isinstance(body["grid"], dict):
                    new_grids[which] = body["grid"]
            _srv.config["display_grids"] = new_grids
        save_config(_srv.config)

        main_msg = json.dumps({
            "type": "display_grid_change",
            "display": "main",
            "grid": new_grids.get("main", {}),
        })
        ext_msg = json.dumps({
            "type": "display_grid_change",
            "display": "extended",
            "grid": new_grids.get("extended", {}),
        })

        dead_d: set = set()
        for ws in list(display_clients):
            try:
                await ws.send_text(main_msg)
            except Exception:
                dead_d.add(ws)
        display_clients.difference_update(dead_d)

        dead_e: set = set()
        for ws in list(extended_clients):
            try:
                await ws.send_text(ext_msg)
            except Exception:
                dead_e.add(ws)
        extended_clients.difference_update(dead_e)

        return {"status": "ok", "display_grids": new_grids}

    # ── Operator WebSocket ──

    @app.websocket("/ws")
    async def o_ws(ws: WebSocket) -> None:
        """Operator WebSocket — handles speaker changes, pause/resume, etc."""
        import server as _srv

        await ws.accept()
        operator_clients.add(ws)
        await ws.send_text(json.dumps({
            "type": "status",
            "state": "connected",
            "model": _srv.stt_backend.name if _srv.stt_backend else "loading",
            "ui_language": _srv.config.get("ui_language", "EN"),
        }))
        with _sources_lock:
            source_list = [
                {"id": s.id, "name": s.name, "speaker": s.speaker, "color": s.color}
                for s in _sources
            ]
        await ws.send_json({"type": "source_list", "sources": source_list})
        try:
            while True:
                msg = json.loads(await ws.receive_text())
                if msg.get("type") == "set_threshold":
                    _srv.silence_threshold = float(msg.get("value", SILENCE_THRESHOLD))
                elif msg.get("type") == "set_speaker":
                    new_name = msg.get("speaker", "")
                    sp_color = msg.get("color", "")
                    source_id = msg.get("source_id")
                    change = {"name": new_name, "time": time.time(), "color": sp_color}
                    with _sources_lock:
                        for src in _sources:
                            if source_id is None or src.id == source_id:
                                with src.speaker_lock:
                                    src.speaker_change_pending = dict(change)
                                if new_name and _srv.config.get("voice_id_enabled", True):
                                    src.voice_id_enroll_pending = new_name
                    await broadcast_all({"type": "speaker_change", "speaker": new_name})
                    _srv._save_speaker_config()
                elif msg.get("type") == "clear_captions":
                    await broadcast_all({"type": "clear_captions"})
                elif msg.get("type") == "set_translation_paused":
                    _srv.translation_paused = bool(msg.get("paused", False))
                    log.info(
                        f"Translation {'PAUSED' if _srv.translation_paused else 'RESUMED'}"
                    )
                    await broadcast_all({
                        "type": "translation_paused",
                        "paused": _srv.translation_paused,
                    })
                elif msg.get("type") == "set_captioning_paused":
                    _srv.captioning_paused = bool(msg.get("paused", False))
                    log.info(
                        f"Captioning {'PAUSED' if _srv.captioning_paused else 'LIVE'}"
                    )
                    await broadcast_all({
                        "type": "captioning_paused",
                        "paused": _srv.captioning_paused,
                    })
                    if _srv.captioning_paused:
                        _srv.plugin_dispatcher.fire(
                            "on_session_stop", {"timestamp": time.time()}
                        )
                    else:
                        _srv.plugin_dispatcher.fire("on_session_start", {
                            "timestamp": time.time(),
                            "backend": _srv.stt_backend.name if _srv.stt_backend else "unknown",
                        })
                elif msg.get("type") == "set_save_transcripts":
                    _srv.save_transcripts = bool(msg.get("enabled", True))
                    log.info(
                        f"Transcript saving {'ON' if _srv.save_transcripts else 'OFF'}"
                    )
                    await broadcast_all({
                        "type": "save_transcripts",
                        "enabled": _srv.save_transcripts,
                    })
                elif msg.get("type") == "set_speaker_lang":
                    speaker = msg.get("speaker")
                    lang = msg.get("lang")
                    if speaker:
                        speaker_langs = _srv.config.get("speaker_langs", {})
                        if lang:
                            speaker_langs[speaker] = lang
                        else:
                            speaker_langs.pop(speaker, None)
                        _srv.config["speaker_langs"] = speaker_langs
                        save_config(_srv.config)
                elif msg.get("type") == "correct_caption":
                    lid = msg.get("line_id")
                    new_text = msg.get("text", "").strip()
                    if lid is not None and new_text:
                        original = None
                        with _line_id_lock:
                            for ln in _recent_lines:
                                if ln["id"] == lid:
                                    original = dict(ln)
                                    ln["text"] = new_text
                                    break
                        if original:
                            speaker = original["speaker"]
                            src_lang = original["src_lang"]
                            log.info(f"   CORRECTION [{lid}]: {new_text}")
                            await broadcast_all({
                                "type": "correct_line",
                                "line_id": lid,
                                "text": new_text,
                                "speaker": speaker,
                            })
                            prefix = f"{speaker}: " if speaker else ""
                            _save_line(src_lang, f"[corrected] {prefix}{new_text}")
                            ev_loop = asyncio.get_event_loop()
                            _translate_all(
                                new_text, "correct_translation", ev_loop,
                                line_id=lid, speaker_override=speaker,
                            )
        except WebSocketDisconnect:
            operator_clients.discard(ws)
        except Exception:
            operator_clients.discard(ws)
