#!/usr/bin/env python3
"""
LinguaTaxi — Live Caption & Translation
Desktop launcher with server management and browser integration.
"""

import atexit, json, os, platform, queue, re, shutil, signal, subprocess, sys, threading, time, webbrowser
import urllib.request, urllib.error, urllib.parse
from pathlib import Path

import tkinter as tk
from tkinter import filedialog, messagebox
import customtkinter as ctk

# ── Version & Paths ──

APP_NAME = "LinguaTaxi"
APP_FULL = "LinguaTaxi — Live Caption & Translation"
VERSION = "1.0.3"

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
    "source_indices": [-1],
    "backend": "auto",
    "model": "large-v3-turbo",
    "display_port": 3000,
    "operator_port": 3001,
    "extended_port": 3002,
    "host": "0.0.0.0",
    "window_geometry": None,
    "check_for_updates": True,
    "dismissed_version": None,
    "language": None,
    "close_to_tray": True,
}


def load_settings():
    try:
        SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
        if SETTINGS_FILE.exists():
            with open(SETTINGS_FILE, "r") as f:
                raw = json.load(f)
            # Migrate old mic_index BEFORE merging defaults (so "source_indices" in defaults doesn't mask it)
            if "mic_index" in raw and "source_indices" not in raw:
                idx = raw.pop("mic_index")
                raw["source_indices"] = [idx if idx is not None else -1]
            elif "mic_index" in raw:
                raw.pop("mic_index")
            cfg = {**DEFAULT_SETTINGS, **raw}
            return cfg
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


# ── Internationalization ──

_strings = {}
_strings_en = {}

def _load_translations(lang_code):
    """Load translation strings for a language, with English fallback."""
    global _strings, _strings_en
    en_path = APP_DIR / "locales" / "en.json"
    if en_path.exists():
        _strings_en = json.loads(en_path.read_text(encoding="utf-8"))
    lang_path = APP_DIR / "locales" / f"{lang_code.lower()}.json"
    if lang_path.exists():
        _strings = json.loads(lang_path.read_text(encoding="utf-8"))
    else:
        _strings = _strings_en.copy()

def _t(key, **kwargs):
    """Translate a string key with optional variable substitution."""
    text = _strings.get(key) or _strings_en.get(key, key)
    if kwargs:
        for k, v in kwargs.items():
            text = text.replace(f"{{{k}}}", str(v))
    return text

def _detect_os_language():
    """Detect the OS UI language and return a DeepL language code."""
    try:
        if IS_WIN:
            import ctypes
            lcid = ctypes.windll.kernel32.GetUserDefaultUILanguage()
            primary = lcid & 0x3FF
            lcid_map = {
                0x01: "AR", 0x02: "BG", 0x05: "CS", 0x06: "DA", 0x07: "DE",
                0x08: "EL", 0x09: "EN", 0x0A: "ES", 0x25: "ET", 0x0B: "FI",
                0x0C: "FR", 0x0E: "HU", 0x21: "ID", 0x10: "IT", 0x11: "JA",
                0x12: "KO", 0x27: "LT", 0x26: "LV", 0x14: "NB", 0x13: "NL",
                0x15: "PL", 0x16: "PT", 0x18: "RO", 0x19: "RU", 0x1B: "SK",
                0x24: "SL", 0x1D: "SV", 0x1F: "TR", 0x22: "UK", 0x04: "ZH",
            }
            return lcid_map.get(primary, "EN")
        elif IS_MAC:
            result = subprocess.check_output(
                ["defaults", "read", ".GlobalPreferences", "AppleLanguages"],
                text=True, timeout=5)
            for line in result.splitlines():
                line = line.strip().strip('",() ')
                if len(line) >= 2 and line[0].isalpha():
                    return line[:2].upper()
            return "EN"
        else:
            lang = os.environ.get("LANG", "en_US.UTF-8")
            return lang[:2].upper()
    except Exception:
        return "EN"

def _load_language_list():
    """Load language metadata from languages.json."""
    lpath = APP_DIR / "locales" / "languages.json"
    if lpath.exists():
        return json.loads(lpath.read_text(encoding="utf-8"))
    return {"EN": {"name": "English", "native": "English", "flag": "", "rtl": False}}


# ── Microphone detection ──

def list_mics():
    """Return list of (index, name, is_loopback) for available input devices."""
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


# ── Windows Job Object (auto-kill server when launcher dies) ──

def _create_win_job(proc):
    """Create a Windows Job Object that auto-kills the child when we die."""
    if not IS_WIN or not proc:
        return None
    try:
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.windll.kernel32

        class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64),
                        ("PerJobUserTimeLimit", ctypes.c_int64),
                        ("LimitFlags", wintypes.DWORD),
                        ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t),
                        ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.POINTER(ctypes.c_ulong)),
                        ("PriorityClass", wintypes.DWORD),
                        ("SchedulingClass", wintypes.DWORD)]

        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [("ReadOperationCount", ctypes.c_uint64),
                        ("WriteOperationCount", ctypes.c_uint64),
                        ("OtherOperationCount", ctypes.c_uint64),
                        ("ReadTransferCount", ctypes.c_uint64),
                        ("WriteTransferCount", ctypes.c_uint64),
                        ("OtherTransferCount", ctypes.c_uint64)]

        class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
                        ("IoInfo", IO_COUNTERS),
                        ("ProcessMemoryLimit", ctypes.c_size_t),
                        ("JobMemoryLimit", ctypes.c_size_t),
                        ("PeakProcessMemoryUsed", ctypes.c_size_t),
                        ("PeakJobMemoryUsed", ctypes.c_size_t)]

        job = k32.CreateJobObjectW(None, None)
        if not job:
            return None
        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        k32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info))
        h = k32.OpenProcess(0x1FFFFF, False, proc.pid)  # PROCESS_ALL_ACCESS
        if h:
            k32.AssignProcessToJobObject(job, h)
            k32.CloseHandle(h)
        return job
    except Exception:
        return None


# ══════════════════════════════════════════════
# MAIN APPLICATION
# ══════════════════════════════════════════════

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("dark-blue")

class LinguaTaxiApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.settings = load_settings()
        self.server_proc = None
        self._server_job = None
        self.log_queue = queue.Queue()
        self._server_running = False
        self._server_ready = False
        self._closing = False

        # Load language
        lang = self.settings.get("language")
        if not lang:
            lang = _detect_os_language()
            self.settings["language"] = lang
        self._languages = _load_language_list()
        _load_translations(lang)
        self._current_lang = lang

        self._setup_window()
        self._build_ui()
        self._setup_tray()
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
        self.title(_t("app.full_name"))
        self.minsize(620, 660)
        self.resizable(True, True)

        # Restore geometry
        geo = self.settings.get("window_geometry")
        if geo:
            try:
                self.geometry(geo)
            except Exception:
                self.geometry("680x740")
        else:
            self.geometry("680x740")

        # Center on screen if no saved position
        if not geo:
            self.update_idletasks()
            w, h = self.winfo_width(), self.winfo_height()
            sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
            self.geometry(f"+{(sw - w) // 2}+{(sh - h) // 2}")

        # Theme colors
        self.BG = "#0d0d1a"
        self.BG2 = "#12122a"
        self.BG3 = "#1f1f2e"
        self.FG = "#ffffff"
        self.FG2 = "#a0a0a0"
        self.ACCENT = "#4FC3F7"
        self.GREEN = "#8BC34A"
        self.RED = "#ff6b6b"
        self.ORANGE = "#FF9800"
        self.YELLOW = "#FFD54F"

        self.configure(fg_color=self.BG)

    # ── Build UI ──

    def _build_ui(self):
        # Scrollable main container
        main = ctk.CTkScrollableFrame(self, fg_color=self.BG)
        main.pack(fill="both", expand=True, padx=16, pady=16)

        # ── Language Selector ──
        lang_row = ctk.CTkFrame(main, fg_color="transparent")
        lang_row.pack(fill="x", pady=(0, 4))

        ctk.CTkLabel(lang_row, text="\U0001F310", font=("Segoe UI", 14)).pack(side="left", padx=(0, 6))

        lang_values = []
        self._lang_codes = []
        for code, info in sorted(self._languages.items(), key=lambda x: x[1].get("native", "")):
            flag = info.get("flag", "")
            native = info.get("native", info.get("name", code))
            lang_values.append(f"{flag} {native}")
            self._lang_codes.append(code)

        self._lang_var = tk.StringVar()
        current_lang_display = ""
        if self._current_lang in self._lang_codes:
            current_lang_display = lang_values[self._lang_codes.index(self._current_lang)]

        self._lang_combo = ctk.CTkComboBox(lang_row, variable=self._lang_var,
                                            values=lang_values, state="readonly",
                                            width=220, font=("Segoe UI", 12),
                                            fg_color=self.BG2, border_color=self.BG3,
                                            button_color=self.BG3, button_hover_color=self.ACCENT,
                                            dropdown_fg_color=self.BG2, dropdown_hover_color=self.BG3,
                                            command=self._on_language_changed)
        self._lang_combo.pack(side="left")
        if current_lang_display:
            self._lang_combo.set(current_lang_display)

        # ── Header ──
        hdr = ctk.CTkFrame(main, fg_color="transparent")
        hdr.pack(fill="x", pady=(0, 12))

        hdr_left = ctk.CTkFrame(hdr, fg_color="transparent")
        hdr_left.pack(side="left", fill="both", expand=True)

        if EDITION != "Dev":
            title_text = _t("launcher.title_edition", edition=EDITION)
        else:
            title_text = _t("launcher.title_dev")
        self._title_lbl = ctk.CTkLabel(hdr_left, text=title_text,
                  font=("Segoe UI", 20, "bold"), text_color=self.ACCENT)
        self._title_lbl.pack(anchor="w")
        self._subtitle_lbl = ctk.CTkLabel(hdr_left, text=_t("app.subtitle"),
                  font=("Segoe UI", 10), text_color=self.FG2)
        self._subtitle_lbl.pack(anchor="w")

        hdr_right = ctk.CTkFrame(hdr, fg_color="transparent")
        hdr_right.pack(side="right", anchor="ne")

        self._update_btn = ctk.CTkButton(hdr_right, text=_t("launcher.check_for_updates"),
                   command=self._check_for_updates_manual, width=140,
                   fg_color=self.BG3, hover_color=self.ACCENT, text_color=self.FG)
        self._update_btn.pack(anchor="e")

        self.update_check_var = tk.BooleanVar(
            value=self.settings.get("check_for_updates", True))
        self._update_chk = ctk.CTkCheckBox(hdr_right, text=_t("launcher.check_on_startup"),
                        variable=self.update_check_var,
                        font=("Segoe UI", 11), text_color=self.FG2,
                        fg_color=self.ACCENT, hover_color=self.BG3,
                        border_color=self.BG3,
                        command=self._on_update_check_toggled)
        self._update_chk.pack(anchor="e", pady=(4, 0))

        self.close_tray_var = tk.BooleanVar(
            value=self.settings.get("close_to_tray", True))
        self._close_tray_chk = ctk.CTkCheckBox(hdr_right,
                        text="Minimize to tray on close",
                        variable=self.close_tray_var,
                        font=("Segoe UI", 11), text_color=self.FG2,
                        fg_color=self.ACCENT, hover_color=self.BG3,
                        border_color=self.BG3)
        self._close_tray_chk.pack(anchor="e", pady=(2, 0))

        # ── Server Control ──
        ctk.CTkLabel(main, text=_t("launcher.server_frame"),
                     font=("Segoe UI", 11, "bold"), text_color=self.ACCENT).pack(anchor="w", pady=(0, 4))
        self._srv_frame = ctk.CTkFrame(main, fg_color=self.BG2, corner_radius=8,
                                        border_width=1, border_color=self.BG3)
        self._srv_frame.pack(fill="x", pady=(0, 10))

        srv_inner = ctk.CTkFrame(self._srv_frame, fg_color="transparent")
        srv_inner.pack(fill="x", padx=12, pady=12)

        status_row = ctk.CTkFrame(srv_inner, fg_color="transparent")
        status_row.pack(fill="x", pady=(0, 8))

        self.status_dot = tk.Canvas(status_row, width=12, height=12,
                                     bg=self.BG2, highlightthickness=0)
        self.status_dot.pack(side="left", padx=(0, 6))
        self._draw_dot("#666")

        self.status_label = ctk.CTkLabel(status_row, text=_t("launcher.status_stopped"),
                                          font=("Segoe UI", 10, "bold"), text_color=self.FG2)
        self.status_label.pack(side="left")

        self.backend_label = ctk.CTkLabel(status_row, text="",
                                           font=("Segoe UI", 10), text_color=self.FG2)
        self.backend_label.pack(side="right")

        btn_row = ctk.CTkFrame(srv_inner, fg_color="transparent")
        btn_row.pack(fill="x")

        self.start_btn = ctk.CTkButton(btn_row, text=_t("launcher.start_server"),
                                        fg_color=self.GREEN, hover_color="#9CCC65",
                                        text_color="#000", font=("Segoe UI", 12, "bold"),
                                        height=40, command=self._start_server)
        self.start_btn.pack(side="left", expand=True, fill="x", padx=(0, 4))

        self.stop_btn = ctk.CTkButton(btn_row, text=_t("launcher.stop_server"),
                                       fg_color=self.RED, hover_color="#EF5350",
                                       text_color="#fff", font=("Segoe UI", 12, "bold"),
                                       height=40, command=self._stop_server, state="disabled")
        self.stop_btn.pack(side="right", expand=True, fill="x", padx=(4, 0))

        # ── Transcribe File button ──
        tf_row = ctk.CTkFrame(srv_inner, fg_color="transparent")
        tf_row.pack(fill="x", pady=(6, 0))

        self.transcribe_btn = ctk.CTkButton(
            tf_row, text="Transcribe File",
            fg_color="#7E57C2", hover_color="#9575CD",
            text_color="#fff", font=("Segoe UI", 11, "bold"),
            height=34, command=self._transcribe_file,
            state="disabled"
        )
        self.transcribe_btn.pack(fill="x")

        # ── Main Controls ──
        ctk.CTkLabel(main, text="Main Controls",
                     font=("Segoe UI", 11, "bold"), text_color=self.ACCENT).pack(anchor="w", pady=(0, 4))
        self._browser_frame = ctk.CTkFrame(main, fg_color=self.BG2, corner_radius=8,
                                            border_width=1, border_color=self.BG3)
        self._browser_frame.pack(fill="x", pady=(0, 10))

        browser_inner = ctk.CTkFrame(self._browser_frame, fg_color="transparent")
        browser_inner.pack(fill="x", padx=12, pady=12)

        self.op_btn = ctk.CTkButton(browser_inner, text=_t("launcher.operator_controls"),
                                     fg_color=self.BG3, hover_color=self.ACCENT,
                                     text_color=self.ACCENT, font=("Segoe UI", 11),
                                     height=34, command=self._open_operator, state="disabled")
        self.op_btn.pack(fill="x", pady=(0, 5))

        disp_row = ctk.CTkFrame(browser_inner, fg_color="transparent")
        disp_row.pack(fill="x")

        self.main_btn = ctk.CTkButton(disp_row, text=_t("launcher.main_display"),
                                       fg_color=self.BG3, hover_color=self.ACCENT,
                                       text_color=self.ACCENT, font=("Segoe UI", 11),
                                       height=34, command=self._open_main, state="disabled")
        self.main_btn.pack(side="left", expand=True, fill="x", padx=(0, 3))

        self.ext_btn = ctk.CTkButton(disp_row, text=_t("launcher.extended_display"),
                                      fg_color=self.BG3, hover_color=self.ACCENT,
                                      text_color=self.ACCENT, font=("Segoe UI", 11),
                                      height=34, command=self._open_extended, state="disabled")
        self.ext_btn.pack(side="right", expand=True, fill="x", padx=(3, 0))

        # ── Extended Features ──
        ctk.CTkLabel(main, text="Extended Features",
                     font=("Segoe UI", 11, "bold"), text_color=self.ACCENT).pack(anchor="w", pady=(0, 4))
        self._ext_frame = ctk.CTkFrame(main, fg_color=self.BG2, corner_radius=8,
                                        border_width=1, border_color=self.BG3)
        self._ext_frame.pack(fill="x", pady=(0, 10))

        ext_inner = ctk.CTkFrame(self._ext_frame, fg_color="transparent")
        ext_inner.pack(fill="x", padx=12, pady=12)

        ext_row = ctk.CTkFrame(ext_inner, fg_color="transparent")
        ext_row.pack(fill="x")

        self.dict_btn = ctk.CTkButton(ext_row, text=_t("launcher.dictation"),
                                       fg_color=self.BG3, hover_color=self.ACCENT,
                                       text_color=self.ACCENT, font=("Segoe UI", 11),
                                       height=34, command=self._open_dictation, state="disabled")
        self.dict_btn.pack(side="left", expand=True, fill="x", padx=(0, 3))

        self.bidir_btn = ctk.CTkButton(ext_row, text=_t("launcher.bidirectional_display"),
                                        fg_color=self.BG3, hover_color=self.ACCENT,
                                        text_color=self.ACCENT, font=("Segoe UI", 11),
                                        height=34, command=self._open_bidirectional, state="disabled")
        self.bidir_btn.pack(side="right", expand=True, fill="x", padx=(3, 0))

        # ── Settings ──
        ctk.CTkLabel(main, text=_t("launcher.settings_frame"),
                     font=("Segoe UI", 11, "bold"), text_color=self.ACCENT).pack(anchor="w", pady=(0, 4))
        self._settings_frame = ctk.CTkFrame(main, fg_color=self.BG2, corner_radius=8,
                                             border_width=1, border_color=self.BG3)
        self._settings_frame.pack(fill="x", pady=(0, 10))

        settings_inner = ctk.CTkFrame(self._settings_frame, fg_color="transparent")
        settings_inner.pack(fill="x", padx=12, pady=12)

        # Audio Sources
        self._audio_lbl = ctk.CTkLabel(settings_inner, text=_t("launcher.audio_sources"),
                  font=("Segoe UI", 10, "bold"), text_color=self.ACCENT)
        self._audio_lbl.pack(anchor="w")
        self._source_frames = []
        self._sources_container = ctk.CTkFrame(settings_inner, fg_color="transparent")
        self._sources_container.pack(fill="x", pady=(2, 4))
        self._mic_devices = []

        for idx in self.settings.get("source_indices", [-1]):
            self._add_source_row(idx)

        self._add_source_btn = ctk.CTkButton(settings_inner, text=_t("launcher.add_source"),
                                              fg_color=self.BG3, hover_color=self.ACCENT,
                                              text_color=self.FG, height=30,
                                              command=lambda: self._add_source_row())
        self._add_source_btn.pack(fill="x", pady=(0, 8))

        # Backend
        self._backend_lbl = ctk.CTkLabel(settings_inner, text=_t("launcher.speech_backend"),
                  font=("Segoe UI", 10, "bold"), text_color=self.ACCENT)
        self._backend_lbl.pack(anchor="w")
        self._backend_labels = {"auto": _t("launcher.backend_auto"),
                                 "whisper": _t("launcher.backend_whisper"),
                                 "vosk": _t("launcher.backend_vosk"),
                                 "mlx": _t("launcher.backend_mlx")}
        self._backend_from_label = {v: k for k, v in self._backend_labels.items()}
        default_backend = "whisper" if EDITION == "Full" else "auto"
        stored_backend = self.settings.get("backend", default_backend)
        self.backend_var = tk.StringVar(value=self._backend_labels.get(stored_backend, stored_backend))
        backend_values = [_t("launcher.backend_auto"), _t("launcher.backend_whisper"),
                          _t("launcher.backend_vosk")]
        if IS_MAC:
            backend_values.append(_t("launcher.backend_mlx"))
        self._backend_combo = ctk.CTkComboBox(settings_inner, variable=self.backend_var,
                                               values=backend_values, state="readonly",
                                               font=("Segoe UI", 11),
                                               fg_color=self.BG, border_color=self.BG3,
                                               button_color=self.BG3, button_hover_color=self.ACCENT,
                                               dropdown_fg_color=self.BG, dropdown_hover_color=self.BG3)
        self._backend_combo.pack(fill="x", pady=(2, 8))

        model_grid = ctk.CTkFrame(settings_inner, fg_color="transparent")
        model_grid.pack(fill="x", pady=(4, 0))
        model_grid.columnconfigure(0, weight=1)
        model_grid.columnconfigure(1, weight=1)

        self._tuned_btn = ctk.CTkButton(model_grid, text=_t("launcher.download_tuned_models"),
                   fg_color=self.BG3, hover_color=self.ACCENT, text_color=self.FG,
                   height=30, command=self._show_tuned_models_dialog)
        self._tuned_btn.grid(row=0, column=0, sticky="ew", padx=(0, 3), pady=(0, 4))

        self._offline_btn = ctk.CTkButton(model_grid, text=_t("launcher.download_offline_models"),
                   fg_color=self.BG3, hover_color=self.ACCENT, text_color=self.FG,
                   height=30, command=self._show_offline_translate_dialog)
        self._offline_btn.grid(row=0, column=1, sticky="ew", padx=(3, 0), pady=(0, 4))

        self._vosk_btn = ctk.CTkButton(model_grid, text=_t("launcher.download_vosk_models"),
                   fg_color=self.BG3, hover_color=self.ACCENT, text_color=self.FG,
                   height=30, command=self._show_vosk_models_dialog)
        self._vosk_btn.grid(row=1, column=0, sticky="ew", padx=(0, 3))

        self._delete_btn = ctk.CTkButton(model_grid, text=_t("launcher.delete_installed_models"),
                   fg_color=self.BG3, hover_color=self.ACCENT, text_color=self.FG,
                   height=30, command=self._show_model_manager_dialog)
        self._delete_btn.grid(row=1, column=1, sticky="ew", padx=(3, 0))

        # ── Log Area ──
        ctk.CTkLabel(main, text=_t("launcher.server_log_frame"),
                     font=("Segoe UI", 11, "bold"), text_color=self.ACCENT).pack(anchor="w", pady=(0, 4))
        log_frame = ctk.CTkFrame(main, fg_color=self.BG2, corner_radius=8,
                                  border_width=1, border_color=self.BG3)
        log_frame.pack(fill="both", expand=True, pady=(0, 8))

        self.log_text = ctk.CTkTextbox(log_frame, height=180,
                                        fg_color="#0a0a1a", text_color="#7fdbca",
                                        font=("Consolas" if IS_WIN else "Menlo", 11),
                                        corner_radius=6, border_width=0,
                                        state="disabled")
        self.log_text.pack(fill="both", expand=True, padx=6, pady=6)

        # Configure log colors via internal textbox
        self.log_text._textbox.tag_configure("info", foreground="#7fdbca")
        self.log_text._textbox.tag_configure("warn", foreground="#FFD54F")
        self.log_text._textbox.tag_configure("error", foreground="#ff6b6b")
        self.log_text._textbox.tag_configure("system", foreground="#4FC3F7")

        # ── Transcript Location ──
        ctk.CTkLabel(main, text=_t("launcher.transcript_files"),
                     font=("Segoe UI", 11, "bold"), text_color=self.ACCENT).pack(anchor="w", pady=(0, 4))
        tdir_frame = ctk.CTkFrame(main, fg_color=self.BG2, corner_radius=8,
                                   border_width=1, border_color=self.BG3)
        tdir_frame.pack(fill="x", pady=(0, 8))

        tdir_row = ctk.CTkFrame(tdir_frame, fg_color="transparent")
        tdir_row.pack(fill="x", padx=12, pady=10)

        self.tdir_var = tk.StringVar(value=self.settings.get("transcripts_dir",
                                     str(DEFAULT_TRANSCRIPTS)))
        self.tdir_entry = ctk.CTkEntry(tdir_row, textvariable=self.tdir_var,
                                        font=("Segoe UI", 11), fg_color=self.BG,
                                        border_color=self.BG3, text_color=self.FG)
        self.tdir_entry.pack(side="left", fill="x", expand=True, padx=(0, 4))

        self._browse_btn = ctk.CTkButton(tdir_row, text=_t("launcher.browse"),
                   fg_color=self.BG3, hover_color=self.ACCENT, text_color=self.FG,
                   width=70, height=28, command=self._browse_tdir)
        self._browse_btn.pack(side="left", padx=(0, 4))

        self.open_tdir_btn = ctk.CTkButton(tdir_row, text=_t("launcher.open_transcripts"),
                                            fg_color=self.BG3, hover_color=self.ACCENT,
                                            text_color=self.FG, width=110, height=28,
                                            command=self._open_transcripts_dir)
        self.open_tdir_btn.pack(side="right")

        # ── Footer ──
        footer = ctk.CTkFrame(main, fg_color="transparent")
        footer.pack(fill="x")

        self._about_btn = ctk.CTkButton(footer, text=_t("launcher.about"),
                   fg_color=self.BG3, hover_color=self.ACCENT, text_color=self.FG,
                   width=80, height=28, command=self._show_about)
        self._about_btn.pack(side="left")

        ctk.CTkLabel(footer, text=f"v{VERSION}", font=("Segoe UI", 10),
                     text_color=self.FG2).pack(side="right")

        # Welcome message
        self._log_system(_t("launcher.log_welcome_version", version=VERSION))
        self._log_system(_t("launcher.log_app_directory", path=APP_DIR))
        self._log_system(_t("launcher.log_transcripts", path=self.tdir_var.get()))
        self._log_system(_t("launcher.log_ready"))

    @staticmethod
    def _block_combo_scroll(combo):
        pass  # CTkComboBox handles this internally

    # ── Drawing ──

    def _draw_dot(self, color):
        self.status_dot.delete("all")
        self.status_dot.create_oval(2, 2, 10, 10, fill=color, outline="")

    # ── Audio Source Management ──

    def _add_source_row(self, device_index=None):
        """Add an audio source row to the settings."""
        if len(self._source_frames) >= 8:
            return
        row = ctk.CTkFrame(self._sources_container, fg_color="transparent")
        row.pack(fill="x", pady=1)

        num = len(self._source_frames) + 1
        lbl = ctk.CTkLabel(row, text=_t("launcher.source_label", num=num),
                            width=70, font=("Segoe UI", 11), text_color=self.FG2)
        lbl.pack(side="left")

        var = tk.StringVar(value=_t("launcher.system_default"))
        combo = ctk.CTkComboBox(row, variable=var, state="readonly",
                                 font=("Segoe UI", 11), width=300,
                                 fg_color=self.BG, border_color=self.BG3,
                                 button_color=self.BG3, button_hover_color=self.ACCENT,
                                 dropdown_fg_color=self.BG, dropdown_hover_color=self.BG3,
                                 command=lambda v, c=None: None)
        combo.pack(side="left", fill="x", expand=True, padx=(4, 4))
        # Refresh values on dropdown click
        combo.configure(command=lambda v, c=combo: self._refresh_source_combo(c))

        rm_btn = None
        if len(self._source_frames) > 0:
            rm_btn = ctk.CTkButton(row, text="X", width=30, height=28,
                                    fg_color=self.RED, hover_color="#EF5350",
                                    text_color="#fff",
                                    command=lambda r=row: self._remove_source_row(r))
            rm_btn.pack(side="right")

        self._source_frames.append((row, combo, var))
        self._refresh_source_combo(combo)

        # Select the specified device
        if device_index is not None and device_index != -1:
            mics = list_mics()
            for j, (i, name, _) in enumerate(mics):
                if i == device_index:
                    combo.set(f"[{i}] {name}")
                    break

        self._update_add_button()

    def _remove_source_row(self, row):
        """Remove an audio source row."""
        self._source_frames = [(r, c, v) for r, c, v in self._source_frames if r != row]
        row.destroy()
        # Renumber labels
        for i, (r, c, v) in enumerate(self._source_frames):
            for child in r.winfo_children():
                if isinstance(child, ctk.CTkLabel):
                    child.configure(text=_t("launcher.source_label", num=i + 1))
                    break
        self._update_add_button()

    def _update_add_button(self):
        """Show/hide the Add Source button based on count."""
        if len(self._source_frames) >= 8:
            self._add_source_btn.pack_forget()
        else:
            try:
                self._add_source_btn.pack(fill="x", pady=(0, 8))
            except Exception:
                pass

    def _refresh_source_combo(self, combo):
        """Refresh a source dropdown with grouped device list."""
        mics = list_mics()
        self._mic_devices = mics
        physical = [f"[{i}] {n}" for i, n, lb in mics if not lb]
        loopback = [f"[{i}] {n}" for i, n, lb in mics if lb]
        values = [_t("launcher.system_default")]
        if physical:
            values.extend(physical)
        if loopback:
            values.append(_t("launcher.system_audio_separator"))
            values.extend(loopback)
        elif IS_WIN:
            values.append(_t("launcher.no_system_audio"))
        combo.configure(values=values)

    def _get_source_indices(self):
        """Get device indices for all configured audio sources."""
        try:
            self._mic_devices = list_mics()
        except Exception:
            pass
        indices = []
        for _, combo, var in self._source_frames:
            text = var.get()
            if text == _t("launcher.system_default") or not text:
                indices.append(-1)
            else:
                matched = False
                for i, name, _ in self._mic_devices:
                    if f"[{i}] {name}" == text:
                        indices.append(i)
                        matched = True
                        break
                if not matched:
                    indices.append(-1)
        return indices

    # ── Server Management ──

    def _build_server_cmd(self):
        python = self._find_python()
        cmd = [python, str(SERVER_PY)]

        # Backend
        backend = self._backend_from_label.get(self.backend_var.get(), self.backend_var.get())
        if backend and backend != "auto":
            cmd.extend(["--backend", backend])

        # Audio sources
        indices = self._get_source_indices()
        if indices:
            cmd.extend(["--sources", ",".join(str(i) for i in indices)])

        # Transcripts directory
        tdir = self.tdir_var.get().strip()
        if tdir:
            cmd.extend(["--transcripts-dir", tdir])

        # Models directory — ensure server uses same path as launcher
        models_dir = APP_DIR / "models"
        cmd.extend(["--models-dir", str(models_dir)])

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
        dlg = ctk.CTkToplevel(self)
        dlg.title(_t("launcher.dialog_first_time_title"))
        dlg.geometry("480x220")
        dlg.resizable(False, False)
        dlg.configure(fg_color=self.BG)
        dlg.transient(self)
        dlg.grab_set()
        dlg.protocol("WM_DELETE_WINDOW", lambda: None)  # prevent close during download

        # Center on parent
        dlg.update_idletasks()
        px = self.winfo_x() + (self.winfo_width() - 480) // 2
        py = self.winfo_y() + (self.winfo_height() - 220) // 2
        dlg.geometry(f"+{px}+{py}")

        f = ctk.CTkFrame(dlg, fg_color="transparent")
        f.pack(fill="both", expand=True)

        ctk.CTkLabel(f, text=_t("launcher.dialog_downloading_model"),
                  font=("Segoe UI", 12, "bold"),
                  foreground=self.ACCENT, background=self.BG).pack(pady=(0, 8))

        status_var = tk.StringVar(value=_t("launcher.dialog_preparing_download"))
        status_lbl = ctk.CTkLabel(f, textvariable=status_var,
                               wraplength=420)
        status_lbl.pack(pady=(0, 12))

        progress = ctk.CTkProgressBar(f, width=420, mode="indeterminate")
        progress.pack(pady=(0, 12))
        progress.start(15)

        hint = ctk.CTkLabel(f,
                         text=_t("launcher.dialog_first_time_hint"),
                         wraplength=420)
        hint.pack()

        download_done = [False]
        dlg._has_active_download = True

        def run_download():
            try:
                python = self._find_python()
                dl_script = APP_DIR / "download_models.py"

                if not dl_script.exists():
                    status_var.set(_t("launcher.dialog_model_download_fallback"))
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
                status_var.set(_t("launcher.dialog_model_download_fallback"))

            finally:
                download_done[0] = True
                dlg._has_active_download = False

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
            messagebox.showinfo(_t("launcher.dialog_tuned_not_available_title"),
                _t("launcher.dialog_tuned_not_available"),
                parent=self)
            return

        dlg = ctk.CTkToplevel(self)
        dlg.title(_t("launcher.dialog_tuned_title"))
        dlg.geometry("520x480")
        dlg.minsize(400, 300)
        dlg.resizable(True, True)
        dlg.configure(fg_color=self.BG)
        dlg.transient(self)
        dlg.grab_set()

        # Center on parent
        dlg.update_idletasks()
        px = self.winfo_x() + (self.winfo_width() - 520) // 2
        py = self.winfo_y() + (self.winfo_height() - 480) // 2
        dlg.geometry(f"+{px}+{py}")

        f = ctk.CTkFrame(dlg, fg_color="transparent")
        f.pack(fill="both", expand=True)

        ctk.CTkLabel(f, text=_t("launcher.dialog_tuned_heading"),
                  font=("Segoe UI", 13, "bold"),
                  foreground=self.ACCENT, background=self.BG).pack(pady=(0, 4))

        ctk.CTkLabel(f, text=_t("launcher.dialog_tuned_description"),
                  justify="center",
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
        cb_scrollbar = ctk.CTkScrollbar(f, orient="vertical", command=cb_canvas.yview)
        cb_frame = ctk.CTkFrame(cb_canvas)
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
        cb_canvas.bind("<Enter>", lambda e: cb_canvas.bind_all("<MouseWheel>", _cb_mousewheel))
        cb_canvas.bind("<Leave>", lambda e: cb_canvas.unbind_all("<MouseWheel>"))
        dlg.bind("<Destroy>", lambda e: self._bind_main_mousewheel() if e.widget == dlg else None)

        check_vars = {}
        cb_widgets = {}
        for lang, info in model_info.items():
            var = tk.BooleanVar(value=False)
            check_vars[lang] = var

            name = info.get("name", lang)
            size = info.get("size_gb", "?")
            avail = info.get("available", False)

            if avail:
                row = ctk.CTkFrame(cb_frame)
                row.pack(anchor="w", pady=2, fill="x")
                tk.Label(row, text=" \u2713 ", fg="#66BB6A", bg=self.BG,
                         font=("Segoe UI", 10, "bold")).pack(side="left")
                tk.Label(row, text=f"{name} \u2014 ~{size} GB  ",
                         fg=self.FG, bg=self.BG,
                         font=("Segoe UI", 9)).pack(side="left")
                tk.Label(row, text=_t("launcher.dialog_tuned_installed"), fg="#66BB6A", bg=self.BG,
                         font=("Segoe UI", 9, "bold")).pack(side="left")
                cb_widgets[lang] = None
            else:
                text = f"{name} \u2014 ~{size} GB"
                cb = ctk.CTkCheckBox(cb_frame, text=text, variable=var)
                cb.pack(anchor="w", pady=2)
                cb_widgets[lang] = cb

        # Buttons (fixed at bottom)
        btn_frame = ctk.CTkFrame(f)
        btn_frame.pack(fill="x", pady=(0, 8))

        dl_btn = ctk.CTkButton(btn_frame, text=_t("launcher.download_selected"),
                            fg_color="#8BC34A", hover_color="#9CCC65", text_color="#000", command=lambda: _start_download())
        dl_btn.pack(side="left", padx=(0, 8))

        close_btn = ctk.CTkButton(btn_frame, text=_t("launcher.close"),
                               command=dlg.destroy)
        close_btn.pack(side="right")

        # Progress area (fixed at bottom)
        prog_frame = ctk.CTkFrame(f)
        prog_frame.pack(fill="x", pady=(8, 0))

        progress_bar = ctk.CTkProgressBar(prog_frame, mode="determinate", width=400)
        progress_bar.pack_forget()

        status_var = tk.StringVar(value=_t("launcher.dialog_tuned_select_prompt"))
        status_label = ctk.CTkLabel(prog_frame, textvariable=status_var,
                                 wraplength=460)
        status_label.pack(fill="x")

        hint_label = ctk.CTkLabel(prog_frame,
                               text=_t("launcher.dialog_tuned_hint"),
                               wraplength=460)
        hint_label.pack(fill="x", pady=(8, 0))

        dl_queue = queue.Queue()

        def _start_download():
            selected = [lang for lang, var in check_vars.items()
                        if var.get() and not model_info.get(lang, {}).get("available")]
            if not selected:
                messagebox.showinfo(_t("launcher.dialog_tuned_no_selection_title"),
                    _t("launcher.dialog_tuned_no_selection"),
                    parent=dlg)
                return

            dl_btn.configure(state="disabled")
            close_btn.configure(state="disabled")
            for cb in cb_widgets.values():
                if cb:
                    cb.configure(state="disabled")
            progress_bar.pack(fill="x", pady=(0, 4))
            progress_bar.set(0)
            status_var.set(_t("launcher.dialog_tuned_starting"))

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
                    dl_queue.put(("finished_err", 0, _t("launcher.dialog_tuned_download_failed", error=err_msg)))
                elif failed > 0 and succeeded == 0:
                    summary = _t("launcher.dialog_tuned_download_failed", error=errors[0]) if errors else _t("launcher.dialog_tuned_download_failed", error="unknown")
                    dl_queue.put(("finished_err", 0, summary))
                elif failed > 0:
                    dl_queue.put(("finished_partial", succeeded,
                                  _t("launcher.dialog_tuned_partial", succeeded=succeeded, failed=failed)))
                else:
                    dl_queue.put(("finished", 0, ""))

            t = threading.Thread(target=_read_output, daemon=True)
            t.start()

            def _poll():
                try:
                    while True:
                        msg_type, val, msg = dl_queue.get_nowait()
                        if msg_type == "progress":
                            progress_bar.set(val / 100)
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
                                progress_bar.set(1.0)
                                status_var.set(_t("launcher.dialog_tuned_download_complete"))
                            elif msg_type == "finished_err":
                                progress_bar.set(0)
                                status_var.set(msg)
                            else:
                                progress_bar.set(1.0)
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

    # ── Vosk Language Models Dialog ──

    def _show_vosk_models_dialog(self):
        """Show dialog for downloading Vosk language models."""
        VOSK_MODELS = {
            "en": {"name": "English (US)", "url": "https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip", "dir": "vosk-model-small-en-us-0.15", "size": "~40 MB download, ~68 MB on disk"},
            "de": {"name": "German", "url": "https://alphacephei.com/vosk/models/vosk-model-small-de-0.15.zip", "dir": "vosk-model-small-de-0.15", "size": "~45 MB download, ~77 MB on disk"},
            "fr": {"name": "French", "url": "https://alphacephei.com/vosk/models/vosk-model-small-fr-0.22.zip", "dir": "vosk-model-small-fr-0.22", "size": "~41 MB download, ~70 MB on disk"},
            "es": {"name": "Spanish", "url": "https://alphacephei.com/vosk/models/vosk-model-small-es-0.42.zip", "dir": "vosk-model-small-es-0.42", "size": "~39 MB download, ~67 MB on disk"},
            "ru": {"name": "Russian", "url": "https://alphacephei.com/vosk/models/vosk-model-small-ru-0.22.zip", "dir": "vosk-model-small-ru-0.22", "size": "~45 MB download, ~77 MB on disk"},
            "it": {"name": "Italian", "url": "https://alphacephei.com/vosk/models/vosk-model-small-it-0.22.zip", "dir": "vosk-model-small-it-0.22", "size": "~48 MB download, ~82 MB on disk"},
            "ja": {"name": "Japanese", "url": "https://alphacephei.com/vosk/models/vosk-model-small-ja-0.22.zip", "dir": "vosk-model-small-ja-0.22", "size": "~48 MB download, ~82 MB on disk"},
            "zh": {"name": "Chinese", "url": "https://alphacephei.com/vosk/models/vosk-model-small-cn-0.22.zip", "dir": "vosk-model-small-cn-0.22", "size": "~42 MB download, ~72 MB on disk"},
            "ar": {"name": "Arabic", "url": "https://alphacephei.com/vosk/models/vosk-model-ar-mgb2-0.4.zip", "dir": "vosk-model-ar-mgb2-0.4", "size": "~318 MB download, ~543 MB on disk"},
            "pt": {"name": "Portuguese", "url": "https://alphacephei.com/vosk/models/vosk-model-small-pt-0.3.zip", "dir": "vosk-model-small-pt-0.3", "size": "~31 MB download, ~53 MB on disk"},
            "tr": {"name": "Turkish", "url": "https://alphacephei.com/vosk/models/vosk-model-small-tr-0.3.zip", "dir": "vosk-model-small-tr-0.3", "size": "~35 MB download, ~60 MB on disk"},
            "ko": {"name": "Korean", "url": "https://alphacephei.com/vosk/models/vosk-model-small-ko-0.22.zip", "dir": "vosk-model-small-ko-0.22", "size": "~82 MB download, ~140 MB on disk"},
        }

        models_dir = APP_DIR / "models"

        dlg = ctk.CTkToplevel(self)
        dlg.title(_t("launcher.dialog_vosk_title"))
        dlg.geometry("520x500")
        dlg.minsize(400, 320)
        dlg.resizable(True, True)
        dlg.configure(fg_color=self.BG)
        dlg.transient(self)
        dlg.grab_set()

        # Center on parent
        dlg.update_idletasks()
        px = self.winfo_x() + (self.winfo_width() - 520) // 2
        py = self.winfo_y() + (self.winfo_height() - 500) // 2
        dlg.geometry(f"+{px}+{py}")

        f = ctk.CTkFrame(dlg, fg_color="transparent")
        f.pack(fill="both", expand=True)

        ctk.CTkLabel(f, text=_t("launcher.dialog_vosk_heading"),
                  font=("Segoe UI", 13, "bold"),
                  foreground=self.ACCENT, background=self.BG).pack(pady=(0, 4))

        ctk.CTkLabel(f, text=_t("launcher.dialog_vosk_description"),
                  justify="center",
                  wraplength=460).pack(pady=(0, 12))

        # Scrollable checkbox area
        cb_canvas = tk.Canvas(f, bg=self.BG, highlightthickness=0)
        cb_scrollbar = ctk.CTkScrollbar(f, orient="vertical", command=cb_canvas.yview)
        cb_frame = ctk.CTkFrame(cb_canvas)
        cb_frame.bind("<Configure>",
                      lambda e: cb_canvas.configure(scrollregion=cb_canvas.bbox("all")))
        cb_canvas.create_window((0, 0), window=cb_frame, anchor="nw", tags="inner")
        cb_canvas.configure(yscrollcommand=cb_scrollbar.set)

        def _resize_cb(event):
            cb_canvas.itemconfig("inner", width=event.width)
        cb_canvas.bind("<Configure>", _resize_cb)

        cb_canvas.pack(side="top", fill="both", expand=True, pady=(0, 8))
        cb_scrollbar.pack(in_=f, side="right", fill="y", before=cb_canvas)

        def _cb_mousewheel(event):
            cb_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        cb_canvas.bind("<Enter>", lambda e: cb_canvas.bind_all("<MouseWheel>", _cb_mousewheel))
        cb_canvas.bind("<Leave>", lambda e: cb_canvas.unbind_all("<MouseWheel>"))
        dlg.bind("<Destroy>", lambda e: self._bind_main_mousewheel() if e.widget == dlg else None)

        check_vars = {}
        cb_widgets = {}
        installed_status = {}

        for lang, info in VOSK_MODELS.items():
            installed = (models_dir / info["dir"]).exists()
            installed_status[lang] = installed
            var = tk.BooleanVar(value=False)
            check_vars[lang] = var

            if installed:
                row = ctk.CTkFrame(cb_frame)
                row.pack(anchor="w", pady=2, fill="x")
                tk.Label(row, text=" \u2713 ", fg="#66BB6A", bg=self.BG,
                         font=("Segoe UI", 10, "bold")).pack(side="left")
                tk.Label(row, text=f"{info['name']} \u2014 {info['size']}  ",
                         fg=self.FG, bg=self.BG,
                         font=("Segoe UI", 9)).pack(side="left")
                tk.Label(row, text=_t("launcher.dialog_tuned_installed"), fg="#66BB6A", bg=self.BG,
                         font=("Segoe UI", 9, "bold")).pack(side="left")
                cb_widgets[lang] = None
            else:
                text = f"{info['name']} \u2014 {info['size']}"
                cb = ctk.CTkCheckBox(cb_frame, text=text, variable=var)
                cb.pack(anchor="w", pady=2)
                cb_widgets[lang] = cb

        # Buttons (fixed at bottom)
        btn_frame = ctk.CTkFrame(f)
        btn_frame.pack(fill="x", pady=(0, 8))

        dl_btn = ctk.CTkButton(btn_frame, text=_t("launcher.download_selected"),
                            fg_color="#8BC34A", hover_color="#9CCC65", text_color="#000", command=lambda: _start_download())
        dl_btn.pack(side="left", padx=(0, 8))

        close_btn = ctk.CTkButton(btn_frame, text=_t("launcher.close"),
                               command=dlg.destroy)
        close_btn.pack(side="right")

        # Progress area (fixed at bottom)
        prog_frame = ctk.CTkFrame(f)
        prog_frame.pack(fill="x", pady=(8, 0))

        progress_bar = ctk.CTkProgressBar(prog_frame, mode="determinate", width=400)
        progress_bar.pack_forget()

        status_var = tk.StringVar(value=_t("launcher.dialog_vosk_select_prompt"))
        status_label = ctk.CTkLabel(prog_frame, textvariable=status_var,
                                 wraplength=460)
        status_label.pack(fill="x")

        hint_label = ctk.CTkLabel(prog_frame,
                               text=_t("launcher.dialog_vosk_hint"),
                               wraplength=460)
        hint_label.pack(fill="x", pady=(8, 0))

        dl_queue = queue.Queue()

        def _start_download():
            selected = [lang for lang, var in check_vars.items()
                        if var.get() and not installed_status.get(lang)]
            if not selected:
                messagebox.showinfo(_t("launcher.dialog_tuned_no_selection_title"),
                    _t("launcher.dialog_tuned_no_selection"),
                    parent=dlg)
                return

            dl_btn.configure(state="disabled")
            close_btn.configure(state="disabled")
            for cb in cb_widgets.values():
                if cb:
                    cb.configure(state="disabled")
            progress_bar.pack(fill="x", pady=(0, 4))
            progress_bar.set(0)
            status_var.set(_t("launcher.dialog_vosk_starting"))

            total = len(selected)
            completed_count = [0]

            def _download_all():
                succeeded = 0
                failed = 0
                errors = []
                for lang in selected:
                    info = VOSK_MODELS[lang]
                    url = info["url"]
                    dest_dir = info["dir"]
                    zip_path = models_dir / f"{dest_dir}.zip"
                    try:
                        models_dir.mkdir(parents=True, exist_ok=True)
                        dl_queue.put(("status", 0, f"Downloading {info['name']}..."))

                        def _report_hook(block_num, block_size, total_size, _lang=lang, _name=info["name"]):
                            if total_size > 0:
                                pct = min(100, int(block_num * block_size * 100 / total_size))
                                overall = int((completed_count[0] * 100 + pct) / total)
                                dl_queue.put(("progress", overall, f"[{_lang.upper()}] {_name}: {pct}%"))

                        urllib.request.urlretrieve(url, str(zip_path), _report_hook)

                        dl_queue.put(("status", 0, f"Extracting {info['name']}..."))
                        import zipfile
                        with zipfile.ZipFile(str(zip_path), "r") as zf:
                            zf.extractall(str(models_dir))
                        zip_path.unlink(missing_ok=True)

                        installed_status[lang] = True
                        completed_count[0] += 1
                        succeeded += 1
                        dl_queue.put(("done_ok", lang, info["name"]))
                    except Exception as exc:
                        zip_path.unlink(missing_ok=True) if zip_path.exists() else None
                        completed_count[0] += 1
                        failed += 1
                        errors.append(str(exc))
                        dl_queue.put(("done_err", lang, str(exc)))

                if failed > 0 and succeeded == 0:
                    dl_queue.put(("finished_err", 0,
                                  _t("launcher.dialog_vosk_download_failed", error=errors[0]) if errors else _t("launcher.dialog_vosk_download_failed", error="unknown")))
                elif failed > 0:
                    dl_queue.put(("finished_partial", succeeded,
                                  _t("launcher.dialog_vosk_partial", succeeded=succeeded, failed=failed)))
                else:
                    dl_queue.put(("finished", 0, ""))

            t = threading.Thread(target=_download_all, daemon=True)
            t.start()

            def _poll():
                try:
                    while True:
                        msg_type, val, msg = dl_queue.get_nowait()
                        if msg_type == "progress":
                            progress_bar.set(val / 100)
                            status_var.set(msg)
                        elif msg_type == "status":
                            status_var.set(msg)
                        elif msg_type == "done_ok":
                            pass  # installed_status already updated in thread
                        elif msg_type == "done_err":
                            lang_code = val
                            status_var.set(f"[{lang_code.upper()}] Error: {msg}")
                        elif msg_type in ("finished", "finished_err", "finished_partial"):
                            if msg_type == "finished":
                                progress_bar.set(1.0)
                                status_var.set(_t("launcher.dialog_vosk_download_complete"))
                            elif msg_type == "finished_err":
                                progress_bar.set(0)
                                status_var.set(msg)
                            else:
                                progress_bar.set(1.0)
                                status_var.set(msg)
                            dl_btn.configure(state="normal")
                            close_btn.configure(state="normal")
                            # Refresh checkboxes for newly installed models
                            for lang, cb in cb_widgets.items():
                                if cb and installed_status.get(lang):
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
            messagebox.showinfo(_t("launcher.dialog_tuned_not_available_title"),
                _t("launcher.dialog_offline_not_available"),
                parent=self)
            return

        dlg = ctk.CTkToplevel(self)
        dlg.title(_t("launcher.dialog_offline_title"))
        dlg.geometry("560x580")
        dlg.minsize(440, 350)
        dlg.resizable(True, True)
        dlg.configure(fg_color=self.BG)
        dlg.transient(self)
        dlg.grab_set()

        dlg.update_idletasks()
        px = self.winfo_x() + (self.winfo_width() - 560) // 2
        py = self.winfo_y() + (self.winfo_height() - 580) // 2
        dlg.geometry(f"+{px}+{py}")

        f = ctk.CTkFrame(dlg, fg_color="transparent")
        f.pack(fill="both", expand=True)

        ctk.CTkLabel(f, text=_t("launcher.dialog_offline_heading"),
                  font=("Segoe UI", 13, "bold"),
                  foreground=self.ACCENT, background=self.BG).pack(pady=(0, 4))

        ctk.CTkLabel(f, text=_t("launcher.dialog_offline_description"),
                  justify="center",
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
        ol_scrollbar = ctk.CTkScrollbar(f, orient="vertical", command=ol_canvas.yview)
        ol_inner = ctk.CTkFrame(ol_canvas)
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
        ol_canvas.bind("<Enter>", lambda e: ol_canvas.bind_all("<MouseWheel>", _ol_mousewheel))
        ol_canvas.bind("<Leave>", lambda e: ol_canvas.unbind_all("<MouseWheel>"))
        dlg.bind("<Destroy>", lambda e: self._bind_main_mousewheel() if e.widget == dlg else None)

        # OPUS-MT section
        ctk.CTkLabel(ol_inner, text=_t("launcher.dialog_offline_opus_section"),
                  ).pack(anchor="w", pady=(4, 2))

        opus_frame = ctk.CTkFrame(ol_inner)
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
                row = ctk.CTkFrame(opus_frame)
                row.pack(anchor="w", pady=1, fill="x")
                tk.Label(row, text=" \u2713 ", fg="#66BB6A", bg=self.BG,
                         font=("Segoe UI", 9, "bold")).pack(side="left")
                tk.Label(row, text=f"{name} ({lang}) \u2014 ~{size} MB download  ",
                         fg=self.FG, bg=self.BG,
                         font=("Segoe UI", 9)).pack(side="left")
                tk.Label(row, text=_t("launcher.dialog_offline_installed"), fg="#66BB6A", bg=self.BG,
                         font=("Segoe UI", 9, "bold")).pack(side="left")
                opus_cbs[lang] = None
            else:
                text = f"{name} ({lang}) \u2014 ~{size} MB download"
                cb = ctk.CTkCheckBox(opus_frame, text=text, variable=var)
                cb.pack(anchor="w", pady=1)
                opus_cbs[lang] = cb

        # M2M-100 section
        ctk.CTkLabel(ol_inner, text=_t("launcher.dialog_offline_m2m_section"),
                  ).pack(anchor="w", pady=(8, 2))

        m2m_frame = ctk.CTkFrame(ol_inner)
        m2m_frame.pack(fill="x", pady=(0, 8))

        m2m_var = tk.BooleanVar(value=False)
        m2m_name = m2m_info.get("name", "M2M-100")
        m2m_size = m2m_info.get("size_mb", 4800)
        m2m_size_str = f"{m2m_size / 1000:.1f} GB" if m2m_size >= 1000 else f"{m2m_size} MB"
        m2m_avail = m2m_info.get("available", False)
        m2m_cb = None
        if m2m_avail:
            row = ctk.CTkFrame(m2m_frame)
            row.pack(anchor="w", fill="x")
            tk.Label(row, text=" \u2713 ", fg="#66BB6A", bg=self.BG,
                     font=("Segoe UI", 9, "bold")).pack(side="left")
            tk.Label(row, text=f"{m2m_name} \u2014 ~{m2m_size_str}  ",
                     fg=self.FG, bg=self.BG,
                     font=("Segoe UI", 9)).pack(side="left")
            tk.Label(row, text=_t("launcher.dialog_offline_installed"), fg="#66BB6A", bg=self.BG,
                     font=("Segoe UI", 9, "bold")).pack(side="left")
        else:
            m2m_text = f"{m2m_name} \u2014 ~{m2m_size_str} download (covers Arabic, Japanese, Chinese, Korean, etc.)"
            m2m_cb = ctk.CTkCheckBox(m2m_frame, text=m2m_text, variable=m2m_var)
            m2m_cb.pack(anchor="w")

        # Buttons (fixed at bottom)
        btn_frame = ctk.CTkFrame(f)
        btn_frame.pack(fill="x", pady=(8, 4))

        dl_btn = ctk.CTkButton(btn_frame, text=_t("launcher.download_selected"),
                            fg_color="#8BC34A", hover_color="#9CCC65", text_color="#000", command=lambda: _start_download())
        dl_btn.pack(side="left", padx=(0, 8))

        close_btn = ctk.CTkButton(btn_frame, text=_t("launcher.close"),
                               command=dlg.destroy)
        close_btn.pack(side="right")

        # Progress (fixed at bottom)
        prog_frame = ctk.CTkFrame(f)
        prog_frame.pack(fill="x", pady=(8, 0))

        progress_bar = ctk.CTkProgressBar(prog_frame, mode="determinate", width=400)
        progress_bar.pack_forget()

        status_var = tk.StringVar(value=_t("launcher.dialog_offline_select_prompt"))
        status_label = ctk.CTkLabel(prog_frame, textvariable=status_var,
                                 wraplength=500)
        status_label.pack(fill="x")

        ctk.CTkLabel(prog_frame,
                  text=_t("launcher.dialog_offline_hint"),
                  wraplength=500).pack(fill="x", pady=(8, 0))

        dl_queue = queue.Queue()

        def _start_download():
            # Collect selections
            opus_selected = [lang for lang, var in opus_vars.items()
                           if var.get() and not opus_models.get(lang, {}).get("available")]
            want_m2m = m2m_var.get() and not m2m_avail

            if not opus_selected and not want_m2m:
                messagebox.showinfo(_t("launcher.dialog_offline_no_selection_title"),
                    _t("launcher.dialog_offline_no_selection"),
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
            progress_bar.set(0)
            status_var.set(_t("launcher.dialog_offline_starting"))

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
                    dl_queue.put(("finished_err", 0, _t("launcher.dialog_offline_download_failed", error=err_msg)))
                elif failed > 0 and succeeded == 0:
                    summary = _t("launcher.dialog_offline_download_failed", error=errors[0]) if errors else _t("launcher.dialog_offline_download_failed", error="unknown")
                    dl_queue.put(("finished_err", 0, summary))
                elif failed > 0:
                    dl_queue.put(("finished_partial", succeeded,
                                  _t("launcher.dialog_offline_partial", succeeded=succeeded, failed=failed)))
                else:
                    dl_queue.put(("finished", 0, ""))

            t = threading.Thread(target=_run_cmds, daemon=True)
            t.start()

            def _poll():
                try:
                    while True:
                        msg_type, val, msg = dl_queue.get_nowait()
                        if msg_type == "progress":
                            progress_bar.set(val / 100)
                            status_var.set(msg)
                        elif msg_type == "status":
                            status_var.set(msg)
                        elif msg_type == "done_ok":
                            pass  # Individual model done
                        elif msg_type == "done_err":
                            status_var.set(f"[{val}] Error: {msg}")
                        elif msg_type in ("finished", "finished_err", "finished_partial"):
                            if msg_type == "finished":
                                progress_bar.set(1.0)
                                status_var.set(_t("launcher.dialog_offline_download_complete"))
                            elif msg_type == "finished_err":
                                progress_bar.set(0)
                                status_var.set(msg)
                            else:
                                progress_bar.set(1.0)
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
        dlg = ctk.CTkToplevel(self)
        dlg.title(_t("launcher.dialog_models_title"))
        dlg.geometry("680x620")
        dlg.minsize(500, 350)
        dlg.resizable(True, True)
        dlg.configure(fg_color=self.BG)
        dlg.transient(self)
        dlg.grab_set()

        dlg.update_idletasks()
        px = self.winfo_x() + (self.winfo_width() - 680) // 2
        py = self.winfo_y() + (self.winfo_height() - 620) // 2
        dlg.geometry(f"+{px}+{py}")

        f = ctk.CTkFrame(dlg, fg_color="transparent")
        f.pack(fill="both", expand=True)

        ctk.CTkLabel(f, text=_t("launcher.dialog_models_heading"),
                  font=("Segoe UI", 13, "bold"),
                  foreground=self.ACCENT, background=self.BG).pack(pady=(0, 4))

        status_var = tk.StringVar(value=_t("launcher.dialog_models_loading"))
        status_lbl = ctk.CTkLabel(f, textvariable=status_var,
                               wraplength=560)
        status_lbl.pack(fill="x", pady=(0, 8))

        # Scrollable list
        canvas = tk.Canvas(f, bg=self.BG, highlightthickness=0)
        scrollbar = ctk.CTkScrollbar(f, orient="vertical", command=canvas.yview)
        list_frame = ctk.CTkFrame(canvas)

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
        canvas.bind("<Enter>", lambda e: canvas.bind_all("<MouseWheel>", _on_mousewheel))
        canvas.bind("<Leave>", lambda e: canvas.unbind_all("<MouseWheel>"))
        dlg.bind("<Destroy>", lambda e: self._bind_main_mousewheel() if e.widget == dlg else None)

        # Button frame
        btn_frame = ctk.CTkFrame(f)
        btn_frame.pack(fill="x", pady=(8, 0))

        total_var = tk.StringVar(value="")
        ctk.CTkLabel(btn_frame, textvariable=total_var,
                  ).pack(side="left")

        ctk.CTkButton(btn_frame, text=_t("launcher.dialog_models_refresh"),
                   command=lambda: _populate()).pack(side="right", padx=(8, 0))
        ctk.CTkButton(btn_frame, text=_t("launcher.close"),
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
            """Delete a model in a background thread and refresh the list."""
            if not messagebox.askyesno(_t("launcher.dialog_models_delete_confirm_title"),
                    _t("launcher.dialog_models_delete_confirm", name=name),
                    parent=dlg):
                return

            status_var.set(_t("launcher.dialog_models_deleting", name=name) if _t("launcher.dialog_models_deleting") != "launcher.dialog_models_deleting" else f"Deleting {name}...")
            dlg.config(cursor="wait")

            def _do_delete():
                kwargs = {}
                if IS_WIN:
                    kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
                try:
                    if model_type == "speech":
                        path = models_dir / key
                        if path.exists():
                            shutil.rmtree(path)
                    elif model_type == "tuned":
                        subprocess.run(
                            [python, str(APP_DIR / "tuned_models.py"),
                             "--delete", key, "--models-dir", str(models_dir)],
                            capture_output=True, timeout=30, cwd=str(APP_DIR), **kwargs)
                    elif model_type == "opus":
                        subprocess.run(
                            [python, str(APP_DIR / "offline_translate.py"),
                             "--delete-opus", key, "--models-dir", str(models_dir)],
                            capture_output=True, timeout=30, cwd=str(APP_DIR), **kwargs)
                    elif model_type == "m2m":
                        subprocess.run(
                            [python, str(APP_DIR / "offline_translate.py"),
                             "--delete-m2m", "--models-dir", str(models_dir)],
                            capture_output=True, timeout=30, cwd=str(APP_DIR), **kwargs)
                    dlg.after(0, lambda: status_var.set(_t("launcher.dialog_models_deleted", name=name)))
                except Exception as e:
                    dlg.after(0, lambda: status_var.set(_t("launcher.error_delete_failed", error=e)))
                finally:
                    dlg.after(0, lambda: (dlg.config(cursor=""), _populate()))

            threading.Thread(target=_do_delete, daemon=True).start()

        def _add_section_header(parent, text):
            """Add a section header label."""
            lbl = tk.Label(parent, text=text, fg=self.ACCENT, bg=self.BG,
                           font=("Segoe UI", 11, "bold"), anchor="w")
            lbl.pack(fill="x", pady=(8, 2))
            sep = ctk.CTkFrame(parent, height=1, fg_color=self.BG3)
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

            del_btn = tk.Button(row, text="  " + _t("launcher.dialog_models_delete_btn") + "  ", fg="#fff", bg="#c62828",
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
            _add_section_header(list_frame, _t("launcher.dialog_models_speech_section"))
            speech = _get_speech_models()
            if speech:
                for m in speech:
                    total_bytes += m["size"]
                    _add_model_row(list_frame, m["name"], _fmt_size(m["size"]),
                                   "speech", m["key"])
            else:
                tk.Label(list_frame, text="  " + _t("launcher.dialog_models_no_speech"),
                         fg="#666", bg=self.BG, font=("Segoe UI", 9, "italic")).pack(anchor="w")

            # Tuned models
            _add_section_header(list_frame, _t("launcher.dialog_models_tuned_section"))
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
                tk.Label(list_frame, text="  " + _t("launcher.dialog_models_no_tuned_script"),
                         fg="#666", bg=self.BG, font=("Segoe UI", 9, "italic")).pack(anchor="w")
            elif not has_tuned:
                tk.Label(list_frame, text="  " + _t("launcher.dialog_models_no_tuned"),
                         fg="#666", bg=self.BG, font=("Segoe UI", 9, "italic")).pack(anchor="w")

            # Translation models
            _add_section_header(list_frame, _t("launcher.dialog_models_translate_section"))
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
                tk.Label(list_frame, text="  " + _t("launcher.dialog_models_no_translate"),
                         fg="#666", bg=self.BG, font=("Segoe UI", 9, "italic")).pack(anchor="w")

            # Check for leftover HF cache
            hf_cache = models_dir / "translate" / "_hf_cache"
            if hf_cache.exists():
                cache_size = sum(f.stat().st_size for f in hf_cache.rglob("*") if f.is_file())
                if cache_size > 0:
                    total_bytes += cache_size
                    _add_section_header(list_frame, _t("launcher.dialog_models_cache_section"))
                    _add_model_row(list_frame, _t("launcher.dialog_models_hf_cache"),
                                   _fmt_size(cache_size), "speech", "translate/_hf_cache")

            total_var.set(_t("launcher.dialog_models_total_disk", size=_fmt_size(total_bytes)))
            status_var.set(_t("launcher.dialog_models_summary",
                          speech=len(speech),
                          tuned=sum(1 for i in tuned.values() if i.get('available')),
                          translate=sum(1 for i in opus.values() if i.get('available'))))
            canvas.yview_moveto(0)

        # Run populate with busy cursor feedback
        def _bg_populate():
            dlg.config(cursor="wait")
            dlg.after(100, lambda: (_populate(), dlg.config(cursor="")))

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
            self._log_system(_t("launcher.log_first_run_downloading"))
            self._download_models()
            self._log_system(_t("launcher.log_model_setup_complete"))

        # Save settings
        self._save_current_settings()

        # Ensure transcript dir exists
        tdir = Path(self.tdir_var.get().strip())
        try:
            tdir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            messagebox.showerror("Error", _t("launcher.error_create_transcript_dir", error=e))
            return

        cmd = self._build_server_cmd()
        self._log_system(_t("launcher.log_starting", command=' '.join(cmd)))

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
            self._server_job = _create_win_job(self.server_proc)

            self._server_running = True
            self._server_ready = False
            self._update_ui_state(running=True)
            self.transcribe_btn.configure(state="normal")

            # Start log reader thread
            t = threading.Thread(target=self._read_server_output, daemon=True)
            t.start()

            # Start HTTP readiness check (backup for log detection)
            threading.Thread(target=self._check_server_readiness, daemon=True).start()

        except FileNotFoundError:
            self._log_error(_t("launcher.error_python_not_found"))
        except Exception as e:
            self._log_error(_t("launcher.error_start_server", error=e))

    def _stop_server(self):
        if not self._server_running or not self.server_proc:
            return

        self._log_system(_t("launcher.log_stopping_server"))
        pid = self.server_proc.pid

        # Try graceful shutdown via HTTP first (releases mic cleanly)
        try:
            req = urllib.request.Request(
                f"http://localhost:{self.settings.get('operator_port', 3001)}/api/shutdown",
                method="POST", data=b"", headers={"Content-Length": "0"})
            urllib.request.urlopen(req, timeout=2)
        except Exception:
            pass

        try:
            try:
                self.server_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                if IS_WIN:
                    self.server_proc.terminate()
                else:
                    self.server_proc.send_signal(signal.SIGINT)
                try:
                    self.server_proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    self.server_proc.kill()
                    self.server_proc.wait(timeout=3)
        except Exception as e:
            self._log_error(_t("launcher.error_stop_server", error=e))
            try:
                self.server_proc.kill()
            except Exception:
                pass

        # Kill any orphan child processes (Windows process tree)
        if IS_WIN and pid:
            try:
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(pid)],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    timeout=5, creationflags=subprocess.CREATE_NO_WINDOW)
            except Exception:
                pass

        self._server_running = False
        self._server_ready = False
        self.server_proc = None
        self._server_job = None
        self._update_ui_state(running=False)
        self.transcribe_btn.configure(state="disabled")
        self._log_system(_t("launcher.log_server_stopped"))

    def _transcribe_file(self):
        """Open file picker, then show mode selection dialog."""
        if not self._server_running:
            return

        file_path = filedialog.askopenfilename(
            title="Select Audio File",
            filetypes=[
                ("Audio Files", "*.wav *.mp3 *.flac *.m4a *.ogg *.webm"),
                ("WAV", "*.wav"), ("MP3", "*.mp3"), ("FLAC", "*.flac"),
                ("M4A", "*.m4a"), ("OGG", "*.ogg"), ("WebM", "*.webm"),
                ("All Files", "*.*"),
            ]
        )
        if not file_path:
            return

        self._show_transcribe_dialog(file_path)

    def _show_transcribe_dialog(self, file_path):
        """Show mode selection dialog for file transcription."""
        dlg = ctk.CTkToplevel(self)
        dlg.title("Transcribe File")
        dlg.geometry("440x320")
        dlg.resizable(False, False)
        dlg.configure(fg_color=self.BG)
        dlg.transient(self)
        dlg.grab_set()

        dlg.update_idletasks()
        px = self.winfo_x() + (self.winfo_width() - 440) // 2
        py = self.winfo_y() + (self.winfo_height() - 320) // 2
        dlg.geometry(f"+{px}+{py}")

        f = ctk.CTkFrame(dlg, fg_color="transparent")
        f.pack(fill="both", expand=True, padx=16, pady=12)

        # Filename display
        fname = Path(file_path).name
        if len(fname) > 45:
            fname = fname[:42] + "..."
        ctk.CTkLabel(f, text=fname,
                     font=("Segoe UI", 11, "bold"),
                     text_color=self.ACCENT).pack(anchor="w", pady=(0, 10))

        # Mode selection
        mode_var = tk.StringVar(value="batch")

        ctk.CTkRadioButton(
            f, text="Batch Transcribe — Process offline, save text files",
            variable=mode_var, value="batch",
            font=("Segoe UI", 11), text_color=self.FG
        ).pack(anchor="w", pady=(0, 4))

        ctk.CTkRadioButton(
            f, text="Play as Live Input — Feed into live captioning pipeline",
            variable=mode_var, value="live",
            font=("Segoe UI", 11), text_color=self.FG
        ).pack(anchor="w", pady=(0, 12))

        # Status area
        status_var = tk.StringVar(value="")
        status_lbl = ctk.CTkLabel(f, textvariable=status_var,
                                  font=("Segoe UI", 10), text_color=self.FG2,
                                  wraplength=400)
        status_lbl.pack(anchor="w", pady=(0, 4))

        progress = ctk.CTkProgressBar(f, width=400, mode="determinate")
        progress.pack(pady=(0, 8))
        progress.set(0)

        # Button frame
        btn_frame = ctk.CTkFrame(f, fg_color="transparent")
        btn_frame.pack(fill="x", pady=(4, 0))

        port = self.settings.get("operator_port", 3001)
        base_url = f"http://localhost:{port}"
        polling = [False]  # mutable flag for polling loop

        def start_batch():
            start_btn.configure(state="disabled")
            status_var.set("Starting batch transcription...")
            progress.configure(mode="indeterminate")
            progress.start(15)
            try:
                data = urllib.parse.urlencode({"file_path": file_path}).encode()
                req = urllib.request.Request(f"{base_url}/api/transcribe-file/batch", data=data)
                resp = urllib.request.urlopen(req, timeout=10)
                result = json.loads(resp.read())
                if "error" in result:
                    status_var.set(f"Error: {result['error']}")
                    start_btn.configure(state="normal")
                    progress.stop()
                    progress.configure(mode="determinate")
                    progress.set(0)
                    return
            except Exception as e:
                status_var.set(f"Error: {e}")
                start_btn.configure(state="normal")
                progress.stop()
                progress.configure(mode="determinate")
                progress.set(0)
                return

            progress.stop()
            progress.configure(mode="determinate")
            polling[0] = True
            cancel_btn.configure(text="Close")
            poll_progress()

        def start_live():
            start_btn.configure(state="disabled")
            status_var.set("Starting live playback...")
            try:
                data = urllib.parse.urlencode({"file_path": file_path}).encode()
                req = urllib.request.Request(f"{base_url}/api/transcribe-file/live", data=data)
                resp = urllib.request.urlopen(req, timeout=10)
                result = json.loads(resp.read())
                if "error" in result:
                    status_var.set(f"Error: {result['error']}")
                    start_btn.configure(state="normal")
                    return
            except Exception as e:
                status_var.set(f"Error: {e}")
                start_btn.configure(state="normal")
                return

            start_btn.pack_forget()
            stop_btn = ctk.CTkButton(btn_frame, text="Stop Playback",
                                     fg_color=self.RED, hover_color="#EF5350",
                                     text_color="#fff", font=("Segoe UI", 11, "bold"),
                                     height=34, command=lambda: stop_playback(stop_btn))
            stop_btn.pack(side="left", expand=True, fill="x", padx=(0, 4))
            polling[0] = True
            poll_progress()

        def stop_playback(btn):
            btn.configure(state="disabled")
            try:
                req = urllib.request.Request(f"{base_url}/api/transcribe-file/stop",
                                            data=b"", method="POST")
                urllib.request.urlopen(req, timeout=5)
            except Exception:
                pass
            polling[0] = False
            status_var.set("Playback stopped, mic resumed")
            progress.set(0)
            dlg.after(1500, dlg.destroy)

        def poll_progress():
            if not polling[0]:
                return
            try:
                resp = urllib.request.urlopen(f"{base_url}/api/transcribe-file/progress", timeout=3)
                p = json.loads(resp.read())
                status_var.set(p.get("message", ""))
                progress.set(p.get("pct", 0) / 100.0)

                if p["status"] == "done":
                    polling[0] = False
                    progress.set(1.0)
                    status_var.set(p.get("message", "Complete"))
                    cancel_btn.configure(text="Close")
                    # Add Open Folder button for batch
                    if mode_var.get() == "batch":
                        import subprocess
                        transcripts_dir = Path.home() / "Documents" / "LinguaTaxi Transcripts"
                        open_btn = ctk.CTkButton(
                            btn_frame, text="Open Folder",
                            fg_color=self.GREEN, hover_color="#9CCC65",
                            text_color="#000", font=("Segoe UI", 11, "bold"),
                            height=34,
                            command=lambda: subprocess.Popen(
                                ["explorer", str(transcripts_dir)]
                            )
                        )
                        open_btn.pack(side="left", expand=True, fill="x", padx=(0, 4))
                    return
                elif p["status"] == "error":
                    polling[0] = False
                    status_var.set(f"Error: {p.get('message', 'Unknown error')}")
                    start_btn.configure(state="normal")
                    cancel_btn.configure(text="Close")
                    return
                elif p["status"] == "idle":
                    polling[0] = False
                    progress.set(0)
                    dlg.after(500, dlg.destroy)
                    return
            except Exception:
                pass

            dlg.after(500, poll_progress)

        def on_start():
            if mode_var.get() == "batch":
                threading.Thread(target=start_batch, daemon=True).start()
            else:
                threading.Thread(target=start_live, daemon=True).start()

        def on_cancel():
            polling[0] = False
            dlg.destroy()

        start_btn = ctk.CTkButton(btn_frame, text="Start",
                                  fg_color=self.GREEN, hover_color="#9CCC65",
                                  text_color="#000", font=("Segoe UI", 11, "bold"),
                                  height=34, command=on_start)
        start_btn.pack(side="left", expand=True, fill="x", padx=(0, 4))

        cancel_btn = ctk.CTkButton(btn_frame, text="Cancel",
                                   fg_color=self.BG3, hover_color="#555",
                                   text_color="#fff", font=("Segoe UI", 11),
                                   height=34, command=on_cancel)
        cancel_btn.pack(side="right", expand=True, fill="x", padx=(4, 0))

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
            # Server exited — include PID to avoid race with rapid stop/start
            if self._server_running:
                self.log_queue.put(("stopped", proc.pid))

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
                        self.status_label.configure(text=_t("launcher.status_running"), text_color=self.GREEN)
                        self._log_system(_t("launcher.log_server_ready"))
                        # Now enable browser buttons
                        for btn in (self.op_btn, self.main_btn, self.ext_btn, self.dict_btn, self.bidir_btn):
                            btn.configure(state="normal")

                elif msg_type == "stopped":
                    # Ignore stale stopped messages from an old process
                    if data is not None and self.server_proc and hasattr(self.server_proc, 'pid') and self.server_proc.pid != data:
                        continue
                    self._server_running = False
                    self._server_ready = False
                    self.server_proc = None
                    self._update_ui_state(running=False)
                    self._log_system(_t("launcher.log_server_ended"))

        except queue.Empty:
            pass

        if not self._closing:
            self.after(100, self._poll_log_queue)

    # ── UI State ──

    def _update_ui_state(self, running):
        if running:
            self.start_btn.configure(state="disabled")
            self.stop_btn.configure(state="normal")
            # Browser buttons stay disabled until server is actually ready
            btn_state = "normal" if self._server_ready else "disabled"
            self.op_btn.configure(state=btn_state)
            self.main_btn.configure(state=btn_state)
            self.ext_btn.configure(state=btn_state)
            self.dict_btn.configure(state=btn_state)
            self.bidir_btn.configure(state=btn_state)
            self._draw_dot(self.ORANGE)
            self.status_label.configure(text=_t("launcher.status_starting"), text_color=self.ORANGE)
            self.backend_label.configure(text=_t("launcher.status_detecting"))
        else:
            self.start_btn.configure(state="normal")
            self.stop_btn.configure(state="disabled")
            self.op_btn.configure(state="disabled")
            self.main_btn.configure(state="disabled")
            self.ext_btn.configure(state="disabled")
            self.dict_btn.configure(state="disabled")
            self.bidir_btn.configure(state="disabled")
            self._draw_dot("#666")
            self.status_label.configure(text=_t("launcher.status_stopped"), text_color=self.FG2)
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
            messagebox.showwarning(_t("launcher.dialog_server_not_running_title"),
                _t("launcher.dialog_server_not_running"),
                parent=self)
            return

        url = f"http://localhost:{port}"

        # Already confirmed ready — open immediately
        if self._server_ready:
            webbrowser.open(url)
            return

        # Server starting but not ready — notify user and wait in background
        messagebox.showinfo(_t("launcher.dialog_server_starting_title"),
            _t("launcher.dialog_server_starting"),
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
            self.after(0, lambda: messagebox.showwarning(_t("launcher.dialog_server_not_responding_title"),
                _t("launcher.dialog_server_not_responding"),
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

    def _open_bidirectional(self):
        port = self.settings.get("display_port", 3000)
        if not self._server_running:
            messagebox.showwarning(_t("launcher.dialog_server_not_running_title"),
                _t("launcher.dialog_server_not_running"),
                parent=self)
            return
        url = f"http://localhost:{port}/bidirectional?mode=split"
        if self._server_ready:
            webbrowser.open(url)
            return
        messagebox.showinfo(_t("launcher.dialog_server_starting_title"),
            _t("launcher.dialog_server_starting"),
            parent=self)

        def _wait_and_open():
            import urllib.request
            for _ in range(30):
                if not self._server_running:
                    return
                try:
                    urllib.request.urlopen(f"http://localhost:{port}", timeout=2)
                    self._server_ready = True
                    self.log_queue.put(("ready", True))
                    webbrowser.open(url)
                    return
                except Exception:
                    time.sleep(1)
            self.after(0, lambda: messagebox.showwarning(_t("launcher.dialog_server_not_responding_title"),
                _t("launcher.dialog_server_not_responding"),
                parent=self))

        threading.Thread(target=_wait_and_open, daemon=True).start()

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
                                     title=_t("launcher.dialog_select_transcript_location"))
        if d:
            self.tdir_var.set(d)
            self._save_current_settings()
            self._log_system(_t("launcher.log_transcripts_directory", path=d))

    def _save_current_settings(self):
        self.settings["transcripts_dir"] = self.tdir_var.get().strip()
        self.settings["source_indices"] = self._get_source_indices()
        self.settings["backend"] = self._backend_from_label.get(self.backend_var.get(), self.backend_var.get())
        self.settings["window_geometry"] = self.geometry()
        self.settings["check_for_updates"] = self.update_check_var.get()
        self.settings["language"] = self._current_lang
        if hasattr(self, 'close_tray_var'):
            self.settings["close_to_tray"] = self.close_tray_var.get()
        save_settings(self.settings)

    # ── Logging ──

    def _append_log(self, text, tag="info"):
        self.log_text.configure(state="normal")
        ts = time.strftime("%H:%M:%S")
        self.log_text._textbox.insert("end", f"[{ts}] {text}\n", tag)
        self.log_text.see("end")
        # Trim to 500 lines
        lines = int(self.log_text._textbox.index("end-1c").split(".")[0])
        if lines > 500:
            self.log_text._textbox.delete("1.0", f"{lines - 500}.0")
        self.log_text.configure(state="disabled")

    def _log_system(self, text):
        self._append_log(text, "system")

    def _log_error(self, text):
        self._append_log(text, "error")

    # ── About ──

    def _show_about(self):
        about = ctk.CTkToplevel(self)
        about.title(_t("launcher.dialog_about_title"))
        about.geometry("400x320")
        about.resizable(False, False)
        about.configure(fg_color=self.BG)
        about.transient(self)
        about.grab_set()

        f = ctk.CTkFrame(about, fg_color="transparent")
        f.pack(fill="both", expand=True)

        ctk.CTkLabel(f, text=_t("launcher.dialog_about_heading"),
                     font=("Segoe UI", 20, "bold"), text_color=self.ACCENT).pack(pady=(20, 4))
        ctk.CTkLabel(f, text=_t("app.subtitle"),
                     font=("Segoe UI", 11), text_color=self.FG2).pack()
        ctk.CTkLabel(f, text=f"Version {VERSION}",
                     font=("Segoe UI", 10), text_color=self.FG2).pack(pady=(8, 16))

        ctk.CTkLabel(f, text=_t("launcher.dialog_about_description"), justify="center",
                     font=("Segoe UI", 10), text_color=self.FG2).pack()

        ctk.CTkButton(f, text=_t("launcher.close"),
                      fg_color=self.BG3, hover_color=self.ACCENT, text_color=self.FG,
                      command=about.destroy).pack(pady=(16, 0))

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
            self._log_system(_t("launcher.log_checking_updates"))

    def _handle_update_result(self, result, manual):
        """Process update check result on the main thread."""
        if result is None:
            if manual:
                messagebox.showinfo(_t("launcher.dialog_update_check_title"),
                    _t("launcher.dialog_update_no_internet"),
                    parent=self)
            return

        tag, assets, body = result
        remote_ver = _parse_version(tag)
        local_ver = _parse_version(VERSION)

        if remote_ver is None or local_ver is None:
            if manual:
                messagebox.showinfo(_t("launcher.dialog_update_check_title"),
                    _t("launcher.dialog_update_parse_error", remote=tag, local=VERSION),
                    parent=self)
            return

        if remote_ver <= local_ver:
            if manual:
                messagebox.showinfo(_t("launcher.dialog_update_check_title"),
                    _t("launcher.dialog_update_up_to_date", version=VERSION), parent=self)
            return

        # New version available — check if dismissed
        if not manual and self.settings.get("dismissed_version") == tag:
            return

        self._show_update_dialog(tag, assets)

    def _show_update_dialog(self, tag, assets):
        """Show dialog offering to download a new version."""
        version = tag.lstrip("v")

        dlg = ctk.CTkToplevel(self)
        dlg.title(_t("launcher.dialog_update_available_title"))
        dlg.geometry("440x200")
        dlg.resizable(False, False)
        dlg.configure(fg_color=self.BG)
        dlg.transient(self)
        dlg.grab_set()

        dlg.update_idletasks()
        px = self.winfo_x() + (self.winfo_width() - 440) // 2
        py = self.winfo_y() + (self.winfo_height() - 200) // 2
        dlg.geometry(f"+{px}+{py}")

        f = ctk.CTkFrame(dlg, fg_color="transparent")
        f.pack(fill="both", expand=True)

        ctk.CTkLabel(f, text=_t("launcher.dialog_update_available_heading", version=version),
                  font=("Segoe UI", 12, "bold"),
                  foreground=self.ACCENT, background=self.BG).pack(pady=(0, 4))
        ctk.CTkLabel(f, text=_t("launcher.dialog_update_current_version", version=VERSION),
                  ).pack(pady=(0, 16))

        btn_frame = ctk.CTkFrame(f)
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

        ctk.CTkButton(btn_frame, text=_t("launcher.dialog_update_download_now"), fg_color="#8BC34A", hover_color="#9CCC65", text_color="#000", command=_download_now).pack(side="left", padx=(0, 8))
        ctk.CTkButton(btn_frame, text=_t("launcher.dialog_update_remind_later"),
                   command=_remind_later).pack(side="left", padx=(0, 8))
        ctk.CTkButton(btn_frame, text=_t("launcher.dialog_update_dont_remind"),
                   command=_dont_remind).pack(side="left")

        self.wait_window(dlg)

    def _download_update(self, tag, assets):
        """Download the installer for the current edition."""
        url, filename = self._find_asset_url(assets, tag)

        if url is None:
            if EDITION == "Dev":
                webbrowser.open(f"https://github.com/{GITHUB_REPO}/releases/tag/{tag}")
                self._log_system(_t("launcher.log_opened_github"))
                return
            messagebox.showerror(_t("launcher.dialog_download_no_installer_title"),
                _t("launcher.dialog_download_no_installer", edition=EDITION),
                parent=self)
            return

        # Ask where to save
        downloads_dir = Path.home() / "Downloads"
        save_path = filedialog.asksaveasfilename(
            parent=self,
            initialdir=str(downloads_dir),
            initialfile=filename,
            title=_t("launcher.dialog_save_installer_title"),
            defaultextension=Path(filename).suffix,
            filetypes=[("Installer", f"*{Path(filename).suffix}"), ("All files", "*.*")],
        )
        if not save_path:
            return

        save_path = Path(save_path)
        self._show_download_progress(url, save_path)

    def _show_download_progress(self, url, save_path):
        """Show progress dialog while downloading the installer."""
        dlg = ctk.CTkToplevel(self)
        dlg.title(_t("launcher.dialog_download_title"))
        dlg.geometry("460x160")
        dlg.resizable(False, False)
        dlg.configure(fg_color=self.BG)
        dlg.transient(self)
        dlg.grab_set()

        dlg.update_idletasks()
        px = self.winfo_x() + (self.winfo_width() - 460) // 2
        py = self.winfo_y() + (self.winfo_height() - 160) // 2
        dlg.geometry(f"+{px}+{py}")

        f = ctk.CTkFrame(dlg, fg_color="transparent")
        f.pack(fill="both", expand=True)

        status_var = tk.StringVar(value=_t("launcher.dialog_download_connecting"))
        ctk.CTkLabel(f, textvariable=status_var, ).pack(pady=(0, 8))

        progress = ctk.CTkProgressBar(f, mode="determinate", width=400)
        progress.pack(pady=(0, 12))

        cancelled = [False]

        def _cancel():
            cancelled[0] = True

        cancel_btn = ctk.CTkButton(f, text=_t("launcher.dialog_download_cancel"), command=_cancel)
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
                                    status_var.set(_t("launcher.dialog_download_progress",
                                        downloaded=f"{m:.1f}", total=f"{t:.1f}", percent=f"{p:.0f}"))
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
                    status_var.set(_t("launcher.dialog_download_failed", error=err))
                    cancel_btn.configure(text=_t("launcher.close"), command=dlg.destroy)
                self.after(0, _show_error)

        def _download_complete(dlg, status_var, progress, cancel_btn):
            status_var.set(_t("launcher.dialog_download_complete"))
            progress.configure(value=100)
            cancel_btn.destroy()

            btn_frame = ctk.CTkFrame(f)
            btn_frame.pack(pady=(4, 0))

            def _open_folder():
                if IS_WIN:
                    subprocess.Popen(["explorer", "/select,", str(save_path)])
                elif IS_MAC:
                    subprocess.Popen(["open", "-R", str(save_path)])
                else:
                    subprocess.Popen(["xdg-open", str(save_path.parent)])
                dlg.destroy()

            ctk.CTkButton(btn_frame, text=_t("launcher.dialog_download_open_folder"), command=_open_folder).pack(side="left", padx=(0, 8))
            ctk.CTkButton(btn_frame, text=_t("launcher.close"), command=dlg.destroy).pack(side="left")

            # Reminder
            ctk.CTkLabel(f, text=_t("launcher.dialog_download_close_reminder"),
                      ).pack(pady=(8, 0))

        threading.Thread(target=_worker, daemon=True).start()
        self.wait_window(dlg)

    # ── Language Switching ──

    def _on_language_changed(self, event=None):
        """Handle language selection change."""
        selected = self._lang_combo.get()
        if not selected:
            return
        # Find matching language code
        lang_values = []
        for code, info in sorted(self._languages.items(), key=lambda x: x[1].get("native", "")):
            flag = info.get("flag", "")
            native = info.get("native", info.get("name", code))
            lang_values.append(f"{flag} {native}")
        try:
            idx = lang_values.index(selected)
        except ValueError:
            return
        lang = self._lang_codes[idx]
        if lang == self._current_lang:
            return
        self._current_lang = lang
        self.settings["language"] = lang
        save_settings(self.settings)
        _load_translations(lang)
        self._refresh_ui()
        # Notify running server (in background to avoid blocking UI)
        if self._server_running:
            def _notify():
                try:
                    port = self.settings.get("operator_port", 3001)
                    data = json.dumps({"ui_language": lang}).encode()
                    req = urllib.request.Request(f"http://127.0.0.1:{port}/api/config",
                        data=data, headers={"Content-Type": "application/json"}, method="POST")
                    urllib.request.urlopen(req, timeout=2)
                except Exception:
                    pass
            threading.Thread(target=_notify, daemon=True).start()

    def _refresh_ui(self):
        """Re-apply all translated strings to UI widgets."""
        # Close any open dialogs first (skip those with active downloads)
        for w in list(self.winfo_children()):
            if isinstance(w, ctk.CTkToplevel):
                if getattr(w, '_has_active_download', False):
                    continue
                w.destroy()

        # Window title
        self.title(_t("app.full_name"))

        # Header
        if EDITION != "Dev":
            self._title_lbl.configure(text=_t("launcher.title_edition", edition=EDITION))
        else:
            self._title_lbl.configure(text=_t("launcher.title_dev"))
        self._subtitle_lbl.configure(text=_t("app.subtitle"))

        # Update controls
        self._update_btn.configure(text=_t("launcher.check_for_updates"))
        self._update_chk.configure(text=_t("launcher.check_on_startup"))

        # Server buttons
        self.start_btn.configure(text=_t("launcher.start_server"))
        self.stop_btn.configure(text=_t("launcher.stop_server"))

        # Update status label based on current state
        if self._server_ready:
            self.status_label.configure(text=_t("launcher.status_running"))
        elif self._server_running:
            self.status_label.configure(text=_t("launcher.status_starting"))
        else:
            self.status_label.configure(text=_t("launcher.status_stopped"))

        # Browser buttons
        self.op_btn.configure(text=_t("launcher.operator_controls"))
        self.main_btn.configure(text=_t("launcher.main_display"))
        self.ext_btn.configure(text=_t("launcher.extended_display"))
        self.dict_btn.configure(text=_t("launcher.dictation"))
        self.bidir_btn.configure(text=_t("launcher.bidirectional_display"))

        # Settings
        self._browse_btn.configure(text=_t("launcher.browse"))
        self._audio_lbl.configure(text=_t("launcher.audio_sources"))
        self._add_source_btn.configure(text=_t("launcher.add_source"))
        self._backend_lbl.configure(text=_t("launcher.speech_backend"))

        # Re-translate backend labels and combo
        old_backend = self._backend_from_label.get(self.backend_var.get(), self.backend_var.get())
        self._backend_labels = {"auto": _t("launcher.backend_auto"),
                                 "whisper": _t("launcher.backend_whisper"),
                                 "vosk": _t("launcher.backend_vosk"),
                                 "mlx": _t("launcher.backend_mlx")}
        self._backend_from_label = {v: k for k, v in self._backend_labels.items()}
        backend_values = [_t("launcher.backend_auto"), _t("launcher.backend_whisper"),
                          _t("launcher.backend_vosk")]
        if IS_MAC:
            backend_values.append(_t("launcher.backend_mlx"))
        self._backend_combo.configure(values=backend_values)
        self.backend_var.set(self._backend_labels.get(old_backend, old_backend))

        # Source row labels
        for i, (r, c, v) in enumerate(self._source_frames):
            for child in r.winfo_children():
                if isinstance(child, ctk.CTkLabel):
                    child.configure(text=_t("launcher.source_label", num=i + 1))
                    break

        # Download/delete buttons
        self._tuned_btn.configure(text=_t("launcher.download_tuned_models"))
        self._offline_btn.configure(text=_t("launcher.download_offline_models"))
        self._delete_btn.configure(text=_t("launcher.delete_installed_models"))
        self._vosk_btn.configure(text=_t("launcher.download_vosk_models"))

        # Footer
        self.open_tdir_btn.configure(text=_t("launcher.open_transcripts"))
        self._about_btn.configure(text=_t("launcher.about"))

    # ── Tray ──

    def _setup_tray(self):
        """Set up system tray icon and start it running (hidden initially)."""
        self._tray_icon = None
        self._tray_running = False
        try:
            import pystray
            from PIL import Image
        except ImportError:
            return

        icon_path = APP_DIR / "assets" / "linguataxi.png"
        if icon_path.exists():
            image = Image.open(str(icon_path)).resize((64, 64))
        else:
            image = Image.new("RGBA", (64, 64), (79, 195, 247, 255))

        def _show_window(icon, item):
            self.after(0, self._restore_from_tray)

        def _start_srv(icon, item):
            self.after(0, self._start_server)

        def _stop_srv(icon, item):
            self.after(0, self._stop_server)

        def _open_op(icon, item):
            self.after(0, self._open_operator)

        def _open_disp(icon, item):
            self.after(0, self._open_main)

        def _open_dict(icon, item):
            self.after(0, self._open_dictation)

        def _quit(icon, item):
            self.after(0, self._quit_from_tray)

        self._tray_icon = pystray.Icon(
            "LinguaTaxi",
            image,
            "LinguaTaxi",
            menu=pystray.Menu(
                pystray.MenuItem("Show Window", _show_window, default=True),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Start Server", _start_srv),
                pystray.MenuItem("Stop Server", _stop_srv),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Open Operator", _open_op),
                pystray.MenuItem("Open Display", _open_disp),
                pystray.MenuItem("Open Dictation", _open_dict),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Quit", _quit),
            ),
        )

        def _run_tray_bg():
            self._tray_running = True
            try:
                self._tray_icon.run(setup=lambda icon: None)
            except Exception as e:
                try:
                    with open(str(SETTINGS_DIR / "tray_error.log"), "w") as f:
                        import traceback
                        f.write(f"Tray icon failed: {e}\n")
                        traceback.print_exc(file=f)
                except Exception:
                    pass
            self._tray_running = False

        threading.Thread(target=_run_tray_bg, daemon=True).start()

    def _minimize_to_tray(self):
        """Hide window and show tray icon."""
        if not self._tray_icon or not self._tray_running:
            return False
        self.withdraw()
        self._tray_icon.visible = True
        self._tray_icon.notify("LinguaTaxi is still running", "LinguaTaxi")
        return True

    def _restore_from_tray(self):
        """Show window and hide tray icon."""
        if self._tray_icon:
            self._tray_icon.visible = False
        self.deiconify()
        self.lift()
        self.focus_force()

    def _quit_from_tray(self):
        """Full quit from tray: stop server, destroy window, exit."""
        if self._server_running:
            self._stop_server()
        if self._tray_icon:
            try:
                self._tray_icon.stop()
            except Exception:
                pass
        self.destroy()

    # ── Cleanup ──

    def _on_close(self):
        self._closing = True
        self._save_current_settings()

        if self.settings.get("close_to_tray", True):
            if self._minimize_to_tray():
                self._closing = False
                return

        if self._server_running:
            if messagebox.askyesno(_t("launcher.dialog_quit_title"),
                _t("launcher.dialog_quit_message")):
                self._stop_server()
            else:
                self._closing = False
                self._poll_log_queue()
                return

        self.destroy()


# ══════════════════════════════════════════════
# ENTRY POINT
# ══════════════════════════════════════════════

if __name__ == "__main__":
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("LinguaTaxi.Launcher")
    app = LinguaTaxiApp()

    def _atexit_cleanup():
        if app._server_running and app.server_proc:
            try:
                app._stop_server()
            except Exception:
                try:
                    app.server_proc.kill()
                except Exception:
                    pass

    atexit.register(_atexit_cleanup)
    app.mainloop()
