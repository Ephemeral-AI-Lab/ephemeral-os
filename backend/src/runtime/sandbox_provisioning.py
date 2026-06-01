"""Sandbox provisioning for one top-level request.

:class:`RequestSandboxProvisioner` prepares the sandbox binding for entry
startup: it either starts a caller-provided sandbox, or creates a fresh sandbox
labelled with the request id.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


CreateSandboxFn = Callable[..., dict[str, Any]]
StartSandboxFn = Callable[[str], dict[str, Any]]


@dataclass(frozen=True, slots=True)
class RequestSandboxBinding:
    sandbox_id: str
    request_id: str


def _default_create(**kwargs: Any) -> dict[str, Any]:
    import sandbox.api as sandbox_api

    return sandbox_api.create_sandbox(**kwargs)


def _default_start(sandbox_id: str) -> dict[str, Any]:
    import sandbox.api as sandbox_api

    return sandbox_api.start_sandbox(sandbox_id)


class RequestSandboxProvisioner:
    """Prepare the sandbox binding used by one top-level request."""

    def __init__(
        self,
        *,
        create_fn: CreateSandboxFn | None = None,
        start_fn: StartSandboxFn | None = None,
    ) -> None:
        self._create = create_fn
        self._start = start_fn

    def prepare_for_run(
        self,
        *,
        request_id: str,
        sandbox_id: str | None,
    ) -> RequestSandboxBinding:
        explicit_id = str(sandbox_id or "").strip()
        if explicit_id:
            start = self._start or _default_start
            start(explicit_id)
            return RequestSandboxBinding(
                sandbox_id=explicit_id,
                request_id=request_id,
            )

        create = self._create or _default_create
        info = create(
            name=f"request-{uuid.uuid4().hex[:8]}",
            labels={
                "origin": "workflow",
                "request_id": request_id,
            },
        )
        new_id = str(info.get("id") or "").strip()
        if not new_id:
            raise RuntimeError("create_sandbox returned no id")
        return RequestSandboxBinding(
            sandbox_id=new_id,
            request_id=request_id,
        )


__all__ = ["RequestSandboxBinding", "RequestSandboxProvisioner"]
