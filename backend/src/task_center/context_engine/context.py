"""Role-scoped TaskCenter context documents."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Literal, Mapping


@dataclass(frozen=True, slots=True)
class ContextSection:
    tag: str
    attrs: Mapping[str, str] = field(default_factory=dict)
    text: str | None = None
    children: tuple["ContextSection", ...] = ()
    guidance: str | None = None


@dataclass(frozen=True, slots=True)
class AgentContext:
    role: Literal["planner", "generator", "reducer"]
    sections: tuple[ContextSection, ...]
    directive: str
    context_limits: tuple[str, ...] = ()
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    target_id: str | None = None
    workflow_id: str | None = None
    iteration_id: str | None = None
    attempt_id: str | None = None
    task_id: str | None = None


__all__ = ["AgentContext", "ContextSection"]
