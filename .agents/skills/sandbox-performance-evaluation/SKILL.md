---
name: sandbox-performance-evaluation
description: Use when evaluating EphemeralOS sandbox correctness or performance for unified layerstack, overlay, OCC, isolated_workspace, command_exec, plugin dispatch, Pyright/LSP, daemon-audit/event stats, high-concurrency live_e2e scenarios, mount(2) overlay O(1) disk usage, CPU, memory, or .sweevo_runs scenario artifacts.
---

# Sandbox Performance Evaluation

Use this skill to verify whether EphemeralOS sandbox operations preserve the
intended filesystem semantics and remain fast under concurrency. It also
verifies that sandbox audit events, performance-report stats, and resource
samples are complete enough to make latency, CPU, memory, IO, artifact size,
and audit-overhead claims. Work from the current checkout and current run
artifacts; never present old timings as current without rechecking.

## Contract To Verify

Check the design as a set of observable claims:

1. `command_exec` and plugin dispatch both go through layerstack snapshot lease, per-operation overlay upperdir, upperdir capture, and OCC publish. They may use different runner functions, but the semantics must match.
2. The overlay is not itself "a lowerdir". Correct wording: the overlay mount is built from shared leased snapshot layers as lowerdirs plus an independent per-operation `upperdir` and `workdir`.
3. Generic plugin operations should default to automatic workspace overlay dispatch. Stateful runtimes such as Pyright/LSP may opt out only if they manage their own leased overlay lifecycle.
4. Pyright/LSP must see the latest layerstack snapshot at the bound workspace root, not a stale projected copy. A snapshot refresh should remount or refresh the long-lived session, not restart the server on every normal write.
5. The mounted process should see a normal container filesystem with only the bound workspace root replaced by the overlay. Files outside the workspace are normal container files and are not captured by workspace OCC unless another mechanism captures them.
6. In the private namespace/new mount API path, overlay disk use should be O(1) with respect to workspace size and number of parallel readers/writers. Per-operation disk should scale with changed files and scratch metadata only.

## Audit Events And Stats Contract

Use this path when the task names `sandbox_events.jsonl`, daemon audit pull,
performance reports, event stats, audit overhead, resource sampling,
`performance_report.json`, `performance_report.md`, or the mock sandbox test
tree under `backend/src/task_center_runner/tests/mock/sandbox`.

Observable claims:

1. The sandbox daemon feeds audit events and sandbox performance metrics
   through the pull RPCs `api.audit.pull` and `api.audit.snapshot`, backed by a
   bounded in-sandbox memory ring. The daemon ring is the live source; it must
   report `schema == "sandbox.daemon.audit.pull.v1"`, monotonic `seq`, stable
   `boot_epoch_id` until restart, `retained_events`, `retained_bytes`,
   `max_events`, `max_bytes`, `pressure`, `dropped_event_count`,
   `dropped_event_count_by_lane`, and `lost_before_seq`.
2. `sandbox_events.jsonl` is the host-side run artifact mirrored from pulled
   daemon events. The daemon must not spill this audit stream to disk in the
   sandbox; check sandbox-resident size separately from host artifact size.
   `performance_report.json` must preserve enough of the pulled evidence to
   answer correctness, latency, and resource questions without rereading every
   JSONL line.
3. The report schema is `task_center_runner.performance_report.v3`. The V3
   `sandbox.sections` mirror must include at least: `summary`,
   `per_tool_timing`, `per_tool_phase_breakdown`, `background_tool_calls`,
   `plugin_activity`, `overlay_workspace`, `layer_stack`, `occ`,
   `isolated_workspace`, `os_resource`, `daemon_audit_pull`, `overhead`, and
   `warnings`.
4. V3 report sections read promoted `payload.<section>` fields. Do not make
   performance claims from stale `payload.daemon_event` fallback shapes.
5. Tool timing stats must include count, p50, p95, max, and per-sample
   `timings_s` for load-bearing sandbox tools. Phase rollups should split
   queued, mount, exec, capture, publish, and release when those phases were
   emitted.
6. Resource stats must include O(1) overlay evidence
   (`workspace_tree_exists == 0`, `workspace_tree_bytes == 0`), upperdir/run
   dir bytes, changed-path count, truncation flags, manifest depth/path count,
   process RSS, and cgroup CPU/IO/memory counters.
7. Audit pull stats must be drop-free for a clean claim:
   `dropped_event_count == 0`, `lost_before_seq == 0`, no unexplained
   `floor_raises`, and no sustained buffer pressure. Treat event-count drift
   between JSONL rows and puller counters as actionable unless a daemon restart
   or partial flush explains it.
8. Audit-overhead claims require methodology metadata, not just a report with
   zero defaults. For a release-gate claim, verify the §12 verdict: overhead
   pass, isolated-workspace pass, drop-free pull pass, and artifact-bound pass.
9. Resource counters are interpreted by type: cgroup CPU and IO are monotonic
   counters and must be read as run deltas; memory current/peak are gauges;
   artifact size is host-side JSONL/rotation footprint, not sandbox write
   payload.
10. The audit path itself must stay cheap. Watch `resource.audit.collect_s`,
   daemon audit pull p95, runner CPU, daemon CPU/RSS delta, and artifact disk
   size. If these regress, diagnose the collector/puller/report path before
   blaming overlay, OCC, or LayerStack.
11. Size checks must cover both places:
   in-sandbox daemon ring memory (`retained_bytes <= max_bytes`, normally
   `max_bytes == 8 MiB`, `pressure < 0.8`) and host artifacts
   (`sandbox_events.jsonl` plus rotations within the V3 artifact-bound gate).
   For isolated-workspace direct pytest tiers, also size the sandbox-local
   `/tmp/sandbox_isolated_workspace_events.jsonl` or the
   `EOS_ISOLATED_WORKSPACE_AUDIT_PATH` override.

## Isolated Workspace Contract

Use this path when the task names `isolated_workspace`, `iws`, pinned
workspace handles, per-agent network namespaces, cgroup freezer behavior,
daemon restart GC, or Tier 8 soak tests.

Observable claims:

1. `isolated_workspace` is structurally separate from OCC publish. It uses a
   distinct `IsolatedWorkspaceHandle`, a pinned layer-stack snapshot lease, a
   private net/pid/mount/user namespace, a tmpfs upperdir, and an explicit
   discard-on-exit path. It must not call OCC or sandbox-overlay publish code.
2. Enter events carry snapshot and setup evidence:
   `manifest_version`, `manifest_root_hash`, `ns_ip`,
   `lowerdir_layer_count`, `materialize=false`, `total_ms`, and `phases_ms`.
