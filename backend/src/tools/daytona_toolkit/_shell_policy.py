"""Shared shell normalization policy for Daytona CodeAct execution."""

from __future__ import annotations

import re
import shlex

_LEADING_CD_PATTERN = re.compile(
    r"^\s*cd\s+(?P<path>\"[^\"]+\"|'[^']+'|[^\s;&|]+)\s*(?P<sep>&&|;)\s*(?P<rest>.*)$",
    flags=re.S,
)
_STDERR_CAPTURE_PATTERNS = (
    (re.compile(r"\s+2>\s*&1\b"), "`2>&1`"),
    (re.compile(r"\s+2>\s*/dev/null\b"), "`2>/dev/null`"),
)


def _strip_shell_quotes(value: str) -> str:
    stripped = value.strip()
    if len(stripped) >= 2 and stripped[0] == stripped[-1] and stripped[0] in {"'", '"'}:
        return stripped[1:-1]
    return stripped


def _normalize_team_shell_command(
    command: str,
    *,
    repo_root: str | None,
) -> tuple[str, list[str]]:
    """Return a policy-normalized command and non-fatal warnings."""
    normalized = command or ""
    warnings: list[str] = []
    root = str(repo_root or "").strip()

    if root and root.lower() != "none":
        match = _LEADING_CD_PATTERN.match(normalized)
        if match:
            cd_path = _strip_shell_quotes(match.group("path"))
            same_root = (
                shlex.quote(cd_path) == shlex.quote(root)
                or cd_path.rstrip("/") == root.rstrip("/")
            )
            if same_root:
                normalized = match.group("rest").lstrip()
                warnings.append(
                    "Removed leading `cd <repo-root>` so the command stays inside "
                    "the CodeAct repo workspace."
                )

    for pattern, label in _STDERR_CAPTURE_PATTERNS:
        updated = pattern.sub("", normalized)
        if updated != normalized:
            normalized = updated
            warnings.append(
                f"Removed redundant shell capture plumbing {label}; "
                "stdout/stderr are already captured separately."
            )

    return normalized.strip(), warnings


__all__ = [
    "_LEADING_CD_PATTERN",
    "_STDERR_CAPTURE_PATTERNS",
    "_normalize_team_shell_command",
    "_strip_shell_quotes",
]
