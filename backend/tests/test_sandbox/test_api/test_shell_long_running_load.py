"""Long-running shell mutation load suites for the sandbox API."""

from __future__ import annotations

import shlex
from pathlib import Path

import pytest

from test_load import (
    ApiLoadEnv,
    LoadRecorder,
    _BarrierInvoker,
    _assert_all_success,
    _assert_logged_progress,
    _assert_single_winner,
    _assert_timing_keys,
    _compact_stack,
    _run_load_batch,
    api_load_env as _shared_api_load_env,
)
from sandbox.api import ShellRequest
from sandbox.api.shell import shell
from sandbox.overlay.client import OverlayClient, register_overlay_client
from sandbox.overlay.runner.snapshot_overlay_runner import SnapshotOverlayRunner


api_load_env = _shared_api_load_env
LONG_RUNNING_CONCURRENCY_LEVELS = (3, 5)
LONG_RUNNING_TIMING_KEYS = (
    "api.shell.total_s",
    "overlay.run_command_s",
    "overlay.capture_changes_s",
    "occ.commit.total_s",
    "occ.serial.batch_size",
)


async def test_long_running_shell_disjoint_mutation_merge_success_levels_3_5(
    request: pytest.FixtureRequest,
) -> None:
    api_load_env: ApiLoadEnv = request.getfixturevalue("api_load_env")
    recorder = LoadRecorder("long_running_shell_merge_success")
    seen = set()
    for level in LONG_RUNNING_CONCURRENCY_LEVELS:
        _register_barrier_overlay(api_load_env, parties=level)

        async def op(index: int):
            path = f"load/long-shell/merge/{level}/{index}.txt"
            payload = f"merge-success-{level}-{index}\n"
            return await shell(
                api_load_env.sandbox_id,
                ShellRequest(
                    command=_long_running_write_command(
                        path=path,
                        payload=payload,
                        progress_path=f"load/long-shell/merge/{level}/{index}.progress",
                    ),
                    actor=api_load_env.actor(index),
                    timeout=20,
                    description="long shell disjoint merge",
                ),
            )

        report = await _run_load_batch(
            api_load_env,
            recorder,
            label="long_shell_merge",
            concurrency=level,
            operation=op,
        )
        _assert_all_success(report)
        _assert_timing_keys(report, LONG_RUNNING_TIMING_KEYS)
        for index in range(level):
            assert api_load_env.manager.read_text(
                f"load/long-shell/merge/{level}/{index}.txt"
            ) == (f"merge-success-{level}-{index}\n", True)
        _compact_stack(api_load_env)
        seen.add(level)

    assert seen == set(LONG_RUNNING_CONCURRENCY_LEVELS)
    _assert_logged_progress(recorder)


async def test_long_running_shell_same_file_conflict_levels_3_5(
    request: pytest.FixtureRequest,
) -> None:
    api_load_env: ApiLoadEnv = request.getfixturevalue("api_load_env")
    recorder = LoadRecorder("long_running_shell_same_file_conflict")
    seen = set()
    for level in LONG_RUNNING_CONCURRENCY_LEVELS:
        _register_barrier_overlay(api_load_env, parties=level)
        conflict_path = f"load/long-shell/conflict/{level}/shared.txt"

        async def op(index: int):
            payload = f"conflict-winner-{level}-{index}\n"
            return await shell(
                api_load_env.sandbox_id,
                ShellRequest(
                    command=_long_running_write_command(
                        path=conflict_path,
                        payload=payload,
                        progress_path=(
                            f"load/long-shell/conflict/{level}/progress-{index}.txt"
                        ),
                    ),
                    actor=api_load_env.actor(index),
                    timeout=20,
                    description="long shell same-file conflict",
                ),
            )

        report = await _run_load_batch(
            api_load_env,
            recorder,
            label="long_shell_conflict",
            concurrency=level,
            operation=op,
        )
        _assert_single_winner(report, conflict_status="aborted_version")
        _assert_timing_keys(report, LONG_RUNNING_TIMING_KEYS)
        content, exists = api_load_env.manager.read_text(conflict_path)
        assert exists is True
        assert content in {f"conflict-winner-{level}-{index}\n" for index in range(level)}
        _compact_stack(api_load_env)
        seen.add(level)

    assert seen == set(LONG_RUNNING_CONCURRENCY_LEVELS)
    _assert_logged_progress(recorder)


def _register_barrier_overlay(env: ApiLoadEnv, *, parties: int) -> None:
    register_overlay_client(
        env.sandbox_id,
        OverlayClient(
            runner=SnapshotOverlayRunner(
                env.manager,
                invoker=_BarrierInvoker(
                    storage_root=env.manager.storage_root,
                    parties=parties,
                ),
            )
        ),
    )


def _long_running_write_command(
    *,
    path: str,
    payload: str,
    progress_path: str,
    delay_s: float = 0.35,
) -> str:
    parent = shlex.quote(str(Path(path).parent))
    progress_parent = shlex.quote(str(Path(progress_path).parent))
    started = shlex.quote("started\n")
    return (
        f"mkdir -p {parent} {progress_parent}; "
        f"printf {started} > {shlex.quote(progress_path)}; "
        f"sleep {delay_s:.2f}; "
        f"printf {shlex.quote(payload)} > {shlex.quote(path)}; "
        f"cat {shlex.quote(path)}"
    )
