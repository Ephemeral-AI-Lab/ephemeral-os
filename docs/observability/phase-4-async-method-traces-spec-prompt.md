# Spec Authoring Prompt: Phase 4 Async Method Traces

Use this prompt to create a full implementation spec at:

```text
/Users/yifanxu/machine_learning/LoVC/ephemeral-ai/ephemeral-os/docs/observability/phase-4-async-method-traces.md
```

You are an architecture spec author. Your job is to write a concrete,
implementation-ready Phase 4 spec for linked async method traces.

Do not implement code. Do not create review findings unless the live code makes
the Phase 4 spec impossible to write. Treat docs as proposals and live code as
the source of truth.

Bias hard toward architectural cleanness and simplicity. The final spec should
choose the smallest design that records useful command-finalization async traces
without introducing a general event bus, observability runtime, storage leakage,
or broad async tracing framework.

## Required Reading

Read these docs first:

```text
docs/observability/sandbox-observability.md
docs/observability/phase-1-observability-foundation.md
docs/observability/phase-2-runtime-snapshots.md
docs/observability/phase-3-request-method-traces.md
docs/observability/phase-3-5-targeted-deep-request-spans.md
```

Then inspect live code, not just docs:

```text
crates/sandbox-runtime/operation/src/lib.rs
crates/sandbox-runtime/operation/src/operation.rs
crates/sandbox-runtime/operation/src/observability.rs
crates/sandbox-runtime/operation/src/services.rs
crates/sandbox-runtime/operation/src/command/service/core.rs
crates/sandbox-runtime/operation/src/command/service/completion.rs
crates/sandbox-runtime/operation/src/command/service/finalize.rs
crates/sandbox-runtime/operation/src/command/service/launch.rs
crates/sandbox-runtime/operation/src/command/service/process_store.rs
crates/sandbox-runtime/operation/src/command/service/impls/exec_command.rs
crates/sandbox-runtime/operation/src/workspace_session/service/impls/destroy_session.rs
crates/sandbox-runtime/operation/src/workspace_remount/service/impls/remount_workspace_session.rs
crates/sandbox-daemon/src/server/dispatch.rs
crates/sandbox-daemon/src/server/runtime.rs
crates/sandbox-daemon/src/observability/service.rs
crates/sandbox-observability/src/records.rs
crates/sandbox-observability/src/store.rs
crates/sandbox-runtime/operation/tests/operation_trace.rs
crates/sandbox-runtime/operation/tests/exec_command.rs
crates/sandbox-runtime/operation/tests/command_remount.rs
crates/sandbox-daemon/tests/unit/observability.rs
```

Use `rg` for call paths and names. Verify current signatures instead of
assuming the docs are current.

## Phase 4 Scope

Phase 4 adds linked async method traces for lifecycle work that happens after
the original request trace has returned.

The first implementation is command finalization only:

- record one linked async trace for command finalization;
- link it to the original request with `origin_request_id`;
- link it to the command with `correlation_kind = "command_session_id"` and
  `correlation_id = command_session_id`;
- populate `workspace_id` and `command_session_id` when known;
- keep the async span model compatible with existing `OperationTrace` /
  `CompletedOperationTrace` where possible;
- persist completed async traces through daemon-owned observability storage.

The final spec must print this exact rollout-budget line in an "Expected LOC"
section:

```text
Expected `crates/sandbox-runtime` change: 60-110 non-test LOC, with 60-80 preferred.
```

If the proposed design needs more than 110 non-test LOC in
`crates/sandbox-runtime`, reject the architecture and simplify it before writing
the detailed plan. If the design cannot fit in the preferred 60-80 LOC band,
the spec must explain which live-code constraint forces the extra code.

Phase 4 may mention future linked traces for workspace destroy/remount, but only
as a future extension unless live code already runs those paths outside the
original request.

Phase 4 must not implement:

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

## Required Current-State Grounding

The spec must include a "Current Repo Grounding" section that confirms:

- whether Phase 3 and Phase 3.5 trace helpers are present in live code;
- the current fields and methods of `OperationTrace`,
  `CompletedOperationTrace`, and `CompletedOperationSpan`;
- whether `OperationTrace` is safe to reuse for async traces without storing
  daemon or SQLite concepts in runtime code;
- how daemon dispatch currently creates, completes, and persists request traces;
- how `DaemonObservability::insert_completed_operation_trace` maps runtime
  spans into storage rows;
- whether `TraceRecord`, `SpanRecord`, and `ObservabilityStore::insert_trace`
  already have enough shape for async fields, or which daemon/storage-only
  schema change is needed;
- how `CommandOperationService` constructs the completion finalizer;
- the current `CommandCompletionSender`, `CommandCompletionPromise`, and
  `CommandCompletion` payload shape;
- how `CommandCompletionPromise::resolve` sends completion work;
- how `spawn_completion_finalizer` receives completions;
- the exact call path through `complete_terminal_command_with_services`,
  `begin_terminal_completion`, `terminal_result`,
  `apply_workspace_completion_policy`, and `complete_command_record`;
- where `command_session_id`, `workspace_session_id`, command ownership, and
  one-shot destroy information are available during finalization;
- whether workspace destroy/remount currently runs synchronously inside the
  original request or asynchronously after request return.

Use exact file paths and current symbol names.

## Required Self-Critical Architecture Check

Before the detailed file plan, the spec must include a self-critical
architecture check. This is not a generic pros/cons section. It must challenge
the proposed design and either simplify it or explain why the extra complexity
is necessary.

The check must answer these questions directly.

### Minimal Shape

- What is the smallest design that records one command-finalization async trace?
- Can the design be reduced to:
  - a small async trace link captured when `exec_command` creates the command;
  - one optional trace collector around the existing finalizer path;
  - one narrow daemon-owned persistence hook;
  - no new runtime storage dependency?
- Can existing `OperationTrace` and `CompletedOperationTrace` be reused rather
  than creating parallel async span structs?
- Can the existing completion channel carry the trace link without a second
  background worker?
- Does every new runtime field have a single reason to exist?
- Which pieces would be deleted if the design had to fit in 60 non-test LOC in
  `crates/sandbox-runtime`?

### Ownership Boundaries

- Does runtime remain responsible only for collecting neutral spans and runtime
  correlation identifiers?
- Does daemon remain responsible for request ids, sandbox ids, storage row
  mapping, SQLite, and observability persistence?
- Does the design avoid pushing `sandbox-observability` records, store handles,
  daemon paths, response JSON, or SQLite concepts into runtime crates?
- Is any new callback or trait runtime-owned, storage-neutral, and narrow?
- Can command finalization still run if observability is disabled or persistence
  fails?

### Simplicity Rejections

Reject or revise the architecture if it requires any of these for Phase 4:

- a global event bus;
- a registry of async task types;
- a second finalizer thread only for tracing;
- a broad trace sink trait with many methods;
- a new runtime observability manager;
- operation-specific daemon persistence code paths beyond async trace mapping;
- storing daemon request metadata directly in low-level command or workspace
  crates;
- changing command responses;
- changing command transcript semantics;
- adding namespace-runner trace metadata.

If the self-critical check finds that a simpler design works, the spec must use
the simpler design.

## Required Architecture Decisions

The spec must make these decisions explicit.

### Async Trace Link

Define the smallest link object needed to connect finalization back to the
request:

```text
origin_request_id
sandbox_id or daemon-provided sandbox identity, if needed for storage mapping
async_name = "command_finalization"
correlation_kind = "command_session_id"
correlation_id = command_session_id
workspace_id / workspace_session_id
command_session_id
```

The spec must decide where this link is created and how much of it belongs in
runtime. Prefer a storage-neutral runtime link that contains stable ids already
known by runtime, plus daemon-side mapping for daemon-only fields.

If `origin_request_id` is not currently available at the point where the command
completion promise is created, specify the minimal dispatch-signature or trace
context change needed to pass it. Do not introduce a broad request context object
unless the live code proves that smaller values are insufficient.

