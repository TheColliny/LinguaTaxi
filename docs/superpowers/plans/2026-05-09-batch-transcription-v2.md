# Batch Transcription V2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enhance the batch transcription dialog with translation engine selection, folder batch processing, text file translation, and output directory control.

**Architecture:** The batch dialog owns its own translation config (independent of the operator panel). The launcher sends a JSON body to an enhanced server endpoint, which routes to the appropriate function in `transcribe_file.py` based on file type (audio vs text) and selection type (file vs folder). All tkinter widget updates happen on the main thread via `dlg.after()` polling.

**Tech Stack:** Python 3.10, FastAPI/Pydantic, customtkinter, urllib.request (JSON POST)

---

## File Structure

| File | Role | Change |
|------|------|--------|
| `transcribe_file.py` | Batch processing logic | Add `batch_translate_text()`, `batch_folder()`, enhance progress state |
| `server.py` | API endpoints | Rewrite batch endpoint to accept JSON body, route by file/folder type |
| `launcher.pyw` | Desktop GUI | Complete dialog rewrite with engine picker, language slots, folder picker, output dir |

---

### Task 1: Text file translation + sanitization (`transcribe_file.py`)

**Files:**
- Modify: `transcribe_file.py:1-12` (add constants after imports)
- Modify: `transcribe_file.py:108-126` (add new functions after `segment_audio`)

- [ ] **Step 1: Add file-type constants and sanitization helper after the imports block**

At the top of `transcribe_file.py`, after line 7 (`from pathlib import Path`), add:

```python
import re

AUDIO_EXTS = {".wav", ".mp3", ".flac", ".m4a", ".ogg", ".webm"}
TEXT_EXTS = {".txt", ".srt", ".vtt", ".md"}
MAX_TEXT_FILE_SIZE = 10 * 1024 * 1024  # 10MB
MAX_LINE_LENGTH = 5000


def _sanitize_text_line(line):
    """Strip null bytes and control characters (keep newlines/tabs), cap length."""
    line = line.replace("\x00", "")
    line = re.sub(r"[\x01-\x08\x0b\x0c\x0e-\x1f\x7f]", "", line)
    return line[:MAX_LINE_LENGTH]
```

- [ ] **Step 2: Add `batch_translate_text()` function**

Insert after the `segment_audio()` function (after line 108) and before the progress state section:

```python
def batch_translate_text(file_path, translate_fn, translations,
                         output_dir, source_lang, progress_callback=None):
    """Translate a text file to multiple languages.
    Returns {lines, languages, output_dir} or None on error."""
    p = Path(file_path)
    _set_progress("processing", 0, f"Reading {p.name}...")

    if p.stat().st_size > MAX_TEXT_FILE_SIZE:
        _set_progress("error", 0, f"Skipped {p.name} (exceeds 10MB limit)")
        return None

    try:
        raw = p.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        _set_progress("error", 0, f"Cannot read {p.name}: {e}")
        return None

    lines = raw.splitlines()
    if not lines:
        _set_progress("error", 0, f"Skipped {p.name} (empty file)")
        return None

    lines = [_sanitize_text_line(l) for l in lines]
    lines = [l for l in lines if l.strip()]
    if not lines:
        _set_progress("error", 0, f"Skipped {p.name} (no text after sanitization)")
        return None

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    stem = p.stem
    ext = p.suffix
    languages = []
    total_slots = len(translations)

    for slot_idx, t in enumerate(translations):
        tgt_lang = t["lang"]
        mode = t.get("mode", "deepl")
        tgt_base = tgt_lang.split("-")[0]
        src_base = source_lang.split("-")[0]

        pct = int(100 * slot_idx / max(total_slots, 1))
        _set_progress("processing", pct, f"Translating to {tgt_lang}...")
        if progress_callback:
            progress_callback(pct, f"Translating to {tgt_lang}")

        out_name = f"{stem}_{tgt_lang}{ext}"
        with open(out / out_name, "w", encoding="utf-8") as f:
            for line in lines:
                if src_base == tgt_base:
                    translated = line
                else:
                    translated = translate_fn(line, tgt_lang, source_lang, mode)
                f.write(translated + "\n")
        languages.append(tgt_lang)

    _set_progress("done", 100, f"Translated {len(lines)} lines to {len(languages)} language(s)")
    return {
        "lines": len(lines),
        "languages": languages,
        "output_dir": str(out),
    }
```

- [ ] **Step 3: Commit**

```bash
git add transcribe_file.py
git commit -m "[feat] add batch_translate_text with input sanitization for text files"
```

---

### Task 2: Enhanced progress tracking + `batch_folder()` (`transcribe_file.py`)

**Files:**
- Modify: `transcribe_file.py` — progress state section (~line 112-126)
- Modify: `transcribe_file.py` — after `batch_translate_text()`

- [ ] **Step 1: Enhance the progress state dict and `_set_progress()`**

Replace the existing progress state block:

```python
# ── Progress state (read by server API) ──
_progress = {"status": "idle", "pct": 0, "message": ""}
_progress_lock = threading.Lock()


def get_progress():
    with _progress_lock:
        return dict(_progress)


def _set_progress(status, pct=0, message=""):
    with _progress_lock:
        _progress["status"] = status
        _progress["pct"] = pct
        _progress["message"] = message
```

