# Observability Phase 4.6 Cleanup and Round-Trip Reduction Prompt

Use this prompt to scan the implemented Phase 4.6 mechanical namespace execution
unification for cleanup opportunities, then remove only the dead, legacy,
redundant, or unnecessarily chatty implementation surfaces that live evidence
proves safe to remove.

This is an implementation prompt, not a review-only prompt. Start with an
evidence-backed audit, then make narrow edits for proven cleanup wins and verify
them.

## Role

You are a pragmatic Rust cleanup engineer working in:

```text
/Users/yifanxu/machine_learning/LoVC/ephemeral-ai/ephemeral-os
```

Your job is to reduce the Phase 4.6 implementation to its simplest correct
shape after the hard namespace execution cutover. Prefer deletion, visibility
narrowing, field removal, method removal, direct call paths, and fewer storage
round trips when they are proven safe.

Do not preserve compatibility scaffolding for deleted Phase 2 execution
snapshot surfaces. Do not add replacement abstractions unless they remove real
complexity in the current code.

## Non-Negotiable Phase 4.6 Contract

Preserve these results:

```text
RuntimeObservabilitySnapshot.active_namespace_executions
RuntimeNamespaceExecutionSnapshot
NamespaceExecutionSnapshotRecord
namespace_execution_snapshots
completed namespace execution traces
operation_name / operation = exec_command for command work
```

Do not reintroduce:

```text
RuntimeExecutionSnapshot
RuntimeObservabilitySnapshot.active_executions
ExecutionSnapshotRecord
execution_snapshots production APIs
active_commands
execution_kind
NamespaceExecutionKind
namespace_execution_kind
runner_kind
execution_scope
command-shaped active observability records
command sidecar tables keyed by namespace_execution_id
compatibility aliases
```

Do not add command payload data to namespace execution snapshots:

```text
command_session_id
command text
stdin
stdout
stderr
environment
command output
transcript path
transcript contents
command lifecycle/finalization state
workspace ownership
process group id
```

Historical Phase 2 migration SQL may still mention old `execution_snapshots`
columns. Do not rewrite checksum-protected historical migrations. Cleanup must
target active code, current tests, active docs, and final migrated schema.

## Start With Worktree Safety

Run:

```sh
git status --short
git diff --stat
git diff --name-only
git ls-files --others --exclude-standard
```

Treat unrelated dirty worktree changes as user-owned. Do not revert them. If a
file already has unrelated edits, read it before changing it and keep your patch
scoped.

## Read First

Read the Phase 4.6 spec and the implementation-correctness prompt:

```text
docs/observability/phase-4-6-mechanical-namespace-execution-unification.md
docs/observability/phase-4-6-mechanical-namespace-execution-unification-completeness-correctness-adversarial-review-prompt.md
```

Then inspect the implementation surfaces:

```text
crates/sandbox-runtime/operation/src/observability.rs
crates/sandbox-runtime/operation/src/services.rs
crates/sandbox-runtime/operation/src/namespace_execution.rs
crates/sandbox-runtime/operation/src/command/service/process_store.rs
crates/sandbox-runtime/operation/src/command/service/impls/exec_command.rs
crates/sandbox-runtime/operation/src/command/service/finalize.rs
crates/sandbox-runtime/operation/src/lib.rs
crates/sandbox-runtime/operation/tests/observability_snapshot.rs

crates/sandbox-daemon/src/observability/service.rs
crates/sandbox-daemon/src/observability/namespace_execution.rs
crates/sandbox-daemon/tests/unit/observability.rs

crates/sandbox-observability/src/records.rs
crates/sandbox-observability/src/store.rs
crates/sandbox-observability/src/lib.rs
crates/sandbox-observability/tests/schema.rs

docs/observability/phase-2-runtime-snapshots.md
docs/observability/phase-3-request-method-traces.md
docs/observability/phase-4-5-namespace-runner-traces.md
docs/observability/phase-5-manager-aggregation.md
docs/observability/sandbox-observability.md
```

