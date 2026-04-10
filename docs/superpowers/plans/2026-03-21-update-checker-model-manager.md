# Update Checker, Model Manager Cleanup & Installer Upgrade — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add auto-update checking with download capability, simplify model manager to delete-only, add edition detection via edition.txt, and add installer upgrade support.

**Architecture:** Edition is determined by a simple `edition.txt` file written by each platform's build system. The launcher reads it at startup to display edition info and select the correct installer asset for updates. Update checking hits the GitHub releases API via stdlib `urllib`. The model manager is stripped of download functionality — only delete buttons remain.

**Tech Stack:** Python 3.11, tkinter, urllib.request, json, Inno Setup (Pascal), bash

**Spec:** `docs/superpowers/specs/2026-03-21-update-checker-model-manager-design.md`

---

## File Structure

| File | Action | Responsibility |
|------|--------|---------------|
| `launcher.pyw` | Modify | Edition detection, header redesign, update checker, model manager simplification |
| `build/windows/installer.iss` | Modify | Write edition.txt, upgrade messaging |
| `build/mac/build.sh` | Modify | Write edition.txt |
| `build/linux/install.sh` | Modify | Write edition.txt |

---

### Task 1: Add New Settings Fields

**Files:**
- Modify: `launcher.pyw:45-55` (DEFAULT_SETTINGS)

- [ ] **Step 1: Add new default settings**

In `launcher.pyw`, add two new fields to `DEFAULT_SETTINGS` (line 45):

```python
DEFAULT_SETTINGS = {
    "transcripts_dir": str(DEFAULT_TRANSCRIPTS),
    "mic_index": None,
    "backend": "auto",
    "model": "large-v3-turbo",
    "display_port": 3000,
    "operator_port": 3001,
    "extended_port": 3002,
    "host": "0.0.0.0",
    "window_geometry": None,
    "check_for_updates": True,
    "dismissed_version": None,
}
```

- [ ] **Step 2: Verify launcher still starts**

Run: `python launcher.pyw` — confirm it opens without error, then close.

- [ ] **Step 3: Commit**

```bash
git add launcher.pyw
git commit -m "[feat] add check_for_updates and dismissed_version settings"
```

---

### Task 2: Edition Detection

**Files:**
- Modify: `launcher.pyw:13-28` (version/paths section)

- [ ] **Step 1: Add edition detection after APP_DIR**

After line 28 (`APP_DIR = Path(__file__).resolve().parent`), add edition reading:

```python
# Detect edition from edition.txt (written by installer/build system)
_edition_file = APP_DIR / "edition.txt"
EDITION = _edition_file.read_text().strip() if _edition_file.exists() else "Dev"
```

- [ ] **Step 2: Add GITHUB_REPO constant**

After the EDITION line, add the repo constant needed for update checking:

```python
GITHUB_REPO = "TheColliny/LinguaTaxi"
```

- [ ] **Step 3: Verify launcher still starts**

Run: `python launcher.pyw` — should show "Dev" edition since no edition.txt exists in dev. Close.

- [ ] **Step 4: Commit**

```bash
git add launcher.pyw
git commit -m "[feat] add edition detection from edition.txt and GITHUB_REPO constant"
```

---

### Task 3: Redesign Header with Edition Label and Update Controls

**Files:**
- Modify: `launcher.pyw:221-227` (header section in `_build_ui`)
- Modify: `launcher.pyw:156-212` (styles in `_setup_window`)

- [ ] **Step 1: Add a style for the update checkbox**

In `_setup_window()`, after the existing `TLabelframe.Label` style block (around line 212), add:

```python
style.configure("Update.TCheckbutton", background=self.BG, foreground=self.FG2,
                 font=("Segoe UI", 8))
style.map("Update.TCheckbutton",
           background=[("active", self.BG)],
           foreground=[("active", self.FG)])
```

- [ ] **Step 2: Redesign the header**

Replace the header block (lines 221-227) with a two-column layout:

