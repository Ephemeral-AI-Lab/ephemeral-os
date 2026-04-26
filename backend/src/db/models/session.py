"""Compatibility module for the request persistence model.

The old conversation-session table has been replaced by TaskCenter request
records. New code should import from :mod:`db.models.task_center` directly.
"""

from __future__ import annotations

from db.models.task_center import TaskCenterRequestRecord

__all__ = ["TaskCenterRequestRecord"]
