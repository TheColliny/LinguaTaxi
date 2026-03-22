# Bi-Directional Translation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add simultaneous back-and-forth translation between two languages with automatic language detection, smart translation routing, and a dedicated bi-directional display.

**Architecture:** Detection-first with lazy model loading. Silero lang_detector_95 (ONNX, 4.7MB) detects language on the first ~1s of each speech segment in the buffer loop. The detected language routes audio to the correct transcription model and triggers reverse translation. Two auto-managed translation slots handle the bi-directional pair; up to 3 additional observer slots remain available.

**Tech Stack:** Python 3.11, FastAPI, faster-whisper, Vosk, Silero lang_detector_95 (ONNX), onnxruntime, WebSocket, vanilla JS/HTML

**Spec:** `docs/superpowers/specs/2026-03-21-bidirectional-translation-design.md`

---

## File Structure

| File | Action | Responsibility |
|------|--------|---------------|
| `lang_detect.py` | Create (~120 lines) | Silero ONNX wrapper: load model, detect language from audio, download helper |
| `bidirectional.html` | Create (~350 lines) | Split-screen and single-language display modes with color-coded captions |
| `server.py` | Modify (~1711→~1900 lines) | Thread `detected_lang` through pipeline, detection in buffer loop, smart translation routing, bi-directional config endpoints, Vosk dual-model, WebSocket message fields |
| `operator.html` | Modify (~1503→~1650 lines) | Bi-directional toggle, input language selectors, speaker language assignment, locked slots, detection indicator |
| `launcher.pyw` | Modify (~2499→~2600 lines) | Vosk model download dialog, bi-directional display browser button |
| `installer.iss` | Modify | Vosk language model optional tasks and CustomMessages |
| `download_models.py` | Modify (~168→~220 lines) | Vosk multi-language model download support |
| `requirements.txt` | Modify | Add `onnxruntime>=1.16.0` |

---

## Task 1: Create `lang_detect.py` — Silero Language Detection Module

**Files:**
- Create: `lang_detect.py`
- Modify: `requirements.txt`

- [ ] **Step 1: Add onnxruntime to requirements.txt**

Add `onnxruntime>=1.16.0` to `requirements.txt` after the existing packages.

- [ ] **Step 2: Create `lang_detect.py` with model download and detection**

```python
"""
Silero Language Classifier 95 — ONNX wrapper for spoken language detection.
MIT licensed, 4.7MB model, <1ms inference, 95 languages.
"""

import logging
import json
import struct
from pathlib import Path

import numpy as np

log = logging.getLogger("lang_detect")

# Default model storage alongside other models
_MODELS_DIR = Path(__file__).parent / "models"
_MODEL_SUBDIR = "silero-lang-detect"

# Silero lang_detector_95 ONNX model hosted on GitHub (snakers4/silero-vad releases)
_MODEL_URL = "https://models.silero.ai/models/langs/lang_detector_95.onnx"
_LANG_DICT_URL = "https://raw.githubusercontent.com/snakers4/silero-vad/master/files/lang_dict_95.json"

_session = None  # ONNX InferenceSession (lazy loaded)
_lang_dict = None  # {index: lang_code} mapping


def set_models_dir(path):
    """Override the default models directory."""
    global _MODELS_DIR
    _MODELS_DIR = Path(path)


def _model_dir():
    return _MODELS_DIR / _MODEL_SUBDIR


def download_model(models_dir=None):
    """Download the Silero lang_detector_95 ONNX model if not present."""
    import requests

    d = Path(models_dir) / _MODEL_SUBDIR if models_dir else _model_dir()
    d.mkdir(parents=True, exist_ok=True)

    onnx_path = d / "lang_detector_95.onnx"
    dict_path = d / "lang_dict_95.json"

    if not onnx_path.exists():
        log.info(f"Downloading Silero language detection model to {d}")
        r = requests.get(_MODEL_URL, timeout=60)
        r.raise_for_status()
        onnx_path.write_bytes(r.content)
        log.info(f"Downloaded {onnx_path.name} ({len(r.content) / 1e6:.1f} MB)")

    if not dict_path.exists():
        r = requests.get(_LANG_DICT_URL, timeout=30)
        r.raise_for_status()
        dict_path.write_text(r.text, encoding="utf-8")
        log.info(f"Downloaded {dict_path.name}")

    return onnx_path, dict_path


def _load():
    """Lazy-load the ONNX model and language dictionary."""
    global _session, _lang_dict

    if _session is not None:
        return

    onnx_path = _model_dir() / "lang_detector_95.onnx"
    dict_path = _model_dir() / "lang_dict_95.json"

    if not onnx_path.exists():
        download_model()

    import onnxruntime as ort
    _session = ort.InferenceSession(str(onnx_path), providers=["CPUExecutionProvider"])

    with open(dict_path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    # Silero dict is {str(index): lang_code}
    _lang_dict = {int(k): v for k, v in raw.items()}

    log.info(f"Silero language detector loaded ({len(_lang_dict)} languages)")


def detect_language(audio, candidates=None):
    """
    Detect spoken language from audio.

    Args:
        audio: numpy float32 array, 16kHz mono
        candidates: optional list of language codes (e.g., ["en", "ar"])
                    to restrict detection to — boosts effective accuracy

    Returns:
        (lang_code, confidence) — e.g., ("en", 0.94)
    """
    _load()

    # Ensure float32, flatten
    audio = np.asarray(audio, dtype=np.float32).flatten()

    # Silero expects batched input: (batch, samples)
    inp = audio.reshape(1, -1)
    output = _session.run(None, {"input": inp})
    probs = output[0][0]  # shape: (num_languages,)

    if candidates:
        # Build index→code for candidates only
        candidate_set = set(c.lower() for c in candidates)
        candidate_indices = [
            (i, _lang_dict[i]) for i in range(len(probs))
            if i in _lang_dict and _lang_dict[i].lower() in candidate_set
        ]
        if candidate_indices:
            best_i, best_lang = max(candidate_indices, key=lambda x: probs[x[0]])
            # Normalize confidence among candidates only
            total = sum(probs[i] for i, _ in candidate_indices)
            conf = probs[best_i] / total if total > 0 else probs[best_i]
            return best_lang, float(conf)

    # Unrestricted: pick global best
    best_i = int(np.argmax(probs))
    return _lang_dict.get(best_i, "unknown"), float(probs[best_i])


def is_available():
    """Check if the ONNX model file exists (without loading it)."""
    return (_model_dir() / "lang_detector_95.onnx").exists()
```

