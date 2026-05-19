"""Plugin Registry -- marketplace for discovering, installing, and updating plugins.

Fetches the plugin registry from GitHub, manages local cache, and handles
plugin installation/update/uninstall via zip archives from GitHub releases.
"""

from __future__ import annotations

import json
import logging
import shutil
import tempfile
import threading
import zipfile
from pathlib import Path
from typing import Any

import requests

log: logging.Logger = logging.getLogger("livecaption")


def _parse_version(v: str) -> tuple[int, ...]:
    """Parse a version string into a comparable tuple.

    Args:
        v: Version string (e.g. ``"1.2.3"``).

    Returns:
        Tuple of integers for comparison.
    """
    try:
        return tuple(int(x) for x in v.split("."))
    except (ValueError, AttributeError):
        return (0, 0, 0)


class PluginRegistry:
    """Manages the plugin marketplace: discovery, installation, updates.

    Fetches plugin metadata from a GitHub repository's ``registry.json``,
    caches it locally, and provides install/update/uninstall operations.
    """

    def __init__(
        self,
        plugins_dir: Path,
        github_repo: str = "TheColliny/linguataxi-plugins",
        app_version: str = "1.0.0",
        edition: str = "Dev",
    ) -> None:
        """Initialize the plugin registry.

        Args:
            plugins_dir: Path to the ``plugins/`` directory.
            github_repo: GitHub repository for the plugin registry.
            app_version: Current application version for compatibility checks.
            edition: Application edition (``"CPU"``, ``"GPU"``, ``"Dev"``).
        """
        self.plugins_dir: Path = Path(plugins_dir)
        self.github_repo: str = github_repo
        self.app_version: str = app_version
        self.edition: str = edition
        self._cache_path: Path = self.plugins_dir / ".registry_cache.json"
        self._lock: threading.Lock = threading.Lock()

    def fetch_registry(self) -> list[dict[str, Any]]:
        """Fetch the plugin registry from GitHub, with local cache fallback.

        Returns:
            List of plugin entry dicts.
        """
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

    def _read_cache(self) -> list[dict[str, Any]]:
        """Read the cached registry from disk.

        Returns:
            Cached registry entries, or empty list.
        """
        with self._lock:
            if self._cache_path.exists():
                try:
                    return json.loads(self._cache_path.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    pass
        return []

    def get_installed(self) -> dict[str, str]:
        """Get all installed plugins and their versions.

        Returns:
            Dict mapping plugin IDs to version strings.
        """
        installed: dict[str, str] = {}
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

    def check_updates(self) -> list[dict[str, Any]]:
        """Check for available plugin updates.

        Returns:
            List of registry entries that have newer versions than installed.
        """
        registry = self._read_cache()
        if not registry:
            registry = self.fetch_registry()
        installed = self.get_installed()
        updates: list[dict[str, Any]] = []
        for entry in registry:
            plugin_id = entry.get("id")
            if plugin_id in installed:
                if _parse_version(entry["version"]) > _parse_version(installed[plugin_id]):
                    updates.append(entry)
        return updates

    def install_plugin(self, plugin_id: str) -> Path:
        """Install a plugin from the registry.

        Args:
            plugin_id: Plugin identifier to install.

        Returns:
            Path to the installed plugin directory.

        Raises:
            ValueError: If the plugin is not found in the registry.
            RuntimeError: If the release asset cannot be found.
        """
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
        """Update an installed plugin by reinstalling from the registry.

        Args:
            plugin_id: Plugin identifier to update.

        Returns:
            Path to the updated plugin directory.
        """
        target = self.plugins_dir / plugin_id
        if target.exists():
            shutil.rmtree(target)
        return self.install_plugin(plugin_id)

    def uninstall_plugin(self, plugin_id: str) -> bool:
        """Uninstall a plugin by removing its directory.

        Args:
            plugin_id: Plugin identifier to uninstall.

        Returns:
            True if the plugin was found and removed.
        """
        target = self.plugins_dir / plugin_id
        if not target.exists():
            return False
        shutil.rmtree(target)
        log.info("Uninstalled plugin '%s'", plugin_id)
        return True

    def is_cached(self) -> bool:
        """Check if the registry cache exists on disk.

        Returns:
            True if the cache file exists.
        """
        with self._lock:
            return self._cache_path.exists()

    def find_plugin(self, plugin_id: str) -> dict[str, Any] | None:
        """Find a plugin entry in the registry by ID.

        Args:
            plugin_id: Plugin identifier.

        Returns:
            Registry entry dict, or ``None`` if not found.
        """
        registry = self._read_cache()
        if not registry:
            registry = self.fetch_registry()
        return next((e for e in registry if e["id"] == plugin_id), None)

    def is_installed(self, plugin_id: str) -> bool:
        """Check if a plugin is installed locally.

        Args:
            plugin_id: Plugin identifier.

        Returns:
            True if the plugin directory contains a manifest.
        """
        return plugin_id in self.get_installed()

    def is_compatible(self, entry: dict[str, Any]) -> bool:
        """Check if a plugin is compatible with this app version and edition.

        Args:
            entry: Registry entry dict.

        Returns:
            True if the plugin is compatible.
        """
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
        """Resolve a release asset filename to its download URL.

        Args:
            asset_filename: Name of the release asset file.

        Returns:
            Download URL string, or ``None`` if not found.
        """
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


def create_registry(
    plugins_dir: Path,
    app_version: str,
    edition: str,
) -> PluginRegistry:
    """Create a :class:`PluginRegistry` instance with default settings.

    Args:
        plugins_dir: Path to the plugins directory.
        app_version: Current application version.
        edition: Application edition.

    Returns:
        Configured :class:`PluginRegistry` instance.
    """
    return PluginRegistry(plugins_dir=plugins_dir, app_version=app_version, edition=edition)
