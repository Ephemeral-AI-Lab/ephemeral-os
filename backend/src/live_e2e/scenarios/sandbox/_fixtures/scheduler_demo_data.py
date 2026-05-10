"""Final-form content + skeleton + patches for the scheduler_demo fixture.

Per §5 of the plan, fixtures are stdlib-only Python checked into the repo as
plain text. Each non-trivial source/test file is paired with a small skeleton
stub plus an ordered list of search/replace patches that, when applied, yield
the final form.

The probe writes the skeleton via ``write_file`` and then applies the patch
list via repeated ``edit_file`` calls. That is what produces the ≥4× edit:write
ratio called for in §13.6.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Patch:
    old_text: str
    new_text: str
    description: str


@dataclass(frozen=True)
class FixtureFile:
    relative_path: str
    final: str
    skeleton: str
    patches: tuple[Patch, ...] = field(default_factory=tuple)
    is_init: bool = False
    """``True`` when the file is an ``__init__.py``-style stub written with no
    patches (kept short on purpose; we still want a write_file call but no
    edit progression for empty packages)."""


# ============================================================================
# Helpers
# ============================================================================


def _build(
    *,
    relative_path: str,
    skeleton: str,
    blocks: tuple[tuple[str, str], ...],
) -> FixtureFile:
    """Construct a FixtureFile by composing *skeleton* with *blocks*.

    *blocks* is an ordered tuple of ``(anchor, block_text)`` pairs. Each block
    is appended after *anchor* via a search/replace patch — the anchor token
    must occur exactly once in the working text at patch time. The first patch
    sees the skeleton; the second sees the skeleton with the first block
    inserted; and so on.

    The final form is the skeleton with every block inserted in order.
    """
    final = skeleton
    patches: list[Patch] = []
    for index, (anchor, block_text) in enumerate(blocks):
        replacement = anchor + block_text
        if anchor not in final:
            raise ValueError(
                f"{relative_path}: anchor missing in working text at patch {index}: "
                f"{anchor!r}"
            )
        final = final.replace(anchor, replacement, 1)
        patches.append(
            Patch(
                old_text=anchor,
                new_text=replacement,
                description=f"build {relative_path} block {index}",
            )
        )
    return FixtureFile(
        relative_path=relative_path,
        final=final,
        skeleton=skeleton,
        patches=tuple(patches),
    )


def _init(relative_path: str, content: str = "") -> FixtureFile:
    return FixtureFile(
        relative_path=relative_path,
        final=content,
        skeleton=content,
        patches=(),
        is_init=True,
    )


# ============================================================================
# Fixture files
# ============================================================================
# Each fixture is a small stdlib-only Python module. The skeleton is a minimal
# stub; the patch list builds the file up incrementally so the OCC apply path
# is exercised many times per file.
# ============================================================================


# --- Top-level project files ------------------------------------------------

_GITIGNORE = "__pycache__/\n*.pyc\n.pytest_cache/\n.metrics/\n"

_PYPROJECT = """\
[tool.pytest.ini_options]
minversion = "7.0"
addopts = "-q"
testpaths = ["tests"]
pythonpath = ["."]
"""

_ROOT_CONFTEST_SKELETON = """\
\"\"\"Top-level pytest fixtures for the scheduler_demo project.\"\"\"
from __future__ import annotations

import pytest


# fixtures
"""

_ROOT_CONFTEST_BLOCKS: tuple[tuple[str, str], ...] = (
    (
        "# fixtures\n",
        """

@pytest.fixture
def task_factory():
    from scheduler_demo.domain.task import Task

    def make(task_id: str = "t-1", payload: str = "demo") -> Task:
        return Task(task_id=task_id, payload=payload)

    return make
""",
    ),
    (
        "    return make\n",
        """

@pytest.fixture
def schedule_factory():
    from scheduler_demo.domain.schedule import Schedule

    def make(name: str = "default") -> Schedule:
        return Schedule(name=name)

    return make
""",
    ),
)


# --- scheduler_demo/__init__.py ---------------------------------------------

_PKG_INIT_FINAL = '''\
"""scheduler_demo — toy task scheduler / queue library (stdlib only).

This package backs the complex_project_build live e2e scenario. It is a
stand-alone stdlib-only Python project that exercises the layer-stack /
overlay / OCC projection so the paired live test can assert that pytest
imports and runs every projected file.
"""

from scheduler_demo.config import Config, load_config
from scheduler_demo.errors import (
    SchedulerError,
    TaskAlreadyExistsError,
    TaskNotFoundError,
)

__all__ = [
    "Config",
    "SchedulerError",
    "TaskAlreadyExistsError",
    "TaskNotFoundError",
    "load_config",
]
'''


# --- scheduler_demo/config.py -----------------------------------------------

_CONFIG_SKELETON = '''\
"""Configuration helpers for scheduler_demo."""
from __future__ import annotations

from dataclasses import dataclass


# ---- config dataclass ----
'''

_CONFIG_BLOCKS: tuple[tuple[str, str], ...] = (
    (
        "# ---- config dataclass ----\n",
        """

@dataclass(frozen=True)
class Config:
    \"\"\"Frozen runtime configuration for a scheduler instance.\"\"\"

    max_workers: int = 4
    retry_limit: int = 3
    default_priority: int = 0
    default_timeout_s: float = 30.0
    serializer: str = "json"
""",
    ),
    (
        '    serializer: str = "json"\n',
        """
    def with_workers(self, workers: int) -> "Config":
        return Config(
            max_workers=int(workers),
            retry_limit=self.retry_limit,
            default_priority=self.default_priority,
            default_timeout_s=self.default_timeout_s,
            serializer=self.serializer,
        )

    def with_serializer(self, serializer: str) -> "Config":
        return Config(
            max_workers=self.max_workers,
            retry_limit=self.retry_limit,
            default_priority=self.default_priority,
            default_timeout_s=self.default_timeout_s,
            serializer=str(serializer),
        )
""",
    ),
    (
        "            serializer=str(serializer),\n        )\n",  # unique end-of-with_serializer anchor
        """

def load_config(payload: dict | None = None) -> Config:
    \"\"\"Build a Config from a plain dict (e.g. parsed JSON).\"\"\"
    payload = payload or {}
    return Config(
        max_workers=int(payload.get("max_workers", 4)),
        retry_limit=int(payload.get("retry_limit", 3)),
        default_priority=int(payload.get("default_priority", 0)),
        default_timeout_s=float(payload.get("default_timeout_s", 30.0)),
        serializer=str(payload.get("serializer", "json")),
    )


def merge_configs(base: Config, override: Config) -> Config:
    \"\"\"Layer two configs by taking non-default values from *override*.\"\"\"
    return Config(
        max_workers=override.max_workers or base.max_workers,
        retry_limit=override.retry_limit or base.retry_limit,
        default_priority=override.default_priority or base.default_priority,
        default_timeout_s=override.default_timeout_s or base.default_timeout_s,
        serializer=override.serializer or base.serializer,
    )
""",
    ),
)


# --- scheduler_demo/errors.py -----------------------------------------------

_ERRORS_SKELETON = '''\
"""Exception hierarchy for scheduler_demo."""
from __future__ import annotations


# ---- error classes ----
'''

_ERRORS_BLOCKS: tuple[tuple[str, str], ...] = (
    (
        "# ---- error classes ----\n",
        """

class SchedulerError(Exception):
    \"\"\"Base exception for the scheduler_demo package.\"\"\"


class TaskNotFoundError(SchedulerError):
    \"\"\"Raised when a task lookup fails.\"\"\"

    def __init__(self, task_id: str) -> None:
        super().__init__(f"task not found: {task_id}")
        self.task_id = task_id
""",
    ),
    (
        "        self.task_id = task_id\n",
        """

class TaskAlreadyExistsError(SchedulerError):
    \"\"\"Raised when attempting to insert a task whose id already exists.\"\"\"

    def __init__(self, task_id: str) -> None:
        super().__init__(f"task already exists: {task_id}")
        self.task_id = task_id


class InvalidTaskStateError(SchedulerError):
    \"\"\"Raised when a task transitions to a state that is not allowed.\"\"\"

    def __init__(self, task_id: str, attempted: str) -> None:
        super().__init__(
            f"task {task_id} cannot transition to state {attempted!r}"
        )
        self.task_id = task_id
        self.attempted = attempted


