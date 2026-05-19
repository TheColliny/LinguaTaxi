"""Dictation mode route handlers.

Routes for the dictation app (port 3005) — plain voice-to-text
without translation or multi-speaker features.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi import Form

from linguataxi.settings import TRANSCRIPTS_DIR, save_config
from linguataxi.server.websocket import dictation_clients, broadcast_dictation

log: logging.Logger = logging.getLogger("livecaption")


def register_dictation_routes(app: FastAPI) -> None:
    """Register all dictation app routes.

    Args:
        app: The dictation FastAPI application (port 3005).
    """

    @app.get("/")
    async def dict_index() -> FileResponse:
        """Serve the dictation HTML page."""
        import server as _srv
        return FileResponse(_srv.BASE_DIR / "dictation.html")

    @app.post("/api/shutdown")
    async def dict_shutdown() -> JSONResponse:
        """Graceful server shutdown via dictation API."""
        import server as _srv

        log.info("Shutdown requested via dictation API")
        threading.Thread(target=_srv._shutdown_and_exit, daemon=True).start()
        return JSONResponse({"status": "shutting_down"})

    @app.get("/api/dictation-config")
    async def dict_config() -> JSONResponse:
        """Return dictation-specific config (output directory)."""
        import server as _srv

        d = _srv.config.get("dictation_dir", str(TRANSCRIPTS_DIR))
        return JSONResponse({"dictation_dir": d})

    @app.get("/api/config")
    async def dict_main_config() -> JSONResponse:
        """Return main config for dictation page (ui_language, etc.)."""
        import server as _srv

        return JSONResponse({
            "ui_language": _srv.config.get("ui_language", "EN"),
            "session_title": _srv.config.get("session_title", ""),
        })

    @app.get("/api/locales/{lang}")
    async def dict_get_locale(lang: str) -> JSONResponse:
        """Serve translation JSON for a language.

        Args:
            lang: Language code (e.g. ``"en"``, ``"es"``).
        """
        import server as _srv

        locale_path = _srv.BASE_DIR / "locales" / f"{lang.lower()}.json"
        if locale_path.exists():
            return JSONResponse(json.loads(locale_path.read_text(encoding="utf-8")))
        en_path = _srv.BASE_DIR / "locales" / "en.json"
        if en_path.exists():
            return JSONResponse(json.loads(en_path.read_text(encoding="utf-8")))
        return JSONResponse({})

    @app.post("/api/dictation-config")
    async def dict_update_config(dictation_dir: str = Form(None)) -> JSONResponse:
        """Update dictation output directory.

        Args:
            dictation_dir: New directory path for dictation output files.
        """
        import server as _srv

        if dictation_dir is not None:
            p = Path(dictation_dir)
            try:
                p.mkdir(parents=True, exist_ok=True)
            except Exception as e:
                log.error(f"Failed to create dictation directory {p}: {e}")
                return JSONResponse({"error": str(e)}, 400)
            _srv.config["dictation_dir"] = str(p)
            save_config(_srv.config)
        return JSONResponse({
            "status": "ok",
            "dictation_dir": _srv.config.get("dictation_dir", str(TRANSCRIPTS_DIR)),
        })

    @app.post("/api/dictation-save")
    async def dict_save(
        text: str = Form(...),
        filename: str = Form(None),
    ) -> JSONResponse:
        """Save dictation text to a file.

        Args:
            text: The dictation text to save.
            filename: Optional filename (auto-generated if not provided).
        """
        import server as _srv

        d = Path(_srv.config.get("dictation_dir", str(TRANSCRIPTS_DIR)))
        d.mkdir(parents=True, exist_ok=True)
        if not filename:
            filename = f"dictation_{time.strftime('%Y%m%d_%H%M%S')}.txt"
        # Sanitize filename
        filename = "".join(
            c if c.isalnum() or c in ".-_ " else "" for c in filename
        ).strip()
        if not filename.endswith(".txt"):
            filename += ".txt"
        fp = d / filename
        with open(fp, "w", encoding="utf-8") as f:
            f.write(text)
        log.info(f"Dictation saved: {fp}")
        return JSONResponse({"status": "ok", "path": str(fp)})

    @app.post("/api/dictation-active")
    async def dict_set_active(req: Request) -> JSONResponse:
        """Set dictation active state via HTTP POST.

        Thread-safe alternative to setting state via WebSocket, added to
        fix a crash caused by WebSocket sends from non-event-loop threads.

        Args:
            req: The incoming request with JSON body ``{"active": bool}``.
        """
        import server as _srv

        try:
            body = await req.json()
            _srv.dictation_active = bool(body.get("active", False))
            log.info(
                f"Dictation {'ACTIVE' if _srv.dictation_active else 'STOPPED'} (via HTTP)"
            )
            await broadcast_dictation({
                "type": "dictation_active",
                "active": _srv.dictation_active,
            })
            return JSONResponse({"active": _srv.dictation_active})
        except Exception as e:
            log.error(f"dict_set_active failed: {e}", exc_info=True)
            return JSONResponse({"error": str(e)}, status_code=500)

    @app.websocket("/ws")
    async def dict_ws(ws: WebSocket) -> None:
        """Dictation WebSocket — sends interim/final text, receives commands."""
        import server as _srv

        await ws.accept()
        dictation_clients.add(ws)
        await ws.send_text(json.dumps({
            "type": "status",
            "state": "connected",
            "dictation_active": _srv.dictation_active,
        }))
        try:
            while True:
                msg = json.loads(await ws.receive_text())
                if msg.get("type") == "set_dictation_active":
                    _srv.dictation_active = bool(msg.get("active", False))
                    log.info(
                        f"Dictation {'ACTIVE' if _srv.dictation_active else 'STOPPED'}"
                    )
                    await broadcast_dictation({
                        "type": "dictation_active",
                        "active": _srv.dictation_active,
                    })
        except WebSocketDisconnect:
            dictation_clients.discard(ws)
            if not dictation_clients:
                _srv.dictation_active = False
        except Exception:
            dictation_clients.discard(ws)
            if not dictation_clients:
                _srv.dictation_active = False
