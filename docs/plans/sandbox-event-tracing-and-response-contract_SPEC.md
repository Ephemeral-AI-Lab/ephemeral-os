# Sandbox Event Tracing and Response Contract

Status: Proposed (rev 2 — destructive posture)
Date: 2026-06-12
Scope: `sandbox/crates` (Rust) + host-side trace persistence; additive TS contract notes only.
Inputs:
- `docs/plans/agent-core-rust-to-typescript-migration/sandbox-response-observability-findings.md`
- `sandbox/docs/sandbox-event-tracing-response-plan.md` (parallel draft; identity model, seq chain, phase vocabulary, declassification, and fail-closed rule merged here)
- Verified live scan of dispatch, transport, host forwarding, command session, plugin, workspace, LayerStack/OCC, and e2e helpers (anchors inline)

## Posture: Destructive, Clean-Slate

Owner directive: the existing code records stats, values, and parameters
poorly — the recording mechanism and the response envelope are both assumed
unsuitable. This plan therefore **replaces** rather than preserves. It treats
tracing as a performance/audit/observability-critical capability: when a bug
occurs inside the sandbox, every internal step must be transparent and
traceable after the fact. Opaque internals are the failure mode this plan
exists to eliminate.

This supersedes the findings doc's preserve-first guidance. The findings doc
remains the inventory of *what information exists today*; it no longer
constrains *the shape it is delivered in*.

What gets deleted (not wrapped, not shimmed long-term):

| Deleted | Anchor | Replaced by |
| --- | --- | --- |
| `OpResponse::Success(serde_json::Value)` untyped bodies | `eos-operation/src/core/response.rs:6-11` | `OperationEnvelope<T>` tagged union with per-family typed results |
| `error: ()` serialized as `null`; `mutation_source: None` as `""` | `core/workspace_outcome.rs:153-189` | honest structs; quirk serializers deleted outright |
| Ad-hoc `json!` envelopes + `success: bool` branching | `core/response.rs:61-85`, `dispatch/dispatcher.rs:99-113`, `protocol.rs:146-151` | one envelope renderer; `status` discriminant |
| Flat dotted-key `timings` maps threaded as `&mut Map<String, Value>` through every layer | `dispatcher.rs:142-162`, `runtime/response.rs:75-93`, settle/OCC/plugin sites | spans as the **single source of truth** for durations; response meta derived from the trace record |
| `merge_runner_timings` key aliasing (`workspace.mount_s` → `command_exec.mount_workspace_s`) | `runtime/response.rs:84-93` | one canonical step vocabulary, no aliases |
| Pretty-JSON final-response crash files as the only command audit | `eos-command-session/src/session.rs` | trace store rows + bounded final-state events (transcript files stay for raw output) |

In-repo consumers that migrate in lockstep (verified — there are no others):
`eos-sandbox-gateway`, `eos-sandbox-host` (including the `e2e_support`
`is_success`/`error_kind` helpers), and the e2e suites (10 test files assert
`timings.` keys today). The TS workspace has no daemon-facing code yet, so the
new contract is its day-one contract.

## Decision Summary

| Decision | Choice | Why |
| --- | --- | --- |
| Instrumentation backbone | `tracing` 0.1 facade + `tracing-subscriber` 0.3 custom Layer; new crate `eos-trace` | Third-party-preferred; macros are no-ops without a subscriber (ns-runner process, library tests); spans land at the existing phase boundaries and **replace** the timing-map plumbing |
| Timing source of truth | The span tree. Response `meta` (duration, step summary, modules touched, resource summary) is rendered **from the trace record**, never hand-inserted | One measurement, one vocabulary; drift between response and trace becomes impossible by construction |
| Identity | `trace_id` (host-minted, propagated in the request envelope) ≠ `op_id` (= `invocation_id`, one request/response) ≠ `span_id`; plus a per-trace monotonic `seq` event chain and cross-op link rows | A long-lived chain (exec → stdin → poll → settle) is one `trace_id` across many `op_id`s; replay is `WHERE trace_id=? ORDER BY seq`; concurrency lives in the span tree, not the chain |
| Event delivery | Hybrid: request-scoped events ride the response as an internal `_trace_events` sidecar (host-ingested, gateway-stripped); background traces (reaper settles, sweeps) buffer in a bounded spool drained via `sandbox.trace.export` | Sidecar = zero loss window and zero extra round trips for request traces; drain op covers work that has no response to ride. The one-request-one-response protocol (`server.rs:262-287`) permits exactly this combination |
| Persistence | SQLite (`rusqlite`, bundled, WAL) on the **host** at `state_dir/sandbox-traces.sqlite` (0600/0700) | Audit = chain reconstruction + joins + aggregation = SQL; matches TS better-sqlite3+WAL+Kysely precedent (`eos-agent-core/packages/db/src/database.ts:17`). JSONL only as a derived export, never the system of record |
| Persistence strictness | **Fail-closed for mutating ops**: host records the request-start row before forwarding; if that write fails, the mutating op is not forwarded. Read-only ops proceed with a `trace_degraded` marker | Audit-critical framing: an untraceable mutation is worse than a refused one |
| Workspace route | 4-valued, trace-only: `ephemeral_workspace` \| `isolated_workspace` \| `fast_path` \| `none` | Owner decision. `fast_path` = data-plane work directly against LayerStack with no workspace (direct file merge/read); `none` = pure control plane. Never used for runtime branching — observability only |
| Response contract | Single typed envelope, `status ∈ {ok, running, rejected, cancelled, timed_out, error}` tagged union; domain payload under `result`, fault under `error`, everything else under `meta` | Most readable: one switch tells the consumer what happened; no `success:false`+error-kind double decode; no null pairs |
| Compatibility | None preserved. A short-lived v1 flattening adapter exists only as a migration vehicle inside the phase ladder and is **deleted** in the final phase | No technical debt is the explicit goal; all in-repo consumers migrate in lockstep |

## Part A — Event Tracing

### Identity model

| Identity | Source | Meaning |
| --- | --- | --- |
| `trace_id` | Host-minted (uuid4) when starting a user-visible call; propagated to the daemon in the request envelope; reused across every op of a long-lived chain | One user-visible sandbox interaction or one long-lived session chain |
| `op_id` | The existing top-level `invocation_id` (`protocol.rs:118-135`) | One daemon request/response |
| `span_id` | Daemon `AtomicU64` (never reuse `tracing::span::Id` — the Registry recycles them) | One timed unit, parented into a per-op tree |
| `seq` | Host-assigned at ingest, monotonic per `trace_id` | Durable observation order; gap-free even when daemon batches arrive late |
| `daemon_boot_id` | uuid4 per daemon process | Exposes respawn gaps in audit |

Two views over the same data, both first-class:

| View | Query | Use |
| --- | --- | --- |
| Timeline chain | `WHERE trace_id=? ORDER BY seq` | Audit replay, "what happened next", total-elapsed narrative |
| Causal tree | `span_id`/`parent_span_id` | Nested/parallel work, subsystem ownership, per-step durations |

Cross-op links (`trace_links` rows) tie long-lived resources into chains:
`command_session_id`, `workspace_handle_id`, `plugin_service_instance_id`,
`layer_manifest_version`.

Chain continuity is **host bookkeeping, specified here**: the host keeps an
in-memory `link_id → trace_id` map (command sessions, workspace handles),
populated when a response returns a link id (exec → `command_session_id`,
isolation enter → `workspace_handle_id`), consulted when a later request's
args carry that id (`write_stdin`, `poll`, `collect`, `cancel`, isolated ops),
pruned on settle/exit, and rebuildable from `trace_links` after a host
restart. The daemon stashes both the origin `op_id` and the chain `trace_id`
in `ActiveCommand` at exec (`service.rs:340`), so background settle traces
carry the chain id even when the host never polls.

### Flow

```
client ──op,args──> host/gateway
                      │ mint trace_id (or reuse chain's), op_id
                      │ INSERT request-start row  ── fail ⇒ mutating op NOT forwarded
                      ▼
              daemon transport (server.rs)
                      │ root span `op_request` opened BEFORE read_request_line
                      │ (wire failures — bad JSON, too-large, timeout, auth — close it
                      │  with status=error; every accepted connection yields a trace)
                      ▼
              spawn_blocking → dispatch ── span `dispatch` {op_resolved, parse, fallback}
                      ▼
              op adapter ── span `op.<family>.<verb>` {workspace_route recorded at decision site}
                      ▼
              subsystems ── spans + phase events (layerstack / overlay / command / isolated / plugin)
                      │     resource_sample events (cgroup, /proc, tree stats)
                      ▼
              root closes ── full TraceRecord assembled; envelope `meta` rendered FROM it
                      │      (the response write itself is observed host-side: received_at,
                      │       rtt, response_persisted — a record cannot describe its own delivery)
                      ▼
              response + `_trace_events` sidecar (the TraceRecord) ──> host: ingest, assign seq,
                      │                              UPDATE op row (outcome, received_at, rtt)
                      │                              strip sidecar at the gateway
                      ▼
   background work (reaper settle, sweeps) ──> bounded spool ──> `sandbox.trace.export`
                                               drained per-forward and exhaustively at release()
```

### New crate `sandbox/crates/eos-trace`

- `record.rs` — typed DTOs: `TraceId`, `OpId`, `SpanUid`, `TraceRecord`,
  `SpanRecord`, `EventRecord`, `WorkspaceRoute`, `TraceKind`
  (`OpRequest | CommandSettle | SessionSweep | IsolatedSweep | PluginService`),
  closed `SpanKind` enum with exhaustive `subsystem()` mapping
  (`Wire | Dispatch | Op | LayerStack | Overlay | CommandSession | Workspace |
  Plugin | Control`), bounded-detail helpers (sizes/hashes/refs, never raw
  blobs).
- `spool.rs` — bounded background-trace buffer (default 4 MiB, drop-oldest,
  `dropped_traces` counter); per-span field budgets with an explicit
  `truncated` flag so one pathological op cannot evict its siblings.
- `layer.rs` — `TraceSpoolLayer: Layer<Registry>`: span state in Registry span
  extensions (`on_new_span` captures fields via `Visit`, `on_record` lands late
  fields like `workspace_route`, `on_event` appends, `on_close` pushes children
  into parents; a closing root assembles the `TraceRecord`). The transport
  closes the root **immediately before envelope render** and calls
  `take_finished(trace_id) -> TraceRecord` exactly once: that record is both
  the source for envelope `meta` and the sidecar payload. The response write
  itself is deliberately outside the record — a record cannot describe its own
  delivery; the host observes it (`received_at_ms`, `host_rtt_ms`,
  `response_persisted` / `response_missing`). Request-scoped records never
  enter the spool; the spool is background-only. Roots with
  `trace_exempt = true` (the export op itself) are skipped.

Crate ownership boundaries (merged from the parallel draft):

| Owner | Responsibility |
| --- | --- |
| `eos-trace` | Storage-neutral DTOs, spool, subscriber layer, route/kind enums, bounded-detail helpers |
| `eos-operation` | Envelope + per-family result DTOs — contract shape, not persistence |
| `eos-daemon` | Root/dispatch/op spans, sidecar assembly, subscriber install, export op |
| mechanism crates (layerstack, workspace, command-session, plugin, overlay) | Emit spans/events at their own phase boundaries; no persistence or policy deps |
| `eos-sandbox-host` | SQLite store, request-start fail-closed rule, sidecar ingest + seq assignment, degraded/uncertain records, export drains |
| `eos-sandbox-gateway` | Declassification: strip `_trace_events` from client-facing responses; operator/debug trace lookup only |
| `@eos/db` / `@eos/contracts` | TS mirror of schema + Zod envelope schemas when the TS host lands |

### Workspace route taxonomy (4-valued, trace-only)

`workspace_route.kind` is an observability attribute recorded at the verified
decision sites. It must never become runtime control flow again.

| Kind | Meaning | Decision site |
| --- | --- | --- |
| `ephemeral_workspace` | One-op ephemeral/overlay route with capture → OCC publish semantics (includes plugin oneshot overlay) | `op_adapter/command.rs` `ExecTarget::Ephemeral` branch; `op_adapter/plugin.rs` overlay path |
| `isolated_workspace` | Caller-keyed isolated workspace; private upperdir; no publish | `command_binding_for` hits in `op_adapter/command.rs` and `op_adapter/files.rs` `route_file_op`; isolation enter/exit lifecycle ops |
| `fast_path` | Data-plane work directly against LayerStack with **no workspace**: direct file merge/read (`FileRoute::Direct`), checkpoint base/commit/binding ops, layer-metrics manifest reads | `route_file_op` direct arm; `op_adapter/checkpoint.rs` |
| `none` | Pure control plane — no workspace and no LayerStack data-plane work: ready, heartbeat, cancel, in-flight/session counts, plugin ensure/status, isolation status/list, workspace-run cancels, trace export | adapter classification table (each op family declares its default; `route_file_op`-style late recording overrides where the route is dynamic) |

Edge calls, decided here: `sandbox.checkpoint.layer_metrics` is `fast_path`
(reads the live manifest); `commit_to_git` stays `fast_path` even though it
mounts an overlay worktree internally — that mount is a projection detail
(visible as its own span), not an agent workspace; `sandbox.isolation.status`
is `none` (registry read, no workspace entry); plugin `ensure`/`status` are
`none` (service control plane) while registered plugin overlay ops are
`ephemeral_workspace`.

### Detail-capture principle

Assume nothing useful is recorded today. Every span records its **inputs' key
parameters and outputs' key results** as typed fields — op args summary (paths,
caller, flags), manifest versions, lease ids, changed-path counts, exit codes,
kill reasons, byte counts, depths, veth/cgroup names, worker exit codes, PPC
message ids. Bounded by rule: sizes, hashes, counts, ids, and references to
content that already exists elsewhere (transcripts, response rows) — never raw
stdout/stderr, file contents, or plugin result blobs in trace events.

Phase-event vocabulary (events inside spans; merged from the parallel draft —
this is the required minimum, not a cap):

| Module | Required events |
| --- | --- |
| `host.protocol` | request_received, request_persisted, forward_started, forward_finished, response_missing, uncertain_outcome, trace_degraded |
| `daemon.transport` | read_started, read_finished, auth_checked, decoded (the write is observed host-side as `response_persisted`) |
| `daemon.dispatch` | dispatch_started, op_resolved, parse_finished, plugin_fallback_checked, dispatch_finished |
| `workspace.route` | route_selected {kind, reason} |
| `layerstack` | binding_loaded, snapshot_acquired, lease_released, manifest_read, auto_squash_started/finished |
| `occ` | commit_started, validate_groups_finished, publish_layer_finished, conflict_detected, commit_finished |
| `overlay` | workspace_prepared, mount_started/finished, capture_started/finished, unmount_finished |
| `command_session` | prepared, spawned, yielded, stdin_written, progress_read, cancelled, timed_out, reaped, settled, final_persisted |
| `isolated_workspace` | enter_started, holder_started, network_configured, status_read, exit_started, teardown_phase_finished (×4), exited |
| `plugin` | ensure_started, package_checked, service_started, service_health_checked, ppc_message_sent/received, overlay_started/finished, callback_request/response |
| `file` | read_started/finished, mutation_started, edit_applied, write_applied |
| `resource` | resource_sampled {cgroup cpu/mem/io, /proc rss, tree stats, layer depth} |

### Span taxonomy (timed tree; verified anchors)

| Step | Span kind(s) | Key fields | Anchor |
| --- | --- | --- | --- |
| wire message | root `op_request` (closes before envelope render) | op, op_id, trace_id, caller_id, is_tcp, read_request duration (response bytes/delivery are host-recorded) | `eos-daemon/src/transport/server.rs:262-287` |
| dispatch | `dispatch`; `op.plugin.dynamic` for the registered-plugin fallback | builtin op, outcome, error_kind | `dispatcher.rs:31-64,66-80` |
| op | `op.<family>.<verb>` per `builtin.rs` arm | workspace_route (recorded late via `Span::record`), parsed-args summary | `eos-daemon/src/dispatch/builtin.rs` |
| layerstack | `layer_stack.acquire_snapshot`, `layer_stack.auto_squash`, `occ.commit` (children `validate`, `publish`) | manifest_version, depth before/after, gated/direct path counts | `eos-layerstack/src/commit/worker.rs:328-399,478-522` |
| overlay | `overlay.capture_upperdir`; ns-runner mount/tool recorded as fields from `RunResult` (separate process — no synthetic spans) | changed_path_count, tree bytes | `eos-workspace/src/shared/capture.rs:27-36` |
| command session | `command.session.spawn/wait`, `command.settle`; background root `command.settle` for the reaper path | command_session_id, kill_reason, exit_code, origin op_id | `eos-operation/src/command/service.rs:60-90,340,383-396` |
| isolated lifecycle | `isolated.enter.{spawn_ns_holder,open_ns_fds,install_veth,mount_overlay,configure_dns,create_cgroup}`; `isolated.exit.{kill_holder,teardown_veth,cgroup_rmdir,rmtree_scratch}` | per-phase durations (replaces `phases_ms`), inspection facts | `eos-workspace/src/isolated_workspace/manager/lifecycle.rs:21-63` |
| plugin | `plugin.ensure/status`, `plugin.overlay.{acquire,setup,run,capture,publish}`, `plugin.ppc.round_trip` | plugin id, op name, worker_exit_code, message ids; the request audit fields plugins currently parse and drop | `eos-daemon/src/op_adapter/plugin.rs`; `eos-operation/src/plugin/overlay.rs:140-176` |

### Context propagation rules

The architecture is async-accept + synchronous dispatch on `spawn_blocking`
(`server.rs:350`). Three explicit rules:

1. **Root**: the `op_request` span opens in `handle_connection` **before**
   `read_request_line`, fields `Empty`, recorded after decode — wire-level
   failures (bad JSON, too-large, read timeout, TCP auth) are built before
   dispatch ever runs (`server.rs:270-281,295-318`) and must still close a
   trace. Move the `Span` into the `spawn_blocking` closure and `enter()`;
   from there the op is synchronous and context flows on the thread stack. A
   registry-aborted invocation leaves a root trace with
   `error_kind = "cancelled"`.
2. **OCC commit worker** (own thread, `worker.rs:144`): the queued work item
   carries `Span::current()` captured at enqueue; the worker enters it, so
   `occ.commit.*` parents under the requesting op.
3. **Background reaper/sweeper threads**: no ambient span; their roots become
   standalone traces (`CommandSettle`/`SessionSweep`/`IsolatedSweep`) that
   carry `command_session_id` + origin `op_id` (stashed in `ActiveCommand` at
   exec, `service.rs:340`) and the chain's `trace_id`. This covers the path
   that today produces **no observable record at all**: sweeper-cancelled
   sessions where `publish_completion = false` (`service.rs:392`).

`eosd ns-runner` is a separate process and is not instrumented; its mount/tool
timings arrive via `RunResult` as span fields. Test-determinism rule: thread-
local `set_default` subscribers do not reach `spawn_blocking`; daemon trace
tests use `with_default` on current-thread paths or a per-test global default.

### Transport

Request gains an optional `trace` envelope field (top level, beside
`invocation_id` — a deliberate wire change under the destructive posture):

```json
{"op":"sandbox.command.exec","invocation_id":"op_9f2c…",
 "trace":{"trace_id":"tr_6b1a…","parent_span_id":null},
 "args":{"cmd":"make test","caller_id":"run_1","layer_stack_root":"/eos/layer-stack"}}
```

Responses carry the internal sidecar, stripped by the gateway before any
client sees it (direct daemon clients — the e2e pool — see it and assert it):

```json
{"status":"ok","result":{…},"meta":{…},
 "_trace_events":{"trace_id":"tr_6b1a…","records":[…],"spool_pending":2}}
```

`spool_pending > 0` tells the host background traces are waiting; it drains
them via `sandbox.trace.export` (new catalog op, `Internal` visibility — the
gateway never routes it; in-sandbox callers cannot observe the audit stream).
Export drain is transactional (records removed only after successful
serialization), oldest-first, `max_bytes`-bounded with `remaining_traces`
looping, plus an exhaustive drain in `release()` (`host.rs:122`) and a drain
helper for the e2e pool (which bypasses `SandboxHost::forward` —
`eos-e2e-test/src/pool.rs:213`).

Loss accounting is explicit everywhere: `dropped_traces` (spool overflow),
`dropped_children`/`truncated` (per-trace caps), `daemon_boot_id` gaps
(crashes), `response_missing`/`uncertain_outcome`/`trace_degraded` host rows
(transport failures). Audit shows gaps; it never silently lies.

Crash forensics: the daemon also installs a `tracing-subscriber` fmt layer
writing JSON lines to the existing `--log-file` (today it only captures raw
stdout/stderr redirection). When the daemon dies mid-op, the log file holds
the structured events that never reached a sidecar or the spool.

### Host persistence (`eos-sandbox-host/src/trace_store.rs`)

Storage layout under the host `state_dir` (0700):

```
<state_dir>/
  sandbox-traces.sqlite          # THE system of record — all sandboxes, all time
  sandboxes/<sandbox_id>/        # per-sandbox artifact folder (bulky files, not records)
    daemon.log.jsonl             # structured crash log (fmt layer output, pulled at release/crash)
    exports/trace-<trace_id>.jsonl   # derived human-shareable exports, rebuilt from SQLite
```

One database, not one per sandbox — deliberately. Cross-sandbox audit ("all
failed plugin-overlay ops touching isolated workspaces last week, any
sandbox") is a core query; SQLite cannot join across hundreds of per-sandbox
files without `ATTACH` gymnastics, and the host process is already the single
writer for every sandbox it owns. `sandbox_id` is a keyed column on every
table; per-sandbox deletion is `DELETE … WHERE sandbox_id = ?` plus removing
the artifact folder. The per-sandbox **folder** exists for what does not
belong in a database: crash logs and derived JSONL exports.

`sandbox-traces.sqlite`: 0600 file, WAL, single-writer behind a `Mutex`. No
trait seam — one backend; tests use temp dirs.

```sql
PRAGMA journal_mode=WAL; PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS trace_ops (
  op_id            TEXT PRIMARY KEY,          -- invocation_id
  trace_id         TEXT NOT NULL,
  sandbox_id       TEXT NOT NULL,
  op               TEXT NOT NULL,
  family           TEXT NOT NULL,             -- catalog OpFamily
  caller_id        TEXT,
  workspace_route  TEXT CHECK (workspace_route IN
    ('ephemeral_workspace','isolated_workspace','fast_path','none') OR workspace_route IS NULL),
  status           TEXT,                      -- envelope status; NULL = never returned
  error_kind       TEXT,
  sent_at_ms       INTEGER NOT NULL,          -- host clock, written BEFORE forward (fail-closed gate)
  received_at_ms   INTEGER,
  host_rtt_ms      INTEGER,
  duration_us      INTEGER,                   -- daemon op span duration (advisory clock)
  daemon_boot_id   TEXT,
  modules_touched  TEXT,                      -- JSON array of subsystems (denormalized rollup)
  response_digest  TEXT,                      -- sha256 of canonical response
  response_summary TEXT                       -- bounded JSON summary, not the full payload
);
CREATE TABLE IF NOT EXISTS trace_spans (
  trace_id        TEXT NOT NULL,
  op_id           TEXT,                       -- NULL for background traces
  span_id         INTEGER NOT NULL,
  parent_span_id  INTEGER,
  kind            TEXT NOT NULL,              -- SpanKind wire spelling
  subsystem       TEXT NOT NULL,
  status          TEXT NOT NULL DEFAULT 'ok',
  started_us      INTEGER NOT NULL,
  duration_us     INTEGER NOT NULL,
  fields_json     TEXT,
  PRIMARY KEY (trace_id, span_id)
);
CREATE TABLE IF NOT EXISTS trace_events (
  trace_id    TEXT NOT NULL,
  seq         INTEGER NOT NULL,               -- host-assigned, monotonic per trace
  op_id       TEXT,
  span_id     INTEGER,
  module      TEXT NOT NULL,
  event       TEXT NOT NULL,
  level       TEXT NOT NULL DEFAULT 'info',
  ts_us       INTEGER NOT NULL,
  details_json TEXT,                          -- bounded
  PRIMARY KEY (trace_id, seq)
);
CREATE TABLE IF NOT EXISTS trace_resources (
  trace_id TEXT NOT NULL, op_id TEXT, span_id INTEGER,
  ts_us INTEGER NOT NULL, kind TEXT NOT NULL, values_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS trace_links (
  trace_id  TEXT NOT NULL,
  link_kind TEXT NOT NULL,                    -- command_session|workspace_handle|plugin_service|manifest_version
  link_id   TEXT NOT NULL,
  op_id     TEXT,
  PRIMARY KEY (trace_id, link_kind, link_id, op_id)
);
CREATE TABLE IF NOT EXISTS sandbox_heartbeats (
  sandbox_id        TEXT NOT NULL,
  ts_ms             INTEGER NOT NULL,           -- host clock
  daemon_boot_id    TEXT,                       -- NULL ⇒ snapshot op failed (sandbox unreachable)
  reachable         INTEGER NOT NULL,           -- 0/1
  uptime_s          REAL,
  -- layerstack
  manifest_version  INTEGER, manifest_depth INTEGER,
  active_leases     INTEGER, storage_bytes INTEGER, layer_dirs INTEGER, staging_dirs INTEGER,
  -- workspace / overlay
  open_isolated     INTEGER, overlay_mounts INTEGER,
  -- command sessions
  active_sessions   INTEGER, running_sessions INTEGER, completed_unclaimed INTEGER,
  -- plugin
  plugin_services_ok INTEGER, plugin_services_failed INTEGER,
  -- resources (cumulative gauges; host derives rates from deltas)
  cpu_usage_usec    INTEGER, memory_current_bytes INTEGER, memory_peak_bytes INTEGER,
  io_rbytes         INTEGER, io_wbytes INTEGER, process_rss_bytes INTEGER,
  -- daemon internals
  inflight_ops      INTEGER, spool_pending INTEGER, spool_dropped_total INTEGER,
  details_json      TEXT,                       -- bounded long tail (per-service health, per-session ids)
  PRIMARY KEY (sandbox_id, ts_ms)
);
CREATE INDEX IF NOT EXISTS idx_hb_time         ON sandbox_heartbeats(ts_ms);
CREATE INDEX IF NOT EXISTS idx_ops_trace      ON trace_ops(trace_id);
CREATE INDEX IF NOT EXISTS idx_ops_sent       ON trace_ops(sent_at_ms);
CREATE INDEX IF NOT EXISTS idx_ops_status     ON trace_ops(status);
CREATE INDEX IF NOT EXISTS idx_spans_kind     ON trace_spans(kind);
CREATE INDEX IF NOT EXISTS idx_links_id       ON trace_links(link_kind, link_id);
CREATE INDEX IF NOT EXISTS idx_events_span    ON trace_events(trace_id, span_id);
```

Write sequencing and strictness:

1. `trace_ops` row inserted **before** forwarding; insert failure ⇒ mutating
   ops are not forwarded (read-only ops proceed, marked `trace_degraded`).
   Mutability comes from catalog metadata (`OpContract.mutates_state`,
   `eos-operation/src/core/catalog.rs` / `ops.json`); dynamic `plugin.*` ops
   are not in the static catalog and **default to mutating** — fail-closed.
2. Sidecar ingest assigns `seq` in arrival order after the host's own
   `request_received`/`forward_started` events; host appends
   `response_persisted` (or `response_missing`/`uncertain_outcome`) last, so
   the chain is gap-free and authoritative even when daemon batches retry.
3. Host clock is truth (`sent_at_ms`/`received_at_ms`/`host_rtt_ms`); daemon
   timestamps are advisory (`daemon_boot_id` disambiguates respawns).

Acceptance queries (phase gates assert these run and return correct shapes):

```sql
-- (1) Replay one user-visible call as a timeline
SELECT seq, module, event, details_json FROM trace_events
WHERE trace_id=:trace_id ORDER BY seq;

-- (2) Per-step durations + subsystems touched for one op response
SELECT s.kind, s.subsystem, s.duration_us/1e3 ms, s.fields_json
FROM trace_spans s WHERE s.op_id=:op_id ORDER BY s.started_us;

-- (3) Full long-running command lifecycle across ops (exec → stdin → polls → background settle)
SELECT o.op_id, o.op, o.status, o.sent_at_ms FROM trace_ops o
JOIN trace_links l ON l.trace_id=o.trace_id
WHERE l.link_kind='command_session' AND l.link_id=:session_id
ORDER BY o.sent_at_ms;

-- (4) All failed plugin-overlay ops touching isolated workspaces, last 7 days
SELECT * FROM trace_ops
WHERE family='Plugins' AND status IN ('error','rejected')
  AND workspace_route='isolated_workspace'
  AND sent_at_ms > (strftime('%s','now')-7*86400)*1000;
```

Retention: `prune_before(ms)` ships unwired (audit store; policy is an open
question). A derived `trace-<trace_id>.jsonl` export command provides the
human-shareable text form — JSONL is a view, never the record.

TS join story: `op_id` is a 32-hex uuid4 (valid OTel trace-id format); id and
timestamp columns stay OTel-format-compatible so the TS agent (OTel JS per the
migration index) joins its run audit logs to this store without schema
migration. We take the format compatibility, not the OTel Rust SDK.

### Continuous monitoring: heartbeat snapshots

Per-op traces answer "what happened during this request"; they cannot answer
"what state is the sandbox in *right now* / was in at 14:32". That is a
separate, time-series capability:

**Daemon side** — new builtin op `sandbox.status.snapshot` (`Internal`
visibility, `workspace_route = none`, `trace_exempt`). It is an aggregation
over collectors that already exist, plus two small additions:

| Snapshot section | Source (existing unless marked new) |
| --- | --- |
| layerstack: manifest version/depth, active leases, storage bytes, layer/staging dirs | `op_adapter/checkpoint.rs:39-49` (`layer_metrics` internals, called directly) |
| workspace: open isolated workspaces (ids, age, last_activity) | isolation registry (`isolation.list_open` internals) |
| overlay: active overlay mount count | **new** — `/proc/self/mountinfo` scan, same source the teardown inspection already reads |
| command sessions: active/running/completed-unclaimed counts, per-session {id, status, age} | command registry (`command.count` + session table internals) |
| plugin: per-service health (probe status, accepted, pid alive), setup failures | plugin registry (`plugin.status` internals, summarized) |
| resources: cgroup cpu_usage_usec, memory current/peak, io r/w bytes, daemon rss | the samplers in `runtime/response.rs:198-270` — **consolidated into one shared `eos-trace` sampler**, deleting the duplicate in `settle.rs:254` |
| daemon internals: uptime, boot_id, in-flight ops, spool pending/dropped totals | dispatcher uptime, in-flight registry, trace spool counters |

The snapshot is a typed `SandboxStatusSnapshot` DTO in `eos-trace` (shared
with the host), not a loose JSON map. Cost: registry reads + three procfs/
cgroupfs file reads — no workspace or LayerStack mutation, safe at short
intervals.

**Host side** — `HeartbeatMonitor` in `eos-sandbox-host`: one interval task
per acquired sandbox (`HostConfig.heartbeat_interval_ms`, default 10 000; 0 =
disabled) that calls the snapshot op and inserts a `sandbox_heartbeats` row.
Semantics:

- **Liveness**: a failed/timed-out snapshot still inserts a row with
  `reachable = 0` and NULL gauges — silence is recorded, never inferred. A
  `daemon_boot_id` change between consecutive rows marks a respawn.
- **Rates**: cumulative counters (cpu_usage_usec, io bytes) are stored raw;
  utilization/rates are derived at query time from row deltas — the store
  never loses the raw gauge to pre-computation.
- **Status derivation** (query-time view, not stored): `degraded` when plugin
  services report failures, leases pile up beyond config, or spool drops are
  growing; `unreachable` after N consecutive `reachable = 0` rows. Thresholds
  live host-side; the daemon only reports facts.
- Heartbeat rows are **not** traces: they are a time series beside the trace
  tables, joinable by `sandbox_id` + time window (e.g. "show heartbeats
  bracketing this slow op").

Monitoring queries the schema must answer:

```sql
-- Current state of every sandbox (latest row per sandbox)
SELECT * FROM sandbox_heartbeats h
WHERE ts_ms = (SELECT MAX(ts_ms) FROM sandbox_heartbeats WHERE sandbox_id = h.sandbox_id);

-- CPU/memory trend for one sandbox over the last hour (rates from deltas)
SELECT ts_ms,
       (cpu_usage_usec - LAG(cpu_usage_usec) OVER w) * 1.0
         / ((ts_ms - LAG(ts_ms) OVER w) * 1000) AS cpu_util,
       memory_current_bytes
FROM sandbox_heartbeats WHERE sandbox_id = :id
  AND ts_ms > (strftime('%s','now')-3600)*1000
WINDOW w AS (ORDER BY ts_ms);

-- What was the sandbox doing when this op was slow?
SELECT * FROM sandbox_heartbeats
WHERE sandbox_id = :sandbox_id
  AND ts_ms BETWEEN :op_sent_at_ms - 30000 AND :op_received_at_ms + 30000;
```

Operator surface: `eos-sandbox-host` (or `xtask`) gains `sandbox status
[<sandbox_id>] [--watch]` rendering the latest snapshot per sandbox and
tailing new rows — the human view over the same table.

### Libraries

| Crate | Version | Where | Role |
| --- | --- | --- | --- |
| `tracing` | 0.1 (MIT) | daemon, operation, layerstack, workspace, command-session, plugin, eos-trace | span/event facade |
| `tracing-subscriber` | 0.3, `registry`,`std`,`fmt`,`json` (MIT) | eos-trace, eos-daemon | Registry + custom Layer; JSON fmt layer for the crash log |
| `rusqlite` | 0.40 `bundled` (MIT) | eos-sandbox-host only | host store; daemon binary unaffected |
| `sha2` (already in workspace) | — | host | response digests |
| existing serde/serde_json/uuid/thiserror/tokio | — | — | reused |

Rejected: OTel Rust SDK (pre-1.0 churn in the static daemon binary; format
compatibility kept instead); `tracing-chrome`/`tracing-tracy` (profiling
viewers, not audit persistence); `minitrace`/`fastrace` (thread-local-only
parenting fights the `spawn_blocking`/commit-worker handoffs); `sqlx`/`diesel`
(overkill for one single-writer store).

## Part B — Response Contract

One envelope for every op. `status` is the single discriminant; arms carry
`result` XOR `error` (never null pairs); everything cross-cutting lives in
`meta`. Rendered in `eos-operation/src/core/envelope.rs`, replacing
`OpResponse` and every ad-hoc `json!` site.

```rust
#[derive(Serialize)]
#[serde(tag = "status", rename_all = "snake_case")]
pub enum OperationEnvelope<T: Serialize> {
    Ok       { result: T, meta: ResponseMeta },
    Running  { result: T, meta: ResponseMeta },  // accepted; continues via a linked resource
    Cancelled{ result: T, meta: ResponseMeta },  // settled facts of the cancelled work
    TimedOut { result: T, meta: ResponseMeta },
    Rejected {                                   // domain refusal: OCC conflict, policy, isolated-gate
        error: OperationFault,
        #[serde(skip_serializing_if = "Option::is_none")]
        result: Option<T>,                       // partial domain facts when work happened before the
        meta: ResponseMeta,                      // rejection — e.g. a command that ran (exit 0, output
    },                                           // captured) but lost its OCC publish
    Error    { error: OperationFault, meta: ResponseMeta }, // parse/transport/internal/unexpected
}

#[derive(Serialize)]
pub struct ResponseMeta {
    pub protocol_version: u8,                    // 2
    pub op: String,                              // catalog name or dynamic plugin.* op
    pub op_id: OpId,
    pub trace: TraceRef,                         // { trace_id, root_span_id, store: "local_sqlite", event_count }
    #[serde(skip_serializing_if = "Option::is_none")]
    pub caller_id: Option<CallerId>,
    pub workspace_route: WorkspaceRoute,         // { kind, reason? }
    pub duration_ms: f64,                        // derived from the op span — not hand-inserted
    pub modules_touched: Vec<Subsystem>,         // derived from the span tree
    pub steps: Vec<StepSummary>,                 // derived: { kind, duration_us, status } per direct child span
    pub resource_summary: ResourceSummary,       // bounded gauges: changed paths, depth, cgroup peaks
    pub warnings: Vec<String>,
}

#[derive(Serialize)]
pub struct OperationFault {
    pub kind: String,        // rejected → op-policy vocabulary; error → protocol vocabulary
    pub message: String,
    pub details: serde_json::Value,              // {} when empty, never null
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error_id: Option<String>,                // internal errors only; explicit
}
```

`meta` is **rendered from the op's span tree** (closed immediately before
envelope render; the wire write event lands host-side). Timing/resource facts
appear exactly once, in one vocabulary — the span kinds. There is no parallel
hand-maintained timings map anywhere.

Per-family `result` shapes (typed DTOs in each family's `contract.rs`; the
direction, merged from the parallel draft):

| Family | `result` shape |
| --- | --- |
| Files | `{ file: {…} }` for reads; `{ mutation: { status, published, changed_paths, changed_path_kinds, conflict? } }` for writes/edits |
| Command | `{ command: { status, exit_code, output_ref, command_session_id }, mutation?: {…} }`; `Running` omits `mutation` |
| Checkpoint | `{ checkpoint: {…} }` — layer metrics are domain data, not metadata |
| Isolated workspace | `{ isolated_workspace: { open, workspace_handle_id, workspace_root, lifetime_s?, evicted_upperdir_bytes?, inspection? } }` |
| Plugin | `{ plugin: {…}, overlay?: {…} }` — worker result stays under `result.plugin` |
| Control / workspace-run | narrow typed counts/readiness/cancellation objects |

Example success (file write, fast path):

```json
{"status":"ok",
 "result":{"mutation":{"status":"committed","published":true,
           "changed_paths":["src/a.rs"],"changed_path_kinds":{"src/a.rs":"write"}}},
 "meta":{"protocol_version":2,"op":"sandbox.file.write","op_id":"op_6b1a…",
         "trace":{"trace_id":"tr_6b1a…","root_span_id":1,"store":"local_sqlite","event_count":9},
         "workspace_route":{"kind":"fast_path"},
         "duration_ms":4.2,
         "modules_touched":["dispatch","op","layer_stack","occ"],
         "steps":[{"kind":"dispatch","duration_us":310,"status":"ok"},
                  {"kind":"op.file.write","duration_us":3650,"status":"ok"}],
         "resource_summary":{"changed_path_count":1,"layer_stack_manifest_depth":3},
         "warnings":[]}}
```

Status mapping rules (total over today's observable states):

| Today | Envelope status | Where the detail lives |
| --- | --- | --- |
| command `running` | `running` | `result.command` + `command_session` link |
| command `ok`, publish committed | `ok` | `result.mutation.status = committed` |
| command `ok`, publish lost OCC (aborted_version/overlap) | `rejected` **with partial `result`** (output, exit_code, discarded paths) | `error.kind = occ_conflict`; `result.command` keeps the facts |
| command `cancelled` / `timed_out` | `cancelled` / `timed_out` | `result` carries settled facts (output so far, kill reason) |
| mutation `accepted` (pre-commit OCC ack) | `ok` | `result.mutation.status = accepted` — domain data, not an envelope state |
| `Refused(OpError)` policy refusals, isolated-gate, lifecycle-in-progress | `rejected` | `error.kind` keeps the op-policy vocabulary |
| parse/transport/auth/unknown-op/internal | `error` | `error.kind` keeps the protocol vocabulary; `error_id` for internal |

Example rejection (OCC conflict):

```json
{"status":"rejected",
 "error":{"kind":"occ_conflict","message":"path contended: src/a.rs",
          "details":{"conflict_file":"src/a.rs","reason":"aborted_overlap"}},
 "meta":{"…":"…","workspace_route":{"kind":"ephemeral_workspace"}}}
```

Framer note: `op` lives inside `meta`, never top-level — `decode_value`
classifies any object with a top-level `op` key as a Request
(`eos-daemon/src/wire/message.rs:139`). `WireMessage` disambiguation is
updated to the new shapes (`status` + `error`) in the same phase that lands
the envelope.

Migration mechanics: a v1 flattening adapter (typed envelope → today's flat
shape) exists **only** while families migrate, so each family can flip
independently; gateway/host/e2e assertions for a family are rewritten in the
same PR that flips it. The adapter and `is_success`/`error_kind` helpers are
deleted in the final phase. No deprecation period beyond the ladder itself —
nothing outside this repo consumes the wire today.

## Phased Plan

| Phase | Scope | Verification gate |
| --- | --- | --- |
| A — contracts first | `eos-trace` crate (records, route kinds, spool, layer, bounded-detail helpers); `OperationEnvelope`/`ResponseMeta`/`OperationFault` + per-family result DTO skeletons in `eos-operation`; serialization goldens incl. v1-flattening adapter | `cargo test -p eos-trace -p eos-operation`; envelope/adapter golden tests |
| B — host store | `trace_store.rs` + DDL; request-start fail-closed rule; sidecar-ingest + seq assignment; degraded/uncertain/missing paths; lookup helpers by trace_id/op_id/link | `cargo test -p eos-sandbox-host` incl. "mutating op does not forward when request-start insert fails" |
| C — daemon propagation | `trace` field on `Request` + host encoding; root span in `handle_connection` (covers wire failures + cancelled invocations); `dispatch` + `op.*` spans; route recording at the four decision sites; sidecar assembly at finalize; export op + spool for background; crash-log fmt layer | `cargo test -p eos-daemon` — tree tests via current-thread `with_default`; wire tests updated for the new request/response shapes |
| D — subsystem events | Full phase-event vocabulary: layerstack/OCC (worker span handoff), overlay, command session (incl. `ActiveCommand` origin stash + background settle roots), isolated lifecycle phases, plugin ensure/PPC/overlay/callbacks, resource samples | per-crate tests; live e2e trace suite: file fast_path, exec ephemeral, isolated enter/exec/exit, **sweeper-cancelled session yields a `CommandSettle` background trace**, chain replay query returns the full timeline |
| E — response migration (destructive) | Step 0: host/gateway gain a shape-aware decoder (`status` field present → envelope path; else legacy) so a per-family mixed wire decodes correctly — `is_success` (`protocol.rs:146`) returns `true` for new-shape errors and must be confined to the legacy branch before any family flips. Then family-by-family flip: control/checkpoint → files → isolated → command → plugin; each flip rewrites that family's gateway/host/e2e assertions in the same change; delete quirk serializers, dotted-timings plumbing, `merge_runner_timings`, `json!` envelopes as each family flips | `cargo test --workspace` after each family; e2e suites green on the new assertions before the next family starts; a mixed-wire decode unit test guards step 0 |
| F — debt deletion | Delete the v1 flattening adapter, `is_success`/`error_kind`, `OpResponse`, remaining flat-timings helpers; drift guard: timing-vocabulary exhaustiveness lives in the `SpanKind` enum (compile-checked) | `cargo test --workspace && cargo clippy --workspace`; `grep` gates: no `"success"` branching, no `timings.` string keys outside eos-trace |
| G — TS mirror | `@eos/db` schema mirror (or sibling trace db factory); `@eos/contracts` Zod envelope + trace-ref schemas; run-audit JSONL stays (run lifecycle audit ≠ sandbox op audit) | `pnpm run typecheck && pnpm run lint && pnpm run test` in `eos-agent-core/` |
| H — heartbeat monitoring (independent; lands any time after C) | `sandbox.status.snapshot` op + `SandboxStatusSnapshot` DTO; shared resource sampler in `eos-trace` (deletes the `settle.rs`/`response.rs` duplication); host `HeartbeatMonitor` + `sandbox_heartbeats` table; `sandbox status --watch` CLI | `cargo test -p eos-daemon -p eos-sandbox-host` incl. unreachable-row and boot-id-change tests; live e2e: heartbeats recorded across an exec + isolated enter/exit, gauges monotone, `reachable=0` row when the daemon is killed |

Phases are small and independently landable to merge around concurrent agent
work on `dispatcher.rs`/`op_adapter`.

## Risks

| Risk | Mitigation |
| --- | --- |
| Destructive wire change breaks an unnoticed consumer | Consumer inventory verified (gateway, host, e2e only; no Rust agent-core usage; no TS daemon client yet); each family flip rewrites its consumers in the same change |
| `TraceSpoolLayer` is bespoke (Registry extensions, `Visit`, parent push, `take_op_tree`) | Isolated small module, landed alone in phase A with focused tree tests |
| Sidecar bloats responses | Per-span field budgets + `truncated` flags; bounded-detail rule (sizes/hashes/refs); sidecar carries records, not raw payloads |
| Fail-closed rule turns trace-store outages into op outages | Scoped to mutating ops only (deliberate audit-critical trade); read-only ops degrade with markers; store is local single-writer SQLite — the failure mode is disk-full, which should halt mutations anyway |
| Meta derived from spans at render time (op span must close before envelope) | `take_op_tree` API + a phase C unit test asserting meta.duration equals the op span duration; wire-write event lands host-side by design |
| Cross-thread context loss (OCC worker, future async) | Explicit `Span` handoff + phase D test asserting `occ.commit` parents under the op; pattern documented in eos-trace |
| Daemon crash loses un-exported background traces | Bounded spool + crash-log JSON lines + `daemon_boot_id` gap surfacing; request traces are sidecar-delivered so the loss window is background-only |
| Host DB growth | `prune_before` exists; JSONL export for archiving; policy open |
| e2e assertion rewrite volume (10 files) | Per-family flips keep each rewrite reviewable; replay/chain queries become shared support helpers |

## Open Questions

1. Retention policy for `sandbox-traces.sqlite` (never prune vs age/size cap).
2. PPC callback correlation: callbacks lack a parent message id; tying
   callback spans to the in-plugin parent op needs an additive `PpcMessage`
   field — follow-up, not in these phases.
3. Should `trace_id` minting move into the gateway for multi-client setups, or
   stay in `eos-sandbox-host`? (Recommendation: host owns it; gateway forwards.)
4. Operator-facing trace lookup surface (CLI `trace show <trace_id>` rendering
   the seq timeline + span tree) — proposed as a small follow-up after phase E.
5. Whether all e2e suites run with sidecar assertion helpers or only the trace
   suites — proposal: all, since the envelope migration touches them anyway.
6. Heartbeat retention: raw rows at 10 s intervals are ~8 640/day/sandbox —
   keep raw forever, or downsample to 1-minute aggregates after N days?
   (`prune_before` applies; downsampling needs a decision.)
7. Heartbeat-driven alerting (host process reacting to `degraded`/
   `unreachable`, e.g. notifying the agent runtime) — out of scope here;
   the table is the seam a future watcher consumes.

## Alternatives Considered

Three designs were drafted independently and adversarially judged (integration
cost / audit value / contract fit). The original winner (`tracing-native`,
8/8/8) was built around preserve-first v1 byte-stability; the owner directive
for a destructive, no-debt plan removed that constraint, which reinstates two
ideas the judges had rejected *only* for compat reasons: the response sidecar
(`owned-contract`'s strongest audit property — the chain rides every delivered
response) and the immediate deletion of the quirk serializers. The losing
designs' remaining grafts are incorporated: closed `SpanKind`/`Subsystem`
enums, host-stamped clocks + row-before-send, per-span budgets with
`truncated` flags, e2e pool drain wiring, plugin audit-field capture, and the
sweeper-cancelled-session e2e assertion. The OTel Rust SDK stays rejected
(pre-1.0 churn in the static daemon binary, no in-repo consumer); its id and
schema **format compatibility** is kept for the TS-side join.

The parallel draft `sandbox/docs/sandbox-event-tracing-response-plan.md`
contributed the three-level identity model, the seq-ordered chain + causal-tree
dual view, the cross-op link rows, the per-module phase vocabulary, the
fail-closed persistence rule, gateway declassification, and the six-status
envelope vocabulary — all merged above. Where the two drafts conflicted, this
document resolves: 4-valued `workspace_route` (`fast_path` and `none` split
per owner decision, replacing 3-valued `skip`), spans as the single timing
source (the parallel draft kept response stamping alongside), and hybrid
sidecar+drain delivery (the parallel draft deferred the drain op).
