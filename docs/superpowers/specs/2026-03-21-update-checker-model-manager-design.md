# Design: Update Checker, Model Manager Cleanup & Installer Upgrade

**Date:** 2026-03-21
**Status:** Approved

## Overview

Four related changes to LinguaTaxi:
1. Rename "Manage Installed Models" to delete-only mode
2. Add edition detection via `edition.txt`
3. Add update checking (manual button + auto-check on startup)
4. Add installer upgrade support

## 1. Edition Detection

### edition.txt
- Simple text file in APP_DIR containing one of: `GPU`, `CPU`, `macOS`, `Linux`
- Written by each platform's build system:
  - Windows `installer.iss`: map `EDITION="Full"` → write `GPU`, `EDITION="Lite"` → write `CPU` in `CurStepChanged`
  - macOS `build/mac/build.sh`: write `macOS`
  - Linux `build/linux/install.sh`: write `Linux`
- Launcher reads at startup, defaults to `"Dev"` if file missing

### Header Layout
```
[ LinguaTaxi — GPU Edition     ] [ Check for Updates ]
[ Live Caption & Translation   ] [x] Check on start
```
- Left side: title label (with edition suffix) and subtitle, both left-anchored
- Right side: button and checkbox, right-anchored
- Use `pack(side="left")` for left content, `pack(side="right")` for right content

## 2. Model Manager → Delete Only

### UI Changes
- Button label: `"🔧 Manage Installed Models"` → `"🗑 Delete Installed Models"`
- Dialog title: `"Manage Installed Models"` → `"Delete Installed Models"`
- Only show installed models with `[Delete]` buttons
- Remove all `[Download]` buttons — do not show uninstalled models at all
- Remove the `_download_model` helper and `grab_release()`/`grab_set()` workaround code
- The `_add_model_row` function drops the `installed` parameter; all rows are installed
- Remove all calls passing `installed=False` in `_populate()`

### Rationale
The model manager's download functionality conflicts with the dedicated download dialogs (the manager's modal grab prevents download dialogs from functioning). Downloading is already handled by "Download Language-Tuned Models" and "Download Offline Translation Models" buttons.

## 3. Update Checking

### Version Comparison
- Fetch `https://api.github.com/repos/TheColliny/LinguaTaxi/releases/latest` via `urllib.request` (stdlib)
- Compare `tag_name` against `VERSION` using tuple comparison after stripping `v` prefix and splitting on `.`
- Only standard `vX.Y.Z` semver tags supported. Tags that cannot be parsed as three integers are silently ignored.
- Timeout: 5 seconds
- Handle HTTP 403/429 (GitHub rate limit): silently skip on startup, show "GitHub rate limit reached, try again later" on manual check

### New Settings (launcher_settings.json)
- `check_for_updates`: `true` (default) — checkbox state
- `dismissed_version`: `null` (default) — version user clicked "Don't remind me" for

Note: "Remind Me Later" requires no persistence — it simply closes the dialog. The next startup check will prompt again naturally.

### Startup Flow (when checkbox enabled)
1. Background thread checks GitHub API (non-blocking, does not delay app startup)
2. If new version found:
   - If `dismissed_version` matches this version → skip silently
   - Otherwise → show update prompt dialog on main thread via `self.after()`
3. On network error or rate limit → silently skip

### Update Prompt Dialog
- Title: "Update Available"
- Body: "LinguaTaxi v{new} is available (you have v{current})."
- Three buttons:
  - **"Download Now"** → save-file dialog → download matching edition installer → notify when complete
  - **"Remind Me Later"** → closes dialog, no persistence (will prompt again next startup)
  - **"Don't Remind Me"** → stores version in `dismissed_version`, suppresses until an even newer version

### Manual "Check for Updates" Button
- Always checks GitHub regardless of checkbox state or dismissed version
- Shows "You're up to date!" if no newer version
- Shows update prompt dialog if newer version exists
- Shows error if network unavailable or rate limited

## 4. Download Flow

### Edition-to-Asset Mapping
Read `edition.txt` to select the correct installer asset:
- `GPU` → `LinguaTaxi-GPU-Setup-{version}.exe`
- `CPU` → `LinguaTaxi-CPU-Setup-{version}.exe`
- `macOS` → `LinguaTaxi-{version}.dmg`
- `Linux` → `LinguaTaxi-{version}-linux.tar.gz`
- `Dev` → no download available; open GitHub releases page in browser instead

Asset URL from GitHub release API `browser_download_url` field for matching asset.

### Download Process
1. User clicks "Download Now"
2. Save-file dialog opens, pre-filled with asset filename, defaults to user's Downloads folder
3. Background thread downloads with progress dialog (progress bar + percentage + cancel button)
4. Progress via `urllib.request.urlopen` with chunked reading (better control over headers and redirects than `urlretrieve`)
5. On completion: "Download complete! The installer is ready at: {path}" with "Open Folder" button, plus reminder: "Close LinguaTaxi before running the installer."
6. On cancel/error: clean up partial file, show message

## 5. Installer Upgrade Support

### Detection
- Inno Setup's `AppId` already detects existing installations
- No additional detection needed

### Upgrade Messaging
- Detect existing version via registry in `InitializeSetup` or `CurPageChanged`
- Change wizard text: "LinguaTaxi v{old} is currently installed. This will upgrade to v{new}."

### edition.txt Generation
In `CurStepChanged` (ssPostInstall), after the existing `pyvenv.cfg` fixup:
- If `EDITION = "Full"`: write `GPU` to `{app}\edition.txt`
- If `EDITION = "Lite"`: write `CPU` to `{app}\edition.txt`

### Preserved During Upgrade
- `models/` directory (outside `[Files]`, untouched by Inno Setup)
- `models/translate/`, `models/tuned/` subdirectories
- Transcripts in `~/Documents/LinguaTaxi Transcripts/` (separate location)
- `launcher_settings.json` in `%APPDATA%` (separate location)
- Server `config.json` in APP_DIR (not in `[Files]`, preserved; new keys use defaults)
- `edition.txt` overwritten with correct edition for new installer

### Replaced During Upgrade
- All `[Files]` entries: `.py`, `.html`, `venv/`, `python/`, `assets/`
- `pyvenv.cfg` fixup runs again

### Custom Uninstall Dialog
- `InitializeUninstall` (per-model deletion choices) only runs during actual uninstall
- Inno Setup does not invoke uninstall hooks during same-AppId upgrade — no changes needed

## Files Modified

| File | Changes |
|------|---------|
| `launcher.pyw` | Header with edition label + update button/checkbox, model manager delete-only, update check logic, download flow, new settings fields |
| `installer.iss` | Write `edition.txt` in CurStepChanged, upgrade messaging in wizard |
| `build/mac/build.sh` | Write `edition.txt` with `macOS` |
| `build/linux/install.sh` | Write `edition.txt` with `Linux` |
| `edition.txt` | New file (not in repo — written by build systems at install time) |