- [ ] **Step 3: Verify syntax**

Run: `python -c "import py_compile; py_compile.compile('lang_detect.py', doraise=True); print('OK')"`
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add lang_detect.py requirements.txt
git commit -m "[feat] add Silero language detection module (lang_detect.py)"
```

---

## Task 2: Thread `detected_lang` Through Server Pipeline

**Files:**
- Modify: `server.py`

This task changes function signatures to accept and pass `detected_lang` without changing behavior. All callers pass `None` for now — the detection logic comes in Task 3.

- [ ] **Step 1: Change `_transcription_worker` to unpack 3-element tuple**

At `server.py:404`, change the worker to unpack `(source, buf, detected_lang)`:

```python
# Before:
source, buf = _transcription_queue.get(timeout=0.5)

# After:
source, buf, detected_lang = _transcription_queue.get(timeout=0.5)
```

- [ ] **Step 2: Change `_buffer_audio_loop` queue submissions to 3-element tuples**

Find all `_transcription_queue.put` and `_transcription_queue.put_nowait` calls in `_buffer_audio_loop` (around lines 420-480). Change each from `(source, buf)` to `(source, buf, None)`.

- [ ] **Step 3: Add `lang` parameter to `WhisperBackend._transcribe` and `MLXWhisperBackend._transcribe`**

`WhisperBackend._transcribe` at line 501:
```python
# Before:
def _transcribe(self, buf):
    whisper_lang = DEEPL_TO_WHISPER.get(config.get("input_lang", "EN"), "en")

# After:
def _transcribe(self, buf, lang=None):
    if lang:
        whisper_lang = lang
    else:
        whisper_lang = DEEPL_TO_WHISPER.get(config.get("input_lang", "EN"), "en")
```

Same pattern for `MLXWhisperBackend._transcribe` at line 665.

- [ ] **Step 4: Pass `detected_lang` from `_transcription_worker` to `transcribe_fn`**

In `_transcription_worker` (line 404), the worker calls `transcribe_fn(buf)`. Change to:
```python
transcribe_fn(buf, lang=detected_lang)
```

For Whisper/MLX, `transcribe_fn` is `backend._transcribe` — the `lang` kwarg is handled by Step 3.

For Vosk, `transcribe_fn` is not used (Vosk has its own streaming loop) — no change needed here.

- [ ] **Step 5: Add `detected_lang` parameter to `_broadcast_final`**

At line 746:
```python
# Before:
def _broadcast_final(text, loop, source):

# After:
def _broadcast_final(text, loop, source, detected_lang=None):
```

Include `detected_lang` in the WebSocket message dict sent to clients:
```python
msg = {..., "detected_lang": detected_lang}
```

- [ ] **Step 6: Pass `detected_lang` from `_transcription_worker` to `_broadcast_final`**

In the worker, after `transcribe_fn`, pass `detected_lang` to `_broadcast_final`.

- [ ] **Step 7: Add `source_lang` parameter to `_translate_all` and `_do_translate`**

`_translate_all` at line 770 — actual signature:
```python
# Before:
def _translate_all(text, msg_type, loop, max_slots=99, line_id=None, speaker_override=None):

