"""CodeAct execution policies — team-mode constraints extracted from codeact_tool.

Policies gate codeact execution at three points:
  1. preflight(code)    — before execution, can reject based on static analysis
  2. post_manifest(manifest) — after execution, can reject based on runtime behaviour
  3. commit_warnings(writes) — during commit, returns advisory warnings

The core codeact_tool only knows about the CodeActPolicy protocol; team-specific
logic lives entirely in TeamCodeActPolicy.
"""

from __future__ import annotations

import ast
import logging
import re
from typing import Any, Protocol

from tools.core.base import ToolExecutionContext
from tools.daytona_toolkit.tools import (
    _get_cwd,
    _verification_surface_enforcement_mode,
    is_coordinated_team_agent,
)

logger = logging.getLogger(__name__)

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
# Team policy — all team-mode constraints
# ---------------------------------------------------------------------------

_DISALLOWED_RUNTIME_CALLS = frozenset(
    {
        "asyncio.create_subprocess_exec",
        "asyncio.create_subprocess_shell",
        "os.popen",
        "os.system",
        "subprocess.Popen",
        "subprocess.call",
        "subprocess.check_call",
        "subprocess.check_output",
        "subprocess.getoutput",
        "subprocess.getstatusoutput",
        "subprocess.run",
    }
)


_VERIFY_PATH_RE = re.compile(
    r"(?<![A-Za-z0-9_./-])([A-Za-z0-9_./-]+\.py)(?![A-Za-z0-9_./-])"
)


class TeamCodeActPolicy:
    """Enforces team-mode constraints on codeact execution.

    Constraints:
    - No raw subprocess/os.system calls (must use shell() helper)
    - No ambient install commands (pip, apt, etc.) in shell() calls
    - Validators may not write repository files
    - Developers may not write to verification surfaces outside owned scope
    """

    def __init__(
        self,
        *,
        agent_name: str,
        repo_root: str,
        owned_files: set[str],
        touches_paths: set[str],
        verify_paths: set[str],
        verification_surface_write_enforcement: str,
    ) -> None:
        self.agent_name = agent_name
        self.repo_root = repo_root
        self.owned_files = owned_files
        self.touches_paths = touches_paths
        self.verify_paths = verify_paths
        self.verification_surface_write_enforcement = (
            verification_surface_write_enforcement
        )

    # -- preflight -----------------------------------------------------------

    def preflight(self, code: str) -> str | None:
        offenders = _detect_disallowed_runtime_calls(code)
        if offenders:
            rendered = ", ".join(offenders)
            return (
                "daytona_codeact: coordinated team developer/validator lanes must "
                'execute repo commands through the provided `shell("...")` helper, '
                f"not raw Python process APIs. Found disallowed call(s): {rendered}."
            )
        return None

    # -- post_manifest -------------------------------------------------------

    def post_manifest(self, manifest: dict[str, Any]) -> str | None:
        # Check write constraints
        writes = manifest.get("writes")
        if not isinstance(writes, list):
            return None

        write_paths = _extract_write_paths(writes, self.repo_root)
        if not write_paths:
            return None

        # Validators must not write any repo files
        if self.agent_name == "validator":
            rendered = ", ".join(write_paths[:3])
            return (
                "daytona_codeact: validator lanes must not write repository files. "
                f"Observed write(s): {rendered}."
            )

        # Developers: check verification surface enforcement
        allowed = self.owned_files | self.touches_paths
        verify_writes = _verification_surface_warning_paths(
            write_paths, allowed_write_paths=allowed, verify_paths=self.verify_paths
        )
        if verify_writes:
            rendered = ", ".join(verify_writes[:3])
            message = (
                "daytona_codeact: developer lanes must keep verification surfaces "
                "read-only unless the WorkItem explicitly owns or widens to them. "
                f"Observed write(s) on verification paths: {rendered}."
            )
            if self.verification_surface_write_enforcement == "warn":
                logger.warning(message)
                return None
            return message

        return None

    # -- commit_warnings -----------------------------------------------------

    def commit_warnings(self, writes: list[dict[str, Any]]) -> list[str]:
        if self.verification_surface_write_enforcement != "warn":
            return []

        write_paths = _extract_write_paths(writes, self.repo_root)
        if not write_paths:
            return []

        allowed = self.owned_files | self.touches_paths
        verify_writes = _verification_surface_warning_paths(
            write_paths, allowed_write_paths=allowed, verify_paths=self.verify_paths
        )
        if verify_writes:
            return [
                "daytona_codeact: verification-surface writes allowed in advisory "
                f"mode. Observed write(s) on verification paths: "
                f"{', '.join(verify_writes[:3])}."
            ]
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