3. Tool-call events carry execution evidence: `argv0`, `exit_code`,
   `duration_s`, `total_ms`, and `phases_ms`.
4. Exit, TTL eviction, and startup-GC events carry discard/reap evidence:
   `upperdir_bytes_discarded` or orphan `kind`, `identifier`, `total_ms`, and
   `phases_ms`.
5. `phases_ms` follows conditional-key emission: a key appears only when that
   phase ran to completion. Do not emit `0.0` for skipped or stubbed phases.
6. SUBSET-COVER must hold on every isolated-workspace audit event:
   `sum(phases_ms.values()) <= total_ms + max(2.0, 0.05 * total_ms)`.
7. Pinned handles freeze between tool calls, discard upperdir contents on exit,
   preserve lowerdir snapshot pinning against peer publishes, and isolate
   filesystem, network, cgroup memory, and bridge ports across agents.
8. The Tier 8 soak standard is opt-in: `TOTAL_CAP=5`, `install_veth` contention
   bounded by `max <= 5 * median`, idle disk <= 10 MiB after 60 s, idle frozen
   cgroup CPU does not grow, public-internet pip/httpx succeeds when egress is
   available, and 100 create/destroy cycles do not grow daemon FD/veth counts.

## Code Pointers

Before making causal claims, inspect the live code paths:

```bash
rg -n "register_plugin_op|auto_workspace_overlay|run_plugin_op_with_workspace_overlay|acquire_operation_overlay|publish_cycle" backend/src/sandbox backend/src/plugins
rg -n "prepare_workspace_snapshot|LayerPathsLayout|mount_workspace_s|mount_overlay|new_mount_api_supported" backend/src/sandbox
rg -n "PyrightSession|refresh_manifest|namespace_remount|auto_workspace_overlay=False|lsp.session" backend/src/plugins/catalog/lsp
rg -n "IsolatedWorkspaceManager|sandbox_isolated_workspace|phases_ms|install_veth|ttl_sweep|startup_gc" backend/src/sandbox/isolated_workspace backend/src/task_center_runner/tests/mock/sandbox/isolated_workspace
rg -n "AuditBuffer|SCHEMA_VERSION|api.audit.pull|api.audit.snapshot|retained_bytes|max_bytes|lost_before_seq" backend/src/sandbox/daemon backend/src/sandbox/api docs/daemon-audit-pull-consolidation-v3
rg -n "RotatingJsonlSink|ROTATION_BYTES_DEFAULT|artifact_inventory|sandbox_events.jsonl|daemon_audit_puller_stats|_daemon_audit_pull_enabled" backend/src/task_center_runner/audit backend/src/task_center_runner/core backend/tests/unit_test/test_task_center_runner
rg -n "build_performance_report|REPORT_SCHEMA|sandbox.sections|evaluate_.*gate" backend/src/task_center_runner/audit backend/tests/unit_test/test_task_center_runner
rg -n "collect_command_exec_resource_metrics|resource.audit.collect_s|os_resource.sampled|workspace_tree_truncated|cgroup|sandbox_isolated_workspace_events" backend/src/sandbox backend/src/task_center_runner/tests/mock/sandbox
```

Expected anchors:

- `backend/src/sandbox/execution/service.py`: command_exec lease -> run -> publish lifecycle.
- `backend/src/sandbox/execution/strategies/namespace_child.py`: command child mounts overlay at `workspace_root`.
- `backend/src/sandbox/execution/overlay/kernel_mount.py`: new mount API overlay construction.
- `backend/src/sandbox/plugin/op_registry.py`: plugin ops default to `auto_workspace_overlay=True`.
- `backend/src/sandbox/plugin/overlay_dispatch.py`: one-shot plugin overlay lease, child dispatch, publish, release.
- `backend/src/sandbox/plugin/overlay_child.py`: plugin child mounts overlay at the workspace binding root.
- `backend/src/sandbox/daemon/service/sandbox_overlay.py`: operation overlay handle, upperdir allocation, OCC publish.
- `backend/src/plugins/catalog/lsp/runtime/session_manager.py`: long-lived LSP session snapshot refresh.
- `backend/src/plugins/catalog/lsp/runtime/pyright_session.py` and `namespace_remount.py`: Pyright private namespace remount.
- `backend/src/sandbox/isolated_workspace/manager.py`: isolated handle
  lifecycle, phase timing, freeze/thaw, discard, TTL, and startup GC.
- `backend/src/sandbox/isolated_workspace/handlers.py`: JSONL audit sink at
  `/tmp/sandbox_isolated_workspace_events.jsonl` unless
  `EOS_ISOLATED_WORKSPACE_AUDIT_PATH` overrides it.
- `backend/src/sandbox/isolated_workspace/ops_handlers.py`: bounded tool-call
  handlers; import fence must keep OCC and sandbox-overlay publish out.
- `backend/src/sandbox/isolated_workspace/network.py`: bridge, MASQUERADE,
  IMDS/RFC1918 policy, veth install/teardown, and IP pool.
- `backend/src/task_center_runner/tests/mock/sandbox/isolated_workspace/`:
  Tier 0-9 contract tests and `RUNNING-LIVE-TESTS.md` /
  `RUNNING-SOAK-TESTS.md`.
- `backend/src/task_center_runner/audit/performance_report.py`: V3
  `performance_report.json` / `.md` builder, `sandbox.sections`, legacy
  timing/resource rollups, cgroup run-delta normalization, artifact inventory.
- `backend/src/sandbox/daemon/audit_buffer.py`: daemon-side bounded
  in-memory ring. The daemon audit stream is not `/tmp` persistence; every
  pull/snapshot reports `retained_bytes`, `max_bytes`, pressure, drops, and
  `lost_before_seq`.
- `backend/src/sandbox/api/daemon_audit.py` and
  `backend/src/sandbox/daemon/rpc/dispatcher.py`: `api.audit.pull`,
  `api.audit.snapshot`, and gated `api.audit.reset_floor`.
- `backend/src/task_center_runner/audit/sandbox_events_sink.py`: host-side
  `sandbox_events.jsonl` writer with 64 MiB live rotation, gzip, and retention
  cap. This is the canonical persisted artifact for pulled daemon events.
- `backend/src/task_center_runner/audit/release_gates.py`: pure release-gate
  evaluators for isolated workspace, drop-free pull, audit overhead, and
  artifact-bound checks.
- `backend/src/task_center_runner/audit/recorder.py` and
  `sandbox_events_sink.py`: daemon audit pull lifecycle, final puller stats,
  JSONL event mirroring, and rotated artifact handling.
