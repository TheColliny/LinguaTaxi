# App-Specific Audio Capture — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let users capture audio from specific applications (Zoom, Teams, etc.) as an audio source on Windows, Linux, and macOS.

**Architecture:** A cross-platform `app_audio.py` module with `AppAudioProvider` / `AppAudioStream` ABCs and three backends (WASAPI, PulseAudio, Core Audio). Server gets new API endpoints and an `app_pid` field on `AudioSource`. Both launcher and operator panel manage sources via API.

**Tech Stack:** Python `comtypes` (Windows COM), `pulsectl` (Linux PulseAudio), Swift `CATapDescription` (macOS), FastAPI endpoints, tkinter, vanilla JS

---

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `app_audio.py` | Create | Cross-platform abstraction: ABCs + Windows WASAPI backend |
| `app_audio_pulse.py` | Create | Linux PulseAudio/PipeWire backend |
| `build/mac/audiotap/main.swift` | Create | macOS Core Audio process tap Swift helper |
| `app_audio_coreaudio.py` | Create | macOS backend (launches Swift helper subprocess) |
| `server.py` | Modify | `AudioSource.app_pid`, `start_app_capture()`, new API endpoints |
| `operator.html` | Modify | Audio Sources collapsible section with device + app source management |
| `launcher.pyw` | Modify | Live source management via API (replaces startup-only dropdown) |
| `requirements.txt` | Modify | Add `comtypes` (Windows) and `pulsectl` (Linux) |
| `build/mac/build.sh` | Modify | Compile Swift helper and bundle in .app |

---

### Task 1: Create the abstraction layer and Windows WASAPI backend

**Files:**
- Create: `app_audio.py`
- Modify: `requirements.txt`

This is the largest task — it contains the core ABCs and the full Windows per-process loopback implementation.

- [ ] **Step 1: Add `comtypes` to requirements.txt**

In `requirements.txt`, add after the existing `websocket-client` line:

```
comtypes>=1.2.0,<2.0; sys_platform == "win32"
```

- [ ] **Step 2: Create `app_audio.py` with ABCs and Windows backend**

Create `app_audio.py` at the project root:

