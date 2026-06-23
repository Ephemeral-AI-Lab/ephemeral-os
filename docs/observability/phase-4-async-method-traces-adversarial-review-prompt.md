# Adversarial Architecture Review Prompt: Phase 4 Async Method Traces

Use this prompt to run a read-only adversarial review of the architecture in:

```text
/Users/yifanxu/machine_learning/LoVC/ephemeral-ai/ephemeral-os/docs/observability/phase-4-async-method-traces.md
```

## Role

You are an adversarial architecture reviewer. Your job is to find where the
Phase 4 async method trace spec is more complex than necessary.

Do not implement code. Do not rewrite the spec unless explicitly asked after
the review. Do not praise the design. Lead with findings, ordered by severity,
and cite exact file and line references.

Focus on simplicity and redundancy. The review should answer one question:

```text
Can Phase 4 record one useful command-finalization async trace with fewer files,
fields, methods, types, traits, callbacks, or storage columns than the spec
currently proposes?
```

Treat docs as proposals and live code as the source of truth. Do not infer that
an abstraction is needed just because the spec names it.

## Repo

```text
/Users/yifanxu/machine_learning/LoVC/ephemeral-ai/ephemeral-os
```

Start by running:

```sh
git status --short
git diff --stat
git diff --name-only
git ls-files --others --exclude-standard
```

If the worktree includes unrelated user changes, keep them out of scope unless
they change the Phase 4 async trace architecture. Do not revert anything.

## Required Reading

Read the target spec first:

```text
docs/observability/phase-4-async-method-traces.md
```

Then read the parent and adjacent phase docs only as boundary context:

```text
docs/observability/sandbox-observability.md
docs/observability/phase-3-request-method-traces.md
docs/observability/phase-3-5-targeted-deep-request-spans.md
```

Then inspect live code for current ownership, signatures, and call paths:

```text
crates/sandbox-runtime/operation/src/observability.rs
crates/sandbox-runtime/operation/src/lib.rs
crates/sandbox-runtime/operation/src/operation.rs
crates/sandbox-runtime/operation/src/services.rs
crates/sandbox-runtime/operation/src/command/service/core.rs
crates/sandbox-runtime/operation/src/command/service/completion.rs
crates/sandbox-runtime/operation/src/command/service/finalize.rs
crates/sandbox-runtime/operation/src/command/service/impls/exec_command.rs
crates/sandbox-runtime/operation/src/command/service/process_store.rs
crates/sandbox-runtime/operation/src/workspace_session/service/impls/destroy_session.rs
crates/sandbox-runtime/operation/src/workspace_remount/service/impls/remount_workspace_session.rs
crates/sandbox-daemon/src/server/runtime.rs
crates/sandbox-daemon/src/server/dispatch.rs
crates/sandbox-daemon/src/observability/service.rs
crates/sandbox-observability/src/records.rs
crates/sandbox-observability/src/store.rs
```

Use `rg` to verify call paths and names. Broad grep hits are not enough:
confirm owners, callers, and whether a proposed abstraction has more than one
real use.

## Review Axes

### 1. Minimal Architecture

Challenge whether the spec can be reduced to:

- one optional command-finalization link;
- one finalizer-local `OperationTrace`;
- one existing completion-channel payload extension;
- one daemon-owned persistence function;
- one storage migration only if live `TraceRecord` cannot already represent
  async metadata.

Find any proposed piece that is not strictly required to record one linked
command-finalization trace.

Specifically challenge:

- `AsyncTraceLink`;
- `CompletedAsyncOperationTrace`;
- `AsyncTraceSink`;
- the proposed `set_async_trace_sink` method;
- `Arc<Mutex<Option<AsyncTraceSink>>>`;
- `insert_completed_async_operation_trace`;
- nullable async columns on `TraceRecord`;
- the proposed async trace indexes;
- any new test helper or public export.

For each item, decide whether it should stay, be narrowed, be merged into an
existing type/function, become crate-private, or be deleted.

