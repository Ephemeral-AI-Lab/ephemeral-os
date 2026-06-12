# Sandbox Event Tracing and Response Contract

Status: Proposed (rev 3 — destructive posture; verified against the four
audit-system rules: ingestion decoupling, storage immutability,
serialization/schema evolution, visibility/lineage — 13 confirmed gaps closed)
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
| Pretty-JSON final-response crash files as the only command audit | `eos-command-session/src/session.rs` | trace store entries + bounded final-state events (transcript files stay for raw output) |

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
| Hot-path ingestion | Daemon spans/events are bounded in-memory facts only: no SQLite, fsync, host RPC, blocking serialization, unbounded JSON allocation, or cross-sandbox lock waits in rule/dispatch/subsystem decision paths | Core sandbox decisions stay decoupled from audit persistence; overflow is explicit (`dropped_traces`, `dropped_children`, `truncated`) instead of silently slowing the decision engine |
| Canonical serialization | Protobuf `TraceBatch` / `SandboxStatusSnapshot` schemas generated by `prost`; JSON is allowed only for human exports and bounded projection fields | Schema-evolvable, low-overhead payloads for sidecars, export drains, and immutable storage; avoids `serde_json::Value` becoming the audit contract |
| Persistence | SQLite (`rusqlite`, bundled, WAL) on the **host** at `state_dir/sandbox-traces.sqlite` (0600/0700), with append-only hash-chained `audit_entries` as the canonical record and relational tables as query projections | Audit = immutable payload history + chain reconstruction + joins + aggregation. SQL serves lookup; cryptographic chain/seals serve tamper evidence. JSONL only as a derived export, never the system of record |
| Persistence strictness | **Fail-closed for mutating ops**: host records the request-start audit entry before forwarding; if that write fails, the mutating op is not forwarded. Read-only ops proceed with a `trace_degraded` marker | Audit-critical framing: an untraceable mutation is worse than a refused one |
| Workspace route | 4-valued, trace-only: `ephemeral_workspace` \| `isolated_workspace` \| `fast_path` \| `none` | Owner decision. `fast_path` = data-plane work directly against LayerStack with no workspace (direct file merge/read); `none` = pure control plane. Never used for runtime branching — observability only |
| Response contract | Single typed envelope, `status ∈ {ok, running, rejected, cancelled, timed_out, error}` tagged union; domain payload under `result`, fault under `error`, everything else under `meta` | Most readable: one switch tells the consumer what happened; no `success:false`+error-kind double decode; no null pairs |
| Compatibility | None preserved. A short-lived v1 flattening adapter exists only as a migration vehicle inside the phase ladder and is **deleted** in the final phase | No technical debt is the explicit goal; all in-repo consumers migrate in lockstep |

### Non-negotiable audit rules

These are implementation gates, not aspirations:

| Rule | Spec commitment | Verification gate |
| --- | --- | --- |
| A. Decouple rule execution from audit logging | Mechanism crates only emit typed span/event facts into in-memory span state or bounded spool buffers. Host persistence, SQLite locks, fsync, hash sealing, export writing, and transcript archival never run inside the daemon rule/dispatch hot path. | A hot-path unit/bench gate asserts representative dispatch + route decisions remain sub-millisecond with tracing enabled and an intentionally slow host store; no daemon crate may depend on `rusqlite` |
| B. Data storage and immutability | `audit_entries` is append-only, stores canonical protobuf payload bytes, chains every entry by SHA-256, and seals contiguous segments with a signer key. Query tables can update/rebuild, but they are projections. | Store tests verify chain continuity, segment signatures, tamper detection, projection rebuild from `audit_entries`, and mutating-op fail-closed behavior when the pre-forward append fails |
| C. Serialization and schema evolution | Protobuf schemas in `eos-trace/proto/eos/trace/v1` define trace batches, events, spans, resources, links, heartbeats, and response trace refs. JSON is not a daemon-host audit payload. | Golden protobuf compatibility tests keep old fixtures decodable; new fields must be optional/additive or a new schema version |
| D. Visibility and lineage | `trace_id`, `op_id`, `span_id`, host `seq`, `trace_links`, heartbeat rows, indexes, and operator views are mandatory. Every decision can be replayed as both a sequence and a causal tree. | Acceptance queries plus `trace show <trace_id>`, `trace verify`, and `sandbox status --watch` e2e checks must pass before deleting legacy timing surfaces |

## Part A — Event Tracing

### Identity model

| Identity | Source | Meaning |
| --- | --- | --- |
| `trace_id` | Host-minted (uuid4) when starting a user-visible call; propagated to the daemon in the request envelope; reused across every op of a long-lived chain | One user-visible sandbox interaction or one long-lived session chain |
| `op_id` | The existing top-level `invocation_id` (`protocol.rs:118-135`) | One daemon request/response |
| `span_id` | Daemon `AtomicU64` (never reuse `tracing::span::Id` — the Registry recycles them) | One timed unit, parented into a per-op tree |
| `seq` | Host-assigned at ingest, monotonic per `trace_id` | Durable observation order; gap-free even when daemon batches arrive late |
| `daemon_boot_id` | uuid4 per daemon process | Exposes respawn gaps in audit |
| `host_boot_id` | uuid4 per host process | Exposes host-restart gaps — the host is the single writer and seq assigner, so its own crashes are first-class audit facts |

Two views over the same data, both first-class:

| View | Query | Use |
| --- | --- | --- |
| Timeline chain | `WHERE trace_id=? ORDER BY seq` | Audit replay, "what happened next", total-elapsed narrative |
| Causal tree | `span_id`/`parent_span_id` | Nested/parallel work, subsystem ownership, per-step durations |

