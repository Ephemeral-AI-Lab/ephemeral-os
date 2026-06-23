# Phase 4: Async Method Traces

## Purpose

Phase 4 records one linked async method trace for command finalization. The
trace is linked to the request that created the command, but it is not modeled
as a child span of that request because finalization is driven by the command
completion watcher and finalizer thread after command launch.

The first implementation is intentionally narrow:

- create one `command_finalization` async trace per command terminal
  finalization;
- link it with `origin_request_id`;
- correlate it with `correlation_kind = "command_session_id"` and
  `correlation_id = command_session_id`;
- populate `workspace_id` and `command_session_id`;
- reuse `OperationTrace`, `CompletedOperationTrace`, and
  `CompletedOperationSpan` for finalizer span timing;
- persist only through daemon-owned observability storage.

Phase 4 is not a general async tracing framework. It should be easy to delete
or replace if a later observability API changes shape.

## Current Repo Grounding

Phase 3 and Phase 3.5 helpers are present in live code.
`crates/sandbox-runtime/operation/src/observability.rs` defines
`OperationTrace`, `SpanGuard`, `SpanKey`, `span_keys`,
`CompletedOperationTrace`, `CompletedOperationSpan`, `measure_optional`, and
`measure_optional_if`. `OperationTrace::measure_if` implements Phase 3.5
enabled child spans.

`OperationTrace` currently stores only runtime timing state:

- `state: RefCell<TraceState>`;
- `enabled_span_keys: HashSet<SpanKey>`.

`TraceState` stores `started_at`, `started_at_unix_ms`, `active_stack`,
`completed`, and `next_call_index`. `OperationTrace` exposes `new`,
`new_with_enabled_span_keys`, `enter`, `measure`, `measure_if`, and `complete`.
This is safe to reuse for async finalization because it has no SQLite,
`sandbox-observability`, daemon path, request response, command output, or
transcript dependency. The finalizer can create a fresh `OperationTrace` inside
the finalizer thread and complete it there.

`CompletedOperationTrace` currently has:

- `started_at_unix_ms: i64`;
- `finished_at_unix_ms: i64`;
- `duration_ms: f64`;
- `spans: Vec<CompletedOperationSpan>`.

`CompletedOperationSpan` currently has:

- `parent_call_index: Option<i64>`;
- `method_name: &'static str`;
- `call_index: i64`;
- `status: &'static str`;
- `started_at_unix_ms: i64`;
- `finished_at_unix_ms: i64`;
- `duration_ms: f64`.

Daemon request tracing is owned by
`crates/sandbox-daemon/src/server/dispatch.rs`. `dispatch_request` validates
the sandbox scope, clones `self.observability`, creates
`OperationTrace::new_with_enabled_span_keys(observability.enabled_deep_span_keys())`
only when observability exists and `sandbox_id` is present, runs
`sandbox_runtime::dispatch_operation(&operations, &request, trace.as_ref())`
inside `tokio::task::spawn_blocking`, projects the response with
`into_json_value`, completes the trace with `OperationTrace::complete`, and
calls `DaemonObservability::insert_completed_operation_trace`. Persistence
failures are ignored for the user response.

`DaemonObservability::insert_completed_operation_trace` in
`crates/sandbox-daemon/src/observability/service.rs` currently:

- updates Phase 3.5 enabled deep span keys from the completed request trace;
- derives `trace_id` as `request:<request_id>`;
- derives request trace status and bounded error fields from the projected
  response JSON;
- maps `CompletedOperationTrace` into one `TraceRecord`;
- maps each `CompletedOperationSpan` into one `SpanRecord`;
- derives storage span ids as `trace_id + ":span:" + call_index`;
- derives `parent_span_id` from `parent_call_index`;
- marks the appropriate Phase 3 coarse span on response errors;
- calls `ObservabilityStore::insert_trace`.

`TraceRecord`, `SpanRecord`, and `ObservabilityStore::insert_trace` are enough
for request traces but not enough for Phase 4 async links. In
`crates/sandbox-observability/src/records.rs`, `TraceRecord` has `trace_id`,
`kind`, `status`, `sandbox_id`, `operation`, optional `request_id`, timing, and
error fields. It does not have `origin_request_id`, `async_name`,
`correlation_kind`, `correlation_id`, `workspace_id`, or
`command_session_id`. `SpanRecord` already has enough shape and should not
change. In `crates/sandbox-observability/src/store.rs`,
`ObservabilityStore::insert_trace` writes one trace and its spans in a single
transaction, but the `traces` table and insert SQL currently lack the async
columns. Phase 4 therefore needs a daemon/storage-only V3 migration and
`TraceRecord` extension; it must not add a `sandbox-observability` dependency
from `sandbox-runtime`.

`CommandOperationService` constructs the completion finalizer in
`crates/sandbox-runtime/operation/src/command/service/core.rs`.
`from_parts` creates a `CommandProcessStore`, calls
`spawn_completion_finalizer(Arc::clone(&workspace), Arc::clone(&process_store))`,
and stores the returned `CommandCompletionSender`.

The current completion channel is in
`crates/sandbox-runtime/operation/src/command/service/completion.rs`.
`CommandCompletionSender` wraps `mpsc::Sender<CommandCompletion>` and ignores
send errors. `CommandCompletionPromise` contains `command_session_id`, the
sender, and shared exit state. `CommandCompletion` contains only
`command_session_id` and `process_exit`. `CommandCompletionPromise::resolve`
sets `exited = true` once and sends `CommandCompletion` to the finalizer.
`spawn_completion_finalizer` receives completions on one background thread and
calls `complete_terminal_command_with_services(...)`.

The finalization call path is in
`crates/sandbox-runtime/operation/src/command/service/finalize.rs`:

```text
complete_terminal_command_with_services
  begin_terminal_completion
    process_store.active
    mark_active_completion
  terminal_result
  apply_workspace_completion_policy
    if CommandWorkspaceOwnership::OneShot:
      WorkspaceSessionService::destroy_session
  complete_command_record
    process_store.complete_active
```

During finalization, `command_session_id` is available from
`CommandCompletion`. `begin_terminal_completion` copies
`workspace_session_id`, `workspace_ownership`, `started_at`, transcript state,
and output offset from the active command record into `ActiveCompletionRecord`.
For one-shot commands, `workspace_ownership` contains the boxed
`WorkspaceSessionHandler` needed by `WorkspaceSessionService::destroy_session`.

`origin_request_id` is not available where `CommandCompletionPromise::new` is
called today. It is available in
`crates/sandbox-runtime/operation/src/command/service/impls/exec_command.rs`
inside `dispatch`, because `dispatch` receives the `Request`. The minimal
change is to pass `request.request_id.clone()` from the `exec_command` dispatch
wrapper into `CommandOperationService::exec_command` only when request tracing
is enabled. Do not introduce a broad request context object.

Workspace destroy and remount have different Phase 4 treatment:

- one-shot workspace destroy during terminal command completion currently runs
  inside `apply_workspace_completion_policy` on the command finalizer path, so
  it should be covered by the command-finalization trace;
- one-shot cleanup after command start failure runs synchronously inside
  `exec_command` before a command completion promise exists, so it remains in
  the request trace and does not get a Phase 4 async trace;
- `WorkspaceRemountService::remount_workspace_session` currently runs
  synchronously in
  `crates/sandbox-runtime/operation/src/workspace_remount/service/impls/remount_workspace_session.rs`;
  it does not spawn separate async lifecycle work, so separate workspace
  remount async traces are deferred.

## Architecture Cleanness Check

The smallest design that records one command-finalization async trace is:

```text
origin_request_id captured by exec_command::dispatch
optional AsyncTraceLink carried by CommandCompletionPromise and CommandCompletion
fresh OperationTrace created inside the existing completion finalizer thread
optional daemon-owned AsyncTraceSink callback called after finalization
DaemonObservability maps the completed async trace into storage rows
```

This reduces to the requested minimum: one small async trace link, one optional
trace collector around the existing finalizer path, one narrow daemon-owned
persistence hook, and no runtime storage dependency.

Existing `OperationTrace` and `CompletedOperationTrace` should be reused. Do
not add parallel async span structs. A tiny `CompletedAsyncOperationTrace`
wrapper is justified only to pair the existing completed trace with the async
link and finalizer outcome.

The existing completion channel can carry the link. Add an optional link to
`CommandCompletionPromise` and copy it into `CommandCompletion` in `resolve`.
Do not add a second finalizer thread or a separate async trace worker.

Do not trace `completion_watcher` in Phase 4. The watcher and finalizer are
different threads, and the live `OperationTrace` uses `RefCell` request-local
state rather than a cross-thread `Arc<Mutex<_>>`. Sharing one trace across the
watcher and finalizer would contort the current model. Creating separate
watcher and finalizer traces would add another async trace and more correlation
rules. The useful first trace is the finalizer.

Every new runtime field has one reason to exist:

- the optional sink slot lets daemon-owned observability receive completed
  async traces without exposing SQLite or `sandbox-observability` to runtime;
- the optional link on the promise/completion preserves request and command
  correlation until terminal finalization;
- no field stores response JSON, daemon paths, store handles, command output,
  transcript content, or namespace-runner metadata.

If the runtime change had to fit in 60 non-test LOC, delete these first:

- watcher spans;
- per-span finalizer error attribution;
- separate workspace destroy/remount trace types;
- extra async task type registries;
- any public config surface for async tracing.

Runtime remains responsible only for neutral timing spans and stable runtime
correlation identifiers. Daemon remains responsible for sandbox identity,
storage ids, SQLite rows, bounded strings, and persistence. Command finalization
must still complete when observability is disabled or when persistence fails.

The design is rejected if implementation requires a global event bus, a
registry of async task types, a second finalizer thread, a broad sink trait with
many methods, a runtime observability manager, operation-specific daemon
persistence outside async trace mapping, command response changes, command
transcript changes, or namespace-runner trace metadata. The simpler callback
design works, so use it.

## Scope

Phase 4 includes:

- one linked async trace for command finalization;
- a storage-neutral runtime async trace link;
- an optional runtime callback supplied by daemon construction;
- finalizer-thread span collection using `OperationTrace`;
- daemon/storage mapping for async trace metadata;
- focused tests for disabled observability and enabled linked async metadata.

## Non-Goals

Phase 4 does not implement:

- Phase 4.5 cross-process namespace-runner traces;
- trace propagation into namespace-runner child processes;
- runner internals such as `runner::run`, `run_setns`,
  `shell_exec::execute_shell`, or `wait_for_command_execution_scope`;
- a general async task tracing framework;
- a global event bus;
- a new runtime observability service;
- runtime SQLite writes;
- a `sandbox-observability` dependency from `sandbox-runtime`;
- command transcript or command output ingestion;
- Prometheus, Grafana, Loki, Tempo, OTLP, or log export;
- manager aggregation;
- response envelopes such as `{ result, meta }`;
- public response-shape changes.

## Data Model

Add a runtime-neutral link type in `sandbox-runtime`:

```text
AsyncTraceLink
  origin_request_id: String
  async_name: &'static str
  correlation_kind: &'static str
  correlation_id: String
  workspace_id: Option<WorkspaceSessionId>
  command_session_id: Option<CommandSessionId>
```

For command finalization:

```text
async_name = "command_finalization"
correlation_kind = "command_session_id"
correlation_id = command_session_id
workspace_id = workspace_session_id
command_session_id = command_session_id
```

Do not put `sandbox_id` in the runtime link. The daemon-owned callback is
installed only when daemon observability is enabled, and
`DaemonObservability` already owns the sandbox identity needed for storage.

Add a small wrapper:

```text
CompletedAsyncOperationTrace
  link: AsyncTraceLink
  status: "ok" | "error"
  error_message: Option<String>
  trace: CompletedOperationTrace
```

The wrapper reuses the existing span DTOs. `status` is the finalizer outcome,
not a response envelope.

Use a callback type rather than a trait unless implementation proves a trait is
shorter:

```text
AsyncTraceSink = Arc<dyn Fn(CompletedAsyncOperationTrace) + Send + Sync + 'static>
```

The callback returns `()`. Daemon-side persistence errors are swallowed at the
daemon boundary, matching current request-trace behavior.

Storage trace ids:

```text
trace_id = "async:" + async_name + ":" + correlation_kind + ":" + correlation_id
span_id = trace_id + ":span:" + call_index
```

Do not add a monotonic suffix for command finalization. The live
`CommandCompletionPromise::resolve` sends at most one completion per command
because it sets `exited = true` under a mutex before sending.

Add a V3 storage migration by extending `traces` with nullable async fields:

```text
origin_request_id TEXT
async_name TEXT
correlation_kind TEXT
correlation_id TEXT
workspace_id TEXT
command_session_id TEXT
```

Add indexes only for the fields Phase 4 needs to query later:

```text
idx_traces_origin_request ON traces(sandbox_id, origin_request_id, started_at_unix_ms)
idx_traces_correlation ON traces(sandbox_id, correlation_kind, correlation_id, started_at_unix_ms)
```

Do not add a `trace_links` table in the first Phase 4 implementation. One async
trace has one origin request and one correlation key, so nullable trace columns
are the smallest storage shape. A separate table can be introduced later if
multiple links per trace become real.

## Runtime Changes

In `crates/sandbox-runtime/operation/src/observability.rs`:

- add `AsyncTraceLink`;
- add `CompletedAsyncOperationTrace`;
- add the `AsyncTraceSink` callback alias;
- reuse `OperationTrace::new`, `measure`, and `complete`;
- do not add storage ids, daemon ids, SQLite types, response JSON, command
  output, or transcript text.

In `crates/sandbox-runtime/operation/src/lib.rs`, re-export only the new
storage-neutral async trace types needed by `sandbox-daemon`.

In `crates/sandbox-runtime/operation/src/command/service/core.rs`:

- add an optional async trace sink slot, preferably
  `Arc<Mutex<Option<AsyncTraceSink>>>`, because the finalizer thread is already
  spawned during service construction;
- pass a clone of that slot into `spawn_completion_finalizer`;
- add a narrow `set_async_trace_sink` method for daemon construction to install
  or clear the callback;
- keep existing constructors defaulting to disabled async tracing.

This setter is smaller than changing daemon `serve` to construct
`DaemonObservability` outside `SandboxDaemonServer::new`, and it avoids making
`DaemonObservability` part of the public daemon API.

In `crates/sandbox-runtime/operation/src/command/service/impls/exec_command.rs`:

- make `dispatch` derive `origin_request_id = trace.is_some().then(|| request.request_id.clone())`;
- pass that optional string into `CommandOperationService::exec_command`;
- after workspace resolution and `allocate_command_session_id`, build an
  `AsyncTraceLink` only when `origin_request_id` is present and an async sink is
  configured;
- pass the optional link to `CommandCompletionPromise::new`.

In `crates/sandbox-runtime/operation/src/command/service/completion.rs`:

- add `async_trace_link: Option<AsyncTraceLink>` to
  `CommandCompletionPromise`;
- add the same optional field to `CommandCompletion`;
- copy the link in `CommandCompletionPromise::resolve`;
- keep the existing completion channel and finalizer thread;
- when a completion has both link and sink, run finalization through a fresh
  `OperationTrace` and emit `CompletedAsyncOperationTrace`;
- when either is missing, call `complete_terminal_command_with_services` exactly
  as today with no trace object.

In `crates/sandbox-runtime/operation/src/command/service/finalize.rs`:

- add `trace: Option<&OperationTrace>` only to the private finalization helper
  path;
- wrap the selected finalizer spans with `measure_optional`;
- keep command transcript and output handling unchanged.

Do not add trace parameters to workspace, command, namespace, or layerstack
lower crates for Phase 4.

## Daemon and Storage Changes

In `crates/sandbox-daemon/src/server/runtime.rs`:

- after `DaemonObservability::from_config(&config).map(Arc::new)`, install an
  async trace sink on `operations.command`;
- if observability is disabled, clear the sink;
- the sink closure calls
  `DaemonObservability::insert_completed_async_operation_trace` and ignores
  errors.

In `crates/sandbox-daemon/src/observability/service.rs`:

- add `insert_completed_async_operation_trace`;
- derive async trace ids from the Phase 4 rule;
- set `TraceRecord.kind = "async"`;
- set `TraceRecord.operation = "command_finalization"`;
- set `TraceRecord.request_id = None`;
- set `TraceRecord.origin_request_id`, `async_name`, `correlation_kind`,
  `correlation_id`, `workspace_id`, and `command_session_id` from the link;
- map spans exactly like request traces, using `:span:` ids and
  `parent_call_index`;
- bound all ids and strings with the existing daemon helpers;
- do not update Phase 3.5 enabled deep span keys from async finalizer traces.

In `crates/sandbox-observability/src/records.rs`:

- add optional async fields to `TraceRecord`;
- validate each optional field with the existing id/kind length helpers;
- do not change `SpanRecord`.

In `crates/sandbox-observability/src/store.rs`:

- add a V3 migration for the nullable async fields and focused indexes;
- update `insert_trace` SQL to include the new fields;
- keep one transaction for the trace row and spans;
- do not add a writer queue or runtime-facing storage API.

## Command Finalization Span Plan

Use this initial span tree:

```text
command_finalization
  completion_finalizer
    complete_terminal_command_with_services
      apply_workspace_completion_policy
      complete_command_record
```

The live call path also includes `begin_terminal_completion` and
`terminal_result`, but they should not be separate first-pass spans. They are
small and are covered by `complete_terminal_command_with_services`.

Do not include `completion_watcher` in Phase 4. The current watcher can attach
the link because the promise can carry it, but tracing the watcher would require
either cross-thread trace sharing or a second completed trace. That violates
the small design.

One-shot workspace destroy is included inside
`apply_workspace_completion_policy`. If a one-shot command reaches terminal
state, the child span covers the `WorkspaceSessionService::destroy_session`
call inclusively.

If `complete_terminal_command_with_services` returns an error, the async trace
row should be `status = "error"` with a bounded `error_message`. First-pass
span rows may keep their runtime `ok` or `panic` statuses; per-span finalizer
error attribution is deferred unless implementation can add it without
exceeding the runtime budget.

## Workspace Destroy/Remount Decision

One-shot workspace destroy during command terminal completion is part of the
command finalization trace because it runs inside
`apply_workspace_completion_policy` on the existing completion finalizer path.

Do not create separate workspace destroy async traces in Phase 4. The live
destroy paths are either synchronous request cleanup after command start
failure or part of command finalization.

Do not create separate workspace remount async traces in Phase 4.
`WorkspaceRemountService::remount_workspace_session` currently runs
synchronously in its service method and does not spawn later lifecycle work.
If future code moves destroy or remount outside the original request, add a
separate linked async trace in a later phase with its own correlation key.

## File-by-File Plan

`crates/sandbox-runtime/operation/src/observability.rs`

- Add `AsyncTraceLink`, `CompletedAsyncOperationTrace`, and `AsyncTraceSink`.
- Keep using `OperationTrace` and `CompletedOperationTrace`.
- Do not import `sandbox-observability`.

`crates/sandbox-runtime/operation/src/lib.rs`

- Re-export the new runtime-neutral async trace types.

`crates/sandbox-runtime/operation/src/command/service/core.rs`

- Add the optional async sink slot.
- Pass the sink slot to `spawn_completion_finalizer`.
- Add `set_async_trace_sink`.
- Keep default construction disabled.

`crates/sandbox-runtime/operation/src/command/service/impls/exec_command.rs`

- Thread optional `origin_request_id` from `dispatch` to command start.
- Build the command-finalization `AsyncTraceLink` after `command_session_id`
  and `workspace_session_id` are known.
- Pass the link into `CommandCompletionPromise`.

`crates/sandbox-runtime/operation/src/command/service/completion.rs`

- Carry the optional link through promise, completion send, and finalizer
  receive.
- Create and complete the finalizer `OperationTrace` only when link and sink
  are both present.
- Keep the existing finalizer thread.

`crates/sandbox-runtime/operation/src/command/service/finalize.rs`

- Add optional trace parameters on private helpers.
- Instrument only the selected finalizer spans.

`crates/sandbox-daemon/src/server/runtime.rs`

- Install or clear the runtime async sink when constructing the server.
- Keep request dispatch response behavior unchanged.

`crates/sandbox-daemon/src/observability/service.rs`

