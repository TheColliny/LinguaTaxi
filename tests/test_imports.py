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


# ── Late import validation ───────────────────────────────────────────

def test_late_import_server_resolves() -> None:
    """Verify that `import server as _srv` inside function bodies works."""
    import server  # noqa: F811 — ensure it's loaded first

    from linguataxi.server.websocket import _bc
    from linguataxi.server.transcripts import _broadcast_final

    assert callable(_bc)
    assert callable(_broadcast_final)


def test_all_route_modules_register() -> None:
    """Every route module has a register_*_routes function."""
    from linguataxi.server.routes import display, operator, dictation

    assert hasattr(display, "register_display_routes")
    assert hasattr(operator, "register_operator_routes")
    assert hasattr(dictation, "register_dictation_routes")
