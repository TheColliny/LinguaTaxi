"""System tray icon management for the LinguaTaxi launcher."""

from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class TrayManager:
    """System tray icon lifecycle manager.

    Parameters
    ----------
    app:
        Reference to the ``LinguaTaxiApp`` instance.
    app_dir:
        Root directory containing the ``assets/`` folder.
    settings_dir:
        Directory for writing tray error logs.
    """

    def __init__(self, app: Any, app_dir: Path, settings_dir: Path) -> None:
        self._app = app
        self._app_dir = app_dir
        self._settings_dir = settings_dir
        self._tray_icon: Any = None
        self._tray_running: bool = False

    def setup(self) -> None:
        """Create the system tray icon and start it running (hidden initially)."""
        try:
            import pystray
            from PIL import Image
        except ImportError:
            logger.debug("pystray or PIL not available; skipping tray setup")
            return

        icon_path = self._app_dir / "assets" / "linguataxi.png"
        if icon_path.exists():
            image = Image.open(str(icon_path)).resize((64, 64))
        else:
            image = Image.new("RGBA", (64, 64), (79, 195, 247, 255))

        app = self._app

        def _show_window(icon: Any, item: Any) -> None:
            app.after(0, self.restore_from_tray)

        def _start_srv(icon: Any, item: Any) -> None:
            app.after(0, app._start_server)

        def _stop_srv(icon: Any, item: Any) -> None:
            app.after(0, app._stop_server)

        def _open_op(icon: Any, item: Any) -> None:
            app.after(0, app._open_operator)

        def _open_disp(icon: Any, item: Any) -> None:
            app.after(0, app._open_main)

        def _open_dict(icon: Any, item: Any) -> None:
            app.after(0, app._open_dictation)

        def _quit(icon: Any, item: Any) -> None:
            app.after(0, self.quit_from_tray)

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

        def _run_tray_bg() -> None:
            self._tray_running = True
            try:
                self._tray_icon.run(setup=lambda icon: None)
            except Exception as e:
                try:
                    with open(str(self._settings_dir / "tray_error.log"), "w") as fh:
                        import traceback
                        fh.write(f"Tray icon failed: {e}\n")
                        traceback.print_exc(file=fh)
                except Exception:
                    logger.debug("Failed to write tray error log", exc_info=True)
            self._tray_running = False

        threading.Thread(target=_run_tray_bg, daemon=True).start()

    def minimize_to_tray(self) -> bool:
        """Hide window and show tray icon.  Returns ``True`` on success."""
        if not self._tray_icon or not self._tray_running:
            return False
        self._app.withdraw()
        self._tray_icon.visible = True
        self._tray_icon.notify("LinguaTaxi is still running", "LinguaTaxi")
        return True

    def restore_from_tray(self) -> None:
        """Show window and hide tray icon."""
        if self._tray_icon:
            self._tray_icon.visible = False
        self._app.deiconify()
        self._app.lift()
        self._app.focus_force()

    def quit_from_tray(self) -> None:
        """Full quit from tray: stop server, destroy window, exit."""
        app = self._app
        app._closing = True

        def _force_exit() -> None:
            time.sleep(20)
            os._exit(1)
        threading.Thread(target=_force_exit, daemon=True).start()

        if app._server_mgr.running:
            app._stop_server()
        if self._tray_icon:
            try:
                self._tray_icon.stop()
            except Exception:
                logger.debug("Tray icon stop failed", exc_info=True)
        try:
            app.destroy()
        except Exception:
            logger.debug("App destroy failed during tray quit", exc_info=True)
        os._exit(0)
