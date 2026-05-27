"""Tools that block on a helper agent (advisor) and return its result.

These are *not* submission tools: they call out to a helper agent, wait for
it, and surface its terminal output back to the caller. The helper agent
itself still uses a submission tool (``submit_advisor_feedback``) to
terminate.
"""

from __future__ import annotations

from tools.ask_helper.ask_advisor import ask_advisor
from tools._framework.core.base import BaseTool


def make_ask_helper_tools() -> list[BaseTool]:
    """Return the ask-helper tool instances for registration."""
    return [ask_advisor]


__all__ = [
    "ask_advisor",
    "make_ask_helper_tools",
]
