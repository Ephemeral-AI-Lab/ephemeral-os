"""Data structures for multi-agent task coordination.

Adapted from Synthetic OS coordination models. Provides the task DAG
data model with status enums, conditions, and CI-aware planning metadata.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Callable, Literal, cast


class TaskStatus(StrEnum):
    PENDING = "pending"
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    BLOCKED = "blocked"
    SKIPPED = "skipped"


class RunStatus(StrEnum):
    PENDING = "pending"
    PLANNING = "planning"
    PLANNING_FAILED = "planning_failed"
    PLANNED = "planned"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    PARTIAL = "partial"


class TaskType(StrEnum):
    WORKER = "worker"
    TASK_PLANNER = "task_planner"


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

RunNamedAgentFn = Callable[..., Any]

_DEFAULT_WORKER_TIMEOUT = 300.0
_DEFAULT_MODEL_CONCURRENCY = 5
_FALLBACK_SUMMARY_CHARS = 1000

BLOCKING_TASK_STATUSES = frozenset(
    {TaskStatus.FAILED, TaskStatus.CANCELLED, TaskStatus.BLOCKED}
)
TERMINAL_TASK_STATUSES = frozenset(
    {
        TaskStatus.COMPLETED,
        TaskStatus.FAILED,
        TaskStatus.CANCELLED,
        TaskStatus.BLOCKED,
        TaskStatus.SKIPPED,
    }
)
TERMINAL_RUN_STATUSES = frozenset(
    {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED, RunStatus.PARTIAL}
)
_VALID_CONDITION_OPERATORS = frozenset(
    {"eq", "ne", "gt", "lt", "contains", "in", "startswith", "regex"}
)


def normalize_model_concurrency(value: int | float | None) -> int:
    """Ensure model concurrency is at least 1."""
    try:
        normalized = int(value) if value is not None else _DEFAULT_MODEL_CONCURRENCY
    except (TypeError, ValueError):
        normalized = _DEFAULT_MODEL_CONCURRENCY
    return max(1, normalized)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class TaskCondition:
    """Evaluated before dispatching a task. If the condition is not met,
    the task transitions to 'skipped' instead of 'running'."""

    depends_on_task: str
    field: str  # "status" | "artifact.<field_name>"
    operator: Literal["eq", "ne", "gt", "lt", "contains", "in", "startswith", "regex"]
    value: Any

    def to_dict(self) -> dict:
        return {
            "depends_on_task": self.depends_on_task,
            "field": self.field,
            "operator": self.operator,
            "value": self.value,
        }

    @classmethod
    def from_dict(cls, data: dict) -> TaskCondition:
        operator = str(data["operator"])
        if operator not in _VALID_CONDITION_OPERATORS:
            raise ValueError(
                f"Invalid TaskCondition operator {operator!r}, "
                f"expected one of {sorted(_VALID_CONDITION_OPERATORS)}"
            )
        return cls(
            depends_on_task=str(data["depends_on_task"]),
            field=str(data["field"]),
            operator=cast(
                Literal["eq", "ne", "gt", "lt", "contains", "in", "startswith", "regex"],
                operator,
            ),
            value=data["value"],
        )


@dataclass
class WorkspaceContract:
    """Shared-sandbox binding for a coordination plan."""

    version: int = 1
    execution_mode: str = "shared_sandbox"
    sandbox_id: str = ""
    workspace_root: str = "/workspace"
    verification_commands: list[str] = field(default_factory=list)
    verification_timeout: int | None = None

    def to_dict(self) -> dict:
        d: dict[str, Any] = {
            "version": self.version,
            "execution_mode": self.execution_mode,
            "sandbox_id": self.sandbox_id,
            "workspace_root": self.workspace_root,
            "verification_commands": self.verification_commands,
        }
        if self.verification_timeout is not None:
            d["verification_timeout"] = int(self.verification_timeout)
        return d

    @classmethod
    def from_dict(cls, data: dict) -> WorkspaceContract:
        return cls(
            version=int(data.get("version", 1)),
            execution_mode=str(data.get("execution_mode", "shared_sandbox")),
            sandbox_id=str(data.get("sandbox_id") or ""),
            workspace_root=str(data.get("workspace_root", "/workspace")),
            verification_commands=list(data.get("verification_commands", [])),
            verification_timeout=(
                int(data["verification_timeout"])
                if data.get("verification_timeout") is not None
                else None
            ),
        )


@dataclass
class TaskCIPlan:
    """CI-aware planning metadata for a single task.

    Groups file/symbol/confidence so TeamTask stays focused on coordination.
    """

    touches_paths: list[str] = field(default_factory=list)
    touches_symbols: list[str] = field(default_factory=list)
    confidence: str = "low"  # "high" | "medium" | "low"
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class ExpansionSlice:
    """Structured child slice for a task_planner expansion."""

    slice_id: str
    description: str
    owned_paths: list[str] = field(default_factory=list)
    agent_name: str | None = None


@dataclass
class TeamTask:
    """A single task in the coordination task graph."""

    task_id: str
    description: str
    agent_name: str
    depends_on: list[str] = field(default_factory=list)
    status: str = TaskStatus.PENDING
    timeout: float | None = None
    role: str = "specialist"  # specialist | replanner
    task_type: TaskType = TaskType.WORKER
    expandable: bool = False
    domain: str | None = None
    expansion_hint: str = ""
    slices: list[ExpansionSlice] = field(default_factory=list)
    expansion_status: str = "not_expanded"
    expanded_run_id: str | None = None
    ci_plan: TaskCIPlan = field(default_factory=TaskCIPlan)
    summary: str | None = None
    result_preview: str | None = None
    error: str | None = None
    run_condition: TaskCondition | None = None
    agent_session_id: str | None = None

    def __post_init__(self) -> None:
        ci_plan = self.ci_plan
        if isinstance(ci_plan, dict):
            ci_plan = TaskCIPlan(
                touches_paths=list(ci_plan.get("touches_paths", []) or []),
                touches_symbols=list(ci_plan.get("touches_symbols", []) or []),
                confidence=str(ci_plan.get("confidence", "low")) or "low",
                extra=dict(ci_plan.get("extra", {}) or {}),
            )
        elif not isinstance(ci_plan, TaskCIPlan):
            ci_plan = TaskCIPlan()
        self.ci_plan = ci_plan

        slices = self.slices
        if isinstance(slices, list):
            resolved: list[ExpansionSlice] = []
            for s in slices:
                if isinstance(s, ExpansionSlice):
                    resolved.append(s)
                elif isinstance(s, dict):
                    resolved.append(
                        ExpansionSlice(
                            slice_id=str(s.get("slice_id", "")),
                            description=str(s.get("description", "")),
                            owned_paths=list(s.get("owned_paths", []) or []),
                            agent_name=s.get("agent_name"),
                        )
                    )
            slices = resolved
        else:
            slices = []
        self.slices = slices

        if self.expandable and self.task_type != TaskType.TASK_PLANNER:
            self.task_type = TaskType.TASK_PLANNER
        elif not self.task_type:
            self.task_type = TaskType.WORKER


@dataclass
class CoordinationPlan:
    """Top-level coordination plan holding the full task graph."""

    plan_id: str
    goal: str
    project_context: str = ""
    status: str = RunStatus.PLANNING
    tasks: dict[str, TeamTask] = field(default_factory=dict)
    worker_timeout: float = _DEFAULT_WORKER_TIMEOUT
    task_planner_session_id: str | None = None
    replanner_agent: str | None = None
    depth: int = 0  # expansion depth (0 = root)
    workspace_contract: WorkspaceContract | None = None
