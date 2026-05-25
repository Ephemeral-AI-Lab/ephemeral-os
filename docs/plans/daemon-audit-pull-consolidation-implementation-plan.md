# Daemon Audit Pull Consolidation Implementation Plan

## Status

Draft plan for implementing pull-only sandbox audit consolidation across the
daemon, task_center_runner audit recorder, and performance report pipeline.

## Problem

The current task_center_runner audit path records useful sandbox evidence, but
most sandbox events are derived from tool-completion metadata after the fact.
The daemon does not yet expose a unified pull surface for live audit state,
resource samples, isolated-workspace health, buffer pressure, or orphan
cleanup evidence.

The implementation must consolidate audit and performance telemetry for:

- `layer_stack`: events, leases, locks, layer count, squash trigger/result,
  CPU, memory, disk usage, and timing.
- `occ`: event logs, file changes, conflict/change-set behavior, and timing.
- `overlay_workspace`: CPU, memory, disk usage, timing, lifecycle, isolated or
  ephemeral workspace mode, and upperdir size.
- `isolated_workspace`: close monitoring of disk, memory, space usage,
  upperdir changes, lifecycle, exit cleanup, and orphan-holder checks.
- `os_resource`: daemon/container/process cgroup CPU, memory, IO, and disk
  gauges or deltas.

## Current Anchors

- `backend/src/task_center_runner/core/engine.py`
  - Owns `run_pipeline`, starts `AuditRecorder`, bridges agent events, captures
    `MetricsAggregator.performance_snapshot()`, and writes the performance
    report after recorder disposal.
- `backend/src/task_center_runner/audit/recorder.py`
  - Writes `run.json`, `metrics.json`, per-task `message.jsonl`, and
    `sandbox_events.jsonl`.
- `backend/src/task_center_runner/audit/stream_bridge.py`
  - Converts tool stream events into audit bus events.
- `backend/src/task_center_runner/audit/sandbox_events.py`
  - Choke point for preserving timing/resource metadata into
    `sandbox_events.jsonl`.
- `backend/src/task_center_runner/audit/performance_report.py`
  - Offline report builder over `sandbox_events.jsonl` plus in-memory tool
    performance snapshot.
- `backend/src/sandbox/daemon/rpc/dispatcher.py`
  - Registers daemon RPC operations.
- `backend/src/sandbox/daemon/builtin_operations.py`
  - Current diagnostic/control surface, including background invocation
    heartbeat. That heartbeat is for invocation TTL, not audit stats.
- `backend/src/sandbox/isolated_workspace/`
  - Isolated workspace lifecycle, resource controls, TTL, and orphan cleanup.

## Design Decisions

1. Use pull-only daemon audit collection.
2. Do not write daemon audit events to daemon-side disk by default.
3. Keep daemon audit memory bounded by both event count and byte estimate.
4. Evict old events when the ring reaches limits.
5. Retain critical isolated-workspace lifecycle and orphan events ahead of
   low-value periodic samples.
6. Keep stream-derived sandbox events as a fallback until daemon pull is fully
   deployed.
7. Persist pulled events into the existing `sandbox_events.jsonl` artifact.
8. Extend `performance_report.json` and `performance_report.md`; do not create
   a parallel reporting format.
9. Use subsystem-section keys in event payloads:
   `daemon`, `layer_stack`, `overlay_workspace`, `occ`,
   `isolated_workspace`, and `os_resource`.
10. Do not add any extra top-level discriminator field for the subsystem.

## Event Ordering Contract

Daemon events must have a global monotonic `seq`.

Pull requests use an exclusive cursor:

```json
{
  "after_seq": 1042,
  "limit": 1000,
  "include_snapshot": true
}
```

Responses return events sorted by ascending `seq`:

```json
{
  "cursor": {
    "after_seq": 1042,
    "next_after_seq": 1120,
    "lost_before_seq": 900,
    "dropped_event_count": 17
  },
  "events": []
}
```

Within one operation, emit sparse `operation_step` values:

| Step | Meaning |
| ---: | --- |
| 10 | request accepted |
| 20 | layer-stack lease requested/acquired |
| 30 | lock wait/acquired |
| 40 | workspace mounted or isolated handle entered |
| 50 | tool or workspace operation executed |
| 60 | upperdir captured or sampled |
| 70 | OCC changeset prepared |
| 90 | OCC transaction locked |
| 110 | changes committed or isolated upperdir retained/discarded |
| 130 | lease/workspace/holder released |
| 150 | request completed |

Under concurrency, `seq` is the daemon append order. Causal grouping comes from
`operation_id`, `tool_id`, `agent_id`, `handle_id`, and `operation_step`.

## Pull Output Contract

Raw daemon pull output is machine-facing. Reports enrich it for humans.

```json
{
  "schema": "sandbox.daemon.audit.pull.v1",
  "sandbox_id": "sandbox-123",
  "cursor": {
    "after_seq": 1842,
    "next_after_seq": 1901,
    "lost_before_seq": 1200,
    "dropped_event_count": 37
  },
  "buffer": {
    "max_events": 50000,
    "max_bytes": 8388608,
    "retained_events": 48712,
    "retained_bytes": 7612230,
    "pressure": 0.91
  },
  "snapshot": {
    "daemon": {
      "pid": 71,
      "uptime_ms": 391221,
      "inflight_count": 3
    },
    "isolated_workspace": {
      "open_handle_count": 1,
      "active_call_count": 0,
      "holder_pid_count": 1,
      "orphan_holder_count": 0,
      "orphan_cgroup_count": 0,
      "orphan_scratch_count": 0,
      "upperdir_bytes_total": 1048576,
      "memory_current_bytes_total": 73400320
    }
  },
  "events": [
    {
      "seq": 1897,
      "ts": "2026-05-26T10:14:12.413Z",
      "event": "isolated_workspace.sampled",
      "severity": "info",
      "operation_id": "op-abc",
      "operation_step": 60,
      "isolated_workspace": {
        "handle_id": "iws-7",
        "agent_id": "executor",
        "lifecycle_state": "active",
        "upperdir_bytes": 1048576,
        "upperdir_file_count": 9,
        "holder_pid": 2331,
        "orphan_holder_count": 0
      },
      "os_resource": {
        "cpu_usage_usec_delta": 18322,
        "memory_current_bytes": 73400320,
        "memory_peak_bytes": 94371840,
        "io_read_bytes_delta": 0,
        "io_write_bytes_delta": 262144
      }
    }
  ]
}
```

## Metrics To Log

### daemon

- `pid`, `uptime_ms`, `thread_count`, `fd_count`
- `inflight_count`, `background_invocation_count`
- `audit_buffer_retained_events`, `audit_buffer_retained_bytes`
- `audit_buffer_dropped_event_count`, `audit_buffer_lost_before_seq`
- RPC call count, error count, timeout count, p50/p95/p99 latency

### layer_stack

- `lease_id`, `lease_wait_ms`, `lease_hold_ms`
- `lock_wait_ms`, `lock_hold_ms`
- `manifest_version`, `manifest_root_hash`
- `layer_count`, `manifest_depth`, `manifest_path_count`
- `squash_trigger_reason`, `squash_input_layers`, `squash_result_layers`
- `prepare_snapshot_ms`, `release_snapshot_ms`
- `stale_staging_reaped_count`

### overlay_workspace

- `workspace_mode`: `default`, `ephemeral`, or `isolated`
- `mount_ms`, `run_ms`, `capture_upperdir_ms`, `publish_ms`, `total_ms`
- `upperdir_bytes`, `upperdir_file_count`, `upperdir_dir_count`
- `changed_path_count`, `changed_paths_sample`
- `mount_strategy`, `lowerdir_count`
- `cleanup_ms`, `scratch_removed`

### occ

- `changeset_id`, `changed_path_count`, `changed_paths_sample`
- `prepare_ms`, `apply_ms`, `commit_ms`, `publish_layer_ms`
- `conflict_kind`, `conflict_path`, `conflict_reason`
- `transaction_lock_wait_ms`, `transaction_lock_hold_ms`
- `committed_layer_id`, `committed_layer_bytes`

### isolated_workspace