```python
        # ── Header ──
        hdr = ttk.Frame(main)
        hdr.pack(fill="x", pady=(0, 12))

        # Left side — title and subtitle
        hdr_left = ttk.Frame(hdr)
        hdr_left.pack(side="left", fill="both", expand=True)

        edition_suffix = f" — {EDITION} Edition" if EDITION != "Dev" else ""
        ttk.Label(hdr_left, text=f"\U0001f695  LinguaTaxi{edition_suffix}",
                  style="Title.TLabel").pack(anchor="w")
        ttk.Label(hdr_left, text="Live Caption & Translation",
                  style="Subtitle.TLabel").pack(anchor="w")

        # Right side — update controls
        hdr_right = ttk.Frame(hdr)
        hdr_right.pack(side="right", anchor="ne")

        ttk.Button(hdr_right, text="Check for Updates",
                   command=self._check_for_updates_manual).pack(anchor="e")

        self.update_check_var = tk.BooleanVar(
            value=self.settings.get("check_for_updates", True))
        ttk.Checkbutton(hdr_right, text="Check on startup",
                        variable=self.update_check_var,
                        style="Update.TCheckbutton",
                        command=self._on_update_check_toggled).pack(anchor="e", pady=(4, 0))
```

- [ ] **Step 3: Add stub methods**

Add placeholder methods after `_on_close()` (before the entry point at line 1914) so the app doesn't crash:

```python
    # ── Update Checking ──

    def _on_update_check_toggled(self):
        """Save the checkbox state when toggled."""
        self.settings["check_for_updates"] = self.update_check_var.get()
        save_settings(self.settings)

    def _check_for_updates_manual(self):
        """Manual update check triggered by button click."""
        messagebox.showinfo("Check for Updates", "Not yet implemented.", parent=self)
```

- [ ] **Step 4: Update _save_current_settings to include new fields**

In `_save_current_settings()` (line 1839), add the new setting:

```python
    def _save_current_settings(self):
        self.settings["transcripts_dir"] = self.tdir_var.get().strip()
        self.settings["mic_index"] = self._get_selected_mic_index()
        self.settings["backend"] = self._backend_from_label.get(self.backend_var.get(), self.backend_var.get())
        self.settings["window_geometry"] = self.geometry()
        self.settings["check_for_updates"] = self.update_check_var.get()
        save_settings(self.settings)
```

- [ ] **Step 5: Verify header layout**

Run: `python launcher.pyw` — confirm:
- Left side shows "LinguaTaxi" (no edition suffix in dev)
- Right side shows "Check for Updates" button and "Check on startup" checkbox
- Checkbox is checked by default
- Button shows "Not yet implemented" messagebox

- [ ] **Step 6: Commit**

```bash
git add launcher.pyw
git commit -m "[feat] redesign header with edition label and update check controls"
```

---

### Task 4: Simplify Model Manager to Delete-Only

**Files:**
- Modify: `launcher.pyw:340-341` (button label)
- Modify: `launcher.pyw:1229-1550` (model manager dialog)

- [ ] **Step 1: Rename the button**

Change line 340-341 from:
```python
        ttk.Button(settings_frame, text="🔧  Manage Installed Models",
                   command=self._show_model_manager_dialog).pack(fill="x", pady=(0, 0))
```
to:
```python
        ttk.Button(settings_frame, text="🗑  Delete Installed Models",
                   command=self._show_model_manager_dialog).pack(fill="x", pady=(0, 0))
```

- [ ] **Step 2: Update dialog title**

Change line 1232 from `"Manage Installed Models"` to `"Delete Installed Models"`.

Change line 1248 from `"Manage Installed Models"` to `"Delete Installed Models"`.

- [ ] **Step 3: Remove _download_model function**

Delete the entire `_download_model` function (lines 1399-1411):

```python
        def _download_model(model_type, key, name):
            """Launch the appropriate download dialog for a model."""
            dlg.grab_release()
            if model_type == "tuned":
                self._show_tuned_models_dialog()
            elif model_type in ("opus", "m2m"):
                self._show_offline_translate_dialog()
            # Re-grab after download dialog closes
            try:
                dlg.grab_set()
            except Exception:
                pass
            _populate()
```

