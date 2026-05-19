"""Entry point for the LinguaTaxi desktop application."""

from __future__ import annotations

import atexit
import sys


def main() -> None:
    """Launch the LinguaTaxi desktop application."""
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("LinguaTaxi.Desktop")

    from linguataxi.launcher.app import LinguaTaxiApp

    app = LinguaTaxiApp()

    def _atexit_cleanup() -> None:
        if app._server_mgr.running and app._server_mgr.server_proc:
            try:
                app._stop_server()
            except Exception:
                try:
                    app._server_mgr.server_proc.kill()
                except Exception:
                    pass

    atexit.register(_atexit_cleanup)
    app.mainloop()


if __name__ == "__main__":
    main()
