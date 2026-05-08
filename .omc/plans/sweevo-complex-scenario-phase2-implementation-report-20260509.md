# SWE-EVO Complex Scenario Phase 2 - Implementation Report

**Date:** 2026-05-09
**Plan:** `.omc/plans/sweevo-complex-scenario-phase2-implementation-plan-20260508.md`
**Status:** implemented and live-Daytona verified

## Summary

Phase 2 now runs a deterministic but agent-shaped SWE-EVO scenario on the real
TaskCenter runtime and the real Daytona sandbox. The scenario uses the exact
rendered user prompt from `build_sweevo_user_prompt(...)`, decomposes it into a
dynamic package DAG, executes multiple mock agents with real sandbox tools, and
records replayable audit artifacts.

The important framing fix is also implemented: the entry executor is not a
Mission or Episode. It is the top-level entry task. It can complete directly
through a terminal submission or call `request_mission_solution` to create the
first delegated Mission. Therefore `mission_01` is delegated work, not the
entry executor task.

## What Landed

### Entry Executor Lifecycle

| Area | Result |
|---|---|
| Entry controller | Removed entry Mission/Episode ownership. The controller now owns only the entry task lifecycle and run finalization. |
| Entry coordinator | Starts the entry executor as a task with `mission_id=None` and `episode_id=None`. |
| Mission starter | Entry-mode callers can request a delegated mission with no parent attempt. |
| Context engine | `ContextScope` and `ContextRefs` allow `mission_id=None` for entry execution. |

### Full-Case SWE-EVO Scenario

| Area | Result |
|---|---|
| Scenario | Added `full_case_user_input`, driven from the already-rendered user prompt. |
| User-input parser | Added a prompt parser that extracts requirement items, subsystem labels, risk, weights, and work packages without doing a second CSV lookup. |
| Dynamic DAG | Planner produces package waves, verifier gates, partial-plan continuations, and a recursive delegated mission. |
| Verifier role | Mock squad now dispatches verifier agents and uses verifier submission tools. |
| Recursive mission | Oversized work delegates through `request_mission_solution`; parent verification waits for recursive close evidence. |

### Mock Agent Execution

| Area | Result |
|---|---|
| Agent transcript | `message.jsonl` now records initial `system_message`, `user_message`, full `assistant_message`, tool calls, and tool results. |
| System prompts | Mock squad definitions include non-empty system prompts for entry executor, planner, executor, verifier, and evaluator. |
| Prepared tool scripts | Deterministic scripts call real public tools instead of mutating host-local fixtures. |
| Sandbox tools | Full-case scripts exercise `read_file`, `write_file`, `edit_file`, and `shell`. |
| Conflict probe | The scenario fabricates an expected `edit_file` anchor-miss conflict and records it without failing the run. |

### Daytona `/testbed` Execution

The public sandbox shell cwd now preserves absolute cwd values, so `/testbed`
reaches the daemon instead of being collapsed to `"."`.

The full-case test verifies:

- `api.workspace_binding` reports `workspace_root == "/testbed"`.
- `base_manifest_version >= 1`.
- `read_file` can read `/testbed/.ephemeralos/sweevo-mock/full_case/workspace-proof.txt`.
- `shell` can run with `cwd="/testbed"` and see the same proof file.

### Sandbox Subsystem Monitoring

Tool metadata now carries sandbox timing data, and `stream_bridge` derives
explicit subsystem events from tool completions.

Persisted monitor artifact:

```text
<run_dir>/sandbox_events.jsonl
```

Event families now recorded:

| Event | Meaning |
|---|---|
| `sandbox_layer_stack_lease_acquired` | Snapshot lease / workspace snapshot acquisition happened. |
| `sandbox_layer_stack_layer_created` | A mutation published a new layer. |
| `sandbox_layer_stack_layers_squashed` | Auto-squash ran after layer depth exceeded the configured threshold. |
| `sandbox_overlay_executed` | Shell command executed through the overlay path. |
| `sandbox_occ_changeset_received` | OCC prepare/routing observed a changeset. |
| `sandbox_occ_changes_committed` | OCC commit/apply path completed. |
| `sandbox_conflict_detected` | A guarded mutation conflict was observed. |

## Latest Live Evidence

Latest passing run:

```text
/Users/yifanxu/machine_learning/LoVC/EphemeralOS/.sweevo_runs/scenario_logs/full_case_user_input/20260508T174115Z_0533c516aeef
```

Sandbox:

```text
1b3ad840-6e82-46b0-8e74-edec471e8dc1
```

Entry transcript:

```text
/Users/yifanxu/machine_learning/LoVC/EphemeralOS/.sweevo_runs/scenario_logs/full_case_user_input/20260508T174115Z_0533c516aeef/entry_executor_3f2b914f-a121-4d5e-8956-ddda2761fe87:entry/message.jsonl
```

The entry transcript starts with:

```text
system_message system entry_executor
user_message user entry_executor
text assistant entry_executor
assistant_message assistant entry_executor
tool_call assistant entry_executor request_mission_solution
```

Mission framing:

```text
mission_01_31bf6548-7e05-4e14-ae92-f656b6e1c81b
requested_by_task_id = 3f2b914f-a121-4d5e-8956-ddda2761fe87:entry
first_episode_final_attempt_count = 2
```

