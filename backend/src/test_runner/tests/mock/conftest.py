"""pytest conftest for mocked-agent test-runner tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from test_runner.environments.sweevo_image.fixtures import (  # noqa: F401
    sweevo_image_instance,
    sweevo_image_sandbox,
    workspace,
)
from test_runner.tests._live_config import rust_sandbox_runtime_unavailable_reason

_THIS_SUITE = Path(__file__).resolve().parent
_SANDBOX_SUITE = _THIS_SUITE / "sandbox"
_CATEGORY_MARKER_BY_DIR = {
    _SANDBOX_SUITE: pytest.mark.sandbox_integration,
    _THIS_SUITE / "request": pytest.mark.request_integration,
}
_RUST_RUNTIME_UNAVAILABLE = rust_sandbox_runtime_unavailable_reason()


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        path = Path(str(item.fspath)).resolve()
        if path.is_relative_to(_THIS_SUITE):
            item.add_marker(pytest.mark.mock)
        for suite_dir, marker in _CATEGORY_MARKER_BY_DIR.items():
            if path.is_relative_to(suite_dir):
                item.add_marker(marker)
                break
        if path.is_relative_to(_SANDBOX_SUITE) and _RUST_RUNTIME_UNAVAILABLE is not None:
            item.add_marker(
                pytest.mark.skip(
                    reason=_RUST_RUNTIME_UNAVAILABLE
                    or "Rust sandbox runtime unavailable"
                )
            )
