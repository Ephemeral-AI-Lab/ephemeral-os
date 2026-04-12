"""Unit tests for team.task_center.TaskCenter."""

from __future__ import annotations

import time

import pytest

from team.models import Note, Task, TaskStatus
from team.task_center import TaskCenter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _note(
    id_: str,
    task_id: str,
    content: str = "some content",
    *,
    agent_name: str = "developer",
    timestamp: float | None = None,
    scope_paths: list[str] | None = None,
    parent_note_id: str | None = None,
) -> Note:
    return Note(
        id=id_,
        task_id=task_id,
        agent_name=agent_name,
        content=content,
        timestamp=timestamp if timestamp is not None else time.time(),
        scope_paths=scope_paths or [],
        parent_note_id=parent_note_id,
    )


def _task(
    id_: str,
    task: str = "do work",
    deps: list[str] | None = None,
    scope_paths: list[str] | None = None,
    parent_id: str | None = None,
) -> Task:
    return Task(
        id=id_,
        team_run_id="run-1",
        agent_name="developer",
        status=TaskStatus.PENDING,
        task=task,
        deps=deps or [],
        scope_paths=scope_paths or [],
        parent_id=parent_id,
    )


# ---------------------------------------------------------------------------
# Basic post / read
# ---------------------------------------------------------------------------


def test_empty_task_center_returns_empty_reads():
    tc = TaskCenter()
    assert tc.read() == []


def test_post_appends_notes():
    tc = TaskCenter()
    n1 = _note("n1", "task-1", "hello")
    n2 = _note("n2", "task-2", "world")
    tc.post(n1)
    tc.post(n2)
    notes = tc.read()
    assert len(notes) == 2
    assert notes[0].id == "n1"
    assert notes[1].id == "n2"


# ---------------------------------------------------------------------------
# Filtering: authors
# ---------------------------------------------------------------------------


def test_read_filters_by_task_id():
    tc = TaskCenter()
    tc.post(_note("n1", "task-A"))
    tc.post(_note("n2", "task-B"))
    tc.post(_note("n3", "task-A"))

    results = tc.read(authors=["task-A"])
    assert len(results) == 2
    assert all(n.task_id == "task-A" for n in results)


def test_read_authors_multiple():
    tc = TaskCenter()
    tc.post(_note("n1", "task-A"))
    tc.post(_note("n2", "task-B"))
    tc.post(_note("n3", "task-C"))

    results = tc.read(authors=["task-A", "task-C"])
    assert {n.task_id for n in results} == {"task-A", "task-C"}


def test_read_authors_no_match_returns_empty():
    tc = TaskCenter()
    tc.post(_note("n1", "task-A"))
    assert tc.read(authors=["task-Z"]) == []


# ---------------------------------------------------------------------------
# Filtering: scope_paths (prefix matching)
# ---------------------------------------------------------------------------


def test_read_scope_paths_prefix_match():
    tc = TaskCenter()
    # note scoped to a file inside src/auth/
    tc.post(_note("n1", "task-1", scope_paths=["src/auth/session.py"]))
    # note scoped elsewhere
    tc.post(_note("n2", "task-2", scope_paths=["src/billing/invoice.py"]))

    results = tc.read(scope_paths=["src/auth"])
    assert len(results) == 1
    assert results[0].id == "n1"


def test_read_scope_paths_exact_match():
    tc = TaskCenter()
    tc.post(_note("n1", "task-1", scope_paths=["src/auth"]))
    results = tc.read(scope_paths=["src/auth"])
    assert len(results) == 1


def test_read_scope_paths_no_scope_on_note_excludes_note():
    tc = TaskCenter()
    # note with no scope_paths should not match a scope query
    tc.post(_note("n1", "task-1"))
    results = tc.read(scope_paths=["src/auth"])
    assert results == []


def test_read_scope_paths_trailing_slash_stripped():
    tc = TaskCenter()
    tc.post(_note("n1", "task-1", scope_paths=["src/auth/session.py"]))
    # query with trailing slash should still match
    results = tc.read(scope_paths=["src/auth/"])
    assert len(results) == 1


# ---------------------------------------------------------------------------
# Filtering: since
# ---------------------------------------------------------------------------


def test_read_since_filters_by_timestamp():
    tc = TaskCenter()
    tc.post(_note("n1", "t1", timestamp=100.0))
    tc.post(_note("n2", "t2", timestamp=200.0))
    tc.post(_note("n3", "t3", timestamp=300.0))

    results = tc.read(since=200.0)
    assert len(results) == 2
    assert {n.id for n in results} == {"n2", "n3"}


def test_read_since_none_returns_all():
    tc = TaskCenter()
    tc.post(_note("n1", "t1", timestamp=100.0))
    tc.post(_note("n2", "t2", timestamp=200.0))
    assert len(tc.read(since=None)) == 2


# ---------------------------------------------------------------------------
# Filtering: limit
# ---------------------------------------------------------------------------


def test_read_limit_returns_last_n():
    tc = TaskCenter()
    for i in range(5):
        tc.post(_note(f"n{i}", f"t{i}"))

    results = tc.read(limit=3)
    assert len(results) == 3
    assert results[0].id == "n2"
    assert results[-1].id == "n4"


def test_read_limit_larger_than_total_returns_all():
    tc = TaskCenter()
    tc.post(_note("n1", "t1"))
    results = tc.read(limit=100)
    assert len(results) == 1


# ---------------------------------------------------------------------------
# Combined filters
# ---------------------------------------------------------------------------


