"""pytest conftest for mocked-agent task-center-runner tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from task_center_runner.environments.sweevo_image.fixtures import (  # noqa: F401
    sweevo_image_instance,
    sweevo_image_sandbox,
    workspace,
)

_THIS_SUITE = Path(__file__).resolve().parent
_CATEGORY_MARKER_BY_DIR = {
    _THIS_SUITE / "sandbox": pytest.mark.sandbox_integration,
    _THIS_SUITE / "task_center": pytest.mark.task_center_integration,
}


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        path = Path(str(item.fspath)).resolve()
        if path.is_relative_to(_THIS_SUITE):
            item.add_marker(pytest.mark.mock)
        for suite_dir, marker in _CATEGORY_MARKER_BY_DIR.items():
            if path.is_relative_to(suite_dir):
                item.add_marker(marker)
                break
