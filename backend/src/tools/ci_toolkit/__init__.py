"""CI Toolkit — read-only code intelligence queries for agents.

Lightweight toolkit for agents that need code grounding without write
access. All tools degrade gracefully if no CI service is configured.
"""

from tools.base import BaseToolkit
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
    """Read-only code intelligence toolkit.

    Provides symbol queries, workspace structure, edit hotspots,
    and recent change awareness. Requires a CI service in the
    tool execution context.
    """

    def __init__(self) -> None:
        super().__init__(
            name="code_intelligence",
            description="Read-only code intelligence: symbols, structure, changes",
            tools=[
                ci_status,
                ci_workspace_structure,
                ci_query_symbols,
                ci_query_references,
                ci_edit_hotspots,
                ci_recent_changes,
                ci_read_file,
            ],
        )


__all__ = ["CIToolkit"]
