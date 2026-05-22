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


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if Path(str(item.fspath)).resolve().is_relative_to(_THIS_SUITE):
            item.add_marker(pytest.mark.mock)