- `backend/src/sandbox/_shared/command_exec_resource_metrics.py`:
  per-command resource snapshots, `resource.audit.collect_s`,
  `os_resource.sampled`, tree truncation flags, process/cgroup counters.
- `backend/src/sandbox/isolated_workspace/_control_plane/pipeline_registry.py`:
  direct isolated-workspace JSONL sink at
  `/tmp/sandbox_isolated_workspace_events.jsonl` unless overridden. This is a
  separate sandbox-local lifecycle audit file, not the daemon pull ring.
- `backend/src/task_center_runner/tests/mock/_layer_stack_occ_overlay_assertions.py`:
  shared assertions for performance-report schema, timing keys, resource keys,
  and O(1) workspace-resource snapshots.
- `docs/daemon-audit-pull-consolidation-v3/phase-3-report-and-release-gates.md`:
  report-section and release-gate contract; use current code as source of
  truth when docs and implementation differ.

## Scenario Selection

Locate current tests first; paths move:

```bash
rg -n "high_concurrency_layerstack_overlay_occ|complex_project_build_shell_edit_lsp|full_system_capacity_matrix|heavy_io_zoned_concurrent|background_shell_" backend/src/task_center_runner/tests backend/src/task_center_runner/scenarios
rg -n "isolated_workspace|sandbox_isolated_workspace|live_e2e_soak|phases_ms|install_veth" backend/src/task_center_runner/tests/mock/sandbox/isolated_workspace backend/src/sandbox/isolated_workspace
rg -n "performance_report_v3|assert_timing_keys_present|assert_resource_key_max|resource.command_exec|daemon_audit_pull|overhead_gate|sandbox.sections" backend/tests/unit_test backend/src/task_center_runner/tests/mock/sandbox backend/src/task_center_runner/audit
```

For daemon audit, event stats, or performance-report refactors, run the
report/static gate before live sandbox scenarios:

```bash
uv run pytest \
  backend/tests/unit_test/test_task_center_runner/test_performance_report_v3.py \
  backend/tests/unit_test/test_task_center_runner/test_performance_report_deferrals.py \
  backend/tests/unit_test/test_task_center_runner/test_async_perf_report.py \
  backend/tests/unit_test/test_task_center_runner/test_daemon_event_normalizer.py \
  backend/tests/unit_test/test_task_center_runner/test_daemon_pull.py \
  backend/tests/unit_test/test_task_center_runner/test_audit_recorder_aclose.py \
  backend/tests/unit_test/test_engine/test_background_task_emitters.py \
  backend/tests/unit_test/test_sandbox/test_daemon/ \
  backend/tests/unit_test/test_audit/ \
  -q
```

Then run a focused live audit/stats ladder. These tests assert both behavior
and the quality of the emitted stats in `sandbox_events.jsonl` and
`performance_report.json`:

```bash
uv run pytest -q -x --tb=short --durations=20 \
  backend/src/task_center_runner/tests/mock/sandbox/ephemeral_workspace/ \
  backend/src/task_center_runner/tests/mock/sandbox/plugin/ \
  backend/src/task_center_runner/tests/mock/sandbox/layer_stack_occ_overlay/test_high_concurrency_layerstack_overlay_occ.py \
  backend/src/task_center_runner/tests/mock/sandbox/layer_stack_occ_overlay/test_heavy_io_zoned_concurrent.py
```

For deeper audit/resource pressure, add the targeted full-stack and
responsiveness probes before running the whole mock sandbox directory:

```bash
uv run pytest -q -x --tb=short --durations=20 \
  backend/src/task_center_runner/tests/mock/sandbox/full_stack/test_full_stack_adversarial.py \
  backend/src/task_center_runner/tests/mock/sandbox/background_tool/test_background_many_small_writes_do_not_starve_dispatcher.py \
  backend/src/task_center_runner/tests/mock/sandbox/project_build/test_project_build_full_o1_disk_budget.py \
  backend/src/task_center_runner/tests/mock/sandbox/project_build/test_project_build_grep_glob_low_latency_after_many_edits.py \
  backend/src/task_center_runner/tests/mock/sandbox/project_build/test_project_build_shell_edit_lsp_remount_not_restart.py
```

Use the explicit shell latency diagnostic only when characterizing mount/OCC
contention or audit collection overhead by concurrency level:

```bash
EOS_RUN_SHELL_LATENCY_MATRIX=1 \
EOS_SHELL_LATENCY_MATRIX_LEVELS=1,5,10 \
uv run pytest -q --tb=short --durations=20 \
  backend/src/task_center_runner/tests/mock/sandbox/layer_stack_occ_overlay/test_shell_concurrency_latency_matrix_diagnostic.py
```

For isolated_workspace changes, run the focused ladder first.

Static surface, always on and fast:

```bash
uv run pytest \
  backend/src/task_center_runner/tests/mock/sandbox/isolated_workspace/pre_flight/ \
  backend/tests/unit_test/test_sandbox/test_daemon/ \
  backend/tests/unit_test/test_sandbox/test_import_fence.py \
  backend/tests/unit_test/test_audit/ \
  backend/tests/unit_test/test_task_center/test_audit/ \
  -q
```

Quick isolated_workspace live smoke:

```bash
EOS_SWEEVO_INSTANCE=dask__dask_2023.3.2_2023.4.0 \
EOS_SANDBOX_PROVIDER=docker \
EOS_ISOLATED_WORKSPACE_ENABLED=true \
EOS__RUNNER__LIVE_E2E__HEAVY_ENABLED=true \
EPHEMERALOS_DATABASE_URL="sqlite:///./.ephemeralos/ephemeralos.db" \
uv run pytest \
  backend/src/task_center_runner/tests/mock/sandbox/isolated_workspace/pre_flight/ \
  backend/src/task_center_runner/tests/mock/sandbox/isolated_workspace/happy_path/ \
  -v
```

Full isolated_workspace live gate, excluding opt-in soak:

```bash
EOS_SWEEVO_INSTANCE=dask__dask_2023.3.2_2023.4.0 \
EOS_SANDBOX_PROVIDER=docker \
EOS_ISOLATED_WORKSPACE_ENABLED=true \
EOS__RUNNER__LIVE_E2E__HEAVY_ENABLED=true \
EOS__RUNNER__SANDBOX_REUSE_MODE=reuse \
EPHEMERALOS_DATABASE_URL="sqlite:///./.ephemeralos/ephemeralos.db" \
uv run pytest \
  backend/src/task_center_runner/tests/mock/sandbox/isolated_workspace/ \
  -m "not live_e2e_soak" \
  --tb=short --durations=20 -v -p no:randomly
```

Tier 8 soak tests are nightly-style and expensive. Prefer one test at a time
while iterating, then run the whole stress directory:

```bash
EOS_SWEEVO_INSTANCE=dask__dask_2023.3.2_2023.4.0 \
EOS_SANDBOX_PROVIDER=docker \
EOS_ISOLATED_WORKSPACE_ENABLED=true \
EOS__RUNNER__LIVE_E2E__HEAVY_ENABLED=true \
EOS__RUNNER__SANDBOX_REUSE_MODE=reuse \
EPHEMERALOS_DATABASE_URL="sqlite:///./.ephemeralos/ephemeralos.db" \
uv run pytest \
  backend/src/task_center_runner/tests/mock/sandbox/isolated_workspace/stress/ \
  -m live_e2e_soak \
  --tb=short --durations=20 -v -p no:randomly
```

If the runner lacks public internet, isolate that infrastructure problem by
deselecting only the internet-bound soak test:

```bash
uv run pytest \
  backend/src/task_center_runner/tests/mock/sandbox/isolated_workspace/stress/ \
  -m live_e2e_soak \
  -k "not test_pip_install_then_run_e2e" \
  --tb=short --durations=20 -v -p no:randomly
```

For the older SWE-EVO scenario coverage, run sequentially. Start with smoke
tests:

```bash
uv run pytest -q -x --tb=short --durations=20 \
  backend/src/task_center_runner/tests/mock/sandbox/project_build/test_complex_project_build_smoke.py

uv run pytest -q -x --tb=short --durations=20 \
  backend/src/task_center_runner/tests/mock/sandbox/project_build/test_complex_project_build_shell_edit_lsp_smoke.py
```

Then run the full targeted scenarios:

```bash
uv run pytest -q -x --tb=short --durations=20 \
  backend/src/task_center_runner/tests/mock/sandbox/project_build/test_complex_project_build_full.py

uv run pytest -q -x --tb=short --durations=20 \
  backend/src/task_center_runner/tests/mock/sandbox/project_build/test_complex_project_build_shell_edit_lsp_full.py

uv run pytest -q -x --tb=short --durations=20 \
  backend/src/task_center_runner/tests/mock/sandbox/layer_stack_occ_overlay/test_high_concurrency_layerstack_overlay_occ.py

uv run pytest -q -x --tb=short --durations=20 \
  backend/src/task_center_runner/tests/mock/sandbox/layer_stack_occ_overlay/test_heavy_io_zoned_concurrent.py

uv run pytest -q -x --tb=short --durations=20 \
  backend/src/task_center_runner/tests/mock/sandbox/capacity/test_full_system_capacity_matrix.py
```

Background-shell suite (`shell(background=True)` daemon-native path,
T1-T8). Runs the seven harness-driven scenarios plus the in-process
TTL-reaper unit test:

```bash
EOS_SWEEVO_INSTANCE=dask__dask_2023.3.2_2023.4.0 \
uv run pytest -q --tb=short --durations=20 \
  backend/src/task_center_runner/tests/mock/sandbox/background_tool/
```

```bash
uv run pytest -q -x --tb=short --durations=20 \
  /Users/yifanxu/machine_learning/LoVC/EphemeralOS/backend/src/task_center_runner/tests/mock/sandbox
```

Scenario coverage cheatsheet:

- `ephemeral_workspace/*`: direct exercise of shared-workspace one-call
  overlays, fast-path read/write/edit, grep/glob, cancellation, outside
  workspace policy, and O(1) lowerdir disk. Use when audit/resource stats for
  per-call overlays, warm read/write/edit p95, tree truncation flags, or
  `mutation_source` are suspect.
- `plugin/*`: plugin dispatch and LSP refresh behavior. Use when
  `plugin_activity`, read-only no-publish behavior, write-allowed plugin
  publish, LSP warm p95, or plugin-blocking in isolated mode is suspect.
- `full_stack_adversarial`: broad cross-module stats proof. It asserts V3
  report completeness for required tools, tool samples with timings, sandbox
  timing keys, cgroup run-delta resource keys, O(1) workspace bytes, and no
  forbidden sandbox event text.
- `complex_project_build_shell_edit_lsp`: serial mixed shell-edit + Pyright
  workload with diagnostics. Use when LSP remount/start counts or
  shell-vs-edit_file routing is suspect.
- `high_concurrency_layerstack_overlay_occ`: 20-way concurrent write/edit
  pressure on a shared OCC target. Use when suspecting OCC commit-queue
  contention, layer-stack lock_wait, auto-squash regression, changed-path
  count, truncation flags, or write/edit p95 regression.
- `heavy_io_zoned_concurrent`: 5 concurrent workers running long shells
  (~30-50s, ~33 MB each) into three placement zones — gitincluded
  (`/testbed/perf_load_tracked/`), gitignored (`/testbed/build/`), and
  outside-workspace (`/tmp/heavy_io_zoned/`). Use when characterizing
  layerstack lease hold under long shells, OCC merge correctness across
  zones, or .gitignore-aware snapshot behavior. Asserts O(1) overlay disk
  (`workspace_tree_bytes == 0`) and outside-zone OCC isolation (`/tmp`
  paths never leak into workspace OCC `changed_paths`).
- `project_build_*_o1_disk_budget` and low-latency project-build tests:
  sustained realistic project activity with warm search, edit, and LSP
  latency assertions. Use when resource stats look correct in synthetic
  scenarios but regress under a larger build graph.
- `full_system_capacity_matrix`: broad sweep that intentionally exercises
  synthetic OCC conflicts (SymlinkChange, anchor-not-found, non-zero
  shell). Use to sanity-check the whole stack and to validate that typed
  conflicts stay typed.
