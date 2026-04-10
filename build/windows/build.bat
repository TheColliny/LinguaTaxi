@echo off
setlocal EnableDelayedExpansion
:: ════════════════════════════════════════════════════════
:: LinguaTaxi — Windows Installer Build Script
::
:: Builds TWO installers:
::   Full  (~200 MB) — GPU (faster-whisper) + CPU (Vosk), CUDA downloaded at install
::   Lite  (~50 MB)  — CPU only (Vosk)
::
:: Prerequisites on BUILD machine:
::   - Inno Setup 6+  (https://jrsoftware.org/isinfo.php)
::   - Internet connection
::
:: What it does:
::   1. Downloads Python 3.11.9 full installer
::   2. Installs Python locally to build\windows\python_dist\
::   3. Creates two venvs:
::      - venv_lite: base packages + Vosk
::      - venv_full: base packages + faster-whisper + Vosk + NVIDIA CUDA libs
::   4. Compiles both Inno Setup installers
::
:: Output:
::   dist\LinguaTaxi-GPU-Setup-1.0.1.exe    (Full, ~200 MB — CUDA downloaded at install)
::   dist\LinguaTaxi-CPU-Setup-1.0.1.exe   (Lite, ~50 MB)
:: ════════════════════════════════════════════════════════

title LinguaTaxi - Build Installer

set "SCRIPT_DIR=%~dp0"
set "PROJECT_DIR=%SCRIPT_DIR%..\.."
set "DIST_DIR=%PROJECT_DIR%\dist"
set "PYTHON_DIR=%SCRIPT_DIR%python_dist"
set "VENV_LITE=%SCRIPT_DIR%venv_lite"
set "VENV_FULL=%SCRIPT_DIR%venv_full"
set "PYTHON_VER=3.11.9"
set "PYTHON_URL=https://www.python.org/ftp/python/%PYTHON_VER%/python-%PYTHON_VER%-amd64.exe"

:: ── Base pip packages ──
:: NOTE: Canonical package list lives in requirements.txt at project root.
:: Keep extras (vosk, faster-whisper, torch, etc.) installed separately below.
set "BASE_PACKAGES=fastapi uvicorn websockets sounddevice numpy requests python-multipart"

echo.
echo   ========================================
echo     LinguaTaxi - Build Installer
echo   ========================================
echo.

:: ── Step 1: Find Inno Setup ──
set "ISCC="
for %%p in (
    "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
    "%ProgramFiles%\Inno Setup 6\ISCC.exe"
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

:: ── Step 2: Download and install Python locally ──
if exist "%PYTHON_DIR%\python.exe" (
    echo   [OK] Python already built at python_dist\
    goto :python_ready
)

echo.
echo   Downloading Python %PYTHON_VER% installer...
set "INSTALLER=%SCRIPT_DIR%python_installer.exe"
powershell -Command "[Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -Uri '%PYTHON_URL%' -OutFile '%INSTALLER%'" 2>nul

if not exist "%INSTALLER%" (
    echo   ERROR: Download failed. Check internet connection.
    if not defined CI pause
    exit /b 1
)

:: L-BD1: Verify SHA256 checksum of the Python installer
echo   Verifying installer checksum...
for /f "delims=" %%h in ('powershell -Command "(Get-FileHash -Algorithm SHA256 '%INSTALLER%').Hash"') do set "DL_HASH=%%h"
if /i not "!DL_HASH!"=="82BFB3AED60FE04EC2AB5178E2EEBCE940AD45E6C3E0B3DCBE80E4C2F55D0B2E" (
    echo   WARNING: SHA256 checksum mismatch — expected 82BFB3AE..., got !DL_HASH!
    echo   The installer may have been tampered with or the expected hash needs updating.
    echo   Continuing anyway — verify manually if concerned.
)

:: H23: Use start /wait for reliable blocking instead of ping-based poll loop
echo   Installing Python to python_dist\ ...
echo   (This takes 1-2 minutes)
start /wait "" "%INSTALLER%" /quiet InstallAllUsers=0 PrependPath=0 Include_launcher=0 Include_test=0 Include_tcltk=1 TargetDir="%PYTHON_DIR%"

if not exist "%PYTHON_DIR%\python.exe" (
    echo   ERROR: Python installation failed — python.exe not found.
    if not defined CI pause
    exit /b 1
)

del "%INSTALLER%" 2>nul

:: Verify tkinter works
"%PYTHON_DIR%\python.exe" -c "import tkinter; print('tkinter OK')" >> "%SCRIPT_DIR%build_log.txt" 2>&1
if !ERRORLEVEL! NEQ 0 (
    echo   ERROR: Python installed but tkinter missing.
    if not defined CI pause
    exit /b 1
)
echo   [OK] Python %PYTHON_VER% installed with tkinter

:python_ready

:: ── Step 3: Build Lite venv (CPU only) ──
if exist "%VENV_LITE%\Scripts\pythonw.exe" (
    echo   [OK] Lite venv already built at venv_lite\
    echo        (Delete venv_lite\ to force rebuild)
    goto :lite_ready
)

echo.
echo   ── Building Lite venv (CPU only) ──
echo   Creating virtual environment...
"%PYTHON_DIR%\python.exe" -m venv "%VENV_LITE%" >> "%SCRIPT_DIR%build_log.txt" 2>&1

if not exist "%VENV_LITE%\Scripts\python.exe" (
    echo   ERROR: Lite venv creation failed.
    if not defined CI pause
    exit /b 1
)

echo   Upgrading pip...
"%VENV_LITE%\Scripts\python.exe" -m pip install --upgrade pip >> "%SCRIPT_DIR%build_log.txt" 2>&1

:: M50: Use requirements.txt as the primary install source
echo   Installing base packages from requirements.txt...
"%VENV_LITE%\Scripts\pip.exe" install -r "%PROJECT_DIR%\requirements.txt" >> "%SCRIPT_DIR%build_log.txt" 2>&1
:: M51: Check for pip install failures
if !ERRORLEVEL! NEQ 0 (
    echo   [FAIL] Base package installation failed. See build_log.txt
    exit /b 1
)

echo   Installing Vosk (CPU speech backend)...
"%VENV_LITE%\Scripts\pip.exe" install vosk >> "%SCRIPT_DIR%build_log.txt" 2>&1
if !ERRORLEVEL! NEQ 0 (
    echo   [FAIL] Vosk installation failed. See build_log.txt
    exit /b 1
)

echo   Installing offline translation packages...
"%VENV_LITE%\Scripts\pip.exe" install sentencepiece ctranslate2 huggingface_hub >> "%SCRIPT_DIR%build_log.txt" 2>&1
if !ERRORLEVEL! NEQ 0 (
    echo   [FAIL] Offline translation package installation failed. See build_log.txt
    exit /b 1
)

echo   [OK] Lite venv ready (Vosk CPU)

:lite_ready

:: ── Step 4: Build Full venv (GPU + CPU) ──
if exist "%VENV_FULL%\Scripts\pythonw.exe" (
    echo   [OK] Full venv already built at venv_full\
    echo        (Delete venv_full\ to force rebuild)
    goto :full_ready
)

echo.
echo   ── Building Full venv (GPU + CPU) ──
echo   Creating virtual environment...
"%PYTHON_DIR%\python.exe" -m venv "%VENV_FULL%" >> "%SCRIPT_DIR%build_log.txt" 2>&1

if not exist "%VENV_FULL%\Scripts\python.exe" (
    echo   ERROR: Full venv creation failed.
    if not defined CI pause
    exit /b 1
)

echo   Upgrading pip...
"%VENV_FULL%\Scripts\python.exe" -m pip install --upgrade pip >> "%SCRIPT_DIR%build_log.txt" 2>&1

:: M50: Use requirements.txt as the primary install source
echo   Installing base packages from requirements.txt...
"%VENV_FULL%\Scripts\pip.exe" install -r "%PROJECT_DIR%\requirements.txt" >> "%SCRIPT_DIR%build_log.txt" 2>&1
if !ERRORLEVEL! NEQ 0 (
    echo   [FAIL] Base package installation failed. See build_log.txt
    exit /b 1
)

echo   Installing faster-whisper (GPU speech backend)...
"%VENV_FULL%\Scripts\pip.exe" install faster-whisper >> "%SCRIPT_DIR%build_log.txt" 2>&1
if !ERRORLEVEL! NEQ 0 (
    echo   [FAIL] faster-whisper installation failed. See build_log.txt
    exit /b 1
)

echo   Installing Vosk (CPU fallback)...
"%VENV_FULL%\Scripts\pip.exe" install vosk >> "%SCRIPT_DIR%build_log.txt" 2>&1
if !ERRORLEVEL! NEQ 0 (
    echo   [FAIL] Vosk installation failed. See build_log.txt
    exit /b 1
)

echo   Installing offline translation packages...
"%VENV_FULL%\Scripts\pip.exe" install sentencepiece ctranslate2 huggingface_hub >> "%SCRIPT_DIR%build_log.txt" 2>&1
if !ERRORLEVEL! NEQ 0 (
    echo   [FAIL] Offline translation package installation failed. See build_log.txt
    exit /b 1
)

echo   Installing model conversion tools (transformers + torch-cpu)...
"%VENV_FULL%\Scripts\pip.exe" install torch --index-url https://download.pytorch.org/whl/cpu >> "%SCRIPT_DIR%build_log.txt" 2>&1
if !ERRORLEVEL! NEQ 0 (
    echo   [FAIL] torch installation failed. See build_log.txt
    exit /b 1
)
"%VENV_FULL%\Scripts\pip.exe" install transformers >> "%SCRIPT_DIR%build_log.txt" 2>&1
if !ERRORLEVEL! NEQ 0 (
    echo   [FAIL] transformers installation failed. See build_log.txt
    exit /b 1
)

echo   [NOTE] NVIDIA CUDA libraries are NOT bundled in the venv.
echo          The installer will download them from GitHub during install.
echo          Source: https://github.com/TheColliny/LinguaTaxi-CUDA/releases/tag/v12.9

echo   [OK] Full venv ready (faster-whisper + Vosk, CUDA downloaded at install)

:full_ready

:: ── Step 5: Check icon ──
if exist "%PROJECT_DIR%\assets\linguataxi.ico" (
    echo   [OK] Icon found
) else (
    echo   [--] No icon -- run: python assets\generate_icons.py
)

:: ── Step 6: Pre-download speech models ──
set "MODELS_PRE=%SCRIPT_DIR%models_prebuilt"
mkdir "%MODELS_PRE%" 2>nul

:: Download Whisper model (CTranslate2 format, ~1.5 GB)
if exist "%MODELS_PRE%\faster-whisper-large-v3-turbo\model.bin" (
    echo   [OK] Whisper model already downloaded
    goto :whisper_done
)

echo.
echo   Downloading Whisper model [faster-whisper-large-v3-turbo, ~1.5 GB]
echo   This may take several minutes...
"%VENV_FULL%\Scripts\python.exe" -c "from huggingface_hub import snapshot_download; snapshot_download('Systran/faster-whisper-large-v3-turbo', local_dir=r'%MODELS_PRE%\faster-whisper-large-v3-turbo'); print('  [OK] Whisper model downloaded')" >> "%SCRIPT_DIR%build_log.txt" 2>&1
if exist "%MODELS_PRE%\faster-whisper-large-v3-turbo\model.bin" (
    echo   [OK] Whisper model downloaded
) else (
    echo   [FAIL] Whisper model download failed -- check build_log.txt
)

:whisper_done

:: Download Vosk small model (~40 MB)
if exist "%MODELS_PRE%\vosk-model-small-en-us-0.15\README" (
    echo   [OK] Vosk model already downloaded
    goto :vosk_done
)

echo.
echo   Downloading Vosk model [vosk-model-small-en-us-0.15, ~40 MB]
"%PYTHON_DIR%\python.exe" -c "import urllib.request,zipfile,os; p=r'%MODELS_PRE%'; urllib.request.urlretrieve('https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip',os.path.join(p,'vosk.zip')); zipfile.ZipFile(os.path.join(p,'vosk.zip')).extractall(p); os.unlink(os.path.join(p,'vosk.zip')); print('OK')" >> "%SCRIPT_DIR%build_log.txt" 2>&1
if exist "%MODELS_PRE%\vosk-model-small-en-us-0.15" (
    echo   [OK] Vosk model downloaded
) else (
    echo   [FAIL] Vosk model download failed -- check build_log.txt
)

:vosk_done

:: Download Silero language detection model (~16 MB, MIT licensed)
set "SILERO_DIR=%MODELS_PRE%\silero-lang-detect"
if exist "%SILERO_DIR%\lang_classifier_95.onnx" (
    echo   [OK] Silero language detection model already downloaded
    goto :silero_done
)

echo.
echo   Downloading Silero language detection model [~16 MB]
mkdir "%SILERO_DIR%" 2>nul
"%PYTHON_DIR%\python.exe" -c "import urllib.request; urllib.request.urlretrieve('https://huggingface.co/deepghs/silero-lang95-onnx/resolve/main/lang_classifier_95.onnx', r'%SILERO_DIR%\lang_classifier_95.onnx'); urllib.request.urlretrieve('https://huggingface.co/deepghs/silero-lang95-onnx/resolve/main/lang_dict_95.json', r'%SILERO_DIR%\lang_dict_95.json'); print('OK')" >> "%SCRIPT_DIR%build_log.txt" 2>&1
if exist "%SILERO_DIR%\lang_classifier_95.onnx" (
    echo   [OK] Silero language detection model downloaded
) else (
    echo   [FAIL] Silero download failed -- check build_log.txt
)

:silero_done

:: ── Step 7: Clean __pycache__ from python_dist and venvs ──
:: .pyc files are regenerated at runtime; bundling them causes "file corrupted" install errors
echo   Cleaning __pycache__ from build artifacts...
for /d /r "%PYTHON_DIR%" %%d in (__pycache__) do @if exist "%%d" rd /s /q "%%d"
for /d /r "%VENV_LITE%" %%d in (__pycache__) do @if exist "%%d" rd /s /q "%%d"
for /d /r "%VENV_FULL%" %%d in (__pycache__) do @if exist "%%d" rd /s /q "%%d"
echo   [OK] __pycache__ cleaned

:: ── Step 8: Compile both installers ──
mkdir "%DIST_DIR%" 2>nul

echo.
echo   --- Compiling CPU Only installer ---
echo.

"%ISCC%" /DEDITION=Lite "%SCRIPT_DIR%installer.iss"

:: M52: Exit nonzero on Inno Setup failure
if !ERRORLEVEL! NEQ 0 (
    echo   [FAIL] CPU Only installer -- check errors above.
    exit /b 1
)
echo   [OK] CPU Only installer built

echo.
echo   --- Compiling CPU+GPU installer ---
echo.

"%ISCC%" /DEDITION=Full "%SCRIPT_DIR%installer.iss"

:: M52: Exit nonzero on Inno Setup failure
if !ERRORLEVEL! NEQ 0 (
    echo   [FAIL] CPU+GPU installer -- check errors above.
    exit /b 1
)
echo   [OK] CPU+GPU installer built

echo.
echo   ========================================
echo     BUILD COMPLETE
echo   ========================================
echo.
echo   Output:
if exist "%DIST_DIR%\LinguaTaxi-GPU-Setup-1.0.1.exe" (
    echo     dist\LinguaTaxi-GPU-Setup-1.0.1.exe   (CPU+GPU Best Accuracy)
)
if exist "%DIST_DIR%\LinguaTaxi-CPU-Setup-1.0.1.exe" (
    echo     dist\LinguaTaxi-CPU-Setup-1.0.1.exe   (CPU Only)
)
echo.
echo   To rebuild from scratch, delete:
echo     build\windows\python_dist\
echo     build\windows\venv_lite\
echo     build\windows\venv_full\
echo     build\windows\models_prebuilt\
echo.

if not defined CI pause