- `handle_id`, `agent_id`, `lease_id`
- `lifecycle_state`: `entering`, `active`, `exiting`, `exited`, `evicted`
- `manifest_version`, `manifest_root_hash`, `lowerdir_layer_count`
- `upperdir_bytes`, `upperdir_file_count`, `upperdir_dir_count`
- `upperdir_bytes_delta`, `upperdir_changed_path_count`
- `holder_pid`, `holder_pid_alive`, `holder_exit_signal`
- `cgroup_path_hash`, `memory_current_bytes`, `memory_peak_bytes`
- `cpu_usage_usec_delta`, `cpu_throttled_usec_delta`
- `network_ns_inode`, `mount_ns_inode`
- `orphan_holder_count`, `orphan_cgroup_count`, `orphan_scratch_count`
- `upperdir_bytes_discarded`, `scratch_removed`, `cgroup_removed`

### os_resource

- CPU: `cpu_usage_usec_delta`, `cpu_user_usec_delta`,
  `cpu_system_usec_delta`, `cpu_throttled_usec_delta`
- Memory: `memory_current_bytes`, `memory_peak_bytes`, `memory_max_bytes`
- IO: `io_read_bytes_delta`, `io_write_bytes_delta`, `io_read_ops_delta`,
  `io_write_ops_delta`
- Disk: sampled `upperdir_bytes`, `run_dir_bytes`, `scratch_bytes`
- Process: `rss_bytes`, `max_rss_bytes`, `child_user_cpu_s`,
  `child_system_cpu_s`

## Implementation Phases

### Phase 1: Daemon Audit Ring

Add `backend/src/sandbox/daemon/audit_buffer.py`.

Responsibilities:

- Assign monotonic `seq`.
- Store normalized event dictionaries.
- Enforce `max_events`.
- Enforce `max_bytes` using a conservative encoded-size estimate.
- Track `dropped_event_count` and `lost_before_seq`.
- Support priority lanes:
  - critical: isolated workspace exit, orphan checks, orphan reaps, cleanup
    failures.
  - normal: lifecycle, lock, lease, OCC, overlay operations.
  - sample: periodic CPU/memory/disk samples.
- Return deterministic ordered slices for `pull(after_seq, limit)`.

Default limits:

- `EOS_DAEMON_AUDIT_MAX_EVENTS=50000`
- `EOS_DAEMON_AUDIT_MAX_BYTES=8388608`
- `EOS_DAEMON_AUDIT_PULL_LIMIT=1000`

### Phase 2: Daemon Pull RPC

Add daemon operations:

- `api.audit.pull`
- `api.audit.snapshot`
- optional test-only `api.audit.reset` gated by test harness env.

Touch points:

- Register in `backend/src/sandbox/daemon/rpc/dispatcher.py`.
- Add wrappers in `backend/src/sandbox/api/daemon_audit.py`.
- Add transport constants in `backend/src/sandbox/api/transport.py`.
- Export wrappers from `backend/src/sandbox/api/__init__.py`.

`api.audit.snapshot` should be cheap and bounded. It may include cached disk
sizes. It must not walk large trees on every call.

### Phase 3: Daemon Emitters

Instrument existing daemon paths to emit into the ring.

Initial emitters:

- Layer-stack lease, lock, layer count, squash trigger/result.
- Overlay workspace mount/run/capture/publish/cleanup.
- OCC prepare/apply/commit/conflict.
- Resource snapshots from existing command execution resource metrics.
- Isolated workspace enter/tool/exit/TTL/GC/orphan checks.

Disk sampling policy:

- Sample upperdir/scratch sizes at lifecycle boundaries and at a bounded
  interval while active.
- Do not recursively walk full workspaces on every pull.
- Cache expensive size calculations with timestamp and truncation markers.
- Emit `*_truncated` or `sample_budget_exhausted` when a sample stops early.

### Phase 4: Runner Puller

Add `backend/src/task_center_runner/audit/daemon_pull.py`.

`DaemonAuditPuller` responsibilities:

- Maintain cursor state in memory.
- Poll `api.audit.pull`.
- Publish normalized events onto `AuditEventBus`.
- Track pull stats: call count, empty count, event count, errors,
  dropped count, max pressure, final cursor.
