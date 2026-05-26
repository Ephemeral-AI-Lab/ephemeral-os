# Phase 2 — Slice 1 (Foundation) Implementation Report

**Date:** 2026-05-26
**Scope:** Foundation slice of the Phase 2 plan
(`phase-2-emitters-and-puller.md`) — the runner-side puller, the daemon-event
normalizer with `daemon_event` boundary lint, the rotating + gzipping
JSONL sink with `_iter_jsonl` rotation-aware reader, and the
`layer_stack` subsystem emitters as the proof end-to-end. Other subsystem
emitters and the dispatcher slow-tail flush are deferred (see below) so
each can ship as its own reviewable PR per the plan's "one PR per
subsystem" guidance.

## Why a slice, not the full phase

`phase-2-emitters-and-puller.md` itself prescribes "emitters land one PR
per subsystem to keep the review surface small." A single push that
combined the puller, 5 subsystem emitters, plugin shim, background-tool
instrumentation, dispatcher slow-tail flush, normalizer and rotation/gzip
would have produced ~20 tests across ~15 files. Slice 1 lands the
infrastructure end-to-end with one proof subsystem (`layer_stack`) so
each subsequent slice can drop a single subsystem in without further
plumbing.

## What landed

### New files

| File | Purpose |
|---|---|
| `backend/src/task_center_runner/audit/daemon_pull.py` | `DaemonAuditPuller` — adaptive cadence + floor enforcement + epoch handling + final drain. |
| `backend/src/task_center_runner/audit/daemon_event_normalizer.py` | Sole writer of `payload["daemon_event"]` (env-gated forensic raw); section promotion; dedupe key + `merge_streams()`. |
| `backend/src/task_center_runner/audit/sandbox_events_sink.py` | `RotatingJsonlSink` (64 MiB roll + gzip + retention cap) and `iter_rotated_jsonl()`. |
| `backend/tests/unit_test/test_task_center_runner/test_daemon_pull.py` | 6 puller tests. |
| `backend/tests/unit_test/test_task_center_runner/test_sandbox_events_sink.py` | 4 rotation/sink tests. |
| `backend/tests/unit_test/test_task_center_runner/test_daemon_event_normalizer.py` | 4 normalizer tests (incl. CI grep boundary lint). |
| `backend/tests/unit_test/test_sandbox/test_daemon/test_layer_stack_emitters.py` | 3 squash-emit tests. |

### Modified files

| File | Change |
|---|---|
| `backend/src/task_center_runner/audit/performance_report.py` | `_iter_jsonl()` now delegates to `iter_rotated_jsonl()` so report readers see rotated `.gz` history transparently. |
| `backend/src/sandbox/daemon/audit_schema.py` | Added `LayerStackSection` dataclass + `build_layer_stack_event()` helper. |
| `backend/src/sandbox/daemon/layer_stack_runtime.py` | Wired `layer_stack.{lease_requested, lease_acquired, snapshot_prepared, lease_released}` emits through `prepare_workspace_snapshot` / `release_lease`. Added `emit_squash_event()` helper for `layer_stack.squash_{triggered,completed,failed}` on the `critical` lane. Per-lease bookkeeping in `_LEASE_TIMELINE` (cleared by `clear_layer_stack_runtime_caches_for_tests`). |
| `docs/daemon-audit-pull-consolidation-v3/phase-2-emitters-and-puller.md` | Added a `Status (2026-05-26)` block enumerating delivered surface, tests, and the deferred slice plan. |

## Contracts honored

- **Schema v1 frozen.** No section keys renamed; `layer_stack` section keys
  reuse Phase 1 dataclass conventions.
- **Lane assignment.** `layer_stack.{lease_*,snapshot_prepared}` → `normal`;
  `layer_stack.squash_{triggered,completed,failed}` → `critical`, per
  README §Lane assignment.
- **Causal chain (Principle 3).** Every emitted layer_stack event carries
  `lease_id`, `owner_request_id`, `manifest_version`, `manifest_root_hash`,
  and `lease_hold_ms` / `lease_wait_ms` so the report can reconstruct
  `lease → snapshot → release`.
- **Dual-write authoritativeness.** `payload["daemon_event"]` is written
  only by the normalizer, only when `EOS_AUDIT_FORENSIC_RAW_ENABLED=true`.
  Default test config asserts the key is absent. CI grep test
  (`test_daemon_event_writer_module_boundary`) prevents new offenders.
- **Pull RPC trust model.** Floor mutation is a runner-side concern; the
  daemon-side `api.audit.reset_floor` stub from Phase 1 remains a no-op
  gate, and the runner's `DaemonAuditPuller.reset_floor()` owns the actual
  state mutation (closing the Phase 1 deferred item).
- **Daemon-restart epoch handling.** Puller observes `boot_epoch_id`
  changes, resets cursor to 0, increments `daemon_restarts_observed`, and
  synthesizes a `daemon.restart_observed` event with
  `previous_epoch_id`/`new_epoch_id`. Tested.
- **Adaptive cadence.** Floor 100 ms by default; raises by 50% (cap
  1000 ms) after `pressure > 0.8` for 3 consecutive pulls; floor never
  auto-lowers; `reset_floor()` returns to default. Tested.
