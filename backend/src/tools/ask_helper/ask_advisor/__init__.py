"""Package for the `ask_advisor` tool."""

from . import ask_advisor as _impl

ask_advisor = _impl.ask_advisor
AskAdvisorInput = _impl.AskAdvisorInput

__all__ = ["AskAdvisorInput", "ask_advisor"]