```python
"""Cross-platform per-process audio capture.

Provides AppAudioProvider / AppAudioStream ABCs and a factory function
get_provider() that returns the correct platform backend, or None if
app audio capture is not supported on this OS/version.

Windows:  WASAPI per-process loopback (Win10 build 20348+)
Linux:    PulseAudio/PipeWire via pulsectl  (see app_audio_pulse.py)
macOS:    Core Audio process tap via Swift helper (see app_audio_coreaudio.py)
"""

from __future__ import annotations
import sys
import dataclasses
from abc import ABC, abstractmethod
import numpy as np

SAMPLE_RATE = 16000
CHANNELS = 1
CHUNK_DURATION = 0.5
CHUNK_SAMPLES = int(SAMPLE_RATE * CHUNK_DURATION)


@dataclasses.dataclass
class AppInfo:
    pid: int
    name: str


class AppAudioStream(ABC):
    @property
    @abstractmethod
    def sample_rate(self) -> int: ...

    @abstractmethod
    def read(self) -> np.ndarray:
        """Blocking read. Returns float32 mono 16 kHz array of CHUNK_SAMPLES."""
        ...

    @abstractmethod
    def stop(self) -> None: ...


class AppAudioProvider(ABC):
    @abstractmethod
    def available(self) -> bool: ...

    @abstractmethod
    def list_apps(self) -> list[AppInfo]: ...

    @abstractmethod
    def open_capture(self, pid: int) -> AppAudioStream: ...


def get_provider() -> AppAudioProvider | None:
    """Return the platform-appropriate provider, or None if unsupported."""
    if sys.platform == "win32":
        try:
            p = _WasapiLoopbackProvider()
            if p.available():
                return p
        except Exception:
            pass
    elif sys.platform == "linux":
        try:
            from app_audio_pulse import PulseAudioProvider
            p = PulseAudioProvider()
            if p.available():
                return p
        except Exception:
            pass
    elif sys.platform == "darwin":
        try:
            from app_audio_coreaudio import CoreAudioTapProvider
            p = CoreAudioTapProvider()
            if p.available():
                return p
        except Exception:
            pass
    return None


# ══════════════════════════════════════════════
# WINDOWS: WASAPI Per-Process Loopback
# ══════════════════════════════════════════════

if sys.platform == "win32":
    import ctypes
    import ctypes.wintypes as wt
    import threading
    import logging

    log = logging.getLogger("livecaption.app_audio")

    try:
        import comtypes
        from comtypes import GUID, HRESULT, COMMETHOD, IUnknown
        from comtypes import CoClass  # noqa: F401
        _HAS_COMTYPES = True
    except ImportError:
        _HAS_COMTYPES = False

    if _HAS_COMTYPES:

        # ── COM GUIDs ──
        CLSID_MMDeviceEnumerator = GUID("{BCDE0395-E52F-467C-8E3D-C4579291692E}")
        IID_IMMDeviceEnumerator = GUID("{A95664D2-9614-4F35-A746-DE8DB63617E6}")
        IID_IAudioSessionManager2 = GUID("{77AA99A0-1BD6-484F-8BC7-2C654C9A9B6F}")
        IID_IAudioClient = GUID("{1CB9AD4C-DBFA-4c32-B178-C2F568A703B2}")
        IID_IAudioCaptureClient = GUID("{C8ADBD64-E71E-48a0-A4DE-185C395CD317}")

        AUDCLNT_STREAMFLAGS_LOOPBACK = 0x00020000
        AUDCLNT_STREAMFLAGS_EVENTCALLBACK = 0x00040000
        AUDCLNT_SHAREMODE_SHARED = 0

        VIRTUAL_AUDIO_DEVICE_PROCESS_LOOPBACK = GUID(
            "{8F18E3EC-0197-4315-A064-3A7192FB35A8}")
        PROCESS_LOOPBACK_MODE_INCLUDE_TARGET_PROCESS_TREE = 0

        WAVE_FORMAT_IEEE_FLOAT = 0x0003
        WAVE_FORMAT_EXTENSIBLE = 0xFFFE
        KSDATAFORMAT_SUBTYPE_IEEE_FLOAT = GUID(
            "{00000003-0000-0010-8000-00AA00389B71}")

        WAIT_OBJECT_0 = 0
        INFINITE = 0xFFFFFFFF

        # ── Minimal COM structures ──

        class WAVEFORMATEX(ctypes.Structure):
            _fields_ = [
                ("wFormatTag", ctypes.c_ushort),
                ("nChannels", ctypes.c_ushort),
                ("nSamplesPerSec", ctypes.c_uint),
                ("nAvgBytesPerSec", ctypes.c_uint),
                ("nBlockAlign", ctypes.c_ushort),
                ("wBitsPerSample", ctypes.c_ushort),
                ("cbSize", ctypes.c_ushort),
            ]

        class WAVEFORMATEXTENSIBLE(ctypes.Structure):
            _fields_ = [
                ("Format", WAVEFORMATEX),
                ("Samples", ctypes.c_ushort),
                ("dwChannelMask", ctypes.c_uint),
                ("SubFormat", GUID),
            ]

        class AUDIOCLIENT_ACTIVATION_PARAMS(ctypes.Structure):
            _fields_ = [
                ("ActivationType", ctypes.c_uint),
                ("u_ProcessLoopbackParams_ProcessId", ctypes.c_uint),
                ("u_ProcessLoopbackParams_Mode", ctypes.c_uint),
            ]

        class PROPVARIANT(ctypes.Structure):
            _fields_ = [
                ("vt", ctypes.c_ushort),
                ("wReserved1", ctypes.c_ushort),
                ("wReserved2", ctypes.c_ushort),
                ("wReserved3", ctypes.c_ushort),
                ("punkVal", ctypes.POINTER(IUnknown)),
            ]

        # Callback interface for ActivateAudioInterfaceAsync
        class IActivateAudioInterfaceCompletionHandler(IUnknown):
            _iid_ = GUID("{41D949AB-9862-444A-80F6-C261334DA5EB}")

        class IActivateAudioInterfaceAsyncOperation(IUnknown):
            _iid_ = GUID("{72A22D78-CDE4-431D-B8CC-843A71199B6D}")
            _methods_ = [
                COMMETHOD([], HRESULT, "GetActivateResult",
                          (["out"], ctypes.POINTER(HRESULT), "activateResult"),
                          (["out"], ctypes.POINTER(ctypes.POINTER(IUnknown)), "activatedInterface")),
            ]

        # ── Activation helpers ──

        _ole32 = ctypes.windll.ole32
        _kernel32 = ctypes.windll.kernel32
        _mmdevapi = None

        def _activate_audio_interface_async(device_id, iid, activation_params, handler):
            """Call ActivateAudioInterfaceAsync from mmdevapi.dll."""
            global _mmdevapi
            if _mmdevapi is None:
                _mmdevapi = ctypes.windll.LoadLibrary("mmdevapi.dll")
            func = _mmdevapi.ActivateAudioInterfaceAsync
            func.restype = HRESULT
            op = ctypes.POINTER(IActivateAudioInterfaceAsyncOperation)()
            hr = func(
                ctypes.c_wchar_p(device_id),
                ctypes.byref(iid),
                ctypes.byref(activation_params) if activation_params else None,
                handler,
                ctypes.byref(op))
            if hr != 0:
                raise OSError(f"ActivateAudioInterfaceAsync failed: 0x{hr:08X}")
            return op

        class CompletionHandler(comtypes.COMObject):
            _com_interfaces_ = [IActivateAudioInterfaceCompletionHandler]
            def __init__(self):
                super().__init__()
                self.event = threading.Event()
                self.operation = None
            def ActivateCompleted(self, operation):
                self.operation = operation
                self.event.set()
                return 0

        def _open_process_loopback(pid: int):
            """Open a WASAPI loopback audio client for a specific process."""
            params = AUDIOCLIENT_ACTIVATION_PARAMS()
            params.ActivationType = 1  # PROCESS_LOOPBACK
            params.u_ProcessLoopbackParams_ProcessId = pid
            params.u_ProcessLoopbackParams_Mode = (
                PROCESS_LOOPBACK_MODE_INCLUDE_TARGET_PROCESS_TREE)

            prop = PROPVARIANT()
            prop.vt = 13  # VT_UNKNOWN — actually we need VT_BLOB
            # Pack activation params as a blob in PROPVARIANT
            prop.vt = 0x1011  # VT_VECTOR | VT_UI1
            blob_size = ctypes.sizeof(params)

            handler = CompletionHandler()

            op = _activate_audio_interface_async(
                str(VIRTUAL_AUDIO_DEVICE_PROCESS_LOOPBACK),
                IID_IAudioClient,
                params,
                handler)

            handler.event.wait(timeout=5.0)
            if not handler.event.is_set():
                raise TimeoutError("Audio activation timed out")

            activate_hr = HRESULT()
            activated = ctypes.POINTER(IUnknown)()
            handler.operation.GetActivateResult(
                ctypes.byref(activate_hr), ctypes.byref(activated))

            if activate_hr.value != 0:
                raise OSError(
                    f"Audio activation failed: 0x{activate_hr.value:08X}")

            audio_client = activated.QueryInterface(
                comtypes.gen.IAudioClient if hasattr(comtypes, "gen") else IUnknown)
            return audio_client

        # ── IAudioSessionManager2 for app enumeration ──

        class IAudioSessionControl(IUnknown):
            _iid_ = GUID("{F4B1A599-7266-4319-A8CA-E70ACB11E8CD}")

        class IAudioSessionControl2(IAudioSessionControl):
            _iid_ = GUID("{BFB7FF88-7239-4FC9-8FA2-07C950BE9C6D}")
            _methods_ = [
                COMMETHOD([], HRESULT, "GetSessionIdentifier",
                          (["out"], ctypes.POINTER(ctypes.c_wchar_p), "pRetVal")),
                COMMETHOD([], HRESULT, "GetSessionInstanceIdentifier",
                          (["out"], ctypes.POINTER(ctypes.c_wchar_p), "pRetVal")),
                COMMETHOD([], HRESULT, "GetProcessId",
                          (["out"], ctypes.POINTER(ctypes.c_uint), "pRetVal")),
                COMMETHOD([], HRESULT, "IsSystemSoundsSession"),
                COMMETHOD([], HRESULT, "SetDuckingPreference",
                          (["in"], ctypes.c_int, "optOut")),
            ]

        class IAudioSessionEnumerator(IUnknown):
            _iid_ = GUID("{E2F5BB11-0570-40CA-ACDD-3AA01277DEE8}")
            _methods_ = [
                COMMETHOD([], HRESULT, "GetCount",
                          (["out"], ctypes.POINTER(ctypes.c_int), "SessionCount")),
                COMMETHOD([], HRESULT, "GetSession",
                          (["in"], ctypes.c_int, "SessionNumber",
                           ["out"], ctypes.POINTER(ctypes.POINTER(IAudioSessionControl)), "Session")),
            ]

        class IAudioSessionManager2(IUnknown):
            _iid_ = IID_IAudioSessionManager2
            _methods_ = [
                COMMETHOD([], HRESULT, "GetAudioSessionControl",
                          (["in"], ctypes.POINTER(GUID), "AudioSessionGuid",
                           ["in"], ctypes.c_uint, "StreamFlags",
                           ["out"], ctypes.POINTER(ctypes.POINTER(IAudioSessionControl)), "SessionControl")),
                COMMETHOD([], HRESULT, "GetSimpleAudioVolume",
                          (["in"], ctypes.POINTER(GUID), "AudioSessionGuid",
                           ["in"], ctypes.c_uint, "StreamFlags",
                           ["out"], ctypes.POINTER(ctypes.POINTER(IUnknown)), "AudioVolume")),
                COMMETHOD([], HRESULT, "GetSessionEnumerator",
                          (["out"], ctypes.POINTER(ctypes.POINTER(IAudioSessionEnumerator)), "SessionEnum")),
            ]

        # ── IMMDevice / IMMDeviceEnumerator ──

        class IMMDevice(IUnknown):
            _iid_ = GUID("{D666063F-1587-4E43-81F1-B948E807363F}")
            _methods_ = [
                COMMETHOD([], HRESULT, "Activate",
                          (["in"], ctypes.POINTER(GUID), "iid",
                           ["in"], ctypes.c_uint, "dwClsCtx",
                           ["in"], ctypes.POINTER(PROPVARIANT), "pActivationParams",
                           ["out"], ctypes.POINTER(ctypes.POINTER(IUnknown)), "ppInterface")),
            ]

        class IMMDeviceEnumerator(IUnknown):
            _iid_ = IID_IMMDeviceEnumerator
            _methods_ = [
                COMMETHOD([], HRESULT, "EnumAudioEndpoints",
                          (["in"], ctypes.c_uint, "dataFlow",
                           ["in"], ctypes.c_uint, "dwStateMask",
                           ["out"], ctypes.POINTER(ctypes.POINTER(IUnknown)), "ppDevices")),
                COMMETHOD([], HRESULT, "GetDefaultAudioEndpoint",
                          (["in"], ctypes.c_uint, "dataFlow",
                           ["in"], ctypes.c_uint, "role",
                           ["out"], ctypes.POINTER(ctypes.POINTER(IMMDevice)), "ppEndpoint")),
            ]

        def _list_audio_sessions() -> list[AppInfo]:
            """Enumerate active audio sessions via IAudioSessionManager2."""
            import psutil
            comtypes.CoInitializeEx(comtypes.COINIT_MULTITHREADED)
            try:
                enumerator = comtypes.CoCreateInstance(
                    CLSID_MMDeviceEnumerator, IMMDeviceEnumerator)
                device = ctypes.POINTER(IMMDevice)()
                enumerator.GetDefaultAudioEndpoint(0, 0, ctypes.byref(device))

                mgr_ptr = ctypes.POINTER(IUnknown)()
                device.Activate(
                    ctypes.byref(IID_IAudioSessionManager2),
                    comtypes.CLSCTX_ALL, None, ctypes.byref(mgr_ptr))
                mgr = mgr_ptr.QueryInterface(IAudioSessionManager2)

                session_enum = ctypes.POINTER(IAudioSessionEnumerator)()
                mgr.GetSessionEnumerator(ctypes.byref(session_enum))

                count = ctypes.c_int()
                session_enum.GetCount(ctypes.byref(count))

                apps = []
                seen_pids = set()
                for i in range(count.value):
                    ctrl = ctypes.POINTER(IAudioSessionControl)()
                    session_enum.GetSession(i, ctypes.byref(ctrl))
                    try:
                        ctrl2 = ctrl.QueryInterface(IAudioSessionControl2)
                        pid_val = ctypes.c_uint()
                        ctrl2.GetProcessId(ctypes.byref(pid_val))
                        pid = pid_val.value
                        if pid == 0 or pid in seen_pids:
                            continue
                        seen_pids.add(pid)
                        try:
                            proc = psutil.Process(pid)
                            name = proc.name()
                            if name.lower().endswith(".exe"):
                                name = name[:-4]
                        except (psutil.NoSuchProcess, psutil.AccessDenied):
                            name = f"PID {pid}"
                        apps.append(AppInfo(pid=pid, name=name))
                    except Exception:
                        continue
                return apps
            finally:
                comtypes.CoUninitialize()

        # ── WASAPI Stream ──

        class _WasapiAppStream(AppAudioStream):
            def __init__(self, pid: int):
                self._pid = pid
                self._stopped = False
                self._client = None
                self._capture_client = None
                self._event = None
                self._native_rate = 0
                self._native_channels = 0
                self._buffer = np.array([], dtype=np.float32)
                self._init_capture()

            def _init_capture(self):
                comtypes.CoInitializeEx(comtypes.COINIT_MULTITHREADED)
                self._client = _open_process_loopback(self._pid)

                # Get mix format
                fmt_ptr = ctypes.POINTER(WAVEFORMATEX)()
                self._client.GetMixFormat(ctypes.byref(fmt_ptr))
                fmt = fmt_ptr.contents
                self._native_rate = fmt.nSamplesPerSec
                self._native_channels = fmt.nChannels
                log.info(f"App capture [{self._pid}]: native {self._native_rate} Hz, "
                         f"{self._native_channels} ch")

                # Create event for buffer-ready notification
                self._event = _kernel32.CreateEventW(None, False, False, None)

                # Initialize audio client
                ref_time = int(10_000_000 * CHUNK_DURATION)  # 100ns units
                self._client.Initialize(
                    AUDCLNT_SHAREMODE_SHARED,
                    AUDCLNT_STREAMFLAGS_LOOPBACK | AUDCLNT_STREAMFLAGS_EVENTCALLBACK,
                    ref_time, 0, fmt_ptr, None)
                self._client.SetEventHandle(self._event)

                # Get capture client
                cap = ctypes.POINTER(IUnknown)()
                self._client.GetService(
                    ctypes.byref(IID_IAudioCaptureClient), ctypes.byref(cap))
                self._capture_client = cap

                self._client.Start()

            @property
            def sample_rate(self) -> int:
                return self._native_rate

            def read(self) -> np.ndarray:
                if self._stopped:
                    raise StopIteration("Stream stopped")

                while len(self._buffer) < CHUNK_SAMPLES:
                    result = _kernel32.WaitForSingleObject(
                        self._event, 1000)  # 1s timeout
                    if self._stopped:
                        raise StopIteration("Stream stopped")
                    if result != WAIT_OBJECT_0:
                        continue

                    # Read all available packets
                    while True:
                        packet_size = ctypes.c_uint()
                        self._capture_client.GetNextPacketSize(
                            ctypes.byref(packet_size))
                        if packet_size.value == 0:
                            break
                        data_ptr = ctypes.c_void_p()
                        frames = ctypes.c_uint()
                        flags = ctypes.c_uint()
                        self._capture_client.GetBuffer(
                            ctypes.byref(data_ptr),
                            ctypes.byref(frames),
                            ctypes.byref(flags), None, None)
                        n_frames = frames.value
                        if n_frames > 0 and data_ptr.value:
                            arr = np.ctypeslib.as_array(
                                (ctypes.c_float * (n_frames * self._native_channels))
                                .from_address(data_ptr.value)).copy()
                            arr = arr.reshape(-1, self._native_channels)
                            # Mix to mono
                            mono = arr.mean(axis=1)
                            # Resample if needed
                            if self._native_rate != SAMPLE_RATE:
                                ratio = SAMPLE_RATE / self._native_rate
                                n_out = int(len(mono) * ratio)
                                indices = np.linspace(
                                    0, len(mono) - 1, n_out).astype(np.float32)
                                idx_floor = indices.astype(np.intp)
                                idx_ceil = np.minimum(
                                    idx_floor + 1, len(mono) - 1)
                                frac = indices - idx_floor
                                mono = (mono[idx_floor] * (1 - frac)
                                        + mono[idx_ceil] * frac)
                            self._buffer = np.concatenate(
                                [self._buffer, mono.astype(np.float32)])
                        self._capture_client.ReleaseBuffer(n_frames)

                chunk = self._buffer[:CHUNK_SAMPLES]
                self._buffer = self._buffer[CHUNK_SAMPLES:]
                return chunk

            def stop(self):
                self._stopped = True
                if self._client:
                    try:
                        self._client.Stop()
                    except Exception:
                        pass
                if self._event:
                    _kernel32.CloseHandle(self._event)
                    self._event = None

        class _WasapiLoopbackProvider(AppAudioProvider):
            def available(self) -> bool:
                if not _HAS_COMTYPES:
                    return False
                # Check Windows build number
                try:
                    build = int(sys.getwindowsversion().build)
                    return build >= 20348
                except Exception:
                    return False

            def list_apps(self) -> list[AppInfo]:
                try:
                    return _list_audio_sessions()
                except Exception as e:
                    log.warning(f"Failed to enumerate audio sessions: {e}")
                    return []

            def open_capture(self, pid: int) -> AppAudioStream:
                return _WasapiAppStream(pid)
```

