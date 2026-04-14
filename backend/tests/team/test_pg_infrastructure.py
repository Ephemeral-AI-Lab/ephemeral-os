"""Tests for PostgreSQL infrastructure components.

Tests ltree_utils, ORM models, and DispatcherStore structure.
Integration tests with a real PG instance are separate — these run
without a database.
"""

from __future__ import annotations

import asyncio
import re

# ---------------------------------------------------------------------------
# ltree_utils
# ---------------------------------------------------------------------------

from team.persistence.ltree_utils import path_to_ltree, _escape_char


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
        for label in result.split('.'):
            assert re.match(r'^[a-zA-Z0-9_]+$', label), f"Unsafe label: {label}"


class TestEscapeChar:
    def test_dot(self):
        assert _escape_char('.') == 'D'

    def test_hyphen(self):
        assert _escape_char('-') == 'H'

    def test_at_sign(self):
        assert _escape_char('@') == 'X40'

    def test_space(self):
        assert _escape_char(' ') == 'X20'


# ---------------------------------------------------------------------------
# ORM models — structure checks
# ---------------------------------------------------------------------------

from team.persistence.task_record import TaskRecord


class TestTaskRecord:
    def test_tablename(self):
        assert TaskRecord.__tablename__ == "tasks"

    def test_composite_pk(self):
        pk_cols = {c.name for c in TaskRecord.__table__.primary_key.columns}
        assert pk_cols == {"id", "team_run_id"}

    def test_explicit_status(self):
        r = TaskRecord(id="t1", team_run_id="r1", agent_name="dev", task="do stuff", status="pending")
        assert r.status == "pending"

    def test_explicit_deps(self):
        r = TaskRecord(id="t1", team_run_id="r1", agent_name="dev", task="x", deps=["a"])
        assert r.deps == ["a"]


# ---------------------------------------------------------------------------
# TaskCenter — structure check (no DB)
# ---------------------------------------------------------------------------

from team.task_center import TaskCenter


class TestTaskCenterStructure:
    def test_has_required_methods(self):
        """Verify the unified TaskCenter API has all required methods."""
        assert callable(getattr(TaskCenter, 'mark_running', None))
        assert callable(getattr(TaskCenter, 'insert_plan', None))
        assert callable(getattr(TaskCenter, 'get_task', None))
        assert callable(getattr(TaskCenter, 'all_terminal', None))
        assert callable(getattr(TaskCenter, 'cascade_cancel_recursive', None))
        assert callable(getattr(TaskCenter, 'recover_running', None))
        assert callable(getattr(TaskCenter, 'fail', None))
        assert callable(getattr(TaskCenter, 'cancel_all_pending', None))
        assert callable(getattr(TaskCenter, 'cancel_all_running', None))
        assert callable(getattr(TaskCenter, 'request_replan', None))
        assert callable(getattr(TaskCenter, 'get_adjacency', None))
        assert callable(getattr(TaskCenter, 'get_statuses', None))
        # Unified: notes + context
        assert callable(getattr(TaskCenter, 'post', None))
        assert callable(getattr(TaskCenter, 'read', None))
        assert callable(getattr(TaskCenter, 'context_for', None))
        # Orchestration
        assert callable(getattr(TaskCenter, 'complete_task', None))
        assert callable(getattr(TaskCenter, 'apply_replan', None))


from team.runtime.dispatch_queue import DispatchQueue


class TestDispatchQueueStructure:
    def test_has_pop_ready(self):
        assert callable(getattr(DispatchQueue, 'pop_ready', None))


class _FakeCascadeResult:
    def fetchall(self):
        return []


class _FakeCascadeSession:
    def __init__(self) -> None:
        self.statements: list[str] = []

    async def execute(self, statement, params):
        del params
        self.statements.append(str(statement))
        return _FakeCascadeResult()

    async def commit(self) -> None:
        return None


class _FakeSessionFactory:
    def __init__(self, session: _FakeCascadeSession) -> None:
        self._session = session

    def __call__(self):
        session = self._session

        class _Ctx:
            async def __aenter__(self_inner):
                return session

            async def __aexit__(self_inner, exc_type, exc, tb):
                del exc_type, exc, tb
                return False

        return _Ctx()


def test_cascade_cancel_recursive_seeds_root_then_walks_dependents():
    session = _FakeCascadeSession()
    from team.task_center import TaskCenter
    from team.models import BudgetConfig, BudgetState
    tc = TaskCenter(
        session_factory=_FakeSessionFactory(session),
        team_run_id="run-1",
        budgets=BudgetConfig(),
        budget_state=BudgetState(),
    )

    asyncio.run(tc.cascade_cancel_recursive("task-1"))

    sql = session.statements[0]
    assert "WITH RECURSIVE dep_chain AS" in sql
    assert "AND id=:tid" in sql
    assert "dc.id = ANY(t.deps)" in sql
    assert "t.parent_id = dc.id" in sql
    assert "WHERE id != :tid" in sql
