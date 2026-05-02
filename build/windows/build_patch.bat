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

echo.
echo   Changed files:

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

del "%DIFF_FILE%" 2>nul

if !FILE_COUNT! EQU 0 (
    echo.
    echo   No patchable app files changed since !BASE_TAG!.
    echo   Nothing to build.
    if not defined CI pause
    exit /b 0
)

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
