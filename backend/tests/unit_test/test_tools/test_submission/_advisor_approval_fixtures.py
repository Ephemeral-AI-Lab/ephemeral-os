"""Shared fixture: construct a synthetic ask_advisor approval transcript pair.

This module is a thin re-export of
``task_center_runner.agent.mock._advisor_approval.build_advisor_approval_messages``.
The canonical helper lives under ``src/`` so the mock runner can import it
without inverting the test→src dependency direction. Test imports of the form
``from .test_submission._advisor_approval_fixtures import
build_advisor_approval_messages`` continue to work unchanged.
"""

from __future__ import annotations

from task_center_runner.agent.mock._advisor_approval import (
    build_advisor_approval_messages,
)

__all__ = ["build_advisor_approval_messages"]
