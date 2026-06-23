# Phase 4.5: Namespace Execution Store and Traces

## Purpose

Phase 4.5 introduces a generic runtime ledger for work executed through the
workspace namespace runner. The root concept is a namespace execution attempt,
not a command session and not a runner-specific trace report.

The first implementation records parent-observed namespace execution lifecycle
data:

- which workspace namespace capability was used;
- which namespace execution kind ran;
- which operation launched it;
- the stable namespace execution id;
- lifecycle status, timing, and bounded errors.

Child-produced runner method spans are deferred. The first pass should not
extend `RunResult`, should not add a child-produced `NamespaceRunnerTraceReport`,
and should not model commands as runner owners.

## Live Boundary

The live hierarchy is:

```text
WorkspaceSession
  WorkspaceEntry namespace capability
  optional CommandSession
  namespace-runner child process attempts
```

`WorkspaceSession` owns the namespace capability exposed as `WorkspaceEntry`.
Command execution consumes that capability to run shell work. Workspace
mount/remount also use the namespace-runner substrate, but they are not shell
exec work.

The unified model is:

```text
WorkspaceSession
  NamespaceExecutionAttempt
    kind = shell_exec | mount_overlay | remount_overlay
    operation_name
    operation_execution_id?
```

For command execution:

```text
CommandProcessStore
  command_session_id
  namespace_execution_id

NamespaceExecutionStore
  execution_id = namespace_execution_id
  execution_kind = shell_exec
  operation_name = exec_command
  operation_execution_id = command_session_id
```

The generic namespace execution record does not have a dedicated
`command_session_id` field. Command identity stays in the command domain and is
represented generically as `operation_name + operation_execution_id` when a
namespace execution needs to be correlated with its launching operation.

## Namespace Execution Store

Add a runtime-side `NamespaceExecutionStore`. It is a state ledger, not a SQLite
store and not an observability service. It should live with the runtime services
that own workspace sessions and operation execution state, and it should be
shared by command execution plus future namespace operations.

The target record shape is:

```rust
pub struct NamespaceExecutionRecord {
    pub execution_id: NamespaceExecutionId,
    pub workspace_session_id: WorkspaceSessionId,
    pub execution_kind: NamespaceExecutionKind,
    pub operation_name: String,
    pub operation_execution_id: Option<String>,
    pub request_id: Option<String>,
    pub lifecycle_state: NamespaceExecutionLifecycle,
    pub started_at_unix_ms: i64,
    pub finished_at_unix_ms: Option<i64>,
    pub duration_ms: Option<f64>,
    pub error_kind: Option<String>,
    pub error_message: Option<String>,
}

pub enum NamespaceExecutionKind {
    ShellExec,
    MountOverlay,
    RemountOverlay,
}

pub enum NamespaceExecutionLifecycle {
    Starting,
    Running,
    Complete,
    Failed,
    TimedOut,
    Cancelled,
}
```

`operation_execution_id` is generic. For `exec_command`, it is the
`command_session_id`. For a future workspace probe, it can be a probe id. For a
future package bootstrap, it can be a bootstrap attempt id. Do not add
operation-specific id fields to `NamespaceExecutionRecord`.

The store should expose narrow operations:

```text
begin_namespace_execution(record metadata) -> NamespaceExecutionId
mark_namespace_execution_running(execution_id)
complete_namespace_execution(execution_id, status, bounded_error)
snapshot_active_namespace_executions()
recent_completed_namespace_executions(limit/window)
```

The exact retention window can be small. The purpose is to support live
observability and completed trace projection, not to become durable command
history.

## Execution Kinds

Use namespace execution kinds for what the runner actually does:

```text
shell_exec
mount_overlay
remount_overlay
```

`shell_exec` is the kind used by command execution and by future operations that
run shell work inside an existing workspace namespace.

`mount_overlay` and `remount_overlay` are not shell-exec subtypes. They share the
same namespace-runner substrate, but they do not call
`shell_exec::execute_shell`.