- `background_shell_*` (T1-T8): seven harness scenarios plus one unit
  test covering the daemon-native `shell(background=True)` launch/poll/
  cancel/reap surface. Each scenario uses a single executor action that
  drives the matching probe in
  `backend/src/task_center_runner/agent/mock/background_shell_probe.py`;
  probes call `shell_tool` with `background_task_id` set, write a JSON
  summary to `/testbed/.ephemeralos/sweevo-mock/background_shell/<mode>/
  summary.json`, and the tests read it back via `sandbox_api.read_file`.
  - `background_shell_golden` (T1): 3 concurrent 5-s sleeps; confirms
    natural-exit reap, exit_code 0, populated stdout. Sanity for the
    launch/reap roundtrip and the rmtree-before-read regression.
  - `background_shell_cancel` (T2): 3 long shells cancelled at 1 s via
    `asyncio.wait_for`. Asserts AC-3 (post-cancel foreground mount under
    5 s) and AC-6 (zero changed_paths from cancelled jobs).
  - `background_shell_interleave` (T3): 1 long background lease + 5
    foreground shells. Records foreground p95
    `command_exec.mount_workspace_s`; AC-3 expects p95 under 5 s while a
    background lease is held.
  - `background_shell_exhaustion` (T5): 80 launches cancelled at 2 s.
    AC-14 — post-exhaustion `read_file` under 1 s proves the daemon RPC
    dispatcher is decoupled from the `ShellExecutor`. Watch
    `command_exec.mount_workspace_s` p95 — concurrency contention shows
    up here (~5-8× T1 baseline).
  - `background_shell_partial_write_cancel` (T6): 800 MB dd into a
    tracked path, cancelled at 2 s. Reads back the target after cancel;
    AC-6 — upperdir is discarded and the OCC publish is skipped. Probe
    wraps dd in `for ... do ... done` so it doesn't match the
    DestructiveShellPreHook regex `[;&|]\s*dd\s+.*of=/`, and seeds a
    sentinel file so OCC persists the parent dir across leases.
  - `background_shell_cancel_during_maintenance` (T7): short shell that
    writes one file + maintenance pass. Asserts the workspace OCC stays
    consistent (target in `changed_paths`, follow-up read returns the
    written content).
  - `background_shell_late_cancel_race` (T8): await a 1-s shell to
    completion. AC-10 — exit_code 0 and stdout preserved (completed >
    failed > cancelled precedence holds when the natural exit wins the
    race).
  - `test_background_shell_engine_kill` (T4): in-process unit test for
    `ShellJobRegistry` TTL reaper. No sandbox, no scenario harness —
    leave alone.

  Background-mode plumbing: `runner._call_tool(background_task_id=...)`
  pipes the bg-id into `ExecutionMetadata.with_overrides` so
  `shell.py:154` flips to the daemon launch/poll/cancel/reap surface.
  Cancel propagation matches the production engine path: an
  `asyncio.wait_for` timeout becomes a `CancelledError` that
  `_shell_background_dispatch._send_cancel_then_reap` handles.

## Run Configuration

Treat `ephemeralos.yaml` as the source of truth for live-run gates and sandbox
reuse. Do not add removed one-off runner-policy env toggles to run commands.

Required YAML shape:

```yaml
runner:
  sandbox_reuse_mode: reuse
  live_e2e:
    heavy_enabled: true
    capacity_enabled: true
```

Use `runner.sandbox_reuse_mode` rather than adding a separate `reuse_sandbox`
field. The supported values are `fresh`, `reuse`, and `force_fresh`.

Sourcing `.env` is still acceptable for secrets such as database and provider
credentials, but not for live-run gate or sandbox-reuse policy:

```bash
set -a; source .env; set +a
uv run pytest -q -x --tb=short --durations=20 \
  backend/src/task_center_runner/tests/mock/sandbox/capacity/test_full_system_capacity_matrix.py
```

If a heavy or capacity test skips unexpectedly, inspect `ephemeralos.yaml` and
`backend/src/task_center_runner/tests/_live_config.py` before changing the
command line.

## Autonomous Run Loop

While tests run, operate an artifact-backed monitoring loop instead of waiting
for pytest summaries. The first step of every loop is to read the current
iteration tracker so the last finding, hypothesis, fix, verification result,
and next target are fresh. Repeat the loop every 30 seconds until the run
finishes or the first actionable failure appears.

0. Read the iteration tracker before starting or resuming a run. If it does not
   exist yet, decide where it will live using the Iteration Finding Notes rules
   below.

1. Resolve the active run directory. Prefer the newest `run.json` under
   `.sweevo_runs/scenario_logs`.

```bash
RUN_DIR="$(find .sweevo_runs/scenario_logs -maxdepth 3 -name run.json | sort | tail -1 | xargs dirname)"
printf 'RUN_DIR=%s\n' "$RUN_DIR"
```

2. Poll run state and progress counters.

```bash
jq '{status, started_ts, finished_ts, scenario_name, sandbox_id}' "$RUN_DIR/run.json"
test -f "$RUN_DIR/message.jsonl" && wc -l "$RUN_DIR/message.jsonl"
test -f "$RUN_DIR/sandbox_events.jsonl" && wc -l "$RUN_DIR/sandbox_events.jsonl"
```

3. Inspect the newest activity.

```bash
test -f "$RUN_DIR/message.jsonl" && tail -n 40 "$RUN_DIR/message.jsonl"
test -f "$RUN_DIR/sandbox_events.jsonl" && tail -n 80 "$RUN_DIR/sandbox_events.jsonl"
```

4. Search for stop signals before the suite completes.

```bash
test -f "$RUN_DIR/sandbox_events.jsonl" && \
  rg -n "internal_error|manifest references missing layer|stale lowerdir|untyped conflict|mount_failed|import failure|remount failure" "$RUN_DIR/sandbox_events.jsonl"
test -f "$RUN_DIR/message.jsonl" && \
  rg -n "failed|cancelled|internal_error|Traceback|TimeoutError" "$RUN_DIR/message.jsonl"
```

5. When a performance report appears, summarize it immediately.

```bash
test -f "$RUN_DIR/performance_report.json" && \
  python3 .agents/skills/sandbox-performance-evaluation/scripts/summarize_sandbox_perf.py "$RUN_DIR"
```

6. For audit/stats work, inspect the V3 sections directly. A report can have
   passing legacy rollups while a release-gate surface is still incomplete.

```bash
test -f "$RUN_DIR/performance_report.json" && \
  jq '{
    schema,
    sections: (.sandbox.sections | keys),
    summary: .sandbox.sections.summary,
    daemon_audit_pull: .sandbox.sections.daemon_audit_pull,
    overhead_verdict: .sandbox.sections.overhead.gate.verdict,
    warnings: .sandbox.sections.warnings.rows
  }' "$RUN_DIR/performance_report.json"
```

7. Check both event-storage surfaces. The daemon pull ring is in sandbox
   process memory; the persisted `sandbox_events.jsonl` is a host-side run
   artifact. If isolated-workspace direct tiers are involved, also check the
   sandbox-local IWS JSONL file.

