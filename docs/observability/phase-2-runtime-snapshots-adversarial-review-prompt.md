# Adversarial Review Prompt: Phase 2 Runtime Snapshots

Use this prompt to review:

```text
/Users/yifanxu/machine_learning/LoVC/ephemeral-ai/ephemeral-os/docs/observability/phase-2-runtime-snapshots.md
```

You are an adversarial architecture reviewer. Your job is to find places where
the Phase 2 runtime snapshot design is too complex, too speculative, too broad,
or likely to add avoidable LOC to `crates/sandbox-runtime`.

Do not implement code. Do not rewrite the spec unless explicitly asked after
the review. Produce findings first, ordered by severity, with exact file and
line references.

## Required Reading

Read the target spec first:

```text
docs/observability/phase-2-runtime-snapshots.md
```

Then read the parent and Phase 1 specs:

```text
docs/observability/sandbox-observability.md
docs/observability/phase-1-observability-foundation.md
```

Then inspect live code, not just docs:

```text
crates/sandbox-runtime/operation/src/services.rs
crates/sandbox-runtime/operation/src/command/service/process_store.rs
crates/sandbox-runtime/operation/src/command/service/core.rs
crates/sandbox-runtime/operation/src/command/service/impls/exec_command.rs
crates/sandbox-runtime/command/src/process.rs
crates/sandbox-runtime/command/src/pty.rs
crates/sandbox-runtime/namespace-process/src/runner/protocol.rs
crates/sandbox-runtime/namespace-process/src/runner/mod.rs
crates/sandbox-runtime/namespace-process/src/runner/setns.rs
crates/sandbox-runtime/namespace-process/src/runner/shell_exec.rs
crates/sandbox-runtime/namespace-process/src/runner/shell_exec/wait.rs
crates/sandbox-runtime/operation/src/workspace_session/service/core.rs
crates/sandbox-runtime/operation/src/workspace_session/service/model.rs
crates/sandbox-runtime/workspace/src/model.rs
crates/sandbox-daemon/src/server/runtime.rs
crates/sandbox-daemon/src/server/dispatch.rs
crates/sandbox-observability/src/records.rs
crates/sandbox-observability/src/store.rs
```

Use `rg` for call paths and names. Treat docs as proposals and code as the
source of truth.

## Review Goals

1. Challenge architecture simplicity.
   - Is the Phase 2 design the smallest coherent design that can populate live
     state?
   - Is `execution_snapshots` a useful operation-neutral boundary, or is it
     over-general for the first implementation?
   - Does the spec accidentally create a registry, DTO hierarchy, or abstraction
     before there is a second producer?
   - Can the design keep the operation-neutral naming while still implementing
     only the current `CommandProcessStore` producer?

2. Challenge design cleanness.
   - Are ownership boundaries crisp between `sandbox-runtime`,
     `sandbox-daemon`, and `sandbox-observability`?
   - Does runtime only copy out state, or does any wording invite runtime to do
     observability work?
   - Are daemon collectors clearly responsible for SQLite writes, resource
     sampling, health, and stale-row cleanup?
   - Is the query/API boundary still Phase 5, or does Phase 2 leak product API
     work?

3. Minimize `sandbox-runtime` LOC.
   - Verify whether the proposed runtime files are necessary.
   - Look for ways to keep runtime non-test LOC under the stated 100-180 budget.
   - Prefer direct copy-out methods over traits, registries, background tasks,
     writer handles, or generic framework code.
   - Identify any spec text that would push runtime above the budget.

4. Find simplification opportunities.
   - Can fields be deferred because the live code cannot expose them cheaply?
   - Can `created_at_unix_ms`, `last_activity_unix_ms`, `holder_pid`, or
     `started_at_unix_ms` stay absent until the owner already tracks them?
   - Can completed/recent executions be bounded or deferred to avoid retention
     machinery in runtime?
   - Can store read helpers remain test-only until Phase 5?
   - Can disk/cgroup sampling be narrower while still satisfying Phase 2?

5. Double-verify ns-runner shell-exec logic.
   Trace the live path and state ownership:
   - `CommandOperationService::exec_command`
   - `CommandProcess::spawn`
   - `build_namespace_runner_request`
   - `spawn_current_exe_ns_runner`
   - `ns-runner`
   - `runner::run`
   - `run_setns`
   - `shell_exec::execute_shell`
   - `wait_for_command_execution_scope`
   - parent-side `CommandProcess`, `PtyProcess`, transcript reader, process
     group id, and `CommandProcessStore`

   Verify whether the spec is precise that an active execution is the
   parent/runtime-side tracked record corresponding to a namespace-runner
   invocation whose `shell_exec` path spawns and waits on a shell process inside
   the workspace namespace. It must not imply that the daemon snapshots the
   short-lived `shell_exec` function body or child process internals directly.

6. Challenge schema naming and table shape.
   - Is `execution_snapshots` the right table name for Phase 2, or does it
     over-promise non-command producers?
   - If `execution_snapshots` is kept, are command-specific fields nullable and
     clearly marked as current-producer fields?
   - Is the table still minimal enough for Phase 2?
   - Does it avoid forcing raw SQL/API commitments before Phase 5?

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

Verdict

Runtime LOC Pressure

ns-runner / shell_exec Verification

Simplification Opportunities

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

- Lead with concrete findings, not a summary.
- Cite exact file paths and line numbers.
- Separate live-code facts from inferred design advice.
- Do not ask for broad rewrites when a small spec edit would fix the issue.
- Do not propose adding compatibility aliases or fallback layers.
- Do not recommend adding runtime SQLite, runtime cgroup reads, runtime disk
  walking, or runtime observability writers.
- Do not recommend one snapshot API per runtime operation class.
- If you find no serious issues, say so explicitly and still list remaining
  simplification opportunities and residual risks.
