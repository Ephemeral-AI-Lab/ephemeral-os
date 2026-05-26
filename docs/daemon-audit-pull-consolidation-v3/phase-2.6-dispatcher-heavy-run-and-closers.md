# Phase 2.6 — Dispatcher Slow-Tail, Heavy-Run Lock-In, and 2.5 Closers

> **Prerequisites:** Read [`README.md`](README.md) for cross-cutting
> contracts. Read
> [`phase-2.5-remaining-emitters-and-wiring.md`](phase-2.5-remaining-emitters-and-wiring.md)
> for slices 1–8 design and
> [`phase-2.5-implementation-report.md`](phase-2.5-implementation-report.md)
> for what already shipped and the deferred items this phase closes.

## Goal

Finish Phase 2's overall goal:

> *"every subsystem emits its event family into the daemon ring; the
> runner-side puller drains it into the rotating JSONL sink; and the
> dispatcher contributes per-tool envelope + slow-tail phase events."*

Phase 2.5 closed items 1 and 2. Phase 2.6 closes item 3 (slice 7) and
locks Phase 2's acceptance criteria into CI (slice 8). On top of that,
2.6 ships four targeted closers (A, C, D, F) from the
[`phase-2.5-implementation-report.md`](phase-2.5-implementation-report.md#deferred-items-not-in-slices-1-6-intentional)
deferred list so the schema and runtime are honest about the contracts
Phase 2.5 stamped.

**When 2.6 lands, Phase 2 is complete.** Phase 3 (report rendering,
release gates, default-on rollout) is the only remaining V3 work.

## What 2.6 finishes

| Item | Source | 2.6 closes |
|---|---|---|
| Slice 7 — per-tool phase emitters | `phase-2.5-remaining-emitters-and-wiring.md §Slice 7` | `tool_call.{started,phase,finished}` with slow-tail buffered flush |
| Slice 8 — end-to-end heavy-run regression | `phase-2.5-remaining-emitters-and-wiring.md §Slice 8` | 1 M-event mock-suite acceptance test in CI + `test_background_tool_heartbeat_reuses_existing_timer` test backfill (phase-2 §Tests gap from 2.5) |
| Closer A — `PreparedChangeset.changeset_id` | `phase-2.5-implementation-report.md` Deferred #A (Principle 3 gap on the OCC half of the causal chain) | Stable per-changeset id plumbed through `occ.*` emits |
| Closer C — Real isolated_workspace sampling cadence | Deferred #C (plan claimed 500 ms; piggyback uses ttl_loop's 30 s) | Dedicated asyncio task (NOT a thread — see §Zero-new-threads reading) + `test_isolated_workspace_orphan_check_after_exit` test backfill (phase-2 §Tests gap from 2.5) |
| Closer D — `PluginManifest.kind` field | Deferred #D (shim defaults to `"custom"`) | Optional `kind` value in `plugin.md` frontmatter; shim uses it |
| Closer F — `AuditRecorder.aclose()` async path | Deferred #F (sync `dispose()` + separate async stop) | Single `async def aclose()` that awaits puller drain + dispose |

Each item ships as its own PR per the "one PR per slice" guidance from
phase-2 and phase-2.5.

## Slice order (one PR per row)

Slice 7 first because it's the largest design surface. Closers A / C / D
/ F land in parallel after slice 7 (no inter-slice dependency). Slice 8
ships LAST so the heavy-run regression also exercises the new slice 7
emitters end-to-end.

### Slice 7 — `tool_call.{started,phase,finished}` with slow-tail flush

**Files to instrument:**
- `backend/src/engine/tool_call/dispatch.py` — envelope + rolling P95
  decision + phase-buffer flush.
- `backend/src/tools/_framework/execution/tool_call.py` — records
  `phase` entries into the active buffer at the framework boundary
  (queued / mount / exec / capture / publish / release as the inner
  stack hits each step).
- New: `backend/src/engine/tool_call/phase_buffer.py` — per-call
  fixed-size deque + per-`tool_name` rolling P95 store (see
  [README §Per-tool phase sampling rule]).

**Schema (additive — section already shipped in
`audit_schema.py::ToolCallSection`):**

```
tool_call.started   (normal lane) — always emit
tool_call.phase     (sample lane) — flushed per slow-tail rule
tool_call.finished  (normal lane) — always emit, with
                                    phase_totals_rollup populated
```

**Slow-tail mechanism (verbatim from V3 README §Per-tool phase sampling
rule; reproduced here for reviewers):**

1. Per-call: `contextvars.ContextVar` carrying a fixed-size deque of
   `{phase, duration_ms}` (max 6 entries, ≈ 96 bytes).
2. Per-`tool_name`: a rolling deque of last 100 `total_ms` values
   (≈ 800 bytes / active tool_name), protected by a per-`tool_name`
   `threading.Lock`. Critical section is O(1) — append + drop-oldest +
   P95 via `statistics.quantiles(n=20)[18]` (or `sorted()[idx]` on the
   fixed-size list — fine at N = 100).
3. On `tool_call.finished`:
   - **Cold window** — rolling-window has < 100 entries → flush all
     phase events.
   - **Slow tail** — rolling window full AND `total_ms ≥ P95` → flush
     all phase events.
   - **Else** — discard phase buffer; `phase_totals_rollup` is still
     populated on `tool_call.finished` from in-process timers.

**Tests** (under
`backend/tests/unit_test/test_engine/test_tool_call_phase_*.py`):

- `test_tool_call_phase_slow_tail_flush` — 200 invocations of a fake
  `smoke_tool` with deterministic timings `[10 ms × 190, 500 ms × 10]`;
  assert (a) first 100 calls flush all 6 phases; (b) of remaining 100,
  the 5 with `total_ms ≥ P95` flush all phases; (c) other 95 flush NO
  phase events but DO emit `tool_call.finished` with populated
  `phase_totals_rollup`.
- `test_tool_call_finished_rollup_present_when_phases_discarded` — one
  fast-tail call after warmup; assert rollup populated with all 6 phase
  keys.
- `test_tool_call_envelope_always_emits_on_normal_lane` — both started
  and finished present, lane = `normal`.
- `test_tool_call_phase_buffer_thread_local_under_many_foreground` —
  exercise `_dispatch_many_foreground_tools` with 4 concurrent fake
  tools; assert each call's phase buffer is independent (contextvars
  copy semantics).

