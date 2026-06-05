# SPEC: `eos-e2e-test` Coverage Expansion and Module README Contracts

Status: DRAFT
Date: 2026-06-05
Owner doc: `docs/plans/eos-e2e-test-coverage-expansion_SPEC.md`
Scope: `sandbox/crates/eos-e2e-test/tests`, `sandbox/crates/eos-e2e-test/src`,
and module-local E2E config files under `sandbox/crates/eos-e2e-test/tests/*/config`.

This spec converts the read-only coverage plan into implementation requirements
for expanding the Rust sandbox E2E suite. It does not implement tests. The first
implementation step is documentation: create one module `readme.md` per test
target, then add correctness, performance, resource, and leak coverage against
the contracts below.

The E2E harness remains protocol-only: operations under test go through
`eos-protocol` against a live `eosd`. Docker lifecycle is harness
infrastructure, not a sandbox operation oracle.

---

## 1. Goals

1. Add a `readme.md` coverage contract for every integration target under
   `sandbox/crates/eos-e2e-test/tests`.
2. Make each README load-bearing: every checklist item must be covered by at
   least one listed scenario-style test case.
3. Expand correctness coverage for OCC, ephemeral workspaces, isolated
   workspaces, file ops, command sessions, plugins, LayerStack/overlay, daemon
   control, and pressure.
4. Promote performance and resource behavior to first-class E2E assertions.
5. Add explicit concurrency comparisons at levels `1`, `3`, `6`, and `12`
   where the subsystem can support those levels.
6. Keep correctness, concurrency, resource, and performance workload knobs in
   module-local YAML contracts backed by typed harness config, not generic
   cost-class variants or ad hoc environment overrides.

## 2. Non-Goals

- No implementation in this spec.
- No model-facing tool rename.
- No daemon wire op rename.
- No non-Docker sandbox provider.
- No agent-core workflow or LLM coverage in `eos-e2e-test`.
- No reliance on stale Python backend surfaces.
- No test oracle that bypasses the daemon protocol for sandbox behavior. Docker
  and container process scans are allowed only for harness lifecycle and leak
  probes where the daemon protocol has no equivalent signal.

---

## 3. Current Harness Facts

The crate already has 8 integration targets and roughly 114 live test
functions:

| Target | Current source files |
|---|---|
| `core` | `command_sessions.rs`, `direct_file_ops.rs`, `envelope_contract.rs`, `errors_and_limits.rs`, `runtime_setup.rs`, `smoke_paths.rs` |
| `daemon` | `control_cancel.rs`, `control_heartbeat.rs`, `control_inflight.rs`, `op_registration.rs`, `runtime_identity.rs` |
| `ephemeral_workspace` | `command_sessions.rs`, `overlay_exec.rs` |
| `isolated_workspace` | `command_sessions.rs`, `lifecycle.rs`, `network.rs`, `no_publish.rs`, `tool_routing.rs` |
| `layerstack` | `commit_to_git.rs`, `commit_to_workspace.rs`, `lease.rs`, `squash.rs`, `squash_bounds.rs`, `squash_deep.rs` |
| `occ` | `gating.rs`, `merge.rs` |
| `plugin` | `isolated_gate.rs`, `lsp.rs`, `packages.rs` |
| `pressure` | `concurrency.rs`, `cross_subsystem.rs`, `failure_recovery.rs` |

Existing harness commands:

```bash
cd /Users/yifanxu/machine_learning/LoVC/EphemeralOS/sandbox
cargo test -p eos-e2e-test
cargo run -p xtask -- package --target x86_64-unknown-linux-musl --out-dir dist
cargo test -p eos-e2e-test --features e2e -- --nocapture
cargo test -p eos-e2e-test --features e2e --test <target> <filter> -- --nocapture
```

Live defaults come from `sandbox/config/prd.yml`; each target may merge one
module-local `config/default.test.yml`. Today those overrides are thin:

- Most targets enable `isolated_workspace` with smaller upperdir and memory
  settings.
- `layerstack` sets `daemon.layer_stack.auto_squash_max_depth: 8`.
- `plugin` forces `eos_e2e_test.pool.mode: per-test` and disables kept
  containers.

