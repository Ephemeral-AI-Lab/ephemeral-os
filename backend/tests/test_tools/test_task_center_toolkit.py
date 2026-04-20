"""Tests for tools.task_center.toolkit and freshness helpers."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from tools.task_center.toolkit import (
    ReadTaskGraphTool,
    ReadTaskNoteTool,
    SubmitTaskNoteTool,
    TaskCenterChangedSinceTool,
)
from tools.core.base import ToolExecutionContext
from team.models import Task, TaskStatus


def _ctx(metadata=None) -> ToolExecutionContext:
    return ToolExecutionContext(cwd=Path("/tmp"), metadata=metadata or {})


def _task(
    task_id: str,
    *,
    parent_id: str | None,
    status: TaskStatus = TaskStatus.READY,
    agent: str = "developer",
    description: str = "",
    deps: list[str] | None = None,
    scope_paths: list[str] | None = None,
    failure_reason: str | None = None,
) -> Task:
    return Task(
        id=task_id,
        team_run_id="run-1",
        agent_name=agent,
        status=status,
        objective=f"Objective for {task_id}",
        description=description,
        deps=list(deps or []),
        scope_paths=list(scope_paths or []),
        parent_id=parent_id,
        failure_reason=failure_reason,
    )


@pytest.mark.asyncio
async def test_submit_task_note_returns_structured_note_output():
    class _Notes:
        def __init__(self) -> None:
            self.posted = []

        async def post(self, note) -> None:
            self.posted.append(note)

    notes = _Notes()
    ctx = _ctx(
        {
            "task_center": SimpleNamespace(notes=notes),
            "work_item_id": "task-1",
            "agent_name": "scout",
            "write_scope": ["src/auth.py"],
        }
    )

    tool = SubmitTaskNoteTool()
    result = await tool.execute(
        tool.input_model(content="Mapped auth surface.", tags=["discovery"]),
        ctx,
    )

    assert result.is_error is False
    payload = json.loads(result.output)
    assert payload["note_id"]
    assert payload["task_id"] == "task-1"
    assert payload["agent_name"] == "scout"
    assert payload["content"] == "Mapped auth surface."
    assert payload["paths"] == ["src/auth.py"]
    assert payload["tags"] == ["discovery"]
    assert notes.posted[0].id == payload["note_id"]


@pytest.mark.asyncio
async def test_submit_task_note_allows_scout_correct_path_content():
    class _Notes:
        def __init__(self) -> None:
            self.posted = []

        async def post(self, note) -> None:
            self.posted.append(note)

    notes = _Notes()
    ctx = _ctx(
        {
            "task_center": SimpleNamespace(notes=notes),
            "work_item_id": "task-1",
            "agent_name": "scout",
        }
    )

    tool = SubmitTaskNoteTool()
    result = await tool.execute(
        tool.input_model(
            content="Missing target; correct path appears to be src/session.py.",
            paths=["src/auth.py"],
            tags=["discovery"],
        ),
        ctx,
    )

    assert result.is_error is False
    assert notes.posted[0].content == "Missing target; correct path appears to be src/session.py."


def test_submit_task_note_rejects_whitespace_only_content():
    with pytest.raises(ValidationError, match="content must contain non-whitespace text"):
        SubmitTaskNoteTool.input_model(content=" \n\t")


def test_submit_task_note_schema_is_pydantic_native():
    schema = SubmitTaskNoteTool().to_api_schema()

    content_description = schema["input_schema"]["properties"]["content"]["description"]
    assert "REQUIRED" in content_description
    assert "non-whitespace" in content_description
    assert "Always send this field in the tool input object" in content_description
    assert '{"content":"<concise Task Center note>"' in content_description
    assert "The input object must include non-empty, non-whitespace `content`" in schema[
        "description"
    ]
    assert "put the note in the `content` field" in schema["description"]
    assert "{}" not in schema["description"]
    assert schema["output_schema"]["properties"]["task_id"]["description"]


def test_read_task_note_schema_explains_background_scout_scope():
    schema = ReadTaskNoteTool().to_api_schema()

    assert "notes posted by run_subagent scouts" in schema["description"]
    assert "omit scope or keep scope='own' after a background scout wave" in schema[
        "description"
    ]
    scope_description = schema["input_schema"]["properties"]["scope"]["description"]
    assert "Background scout/subagent notes created by run_subagent are own-scope notes" in (
        scope_description
    )
    assert "true sibling team tasks" in scope_description


@pytest.mark.asyncio
async def test_read_task_graph_defaults_to_peer_tree_json():
    graph = {
        "root": _task("root", parent_id=None, agent="planner", description="Root"),
        "parent": _task("parent", parent_id="root", agent="planner", description="Parent"),
        "self": _task(
            "self",
            parent_id="parent",
            status=TaskStatus.RUNNING,
            description="Current task",
            deps=["peer"],
            scope_paths=["src/self.py"],
        ),
        "peer": _task("peer", parent_id="parent", description="Peer task"),
        "peer-child": _task(
            "peer-child",
            parent_id="peer",
            status=TaskStatus.PENDING,
            description="Nested child",
        ),
        "other-branch": _task("other-branch", parent_id="root"),
    }
    ctx = _ctx(
        {
            "task_center": SimpleNamespace(graph=graph),
            "work_item_id": "self",
        }
    )

    result = await ReadTaskGraphTool().execute(
        ReadTaskGraphTool.input_model(),
        ctx,
    )

    assert result.is_error is False
    payload = json.loads(result.output)
    assert payload["parent"] == {
        "id": "parent",
        "agent": "planner",
        "status": "ready",
        "description": "Parent",
    }
    assert [task["id"] for task in payload["tasks"]] == ["self", "peer"]
    self_node = payload["tasks"][0]
    assert self_node["is_you"] is True
    assert self_node["deps"] == ["peer"]
    assert self_node["scope_paths"] == ["src/self.py"]
    assert payload["tasks"][1]["children"][0]["id"] == "peer-child"
    assert "other-branch" not in json.dumps(payload)


@pytest.mark.asyncio
async def test_read_task_graph_global_scope_includes_roots_and_detached_nodes():
    graph = {
        "root": _task("root", parent_id=None, agent="planner"),
        "child": _task("child", parent_id="root", description="Child"),
        "orphan": _task(
            "orphan",
            parent_id="missing-parent",
            status=TaskStatus.FAILED,
            failure_reason="parent was pruned",
        ),
    }
    ctx = _ctx(
        {
            "task_center": SimpleNamespace(graph=graph),
            "work_item_id": "child",
        }
    )

    result = await ReadTaskGraphTool().execute(
        ReadTaskGraphTool.input_model(global_scope=True),
        ctx,
    )

    assert result.is_error is False
    payload = json.loads(result.output)
    assert [task["id"] for task in payload["tasks"]] == ["root"]
    assert payload["tasks"][0]["children"][0]["id"] == "child"
    assert payload["tasks"][0]["children"][0]["is_you"] is True
    assert [task["id"] for task in payload["detached"]] == ["orphan"]
    assert payload["detached"][0]["failure_reason"] == "parent was pruned"


@pytest.mark.asyncio
async def test_read_task_note_empty_path_read_is_successful_freshness_check():
    class _Notes:
        async def read(self, **_kwargs):
            return []

        async def read_notes(self, **_kwargs):
            return []

        def known_paths(self):
            return ["src/other.py"]

    ctx = _ctx({"task_center": SimpleNamespace(notes=_Notes())})

    result = await ReadTaskNoteTool().execute(
        ReadTaskNoteTool.input_model(paths=["src/auth.py"]),
        ctx,
    )

    assert result.is_error is False
    assert "No notes found for paths" in result.output
    assert "src/other.py" in result.output


@pytest.mark.asyncio
async def test_task_center_changed_since_marks_checked_and_excludes_own_run_changes():
    own_change = SimpleNamespace(
        file_path="src/auth/local.py",
        agent_run_id="run-1",
        task_id="task-own",
    )
    other_change = SimpleNamespace(
        file_path="src/auth/session.py",
        agent_run_id="run-2",
        task_id="task-peer",
    )
    ctx = _ctx(
        {
            "work_item_started_at": 1.0,
            "agent_run_id": "run-1",
            "write_scope": ["src/auth/"],
            "arbiter": SimpleNamespace(
                initialized=True,
                changes_since=lambda _since, team_run_id=None: [own_change, other_change],
            ),
        }
    )

    result = await TaskCenterChangedSinceTool().execute(
        TaskCenterChangedSinceTool.input_model(),
        ctx,
    )

    payload = json.loads(result.output)
    assert payload["scope_changes_by_others"] == 1
    assert payload["stale"] is True
    assert ctx.metadata["checked_context_freshness"] is True


@pytest.mark.asyncio
async def test_task_center_changed_since_ignores_unrelated_sibling_completion():
    class _Dispatcher:
        async def done_sibling_ids(self, **_kwargs):
            return ["sib-1"]

        async def get_task_by_id(self, _task_id):
            return SimpleNamespace(scope_paths=["src/payments/"])

    ctx = _ctx(
        {
            "work_item_started_at": 1.0,
            "work_item_id": "task-1",
            "task_parent_id": "parent-1",
            "write_scope": ["src/auth/"],
            "dispatcher": _Dispatcher(),
        }
    )

    result = await TaskCenterChangedSinceTool().execute(
        TaskCenterChangedSinceTool.input_model(),
        ctx,
    )

    payload = json.loads(result.output)
    assert payload["stale"] is False
    assert payload["new_sibling_completions"] == 0


@pytest.mark.asyncio
async def test_task_center_changed_since_counts_overlapping_sibling_completion():
    class _FakeTaskCenter:
        def __init__(self):
            self.store = self  # production reads get_done_sibling_ids via tc.store

        async def get_done_sibling_ids(self, **_kwargs):
            return ["sib-1"]

        async def get_task(self, _task_id):
            return SimpleNamespace(scope_paths=["src/auth/session.py"])

    ctx = _ctx(
        {
            "work_item_started_at": 1.0,
            "work_item_id": "task-1",
            "task_parent_id": "parent-1",
            "write_scope": ["src/auth/"],
            "task_center": _FakeTaskCenter(),
        }
    )

    result = await TaskCenterChangedSinceTool().execute(
        TaskCenterChangedSinceTool.input_model(),
        ctx,
    )

    payload = json.loads(result.output)
    assert payload["stale"] is True
    assert payload["new_sibling_completions"] == 1