Do not add a `NamespaceRunnerMode` trace field. If implementation needs an
internal enum for dispatch, keep it in the runner/adapter layer and map it into
the small generic `NamespaceExecutionKind` values above.

## Command Integration

Command execution keeps command-specific state in `CommandProcessStore`:

```text
command_session_id
workspace_session_id
namespace_execution_id
transcript_path
command lifecycle/finalization
```

The command store owns command lifecycle, transcript lookup, stdin/stdout
operations, and finalization. It does not own the generic namespace execution
model.

When `exec_command` starts a command:

1. Resolve or create the `WorkspaceSession`.
2. Allocate `command_session_id` in the command store.
3. Begin a namespace execution:

   ```text
   execution_kind = shell_exec
   operation_name = exec_command
   operation_execution_id = command_session_id
   workspace_session_id = resolved workspace id
   request_id = request id when one exists
   ```

4. Store `namespace_execution_id` on the active command record.
5. Spawn the runner using the existing command process path.
6. Complete the namespace execution when the runner/command process reaches a
   terminal state.

Command APIs continue to use `command_session_id` for `write_command_stdin`,
`read_command_lines`, cancellation, finalization, and transcript lookup. The
generic namespace execution store does not need a `command_session_id` column or
field.

## Future Operation Integration

Future operations that use namespace shell execution should use the same store
without becoming command operations:

```text
execution_kind = shell_exec
operation_name = workspace_probe | workspace_setup_validation | package_bootstrap | tool_exec
operation_execution_id = operation-specific attempt id, if one exists
workspace_session_id = resolved workspace id
request_id = request id when one exists
```

Future non-shell namespace work uses the same store with a different execution
kind:

```text
execution_kind = remount_overlay
operation_name = remount_workspace_session
operation_execution_id = remount attempt id, if one exists
workspace_session_id = target workspace id
request_id = request id when one exists
```

Some namespace executions are not launched by a public CLI/runtime request. In
that case `request_id` is `None`; the execution still has
`namespace_execution_id`, `workspace_session_id`, `execution_kind`,
`operation_name`, and optional `operation_execution_id`. Remount is allowed to
have no request id when it is triggered by internal lifecycle work.

Do not add enum variants for every future operation. New operations should set
`operation_name`; `NamespaceExecutionKind` should only distinguish the small set
of runner substrate behaviors that materially affect lifecycle and trace shape.

## Child-Visible Data

The first implementation should not send observability trace context to the
child. Parent-observed lifecycle timing is enough to make namespace execution
visible and generic.

If a later phase adds child-produced runner spans, the child-visible data should
be limited to:

```rust
pub struct NamespaceExecutionContext {
    pub namespace_execution_id: String,
}
```

Do not include these fields in child-visible data:

- `command_session_id`;
- `workspace_session_id`;
- `request_id`;
- operation owner enums;
- shell command text;
- environment dumps;
- SQLite paths, writer handles, daemon stores, or `sandbox-observability` types.

## Transport

Do not extend `RunResult` with observability data in Phase 4.5.

The existing runner result is functional protocol:

```rust
pub struct RunResult {
    pub exit_code: i32,
    pub payload: serde_json::Value,
}
```

It is consumed by command execution for terminal status and by workspace
remount for verification payloads. Keep it focused on functional result data.

Phase 4.5 transport is parent-side store updates:

```text
parent starts child process -> NamespaceExecutionStore::begin
parent observes terminal state -> NamespaceExecutionStore::complete
daemon collector/projector reads runtime store -> observability rows
```

If child-produced spans are later required, use a separate bounded control pipe
or an internal parent envelope beside `RunResult`. Do not write trace data into
`transcript.log`, do not write directly to `observability.sqlite`, and do not
let missing or malformed child trace data fail the user operation.

## Persistence

