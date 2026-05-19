"""Display and extended display route handlers.

Routes for the audience-facing caption display (port 3000) and
the extended overflow display (port 3002).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from starlette.responses import Response

log: logging.Logger = logging.getLogger("livecaption")


def _render_display_html(display_target: str) -> str:
    """Render display.html with plugin assets injected.

    Args:
        display_target: Either ``"main"`` or ``"extended"``.

    Returns:
        The rendered HTML string.
    """
    import server as _srv

    html = (_srv.BASE_DIR / "display.html").read_text(encoding="utf-8")
    html = html.replace("<!-- DISPLAY_TARGET -->", display_target)
    html = html.replace("<!-- PLUGIN_CSS -->", _srv.plugin_dispatcher.get_css_links())
    html = html.replace("<!-- PLUGIN_PANELS -->", _srv.plugin_dispatcher.get_panel_html())
    html = html.replace("<!-- PLUGIN_JS -->", _srv.plugin_dispatcher.get_js_scripts())
    return html


def _style_config() -> dict[str, Any]:
    """Build the common style config dict for display clients.

    Returns:
        Dict of style configuration values.
    """
    import server as _srv
    from linguataxi.constants import DEEPL_SOURCE_LANGS

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
    from linguataxi.constants import FONT_OPTIONS

    for f in FONT_OPTIONS:
        if f["id"] == fid:
            return f["css"]
    return FONT_OPTIONS[0]["css"]


def _translations_for_slots(slot_start: int, slot_end: int) -> list[dict[str, Any]]:
    """Return translation configs for the given slot range.

    Args:
        slot_start: First slot index (inclusive).
        slot_end: Last slot index (inclusive).

    Returns:
        List of translation config dicts with lang, name, color, slot.
    """
    import server as _srv
    from linguataxi.constants import DEEPL_TARGET_LANGS

    all_t = _srv.config.get("translations", [])
    result: list[dict[str, Any]] = []
    for i in range(slot_start, min(slot_end + 1, len(all_t))):
        t = all_t[i]
        lang_name = DEEPL_TARGET_LANGS.get(t["lang"], t["lang"])
        result.append({
            "lang": t["lang"],
            "name": lang_name,
            "color": t.get("color", "#FFD54F"),
            "slot": i,
        })
    return result


def _snapshot_display_grids() -> dict[str, dict]:
    """Return a defensive copy of the display_grids config.

    Safe to serialize while another thread is mutating the live dict.

    Returns:
        Dict with ``"main"`` and ``"extended"`` grid configs.
    """
    import server as _srv

    src = _srv.config.get("display_grids", {"main": {}, "extended": {}}) or {}
    return {
        "main": dict(src.get("main") or {}),
        "extended": dict(src.get("extended") or {}),
    }


def register_display_routes(app: FastAPI, extended_app: FastAPI) -> None:
    """Register all display and extended display routes.

    Args:
        app: The display FastAPI application (port 3000).
        extended_app: The extended display FastAPI application (port 3002).
    """
    from linguataxi.settings import UPLOADS_DIR
    from linguataxi.server.websocket import display_clients, extended_clients
    from linguataxi.server.audio import _sources, _sources_lock

    # ── Display app routes (port 3000) ──

    @app.get("/")
    async def d_index() -> HTMLResponse:
        """Serve the main display page."""
        return HTMLResponse(_render_display_html("main"))

    @app.get("/bidirectional")
    async def bidirectional_page() -> FileResponse:
        """Serve the bidirectional captioning page."""
        import server as _srv
        return FileResponse(_srv.BASE_DIR / "bidirectional.html")

    @app.get("/uploads/{fn}")
    async def d_uploads(fn: str) -> Response:
        """Serve uploaded files (footer images, etc.).

        Args:
            fn: Filename to serve.
        """
        p = (UPLOADS_DIR / fn).resolve()
        if not str(p).startswith(str(UPLOADS_DIR.resolve())):
            return JSONResponse({"error": "invalid path"}, 400)
        return FileResponse(p) if p.exists() else JSONResponse({"error": "not found"}, 404)

    @app.get("/api/config")
    async def d_config() -> JSONResponse:
        """Return display config including translation slots 0-1."""
        import server as _srv

        sc = _style_config()
        sc["translations"] = _translations_for_slots(0, 1)
        sc["show_caption"] = True
        sc["font_css"] = _font_css(_srv.config.get("font_family", "atkinson"))
        sc["bidirectional_enabled"] = _srv.config.get("bidirectional_enabled", False)
        sc["bidirectional_langs"] = _srv.config.get("bidirectional_langs", [])
        sc["bidirectional_tuned_swap"] = _srv.config.get("bidirectional_tuned_swap", False)
        return JSONResponse(sc)

    @app.get("/api/locales/{lang}")
    async def d_get_locale(lang: str) -> JSONResponse:
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

    @app.websocket("/ws")
    async def d_ws(ws: WebSocket) -> None:
        """Display WebSocket — sends captions and config updates."""
        import server as _srv

        await ws.accept()
        display_clients.add(ws)
        await ws.send_text(json.dumps({
            "type": "status",
            "state": "connected",
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
                await ws.receive_text()
        except WebSocketDisconnect:
            display_clients.discard(ws)

    @app.get("/api/display-grids")
    async def d_get_grids() -> dict:
        """Return display grid layout config."""
        return _snapshot_display_grids()

    # ── Extended app routes (port 3002) ──

    @extended_app.get("/")
    async def e_index() -> HTMLResponse:
        """Serve the extended display page."""
        return HTMLResponse(_render_display_html("extended"))

    @extended_app.get("/uploads/{fn}")
    async def e_uploads(fn: str) -> Response:
        """Serve uploaded files on extended display.

        Args:
            fn: Filename to serve.
        """
        p = (UPLOADS_DIR / fn).resolve()
        if not str(p).startswith(str(UPLOADS_DIR.resolve())):
            return JSONResponse({"error": "invalid path"}, 400)
        return FileResponse(p) if p.exists() else JSONResponse({"error": "not found"}, 404)

    @extended_app.get("/api/config")
    async def e_config() -> JSONResponse:
        """Return extended display config including translation slots 2-4."""
        import server as _srv

        sc = _style_config()
        sc["translations"] = _translations_for_slots(2, 4)
        sc["show_caption"] = True
        sc["font_css"] = _font_css(_srv.config.get("font_family", "atkinson"))
        return JSONResponse(sc)

    @extended_app.websocket("/ws")
    async def e_ws(ws: WebSocket) -> None:
        """Extended display WebSocket — sends captions and config updates."""
        import server as _srv

        await ws.accept()
        extended_clients.add(ws)
        await ws.send_text(json.dumps({
            "type": "status",
            "state": "connected",
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
                await ws.receive_text()
        except WebSocketDisconnect:
            extended_clients.discard(ws)

    @extended_app.get("/api/display-grids")
    async def e_get_grids() -> dict:
        """Return display grid layout config for extended display."""
        return _snapshot_display_grids()
