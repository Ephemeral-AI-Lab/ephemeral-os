---
name: live-e2e-plan-executor
description: Use when given an EphemeralOS 3.* live E2E plan file and asked to create or run tests, add complex load-bearing coverage, troubleshoot correctness or performance failures, inspect .sweevo_runs artifacts, enforce O(1) sandbox memory and disk behavior, analyze OCC/layerstack/overlay/workspace latency, and produce an iteration report.
---

# Live E2E Plan Executor

Use this skill to execute a repo-local `docs/plans/3.*` test plan end to end: add missing load-bearing tests, run the plan's verification command, fix correctness/performance bugs, inspect live artifacts, and maintain an iteration report.

This skill is self-contained for the `3.*` plan family. Do not blindly copy older generic sandbox-performance steps. The specific plan file is the source of truth for test shape, harness work, evidence source, command, and performance gates.

## Plan-first intake

Start by reading the provided plan and extracting these sections:

- `Goal`
- `Performance Evaluation Contract`
- `Current Files`, `Current File`, or `Existing Surface`
- `3.x Complex Cases To Add`
- `Required Harness Work`
- `Verification Command`

Then map the plan to current code with `rg` before editing:

```bash
rg -n "<test-name>|<scenario-name>|<probe-mode>|<summary-path>" backend/src/task_center_runner backend/tests docs/plans
```

If the plan is part of a dependency chain, respect the order:

1. `3.0` unit/static contracts.
2. `3.1` layer stack, OCC, and overlay.
3. `3.2` ephemeral workspace.
4. `3.3` background tool.
5. `3.4` isolated workspace.
6. `3.5` plugin and LSP.
7. `3.6` project build.
8. `3.7` full-stack adversarial.

If a later plan fails because an earlier dependency is broken, switch to the earlier plan's narrow gate and record that pivot in the report.

## Plan-family dispatch

Use this table to align the workflow. The plan's own `Verification Command` overrides any generic command.

| Plan | Primary target | Evidence source | Special handling |
| --- | --- | --- | --- |
| `3.0` unit | `backend/tests/unit_test/test_sandbox`, `backend/tests/unit_test/test_plugins` | pytest output and contract greps | No live sandbox claim unless a live command is explicitly added. |
| `3.1` layer/OCC/overlay | `backend/src/task_center_runner/tests/mock/sandbox/layer_stack_occ_overlay/` | `.sweevo_runs/scenario_logs/...` | O(1) lowerdir and latency attribution are the central assertions. |
| `3.2` ephemeral | `backend/src/task_center_runner/tests/mock/sandbox/ephemeral_workspace/` | `.sweevo_runs/scenario_logs/...` | Hybrid routing: default in-workspace file verbs use the direct fast path; shell/search and outside-workspace paths use the overlay pipeline. Cover cancellation, conflict/retry, outside-workspace policy, and 100-call O(1) checks. |
| `3.3` background | `backend/src/task_center_runner/tests/mock/sandbox/background_tool/` | `.sweevo_runs/scenario_logs/...` plus probe summary JSON | Engine-owned background wrapper; never revive shell-job RPC compatibility. Foreground file verbs stay direct unless the plan explicitly invokes a background or shell path. |
| `3.4` isolated | `backend/src/task_center_runner/tests/mock/sandbox/isolated_workspace/` | daemon/container audit JSONL unless routed through scenario harness | Active isolated-workspace file verbs route through the isolated pipeline with pinned lowerdir, discard-on-exit, same-session parallelism, and no OCC publish. |
| `3.5` plugin | `backend/src/task_center_runner/tests/mock/sandbox/plugin/` | `.sweevo_runs/scenario_logs/...` plus plugin probe summaries | Separate READ_ONLY service latency, WRITE_ALLOWED plugin overlay/OCC latency, and normal direct file-verb latency. |
| `3.6` project build | `backend/src/task_center_runner/tests/mock/sandbox/project_build/` | `.sweevo_runs/scenario_logs/...` | Multi-file mixed tools, LSP/search latency after accumulated edits; classify direct file, shell/search overlay, plugin, and isolated samples separately. |
| `3.7` full stack | `backend/src/task_center_runner/tests/mock/sandbox/full_stack/` | `.sweevo_runs/scenario_logs/...` | Whole TaskCenter workflow with the same hybrid routing attribution; synthetic verifier failures can be expected, sandbox internal errors cannot. |