The harness config structs use strict typed fields. New workload knobs must be
added to the typed config before YAML can carry them.

---

## 4. README Contract

Create these files before expanding tests:

```text
sandbox/crates/eos-e2e-test/tests/core/readme.md
sandbox/crates/eos-e2e-test/tests/daemon/readme.md
sandbox/crates/eos-e2e-test/tests/ephemeral_workspace/readme.md
sandbox/crates/eos-e2e-test/tests/isolated_workspace/readme.md
sandbox/crates/eos-e2e-test/tests/layerstack/readme.md
sandbox/crates/eos-e2e-test/tests/occ/readme.md
sandbox/crates/eos-e2e-test/tests/plugin/readme.md
sandbox/crates/eos-e2e-test/tests/pressure/readme.md
```

Each README must use exactly this section structure:

```md
# <module>

## Overview

## Checklist

- [ ] <module>-<stable-id>: <target behavior>

## Test Case

| Test name | Test description | Command to run | Checklist item |
|---|---|---|---|
```

README requirements:

1. The overview names the owning subsystem, the daemon ops under test, the
   module-local config path, and any named special-purpose config variant it
   uses.
2. Checklist IDs are stable and local to the module, for example
   `occ-git-drop` or `iws-port-matrix`.
3. Every checklist item appears in at least one `Test Case` row.
4. Each module has at most five `Test Case` rows. Rows are scenario contracts,
   not a one-row-per-Rust-function inventory.
5. Checklist and test rows are many-to-many. Because live sandbox setup is
   expensive, one test case should cover multiple checklist items when the
   assertions naturally share a daemon/container setup; do not split a coherent
   live scenario into one test per checklist item.
6. A row may name several existing Rust test functions in its description when
   those functions already implement part of the scenario. Planned coverage may
   be grouped into the same row when it completes the scenario.
7. Commands use Cargo filters that a developer can run directly from
   `sandbox/`.
8. README text records known expensive or long-running tests but does not record
   transient pass/fail claims.

Add a later validation check that compares `cargo test -p eos-e2e-test
--features e2e -- --list` or an `rg` inventory against the README tables.

---

## 5. Unified Config Contract

Keep `config/default.test.yml` as the default target config for each module.
Modules may add named `*.test.yml` variants only when a concrete subsystem test
needs a distinct daemon or workload contract, but a live run uploads and merges
at most one module-local `*.test.yml`. Do not add generic cost-class variant
files; variant names must describe the actual special need, for example
`short-ttl.test.yml`, `isolated-cap-12.test.yml`, or
`plugin-reload.test.yml`.

Required typed workload config additions:

| Field | Purpose |
|---|---|
| `workload.concurrency_levels` | Default `[1, 3, 6, 12]` for ladder tests. |
| `workload.write_iterations` | Bound repeated write/squash/refresh loops. |
| `workload.sample_count` | Performance sample count before summary artifact emission. |
| `workload.perf_artifact_dir` | Directory for JSON performance reports. |
| `workload.timeout_s` | Workload operation budget independent of socket timeout. |

Unified workload policy:

- The committed default ladder is `1/3/6/12`; a test may use a subset only when
  the daemon subsystem exposes a hard cap and the test asserts the cap behavior.
- Repeated samples emit JSON artifacts before any strict regression threshold is
  introduced.
- No fragile absolute latency assertions in the first version.
- Structural bounds come first: O(1) resource shape, no monotonic leaks, no
  orphaned sessions or processes, and no unbounded durable storage growth.

---

## 6. Global Performance and Resource Oracles

Use at least two independent signals for resource-sensitive tests.

Protocol-visible oracles:

- `api.layer_metrics`: `manifest_depth`, `active_leases`, `leased_layers`,
  `layer_dirs`, `referenced_layers`, `staging_dirs`, `storage_bytes`.
- `api.v1.command_session_count`.
- `api.plugin.status`.
- `api.audit.pull`.
- Response `timings`, including `runtime.dispatch_s`,
  `api.write.occ_apply_s`, `api.read.layer_stack_read_s`,
  `resource.command_exec.upperdir_tree_bytes`,
  `resource.command_exec.run_dir_tree_bytes`,
  `resource.command_exec.workspace_tree_bytes`,
  `resource.cgroup.cpu_*`, and `resource.cgroup.io_*`.

