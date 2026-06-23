# Phase 3 Request Method Traces

Status: draft implementation spec

## Parent Spec

[sandbox-observability.md](./sandbox-observability.md)

## Builds On

- [phase-1-observability-foundation.md](./phase-1-observability-foundation.md)
- [phase-2-runtime-snapshots.md](./phase-2-runtime-snapshots.md)

## Exact Goal

Phase 3 adds completed request method traces for the daemon-owned runtime
operation path. It records one request-local runtime trace, a root
`dispatch_operation` span, and a small set of coarse operation spans for the
current long-running or observability-relevant runtime operations.

Phase 3 implements only:

- create one request-local `OperationTrace` at daemon dispatch;
- pass that trace context into `sandbox_runtime::dispatch_operation`;
- add the automatic root `dispatch_operation` span;
- add selected coarse spans for `exec_command`, `write_command_stdin`,
  `read_command_lines`, and `squash`;
- persist completed request traces and spans through daemon-owned
  observability storage;
- preserve the current `sandbox_protocol::Response` payload shape.

Phase 3 must not add response envelopes, async trace links, runner child-process
traces, manager aggregation, query APIs, external observability services, command
transcript ingestion, runtime SQLite writes, or a `sandbox-observability`
dependency from `sandbox-runtime`.

## Current Repo Grounding

This section describes the live checkout this spec is grounded in. Docs are
treated as rollout proposals; live code is the source of truth.

### Phase 2 Snapshot State

Phase 2 snapshot code is present in live code even though
`docs/observability/phase-2-runtime-snapshots.md` still has an unchecked
completion checklist.

Live Phase 2 pieces:

- `crates/sandbox-runtime/operation/src/observability.rs` defines
  `RuntimeObservabilitySnapshot`, `RuntimeWorkspaceSnapshot`, and
  `RuntimeExecutionSnapshot`.
- `crates/sandbox-runtime/operation/src/services.rs` exposes
  `SandboxRuntimeOperations::observability_snapshot`.
- `crates/sandbox-runtime/operation/src/workspace_session/service/snapshot.rs`
  snapshots active workspace sessions.
- `crates/sandbox-runtime/operation/src/command/service/process_store.rs`
  snapshots active command executions through
  `CommandProcessStore::snapshot_active_executions`.
- `crates/sandbox-daemon/src/observability/service.rs` defines
  `DaemonObservability` and writes sandbox, workspace, execution, and resource
  snapshot records.
- `crates/sandbox-observability/src/store.rs` has two migrations:
  `phase_1_observability_foundation` and `phase_2_runtime_snapshots`.
- `crates/sandbox-daemon/tests/unit/observability.rs`,
  `crates/sandbox-runtime/operation/tests/observability_snapshot.rs`, and
  `crates/sandbox-observability/tests/schema.rs` cover the current Phase 2
  storage and snapshot behavior.

Still incomplete or dirty pieces relevant to Phase 3:

- The Phase 2 spec checklist is not updated to match live code.
- `RuntimeExecutionSnapshot::started_at_unix_ms` is currently `None` for active
  command executions; Phase 3 request trace timing must not depend on that
  field.
- Cgroup sampling is currently written as unavailable when the daemon does not
  have a concrete cgroup path. This does not block request traces.
- Daemon snapshot collection errors are ignored by
  `SandboxDaemonServer::trigger_observability_collection`; Phase 3 trace
  persistence must follow the same best-effort request-safety rule.

### Current Daemon Server Shape

`crates/sandbox-daemon/src/server/runtime.rs` currently defines:

```rust
pub struct SandboxDaemonServer {
    pub(crate) config: ServerConfig,
    pub(crate) operations: Arc<SandboxRuntimeOperations>,
    pub(crate) observability: Option<Arc<DaemonObservability>>,
    pub(crate) shutdown: CancellationToken,
}
```

`ServerConfig` carries `socket_path`, `pid_path`, optional TCP fields,
`auth_token`, and optional `sandbox_id`.

`SandboxDaemonServer::new` currently creates
`DaemonObservability::from_config(&config).map(Arc::new)`. Observability is
disabled when `sandbox_id` is missing, empty, path derivation fails, or the
store cannot open.

### Current Daemon Dispatch Flow

`crates/sandbox-daemon/src/server/dispatch.rs` currently:

- parses bytes into `serde_json::Value`;
- strips TCP auth before request decoding;
- decodes `sandbox_protocol::Request` through `decode_request`;
- validates daemon scope through `validate_daemon_scope`;
- calls `sandbox_runtime::dispatch_operation(&operations, &request)` inside
  `tokio::task::spawn_blocking`;
- projects the runtime `Response` with `into_json_value`;
- triggers Phase 2 snapshot collection only after `Ok(response)` from the
  blocking task.

Phase 3 must keep that response projection behavior. Trace persistence happens
after the operation response has been projected and must not change the response
value returned to the caller.

### Current Runtime Operation Boundary

`crates/sandbox-runtime/operation/src/operation.rs` currently defines:

```rust
pub(crate) struct OperationEntry {
    pub(crate) name: &'static str,
    pub(crate) cli: Option<&'static CliOperationSpec>,
    pub(crate) dispatch:
        fn(&SandboxRuntimeOperations, &sandbox_protocol::Request) -> sandbox_protocol::Response,
}
```

`OperationEntry::cli` accepts the same two-argument function pointer.

`dispatch_operation` currently has this shape:

```rust
pub(crate) fn dispatch_operation(
    operations: &SandboxRuntimeOperations,
    request: &sandbox_protocol::Request,
) -> sandbox_protocol::Response
```

`crates/sandbox-runtime/operation/src/lib.rs` re-exports it as:

```rust
pub fn dispatch_operation(
    operations: &SandboxRuntimeOperations,
    request: &sandbox_protocol::Request,
) -> sandbox_protocol::Response
```

Phase 3 changes these signatures to pass a runtime-owned trace context.

### Current Public Runtime Operation Entries

The current operation entry groups are:

- `crates/sandbox-runtime/operation/src/command/service/impls/mod.rs`
  - `exec_command`
  - `write_command_stdin`
  - `read_command_lines`
- `crates/sandbox-runtime/operation/src/layerstack/service/impls/mod.rs`
  - `squash`

The current dispatch functions are:

```rust
pub(crate) fn dispatch(operations: &SandboxRuntimeOperations, request: &Request) -> Response
```

for each command operation, and:

```rust
pub(crate) fn dispatch(operations: &SandboxRuntimeOperations, _request: &Request) -> Response
```

for `squash`.

The current selected service methods are:

- `CommandOperationService::exec_command(input)`;
- `CommandOperationService::write_command_stdin(input)`;
- `CommandOperationService::read_command_lines(input)`;
- `LayerStackService::squash()`.

Phase 3 should change the selected methods directly to accept
`&OperationTrace`. Do not add alias methods, compatibility wrappers, or a second
parallel operation path.

### Current Phase 1/2 Store Shape

`crates/sandbox-observability/src/records.rs` currently defines
`TraceRecord` with:

```rust
pub struct TraceRecord {
    pub trace_id: String,
    pub kind: String,
    pub status: String,
    pub sandbox_id: String,
    pub operation: String,
    pub request_id: Option<String>,
    pub started_at_unix_ms: i64,
    pub finished_at_unix_ms: Option<i64>,
    pub duration_ms: Option<f64>,
    pub error_kind: Option<String>,
    pub error_message: Option<String>,
}
```

`SpanRecord` currently has `span_id`, `trace_id`, optional `parent_span_id`,
`method_name`, `call_index`, `status`, timing fields, and optional error fields.

`crates/sandbox-observability/src/store.rs` currently exposes:

```rust
pub fn insert_trace(
    &self,
    trace: &TraceRecord,
    spans: &[SpanRecord],
) -> Result<(), StoreError>
```

and inserts one trace row plus all span rows in a single SQLite transaction.

The current `traces` table does not have `workspace_id` or
`command_session_id`. Phase 3 must add only those hierarchy fields and their
indexes.

### Current Request and Response Shape

`crates/sandbox-protocol/src/request.rs` defines:

```rust
pub struct Request {
    pub op: String,
    pub request_id: String,
    pub scope: CliOperationScope,
    pub args: Value,
}
```

`crates/sandbox-protocol/src/response.rs` defines `Response` as a private raw
`serde_json::Value` wrapper. `Response::ok(result)` and
`Response::running(result)` store the result directly. Fault responses are
top-level JSON objects with an `error` field. Phase 3 must not replace this
shape with `{ "result": ..., "meta": ... }`.

### Command Output and Transcripts

Command output remains in operation responses such as `CommandYield.output` and
`CommandLinesOutput.output`. Command transcript content remains in command
transcript artifacts. Phase 3 trace records and span records must store only
method names, parentage, ordering, status, timing, and bounded error metadata.
They must not ingest command output, transcript rows, stdout/stderr chunks,
environment dumps, or shell text.

## Non-Goals

Phase 3 does not implement:

- Phase 3.5 targeted deep request spans;
- Phase 4 async finalization traces;
- Phase 4.5 cross-process namespace-runner traces;
- `trace_links`;
- `origin_request_id`;
- `correlation_kind`;
- `correlation_id`;
- `async_name`;
- manager aggregation;
- daemon query APIs such as `get_observability_snapshot`;
- Prometheus, Grafana, Loki, Tempo, OTLP, or log export;
- command transcript ingestion;
- response envelopes such as `{ "result": ..., "meta": ... }`;
- runtime SQLite writes;
- a `sandbox-observability` dependency from `sandbox-runtime`;
- a new crate;
- manager-side files.

## Architecture

### Runtime Trace Context

Add runtime-owned request tracing under
`crates/sandbox-runtime/operation/src/observability/trace.rs`.

`OperationTrace` is a request-local runtime context. It records span structure
and terminal status but does not know SQLite, daemon paths, or
`sandbox-observability` record types.

Use this domain split:

- `OperationTrace`: mutable request aggregate used while dispatch runs.
- `CompletedOperationTrace`: immutable runtime DTO returned to the daemon.
- `CompletedOperationSpan`: immutable runtime DTO for completed spans.
- `TraceStatus`: runtime enum or string adapter for `ok`, `error`, and `panic`.

The enabled trace state must include:

- `trace_id`;
- `request_id`;
- optional `sandbox_id`;
- `operation`;
- optional `workspace_id`;
- optional `command_session_id`;
- monotonic start time as `Instant`;
- Unix start time in milliseconds;
- active parent stack;
- completed spans;
- next stable `call_index`;
- terminal status;
- optional terminal `error_kind`;
- optional terminal `error_message`.

The request trace id is:

```text
trace_id = "request:" + request_id
```

Pass `&OperationTrace`, not `&mut OperationTrace`.

Reason: Phase 3 needs RAII span guards that close on normal return, early
return, and panic unwind while still allowing nested calls to enter child spans.
A guard that holds `&mut OperationTrace` would borrow the trace for the full
scope and make nested instrumentation awkward or impossible without unsafe code
or manual close calls. `OperationTrace` should use interior mutability, such as
`RefCell<TraceState>`, because the trace is request-local and not shared across
concurrent threads. It does not need `Arc<Mutex<_>>`.

Expected API shape:

```rust
pub struct OperationTrace {
    state: TraceStateStorage,
}

pub struct SpanGuard<'a> {
    trace: &'a OperationTrace,
    span_id: String,
}

impl OperationTrace {
    pub fn enabled(
        request_id: String,
        operation: String,
        sandbox_id: String,
    ) -> Self;

    pub fn disabled(request_id: String, operation: String) -> Self;

    pub fn is_enabled(&self) -> bool;

    pub fn set_workspace_id(&self, workspace_id: impl Into<String>);

    pub fn set_command_session_id(&self, command_session_id: impl Into<String>);

    pub fn enter(&self, method_name: &'static str) -> SpanGuard<'_>;

    pub fn measure<T>(
        &self,
        method_name: &'static str,
        call: impl FnOnce() -> T,
    ) -> T;

    pub fn measure_result<T, E: std::fmt::Display>(
        &self,
        method_name: &'static str,
        error_kind: &'static str,
        call: impl FnOnce() -> Result<T, E>,
    ) -> Result<T, E>;

    pub fn complete_from_response_value(
        &self,
        response: &serde_json::Value,
    ) -> CompletedOperationTrace;

    pub fn complete_panic(
        &self,
        message: impl Into<String>,
    ) -> CompletedOperationTrace;
}
```

`enter` creates a span, assigns `call_index`, uses the current top of the parent
stack as `parent_span_id`, pushes the new span id, and returns a `SpanGuard`.
Dropping the guard pops the span if it is still active and records finish time
and duration. If the guard is dropped during unwind, the span closes with
`status = "panic"` unless it was already marked with a more specific error.

`measure` is a convenience wrapper around `enter` for one call or expression.

`measure_result` is the selected Phase 3 way to map typed service errors into
span error metadata without changing operation response payloads. The closure's
`Err` value is recorded as bounded `error_message` with the caller-provided
`error_kind`, then the original `Err` is returned unchanged.

Operation response errors are represented by inspecting the projected JSON value
at trace completion:

- if the response has top-level `error.kind` and `error.message`, the trace
  terminal status is `error` and those bounded fields are copied into trace
  metadata;
- otherwise the trace terminal status is `ok`;
- if completion sees an error response and no span has an error status, the
  current operation dispatch span or root span should be marked with the same
  bounded error metadata so unknown operations and request argument parse errors
  still have useful root timing.

This stores error metadata only in observability records. It must not add error
metadata to the operation response and must not wrap successful responses.

`CompletedOperationTrace` must be produced before daemon persistence. The
completed DTO remains a runtime type so the daemon can map it into
`TraceRecord` and `SpanRecord` without making runtime import
`sandbox-observability`.

### Dispatch Boundary

Change the runtime dispatch boundary to accept `&OperationTrace`.

Expected `OperationEntry` shape in
`crates/sandbox-runtime/operation/src/operation.rs`:

```rust
use crate::observability::OperationTrace;

pub(crate) struct OperationEntry {
    pub(crate) name: &'static str,
    pub(crate) cli: Option<&'static CliOperationSpec>,
    pub(crate) dispatch: fn(
        &SandboxRuntimeOperations,
        &sandbox_protocol::Request,
        &OperationTrace,
    ) -> sandbox_protocol::Response,
}
```

Expected dispatch shape:

```rust
pub(crate) fn dispatch_operation(
    operations: &SandboxRuntimeOperations,
    request: &sandbox_protocol::Request,
    trace: &OperationTrace,
) -> sandbox_protocol::Response {
    trace.measure("dispatch_operation", || {
        operation_entry_groups()
            .into_iter()
            .flat_map(|entries| entries.iter())
            .find(|entry| entry.name == request.op)
            .map_or_else(sandbox_protocol::Response::unknown_op, |entry| {
                trace.measure(operation_dispatch_span(entry.name), || {
                    (entry.dispatch)(operations, request, trace)
                })
            })
    })
}
```

`operation_dispatch_span(entry.name)` should produce the exact operation
dispatch span name:

```text
exec_command::dispatch
write_command_stdin::dispatch
read_command_lines::dispatch
squash::dispatch
```

The public runtime entry in `crates/sandbox-runtime/operation/src/lib.rs`
changes to:

```rust
pub fn dispatch_operation(
    operations: &SandboxRuntimeOperations,
    request: &sandbox_protocol::Request,
    trace: &OperationTrace,
) -> sandbox_protocol::Response
```

The selected operation dispatch functions change to:

```rust
pub(crate) fn dispatch(
    operations: &SandboxRuntimeOperations,
    request: &Request,
    trace: &OperationTrace,
) -> Response
```

for command operations and `squash`.

Unknown operations still pass through `dispatch_operation`, so the request gets
at least:

```text
dispatch_operation
```

with terminal `unknown_op` metadata after response projection.

Request argument parse errors inside operation dispatch still get:

```text
dispatch_operation
<operation>::dispatch
```

because `<operation>::dispatch` wraps `parse_input`. Phase 3 must not add a
separate `parse_input` span.

Daemon request decode errors happen before a typed `Request` exists. Phase 3 can
persist a minimal decode-error trace only when the raw decoded JSON contains a
non-empty string `request_id` and a non-empty string `op`; otherwise no valid
`request:<request_id>` trace id can be formed. Bad JSON without a request id is
not a Phase 3 trace producer.

### Daemon Persistence

`SandboxDaemonServer::dispatch_request` owns trace creation and persistence.

Trace creation rule:

- if `self.observability.is_some()` and `self.config.sandbox_id` is a non-empty
  string, create `OperationTrace::enabled(request.request_id.clone(),
  request.op.clone(), sandbox_id.clone())`;
- otherwise create `OperationTrace::disabled(request.request_id.clone(),
  request.op.clone())` and still pass it through runtime dispatch.

Using a disabled trace keeps runtime code simple and avoids optional trace
plumbing through every selected operation. Disabled trace methods are no-ops and
`complete_from_response_value` returns a disabled completed trace that the daemon
does not persist.

Expected daemon shape:

