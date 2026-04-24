"""Tests for PostgreSQL infrastructure components.

Tests ltree_utils, ORM models, and DispatcherStore structure.
Integration tests with a real PG instance are separate — these run
without a database.
"""

from __future__ import annotations

import asyncio
import re

from sqlalchemy import Text

from team.core.models import BudgetConfig, BudgetState, TERMINAL_STATUSES, Task, TaskStatus
from team.persistence.ltree_utils import _escape_char, path_to_ltree
from team.persistence import tasks_sql as task_queries
from team.persistence.tasks_sql import TaskRecord
from team.runtime.task_queue import TaskQueue
from team.task_center import TaskCenter


def _spec(goal: str) -> dict[str, str]:
    return {
        "goal": goal,
        "detail": f"Detail for {goal}",
        "acceptance_criteria": f"Acceptance for {goal}",
    }


# ---------------------------------------------------------------------------
# ltree_utils
# ---------------------------------------------------------------------------


class TestPathToLtree:
    def test_simple_directory(self):
        assert path_to_ltree("src/auth/") == "src.auth"

    def test_file_with_extension(self):
        assert path_to_ltree("src/auth/session.py") == "src.auth.sessionDpy"

    def test_init_file(self):
        assert path_to_ltree("src/auth/__init__.py") == "src.auth.__init__Dpy"

    def test_dotted_filename(self):
        assert path_to_ltree("src/payment/utils.v2.py") == "src.payment.utilsDv2Dpy"

    def test_hyphenated_module(self):
        assert path_to_ltree("src/my-module/foo.py") == "src.myHmodule.fooDpy"

    def test_underscore_module(self):
        assert path_to_ltree("src/my_module/foo.py") == "src.my_module.fooDpy"

    def test_leading_slash_stripped(self):
        assert path_to_ltree("/leading/slash") == "leading.slash"

    def test_trailing_slash_stripped(self):
        assert path_to_ltree("trailing/slash/") == "trailing.slash"

    def test_no_collision_hyphen_vs_underscore(self):
        """Hyphen and underscore must produce different labels."""
        assert path_to_ltree("my-mod") != path_to_ltree("my_mod")

    def test_empty_components_dropped(self):
        assert path_to_ltree("a//b") == "a.b"

    def test_labels_are_ltree_safe(self):
        """All labels must match [a-zA-Z0-9_]+."""
        result = path_to_ltree("src/some.weird-file@v2.py")
        for label in result.split("."):
            assert re.match(r"^[a-zA-Z0-9_]+$", label), f"Unsafe label: {label}"


class TestEscapeChar:
    def test_dot(self):
        assert _escape_char(".") == "D"

    def test_hyphen(self):
        assert _escape_char("-") == "H"

    def test_at_sign(self):
        assert _escape_char("@") == "X40"

    def test_space(self):
        assert _escape_char(" ") == "X20"


# ---------------------------------------------------------------------------
# ORM models — structure checks
# ---------------------------------------------------------------------------


class TestTaskRecord:
    def test_tablename(self):
        assert TaskRecord.__tablename__ == "tasks"

    def test_composite_pk(self):
        pk_cols = {c.name for c in TaskRecord.__table__.primary_key.columns}
        assert pk_cols == {"id", "team_run_id"}

    def test_explicit_status(self):
        r = TaskRecord(
            id="t1", team_run_id="r1", agent_name="dev", spec=_spec("do stuff"), status="pending"
        )
        assert r.status == "pending"

    def test_explicit_deps(self):
        r = TaskRecord(id="t1", team_run_id="r1", agent_name="dev", spec=_spec("x"), deps=["a"])
        assert r.deps == ["a"]

    def test_status_column_is_unbounded_text(self):
        assert isinstance(TaskRecord.status.type, Text)
        assert TaskRecord.status.type.length is None
        assert {status.value for status in TaskStatus} == {
            "pending",
            "ready",
            "running",
            "expanded",
            "request_replan",
            "done",
            "failed",
            "cancelled",
        }

# ---------------------------------------------------------------------------
# TaskCenter — structure check (no DB)
# ---------------------------------------------------------------------------


class TestTaskCenterStructure:
    def test_has_required_methods(self):
        """Facade surface: add_task + manager property accessors."""
        assert callable(getattr(TaskCenter, "add_task", None))
        assert callable(getattr(TaskCenter, "get_task", None))
        assert callable(getattr(TaskCenter, "emit_event", None))
        # Manager accessors exposed as properties
        assert isinstance(getattr(TaskCenter, "notes", None), property)
        assert isinstance(getattr(TaskCenter, "store", None), property)
        assert isinstance(getattr(TaskCenter, "budget", None), property)
        assert isinstance(getattr(TaskCenter, "expander", None), property)
        assert isinstance(getattr(TaskCenter, "context", None), property)


class TestTaskQueueStructure:
    def test_has_required_surface(self):
        assert callable(getattr(TaskQueue, "enqueue", None))
        assert callable(getattr(TaskQueue, "start", None))
        assert callable(getattr(TaskQueue, "drain_and_stop", None))


class _FakeCountResult:
    def scalar(self):
        return 0


class _FakeCountSession:
    def __init__(self) -> None:
        self.statement = None

    async def execute(self, statement, *args, **kwargs):
        del args, kwargs
        self.statement = statement
        return _FakeCountResult()


def test_count_non_terminal_excludes_all_terminal_statuses():
    session = _FakeCountSession()

    asyncio.run(task_queries.count_non_terminal(session, "run-1"))

    params = session.statement.compile().params
    assert set(params["status_1"]) == {status.value for status in TERMINAL_STATUSES}