Audit-visible oracles:

- `occ.publish`.
- `occ.conflict`.
- `layer_stack.lease_acquired`.
- `layer_stack.lease_released`.
- `layer_stack.squash_triggered`.
- `layer_stack.squash_completed`.
- `overlay_workspace.cleanup`.

Host/container probes allowed only for leak checks:

- `/proc` marker process scans after cancel, terminate, reload, and daemon
  cleanup.
- File/socket cleanup checks under `/eos/scratch` only when daemon protocol does
  not expose an equivalent signal.

Do not rely on zero-valued `orphan_layer_count` or `missing_layer_count` as the
only leak oracle. Treat them as supplemental until they are backed by real
enumeration.

---

## 7. Module Coverage Contracts

### 7.1 `occ`

Checklist:

| ID | Requirement |
|---|---|
| `occ-git-drop` | `.git/**` changes are dropped, unreadable, and do not advance manifest state. |
| `occ-gitignored-direct` | Gitignored paths route direct and bypass gated OCC hash checks. |
| `occ-tracked-gated` | Non-gitignored paths route gated and publish through OCC. |
| `occ-disjoint-merge` | Concurrent disjoint tracked writes all commit and remain readable. |
| `occ-conflict-report` | Concurrent same-path edits produce structured conflict results and coherent final content. |
| `occ-edit-anchor-errors` | Missing anchor and ambiguous multiple occurrence edits return no-op conflict payloads. |
| `occ-audit-accounting` | Publish and conflict paths emit audit events and route timing counters. |
| `occ-result-catalog` | Committed, rejected, dropped, and edit-conflict FileResult statuses keep stable wire names and reasons. |

Planned test cases:

| Test name | Description | Command | Checklist |
|---|---|---|---|
| `occ-route-and-drop-matrix` | Groups `.git` drop, gitignored direct routing, tracked gated routing, and planned same-path ignored direct race; strengthens route timings, unchanged manifest state, absent readback, and whole-payload final content. | `cargo test -p eos-e2e-test --features e2e --test occ gating -- --nocapture` | `occ-git-drop`, `occ-gitignored-direct`, `occ-tracked-gated`, `occ-audit-accounting` |
| `occ-concurrency-merge-conflict-matrix` | Groups disjoint merge, same-path conflict, and retry-budget pressure; asserts all disjoint writes remain readable and conflicting writers produce structured commit/conflict/rejection payloads with coherent final content. | `cargo test -p eos-e2e-test --features e2e --test occ merge -- --nocapture` | `occ-disjoint-merge`, `occ-conflict-report` |
| `occ-edit-and-result-catalog` | Groups edit overlap, create-only rejection, missing-anchor/no-publish planned coverage, and stable FileResult status names/reasons. | `cargo test -p eos-e2e-test --features e2e --test occ route_fileresult_catalog -- --nocapture` | `occ-edit-anchor-errors`, `occ-result-catalog`, `occ-audit-accounting` |
| `occ-publish-audit-accounting` | Groups tracked publish and conflict/rejection accounting so audit events, changed paths, and timing counters are checked as first-class oracles. | `cargo test -p eos-e2e-test --features e2e --test occ publish_accounting -- --nocapture` | `occ-tracked-gated`, `occ-audit-accounting` |

### 7.2 `ephemeral_workspace`

Checklist:

| ID | Requirement |
|---|---|
| `eph-per-call-workspace` | Each shell/exec operation gets a fresh ephemeral overlay over the latest LayerStack manifest. |
| `eph-outside-direct-fs` | Writes outside the workspace are not OCC-captured and land directly in the container filesystem. |
| `eph-upperdir-delta` | Upperdir bytes scale with changed bytes, not lowerdir/repo size. |
| `eph-overlay-cleanup` | Completed exec releases layer leases and removes overlay scratch. |
| `eph-occ-publish` | In-workspace exec changes publish through OCC after tool finish. |
| `eph-stale-exec-conflict` | Long-running exec from stale snapshot cannot overwrite newer direct file content silently. |