---

### Slice 8 — End-to-end heavy-run regression suite

**Tests** (lifted from `phase-2.5 §Slice 8`):

- `test_sandbox_events_jsonl_rotates_at_64mib_and_caps_history` —
  synthetic 1 M-event mock-suite run; assert exactly N rotated files;
  live file ≤ 64 MiB.
- `test_sandbox_events_jsonl_rotation_path_stable_under_eos_tier_run_id`
  — full-pipeline version (slice-1 already covers the sink unit).
- `test_iter_jsonl_concatenates_rotated_gzipped_history` — full-pipeline
  version.
- `test_no_consumer_reads_daemon_event_under_default_config` — full
  mock suite with `EOS_AUDIT_FORENSIC_RAW_ENABLED` unset.
- `test_forensic_raw_present_when_env_enabled` — full mock suite with
  env enabled.
- Acceptance assertion: after one full mock-suite run,
  `dropped_event_count == 0` AND `lost_before_seq == 0`.
- New: `test_subsystem_section_coverage_in_heavy_run` — `jq` over the
  produced `sandbox_events.jsonl` and assert at least one row per
  subsystem section listed in
  [README §Subsystem section keys] (`daemon`, `layer_stack`,
  `overlay_workspace`, `occ`, `isolated_workspace`, `os_resource`,
  `plugin`, `background_tool`, `tool_call`).
- **Backfill** `test_background_tool_heartbeat_reuses_existing_timer` —
  closes phase-2 §Tests gap from 2.5 (which wired the heartbeat emit
  but did not pin the no-new-thread + existing-timer-reuse
  invariants). Snapshot `threading.active_count()` before launching a
  long-running fake background task, monkeypatch
  `_HEARTBEAT_INTERVAL_S` to 0.05 s, await one tick, assert (a) thread
  count unchanged, (b) at least one `background_tool.heartbeat` event
  with `background_task_id` matching the running task, (c) no
  additional asyncio task beyond the single `_heartbeat_task` the
  supervisor already owns.

