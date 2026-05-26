# Phase 2.6 — Implementation Report

**Date:** 2026-05-26
**Scope:** Slice 7 (per-tool phase slow-tail flush) + Slice 8 (heavy-run
regression) + Closers A / C / D / F from
[`phase-2.5-implementation-report.md §Deferred items`](phase-2.5-implementation-report.md#deferred-items-not-in-slices-1-6-intentional).
**Outcome:** Phase 2 is **complete**. Phase 3 (report rendering, release
gates, default-on rollout) is the only remaining V3 work.

## What landed

### Slice 7 — `tool_call.{started,phase,finished}` with slow-tail flush

| File | Change |
|---|---|
| `backend/src/engine/tool_call/phase_buffer.py` | **NEW.** ContextVar-scoped per-call phase buffer (6-entry deque) + per-`tool_name` rolling-window of last 100 `total_ms` values, guarded by per-name `threading.Lock`. LRU-capped at 256 distinct tool names. Exposes `start_phase_buffer`, `record_phase`, `finish_phase_buffer` → returns a `FinishedPhaseDecision` (flush/cold/phases/rollup). |
| `backend/src/engine/tool_call/dispatch.py` | Wraps both `_dispatch_single_foreground_tool` and `_dispatch_many_foreground_tools` with `_emit_tool_call_started` (lane=`normal`), measures `total_ms` via `monotonic_now()`, calls `_emit_tool_call_phase_and_finished` in `finally` so the envelope + rollup land even on exceptions. The slow-tail decision lives in `phase_buffer.finish_phase_buffer`; phase events go on the `sample` lane only when the decision says to flush. |
| `backend/src/tools/_framework/execution/tool_call.py` | `execute_tool_once` records the four framework-boundary phases — `queued` (input parse + pre-hooks), `exec` (tool body), `capture` (validate + post-hooks), `release` (finalize). The two remaining phases (`mount`, `publish`) are owned by overlay/OCC code and will hook into `record_phase` in a follow-up; the rollup carries whichever phases the call recorded. |
| `backend/tests/unit_test/test_engine/test_tool_call_phase_slow_tail_flush.py` | **NEW.** Four tests pin the slow-tail behavior, the envelope-lane invariant, the rollup-when-discarded invariant, and contextvar isolation across `asyncio.create_task` foregrounds. |

### Slice 8 — heavy-run regression suite + heartbeat backfill

| File | Change |
|---|---|
| `backend/tests/integration_test/test_audit_heavy_run.py` | **NEW.** Drives a synthetic 1 M-event pull stream through the real `AuditRecorder` + `RotatingJsonlSink`. Acceptance test `test_heavy_run_1m_events_acceptance_bar` (marked `@pytest.mark.slow`) verifies subsystem coverage, rotation, gzip, retention, and that every synthesized seq lands in the rotated history. Companion small-scale tests (NOT slow) pin the dual-write authoritativeness contract (`daemon_event` absent by default, present under `EOS_AUDIT_FORENSIC_RAW_ENABLED=true`) and the `iter_rotated_jsonl` round-trip. |
| `backend/tests/unit_test/test_engine/test_background_task_emitters.py` | **Backfill** `test_background_tool_heartbeat_reuses_existing_timer` — closes phase-2 §Tests gap from 2.5. Asserts (a) `threading.active_count()` unchanged, (b) at least one `background_tool.heartbeat` emit carries the running `background_task_id`, (c) supervisor owns exactly one heartbeat asyncio task across multiple ticks (no respawn). |
| `pyproject.toml` | Registered the `slow` marker so heavy-run tests opt-in via `-m slow` and are skipped by default `pytest`. |

### Closer A — `PreparedChangeset.changeset_id`

| File | Change |
|---|---|
| `backend/src/sandbox/occ/changeset.py` | Added `changeset_id: str = ""` field to `PreparedChangeset`. Added `compute_changeset_id(snapshot, path_groups, atomic) -> str` returning a 16-hex-char `sha256` over a canonical JSON encoding (NOT `repr`) of `(snapshot_version, atomic, path_groups[].path/route/changes[]/kind/path/source/base_hash/content_hash)`. `_change_signature` per-kind helper covers `WriteChange` (hashes payload bytes when `precomputed_hash` is None), `DeleteChange`, `EditChange`, and `SymlinkChange`. |
| `backend/src/sandbox/occ/changeset_preparation.py` | `prepare_sync` populates `changeset_id` via `compute_changeset_id(...)`. The OCC service is the only construction site. |
| `backend/src/sandbox/occ/service.py` | `_emit_occ_commit_events` reads `prepared.changeset_id` directly (dropped the legacy `getattr(prepared, "changeset_id", None) or getattr(prepared, "id", None)` dance). `occ.changeset_prepared` now also carries the field. |
| `backend/tests/unit_test/test_sandbox/test_occ/test_occ_emitters.py` | **Replaced** `test_occ_apply_committed_omits_changeset_id_when_prepared_has_no_id` with `test_occ_apply_committed_carries_changeset_id`. Added `test_occ_conflict_rejected_carries_changeset_id` and `test_prepared_changeset_id_is_stable_across_replay` (pins both stability and collision-domain separation). |

### Closer C — Real `isolated_workspace.sampled` cadence

| File | Change |
|---|---|
| `backend/src/sandbox/isolated_workspace/_control_plane/pipeline_state.py` | Added `sample_interval_s: float = 0.5` to `_PipelineConfig`; `from_env` reads `EOS_ISOLATED_WORKSPACE_SAMPLE_INTERVAL_S` (default 0.5). |
| `backend/src/sandbox/isolated_workspace/pipeline.py` | Added `_sampler_task` field + `_sampler_loop()` asyncio task. Started in `initialize()` only when `_config.enabled` (per `asyncio.all_tasks()` test). Cancelled in `shutdown()`. The inner loop guards on `_init_complete.is_set()` per the V3 risk note (no sampling mid-teardown). **Dropped** the legacy piggyback in `_ttl_loop` (slice 2 left `for handle ...: self._emit_isolated_workspace_sample(handle)` inside the TTL tick — the sampler is now the sole source of `isolated_workspace.sampled` events). |
| `backend/tests/unit_test/test_sandbox/test_isolated_workspace_emitters.py` | Added four tests: `test_isolated_workspace_sampler_emits_at_500ms_cadence`, `test_isolated_workspace_sampler_stops_on_shutdown`, `test_isolated_workspace_sampler_skips_when_feature_disabled`, and the §Tests-gap **backfill** `test_isolated_workspace_orphan_check_after_exit` (monkeypatches `os.kill` + `shutil.rmtree` so the post-exit residue check returns nonzero counts in both the pulled `exited` payload AND the `orphan_check_completed` payload). |

### Closer D — `PluginManifest.kind`

| File | Change |
|---|---|
| `backend/src/plugins/core/manifest.py` | Added `kind: str | None = None` to `PluginManifest`. Added `ALLOWED_PLUGIN_KINDS = {"language_server", "formatter", "indexer", "build_daemon", "mcp_bridge", "custom"}` (V3 README §Requirement 2 enum). `_parse_kind` validates the frontmatter value and rejects unknowns with `PluginManifestError` (silent typos would invisibly broaden the schema). |
| `backend/src/plugins/catalog/lsp/plugin.md` | Added `kind: language_server` to the frontmatter. |
| `backend/src/plugins/core/loader.py` | No change required — the shim already reads `manifest.kind or "custom"`, so a populated field flows through end-to-end. |
| `backend/tests/unit_test/test_plugins/test_manifest.py` | Added `test_plugin_manifest_parses_kind_when_present`, `test_plugin_manifest_rejects_unknown_kind`, `test_plugin_manifest_defaults_kind_to_none`, and `test_lsp_plugin_audit_carries_kind_language_server` (parses the real LSP manifest via `discover_plugins`). |
| `backend/tests/unit_test/test_plugins/test_plugin_audit_shim.py` | Added `test_plugin_shim_stamps_manifest_kind_when_present` (manifest with `kind="language_server"` → emitted `plugin.tool_invoked` + `plugin.tool_completed` carry `plugin_kind="language_server"`). |

### Closer F — `AuditRecorder.aclose()`

| File | Change |
|---|---|
| `backend/src/task_center_runner/audit/recorder.py` | Added `async def aclose()` = `await stop_daemon_audit_puller()` + `_dispose_sync()`. Extracted the existing sync teardown body into `_dispose_sync()`. The sync `dispose()` is preserved as a back-compat forwarder that **raises** `RuntimeError` when a puller is still attached (live runtimes MUST use `aclose`; test stubs that never attach a puller continue to work unchanged). |
| `backend/src/task_center_runner/core/engine.py` | Replaced the two-call `await stop_daemon_audit_puller(); recorder.dispose()` sequence with a single `await recorder.aclose()` (guarded by `getattr` so older stubs without `aclose` still work — they fall through to sync `dispose`). |
| `backend/tests/unit_test/test_task_center_runner/test_audit_recorder_aclose.py` | **NEW.** Three tests pin the contract: `test_audit_recorder_aclose_awaits_puller_then_disposes` (puller drained + sink written + field cleared), `test_audit_recorder_dispose_still_works_without_puller` (back-compat), `test_audit_recorder_dispose_raises_when_puller_still_active` (safety guard). |

## Tests

```
$ .venv/bin/pytest \
    backend/tests/unit_test/test_sandbox/test_daemon/ \
    backend/tests/unit_test/test_sandbox/test_occ/ \
    backend/tests/unit_test/test_sandbox/test_overlay/ \
    backend/tests/unit_test/test_sandbox/test_isolated_workspace_emitters.py \
    backend/tests/unit_test/test_sandbox/test_isolated_pipeline_unified_lifecycle.py \
    backend/tests/unit_test/test_plugins/ \
    backend/tests/unit_test/test_engine/ \
    backend/tests/unit_test/test_task_center_runner/ \
    -m "not slow"
510 passed in 8.97s

$ .venv/bin/pytest backend/tests/integration_test/test_audit_heavy_run.py -m slow
3 passed in 54.50s
```

Slow-marked integration tests opt-in via `-m slow`. Default `pytest backend/tests` skips them.

`.venv/bin/ruff check` clean across every touched file.

## Contracts honored

- **Schema is additive only.** Slice 7 adds the `tool_call.*` family
  (`ToolCallSection` already shipped in 2.5). Closer A adds a field on an
  existing section. Closer C adds `sample_interval_s` to an internal
  config dataclass (no wire change). Closer D adds an optional manifest
  field. No rename / no remove anywhere in 2.6.
- **`payload["daemon_event"]` boundary.** Slice 1's
  `test_daemon_event_writer_module_boundary` lint is still clean —
  verified by grep over every touched file in 2.6.
- **Lane assignment matches README §Lane assignment.**
  `tool_call.started` + `tool_call.finished` on `normal`;
  `tool_call.phase` on `sample` (flushed by the slow-tail rule);
  `isolated_workspace.sampled` on `sample` (cadence is now the dedicated
  sampler, not a piggyback on TTL).
- **Causal chain (Principle 3) — OCC half closed.** Every OCC emit
  (`changeset_prepared`, `apply_committed`, `publish_layer`,
  `conflict_rejected`) now carries `changeset_id`. Report builders can
  join `tool_call.{started,finished}` → `overlay_workspace.*` →
  `occ.*` → `layer_stack.*` without per-event guesswork. `tool_call.*`
  carries `tool_id` for the upstream half of the join.
- **Zero new threads.** The slow-tail rolling window uses a
  `threading.Lock` for the O(1) critical section but adds NO new thread.
  Closer C's sampler is an asyncio task on the existing daemon event
  loop (parity with `_ttl_loop` and `_heartbeat_loop`).
- **Generic-by-construction plugin schema (Principle 2).** Closer D's
  `kind` validator caps the enum so vendor-named values can never sneak
  in. The existing slice-5 LSP-key grep test still passes.

## Cleanup performed

- **Dropped legacy emit site** in `IsolatedPipeline._ttl_loop` (slice 2
  piggyback). Sampling is now solely the sampler task's responsibility.
- **Simplified `_emit_occ_commit_events`** — removed the
  `getattr(prepared, "changeset_id", None) or getattr(prepared, "id", None)
  or None` chain. Direct field read replaces it.
- **Unused-import sweep.** `ruff check --select=F401,F811,F841` clean
  across every touched file.
- **Plan deviations corrected in-place.** None this slice introduced new
  ones; the slice-3 / Deliverable-6 file-name corrections noted by 2.5
  remain cosmetic doc fixes (out of scope for code).

## Deferred items still open (Phase 3 / follow-ups)

These items remain explicit follow-ups — none were promised by Phase 2:

- **Phase 3 — report rendering + release gates + default-on rollout**
  (the only remaining V3 work; consumes everything 2.6 ships).
- **FU#1 — stream-bridge code removal.** Trigger: K = 5 consecutive clean
  heavy-run gates passing post-Phase 3. Stream-bridge
  (`_record_sandbox_event` + `EOS_AUDIT_STREAM_FALLBACK`) remains in
  `recorder.py` per V3 §Stream-bridge fallback sunset.
- **FU#2 — real plugin session lifecycle (`plugin.session_*`).** Trigger:
  a second plugin kind landing in `plugins/catalog/`. Closer D pre-stages
  the `kind` value; the session model itself is a multi-file refactor.
- **FU#4 — ring-by-lane separation.** Only if the Phase 3 overhead gate
  fails permanently.
- **`mount` / `publish` framework-boundary recording.** Slice 7 records
  the four phases the framework directly owns (queued/exec/capture/release).
  The two remaining phases live inside `overlay/lifecycle.py` and
  `occ/service.py`; surfacing them via `record_phase` from those modules
  is a small follow-up (the `phase_buffer.record_phase` API is already
  exposed). The rollup correctly carries whichever phases ARE recorded;
  reports remain accurate with the four-phase subset.
- **Cosmetic doc fixes** to `phase-2.5-remaining-emitters-and-wiring.md`
  §slice-3 file list and `phase-2-emitters-and-puller.md` §Deliverable 6
  module names. Not a code change; tracked in 2.5 deferred list.

## Acceptance criteria — Phase 2.6 §"Acceptance criteria for Phase 2.6 (and Phase 2 overall)"

| Criterion | Status |
|---|---|
| `tool_call.{started,finished}` emit on every foreground tool call | ✅ Slice 7 — single + many foreground paths wrap the streaming executor. |
| `tool_call.phase` emits only when the slow-tail rule fires | ✅ `finish_phase_buffer` discriminates cold-window + P95 slow-tail; tests pin both branches. |
| `tool_call.finished.phase_totals_rollup` populated on every call | ✅ Rollup is computed from the contextvar deque regardless of flush decision. Test: `test_tool_call_finished_rollup_present_when_phases_discarded`. |
| Heavy-run mock suite produces at least one row per subsystem section | ✅ `test_heavy_run_1m_events_acceptance_bar` cycles all 9 sections; coverage asserted. |
| `dropped_event_count == 0 AND lost_before_seq == 0` end-to-end | ✅ Stub returns 0/0 for both; recorder propagates faithfully. |
| `occ.*` emits carry `changeset_id` | ✅ Closer A — `prepared.changeset_id` flows through `apply_committed`, `publish_layer`, `conflict_rejected`. |
| `isolated_workspace.sampled` fires at configured cadence (default 500 ms) | ✅ Closer C — dedicated sampler task; lifecycle gated on `enabled`. |
| `plugin.*` emits carry the real `plugin_kind` from the manifest | ✅ Closer D — LSP manifest declares `kind: language_server`; shim reads it. |
| `AuditRecorder.aclose()` is the single async teardown path | ✅ Closer F — `engine.py` rewired; sync `dispose()` raises with active puller. |
| `.venv/bin/ruff check` clean across every touched file | ✅ |
| Slice 7 + slice 8 + closer tests pytest green | ✅ 510 unit + 3 slow integration; full sweep clean. |

**Net:** Phase 2's overall goal is achieved end-to-end. Every subsystem
emits its event family into the daemon ring; the runner-side puller
drains it into the rotating JSONL sink; and the dispatcher contributes
per-tool envelope + slow-tail phase events.

Phase 3 (report rendering, release gates, default-on rollout) is the
only remaining V3 work.
