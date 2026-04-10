# Multiple Audio Sources with Speaker Management — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Support up to 8 simultaneous audio inputs with per-source speaker labeling, 50 speakers with color customization, and a shared Whisper model.

**Architecture:** Each audio source gets its own `sd.InputStream`, queue, and buffer thread. Whisper/MLX sources submit ready buffers to a shared transcription queue processed by a single worker. Vosk sources get per-source `KaldiRecognizer` instances. All global speaker state (`current_speaker`, `_speaker_change_pending`) moves into per-source `AudioSource` objects. The operator panel gains source-focus selection, 50-speaker grid, and an HSB color picker.

**Tech Stack:** Python 3.11, sounddevice, faster-whisper, vosk, FastAPI, WebSocket, JavaScript (vanilla)

**Spec:** `docs/superpowers/specs/2026-03-21-multi-source-audio-design.md`

---

## File Structure

| File | Action | Responsibility |
|------|--------|---------------|
| `server.py` | Modify | AudioSource class, multi-stream capture, per-source buffers, shared transcription queue, Vosk per-source recognizers, WebSocket protocol, source management API, speaker persistence, `--sources` CLI |
| `launcher.pyw` | Modify | Multi-source UI rows, grouped device dropdowns, loopback detection, `source_indices` settings, `--sources` CLI passing |
| `operator.html` | Modify | Source-speaker rows, focused source, expanded 50-speaker grid, color picker (presets + HSB), reset confirmation |
| `display.html` | Modify | Colored speaker name labels, `source_id`/`color` in message handling |
| `dictation.html` | Modify | Add `color` field to message handling |

---

### Task 1: AudioSource Data Model + Replace Global Speaker State

**Files:**
- Modify: `server.py:232-250` (global audio variables)
- Modify: `server.py:246-248` (current_speaker, _speaker_change_pending, _speaker_lock)

This is the foundational change. Create the `AudioSource` class and a source manager, then remove the global speaker variables.

- [ ] **Step 1: Add AudioSource class and source manager**

After the existing globals section (around line 250), add:

```python
class AudioSource:
    """Represents one audio input source with its own capture stream and speaker state."""
    _next_id = 0

    def __init__(self, device_index=None, name=None):
        self.id = AudioSource._next_id
        AudioSource._next_id += 1
        self.device_index = device_index
        self.name = name or f"Source {self.id + 1}"
        self.speaker = ""
        self.color = ""  # empty = use default text color
        self.speaker_change_pending = None  # {"name": str, "time": float}
        self.speaker_lock = threading.Lock()
        self.queue = queue.Queue()
        self.stream = None  # sd.InputStream
        self.capture_thread = None
        self.buffer_thread = None
        self.active = True
        self.restart_event = threading.Event()

    def set_speaker(self, name, color=""):
        """Set pending speaker change with retroactive timing."""
        with self.speaker_lock:
            self.speaker_change_pending = {"name": name, "time": time.time()}
        self.speaker = name
        if color:
            self.color = color

# Thread-safe source registry
_sources = []  # List[AudioSource]
_sources_lock = threading.Lock()
_transcription_queue = queue.Queue(maxsize=16)  # shared for Whisper/MLX
```

- [ ] **Step 2: Remove old global speaker variables**

Remove or comment out these globals (lines 246-248):
```python
current_speaker = ""
_speaker_change_pending = None
_speaker_lock = threading.Lock()
```

Also remove `audio_queue = queue.Queue()` at line 232 — each source now has its own queue.

Keep `current_mic_index` (line 239) for backward compatibility but it will be deprecated.

- [ ] **Step 3: Add source helper functions**

```python
def get_source(source_id):
    """Get an AudioSource by ID."""
    with _sources_lock:
        for s in _sources:
            if s.id == source_id:
                return s
    return None

def add_source(device_index=None, name=None):
    """Create and register a new AudioSource."""
    if len(_sources) >= 8:
        return None
    src = AudioSource(device_index, name)
    with _sources_lock:
        _sources.append(src)
    return src

def remove_source(source_id):
    """Stop and remove an AudioSource."""
    src = get_source(source_id)
    if not src:
        return False
    src.active = False
    src.restart_event.set()
    if src.stream:
        try:
            src.stream.stop()
            src.stream.close()
        except Exception:
            pass
    with _sources_lock:
        _sources[:] = [s for s in _sources if s.id != source_id]
    return True
```

- [ ] **Step 4: Commit**

