# LinguaTaxi Code Professionalization — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refactor the LinguaTaxi codebase into a clean `linguataxi/` package with professional code standards — type hints, docstrings, proper error handling, and modular architecture — while keeping root entry points stable for the installer.

**Architecture:** Extract internals from 3 monolithic files (server.py 2994 lines, launcher.pyw 3672 lines, tray_dictation.py 913 lines) into focused modules inside a `linguataxi/` package. Root files become thin shells. HTML files split into templates + static CSS/JS.

**Tech Stack:** Python 3.11+, FastAPI, uvicorn, customtkinter, pystray, pynput, faster-whisper, Vosk, sounddevice

**Spec:** `docs/superpowers/specs/2026-05-19-code-professionalization-design.md`

---

## Shared State Strategy

server.py has 11 categories of shared mutable state (globals). The extraction strategy for each:

| Global State | Current Location | Extraction Target | Access Pattern |
|---|---|---|---|
| `stt_backend` | server.py:301 | `server/main.py` module-level, accessor `get_backend()` | Import from `server.main` |
| `config` + `_config_lock` | server.py:198-205 | `settings.py` with `get_config()` / `update_config()` | Thread-safe accessors |
| `_translate_pool` + `_translate_gen` | server.py:323,320 | `server/translation.py` module-level | Encapsulated in module |
| `translation_paused`, `captioning_paused`, `dictation_active` | server.py:310-313 | `server/state.py` (new) — simple module with getters/setters | Import from `server.state` |
| `_line_id`, `_recent_lines` | server.py:316-319 | `server/transcripts.py` module-level | Encapsulated in module |
| `display_clients`, `operator_clients`, etc. | server.py:303-306 | `server/websocket.py` module-level | Encapsulated in module |
| `_model_lock` | server.py:318 | `server/backends/__init__.py` | Import from backends |
| `_dictation_loop` | server.py:314 | `server/main.py` | Set during startup |
| `plugin_dispatcher` | server.py:213 | `server/app.py` module-level | Import from `server.app` |
| `_sources`, `_sources_lock` | server.py:370-371 | `server/audio.py` module-level | Encapsulated in module |
| `shutdown_event`, `mic_restart_event` | server.py:307-308 | `server/state.py` | Import from `server.state` |

**Note:** `server/state.py` is added to the spec as a thin module holding the global flags and events. This avoids circular imports between audio, translation, and routes modules that all need to read these flags.

---

## Task 1: Create Branch and Package Skeleton

**Files:**
- Create: `linguataxi/__init__.py`
- Create: `linguataxi/server/__init__.py`
- Create: `linguataxi/server/backends/__init__.py`
- Create: `linguataxi/server/routes/__init__.py`
- Create: `linguataxi/launcher/__init__.py`
- Create: `linguataxi/dictation/__init__.py`
- Create: `linguataxi/models/__init__.py`
- Create: `linguataxi/plugins/__init__.py`

- [ ] **Step 1: Create and checkout branch**

```bash
git checkout -b refactor/professionalize master
```

- [ ] **Step 2: Create package directories and `__init__.py` files**

```bash
mkdir -p linguataxi/server/backends linguataxi/server/routes linguataxi/launcher linguataxi/dictation linguataxi/models linguataxi/plugins
```

Create `linguataxi/__init__.py`:
```python
"""LinguaTaxi — Live Caption & Translation."""

__version__ = "1.0.3"
```

Create empty `__init__.py` in each subpackage with a one-line module docstring:
- `linguataxi/server/__init__.py`: `"""LinguaTaxi server package."""`
- `linguataxi/server/backends/__init__.py`: `"""Speech-to-text backend implementations."""`
- `linguataxi/server/routes/__init__.py`: `"""HTTP and WebSocket route handlers."""`
- `linguataxi/launcher/__init__.py`: `"""Desktop launcher GUI package."""`
- `linguataxi/dictation/__init__.py`: `"""Global dictation tray application."""`
- `linguataxi/models/__init__.py`: `"""Model management utilities."""`
- `linguataxi/plugins/__init__.py`: `"""Plugin loading and registry."""`

- [ ] **Step 3: Commit**

```bash
git add linguataxi/
git commit -m "[refactor] create linguataxi package skeleton"
```

---

## Task 2: Extract Foundation Modules (constants, settings, types, state)

**Files:**
- Create: `linguataxi/constants.py`
- Create: `linguataxi/settings.py`
- Create: `linguataxi/types.py`
- Create: `linguataxi/server/state.py`
- Modify: `server.py` — replace inlined constants/settings with imports
- Modify: `tray_dictation.py` — replace duplicated settings with imports

### Step-by-step:

- [ ] **Step 1: Create `linguataxi/constants.py`**

Extract from `server.py` lines 85-187 (language maps, color palette, bg options, font options, default config) and from `launcher.pyw` lines 15-76 (batch lang maps, engine names, app metadata, platform detection, paths).

The file should contain:
- All `DEEPL_*` language dicts (server.py:86-115)
- `VOSK_DIR_LANGS` (server.py:117-122)
- `COLOR_PALETTE` (server.py:124-137)
- `BG_OPTIONS` (server.py:139-144)
- `FONT_OPTIONS` (server.py:146-156)
- `DEFAULT_CONFIG` (server.py:159-187)
- Audio constants: `SAMPLE_RATE`, `CHANNELS`, `DTYPE`, `CHUNK_DURATION`, `SILENCE_THRESHOLD`, `SILENCE_DURATION`, `MAX_SEGMENT_DURATION`, `INTERIM_INTERVAL`, `MIN_SPEECH_DURATION` (server.py:289-297)
- Port constants: `DISPLAY_PORT = 3000`, `OPERATOR_PORT = 3001`, `EXTENDED_PORT = 3002`, `DICTATION_PORT = 3005` (currently hardcoded in server.py main() at line 2828+)
- `BATCH_DEEPL_LANGS`, `BATCH_OPUS_LANGS`, `BATCH_M2M_LANGS`, `BATCH_ENGINE_NAMES` (launcher.pyw:15-53)
- `APP_NAME`, `APP_FULL`, `VERSION`, `GITHUB_REPO` (launcher.pyw:57-76)
- `IS_WIN`, `IS_MAC` platform booleans (launcher.pyw:61-62)
- `APP_DIR` computation (launcher.pyw:65-70)
- `GRACE_MS = 750` (tray_dictation.py:35)