- [ ] **Step 4: Simplify _add_model_row — remove installed parameter and download branch**

Replace the entire `_add_model_row` function (lines 1413-1450) with:

```python
        def _add_model_row(parent, name, size_str, model_type, key):
            """Add a single model row with name, size, and delete button."""
            row = tk.Frame(parent, bg=self.BG)
            row.pack(fill="x", pady=2, padx=4)

            indicator = tk.Label(row, text="●", fg="#66BB6A", bg=self.BG,
                                 font=("Segoe UI", 9))
            indicator.pack(side="left", padx=(0, 4))

            name_lbl = tk.Label(row, text=name, fg=self.FG, bg=self.BG,
                                font=("Segoe UI", 9), anchor="w")
            name_lbl.pack(side="left", fill="x", expand=True)

            size_lbl = tk.Label(row, text=size_str, fg="#999", bg=self.BG,
                                font=("Segoe UI", 9))
            size_lbl.pack(side="left", padx=(8, 8))

            del_btn = tk.Button(row, text="  Delete  ", fg="#fff", bg="#c62828",
                                activeforeground="#fff", activebackground="#f44336",
                                font=("Segoe UI", 8, "bold"), relief="raised",
                                cursor="hand2", bd=1,
                                command=lambda mt=model_type, k=key, n=name:
                                    _delete_model(mt, k, n))
            del_btn.pack(side="right", padx=(4, 0))
```

- [ ] **Step 5: Update _populate to only show installed models**

In `_populate()`, make these changes:

**Tuned models section** (around lines 1472-1490) — replace with:
```python
            # Tuned models
            _add_section_header(list_frame, "Language-Tuned Whisper Models")
            tuned = _get_tuned_models()
            has_tuned = False
            for lang, info in sorted(tuned.items()):
                if info.get("available", False):
                    has_tuned = True
                    name = f"{info.get('name', lang)} ({lang})"
                    tuned_dir = models_dir / "tuned" / lang.lower()
                    size = sum(f.stat().st_size for f in tuned_dir.rglob("*") if f.is_file()) if tuned_dir.exists() else 0
                    total_bytes += size
                    _add_model_row(list_frame, name, _fmt_size(size), "tuned", lang)
            if not tuned:
                tk.Label(list_frame, text="  tuned_models.py not found (Full edition only)",
                         fg="#666", bg=self.BG, font=("Segoe UI", 9, "italic")).pack(anchor="w")
            elif not has_tuned:
                tk.Label(list_frame, text="  No tuned models installed",
                         fg="#666", bg=self.BG, font=("Segoe UI", 9, "italic")).pack(anchor="w")
```

**OPUS-MT section** (around lines 1493-1510) — keep the section header and variable initializations (lines 1493-1496), then replace the OPUS-MT loop with:
```python
            # Translation models (keep existing header + variable lines 1493-1496 as-is)
            _add_section_header(list_frame, "Offline Translation Models")
            translate = _get_translate_models()
            opus = translate.get("opus", {})
            m2m = translate.get("m2m100", {})

            # OPUS-MT (replace from here)
            has_opus = False
            if opus:
                for lang, info in sorted(opus.items()):
                    if info.get("available", False):
                        has_opus = True
                        name = f"OPUS-MT {info.get('name', lang)} ({lang})"
                        opus_dir = models_dir / "translate" / f"opus-mt-en-{lang.lower()}"
                        size = sum(f.stat().st_size for f in opus_dir.rglob("*") if f.is_file()) if opus_dir.exists() else 0
                        total_bytes += size
                        _add_model_row(list_frame, name, _fmt_size(size), "opus", lang)
```