Cross-op links (`trace_links` rows) tie long-lived resources into chains:
`command_session_id`, `workspace_handle_id`, `plugin_service_instance_id`,
`layer_manifest_version`.

Chain continuity is **host bookkeeping, specified here**, keyed by what later
requests actually carry (verified against the op adapters — isolated ops are
keyed by `caller_id`; `workspace_handle_id` appears only in the enter
response, `op_adapter/isolation.rs:35`):

- `command_session_id → trace_id`: populated from exec responses, consulted
  when later args carry the id (`write_stdin`, `read_progress`/`poll`,
  `collect`, `cancel`), pruned at settle/collect.
- `(sandbox_id, caller_id) → {workspace_handle_id, trace_id}`: populated when
  the isolation-enter response returns `workspace_handle_id`, consulted
  pre-forward for any subsequent op whose args carry that `caller_id` while
  the entry is open (command exec, file ops, isolation status/exit,
  workspace-run cancel) — deliberately mirroring the daemon's
  `command_binding_for(caller_id)` routing key (`op_adapter/command.rs:99`,
  `op_adapter/files.rs:181`); the exit op records into the chain, then prunes
  the entry. Caller-map attribution is predictive — the daemon's recorded
  `route_selected {kind, reason}` is the truth; the host also prunes on
  ingested exit responses and on exported `IsolatedSweep` background traces,
  and a chain-attributed op that returns a non-isolated route is visible in
  `trace_ops` (chain `trace_id` beside actual route). Divergence is
  observable, never silent.

Both maps are rebuildable from `trace_links` after a host restart. The daemon
stashes both the origin `op_id` and the chain `trace_id` in `ActiveCommand` at
exec (`service.rs:340`), so background settle traces carry the chain id even
when the host never polls.

Per-kind link semantics — chain links continue a `trace_id` across ops; tag
links only correlate across otherwise-unrelated traces:

| link_kind | Written when / from | Trace reuse |
| --- | --- | --- |
| `command_session` | Host, at exec-response ingest; id enters the chain map above | Chain |
| `workspace_handle` | Host, at isolation-enter ingest; chained via the `(sandbox_id, caller_id)` map above | Chain |
| `plugin_service` | Host, at sidecar/export ingest: plugin ensure/status spans and `service_started`/`service_health_checked` events carry `service_instance_id` (`eos-plugin/src/service.rs:130`) as a required typed field; `PluginService` background roots carry it the way `CommandSettle` roots carry `command_session_id` | Tag only — never enters a chain map |
| `manifest_version` | Host, at sidecar/export ingest: `snapshot_acquired` (version read against) and `publish_layer_finished`/`auto_squash_finished` (version produced) carry the manifest version as a required typed field | Tag only |

### Flow

```
client ──op,args──> host/gateway
                      │ mint trace_id (or reuse chain's), op_id
                      │ INSERT request-start audit entry ── fail ⇒ mutating op NOT forwarded
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
              response + `_trace_events` sidecar (protobuf TraceBatch) ──> host: ingest, assign seq,
                      │                              update projections (outcome, received_at, rtt)
                      │                              strip sidecar at the gateway
                      ▼
   background work (reaper settle, sweeps) ──> bounded spool ──> `sandbox.trace.export`
                              drain SCHEDULED by sidecar `spool_pending`/heartbeats, RUN by the
                              host's background drainer; exhaustive (synchronous) only at release()
```

### New crate `sandbox/crates/eos-trace`

- `record.rs` — typed DTOs: `TraceId`, `OpId`, `SpanUid`, `TraceRecord`,
  `SpanRecord`, `EventRecord`, `WorkspaceRoute`, `TraceKind`
  (`OpRequest | CommandSettle | SessionSweep | IsolatedSweep | PluginService`),
  closed `SpanKind` enum with exhaustive `subsystem()` mapping
  (`Wire | Dispatch | Op | LayerStack | Overlay | CommandSession | Workspace |
  Plugin | Control`), bounded-detail helpers (sizes/hashes/refs, never raw
  blobs).
- `proto/eos/trace/v1/*.proto` + `codec.rs` — canonical protobuf payloads:
  `TraceBatch`, `TraceSpan`, `TraceEvent`, `TraceResource`, `TraceLink`,
  `RequestStart`, `SandboxStatusSnapshot`, `ResponseTraceRef`, and
  `AuditEntry`. Rust DTOs
  convert into the protobuf schema at sidecar/export/store boundaries; protobuf
  bytes are what the immutable log hashes and seals. JSON is derived after
  ingest for query fields and operator export only.
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

Capture budgets (named configurable defaults; overflow records
`{ truncated: true, sha256, original_len }`, never a silent drop):

| Field | Default budget |
| --- | --- |
| `request_start.args_summary` | 4 KiB |
| span `fields_json` | 2 KiB |
| event `details_json` | 1 KiB |
| `trace_ops.response_summary` | 2 KiB |
| heartbeat `details_json` | 4 KiB |
| per-record sidecar total | 64 KiB — request records cannot spool, so overflow drops children with `dropped_children`, never the root |

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
(`server.rs:350`). Four explicit rules:

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
4. **PPC reader thread**: `PendingCalls::register` captures `Span::current()`
   (the owning op's span — `round_trip_with_callbacks` runs on the op's
   `spawn_blocking` thread) into the pending entry;
   `callback_handler_for_message` returns it with the handler, and the reader
   thread enters it around callback handling. Every callback-driven OCC
   publish therefore parents under the owning op's trace by construction, and
   the nested `occ.commit` enqueue (rule 2) captures the correct span
   transitively. An unresolvable callback (unknown/ambiguous parent id) is
   refused before any handler runs — no mutation is possible on that path;
   the refusal opens a bounded `PluginService` root carrying the plugin
   `service_instance_id` and the claimed parent id. To support this,
   `parent_message_id` is promoted from an opaque body convention
   (`ppc.rs:15-17`) to a typed `Option<String>` field on `PpcMessage` in
   phase D, deleting the body re-parse in callback routing.