Planned test cases:

| Test name | Description | Command | Checklist |
|---|---|---|---|
| `eph-overlay-routing-publish-scenario` | Groups outside-workspace direct filesystem writes, in-workspace overlay capture, per-call workspace freshness, and OCC publish changed paths. | `cargo test -p eos-e2e-test --features e2e --test ephemeral_workspace overlay_exec -- --nocapture` | `eph-per-call-workspace`, `eph-outside-direct-fs`, `eph-occ-publish` |
| `eph-resource-cleanup-and-delta-scenario` | Groups overlay cleanup audit, active lease release, scratch cleanup, and upperdir bytes scaling with changed bytes rather than lowerdir size. | `cargo test -p eos-e2e-test --features e2e --test ephemeral_workspace foreground_exec_recycles_overlay_scratch -- --nocapture` | `eph-upperdir-delta`, `eph-overlay-cleanup` |
| `eph-command-descendant-lifecycle-scenario` | Groups foreground/background exec, lingering child semantics, terminate, cancel, and descendant cleanup so sessions do not leak across shared daemon setup. | `cargo test -p eos-e2e-test --features e2e --test ephemeral_workspace command_sessions -- --nocapture` | `eph-overlay-cleanup`, `eph-per-call-workspace` |
| `eph-stale-snapshot-conflict-scenario` | Planned scenario: start long-running exec from a stale snapshot, mutate the same file directly, release exec, and assert conflict/rejection with direct-write content preserved. | `cargo test -p eos-e2e-test --features e2e --test ephemeral_workspace long_running_exec_conflicts_after_direct_write -- --nocapture` | `eph-stale-exec-conflict`, `eph-occ-publish` |

### 7.3 `isolated_workspace`

Checklist:

| ID | Requirement |
|---|---|
| `iws-lifecycle-pin` | Enter pins a LayerStack manifest and exit releases the lease. |
| `iws-private-persistence` | Writes persist across tool calls while the isolated handle is open. |
| `iws-no-publish` | Isolated file and exec writes never publish through OCC. |
| `iws-discard-exit` | Exit discards private upperdir and public workspace cannot read private writes. |
| `iws-network-isolation` | Network namespace isolation allows same-port servers across isolated namespaces. |
| `iws-same-netns-conflict` | Same namespace still conflicts on same port. |
| `iws-exit-cleanup` | Exit tears down scratch, namespace, cgroup, holder, and lease state. |

Planned test cases:

| Test name | Description | Command | Checklist |
|---|---|---|---|
| `iws-lifecycle-private-state-scenario` | Groups enter/status/exit, manifest pinning, private writes, readback while open, exit discard, and public read absence after teardown. | `cargo test -p eos-e2e-test --features e2e --test isolated_workspace lifecycle -- --nocapture` | `iws-lifecycle-pin`, `iws-private-persistence`, `iws-discard-exit`, `iws-exit-cleanup` |
| `iws-no-publish-routing-scenario` | Groups isolated file write, edit, read, planned isolated exec write, no `occ.publish`, no lease release by private write, and ephemeral routing after exit. | `cargo test -p eos-e2e-test --features e2e --test isolated_workspace tool_routing -- --nocapture` | `iws-private-persistence`, `iws-no-publish`, `iws-discard-exit` |
| `iws-network-port-matrix-scenario` | Groups cross-mode same-port success, same-namespace conflict, dedicated netns reporting, and planned isolated-to-isolated same-port matrix. | `cargo test -p eos-e2e-test --features e2e --test isolated_workspace network -- --nocapture` | `iws-network-isolation`, `iws-same-netns-conflict`, `iws-exit-cleanup` |
| `iws-command-session-discard-scenario` | Groups isolated command session same-port behavior, exit discard, re-enter reuse, and command cleanup through the isolated caller handle. | `cargo test -p eos-e2e-test --features e2e --test isolated_workspace command_sessions -- --nocapture` | `iws-private-persistence`, `iws-network-isolation`, `iws-exit-cleanup` |

### 7.4 `core` File Ops and Command Sessions

Checklist:

| ID | Requirement |
|---|---|
| `core-runtime-base` | Runtime readiness, probes, workspace binding, base-layer metrics, rebuild timing, heartbeat idle state, and base audit fields remain protocol-visible and coherent. |
| `core-workspace-commit` | Committing the LayerStack view to the workspace survives a base rebuild and keeps manifest audit fields aligned with the response. |
| `core-envelope-guards` | Unknown ops, malformed frames, oversized requests, bad or missing auth, and isolated-mode plugin-family ops return deterministic structured errors. |
| `core-fast-file-ops` | Direct read/write/edit use fast paths and bypass overlay leasing. |
| `core-file-error-catalog` | Read/write/edit guards and edit conflicts return deterministic payloads. |
| `core-command-lifecycle` | `exec_command`, `write_stdin`, collect, cancel, timeout, and output cap behave correctly. |
| `core-command-cursors` | Session output cursors do not replay consumed output. |
| `core-command-terminate-kills-group` | `write_stdin(terminate)` kills the same-process-group child set. |
| `core-detached-child-contract` | `nohup` and `setsid nohup` behavior is explicitly decided and tested. |

Planned test cases:

| Test name | Description | Command | Checklist |
|---|---|---|---|
| `core-runtime-envelope-base-scenario` | Groups readiness, probes, heartbeat idle state, workspace binding/base setup, commit-to-workspace rebuild, malformed/auth envelope guards, and isolated-mode forbidden ops. | `cargo test -p eos-e2e-test --features e2e --test core runtime_setup -- --nocapture` | `core-runtime-base`, `core-workspace-commit`, `core-envelope-guards` |
| `core-direct-file-contract-scenario` | Groups direct write/read/edit, no overlay lease audit, OCC timings, repeated lease-zero writes, size guards, missing reads, anchor errors, count mismatch, create-only conflict, and planned `1/3/6/12` direct-file ladder. | `cargo test -p eos-e2e-test --features e2e --test core direct_file_ops -- --nocapture` | `core-fast-file-ops`, `core-file-error-catalog` |
| `core-command-lifecycle-cursor-scenario` | Groups exec lifecycle, stdin, completed collection, cancel, timeout, session count, output cap, and planned cursor no-replay assertions. | `cargo test -p eos-e2e-test --features e2e --test core command_sessions -- --nocapture` | `core-command-lifecycle`, `core-command-cursors` |
| `core-termination-detached-process-scenario` | Groups terminate/cancel descendant cleanup, process-group reaping, active lease drain, and planned `nohup` / `setsid nohup` contract decisions. | `cargo test -p eos-e2e-test --features e2e --test core command_sessions_cancel_cleans_descendant_processes -- --nocapture` | `core-command-terminate-kills-group`, `core-detached-child-contract` |

### 7.5 `layerstack`

Checklist:

| ID | Requirement |
|---|---|
| `layer-base` | Workspace base creation and rebuild are idempotent and visible through metrics. |
| `layer-lease-pin` | Active leases pin their frozen manifest and release to zero. |
| `layer-squash-depth` | Auto-squash keeps depth bounded by configured target. |
| `layer-squash-gap-formula` | Post-squash depth equals lease heads plus foldable gap runs. |
| `layer-storage-bounded` | Repeated overwrites do not grow durable layer storage linearly. |
| `layer-commit-workspace` | Commit to workspace materializes merged view and emits coherent version/timing data. |
| `layer-commit-git` | Commit to Git works after repeated squash and reports bounded depth. |

Planned test cases:

| Test name | Description | Command | Checklist |
|---|---|---|---|
| `layer-base-and-commit-scenario` | Groups base creation/rebuild visibility, commit-to-workspace merged view, version monotonicity, and audit/timing fields. | `cargo test -p eos-e2e-test --features e2e --test layerstack commit_to_workspace -- --nocapture` | `layer-base`, `layer-commit-workspace` |
| `layer-lease-and-squash-formula-scenario` | Groups lease acquire/release, lease pin under squash, hold-time ordering, and planned post-squash lease-head plus foldable-gap formula. | `cargo test -p eos-e2e-test --features e2e --test layerstack lease -- --nocapture` | `layer-lease-pin`, `layer-squash-depth`, `layer-squash-gap-formula` |
| `layer-storage-bound-scenario` | Groups auto-squash trigger, bounded depth, repeated overwrite storage growth, superseded layer-dir reclaim, and deep-stack repeated squash. | `cargo test -p eos-e2e-test --features e2e --test layerstack squash -- --nocapture` | `layer-squash-depth`, `layer-storage-bounded` |
| `layer-git-overlay-commit-scenario` | Groups commit-to-Git after repeated squash, overlay snapshot materialization, bounded depth reporting, path filtering, and timing phases. | `cargo test -p eos-e2e-test --features e2e --test layerstack commit_to_git_commits_overlay_snapshot_after_repeated_squash -- --nocapture` | `layer-commit-git`, `layer-squash-depth`, `layer-storage-bounded` |

### 7.6 `plugin`

Checklist:

| ID | Requirement |
|---|---|
| `plugin-package-ensure` | Warm/cold ensure publishes package and setup roots by digest. |
| `plugin-setup-idempotent` | Re-ensure skips setup when package and setup digests match. |
| `plugin-dispatch-roundtrip` | Dynamic daemon PPC dispatch preserves operation name, request body, success envelope, package root, and dependency root. |
| `plugin-service-hosted` | Daemon-hosted plugin services run as real processes and are visible in status. |
| `plugin-service-cleanup` | Reload/stop removes routes, PPC clients, service snapshots, sockets, uploads, and marker processes. |
| `plugin-refresh-remount` | Read-only service sees latest workspace after LayerStack update. |
| `plugin-refresh-singleflight` | Concurrent refreshes see new content and keep refresh counts bounded. |
| `plugin-restart-policy` | Restart strategy restarts process instead of remounting. |
| `plugin-isolated-gate` | Plugin operations are rejected while the caller is in isolated mode. |
| `plugin-lsp-lifecycle` | LSP package setup, dependency roots, route connection, and symbol dispatch remain live through the generic plugin lifecycle. |
| `plugin-write-allowed` | Live write-allowed/oneshot overlay plugin paths publish only through daemon-owned OCC paths. |

Planned test cases:

| Test name | Description | Command | Checklist |
|---|---|---|---|
| `plugin-package-dispatch-scenario` | Groups warm/cold package ensure, setup-root publication, idempotent re-ensure, dependency/scratch setup artifacts, and generic dispatch roundtrip. | `cargo test -p eos-e2e-test --features e2e --test plugin packages -- --nocapture` | `plugin-package-ensure`, `plugin-setup-idempotent`, `plugin-dispatch-roundtrip` |
| `plugin-service-lifecycle-cleanup-scenario` | Groups live service health/status, planned package reload cleanup of routes/PPC clients/sockets/uploads/markers/processes, and daemon-hosted worker visibility. | `cargo test -p eos-e2e-test --features e2e --test plugin service_health_probe_reports_connected_service -- --nocapture` | `plugin-service-hosted`, `plugin-service-cleanup` |
| `plugin-refresh-restart-scenario` | Groups workspace refresh remount, planned concurrent singleflight refresh, bounded refresh counts, and restart-service strategy behavior. | `cargo test -p eos-e2e-test --features e2e --test plugin restart_service_strategy_restarts_on_workspace_edit -- --nocapture` | `plugin-refresh-remount`, `plugin-refresh-singleflight`, `plugin-restart-policy` |
| `plugin-isolated-lsp-write-scenario` | Groups isolated-mode rejection, LSP package lifecycle/symbol query, and planned write-allowed plugin OCC publish path. | `cargo test -p eos-e2e-test --features e2e --test plugin -- --nocapture` | `plugin-isolated-gate`, `plugin-lsp-lifecycle`, `plugin-write-allowed` |

### 7.7 `daemon`

Checklist:

| ID | Requirement |
|---|---|
| `daemon-ready-identity` | Runtime readiness exposes daemon identity, probes, and timings. |
| `daemon-op-registry` | Built-in daemon ops are registered and reject unknown ops cleanly. |
| `daemon-inflight` | Background invocations are counted, heartbeated, and cancellable. |
| `daemon-command-control` | Command-session control ops remain coherent under live sessions. |
| `daemon-audit` | Audit pull, pagination, floor behavior, and reset/test hooks are explicitly tested. |
| `daemon-ttl-reaper` | Short TTL/reaper config cleans stale inflight state in a named TTL variant. |
| `daemon-plugin-control` | Background plugin/PPC operations participate in inflight/cancel/heartbeat control where supported. |

