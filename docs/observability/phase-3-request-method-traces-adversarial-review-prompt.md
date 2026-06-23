# Adversarial Implementation Review Prompt: Phase 3 Request Method Traces

Use this prompt to run a read-only adversarial review of the Phase 3 request
method traces implementation against:

```text
/Users/yifanxu/machine_learning/LoVC/ephemeral-ai/ephemeral-os/docs/observability/phase-3-request-method-traces.md
```

## Role

You are an adversarial code reviewer. Your job is to find concrete defects in
completeness, correctness, and cleanness. Do not praise the patch. Do not
implement fixes. Do not rewrite docs unless explicitly asked after the review.

Lead with findings, ordered by severity, and cite exact file and line
references. Treat the Phase 3 spec as the requirement and live code/tests as the
source of truth for what actually landed.

Do not infer success from checked boxes, prior summaries, or broad grep hits.
Verify behavior through current code paths, storage schema, focused tests, and
boundary scans.

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
they affect Phase 3 request method traces. Do not revert anything.

If unrelated Phase 3.5 docs or prompt files are present, classify them
separately from the Phase 3 implementation. Do not count unrelated docs toward
Phase 3 code completeness or cleanness unless they claim Phase 3 behavior.

## Required Reading

Read the Phase 3 implementation spec first:

```text
docs/observability/phase-3-request-method-traces.md
```

Then read the parent and adjacent specs only as boundary context:

```text
docs/observability/sandbox-observability.md
docs/observability/phase-1-observability-foundation.md
docs/observability/phase-2-runtime-snapshots.md
```

Then inspect the implementation:

```text
crates/sandbox-protocol/src/request.rs
crates/sandbox-protocol/src/response.rs
crates/sandbox-runtime/operation/Cargo.toml
crates/sandbox-runtime/operation/src/lib.rs
crates/sandbox-runtime/operation/src/operation.rs
crates/sandbox-runtime/operation/src/observability.rs
crates/sandbox-runtime/operation/src/services.rs
crates/sandbox-runtime/operation/src/command/service/impls/mod.rs
crates/sandbox-runtime/operation/src/command/service/impls/exec_command.rs
crates/sandbox-runtime/operation/src/command/service/impls/write_command_stdin.rs
crates/sandbox-runtime/operation/src/command/service/impls/read_command_lines.rs
crates/sandbox-runtime/operation/src/layerstack/service/impls/mod.rs
crates/sandbox-runtime/operation/src/layerstack/service/impls/squash.rs
crates/sandbox-runtime/operation/tests/operation_trace.rs
crates/sandbox-runtime/operation/tests/observability_snapshot.rs
crates/sandbox-runtime/operation/tests/service_graph.rs
crates/sandbox-daemon/src/server/runtime.rs
crates/sandbox-daemon/src/server/dispatch.rs
crates/sandbox-daemon/src/observability/service.rs
crates/sandbox-daemon/tests/unit/observability.rs
crates/sandbox-observability/src/records.rs
crates/sandbox-observability/src/store.rs
crates/sandbox-observability/tests/schema.rs
```

Use `rg` to verify live call paths and dependency boundaries. Do not rely on
grep hits alone; trace owners, callers, and tests.

## Review Axes

### 1. Completeness

Check whether the implementation satisfies the exact Phase 3 deliverable:

- `OperationTrace`, `SpanGuard`, `CompletedOperationTrace`, and
  `CompletedOperationSpan` exist under `crates/sandbox-runtime/operation`.
- Runtime trace state stores only monotonic request start time, Unix request
  start time in milliseconds, active parent stack by `call_index`, completed
  spans, and next stable `call_index`.
- `SpanGuard::drop` closes spans on normal return, early return, and panic
  unwind.
- Runtime public dispatch accepts `Option<&OperationTrace>`.
- `OperationEntry` dispatch pointers and the selected dispatch functions accept
  `Option<&OperationTrace>`.
- `dispatch_operation` records the automatic root `dispatch_operation` span.
- Matched operations record exactly one `<operation>::dispatch` span.
- Unknown operations record only the root dispatch span.
- Parse errors record root plus `<operation>::dispatch`, with no `parse_input`
  span.