`eosd ns-runner` is a separate process and is not instrumented; its mount/tool
timings arrive via `RunResult` as span fields. Test-determinism rule: thread-
local `set_default` subscribers do not reach `spawn_blocking`; daemon trace
tests use `with_default` on current-thread paths or a per-test global default.

### Hot-path ingestion contract

The sandbox decision engine is not allowed to wait for audit persistence.
Instrumentation inside daemon dispatch, route selection, OCC validation,
LayerStack reads, overlay setup/capture, plugin dispatch, command-session state
transitions, and isolated-workspace lifecycle code follows these rules:

1. `tracing` span/event calls record bounded typed fields into in-process span
   state only. No daemon hot-path module may depend on `rusqlite`, open audit
   files, write JSONL exports, sign audit seals, or call back to the host to
   persist trace data.
2. The subscriber layer enforces per-span and per-record budgets before storing
   a field. Oversize values become `{ truncated: true, sha256, original_len }`
   summaries; they do not allocate an unbounded `serde_json::Value`.
3. Request-scoped records are assembled after the operation decision has
   completed and immediately before envelope render. That post-decision encode
   is measured as trace overhead, but it cannot change the decision outcome.
4. Background roots use a bounded spool with non-blocking `try_push` semantics.
   On overflow the oldest background trace is dropped, `dropped_traces` is
   incremented, and the next successful export records the loss. Request-scoped
   sidecars are never dropped silently.
5. Host-side SQLite locks, hash-chain updates, segment signing, WORM export, and
   transcript archival live outside daemon execution. The host may fail closed
   before forwarding a mutating op, but it cannot slow a mutation after the
   daemon has started executing it.

### Transport

Request gains an optional `trace` envelope field (top level, beside
`invocation_id` — a deliberate wire change under the destructive posture):

```json
{"op":"sandbox.command.exec","invocation_id":"op_9f2c…",
 "trace":{"trace_id":"tr_6b1a…","parent_span_id":null},
 "args":{"cmd":"make test","caller_id":"run_1","layer_stack_root":"/eos/layer-stack"}}
```

Responses carry the internal sidecar, stripped by the gateway before any
client sees it (direct daemon clients — the e2e pool — see it and assert it).
The public envelope remains JSON while the internal audit payload is canonical
protobuf bytes, base64-wrapped only because the current daemon transport is a
JSON line protocol:

```json
{"status":"ok","result":{…},"meta":{…},
 "_trace_events":{"schema":"eos.trace.v1.TraceBatch","encoding":"protobuf+base64",
                  "batch_b64":"CiR0cl82YjFh…","spool_pending":2}}
```

The base64 wrapper is not the audit format. Host ingest decodes once, stores the
exact protobuf bytes in `audit_entries.payload`, and populates relational
projection tables from the decoded DTOs. If the daemon transport later becomes a
framed binary protocol, the same `TraceBatch` bytes ride without base64 and the
store schema does not change.

`spool_pending > 0` in a sidecar — or in a heartbeat snapshot once phase H
lands, covering idle sandboxes that receive no forwards — **schedules** a
drain; the host never issues `sandbox.trace.export` on the forwarding caller's
thread, because a drain is a whole extra daemon round trip that must not be
charged to an unrelated agent request. A host-owned background drainer
performs the export round trips: single-flight per sandbox, oldest-first,
looping on `remaining_traces` until empty; drain requests arriving mid-flight
coalesce into the current loop. Deferral is audit-safe by construction: `seq`
is host-assigned at ingest, and spool overflow during the window is already
explicit via `dropped_traces`. The export op is a new catalog op, `Internal`
visibility — the gateway never routes it; in-sandbox callers cannot observe
the audit stream. Export drain is transactional (records removed only after
successful serialization) and `max_bytes`-bounded. Exhaustive drains stay
synchronous where they are teardown correctness rather than latency:
`release()` (`host.rs:122`) stops the sandbox's drainer, then drains to empty
before container removal; the e2e pool keeps its own drain helper (it bypasses
`SandboxHost::forward` — `eos-e2e-test/src/pool.rs:213`).

Loss accounting is explicit everywhere: `dropped_traces` (spool overflow),
`dropped_children`/`truncated` (per-trace caps), `daemon_boot_id` gaps
(daemon crashes), `host_boot` entries + startup reconciliation (host
crashes/restarts), `response_missing`/`uncertain_outcome`/`trace_degraded`
host rows (transport failures). Audit shows gaps; it never silently lies.

Crash forensics: the daemon also installs a `tracing-subscriber` fmt layer
writing JSON lines to the existing `--log-file` (today it only captures raw
stdout/stderr redirection). When the daemon dies mid-op, the log file holds
the structured events that never reached a sidecar or the spool.

### Host persistence (`eos-sandbox-host/src/trace_store.rs`)

Storage layout under the host `state_dir` (0700):