**Note:** This is a substantial file (~350 lines). The COM interface definitions are verbose but necessary — they define the exact memory layout Windows expects. The actual logic is in `_WasapiAppStream.read()` (event-driven buffer reads with resampling) and `_list_audio_sessions()` (session enumeration).

- [ ] **Step 3: Verify the module imports without error**

Run:
```bash
build/windows/venv_full/Scripts/python.exe -c "from app_audio import get_provider, AppInfo; print('OK')"
```
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add app_audio.py requirements.txt
git commit -m "[feat] add app_audio.py — cross-platform app audio capture abstraction + Windows WASAPI backend"
```

---

### Task 2: Create the Linux PulseAudio backend

**Files:**
- Create: `app_audio_pulse.py`
- Modify: `requirements.txt`

- [ ] **Step 1: Add `pulsectl` to requirements.txt**

In `requirements.txt`, add after the `comtypes` line:

```
pulsectl>=23.5.0,<24.0; sys_platform == "linux"
```

- [ ] **Step 2: Create `app_audio_pulse.py`**

Create `app_audio_pulse.py` at the project root:

```python
"""Linux PulseAudio/PipeWire backend for per-app audio capture.

Uses pulsectl to:
1. Enumerate apps producing audio (sink inputs)
2. Create a combined sink so capture doesn't silence the user's speakers
3. Record from the null sink's monitor source

Works with both PulseAudio and PipeWire (via its PulseAudio compat layer).
"""

