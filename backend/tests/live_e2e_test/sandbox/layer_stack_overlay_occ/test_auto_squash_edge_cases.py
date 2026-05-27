"""Focused public autosquash edge-case validation.

These cases force OCC's post-publish autosquash path instead of calling
``LayerStack.squash()`` directly. Each row records the explicit squash audit
signals, depth transition, timing, correctness checks, and layer-storage
integrity counters.
"""

from __future__ import annotations

import asyncio
import json
import shlex
import time
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

import sandbox.host.daemon_client as daemon_client_mod
from sandbox.ephemeral_workspace.plugin import call_plugin
from sandbox.host.daemon_client import DEFAULT_LAYER_STACK_ROOT
from tools._framework.core.base import ToolExecutionContextService

from .._harness.integrated_cases import (
    RuntimeCallMetric,
    assert_committed,
    assert_read,
    percentile,
    q,
    remove_tmp,
    timed_call,
    tmp_path,
    token,
    touch_tmp,
    wait_for_tmp,
)
from .._harness.sandbox_fixture import SandboxHandle, WORKSPACE_ROOT
from .._harness.workspace_base_public import seed_imported_base


pytestmark = pytest.mark.asyncio

AUTO_SQUASH_MAX_DEPTH = 100
TRIGGER_WRITES = AUTO_SQUASH_MAX_DEPTH + 4
HEAVY_COMPLEX_AUTO_SQUASHES = 3
HEAVY_COMPLEX_WRITES = HEAVY_COMPLEX_AUTO_SQUASHES * (AUTO_SQUASH_MAX_DEPTH + 1) + 4
PROJECT_PACKAGES = 40
PROJECT_MODULES_PER_PACKAGE = 50
PROJECT_FILE_COUNT = PROJECT_PACKAGES * PROJECT_MODULES_PER_PACKAGE
COMPLEX_LOGICAL_EVENT_FLOOR = 2_000
SHELL_EDIT_LSP_LOGICAL_EDIT_FLOOR = 600
SHELL_EDIT_LSP_TARGET_AUTO_SQUASHES = 6
SHELL_EDIT_LSP_MAX_LOGICAL_EDITS = 750


@pytest.mark.timeout(900)
async def test_complex_project_autosquash_has_explicit_audit_and_no_orphans(
    workspace_base_sandbox: SandboxHandle,
) -> None:
    handle = workspace_base_sandbox
    await _seed_complex_project_base(handle)
    audit_cursor = await _audit_cursor(handle)

    rows = []
    metrics = await _write_public_burst(
        handle,
        label_prefix="autosquash_complex_project_depth",
        path_prefix="tracked/autosquash/complex/depth",
        count=HEAVY_COMPLEX_WRITES,
    )
    grep_probe, grep_metric = await timed_call(
        "autosquash_complex_project_grep_glob_probe",
        handle.tool.shell(
            (
                "set -e; "
                f"test $(find src -name '*.py' | wc -l) -ge {PROJECT_FILE_COUNT}; "
                "grep -R 'def fn_00_' src/pkg_00 >/dev/null"
            ),
            timeout=90,
            description="autosquash complex project grep/glob readback",
        ),
    )
    assert grep_probe.success, grep_probe.stderr
    metrics.append(grep_metric)

    squash_records = _require_auto_squash_records(
        "complex_project",
        metrics,
        expected_count=HEAVY_COMPLEX_AUTO_SQUASHES,
    )
    audit_events = await _require_squash_audit_events(
        handle,
        audit_cursor,
        expected_completed=HEAVY_COMPLEX_AUTO_SQUASHES,
    )
    after = await _assert_clean_layer_storage(handle)
    changed_path_count = sum(len(metric.changed_paths) for metric in metrics)
    logical_event_count = PROJECT_FILE_COUNT + changed_path_count
    assert logical_event_count >= COMPLEX_LOGICAL_EVENT_FLOOR
    assert int(after["manifest_depth"]) <= AUTO_SQUASH_MAX_DEPTH
    assert int(after["layer_dirs"]) == int(after["manifest_depth"])

    await assert_read(handle, "src/pkg_00/module_000.py", _module_content(0, 0))
    await assert_read(
        handle,
        f"tracked/autosquash/complex/depth/{HEAVY_COMPLEX_WRITES - 1:03d}.txt",
        f"burst-{HEAVY_COMPLEX_WRITES - 1:03d}\n",
    )

    rows.append(
        _case_row(
            "complex_project_thousands_of_events",
            metrics=metrics,
            squash_records=squash_records,
            audit_events=audit_events,
            layer_metrics=after,
            extra={
                "project_file_count": PROJECT_FILE_COUNT,
                "target_auto_squashes": HEAVY_COMPLEX_AUTO_SQUASHES,
                "public_write_count": HEAVY_COMPLEX_WRITES,
                "changed_path_count": changed_path_count,
                "logical_event_count": logical_event_count,
            },
        )
    )
    artifact = _write_autosquash_artifact(
        "complex_project_explicit_audit",
        rows,
    )
    print(f"\n[autosquash:complex_project_explicit_audit] artifact={artifact}")