def test_read_combined_authors_and_since():
    tc = TaskCenter()
    tc.post(_note("n1", "task-A", timestamp=100.0))
    tc.post(_note("n2", "task-A", timestamp=300.0))
    tc.post(_note("n3", "task-B", timestamp=300.0))

    results = tc.read(authors=["task-A"], since=200.0)
    assert len(results) == 1
    assert results[0].id == "n2"


def test_read_combined_scope_and_limit():
    tc = TaskCenter()
    tc.post(_note("n1", "t1", scope_paths=["src/auth/a.py"]))
    tc.post(_note("n2", "t2", scope_paths=["src/auth/b.py"]))
    tc.post(_note("n3", "t3", scope_paths=["src/auth/c.py"]))

    results = tc.read(scope_paths=["src/auth"], limit=2)
    assert len(results) == 2
    assert results[0].id == "n2"
    assert results[1].id == "n3"


# ---------------------------------------------------------------------------
# context_for
# ---------------------------------------------------------------------------


def test_context_for_always_includes_task_section():
    tc = TaskCenter()
    task = _task("work-1", task="implement login flow")
    ctx = tc.context_for(task)
    assert "## Your task" in ctx
    assert "implement login flow" in ctx


def test_context_for_includes_scope_paths_when_present():
    tc = TaskCenter()
    task = _task("work-1", task="do auth", scope_paths=["src/auth/"])
    ctx = tc.context_for(task)
    assert "Scope:" in ctx
    assert "src/auth/" in ctx


def test_context_for_no_scope_paths_omits_scope_line():
    tc = TaskCenter()
    task = _task("work-1", task="general work")
    ctx = tc.context_for(task)
    assert "Scope:" not in ctx


def test_context_for_includes_dep_notes_when_deps_exist():
    tc = TaskCenter()
    # dep task posted a note
    tc.post(_note("n1", "dep-task", "dependency output", agent_name="developer"))
    task = _task("work-1", task="build on dep", deps=["dep-task"])
    ctx = tc.context_for(task)
    assert "Context from dependencies" in ctx
    assert "dependency output" in ctx


def test_context_for_dep_notes_absent_when_no_deps():
    tc = TaskCenter()
    tc.post(_note("n1", "unrelated", "some output"))
    task = _task("work-1", task="standalone work")
    ctx = tc.context_for(task)
    assert "Context from dependencies" not in ctx


def test_context_for_includes_parent_notes_when_parent_id_matches():
    tc = TaskCenter()
    # parent task posted a note
    tc.post(_note("n1", "parent-task", "parent reasoning", agent_name="team_planner"))
    task = _task("work-1", task="child task", parent_id="parent-task")
    ctx = tc.context_for(task)
    assert "Parent context" in ctx
    assert "parent reasoning" in ctx


def test_context_for_no_parent_notes_when_parent_id_is_none():
    tc = TaskCenter()
    tc.post(_note("n1", "some-task", "context"))
    task = _task("work-1", task="root level task")
    ctx = tc.context_for(task)
    assert "Parent context" not in ctx


def test_context_for_respects_max_context_bytes():
    tc = TaskCenter()
    # Post a very large dep note
    big_content = "x" * 100_000
    tc.post(_note("n1", "dep-task", big_content, agent_name="developer"))
    task = _task("work-1", task="build on dep", deps=["dep-task"])

    # With a tight budget, dep context should be truncated
    ctx = tc.context_for(task, max_context_bytes=500)
    assert "## Your task" in ctx
    # The context should be well under the original size
    assert len(ctx.encode()) < 100_000


def test_context_for_task_section_never_trimmed():
    tc = TaskCenter()
    big_content = "z" * 200_000
    tc.post(_note("n1", "dep-task", big_content))
    task = _task("work-1", task="important task description", deps=["dep-task"])
    ctx = tc.context_for(task, max_context_bytes=100)
    # Task section is priority 1 and never trimmed
    assert "important task description" in ctx


# ---------------------------------------------------------------------------
# snapshot / restore
# ---------------------------------------------------------------------------


def test_snapshot_returns_copy_of_notes():
    tc = TaskCenter()
    tc.post(_note("n1", "t1"))
    tc.post(_note("n2", "t2"))

    snap = tc.snapshot()
    assert len(snap) == 2
    assert snap is not tc._notes  # must be a copy


def test_snapshot_copy_is_independent():
    tc = TaskCenter()
    tc.post(_note("n1", "t1"))
    snap = tc.snapshot()
    tc.post(_note("n2", "t2"))
    # snapshot should not reflect new posts
    assert len(snap) == 1
    assert len(tc.read()) == 2


def test_restore_replaces_notes():
    tc = TaskCenter()
    tc.post(_note("n1", "t1"))
    tc.post(_note("n2", "t2"))

    backup = tc.snapshot()
    # Post more notes
    tc.post(_note("n3", "t3"))
    assert len(tc.read()) == 3

    tc.restore(backup)
    assert len(tc.read()) == 2
    assert tc.read()[0].id == "n1"
    assert tc.read()[1].id == "n2"


def test_restore_empty_list_clears_notes():
    tc = TaskCenter()
    tc.post(_note("n1", "t1"))
    tc.restore([])
    assert tc.read() == []


# ---------------------------------------------------------------------------
# TaskCenter initialization
# ---------------------------------------------------------------------------


def test_task_center_stores_goal_and_user_request():
    tc = TaskCenter(goal="build feature", user_request="please add auth")
    assert tc.goal == "build feature"
    assert tc.user_request == "please add auth"