- Final-drain on stop.
- Never block the main run on transient pull failures.

Suggested interval policy:

- active run: 1 second
- idle: 5 seconds
- isolated workspace active: 500 milliseconds
- buffer pressure >= 0.8: 250 milliseconds
- final drain: pull until empty or bounded by a small max drain duration

Integrate in `backend/src/task_center_runner/core/engine.py`:

1. Start after `recorder.start()`.
2. Bind to `lease.sandbox_id`.
3. Stop and final-drain before `recorder.dispose()`.
4. Capture puller stats before `performance_snapshot`.
5. Include puller stats in the performance report input.

### Phase 5: Audit Event Normalization

Add a normalizer near `task_center_runner/audit/sandbox_events.py` or in the
new puller module.

Rules:

- Preserve the raw daemon event under `payload["daemon_event"]`.
- Map known events to existing `EventType` values where possible.
- Add new `EventType` values only where existing types cannot represent the
  meaning:
  - `SANDBOX_DAEMON_AUDIT_BUFFER`
  - `SANDBOX_ISOLATED_WORKSPACE_SAMPLE`
  - `SANDBOX_ISOLATED_WORKSPACE_ORPHAN_CHECK`
- Keep existing stream-derived events as fallback.
- Dedupe daemon-pulled and stream-derived events using:
  1. `seq` when present.
  2. `(operation_id, event, operation_step, tool_id)`.

### Phase 6: Recorder Persistence

Keep `sandbox_events.jsonl` as the canonical artifact.

Each row should preserve the existing shape:

```json
{
  "ts": "2026-05-26T10:14:12.413Z",
  "event_type": "sandbox_isolated_workspace_sample",
  "node": {
    "task_center_run_id": "tcr-123",
    "agent_name": "executor",
    "agent_run_id": "ar-123",
    "tool_name": "shell"
  },
  "payload": {
    "daemon_seq": 1897,
    "daemon_event": {},
    "isolated_workspace": {},
    "os_resource": {}
  },
  "correlation_id": "op-abc"
}
```

This keeps old readers working while allowing new report sections.

### Phase 7: Performance Report Consolidation

Extend `backend/src/task_center_runner/audit/performance_report.py`.

Add `sandbox.sections`:

```json
{
  "sandbox": {
    "sections": {
      "daemon": {},
      "layer_stack": {},
      "overlay_workspace": {},
      "occ": {},
      "isolated_workspace": {},
      "os_resource": {}
    }
  }
}
```

Add `sandbox.daemon_audit_pull`:

- `pull_count`
- `empty_pull_count`
- `events_pulled`
- `pull_error_count`
- `dropped_event_count`
- `lost_before_seq`
- `max_buffer_pressure`
- `final_cursor`

Add observations and warnings:

- Audit events were dropped.
- Pull buffer pressure exceeded 80%.
- Isolated workspace exited with orphan holders.
- Isolated workspace exited with orphan cgroups or scratch.
- Upperdir growth exceeded threshold.
- Memory peak exceeded threshold.
- OCC conflict cluster detected.
- Layer-stack lock wait p95 exceeded threshold.
- Squash failed or did not reduce layer count.

### Phase 8: Isolated Workspace Close Monitoring

This is the highest-risk path and must be treated as a release gate.

Required event family:

- `isolated_workspace.entered`
- `isolated_workspace.tool_executed`
- `isolated_workspace.sampled`
- `isolated_workspace.exited`
- `isolated_workspace.evicted`
- `isolated_workspace.orphan_check_completed`
- `isolated_workspace.orphan_reaped`

Exit contract:

1. `exit_workspace` starts.
2. Active tool calls are stopped or rejected.
3. Holder process tree is terminated.
4. Scratch/upperdir is removed or recorded as retained only if policy says so.
5. Cgroup is removed.
6. Lease is released.
7. Immediate orphan check runs.
8. Pull output reports zero orphan holders, cgroups, and scratch paths.

Report failure conditions:

- `orphan_holder_count_after > 0`
- `orphan_cgroup_count_after > 0`
- `orphan_scratch_count_after > 0`
- `open_handle_count > 0` at run completion
- holder PID alive after exit
- upperdir not discarded after non-promoted isolated workspace exit

