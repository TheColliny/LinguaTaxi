# Design: Multiple Audio Sources with Speaker Management

**Date:** 2026-03-21
**Status:** Approved

## Overview

Add support for up to 8 simultaneous audio input sources (physical mics, system audio/loopback) with automatic per-source speaker labeling. Expand speaker management to 50 speakers with per-speaker color customization and full persistence across restarts.

## 1. Multi-Source Audio Architecture

### AudioSource Data Model

Each source is an `AudioSource` object containing all per-source state:

```
AudioSource:
  id: int                          # 0-7, stable identifier
  device_index: int | None         # sounddevice device index (None = system default)
  name: str                        # display name ("Room Mic", "Zoom Audio")
  speaker: str                     # current speaker label ("Alice")
  color: str                       # speaker color hex ("#4FC3F7"), defaults to text color
  speaker_change_pending: dict|None # per-source pending speaker change
  queue: Queue                     # per-source audio queue
  stream: sd.InputStream | None    # per-source audio stream
  # Buffer loop state (managed by the per-source buffer thread):
  seg_start: float                 # segment start time
  is_speech: bool                  # currently detecting speech
  silence_start: float             # when silence began
```

The current global variables `current_speaker`, `_speaker_change_pending` are removed and replaced by per-source state in `AudioSource`.

### Source Management
- Up to 8 audio sources, each with its own `sd.InputStream`, own `queue.Queue()`, and own accumulation buffer
- Sources tracked in a thread-safe list of `AudioSource` objects
- Single shared Whisper/MLX model instance — sources timeshare it via a shared transcription queue

### Audio Flow Per Source (Whisper/MLX)
1. Each source has its own `sd.InputStream` callback pushing to its own `source.queue`
2. Each source has its own buffer accumulation thread (same logic as current `_buffer_audio_loop` but parameterized with the `AudioSource` object — reads `source.speaker`, `source.speaker_change_pending`, etc.)
3. When a buffer is ready to transcribe (silence gap or max duration), a `(source_id, audio_buffer, speaker, color, seg_start)` tuple is submitted to a shared transcription queue
4. A single transcription worker pulls from the shared queue, runs Whisper, and broadcasts the result with the source's speaker name and color
5. Queue depth limit: 16 entries. If full, oldest entry dropped (prevents unbounded memory growth with 8 active sources)

### Vosk Backend — Per-Source Recognizer
Vosk uses a stateful `KaldiRecognizer` that cannot be shared across sources. Each Vosk source gets:
- Its own `KaldiRecognizer` instance (~150MB per small model)
- Its own streaming processing loop (same as current `_vosk_audio_loop` but parameterized per-source)
- Feeds audio chunks directly to its own recognizer (no shared transcription queue)
- Speaker change on Vosk sources uses force-finalize (same as current behavior, no retroactive split)
- Memory note: 8 Vosk sources = ~1.2GB. In practice, most setups will use 2-3 sources.

### Interleaved Output
- All sources buffer independently and transcribe as soon as they hit their silence gap
- If multiple sources finalize at the same moment, they appear in quick succession on the display
- No source blocks another — truly concurrent capture with sequential transcription (Whisper/MLX) or fully parallel (Vosk)

### Speaker Assignment
- Each source has a current speaker name (default: "Source 1", "Source 2", etc.) stored in `source.speaker`
- Operator can rename sources at any time
- Operator can also use speaker buttons (1-9 hotkeys, 10-50 click-only) to override the current speaker for any source
- Same retroactive 0.5s split logic applies per-source for Whisper/MLX backends, operating on `source.speaker_change_pending`
- `_broadcast_final()` accepts speaker and color as parameters instead of reading globals
- `_check_speaker_change()` operates on the source's own `speaker_change_pending` state

### Device Detection
- Physical mics and loopback/system audio devices listed separately in dropdowns
- Loopback devices detected by checking for "loopback", "stereo mix", "what u hear", "WASAPI" in device names (case-insensitive)
- If no system audio devices found, a help tooltip/note guides the user: "No system audio devices found — enable Stereo Mix in Windows Sound settings or install a virtual audio cable"
- Device open errors (e.g., exclusive-mode conflict) shown as user-friendly error messages per source

### Runtime Source Add/Remove
- New API endpoints: `POST /api/sources/add` (body: `{device_index}`) and `POST /api/sources/remove` (body: `{source_id}`)
- Adding a source: creates AudioSource, opens InputStream, starts buffer thread
- Removing a source: signals buffer thread to stop, closes InputStream, flushes any remaining buffer (transcribes it), removes from source list
- Speaker config for removed sources preserved in config.json (reconnects if same device re-added)
- Sources can be added/removed while captioning is live or paused

## 2. Launcher GUI — Multi-Source UI

### Source List in Settings Frame
- The current single "Microphone" dropdown becomes "Audio Source 1" with the same device dropdown
- Below it, a "+ Add Source" button
- Clicking adds a new row: "Audio Source N" with its own device dropdown and an "X" remove button
- Up to 8 sources total. The "+ Add Source" button hides when 8 are active
- Each dropdown groups devices: "Microphones" section at top, "System Audio" section below (loopback devices)
- If no system audio devices detected, the "System Audio" section shows a disabled entry with guidance text

### CLI Passing to Server
- Current `--mic INDEX` changes to `--sources INDEX1,INDEX2,...` — comma-separated device indices
- Use `-1` as sentinel for system default device (e.g., `--sources -1,3,5`)
- Server parses and creates one AudioSource per index
- Backward compatible: `--mic INDEX` still accepted, treated as `--sources INDEX`

### Settings Persistence
- `launcher_settings.json` changes `mic_index` (single int) to `source_indices` (list of ints, `-1` for system default)
- Backward compatible: if old `mic_index` found on load, migrated to `source_indices: [mic_index]`

## 3. Operator Panel — Speaker Management

### Source-Speaker Rows
- New section in the operator panel: "Audio Sources & Speakers"
- Each active audio source shows as a row: `[color square] [editable name field] [source device label]`
- The color square is a small clickable swatch (defaults to current text color for all speakers)
- Clicking the color square opens the color picker popup
- Clicking a source row selects it as the "focused source" (highlighted border)

### Focused Source
- Default focus: Source 1 (first source) when the operator page loads
- Keyboard shortcuts (1-9) apply to the currently focused source
- Focus is client-side state only (each operator panel tab has independent focus)
- Visual indicator: highlighted border + subtle background color on the focused row

### Speaker Buttons (Expanded to 50)
- Speakers 1-9: keyboard shortcuts (1-9 keys, same as today)
- Speakers 10-50: click-only buttons, no hotkeys
- Displayed as a scrollable grid below the source rows
- New empty slots appear as current slots are filled (e.g., if 3 speakers are named, slot 4 shows as an empty "+" to add another)
- Each speaker button shows: `[color square] [name]`
- Clicking a speaker button assigns that speaker to the currently focused audio source
- The 0.5s retroactive split triggers on the focused source only

### Color Picker Popup
- 32 preset color swatches in a grid (4 rows x 8 columns)
- Preset colors computed client-side using evenly spaced hues at high saturation, filtered for WCAG AA contrast ratio (4.5:1) against the current display background color
- Below the grid: HSB picker — saturation-brightness square + hue slider bar
- Clicking a preset applies immediately
- HSB picker applies on release/change
- "Reset" button to return to default text color
- Speaker colors are fixed once assigned — changing the display background does not auto-update existing speaker colors

### Reset Speakers
- "Reset Speakers" button in the speaker management section
- First click: replaces with "Confirm Reset?" prompt with "Yes" and "No" buttons
- Prompt stays for 10 seconds, then reverts to the original button if neither is clicked
- "Yes": clears all speaker names back to defaults ("Source 1", "Source 2"...), resets all colors to default text color, clears all speaker assignments. Does NOT remove audio sources.
- "No": cancels, reverts to original button immediately

## 4. Display Pages — Speaker Colors & WebSocket Protocol

### Rendering
- Speaker name labels rendered in the speaker's assigned color
- Transcript text remains the current text color (not colored per speaker)
- Applies to `display.html`, `operator.html`, and extended display

### WebSocket Protocol Changes
All transcription messages gain `source_id` and `color` fields:

- `final`: `{type: "final", text: "...", speaker: "Alice", color: "#4FC3F7", source_id: 0, lang: "EN"}`
- `interim`: `{type: "interim", text: "...", speaker: "Alice", color: "#4FC3F7", source_id: 0}`
- `speaker_change`: `{type: "speaker_change", speaker: "Alice", color: "#4FC3F7", source_id: 0}`

New messages for source management:
- `source_list`: `{type: "source_list", sources: [{id: 0, name: "Room Mic", speaker: "Alice", color: "#4FC3F7"}, ...]}` — sent on WebSocket connect
- `source_added`: `{type: "source_added", source: {id, name, speaker, color}}`
- `source_removed`: `{type: "source_removed", source_id: 0}`

Updated operator messages:
- `set_speaker`: `{type: "set_speaker", speaker: "Alice", source_id: 0}` — now requires `source_id`

### Transcript Files
- Speaker names already prefix each line in transcripts (existing behavior)
- No additional source identifier needed — the speaker name is sufficient for distinguishing sources in the transcript

### Persistence
- Speaker names, colors, and source-to-speaker assignments saved to `config.json` (server-side config)
- Keyed by device index for reconnection on restart
- Restored on program restart — sources reconnect to their previous speaker assignments by matching device index
- If a previously used device is no longer available, the speaker config is kept but marked inactive

## 5. Dictation Mode

- Dictation mode uses Source 1 only (the first/primary audio source)
- Multi-source does not affect dictation — it always feeds from the first source
- No changes to `dictation.html` beyond adding the `color` field to message handling

## Files Modified

| File | Changes |
|------|---------|
| `server.py` | AudioSource class, multi-source capture, per-source buffers/state, shared transcription queue (Whisper/MLX), per-source KaldiRecognizer (Vosk), remove global speaker state, expanded speaker management, color in WebSocket messages, new source management API endpoints, config persistence, `--sources` CLI arg |
| `launcher.pyw` | Multi-source UI (add/remove rows, grouped dropdowns, loopback detection), `source_indices` settings, `--sources` CLI passing, `-1` sentinel for system default |
| `operator.html` | Source-speaker rows with focused-source selection, expanded speaker buttons (50), color picker popup (32 presets + HSB picker), reset with 10s Yes/No confirmation, per-source speaker assignment via `source_id` |
| `display.html` | Colored speaker name labels, `source_id` and `color` fields in WebSocket message handling |
| `dictation.html` | Add `color` field to message handling |
