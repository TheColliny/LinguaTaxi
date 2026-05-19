# LinguaTaxi Code Professionalization — Design Spec

**Date:** 2026-05-19
**Goal:** Refactor the LinguaTaxi codebase to the standard a veteran senior developer (40 years experience) would expect — clean architecture, proper documentation, type safety, and professional error handling — without breaking the existing build/install pipeline.
**Branch:** `refactor/professionalize` (off `master`)
**Strategy:** Top-down by file (Approach 2), internal extraction with stable entry points (Option B+C). Fix the dictation HTTP 500 bug in-flight.

---

## 1. Constraints

- **Root entry points stay in place.** `server.py`, `launcher.pyw`, `tray_dictation.py`, `download_models.py`, `transcribe_file.py` remain at repository root as thin shells. The Inno Setup installer and build scripts reference these paths and must not change.
- **Plugin structure untouched.** `plugins/` directory and its contents are not refactored.
- **Locales, assets, build, dev, scripts directories untouched** unless explicitly noted (e.g., .gitignore fixes).
- **No new dependencies.** We work with the existing stack. No linters or formatters added to production requirements (dev requirements are fine).
- **Behavior-preserving.** Every refactored module must produce identical behavior. No feature additions, no UX changes, no API changes.

---

## 2. Package Structure

After cleanup, the repository root contains only entry points and config. All internal logic lives in `linguataxi/`.

```
LinguaTaxi/
├── server.py                    # 5-10 lines: imports linguataxi.server.main and calls main()
├── launcher.pyw                 # 5-10 lines: imports linguataxi.launcher.main and calls main()
├── tray_dictation.py            # 5-10 lines: imports linguataxi.dictation.main and calls main()
├── download_models.py           # 5-10 lines: imports linguataxi.models.downloader and calls main()
├── transcribe_file.py           # 5-10 lines: imports linguataxi.server.routes.transcribe and calls main()
├── requirements.txt
├── requirements-dev.txt         # NEW: pytest, mypy, etc.
├── version.json
├── README.md
├── LICENSE
├── THIRD_PARTY_NOTICES.txt
├── .gitignore                   # Updated to exclude binaries
│
├── linguataxi/                  # The package
│   ├── __init__.py              # Version string, package docstring
│   ├── constants.py             # All shared constants (ports, sample rate, language maps)
│   ├── settings.py              # Shared settings load/save (eliminates duplication)
│   ├── types.py                 # Shared type definitions, dataclasses, TypedDicts
│   │
│   ├── server/
│   │   ├── __init__.py
│   │   ├── app.py               # FastAPI app creation, middleware, mount points
│   │   ├── audio.py             # AudioCapture class, mic management, silence detection
│   │   ├── backends/
│   │   │   ├── __init__.py      # Backend registry, factory function
│   │   │   ├── base.py          # STTBackend abstract base class
│   │   │   ├── whisper.py       # faster-whisper (CUDA) backend
│   │   │   ├── mlx_whisper.py   # MLX Whisper (Apple Metal) backend
│   │   │   └── vosk.py          # Vosk (CPU fallback) backend
│   │   ├── translation.py       # DeepL + offline translation thread pool
│   │   ├── websocket.py         # WebSocketManager: client tracking, broadcast
│   │   ├── routes/
│   │   │   ├── __init__.py
│   │   │   ├── operator.py      # Operator panel endpoints
│   │   │   ├── display.py       # Display/extended HTML rendering + endpoints
│   │   │   ├── dictation.py     # Dictation endpoints (HTTP 500 fix here)
│   │   │   └── transcribe.py    # File transcription endpoints
│   │   ├── transcripts.py       # Transcript saving logic (_save_line)
│   │   └── main.py              # Config loading, server startup, graceful shutdown
│   │
│   ├── launcher/
│   │   ├── __init__.py
│   │   ├── app.py               # LinguaTaxiApp(ctk.CTk) — thin composition shell
│   │   ├── server_manager.py    # ServerManager: subprocess, log capture, Job Object
│   │   ├── settings_panel.py    # SettingsPanel(ctk.CTkFrame): mic, backend, transcript dir
│   │   ├── batch_transcriber.py # BatchTranscriberPanel(ctk.CTkFrame): file transcription UI
│   │   ├── tray_manager.py      # TrayManager: pystray icon, menu, minimize/restore/quit
│   │   ├── model_download.py    # ModelDownloadDialog: first-run model download UI
│   │   └── main.py              # Entry point: arg parsing, app launch
│   │
│   ├── dictation/
│   │   ├── __init__.py
│   │   ├── tray.py              # Tray icon management, overlay window, menu
│   │   ├── hotkeys.py           # pynput listener, key matching, hold/toggle modes
│   │   ├── injection.py         # Clipboard-based text injection (ctypes + pynput)
│   │   └── main.py              # Entry point: config load, tray + hotkey startup
│   │
│   ├── models/
│   │   ├── __init__.py
│   │   ├── manager.py           # From model_manager.py
│   │   ├── tuned.py             # From tuned_models.py
│   │   └── downloader.py        # From download_models.py
│   │
│   └── plugins/
│       ├── __init__.py
│       ├── loader.py            # From plugin_loader.py
│       └── registry.py          # From plugin_registry.py
│
├── templates/                   # HTML files (markup only after CSS/JS extraction)
│   ├── operator.html
│   ├── display.html
│   ├── dictation.html
│   └── bidirectional.html
│
├── static/                      # Existing static/ plus extracted CSS/JS
│   ├── css/
│   │   ├── operator.css
│   │   ├── display.css
│   │   └── dictation.css
│   ├── js/
│   │   ├── operator.js
│   │   ├── display.js
│   │   ├── grid-editor.js       # Extracted from operator.html (grid + drag/drop)
│   │   └── dictation.js
│   ├── plugin_dispatcher.js     # Already exists
│   ├── plugin_grid.js           # Already exists
│   └── plugin_panel.css         # Already exists
│
├── plugins/                     # Unchanged
├── locales/                     # Unchanged
├── assets/                      # Unchanged
├── build/                       # Unchanged
├── dev/                         # Unchanged
├── scripts/                     # Unchanged
└── tests/                       # Expanded (see Section 7)
```