Synthetic generator lives under
`backend/tests/integration_test/test_audit_heavy_run.py`; uses the
`AuditRecorder` + `RotatingJsonlSink` real classes with a stub
`DaemonAuditPuller.pull` returning a deterministic stream of 1 M
events.

---

### Closer A — `PreparedChangeset.changeset_id`

**Files to change:**
- `backend/src/sandbox/occ/changeset.py` — add
  `changeset_id: str` to `PreparedChangeset` (auto-populated from
  `uuid.uuid4().hex[:16]` at construction; `changeset_preparation.py`
  is the only constructor site).
- `backend/src/sandbox/occ/changeset_preparation.py` — populate the
  field when emitting `PreparedChangeset`.
- `backend/src/sandbox/occ/service.py` — propagate
  `prepared.changeset_id` into `_emit_occ_commit_events` so the OCC
  emits in slice 3 carry the id.

**Tests:**
- `test_occ_apply_committed_carries_changeset_id` (replaces the
  current `_omits_changeset_id_when_prepared_has_no_id` xfail-style
  test in `test_occ_emitters.py`).
- `test_occ_conflict_rejected_carries_changeset_id`.
- `test_prepared_changeset_id_is_stable_across_replay` — same inputs
  twice → SAME `changeset_id` (uses a content-hash derivation, not
  a fresh UUID, so replays match). [Open question — see Risk notes.]

---

### Closer C — Real `isolated_workspace.sampled` cadence

**File to change:**
- `backend/src/sandbox/isolated_workspace/pipeline.py` — add a
  dedicated `_sampler_loop()` asyncio task that runs at the cadence
  defined by `EOS_ISOLATED_WORKSPACE_SAMPLE_INTERVAL_S` (default
  `0.5` per V3 plan §Risk notes).

**Zero-new-threads reading:** README §Cross-cutting contracts says
literally "**zero new threads**" — not "no new concurrency primitives."
`_ttl_loop` and `_heartbeat_loop` are already asyncio tasks (not new
threads). Adding a `_sampler_loop()` asyncio task is consistent with
both existing patterns and the verbatim contract.

**Lifecycle:**
- Start the sampler in `initialize()` alongside `_ttl_task`, gated by
  `EOS_ISOLATED_WORKSPACE_ENABLED` (no-op when feature is off).
- Cancel the sampler in `shutdown()` alongside `_ttl_task`.
- Drop the existing piggyback on `_ttl_loop` (slice 2 left a
  `for handle in list(self._handles.values()): self._emit_isolated_workspace_sample(handle)`
  inside `_ttl_loop`).

**Cadence cap:** ~120 events/min/workspace at 500 ms × 1 sample event;
≈ 7 200 events/hour. Well under the 50 000-event ring cap with single
isolated workspace, comfortable up to ~6 concurrent isolated workspaces.
Per the V3 risk note, if parallel-isolated workspaces overrun the ring,
raise the cadence floor BEFORE raising the sample interval.

**Tests:**
- `test_isolated_workspace_sampler_emits_at_500ms_cadence` — patch the
  interval to 0.05 s; assert ≥ 3 samples within 200 ms.
- `test_isolated_workspace_sampler_stops_on_shutdown` — `await
  pipeline.shutdown()` and assert no further samples after the task
  cancels.
- `test_isolated_workspace_sampler_skips_when_feature_disabled` —
  `enabled=False`; assert no sampler task created (no new task in
  `asyncio.all_tasks()`).
- **Backfill** `test_isolated_workspace_orphan_check_after_exit` —
  closes phase-2 §Tests gap from 2.5 (which only covered the
  zero-orphan happy path). Enter a handle, mock `os.kill(handle.root_pid, 0)`
  to raise nothing (holder appears live) AND mock
  `handle.scratch_dir.exists()` to return True after exit (residue
  present); assert `orphan_holder_count > 0` and
  `orphan_scratch_count > 0` in both the pulled `isolated_workspace.exited`
  payload AND the `isolated_workspace.orphan_check_completed` payload.

---

### Closer D — `PluginManifest.kind` field