```rust
async fn dispatch_request(&self, request: Request) -> serde_json::Value {
    if let Err(response) = validate_daemon_scope(&request) {
        return response;
    }

    let trace = self.operation_trace_for(&request);
    let operations = Arc::clone(&self.operations);
    let task = tokio::task::spawn_blocking(move || {
        let response = sandbox_runtime::dispatch_operation(
            &operations,
            &request,
            &trace,
        );
        let value = response.into_json_value();
        let completed_trace = trace.complete_from_response_value(&value);
        (value, completed_trace)
    });

    match task.await {
        Ok((response, completed_trace)) => {
            self.persist_completed_trace(completed_trace);
            self.trigger_observability_collection();
            response
        }
        Err(err) if err.is_cancelled() => { /* current cancelled response */ }
        Err(err) => { /* current internal-error response */ }
    }
}
```

`persist_completed_trace` should:

- return immediately when the completed trace is disabled;
- return immediately when `self.observability` is `None`;
- map runtime trace DTOs into `TraceRecord` and `SpanRecord`;
- call a daemon-owned method such as `DaemonObservability::insert_trace`;
- ignore write failures for the user operation response;
- optionally record bounded internal diagnostics later, but not in Phase 3
  response payloads.

Do not move SQLite handles, `ObservabilityStore`, `TraceRecord`, or
`SpanRecord` into `sandbox-runtime`.

Panic handling:

- `SpanGuard::drop` must close active spans during unwind.
- The daemon should keep the current user-facing panic behavior: a runtime panic
  maps to the existing internal daemon error path.
- If the implementation adds a narrow `catch_unwind` inside the blocking closure
  to complete and persist a `panic` trace, it must still return the same error
  kind and same broad internal-error behavior to the caller. Panic persistence is
  best effort; normal operation errors are the required Phase 3 persistence
  target.

Phase 2 snapshot collection continues after request handling. Trace persistence
should run before `trigger_observability_collection` so the completed method
trace is durable even if snapshot collection later fails.

### Storage Migration

The live schema lacks `workspace_id` and `command_session_id` on `traces`.
Add one new migration to `crates/sandbox-observability/src/store.rs`:

```rust
Migration {
    version: 3,
    name: "phase_3_request_method_traces",
    sql: V3_SCHEMA_SQL,
}
```

`V3_SCHEMA_SQL` must add only:

```sql
ALTER TABLE traces ADD COLUMN workspace_id TEXT;
ALTER TABLE traces ADD COLUMN command_session_id TEXT;

CREATE INDEX IF NOT EXISTS idx_traces_workspace_time
  ON traces(sandbox_id, workspace_id, started_at_unix_ms);

CREATE INDEX IF NOT EXISTS idx_traces_command_time
  ON traces(sandbox_id, command_session_id, started_at_unix_ms);
```

Update `TraceRecord` in
`crates/sandbox-observability/src/records.rs` to include:

```rust
pub workspace_id: Option<String>,
pub command_session_id: Option<String>,
```

and validate them with `MAX_ID_LENGTH`.

Update `ObservabilityStore::insert_trace` to insert those fields into the
`traces` table. Do not change the `spans` table for Phase 3.

Do not add `trace_links`, `origin_request_id`, `correlation_kind`,
`correlation_id`, or `async_name`.

### Selected Span Policy

Phase 3 spans are inclusive timings. A span measures the full elapsed time of
the code block it wraps, including lower-level work that Phase 3 intentionally
does not split.

Every request trace starts with:

```text
dispatch_operation
<operation>::dispatch
```

`<operation>::dispatch` is omitted only when no operation entry matches the
request `op`.

For `exec_command`, add only:

```text
CommandOperationService::exec_command
resolve_exec_workspace
start_command_process
initial_exec_yield
```

`resolve_exec_workspace` may include workspace-session resolution or
workspace-session creation and related layerstack work. `start_command_process`
may include `WorkspaceHandle::entry`, parent-side command spawn preparation,
namespace-runner request building, and parent-side process spawn. Phase 3 does
not split those internal calls.

For `write_command_stdin`, add only:

```text
CommandOperationService::write_command_stdin
write_or_cancel
wait_for_command_yield
```

`write_or_cancel` should wrap the existing branch that either cancels the
process for kill input or writes to process stdin. It does not need to become a
new public method.

For `read_command_lines`, add only:

```text
CommandOperationService::read_command_lines
read_transcript_window
```

`read_transcript_window` should include active transcript window reads and
completed transcript window reads. It must not record transcript content.

For `squash`, add only:

```text
LayerStackService::squash
```

Explicitly defer these helper or lower-level spans:

```text
parse_input
command_admission
register_active_command
start_completion_watcher
command_yield_response
WorkspaceSessionService::create_workspace_session
WorkspaceRuntimeService::create_workspace
layerstack snapshot or lease acquisition
CommandProcessSpawn::prepare
CommandProcess::spawn
build_namespace_runner_request
spawn_current_exe_ns_runner
runner::run
run_setns
shell_exec::execute_shell
wait_for_command_execution_scope
```

### Phase 3.5 / Phase 4 / Phase 4.5 Boundaries

Phase 3.5 may split proven-slow Phase 3 parent spans into a small number of
lower-level in-process spans. It must be justified by observed Phase 3 traces.
Phase 3.5 remains in-process and does not add async link metadata.

Phase 4 adds linked async traces for work that finishes after the request
returns, such as command finalization. It owns `origin_request_id`,
`correlation_kind`, `correlation_id`, and `async_name`.

Phase 4.5 adds cross-process namespace-runner traces. Runner internals such as
`runner::run`, `run_setns`, `shell_exec::execute_shell`, and
`wait_for_command_execution_scope` are not ordinary Phase 3 child spans because
they run across a process boundary and may outlive the request.

## Detailed File Plan

Keep the implementation small and use the existing crate layout.

Runtime files:

- `crates/sandbox-runtime/operation/src/observability.rs`
  - Move this file to `crates/sandbox-runtime/operation/src/observability/mod.rs`
    because Rust cannot keep both `observability.rs` and an
    `observability/` module directory.
- `crates/sandbox-runtime/operation/src/observability/mod.rs`
  - Re-export snapshot DTOs and trace DTOs.
- `crates/sandbox-runtime/operation/src/observability/snapshot.rs`
  - Hold the current `RuntimeObservabilitySnapshot`,
    `RuntimeWorkspaceSnapshot`, and `RuntimeExecutionSnapshot` definitions.
- `crates/sandbox-runtime/operation/src/observability/trace.rs`
  - Add `OperationTrace`, `SpanGuard`, `CompletedOperationTrace`, and
    `CompletedOperationSpan`.
- `crates/sandbox-runtime/operation/src/lib.rs`
  - Re-export `OperationTrace` and completed trace DTOs as needed by
    `sandbox-daemon`.
  - Change public `dispatch_operation` signature.
- `crates/sandbox-runtime/operation/src/operation.rs`
  - Change `OperationEntry` function pointer type.
  - Add root `dispatch_operation` span.
  - Add `<operation>::dispatch` span names.
- `crates/sandbox-runtime/operation/src/command/service/impls/mod.rs`
  - Update operation entry constants for the new dispatch function pointer.
- `crates/sandbox-runtime/operation/src/command/service/impls/exec_command.rs`
  - Add trace parameter to dispatch and selected service methods.
  - Add the four selected `exec_command` spans.
- `crates/sandbox-runtime/operation/src/command/service/impls/write_command_stdin.rs`
  - Add trace parameter.
  - Add selected write/cancel and yield spans.
- `crates/sandbox-runtime/operation/src/command/service/impls/read_command_lines.rs`
  - Add trace parameter.
  - Add selected transcript-window span without recording transcript content.
- `crates/sandbox-runtime/operation/src/layerstack/service/impls/mod.rs`
  - Update the operation entry constant for the new dispatch function pointer.
- `crates/sandbox-runtime/operation/src/layerstack/service/impls/squash.rs`
  - Add trace parameter and selected `LayerStackService::squash` span.

Daemon files:

- `crates/sandbox-daemon/src/server/dispatch.rs`
  - Create enabled or disabled `OperationTrace`.
  - Pass trace into runtime dispatch.
  - Complete trace from projected response JSON.
  - Persist completed trace before Phase 2 snapshot collection.
- `crates/sandbox-daemon/src/observability/mod.rs`
  - Add `mod trace;`.
- `crates/sandbox-daemon/src/observability/service.rs`
  - Add daemon-owned trace persistence entrypoint.
  - Keep snapshot collection behavior unchanged.
- `crates/sandbox-daemon/src/observability/trace.rs`
  - Map `CompletedOperationTrace` and `CompletedOperationSpan` into
    `TraceRecord` and `SpanRecord`.
  - Bound error strings consistently with the current daemon service helpers.

Storage files:

- `crates/sandbox-observability/src/records.rs`
  - Add `workspace_id` and `command_session_id` to `TraceRecord`.
- `crates/sandbox-observability/src/store.rs`
  - Add migration version 3.
  - Update `insert_trace`.
  - Add test-only trace read helper only if needed by focused tests.
- `crates/sandbox-observability/tests/schema.rs`
  - Update allowed index set.
  - Update synthetic trace construction.
  - Add migration and hierarchy-field assertions.

Do not add a new crate. Do not add manager files.

## Expected Struct and Signature Changes

Expected runtime trace DTOs:

```rust
pub struct CompletedOperationTrace {
    pub enabled: bool,
    pub trace_id: String,
    pub kind: &'static str,
    pub status: String,
    pub sandbox_id: Option<String>,
    pub operation: String,
    pub request_id: String,
    pub workspace_id: Option<String>,
    pub command_session_id: Option<String>,
    pub started_at_unix_ms: i64,
    pub finished_at_unix_ms: Option<i64>,
    pub duration_ms: Option<f64>,
    pub error_kind: Option<String>,
    pub error_message: Option<String>,
    pub spans: Vec<CompletedOperationSpan>,
}

pub struct CompletedOperationSpan {
    pub span_id: String,
    pub trace_id: String,
    pub parent_span_id: Option<String>,
    pub method_name: String,
    pub call_index: i64,
    pub status: String,
    pub started_at_unix_ms: i64,
    pub finished_at_unix_ms: Option<i64>,
    pub duration_ms: Option<f64>,
    pub error_kind: Option<String>,
    pub error_message: Option<String>,
}
```

`kind` is always `"request"` in Phase 3.

Expected service signatures:

```rust
impl CommandOperationService {
    pub fn exec_command(
        &self,
        input: ExecCommandInput,
        trace: &OperationTrace,
    ) -> Result<CommandYield, CommandServiceError>;

    pub fn write_command_stdin(
        &self,
        input: WriteCommandStdinInput,
        trace: &OperationTrace,
    ) -> Result<CommandYield, CommandServiceError>;

    pub fn read_command_lines(
        &self,
        input: ReadCommandLinesInput,
        trace: &OperationTrace,
    ) -> Result<CommandLinesOutput, CommandServiceError>;
}

impl LayerStackService {
    pub fn squash(
        &self,
        trace: &OperationTrace,
    ) -> Result<SquashLayerStackResult, LayerStackServiceError>;
}
```

Expected daemon persistence signature:

```rust
impl DaemonObservability {
    pub(crate) fn insert_trace(
        &self,
        trace: CompletedOperationTrace,
    ) -> Result<(), StoreError>;
}
```

`DaemonObservability::insert_trace` should reject or ignore disabled completed
traces before record mapping. It should also skip persistence if
`trace.sandbox_id` is missing, because `TraceRecord.sandbox_id` is required by
the storage schema.

## Failure Policy

Observability remains best effort.

- Missing `sandbox_id` disables trace persistence and must not fail daemon
  serving.
- Failure to open `DaemonObservability` disables trace persistence and must not
  fail daemon serving.
- `ObservabilityStore::insert_trace` failures must not change operation
  responses.
- Trace persistence failures must not prevent Phase 2 snapshot collection.
- Phase 2 snapshot collection failures must not change operation responses.
- Runtime operation errors still return the same operation response shape and
  are recorded only as trace/span status and bounded error metadata.
- Unknown operations still return `Response::unknown_op()` and record a request
  trace when observability is enabled.
- Request argument parse errors still return the current `invalid_request`
  response shape and record the trace status when observability is enabled.
- Command output, transcript content, stdout/stderr chunks, and shell input text
  must not be copied into trace or span records.

## LOC Budget

`crates/sandbox-runtime` non-test LOC target: `110-250`.

Expected split:

```text
OperationTrace + span guard/types           70-130
dispatch boundary plumbing                  20-35
selected operation spans                    25-45
exports/module movement, if needed           5-40
```

If runtime non-test LOC trends above 250, stop and narrow the boundary. The
usual cause is accidentally implementing Phase 3.5 lower-level spans or
Phase 4/4.5 trace linking in Phase 3.

Daemon and storage LOC are outside this runtime budget, but should stay focused:
one daemon trace mapper, one store migration, and focused tests.

## Verification Plan

Run required checks:

```sh
cargo fmt --check
cargo check -p sandbox-observability --tests
cargo test -p sandbox-observability
cargo check -p sandbox-runtime --tests
cargo test -p sandbox-runtime operation_trace
cargo check -p sandbox-daemon --tests
cargo test -p sandbox-daemon observability
```

