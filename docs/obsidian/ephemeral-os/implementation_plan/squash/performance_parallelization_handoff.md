# LayerStack Squash Performance Parallelization Handoff

## Problem Statement

`sandbox-cli manager checkpoint_squash` is currently correct under live Docker E2E coverage, but its latency is too high for heavy interactive workloads with hundreds of active workspace sessions. The current user-facing concern is that a worst-case live squash invocation near 800-900 ms may be noticeable, and may scale poorly as active workspace/session count rises.

The goal of this handoff is to find the fastest safe solution for:

- parallelizing work where correctness permits it;
- optimizing per squash-block time;
- reducing live-session disruption without weakening the LayerStack squash spec, live-remount guarantees, durability, or E2E assertions.

Aggressive optimization is welcome. A large change is acceptable if it comes with a clear safety guarantee and benchmark evidence. Minimal patch size is not a preference here; the target is maximum safe performance.

Scope for this handoff: spec, design, and experiments only. Do not implement production changes in `src/` or `crates/` until the experiment evidence and design conclusion are recorded.

Do not stub product behavior, remove correctness barriers, weaken tests, widen budgets, or use test hooks in production code.

## Current Performance Status

Latest measured `LOAD-COMBO-HTTP` live Docker run: `squash-20260703-043711`.

Per squash invocation:

| invocation | wall time | squash blocks | replaced layers |
|---|---:|---:|---:|
| 1 | `645.098 ms` | 1 | 37 |
| 2 | `714.666 ms` | 1 | 33 |
| 3 | `822.585 ms` | 1 | 33 |
| final cleanup/no-op | `46.597 ms` | 1 | 6 |

Reported `T_squash` is the max single invocation: `822.585 ms`.

HTTP disconnect metric in the same run: `15.762 ms`.

Worst of the two final green combo runs:

- max squash invocation: `913.955 ms`;
- HTTP max silence: `17.107 ms`;
- active sessions: 200;
- HTTP servers: 4;
- background commands: 8;
- rounds: 3.

Important interpretation: the slow live invocations above each had only one squashable block. Parallelizing block flattening alone will not explain or fix this load-case latency if the dominant cost is the post-commit session remount sweep.

## Benchmark Test Case

Primary benchmark case: `LOAD-COMBO-HTTP`.

Catalog location:

- `cli-operation-e2e-live-test/manager/management/squash/helpers.py`
- `cli-operation-e2e-live-test/manager/management/squash/test_spec.md`

Scenario function:

- `_scenario_load_combo_http`

Default benchmark knobs:

| knob | default |
|---|---:|
| `SQUASH_COMBO_ROUNDS` | 3 |
| `SQUASH_COMBO_SESSIONS` | 200 |
| `SQUASH_COMBO_HTTP_SERVERS` | 4 |
| `SQUASH_COMBO_COMMANDS` | 8 |
| `SQUASH_COMBO_SMALL_PER_ROUND` | 24 |
| `SQUASH_COMBO_SMALL_EDITS` | 8 |
| `SQUASH_COMBO_LARGE_PER_ROUND` | 2 |
| `SQUASH_COMBO_LARGE_KIB` | 2048 |

What the benchmark does:

- creates one live Docker sandbox with `ubuntu:24.04`;
- installs the HTTP helper through `sandbox-cli runtime exec_command`;
- runs three rounds of mixed workload;
- each round publishes small files, overwrites small files, writes large zero files, increases active workspace sessions toward 200, starts background commands, starts four HTTP servers, runs `checkpoint_squash`, probes correctness, and records HTTP max-silence disconnect time;
- after session cleanup, runs one final cleanup/no-op squash.

Rerun command:

```bash
export PATH="$PWD/bin:$PATH"
bin/start-sandbox-docker-gateway --rebuild-binary
/opt/homebrew/bin/pytest cli-operation-e2e-live-test/manager/management/squash/test_squash_hard.py::test_squash_hard_catalog[LOAD-COMBO-HTTP] -q
```

Reference artifacts:

- latest green report: `cli-operation-e2e-live-test/manager/management/squash/test-reports/squash-20260703-043711/LOAD-COMBO-HTTP/`
- summary: `cli-operation-e2e-live-test/manager/management/squash/test-reports/squash-20260703-043711/SUMMARY.md`
- per-case verdict: `cli-operation-e2e-live-test/manager/management/squash/test-reports/squash-20260703-043711/LOAD-COMBO-HTTP/verdict.json`
- combo metrics: `cli-operation-e2e-live-test/manager/management/squash/test-reports/squash-20260703-043711/LOAD-COMBO-HTTP/combo-summary.json`

Secondary load benchmarks already available:

- `LOAD-499-HTTP`: 499-stack load plus HTTP disconnect measurement;
- `LOAD-LARGE-HTTP`: large-file squash plus HTTP disconnect measurement.

## Current Implementation Shape

Manager entry point:

- `crates/sandbox-manager/src/operation/management/service/impls/checkpoint_squash.rs`
- forwards one `checkpoint_squash` call to daemon-local runtime op `squash_layerstack`.