class SerializerError(SchedulerError):
    \"\"\"Raised when a payload cannot be serialized or deserialized.\"\"\"


class ScheduleConflictError(SchedulerError):
    \"\"\"Raised when two tasks claim the same exclusive schedule slot.\"\"\"
""",
    ),
)


# --- scheduler_demo/domain/__init__.py --------------------------------------

_DOMAIN_INIT_FINAL = '''\
"""Domain models for scheduler_demo."""

from scheduler_demo.domain.priority import Priority
from scheduler_demo.domain.schedule import Schedule, ScheduleSlot
from scheduler_demo.domain.task import Task, TaskState

__all__ = [
    "Priority",
    "Schedule",
    "ScheduleSlot",
    "Task",
    "TaskState",
]
'''


# --- scheduler_demo/domain/task.py ------------------------------------------

_TASK_SKELETON = '''\
"""Task domain model."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


# ---- task state ----
'''

_TASK_BLOCKS: tuple[tuple[str, str], ...] = (
    (
        "# ---- task state ----\n",
        """

class TaskState(str, Enum):
    \"\"\"Lifecycle states a task can be in.\"\"\"

    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @classmethod
    def terminal_states(cls) -> tuple["TaskState", ...]:
        return (cls.DONE, cls.FAILED, cls.CANCELLED)

    def is_terminal(self) -> bool:
        return self in self.terminal_states()
""",
    ),
    (
        "        return self in self.terminal_states()\n",
        """

_LEGAL_TRANSITIONS: dict[TaskState, frozenset[TaskState]] = {
    TaskState.PENDING: frozenset({TaskState.RUNNING, TaskState.CANCELLED}),
    TaskState.RUNNING: frozenset(
        {TaskState.DONE, TaskState.FAILED, TaskState.CANCELLED}
    ),
    TaskState.DONE: frozenset(),
    TaskState.FAILED: frozenset({TaskState.PENDING}),
    TaskState.CANCELLED: frozenset(),
}


@dataclass
class Task:
    \"\"\"A scheduled unit of work.\"\"\"

    task_id: str
    payload: Any = None
    state: TaskState = TaskState.PENDING
    priority: int = 0
    attempts: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)
""",
    ),
    (
        "    metadata: dict[str, Any] = field(default_factory=dict)\n",
        """
    def transition(self, new_state: TaskState) -> None:
        legal = _LEGAL_TRANSITIONS[self.state]
        if new_state not in legal:
            from scheduler_demo.errors import InvalidTaskStateError

            raise InvalidTaskStateError(self.task_id, new_state.value)
        self.state = new_state

    def increment_attempt(self) -> int:
        self.attempts += 1
        return self.attempts

    def is_active(self) -> bool:
        return not self.state.is_terminal()

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "payload": self.payload,
            "state": self.state.value,
            "priority": int(self.priority),
            "attempts": int(self.attempts),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "Task":
        return cls(
            task_id=str(payload["task_id"]),
            payload=payload.get("payload"),
            state=TaskState(payload.get("state", "pending")),
            priority=int(payload.get("priority", 0)),
            attempts=int(payload.get("attempts", 0)),
            metadata=dict(payload.get("metadata") or {}),
        )
""",
    ),
)


# --- scheduler_demo/domain/schedule.py --------------------------------------

_SCHEDULE_SKELETON = '''\
"""Schedule domain model."""
from __future__ import annotations

from dataclasses import dataclass, field


# ---- schedule slot ----
'''

_SCHEDULE_BLOCKS: tuple[tuple[str, str], ...] = (
    (
        "# ---- schedule slot ----\n",
        """

@dataclass(frozen=True)
class ScheduleSlot:
    \"\"\"A single slot inside a Schedule.\"\"\"

    slot_id: str
    capacity: int = 1
    exclusive: bool = False

    def can_accept(self, current_load: int) -> bool:
        return current_load < self.capacity
""",
    ),
    (
        "        return current_load < self.capacity\n",
        """

@dataclass
class Schedule:
    \"\"\"A named schedule consisting of one or more slots.\"\"\"

    name: str
    slots: list[ScheduleSlot] = field(default_factory=list)

    def add_slot(self, slot_id: str, capacity: int = 1, *, exclusive: bool = False) -> ScheduleSlot:
        slot = ScheduleSlot(slot_id=slot_id, capacity=int(capacity), exclusive=bool(exclusive))
        self.slots.append(slot)
        return slot

    def find_slot(self, slot_id: str) -> ScheduleSlot | None:
        for slot in self.slots:
            if slot.slot_id == slot_id:
                return slot
        return None

    def remove_slot(self, slot_id: str) -> bool:
        for index, slot in enumerate(self.slots):
            if slot.slot_id == slot_id:
                self.slots.pop(index)
                return True
        return False

    def total_capacity(self) -> int:
        return sum(slot.capacity for slot in self.slots)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "slots": [
                {
                    "slot_id": s.slot_id,
                    "capacity": s.capacity,
                    "exclusive": s.exclusive,
                }
                for s in self.slots
            ],
        }
""",
    ),
)


# --- scheduler_demo/domain/priority.py --------------------------------------

_PRIORITY_SKELETON = '''\
"""Priority helpers for scheduler_demo."""
from __future__ import annotations

from enum import IntEnum


# ---- priority enum ----
'''

_PRIORITY_BLOCKS: tuple[tuple[str, str], ...] = (
    (
        "# ---- priority enum ----\n",
        """

class Priority(IntEnum):
    LOW = 0
    NORMAL = 5
    HIGH = 9
    CRITICAL = 13

    @classmethod
    def from_int(cls, value: int) -> "Priority":
        \"\"\"Snap a raw integer to the closest defined Priority bucket.\"\"\"
        if value <= cls.LOW:
            return cls.LOW
        if value <= cls.NORMAL:
            return cls.NORMAL
        if value <= cls.HIGH:
            return cls.HIGH
        return cls.CRITICAL

    def boost(self, amount: int = 1) -> "Priority":
        return Priority.from_int(int(self) + int(amount))
""",
    ),
    (
        "        return Priority.from_int(int(self) + int(amount))\n",
        """

def compare(left: int, right: int) -> int:
    \"\"\"Standard 3-way compare for priority ordering.\"\"\"
    if left < right:
        return -1
    if left > right:
        return 1
    return 0


def highest(values: list[int]) -> int:
    if not values:
        return int(Priority.LOW)
    return max(int(value) for value in values)
""",
    ),
)


# --- scheduler_demo/services/__init__.py ------------------------------------

_SERVICES_INIT_FINAL = '''\
"""Service-layer modules for scheduler_demo."""

from scheduler_demo.services.executor import Executor
from scheduler_demo.services.retry import RetryPolicy
from scheduler_demo.services.scheduler import Scheduler

__all__ = ["Executor", "RetryPolicy", "Scheduler"]
'''


# --- scheduler_demo/services/scheduler.py -----------------------------------

_SCHEDULER_SKELETON = '''\
"""Scheduler service."""
from __future__ import annotations

from dataclasses import dataclass, field

from scheduler_demo.config import Config
from scheduler_demo.domain.priority import Priority
from scheduler_demo.domain.task import Task, TaskState


