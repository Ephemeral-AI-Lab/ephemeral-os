from __future__ import annotations

from types import SimpleNamespace

from benchmarks.sweevo.team_runner import _make_context_builders
from team.models import WorkItem, WorkItemKind, WorkItemStatus


def test_posthook_ctx_prefers_final_text_over_wrapped_work_result():
    _, build_posthook_ctx = _make_context_builders("sbx-1")

    ctx = build_posthook_ctx(
        SimpleNamespace(name="submit_atlas_agent"),
        {
            "final_text": '{"chunks":[{"subsystem":"pydantic","brief":{"target_paths":["pydantic"]}}]}',
            "team_run_id": "T1",
            "work_item_id": "W1",
        },
    )

    assert ctx.user_message == (
        '{"chunks":[{"subsystem":"pydantic","brief":{"target_paths":["pydantic"]}}]}'
    )
    assert ctx.tool_metadata.team_run_id == "T1"
    assert ctx.tool_metadata.work_item_id == "W1"


def test_query_ctx_seeds_repo_root_for_daytona_and_ci():
    build_query_ctx, _ = _make_context_builders("sbx-1", repo_dir="/testbed")
    ctx = build_query_ctx(
        SimpleNamespace(name="developer"),
        SimpleNamespace(
            id="TR1",
            sandbox_id="sbx-1",
            dispatcher=SimpleNamespace(
                artifact_store=SimpleNamespace(load=lambda _ref: None)
            ),
            budgets=None,
            project_context=None,
        ),
        WorkItem(
            id="W1",
            team_run_id="T1",
            agent_name="developer",
            status=WorkItemStatus.PENDING,
            kind=WorkItemKind.ATOMIC,
            payload={"prompt": "Fix it"},
        ),
    )

    assert ctx.tool_metadata.sandbox_id == "sbx-1"
    assert ctx.tool_metadata.daytona_cwd == "/testbed"
    assert ctx.tool_metadata["ci_workspace_root"] == "/testbed"
