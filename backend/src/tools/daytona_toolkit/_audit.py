"""Post-commit audit helpers shared across OCC-gated Daytona tools.

Today only ``daytona_codeact`` registers a post-hook that uses this helper —
because codeact commands commit paths the tool input does not name and the
pre-hooks cannot inspect ahead of time. For the pure OCC tools
(``daytona_write_file``, ``daytona_edit_file``, ``daytona_delete_file``,
``daytona_move_file``) the path-in equals path-out
(see ``code_intelligence/routing/service.py::_write_spec_to_change`` and
``editing/write_coordinator.py``), so a post-commit audit would fire on the
same path the pre-hook ``write_scope_advisory`` already surfaced. The helper
still lives here as a shared primitive; adding registrations for the file
tools is deliberately not wired.
"""

from __future__ import annotations

from tools.core.base import ToolExecutionContext
from tools.daytona_toolkit._daytona_utils import (
    _team_repo_write_error,
    _team_repo_write_warning,
)


def audited_write_outcome(
    context: ToolExecutionContext,
    changed_paths: list[str],
    *,
    tool_name: str,
) -> tuple[list[str], str]:
    """Classify each committed path as error / warning / ok.

    Returns ``(warnings, error_text)``. Error text is a newline-joined
    aggregate of hard-block errors (test-file writes in coordinated lanes);
    the empty string means no path tripped a hard block. Warnings are the
    outside-scope advisory strings from :func:`_team_repo_write_warning`.
    """
    warnings: list[str] = []
    errors: list[str] = []
    for path in changed_paths:
        error = _team_repo_write_error(context, path, tool_name=tool_name)
        if error is not None:
            errors.append(error)
            continue
        warning = _team_repo_write_warning(context, path, tool_name=tool_name)
        if warning is not None:
            warnings.append(warning)
    return warnings, "\n".join(errors)


__all__ = ["audited_write_outcome"]
