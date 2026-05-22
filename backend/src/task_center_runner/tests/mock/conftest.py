"""pytest conftest for SWE-EVO live e2e tests."""

from __future__ import annotations

from task_center_runner.environments.sweevo_image.fixtures import (  # noqa: F401
    sweevo_image_instance,
    sweevo_image_sandbox,
    workspace,
)
