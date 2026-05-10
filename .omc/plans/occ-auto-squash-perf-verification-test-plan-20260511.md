# OCC Auto-Squash Performance Verification — Scenario-Based Test Plan

Generated: 2026-05-11
Parent report: `.omc/plans/occ-layer-stack-commit-resume-auto-squash-report-20260511.md`
Status: Draft — to be executed when an opt-in async/debounced squash mode is introduced.

## Purpose

The parent report identified that synchronous post-publish auto-squash dominates user-facing
mutation latency. This document defines the scenario-based testing protocol that must run
**twice** (synchronous baseline vs experimental flag) for any change to that path. It does
not propose runtime changes. It specifies which scenarios to run, which metrics to capture,
which thresholds to accept, and which behavioral invariants must remain bit-exact.

The plan is structured so a perf change is acceptable only when:
1. Every scenario passes behavior-equivalence assertions in both modes.
2. Every per-tool metric in the experimental mode is at-least as good or within tolerance.
3. No new fail-modes appear (e.g., missed maintenance, lease invalidation, race corruption).

## Scope

In scope:
- Live E2E scenario tests that drive only public sandbox tools (`write_file`,
  `edit_file`, `read_file`, `shell`) and assert per-call timing metadata + final state.
- Aggregation and threshold checks against captured `metadata.timings` keys.
- A run harness that produces a single PASS/FAIL verdict per (scenario × mode).

Out of scope:
- Daemon-internal microbenchmarks (covered by unit tests in
  `backend/tests/unit_test/test_sandbox/test_layer_stack/`).
- Provider-level Daytona benchmarks.
- Any optimization that changes default behavior (gated behind flag; default stays sync).

## Test Matrix

Each scenario is run twice per CI/perf invocation:

| Mode | Env / Flag |
|---|---|
| `sync_baseline` | `EOS_OCC_SQUASH_MODE` unset or `sync` (default today) |
| `experimental` | `EOS_OCC_SQUASH_MODE=async` (or whatever flag the optimization adds) |

The flag name is illustrative; the actual flag MUST be defined by the optimization PR.
Until that PR exists, only `sync_baseline` runs and is recorded as the canonical baseline.

### Scenario Inventory

| Scenario key | Test file | Purpose | Required perf gate |
|---|---|---|---|
| `sandbox.auto_squash_commit_resume` | `backend/src/live_e2e/tests/sweevo/test_auto_squash_commit_resume.py` | Isolated single-actor write+edit past `AUTO_SQUASH_MAX_DEPTH`. Established this PR. | Yes |
| `sandbox.occ_concurrent_conflicts` | `backend/src/live_e2e/tests/sweevo/test_focused_scenarios.py` (existing param) | Concurrent disjoint writes + stale-edit conflicts under squash. | Yes |
| `full_case_user_input` | `backend/src/live_e2e/tests/sweevo/test_full_case_user_input.py` | Real high-mutation hotspot (71 squash events in baseline). | Yes |
| `full_stack_adversarial` | `backend/src/live_e2e/tests/sweevo/test_full_stack_adversarial.py` | Full subsystem matrix (LSP + recursive missions + final reconciliation). | Behavior-only (perf noisy) |
| `correctness_testing` | `backend/src/live_e2e/tests/sweevo/test_correctness.py` | Read-after-write / edit semantics floor. | Behavior-only |

### New Scenarios To Add (when optimization lands)

The current sandbox-only scenario covers single-actor write-then-edit. Two gaps remain:

| Proposed key | Why | Probe shape |
|---|---|---|
| `sandbox.deep_squash_scaling` | Validate squash cost scales with depth-over-threshold, not just past it. | Drive `2*MAX_DEPTH` writes (~64) without intervening reads, then a single `edit_file`; capture per-event `auto_squash.total_s` and assert monotonic-ish growth. |
| `sandbox.lease_during_squash` | Active snapshot-lease must survive squash without rewrite. | Open a long-lived `shell` lease (sleep+tail), drive 36 concurrent writes from another tool track, assert `read_file` from inside the lease still sees the frozen view. |
| `sandbox.shell_multipath_capture` | Multi-file shell mutation atomicity under squash. | One `shell` command writes 8 files atomically while writes from another track push depth past threshold; assert atomic commit OR atomic reject, never partial. |

These are **proposals**. Add them only if the optimization PR’s scope demands them.
Mirror the existing `sandbox.auto_squash_commit_resume` scaffolding (single executor probe,
SCENARIO_REGISTRY entry, paired live test).

## Metrics Captured Per Scenario

Source of truth: `ToolCallRecord.metadata["timings"]` written by
`backend/src/tools/sandbox_toolkit/mutation_result.py`. The post-run aggregator reads
`message.jsonl` from each scenario’s run directory under
`.sweevo_runs/scenario_logs/<scenario_key>/<run_id>/.../message.jsonl`.

Required keys to extract per tool call:

