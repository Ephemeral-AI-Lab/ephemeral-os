from __future__ import annotations

import asyncio
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock

from benchmarks.sweevo.__main__ import _collect_health_issues
from benchmarks.sweevo.models import SWEEvoInstance
from benchmarks.sweevo.runner import run_sweevo_with_agent


def _instance() -> SWEEvoInstance:
    return SWEEvoInstance(
        instance_id="pydantic__pydantic_v2.6.0b1_v2.6.0",
        repo="pydantic/pydantic",
        base_commit="abc123",
        problem_statement="",
        patch="",
        fail_to_pass=["tests/test_discriminated_union.py::test_presence_of_discriminator"],
        pass_to_pass=["tests/test_json_schema.py::test_alias_same"],
        docker_image="xingyaoww/sweb.eval.x86_64.pydantic_s_pydantic-8583",
        test_cmds="pytest --continue-on-collection-errors -rA",
        environment_setup_commit="",
    )


def test_run_sweevo_with_agent_returns_structured_grading(monkeypatch):
    from benchmarks.sweevo import runner as sweevo_runner

    instance = _instance()
    printer = SimpleNamespace(flush=lambda: None)

    fake_team_runner = ModuleType("benchmarks.sweevo.team_runner")

    async def _fake_run_team(*args, **kwargs):
        return "succeeded", 2

    fake_team_runner.run_sweevo_team = _fake_run_team
    monkeypatch.setitem(sys.modules, "benchmarks.sweevo.team_runner", fake_team_runner)

    fake_sandbox_pkg = ModuleType("sandbox")
    fake_lifecycle = ModuleType("sandbox.lifecycle")
    fake_lifecycle.shutdown_cached_client = lambda: None
    monkeypatch.setitem(sys.modules, "sandbox", fake_sandbox_pkg)
    monkeypatch.setitem(sys.modules, "sandbox.lifecycle", fake_lifecycle)

    monkeypatch.setattr(sweevo_runner, "select_sweevo_instance", lambda **_: instance)
    monkeypatch.setattr(
        sweevo_runner,
        "create_sweevo_test_sandbox",
        AsyncMock(
            return_value={
                "sandbox_id": "sbx-1",
                "sandbox": {"id": "sbx-1"},
                "snapshot_name": "snap-1",
            }
        ),
    )
    monkeypatch.setattr(sweevo_runner, "_extract_combined_patch", AsyncMock(return_value="diff"))
    monkeypatch.setattr(
        sweevo_runner,
        "run_sweevo_required_test",
        AsyncMock(return_value={"command": "pytest", "exit_code": 1, "output": "failed"}),
    )

    async def _fake_evaluate(instance_arg, result, sandbox_id, repo_dir="/testbed"):
        assert instance_arg is instance
        assert sandbox_id == "sbx-1"
        assert repo_dir == "/testbed"
        result.resolved = False
        result.fix_rate = 0.0
        result.fail_to_pass_passed = 0
        result.fail_to_pass_total = 1
        result.pass_to_pass_broken = 1
        result.pass_to_pass_total = 1
        return result

    monkeypatch.setattr(sweevo_runner, "evaluate_sweevo_result", _fake_evaluate)

    result = asyncio.run(
        run_sweevo_with_agent(
            printer=printer,
            instance_id=instance.instance_id,
            register_snapshot=False,
        )
    )

    assert result["test"]["exit_code"] == 1
    assert result["grading"] == {
        "resolved": False,
        "fix_rate": 0.0,
        "fail_to_pass_passed": 0,
        "fail_to_pass_total": 1,
        "pass_to_pass_broken": 1,
        "pass_to_pass_total": 1,
        "status": "completed",
    }


def test_collect_health_issues_includes_unresolved_grading():
    issues = _collect_health_issues(
        {
            "team_status": "succeeded",
            "grading": {
                "resolved": False,
                "fail_to_pass_passed": 0,
                "fail_to_pass_total": 1,
                "pass_to_pass_broken": 1,
                "pass_to_pass_total": 5,
                "fix_rate": 0.0,
            },
        }
    )

    assert issues == ["f2p=0/1", "p2p_broken=1/5"]