@pytest.mark.timeout(600)
async def test_autosquash_while_background_shell_holds_snapshot_lease(
    workspace_base_sandbox: SandboxHandle,
) -> None:
    handle = workspace_base_sandbox
    watched_path = "tracked/autosquash/background/watched.txt"
    shell_output = "tracked/autosquash/background/shell-view.txt"
    await seed_imported_base(
        handle,
        {
            watched_path: "base-view\n",
            "tracked/autosquash/background/anchor.txt": "anchor\n",
        },
    )
    audit_cursor = await _audit_cursor(handle)

    run = token("autosquash-background")
    started = tmp_path(f"{run}-started")
    proceed = tmp_path(f"{run}-proceed")
    await remove_tmp(handle, started, proceed)
    shell_task = asyncio.create_task(
        timed_call(
            "autosquash_background_shell_lease",
            handle.tool.shell(
                (
                    "set -e; "
                    f"first=$(cat {q(watched_path)}); "
                    f"touch {q(started)}; "
                    f"while [ ! -f {q(proceed)} ]; do sleep 0.01; done; "
                    f"second=$(cat {q(watched_path)}); "
                    f"mkdir -p {q(str(Path(shell_output).parent))}; "
                    f"printf '%s|%s\\n' \"$first\" \"$second\" > {q(shell_output)}"
                ),
                timeout=90,
                description="autosquash background shell lease holder",
            ),
        )
    )
    await wait_for_tmp(handle, started)
    mid = await handle.tool.layer_metrics()
    assert int(mid["active_leases"]) >= 1, mid

    metrics: list[RuntimeCallMetric] = []
    update, update_metric = await timed_call(
        "autosquash_background_active_update",
        handle.tool.write_file(
            watched_path,
            "active-after\n",
            description="autosquash update while background shell holds lease",
        ),
    )
    assert_committed(update, path=watched_path)
    metrics.append(update_metric)
    metrics.extend(
        await _write_public_burst(
            handle,
            label_prefix="autosquash_background_depth",
            path_prefix="tracked/autosquash/background/depth",
            count=TRIGGER_WRITES - 1,
        )
    )

    squash_records = _require_auto_squash_records(
        "background_shell",
        metrics,
        expected_count=1,
    )
    during = await _assert_clean_layer_storage(handle)
    assert int(during["active_leases"]) >= 1, during
    assert int(during["manifest_depth"]) <= AUTO_SQUASH_MAX_DEPTH

    await touch_tmp(handle, proceed)
    shell, shell_metric = await shell_task
    metrics.append(shell_metric)
    assert_committed(shell, path=shell_output)
    assert shell.exit_code == 0, shell.stderr
    await assert_read(handle, watched_path, "active-after\n")
    await assert_read(handle, shell_output, "base-view|base-view\n")

    audit_events = await _require_squash_audit_events(
        handle,
        audit_cursor,
        expected_completed=1,
    )
    after = await _assert_clean_layer_storage(handle)
    assert int(after["active_leases"]) == 0, after

    artifact = _write_autosquash_artifact(
        "background_shell_lease",
        [
            _case_row(
                "background_shell_lease",
                metrics=metrics,
                squash_records=squash_records,
                audit_events=audit_events,
                layer_metrics=after,
                extra={
                    "mid_active_leases": int(mid["active_leases"]),
                    "during_active_leases": int(during["active_leases"]),
                    "frozen_shell_output": "base-view|base-view",
                },
            )
        ],
    )
    print(f"\n[autosquash:background_shell_lease] artifact={artifact}")


