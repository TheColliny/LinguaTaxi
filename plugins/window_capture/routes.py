"""Window Capture Plugin — relays H.264/VP8 video chunks to display clients.

Operator sends a JSON init message (MIME type), then binary MediaRecorder
chunks. Both are relayed to all connected display and extended clients.
"""

import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

log = logging.getLogger("livecaption")

router = APIRouter(prefix="/api")

_capturing: bool = False


@router.get("/window-capture/status")
async def window_capture_status():
    return {"status": "ok", "capturing": _capturing}


@router.websocket("/window-capture/ws")
async def window_capture_ws(ws: WebSocket):
    """Receive video chunks from operator and relay to display clients."""
    from linguataxi.server.websocket import display_clients, extended_clients

    global _capturing
    await ws.accept()
    _capturing = True
    log.info("Window capture: operator connected, streaming started")
    try:
        while True:
            msg = await ws.receive()
            if msg.get("text") is not None:
                text = msg["text"]
                for clients in [display_clients, extended_clients]:
                    dead: set = set()
                    for c in list(clients):
                        try:
                            await c.send_text(text)
                        except Exception:
                            dead.add(c)
                    clients.difference_update(dead)
            elif msg.get("bytes") is not None:
                data = msg["bytes"]
                for clients in [display_clients, extended_clients]:
                    dead: set = set()
                    for c in list(clients):
                        try:
                            await c.send_bytes(data)
                        except Exception:
                            dead.add(c)
                    clients.difference_update(dead)
    except WebSocketDisconnect:
        pass
    except Exception as e:
        log.warning("Window capture: stream error: %s", e)
    finally:
        _capturing = False
        log.info("Window capture: streaming stopped")


def handle_event(event_name, data, settings):
    pass