---

## 3. Code Standards

Every file produced by this refactor adheres to these standards uniformly.

### 3.1 Imports — PEP 8 ordering, one per line

```python
# Standard library
import asyncio
import json
import logging
import threading
from pathlib import Path
from typing import Any, Optional

# Third-party
import numpy as np
from fastapi import FastAPI, Request

# Local
from linguataxi.constants import SAMPLE_RATE
from linguataxi.settings import load_settings
```

No comma-separated imports. No wildcard imports. isort-compatible grouping.

### 3.2 Type Hints — All function signatures typed

Every function has parameter types and return type annotations. Use `dict`, `list`, `set` (lowercase, Python 3.9+) where possible. Use `from typing import Any, Optional` for complex types. Use `from __future__ import annotations` at top of every module for forward references.

### 3.3 Docstrings — Google style, every public function

```python
def broadcast_dictation(msg: dict[str, Any]) -> None:
    """Send a message to all connected dictation WebSocket clients.

    Args:
        msg: JSON-serializable dict with at minimum a "type" key.
    """
```

Every module has a one-line module docstring. Every class has a docstring describing its responsibility. Every public method has a docstring. Private methods (`_prefixed`) get docstrings when the behavior is non-obvious.

### 3.4 Error Handling — No silent swallowing

```python
# NEVER this:
except Exception:
    pass

# ALWAYS this:
except ConnectionError:
    log.warning("Client disconnected during broadcast")
except json.JSONDecodeError:
    log.error("Invalid JSON in config file: %s", config_path)
```

Rules:
- Catch the most specific exception possible.
- Always log with context (what operation, what input).
- Use `exc_info=True` for unexpected exceptions.
- Only use bare `except Exception` at outermost boundaries (main loops, signal handlers), and always log.