With this enhanced version:

```python
# ── Progress state (read by server API) ──
_progress = {
    "status": "idle", "pct": 0, "message": "",
    "current_file": "", "files_done": 0, "files_total": 0,
}
_progress_lock = threading.Lock()


def get_progress():
    with _progress_lock:
        return dict(_progress)


def _set_progress(status, pct=0, message="", current_file="",
                  files_done=0, files_total=0):
    with _progress_lock:
        _progress["status"] = status
        _progress["pct"] = pct
        _progress["message"] = message
        _progress["current_file"] = current_file
        _progress["files_done"] = files_done
        _progress["files_total"] = files_total
```

- [ ] **Step 2: Add `batch_folder()` function**

Insert after `batch_translate_text()`, before the live playback section:

```python
def batch_folder(folder_path, recursive, stt_backend, translate_fn,
                 translations, output_dir, source_lang,
                 progress_callback=None):
    """Process all supported files in a folder.
    Returns {files_processed, files_skipped, total_lines, languages, output_dir}."""
    root = Path(folder_path)
    if not root.is_dir():
        _set_progress("error", 0, f"Not a directory: {folder_path}")
        return None

    # Collect supported files
    files = []
    pattern = root.rglob("*") if recursive else root.glob("*")
    for fp in sorted(pattern):
        if not fp.is_file():
            continue
        ext = fp.suffix.lower()
        if ext in AUDIO_EXTS or ext in TEXT_EXTS:
            files.append(fp)

    if not files:
        _set_progress("error", 0, "No audio or text files found in folder")
        return None

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    total = len(files)
    processed = 0
    skipped = 0
    total_lines = 0
    languages_set = set()

    for idx, fp in enumerate(files):
        ext = fp.suffix.lower()
        file_pct = int(100 * idx / total)
        _set_progress("processing", file_pct,
                      f"Processing {fp.name} ({idx + 1}/{total})",
                      current_file=fp.name, files_done=idx, files_total=total)

        if ext in TEXT_EXTS:
            if not translations:
                log.warning(f"Skipping text file {fp.name} — no translation configured")
                skipped += 1
                continue
            result = batch_translate_text(
                str(fp), translate_fn, translations,
                str(out), source_lang, progress_callback=None,
            )
            if result:
                total_lines += result["lines"]
                languages_set.update(result["languages"])
                processed += 1
            else:
                skipped += 1

        elif ext in AUDIO_EXTS:
            result = batch_transcribe(
                str(fp), stt_backend, translate_fn, translations,
                str(out), source_lang, progress_callback=None,
            )
            if result:
                total_lines += result["lines"]
                languages_set.update(result["languages"])
                processed += 1
            else:
                skipped += 1

    msg = f"Done: {processed} files processed"
    if skipped:
        msg += f", {skipped} skipped"
    _set_progress("done", 100, msg,
                  current_file="", files_done=total, files_total=total)
    return {
        "files_processed": processed,
        "files_skipped": skipped,
        "total_lines": total_lines,
        "languages": sorted(languages_set),
        "output_dir": str(out),
    }
```

- [ ] **Step 3: Commit**

```bash
git add transcribe_file.py
git commit -m "[feat] add batch_folder and enhanced progress tracking for folder processing"
```

---

### Task 3: Server API v2 — JSON batch endpoint (`server.py`)

**Files:**
- Modify: `server.py:49-63` (add imports)
- Modify: `server.py:2346-2440` (rewrite batch endpoint, update progress endpoint)

- [ ] **Step 1: Add Pydantic import**

After line 56 (`from fastapi.responses import FileResponse, HTMLResponse, JSONResponse`), add:

```python
from pydantic import BaseModel
```

After line 63 (the last existing import), add:

```python
from typing import List, Optional
```

- [ ] **Step 2: Add `BatchRequest` model**

Insert just before the `_file_transcribe_lock` line (before `# ── File transcription endpoints ──`):

```python
class BatchTranslationSlot(BaseModel):
    lang: str
    mode: str = "deepl"

class BatchRequest(BaseModel):
    file_path: Optional[str] = None
    folder_path: Optional[str] = None
    recursive: bool = False
    translations: List[BatchTranslationSlot] = []
    output_dir: Optional[str] = None
    source_lang: Optional[str] = None
```

- [ ] **Step 3: Rewrite the batch endpoint**

Replace the existing `o_transcribe_batch` function (lines 2350-2375) with:

```python
@operator_app.post("/api/transcribe-file/batch")
async def o_transcribe_batch(req: BatchRequest):
    """Start batch file transcription/translation in a background thread."""
    progress = transcribe_file.get_progress()
    if progress["status"] in ("processing", "playing"):
        return JSONResponse({"error": "File transcription already in progress"}, status_code=409)

    # Validate paths
    has_file = req.file_path and Path(req.file_path).exists()
    has_folder = req.folder_path and Path(req.folder_path).is_dir()
    if not has_file and not has_folder:
        return JSONResponse({"error": "File or folder not found"}, status_code=400)

    translations = [{"lang": t.lang, "mode": t.mode} for t in req.translations]
    src_lang = req.source_lang or config.get("input_lang", "EN")
    output_dir = req.output_dir or str(TRANSCRIPTS_DIR)

    def run():
        with _file_transcribe_lock:
            if has_folder:
                transcribe_file.batch_folder(
                    folder_path=req.folder_path,
                    recursive=req.recursive,
                    stt_backend=stt_backend,
                    translate_fn=translate_text,
                    translations=translations,
                    output_dir=output_dir,
                    source_lang=src_lang,
                )
            else:
                ext = Path(req.file_path).suffix.lower()
                if ext in transcribe_file.TEXT_EXTS:
                    if not translations:
                        transcribe_file._set_progress(
                            "error", 0,
                            "Text files require translation — select a language")
                        return
                    transcribe_file.batch_translate_text(
                        file_path=req.file_path,
                        translate_fn=translate_text,
                        translations=translations,
                        output_dir=output_dir,
                        source_lang=src_lang,
                    )
                else:
                    transcribe_file.batch_transcribe(
                        file_path=req.file_path,
                        stt_backend=stt_backend,
                        translate_fn=translate_text,
                        translations=translations,
                        transcripts_dir=output_dir,
                        source_lang=src_lang,
                    )

    threading.Thread(target=run, daemon=True).start()
    return JSONResponse({"status": "started"})
```

- [ ] **Step 4: Update the progress endpoint**

The progress endpoint at line 2437 already returns `transcribe_file.get_progress()` which now includes the new fields (`current_file`, `files_done`, `files_total`). No code change needed — verify it still works:

```python
@operator_app.get("/api/transcribe-file/progress")
async def o_transcribe_progress():
    """Get current file transcription progress."""
    return JSONResponse(transcribe_file.get_progress())
```

This automatically picks up the enhanced progress dict. No change required.

- [ ] **Step 5: Commit**

```bash
git add server.py
git commit -m "[feat] upgrade batch endpoint to JSON body with folder and text file routing"
```

---

### Task 4: Launcher dialog rewrite (`launcher.pyw`)

This is the largest task. The entire `_transcribe_file()` and `_show_transcribe_dialog()` methods are replaced. The dialog gains file/folder selection, translation engine picker, dynamic language slots, and output directory control.

**Files:**
- Modify: `launcher.pyw:1-12` (no change needed — `json`, `urllib`, `threading`, `messagebox`, `filedialog` already imported)
- Modify: `launcher.pyw:2213-2428` (replace `_transcribe_file` and `_show_transcribe_dialog`)

**Context the implementer needs:**
- `self.BG`, `self.BG3`, `self.FG`, `self.FG2`, `self.ACCENT`, `self.GREEN`, `self.RED` are color constants on the app class
- `self._server_running` is a bool flag
- `self.settings` is a dict with `"operator_port"` key (default 3001)
- The existing polling pattern uses: bg threads write to shared lists, main thread reads via `dlg.after(500, poll_progress)`
- `ctk` is `customtkinter`, `tk` is `tkinter`
- `messagebox` is `tkinter.messagebox` (already imported)
- `filedialog` is `tkinter.filedialog` (already imported)
- Available engines: `"none"`, `"deepl"`, `"offline-opus"`, `"offline-m2m"`
- The server's `/api/config` endpoint returns `input_lang` (e.g. `"EN"`)

- [ ] **Step 1: Add language list constants at module level**

Insert after the existing imports (after line 20, before the class definition). Find a blank area near the top of the file after all `import` statements:

```python
# ── Batch dialog language lists (per translation engine) ──
BATCH_DEEPL_LANGS = {
    "AR": "Arabic", "BG": "Bulgarian", "CS": "Czech", "DA": "Danish",
    "DE": "German", "EL": "Greek", "EN-GB": "English (UK)",
    "EN-US": "English (US)", "ES": "Spanish", "ET": "Estonian",
    "FI": "Finnish", "FR": "French", "HU": "Hungarian", "ID": "Indonesian",
    "IT": "Italian", "JA": "Japanese", "KO": "Korean", "LT": "Lithuanian",
    "LV": "Latvian", "NB": "Norwegian", "NL": "Dutch", "PL": "Polish",
    "PT-BR": "Portuguese (BR)", "PT-PT": "Portuguese (PT)", "RO": "Romanian",
    "RU": "Russian", "SK": "Slovak", "SL": "Slovenian", "SV": "Swedish",
    "TR": "Turkish", "UK": "Ukrainian", "ZH-HANS": "Chinese (Simplified)",
    "ZH-HANT": "Chinese (Traditional)",
}
BATCH_OPUS_LANGS = {
    "ES": "Spanish", "FR": "French", "DE": "German", "IT": "Italian",
    "NL": "Dutch", "RU": "Russian", "PL": "Polish", "SV": "Swedish",
    "DA": "Danish", "FI": "Finnish", "PT-BR": "Portuguese (BR)",
    "PT-PT": "Portuguese (PT)", "RO": "Romanian", "BG": "Bulgarian",
    "CS": "Czech", "ET": "Estonian", "HU": "Hungarian", "LT": "Lithuanian",
    "LV": "Latvian", "SK": "Slovak", "SL": "Slovenian", "EL": "Greek",
    "TR": "Turkish", "UK": "Ukrainian",
}
BATCH_M2M_LANGS = {
    "AR": "Arabic", "BG": "Bulgarian", "CS": "Czech", "DA": "Danish",
    "DE": "German", "EL": "Greek", "EN-GB": "English (UK)",
    "EN-US": "English (US)", "ES": "Spanish", "ET": "Estonian",
    "FI": "Finnish", "FR": "French", "HU": "Hungarian", "ID": "Indonesian",
    "IT": "Italian", "JA": "Japanese", "KO": "Korean", "LT": "Lithuanian",
    "LV": "Latvian", "NB": "Norwegian", "NL": "Dutch", "PL": "Polish",
    "PT-BR": "Portuguese (BR)", "PT-PT": "Portuguese (PT)", "RO": "Romanian",
    "RU": "Russian", "SK": "Slovak", "SL": "Slovenian", "SV": "Swedish",
    "TR": "Turkish", "UK": "Ukrainian", "ZH-HANS": "Chinese (Simplified)",
    "ZH-HANT": "Chinese (Traditional)",
}
BATCH_ENGINE_NAMES = {
    "none": "No Translation",
    "deepl": "DeepL (Online)",
    "offline-opus": "Offline (Language Specific)",
    "offline-m2m": "Offline (M2M 100+ Languages)",
}
```