**M2M-100 section** (around lines 1512-1524) — replace with:
```python
            # M2M-100
            if m2m and m2m.get("available", False):
                m2m_name = m2m.get("name", "M2M-100")
                m2m_dir = models_dir / "translate" / "m2m100-1.2b"
                size = sum(f.stat().st_size for f in m2m_dir.rglob("*") if f.is_file()) if m2m_dir.exists() else 0
                total_bytes += size
                _add_model_row(list_frame, m2m_name, _fmt_size(size), "m2m", "m2m100")
```

**No-translate fallback** (around lines 1526-1528) — replace with:
```python
            if not translate or (not has_opus and not (m2m and m2m.get("available", False))):
                tk.Label(list_frame, text="  No offline translation models installed",
                         fg="#666", bg=self.BG, font=("Segoe UI", 9, "italic")).pack(anchor="w")
```

- [ ] **Step 6: Verify model manager**

Run: `python launcher.pyw` → click "Delete Installed Models" → confirm:
- Title says "Delete Installed Models"
- Only installed models shown (no "Download" buttons, no uninstalled rows)
- Delete buttons work (test on a non-critical model if available, or just verify the button exists)

- [ ] **Step 7: Commit**

```bash
git add launcher.pyw
git commit -m "[feat] simplify model manager to delete-only, remove download functionality"
```

---

### Task 5: Implement Update Checking Logic

**Files:**
- Modify: `launcher.pyw` — add new imports and `_check_github_release()` method

- [ ] **Step 1: Add urllib import**

At line 7, add `urllib.request` and `urllib.error` to the imports:

```python
import json, os, platform, queue, re, shutil, signal, subprocess, sys, threading, time, webbrowser
import urllib.request, urllib.error
from pathlib import Path
```

- [ ] **Step 2: Add version comparison helper**

After the `GITHUB_REPO` constant (added in Task 2), add:

```python
def _parse_version(tag):
    """Parse 'vX.Y.Z' or 'X.Y.Z' into (X, Y, Z) tuple. Returns None on failure."""
    tag = tag.strip().lstrip("v")
    try:
        parts = tuple(int(x) for x in tag.split("."))
        if len(parts) == 3:
            return parts
    except (ValueError, AttributeError):
        pass
    return None
```

- [ ] **Step 3: Add _check_github_release method**

Replace the `_check_for_updates_manual` stub (from Task 3) with the real implementation. Add these methods in the `# ── Update Checking ──` section:

```python
    # ── Update Checking ──

    def _on_update_check_toggled(self):
        """Save the checkbox state when toggled."""
        self.settings["check_for_updates"] = self.update_check_var.get()
        save_settings(self.settings)

    def _check_github_release(self):
        """Fetch latest release from GitHub. Returns (tag, assets, body) or None."""
        url = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
        req = urllib.request.Request(url, headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": f"LinguaTaxi/{VERSION}",
        })
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode())
                return data.get("tag_name", ""), data.get("assets", []), data.get("body", "")
        except (urllib.error.HTTPError, urllib.error.URLError, OSError, ValueError):
            return None

    def _find_asset_url(self, assets, tag):
        """Find the download URL for the current edition's installer."""
        version = tag.lstrip("v")
        patterns = {
            "GPU": f"LinguaTaxi-GPU-Setup-{version}.exe",
            "CPU": f"LinguaTaxi-CPU-Setup-{version}.exe",
            "macOS": f"LinguaTaxi-{version}.dmg",
            "Linux": f"LinguaTaxi-{version}-linux.tar.gz",
        }
        target = patterns.get(EDITION)
        if not target:
            return None, None
        for asset in assets:
            if asset.get("name") == target:
                return asset["browser_download_url"], target
        return None, None

    def _check_for_updates_manual(self):
        """Manual update check triggered by button click."""
        self._do_update_check(manual=True)

    def _do_update_check(self, manual=False):
        """Run update check in background thread, show result on main thread."""
        def _worker():
            result = self._check_github_release()
            self.after(0, lambda: self._handle_update_result(result, manual))

        threading.Thread(target=_worker, daemon=True).start()
        if manual:
            self._log_system("Checking for updates...")

    def _handle_update_result(self, result, manual):
        """Process update check result on the main thread."""
        if result is None:
            if manual:
                messagebox.showinfo("Check for Updates",
                    "Could not reach GitHub. Check your internet connection\n"
                    "or try again later (rate limit: 60 requests/hour).",
                    parent=self)
            return

        tag, assets, body = result
        remote_ver = _parse_version(tag)
        local_ver = _parse_version(VERSION)

        if remote_ver is None or local_ver is None:
            if manual:
                messagebox.showinfo("Check for Updates",
                    f"Could not parse version: remote={tag}, local={VERSION}",
                    parent=self)
            return

        if remote_ver <= local_ver:
            if manual:
                messagebox.showinfo("Check for Updates",
                    f"You're up to date! (v{VERSION})", parent=self)
            return

        # New version available — check if dismissed
        if not manual and self.settings.get("dismissed_version") == tag:
            return

        self._show_update_dialog(tag, assets)
```

