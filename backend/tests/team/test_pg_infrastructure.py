"""Tests for Section 14 PostgreSQL infrastructure components.

Tests ltree_utils, partitions validation, ORM models, NoteStore null fallback,
and DispatcherStore structure. Integration tests with a real PG instance are
separate — these run without a database.
"""

from __future__ import annotations

import re

import pytest

# ---------------------------------------------------------------------------
# ltree_utils
# ---------------------------------------------------------------------------

from team.persistence.ltree_utils import path_to_ltree, _escape_char


class TestPathToLtree:
    """Section 14.5 spec compliance."""

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
# partitions — validation only (no DB)
# ---------------------------------------------------------------------------

from team.persistence.partitions import _partition_suffix, _VALID_RUN_ID


class TestPartitions:
    def test_valid_run_id_pattern(self):
        assert _VALID_RUN_ID.match("my-run_123")
        assert _VALID_RUN_ID.match("abc")
        assert not _VALID_RUN_ID.match("has spaces")
        assert not _VALID_RUN_ID.match("has;semicolons")
        assert not _VALID_RUN_ID.match("")

    def test_suffix_deterministic(self):
        s1 = _partition_suffix("run-42")
        s2 = _partition_suffix("run-42")
        assert s1 == s2

    def test_suffix_is_hex(self):
        s = _partition_suffix("test")
        assert re.match(r'^[0-9a-f]{12}$', s)

    def test_different_runs_different_suffixes(self):
        assert _partition_suffix("run-1") != _partition_suffix("run-2")


# ---------------------------------------------------------------------------
# ORM models — structure checks
# ---------------------------------------------------------------------------

from team.persistence.task_record import TaskRecord
from team.persistence.task_note_record import TaskNoteRecord


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


class TestTaskNoteRecord:
    def test_tablename(self):
        assert TaskNoteRecord.__tablename__ == "task_notes"

    def test_composite_pk(self):
        pk_cols = {c.name for c in TaskNoteRecord.__table__.primary_key.columns}
        assert pk_cols == {"id", "team_run_id"}


# ---------------------------------------------------------------------------
# NullNoteStore — async no-op fallback
# ---------------------------------------------------------------------------

from team.persistence.note_store import NullNoteStore


class TestNullNoteStore:
    def test_not_initialized(self):
        store = NullNoteStore()
        assert store.initialized is False

    @pytest.mark.asyncio
    async def test_insert_is_noop(self):
        store = NullNoteStore()
        await store.insert(None)

    @pytest.mark.asyncio
    async def test_query_returns_empty(self):
        store = NullNoteStore()
        result = await store.query_by_task_ids("run", ["t1"])
        assert result == []

    @pytest.mark.asyncio
    async def test_search_returns_empty(self):
        store = NullNoteStore()
        result = await store.search_fts("run", "query")
        assert result == []


# ---------------------------------------------------------------------------
# DispatcherStore — structure check (no DB)
# ---------------------------------------------------------------------------

from team.runtime.dispatcher_store import DispatcherStore


class TestDispatcherStoreStructure:
    def test_has_required_methods(self):
        """Verify the public API matches Section 14.6 spec."""
        assert callable(getattr(DispatcherStore, 'pop_ready', None))
        assert callable(getattr(DispatcherStore, 'mark_running', None))
        assert callable(getattr(DispatcherStore, 'mark_done', None))
        assert callable(getattr(DispatcherStore, 'insert_plan', None))
        assert callable(getattr(DispatcherStore, 'mark_failed', None))
        assert callable(getattr(DispatcherStore, 'mark_cancelled', None))
        assert callable(getattr(DispatcherStore, 'get_task', None))
        assert callable(getattr(DispatcherStore, 'all_terminal', None))
        assert callable(getattr(DispatcherStore, 'cascade_cancel_recursive', None))
        assert callable(getattr(DispatcherStore, 'recover_running', None))
        # Full mutation ops
        assert callable(getattr(DispatcherStore, 'fail_task', None))
        assert callable(getattr(DispatcherStore, 'retry_task', None))
        assert callable(getattr(DispatcherStore, 'cancel_all_pending', None))
        assert callable(getattr(DispatcherStore, 'cancel_all_running', None))
        assert callable(getattr(DispatcherStore, 'request_replan', None))
        assert callable(getattr(DispatcherStore, 'get_adjacency', None))
        assert callable(getattr(DispatcherStore, 'get_statuses', None))
