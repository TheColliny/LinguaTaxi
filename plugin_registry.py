import json
import logging
import shutil
import tempfile
import threading
import zipfile
from pathlib import Path

import requests

log = logging.getLogger("livecaption")


def _parse_version(v: str) -> tuple:
    try:
        return tuple(int(x) for x in v.split("."))
    except (ValueError, AttributeError):
        return (0, 0, 0)


class PluginRegistry:
    def __init__(
        self,
        plugins_dir: Path,
        github_repo: str = "TheColliny/linguataxi-plugins",
        app_version: str = "1.0.0",
        edition: str = "Dev",
    ):
        self.plugins_dir = Path(plugins_dir)
        self.github_repo = github_repo
        self.app_version = app_version
        self.edition = edition
        self._cache_path = self.plugins_dir / ".registry_cache.json"
        self._lock = threading.Lock()

    def fetch_registry(self) -> list[dict]:
        url = f"https://raw.githubusercontent.com/{self.github_repo}/main/registry.json"
        try:
            resp = requests.get(url, timeout=10)
            resp.raise_for_status()
            registry = resp.json()
        except Exception as e:
            log.warning("Failed to fetch registry from GitHub: %s", e)
            return self._read_cache()

        with self._lock:
            self._cache_path.parent.mkdir(parents=True, exist_ok=True)
            self._cache_path.write_text(json.dumps(registry, indent=2), encoding="utf-8")

        return registry

    def _read_cache(self) -> list[dict]:
        with self._lock:
            if self._cache_path.exists():
                try:
                    return json.loads(self._cache_path.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    pass
        return []

    def get_installed(self) -> dict[str, str]:
        installed = {}
        if not self.plugins_dir.exists():
            return installed
        for manifest_path in self.plugins_dir.glob("*/manifest.json"):
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                plugin_id = manifest.get("id", manifest_path.parent.name)
                version = manifest.get("version", "0.0.0")
                installed[plugin_id] = version
            except (json.JSONDecodeError, OSError):
                continue
        return installed

    def check_updates(self) -> list[dict]:
        registry = self._read_cache()
        if not registry:
            registry = self.fetch_registry()
        installed = self.get_installed()
        updates = []
        for entry in registry:
            plugin_id = entry.get("id")
            if plugin_id in installed:
                if _parse_version(entry["version"]) > _parse_version(installed[plugin_id]):
                    updates.append(entry)
        return updates

    def install_plugin(self, plugin_id: str) -> Path:
        registry = self._read_cache()
        if not registry:
            registry = self.fetch_registry()

        entry = next((e for e in registry if e["id"] == plugin_id), None)
        if entry is None:
            raise ValueError(f"Plugin '{plugin_id}' not found in registry")

        asset_filename = entry["asset_filename"]
        download_url = self._resolve_asset_url(asset_filename)
        if download_url is None:
            raise RuntimeError(f"Could not find release asset '{asset_filename}'")

        dest = self.plugins_dir / plugin_id
        dest.mkdir(parents=True, exist_ok=True)

        resp = requests.get(download_url, timeout=60, stream=True)
        resp.raise_for_status()

        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
            tmp_path = Path(tmp.name)
            for chunk in resp.iter_content(chunk_size=8192):
                tmp.write(chunk)

        try:
            with zipfile.ZipFile(tmp_path, "r") as zf:
                zf.extractall(dest)
        finally:
            tmp_path.unlink(missing_ok=True)

        log.info("Installed plugin '%s' v%s to %s", plugin_id, entry["version"], dest)
        return dest

    def update_plugin(self, plugin_id: str) -> Path:
        target = self.plugins_dir / plugin_id
        if target.exists():
            shutil.rmtree(target)
        return self.install_plugin(plugin_id)

    def uninstall_plugin(self, plugin_id: str) -> bool:
        target = self.plugins_dir / plugin_id
        if not target.exists():
            return False
        shutil.rmtree(target)
        log.info("Uninstalled plugin '%s'", plugin_id)
        return True

    def is_cached(self) -> bool:
        with self._lock:
            return self._cache_path.exists()

    def find_plugin(self, plugin_id: str) -> dict | None:
        registry = self._read_cache()
        if not registry:
            registry = self.fetch_registry()
        return next((e for e in registry if e["id"] == plugin_id), None)

    def is_installed(self, plugin_id: str) -> bool:
        return plugin_id in self.get_installed()

    def is_compatible(self, entry: dict) -> bool:
        min_ver = entry.get("min_app_version")
        if min_ver and _parse_version(self.app_version) < _parse_version(min_ver):
            return False

        compatibility = entry.get("compatibility", "cpu+gpu")
        if compatibility == "cpu+gpu":
            return True
        if compatibility == "gpu" and self.edition.lower() in ("gpu", "dev"):
            return True
        if compatibility == "cpu" and self.edition.lower() in ("cpu", "dev"):
            return True

        return False

    def _resolve_asset_url(self, asset_filename: str) -> str | None:
        url = f"https://api.github.com/repos/{self.github_repo}/releases"
        try:
            resp = requests.get(url, timeout=10)
            resp.raise_for_status()
            releases = resp.json()
        except Exception as e:
            log.error("Failed to fetch releases: %s", e)
            return None

        for release in releases:
            for asset in release.get("assets", []):
                if asset["name"] == asset_filename:
                    return asset["browser_download_url"]
        return None


def create_registry(plugins_dir: Path, app_version: str, edition: str) -> PluginRegistry:
    return PluginRegistry(plugins_dir=plugins_dir, app_version=app_version, edition=edition)
