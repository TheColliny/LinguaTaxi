#!/bin/bash
# ════════════════════════════════════════════════════════
# LinguaTaxi — macOS DMG Builder
#
# Prerequisites:
#   - macOS 12+ with Xcode Command Line Tools
#   - Optional: create-dmg (brew install create-dmg) for fancy DMG
#
# Output: dist/LinguaTaxi-1.0.1.dmg
# ════════════════════════════════════════════════════════

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
BUILD_DIR="$PROJECT_DIR/dist/mac_build"
APP_BUNDLE="$BUILD_DIR/LinguaTaxi.app"
DIST_DIR="$PROJECT_DIR/dist"
# H20: Match Windows version
VERSION="1.0.1"
RESOURCES="$APP_BUNDLE/Contents/Resources"

echo ""
echo "  ╔══════════════════════════════════════════════╗"
echo "  ║  LinguaTaxi — macOS DMG Builder              ║"
echo "  ╚══════════════════════════════════════════════╝"
echo ""

# ── Clean ──
rm -rf "$BUILD_DIR"
mkdir -p "$BUILD_DIR" "$DIST_DIR"

# ── Create .app bundle structure ──
echo "  Creating app bundle..."
mkdir -p "$APP_BUNDLE/Contents/MacOS"
mkdir -p "$RESOURCES"
mkdir -p "$RESOURCES/uploads"
mkdir -p "$RESOURCES/models"

# Copy Info.plist
cp "$SCRIPT_DIR/Info.plist" "$APP_BUNDLE/Contents/"

# Copy launcher script
cp "$SCRIPT_DIR/launcher.sh" "$APP_BUNDLE/Contents/MacOS/LinguaTaxi"
chmod +x "$APP_BUNDLE/Contents/MacOS/LinguaTaxi"

# Copy application files
# NOTE: Keep this list in sync with [Files] section of build/windows/installer.iss
cp "$PROJECT_DIR/server.py" "$RESOURCES/"
cp "$PROJECT_DIR/launcher.pyw" "$RESOURCES/"
cp "$PROJECT_DIR/display.html" "$RESOURCES/"
cp "$PROJECT_DIR/operator.html" "$RESOURCES/"
cp "$PROJECT_DIR/dictation.html" "$RESOURCES/"
cp "$PROJECT_DIR/requirements.txt" "$RESOURCES/"
cp "$PROJECT_DIR/download_models.py" "$RESOURCES/"
cp "$PROJECT_DIR/tuned_models.py" "$RESOURCES/"
cp "$PROJECT_DIR/offline_translate.py" "$RESOURCES/"
cp "$PROJECT_DIR/plugin_loader.py" "$RESOURCES/"
cp -r "$PROJECT_DIR/static" "$RESOURCES/static"
cp -r "$PROJECT_DIR/plugins" "$RESOURCES/plugins"
# H19: Copy files that were missing from macOS but present in Windows installer
cp "$PROJECT_DIR/bidirectional.html" "$RESOURCES/"
cp "$PROJECT_DIR/lang_detect.py" "$RESOURCES/"
cp -r "$PROJECT_DIR/locales" "$RESOURCES/locales"
cp -r "$PROJECT_DIR/assets" "$RESOURCES/assets"
cp "$PROJECT_DIR/LICENSE" "$RESOURCES/" 2>/dev/null || true

echo "macOS" > "$RESOURCES/edition.txt"

# ── Icon ──
if [ -f "$PROJECT_DIR/assets/linguataxi.icns" ]; then
    cp "$PROJECT_DIR/assets/linguataxi.icns" "$RESOURCES/"
    echo "  [OK] Icon copied"
elif [ -f "$PROJECT_DIR/assets/linguataxi.png" ]; then
    # Convert PNG to ICNS
    echo "  Converting PNG icon to ICNS..."
    ICONSET="$BUILD_DIR/linguataxi.iconset"
    mkdir -p "$ICONSET"
    sips -z 16 16     "$PROJECT_DIR/assets/linguataxi.png" --out "$ICONSET/icon_16x16.png" 2>/dev/null
    sips -z 32 32     "$PROJECT_DIR/assets/linguataxi.png" --out "$ICONSET/icon_16x16@2x.png" 2>/dev/null
    sips -z 32 32     "$PROJECT_DIR/assets/linguataxi.png" --out "$ICONSET/icon_32x32.png" 2>/dev/null
    sips -z 64 64     "$PROJECT_DIR/assets/linguataxi.png" --out "$ICONSET/icon_32x32@2x.png" 2>/dev/null
    sips -z 128 128   "$PROJECT_DIR/assets/linguataxi.png" --out "$ICONSET/icon_128x128.png" 2>/dev/null
    sips -z 256 256   "$PROJECT_DIR/assets/linguataxi.png" --out "$ICONSET/icon_128x128@2x.png" 2>/dev/null
    sips -z 256 256   "$PROJECT_DIR/assets/linguataxi.png" --out "$ICONSET/icon_256x256.png" 2>/dev/null
    sips -z 512 512   "$PROJECT_DIR/assets/linguataxi.png" --out "$ICONSET/icon_256x256@2x.png" 2>/dev/null
    sips -z 512 512   "$PROJECT_DIR/assets/linguataxi.png" --out "$ICONSET/icon_512x512.png" 2>/dev/null
    sips -z 1024 1024 "$PROJECT_DIR/assets/linguataxi.png" --out "$ICONSET/icon_512x512@2x.png" 2>/dev/null
    iconutil -c icns "$ICONSET" -o "$RESOURCES/linguataxi.icns"
    rm -rf "$ICONSET"
    echo "  [OK] Icon converted"
else
    echo "  NOTE: No icon found. Place linguataxi.png or .icns in assets/"
fi

# ── Create DMG ──
DMG_NAME="LinguaTaxi-${VERSION}.dmg"
DMG_PATH="$DIST_DIR/$DMG_NAME"

echo "  Creating DMG..."

if command -v create-dmg &>/dev/null; then
    # Fancy DMG with create-dmg
    create-dmg \
        --volname "LinguaTaxi" \
        --volicon "$RESOURCES/linguataxi.icns" \
        --window-pos 200 120 \
        --window-size 600 400 \
        --icon-size 100 \
        --icon "LinguaTaxi.app" 175 180 \
        --app-drop-link 425 180 \
        --hide-extension "LinguaTaxi.app" \
        --background "$PROJECT_DIR/assets/dmg_background.png" \
        "$DMG_PATH" \
        "$BUILD_DIR/" \
        2>/dev/null || {
            # Fallback without background if image missing
            create-dmg \
                --volname "LinguaTaxi" \
                --window-pos 200 120 \
                --window-size 600 400 \
                --icon-size 100 \
                --icon "LinguaTaxi.app" 175 180 \
                --app-drop-link 425 180 \
                "$DMG_PATH" \
                "$BUILD_DIR/"
        }
else
    # Simple DMG with hdiutil
    echo "  (Install create-dmg for a prettier DMG: brew install create-dmg)"
    hdiutil create -volname "LinguaTaxi" \
        -srcfolder "$BUILD_DIR" \
        -ov -format UDZO \
        "$DMG_PATH"
fi

# ── Cleanup ──
rm -rf "$BUILD_DIR"

echo ""
echo "  ╔══════════════════════════════════════════════╗"
echo "  ║  BUILD SUCCESSFUL!                            ║"
echo "  ║                                                ║"
echo "  ║  Output: dist/$DMG_NAME            ║"
echo "  ╚══════════════════════════════════════════════╝"
echo ""