```bash
git add server.py
git commit -m "[feat] add AudioSource data model and source registry, remove global speaker state"
```

---

### Task 2: Per-Source Audio Capture

**Files:**
- Modify: `server.py:661-685` (audio_callback, start_audio_capture)

Refactor capture to work per-source instead of using a single global stream.

- [ ] **Step 1: Replace audio_callback and start_audio_capture**

Replace `audio_callback` (lines 661-663) and `start_audio_capture` (lines 665-685) with:

```python
def _make_audio_callback(source):
    """Create a callback bound to a specific AudioSource's queue."""
    def callback(indata, frames, ti, status):
        if status:
            log.warning(f"Audio [{source.name}]: {status}")
        source.queue.put(indata.copy())
    return callback

def start_source_capture(source):
    """Open audio stream for a single AudioSource. Runs in its own thread."""
    bs = int(SAMPLE_RATE * CHUNK_DURATION)
    while source.active and not shutdown_event.is_set():
        source.restart_event.clear()
        try:
            cb = _make_audio_callback(source)
            s = sd.InputStream(samplerate=SAMPLE_RATE, channels=CHANNELS, dtype=DTYPE,
                               blocksize=bs, device=source.device_index, callback=cb)
            source.stream = s
            s.start()
            log.info(f"Audio capture started for [{source.name}] (device: {source.device_index or 'default'})")
            while source.active and not shutdown_event.is_set() and not source.restart_event.is_set():
                shutdown_event.wait(0.3)
            s.stop()
            s.close()
            source.stream = None
            if source.restart_event.is_set() and source.active:
                log.info(f"Restarting capture for [{source.name}]")
                continue
            break
        except Exception as e:
            log.error(f"Audio capture error [{source.name}]: {e}")
            source.stream = None
            break
```

- [ ] **Step 2: Keep old start_audio_capture as a thin wrapper for backward compat**

```python
def start_audio_capture(dev_idx=None):
    """Legacy single-source capture. Creates Source 0 if not exists."""
    src = get_source(0)
    if not src:
        src = add_source(dev_idx, "Microphone")
    start_source_capture(src)
```

- [ ] **Step 3: Commit**

```bash
git add server.py
git commit -m "[feat] refactor audio capture to per-source streams with callbacks"
```

---

### Task 3: Per-Source Buffer Loop + Shared Transcription Queue

**Files:**
- Modify: `server.py:270-309` (_check_speaker_change)
- Modify: `server.py:312-369` (_buffer_audio_loop)
- Modify: `server.py:607-624` (_broadcast_final)

Parameterize the buffer loop and speaker change logic to operate on an AudioSource.

- [ ] **Step 1: Refactor _check_speaker_change to accept source**

Change `_check_speaker_change(transcribe_fn, buf, seg_start, loop)` to `_check_speaker_change(source, transcribe_fn, buf, seg_start, loop)`.

Replace all reads of the global `_speaker_change_pending` with `source.speaker_change_pending`, and all reads/writes of `current_speaker` with `source.speaker`. Use `source.speaker_lock` instead of `_speaker_lock`.

- [ ] **Step 2: Refactor _broadcast_final to accept speaker and color**

Change signature from `_broadcast_final(text, loop)` to `_broadcast_final(text, loop, source)`.

Replace `speaker = current_speaker` (line 609) with:
```python
    speaker = source.speaker
    color = source.color
    source_id = source.id
```

Add `"color": color, "source_id": source_id` to all broadcast messages in this function.

- [ ] **Step 3: Refactor _buffer_audio_loop to accept source**

Change signature to `_buffer_audio_loop(transcribe_fn, loop, source)`.

Key changes:
- Read from `source.queue` instead of `audio_queue` (line 320)
- Pass `source` to `_check_speaker_change` (line 331)
- Use `source.speaker` instead of `current_speaker` in interim broadcasts (line 350)
- Add `"color": source.color, "source_id": source.id` to interim messages
- Pass `source` to `_broadcast_final` (line 360)

- [ ] **Step 4: Add shared transcription worker**

For Whisper/MLX, instead of calling `transcribe_fn(buf)` directly in the buffer loop, submit to the shared queue:

```python
def _transcription_worker(transcribe_fn, loop):
    """Single worker that processes transcription requests from all sources."""
    while not shutdown_event.is_set():
        try:
            source, buf, seg_start = _transcription_queue.get(timeout=0.5)
            if not source.active:
                continue
            text = transcribe_fn(buf)
            if text and text.strip():
                _broadcast_final(text.strip(), loop, source)
        except queue.Empty:
            continue
        except Exception as e:
            log.error(f"Transcription error: {e}")
```