### 3.5 Logging — Consistent per-module pattern

```python
log = logging.getLogger(__name__)
```

At the top of every module. No `print()` statements for diagnostics. Use `log.debug()` / `log.info()` / `log.warning()` / `log.error()` appropriately.

### 3.6 Naming Conventions

- Functions and variables: `snake_case`
- Classes: `PascalCase`
- Module-level constants: `UPPER_SNAKE_CASE`
- Private functions/methods: `_leading_underscore`
- No Hungarian notation, no abbreviations except widely-known ones (`ws`, `db`, `config`)

### 3.7 Module Docstrings

Every `.py` file opens with a one-line summary:

```python
"""Audio capture and silence detection for the LinguaTaxi server."""
```

---

## 4. server.py Decomposition

### 4.1 linguataxi/constants.py

Consolidates constants from server.py, launcher.pyw, and tray_dictation.py:
- Audio: `SAMPLE_RATE`, `CHANNELS`, `DTYPE`, `CHUNK_DURATION`
- Detection: `SILENCE_THRESHOLD`, `SILENCE_DURATION`, `MAX_SEGMENT_DURATION`, `INTERIM_INTERVAL`, `MIN_SPEECH_DURATION`
- Ports: `DISPLAY_PORT`, `OPERATOR_PORT`, `EXTENDED_PORT`, `DICTATION_PORT`
- Language maps: `DEEPL_SOURCE_LANGS`, `DEEPL_TARGET_LANGS`, `WHISPER_LANG_MAP`
- Config defaults: `DEFAULT_CONFIG`

### 4.2 linguataxi/settings.py

Single source of truth for all settings management:
- `SETTINGS_DIR` — Platform-specific AppData/Library/config path
- `SETTINGS_FILE` — `launcher_settings.json` path
- `CONFIG_PATH` — `config.json` path
- `load_settings() -> dict[str, Any]` — Load launcher settings from disk
- `save_settings(data: dict[str, Any]) -> None` — Save launcher settings to disk
- `load_config() -> dict[str, Any]` — Load server config with fallback to defaults
- `save_config(config: dict[str, Any]) -> None` — Save server config

Both launcher.pyw and tray_dictation.py import from here instead of duplicating.

### 4.3 linguataxi/server/audio.py — AudioCapture class

```python
class AudioCapture:
    """Manages audio input stream and buffers raw audio for transcription."""

    def __init__(self, sample_rate: int, channels: int, ...) -> None: ...
    def start(self, device_index: Optional[int] = None) -> None: ...
    def stop(self) -> None: ...
    def restart(self, device_index: Optional[int] = None) -> None: ...
    def get_buffer(self) -> np.ndarray: ...
```

Encapsulates: sounddevice stream, audio ring buffer, silence detection, speaker change detection (`_check_speaker_change()`), buffer splitting logic.

### 4.4 linguataxi/server/backends/

**base.py:**
```python
class STTBackend(ABC):
    """Abstract base class for speech-to-text backends."""

    @abstractmethod
    def transcribe(self, audio: np.ndarray, language: Optional[str] = None) -> list[Segment]: ...

    @abstractmethod
    def transcribe_interim(self, audio: np.ndarray) -> Optional[str]: ...

    @abstractmethod
    def supports_interim(self) -> bool: ...

    @abstractmethod
    def cleanup(self) -> None: ...
```

**whisper.py:** `WhisperBackend(STTBackend)` — faster-whisper with CUDA. Wraps model loading, GPU detection, beam size config.

**mlx_whisper.py:** `MLXWhisperBackend(STTBackend)` — Apple Metal. Wraps MLX-specific model loading.

**vosk.py:** `VoskBackend(STTBackend)` — CPU fallback. Wraps the stateful recognizer, force-finalize on speaker change.

**`__init__.py`:** Factory function:
```python
def create_backend(name: str, **kwargs) -> STTBackend:
    """Create an STT backend by name ('whisper', 'mlx', 'vosk')."""
```

