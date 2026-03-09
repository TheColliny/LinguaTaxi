@echo off
setlocal EnableDelayedExpansion
:: ════════════════════════════════════════════════════════
:: LinguaTaxi — Windows Installer Build Script
::
:: Builds TWO installers:
::   Full  (~600 MB) — GPU (faster-whisper + CUDA) + CPU (Vosk)
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
::   dist\LinguaTaxi-Setup-1.0.0.exe       (Full, ~600 MB)
::   dist\LinguaTaxi-Lite-Setup-1.0.0.exe  (Lite, ~50 MB)
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

:: ── Base pip packages (no speech backends) ──
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

echo   Installing Python to python_dist\ ...
echo   (This takes 1-2 minutes)
"%INSTALLER%" /quiet InstallAllUsers=0 PrependPath=0 Include_launcher=0 Include_test=0 Include_tcltk=1 TargetDir="%PYTHON_DIR%" >> "%SCRIPT_DIR%build_log.txt" 2>&1

:: Wait for installer to complete (ping used as portable sleep — works in cmd and bash)
:wait_python
ping -n 3 127.0.0.1 >nul 2>&1
if not exist "%PYTHON_DIR%\python.exe" goto :wait_python

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

echo   Installing base packages...
"%VENV_LITE%\Scripts\pip.exe" install %BASE_PACKAGES% >> "%SCRIPT_DIR%build_log.txt" 2>&1

echo   Installing Vosk (CPU speech backend)...
"%VENV_LITE%\Scripts\pip.exe" install vosk >> "%SCRIPT_DIR%build_log.txt" 2>&1

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

echo   Installing base packages...
"%VENV_FULL%\Scripts\pip.exe" install %BASE_PACKAGES% >> "%SCRIPT_DIR%build_log.txt" 2>&1

echo   Installing faster-whisper (GPU speech backend)...
"%VENV_FULL%\Scripts\pip.exe" install faster-whisper >> "%SCRIPT_DIR%build_log.txt" 2>&1

echo   Installing Vosk (CPU fallback)...
"%VENV_FULL%\Scripts\pip.exe" install vosk >> "%SCRIPT_DIR%build_log.txt" 2>&1

echo   Installing NVIDIA CUDA libraries (~1.2 GB download)...
echo   (This may take several minutes)
"%VENV_FULL%\Scripts\pip.exe" install nvidia-cublas-cu12 nvidia-cudnn-cu12 nvidia-cuda-runtime-cu12 >> "%SCRIPT_DIR%build_log.txt" 2>&1
if !ERRORLEVEL! NEQ 0 (
    echo   WARNING: NVIDIA CUDA packages failed to install.
    echo   The Full installer will work but without bundled GPU libraries.
    echo   Users will need CUDA Toolkit installed separately.
)

echo   [OK] Full venv ready (faster-whisper + Vosk + CUDA)

:full_ready

:: ── Step 5: Check icon ──
if exist "%PROJECT_DIR%\assets\linguataxi.ico" (
    echo   [OK] Icon found
) else (
    echo   [--] No icon — run: python assets\generate_icons.py
)

:: ── Step 6: Compile both installers ──
mkdir "%DIST_DIR%" 2>nul

echo.
echo   ── Compiling Lite installer ──
echo.

"%ISCC%" /DEDITION=Lite "%SCRIPT_DIR%installer.iss"

if !ERRORLEVEL! EQU 0 (
    echo   [OK] Lite installer built
) else (
    echo   [FAIL] Lite installer — check errors above.
)

echo.
echo   ── Compiling Full installer ──
echo.

"%ISCC%" /DEDITION=Full "%SCRIPT_DIR%installer.iss"

if !ERRORLEVEL! EQU 0 (
    echo   [OK] Full installer built
) else (
    echo   [FAIL] Full installer — check errors above.
)

echo.
echo   ========================================
echo     BUILD COMPLETE
echo   ========================================
echo.
echo   Output:
if exist "%DIST_DIR%\LinguaTaxi-Setup-1.0.0.exe" (
    echo     dist\LinguaTaxi-Setup-1.0.0.exe       (Full, GPU + CPU)
)
if exist "%DIST_DIR%\LinguaTaxi-Lite-Setup-1.0.0.exe" (
    echo     dist\LinguaTaxi-Lite-Setup-1.0.0.exe  (Lite, CPU only)
)
echo.
echo   To rebuild from scratch, delete:
echo     build\windows\python_dist\
echo     build\windows\venv_lite\
echo     build\windows\venv_full\
echo.

if not defined CI pause
