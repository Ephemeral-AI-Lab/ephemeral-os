# Spec Authoring Prompt: Phase 3.5 Targeted Deep Request Spans

Use this prompt to create a full implementation spec at:

```text
/Users/yifanxu/machine_learning/LoVC/ephemeral-ai/ephemeral-os/docs/observability/phase-3-5-targeted-deep-request-spans.md
```

You are an architecture spec author. Your job is to write a concrete,
implementation-ready Phase 3.5 spec for generic, automatic, and dynamic targeted
deep request spans.

Do not implement code. Do not create review findings unless the live code makes
the Phase 3.5 spec impossible to write. Treat docs as proposals and live code as
the source of truth.

## Required Reading

Read these docs first:

```text
docs/observability/sandbox-observability.md
docs/observability/phase-1-observability-foundation.md
docs/observability/phase-2-runtime-snapshots.md
docs/observability/phase-3-request-method-traces.md
```

Then inspect live code, not just docs:

```text
crates/sandbox-runtime/operation/src/lib.rs
crates/sandbox-runtime/operation/src/operation.rs
crates/sandbox-runtime/operation/src/observability.rs
crates/sandbox-runtime/operation/src/services.rs
crates/sandbox-runtime/operation/src/command/service/core.rs
crates/sandbox-runtime/operation/src/command/service/helpers.rs
crates/sandbox-runtime/operation/src/command/service/launch.rs
crates/sandbox-runtime/operation/src/command/service/impls/exec_command.rs
crates/sandbox-runtime/operation/src/command/service/impls/write_command_stdin.rs
crates/sandbox-runtime/operation/src/command/service/impls/read_command_lines.rs
crates/sandbox-runtime/operation/src/layerstack/service/impls/squash.rs
crates/sandbox-runtime/operation/src/workspace_session/service/impls/create_workspace_session.rs
crates/sandbox-runtime/operation/src/workspace_session/service/impls/resolve_session.rs
crates/sandbox-runtime/workspace/src/service/impls/create_workspace.rs
crates/sandbox-runtime/command/src/process.rs
crates/sandbox-runtime/command/src/pty.rs
crates/sandbox-runtime/layerstack/src/stack/ops/squash.rs
crates/sandbox-daemon/src/server/runtime.rs
crates/sandbox-daemon/src/server/dispatch.rs
crates/sandbox-daemon/src/observability/service.rs
crates/sandbox-observability/src/records.rs
crates/sandbox-observability/src/store.rs
crates/sandbox-runtime/operation/tests/operation_trace.rs
crates/sandbox-daemon/tests/unit/observability.rs
```

Use `rg` for call paths and names. Verify the current signatures instead of
assuming the docs are current.

## Phase 3.5 Scope

Phase 3.5 builds on Phase 3 request method traces. It adds policy-gated child
spans under proven-slow or ambiguous Phase 3 parent spans.

Phase 3.5 must implement a generic, automatic, and dynamic mechanism:

- one optional child-span API, such as `measure_if`;
- one stable span-key namespace;
- one request-local policy object containing enabled span keys;
- one existing storage path using `traces` and `spans`;
- policy construction from daemon configuration, recent local trace statistics,
  or both;
- disabled span keys run the original code path without recording a child span.

Keep the first design deliberately simple. An acceptable first implementation is
an in-memory enabled-key set populated from daemon configuration and updated
after completed request traces. Do not design a new adaptive observability
subsystem.

Phase 3.5 must not implement:

- generic automatic discovery of every Rust function call;
- broad `tracing` attribute adoption;
- compiler instrumentation;
- eBPF or profiler integration;
- new observability tables or schema migrations;
- background tuning workers;
- percentile math;
- new query APIs;
- `trace_links`;
- Phase 4 async finalization traces;
- Phase 4.5 cross-process namespace-runner traces;
- manager aggregation;
- response envelopes such as `{ result, meta }`;
- command transcript or command output ingestion;
- runtime dependency on `sandbox-observability`, `rusqlite`, daemon paths, or
  store/record types.

## Required Current-State Grounding

The spec must include a "Current Repo Grounding" section that confirms:

- whether Phase 3 `OperationTrace`, `SpanGuard`, `CompletedOperationTrace`, and
  `CompletedOperationSpan` already exist;
- the current `OperationTrace` fields and whether it can accept a policy without
  storing daemon/storage concepts;
- the current `measure_optional` helper shape;
- the current `dispatch_operation` and operation dispatch signatures;
- where Phase 3 service-method spans are currently recorded;
- how daemon dispatch creates and completes `OperationTrace`;
- how `DaemonObservability::insert_completed_operation_trace` maps spans into
  storage rows;
- whether `TraceRecord`, `SpanRecord`, and `ObservabilityStore::insert_trace`
  are enough for Phase 3.5 without schema changes;
- where candidate child-span call sites currently live;
- which candidate call sites cross crate or process boundaries and therefore
  need caller-owned spans or deferral.

Use exact file paths and current symbol names.

## Required Architecture Decisions

The spec must make these decisions explicit.

## Required Self-Critical Architecture Check

Before the detailed file plan, the spec must include a self-critical architecture
check. This is not a generic pros/cons section. It must challenge the proposed
design and either simplify it or explain why the extra complexity is necessary.

The check must answer these questions directly.

### Simplicity

- What is the smallest design that satisfies generic, automatic, and dynamic
  child span enablement?
- Can the design be reduced to an enabled-key set plus `measure_if`?
- Does the design add any new table, background worker, query API, cache,
  registry module, trait hierarchy, or config surface that Phase 3.5 can avoid?
- Which proposed pieces would be deleted if the first implementation had to fit
  in the lower half of the 60-130 runtime LOC budget?
- Does the happy path remain readable at each span call site?

### Genericity

- Is the mechanism generic because the API and policy are reusable, rather than
  because every function is automatically instrumented?
- Can a future operation add child spans by registering span keys and calling the
  same API, without adding operation-specific daemon plumbing?
- Are span keys stable domain names instead of storage ids, display-only labels,
  or function names that will churn during refactors?
- Is the policy independent of SQLite, daemon paths, request ids, response JSON,
  command output, and `sandbox-observability` records?

### Extensibility

- How would the next runtime operation add eligible child spans with minimal
  code?
- How would config-driven enablement and recent-trace-driven enablement compose
  without changing runtime call sites?
- What would need to change if later phases add a product query API?
- Which boundaries intentionally remain deferred to Phase 4 or Phase 4.5?
- What extension point exists without creating a dependency from lower runtime
  crates back to `sandbox-runtime/operation`?

### Rejection Criteria

The spec must reject or revise its own architecture if it requires any of these
for Phase 3.5:

- a new persistent schema;
- a profiler-like function discovery system;
- broad `tracing` annotations;
- a background tuning service;
- a large trait hierarchy for span policies;
- operation-specific daemon persistence paths;
- cross-process runner internals as ordinary child spans;
- public response-shape changes.

If the self-critical check finds that a simpler design works, the spec must use
the simpler design.

### Generic Span Policy

Define a minimal runtime-side policy model.

The design should include:

- a stable `SpanKey` representation, preferably string-backed and static where
  possible;
- an `OperationTrace` policy field or equivalent request-local policy access;
- a child-span API such as:

  ```rust
  pub fn measure_if<T>(&self, span_key: SpanKey, call: impl FnOnce() -> T) -> T;
  ```

- a rule that disabled keys call through without recording;
- a rule that policy state does not include SQLite handles, daemon paths,
  request ids, sandbox ids, response JSON, storage row ids, or command output;
- a small enabled-key set as the first policy implementation.

The spec should decide whether span keys and display names are the same string
or separate values. Prefer the simpler choice unless live code shows a clear
need for separation.

### Policy Construction

Specify where the policy is constructed and how it reaches runtime dispatch.

The expected direction is:

- daemon decides whether observability is enabled as in Phase 3;
- daemon creates an `OperationTrace` with a policy when tracing is enabled;
- daemon policy can start from static config and recent completed local traces;
- runtime receives only the neutral trace context;
- runtime does not read daemon config, SQLite, or observability stores.

Keep the policy update path simple. If recent traces are used, update an
in-memory enabled-key set after completed request trace insertion. Do not add a
background worker or query API just to support Phase 3.5.

### Candidate Span Keys

Use stable domain keys for eligible in-process boundaries. The spec must include
an initial key registry, but the registry is eligibility, not an always-on list.

Start with these candidate groups:

```text
command.exec.resolve_workspace
command.exec.workspace_session.resolve_session
command.exec.workspace_session.create_workspace_session
command.exec.workspace_runtime.create_workspace
command.exec.layerstack.snapshot_or_lease

command.exec.start_command_process
command.exec.workspace_handle.entry
command.exec.spawn.prepare
command.exec.spawn.process_spawn
command.exec.spawn.build_namespace_runner_request
command.exec.spawn.spawn_current_exe_ns_runner

layerstack.squash.open_layerstack
layerstack.squash.squash_layerstack
```

The spec must say which keys can be implemented in Phase 3.5 without crossing an
awkward crate boundary, and which keys should remain caller-owned boundary spans
or be deferred.

### Crate and Process Boundaries

Phase 3.5 spans stay on the same request trace only when the work runs in the
daemon or runtime process.

Specify:

- no dependency from lower workspace, layerstack, command, or namespace crates
  back to `sandbox-runtime/operation` just to carry trace context;
- no `sandbox-observability` types in lower runtime crates;
- if a lower crate boundary cannot accept neutral trace context cleanly, keep a
  span around the caller-owned boundary;
- parent-side namespace-runner request build/spawn spans may be eligible;
- `runner::run`, `run_setns`, shell execution, and command wait-loop internals
  are Phase 4.5, not Phase 3.5 child spans.

### Storage and Response Shape

Specify that Phase 3.5 uses the existing Phase 3 trace persistence path. It
should add child spans to the completed runtime trace and let the existing daemon
mapping persist them as ordinary `SpanRecord` rows.

Do not add storage columns, indexes, `trace_links`, response metadata, or
external observability services.

## Required Spec Structure

Write `docs/observability/phase-3-5-targeted-deep-request-spans.md` with this
shape:

```text
# Phase 3.5 Targeted Deep Request Spans

Status: draft implementation spec

Parent spec
Builds on

Exact Goal
Current Repo Grounding
Non-Goals
Architecture
  Generic Span Policy
  Policy Construction
  Span Key Registry
  Runtime API
  Daemon Policy State
  Storage and Response Shape
  Crate and Process Boundaries
Self-Critical Architecture Check
Detailed File Plan
Expected Struct and Signature Changes
Candidate Span Decisions
Failure Policy
LOC Budget
Verification Plan
Completion Criteria
Open Questions
```

## Detailed File Plan Requirements

The spec must name expected files and keep the structure small. Use the existing
crate layout.

Expected additions or edits may include:

```text
crates/sandbox-runtime/operation/src/observability.rs
crates/sandbox-runtime/operation/src/lib.rs
crates/sandbox-runtime/operation/src/operation.rs
crates/sandbox-runtime/operation/src/command/service/impls/exec_command.rs
crates/sandbox-runtime/operation/src/command/service/helpers.rs
crates/sandbox-runtime/operation/src/command/service/launch.rs
crates/sandbox-runtime/operation/src/layerstack/service/impls/squash.rs
crates/sandbox-daemon/src/server/dispatch.rs
crates/sandbox-daemon/src/observability/service.rs
crates/sandbox-daemon/tests/unit/observability.rs
crates/sandbox-runtime/operation/tests/operation_trace.rs
```

Avoid new modules unless the spec can justify them with real complexity. Do not
add a new crate. Do not add manager files. Do not split the storage layer for
Phase 3.5.

## LOC Budget

The Phase 3.5 spec must include a runtime non-test LOC budget and explain where
the cost goes.

Use this target from the parent spec:

```text
crates/sandbox-runtime non-test LOC: 60-130
```

The implementation should prefer the lower half of that range. Treat a design
that needs new storage schema, background workers, a profiler-like engine, or
large public API churn as too complex for Phase 3.5.

Expected split:

```text
span key/policy additions                     20-40
measure_if-style runtime API                  10-20
selected child span call-site wiring          20-50
daemon policy creation/update                 10-25
tests and small exports as needed
```

If the spec predicts more than 130 runtime non-test LOC, it must stop and revise
the boundary.

## Verification Plan Requirements

Include focused checks:

```sh
cargo fmt --check
cargo check -p sandbox-runtime --tests
cargo test -p sandbox-runtime operation_trace
cargo check -p sandbox-daemon --tests
cargo test -p sandbox-daemon observability
cargo check -p sandbox-observability --tests
cargo test -p sandbox-observability
```

Required behavior tests:

- disabled span keys do not record child spans;
- enabled span keys record child spans with correct parentage under Phase 3
  parent spans;
- call index ordering remains stable with mixed enabled and disabled child keys;
- existing Phase 3 coarse spans still appear;
- daemon can create a trace policy without changing operation responses;
- trace persistence uses existing `TraceRecord`, `SpanRecord`, and
  `ObservabilityStore::insert_trace`;
- no new schema migration is required;
- runtime tests do not import `sandbox-observability`;
- missing `sandbox_id` or observability store failures still do not fail user
  operations;
- no cross-process runner internals appear as Phase 3.5 child spans.

## Output Rules

- Write the complete spec, not a summary.
- Use exact file paths and current symbol names.
- Separate live-code facts from design decisions.
- Keep the Phase 3.5 design generic, automatic, dynamic, and simple.
- Include the self-critical architecture check and let it simplify the design
  before the file plan.
- Do not design a profiler.
- Do not recommend aliases, compatibility shims, fallback response shapes, or
  response-envelope migrations.
- Do not make command transcripts or command output part of observability.
- Do not require external observability services.
- If live code has drifted enough that this prompt is wrong, document the drift
  in an "Open Questions" section and keep the proposed correction minimal.