### 4.5 linguataxi/server/translation.py

```python
class TranslationManager:
    """Manages parallel translation across configured language slots."""

    def __init__(self, config: dict[str, Any]) -> None: ...
    def translate(self, text: str, source_lang: str) -> dict[str, str]: ...
    def update_config(self, config: dict[str, Any]) -> None: ...
    def shutdown(self) -> None: ...
```

Encapsulates: DeepL API calls, offline translation dispatch, thread pool, language slot management.

### 4.6 linguataxi/server/websocket.py

```python
class WebSocketManager:
    """Tracks connected WebSocket clients and broadcasts messages."""

    def __init__(self) -> None: ...
    async def connect(self, ws: WebSocket, group: str) -> None: ...
    async def disconnect(self, ws: WebSocket, group: str) -> None: ...
    async def broadcast(self, msg: dict[str, Any], groups: list[str]) -> None: ...
    async def broadcast_dictation(self, msg: dict[str, Any]) -> None: ...
```

Groups: `"display"`, `"extended"`, `"operator"`, `"dictation"`.

### 4.7 linguataxi/server/routes/dictation.py — HTTP 500 Fix

The dictation endpoint is extracted into its own route module with proper error handling. The HTTP 500 bug will be diagnosed and fixed here. The endpoint gets:
- Proper request validation
- Specific exception handling with logging
- The `broadcast_dictation` call verified to work in the correct async context

### 4.8 linguataxi/server/main.py

Wires everything together:
1. Parse CLI args (`--transcripts-dir`, `--backend`, etc.)
2. Load config via `linguataxi.settings`
3. Create `AudioCapture`, `STTBackend`, `TranslationManager`, `WebSocketManager`
4. Create FastAPI apps, register routes
5. Start uvicorn threads for each port
6. Register graceful shutdown handlers

---

## 5. launcher.pyw Decomposition

### 5.1 linguataxi/launcher/server_manager.py

```python
class ServerManager:
    """Manages the server subprocess lifecycle."""

    def __init__(self, on_log: Callable[[str], None]) -> None: ...
    def start(self, backend: str, mic_index: Optional[int] = None) -> None: ...
    def stop(self) -> None: ...
    @property
    def is_running(self) -> bool: ...
```

Encapsulates: subprocess spawning, stdout/stderr capture threads, Windows Job Object for child cleanup, health polling.

### 5.2 linguataxi/launcher/settings_panel.py

```python
class SettingsPanel(ctk.CTkFrame):
    """Settings UI for microphone, backend, and transcript configuration."""

    def __init__(self, parent: ctk.CTk, on_settings_changed: Callable) -> None: ...
```

### 5.3 linguataxi/launcher/batch_transcriber.py

```python
class BatchTranscriberPanel(ctk.CTkFrame):
    """UI for batch file transcription with translation."""

    def __init__(self, parent: ctk.CTk) -> None: ...
```

### 5.4 linguataxi/launcher/tray_manager.py

```python
class TrayManager:
    """System tray icon with minimize/restore/quit behavior."""

    def __init__(self, app: ctk.CTk) -> None: ...
    def setup(self) -> None: ...
    def quit(self) -> None: ...
```

Includes the force-exit watchdog logic.

### 5.5 linguataxi/launcher/app.py

The main class becomes a thin composition shell:

```python
class LinguaTaxiApp(ctk.CTk):
    """Main LinguaTaxi desktop application window."""

    def __init__(self) -> None:
        super().__init__()
        self.server = ServerManager(on_log=self._append_log)
        self.settings_panel = SettingsPanel(self, on_settings_changed=self._on_settings_changed)
        self.batch_panel = BatchTranscriberPanel(self)
        self.tray = TrayManager(self)
```

---

## 6. tray_dictation.py Decomposition

### 6.1 linguataxi/dictation/tray.py

Tray icon management, overlay window, menu construction. Uses `linguataxi.settings` for shared config.

