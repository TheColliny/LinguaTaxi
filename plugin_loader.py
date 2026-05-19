"""Plugin Loader entry point — delegates to linguataxi.plugins.loader."""

from linguataxi.plugins.loader import (  # noqa: F401
    PluginManifest,
    LoadedPlugin,
    PluginDispatcher,
)