# After:
def _translate_all(text, msg_type, loop, max_slots=99, line_id=None, speaker_override=None, source_lang=None):
```

Pass `source_lang` through to `_do_translate` in the Thread args:
```python
threading.Thread(target=_do_translate,
    args=(text, t["lang"], i, msg_type, loop, line_id, speaker_override, source_lang), daemon=True).start()
```

`_do_translate` at line 781 — actual signature:
```python
# Before:
def _do_translate(text, lang, slot, msg_type, loop, line_id=None, speaker_override=None):

# After:
def _do_translate(text, lang, slot, msg_type, loop, line_id=None, speaker_override=None, source_lang=None):
```

In `_do_translate`, pass `source_lang` to `translate_text()`:
```python
# Before:
translated = translate_text(text, lang, mode=mode)

# After:
translated = translate_text(text, lang, source_lang=source_lang, mode=mode)
```

- [ ] **Step 8: Pass `source_lang` from `_broadcast_final` to `_translate_all`**

In `_broadcast_final`, change the `_translate_all` call to include `source_lang=detected_lang`. Note the actual call signature uses positional args — add `source_lang` as kwarg.

- [ ] **Step 9: Update `_check_speaker_change` direct calls**

At lines 382-384 and 391-393, `_check_speaker_change` calls `transcribe_fn(buf)` and `_broadcast_final(text, loop, source)` directly (not through the queue). Update these:
```python
# Line 382: add lang kwarg
text = transcribe_fn(old_buf, lang=source.current_lang)

# Line 384: add detected_lang kwarg
_broadcast_final(text, loop, source, detected_lang=source.current_lang)

# Same for lines 391-393
text = transcribe_fn(buf, lang=source.current_lang)
_broadcast_final(text, loop, source, detected_lang=source.current_lang)
```

- [ ] **Step 10: Update interim transcription and translation calls in buffer loop**

At line 456, the interim `transcribe_fn(buf)` call needs the language:
```python
text = transcribe_fn(buf, lang=source.current_lang)
```

At line 460, the interim `_translate_all` call needs `source_lang`:
```python
_translate_all(text, "interim_translation", loop, max_slots=2, source_lang=source.current_lang)
```

- [ ] **Step 11: Verify syntax and no behavioral change**

Run: `python -c "import py_compile; py_compile.compile('server.py', doraise=True); print('OK')"`
Expected: `OK`

Manually start the server and verify standard captioning still works — since all `detected_lang` and `source.current_lang` values are `None`, behavior should be identical.

- [ ] **Step 12: Commit**

```bash
git add server.py
git commit -m "[refactor] thread detected_lang parameter through transcription and translation pipeline"
```

---

## Task 3: Add Bi-directional Config and Language Detection in Buffer Loop

**Files:**
- Modify: `server.py`

- [ ] **Step 1: Add bi-directional fields to DEFAULT_CONFIG**

At `server.py:142`, add to `DEFAULT_CONFIG`:
```python
"bidirectional_enabled": False,
"bidirectional_langs": [],        # e.g., ["EN", "AR"]
"bidirectional_tuned_swap": False,
```

- [ ] **Step 2: Add `DEEPL_TARGET_DEFAULTS` dict**

Near line 98 (after `DEEPL_TO_WHISPER`), add default target code variants:
```python
DEEPL_TARGET_DEFAULTS = {
    "EN": "EN-US", "PT": "PT-BR", "ZH": "ZH-HANS",
}
```

- [ ] **Step 3: Add `current_lang` field to `AudioSource`**

At line 282 in `AudioSource.__init__`, add:
```python
self.current_lang = None  # detected language in bi-directional mode
```

- [ ] **Step 4: Add language detection call in `_buffer_audio_loop`**

In `_buffer_audio_loop` (line 420), after the silence detection determines a segment is ready for transcription (just before `_transcription_queue.put`), add detection:

```python
detected_lang = None
if config.get("bidirectional_enabled") and len(config.get("bidirectional_langs", [])) == 2:
    # Check if speaker has an assigned language override
    speaker_lang = _get_speaker_lang(source)
    if speaker_lang:
        detected_lang = speaker_lang
    else:
        try:
            import lang_detect
            bidir_langs = config["bidirectional_langs"]
            # Convert DeepL codes to Whisper/Silero codes for detection
            candidates = [DEEPL_TO_WHISPER.get(l, l.lower()) for l in bidir_langs]
            audio_for_detect = buf[:SAMPLE_RATE].flatten()  # first ~1s of audio
            lang, conf = lang_detect.detect_language(audio_for_detect, candidates=candidates)
            if conf >= 0.6:
                # Map back to DeepL code
                whisper_to_deepl = {v: k for k, v in DEEPL_TO_WHISPER.items()}
                detected_lang = whisper_to_deepl.get(lang, lang.upper())
            else:
                detected_lang = source.current_lang  # fallback to last known
        except ImportError:
            # onnxruntime not available — try Whisper's built-in detection as fallback
            if hasattr(stt_backend, '_model') and hasattr(stt_backend._model, 'detect_language'):
                try:
                    audio_flat = buf[:SAMPLE_RATE].flatten().astype(np.float32)
                    _, lang_probs = stt_backend._model.detect_language(audio_flat)
                    if lang_probs:
                        best = max(lang_probs, key=lambda x: x[1])
                        whisper_to_deepl = {v: k for k, v in DEEPL_TO_WHISPER.items()}
                        detected_lang = whisper_to_deepl.get(best[0], best[0].upper())
                except Exception:
                    detected_lang = source.current_lang
            else:
                log.warning("Language detection unavailable: install onnxruntime")
                detected_lang = source.current_lang
        except Exception as e:
            log.warning(f"Language detection failed: {e}")
            detected_lang = source.current_lang
    if detected_lang:
        source.current_lang = detected_lang
