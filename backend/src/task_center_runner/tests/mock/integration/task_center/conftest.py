"""pytest category marker for TaskCenter workflow integration tests."""

from __future__ import annotations

from pathlib import Path

import pytest

_THIS_SUITE = Path(__file__).resolve().parent


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if Path(str(item.fspath)).resolve().is_relative_to(_THIS_SUITE):
            item.add_marker(pytest.mark.task_center_integration)
