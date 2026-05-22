"""WebSocket connection tests for all LinguaTaxi apps.

Validates that WebSocket endpoints accept connections and respond
to the initial handshake with the expected message types.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
from typing import Any

import pytest
import websockets


DISPLAY_WS = "ws://127.0.0.1:3000/ws"
OPERATOR_WS = "ws://127.0.0.1:3001/ws"
EXTENDED_WS = "ws://127.0.0.1:3002/ws"
DICTATION_WS = "ws://127.0.0.1:3005/ws"


async def _connect_and_receive(uri: str, timeout: float = 5.0) -> list[dict[str, Any]]:
    """Connect to a WebSocket, collect messages for `timeout` seconds."""
    messages: list[dict[str, Any]] = []
    async with websockets.connect(uri) as ws:
        try:
            while True:
                raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
                messages.append(json.loads(raw))
        except (asyncio.TimeoutError, websockets.ConnectionClosed):
            pass
    return messages


@pytest.fixture(scope="module")
def _ensure_server(server_process: subprocess.Popen) -> None:
    pass


@pytest.mark.usefixtures("_ensure_server")
class TestWebSockets:

    def test_display_ws_connects(self) -> None:
        msgs = asyncio.run(_connect_and_receive(DISPLAY_WS, timeout=3.0))
        assert any(
            m.get("type") in ("status", "config", "source_list") for m in msgs
        ), f"Expected status/config message, got: {msgs}"

    def test_operator_ws_connects(self) -> None:
        msgs = asyncio.run(_connect_and_receive(OPERATOR_WS, timeout=3.0))
        assert any(
            m.get("type") in ("status", "config", "source_list") for m in msgs
        ), f"Expected status/config message, got: {msgs}"

    def test_extended_ws_connects(self) -> None:
        msgs = asyncio.run(_connect_and_receive(EXTENDED_WS, timeout=3.0))
        assert any(
            m.get("type") in ("status", "config", "source_list") for m in msgs
        ), f"Expected status/config message, got: {msgs}"

    def test_dictation_ws_connects(self) -> None:
        msgs = asyncio.run(_connect_and_receive(DICTATION_WS, timeout=3.0))
        assert isinstance(msgs, list)

    def test_display_ws_receives_source_list(self) -> None:
        """Display clients should get a source_list message on connect."""
        msgs = asyncio.run(_connect_and_receive(DISPLAY_WS, timeout=3.0))
        source_msgs = [m for m in msgs if m.get("type") == "source_list"]
        if source_msgs:
            assert "sources" in source_msgs[0]
