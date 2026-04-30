"""Public ``get_attempt_count`` helper.

Phase 01 spec exit criterion: derive the count from ``harness_graph_ids``
rather than storing a separate counter.
"""

from __future__ import annotations

from task_center.domain.task_segment import TaskSegment


def get_attempt_count(task_segment: TaskSegment) -> int:
    return len(task_segment.harness_graph_ids)