| Family | Keys |
|---|---|
| Edit critical path | `api.edit.total_s`, `api.edit.snapshot_read_s`, `api.edit.derive_bytes_s`, `api.edit.occ_apply_s` |
| Write critical path | `api.write.total_s`, `api.write.occ_apply_s` |
| Shell critical path | `api.shell.total_s`, `api.shell.dispatch_total_s`, `api.shell.overlay_s`, `command_exec.run_command_s`, `command_exec.occ_apply_s` |
| OCC apply | `occ.apply.total_s`, `occ.apply.commit_queue_wait_s`, `occ.apply.commit_worker_s`, `occ.apply.commit_resume_wait_s`, `occ.apply.commit_s` |
| OCC prepare | `occ.prepare.total_s`, `occ.prepare.gitignore_s`, `occ.prepare.route_and_base_hash_s` |
| Auto-squash | `layer_stack.auto_squash.total_s`, `layer_stack.auto_squash.depth_before`, `layer_stack.auto_squash.depth_after`, `layer_stack.auto_squash.max_depth`, `layer_stack.auto_squash.manifest_version`, `layer_stack.auto_squash.raced` (if present) |
| Layer publish | `layer_stack.publish.total_s`, `layer_stack.publish.write_manifest_s`, `layer_stack.transaction.lock_held_s`, `layer_stack.transaction.lock_wait_s` |

Aggregation: per scenario × per tool × per key, compute `count`, `avg`, `p50`, `p95`,
`max`, `total`. Output as one JSON file per `(scenario, mode)` and one comparison Markdown
table per scenario.

## Behavior-Equivalence Assertions (must hold in both modes)

These are deal-breakers. Any divergence here fails the optimization regardless of perf wins.

1. **Final committed contents**: After scenario completes, every file’s final
   `read_file` content is byte-equal across modes.
2. **Conflict surface**: Intentional conflicts (stale base hash, missing anchor, disjoint
   races) return identical `status`, `conflict_reason`, `changed_paths`, `is_error` shape.
3. **Read-after-write visibility**: Successful tool result returning ⇒ subsequent
   `read_file` and `shell` see the committed bytes. No window where a returned write is
   not yet visible.
4. **Active lease invariant**: Workspace snapshot leases held during squash see the
   frozen pre-squash view; post-squash readers see new content. Squash must never delete
   a layer addressable by a live lease.
5. **Multi-path shell atomicity**: A `shell` command that mutates N files either commits
   all N or rejects all N. No partial commits under squash race.
6. **Event sequence**: `SANDBOX_LAYER_STACK_LAYERS_SQUASHED`,
   `SANDBOX_OCC_CHANGESET_RECEIVED`, `SANDBOX_OCC_CHANGES_COMMITTED` appear in both
   `report.events` and `sandbox_events.jsonl` in both modes.
7. **Fail-closed**: Forced squash failure (injectable test seam) must propagate as a
   tool error in `sync_baseline`. In `experimental`, propagation rules MUST be defined
   in writing before the scenario is allowed to pass — silent maintenance failure is
   only acceptable if a separate maintenance-error contract is documented and tested.

## Performance Acceptance Thresholds

Apply these only when `experimental` mode exists. Per scenario, per tool, per metric:

| Metric | Threshold |
|---|---|
| `api.edit.derive_bytes_s` p95 | No change (Δ ≤ 5 ms or ≤ 20% of baseline, whichever larger). |
| `api.edit.snapshot_read_s` p95 | Same. |
| `occ.apply.commit_queue_wait_s` p95 | Must not regress (Δ ≤ 5 ms tolerance). |
| `occ.apply.commit_worker_s` p95 | Must not regress (Δ ≤ 5 ms tolerance). |
| `occ.apply.commit_resume_wait_s` p95 | **≥ 50% reduction** vs baseline OR absolute < 500 ms in the scenario, whichever is met. |
| `layer_stack.auto_squash.total_s` p95 | May increase or decrease in async mode (now off-critical-path) — not gated on user latency. Must still appear in the metrics file (proves squash actually ran). |
| `api.edit.total_s` p95 | Must drop in line with `commit_resume_wait_s` reduction; otherwise the optimization missed its target. |
| Any tool error count | Must equal baseline (intentional-conflict only). |

Hard floor: if any p95 in `experimental` is more than 25% worse than `sync_baseline` for
`api.*.total_s`, `occ.apply.commit_queue_wait_s`, or `occ.apply.commit_worker_s`, the run
fails regardless of `commit_resume_wait_s` improvements.

## Reference Baseline (sync_baseline as of 2026-05-11)

Captured this PR (sync mode):

| Scenario | Tool | p50 | p95 | total | n |
|---|---|--:|--:|--:|--:|
| `sandbox.auto_squash_commit_resume` | `write_file` | 418.5 ms | 524.6 ms | 16,605 ms | 38 |
| `sandbox.auto_squash_commit_resume` | `edit_file` | 522.6 ms | 532.1 ms | 1,476 ms | 3 |
| `sandbox.auto_squash_commit_resume` | `auto_squash.total_s` (write) | 68.1 ms | 70.9 ms | 481 ms | 7 |
| `sandbox.auto_squash_commit_resume` | `commit_resume_wait_s` (write) | 0.3 ms | 69.2 ms | 493 ms | 38 |
| `full_case_user_input` (parent report) | `edit_file` | 1090.1 ms | 2800.5 ms | 132,533 ms | 86 |
| `full_case_user_input` (parent report) | `auto_squash.total_s` | 1209.7 ms | 2179.6 ms | 78,401 ms | 71 |

