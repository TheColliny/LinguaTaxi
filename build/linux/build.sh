#!/bin/bash
# ════════════════════════════════════════════════════════
# LinguaTaxi — Linux tar.gz Builder
#
# Creates a portable tar.gz with source files + install script.
# Users extract and run install.sh to set up the app.
#
# Output: dist/LinguaTaxi-1.0.0-linux.tar.gz
# ════════════════════════════════════════════════════════

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
DIST_DIR="$PROJECT_DIR/dist"
VERSION="1.0.0"
PACKAGE_NAME="LinguaTaxi-${VERSION}"
BUILD_DIR="$DIST_DIR/${PACKAGE_NAME}"

echo ""
echo "  ========================================"
echo "    LinguaTaxi — Linux Package Builder"
echo "  ========================================"
echo ""

# ── Clean ──
rm -rf "$BUILD_DIR"
mkdir -p "$BUILD_DIR" "$DIST_DIR"

# ── Copy application files ──
echo "  Copying application files..."
cp "$PROJECT_DIR/server.py" "$BUILD_DIR/"
cp "$PROJECT_DIR/launcher.pyw" "$BUILD_DIR/"
cp "$PROJECT_DIR/display.html" "$BUILD_DIR/"
cp "$PROJECT_DIR/operator.html" "$BUILD_DIR/"
cp "$PROJECT_DIR/dictation.html" "$BUILD_DIR/"
cp "$PROJECT_DIR/requirements.txt" "$BUILD_DIR/"
cp "$PROJECT_DIR/download_models.py" "$BUILD_DIR/"
cp "$PROJECT_DIR/tuned_models.py" "$BUILD_DIR/"
cp "$PROJECT_DIR/offline_translate.py" "$BUILD_DIR/"
cp "$PROJECT_DIR/LICENSE" "$BUILD_DIR/"
cp "$PROJECT_DIR/README.md" "$BUILD_DIR/"

# ── Copy assets ──
if [ -d "$PROJECT_DIR/assets" ]; then
    cp -r "$PROJECT_DIR/assets" "$BUILD_DIR/"
    echo "  [OK] Assets copied"
fi

# ── Copy install script ──
cp "$SCRIPT_DIR/install.sh" "$BUILD_DIR/"
chmod +x "$BUILD_DIR/install.sh"

# ── Create directories ──
mkdir -p "$BUILD_DIR/models"
mkdir -p "$BUILD_DIR/uploads"

# ── Create tar.gz ──
echo "  Creating tar.gz..."
cd "$DIST_DIR"
tar -czf "${PACKAGE_NAME}-linux.tar.gz" "${PACKAGE_NAME}/"

# ── Cleanup ──
rm -rf "$BUILD_DIR"

echo ""
echo "  ========================================"
echo "    BUILD SUCCESSFUL!"
echo ""
echo "    Output: dist/${PACKAGE_NAME}-linux.tar.gz"
echo "  ========================================"
echo ""