## Cleanup Targets

Find and remove only proven cleanup opportunities in these categories.

### 1. Unused and Legacy Code

Search for old-lane leftovers and prove whether each hit is active:

```sh
rg -n '\bRuntimeExecutionSnapshot\b|\bExecutionSnapshotRecord\b|\bupsert_execution_snapshots\b|\bprune_execution_snapshots\b|\bexecution_snapshots_for_test\b' \
  crates/sandbox-runtime/operation/src \
  crates/sandbox-daemon/src \
  crates/sandbox-observability/src \
  crates/sandbox-runtime/operation/tests \
  crates/sandbox-daemon/tests \
  crates/sandbox-observability/tests

rg -n 'active_executions|active_commands|execution_snapshots|idx_execution_snapshots' \
  crates/sandbox-runtime/operation/src \
  crates/sandbox-daemon/src \
  crates/sandbox-observability/src \
  crates/sandbox-runtime/operation/tests \
  crates/sandbox-daemon/tests \
  crates/sandbox-observability/tests \
  docs/observability
```

Allowed hits:

- historical Phase 2 docs when they are clearly marked superseded;
- historical V2 migration SQL when a later V5 migration drops the final table;
- `NamespaceExecutionSnapshotRecord` as a substring false positive when using
  broad grep.

Cleanup candidates:

- stale public re-exports;
- dead validation constants;
- unused helper functions;
- unused test helpers;
- old doc examples that describe current API shape incorrectly;
- comments that preserve deleted concepts as if they were still active;
- tests that still prove the deleted lane instead of the surviving namespace
  lane.

### 2. Redundant Fields and Methods

Inspect struct fields, helpers, and methods added or left behind by Phase 4.6.
For each candidate, prove one of:

- the field is never read by production code or tests;
- the method is only a one-line wrapper with no useful test seam or invariant;
- the helper duplicates a standard library operation or a local helper already
  used nearby;
- the export is broader than all call sites require;
- the test-only surface can be replaced by an existing public or crate-private
  API without losing coverage.

Use:

```sh
rg -n 'pub |pub\\(crate\\)|fn |struct |enum |type ' \
  crates/sandbox-runtime/operation/src/observability.rs \
  crates/sandbox-runtime/operation/src/namespace_execution.rs \
  crates/sandbox-daemon/src/observability/service.rs \
  crates/sandbox-daemon/src/observability/namespace_execution.rs \
  crates/sandbox-observability/src/records.rs \
  crates/sandbox-observability/src/store.rs \
  crates/sandbox-observability/src/lib.rs

cargo clippy -p sandbox-runtime --all-targets --no-deps -- -D warnings
cargo clippy -p sandbox-daemon --all-targets --no-deps -- -D warnings
```

Do not remove fields from the required runtime/storage/public DTO shapes just
because current tests do not inspect every field. Required Phase 4.6 fields are
part of the contract.

### 3. Round-Trip Reduction

Look for unnecessary round trips introduced or preserved around Phase 4.6:

- repeated SQLite transactions where one transaction would preserve semantics;
- upsert followed by prune APIs that could share a transaction without changing
  validation or error handling;
- repeated reads of the same active namespace execution ids;
- repeated runtime/store locks that can be collapsed without increasing lock
  scope across slow IO;
- daemon snapshot collection paths that call into the store more times than
  needed for the same sampled snapshot;
- tests that reopen stores/connections repeatedly without need.

Keep this scoped. Do not propose crate merges, daemon/gateway ownership changes,
socket protocol redesign, or manager aggregation changes unless direct Phase
4.6 code evidence proves the round trip is in this slice.

Before changing round-trip behavior, answer:

```text
What is the exact old call sequence?
What is the exact new call sequence?
Does error handling remain equivalent?
Does partial snapshot behavior remain equivalent?
Does validation still run before writes?
Does stale-row cleanup still happen after successful active-row projection?
Does completed namespace trace acknowledgement still happen only after successful writes?
```

