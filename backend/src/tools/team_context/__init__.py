"""Team-context toolkit — run-scoped shared briefings (§13)."""

from tools.core.base import BaseToolkit
from tools.team_context.share_briefing import share_briefing


class TeamContextToolkit(BaseToolkit):
    """Tools for promoting briefs into the run-scoped shared context.

    Currently just :func:`share_briefing`. Gated to consumer agents
    (planners and similar) — never granted to scouts, which must not
    self-promote partial briefs.
    """

    def __init__(self) -> None:
        super().__init__(
            name="team_context",
            description="Run-scoped shared briefings for cross-WorkItem context inheritance.",
            tools=[share_briefing],
            instructions=(
                "Promote a high-confidence brief into the run-scoped shared "
                "context so future WorkItems and subagents inherit it.\n\n"
                "- `share_briefing` — attach a brief (artifact ref or inline "
                "text) to the run's shared context. Only promote briefs you "
                "trust; promoted briefs are visible to every subsequent "
                "executor in this run.\n"
                "- Use `source=\"artifact\"` only when you already have a real "
                "team artifact ref (for example an atlas staged ref or a "
                "completed WorkItem artifact).\n"
                "- Fresh `run_subagent` scout results should usually be shared "
                "as `source=\"inline\"` distilled notes, or kept local to the "
                "current planner turn."
            ),
        )


__all__ = ["TeamContextToolkit", "share_briefing"]
