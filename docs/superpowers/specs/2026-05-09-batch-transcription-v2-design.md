# Batch Transcription V2 — Design Spec

## Problem

The current batch transcription dialog in the launcher has no translation controls — it silently uses whatever translations the operator panel has configured. There's no way to select a folder of files, no way to translate text files, and no way to choose a translation engine or output directory from the batch dialog.

## Solution

Enhance the launcher's batch transcription dialog with:
1. Translation engine selection (No Translation / DeepL / OPUS-MT / M2M-100) with dynamic language slot management
2. Folder selection with optional subfolder recursion
3. Text file translation (`.txt`, `.srt`, `.vtt`, `.md`)
4. Output directory picker
5. Input sanitization for text files

## Architecture

Three files change:

| File | Role |
|------|------|
| `launcher.pyw` | Expanded dialog UI — engine picker, language slots, folder picker, output dir |
| `server.py` | Enhanced batch endpoint accepts translation config + folder path as JSON |
| `transcribe_file.py` | New `batch_translate_text()` for text files, `batch_folder()` for directory iteration |

**Key principle:** The batch dialog owns its own translation configuration. It does NOT read or modify the operator panel's live translation settings.

## Dialog UI

The dialog grows from 440x320 to ~440x480. Layout top-to-bottom:

### File/Folder Selection Row
- Two buttons side by side: "Select File" and "Select Folder"
- Selected path displayed below (truncated if long)
- When folder selected: "Include subfolders" checkbox appears (default unchecked)

### Live Playback Option
- "Play as Live Input" checkbox — only visible when a single audio file is selected
- When checked, hides all translation/output controls and behaves like the existing live playback mode

### Translation Engine Dropdown
- Options: "No Translation" (default), "DeepL (Online)", "Offline (Language Specific)", "Offline (M2M 100+ Languages)"
- "Offline (Language Specific)" (OPUS-MT) is hidden when the source language is not English

### Language Slots (hidden when "No Translation")
- Each row: language dropdown + remove button (x)
- "+" button to add another language row
- Dropdowns are filtered: already-selected languages are removed from all other dropdowns
- All dropdowns update live when any selection changes (no reliance on the + button to refresh)
- Available languages depend on the selected engine:
  - DeepL: 32 targets (EN-GB, EN-US, PT-BR, PT-PT variants)
  - OPUS-MT: ~23 languages (English source only)
  - M2M-100: ~30 languages (any source)

### Engine Switch Behavior
- If all selected languages are compatible with the new engine: switch silently
- If any selected languages are incompatible: show a confirm dialog naming the incompatible languages (e.g., "Switching to OPUS-MT will remove Arabic, Japanese. Continue?"). On confirm, drop incompatible languages. On cancel, revert engine selection.
- If all languages are dropped after engine switch: reset to "No Translation" state

### Output Directory
- Path display with "Change..." button
- Default: parent directory of selected file or folder

### Progress / Buttons
- Status label, progress bar, Start/Cancel buttons (same pattern as current)

## Data Flow — Single File (Audio)

1. User picks file → selects engine + languages → picks output dir → Start
2. Launcher POSTs to `POST /api/transcribe-file/batch` with `{file_path, translations: [{lang, mode},...], output_dir, source_lang}`
3. `batch_transcribe()` runs using the provided translations list and output dir instead of reading from operator config

## Data Flow — Single File (Text)

1. Same dialog flow — launcher detects text extension (`.txt`, `.srt`, `.vtt`, `.md`)
2. POSTs to `POST /api/transcribe-file/batch` with same shape
3. Server detects text vs audio by extension
4. `batch_translate_text()` reads lines, sanitizes, translates each line per language, saves `filename_LANG.ext` per language to the output directory

## Data Flow — Folder

1. User picks folder → optional "Include subfolders" checkbox → same engine/language/output setup → Start
2. Launcher POSTs with `{folder_path, recursive: bool, translations, output_dir, source_lang}`
3. Server iterates matching files (audio + text extensions), calls the appropriate function per file
4. Progress reports per-file: "Processing file 3/17: meeting.wav (segment 5/12)"

## File Type Detection

- **Audio**: `.wav`, `.mp3`, `.flac`, `.m4a`, `.ogg`, `.webm`
- **Text**: `.txt`, `.srt`, `.vtt`, `.md`
- **Everything else**: silently skipped