@pytest.mark.timeout(600)
async def test_autosquash_while_isolated_workspace_holds_snapshot_lease(
    workspace_base_sandbox: SandboxHandle,
) -> None:
    handle = workspace_base_sandbox
    await seed_imported_base(
        handle,
        {
            "tracked/autosquash/iws/base.txt": "base\n",
            "tracked/autosquash/iws/anchor.txt": "anchor\n",
        },
    )
    await _prepare_isolated_workspace_runtime(handle)
    audit_cursor = await _audit_cursor(handle)

    agent_id = token("autosquash-iws")
    entered = await _iws_enter(handle, agent_id)
    assert entered.get("success") is True, entered
    after_enter = await handle.tool.layer_metrics()
    assert int(after_enter["active_leases"]) >= 1, after_enter

    metrics: list[RuntimeCallMetric] = []
    try:
        metrics.extend(
            await _write_public_burst(
                handle,
                label_prefix="autosquash_iws_depth",
                path_prefix="tracked/autosquash/iws/depth",
                count=TRIGGER_WRITES,
            )
        )
        squash_records = _require_auto_squash_records(
            "isolated_workspace",
            metrics,
            expected_count=1,
        )
        during = await _assert_clean_layer_storage(handle)
        assert int(during["active_leases"]) >= 1, during
        assert int(during["leased_layers"]) >= 1, during
        assert int(during["manifest_depth"]) <= AUTO_SQUASH_MAX_DEPTH
    finally:
        exited = await _iws_exit(handle, agent_id)
        assert exited.get("success") is True, exited

    audit_events = await _require_squash_audit_events(
        handle,
        audit_cursor,
        expected_completed=1,
    )
    after_exit = await _assert_clean_layer_storage(handle)
    assert int(after_exit["active_leases"]) == 0, after_exit
    assert int(after_exit["layer_dirs"]) == int(after_exit["manifest_depth"])
    await assert_read(
        handle,
        f"tracked/autosquash/iws/depth/{TRIGGER_WRITES - 1:03d}.txt",
        f"burst-{TRIGGER_WRITES - 1:03d}\n",
    )

    artifact = _write_autosquash_artifact(
        "isolated_workspace_lease",
        [
            _case_row(
                "isolated_workspace_lease",
                metrics=metrics,
                squash_records=squash_records,
                audit_events=audit_events,
                layer_metrics=after_exit,
                extra={
                    "iws_manifest_version": entered.get("manifest_version"),
                    "active_leases_after_enter": int(after_enter["active_leases"]),
                    "active_leases_during_squash": int(during["active_leases"]),
                    "leased_layers_during_squash": int(during["leased_layers"]),
                },
            )
        ],
    )
    print(f"\n[autosquash:isolated_workspace_lease] artifact={artifact}")


