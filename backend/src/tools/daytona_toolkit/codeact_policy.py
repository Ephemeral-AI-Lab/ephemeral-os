"""CodeAct execution policies.

Policies gate codeact execution at three points:
  1. preflight(code)    — before execution, can reject based on static analysis
  2. post_manifest(manifest) — after execution, can reject based on runtime behaviour
  3. commit_warnings(writes) — during commit, returns advisory warnings

The core codeact_tool only knows about the CodeActPolicy protocol; team-specific
logic lives entirely in TeamCodeActPolicy.
"""

from __future__ import annotations

from typing import Any, Protocol

from tools.core.base import ToolExecutionContext

# ---------------------------------------------------------------------------
# Policy protocol
# ---------------------------------------------------------------------------


class CodeActPolicy(Protocol):
    """Three-hook contract that codeact_tool calls at well-defined points."""

    def preflight(self, code: str) -> str | None:
        """Return an error string to block execution, or None to allow."""
        ...

    def post_manifest(self, manifest: dict[str, Any]) -> str | None:
        """Return an error string to reject the result, or None to allow."""
        ...

    def commit_warnings(self, writes: list[dict[str, Any]]) -> list[str]:
        """Return advisory warnings emitted alongside a successful commit."""
        ...


# ---------------------------------------------------------------------------
# Null policy — no constraints (standalone / non-team mode)
# ---------------------------------------------------------------------------


class NullPolicy:
    """No-op policy for standalone (non-team) execution."""

    def preflight(self, code: str) -> str | None:
        return None

    def post_manifest(self, manifest: dict[str, Any]) -> str | None:
        return None

    def commit_warnings(self, writes: list[dict[str, Any]]) -> list[str]:
        return []


# ---------------------------------------------------------------------------
# Policy factory
# ---------------------------------------------------------------------------


def resolve_policy(context: ToolExecutionContext) -> CodeActPolicy:
    """CodeAct is decoupled from team coordination — always return NullPolicy.

    Write constraints are enforced at the daytona_write_file / daytona_edit_file
    layer via write_scope prefix matching. CodeAct execution is unconstrained.
    """
    return NullPolicy()