```

Update the queue put to use the 3-element tuple with the real `detected_lang`.

- [ ] **Step 5: Add `_get_speaker_lang` helper**

Add a helper function that checks if the current speaker on a source has an assigned language override:

```python
def _get_speaker_lang(source):
    """Check if the current speaker has an assigned language override."""
    if not source.speaker:
        return None
    speaker_langs = config.get("speaker_langs", {})
    return speaker_langs.get(source.speaker)
```

- [ ] **Step 6: Add bi-directional config API endpoints**

Add to the operator `/api/config` POST handler (line 1063) support for:
- `bidirectional_enabled` (bool)
- `bidirectional_langs` (JSON array of 2 language codes)
- `bidirectional_tuned_swap` (bool)
- `speaker_langs` (JSON dict of speaker_name → lang_code)

When `bidirectional_enabled` is toggled on, auto-create 2 translation slots at positions 0-1. When toggled off, remove them.

- [ ] **Step 7: Add bi-directional fields to operator `/api/config` GET**

Ensure the operator config endpoint (line 988) returns the new bi-directional fields so the operator panel can read them on connect.

- [ ] **Step 8: Initialize Silero model at server startup if onnxruntime is available**

In `main()` after model loading, add:
```python
try:
    import lang_detect
    lang_detect.set_models_dir(MODELS_DIR)
    if not lang_detect.is_available():
        log.info("Silero language detection model not found — will download on first use")
except ImportError:
    log.info("onnxruntime not installed — Silero language detection unavailable")
```

- [ ] **Step 9: Verify syntax**

Run: `python -c "import py_compile; py_compile.compile('server.py', doraise=True); print('OK')"`

- [ ] **Step 10: Commit**

```bash
git add server.py
git commit -m "[feat] add bi-directional config, language detection in buffer loop"
```

---

## Task 4: Smart Translation Routing

**Files:**
- Modify: `server.py`

- [ ] **Step 1: Modify `_translate_all` to implement smart bi-directional routing**

In `_translate_all` (line 770), add smart routing logic. The actual signature is `_translate_all(text, msg_type, loop, max_slots=99, line_id=None, speaker_override=None, source_lang=None)`:

```python
def _translate_all(text, msg_type, loop, max_slots=99, line_id=None, speaker_override=None, source_lang=None):
    if translation_paused:
        return
    if captioning_paused and dictation_active:
        return
    translations = config.get("translations", [])
    bidir = config.get("bidirectional_enabled", False)
    bidir_langs = config.get("bidirectional_langs", [])

    for i, t in enumerate(translations):
        if i >= max_slots: break

        # Smart routing for auto-managed bi-directional slots (0 and 1)
        if bidir and i < 2 and len(bidir_langs) == 2 and source_lang:
            slot_target = bidir_langs[0] if i == 0 else bidir_langs[1]
            src_base = source_lang.split("-")[0]
            tgt_base = slot_target.split("-")[0]
            if src_base == tgt_base:
                continue  # skip — source already in this slot's target language

        threading.Thread(target=_do_translate,
            args=(text, t["lang"], i, msg_type, loop, line_id, speaker_override, source_lang),
            daemon=True).start()
```

- [ ] **Step 2: Add `is_translation` field to translation broadcast messages**

In `_do_translate` (line 781), when broadcasting `final_translation` messages, add `"is_translation": True` to the message dict. This enables the display to color-code translated vs original text.

- [ ] **Step 3: Add `is_translation: False` to original caption messages**

In `_broadcast_final`, add `"is_translation": False` to the final caption WebSocket message.

- [ ] **Step 4: Verify syntax**

Run: `python -c "import py_compile; py_compile.compile('server.py', doraise=True); print('OK')"`

- [ ] **Step 5: Commit**

```bash
git add server.py
git commit -m "[feat] smart bi-directional translation routing with slot skipping"
```

---

## Task 5: Vosk Dual-Model Support

**Files:**
- Modify: `server.py`

- [ ] **Step 1: Add second Vosk model loading when bi-directional mode is enabled**

Add a function to load a second Vosk model on demand:

```python
def _load_vosk_bidir_model(lang_code):
    """Load a second Vosk model for bi-directional mode."""
    # Map DeepL language codes to Vosk model directory names
    model_pattern = f"vosk-model-*-{lang_code.lower()}-*"
    candidates = list(MODELS_DIR.glob(model_pattern))
    if not candidates:
        log.error(f"No Vosk model found for language: {lang_code}")
        return None
    model_path = candidates[0]  # prefer first match
    import vosk
    vosk.SetLogLevel(-1)
    return vosk.Model(str(model_path))
