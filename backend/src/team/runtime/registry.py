"""In-process registry mapping ``team_run_id`` → live ``TeamRun``.

Tools that need run-scoped state (notably ``share_briefing``) look up
their owning ``TeamRun`` here using the ``team_run_id`` plumbed onto
``ExecutionMetadata`` by the executor's query-context builder.

The registry is intentionally simple: a module-level dict guarded by
the GIL. Single-process by design — distributed coordination would use
a different mechanism entirely.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from team.runtime.team_run import TeamRun


_active: dict[str, "TeamRun"] = {}


def register(team_run: "TeamRun") -> None:
    _active[team_run.id] = team_run


def unregister(team_run_id: str) -> None:
    _active.pop(team_run_id, None)


def get(team_run_id: str) -> "TeamRun | None":
    return _active.get(team_run_id)