```bash
test -f "$RUN_DIR/sandbox_events.jsonl" && \
  find "$RUN_DIR" -maxdepth 1 \( -name 'sandbox_events.jsonl' -o -name 'sandbox_events.jsonl.*.gz' \) \
    -printf '%f %s bytes\n' | sort

SANDBOX_ID="$(jq -r '.sandbox_id // empty' "$RUN_DIR/run.json")"
export SANDBOX_ID
uv run python - <<'PY'
import asyncio
import json
import os

from sandbox.api import audit_snapshot, raw_exec

async def main() -> None:
    sandbox_id = os.environ.get("SANDBOX_ID", "")
    if not sandbox_id:
        print("SANDBOX_ID missing")
        return
    snapshot = await audit_snapshot(sandbox_id)
    print(json.dumps({"daemon_ring": snapshot.get("buffer", {})}, indent=2, sort_keys=True))
    result = await raw_exec(
        sandbox_id,
        "for p in /tmp/sandbox_isolated_workspace_events.jsonl \"${EOS_ISOLATED_WORKSPACE_AUDIT_PATH:-}\"; do "
        "[ -n \"$p\" ] && [ -e \"$p\" ] && stat -c '%n %s bytes' \"$p\"; "
        "done",
        cwd="/",
        timeout=10,
    )
    if result.stdout.strip():
        print(result.stdout.strip())

asyncio.run(main())
PY
```

Healthy progress means `run.json` is still running, `message.jsonl` or
`sandbox_events.jsonl` line counts advance between loops, and tool calls are not
stuck outstanding. Stop the active pytest run and diagnose the first actionable
signal when any of these happen:

- `run.json` or task artifacts show failed, cancelled, or no forward progress.
- `message.jsonl` stops advancing while tool calls are outstanding.
- `sandbox_events.jsonl` repeats internal errors, stale lowerdirs, missing layers, untyped conflicts, mount failures, import failures, or remount failures.
- `performance_report.json` shows the wrong schema, missing V3 sections,
  incomplete tool calls, high error rate outside expected synthetic conflicts,
  drop/loss in daemon-audit pull stats, failed release-gate verdicts, or a
  clear latency step-up.
- Daemon ring snapshot shows high pressure, `retained_bytes > max_bytes`,
  nonzero `dropped_event_count`, or nonzero `lost_before_seq`.
- Host event artifacts exceed the artifact-bound gate, rotate unexpectedly
  often, or fail to gzip/retain history.
- Sandbox-local IWS audit JSONL grows without bound across direct pytest tiers.
- Resource metrics show workspace copies in namespace mode or upperdir/scratch growth proportional to repository size.

After every fix, write the iteration tracker entry described below, rerun the
narrowest scenario that exposed it, inspect artifacts, then resume the broader
sweep.

## Iteration Finding Notes

Maintain an iteration tracker during any multi-step performance/debugging run.
This is mandatory for sandbox audit, performance, resource, and live-E2E work.
The tracker is both the first and last step of the iteration loop:

- First step: read the existing tracker before running or inspecting anything.
  Use it to recover the last finding, current hypothesis, fix already applied,
  verification result, and next target.
- Last step: write or update the tracker after the run/inspection/fix/verify
  cycle and before starting the next test iteration.

Do not start a new iteration until the previous iteration's note has been
written.

Where to write it:

- Prefer an existing task-owned report such as `IMPLEMENTATION-REPORT.md`,
  `NEXT-FIXES.md`, a plan-specific iteration report, or the user-specified
  handoff file.
- If no report exists, create `ITERATION-REPORT.md` beside the targeted test
  directory or under the smallest task-owned artifact directory. Do not write
  it into unrelated docs or the generated `.sweevo_runs` run directory unless
  the user explicitly asked for run-local notes.

Each iteration entry must include:

- Iteration number and timestamp.
- Exact command run.
- Exact run directory or artifact paths inspected.
- Pass/fail/skip status.
- Findings summary: what changed, what progressed, and what evidence proves it.
- Issues found: first concrete failure, warning, hang, bottleneck, or resource
  regression.
- Why it failed: current causal hypothesis, tied to code paths or artifact
  fields. Mark it as a hypothesis when not proven.
- Fix applied: files/functions changed and why that fix addresses the finding.
- Verification result after the fix, including the next command to run.
- Remaining risk or next iteration target.

Do not start the next iteration with only terminal scrollback as memory. If the
next run fails, the tracker should let a new agent reconstruct the previous
state without rereading the entire conversation.

## Artifact Analysis

Use the bundled summarizer for compact evidence:

```bash
python3 .agents/skills/sandbox-performance-evaluation/scripts/summarize_sandbox_perf.py \
  .sweevo_runs/scenario_logs/<scenario>/<run>
```

Inspect these files directly when needed:

- `run.json`: status, scenario, timestamps, sandbox id.
- `task.json`: task terminal state and failure detail.
- `message.jsonl`: live progress and repeated tool errors.
- `sandbox_events.jsonl`: low-level timings, resource snapshots, conflict events.
- `metrics.json`: scenario-level counters.
- `performance_report.json` or `.md`: per-tool call speed, slow calls, error totals, sandbox timings.
- `performance_report.json["sandbox"]["sections"]`: V3 audit/report mirror.
  Inspect `summary`, `per_tool_timing`, `per_tool_phase_breakdown`,
  `background_tool_calls`, `plugin_activity`, `overlay_workspace`,
  `layer_stack`, `occ`, `isolated_workspace`, `os_resource`,
  `daemon_audit_pull`, `overhead`, and `warnings` before claiming a report is
  complete.
- Daemon ring snapshot from `api.audit.snapshot`: current in-sandbox memory
  footprint for pulled events. Read `buffer.retained_events`,
  `buffer.retained_bytes`, `buffer.max_events`, `buffer.max_bytes`,
  `buffer.pressure`, `buffer.dropped_event_count`,
  `buffer.dropped_event_count_by_lane`, and `buffer.lost_before_seq`.
- Host artifact inventory for pulled events: live `sandbox_events.jsonl` plus
  `sandbox_events.jsonl.<N>.gz` rotations under the scenario run directory.
  This is the persistent event log for daemon-pulled events.
- isolated_workspace daemon audit JSONL:
  `/tmp/sandbox_isolated_workspace_events.jsonl` inside the SWE-EVO container,
  read through the test fixture or `raw_exec`. The path can be overridden with
  `EOS_ISOLATED_WORKSPACE_AUDIT_PATH`. This sandbox-local file is separate
  from the V3 daemon audit pull ring and should be sized separately.

Key timing fields:

- command execution: `command_exec.mount_workspace_s`, `command_exec.run_command_s`, `command_exec.capture_upperdir_s`, `command_exec.total_s`, `api.shell.total_s`.
- layerstack: `layer_stack.prepare_workspace_snapshot.total_s`, `layer_stack.publish.total_s`, `layer_stack.transaction.lock_wait_s`, `layer_stack.transaction.lock_held_s`.
- OCC: `occ.apply.total_s`, `occ.serial.queue_wait_s`, `occ.apply.commit_queue_wait_s`, `occ.apply.commit_worker_s`.
- file APIs: `api.read.lease_acquire_s`, `api.write.lease_acquire_s`, `api.edit.lease_acquire_s`, `api.write.total_s`, `api.edit.total_s`.
- LSP: `lsp.total_s`, `lsp.<op>.body_s`, `lsp.session.start_count_delta`, `lsp.session.refresh_count_delta`, `lsp.session.remount_count_delta`, `lsp.session.private_overlay_namespace`, `lsp.session.has_overlay_handle`.
- audit/resource collection: `resource.audit.collect_s`,
  `daemon_audit_pull.pull_ms`, `tool_call.phase_totals_rollup.*_ms`, and
  `sandbox.sections.overhead.tool_latency_p95_delta_ms`.

