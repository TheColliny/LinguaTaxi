"""Settings-related UI methods for the LinguaTaxi launcher."""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    import tkinter as tk
    import customtkinter as ctk

logger = logging.getLogger(__name__)

IS_WIN = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"

# ── Settings directory ──────────────────────────────────────────────

if IS_WIN:
    SETTINGS_DIR = Path(os.environ.get("APPDATA", Path.home())) / "LinguaTaxi"
elif IS_MAC:
    SETTINGS_DIR = Path.home() / "Library" / "Application Support" / "LinguaTaxi"
else:
    SETTINGS_DIR = Path.home() / ".config" / "linguataxi"

SETTINGS_FILE = SETTINGS_DIR / "launcher_settings.json"
DEFAULT_TRANSCRIPTS = Path.home() / "Documents" / "LinguaTaxi Transcripts"

DEFAULT_SETTINGS: dict[str, Any] = {
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


def load_settings() -> dict[str, Any]:
    """Load launcher settings from disk, merging with defaults."""
    try:
        SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
        if SETTINGS_FILE.exists():
            with open(SETTINGS_FILE, "r") as f:
                raw = json.load(f)
            # Migrate old mic_index BEFORE merging defaults
            if "mic_index" in raw and "source_indices" not in raw:
                idx = raw.pop("mic_index")
                raw["source_indices"] = [idx if idx is not None else -1]
            elif "mic_index" in raw:
                raw.pop("mic_index")
            cfg = {**DEFAULT_SETTINGS, **raw}
            return cfg
    except Exception:
        logger.debug("Failed to load settings, using defaults", exc_info=True)
    return dict(DEFAULT_SETTINGS)


def save_settings(cfg: dict[str, Any]) -> None:
    """Persist launcher settings to disk."""
    try:
        SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
        with open(SETTINGS_FILE, "w") as f:
            json.dump(cfg, f, indent=2)
    except Exception:
        logger.debug("Failed to save settings", exc_info=True)


# ── Microphone detection ────────────────────────────────────────────

def list_mics() -> list[tuple[int, str, bool]]:
    """Return list of ``(index, name, is_loopback)`` for available input devices."""
    try:
        import sounddevice as sd

        devices = sd.query_devices()
        mics: list[tuple[int, str, bool]] = []
        for i, d in enumerate(devices):
            if d.get("max_input_channels", 0) > 0:
                name = d["name"]
                is_loopback = any(
                    kw in name.lower()
                    for kw in ["loopback", "stereo mix", "what u hear", "wasapi"]
                )
                mics.append((i, name, is_loopback))
        return mics
    except Exception:
        logger.debug("Failed to enumerate audio devices", exc_info=True)
        return []


class SettingsHelper:
    """Audio-source and settings helpers that operate on the app's widgets.

    Parameters
    ----------
    app:
        Reference to the ``LinguaTaxiApp`` instance for widget access.
    """

    def __init__(self, app: Any) -> None:
        self._app = app

    # ── Audio source rows ────────────────────────────────────────────

    def add_source_row(self, device_index: Optional[int] = None) -> None:
        """Add an audio source row to the settings panel."""
        import customtkinter as ctk

        app = self._app
        from linguataxi.launcher.i18n import _t

        if len(app._source_frames) >= 8:
            return

        row = ctk.CTkFrame(app._sources_container, fg_color="transparent")
        row.pack(fill="x", pady=1)

        num = len(app._source_frames) + 1
        lbl = ctk.CTkLabel(
            row,
            text=_t("launcher.source_label", num=num),
            width=70,
            font=("Segoe UI", 11),
            text_color=app.FG2,
        )
        lbl.pack(side="left")

        import tkinter as tk

        var = tk.StringVar(value=_t("launcher.system_default"))
        combo = ctk.CTkComboBox(
            row,
            variable=var,
            state="readonly",
            font=("Segoe UI", 11),
            width=300,
            fg_color=app.BG,
            border_color=app.BG3,
            button_color=app.BG3,
            button_hover_color=app.ACCENT,
            dropdown_fg_color=app.BG,
            dropdown_hover_color=app.BG3,
            command=lambda v, c=None: None,
        )
        combo.pack(side="left", fill="x", expand=True, padx=(4, 4))
        combo.configure(command=lambda v, c=combo: self.refresh_source_combo(c))

        rm_btn = None
        if len(app._source_frames) > 0:
            rm_btn = ctk.CTkButton(
                row,
                text="X",
                width=30,
                height=28,
                fg_color=app.RED,
                hover_color="#EF9A9A",
                text_color="#fff",
                command=lambda r=row: self.remove_source_row(r),
            )
            rm_btn.pack(side="right")

        app._source_frames.append((row, combo, var))
        self.refresh_source_combo(combo)

        # Select the specified device
        if device_index is not None and device_index != -1:
            mics = list_mics()
            for j, (i, name, _) in enumerate(mics):
                if i == device_index:
                    combo.set(f"[{i}] {name}")
                    break

        self.update_add_button()

    def remove_source_row(self, row: Any) -> None:
        """Remove an audio source row and renumber the remaining ones."""
        import customtkinter as ctk
        from linguataxi.launcher.i18n import _t

        app = self._app
        app._source_frames = [(r, c, v) for r, c, v in app._source_frames if r != row]
        row.destroy()
        for i, (r, c, v) in enumerate(app._source_frames):
            for child in r.winfo_children():
                if isinstance(child, ctk.CTkLabel):
                    child.configure(text=_t("launcher.source_label", num=i + 1))
                    break
        self.update_add_button()

    def update_add_button(self) -> None:
        """Show/hide the Add Source button based on current count."""
        app = self._app
        if len(app._source_frames) >= 8:
            app._add_source_btn.pack_forget()
        else:
            try:
                app._add_source_btn.pack(fill="x", pady=(0, 8))
            except Exception:
                logger.debug("Could not re-pack add-source button", exc_info=True)

    def refresh_source_combo(self, combo: Any) -> None:
        """Refresh a source dropdown with a grouped device list."""
        from linguataxi.launcher.i18n import _t

        app = self._app
        mics = list_mics()
        app._mic_devices = mics
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

    def get_source_indices(self) -> list[int]:
        """Get device indices for all configured audio sources."""
        from linguataxi.launcher.i18n import _t

        app = self._app
        try:
            app._mic_devices = list_mics()
        except Exception:
            logger.debug("Failed to refresh mic list", exc_info=True)
        indices: list[int] = []
        for _, combo, var in app._source_frames:
            text = var.get()
            if text == _t("launcher.system_default") or not text:
                indices.append(-1)
            else:
                matched = False
                for i, name, _ in app._mic_devices:
                    if f"[{i}] {name}" == text:
                        indices.append(i)
                        matched = True
                        break
                if not matched:
                    indices.append(-1)
        return indices

    # ── Transcript directory ─────────────────────────────────────────

    def browse_tdir(self) -> None:
        """Open a directory chooser for the transcript location."""
        from tkinter import filedialog
        from linguataxi.launcher.i18n import _t

        app = self._app
        current = app.tdir_var.get().strip()
        d = filedialog.askdirectory(
            initialdir=current if Path(current).exists() else str(Path.home()),
            title=_t("launcher.dialog_select_transcript_location"),
        )
        if d:
            app.tdir_var.set(d)
            self.save_current_settings()
            app._log_system(_t("launcher.log_transcripts_directory", path=d))

    def save_current_settings(self) -> None:
        """Gather current widget values and persist them."""
        app = self._app
        app.settings["transcripts_dir"] = app.tdir_var.get().strip()
        app.settings["source_indices"] = self.get_source_indices()
        app.settings["backend"] = app._backend_from_label.get(
            app.backend_var.get(), app.backend_var.get()
        )
        app.settings["window_geometry"] = app.geometry()
        app.settings["check_for_updates"] = app.update_check_var.get()
        app.settings["language"] = app._current_lang
        if hasattr(app, "close_tray_var"):
            app.settings["close_to_tray"] = app.close_tray_var.get()
        save_settings(app.settings)
