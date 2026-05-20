"""Wiring tests for the ``--sweevo-runner`` CLI flag.

We do NOT execute the full pipeline (that needs Daytona + real LLM
creds). Instead we verify the CLI plumbing: argparse accepts the flag,
each fail-fast branch returns exit 2 with a useful stderr message, and
the snapshot-missing path does not create a sandbox.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from benchmarks.sweevo import __main__ as sweevo_main
from benchmarks.sweevo.sandbox import SnapshotNotRegisteredError


def _argv(*extra: str) -> list[str]:
    return ["--sweevo-runner", "--instance-id", "dask__dask_2023.3.2_2023.4.0", *extra]


def test_parser_accepts_sweevo_runner_flag() -> None:
    args = sweevo_main._build_parser().parse_args(
        ["--sweevo-runner", "--instance-id", "x"]
    )
    assert args.sweevo_runner is True
    assert args.instance_id == "x"


def test_parser_accepts_csv_path() -> None:
    args = sweevo_main._build_parser().parse_args(
        ["--sweevo-runner", "--instance-id", "x", "--csv-path", "/tmp/y.csv"]
    )
    assert args.csv_path == "/tmp/y.csv"


def test_sweevo_runner_without_instance_id_returns_2(
    capsys: pytest.CaptureFixture[str],
) -> None:
    args = sweevo_main._build_parser().parse_args(["--sweevo-runner"])

    rc = asyncio.run(sweevo_main._cmd_sweevo_runner(args))

    err = capsys.readouterr().err
    assert rc == 2
    assert "--instance-id" in err


def test_sweevo_runner_missing_row_returns_2(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from benchmarks.sweevo import prompt as prompt_mod

    def fake_load_pr_description(instance_id: str, *, csv_path: Any = None) -> str:
        raise KeyError(f"instance_id {instance_id!r} not found in /tmp/pr.csv")

    monkeypatch.setattr(prompt_mod, "load_pr_description", fake_load_pr_description)

    args = sweevo_main._build_parser().parse_args(_argv())
    rc = asyncio.run(sweevo_main._cmd_sweevo_runner(args))

    err = capsys.readouterr().err
    assert rc == 2
    assert "dask__dask_2023.3.2_2023.4.0" in err


def test_sweevo_runner_missing_file_returns_2(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from benchmarks.sweevo import prompt as prompt_mod

    def fake_load_pr_description(instance_id: str, *, csv_path: Any = None) -> str:
        raise FileNotFoundError("/no/such/file.csv")

    monkeypatch.setattr(prompt_mod, "load_pr_description", fake_load_pr_description)

    args = sweevo_main._build_parser().parse_args(_argv())
    rc = asyncio.run(sweevo_main._cmd_sweevo_runner(args))

    err = capsys.readouterr().err
    assert rc == 2
    assert "/no/such/file.csv" in err


def test_sweevo_runner_empty_value_returns_2(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from benchmarks.sweevo import prompt as prompt_mod

    def fake_load_pr_description(instance_id: str, *, csv_path: Any = None) -> str:
        raise ValueError(f"row for {instance_id!r} has empty pr_description")

    monkeypatch.setattr(prompt_mod, "load_pr_description", fake_load_pr_description)

    args = sweevo_main._build_parser().parse_args(_argv())
    rc = asyncio.run(sweevo_main._cmd_sweevo_runner(args))

    err = capsys.readouterr().err
    assert rc == 2
    assert "empty pr_description" in err


def test_sweevo_runner_missing_snapshot_returns_2(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from benchmarks.sweevo import dataset as dataset_mod
    from benchmarks.sweevo import prompt as prompt_mod
    from benchmarks.sweevo import sandbox as sandbox_mod

    def fake_load_pr_description(instance_id: str, *, csv_path: Any = None) -> str:
        return "fix the bug"

    monkeypatch.setattr(prompt_mod, "load_pr_description", fake_load_pr_description)

    fake_instance = dataset_mod.SWEEvoInstance(
        instance_id="dask__dask_2023.3.2_2023.4.0",
        repo="dask/dask",
        base_commit="abc",
        problem_statement="",
        patch="",
        fail_to_pass=[],
        pass_to_pass=[],
        docker_image="example/image:1",
        test_cmds="pytest",
        environment_setup_commit="",
    )
    monkeypatch.setattr(
        dataset_mod, "load_sweevo_instance", lambda **_kw: fake_instance
    )

    # Sandbox-provider bootstrap should be a no-op for this wiring test.
    monkeypatch.setattr(sweevo_main, "_bootstrap_sandbox_provider", lambda: None)

    def fake_verify(_instance: Any) -> str:
        raise SnapshotNotRegisteredError(
            "Daytona snapshot 'sweevo-dask__dask_2023.3.2_2023.4.0' is not "
            "registered. Pre-register via register_sweevo_snapshot."
        )

    monkeypatch.setattr(
        sandbox_mod, "verify_sweevo_snapshot_exists", fake_verify
    )

    # If the snapshot check raises, ``create_sweevo_test_sandbox`` must NOT
    # be called — wire a sentinel that fails the test if invoked.
    def fake_create(*_args: Any, **_kw: Any) -> Any:
        raise AssertionError("create_sweevo_test_sandbox called despite snapshot-not-registered")

    monkeypatch.setattr(sandbox_mod, "create_sweevo_test_sandbox", fake_create)

    args = sweevo_main._build_parser().parse_args(_argv())
    rc = asyncio.run(sweevo_main._cmd_sweevo_runner(args))

    err = capsys.readouterr().err
    assert rc == 2
    assert "register_sweevo_snapshot" in err
    assert "is not registered" in err


def test_sweevo_runner_bare_image_skips_snapshot_preflight(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from benchmarks.sweevo import dataset as dataset_mod
    from benchmarks.sweevo import prompt as prompt_mod
    from benchmarks.sweevo import sandbox as sandbox_mod
    import sandbox.api as sandbox_api
    from task_center_runner.core import engine as engine_mod

    captured: dict[str, Any] = {}

    def fake_load_pr_description(instance_id: str, *, csv_path: Any = None) -> str:
        return "fix the bug"

    monkeypatch.setattr(prompt_mod, "load_pr_description", fake_load_pr_description)

    fake_instance = dataset_mod.SWEEvoInstance(
        instance_id="dask__dask_2023.3.2_2023.4.0",
        repo="dask/dask",
        base_commit="abc",
        problem_statement="",
        patch="",
        fail_to_pass=[],
        pass_to_pass=[],
        docker_image="example/image",
        test_cmds="pytest",
        environment_setup_commit="",
    )
    monkeypatch.setattr(
        dataset_mod, "load_sweevo_instance", lambda **_kw: fake_instance
    )
    monkeypatch.setattr(sweevo_main, "_bootstrap_sandbox_provider", lambda: None)
    monkeypatch.setattr(
        sandbox_mod,
        "verify_sweevo_snapshot_exists",
        lambda _instance: pytest.fail("bare image must not require snapshot preflight"),
    )

    async def fake_create(*_args: Any, **kwargs: Any) -> dict[str, object]:
        captured["create_kwargs"] = kwargs
        return {"sandbox_id": "sbx-1"}

    async def fake_run_pipeline(_config: Any) -> SimpleNamespace:
        captured["config"] = _config
        return SimpleNamespace(
            task_center_status="completed",
            task_center_run_id="run-1",
            lifecycle_extras={
                "sweevo_result": SimpleNamespace(resolved=True, fix_rate=1.0)
            },
            run_dir=Path("/tmp/sweevo-run"),
        )

    monkeypatch.setattr(sandbox_mod, "create_sweevo_test_sandbox", fake_create)
    monkeypatch.setattr(engine_mod, "run_pipeline", fake_run_pipeline)
    monkeypatch.setattr(sandbox_api, "delete_sandbox", lambda _sandbox_id: None)

    args = sweevo_main._build_parser().parse_args(_argv())
    rc = asyncio.run(sweevo_main._cmd_sweevo_runner(args))

    assert rc == 0
    assert captured["create_kwargs"]["snapshot_name"] == ""
    assert captured["create_kwargs"]["register_snapshot"] is False
    assert captured["config"].repo_dir == "/testbed"
    assert captured["config"].extras["runtime_config"].cwd == str(Path.cwd())


def test_help_message_lists_sweevo_runner(capsys: pytest.CaptureFixture[str]) -> None:
    """The ``--sweevo-runner`` flag must surface in help output."""
    with pytest.raises(SystemExit):
        sweevo_main._build_parser().parse_args(["--help"])
    out = capsys.readouterr().out
    assert "--sweevo-runner" in out


def test_main_no_args_lists_sweevo_runner(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The default help line mentions ``--sweevo-runner``."""
    monkeypatch.setattr(sys, "argv", ["benchmarks.sweevo"])
    rc = sweevo_main.main([])
    err = capsys.readouterr().err
    assert rc == 2
    assert "--sweevo-runner" in err
