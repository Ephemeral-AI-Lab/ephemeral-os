# Adversarial Implementation Review Prompt: Phase 2 Runtime Snapshots

Use this prompt to run a read-only adversarial review of the Phase 2 runtime
snapshots implementation.

## Role

You are an adversarial code reviewer. Your job is to find concrete defects in
completeness, correctness, and cleanness. Do not praise the patch. Do not
implement fixes. Do not rewrite docs unless explicitly asked after the review.

Lead with findings, ordered by severity, and cite exact file and line
references. Treat docs as requirements and live code/tests as the source of
truth for what actually landed.

## Repo

```text
/Users/yifanxu/machine_learning/LoVC/ephemeral-ai/ephemeral-os
```

Start by running:

```sh
git status --short
git diff --stat
git diff --name-only
```

If the worktree includes unrelated user changes, keep them out of scope unless
they affect Phase 2 runtime snapshots. Do not revert anything.

## Required Reading

Read these specs first:

```text
docs/observability/phase-2-runtime-snapshots.md
docs/observability/sandbox-observability.md
docs/observability/phase-1-observability-foundation.md
```

Then inspect the implementation:

```text
Cargo.toml
Cargo.lock
crates/sandbox-observability/Cargo.toml
crates/sandbox-observability/src/lib.rs
crates/sandbox-observability/src/records.rs
crates/sandbox-observability/src/store.rs
crates/sandbox-observability/tests/schema.rs
crates/sandbox-runtime/operation/Cargo.toml
crates/sandbox-runtime/operation/src/lib.rs
crates/sandbox-runtime/operation/src/services.rs
crates/sandbox-runtime/operation/src/observability.rs
crates/sandbox-runtime/operation/src/workspace_session/service.rs
crates/sandbox-runtime/operation/src/workspace_session/service/snapshot.rs
crates/sandbox-runtime/operation/src/command/service/process_store.rs
crates/sandbox-runtime/operation/tests/observability_snapshot.rs
crates/sandbox-daemon/Cargo.toml
crates/sandbox-daemon/src/lib.rs
crates/sandbox-daemon/src/server/runtime.rs
crates/sandbox-daemon/src/server/dispatch.rs
crates/sandbox-daemon/src/observability/
crates/sandbox-daemon/tests/unit.rs
crates/sandbox-daemon/tests/unit/observability.rs
```

Use `rg` to verify live call paths and dependency boundaries. Do not rely on
grep hits alone; trace owners and callers.

## Review Axes

### 1. Completeness

Check whether the implementation satisfies the Phase 2 deliverable:

- `sandbox-observability` adds Phase 2 row records for workspace snapshots,
  active execution snapshots, and resource samples.
- A second idempotent migration creates/upgrades only the simplified Phase 2
  schema:
  - `workspace_snapshots` has no `holder_pid`, `created_at_unix_ms`, or
    `last_activity_unix_ms`;
  - `execution_snapshots` is active-only and has no
    `namespace_runner_request_id` or `command_total_time_ms`;
  - `resource_samples` keeps the narrow cgroup field set only.
- Store helpers are transactional where multi-row state can change:
  `upsert_workspace_snapshots`, `reconcile_workspace_snapshots`,
  `upsert_execution_snapshots`, `prune_execution_snapshots`, and
  `insert_resource_samples`.
- Stale workspaces are marked `state = 'destroyed'`, while retained
  workspace-scoped `resource_samples` remain available.
- Stale active executions are pruned, not retained as completed history.
- Sandbox-global resource samples use `workspace_id IS NULL`; per-workspace
  samples use `workspace_id IS NOT NULL`.
- Missing cgroup paths write unavailable cgroup fields instead of failing.
- Disk sampler errors become partial sample fields and do not fail collection.
- Runtime snapshot tests do not import `sandbox-observability`.
- Daemon observability write failures do not alter operation responses.

Report any missing behavior or missing test as a finding unless it is clearly
outside Phase 2.

### 2. Correctness

Try to prove the implementation is wrong:

- Migration idempotence: can a fresh database and already-migrated database both
  open successfully? Are checksums stable?
- SQLite semantics: are upsert/reconcile/prune helpers scoped by `sandbox_id`
  and transactionally consistent?