Every constant must have a type annotation. Group by category with section comments. Module docstring: `"""Shared constants, language maps, and default configuration."""`

- [ ] **Step 2: Create `linguataxi/settings.py`**

Extract from `server.py` lines 66-82 (paths), 189-237 (config load/save/speaker config) and from `tray_dictation.py` lines 12-62 (duplicated settings). This becomes the single source of truth.

Functions to include (all with type hints and docstrings):
- `SETTINGS_DIR` — platform-specific path (from launcher.pyw:92-98 and tray_dictation.py:25-29)
- `SETTINGS_FILE` — path to `launcher_settings.json`
- `CONFIG_PATH` — path to `config.json`
- `UPLOADS_DIR`, `MODELS_DIR`, `TRANSCRIPTS_DIR` — data directories (server.py:77-83)
- `load_settings() -> dict[str, Any]` — from launcher.pyw:121-137 (includes migration logic)
- `save_settings(data: dict[str, Any]) -> None` — from launcher.pyw:140-146
- `get_setting(key: str, default: Any = None) -> Any` — from tray_dictation.py:56-57
- `set_setting(key: str, value: Any) -> None` — from tray_dictation.py:59-62
- `load_config() -> dict[str, Any]` — from server.py:189-196
- `save_config(cfg: dict[str, Any]) -> None` — from server.py:200-205
- `save_speaker_config() -> None` — from server.py:215-226
- `load_speaker_config() -> None` — from server.py:228-237

Note: `save_speaker_config` and `load_speaker_config` reference `_sources` and `config` globals from server.py. For now, these two functions should accept their dependencies as parameters rather than importing globals:
```python
def save_speaker_config(sources: list, config: dict[str, Any], config_lock: threading.Lock) -> None:
```

- [ ] **Step 3: Create `linguataxi/types.py`**

Define shared type aliases and dataclasses:
```python
"""Shared type definitions for LinguaTaxi."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

# Type aliases
ConfigDict = dict[str, Any]
LangCode = str
SpeakerName = str
```

This file will grow as extraction proceeds and common types become apparent.

- [ ] **Step 4: Create `linguataxi/server/state.py`**

This thin module holds the global flags and events that are read by multiple server submodules (audio, translation, routes, websocket). Centralizing them here avoids circular imports.

```python
"""Global server state flags and coordination events."""

from __future__ import annotations

import threading

translation_paused: bool = True
captioning_paused: bool = True
dictation_active: bool = False
save_transcripts: bool = True

shutdown_event = threading.Event()
mic_restart_event = threading.Event()
```

- [ ] **Step 5: Update `server.py` to import from new modules**

At the top of server.py, add:
```python
from linguataxi.constants import (
    DEEPL_SOURCE_LANGS, DEEPL_TARGET_LANGS, DEEPL_TO_WHISPER,
    DEEPL_TARGET_DEFAULTS, VOSK_DIR_LANGS, COLOR_PALETTE, BG_OPTIONS,
    FONT_OPTIONS, DEFAULT_CONFIG, SAMPLE_RATE, CHANNELS, DTYPE,
    CHUNK_DURATION, SILENCE_THRESHOLD, SILENCE_DURATION,
    MAX_SEGMENT_DURATION, INTERIM_INTERVAL, MIN_SPEECH_DURATION,
)
from linguataxi.settings import (
    CONFIG_PATH, UPLOADS_DIR, MODELS_DIR, TRANSCRIPTS_DIR,
    load_config, save_config,
)
from linguataxi.server.state import (
    translation_paused, captioning_paused, dictation_active,
    shutdown_event, mic_restart_event,
)
```

Remove the corresponding inline definitions from server.py. The functions and constants that were moved should be deleted from server.py, replaced by the imports above. Leave everything else in server.py for now — subsequent tasks will extract further.

- [ ] **Step 6: Update `tray_dictation.py` to import from shared modules**

Replace lines 12-62 (duplicated paths, settings functions) with:
```python
from linguataxi.constants import DICTATION_PORT, GRACE_MS, IS_WIN, APP_DIR
from linguataxi.settings import (
    SETTINGS_DIR, SETTINGS_FILE, load_settings, save_settings,
    get_setting, set_setting,
)
```

Delete the duplicated `IS_WIN`, `APP_DIR`, `SERVER_PY`, `SETTINGS_DIR`, `SETTINGS_FILE`, `DICTATION_PORT`, `GRACE_MS`, `DEFAULT_TRANSCRIPTS`, `load_settings`, `save_settings`, `get_setting`, `set_setting` from tray_dictation.py.

- [ ] **Step 7: Verify imports work**

```bash
python -c "from linguataxi.constants import SAMPLE_RATE; print(SAMPLE_RATE)"
python -c "from linguataxi.settings import load_config; print(type(load_config()))"
python -c "from linguataxi.server.state import shutdown_event; print(shutdown_event)"
```

- [ ] **Step 8: Commit**

```bash
git add linguataxi/ server.py tray_dictation.py
git commit -m "[refactor] extract constants, settings, types, and state modules"
```