- [ ] **Step 2: Replace `_transcribe_file()` method**

Replace lines 2213-2230 with:

```python
    def _transcribe_file(self):
        """Open the batch processing dialog."""
        if not self._server_running:
            return
        self._show_transcribe_dialog()
```

- [ ] **Step 3: Replace `_show_transcribe_dialog()` with the full new implementation**

Replace lines 2232-2428 (the entire `_show_transcribe_dialog` method) with the following. This is one large method — all widget updates stay on the main thread via `dlg.after()`.

```python
    def _show_transcribe_dialog(self):
        """Batch processing dialog with translation engine, language slots, folder support."""
        dlg = ctk.CTkToplevel(self)
        dlg.title("Batch Processing")
        dlg.geometry("460x520")
        dlg.resizable(False, False)
        dlg.configure(fg_color=self.BG)
        dlg.transient(self)
        dlg.grab_set()

        dlg.update_idletasks()
        px = self.winfo_x() + (self.winfo_width() - 460) // 2
        py = self.winfo_y() + (self.winfo_height() - 520) // 2
        dlg.geometry(f"+{px}+{py}")

        f = ctk.CTkFrame(dlg, fg_color="transparent")
        f.pack(fill="both", expand=True, padx=16, pady=12)

        # ── Shared state ──
        port = self.settings.get("operator_port", 3001)
        base_url = f"http://localhost:{port}"
        selection = {"file_path": None, "folder_path": None, "is_audio": False,
                     "is_text": False, "is_folder": False}
        output_dir_var = tk.StringVar(value="")
        source_lang = ["EN"]  # fetched from server

        # Fetch source language from server
        def _fetch_source_lang():
            try:
                resp = urllib.request.urlopen(f"{base_url}/api/config", timeout=3)
                cfg = json.loads(resp.read())
                source_lang[0] = cfg.get("input_lang", "EN")
            except Exception:
                pass
        threading.Thread(target=_fetch_source_lang, daemon=True).start()

        # ── File/Folder selection row ──
        sel_row = ctk.CTkFrame(f, fg_color="transparent")
        sel_row.pack(fill="x", pady=(0, 4))

        def pick_file():
            fp = filedialog.askopenfilename(
                title="Select File",
                filetypes=[
                    ("Supported Files",
                     "*.wav *.mp3 *.flac *.m4a *.ogg *.webm *.txt *.srt *.vtt *.md"),
                    ("Audio Files", "*.wav *.mp3 *.flac *.m4a *.ogg *.webm"),
                    ("Text Files", "*.txt *.srt *.vtt *.md"),
                    ("All Files", "*.*"),
                ])
            if not fp:
                return
            ext = Path(fp).suffix.lower()
            selection["file_path"] = fp
            selection["folder_path"] = None
            selection["is_folder"] = False
            selection["is_audio"] = ext in {".wav", ".mp3", ".flac", ".m4a", ".ogg", ".webm"}
            selection["is_text"] = ext in {".txt", ".srt", ".vtt", ".md"}
            output_dir_var.set(str(Path(fp).parent))
            _update_ui_for_selection()

        def pick_folder():
            dp = filedialog.askdirectory(title="Select Folder")
            if not dp:
                return
            selection["file_path"] = None
            selection["folder_path"] = dp
            selection["is_folder"] = True
            selection["is_audio"] = False
            selection["is_text"] = False
            output_dir_var.set(dp)
            _update_ui_for_selection()

        ctk.CTkButton(sel_row, text="Select File", width=130, height=30,
                      fg_color=self.ACCENT, hover_color="#81D4FA",
                      text_color="#000", font=("Segoe UI", 11),
                      command=pick_file).pack(side="left", padx=(0, 6))
        ctk.CTkButton(sel_row, text="Select Folder", width=130, height=30,
                      fg_color=self.ACCENT, hover_color="#81D4FA",
                      text_color="#000", font=("Segoe UI", 11),
                      command=pick_folder).pack(side="left")

        # Selected path display
        path_var = tk.StringVar(value="No file or folder selected")
        path_lbl = ctk.CTkLabel(f, textvariable=path_var,
                                font=("Segoe UI", 10), text_color=self.ACCENT,
                                wraplength=420, anchor="w", justify="left")
        path_lbl.pack(fill="x", pady=(2, 4))

        # Subfolder checkbox (hidden until folder selected)
        subfolder_var = tk.BooleanVar(value=False)
        subfolder_frame = ctk.CTkFrame(f, fg_color="transparent")
        ctk.CTkCheckBox(subfolder_frame, text="Include subfolders",
                        variable=subfolder_var, font=("Segoe UI", 11),
                        text_color=self.FG).pack(anchor="w")

        # ── Live playback checkbox (single audio file only) ──
        live_var = tk.BooleanVar(value=False)
        live_frame = ctk.CTkFrame(f, fg_color="transparent")
        ctk.CTkCheckBox(live_frame, text="Play as Live Input",
                        variable=live_var, font=("Segoe UI", 11),
                        text_color=self.FG,
                        command=lambda: _toggle_live_mode()).pack(anchor="w")

        # ── Translation engine dropdown ──
        engine_frame = ctk.CTkFrame(f, fg_color="transparent")
        engine_var = tk.StringVar(value="none")

        def _get_engine_options():
            opts = ["No Translation", "DeepL (Online)"]
            if source_lang[0].upper().startswith("EN"):
                opts.append("Offline (Language Specific)")
            opts.append("Offline (M2M 100+ Languages)")
            return opts

        def _engine_display_to_key(display):
            for k, v in BATCH_ENGINE_NAMES.items():
                if v == display:
                    return k
            return "none"

        def _engine_key_to_display(key):
            return BATCH_ENGINE_NAMES.get(key, "No Translation")

        ctk.CTkLabel(engine_frame, text="Translation Engine:",
                     font=("Segoe UI", 11, "bold"),
                     text_color=self.FG).pack(anchor="w", pady=(0, 2))

        engine_display_var = tk.StringVar(value="No Translation")
        engine_menu = ctk.CTkOptionMenu(
            engine_frame, variable=engine_display_var,
            values=_get_engine_options(),
            width=300, height=28,
            font=("Segoe UI", 11),
            command=lambda val: _on_engine_change(val))
        engine_menu.pack(anchor="w")

        # ── Language slots ──
        lang_frame = ctk.CTkFrame(f, fg_color="transparent")
        lang_rows = []  # list of {"frame": CTkFrame, "var": StringVar, "menu": CTkOptionMenu}

        def _get_lang_dict():
            key = engine_var.get()
            if key == "deepl":
                return BATCH_DEEPL_LANGS
            elif key == "offline-opus":
                return BATCH_OPUS_LANGS
            elif key == "offline-m2m":
                return BATCH_M2M_LANGS
            return {}

        def _available_display_values(exclude_var=None):
            """Language display strings not yet selected by other rows."""
            ld = _get_lang_dict()
            selected = set()
            for row in lang_rows:
                if row["var"] is not exclude_var:
                    val = row["var"].get()
                    if val:
                        for code, name in ld.items():
                            if f"{name} ({code})" == val:
                                selected.add(code)
                                break
            return [f"{name} ({code})" for code, name in sorted(ld.items(), key=lambda x: x[1])
                    if code not in selected]

        def _refresh_all_dropdowns():
            for row in lang_rows:
                avail = _available_display_values(exclude_var=row["var"])
                current = row["var"].get()
                if current and current not in avail:
                    avail.insert(0, current)
                row["menu"].configure(values=avail if avail else ["—"])

        def _on_lang_change(val):
            _refresh_all_dropdowns()

        def _add_lang_row(preset_display=None):
            row_frame = ctk.CTkFrame(lang_frame, fg_color="transparent")
            row_frame.pack(fill="x", pady=(0, 3))

            var = tk.StringVar(value="")
            avail = _available_display_values(exclude_var=var)
            if preset_display and preset_display in avail:
                var.set(preset_display)
            elif avail:
                var.set(avail[0])

            menu = ctk.CTkOptionMenu(
                row_frame, variable=var, values=avail if avail else ["—"],
                width=260, height=26, font=("Segoe UI", 10),
                command=lambda v: _on_lang_change(v))
            menu.pack(side="left", padx=(0, 4))

            def remove():
                row_frame.destroy()
                lang_rows[:] = [r for r in lang_rows if r["frame"] is not row_frame]
                _refresh_all_dropdowns()

            ctk.CTkButton(row_frame, text="x", width=26, height=26,
                          fg_color=self.RED, hover_color="#EF9A9A",
                          text_color="#fff", font=("Segoe UI", 10, "bold"),
                          command=remove).pack(side="left")

            entry = {"frame": row_frame, "var": var, "menu": menu}
            lang_rows.append(entry)
            _refresh_all_dropdowns()

        add_lang_btn = ctk.CTkButton(lang_frame, text="+  Add Language",
                                     width=120, height=26,
                                     fg_color=self.BG3, hover_color="#555",
                                     text_color="#fff", font=("Segoe UI", 10),
                                     command=lambda: _add_lang_row())

        def _on_engine_change(display_val):
            new_key = _engine_display_to_key(display_val)
            old_key = engine_var.get()

            if new_key == old_key:
                return

            new_dict = {}
            if new_key == "deepl":
                new_dict = BATCH_DEEPL_LANGS
            elif new_key == "offline-opus":
                new_dict = BATCH_OPUS_LANGS
            elif new_key == "offline-m2m":
                new_dict = BATCH_M2M_LANGS

            # Check compatibility of existing selections
            if lang_rows and new_key != "none":
                old_dict = _get_lang_dict()
                incompatible = []
                for row in lang_rows:
                    val = row["var"].get()
                    for code, name in old_dict.items():
                        if f"{name} ({code})" == val and code not in new_dict:
                            incompatible.append(f"{name} ({code})")
                            break

                if incompatible:
                    names = ", ".join(incompatible)
                    ok = messagebox.askyesno(
                        "Switch Translation Engine",
                        f"Switching to {display_val} will remove: {names}.\n\nContinue?",
                        parent=dlg)
                    if not ok:
                        engine_display_var.set(_engine_key_to_display(old_key))
                        return

            engine_var.set(new_key)

            # Rebuild lang rows — keep compatible, drop incompatible
            if new_key == "none":
                for row in lang_rows:
                    row["frame"].destroy()
                lang_rows.clear()
                lang_frame.pack_forget()
                add_lang_btn.pack_forget()
            else:
                kept = []
                for row in lang_rows:
                    val = row["var"].get()
                    found_code = None
                    old_dict_for_check = BATCH_DEEPL_LANGS if old_key == "deepl" else (
                        BATCH_OPUS_LANGS if old_key == "offline-opus" else BATCH_M2M_LANGS)
                    for code, name in old_dict_for_check.items():
                        if f"{name} ({code})" == val:
                            found_code = code
                            break
                    if found_code and found_code in new_dict:
                        kept.append(f"{new_dict[found_code]} ({found_code})")
                    row["frame"].destroy()
                lang_rows.clear()

                lang_frame.pack(fill="x", pady=(4, 4))
                for display in kept:
                    _add_lang_row(preset_display=display)
                if not kept:
                    _add_lang_row()
                add_lang_btn.pack(anchor="w", pady=(0, 4))

            _update_controls_visibility()

        # ── Output directory ──
        output_frame = ctk.CTkFrame(f, fg_color="transparent")
        ctk.CTkLabel(output_frame, text="Output Directory:",
                     font=("Segoe UI", 11, "bold"),
                     text_color=self.FG).pack(anchor="w", pady=(0, 2))

        out_row = ctk.CTkFrame(output_frame, fg_color="transparent")
        out_row.pack(fill="x")
        out_path_lbl = ctk.CTkLabel(out_row, textvariable=output_dir_var,
                                    font=("Segoe UI", 10), text_color=self.FG2,
                                    wraplength=320, anchor="w", justify="left")
        out_path_lbl.pack(side="left", fill="x", expand=True)

        def change_output():
            d = filedialog.askdirectory(title="Choose Output Directory",
                                        initialdir=output_dir_var.get() or None)
            if d:
                output_dir_var.set(d)

        ctk.CTkButton(out_row, text="Change...", width=80, height=26,
                      fg_color=self.BG3, hover_color="#555",
                      text_color="#fff", font=("Segoe UI", 10),
                      command=change_output).pack(side="right")

        # ── Status / progress ──
        status_var = tk.StringVar(value="")
        status_lbl = ctk.CTkLabel(f, textvariable=status_var,
                                  font=("Segoe UI", 10), text_color=self.FG2,
                                  wraplength=420)
        status_lbl.pack(anchor="w", pady=(6, 2))

        progress = ctk.CTkProgressBar(f, width=420, mode="determinate")
        progress.pack(pady=(0, 6))
        progress.set(0)

        # ── Buttons ──
        btn_frame = ctk.CTkFrame(f, fg_color="transparent")
        btn_frame.pack(fill="x", pady=(2, 0))

        # ── UI visibility helpers ──
        def _update_ui_for_selection():
            name = ""
            if selection["file_path"]:
                name = Path(selection["file_path"]).name
                if len(name) > 50:
                    name = name[:47] + "..."
            elif selection["folder_path"]:
                name = selection["folder_path"]
                if len(name) > 50:
                    name = "..." + name[-47:]
            path_var.set(name or "No file or folder selected")

            # Subfolder checkbox
            if selection["is_folder"]:
                subfolder_frame.pack(fill="x", pady=(0, 4))
            else:
                subfolder_frame.pack_forget()
                subfolder_var.set(False)

            # Live playback checkbox
            if selection["is_audio"] and not selection["is_folder"]:
                live_frame.pack(fill="x", pady=(0, 4))
            else:
                live_frame.pack_forget()
                live_var.set(False)

            _update_controls_visibility()
            start_btn.configure(state="normal")

        def _toggle_live_mode():
            _update_controls_visibility()

        def _update_controls_visibility():
            is_live = live_var.get()
            if is_live:
                engine_frame.pack_forget()
                lang_frame.pack_forget()
                add_lang_btn.pack_forget()
                output_frame.pack_forget()
            else:
                engine_frame.pack(fill="x", pady=(4, 4))
                if engine_var.get() != "none":
                    lang_frame.pack(fill="x", pady=(0, 4))
                    add_lang_btn.pack(anchor="w", pady=(0, 4))
                else:
                    lang_frame.pack_forget()
                    add_lang_btn.pack_forget()
                output_frame.pack(fill="x", pady=(4, 4))

        # ── Polling / network (thread-safe pattern) ──
        polling = [False]
        request_result = [None]
        poll_data = [None]
        started = [False]
        live_stop_btn = [None]

        def _send_batch_request():
            """Background thread: build JSON body, POST to server."""
            try:
                ld = _get_lang_dict()
                trans_list = []
                for row in lang_rows:
                    val = row["var"].get()
                    for code, name in ld.items():
                        if f"{name} ({code})" == val:
                            trans_list.append({"lang": code, "mode": engine_var.get()})
                            break

                body = {
                    "file_path": selection["file_path"],
                    "folder_path": selection["folder_path"],
                    "recursive": subfolder_var.get(),
                    "translations": trans_list,
                    "output_dir": output_dir_var.get() or None,
                    "source_lang": source_lang[0],
                }
                data = json.dumps(body).encode("utf-8")
                req = urllib.request.Request(
                    f"{base_url}/api/transcribe-file/batch",
                    data=data,
                    headers={"Content-Type": "application/json"})
                resp = urllib.request.urlopen(req, timeout=15)
                request_result[0] = json.loads(resp.read())
            except Exception as e:
                request_result[0] = {"error": str(e)}

        def _send_live_request():
            """Background thread: POST file path for live playback."""
            try:
                data = urllib.parse.urlencode(
                    {"file_path": selection["file_path"]}).encode()
                req = urllib.request.Request(
                    f"{base_url}/api/transcribe-file/live", data=data)
                resp = urllib.request.urlopen(req, timeout=10)
                request_result[0] = json.loads(resp.read())
            except Exception as e:
                request_result[0] = {"error": str(e)}

        def _fetch_progress():
            """Background thread: fetch progress JSON."""
            try:
                resp = urllib.request.urlopen(
                    f"{base_url}/api/transcribe-file/progress", timeout=3)
                poll_data[0] = json.loads(resp.read())
            except Exception:
                pass

        def stop_playback():
            if live_stop_btn[0]:
                live_stop_btn[0].configure(state="disabled")
            polling[0] = False
            def _send_stop():
                try:
                    req = urllib.request.Request(
                        f"{base_url}/api/transcribe-file/stop",
                        data=b"", method="POST")
                    urllib.request.urlopen(req, timeout=5)
                except Exception:
                    pass
            threading.Thread(target=_send_stop, daemon=True).start()
            status_var.set("Playback stopped, mic resumed")
            progress.set(0)
            dlg.after(1500, dlg.destroy)

        def poll_progress():
            """Main thread: read bg results, update widgets, schedule next."""
            if not polling[0]:
                return

            r = request_result[0]
            if r is not None and not started[0]:
                request_result[0] = None
                if "error" in r:
                    polling[0] = False
                    progress.stop()
                    progress.configure(mode="determinate")
                    progress.set(0)
                    status_var.set(f"Error: {r['error']}")
                    start_btn.configure(state="normal")
                    cancel_btn.configure(text="Close")
                    return
                started[0] = True
                progress.stop()
                progress.configure(mode="determinate")
                cancel_btn.configure(text="Close")
                if live_var.get():
                    start_btn.pack_forget()
                    sb = ctk.CTkButton(btn_frame, text="Stop Playback",
                                       fg_color=self.RED, hover_color="#EF9A9A",
                                       text_color="#fff",
                                       font=("Segoe UI", 11, "bold"),
                                       height=34, command=stop_playback)
                    sb.pack(side="left", expand=True, fill="x", padx=(0, 4))
                    live_stop_btn[0] = sb

            p = poll_data[0]
            if p is not None:
                poll_data[0] = None
                msg = p.get("message", "")
                cf = p.get("current_file", "")
                fd = p.get("files_done", 0)
                ft = p.get("files_total", 0)
                if cf and ft > 1:
                    msg = f"[{fd + 1}/{ft}] {cf}: {msg}"
                status_var.set(msg)
                progress.set(p.get("pct", 0) / 100.0)

                if p["status"] == "done":
                    polling[0] = False
                    progress.set(1.0)
                    status_var.set(p.get("message", "Complete"))
                    cancel_btn.configure(text="Close")
                    out_dir = output_dir_var.get()
                    if out_dir and not live_var.get():
                        open_btn = ctk.CTkButton(
                            btn_frame, text="Open Folder",
                            fg_color=self.GREEN, hover_color="#81C784",
                            text_color="#000", font=("Segoe UI", 11, "bold"),
                            height=34,
                            command=lambda: subprocess.Popen(
                                ["explorer", out_dir]))
                        open_btn.pack(side="left", expand=True, fill="x",
                                      padx=(0, 4))
                    return
                elif p["status"] == "error":
                    polling[0] = False
                    status_var.set(f"Error: {p.get('message', 'Unknown error')}")
                    start_btn.configure(state="normal")
                    cancel_btn.configure(text="Close")
                    return

            if started[0]:
                threading.Thread(target=_fetch_progress, daemon=True).start()
            dlg.after(500, poll_progress)

        def on_start():
            if not selection["file_path"] and not selection["folder_path"]:
                status_var.set("Select a file or folder first")
                return

            # Text-only with no translation
            if (selection["is_text"] and not selection["is_folder"]
                    and engine_var.get() == "none" and not live_var.get()):
                status_var.set("Text files require translation — select an engine and language")
                return

            start_btn.configure(state="disabled")
            is_live = live_var.get()
            status_var.set("Starting live playback..." if is_live
                           else "Starting batch processing...")
            progress.configure(mode="indeterminate")
            progress.start(15)
            polling[0] = True
            started[0] = False
            request_result[0] = None
            poll_data[0] = None

            if is_live:
                threading.Thread(target=_send_live_request,
                                 daemon=True).start()
            else:
                threading.Thread(target=_send_batch_request,
                                 daemon=True).start()
            dlg.after(500, poll_progress)

        def on_cancel():
            polling[0] = False
            dlg.destroy()

        start_btn = ctk.CTkButton(btn_frame, text="Start",
                                  fg_color=self.GREEN, hover_color="#81C784",
                                  text_color="#000", font=("Segoe UI", 11, "bold"),
                                  height=34, command=on_start, state="disabled")
        start_btn.pack(side="left", expand=True, fill="x", padx=(0, 4))

        cancel_btn = ctk.CTkButton(btn_frame, text="Cancel",
                                   fg_color=self.BG3, hover_color="#555",
                                   text_color="#fff", font=("Segoe UI", 11),
                                   height=34, command=on_cancel)
        cancel_btn.pack(side="right", expand=True, fill="x", padx=(4, 0))

        # Initial visibility — show engine frame but nothing selected yet
        engine_frame.pack(fill="x", pady=(4, 4))
        output_frame.pack(fill="x", pady=(4, 4))
```