- [ ] **Step 4: Verify manual check works**

Run: `python launcher.pyw` → click "Check for Updates" → should show "You're up to date!" (since local version matches remote v1.0.0).

- [ ] **Step 5: Commit**

```bash
git add launcher.pyw
git commit -m "[feat] implement GitHub release checking with version comparison"
```

---

### Task 6: Implement Update Prompt Dialog

**Files:**
- Modify: `launcher.pyw` — add `_show_update_dialog()` method

- [ ] **Step 1: Add the update dialog method**

Add after `_handle_update_result`:

```python
    def _show_update_dialog(self, tag, assets):
        """Show dialog offering to download a new version."""
        version = tag.lstrip("v")

        dlg = tk.Toplevel(self)
        dlg.title("Update Available")
        dlg.geometry("440x200")
        dlg.resizable(False, False)
        dlg.configure(bg=self.BG)
        dlg.transient(self)
        dlg.grab_set()

        dlg.update_idletasks()
        px = self.winfo_x() + (self.winfo_width() - 440) // 2
        py = self.winfo_y() + (self.winfo_height() - 200) // 2
        dlg.geometry(f"+{px}+{py}")

        f = ttk.Frame(dlg, padding=24)
        f.pack(fill="both", expand=True)

        ttk.Label(f, text=f"LinguaTaxi v{version} is available!",
                  font=("Segoe UI", 12, "bold"),
                  foreground=self.ACCENT, background=self.BG).pack(pady=(0, 4))
        ttk.Label(f, text=f"You have v{VERSION}.",
                  style="Subtitle.TLabel").pack(pady=(0, 16))

        btn_frame = ttk.Frame(f)
        btn_frame.pack(fill="x")

        def _download_now():
            dlg.destroy()
            self._download_update(tag, assets)

        def _remind_later():
            dlg.destroy()

        def _dont_remind():
            self.settings["dismissed_version"] = tag
            save_settings(self.settings)
            dlg.destroy()

        ttk.Button(btn_frame, text="Download Now", style="Start.TButton",
                   command=_download_now).pack(side="left", padx=(0, 8))
        ttk.Button(btn_frame, text="Remind Me Later",
                   command=_remind_later).pack(side="left", padx=(0, 8))
        ttk.Button(btn_frame, text="Don't Remind Me",
                   command=_dont_remind).pack(side="left")

        self.wait_window(dlg)
```

- [ ] **Step 2: Add stub _download_update**

```python
    def _download_update(self, tag, assets):
        """Download the installer for the current edition."""
        messagebox.showinfo("Download", "Not yet implemented.", parent=self)
```

- [ ] **Step 3: Verify dialog appearance**

To test, temporarily change `VERSION = "0.9.0"` in launcher.pyw, run, click "Check for Updates" — should show the update dialog with three buttons. Then revert VERSION back to `"1.0.0"`.

- [ ] **Step 4: Commit**

```bash
git add launcher.pyw
git commit -m "[feat] add update available dialog with download/remind/dismiss options"
```

---

### Task 7: Implement Download Flow

**Files:**
- Modify: `launcher.pyw` — replace `_download_update` stub

- [ ] **Step 1: Replace _download_update with full implementation**

