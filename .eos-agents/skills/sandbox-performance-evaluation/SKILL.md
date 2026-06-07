---
name: sandbox-performance-evaluation
description: "Use when evaluating current EphemeralOS Rust sandbox correctness or performance: eos-e2e-test live Docker runs, LayerStack, overlay/ephemeral exec, OCC publish/conflict behavior, isolated workspace lifecycle, command sessions, daemon audit, plugin/LSP dispatch, pressure/resource reports, O(1) overlay disk usage, CPU, memory, IO, or historical .sweevo_runs sandbox artifacts."
---

# Sandbox Performance Evaluation

Use this skill to prove sandbox behavior from the live checkout, not from stale
runner notes. The implementation is Rust-only: `sandbox/crates/*` owns daemon,
LayerStack, OCC, overlay/ephemeral execution, isolated workspace, command
sessions, plugin/PPC, and the protocol-only E2E harness. The old Python
`backend/`, `task_center_runner`, `ScenarioLoopRunner`, `uv run pytest`, and
`integration-test/SPEC.md` surfaces are historical unless the user explicitly
asks to inspect old reports or migration notes.

## Current Workflow

1. State the concrete claim before running tests: correctness, latency,
   resource use, audit completeness, or cleanup/leak behavior.
2. Read the maintained architecture page first when the claim is
   architecture-shaped: start at `docs/architecture/index.html`, then use the
   relevant `docs/architecture/sandbox/*` or `docs/architecture/tools/*` page.
3. Inspect the owning Rust code before making causal claims. Prefer `rg` over
   broad file reads and keep source truth in `sandbox/` or `agent-core/`.
4. Select the narrowest live E2E module from `sandbox/crates/eos-e2e-test/tests`
   and its `readme.md` / `index.html`.
5. Package the daemon when live Docker tests need a fresh binary:

```bash
cd sandbox
cargo run -p xtask -- package --target x86_64-unknown-linux-musl --out-dir dist
```

6. Run the narrowest check ladder that proves the claim:

```bash
cd sandbox
cargo check -p eos-e2e-test --all-targets --features e2e
cargo test -p eos-e2e-test --features e2e --test <module> <targeted_test> -- --nocapture
```

Broaden to the whole module or workspace only when the change crosses module
boundaries. The default live image and platform are config-owned in
`sandbox/config/prd.yml` under `eos_e2e_test.docker`:
`sweevo-dask__dask-10042:latest` and `linux/amd64`. Check that YAML before
overriding anything.

## Contract To Verify

Use these observable claims as the default rubric:

- `api.v1.exec_command` runs through daemon-owned ephemeral overlay execution:
  latest LayerStack snapshot lease, overlay lowerdirs from shared layers,
  independent per-operation `upperdir` and `workdir`, upperdir capture, and OCC
  publish for in-workspace changes.
- Direct `api.v1.read_file`, `write_file`, and `edit_file` use daemon
  LayerStack/OCC fast paths and should not create overlay leases.
- Files outside the workspace are ordinary container filesystem effects. They
  must not appear in OCC `changed_paths` unless a specific operation explicitly
  captures them.
- Overlay disk use should be O(1) with respect to workspace size and parallel
  readers/writers. Per-operation disk should scale with changed files plus
  scratch metadata, not repository size.
- OCC conflicts must be typed and explainable. Expected synthetic conflicts are
  not internal errors.
- Plugin dispatch goes through daemon package/service routes. Write-capable
  plugin operations publish through daemon-owned OCC; read-only/LSP operations
  refresh or remount to see the latest LayerStack projection without normal
  write-triggered process restarts.
- Isolated workspace mode is explicit `api.isolated_workspace.enter` / `exit`.
  It pins a snapshot, routes file/exec/session work through private state,
  discards upperdir contents on exit, and must not publish through OCC.
- Command sessions expose typed session ids, stdin/cancel/collect surfaces,
  process-group cleanup, bounded output, and lease/session drain after cancel or
  completion.
- Daemon audit and runtime timing data must be pulled through the Rust daemon
  protocol (`api.audit.pull`, `api.audit.snapshot`) or module artifacts. Do not
  claim release-grade audit overhead from missing or historical report sections.

## Live E2E Matrix

All commands below run from `sandbox/`.