Prefer a small store helper that combines existing namespace snapshot upsert and
prune in one transaction if it demonstrably reduces calls without widening the
API. Do not add a generic repository abstraction.

### 4. Test and Doc Cleanup

Tests should prove the surviving namespace lane, not the deleted command-shaped
lane.

Remove or rewrite tests that:

- query dropped tables;
- assert old DTO fields;
- only prove `execution_snapshots` was empty;
- preserve command payload fields in expected rows;
- require compatibility helpers that production no longer has.

Docs should be aligned enough that Phase 5 will not rebuild old surfaces. Do not
rewrite historical phase plans wholesale. Add or tighten superseded notes where
needed.

## Suggested Scan Commands

Run these while auditing:

```sh
cargo machete --with-metadata

rg -n 'legacy|compat|fallback|alias|sidecar|active_commands|active_executions|execution_kind|runner_kind|execution_scope' \
  crates/sandbox-runtime/operation/src \
  crates/sandbox-daemon/src \
  crates/sandbox-observability/src \
  crates/sandbox-runtime/operation/tests \
  crates/sandbox-daemon/tests \
  crates/sandbox-observability/tests \
  docs/observability

rg -n 'namespace_execution_snapshots|upsert_namespace_execution_snapshots|prune_namespace_execution_snapshots|namespace_execution_snapshots_for_test' \
  crates/sandbox-daemon/src \
  crates/sandbox-observability/src \
  crates/sandbox-daemon/tests \
  crates/sandbox-observability/tests

rg -n 'TODO|FIXME|phase 4\\.6|Phase 4\\.6|superseded|dropped|drop migration' \
  crates/sandbox-runtime/operation/src \
  crates/sandbox-daemon/src \
  crates/sandbox-observability/src \
  crates/sandbox-runtime/operation/tests \
  crates/sandbox-daemon/tests \
  crates/sandbox-observability/tests \
  docs/observability
```

Treat broad `legacy` or `compat` hits as leads, not proof. Stop once compiler,
call-site, and test evidence no longer support more cleanup.

## Implementation Rules

- Keep edits small and reviewable.
- Prefer deletion to renaming.
- Prefer visibility narrowing to new wrappers.
- Prefer direct helper removal to broad file/module reorganization.
- Do not change command API behavior.
- Do not change namespace execution lifecycle semantics.
- Do not change public Phase 5 DTO shape except to remove stale old-lane
  references from docs.
- Do not rewrite historical migration SQL.
- Do not add compatibility shims.
- Do not delete tests only because they are inconvenient; update them to prove
  the active contract or remove them only when the contract is covered
  elsewhere.
- Do not widen cleanup into unrelated observability phases, manager APIs,
  gateway latency work, or workspace/session lifecycle changes.

## Verification

Run the smallest meaningful check after each cleanup batch, then run the full
Phase 4.6 verification before closing:

```sh
cargo fmt --check
cargo check -p sandbox-runtime --tests
cargo test -p sandbox-runtime observability
cargo check -p sandbox-observability --tests
cargo test -p sandbox-observability
cargo test -p sandbox-daemon observability
cargo clippy -p sandbox-runtime --all-targets --no-deps -- -D warnings
cargo clippy -p sandbox-daemon --all-targets --no-deps -- -D warnings
git diff --check
```

If dependency or public-surface cleanup is touched, also run:

```sh
cargo machete --with-metadata
cargo check --workspace --tests
```

If Linux-only namespace code is touched, also run:

```sh
cargo check --tests --target x86_64-unknown-linux-gnu
```

## Final Report

Lead with what changed and why it was safe:

```text
Cleanup Summary
- removed ...
- narrowed ...
- combined ...

Evidence
- dead by call-site scan ...
- dead by clippy/check ...
- round trip reduced from ... to ...

Verification
- command: pass/fail/not run

Residual Risk
- ...
```

If no cleanup is justified, say:

```text
No safe Phase 4.6 cleanup candidates found beyond the current implementation.
```

Then include the scans and verification that support that stop decision.