- Selected operations record exactly one public service-method span:
  - `CommandOperationService::exec_command`;
  - `CommandOperationService::write_command_stdin`;
  - `CommandOperationService::read_command_lines`;
  - `LayerStackService::squash`.
- Public service method signatures are unchanged.
- Daemon dispatch creates `Some(OperationTrace::new())` only when daemon
  observability is enabled and a non-empty `sandbox_id` exists.
- Daemon passes `None` when persistence is disabled.
- Daemon completes and persists the trace after `Response::into_json_value()`
  projection and before Phase 2 snapshot collection.
- Daemon maps runtime DTOs into `TraceRecord` and `SpanRecord` through
  `ObservabilityStore::insert_trace`.
- Daemon derives `trace_id = request:{request_id}` and span ids
  `{trace_id}:span:{call_index}`.
- Fault responses mark the deepest completed span with bounded error metadata.
- Successful responses persist status `ok`.
- Unknown operation, argument parse error, and service error traces are covered
  by tests.
- Missing `sandbox_id` disables trace persistence without changing the response.
- Observability store failure does not alter operation responses.
- Command output and transcript content do not appear in trace/span rows.

Report any missing behavior or missing test as a finding unless it is clearly
outside Phase 3.

### 2. Correctness

Try to prove the implementation is wrong:

- Does `OperationTrace::complete()` return spans in parent-before-child order so
  storage foreign keys can insert nested spans?
- Is parentage recorded from the active stack before pushing the child span?
- Can early returns and panic unwinds leave the active stack corrupted?
- Are span statuses limited to runtime-local static strings, with response
  errors mapped only in daemon code?
- Does `dispatch_operation` wrap the lookup and unknown-op path in the root
  span?
- Does `<operation>::dispatch` wrap parsing and service dispatch, so parse
  errors get the operation-dispatch span?
- Do selected service-method spans start only after parsing succeeds?
- Do the selected operation wrappers avoid helper spans such as
  `resolve_exec_workspace`, `start_command_process`, `initial_exec_yield`,
  `write_or_cancel`, `wait_for_command_yield`, or `read_transcript_window`?
- Does daemon trace creation avoid disabled/no-op trace objects?
- Does trace persistence stay inside the existing `spawn_blocking` work item?
- Does response projection remain exactly the current raw
  `sandbox_protocol::Response` behavior?
- Can trace insert failures or SQLite errors escape into user responses?
- Are error `kind` and `message` bounded before storage?
- Does the mapper avoid copying `error.details`, command output, transcript
  rows, stdout/stderr chunks, environment dumps, or shell text?
- Does Phase 2 snapshot collection still happen after request handling?
- Are panic traces intentionally not persisted, matching the Phase 3 spec?

For each suspected issue, cite the exact code path and explain the failing
scenario.

### 3. Cleanness

Attack unnecessary surface area and boundary drift:

- `sandbox-runtime` must not depend on `sandbox-observability`, `rusqlite`,
  daemon paths, `ObservabilityStore`, `TraceRecord`, or `SpanRecord`.
- Runtime DTOs must not store trace ids, sandbox ids, request ids, operation
  names, workspace hierarchy, command hierarchy, response JSON, terminal trace
  status, or terminal error metadata.
- Runtime must use request-local `RefCell<TraceState>`, not `Arc<Mutex<_>>`.
- There must be no disabled/no-op trace object.
- There must be no new crate, compatibility shim, alias method, or parallel
  dispatch path.
- There must be no production storage migration for Phase 3.
- There must be no `traces.workspace_id`, `traces.command_session_id`,
  `idx_traces_workspace_time`, `idx_traces_command_time`, `trace_links`,
  `origin_request_id`, `correlation_kind`, or `async_name`.
- There must be no manager aggregation, daemon query API, response envelope,
  external observability service, command transcript ingestion, or command output
  ingestion.
- Hidden/test-only store read helpers are acceptable only if they do not expose
  a product query API.
- Runtime non-test source additions must stay within 70-120 added LOC under
  `crates/sandbox-runtime/operation/src`, with 75-95 preferred. Count actual
  diff output, including any untracked production files.
