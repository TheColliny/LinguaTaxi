# App-Specific Audio Capture — Design Spec

**Goal:** Let users capture audio from specific applications (Zoom, Teams, etc.) as an audio source, solving the problem where Stereo Mix can't capture apps routing audio to Bluetooth/non-default devices.

**Status:** Approved design

---

## Overview

A new `app_audio.py` module provides a cross-platform abstraction for per-process audio capture. Platform-specific backends handle Windows (WASAPI per-process loopback), Linux (PulseAudio/PipeWire via `pulsectl`), and macOS (Core Audio process tap via Swift helper). App capture sources integrate into the existing `AudioSource` multi-source system — same queue, same processing pipeline, same WebSocket broadcast.

Both the launcher (tkinter) and operator panel (web) can add/remove device and app sources at runtime via server API, staying in sync through polling.

---

## Platform Backends

### Abstraction Layer (`app_audio.py`)

```
app_audio.py
├── AppInfo (dataclass): pid, name
├── AppAudioProvider (ABC):
│   ├── available() -> bool
│   ├── list_apps() -> list[AppInfo]
│   └── open_capture(pid) -> AppAudioStream
├── AppAudioStream (ABC):
│   ├── read() -> np.ndarray    # blocking, returns float32 mono 16kHz chunks
│   ├── stop()
│   └── sample_rate -> int
└── get_provider() -> AppAudioProvider | None
```

Platform selection via `sys.platform` at import time. Returns `None` on unsupported platforms.

