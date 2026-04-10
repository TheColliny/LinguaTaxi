# Fix Model Downloads — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix three model download bugs (OPUS-MT converter crash, Whisper downloading to wrong location, empty dirs on failure) and parallelize installer downloads so CUDA and models download concurrently.

**Architecture:** Fix the OPUS-MT converter to fall back to TransformersConverter when OpusMTConverter fails on missing `decoder.yml`. Fix download_models.py to download Whisper to the local `models/` dir using `huggingface_hub.snapshot_download()` instead of HF cache. Restructure installer to launch CUDA downloads as background processes in ssPostInstall while model downloads run in parallel via `[Run]`, and add `runhidden` to all entries.

**Tech Stack:** Python 3.11, ctranslate2, huggingface_hub, Inno Setup (Pascal)

---

## File Structure

| File | Action | Responsibility |
|------|--------|---------------|
| `offline_translate.py` | Modify | Fix OPUS-MT converter fallback + empty dir cleanup |
| `download_models.py` | Modify | Download Whisper to local models dir instead of HF cache |
| `build/windows/installer.iss` | Modify | Parallelize CUDA + model downloads, add runhidden |

---

### Task 1: Fix OPUS-MT Converter — Fall Back to TransformersConverter

**Files:**
- Modify: `offline_translate.py:280-286` (OPUS-MT conversion in `_worker`)

The root cause: `OpusMTConverter` in ctranslate2 4.7.1 requires a `decoder.yml` file that Helsinki-NLP OPUS-MT models don't include. The M2M-100 download already handles this pattern (lines 365-371) by falling back to `TransformersConverter`. Apply the same fallback to OPUS-MT.

- [ ] **Step 1: Replace the conversion block**

In `offline_translate.py`, find the OPUS-MT conversion (lines 284-286):

```python
            from ctranslate2.converters import OpusMTConverter
            converter = OpusMTConverter(hf_local)
            converter.convert(str(output_path), quantization="int8", force=True)
```

Replace with:

```python
            try:
                from ctranslate2.converters import OpusMTConverter
                converter = OpusMTConverter(hf_local)
            except (ImportError, Exception):
                from ctranslate2.converters import TransformersConverter
                converter = TransformersConverter(hf_local)
            converter.convert(str(output_path), quantization="int8", force=True)
```

- [ ] **Step 2: Add empty directory cleanup on error**

In the same `_worker` function, find the exception handler (lines 306-311):

```python
        except Exception as e:
            error_msg = str(e)[:200]
            _set_progress(key, "error", 0, error_msg)
            log.error(f"OPUS-MT download failed for {lang_code}: {e}")
            if on_complete:
                on_complete(key, False, error_msg)
```

Add cleanup before the error reporting:

```python
        except Exception as e:
            # Clean up empty/partial output directory
            if output_path.exists() and not (output_path / "model.bin").exists():
                try:
                    shutil.rmtree(output_path)
                except Exception:
                    pass
            error_msg = str(e)[:200]
            _set_progress(key, "error", 0, error_msg)
            log.error(f"OPUS-MT download failed for {lang_code}: {e}")
            if on_complete:
                on_complete(key, False, error_msg)
```

- [ ] **Step 3: Apply the same cleanup to M2M-100 error handler**

In the M2M-100 `_worker` (line 391-396), find:

```python
        except Exception as e:
            error_msg = str(e)[:200]
            _set_progress(key, "error", 0, error_msg)
            log.error(f"M2M-100 download failed: {e}")
            if on_complete:
                on_complete(key, False, error_msg)
```

Add the same cleanup:

```python
        except Exception as e:
            # Clean up empty/partial output directory
            if output_path.exists() and not (output_path / "model.bin").exists():
                try:
                    shutil.rmtree(output_path)
                except Exception:
                    pass
            error_msg = str(e)[:200]
            _set_progress(key, "error", 0, error_msg)
            log.error(f"M2M-100 download failed: {e}")
            if on_complete:
                on_complete(key, False, error_msg)
```

- [ ] **Step 4: Test the OPUS-MT download**

Run from the installed venv to verify the fix:

```bash
"C:/Program Files/LinguaTaxi - Live Caption and Translation/venv/Scripts/python.exe" offline_translate.py --download-opus ES --models-dir "C:/Program Files/LinguaTaxi - Live Caption and Translation/models"
```

Expected: Should download and convert successfully, producing `models/translate/opus-mt-en-es/model.bin`.

- [ ] **Step 5: Clean up existing empty OPUS-MT directories**

The old empty directories from failed downloads need to be removed so the next download attempt works:

```bash
rmdir "C:/Program Files/LinguaTaxi - Live Caption and Translation/models/translate/opus-mt-en-es" 2>/dev/null
rmdir "C:/Program Files/LinguaTaxi - Live Caption and Translation/models/translate/opus-mt-en-fr" 2>/dev/null
rmdir "C:/Program Files/LinguaTaxi - Live Caption and Translation/models/translate/opus-mt-en-it" 2>/dev/null
```

- [ ] **Step 6: Commit**

```bash
git add offline_translate.py
git commit -m "[fix] fall back to TransformersConverter for OPUS-MT, clean up empty dirs on failure"
```

---

### Task 2: Fix Whisper Download — Download to Local Models Dir

**Files:**
- Modify: `download_models.py:16-37` (download_whisper_model function)

Currently, `download_whisper_model()` calls `WhisperModel(model_name, ...)` which downloads to HuggingFace's cache (`~/.cache/huggingface/hub/`). The model should instead download to `models/faster-whisper-large-v3-turbo/` so `server.py` finds it locally (server.py checks `models/faster-whisper-{name}/model.bin` first).

- [ ] **Step 1: Replace download_whisper_model**

Replace the entire `download_whisper_model` function (lines 16-37) with:

```python
def download_whisper_model():
    """Pre-download faster-whisper large-v3-turbo model to local models dir."""
    try:
        import faster_whisper  # noqa: F401 — verify package is installed
    except ImportError:
        return False

    model_name = "large-v3-turbo"
    local_dir = MODELS_DIR / f"faster-whisper-{model_name}"

    # Already downloaded locally?
    if (local_dir / "model.bin").exists():
        print(f"\n  [OK] Whisper model already present: {local_dir.name}")
        return True

    print(f"\n  Downloading Whisper model: {model_name}")
    print(f"  This is ~1.5 GB and may take several minutes...\n")

    try:
        from huggingface_hub import snapshot_download

        snapshot_download(
            "Systran/faster-whisper-large-v3-turbo",
            local_dir=str(local_dir),
            allow_patterns=["*.bin", "*.json", "*.txt"],
        )

        if (local_dir / "model.bin").exists():
            print(f"\n  [OK] Whisper model '{model_name}' ready!")
            return True
        else:
            print(f"\n  [WARNING] Download completed but model.bin not found.")
            return False

    except Exception as e:
        print(f"\n  [WARNING] Whisper model download failed: {e}")
        print(f"  The model will download automatically on first server start.")
        return False
```

Key changes:
- Uses `huggingface_hub.snapshot_download()` with `local_dir=models/faster-whisper-large-v3-turbo/` instead of `WhisperModel()` which goes to HF cache
- Uses `allow_patterns` to only download essential files (model.bin, config.json, etc.)
- Checks for existing local model first
- This matches what the CI build step does (`snapshot_download` with `local_dir`)

- [ ] **Step 2: Test the Whisper download**

Run to verify (this will download ~1.5 GB, or skip if already cached):

```bash
"C:/Program Files/LinguaTaxi - Live Caption and Translation/venv/Scripts/python.exe" download_models.py
```

Expected: Downloads to `models/faster-whisper-large-v3-turbo/` with `model.bin` present.

- [ ] **Step 3: Copy fixed scripts to installed location**