@pytest.mark.timeout(900)
async def test_shell_edit_lsp_style_autosquash_focus(
    workspace_base_sandbox: SandboxHandle,
) -> None:
    handle = workspace_base_sandbox
    path = "src/shell_lsp_focus/probe.py"
    await seed_imported_base(
        handle,
        {
            path: _shell_lsp_probe_content(0),
            "src/shell_lsp_focus/__init__.py": "",
        },
    )
    audit_cursor = await _audit_cursor(handle)

    metrics: list[RuntimeCallMetric] = []
    lsp_metrics = [
        await _lsp_query_symbols_metric(
            handle,
            label="autosquash_shell_edit_lsp_initial_symbols",
            path=path,
        )
    ]
    after_lsp_start = await handle.tool.layer_metrics()
    assert int(after_lsp_start["active_leases"]) >= 1, after_lsp_start

    shell_metric = await _apply_shell_logical_edit(
        handle,
        label="autosquash_shell_edit_lsp_initial_shell_edit",
        path=path,
        old_value=0,
        new_value=1,
    )
    metrics.append(shell_metric)
    logical_edit_count = 1
    edit_file_edit_count = 0
    shell_edit_count = 1
    while (
        logical_edit_count < SHELL_EDIT_LSP_LOGICAL_EDIT_FLOOR
        or _auto_squash_record_count(metrics) < SHELL_EDIT_LSP_TARGET_AUTO_SQUASHES
        or not _last_metric_auto_squashed(metrics)
    ):
        if logical_edit_count >= SHELL_EDIT_LSP_MAX_LOGICAL_EDITS:
            raise AssertionError(
                "shell-edit/LSP autosquash focus exceeded "
                f"{SHELL_EDIT_LSP_MAX_LOGICAL_EDITS} logical edits without ending "
                "on the target autosquash boundary"
            )
        current_value = logical_edit_count
        next_value = logical_edit_count + 1
        metric = await _apply_public_logical_edit(
            handle,
            label=f"autosquash_shell_edit_lsp_edit_file_{logical_edit_count:03d}",
            path=path,
            old_value=current_value,
            new_value=next_value,
        )
        edit_file_edit_count += 1
        metrics.append(metric)
        logical_edit_count += 1

    mutation_metrics = list(metrics)
    lsp_metrics.append(
        await _lsp_query_symbols_metric(
            handle,
            label="autosquash_shell_edit_lsp_final_symbols",
            path=path,
        )
    )
    metrics.extend(lsp_metrics)

    squash_records = _require_auto_squash_records(
        "shell_edit_lsp_focus",
        metrics,
    )
    audit_events = await _require_squash_audit_events(
        handle,
        audit_cursor,
    )
    assert len(squash_records) >= SHELL_EDIT_LSP_TARGET_AUTO_SQUASHES, squash_records
    audit_counts = _squash_audit_counts(audit_events)
    assert audit_counts["layer_stack.squash_completed"] == len(squash_records)
    after = await _assert_clean_layer_storage(handle)
    assert int(after["manifest_depth"]) <= AUTO_SQUASH_MAX_DEPTH
    assert logical_edit_count >= SHELL_EDIT_LSP_LOGICAL_EDIT_FLOOR
    assert edit_file_edit_count >= SHELL_EDIT_LSP_LOGICAL_EDIT_FLOOR - 1
    assert shell_edit_count == 1
    assert _last_metric_auto_squashed(mutation_metrics)

    final_lsp_timings = lsp_metrics[-1].timings
    assert (
        int(final_lsp_timings.get("lsp.session.refresh_count_delta", 0)) >= 1
        or int(final_lsp_timings.get("lsp.session.remount_count_delta", 0)) >= 1
    ), final_lsp_timings
    await assert_read(handle, path, _shell_lsp_probe_content(logical_edit_count))

    artifact = _write_autosquash_artifact(
        "shell_edit_lsp_focus",
        [
            _case_row(
                "shell_edit_lsp_focus",
                metrics=metrics,
                squash_records=squash_records,
                audit_events=audit_events,
                layer_metrics=after,
                extra={
                    "logical_edit_count": logical_edit_count,
                    "edit_file_edit_count": edit_file_edit_count,
                    "shell_edit_count": shell_edit_count,
                    "lsp_query_symbols_count": len(lsp_metrics),
                    "active_leases_after_lsp_start": int(after_lsp_start["active_leases"]),
                    "active_leases_after_final_lsp": int(after["active_leases"]),
                    "minimum_auto_squashes": SHELL_EDIT_LSP_TARGET_AUTO_SQUASHES,
                    "focus": "lsp lease + initial shell edit + edit_file autosquash saturation",
                },
            )
        ],
    )
    print(f"\n[autosquash:shell_edit_lsp_focus] artifact={artifact}")


