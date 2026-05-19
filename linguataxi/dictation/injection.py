"""Text injection into focused application via clipboard paste."""

from __future__ import annotations

import logging
from typing import Any

from linguataxi.constants import IS_WIN

_log: logging.Logger = logging.getLogger("tray")

# Lazy-initialised pynput keyboard controller
_kb_controller: Any | None = None


def _inject_text(text: str) -> None:
    """Inject *text* into the currently focused application.

    On Windows the text is placed on the clipboard and pasted via Ctrl+V.
    On other platforms each word is typed via the pynput keyboard controller.
    """
    global _kb_controller

    _log.info("_inject_text called: '%s' | len=%d", text[:100], len(text))
    if not text.strip():
        return

    inject: str = text + " "
    try:
        if IS_WIN:
            import ctypes

            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32

            CF_UNICODETEXT: int = 13
            GMEM_MOVEABLE: int = 0x0002

            data: bytes = inject.encode("utf-16-le") + b"\x00\x00"
            h = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
            p = kernel32.GlobalLock(h)
            ctypes.memmove(p, data, len(data))
            kernel32.GlobalUnlock(h)

            user32.OpenClipboard(0)
            user32.EmptyClipboard()
            user32.SetClipboardData(CF_UNICODETEXT, h)
            user32.CloseClipboard()

            if _kb_controller is None:
                from pynput.keyboard import Controller
                _kb_controller = Controller()
            from pynput.keyboard import Key

            _kb_controller.press(Key.ctrl)
            _kb_controller.press("v")
            _kb_controller.release("v")
            _kb_controller.release(Key.ctrl)
            _log.info("_inject_text completed OK (clipboard paste)")
        else:
            if _kb_controller is None:
                from pynput.keyboard import Controller
                _kb_controller = Controller()
            words: list[str] = inject.split()
            for i, word in enumerate(words):
                if i > 0:
                    _kb_controller.type(" ")
                _kb_controller.type(word)
            _log.info("_inject_text completed OK (keyboard type)")
    except Exception as exc:
        _log.error("_inject_text FAILED: %s", exc, exc_info=True)