- Total changed LOC must stay under 3800. Separate unrelated docs or prompt
  changes from implementation stats.

Prefer deletion, narrowing, or test-only visibility over new abstractions.

## Completion Criteria Audit

Create a table with one row per Phase 3 completion criterion:

```text
Criterion | Status | Evidence | Notes
```

Use one of these statuses:

```text
Complete
Incomplete
Blocked by evidence gap
Out of scope / unrelated change
```

Audit these groups:

- Storage criteria from `docs/observability/phase-3-request-method-traces.md`.
- Runtime boundary criteria.
- Daemon boundary criteria.
- Data boundary criteria.
- Verification criteria.

Do not mark a criterion complete without exact file/line or command-output
evidence.

## Required Evidence Commands

Run these commands, or state exactly why they could not be run:

```sh
git status --short
git diff --stat
git diff --name-only
git ls-files --others --exclude-standard
git diff --numstat -- crates/sandbox-runtime/operation/src
git diff --check

rg -n "sandbox-observability|rusqlite|ObservabilityStore|TraceRecord|SpanRecord" crates/sandbox-runtime/operation/src crates/sandbox-runtime/operation/Cargo.toml
rg -n "workspace_id|command_session_id|idx_traces_workspace_time|idx_traces_command_time|trace_links|origin_request_id|correlation_kind|async_name" crates/sandbox-observability/src crates/sandbox-observability/tests
git diff -- crates/sandbox-observability/src crates/sandbox-observability/tests | rg -n "traces\\.workspace_id|traces\\.command_session_id|idx_traces_workspace_time|idx_traces_command_time|trace_links|origin_request_id|correlation_kind|async_name"
rg -n "resolve_exec_workspace|start_command_process|initial_exec_yield|write_or_cancel|read_transcript_window|runner::run|shell_exec" crates/sandbox-runtime/operation/src/observability.rs crates/sandbox-runtime/operation/src/operation.rs crates/sandbox-runtime/operation/src/command/service/impls crates/sandbox-runtime/operation/src/layerstack/service/impls
git diff -- crates/sandbox-runtime/operation/src/observability.rs crates/sandbox-runtime/operation/src/operation.rs crates/sandbox-runtime/operation/src/command/service/impls crates/sandbox-runtime/operation/src/layerstack/service/impls | rg -n "resolve_exec_workspace|start_command_process|initial_exec_yield|write_or_cancel|read_transcript_window|runner::run|shell_exec"

cargo fmt --check
cargo check -p sandbox-observability --tests
cargo test -p sandbox-observability
cargo check -p sandbox-runtime --tests
cargo test -p sandbox-runtime operation_trace
cargo check -p sandbox-daemon --tests
cargo test -p sandbox-daemon observability
```

Broad scans can report pre-existing Phase 2 fields or existing helper method
names. When that happens, classify whether the hit is pre-existing, unrelated,
or newly introduced by Phase 3. Use diff-focused scans to separate real Phase 3
violations from existing code.

If a command would mutate tracked files, stop and report the risk instead of
running it.

## Output Format

Use this structure:

```text
Findings

1. [P0/P1/P2/P3] Title
   Axis: Completeness | Correctness | Cleanness
   File:line
   Problem:
   Evidence:
   Why it matters:
   Minimal correction:

2. ...

Completion Criteria Audit

Storage Criteria
Runtime Boundary Criteria
Daemon Boundary Criteria
Data Boundary Criteria
Verification Criteria

Completeness Verdict

Correctness Verdict

Cleanness Verdict

Boundary Confirmation

Verification Run

Out-of-Scope Worktree Changes

Open Questions
```

Severity scale:

```text
P0 blocks correctness or violates a hard Phase 3 boundary
P1 likely causes wrong behavior, data loss, or large rework
P2 meaningful missing coverage, simplification, or boundary tightening
P3 wording, naming, or minor maintainability issue
```

If there are no actionable findings, say:

```text
No actionable findings found.
```

Even when no findings are found, still include the completion criteria audit and
verification run summary. Do not claim Phase 3 completion if any hard Phase 3
boundary is violated.
