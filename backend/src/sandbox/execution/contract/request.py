"""Request object for guarded command execution."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field


@dataclass(frozen=True)
class CommandExecRequest:
    """One shell command against a workspace replacement mount."""

    request_id: str
    workspace_ref: str
    workspace_root: str
    command: tuple[str, ...]
    cwd: str = "."
    env: Mapping[str, str] = field(default_factory=dict)
    timeout_seconds: float | None = None
    actor_id: str = ""
    description: str = "shell"

    def __post_init__(self) -> None:
        request_id = str(self.request_id).strip()
        if not request_id:
            raise ValueError("request_id must not be empty")
        workspace_ref = str(self.workspace_ref).strip()
        if not workspace_ref:
            raise ValueError("workspace_ref must not be empty")
        workspace_root = str(self.workspace_root).strip()
        if not workspace_root.startswith("/"):
            raise ValueError("workspace_root must be an absolute path")
        command = tuple(str(part) for part in self.command)
        if not command or any(part == "" for part in command):
            raise ValueError("command must contain non-empty argv parts")
        timeout = self.timeout_seconds
        if timeout is not None and timeout <= 0:
            raise ValueError("timeout_seconds must be positive when provided")

        cwd_raw = str(self.cwd).strip() or "."
        cwd_normalized = os.path.normpath(cwd_raw)
        if cwd_normalized == ".." or cwd_normalized.startswith("../"):
            raise ValueError(f"cwd must not escape workspace root: {cwd_raw!r}")
        if not cwd_normalized.startswith("/") and ".." in cwd_normalized.split("/"):
            raise ValueError(f"cwd must not contain '..' segments: {cwd_raw!r}")

        object.__setattr__(self, "request_id", request_id)
        object.__setattr__(self, "workspace_ref", workspace_ref)
        object.__setattr__(self, "workspace_root", workspace_root.rstrip("/") or "/")
        object.__setattr__(self, "command", command)
        object.__setattr__(self, "cwd", cwd_normalized)
        object.__setattr__(
            self,
            "env",
            {str(key): str(value) for key, value in self.env.items()},
        )
        object.__setattr__(self, "actor_id", str(self.actor_id))
        object.__setattr__(self, "description", str(self.description or "shell"))


__all__ = ["CommandExecRequest"]
