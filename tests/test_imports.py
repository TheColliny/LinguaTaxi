"""Validate that all LinguaTaxi modules import cleanly after refactor.

Tests the full import chain including the _ServerModule proxy in server.py,
all subpackages, and the late `import server as _srv` pattern used throughout.
"""

from __future__ import annotations

import importlib
import sys
import types

import pytest


# ── Module import tests ──────────────────────────────────────────────

ALL_MODULES = [
    "linguataxi",
    "linguataxi.constants",
    "linguataxi.settings",
    "linguataxi.server",
    "linguataxi.server.app",
    "linguataxi.server.audio",
    "linguataxi.server.main",
    "linguataxi.server.transcripts",
    "linguataxi.server.translation",
    "linguataxi.server.websocket",
    "linguataxi.server.backends",
    "linguataxi.server.backends.whisper",
    "linguataxi.server.backends.vosk",
    "linguataxi.server.routes.display",
    "linguataxi.server.routes.operator",
    "linguataxi.server.routes.dictation",
    "linguataxi.server.routes.transcribe",
    "linguataxi.launcher",
    "linguataxi.launcher.app",
    "linguataxi.launcher.main",
    "linguataxi.launcher.server_manager",
    "linguataxi.launcher.settings_panel",
    "linguataxi.launcher.batch_transcriber",
    "linguataxi.launcher.model_download",
    "linguataxi.launcher.i18n",
    "linguataxi.dictation",
    "linguataxi.dictation.main",
    "linguataxi.models",
    "linguataxi.models.manager",
    "linguataxi.models.downloader",
    "linguataxi.models.offline_translate",
    "linguataxi.plugins",
    "linguataxi.plugins.loader",
    "linguataxi.plugins.registry",
]

# Modules that need a display (tkinter) — skip on headless
DISPLAY_MODULES = {
    "linguataxi.launcher.app",
    "linguataxi.launcher.main",
    "linguataxi.launcher.settings_panel",
    "linguataxi.launcher.batch_transcriber",
    "linguataxi.launcher.model_download",
    "linguataxi.launcher.tray_manager",
    "linguataxi.dictation.tray",
    "linguataxi.dictation.hotkeys",
    "linguataxi.dictation.injection",
    "linguataxi.dictation.main",
}


@pytest.mark.parametrize("module_name", ALL_MODULES)
def test_module_imports(module_name: str) -> None:
    """Each module in the linguataxi package imports without error."""
    if module_name in DISPLAY_MODULES:
        pytest.skip("requires display / tkinter")
    try:
        importlib.import_module(module_name)
    except ModuleNotFoundError as exc:
        if "tkinter" in str(exc) or "pystray" in str(exc):
            pytest.skip(f"optional display dependency: {exc}")
        raise


# ── server.py thin-shell proxy tests ─────────────────────────────────

class TestServerProxy:
    """Verify the _ServerModule proxy in server.py works correctly."""

    @pytest.fixture(autouse=True)
    def _import_server(self) -> None:
        import server  # noqa: F811
        self.server = server

    def test_server_is_proxy_module(self) -> None:
        assert isinstance(self.server, types.ModuleType)
        assert type(self.server).__name__ == "_ServerModule"

    def test_mutable_app_attrs_readable(self) -> None:
        attrs = [
            "stt_backend", "current_mic_index", "silence_threshold",
            "translation_paused", "captioning_paused", "dictation_active",
            "_dictation_loop", "save_transcripts",
        ]
        for attr in attrs:
            getattr(self.server, attr)  # should not raise

    def test_mutable_app_attrs_writable(self) -> None:
        import linguataxi.server.app as app_mod

        original = app_mod.captioning_paused
        try:
            self.server.captioning_paused = not original
            assert app_mod.captioning_paused == (not original)
            assert self.server.captioning_paused == (not original)
        finally:
            app_mod.captioning_paused = original

    def test_identity_stable_objects(self) -> None:
        import linguataxi.server.app as app_mod

        assert self.server.config is app_mod.config
        assert self.server.shutdown_event is app_mod.shutdown_event
        assert self.server.display_app is app_mod.display_app
        assert self.server.operator_app is app_mod.operator_app
        assert self.server.extended_app is app_mod.extended_app
        assert self.server.dictation_app is app_mod.dictation_app

    def test_models_dir_proxy(self) -> None:
        import linguataxi.settings as settings_mod

        val = self.server.MODELS_DIR
        assert val == settings_mod.MODELS_DIR

    def test_unknown_attr_raises(self) -> None:
        with pytest.raises(AttributeError):
            _ = self.server.nonexistent_attribute_xyz

    def test_exported_functions(self) -> None:
        assert callable(self.server.main)
        assert callable(self.server.detect_gpu)

    def test_proxy_registered_as_server(self) -> None:
        """sys.modules['server'] must always be the proxy, even when run as __main__."""
        mod = sys.modules.get("server")
        assert mod is not None, "sys.modules['server'] not set"
        assert type(mod).__name__ == "_ServerModule"

    def test_proxy_getattr_all_mutable_attrs(self) -> None:
        """Every attr in _MUTABLE_APP_ATTRS must be accessible via __getattr__."""
        import linguataxi.server.app as app_mod

        for attr in (
            "stt_backend", "current_mic_index", "silence_threshold",
            "translation_paused", "captioning_paused", "dictation_active",
            "_dictation_loop", "save_transcripts",
        ):
            proxy_val = getattr(self.server, attr)
            canonical_val = getattr(app_mod, attr)
            assert proxy_val == canonical_val, (
                f"Proxy mismatch for {attr}: {proxy_val!r} != {canonical_val!r}"
            )


