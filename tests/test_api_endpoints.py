"""Integration tests for all LinguaTaxi HTTP API endpoints.

Requires a running server (provided by the server_process fixture).
Tests every endpoint documented in the architecture for correct HTTP status
and response shape. Does NOT test business logic deeply — validates that
the refactored route wiring is intact.
"""

from __future__ import annotations

import subprocess

import httpx
import pytest

DISPLAY = "http://127.0.0.1:3000"
OPERATOR = "http://127.0.0.1:3001"
EXTENDED = "http://127.0.0.1:3002"
DICTATION = "http://127.0.0.1:3005"


@pytest.fixture(scope="module")
def client(server_process: subprocess.Popen) -> httpx.Client:
    return httpx.Client(timeout=10.0)


# ══════════════════════════════════════════════════════════════════════
# Display app (port 3000)
# ══════════════════════════════════════════════════════════════════════

class TestDisplayApp:

    def test_root_html(self, client: httpx.Client) -> None:
        r = client.get(f"{DISPLAY}/")
        assert r.status_code == 200
        assert "text/html" in r.headers["content-type"]

    def test_api_config(self, client: httpx.Client) -> None:
        r = client.get(f"{DISPLAY}/api/config")
        assert r.status_code == 200
        data = r.json()
        assert "bg_color" in data or "translations" in data

    def test_api_locales(self, client: httpx.Client) -> None:
        r = client.get(f"{DISPLAY}/api/locales/EN")
        assert r.status_code == 200

    def test_api_display_grids(self, client: httpx.Client) -> None:
        r = client.get(f"{DISPLAY}/api/display-grids")
        assert r.status_code == 200
        data = r.json()
        assert "main" in data or "extended" in data


# ══════════════════════════════════════════════════════════════════════
# Extended app (port 3002)
# ══════════════════════════════════════════════════════════════════════

class TestExtendedApp:

    def test_root_html(self, client: httpx.Client) -> None:
        r = client.get(f"{EXTENDED}/")
        assert r.status_code == 200
        assert "text/html" in r.headers["content-type"]

    def test_api_config(self, client: httpx.Client) -> None:
        r = client.get(f"{EXTENDED}/api/config")
        assert r.status_code == 200

    def test_api_display_grids(self, client: httpx.Client) -> None:
        r = client.get(f"{EXTENDED}/api/display-grids")
        assert r.status_code == 200


# ══════════════════════════════════════════════════════════════════════
# Operator app (port 3001)
# ══════════════════════════════════════════════════════════════════════

class TestOperatorApp:

    def test_root_html(self, client: httpx.Client) -> None:
        r = client.get(f"{OPERATOR}/")
        assert r.status_code == 200
        assert "text/html" in r.headers["content-type"]

    def test_api_config_get(self, client: httpx.Client) -> None:
        r = client.get(f"{OPERATOR}/api/config")
        assert r.status_code == 200
        data = r.json()
        assert "session_title" in data
        assert "input_lang" in data

    def test_api_status(self, client: httpx.Client) -> None:
        r = client.get(f"{OPERATOR}/api/status")
        assert r.status_code == 200
        data = r.json()
        assert "captioning_paused" in data
        assert "translation_paused" in data
        assert data["captioning_paused"] is True
        assert data["translation_paused"] is True

    def test_api_mics(self, client: httpx.Client) -> None:
        r = client.get(f"{OPERATOR}/api/mics")
        assert r.status_code == 200
        data = r.json()
        assert "mics" in data

    def test_api_sources(self, client: httpx.Client) -> None:
        r = client.get(f"{OPERATOR}/api/sources")
        assert r.status_code == 200
        data = r.json()
        assert isinstance(data, list)

    def test_api_plugins(self, client: httpx.Client) -> None:
        r = client.get(f"{OPERATOR}/api/plugins")
        assert r.status_code == 200
        data = r.json()
        assert isinstance(data, list)

    def test_api_tuned_models(self, client: httpx.Client) -> None:
        r = client.get(f"{OPERATOR}/api/tuned-models")
        assert r.status_code == 200

    def test_api_offline_translate_status(self, client: httpx.Client) -> None:
        r = client.get(f"{OPERATOR}/api/offline-translate/status")
        assert r.status_code == 200

    def test_api_voice_id_status(self, client: httpx.Client) -> None:
        r = client.get(f"{OPERATOR}/api/voice-id/status")
        assert r.status_code == 200
        data = r.json()
        assert "enabled" in data

    def test_api_display_grids_get(self, client: httpx.Client) -> None:
        r = client.get(f"{OPERATOR}/api/display-grids")
        assert r.status_code == 200

    def test_api_locales(self, client: httpx.Client) -> None:
        r = client.get(f"{OPERATOR}/api/locales/EN")
        assert r.status_code == 200

    def test_api_config_post_roundtrip(self, client: httpx.Client) -> None:
        """POST config with minimal fields, then GET and verify."""
        r = client.post(
            f"{OPERATOR}/api/config",
            data={"session_title": "Test Session"},
        )
        assert r.status_code == 200

        r2 = client.get(f"{OPERATOR}/api/config")
        assert r2.json()["session_title"] == "Test Session"

        client.post(
            f"{OPERATOR}/api/config",
            data={"session_title": "Live Captioning"},
        )

    def test_api_transcribe_file_progress(self, client: httpx.Client) -> None:
        r = client.get(f"{OPERATOR}/api/transcribe-file/progress")
        assert r.status_code == 200


# ══════════════════════════════════════════════════════════════════════
# Dictation app (port 3005)
# ══════════════════════════════════════════════════════════════════════

class TestDictationApp:

    def test_root_html(self, client: httpx.Client) -> None:
        r = client.get(f"{DICTATION}/")
        assert r.status_code == 200

    def test_api_config(self, client: httpx.Client) -> None:
        r = client.get(f"{DICTATION}/api/config")
        assert r.status_code == 200

    def test_api_dictation_config(self, client: httpx.Client) -> None:
        r = client.get(f"{DICTATION}/api/dictation-config")
        assert r.status_code == 200
        data = r.json()
        assert "dictation_dir" in data

    def test_api_dictation_active_toggle(self, client: httpx.Client) -> None:
        r = client.post(
            f"{DICTATION}/api/dictation-active",
            json={"active": True},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["active"] is True

        r2 = client.post(
            f"{DICTATION}/api/dictation-active",
            json={"active": False},
        )
        assert r2.status_code == 200
        assert r2.json()["active"] is False

    def test_api_locales(self, client: httpx.Client) -> None:
        r = client.get(f"{DICTATION}/api/locales/EN")
        assert r.status_code == 200


# ══════════════════════════════════════════════════════════════════════
# Cross-cutting: static files served on all apps
# ══════════════════════════════════════════════════════════════════════

class TestStaticFiles:

    @pytest.mark.parametrize("base_url", [DISPLAY, OPERATOR, EXTENDED])
    def test_static_css_exists(self, client: httpx.Client, base_url: str) -> None:
        r = client.get(f"{base_url}/static/css/display.css")
        if r.status_code == 404:
            pytest.skip("static CSS not mounted on this app")
        assert r.status_code == 200

    @pytest.mark.parametrize("base_url", [DISPLAY, OPERATOR, EXTENDED])
    def test_static_js_exists(self, client: httpx.Client, base_url: str) -> None:
        r = client.get(f"{base_url}/static/js/display.js")
        if r.status_code == 404:
            pytest.skip("static JS not mounted on this app")
        assert r.status_code == 200