## Configuration

Add a small config object to `RunConfig.extras` first to avoid broad config
churn:

```python
extras={
    "daemon_audit_pull": {
        "enabled": True,
        "interval_s": 1.0,
        "idle_interval_s": 5.0,
        "pressure_interval_s": 0.25,
        "isolated_workspace_interval_s": 0.5,
        "limit": 1000,
        "include_snapshot": True,
        "final_drain_timeout_s": 3.0
    }
}
```

After behavior stabilizes, promote it into first-class runner config only if
multiple callers need to tune it.

## Test Plan

### Unit Tests

Add or extend tests under:

- `backend/tests/unit_test/test_sandbox/test_daemon/`
- `backend/tests/unit_test/test_audit/`
- `backend/tests/unit_test/test_task_center/test_audit/`

Coverage:

- Audit ring preserves order by `seq`.
- Pull cursor is exclusive.
- Max event eviction works.
- Max byte eviction works.
- Dropped counters and `lost_before_seq` are correct.
- Critical isolated-workspace events survive sample pressure.
- Puller final-drains before recorder disposal.
- Recorder writes daemon-pulled events to `sandbox_events.jsonl`.
- Performance report groups section-keyed events.

### Focused Isolated Workspace Tests

Run:

```bash
uv run pytest \
  backend/src/task_center_runner/tests/mock/sandbox/isolated_workspace/pre_flight/ \
  backend/tests/unit_test/test_sandbox/test_daemon/ \
  backend/tests/unit_test/test_audit/ \
  backend/tests/unit_test/test_task_center/test_audit/ \
  -q
```

Then:

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

### Report Regression Tests

Use existing sandbox report scenarios:

```bash
uv run pytest -q -x --tb=short --durations=20 \
  backend/src/task_center_runner/tests/mock/sandbox/layer_stack_occ_overlay/
```

Expected artifacts:

- `run.json`
- `metrics.json`
- `message.jsonl`
- `sandbox_events.jsonl`
- `performance_report.json`
- `performance_report.md`

### Live Health Checks

During long runs, fail fast on:

- `run.json` stops advancing while tasks remain active.
- `sandbox_events.jsonl` stops advancing during sandbox activity.
- `performance_report.json` is missing after completion.
- Puller reports dropped events.
- Puller reports high buffer pressure.
- Isolated workspace exit has nonzero orphan counts.

## Performance Guardrails

- Pull RPC must be O(number of returned events), not O(total retained events).
- `api.audit.snapshot` must avoid full tree walks.
- Disk size scans must be cached, bounded, and marked truncated when needed.
- The puller must never block tool execution.
- The daemon ring must have fixed memory ceilings.
- Sample frequency must increase under pressure but remain bounded.
- Final drain must be bounded so teardown cannot hang indefinitely.

## Rollout Plan

1. Land daemon audit ring with tests, no emitters.
2. Land `api.audit.pull` and `api.audit.snapshot` with tests.
3. Add runner puller behind `daemon_audit_pull.enabled`.
4. Persist pulled events to `sandbox_events.jsonl`.
5. Extend performance report with `daemon_audit_pull` and `sandbox.sections`.
6. Add layer_stack, overlay_workspace, OCC, and os_resource emitters.
7. Add isolated_workspace emitters and orphan-check report failures.
8. Enable by default for sandbox-backed task_center_runner runs.
9. Run focused live isolated workspace smoke.
10. Run layer_stack/OCC/overlay report regression.

## Open Questions

1. Should puller stats be injected into `MetricsAggregator.performance_snapshot()`
   or passed directly to `write_performance_reports` as a second input?
   Recommendation: pass directly to report generation to keep tool metrics
   separate from audit transport metrics.
2. Should critical events have a separate reserved buffer?
   Recommendation: start with priority eviction in one ring; add a reserved
   critical lane only if tests show sample pressure can still evict lifecycle
   proof.
3. Should daemon pull expose full path lists?
   Recommendation: use counts plus bounded samples by default. Full path lists
   should be opt-in for debug pulls because they can be large.