This proves `mission_01` is delegated work requested by the entry task and has
attempts. It is not an attempt-less entry Mission.

Tool metrics:

```text
tool_calls_total = 672
tool_errors_total = 2
per_tool = edit_file, read_file, request_mission_solution, shell,
           submit_evaluation_success, submit_execution_success,
           submit_full_plan, submit_partial_plan,
           submit_verification_failure, submit_verification_success,
           write_file
```

The 2 tool errors are expected fabricated conflict probes:

```text
conflict_reason = anchor not found in .ephemeralos/sweevo-mock/full_case/conflict-probe.txt:
expected 1 occurrences of 'missing-anchor\n', found 0
status = aborted_overlap
tool_name = edit_file
```

Sandbox event counts from `sandbox_events.jsonl`:

| Event | Count |
|---|---:|
| `sandbox_conflict_detected` | 2 |
| `sandbox_layer_stack_layer_created` | 188 |
| `sandbox_layer_stack_layers_squashed` | 153 |
| `sandbox_layer_stack_lease_acquired` | 528 |
| `sandbox_occ_changes_committed` | 405 |
| `sandbox_occ_changeset_received` | 188 |
| `sandbox_overlay_executed` | 219 |

Parallel execution proof:

```text
13_executor_...:gen:exec_pkg_config_11 shell
  start=1778262104.657089
  end=1778262106.531369

11_executor_...:gen:exec_pkg_dataframe_09 shell
  start=1778262104.6518369
  end=1778262106.334501
```

The intervals overlap and belong to distinct executor task directories.

## Verification

### Focused unit and smoke checks

```bash
uv run pytest \
  backend/tests/unit_test/test_benchmarks/test_sweevo_sandbox_event_monitor.py \
  backend/tests/unit_test/test_tools/test_sandbox_toolkit/test_write_file.py \
  backend/tests/unit_test/test_tools/test_sandbox_toolkit/test_edit_file.py \
  backend/tests/unit_test/test_tools/test_sandbox_toolkit/test_shell.py \
  -q
# 16 passed, 1 warning
```

```bash
uv run pytest \
  backend/tests/unit_test/test_benchmarks/test_sweevo_mock_agent_execution.py \
  backend/src/benchmarks/sweevo/live_test/tests/test_correctness.py \
  backend/tests/unit_test/test_benchmarks/test_sweevo_sandbox_event_monitor.py \
  -q
# 5 passed, 1 warning
```

### Lint

```bash
uv run ruff check \
  backend/src/benchmarks/sweevo/live_test/audit/events.py \
  backend/src/benchmarks/sweevo/live_test/audit/sandbox_events.py \
  backend/src/benchmarks/sweevo/live_test/audit/stream_bridge.py \
  backend/src/benchmarks/sweevo/live_test/audit/recorder.py \
  backend/src/benchmarks/sweevo/live_test/squad/runner.py \
  backend/src/benchmarks/sweevo/live_test/squad/tool_scripts.py \
  backend/src/benchmarks/sweevo/live_test/tests/test_full_case_user_input.py \
  backend/src/tools/sandbox_toolkit/mutation_result.py \
  backend/src/tools/sandbox_toolkit/write_file.py \
  backend/src/tools/sandbox_toolkit/edit_file.py \
  backend/src/tools/sandbox_toolkit/shell.py \
  backend/src/tools/sandbox_toolkit/read_file.py \
  backend/src/tools/sandbox_toolkit/file_payloads.py \
  backend/tests/unit_test/test_benchmarks/test_sweevo_sandbox_event_monitor.py
# All checks passed
```

### Live Daytona full case

```bash
EOS_SWEEVO_INSTANCE=dask__dask_2023.3.2_2023.4.0 \
  uv run pytest backend/src/benchmarks/sweevo/live_test/tests/test_full_case_user_input.py -q
# 2 passed, 1 warning in 221.47s (0:03:41)
```

The warning is the existing Hypothesis `norecursedirs` collection warning.

## Known Gap: Dedicated `command_exec` Auditor

A dedicated `command_exec` auditor has not landed in this report. Current
monitoring derives command-exec evidence from shell tool metadata and timing
keys such as:

```text
command_exec.prepare_snapshot_s
command_exec.release_snapshot_s
command_exec.occ_apply_s
api.shell.overlay_s
```

That is enough to prove shell execution crossed the command-exec, overlay, OCC,
lease, and layer-stack paths, but it is not yet a first-class command-exec event
stream.

Recommended next implementation slice:

1. Add `backend/src/benchmarks/sweevo/live_test/audit/command_exec_events.py`.
2. Emit explicit events such as:
   - `sandbox_command_exec_snapshot_prepared`
   - `sandbox_command_exec_overlay_invoked`
   - `sandbox_command_exec_capture_applied`
   - `sandbox_command_exec_snapshot_released`
3. Persist those events into either `sandbox_events.jsonl` or a dedicated
   `command_exec_events.jsonl`.
4. Extend `test_full_case_user_input.py` to assert the command-exec event set
   separately from the generic overlay/OCC/layer-stack monitors.

## Status

Phase 2 is implemented and live verified. The remaining request is a dedicated
`command_exec` auditor, which should be handled as the next narrow change rather
than hidden inside the generic sandbox monitor.
