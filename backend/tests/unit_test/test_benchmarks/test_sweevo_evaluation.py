from __future__ import annotations

import pytest

from task_center_runner.benchmarks.sweevo import eval as sweevo_evaluation
from task_center_runner.benchmarks.sweevo.models import SWEEvoInstance, SWEEvoResult


def _instance() -> SWEEvoInstance:
    return SWEEvoInstance(
        instance_id="dask__dask_2023.3.2_2023.4.0",
        repo="dask/dask",
        base_commit="abc",
        problem_statement="",
        patch="",
        test_patch="diff --git a/test b/test\n",
        fail_to_pass=["tests/test_fix.py::test_case"],
        pass_to_pass=[],
        docker_image="example/image",
        test_cmds="pytest -q",
        environment_setup_commit="",
    )


@pytest.mark.asyncio
async def test_evaluate_runs_extract_then_patch_then_tests(monkeypatch):
    """evaluate_sweevo_result no longer materializes — the lifecycle does.

    Per Phase 3b of the migration plan, ``SweevoLifecycle.after_run`` calls
    ``apply_layerstack_to_repo`` *before* dispatching to ``evaluate_sweevo_result``
    and asserts the projected workspace has ``.git``. The evaluator can
    therefore assume the bytes are already on disk.
    """
    calls: list[str] = []

    async def fake_extract_patch(_sandbox_id: str, _repo_dir: str) -> str:
        calls.append("extract_patch")
        return "diff --git a/dask/config.py b/dask/config.py\n"

    async def fake_ensure_patch(_instance, _sandbox_id: str, _repo_dir: str) -> None:
        calls.append("test_patch")

    async def fake_run_tests(
        _sandbox_id: str,
        _repo_dir: str,
        test_ids: list[str],
        _test_cmds: str,
    ) -> sweevo_evaluation._TestSetOutcome:
        calls.append("run_tests")
        return sweevo_evaluation._TestSetOutcome(
            passed=len(test_ids), runnable_total=len(test_ids)
        )

    monkeypatch.setattr(sweevo_evaluation, "_extract_combined_patch", fake_extract_patch)
    monkeypatch.setattr(sweevo_evaluation, "ensure_sweevo_test_patch", fake_ensure_patch)
    monkeypatch.setattr(sweevo_evaluation, "_run_test_set_outcome", fake_run_tests)

    result = await sweevo_evaluation.evaluate_sweevo_result(
        _instance(),
        SWEEvoResult(plan_id="plan", instance_id="dask__dask_2023.3.2_2023.4.0"),
        "sbx-1",
        "/testbed",
    )

    assert calls == ["extract_patch", "test_patch", "run_tests"]
    assert result.agent_patch.startswith("diff --git")
    assert result.resolved is True


@pytest.mark.asyncio
async def test_evaluate_keeps_f2p_strict_and_treats_dropped_p2p_as_neutral(
    monkeypatch,
):
    instance = _instance()
    instance.fail_to_pass = ["tests/test_fix.py::test_one", "tests/test_fix.py::test_two"]
    instance.pass_to_pass = ["tests/test_stable.py::test_ok", "tests/test_stable.py::bad"]

    async def fake_extract_patch(_sandbox_id: str, _repo_dir: str) -> str:
        return ""

    async def fake_ensure_patch(_instance, _sandbox_id: str, _repo_dir: str) -> None:
        return None

    async def fake_run_f2p(
        _sandbox_id: str,
        _repo_dir: str,
        _test_ids: list[str],
        _test_cmds: str,
    ) -> sweevo_evaluation._TestSetOutcome:
        return sweevo_evaluation._TestSetOutcome(
            passed=0,
            runnable_total=1,
            dropped_unfindable=1,
        )

    async def fake_run_p2p(
        _sandbox_id: str,
        _repo_dir: str,
        _test_ids: list[str],
        _test_cmds: str,
    ) -> sweevo_evaluation._TestSetOutcome:
        return sweevo_evaluation._TestSetOutcome(
            passed=1,
            runnable_total=1,
            dropped_unfindable=1,
        )

    monkeypatch.setattr(sweevo_evaluation, "_extract_combined_patch", fake_extract_patch)
    monkeypatch.setattr(sweevo_evaluation, "ensure_sweevo_test_patch", fake_ensure_patch)

    calls = [fake_run_f2p, fake_run_p2p]

    async def fake_run_test_set(*args):
        return await calls.pop(0)(*args)

    monkeypatch.setattr(sweevo_evaluation, "_run_test_set_outcome", fake_run_test_set)

    result = await sweevo_evaluation.evaluate_sweevo_result(
        instance,
        SWEEvoResult(plan_id="plan", instance_id=instance.instance_id),
        "sbx-1",
        "/testbed",
    )

    assert result.fail_to_pass_passed == 0
    assert result.fail_to_pass_total == 2
    assert result.fix_rate == 0
    assert result.pass_to_pass_broken == 0
    assert result.pass_to_pass_total == 2
    assert result.resolved is False


