"""LSP semantic expectations for the scheduler_demo live fixture.

The live probes resolve these anchors against the projected sandbox files at
runtime. Keeping anchors here avoids baking line numbers into probe code while
still giving the LSP checks concrete symbols and locations to assert.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LspExpectation:
    symbol: str
    source_path: str
    source_anchor: str
    definition_path: str
    definition_anchor: str
    min_references: int
    hover_contains: tuple[str, ...]


LSP_EXPECTATIONS: tuple[LspExpectation, ...] = (
    LspExpectation(
        symbol="Task",
        source_path="tests/test_task.py",
        source_anchor='task = Task(task_id="t", payload={"x": 1}, priority=5)',
        definition_path="scheduler_demo/domain/task.py",
        definition_anchor="class Task:",
        min_references=5,
        hover_contains=("Task", "scheduled", "dataclass"),
    ),
    LspExpectation(
        symbol="TaskState",
        source_path="tests/test_task.py",
        source_anchor="assert TaskState.DONE.is_terminal()",
        definition_path="scheduler_demo/domain/task.py",
        definition_anchor="class TaskState(str, Enum):",
        min_references=5,
        hover_contains=("TaskState", "Enum", "Lifecycle"),
    ),
    LspExpectation(
        symbol="Schedule",
        source_path="scheduler_demo/domain/__init__.py",
        source_anchor="from scheduler_demo.domain.schedule import Schedule, ScheduleSlot",
        definition_path="scheduler_demo/domain/schedule.py",
        definition_anchor="class Schedule:",
        min_references=2,
        hover_contains=("Schedule", "schedule", "dataclass"),
    ),
    LspExpectation(
        symbol="Priority",
        source_path="scheduler_demo/domain/__init__.py",
        source_anchor="from scheduler_demo.domain.priority import Priority",
        definition_path="scheduler_demo/domain/priority.py",
        definition_anchor="class Priority(IntEnum):",
        min_references=2,
        hover_contains=("Priority", "IntEnum", "bucket"),
    ),
    LspExpectation(
        symbol="Scheduler",
        source_path="scheduler_demo/services/__init__.py",
        source_anchor="from scheduler_demo.services.scheduler import Scheduler",
        definition_path="scheduler_demo/services/scheduler.py",
        definition_anchor="class Scheduler:",
        min_references=2,
        hover_contains=("Scheduler", "scheduler", "dataclass"),
    ),
    LspExpectation(
        symbol="MemoryStore",
        source_path="tests/test_memory_store.py",
        source_anchor="one = MemoryStore()",
        definition_path="scheduler_demo/storage/memory_store.py",
        definition_anchor="class MemoryStore:",
        min_references=4,
        hover_contains=("MemoryStore", "dict", "dataclass"),
    ),
    LspExpectation(
        symbol="JsonSerializer",
        source_path="scheduler_demo/storage/json_serializer.py",
        source_anchor="class JsonSerializer:",
        definition_path="scheduler_demo/storage/json_serializer.py",
        definition_anchor="class JsonSerializer:",
        min_references=1,
        hover_contains=("JsonSerializer", "JSON", "serializer"),
    ),
    LspExpectation(
        symbol="RetryPolicy",
        source_path="scheduler_demo/services/__init__.py",
        source_anchor="from scheduler_demo.services.retry import RetryPolicy",
        definition_path="scheduler_demo/services/retry.py",
        definition_anchor="class RetryPolicy:",
        min_references=2,
        hover_contains=("RetryPolicy", "retry", "dataclass"),
    ),
)


__all__ = ["LSP_EXPECTATIONS", "LspExpectation"]
