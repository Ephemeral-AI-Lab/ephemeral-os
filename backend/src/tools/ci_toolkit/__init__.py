"""Read-only code intelligence tools for agents."""

from tools.core.base import BaseToolkit
from tools.ci_toolkit.query_tools import (
    ci_status,
    ci_edit_hotspots,
    ci_query_symbols,
    ci_query_references,
    ci_workspace_structure,
)
from tools.ci_toolkit.file_tools import ci_read_file
from tools.ci_toolkit.lsp_tools import ci_diagnostics, ci_hover

_ALL_TOOLS = [
    ci_status,
    ci_workspace_structure,
    ci_query_symbols,
    ci_query_references,
    ci_hover,
    ci_diagnostics,
    ci_edit_hotspots,
    ci_read_file,
]

_INSTRUCTIONS = (
    "Read-only code intelligence for grounding same-run work.\n"
    "- `ci_status` — check if the code intelligence service is available.\n"
    "- `ci_workspace_structure` — tree view of the project layout.\n"
    "- `ci_query_symbols` / `ci_query_references` — locate definitions and callers.\n"
    "- `ci_hover` — precise position-based symbol info backed by the CI service.\n"
    "- `ci_diagnostics` — syntax and type diagnostics for a file.\n"
    "- `ci_edit_hotspots` — find contention-prone files before editing. "
    "Use `cross_run=True` for cross-run multi-agent contention data.\n"
    "- `ci_read_file` — read file contents via the CI service when sandbox tools are unavailable.\n"
    "- Call-chain rule — use "
    "`ci_query_symbols(...)`, `ci_query_references(...)`, `ci_hover(...)`, or "
    "`ci_diagnostics(...)` before "
    "falling back to custom runtime scripts when localizing a production boundary.\n"
    "- Dead-cycle rule — if the same boundary survives one scoped packet, one owner query, "
    "and one narrow repro, stop opening more greps or readbacks and move to edit, blocker, or replan.\n"
    "Tool-choice rule:\n"
    "- use code_intelligence for live symbol truth, recent edits, collision awareness, and call-chain localization"
)


class CIToolkit(BaseToolkit):
    """Read-only code intelligence toolkit.

    All tools are always registered. Role-based restrictions (e.g. blocking
    ci_read_file for planners) are handled via ``blocked_tools`` in agent
    definitions.
    """

    @classmethod
    def from_context(cls, ctx):  # type: ignore[override]
        return cls(
            name="code_intelligence",
            description="Read-only code intelligence: symbols, LSP, structure, changes",
            tools=list(_ALL_TOOLS),
            instructions=_INSTRUCTIONS,
        )


__all__ = ["CIToolkit"]