Key resource fields (per-op, shown under `resource_max` in the summary):

- O(1) overlay disk: `resource.command_exec.workspace_tree_exists` should be `0` and `resource.command_exec.workspace_tree_bytes` should be `0` in private namespace mode.
- Per-op writes: `resource.command_exec.upperdir_tree_bytes` should scale with changed paths, not repository size or concurrency.
- Scratch: `resource.command_exec.run_dir_tree_bytes` and scratch filesystem used bytes should stay small and transient.
- Truncation flags: `resource.command_exec.run_dir_tree_truncated`,
  `resource.command_exec.upperdir_tree_truncated`, and
  `resource.command_exec.workspace_tree_truncated` should be `0` in normal
  gates; a `1` means the resource sample is capped and byte/count conclusions
  need extra evidence.
- Operation scale: `resource.command_exec.changed_path_count`,
  `resource.command_exec.upperdir_tree_file_count`, and
  `resource.command_exec.upperdir_tree_entry_count` should match the workload
  shape.
- Layer depth: `resource.layer_stack.manifest_depth` and
  `resource.layer_stack.manifest_path_count` should stay below operational
  squash targets.
- Process/cgroup: `resource.process.rss_bytes`,
  `resource.process.max_rss_bytes`, `resource.cgroup.cpu_*`,
  `resource.cgroup.io_*`, `resource.cgroup.memory_current_bytes`, and
  `resource.cgroup.memory_peak_bytes`.

Key V3 report checks:

- `schema == "task_center_runner.performance_report.v3"`.
- `sandbox.sections.summary.event_count` matches the number of normalized
  sandbox rows unless a documented restart/partial flush explains drift.
- `sandbox.sections.daemon_audit_pull.puller_attached` is true for normal
  sandbox-backed runs, with `dropped_event_count == 0` and
  `lost_before_seq == 0`.
- Live `api.audit.snapshot` reports daemon ring `retained_bytes <= max_bytes`
  and normal pressure below 0.8. The design target is an 8 MiB daemon ring and
  zero sandbox-side disk writes for the V3 pull path.
- `sandbox.sections.overhead.gate.verdict` has true values for the gates you
  are claiming. `overhead_pass` cannot be claimed when methodology is absent.
- `sandbox.sections.os_resource` reports CPU/IO deltas and RSS peak from
  `os_resource.sampled`, not from cgroup lifetime totals.
- `sandbox.sections.warnings.rows` is empty or contains only expected,
  explained warnings. Treat `audit.dropped`, `audit.pressure`,
  `audit.events_count_drift`, `isolated_workspace.gate_failure`,
  `os_resource.memory_peak`, `overlay_workspace.upperdir_cap`, and
  `layer_stack.squash_failed` as stop signals until explained.

Isolated workspace audit fields:

- enter: `total_ms`, `phases_ms.prepare_snapshot`,
  `phases_ms.spawn_ns_holder`, `phases_ms.open_ns_fds`,
  `phases_ms.install_veth`, `phases_ms.mount_overlay`,
  `phases_ms.configure_dns`, `phases_ms.create_cgroup`,
  `lowerdir_layer_count`, `materialize`, `ns_ip`, `rfc1918_egress_mode`.
- tool call: `total_ms`, `duration_s`, `argv0`, `exit_code`,
  `phases_ms.unfreeze`, `phases_ms.exec`, `phases_ms.freeze`.
- exit: `total_ms`, `lifetime_s`, `upperdir_bytes_discarded`,
  `phases_ms.kill_holder`, `phases_ms.teardown_veth`,
  `phases_ms.release_snapshot`, `phases_ms.cgroup_rmdir`,
  `phases_ms.rmtree_scratch`.
- eviction/GC: `reason`, `kind`, `identifier`, `released`, `total_ms`,
  `phases_ms.discover`, `phases_ms.reap`.

Isolated workspace performance standards:

- Every emitted `phases_ms` map passes SUBSET-COVER and contains only
  documented keys for that event type.
- `sandbox_isolated_workspace_enter` appears once per successful enter and
  carries distinct `handle_id`s under concurrent N=5 enter.
- `install_veth` contention in Tier 8 stays within `max <= 5 * median`.
- `upperdir_bytes_discarded` reflects discard size, and re-entering the same
  agent after exit must not see the previous upperdir contents.
- Lowerdir paths remain shared across concurrent handles; lowerdir disk usage
  is O(1), while upperdir/scratch usage scales with each handle's writes.
- The isolated path must not produce OCC publish events during full
  enter/tool/exit cycles.

Sandbox audit/stat performance standards:

- Every live mock sandbox scenario that claims performance coverage must
  produce `performance_report.json` and `sandbox_events.jsonl`; missing reports
  are test failures, not "no data".
- The report must keep both surfaces useful: legacy `sandbox.timing_keys` /
  `sandbox.resource_keys` for existing assertions, and V3 `sandbox.sections`
  for release-gate and subsystem summaries.
- `tools.per_tool[*]` must include count, errors, mean, p50, p95, max, and
  representative samples with `timings_s` for shell, read/write/edit, search,
  plugin, and LSP-heavy scenarios.
- Warm p95 budgets are scenario-specific. Current mock sandbox gates commonly
  use read/grep/glob <= 500 ms and write/edit <= 1,000 ms for warm
  ephemeral/project-build paths, foreground sandbox p95 <= 5,000 ms under
  background pressure, and LSP no-refresh/warm p95 <= 500 ms.
- `resource.audit.collect_s` should stay tiny relative to
  `command_exec.total_s`. If collection time grows with repository size, check
  tree-stat bounding before changing latency budgets.
- Tree resource samples must not be truncated in normal gates. If a
  `*_tree_truncated` max is `1`, do not use the paired byte/count values to
  prove O(1) behavior until you inspect the raw event and workload.
- Audit pull must be drop-free for a clean run:
  `dropped_event_count == 0`, `lost_before_seq == 0`, and no unexplained
  high buffer pressure. A report with `puller_attached=false` can still be
  structurally valid but does not prove the daemon-audit path.