```python
    def _download_update(self, tag, assets):
        """Download the installer for the current edition."""
        url, filename = self._find_asset_url(assets, tag)

        if url is None:
            if EDITION == "Dev":
                webbrowser.open(f"https://github.com/{GITHUB_REPO}/releases/tag/{tag}")
                self._log_system("Opened GitHub releases page in browser.")
                return
            messagebox.showerror("Download Error",
                f"Could not find installer for {EDITION} edition in this release.",
                parent=self)
            return

        # Ask where to save
        downloads_dir = Path.home() / "Downloads"
        save_path = filedialog.asksaveasfilename(
            parent=self,
            initialdir=str(downloads_dir),
            initialfile=filename,
            title="Save Installer As",
            defaultextension=Path(filename).suffix,
            filetypes=[("Installer", f"*{Path(filename).suffix}"), ("All files", "*.*")],
        )
        if not save_path:
            return

        save_path = Path(save_path)
        self._show_download_progress(url, save_path)

    def _show_download_progress(self, url, save_path):
        """Show progress dialog while downloading the installer."""
        dlg = tk.Toplevel(self)
        dlg.title("Downloading Update")
        dlg.geometry("460x160")
        dlg.resizable(False, False)
        dlg.configure(bg=self.BG)
        dlg.transient(self)
        dlg.grab_set()

        dlg.update_idletasks()
        px = self.winfo_x() + (self.winfo_width() - 460) // 2
        py = self.winfo_y() + (self.winfo_height() - 160) // 2
        dlg.geometry(f"+{px}+{py}")

        f = ttk.Frame(dlg, padding=20)
        f.pack(fill="both", expand=True)

        status_var = tk.StringVar(value="Connecting...")
        ttk.Label(f, textvariable=status_var, style="Subtitle.TLabel").pack(pady=(0, 8))

        progress = ttk.Progressbar(f, mode="determinate", length=400)
        progress.pack(pady=(0, 12))

        cancelled = [False]

        def _cancel():
            cancelled[0] = True

        cancel_btn = ttk.Button(f, text="Cancel", command=_cancel)
        cancel_btn.pack()

        def _worker():
            partial = Path(str(save_path) + ".part")
            try:
                req = urllib.request.Request(url, headers={
                    "User-Agent": f"LinguaTaxi/{VERSION}",
                })
                with urllib.request.urlopen(req, timeout=30) as resp:
                    total = int(resp.headers.get("Content-Length", 0))
                    downloaded = 0
                    chunk_size = 64 * 1024

                    with open(partial, "wb") as out:
                        while True:
                            if cancelled[0]:
                                break
                            chunk = resp.read(chunk_size)
                            if not chunk:
                                break
                            out.write(chunk)
                            downloaded += len(chunk)

                            if total > 0:
                                pct = downloaded * 100 / total
                                mb = downloaded / (1024 * 1024)
                                total_mb = total / (1024 * 1024)
                                self.after(0, lambda p=pct, m=mb, t=total_mb: (
                                    progress.configure(value=p),
                                    status_var.set(f"{m:.1f} / {t:.1f} MB ({p:.0f}%)")
                                ))

                if cancelled[0]:
                    partial.unlink(missing_ok=True)
                    self.after(0, dlg.destroy)
                    return

                # Rename .part to final
                if save_path.exists():
                    save_path.unlink()
                partial.rename(save_path)

                self.after(0, lambda: _download_complete(dlg, status_var, progress, cancel_btn))

            except Exception as e:
                partial.unlink(missing_ok=True)
                def _show_error(err=e):
                    status_var.set(f"Download failed: {err}")
                    cancel_btn.configure(text="Close", command=dlg.destroy)
                self.after(0, _show_error)

        def _download_complete(dlg, status_var, progress, cancel_btn):
            status_var.set("Download complete!")
            progress.configure(value=100)
            cancel_btn.destroy()

            btn_frame = ttk.Frame(f)
            btn_frame.pack(pady=(4, 0))

            def _open_folder():
                if IS_WIN:
                    subprocess.Popen(["explorer", "/select,", str(save_path)])
                elif IS_MAC:
                    subprocess.Popen(["open", "-R", str(save_path)])
                else:
                    subprocess.Popen(["xdg-open", str(save_path.parent)])
                dlg.destroy()

            ttk.Button(btn_frame, text="Open Folder", command=_open_folder).pack(side="left", padx=(0, 8))
            ttk.Button(btn_frame, text="Close", command=dlg.destroy).pack(side="left")

            # Reminder
            ttk.Label(f, text="Close LinguaTaxi before running the installer.",
                      style="Subtitle.TLabel").pack(pady=(8, 0))

        threading.Thread(target=_worker, daemon=True).start()
        self.wait_window(dlg)
```

