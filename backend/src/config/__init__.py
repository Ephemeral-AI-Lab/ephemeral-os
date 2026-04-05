"""Configuration system for EphemeralOS.

Provides settings management, path resolution, and API key handling.
"""

from config.paths import (
    get_config_dir,
    get_config_file_path,
    get_data_dir,
    get_logs_dir,
)
from config.settings import DatabaseSettings, Settings, load_settings, save_settings

__all__ = [
    "DatabaseSettings",
    "Settings",
    "get_config_dir",
    "get_config_file_path",
    "get_data_dir",
    "get_logs_dir",
    "load_settings",
    "save_settings",
]
