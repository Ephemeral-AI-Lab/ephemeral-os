"""Frozen DTOs and enums for the TaskCenter harness lifecycle."""

from task_center.domain.complex_task_request import (
    ComplexTaskCloseReport,
    ComplexTaskRequest,
    ComplexTaskRequestStatus,
)
from task_center.domain.harness_graph import (
    HarnessGraph,
    HarnessGraphFailReason,
    HarnessGraphStage,
    HarnessGraphStatus,
)
from task_center.domain.segment_closure_report import (
    AttemptedPlanEntry,
    AttemptPlanFailed,
    ClosureOutcome,
    SuccessContinue,
    TaskSegmentClosureReport,
    TerminalSuccess,
)
from task_center.domain.task_segment import (
    TaskSegment,
    TaskSegmentCreationReason,
    TaskSegmentStatus,
)

__all__ = [
    "AttemptPlanFailed",
    "AttemptedPlanEntry",
    "ClosureOutcome",
    "ComplexTaskCloseReport",
    "ComplexTaskRequest",
    "ComplexTaskRequestStatus",
    "HarnessGraph",
    "HarnessGraphFailReason",
    "HarnessGraphStage",
    "HarnessGraphStatus",
    "SuccessContinue",
    "TaskSegment",
    "TaskSegmentClosureReport",
    "TaskSegmentCreationReason",
    "TaskSegmentStatus",
    "TerminalSuccess",
]