### Completion Finalizer Instrumentation

Specify the expected command-finalization span tree. Start from the current
documented chain and revise only when live code differs:

```text
command_finalization
  completion_watcher
    CommandProcess::take_exit
    CommandCompletionPromise::resolve
  completion_finalizer
    complete_terminal_command_with_services
      begin_terminal_completion
      terminal_result
      apply_workspace_completion_policy
        if one-shot:
          WorkspaceSessionService::destroy_session
      complete_command_record
```

The spec must be self-critical about span count. Prefer fewer spans if the
watcher path cannot be traced without awkward plumbing. A valid minimal Phase 4
can start with:

```text
command_finalization
  completion_finalizer
  complete_terminal_command_with_services
  apply_workspace_completion_policy
  complete_command_record
```

Only include `completion_watcher` spans if the current watcher path can attach
the link without contorting `CommandCompletionPromise` or making disabled tracing
hard to read.

### Persistence Boundary

Specify how the completed async trace reaches daemon-owned observability
storage.

The preferred shape is one narrow, storage-neutral callback or sink supplied by
the daemon/runtime construction boundary, for example:

```rust
pub trait AsyncTraceSink: Send + Sync {
    fn record_async_trace(&self, trace: CompletedAsyncOperationTrace);
}
```

Do not copy this pseudocode blindly. The spec must verify whether a trait is
actually needed. If a closure, existing service boundary, or existing daemon
finalization path is simpler, use that instead.

The sink contract must state:

- disabled observability means no async trace is created or emitted;
- sink failure must not fail command finalization;
- persistence errors are logged or counted at the daemon boundary only if an
  existing pattern exists;
- runtime never opens SQLite or imports `sandbox-observability`;
- daemon maps runtime span data into `TraceRecord` / `SpanRecord`.

### Trace IDs and Span IDs

Use the target trace-id shape unless live code has a better established helper:

```text
trace_id = "async:" + async_name + ":" + correlation_kind + ":" + correlation_id
span_id = trace_id + ":span:" + call_index
```

If multiple async finalization traces can exist for the same command session,
add a monotonic suffix and explain why. Otherwise, do not add sequencing.

### Workspace Destroy and Remount

The Phase 4 spec must inspect workspace destroy/remount live paths and decide:

- one-shot workspace destroy during command finalization should be included as a
  child span when it runs inside command finalization;
- workspace destroy/remount should get separate linked async traces only if they
  run outside the original request in current or immediate Phase 4 code;
- otherwise, defer separate workspace destroy/remount async traces with no
  placeholder code.

### Tests and Verification

The spec must include focused verification, including:

```text
cargo test -p sandbox-runtime-operation operation_trace
cargo test -p sandbox-runtime-operation exec_command
cargo test -p sandbox-daemon observability
```

Adjust exact package or test names after checking the workspace. Add one focused
test for disabled observability showing command finalization does not pay
complexity or persistence cost, and one focused test for linked async trace
metadata when observability is enabled.

Do not require broad workspace tests unless the file plan changes shared
contracts beyond the command finalization trace path.

## Required Output Shape

The generated Phase 4 spec must use these sections:

```text
# Phase 4: Async Method Traces

## Purpose
## Current Repo Grounding
## Architecture Cleanness Check
## Scope
## Non-Goals
## Data Model
## Runtime Changes
## Daemon and Storage Changes
## Command Finalization Span Plan
## Workspace Destroy/Remount Decision
## File-by-File Plan
## Expected LOC
## Verification
## Deferred Work
```

The "Expected LOC" section must print:

```text
Expected `crates/sandbox-runtime` change: 60-110 non-test LOC, with 60-80 preferred.
```

It may also list daemon and storage LOC estimates if useful, but the
`crates/sandbox-runtime` line is required.

## Final Quality Bar

Before finishing, reread the spec and remove unnecessary abstractions. The spec
is not acceptable if it makes Phase 4 feel like a generic observability platform
instead of a narrow linked async trace for command finalization.

The final spec should make the implementation path obvious, small, and
reversible.
