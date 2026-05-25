"""Slimmed LinguaTaxiApp class — delegates to component modules."""

from __future__ import annotations

import atexit
import json
import logging
import os
import queue
import re
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox
from typing import Any

import tkinter as tk
import customtkinter as ctk

from linguataxi.launcher.i18n import (
    _load_translations,
    _t,
    detect_os_language,
    load_language_list,
)
from linguataxi.launcher.settings_panel import (
    DEFAULT_TRANSCRIPTS,
    SETTINGS_DIR,
    SettingsHelper,
    list_mics,
    load_settings,
    save_settings,
)
from linguataxi.launcher.server_manager import ServerManager
from linguataxi.launcher.model_download import ModelDownloadHelper
from linguataxi.launcher.batch_transcriber import BatchTranscriber
from linguataxi.launcher.tray_manager import TrayManager

logger = logging.getLogger(__name__)

# ── Version & Paths ─────────────────────────────────────────────────

APP_NAME = "LinguaTaxi"
APP_FULL = "LinguaTaxi \u2014 Live Caption & Translation"
VERSION = "1.0.3b"

IS_WIN = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"

# Determine app directory (where server.py lives)
if os.environ.get("LINGUATAXI_APP_DIR"):
    APP_DIR = Path(os.environ["LINGUATAXI_APP_DIR"])
elif getattr(sys, "frozen", False):
    APP_DIR = Path(sys.executable).parent
else:
    APP_DIR = Path(__file__).resolve().parent.parent.parent  # linguataxi/launcher/app.py -> repo root

# Detect edition from edition.txt (written by installer / build system)
_edition_file = APP_DIR / "edition.txt"
EDITION = _edition_file.read_text().strip() if _edition_file.exists() else "Dev"

GITHUB_REPO = "TheColliny/LinguaTaxi"

SERVER_PY = APP_DIR / "server.py"


def _parse_version(tag: str) -> tuple[int, int, int] | None:
    """Parse ``'vX.Y.Z'`` or ``'X.Y.Z'`` into ``(X, Y, Z)``.  Returns ``None`` on failure."""
    tag = tag.strip().lstrip("v")
    try:
        parts = tuple(int(x) for x in tag.split("."))
        if len(parts) == 3:
            return parts  # type: ignore[return-value]
    except (ValueError, AttributeError):
        pass
    return None


# ── Theme initialisation ────────────────────────────────────────────

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("dark-blue")


# ══════════════════════════════════════════════════════════════════════
# MAIN APPLICATION
# ══════════════════════════════════════════════════════════════════════