```

- [ ] **Step 2: Modify `_vosk_source_loop` to support dual recognizers**

In `VoskBackend._vosk_source_loop` (line 583), when bi-directional mode is enabled:
- Create two `KaldiRecognizer` instances (one per language model)
- Track which recognizer is active based on `source.current_lang`
- On language change detection, force-finalize the current recognizer and switch to the other
- Reuse the existing force-finalize pattern from speaker change handling

- [ ] **Step 3: Add language detection in Vosk streaming loop**

Since Vosk doesn't use the shared transcription queue, add Silero detection directly in the Vosk source loop. Call `lang_detect.detect_language()` periodically on accumulated audio (~1s worth) when bi-directional mode is active.

- [ ] **Step 4: Handle bi-directional toggle mid-session for Vosk**

When bi-directional mode is toggled on via the operator:
- Load the second Vosk model (1-3 seconds, show status to operator)
- Create second `KaldiRecognizer` per source

When toggled off:
- Discard the second model and recognizers to free memory
- Continue with the primary language recognizer

- [ ] **Step 5: Add `/api/vosk-models` endpoint**

Add an endpoint that reports which Vosk language models are installed in `MODELS_DIR/`:

```python
@operator_app.get("/api/vosk-models")
async def get_vosk_models():
    models = []
    for d in MODELS_DIR.glob("vosk-model-*"):
        if d.is_dir():
            # Vosk model dirs have inconsistent naming:
            #   vosk-model-small-en-us-0.15, vosk-model-ar-mgb2-0.4, vosk-model-small-cn-0.22
            # Use a mapping of known model dir prefixes to language codes
            name = d.name.lower()
            VOSK_DIR_LANGS = {
                "en-us": "en", "en-in": "en", "de": "de", "fr": "fr", "es": "es",
                "ru": "ru", "it": "it", "ja": "ja", "cn": "zh", "zh": "zh",
                "ar": "ar", "pt": "pt", "tr": "tr", "ko": "ko", "nl": "nl",
                "uk": "uk", "pl": "pl", "hi": "hi", "fa": "fa", "ca": "ca",
            }
            lang = None
            for pattern, code in VOSK_DIR_LANGS.items():
                if f"-{pattern}" in name:
                    lang = code
                    break
            if lang:
                models.append({"path": d.name, "lang": lang})
    return {"models": models}
```

- [ ] **Step 6: Verify syntax**

Run: `python -c "import py_compile; py_compile.compile('server.py', doraise=True); print('OK')"`

- [ ] **Step 7: Commit**

```bash
git add server.py
git commit -m "[feat] Vosk dual-model support for bi-directional mode"
```

---

## Task 6: Whisper Tuned Model Swap Toggle

**Files:**
- Modify: `server.py`

- [ ] **Step 1: Add tuned model swap logic in `_transcription_worker`**

In the transcription worker, when `bidirectional_tuned_swap` is enabled and `detected_lang` differs from the currently loaded tuned model's language:

```python
if (config.get("bidirectional_tuned_swap") and
    detected_lang and
    isinstance(stt_backend, WhisperBackend)):
    current_model_lang = getattr(stt_backend, '_tuned_lang', None)
    if current_model_lang != detected_lang:
        # Check if a tuned model exists for this language
        from tuned_models import TUNED_MODELS, get_model_path
        from faster_whisper import WhisperModel
        if detected_lang in TUNED_MODELS:
            model_path = get_model_path(detected_lang, MODELS_DIR)
            if model_path and model_path.exists():
                log.info(f"Swapping to tuned model for {detected_lang}")
                stt_backend._model = WhisperModel(
                    str(model_path),
                    device=stt_backend._device,
                    compute_type=stt_backend._compute_type
                )
                stt_backend._tuned_lang = detected_lang
