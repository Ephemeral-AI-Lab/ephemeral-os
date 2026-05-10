# OCC / Layer-Stack Commit Resume Auto-Squash Performance Report

Generated: 2026-05-11

## Executive Summary

`edit_file` is slow in the live SWE-EVO scenario suite because successful edits must publish an OCC changeset into the layer stack. The dominant delay is not text replacement, LSP, or the Daytona command runtime. The dominant delay is waiting for OCC commit completion to resume after layer-stack maintenance, mostly auto-squash.

Across the latest finished scenario runs in `.sweevo_runs/scenario_logs`, `edit_file` spent:

| Timing field | Count | Avg ms | P50 ms | P95 ms | Max ms | Total ms |
|---|---:|---:|---:|---:|---:|---:|
| `api.edit.total_s` | 97 | 913.6 | 475.9 | 2275.0 | 2304.6 | 88614.6 |
| `api.edit.occ_apply_s` | 92 | 909.9 | 462.5 | 2247.3 | 2281.4 | 83708.2 |
| `occ.apply.commit_resume_wait_s` | 92 | 895.8 | 443.2 | 2219.5 | 2276.9 | 82414.1 |
| `layer_stack.auto_squash.total_s` | 72 | 1090.0 | 1209.7 | 2179.6 | 2220.5 | 78479.6 |

The direct edit work was negligible by comparison:

| Timing field | Count | Avg ms | P50 ms | P95 ms | Total ms |
|---|---:|---:|---:|---:|---:|
| `api.edit.snapshot_read_s` | 97 | 4.8 | 1.5 | 16.8 | 462.1 |
| `api.edit.derive_bytes_s` | 97 | 0.0 | 0.0 | 0.0 | 0.6 |
| `occ.prepare.total_s` | 92 | 31.5 | 1.4 | 184.9 | 2900.1 |

The root cause is therefore on the post-publish layer-stack maintenance path: edits publish layers fast enough, but callers wait while auto-squash work is performed or contended.

## Data Set

The report uses the latest `status=finished` run directory for each scenario under `.sweevo_runs/scenario_logs`:

| Scenario | Run |
|---|---|
| `correctness_testing` | `20260510T172656Z_436e70e88d42` |
| `full_case_user_input` | `20260510T171357Z_b2fed3a1680f` |
| `full_stack_adversarial` | `20260510T171649Z_93d68e79bc5b` |
| `pipeline.attempt_budget_exhausted` | `20260510T171331Z_64c034ae805f` |
| `pipeline.attempt_retry_evaluator_failure` | `20260510T172729Z_eb135d74eaf9` |
| `pipeline.dependency_dag_mixed` | `20260510T171307Z_804b7292422b` |
| `pipeline.dependency_dag_serial` | `20260510T172739Z_39eb399e637b` |
| `pipeline.episodic_continuation` | `20260510T172720Z_b16fc6a4675d` |
| `pipeline.generator_failure_quiescence` | `20260510T171320Z_fea97bb98e6c` |
| `pipeline.initial_mission` | `20260510T172712Z_7d06defc506e` |
| `planner_validation.duplicate_local_id` | `20260510T171350Z_bdf4d49f47e7` |
| `sandbox.occ_concurrent_conflicts` | `20260510T171338Z_515c4869febb` |

The aggregate public tool latencies from those runs were:

| Tool | Count | Errors | Avg ms | P50 ms | P95 ms | Max ms | Total ms |
|---|---:|---:|---:|---:|---:|---:|---:|
| `read_file` | 173 | 1 | 512.0 | 466.7 | 791.8 | 1390.6 | 88580.7 |
| `write_file` | 185 | 0 | 1381.9 | 579.8 | 3192.5 | 3542.6 | 255649.1 |
| `edit_file` | 97 | 5 | 1420.7 | 966.0 | 2800.5 | 2828.0 | 137807.9 |
| `shell` | 274 | 3 | 1199.0 | 1120.2 | 1818.1 | 2578.2 | 328530.2 |

## Edit File Path

The current edit path is:

```text
tools/sandbox_toolkit/edit_file.py
  -> sandbox.api.tool.edit.edit_file(...)
  -> runtime daemon handler/tools/edit.py
     -> acquire snapshot lease
     -> read current file bytes from layer stack
     -> apply exact search/replace
     -> prepare single-path OCC changeset
     -> commit prepared changeset through OCC client
     -> project changeset result and timings
```

The critical code is in `backend/src/sandbox/runtime/daemon/handler/tools/edit.py`:

- Lines 69-78 acquire the snapshot lease and read file bytes.
- Lines 88-103 apply the text edit.
- Lines 105-116 prepare the single-path OCC changeset.
- Lines 117-122 call `services.occ_client.commit_prepared_changeset(...)`.
- Lines 132-137 record `api.edit.occ_apply_s`.

That means every successful in-workspace edit is a persisted mutation. Even when the text replacement takes microseconds, the call still waits for OCC commit and layer publication semantics.

## OCC Commit and Auto-Squash Path

The relevant OCC path is in `backend/src/sandbox/occ/service.py`:

- `AUTO_SQUASH_MAX_DEPTH = 32`.
- `commit_prepared(...)` commits through the serial merger, then calls `_auto_squash_after_publish_sync(...)` before returning.
- `_auto_squash_after_publish_sync(...)` reads the active manifest and runs `squash(max_depth=32)` when manifest depth is greater than 32.
- `_wrap_commit_result(...)` reports `occ.apply.commit_resume_wait_s`, `occ.apply.commit_s`, and `occ.apply.total_s`.

Measured auto-squash trigger depth:

| Tool | Auto-squash count | Depth before min | Depth before p50 | Depth before max | Raced count |
|---|---:|---:|---:|---:|---:|
| `edit_file` | 72 | 33 | 35 | 42 | 46 |
| `write_file` | 143 | 33 | 34 | 44 | 69 |
| `shell` | 6 | 33 | 33 | 34 | 0 |

This explains the shape of the latency: once the layer stack exceeds depth 32, many mutation calls pay squash or squash-race wait costs on the user-facing path.

## Why Shell Can Look Faster

The shell path is structurally different:

```text
sandbox.api.shell
  -> prepare workspace snapshot
  -> run command in workspace replacement mount
  -> capture upperdir changes
  -> if no typed changes: return empty changeset
  -> if typed changes exist: apply OCC changeset
```

The no-change fast path is in `backend/src/sandbox/runtime/daemon/service/shell_runner.py`:

- Lines 65-75 prepare the snapshot.
- Lines 85-91 run the command.
- Lines 93-105 capture upperdir changes.
- Lines 174-180 return an empty `ChangesetResult` when there are no typed changes.
- Lines 187-192 apply OCC only when typed changes exist.

In the same run set, shell timings were:

| Timing field | Count | Avg ms | P50 ms | P95 ms | Max ms | Total ms |
|---|---:|---:|---:|---:|---:|---:|
| `api.shell.dispatch_total_s` | 274 | 1198.8 | 1120.0 | 1817.9 | 2577.8 | 328460.2 |
| `api.shell.total_s` | 274 | 631.1 | 608.0 | 808.5 | 1609.9 | 172908.5 |
| `command_exec.run_command_s` | 274 | 369.8 | 377.8 | 428.8 | 479.4 | 101324.5 |
| `api.shell.overlay_s` | 274 | 394.3 | 402.4 | 452.8 | 501.7 | 108026.9 |
| `command_exec.occ_apply_s` | 274 | 2.9 | 0.0 | 6.1 | 191.8 | 790.1 |
| `layer_stack.auto_squash.total_s` | 6 | 92.3 | 77.5 | 160.4 | 160.4 | 553.9 |

Shell appears faster because most shell calls in the scenario suite are read-only or produce no workspace changes. Those calls still pay command execution and mount/capture overhead, but they usually do not pay OCC commit or auto-squash. `edit_file` always mutates on success, so it pays the mutation path every time.

## Scenario Concentration

The slow `edit_file` behavior is concentrated in `full_case_user_input`:

| Scenario | Edit count | Tool total ms | P50 ms | P95 ms | Auto-squash count | Auto-squash total ms | Resume wait total ms |
|---|---:|---:|---:|---:|---:|---:|---:|
| `correctness_testing` | 1 | 471.7 | 471.7 | 471.7 | 0 | 0.0 | 0.3 |
| `full_case_user_input` | 86 | 132533.2 | 1090.1 | 2800.5 | 71 | 78401.1 | 82332.7 |
| `full_stack_adversarial` | 9 | 4358.1 | 474.5 | 562.4 | 1 | 78.4 | 80.8 |
| `sandbox.occ_concurrent_conflicts` | 1 | 445.0 | 445.0 | 445.0 | 0 | 0.0 | 0.3 |

This matters because it shows `edit_file` is not intrinsically slow in every workload. It becomes slow when repeated writes and edits push layer-stack depth past the squash threshold and force maintenance into the mutation critical path.

## Root Cause

The root cause is synchronous post-commit layer-stack maintenance in the OCC mutation path.

More precisely:

1. `edit_file` successful calls always produce a workspace mutation.
2. The mutation path commits through OCC and publishes a layer.
3. After publish, `OccService.commit_prepared(...)` invokes auto-squash when active manifest depth exceeds 32.
4. The caller waits for the post-publish auto-squash path before receiving the tool result.
5. In high-mutation scenarios, many calls cross the depth threshold, so the user-facing edit latency becomes dominated by `occ.apply.commit_resume_wait_s` and `layer_stack.auto_squash.total_s`.

This is why the slow part is waiting for OCC/layer-stack commit resume, mostly auto-squash.

## Safety and Behavior Preservation

This report did not change runtime code. Any follow-up optimization must be treated as behavior-preserving unless explicitly approved otherwise.

Behavior-preserving means:

- Public tool payloads remain compatible: `status`, `changed_paths`, `conflict_reason`, `cwd`, `file_path`, `bytes_written`, `applied_edits`, shell `exit_code`, `stdout`, and `stderr` keep the same meaning.
- OCC conflict semantics remain unchanged: stale base hashes still reject, missing anchors still produce edit conflicts, and disjoint writes still follow the current serial-merger rules.
- Visibility semantics remain unchanged: once a successful `edit_file`, `write_file`, or mutating `shell` call returns, later reads and shell commands must see the committed content.
- Snapshot lease safety remains unchanged: a squash must never delete or rewrite layer data that an active lease can still address.
- Multi-path shell capture atomicity remains unchanged: multi-file shell captures must still commit atomically or reject atomically.
- Fail-closed behavior remains unchanged: commit failures must still fail the tool call. If auto-squash failure currently propagates through the synchronous path, moving squash out-of-band would change the error surface unless that behavior is deliberately preserved or explicitly accepted.
- Timing metadata may add fields, but existing timing keys should keep their current meaning so reports remain comparable.

The safest implementation order is:

1. Add tests and instrumentation only, with no runtime behavior change.
2. Introduce any non-blocking or debounced squash path behind an opt-in flag that is disabled by default.
3. Run the current scenario suite and targeted OCC/layer-stack tests with the flag disabled to prove baseline behavior is unchanged.
4. Enable the flag only in a focused performance test environment and compare tool payloads, committed file contents, conflicts, and manifest visibility against the synchronous baseline.
5. Promote the new path only after behavioral equivalence is proven under read-after-write, stale-edit, concurrent-disjoint-write, multi-path-shell-capture, and active-lease-during-squash cases.

Risk areas to explicitly test before changing defaults:

| Proposed optimization | Behavior risk | Required guard |
|---|---|---|
| Async auto-squash | Squash failure may no longer fail the initiating tool call. | Keep default synchronous, or define a separate maintenance-error contract before defaulting async. |
| Debounced squash | Manifest depth can remain above 32 longer than today. | Prove readers, writers, and cleanup tolerate temporary depth growth. |
| Coalesced squash worker | Concurrent publishes may race with compaction. | Use CAS-safe manifest updates and retry/skip semantics that preserve committed layers. |
| Background cleanup | Active leases may reference older layers. | Retain lease-referenced layers until all leases release. |

