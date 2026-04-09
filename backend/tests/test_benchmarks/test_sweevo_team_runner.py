from __future__ import annotations

from types import SimpleNamespace

from benchmarks.sweevo.team_runner import _make_context_builders
from benchmarks.sweevo.team_runner import _build_sweevo_planner_runtime_prompt, _derive_planner_controls
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


def test_planner_controls_scale_with_large_instance():
    instance = SimpleNamespace(
        repo="pydantic/pydantic",
        instance_id="pydantic__pydantic_v2.6.0b1_v2.6.0",
        instance_id_swe="pydantic__pydantic_v2.6.0b1_v2.6.0",
        start_version="2.6.0b1",
        end_version="2.6.0",
        docker_image="example/image:latest",
        test_cmds="pytest -q",
        problem_statement="- bullet\n" * 80,
        fail_to_pass=["tests/test_foo.py::test_bar"],
        pass_to_pass=["tests/test_foo.py::test_existing"],
    )
    controls = _derive_planner_controls(instance)

    assert controls["first_plan_exploration_budget"] == 12
    assert controls["tool_call_limit"] == 18
    assert "Once you say or infer that you have enough context" in _build_sweevo_planner_runtime_prompt(instance)
