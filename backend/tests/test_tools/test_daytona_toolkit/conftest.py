"""Shared Daytona tool test setup."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _register_daytona_platform_hooks() -> None:
    from tools.daytona_toolkit.hooks import register_all

    register_all()