- Add async trace mapping and trace-id helpers.
- Keep request-trace mapping unchanged except shared helper extraction if it
  reduces duplication.

`crates/sandbox-observability/src/records.rs`

- Extend `TraceRecord` with nullable async fields.
- Keep `SpanRecord` unchanged.

`crates/sandbox-observability/src/store.rs`

- Add migration `phase_4_async_method_traces`.
- Extend trace insert SQL and test helpers.

`crates/sandbox-runtime/operation/tests/operation_trace.rs`

- Add a small test showing the finalizer can reuse `OperationTrace` for the
  selected span tree, or cover this through command tests if no public helper is
  exposed.

`crates/sandbox-runtime/operation/tests/exec_command.rs`

- Add a disabled-observability test proving commands still finalize and no
  async callback is required when no sink/link exists.
- Update direct `exec_command` calls if its signature gains
  `origin_request_id`.

`crates/sandbox-runtime/operation/tests/command_remount.rs`

- Keep focused remount tests passing; no remount async trace tests are required.

`crates/sandbox-daemon/tests/unit/observability.rs`

- Add a linked async trace persistence test covering `origin_request_id`,
  `async_name`, `correlation_kind`, `correlation_id`, `workspace_id`, and
  `command_session_id`.
- Add a disabled observability path test if it is clearer at daemon level than
  runtime level.
- Verify store failures do not alter command responses.

`crates/sandbox-observability/tests/schema.rs`

- Verify the V3 migration creates the async columns and indexes.

## Expected LOC

Expected `crates/sandbox-runtime` change: 60-110 non-test LOC, with 60-80 preferred.

Expected split:

```text
runtime async link/sink DTOs                  15-25
command service sink slot and setter          15-25
completion channel link and finalizer trace   25-40
finalize.rs selected spans                    15-25
exec_command origin/link wiring               10-20
```

The preferred 60-80 LOC band is reachable only if the implementation keeps the
callback simple and does not trace the watcher. If it lands closer to 90-100
LOC, the live-code constraint is the existing service construction shape: the
completion finalizer thread is spawned before daemon observability exists, so a
small shared sink slot and setter are needed to connect daemon-owned storage
without making `DaemonObservability` public or rebuilding runtime operations in
daemon `serve`.

If runtime production changes exceed 110 non-test LOC, stop and simplify before
implementation.

Daemon and storage changes are outside the runtime budget. They should remain
limited to one sink installation point, one async trace mapper, one V3 schema
migration, and focused tests.

## Verification

Focused checks:

```sh
cargo fmt --check
cargo test -p sandbox-runtime operation_trace
cargo test -p sandbox-runtime exec_command
cargo test -p sandbox-runtime command_remount
cargo test -p sandbox-daemon observability
cargo test -p sandbox-observability schema
```

Required behavior coverage:

- disabled observability does not create a link, construct an async
  `OperationTrace`, call a sink, or change command finalization behavior;
- enabled observability persists one async trace for command finalization;
- async trace row has `kind = "async"`, `operation = "command_finalization"`,
  `request_id = NULL`, and the expected origin/correlation/workspace/command
  fields;
- async span ids use `async:command_finalization:command_session_id:<id>:span:<call_index>`;
- the selected finalizer span tree is recorded in call order;
- one-shot workspace destroy is covered under
  `apply_workspace_completion_policy`;
- existing request traces still persist through
  `insert_completed_operation_trace`;
- Phase 3.5 enabled deep span keys are not updated from async finalizer traces;
- store failures do not fail or alter command responses;
- runtime tests do not import `sandbox-observability`;
- no command output, transcript text, shell text, or namespace-runner internals
  appear in trace rows or span rows.

Do not require broad workspace tests unless implementation changes shared
contracts beyond command finalization tracing.

## Deferred Work

Defer:

- `completion_watcher` spans;
- per-span finalizer error attribution if it costs extra runtime plumbing;
- separate workspace destroy async traces;
- separate workspace remount async traces;
- a `trace_links` table for multi-link traces;
- daemon or manager trace query APIs;
- Phase 4.5 namespace-runner propagation and child-process spans;
- any metrics/log export or response-envelope changes.