# ---- scheduler service ----
'''

_SCHEDULER_BLOCKS: tuple[tuple[str, str], ...] = (
    (
        "# ---- scheduler service ----\n",
        """

@dataclass
class Scheduler:
    \"\"\"In-memory scheduler that ranks tasks by priority then insertion.\"\"\"

    config: Config = field(default_factory=Config)
    _tasks: list[Task] = field(default_factory=list)

    def submit(self, task: Task) -> None:
        for existing in self._tasks:
            if existing.task_id == task.task_id:
                from scheduler_demo.errors import TaskAlreadyExistsError

                raise TaskAlreadyExistsError(task.task_id)
        self._tasks.append(task)
        self._tasks.sort(key=lambda t: (-int(t.priority), t.task_id))
""",
    ),
    (
        "        self._tasks.sort(key=lambda t: (-int(t.priority), t.task_id))\n",
        """

    def next_pending(self) -> Task | None:
        for task in self._tasks:
            if task.state == TaskState.PENDING:
                return task
        return None

    def fetch(self, task_id: str) -> Task:
        for task in self._tasks:
            if task.task_id == task_id:
                return task
        from scheduler_demo.errors import TaskNotFoundError

        raise TaskNotFoundError(task_id)

    def all_tasks(self) -> list[Task]:
        return list(self._tasks)

    def boost(self, task_id: str, amount: int = 1) -> Task:
        task = self.fetch(task_id)
        task.priority = int(Priority.from_int(int(task.priority) + amount))
        self._tasks.sort(key=lambda t: (-int(t.priority), t.task_id))
        return task

    def cancel(self, task_id: str) -> Task:
        task = self.fetch(task_id)
        task.transition(TaskState.CANCELLED)
        return task
""",
    ),
)


# --- scheduler_demo/services/executor.py ------------------------------------

_EXECUTOR_SKELETON = '''\
"""Executor service."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from scheduler_demo.domain.task import Task, TaskState


# ---- executor ----
'''

_EXECUTOR_BLOCKS: tuple[tuple[str, str], ...] = (
    (
        "# ---- executor ----\n",
        """

Handler = Callable[[Task], None]


@dataclass
class Executor:
    \"\"\"Synchronous executor — applies a registered handler per task.\"\"\"

    handlers: dict[str, Handler] = field(default_factory=dict)
    history: list[str] = field(default_factory=list)

    def register(self, kind: str, handler: Handler) -> None:
        self.handlers[str(kind)] = handler

    def run(self, task: Task) -> None:
        if task.state != TaskState.PENDING:
            from scheduler_demo.errors import InvalidTaskStateError

            raise InvalidTaskStateError(task.task_id, task.state.value)
        task.transition(TaskState.RUNNING)
""",
    ),
    (
        "        task.transition(TaskState.RUNNING)\n",
        """
        kind = str(task.metadata.get("kind", "default"))
        handler = self.handlers.get(kind, self.handlers.get("default"))
        if handler is None:
            task.transition(TaskState.FAILED)
            self.history.append(task.task_id)
            return
        try:
            handler(task)
        except Exception:  # noqa: BLE001 — toy executor
            task.increment_attempt()
            task.transition(TaskState.FAILED)
        else:
            task.transition(TaskState.DONE)
        self.history.append(task.task_id)

    def has_run(self, task_id: str) -> bool:
        return task_id in self.history

    def reset(self) -> None:
        self.history.clear()
""",
    ),
)


# --- scheduler_demo/services/retry.py ---------------------------------------

_RETRY_SKELETON = '''\
"""Retry policy for failed tasks."""
from __future__ import annotations

from dataclasses import dataclass

from scheduler_demo.domain.task import Task, TaskState


# ---- retry policy ----
'''

_RETRY_BLOCKS: tuple[tuple[str, str], ...] = (
    (
        "# ---- retry policy ----\n",
        """

@dataclass
class RetryPolicy:
    max_attempts: int = 3
    backoff_base_s: float = 0.5

    def should_retry(self, task: Task) -> bool:
        if task.state != TaskState.FAILED:
            return False
        return task.attempts < self.max_attempts

    def next_delay_s(self, task: Task) -> float:
        attempt = max(1, task.attempts)
        return float(self.backoff_base_s) * (2 ** (attempt - 1))

    def reset(self, task: Task) -> None:
        if task.state == TaskState.FAILED:
            task.transition(TaskState.PENDING)
""",
    ),
)


# --- scheduler_demo/storage/__init__.py -------------------------------------

_STORAGE_INIT_FINAL = '''\
"""Storage backends for scheduler_demo."""

from scheduler_demo.storage.memory_store import MemoryStore
from scheduler_demo.storage.serializer import dump, load

__all__ = ["MemoryStore", "dump", "load"]
'''


# --- scheduler_demo/storage/memory_store.py ---------------------------------

_MEMORY_SKELETON = '''\
"""In-memory task store."""
from __future__ import annotations

from dataclasses import dataclass, field

from scheduler_demo.domain.task import Task


# ---- memory store ----
'''

_MEMORY_BLOCKS: tuple[tuple[str, str], ...] = (
    (
        "# ---- memory store ----\n",
        """

@dataclass
class MemoryStore:
    \"\"\"Simple dict-backed task store.\"\"\"

    items: dict[str, Task] = field(default_factory=dict)

    def put(self, task: Task) -> None:
        if task.task_id in self.items:
            from scheduler_demo.errors import TaskAlreadyExistsError

            raise TaskAlreadyExistsError(task.task_id)
        self.items[task.task_id] = task

    def fetch(self, task_id: str) -> Task:
        try:
            return self.items[task_id]
        except KeyError:
            from scheduler_demo.errors import TaskNotFoundError

            raise TaskNotFoundError(task_id) from None
""",
    ),
    (
        "            raise TaskNotFoundError(task_id) from None\n",
        """

    def remove(self, task_id: str) -> Task:
        try:
            return self.items.pop(task_id)
        except KeyError:
            from scheduler_demo.errors import TaskNotFoundError

            raise TaskNotFoundError(task_id) from None

    def all(self) -> list[Task]:
        return list(self.items.values())

    def __len__(self) -> int:
        return len(self.items)

    def __contains__(self, task_id: object) -> bool:
        return str(task_id) in self.items
""",
    ),
)


# --- scheduler_demo/storage/serializer.py -----------------------------------

_SERIALIZER_SKELETON = '''\
"""JSON serializer for the scheduler_demo domain.\"\"\"
from __future__ import annotations

import json

from scheduler_demo.domain.task import Task


# ---- serializer ----
'''

_SERIALIZER_BLOCKS: tuple[tuple[str, str], ...] = (
    (
        "# ---- serializer ----\n",
        """

def dump(tasks: list[Task]) -> str:
    return json.dumps([task.to_dict() for task in tasks], sort_keys=True)


def load(payload: str) -> list[Task]:
    raw = json.loads(payload)
    if not isinstance(raw, list):
        from scheduler_demo.errors import SerializerError

        raise SerializerError("expected list at top level")
    return [Task.from_dict(item) for item in raw]


def round_trip(tasks: list[Task]) -> list[Task]:
    return load(dump(tasks))
""",
    ),
)


# --- scheduler_demo/api/__init__.py -----------------------------------------

_API_INIT_FINAL = '''\
"""API adapters for scheduler_demo."""

from scheduler_demo.api.adapters import HandlerAdapter
from scheduler_demo.api.routes import Router

__all__ = ["HandlerAdapter", "Router"]
'''


# --- scheduler_demo/api/routes.py -------------------------------------------

_ROUTES_SKELETON = '''\
"""HTTP-style route registry for scheduler_demo (toy)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable


# ---- router ----
'''

_ROUTES_BLOCKS: tuple[tuple[str, str], ...] = (
    (
        "# ---- router ----\n",
        """

Handler = Callable[[dict], dict]


@dataclass
class Router:
    routes: dict[str, Handler] = field(default_factory=dict)

    def register(self, path: str, handler: Handler) -> None:
        self.routes[str(path)] = handler

    def dispatch(self, path: str, payload: dict | None = None) -> dict:
        handler = self.routes.get(str(path))
        if handler is None:
            return {"status": "not_found", "path": path}
        return handler(dict(payload or {}))

    def list_paths(self) -> list[str]:
        return sorted(self.routes.keys())
""",
    ),
)


# --- scheduler_demo/api/adapters.py -----------------------------------------

_ADAPTERS_SKELETON = '''\
"""Convert domain objects to API payloads."""
from __future__ import annotations

from dataclasses import dataclass

from scheduler_demo.domain.task import Task


# ---- handler adapter ----
'''

_ADAPTERS_BLOCKS: tuple[tuple[str, str], ...] = (
    (
        "# ---- handler adapter ----\n",
        """

@dataclass
class HandlerAdapter:
    name: str = "default"

    def to_payload(self, task: Task) -> dict:
        return {
            "id": task.task_id,
            "state": task.state.value,
            "priority": int(task.priority),
            "adapter": self.name,
        }

    def from_payload(self, payload: dict) -> Task:
        return Task.from_dict(payload)