Update `_buffer_audio_loop` to submit to `_transcription_queue` instead of calling `transcribe_fn` directly:
```python
# Instead of: text = transcribe_fn(buf)
_transcription_queue.put((source, buf.copy(), seg_start))
```

- [ ] **Step 5: Update WhisperBackend.process_audio_loop**

Change `process_audio_loop` (line 400-401) to start per-source buffer loops + shared worker:

```python
    def process_audio_loop(self, loop):
        # Start transcription worker (single thread, shared across sources)
        threading.Thread(target=_transcription_worker,
                         args=(self._transcribe, loop), daemon=True).start()
        # Start buffer loops for all sources
        with _sources_lock:
            for src in _sources:
                t = threading.Thread(target=_buffer_audio_loop,
                                     args=(self._transcribe, loop, src), daemon=True)
                t.start()
                src.buffer_thread = t
```

- [ ] **Step 6: Commit**

```bash
git add server.py
git commit -m "[feat] per-source buffer loops with shared transcription queue"
```

---

### Task 4: Vosk Per-Source Recognizer

**Files:**
- Modify: `server.py:454-507` (VoskBackend.process_audio_loop)

- [ ] **Step 1: Refactor VoskBackend for multi-source**

Replace the single `process_audio_loop` with a per-source version. Each source gets its own `KaldiRecognizer`:

```python
    def process_audio_loop(self, loop):
        """Start a Vosk processing loop for each audio source."""
        with _sources_lock:
            for src in _sources:
                t = threading.Thread(target=self._vosk_source_loop,
                                     args=(loop, src), daemon=True)
                t.start()
                src.buffer_thread = t

    def _vosk_source_loop(self, loop, source):
        """Vosk streaming loop for a single source."""
        rec = vosk.KaldiRecognizer(self._model, SAMPLE_RATE)
        # ... same logic as current process_audio_loop but:
        #   - read from source.queue instead of audio_queue
        #   - use source.speaker instead of current_speaker
        #   - use source.speaker_change_pending instead of _speaker_change_pending
        #   - pass source to _broadcast_final
        #   - add source_id and color to interim messages
```

Port the full current VoskBackend.process_audio_loop (lines 454-507) into `_vosk_source_loop`, replacing all global references with per-source state.

- [ ] **Step 2: Commit**

```bash
git add server.py
git commit -m "[feat] Vosk per-source KaldiRecognizer instances"
```

---

### Task 5: CLI, Startup, and Source Management API

**Files:**
- Modify: `server.py:1334-1350` (argparse)
- Modify: `server.py:1238-1252` (startup)
- Add new API endpoints

- [ ] **Step 1: Add --sources CLI argument**

At line 1340, change:
```python
parser.add_argument("--mic", type=int, default=None)
```
To:
```python
parser.add_argument("--mic", type=int, default=None, help="(deprecated) Single mic index")
parser.add_argument("--sources", type=str, default=None,
                    help="Comma-separated device indices (-1 for default)")
```

- [ ] **Step 2: Parse sources and create AudioSource objects at startup**

In the startup section (around line 1350), after args are parsed:

```python
    # Parse audio sources
    if args.sources:
        for idx_str in args.sources.split(","):
            idx = int(idx_str.strip())
            dev = None if idx == -1 else idx
            add_source(dev)
    elif args.mic is not None:
        add_source(args.mic)
    else:
        add_source(None)  # system default
```

- [ ] **Step 3: Update startup thread launch**

Replace lines 1243-1245:
```python
threading.Thread(target=stt_backend.process_audio_loop, args=(loop,), daemon=True).start()
mic = getattr(app.state, "mic_index", None)
threading.Thread(target=start_audio_capture, args=(mic,), daemon=True).start()
```

With:
```python
# Start capture threads for all sources
with _sources_lock:
    for src in _sources:
        t = threading.Thread(target=start_source_capture, args=(src,), daemon=True)
        t.start()
        src.capture_thread = t
# Start processing (creates per-source buffer threads + shared worker)
threading.Thread(target=stt_backend.process_audio_loop, args=(loop,), daemon=True).start()
```

- [ ] **Step 4: Add source management API endpoints**

Add to the operator app:

```python
@operator_app.post("/api/sources/add")
async def api_add_source(request: Request):
    data = await request.json()
    dev_idx = data.get("device_index")
    name = data.get("name")
    src = add_source(dev_idx, name)
    if not src:
        return JSONResponse({"error": "Maximum 8 sources"}, 400)
    # Start capture and buffer threads
    loop = asyncio.get_event_loop()
    t = threading.Thread(target=start_source_capture, args=(src,), daemon=True)
    t.start()
    src.capture_thread = t
    # Start buffer thread based on backend type
    # (implementation depends on backend — Whisper vs Vosk)
    await broadcast_all({"type": "source_added", "source": {
        "id": src.id, "name": src.name, "speaker": src.speaker, "color": src.color}})
    return JSONResponse({"id": src.id, "name": src.name})

@operator_app.post("/api/sources/remove")
async def api_remove_source(request: Request):
    data = await request.json()
    source_id = data.get("source_id")
    if remove_source(source_id):
        await broadcast_all({"type": "source_removed", "source_id": source_id})
        return JSONResponse({"ok": True})
    return JSONResponse({"error": "Source not found"}, 404)

@operator_app.get("/api/sources")
async def api_list_sources():
    with _sources_lock:
        return JSONResponse([{
            "id": s.id, "name": s.name, "speaker": s.speaker,
            "color": s.color, "device_index": s.device_index
        } for s in _sources])
```

- [ ] **Step 5: Update set_speaker WebSocket handler**

Change the set_speaker handler (line 1121-1126) to accept `source_id`:

```python
elif msg.get("type") == "set_speaker":
    new_name = msg.get("speaker", "")
    source_id = msg.get("source_id", 0)
    color = msg.get("color", "")
    src = get_source(source_id)
    if src:
        with src.speaker_lock:
            src.speaker_change_pending = {"name": new_name, "time": time.time()}
        if color:
            src.color = color
        await broadcast_all({"type": "speaker_change", "speaker": new_name,
                             "color": color, "source_id": source_id})
```

- [ ] **Step 6: Send source_list on WebSocket connect**

In the WebSocket connect handler, after sending initial state, add:

```python
with _sources_lock:
    source_list = [{"id": s.id, "name": s.name, "speaker": s.speaker,
                    "color": s.color} for s in _sources]
await ws.send_json({"type": "source_list", "sources": source_list})
```

- [ ] **Step 7: Commit**

```bash
git add server.py
git commit -m "[feat] --sources CLI, source management API, per-source speaker WebSocket protocol"
```

---

### Task 6: Launcher Multi-Source UI

**Files:**
- Modify: `launcher.pyw:63-75` (DEFAULT_SETTINGS)
- Modify: `launcher.pyw:357-366` (mic UI section)
- Modify: `launcher.pyw:441-465` (_refresh_mics, _get_selected_mic_index)
- Modify: `launcher.pyw:497-516` (_build_server_cmd)

- [ ] **Step 1: Update settings**

In DEFAULT_SETTINGS, change `"mic_index": None` to `"source_indices": [-1]` (list with system default).

Add backward compat in `load_settings()`: if `mic_index` exists but `source_indices` doesn't, migrate.

- [ ] **Step 2: Add loopback detection to list_mics**

Update `list_mics()` (lines 100-111) to return a `is_loopback` flag:

```python
def list_mics():
    try:
        import sounddevice as sd
        devices = sd.query_devices()
        mics = []
        for i, d in enumerate(devices):
            if d.get("max_input_channels", 0) > 0:
                name = d["name"]
                is_loopback = any(kw in name.lower() for kw in
                    ["loopback", "stereo mix", "what u hear", "wasapi"])
                mics.append((i, name, is_loopback))
        return mics
    except Exception:
        return []
```

- [ ] **Step 3: Replace single mic dropdown with multi-source UI**

Replace the microphone section (lines 357-366) with a dynamic source list:

```python
        # Audio Sources
        ttk.Label(settings_frame, text="Audio Sources:",
                  style="Section.TLabel").pack(anchor="w")
        self._source_frames = []  # list of (frame, combo, var) tuples
        self._sources_container = ttk.Frame(settings_frame)
        self._sources_container.pack(fill="x", pady=(2, 4))

        # Initialize from settings
        for idx in self.settings.get("source_indices", [-1]):
            self._add_source_row(idx)

        self._add_source_btn = ttk.Button(settings_frame, text="+ Add Source",
                                           command=lambda: self._add_source_row())
        self._add_source_btn.pack(fill="x", pady=(0, 8))
```