Persist namespace execution observability outside the child process through
daemon-owned mapping code.

The runtime store is the source for active and recently completed namespace
execution facts. Daemon observability may project those facts into existing
snapshot and trace rows.

Recommended completed trace row shape:

```text
trace_id = "namespace_execution:" + namespace_execution_id
kind = "namespace_execution"
operation = operation_name
request_id = request_id, if known
workspace_id = workspace_session_id
command_session_id = NULL
```

Do not store command identity in `command_session_id` for namespace execution
rows. If storage needs generic operation lookup, add or use a generic
`operation_execution_id` / correlation field. Do not add a command-specific
namespace execution column.

`trace_links` is deferred. A namespace execution has one execution id and one
launching operation id in the first implementation. Add a link table only when
query APIs prove that one namespace execution needs multiple independent links.

## Storage and Snapshot Shape

Active namespace executions should feed the operation-neutral execution snapshot
lane. The snapshot row should be generic:

```text
execution_id = namespace_execution_id
execution_kind = shell_exec | mount_overlay | remount_overlay
operation = operation_name
workspace_id = workspace_session_id
operation_execution_id = optional generic operation id, if storage supports it
```

Do not add `command_session_id` to namespace execution snapshots. Existing
command execution snapshots may continue to include command-specific fields
because they are command snapshots. The namespace execution snapshot is a
different, generic row.

If the current storage schema cannot store `operation_execution_id`, Phase 4.5
may keep that field runtime-only and defer the SQLite column until the first
query API needs it.

## Span Boundaries

Phase 4.5 first pass records a single parent-observed namespace execution trace
or snapshot lifecycle, not child method spans.

Deferred child span candidates are:

```text
namespace_execution
  runner::run
  run_setns
  shell_exec::execute_shell
  wait_for_command_execution_scope
```

For mount/remount:

```text
namespace_execution
  setns_overlay_mount
```

```text
namespace_execution
  remount_overlay
  staged_remount_overlay
```

Do not record one span per wait-loop iteration, environment variable, shell
output chunk, transcript line, or filesystem entry.

## Non-Goals

Phase 4.5 does not implement:

- command-owned runner traces;
- `NamespaceRunnerOwner`;
- `NamespaceRunnerMode` as trace metadata;
- `command_session_id` in `NamespaceExecutionRecord`;
- `command_session_id` in child-produced data;
- `runner_trace` on `RunResult`;
- `NamespaceRunnerTraceReport` in the first pass;
- direct SQLite writes from the runner child process;
- command output, transcript, stdin, environment, or shell text ingestion;
- response envelope changes;
- manager aggregation or public query APIs;
- `trace_links`.

## Verification

Focused checks after implementation should include:

```sh
cargo fmt --check
cargo check -p sandbox-runtime --tests
cargo check -p sandbox-runtime-command --tests
cargo check -p sandbox-runtime-namespace-process --tests
cargo check -p sandbox-daemon --tests
cargo test -p sandbox-daemon observability
```

If Linux-only runner code changes, also run:

```sh
cargo check --tests --target x86_64-unknown-linux-gnu
```

Required behavior coverage:

- namespace execution ids are generated parent-side;
- command active records keep `command_session_id` and `namespace_execution_id`;
- `NamespaceExecutionRecord` has no dedicated `command_session_id`;
- `NamespaceExecutionRecord.request_id` is optional;
- command exec records `execution_kind = shell_exec`;
- future shell-exec tests can record a namespace execution without command state;
- remount or other internal lifecycle work can record a namespace execution
  without `request_id`;
- mount/remount records use `mount_overlay` / `remount_overlay`, not `shell_exec`;
- `RunResult` stays functional and has no `runner_trace`;
- missing observability store/projector failures do not fail user operations;
- no command output, transcript text, shell text, environment dump, or per-loop
  wait events appear in namespace execution records or trace rows;
- the runner child never opens or writes `observability.sqlite`.
