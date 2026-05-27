# LinguaTaxi

**Real-time live captioning and multi-language translation for speeches, meetings, and events.**

LinguaTaxi captures speech from any microphone, transcribes it in real time using GPU or CPU speech engines, and simultaneously translates into up to 5 languages via DeepL. A dedicated operator panel gives full control over speakers, styling, and languages while audience displays show scrolling captions — ready for projectors, monitors, or live streams.

---

## Quick Start

### Windows (Recommended)

1. Download `LinguaTaxi-GPU-Setup-1.0.0.exe` (NVIDIA GPU) or `LinguaTaxi-CPU-Setup-1.0.0.exe` (CPU only)
2. Run the installer
3. Launch **LinguaTaxi** from the Desktop or Start Menu
4. Click **Start Server** — a model will download on first run (~1 GB for GPU, ~50 MB for CPU)
5. Click **Operator Controls** to open the control panel in your browser
6. Click **GO LIVE** to start captioning
7. Click **Open Main Display** to see the audience-facing caption view

### macOS

1. Open `LinguaTaxi-1.0.0.dmg`, drag to Applications
2. Launch LinguaTaxi — first run installs dependencies automatically (2-5 min)
3. Grant microphone permission when prompted
4. Follow steps 4-7 above

### Linux / Manual Install

```bash
git clone https://github.com/TheColliny/LinguaTaxi.git
cd LinguaTaxi
pip install -r requirements.txt
pip install faster-whisper    # NVIDIA GPU (recommended)
# OR: pip install vosk        # CPU fallback
python launcher.pyw
```

---

## How It Works

LinguaTaxi runs a local web server with three browser-based displays:

| Display | Port | Purpose |
|---------|------|---------|
| **Main Display** | :3000 | Audience-facing captions — put this on a projector or monitor |
| **Operator Panel** | :3001 | Full controls — speakers, languages, styling, grid layout |
| **Extended Display** | :3002 | Additional translations or plugin tiles for a second screen |

The operator controls everything from the operator panel while the audience only sees clean, styled captions on the main display.

### Typical Workflow