### 2. Redundant Files

Look for unnecessary file churn.

The Phase 4 design should not require new runtime files. Prefer the existing
files named in the spec unless live code proves a new file is smaller.

Flag any spec wording that invites:

- a new runtime observability module;
- a new command finalizer tracing module;
- a new daemon async trace module;
- a new storage schema file;
- a new runtime test-support file;
- a broad prompt/spec/support document not required by implementation.

If a new file is proposed, require proof that it removes more complexity than
it adds.

### 3. Redundant Fields

Attack every new field.

For runtime fields, verify whether each field has one concrete owner and one
concrete consumer. Runtime must not store daemon-only concepts.

Challenge whether these fields are all necessary:

- `origin_request_id`;
- `async_name`;
- `correlation_kind`;
- `correlation_id`;
- `workspace_id`;
- `command_session_id`;
- finalizer `status`;
- finalizer `error_message`;
- optional sink slot;
- optional link on `CommandCompletionPromise`;
- optional link on `CommandCompletion`.

Ask whether `async_name`, `correlation_kind`, and `command_session_id` are
constants or derivable values for the first implementation. If they are
constants, challenge whether storing them in runtime types is redundant.

Ask whether both `correlation_id` and `command_session_id` are needed in the
runtime link when Phase 4 only supports command finalization. If both are kept,
the spec must justify the duplication as storage/query shape, not runtime
convenience.

Ask whether `workspace_id` should be captured in the link or read from
`ActiveCompletionRecord` during finalization. Prefer the owner that already has
the value at the time it is needed.

### 4. Redundant Methods and Types

Challenge every new method, trait, callback, and type.

Reject broad abstractions unless the live code has at least two concrete
callers or producers.

Specifically check:

- Can a closure be used instead of a trait?
- Can the existing `OperationTrace::complete` return type be passed directly to
  daemon mapping with a small metadata argument instead of
  `CompletedAsyncOperationTrace`?
- Can sink installation happen during existing server/runtime construction
  without a public setter?
- If a setter is required, can it be crate-private or test-hidden?
- Can finalizer instrumentation use existing `measure_optional` without adding
  new helper methods?
- Can async trace id creation reuse existing `trace_span_id` helpers instead of
  adding parallel helpers?
- Can request and async trace mapping share a small private mapper without a
  new class/type hierarchy?

Do not recommend classes, managers, registries, builders, event buses, broad
traits, or compatibility wrappers.

### 5. Storage Simplicity

Challenge the storage shape hard.

The spec currently prefers nullable async columns on `traces` rather than a
`trace_links` table. Verify whether that is the smallest viable shape.

Review both directions:

- If nullable columns are enough, reject `trace_links` as premature.
- If nullable columns duplicate too much state, propose the smaller correction
  with exact schema impact.

Check whether the proposed indexes are premature. If Phase 4 has no query API,
the reviewer should challenge any index not needed by tests or immediate
storage correctness.

Check whether `TraceRecord` should grow all async fields at once or only the
fields needed to persist command-finalization correlation now.

Do not propose runtime SQLite writes, runtime `sandbox-observability` imports,
manager aggregation, query APIs, metrics export, log export, or response
envelope changes.

### 6. Span Simplicity

Verify the span tree is not over-instrumented.

The minimal acceptable tree is:

```text
command_finalization
  completion_finalizer
    complete_terminal_command_with_services
      apply_workspace_completion_policy
      complete_command_record
```

Challenge whether even this can be smaller while still useful:

- Is `command_finalization` redundant with trace `operation` or `async_name`?
- Is both `command_finalization` and `completion_finalizer` redundant?
- Should `complete_terminal_command_with_services` be the root span?
- Are `apply_workspace_completion_policy` and `complete_command_record` the
  only useful child spans?

Reject `completion_watcher` spans unless the live code can attach them without
cross-thread trace sharing, a second trace, or extra lifecycle machinery.