Each backend's `open_capture(pid)` returns a stream that:
1. Opens per-process audio capture at the device's native sample rate.
2. Resamples internally to 16 kHz mono float32 (matching server's `SAMPLE_RATE`/`CHANNELS`/`DTYPE`).
3. `read()` returns chunks matching `CHUNK_DURATION` (0.5s = 8000 samples).

### Windows: WASAPI Per-Process Loopback

- **Min version:** Windows 10 build 20348 / Windows 11
- **Dependency:** `comtypes` (pure Python, pip-installable)
- **Capture:** `ActivateAudioInterfaceAsync` with `AUDIOCLIENT_ACTIVATION_PARAMS` set to `PROCESS_LOOPBACK` mode and target PID. Returns an `IAudioClient` capturing only that process's audio.
- **Buffer reads:** Event-driven via `WaitForSingleObject` on the audio client's event handle (~10ms latency per buffer, no busy-spinning).
- **App enumeration:** `IAudioSessionManager2` → `IAudioSessionEnumerator` lists active audio sessions with PID and display name.
- **Lifecycle:** If the target app stops producing audio, the stream returns silence. If the app exits, `read()` raises an exception handled by the capture loop.
- **COM threading:** The capture thread calls `CoInitializeEx` before any COM calls.

### Linux: PulseAudio/PipeWire

- **Min version:** Any distro with PulseAudio or PipeWire (PulseAudio compat layer)
- **Dependency:** `pulsectl` (pure Python, pip-installable, Linux only)
- **Capture flow:**
  1. Create a **combined sink** (`module-combine-sink`) that routes to both a null sink and the app's original output — so capture doesn't silence the user's speakers.
  2. Move the target app's sink input to the combined sink.
  3. Record from the null sink's monitor source.
- **App enumeration:** `pulsectl.Pulse().sink_input_list()` returns apps with `proplist['application.name']` and `proplist['application.process.id']`.
- **Cleanup (critical):** On `stop()` and in `atexit` handler:
  1. Move sink input back to its original sink.
  2. Unload the combined sink module.

### macOS: Core Audio Process Tap

- **Min version:** macOS 14.2+
- **Dependency:** None at runtime (compiled Swift binary bundled in .app)
- **Architecture:** A Swift helper binary (`linguataxi-audiotap`, ~300 lines, ~200KB universal binary) at `build/mac/audiotap/main.swift`.
- **Capture flow:**
  1. Python launches helper: `linguataxi-audiotap --pid <PID> --rate 16000 --format float32`
  2. Helper uses `CATapDescription` to tap the target process's audio.
  3. Audio streams back via stdout as raw PCM bytes.
  4. Helper handles resampling internally via `AVAudioConverter`.
- **App enumeration:** Helper supports `--list-apps` mode, outputs JSON to stdout.
- **Lifecycle:** Python sends newline to stdin for graceful shutdown. Broken pipe detection handles unexpected helper death. Helper exits when target app exits.
- **Build:** `build/mac/build.sh` compiles with `swiftc` and bundles into .app.

---

## Server Integration

### AudioSource Changes

```python
class AudioSource:
    def __init__(self, device_index=None, name=None, app_pid=None):
        # ... existing fields ...
        self.app_pid = app_pid          # None = device source, int = app capture
        self.app_stream = None          # AppAudioStream instance
```

### New Capture Function

```python
def start_app_capture(source):
    """Capture loop for app audio sources. Reads from AppAudioStream,
    queues chunks into source.queue — same as device sources."""
```

Calls `provider.open_capture(source.app_pid)`, loops `stream.read()` → `source.queue.put()`. The rest of the pipeline (`_buffer_audio_loop`, `_transcription_queue`, Whisper/Vosk, WebSocket broadcast) is untouched.

### New API Endpoints

| Endpoint | Method | Purpose |
|---|---|---|
| `GET /api/app-audio/available` | GET | Returns `{available: true/false}` |
| `GET /api/app-audio/list` | GET | Returns `[{pid, name}, ...]` from `provider.list_apps()` |
| `POST /api/sources/add-app` | POST | Creates AudioSource with `app_pid`, spawns `start_app_capture()` thread. Body: `{pid: int, name: str}` |

Existing endpoints work unchanged:
- `GET /api/sources` — lists all sources (device and app)
- `POST /api/sources/{id}/remove` — removes any source type

Server rejects duplicate sources (same PID or device_index already active) with a clear error.

---

## Dual-UI Source Management

The server is the single source of truth. Both UIs communicate via API.

### Shared Capabilities (Launcher + Operator Panel)

| Capability | API |
|---|---|
| List active sources | `GET /api/sources` (poll every 5s) |
| Add device source | `POST /api/sources/add` |
| Add app source | `POST /api/sources/add-app` |
| Remove source | `POST /api/sources/{id}/remove` |
| List available devices | `GET /api/mics` |
| List available apps | `GET /api/app-audio/list` (poll every 15s + manual refresh) |

### Launcher Changes

- Replace the startup-only mic dropdown with a live source management panel.
- On server start, launcher still passes initial sources via `--sources` CLI arg; after that, all changes go through the API.
- Source panel shows device and app sources in a unified list with "Add Device Source" and "Add App Source" buttons.
- App source list has manual "Refresh" button + 15s auto-refresh.

### Operator Panel Changes

- New "Audio Sources" collapsible section.
- Shows active sources with name, type badge (Device/App), and remove button.
- "Add Source" dropdown with "Devices" and "Applications" sections.
- Applications section only appears if `GET /api/app-audio/available` returns true.
- 15s auto-refresh + manual refresh button for app list.
- Max 8 sources enforced — controls disable at limit.

---

## Dependencies

| Package | Platform | Purpose |
|---|---|---|
| `comtypes` | Windows | COM interface calls for WASAPI |
| `pulsectl` | Linux | PulseAudio/PipeWire control |
| (none) | macOS | Swift helper binary, no pip dependency |

Added to `requirements.txt` with platform markers:
```
comtypes>=1.2.0,<2.0; sys_platform == "win32"
pulsectl>=23.5.0,<24.0; sys_platform == "linux"
```

**Note:** Adding dependencies means a full build is required, not a patch build.

---

## Latency Impact

App capture adds ~10ms to the pipeline (event-driven buffer reads). The existing pipeline latency (0.5–8s segment accumulation + 0.5–2s Whisper inference) dominates. No perceptible difference from device sources.