async def _seed_complex_project_base(handle: SandboxHandle) -> None:
    script = r"""
import sys
from pathlib import Path

root = Path(sys.argv[1])
packages = int(sys.argv[2])
modules_per_package = int(sys.argv[3])
(root / "tracked" / "autosquash" / "complex").mkdir(parents=True, exist_ok=True)
for package in range(packages):
    package_dir = root / "src" / ("pkg_%02d" % package)
    package_dir.mkdir(parents=True, exist_ok=True)
    (package_dir / "__init__.py").write_text("", encoding="utf-8")
    for module in range(modules_per_package):
        body = (
            "# generated module\n"
            "VALUE = %d\n"
            "def fn_%02d_%03d():\n"
            "    return VALUE\n"
        ) % (package * modules_per_package + module, package, module)
        (package_dir / ("module_%03d.py" % module)).write_text(body, encoding="utf-8")
"""
    result = await handle.raw_exec(
        handle.sandbox_id,
        "python3 -c {script} {root} {packages} {modules}".format(
            script=shlex.quote(script),
            root=shlex.quote(WORKSPACE_ROOT),
            packages=PROJECT_PACKAGES,
            modules=PROJECT_MODULES_PER_PACKAGE,
        ),
        timeout=120,
    )
    assert result.exit_code == 0, result.stderr or result.stdout
    built = await daemon_client_mod.call_daemon_api(
        handle.sandbox_id,
        "api.build_workspace_base",
        {"workspace_root": WORKSPACE_ROOT, "reset": True},
        timeout=240,
    )
    assert built.get("success") is True, built
    before = await handle.tool.layer_metrics()
    assert int(before["manifest_depth"]) == 1, before
    assert int(before["orphan_layer_count"]) == 0, before


async def _apply_public_logical_edit(
    handle: SandboxHandle,
    *,
    label: str,
    path: str,
    old_value: int,
    new_value: int,
) -> RuntimeCallMetric:
    result, metric = await timed_call(
        label,
        handle.tool.edit_file(
            path,
            [(f"VALUE = {old_value}\n", f"VALUE = {new_value}\n")],
            description=label,
        ),
    )
    assert_committed(result, path=path)
    assert result.applied_edits == 1, result
    return metric


async def _apply_shell_logical_edit(
    handle: SandboxHandle,
    *,
    label: str,
    path: str,
    old_value: int,
    new_value: int,
) -> RuntimeCallMetric:
    result, metric = await timed_call(
        label,
        handle.tool.shell(
            _shell_value_replace_command(
                path=path,
                old_text=f"VALUE = {old_value}\n",
                new_text=f"VALUE = {new_value}\n",
            ),
            cwd=WORKSPACE_ROOT,
            timeout=90,
            description=label,
        ),
    )
    assert_committed(result, path=path)
    assert result.exit_code == 0, result.stderr
    return metric


def _shell_value_replace_command(*, path: str, old_text: str, new_text: str) -> str:
    return "\n".join(
        (
            "python3 - <<'PY'",
            "import hashlib",
            "import json",
            "from pathlib import Path",
            f"path = Path({json.dumps(path)})",
            f"old = {json.dumps(old_text)}",
            f"new = {json.dumps(new_text)}",
            "data = path.read_text(encoding='utf-8')",
            "if data.count(old) != 1:",
            "    raise SystemExit(f'expected exactly one match for {old!r}')",
            "before = hashlib.sha256(data.encode('utf-8')).hexdigest()",
            "updated = data.replace(old, new, 1)",
            "path.write_text(updated, encoding='utf-8')",
            "after = hashlib.sha256(updated.encode('utf-8')).hexdigest()",
            "print(json.dumps({'before_sha256': before, 'after_sha256': after}))",
            "PY",
        )
    )


