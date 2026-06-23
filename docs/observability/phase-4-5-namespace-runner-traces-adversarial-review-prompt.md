# Adversarial Architecture Review Prompt: Phase 4.5 Namespace-Runner Traces

Use this prompt to run a read-only adversarial review of the Phase 4.5
namespace-runner trace architecture in:

```text
/Users/yifanxu/machine_learning/LoVC/ephemeral-ai/ephemeral-os/docs/observability/phase-4-5-namespace-runner-traces.md
```

## Role

You are an adversarial architecture reviewer. Your job is to treat the proposed
Phase 4.5 design as too complex, insufficiently generic, and possibly biased by
today's command path. Do not praise the design. Do not implement code. Do not
rewrite the spec unless explicitly asked after the review.

Lead with findings, ordered by severity, and cite exact file and line
references. After the findings, propose a simpler design that handles future
operations that need workspace namespace execution, including operations that
use `shell_exec` but are not command operations.

The review should answer these questions:

```text
Is the Phase 4.5 design still too command-shaped even after removing
command_session_id from child-produced trace data?

Can namespace-runner observability be modeled around WorkspaceSession namespace
capability and generic namespace execution, with less trace-specific plumbing?

What is the smallest design that supports command exec today and future
namespace/shell-exec operations tomorrow?
```

Treat docs as proposals and live code as the source of truth. Broad grep hits are
not enough; trace ownership, call paths, data flow, and crate dependencies.

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
they affect Phase 4.5 namespace-runner trace architecture. Do not revert
anything.

## Required Reading

Read the target Phase 4.5 spec first:

```text
docs/observability/phase-4-5-namespace-runner-traces.md
```

Then read parent and adjacent observability docs only as boundary context:

```text
docs/observability/sandbox-observability.md
docs/observability/phase-2-runtime-snapshots.md
docs/observability/phase-3-5-targeted-deep-request-spans.md
docs/observability/phase-4-async-method-traces.md
```

Then inspect live code for the real namespace/command/workspace boundaries:

```text
crates/sandbox-runtime/workspace/src/model.rs
crates/sandbox-runtime/workspace/src/namespace/setns_runner.rs
crates/sandbox-runtime/command/src/process.rs
crates/sandbox-runtime/command/src/pty.rs
crates/sandbox-runtime/namespace-process/src/runner/protocol.rs
crates/sandbox-runtime/namespace-process/src/runner/mod.rs
crates/sandbox-runtime/namespace-process/src/runner/setns.rs
crates/sandbox-runtime/namespace-process/src/runner/shell_exec.rs
crates/sandbox-runtime/namespace-process/src/runner/shell_exec/wait.rs
crates/sandbox-runtime/operation/src/command/service/impls/exec_command.rs
crates/sandbox-runtime/operation/src/command/service/completion.rs
crates/sandbox-runtime/operation/src/command/service/finalize.rs
crates/sandbox-daemon/src/runner.rs
crates/sandbox-daemon/src/observability/service.rs
crates/sandbox-observability/src/records.rs
crates/sandbox-observability/src/store.rs
```

Use `rg` to verify all live `NamespaceRunnerRequest` builders and all
`ns-runner` modes. Include workspace mount/remount and command exec in the call
path analysis.

## Adversarial Premise

Assume the current Phase 4.5 proposal is probably still too complex. In
particular, challenge:

- whether `NamespaceRunnerTraceContext` is needed at all;
- whether `NamespaceRunnerTraceReport` is the right abstraction;
- whether `invocation_id` should be a trace concept or a generic namespace
  execution id;
- whether embedding `runner_trace` in `RunResult` couples all runner users to
  observability;
- whether `trace_links` should be part of the Phase 4.5 design or deferred;
- whether the proposed parent-side linking still leaks command-thinking into a
  namespace-runner design;
- whether "runner invocation" is the right domain concept, or whether the root
  concept should be a workspace-owned namespace execution attempt.

Do not accept a type just because it has been stripped down. Ask whether the type
needs to exist.

## Review Axes

### 1. Domain Ownership

Start from the live hierarchy:

```text
WorkspaceSession
  namespace capability / WorkspaceEntry
  optional CommandSession
  namespace-runner child process invocations
```

Challenge any design that treats command as a first-class runner owner. Command
sessions are children of workspace sessions. Future operations may use
`shell_exec` without command sessions.

Answer:

- Does `WorkspaceSession` own the namespace capability cleanly in live code?
- Does `command` merely consume `WorkspaceEntry` to run shell work?
- Do workspace mount/remount already use `NamespaceRunnerRequest` without a
  command session?
- Should Phase 4.5 model a generic workspace namespace execution instead of a
  namespace-runner trace?