| Claim | First module to run | Useful focused target |
|---|---|---|
| Protocol readiness, direct file ops, envelope guards, audit snapshot | `core` | `cargo test -p eos-e2e-test --features e2e --test core -- --nocapture` |
| Daemon identity, inflight, heartbeat, cancel, audit pull/reset | `daemon` | `cargo test -p eos-e2e-test --features e2e --test daemon audit_pull_paginates_and_reset_floor_is_enabled_by_config -- --nocapture` |
| Ephemeral overlay exec, OCC publish, outside-workspace exclusion, stale exec conflict | `ephemeral_workspace` | `cargo test -p eos-e2e-test --features e2e --test ephemeral_workspace exec_multi_path_route_timings_and_read_intent_no_publish -- --nocapture` |
| LayerStack leases, squash bounds, base rebuild, workspace/git commit | `eos-layerstack` | `cargo test -p eos-e2e-test --features e2e --test eos-layerstack -- --nocapture` |
| OCC route gating, concurrent publish, merge conflicts | `eos-occ` | `cargo test -p eos-e2e-test --features e2e --test eos-occ -- --nocapture` |
| Plugin package setup, PPC dispatch, refresh/remount, LSP query, isolated gate | `plugin` | `cargo test -p eos-e2e-test --features e2e --test plugin -- --nocapture` |
| Isolated lifecycle, no-publish private file/exec, network isolation, lifecycle audit | `eos-isolated-workspace` | `cargo test -p eos-e2e-test --features e2e --test eos-isolated-workspace -- --nocapture` |
| Command-session lifecycle, stdin/cancel/collect, process-group behavior, isolated cleanup | `eos-command-session` | `cargo test -p eos-e2e-test --features e2e --test eos-command-session lifecycle -- --nocapture` |
| Cross-subsystem pressure, concurrency ladders, resource reports, cleanup oracles | `pressure` | `cargo test -p eos-e2e-test --features e2e --test pressure resource_report_smoke -- --nocapture` |

For a broader sandbox performance pass, run targeted pressure tests before the
whole pressure module:

```bash
cd sandbox
cargo test -p eos-e2e-test --features e2e --test pressure file_ops_ladder_1_3_6_12 -- --nocapture
cargo test -p eos-e2e-test --features e2e --test pressure occ_ladder_1_3_6_12 -- --nocapture
cargo test -p eos-e2e-test --features e2e --test pressure command_sessions_ladder_1_3_6_12 -- --nocapture
cargo test -p eos-e2e-test --features e2e --test pressure plugin_refresh_ladder_1_3_6_12 -- --nocapture
cargo test -p eos-e2e-test --features e2e --test pressure isolated_handle_cap_ladder -- --nocapture
cargo test -p eos-e2e-test --features e2e --test pressure protocol_only_bundled_sandbox_capstone -- --nocapture
cargo test -p eos-e2e-test --features e2e --test pressure -- --nocapture
```

## Current Code Pointers

Inspect these live anchors before attributing a regression:

```bash
rg -n "API_V1_EXEC_COMMAND|API_AUDIT_PULL|API_ISOLATED_WORKSPACE|API_PLUGIN" sandbox/crates/eos-protocol/src
rg -n "dispatch|workspace|isolated|plugin|audit|command" sandbox/crates/eos-daemon/src
rg -n "capture|finalize|command_session|timings" sandbox/crates/eos-ephemeral-workspace/src
rg -n "OverlayMount|upperdir|workdir|capture" sandbox/crates/eos-overlay/src
rg -n "lease|squash|manifest|workspace_base|workspace_binding" sandbox/crates/eos-layerstack/src
rg -n "commit_queue|apply|conflict|route" sandbox/crates/eos-occ/src
rg -n "enter|exit|upperdir|audit|network|capacity|gc" sandbox/crates/eos-isolated-workspace/src
rg -n "Session|cancel|collect|process|pty|output" sandbox/crates/eos-command-session/src
rg -n "manifest|ppc|refresh|service|registry" sandbox/crates/eos-plugin/src
rg -n "resource_report_smoke|concurrency_levels|perf_artifact_dir" sandbox/crates/eos-e2e-test/tests
```

Expected owner map:

- `sandbox/crates/eos-protocol/src/ops.rs`: stable daemon op names.
- `sandbox/crates/eos-daemon/src/dispatch/dispatcher.rs`: request routing.
- `sandbox/crates/eos-daemon/src/ops/{files,audit,plugins,isolated_workspace,command_sessions}.rs`:
  daemon op handlers.
- `sandbox/crates/eos-ephemeral-workspace/src`: overlay-backed command exec
  prepare/capture/finalize path.
- `sandbox/crates/eos-overlay/src`: kernel overlay mount and upperdir capture.
- `sandbox/crates/eos-layerstack/src`: snapshot leases, manifests, squash, and
  workspace base/binding.
- `sandbox/crates/eos-occ/src`: publish gate, route policy, conflict handling,
  and commit queue.
- `sandbox/crates/eos-isolated-workspace/src`: isolated session lifecycle,
  private routing, audit, networking, and cleanup.
- `sandbox/crates/eos-command-session/src`: PTY/session lifecycle and
  cancellation.
- `sandbox/crates/eos-plugin/src` plus `sandbox/crates/eos-daemon/src/plugin*`:
  plugin package, PPC service, refresh, and dynamic route behavior.
- `sandbox/crates/eos-e2e-test/tests/*/readme.md`: executable E2E intent,
  checklist ids, and focused commands.

## Run Configuration

Do not add one-off runner env toggles for live E2E policy. The Rust harness
loads `sandbox/config/prd.yml` plus one module-local override such as
`sandbox/crates/eos-e2e-test/tests/pressure/config/default.test.yml`.

Important config fields:

- `eos_e2e_test.docker.image`, `platform`, and `eosd_path` control Docker bringup.
- `eos_e2e_test.pool.mode`, `sandboxes`, `recycle_after`, and `keep_container`
  control reuse and concurrency.
