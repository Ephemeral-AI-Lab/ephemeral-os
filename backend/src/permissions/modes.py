"""Permission mode definitions."""

from __future__ import annotations

from enum import Enum


class PermissionMode(str, Enum):
    """Supported permission modes."""

    DEFAULT = "default"
    PLAN = "plan"
    FULL_AUTO = "full_auto"
