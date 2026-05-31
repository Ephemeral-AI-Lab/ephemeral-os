"""Role-scoped TaskCenter context documents."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Mapping


@dataclass(frozen=True, slots=True)
class ContextSection:
    tag: str
    attrs: Mapping[str, str] = field(default_factory=dict)
    text: str | None = None
    children: tuple["ContextSection", ...] = ()


@dataclass(frozen=True, slots=True)
class AgentContext:
    role: Literal["planner", "generator", "reducer"]
    sections: tuple[ContextSection, ...]
    directive: str
    context_limits: tuple[str, ...] = ()


__all__ = ["AgentContext", "ContextSection"]
