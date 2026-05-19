"""Dictation tray application entry point."""

from __future__ import annotations

import atexit
import logging
import sys

from linguataxi.constants import IS_WIN
from linguataxi.settings import SETTINGS_DIR


def main() -> None:
    """Start the dictation tray application."""
    SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s [%(levelname)s] %(message)s",
        filename=str(SETTINGS_DIR / "tray_debug.log"),
        filemode="w",
    )
    _log = logging.getLogger("tray")

    from linguataxi.dictation.tray import run_tray, stop_server

    atexit.register(stop_server)

    if IS_WIN:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "LinguaTaxi.Dictation",
        )
        _log.info("AppUserModelID set to LinguaTaxi.Dictation")

    try:
        run_tray()
    except Exception:
        _log.exception("run_tray crashed")
        raise
    finally:
        stop_server()