**Files to change:**
- `backend/src/plugins/core/manifest.py` — add `kind: str | None`
  default `None`; parse from frontmatter when present.
- `backend/src/plugins/catalog/lsp/plugin.md` — add
  `kind: language_server`.
- `backend/src/plugins/core/loader.py` — `_install_plugin_audit_shim`
  reads `manifest.kind or "custom"` (today defaults `"custom"`
  unconditionally).

**Allowed values (V3 README §Requirement 2 enum):**
`language_server`, `formatter`, `indexer`, `build_daemon`,
`mcp_bridge`, `custom`. The manifest parser MUST reject unknown
values so a typo doesn't silently widen the schema.

**Tests:**
- `test_plugin_manifest_parses_kind_when_present` — manifest with
  `kind: indexer` → `manifest.kind == "indexer"`.
- `test_plugin_manifest_rejects_unknown_kind` — manifest with
  `kind: nope` → `PluginManifestError`.
- `test_plugin_manifest_defaults_kind_to_none` — manifest without
  `kind` → `manifest.kind is None`; shim still emits with
  `plugin_kind="custom"`.
- `test_lsp_plugin_audit_carries_kind_language_server` — register the
  real LSP plugin; assert `plugin_kind == "language_server"` on the
  shim's emits.

---

### Closer F — `AuditRecorder.aclose()` async path

**Files to change:**
- `backend/src/task_center_runner/audit/recorder.py` — add
  `async def aclose(self) -> None:` that:
  1. `await self.stop_daemon_audit_puller()` (existing method).
  2. Synchronously runs the rest of today's `dispose()` body.
  Leave the sync `dispose()` method in place as a forwarder that
  raises if called when a puller is still active (so test stubs that
  call `dispose()` without the puller continue to work; live callers
  must use `aclose()`).
- `backend/src/task_center_runner/core/engine.py` — replace the
  current
  `await getattr(recorder, "stop_daemon_audit_puller", lambda: None)();
  recorder.dispose()` pair with a single
  `await recorder.aclose()` call (with the same `getattr` guard for
  back-compat with test stubs).

**Tests:**
- `test_audit_recorder_aclose_awaits_puller_then_disposes` — attach
  a puller, `await recorder.aclose()`, assert sink file written + no
  background tasks remain in `asyncio.all_tasks()`.
- `test_audit_recorder_dispose_still_works_without_puller` — back-compat
  for test stubs.

---

## Cross-cutting contracts (apply unchanged)

- **Schema is additive only.** Slice 7 adds `tool_call.*` event family
  (`ToolCallSection` already shipped in `audit_schema.py`). Closer D
  adds an optional manifest field. No rename / remove.
- **`payload["daemon_event"]` boundary.** The slice-1 CI lint
  (`test_daemon_event_writer_module_boundary`) must remain clean. No
  new file in 2.6 may reference `payload["daemon_event"]`.
- **Lane assignment.** Slice 7 lanes are already specified in
  [README §Lane assignment]. Closer C `isolated_workspace.sampled` is
  already `sample`.
- **Causal chain (Principle 3).** Closer A closes the OCC half of the
  chain; slice 7 envelope events MUST carry the `tool_id` so the
  report can join `tool_call.{started,finished}` → `overlay_workspace.*`
  → `occ.*` → `layer_stack.*`.
- **Zero new threads.** No new threads in slice 7's rolling-window lock
  (locking only — no new thread). Closer C uses an asyncio task on the
  existing event loop, consistent with `_ttl_loop` and
  `_heartbeat_loop`. The slice 7 phase buffer is `contextvars`-scoped
  (already async-safe; no new thread).

## Acceptance criteria for Phase 2.6 (and Phase 2 overall)

When the last slice merges, all of the following are true:

- `tool_call.{started,finished}` emit on every foreground tool call;
  `tool_call.phase` emits only when the slow-tail rule fires.
- `tool_call.finished.phase_totals_rollup` is populated on every call,
  even when phase events are discarded.
- Heavy-run mock suite produces at least one row per subsystem
  section in `sandbox_events.jsonl` AND
  `dropped_event_count == 0 AND lost_before_seq == 0`.