- `eos_e2e_test.workload.concurrency_levels`, `write_iterations`,
  `sample_count`, `perf_artifact_dir`, and `timeout_s` control pressure runs.
- `isolated_workspace.enabled`, `total_cap`, `upperdir_bytes`, and
  `memavail_fraction` control isolated mode tests.

If a test unexpectedly skips or fails before reaching daemon protocol calls,
check Docker availability, `sandbox/dist/eosd-linux-amd64`, the configured
image/platform, and the module-local `default.test.yml` before changing code.

## Artifact And Progress Loop

For long pressure runs, keep an iteration tracker in the task-owned report or a
nearby `ITERATION-REPORT.md`. Each iteration should record the exact command,
artifact paths, pass/fail/skip status, first failure, hypothesis, fix, and next
verification command.

Use this lightweight loop while a run is active:

```bash
cd sandbox
find target/e2e-perf -maxdepth 1 -type f -name '*.json' -print 2>/dev/null | sort | tail
docker ps --filter 'label=eos.e2e.pool' --format '{{.Names}}\t{{.Status}}\t{{.Image}}'
```

For `pressure resource_report_smoke`, inspect the newest artifact:

```bash
cd sandbox
REPORT="$(find target/e2e-perf -maxdepth 1 -name 'pressure-resource-report-*.json' | sort | tail -1)"
jq '{
  scenario,
  workload,
  sample_count: (.samples | length),
  leak_counters,
  final_metrics,
  timing_keys: [.samples[].exec_timing_keys[]] | unique
}' "$REPORT"
```

Stop and diagnose the first actionable signal when:

- Cargo output reports a daemon protocol error, timeout, untyped conflict, stale
  lowerdir, mount failure, or missing layer.
- `api.layer_metrics` or resource reports show active leases or command sessions
  failing to return to zero after cleanup.
- Direct file paths emit overlay/lease behavior or command exec fails to publish
  in-workspace changes.
- Isolated workspace tests emit OCC publication or public readback of private
  data after exit.
- Plugin/LSP tests see stale workspace content, repeated normal restarts, or
  plugin operations accepted while isolated mode is active.
- Pressure artifacts show upperdir/scratch growth proportional to repository
  size rather than changed files, or configured leak counters are nonzero.

## Historical Artifact Handling

Only use this section for old `.sweevo_runs/scenario_logs/...` outputs or old
reports that still contain `performance_report.json`, `sandbox_events.jsonl`,
`message.jsonl`, or `task_center_runner.performance_report.v3`. These artifacts
are useful for comparison, not current proof.

```bash
RUN_DIR="$(find .sweevo_runs/scenario_logs -maxdepth 3 -name run.json | sort | tail -1 | xargs dirname)"
python3 .eos-agents/skills/sandbox-performance-evaluation/scripts/summarize_sandbox_perf.py "$RUN_DIR"
```

When reading historical reports:

- Say explicitly that the data is historical unless you reran current Rust E2E.
- Do not require V3 `sandbox.sections` for current Rust `eos-e2e-test`
  evidence; the Rust pressure suite currently emits its own JSON resource
  artifact under `target/e2e-perf`.
- Keep old `backend/src/...`, `ScenarioLoopRunner`, and `uv run pytest` paths
  out of runnable instructions.

## Interpretation Rules

- Split total latency into dispatch, mount/prepare, command body, capture,
  publish/OCC queue, plugin/LSP body, and cleanup before blaming LayerStack or
  overlay.
- If high concurrency slows writes, inspect OCC queue and LayerStack lock
  timings before changing latency budgets.
- If shell latency rises while mount/prepare timing stays flat, suspect command
  body, process scheduling, Docker pressure, or command-session cleanup.
- If LSP/plugin queries return stale data, compare service refresh/remount
  counters and daemon plugin status before restarting the service by default.
- If direct file ops acquire overlay leases, treat it as a route regression.
- If isolated workspace read/write/exec publishes to public OCC, treat it as a
  correctness bug even if the user-visible content looks right while isolated.
- If cgroup or Docker counters are used, distinguish monotonic lifetime counters
  from per-run deltas. Quote per-run deltas for "this test wrote/used X"; quote
  lifetime only for quota or leak context.
- Do not broaden thresholds to hide truncation, skipped modules, missing audit
  data, or nonzero leak counters. Fix the collector, cleanup path, or test setup
  first.

## Report Shape

Final sandbox performance reports should include:

- Exact commands run and whether `xtask package` refreshed `sandbox/dist/eosd-linux-amd64`.
- Exact module/test names plus the module config used.
- Pass/fail/skip status, including whether conflicts were expected synthetic
  conflicts.
- Artifact paths inspected, especially `target/e2e-perf/*.json` for pressure
  resource runs.
- Timing/resource evidence for the specific claim: dispatch, overlay
  prepare/mount, command body, capture, OCC publish/queue, plugin/LSP refresh,
  LayerStack depth, active lease/session counters, upperdir/scratch bytes, and
  leak counters.
- Any fixes made, targeted verification after each fix, and the next broader
  rerun needed if the user wants release-grade confidence.