- [ ] **Step 4: Verify `_read_server_output` still follows at the correct position**

After the new `_show_transcribe_dialog()` method ends, the next method should be `_read_server_output`. Confirm no code was lost or displaced between the two methods.

- [ ] **Step 5: Manual test — open dialog, verify layout**

Run: `python launcher.pyw`

1. Start the server
2. Click "Transcribe File"
3. Verify dialog opens with Select File / Select Folder buttons
4. Select a .wav file → verify "Play as Live Input" checkbox appears
5. Select a .txt file → verify checkbox does NOT appear
6. Select a folder → verify "Include subfolders" checkbox appears
7. Change engine to "DeepL" → verify language slot appears
8. Add a second language → verify first language is removed from second dropdown
9. Switch engine to "Offline (M2M 100+ Languages)" → verify compatible languages kept
10. Switch to an engine that drops languages → verify confirmation dialog appears
11. Set output directory → verify Change... button works
12. Click Start with a small audio file → verify progress polling works
13. Click Start with a text file + DeepL engine + language → verify text translation completes

- [ ] **Step 6: Commit**

```bash
git add launcher.pyw
git commit -m "[feat] rewrite batch dialog with translation engine, language slots, folder support"
```

---

### Task 5: Update patch manifest

**Files:**
- Modify: `build/windows/patch_files.iss`
- Modify: `version.json`