## Current workspace routing contract

For current 3.x plans, do not assume all sandbox tools are wrapped in the ephemeral overlay pipeline.

- Default in-workspace `read_file`, `write_file`, and `edit_file` use the direct layer-stack/OCC fast path. They should be evaluated with per-tool API totals, layer-stack read/write timings, OCC apply timings when present, and zero overlay command resource fields.
- `shell`, search-style tools such as `grep`/`glob`, and file operations outside the workspace root use the ephemeral workspace pipeline and should expose command execution, mount, capture, upperdir, and cleanup timings when the harness records them.
- Active `isolated_workspace` sessions route file verbs through `IsolatedPipeline.run_tool_call`, keep changes in the isolated workspace, avoid OCC publish, and discard on exit.
- Plugin `WRITE_ALLOWED` operations are plugin-owned overlay operations. Do not compare their overlay timings directly against normal `write_file` or `edit_file` direct-path timings.

When a plan mixes these paths, report correctness, O(1) resource behavior, and latency by route as well as by tool family.

## Iteration report

Create or update an append-only report next to the plan:

```text
<plan-stem>-iteration-report.md
```

Example:

```text
docs/plans/3.3-background-tool-live-e2e-plan-iteration-report.md
```

Before every new iteration, read the existing report first. If it exists and was not read, stop and read it before changing tests, code, commands, or audit logs.

Each entry must include:

- timestamp and checkout summary;
- plan path, target files, and dependency pivot if any;
- coverage gaps found against the plan's `3.x Complex Cases To Add`;
- exact tests, probes, or audit fields added;
- exact commands run;
- fresh artifact or isolated-audit paths inspected;
- first failure/stop signal, if any;
- root-cause hypothesis and evidence;
- code/test/audit changes made;
- correctness result;
- performance result with O(1) memory/disk and latency verdicts;
- next iteration entry point.

## Coverage workflow

1. Compare the plan's current surface to the `3.x Complex Cases To Add`.
2. Add only missing load-bearing coverage. Do not duplicate existing tests with weaker smoke cases.
3. Implement `Required Harness Work` before writing assertions that depend on it.
4. Prefer existing probes, fixture shape, scenario registry patterns, and summary JSON paths.
5. Run collect-only for changed test folders before live execution.
6. Run the narrowest test that exercises the new coverage, then the plan's verification command.

A test is load-bearing when it can catch a real regression from the plan:

- repeated N-operation pressure or concurrency;
- multiple tool families in one workflow;
- OCC conflict, retry, cancellation, timeout, or cleanup path;
- plugin/LSP/background/isolated-workspace interaction when named by the plan;
- explicit artifact assertions for lowerdir disk, upperdir growth, run-dir cleanup, and latency attribution.

Do not weaken assertions to make a live run pass. If an assertion is wrong, replace it with a stricter observable contract from the plan or current architecture.

## Running and monitoring

Use the plan's `Verification Command` as written unless local prerequisite checks prove a command flag is unavailable. For iterative runs, prefer `-x`, `--tb=short`, and `--durations=20`; for a full plan gate, preserve the command in the plan.

For 3.1 high-concurrency overlay/OCC work, distinguish total workload size from
active sandbox-tool fanout. Keep `high_concurrency_layerstack_overlay_occ`
bounded to at most 5 active worker lanes unless the user explicitly asks for a
higher bound. Do this in the test/scenario DAG or diagnostic harness, not by
adding a daemon-wide semaphore. Shell latency probes may still use explicit
diagnostic matrices such as 1/5/10, but the recurring high-concurrency live
test should assert the observed overlap cap from `performance_report.json`
samples.

While a live run is active, poll fresh artifacts instead of waiting for a final pytest summary. Resolve the newest relevant run directory under:

```text
.sweevo_runs/scenario_logs/
```

Read, when present:

- `run.json`
- `message.jsonl`
- `sandbox_events.jsonl`
- `metrics.json`
- `performance_report.json`
- `performance_report.md`
- probe summary JSON under `/testbed/.ephemeralos/...` when the harness writes one and exposes it through the test.