## Verification and Acceptance Criteria

The verification plan has two gates:

1. Baseline behavior gate: proves the current synchronous behavior remains unchanged.
2. Opt-in performance gate: proves any new async or debounced squash mode is behavior-equivalent before it can become a default.

No optimization should be considered acceptable if it passes the performance gate but fails the baseline behavior gate.

### Existing Tests That Must Run

Run these before any code change and again after the change with the default behavior unchanged:

| Scope | Command | Acceptance criteria |
|---|---|---|
| Scenario registry and protocol conformance | `uv run pytest backend/src/live_e2e/tests/test_scenario_suite_imports.py -q` | Every scenario in `SCENARIO_REGISTRY` imports, implements the scenario protocol, and declares an event sequence. |
| OCC auto-squash unit coverage | `uv run pytest backend/tests/unit_test/test_sandbox/test_occ/test_auto_squash.py -q` | Natural OCC publications trigger squash and preserve active lease views. |
| Layer-stack squash and GC unit coverage | `uv run pytest backend/tests/unit_test/test_sandbox/test_layer_stack/test_squash.py backend/tests/unit_test/test_sandbox/test_layer_stack/test_squash_gc.py backend/tests/unit_test/test_sandbox/test_layer_stack/test_lease_pinning.py -q` | Squash preserves active views, delete semantics, leased layers, and GC safety. |
| Public API natural squash live sandbox coverage | `uv run pytest backend/tests/live_e2e_test/sandbox/layer_stack_overlay_occ/test_workspace_base_shell_lease_squash.py::test_public_mutations_naturally_trigger_squash_and_keep_workspace_view -q` | Public writes naturally trigger auto-squash, active shell lease sees its frozen view, active view sees new writes, and final manifest depth is within `AUTO_SQUASH_MAX_DEPTH`. |
| Focused scenario suite | `uv run pytest backend/src/live_e2e/tests/sweevo/test_focused_scenarios.py -q` | All focused live scenarios complete with expected status, event counts, graph shape, and no unexpected sandbox failures. |
| Current high-mutation hotspot | `uv run pytest backend/src/live_e2e/tests/sweevo/test_full_case_user_input.py::test_full_case_user_input_runs_dynamic_verifier_dag -q` | `full_case_user_input` finishes `done`, emits sandbox monitor events, includes write/edit/read/shell calls, and leaves verified `/testbed` state. |
| Full subsystem matrix | `uv run pytest backend/src/live_e2e/tests/sweevo/test_full_stack_adversarial.py::test_full_stack_adversarial_runs_agent_tool_script_matrix -q` | `full_stack_adversarial` finishes `done`, records OCC, overlay, layer-stack, LSP, recursive mission, and final reconciliation evidence. |

Live scenario commands require the normal live environment: Daytona healthy, `EPHEMERALOS_DATABASE_URL` set, and the SWE-EVO workspace fixture available.

### Existing Scenario Coverage

The current live scenario suite should continue to run because it protects behavior around this performance path:

| Scenario | Existing test | Why it matters |
|---|---|---|
| `correctness_testing` | `backend/src/live_e2e/tests/sweevo/test_correctness.py` | Basic public `write_file`, `read_file`, `edit_file`, and `shell` correctness. Verifies final content survives the shell/OCC/squash boundary. |
| `sandbox.occ_concurrent_conflicts` | `backend/src/live_e2e/tests/sweevo/test_focused_scenarios.py` | Focused OCC conflict behavior: stale edit conflict, batch edit, shell mutation, and expected conflict event coverage. |
| `full_stack_adversarial` | `backend/src/live_e2e/tests/sweevo/test_full_stack_adversarial.py` | Full subsystem matrix with OCC, overlay, layer-stack, LSP, monitor events, and final workspace verification. |
| `full_case_user_input` | `backend/src/live_e2e/tests/sweevo/test_full_case_user_input.py` | Current real high-mutation hotspot where most edit auto-squash delay was observed. |