- Artifact footprint should stay within the V3 bound:
  live `sandbox_events.jsonl` <= 64 MiB plus at most 8 rotated gzip files of
  <= 8 MiB each, unless the run explicitly opts into larger diagnostics.
- Sandbox-side event-log footprint should be zero for daemon-pulled events
  because the daemon ring is memory-only. The only expected sandbox-local
  audit JSONL in this workflow is the isolated-workspace direct-test sink; size
  it explicitly and treat growth across tests as test cleanup or sink-retention
  risk.
- Audit overhead release-gate thresholds come from code:
  latency p95 delta CI upper <= 5 ms, daemon RSS delta <= 16 MiB, runner CPU
  p99 <= 0.5%, daemon CPU p99 < 1%, and sandbox disk delta == 0.

### Cgroup counters — lifetime vs run delta

`resource.cgroup.*` values are **monotonic cumulative counters** maintained by
the Linux kernel against the sandbox cgroup. In `sandbox_reuse_mode: reuse`,
the same Daytona sandbox is shared across many test sessions, so the raw
value at any sample point is **sandbox lifetime since cgroup creation**, not
this test's contribution. Always read the run delta first; only consult the
lifetime value when watching for hard limits (memory.max, disk quota).

The summarizer splits this for you:

- `cgroup_run_delta`: last_sample − first_sample for this run. The right
  number for "how much did this test write / use".
- `cgroup_lifetime`: end-of-run cumulative value. Useful only as a sanity
  check against cgroup `memory.max` or storage quotas.

Memory fields (`memory_current_bytes`, `memory_peak_bytes`) are gauges, not
counters. Read the `cgroup_lifetime` peak as "highest observed in-flight
resident memory" — it does not accumulate across test sessions, so it is
already meaningful as an absolute.

Healthy SWE-EVO-sandbox baselines (image already loads conda + dask +
Pyright):

- `memory_peak_bytes` (lifetime): **1.0-1.5 GB** baseline. Concerning above
  4 GB or when growing run-over-run on stable workloads.
- `io_wbytes` (run delta): scales with on-disk workload. Expect
  **~5× write amplification** over the dd payload because overlay scratch
  staging, OCC commit copy, and the audit DB all write to the same cgroup.
  (e.g. heavy_io_zoned writes ~165 MB of raw dd payload across 15 shells
  and reports ~520 MB run-delta `io_wbytes` — within band.)
- `cpu_usage_usec` (run delta): scales with shell body CPU plus daemon
  overhead. Expect tens of seconds of CPU on a multi-minute heavy run.

## Interpretation Rules

- If `layer_stack.prepare_workspace_snapshot.total_s` is low but tool latency is high, do not blame layerstack. Split mount, command body, OCC queue, LSP body, and provider/runtime overhead.
- If high concurrency slows writes, check OCC queue timing before blaming overlay mount.
- If shell calls are slow but `command_exec.mount_workspace_s` is small, the bottleneck is usually command body, process/runtime scheduling, or CPU contention.
- If LSP gets slow after writes, compare `lsp.session.start_count_delta` with `remount_count_delta`. Repeated restarts are a correctness/performance regression; remounts are expected.
- If `layer_stack.materialize_s` is nonzero in a private namespace run, verify whether the code fell back from mount(2) overlay to materialized/copy-backed mode.
- If workspace tree bytes are nonzero in namespace mode, treat it as a possible O(1) disk regression.
- Expected synthetic OCC conflicts must be typed conflicts, not internal errors. Count them separately from correctness failures.
- If V3 `sandbox.sections` is missing or empty while legacy timing/resource
  rollups exist, treat it as a report-consumer regression. The legacy blocks
  are compatibility output, not the release-gate surface.
- If daemon audit pull counters show dropped or lost events, avoid precise
  percentile/resource claims until you prove the missing events are unrelated
  to the metric being reported.
- Do not look for daemon-pull event persistence under `/tmp` as the normal
  path. Per design, pulled daemon events live in an in-memory ring in the
  sandbox and are persisted by the host-side runner sink. `/tmp` JSONL audit
  files are isolated-workspace direct-test artifacts unless a test explicitly
  overrides a path.
- If `resource.audit.collect_s` or §11 pull p95 jumps while command body time
  is flat, investigate audit sampling, JSONL writing, pull cadence, and report
  generation before changing sandbox operation budgets.
- If event-count drift appears, compare `sandbox_events.jsonl` rows,
  `sandbox.sections.summary.event_count`,
  `sandbox.sections.daemon_audit_pull.events_pulled`, and
  `daemon_restarts_observed`. A daemon restart can explain drift; a normal
  run should not.
- Never quote `cgroup_lifetime` `io_*bytes` as "this test wrote X GB" in a report. Quote the `cgroup_run_delta` instead, and only mention lifetime if the sandbox is approaching a quota or limit.
- For isolated_workspace, do not expect `.sweevo_runs/scenario_logs` for the
  direct pytest-tier tests. Use the daemon JSONL audit file plus pytest
  failure output as the primary evidence. Scenario logs still apply when an
  isolated workspace issue is exercised through a broader scenario harness.
- If isolated_workspace full live passes one-at-a-time but fails in the
  combined non-soak run, inspect daemon lifetime artifacts first: open handles,
  zombie `ns_holder`/`unshare` processes, veth count, cgroup directories, and
  `manager.json`. Treat accumulation as a cleanup or test-isolation bug, not a
  proof that the individual correctness invariant failed.

## Report Shape

Final reports should include:

- Exact commands run.
- Exact scenario log directories inspected.
- Pass/fail status and whether failures were expected synthetic conflicts.
- Per-tool latency summary, especially mean/p95/max for shell, write/edit/read, plugin, and LSP tools.
- V3 report-section status: schema, section keys present, daemon-audit pull
  counters, overhead gate verdict, artifact inventory, and warnings.
- Sandbox-operation timing evidence for mount, layerstack prepare/publish, OCC apply/queue, and LSP remount/restart behavior.
- Disk evidence for workspace tree bytes, upperdir bytes, scratch bytes,
  truncation flags, host artifact JSONL/rotation bytes, sandbox daemon ring
  retained bytes, sandbox-local isolated-workspace audit JSONL bytes, and
  manifest depth.
- CPU/memory/IO evidence from `cgroup_run_delta` (per-run) and `cgroup_lifetime` (sandbox-lifetime) — never conflate the two.
- Audit path overhead evidence: `resource.audit.collect_s`, §11 pull latency,
  dropped/lost event counters, buffer pressure, runner/daemon CPU, daemon RSS
  delta, and whether methodology metadata was present.
- Fixes made, targeted verification after each fix, and the broader rerun result.
