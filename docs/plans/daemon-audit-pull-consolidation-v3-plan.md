# Daemon Audit Pull Consolidation — V3 Plan

> **Lineage:** V1 = `daemon-audit-pull-consolidation-implementation-plan.md` (8 phases, original).
> V2 = `daemon-audit-pull-consolidation-review-and-v2-plan.md` (3 phases, consolidated; V1 review + rewrite).
> V3 (this document) = V2 + closure of 10 residual gaps; ≤3 phases.

> **Revision history:**
> - **V3.0** — initial draft.
> - **V3.1** — applied iteration-1 consensus-loop fixes from Architect (steelman + 4 named adjustments) and Critic (ITERATE verdict with P0/P1/P2 issues):
>   - **P0 fixes:** corrected wrong file path (`isolated_workspace/manager.py` → `_control_plane/{pipeline_registry,pipeline_state,orphan_reaper,workspace_handle_lifecycle,linux_runtime}.py`); release-gate methodology now specifies warmup + N≥1000 + paired-bootstrap 95 % CI; resolved safety-gate-vs-toggle ambiguity (gate evaluated with puller on; runtime invariant added; engine refuses dual-disable when isolated_workspace is on).
>   - **P1 fixes:** replaced `tool_call.phase` 1-in-N sampling with slow-tail buffered flush (Principle 3 upheld for outliers); `payload.daemon_event` now env-gated (`EOS_AUDIT_FORENSIC_RAW_ENABLED`, default off) + module boundary + CI lint test; Phase 1 acceptance now includes full causal-chain smoke; `background_tool.heartbeat` carries `background_task_id`; §5 plugin-table test switched from golden-file to schema-shape assertions.
>   - **P2 fixes:** added pre-merge requirement to file follow-up tracking issues; renamed `peak_rss_*` → `peak_resident_*` (multi-process futureproof); added `retained_bytes`/`retained_events`/per-lane drop counters explicitly; added Pull RPC trust model + daemon-restart epoch handling sections.
> - **V3.2 (iteration-2 polish)** — Architect re-review verdict: PROCEED; Critic re-review verdict: APPROVE (consensus reached). 3 micro-corrections applied: (a) per-`tool_name` lock semantics for dispatcher rolling-window; (b) `daemon.restart_observed` added to lane-assignment table (critical lane); (c) stale traceability-row label corrected (golden-file → schema-shape).

---

## RALPLAN-DR Summary

### Principles
1. **Pull-only audit; bounded daemon ring; single canonical artifact.** Daemon never writes audit to disk. Pull RPC is O(returned events), never O(retained).
2. **One schema with subsystem section keys; generic by construction.** No section key contains a vendor or technology name. `plugin_kind` is a value, never a key.
3. **Causal chain over flat events.** Every write transaction carries `operation_id` + `lease_id` + `changeset_id` so the report reconstructs `lease → lock → changeset → commit → publish → release` without manual joins.
4. **Overhead is a release gate, not a hope. Disk is bounded at both ends.** Sandbox-side zero-write; host-side rotated+gzipped+retention-capped.

### Decision Drivers (top 3)
1. **Production safety of isolated-workspace exit.** Orphan holder PIDs / cgroups / scratch dirs are data-leak risks and the highest-blast-radius surface in the codebase.
2. **Bounded resource cost.** The audit path itself must not regress sandbox throughput; memory, CPU, and disk are all capped numerically.
3. **Future-proofness for plugins and background tools.** A new plugin kind (formatter daemon, indexer, MCP bridge, …) or a new background tool family must drop in without a schema bump or vendor-named field.

### Viable Options
- **A. V2 as-is (3-phase, dual-write).** Pros: zero delta from current draft; ready to execute. Cons: 10 residual gaps remain implicit (see Part 1); consumer-divergence risk between `payload.daemon_event` and promoted `payload.<section>`; phase-event budget math missing; cadence floor mechanism undefined.
- **B. V3 = V2 + closure of 10 residual gaps (recommended; this plan).** Pros: addresses all 5 user requirements at depth; explicit lane-assignment table; defined cadence floor; defined stream-bridge sunset; integrates with `EOS_TIER_RUN_ID`. Cons: more discipline at review time; lane-assignment + sampling rule are verbose.
- **C. 2-phase compression (ring+RPC+emitters together).** Pros: shorter timeline. Cons: merging ring and emitters loses the smoke-only Phase 1 safety net; ring and emitters are independently testable and should be reviewed separately. **Rejected.**

### Pre-mortem (3 scenarios — deliberate mode)
1. **Overhead gate fails on heavy live-e2e runs.** Tool-call p95 wall-time delta > 1 ms under puller-on vs puller-off comparison. Likelihood: medium. Blast radius: blocks ship of default-on toggle. Mitigation: adaptive cadence floor (`EOS_DAEMON_AUDIT_PULL_FLOOR_MS`) + per-tool phase sampling rule + `daemon_audit_pull.enabled=false` fallback so the rest of the consolidation still ships.
2. **`tool_call.phase` events flood the ring.** A 10 k-call run with 6 phases each = 60 k events > 50 k ring cap → critical lifecycle events evicted → orphan-detection invariants broken. Likelihood: high without explicit budget math. Blast radius: silent loss of safety evidence on long runs. Mitigation: `tool_call.phase` assigned to `sample` lane; **slow-tail buffered flush** at the dispatcher (always flush during cold window of first 100 calls per `tool_name`, then flush only when `total_ms ≥ P95` of rolling-window) — this combines bounded ring cost with full causal-chain preservation for outlier calls (Principle 3 upheld for the slow tail); critical lane reserved for isolated_workspace lifecycle; `tool_call.finished.phase_totals_rollup` always populated from in-process timers so per-tool aggregate stats survive even when phase events are not flushed.
3. **Plugin session emission has no clear emit site.** The current loader (`backend/src/plugins/core/loader.py`) is an import-time singleton (`_LOAD_CACHE: dict[Path, list[BaseTool]]`) with no native per-invocation lifecycle. Likelihood: certain (already true today). Blast radius: design hole — V2's `plugin.session_started/stopped` events would have no real emit point. Mitigation: V3 drops `plugin.session_*` from the v1 schema; emits `plugin.tool_invoked` / `plugin.tool_completed` / `plugin.error` only; defers real plugin session model to a follow-up plan; `plugin.session_*` can be added additively later without a schema bump.