- Record validation: are IDs, states, paths, command text, and error strings
  bounded before insertion?
- Runtime snapshots: does `WorkspaceSessionService` lock sessions once and copy
  only bounded fields from handles/entries? Does it return partial errors
  without panicking?
- Execution snapshots: does `CommandProcessStore` enumerate only active command
  records and avoid completed command history and transcript contents?
- Daemon collection: is `sandbox_id` required for live writes, and are
  `ObservabilityPaths` derived from `ServerConfig.socket_path`?
- Failure policy: can SQLite, disk, cgroup, or snapshot errors escape into
  `sandbox_protocol::Response`?
- Resource semantics: are workspace-scoped samples stopped after a workspace
  disappears from the runtime snapshot?
- Disk sampler: does it avoid following symlinks and continue after per-entry
  errors?
- Cgroup sampler: does it use only explicit daemon-owned paths and avoid
  guessing workspace cgroups from pids?

For each suspected issue, cite the exact code path and explain the failing
scenario.

### 3. Cleanness

Attack unnecessary surface area and boundary drift:

- `sandbox-runtime` must not depend on `sandbox-observability`, `rusqlite`,
  SQLite paths, disk walking, cgroup reads, tracing spans, writer queues,
  background tasks, or `sandbox_id`.
- Runtime non-test production additions must stay within the 100-180 LOC budget.
  Count the actual diff, including untracked new files.
- Runtime DTOs should be read-only copy-out shapes, not an observability
  framework.
- The daemon should own collectors, disk/cgroup sampling, path derivation,
  store writes, and health/error containment.
- `sandbox-observability` should expose records and store helpers, not a product
  raw SQL API. Hidden/test-only read helpers are acceptable only for tests.
- No manager aggregation, public daemon `get_observability_snapshot`, method
  tracing, trace links, Prometheus/Grafana/Loki/Tempo/OTLP work, compatibility
  aliases, or fallback layers should appear in Phase 2.
- The daemon manifest must not depend directly on `rusqlite`.
- Test hooks should not become product APIs.

Prefer deletion or narrowing visibility over adding abstractions.

## Required Evidence Commands

Run these commands, or state exactly why they could not be run:

```sh
rg -n "sandbox-observability|rusqlite|ObservabilityStore|observability.sqlite|/sys/fs/cgroup|read_dir|symlink_metadata|sandbox_id" crates/sandbox-runtime/operation/src crates/sandbox-runtime/operation/Cargo.toml
rg -n "holder_pid|created_at_unix_ms|last_activity_unix_ms|namespace_runner_request_id|command_total_time_ms|cpu_user_usec|cpu_system_usec|memory_peak_bytes|memory_oom" crates/sandbox-observability/src crates/sandbox-observability/tests
git diff --numstat -- crates/sandbox-runtime/operation/src
cargo fmt --check
cargo check -p sandbox-observability --tests
cargo test -p sandbox-observability
cargo check -p sandbox-runtime --tests
cargo test -p sandbox-runtime observability_snapshot
cargo check -p sandbox-daemon --tests
cargo test -p sandbox-daemon observability
git diff --check
```

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

Completeness Verdict

Correctness Verdict

Cleanness Verdict

Boundary Confirmation

Verification Run

Open Questions
```

Severity scale:

```text
P0 blocks correctness or violates a hard Phase 2 boundary
P1 likely causes wrong behavior, data loss, or large rework
P2 meaningful missing coverage, simplification, or boundary tightening
P3 wording, naming, or minor maintainability issue
```

If there are no actionable findings, say:

```text
No actionable findings found.
```

Then still include residual risks, boundary confirmation, and the verification
matrix.

## Review Discipline

- Stay read-only.
- Findings first; summary second.
- Cite exact file and line references.
- Separate live-code facts from inferred design advice.
- Do not propose compatibility shims or aliases.
- Do not recommend moving SQLite, cgroup reads, disk walking, or writer queues
  into `sandbox-runtime`.
- Do not recommend one snapshot API per runtime operation class.
- Do not treat historical docs as live implementation requirements unless the
  Phase 2 spec imports them directly.
