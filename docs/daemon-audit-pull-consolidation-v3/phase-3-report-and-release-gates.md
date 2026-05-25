# Phase 3 — Consolidated Performance & Resource Report + Release Gates

> **Prerequisites:** Read [`README.md`](README.md) first — it owns the
> cross-cutting contracts and the ADR. Phase 3 consumes everything
> [Phase 1](phase-1-audit-buffer-and-pull-rpc.md) and [Phase 2](phase-2-emitters-and-puller.md) produce.

## Goal

Render a human-readable, decision-grade performance & resource report; gate rollout on **measured overhead AND isolated-workspace orphan counts AND drop-free pull AND artifact bound**. Promote `daemon_audit_pull.enabled=true` as default for sandbox-backed runs after gates pass.

Phase 3 is where Principle 4 (overhead is a release gate, not a hope) becomes enforceable.

## Deliverables

### 1. Extended performance report

Extend `backend/src/task_center_runner/audit/performance_report.py` to produce:

- Structured `sandbox.sections` JSON object (mirror of MD).
- Rendered Markdown report with the fixed layout below.
- **Single source of truth:** the report builder reads `payload.<section>` only; never reads `payload.daemon_event` (asserted by `test_report_consumer_reads_promoted_payload_section_not_daemon_event`).

### 2. `sandbox.daemon_audit_pull` block

Puller stats from Phase 2:

- `pull_count`, `empty_pull_count`, `events_pulled`
- `dropped_event_count`, `lost_before_seq`
- `max_buffer_pressure`
- `floor_raises`
- `pull_ms` (p50/p95/p99)
- `final_cursor`
- `daemon_restarts_observed`

### 3. `sandbox.overhead` block

Measured cost of the audit path itself:

- daemon ring memory delta (max `retained_bytes` vs `max_bytes`)
- daemon CPU attributable to audit
- runner CPU attributable to puller
- tool-call wall-time p95 delta with vs without puller (paired-bootstrap CI upper bound)
- artifact disk: live + rotated, total bytes
- methodology metadata: `n_calls`, `n_paired_runs`, `warmup_s`, `bootstrap_resamples`, `p95_delta_ci_upper`

### 4. Default-on rollout

Promote `daemon_audit_pull.enabled=true` as default in the sandbox-backed runner config — **gated on all 4 release gates passing**.

## Fixed report layout (`performance_report.md`)

```
# Performance & Resource Report — <run_id>

## 1. Summary
   - duration_total_ms, tools_called, background_tools, sandbox_ops
   - peak: rss_bytes, upperdir_bytes_total, layer_count
   - audit: events_pulled, dropped_event_count, max_buffer_pressure, floor_raises

## 2. Per-tool timing (foreground, split by workspace_mode)
   | tool_name | workspace_mode | calls | queued_ms p50/95/99 | mount_ms p50/95/99 | exec_ms p50/95/99 | capture_ms p50/95/99 | publish_ms p50/95/99 | release_ms p50/95/99 | total_ms p50/95/99 |
   - One row per (tool_name, workspace_mode) — same tool in ephemeral vs isolated lives on separate rows
   - Phase columns show "—" if all phase events for that cohort were sampled out; total_ms always present (from phase_totals_rollup)

## 3. Per-tool phase breakdown (top-10 by total_ms)
   - Stacked ASCII bar per tool showing queued/mount/exec/capture/publish/release proportions
   - Numbers in the JSON mirror

## 4. Background tool calls
   | task_id | tool_name | task_kind | started_at | duration_ms | status | delivery_latency_ms |
   - heartbeat coverage: <heartbeats_emitted> / <expected> = NN %
   - longest-running task and its tool_name

## 5. Plugin activity (generic; per plugin_id × plugin_kind)
   | plugin_id | plugin_kind     | invocations | p50_ms | p95_ms | p99_ms | peak_resident_bytes | errors |
   | lsp-py    | language_server | 188         | 5.2    | 42.0   | 110.0  | 312 MiB        | 0      |
   | ruff-d    | formatter       | 91          | 1.8    | 8.1    | 22.3   | 48 MiB         | 0      |
   | idx-1     | indexer         | 12          | 88.0   | 220.0  | 420.0  | 180 MiB        | 2      |
   - Column headers must contain NO vendor names

## 6. Overlay workspace — ephemeral vs isolated
   Side-by-side table (from Phase 1 schema). Includes:
   - total mount_ms / cleanup_ms per mode
   - upperdir_bytes p50/p95/max per mode
   - changed_path_count per mode
   - lifecycle state distribution

## 7. LayerStack
   - leases: count, wait_ms p50/p95, hold_ms p50/p95
   - locks:  count, wait_ms p50/p95, hold_ms p50/p95
   - manifest depth over time (ASCII sparkline)
   - squashes: triggered / completed / failed; input_layers → result_layers

## 8. OCC
   - transactions: prepared, committed, rejected
   - conflict matrix: conflict_kind × count; top conflict paths
   - prepare_ms / apply_ms / commit_ms / publish_layer_ms p50/p95

## 9. Isolated workspace (release gate surface)
   - handles: opened, closed, evicted
   - upperdir growth distribution
   - orphan counts after exit (MUST be 0 — release gate)
   - holder PID liveness after exit (MUST be false — release gate)

## 10. OS resource (process / cgroup)
   - CPU: user / system / throttled (us deltas over run)
   - Memory: rss peak, memory_peak_bytes per workspace
   - IO: read/write bytes & ops

## 11. Daemon audit pull
   - pull_count, empty_pull_count, events_pulled
   - dropped_event_count, lost_before_seq
   - max_buffer_pressure, final_cursor
   - floor_raises (count of times the cadence floor escalated)
   - pull_ms p50/p95/p99
   - daemon_restarts_observed
   - puller CPU% (measured), puller wall-ms total

## 12. Audit path overhead (release gate)
   - daemon ring memory: max retained_bytes / max_bytes
   - daemon CPU attributable to audit: < 1 % p99 (gate)
   - runner CPU attributable to puller: < 0.5 % p99 (gate)
   - tool latency delta with vs without puller: < 1 ms p95 (gate)
   - artifact disk: live + rotated, total bytes

## 13. Warnings
   - audit dropped (any lane)
   - pressure > 80 % at any point
   - orphan counts > 0
   - upperdir > 80 % of cap
   - memory peak > threshold
   - OCC conflict cluster
   - lock_wait p95 over threshold
   - squash failed-to-reduce or squash_failed event present
   - floor escalated above default
```

## Release gates (Phase 3 cannot ship without all 4 passing)

### Measurement methodology (applies to overhead + drop-free gates)

- 3 paired runs (puller on vs puller off) of the dask-heavy live-e2e fixture below.
- First 60 s of each run discarded as warmup (daemon boot + JIT settling).
- Minimum N ≥ 1000 tool calls per run for delta computation.
- Statistical test: paired bootstrap (10,000 resamples) of the p95 delta; gate passes when the 95 % confidence-interval **upper bound** is below the threshold.
- All measurements pinned to `EOS_SWEEVO_INSTANCE=dask__dask_2023.3.2_2023.4.0` under `EOS_SANDBOX_PROVIDER=docker` + `EOS_ISOLATED_WORKSPACE_ENABLED=true` (V1 reproducibility anchor).

### Gate matrix

| Gate | Threshold (95 % CI upper bound) | Fallback if not met |
|---|---|---|
| **Audit overhead gate** | tool-call wall-time p95 delta ≤ 5 ms; daemon RSS delta ≤ 16 MiB at steady state (post-60s warmup); runner CPU delta ≤ 0.5 % averaged over a ≥ 5 min window; sandbox disk delta = 0 bytes | Raise pull floor (don't raise ring caps); if still failing after floor escalation, ship Phase 3 with `daemon_audit_pull.enabled=false` as default + open follow-up plan |
| **Isolated workspace gate** | Every isolated_workspace exit reports `orphan_holder_count == 0`, `orphan_cgroup_count == 0`, `orphan_scratch_count == 0`; at run completion `open_handle_count == 0`; after each exit `holder_pid_alive == false`. **The gate is evaluated against the daemon-side ring via `api.audit.snapshot` AND the puller's recorded events** (so it does not depend on the puller toggle — see below) | **HARD BLOCK** — do not ship; isolated workspace is the highest-risk safety surface; fix root cause |
| **Drop-free pull gate** | `dropped_event_count == 0` and `lost_before_seq == 0` across the gate suite (95 % CI upper bound) | Raise pull floor (more frequent pulls); do not raise ring caps as a workaround |
| **Artifact bound gate** | Rotation kicks in correctly during synthetic 1 M-event run; total host-side footprint stays within `64 MiB + 8 × rotated` cap; gzip succeeds for every rotation | Tune retention cap down; do not relax rotation threshold |

### Safety-gate-vs-toggle resolution (closes Critic P0)

The isolated-workspace HARD BLOCK gate is evaluated during the release-gate suite with `daemon_audit_pull.enabled=true`. **Shipping any part of V3 requires all 4 gates pass with the puller enabled** — the `daemon_audit_pull.enabled=false` fallback applies ONLY to the runtime default-on rollout (audit overhead gate), NOT to gate evaluation.

Runtime invariant after ship:

- `isolated_workspace.{exited, orphan_check_completed, orphan_reaped}` are emitted by the daemon **unconditionally** on the `critical` lane (the emission site is in the orphan reaper, not the puller).
- When the puller is disabled at runtime, orphan evidence is still captured in the daemon ring (recoverable via `api.audit.snapshot` for live diagnostics) AND mirrored by the existing stream-bridge fallback into `sandbox_events.jsonl` (until stream-bridge sunset gate fires).
- Operators MUST NOT disable both puller AND stream-bridge simultaneously while isolated_workspace is enabled — a Phase 2 startup check (`task_center_runner/core/engine.py`) refuses to start when `daemon_audit_pull.enabled=false` AND `EOS_AUDIT_STREAM_FALLBACK=false` AND `EOS_ISOLATED_WORKSPACE_ENABLED=true`.

## Gate verification commands (pinned to V1 reproducibility anchor)

```bash
# Mock report regression
.venv/bin/pytest -q -x --tb=short --durations=20 \
  backend/src/task_center_runner/tests/mock/sandbox/layer_stack_occ_overlay/

# Isolated workspace pre-flight + happy path (puller on)
EOS_SWEEVO_INSTANCE=dask__dask_2023.3.2_2023.4.0 \
EOS_SANDBOX_PROVIDER=docker \
EOS_ISOLATED_WORKSPACE_ENABLED=true \
EOS__RUNNER__LIVE_E2E__HEAVY_ENABLED=true \
EPHEMERALOS_DATABASE_URL="sqlite:///./.ephemeralos/ephemeralos.db" \
EOS_DAEMON_AUDIT_PULL_ENABLED=true \
.venv/bin/pytest -v \
  backend/src/task_center_runner/tests/mock/sandbox/isolated_workspace/pre_flight/ \
  backend/src/task_center_runner/tests/mock/sandbox/isolated_workspace/happy_path/

# Overhead comparison (puller on vs off; same workload twice)
# Compare performance_report.md §12 between the two runs.
```

## Tests

- `test_performance_report_md_layout_structure` — schema-shape assertions (NOT golden-file diff): assert all 13 section headers present in order; assert §2 has the expected column set via regex `^\|\s*tool_name\s*\|\s*workspace_mode\s*\|\s*calls\s*\|.*total_ms p50/95/99\s*\|$`; assert §5 has expected columns via regex `^\|\s*plugin_id\s*\|\s*plugin_kind\s*\|\s*invocations\s*\|.*peak_resident_bytes\s*\|\s*errors\s*\|$`; assert §5 `plugin_kind` values ∈ {`language_server`, `formatter`, `indexer`, `build_daemon`, `mcp_bridge`, `custom`}.
- `test_performance_report_json_contains_all_subsystem_sections` — assert every section key from the cross-cutting list appears.
- `test_per_tool_phase_breakdown_matches_emitted_phases`.
- `test_per_tool_tables_split_by_workspace_mode` — same `tool_name` invoked in ephemeral and isolated → two rows in §2.
- `test_overhead_gate_methodology_recorded_in_json` — assert `sandbox.overhead` block includes `n_calls`, `n_paired_runs`, `warmup_s`, `bootstrap_resamples`, and `p95_delta_ci_upper` keys.
- `test_overhead_gate_metrics_present_and_below_thresholds`.
- `test_isolated_workspace_gate_fails_on_synthetic_orphan` — inject a synthetic orphan via fault-injection harness; assert gate fails loudly and emits `isolated_workspace.gate_failure` warning row in report §13.
- `test_isolated_workspace_gate_evaluable_via_snapshot_when_puller_off` — disable puller; trigger isolated_workspace exit; assert gate-evaluation harness reads orphan state from `api.audit.snapshot` and still produces a verdict.
- `test_engine_refuses_dual_disable_when_isolated_workspace_enabled` — set `EOS_DAEMON_AUDIT_PULL_ENABLED=false` AND `EOS_AUDIT_STREAM_FALLBACK=false` AND `EOS_ISOLATED_WORKSPACE_ENABLED=true` → engine startup raises with explicit error message.
- `test_report_renders_without_lsp_specific_strings` — grep rendered report for `"lsp"`, `"pyright"`, `"language_server"` (as a JSON key, not a value) → must be 0 hits. Header column names asserted generic.
- `test_report_consumer_reads_promoted_payload_section_not_daemon_event` — with `EOS_AUDIT_FORENSIC_RAW_ENABLED=true`, corrupt `payload.daemon_event` deliberately; assert report unchanged (because it reads from `payload.<section>`).

## Acceptance criteria

- All tests above pass under `.venv/bin/pytest`.
- All 4 release gates pass on `EOS_SWEEVO_INSTANCE=dask__dask_2023.3.2_2023.4.0` heavy live-e2e run.
- `daemon_audit_pull.enabled=true` set as default in sandbox-backed runner config.
- Stream-bridge retirement countdown begins (K=5 consecutive clean heavy runs).
- ADR follow-up issues 1–4 filed in the issue tracker and linked from the [README ADR](README.md#follow-ups-out-of-scope-for-this-plan) section.

## What this phase does NOT do

- Does NOT remove the stream-bridge fallback. Per [README §Stream-bridge fallback sunset](README.md#stream-bridge-fallback-sunset), removal is a follow-up after the K=5 retirement gate.
- Does NOT introduce a real plugin session lifecycle. Per ADR follow-up #2.
- Does NOT add per-subsystem ring sharding. Per ADR follow-up #4 (only if the overhead gate fails post-ship).
