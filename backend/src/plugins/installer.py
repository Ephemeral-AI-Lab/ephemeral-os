"""Plugin installation helpers."""

from __future__ import annotations

import shutil
from pathlib import Path

from ephemeralos.plugins.loader import get_user_plugins_dir


def install_plugin_from_path(source: str | Path) -> Path:
    """Install a plugin directory into the user plugin directory."""
    src = Path(source).resolve()
    dest = get_user_plugins_dir() / src.name
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(src, dest)
    return dest


def uninstall_plugin(name: str) -> bool:
    """Remove a user plugin by directory name."""
    path = get_user_plugins_dir() / name
    if not path.exists():
        return False
    shutil.rmtree(path)
    return True