""",
    ),
)


# --- scheduler_demo/util/__init__.py ----------------------------------------

_UTIL_INIT_FINAL = '''\
"""Misc helpers for scheduler_demo."""

from scheduler_demo.util.time_utils import iso_now, monotonic_ms

__all__ = ["iso_now", "monotonic_ms"]
'''


# --- scheduler_demo/util/time_utils.py --------------------------------------

_TIME_SKELETON = '''\
"""Time helpers."""
from __future__ import annotations

import datetime as _dt
import time as _time


# ---- helpers ----
'''

_TIME_BLOCKS: tuple[tuple[str, str], ...] = (
    (
        "# ---- helpers ----\n",
        """

def iso_now() -> str:
    \"\"\"Return the current UTC time formatted as ISO-8601.\"\"\"
    return _dt.datetime.now(tz=_dt.timezone.utc).isoformat()


def monotonic_ms() -> int:
    return int(_time.monotonic() * 1000)
""",
    ),
)


# --- tests/__init__.py ------------------------------------------------------

_TESTS_INIT_FINAL = ""

_TESTS_CONFTEST_SKELETON = '''\
"""Pytest fixtures for the scheduler_demo test suite."""
from __future__ import annotations

import pytest


# ---- fixtures ----
'''

_TESTS_CONFTEST_BLOCKS: tuple[tuple[str, str], ...] = (
    (
        "# ---- fixtures ----\n",
        """

@pytest.fixture
def make_task():
    from scheduler_demo.domain.task import Task

    def _make(**overrides):
        kwargs = {"task_id": "t-1", "payload": "demo"}
        kwargs.update(overrides)
        return Task(**kwargs)

    return _make
""",
    ),
    (
        "    return _make\n",
        """

@pytest.fixture
def make_scheduler():
    from scheduler_demo.config import Config
    from scheduler_demo.services.scheduler import Scheduler

    def _make(**overrides):
        return Scheduler(config=Config(**overrides))

    return _make
""",
    ),
)


# --- tests/test_config.py ---------------------------------------------------

_TC_CONFIG_SKELETON = '''\
"""Tests for scheduler_demo.config."""
from __future__ import annotations

import pytest

from scheduler_demo.config import Config, load_config, merge_configs


# ---- tests ----
'''

_TC_CONFIG_BLOCKS: tuple[tuple[str, str], ...] = (
    (
        "# ---- tests ----\n",
        """

def test_default_config_values():
    cfg = Config()
    assert cfg.max_workers == 4
    assert cfg.retry_limit == 3
    assert cfg.serializer == "json"


def test_with_workers_returns_new_instance():
    cfg = Config()
    new = cfg.with_workers(8)
    assert new.max_workers == 8
    assert cfg.max_workers == 4
""",
    ),
    (
        "def test_with_workers_returns_new_instance():\n    cfg = Config()\n    new = cfg.with_workers(8)\n    assert new.max_workers == 8\n    assert cfg.max_workers == 4\n",
        """

def test_load_config_uses_defaults_for_empty_payload():
    cfg = load_config({})
    assert cfg.max_workers == 4


def test_load_config_overrides():
    cfg = load_config({"max_workers": 16, "serializer": "pickle"})
    assert cfg.max_workers == 16
    assert cfg.serializer == "pickle"


def test_merge_configs_prefers_override():
    base = Config(max_workers=2, serializer="json")
    override = Config(max_workers=8, serializer="json")
    merged = merge_configs(base, override)
    assert merged.max_workers == 8
""",
    ),
    (
        "    assert merged.max_workers == 8\n",
        """

def test_with_serializer_returns_new_instance():
    cfg = Config()
    new = cfg.with_serializer("pickle")
    assert new.serializer == "pickle"
    assert cfg.serializer == "json"


def test_load_config_handles_none_payload():
    cfg = load_config(None)
    assert cfg.max_workers == 4


def test_load_config_coerces_strings_to_ints():
    cfg = load_config({"max_workers": "16"})
    assert cfg.max_workers == 16


def test_load_config_coerces_floats():
    cfg = load_config({"default_timeout_s": "12.5"})
    assert cfg.default_timeout_s == 12.5


def test_config_is_frozen():
    import dataclasses

    cfg = Config()
    with pytest.raises(dataclasses.FrozenInstanceError):
        cfg.max_workers = 99  # type: ignore[misc]


def test_config_equality():
    assert Config() == Config()
    assert Config(max_workers=4) == Config(max_workers=4)
    assert Config(max_workers=4) != Config(max_workers=8)


def test_config_hash_matches_equality():
    one = Config(max_workers=4)
    two = Config(max_workers=4)
    assert hash(one) == hash(two)
""",
    ),
)


# --- tests/test_errors.py ---------------------------------------------------

_TC_ERRORS_SKELETON = '''\
"""Tests for scheduler_demo.errors."""
from __future__ import annotations

import pytest

from scheduler_demo import errors


# ---- tests ----
'''

_TC_ERRORS_BLOCKS: tuple[tuple[str, str], ...] = (
    (
        "# ---- tests ----\n",
        """

def test_scheduler_error_is_base_exception():
    err = errors.SchedulerError("boom")
    assert isinstance(err, Exception)


def test_task_not_found_carries_id():
    err = errors.TaskNotFoundError("abc")
    assert err.task_id == "abc"
    assert "abc" in str(err)


def test_task_already_exists_subclasses_scheduler_error():
    with pytest.raises(errors.SchedulerError):
        raise errors.TaskAlreadyExistsError("x")


def test_invalid_state_carries_attempted():
    err = errors.InvalidTaskStateError("t-1", "running")
    assert err.task_id == "t-1"
    assert err.attempted == "running"
""",
    ),
)


# --- tests/test_task.py -----------------------------------------------------

_TC_TASK_SKELETON = '''\
"""Tests for scheduler_demo.domain.task."""
from __future__ import annotations

import pytest

from scheduler_demo.domain.task import Task, TaskState
from scheduler_demo.errors import InvalidTaskStateError


# ---- tests ----
'''

_TC_TASK_BLOCKS: tuple[tuple[str, str], ...] = (
    (
        "# ---- tests ----\n",
        """

def test_task_default_state_is_pending():
    task = Task(task_id="t")
    assert task.state == TaskState.PENDING


def test_task_round_trip_dict():
    task = Task(task_id="t", payload={"x": 1}, priority=5)
    revived = Task.from_dict(task.to_dict())
    assert revived.task_id == "t"
    assert revived.priority == 5
    assert revived.state == TaskState.PENDING


def test_task_transition_pending_to_running_ok():
    task = Task(task_id="t")
    task.transition(TaskState.RUNNING)
    assert task.state == TaskState.RUNNING


def test_task_transition_pending_to_done_rejected():
    task = Task(task_id="t")
    with pytest.raises(InvalidTaskStateError):
        task.transition(TaskState.DONE)
""",
    ),
    (
        "        task.transition(TaskState.DONE)\n",
        """

def test_task_increment_attempt_returns_new_count():
    task = Task(task_id="t")
    assert task.increment_attempt() == 1
    assert task.increment_attempt() == 2


def test_task_terminal_states_classification():
    assert TaskState.DONE.is_terminal()
    assert TaskState.FAILED.is_terminal()
    assert TaskState.CANCELLED.is_terminal()
    assert not TaskState.PENDING.is_terminal()


def test_task_is_active_when_pending_or_running():
    pending = Task(task_id="p")
    assert pending.is_active()
    pending.transition(TaskState.RUNNING)
    assert pending.is_active()
    pending.transition(TaskState.DONE)
    assert not pending.is_active()
""",
    ),
    (
        "    assert not pending.is_active()\n",
        """

def test_failed_task_can_be_reset_to_pending():
    task = Task(task_id="t")
    task.transition(TaskState.RUNNING)
    task.transition(TaskState.FAILED)
    task.transition(TaskState.PENDING)
    assert task.state == TaskState.PENDING


def test_running_to_cancelled_is_legal():
    task = Task(task_id="t")
    task.transition(TaskState.RUNNING)
    task.transition(TaskState.CANCELLED)
    assert task.state == TaskState.CANCELLED


def test_done_is_dead_end():
    task = Task(task_id="t")
    task.transition(TaskState.RUNNING)
    task.transition(TaskState.DONE)
    with pytest.raises(InvalidTaskStateError):
        task.transition(TaskState.PENDING)


