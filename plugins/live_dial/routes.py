"""
LinguaTaxi — Live Dial Testing Plugin Routes

Audience members scan a QR code, get a simple slider page on their phone,
and vote approve/disapprove in real-time. The operator sees a live sentiment
graph in the panel.

Architecture:
  - GET  /audience-dial          — full mobile page (standalone HTML)
  - WS   /api/dial/audience-ws   — audience WebSocket (sends slider values)
  - WS   /api/dial/operator-ws   — operator WebSocket (receives aggregated data)
  - POST /api/dial/tunnel/start  — start SSH tunnel for public access
  - POST /api/dial/tunnel/stop   — stop SSH tunnel
  - GET  /api/dial/status        — tunnel URL, audience count, current data
  - POST /api/dial/speaker       — set current speaker name
  - POST /api/dial/reset         — reset all data for new session
"""

import asyncio
import json
import logging
import re
import subprocess
import threading
import time
from pathlib import Path
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import HTMLResponse, JSONResponse

log = logging.getLogger("livecaption")

router = APIRouter(prefix="/api")

# ── Plugin settings ──
_plugin_settings = {}

# ── State ──
_audience_ws: list[WebSocket] = []       # connected audience members
_operator_ws: list[WebSocket] = []       # connected operator panels
_audience_lock = threading.Lock()
_operator_lock = threading.Lock()

_current_speaker = ""
_dial_values: dict[str, int] = {}        # ws_id -> last slider value (0-100)
_dial_values_lock = threading.Lock()
_history: list[dict] = []                # [{time, avg, count, speaker}, ...]
_MAX_HISTORY = 3600                      # ~1 hour at 1 sample/sec
_session_start = time.monotonic()

# ── Tunnel state ──
_tunnel_proc = None
_tunnel_url = ""
_tunnel_lock = threading.Lock()
_tunnel_loop = None  # asyncio event loop reference for cross-thread broadcasts


def _get_tunnel_service():
    s = _plugin_settings.get("tunnel_service", "serveo").strip().lower()
    return s if s in ("serveo", "localhost.run", "none") else "serveo"


# ═══════════════════════════════════════════════════════════════════════════
# Audience page (standalone full HTML served at /audience-dial)
# ═══════════════════════════════════════════════════════════════════════════

_AUDIENCE_HTML_PATH = Path(__file__).parent / "audience.html"


