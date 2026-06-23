# Spec Authoring Prompt: Phase 3 Request Method Traces

Use this prompt to create a full implementation spec at:

```text
/Users/yifanxu/machine_learning/LoVC/ephemeral-ai/ephemeral-os/docs/observability/phase-3-request-method-traces.md
```

You are an architecture spec author. Your job is to write a concrete,
implementation-ready Phase 3 spec for coarse request method traces.

Do not implement code. Do not create review findings unless the live code makes
the Phase 3 spec impossible to write. Treat docs as proposals and live code as
the source of truth.

## Required Reading

Read these docs first:

```text
docs/observability/sandbox-observability.md
docs/observability/phase-1-observability-foundation.md
docs/observability/phase-2-runtime-snapshots.md
```

Then inspect live code, not just docs:

```text
crates/sandbox-protocol/src/request.rs
crates/sandbox-protocol/src/response.rs
crates/sandbox-runtime/operation/src/lib.rs
crates/sandbox-runtime/operation/src/operation.rs
crates/sandbox-runtime/operation/src/services.rs
crates/sandbox-runtime/operation/src/observability.rs
crates/sandbox-runtime/operation/src/command/service/impls/mod.rs
crates/sandbox-runtime/operation/src/command/service/impls/exec_command.rs
crates/sandbox-runtime/operation/src/command/service/impls/write_command_stdin.rs
crates/sandbox-runtime/operation/src/command/service/impls/read_command_lines.rs
crates/sandbox-runtime/operation/src/layerstack/service/impls/mod.rs
crates/sandbox-runtime/operation/src/layerstack/service/impls/squash.rs
crates/sandbox-runtime/operation/src/command/service/core.rs
crates/sandbox-runtime/operation/src/command/service/process_store.rs
crates/sandbox-daemon/src/server/runtime.rs
crates/sandbox-daemon/src/server/dispatch.rs
crates/sandbox-daemon/src/observability/mod.rs
crates/sandbox-daemon/src/observability/service.rs
crates/sandbox-observability/src/records.rs
crates/sandbox-observability/src/store.rs
crates/sandbox-observability/tests/schema.rs
```

Use `rg` for call paths and names. Verify the current signatures instead of
assuming the docs are current.

## Phase 3 Scope

Phase 3 implements coarse request method traces only:

- create one request-local `OperationTrace` at daemon dispatch;
- pass trace context into runtime operation dispatch;
- add the automatic root `dispatch_operation` span;
- add coarse selected spans for:
  - `exec_command`;
  - `write_command_stdin`;
  - `read_command_lines`;
  - `squash`;
- persist completed request traces and spans through daemon-owned
  observability storage;
- keep `sandbox_protocol::Response` unchanged.

Phase 3 must not implement:

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
- response envelopes such as `{ result, meta }`;
- runtime SQLite writes;
- a `sandbox-observability` dependency from `sandbox-runtime`.

## Required Current-State Grounding

The spec must include a "Current Repo Grounding" section that confirms:

- whether Phase 2 snapshot code is present and what pieces are still dirty or
  incomplete;
- the current shape of `SandboxDaemonServer`, including any existing
  `DaemonObservability` field;
- the current daemon dispatch flow around `tokio::task::spawn_blocking`;
- the current `OperationEntry` and `dispatch_operation` signatures;
- the current public runtime operation entries;
- the current Phase 1/2 `TraceRecord`, `SpanRecord`, and `insert_trace` shape;
- whether `traces` currently has `workspace_id` and `command_session_id`;
- that `Request` already carries `request_id`, `op`, `scope`, and `args`;
- that command output and transcripts stay outside observability storage.

Use exact file paths and current symbol names.

## Required Architecture Decisions

The spec must make these decisions explicit.

### Trace Context Ownership

Define `OperationTrace` as runtime-side request context that records spans but
does not know SQLite, daemon paths, or `sandbox-observability` record types.

Specify:

- fields for `trace_id`, `request_id`, optional `sandbox_id`, `operation`,
  optional `workspace_id`, optional `command_session_id`, monotonic start time,
  Unix start time, active parent stack, completed spans, and terminal status;
- how `trace_id = "request:" + request_id` is formed;
- whether the implementation should pass `&OperationTrace` with interior
  mutability or `&mut OperationTrace`, and why;
- `enter` for scope spans;
- `measure` for one call or expression;
- how spans finish on early return, panic unwind, or normal return;
- how errors are represented without changing operation response payloads;
- how the trace is completed before daemon persistence.

### Dispatch Boundary

Specify the signature changes needed in:

```text
crates/sandbox-runtime/operation/src/operation.rs
crates/sandbox-runtime/operation/src/command/service/impls/mod.rs
crates/sandbox-runtime/operation/src/layerstack/service/impls/mod.rs
crates/sandbox-runtime/operation/src/command/service/impls/*.rs
crates/sandbox-runtime/operation/src/layerstack/service/impls/squash.rs
crates/sandbox-daemon/src/server/dispatch.rs
```

The spec must show the expected new function-pointer and dispatch shape in Rust
pseudocode. It must also explain how unknown operations and parse errors should
still get useful request/root spans without changing response payloads.

### Selected Phase 3 Spans

Use this coarse first-pass span policy:

```text
dispatch_operation
<operation>::dispatch
```

For `exec_command`, add only:

```text
CommandOperationService::exec_command
resolve_exec_workspace
start_command_process
initial_exec_yield
```

For `write_command_stdin`, add only:

```text
CommandOperationService::write_command_stdin
write_or_cancel
wait_for_command_yield
```

For `read_command_lines`, add only:

```text
CommandOperationService::read_command_lines
read_transcript_window
```

For `squash`, add only:

```text
LayerStackService::squash
```

The spec must explicitly defer these helper or lower-level spans:

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

Explain that Phase 3 spans are inclusive timings. For example,
`resolve_exec_workspace` may include workspace/session/layerstack work, and
`start_command_process` may include parent-side spawn preparation.

### Storage Migration

If the live schema still lacks request trace hierarchy fields, specify a Phase 3
migration that adds only:

```sql
ALTER TABLE traces ADD COLUMN workspace_id TEXT;
ALTER TABLE traces ADD COLUMN command_session_id TEXT;

CREATE INDEX IF NOT EXISTS idx_traces_workspace_time
  ON traces(sandbox_id, workspace_id, started_at_unix_ms);

CREATE INDEX IF NOT EXISTS idx_traces_command_time
  ON traces(sandbox_id, command_session_id, started_at_unix_ms);
```

Update `TraceRecord` and `ObservabilityStore::insert_trace` accordingly.

Do not add `trace_links`, `origin_request_id`, `correlation_kind`,
`correlation_id`, or `async_name` in Phase 3.

### Daemon Persistence

Specify how `SandboxDaemonServer::dispatch_request` should:

- create the request trace only when observability is enabled and `sandbox_id`
  is available, or create a disabled/no-op trace if that keeps runtime code
  simpler;
- pass the trace into `sandbox_runtime::dispatch_operation`;
- project the operation response exactly as today;
- persist the completed request trace and spans after response projection;
- ignore or record observability write failures without changing the user
  operation response;
- continue to trigger Phase 2 snapshot collection after requests.

The daemon should map runtime trace completion into `TraceRecord` and
`SpanRecord`. Runtime must not import `sandbox-observability`.

## Required Spec Structure

Write `docs/observability/phase-3-request-method-traces.md` with this shape:

```text
# Phase 3 Request Method Traces

Status: draft implementation spec

Parent spec
Builds on

Exact Goal
Current Repo Grounding
Non-Goals
Architecture
  Runtime Trace Context
  Dispatch Boundary
  Daemon Persistence
  Storage Migration
  Selected Span Policy
  Phase 3.5 / Phase 4 / Phase 4.5 Boundaries
Detailed File Plan
Expected Struct and Signature Changes
Failure Policy
LOC Budget
Verification Plan
Completion Criteria
Open Questions
```

## Detailed File Plan Requirements

The spec must name expected files and keep the structure small. Use the existing
crate layout.

Expected additions or edits:

```text
crates/sandbox-runtime/operation/src/observability.rs
crates/sandbox-runtime/operation/src/observability/trace.rs
crates/sandbox-runtime/operation/src/operation.rs
crates/sandbox-runtime/operation/src/command/service/impls/exec_command.rs
crates/sandbox-runtime/operation/src/command/service/impls/write_command_stdin.rs
crates/sandbox-runtime/operation/src/command/service/impls/read_command_lines.rs
crates/sandbox-runtime/operation/src/layerstack/service/impls/squash.rs
crates/sandbox-daemon/src/server/dispatch.rs
crates/sandbox-daemon/src/observability/service.rs
crates/sandbox-daemon/src/observability/trace.rs
crates/sandbox-observability/src/records.rs
crates/sandbox-observability/src/store.rs
crates/sandbox-observability/tests/schema.rs
```

If `observability.rs` cannot remain both a file and a module directory in the
current Rust layout, specify the cleanest module move, for example:

```text
crates/sandbox-runtime/operation/src/observability/mod.rs
crates/sandbox-runtime/operation/src/observability/snapshot.rs
crates/sandbox-runtime/operation/src/observability/trace.rs
```

Do not add a new crate. Do not add manager files.

## LOC Budget

The Phase 3 spec must include a runtime non-test LOC budget and explain where
the cost goes.

Use this target:

```text
crates/sandbox-runtime non-test LOC: 110-250
```

Expected split:

```text
OperationTrace + span guard/types           70-130
dispatch boundary plumbing                  20-35
selected operation spans                    25-45
exports/module movement, if needed           5-40
```

If the spec predicts more than 250 runtime non-test LOC, it must stop and revise
the boundary. The usual mistake is implementing Phase 3.5 or lower-crate tracing
inside Phase 3.

## Verification Plan Requirements

Include focused checks:

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
- synthetic request trace persists all spans under one `trace_id`;
- span `call_index` ordering is stable;
- nested spans get the correct `parent_span_id`;
- early returns close active spans;
- operation errors still persist trace status without changing response shape;
- missing `sandbox_id` disables persistence without failing requests;
- observability store failures do not change operation responses;
- runtime tests do not import `sandbox-observability`;
- selected operation traces contain only the coarse Phase 3 span set.

## Output Rules

- Write the complete spec, not a summary.
- Use exact file paths and current symbol names.
- Separate live-code facts from design decisions.
- Keep Phase 3 narrower than Phase 3.5, Phase 4, and Phase 4.5.
- Do not recommend aliases, compatibility shims, fallback response shapes, or
  response-envelope migrations.
- Do not make command transcripts or command output part of observability.
- Do not require external observability services.
- If live code has drifted enough that this prompt is wrong, document the drift
  in an "Open Questions" section and keep the proposed correction minimal.