from __future__ import annotations
import atexit
import logging
import threading
import numpy as np

from app_audio import AppAudioProvider, AppAudioStream, AppInfo, SAMPLE_RATE, CHUNK_SAMPLES

log = logging.getLogger("livecaption.app_audio")

try:
    import pulsectl
    _HAS_PULSECTL = True
except ImportError:
    _HAS_PULSECTL = False


class _PulseAppStream(AppAudioStream):
    """Capture audio from a specific app by PID via PulseAudio."""

    def __init__(self, pid: int, pulse: pulsectl.Pulse):
        self._pid = pid
        self._stopped = False
        self._null_module_idx = None
        self._combine_module_idx = None
        self._original_sink = None
        self._sink_input_idx = None
        self._pulse = pulse
        self._rec_pulse = None
        self._native_rate = 44100
        self._buffer = np.array([], dtype=np.float32)
        self._lock = threading.Lock()
        self._setup()
        atexit.register(self._cleanup)

    def _setup(self):
        # Find the sink input for the target PID
        for si in self._pulse.sink_input_list():
            si_pid = si.proplist.get("application.process.id", "")
            if str(si_pid) == str(self._pid):
                self._sink_input_idx = si.index
                self._original_sink = si.sink
                break
        if self._sink_input_idx is None:
            raise RuntimeError(f"No PulseAudio sink input found for PID {self._pid}")

        # Create a null sink for capture
        null_name = f"linguataxi_capture_{self._pid}"
        self._null_module_idx = self._pulse.module_load(
            "module-null-sink",
            f'sink_name={null_name} sink_properties=device.description="LinguaTaxi_Capture"')

        # Create a combined sink: routes audio to both null sink AND original output
        combine_name = f"linguataxi_combine_{self._pid}"
        original_sink_name = self._pulse.sink_info(self._original_sink).name
        self._combine_module_idx = self._pulse.module_load(
            "module-combine-sink",
            f'sink_name={combine_name} slaves={null_name},{original_sink_name} '
            f'sink_properties=device.description="LinguaTaxi_Combined"')

        # Move the app's audio to the combined sink
        combine_sink = None
        for s in self._pulse.sink_list():
            if s.name == combine_name:
                combine_sink = s
                break
        if combine_sink:
            self._pulse.sink_input_move(self._sink_input_idx, combine_sink.index)

        # Find the null sink's monitor source for recording
        monitor_source = None
        for src in self._pulse.source_list():
            if src.name == f"{null_name}.monitor":
                monitor_source = src.name
                self._native_rate = src.sample_spec.rate or 44100
                break

        if not monitor_source:
            raise RuntimeError(f"Monitor source not found for {null_name}")

        # Open a recording stream on the monitor
        self._rec_pulse = pulsectl.Pulse("linguataxi-capture")
        self._rec_stream = self._rec_pulse.stream_record(
            monitor_source, rate=self._native_rate, channels=1, format="float32le")

    @property
    def sample_rate(self) -> int:
        return self._native_rate

    def read(self) -> np.ndarray:
        if self._stopped:
            raise StopIteration("Stream stopped")

        while len(self._buffer) < CHUNK_SAMPLES:
            data = self._rec_stream.read(4096)
            if data is None:
                if self._stopped:
                    raise StopIteration("Stream stopped")
                continue
            samples = np.frombuffer(data, dtype=np.float32)
            if self._native_rate != SAMPLE_RATE:
                ratio = SAMPLE_RATE / self._native_rate
                n_out = int(len(samples) * ratio)
                indices = np.linspace(0, len(samples) - 1, n_out).astype(np.float32)
                idx_floor = indices.astype(np.intp)
                idx_ceil = np.minimum(idx_floor + 1, len(samples) - 1)
                frac = indices - idx_floor
                samples = (samples[idx_floor] * (1 - frac)
                           + samples[idx_ceil] * frac).astype(np.float32)
            self._buffer = np.concatenate([self._buffer, samples])

        chunk = self._buffer[:CHUNK_SAMPLES]
        self._buffer = self._buffer[CHUNK_SAMPLES:]
        return chunk

    def _cleanup(self):
        """Restore original audio routing and unload modules."""
        try:
            if self._sink_input_idx is not None and self._original_sink is not None:
                self._pulse.sink_input_move(self._sink_input_idx, self._original_sink)
        except Exception:
            pass
        try:
            if self._combine_module_idx is not None:
                self._pulse.module_unload(self._combine_module_idx)
        except Exception:
            pass
        try:
            if self._null_module_idx is not None:
                self._pulse.module_unload(self._null_module_idx)
        except Exception:
            pass

    def stop(self):
        self._stopped = True
        self._cleanup()
        atexit.unregister(self._cleanup)
        if self._rec_pulse:
            try:
                self._rec_pulse.close()
            except Exception:
                pass


class PulseAudioProvider(AppAudioProvider):
    def __init__(self):
        self._pulse = None

    def _get_pulse(self):
        if self._pulse is None:
            self._pulse = pulsectl.Pulse("linguataxi-provider")
        return self._pulse

    def available(self) -> bool:
        if not _HAS_PULSECTL:
            return False
        try:
            self._get_pulse()
            return True
        except Exception:
            return False

    def list_apps(self) -> list[AppInfo]:
        try:
            pulse = self._get_pulse()
            apps = []
            seen_pids = set()
            for si in pulse.sink_input_list():
                pid_str = si.proplist.get("application.process.id", "")
                name = si.proplist.get("application.name", "")
                if not pid_str:
                    continue
                pid = int(pid_str)
                if pid in seen_pids:
                    continue
                seen_pids.add(pid)
                apps.append(AppInfo(pid=pid, name=name or f"PID {pid}"))
            return apps
        except Exception as e:
            log.warning(f"Failed to enumerate PulseAudio sessions: {e}")
            return []

    def open_capture(self, pid: int) -> AppAudioStream:
        return _PulseAppStream(pid, self._get_pulse())