- [ ] **Step 4: Implement _add_source_row and _remove_source_row**

```python
    def _add_source_row(self, device_index=None):
        if len(self._source_frames) >= 8:
            return
        row = ttk.Frame(self._sources_container)
        row.pack(fill="x", pady=1)

        lbl = ttk.Label(row, text=f"Source {len(self._source_frames) + 1}:",
                         style="Subtitle.TLabel", width=8)
        lbl.pack(side="left")

        var = tk.StringVar(value="System Default")
        combo = ttk.Combobox(row, textvariable=var, state="readonly",
                              font=("Segoe UI", 10))
        combo.pack(side="left", fill="x", expand=True, padx=(4, 4))
        combo.bind("<ButtonPress-1>", lambda e, c=combo: self._refresh_source_combo(c))

        if len(self._source_frames) > 0:  # Don't allow removing Source 1
            rm_btn = ttk.Button(row, text="X", width=3,
                                 command=lambda r=row: self._remove_source_row(r))
            rm_btn.pack(side="right")

        self._source_frames.append((row, combo, var))
        self._refresh_source_combo(combo)

        # Select the specified device
        if device_index is not None and device_index != -1:
            mics = list_mics()
            for j, (i, name, _) in enumerate(mics):
                if i == device_index:
                    combo.current(j + 1)  # +1 for "System Default"
                    break

        self._update_add_button()

    def _remove_source_row(self, row):
        self._source_frames = [(r, c, v) for r, c, v in self._source_frames if r != row]
        row.destroy()
        self._update_add_button()

    def _update_add_button(self):
        if len(self._source_frames) >= 8:
            self._add_source_btn.pack_forget()
        else:
            self._add_source_btn.pack(fill="x", pady=(0, 8))
```

- [ ] **Step 5: Implement grouped dropdown refresh**

```python
    def _refresh_source_combo(self, combo):
        mics = list_mics()
        physical = [f"[{i}] {n}" for i, n, lb in mics if not lb]
        loopback = [f"[{i}] {n}" for i, n, lb in mics if lb]
        values = ["System Default"]
        if physical:
            values.extend(physical)
        if loopback:
            values.append("── System Audio ──")
            values.extend(loopback)
        else:
            values.append("── No system audio devices found ──")
        combo["values"] = values
        self._mic_devices = mics  # store for index lookup
```

- [ ] **Step 6: Update _build_server_cmd for --sources**

Replace the mic section in `_build_server_cmd` (lines 506-509) with:

```python
        # Audio sources
        indices = []
        for _, combo, var in self._source_frames:
            sel = combo.current()
            if sel <= 0:
                indices.append("-1")
            else:
                # Find the actual device index from the combo selection
                text = var.get()
                for i, name, _ in self._mic_devices:
                    if f"[{i}] {name}" == text:
                        indices.append(str(i))
                        break
        if indices:
            cmd.extend(["--sources", ",".join(indices)])
```

- [ ] **Step 7: Update _save_current_settings**

Save `source_indices` instead of `mic_index`:

```python
        indices = []
        for _, combo, var in self._source_frames:
            sel = combo.current()
            if sel <= 0:
                indices.append(-1)
            else:
                text = var.get()
                for i, name, _ in self._mic_devices:
                    if f"[{i}] {name}" == text:
                        indices.append(i)
                        break
        self.settings["source_indices"] = indices
```

- [ ] **Step 8: Commit**

```bash
git add launcher.pyw
git commit -m "[feat] multi-source UI with add/remove rows and grouped device dropdowns"
```

---

### Task 7: Operator Panel — Source Rows + Expanded Speakers

**Files:**
- Modify: `operator.html:170-175` (speaker section HTML)
- Modify: `operator.html:735-766` (speaker button generation + setSp)
- Modify: `operator.html:1085-1143` (WebSocket handler)
- Modify: `operator.html:1153-1161` (keyboard handlers)

- [ ] **Step 1: Add source list HTML section**

Above the existing speaker grid, add a source list container:

```html
<div class="section-label">Audio Sources</div>
<div id="sourceList"></div>
<div class="section-label">Speakers</div>
<div class="spgrid" id="spGrid"></div>
```

- [ ] **Step 2: Add source list rendering JavaScript**

