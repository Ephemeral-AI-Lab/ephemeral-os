# OCC Auto-Squash Performance Verification — H1 Default

Generated: 2026-05-11
Parent report: `.omc/plans/occ-layer-stack-commit-resume-auto-squash-report-20260511.md`
Status: Finalized — H1 coalesced synchronous squash is the only supported runtime mode.

## Purpose

The parent report identified synchronous post-publish auto-squash as the dominant
user-facing mutation latency source on high-pile-up edit/write scenarios. The
accepted fix is not a background maintenance mode; it is the default synchronous
H1 coalescing behavior that removes redundant concurrent squash attempts while
preserving fail-closed semantics.

This document records the verification protocol for that default path.

## Scope

In scope:

- Live E2E scenario tests that drive only public sandbox tools (`write_file`,
  `edit_file`, `read_file`, `shell`) and assert final state.
- Per-call timing metadata review for edit/write regressions.
- Compatibility checks that removed squash-mode environment parameters are not
  honored by the daemon or OCC service.

Out of scope:

- Daemon-internal microbenchmarks.
- Provider-level Daytona benchmarks.
- Runtime selection between multiple squash modes. There is no supported
  selection surface.

## Scenario Inventory

| Scenario key | Test file | Purpose |
|---|---|---|
| `sandbox.auto_squash_commit_resume` | `backend/src/live_e2e/tests/sweevo/test_auto_squash_commit_resume.py` | Isolated single-actor write+edit past `AUTO_SQUASH_MAX_DEPTH`. |
| `sandbox.occ_concurrent_conflicts` | `backend/src/live_e2e/tests/sweevo/test_focused_scenarios.py` | Concurrent disjoint writes + stale-edit conflicts under squash. |
| `full_case_user_input` | `backend/src/live_e2e/tests/sweevo/test_full_case_user_input.py` | Real high-mutation hotspot. |
| `full_stack_adversarial` | `backend/src/live_e2e/tests/sweevo/test_full_stack_adversarial.py` | Full subsystem matrix with final reconciliation. |
| `correctness_testing` | `backend/src/live_e2e/tests/sweevo/test_correctness.py` | Read-after-write and edit semantics floor. |

## Metrics Captured Per Scenario

Source of truth: `ToolCallRecord.metadata["timings"]` written by
`backend/src/tools/sandbox_toolkit/mutation_result.py`. The post-run aggregator reads
`message.jsonl` from each scenario’s run directory under
`.sweevo_runs/scenario_logs/<scenario_key>/<run_id>/.../message.jsonl`.

Required keys to inspect:

| Family | Keys |
|---|---|
| Edit critical path | `api.edit.total_s`, `api.edit.snapshot_read_s`, `api.edit.derive_bytes_s`, `api.edit.occ_apply_s` |
| Write critical path | `api.write.total_s`, `api.write.occ_apply_s` |
| Shell critical path | `api.shell.total_s`, `api.shell.dispatch_total_s`, `api.shell.overlay_s`, `command_exec.run_command_s`, `command_exec.occ_apply_s` |
| OCC apply | `occ.apply.total_s`, `occ.apply.commit_queue_wait_s`, `occ.apply.commit_worker_s`, `occ.apply.commit_resume_wait_s`, `occ.apply.commit_s` |
| OCC prepare | `occ.prepare.total_s`, `occ.prepare.gitignore_s`, `occ.prepare.route_and_base_hash_s` |
| Auto-squash | `layer_stack.auto_squash.total_s`, `layer_stack.auto_squash.depth_before`, `layer_stack.auto_squash.depth_after`, `layer_stack.auto_squash.max_depth`, `layer_stack.auto_squash.manifest_version`, `layer_stack.auto_squash.raced`, `layer_stack.auto_squash.skipped_in_flight`, `layer_stack.auto_squash.recheck_triggered` |
| Layer publish | `layer_stack.publish.total_s`, `layer_stack.publish.write_manifest_s`, `layer_stack.transaction.lock_held_s`, `layer_stack.transaction.lock_wait_s` |

## Behavior Assertions

These are deal-breakers. Any divergence fails the change regardless of timing wins.

1. Final committed contents are byte-correct after each scenario.
2. Intentional conflicts keep identical `status`, `conflict_reason`,
   `changed_paths`, and `is_error` shape.
3. A successful returned write is immediately visible to subsequent `read_file`
   and `shell` calls.
4. Active snapshot leases keep their frozen pre-squash view.
5. Multi-path shell mutation remains atomic: all paths commit or all paths reject.
6. Squash and OCC commit events are still emitted where scenarios expect them.
7. Squash failures remain synchronous tool failures; there is no silent
   background-maintenance failure path.

## Run Procedure

```bash
uv run pytest -q \
  'backend/src/live_e2e/tests/sweevo/test_focused_scenarios.py::test_focused_reference_scenario_runs[sandbox.occ_concurrent_conflicts]' \
  backend/src/live_e2e/tests/sweevo/test_full_stack_adversarial.py \
  backend/src/live_e2e/tests/sweevo/test_correctness.py \
  backend/src/live_e2e/tests/sweevo/test_full_case_user_input.py
```

For narrower regression checks, run:

```bash
uv run pytest \
  backend/tests/unit_test/test_sandbox/test_occ/test_auto_squash.py \
  backend/tests/unit_test/test_sandbox/test_daemon/test_daemon_transport.py \
  -q
```

## Acceptance Criteria

- H1 coalescing is default and unconditional.
- Removed squash-mode and squash-depth parameters are ignored by the service and
  not forwarded into the daemon environment.
- The live scenario suite above passes.
- Timing reports show no reintroduction of redundant squash contention as a
  user-facing edit/write bottleneck.