Reject spans for `begin_terminal_completion`, `terminal_result`, transcript
reads, command output ingestion, namespace-runner internals, or tiny helpers.

### 7. LOC and Blast Radius

Use the spec's budget as a hard constraint:

```text
Expected `crates/sandbox-runtime` change: 60-110 non-test LOC, with 60-80 preferred.
```

Run or request:

```sh
git diff --numstat -- crates/sandbox-runtime/operation/src
```

If the architecture cannot plausibly fit in the preferred 60-80 runtime LOC
band, identify the exact pieces causing overrun and propose deletions.

Runtime production changes should stay local to:

- `observability.rs`;
- `lib.rs`;
- `command/service/core.rs`;
- `command/service/completion.rs`;
- `command/service/finalize.rs`;
- `command/service/impls/exec_command.rs`.

Flag any wider runtime blast radius unless it is proven necessary.

## Required Redundancy Audit

Create a table with this exact shape:

```text
Item | Kind | Keep / Remove / Narrow / Merge | Evidence | Minimal correction
```

Audit at least these items:

- `AsyncTraceLink`;
- `CompletedAsyncOperationTrace`;
- `AsyncTraceSink`;
- optional sink slot;
- `set_async_trace_sink`;
- optional promise link;
- optional completion link;
- `origin_request_id`;
- `async_name`;
- `correlation_kind`;
- `correlation_id`;
- `workspace_id`;
- `command_session_id`;
- finalizer `status`;
- finalizer `error_message`;
- `insert_completed_async_operation_trace`;
- V3 storage migration;
- async trace indexes;
- `command_finalization` root span;
- `completion_finalizer` span;
- `complete_terminal_command_with_services` span;
- `apply_workspace_completion_policy` span;
- `complete_command_record` span.

Add any other redundant file, field, method, type, trait, callback, or test
surface discovered during review.

## Output Format

Use this structure:

```text
Findings

1. [Severity] Title
   File:line
   Problem:
   Why it matters:
   Minimal correction:

2. ...

Redundancy Audit

Architecture Simplification Verdict

Runtime LOC Pressure

Storage Simplicity

Open Questions
```

Severity scale:

```text
P0 blocks implementation correctness
P1 likely causes wrong architecture or large rework
P2 meaningful simplification or LOC reduction
P3 wording or minor clarity issue
```

Rules:

- Findings first, ordered by severity.
- Cite exact paths and line numbers.
- Separate live-code facts from inferred simplification advice.
- Do not ask for broad rewrites when a small spec edit would fix the issue.
- Do not propose compatibility aliases or fallback layers.
- Do not propose adding a general observability runtime.
- Do not preserve an abstraction just because it might help future phases.
- If no serious issues are found, say so explicitly and still list smaller
  redundancy risks.

## Required Evidence Commands

Run these commands, or state exactly why they could not be run:

```sh
git status --short
git diff --stat
git diff --name-only
git ls-files --others --exclude-standard
git diff --numstat -- crates/sandbox-runtime/operation/src
git diff --check
rg -n "AsyncTraceLink|CompletedAsyncOperationTrace|AsyncTraceSink|set_async_trace_sink|insert_completed_async_operation_trace|trace_links|origin_request_id|correlation_kind|command_finalization|completion_finalizer" docs/observability crates/sandbox-runtime/operation/src crates/sandbox-daemon/src crates/sandbox-observability/src
rg -n "thread::spawn|spawn_completion_finalizer|CommandCompletionPromise|CommandCompletion|complete_terminal_command_with_services|apply_workspace_completion_policy|complete_command_record" crates/sandbox-runtime/operation/src/command/service
```

Focused tests are optional for this architecture review. If tests are run, keep
them narrow:

```sh
cargo test -p sandbox-runtime operation_trace
cargo test -p sandbox-runtime exec_command
cargo test -p sandbox-daemon observability
cargo test -p sandbox-observability schema
```

Do not require broad workspace tests for a spec-simplicity review.