async def _lsp_query_symbols_metric(
    handle: SandboxHandle,
    *,
    label: str,
    path: str,
) -> RuntimeCallMetric:
    context = _plugin_context(handle)
    start = time.perf_counter()
    result = await call_plugin(
        context,
        plugin="lsp",
        op="query_symbols",
        payload={
            "query": "current_value",
            "file_path": f"{WORKSPACE_ROOT}/{path}",
        },
        timeout=180,
    )
    elapsed_ms = (time.perf_counter() - start) * 1000.0
    timings = _plugin_timings(result.metadata)
    metric = RuntimeCallMetric(
        label=label,
        op="lsp.query_symbols",
        success=not result.is_error,
        status="ok" if not result.is_error else "error",
        elapsed_ms=elapsed_ms,
        changed_paths=(),
        conflict_reason=result.output if result.is_error else None,
        timings=timings,
    )
    assert not result.is_error, result.output
    payload = json.loads(result.output)
    symbols = payload.get("symbols")
    assert isinstance(symbols, list), payload
    assert any(symbol.get("name") == "current_value" for symbol in symbols), payload
    return metric


def _plugin_context(handle: SandboxHandle) -> ToolExecutionContextService:
    context = ToolExecutionContextService(cwd=Path("/tmp"))
    context["sandbox_id"] = handle.sandbox_id
    context["repo_root"] = WORKSPACE_ROOT
    context["layer_stack_root"] = DEFAULT_LAYER_STACK_ROOT
    context.agent_name = handle.caller.agent_id
    context.agent_run_id = handle.caller.agent_id
    return context


def _plugin_timings(metadata: Mapping[str, Any] | None) -> dict[str, float]:
    timings = (metadata or {}).get("timings")
    if not isinstance(timings, Mapping):
        return {}
    return {str(key): float(value) for key, value in timings.items()}


async def _write_public_burst(
    handle: SandboxHandle,
    *,
    label_prefix: str,
    path_prefix: str,
    count: int,
) -> list[RuntimeCallMetric]:
    metrics: list[RuntimeCallMetric] = []
    for index in range(count):
        path = f"{path_prefix}/{index:03d}.txt"
        result, metric = await timed_call(
            f"{label_prefix}_{index:03d}",
            handle.tool.write_file(
                path,
                f"burst-{index:03d}\n",
                description=f"{label_prefix} {index:03d}",
            ),
        )
        assert_committed(result, path=path)
        metrics.append(metric)
    return metrics


def _auto_squash_record_count(metrics: Sequence[RuntimeCallMetric]) -> int:
    return sum(
        1
        for metric in metrics
        if "layer_stack.auto_squash.total_s" in metric.timings
    )


def _last_metric_auto_squashed(metrics: Sequence[RuntimeCallMetric]) -> bool:
    if not metrics:
        return False
    return "layer_stack.auto_squash.total_s" in metrics[-1].timings


def _require_auto_squash_records(
    case: str,
    metrics: Sequence[RuntimeCallMetric],
    *,
    expected_count: int | None = None,
) -> list[dict[str, object]]:
    records = []
    for metric in metrics:
        timings = metric.timings
        if "layer_stack.auto_squash.total_s" not in timings:
            continue
        record = {
            "label": metric.label,
            "total_ms": round(float(timings["layer_stack.auto_squash.total_s"]) * 1000, 3),
            "max_depth": int(timings["layer_stack.auto_squash.max_depth"]),
            "depth_before": int(timings["layer_stack.auto_squash.depth_before"]),
            "depth_after": int(timings["layer_stack.auto_squash.depth_after"]),
            "manifest_version": int(timings["layer_stack.auto_squash.manifest_version"]),
        }
        assert record["max_depth"] == AUTO_SQUASH_MAX_DEPTH, record
        assert record["depth_before"] > AUTO_SQUASH_MAX_DEPTH, record
        assert record["depth_after"] <= AUTO_SQUASH_MAX_DEPTH, record
        assert "layer_stack.auto_squash.raced" not in timings, timings
        records.append(record)
    assert records, f"{case}: no explicit layer_stack.auto_squash timing record"
    if expected_count is not None:
        assert len(records) == expected_count, (case, records)
    return records


