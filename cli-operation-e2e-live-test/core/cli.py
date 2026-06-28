"""Thin sandbox-cli wrapper: run an operation, return its parsed JSON.

The CLI writes its result as a single JSON line — to stdout on success (exit 0)
and to stderr on error (exit 1). We capture both and parse whichever carries the
JSON, so error responses come back as ``{"error": {...}}`` dicts rather than
exceptions. Tests assert on the structured result; they never read logs.
"""

import json
import subprocess

from .config import REPO_ROOT, SANDBOX_CLI


class CliError(Exception):
    """The CLI produced output that was not a JSON line."""


def cli(*args, timeout=180):
    """Run ``sandbox-cli <args...>`` and return the parsed JSON response."""
    proc = subprocess.run(
        [str(SANDBOX_CLI), *map(str, args)],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    raw = proc.stdout.strip() or proc.stderr.strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CliError(
            f"non-JSON CLI output (exit {proc.returncode}): {raw!r}"
        ) from exc


def manager(operation, *args, **kwargs):
    """Run a manager-space operation."""
    return cli("manager", operation, *args, **kwargs)


def runtime(sandbox_id, operation, *args, **kwargs):
    """Run a runtime-space operation, routed to ``sandbox_id``.

    The ``--sandbox-id`` flag must precede the operation name.
    """
    return cli("runtime", "--sandbox-id", sandbox_id, operation, *args, **kwargs)


def observability(operation, *args, **kwargs):
    """Run an observability-space operation."""
    return cli("observability", operation, *args, **kwargs)


def is_error(result):
    return isinstance(result, dict) and "error" in result
