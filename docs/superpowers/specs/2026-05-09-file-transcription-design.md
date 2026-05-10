# File Transcription — Design Spec

## Problem

LinguaTaxi can only transcribe live microphone input. There is no way to transcribe an existing audio file to text, and no way to replay a known audio file through the live pipeline for troubleshooting.

## Solution

Add file transcription with two modes accessible from a single "Transcribe File" button in the launcher:

1. **Batch Transcribe** — Process an audio file offline, transcribe it, run translations on each line, save output as text files (one per language). Fast, no live display.
2. **Play as Live Input** — Feed the audio file into the existing live captioning pipeline as if it were a microphone. Display updates, translations fire, plugins trigger. Useful for testing and troubleshooting.

## Architecture

A new `transcribe_file.py` module handles all file transcription logic. It exposes functions consumed by two new API endpoints on the operator app. The launcher gets one button that opens a file dialog, then a mode-selection popup.

### Files

| File | Role |
|------|------|
| `transcribe_file.py` (new) | File loading, resampling, segmentation, batch processing, live feed logic |
| `server.py` | Two new endpoints + one stop endpoint |
| `launcher.pyw` | "Transcribe File" button + dialog |

## Data Flow — Batch Mode

1. User clicks "Transcribe File" in launcher → native file picker → selects "Batch Transcribe"
2. Launcher POSTs the audio file to `POST /api/transcribe-file/batch`
3. `transcribe_file.py` loads the file into numpy via `soundfile` and resamples to 16kHz mono float32
4. Segments the audio using the same silence detection parameters from `server.py` (`SILENCE_THRESHOLD=0.008`, `SILENCE_DURATION=0.7`, `MAX_SEGMENT_DURATION=8`)
5. Each segment is transcribed via `stt_backend._transcribe(buf)`
6. Each transcribed line runs through `translate_text()` for every active translation slot
7. Output saved as timestamped text files in the transcripts directory — one per language, same format as live transcripts (e.g., `20260509_163000_EN.txt`, `20260509_163000_ES.txt`)
8. Server returns summary JSON: `{lines: N, duration_sec: N, output_dir: "...", languages: ["EN", "ES"]}`
9. Launcher shows a completion dialog with the results
10. Progress is available via `GET /api/transcribe-file/progress` — returns `{status: "processing"|"done"|"error", pct: 0-100, message: ""}`

### Batch Mode — Vosk Handling

Vosk's `KaldiRecognizer` requires chunk-by-chunk PCM feeding rather than a single `_transcribe(buf)` call. For Vosk:
- Create a temporary `KaldiRecognizer` with the loaded model
- Feed each segment's audio as 16-bit PCM bytes in chunks
- Collect `FinalResult()` after each segment
- This runs independently from the live Vosk recognizer

## Data Flow — Live Playback Mode

1. User clicks "Transcribe File" in launcher → file picker → selects "Play as Live Input"
2. Launcher POSTs to `POST /api/transcribe-file/live`
3. Server pauses the active mic stream (`source.stream.stop()`) and stores a reference to resume later
4. `transcribe_file.py` reads the file in chunks matching the mic's chunk duration (0.5s at 16kHz = 8000 samples) and feeds them into the active `AudioSource.queue` at real-time pace using a threading timer
5. The existing `_buffer_audio_loop` / `_vosk_source_loop` processes it exactly like mic input — display updates, translations fire, plugins trigger
6. When the file ends, server resumes the mic stream (`source.stream.start()`)
7. `POST /api/transcribe-file/stop` allows cancelling mid-playback and immediately resumes mic

### Live Playback — Mic Resume

If the user clicks "GO LIVE" on the operator panel or "Stop Playback" in the launcher, the file playback stops and mic input resumes. The stop endpoint handles cleanup.

## API Endpoints

### `POST /api/transcribe-file/batch`
- Body: multipart form with `file` (audio file)
- Returns: `{status: "started"}` or `{error: "..."}`
- Runs in a background thread
- Progress via `GET /api/transcribe-file/progress`