### Expanded Test Plan
- **Unit:** ring eviction priority (critical survives sample-pressure flood); pull cursor exclusive semantics; pressure formula; lane assignment per event family; phase-event budget math; plugin event genericness check (grep for `"lsp"` / `"pyright"` as keys → must be 0).
- **Integration:** puller + emitters end-to-end against a mock daemon; dedupe correctness across stream + pull (pull supersedes when both present); rotation/gzip/retention on a synthetic 100 MiB run; EOS_TIER_RUN_ID artifact-path stability.
- **E2E:** live e2e heavy run with `EOS_SWEEVO_INSTANCE=dask__dask_2023.3.2_2023.4.0` under `EOS_SANDBOX_PROVIDER=docker` + `EOS_ISOLATED_WORKSPACE_ENABLED=true`; puller-on vs puller-off comparison for the overhead gate; isolated-workspace orphan gate at run completion.
- **Observability:** harness snapshots `audit_buffer` stats every 10 s; assert `max_buffer_pressure < 0.8` across full run; assert `orphan_holder_count == orphan_cgroup_count == orphan_scratch_count == 0` at every isolated_workspace exit; assert `dropped_event_count == 0` and `lost_before_seq == 0` end-to-end.

---

## Part 1 — Review of V2

### V2 strengths (kept verbatim in V3)
- 3-phase shape (Ring+RPC / Emitters+Puller / Report+Gates) is the correct cut.
- Causal-chain principle (`lease_id` + `changeset_id` + `operation_id` on every transaction event) is correct and reuses the project's [[project_ephemeralos_layerstack_occ_design]] story.
- Disk contract (zero sandbox writes + host rotation/gzip/retention) is the right model.
- Generic plugin section keyed by `plugin_kind` is the correct shape.
- `BackgroundTaskStatus` lattice reuse + existing 60 s heartbeat reuse (no new threads) is correct.
- Side-by-side ephemeral-vs-isolated workspace property table is excellent.
- Per-tool phase events (`tool_call.phase`) is the right answer to "where did time go".
- Release gates (overhead + isolated-workspace orphan) are well-chosen.

### V2 residual gaps (closed by V3)

| # | Gap in V2 | V3 closure |
|--:|---|---|
| 1 | Schema evolution rule missing — V2 says "Frozen event schema v1" but no bump policy | V3 §Schema contract: additive=v1; rename/remove=v2; consumers reject unknown majors |
| 2 | Dual-write authoritativeness ambiguous — both `payload.daemon_event` and promoted `payload.<section>` exist; who reads which? | V3 §Dual-write authoritativeness: `payload.<section>` is the consumer surface; `payload.daemon_event` is forensic-only |
| 3 | `tool_call.phase` budget math missing — 6 phases × 10 k calls = 60 k events > 50 k ring cap | V3 §Lane assignment (phase events on `sample` lane) + slow-tail buffered flush at the dispatcher (cold window + P95 slow-tail); `tool_call.finished.phase_totals_rollup` always populated; budget estimate ≤ ~32 k events on a 10 k-call run |
| 4 | Plugin session lifecycle mismatch — loader is import-time singleton; `plugin.session_*` events have no emit site | V3 drops `plugin.session_*` from v1 schema; keeps `plugin.tool_invoked/completed/error` only; defers session model to follow-up |
| 5 | Lane assignment table missing — V2 mentions critical/normal/sample but doesn't map event families | V3 §Lane assignment: full table mapping every event family to a lane with rationale |
| 6 | Adaptive cadence floor mechanism missing — V2 says "raise the floor" but doesn't define the floor | V3 defines `EOS_DAEMON_AUDIT_PULL_FLOOR_MS` (default 100 ms) + pressure-based escalation rule |
| 7 | Buffer pressure formula undefined — V2 reports `pressure: 0.91` but never says how it's computed | V3 specifies `pressure = max(retained_bytes/max_bytes, retained_events/max_events)` |
| 8 | Stream-bridge fallback sunset undefined — V1's Decision #6 keeps it indefinitely | V3 defines retirement gate (K=5 consecutive clean heavy runs → flip default); full removal is a follow-up phase out of scope |
| 9 | `EOS_TIER_RUN_ID` integration unstated — referenced in memory but not the plan | V3 §Disk contract: rotated artifacts live under EOS_TIER_RUN_ID-stable paths |
| 10 | Per-mode timing rollup missing — req #3 says "time per tool"; V2 aggregates across modes | V3 §Report layout §2: per-tool tables explicitly split by `workspace_mode` (default / ephemeral / isolated) |

---

## Part 2 — V3 Implementation (3 phases)

### Cross-cutting contracts (apply across all phases)

#### Schema contract
- Schema identifier: `sandbox.daemon.audit.pull.v1`
- **Additive changes** (new field on existing section, new event name, new subsystem section) → stay v1
- **Breaking changes** (rename or remove a field, change a field's semantics) → bump to v2
- Consumers MUST reject unknown major versions explicitly; current consumers reject `v2+` until updated

#### Subsystem section keys (frozen at v1)
`daemon`, `layer_stack`, `overlay_workspace`, `occ`, `isolated_workspace`, `os_resource`, `plugin`, `background_tool`, `tool_call`

#### Dual-write authoritativeness (env-gated forensic raw)
- `payload.<section>` (promoted, structured) = **consumer surface, always written**. Report builder, downstream notebooks, live health checks MUST read from here. This is the only authoritative view.
- `payload.daemon_event` (verbatim raw) = **forensic-only, opt-in**. Written ONLY when `EOS_AUDIT_FORENSIC_RAW_ENABLED=true` (default: `false`). Used for manual audit replay and debugging when the promoted view looks wrong. Operators flip the env var per-run when investigating a specific incident.
- Consumer-divergence enforcement (closes Architect A2 / Critic P1):
  1. Module-boundary: the normalizer (`task_center_runner/audit/sandbox_events.py`) is the only writer of `payload.daemon_event`.
  2. CI lint rule: a repo-level grep job fails CI if any file outside `task_center_runner/audit/sandbox_events.py` or test files references `payload["daemon_event"]` / `payload.get("daemon_event")` / `["daemon_event"]` outside an opt-in test fixture.
  3. Default-off test: `test_no_consumer_reads_daemon_event_under_default_config` runs a full mock suite with `EOS_AUDIT_FORENSIC_RAW_ENABLED` unset and asserts `daemon_event` key absent from every recorded payload.
  4. Negative test: `test_report_consumer_reads_promoted_payload_section_not_daemon_event` corrupts `payload.daemon_event` (with the env enabled); asserts the report is unchanged.

#### Buffer pressure formula and tracked counters

```
pressure = max(retained_bytes / max_bytes, retained_events / max_events)
```

Audit-buffer tracked counters (all reported in every pull response under `buffer`):
- `retained_events` — count of events currently in the ring (across all lanes)
- `retained_bytes` — sum of encoded-size estimate of events currently in the ring
- `max_events`, `max_bytes` — configured caps
- `pressure` — derived from formula above
- `dropped_event_count` — total events evicted since daemon boot
- `dropped_event_count_by_lane` — `{critical: int, normal: int, sample: int}`
- `lost_before_seq` — exclusive lower bound; events with `seq < lost_before_seq` are no longer retrievable

Reported in every pull response under `buffer`. The puller raises its cadence floor when `pressure > 0.8` sustained for 3 consecutive pulls.

#### Lane assignment

Every emitted event belongs to exactly one lane. Eviction priority: `sample` evicted first, then `normal`, then `critical`. Lane assignment is part of the schema (changing a lane is a v2 break).

| Event family | Lane | Rationale |
|---|---|---|
| `daemon.{started,stopped,audit_buffer_pressure}` | critical | self-observability of the audit path |
| `daemon.restart_observed` (synthesized by puller on epoch boundary) | critical | epoch boundary is unconditionally consequential for report correctness |
| `isolated_workspace.{entered,exited,evicted,orphan_check_completed,orphan_reaped}` | critical | exit safety / orphan-detection invariants |
| `overlay_workspace.{mounted,published,cleaned,cleanup_failed}` | critical | lifecycle proof per tool call |
| `layer_stack.{squash_triggered,squash_completed,squash_failed}` | critical | manifest depth invariants |
| `occ.conflict_rejected` | critical | OCC stale-base evidence (debugging concurrent writes) |
| `background_tool.{started,completed,failed,cancelled,delivered}` | normal | terminal-state events for long-running tools |
| `layer_stack.{lease_requested,lease_acquired,lease_released,lock_acquired,snapshot_prepared}` | normal | timing data |
| `occ.{changeset_prepared,transaction_lock_acquired,apply_committed,publish_layer}` | normal | timing data |
| `plugin.{tool_invoked,tool_completed,error}` | normal | plugin observability |
| `tool_call.{started,finished}` | normal | per-tool envelope (always present) |
| `isolated_workspace.sampled` (500 ms cadence) | sample | periodic; tolerable to drop under pressure |
| `os_resource.sampled` (heartbeat cadence) | sample | periodic; tolerable to drop under pressure |
| `background_tool.heartbeat` (60 s cadence) | sample | periodic; tolerable to drop under pressure |
| `plugin.peak_resident_sampled` | sample | periodic |
| `tool_call.phase` | sample (with per-tool sampling rule) | high volume — 6 phases × N calls |

#### Per-tool phase sampling rule (slow-tail buffered flush)

Goal: bounded ring cost AND complete causal chain preserved for the slow tail (Principle 3 upheld for outlier debugging).

Mechanism:
- The dispatcher (`engine/tool_call/dispatch.py`) maintains a thread-local **phase buffer** during each tool call: a fixed-size ring of `{phase, timestamp, duration_ms}` records (max 6 entries — one per phase). Cost: ~96 bytes per in-flight call.
- The dispatcher also maintains a per-`tool_name` rolling-window of the last 100 finished calls' `total_ms` (in-process; ~800 bytes per active tool_name). Each rolling window is protected by a per-`tool_name` lock; contention is acceptable because the critical section is O(1) under a fixed-size deque (append + drop-oldest + P95 lookup via an auxiliary sorted structure).
- On `tool_call.finished`, the dispatcher decides whether to flush the phase buffer to the daemon ring:
  - **Cold window:** if rolling-window has fewer than 100 samples for this `tool_name`, ALWAYS flush (cold-start coverage).
  - **Slow tail:** if `total_ms ≥ P95(rolling-window)`, ALWAYS flush (captures the slowest ~5% of calls).
  - **Otherwise:** discard the phase buffer (the call's aggregate is still captured via `phase_totals_rollup` on `tool_call.finished`).
- All flushed `tool_call.phase` events go on `sample` lane (evicted last under critical-lane pressure).
- Always emit `tool_call.started` and `tool_call.finished` on `normal` lane — envelope is unconditional.
- `tool_call.finished.phase_totals_rollup` is a map `{queued_ms, mount_ms, exec_ms, capture_ms, publish_ms, release_ms}` computed from in-process timers (NOT from emitted phase events). Per-tool aggregate p50/p95/p99 reports are accurate even when phase events are flushed out.

Why slow-tail instead of 1-in-N: the slow tail is exactly where causal-chain reconstruction matters (join against `layer_stack.lock_acquired`, `occ.transaction_lock_acquired`, etc.). 1-in-N would drop the very calls that need investigation. Slow-tail captures the outliers without flooding the ring on hot tools.

Definition of `total_ms` for the gate: wall-clock from `tool_call.started` to `tool_call.finished`, measured via `monotonic_now()`.

Budget estimate (10 k tool-call run, 50 distinct tool_names, 200 calls/tool average):
- Cold window flushes: 50 × 100 = 5,000 calls × 6 phases = 30,000 phase events
- Slow-tail flushes (after warmup): 50 × 100 calls × 5 % × 6 phases = 1,500 phase events
- Total per run: ~31,500 phase events (well within 50,000 ring cap; combined with `normal`+`critical` lanes leaves headroom).

#### Adaptive cadence policy with floor enforcement

Floor: `EOS_DAEMON_AUDIT_PULL_FLOOR_MS` (default 100 ms) — the puller never polls faster than this regardless of pressure or workspace mode.

Pressure-based floor escalation:
- If `pressure > 0.8` sustained for 3 consecutive pulls → raise floor by 50 % (cap at 1000 ms).
- Floor is never auto-lowered. Operators can manually reset via `api.audit.reset_floor` (gated by `EOS_DAEMON_AUDIT_ALLOW_FLOOR_RESET=true`).

Interval table (target intervals are clamped to floor):

| Condition | Target interval | Notes |
|---|---:|---|
| active run, default | 1 s | normal cadence |
| idle (no inflight) | 5 s | background heartbeat dominates |
| isolated workspace active | 500 ms | catch orphan / holder drift fast |
| buffer pressure ≥ 0.8 | 250 ms | drain before eviction |
| final drain (puller stop) | until empty or 3 s cap | bounded teardown |

#### Disk & log persistence contract

- **Sandbox side:** zero disk writes for the audit path. Ring is in-memory only. Upperdir size queries are TTL-cached and bounded; full tree walks are forbidden. Per-sandbox disk usage is whatever the workload writes — audit adds nothing.
- **Host side:** `sandbox_events.jsonl` rotates at 64 MiB. Gzip on rotation. Retention cap: `EOS_AUDIT_ARTIFACT_RETENTION_FILES` (default 8). Worst-case footprint per run ≈ 64 MiB live + 8 × ~10 MiB compressed ≈ 150 MiB. `performance_report.{json,md}` written once post-run, no rotation needed.
- **Artifact stability:** rotated `sandbox_events.jsonl.gz.N` files live under the EOS_TIER_RUN_ID-stable artifact path (per `eos_tier_run_id_artifact_stability` invariant), so `run_tiered.py`'s resume-on-restart contract holds without modification.
- **Retention beyond a single run:** inherits existing run-directory GC; no new retention policy added in this plan.

#### Stream-bridge fallback sunset

V1 Decision #6 keeps stream-derived sandbox events as a fallback alongside daemon-pulled events; V2 inherits this without a retirement gate. V3:

- Retirement gate: after Phase 3 ships, if `dropped_event_count == 0` AND `lost_before_seq == 0` across **K = 5 consecutive heavy live-e2e runs** (one per week minimum), flip `EOS_AUDIT_STREAM_FALLBACK=false` as default.
- Stream-bridge code removal is a **follow-up phase OUT OF SCOPE** for this plan; a tracking issue MUST be filed before this plan merges (see ADR §Follow-ups).

#### Pull RPC trust model

- `api.audit.pull`, `api.audit.snapshot` — trusted-transport. Daemon and runner share the in-sandbox AF_UNIX socket; no per-call authentication. The transport's filesystem permissions (socket file `0600`, owned by the sandbox user) are the authentication boundary, the same model used by every other daemon RPC.
- `api.audit.reset_floor` — operator escape hatch, NOT a security boundary. Gated by `EOS_DAEMON_AUDIT_ALLOW_FLOOR_RESET=true` env check at handler entry. The env gate exists to prevent test-suite or automation accidents, not to defend against malicious callers (if a caller can reach the AF_UNIX socket, they already have the same trust level as the runner).

#### Daemon-restart epoch handling

The audit ring is in-memory; daemon restart loses all retained events. The puller MUST observe and report this cleanly:

- On daemon boot, the ring assigns a `boot_epoch_id` (e.g., monotonic-clock value at start) and reports it in every pull/snapshot response under `snapshot.daemon.boot_epoch_id`.
- The puller tracks the last-seen `boot_epoch_id` alongside its cursor.
- If the next pull returns a different `boot_epoch_id`, the puller treats this as an epoch boundary: it sets its local cursor to 0, records `boot_epoch_boundary_observed=true` in puller stats, increments `daemon_restarts_observed`, and resumes pulling from the new epoch's seq=0.
- Events from the previous epoch are not re-pulled (they are lost). Report §11 shows `daemon_restarts_observed` so a heavy run with a daemon crash is visible to the reader.

---

### Phase 1 — Audit Buffer, Pull RPC, Schema Contract

**Goal:** Bounded daemon-side ring + pull/snapshot RPCs + frozen schema (v1) covering all subsystem section keys including `plugin`, `background_tool`, and `tool_call`. No emitters wired beyond a minimal smoke set.

#### Deliverables

1. **`backend/src/sandbox/daemon/audit_buffer.py`** — new file.
   - Monotonic `seq` across all lanes.
   - `boot_epoch_id` assigned at construction (monotonic-clock value).
   - `max_events` (default 50,000), `max_bytes` (default 8 MiB).
   - Priority lanes: `critical` / `normal` / `sample`. Eviction order: sample → normal → critical.
   - Pressure formula: `max(retained_bytes/max_bytes, retained_events/max_events)`.
   - Tracked counters (all reported in every pull/snapshot response):
     - `retained_events`, `retained_bytes`
     - `dropped_event_count`, `dropped_event_count_by_lane` (`{critical, normal, sample}`)
     - `lost_before_seq`
     - `pressure` (derived)
   - Critical-lane events survive sample-lane eviction (proven in tests).
   - Public methods: `append(event, lane)`, `pull(after_seq, limit)`, `snapshot()`.

2. **RPC ops** registered in `backend/src/sandbox/daemon/rpc/dispatcher.py` (via `register_op`):
   - `api.audit.pull` — returns `cursor` + `buffer` + `snapshot` + `events`; O(returned events).
   - `api.audit.snapshot` — returns cached gauges only; O(1); must NOT walk large trees.
   - `api.audit.reset_floor` — operator-only; gated by `EOS_DAEMON_AUDIT_ALLOW_FLOOR_RESET=true` environment check.

3. **Wrappers** in `backend/src/sandbox/api/daemon_audit.py`; transport constants in `backend/src/sandbox/api/transport.py`; exports in `backend/src/sandbox/api/__init__.py`.

4. **Frozen schema v1**:
   - `audit_buffer.SCHEMA_VERSION = "sandbox.daemon.audit.pull.v1"` constant.
   - Sibling `audit_schema.py` with subsystem section dataclasses (for typed construction in emitters).
   - Schema doc inline at top of `audit_buffer.py` enumerating all sections, all event families, and lane assignments (single source of truth).

5. **Smoke emitters** (proves the wiring works without committing to full instrumentation):
   - `daemon.started` on daemon boot.
   - `daemon.audit_buffer_pressure` whenever pressure crosses 0.8 threshold.
   - `os_resource.sampled` on the existing sampler tick.

#### State / event / resource tables (Phase 1 schema commitment)

**Overlay workspace — ephemeral vs isolated side-by-side**

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

**LayerStack — lease/lock/squash family**

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

**OCC — changeset transaction family**

```
occ.changeset_prepared        (operation_step=70, changeset_id, changed_path_count)
occ.transaction_lock_acquired (operation_step=90, transaction_lock_wait_ms)
occ.apply_committed           (operation_step=110, apply_ms, commit_ms, committed_layer_id)
occ.publish_layer             (publish_layer_ms, committed_layer_bytes)
occ.conflict_rejected         (conflict_kind, conflict_path, conflict_reason,    [critical]
                               base_manifest_version, current_manifest_version)
```

Conflict events carry both `base_manifest_version` (writer's view) and `current_manifest_version` (daemon's view), matching the [[project_ephemeralos_layerstack_occ_design]] stale-base story.

**Background tool calls — generic, plugin-agnostic**

```
background_tool.started      (background_task_id, task_kind, tool_name, agent_id)     [normal]
background_tool.heartbeat    (background_task_id, uptime_ms, status=RUNNING)          [sample]
background_tool.completed    (background_task_id, exit_code, duration_ms)             [normal]
background_tool.failed       (background_task_id, error_kind, duration_ms)            [normal]
background_tool.cancelled    (background_task_id, cancel_reason, duration_ms)         [normal]
background_tool.delivered    (background_task_id, delivery_latency_ms)                [normal]
```

Mirrors the existing `BackgroundTaskStatus` lattice in `backend/src/engine/background/task_supervisor.py` (`RUNNING → {COMPLETED, FAILED, CANCELLED} → DELIVERED`). Heartbeats reuse the existing `EOS_BACKGROUND_HEARTBEAT_INTERVAL_S` (default 60 s) — **zero new timer threads**. Background tool emission cost is therefore bounded by the existing heartbeat, not added on top.

**Plugin — generic, not LSP-specific**

```
plugin.tool_invoked      (plugin_id, plugin_kind, plugin_version, plugin_tool_name,
                          request_bytes, workspace_handle_id, agent_id)               [normal]
plugin.tool_completed    (plugin_id, plugin_tool_name, duration_ms, response_bytes,
                          status)                                                     [normal]
plugin.error             (plugin_id, plugin_kind, error_kind, message_hash)           [normal]
plugin.peak_resident_sampled  (plugin_id, peak_resident_bytes)                                  [sample]
```

`plugin_kind` values: `language_server`, `formatter`, `indexer`, `build_daemon`, `mcp_bridge`, `custom`. The current LSP plugin (`backend/src/plugins/catalog/lsp/`) is *one instance* of `plugin_kind = "language_server"`. **No field name contains `lsp`, `pyright`, or `language`** — those are values, not keys. A future Ruff long-running daemon or `tsc --watch` plugin emits the same event family unchanged.

**Note on `plugin.session_*`:** V2 proposed `plugin.session_started/stopped`. V3 drops these from v1 because the current loader (`backend/src/plugins/core/loader.py`) is an import-time singleton with no native per-invocation lifecycle. When a real plugin session model is introduced (separate follow-up plan), `plugin.session_*` can be added additively without a schema bump.

**Per-tool timing — every foreground tool**

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

#### Resource & overhead budget for Phase 1 itself

The pull/heartbeat/ring path is intentionally cheap. Budgets (verified by Phase 3 release gates):

| Component | Memory ceiling | CPU ceiling | Disk (sandbox) | Notes |
|---|---:|---:|---:|---|
| Daemon ring | 8 MiB (`max_bytes`) | < 0.1 % avg, < 1 % p99 | 0 (never spills) | hard-capped by both `max_bytes` and `max_events` |
| `api.audit.pull` (1 s cadence) | < 1 MiB transient per call | ~2 ms CPU per call at 1000 events | 0 | O(returned events), not O(retained) |
| `api.audit.snapshot` | 0 | < 0.5 ms | 0 | reads cached gauges only; never walks trees |
| Heartbeat (background tool) | reuses existing 60 s timer | unchanged | 0 | **zero new threads** |
| Upperdir disk samples | 0 | bounded by sample budget; emits `sample_budget_exhausted` | reads only — never writes | TTL-cached |

#### Tests for Phase 1
- `test_audit_buffer_ordering` — `seq` is strictly monotonic across all lanes.
- `test_audit_buffer_eviction_events_and_bytes` — both caps independently enforced.
- `test_audit_buffer_critical_lane_survives_sample_pressure` — flood sample lane to 200 % capacity; assert all critical events retained, lane drop counter accurate.
- `test_audit_buffer_pressure_formula` — assert `max(bytes_ratio, events_ratio)` for boundary cases.
- `test_pull_cursor_exclusive_and_drops_reported` — pull with `after_seq=N` returns events with `seq > N`; `dropped_event_count` and `lost_before_seq` non-zero after forced eviction.
- `test_snapshot_is_o1_under_load` — generate 1 M synthetic events; assert snapshot latency p99 < 1 ms.
- `test_schema_version_constant_matches_pull_response` — `audit_buffer.SCHEMA_VERSION` matches the `schema` field in every pull response.
- `test_audit_reset_floor_op_gated_by_env` — call without `EOS_DAEMON_AUDIT_ALLOW_FLOOR_RESET=true` → rejected; with it → accepted.

#### Phase 1 acceptance criteria
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

---

### Phase 2 — Runner Puller, Emitters, Plugin & Background Instrumentation

**Goal:** Wire daemon emitters across all subsystems; stand up the runner-side puller with adaptive cadence + floor enforcement; instrument generic plugin + background tool surfaces; persist normalized events into rotated+gzipped `sandbox_events.jsonl`.

#### Deliverables

1. **`backend/src/task_center_runner/audit/daemon_pull.py`** — new file.
   - `DaemonAuditPuller` with cursor state, adaptive interval policy with floor enforcement, final-drain on stop.
   - Stats published to performance report: `pull_count`, `empty_pull_count`, `events_pulled`, `pull_error_count`, `dropped_event_count`, `lost_before_seq`, `max_buffer_pressure`, `final_cursor`, `floor_raises` (count of times the cadence floor escalated), `pull_ms` (p50/p95/p99).
   - Floor: `EOS_DAEMON_AUDIT_PULL_FLOOR_MS` (default 100 ms); pressure-based escalation per cross-cutting policy.
   - Never blocks the main run on transient pull failures; logs error and continues at next interval.

2. **Daemon emitters** (one PR per subsystem, mergeable independently to keep review surface small):
   - `layer_stack` — instrument `backend/src/sandbox/daemon/layer_stack_runtime.py`.
   - `overlay_workspace` (ephemeral) — instrument `backend/src/sandbox/overlay/{lifecycle,handle,namespace_runner}.py` and `backend/src/sandbox/ephemeral_workspace/pipeline.py`; stamp `workspace_mode="ephemeral"`.
   - `isolated_workspace` — instrument `backend/src/sandbox/isolated_workspace/pipeline.py` and `backend/src/sandbox/isolated_workspace/_control_plane/{pipeline_registry,pipeline_state,orphan_reaper,workspace_handle_lifecycle,linux_runtime}.py` with the full lifecycle family from Phase 1 schema; stamp `workspace_mode="isolated"`. (Note: V2 said `manager.py` — that path does not exist; control plane actually lives under `_control_plane/`.)
   - `occ` — instrument `backend/src/sandbox/daemon/occ_runtime_services.py` and `backend/src/sandbox/daemon/changeset_projection.py`.
   - `os_resource` — extend existing command-execution resource metrics.

3. **Generic plugin instrumentation** in `backend/src/plugins/core/loader.py`:
   - Wrap plugin-tool dispatch in a thin emitter shim that fires `plugin.tool_invoked` before and `plugin.tool_completed` after.
   - Emit `plugin.error` on exception.
   - **No code in `backend/src/plugins/catalog/lsp/` learns about audit.** Future plugins (formatters, indexers, MCP bridges) inherit instrumentation for free because it lives in `plugins/core/`.
   - `plugin.peak_resident_sampled` emitted on the existing OS resource sampler tick when a plugin process is identified.

4. **Background tool instrumentation** in `backend/src/engine/background/task_supervisor.py`:
   - Emit `background_tool.{started,completed,failed,cancelled,delivered}` from `_apply_terminal_status_transition` and from the `collect_completed` path.
   - Emit `background_tool.heartbeat` on each existing heartbeat tick (60 s).
   - **Zero new threads.**

5. **Per-tool phase emitters** in `backend/src/engine/tool_call/dispatch.py`:
   - Always emit `tool_call.started` + `tool_call.finished` (envelope).
   - Emit `tool_call.phase` per phase, subject to the per-tool sampling rule (cross-cutting §Per-tool phase sampling).
   - `tool_call.finished.phase_totals_rollup` populated from in-process timers (not dependent on phase event emission).

6. **Normalizer** in `backend/src/task_center_runner/audit/sandbox_events.py`:
   - Promote subsystem sections to `payload["<section>"]` — **always** (consumer surface, what the report builder reads).
   - Preserve raw event under `payload["daemon_event"]` — **only when `EOS_AUDIT_FORENSIC_RAW_ENABLED=true`** (default off; forensic-only, never read by automated consumers).
   - The normalizer is the **only file** allowed to write `payload["daemon_event"]`; a CI lint rule (added in this phase) enforces the boundary.
   - Dedupe stream + pull by `seq` then `(operation_id, event, operation_step, tool_id)`.
   - When both stream-derived and pull-derived events match: **pull is authoritative** (richer timing/resource fields).
   - Carry `boot_epoch_id` through; on epoch boundary observed, write a synthetic `daemon.restart_observed` event with `previous_epoch_id`, `new_epoch_id` to preserve the timeline.

7. **`sandbox_events.jsonl` writer** gains rotation + gzip:
   - Rotate at 64 MiB live file.
   - Gzip on rotation (background thread, bounded queue depth = 2).
   - Retain `EOS_AUDIT_ARTIFACT_RETENTION_FILES` (default 8) historical compressed files per run.
   - All files (live + rotated) live under the EOS_TIER_RUN_ID-stable artifact path.

#### Tests for Phase 2

- `test_puller_final_drain_before_recorder_dispose` — recorder dispose blocks until puller drained; assert no events left in ring.
- `test_puller_never_blocks_tool_dispatch` — inject 250 ms pull stall; assert tool latency unchanged.
- `test_puller_floor_raises_under_sustained_pressure` — simulate 5 consecutive pulls with `pressure > 0.8`; assert floor raised by 50 % within the 3-pull threshold.
- `test_puller_floor_never_lowers_automatically` — after escalation, set pressure to 0; assert floor stays raised.
- `test_puller_reset_floor_op_works_when_authorized` — call `api.audit.reset_floor` with env enabled; floor returns to default.
- `test_plugin_events_are_kind_generic` — register a fake `plugin_kind="indexer"` plugin via `plugins/core/loader.py`; assert it emits the same event family as the LSP plugin with NO LSP-specific keys. Grep the emitted JSON for `"lsp"` and `"pyright"` as keys → must be 0 hits (occurrences as `plugin_id` values are OK).
- `test_background_tool_lifecycle_emits_full_lattice` — three runs: RUNNING → COMPLETED → DELIVERED, RUNNING → FAILED → DELIVERED, RUNNING → CANCELLED → DELIVERED; assert exactly one terminal event per run.
- `test_background_tool_heartbeat_reuses_existing_timer` — assert no new threads spawned; assert heartbeat events arrive on the existing `EOS_BACKGROUND_HEARTBEAT_INTERVAL_S` cadence.
- `test_isolated_workspace_orphan_check_after_exit` — kill holder mid-run; assert `orphan_holder_count > 0` in pulled events and in `isolated_workspace.exited` payload.
- `test_sandbox_events_jsonl_rotates_at_64mib_and_caps_history` — synthetic 1 M-event run; assert exactly N rotated files; assert live file ≤ 64 MiB.
- `test_sandbox_events_jsonl_rotation_path_stable_under_eos_tier_run_id` — start run with `EOS_TIER_RUN_ID=test-xyz`; restart mid-run; assert rotated files at the same paths.
- `test_tool_call_phase_slow_tail_flush` — emit 200 invocations of `smoke_tool` with deterministic `total_ms` from a fixture (e.g., `[10ms × 190, 500ms × 10]`); assert (a) first 100 calls always flush all 6 phases (cold window); (b) of remaining 100 calls, the 5 with `total_ms ≥ P95` flush all phases; (c) the other 95 flush no phase events but DO emit `tool_call.finished` with populated `phase_totals_rollup`.
- `test_tool_call_finished_rollup_present_when_phases_discarded` — fast-tail call (total_ms below P95 in the rolling window); assert `tool_call.finished.phase_totals_rollup` populated with all 6 phase keys.
- `test_dedupe_pull_supersedes_stream_when_both_present` — emit same logical event via both paths; assert consumer sees the pull version (richer fields).
- `test_no_consumer_reads_daemon_event_under_default_config` — full mock suite with `EOS_AUDIT_FORENSIC_RAW_ENABLED` unset; assert `daemon_event` key absent from every recorded payload.
- `test_forensic_raw_present_when_env_enabled` — same suite with `EOS_AUDIT_FORENSIC_RAW_ENABLED=true`; assert `daemon_event` key present and structurally equal to source.
- `test_daemon_event_writer_module_boundary` — CI-grade grep: any file outside `task_center_runner/audit/sandbox_events.py` (and test files) referencing `payload["daemon_event"]` / `payload.get("daemon_event")` → fail.
- `test_daemon_restart_epoch_handled_by_puller` — simulate boot_epoch_id change between pulls; assert puller resets cursor, increments `daemon_restarts_observed`, writes a synthetic `daemon.restart_observed` event.

#### Phase 2 acceptance criteria
- All tests above pass under `.venv/bin/pytest`.
- Mock-suite end-to-end run produces `sandbox_events.jsonl` with all subsystem sections populated (verified by jq query).
- Rotation kicks in correctly on a synthetic 100 MiB run; gzip succeeds; retention cap holds at 8 files.
- `dropped_event_count == 0` and `lost_before_seq == 0` on the full mock suite.
- No new threads created in `task_supervisor.py` (verified by thread count diff).

---

### Phase 3 — Consolidated Performance & Resource Report + Release Gates

**Goal:** Render a human-readable, decision-grade performance & resource report; gate rollout on measured overhead AND isolated-workspace orphan counts AND drop-free pull AND artifact bound. Promote `daemon_audit_pull.enabled=true` as default for sandbox-backed runs after gates pass.

#### Deliverables

1. Extend `backend/src/task_center_runner/audit/performance_report.py` to produce:
   - Structured `sandbox.sections` JSON object (mirror of MD).
   - Rendered Markdown report with the fixed layout below.
   - Single source of truth: report builder reads `payload.<section>` only; never reads `payload.daemon_event` (asserted by `test_report_consumer_reads_promoted_payload_section_not_daemon_event`).

2. Add `sandbox.daemon_audit_pull` block — puller stats from Phase 2 (`pull_count`, `events_pulled`, `dropped_event_count`, `lost_before_seq`, `max_buffer_pressure`, `floor_raises`, `pull_ms` p50/p95/p99, `final_cursor`).

3. Add `sandbox.overhead` block — measured cost of the audit path itself (memory delta, CPU delta, tool latency delta, disk delta).

4. Promote `daemon_audit_pull.enabled=true` as default in the sandbox-backed runner config — gated on all 4 release gates passing.

#### Fixed report layout (`performance_report.md`)

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

#### Release gates (Phase 3 cannot ship without all 4 passing)

**Measurement methodology (applies to overhead + drop-free gates):**
- 3 paired runs (puller on vs puller off) of the dask-heavy live-e2e fixture below.
- First 60 s of each run discarded as warmup (daemon boot + JIT settling).
- Minimum N ≥ 1000 tool calls per run for delta computation.
- Statistical test: paired bootstrap (10,000 resamples) of the p95 delta; gate passes when the 95 % confidence-interval **upper bound** is below the threshold.
- All measurements pinned to `EOS_SWEEVO_INSTANCE=dask__dask_2023.3.2_2023.4.0` under `EOS_SANDBOX_PROVIDER=docker` + `EOS_ISOLATED_WORKSPACE_ENABLED=true` (V1 reproducibility anchor).

| Gate | Threshold (95 % CI upper bound) | Fallback if not met |
|---|---|---|
| **Audit overhead gate** | tool-call wall-time p95 delta ≤ 5 ms; daemon RSS delta ≤ 16 MiB at steady state (post-60s warmup); runner CPU delta ≤ 0.5 % averaged over a ≥ 5 min window; sandbox disk delta = 0 bytes | Raise pull floor (don't raise ring caps); if still failing after floor escalation, ship Phase 3 with `daemon_audit_pull.enabled=false` as default + open follow-up plan |
| **Isolated workspace gate** | Every isolated_workspace exit reports `orphan_holder_count == 0`, `orphan_cgroup_count == 0`, `orphan_scratch_count == 0`; at run completion `open_handle_count == 0`; after each exit `holder_pid_alive == false`. **The gate is evaluated against the daemon-side ring via `api.audit.snapshot` AND the puller's recorded events** (so it does not depend on the puller toggle — see below) | **HARD BLOCK** — do not ship; isolated workspace is the highest-risk safety surface; fix root cause |
| **Drop-free pull gate** | `dropped_event_count == 0` and `lost_before_seq == 0` across the gate suite (95 % CI upper bound) | Raise pull floor (more frequent pulls); do not raise ring caps as a workaround |
| **Artifact bound gate** | Rotation kicks in correctly during synthetic 1 M-event run; total host-side footprint stays within `64 MiB + 8 × rotated` cap; gzip succeeds for every rotation | Tune retention cap down; do not relax rotation threshold |

**Safety-gate-vs-toggle resolution (closes Critic P0):**

The isolated-workspace HARD BLOCK gate is evaluated during the release-gate suite with `daemon_audit_pull.enabled=true`. **Shipping any part of V3 requires all 4 gates pass with the puller enabled** — the `daemon_audit_pull.enabled=false` fallback applies ONLY to the runtime default-on rollout (audit overhead gate), NOT to gate evaluation.

Runtime invariant after ship:
- `isolated_workspace.{exited, orphan_check_completed, orphan_reaped}` are emitted by the daemon **unconditionally** on the `critical` lane (the emission site is in the orphan reaper, not the puller).
- When the puller is disabled at runtime, orphan evidence is still captured in the daemon ring (recoverable via `api.audit.snapshot` for live diagnostics) AND mirrored by the existing stream-bridge fallback into `sandbox_events.jsonl` (until stream-bridge sunset gate fires).
- Operators MUST NOT disable both puller AND stream-bridge simultaneously while isolated_workspace is enabled — a Phase 2 startup check (`task_center_runner/core/engine.py`) refuses to start when `daemon_audit_pull.enabled=false` AND `EOS_AUDIT_STREAM_FALLBACK=false` AND `EOS_ISOLATED_WORKSPACE_ENABLED=true`.

#### Gate verification commands (pinned to V1 reproducibility anchor)

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

#### Tests for Phase 3
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

#### Phase 3 acceptance criteria
- All tests above pass under `.venv/bin/pytest`.
- All 4 release gates pass on `EOS_SWEEVO_INSTANCE=dask__dask_2023.3.2_2023.4.0` heavy live-e2e run.
- `daemon_audit_pull.enabled=true` set as default in sandbox-backed runner config.
- Stream-bridge retirement countdown begins (K=5 consecutive clean heavy runs).

---

## Part 3 — Architectural Decision Record

**Decision:** Implement V3 plan (V2 + closure of 10 residual gaps) in 3 phases.

**Drivers:**
1. Production safety of isolated-workspace exit (highest blast radius — orphan PIDs/cgroups/scratch are data leaks).
2. Bounded resource cost — the audit path must not regress sandbox throughput.
3. Future-proofness for new plugin kinds and tool families.

**Alternatives considered:**
- **V2 as-is** — rejected; 10 residual gaps remain implicit (consumer divergence between `daemon_event` and promoted sections; cadence floor undefined; phase-event budget math missing; plugin lifecycle mismatch unaddressed; stream-bridge sunset undefined).
- **2-phase compression** — rejected; merging ring+RPC with emitters loses the smoke-only Phase 1 safety net and concentrates risk; ring and emitters are independently testable and deserve separate review.
- **Add real plugin session lifecycle now** — rejected; current loader is import-time singleton; introducing a session abstraction is a multi-file refactor outside this plan's scope; V3 accepts the regression in session-level observability and explicitly defers to a follow-up.

**Why chosen (V3):**
- Closes consumer-divergence by declaring `payload.<section>` authoritative (gap #2).
- Closes phase-event budget hole with lane assignment + per-tool sampling (gap #3).
- Closes plugin lifecycle hole by dropping `plugin.session_*` from v1 and deferring honestly (gap #4).
- Closes cadence runaway by defining `EOS_DAEMON_AUDIT_PULL_FLOOR_MS` + escalation (gap #6).
- Closes pressure-formula ambiguity (gap #7).
- Defines stream-bridge sunset gate (gap #8).
- Integrates with `EOS_TIER_RUN_ID` artifact stability (gap #9).
- Splits per-tool reports by `workspace_mode` so "is `edit_file` slower in isolated mode?" is directly answerable (gap #10).

**Consequences:**
- Phase 1 is purely additive (ring + RPC + schema, no instrumentation) — easy to revert if needed.
- Phase 2 touches many files (one PR per subsystem) — biggest review surface; mitigated by per-subsystem PR splits.
- Phase 3 carries hard release gates that could block ship if overhead is too high. Mitigation: `daemon_audit_pull.enabled=false` fallback toggle lets the rest of the consolidation ship even if the default-on rollout is deferred.
- Stream-bridge code remains in V3; retirement is a follow-up plan.
- Plugin session lifecycle remains undelivered; follow-up plan needed.
- `tool_call.phase` slow-tail flush captures full causal chain for the slowest ~5 % of calls per tool but discards phase events for fast-path calls. Aggregate per-tool stats remain accurate via `phase_totals_rollup` on `tool_call.finished`. Long-tail debugging ("why was *this* call slow?") is fully supported; uniform sampling debugging ("show me phase boundaries for every call") is not — and is an explicitly accepted tradeoff in service of Principle 4 (bounded resource cost).

**Follow-ups (out of scope for this plan):**

**Pre-merge requirement** (closes Critic P2): the following tracking issues MUST be filed and linked in this ADR BEFORE this plan merges. Without filed issues, "follow-up" becomes "permanent regression".

1. **Stream-bridge code removal** after retirement gate passes (K=5 consecutive clean heavy runs). Issue title: `[Audit] Remove stream-bridge fallback after K=5 clean runs (post-V3)`. Trigger: retirement gate observed in 5 weekly heavy runs.
2. **Real plugin session model** + `plugin.session_*` events (additive, no schema bump). Issue title: `[Plugins] Introduce per-workspace plugin session lifecycle`. Trigger: any second plugin kind added to `plugins/catalog/` (forces the question).
3. **Plugin-kind catalog expansion** (Ruff daemon, `tsc --watch`, mypy daemon — each new kind drops into the existing schema). No specific trigger; opportunistic.
4. **Ring-by-lane separation** if audit overhead gate fails permanently — investigate per-subsystem ring sharding. Trigger: overhead gate failure post-ship.

Each follow-up issue references this plan by path and version (V3) so future readers can trace the original decision context.

---

## Part 4 — Requirement traceability

| User requirement | Addressed by |
|---|---|
| **1.** States/events/resources for overlay (isolated vs ephemeral), layerstack, OCC, background tool calls | Phase 1 schema tables (ephemeral-vs-isolated property table; `layer_stack`, `occ`, `background_tool` event families with operation_step + lease_id + changeset_id + manifest_root_hash); Phase 3 report §2 / §4 / §6 / §7 / §8 / §9 / §10 |
| **2.** Background tool calls + GENERIC (non-LSP) plugin details | Phase 1 `background_tool.*` family (reuses existing `BackgroundTaskStatus` lattice and existing 60 s heartbeat); `plugin.*` family keyed by `plugin_id` + `plugin_kind` (values: `language_server`, `formatter`, `indexer`, `build_daemon`, `mcp_bridge`, `custom`); Phase 2 instrumentation lives in `backend/src/plugins/core/loader.py` and `backend/src/engine/background/task_supervisor.py` — NOT in `backend/src/plugins/catalog/lsp/`; `test_plugin_events_are_kind_generic` and `test_report_renders_without_lsp_specific_strings` enforce no LSP-named keys; Phase 3 report §4 + §5 |
| **3.** Detailed per-tool time stats | Phase 1 `tool_call.{started,phase,finished}` schema with `phase_totals_rollup` always populated; Phase 2 slow-tail buffered phase flush (cold window + P95 slow-tail) + always-emit envelope; Phase 3 report §2 (per-tool, **per-workspace-mode**, p50/p95/p99 across all 6 phases) + §3 (top-10 phase breakdown). Per-call causal chain preserved for the slowest ~5 % of calls per tool (Principle 3 upheld for outlier debugging). |
| **4.** Detailed performance & resource report | Phase 3 fixed Markdown layout §1–§13 with structured JSON mirror; release-gate-grade; `test_performance_report_md_layout_structure` schema-shape assertions (column-regex + `plugin_kind ∈ enum`); plus `test_performance_report_json_contains_all_subsystem_sections` |
| **5.** Heartbeat / audit / pull cheap; sandbox disk controllable; host log persistence managed | Phase 1 overhead budget table (8 MiB ring, < 1 % p99 daemon CPU, zero new threads, snapshot O(1)); Phase 2 disk contract (zero sandbox writes; host rotation at 64 MiB + gzip + 8-file retention; EOS_TIER_RUN_ID-stable artifact paths); Phase 3 §11 + §12 + overhead release gate with explicit fallback toggle (`daemon_audit_pull.enabled=false`) |

---

*End of V3 plan. Next: spawn Architect (read-only steelman) and Critic (quality/test rigor verdict) per the ralplan-consensus protocol.*