- `occ.*` emits carry `changeset_id`.
- `isolated_workspace.sampled` fires at the configured cadence
  (default 500 ms) when an isolated workspace is active.
- `plugin.*` emits carry the real `plugin_kind` from the manifest.
- `AuditRecorder.aclose()` is the single async teardown path; sync
  `dispose()` survives for test stubs.
- `.venv/bin/ruff check` clean across every touched file.
- `.venv/bin/pytest backend/tests/unit_test/test_engine/test_tool_call_phase_*.py
  backend/tests/integration_test/test_audit_heavy_run.py
  backend/tests/unit_test/test_sandbox/test_occ/test_occ_emitters.py
  backend/tests/unit_test/test_sandbox/test_isolated_workspace_emitters.py
  backend/tests/unit_test/test_plugins/test_lsp_catalog.py` green.

**Net:** every Phase 2 acceptance criterion in
[`phase-2-emitters-and-puller.md §Tests`](phase-2-emitters-and-puller.md)
holds. Phase 3 (release gates + default-on rollout) is the only
remaining V3 work.

## Risk notes

- **`changeset_id` derivation (closer A).** UUID is simplest; content
  hash makes replays match (useful for debugging concurrent commits
  against the same prepared changeset). Recommend content-hash
  derivation using `sha256(repr(path_groups) + repr(snapshot.version))`
  — but accept UUID if the hash version surfaces an unexpected
  collision domain. Decide before slice ships, then pin in the test
  name (`test_prepared_changeset_id_is_*`).
- **Slow-tail rolling-window memory growth.** N active `tool_name`s
  × 100 floats ≈ ~800 bytes each. At 50 active tool names that's ~40
  KB; bounded. If a malicious agent invents arbitrary `tool_name`
  strings, the dict grows unbounded — add an `OrderedDict`-based LRU
  cap of 256 distinct tool_names in `phase_buffer.py`. Documented in
  the slice but not test-pinned (would require a hostile-agent
  fixture).
- **Closer C task ordering.** The sampler MUST NOT race with
  `_ttl_loop`'s exit path or it may sample a handle mid-teardown.
  Use `_init_complete.is_set()` as the guard, mirroring `enter()`.
- **Slice 8 heavy-run wall-time.** 1 M events at ~10 µs/event = 10 s
  in-process; rotation + gzip adds bounded overhead. Test should run
  in under 60 s; mark as `pytest.mark.slow` so the default `pytest`
  invocation skips it (CI enables it).

## Out of scope (follow-ups only)

The following items from
[`phase-2.5-implementation-report.md`](phase-2.5-implementation-report.md#deferred-items-not-in-slices-1-6-intentional)
remain explicit follow-ups — they were never Phase 2 scope:

- **Deferred E — Real plugin session lifecycle (`plugin.session_*`)**:
  V3 ADR §Follow-ups FU#2. Triggers on a second plugin kind landing
  in `plugins/catalog/`.
- **Deferred G — Stream-bridge code removal**: V3 ADR §Follow-ups
  FU#1. Triggers on K = 5 consecutive clean heavy-run gates passing
  post-Phase 3.
- **Deferred H — Ring-by-lane separation**: V3 ADR §Follow-ups FU#4.
  Triggers only if the Phase 3 overhead gate fails permanently.
- **Plan correction for slice 3 file list** (`occ_runtime_services.py`
  / `changeset_projection.py` → `occ/service.py`): cosmetic doc fix
  for `phase-2.5-remaining-emitters-and-wiring.md`, not a code
  change.
- **Plan correction for §Deliverable 6 normalizer module name**
  (`task_center_runner/audit/sandbox_events.py` →
  `task_center_runner/audit/daemon_event_normalizer.py` +
  `sandbox_events_sink.py`). Slice 1 split the normalizer and sink
  into two files; the `test_daemon_event_writer_module_boundary` lint
  enforces the sole-writer contract against the real file name, so
  Phase 2 acceptance still holds. Cosmetic doc fix for
  `phase-2-emitters-and-puller.md §Deliverable 6`.
- **Phase 3 — report rendering + release gates + default-on rollout**.

When 2.6 merges, Phase 2 is closed. Phase 3 is the only V3 work
remaining.
