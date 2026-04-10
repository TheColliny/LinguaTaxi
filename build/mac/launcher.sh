#!/bin/bash
# ════════════════════════════════════════════════════
# LinguaTaxi.app — macOS launcher
# Lives in LinguaTaxi.app/Contents/MacOS/LinguaTaxi
# ════════════════════════════════════════════════════

set -e

CONTENTS_DIR="$(cd "$(dirname "$0")/.." && pwd)"
RESOURCES="$CONTENTS_DIR/Resources"
SUPPORT_DIR="$HOME/Library/Application Support/LinguaTaxi"
VENV="$SUPPORT_DIR/venv"
LOG="$SUPPORT_DIR/install.log"
PYTHON3=""
# H20/M61: Single version constant used for setup marker comparison
APP_VERSION="1.0.1"

# ── Find Python 3 ──
find_python() {
    # Check Homebrew paths first (Apple Silicon then Intel)
    for p in /opt/homebrew/bin/python3 /usr/local/bin/python3 /usr/bin/python3; do
        if [ -x "$p" ]; then
            # Verify it has tkinter
            if "$p" -c "import tkinter" 2>/dev/null; then
                PYTHON3="$p"
                return 0
            fi
        fi
    done
    return 1
}

# ── First-run setup ──
first_run_setup() {
    mkdir -p "$SUPPORT_DIR"
    echo "[$(date)] First-run setup starting..." > "$LOG"

    # Check for Python with tkinter
    if ! find_python; then
        # Prompt to install Python via Homebrew
        osascript -e 'display dialog "LinguaTaxi requires Python 3 with tkinter.\n\nClick OK to install via Homebrew (recommended), or install Python 3.10+ manually from python.org" buttons {"Cancel", "Install Python"} default button "Install Python" with title "LinguaTaxi Setup" with icon caution' 2>/dev/null || exit 1

        # Check/install Homebrew
        if ! command -v brew &>/dev/null; then
            echo "Installing Homebrew..." >> "$LOG"
            # H24: Use NONINTERACTIVE=1 to prevent Homebrew installer from hanging on prompts
            NONINTERACTIVE=1 /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)" >> "$LOG" 2>&1

            # Add to PATH for this session
            if [ -f /opt/homebrew/bin/brew ]; then
                eval "$(/opt/homebrew/bin/brew shellenv)"
            elif [ -f /usr/local/bin/brew ]; then
                eval "$(/usr/local/bin/brew shellenv)"
            fi
        fi

        # Install Python + PortAudio
        echo "Installing Python and PortAudio..." >> "$LOG"
        brew install python3 python-tk >> "$LOG" 2>&1
        # M63: Don't let portaudio failure kill the launcher (set -e is active)
        brew install portaudio >> "$LOG" 2>&1 || true

        if ! find_python; then
            osascript -e 'display dialog "Failed to install Python with tkinter. Please install manually from python.org and try again." buttons {"OK"} default button "OK" with title "LinguaTaxi" with icon stop'
            exit 1
        fi
    fi

    # Check for PortAudio (needed by sounddevice)
    if ! brew list portaudio &>/dev/null 2>&1; then
        if command -v brew &>/dev/null; then
            echo "Installing PortAudio..." >> "$LOG"
            # M63: Don't let portaudio failure kill the launcher
            brew install portaudio >> "$LOG" 2>&1 || true
        fi
    fi

    echo "Using Python: $PYTHON3" >> "$LOG"

    # Create venv
    echo "Creating virtual environment..." >> "$LOG"
    "$PYTHON3" -m venv "$VENV" >> "$LOG" 2>&1

    # Install dependencies
    # NOTE: Canonical package list lives in requirements.txt at project root.
    # Keep this list in sync with requirements.txt and build/windows/build.bat.
    echo "Installing packages..." >> "$LOG"
    "$VENV/bin/pip" install --upgrade pip >> "$LOG" 2>&1
    # M62: Include all packages that the Windows build installs (onnxruntime, sentencepiece, etc.)
    "$VENV/bin/pip" install fastapi uvicorn websockets sounddevice numpy requests python-multipart onnxruntime sentencepiece ctranslate2 huggingface_hub >> "$LOG" 2>&1

    # Detect Apple Silicon vs Intel
    ARCH=$(uname -m)
    if [ "$ARCH" = "arm64" ]; then
        echo "Apple Silicon detected — installing MLX Whisper..." >> "$LOG"
        "$VENV/bin/pip" install mlx-whisper >> "$LOG" 2>&1 || {
            echo "MLX install failed, trying Vosk..." >> "$LOG"
            "$VENV/bin/pip" install vosk >> "$LOG" 2>&1
        }
    else
        echo "Intel Mac — installing Vosk..." >> "$LOG"
        "$VENV/bin/pip" install vosk >> "$LOG" 2>&1
    fi

    # M61: Write version into marker so upgrades re-run setup
    echo "$APP_VERSION" > "$SUPPORT_DIR/.setup_complete"

    # Pre-download speech model
    echo "Downloading speech recognition model..." >> "$LOG"
    "$VENV/bin/python3" "$RESOURCES/download_models.py" >> "$LOG" 2>&1 || true

    echo "[$(date)] Setup complete!" >> "$LOG"
}

# ── Main ──

mkdir -p "$SUPPORT_DIR"

# M61: Check if first run OR version has changed (triggers re-setup on upgrade)
SETUP_MARKER="$SUPPORT_DIR/.setup_complete"
if [ ! -f "$SETUP_MARKER" ] || [ ! -d "$VENV" ] || [ "$(cat "$SETUP_MARKER" 2>/dev/null)" != "$APP_VERSION" ]; then
    # Show progress via a background AppleScript dialog
    osascript -e 'display notification "Setting up LinguaTaxi for first use..." with title "LinguaTaxi" subtitle "This may take a few minutes"' 2>/dev/null &

    first_run_setup
fi

# Verify venv exists
if [ ! -f "$VENV/bin/python3" ]; then
    osascript -e 'display dialog "LinguaTaxi virtual environment is missing. Please reinstall the application." buttons {"OK"} default button "OK" with title "LinguaTaxi" with icon stop'
    exit 1
fi

# Launch the tkinter GUI
export LINGUATAXI_APP_DIR="$RESOURCES"
exec "$VENV/bin/python3" "$RESOURCES/launcher.pyw"
