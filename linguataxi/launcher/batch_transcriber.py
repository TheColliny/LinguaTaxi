"""Batch file transcription dialog for the LinguaTaxi launcher."""

from __future__ import annotations

import json
import logging
import subprocess
import sys
import threading
import urllib.parse
import urllib.request
from pathlib import Path
from typing import TYPE_CHECKING, Any

import tkinter as tk
import customtkinter as ctk

from linguataxi.launcher.i18n import _t

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

IS_WIN = sys.platform == "win32"

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


class BatchTranscriber:
    """Batch transcription dialog controller.

    Parameters
    ----------
    app:
        Reference to the ``LinguaTaxiApp`` instance.
    """

    def __init__(self, app: Any) -> None:
        self._app = app

    def transcribe_file(self) -> None:
        """Open the batch processing dialog (only if server is running)."""
        if not self._app._server_mgr.running:
            return
        self.show_transcribe_dialog()

    def show_transcribe_dialog(self) -> None:
        """Build and display the batch processing dialog."""
        app = self._app
        from tkinter import filedialog, messagebox

        dlg = ctk.CTkToplevel(app)
        dlg.title("Batch Processing")
        dlg.geometry("460x520")
        dlg.resizable(False, False)
        dlg.configure(fg_color=app.BG)
        dlg.transient(app)
        dlg.grab_set()

        dlg.update_idletasks()
        px = app.winfo_x() + (app.winfo_width() - 460) // 2
        py = app.winfo_y() + (app.winfo_height() - 520) // 2
        dlg.geometry(f"+{px}+{py}")

        f = ctk.CTkFrame(dlg, fg_color="transparent")
        f.pack(fill="both", expand=True, padx=16, pady=12)

        # Shared state
        port = app.settings.get("operator_port", 3001)
        base_url = f"http://localhost:{port}"
        selection: dict[str, Any] = {
            "file_path": None, "folder_path": None,
            "is_audio": False, "is_text": False, "is_folder": False,
        }
        output_dir_var = tk.StringVar(value="")
        source_lang = ["EN"]

        def _fetch_source_lang() -> None:
            try:
                resp = urllib.request.urlopen(f"{base_url}/api/config", timeout=3)
                cfg = json.loads(resp.read())
                source_lang[0] = cfg.get("input_lang", "EN")
                dlg.after(0, _refresh_engine_options)
            except Exception:
                logger.debug("Failed to fetch source language", exc_info=True)
        threading.Thread(target=_fetch_source_lang, daemon=True).start()

        # File / Folder selection row
        sel_row = ctk.CTkFrame(f, fg_color="transparent")
        sel_row.pack(fill="x", pady=(0, 4))

        def pick_file() -> None:
            fp = filedialog.askopenfilename(
                title="Select File",
                filetypes=[
                    ("Supported Files",
                     "*.wav *.mp3 *.flac *.m4a *.ogg *.webm *.txt *.srt *.vtt *.md"),
                    ("Audio Files", "*.wav *.mp3 *.flac *.m4a *.ogg *.webm"),
                    ("Text Files", "*.txt *.srt *.vtt *.md"),
                    ("All Files", "*.*"),
                ],
            )
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

        def pick_folder() -> None:
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

        ctk.CTkButton(
            sel_row, text="Select File", width=130, height=30,
            fg_color=app.ACCENT, hover_color="#81D4FA",
            text_color="#000", font=("Segoe UI", 11),
            command=pick_file,
        ).pack(side="left", padx=(0, 6))
        ctk.CTkButton(
            sel_row, text="Select Folder", width=130, height=30,
            fg_color=app.ACCENT, hover_color="#81D4FA",
            text_color="#000", font=("Segoe UI", 11),
            command=pick_folder,
        ).pack(side="left")

        path_var = tk.StringVar(value="No file or folder selected")
        path_lbl = ctk.CTkLabel(
            f, textvariable=path_var,
            font=("Segoe UI", 10), text_color=app.ACCENT,
            wraplength=420, anchor="w", justify="left",
        )
        path_lbl.pack(fill="x", pady=(2, 4))

        subfolder_var = tk.BooleanVar(value=False)
        subfolder_frame = ctk.CTkFrame(f, fg_color="transparent")
        ctk.CTkCheckBox(
            subfolder_frame, text="Include subfolders",
            variable=subfolder_var, font=("Segoe UI", 11),
            text_color=app.FG,
        ).pack(anchor="w")

        # Live playback checkbox
        live_var = tk.BooleanVar(value=False)
        live_frame = ctk.CTkFrame(f, fg_color="transparent")
        ctk.CTkCheckBox(
            live_frame, text="Play as Live Input",
            variable=live_var, font=("Segoe UI", 11),
            text_color=app.FG,
            command=lambda: _toggle_live_mode(),
        ).pack(anchor="w")

        # Translation engine dropdown
        engine_frame = ctk.CTkFrame(f, fg_color="transparent")
        engine_var = tk.StringVar(value="none")

        def _get_engine_options() -> list[str]:
            opts = ["No Translation", "DeepL (Online)"]
            if source_lang[0].upper().startswith("EN"):
                opts.append("Offline (Language Specific)")
            opts.append("Offline (M2M 100+ Languages)")
            return opts

        def _engine_display_to_key(display: str) -> str:
            for k, v in BATCH_ENGINE_NAMES.items():
                if v == display:
                    return k
            return "none"

        def _engine_key_to_display(key: str) -> str:
            return BATCH_ENGINE_NAMES.get(key, "No Translation")

        ctk.CTkLabel(
            engine_frame, text="Translation Engine:",
            font=("Segoe UI", 11, "bold"), text_color=app.FG,
        ).pack(anchor="w", pady=(0, 2))

        engine_display_var = tk.StringVar(value="No Translation")
        engine_menu = ctk.CTkOptionMenu(
            engine_frame, variable=engine_display_var,
            values=_get_engine_options(),
            width=300, height=28, font=("Segoe UI", 11),
            command=lambda val: _on_engine_change(val),
        )
        engine_menu.pack(anchor="w")

        def _refresh_engine_options() -> None:
            engine_menu.configure(values=_get_engine_options())

        # Language slots
        lang_frame = ctk.CTkFrame(f, fg_color="transparent")
        lang_rows: list[dict[str, Any]] = []

        def _get_lang_dict() -> dict[str, str]:
            key = engine_var.get()
            if key == "deepl":
                return BATCH_DEEPL_LANGS
            elif key == "offline-opus":
                return BATCH_OPUS_LANGS
            elif key == "offline-m2m":
                return BATCH_M2M_LANGS
            return {}

        def _available_display_values(exclude_var: Any = None) -> list[str]:
            ld = _get_lang_dict()
            selected_set: set[str] = set()
            for row in lang_rows:
                if row["var"] is not exclude_var:
                    val = row["var"].get()
                    if val:
                        for code, name in ld.items():
                            if f"{name} ({code})" == val:
                                selected_set.add(code)
                                break
            return [
                f"{name} ({code})"
                for code, name in sorted(ld.items(), key=lambda x: x[1])
                if code not in selected_set
            ]

        def _refresh_all_dropdowns() -> None:
            for row in lang_rows:
                avail = _available_display_values(exclude_var=row["var"])
                current = row["var"].get()
                if current and current not in avail:
                    avail.insert(0, current)
                row["menu"].configure(values=avail if avail else ["\u2014"])

        def _on_lang_change(val: str) -> None:
            _refresh_all_dropdowns()

        def _add_lang_row(preset_display: str | None = None) -> None:
            row_frame = ctk.CTkFrame(lang_frame, fg_color="transparent")
            row_frame.pack(fill="x", pady=(0, 3))

            var = tk.StringVar(value="")
            avail = _available_display_values(exclude_var=var)
            if preset_display and preset_display in avail:
                var.set(preset_display)
            elif avail:
                var.set(avail[0])

            menu = ctk.CTkOptionMenu(
                row_frame, variable=var, values=avail if avail else ["\u2014"],
                width=260, height=26, font=("Segoe UI", 10),
                command=lambda v: _on_lang_change(v),
            )
            menu.pack(side="left", padx=(0, 4))

            def remove() -> None:
                row_frame.destroy()
                lang_rows[:] = [r for r in lang_rows if r["frame"] is not row_frame]
                _refresh_all_dropdowns()

            ctk.CTkButton(
                row_frame, text="x", width=26, height=26,
                fg_color=app.RED, hover_color="#EF9A9A",
                text_color="#fff", font=("Segoe UI", 10, "bold"),
                command=remove,
            ).pack(side="left")

            entry = {"frame": row_frame, "var": var, "menu": menu}
            lang_rows.append(entry)
            _refresh_all_dropdowns()

        add_lang_btn = ctk.CTkButton(
            lang_frame, text="+  Add Language",
            width=120, height=26,
            fg_color=app.BG3, hover_color="#555",
            text_color="#fff", font=("Segoe UI", 10),
            command=lambda: _add_lang_row(),
        )

        def _on_engine_change(display_val: str) -> None:
            new_key = _engine_display_to_key(display_val)
            old_key = engine_var.get()

            if new_key == old_key:
                return

            new_dict: dict[str, str] = {}
            if new_key == "deepl":
                new_dict = BATCH_DEEPL_LANGS
            elif new_key == "offline-opus":
                new_dict = BATCH_OPUS_LANGS
            elif new_key == "offline-m2m":
                new_dict = BATCH_M2M_LANGS

            if lang_rows and new_key != "none":
                old_dict = _get_lang_dict()
                incompatible: list[str] = []
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
                        parent=dlg,
                    )
                    if not ok:
                        engine_display_var.set(_engine_key_to_display(old_key))
                        return

            engine_var.set(new_key)

            if new_key == "none":
                for row in lang_rows:
                    row["frame"].destroy()
                lang_rows.clear()
                lang_frame.pack_forget()
                add_lang_btn.pack_forget()
            elif old_key == "none":
                lang_frame.pack(fill="x", pady=(0, 4))
                _add_lang_row()
                add_lang_btn.pack(anchor="w", pady=(0, 4))
            else:
                kept: list[str] = []
                if old_key == "deepl":
                    old_dict_for_check = BATCH_DEEPL_LANGS
                elif old_key == "offline-opus":
                    old_dict_for_check = BATCH_OPUS_LANGS
                elif old_key == "offline-m2m":
                    old_dict_for_check = BATCH_M2M_LANGS
                else:
                    old_dict_for_check = {}

                for row in lang_rows:
                    val = row["var"].get()
                    found_code = None
                    for code, name in old_dict_for_check.items():
                        if f"{name} ({code})" == val:
                            found_code = code
                            break
                    if found_code and found_code in new_dict:
                        kept.append(f"{new_dict[found_code]} ({found_code})")
                    row["frame"].destroy()
                lang_rows.clear()

                if not kept:
                    lang_frame.pack(fill="x", pady=(0, 4))
                    _add_lang_row()
                    add_lang_btn.pack(anchor="w", pady=(0, 4))
                else:
                    lang_frame.pack(fill="x", pady=(0, 4))
                    for display in kept:
                        _add_lang_row(preset_display=display)
                    add_lang_btn.pack(anchor="w", pady=(0, 4))

            _update_controls_visibility()

        # Output directory
        output_frame = ctk.CTkFrame(f, fg_color="transparent")
        ctk.CTkLabel(
            output_frame, text="Output Directory:",
            font=("Segoe UI", 11, "bold"), text_color=app.FG,
        ).pack(anchor="w", pady=(0, 2))

        out_row = ctk.CTkFrame(output_frame, fg_color="transparent")
        out_row.pack(fill="x")
        ctk.CTkLabel(
            out_row, textvariable=output_dir_var,
            font=("Segoe UI", 10), text_color=app.FG2,
            wraplength=320, anchor="w", justify="left",
        ).pack(side="left", fill="x", expand=True)

        def change_output() -> None:
            d = filedialog.askdirectory(
                title="Choose Output Directory",
                initialdir=output_dir_var.get() or None,
            )
            if d:
                output_dir_var.set(d)

        ctk.CTkButton(
            out_row, text="Change...", width=80, height=26,
            fg_color=app.BG3, hover_color="#555",
            text_color="#fff", font=("Segoe UI", 10),
            command=change_output,
        ).pack(side="right")

        # Buttons (bottom)
        btn_frame = ctk.CTkFrame(f, fg_color="transparent")
        btn_frame.pack(fill="x", side="bottom", pady=(2, 0))

        progress = ctk.CTkProgressBar(f, width=420, mode="determinate")
        progress.pack(side="bottom", pady=(0, 6))
        progress.set(0)

        # Status
        status_var = tk.StringVar(value="")
        ctk.CTkLabel(
            f, textvariable=status_var,
            font=("Segoe UI", 10), text_color=app.FG2, wraplength=420,
        ).pack(side="bottom", anchor="w", pady=(6, 2))

        # UI visibility helpers
        def _update_ui_for_selection() -> None:
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

            if selection["is_folder"]:
                subfolder_frame.pack(fill="x", pady=(0, 4))
            else:
                subfolder_frame.pack_forget()
                subfolder_var.set(False)

            if selection["is_audio"] and not selection["is_folder"]:
                live_frame.pack(fill="x", pady=(0, 4))
            else:
                live_frame.pack_forget()
                live_var.set(False)

            _update_controls_visibility()
            start_btn.configure(state="normal")

        def _toggle_live_mode() -> None:
            _update_controls_visibility()

        def _update_controls_visibility() -> None:
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

        # Polling / network
        polling = [False]
        request_result: list[Any] = [None]
        poll_data: list[Any] = [None]
        started = [False]
        live_stop_btn: list[Any] = [None]

        def _send_batch_request(snapshot: dict) -> None:
            try:
                body = {
                    "file_path": snapshot["file_path"],
                    "folder_path": snapshot["folder_path"],
                    "recursive": snapshot["recursive"],
                    "translations": snapshot["translations"],
                    "output_dir": snapshot["output_dir"],
                    "source_lang": snapshot["source_lang"],
                }
                data = json.dumps(body).encode("utf-8")
                req = urllib.request.Request(
                    f"{base_url}/api/transcribe-file/batch",
                    data=data,
                    headers={"Content-Type": "application/json"},
                )
                resp = urllib.request.urlopen(req, timeout=15)
                request_result[0] = json.loads(resp.read())
            except Exception as e:
                request_result[0] = {"error": str(e)}

        def _send_live_request(file_path: str) -> None:
            try:
                data = urllib.parse.urlencode({"file_path": file_path}).encode()
                req = urllib.request.Request(
                    f"{base_url}/api/transcribe-file/live", data=data,
                )
                resp = urllib.request.urlopen(req, timeout=10)
                request_result[0] = json.loads(resp.read())
            except Exception as e:
                request_result[0] = {"error": str(e)}

        def _fetch_progress() -> None:
            try:
                resp = urllib.request.urlopen(
                    f"{base_url}/api/transcribe-file/progress", timeout=3,
                )
                poll_data[0] = json.loads(resp.read())
            except Exception:
                logger.debug("Failed to fetch batch progress", exc_info=True)

        def stop_playback() -> None:
            if live_stop_btn[0]:
                live_stop_btn[0].configure(state="disabled")
            polling[0] = False

            def _send_stop() -> None:
                try:
                    req = urllib.request.Request(
                        f"{base_url}/api/transcribe-file/stop",
                        data=b"", method="POST",
                    )
                    urllib.request.urlopen(req, timeout=5)
                except Exception:
                    logger.debug("Failed to send stop-playback", exc_info=True)
            threading.Thread(target=_send_stop, daemon=True).start()
            status_var.set("Playback stopped, mic resumed")
            progress.set(0)
            dlg.after(1500, dlg.destroy)

        def poll_progress() -> None:
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
                    sb = ctk.CTkButton(
                        btn_frame, text="Stop Playback",
                        fg_color=app.RED, hover_color="#EF9A9A",
                        text_color="#fff",
                        font=("Segoe UI", 11, "bold"),
                        height=34, command=stop_playback,
                    )
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
                            fg_color=app.GREEN, hover_color="#81C784",
                            text_color="#000", font=("Segoe UI", 11, "bold"),
                            height=34,
                            command=lambda: subprocess.Popen(["explorer", out_dir]),
                        )
                        open_btn.pack(side="left", expand=True, fill="x", padx=(0, 4))
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

        def on_start() -> None:
            if not selection["file_path"] and not selection["folder_path"]:
                status_var.set("Select a file or folder first")
                return

            if (selection["is_text"] and not selection["is_folder"]
                    and engine_var.get() == "none" and not live_var.get()):
                status_var.set("Text files require translation -- select an engine and language")
                return

            start_btn.configure(state="disabled")
            is_live = live_var.get()
            status_var.set(
                "Starting live playback..." if is_live else "Starting batch processing..."
            )
            progress.configure(mode="indeterminate")
            progress.start(15)
            polling[0] = True
            started[0] = False
            request_result[0] = None
            poll_data[0] = None

            if is_live:
                threading.Thread(
                    target=_send_live_request,
                    args=(selection["file_path"],),
                    daemon=True,
                ).start()
            else:
                ld = _get_lang_dict()
                trans_list: list[dict[str, str]] = []
                for row in lang_rows:
                    val = row["var"].get()
                    for code, name in ld.items():
                        if f"{name} ({code})" == val:
                            trans_list.append({"lang": code, "mode": engine_var.get()})
                            break
                snapshot = {
                    "file_path": selection["file_path"],
                    "folder_path": selection["folder_path"],
                    "recursive": subfolder_var.get(),
                    "translations": trans_list,
                    "output_dir": output_dir_var.get() or None,
                    "source_lang": source_lang[0],
                }
                threading.Thread(
                    target=_send_batch_request,
                    args=(snapshot,),
                    daemon=True,
                ).start()
            dlg.after(500, poll_progress)

        def on_cancel() -> None:
            polling[0] = False
            dlg.destroy()

        start_btn = ctk.CTkButton(
            btn_frame, text="Start",
            fg_color=app.GREEN, hover_color="#81C784",
            text_color="#000", font=("Segoe UI", 11, "bold"),
            height=34, command=on_start, state="disabled",
        )
        start_btn.pack(side="left", expand=True, fill="x", padx=(0, 4))

        cancel_btn = ctk.CTkButton(
            btn_frame, text="Cancel",
            fg_color=app.BG3, hover_color="#555",
            text_color="#fff", font=("Segoe UI", 11),
            height=34, command=on_cancel,
        )
        cancel_btn.pack(side="right", expand=True, fill="x", padx=(4, 0))

        engine_frame.pack(fill="x", pady=(4, 4))
        output_frame.pack(fill="x", pady=(4, 4))