These scenarios are necessary, but they are not sufficient as the only regression guard for an async or debounced squash change. `full_case_user_input` is broad and noisy, while `full_stack_adversarial` covers layer-stack squash but is not a dedicated commit-resume performance regression.

### Scenario That Should Be Added

Add a focused live E2E scenario before enabling any non-synchronous squash path by default:

| Item | Value |
|---|---|
| Scenario key | `sandbox.auto_squash_commit_resume` |
| Scenario file | `backend/src/live_e2e/scenarios/sandbox/auto_squash_commit_resume.py` |
| Test file | `backend/src/live_e2e/tests/sweevo/test_auto_squash_commit_resume.py` |
| Registry update | Add `sandbox.auto_squash_commit_resume` to `SCENARIO_REGISTRY`. |
| Purpose | Isolate the edit/write mutation critical path that crosses `AUTO_SQUASH_MAX_DEPTH` and prove behavior equivalence while measuring commit resume wait. |

The scenario should drive public tools only, not raw provider exec:

1. Seed `/testbed/.ephemeralos/sweevo-mock/auto_squash_commit_resume/`.
2. Perform at least `AUTO_SQUASH_MAX_DEPTH + 4` public `write_file` calls to naturally trigger auto-squash.
3. Perform several public `edit_file` calls after the threshold is crossed.
4. Interleave `read_file` checks against the first, middle, last, and edited files.
5. Run a `shell` readback from `/testbed` to prove shell sees the same committed state.
6. Execute one intentional stale or missing-anchor edit conflict and assert the same conflict surface as today.
7. Write a summary artifact in the sandbox with final file contents, changed paths, observed conflict reason, manifest-depth evidence, and per-tool timing keys.

The paired test should assert:

- `report.task_center_status == "done"`.
- `SANDBOX_LAYER_STACK_LAYERS_SQUASHED`, `SANDBOX_OCC_CHANGESET_RECEIVED`, and `SANDBOX_OCC_CHANGES_COMMITTED` appear in both in-memory events and `sandbox_events.jsonl`.
- At least one tool result includes `layer_stack.auto_squash.total_s`.
- At least one tool result includes `occ.apply.commit_resume_wait_s`.
- `layer_stack.auto_squash.depth_before > 32` appears in timing metadata.
- Final `read_file` and final `shell` readback agree on committed contents.
- The intentional conflict has the same `status`, `conflict_reason`, `changed_paths`, and `is_error` shape as the current synchronous path.
- No unexpected tool errors occur beyond the intentional conflict.

### Opt-In Performance Acceptance

If an async or debounced squash mode is added behind a flag, the new scenario must run twice:

| Mode | Expected result |
|---|---|
| Default synchronous mode | Behavior and timings match the current contract. This is the compatibility baseline. |
| Opt-in experimental mode | Final contents, conflict results, changed paths, event sequence, and read-after-write visibility match the synchronous baseline. |

The performance acceptance criteria for the opt-in mode should be:

- `api.edit.derive_bytes_s` and `api.edit.snapshot_read_s` remain small and do not regress materially.
- `occ.apply.commit_queue_wait_s` and `occ.apply.commit_worker_s` do not increase enough to indicate wider serialization.
- User-facing edit latency is no longer dominated by `layer_stack.auto_squash.total_s`.
- In the focused scenario, `occ.apply.commit_resume_wait_s` p95 should drop by at least 50 percent versus the synchronous baseline, or stay below 500 ms on the same machine.
- If a squash job is deferred, a later metric or event must prove the maintenance completed or safely skipped due to a race.

These performance criteria are not a license to change behavior. If the final contents, conflict shape, read-after-write visibility, or lease safety differ, the optimization fails even if latency improves.

## Non-Causes

These are measurable but not the primary bottleneck:

| Candidate | Evidence |
|---|---|
| Text replacement | `api.edit.derive_bytes_s` total was 0.6 ms across 97 edit calls. |
| Snapshot file read | `api.edit.snapshot_read_s` total was 462.1 ms across 97 edit calls. |
| OCC preparation | `occ.prepare.total_s` total was 2900.1 ms across 92 successful edit commits. |
| Shell overlay runtime | Shell overlay is visible, but shell usually avoids persisted OCC mutation; edit cannot. |
| LSP | LSP tool calls were separate and low count; they do not explain edit latency. |

## Recommendations

### 1. Move auto-squash off the user-facing commit path

The highest-impact possible change is to stop awaiting full auto-squash before returning from `commit_prepared(...)`. This must not be enabled by default until the behavior-preservation requirements above are satisfied. The safe version is an opt-in experiment first: publish the layer, return the committed result, and schedule squash as workspace maintenance only when the experiment flag is enabled.

Required invariants:

- Published manifest remains valid immediately after commit.
- Active manifest updates remain CAS-safe.
- Snapshot leases continue to point at valid layer data until released.
- A running squash must not invalidate a reader or a concurrent commit prepared from an older manifest.
- If squash races with a newer publish, it should retry, skip, or record a raced metric without corrupting the active manifest.
- Default behavior must remain synchronous until tests prove that changing the wait policy does not alter observable tool semantics.

### 2. Debounce and coalesce squash work per workspace

The current data shows repeated squash attempts once depth exceeds 32. A per-workspace squash worker could coalesce multiple triggers into one maintenance pass, but this is behavior-safe only if the existing synchronous path remains the default while the worker is validated.

Target behavior:

- If no squash is running and depth exceeds threshold, enqueue one squash job.
- If squash is already running, record that squash is pending and return the mutation result.
- After the running squash finishes, re-check active depth once and run another pass only if still needed.

### 3. Keep commit serialization narrow

The commit worker itself is small:

| Timing field | Edit total ms |
|---|---:|
| `occ.apply.commit_queue_wait_s` | 395.8 |
| `occ.apply.commit_worker_s` | 767.3 |
| `occ.apply.commit_resume_wait_s` | 82414.1 |

This suggests the serialization and core commit work are not the dominant issue. Avoid widening the commit lock or bundling maintenance into the lock. The goal is to keep publication fast and make compaction asynchronous.

### 4. Add a regression benchmark around mutation critical-path timings

Add a focused live or daemon-level benchmark that records:

- `api.edit.total_s`
- `api.edit.occ_apply_s`
- `occ.apply.commit_queue_wait_s`
- `occ.apply.commit_worker_s`
- `occ.apply.commit_resume_wait_s`
- `layer_stack.auto_squash.total_s`
- `layer_stack.auto_squash.depth_before`
- `layer_stack.auto_squash.raced`

The benchmark should trigger more than 32 sequential small writes/edits and assert that the p95 user-facing edit latency does not track full squash runtime after the fix.

The benchmark must also assert behavioral equivalence:

- Every committed file has identical final contents under synchronous and experimental squash modes.
- Conflict cases return the same `status`, `conflict_reason`, and changed-path set.
- Read-after-edit and shell-after-edit visibility are unchanged.
- A forced squash failure has the same externally visible behavior unless a new maintenance-error contract is explicitly adopted.

## Expected Impact

If auto-squash is moved out of the synchronous edit commit path, the expected `edit_file` latency floor should look closer to the non-squashing scenarios:

| Scenario | Current edit P50 ms | Current edit P95 ms | Auto-squash involvement |
|---|---:|---:|---|
| `full_stack_adversarial` | 474.5 | 562.4 | 1 auto-squash event |
| `full_case_user_input` | 1090.1 | 2800.5 | 71 auto-squash events |

The realistic goal is not to make edit cheaper than a no-op read. The goal is to keep a single-file edit from inheriting multi-second layer-stack compaction work.

## Conclusion

The performance data supports one main conclusion: the slow path is OCC/layer-stack commit resume, and the concrete cost center is synchronous auto-squash after publish. `edit_file` is slower than shell in the aggregate because edit always commits a workspace mutation, while most shell calls in the suite do not.

The fix should target the auto-squash scheduling model, not the edit string-replacement code, LSP tools, or Daytona shell execution.