@router.get("/dial/audience", response_class=HTMLResponse)
async def audience_page():
    """Serve the audience dial page (standalone HTML)."""
    if _AUDIENCE_HTML_PATH.exists():
        return HTMLResponse(_AUDIENCE_HTML_PATH.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>Audience page not found</h1>", status_code=404)


# ═══════════════════════════════════════════════════════════════════════════
# WebSocket: Audience connections
# ═══════════════════════════════════════════════════════════════════════════

@router.websocket("/dial/audience-ws")
async def audience_websocket(ws: WebSocket):
    await ws.accept()
    ws_id = f"a-{id(ws)}-{time.monotonic_ns()}"

    with _audience_lock:
        _audience_ws.append(ws)

    # Ensure aggregation loop is running
    _ensure_aggregation_loop()

    # Send current speaker
    try:
        await ws.send_json({"type": "speaker", "name": _current_speaker})
    except (WebSocketDisconnect, RuntimeError, ConnectionError):
        pass

    # Broadcast updated count to operators
    await _broadcast_count()

    try:
        while True:
            data = await ws.receive_json()
            if data.get("type") == "dial":
                try:
                    val = max(0, min(100, int(data.get("value", 50))))
                except (ValueError, TypeError):
                    continue  # skip malformed message, don't disconnect
                with _dial_values_lock:
                    _dial_values[ws_id] = val
    except (WebSocketDisconnect, RuntimeError, ConnectionError, ValueError):
        pass
    finally:
        with _audience_lock:
            if ws in _audience_ws:
                _audience_ws.remove(ws)
        with _dial_values_lock:
            _dial_values.pop(ws_id, None)
        await _broadcast_count()


# ═══════════════════════════════════════════════════════════════════════════
# WebSocket: Operator connections
# ═══════════════════════════════════════════════════════════════════════════

@router.websocket("/dial/operator-ws")
async def operator_websocket(ws: WebSocket):
    await ws.accept()
    with _operator_lock:
        _operator_ws.append(ws)

    # Ensure aggregation loop is running
    _ensure_aggregation_loop()

    # Send current state
    try:
        await ws.send_json({
            "type": "init",
            "speaker": _current_speaker,
            "audience_count": len(_audience_ws),
            "tunnel_url": _tunnel_url,
            "history": _history[-300:],  # last 5 minutes
        })
    except (WebSocketDisconnect, RuntimeError, ConnectionError):
        pass

    try:
        while True:
            await ws.receive_text()  # keep alive
    except (WebSocketDisconnect, RuntimeError, ConnectionError):
        pass
    finally:
        with _operator_lock:
            if ws in _operator_ws:
                _operator_ws.remove(ws)


# ═══════════════════════════════════════════════════════════════════════════
# Aggregation loop (runs in background)
# ═══════════════════════════════════════════════════════════════════════════

_agg_task = None


def _ensure_aggregation_loop():
    """Start the aggregation loop if not already running. Safe to call repeatedly."""
    global _agg_task
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    if _agg_task is None or _agg_task.done():
        _agg_task = loop.create_task(_aggregation_loop())


async def _aggregation_loop():
    """Every second, compute average dial value and broadcast to operators.

    Wrapped in supervisor: if the inner loop crashes, log and restart.
    """
    while True:
        try:
            while True:
                await asyncio.sleep(1)
                with _dial_values_lock:
                    values = list(_dial_values.values())
                count = len(values)
                avg = round(sum(values) / count, 1) if count > 0 else 50.0
                elapsed = round(time.monotonic() - _session_start, 1)

                sample = {
                    "time": elapsed,
                    "avg": avg,
                    "count": count,
                    "speaker": _current_speaker,
                }
                _history.append(sample)
                if len(_history) > _MAX_HISTORY:
                    _history.pop(0)

                msg = json.dumps({"type": "sample", **sample})
                await _broadcast_to_operators(msg)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.error(f"Live Dial: aggregation loop crashed: {e}; restarting in 1s")
            await asyncio.sleep(1)


async def _broadcast_to_operators(msg: str):
    # Snapshot the ws list under the lock, send OUTSIDE the lock
    with _operator_lock:
        wss = list(_operator_ws)
    for ws in wss:
        try:
            await ws.send_text(msg)
        except (WebSocketDisconnect, RuntimeError, ConnectionError):
            with _operator_lock:
                if ws in _operator_ws:
                    _operator_ws.remove(ws)


async def _broadcast_count():
    count = len(_audience_ws)
    msg = json.dumps({"type": "count", "audience_count": count})
    await _broadcast_to_operators(msg)


async def _broadcast_speaker():
    msg = json.dumps({"type": "speaker", "name": _current_speaker})
    # To operators
    await _broadcast_to_operators(msg)
    # To audience — snapshot then send outside lock
    with _audience_lock:
        wss = list(_audience_ws)
    for ws in wss:
        try:
            await ws.send_text(msg)
        except (WebSocketDisconnect, RuntimeError, ConnectionError):
            with _audience_lock:
                if ws in _audience_ws:
                    _audience_ws.remove(ws)


# ═══════════════════════════════════════════════════════════════════════════
# Tunnel management
# ═══════════════════════════════════════════════════════════════════════════

def _start_tunnel(port: int):
    """Start an SSH tunnel in a background thread. Sets _tunnel_url when ready."""
    global _tunnel_proc, _tunnel_url
    service = _get_tunnel_service()

    if service == "none":
        return

    with _tunnel_lock:
        if _tunnel_proc and _tunnel_proc.poll() is None:
            return  # already running

    def _run():
        global _tunnel_proc, _tunnel_url
        if service == "serveo":
            cmd = ["ssh", "-o", "StrictHostKeyChecking=no", "-o", "ServerAliveInterval=30",
                   "-R", f"80:localhost:{port}", "serveo.net"]
            url_re = re.compile(r'https?://\S+\.serveo\.net')
        else:  # localhost.run
            cmd = ["ssh", "-o", "StrictHostKeyChecking=no", "-o", "ServerAliveInterval=30",
                   "-R", f"80:localhost:{port}", "localhost.run"]
            url_re = re.compile(r'https?://\S+\.localhost\.run')

        log.info(f"Live Dial: starting tunnel via {service} on port {port}")
        proc = None
        try:
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1,
            )
            with _tunnel_lock:
                _tunnel_proc = proc

            for line in proc.stdout:
                line = line.strip()
                log.debug(f"Tunnel: {line}")
                match = url_re.search(line)
                if match:
                    with _tunnel_lock:
                        # Only update if we're still the active proc
                        if _tunnel_proc is proc:
                            _tunnel_url = match.group(0)
                            log.info(f"Live Dial: tunnel active at {_tunnel_url}")
                    break

            # Keep reading to keep the tunnel alive
            if proc.stdout:
                for _ in proc.stdout:
                    pass

        except FileNotFoundError:
            log.error("Live Dial: 'ssh' command not found. Install OpenSSH to use tunnels.")
            with _tunnel_lock:
                if _tunnel_proc is proc:
                    _tunnel_url = ""
            return
        except Exception as e:
            log.error(f"Live Dial: tunnel error: {e}")
            with _tunnel_lock:
                if _tunnel_proc is proc:
                    _tunnel_url = ""
            return

        # Stdout drain completed → tunnel died. Detect + broadcast.
        with _tunnel_lock:
            still_active = _tunnel_proc is proc
            if still_active:
                _tunnel_url = ""
                _tunnel_proc = None
        if still_active:
            log.warning("Live Dial: tunnel closed (stdout ended)")
            # Broadcast tunnel_down to operators from this thread
            loop_ref = _tunnel_loop
            if loop_ref is not None and not loop_ref.is_closed():
                try:
                    msg = json.dumps({"type": "tunnel_down"})
                    asyncio.run_coroutine_threadsafe(_broadcast_to_operators(msg), loop_ref)
                except Exception as e:
                    log.error(f"Live Dial: failed to broadcast tunnel_down: {e}")

    thread = threading.Thread(target=_run, daemon=True, name="dial-tunnel")
    thread.start()