### `POST /api/transcribe-file/live`
- Body: multipart form with `file` (audio file)
- Returns: `{status: "playing", duration_sec: N}` or `{error: "..."}`

### `POST /api/transcribe-file/stop`
- No body
- Stops live playback if active, resumes mic
- Returns: `{status: "stopped"}`

### `GET /api/transcribe-file/progress`
- Returns: `{status: "idle"|"processing"|"done"|"error"|"playing", pct: 0-100, message: ""}`

## Launcher UI

### Button Placement
One "Transcribe File" button in the Server Control section of the launcher, alongside the existing Start/Stop server buttons. Disabled when server is not running.

### Dialog Flow
1. Click button → native OS file picker opens
   - Filters: `.wav`, `.mp3`, `.flac`, `.m4a`, `.ogg`, `.webm`
2. After file selection → `customtkinter` dialog appears:
   - Filename displayed (truncated if long)
   - Two radio buttons: "Batch Transcribe" / "Play as Live Input"
   - "Start" button + "Cancel" button
3. **Batch mode**: Progress bar appears, updates via polling `/api/transcribe-file/progress`. On completion, shows summary (lines transcribed, output directory). "Open Folder" button to open transcript directory.
4. **Live mode**: "Stop Playback" button replaces "Start". Status label shows elapsed/total time. When file ends or user stops, dialog closes and mic resumes.

## Audio Format Support

- **Native** (`soundfile`): `.wav`, `.flac`, `.ogg`
- **Extended** (`pydub` with `ffmpeg`): `.mp3`, `.m4a`, `.webm`
- If extended format is selected and `pydub`/`ffmpeg` is not available, show error: "MP3/M4A support requires ffmpeg. Install it or convert to WAV."
- All formats are loaded and converted to 16kHz mono float32 numpy arrays before processing

## `transcribe_file.py` Module

### Public Functions

```python
def load_audio(file_path: str) -> tuple[np.ndarray, float]:
    """Load audio file, resample to 16kHz mono float32.
    Returns (samples, duration_sec). Raises ValueError for unsupported formats."""

def segment_audio(samples: np.ndarray, silence_threshold=0.008,
                  silence_duration=0.7, max_segment_duration=8.0) -> list[np.ndarray]:
    """Split audio into segments using silence detection.
    Same algorithm as server.py's _buffer_audio_loop."""

def batch_transcribe(file_path: str, stt_backend, translate_fn, translations: list,
                     transcripts_dir: str, source_lang: str,
                     progress_callback=None) -> dict:
    """Full batch pipeline: load → segment → transcribe → translate → save.
    Returns {lines, duration_sec, output_dir, languages}."""

def start_live_playback(file_path: str, source, on_complete=None) -> threading.Event:
    """Feed audio file into source.queue at real-time pace.
    Returns a stop_event that can be set to cancel playback."""

def stop_live_playback():
    """Stop any active live playback and signal completion."""
```

## Edge Cases

| Case | Behavior |
|------|----------|
| Server not running | Button disabled (same as existing browser buttons) |
| Already playing/processing a file | Reject with error: "File transcription already in progress" |
| Live session active + batch mode | Works independently — doesn't touch display or audio pipeline |
| Live session active + playback mode | Mic paused, file takes over. On stop/end, mic resumes |
| GO LIVE pressed during playback | Playback stops, mic resumes |
| Unsupported format without ffmpeg | Error message with install instructions |
| Empty/corrupt audio file | Error returned: "Could not read audio file" |
| Very long file in batch mode | Progress bar shows percentage. No timeout — let it run |
| Vosk backend + batch mode | Uses temporary KaldiRecognizer with chunk feeding |

## Not In Scope

- Drag-and-drop onto the launcher window
- Batch processing of multiple files at once
- Speed control for live playback (always real-time)
- Operator panel UI for file transcription (launcher only)