```

- [ ] **Step 2: Track current tuned language on WhisperBackend**

Add `self._tuned_lang = None` to `WhisperBackend.__init__` to track which language's tuned model is currently loaded.

- [ ] **Step 3: Verify syntax**

Run: `python -c "import py_compile; py_compile.compile('server.py', doraise=True); print('OK')"`

- [ ] **Step 4: Commit**

```bash
git add server.py
git commit -m "[feat] optional tuned model hot-swap in bi-directional mode"
```

---

## Task 7: Operator Panel Bi-directional UI

**Files:**
- Modify: `operator.html`

- [ ] **Step 1: Add bi-directional toggle HTML**

Above the existing translation slot controls (around line 156), add:
- A toggle switch: "Bi-directional Mode"
- Two language dropdowns (hidden by default): "Input Language A" and "Input Language B"
- A checkbox (hidden by default): "Use tuned model when available"
- A detection indicator area showing current detected language per source

- [ ] **Step 2: Add bi-directional JavaScript toggle handler**

When the toggle is switched on:
- Show the input language dropdowns and tuned model checkbox
- POST the bi-directional config to `/api/config`
- Lock translation slots 0-1 with auto-managed labels
- Update `buildTranslationSlots()` to start user-configurable slots from index 2

When toggled off:
- Hide the bi-directional controls
- POST to disable
- Unlock all slots, remove auto-managed ones

- [ ] **Step 3: Add speaker language assignment dropdowns**

In the speaker button section (line 193), add a small language dropdown next to each speaker button. The dropdown contains the two bi-directional input languages plus "Auto-detect" (default). Only visible when bi-directional mode is on.

When a language is assigned, send a WebSocket message to update `speaker_langs` config.

- [ ] **Step 4: Add detection indicator**

Add a small area near the audio source list that shows:
- Source name → detected language badge (e.g., "Mic 1: AR ●" with confidence color)
- Updates on each `final` WebSocket message that includes `detected_lang`

- [ ] **Step 5: Add bi-directional display open button**

In the browser buttons section, add a "Bi-directional Display" button that opens `bidirectional.html` in a new window. Only enabled when server is running and bi-directional mode is on.

- [ ] **Step 6: Add locked slot UI rendering**

Modify `buildTranslationSlots()` to render slots 0-1 as locked/read-only when bi-directional mode is active:
- Show labels like "Arabic → English (auto)" and "English → Arabic (auto)"
- Grey out the lang/mode dropdowns
- Start editable slots from index 2

- [ ] **Step 7: Add i18n strings**

Add translation keys for all new UI strings to `locales/en.json` and use `t()` for all visible text. Other locale files can use English fallback initially.

- [ ] **Step 8: Verify operator panel loads without errors**

Start the server, open the operator panel, verify the new toggle appears and existing controls still work.

- [ ] **Step 9: Commit**

```bash
git add operator.html locales/en.json
git commit -m "[feat] operator panel bi-directional mode UI"
```

---

## Task 8: Create `bidirectional.html` Display Page

**Files:**
- Create: `bidirectional.html`
- Modify: `server.py` (add route)

- [ ] **Step 1: Add `/bidirectional` route to display app**

In `server.py`, add a route on the display app:
```python
@display_app.get("/bidirectional")
async def bidirectional_page():
    return FileResponse("bidirectional.html")
```

- [ ] **Step 2: Create `bidirectional.html` with URL parameter handling**

The page supports two modes via URL parameters:
- `?lang=EN` — single-language mode
- `?mode=split` — split-screen mode (default if no params)

Parse the URL parameters on load to determine rendering mode.

- [ ] **Step 3: Implement WebSocket connection and message handling**

Connect to the same WebSocket as `display.html` (port 3000, path `/ws`). Handle message types:
- `final` — original caption with `detected_lang` and `is_translation`
- `final_translation` — translated text with `is_translation: true`
- `interim` / `interim_translation` — interim captions
- `clear_captions` — clear display
- `style` — apply styling

- [ ] **Step 4: Implement split-screen mode rendering**

Two side-by-side (or top/bottom) zones:
- Left/top zone: Language A — shows originals in Language A with primary color, translations to Language A with secondary color
- Right/bottom zone: Language B — same pattern

Each zone is a scrolling caption area similar to `display.html`.

- [ ] **Step 5: Implement single-language mode rendering**

One zone showing everything in the selected language:
- Original speech in that language → primary color
- Translated speech into that language → secondary color (dimmer/italic)

- [ ] **Step 6: Add color coding**

Apply different styling based on `is_translation`:
- `false` (original speech): primary text color, normal weight
- `true` (translated): secondary text color, italic

Colors are configurable via the existing style system from the operator panel.

- [ ] **Step 7: Add responsive split-screen layout**

CSS flexbox layout that works for both horizontal (side-by-side) and vertical (stacked) splits. Default to horizontal; the layout can be toggled via URL param `?split=v` for vertical.

- [ ] **Step 8: Verify page loads and connects**

Start server, open `http://localhost:3000/bidirectional?mode=split`, verify WebSocket connects and styling is received.

- [ ] **Step 9: Commit**

```bash
git add bidirectional.html server.py
git commit -m "[feat] add bidirectional.html display page with split-screen and single-language modes"
```

---

## Task 9: Launcher — Vosk Model Download Dialog and Browser Button

**Files:**
- Modify: `launcher.pyw`

- [ ] **Step 1: Add "Download Vosk Models" button in settings frame**