def _stop_tunnel():
    global _tunnel_proc, _tunnel_url
    with _tunnel_lock:
        proc = _tunnel_proc
        # Clear _tunnel_proc immediately so stale threads' updates are ignored
        _tunnel_proc = None
        _tunnel_url = ""
    if proc:
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
    log.info("Live Dial: tunnel stopped")


# ═══════════════════════════════════════════════════════════════════════════
# REST routes
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/dial/status")
async def dial_status():
    # Ensure aggregation loop is running
    _ensure_aggregation_loop()

    with _dial_values_lock:
        values = list(_dial_values.values())
    avg = round(sum(values) / len(values), 1) if values else 50.0
    return {
        "status": "ok",
        "audience_count": len(_audience_ws),
        "current_avg": avg,
        "speaker": _current_speaker,
        "tunnel_url": _tunnel_url,
        "tunnel_service": _get_tunnel_service(),
        "history_length": len(_history),
    }


@router.post("/dial/tunnel/start")
async def start_tunnel(request: Request):
    """Start the SSH tunnel for public audience access."""
    global _tunnel_loop
    # Capture event loop reference for cross-thread broadcasts (tunnel_down)
    try:
        _tunnel_loop = asyncio.get_running_loop()
    except RuntimeError:
        _tunnel_loop = None
    # Determine the operator port (the app this router is mounted on)
    port = request.url.port or 3001
    _start_tunnel(port)
    # Wait briefly for URL to become available
    for _ in range(20):
        if _tunnel_url:
            break
        await asyncio.sleep(0.5)
    return {"tunnel_url": _tunnel_url, "status": "started" if _tunnel_url else "starting"}


@router.post("/dial/tunnel/stop")
async def stop_tunnel():
    _stop_tunnel()
    return {"status": "stopped"}


@router.post("/dial/speaker")
async def set_speaker(request: Request):
    global _current_speaker
    body = await request.json()
    _current_speaker = str(body.get("name", "")).strip()
    await _broadcast_speaker()
    return {"speaker": _current_speaker}


@router.post("/dial/reset")
async def reset_session():
    global _current_speaker, _session_start
    _current_speaker = ""
    with _dial_values_lock:
        _dial_values.clear()
    _history.clear()
    _session_start = time.monotonic()
    await _broadcast_speaker()
    await _broadcast_to_operators(json.dumps({"type": "reset"}))
    return {"status": "reset"}


def handle_event(event_name, data, settings):
    global _plugin_settings
    if event_name == "on_config_change":
        _plugin_settings = settings
    elif event_name == "on_shutdown":
        _stop_tunnel()
