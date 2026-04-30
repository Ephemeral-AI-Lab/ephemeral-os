"""ComplexTaskRequest domain DTO and enums."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from typing import Literal


class ComplexTaskRequestStatus(StrEnum):
    OPEN = "open"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class ComplexTaskRequest:
    """Immutable view of a persisted ComplexTaskRequest."""

    id: str
    task_center_run_id: str
    requested_by_task_id: str
    goal: str
    status: ComplexTaskRequestStatus
    task_segment_ids: tuple[str, ...]
    final_outcome: dict | None
    created_at: datetime
    updated_at: datetime
    closed_at: datetime | None

    @property
    def is_open(self) -> bool:
        return self.status == ComplexTaskRequestStatus.OPEN

    @property
    def latest_segment_id(self) -> str | None:
        return self.task_segment_ids[-1] if self.task_segment_ids else None

    def with_appended_segment(self, segment_id: str) -> "ComplexTaskRequest":
        return replace(
            self, task_segment_ids=(*self.task_segment_ids, segment_id)
        )


@dataclass(frozen=True, slots=True)
class ComplexTaskCloseReport:
    """Final report attached to ``requested_by_task_id`` when the request closes.

    Phase 04 wires the actual delivery to the executor task.
    """

    complex_task_request_id: str
    requested_by_task_id: str
    outcome: Literal["success", "failed"]
    final_segment_id: str
    final_harness_graph_id: str
