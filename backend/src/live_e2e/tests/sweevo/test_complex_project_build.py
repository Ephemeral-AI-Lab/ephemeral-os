"""Live regression for the complex_project_build scenario (smoke + full).

Asserts the §7 contract from
``.omc/plans/complex-build-from-scratch-layer-stack-projection-verification-plan-20260511.md``:

- ``report.task_center_status == 'done'``
- Tool-call floor (smoke ≥250, full ≥2,000)
- ≥10 ``SANDBOX_LAYER_STACK_LAYERS_SQUASHED`` events (full)
- Required SANDBOX_* events present in events + ``sandbox_events.jsonl``
- Edit:write ratio ≥4×
- LSP saturation: each of 5 LSP tools invoked ≥30 times (full) / ≥3 (smoke)
- Direct ``sandbox.api`` saturation: ≥40 reads / ≥10 batch edits / ≥3 shells (full);
  smaller floor for smoke
- ``/ephemeral-os/.metrics/perf.json`` parses and validates against the v1 schema
- ``pytest`` exit code = 0 inside the projected workspace
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

import pytest

import sandbox.api as sandbox_api
from benchmarks.sweevo.models import SWEEvoInstance
from sandbox.api import ReadFileRequest, SandboxCaller, ShellRequest

from live_e2e.audit.events import EventType
from live_e2e.scenarios import SCENARIO_REGISTRY
from live_e2e.scenarios.sandbox._metrics import PERF_SCHEMA
from live_e2e.squad.complex_project_build_probe import (
    METRICS_PATH,
    WORKSPACE_ROOT,
)
from live_e2e.stores import TaskCenterStoreBundle
from live_e2e.sweevo_adapter import run_sweevo_scenario


pytestmark = pytest.mark.asyncio


_REQUIRED_SANDBOX_EVENTS_FULL = (
    EventType.SANDBOX_LAYER_STACK_LAYERS_SQUASHED,
    EventType.SANDBOX_OCC_CHANGESET_RECEIVED,
    EventType.SANDBOX_OCC_CHANGES_COMMITTED,
    EventType.SANDBOX_CONFLICT_DETECTED,
)
_REQUIRED_SANDBOX_EVENTS_SMOKE = (
    EventType.SANDBOX_OCC_CHANGESET_RECEIVED,
    EventType.SANDBOX_OCC_CHANGES_COMMITTED,
    EventType.SANDBOX_CONFLICT_DETECTED,
)

_LSP_NAMES = (
    "lsp.hover",
    "lsp.find_definitions",
    "lsp.find_references",
    "lsp.query_symbols",
    "lsp.diagnostics",
)


@pytest.mark.skipif(
    not os.environ.get("EPHEMERALOS_DATABASE_URL"),
    reason="EPHEMERALOS_DATABASE_URL not set - live_e2e requires PostgreSQL",
)
@pytest.mark.timeout(900)
async def test_complex_project_build_smoke(
    sweevo_instance: SWEEvoInstance,
    workspace: dict[str, object],
    audit_dir: Path,
    stores: TaskCenterStoreBundle,
) -> None:
    scenario_cls = SCENARIO_REGISTRY["sandbox.complex_project_build_smoke"]
    scenario = scenario_cls()
    sandbox_id = str(workspace["sandbox_id"])
    report = await run_sweevo_scenario(
        scenario,
        instance=sweevo_instance,
        sandbox_id=sandbox_id,
        audit_dir=audit_dir,
        stores=stores,
    )
    await _assert_complex_build_contract(
        report=report,
        sandbox_id=sandbox_id,
        smoke=True,
    )


@pytest.mark.skipif(
    not os.environ.get("EPHEMERALOS_DATABASE_URL"),
    reason="EPHEMERALOS_DATABASE_URL not set - live_e2e requires PostgreSQL",
)
@pytest.mark.skipif(
    not os.environ.get("EPHEMERALOS_RUN_HEAVY_LIVE_E2E"),
    reason="heavy live e2e - opt-in via EPHEMERALOS_RUN_HEAVY_LIVE_E2E=1",
)
@pytest.mark.timeout(2400)
async def test_complex_project_build_full(
    sweevo_instance: SWEEvoInstance,
    workspace: dict[str, object],
    audit_dir: Path,
    stores: TaskCenterStoreBundle,
) -> None:
    scenario_cls = SCENARIO_REGISTRY["sandbox.complex_project_build"]
    scenario = scenario_cls()
    sandbox_id = str(workspace["sandbox_id"])
    report = await run_sweevo_scenario(
        scenario,
        instance=sweevo_instance,
        sandbox_id=sandbox_id,
        audit_dir=audit_dir,
        stores=stores,
    )
    await _assert_complex_build_contract(
        report=report,
        sandbox_id=sandbox_id,
        smoke=False,
    )


async def _assert_complex_build_contract(
    *,
    report,
    sandbox_id: str,
    smoke: bool,
) -> None:
    assert report.task_center_status == "done", report.metrics
    assert report.passed_prompt_inspections, [
        item for item in report.prompt_inspections if not item.passed
    ]
    assert report.passed_sandbox_checks, [
        item for item in report.sandbox_checks if not item.passed
    ]

    # Tool call floor (§7.2).
    tool_call_floor = 250 if smoke else 2000
    assert len(report.tool_calls) >= tool_call_floor, (
        f"tool_calls={len(report.tool_calls)} below floor {tool_call_floor}"
    )

    # Required SANDBOX_* events in memory + jsonl (§7.3, §7.4).
    required = _REQUIRED_SANDBOX_EVENTS_SMOKE if smoke else _REQUIRED_SANDBOX_EVENTS_FULL
    seen_events = {event.type for event in report.events}
    missing_events = sorted(
        event.value for event in required if event not in seen_events
    )
    assert not missing_events, f"missing in-memory events: {missing_events}"

    sandbox_log = report.run_dir / "sandbox_events.jsonl"
    assert sandbox_log.exists()
    logged_events = {
        EventType(row["event_type"])
        for row in _jsonl_rows(sandbox_log)
    }
    missing_logged = sorted(
        event.value for event in required if event not in logged_events
    )
    assert not missing_logged, f"missing persisted events: {missing_logged}"

    if not smoke:
        # ≥10 squash events for full variant (§7.3).
        squash_event_count = sum(
            1
            for event in report.events
            if event.type == EventType.SANDBOX_LAYER_STACK_LAYERS_SQUASHED
        )
        assert squash_event_count >= 10, (
            f"only {squash_event_count} squash events; expected >= 10"
        )

    # Edit:write ratio (§7.11).
    edit_count = sum(1 for c in report.tool_calls if c.tool_name == "edit_file")
    write_count = sum(1 for c in report.tool_calls if c.tool_name == "write_file")
    if write_count == 0:
        assert edit_count > 0
    else:
        ratio = edit_count / write_count
        assert ratio >= 4.0, (
            f"edit:write={ratio:.2f} (edit={edit_count}, write={write_count}) below 4.0"
        )

    # LSP saturation (§7.12).
    lsp_floor = 3 if smoke else 30
    lsp_counts = {
        name: sum(1 for c in report.tool_calls if c.tool_name == name)
        for name in _LSP_NAMES
    }
    weak = [name for name, cnt in lsp_counts.items() if cnt < lsp_floor]
    assert not weak, f"LSP tools below floor {lsp_floor}: {lsp_counts}"

    # Read perf metrics artifact via direct sandbox.api (§7.19–23).
    caller = SandboxCaller(agent_id="complex-project-build-test")
    perf_read = await sandbox_api.read_file(
        sandbox_id,
        ReadFileRequest(path=METRICS_PATH, caller=caller),
    )
    assert perf_read.success and perf_read.exists, (
        f"perf.json missing at {METRICS_PATH}"
    )
    perf = json.loads(perf_read.content)
    assert perf.get("schema") == PERF_SCHEMA
    for top_key in ("tool_use", "layer_stack", "overlay", "occ", "phases"):
        assert top_key in perf, f"perf.json missing {top_key!r}: keys={list(perf.keys())}"

    # §7.20: tool_use.total_calls matches len(report.tool_calls) — minus the
    # framework's own submission calls (entry-executor's
    # submit_execution_handoff, planner's submit_full_plan, executor's
    # submit_execution_success, evaluator's submit_evaluation_success) which
    # the probe does not track. Use the actual toolkit-call count from the
    # report (excluding submission tool names) as ground truth.
    submission_tool_names = {
        "submit_execution_handoff",
        "submit_full_plan",
        "submit_partial_plan",
        "submit_execution_success",
        "submit_execution_failure",
        "submit_evaluation_success",
        "submit_evaluation_failure",
        "submit_verification_success",
        "submit_verification_failure",
    }
    probe_tool_calls = [
        c for c in report.tool_calls if c.tool_name not in submission_tool_names
    ]
    perf_total_calls = int(perf["tool_use"].get("total_calls") or 0)
    # ±5 absorbs the small drift between probe-tracked counters (incremented
    # per ``_call_tool`` invocation in the probe wrappers) and the runner's
    # ``ToolCallRecord`` list (which also captures fixture-internal tool
    # invocations that bypass the probe wrappers — e.g. the entry-executor's
    # prompt-inspection shell calls).
    assert abs(perf_total_calls - len(probe_tool_calls)) <= 5, (
        f"perf.tool_use.total_calls={perf_total_calls} vs "
        f"probe-toolkit-calls={len(probe_tool_calls)} "
        f"(report.tool_calls={len(report.tool_calls)})"
    )

    # §7.23: overlay capture_upperdir cost recorded; shell_calls field present.
    overlay = perf.get("overlay") or {}
    assert int(overlay.get("shell_calls") or 0) > 0, "overlay.shell_calls = 0"

    # §7.22: occ.commit_count matches the SANDBOX_OCC_CHANGES_COMMITTED event
    # count in the persisted jsonl (≥ since the probe's own metrics-write may
    # commit after the snapshot is taken).
    occ_committed_events = sum(
        1
        for row in _jsonl_rows(sandbox_log)
        if row.get("event_type") == EventType.SANDBOX_OCC_CHANGES_COMMITTED.value
    )
    perf_commit_count = int(perf["occ"].get("commit_count") or 0)
    assert occ_committed_events >= perf_commit_count, (
        f"occ.commit_count={perf_commit_count} > "
        f"SANDBOX_OCC_CHANGES_COMMITTED events={occ_committed_events}"
    )
    assert perf_commit_count > 0, "perf.occ.commit_count = 0"

    if not smoke:
        # Layer-stack squash count + max depth before should reflect the
        # natural threshold crossing.
        layer = perf.get("layer_stack") or {}
        assert int(layer.get("squash_count") or 0) >= 10
        assert float(layer.get("max_depth_before") or 0.0) > 32.0

    # §7.15 / §7.16 / §7.17: direct sandbox.api saturation. The probe writes
    # an audit summary at /ephemeral-os/.metrics/summary.json with the
    # per-API-surface counts; assert against the documented floors.
    summary_read = await sandbox_api.read_file(
        sandbox_id,
        ReadFileRequest(
            path=f"{WORKSPACE_ROOT}/.metrics/summary.json",
            caller=caller,
        ),
    )
    assert summary_read.success and summary_read.exists, (
        f"summary.json missing at {WORKSPACE_ROOT}/.metrics/summary.json"
    )
    summary = json.loads(summary_read.content)
    api_calls = summary.get("api_calls") or {}
    api_read_count = int(api_calls.get("read") or 0)
    api_edit_count = int(api_calls.get("edit") or 0)
    api_shell_count = int(api_calls.get("shell") or 0)
    # Smoke floor relaxed (~10 reads / ≥1 edit / ≥1 shell); full enforces the
    # §7.15–17 numbers verbatim.
    if smoke:
        assert api_read_count >= 5, f"api.read_file count={api_read_count}"
        assert api_edit_count >= 1, f"api.edit_file count={api_edit_count}"
        assert api_shell_count >= 1, f"api.shell count={api_shell_count}"
    else:
        assert api_read_count >= 40, f"api.read_file count={api_read_count}"
        assert api_edit_count >= 10, f"api.edit_file count={api_edit_count}"
        assert api_shell_count >= 3, f"api.shell count={api_shell_count}"

    # §7.24: pytest junit XML is well-formed and reports zero failures /
    # zero errors. Parse it directly rather than relying on file presence.
    junit_read = await sandbox_api.read_file(
        sandbox_id,
        ReadFileRequest(
            path=f"{WORKSPACE_ROOT}/.metrics/pytest.xml",
            caller=caller,
        ),
    )
    assert junit_read.success and junit_read.exists, "pytest.xml missing"
    junit_root = ElementTree.fromstring(junit_read.content)
    junit_suite = junit_root
    if junit_root.tag != "testsuite":
        junit_suite = junit_root.find("testsuite")
        if junit_suite is None:
            junit_suite = junit_root
    failures = int(junit_suite.get("failures", "0"))
    errors = int(junit_suite.get("errors", "0"))
    tests = int(junit_suite.get("tests", "0"))
    assert failures == 0, f"pytest junit failures={failures}"
    assert errors == 0, f"pytest junit errors={errors}"
    test_floor = 5 if smoke else 30
    assert tests >= test_floor, f"pytest junit tests={tests} (floor {test_floor})"

    # Final shell readback to confirm pytest junit XML is committed (legacy
    # §7.6 / §7.26 file-existence check; §7.24 above already verifies content).
    shell_result = await sandbox_api.shell(
        sandbox_id,
        ShellRequest(
            command=f"test -s {WORKSPACE_ROOT}/.metrics/pytest.xml && echo OK",
            cwd=WORKSPACE_ROOT,
            timeout=60,
            caller=caller,
            description="complex_project_build pytest.xml readback",
        ),
    )
    assert shell_result.success
    assert shell_result.exit_code == 0
    assert "OK" in shell_result.stdout


def _jsonl_rows(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
