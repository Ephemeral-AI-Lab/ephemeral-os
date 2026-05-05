"""Sandbox provisioning policy for TaskCenter entry runs."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
import uuid

from task_center.sandbox_binding import TaskCenterSandboxBinding


CreateSandboxFn = Callable[..., dict[str, Any]]


def _default_create(**kwargs: Any) -> dict[str, Any]:
    from sandbox.api import api

    return api.create_sandbox(**kwargs)


class SandboxProvisioner:
    """Resolve the sandbox binding for a TaskCenter run."""

    def __init__(self, *, create_fn: CreateSandboxFn | None = None) -> None:
        self._create = create_fn

    def provision(
        self,
        *,
        task_center_run_id: str,
        sandbox_id: str | None,
    ) -> TaskCenterSandboxBinding:
        explicit_id = str(sandbox_id or "").strip()
        if explicit_id:
            return TaskCenterSandboxBinding(
                sandbox_id=explicit_id,
                task_center_run_id=task_center_run_id,
                owned_by_task_center=False,
            )

        create = self._create or _default_create
        info = create(
            name=f"task-center-{uuid.uuid4().hex[:8]}",
            labels={
                "origin": "task_center",
                "task_center_run_id": task_center_run_id,
            },
        )
        new_id = str(info.get("id") or "").strip()
        if not new_id:
            raise RuntimeError("create_sandbox returned no id")
        return TaskCenterSandboxBinding(
            sandbox_id=new_id,
            task_center_run_id=task_center_run_id,
            owned_by_task_center=True,
        )


__all__ = ["CreateSandboxFn", "SandboxProvisioner"]
