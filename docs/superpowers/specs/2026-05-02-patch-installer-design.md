# Patch Installer Framework — Design Spec

**Goal:** Enable fast (~seconds) incremental patch builds that ship only changed app files, avoiding the 20–30 minute full GPU installer rebuild.

**Status:** Approved design

---

## Overview

When bug fixes only touch app-level files (Python scripts, HTML, JS, CSS, plugins), a patch installer copies just those files into an existing LinguaTaxi installation. The full Python distribution, venv (with CUDA libraries), and speech models are untouched.

Patch installers are built per-edition (CPU and GPU) and require the correct base version and all prior patches applied sequentially.

---

## Version Tracking

### `version.json` (project root + installed at `{app}\version.json`)

```json
{"version": "1.0.2", "patch": 0, "edition": "Full"}
```

- **Full installer** writes this at install time with `patch: 0` and the correct `edition`.
- **Patch installer** increments `patch` by 1 after copying files.
- Committed to git so build scripts can read the current version.

### Git Tags

Created automatically by build scripts:

| Build type | Tag format | Example |
|---|---|---|
| Full build | `build/v{version}-{Edition}` | `build/v1.0.2-Full`, `build/v1.0.2-Lite` |
| Patch build | `build/v{version}-p{N}-{Edition}` | `build/v1.0.2-p1-Full`, `build/v1.0.2-p1-Lite` |

The patch builder finds the most recent `build/v{version}*` tag as the diff base.

---

## New Files

### `build/windows/build_patch.bat`

Orchestrates the patch build:

1. Reads `version.json` from project root for current version and patch number.
2. Finds the latest git tag matching `build/v{version}*`.
3. Runs `git diff --name-only {tag} HEAD` to get changed/added files.
4. Filters file list to app-level files only (see File Mapping Rules below).
5. **Safety checks — refuses to build if:**
   - `requirements.txt` changed (new pip dependencies require a full build).
   - Files under `build/windows/venv_*` or `build/windows/python_dist/` changed.
   - No app files actually changed (nothing to patch).
   - Working tree has uncommitted changes.
6. Increments patch number: `new_patch = current_patch + 1`.
7. Generates `build/windows/patch_files.iss` — one `Source:` line per changed file with correct `DestDir` mapping.
8. Compiles two patch installers via ISCC: `ISCC /DEDITION=Lite patch_installer.iss` and `ISCC /DEDITION=Full patch_installer.iss`.
9. Tags git: `build/v{version}-p{N}-Full` and `build/v{version}-p{N}-Lite`.
10. Updates `version.json` in the repo with the new patch number.

**Output:**
- `dist/LinguaTaxi-GPU-Patch-{version}-p{N}.exe`
- `dist/LinguaTaxi-CPU-Patch-{version}-p{N}.exe`

### `build/windows/patch_installer.iss`

Minimal Inno Setup script for patch delivery:

**Setup section:**
- Same `AppId` as full installer (Windows recognizes it as the same app).
- `OutputBaseFilename` = `LinguaTaxi-{GPU|CPU}-Patch-{version}-p{N}`.
- `UsePreviousAppDir=yes` — installs to existing LinguaTaxi directory.
- `CreateUninstallRegKey=no` — no separate uninstall entry for patches.
- `UpdateUninstallLogAppName=no` — doesn't touch full installer's uninstall log.
- No `[Icons]`, no shortcuts, no model downloads.

**Version check in `[Code]` InitializeSetup:**
1. Reads `{app}\version.json`.
2. Verifies `version` matches the patch's target version.
3. Verifies `edition` matches ("Full" for GPU patch, "Lite" for CPU patch).
4. Verifies `patch` equals N-1 (patch 3 requires patch 2 already applied).
5. On failure: shows a clear error message explaining what's wrong and aborts.

**Post-install in `[Code]` CurStepChanged:**
1. Writes updated `version.json` with incremented patch number.

**`[Files]` section:**
- `#include "patch_files.iss"` — the auto-generated file list from `build_patch.bat`.
- All entries use `Flags: ignoreversion`.

### `version.json` (project root)

```json
{"version": "1.0.2", "patch": 0}
```

Note: `edition` is only written by the installer at install time, not stored in the git-tracked file.

---

## Modified Files

### `build/windows/build.bat`

Additions at end, after ISCC compilation succeeds:

1. Auto-tag git: `git tag build/v{version}-Full` and `git tag build/v{version}-Lite`.
2. Reset `version.json` at project root to `{"version": "{version}", "patch": 0}`.

### `build/windows/installer.iss`

Additions:

1. **`[Files]`:** Add `Source: "..\..\version.json"; DestDir: "{app}"; Flags: ignoreversion`.
2. **`[Code]` CurStepChanged post-install:** After venv path fixup, write `{app}\version.json` with the correct `edition` field ("Full" or "Lite") and `patch: 0`.

---

## File Mapping Rules

The patch build script maps git paths to installer destinations:

| Git path pattern | Installer `DestDir` |
|---|---|
| Root-level `*.py`, `*.html`, `*.json`, `*.txt`, `LICENSE` | `{app}` |
| `static/*` | `{app}\static` (preserving subdirs) |
| `plugins/*` | `{app}\plugins` (preserving subdirs) |
| `locales/*` | `{app}\locales` (preserving subdirs) |
| `assets/*` | `{app}\assets` (preserving subdirs) |

**Ignored paths** (changes don't produce patch files):

- `build/*`, `docs/*`, `.github/*`, `dist/*`
- `CLAUDE.md`, `.gitignore`, `*.md` (except inside `locales/` or `plugins/`)
- `requirements.txt` — triggers build refusal, not a patch file
- Any path not matching the patterns above

---

## Safety Guarantees

1. **No partial patches:** Inno Setup is transactional — if the installer is cancelled mid-copy, files aren't left in a half-updated state.
2. **Sequential enforcement:** Patch N requires patch N-1 applied. Verified via `version.json` before any files are touched.
3. **Edition enforcement:** GPU patches won't install on CPU editions and vice versa.
4. **Dependency fence:** If `requirements.txt` changes, `build_patch.bat` refuses and tells you to do a full build.
5. **Clean working tree:** `build_patch.bat` refuses to build with uncommitted changes so the git tag accurately represents what shipped.

---

## Workflow Example

```
# After full build of v1.0.2:
#   Git tags: build/v1.0.2-Full, build/v1.0.2-Lite
#   version.json: {"version": "1.0.2", "patch": 0}

# Fix a bug in server.py and operator.html, commit it

> build\windows\build_patch.bat

  Patch build for LinguaTaxi v1.0.2
  Base tag: build/v1.0.2-Full (last build)
  Changed files: 2
    server.py -> {app}\server.py
    operator.html -> {app}\operator.html

  Compiling CPU patch...  [OK] (3 sec)
  Compiling GPU patch...  [OK] (3 sec)

  Output:
    dist\LinguaTaxi-CPU-Patch-1.0.2-p1.exe  (12 KB)
    dist\LinguaTaxi-GPU-Patch-1.0.2-p1.exe  (12 KB)

  Tagged: build/v1.0.2-p1-Full, build/v1.0.2-p1-Lite
  Updated version.json: patch 0 -> 1
```
