"""Read-only code intelligence queries for agents."""

from tools.core.base import BaseToolkit
from tools.ci_toolkit.query_tools import (
    ci_status,
    ci_edit_hotspots,
    ci_recent_changes,
    ci_query_symbols,
    ci_query_references,
    ci_workspace_structure,
)
from tools.ci_toolkit.file_tools import ci_read_file


class CIToolkit(BaseToolkit):
    """Read-only code intelligence toolkit."""

    _NO_FILE_READ_AGENTS = frozenset({"team_planner", "team_replanner"})
    _NO_CHANGE_AWARENESS_AGENTS = frozenset({"team_planner"})

    def __init__(
        self,
        *,
        include_file_reads: bool = True,
        include_change_awareness: bool = True,
    ) -> None:
        tools = [
            ci_status,
            ci_workspace_structure,
            ci_query_symbols,
            ci_query_references,
        ]
        if include_change_awareness:
            tools.extend([ci_edit_hotspots, ci_recent_changes])
        instructions = (
            "Read-only code intelligence for grounding same-run work. "
            "If Atlas or briefings disagree with current CI state, trust CI.\n\n"
            "- `ci_status` — check if the code intelligence service is available.\n"
            "- `ci_workspace_structure` — tree view of the project layout.\n"
            "- `ci_query_symbols` / `ci_query_references` — locate definitions and callers.\n"
            "- Call-chain rule — use "
            "`ci_query_symbols(...)` or `ci_query_references(...)` before falling back to "
            "custom runtime scripts when localizing a production boundary.\n"
            "- Dead-cycle rule — if the same boundary survives one scoped packet, one owner query, "
            "and one narrow repro, stop opening more greps or readbacks and move to edit, blocker, or replan.\n"
        )
        if include_change_awareness:
            instructions += (
                "- `ci_edit_hotspots` — find contention-prone files before editing.\n"
                "- `ci_recent_changes` — see same-run sibling edits, not release archaeology.\n"
            )
        else:
            instructions += (
                "- `ci_edit_hotspots` and `ci_recent_changes` are intentionally unavailable "
                "for planner-style agents.\n"
            )
        if include_file_reads:
            tools.append(ci_read_file)
            instructions += "- `ci_read_file` — read file contents via the CI service when sandbox tools are unavailable."
        else:
            instructions += "- `ci_read_file` is intentionally unavailable in planner mode."
        instructions += (
            "\nTool-choice rule:\n"
            "- use code_intelligence for live symbol truth, recent edits, collision awareness, and call-chain localization"
        )
        super().__init__(
            name="code_intelligence",
            description="Read-only code intelligence: symbols, structure, changes",
            tools=tools,
            instructions=instructions,
        )

    @classmethod
    def from_context(cls, ctx):  # type: ignore[override]
        agent_name = str((ctx.metadata or {}).get("agent_name") or "").strip()
        return cls(
            include_file_reads=agent_name not in cls._NO_FILE_READ_AGENTS,
            include_change_awareness=agent_name not in cls._NO_CHANGE_AWARENESS_AGENTS,
        )


__all__ = ["CIToolkit"]