In `_build_ui()`, after the existing "Delete installed models" button (around line 519), add a new button:
```python
self._vosk_btn = ttk.Button(self._settings_frame, text=_t("launcher.download_vosk_models"),
           command=self._show_vosk_models_dialog)
self._vosk_btn.pack(fill="x", pady=(4, 0))
```

- [ ] **Step 2: Create `_show_vosk_models_dialog` method**

Follow the exact same pattern as `_show_tuned_models_dialog` (line 844):
- Toplevel dialog with dark theme
- Canvas + scrollbar for the model list
- Checkbox per available Vosk language model
- Download button that runs downloads in background threads
- Progress display

Define available Vosk models as a dict:
```python
VOSK_MODELS = {
    "en": {"name": "English (US)", "url": "https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip", "size": "40 MB"},
    "de": {"name": "German", "url": "https://alphacephei.com/vosk/models/vosk-model-small-de-0.15.zip", "size": "45 MB"},
    "fr": {"name": "French", "url": "https://alphacephei.com/vosk/models/vosk-model-small-fr-0.22.zip", "size": "41 MB"},
    "es": {"name": "Spanish", "url": "https://alphacephei.com/vosk/models/vosk-model-small-es-0.42.zip", "size": "39 MB"},
    "ru": {"name": "Russian", "url": "https://alphacephei.com/vosk/models/vosk-model-small-ru-0.22.zip", "size": "45 MB"},
    "it": {"name": "Italian", "url": "https://alphacephei.com/vosk/models/vosk-model-small-it-0.22.zip", "size": "48 MB"},
    "ja": {"name": "Japanese", "url": "https://alphacephei.com/vosk/models/vosk-model-small-ja-0.22.zip", "size": "48 MB"},
    "zh": {"name": "Chinese", "url": "https://alphacephei.com/vosk/models/vosk-model-small-cn-0.22.zip", "size": "42 MB"},
    "ar": {"name": "Arabic", "url": "https://alphacephei.com/vosk/models/vosk-model-ar-mgb2-0.4.zip", "size": "318 MB"},
    "pt": {"name": "Portuguese", "url": "https://alphacephei.com/vosk/models/vosk-model-small-pt-0.3.zip", "size": "31 MB"},
    "tr": {"name": "Turkish", "url": "https://alphacephei.com/vosk/models/vosk-model-small-tr-0.3.zip", "size": "35 MB"},
    "ko": {"name": "Korean", "url": "https://alphacephei.com/vosk/models/vosk-model-small-ko-0.22.zip", "size": "82 MB"},
}
```

- [ ] **Step 3: Add "Bi-directional Display" browser button**

In the browser buttons section of `_build_ui`, add a button for opening the bi-directional display:
```python
self.bidir_btn = ttk.Button(self._browser_frame, text=_t("launcher.bidirectional_display"),
                             style="Browser.TButton", command=self._open_bidirectional,
                             state="disabled")
self.bidir_btn.pack(fill="x", pady=(5, 0))
```

Add `_open_bidirectional` method that opens `http://localhost:3000/bidirectional?mode=split`.

Enable/disable this button alongside the other browser buttons when the server starts/stops.

- [ ] **Step 4: Add i18n strings**

Add translation keys to `locales/en.json`:
- `launcher.download_vosk_models`
- `launcher.bidirectional_display`
- Related dialog strings

- [ ] **Step 5: Verify syntax**

Run: `python -c "import py_compile; py_compile.compile('launcher.pyw', doraise=True); print('OK')"`

- [ ] **Step 6: Commit**

```bash
git add launcher.pyw locales/en.json
git commit -m "[feat] launcher Vosk model download dialog and bi-directional display button"
```

---

## Task 10: Vosk Multi-Language Download Support

**Files:**
- Modify: `download_models.py`

- [ ] **Step 1: Add multi-language Vosk model download support**

Extend `download_vosk_model()` to accept a language parameter:

```python
def download_vosk_model(models_dir=None, lang="en"):
    """Download a Vosk model for the specified language."""
    # Map of language codes to model URLs and directory names
    VOSK_MODEL_MAP = {
        "en": ("vosk-model-small-en-us-0.15", "https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip"),
        # ... (same list as Task 9 VOSK_MODELS)
    }
    if lang not in VOSK_MODEL_MAP:
        print(f"  Unknown language: {lang}")
        return False
    model_name, url = VOSK_MODEL_MAP[lang]
    # ... download and extract logic
```

- [ ] **Step 2: Add `--vosk-lang` and `--models-dir` CLI arguments**

Add CLI flags so the installer can call:
```
download_models.py --vosk-lang de --models-dir "C:\path\to\models"
```

The `--models-dir` argument overrides the default `APP_DIR / "models"` path. This is required because the installer runs the script with a different working directory than the installed app location.

- [ ] **Step 3: Verify syntax**

Run: `python -c "import py_compile; py_compile.compile('download_models.py', doraise=True); print('OK')"`

- [ ] **Step 4: Commit**

```bash
git add download_models.py
git commit -m "[feat] multi-language Vosk model download support"
```