# ── Late import validation ───────────────────────────────────────────

def test_late_import_server_resolves() -> None:
    """Verify that `import server as _srv` inside function bodies works."""
    import server  # noqa: F811 — ensure it's loaded first

    from linguataxi.server.websocket import _bc
    from linguataxi.server.transcripts import _broadcast_final

    assert callable(_bc)
    assert callable(_broadcast_final)


_ALL_SRV_ATTRS = [
    "_detect_segment_lang",
    "_dictation_loop",
    "_get_registry",
    "_save_speaker_config",
    "_shutdown_and_exit",
    "_voice_id_try_enroll",
    "_voice_id_try_identify",
    "BASE_DIR",
    "captioning_paused",
    "config",
    "current_mic_index",
    "detect_gpu",
    "dictation_active",
    "mic_restart_event",
    "MODELS_DIR",
    "plugin_dispatcher",
    "save_transcripts",
    "shutdown_event",
    "silence_threshold",
    "stt_backend",
    "translation_paused",
]


@pytest.mark.parametrize("attr", _ALL_SRV_ATTRS)
def test_server_proxy_exposes_attr(attr: str) -> None:
    """Every _srv.X used in the codebase must be accessible on the proxy.

    These attribute names are scraped from all `_srv.X` usages across
    linguataxi/server/.  If this test fails, a runtime AttributeError
    will crash the corresponding server thread.
    """
    import server
    getattr(server, attr)  # must not raise


def test_all_route_modules_register() -> None:
    """Every route module has a register_*_routes function."""
    from linguataxi.server.routes import display, operator, dictation

    assert hasattr(display, "register_display_routes")
    assert hasattr(operator, "register_operator_routes")
    assert hasattr(dictation, "register_dictation_routes")


# ── Production proxy scenario (server.py as __main__) ────────────────

def test_proxy_works_when_run_as_main() -> None:
    """Simulate production: server.py runs as __main__, then import server works.

    This is the exact scenario that caused the captioning_paused AttributeError:
    python server.py sets __name__='__main__', then _buffer_audio_loop does
    `import server as _srv` and accesses _srv.captioning_paused.
    """
    import subprocess
    import textwrap

    script = textwrap.dedent("""\
        import sys, runpy

        # Run server.py as __main__ (like `python server.py` would)
        # but intercept before main() is called by patching it out
        import linguataxi.server.main
        linguataxi.server.main._original_main = linguataxi.server.main.main
        linguataxi.server.main.main = lambda: None  # no-op so we don't start uvicorn

        # Execute server.py as __main__
        runpy.run_path("server.py", run_name="__main__")

        # Now simulate what _buffer_audio_loop does
        import server as _srv

        # Verify proxy type
        assert type(_srv).__name__ == "_ServerModule", (
            f"Expected _ServerModule, got {type(_srv).__name__}"
        )

        # Verify the exact attribute that was failing
        _ = _srv.captioning_paused
        _ = _srv.dictation_active
        _ = _srv.translation_paused
        _ = _srv.stt_backend

        print("PROXY_OK")
    """)

    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert "PROXY_OK" in result.stdout, (
        f"Proxy test failed.\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )
