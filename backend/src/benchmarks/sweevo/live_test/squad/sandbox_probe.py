"""Sandbox probe primitives + SandboxCheck.

Relocated from ``benchmarks.sweevo.mock_agent_execution`` in S-03.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class SandboxCheck:
    name: str
    passed: bool
    detail: str
    changed_paths: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "passed": self.passed,
            "detail": self.detail,
            "changed_paths": list(self.changed_paths),
        }


__all__ = ["SandboxCheck"]