- What parent hierarchy should future non-command shell-exec operations use?

If the current spec adds extra ownership terminology, flag it.

### 2. Generic Future Operations

Assume new operations will use the namespace and may eventually call
`shell_exec::execute_shell` without being command operations.

Stress the design against these cases:

```text
workspace probe using shell_exec
workspace setup validation using shell_exec
package install/bootstrap operation using shell_exec
remount verification using shell_exec
future tool/plugin execution using shell_exec
```

For each case, ask whether the current Phase 4.5 design can link traces without:

- a command session;
- a command finalizer;
- command transcript artifacts;
- command process store state;
- command-specific response shape;
- new enum variants for every operation.

If it cannot, propose the smaller generic correction.

### 3. Type Surface Area

Attack every proposed type and field.

Specifically challenge:

- `NamespaceRunnerTraceContext`;
- `NamespaceRunnerTraceReport`;
- `NamespaceRunnerSpan`;
- `runner_trace: Option<NamespaceRunnerTraceReport>` on `RunResult`;
- any proposed `kind = "runner"` storage distinction;
- any new trace id format;
- any new link table use;
- any parent-side metadata object.

For each item, decide whether it should:

- stay as-is;
- be renamed to a non-observability domain concept;
- be merged into existing `OperationTrace` / `CompletedOperationTrace`;
- be stored only parent-side;
- be represented as ordinary fields on a generic namespace execution result;
- be deleted.

Prefer fewer types over perfectly named types. Reject abstractions with one live
producer and speculative future value unless they are cheaper than the
alternative.

### 4. Transport Simplicity

The current spec prefers extending `RunResult` with optional runner trace data.
Challenge that.

Review alternatives:

- embed bounded timing data in `RunResult`;
- return timing data beside `RunResult` in a new internal envelope;
- carry only parent-observed timing for Phase 4.5 and defer child internals;
- use an existing pipe with a hard byte cap;
- use a separate bounded control pipe only if it removes coupling;
- use no child-produced trace for first pass and record parent-side runner
  lifecycle events only.

Reject any design that:

- lets the child write SQLite;
- writes trace data into `transcript.log`;
- depends on OTLP/Loki/Tempo;
- records command output, stdin, environment, or shell text;
- requires per-operation runner-specific transport code.

The chosen transport must be generic across command exec, workspace
mount/remount, and future shell-exec operations.

### 5. Storage and Query Shape

Challenge storage additions before accepting them.

Ask:

- Is `kind = "runner"` needed, or can the existing trace kind model represent
  child-process work without adding another category?
- Is a new `trace_links` table needed in Phase 4.5, or can parent hierarchy be
  represented by existing snapshot/execution state until query APIs exist?
- If `trace_links` is needed, should it be generic over workspace namespace
  execution rather than command/request ownership?
- Are `workspace_id` / `command_session_id` trace columns still the right shape
  if future namespace operations are not commands?
- Should storage use `workspace_session_id` consistently in docs, or keep the
  existing `workspace_id` schema name but define its semantic mapping?

Do not add query APIs, manager aggregation, metrics export, log export, or
response envelopes as part of the better Phase 4.5 design.

### 6. Better Design Requirement

After findings, propose a better design. The proposal must be concrete enough
that another agent could update the spec from it.

Include:

- the domain concept name;
- the minimum child-visible data, if any;
- the minimum parent-side data;
- the transport shape;
- how command exec links into it today;
- how a future non-command shell-exec operation links into it;
- what storage changes are required now;
- what storage/query work should be deferred.

Prefer a design shaped like:

```text
WorkspaceSession
  NamespaceExecution
    optional ShellExec
    optional CommandSession linkage
```

over a design shaped like:

```text
CommandSession
  NamespaceRunnerTrace
```

If the best answer is to defer child-produced runner spans and only record
parent-side namespace execution timing in Phase 4.5, say so and justify the
tradeoff.

## Forbidden Recommendations

Do not recommend:

- `NamespaceRunnerOwner`;
- `NamespaceRunnerMode` as trace metadata;
- `command_session_id` in child-produced trace data;
- command-owned runner traces;
- command transcript ingestion;
- command output/stdin/stderr ingestion;
- environment dumps;
- direct child writes to `observability.sqlite`;
- a global event bus;
- public response shape changes;
- a broad plugin/tool abstraction;
- compatibility aliases or fallback APIs;
- speculative enum variants for future operations.

## Output Format

Use this structure:

```text
Findings

1. [Severity] Finding title
   Evidence: file:line
   Why it matters:
   Smaller design:

Open Questions

Better Design

Minimal Implementation Surface

Deferred Work
```

If there are no findings, say that clearly, but still provide the better-design
analysis and residual risks.
