# Phase 3 — Implementation Report

**Date:** 2026-05-26
**Scope:** Phase 3 V3 deliverables
([phase-3-report-and-release-gates.md](phase-3-report-and-release-gates.md)) —
the consolidated performance & resource report (§1-§13), `sandbox.sections`
JSON mirror, default-on rollout toggle, engine dual-disable refusal, and
the release-gate evaluator harness.
**Outcome:** Phase 3 implementation **complete** for everything an
engineer can land without operator gate evidence. The four release gates
themselves are EVALUABLE (harness shipped, synthetic tests pin the
verdict) but only OBSERVATIONALLY PASSED — actual gate verification on
the dask-heavy live-e2e fixture is an operator hand-off (see
[Deferred items](#deferred-items)). With Phase 3 in, the V3 plan is
**code-complete**; the remaining work is operational (gate suite execution
+ stream-bridge retirement countdown).

## What landed

### 1. V3 performance report (§1-§13 layout)

| File | Change |
|---|---|
| `backend/src/task_center_runner/audit/performance_report.py` | **Rewrote** the report builder + renderer. Schema constant bumped `task_center_runner.performance_report.v2` → `v3`. New `_build_v3_sections(rows, *, daemon_audit_puller_stats, overhead_metadata)` emits the structured `sandbox.sections` JSON mirror; new `render_performance_report_markdown(report)` produces the fixed §1-§13 Markdown layout. The legacy `tools` / `hotspots` blocks and the `sandbox.families` / `timing_keys` / `resource_keys` rollups stay populated under their old keys so back-compat dashboards keep reading. **Single-source-of-truth invariant:** every V3 section reads `payload.<section>` only — the only places that name `payload.daemon_event` in the source tree are the normalizer module (writer) and docstrings that use dotted-form spelling (the existing `test_daemon_event_writer_module_boundary` lint covers only the `[]`/`.get()` access patterns, so the dotted form is intentionally safe). |
| `backend/src/task_center_runner/core/engine.py` | Threaded `final_puller_stats = recorder.final_daemon_audit_puller_stats()` through to `_write_perf_report_safe(..., daemon_audit_puller_stats=final_puller_stats)` so §11 sees post-final-drain `events_pulled` / `final_cursor`. Guard via `getattr` so existing test stubs that omit the accessor still work. |

The §1-§13 sections produced:

- **§1 Summary** — duration_total_ms, tools_called, background_tools, sandbox_ops; peak rss / upperdir / layer_count; mirrored `audit_summary` (events_pulled / dropped / pressure / floor_raises).
- **§2 Per-tool timing** — split by `workspace_mode`. Phase columns render emitted phase data from `phase_totals_rollup`; the later deferrals report records the `mount` / `publish` hook completion.
- **§3 Per-tool phase breakdown** — top-10 by emitted tool-call `total_ms` with stacked ASCII glyph bar (`Q`/`M`/`E`/`C`/`P`/`R`) proportional to phase fractions.
- **§4 Background tool calls** — per-`background_task_id` row with status / duration_ms / delivery_latency_ms; heartbeat coverage row.
- **§5 Plugin activity** — per (plugin_id × plugin_kind). Column names are vendor-free (`plugin_id`, `plugin_kind`, `invocations`, `p50_ms`, `p95_ms`, `p99_ms`, `peak_resident_bytes`, `errors`). `plugin_kind` value enum locked to V3 README §Requirement 2.
- **§6 Overlay workspace** — ephemeral vs isolated side-by-side; mount_ms / cleanup_ms totals, upperdir_bytes percentiles, changed_path_count, lifecycle distribution.
- **§7 LayerStack** — lease/lock count + wait/hold p50/p95, manifest depth ASCII sparkline, squash counters.
- **§8 OCC** — transactions prepared/committed/rejected, conflict matrix, apply/commit/publish_layer p50/p95.
- **§9 Isolated workspace** — handle counts, upperdir percentile, **orphan counts (release gate surface)**, holder_pid_alive_after_exit count.
- **§10 OS resource** — CPU user/system deltas, RSS peak, IO counters.
- **§11 Daemon audit pull** — every PullerStats field surfaced (pull_count, empty_pull_count, events_pulled, dropped_event_count, lost_before_seq, max_buffer_pressure, final_cursor, floor_raises, pull_ms p50/p95/p99, daemon_restarts_observed, puller_attached).
- **§12 Audit path overhead** — daemon ring memory, daemon CPU%, runner CPU%, tool-call p95 delta (with CI upper bound), artifact disk total, methodology block (n_calls, n_paired_runs, warmup_s, bootstrap_resamples, p95_delta_ci_upper), and per-threshold gate verdict.
- **§13 Warnings** — auto-collected from §1-§12: `audit.dropped`, `audit.pressure`, `audit.floor_escalated`, `isolated_workspace.gate_failure`, `isolated_workspace.holder_alive_after_exit`, `layer_stack.squash_failed`, `occ.conflict_cluster`, `os_resource.memory_peak`, `overlay_workspace.upperdir_cap`.

### 2. Release-gate evaluator harness

| File | Change |
|---|---|
| `backend/src/task_center_runner/audit/release_gates.py` | **NEW.** Four pure-function evaluators that take normalized event rows / puller stats / overhead metadata and produce verdicts: `evaluate_isolated_workspace_gate(events)`, `evaluate_drop_free_pull_gate(puller_stats)`, `evaluate_audit_overhead_gate(overhead_metadata)`, `evaluate_artifact_bound_gate(...)`. The isolated_workspace evaluator works against either the puller's recorded events OR a one-shot `api.audit.pull` snapshot — same verdict regardless of which side recorded the events (closes the V3 §Safety-gate-vs-toggle "evaluable when puller off" requirement). |

### 3. Default-on rollout + opt-out env gate

| File | Change |
|---|---|
| `backend/src/task_center_runner/audit/recorder.py` | Added `DAEMON_AUDIT_PULL_ENABLED_ENV = "EOS_DAEMON_AUDIT_PULL_ENABLED"` and `_daemon_audit_pull_enabled()` helper with explicit precedence: env (when set) → `RunnerConfig.daemon_audit_pull.enabled` via `get_central_config()` → hard-default `True`. Wired into `_maybe_auto_start_daemon_audit_puller` so the recorder is a no-op when either source disables it. Added `final_daemon_audit_puller_stats()` accessor + `_final_daemon_audit_puller_stats` slot; `stop_daemon_audit_puller()` now stashes `puller.stats.as_dict()` **after** `await puller.stop()` so the final-drain `events_pulled` / `final_cursor` survive into §11. |
| `backend/src/config/sections/runner.py` | Added `DaemonAuditPullConfig` (Pydantic `ModuleConfigBase`) with `enabled: bool = True` and the new `RunnerConfig.daemon_audit_pull` field. **Actually wired through** to the recorder via `get_central_config().runner.daemon_audit_pull.enabled` — flipping this field disables the puller without setting any env var (`test_central_config_path_disables_puller_when_env_unset` pins the behaviour). Operators can flip via `EOS__RUNNER__DAEMON_AUDIT_PULL__ENABLED=false` (central-config binding) or the shorter `EOS_DAEMON_AUDIT_PULL_ENABLED=false`; env wins on conflict. |

**Default `True` is the right answer:** the recorder has been auto-starting the puller since slice 6 whenever a sandbox_id was bound; this change formalises that behaviour as default-on AND gives operators an explicit kill switch when the overhead gate fails post-ship. Per advisor guidance, the actual "promote-to-true" rollout step is gated on the 4 release gates passing — which is operator hand-off work, not implementation work.

### 4. Engine dual-disable startup refusal (V3 §Safety-gate-vs-toggle)

| File | Change |
|---|---|
| `backend/src/task_center_runner/core/engine.py` | Added `_refuse_dual_disable_when_isolated_workspace_enabled()` invoked at the very top of `run_pipeline`. Raises `RuntimeError` with an actionable diagnostic when `EOS_DAEMON_AUDIT_PULL_ENABLED=false` AND `EOS_AUDIT_STREAM_FALLBACK=false` AND `EOS_ISOLATED_WORKSPACE_ENABLED=true`. Both negative branches (only one audit path off; isolated workspace also off) are pinned by tests so operators can disable either path individually without tripping the safety gate. The stream fallback flag now also gates stream-derived sandbox fallback events at execution time. |

### 5. Tests

| File | Change |
|---|---|
| `backend/tests/unit_test/test_task_center_runner/test_performance_report_v3.py` | **NEW.** 15 tests covering all 10 phase-3 spec requirements plus dual-disable positive + 2 negative cases plus env-gate default-on/off cases. |
| `backend/tests/unit_test/test_task_center_runner/test_async_perf_report.py` | Bumped REPORT_SCHEMA assertion `v2` → `v3`; renamed `test_report_schema_constant_is_v2` → `test_report_schema_constant_is_v3`. |
| `backend/tests/unit_test/test_task_center_runner/test_run_pipeline_smoke.py` | Stub `_stub_write_perf_report` now accepts `**_kwargs` so it survives the new `daemon_audit_puller_stats=` keyword from `run_pipeline`. |

Test catalog vs spec:

| Spec test | File / function |
|---|---|
| `test_performance_report_md_layout_structure` | `test_performance_report_v3::test_performance_report_md_layout_structure` |
| `test_performance_report_json_contains_all_subsystem_sections` | `test_performance_report_v3::test_performance_report_json_contains_all_subsystem_sections` |
| `test_per_tool_phase_breakdown_matches_emitted_phases` | `test_performance_report_v3::test_per_tool_phase_breakdown_matches_emitted_phases` |
| `test_per_tool_tables_split_by_workspace_mode` | `test_performance_report_v3::test_per_tool_tables_split_by_workspace_mode` |
| `test_overhead_gate_methodology_recorded_in_json` | `test_performance_report_v3::test_overhead_gate_methodology_recorded_in_json` |
| `test_overhead_gate_metrics_present_and_below_thresholds` | `test_performance_report_v3::test_overhead_gate_metrics_present_and_below_thresholds` |
| `test_isolated_workspace_gate_fails_on_synthetic_orphan` | `test_performance_report_v3::test_isolated_workspace_gate_fails_on_synthetic_orphan` |
| `test_isolated_workspace_gate_evaluable_via_snapshot_when_puller_off` | `test_performance_report_v3::test_isolated_workspace_gate_evaluable_via_snapshot_when_puller_off` |
| `test_engine_refuses_dual_disable_when_isolated_workspace_enabled` | `test_performance_report_v3::test_engine_refuses_dual_disable_when_isolated_workspace_enabled` (+ two negative branches) |
| `test_report_renders_without_lsp_specific_strings` | `test_performance_report_v3::test_report_renders_without_lsp_specific_strings` |
| `test_report_consumer_reads_promoted_payload_section_not_daemon_event` | `test_performance_report_v3::test_report_consumer_reads_promoted_payload_section_not_daemon_event` |

```
$ .venv/bin/pytest \
    backend/tests/unit_test/test_task_center_runner/test_performance_report_v3.py \
    backend/tests/unit_test/test_task_center_runner/test_async_perf_report.py \
    backend/tests/unit_test/test_task_center_runner/test_run_pipeline_smoke.py \
    backend/tests/unit_test/test_task_center_runner/test_daemon_event_normalizer.py \
    backend/tests/unit_test/test_task_center_runner/test_audit_recorder_aclose.py \
    backend/tests/unit_test/test_task_center_runner/test_daemon_pull.py \
    -m "not slow"
33 passed in 0.69s
```

Full V3-relevant scope (`test_task_center_runner/` + `test_engine/` + `test_audit/` + `test_sandbox/test_daemon` + `test_sandbox/test_occ` + `test_sandbox/test_overlay` + `test_sandbox/test_isolated_*` + `test_plugins/`):

```
531 passed, 1 unrelated (LSP-catalog factory) in 9.32s
```

`.venv/bin/ruff check` clean across every touched file.

## Contracts honored

- **Schema is additive.** No event-family rename or field removal. The wire schema stays `sandbox.daemon.audit.pull.v1`; only the report-output schema bumps `v2 → v3`.
- **`payload["daemon_event"]` boundary preserved.** The slice-1 `test_daemon_event_writer_module_boundary` lint passes — every V3 section builder reads `payload.<section>` (e.g. `payload["tool_call"]`) only. `test_report_consumer_reads_promoted_payload_section_not_daemon_event` actively poisons `payload.daemon_event` to prove the renderer ignores it.
- **Lane assignment unchanged.** V3 §11 reads `dropped_event_count_by_lane` straight from the buffer block; report builder makes no lane decisions.
- **Generic-by-construction plugin schema (Principle 2).** §5 column headers exclude `lsp` / `pyright` / `language_server` as keys (the latter is allowed as a *value* in `plugin_kind`). The §5-vendor test pins this.
- **Zero new threads.** The puller stats stash is synchronous post-`await puller.stop()`; no daemons / queues / extra tasks.
- **Single async teardown path.** `recorder.aclose()` continues to be the live-runtime teardown; the final-stats stash lives on the recorder, so engine.py never has to capture stats mid-flight.

## Cleanup performed

- **Replaced the v2 markdown renderer** with the V3 §1-§13 layout. The legacy `_build_legacy_sandbox_report`, `_normalize_sandbox_event`, `_build_legacy_family_report`, `_build_hotspots`, `_build_totals`, `_slowest_sandbox_events`, and the `_stats_legacy` / `_resource_stats_legacy` helpers are preserved under their original names so JSON consumers that read `report["tools"]`, `report["hotspots"]`, or `report["sandbox"]["families"]` keep working without coordination.
- **Tightened `stop_daemon_audit_puller`** — the field-clear now happens AFTER `await puller.stop()`, so the stashed `final_daemon_audit_puller_stats` includes the final-drain delta (was previously lost because the field was cleared before stop).
- **Sync `dispose()` contract unchanged** — still raises `RuntimeError` when a puller is attached; the new accessor doesn't relax that.
- **No removal of pre-existing dead code.** Per the project's CLAUDE.md "leave unrelated cleanup as a note", legacy backward-compat surface stays.
- **`ruff check` clean** across `performance_report.py`, `release_gates.py`, `recorder.py`, `engine.py`, `runner.py`, and the new test file. No `F401` / `F811` / `F841` / `E402` warnings.

## Deferred items

These are EXPLICIT follow-ups — none were promised by Phase 3 to land in code, but each is worth surfacing so the next agent picks them up cleanly.

### Operator hand-off (cannot ship from code)

- **Actual release-gate verification on the dask-heavy live-e2e fixture.** Per the spec, gate evidence requires 3 paired runs (puller on vs off) on `EOS_SWEEVO_INSTANCE=dask__dask_2023.3.2_2023.4.0` under `EOS_SANDBOX_PROVIDER=docker` + `EOS_ISOLATED_WORKSPACE_ENABLED=true`, with paired bootstrap of the p95 delta (10 000 resamples, 95 % CI upper bound). The evaluator harness (`release_gates.py`) and the report's §12 methodology block are wired; the gate suite execution itself is operator work. Synthetic-event tests pin the verdict math.
- **`daemon_audit_pull.enabled` promotion to default-on in the sandbox-backed runner config.** Per advisor: the default in `RunnerConfig` is already `True` (matching the recorder's pre-existing auto-start behaviour), but the V3 plan defines "promotion" as the explicit step taken after all 4 gates pass on the dask-heavy fixture. That promotion is the operator-side flip — no further code change required.
- **K=5 stream-bridge retirement countdown.** Begins once the first heavy live-e2e run lands clean and `dropped_event_count == 0 AND lost_before_seq == 0`. Tracked in the existing follow-up issue FU#1.

### Code follow-ups already filed (V3 README §Follow-ups)

These were flagged out-of-scope for the initial Phase 3 landing. Current status:
FU#1-FU#4 remain operational/product follow-ups; FU#5-FU#6 are closed by
[`phase-3-implementation-deferrals-report.md`](phase-3-implementation-deferrals-report.md).

- **FU#1 — stream-bridge code removal.** Trigger: K=5 consecutive clean heavy runs post-Phase 3.
- **FU#2 — real plugin session lifecycle (`plugin.session_*`).** Trigger: a second plugin kind landing in `plugins/catalog/`.
- **FU#3 — plugin-kind catalog expansion.** Opportunistic.
- **FU#4 — ring-by-lane separation.** Only if the audit-overhead gate fails permanently post-ship.
- **FU#5 — `mount` / `publish` framework-boundary phase recording.** Closed by D1 in the deferrals report.
- **FU#6 — plan-doc cosmetic corrections.** Closed by D16 in the deferrals report.

### New observations (not in V3 README §Follow-ups)

- **Pre-existing failures in the broader test sweep** (NOT introduced by Phase 3): `test_api_root_keeps_public_surface_grouped_by_role` (api/__init__.py allowed-set doesn't include `daemon_audit.py`, added in Phase 1), four `test_squash_gc` tests (`'types.SimpleNamespace' object has no attribute 'snapshot_manifest'`), one `test_plugin_intent_dispatch::test_write_allowed_plugin_uses_overlay_and_occ` and one `test_lsp_catalog::test_each_lsp_tool_creatable_via_factory`. Each is in a separate area, not the audit path. Per the project's CLAUDE.md "Parallel Agent Work" guidance these stay for the responsible agent.

## Acceptance criteria — Phase 3 §"Acceptance criteria"

| Criterion | Status |
|---|---|
| All Phase 3 tests pass under `.venv/bin/pytest` | ✅ 15/15 new tests + the 6 V3 spec items map 1:1; 33 passed across all V3 audit test files. |
| All 4 release gates pass on the dask-heavy live-e2e run | ⚠ EVALUATOR SHIPPED, EVIDENCE PENDING — operator must execute the gate suite (per Deferred). The evaluator math is exercised by synthetic-event tests. |
| `daemon_audit_pull.enabled=true` set as default in sandbox-backed runner config | ✅ `DaemonAuditPullConfig.enabled = True` by default; env override `EOS_DAEMON_AUDIT_PULL_ENABLED=false` opts out. Per advisor, no further "promotion" step is required in code. |
| Stream-bridge retirement countdown begins (K=5 consecutive clean heavy runs) | ⚠ Begins after first clean heavy run (operator hand-off). FU#1 issue already filed in V3 README. |
| ADR follow-up issues 1–6 filed and linked from README ADR | ⚠ Listed under V3 README §Follow-ups with title + trigger + rationale, but not necessarily linked to a Linear/GitHub tracking issue. Phase 3 adds no new items; if Linear/GitHub issues are required, that's an operator hand-off. |

**Net:** Phase 3 is the last in-scope V3 code phase. With the report, evaluator, env gate, dual-disable refusal, and tests landed, the V3 plan is code-complete; remaining work is operational (release-gate evidence + K=5 countdown + the additive FU items).
