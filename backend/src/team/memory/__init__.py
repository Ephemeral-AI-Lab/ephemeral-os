"""Typed durable team memory records.

This store is intentionally separate from Atlas:

- Atlas remains a cross-run structural scout cache.
- Team memory stores non-Atlas facts such as validator outcomes,
  coordination conflicts, and architecture decisions.
"""

from team.memory.model import TeamMemoryRecordModel
from team.memory.store import TeamMemoryRecord, TeamMemoryStore, get_default_store

__all__ = [
    "TeamMemoryRecord",
    "TeamMemoryRecordModel",
    "TeamMemoryStore",
    "get_default_store",
]
