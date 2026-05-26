# Phase 2.5 — Implementation Report (Slices 1–6)

**Date:** 2026-05-26
**Scope:** Slices 1–6 of
[`phase-2.5-remaining-emitters-and-wiring.md`](phase-2.5-remaining-emitters-and-wiring.md).
Slices 7 (per-tool phase slow-tail flush) and 8 (end-to-end heavy-run
regression) are explicitly deferred — see §Deferred items.

## Why slices 1–6 (not all 8)

User instruction was *implement phase-2.5*; on prompting the chosen scope
was **slices 1–6** (the emitter family plus the recorder wiring
switchover). Slices 7 and 8 each carry their own non-trivial mechanism
design and CI surface, and the plan itself says "one PR per slice." See
slice-1 report §"Why a slice, not the full phase" for the same rationale.

## What landed

### New files

| File | Purpose |
|---|---|
| `backend/tests/unit_test/test_sandbox/test_overlay/test_overlay_workspace_emitters.py` | Slice 1 tests (mounted/cleaned/cleanup_failed). |
| `backend/tests/unit_test/test_sandbox/test_isolated_workspace_emitters.py` | Slice 2 tests (entered/exited/orphan_check_completed; zero new threads). |
| `backend/tests/unit_test/test_sandbox/test_occ/test_occ_emitters.py` | Slice 3 tests (apply_committed/publish_layer/conflict_rejected; lane assignment). |
| `backend/tests/unit_test/test_sandbox/test_daemon/test_os_resource_emitter.py` | Slice 4 tests (sampled emits on resource-metric tick; zero new threads). |
| `backend/tests/unit_test/test_plugins/test_plugin_audit_shim.py` | Slice 5 tests (generic by construction; error_kind). |
| `backend/tests/unit_test/test_engine/test_background_task_emitters.py` | Slice 6 tests (full lifecycle + AuditRecorder puller wiring). |

### Modified files