- **Disk contract.** Sink rotates at 64 MiB; gzip on rotation; retention
  cap honors `EOS_AUDIT_ARTIFACT_RETENTION_FILES` (default 8); rotated
  files live in the same parent directory as the live file, so caller-
  supplied EOS_TIER_RUN_ID-stable paths are preserved transparently.

## Tests

```
$ .venv/bin/pytest \
    backend/tests/unit_test/test_sandbox/test_daemon/ \
    backend/tests/unit_test/test_task_center_runner/ -q
174 passed in 2.27s
```

`.venv/bin/ruff check backend/src/task_center_runner/audit/ backend/src/sandbox/daemon/`
is clean.

## Deferred (not in slice 1)

Each item below is mergeable independently against the foundation that
slice 1 provides. Order is the recommended order; nothing forces it.

1. **`overlay_workspace` emitters** — instrument
   `sandbox/overlay/{lifecycle,handle,namespace_runner}.py` and
   `sandbox/ephemeral_workspace/pipeline.py`. Stamp
   `workspace_mode="ephemeral"`. Critical lane for
   `mounted/published/cleaned/cleanup_failed`.
2. **`isolated_workspace` emitters** — instrument
   `sandbox/isolated_workspace/pipeline.py` and
   `_control_plane/{pipeline_registry,pipeline_state,orphan_reaper,workspace_handle_lifecycle,linux_runtime}.py`.
   Critical lane for `entered/exited/evicted/orphan_*`, sample lane for
   `sampled`.
3. **`occ` emitters** — instrument
   `sandbox/daemon/{occ_runtime_services,changeset_projection}.py`.
   Normal lane for `changeset_prepared/transaction_lock_acquired/apply_committed/publish_layer`;
   critical lane for `conflict_rejected`.
4. **`os_resource.sampled`** — piggyback the existing command-exec
   resource-metrics tick (no new sampler thread, per the Phase 1
   revertability contract and the §2 "option (a) preferred" guidance).
5. **Generic plugin shim** in `backend/src/plugins/core/loader.py` —
   `plugin.{tool_invoked,tool_completed,error}` on `normal` lane;
   `plugin.peak_resident_sampled` on `sample`. No code in
   `backend/src/plugins/catalog/lsp/` may learn about audit.
6. **Background tool instrumentation** in
   `backend/src/engine/background/task_supervisor.py`. Emit from
   `_set_terminal_status` and `collect_completed`; heartbeat on the
   existing 60 s timer (zero new threads).
7. **Per-tool phase emitters** in `backend/src/engine/tool_call/dispatch.py`
   — slow-tail flush per V3 README §Per-tool phase sampling rule.
   `tool_call.{started,finished}` always emit on `normal`; `tool_call.phase`
   on `sample` subject to per-`tool_name` rolling P95 from a fixed-size
   deque. `phase_totals_rollup` is always populated from in-process timers.
8. **Puller-to-recorder wiring** — call `DaemonAuditPuller.start()` from
   `AuditRecorder.start()` (or a host-level wrapper) and `stop()` before
   `dispose()`. Emit callback feeds `RotatingJsonlSink.append_event(
   normalize_pulled_event(raw, boot_epoch_id=..., task_center_run_id=...))`.
9. **`os_resource` resource metrics promotion** — once the sampler is
   piggybacked, populate `OsResourceSection` and route through the same
   normalizer + sink path.
10. **Phase 3 wiring** — report rendering and release gates remain
    Phase 3 scope; nothing in slice 1 changes the existing
    `performance_report.py` schema.

## Cleanup performed

- Removed unused `import statistics` from `daemon_pull.py`.
- Tightened `manifest_root_hash` import in `layer_stack_runtime.py` after
  ruff flagged it as unused (the value is provided by
  `PrepareWorkspaceSnapshotResult.root_hash`, no separate computation
  needed).
- No legacy code removal: `stream_bridge.py` and the V1 stream-derived
  `sandbox_events.py` consumer remain in place. Per V3 README §Stream-bridge
  fallback sunset, retirement waits on K=5 clean heavy runs and is filed as
  a follow-up tracking issue, not this slice's work.

## Risk notes

- **Synchronous gzip on rotation** diverges from the plan's "background
  thread, bounded queue depth = 2." If a heavy run's tail trace shows a
  rotation pause > 200 ms, slice 2 should move the gzip behind a
  ThreadPoolExecutor with a bounded queue. Until measured, the simpler
  synchronous path stays. (Memory: `feedback_use_venv_pytest` — using
  `.venv/bin/pytest` for measurements.)
- **`_LEASE_TIMELINE` is module-global** in `layer_stack_runtime.py`.
  Concurrent leases under the same `layer_stack_root` are serialized by the
  daemon's existing `_MANAGER_CACHE_LOCK` indirectly (lease IDs are unique
  UUIDs); a stale entry only occurs if the daemon process crashes between
  `prepare_workspace_snapshot` and `release_lease`, in which case the
  whole ring is reset by the new epoch. Acceptable.
- **No production code paths consume the puller yet.** Slice 1 is library
  surface plus the layer_stack proof. Wiring is item 8 in the deferred
  list; until then `sandbox_events.jsonl` is still written by the legacy
  `AuditRecorder._record_sandbox_event` stream-bridge path.
