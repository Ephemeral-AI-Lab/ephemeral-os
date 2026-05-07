"""TaskCenter submission tools."""

from __future__ import annotations

from typing import Any


def __getattr__(name: str) -> Any:
    if name == "make_submission_tools":
        from tools.submission.factory import make_submission_tools

        return make_submission_tools
    raise AttributeError(name)

__all__ = ["make_submission_tools"]