---

## Task 3: Extract Speech Backends

**Files:**
- Create: `linguataxi/server/backends/base.py`
- Create: `linguataxi/server/backends/whisper.py`
- Create: `linguataxi/server/backends/vosk.py`
- Create: `linguataxi/server/backends/mlx_whisper.py`
- Modify: `linguataxi/server/backends/__init__.py` — add factory function
- Modify: `server.py` — remove backend classes, import from package

- [ ] **Step 1: Create `linguataxi/server/backends/base.py`**

Extract `SpeechBackend` ABC from server.py:416-422. Add type hints and docstring.

```python
"""Abstract base class for speech-to-text backends."""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod


class SpeechBackend(ABC):
    """Base interface for all speech-to-text backends."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable backend identifier."""

    @abstractmethod
    def process_audio_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Start the backend's audio processing loop."""

    def cleanup(self) -> None:
        """Release backend resources. Override if needed."""
```

- [ ] **Step 2: Create `linguataxi/server/backends/whisper.py`**

Extract `WhisperBackend` class from server.py:688-731. This class needs to import:
- `SpeechBackend` from `.base`
- Audio processing functions from `linguataxi.server.audio` (which doesn't exist yet)

**Important:** Since `server/audio.py` hasn't been extracted yet, the Whisper backend will initially reference the audio functions that still live in server.py. The import will be updated in Task 4 when audio.py is extracted. For now, use a deferred import pattern:

```python
"""faster-whisper GPU-accelerated speech-to-text backend."""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import TYPE_CHECKING, Optional

import numpy as np

from linguataxi.server.backends.base import SpeechBackend

if TYPE_CHECKING:
    from faster_whisper import WhisperModel

log = logging.getLogger(__name__)
```

Move the `WhisperBackend` class (server.py:688-731), adding full type hints and docstrings to `__init__`, `name`, `_transcribe`, and `process_audio_loop`.

- [ ] **Step 3: Create `linguataxi/server/backends/vosk.py`**

Extract `VoskBackend` class (server.py:750-956) and helper `_load_vosk_bidir_model` (server.py:733-748). Same pattern — add type hints, docstrings, proper error handling (replace silent `except Exception: pass` with logged exceptions).

- [ ] **Step 4: Create `linguataxi/server/backends/mlx_whisper.py`**

Extract `MLXWhisperBackend` class (server.py:958-1011). Add type hints and docstrings.

- [ ] **Step 5: Update `linguataxi/server/backends/__init__.py`** with factory:

```python
"""Speech-to-text backend implementations."""

from __future__ import annotations

import threading
from typing import Any, Optional

from linguataxi.server.backends.base import SpeechBackend

model_lock = threading.RLock()


def create_backend(name: str, **kwargs: Any) -> SpeechBackend:
    """Create an STT backend by name.

    Args:
        name: One of 'whisper', 'vosk', 'mlx'.
        **kwargs: Backend-specific configuration.
    """
    if name == "whisper":
        from linguataxi.server.backends.whisper import WhisperBackend
        return WhisperBackend(**kwargs)
    elif name == "vosk":
        from linguataxi.server.backends.vosk import VoskBackend
        return VoskBackend(**kwargs)
    elif name == "mlx":
        from linguataxi.server.backends.mlx_whisper import MLXWhisperBackend
        return MLXWhisperBackend(**kwargs)
    else:
        raise ValueError(f"Unknown backend: {name!r}")
```

- [ ] **Step 6: Update `server.py`** — remove the three backend classes and `_load_vosk_bidir_model`. Add imports:
```python
from linguataxi.server.backends import create_backend, model_lock
from linguataxi.server.backends.base import SpeechBackend
from linguataxi.server.backends.whisper import WhisperBackend
```

Update references to `_model_lock` → `model_lock` (imported from backends).

- [ ] **Step 7: Verify**

```bash
python -c "from linguataxi.server.backends import create_backend; print('OK')"
```

- [ ] **Step 8: Commit**

```bash
git add linguataxi/server/backends/ server.py
git commit -m "[refactor] extract speech backends into linguataxi.server.backends"
```

---

## Task 4: Extract Audio Capture Module

**Files:**
- Create: `linguataxi/server/audio.py`
- Modify: `server.py` — remove audio functions, import from module

- [ ] **Step 1: Create `linguataxi/server/audio.py`**

Extract from server.py:
- `AudioSource` class (line 347-372)
- `get_source()` (line 375)
- `add_source()` (line 384)
- `remove_source()` (line 394)
- `_sources`, `_sources_lock`, `_transcription_queue` globals (line 370-372)
- `_check_speaker_change()` (line 426)
- `_transcription_worker()` (line 471)
- `_get_speaker_lang()` (line 515)
- `_detect_segment_lang()` (line 523)
- `_voice_id_try_enroll()` (line 530)
- `_voice_id_try_identify()` (line 562)
- `_buffer_audio_loop()` (line 612)
- `_make_audio_callback()` (line 1182)
- `_open_input_stream()` (line 1191)
- `start_source_capture()` (line 1248)
- `start_audio_capture()` (line 1279)

Module docstring: `"""Audio capture, source management, and silence detection."""`

All functions get type hints, docstrings, and specific exception handling (replace `except Exception: pass` with logged exceptions).

The module-level globals become:
```python
_sources: list[AudioSource] = []
_sources_lock = threading.Lock()
_transcription_queue: queue.Queue = queue.Queue(maxsize=16)
```

Functions that reference other server globals (`config`, `stt_backend`, `translation_paused`, etc.) should import them:
```python
from linguataxi.server.state import captioning_paused, shutdown_event, mic_restart_event
```

For `config` access, import from settings:
```python
from linguataxi.settings import load_config
```

For `stt_backend` and `_broadcast_final` references, use late imports inside functions to avoid circular imports (these modules don't exist yet).

- [ ] **Step 2: Update `server.py`** — remove all extracted functions/classes, add:
```python
from linguataxi.server.audio import (
    AudioSource, get_source, add_source, remove_source,
    start_source_capture, start_audio_capture,
    _sources, _sources_lock, _transcription_queue,
    _buffer_audio_loop, _transcription_worker,
    _check_speaker_change, _make_audio_callback, _open_input_stream,
)
```

- [ ] **Step 3: Verify**

```bash
python -c "from linguataxi.server.audio import AudioSource, get_source; print('OK')"
```

- [ ] **Step 4: Commit**

```bash
git add linguataxi/server/audio.py server.py
git commit -m "[refactor] extract audio capture into linguataxi.server.audio"
```

---

## Task 5: Extract WebSocket, Transcripts, and Translation Modules

**Files:**
- Create: `linguataxi/server/websocket.py`
- Create: `linguataxi/server/transcripts.py`
- Create: `linguataxi/server/translation.py`
- Modify: `server.py`

- [ ] **Step 1: Create `linguataxi/server/websocket.py`**

Extract from server.py:
- Client sets: `display_clients`, `extended_clients`, `operator_clients`, `dictation_clients` (lines 303-306)
- `_bc()` (line 1014)
- `broadcast_all()` (line 1027)
- `broadcast_dictation()` (line 1036)

Module docstring: `"""WebSocket client tracking and message broadcast."""`

- [ ] **Step 2: Create `linguataxi/server/transcripts.py`**

Extract from server.py:
- `_session_stamp` (line 315)
- `_line_id`, `_line_id_lock` (line 316-317)
- `_recent_lines` (line 319)
- `_save_line()` (line 1045)
- `_next_line_id()` (line 1059)
- `_store_recent_line()` (line 1065)
- `_broadcast_final()` (line 1069)

Module docstring: `"""Transcript saving and final caption broadcast."""`

- [ ] **Step 3: Create `linguataxi/server/translation.py`**

Extract from server.py:
- `get_deepl_url()` (line 240)
- `_translate_deepl()` (line 243)
- `translate_text()` (line 264)
- `_translate_pool` (line 323)
- `_translate_gen`, `_translate_gen_lock` (lines 320-321)
- `_translate_all()` (line 1099)
- `_do_translate()` (line 1131)

Module docstring: `"""Translation management with DeepL and offline model support."""`

- [ ] **Step 4: Update `server.py`** — remove extracted functions, add imports from the three new modules.

- [ ] **Step 5: Verify and commit**

```bash
python -c "from linguataxi.server.websocket import broadcast_all; print('OK')"
python -c "from linguataxi.server.transcripts import _save_line; print('OK')"
python -c "from linguataxi.server.translation import translate_text; print('OK')"
git add linguataxi/server/ server.py
git commit -m "[refactor] extract websocket, transcripts, and translation modules"
```

---

## Task 6: Extract Server Routes

**Files:**
- Create: `linguataxi/server/routes/display.py`
- Create: `linguataxi/server/routes/operator.py`
- Create: `linguataxi/server/routes/dictation.py`
- Create: `linguataxi/server/routes/transcribe.py`
- Modify: `server.py`

- [ ] **Step 1: Create `linguataxi/server/routes/display.py`**

Extract from server.py:
- `_render_display_html()` (line 1328)
- All `@display_app` routes (lines 1338-1427): `d_index`, `bidirectional_page`, `d_uploads`, `d_config`, `d_get_locale`, `d_ws`, `d_get_grids`
- All `@extended_app` routes (lines 1395-1427): `e_index`, `e_uploads`, `e_config`, `e_ws`, `e_get_grids`

The routes need the FastAPI app instances. Use a registration pattern — define a function that takes the app and registers routes:

```python
"""Display and extended display route handlers."""

from __future__ import annotations

from fastapi import FastAPI, WebSocket


def register_display_routes(app: FastAPI, extended_app: FastAPI) -> None:
    """Register all display and extended display routes."""

    @app.get("/")
    async def d_index() -> HTMLResponse:
        ...
```

- [ ] **Step 2: Create `linguataxi/server/routes/operator.py`**

Extract ALL `@operator_app` routes from server.py (lines 1475-2620). This is the largest route file (~1100 lines of endpoints). Includes:
- Plugin management (lines 1485-1538)
- Config endpoints (lines 1561-1640)
- Full config update (lines 1668-1781)
- Footer management (lines 1781-1805)
- Tuned models API (lines 1806-1957)
- Offline translation API (lines 1958-2003)
- Mic management (lines 2004-2042)
- Source management (lines 2045-2112)
- Vosk models (line 2113)
- Voice ID (lines 2131-2175)
- Plugin marketplace (lines 2177-2267)
- Display grids (lines 2268-2337)
- Operator WebSocket (line 2522)

Same registration pattern:
```python
def register_operator_routes(app: FastAPI) -> None:
    """Register all operator panel routes."""
```

- [ ] **Step 3: Create `linguataxi/server/routes/dictation.py`**

Extract all `@dictation_app` routes (lines 2624-2721). **Fix the HTTP 500 bug** in `dict_set_active` — the error logging added in the last session will now surface the actual exception. Ensure `broadcast_dictation` is properly awaited in the async context.

```python
def register_dictation_routes(app: FastAPI) -> None:
    """Register dictation mode routes."""
```

- [ ] **Step 4: Create `linguataxi/server/routes/transcribe.py`**

Extract from server.py:
- `BatchTranslationSlot` model (line 2348)
- `BatchRequest` model (line 2352)
- Batch transcription endpoint (line 2365)
- Live transcription endpoints (lines 2457-2520)

```python
def register_transcribe_routes(app: FastAPI) -> None:
    """Register file transcription routes."""
```

- [ ] **Step 5: Update `server.py`** — remove all route handlers, replace with registration calls:
```python
from linguataxi.server.routes.display import register_display_routes
from linguataxi.server.routes.operator import register_operator_routes
from linguataxi.server.routes.dictation import register_dictation_routes
from linguataxi.server.routes.transcribe import register_transcribe_routes

register_display_routes(display_app, extended_app)
register_operator_routes(operator_app)
register_dictation_routes(dictation_app)
register_transcribe_routes(operator_app)
```

- [ ] **Step 6: Verify and commit**

```bash
python -c "from linguataxi.server.routes.display import register_display_routes; print('OK')"
git add linguataxi/server/routes/ server.py
git commit -m "[refactor] extract server routes into linguataxi.server.routes"
```

---

## Task 7: Extract Server App and Main Entry Point

**Files:**
- Create: `linguataxi/server/app.py`
- Create: `linguataxi/server/main.py`
- Modify: `server.py` — becomes thin shell

- [ ] **Step 1: Create `linguataxi/server/app.py`**

Extract from server.py:
- FastAPI app creation: `display_app`, `extended_app`, `operator_app`, `dictation_app` (lines 298-301)
- Plugin loading & static file mounting (lines 1434-1473)
- `_make_plugin_file_handler()` (line 1448)
- `_get_registry()` (line 330)
- `_style_config()` (line 1291)
- `_font_css()` (line 1308)
- `_translations_for_slots()` (line 1313)
- `_snapshot_display_grids()` (line 2268)
- `plugin_dispatcher`, `use_plugins` globals (line 210-213)
- `EDITION` reading (line 325-328)

```python
"""FastAPI application creation, plugin integration, and static file serving."""
```

- [ ] **Step 2: Create `linguataxi/server/main.py`**

Extract from server.py:
- `setup_events()` (line 2724)
- `detect_gpu()` (line 2752)
- `detect_apple_silicon()` (line 2773)
- `resolve_backend()` (line 2789)
- `list_mics()` (line 2817)
- `run_server()` (line 2825)
- `main()` (line 2828)
- `_graceful_shutdown()` (line 2951)
- `_shutdown_and_exit()` (line 2982)
- CUDA library path setup (lines 14-47)

```python
"""Server entry point: startup, shutdown, GPU detection, and backend selection."""
```

- [ ] **Step 3: Reduce `server.py` to thin shell**

```python
"""LinguaTaxi server entry point."""

from linguataxi.server.main import main

if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Verify the server starts**

```bash
python server.py --help
```

If `--help` works (argparse runs without import errors), the extraction is wired correctly. Full server start requires audio hardware and models, so `--help` is the smoke test.

- [ ] **Step 5: Commit**

```bash
git add linguataxi/server/ server.py
git commit -m "[refactor] extract server app and main into linguataxi.server"
```

---

## Task 8: Extract Launcher Modules

**Files:**
- Create: `linguataxi/launcher/server_manager.py`
- Create: `linguataxi/launcher/settings_panel.py`
- Create: `linguataxi/launcher/batch_transcriber.py`
- Create: `linguataxi/launcher/tray_manager.py`
- Create: `linguataxi/launcher/model_download.py`
- Create: `linguataxi/launcher/app.py`
- Create: `linguataxi/launcher/main.py`
- Modify: `launcher.pyw` — becomes thin shell

This is the second-largest extraction (3672 lines). All methods belong to the single `LinguaTaxiApp` class and share `self` state. The decomposition uses composition — new classes receive a reference to the app or its relevant state.

- [ ] **Step 1: Create `linguataxi/launcher/server_manager.py`**

Extract from launcher.pyw:
- `_create_win_job()` (line 234-282)
- `_find_python()` (line 2128-2136)
- `_build_server_cmd()` (line 820-843)
- `_start_server()` (line 2140-2199)
- `_stop_server()` (line 2200-2252)
- `_read_server_output()` (line 2841-2865)
- `_check_server_readiness()` (line 2945-2957)

Create a `ServerManager` class:
```python
"""Server subprocess lifecycle management."""

from __future__ import annotations

import logging
import os
import subprocess
import threading
from pathlib import Path
from typing import Any, Callable, Optional

log = logging.getLogger(__name__)


class ServerManager:
    """Manages the LinguaTaxi server subprocess."""

    def __init__(self, app_dir: Path, on_log: Callable[[str, str], None]) -> None:
        """Initialize server manager.

        Args:
            app_dir: Directory containing server.py.
            on_log: Callback for log messages (message, tag).
        """
```

Convert all `self._xxx` references from the original app methods to use the ServerManager's own state.

- [ ] **Step 2: Create `linguataxi/launcher/settings_panel.py`**

Extract from launcher.pyw:
- `_add_source_row()` (line 714)
- `_remove_source_row()` (line 758)
- `_update_add_button()` (line 770)
- `_refresh_source_combo()` (line 780)
- `_get_source_indices()` (line 796)
- `_browse_tdir()` (line 3060)
- `_save_current_settings()` (line 3069)

```python
"""Settings UI panel for audio sources, backend, and transcript configuration."""
```

- [ ] **Step 3: Create `linguataxi/launcher/model_download.py`**

Extract from launcher.pyw:
- `_needs_model_download()` (line 847)
- `_download_models()` (line 865)
- `_get_tuned_model_info()` (line 960)
- `_show_tuned_models_dialog()` (line 976)
- `_show_vosk_models_dialog()` (line 1246)
- `_get_offline_translate_info()` (line 1484)
- `_show_offline_translate_dialog()` (line 1500)
- `_show_model_manager_dialog()` (line 1840)

```python
"""Model download and management dialogs."""
```

- [ ] **Step 4: Create `linguataxi/launcher/batch_transcriber.py`**

Extract from launcher.pyw:
- `_transcribe_file()` (line 2254)
- `_show_transcribe_dialog()` (line 2260-2840)

```python
"""Batch file transcription UI with translation support."""
```

- [ ] **Step 5: Create `linguataxi/launcher/tray_manager.py`**

Extract from launcher.pyw:
- `_setup_tray()` (line 3518)
- `_minimize_to_tray()` (line 3589)
- `_restore_from_tray()` (line 3598)
- `_quit_from_tray()` (line 3606)

```python
"""System tray icon with minimize, restore, and quit behavior."""
```

- [ ] **Step 6: Create `linguataxi/launcher/app.py`**

The `LinguaTaxiApp` class remains here but becomes thin — it delegates to the extracted components. Keep in this file:
- `__init__()` — creates all sub-components
- `_setup_window()` (line 329)
- `_build_ui()` (line 367) — calls sub-components to populate UI sections
- `_draw_dot()` (line 708)
- `_update_ui_state()` (line 2915)
- `_poll_log_queue()` (line 2867)
- `_append_log()` / `_log_system()` / `_log_error()` (line 3082-3097)
- `_open_browser_when_ready()` (line 2959)
- `_open_operator()` through `_open_dictation()` (line 3002-3009)
- `_open_bidirectional()` (line 3011)
- `_open_transcripts_dir()` (line 3048)
- `_show_about()` (line 3101)
- Update checking methods (lines 3129-3397)
- Language switching (lines 3401-3514)
- `_on_close()` (line 3630)
- `list_mics()` module function (line 215)

- [ ] **Step 7: Create `linguataxi/launcher/main.py`**

```python
"""Launcher entry point."""

from __future__ import annotations

import atexit
import logging
import sys

log = logging.getLogger(__name__)


def main() -> None:
    """Launch the LinguaTaxi desktop application."""
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("LinguaTaxi.Desktop")

    from linguataxi.launcher.app import LinguaTaxiApp

    app = LinguaTaxiApp()

    def _cleanup() -> None:
        if hasattr(app, "server") and app.server.is_running:
            app.server.stop()

    atexit.register(_cleanup)
    app.mainloop()
```

- [ ] **Step 8: Reduce `launcher.pyw` to thin shell**

```python
"""LinguaTaxi desktop launcher entry point."""

from linguataxi.launcher.main import main

if __name__ == "__main__":
    main()
```

- [ ] **Step 9: Verify**

```bash
python -c "from linguataxi.launcher.main import main; print('OK')"
```

- [ ] **Step 10: Commit**

```bash
git add linguataxi/launcher/ launcher.pyw
git commit -m "[refactor] extract launcher into linguataxi.launcher"
```

---

## Task 9: Extract Dictation Tray Modules

**Files:**
- Create: `linguataxi/dictation/tray.py`
- Create: `linguataxi/dictation/hotkeys.py`
- Create: `linguataxi/dictation/injection.py`
- Create: `linguataxi/dictation/main.py`
- Modify: `tray_dictation.py` — becomes thin shell

- [ ] **Step 1: Create `linguataxi/dictation/injection.py`**

Extract from tray_dictation.py:
- `_inject_text()` (line 487-540) — the clipboard paste logic
- `_kb_controller` global

```python
"""Text injection into focused application via clipboard paste."""
```

- [ ] **Step 2: Create `linguataxi/dictation/hotkeys.py`**

Extract from tray_dictation.py:
- `_reload_hotkey_cache()` (around line 640)
- `_is_modifier()`, `_key_to_code()`, `_match_hotkey()` (lines 660-687)
- `_start_hotkey_listener()` and its nested functions (line 689):
  - `_http_set_active()` (line 695)
  - `_activate_dictation()` (line 710)
  - `_deactivate_dictation()` (line 724)
  - `on_press()` (line 730)
  - `on_release()` (line 766)
- Hotkey configuration dialog code (lines 813-913)

```python
"""Hotkey listener and key matching for dictation activation."""
```

- [ ] **Step 3: Create `linguataxi/dictation/tray.py`**

Extract from tray_dictation.py:
- `_make_icon()` (line 66)
- Icon constants: `ICON_GREY`, `ICON_GREEN`, `ICON_RED` (lines 87-89)
- `_update_tray_icon()` (around line 95)
- `_show_overlay()` / `_hide_overlay()` (overlay window functions)
- `_on_quit()` (line 293)
- Tray menu construction and `icon.run()` setup
- WebSocket connection management: `_ws_thread()`, `_on_ws_message()`, etc.

```python
"""System tray icon, overlay window, and WebSocket connection management."""
```

- [ ] **Step 4: Create `linguataxi/dictation/main.py`**

```python
"""Dictation tray application entry point."""

from __future__ import annotations

import logging
import sys


def main() -> None:
    """Start the dictation tray application."""
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    if sys.platform == "win32":
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "LinguaTaxi.Dictation"
        )

    from linguataxi.dictation.tray import run_tray
    run_tray()
```

- [ ] **Step 5: Reduce `tray_dictation.py` to thin shell**

```python
"""LinguaTaxi global dictation tray entry point."""

from linguataxi.dictation.main import main

if __name__ == "__main__":
    main()
```

- [ ] **Step 6: Verify and commit**

```bash
python -c "from linguataxi.dictation.main import main; print('OK')"
git add linguataxi/dictation/ tray_dictation.py
git commit -m "[refactor] extract dictation tray into linguataxi.dictation"
```

---

## Task 10: Move Utility Modules

**Files:**
- Create: `linguataxi/models/manager.py` (from `model_manager.py`)
- Create: `linguataxi/models/tuned.py` (from `tuned_models.py`)
- Create: `linguataxi/models/downloader.py` (from `download_models.py`)
- Create: `linguataxi/plugins/loader.py` (from `plugin_loader.py`)
- Create: `linguataxi/plugins/registry.py` (from `plugin_registry.py`)
- Modify: root entry points `download_models.py`
- Modify: `server.py` imports (if any remain)

- [ ] **Step 1: Move each utility module**

For each file, the process is:
1. Copy contents to new location inside `linguataxi/`
2. Add module docstring, type hints on all public functions, docstrings
3. Fix imports (change `import tuned_models` → `from linguataxi.models import tuned`)
4. Convert root file to thin shell

Example for `model_manager.py` → `linguataxi/models/manager.py`:
```python
"""Speech model management and availability detection."""
# ... moved and cleaned up contents ...
```

Root `model_manager.py` is no longer an entry point (not referenced by installer), so it can simply be deleted after confirming all imports reference the new location. If anything imports it, update those imports.

For `download_models.py` (referenced by installer):
```python
"""Model downloader entry point."""

from linguataxi.models.downloader import main

if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Update all internal imports**

Search for `import tuned_models`, `import offline_translate`, `import transcribe_file`, `import voice_id`, `from plugin_loader`, `from plugin_registry` across the codebase and update to `from linguataxi.models.xxx` or `from linguataxi.plugins.xxx`.

- [ ] **Step 3: Verify and commit**

```bash
python -c "from linguataxi.models.manager import ModelManager; print('OK')" 2>/dev/null || echo "No ModelManager class"
python -c "from linguataxi.plugins.loader import PluginDispatcher; print('OK')"
git add linguataxi/models/ linguataxi/plugins/ model_manager.py tuned_models.py download_models.py plugin_loader.py plugin_registry.py offline_translate.py transcribe_file.py voice_id.py lang_detect.py
git commit -m "[refactor] move utility modules into linguataxi package"
```

---

## Task 11: Extract HTML/CSS/JS

**Files:**
- Create: `templates/operator.html`, `templates/display.html`, `templates/dictation.html`, `templates/bidirectional.html`
- Create: `static/css/operator.css`, `static/css/display.css`, `static/css/dictation.css`, `static/css/bidirectional.css`
- Create: `static/js/operator.js`, `static/js/display.js`, `static/js/dictation.js`, `static/js/bidirectional.js`, `static/js/grid-editor.js`
- Modify: Server route handlers to serve from `templates/` and reference `static/` assets
- Remove: root `operator.html`, `display.html`, `dictation.html`, `bidirectional.html`

- [ ] **Step 1: Create directories**

```bash
mkdir -p templates static/css static/js
```

- [ ] **Step 2: Split `operator.html` (2765 lines)**

- Extract lines 10-363 (`<style>` block) → `static/css/operator.css`
- Extract lines 633-2233 (main logic `<script>`) → `static/js/operator.js`
- Extract lines 2429-2763 (grid editor `<script>`) → `static/js/grid-editor.js`
- Extract lines 2254-2427 (plugin store `<script>`) — append to `static/js/operator.js` or keep as separate section
- The remaining HTML skeleton goes to `templates/operator.html` with `<link>` and `<script>` tags:

```html
<link rel="stylesheet" href="/static/css/operator.css">
<!-- ... markup ... -->
<script src="/static/js/operator.js"></script>
<script src="/static/js/grid-editor.js"></script>
```

Note: inline `onclick=` handlers in the HTML markup reference functions defined in the JS. These continue to work as long as the JS is loaded before the handlers fire (use `defer` or place scripts at end of body).

- [ ] **Step 3: Split `display.html` (626 lines)**

- Lines 11-55 → `static/css/display.css`
- Lines 74-619 → `static/js/display.js`
- Skeleton → `templates/display.html`

- [ ] **Step 4: Split `dictation.html` (548 lines)**

- Lines 10-58 → `static/css/dictation.css`
- Lines 99-546 → `static/js/dictation.js`
- Skeleton → `templates/dictation.html`

- [ ] **Step 5: Split `bidirectional.html` (470 lines)**

- Lines 10-50 → `static/css/bidirectional.css`
- Lines 61-468 → `static/js/bidirectional.js`
- Skeleton → `templates/bidirectional.html`

- [ ] **Step 6: Update server routes to serve from `templates/`**

In `linguataxi/server/routes/display.py`, update `_render_display_html()` to read from `templates/display.html` instead of `BASE_DIR / "display.html"`.

Ensure all FastAPI apps mount the static directory:
```python
from starlette.staticfiles import StaticFiles
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
```

- [ ] **Step 7: Move existing `static/` files into proper subdirectories**

The existing `static/plugin_dispatcher.js`, `static/plugin_grid.js`, `static/plugin_panel.css` stay where they are (they're already in `static/`).

- [ ] **Step 8: Delete root HTML files**

```bash
git rm operator.html display.html dictation.html bidirectional.html
```

- [ ] **Step 9: Verify and commit**

Start the server briefly, open each page in the browser to confirm CSS/JS loads correctly.

```bash
git add templates/ static/ linguataxi/server/routes/
git commit -m "[refactor] split HTML files into templates + static CSS/JS"
```

---

## Task 12: Polish Pass

**Files:**
- Modify: `.gitignore`
- Modify: `requirements.txt`
- Create: `requirements-dev.txt`
- Modify: `README.md`

- [ ] **Step 1: Fix `.gitignore`**

Add binary patterns that should never be tracked:
```
# Binaries (should never be in repo)
*.exe
*.zip
*.wav
```

Remove tracked binaries:
```bash
git rm --cached "LinguaTaxi-CPU-Setup-1.0.0.exe" 2>/dev/null || true
git rm --cached "LinguaTaxi-GPU-Setup-1.0.0.exe" 2>/dev/null || true
git rm --cached "live-caption (7).zip" 2>/dev/null || true
git rm --cached "Test_Audio.wav" 2>/dev/null || true
```

Also add:
```
# Templates are served from templates/ now
# (root HTML files removed)
```

- [ ] **Step 2: Tighten `requirements.txt`**

Update loose version bounds:
- `numpy>=1.24.0,<2.0` (was `<3.0`)
- `Pillow>=10.0.0,<11.0` (was `<12.0`)

- [ ] **Step 3: Create `requirements-dev.txt`**

```
-r requirements.txt
pytest>=7.0.0,<9.0
pytest-asyncio>=0.21.0,<1.0
mypy>=1.0.0,<2.0
```

- [ ] **Step 4: Update `README.md`**

Add an "Architecture" section after the features list:

```markdown
## Architecture

LinguaTaxi is organized as a Python package (`linguataxi/`) with thin entry-point
scripts at the repository root for installer compatibility.

- `linguataxi/server/` — FastAPI backend: audio capture, STT backends, translation, WebSocket broadcast
- `linguataxi/launcher/` — Desktop GUI: server management, settings, model downloads
- `linguataxi/dictation/` — System tray push-to-talk application
- `linguataxi/models/` — Model management and download utilities
- `linguataxi/plugins/` — Plugin loading and registry
- `templates/` — HTML page templates
- `static/` — CSS, JavaScript, and plugin assets
- `plugins/` — Installed plugins (each with manifest.json, routes, panel)
```

- [ ] **Step 5: Commit**

```bash
git add .gitignore requirements.txt requirements-dev.txt README.md
git commit -m "[chore] polish: fix gitignore, tighten deps, add architecture to README"
```

---

## Task 13: Final Verification

- [ ] **Step 1: Verify all imports resolve**

```bash
python -c "
from linguataxi.constants import SAMPLE_RATE, DISPLAY_PORT
from linguataxi.settings import load_config, load_settings
from linguataxi.server.state import shutdown_event
from linguataxi.server.audio import AudioSource
from linguataxi.server.backends import create_backend
from linguataxi.server.websocket import broadcast_all
from linguataxi.server.transcripts import _save_line
from linguataxi.server.translation import translate_text
from linguataxi.server.routes.display import register_display_routes
from linguataxi.server.routes.operator import register_operator_routes
from linguataxi.server.routes.dictation import register_dictation_routes
from linguataxi.server.routes.transcribe import register_transcribe_routes
from linguataxi.launcher.server_manager import ServerManager
from linguataxi.launcher.app import LinguaTaxiApp
from linguataxi.dictation.main import main as dict_main
from linguataxi.models.downloader import main as dl_main
from linguataxi.plugins.loader import PluginDispatcher
print('All imports OK')
"
```

- [ ] **Step 2: Verify entry points**

```bash
python server.py --help
python -c "import launcher; print('launcher shell OK')" 2>/dev/null || true
python -c "import tray_dictation; print('dictation shell OK')" 2>/dev/null || true
```

- [ ] **Step 3: Run the server and test all three ports**

Start the server, open:
- http://localhost:3000 (display)
- http://localhost:3001 (operator)
- http://localhost:3002 (extended)
- http://localhost:3005 (dictation)

Verify CSS/JS loads (no unstyled pages, no console errors).

- [ ] **Step 4: Test dictation activation**

Press the hotkey. Verify:
- No HTTP 500 errors in logs
- Dictation activates via HTTP POST
- Text appears in focused application

- [ ] **Step 5: Verify launcher starts**

Launch `launcher.pyw`, verify:
- GUI appears
- Server can start/stop
- Settings save/load correctly
- Tray icon works
- Quit from tray works cleanly

- [ ] **Step 6: Final commit and tag**

```bash
git add -A
git status  # verify nothing unexpected
git commit -m "[refactor] verification pass — all entry points and modules confirmed working"
```

---

## Parallelization Guide

Tasks that can run in parallel (after their dependencies complete):

```
Task 1 (branch + skeleton)
  └─→ Task 2 (foundation)
        ├─→ Task 3 (backends)     ─┐
        ├─→ Task 4 (audio)        ─┤
        ├─→ Task 5 (ws/tx/trans)  ─┤─→ Task 7 (app + main) ─→ Task 13 (verify)
        └─→ Task 6 (routes)       ─┘
        │
        ├─→ Task 8 (launcher)     ─→ Task 13
        ├─→ Task 9 (dictation)    ─→ Task 13
        ├─→ Task 10 (utilities)   ─→ Task 13
        └─→ Task 11 (HTML/CSS/JS) ─→ Task 13
        │
        └─→ Task 12 (polish)      ─→ Task 13
```

**Wave 1** (sequential): Tasks 1-2
**Wave 2** (parallel, 4 agents): Tasks 3+4+5+6 (all extract from server.py — coordinate via separate sections, no overlapping lines)
**Wave 3** (sequential): Task 7 (wires server together, depends on 3-6)
**Wave 4** (parallel, 4 agents): Tasks 8+9+10+11 (independent files)
**Wave 5** (sequential): Task 12 (polish)
**Wave 6** (sequential): Task 13 (verification)

**IMPORTANT for parallel agents:** Tasks 3-6 all modify `server.py` (removing extracted code). To avoid merge conflicts, each agent should ONLY remove the specific line ranges assigned to it. Task 7 does the final cleanup of server.py into a thin shell.
