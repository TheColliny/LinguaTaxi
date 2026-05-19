"""Model download and management dialogs for the LinguaTaxi launcher."""

from __future__ import annotations

import json
import logging
import os
import queue
import shutil
import subprocess
import sys
import threading
import urllib.request
from pathlib import Path
from tkinter import messagebox
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass

import tkinter as tk
import customtkinter as ctk

from linguataxi.launcher.i18n import _t

logger = logging.getLogger(__name__)

IS_WIN = sys.platform == "win32"


class ModelDownloadHelper:
    """Model download and management logic.

    Parameters
    ----------
    app:
        Reference to the ``LinguaTaxiApp`` instance.
    """

    def __init__(self, app: Any) -> None:
        self._app = app

    @property
    def _app_dir(self) -> Path:
        from linguataxi.launcher.app import APP_DIR
        return APP_DIR

    def _find_python(self) -> str:
        return self._app._server_mgr.find_python()

    # ── First-run check ──────────────────────────────────────────────

    def needs_model_download(self) -> bool:
        """Return ``True`` if no speech models are installed."""
        models_dir = self._app_dir / "models"

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

    def download_models(self) -> None:
        """Show a progress dialog while downloading speech models."""
        app = self._app
        dlg = ctk.CTkToplevel(app)
        dlg.title(_t("launcher.dialog_first_time_title"))
        dlg.geometry("480x220")
        dlg.resizable(False, False)
        dlg.configure(fg_color=app.BG)
        dlg.transient(app)
        dlg.grab_set()
        dlg.protocol("WM_DELETE_WINDOW", lambda: None)

        dlg.update_idletasks()
        px = app.winfo_x() + (app.winfo_width() - 480) // 2
        py = app.winfo_y() + (app.winfo_height() - 220) // 2
        dlg.geometry(f"+{px}+{py}")

        f = ctk.CTkFrame(dlg, fg_color="transparent")
        f.pack(fill="both", expand=True)

        ctk.CTkLabel(
            f,
            text=_t("launcher.dialog_downloading_model"),
            font=("Segoe UI", 12, "bold"),
            foreground=app.ACCENT,
            background=app.BG,
        ).pack(pady=(0, 8))

        status_var = tk.StringVar(value=_t("launcher.dialog_preparing_download"))
        ctk.CTkLabel(f, textvariable=status_var, wraplength=420).pack(pady=(0, 12))

        progress = ctk.CTkProgressBar(f, width=420, mode="indeterminate")
        progress.pack(pady=(0, 12))
        progress.start(15)

        ctk.CTkLabel(
            f,
            text=_t("launcher.dialog_first_time_hint"),
            wraplength=420,
        ).pack()

        download_done = [False]
        dlg._has_active_download = True

        def run_download() -> None:
            try:
                python = self._find_python()
                dl_script = self._app_dir / "download_models.py"

                if not dl_script.exists():
                    status_var.set(_t("launcher.dialog_model_download_fallback"))
                    download_done[0] = True
                    return

                kwargs: dict = {}
                if IS_WIN:
                    kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

                proc = subprocess.Popen(
                    [python, str(dl_script)],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    universal_newlines=True,
                    cwd=str(self._app_dir),
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
                logger.warning("Model download failed: %s", e, exc_info=True)
                status_var.set(_t("launcher.dialog_model_download_fallback"))
            finally:
                download_done[0] = True
                dlg._has_active_download = False

        t = threading.Thread(target=run_download, daemon=True)
        t.start()

        def poll() -> None:
            if download_done[0]:
                progress.stop()
                dlg.destroy()
                return
            dlg.after(200, poll)

        poll()
        app.wait_window(dlg)

    # ── Tuned Models ─────────────────────────────────────────────────

    def get_tuned_model_info(self) -> dict:
        """Get tuned model info by running ``tuned_models.py --list``."""
        models_dir = self._app_dir / "models"
        try:
            python = self._find_python()
            result = subprocess.run(
                [python, str(self._app_dir / "tuned_models.py"), "--list",
                 "--models-dir", str(models_dir)],
                capture_output=True, text=True, timeout=15,
                cwd=str(self._app_dir),
            )
            if result.returncode == 0:
                return json.loads(result.stdout.strip())
        except Exception:
            logger.debug("Failed to get tuned model info", exc_info=True)
        return {}

    def show_tuned_models_dialog(self) -> None:
        """Show dialog for downloading language-tuned Whisper models."""
        app = self._app

        if not (self._app_dir / "tuned_models.py").exists():
            messagebox.showinfo(
                _t("launcher.dialog_tuned_not_available_title"),
                _t("launcher.dialog_tuned_not_available"),
                parent=app,
            )
            return

        dlg = ctk.CTkToplevel(app)
        dlg.title(_t("launcher.dialog_tuned_title"))
        dlg.geometry("520x480")
        dlg.minsize(400, 300)
        dlg.resizable(True, True)
        dlg.configure(fg_color=app.BG)
        dlg.transient(app)
        dlg.grab_set()

        dlg.update_idletasks()
        px = app.winfo_x() + (app.winfo_width() - 520) // 2
        py = app.winfo_y() + (app.winfo_height() - 480) // 2
        dlg.geometry(f"+{px}+{py}")

        f = ctk.CTkFrame(dlg, fg_color="transparent")
        f.pack(fill="both", expand=True)

        ctk.CTkLabel(
            f, text=_t("launcher.dialog_tuned_heading"),
            font=("Segoe UI", 13, "bold"),
            foreground=app.ACCENT, background=app.BG,
        ).pack(pady=(0, 4))

        ctk.CTkLabel(
            f, text=_t("launcher.dialog_tuned_description"),
            justify="center", wraplength=460,
        ).pack(pady=(0, 12))

        model_info = self.get_tuned_model_info()

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
        cb_canvas = tk.Canvas(f, bg=app.BG, highlightthickness=0)
        cb_scrollbar = ctk.CTkScrollbar(f, orient="vertical", command=cb_canvas.yview)
        cb_frame = ctk.CTkFrame(cb_canvas)
        cb_frame.bind(
            "<Configure>",
            lambda e: cb_canvas.configure(scrollregion=cb_canvas.bbox("all")),
        )
        cb_canvas.create_window((0, 0), window=cb_frame, anchor="nw", tags="inner")
        cb_canvas.configure(yscrollcommand=cb_scrollbar.set)

        def _resize_cb(event: Any) -> None:
            cb_canvas.itemconfig("inner", width=event.width)
        cb_canvas.bind("<Configure>", _resize_cb)

        cb_canvas.pack(side="top", fill="both", expand=True, pady=(0, 8))
        cb_scrollbar.pack(in_=f, side="right", fill="y", before=cb_canvas)

        def _cb_mousewheel(event: Any) -> None:
            cb_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        cb_canvas.bind("<Enter>", lambda e: cb_canvas.bind_all("<MouseWheel>", _cb_mousewheel))
        cb_canvas.bind("<Leave>", lambda e: cb_canvas.unbind_all("<MouseWheel>"))
        dlg.bind("<Destroy>", lambda e: app._bind_main_mousewheel() if e.widget == dlg else None)

        check_vars: dict[str, tk.BooleanVar] = {}
        cb_widgets: dict[str, Any] = {}
        for lang, info in model_info.items():
            var = tk.BooleanVar(value=False)
            check_vars[lang] = var

            name = info.get("name", lang)
            size = info.get("size_gb", "?")
            avail = info.get("available", False)

            if avail:
                row = ctk.CTkFrame(cb_frame)
                row.pack(anchor="w", pady=2, fill="x")
                tk.Label(row, text=" \u2713 ", fg="#66BB6A", bg=app.BG,
                         font=("Segoe UI", 10, "bold")).pack(side="left")
                tk.Label(row, text=f"{name} \u2014 ~{size} GB  ",
                         fg=app.FG, bg=app.BG,
                         font=("Segoe UI", 9)).pack(side="left")
                tk.Label(row, text=_t("launcher.dialog_tuned_installed"), fg="#66BB6A", bg=app.BG,
                         font=("Segoe UI", 9, "bold")).pack(side="left")
                cb_widgets[lang] = None
            else:
                text = f"{name} \u2014 ~{size} GB"
                cb = ctk.CTkCheckBox(cb_frame, text=text, variable=var)
                cb.pack(anchor="w", pady=2)
                cb_widgets[lang] = cb

        btn_frame = ctk.CTkFrame(f)
        btn_frame.pack(fill="x", pady=(0, 8))

        dl_btn = ctk.CTkButton(
            btn_frame, text=_t("launcher.download_selected"),
            fg_color="#66BB6A", hover_color="#81C784", text_color="#000",
            command=lambda: _start_download(),
        )
        dl_btn.pack(side="left", padx=(0, 8))

        close_btn = ctk.CTkButton(btn_frame, text=_t("launcher.close"), command=dlg.destroy)
        close_btn.pack(side="right")

        prog_frame = ctk.CTkFrame(f)
        prog_frame.pack(fill="x", pady=(8, 0))

        progress_bar = ctk.CTkProgressBar(prog_frame, mode="determinate", width=400)
        progress_bar.pack_forget()

        status_var = tk.StringVar(value=_t("launcher.dialog_tuned_select_prompt"))
        ctk.CTkLabel(prog_frame, textvariable=status_var, wraplength=460).pack(fill="x")

        ctk.CTkLabel(
            prog_frame, text=_t("launcher.dialog_tuned_hint"), wraplength=460,
        ).pack(fill="x", pady=(8, 0))

        dl_queue: queue.Queue = queue.Queue()

        def _start_download() -> None:
            selected = [
                lang for lang, var in check_vars.items()
                if var.get() and not model_info.get(lang, {}).get("available")
            ]
            if not selected:
                messagebox.showinfo(
                    _t("launcher.dialog_tuned_no_selection_title"),
                    _t("launcher.dialog_tuned_no_selection"),
                    parent=dlg,
                )
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
            models_dir = self._app_dir / "models"
            cmd = [python, str(self._app_dir / "tuned_models.py"),
                   "--download"] + selected + [
                   "--models-dir", str(models_dir)]

            kwargs: dict = {}
            if IS_WIN:
                kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                universal_newlines=True, cwd=str(self._app_dir),
                env={**os.environ, "PYTHONUNBUFFERED": "1"},
                **kwargs,
            )

            total = len(selected)

            def _read_output() -> None:
                completed = 0
                succeeded = 0
                failed = 0
                errors: list[str] = []
                last_output: list[str] = []
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
                                dl_queue.put(("progress", overall, f"[{lang_code}] {msg}"))
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

            threading.Thread(target=_read_output, daemon=True).start()

            def _poll() -> None:
                try:
                    while True:
                        msg_type, val, msg = dl_queue.get_nowait()
                        if msg_type == "progress":
                            progress_bar.set(val / 100)
                            status_var.set(msg)
                        elif msg_type == "status":
                            status_var.set(msg)
                        elif msg_type == "done_ok":
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

    # ── Vosk Models ──────────────────────────────────────────────────

    def show_vosk_models_dialog(self) -> None:
        """Show dialog for downloading Vosk language models."""
        app = self._app

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

        models_dir = self._app_dir / "models"

        dlg = ctk.CTkToplevel(app)
        dlg.title(_t("launcher.dialog_vosk_title"))
        dlg.geometry("520x500")
        dlg.minsize(400, 320)
        dlg.resizable(True, True)
        dlg.configure(fg_color=app.BG)
        dlg.transient(app)
        dlg.grab_set()

        dlg.update_idletasks()
        px = app.winfo_x() + (app.winfo_width() - 520) // 2
        py = app.winfo_y() + (app.winfo_height() - 500) // 2
        dlg.geometry(f"+{px}+{py}")

        f = ctk.CTkFrame(dlg, fg_color="transparent")
        f.pack(fill="both", expand=True)

        ctk.CTkLabel(
            f, text=_t("launcher.dialog_vosk_heading"),
            font=("Segoe UI", 13, "bold"),
            foreground=app.ACCENT, background=app.BG,
        ).pack(pady=(0, 4))

        ctk.CTkLabel(
            f, text=_t("launcher.dialog_vosk_description"),
            justify="center", wraplength=460,
        ).pack(pady=(0, 12))

        cb_canvas = tk.Canvas(f, bg=app.BG, highlightthickness=0)
        cb_scrollbar = ctk.CTkScrollbar(f, orient="vertical", command=cb_canvas.yview)
        cb_frame = ctk.CTkFrame(cb_canvas)
        cb_frame.bind(
            "<Configure>",
            lambda e: cb_canvas.configure(scrollregion=cb_canvas.bbox("all")),
        )
        cb_canvas.create_window((0, 0), window=cb_frame, anchor="nw", tags="inner")
        cb_canvas.configure(yscrollcommand=cb_scrollbar.set)

        def _resize_cb(event: Any) -> None:
            cb_canvas.itemconfig("inner", width=event.width)
        cb_canvas.bind("<Configure>", _resize_cb)

        cb_canvas.pack(side="top", fill="both", expand=True, pady=(0, 8))
        cb_scrollbar.pack(in_=f, side="right", fill="y", before=cb_canvas)

        def _cb_mousewheel(event: Any) -> None:
            cb_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        cb_canvas.bind("<Enter>", lambda e: cb_canvas.bind_all("<MouseWheel>", _cb_mousewheel))
        cb_canvas.bind("<Leave>", lambda e: cb_canvas.unbind_all("<MouseWheel>"))
        dlg.bind("<Destroy>", lambda e: app._bind_main_mousewheel() if e.widget == dlg else None)

        check_vars: dict[str, tk.BooleanVar] = {}
        cb_widgets: dict[str, Any] = {}
        installed_status: dict[str, bool] = {}

        for lang, info in VOSK_MODELS.items():
            installed = (models_dir / info["dir"]).exists()
            installed_status[lang] = installed
            var = tk.BooleanVar(value=False)
            check_vars[lang] = var

            if installed:
                row = ctk.CTkFrame(cb_frame)
                row.pack(anchor="w", pady=2, fill="x")
                tk.Label(row, text=" \u2713 ", fg="#66BB6A", bg=app.BG,
                         font=("Segoe UI", 10, "bold")).pack(side="left")
                tk.Label(row, text=f"{info['name']} \u2014 {info['size']}  ",
                         fg=app.FG, bg=app.BG,
                         font=("Segoe UI", 9)).pack(side="left")
                tk.Label(row, text=_t("launcher.dialog_tuned_installed"), fg="#66BB6A", bg=app.BG,
                         font=("Segoe UI", 9, "bold")).pack(side="left")
                cb_widgets[lang] = None
            else:
                text = f"{info['name']} \u2014 {info['size']}"
                cb = ctk.CTkCheckBox(cb_frame, text=text, variable=var)
                cb.pack(anchor="w", pady=2)
                cb_widgets[lang] = cb

        btn_frame = ctk.CTkFrame(f)
        btn_frame.pack(fill="x", pady=(0, 8))

        dl_btn = ctk.CTkButton(
            btn_frame, text=_t("launcher.download_selected"),
            fg_color="#66BB6A", hover_color="#81C784", text_color="#000",
            command=lambda: _start_download(),
        )
        dl_btn.pack(side="left", padx=(0, 8))

        close_btn = ctk.CTkButton(btn_frame, text=_t("launcher.close"), command=dlg.destroy)
        close_btn.pack(side="right")

        prog_frame = ctk.CTkFrame(f)
        prog_frame.pack(fill="x", pady=(8, 0))

        progress_bar = ctk.CTkProgressBar(prog_frame, mode="determinate", width=400)
        progress_bar.pack_forget()

        status_var = tk.StringVar(value=_t("launcher.dialog_vosk_select_prompt"))
        ctk.CTkLabel(prog_frame, textvariable=status_var, wraplength=460).pack(fill="x")

        ctk.CTkLabel(
            prog_frame, text=_t("launcher.dialog_vosk_hint"), wraplength=460,
        ).pack(fill="x", pady=(8, 0))

        dl_queue: queue.Queue = queue.Queue()

        def _start_download() -> None:
            selected = [
                lang for lang, var in check_vars.items()
                if var.get() and not installed_status.get(lang)
            ]
            if not selected:
                messagebox.showinfo(
                    _t("launcher.dialog_tuned_no_selection_title"),
                    _t("launcher.dialog_tuned_no_selection"),
                    parent=dlg,
                )
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

            def _download_all() -> None:
                succeeded = 0
                failed = 0
                errors: list[str] = []
                for lang in selected:
                    info = VOSK_MODELS[lang]
                    url = info["url"]
                    dest_dir = info["dir"]
                    zip_path = models_dir / f"{dest_dir}.zip"
                    try:
                        models_dir.mkdir(parents=True, exist_ok=True)
                        dl_queue.put(("status", 0, f"Downloading {info['name']}..."))

                        def _report_hook(block_num: int, block_size: int, total_size: int,
                                         _lang: str = lang, _name: str = info["name"]) -> None:
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
                        if zip_path.exists():
                            zip_path.unlink(missing_ok=True)
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

            threading.Thread(target=_download_all, daemon=True).start()

            def _poll() -> None:
                try:
                    while True:
                        msg_type, val, msg = dl_queue.get_nowait()
                        if msg_type == "progress":
                            progress_bar.set(val / 100)
                            status_var.set(msg)
                        elif msg_type == "status":
                            status_var.set(msg)
                        elif msg_type == "done_ok":
                            pass
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

    # ── Offline Translation Models ───────────────────────────────────

    def get_offline_translate_info(self) -> dict:
        """Get offline translation model info via ``offline_translate.py --list``."""
        models_dir = self._app_dir / "models"
        try:
            python = self._find_python()
            result = subprocess.run(
                [python, str(self._app_dir / "offline_translate.py"), "--list",
                 "--models-dir", str(models_dir)],
                capture_output=True, text=True, timeout=15,
                cwd=str(self._app_dir),
            )
            if result.returncode == 0:
                return json.loads(result.stdout.strip())
        except Exception:
            logger.debug("Failed to get offline translate info", exc_info=True)
        return {}

    def show_offline_translate_dialog(self) -> None:
        """Show dialog for downloading offline translation models."""
        app = self._app

        if not (self._app_dir / "offline_translate.py").exists():
            messagebox.showinfo(
                _t("launcher.dialog_tuned_not_available_title"),
                _t("launcher.dialog_offline_not_available"),
                parent=app,
            )
            return

        dlg = ctk.CTkToplevel(app)
        dlg.title(_t("launcher.dialog_offline_title"))
        dlg.geometry("560x580")
        dlg.minsize(440, 350)
        dlg.resizable(True, True)
        dlg.configure(fg_color=app.BG)
        dlg.transient(app)
        dlg.grab_set()

        dlg.update_idletasks()
        px = app.winfo_x() + (app.winfo_width() - 560) // 2
        py = app.winfo_y() + (app.winfo_height() - 580) // 2
        dlg.geometry(f"+{px}+{py}")

        f = ctk.CTkFrame(dlg, fg_color="transparent")
        f.pack(fill="both", expand=True)

        ctk.CTkLabel(
            f, text=_t("launcher.dialog_offline_heading"),
            font=("Segoe UI", 13, "bold"),
            foreground=app.ACCENT, background=app.BG,
        ).pack(pady=(0, 4))

        ctk.CTkLabel(
            f, text=_t("launcher.dialog_offline_description"),
            justify="center", wraplength=500,
        ).pack(pady=(0, 12))

        model_info = self.get_offline_translate_info()

        opus_models = model_info.get("opus", {})
        m2m_info = model_info.get("m2m100", {})

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

        ol_canvas = tk.Canvas(f, bg=app.BG, highlightthickness=0)
        ol_scrollbar = ctk.CTkScrollbar(f, orient="vertical", command=ol_canvas.yview)
        ol_inner = ctk.CTkFrame(ol_canvas)
        ol_inner.bind(
            "<Configure>",
            lambda e: ol_canvas.configure(scrollregion=ol_canvas.bbox("all")),
        )
        ol_canvas.create_window((0, 0), window=ol_inner, anchor="nw", tags="inner")
        ol_canvas.configure(yscrollcommand=ol_scrollbar.set)

        def _resize_ol(event: Any) -> None:
            ol_canvas.itemconfig("inner", width=event.width)
        ol_canvas.bind("<Configure>", _resize_ol)

        ol_canvas.pack(side="top", fill="both", expand=True, pady=(0, 8))
        ol_scrollbar.pack(in_=f, side="right", fill="y", before=ol_canvas)

        def _ol_mousewheel(event: Any) -> None:
            ol_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        ol_canvas.bind("<Enter>", lambda e: ol_canvas.bind_all("<MouseWheel>", _ol_mousewheel))
        ol_canvas.bind("<Leave>", lambda e: ol_canvas.unbind_all("<MouseWheel>"))
        dlg.bind("<Destroy>", lambda e: app._bind_main_mousewheel() if e.widget == dlg else None)

        # OPUS-MT section
        ctk.CTkLabel(ol_inner, text=_t("launcher.dialog_offline_opus_section")).pack(anchor="w", pady=(4, 2))

        opus_frame = ctk.CTkFrame(ol_inner)
        opus_frame.pack(fill="x", pady=(0, 8))

        opus_vars: dict[str, tk.BooleanVar] = {}
        opus_cbs: dict[str, Any] = {}
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
                tk.Label(row, text=" \u2713 ", fg="#66BB6A", bg=app.BG,
                         font=("Segoe UI", 9, "bold")).pack(side="left")
                tk.Label(row, text=f"{name} ({lang}) \u2014 ~{size} MB download  ",
                         fg=app.FG, bg=app.BG,
                         font=("Segoe UI", 9)).pack(side="left")
                tk.Label(row, text=_t("launcher.dialog_offline_installed"), fg="#66BB6A", bg=app.BG,
                         font=("Segoe UI", 9, "bold")).pack(side="left")
                opus_cbs[lang] = None
            else:
                text = f"{name} ({lang}) \u2014 ~{size} MB download"
                cb = ctk.CTkCheckBox(opus_frame, text=text, variable=var)
                cb.pack(anchor="w", pady=1)
                opus_cbs[lang] = cb

        # M2M-100 section
        ctk.CTkLabel(ol_inner, text=_t("launcher.dialog_offline_m2m_section")).pack(anchor="w", pady=(8, 2))

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
            tk.Label(row, text=" \u2713 ", fg="#66BB6A", bg=app.BG,
                     font=("Segoe UI", 9, "bold")).pack(side="left")
            tk.Label(row, text=f"{m2m_name} \u2014 ~{m2m_size_str}  ",
                     fg=app.FG, bg=app.BG,
                     font=("Segoe UI", 9)).pack(side="left")
            tk.Label(row, text=_t("launcher.dialog_offline_installed"), fg="#66BB6A", bg=app.BG,
                     font=("Segoe UI", 9, "bold")).pack(side="left")
        else:
            m2m_text = f"{m2m_name} \u2014 ~{m2m_size_str} download (covers Arabic, Japanese, Chinese, Korean, etc.)"
            m2m_cb = ctk.CTkCheckBox(m2m_frame, text=m2m_text, variable=m2m_var)
            m2m_cb.pack(anchor="w")

        btn_frame = ctk.CTkFrame(f)
        btn_frame.pack(fill="x", pady=(8, 4))

        dl_btn = ctk.CTkButton(
            btn_frame, text=_t("launcher.download_selected"),
            fg_color="#66BB6A", hover_color="#81C784", text_color="#000",
            command=lambda: _start_download(),
        )
        dl_btn.pack(side="left", padx=(0, 8))

        close_btn = ctk.CTkButton(btn_frame, text=_t("launcher.close"), command=dlg.destroy)
        close_btn.pack(side="right")

        prog_frame = ctk.CTkFrame(f)
        prog_frame.pack(fill="x", pady=(8, 0))

        progress_bar = ctk.CTkProgressBar(prog_frame, mode="determinate", width=400)
        progress_bar.pack_forget()

        status_var = tk.StringVar(value=_t("launcher.dialog_offline_select_prompt"))
        ctk.CTkLabel(prog_frame, textvariable=status_var, wraplength=500).pack(fill="x")

        ctk.CTkLabel(
            prog_frame, text=_t("launcher.dialog_offline_hint"), wraplength=500,
        ).pack(fill="x", pady=(8, 0))

        dl_queue: queue.Queue = queue.Queue()

        def _start_download() -> None:
            opus_selected = [
                lang for lang, var in opus_vars.items()
                if var.get() and not opus_models.get(lang, {}).get("available")
            ]
            want_m2m = m2m_var.get() and not m2m_avail

            if not opus_selected and not want_m2m:
                messagebox.showinfo(
                    _t("launcher.dialog_offline_no_selection_title"),
                    _t("launcher.dialog_offline_no_selection"),
                    parent=dlg,
                )
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
            models_dir = self._app_dir / "models"

            cmds: list[list[str]] = []
            if opus_selected:
                cmds.append(
                    [python, str(self._app_dir / "offline_translate.py"),
                     "--download-opus"] + opus_selected +
                    ["--models-dir", str(models_dir)]
                )
            if want_m2m:
                cmds.append(
                    [python, str(self._app_dir / "offline_translate.py"),
                     "--download-m2m", "--models-dir", str(models_dir)]
                )

            total_steps = len(opus_selected) + (1 if want_m2m else 0)

            kwargs: dict = {}
            if IS_WIN:
                kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

            def _run_cmds() -> None:
                completed = 0
                succeeded = 0
                failed = 0
                errors: list[str] = []
                last_output: list[str] = []
                for cmd in cmds:
                    try:
                        proc = subprocess.Popen(
                            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            universal_newlines=True, cwd=str(self._app_dir),
                            env={**os.environ, "PYTHONUNBUFFERED": "1"},
                            **kwargs,
                        )
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

                    if proc.returncode != 0 and completed == 0:
                        failed += 1
                        err_detail = last_output[-1] if last_output else f"Process exited with code {proc.returncode}"
                        errors.append(err_detail)
                        dl_queue.put(("done_err", "process", err_detail))

                if completed == 0 and failed == 0:
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

            threading.Thread(target=_run_cmds, daemon=True).start()

            def _poll() -> None:
                try:
                    while True:
                        msg_type, val, msg = dl_queue.get_nowait()
                        if msg_type == "progress":
                            progress_bar.set(val / 100)
                            status_var.set(msg)
                        elif msg_type == "status":
                            status_var.set(msg)
                        elif msg_type == "done_ok":
                            pass
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
                            new_info = self.get_offline_translate_info()
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

    # ── Model Manager ────────────────────────────────────────────────

    def show_model_manager_dialog(self) -> None:
        """Show dialog to view, update, and delete installed models."""
        app = self._app

        dlg = ctk.CTkToplevel(app)
        dlg.title(_t("launcher.dialog_models_title"))
        dlg.geometry("680x620")
        dlg.minsize(500, 350)
        dlg.resizable(True, True)
        dlg.configure(fg_color=app.BG)
        dlg.transient(app)
        dlg.grab_set()

        dlg.update_idletasks()
        px = app.winfo_x() + (app.winfo_width() - 680) // 2
        py = app.winfo_y() + (app.winfo_height() - 620) // 2
        dlg.geometry(f"+{px}+{py}")

        f = ctk.CTkFrame(dlg, fg_color="transparent")
        f.pack(fill="both", expand=True)

        ctk.CTkLabel(
            f, text=_t("launcher.dialog_models_heading"),
            font=("Segoe UI", 13, "bold"),
            foreground=app.ACCENT, background=app.BG,
        ).pack(pady=(0, 4))

        status_var = tk.StringVar(value=_t("launcher.dialog_models_loading"))
        ctk.CTkLabel(f, textvariable=status_var, wraplength=560).pack(fill="x", pady=(0, 8))

        canvas = tk.Canvas(f, bg=app.BG, highlightthickness=0)
        scrollbar = ctk.CTkScrollbar(f, orient="vertical", command=canvas.yview)
        list_frame = ctk.CTkFrame(canvas)

        list_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all")),
        )
        canvas.create_window((0, 0), window=list_frame, anchor="nw", tags="inner")
        canvas.configure(yscrollcommand=scrollbar.set)

        def _resize_mgr(event: Any) -> None:
            canvas.itemconfig("inner", width=event.width)
        canvas.bind("<Configure>", _resize_mgr)

        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        def _on_mousewheel(event: Any) -> None:
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        canvas.bind("<Enter>", lambda e: canvas.bind_all("<MouseWheel>", _on_mousewheel))
        canvas.bind("<Leave>", lambda e: canvas.unbind_all("<MouseWheel>"))
        dlg.bind("<Destroy>", lambda e: app._bind_main_mousewheel() if e.widget == dlg else None)

        btn_frame = ctk.CTkFrame(f)
        btn_frame.pack(fill="x", pady=(8, 0))

        total_var = tk.StringVar(value="")
        ctk.CTkLabel(btn_frame, textvariable=total_var).pack(side="left")

        ctk.CTkButton(
            btn_frame, text=_t("launcher.dialog_models_refresh"),
            command=lambda: _populate(),
        ).pack(side="right", padx=(8, 0))
        ctk.CTkButton(btn_frame, text=_t("launcher.close"), command=dlg.destroy).pack(side="right")

        python = self._find_python()
        models_dir = self._app_dir / "models"

        def _fmt_size(size_bytes: int) -> str:
            if size_bytes <= 0:
                return "\u2014"
            if size_bytes >= 1024 ** 3:
                return f"{size_bytes / (1024**3):.1f} GB"
            return f"{size_bytes / (1024**2):.0f} MB"

        def _get_speech_models() -> list[dict]:
            results: list[dict] = []
            whisper_dir = models_dir / "faster-whisper-large-v3-turbo"
            if (whisper_dir / "model.bin").exists():
                size = sum(f.stat().st_size for f in whisper_dir.rglob("*") if f.is_file())
                results.append({"name": "Whisper large-v3-turbo (GPU)", "path": whisper_dir,
                                "size": size, "type": "speech", "key": "whisper"})
            for vdir in sorted(models_dir.glob("vosk-model-*")):
                if vdir.is_dir():
                    size = sum(f.stat().st_size for f in vdir.rglob("*") if f.is_file())
                    results.append({"name": f"Vosk {vdir.name} (CPU)", "path": vdir,
                                    "size": size, "type": "speech", "key": vdir.name})
            return results

        def _get_tuned_models() -> dict:
            try:
                result = subprocess.run(
                    [python, str(self._app_dir / "tuned_models.py"), "--list",
                     "--models-dir", str(models_dir)],
                    capture_output=True, text=True, timeout=15, cwd=str(self._app_dir))
                if result.returncode == 0:
                    return json.loads(result.stdout.strip())
            except Exception:
                logger.debug("Failed to list tuned models", exc_info=True)
            return {}

        def _get_translate_models() -> dict:
            try:
                result = subprocess.run(
                    [python, str(self._app_dir / "offline_translate.py"), "--list",
                     "--models-dir", str(models_dir)],
                    capture_output=True, text=True, timeout=15, cwd=str(self._app_dir))
                if result.returncode == 0:
                    return json.loads(result.stdout.strip())
            except Exception:
                logger.debug("Failed to list translate models", exc_info=True)
            return {}

        def _delete_model(model_type: str, key: str, name: str) -> None:
            if not messagebox.askyesno(
                _t("launcher.dialog_models_delete_confirm_title"),
                _t("launcher.dialog_models_delete_confirm", name=name),
                parent=dlg,
            ):
                return

            delete_text = _t("launcher.dialog_models_deleting")
            if delete_text != "launcher.dialog_models_deleting":
                status_var.set(_t("launcher.dialog_models_deleting", name=name))
            else:
                status_var.set(f"Deleting {name}...")
            dlg.config(cursor="wait")

            def _do_delete() -> None:
                kwargs_d: dict = {}
                if IS_WIN:
                    kwargs_d["creationflags"] = subprocess.CREATE_NO_WINDOW
                try:
                    if model_type == "speech":
                        path = models_dir / key
                        if path.exists():
                            shutil.rmtree(path)
                    elif model_type == "tuned":
                        subprocess.run(
                            [python, str(self._app_dir / "tuned_models.py"),
                             "--delete", key, "--models-dir", str(models_dir)],
                            capture_output=True, timeout=30, cwd=str(self._app_dir), **kwargs_d)
                    elif model_type == "opus":
                        subprocess.run(
                            [python, str(self._app_dir / "offline_translate.py"),
                             "--delete-opus", key, "--models-dir", str(models_dir)],
                            capture_output=True, timeout=30, cwd=str(self._app_dir), **kwargs_d)
                    elif model_type == "m2m":
                        subprocess.run(
                            [python, str(self._app_dir / "offline_translate.py"),
                             "--delete-m2m", "--models-dir", str(models_dir)],
                            capture_output=True, timeout=30, cwd=str(self._app_dir), **kwargs_d)
                    dlg.after(0, lambda: status_var.set(_t("launcher.dialog_models_deleted", name=name)))
                except Exception as e:
                    dlg.after(0, lambda: status_var.set(_t("launcher.error_delete_failed", error=e)))
                finally:
                    dlg.after(0, lambda: (dlg.config(cursor=""), _populate()))

            threading.Thread(target=_do_delete, daemon=True).start()

        def _add_section_header(parent: Any, text: str) -> None:
            lbl = tk.Label(parent, text=text, fg=app.ACCENT, bg=app.BG,
                           font=("Segoe UI", 11, "bold"), anchor="w")
            lbl.pack(fill="x", pady=(8, 2))
            sep = ctk.CTkFrame(parent, height=1, fg_color=app.BG3)
            sep.pack(fill="x", pady=(0, 4))

        def _add_model_row(parent: Any, name: str, size_str: str,
                           model_type: str, key: str) -> None:
            row = tk.Frame(parent, bg=app.BG)
            row.pack(fill="x", pady=2, padx=4)

            tk.Label(row, text="\u25cf", fg="#66BB6A", bg=app.BG,
                     font=("Segoe UI", 9)).pack(side="left", padx=(0, 4))
            tk.Label(row, text=name, fg=app.FG, bg=app.BG,
                     font=("Segoe UI", 9), anchor="w").pack(side="left", fill="x", expand=True)
            tk.Label(row, text=size_str, fg="#999", bg=app.BG,
                     font=("Segoe UI", 9)).pack(side="left", padx=(8, 8))
            tk.Button(
                row, text="  " + _t("launcher.dialog_models_delete_btn") + "  ",
                fg="#fff", bg="#c62828", activeforeground="#fff", activebackground="#f44336",
                font=("Segoe UI", 8, "bold"), relief="raised", cursor="hand2", bd=1,
                command=lambda mt=model_type, k=key, n=name: _delete_model(mt, k, n),
            ).pack(side="right", padx=(4, 0))

        def _populate() -> None:
            for widget in list_frame.winfo_children():
                widget.destroy()

            total_bytes = 0

            _add_section_header(list_frame, _t("launcher.dialog_models_speech_section"))
            speech = _get_speech_models()
            if speech:
                for m in speech:
                    total_bytes += m["size"]
                    _add_model_row(list_frame, m["name"], _fmt_size(m["size"]),
                                   "speech", m["key"])
            else:
                tk.Label(list_frame, text="  " + _t("launcher.dialog_models_no_speech"),
                         fg="#666", bg=app.BG, font=("Segoe UI", 9, "italic")).pack(anchor="w")

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
                         fg="#666", bg=app.BG, font=("Segoe UI", 9, "italic")).pack(anchor="w")
            elif not has_tuned:
                tk.Label(list_frame, text="  " + _t("launcher.dialog_models_no_tuned"),
                         fg="#666", bg=app.BG, font=("Segoe UI", 9, "italic")).pack(anchor="w")

            _add_section_header(list_frame, _t("launcher.dialog_models_translate_section"))
            translate = _get_translate_models()
            opus = translate.get("opus", {})
            m2m = translate.get("m2m100", {})

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

            if m2m and m2m.get("available", False):
                m2m_name_val = m2m.get("name", "M2M-100")
                m2m_dir = models_dir / "translate" / "m2m100-1.2b"
                size = sum(f.stat().st_size for f in m2m_dir.rglob("*") if f.is_file()) if m2m_dir.exists() else 0
                total_bytes += size
                _add_model_row(list_frame, m2m_name_val, _fmt_size(size), "m2m", "m2m100")

            if not translate or (not has_opus and not (m2m and m2m.get("available", False))):
                tk.Label(list_frame, text="  " + _t("launcher.dialog_models_no_translate"),
                         fg="#666", bg=app.BG, font=("Segoe UI", 9, "italic")).pack(anchor="w")

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

        def _bg_populate() -> None:
            dlg.config(cursor="wait")
            dlg.after(100, lambda: (_populate(), dlg.config(cursor="")))

        _bg_populate()
