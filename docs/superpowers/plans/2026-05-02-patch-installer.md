# Patch Installer Framework — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enable fast (~seconds) incremental patch builds that ship only changed app files, skipping the 20-30 minute full GPU rebuild.

**Architecture:** A `build_patch.bat` script diffs git HEAD against the last build tag, generates a minimal Inno Setup file list, and compiles two small patch installers (CPU/GPU). A `version.json` file tracks version + patch level + edition, enforced by both the full and patch installers.

**Tech Stack:** Batch scripting, PowerShell (JSON parsing), Inno Setup 6 Pascal Script

---

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `version.json` | Create | Single source of truth for version/patch/edition |
| `build/windows/installer.iss` | Modify | Bundle version.json, write edition+patch at install |
| `build/windows/build.bat` | Modify | Auto-tag git, reset version.json after full build |
| `build/windows/patch_installer.iss` | Create | Minimal Inno Setup script for patch delivery |
| `build/windows/build_patch.bat` | Create | Orchestrates patch builds: diff, filter, generate, compile, tag |

---

### Task 1: Create version.json

**Files:**
- Create: `version.json`

- [ ] **Step 1: Create the version file**

Create `version.json` at the project root:

```json
{"version": "1.0.2", "patch": 0}
```

Note: `edition` is NOT stored in git — it's written by the installer at install time.

- [ ] **Step 2: Verify the file is valid JSON**

Run:
```bash
python -c "import json; json.load(open('version.json')); print('OK')"
```
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add version.json
git commit -m "[build] add version.json for patch installer framework"
```

---

### Task 2: Modify installer.iss — bundle and write version.json

**Files:**
- Modify: `build/windows/installer.iss:183-199` ([Files] section)
- Modify: `build/windows/installer.iss:615-637` (CurStepChanged)

- [ ] **Step 1: Add version.json to the [Files] section**

In `build/windows/installer.iss`, find line 199 (`Source: "..\..\LICENSE"`), and add after it:

```iss
Source: "..\..\version.json"; DestDir: "{app}"; Flags: ignoreversion
```

- [ ] **Step 2: Write version.json with edition in CurStepChanged**

In `build/windows/installer.iss`, find the `CurStepChanged` procedure. After the `// 2. Write edition.txt` block (after line 637), add:

```pascal
    // 3. Write version.json with edition
    SaveStringToFile(ExpandConstant('{app}\version.json'),
      '{"version": "{#MyAppVersion}", "patch": 0, "edition": "{#EDITION}"}',
      False);
```

Note: This overwrites the git-tracked version.json (which has no edition) with one that includes the edition field. `{#MyAppVersion}` and `{#EDITION}` are Inno Setup preprocessor constants already defined at the top of the file.

- [ ] **Step 3: Verify the .iss file compiles**

