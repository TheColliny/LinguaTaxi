#!/bin/bash
# ════════════════════════════════════════════════════════
# LinguaTaxi — Linux Installer
#
# Run this script after extracting the tar.gz:
#   tar -xzf LinguaTaxi-1.0.0-linux.tar.gz
#   cd LinguaTaxi-1.0.0
#   ./install.sh
#
# What it does:
#   1. Checks for Python 3.10+ with tkinter
#   2. Installs system dependencies (PortAudio)
#   3. Creates a virtual environment
#   4. Installs Python packages
#   5. Downloads speech recognition models
#   6. Creates a desktop shortcut (optional)
#   7. Creates a launch script
# ════════════════════════════════════════════════════════

set -e

APP_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV="$APP_DIR/venv"

echo ""
echo "  ========================================"
echo "    LinguaTaxi — Linux Installer"
echo "  ========================================"
echo ""

# ── Check Python 3 ──
PYTHON3=""
for p in python3.12 python3.11 python3.10 python3; do
    if command -v "$p" &>/dev/null; then
        VER=$("$p" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null)
        MAJOR=$(echo "$VER" | cut -d. -f1)
        MINOR=$(echo "$VER" | cut -d. -f2)
        if [ "$MAJOR" -ge 3 ] && [ "$MINOR" -ge 10 ]; then
            PYTHON3="$p"
            break
        fi
    fi
done

if [ -z "$PYTHON3" ]; then
    echo "  ERROR: Python 3.10 or later is required."
    echo ""
    echo "  Install it with your package manager:"
    echo "    Ubuntu/Debian:  sudo apt install python3 python3-venv python3-tk"
    echo "    Fedora:         sudo dnf install python3 python3-tkinter"
    echo "    Arch:           sudo pacman -S python tk"
    echo ""
    exit 1
fi

echo "  [OK] Python: $PYTHON3 ($VER)"

# ── Check tkinter ──
if ! "$PYTHON3" -c "import tkinter" 2>/dev/null; then
    echo "  ERROR: Python tkinter module not found."
    echo ""
    echo "  Install it:"
    echo "    Ubuntu/Debian:  sudo apt install python3-tk"
    echo "    Fedora:         sudo dnf install python3-tkinter"
    echo "    Arch:           sudo pacman -S tk"
    echo ""
    exit 1
fi
echo "  [OK] tkinter available"

# ── Install system dependencies ──
echo ""
echo "  Checking system dependencies..."

# PortAudio (needed by sounddevice)
if ! ldconfig -p 2>/dev/null | grep -q libportaudio; then
    echo "  Installing PortAudio..."
    if command -v apt-get &>/dev/null; then
        sudo apt-get install -y portaudio19-dev 2>/dev/null || echo "  WARNING: Could not install PortAudio. Install manually: sudo apt install portaudio19-dev"
    elif command -v dnf &>/dev/null; then
        sudo dnf install -y portaudio-devel 2>/dev/null || echo "  WARNING: Could not install PortAudio. Install manually: sudo dnf install portaudio-devel"
    elif command -v pacman &>/dev/null; then
        sudo pacman -S --noconfirm portaudio 2>/dev/null || echo "  WARNING: Could not install PortAudio. Install manually: sudo pacman -S portaudio"
    else
        echo "  WARNING: Could not detect package manager. Install PortAudio manually."
    fi
else
    echo "  [OK] PortAudio found"
fi

# ── Create virtual environment ──
echo ""
if [ -d "$VENV" ]; then
    echo "  Virtual environment already exists. Updating..."
else
    echo "  Creating virtual environment..."
    "$PYTHON3" -m venv "$VENV"
fi

# ── Install Python packages ──
echo "  Installing Python packages..."
"$VENV/bin/pip" install --upgrade pip -q
"$VENV/bin/pip" install fastapi uvicorn websockets sounddevice numpy requests python-multipart -q
echo "  [OK] Base packages installed"

# ── Install speech backend ──
echo ""
echo "  Which speech backend would you like to install?"
echo "    1) Vosk (CPU only, ~40 MB model, works everywhere)"
echo "    2) faster-whisper (NVIDIA GPU, best accuracy, ~1.5 GB model)"
echo "    3) Both"
echo ""
read -p "  Choose [1/2/3, default=1]: " BACKEND_CHOICE
BACKEND_CHOICE=${BACKEND_CHOICE:-1}

case "$BACKEND_CHOICE" in
    2)
        echo "  Installing faster-whisper..."
        "$VENV/bin/pip" install faster-whisper -q
        echo "  [OK] faster-whisper installed"
        ;;
    3)
        echo "  Installing Vosk and faster-whisper..."
        "$VENV/bin/pip" install vosk faster-whisper -q
        echo "  [OK] Both backends installed"
        ;;
    *)
        echo "  Installing Vosk..."
        "$VENV/bin/pip" install vosk -q
        echo "  [OK] Vosk installed"
        ;;
esac

# ── Install offline translation packages ──
echo "  Installing offline translation support..."
"$VENV/bin/pip" install sentencepiece ctranslate2 huggingface_hub -q
echo "  [OK] Offline translation packages installed"

# ── Download speech models ──
echo ""
echo "  Downloading speech recognition model..."
"$VENV/bin/python3" "$APP_DIR/download_models.py" || echo "  WARNING: Model download failed. You can download later from the app."

# ── Create launch script ──
cat > "$APP_DIR/linguataxi" << 'LAUNCHER'
#!/bin/bash
DIR="$(cd "$(dirname "$0")" && pwd)"
exec "$DIR/venv/bin/python3" "$DIR/launcher.pyw" "$@"
LAUNCHER
chmod +x "$APP_DIR/linguataxi"

# ── Desktop shortcut (optional) ──
echo ""
read -p "  Create desktop shortcut? [y/N]: " CREATE_SHORTCUT
if [ "$CREATE_SHORTCUT" = "y" ] || [ "$CREATE_SHORTCUT" = "Y" ]; then
    DESKTOP_DIR="${XDG_DESKTOP_DIR:-$HOME/Desktop}"
    ICON_PATH=""
    if [ -f "$APP_DIR/assets/linguataxi.png" ]; then
        ICON_PATH="$APP_DIR/assets/linguataxi.png"
    fi

    cat > "$DESKTOP_DIR/linguataxi.desktop" << DESKTOP
[Desktop Entry]
Type=Application
Name=LinguaTaxi
Comment=Live Caption and Translation
Exec=$APP_DIR/linguataxi
Icon=${ICON_PATH}
Terminal=false
Categories=Utility;Audio;
DESKTOP
    chmod +x "$DESKTOP_DIR/linguataxi.desktop"
    echo "  [OK] Desktop shortcut created"
fi

echo ""
echo "  ========================================"
echo "    Installation complete!"
echo ""
echo "    Launch with: ./linguataxi"
echo "  ========================================"
echo ""