```

- [ ] **Step 3: Commit**

```bash
git add app_audio_pulse.py requirements.txt
git commit -m "[feat] add Linux PulseAudio/PipeWire backend for app audio capture"
```

---

### Task 3: Create the macOS Core Audio backend

**Files:**
- Create: `build/mac/audiotap/main.swift`
- Create: `app_audio_coreaudio.py`
- Modify: `build/mac/build.sh`

- [ ] **Step 1: Create the Swift helper source**

Create `build/mac/audiotap/main.swift`:

```swift
// linguataxi-audiotap — Core Audio per-process audio tap helper
//
// Usage:
//   linguataxi-audiotap --pid <PID> --rate 16000 --format float32
//   linguataxi-audiotap --list-apps
//
// Audio is streamed as raw PCM float32 mono on stdout.
// Send a newline on stdin to stop gracefully.

import Foundation
import AVFoundation
import CoreAudio

// MARK: - App listing mode

func listApps() {
    var apps: [[String: Any]] = []
    if #available(macOS 14.2, *) {
        let workspace = NSWorkspace.shared
        for app in workspace.runningApplications {
            guard app.activationPolicy == .regular,
                  let name = app.localizedName else { continue }
            apps.append(["pid": app.processIdentifier, "name": name])
        }
    }
    if let data = try? JSONSerialization.data(withJSONObject: apps),
       let json = String(data: data, encoding: .utf8) {
        FileHandle.standardOutput.write(Data((json + "\n").utf8))
    }
}

// MARK: - Capture mode

@available(macOS 14.2, *)
func startCapture(pid: pid_t, targetRate: Double) {
    let tapDesc = CATapDescription(stereoMixdownOfProcesses: [pid])
    var tapID: AudioObjectID = 0

    var status = AudioHardwareCreateProcessTap(tapDesc, &tapID)
    guard status == noErr else {
        FileHandle.standardError.write(
            Data("ERROR: AudioHardwareCreateProcessTap failed: \(status)\n".utf8))
        exit(1)
    }

    let engine = AVAudioEngine()
    let inputNode = engine.inputNode

    let nativeFormat = inputNode.outputFormat(forBus: 0)
    let outputFormat = AVAudioFormat(
        commonFormat: .pcmFormatFloat32,
        sampleRate: targetRate,
        channels: 1,
        interleaved: false)!

    guard let converter = AVAudioConverter(from: nativeFormat, to: outputFormat) else {
        FileHandle.standardError.write(Data("ERROR: Cannot create converter\n".utf8))
        exit(1)
    }

    inputNode.installTap(onBus: 0, bufferSize: 4096, format: nativeFormat) {
        buffer, _ in
        let frameCapacity = AVAudioFrameCount(
            Double(buffer.frameLength) * targetRate / nativeFormat.sampleRate)
        guard let outBuffer = AVAudioPCMBuffer(
            pcmFormat: outputFormat, frameCapacity: frameCapacity) else { return }

        var error: NSError?
        converter.convert(to: outBuffer, error: &error) { _, outStatus in
            outStatus.pointee = .haveData
            return buffer
        }

        if let channelData = outBuffer.floatChannelData {
            let count = Int(outBuffer.frameLength)
            let data = Data(bytes: channelData[0], count: count * 4)
            FileHandle.standardOutput.write(data)
        }
    }

    do {
        try engine.start()
    } catch {
        FileHandle.standardError.write(
            Data("ERROR: Engine start failed: \(error)\n".utf8))
        exit(1)
    }

    // Wait for newline on stdin or EOF to stop
    let _ = FileHandle.standardInput.availableData
    engine.stop()
    AudioHardwareDestroyProcessTap(tapID)
}

// MARK: - Main

let args = CommandLine.arguments

if args.contains("--list-apps") {
    listApps()
    exit(0)
}

guard let pidIdx = args.firstIndex(of: "--pid"),
      pidIdx + 1 < args.count,
      let pid = Int32(args[pidIdx + 1]) else {
    FileHandle.standardError.write(
        Data("Usage: linguataxi-audiotap --pid <PID> [--rate 16000]\n".utf8))
    exit(1)
}

var targetRate: Double = 16000
if let rateIdx = args.firstIndex(of: "--rate"),
   rateIdx + 1 < args.count,
   let rate = Double(args[rateIdx + 1]) {
    targetRate = rate
}

if #available(macOS 14.2, *) {
    startCapture(pid: pid, targetRate: targetRate)
} else {
    FileHandle.standardError.write(
        Data("ERROR: Requires macOS 14.2+\n".utf8))
    exit(1)
}
```

- [ ] **Step 2: Create `app_audio_coreaudio.py`**

Create `app_audio_coreaudio.py` at the project root:

```python
"""macOS Core Audio process tap backend for per-app audio capture.

Launches a small Swift helper binary (linguataxi-audiotap) that uses
CATapDescription (macOS 14.2+) to tap a specific process's audio.
Audio streams back as raw PCM float32 mono via stdout.
"""

from __future__ import annotations
import json
import logging
import os
import platform
import subprocess
import sys
import numpy as np

from app_audio import AppAudioProvider, AppAudioStream, AppInfo, SAMPLE_RATE, CHUNK_SAMPLES

log = logging.getLogger("livecaption.app_audio")


def _helper_path() -> str | None:
    """Find the linguataxi-audiotap binary."""
    # When running from .app bundle
    if getattr(sys, "frozen", False):
        bundle_dir = os.path.dirname(sys.executable)
        p = os.path.join(bundle_dir, "linguataxi-audiotap")
        if os.path.isfile(p):
            return p
    # When running from source
    src_dir = os.path.dirname(os.path.abspath(__file__))
    p = os.path.join(src_dir, "build", "mac", "audiotap", "linguataxi-audiotap")
    if os.path.isfile(p):
        return p
    return None


class _CoreAudioAppStream(AppAudioStream):
    def __init__(self, pid: int, helper: str):
        self._pid = pid
        self._stopped = False
        self._buffer = np.array([], dtype=np.float32)
        self._proc = subprocess.Popen(
            [helper, "--pid", str(pid), "--rate", str(SAMPLE_RATE)],
            stdout=subprocess.PIPE,
            stdin=subprocess.PIPE,
            stderr=subprocess.PIPE)

    @property
    def sample_rate(self) -> int:
        return SAMPLE_RATE  # helper resamples internally

    def read(self) -> np.ndarray:
        if self._stopped or self._proc.poll() is not None:
            raise StopIteration("Stream stopped")

        bytes_needed = CHUNK_SAMPLES * 4  # float32 = 4 bytes
        while len(self._buffer) < CHUNK_SAMPLES:
            raw = self._proc.stdout.read(bytes_needed)
            if not raw:
                if self._stopped or self._proc.poll() is not None:
                    raise StopIteration("Helper exited")
                continue
            samples = np.frombuffer(raw, dtype=np.float32)
            self._buffer = np.concatenate([self._buffer, samples])

        chunk = self._buffer[:CHUNK_SAMPLES]
        self._buffer = self._buffer[CHUNK_SAMPLES:]
        return chunk

    def stop(self):
        self._stopped = True
        if self._proc and self._proc.poll() is None:
            try:
                self._proc.stdin.write(b"\n")
                self._proc.stdin.flush()
                self._proc.wait(timeout=3)
            except Exception:
                self._proc.kill()