Run (just a syntax check — don't need a full build):
```cmd
"C:\Users\Laptop\AppData\Local\Programs\Inno Setup 6\ISCC.exe" /? >nul 2>&1 && echo "ISCC found"
```
Expected: `ISCC found`

- [ ] **Step 4: Commit**

```bash
git add build/windows/installer.iss
git commit -m "[build] installer.iss: bundle version.json, write edition at install"
```

---

### Task 3: Modify build.bat — auto-tag and reset version.json

**Files:**
- Modify: `build/windows/build.bat:349-371` (after ISCC compilation, before final output)

- [ ] **Step 1: Add auto-tagging and version.json reset after successful builds**

In `build/windows/build.bat`, find the `BUILD COMPLETE` banner (line 352). Insert the following block BEFORE that banner (after line 349 `echo   [OK] CPU+GPU installer built`):

```bat
:: ── Step 9: Tag git and reset version.json ──
echo.
echo   Tagging git...

:: Read version from version.json
for /f "delims=" %%v in ('powershell -Command "(Get-Content '%PROJECT_DIR%\version.json' | ConvertFrom-Json).version"') do set "APP_VER=%%v"

:: Delete existing build tags for this version (in case of rebuild)
git tag -d "build/v!APP_VER!-Full" 2>nul
git tag -d "build/v!APP_VER!-Lite" 2>nul

:: Create new tags
git tag "build/v!APP_VER!-Full"
git tag "build/v!APP_VER!-Lite"
echo   [OK] Tagged: build/v!APP_VER!-Full, build/v!APP_VER!-Lite

:: Reset version.json patch to 0 (in case it was incremented by a prior patch build)
powershell -Command "$j = Get-Content '%PROJECT_DIR%\version.json' | ConvertFrom-Json; $j.patch = 0; $j | ConvertTo-Json -Compress | Set-Content '%PROJECT_DIR%\version.json' -Encoding utf8"
echo   [OK] version.json reset to patch 0
```

- [ ] **Step 2: Verify build.bat syntax**

Run:
```cmd
cmd.exe /C "C:\Users\Laptop\Documents\LinguaTaxi\build\windows\build.bat" 2>&1 | head -20
```
Expected: Should start normally (will skip existing venvs/models and proceed to ISCC). Don't need to wait for completion — just verify no syntax errors at launch.

- [ ] **Step 3: Commit**

```bash
git add build/windows/build.bat
git commit -m "[build] build.bat: auto-tag git and reset version.json after full build"
```

---

### Task 4: Create patch_installer.iss

**Files:**
- Create: `build/windows/patch_installer.iss`

- [ ] **Step 1: Create the patch installer Inno Setup script**

Create `build/windows/patch_installer.iss`:

```iss
; ══════════════════════════════════════════════════════
; LinguaTaxi Patch Installer — Inno Setup Script
;
; Copies only changed app files into an existing install.
; Built by build_patch.bat — do NOT compile manually.
;
; Required defines (passed by build_patch.bat):
;   /DEDITION=Full|Lite
;   /DPATCH_VER=1.0.2
;   /DPATCH_NUM=1
; ══════════════════════════════════════════════════════

#ifndef EDITION
  #define EDITION "Full"
#endif
#ifndef PATCH_VER
  #define PATCH_VER "0.0.0"
#endif
#ifndef PATCH_NUM
  #define PATCH_NUM "0"
#endif

#define MyAppName "LinguaTaxi - Live Caption and Translation"
#define MyAppShortName "LinguaTaxi"

#if EDITION == "Lite"
  #define OutputName "LinguaTaxi-CPU-Patch-" + PATCH_VER + "-p" + PATCH_NUM
  #define EditionLabel "CPU Only"
  #define ExpectedEdition "Lite"
#else
  #define OutputName "LinguaTaxi-GPU-Patch-" + PATCH_VER + "-p" + PATCH_NUM
  #define EditionLabel "CPU+GPU"
  #define ExpectedEdition "Full"
#endif

[Setup]
; Same AppId as the full installer so Windows recognizes it
AppId={{B8A5C2E1-4F3D-4A7B-9E2C-1D3F5A6B7C8D}
AppName={#MyAppName} Patch
AppVersion={#PATCH_VER}.{#PATCH_NUM}
DefaultDirName={autopf}\{#MyAppName}
OutputDir=..\..\dist
OutputBaseFilename={#OutputName}
#ifexist "..\..\assets\linguataxi.ico"
SetupIconFile=..\..\assets\linguataxi.ico
#endif
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
; Patch-specific: install to existing dir, no uninstall entry
UsePreviousAppDir=yes
CreateUninstallRegKey=no
UpdateUninstallLogAppName=no
; Close running LinguaTaxi before patching
CloseApplications=yes
RestartApplications=yes
; Disable pages that don't apply to patches
DisableProgramGroupPage=yes
DisableDirPage=yes
DisableReadyPage=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Files]
; Auto-generated file list from build_patch.bat
#include "patch_files.iss"

[Code]

// ── JSON helpers (same as full installer) ──

function JsonStr(const Json, Key: String): String;
var
  Search: String;
  P, Q: Integer;
  Rest: String;
begin
  Result := '';
  Search := '"' + Key + '":';
  P := Pos(Search, Json);
  if P = 0 then Exit;
  Rest := Copy(Json, P + Length(Search), Length(Json));
  Rest := Trim(Rest);
  if (Length(Rest) > 0) and (Rest[1] = '"') then begin
    Rest := Copy(Rest, 2, Length(Rest));
    Q := Pos('"', Rest);
    if Q > 0 then
      Result := Copy(Rest, 1, Q - 1);
  end;
end;

function JsonInt(const Json, Key: String): Integer;
var
  Search: String;
  P, I: Integer;
  Rest, Num: String;
begin
  Result := 0;
  Search := '"' + Key + '":';
  P := Pos(Search, Json);
  if P = 0 then Exit;
  Rest := Trim(Copy(Json, P + Length(Search), Length(Json)));
  Num := '';
  for I := 1 to Length(Rest) do begin
    if (Rest[I] >= '0') and (Rest[I] <= '9') then
      Num := Num + Rest[I]
    else
      Break;
  end;
  Result := StrToIntDef(Num, 0);
end;

// ── Version check ──

function InitializeSetup(): Boolean;
var
  VersionPath, Content: String;
  ContentA: AnsiString;
  InstalledVer, InstalledEdition: String;
  InstalledPatch, RequiredPatch: Integer;
begin
  Result := False;

  // Find the existing install directory
  VersionPath := ExpandConstant('{autopf}\{#MyAppName}\version.json');
  if not FileExists(VersionPath) then begin
    MsgBox('LinguaTaxi is not installed, or version.json is missing.' + #13#10 +
           'Please install the full version first.' + #13#10 + #13#10 +
           'This patch requires: v{#PATCH_VER}',
           mbError, MB_OK);
    Exit;
  end;

  // Read and parse version.json
  if not LoadStringFromFile(VersionPath, ContentA) then begin
    MsgBox('Could not read version.json.', mbError, MB_OK);
    Exit;
  end;
  Content := String(ContentA);

  InstalledVer := JsonStr(Content, 'version');
  InstalledEdition := JsonStr(Content, 'edition');
  InstalledPatch := JsonInt(Content, 'patch');
  RequiredPatch := {#PATCH_NUM} - 1;

  // Check version
  if InstalledVer <> '{#PATCH_VER}' then begin
    MsgBox('Version mismatch!' + #13#10 + #13#10 +
           'Installed: v' + InstalledVer + #13#10 +
           'This patch requires: v{#PATCH_VER}' + #13#10 + #13#10 +
           'Please install the matching full version first.',
           mbError, MB_OK);
    Exit;
  end;

  // Check edition
  if InstalledEdition <> '{#ExpectedEdition}' then begin
    MsgBox('Edition mismatch!' + #13#10 + #13#10 +
           'Installed edition: ' + InstalledEdition + #13#10 +
           'This patch is for: {#ExpectedEdition} ({#EditionLabel})' + #13#10 + #13#10 +
           'Please use the correct patch for your edition.',
           mbError, MB_OK);
    Exit;
  end;

  // Check patch level (must be exactly N-1)
  if InstalledPatch <> RequiredPatch then begin
    if InstalledPatch < RequiredPatch then
      MsgBox('Patch level too low!' + #13#10 + #13#10 +
             'Installed: v' + InstalledVer + ' patch ' + IntToStr(InstalledPatch) + #13#10 +
             'This patch requires: patch ' + IntToStr(RequiredPatch) + ' applied first.' + #13#10 + #13#10 +
             'Please install the missing patches in order.',
             mbError, MB_OK)
    else
      MsgBox('Patch already applied!' + #13#10 + #13#10 +
             'Installed: v' + InstalledVer + ' patch ' + IntToStr(InstalledPatch) + #13#10 +
             'This patch: p{#PATCH_NUM}' + #13#10 + #13#10 +
             'Your installation is already at or beyond this patch level.',
             mbInformation, MB_OK);
    Exit;
  end;

  Result := True;
end;

// ── Post-install: bump patch number in version.json ──

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then begin
    SaveStringToFile(
      ExpandConstant('{app}\version.json'),
      '{"version": "{#PATCH_VER}", "patch": {#PATCH_NUM}, "edition": "{#ExpectedEdition}"}',
      False);
  end;
end;

// ── Ready memo ──

function UpdateReadyMemo(Space, NewLine, MemoUserInfoInfo, MemoDirInfo,
  MemoTypeInfo, MemoComponentsInfo, MemoGroupInfo, MemoTasksInfo: String): String;
begin
  Result := 'APPLYING PATCH {#PATCH_NUM} to LinguaTaxi v{#PATCH_VER} ({#EditionLabel}).' + NewLine + NewLine +
            'Only changed program files will be updated.' + NewLine +
            'Models, transcripts, and settings are not affected.' + NewLine + NewLine;
  if MemoDirInfo <> '' then
    Result := Result + MemoDirInfo + NewLine;
end;
```

- [ ] **Step 2: Create a placeholder patch_files.iss so the .iss can be parsed**

Create `build/windows/patch_files.iss`:

```iss
; Auto-generated by build_patch.bat — do not edit manually.
; This file is overwritten on each patch build.
```

- [ ] **Step 3: Commit**

```bash
git add build/windows/patch_installer.iss build/windows/patch_files.iss
git commit -m "[build] add patch_installer.iss — minimal Inno Setup script for patch delivery"
```

---

### Task 5: Create build_patch.bat

**Files:**
- Create: `build/windows/build_patch.bat`

- [ ] **Step 1: Create the patch build script**

Create `build/windows/build_patch.bat`:

```bat
@echo off
setlocal EnableDelayedExpansion
:: ════════════════════════════════════════════════════════
:: LinguaTaxi — Patch Installer Build Script
::
:: Builds small patch installers containing only files
:: changed since the last full build or patch build.
::
:: Prerequisites:
::   - Inno Setup 6+
::   - Git
::   - An existing full build (build tags must exist)
::   - Clean working tree (all changes committed)
::
:: Output:
::   dist\LinguaTaxi-GPU-Patch-{ver}-p{N}.exe
::   dist\LinguaTaxi-CPU-Patch-{ver}-p{N}.exe
:: ════════════════════════════════════════════════════════

title LinguaTaxi - Patch Build

set "SCRIPT_DIR=%~dp0"
set "PROJECT_DIR=%SCRIPT_DIR%..\.."
set "DIST_DIR=%PROJECT_DIR%\dist"
set "VERSION_FILE=%PROJECT_DIR%\version.json"

echo.
echo   ========================================
echo     LinguaTaxi - Patch Build
echo   ========================================
echo.

:: ── Step 1: Find Inno Setup ──
set "ISCC="
for %%p in (
    "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
    "%ProgramFiles%\Inno Setup 6\ISCC.exe"
    "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
) do (
    if exist "%%~p" set "ISCC=%%~p"
)

if not defined ISCC (
    echo   ERROR: Inno Setup 6 not found.
    echo   Download from: https://jrsoftware.org/isinfo.php
    if not defined CI pause
    exit /b 1
)
echo   [OK] Inno Setup: %ISCC%

:: ── Step 2: Read version.json ──
if not exist "%VERSION_FILE%" (
    echo   ERROR: version.json not found at project root.
    echo   Run a full build first to create it.
    if not defined CI pause
    exit /b 1
)

for /f "delims=" %%v in ('powershell -Command "(Get-Content '%VERSION_FILE%' | ConvertFrom-Json).version"') do set "APP_VER=%%v"
for /f "delims=" %%p in ('powershell -Command "(Get-Content '%VERSION_FILE%' | ConvertFrom-Json).patch"') do set "CUR_PATCH=%%p"

if not defined APP_VER (
    echo   ERROR: Could not read version from version.json.
    if not defined CI pause
    exit /b 1
)

set /a NEW_PATCH=!CUR_PATCH!+1
echo   Version: v!APP_VER!  (current patch: !CUR_PATCH!, building patch: !NEW_PATCH!)

:: ── Step 3: Check for clean working tree ──
for /f %%c in ('git -C "%PROJECT_DIR%" status --porcelain ^| find /c /v ""') do set "DIRTY=%%c"
if !DIRTY! GTR 0 (
    echo.
    echo   ERROR: Working tree has uncommitted changes.
    echo   Please commit all changes before building a patch.
    echo.
    git -C "%PROJECT_DIR%" status --short
    if not defined CI pause
    exit /b 1
)
echo   [OK] Working tree clean

:: ── Step 4: Find the latest build tag ──
set "BASE_TAG="
:: Try patch tags first (most recent patch), then full build tag
for /f "delims=" %%t in ('git -C "%PROJECT_DIR%" tag -l "build/v!APP_VER!-p*" --sort=-version:refname 2^>nul') do (
    if not defined BASE_TAG set "BASE_TAG=%%t"
)
:: If no patch tags, use the full build tag
if not defined BASE_TAG (
    for /f "delims=" %%t in ('git -C "%PROJECT_DIR%" tag -l "build/v!APP_VER!-Full" 2^>nul') do set "BASE_TAG=%%t"
)
if not defined BASE_TAG (
    for /f "delims=" %%t in ('git -C "%PROJECT_DIR%" tag -l "build/v!APP_VER!-Lite" 2^>nul') do set "BASE_TAG=%%t"
)

if not defined BASE_TAG (
    echo.
    echo   ERROR: No build tag found for v!APP_VER!.
    echo   Expected tags like: build/v!APP_VER!-Full or build/v!APP_VER!-p1-Full
    echo   Run a full build first.
    if not defined CI pause
    exit /b 1
)
echo   [OK] Base tag: !BASE_TAG!

:: ── Step 5: Get changed files ──
set "DIFF_FILE=%SCRIPT_DIR%patch_diff.txt"
git -C "%PROJECT_DIR%" diff --name-only --diff-filter=ACMR "!BASE_TAG!" HEAD > "%DIFF_FILE%" 2>nul

:: Check for requirements.txt changes (safety fence)
findstr /x "requirements.txt" "%DIFF_FILE%" >nul 2>&1
if !ERRORLEVEL! EQU 0 (
    echo.
    echo   ERROR: requirements.txt has changed since !BASE_TAG!.
    echo   New pip dependencies require a FULL build, not a patch.
    echo   Please run build.bat instead.
    del "%DIFF_FILE%" 2>nul
    if not defined CI pause
    exit /b 1
)

:: Check for venv/python_dist changes
findstr /i "^build/windows/venv_ ^build/windows/python_dist/" "%DIFF_FILE%" >nul 2>&1
if !ERRORLEVEL! EQU 0 (
    echo.
    echo   ERROR: Venv or Python distribution files changed since !BASE_TAG!.
    echo   These require a FULL build. Please run build.bat instead.
    del "%DIFF_FILE%" 2>nul
    if not defined CI pause
    exit /b 1
)

:: ── Step 6: Filter to patchable app files and generate patch_files.iss ──
set "ISS_FILE=%SCRIPT_DIR%patch_files.iss"
set "FILE_COUNT=0"

echo ; Auto-generated by build_patch.bat — do not edit manually.> "%ISS_FILE%"
echo ; Patch !NEW_PATCH! for v!APP_VER! — built %date% %time%>> "%ISS_FILE%"

echo.
echo   Changed files:

for /f "usebackq delims=" %%f in ("%DIFF_FILE%") do (
    set "FPATH=%%f"
    set "MAPPED="

    :: Root-level app files (.py, .html, .json, .txt, LICENSE)
    echo !FPATH! | findstr /r "^[^/\\]*\.py$ ^[^/\\]*\.html$ ^[^/\\]*\.json$ ^[^/\\]*\.txt$ ^LICENSE$" >nul 2>&1
    if !ERRORLEVEL! EQU 0 (
        :: Exclude files that aren't part of the app bundle
        echo !FPATH! | findstr /i "^CLAUDE.md$ ^requirements.txt$" >nul 2>&1
        if !ERRORLEVEL! NEQ 0 (
            echo Source: "..\..\!FPATH!"; DestDir: "{app}"; Flags: ignoreversion>> "%ISS_FILE%"
            echo     !FPATH! -^> {app}\!FPATH!
            set /a FILE_COUNT+=1
            set "MAPPED=1"
        )
    )

    :: static/*
    if not defined MAPPED (
        echo !FPATH! | findstr /i "^static/" >nul 2>&1
        if !ERRORLEVEL! EQU 0 (
            :: Get the subdirectory portion for DestDir
            for %%d in ("!FPATH!") do set "DEST_SUB=%%~dpf"
            echo Source: "..\..\!FPATH!"; DestDir: "{app}\static"; Flags: ignoreversion>> "%ISS_FILE%"
            echo     !FPATH! -^> {app}\!FPATH!
            set /a FILE_COUNT+=1
            set "MAPPED=1"
        )
    )

    :: plugins/*
    if not defined MAPPED (
        echo !FPATH! | findstr /i "^plugins/" >nul 2>&1
        if !ERRORLEVEL! EQU 0 (
            echo Source: "..\..\!FPATH!"; DestDir: "{app}\plugins"; Flags: ignoreversion>> "%ISS_FILE%"
            echo     !FPATH! -^> {app}\!FPATH!
            set /a FILE_COUNT+=1
            set "MAPPED=1"
        )
    )

    :: locales/*
    if not defined MAPPED (
        echo !FPATH! | findstr /i "^locales/" >nul 2>&1
        if !ERRORLEVEL! EQU 0 (
            echo Source: "..\..\!FPATH!"; DestDir: "{app}\locales"; Flags: ignoreversion>> "%ISS_FILE%"
            echo     !FPATH! -^> {app}\!FPATH!
            set /a FILE_COUNT+=1
            set "MAPPED=1"
        )
    )

    :: assets/*
    if not defined MAPPED (
        echo !FPATH! | findstr /i "^assets/" >nul 2>&1
        if !ERRORLEVEL! EQU 0 (
            echo Source: "..\..\!FPATH!"; DestDir: "{app}\assets"; Flags: ignoreversion>> "%ISS_FILE%"
            echo     !FPATH! -^> {app}\!FPATH!
            set /a FILE_COUNT+=1
            set "MAPPED=1"
        )
    )

    :: Everything else is ignored (build/*, docs/*, .github/*, .md files, etc.)
)

del "%DIFF_FILE%" 2>nul

if !FILE_COUNT! EQU 0 (
    echo.
    echo   No patchable app files changed since !BASE_TAG!.
    echo   Nothing to build.
    if not defined CI pause
    exit /b 0
)

echo.
echo   Total patchable files: !FILE_COUNT!

:: ── Step 7: Compile patch installers ──
mkdir "%DIST_DIR%" 2>nul

echo.
echo   --- Compiling CPU patch installer ---
echo.

"%ISCC%" /DEDITION=Lite /DPATCH_VER=!APP_VER! /DPATCH_NUM=!NEW_PATCH! "%SCRIPT_DIR%patch_installer.iss"

if !ERRORLEVEL! NEQ 0 (
    echo   [FAIL] CPU patch installer — check errors above.
    if not defined CI pause
    exit /b 1
)
echo   [OK] CPU patch built

echo.
echo   --- Compiling GPU patch installer ---
echo.

"%ISCC%" /DEDITION=Full /DPATCH_VER=!APP_VER! /DPATCH_NUM=!NEW_PATCH! "%SCRIPT_DIR%patch_installer.iss"

if !ERRORLEVEL! NEQ 0 (
    echo   [FAIL] GPU patch installer — check errors above.
    if not defined CI pause
    exit /b 1
)
echo   [OK] GPU patch built

:: ── Step 8: Tag git ──
echo.
echo   Tagging git...
git -C "%PROJECT_DIR%" tag "build/v!APP_VER!-p!NEW_PATCH!-Full"
git -C "%PROJECT_DIR%" tag "build/v!APP_VER!-p!NEW_PATCH!-Lite"
echo   [OK] Tagged: build/v!APP_VER!-p!NEW_PATCH!-Full, build/v!APP_VER!-p!NEW_PATCH!-Lite

:: ── Step 9: Update version.json ──
powershell -Command "$j = Get-Content '%VERSION_FILE%' | ConvertFrom-Json; $j.patch = !NEW_PATCH!; $j | ConvertTo-Json -Compress | Set-Content '%VERSION_FILE%' -Encoding utf8"
echo   [OK] version.json updated: patch !CUR_PATCH! -^> !NEW_PATCH!

echo.
echo   ========================================
echo     PATCH BUILD COMPLETE
echo   ========================================
echo.
echo   Output:
if exist "%DIST_DIR%\LinguaTaxi-GPU-Patch-!APP_VER!-p!NEW_PATCH!.exe" (
    echo     dist\LinguaTaxi-GPU-Patch-!APP_VER!-p!NEW_PATCH!.exe
)
if exist "%DIST_DIR%\LinguaTaxi-CPU-Patch-!APP_VER!-p!NEW_PATCH!.exe" (
    echo     dist\LinguaTaxi-CPU-Patch-!APP_VER!-p!NEW_PATCH!.exe
)
echo.
echo   Base tag: !BASE_TAG!
echo   Files patched: !FILE_COUNT!
echo.

if not defined CI pause
```

- [ ] **Step 2: Commit**

```bash
git add build/windows/build_patch.bat
git commit -m "[build] add build_patch.bat — fast incremental patch installer builder"
```

---

### Task 6: Fix file path mapping for subdirectories

The `Source:` lines generated in Task 5 for files inside `static/`, `plugins/`, `locales/`, and `assets/` need correct subdirectory handling. Inno Setup's `Source:` directive copies a single file, and `DestDir:` must include the full subdirectory path.

**Files:**
- Modify: `build/windows/build_patch.bat`

- [ ] **Step 1: Update the file mapping to use a PowerShell helper for subdirectory paths**

Replace the file-mapping `for` loop in `build_patch.bat` (the entire block from `for /f "usebackq delims=" %%f in ("%DIFF_FILE%") do (` through the closing `)` before `del "%DIFF_FILE%"`) with the following version that uses PowerShell for robust path handling:

```bat
:: Use PowerShell for robust path mapping — batch string manipulation is fragile
:: with forward slashes, backslashes, and subdirectory extraction.
powershell -Command ^
  "$lines = Get-Content '%DIFF_FILE%';" ^
  "$iss = @();" ^
  "$count = 0;" ^
  "$mapped = @();" ^
  "foreach ($f in $lines) {" ^
  "  $f = $f.Trim();" ^
  "  if (-not $f) { continue }" ^
  "  $fb = $f -replace '/', '\';" ^
  "  # Root-level app files" ^
  "  if ($f -notmatch '/' -and $f -match '\.(py|html|json|txt)$|^LICENSE$') {" ^
  "    if ($f -notin @('CLAUDE.md','requirements.txt','.gitignore')) {" ^
  "      $iss += 'Source: \"..\..\{0}\"; DestDir: \"{1}app\"; Flags: ignoreversion' -f $fb,'{'" ^
  "      $mapped += '    {0} -> {{app}}\{1}' -f $f,$fb" ^
  "      $count++" ^
  "    }" ^
  "  }" ^
  "  # Subdirectory files: static/, plugins/, locales/, assets/" ^
  "  elseif ($f -match '^(static|plugins|locales|assets)/(.+)$') {" ^
  "    $topDir = $matches[1];" ^
  "    $relPath = $matches[2] -replace '/', '\';" ^
  "    $subDir = Split-Path $relPath -Parent;" ^
  "    $destDir = if ($subDir) { '{app}\' + $topDir + '\' + $subDir } else { '{app}\' + $topDir };" ^
  "    $destDir = $destDir -replace '\\$','';" ^
  "    $iss += 'Source: \"..\..\{0}\"; DestDir: \"{1}{2}\"; Flags: ignoreversion' -f $fb,'{', $destDir.Substring(1)" ^
  "    $mapped += '    {0} -> {1}' -f $f,$destDir" ^
  "    $count++" ^
  "  }" ^
  "}" ^
  "$header = '; Auto-generated by build_patch.bat — do not edit manually.';" ^
  "$header += \"`n; Patch %NEW_PATCH% for v%APP_VER% — built $(Get-Date -Format ''yyyy-MM-dd HH:mm'')\";" ^
  "($header + \"`n\" + ($iss -join \"`n\")) | Set-Content '%ISS_FILE%' -Encoding ascii;" ^
  "$mapped | ForEach-Object { Write-Host $_ };" ^
  "Write-Host \"\";" ^
  "Write-Host \"  Total patchable files: $count\";" ^
  "Set-Content '%SCRIPT_DIR%patch_count.txt' $count -Encoding ascii"

:: Read file count back into batch
set /p FILE_COUNT=<"%SCRIPT_DIR%patch_count.txt"
del "%SCRIPT_DIR%patch_count.txt" 2>nul
```

Note: This is a complex replacement. A cleaner approach is to extract the mapping logic into a small standalone PowerShell script. But given the project convention of self-contained .bat files, inline PowerShell is more consistent with the existing build.bat pattern.

- [ ] **Step 2: Verify by inspecting generated patch_files.iss content**

After running build_patch.bat, check that `build/windows/patch_files.iss` contains correct Inno Setup `Source:` lines with proper subdirectory paths. For example, a changed `plugins/fact_checker/panel.js` should produce:

```
Source: "..\..\plugins\fact_checker\panel.js"; DestDir: "{app}\plugins\fact_checker"; Flags: ignoreversion
```

- [ ] **Step 3: Commit**

```bash
git add build/windows/build_patch.bat
git commit -m "[build] build_patch.bat: use PowerShell for robust subdirectory path mapping"
```

---

### Task 7: End-to-end verification

- [ ] **Step 1: Create build tags for the current state**

Since there's no prior full build with tags, create the initial tags manually:

```bash
git tag build/v1.0.2-Full
git tag build/v1.0.2-Lite
```

- [ ] **Step 2: Make a test change and commit it**

Make a trivial change to server.py (e.g., add/remove a blank line at the end), and commit:

```bash
git add server.py
git commit -m "[test] trivial change to verify patch build"
```

- [ ] **Step 3: Run build_patch.bat**

```cmd
cmd.exe /C "C:\Users\Laptop\Documents\LinguaTaxi\build\windows\build_patch.bat"
```

Expected output:
```
  Version: v1.0.2  (current patch: 0, building patch: 1)
  [OK] Working tree clean
  [OK] Base tag: build/v1.0.2-Full (or similar)
  Changed files:
    server.py -> {app}\server.py
  Total patchable files: 1
  Compiling CPU patch installer...  [OK]
  Compiling GPU patch installer...  [OK]
  Tagged: build/v1.0.2-p1-Full, build/v1.0.2-p1-Lite
  version.json updated: patch 0 -> 1
  PATCH BUILD COMPLETE
```

- [ ] **Step 4: Verify outputs**

```bash
ls -lh dist/LinguaTaxi-*Patch*
```

Expected: Two small .exe files (< 1 MB each).

- [ ] **Step 5: Verify version.json was updated**

```bash
cat version.json
```

Expected: `{"version":"1.0.2","patch":1}`

- [ ] **Step 6: Verify git tags were created**

```bash
git tag -l "build/v1.0.2-p1*"
```

Expected:
```
build/v1.0.2-p1-Full
build/v1.0.2-p1-Lite
```

- [ ] **Step 7: Revert the test commit and tags**

```bash
git tag -d build/v1.0.2-p1-Full build/v1.0.2-p1-Lite
git reset --soft HEAD~1
git checkout -- server.py
```

Reset version.json back to patch 0:
```bash
python -c "import json; d=json.load(open('version.json')); d['patch']=0; json.dump(d, open('version.json','w'))"
```

- [ ] **Step 8: Final commit with any fixes discovered during testing**

```bash
git add -A
git commit -m "[build] patch installer framework — verified end-to-end"
```