Required behavior tests:

- schema migration adds trace `workspace_id` and `command_session_id` fields;
- schema migration adds only `idx_traces_workspace_time` and
  `idx_traces_command_time` for Phase 3 trace hierarchy lookup;
- synthetic request trace persists all spans under one `trace_id`;
- span `call_index` ordering is stable;
- nested spans get the correct `parent_span_id`;
- early returns close active spans;
- operation errors still persist trace status without changing response shape;
- unknown operations persist a request/root trace when observability is enabled;
- operation argument parse errors persist a request/root trace when
  observability is enabled;
- missing `sandbox_id` disables persistence without failing requests;
- observability store failures do not change operation responses;
- runtime tests do not import `sandbox-observability`;
- selected operation traces contain only the coarse Phase 3 span set;
- command output and transcript content do not appear in trace or span records.

Suggested focused test placement:

- `crates/sandbox-runtime/operation/tests/operation_trace.rs`
  - span nesting;
  - call index order;
  - early return guard closure;
  - disabled trace no-op behavior;
  - selected span set with fake services where practical.
- `crates/sandbox-observability/tests/schema.rs`
  - migration count becomes 3;
  - allowed index set includes the two new trace indexes;
  - trace rows persist `workspace_id` and `command_session_id`.
- `crates/sandbox-daemon/tests/unit/observability.rs`
  - completed trace mapping and persistence;
  - missing sandbox id;
  - store failure does not alter response;
  - unknown operation trace persistence.

## Completion Criteria

Storage:

- [ ] `observability.sqlite` remains the only observability database.
- [ ] `traces.workspace_id` exists.
- [ ] `traces.command_session_id` exists.
- [ ] `idx_traces_workspace_time` exists.
- [ ] `idx_traces_command_time` exists.
- [ ] No `trace_links` table is created.
- [ ] No async trace columns are added in Phase 3.

Runtime boundary:

- [ ] `OperationTrace` lives under `crates/sandbox-runtime/operation`.
- [ ] Runtime does not depend on `sandbox-observability`.
- [ ] Runtime does not import `rusqlite`.
- [ ] Runtime does not know daemon paths or `ObservabilityStore`.
- [ ] Runtime public dispatch accepts `&OperationTrace`.
- [ ] Operation entries and selected dispatch functions accept `&OperationTrace`.
- [ ] The root `dispatch_operation` span is automatic.
- [ ] The selected operations contain only the Phase 3 coarse spans.
- [ ] Runtime non-test LOC stays within `110-250`.

Daemon boundary:

- [ ] Daemon creates enabled traces only when observability is enabled and
  `sandbox_id` is available.
- [ ] Daemon passes disabled traces when persistence is disabled.
- [ ] Daemon persists completed traces after response projection.
- [ ] Daemon does not change operation response payloads.
- [ ] Daemon-owned code maps runtime trace DTOs into `TraceRecord` and
  `SpanRecord`.
- [ ] Trace persistence failures do not change user operation responses.
- [ ] Phase 2 snapshot collection still runs after request handling.

Data boundary:

- [ ] `sandbox_protocol::Response` remains a raw payload wrapper.
- [ ] No `{ "result": ..., "meta": ... }` response envelope is introduced.
- [ ] Command output is not written to trace or span storage.
- [ ] Transcript content is not written to trace or span storage.
- [ ] External observability services are not required.

Verification:

- [ ] `cargo fmt --check` passes.
- [ ] `cargo check -p sandbox-observability --tests` passes.
- [ ] `cargo test -p sandbox-observability` passes.
- [ ] `cargo check -p sandbox-runtime --tests` passes.
- [ ] `cargo test -p sandbox-runtime operation_trace` passes.
- [ ] `cargo check -p sandbox-daemon --tests` passes.
- [ ] `cargo test -p sandbox-daemon observability` passes.

## Open Questions

- The Phase 2 implementation is present in live code, but
  `docs/observability/phase-2-runtime-snapshots.md` still has an unchecked
  completion checklist. That doc cleanup is separate from Phase 3 and should not
  block this spec.
- Panic trace persistence is best effort unless the implementation can catch
  runtime panics inside the blocking closure without changing the broad
  user-facing daemon internal-error behavior. Normal operation errors, unknown
  operations, and argument parse errors are required Phase 3 persistence cases.