---

## Task 11: Installer — Vosk Language Model Tasks

**Files:**
- Modify: `build/windows/installer.iss`

- [ ] **Step 1: Add Vosk model CustomMessages**

In the `[CustomMessages]` section, add descriptions for each Vosk language model:
```
DownloadVosk=Download Vosk language models (for CPU bi-directional translation)
VoskDe=German — Vosk (~45 MB download)
VoskFr=French — Vosk (~41 MB download)
VoskEs=Spanish — Vosk (~39 MB download)
VoskRu=Russian — Vosk (~45 MB download)
VoskAr=Arabic — Vosk (~318 MB download)
VoskJa=Japanese — Vosk (~48 MB download)
VoskZh=Chinese — Vosk (~42 MB download)
VoskModels=Vosk CPU Language Models (for bi-directional mode):
DownloadingVoskDe=Downloading German Vosk model...
DownloadingVoskFr=Downloading French Vosk model...
; ... etc for each language
```

- [ ] **Step 2: Add Vosk model tasks**

In the `[Tasks]` section, add Vosk model checkboxes (inside the `#if EDITION == "Full"` block, or outside if CPU edition should also have them):
```
Name: "vosk_lang"; Description: "{cm:DownloadVosk}"; GroupDescription: "{cm:VoskModels}"; Flags: unchecked
Name: "vosk_lang\de"; Description: "{cm:VoskDe}"; Flags: unchecked
Name: "vosk_lang\fr"; Description: "{cm:VoskFr}"; Flags: unchecked
; ... etc
```

- [ ] **Step 3: Add Vosk model download Run entries**

In the `[Run]` section:
```
Filename: "{app}\venv\Scripts\python.exe"; Parameters: """{app}\download_models.py"" --vosk-lang de --models-dir ""{app}\models"""; Tasks: vosk_lang\de; StatusMsg: "{cm:DownloadingVoskDe}"; Flags: runhidden
; ... etc for each language
```

- [ ] **Step 4: Add `lang_detect.py` to installer Files section**

In `[Files]`, add:
```
Source: "..\..\lang_detect.py"; DestDir: "{app}"; Flags: ignoreversion
```

- [ ] **Step 5: Add `bidirectional.html` to installer Files section**

```
Source: "..\..\bidirectional.html"; DestDir: "{app}"; Flags: ignoreversion
```

- [ ] **Step 6: Commit**

```bash
git add build/windows/installer.iss
git commit -m "[feat] installer tasks for Vosk language models and new files"
```

---

## Task 12: Install onnxruntime in Build Venvs

**Files:**
- Modify: `build/windows/build.bat`

- [ ] **Step 1: Add onnxruntime to both venv builds**

In the Lite venv section (after Vosk install, around line 128):
```batch
echo   Installing language detection runtime...
"%VENV_LITE%\Scripts\pip.exe" install onnxruntime >> "%SCRIPT_DIR%build_log.txt" 2>&1
```

Same for Full venv section (after offline translation packages, around line 168).

- [ ] **Step 2: Commit**

```bash
git add build/windows/build.bat
git commit -m "[build] add onnxruntime to both installer venvs"
```

---

## Task 13: Integration Testing and Polish

- [ ] **Step 1: End-to-end test — Whisper backend, bi-directional mode**

1. Start server with Whisper backend
2. Open operator panel, enable bi-directional mode, select English + one other language
3. Verify auto-managed slots 0-1 appear locked
4. Verify detection indicator shows language changes
5. Open `bidirectional.html?mode=split` — verify split display works
6. Speak in both languages — verify correct detection, transcription, and reverse translation

- [ ] **Step 2: End-to-end test — Vosk backend**

1. Download a second Vosk model from the launcher
2. Start server with Vosk backend
3. Enable bi-directional mode, select the two languages
4. Verify model loading status
5. Speak in both languages — verify detection routes to correct recognizer

- [ ] **Step 3: Test observer slots**

1. With bi-directional mode on, add a 3rd translation slot (observer)
2. Speak in either language — verify the observer slot translates from the correct source language

- [ ] **Step 4: Test toggle mid-session**

1. Enable bi-directional mode, speak some text
2. Toggle off — verify auto-managed slots removed, standard mode resumes
3. Toggle back on — verify bi-directional slots recreated

- [ ] **Step 5: Test single-language display mode**

Open `bidirectional.html?lang=EN` — verify all captions appear in English with color coding for original vs translated.

- [ ] **Step 6: Test speaker language override**

1. Assign Language A to Speaker 1, Language B to Speaker 2
2. Switch speakers — verify detection is overridden by assignment
3. Remove assignment — verify auto-detection resumes

- [ ] **Step 7: Build Windows installers**

Run `build.bat` and verify both CPU and GPU installers compile successfully with the new files and tasks.

- [ ] **Step 8: Final commit**

```bash
git add -A
git commit -m "[feat] bi-directional translation — integration polish"
```