```javascript
let sources = [];  // [{id, name, speaker, color}]
let focusedSourceId = 0;

function buildSourceList() {
    const el = document.getElementById('sourceList');
    let h = '';
    sources.forEach(s => {
        const focused = s.id === focusedSourceId ? ' source-focused' : '';
        const col = s.color || currentTextColor;
        h += `<div class="source-row${focused}" onclick="focusSource(${s.id})">
            <span class="color-swatch" style="background:${col}"
                  onclick="event.stopPropagation();openColorPicker(${s.id})"></span>
            <input class="source-name" value="${esc(s.speaker || s.name)}"
                   onchange="renameSource(${s.id}, this.value)">
            <span class="source-device">${esc(s.name)}</span>
        </div>`;
    });
    el.innerHTML = h;
}

function focusSource(id) {
    focusedSourceId = id;
    buildSourceList();
}
```

- [ ] **Step 3: Expand speaker buttons to 50**

Replace the speaker button generation (lines 735-753) to support up to 50:

```javascript
function buildSpeakerBtns(){
    const el=document.getElementById('spGrid');
    let h=`<button class="spbtn spwide${activeSp===0?' act':''}" onclick="setSp(0)">No Label<span class="hk">0</span></button>`;
    speakers.forEach((n,i)=>{
        const idx=i+1;
        const hk = idx <= 9 ? `<span class="hk">${idx}</span>` : '';
        h+=`<button class="spbtn${activeSp===idx?' act':''}" onclick="setSp(${idx})">${esc(n)}${hk}</button>`;
    });
    // Show one empty "+" slot if room remains
    if(speakers.length < 50){
        h+=`<button class="spbtn spbtn-add" onclick="document.getElementById('iNewSp').focus()">+</button>`;
    }
    el.innerHTML=h;
}
```

- [ ] **Step 4: Update setSp to include source_id**

```javascript
function setSp(idx){
    if(editingSpeakerIdx>0) return;
    activeSp=idx;
    const name=(idx>0&&idx<=speakers.length)?speakers[idx-1]:'';
    document.getElementById('pSpeaker').textContent=name;
    buildSpeakerBtns();
    if(ws&&ws.readyState===1)ws.send(JSON.stringify({
        type:'set_speaker', speaker:name, source_id:focusedSourceId
    }));
}
```

- [ ] **Step 5: Handle source_list, source_added, source_removed messages**

In the WebSocket message handler:

```javascript
else if(m.type==='source_list'){
    sources = m.sources;
    if(sources.length > 0) focusedSourceId = sources[0].id;
    buildSourceList();
}
else if(m.type==='source_added'){
    sources.push(m.source);
    buildSourceList();
}
else if(m.type==='source_removed'){
    sources = sources.filter(s => s.id !== m.source_id);
    if(focusedSourceId === m.source_id && sources.length > 0)
        focusedSourceId = sources[0].id;
    buildSourceList();
}
```

- [ ] **Step 6: Update interim/final handlers for source_id and color**

Update existing handlers to pass `m.color` and `m.source_id` through to display functions.

- [ ] **Step 7: Add Reset Speakers button with 10s Yes/No confirmation**

```javascript
function resetSpeakers(){
    const container = document.getElementById('resetContainer');
    container.innerHTML = `<span>Confirm Reset?</span>
        <button class="btn btn-danger" onclick="confirmReset()">Yes</button>
        <button class="btn" onclick="cancelReset()">No</button>`;
    resetTimeout = setTimeout(cancelReset, 10000);
}
function confirmReset(){
    clearTimeout(resetTimeout);
    // Reset all speaker names and colors via API
    fetch('/api/speakers/reset', {method:'POST'});
    cancelReset();
}
function cancelReset(){
    clearTimeout(resetTimeout);
    document.getElementById('resetContainer').innerHTML =
        `<button class="btn" onclick="resetSpeakers()">Reset Speakers</button>`;
}
```

- [ ] **Step 8: Add CSS for source rows and focused state**

```css
.source-row { display:flex; align-items:center; padding:6px 8px; border:2px solid transparent;
              border-radius:6px; margin:2px 0; cursor:pointer; }
.source-focused { border-color: var(--accent); background: rgba(79,195,247,0.1); }
.color-swatch { width:20px; height:20px; border-radius:4px; border:1px solid #666;
                cursor:pointer; margin-right:8px; flex-shrink:0; }
.source-name { background:transparent; border:1px solid #444; color:inherit;
               border-radius:4px; padding:2px 6px; flex:1; }
.source-device { color:#888; font-size:0.85em; margin-left:8px; }
```