## Behavior Matrix

| Selection | Translation? | Audio files | Text files |
|-----------|-------------|-------------|------------|
| File/Folder | No Translation | Transcribe only | Skipped (warning: "Text files require translation") |
| File/Folder | Engine selected | Transcribe + translate | Translate only |
| Single audio file | Any | Also offers "Play as Live Input" | N/A |
| Single text file | Any | N/A | Hide "Play as Live Input" |

## Input Sanitization (Text Files)

- Strip null bytes and control characters (except newlines and tabs)
- Cap line length at 5000 characters (truncate, do not reject)
- Cap file size at 10MB (skip with error in progress: "Skipped filename.txt (exceeds 10MB limit)")
- UTF-8 decode with error replacement (no crashes on bad encoding)

## API Changes

### `POST /api/transcribe-file/batch` (enhanced)

Current: `file_path: str = Form(...)`

New: accepts JSON body instead of form data:

```json
{
  "file_path": "C:/path/to/file.wav",
  "folder_path": null,
  "recursive": false,
  "translations": [
    {"lang": "ES", "mode": "deepl"},
    {"lang": "FR", "mode": "offline-m2m"}
  ],
  "output_dir": "C:/path/to/output",
  "source_lang": "EN"
}
```

- Either `file_path` or `folder_path` is set, not both
- `translations` is an empty list when "No Translation" is selected
- `mode` values: `"deepl"`, `"offline-opus"`, `"offline-m2m"`
- `source_lang` defaults to the server's configured input language if not provided

### `POST /api/transcribe-file/live` (unchanged)

Still accepts a single audio file path via form data. No changes.

### `GET /api/transcribe-file/progress` (enhanced)

Add `current_file` field for folder processing:

```json
{
  "status": "processing",
  "pct": 45,
  "message": "Translating to ES...",
  "current_file": "meeting.wav",
  "files_done": 3,
  "files_total": 17
}
```

## `transcribe_file.py` Changes

### New: `batch_translate_text()`

```python
def batch_translate_text(file_path, translate_fn, translations,
                         output_dir, source_lang, progress_callback=None) -> dict:
    """Translate a text file to multiple languages.
    Returns {lines, languages, output_dir}."""
```

- Reads file with UTF-8 (errors="replace")
- Applies sanitization (null bytes, control chars, line length cap)
- Rejects files > 10MB
- Translates line by line per language
- Saves `filename_LANG.ext` per language to `output_dir`

### New: `batch_folder()`

```python
def batch_folder(folder_path, recursive, stt_backend, translate_fn,
                 translations, output_dir, source_lang,
                 progress_callback=None) -> dict:
    """Process all supported files in a folder.
    Returns {files_processed, files_skipped, total_lines, languages, output_dir}."""
```

- Collects audio + text files (respects `recursive` flag)
- Calls `batch_transcribe()` for audio, `batch_translate_text()` for text
- Skips text files if `translations` is empty (logs warning)
- Updates progress with per-file tracking

### Modified: `batch_transcribe()`

- Accept `output_dir` parameter (currently hardcoded to `transcripts_dir`)
- Accept `translations` directly from the API (already does this)

## Edge Cases

| Case | Behavior |
|------|----------|
| Folder with no supported files | Error: "No audio or text files found in folder" |
| Folder with mix of audio + text | Process all — transcribe+translate audio, translate-only text |
| Text file with no translation configured | Skip with warning: "Text files require translation — select a language" |
| Audio file with no translation configured | Transcribe only (current behavior) |
| Single audio file selected | Show "Play as Live Input" option |
| Single text file selected | Hide "Play as Live Input" |
| Engine switch drops languages | Confirm dialog naming incompatible languages |
| All languages removed after engine switch | Reset to "No Translation" state |
| Empty text file | Skip, log warning |
| Text file > 10MB | Skip with error in progress |
| Badly encoded text file | Read with UTF-8 replacement characters, proceed |
| Unsupported extension in folder | Silently skip |
| Batch already in progress | Reject: "File transcription already in progress" |

## Not In Scope

- Structured format translation (CSV, JSON, HTML, XML) — plain text only for now
- Drag-and-drop onto the dialog
- Saving/loading translation presets
- Progress persistence across launcher restarts