def test_cancelled_is_dead_end():
    task = Task(task_id="t")
    task.transition(TaskState.CANCELLED)
    with pytest.raises(InvalidTaskStateError):
        task.transition(TaskState.RUNNING)


def test_terminal_states_membership():
    terminals = TaskState.terminal_states()
    assert TaskState.DONE in terminals
    assert TaskState.PENDING not in terminals


def test_to_dict_includes_metadata():
    task = Task(task_id="t", metadata={"kind": "demo"})
    assert task.to_dict()["metadata"] == {"kind": "demo"}


def test_from_dict_with_minimal_payload():
    payload = {"task_id": "t"}
    task = Task.from_dict(payload)
    assert task.state == TaskState.PENDING
    assert task.priority == 0
    assert task.metadata == {}


def test_attempts_default_to_zero():
    task = Task(task_id="t")
    assert task.attempts == 0


def test_priority_round_trips_via_dict():
    task = Task(task_id="t", priority=7)
    revived = Task.from_dict(task.to_dict())
    assert revived.priority == 7
""",
    ),
)


# --- tests/test_schedule.py -------------------------------------------------

_TC_SCHEDULE_SKELETON = '''\
"""Tests for scheduler_demo.domain.schedule."""
from __future__ import annotations

from scheduler_demo.domain.schedule import Schedule


# ---- tests ----
'''

_TC_SCHEDULE_BLOCKS: tuple[tuple[str, str], ...] = (
    (
        "# ---- tests ----\n",
        """

def test_add_slot_appends_and_returns_slot():
    schedule = Schedule(name="default")
    slot = schedule.add_slot("a", capacity=2)
    assert slot.slot_id == "a"
    assert schedule.slots == [slot]


def test_remove_slot_returns_true_when_present():
    schedule = Schedule(name="default")
    schedule.add_slot("a")
    assert schedule.remove_slot("a") is True
    assert schedule.slots == []


def test_remove_slot_returns_false_when_absent():
    schedule = Schedule(name="default")
    assert schedule.remove_slot("a") is False


def test_total_capacity_sums_slots():
    schedule = Schedule(name="default")
    schedule.add_slot("a", capacity=2)
    schedule.add_slot("b", capacity=3)
    assert schedule.total_capacity() == 5


def test_find_slot_returns_match_or_none():
    schedule = Schedule(name="default")
    slot = schedule.add_slot("a")
    assert schedule.find_slot("a") is slot
    assert schedule.find_slot("missing") is None
""",
    ),
    (
        "    assert schedule.find_slot(\"missing\") is None\n",
        """

def test_slot_can_accept_load_below_capacity():
    from scheduler_demo.domain.schedule import ScheduleSlot

    slot = ScheduleSlot(slot_id="a", capacity=3)
    assert slot.can_accept(0) is True
    assert slot.can_accept(2) is True
    assert slot.can_accept(3) is False


def test_to_dict_round_trip_shape():
    schedule = Schedule(name="default")
    schedule.add_slot("a", capacity=2, exclusive=True)
    payload = schedule.to_dict()
    assert payload["name"] == "default"
    assert payload["slots"][0] == {"slot_id": "a", "capacity": 2, "exclusive": True}


def test_total_capacity_zero_for_empty_schedule():
    assert Schedule(name="empty").total_capacity() == 0


def test_remove_then_re_add_works():
    schedule = Schedule(name="default")
    schedule.add_slot("a")
    schedule.remove_slot("a")
    schedule.add_slot("a", capacity=5)
    assert schedule.total_capacity() == 5
""",
    ),
)


# --- tests/test_priority.py -------------------------------------------------

_TC_PRIORITY_SKELETON = '''\
"""Tests for scheduler_demo.domain.priority."""
from __future__ import annotations

from scheduler_demo.domain.priority import Priority, compare, highest


# ---- tests ----
'''

_TC_PRIORITY_BLOCKS: tuple[tuple[str, str], ...] = (
    (
        "# ---- tests ----\n",
        """

def test_priority_from_int_buckets():
    assert Priority.from_int(-5) == Priority.LOW
    assert Priority.from_int(0) == Priority.LOW
    assert Priority.from_int(3) == Priority.NORMAL
    assert Priority.from_int(7) == Priority.HIGH
    assert Priority.from_int(99) == Priority.CRITICAL


def test_priority_boost_increments_bucket():
    assert Priority.LOW.boost(amount=5) == Priority.NORMAL
    assert Priority.NORMAL.boost(amount=4) == Priority.HIGH


def test_compare_three_way():
    assert compare(1, 2) == -1
    assert compare(2, 1) == 1
    assert compare(2, 2) == 0


def test_highest_returns_max_or_low():
    assert highest([]) == int(Priority.LOW)
    assert highest([1, 5, 3]) == 5
""",
    ),
    (
        "    assert highest([1, 5, 3]) == 5\n",
        """

def test_priority_int_values():
    assert int(Priority.LOW) == 0
    assert int(Priority.NORMAL) == 5
    assert int(Priority.HIGH) == 9
    assert int(Priority.CRITICAL) == 13


def test_priority_ordering_is_intuitive():
    assert Priority.LOW < Priority.NORMAL < Priority.HIGH < Priority.CRITICAL


def test_boost_is_idempotent_at_critical():
    assert Priority.CRITICAL.boost(amount=10) == Priority.CRITICAL


def test_compare_negative_args():
    assert compare(-5, 0) == -1
    assert compare(0, -5) == 1
    assert compare(-3, -3) == 0


def test_highest_with_mixed_signs():
    assert highest([-3, -1, -10]) == -1
    assert highest([5, -5, 0]) == 5


def test_priority_str_via_int():
    assert int(Priority.from_int(7)) == 9
    assert int(Priority.from_int(2)) == 5
""",
    ),
)


# --- tests/test_scheduler.py ------------------------------------------------

_TC_SCHEDULER_SKELETON = '''\
"""Tests for scheduler_demo.services.scheduler."""
from __future__ import annotations

import pytest

from scheduler_demo.config import Config
from scheduler_demo.domain.task import Task, TaskState
from scheduler_demo.errors import TaskAlreadyExistsError, TaskNotFoundError
from scheduler_demo.services.scheduler import Scheduler


# ---- tests ----
'''

_TC_SCHEDULER_BLOCKS: tuple[tuple[str, str], ...] = (
    (
        "# ---- tests ----\n",
        """

def _scheduler():
    return Scheduler(config=Config(max_workers=2))


def test_submit_then_fetch():
    sched = _scheduler()
    sched.submit(Task(task_id="t", payload="x"))
    assert sched.fetch("t").task_id == "t"


def test_duplicate_submit_raises():
    sched = _scheduler()
    sched.submit(Task(task_id="t"))
    with pytest.raises(TaskAlreadyExistsError):
        sched.submit(Task(task_id="t"))


def test_next_pending_returns_highest_priority_first():
    sched = _scheduler()
    sched.submit(Task(task_id="lo", priority=1))
    sched.submit(Task(task_id="hi", priority=9))
    assert sched.next_pending().task_id == "hi"


def test_fetch_missing_raises():
    sched = _scheduler()
    with pytest.raises(TaskNotFoundError):
        sched.fetch("missing")
""",
    ),
    (
        "        sched.fetch(\"missing\")\n",
        """

def test_boost_raises_priority_and_resorts():
    sched = _scheduler()
    sched.submit(Task(task_id="lo", priority=1))
    sched.submit(Task(task_id="hi", priority=9))
    sched.boost("lo", amount=12)
    assert sched.next_pending().task_id == "lo"


def test_cancel_marks_task_cancelled():
    sched = _scheduler()
    sched.submit(Task(task_id="t"))
    sched.cancel("t")
    assert sched.fetch("t").state == TaskState.CANCELLED


def test_all_tasks_returns_copy():
    sched = _scheduler()
    sched.submit(Task(task_id="a"))
    snapshot = sched.all_tasks()
    sched.submit(Task(task_id="b"))
    assert len(snapshot) == 1
""",
    ),
    (
        "    assert len(snapshot) == 1\n",
        """

def test_next_pending_returns_none_when_all_terminal():
    sched = _scheduler()
    task = Task(task_id="t")
    sched.submit(task)
    sched.cancel("t")
    assert sched.next_pending() is None