```
<state_dir>/
  sandbox-traces.sqlite          # immutable audit log + query projections, all sandboxes
  sandboxes/<sandbox_id>/        # per-sandbox artifact folder (bulky files, not records)
    daemon.log.jsonl             # structured crash log (fmt layer output, pulled at release/crash)
    exports/trace-<trace_id>.jsonl   # derived human-shareable exports, rebuilt from SQLite
    sessions/<command_session_id>/   # archived command session artifacts
      transcript.log             # PTY output (teed from progress reads + settlement tail fetch)
      stdin.log                  # archived at forward time — host sees stdin first
```

One database, not one per sandbox — deliberately. Cross-sandbox audit ("all
failed plugin-overlay ops touching isolated workspaces last week, any
sandbox") is a core query; SQLite cannot join across hundreds of per-sandbox
files without `ATTACH` gymnastics, and the host process is already the single
writer for every sandbox it owns. `sandbox_id` is a keyed column on every
table. The per-sandbox **folder** exists for what does not belong in a database:
crash logs, derived JSONL exports, and transcript/stdin bulk artifacts.

Inside the database, `audit_entries` is the canonical append-only record. The
relational tables below are projections maintained for fast queries and operator
views. Projection rows may be updated or rebuilt; `audit_entries` may only
append. Per-sandbox deletion is therefore two operations: delete projection rows
and artifact folders when policy allows, then prune sealed `audit_entries`
segments only after the configured retention/export requirement has been met.

`sandbox-traces.sqlite`: 0600 file, WAL, single-writer behind a `Mutex`. No
trait seam — one backend; tests use temp dirs.

```sql
-- Connection-open pragma set (synchronous/foreign_keys are per-connection settings, not schema):
PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL; PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS audit_entries (
  audit_seq             INTEGER PRIMARY KEY AUTOINCREMENT,
  sandbox_id            TEXT NOT NULL,
  trace_id              TEXT NOT NULL,
  op_id                 TEXT,
  entry_kind            TEXT NOT NULL,              -- request_start|trace_batch|response_persisted|heartbeat
                                                    -- |transcript_ref|loss|trace_degraded|projection_rebuilt
                                                    -- |host_boot|prune|seal
  schema_name           TEXT NOT NULL,              -- eos.trace.v1.TraceBatch, etc.
  schema_version        INTEGER NOT NULL,
  received_at_ms        INTEGER NOT NULL,           -- host clock
  payload               BLOB NOT NULL,              -- canonical protobuf bytes
  payload_sha256        TEXT NOT NULL,
  prev_global_sha256    TEXT,                       -- total host-owned chain
  prev_sandbox_sha256   TEXT,                       -- per-sandbox chain for scoped verification
  entry_sha256          TEXT NOT NULL UNIQUE,       -- hash over header + payload + prev hashes
  segment_id            TEXT,
  key_id                TEXT,
  signature             BLOB                        -- present for seal entries; NULL for ordinary rows
);
CREATE TABLE IF NOT EXISTS audit_segment_seals (
  segment_id       TEXT PRIMARY KEY,
  first_audit_seq  INTEGER NOT NULL,
  last_audit_seq   INTEGER NOT NULL,
  root_sha256      TEXT NOT NULL,
  key_id           TEXT NOT NULL,
  signature        BLOB NOT NULL,
  sealed_at_ms     INTEGER NOT NULL,
  export_ref       TEXT                             -- WORM/object-lock path or external anchor id
);
CREATE TABLE IF NOT EXISTS trace_ops (
  op_id            TEXT PRIMARY KEY,          -- invocation_id
  trace_id         TEXT NOT NULL,
  sandbox_id       TEXT NOT NULL,
  op               TEXT NOT NULL,
  family           TEXT NOT NULL,             -- catalog OpFamily
  caller_id        TEXT,
  args_summary     TEXT,                      -- budgeted JSON projection of request args (from request_start)
  args_digest      TEXT,                      -- sha256 of the full args bytes as forwarded
  workspace_route  TEXT CHECK (workspace_route IN
    ('ephemeral_workspace','isolated_workspace','fast_path','none') OR workspace_route IS NULL),
  status           TEXT,                      -- envelope status; NULL = in flight; 'uncertain' after
                                              -- startup reconciliation of a prior boot's orphans
  error_kind       TEXT,
  sent_at_ms       INTEGER NOT NULL,          -- host clock, written BEFORE forward (fail-closed gate)
  received_at_ms   INTEGER,
  host_rtt_ms      INTEGER,
  duration_us      INTEGER,                   -- daemon op span duration (advisory clock)
  daemon_boot_id   TEXT,
  host_boot_id     TEXT NOT NULL,             -- host process that forwarded this op
  modules_touched  TEXT,                      -- JSON array of subsystems (denormalized rollup)
  response_digest  TEXT,                      -- sha256 of the exact response wire bytes as received
                                              -- (sidecar included), computed once at ingest — never
                                              -- from a re-serialized Value
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
-- trace_resources: time-series gauge samples, deliberately no PRIMARY KEY — rows have no per-row
-- identity (concurrent spans may emit the same kind in the same microsecond); duplicate detection
-- and integrity belong to the audit chain, since this is a projection rebuildable from audit_entries.
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
CREATE INDEX IF NOT EXISTS idx_audit_trace     ON audit_entries(trace_id, audit_seq);
CREATE INDEX IF NOT EXISTS idx_audit_sandbox   ON audit_entries(sandbox_id, audit_seq);
CREATE INDEX IF NOT EXISTS idx_audit_op        ON audit_entries(op_id);
CREATE INDEX IF NOT EXISTS idx_ops_trace      ON trace_ops(trace_id);
CREATE INDEX IF NOT EXISTS idx_ops_sent       ON trace_ops(sent_at_ms);
CREATE INDEX IF NOT EXISTS idx_ops_status     ON trace_ops(status);
CREATE INDEX IF NOT EXISTS idx_spans_kind     ON trace_spans(kind);
CREATE INDEX IF NOT EXISTS idx_spans_op       ON trace_spans(op_id);
CREATE INDEX IF NOT EXISTS idx_events_op      ON trace_events(op_id);
CREATE INDEX IF NOT EXISTS idx_resources_trace ON trace_resources(trace_id, ts_us);
CREATE INDEX IF NOT EXISTS idx_links_id       ON trace_links(link_kind, link_id);
CREATE INDEX IF NOT EXISTS idx_events_span    ON trace_events(trace_id, span_id);
```

The per-op indexes are plain — deliberately not partial and not composite:
NULL `op_id` background rows are never matched by `op_id = :op_id` lookups,
and per-op span counts are small enough that the `ORDER BY started_us` sort is
trivial.

Immutability and compliance contract:

- Every host-observed fact first becomes an `audit_entries` row with canonical
  protobuf bytes. Query tables are populated in the same transaction when
  possible; on startup the host can rebuild every projection from
  `audit_entries`.
- `entry_sha256 = sha256(canonical_header || payload_sha256 ||
  prev_global_sha256 || prev_sandbox_sha256)`. This gives both a total host
  chain and an independently verifiable per-sandbox chain.
- Segment sealing is mandatory before retention pruning or external export:
  contiguous `audit_seq` ranges are sealed into `audit_segment_seals` with a
  signer key (`key_id`) and exported/anchored via `export_ref`. A local SQLite
  file alone is not a regulatory immutability guarantee if an attacker can
  rewrite the whole file; the segment seal is the durable tamper-evidence unit.
- Pruning operates only on whole sealed segments — never partial segments,
  never unsealed entries; `audit_segment_seals` rows are never pruned — and
  appends a `prune` tombstone entry **before** deleting rows, recording the
  pruned `segment_id`s, first/last `audit_seq`, entry and trace counts, and
  each pruned segment's `root_sha256`. The tombstone is hash-chained and later
  sealed like any entry, so deletion lives inside the auditable record;
  `trace verify` bridges a pruned segment via seal + tombstone and reports it
  as "pruned (sealed, anchored)" rather than tamper — verification stays
  total after pruning.
- Ordinary events never mutate prior audit rows. Corrections and projection
  rebuilds append new entries (`loss`, `trace_degraded`, `projection_rebuilt`,
  `seal`) instead of editing history.
- Raw stdout/stderr, file contents, and plugin result blobs still stay out of
  protobuf payloads. The immutable entry stores refs, byte counts, hashes, and
  truncation markers; bulky artifacts live under the per-sandbox artifact tree.

Store and schema versioning:

- `trace_store.rs` stamps `PRAGMA user_version = <store schema rev>` on
  create. On open, an older version runs forward-only migrations in one
  transaction; a **newer** version refuses to open — the host never writes
  through a schema it does not understand (consistent with fail-closed: a
  refused store halts mutations). Projection tables may migrate by
  drop-and-rebuild from `audit_entries`; `audit_entries` and
  `audit_segment_seals` DDL changes must be additive-only.
- Ingest skew rule: the host derives the `schema_version` column from the
  sidecar's declared schema name (`eos.trace.v1.TraceBatch` → 1). An unknown
  schema/version (daemon newer than the host during rolling dev) still
  appends the `audit_entries` row — canonical bytes are never dropped — but
  skips projections and appends a `loss` entry marking `projection_skipped`;
  a later host re-runs projection rebuild to backfill.

Write sequencing and strictness:

1. A `request_start` audit entry and `trace_ops` projection row are inserted
   and durably committed **before** forwarding (WAL + `synchronous=FULL`
   syncs the WAL on every commit, so a successful request-start append is
   power-loss durable before the mutating op runs; at human-agent op rates
   plus one heartbeat row per 10 s per sandbox, the per-commit fsync is
   immaterial on a single-host store); insert failure ⇒ mutating ops are not
   forwarded (read-only ops proceed, marked `trace_degraded`). The
   `request_start` payload is `eos.trace.v1.RequestStart { op, op_id,
   trace_id, sandbox_id, caller_id, host_boot_id, args_summary (canonical
   JSON bytes, budgeted), args_len, args_digest (sha256 of the full args
   bytes), truncated }` — computed by the host from the request it already
   holds, zero daemon hot-path cost; `trace_ops.args_summary`/`args_digest`
   are projections of it. The daemon op span keeps its separate parsed-args
   summary: raw-args-as-sent (host) vs args-as-parsed (daemon) diverge
   exactly when parse bugs occur — audit signal, not duplication.
   Mutability comes from catalog metadata (`OpContract.mutates_state`,
   `eos-operation/src/core/catalog.rs` / `ops.json`); dynamic `plugin.*` ops
   are not in the static catalog and **default to mutating** — fail-closed.
2. Sidecar ingest decodes one protobuf `TraceBatch`, appends a `trace_batch`
   audit entry, assigns `seq` in arrival order after the host's own
   `request_received`/`forward_started` events, then updates projections. Host
   appends `response_persisted` (or `response_missing`/`uncertain_outcome`) last,
   so the chain is gap-free and authoritative even when daemon batches retry.
   `response_digest` is computed here, over the exact framed response bytes as
   received from the daemon (sidecar included) — never over a re-serialized
   `Value` (serde_json is `preserve_order` across the wire crates; bytes-as-
   received is the only defined digest input) — and is carried in the
   `response_persisted` entry payload, so it is hash-chained and survives
   projection rebuild. The digest is an ingest-time commitment binding what
   the daemon sent: joinable against direct-daemon wire copies (the e2e pool
   sees pre-strip bytes), deliberately not against gateway-stripped client
   copies.
3. Host clock is truth (`sent_at_ms`/`received_at_ms`/`host_rtt_ms`); daemon
   timestamps are advisory (`daemon_boot_id` disambiguates respawns).
4. Heartbeat snapshots append `heartbeat` audit entries before inserting the
   `sandbox_heartbeats` projection row. A failed snapshot appends a loss/failure
   entry and still inserts a `reachable = 0` projection row.
5. On startup the host appends a `host_boot` audit entry recording its new
   `host_boot_id`, rebuilds/repairs projections, then reconciles orphans:
   every `trace_ops` row with `status IS NULL` from a prior `host_boot_id` is
   necessarily orphaned (a restarting host has no in-flight forwards) — for
   each, append an `uncertain_outcome` loss entry (`entry_kind = 'loss'`,
   payload referencing `op_id`, `trace_id`, and the orphaning boot) with a
   host-assigned `seq` in that trace's chain, then set the projection row to
   `status = 'uncertain'`. Append-before-update, so projection rebuild
   reproduces `status = 'uncertain'`.

Acceptance queries (phase gates assert these run, return correct shapes, and
execute via an index: a phase B test runs `EXPLAIN QUERY PLAN` on each and
fails if any trace-table access is a SCAN rather than a SEARCH — bundled
rusqlite pins the SQLite version, so the plan-shape assertion is
deterministic in-repo):

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

-- (5) Fetch the immutable chain slice backing one trace
SELECT audit_seq, entry_kind, payload_sha256, prev_global_sha256,
       prev_sandbox_sha256, entry_sha256
FROM audit_entries
WHERE trace_id=:trace_id ORDER BY audit_seq;
```

Retention: `prune_before(ms)` ships unwired (audit store; policy is an open
question); when wired it operates only on whole sealed segments and appends
the `prune` tombstone described above before deleting — it refuses to delete
unsealed `audit_entries`. A derived
`trace-<trace_id>.jsonl` export command provides the human-shareable text form
from projections + protobuf payloads — JSONL is a view, never the record.

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
with the host) and encoded as protobuf for host ingest, not a loose JSON map.
Cost: registry reads + three procfs/cgroupfs file reads — no workspace or
LayerStack mutation, safe at short intervals.

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
- A snapshot reporting `spool_pending > 0` schedules a background-drainer
  pass (see Transport) — heartbeats cover idle sandboxes that receive no
  forwards, e.g. sweeper-cancelled sessions the host never polls.
- Heartbeat rows are **not** traces: they are a time series beside the trace
  tables, joinable by `sandbox_id` + time window (e.g. "show heartbeats
  bracketing this slow op"). They are still audit-backed: every successful or
  failed snapshot appends an `audit_entries` row before the projection insert.

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

### Command transcripts: live plane vs archive plane

PTY transcripts (`transcript.log`) and `final.json` live in the session dir
(`eos-operation/src/command/prepare.rs:118-119`) and are destroyed with it at
reap/recovery (`command/runtime.rs:102`). `read_command_progress` tail-reads
the transcript just-in-time (`eos-command-session/src/session.rs:183-184`).
Two consumers with **different requirements** — naming them dissolves the
push-vs-pull dilemma:

| Consumer | Requirement | Served by |
| --- | --- | --- |
| Agent progress reads (`read_command_progress`) | Zero-latency truth, right now | The in-sandbox transcript, pulled per request — **unchanged**. A host mirror is always stale; it can never serve this |
| Audit archive | Completeness + durability | Host copy whose deadline is **destruction time, not real time** |

Neither a daemon push channel nor a faster heartbeat is the answer:

- **No push.** The daemon is a pure server (one request/response per inbound
  connection, `server.rs:262`); push means daemon-initiated egress plus a
  host-side listener — exactly the connectivity a sandbox must not have. It
  also buys freshness the audit plane doesn't need.
- **No heartbeat cranking.** Heartbeats are a fixed-size state time series;
  transcripts are unbounded bulk data. Raising the snapshot frequency to chase
  transcript bytes conflates the two planes and still loses the
  destruction race.

Instead, **destruction-gated archival** with a free progressive tee:

1. **stdin needs no transfer at all**: the host forwards `write_stdin` and
   archives the payload at forward time — it sees stdin before the sandbox
   does. Stored as `sandboxes/<sandbox_id>/sessions/<session_id>/stdin.log`
   with ts + op_id prefixes.
2. **Tee what already crosses the wire**: every `read_command_progress`
   response carries transcript lines the agent paid for anyway; the host tees
   them into `sessions/<session_id>/transcript.log`, tracking the archived
   byte offset. Zero extra round trips during the session.
3. **Settlement fetch of the un-teed tail**: at settle/collect the host
   fetches the remaining bytes by offset (ranged `sandbox.command.transcript`
   read, `Internal` visibility), bounded by
   `transcript_archive_max_bytes` (config; truncation recorded with a
   `truncated` marker + full-length sha256 so tampering/loss is evident).
4. **Destruction is gated on the archive ack**: the session dir survives the
   reap until the host confirms the archive (the completion already waits in
   the completed buffer for collection — the dir adopts the same holding
   pattern), with a TTL fallback so an absent host cannot fill sandbox disk;
   a TTL-fired deletion writes a `transcript_lost` event into the trace chain
   instead of losing it silently. Isolated-workspace exit orders an archive
   step **before** `rmtree_scratch`. The orphan-recovery path
   (`runtime.rs:60-102`) retains the dir under the same gate.

The database stores references, never the bulk (bounded-detail rule):
`trace_links` ties `command_session` → trace; the settle trace appends a
`transcript_ref` audit entry and projection detail
`{transcript_path, stdin_path, bytes, sha256, truncated}`. JIT progress reads
keep hitting the daemon; auditors read the archived files; the two planes never
trade their requirements against each other.

### Libraries

| Crate | Version | Where | Role |
| --- | --- | --- | --- |
| `tracing` | 0.1 (MIT) | daemon, operation, layerstack, workspace, command-session, plugin, eos-trace | span/event facade |
| `tracing-subscriber` | 0.3, `registry`,`std`,`fmt`,`json` (MIT) | eos-trace, eos-daemon | Registry + custom Layer; JSON fmt layer for the crash log |
| `rusqlite` | 0.40 `bundled` (MIT) | eos-sandbox-host only | host store; daemon binary unaffected |
| `prost` / `prost-build` | workspace-pinned | eos-trace | protobuf DTO generation and canonical audit payload encoding |
| `base64` | workspace-pinned | eos-daemon/eos-sandbox-host | temporary JSON-line transport wrapper for protobuf sidecars |
| `sha2` (already in workspace) | — | host | response digests, payload digests, hash-chain entries |
| signer (`ed25519-dalek` or host signer provider) | workspace-pinned / configured | eos-sandbox-host | segment-seal signatures over immutable audit ranges |
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
| A — contracts first | `eos-trace` crate (records, protobuf schema/codegen, route kinds, spool, layer, bounded-detail helpers); `OperationEnvelope`/`ResponseMeta`/`OperationFault` + per-family result DTO skeletons in `eos-operation`; serialization goldens incl. v1-flattening adapter | `cargo test -p eos-trace -p eos-operation`; protobuf backward-compatibility goldens; envelope/adapter golden tests |
| B — host store | `trace_store.rs` + DDL; append-only `audit_entries`, hash-chain continuity, segment seals, projection rebuild; `user_version` stamping + forward-only migrations; request-start fail-closed rule (durable commit, `RequestStart` payload incl. args capture); host_boot entry + startup reconciliation; prune tombstones; protobuf sidecar-ingest + seq assignment; degraded/uncertain/missing paths; lookup helpers by trace_id/op_id/link | `cargo test -p eos-sandbox-host` incl. "mutating op does not forward when request-start append fails", tamper detection, segment-signature verification, projection rebuild from `audit_entries`; pragma posture (journal_mode=wal, synchronous=FULL on the live connection); `EXPLAIN QUERY PLAN` plan-shape on all acceptance queries; seal → prune → verify reports the pruned range; refuse-to-open-newer `user_version`; NULL-status rows from a prior boot become `uncertain` with a chained loss entry |
| C — daemon propagation | `trace` field on `Request` + host encoding; root span in `handle_connection` (covers wire failures + cancelled invocations); `dispatch` + `op.*` spans; route recording at the four decision sites; protobuf sidecar assembly at finalize; export op + bounded non-blocking spool for background; host background drainer (single-flight per sandbox, never on the forwarding caller's thread); crash-log fmt layer | `cargo test -p eos-daemon` — tree tests via current-thread `with_default`; wire tests updated for the new request/response shapes; hot-path test asserts no daemon dependency on host-store crates and bounded trace overhead |
| D — subsystem events | Full phase-event vocabulary: layerstack/OCC (worker span handoff), overlay, command session (incl. `ActiveCommand` origin stash + background settle roots), isolated lifecycle phases, plugin ensure/PPC/overlay/callbacks (incl. typed `parent_message_id` on `PpcMessage` + reader-thread span handoff per rule 4), resource samples | per-crate tests; live e2e trace suite: file fast_path, exec ephemeral, isolated enter/exec/exit, **sweeper-cancelled session yields a `CommandSettle` background trace**, callback-driven OCC publish parents under the owning op's trace, chain replay query returns the full timeline |
| E — response migration (destructive) | Step 0: host/gateway gain a shape-aware decoder (`status` field present → envelope path; else legacy) so a per-family mixed wire decodes correctly — `is_success` (`protocol.rs:146`) returns `true` for new-shape errors and must be confined to the legacy branch before any family flips. Then family-by-family flip: control/checkpoint → files → isolated → command → plugin; each flip rewrites that family's gateway/host/e2e assertions in the same change; delete quirk serializers, dotted-timings plumbing, `merge_runner_timings`, `json!` envelopes as each family flips | `cargo test --workspace` after each family; e2e suites green on the new assertions before the next family starts; a mixed-wire decode unit test guards step 0 |
| F — debt deletion | Delete the v1 flattening adapter, `is_success`/`error_kind`, `OpResponse`, remaining flat-timings helpers; drift guard: timing-vocabulary exhaustiveness lives in the `SpanKind` enum (compile-checked); JSON trace payload helpers deleted outside exports/projections | `cargo test --workspace && cargo clippy --workspace`; `grep` gates: no `"success"` branching, no `timings.` string keys outside eos-trace, no daemon-host audit payload as `serde_json::Value` |
| G — TS mirror | `@eos/db` schema mirror (or sibling trace db factory); `@eos/contracts` Zod envelope + trace-ref schemas; run-audit JSONL stays (run lifecycle audit ≠ sandbox op audit) | `pnpm run typecheck && pnpm run lint && pnpm run test` in `eos-agent-core/` |
| H — heartbeat monitoring (independent; lands any time after C) | `sandbox.status.snapshot` op + protobuf `SandboxStatusSnapshot` DTO; shared resource sampler in `eos-trace` (deletes the `settle.rs`/`response.rs` duplication); host `HeartbeatMonitor` + `sandbox_heartbeats` projection; heartbeat audit entries; `sandbox status --watch` CLI | `cargo test -p eos-daemon -p eos-sandbox-host` incl. unreachable-row and boot-id-change tests; live e2e: heartbeats recorded across an exec + isolated enter/exit, gauges monotone, `reachable=0` row when the daemon is killed, heartbeat chain verifies |
| I — transcript archival (after D) | stdin archive at forward time; progress-read tee with byte offsets; ranged `sandbox.command.transcript` tail fetch; destruction gated on archive ack + TTL fallback with `transcript_lost` event; isolated-exit archive step before `rmtree_scratch`; orphan-recovery retention | `cargo test -p eos-command-session -p eos-operation -p eos-sandbox-host`; live e2e: long-running exec with stdin + polls yields a byte-complete archived transcript (sha matches a direct in-session read taken before settle); kill-host-then-TTL case writes `transcript_lost`; JIT progress reads unchanged (latency assertion vs baseline) |
| J — operator lineage views | `trace show <trace_id>` renders seq timeline + causal tree + immutable-chain proof; `trace verify [--sandbox <id>]` verifies hashes/seals; `trace heartbeats <trace_id>` shows bracketing status rows | CLI/e2e checks reconstruct one file op, one command chain, one isolated lifecycle, and one plugin overlay from store data only; tampered DB fixture fails verification |

Phases are small and independently landable to merge around concurrent agent
work on `dispatcher.rs`/`op_adapter`.

## Risks

| Risk | Mitigation |
| --- | --- |
| Destructive wire change breaks an unnoticed consumer | Consumer inventory verified (gateway, host, e2e only; no Rust agent-core usage; no TS daemon client yet); each family flip rewrites its consumers in the same change |
| `TraceSpoolLayer` is bespoke (Registry extensions, `Visit`, parent push, `take_op_tree`) | Isolated small module, landed alone in phase A with focused tree tests |
| Sidecar bloats responses | Protobuf sidecar, per-span field budgets + `truncated` flags; bounded-detail rule (sizes/hashes/refs); sidecar carries records, not raw payloads; base64 wrapper is temporary transport glue only |
| Fail-closed rule turns trace-store outages into op outages | Scoped to mutating ops only (deliberate audit-critical trade); read-only ops degrade with markers; store is local single-writer SQLite — the failure mode is disk-full, which should halt mutations anyway |
| Meta derived from spans at render time (op span must close before envelope) | `take_op_tree` API + a phase C unit test asserting meta.duration equals the op span duration; wire-write event lands host-side by design |
| Cross-thread context loss (OCC worker, future async) | Explicit `Span` handoff + phase D test asserting `occ.commit` parents under the op; pattern documented in eos-trace |
| Daemon crash loses un-exported background traces | Bounded spool + crash-log JSON lines + `daemon_boot_id` gap surfacing; request traces are sidecar-delivered so the loss window is background-only |
| Query projections drift from immutable audit entries | Projection rebuild from `audit_entries` is a phase B test and a host startup repair path; projection-only facts are forbidden |
| Local hash chain can be rewritten by an attacker with full disk control | Segment seals include signer key id + signature and are exported/anchored via `export_ref`; compliance mode requires external/WORM anchoring before retention pruning |
| Host DB growth | `prune_before` exists; JSONL export for archiving; policy open |
| e2e assertion rewrite volume (10 files) | Per-family flips keep each rewrite reviewable; replay/chain queries become shared support helpers |

## Open Questions

1. Retention policy for `sandbox-traces.sqlite` and sealed audit segments
   (never prune vs age/size cap; WORM export required before pruning).
2. Should `trace_id` minting move into the gateway for multi-client setups, or
   stay in `eos-sandbox-host`? (Recommendation: host owns it; gateway forwards.)
3. Audit segment signer source: host-local 0600 key in dev, OS keychain, or
   external signing service/HSM in compliance deployments. The schema supports
   all via `key_id`; the default needs an implementation choice.
4. Whether all e2e suites run with sidecar assertion helpers or only the trace
   suites — proposal: all, since the envelope migration touches them anyway.
5. Heartbeat retention: raw rows at 10 s intervals are ~8 640/day/sandbox —
   keep raw forever, or downsample to 1-minute aggregates after N days?
   (`prune_before` applies; downsampling needs a decision.)
6. Heartbeat-driven alerting (host process reacting to `degraded`/
   `unreachable`, e.g. notifying the agent runtime) — out of scope here;
   the table is the seam a future watcher consumes.

Resolved since rev 2: PPC callback correlation (formerly open) is now
specified — context-propagation rule 4 plus the typed `parent_message_id`
field on `PpcMessage`, landing in phase D.

## Alternatives Considered

Three designs were drafted independently and adversarially judged (integration
cost / audit value / contract fit). The original winner (`tracing-native`,
8/8/8) was built around preserve-first v1 byte-stability; the owner directive
for a destructive, no-debt plan removed that constraint, which reinstates two
ideas the judges had rejected *only* for compat reasons: the response sidecar
(`owned-contract`'s strongest audit property — the chain rides every delivered
response) and the immediate deletion of the quirk serializers. The losing
designs' remaining grafts are incorporated: closed `SpanKind`/`Subsystem`
enums, host-stamped clocks + request-start audit append-before-send, protobuf
sidecars, hash-chained immutable entries, per-span budgets with `truncated`
flags, e2e pool drain wiring, plugin audit-field capture, and the
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