# ---------------------------------------------------------------------------
# Helpers (moved from codeact_tool.py)
# ---------------------------------------------------------------------------


def _normalize_repo_relative_path(path: Any, repo_root: str) -> str | None:
    if not isinstance(path, str):
        return None
    cleaned = path.strip().replace("\\", "/")
    if not cleaned:
        return None
    while cleaned.startswith("./"):
        cleaned = cleaned[2:]
    cleaned = cleaned.rstrip("/")
    if not cleaned:
        return None
    if not cleaned.startswith("/"):
        return cleaned
    root = repo_root.rstrip("/")
    if root and cleaned.startswith(root + "/"):
        rel = cleaned[len(root) + 1 :].strip().rstrip("/")
        return rel or None
    return None


def _normalize_string_list(value: Any, repo_root: str) -> list[str]:
    if isinstance(value, str):
        values = [value]
    elif isinstance(value, list):
        values = [item for item in value if isinstance(item, str)]
    else:
        return []
    out: list[str] = []
    for item in values:
        normalized = _normalize_repo_relative_path(item, repo_root)
        if normalized:
            out.append(normalized)
    return out


def _extract_verify_paths(value: Any, repo_root: str) -> list[str]:
    if isinstance(value, str):
        candidates = [value]
    elif isinstance(value, list):
        candidates = [item for item in value if isinstance(item, str)]
    else:
        return []
    out: list[str] = []
    for item in candidates:
        stripped = item.strip()
        if not stripped:
            continue
        if stripped.endswith(".py") or "::" in stripped:
            normalized = _normalize_repo_relative_path(
                stripped.split("::", 1)[0], repo_root
            )
            if normalized:
                out.append(normalized)
        for match in _VERIFY_PATH_RE.findall(stripped):
            normalized = _normalize_repo_relative_path(
                match.split("::", 1)[0], repo_root
            )
            if normalized:
                out.append(normalized)
    return out


def _extract_write_paths(
    writes: list[Any], repo_root: str
) -> list[str]:
    return [
        rel
        for rel in (
            _normalize_repo_relative_path(item.get("path"), repo_root)
            for item in writes
            if isinstance(item, dict)
        )
        if rel
    ]


def _verification_surface_warning_paths(
    write_paths: list[str],
    *,
    allowed_write_paths: set[str],
    verify_paths: set[str],
) -> list[str]:
    return sorted(
        path
        for path in set(write_paths)
        if path in verify_paths and path not in allowed_write_paths
    )


def _resolve_call_name(node: ast.AST, aliases: dict[str, str]) -> str | None:
    parts: list[str] = []
    current = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if not isinstance(current, ast.Name):
        return None
    parts.append(current.id)
    raw_name = ".".join(reversed(parts))
    root, *rest = raw_name.split(".")
    mapped_root = aliases.get(root, root)
    return ".".join([mapped_root, *rest]) if rest else mapped_root


def _detect_disallowed_runtime_calls(code: str) -> list[str]:
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []

    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                name = alias.asname or alias.name
                aliases[name] = alias.name
        elif isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                name = alias.asname or alias.name
                aliases[name] = f"{node.module}.{alias.name}"

    offenders: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        resolved = _resolve_call_name(node.func, aliases)
        if resolved in _DISALLOWED_RUNTIME_CALLS:
            offenders.add(resolved)
    return sorted(offenders)


