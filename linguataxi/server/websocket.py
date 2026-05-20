"""WebSocket client tracking and message broadcast."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from fastapi import WebSocket

log: logging.Logger = logging.getLogger("livecaption")

# ── Client tracking sets ──

display_clients: set[WebSocket] = set()
extended_clients: set[WebSocket] = set()
operator_clients: set[WebSocket] = set()
dictation_clients: set[WebSocket] = set()


async def broadcast_all(msg: dict[str, Any]) -> None:
    """Broadcast a message to all display, extended, and operator clients.

    Dead connections are silently removed from the tracking sets.

    Args:
        msg: JSON-serialisable message dict.
    """
    data: str = json.dumps(msg)
    for cs in [display_clients, extended_clients, operator_clients]:
        dead: set[WebSocket] = set()
        for ws in list(cs):  # iterate over copy to avoid RuntimeError
            try:
                await ws.send_text(data)
            except Exception:
                dead.add(ws)
        cs.difference_update(dead)


async def broadcast_dictation(msg: dict[str, Any]) -> None:
    """Broadcast a message only to dictation clients.

    Dead connections are silently removed from the tracking set.

    Args:
        msg: JSON-serialisable message dict.
    """
    data: str = json.dumps(msg)
    dead: set[WebSocket] = set()
    for ws in list(dictation_clients):  # iterate over copy
        try:
            await ws.send_text(data)
        except Exception:
            dead.add(ws)
    dictation_clients.difference_update(dead)


def _bc(loop: asyncio.AbstractEventLoop, msg: dict[str, Any]) -> None:
    """Broadcast to appropriate clients based on mode.

    In dictation-only mode (captioning paused but dictation active),
    only send to dictation clients.  Dictation clients always use
    ``_dictation_loop`` to avoid cross-loop corruption.

    Args:
        loop: The main asyncio event loop.
        msg: JSON-serialisable message dict.
    """
    # Late import to access server globals that remain in server.py
    import server as _srv

    if _srv.captioning_paused and _srv.dictation_active:
        dl = _srv._dictation_loop or loop
        asyncio.run_coroutine_threadsafe(broadcast_dictation(msg), dl)
    else:
        asyncio.run_coroutine_threadsafe(broadcast_all(msg), loop)
        if dictation_clients and _srv._dictation_loop:
            asyncio.run_coroutine_threadsafe(broadcast_dictation(msg), _srv._dictation_loop)
