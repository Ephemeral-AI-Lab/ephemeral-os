"""Exceptions raised by the TaskCenter harness lifecycle."""

from __future__ import annotations


class TaskCenterInvariantViolation(Exception):
    """Raised when a harness lifecycle invariant is violated.

    Hard, non-tolerable harness state breach. Matches the existing
    ``TaskCenterInvariantViolation`` convention used elsewhere in the codebase.
    """