- [ ] **Step 1: Update version.json patch number**

```json
{"version":"1.0.3","patch":5}
```

- [ ] **Step 2: Update patch_files.iss**

```ini
; Auto-generated — Patch 5 for v1.0.3
; Batch transcription v2: translation engine, folder batch, text files
Source: "..\..\transcribe_file.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\..\server.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\..\launcher.pyw"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\..\version.json"; DestDir: "{app}"; Flags: ignoreversion
```

- [ ] **Step 3: Commit**

```bash
git add version.json build/windows/patch_files.iss
git commit -m "[build] bump to patch 5 for batch transcription v2"
```

---

## Self-Review Checklist

**Spec coverage:**
- [x] Translation engine selection (No Translation / DeepL / OPUS-MT / M2M-100) — Task 4 Step 1 + Task 4 Step 3
- [x] Dynamic language slot management with filtered dropdowns — Task 4 Step 3
- [x] Engine switch with confirmation dialog — Task 4 Step 3 (`_on_engine_change`)
- [x] OPUS-MT hidden when source not English — Task 4 Step 3 (`_get_engine_options`)
- [x] Folder selection with optional subfolder recursion — Task 4 Steps 2-3
- [x] Text file translation (.txt, .srt, .vtt, .md) — Task 1 Step 2
- [x] Input sanitization (null bytes, control chars, line length, file size) — Task 1 Step 1
- [x] Output directory picker — Task 4 Step 3
- [x] Enhanced API accepting JSON body — Task 3 Steps 2-3
- [x] `batch_translate_text()` — Task 1 Step 2
- [x] `batch_folder()` — Task 2 Step 2
- [x] Enhanced progress with `current_file`, `files_done`, `files_total` — Task 2 Step 1
- [x] Behavior matrix: text with no translation → skip with warning — Task 3 Step 3 + Task 4 Step 3 (`on_start`)
- [x] "Play as Live Input" visibility — Task 4 Step 3
- [x] Batch already in progress → reject — Task 3 Step 3 (409 check)

**Placeholder scan:** No TBD/TODO/placeholders found.

**Type consistency:**
- `BatchTranslationSlot.lang`/`.mode` matches `translations[n]["lang"]`/`["mode"]` throughout
- `_set_progress()` signature matches all call sites
- `batch_translate_text()` signature matches call from `batch_folder()` and server endpoint
- `batch_folder()` signature matches server endpoint call