class LinguaTaxiApp(ctk.CTk):
    """Top-level launcher window that delegates to component modules."""

    def __init__(self) -> None:
        super().__init__()
        self.settings = load_settings()
        self.log_queue: queue.Queue[tuple[str, Any]] = queue.Queue()
        self._closing = False

        # ── Sub-components ───────────────────────────────────────────
        self._server_mgr = ServerManager(
            APP_DIR,
            on_log=lambda msg_type, data: self.log_queue.put((msg_type, data)),
        )
        self._settings_helper = SettingsHelper(self)
        self._model_helper = ModelDownloadHelper(self)
        self._batch = BatchTranscriber(self)
        self._tray = TrayManager(self, APP_DIR, SETTINGS_DIR)

        # Load language
        lang = self.settings.get("language")
        if not lang:
            lang = detect_os_language()
            self.settings["language"] = lang
        self._languages = load_language_list(APP_DIR)
        _load_translations(lang, APP_DIR)
        self._current_lang = lang

        self._setup_window()
        self._build_ui()
        self._tray.setup()
        self._poll_log_queue()

        # Handle close
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        if IS_WIN:
            signal.signal(signal.SIGINT, lambda *a: self._on_close())

        # Auto-check for updates after UI is ready
        if self.settings.get("check_for_updates", True):
            self.after(2000, lambda: self._do_update_check(manual=False))

    # ── Window Setup ─────────────────────────────────────────────────

    def _setup_window(self) -> None:
        """Configure window title, size, geometry, and theme colours."""
        self.title(_t("app.full_name"))
        self.minsize(620, 660)
        self.resizable(True, True)

        geo = self.settings.get("window_geometry")
        if geo:
            try:
                self.geometry(geo)
            except Exception:
                self.geometry("680x740")
        else:
            self.geometry("680x740")

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
        self.GREEN = "#66BB6A"
        self.RED = "#E57373"
        self.ORANGE = "#FF9800"
        self.YELLOW = "#FFD54F"

        self.configure(fg_color=self.BG)

    # ── Build UI ─────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        """Construct the complete launcher UI."""
        main = ctk.CTkScrollableFrame(self, fg_color=self.BG)
        main.pack(fill="both", expand=True, padx=16, pady=16)

        # Language Selector
        lang_row = ctk.CTkFrame(main, fg_color="transparent")
        lang_row.pack(fill="x", pady=(0, 4))

        ctk.CTkLabel(lang_row, text="\U0001F310", font=("Segoe UI", 14)).pack(side="left", padx=(0, 6))

        lang_values: list[str] = []
        self._lang_codes: list[str] = []
        for code, info in sorted(self._languages.items(), key=lambda x: x[1].get("native", "")):
            flag = info.get("flag", "")
            native = info.get("native", info.get("name", code))
            lang_values.append(f"{flag} {native}")
            self._lang_codes.append(code)

        self._lang_var = tk.StringVar()
        current_lang_display = ""
        if self._current_lang in self._lang_codes:
            current_lang_display = lang_values[self._lang_codes.index(self._current_lang)]

        self._lang_combo = ctk.CTkComboBox(
            lang_row, variable=self._lang_var,
            values=lang_values, state="readonly",
            width=220, font=("Segoe UI", 12),
            fg_color=self.BG2, border_color=self.BG3,
            button_color=self.BG3, button_hover_color=self.ACCENT,
            dropdown_fg_color=self.BG2, dropdown_hover_color=self.BG3,
            command=self._on_language_changed,
        )
        self._lang_combo.pack(side="left")
        if current_lang_display:
            self._lang_combo.set(current_lang_display)

        # Header
        hdr = ctk.CTkFrame(main, fg_color="transparent")
        hdr.pack(fill="x", pady=(0, 12))

        hdr_left = ctk.CTkFrame(hdr, fg_color="transparent")
        hdr_left.pack(side="left", fill="both", expand=True)

        if EDITION != "Dev":
            title_text = _t("launcher.title_edition", edition=EDITION)
        else:
            title_text = _t("launcher.title_dev")
        self._title_lbl = ctk.CTkLabel(
            hdr_left, text=title_text,
            font=("Segoe UI", 20, "bold"), text_color=self.ACCENT,
        )
        self._title_lbl.pack(anchor="w")
        self._subtitle_lbl = ctk.CTkLabel(
            hdr_left, text=_t("app.subtitle"),
            font=("Segoe UI", 10), text_color=self.FG2,
        )
        self._subtitle_lbl.pack(anchor="w")

        hdr_right = ctk.CTkFrame(hdr, fg_color="transparent")
        hdr_right.pack(side="right", anchor="ne")

        self._update_btn = ctk.CTkButton(
            hdr_right, text=_t("launcher.check_for_updates"),
            command=self._check_for_updates_manual, width=140,
            fg_color=self.BG3, hover_color=self.ACCENT, text_color=self.FG,
        )
        self._update_btn.pack(anchor="e")

        self.update_check_var = tk.BooleanVar(value=self.settings.get("check_for_updates", True))
        self._update_chk = ctk.CTkCheckBox(
            hdr_right, text=_t("launcher.check_on_startup"),
            variable=self.update_check_var,
            font=("Segoe UI", 11), text_color=self.FG2,
            fg_color=self.ACCENT, hover_color=self.BG3, border_color=self.BG3,
            command=self._on_update_check_toggled,
        )
        self._update_chk.pack(anchor="e", pady=(4, 0))

        self.close_tray_var = tk.BooleanVar(value=self.settings.get("close_to_tray", True))
        self._close_tray_chk = ctk.CTkCheckBox(
            hdr_right, text="Minimize to tray on close",
            variable=self.close_tray_var,
            font=("Segoe UI", 11), text_color=self.FG2,
            fg_color=self.ACCENT, hover_color=self.BG3, border_color=self.BG3,
        )
        self._close_tray_chk.pack(anchor="e", pady=(2, 0))

        # Server Control
        ctk.CTkLabel(
            main, text=_t("launcher.server_frame"),
            font=("Segoe UI", 11, "bold"), text_color=self.ACCENT,
        ).pack(anchor="w", pady=(0, 4))
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

        self.status_label = ctk.CTkLabel(
            status_row, text=_t("launcher.status_stopped"),
            font=("Segoe UI", 10, "bold"), text_color=self.FG2,
        )
        self.status_label.pack(side="left")

        self.backend_label = ctk.CTkLabel(
            status_row, text="",
            font=("Segoe UI", 10), text_color=self.FG2,
        )
        self.backend_label.pack(side="right")

        btn_row = ctk.CTkFrame(srv_inner, fg_color="transparent")
        btn_row.pack(fill="x")

        self.start_btn = ctk.CTkButton(
            btn_row, text=_t("launcher.start_server"),
            fg_color=self.GREEN, hover_color="#81C784",
            text_color="#000", font=("Segoe UI", 12, "bold"),
            height=40, command=self._start_server,
        )
        self.start_btn.pack(side="left", expand=True, fill="x", padx=(0, 4))

        self.stop_btn = ctk.CTkButton(
            btn_row, text=_t("launcher.stop_server"),
            fg_color=self.RED, hover_color="#EF9A9A",
            text_color="#fff", font=("Segoe UI", 12, "bold"),
            height=40, command=self._stop_server, state="disabled",
        )
        self.stop_btn.pack(side="right", expand=True, fill="x", padx=(4, 0))

        # Transcribe File button
        tf_row = ctk.CTkFrame(srv_inner, fg_color="transparent")
        tf_row.pack(fill="x", pady=(6, 0))

        self.transcribe_btn = ctk.CTkButton(
            tf_row, text="Transcribe File",
            fg_color="#9575CD", hover_color="#B39DDB",
            text_color="#fff", font=("Segoe UI", 11, "bold"),
            height=34, command=self._batch.transcribe_file,
            state="disabled",
        )
        self.transcribe_btn.pack(fill="x")

        # Main Controls
        ctk.CTkLabel(
            main, text="Main Controls",
            font=("Segoe UI", 11, "bold"), text_color=self.ACCENT,
        ).pack(anchor="w", pady=(0, 4))
        self._browser_frame = ctk.CTkFrame(main, fg_color=self.BG2, corner_radius=8,
                                            border_width=1, border_color=self.BG3)
        self._browser_frame.pack(fill="x", pady=(0, 10))

        browser_inner = ctk.CTkFrame(self._browser_frame, fg_color="transparent")
        browser_inner.pack(fill="x", padx=12, pady=12)

        self.op_btn = ctk.CTkButton(
            browser_inner, text=_t("launcher.operator_controls"),
            fg_color=self.BG3, hover_color=self.ACCENT,
            text_color=self.ACCENT, font=("Segoe UI", 11),
            height=34, command=self._open_operator, state="disabled",
        )
        self.op_btn.pack(fill="x", pady=(0, 5))

        disp_row = ctk.CTkFrame(browser_inner, fg_color="transparent")
        disp_row.pack(fill="x")

        self.main_btn = ctk.CTkButton(
            disp_row, text=_t("launcher.main_display"),
            fg_color=self.BG3, hover_color=self.ACCENT,
            text_color=self.ACCENT, font=("Segoe UI", 11),
            height=34, command=self._open_main, state="disabled",
        )
        self.main_btn.pack(side="left", expand=True, fill="x", padx=(0, 3))

        self.ext_btn = ctk.CTkButton(
            disp_row, text=_t("launcher.extended_display"),
            fg_color=self.BG3, hover_color=self.ACCENT,
            text_color=self.ACCENT, font=("Segoe UI", 11),
            height=34, command=self._open_extended, state="disabled",
        )
        self.ext_btn.pack(side="right", expand=True, fill="x", padx=(3, 0))

        # Extended Features
        ctk.CTkLabel(
            main, text="Extended Features",
            font=("Segoe UI", 11, "bold"), text_color=self.ACCENT,
        ).pack(anchor="w", pady=(0, 4))
        self._ext_frame = ctk.CTkFrame(main, fg_color=self.BG2, corner_radius=8,
                                        border_width=1, border_color=self.BG3)
        self._ext_frame.pack(fill="x", pady=(0, 10))

        ext_inner = ctk.CTkFrame(self._ext_frame, fg_color="transparent")
        ext_inner.pack(fill="x", padx=12, pady=12)

        ext_row = ctk.CTkFrame(ext_inner, fg_color="transparent")
        ext_row.pack(fill="x")

        self.dict_btn = ctk.CTkButton(
            ext_row, text=_t("launcher.dictation"),
            fg_color=self.BG3, hover_color=self.ACCENT,
            text_color=self.ACCENT, font=("Segoe UI", 11),
            height=34, command=self._open_dictation, state="disabled",
        )
        self.dict_btn.pack(side="left", expand=True, fill="x", padx=(0, 3))

        self.bidir_btn = ctk.CTkButton(
            ext_row, text=_t("launcher.bidirectional_display"),
            fg_color=self.BG3, hover_color=self.ACCENT,
            text_color=self.ACCENT, font=("Segoe UI", 11),
            height=34, command=self._open_bidirectional, state="disabled",
        )
        self.bidir_btn.pack(side="right", expand=True, fill="x", padx=(3, 0))

        # Settings
        ctk.CTkLabel(
            main, text=_t("launcher.settings_frame"),
            font=("Segoe UI", 11, "bold"), text_color=self.ACCENT,
        ).pack(anchor="w", pady=(0, 4))
        self._settings_frame = ctk.CTkFrame(main, fg_color=self.BG2, corner_radius=8,
                                             border_width=1, border_color=self.BG3)
        self._settings_frame.pack(fill="x", pady=(0, 10))

        settings_inner = ctk.CTkFrame(self._settings_frame, fg_color="transparent")
        settings_inner.pack(fill="x", padx=12, pady=12)

        # Audio Sources
        audio_header = ctk.CTkFrame(settings_inner, fg_color="transparent")
        audio_header.pack(fill="x")
        self._audio_lbl = ctk.CTkLabel(
            audio_header, text=_t("launcher.audio_sources"),
            font=("Segoe UI", 10, "bold"), text_color=self.ACCENT,
        )
        self._audio_lbl.pack(side="left")
        self._refresh_audio_btn = ctk.CTkButton(
            audio_header, text=_t("launcher.refresh_devices"),
            fg_color=self.BG3, hover_color=self.ACCENT,
            text_color=self.FG, height=22, width=80,
            font=("Segoe UI", 10),
            command=lambda: self._settings_helper.refresh_all_sources(),
        )
        self._refresh_audio_btn.pack(side="right")
        self._source_frames: list[tuple[Any, Any, Any]] = []
        self._sources_container = ctk.CTkFrame(settings_inner, fg_color="transparent")
        self._sources_container.pack(fill="x", pady=(2, 4))
        self._mic_devices: list[tuple[int, str, bool]] = []

        for idx in self.settings.get("source_indices", [-1]):
            self._settings_helper.add_source_row(idx)

        self._add_source_btn = ctk.CTkButton(
            settings_inner, text=_t("launcher.add_source"),
            fg_color=self.BG3, hover_color=self.ACCENT,
            text_color=self.FG, height=30,
            command=lambda: self._settings_helper.add_source_row(),
        )
        self._add_source_btn.pack(fill="x", pady=(0, 8))

        # Backend
        self._backend_lbl = ctk.CTkLabel(
            settings_inner, text=_t("launcher.speech_backend"),
            font=("Segoe UI", 10, "bold"), text_color=self.ACCENT,
        )
        self._backend_lbl.pack(anchor="w")
        self._backend_labels = {
            "auto": _t("launcher.backend_auto"),
            "whisper": _t("launcher.backend_whisper"),
            "vosk": _t("launcher.backend_vosk"),
            "mlx": _t("launcher.backend_mlx"),
        }
        self._backend_from_label = {v: k for k, v in self._backend_labels.items()}
        default_backend = "whisper" if EDITION == "Full" else "auto"
        stored_backend = self.settings.get("backend", default_backend)
        self.backend_var = tk.StringVar(
            value=self._backend_labels.get(stored_backend, stored_backend),
        )
        backend_values = [
            _t("launcher.backend_auto"),
            _t("launcher.backend_whisper"),
            _t("launcher.backend_vosk"),
        ]
        if IS_MAC:
            backend_values.append(_t("launcher.backend_mlx"))
        self._backend_combo = ctk.CTkComboBox(
            settings_inner, variable=self.backend_var,
            values=backend_values, state="readonly",
            font=("Segoe UI", 11),
            fg_color=self.BG, border_color=self.BG3,
            button_color=self.BG3, button_hover_color=self.ACCENT,
            dropdown_fg_color=self.BG, dropdown_hover_color=self.BG3,
        )
        self._backend_combo.pack(fill="x", pady=(2, 8))

        model_grid = ctk.CTkFrame(settings_inner, fg_color="transparent")
        model_grid.pack(fill="x", pady=(4, 0))
        model_grid.columnconfigure(0, weight=1)
        model_grid.columnconfigure(1, weight=1)

        self._tuned_btn = ctk.CTkButton(
            model_grid, text=_t("launcher.download_tuned_models"),
            fg_color=self.BG3, hover_color=self.ACCENT, text_color=self.FG,
            height=30, command=self._model_helper.show_tuned_models_dialog,
        )
        self._tuned_btn.grid(row=0, column=0, sticky="ew", padx=(0, 3), pady=(0, 4))

        self._offline_btn = ctk.CTkButton(
            model_grid, text=_t("launcher.download_offline_models"),
            fg_color=self.BG3, hover_color=self.ACCENT, text_color=self.FG,
            height=30, command=self._model_helper.show_offline_translate_dialog,
        )
        self._offline_btn.grid(row=0, column=1, sticky="ew", padx=(3, 0), pady=(0, 4))

        self._vosk_btn = ctk.CTkButton(
            model_grid, text=_t("launcher.download_vosk_models"),
            fg_color=self.BG3, hover_color=self.ACCENT, text_color=self.FG,
            height=30, command=self._model_helper.show_vosk_models_dialog,
        )
        self._vosk_btn.grid(row=1, column=0, sticky="ew", padx=(0, 3))

        self._delete_btn = ctk.CTkButton(
            model_grid, text=_t("launcher.delete_installed_models"),
            fg_color=self.BG3, hover_color=self.ACCENT, text_color=self.FG,
            height=30, command=self._model_helper.show_model_manager_dialog,
        )
        self._delete_btn.grid(row=1, column=1, sticky="ew", padx=(3, 0))

        self._keys_btn = ctk.CTkButton(
            model_grid, text=_t("launcher.manage_api_keys"),
            fg_color=self.BG3, hover_color=self.ACCENT, text_color=self.FG,
            height=30, command=self._model_helper.show_key_manager_dialog,
        )
        self._keys_btn.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(4, 0))

        # Log Area
        ctk.CTkLabel(
            main, text=_t("launcher.server_log_frame"),
            font=("Segoe UI", 11, "bold"), text_color=self.ACCENT,
        ).pack(anchor="w", pady=(0, 4))
        log_frame = ctk.CTkFrame(main, fg_color=self.BG2, corner_radius=8,
                                  border_width=1, border_color=self.BG3)
        log_frame.pack(fill="both", expand=True, pady=(0, 8))

        self.log_text = ctk.CTkTextbox(
            log_frame, height=180,
            fg_color="#0a0a1a", text_color="#7fdbca",
            font=("Consolas" if IS_WIN else "Menlo", 11),
            corner_radius=6, border_width=0, state="disabled",
        )
        self.log_text.pack(fill="both", expand=True, padx=6, pady=6)

        self.log_text._textbox.tag_configure("info", foreground="#7fdbca")
        self.log_text._textbox.tag_configure("warn", foreground="#FFD54F")
        self.log_text._textbox.tag_configure("error", foreground="#ff6b6b")
        self.log_text._textbox.tag_configure("system", foreground="#4FC3F7")

        # Transcript Location
        ctk.CTkLabel(
            main, text=_t("launcher.transcript_files"),
            font=("Segoe UI", 11, "bold"), text_color=self.ACCENT,
        ).pack(anchor="w", pady=(0, 4))
        tdir_frame = ctk.CTkFrame(main, fg_color=self.BG2, corner_radius=8,
                                   border_width=1, border_color=self.BG3)
        tdir_frame.pack(fill="x", pady=(0, 8))

        tdir_row = ctk.CTkFrame(tdir_frame, fg_color="transparent")
        tdir_row.pack(fill="x", padx=12, pady=10)

        self.tdir_var = tk.StringVar(
            value=self.settings.get("transcripts_dir", str(DEFAULT_TRANSCRIPTS)),
        )
        self.tdir_entry = ctk.CTkEntry(
            tdir_row, textvariable=self.tdir_var,
            font=("Segoe UI", 11), fg_color=self.BG,
            border_color=self.BG3, text_color=self.FG,
        )
        self.tdir_entry.pack(side="left", fill="x", expand=True, padx=(0, 4))

        self._browse_btn = ctk.CTkButton(
            tdir_row, text=_t("launcher.browse"),
            fg_color=self.BG3, hover_color=self.ACCENT, text_color=self.FG,
            width=70, height=28, command=self._settings_helper.browse_tdir,
        )
        self._browse_btn.pack(side="left", padx=(0, 4))

        self.open_tdir_btn = ctk.CTkButton(
            tdir_row, text=_t("launcher.open_transcripts"),
            fg_color=self.BG3, hover_color=self.ACCENT,
            text_color=self.FG, width=110, height=28,
            command=self._open_transcripts_dir,
        )
        self.open_tdir_btn.pack(side="right")

        # Footer
        footer = ctk.CTkFrame(main, fg_color="transparent")
        footer.pack(fill="x")

        self._about_btn = ctk.CTkButton(
            footer, text=_t("launcher.about"),
            fg_color=self.BG3, hover_color=self.ACCENT, text_color=self.FG,
            width=80, height=28, command=self._show_about,
        )
        self._about_btn.pack(side="left")

        ctk.CTkLabel(
            footer, text=f"v{VERSION}",
            font=("Segoe UI", 10), text_color=self.FG2,
        ).pack(side="right")

        # Welcome message
        self._log_system(_t("launcher.log_welcome_version", version=VERSION))
        self._log_system(_t("launcher.log_app_directory", path=APP_DIR))
        self._log_system(_t("launcher.log_transcripts", path=self.tdir_var.get()))
        self._log_system(_t("launcher.log_ready"))

    # ── Drawing ──────────────────────────────────────────────────────

    def _draw_dot(self, color: str) -> None:
        """Draw the coloured status indicator dot."""
        self.status_dot.delete("all")
        self.status_dot.create_oval(2, 2, 10, 10, fill=color, outline="")

    # ── Mousewheel (no-op, called from dialog Destroy bindings) ──────

    def _bind_main_mousewheel(self) -> None:
        """Re-bind mousewheel after a scrollable dialog closes (no-op)."""

    # ── Server Management (delegates to ServerManager) ───────────────

    def _start_server(self) -> None:
        """Start the server subprocess."""
        if self._server_mgr.running:
            return

        # First-run: download speech model if needed
        if self._model_helper.needs_model_download():
            self._log_system(_t("launcher.log_first_run_downloading"))
            self._model_helper.download_models()
            self._log_system(_t("launcher.log_model_setup_complete"))

        self._settings_helper.save_current_settings()

        tdir = Path(self.tdir_var.get().strip())
        try:
            tdir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            messagebox.showerror("Error", _t("launcher.error_create_transcript_dir", error=e))
            return

        backend = self._backend_from_label.get(self.backend_var.get(), self.backend_var.get())
        source_indices = self._settings_helper.get_source_indices()
        cmd = self._server_mgr.build_server_cmd(backend, source_indices, str(tdir))
        self._log_system(_t("launcher.log_starting", command=" ".join(cmd)))

        try:
            self._server_mgr.start(
                cmd,
                transcripts_dir=str(tdir),
                operator_port=self.settings.get("operator_port", 3001),
            )
            self._update_ui_state(running=True)
            self.transcribe_btn.configure(state="normal")
        except FileNotFoundError:
            self._log_error(_t("launcher.error_python_not_found"))
        except Exception as e:
            self._log_error(_t("launcher.error_start_server", error=e))

    def _stop_server(self) -> None:
        """Stop the server subprocess."""
        if not self._server_mgr.running:
            return
        self._log_system(_t("launcher.log_stopping_server"))
        self._server_mgr.stop(operator_port=self.settings.get("operator_port", 3001))
        self._update_ui_state(running=False)
        self.transcribe_btn.configure(state="disabled")
        self._log_system(_t("launcher.log_server_stopped"))

    # ── Log queue processing ─────────────────────────────────────────

    def _poll_log_queue(self) -> None:
        """Process log messages from the server thread (runs on main thread)."""
        try:
            while True:
                msg_type, data = self.log_queue.get_nowait()

                if msg_type == "output":
                    tag = "info"
                    lower = data.lower()
                    if "error" in lower or "failed" in lower or "exception" in lower:
                        tag = "error"
                    elif "warn" in lower:
                        tag = "warn"
                    self._append_log(data, tag)

                elif msg_type == "backend":
                    m = re.search(r"Backend:\s*(.+)", data, re.IGNORECASE)
                    if m:
                        self.backend_label.configure(text=m.group(1).strip())

                elif msg_type == "ready":
                    if not self._server_mgr.ready:
                        self._server_mgr.ready = True
                        self._draw_dot(self.GREEN)
                        self.status_label.configure(
                            text=_t("launcher.status_running"), text_color=self.GREEN,
                        )
                        self._log_system(_t("launcher.log_server_ready"))
                        for btn in (self.op_btn, self.main_btn, self.ext_btn,
                                    self.dict_btn, self.bidir_btn):
                            btn.configure(state="normal")

                elif msg_type == "stopped":
                    if (data is not None
                            and self._server_mgr.server_proc
                            and hasattr(self._server_mgr.server_proc, "pid")
                            and self._server_mgr.server_proc.pid != data):
                        continue
                    self._server_mgr.running = False
                    self._server_mgr.ready = False
                    self._server_mgr.server_proc = None
                    self._update_ui_state(running=False)
                    self._log_system(_t("launcher.log_server_ended"))

        except queue.Empty:
            pass

        if not self._closing:
            self.after(100, self._poll_log_queue)

    # ── UI State ─────────────────────────────────────────────────────

    def _update_ui_state(self, running: bool) -> None:
        """Toggle button states based on server status."""
        if running:
            self.start_btn.configure(state="disabled")
            self.stop_btn.configure(state="normal")
            btn_state = "normal" if self._server_mgr.ready else "disabled"
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

    # ── Logging ──────────────────────────────────────────────────────

    def _append_log(self, text: str, tag: str = "info") -> None:
        """Append a line to the log widget with a timestamp."""
        self.log_text.configure(state="normal")
        ts = time.strftime("%H:%M:%S")
        self.log_text._textbox.insert("end", f"[{ts}] {text}\n", tag)
        self.log_text.see("end")
        lines = int(self.log_text._textbox.index("end-1c").split(".")[0])
        if lines > 500:
            self.log_text._textbox.delete("1.0", f"{lines - 500}.0")
        self.log_text.configure(state="disabled")

    def _log_system(self, text: str) -> None:
        """Log a system-level message (blue)."""
        self._append_log(text, "system")

    def _log_error(self, text: str) -> None:
        """Log an error message (red)."""
        self._append_log(text, "error")

    # ── Browser Actions ──────────────────────────────────────────────

    def _open_browser_when_ready(self, port: int) -> None:
        """Open browser once the server is confirmed ready on *port*."""
        if not self._server_mgr.running:
            messagebox.showwarning(
                _t("launcher.dialog_server_not_running_title"),
                _t("launcher.dialog_server_not_running"),
                parent=self,
            )
            return

        url = f"http://localhost:{port}"

        if self._server_mgr.ready:
            webbrowser.open(url)
            return

        messagebox.showinfo(
            _t("launcher.dialog_server_starting_title"),
            _t("launcher.dialog_server_starting"),
            parent=self,
        )

        def _wait_and_open() -> None:
            for _ in range(30):
                if not self._server_mgr.running:
                    return
                try:
                    urllib.request.urlopen(url, timeout=2)
                    self._server_mgr.ready = True
                    self.log_queue.put(("ready", True))
                    webbrowser.open(url)
                    return
                except Exception:
                    time.sleep(1)
            self.after(
                0,
                lambda: messagebox.showwarning(
                    _t("launcher.dialog_server_not_responding_title"),
                    _t("launcher.dialog_server_not_responding"),
                    parent=self,
                ),
            )

        threading.Thread(target=_wait_and_open, daemon=True).start()

    def _open_operator(self) -> None:
        self._open_browser_when_ready(self.settings.get("operator_port", 3001))

    def _open_main(self) -> None:
        self._open_browser_when_ready(self.settings.get("display_port", 3000))

    def _open_extended(self) -> None:
        self._open_browser_when_ready(self.settings.get("extended_port", 3002))

    def _open_dictation(self) -> None:
        self._open_browser_when_ready(self.settings.get("dictation_port", 3005))

    def _open_bidirectional(self) -> None:
        port = self.settings.get("display_port", 3000)
        if not self._server_mgr.running:
            messagebox.showwarning(
                _t("launcher.dialog_server_not_running_title"),
                _t("launcher.dialog_server_not_running"),
                parent=self,
            )
            return
        url = f"http://localhost:{port}/bidirectional?mode=split"
        if self._server_mgr.ready:
            webbrowser.open(url)
            return
        messagebox.showinfo(
            _t("launcher.dialog_server_starting_title"),
            _t("launcher.dialog_server_starting"),
            parent=self,
        )

        def _wait_and_open() -> None:
            for _ in range(30):
                if not self._server_mgr.running:
                    return
                try:
                    urllib.request.urlopen(f"http://localhost:{port}", timeout=2)
                    self._server_mgr.ready = True
                    self.log_queue.put(("ready", True))
                    webbrowser.open(url)
                    return
                except Exception:
                    time.sleep(1)
            self.after(
                0,
                lambda: messagebox.showwarning(
                    _t("launcher.dialog_server_not_responding_title"),
                    _t("launcher.dialog_server_not_responding"),
                    parent=self,
                ),
            )

        threading.Thread(target=_wait_and_open, daemon=True).start()

    def _open_transcripts_dir(self) -> None:
        """Open the transcript directory in the system file browser."""
        tdir = Path(self.tdir_var.get().strip())
        tdir.mkdir(parents=True, exist_ok=True)
        if IS_WIN:
            os.startfile(str(tdir))
        elif IS_MAC:
            subprocess.Popen(["open", str(tdir)])
        else:
            subprocess.Popen(["xdg-open", str(tdir)])

    # ── About ────────────────────────────────────────────────────────

    def _show_about(self) -> None:
        """Display the About dialog."""
        about = ctk.CTkToplevel(self)
        about.title(_t("launcher.dialog_about_title"))
        about.geometry("400x320")
        about.resizable(False, False)
        about.configure(fg_color=self.BG)
        about.transient(self)
        about.grab_set()

        f = ctk.CTkFrame(about, fg_color="transparent")
        f.pack(fill="both", expand=True)

        ctk.CTkLabel(
            f, text=_t("launcher.dialog_about_heading"),
            font=("Segoe UI", 20, "bold"), text_color=self.ACCENT,
        ).pack(pady=(20, 4))
        ctk.CTkLabel(
            f, text=_t("app.subtitle"),
            font=("Segoe UI", 11), text_color=self.FG2,
        ).pack()
        ctk.CTkLabel(
            f, text=f"Version {VERSION}",
            font=("Segoe UI", 10), text_color=self.FG2,
        ).pack(pady=(8, 16))
        ctk.CTkLabel(
            f, text=_t("launcher.dialog_about_description"), justify="center",
            font=("Segoe UI", 10), text_color=self.FG2,
        ).pack()
        ctk.CTkButton(
            f, text=_t("launcher.close"),
            fg_color=self.BG3, hover_color=self.ACCENT, text_color=self.FG,
            command=about.destroy,
        ).pack(pady=(16, 0))

    # ── Update Checking ──────────────────────────────────────────────

    def _on_update_check_toggled(self) -> None:
        """Save the checkbox state when toggled."""
        self.settings["check_for_updates"] = self.update_check_var.get()
        save_settings(self.settings)

    def _check_github_release(self) -> tuple[str, list, str] | None:
        """Fetch latest release from GitHub."""
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

    def _find_asset_url(self, assets: list, tag: str) -> tuple[str | None, str | None]:
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

    def _check_for_updates_manual(self) -> None:
        """Manual update check triggered by button click."""
        self._do_update_check(manual=True)

    def _do_update_check(self, manual: bool = False) -> None:
        """Run update check in background thread, show result on main thread."""
        def _worker() -> None:
            result = self._check_github_release()
            self.after(0, lambda: self._handle_update_result(result, manual))

        threading.Thread(target=_worker, daemon=True).start()
        if manual:
            self._log_system(_t("launcher.log_checking_updates"))

    def _handle_update_result(self, result: tuple | None, manual: bool) -> None:
        """Process update check result on the main thread."""
        if result is None:
            if manual:
                messagebox.showinfo(
                    _t("launcher.dialog_update_check_title"),
                    _t("launcher.dialog_update_no_internet"),
                    parent=self,
                )
            return

        tag, assets, body = result
        remote_ver = _parse_version(tag)
        local_ver = _parse_version(VERSION)

        if remote_ver is None or local_ver is None:
            if manual:
                messagebox.showinfo(
                    _t("launcher.dialog_update_check_title"),
                    _t("launcher.dialog_update_parse_error", remote=tag, local=VERSION),
                    parent=self,
                )
            return

        if remote_ver <= local_ver:
            if manual:
                messagebox.showinfo(
                    _t("launcher.dialog_update_check_title"),
                    _t("launcher.dialog_update_up_to_date", version=VERSION),
                    parent=self,
                )
            return

        if not manual and self.settings.get("dismissed_version") == tag:
            return

        self._show_update_dialog(tag, assets)

    def _show_update_dialog(self, tag: str, assets: list) -> None:
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

        ctk.CTkLabel(
            f, text=_t("launcher.dialog_update_available_heading", version=version),
            font=("Segoe UI", 12, "bold"),
            text_color=self.ACCENT, fg_color=self.BG,
        ).pack(pady=(0, 4))
        ctk.CTkLabel(
            f, text=_t("launcher.dialog_update_current_version", version=VERSION),
        ).pack(pady=(0, 16))

        btn_frame = ctk.CTkFrame(f)
        btn_frame.pack(fill="x")

        def _download_now() -> None:
            dlg.destroy()
            self._download_update(tag, assets)

        def _remind_later() -> None:
            dlg.destroy()

        def _dont_remind() -> None:
            self.settings["dismissed_version"] = tag
            save_settings(self.settings)
            dlg.destroy()

        ctk.CTkButton(
            btn_frame, text=_t("launcher.dialog_update_download_now"),
            fg_color="#66BB6A", hover_color="#81C784", text_color="#000",
            command=_download_now,
        ).pack(side="left", padx=(0, 8))
        ctk.CTkButton(
            btn_frame, text=_t("launcher.dialog_update_remind_later"),
            command=_remind_later,
        ).pack(side="left", padx=(0, 8))
        ctk.CTkButton(
            btn_frame, text=_t("launcher.dialog_update_dont_remind"),
            command=_dont_remind,
        ).pack(side="left")

        self.wait_window(dlg)

    def _download_update(self, tag: str, assets: list) -> None:
        """Download the installer for the current edition."""
        url, filename = self._find_asset_url(assets, tag)

        if url is None:
            if EDITION == "Dev":
                webbrowser.open(f"https://github.com/{GITHUB_REPO}/releases/tag/{tag}")
                self._log_system(_t("launcher.log_opened_github"))
                return
            messagebox.showerror(
                _t("launcher.dialog_download_no_installer_title"),
                _t("launcher.dialog_download_no_installer", edition=EDITION),
                parent=self,
            )
            return

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

    def _show_download_progress(self, url: str, save_path: Path) -> None:
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
        ctk.CTkLabel(f, textvariable=status_var).pack(pady=(0, 8))

        progress = ctk.CTkProgressBar(f, mode="determinate", width=400)
        progress.pack(pady=(0, 12))

        cancelled = [False]

        def _cancel() -> None:
            cancelled[0] = True

        cancel_btn = ctk.CTkButton(f, text=_t("launcher.dialog_download_cancel"), command=_cancel)
        cancel_btn.pack()

        def _worker() -> None:
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
                                    status_var.set(_t(
                                        "launcher.dialog_download_progress",
                                        downloaded=f"{m:.1f}",
                                        total=f"{t:.1f}",
                                        percent=f"{p:.0f}",
                                    )),
                                ))

                if cancelled[0]:
                    partial.unlink(missing_ok=True)
                    self.after(0, dlg.destroy)
                    return

                if save_path.exists():
                    save_path.unlink()
                partial.rename(save_path)

                self.after(0, lambda: _download_complete(dlg, status_var, progress, cancel_btn))

            except Exception as e:
                partial.unlink(missing_ok=True)

                def _show_error(err: Exception = e) -> None:
                    status_var.set(_t("launcher.dialog_download_failed", error=err))
                    cancel_btn.configure(text=_t("launcher.close"), command=dlg.destroy)
                self.after(0, _show_error)

        def _download_complete(
            dlg: ctk.CTkToplevel,
            status_var: tk.StringVar,
            progress: ctk.CTkProgressBar,
            cancel_btn: ctk.CTkButton,
        ) -> None:
            status_var.set(_t("launcher.dialog_download_complete"))
            progress.configure(value=100)
            cancel_btn.destroy()

            btn_frame = ctk.CTkFrame(f)
            btn_frame.pack(pady=(4, 0))

            def _open_folder() -> None:
                if IS_WIN:
                    subprocess.Popen(["explorer", "/select,", str(save_path)])
                elif IS_MAC:
                    subprocess.Popen(["open", "-R", str(save_path)])
                else:
                    subprocess.Popen(["xdg-open", str(save_path.parent)])
                dlg.destroy()

            ctk.CTkButton(
                btn_frame, text=_t("launcher.dialog_download_open_folder"),
                command=_open_folder,
            ).pack(side="left", padx=(0, 8))
            ctk.CTkButton(
                btn_frame, text=_t("launcher.close"), command=dlg.destroy,
            ).pack(side="left")

            ctk.CTkLabel(
                f, text=_t("launcher.dialog_download_close_reminder"),
            ).pack(pady=(8, 0))

        threading.Thread(target=_worker, daemon=True).start()
        self.wait_window(dlg)

    # ── Language Switching ───────────────────────────────────────────

    def _on_language_changed(self, event: Any = None) -> None:
        """Handle language selection change."""
        selected = self._lang_combo.get()
        if not selected:
            return
        lang_values: list[str] = []
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
        _load_translations(lang, APP_DIR)
        self._refresh_ui()
        if self._server_mgr.running:
            def _notify() -> None:
                try:
                    port = self.settings.get("operator_port", 3001)
                    data = json.dumps({"ui_language": lang}).encode()
                    req = urllib.request.Request(
                        f"http://127.0.0.1:{port}/api/config",
                        data=data,
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    urllib.request.urlopen(req, timeout=2)
                except Exception:
                    logger.debug("Failed to notify server of language change", exc_info=True)
            threading.Thread(target=_notify, daemon=True).start()

    def _refresh_ui(self) -> None:
        """Re-apply all translated strings to UI widgets."""
        for w in list(self.winfo_children()):
            if isinstance(w, ctk.CTkToplevel):
                if getattr(w, "_has_active_download", False):
                    continue
                w.destroy()

        self.title(_t("app.full_name"))

        if EDITION != "Dev":
            self._title_lbl.configure(text=_t("launcher.title_edition", edition=EDITION))
        else:
            self._title_lbl.configure(text=_t("launcher.title_dev"))
        self._subtitle_lbl.configure(text=_t("app.subtitle"))

        self._update_btn.configure(text=_t("launcher.check_for_updates"))
        self._update_chk.configure(text=_t("launcher.check_on_startup"))

        self.start_btn.configure(text=_t("launcher.start_server"))
        self.stop_btn.configure(text=_t("launcher.stop_server"))

        if self._server_mgr.ready:
            self.status_label.configure(text=_t("launcher.status_running"))
        elif self._server_mgr.running:
            self.status_label.configure(text=_t("launcher.status_starting"))
        else:
            self.status_label.configure(text=_t("launcher.status_stopped"))

        self.op_btn.configure(text=_t("launcher.operator_controls"))
        self.main_btn.configure(text=_t("launcher.main_display"))
        self.ext_btn.configure(text=_t("launcher.extended_display"))
        self.dict_btn.configure(text=_t("launcher.dictation"))
        self.bidir_btn.configure(text=_t("launcher.bidirectional_display"))

        self._browse_btn.configure(text=_t("launcher.browse"))
        self._audio_lbl.configure(text=_t("launcher.audio_sources"))
        self._refresh_audio_btn.configure(text=_t("launcher.refresh_devices"))
        self._add_source_btn.configure(text=_t("launcher.add_source"))
        self._backend_lbl.configure(text=_t("launcher.speech_backend"))

        old_backend = self._backend_from_label.get(self.backend_var.get(), self.backend_var.get())
        self._backend_labels = {
            "auto": _t("launcher.backend_auto"),
            "whisper": _t("launcher.backend_whisper"),
            "vosk": _t("launcher.backend_vosk"),
            "mlx": _t("launcher.backend_mlx"),
        }
        self._backend_from_label = {v: k for k, v in self._backend_labels.items()}
        backend_values = [
            _t("launcher.backend_auto"),
            _t("launcher.backend_whisper"),
            _t("launcher.backend_vosk"),
        ]
        if IS_MAC:
            backend_values.append(_t("launcher.backend_mlx"))
        self._backend_combo.configure(values=backend_values)
        self.backend_var.set(self._backend_labels.get(old_backend, old_backend))

        for i, (r, c, v) in enumerate(self._source_frames):
            for child in r.winfo_children():
                if isinstance(child, ctk.CTkLabel):
                    child.configure(text=_t("launcher.source_label", num=i + 1))
                    break

        self._tuned_btn.configure(text=_t("launcher.download_tuned_models"))
        self._offline_btn.configure(text=_t("launcher.download_offline_models"))
        self._delete_btn.configure(text=_t("launcher.delete_installed_models"))
        self._keys_btn.configure(text=_t("launcher.manage_api_keys"))
        self._vosk_btn.configure(text=_t("launcher.download_vosk_models"))

        self.open_tdir_btn.configure(text=_t("launcher.open_transcripts"))
        self._about_btn.configure(text=_t("launcher.about"))

    # ── Cleanup ──────────────────────────────────────────────────────

    def _on_close(self) -> None:
        """Handle window close: tray minimize, confirm quit, or destroy."""
        self._closing = True
        self._settings_helper.save_current_settings()

        if self.settings.get("close_to_tray", True):
            if self._tray.minimize_to_tray():
                self._closing = False
                return

        if self._server_mgr.running:
            if messagebox.askyesno(
                _t("launcher.dialog_quit_title"),
                _t("launcher.dialog_quit_message"),
            ):
                self._stop_server()
            else:
                self._closing = False
                self._poll_log_queue()
                return

        self.destroy()