1. **Start the server** from the desktop launcher
2. **Configure** in the operator panel: set input language, add translation languages, assign speaker names
3. **GO LIVE** — captioning begins (translations remain paused until you're ready)
4. **Resume Translation** — DeepL translations start flowing to the audience
5. **Switch speakers** during the event using buttons or keyboard shortcuts (1-9)

### Keyboard Shortcuts (Operator Panel)

| Key | Action |
|-----|--------|
| L | Toggle live captioning |
| P | Toggle translation pause |
| C | Clear all captions |
| 1-9 | Switch to speaker 1-9 |
| 0 | Clear active speaker |

---

## Features

### Speech Recognition
- **Faster-Whisper** (NVIDIA CUDA GPU) — highest accuracy, real-time on RTX cards
- **MLX Whisper** (Apple Metal) — native GPU acceleration on Apple Silicon Macs
- **Vosk** (CPU) — lightweight fallback, works on any machine
- Automatic backend selection based on available hardware
- Language-tuned model downloads for improved accuracy in specific languages

### Translation
- **DeepL API** — up to 5 simultaneous translation slots running in parallel threads
- **Offline translation** — OPUS-MT and M2M-100 models for air-gapped environments
- Each translation slot is independently configurable with language and display color

### Display & Styling
- **10x10 drag-and-drop grid** — arrange captions, translations, and plugins freely
- 4 background themes, 5 font families (including CJK and Arabic support)
- 12 caption colors, text size from 24px to 960px, 1-8 visible lines
- Scrolling captions with inline speaker labels (name shown only on speaker change)
- Footer banner with custom images or text

### Speaker Management
- Up to 9 named speakers with color-coded labels
- 0.5-second retroactive buffer splitting — when you switch speakers mid-sentence, the audio is split and the previous portion is attributed to the correct speaker
- Speaker labels appear inline in captions, shown only when the speaker changes

### Transcripts
- Automatic timestamped transcript saving — one `.txt` file per language
- Includes speaker labels and timestamps
- Default location: `Documents/LinguaTaxi Transcripts/` (configurable in the launcher or via `--transcripts-dir`)

### Dictation Mode
- System tray push-to-talk application for voice-to-text input
- Toggle or hold-to-talk modes
- Word count tracking, copy to clipboard, save as file

### Bidirectional Captioning
- Split-screen display for two-way translated conversations
- Useful for bilingual meetings or interview settings

### Multi-Source Audio
- Up to 8 simultaneous audio input sources
- Each source can have its own speaker assignment
- Add and remove sources at runtime from the operator panel

### Plugin System
LinguaTaxi includes a plugin architecture for extending functionality. Bundled plugins:

- **Window Capture** — stream any application window to the audience display via the browser's screen capture API
- **Fact Checker** — multi-provider consensus fact-checking with source credibility scoring
- **Donor Cloud** — real-time donor name word cloud from FEC and state disclosure databases
- **Live Dial** — audience approval dial testing with QR code join and live sentiment graphs
- **Polls Checker** — opinion claim fact-checking using polling data

Plugins can be enabled/disabled at runtime and appear as draggable tiles in the display grid. See [Plugin Development Guide](docs/PLUGIN_DEVELOPMENT_GUIDE.md) for creating your own.

### Internationalization
- Operator panel UI translated into 40+ languages
- Language selector with flags and native language names

---

## Architecture

LinguaTaxi is a Python package with a FastAPI web backend and a desktop GUI launcher.

```
linguataxi/
  server/          FastAPI backend: audio capture, STT, translation, WebSocket broadcast
    backends/      Speech engines: faster-whisper, vosk, mlx-whisper
    routes/        HTTP and WebSocket endpoints for each display
  launcher/        Desktop GUI: server management, settings, model downloads
  dictation/       System tray push-to-talk application
  models/          Model management and download utilities
  plugins/         Plugin loader and registry
templates/         HTML page templates (display, operator, dictation, bidirectional)
static/            CSS, JavaScript, and shared assets
plugins/           Installed plugins (each with manifest.json, routes, panel UI)
build/             Platform-specific build scripts and installer configs
locales/           UI translation files (40+ languages)
```

### Key Technical Details
- Audio captured at 16 kHz mono float32 via sounddevice
- WebSocket broadcast to all connected display clients
- Translation runs in a thread pool, one thread per language slot
- Config persisted in `config.json` (server) and `launcher_settings.json` (GUI)
- Server starts with captioning and translation paused by default

---

## Building From Source

### Windows

Requires [Inno Setup 6+](https://jrsoftware.org/isinfo.php) and an internet connection.

```
cd build\windows
build.bat
```

Outputs:
- `dist\LinguaTaxi-GPU-Setup-1.0.0.exe` — GPU edition (~200 MB)
- `dist\LinguaTaxi-CPU-Setup-1.0.0.exe` — CPU edition (~50 MB)

### macOS

```bash
cd build/mac
./build.sh
```

Outputs `dist/LinguaTaxi-1.0.0.dmg`. Optional: `brew install create-dmg` for a styled DMG.

### Linux

```bash
cd build/linux
./build.sh
```

Outputs `dist/LinguaTaxi-1.0.0-linux.tar.gz` with an install script.

### Icons

```bash
python assets/generate_icons.py   # requires Pillow
```

---

## Uninstalling

**Windows:** Start Menu > Uninstall LinguaTaxi. Checkboxes let you keep transcripts, models, and settings.

**macOS:** Trash the app. Optionally delete `~/Library/Application Support/LinguaTaxi/` and `~/Documents/LinguaTaxi Transcripts/`.

**Linux:** Delete the install directory and optionally `~/.config/LinguaTaxi/`.

---

## Configuration

### DeepL API Key
Enter your DeepL API key in the operator panel under the translation settings. A free DeepL account provides 500,000 characters/month.

### Transcript Directory
Configure via:
- The launcher GUI settings
- CLI flag: `--transcripts-dir /path/to/dir`
- Environment variable: `LINGUATAXI_TRANSCRIPTS`

### Speech Backend
Set in the launcher: Auto (recommended), Whisper (GPU), Vosk (CPU), or MLX (macOS).

---

## License

[MIT License](LICENSE) — Copyright (c) 2026 TheColliny
