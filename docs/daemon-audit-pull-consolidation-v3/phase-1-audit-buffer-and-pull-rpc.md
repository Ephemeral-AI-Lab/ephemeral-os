# Phase 1 — Audit Buffer, Pull RPC, Schema Contract

> **Prerequisites:** Read [`README.md`](README.md) first — it owns the
> RALPLAN-DR summary, the V2 review (Part 1), and all cross-cutting contracts
> (schema, dual-write, pressure formula, lane assignment, sampling rule,
> cadence policy, disk contract, RPC trust model, daemon-restart epoch handling).
> This phase file is the deliverable spec; the index is the source of truth
> for everything cross-cutting.

## Goal

Bounded daemon-side ring + pull/snapshot RPCs + frozen schema (v1) covering all subsystem section keys including `plugin`, `background_tool`, and `tool_call`. **No emitters wired beyond a minimal smoke set.**

This phase is purely additive — it must be revertable by deleting two new files and one block from `dispatcher.py`. No existing code paths change semantics.

## Deliverables

### 1. New file — `backend/src/sandbox/daemon/audit_buffer.py`

- Monotonic `seq` across all lanes.
- `boot_epoch_id` assigned at construction (monotonic-clock value).
- `max_events` (default 50,000), `max_bytes` (default 8 MiB).
- Priority lanes: `critical` / `normal` / `sample`. Eviction order: sample → normal → critical.
- Pressure formula: `max(retained_bytes/max_bytes, retained_events/max_events)` (see [README §Buffer pressure formula](README.md#buffer-pressure-formula-and-tracked-counters)).
- Tracked counters (all reported in every pull/snapshot response):
  - `retained_events`, `retained_bytes`
  - `dropped_event_count`, `dropped_event_count_by_lane` (`{critical, normal, sample}`)
  - `lost_before_seq`
  - `pressure` (derived)
- Critical-lane events survive sample-lane eviction (proven in tests).
- Public methods: `append(event, lane)`, `pull(after_seq, limit)`, `snapshot()`.

### 2. RPC ops registered in `backend/src/sandbox/daemon/rpc/dispatcher.py` (via `register_op`)

- `api.audit.pull` — returns `cursor` + `buffer` + `snapshot` + `events`; O(returned events).
- `api.audit.snapshot` — returns cached gauges only; O(1); must NOT walk large trees.
- `api.audit.reset_floor` — operator-only; gated by `EOS_DAEMON_AUDIT_ALLOW_FLOOR_RESET=true` environment check.

### 3. Transport wrappers

- Wrappers in `backend/src/sandbox/api/daemon_audit.py` (new file).
- Transport constants in `backend/src/sandbox/api/transport.py`.
- Exports in `backend/src/sandbox/api/__init__.py`.

### 4. Frozen schema v1

- `audit_buffer.SCHEMA_VERSION = "sandbox.daemon.audit.pull.v1"` constant.
- Sibling `audit_schema.py` with subsystem section dataclasses (for typed construction in emitters).
- Schema doc inline at top of `audit_buffer.py` enumerating all sections, all event families, and lane assignments (single source of truth).

### 5. Smoke emitters

Proves the wiring works without committing to full instrumentation.

- `daemon.started` on daemon boot.
- `daemon.audit_buffer_pressure` whenever pressure crosses 0.8 threshold.
- `os_resource.sampled` on the existing sampler tick.

## State / event / resource tables (Phase 1 schema commitment)

### Overlay workspace — ephemeral vs isolated side-by-side

| Property | Ephemeral | Isolated |
|---|---|---|
| Lifecycle states | `mount → exec → capture → publish → unmount` (per tool call) | `enter → active → (tool…tool…sample…) → exit` (per agent/task) |
| Dominant event family | `overlay_workspace.{mounted,published,cleaned}` | `isolated_workspace.{entered,sampled,exited,orphan_check_completed}` |
| Upperdir fate | always discarded at `unmount` | retained until `exit`; optionally promoted via OCC commit |
| Holder PID | none (daemon-internal) | exactly one external holder PID + cgroup |
| Sampling cadence | lifecycle boundaries only | lifecycle boundaries + 500 ms steady-state |
| Memory profile | spike during exec, free at unmount | resident; `memory_current_bytes` + `memory_peak_bytes` tracked across lifetime |
| Disk profile | bounded by single tool's write | bounded by `EOS_ISOLATED_WORKSPACE_UPPERDIR_MAX_BYTES`; warn at 80 % |
| CPU profile | sum of `run_ms` per call | continuous `cpu_usage_usec_delta` per sample window |
| Cleanup signal | `scratch_removed=true` on every event | `scratch_removed`, `cgroup_removed`, `holder_pid_alive=false` on `exited` |
| Failure signal | non-zero `cleanup_ms` only | non-zero `orphan_holder_count / orphan_cgroup_count / orphan_scratch_count` |
| Lane (events) | critical (mount/published/cleaned) | critical (entered/exited/orphan_*) + sample (sampled) |
| Default workspace_mode | `default` (when overlay disabled) → falls back to non-overlay path | n/a (always overlay) |

### LayerStack — lease/lock/squash family

```
layer_stack.lease_requested   (operation_step=20, lease_id, manifest_version)
layer_stack.lease_acquired    (operation_step=20, lease_wait_ms)
layer_stack.lock_acquired     (operation_step=30, lock_wait_ms)
layer_stack.snapshot_prepared (operation_step=40, prepare_snapshot_ms, layer_count)
layer_stack.squash_triggered  (squash_trigger_reason, squash_input_layers)
layer_stack.squash_completed  (squash_result_layers, manifest_root_hash)
layer_stack.squash_failed     (squash_failure_kind, manifest_root_hash)        [critical]
layer_stack.lease_released    (operation_step=130, lease_hold_ms)
```

Every event carries `lease_id` for per-lease timeline reconstruction. `manifest_root_hash` lets a stale-base OCC rejection cite the exact manifest version it was rejected against.

### OCC — changeset transaction family

```
occ.changeset_prepared        (operation_step=70, changeset_id, changed_path_count)
occ.transaction_lock_acquired (operation_step=90, transaction_lock_wait_ms)
occ.apply_committed           (operation_step=110, apply_ms, commit_ms, committed_layer_id)
occ.publish_layer             (publish_layer_ms, committed_layer_bytes)
occ.conflict_rejected         (conflict_kind, conflict_path, conflict_reason,    [critical]
                               base_manifest_version, current_manifest_version)
```

Conflict events carry both `base_manifest_version` (writer's view) and `current_manifest_version` (daemon's view), matching the [[project_ephemeralos_layerstack_occ_design]] stale-base story.

### Background tool calls — generic, plugin-agnostic

```
background_tool.started      (background_task_id, task_kind, tool_name, agent_id)     [normal]
background_tool.heartbeat    (background_task_id, uptime_ms, status=RUNNING)          [sample]
background_tool.completed    (background_task_id, exit_code, duration_ms)             [normal]
background_tool.failed       (background_task_id, error_kind, duration_ms)            [normal]
background_tool.cancelled    (background_task_id, cancel_reason, duration_ms)         [normal]
background_tool.delivered    (background_task_id, delivery_latency_ms)                [normal]
```

Mirrors the existing `BackgroundTaskStatus` lattice in `backend/src/engine/background/task_supervisor.py` (`RUNNING → {COMPLETED, FAILED, CANCELLED} → DELIVERED`). Heartbeats reuse the existing `EOS_BACKGROUND_HEARTBEAT_INTERVAL_S` (default 60 s) — **zero new timer threads**. Background tool emission cost is therefore bounded by the existing heartbeat, not added on top.

### Plugin — generic, not LSP-specific

```
plugin.tool_invoked           (plugin_id, plugin_kind, plugin_version, plugin_tool_name,
                               request_bytes, workspace_handle_id, agent_id)            [normal]
plugin.tool_completed         (plugin_id, plugin_tool_name, duration_ms, response_bytes,
                               status)                                                  [normal]
plugin.error                  (plugin_id, plugin_kind, error_kind, message_hash)        [normal]
plugin.peak_resident_sampled  (plugin_id, peak_resident_bytes)                          [sample]
```

`plugin_kind` values: `language_server`, `formatter`, `indexer`, `build_daemon`, `mcp_bridge`, `custom`. The current LSP plugin (`backend/src/plugins/catalog/lsp/`) is *one instance* of `plugin_kind = "language_server"`. **No field name contains `lsp`, `pyright`, or `language`** — those are values, not keys. A future Ruff long-running daemon or `tsc --watch` plugin emits the same event family unchanged.

**Note on `plugin.session_*`:** V2 proposed `plugin.session_started/stopped`. V3 drops these from v1 because the current loader (`backend/src/plugins/core/loader.py`) is an import-time singleton with no native per-invocation lifecycle. When a real plugin session model is introduced (separate follow-up plan), `plugin.session_*` can be added additively without a schema bump.

### Per-tool timing — every foreground tool

```
tool_call.started   (tool_id, tool_name, agent_id, workspace_mode,                    [normal]
                     workspace_handle_id)
tool_call.phase     (phase ∈ {queued, mount, exec, capture, publish, release},        [sample, per-tool sampling]
                     duration_ms)
tool_call.finished  (total_ms, exit_status, bytes_in, bytes_out,                      [normal, always emitted]
                     phase_totals_rollup={queued_ms,mount_ms,exec_ms,
                                          capture_ms,publish_ms,release_ms})
```

`tool_call.finished.phase_totals_rollup` is computed from in-process timers and is **always populated**, even when `tool_call.phase` events are sampled out. Per-tool aggregate p50/p95/p99 in the report (§2) is accurate without depending on phase event emission.

`workspace_mode` lets the report split the same `tool_name` between `default` / `ephemeral` / `isolated` cohorts (answers "is `edit_file` slower in isolated mode?").

## Resource & overhead budget for Phase 1 itself

The pull/heartbeat/ring path is intentionally cheap. Budgets (verified by Phase 3 release gates):

| Component | Memory ceiling | CPU ceiling | Disk (sandbox) | Notes |
|---|---:|---:|---:|---|
| Daemon ring | 8 MiB (`max_bytes`) | < 0.1 % avg, < 1 % p99 | 0 (never spills) | hard-capped by both `max_bytes` and `max_events` |
| `api.audit.pull` (1 s cadence) | < 1 MiB transient per call | ~2 ms CPU per call at 1000 events | 0 | O(returned events), not O(retained) |
| `api.audit.snapshot` | 0 | < 0.5 ms | 0 | reads cached gauges only; never walks trees |
| Heartbeat (background tool) | reuses existing 60 s timer | unchanged | 0 | **zero new threads** |
| Upperdir disk samples | 0 | bounded by sample budget; emits `sample_budget_exhausted` | reads only — never writes | TTL-cached |

## Tests

- `test_audit_buffer_ordering` — `seq` is strictly monotonic across all lanes.
- `test_audit_buffer_eviction_events_and_bytes` — both caps independently enforced.
- `test_audit_buffer_critical_lane_survives_sample_pressure` — flood sample lane to 200 % capacity; assert all critical events retained, lane drop counter accurate.
- `test_audit_buffer_pressure_formula` — assert `max(bytes_ratio, events_ratio)` for boundary cases.
- `test_pull_cursor_exclusive_and_drops_reported` — pull with `after_seq=N` returns events with `seq > N`; `dropped_event_count` and `lost_before_seq` non-zero after forced eviction.
- `test_snapshot_is_o1_under_load` — generate 1 M synthetic events; assert snapshot latency p99 < 1 ms.
- `test_schema_version_constant_matches_pull_response` — `audit_buffer.SCHEMA_VERSION` matches the `schema` field in every pull response.
- `test_audit_reset_floor_op_gated_by_env` — call without `EOS_DAEMON_AUDIT_ALLOW_FLOOR_RESET=true` → rejected; with it → accepted.

## Acceptance criteria

- All tests above pass under `.venv/bin/pytest` (per memory: `feedback_use_venv_pytest`).
- `make test` green for ring/RPC unit tests; `.venv/bin/ruff check` clean.
- `pressure < 0.05` on idle daemon with smoke emitters only.
- **Causal-chain smoke** (closes Architect+Critic feedback that Phase 1 acceptance was self-referential): a synthetic harness exercises one fake foreground tool call AND one fake isolated-workspace lifecycle through the ring + pull RPC. Specifically:
  1. Emit `isolated_workspace.entered` (critical, shared `operation_id=op-smoke-1`, `handle_id=iws-smoke`)
  2. Emit `tool_call.started` (normal, same `operation_id`, `tool_name=smoke_tool`, `workspace_handle_id=iws-smoke`)
  3. Emit `overlay_workspace.mounted` (critical, same `operation_id`)
  4. Emit `tool_call.phase` for each of `mount, exec, capture, publish, release` (sample, same `operation_id`, `tool_id` set)
  5. Emit `overlay_workspace.published` (critical, same `operation_id`)
  6. Emit `tool_call.finished` (normal, same `operation_id`, `phase_totals_rollup` populated)
  7. Emit `isolated_workspace.exited` (critical, same `operation_id`, `orphan_holder_count=0`, `holder_pid_alive=false`)
  8. Pull from `seq=0` and assert:
     - All 11 events present with strictly monotonic `seq`
     - All carry the same `operation_id`
     - The four critical events appear regardless of subsequent sample-lane pressure (re-run with a synthetic flood of 100,000 throwaway sample-lane events between steps 5–6; critical events still present at end)
     - `snapshot.daemon.boot_epoch_id` is stable across both pulls
- Test name: `test_phase_1_causal_chain_smoke`.

## What this phase does NOT do

- Does NOT instrument any production code path beyond the smoke emitters. Real emitters (layer_stack, overlay, occ, isolated_workspace, plugin, background_tool, tool_call.phase) land in [Phase 2](phase-2-emitters-and-puller.md).
- Does NOT start a runner-side puller. The puller is Phase 2.
- Does NOT touch `sandbox_events.jsonl`, the normalizer, or the performance report. Those are Phases 2 and 3.