- [ ] **Step 2: Verify download flow end-to-end**

To test, temporarily set `VERSION = "0.9.0"`, run, click "Check for Updates" → "Download Now" → choose save location → verify download completes with progress bar and "Open Folder" button works. Revert VERSION.

- [ ] **Step 3: Commit**

```bash
git add launcher.pyw
git commit -m "[feat] implement installer download with progress dialog and cancel support"
```

---

### Task 8: Add Startup Auto-Check

**Files:**
- Modify: `launcher.pyw:98-116` (`__init__` method)

- [ ] **Step 1: Add auto-check call at end of __init__**

After the existing `signal.signal` block (line 116), add:

```python
        # Auto-check for updates after UI is ready
        if self.settings.get("check_for_updates", True):
            self.after(2000, lambda: self._do_update_check(manual=False))
```

The 2-second delay ensures the UI is fully rendered before any dialog might appear.

- [ ] **Step 2: Verify startup check**

Run `python launcher.pyw` — should start normally. With current version matching remote, nothing should pop up. To test the dialog, temporarily set `VERSION = "0.9.0"`, run, wait 2 seconds — update dialog should appear. Revert VERSION.

- [ ] **Step 3: Test dismissed version**

Set `VERSION = "0.9.0"`, run, click "Don't Remind Me" in the update dialog. Close and reopen — no dialog should appear on startup. Check `launcher_settings.json` has `"dismissed_version": "v1.0.0"`. Revert VERSION and clear dismissed_version from settings.

- [ ] **Step 4: Commit**

```bash
git add launcher.pyw
git commit -m "[feat] add auto-check for updates on startup with 2s delay"
```

---

### Task 9: Installer — Write edition.txt and Upgrade Messaging

**Files:**
- Modify: `build/windows/installer.iss:172-187` (CurStepChanged)
- Modify: `build/windows/installer.iss:39-60` (Setup section)

- [ ] **Step 1: Write edition.txt in CurStepChanged**

In `installer.iss`, expand the existing `CurStepChanged` procedure (lines 172-187) to also write `edition.txt`:

Replace the entire `CurStepChanged` procedure (lines 172-187) with:

```pascal
procedure CurStepChanged(CurStep: TSetupStep);
var
  CfgPath: String;
  PythonHome: String;
  EditionPath: String;
begin
  if CurStep = ssPostInstall then
  begin
    // Fix venv paths
    CfgPath := ExpandConstant('{app}\venv\pyvenv.cfg');
    PythonHome := ExpandConstant('{app}\python');
    SaveStringToFile(CfgPath,
      'home = ' + PythonHome + #13#10 +
      'include-system-site-packages = false' + #13#10 +
      'version = 3.11.9' + #13#10,
      False);

    // Write edition.txt (ISPP #if is evaluated at compile time, not runtime)
    EditionPath := ExpandConstant('{app}\edition.txt');
  #if EDITION == "Full"
    SaveStringToFile(EditionPath, 'GPU', False);
  #else
    SaveStringToFile(EditionPath, 'CPU', False);
  #endif
  end;
end;
```

- [ ] **Step 2: Add upgrade detection in InitializeSetup**

Add a new function before `CurStepChanged` to detect upgrades and customize the Ready page memo. Use `UpdateReadyMemo` (the officially supported Inno Setup approach):

