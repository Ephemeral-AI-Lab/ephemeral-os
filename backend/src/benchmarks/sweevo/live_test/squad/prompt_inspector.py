"""PromptInspection, LaunchRecord, ToolCallRecord dataclasses.

Relocated from ``benchmarks.sweevo.mock_agent_execution`` in S-03.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class PromptInspection:
    task_id: str
    agent_name: str
    role: str
    checks: dict[str, bool]
    justification: str

    @property
    def passed(self) -> bool:
        return all(self.checks.values())

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["passed"] = self.passed
        return payload


@dataclass(frozen=True, slots=True)
class LaunchRecord:
    task_id: str
    attempt_id: str | None
    agent_name: str
    role: str
    prompt_preview: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ToolCallRecord:
    task_id: str
    tool_name: str
    is_error: bool
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


__all__ = [
    "LaunchRecord",
    "PromptInspection",
    "ToolCallRecord",
]