async def _assert_clean_layer_storage(handle: SandboxHandle) -> Mapping[str, object]:
    metrics = await handle.tool.layer_metrics()
    assert int(metrics["orphan_layer_count"]) == 0, metrics
    assert int(metrics["missing_layer_count"]) == 0, metrics
    assert int(metrics["staging_dirs"]) == 0, metrics
    return metrics


async def _audit_cursor(handle: SandboxHandle) -> int:
    snapshot = await daemon_client_mod.call_daemon_api(
        handle.sandbox_id,
        "api.audit.snapshot",
        {},
        timeout=30,
    )
    assert snapshot.get("success") is True, snapshot
    daemon = snapshot["snapshot"]["daemon"]
    return int(daemon["next_seq"]) - 1


async def _require_squash_audit_events(
    handle: SandboxHandle,
    after_seq: int,
    *,
    expected_completed: int | None = None,
) -> list[dict[str, object]]:
    pulled = await daemon_client_mod.call_daemon_api(
        handle.sandbox_id,
        "api.audit.pull",
        {"after_seq": after_seq, "limit": 10_000},
        timeout=60,
    )
    assert pulled.get("success") is True, pulled
    events = [
        event
        for event in pulled.get("events", [])
        if str(event.get("type", "")).startswith("layer_stack.squash_")
    ]
    triggered = [event for event in events if event.get("type") == "layer_stack.squash_triggered"]
    completed = [event for event in events if event.get("type") == "layer_stack.squash_completed"]
    failed = [event for event in events if event.get("type") == "layer_stack.squash_failed"]
    assert triggered, events
    assert completed, events
    assert not failed, events
    assert len(triggered) == len(completed), events
    if expected_completed is not None:
        assert len(completed) == expected_completed, events
    for event in triggered:
        payload = event["payload"]["layer_stack"]
        assert payload["squash_trigger_reason"] == "post_publish_depth", event
        assert int(payload["squash_input_layers"]) > AUTO_SQUASH_MAX_DEPTH, event
    for event in completed:
        payload = event["payload"]["layer_stack"]
        assert int(payload["squash_input_layers"]) > AUTO_SQUASH_MAX_DEPTH, event
        assert int(payload["squash_result_layers"]) <= AUTO_SQUASH_MAX_DEPTH, event
        assert str(payload["manifest_root_hash"]), event
    return events


async def _iws_enter(handle: SandboxHandle, agent_id: str) -> dict[str, object]:
    return await daemon_client_mod.call_daemon_api(
        handle.sandbox_id,
        "api.isolated_workspace.enter",
        {
            "agent_id": agent_id,
            "layer_stack_root": DEFAULT_LAYER_STACK_ROOT,
        },
        timeout=180,
    )


async def _prepare_isolated_workspace_runtime(handle: SandboxHandle) -> None:
    result = await handle.raw_exec(
        handle.sandbox_id,
        (
            "set -e; "
            "if command -v ip >/dev/null 2>&1 && command -v nft >/dev/null 2>&1; "
            "then exit 0; fi; "
            "export DEBIAN_FRONTEND=noninteractive; "
            "apt-get update >/dev/null; "
            "apt-get install -y --no-install-recommends iproute2 nftables >/dev/null"
        ),
        timeout=240,
    )
    assert result.exit_code == 0, result.stderr or result.stdout
    result = await handle.raw_exec(
        handle.sandbox_id,
        (
            "set -e; "
            "grep -q '^EOS_ISOLATED_WORKSPACE_ENABLED=' /etc/environment "
            "2>/dev/null || echo 'EOS_ISOLATED_WORKSPACE_ENABLED=true' "
            ">> /etc/environment; "
            "sed -i '/^EOS_ISOLATED_WORKSPACE_UPPERDIR_BYTES=/d' "
            "/etc/environment 2>/dev/null || true; "
            "echo 'EOS_ISOLATED_WORKSPACE_UPPERDIR_BYTES=67108864' "
            ">> /etc/environment; "
            "mount -o remount,rw /sys/fs/cgroup 2>/dev/null || true; "
            "pkill -f '^.*python.*-m sandbox\\.daemon' || true; "
            "rm -f /tmp/eos-sandbox-runtime/runtime.sock "
            "/tmp/eos-sandbox-runtime/runtime.env"
        ),
        timeout=30,
    )
    assert result.exit_code == 0, result.stderr or result.stdout
    daemon_client_mod.invalidate_daemon_tcp_endpoint(handle.sandbox_id)