For direct isolated-workspace pytest or soak paths, normal `.sweevo_runs` scenario logs may not exist. Use the active daemon/container audit JSONL instead, normally `/tmp/sandbox_isolated_workspace_events.jsonl` inside the active SWE-EVO container or the path from `EOS_ISOLATED_WORKSPACE_AUDIT_PATH`.

Stop the run and fix immediately when a concrete issue appears:

- traceback, terminal failure, internal sandbox error, daemon disconnect, mount failure, missing layer, or stale lowerdir;
- no progress in `message.jsonl` while sandbox events show a blocked phase;
- per-tool p95/max exceeds the plan budget;
- `workspace_tree_exists != 0` or `workspace_tree_bytes != 0` for private namespace lowerdir samples or direct file-operation samples that should not create an overlay workspace tree;
- upperdir/run-dir bytes scale with workspace size or operation count instead of changed bytes, or direct file-operation samples unexpectedly create overlay upperdir/run-dir bytes;
- LSP restarts on normal writes when the plan expects warm refresh/remount;
- background cancellation publishes partial changes;
- isolated workspace publishes through OCC or fails to discard on exit.

## Performance contract

Every live E2E closeout needs separate correctness and performance verdicts.

O(1) resource checks:

- private namespace lowerdir workspace tree remains O(1): max `resource.command_exec.workspace_tree_exists == 0` and max `resource.command_exec.workspace_tree_bytes == 0`;
- lowerdir disk does not grow with operation count, worker count, or workspace size;
- direct file verbs do not create overlay run dirs or nonzero command-resource workspace tree fields;
- upperdir bytes scale with changed bytes only for overlay-backed operations;
- run directories and temporary mounts are cleaned up after publish/cancel/exit;
- repeated create/destroy or repeated tool-call plans do not show monotonic memory or FD growth.

Latency attribution:

- separate tool body time from sandbox plumbing time;
- report p50/p95/max per tool family when samples exist;
- attribute slow calls to named layers when artifacts expose them:
  direct file totals such as `api.read.total_s`, `api.write.total_s`, and
  `api.edit.total_s`; direct layer-stack/OCC timings such as
  `api.read.layer_stack_read_s`, `api.write.layer_stack_write_s`,
  `api.edit.layer_stack_read_s`, `api.edit.layer_stack_write_s`, and OCC
  apply phases; overlay timings such as `command_exec.mount_workspace_s`,
  `run_command_s`, `capture_upperdir_s`,
  `layer_stack.prepare_workspace_snapshot.total_s`, OCC queue/apply phases,
  overlay acquire/mount/release, plugin service dispatch, LSP refresh/remount,
  and `ephemeral_workspace`, `isolated_workspace`, or `main_workspace` lifecycle phases.

Do not require overlay mount/capture timings for normal direct `read_file`, `write_file`, or `edit_file` calls. Missing overlay timings are expected on that fast path.

If the artifacts cannot answer a required timing or resource question, add narrow structured audit fields/logs. Prefer stable JSON-compatible names such as `operation_id`, `agent_id`, `workspace_mode`, `tool_name`, `phase_ms`, `upperdir_bytes`, `workspace_tree_bytes`, `manifest_version`, `changed_paths_count`, and `run_dir_removed`.

## Root-cause loop

For each failure or regression:

1. Record the first concrete signal in the iteration report.
2. Read the exact error and artifact lines around it.
3. Reproduce with the smallest command or probe.
4. Trace backward across the failing boundary: test/probe -> tool wrapper -> workspace dispatch -> direct file fast path, isolated pipeline, plugin service, or overlay/layerstack/OCC -> daemon/provider.
5. Add temporary or permanent audit only when it answers a boundary question.
6. State one root-cause hypothesis.
7. Make one targeted fix.
8. Rerun the narrow failing test.
9. Re-read fresh artifacts before declaring success.
10. Append the result and remaining risk to the iteration report.

If three fixes fail, stop changing code and reassess the architecture or test assumption before proceeding.

## Final response requirements

When the task is done, report:

- plan path and iteration report path;
- tests/probes/audit fields added or changed;
- exact commands run and pass/fail status;
- fresh artifact directories or audit files inspected;
- correctness verdict;
- O(1) memory/disk verdict;
- latency verdict by layer/tool family;
- remaining risk or skipped live scope.

Do not claim performance success from stale artifacts or from a run that did not exercise the plan's live path.