@pytest.mark.asyncio
async def test_run_test_set_stages_ids_and_real_pytest_runner_files(monkeypatch):
    """Test IDs and pytest runner must travel via files, never inline argv.

    Inlining 6000+ IDs blows the docker-exec argv limit (`exec /bin/bash:
    argument list too long`); the helper stages the IDs as JSON in /tmp
    and runs pytest from a real Python file so multiprocessing spawn can reload
    the parent module.
    """
    captured: dict[str, object] = {"writes": [], "cmds": []}

    async def fake_write(_sandbox_id, path, content, *, chunk_size: int = 4096):
        captured["writes"].append((path, content))

    async def fake_exec(_sandbox_id, cmd, **_kwargs):
        captured["cmds"].append(cmd)
        return "EXIT_CODE=0"

    monkeypatch.setattr(sweevo_evaluation, "_write_file_via_chunked_base64", fake_write)
    monkeypatch.setattr(sweevo_evaluation, "_exec", fake_exec)

    passed = await sweevo_evaluation._run_test_set(
        "sbx-1",
        "/testbed",
        ['tests/test_networks.py::test_address_invalid[\n@example.com-None]'],
        "pytest -q",
    )

    assert passed == 1
    # The JSON IDs and Python runner are both staged as files.
    assert len(captured["writes"]) == 2
    ids_path, ids_blob = captured["writes"][0]
    runner_path, runner_blob = captured["writes"][1]
    assert ids_path.startswith("/tmp/sweevo_ids_") and ids_path.endswith(".json")
    assert runner_path.startswith("/tmp/sweevo_pytest_runner_")
    assert runner_path.endswith(".py")
    decoded = ids_blob.decode("utf-8")
    assert 'tests/test_networks.py::test_address_invalid[\\n@example.com-None]' in decoded
    runner_script = runner_blob.decode("utf-8")
    assert "pytest.main(pytest_argv)" in runner_script
    assert "if __name__ == '__main__':" in runner_script
    assert ids_path in runner_script
    runner_cmd = next(c for c in captured["cmds"] if runner_path in c)
    # And the test IDs themselves must NOT appear inline in the runner cmd.
    assert "test_address_invalid" not in runner_cmd


@pytest.mark.asyncio
async def test_run_test_set_counts_passed_tests_from_pytest_summary(monkeypatch):
    async def fake_write(_sandbox_id, path, content, *, chunk_size: int = 4096):
        return None

    async def fake_exec(_sandbox_id, cmd, **_kwargs):
        return "2 failed, 3 passed\nEXIT_CODE=1"

    monkeypatch.setattr(sweevo_evaluation, "_write_file_via_chunked_base64", fake_write)
    monkeypatch.setattr(sweevo_evaluation, "_exec", fake_exec)

    passed = await sweevo_evaluation._run_test_set(
        "sbx-1",
        "/testbed",
        ["a", "b", "c", "d", "e"],
        "pytest -q",
    )

    assert passed == 3