async def _iws_exit(handle: SandboxHandle, agent_id: str) -> dict[str, object]:
    return await daemon_client_mod.call_daemon_api(
        handle.sandbox_id,
        "api.isolated_workspace.exit",
        {"agent_id": agent_id},
        timeout=180,
    )


def _case_row(
    name: str,
    *,
    metrics: Sequence[RuntimeCallMetric],
    squash_records: Sequence[Mapping[str, object]],
    audit_events: Sequence[Mapping[str, object]],
    layer_metrics: Mapping[str, object],
    extra: Mapping[str, object] | None = None,
) -> dict[str, object]:
    wall_ms = [metric.elapsed_ms for metric in metrics]
    changed_path_count = sum(len(metric.changed_paths) for metric in metrics)
    row: dict[str, object] = {
        "schema": "sandbox.live_e2e.auto_squash_edge_case.v1",
        "name": name,
        "success": True,
        "call_count": len(metrics),
        "changed_path_count": changed_path_count,
        "wall_p50_ms": round(percentile(wall_ms, 50), 3),
        "wall_p95_ms": round(percentile(wall_ms, 95), 3),
        "wall_max_ms": round(max(wall_ms, default=0.0), 3),
        "auto_squash_count": len(squash_records),
        "auto_squash": list(squash_records),
        "squash_audit_event_types": [str(event.get("type")) for event in audit_events],
        "squash_audit_counts": _squash_audit_counts(audit_events),
        "squash_audit_completed": _completed_squash_audit_payloads(audit_events),
        "layer_metrics": dict(layer_metrics),
    }
    if extra:
        row["extra"] = dict(extra)
    return row


def _squash_audit_counts(events: Sequence[Mapping[str, object]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for event in events:
        event_type = str(event.get("type"))
        counts[event_type] = counts.get(event_type, 0) + 1
    return counts


def _completed_squash_audit_payloads(
    events: Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    payloads = []
    for event in events:
        if event.get("type") != "layer_stack.squash_completed":
            continue
        payload = event["payload"]["layer_stack"]
        payloads.append(
            {
                "input_layers": int(payload["squash_input_layers"]),
                "result_layers": int(payload["squash_result_layers"]),
                "manifest_root_hash": str(payload["manifest_root_hash"]),
            }
        )
    return payloads


def _write_autosquash_artifact(
    case: str,
    rows: Sequence[Mapping[str, object]],
) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    artifact = (
        Path.cwd()
        / ".omc"
        / "results"
        / f"live-e2e-auto-squash-edge-{case}-{stamp}.jsonl"
    )
    artifact.parent.mkdir(parents=True, exist_ok=True)
    summary = {
        "schema": "sandbox.live_e2e.auto_squash_edge_case.summary.v1",
        "case": case,
        "success": all(bool(row.get("success")) for row in rows),
        "rows": len(rows),
        "auto_squash_count": sum(int(row.get("auto_squash_count", 0)) for row in rows),
    }
    with artifact.open("w", encoding="utf-8") as file:
        for row in (summary, *rows):
            file.write(json.dumps(row, sort_keys=True, separators=(",", ":")))
            file.write("\n")
    return artifact


def _module_content(package: int, module: int) -> str:
    value = package * PROJECT_MODULES_PER_PACKAGE + module
    return (
        "# generated module\n"
        f"VALUE = {value}\n"
        f"def fn_{package:02d}_{module:03d}():\n"
        "    return VALUE\n"
    )


def _shell_lsp_probe_content(value: int) -> str:
    return (
        "from __future__ import annotations\n\n"
        f"VALUE = {value}\n\n"
        "def current_value() -> int:\n"
        "    return VALUE\n\n"
        "def use_current() -> int:\n"
        "    return current_value()\n"
    )