Runtime squash operation:

- `crates/sandbox-runtime/operation/src/layerstack/service/impls/squash.rs`
- calls `LayerStack::squash()`;
- then runs a post-commit remount sweep over `workspace_session.session_ids()`;
- current sweep is serial with `.iter().map(|id| remount_session(id)).collect()`.

LayerStack squash:

- `crates/sandbox-runtime/layerstack/src/stack/squash.rs`
- one squash per storage root is enforced by a singleflight guard;
- planning runs under a shared writer guard;
- block flatten/build runs outside the storage writer lock;
- commit runs under one exclusive writer lock;
- commit does recheck, promote, `syncfs`, manifest write, substitution recording, and plan-lease release/GC.

Block flatten:

- `crates/sandbox-runtime/layerstack/src/stack/squash/flatten.rs`
- pure fold from immutable source layer dirs into a block-specific staging dir;
- does not mutate active manifest.

Session remount:

- `crates/sandbox-runtime/operation/src/workspace_session/service/impls/remount_session.rs`
- per-session admission gate exists, so same-session operations are serialized.

Workspace remount blocker:

- `crates/sandbox-runtime/workspace/src/service/impls/remount_workspace.rs`
- currently locks global workspace runtime state and holds it through `WorkspaceManager::remount_session`.
- `WorkspaceManager::remount_session` performs lease rewrite, quiesce, remount runner wait, handle mutation, persistence, and old-lease release under `&mut self`.

This global runtime mutex means a naive threaded remount sweep would still serialize.

## Safety Constraints

Keep these serial/correctness boundaries unless there is a proven replacement design:

- no concurrent top-level `checkpoint_squash` for the same sandbox/storage root;
- keep commit serial and transactional;
- keep `syncfs`/manifest durability semantics;
- keep substitution recording order deterministic;
- keep plan lease semantics so source layers cannot be reclaimed during build;
- keep per-session admission gates;
- keep live-remount C1/C5 classification semantics;
- keep strict teardown guarantees from the live E2E suite.

Do not parallelize or bypass:

- manifest commit;
- active manifest write;
- lease release/GC correctness;
- rollback/fault handling;
- durability sync;
- remount report classification.

## Requested Investigation

Run experiments before reaching a design conclusion. The handoff agent should first measure where time is spent, test candidate bottleneck fixes, and only then choose the design. Do not settle on a serial, parallel, or refactor-heavy approach by inspection alone.

Find the highest-performing safe implementation plan, in this order:

1. Add internal timing attribution for `plan`, `build`, `commit`, and `remount_sweep`.
   This should prove where the 800-900 ms is spent before changing behavior.

2. Optimize the serial path with low-risk changes:
   - skip remount attempts for sessions whose pre-squash manifest cannot contain any replaced layer;
   - avoid repeated path/lower-stack recomputation in `build_blocks`;
   - avoid acquiring/releasing a plan lease for true no-op squash if this remains safe under the existing writer guard.

3. Parallelize independent block builds inside one squash invocation:
   - preallocate block metadata in plan order;
   - run `flatten_block_into_with_lower` concurrently with bounded concurrency;
   - collect results in plan order;
   - keep `commit_squash` unchanged and serial.

4. Investigate parallel remount sweep for hundreds of active sessions:
   - split `WorkspaceManager::remount_session` into snapshot/rewrite/quiesce/remount/apply phases if safe;
   - allow expensive per-session quiesce and namespace remount runner work to proceed concurrently;
   - serialize handle mutation and handle-file persistence;
   - preserve per-session gates and C1/C5 outcomes.

5. If the measurements show another bottleneck, pursue that instead. The output should follow the evidence, not the initial guess.

## Expected Output From Handoff Agent

The handoff agent should return:

- spec/design updates that state the selected optimization path and safety contract;
- experiments run, raw timing deltas, and the conclusion those measurements support;
- saved experiment scripts, commands, and logs as evidence when practical;
- a concrete implementation design with file-level changes, but no production implementation yet;
- exact correctness invariants preserved;
- expected performance impact for single-block, multi-block, and hundreds-session cases;
- smallest unit tests and live E2E proof runs required;
- risks and fallback plan if parallel remount is too invasive.

Recommended evidence layout:

- `docs/obsidian/ephemeral-os/implementation_plan/squash/experiments/performance-parallelization/<RUN_ID>/scripts/`
- `docs/obsidian/ephemeral-os/implementation_plan/squash/experiments/performance-parallelization/<RUN_ID>/logs/`
- `docs/obsidian/ephemeral-os/implementation_plan/squash/experiments/performance-parallelization/<RUN_ID>/RESULTS.md`

Preferred first step:

- phase timing attribution plus benchmark experiments on the current implementation.

Optimization target:

- pursue the best safe speedup, including bounded parallel block build, remount sweep parallelization, state-lock refactors, batching, caching, or I/O reduction when experiments justify them.

Parallel remount sweep is explicitly in scope if attribution proves it dominates and the replacement design preserves the safety constraints above.
