"""Configuration system for EphemeralOS.

Provides settings management, path resolution, and API key handling.
"""

from .defaults import (
    DEFAULT_MAX_DEPTH,
    DEFAULT_MAX_NOTE_BYTES,
    DEFAULT_MAX_PLAN_SIZE,
    DEFAULT_MAX_REPLANS_PER_RUN,
    DEFAULT_MAX_RETRIES,
    DEFAULT_MAX_TASKS,
    DEFAULT_MAX_TOTAL_NOTE_BYTES,
    DEFAULT_BASE_DELAY,
    DEFAULT_MAX_DELAY,
    DEFAULT_RETRY_STATUS_CODES,
    DEFAULT_DATABASE_POOL_SIZE,
    DEFAULT_DATABASE_MAX_OVERFLOW,
    DEFAULT_SANDBOX_CI_ROOT,
    DEFAULT_TEAM_TOOL_CALL_LIMIT,
    DEFAULT_TEAM_SAFE_AGENT_NAMES,
    OWNED_FAILURES_PREVIEW_LIMIT,
)
from .paths import (
    get_config_dir,
    get_config_file_path,
    get_data_dir,
    get_logs_dir,
)
from .settings import DatabaseSettings, Settings, load_settings, save_settings

__all__ = [
    "DatabaseSettings",
    "Settings",
    "get_config_dir",
    "get_config_file_path",
    "get_data_dir",
    "get_logs_dir",
    "load_settings",
    "save_settings",
    "DEFAULT_MAX_TASKS",
    "DEFAULT_MAX_DEPTH",
    "DEFAULT_MAX_PLAN_SIZE",
    "DEFAULT_MAX_RETRIES",
    "DEFAULT_BASE_DELAY",
    "DEFAULT_MAX_DELAY",
    "DEFAULT_RETRY_STATUS_CODES",
    "DEFAULT_DATABASE_POOL_SIZE",
    "DEFAULT_DATABASE_MAX_OVERFLOW",
    "DEFAULT_SANDBOX_CI_ROOT",
    "DEFAULT_TEAM_TOOL_CALL_LIMIT",
    "DEFAULT_TEAM_SAFE_AGENT_NAMES",
    "DEFAULT_MAX_NOTE_BYTES",
    "DEFAULT_MAX_TOTAL_NOTE_BYTES",
    "DEFAULT_MAX_REPLANS_PER_RUN",
    "OWNED_FAILURES_PREVIEW_LIMIT",
]