class CoreAudioTapProvider(AppAudioProvider):
    def __init__(self):
        self._helper = _helper_path()

    def available(self) -> bool:
        if sys.platform != "darwin" or not self._helper:
            return False
        try:
            ver = platform.mac_ver()[0]  # e.g. "14.3.1"
            major, minor = int(ver.split(".")[0]), int(ver.split(".")[1])
            return (major, minor) >= (14, 2)
        except Exception:
            return False

    def list_apps(self) -> list[AppInfo]:
        if not self._helper:
            return []
        try:
            result = subprocess.run(
                [self._helper, "--list-apps"],
                capture_output=True, text=True, timeout=5)
            data = json.loads(result.stdout)
            return [AppInfo(pid=a["pid"], name=a["name"]) for a in data]
        except Exception as e:
            log.warning(f"Failed to list apps via audiotap: {e}")
            return []

    def open_capture(self, pid: int) -> AppAudioStream:
        if not self._helper:
            raise RuntimeError("audiotap helper not found")
        return _CoreAudioAppStream(pid, self._helper)
```

- [ ] **Step 3: Add Swift compilation to `build/mac/build.sh`**

In `build/mac/build.sh`, add before the DMG creation step:

```bash
# ── Compile audiotap helper ──
echo "  Compiling linguataxi-audiotap..."
swiftc -O -o "$APP_DIR/Contents/MacOS/linguataxi-audiotap" \
    "$SCRIPT_DIR/audiotap/main.swift" \
    -target arm64-apple-macos14.2 \
    -target x86_64-apple-macos14.2 \
    2>/dev/null || echo "  [WARN] audiotap compilation skipped (requires macOS 14.2 SDK)"
```

- [ ] **Step 4: Commit**

```bash
git add build/mac/audiotap/main.swift app_audio_coreaudio.py build/mac/build.sh
git commit -m "[feat] add macOS Core Audio process tap backend for app audio capture"
```

---

### Task 4: Server integration — AudioSource changes and API endpoints

**Files:**
- Modify: `server.py`

- [ ] **Step 1: Import app_audio at the top of server.py**

In `server.py`, after the existing imports (around line 30, after `from pathlib import Path`), add:

```python
from app_audio import get_provider as get_app_audio_provider
```

- [ ] **Step 2: Initialize the provider in `main()`**

In `server.py`, in the `main()` function (search for `if __name__` or the `main()` definition), add after the argument parsing and before server startup:

```python
    global _app_audio_provider
    _app_audio_provider = get_app_audio_provider()
    if _app_audio_provider:
        log.info(f"App audio capture available ({sys.platform})")
    else:
        log.info("App audio capture not available on this platform")
```

And add the global near the other globals (around line 298):

```python
_app_audio_provider = None  # AppAudioProvider instance, set in main()
```

- [ ] **Step 3: Add `app_pid` and `app_stream` to AudioSource**

In `server.py`, modify the `AudioSource.__init__` method (line 319) to accept and store `app_pid`:

```python
    def __init__(self, device_index=None, name=None, app_pid=None):
        self.id = AudioSource._next_id
        AudioSource._next_id += 1
        self.device_index = device_index
        self.app_pid = app_pid
        self.app_stream = None
        self.name = name or f"Source {self.id + 1}"
        self.speaker = ""
        self.color = ""
        self.speaker_change_pending = None
        self.speaker_lock = threading.Lock()
        self.queue = queue.Queue()
        self.stream = None
        self.capture_thread = None
        self.buffer_thread = None
        self.active = True
        self.restart_event = threading.Event()
        self.current_lang = None
        self.voice_id_enroll_pending = None
```

- [ ] **Step 4: Update `add_source` to accept `app_pid`**

Modify the `add_source` function (line 352):

```python
def add_source(device_index=None, name=None, app_pid=None):
    """Create and register a new AudioSource."""
    with _sources_lock:
        if len(_sources) >= 8:
            return None
        # Reject duplicates
        for s in _sources:
            if app_pid and s.app_pid == app_pid:
                return None
            if device_index is not None and s.device_index == device_index and not app_pid:
                return None
        src = AudioSource(device_index, name, app_pid=app_pid)
        _sources.append(src)
    return src
```

- [ ] **Step 5: Update `remove_source` to clean up app streams**

Modify `remove_source` (line 362) to also stop app streams:

```python
def remove_source(source_id):
    """Stop and remove an AudioSource."""
    src = get_source(source_id)
    if not src:
        return False
    src.active = False
    src.restart_event.set()
    if src.app_stream:
        try:
            src.app_stream.stop()
        except Exception:
            pass
    if src.stream:
        try:
            src.stream.stop()
            src.stream.close()
        except Exception:
            pass
    with _sources_lock:
        _sources[:] = [s for s in _sources if s.id != source_id]
    return True
```

- [ ] **Step 6: Add `start_app_capture` function**

After `start_source_capture` (around line 1230), add:

```python
def start_app_capture(source):
    """Capture loop for app audio sources. Reads from AppAudioStream,
    queues chunks into source.queue — same interface as device sources."""
    retry_delay = 2
    while source.active and not shutdown_event.is_set():
        try:
            stream = _app_audio_provider.open_capture(source.app_pid)
            source.app_stream = stream
            log.info(f"App capture started for [{source.name}] (PID {source.app_pid})")
            retry_delay = 2
            while source.active and not shutdown_event.is_set():
                try:
                    chunk = stream.read()
                    source.queue.put(chunk)
                except StopIteration:
                    log.info(f"App capture ended for [{source.name}] (app exited)")
                    break
            stream.stop()
            source.app_stream = None
            if not source.active or shutdown_event.is_set():
                break
        except Exception as e:
            log.error(f"App capture error [{source.name}]: {e}")
            source.app_stream = None
            if not source.active or shutdown_event.is_set():
                break
            log.info(f"[{source.name}] retrying in {retry_delay}s...")
            shutdown_event.wait(retry_delay)
            retry_delay = min(retry_delay * 2, 30)
```

- [ ] **Step 7: Update `api_list_sources` to include app_pid and source type**

Modify the `/api/sources` endpoint (line 1943):

```python
@operator_app.get("/api/sources")
async def api_list_sources():
    """List all active audio sources."""
    with _sources_lock:
        return JSONResponse([{
            "id": s.id, "name": s.name, "speaker": s.speaker,
            "color": s.color, "device_index": s.device_index,
            "app_pid": s.app_pid,
            "type": "app" if s.app_pid else "device"
        } for s in _sources])
```

- [ ] **Step 8: Add the three new API endpoints**

After the existing `/api/sources/remove` endpoint (around line 1995), add:

```python
@operator_app.get("/api/app-audio/available")
async def api_app_audio_available():
    """Check if app audio capture is available on this platform."""
    return JSONResponse({
        "available": _app_audio_provider is not None and _app_audio_provider.available()
    })

@operator_app.get("/api/app-audio/list")
async def api_app_audio_list():
    """List applications currently producing audio."""
    if not _app_audio_provider:
        return JSONResponse([])
    try:
        apps = _app_audio_provider.list_apps()
        # Filter out apps already captured
        with _sources_lock:
            captured_pids = {s.app_pid for s in _sources if s.app_pid}
        return JSONResponse([
            {"pid": a.pid, "name": a.name}
            for a in apps if a.pid not in captured_pids
        ])
    except Exception as e:
        log.warning(f"App enumeration failed: {e}")
        return JSONResponse([])