- [ ] **Step 9: Commit**

```bash
git add operator.html
git commit -m "[feat] source-speaker rows, 50-speaker grid, reset confirmation, source_id in messages"
```

---

### Task 8: Color Picker (Operator Panel)

**Files:**
- Modify: `operator.html` — add color picker popup

- [ ] **Step 1: Add color picker HTML**

```html
<div id="colorPicker" class="color-picker-overlay" style="display:none">
  <div class="color-picker-popup">
    <div class="color-presets" id="colorPresets"></div>
    <canvas id="cpSquare" width="256" height="256"></canvas>
    <canvas id="cpHue" width="256" height="20"></canvas>
    <div class="cp-buttons">
      <button class="btn" onclick="resetColor()">Reset</button>
      <button class="btn btn-g" onclick="applyColor()">Done</button>
    </div>
  </div>
</div>
```

- [ ] **Step 2: Implement 32 preset colors**

```javascript
function generatePresets(bgColor, textColor){
    // Generate 32 evenly-spaced hues, filter for WCAG AA contrast (4.5:1)
    const presets = [];
    for(let i=0; i<32; i++){
        const hue = (i * 360 / 32) % 360;
        const color = hslToHex(hue, 80, 55);
        if(getContrastRatio(color, bgColor) >= 4.5) presets.push(color);
    }
    // Fill remaining slots with adjusted lightness
    while(presets.length < 32){
        const hue = (presets.length * 360 / 32) % 360;
        presets.push(hslToHex(hue, 70, 65));
    }
    return presets.slice(0, 32);
}
```

- [ ] **Step 3: Implement HSB square + hue slider**

```javascript
let cpSourceId = -1, cpHue = 0, cpSat = 100, cpBri = 100;

function openColorPicker(sourceId){
    cpSourceId = sourceId;
    document.getElementById('colorPicker').style.display = 'flex';
    renderPresets();
    drawHueBar();
    drawSBSquare();
}

function drawHueBar(){
    const c = document.getElementById('cpHue');
    const ctx = c.getContext('2d');
    const grad = ctx.createLinearGradient(0, 0, c.width, 0);
    for(let i=0; i<=360; i+=60) grad.addColorStop(i/360, `hsl(${i},100%,50%)`);
    ctx.fillStyle = grad;
    ctx.fillRect(0, 0, c.width, c.height);
}

function drawSBSquare(){
    const c = document.getElementById('cpSquare');
    const ctx = c.getContext('2d');
    // Fill with current hue
    ctx.fillStyle = `hsl(${cpHue},100%,50%)`;
    ctx.fillRect(0, 0, c.width, c.height);
    // White gradient left to right
    const gradW = ctx.createLinearGradient(0, 0, c.width, 0);
    gradW.addColorStop(0, 'rgba(255,255,255,1)');
    gradW.addColorStop(1, 'rgba(255,255,255,0)');
    ctx.fillStyle = gradW; ctx.fillRect(0, 0, c.width, c.height);
    // Black gradient top to bottom
    const gradB = ctx.createLinearGradient(0, 0, 0, c.height);
    gradB.addColorStop(0, 'rgba(0,0,0,0)');
    gradB.addColorStop(1, 'rgba(0,0,0,1)');
    ctx.fillStyle = gradB; ctx.fillRect(0, 0, c.width, c.height);
}
```

Add mouse handlers for both canvases to update `cpHue`/`cpSat`/`cpBri` and send color updates.

- [ ] **Step 4: Add CSS for color picker**

```css
.color-picker-overlay { position:fixed; top:0; left:0; width:100%; height:100%;
    background:rgba(0,0,0,0.5); display:flex; align-items:center; justify-content:center; z-index:1000; }
.color-picker-popup { background:#1a1a2e; border-radius:12px; padding:16px; }
.color-presets { display:grid; grid-template-columns:repeat(8,1fr); gap:4px; margin-bottom:12px; }
.color-presets span { width:24px; height:24px; border-radius:4px; cursor:pointer; border:2px solid transparent; }
.color-presets span:hover { border-color:#fff; }
#cpSquare { border-radius:4px; cursor:crosshair; margin-bottom:8px; display:block; }
#cpHue { border-radius:10px; cursor:pointer; display:block; margin-bottom:12px; }
.cp-buttons { display:flex; gap:8px; justify-content:flex-end; }
```

- [ ] **Step 5: Commit**