def test_submit_orders_lexically_when_priority_ties():
    sched = _scheduler()
    sched.submit(Task(task_id="b", priority=5))
    sched.submit(Task(task_id="a", priority=5))
    assert sched.next_pending().task_id == "a"


def test_boost_idempotent_on_already_critical():
    sched = _scheduler()
    sched.submit(Task(task_id="t", priority=13))
    sched.boost("t", amount=5)
    fetched = sched.fetch("t")
    assert fetched.priority == 13


def test_cancel_unknown_raises_not_found():
    sched = _scheduler()
    with pytest.raises(TaskNotFoundError):
        sched.cancel("missing")


def test_boost_unknown_raises_not_found():
    sched = _scheduler()
    with pytest.raises(TaskNotFoundError):
        sched.boost("missing")


def test_scheduler_starts_empty():
    sched = _scheduler()
    assert sched.all_tasks() == []
    assert sched.next_pending() is None


def test_submit_then_run_state_progression():
    sched = _scheduler()
    task = Task(task_id="t")
    sched.submit(task)
    pending = sched.next_pending()
    assert pending is task
    pending.transition(TaskState.RUNNING)
    assert sched.next_pending() is None
""",
    ),
)


# --- tests/test_executor.py -------------------------------------------------

_TC_EXEC_SKELETON = '''\
"""Tests for scheduler_demo.services.executor."""
from __future__ import annotations

from scheduler_demo.domain.task import Task, TaskState
from scheduler_demo.services.executor import Executor


# ---- tests ----
'''

_TC_EXEC_BLOCKS: tuple[tuple[str, str], ...] = (
    (
        "# ---- tests ----\n",
        """

def test_execute_default_handler_marks_task_done():
    executor = Executor()
    executor.register("default", lambda task: None)
    task = Task(task_id="t")
    executor.run(task)
    assert task.state == TaskState.DONE
    assert executor.has_run("t")


def test_execute_failing_handler_marks_failed():
    executor = Executor()
    def boom(task):
        raise RuntimeError("nope")

    executor.register("default", boom)
    task = Task(task_id="t")
    executor.run(task)
    assert task.state == TaskState.FAILED


def test_unknown_kind_marks_failed():
    executor = Executor()
    task = Task(task_id="t", metadata={"kind": "nope"})
    executor.run(task)
    assert task.state == TaskState.FAILED


def test_reset_clears_history():
    executor = Executor()
    executor.register("default", lambda task: None)
    executor.run(Task(task_id="a"))
    executor.reset()
    assert not executor.has_run("a")
""",
    ),
    (
        "    assert not executor.has_run(\"a\")\n",
        """

def test_register_overwrites_handler():
    executor = Executor()
    calls = {"first": 0, "second": 0}

    def first(task):
        calls["first"] += 1

    def second(task):
        calls["second"] += 1

    executor.register("default", first)
    executor.register("default", second)
    executor.run(Task(task_id="t"))
    assert calls["first"] == 0
    assert calls["second"] == 1


def test_run_routes_by_metadata_kind():
    executor = Executor()
    seen = []
    executor.register("default", lambda task: seen.append(("default", task.task_id)))
    executor.register("special", lambda task: seen.append(("special", task.task_id)))
    executor.run(Task(task_id="d"))
    executor.run(Task(task_id="s", metadata={"kind": "special"}))
    assert seen == [("default", "d"), ("special", "s")]


def test_history_appended_in_order():
    executor = Executor()
    executor.register("default", lambda task: None)
    for tid in ("c", "a", "b"):
        executor.run(Task(task_id=tid))
    assert executor.history == ["c", "a", "b"]


def test_failed_handler_increments_attempts_and_appends_history():
    executor = Executor()

    def boom(task):
        raise RuntimeError("boom")

    executor.register("default", boom)
    task = Task(task_id="t")
    executor.run(task)
    assert task.attempts == 1
    assert executor.has_run("t")


def test_run_does_not_swallow_invalid_state_transition():
    import pytest

    from scheduler_demo.errors import InvalidTaskStateError

    executor = Executor()
    executor.register("default", lambda task: None)
    task = Task(task_id="t")
    task.transition(TaskState.RUNNING)
    with pytest.raises(InvalidTaskStateError):
        executor.run(task)
""",
    ),
)


# --- tests/test_retry.py ----------------------------------------------------

_TC_RETRY_SKELETON = '''\
"""Tests for scheduler_demo.services.retry."""
from __future__ import annotations

from scheduler_demo.domain.task import Task, TaskState
from scheduler_demo.services.retry import RetryPolicy


# ---- tests ----
'''

_TC_RETRY_BLOCKS: tuple[tuple[str, str], ...] = (
    (
        "# ---- tests ----\n",
        """

def _failed_task(attempts: int = 0) -> Task:
    task = Task(task_id="t")
    task.transition(TaskState.RUNNING)
    task.transition(TaskState.FAILED)
    task.attempts = attempts
    return task


def test_should_retry_when_below_max():
    policy = RetryPolicy(max_attempts=3)
    assert policy.should_retry(_failed_task(attempts=1)) is True


def test_should_not_retry_at_max():
    policy = RetryPolicy(max_attempts=3)
    assert policy.should_retry(_failed_task(attempts=3)) is False


def test_next_delay_grows_exponentially():
    policy = RetryPolicy(max_attempts=5, backoff_base_s=1.0)
    assert policy.next_delay_s(_failed_task(attempts=1)) == 1.0
    assert policy.next_delay_s(_failed_task(attempts=2)) == 2.0


def test_reset_returns_failed_to_pending():
    policy = RetryPolicy()
    task = _failed_task(attempts=1)
    policy.reset(task)
    assert task.state == TaskState.PENDING
""",
    ),
)


# --- tests/test_memory_store.py ---------------------------------------------

_TC_MEM_SKELETON = '''\
"""Tests for scheduler_demo.storage.memory_store."""
from __future__ import annotations

import pytest

from scheduler_demo.domain.task import Task
from scheduler_demo.errors import TaskAlreadyExistsError, TaskNotFoundError
from scheduler_demo.storage.memory_store import MemoryStore


# ---- tests ----
'''

_TC_MEM_BLOCKS: tuple[tuple[str, str], ...] = (
    (
        "# ---- tests ----\n",
        """

def test_put_then_fetch_round_trip():
    store = MemoryStore()
    store.put(Task(task_id="t"))
    assert store.fetch("t").task_id == "t"


def test_put_duplicate_raises():
    store = MemoryStore()
    store.put(Task(task_id="t"))
    with pytest.raises(TaskAlreadyExistsError):
        store.put(Task(task_id="t"))


def test_fetch_missing_raises():
    store = MemoryStore()
    with pytest.raises(TaskNotFoundError):
        store.fetch("missing")


def test_remove_returns_task_and_drops_it():
    store = MemoryStore()
    store.put(Task(task_id="t"))
    removed = store.remove("t")
    assert removed.task_id == "t"
    assert "t" not in store


def test_len_reflects_size():
    store = MemoryStore()
    assert len(store) == 0
    store.put(Task(task_id="t"))
    assert len(store) == 1
""",
    ),
    (
        "    assert len(store) == 1\n",
        """

def test_remove_missing_raises():
    store = MemoryStore()
    with pytest.raises(TaskNotFoundError):
        store.remove("missing")


def test_all_returns_inserted_tasks():
    store = MemoryStore()
    for tid in ("a", "b", "c"):
        store.put(Task(task_id=tid))
    ids = sorted(t.task_id for t in store.all())
    assert ids == ["a", "b", "c"]


def test_contains_works_for_string_key():
    store = MemoryStore()
    store.put(Task(task_id="t"))
    assert "t" in store
    assert "missing" not in store


def test_putting_then_removing_round_trip():
    store = MemoryStore()
    task = Task(task_id="t", priority=4)
    store.put(task)
    removed = store.remove("t")
    assert removed.priority == 4
    assert len(store) == 0


def test_independent_stores_do_not_leak():
    one = MemoryStore()
    two = MemoryStore()
    one.put(Task(task_id="t"))
    assert "t" in one
    assert "t" not in two