@operator_app.post("/api/sources/add-app")
async def api_add_app_source(request: Request):
    """Add an app audio source."""
    if not _app_audio_provider or not _app_audio_provider.available():
        return JSONResponse({"error": "App audio capture not available"}, status_code=400)
    data = await request.json()
    pid = data.get("pid")
    name = data.get("name", f"App (PID {pid})")
    if not pid:
        return JSONResponse({"error": "pid required"}, status_code=400)
    src = add_source(app_pid=pid, name=name)
    if not src:
        return JSONResponse({"error": "Maximum 8 sources or app already captured"}, status_code=400)
    # Start app capture thread
    t = threading.Thread(target=start_app_capture, args=(src,), daemon=True)
    t.start()
    src.capture_thread = t
    # Start buffer processing thread
    if stt_backend and hasattr(stt_backend, '_transcribe'):
        bt = threading.Thread(target=_buffer_audio_loop,
                              args=(stt_backend._transcribe, loop, src), daemon=True)
        bt.start()
        src.buffer_thread = bt
    elif stt_backend and hasattr(stt_backend, '_vosk_source_loop'):
        bt = threading.Thread(target=stt_backend._vosk_source_loop,
                              args=(loop, src), daemon=True)
        bt.start()
        src.buffer_thread = bt
    await broadcast_all({"type": "source_added", "source": {
        "id": src.id, "name": src.name, "speaker": src.speaker,
        "color": src.color, "app_pid": src.app_pid, "type": "app"}})
    return JSONResponse({"id": src.id, "name": src.name})
```

- [ ] **Step 9: Commit**

```bash
git add server.py
git commit -m "[feat] server.py: app audio source support — AudioSource.app_pid, start_app_capture, API endpoints"
```

---

### Task 5: Operator panel — Audio Sources section

**Files:**
- Modify: `operator.html`

- [ ] **Step 1: Add the Audio Sources section HTML**

In `operator.html`, find a suitable location in the left panel (`.ctrl` div) to add the Audio Sources section. Add after the speaker/source management area:

```html
<!-- ── Audio Sources ─�� -->
<div class="section collapsible" id="srcSection">
  <div class="section-hdr" onclick="toggleSection('srcSection')">
    <span>Audio Sources</span><span class="chev">▾</span>
  </div>
  <div class="section-body">
    <div id="activeSources"></div>
    <div style="display:flex;gap:6px;margin-top:8px">
      <select id="addSourceSel" style="flex:1;padding:4px 8px;background:rgba(255,255,255,.06);border:1px solid var(--bdr);border-radius:4px;color:#e0e0e0;font-size:12px">
        <option value="">Select source...</option>
      </select>
      <button class="btn" onclick="addSelectedSource()">Add</button>
      <button class="btn" onclick="refreshAppList()" title="Refresh app list">↻</button>
    </div>
    <div id="srcLimit" style="display:none;color:#F09595;font-size:11px;margin-top:4px">Maximum 8 sources reached</div>
  </div>