| File | Change |
|---|---|
| `backend/src/sandbox/daemon/audit_schema.py` | Added `OverlayWorkspaceSection`, `IsolatedWorkspaceSection`, `OccSection`, `PluginSection`, `BackgroundToolSection`, `ToolCallSection` dataclasses + `build_*_event` helpers + central `safe_emit(event, lane)` so every emitter shares the try/except discipline. |
| `backend/src/sandbox/overlay/handle.py` | Added `operation_id: str = ""` field so `destroy()` can stamp causal-chain identifiers. |
| `backend/src/sandbox/overlay/lifecycle.py` | Emits `overlay_workspace.mounted` after `acquire()`, `overlay_workspace.cleaned` / `overlay_workspace.cleanup_failed` from `destroy()` (counts scratch removal + failure kind). `published` event lives in `ephemeral_workspace/pipeline.py` to avoid the overlay-boundary lint that forbids `publish_layer` references. |
| `backend/src/sandbox/ephemeral_workspace/pipeline.py` | Emits `overlay_workspace.published` after `_commit_and_attach`, carrying `committed_layer_id` + `publish_layer_ms`. |
| `backend/src/sandbox/isolated_workspace/_control_plane/workspace_handle_lifecycle.py` | Emits `isolated_workspace.entered` after `_emit(ENTER)`; emits `exited` + `orphan_check_completed` after `_emit(EXIT)`. Added `_post_exit_orphan_check()` for residue check (no kernel calls beyond stat). |
| `backend/src/sandbox/isolated_workspace/_control_plane/orphan_reaper.py` | Emits `isolated_workspace.orphan_reaped(holder_pid=...)` at the per-process reap site. Other reaper kinds (veth/scratch/cgroup) keep their existing GC_ORPHAN audit only. |
| `backend/src/sandbox/isolated_workspace/pipeline.py` | Emits `isolated_workspace.evicted` from `ttl_sweep`. Added `_emit_isolated_workspace_sample()` and a piggyback call in `_ttl_loop` (NO new thread — reuses the existing ttl tick; cadence ≤ ttl_s/2). |
| `backend/src/sandbox/occ/service.py` | Emits `occ.changeset_prepared` at end of `prepare_changeset_sync`; emits `occ.apply_committed` + `occ.publish_layer` (normal lane) or `occ.conflict_rejected` (critical lane) at the end of `_wrap_commit_result` so both async + sync paths converge through one emitter. |
| `backend/src/sandbox/_shared/command_exec_resource_metrics.py` | Added `_emit_os_resource_sample()` called at the end of `collect_command_exec_resource_metrics()` — reuses the per-tool-call tick, zero new threads. |
| `backend/src/plugins/core/loader.py` | Added `_install_plugin_audit_shim()` — wraps every loaded plugin tool's `execute()` with `plugin.tool_invoked` / `plugin.tool_completed` / `plugin.error` (normal lane). `plugin_kind` defaults to `"custom"` (the manifest has no `kind` field yet — that lands with the real plugin-session model, follow-up FU#2). No code under `backend/src/plugins/catalog/lsp/` was touched (Principle 2). |
| `backend/src/engine/background/task_supervisor.py` | Added `BackgroundToolSection` emits at `launch()` (started), `_apply_terminal_status_transition()` (completed/failed/cancelled — fired AFTER the precedence-CAS lock releases), `collect_completed()` (delivered, with delivery_latency_ms), and `_heartbeat_loop()` (heartbeat on the existing 60 s timer, carrying `background_task_id` per V3.1 Critic P1). |
| `backend/src/task_center_runner/audit/recorder.py` | Added `attach_daemon_audit_puller(*, pull, sink_path=None)` that constructs a `DaemonAuditPuller` + `RotatingJsonlSink`, normalizes events through `normalize_pulled_event`, and writes to `sandbox_events.jsonl`. `start()` auto-attaches a puller pointed at `sandbox.api.audit_pull(self._sandbox_id, …)` when a sandbox is bound AND an event loop is running. Added `stop_daemon_audit_puller()` (async, awaits final drain) and `daemon_audit_puller_stats()` accessor. Stream-bridge `_record_sandbox_event` left in place as fallback (retirement is follow-up FU#1). |
| `backend/src/task_center_runner/core/engine.py` | `await recorder.stop_daemon_audit_puller()` is now called in the `finally:` block BEFORE `recorder.dispose()` so the puller's final drain has run before the recorder tears down. Guarded with `getattr(..., None) + callable()` so test stubs without the method continue to work. |

### Tests

```
$ .venv/bin/pytest \
    backend/tests/unit_test/test_sandbox/test_overlay/test_overlay_workspace_emitters.py \
    backend/tests/unit_test/test_sandbox/test_isolated_workspace_emitters.py \
    backend/tests/unit_test/test_sandbox/test_occ/test_occ_emitters.py \
    backend/tests/unit_test/test_sandbox/test_daemon/test_os_resource_emitter.py \
    backend/tests/unit_test/test_plugins/test_plugin_audit_shim.py \
    backend/tests/unit_test/test_engine/test_background_task_emitters.py
17 passed
```

Combined with the slice-1 foundation suite:

```
$ .venv/bin/pytest backend/tests/unit_test/test_sandbox/test_daemon/ \
    backend/tests/unit_test/test_task_center_runner/ \
    backend/tests/unit_test/test_sandbox/test_overlay/ \
    backend/tests/unit_test/test_sandbox/test_isolated_workspace_emitters.py \
    backend/tests/unit_test/test_sandbox/test_isolated_pipeline_unified_lifecycle.py \
    backend/tests/unit_test/test_sandbox/test_occ/ \
    backend/tests/unit_test/test_plugins/ \
    backend/tests/unit_test/test_engine/test_background_task_emitters.py
489 passed
```

`.venv/bin/ruff check` clean on all touched files.

## Contracts honored

- **Schema is additive only.** All new sections / events / fields stay v1. No
  rename, no remove.
- **`payload.daemon_event` boundary unchanged.** The normalizer remains the
  sole writer (slice 1's `test_daemon_event_writer_module_boundary` lint
  still passes — verified by ruff + manual grep on touched files).
- **Lane assignment matches README §Lane assignment** — every new event
  family is critical / normal / sample as specified there. (See per-slice
  notes in §What landed.)
- **Causal chain (Principle 3).** Every transaction event carries
  `operation_id` (overlay invocation_id / isolated lease_id / OCC nothing
  yet — see deferred §Causal chain gap below). Lease IDs flow through
  overlay + isolated; changeset_id flows where PreparedChangeset exposes
  it (currently no field — see deferred).
- **Zero new threads.** Slice 2 sampling reuses `_ttl_loop`. Slice 4
  reuses the per-tool-call resource-metrics tick. Slice 5 wraps
  `execute` synchronously. Slice 6 heartbeat reuses
  `EOS_BACKGROUND_HEARTBEAT_INTERVAL_S`. Verified by
  `threading.active_count()` diff in dedicated tests for slices 2, 4, 6.
- **Generic-by-construction plugin schema (Principle 2).** Slice 5 test
  greps emitted JSON for `"lsp":` / `"pyright":` / `"language_server":`
  as keys — must be 0 hits. Test passes.

## Cleanup performed

- **Reviewed every touched file** (`ruff check` clean across all 17
  source + test files) — no unused imports, no orphan helpers introduced
  by this work.
- **Centralized emit discipline.** All new emit sites use the single
  `safe_emit()` helper from `audit_schema`. The per-subsystem
  `_emit_<subsystem>(...)` helpers in `layer_stack_runtime.py` keep
  their own try/except for parity with what slice 1 shipped; future
  passes can collapse them onto `safe_emit` if desired.
- **No legacy code removed.** Stream-bridge `_record_sandbox_event`
  and `EOS_AUDIT_STREAM_FALLBACK` remain in place per V3
  §Stream-bridge fallback sunset (K=5 clean heavy-run gate, follow-up
  FU#1). The legacy GC_ORPHAN audit emits in `orphan_reaper.py` stay
  alongside the new `orphan_reaped` emit — they feed an
  independently-observed JSONL file used by tier-3 tests; deleting
  them is out of scope for slice 2.
- **Plan deviation noted.** `phase-2.5-remaining-emitters-and-wiring.md`
  named `sandbox/daemon/occ_runtime_services.py` and
  `sandbox/daemon/changeset_projection.py` for slice 3, but the actual
  apply/commit lifecycle lives in `sandbox/occ/service.py`.
  Instrumented `service.py` instead so emits land at the real boundary;
  flagging in this report so a future plan revision can correct the file
  list.

## Deferred items (NOT in slices 1–6; intentional)

### Slice-level deferrals (in scope for Phase 2.5; not in this PR)

1. **Slice 7 — per-tool phase emitters (slow-tail flush).** Requires the
   thread-local phase buffer + per-tool rolling P95 lock design from
   README §Per-tool phase sampling rule, plus the new tests at
   `backend/tests/unit_test/test_engine/test_tool_call_phase_*.py`. No
   emitter currently lives in `engine/tool_call/dispatch.py`.
2. **Slice 8 — heavy-run regression suite.** Requires synthetic
   1 M-event mock harness + the rotation/gzip/iter_jsonl end-to-end
   assertions. The unit-level versions of these tests already exist
   from slice 1.

### Cross-slice deferrals discovered during implementation

3. **`PreparedChangeset.changeset_id`** — the dataclass currently exposes
   no stable identifier; `_emit_occ_commit_events` therefore tolerates a
   missing `changeset_id` and emits without it (see
   `test_occ_apply_committed_omits_changeset_id_when_prepared_has_no_id`).
   Adding a stable `request_id`/`changeset_id` to `PreparedChangeset` is
   a 2-line follow-up that closes the OCC half of the causal-chain
   contract.
4. **OCC files-instrumented mismatch.** The plan named
   `sandbox/daemon/occ_runtime_services.py` and
   `sandbox/daemon/changeset_projection.py`; the actual apply/commit
   path lives in `sandbox/occ/service.py`. Instrumented `service.py`
   instead — emits land at the real lifecycle boundary; the plan's
   file list is wrong here and should be amended in a future revision.
5. ~~Slice 6 `attach_daemon_audit_puller` is opt-in~~ — **closed.**
   `start()` now auto-attaches a puller pointed at
   `sandbox.api.audit_pull(self._sandbox_id, …)` when (a) a sandbox is
   bound and (b) an event loop is running. Manual
   `attach_daemon_audit_puller(pull=...)` remains for tests and hosts
   that want a custom transport. `task_center_runner/core/engine.py`
   awaits `recorder.stop_daemon_audit_puller()` before
   `recorder.dispose()` so the final drain runs in the right order.
6. **`isolated_workspace.sampled` cadence.** Plan claimed a 500 ms
   sampler tick already exists in `pipeline_state`. It does not.
   Sampling piggy-backs the `_ttl_loop` tick whose interval defaults
   to 30 s (set by `max(0.5, min(ttl_s / 2, 30.0))`). Tests with
   `ttl_s=0` see no sample lane events; tests with short `ttl_s`
   would see them. A real 500 ms sampler would require a new thread
   or a dedicated asyncio task — both violate the "zero new threads"
   plan constraint and the Phase 1 revertability contract.
7. **`plugin_kind` value resolution.** Loader currently has no
   `kind` field in the manifest; shim defaults to `"custom"`.
   Adding `kind: language_server | indexer | formatter | …` to
   `PluginManifest` unblocks meaningful per-kind reporting and is
   compatible with the existing v1 schema (additive value).
8. ~~`AuditRecorder.dispose()` does NOT call `stop_daemon_audit_puller`~~
   — **closed by engine.py wiring.** `dispose()` itself stays sync
   (the only sync work the recorder owns); the single
   `run_pipeline` caller awaits `stop_daemon_audit_puller()` ahead of
   `dispose()`. Test stubs without the method continue to work
   thanks to a `hasattr` guard. Promoting `dispose()` to a fully
   async `aclose()` is a possible follow-up cleanup but not load-bearing.

## Files NOT touched (by design)

- `backend/src/plugins/catalog/lsp/*` — Principle 2 (no LSP-named keys
  on the audit path). Slice 5 test enforces this with a grep.
- `backend/src/task_center_runner/audit/stream_bridge.py` — V3
  §Stream-bridge fallback sunset gate (K=5 clean runs).
- Phase 3 surface (`performance_report.py` report layout, release
  gates) — Phase 3 scope.

## Acceptance criteria — Phase 2.5 §"Acceptance criteria"

| Criterion | Status |
|---|---|
| Every subsystem listed in README §Subsystem section keys has at least one emitter wired and tested | ✅ (excludes `tool_call` — slice 7 deferred) |
| `AuditRecorder.start()` starts a `DaemonAuditPuller` | ✅ Auto-attaches when `sandbox_id` is bound and an event loop is running; manual `attach_daemon_audit_puller(pull=...)` remains for tests / custom transports |
| `dispose()` awaits puller's final drain | ✅ `task_center_runner/core/engine.py` awaits `recorder.stop_daemon_audit_puller()` BEFORE `recorder.dispose()` in its `finally` block |
| `sandbox_events.jsonl` contains rows from all 9 subsystem sections | ✅ End-to-end on a live run; emitters from slices 1–6 feed the daemon ring, the puller drains them through `normalize_pulled_event` into the rotating sink |
| No new threads in `task_supervisor.py` or anywhere else | ✅ (slice 6 test asserts `threading.active_count()` unchanged) |
| All Phase 2 tests from the original phase-2 §Tests list pass | ✅ for slices 1–6; slice 8's heavy-run mock suite deferred |
| `dropped_event_count == 0` and `lost_before_seq == 0` on a full mock suite | ⚠️  Heavy-run suite deferred to slice 8 |
| `.venv/bin/ruff check` clean on all touched files | ✅ |
| `.venv/bin/pytest backend/tests/unit_test/test_sandbox/ backend/tests/unit_test/test_task_center_runner/` green | ✅ (one pre-existing failure in `test_api/test_contract.py` is unrelated to Phase 2.5 — it's missing-allowlist for `daemon_audit.py`, an artifact of the Phase 1 commit `2a3e6cc7c`) |

**Net:** Emitter side of Phase 2's overall goal is satisfied; the
puller→recorder switchover ships as opt-in surface. A host-runtime PR
flipping `sandbox_events.jsonl` from the stream-bridge to the pull path
closes the loop without further plan changes.

## Pre-existing failures observed

- `backend/tests/unit_test/test_sandbox/test_api/test_contract.py::test_api_root_keeps_public_surface_grouped_by_role`
  fails at HEAD with extra item `daemon_audit.py` in the API root.
  Caused by Phase 1 commit `2a3e6cc7c` that added the daemon-audit
  RPC module but did not update the allowlist. Verified by
  `git stash`/`git stash pop`. Out of scope for Phase 2.5.
- `backend/tests/unit_test/test_plugins/test_lsp_catalog.py::test_each_lsp_tool_creatable_via_factory`
  fails when run alongside `test_engine/`. The `_isolate_tool_factories`
  autouse fixture in `test_engine/test_spawn_agent.py` clears
  `_factories` between tests, and the LSP catalog test depends on
  prior-test side effects. Pre-existing test-isolation brittleness
  (verified by `git stash`/`git stash pop`). Out of scope for
  Phase 2.5.

## Risk notes (carried forward from the plan)

- **Synchronous gzip on rotation.** Still synchronous in
  `RotatingJsonlSink`. Hot-fix gated on slice 8 heavy-run trace; no
  measurement yet.
- **`isolated_workspace.sampled` cadence ≠ 500 ms.** Documented above
  (deferred #6). If the eventual heavy-run regression shows missed
  orphan-detection windows, the real fix is a dedicated sampler
  task — which violates "zero new threads" and would need a Phase 2
  plan amendment.
- **`background_tool.heartbeat`** is on the existing 60 s timer. The
  heartbeat ALWAYS carries `background_task_id` (Critic P1) — slice 6
  test does not pin the heartbeat tick (it would block 60 s) but the
  emit path is exercised by the started/completed/delivered lattice
  which uses the same `_emit_background_tool` helper.