`full_case_user_input` is the high-pile-up case (many layers coalesce). Both must be
re-run as the canonical baseline against any experimental change.

## Run Procedure

1. **Snapshot baseline** (current code, sync mode):
   ```bash
   EOS_TIER_RUN_ID=baseline-$(date +%s) \
     .venv/bin/pytest \
     backend/src/live_e2e/tests/sweevo/test_auto_squash_commit_resume.py \
     backend/src/live_e2e/tests/sweevo/test_focused_scenarios.py \
     backend/src/live_e2e/tests/sweevo/test_full_case_user_input.py \
     backend/src/live_e2e/tests/sweevo/test_full_stack_adversarial.py \
     -q
   ```
   Capture each `.sweevo_runs/scenario_logs/<scenario>/<run_id>/` under
   `.omc/perf/baselines/<date>/`.

2. **Apply optimization PR** with `EOS_OCC_SQUASH_MODE=sync` still default. Re-run
   step 1 — must match baseline within tolerance (proves the flag is gated).

3. **Run experimental** with the flag enabled:
   ```bash
   EOS_OCC_SQUASH_MODE=async \
   EOS_TIER_RUN_ID=experimental-$(date +%s) \
     .venv/bin/pytest <same scenarios as step 1> -q
   ```
   Capture under `.omc/perf/experimental/<date>/`.

4. **Compare** via the aggregator script (to be written; sketch in next section). Output
   one `comparison.md` per scenario.

5. **Behavior gate**: aggregator asserts the seven invariants from §"Behavior-Equivalence
   Assertions". If any fail → run is rejected; perf table is not even displayed.

6. **Perf gate**: aggregator applies §"Performance Acceptance Thresholds". Output PASS/FAIL.

7. **Promotion**: only after PASS in steps 5 + 6 across all scenarios may the flag default
   change. The change requires its own PR with its own behavior gate run.

## Aggregator Sketch (script to add separately)

`backend/scripts/perf/auto_squash_compare.py`:

- Input: two run-root paths (baseline, experimental) and a list of scenario keys.
- Output: per-scenario `comparison.md` + a top-level `verdict.json`.
- Reads `.../message.jsonl` per executor task, walks tool-result messages, extracts
  `metadata.timings`. Computes per-tool aggregates.
- Cross-checks behavior assertions against `report.events`, `sandbox_events.jsonl`, and
  the scenario’s `summary` artifact.
- Emits exit code 0 only if behavior + perf gates both pass.

This script is intentionally not implemented in this plan; it is implementation work for
the optimization PR and should ship in the same change.

## Required Tests Already In Place (no work needed)

| Concern | Test |
|---|---|
| Scenario registry conformance | `backend/src/live_e2e/tests/test_scenario_suite_imports.py` |
| OCC auto-squash unit | `backend/tests/unit_test/test_sandbox/test_occ/test_auto_squash.py` |
| Layer-stack squash + GC + lease pinning | `backend/tests/unit_test/test_sandbox/test_layer_stack/test_squash.py`, `…/test_squash_gc.py`, `…/test_lease_pinning.py` |
| Public API natural squash | `backend/tests/live_e2e_test/sandbox/layer_stack_overlay_occ/test_workspace_base_shell_lease_squash.py::test_public_mutations_naturally_trigger_squash_and_keep_workspace_view` |
| Single-actor probe | `backend/src/live_e2e/tests/sweevo/test_auto_squash_commit_resume.py` (added in this PR) |

## Risks / Open Questions

1. `full_stack_adversarial` showed a SETUP flake (`git checkout -f <dask-base-commit>`
   exit 128 on cold sandbox snapshot) that resolved on retry. This is independent of
   the auto-squash path. Recommendation: add a fixture-level retry on transient sandbox
   git failures before treating perf delta on this scenario as signal.
2. `full_case_user_input` is a noisy real-world hotspot. Use it for direction-of-change
   evidence only; do not gate the perf decision solely on its numbers.
3. `EOS_OCC_SQUASH_MODE` is a placeholder name. The optimization PR must define the real
   flag and update step 1/3 commands above.
4. Async-mode failure semantics MUST be specified in writing before any scenario is
   allowed to pass an async run with a silent maintenance error. This is a behavior
   change, not a perf change, and needs its own contract.

## Acceptance Criteria For This Plan Document

This plan document itself is considered complete when:
- All scenario keys, metric keys, and threshold rules are unambiguous.
- A reviewer can run the documented commands without further clarification.
- The behavior-equivalence assertions are testable as written.
- The reference baseline numbers are captured from a real run on the current branch.