Planned test cases:

| Test name | Description | Command | Checklist |
|---|---|---|---|
| `daemon-ready-registry-timing-scenario` | Groups runtime identity, dispatch timings on success/error, built-in op registry routing, and unknown-op rejection. | `cargo test -p eos-e2e-test --features e2e --test daemon runtime_identity -- --nocapture` | `daemon-ready-identity`, `daemon-op-registry` |
| `daemon-inflight-heartbeat-cancel-scenario` | Groups concurrent background invocation visibility, heartbeat touch semantics, live cancel, unknown cancel, command-session control, and cleanup fields. | `cargo test -p eos-e2e-test --features e2e --test daemon control -- --nocapture` | `daemon-inflight`, `daemon-command-control` |
| `daemon-audit-reset-scenario` | Planned scenario for audit pull pagination, baselines, floor behavior, and gated isolated-workspace test reset without transient global-state assumptions. | `cargo test -p eos-e2e-test --features e2e --test daemon audit_pull_paginates_and_baselines -- --nocapture` | `daemon-audit` |
| `daemon-ttl-and-plugin-control-scenario` | Planned scenario for short-TTL inflight reaper and background plugin/PPC operation control through inflight, heartbeat, and cancel surfaces. | `cargo test -p eos-e2e-test --features e2e --test daemon inflight_ttl_reaper_cleanup -- --nocapture` | `daemon-ttl-reaper`, `daemon-plugin-control` |

### 7.8 `pressure`

Checklist:

| ID | Requirement |
|---|---|
| `pressure-ladder-file` | Direct file ops pass at concurrency `1/3/6/12`. |
| `pressure-ladder-exec` | Ephemeral exec passes at concurrency `1/3/6/12` and releases leases. |
| `pressure-ladder-command` | Command sessions start/cancel at `1/3/6/12` and drain session/lease counts. |
| `pressure-ladder-occ` | OCC disjoint writes and same-path conflict pressure return coherent payloads at `1/3/6/12`. |
| `pressure-ladder-plugin` | Plugin refresh/dispatch pressure remains coherent at configured levels. |
| `pressure-isolated-cap` | Isolated handle pressure either runs under a high-cap config or asserts cap rejection for levels beyond default. |
| `pressure-squash-bound` | Repeated overwrite pressure keeps manifest depth under the operational auto-squash target while preserving latest content. |
| `pressure-recovery-cleanup` | Midflight cancel and cancel bursts leave daemon readiness intact and drain command sessions, active leases, and marker work. |
| `pressure-resource-report` | Resource-report runs emit JSON summaries for latency shape, resource counters, and leak counters. |

Planned test cases:

| Test name | Description | Command | Checklist |
|---|---|---|---|
| `pressure-file-exec-ladder-scenario` | Groups mixed concurrent ops, explicit planned direct file and ephemeral exec ladders at `1/3/6/12`, publish/readback correctness, and lease cleanup. | `cargo test -p eos-e2e-test --features e2e --test pressure concurrency -- --nocapture` | `pressure-ladder-file`, `pressure-ladder-exec` |
| `pressure-command-recovery-scenario` | Groups command-session start/cancel/drain ladder, cancel bursts, midflight cancel recovery, daemon readiness, session count zero, and active lease drain. | `cargo test -p eos-e2e-test --features e2e --test pressure failure_recovery -- --nocapture` | `pressure-ladder-command`, `pressure-recovery-cleanup` |
| `pressure-occ-squash-scenario` | Groups OCC disjoint publish pressure, planned same-path conflict ladder, repeated write storm, LayerStack auto-squash bounded depth, and latest-content correctness. | `cargo test -p eos-e2e-test --features e2e --test pressure cross_subsystem -- --nocapture` | `pressure-ladder-occ`, `pressure-squash-bound` |
| `pressure-plugin-isolated-resource-scenario` | Planned scenario for plugin refresh/dispatch ladder, isolated handle high-cap or cap-rejection matrix, and JSON latency/resource/leak report emission. | `cargo test -p eos-e2e-test --features e2e --test pressure resource_report_smoke -- --nocapture` | `pressure-ladder-plugin`, `pressure-isolated-cap`, `pressure-resource-report` |