</div>
```

- [ ] **Step 2: Add the collapsible section CSS**

In the `<style>` section of `operator.html`, add:

```css
.section{border:1px solid var(--bdr);border-radius:6px;overflow:hidden;margin-bottom:8px}
.section-hdr{display:flex;justify-content:space-between;align-items:center;padding:8px 12px;background:rgba(255,255,255,.03);cursor:pointer;font-size:12px;font-weight:600;text-transform:uppercase;letter-spacing:.06em;color:rgba(255,255,255,.6);user-select:none}
.section-hdr:hover{background:rgba(255,255,255,.06)}
.section-hdr .chev{transition:transform .2s}
.section.collapsed .section-body{display:none}
.section.collapsed .chev{transform:rotate(-90deg)}
.section-body{padding:10px 12px}
.src-row{display:flex;align-items:center;gap:8px;padding:4px 0;font-size:12px}
.src-row .src-type{font-size:9px;padding:1px 5px;border-radius:3px;text-transform:uppercase;font-weight:600}
.src-row .src-type.device{background:rgba(79,195,247,.15);color:#4FC3F7}
.src-row .src-type.app{background:rgba(129,199,132,.15);color:#81C784}
.src-row .src-name{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.src-row .src-rm{background:none;border:none;color:#F09595;cursor:pointer;font-size:14px;padding:0 4px}
.src-row .src-rm:hover{color:#FF5252}
```

- [ ] **Step 3: Add the JavaScript for source management**

In the `<script>` section of `operator.html`, add:

```javascript
// ── Audio Source Management ──
let appAudioAvailable = false;
let availableApps = [];
let availableDevices = [];

function toggleSection(id) {
  const el = document.getElementById(id);
  el.classList.toggle('collapsed');
  // Save collapsed state
  const collapsed = JSON.parse(localStorage.getItem('collapsedSections') || '{}');
  collapsed[id] = el.classList.contains('collapsed');
  localStorage.setItem('collapsedSections', JSON.stringify(collapsed));
}

function restoreCollapsedSections() {
  const collapsed = JSON.parse(localStorage.getItem('collapsedSections') || '{}');
  for (const [id, isCollapsed] of Object.entries(collapsed)) {
    const el = document.getElementById(id);
    if (el) el.classList.toggle('collapsed', isCollapsed);
  }
}

function buildActiveSourcesList() {
  const container = document.getElementById('activeSources');
  if (!container) return;
  container.innerHTML = '';
  for (const s of sources) {
    const row = document.createElement('div');
    row.className = 'src-row';
    const typeBadge = `<span class="src-type ${s.type || 'device'}">${s.type || 'device'}</span>`;
    const rmBtn = sources.indexOf(s) > 0
      ? `<button class="src-rm" onclick="removeSource(${s.id})" title="Remove">✕</button>`
      : '';
    row.innerHTML = `${typeBadge}<span class="src-name">${s.name}</span>${rmBtn}`;
    container.appendChild(row);
  }
  document.getElementById('srcLimit').style.display = sources.length >= 8 ? '' : 'none';
  document.getElementById('addSourceSel').disabled = sources.length >= 8;
}

async function refreshSourceDropdown() {
  const sel = document.getElementById('addSourceSel');
  if (!sel) return;
  sel.innerHTML = '<option value="">Select source...</option>';

  // Devices
  try {
    const r = await fetch('/api/mics');
    availableDevices = (await r.json()).mics || [];
  } catch (e) { availableDevices = []; }

  if (availableDevices.length > 0) {
    const grp = document.createElement('optgroup');
    grp.label = 'Devices';
    for (const d of availableDevices) {
      const opt = document.createElement('option');
      opt.value = `device:${d.index}`;
      opt.textContent = d.name + (d.is_default ? ' (default)' : '');
      grp.appendChild(opt);
    }
    sel.appendChild(grp);
  }

  // Apps
  if (appAudioAvailable) {
    await refreshAppList();
  }
}

async function refreshAppList() {
  const sel = document.getElementById('addSourceSel');
  // Remove existing app optgroup
  for (const child of [...sel.children]) {
    if (child.tagName === 'OPTGROUP' && child.label === 'Applications') {
      child.remove();
    }
  }
  try {
    const r = await fetch('/api/app-audio/list');
    availableApps = await r.json();
  } catch (e) { availableApps = []; }
  if (availableApps.length > 0) {
    const grp = document.createElement('optgroup');
    grp.label = 'Applications';
    for (const a of availableApps) {
      const opt = document.createElement('option');
      opt.value = `app:${a.pid}:${a.name}`;
      opt.textContent = a.name;
      grp.appendChild(opt);
    }
    sel.appendChild(grp);
  }
}

async function addSelectedSource() {
  const sel = document.getElementById('addSourceSel');
  const val = sel.value;
  if (!val) return;
  if (val.startsWith('device:')) {
    const idx = parseInt(val.split(':')[1]);
    await fetch('/api/sources/add', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({device_index: idx})
    });
  } else if (val.startsWith('app:')) {
    const parts = val.split(':');
    const pid = parseInt(parts[1]);
    const name = parts.slice(2).join(':');
    await fetch('/api/sources/add-app', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({pid, name})
    });
  }
  sel.value = '';
}

async function removeSource(id) {
  await fetch('/api/sources/remove', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({source_id: id})
  });
}

// Check app audio availability on load
async function initAppAudio() {
  try {
    const r = await fetch('/api/app-audio/available');
    const data = await r.json();
    appAudioAvailable = data.available;
  } catch (e) { appAudioAvailable = false; }
  refreshSourceDropdown();
}

// Polling: refresh sources every 5s, apps every 15s
let _appPollCount = 0;
setInterval(async () => {
  try {
    const r = await fetch('/api/sources');
    const data = await r.json();
    sources = data;
    buildActiveSourcesList();
  } catch (e) {}
  _appPollCount++;
  if (_appPollCount % 3 === 0 && appAudioAvailable) {
    refreshAppList();
  }
}, 5000);
```

- [ ] **Step 4: Hook into existing initialization**

In the operator panel's WebSocket `onopen` or initialization code, add calls to:

```javascript
restoreCollapsedSections();
initAppAudio();
```

Also update the existing `buildSourceList()` function to also call `buildActiveSourcesList()` so the active sources display stays in sync with WebSocket updates.

- [ ] **Step 5: Commit**

```bash
git add operator.html
git commit -m "[feat] operator panel: Audio Sources section with device + app source management"
```

---

### Task 6: Launcher — live source management via API

**Files:**
- Modify: `launcher.pyw`

- [ ] **Step 1: Add API polling for live source management**

In `launcher.pyw`, add a method to the `LinguaTaxiApp` class that polls the server for source state when the server is running. This replaces the current startup-only approach with live management:

```python
def _poll_sources(self):
    """Poll server for active sources and update the UI."""
    if not self.server_proc or self.server_proc.poll() is not None:
        return
    try:
        import urllib.request, json
        url = f"http://127.0.0.1:{self._server_port}/operator/api/sources"
        with urllib.request.urlopen(url, timeout=2) as resp:
            data = json.loads(resp.read())
        self._update_source_display(data)
    except Exception:
        pass
    self.after(5000, self._poll_sources)
```

- [ ] **Step 2: Add app audio source controls**

Add methods to check app audio availability and show app sources in the source dropdown:

```python
def _check_app_audio(self):
    """Check if app audio capture is available and update UI."""
    if not self.server_proc or self.server_proc.poll() is not None:
        return
    try:
        import urllib.request, json
        url = f"http://127.0.0.1:{self._server_port}/operator/api/app-audio/available"
        with urllib.request.urlopen(url, timeout=2) as resp:
            data = json.loads(resp.read())
        self._app_audio_available = data.get("available", False)
    except Exception:
        self._app_audio_available = False

def _refresh_app_list(self):
    """Refresh the list of available app audio sources."""
    if not self._app_audio_available:
        return []
    try:
        import urllib.request, json
        url = f"http://127.0.0.1:{self._server_port}/operator/api/app-audio/list"
        with urllib.request.urlopen(url, timeout=2) as resp:
            return json.loads(resp.read())
    except Exception:
        return []

def _add_app_source(self, pid, name):
    """Add an app audio source via the server API."""
    try:
        import urllib.request, json
        url = f"http://127.0.0.1:{self._server_port}/operator/api/sources/add-app"
        data = json.dumps({"pid": pid, "name": name}).encode()
        req = urllib.request.Request(url, data=data,
                                     headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=5)
    except Exception as e:
        self._log_error(f"Failed to add app source: {e}")

def _remove_source_api(self, source_id):
    """Remove a source via the server API."""
    try:
        import urllib.request, json
        url = f"http://127.0.0.1:{self._server_port}/operator/api/sources/remove"
        data = json.dumps({"source_id": source_id}).encode()
        req = urllib.request.Request(url, data=data,
                                     headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=5)
    except Exception as e:
        self._log_error(f"Failed to remove source: {e}")
```

- [ ] **Step 3: Update the source dropdown to include app sources**

Modify `_refresh_source_combo` to add an "Applications" section when app audio is available:

```python
def _refresh_source_combo(self, combo):
    """Refresh a source dropdown with grouped device list + apps."""
    mics = list_mics()
    self._mic_devices = mics
    physical = [f"[{i}] {n}" for i, n, lb in mics if not lb]
    loopback = [f"[{i}] {n}" for i, n, lb in mics if lb]
    values = [_t("launcher.system_default")]
    if physical:
        values.extend(physical)
    if loopback:
        values.append(_t("launcher.system_audio_separator"))
        values.extend(loopback)
    elif IS_WIN:
        values.append(_t("launcher.no_system_audio"))
    # Add app sources if available
    if getattr(self, '_app_audio_available', False):
        apps = self._refresh_app_list()
        if apps:
            values.append("── Applications ──")
            for app in apps:
                values.append(f"[APP:{app['pid']}] {app['name']}")
    combo["values"] = values
```

- [ ] **Step 4: Start polling when server starts**

In the `_start_server` method, after the server process is confirmed running, add:

```python
self._app_audio_available = False
self.after(3000, self._check_app_audio)
self.after(5000, self._poll_sources)
```

- [ ] **Step 5: Commit**

```bash
git add launcher.pyw
git commit -m "[feat] launcher: live source management via API with app audio support"
```

---

### Task 7: End-to-end verification

- [ ] **Step 1: Verify the module loads on Windows**

```bash
cd C:\Users\Laptop\Documents\LinguaTaxi
build\windows\venv_full\Scripts\python.exe -c "
from app_audio import get_provider
p = get_provider()
print(f'Provider: {p}')
if p:
    print(f'Available: {p.available()}')
    apps = p.list_apps()
    print(f'Apps producing audio: {len(apps)}')
    for a in apps:
        print(f'  PID {a.pid}: {a.name}')
"
```

Expected: Shows the provider, availability status, and any apps currently producing audio.

- [ ] **Step 2: Start the server and test the API endpoints**

Start the server, then in a separate terminal:

```bash
curl http://127.0.0.1:3001/api/app-audio/available
curl http://127.0.0.1:3001/api/app-audio/list
curl http://127.0.0.1:3001/api/sources
```

Expected: Available returns `{available: true}` on Windows 10 20348+, list returns apps producing audio, sources returns the default source.

- [ ] **Step 3: Test adding an app source**

Start a media player or browser with audio playing, then:

```bash
curl -X POST http://127.0.0.1:3001/api/sources/add-app \
  -H "Content-Type: application/json" \
  -d '{"pid": <PID_FROM_LIST>, "name": "Test App"}'
```

Expected: Returns `{"id": 1, "name": "Test App"}`. The source should appear in `/api/sources` with `type: "app"`.

- [ ] **Step 4: Verify the operator panel shows the new section**

Open the operator panel at `http://127.0.0.1:3001` and verify:
- The "Audio Sources" collapsible section appears
- Active sources are listed with type badges (Device/App)
- The dropdown shows both Devices and Applications sections
- Adding/removing sources works from the UI

- [ ] **Step 5: Commit any fixes discovered during testing**

```bash
git add -A
git commit -m "[fix] app audio capture — fixes from end-to-end testing"
```