```bash
powershell -Command "Start-Process cmd -Verb RunAs -Wait -ArgumentList '/c copy /Y \"C:\\Users\\User\\Documents\\LinguaTaxi\\download_models.py\" \"C:\\Program Files\\LinguaTaxi - Live Caption and Translation\\download_models.py\" && copy /Y \"C:\\Users\\User\\Documents\\LinguaTaxi\\offline_translate.py\" \"C:\\Program Files\\LinguaTaxi - Live Caption and Translation\\offline_translate.py\"'"
```

- [ ] **Step 4: Commit**

```bash
git add download_models.py
git commit -m "[fix] download Whisper model to local models dir instead of HF cache"
```

---

### Task 3: Installer — Parallelize Downloads and Add runhidden

**Files:**
- Modify: `build/windows/installer.iss:135-161` ([Run] section)
- Modify: `build/windows/installer.iss:172-230` ([Code] section, CurStepChanged)

The current installer runs CUDA downloads (1.2 GB) sequentially in `[Run]`, followed by model downloads. All of these block the installer progress bar. We'll move CUDA downloads to background processes launched in `CurStepChanged(ssPostInstall)` so they run concurrently with the model downloads in `[Run]`.

- [ ] **Step 1: Remove CUDA downloads from [Run] section**

In `installer.iss`, find and remove the three CUDA pip install lines (138-140):

```ini
Filename: "{app}\venv\Scripts\pip.exe"; Parameters: "install --no-deps ""https://github.com/TheColliny/LinguaTaxi-CUDA/releases/download/v12.9/nvidia_cuda_runtime_cu12-12.9.79-py3-none-win_amd64.whl"""; WorkingDir: "{app}"; StatusMsg: "Downloading NVIDIA CUDA Runtime (3.6 MB)..."; Flags: runhidden
Filename: "{app}\venv\Scripts\pip.exe"; Parameters: "install --no-deps ""https://github.com/TheColliny/LinguaTaxi-CUDA/releases/download/v12.9/nvidia_cublas_cu12-12.9.1.4-py3-none-win_amd64.whl"""; WorkingDir: "{app}"; StatusMsg: "Downloading NVIDIA cuBLAS (553 MB)..."; Flags: runhidden
Filename: "{app}\venv\Scripts\pip.exe"; Parameters: "install --no-deps ""https://github.com/TheColliny/LinguaTaxi-CUDA/releases/download/v12.9/nvidia_cudnn_cu12-9.19.0.56-py3-none-win_amd64.whl"""; WorkingDir: "{app}"; StatusMsg: "Downloading NVIDIA cuDNN (644 MB)..."; Flags: runhidden
```

- [ ] **Step 2: Add `Flags: runhidden` to all remaining model download entries**

Add `; Flags: runhidden` to EVERY model download line in the `[Run]` section. This includes:
- Line 143: `download_models.py` (speech models) — add `; Flags: runhidden`
- Lines 146-151: `tuned_models.py` downloads — add `; Flags: runhidden`
- Lines 153-158: `offline_translate.py` downloads — add `; Flags: runhidden`

For example, line 143 becomes:
```ini
Filename: "{app}\venv\Scripts\python.exe"; Parameters: """{app}\download_models.py"""; WorkingDir: "{app}"; Tasks: updatemodels; StatusMsg: "Checking for updated voice recognition models..."; Flags: runhidden
```

And line 146 becomes:
```ini
Filename: "{app}\venv\Scripts\python.exe"; Parameters: """{app}\tuned_models.py"" --download ES --models-dir ""{app}\models"""; WorkingDir: "{app}"; Tasks: tuned\es; StatusMsg: "Downloading & converting Spanish tuned model (~1.6 GB)..."; Flags: runhidden
```

Apply the same pattern to ALL model download lines (146-158).

- [ ] **Step 3: Launch CUDA downloads as background processes in CurStepChanged**

In the `[Code]` section, expand `CurStepChanged` to launch CUDA downloads immediately after fixing pyvenv.cfg and writing edition.txt. Use Inno Setup's `Exec` function with `ewNoWait` to run them in the background:

Replace the current `CurStepChanged` procedure with:

```pascal
procedure CurStepChanged(CurStep: TSetupStep);
var
  CfgPath: String;
  PythonHome: String;
  EditionPath: String;
  PipPath: String;
  ResultCode: Integer;
begin
  if CurStep = ssPostInstall then
  begin
    // Fix venv paths (must happen first — pip needs working venv)
    CfgPath := ExpandConstant('{app}\venv\pyvenv.cfg');
    PythonHome := ExpandConstant('{app}\python');
    SaveStringToFile(CfgPath,
      'home = ' + PythonHome + #13#10 +
      'include-system-site-packages = false' + #13#10 +
      'version = 3.11.9' + #13#10,
      False);

    // Write edition.txt
    EditionPath := ExpandConstant('{app}\edition.txt');
  #if EDITION == "Full"
    SaveStringToFile(EditionPath, 'GPU', False);
  #else
    SaveStringToFile(EditionPath, 'CPU', False);
  #endif

  #if EDITION == "Full"
    // Launch CUDA downloads in background (run concurrently with [Run] model downloads)
    PipPath := ExpandConstant('{app}\venv\Scripts\pip.exe');
    Exec(PipPath, 'install --no-deps "https://github.com/TheColliny/LinguaTaxi-CUDA/releases/download/v12.9/nvidia_cuda_runtime_cu12-12.9.79-py3-none-win_amd64.whl"',
         ExpandConstant('{app}'), SW_HIDE, ewNoWait, ResultCode);
    Exec(PipPath, 'install --no-deps "https://github.com/TheColliny/LinguaTaxi-CUDA/releases/download/v12.9/nvidia_cublas_cu12-12.9.1.4-py3-none-win_amd64.whl"',
         ExpandConstant('{app}'), SW_HIDE, ewNoWait, ResultCode);
    Exec(PipPath, 'install --no-deps "https://github.com/TheColliny/LinguaTaxi-CUDA/releases/download/v12.9/nvidia_cudnn_cu12-9.19.0.56-py3-none-win_amd64.whl"',
         ExpandConstant('{app}'), SW_HIDE, ewNoWait, ResultCode);
  #endif
  end;
end;
```

Key points:
- `SW_HIDE` hides the console window
- `ewNoWait` returns immediately without waiting for the process to complete
- The CUDA downloads now run in background while `[Run]` section executes model downloads
- Since CUDA downloads use pip and model downloads use python scripts, there's no file contention
- The 3 pip processes will download concurrently with each other AND with any model downloads in `[Run]`

- [ ] **Step 4: Verify installer compiles**

If ISCC is available locally:
```bash
cd build/windows && ISCC /DEDITION=Full installer.iss
```

Otherwise, review the Pascal syntax carefully.

- [ ] **Step 5: Commit**

```bash
git add build/windows/installer.iss
git commit -m "[fix] parallelize CUDA downloads in background, add runhidden to all model downloads"
```

---

### Task 4: Verification

- [ ] **Step 1: Test OPUS-MT download from installed venv**

After copying the fixed `offline_translate.py` to the installed location:
```bash
"C:/Program Files/LinguaTaxi - Live Caption and Translation/venv/Scripts/python.exe" "C:/Program Files/LinguaTaxi - Live Caption and Translation/offline_translate.py" --download-opus ES --models-dir "C:/Program Files/LinguaTaxi - Live Caption and Translation/models"
```

Expected: `DONE:opus-ES:ok:OPUS-MT Spanish ready` and `models/translate/opus-mt-en-es/model.bin` exists.

- [ ] **Step 2: Verify Whisper model location**

After running `download_models.py`:
```bash
ls "C:/Program Files/LinguaTaxi - Live Caption and Translation/models/faster-whisper-large-v3-turbo/model.bin"
```

Expected: File exists.

- [ ] **Step 3: Verify git log**

```bash
git log --oneline -5
```

Expected: 3 new commits for tasks 1-3.