---

## 8. Adversarial Review Requirements

Before implementation starts:

1. Re-read the live Rust sources, not only `docs/architecture`, for plugin and
   daemon paths. Some architecture plugin paths can lag; prefer
   `sandbox/crates/eos-daemon/src/services/plugins/*`.
2. Use `eos-command-session` and `eos-runner` as the command-session truth. Do
   not plan around an `eos-terminal-pair` crate if it is absent in the checkout.
3. Decide the `setsid nohup` contract before writing that test. The test must
   encode the intended behavior, not accidentally document a leak.
4. Do not assert live OCC batching unless the path under test submits a
   non-atomic changeset. Current disjoint write tests prove correctness, not
   queue batching.
5. Treat `orphan_layer_count == 0` and `missing_layer_count == 0` as
   supplemental until backed by real enumeration.
6. Keep write scopes disjoint if multiple agents implement this spec in
   parallel: README files, harness config, OCC/LayerStack, workspace/session,
   plugin/daemon, and pressure can be separate workstreams.

---

## 9. Verification Ladder

Baseline non-live check:

```bash
cd /Users/yifanxu/machine_learning/LoVC/EphemeralOS/sandbox
cargo test -p eos-e2e-test -- --list
cargo check -p eos-e2e-test --all-targets
```

Live build prerequisite:

```bash
cd /Users/yifanxu/machine_learning/LoVC/EphemeralOS/sandbox
cargo run -p xtask -- package --target x86_64-unknown-linux-musl --out-dir dist
```

Focused live modules:

```bash
cd /Users/yifanxu/machine_learning/LoVC/EphemeralOS/sandbox
cargo test -p eos-e2e-test --features e2e --test occ -- --nocapture
cargo test -p eos-e2e-test --features e2e --test ephemeral_workspace -- --nocapture
cargo test -p eos-e2e-test --features e2e --test isolated_workspace -- --nocapture
cargo test -p eos-e2e-test --features e2e --test layerstack -- --nocapture
cargo test -p eos-e2e-test --features e2e --test daemon -- --nocapture
cargo test -p eos-e2e-test --features e2e --test plugin -- --nocapture
cargo test -p eos-e2e-test --features e2e --test pressure -- --nocapture
```

Supporting crate checks for lower-level changes:

```bash
cd /Users/yifanxu/machine_learning/LoVC/EphemeralOS/sandbox
cargo test -p eos-occ --all-targets
cargo test -p eos-layerstack --all-targets
cargo test -p eos-overlay --all-targets
cargo test -p eos-ephemeral-workspace --all-targets
cargo test -p eos-command-session --all-targets
cargo check -p eos-daemon --all-targets
```

---

## 10. Acceptance Criteria

1. All 8 module `readme.md` files exist and use the required structure.
2. Every README checklist item is covered by at least one test-case row.
3. README test rows are at most five per module, cover every checklist item, and
   group live Rust functions or planned additions into load-bearing scenarios.
4. Default live module runs pass under Docker with the default dask image.
5. The suite has typed module-local workload contracts; there are no
   generic cost-class variant files or selector environment variables, and
   any named variant is selected as the one explicit `*.test.yml` for that run.
6. `1/3/6/12` concurrency comparisons exist for direct file ops, ephemeral exec,
   command sessions, OCC pressure, and plugin refresh where valid.
7. Isolated workspace pressure either uses a high-cap config or asserts cap
   rejection above the configured cap.
8. Resource-critical tests assert both correctness and cleanup: no leaked
   command sessions, active leases return to zero, overlay scratch is removed,
   plugin service processes are reaped, and storage growth stays bounded.
9. Performance-critical tests emit JSON artifacts before any strict regression
   threshold is added.
10. The suite remains Docker-only and protocol-first.