```pascal
function UpdateReadyMemo(Space, NewLine, MemoUserInfoInfo, MemoDirInfo,
  MemoTypeInfo, MemoComponentsInfo, MemoGroupInfo, MemoTasksInfo: String): String;
var
  PrevVersion: String;
begin
  // Check if upgrading from a previous version
  if RegQueryStringValue(HKLM,
       'SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\{#SetupSetting("AppId")}_is1',
       'DisplayVersion', PrevVersion) then
  begin
    if PrevVersion <> '{#MyAppVersion}' then
      Result := 'Upgrading LinguaTaxi from v' + PrevVersion + ' to v{#MyAppVersion}.' + NewLine + NewLine +
                'Your models, transcripts, and settings will be preserved.' + NewLine +
                'Only program files will be updated.' + NewLine + NewLine;
  end;
  // Append standard memo content
  if MemoDirInfo <> '' then
    Result := Result + MemoDirInfo + NewLine + NewLine;
  if MemoGroupInfo <> '' then
    Result := Result + MemoGroupInfo + NewLine + NewLine;
  if MemoTasksInfo <> '' then
    Result := Result + MemoTasksInfo + NewLine;
end;
```

- [ ] **Step 3: Verify installer compiles**

If Inno Setup is available locally, compile `installer.iss` with `ISCC /DEDITION=Full installer.iss` and verify no errors. If not available, review the Pascal syntax carefully.

- [ ] **Step 4: Commit**

```bash
git add build/windows/installer.iss
git commit -m "[feat] installer writes edition.txt and shows upgrade messaging"
```

---

### Task 10: Build Scripts — Write edition.txt for macOS and Linux

**Files:**
- Modify: `build/mac/build.sh:46-54` (after copying application files)
- Modify: `build/linux/install.sh:144-150` (after model download, before launch script)

- [ ] **Step 1: Add edition.txt to macOS build**

In `build/mac/build.sh`, after line 54 (last `cp` of application files), add:

```bash
echo "macOS" > "$APP_BUNDLE/Contents/Resources/edition.txt"
```

- [ ] **Step 2: Add edition.txt to Linux install**

In `build/linux/install.sh`, after line 142 (model download), add:

```bash
# ── Write edition marker ──
echo "Linux" > "$APP_DIR/edition.txt"
```

- [ ] **Step 3: Commit**

```bash
git add build/mac/build.sh build/linux/install.sh
git commit -m "[feat] macOS and Linux build scripts write edition.txt"
```

---

### Task 11: Final Integration Verification

- [ ] **Step 1: Full smoke test of launcher**

Run `python launcher.pyw` and verify:
1. Header shows "LinguaTaxi" (no edition suffix — Dev mode)
2. "Check for Updates" button and checkbox visible in top-right
3. Checkbox is checked by default
4. Click "Check for Updates" → "You're up to date!" (assuming v1.0.0 matches remote)
5. Click "Delete Installed Models" → only installed models shown, no download buttons
6. Delete dialog title says "Delete Installed Models"
7. Close the app, reopen — checkbox state persists

- [ ] **Step 2: Test update flow with version mismatch**

Temporarily set `VERSION = "0.9.0"`:
1. Launch → after 2s, update dialog appears
2. Click "Remind Me Later" → dialog closes, no settings change
3. Close and reopen → update dialog appears again
4. Click "Don't Remind Me" → dialog closes
5. Close and reopen → NO update dialog
6. Click "Check for Updates" manually → update dialog STILL appears (manual overrides dismiss)
7. Click "Download Now" → save dialog → download with progress → "Open Folder" works

Revert `VERSION = "1.0.0"` and clear `dismissed_version` from settings file.

- [ ] **Step 3: Final commit of any cleanup**

```bash
git add -A
git commit -m "[chore] final cleanup for update checker and model manager changes"
```

- [ ] **Step 4: Verify all changes with git diff**

```bash
git log --oneline -12
```

Confirm all commits are present and logical.