### 6.2 linguataxi/dictation/hotkeys.py

pynput keyboard listener, hotkey matching, hold/toggle mode logic. Calls into `injection.py` for text insertion and communicates with server via HTTP POST (not WebSocket send).

### 6.3 linguataxi/dictation/injection.py

The clipboard-based text injection:
- Windows: ctypes `OpenClipboard`/`SetClipboardData` + pynput `Ctrl+V`
- macOS/Linux: pynput character-by-character fallback

---

## 7. HTML/CSS/JS Extraction

Each HTML file splits into three parts.

### 7.1 operator.html (~2,765 lines)

- `templates/operator.html` — Markup skeleton (~200 lines) with `<link href="/static/css/operator.css">` and `<script src="/static/js/operator.js">`
- `static/css/operator.css` — All `<style>` blocks extracted (~500 lines)
- `static/js/operator.js` — Main logic (~800 lines)
- `static/js/grid-editor.js` — Grid layout editor + drag/drop (~400 lines, separated because it's an independent subsystem)

### 7.2 display.html, dictation.html, bidirectional.html

Same pattern: markup + external CSS + external JS. Server routes updated to serve from `templates/` and mount `static/` for assets.

### 7.3 Static file serving

The FastAPI apps mount `static/` directory:
```python
app.mount("/static", StaticFiles(directory="static"), name="static")
```

HTML rendering functions updated to read from `templates/` instead of root.

---

## 8. Polish Pass

### 8.1 .gitignore

Add patterns for binaries that should never be tracked:
```
*.exe
*.zip
*.wav
```

Remove already-tracked binaries:
```bash
git rm --cached LinguaTaxi-CPU-Setup-1.0.0.exe
git rm --cached LinguaTaxi-GPU-Setup-1.0.0.exe
git rm --cached "live-caption (7).zip"
git rm --cached Test_Audio.wav
```

### 8.2 requirements.txt

Tighten version bounds:
```
numpy>=1.24.0,<2.0
Pillow>=10.0.0,<11.0
```

### 8.3 requirements-dev.txt (new)

```
pytest>=7.0.0,<9.0
pytest-asyncio>=0.21.0,<1.0
mypy>=1.0.0,<2.0
```

### 8.4 README.md

Add:
- Architecture overview section describing the package structure
- Python version badge
- License badge
- Brief troubleshooting section

### 8.5 Logging consistency

Every module uses `log = logging.getLogger(__name__)`. No `print()` for diagnostics. Root logger configured once in each entry point's `main()`.

---

## 9. Execution Order

1. **Create branch** `refactor/professionalize` off `master`
2. **Foundation** — Create `linguataxi/` package, `constants.py`, `settings.py`, `types.py`
3. **server.py** — Extract into `linguataxi/server/` submodules; fix dictation HTTP 500
4. **launcher.pyw** — Extract into `linguataxi/launcher/` submodules
5. **tray_dictation.py** — Extract into `linguataxi/dictation/` submodules
6. **Utility modules** — Move `model_manager.py`, `tuned_models.py`, `plugin_loader.py`, `plugin_registry.py`, `download_models.py` into `linguataxi/models/` and `linguataxi/plugins/`
7. **HTML/CSS/JS** — Split all HTML files, create `templates/` and `static/css/` + `static/js/`
8. **Polish** — README, requirements, .gitignore, remove tracked binaries
9. **Verification** — Run the app, verify all three ports serve correctly, verify dictation works, verify installer entry points still function

---

## 10. Risk Mitigation

- **Branch isolation.** All work on `refactor/professionalize`. Master stays untouched.
- **Incremental commits.** Each module extraction is a separate commit. If something breaks, we can bisect.
- **Entry point stability.** Root files stay as thin shells — the installer never needs updating.
- **Behavioral equivalence.** No feature changes. Every refactored path must produce the same output as before.
- **Manual verification.** After each major extraction (server, launcher, dictation), start the app and verify basic functionality before proceeding.
