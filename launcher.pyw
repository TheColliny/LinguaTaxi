#!/usr/bin/env python3
"""
LinguaTaxi — Live Caption & Translation
Desktop launcher with server management and browser integration.
"""

import json, os, platform, queue, re, shutil, signal, subprocess, sys, threading, time, webbrowser
import urllib.request, urllib.error
from pathlib import Path

import tkinter as tk
from tkinter import ttk, filedialog, messagebox

# ── Version & Paths ──

APP_NAME = "LinguaTaxi"
APP_FULL = "LinguaTaxi — Live Caption & Translation"
VERSION = "1.0.0"

IS_WIN = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"

# Determine app directory (where server.py lives)
if os.environ.get("LINGUATAXI_APP_DIR"):
    APP_DIR = Path(os.environ["LINGUATAXI_APP_DIR"])
elif getattr(sys, "frozen", False):
    APP_DIR = Path(sys.executable).parent
else:
    APP_DIR = Path(__file__).resolve().parent

# Detect edition from edition.txt (written by installer/build system)
_edition_file = APP_DIR / "edition.txt"
EDITION = _edition_file.read_text().strip() if _edition_file.exists() else "Dev"

GITHUB_REPO = "TheColliny/LinguaTaxi"

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

SERVER_PY = APP_DIR / "server.py"

# Settings directory
if IS_WIN:
    SETTINGS_DIR = Path(os.environ.get("APPDATA", Path.home())) / "LinguaTaxi"
elif IS_MAC:
    SETTINGS_DIR = Path.home() / "Library" / "Application Support" / "LinguaTaxi"
else:
    SETTINGS_DIR = Path.home() / ".config" / "linguataxi"

SETTINGS_FILE = SETTINGS_DIR / "launcher_settings.json"
DEFAULT_TRANSCRIPTS = Path.home() / "Documents" / "LinguaTaxi Transcripts"

# ── Default Settings ──

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


def load_settings():
    try:
        SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
        if SETTINGS_FILE.exists():
            with open(SETTINGS_FILE, "r") as f:
                return {**DEFAULT_SETTINGS, **json.load(f)}
    except Exception:
        pass
    return dict(DEFAULT_SETTINGS)


def save_settings(cfg):
    try:
        SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
        with open(SETTINGS_FILE, "w") as f:
            json.dump(cfg, f, indent=2)
    except Exception:
        pass


# ── Microphone detection ──

def list_mics():
    """Return list of (index, name) for available input devices."""
    try:
        import sounddevice as sd
        devices = sd.query_devices()
        mics = []
        for i, d in enumerate(devices):
            if d.get("max_input_channels", 0) > 0:
                mics.append((i, d["name"]))
        return mics
    except Exception:
        return []


# ══════════════════════════════════════════════
# MAIN APPLICATION
# ══════════════════════════════════════════════

class LinguaTaxiApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.settings = load_settings()
        self.server_proc = None
        self.log_queue = queue.Queue()
        self._server_running = False
        self._server_ready = False
        self._closing = False

        self._setup_window()
        self._build_ui()
        self._poll_log_queue()

        # Handle close
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        if IS_WIN:
            # Handle Ctrl+C
            signal.signal(signal.SIGINT, lambda *a: self._on_close())

        # Auto-check for updates after UI is ready
        if self.settings.get("check_for_updates", True):
            self.after(2000, lambda: self._do_update_check(manual=False))

    # ── Window Setup ──

    def _setup_window(self):
        self.title(APP_FULL)
        self.minsize(520, 620)
        self.resizable(True, True)

        # Restore geometry
        geo = self.settings.get("window_geometry")
        if geo:
            try:
                self.geometry(geo)
            except Exception:
                self.geometry("560x700")
        else:
            self.geometry("560x700")

        # Center on screen if no saved position
        if not geo:
            self.update_idletasks()
            w, h = self.winfo_width(), self.winfo_height()
            sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
            self.geometry(f"+{(sw - w) // 2}+{(sh - h) // 2}")

        # Dark theme colors
        self.BG = "#1a1a2e"
        self.BG2 = "#16213e"
        self.BG3 = "#0f3460"
        self.FG = "#e8e8e8"
        self.FG2 = "#8899aa"
        self.ACCENT = "#4FC3F7"
        self.GREEN = "#4CAF50"
        self.RED = "#f44336"
        self.ORANGE = "#FF9800"
        self.YELLOW = "#FFD54F"

        self.configure(bg=self.BG)

        # Configure ttk styles
        style = ttk.Style()
        style.theme_use("clam")

        style.configure(".", background=self.BG, foreground=self.FG,
                         fieldbackground=self.BG2, borderwidth=0)
        style.configure("TFrame", background=self.BG)
        style.configure("Card.TFrame", background=self.BG2)
        style.configure("TLabel", background=self.BG, foreground=self.FG, font=("Segoe UI", 10))
        style.configure("Title.TLabel", font=("Segoe UI", 20, "bold"), foreground=self.ACCENT)
        style.configure("Subtitle.TLabel", font=("Segoe UI", 10), foreground=self.FG2)
        style.configure("Status.TLabel", font=("Segoe UI", 10, "bold"), foreground=self.FG2)
        style.configure("Section.TLabel", font=("Segoe UI", 10, "bold"),
                         foreground=self.ACCENT, background=self.BG)

        style.configure("TButton", padding=(12, 6), font=("Segoe UI", 10),
                         background=self.BG3, foreground=self.FG)
        style.map("TButton",
                   background=[("active", self.ACCENT), ("disabled", "#333")],
                   foreground=[("active", "#000"), ("disabled", "#666")])

        style.configure("Start.TButton", font=("Segoe UI", 11, "bold"),
                         background=self.GREEN, foreground="#fff", padding=(16, 10))
        style.map("Start.TButton",
                   background=[("active", "#66BB6A"), ("disabled", "#333")])

        style.configure("Stop.TButton", font=("Segoe UI", 11, "bold"),
                         background=self.RED, foreground="#fff", padding=(16, 10))
        style.map("Stop.TButton",
                   background=[("active", "#EF5350"), ("disabled", "#333")])

        style.configure("Browser.TButton", font=("Segoe UI", 10),
                         background=self.BG3, foreground=self.ACCENT, padding=(10, 7))
        style.map("Browser.TButton",
                   background=[("active", self.ACCENT)],
                   foreground=[("active", "#000"), ("disabled", "#555")])

        style.configure("TCombobox", fieldbackground=self.BG2, foreground=self.FG,
                         selectbackground=self.BG2, selectforeground=self.FG,
                         arrowcolor=self.FG, font=("Segoe UI", 10))
        style.map("TCombobox",
                   fieldbackground=[("readonly", self.BG2), ("disabled", "#333")],
                   foreground=[("readonly", self.FG), ("disabled", "#666")],
                   selectbackground=[("readonly", self.BG2)],
                   selectforeground=[("readonly", self.FG)],
                   arrowcolor=[("readonly", self.FG), ("disabled", "#666")])

        # Style the dropdown popup listbox (not reachable via ttk.Style)
        self.option_add("*TCombobox*Listbox.background", self.BG2)
        self.option_add("*TCombobox*Listbox.foreground", self.FG)
        self.option_add("*TCombobox*Listbox.selectBackground", self.ACCENT)
        self.option_add("*TCombobox*Listbox.selectForeground", "#000")
        self.option_add("*TCombobox*Listbox.font", ("Segoe UI", 10))

        style.configure("TLabelframe", background=self.BG, foreground=self.ACCENT)
        style.configure("TLabelframe.Label", background=self.BG,
                         foreground=self.ACCENT, font=("Segoe UI", 10, "bold"))

        style.configure("Update.TCheckbutton", background=self.BG, foreground=self.FG2,
                         font=("Segoe UI", 8))
        style.map("Update.TCheckbutton",
                   background=[("active", self.BG)],
                   foreground=[("active", self.FG)])

    # ── Build UI ──

    def _build_ui(self):
        # Main container with padding
        main = ttk.Frame(self, padding=16)
        main.pack(fill="both", expand=True)

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

        # ── Server Control ──
        srv_frame = ttk.LabelFrame(main, text="  Server  ", padding=12)
        srv_frame.pack(fill="x", pady=(0, 10))

        status_row = ttk.Frame(srv_frame)
        status_row.pack(fill="x", pady=(0, 8))

        self.status_dot = tk.Canvas(status_row, width=12, height=12,
                                     bg=self.BG, highlightthickness=0)
        self.status_dot.pack(side="left", padx=(0, 6))
        self._draw_dot("#666")

        self.status_label = ttk.Label(status_row, text="Stopped",
                                       style="Status.TLabel")
        self.status_label.pack(side="left")

        self.backend_label = ttk.Label(status_row, text="",
                                        style="Subtitle.TLabel")
        self.backend_label.pack(side="right")

        btn_row = ttk.Frame(srv_frame)
        btn_row.pack(fill="x")

        self.start_btn = ttk.Button(btn_row, text="▶  Start Server",
                                     style="Start.TButton", command=self._start_server)
        self.start_btn.pack(side="left", expand=True, fill="x", padx=(0, 4))

        self.stop_btn = ttk.Button(btn_row, text="⏹  Stop",
                                    style="Stop.TButton", command=self._stop_server,
                                    state="disabled")
        self.stop_btn.pack(side="right", expand=True, fill="x", padx=(4, 0))

        # ── Browser Buttons ──
        browser_frame = ttk.LabelFrame(main, text="  Open in Browser  ", padding=12)
        browser_frame.pack(fill="x", pady=(0, 10))

        self.op_btn = ttk.Button(browser_frame, text="🎛  Operator Controls",
                                  style="Browser.TButton", command=self._open_operator,
                                  state="disabled")
        self.op_btn.pack(fill="x", pady=(0, 5))

        disp_row = ttk.Frame(browser_frame)
        disp_row.pack(fill="x")

        self.main_btn = ttk.Button(disp_row, text="📺  Main Display",
                                    style="Browser.TButton", command=self._open_main,
                                    state="disabled")
        self.main_btn.pack(side="left", expand=True, fill="x", padx=(0, 3))

        self.ext_btn = ttk.Button(disp_row, text="📺  Extended Display",
                                   style="Browser.TButton", command=self._open_extended,
                                   state="disabled")
        self.ext_btn.pack(side="right", expand=True, fill="x", padx=(3, 0))

        self.dict_btn = ttk.Button(browser_frame, text="📝  Dictation (Voice-to-Text)",
                                    style="Browser.TButton", command=self._open_dictation,
                                    state="disabled")
        self.dict_btn.pack(fill="x", pady=(5, 0))

        # ── Settings ──
        settings_frame = ttk.LabelFrame(main, text="  Settings  ", padding=12)
        settings_frame.pack(fill="x", pady=(0, 10))

        # Transcript directory
        ttk.Label(settings_frame, text="Transcript Files:",
                  style="Section.TLabel").pack(anchor="w")
        tdir_row = ttk.Frame(settings_frame)
        tdir_row.pack(fill="x", pady=(2, 8))

        self.tdir_var = tk.StringVar(value=self.settings.get("transcripts_dir",
                                     str(DEFAULT_TRANSCRIPTS)))
        self.tdir_entry = ttk.Entry(tdir_row, textvariable=self.tdir_var,
                                     font=("Segoe UI", 10))
        self.tdir_entry.pack(side="left", fill="x", expand=True, padx=(0, 4))

        ttk.Button(tdir_row, text="📁 Browse",
                   command=self._browse_tdir).pack(side="right")

        # Microphone
        ttk.Label(settings_frame, text="Microphone:",
                  style="Section.TLabel").pack(anchor="w")
        self.mic_var = tk.StringVar(value="System Default")
        self.mic_combo = ttk.Combobox(settings_frame, textvariable=self.mic_var,
                                       state="readonly", font=("Segoe UI", 10))
        self.mic_combo.pack(fill="x", pady=(2, 8))
        self.mic_combo.bind("<<ComboboxSelected>>", self._on_mic_changed)
        self.mic_combo.bind("<ButtonPress-1>", self._on_mic_dropdown_open)
        self._refresh_mics()

        # Backend
        ttk.Label(settings_frame, text="Speech Backend:",
                  style="Section.TLabel").pack(anchor="w")
        self._backend_labels = {"auto": "auto", "whisper": "whisper (best)",
                                 "vosk": "vosk (CPU only)", "mlx": "mlx (Apple Silicon)"}
        self._backend_from_label = {v: k for k, v in self._backend_labels.items()}
        stored_backend = self.settings.get("backend", "auto")
        self.backend_var = tk.StringVar(value=self._backend_labels.get(stored_backend, stored_backend))
        backend_values = ["auto", "whisper (best)", "vosk (CPU only)"]
        if IS_MAC:
            backend_values.append("mlx (Apple Silicon)")
        backend_combo = ttk.Combobox(settings_frame, textvariable=self.backend_var,
                                      state="readonly", font=("Segoe UI", 10),
                                      values=backend_values)
        backend_combo.pack(fill="x", pady=(2, 8))

        ttk.Button(settings_frame, text="⬇  Download Language-Tuned Models",
                   command=self._show_tuned_models_dialog).pack(fill="x", pady=(0, 4))

        ttk.Button(settings_frame, text="⬇  Download Offline Translation Models",
                   command=self._show_offline_translate_dialog).pack(fill="x", pady=(0, 4))

        ttk.Button(settings_frame, text="🗑  Delete Installed Models",
                   command=self._show_model_manager_dialog).pack(fill="x", pady=(0, 0))

        # ── Log Area ──
        log_frame = ttk.LabelFrame(main, text="  Server Log  ", padding=(8, 6))
        log_frame.pack(fill="both", expand=True, pady=(0, 8))

        log_scroll = ttk.Scrollbar(log_frame, orient="vertical")
        self.log_text = tk.Text(log_frame, height=8, wrap="word",
                                 bg="#0a0a1a", fg="#7fdbca", insertbackground="#7fdbca",
                                 font=("Consolas" if IS_WIN else "Menlo", 10),
                                 relief="flat", padx=8, pady=6,
                                 state="disabled",
                                 yscrollcommand=log_scroll.set)
        log_scroll.configure(command=self.log_text.yview)
        log_scroll.pack(side="right", fill="y")
        self.log_text.pack(side="left", fill="both", expand=True)

        # Configure log colors
        self.log_text.tag_configure("info", foreground="#7fdbca")
        self.log_text.tag_configure("warn", foreground="#FFD54F")
        self.log_text.tag_configure("error", foreground="#ff6b6b")
        self.log_text.tag_configure("system", foreground="#4FC3F7")

        # ── Footer ──
        footer = ttk.Frame(main)
        footer.pack(fill="x")

        self.open_tdir_btn = ttk.Button(footer, text="📂 Open Transcripts",
                                         command=self._open_transcripts_dir)
        self.open_tdir_btn.pack(side="left")

        ttk.Button(footer, text="ℹ About",
                   command=self._show_about).pack(side="left", padx=(6, 0))

        ttk.Label(footer, text=f"v{VERSION}", style="Subtitle.TLabel").pack(side="right")

        # Welcome message
        self._log_system(f"LinguaTaxi v{VERSION}")
        self._log_system(f"App directory: {APP_DIR}")
        self._log_system(f"Transcripts: {self.tdir_var.get()}")
        self._log_system("Ready — click 'Start Server' to begin.")

    # ── Drawing ──

    def _draw_dot(self, color):
        self.status_dot.delete("all")
        self.status_dot.create_oval(2, 2, 10, 10, fill=color, outline="")

    # ── Microphone Management ──

    def _refresh_mics(self, preserve_selection=False):
        prev_idx = self._get_selected_mic_index() if preserve_selection else None
        mics = list_mics()
        self._mic_devices = mics
        names = ["System Default"] + [f"[{i}] {n}" for i, n in mics]
        self.mic_combo["values"] = names

        # Restore selection: either previous (on refresh) or saved (on init)
        target_idx = prev_idx if preserve_selection else self.settings.get("mic_index")
        if target_idx is not None:
            for j, (i, n) in enumerate(mics):
                if i == target_idx:
                    self.mic_combo.current(j + 1)
                    return
        self.mic_combo.current(0)

    def _on_mic_dropdown_open(self, event=None):
        """Re-query audio devices when the dropdown is clicked."""
        self._refresh_mics(preserve_selection=True)

    def _get_selected_mic_index(self):
        sel = self.mic_combo.current()
        if sel <= 0:
            return None  # System default
        return self._mic_devices[sel - 1][0]

    def _on_mic_changed(self, event=None):
        """When mic dropdown changes, push to running server via API."""
        if not self._server_running or not self._server_ready:
            return  # Will be applied at next server start via CLI args
        mic_idx = self._get_selected_mic_index()
        port = self.settings.get("operator_port", 3001)

        def _send():
            import urllib.request, urllib.parse
            try:
                data = urllib.parse.urlencode(
                    {"mic_index": mic_idx if mic_idx is not None else ""})
                req = urllib.request.Request(
                    f"http://localhost:{port}/api/set-mic",
                    data=data.encode(), method="POST")
                with urllib.request.urlopen(req, timeout=5) as resp:
                    import json as _json
                    body = _json.loads(resp.read())
                    if body.get("changed"):
                        name = body.get("mic_name", "default")
                        self.log_queue.put(("output",
                            f"  Microphone switched to: {name}"))
            except Exception as e:
                self.log_queue.put(("output",
                    f"  Mic change failed (will apply on restart): {e}"))

        threading.Thread(target=_send, daemon=True).start()

    # ── Server Management ──

    def _build_server_cmd(self):
        python = self._find_python()
        cmd = [python, str(SERVER_PY)]

        # Backend
        backend = self._backend_from_label.get(self.backend_var.get(), self.backend_var.get())
        if backend and backend != "auto":
            cmd.extend(["--backend", backend])

        # Microphone
        mic_idx = self._get_selected_mic_index()
        if mic_idx is not None:
            cmd.extend(["--mic", str(mic_idx)])

        # Transcripts directory
        tdir = self.tdir_var.get().strip()
        if tdir:
            cmd.extend(["--transcripts-dir", tdir])

        return cmd

    # ── First-Run Model Download ──

    def _needs_model_download(self):
        """Check if speech models are already present."""
        models_dir = APP_DIR / "models"

        # Check for Vosk models
        for item in (models_dir.iterdir() if models_dir.exists() else []):
            if item.is_dir() and "vosk-model" in item.name:
                return False

        # Check for Whisper models in HuggingFace cache
        hf_cache = Path.home() / ".cache" / "huggingface" / "hub"
        if hf_cache.exists():
            for item in hf_cache.iterdir():
                if item.is_dir() and "whisper" in item.name.lower():
                    return False

        return True

    def _download_models(self):
        """Show a progress dialog while downloading speech models."""
        dlg = tk.Toplevel(self)
        dlg.title("First-Time Setup")
        dlg.geometry("480x220")
        dlg.resizable(False, False)
        dlg.configure(bg=self.BG)
        dlg.transient(self)
        dlg.grab_set()

        # Center on parent
        dlg.update_idletasks()
        px = self.winfo_x() + (self.winfo_width() - 480) // 2
        py = self.winfo_y() + (self.winfo_height() - 220) // 2
        dlg.geometry(f"+{px}+{py}")

        f = ttk.Frame(dlg, padding=24)
        f.pack(fill="both", expand=True)

        ttk.Label(f, text="Downloading Speech Recognition Model",
                  font=("Segoe UI", 12, "bold"),
                  foreground=self.ACCENT, background=self.BG).pack(pady=(0, 8))

        status_var = tk.StringVar(value="Preparing download...")
        status_lbl = ttk.Label(f, textvariable=status_var,
                               style="Subtitle.TLabel", wraplength=420)
        status_lbl.pack(pady=(0, 12))

        progress = ttk.Progressbar(f, mode="indeterminate", length=420)
        progress.pack(pady=(0, 12))
        progress.start(15)

        hint = ttk.Label(f,
                         text="This only happens once. The model enables offline speech recognition.",
                         style="Subtitle.TLabel", wraplength=420)
        hint.pack()

        download_done = [False]

        def run_download():
            try:
                python = self._find_python()
                dl_script = APP_DIR / "download_models.py"

                if not dl_script.exists():
                    status_var.set("Model will download on first server start.")
                    download_done[0] = True
                    return

                kwargs = {}
                if IS_WIN:
                    kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

                proc = subprocess.Popen(
                    [python, str(dl_script)],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    universal_newlines=True,
                    cwd=str(APP_DIR),
                    **kwargs,
                )

                for line in iter(proc.stdout.readline, ""):
                    line = line.strip()
                    if line and not line.startswith("="):
                        clean = line.lstrip(" [")
                        if clean:
                            status_var.set(clean[:80])

                proc.wait()

            except Exception as e:
                status_var.set(f"Model will download on first server start.")

            finally:
                download_done[0] = True

        t = threading.Thread(target=run_download, daemon=True)
        t.start()

        def poll():
            if download_done[0]:
                progress.stop()
                dlg.destroy()
                return
            dlg.after(200, poll)

        poll()
        self.wait_window(dlg)

    # ── Tuned Models Dialog ──

    def _get_tuned_model_info(self):
        """Get tuned model info by running tuned_models.py --list."""
        models_dir = APP_DIR / "models"
        try:
            python = self._find_python()
            result = subprocess.run(
                [python, str(APP_DIR / "tuned_models.py"), "--list",
                 "--models-dir", str(models_dir)],
                capture_output=True, text=True, timeout=15,
                cwd=str(APP_DIR))
            if result.returncode == 0:
                return json.loads(result.stdout.strip())
        except Exception:
            pass
        return {}

    def _show_tuned_models_dialog(self):
        """Show dialog for downloading language-tuned Whisper models."""
        # Check tuned_models.py exists
        if not (APP_DIR / "tuned_models.py").exists():
            messagebox.showinfo("Not Available",
                "tuned_models.py not found.\n"
                "This feature requires the Full edition.",
                parent=self)
            return

        dlg = tk.Toplevel(self)
        dlg.title("Download Language-Tuned Models")
        dlg.geometry("520x480")
        dlg.minsize(400, 300)
        dlg.resizable(True, True)
        dlg.configure(bg=self.BG)
        dlg.transient(self)
        dlg.grab_set()

        # Center on parent
        dlg.update_idletasks()
        px = self.winfo_x() + (self.winfo_width() - 520) // 2
        py = self.winfo_y() + (self.winfo_height() - 480) // 2
        dlg.geometry(f"+{px}+{py}")

        f = ttk.Frame(dlg, padding=20)
        f.pack(fill="both", expand=True)

        ttk.Label(f, text="Download Language-Tuned Models",
                  font=("Segoe UI", 13, "bold"),
                  foreground=self.ACCENT, background=self.BG).pack(pady=(0, 4))

        ttk.Label(f, text="Fine-tuned Whisper models with better accuracy for\n"
                  "specific languages. Used automatically when you select\n"
                  "the matching input language in the operator panel.",
                  style="Subtitle.TLabel", justify="center",
                  wraplength=460).pack(pady=(0, 12))

        # Get current status
        model_info = self._get_tuned_model_info()

        # Fallback if tuned_models.py can't be reached
        if not model_info:
            model_info = {
                "ES": {"name": "Spanish (Turbo)", "size_gb": 1.6, "available": False},
                "FR": {"name": "French", "size_gb": 3.1, "available": False},
                "DE": {"name": "German", "size_gb": 3.1, "available": False},
                "AR": {"name": "Arabic", "size_gb": 3.1, "available": False},
                "JA": {"name": "Japanese", "size_gb": 1.5, "available": False},
                "ZH": {"name": "Chinese", "size_gb": 3.1, "available": False},
            }

        # Scrollable checkbox area
        cb_canvas = tk.Canvas(f, bg=self.BG, highlightthickness=0)
        cb_scrollbar = ttk.Scrollbar(f, orient="vertical", command=cb_canvas.yview)
        cb_frame = ttk.Frame(cb_canvas)
        cb_frame.bind("<Configure>",
                      lambda e: cb_canvas.configure(scrollregion=cb_canvas.bbox("all")))
        cb_canvas.create_window((0, 0), window=cb_frame, anchor="nw",
                                tags="inner")
        cb_canvas.configure(yscrollcommand=cb_scrollbar.set)

        # Resize inner frame width when canvas resizes
        def _resize_cb(event):
            cb_canvas.itemconfig("inner", width=event.width)
        cb_canvas.bind("<Configure>", _resize_cb)

        cb_canvas.pack(side="top", fill="both", expand=True, pady=(0, 8))
        cb_scrollbar.pack(in_=f, side="right", fill="y", before=cb_canvas)

        def _cb_mousewheel(event):
            cb_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        cb_canvas.bind_all("<MouseWheel>", _cb_mousewheel)
        dlg.bind("<Destroy>", lambda e: cb_canvas.unbind_all("<MouseWheel>") if e.widget == dlg else None)

        check_vars = {}
        cb_widgets = {}
        for lang, info in model_info.items():
            var = tk.BooleanVar(value=False)
            check_vars[lang] = var

            name = info.get("name", lang)
            size = info.get("size_gb", "?")
            avail = info.get("available", False)

            if avail:
                row = ttk.Frame(cb_frame)
                row.pack(anchor="w", pady=2, fill="x")
                tk.Label(row, text=" \u2713 ", fg="#66BB6A", bg=self.BG,
                         font=("Segoe UI", 10, "bold")).pack(side="left")
                tk.Label(row, text=f"{name} \u2014 ~{size} GB  ",
                         fg=self.FG, bg=self.BG,
                         font=("Segoe UI", 9)).pack(side="left")
                tk.Label(row, text="Installed", fg="#66BB6A", bg=self.BG,
                         font=("Segoe UI", 9, "bold")).pack(side="left")
                cb_widgets[lang] = None
            else:
                text = f"{name} \u2014 ~{size} GB"
                cb = ttk.Checkbutton(cb_frame, text=text, variable=var)
                cb.pack(anchor="w", pady=2)
                cb_widgets[lang] = cb

        # Buttons (fixed at bottom)
        btn_frame = ttk.Frame(f)
        btn_frame.pack(fill="x", pady=(0, 8))

        dl_btn = ttk.Button(btn_frame, text="Download Selected",
                            style="Start.TButton",
                            command=lambda: _start_download())
        dl_btn.pack(side="left", padx=(0, 8))

        close_btn = ttk.Button(btn_frame, text="Close",
                               command=dlg.destroy)
        close_btn.pack(side="right")

        # Progress area (fixed at bottom)
        prog_frame = ttk.Frame(f)
        prog_frame.pack(fill="x", pady=(8, 0))

        progress_bar = ttk.Progressbar(prog_frame, mode="determinate")
        progress_bar.pack_forget()

        status_var = tk.StringVar(value="Select languages and click Download.")
        status_label = ttk.Label(prog_frame, textvariable=status_var,
                                 style="Subtitle.TLabel", wraplength=460)
        status_label.pack(fill="x")

        hint_label = ttk.Label(prog_frame,
                               text="Requires transformers and torch packages in the venv.\n"
                               "Included in Full installer. Each model takes 5-30 minutes.",
                               style="Subtitle.TLabel", wraplength=460)
        hint_label.pack(fill="x", pady=(8, 0))

        dl_queue = queue.Queue()

        def _start_download():
            selected = [lang for lang, var in check_vars.items()
                        if var.get() and not model_info.get(lang, {}).get("available")]
            if not selected:
                messagebox.showinfo("No Selection",
                    "Please select at least one language to download.",
                    parent=dlg)
                return

            dl_btn.configure(state="disabled")
            close_btn.configure(state="disabled")
            for cb in cb_widgets.values():
                if cb:
                    cb.configure(state="disabled")
            progress_bar.pack(fill="x", pady=(0, 4))
            progress_bar["value"] = 0
            status_var.set("Starting download...")

            python = self._find_python()
            models_dir = APP_DIR / "models"
            cmd = [python, str(APP_DIR / "tuned_models.py"),
                   "--download"] + selected + [
                   "--models-dir", str(models_dir)]

            kwargs = {}
            if IS_WIN:
                kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                universal_newlines=True, cwd=str(APP_DIR),
                env={**os.environ, "PYTHONUNBUFFERED": "1"},
                **kwargs)

            total = len(selected)

            def _read_output():
                completed = 0
                succeeded = 0
                failed = 0
                errors = []
                last_output = []
                for line in iter(proc.stdout.readline, ""):
                    line = line.strip()
                    if not line:
                        continue
                    if line.startswith("PROGRESS:"):
                        parts = line.split(":", 4)
                        if len(parts) >= 5:
                            lang_code = parts[1]
                            pct_str = parts[3]
                            msg = parts[4]
                            try:
                                pct = int(pct_str)
                                overall = int((completed * 100 + pct) / total)
                                dl_queue.put(("progress", overall,
                                              f"[{lang_code}] {msg}"))
                            except ValueError:
                                dl_queue.put(("status", 0, msg))
                    elif line.startswith("DONE:"):
                        parts = line.split(":", 3)
                        if len(parts) >= 3:
                            completed += 1
                            lang_code = parts[1]
                            ok = parts[2] == "ok"
                            msg = parts[3] if len(parts) > 3 else ""
                            if ok:
                                succeeded += 1
                                dl_queue.put(("done_ok", lang_code, msg))
                            else:
                                failed += 1
                                errors.append(msg)
                                dl_queue.put(("done_err", lang_code, msg))
                    else:
                        last_output.append(line)
                        if len(last_output) > 10:
                            last_output.pop(0)
                proc.wait()
                if completed == 0 and proc.returncode != 0:
                    err_msg = last_output[-1] if last_output else f"Process exited with code {proc.returncode}"
                    dl_queue.put(("finished_err", 0, f"Download failed: {err_msg}"))
                elif failed > 0 and succeeded == 0:
                    summary = f"Download failed: {errors[0]}" if errors else "Download failed."
                    dl_queue.put(("finished_err", 0, summary))
                elif failed > 0:
                    dl_queue.put(("finished_partial", succeeded,
                                  f"{succeeded} downloaded, {failed} failed"))
                else:
                    dl_queue.put(("finished", 0, ""))

            t = threading.Thread(target=_read_output, daemon=True)
            t.start()

            def _poll():
                try:
                    while True:
                        msg_type, val, msg = dl_queue.get_nowait()
                        if msg_type == "progress":
                            progress_bar["value"] = val
                            status_var.set(msg)
                        elif msg_type == "status":
                            status_var.set(msg)
                        elif msg_type == "done_ok":
                            # Mark as downloaded
                            lang_code = val
                            if lang_code in model_info:
                                model_info[lang_code]["available"] = True
                        elif msg_type == "done_err":
                            lang_code = val
                            status_var.set(f"[{lang_code}] Error: {msg}")
                        elif msg_type in ("finished", "finished_err", "finished_partial"):
                            if msg_type == "finished":
                                progress_bar["value"] = 100
                                status_var.set("Download complete!")
                            elif msg_type == "finished_err":
                                progress_bar["value"] = 0
                                status_var.set(msg)
                            else:
                                progress_bar["value"] = 100
                                status_var.set(msg)
                            dl_btn.configure(state="normal")
                            close_btn.configure(state="normal")
                            # Update checkboxes
                            for lang, info in model_info.items():
                                cb = cb_widgets.get(lang)
                                if cb and info.get("available"):
                                    cb.configure(state="disabled")
                                    check_vars[lang].set(False)
                                elif cb:
                                    cb.configure(state="normal")
                            return
                except queue.Empty:
                    pass
                dlg.after(200, _poll)

            _poll()

    # ── Offline Translation Models Dialog ──

    def _get_offline_translate_info(self):
        """Get offline translation model info by running offline_translate.py --list."""
        models_dir = APP_DIR / "models"
        try:
            python = self._find_python()
            result = subprocess.run(
                [python, str(APP_DIR / "offline_translate.py"), "--list",
                 "--models-dir", str(models_dir)],
                capture_output=True, text=True, timeout=15,
                cwd=str(APP_DIR))
            if result.returncode == 0:
                return json.loads(result.stdout.strip())
        except Exception:
            pass
        return {}

    def _show_offline_translate_dialog(self):
        """Show dialog for downloading offline translation models."""
        if not (APP_DIR / "offline_translate.py").exists():
            messagebox.showinfo("Not Available",
                "offline_translate.py not found.",
                parent=self)
            return

        dlg = tk.Toplevel(self)
        dlg.title("Download Offline Translation Models")
        dlg.geometry("560x580")
        dlg.minsize(440, 350)
        dlg.resizable(True, True)
        dlg.configure(bg=self.BG)
        dlg.transient(self)
        dlg.grab_set()

        dlg.update_idletasks()
        px = self.winfo_x() + (self.winfo_width() - 560) // 2
        py = self.winfo_y() + (self.winfo_height() - 580) // 2
        dlg.geometry(f"+{px}+{py}")

        f = ttk.Frame(dlg, padding=20)
        f.pack(fill="both", expand=True)

        ttk.Label(f, text="Download Offline Translation Models",
                  font=("Segoe UI", 13, "bold"),
                  foreground=self.ACCENT, background=self.BG).pack(pady=(0, 4))

        ttk.Label(f, text="Translate without internet or DeepL credits.\n"
                  "OPUS-MT: fast, best for European languages (~310 MB download each)\n"
                  "M2M-100: covers 100 languages (~4.8 GB download, slower)",
                  style="Subtitle.TLabel", justify="center",
                  wraplength=500).pack(pady=(0, 12))

        model_info = self._get_offline_translate_info()

        opus_models = model_info.get("opus", {})
        m2m_info = model_info.get("m2m100", {})

        # Fallback data if script unavailable
        if not opus_models:
            opus_models = {
                "ES": {"name": "Spanish", "size_mb": 310, "available": False},
                "FR": {"name": "French", "size_mb": 310, "available": False},
                "DE": {"name": "German", "size_mb": 310, "available": False},
                "IT": {"name": "Italian", "size_mb": 310, "available": False},
                "RU": {"name": "Russian", "size_mb": 310, "available": False},
                "PL": {"name": "Polish", "size_mb": 310, "available": False},
            }
        if not m2m_info:
            m2m_info = {"name": "M2M-100 Multilingual", "size_mb": 4800, "available": False}

        # Scrollable model list area
        ol_canvas = tk.Canvas(f, bg=self.BG, highlightthickness=0)
        ol_scrollbar = ttk.Scrollbar(f, orient="vertical", command=ol_canvas.yview)
        ol_inner = ttk.Frame(ol_canvas)
        ol_inner.bind("<Configure>",
                      lambda e: ol_canvas.configure(scrollregion=ol_canvas.bbox("all")))
        ol_canvas.create_window((0, 0), window=ol_inner, anchor="nw", tags="inner")
        ol_canvas.configure(yscrollcommand=ol_scrollbar.set)

        def _resize_ol(event):
            ol_canvas.itemconfig("inner", width=event.width)
        ol_canvas.bind("<Configure>", _resize_ol)

        ol_canvas.pack(side="top", fill="both", expand=True, pady=(0, 8))
        ol_scrollbar.pack(in_=f, side="right", fill="y", before=ol_canvas)

        def _ol_mousewheel(event):
            ol_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        ol_canvas.bind_all("<MouseWheel>", _ol_mousewheel)
        dlg.bind("<Destroy>", lambda e: ol_canvas.unbind_all("<MouseWheel>") if e.widget == dlg else None)

        # OPUS-MT section
        ttk.Label(ol_inner, text="OPUS-MT (per-language, fast):",
                  style="Section.TLabel").pack(anchor="w", pady=(4, 2))

        opus_frame = ttk.Frame(ol_inner)
        opus_frame.pack(fill="x", pady=(0, 8))

        opus_vars = {}
        opus_cbs = {}
        popular = ["ES", "FR", "DE", "IT", "RU", "PL", "NL", "SV", "TR", "UK"]
        for lang in popular:
            info = opus_models.get(lang)
            if not info:
                continue
            var = tk.BooleanVar(value=False)
            opus_vars[lang] = var
            name = info.get("name", lang)
            size = info.get("size_mb", 310)
            avail = info.get("available", False)

            if avail:
                row = ttk.Frame(opus_frame)
                row.pack(anchor="w", pady=1, fill="x")
                tk.Label(row, text=" \u2713 ", fg="#66BB6A", bg=self.BG,
                         font=("Segoe UI", 9, "bold")).pack(side="left")
                tk.Label(row, text=f"{name} ({lang}) \u2014 ~{size} MB download  ",
                         fg=self.FG, bg=self.BG,
                         font=("Segoe UI", 9)).pack(side="left")
                tk.Label(row, text="Installed", fg="#66BB6A", bg=self.BG,
                         font=("Segoe UI", 9, "bold")).pack(side="left")
                opus_cbs[lang] = None
            else:
                text = f"{name} ({lang}) \u2014 ~{size} MB download"
                cb = ttk.Checkbutton(opus_frame, text=text, variable=var)
                cb.pack(anchor="w", pady=1)
                opus_cbs[lang] = cb

        # M2M-100 section
        ttk.Label(ol_inner, text="M2M-100 (all languages, larger):",
                  style="Section.TLabel").pack(anchor="w", pady=(8, 2))

        m2m_frame = ttk.Frame(ol_inner)
        m2m_frame.pack(fill="x", pady=(0, 8))

        m2m_var = tk.BooleanVar(value=False)
        m2m_name = m2m_info.get("name", "M2M-100")
        m2m_size = m2m_info.get("size_mb", 4800)
        m2m_size_str = f"{m2m_size / 1000:.1f} GB" if m2m_size >= 1000 else f"{m2m_size} MB"
        m2m_avail = m2m_info.get("available", False)
        m2m_cb = None
        if m2m_avail:
            row = ttk.Frame(m2m_frame)
            row.pack(anchor="w", fill="x")
            tk.Label(row, text=" \u2713 ", fg="#66BB6A", bg=self.BG,
                     font=("Segoe UI", 9, "bold")).pack(side="left")
            tk.Label(row, text=f"{m2m_name} \u2014 ~{m2m_size_str}  ",
                     fg=self.FG, bg=self.BG,
                     font=("Segoe UI", 9)).pack(side="left")
            tk.Label(row, text="Installed", fg="#66BB6A", bg=self.BG,
                     font=("Segoe UI", 9, "bold")).pack(side="left")
        else:
            m2m_text = f"{m2m_name} \u2014 ~{m2m_size_str} download (covers Arabic, Japanese, Chinese, Korean, etc.)"
            m2m_cb = ttk.Checkbutton(m2m_frame, text=m2m_text, variable=m2m_var)
            m2m_cb.pack(anchor="w")

        # Buttons (fixed at bottom)
        btn_frame = ttk.Frame(f)
        btn_frame.pack(fill="x", pady=(8, 4))

        dl_btn = ttk.Button(btn_frame, text="Download Selected",
                            style="Start.TButton",
                            command=lambda: _start_download())
        dl_btn.pack(side="left", padx=(0, 8))

        close_btn = ttk.Button(btn_frame, text="Close",
                               command=dlg.destroy)
        close_btn.pack(side="right")

        # Progress (fixed at bottom)
        prog_frame = ttk.Frame(f)
        prog_frame.pack(fill="x", pady=(8, 0))

        progress_bar = ttk.Progressbar(prog_frame, mode="determinate")
        progress_bar.pack_forget()

        status_var = tk.StringVar(value="Select models and click Download.")
        status_label = ttk.Label(prog_frame, textvariable=status_var,
                                 style="Subtitle.TLabel", wraplength=500)
        status_label.pack(fill="x")

        ttk.Label(prog_frame,
                  text="Requires transformers, torch, and ctranslate2.\n"
                  "Included in Full installer. OPUS-MT: ~5 min each, M2M-100: ~30-60 min.",
                  style="Subtitle.TLabel", wraplength=500).pack(fill="x", pady=(8, 0))

        dl_queue = queue.Queue()

        def _start_download():
            # Collect selections
            opus_selected = [lang for lang, var in opus_vars.items()
                           if var.get() and not opus_models.get(lang, {}).get("available")]
            want_m2m = m2m_var.get() and not m2m_avail

            if not opus_selected and not want_m2m:
                messagebox.showinfo("No Selection",
                    "Please select at least one model to download.",
                    parent=dlg)
                return

            dl_btn.configure(state="disabled")
            close_btn.configure(state="disabled")
            for cb in opus_cbs.values():
                if cb:
                    cb.configure(state="disabled")
            if m2m_cb:
                m2m_cb.configure(state="disabled")
            progress_bar.pack(fill="x", pady=(0, 4))
            progress_bar["value"] = 0
            status_var.set("Starting download...")

            python = self._find_python()
            models_dir = APP_DIR / "models"

            # Build command — download OPUS models first, then M2M
            cmds = []
            if opus_selected:
                cmds.append([python, str(APP_DIR / "offline_translate.py"),
                            "--download-opus"] + opus_selected +
                           ["--models-dir", str(models_dir)])
            if want_m2m:
                cmds.append([python, str(APP_DIR / "offline_translate.py"),
                            "--download-m2m",
                            "--models-dir", str(models_dir)])

            total_steps = len(opus_selected) + (1 if want_m2m else 0)

            kwargs = {}
            if IS_WIN:
                kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

            def _run_cmds():
                completed = 0
                succeeded = 0
                failed = 0
                errors = []
                last_output = []  # Capture non-PROGRESS/DONE output for diagnostics
                for cmd in cmds:
                    try:
                        proc = subprocess.Popen(
                            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            universal_newlines=True, cwd=str(APP_DIR),
                            env={**os.environ, "PYTHONUNBUFFERED": "1"},
                            **kwargs)
                    except Exception as e:
                        failed += total_steps
                        errors.append(f"Failed to start: {e}")
                        dl_queue.put(("done_err", "process", str(e)))
                        continue

                    for line in iter(proc.stdout.readline, ""):
                        line = line.strip()
                        if not line:
                            continue
                        if line.startswith("PROGRESS:"):
                            parts = line.split(":", 4)
                            if len(parts) >= 5:
                                pkey = parts[1]
                                msg = parts[4]
                                try:
                                    pct = int(parts[3])
                                    overall = int((completed * 100 + pct) / total_steps)
                                    dl_queue.put(("progress", overall, f"[{pkey}] {msg}"))
                                except ValueError:
                                    dl_queue.put(("status", 0, msg))
                        elif line.startswith("DONE:"):
                            parts = line.split(":", 3)
                            if len(parts) >= 3:
                                completed += 1
                                dkey = parts[1]
                                ok = parts[2] == "ok"
                                msg = parts[3] if len(parts) > 3 else ""
                                if ok:
                                    succeeded += 1
                                    dl_queue.put(("done_ok", dkey, msg))
                                else:
                                    failed += 1
                                    errors.append(msg)
                                    dl_queue.put(("done_err", dkey, msg))
                        else:
                            last_output.append(line)
                            if len(last_output) > 10:
                                last_output.pop(0)
                    proc.wait()

                    # Detect subprocess crash (non-zero exit with no DONE lines)
                    if proc.returncode != 0 and completed == 0:
                        failed += 1
                        err_detail = last_output[-1] if last_output else f"Process exited with code {proc.returncode}"
                        errors.append(err_detail)
                        dl_queue.put(("done_err", "process", err_detail))

                if completed == 0 and failed == 0:
                    # No DONE lines at all — subprocess likely crashed silently
                    err_msg = last_output[-1] if last_output else "Download process produced no output"
                    dl_queue.put(("finished_err", 0, f"Download failed: {err_msg}"))
                elif failed > 0 and succeeded == 0:
                    summary = f"Download failed: {errors[0]}" if errors else "Download failed."
                    dl_queue.put(("finished_err", 0, summary))
                elif failed > 0:
                    dl_queue.put(("finished_partial", succeeded,
                                  f"{succeeded} downloaded, {failed} failed"))
                else:
                    dl_queue.put(("finished", 0, ""))

            t = threading.Thread(target=_run_cmds, daemon=True)
            t.start()

            def _poll():
                try:
                    while True:
                        msg_type, val, msg = dl_queue.get_nowait()
                        if msg_type == "progress":
                            progress_bar["value"] = val
                            status_var.set(msg)
                        elif msg_type == "status":
                            status_var.set(msg)
                        elif msg_type == "done_ok":
                            pass  # Individual model done
                        elif msg_type == "done_err":
                            status_var.set(f"[{val}] Error: {msg}")
                        elif msg_type in ("finished", "finished_err", "finished_partial"):
                            if msg_type == "finished":
                                progress_bar["value"] = 100
                                status_var.set("Download complete!")
                            elif msg_type == "finished_err":
                                progress_bar["value"] = 0
                                status_var.set(msg)
                            else:
                                progress_bar["value"] = 100
                                status_var.set(msg)
                            dl_btn.configure(state="normal")
                            close_btn.configure(state="normal")
                            # Refresh status
                            new_info = self._get_offline_translate_info()
                            new_opus = new_info.get("opus", {})
                            new_m2m = new_info.get("m2m100", {})
                            for lang, cb in opus_cbs.items():
                                if not cb:
                                    continue
                                if new_opus.get(lang, {}).get("available"):
                                    cb.configure(state="disabled")
                                    opus_vars[lang].set(False)
                                else:
                                    cb.configure(state="normal")
                            if m2m_cb:
                                if new_m2m.get("available"):
                                    m2m_cb.configure(state="disabled")
                                    m2m_var.set(False)
                                else:
                                    m2m_cb.configure(state="normal")
                            return
                except queue.Empty:
                    pass
                dlg.after(200, _poll)

            _poll()

    # ── Model Manager Dialog ──

    def _show_model_manager_dialog(self):
        """Show dialog to view, update, and delete installed models."""
        dlg = tk.Toplevel(self)
        dlg.title("Delete Installed Models")
        dlg.geometry("680x620")
        dlg.minsize(500, 350)
        dlg.resizable(True, True)
        dlg.configure(bg=self.BG)
        dlg.transient(self)
        dlg.grab_set()

        dlg.update_idletasks()
        px = self.winfo_x() + (self.winfo_width() - 680) // 2
        py = self.winfo_y() + (self.winfo_height() - 620) // 2
        dlg.geometry(f"+{px}+{py}")

        f = ttk.Frame(dlg, padding=16)
        f.pack(fill="both", expand=True)

        ttk.Label(f, text="Delete Installed Models",
                  font=("Segoe UI", 13, "bold"),
                  foreground=self.ACCENT, background=self.BG).pack(pady=(0, 4))

        status_var = tk.StringVar(value="Loading model information...")
        status_lbl = ttk.Label(f, textvariable=status_var,
                               style="Subtitle.TLabel", wraplength=560)
        status_lbl.pack(fill="x", pady=(0, 8))

        # Scrollable list
        canvas = tk.Canvas(f, bg=self.BG, highlightthickness=0)
        scrollbar = ttk.Scrollbar(f, orient="vertical", command=canvas.yview)
        list_frame = ttk.Frame(canvas)

        list_frame.bind("<Configure>",
                        lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=list_frame, anchor="nw", tags="inner")
        canvas.configure(yscrollcommand=scrollbar.set)

        # Resize inner frame width when canvas resizes
        def _resize_mgr(event):
            canvas.itemconfig("inner", width=event.width)
        canvas.bind("<Configure>", _resize_mgr)

        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        # Mousewheel scrolling
        def _on_mousewheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        canvas.bind_all("<MouseWheel>", _on_mousewheel)
        dlg.bind("<Destroy>", lambda e: canvas.unbind_all("<MouseWheel>") if e.widget == dlg else None)

        # Button frame
        btn_frame = ttk.Frame(f)
        btn_frame.pack(fill="x", pady=(8, 0))

        total_var = tk.StringVar(value="")
        ttk.Label(btn_frame, textvariable=total_var,
                  style="Subtitle.TLabel").pack(side="left")

        ttk.Button(btn_frame, text="Refresh",
                   command=lambda: _populate()).pack(side="right", padx=(8, 0))
        ttk.Button(btn_frame, text="Close",
                   command=dlg.destroy).pack(side="right")

        python = self._find_python()
        models_dir = APP_DIR / "models"

        def _fmt_size(size_bytes):
            """Format byte count as human-readable string."""
            if size_bytes <= 0:
                return "—"
            if size_bytes >= 1024 ** 3:
                return f"{size_bytes / (1024**3):.1f} GB"
            return f"{size_bytes / (1024**2):.0f} MB"

        def _get_speech_models():
            """Detect installed speech recognition models."""
            results = []
            # Whisper model
            whisper_dir = models_dir / "faster-whisper-large-v3-turbo"
            if (whisper_dir / "model.bin").exists():
                size = sum(f.stat().st_size for f in whisper_dir.rglob("*") if f.is_file())
                results.append({"name": "Whisper large-v3-turbo (GPU)", "path": whisper_dir,
                                "size": size, "type": "speech", "key": "whisper"})
            # Vosk models
            for vdir in sorted(models_dir.glob("vosk-model-*")):
                if vdir.is_dir():
                    size = sum(f.stat().st_size for f in vdir.rglob("*") if f.is_file())
                    results.append({"name": f"Vosk {vdir.name} (CPU)", "path": vdir,
                                    "size": size, "type": "speech", "key": vdir.name})
            return results

        def _get_tuned_models():
            """Get tuned model status."""
            try:
                result = subprocess.run(
                    [python, str(APP_DIR / "tuned_models.py"), "--list",
                     "--models-dir", str(models_dir)],
                    capture_output=True, text=True, timeout=15, cwd=str(APP_DIR))
                if result.returncode == 0:
                    return json.loads(result.stdout.strip())
            except Exception:
                pass
            return {}

        def _get_translate_models():
            """Get offline translation model status."""
            try:
                result = subprocess.run(
                    [python, str(APP_DIR / "offline_translate.py"), "--list",
                     "--models-dir", str(models_dir)],
                    capture_output=True, text=True, timeout=15, cwd=str(APP_DIR))
                if result.returncode == 0:
                    return json.loads(result.stdout.strip())
            except Exception:
                pass
            return {}

        def _delete_model(model_type, key, name):
            """Delete a model and refresh the list."""
            if not messagebox.askyesno("Delete Model",
                    f"Delete {name}?\n\nThis cannot be undone. "
                    f"You'll need to re-download to use it again.",
                    parent=dlg):
                return

            kwargs = {}
            if IS_WIN:
                kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

            try:
                if model_type == "speech":
                    # Direct delete for speech models
                    path = models_dir / key
                    if path.exists():
                        shutil.rmtree(path)
                    status_var.set(f"Deleted {name}")
                elif model_type == "tuned":
                    subprocess.run(
                        [python, str(APP_DIR / "tuned_models.py"),
                         "--delete", key, "--models-dir", str(models_dir)],
                        capture_output=True, timeout=30, cwd=str(APP_DIR), **kwargs)
                    status_var.set(f"Deleted {name}")
                elif model_type == "opus":
                    subprocess.run(
                        [python, str(APP_DIR / "offline_translate.py"),
                         "--delete-opus", key, "--models-dir", str(models_dir)],
                        capture_output=True, timeout=30, cwd=str(APP_DIR), **kwargs)
                    status_var.set(f"Deleted {name}")
                elif model_type == "m2m":
                    subprocess.run(
                        [python, str(APP_DIR / "offline_translate.py"),
                         "--delete-m2m", "--models-dir", str(models_dir)],
                        capture_output=True, timeout=30, cwd=str(APP_DIR), **kwargs)
                    status_var.set(f"Deleted {name}")
            except Exception as e:
                status_var.set(f"Delete failed: {e}")
                return

            _populate()

        def _add_section_header(parent, text):
            """Add a section header label."""
            lbl = tk.Label(parent, text=text, fg=self.ACCENT, bg=self.BG,
                           font=("Segoe UI", 11, "bold"), anchor="w")
            lbl.pack(fill="x", pady=(8, 2))
            sep = ttk.Separator(parent, orient="horizontal")
            sep.pack(fill="x", pady=(0, 4))

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

        def _populate():
            """Load and display all model info."""
            # Clear existing
            for widget in list_frame.winfo_children():
                widget.destroy()

            total_bytes = 0

            # Speech models
            _add_section_header(list_frame, "Speech Recognition Models")
            speech = _get_speech_models()
            if speech:
                for m in speech:
                    total_bytes += m["size"]
                    _add_model_row(list_frame, m["name"], _fmt_size(m["size"]),
                                   "speech", m["key"])
            else:
                tk.Label(list_frame, text="  No speech models installed",
                         fg="#666", bg=self.BG, font=("Segoe UI", 9, "italic")).pack(anchor="w")

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

            # Translation models
            _add_section_header(list_frame, "Offline Translation Models")
            translate = _get_translate_models()
            opus = translate.get("opus", {})
            m2m = translate.get("m2m100", {})

            # OPUS-MT
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

            # M2M-100
            if m2m and m2m.get("available", False):
                m2m_name = m2m.get("name", "M2M-100")
                m2m_dir = models_dir / "translate" / "m2m100-1.2b"
                size = sum(f.stat().st_size for f in m2m_dir.rglob("*") if f.is_file()) if m2m_dir.exists() else 0
                total_bytes += size
                _add_model_row(list_frame, m2m_name, _fmt_size(size), "m2m", "m2m100")

            if not translate or (not has_opus and not (m2m and m2m.get("available", False))):
                tk.Label(list_frame, text="  No offline translation models installed",
                         fg="#666", bg=self.BG, font=("Segoe UI", 9, "italic")).pack(anchor="w")

            # Check for leftover HF cache
            hf_cache = models_dir / "translate" / "_hf_cache"
            if hf_cache.exists():
                cache_size = sum(f.stat().st_size for f in hf_cache.rglob("*") if f.is_file())
                if cache_size > 0:
                    total_bytes += cache_size
                    _add_section_header(list_frame, "Temporary Cache")
                    _add_model_row(list_frame, "HuggingFace download cache",
                                   _fmt_size(cache_size), "speech", "translate/_hf_cache")

            total_var.set(f"Total disk usage: {_fmt_size(total_bytes)}")
            status_var.set(f"Found {len(speech)} speech, "
                          f"{sum(1 for i in tuned.values() if i.get('available'))} tuned, "
                          f"{sum(1 for i in opus.values() if i.get('available'))} translation models")
            canvas.yview_moveto(0)

        # Run initial populate in a thread to avoid blocking UI
        def _bg_populate():
            dlg.after(100, _populate)

        _bg_populate()

    def _find_python(self):
        """Find the Python executable for running scripts."""
        if IS_WIN:
            venv_py = APP_DIR / "venv" / "Scripts" / "python.exe"
        else:
            venv_py = APP_DIR / "venv" / "bin" / "python3"
        if venv_py.exists():
            return str(venv_py)
        return sys.executable

    # ── Server Management ──

    def _start_server(self):
        if self._server_running:
            return

        # First-run: download speech model if needed
        if self._needs_model_download():
            self._log_system("First run — downloading speech recognition model...")
            self._download_models()
            self._log_system("Model setup complete.")

        # Save settings
        self._save_current_settings()

        # Ensure transcript dir exists
        tdir = Path(self.tdir_var.get().strip())
        try:
            tdir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            messagebox.showerror("Error", f"Cannot create transcript directory:\n{e}")
            return

        cmd = self._build_server_cmd()
        self._log_system(f"Starting: {' '.join(cmd)}")

        try:
            # Use CREATE_NO_WINDOW on Windows to avoid console flash
            kwargs = {}
            if IS_WIN:
                kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

            self.server_proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                bufsize=1,
                universal_newlines=True,
                cwd=str(APP_DIR),
                env={**os.environ, "PYTHONUNBUFFERED": "1",
                     "LINGUATAXI_TRANSCRIPTS": self.tdir_var.get().strip()},
                **kwargs,
            )

            self._server_running = True
            self._server_ready = False
            self._update_ui_state(running=True)

            # Start log reader thread
            t = threading.Thread(target=self._read_server_output, daemon=True)
            t.start()

            # Start HTTP readiness check (backup for log detection)
            threading.Thread(target=self._check_server_readiness, daemon=True).start()

        except FileNotFoundError:
            self._log_error("Python not found. Please install Python 3.10+ or reinstall LinguaTaxi.")
        except Exception as e:
            self._log_error(f"Failed to start server: {e}")

    def _stop_server(self):
        if not self._server_running or not self.server_proc:
            return

        self._log_system("Stopping server...")

        try:
            if IS_WIN:
                # Graceful shutdown on Windows
                self.server_proc.terminate()
            else:
                self.server_proc.send_signal(signal.SIGINT)

            # Wait up to 5 seconds
            try:
                self.server_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.server_proc.kill()
                self.server_proc.wait(timeout=3)
        except Exception as e:
            self._log_error(f"Error stopping server: {e}")
            try:
                self.server_proc.kill()
            except Exception:
                pass

        self._server_running = False
        self._server_ready = False
        self.server_proc = None
        self._update_ui_state(running=False)
        self._log_system("Server stopped.")

    def _read_server_output(self):
        """Read server stdout in a background thread."""
        proc = self.server_proc
        try:
            for line in iter(proc.stdout.readline, ""):
                if not line or not self._server_running:
                    break
                line = line.rstrip("\n\r")
                if line:
                    self.log_queue.put(("output", line))

                    # Detect backend info
                    if "Backend:" in line or "backend:" in line.lower():
                        self.log_queue.put(("backend", line))
                    # Detect ready state (server prints "Ctrl+C to stop"
                    # right before starting uvicorn threads; uvicorn's own
                    # "Uvicorn running" is suppressed at log_level=warning)
                    if "Ctrl+C to stop" in line or "Uvicorn running" in line:
                        self.log_queue.put(("ready", True))
        except Exception:
            pass
        finally:
            # Server exited
            if self._server_running:
                self.log_queue.put(("stopped", None))

    def _poll_log_queue(self):
        """Process log messages from the server thread (runs on main thread)."""
        try:
            while True:
                msg_type, data = self.log_queue.get_nowait()

                if msg_type == "output":
                    # Determine tag
                    tag = "info"
                    lower = data.lower()
                    if "error" in lower or "failed" in lower or "exception" in lower:
                        tag = "error"
                    elif "warn" in lower:
                        tag = "warn"
                    self._append_log(data, tag)

                elif msg_type == "backend":
                    # Extract backend name
                    m = re.search(r'Backend:\s*(.+)', data, re.IGNORECASE)
                    if m:
                        self.backend_label.configure(text=m.group(1).strip())

                elif msg_type == "ready":
                    if not self._server_ready:
                        self._server_ready = True
                        self._draw_dot(self.GREEN)
                        self.status_label.configure(text="Running", foreground=self.GREEN)
                        self._log_system("Server is ready!")

                elif msg_type == "stopped":
                    self._server_running = False
                    self._server_ready = False
                    self.server_proc = None
                    self._update_ui_state(running=False)
                    self._log_system("Server process ended.")

        except queue.Empty:
            pass

        if not self._closing:
            self.after(100, self._poll_log_queue)

    # ── UI State ──

    def _update_ui_state(self, running):
        if running:
            self.start_btn.configure(state="disabled")
            self.stop_btn.configure(state="normal")
            self.op_btn.configure(state="normal")
            self.main_btn.configure(state="normal")
            self.ext_btn.configure(state="normal")
            self.dict_btn.configure(state="normal")
            self._draw_dot(self.ORANGE)
            self.status_label.configure(text="Starting...", foreground=self.ORANGE)
            self.backend_label.configure(text="detecting...")
        else:
            self.start_btn.configure(state="normal")
            self.stop_btn.configure(state="disabled")
            self.op_btn.configure(state="disabled")
            self.main_btn.configure(state="disabled")
            self.ext_btn.configure(state="disabled")
            self.dict_btn.configure(state="disabled")
            self._draw_dot("#666")
            self.status_label.configure(text="Stopped", foreground=self.FG2)
            self.backend_label.configure(text="")

    # ── Server Readiness ──

    def _check_server_readiness(self):
        """Background HTTP check to confirm server is accepting connections."""
        import urllib.request
        port = self.settings.get("operator_port", 3001)
        for _ in range(60):  # Up to 60 seconds
            if not self._server_running or self._server_ready:
                return
            try:
                urllib.request.urlopen(f"http://localhost:{port}", timeout=2)
                self.log_queue.put(("ready", True))
                return
            except Exception:
                time.sleep(1)

    def _open_browser_when_ready(self, port):
        """Open browser once the server is confirmed ready on the given port."""
        import urllib.request

        if not self._server_running:
            messagebox.showwarning("Server Not Running",
                "The server is not running.\nClick 'Start Server' first.",
                parent=self)
            return

        url = f"http://localhost:{port}"

        # Already confirmed ready — open immediately
        if self._server_ready:
            webbrowser.open(url)
            return

        # Server starting but not ready — notify user and wait in background
        messagebox.showinfo("Server Starting",
            "The server is starting. Your window will open upon server start.",
            parent=self)

        def _wait_and_open():
            for _ in range(30):  # Up to 30 seconds
                if not self._server_running:
                    return
                try:
                    urllib.request.urlopen(url, timeout=2)
                    self._server_ready = True
                    self.log_queue.put(("ready", True))
                    webbrowser.open(url)
                    return
                except Exception:
                    time.sleep(1)
            # Timeout — server never responded
            self.after(0, lambda: messagebox.showwarning("Server Not Responding",
                "The server does not seem to be responding.\n"
                "Try stopping the server and starting it again.",
                parent=self))

        threading.Thread(target=_wait_and_open, daemon=True).start()

    # ── Browser Actions ──

    def _open_operator(self):
        self._open_browser_when_ready(self.settings.get("operator_port", 3001))

    def _open_main(self):
        self._open_browser_when_ready(self.settings.get("display_port", 3000))

    def _open_extended(self):
        self._open_browser_when_ready(self.settings.get("extended_port", 3002))

    def _open_dictation(self):
        self._open_browser_when_ready(self.settings.get("dictation_port", 3005))

    def _open_transcripts_dir(self):
        tdir = Path(self.tdir_var.get().strip())
        tdir.mkdir(parents=True, exist_ok=True)
        if IS_WIN:
            os.startfile(str(tdir))
        elif IS_MAC:
            subprocess.Popen(["open", str(tdir)])
        else:
            subprocess.Popen(["xdg-open", str(tdir)])

    # ── Settings ──

    def _browse_tdir(self):
        current = self.tdir_var.get().strip()
        d = filedialog.askdirectory(initialdir=current if Path(current).exists() else str(Path.home()),
                                     title="Select Transcript Save Location")
        if d:
            self.tdir_var.set(d)
            self._save_current_settings()
            self._log_system(f"Transcripts directory: {d}")

    def _save_current_settings(self):
        self.settings["transcripts_dir"] = self.tdir_var.get().strip()
        self.settings["mic_index"] = self._get_selected_mic_index()
        self.settings["backend"] = self._backend_from_label.get(self.backend_var.get(), self.backend_var.get())
        self.settings["window_geometry"] = self.geometry()
        self.settings["check_for_updates"] = self.update_check_var.get()
        save_settings(self.settings)

    # ── Logging ──

    def _append_log(self, text, tag="info"):
        self.log_text.configure(state="normal")
        ts = time.strftime("%H:%M:%S")
        self.log_text.insert("end", f"[{ts}] {text}\n", tag)
        self.log_text.see("end")
        # Trim to 500 lines
        lines = int(self.log_text.index("end-1c").split(".")[0])
        if lines > 500:
            self.log_text.delete("1.0", f"{lines - 500}.0")
        self.log_text.configure(state="disabled")

    def _log_system(self, text):
        self._append_log(text, "system")

    def _log_error(self, text):
        self._append_log(text, "error")

    # ── About ──

    def _show_about(self):
        about = tk.Toplevel(self)
        about.title("About LinguaTaxi")
        about.geometry("400x320")
        about.resizable(False, False)
        about.configure(bg=self.BG)
        about.transient(self)
        about.grab_set()

        f = ttk.Frame(about, padding=24)
        f.pack(fill="both", expand=True)

        ttk.Label(f, text="🚕  LinguaTaxi", style="Title.TLabel").pack(pady=(0, 4))
        ttk.Label(f, text="Live Caption & Translation",
                  style="Subtitle.TLabel").pack()
        ttk.Label(f, text=f"Version {VERSION}",
                  style="Subtitle.TLabel").pack(pady=(8, 16))

        info = (
            "Real-time speech captioning with up to 5\n"
            "simultaneous translations via DeepL.\n\n"
            "Supports NVIDIA CUDA (Windows/Linux),\n"
            "Apple Metal (macOS), and CPU fallback.\n\n"
            "Backends: faster-whisper • mlx-whisper • Vosk"
        )
        ttk.Label(f, text=info, justify="center",
                  style="Subtitle.TLabel").pack()

        ttk.Button(f, text="Close", command=about.destroy).pack(pady=(16, 0))

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

    # ── Cleanup ──

    def _on_close(self):
        self._closing = True
        self._save_current_settings()

        if self._server_running:
            if messagebox.askyesno("Quit",
                "The server is still running.\nStop it and quit?"):
                self._stop_server()
            else:
                self._closing = False
                return

        self.destroy()


# ══════════════════════════════════════════════
# ENTRY POINT
# ══════════════════════════════════════════════

if __name__ == "__main__":
    app = LinguaTaxiApp()
    app.mainloop()