""",
    ),
)


# --- tests/test_serializer.py -----------------------------------------------

_TC_SER_SKELETON = '''\
"""Tests for scheduler_demo.storage.serializer."""
from __future__ import annotations

import pytest

from scheduler_demo.domain.task import Task
from scheduler_demo.errors import SerializerError
from scheduler_demo.storage.serializer import dump, load, round_trip


# ---- tests ----
'''

_TC_SER_BLOCKS: tuple[tuple[str, str], ...] = (
    (
        "# ---- tests ----\n",
        """

def test_round_trip_preserves_task():
    tasks = [Task(task_id="a"), Task(task_id="b", priority=5)]
    revived = round_trip(tasks)
    assert [t.task_id for t in revived] == ["a", "b"]


def test_dump_is_sorted_keys():
    tasks = [Task(task_id="a", priority=2)]
    payload = dump(tasks)
    assert "\\"task_id\\"" in payload


def test_load_rejects_non_list():
    with pytest.raises(SerializerError):
        load("{}")


def test_load_empty_list():
    assert load("[]") == []
""",
    ),
    (
        "    assert load(\"[]\") == []\n",
        """

def test_dump_handles_empty_list():
    assert dump([]) == "[]"


def test_dump_includes_priority_and_state():
    payload = dump([Task(task_id="t", priority=7)])
    assert "\\"priority\\": 7" in payload
    assert "\\"state\\": \\"pending\\"" in payload


def test_round_trip_preserves_attempts():
    task = Task(task_id="t")
    task.attempts = 4
    revived = round_trip([task])
    assert revived[0].attempts == 4


def test_round_trip_preserves_metadata():
    task = Task(task_id="t", metadata={"kind": "demo"})
    revived = round_trip([task])
    assert revived[0].metadata == {"kind": "demo"}
""",
    ),
)


# --- tests/test_routes.py ---------------------------------------------------

_TC_ROUTES_SKELETON = '''\
"""Tests for scheduler_demo.api.routes."""
from __future__ import annotations

from scheduler_demo.api.routes import Router


# ---- tests ----
'''

_TC_ROUTES_BLOCKS: tuple[tuple[str, str], ...] = (
    (
        "# ---- tests ----\n",
        """

def test_dispatch_unknown_returns_not_found():
    router = Router()
    assert router.dispatch("/missing") == {"status": "not_found", "path": "/missing"}


def test_register_then_dispatch_calls_handler():
    router = Router()
    router.register("/echo", lambda payload: {"echo": payload})
    out = router.dispatch("/echo", {"x": 1})
    assert out == {"echo": {"x": 1}}


def test_list_paths_is_sorted():
    router = Router()
    router.register("/b", lambda p: {})
    router.register("/a", lambda p: {})
    assert router.list_paths() == ["/a", "/b"]
""",
    ),
    (
        "    assert router.list_paths() == [\"/a\", \"/b\"]\n",
        """

def test_register_overwrites_handler():
    router = Router()
    router.register("/x", lambda p: {"v": 1})
    router.register("/x", lambda p: {"v": 2})
    assert router.dispatch("/x") == {"v": 2}


def test_dispatch_with_none_payload_treated_as_empty():
    router = Router()
    router.register("/x", lambda p: {"received": p})
    assert router.dispatch("/x", None) == {"received": {}}


def test_list_paths_is_empty_initially():
    router = Router()
    assert router.list_paths() == []


def test_dispatch_unknown_includes_path():
    router = Router()
    out = router.dispatch("/missing")
    assert out["path"] == "/missing"
    assert out["status"] == "not_found"
""",
    ),
)


# --- tests/test_adapters.py -------------------------------------------------

_TC_ADAPT_SKELETON = '''\
"""Tests for scheduler_demo.api.adapters."""
from __future__ import annotations

from scheduler_demo.api.adapters import HandlerAdapter
from scheduler_demo.domain.task import Task


# ---- tests ----
'''

_TC_ADAPT_BLOCKS: tuple[tuple[str, str], ...] = (
    (
        "# ---- tests ----\n",
        """

def test_to_payload_includes_id_state_priority():
    adapter = HandlerAdapter(name="primary")
    payload = adapter.to_payload(Task(task_id="t", priority=3))
    assert payload["id"] == "t"
    assert payload["priority"] == 3
    assert payload["adapter"] == "primary"


def test_from_payload_round_trips():
    adapter = HandlerAdapter()
    payload = {"task_id": "t", "priority": 4, "state": "pending"}
    revived = adapter.from_payload(payload)
    assert revived.task_id == "t"
    assert revived.priority == 4
""",
    ),
)


# --- tests/test_time_utils.py -----------------------------------------------

_TC_TIME_SKELETON = '''\
"""Tests for scheduler_demo.util.time_utils."""
from __future__ import annotations

from scheduler_demo.util.time_utils import iso_now, monotonic_ms


# ---- tests ----
'''

_TC_TIME_BLOCKS: tuple[tuple[str, str], ...] = (
    (
        "# ---- tests ----\n",
        """

def test_iso_now_is_string():
    value = iso_now()
    assert isinstance(value, str)
    assert "T" in value


def test_monotonic_ms_is_non_negative_integer():
    value = monotonic_ms()
    assert isinstance(value, int)
    assert value >= 0
""",
    ),
)


# --- tests/test_integration.py ----------------------------------------------

_TC_INT_SKELETON = '''\
"""Integration tests across scheduler_demo modules."""
from __future__ import annotations

from scheduler_demo.config import Config
from scheduler_demo.domain.task import Task, TaskState
from scheduler_demo.services.executor import Executor
from scheduler_demo.services.retry import RetryPolicy
from scheduler_demo.services.scheduler import Scheduler
from scheduler_demo.storage.memory_store import MemoryStore
from scheduler_demo.storage.serializer import round_trip


# ---- tests ----
'''

_TC_INT_BLOCKS: tuple[tuple[str, str], ...] = (
    (
        "# ---- tests ----\n",
        """

def test_full_pipeline_succeeds():
    store = MemoryStore()
    sched = Scheduler(config=Config())
    executor = Executor()
    executor.register("default", lambda task: None)

    task = Task(task_id="t-1", priority=5)
    store.put(task)
    sched.submit(task)

    next_task = sched.next_pending()
    assert next_task is not None
    executor.run(next_task)

    assert next_task.state == TaskState.DONE
    assert "t-1" in store


def test_retry_then_succeed():
    sched = Scheduler(config=Config())
    executor = Executor()

    calls = {"count": 0}

    def flaky(task):
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("flaky once")

    executor.register("default", flaky)
    policy = RetryPolicy(max_attempts=3)

    task = Task(task_id="r-1")
    sched.submit(task)

    pending = sched.next_pending()
    assert pending is not None
    executor.run(pending)
    assert pending.state == TaskState.FAILED
    assert policy.should_retry(pending)

    policy.reset(pending)
    executor.run(pending)
    assert pending.state == TaskState.DONE


def test_serializer_round_trip_preserves_priorities():
    tasks = [Task(task_id="a", priority=1), Task(task_id="b", priority=9)]
    revived = round_trip(tasks)
    assert [t.priority for t in revived] == [1, 9]
""",
    ),
    (
        "    assert [t.priority for t in revived] == [1, 9]\n",
        """

def test_full_pipeline_with_store_and_executor():
    store = MemoryStore()
    sched = Scheduler()
    executor = Executor()
    executor.register("default", lambda task: None)

    for task_id, priority in (("low", 1), ("hi", 9), ("mid", 5)):
        task = Task(task_id=task_id, priority=priority)
        store.put(task)
        sched.submit(task)

    expected_order = ["hi", "mid", "low"]
    for expected in expected_order:
        nxt = sched.next_pending()
        assert nxt is not None
        assert nxt.task_id == expected
        executor.run(nxt)
    assert sched.next_pending() is None


def test_executor_history_records_each_task():
    sched = Scheduler()
    executor = Executor()
    executor.register("default", lambda task: None)

    for task_id in ("a", "b", "c"):
        task = Task(task_id=task_id)
        sched.submit(task)
    while True:
        pending = sched.next_pending()
        if pending is None:
            break
        executor.run(pending)
    assert executor.history == ["a", "b", "c"]


def test_cancelled_task_skipped_by_scheduler():
    sched = Scheduler()
    sched.submit(Task(task_id="a"))
    sched.submit(Task(task_id="b"))
    sched.cancel("a")
    nxt = sched.next_pending()
    assert nxt is not None
    assert nxt.task_id == "b"


