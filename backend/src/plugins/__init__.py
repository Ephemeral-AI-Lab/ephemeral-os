"""Plugin exports."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from ephemeralos.plugins.schemas import PluginManifest
    from ephemeralos.plugins.types import LoadedPlugin

__all__ = [
    "LoadedPlugin",
    "PluginManifest",
    "discover_plugin_paths",
    "get_project_plugins_dir",
    "get_user_plugins_dir",
    "install_plugin_from_path",
    "load_plugins",
    "uninstall_plugin",
]


def __getattr__(name: str):
    if name in {"discover_plugin_paths", "get_project_plugins_dir", "get_user_plugins_dir", "load_plugins"}:
        from ephemeralos.plugins.loader import (
            discover_plugin_paths,
            get_project_plugins_dir,
            get_user_plugins_dir,
            load_plugins,
        )

        return {
            "discover_plugin_paths": discover_plugin_paths,
            "get_project_plugins_dir": get_project_plugins_dir,
            "get_user_plugins_dir": get_user_plugins_dir,
            "load_plugins": load_plugins,
        }[name]
    if name in {"install_plugin_from_path", "uninstall_plugin"}:
        from ephemeralos.plugins.installer import install_plugin_from_path, uninstall_plugin

        return {
            "install_plugin_from_path": install_plugin_from_path,
            "uninstall_plugin": uninstall_plugin,
        }[name]
    if name == "PluginManifest":
        from ephemeralos.plugins.schemas import PluginManifest

        return PluginManifest
    if name == "LoadedPlugin":
        from ephemeralos.plugins.types import LoadedPlugin

        return LoadedPlugin
    raise AttributeError(name)