```bash
git add operator.html
git commit -m "[feat] color picker with 32 high-contrast presets and HSB square+hue slider"
```

---

### Task 9: Display Pages — Colored Speaker Labels

**Files:**
- Modify: `display.html:87-110` (addFinalLine speaker tag)
- Modify: `display.html:132-150` (setInterim speaker tag)
- Modify: `display.html:176-223` (WebSocket handler)
- Modify: `dictation.html` (same pattern)

- [ ] **Step 1: Update display.html speaker tag rendering**

In `addFinalLine` (line 97), change:
```javascript
tag.style.color=s.color;
```
To use the speaker's color from the message:
```javascript
tag.style.color = speakerColor || s.color;
```

Update function signatures to accept `color` parameter:
```javascript
function addFinalLine(slotId, text, speaker, lineId, color) {
    // ... existing code ...
    if(speaker && speaker !== s.lastSpeaker){
        const tag=document.createElement('span');
        tag.className='speaker-tag';
        tag.textContent=speaker+': ';
        tag.style.color = color || s.color;  // use speaker color, fall back to slot color
        line.appendChild(tag);
        s.lastSpeaker=speaker;
    }
```

Do the same for `setInterim`.

- [ ] **Step 2: Update WebSocket handler to pass color**

In the message handler:
```javascript
case 'final':
    if(msg.text) addFinalLine('caption', msg.text, msg.speaker||'', msg.line_id, msg.color);
    break;
case 'interim':
    setInterim('caption', msg.text||'', msg.speaker||'', msg.color);
    break;
case 'final_translation':
    if(msg.translated) addFinalLine(k, msg.translated, msg.speaker||'', msg.line_id, msg.color);
    break;
case 'interim_translation':
    setInterim(k, msg.translated||'', msg.speaker||'', msg.color);
    break;
```

- [ ] **Step 3: Apply same changes to dictation.html**

Add `color` parameter handling to dictation.html's message handler.

- [ ] **Step 4: Commit**

```bash
git add display.html dictation.html
git commit -m "[feat] colored speaker name labels from per-source color"
```

---

### Task 10: Speaker Persistence

**Files:**
- Modify: `server.py` — save/load speaker config in config.json

- [ ] **Step 1: Add save/load speaker config**

```python
def _save_speaker_config():
    """Save speaker names, colors, assignments to config."""
    with _sources_lock:
        speaker_config = {}
        for s in _sources:
            key = str(s.device_index) if s.device_index is not None else "default"
            speaker_config[key] = {
                "name": s.name, "speaker": s.speaker, "color": s.color
            }
    config["speaker_config"] = speaker_config
    _save_config()

def _load_speaker_config():
    """Restore speaker names, colors from config after sources are created."""
    sc = config.get("speaker_config", {})
    with _sources_lock:
        for s in _sources:
            key = str(s.device_index) if s.device_index is not None else "default"
            if key in sc:
                s.name = sc[key].get("name", s.name)
                s.speaker = sc[key].get("speaker", s.speaker)
                s.color = sc[key].get("color", "")
```

Call `_load_speaker_config()` after sources are created at startup. Call `_save_speaker_config()` when speakers are renamed/recolored.

- [ ] **Step 2: Add /api/speakers/reset endpoint**

```python
@operator_app.post("/api/speakers/reset")
async def api_reset_speakers():
    with _sources_lock:
        for s in _sources:
            s.speaker = ""
            s.color = ""
    _save_speaker_config()
    await broadcast_all({"type": "speakers_reset"})
    return JSONResponse({"ok": True})
```

- [ ] **Step 3: Commit**

```bash
git add server.py
git commit -m "[feat] persist speaker names and colors across restarts"
```

---

### Task 11: Integration Verification

- [ ] **Step 1: Syntax check all modified files**

```bash
python -c "import ast; [ast.parse(open(f, encoding='utf-8').read()) for f in ['server.py','launcher.pyw']]"
```

- [ ] **Step 2: Verify git log**

```bash
git log --oneline -12
```

Expected: 10 clean commits for tasks 1-10.

- [ ] **Step 3: Manual smoke test**

1. Launch with single source (backward compat): `python server.py` — should work as before
2. Launch with multiple sources: `python server.py --sources -1,3` — should create 2 AudioSource objects
3. Launcher: add/remove source rows, verify grouped dropdowns
4. Operator panel: focus sources, assign speakers, test color picker
5. Display: verify colored speaker labels appear