def test_round_trip_preserves_state():
    task = Task(task_id="t")
    task.transition(TaskState.RUNNING)
    task.transition(TaskState.DONE)
    revived = round_trip([task])
    assert revived[0].state == TaskState.DONE


def test_retry_policy_triggers_only_after_failure():
    policy = RetryPolicy(max_attempts=3)
    pending = Task(task_id="t")
    assert policy.should_retry(pending) is False
""",
    ),
)


# ============================================================================
# Assemble the fixture set
# ============================================================================


SCHEDULER_DEMO_FILES: tuple[FixtureFile, ...] = (
    # Top-level project files
    FixtureFile(
        relative_path=".gitignore",
        final=_GITIGNORE,
        skeleton=_GITIGNORE,
        patches=(),
        is_init=True,
    ),
    FixtureFile(
        relative_path="pyproject.toml",
        final=_PYPROJECT,
        skeleton=_PYPROJECT,
        patches=(),
        is_init=True,
    ),
    _build(
        relative_path="conftest.py",
        skeleton=_ROOT_CONFTEST_SKELETON,
        blocks=_ROOT_CONFTEST_BLOCKS,
    ),
    # Source package
    _init("scheduler_demo/__init__.py", _PKG_INIT_FINAL),
    _build(
        relative_path="scheduler_demo/config.py",
        skeleton=_CONFIG_SKELETON,
        blocks=_CONFIG_BLOCKS,
    ),
    _build(
        relative_path="scheduler_demo/errors.py",
        skeleton=_ERRORS_SKELETON,
        blocks=_ERRORS_BLOCKS,
    ),
    # domain/
    _init("scheduler_demo/domain/__init__.py", _DOMAIN_INIT_FINAL),
    _build(
        relative_path="scheduler_demo/domain/task.py",
        skeleton=_TASK_SKELETON,
        blocks=_TASK_BLOCKS,
    ),
    _build(
        relative_path="scheduler_demo/domain/schedule.py",
        skeleton=_SCHEDULE_SKELETON,
        blocks=_SCHEDULE_BLOCKS,
    ),
    _build(
        relative_path="scheduler_demo/domain/priority.py",
        skeleton=_PRIORITY_SKELETON,
        blocks=_PRIORITY_BLOCKS,
    ),
    # services/
    _init("scheduler_demo/services/__init__.py", _SERVICES_INIT_FINAL),
    _build(
        relative_path="scheduler_demo/services/scheduler.py",
        skeleton=_SCHEDULER_SKELETON,
        blocks=_SCHEDULER_BLOCKS,
    ),
    _build(
        relative_path="scheduler_demo/services/executor.py",
        skeleton=_EXECUTOR_SKELETON,
        blocks=_EXECUTOR_BLOCKS,
    ),
    _build(
        relative_path="scheduler_demo/services/retry.py",
        skeleton=_RETRY_SKELETON,
        blocks=_RETRY_BLOCKS,
    ),
    # storage/
    _init("scheduler_demo/storage/__init__.py", _STORAGE_INIT_FINAL),
    _build(
        relative_path="scheduler_demo/storage/memory_store.py",
        skeleton=_MEMORY_SKELETON,
        blocks=_MEMORY_BLOCKS,
    ),
    _build(
        relative_path="scheduler_demo/storage/serializer.py",
        skeleton=_SERIALIZER_SKELETON,
        blocks=_SERIALIZER_BLOCKS,
    ),
    # api/
    _init("scheduler_demo/api/__init__.py", _API_INIT_FINAL),
    _build(
        relative_path="scheduler_demo/api/routes.py",
        skeleton=_ROUTES_SKELETON,
        blocks=_ROUTES_BLOCKS,
    ),
    _build(
        relative_path="scheduler_demo/api/adapters.py",
        skeleton=_ADAPTERS_SKELETON,
        blocks=_ADAPTERS_BLOCKS,
    ),
    # util/
    _init("scheduler_demo/util/__init__.py", _UTIL_INIT_FINAL),
    _build(
        relative_path="scheduler_demo/util/time_utils.py",
        skeleton=_TIME_SKELETON,
        blocks=_TIME_BLOCKS,
    ),
    # tests/
    _init("tests/__init__.py", _TESTS_INIT_FINAL),
    _build(
        relative_path="tests/conftest.py",
        skeleton=_TESTS_CONFTEST_SKELETON,
        blocks=_TESTS_CONFTEST_BLOCKS,
    ),
    _build(
        relative_path="tests/test_config.py",
        skeleton=_TC_CONFIG_SKELETON,
        blocks=_TC_CONFIG_BLOCKS,
    ),
    _build(
        relative_path="tests/test_errors.py",
        skeleton=_TC_ERRORS_SKELETON,
        blocks=_TC_ERRORS_BLOCKS,
    ),
    _build(
        relative_path="tests/test_task.py",
        skeleton=_TC_TASK_SKELETON,
        blocks=_TC_TASK_BLOCKS,
    ),
    _build(
        relative_path="tests/test_schedule.py",
        skeleton=_TC_SCHEDULE_SKELETON,
        blocks=_TC_SCHEDULE_BLOCKS,
    ),
    _build(
        relative_path="tests/test_priority.py",
        skeleton=_TC_PRIORITY_SKELETON,
        blocks=_TC_PRIORITY_BLOCKS,
    ),
    _build(
        relative_path="tests/test_scheduler.py",
        skeleton=_TC_SCHEDULER_SKELETON,
        blocks=_TC_SCHEDULER_BLOCKS,
    ),
    _build(
        relative_path="tests/test_executor.py",
        skeleton=_TC_EXEC_SKELETON,
        blocks=_TC_EXEC_BLOCKS,
    ),
    _build(
        relative_path="tests/test_retry.py",
        skeleton=_TC_RETRY_SKELETON,
        blocks=_TC_RETRY_BLOCKS,
    ),
    _build(
        relative_path="tests/test_memory_store.py",
        skeleton=_TC_MEM_SKELETON,
        blocks=_TC_MEM_BLOCKS,
    ),
    _build(
        relative_path="tests/test_serializer.py",
        skeleton=_TC_SER_SKELETON,
        blocks=_TC_SER_BLOCKS,
    ),
    _build(
        relative_path="tests/test_routes.py",
        skeleton=_TC_ROUTES_SKELETON,
        blocks=_TC_ROUTES_BLOCKS,
    ),
    _build(
        relative_path="tests/test_adapters.py",
        skeleton=_TC_ADAPT_SKELETON,
        blocks=_TC_ADAPT_BLOCKS,
    ),
    _build(
        relative_path="tests/test_time_utils.py",
        skeleton=_TC_TIME_SKELETON,
        blocks=_TC_TIME_BLOCKS,
    ),
    _build(
        relative_path="tests/test_integration.py",
        skeleton=_TC_INT_SKELETON,
        blocks=_TC_INT_BLOCKS,
    ),
)


# Smoke variant uses 6 paths only (per plan §11). conftest paths are required
# regardless of variant.
SMOKE_FILE_PATHS: frozenset[str] = frozenset(
    {
        # Project root.
        ".gitignore",
        "pyproject.toml",
        "conftest.py",
        # scheduler_demo top level. The package __init__ imports config +
        # errors, so both must be present.
        "scheduler_demo/__init__.py",
        "scheduler_demo/config.py",
        "scheduler_demo/errors.py",
        # domain/ — its __init__ imports priority, schedule, task transitively.
        "scheduler_demo/domain/__init__.py",
        "scheduler_demo/domain/task.py",
        "scheduler_demo/domain/priority.py",
        "scheduler_demo/domain/schedule.py",
        # services/ — its __init__ imports executor, retry, scheduler.
        "scheduler_demo/services/__init__.py",
        "scheduler_demo/services/scheduler.py",
        "scheduler_demo/services/executor.py",
        "scheduler_demo/services/retry.py",
        # tests/ — single test_task module exercises the smoke surface.
        "tests/__init__.py",
        "tests/conftest.py",
        "tests/test_task.py",
    }
)


__all__ = [
    "FixtureFile",
    "Patch",
    "SCHEDULER_DEMO_FILES",
    "SMOKE_FILE_PATHS",
]
