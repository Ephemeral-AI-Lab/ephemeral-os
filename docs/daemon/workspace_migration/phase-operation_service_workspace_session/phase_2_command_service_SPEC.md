# Phase 2 Command Service Migration Spec

Date: 2026-06-18
Status: Implementation-ready draft
Parent spec: `docs/daemon/workspace_migration/operation_service_workspace_session_SPEC.md`
Previous phase: `docs/daemon/workspace_migration/phase-operation_service_workspace_session/phase_1_workspace_service_workspace_manager_SPEC.md`

## Summary

Phase 2 migrates daemon command lifecycle ownership into
`operation_service::command::CommandOperationService`.

The target boundary is:

```text
daemon request entrypoint
  parse protocol requests
  resolve request-scoped caller/trace context if that layer still exists
  call operation_service
  return protocol responses
  no workspace command routing
  no command lifecycle ownership

operation_service
  OperationServices { workspace, command, remount, ... }
  WorkspaceManagerService owns open workspace sessions and workspace lifecycle
  CommandOperationService owns command lifecycle and command-to-workspace binding
  WorkspaceRemountService owns live-remount orchestration

command crate
  low-level PTY/process/transcript substrate
  no workspace policy
  no publish policy
  no daemon session ownership
```

Hard target:

- `WorkspaceRuntime` is deleted.
- `daemon/core/runtime` is dropped.
- `daemon/core` command adapters are dropped or reduced to temporary
  compatibility during migration. The target service API is not shaped around
  `daemon/core`.
- `operation::command::contract` is old command-ops contract evidence, not the
  target command-service contract.
- `workspace` stays resource-facing only: create, capture, remount, destroy,
  latest snapshot.
- The old command/runtime modules are behavior evidence only. They are not the
  target architecture.

Phase 2 should not move `crates/daemon/command/src` into
`operation_service/src/command`. The command crate already has the right
mechanism boundary: PTY process management, transcript IO, yield waiting, and
policy-free process exits. Phase 2 moves or wraps the higher-level lifecycle,
registry, capture, publish, cancellation, and remount policy currently under
`crates/daemon/operation/src/command` into
`crates/daemon/operation_service/src/command`.

Some policy-free mechanics currently planned under
`operation_service/src/command` may be extracted into the existing `command`
crate instead. The extraction rule is dependency-based: code may move to
`command` only when it does not know about `WorkspaceManagerService`,
`WorkspaceId` binding ownership, session lifecycle, LayerStack publish/OCC, or
operation-service response policy.

## Current-State Evidence

The current codebase shows the migration split clearly:

- `crates/daemon/command/src/lib.rs:1` documents the command crate as the PTY
  substrate. It owns spawning, cancellation, yield waiting, and final-response
  persistence, and explicitly says workspace placement and upperdir finalization
  are the command-ops tier's concern.
- `crates/daemon/command/src/process.rs:27` reinforces the same boundary:
  `CommandProcess` owns child process, transcript, and cancel state, while its
  `CommandProcessExit` is policy-free.
- `crates/daemon/command/src/contract.rs:39` exposes low-level DTOs such as
  `StartCommand`, `WriteStdin`, `ReadCommandProgress`, `CancelCommand`, and
  `CollectCompleted`.
- `crates/daemon/operation/src/command/service.rs:48` currently mixes command
  lifecycle with workspace target shape through `ExecTarget::Host` and
  `ExecTarget::IsolatedNetwork`.
- `crates/daemon/operation/src/command/service.rs:134` shows `CommandOps`
  owning config, commit/capture options, command registry, resource samples,
  and finalize trace buffering. This is the lifecycle/policy surface to migrate
  into `operation_service::command`.
- `crates/daemon/operation/src/command/service/exec.rs:31` starts commands by
  target type, spawns the `command` crate process, registers active runs, waits
  for the yield window, and finalizes if the command exits in-window.
- `crates/daemon/operation/src/command/finalize.rs:29` publishes successful
  host commands through the lane-aware command capture path. Lines
  `135-168` discard non-success command captures before publish/OCC effects.
- `crates/daemon/operation/src/command/finalize.rs:256` finalizes isolated
  commands by reporting captured upperdir changes and explicitly setting
  `published: false`.
- `crates/daemon/operation/src/command/registry.rs:121` stores active commands
  as `HashMap<caller_id, HashMap<command_id, ActiveCommand>>`. That is current
  behavior evidence, but Phase 2 should reduce the command registry to one
  binding map from `command_id` to `workspace_id`.
- `crates/daemon/core/src/op_adapter/command.rs:64` currently parses
  `sandbox.command.exec`, asks `WorkspaceRuntime` to select a route, calls
  `operation::command::CommandOps`, records command trace events, and strips
  completed command ids from foreground responses.
- `crates/daemon/core/src/op_adapter/command.rs:203` maps write-stdin,
  progress read, and cancel directly to `CommandOps`.
- `crates/daemon/core/src/runtime/workspace.rs:630` defines
  `WorkspaceRuntime`, which currently owns isolated workspace lifecycle and has
  a direct `Arc<CommandOps>`.
- `crates/daemon/core/src/runtime/workspace.rs:1121` currently marks
  remount-pending, asks command ops to quiesce commands, runs remount, and
  resumes process groups. This orchestration must move to
  `WorkspaceRemountService`, using `WorkspaceManagerService` for
  session/resource steps and `CommandOperationService` for command quiesce.
- `crates/daemon/workspace/src/service.rs:8` already exposes the intended
  resource-facing `WorkspaceService` surface: create, capture, remount,
  destroy, latest snapshot.
- `crates/daemon/operation_service/src/services.rs:6` currently has only
  `OperationServices { workspace }`. Phase 2 adds `command` and `remount`
  beside it.
- `crates/daemon/operation_service/src/workspace_manager/service.rs:21` already wraps
  workspace create/resolve/capture/remount/destroy around the session manager.
- `crates/daemon/operation_service/src/workspace_manager/session_manager.rs:16` already
  defines `WorkspaceSessionHandler`, and lines `125-175` keep the primary map
  keyed by `WorkspaceId` with derived caller/lease lookups.
- `crates/shared/protocol/src/catalog.rs:320` lists command exec/write/poll/
  cancel/collect/count as the current daemon-native command operations. Phase 2
  keeps exec/write/read/poll/cancel in the command service target and drops
  collect/count lifecycle advancement from that service API.
- The local_os `exec-command` reference and
  `helper/command-transcript.ts:87` show the local_os row-oriented output
  projection that Phase 2 must preserve where relevant:
  `{ offset, next_offset, total_lines, output_truncated, output: rows }`.

## Target Architecture

Phase 2 adds command and workspace-remount service domain folders under
`operation_service`:

```text
crates/daemon/operation_service/src/
  lib.rs
  services.rs
  error.rs

  workspace_manager/
    mod.rs
    service.rs
    session_manager.rs
    error.rs

  command/
    mod.rs
    service.rs       # CommandOperationService
    contract.rs      # operation-service command inputs/outputs
    registry.rs      # command_id -> workspace_id binding
    exec.rs          # command start/yield/finalize flow
    remount.rs       # quiesce/resume and remount inspection
    error.rs

  workspace_remount/
    mod.rs
    service.rs       # WorkspaceRemountService
    error.rs
```

Expected `OperationServices` shape:

```rust
pub struct OperationServices {
    pub workspace: Arc<WorkspaceManagerService>,
    pub command: Arc<CommandOperationService>,
    pub remount: Arc<WorkspaceRemountService>,
}
```

`OperationServices` wires the three operation-service domains together. It should
not inline the live-remount workflow itself beyond dispatching to
`WorkspaceRemountService`.

Expected workspace remount service shape:

```rust
pub struct WorkspaceRemountService {
    workspace: Arc<WorkspaceManagerService>,
    command: Arc<CommandOperationService>,
    options: WorkspaceRemountOptions,
}

pub struct WorkspaceRemountOptions {
    // policy knobs for production remount strategy can be added here as needed
}
```

`WorkspaceRemountService` owns cross-service orchestration that needs both
workspace session state and command process state. It marks workspace remount
state through `WorkspaceManagerService`, asks `CommandOperationService` for
command quiesce/resume, then applies the workspace resource remount through
`WorkspaceManagerService`.

Expected command service shape:

```rust
pub struct CommandOperationService {
    workspace: Arc<WorkspaceManagerService>,
    config: command::CommandConfig,
    registry: Arc<CommandRegistry>,
    process_store: Arc<CommandProcessStore>,
    finalization_options: CommandFinalizationOptions,
}

pub struct CommandFinalizationOptions {
    pub one_shot_capture: BoundedCaptureOptions,
    pub one_shot_publish: CommitOptions,
}
```

`CommandFinalizationOptions` groups the policy knobs used when a temporary
one-shot host command finalizes. `one_shot_capture` controls bounded upperdir
capture before publish or drop reporting. `one_shot_publish` controls the
LayerStack publish behavior for successful one-shot captures. These options do
not apply to persistent session commands, which mutate the live workspace but do
not implicitly publish, destroy, or update session snapshot metadata during
normal command finalization.

`CommandOperationService` is the owner of:

- command admission limits;
- command id allocation;
- active PTY registration;
- stdin/read/poll/cancel command lookup;
- command id to workspace id binding;
- temporary one-shot host workspace creation and destruction;
- command finalization;
- command publish/discard policy;
- command-side process quiesce and `/proc` inspection for remount;
- command trace facts returned to the request entrypoint or trace sink.

`WorkspaceManagerService` remains the owner of:

- open workspace sessions;
- `workspace_id` lookup and caller ownership validation;
- session lifecycle state, including active/closing/remount-pending;
- create/capture/remount/destroy wrapping over `workspace::WorkspaceService`;
- session snapshot, layer path, lease metadata updates.
- remount state transitions and session metadata refresh requested by
  cross-service operation orchestration.

`workspace::WorkspaceService` remains the owner only of resource primitives. It
does not own command lifecycle, publish policy, session lookup, or remount
policy. If Phase 2 needs richer capture data for command publish, extend the
resource capture result returned through `WorkspaceManagerService`; do not move
publish decisions into `workspace`.

## Move Or Keep `crates/daemon/command/src`

Decision: keep `crates/daemon/command/src` as the `command` crate. Do not
remove it.

Reasons:

- It already states the correct substrate boundary in its crate docs.
- Its process types are policy-free enough to be reused by
  `operation_service::command`.
- Existing command DTOs such as `StartCommand` and `CollectCompleted` are
  migration evidence only until they are replaced or reduced to policy-free
  launch/result DTOs. They must not be reused in the target command-service
  contract while carrying request correlation fields, collect APIs, or
  per-command remount flags.
- Moving it into `operation_service` would couple PTY/process mechanics to
  daemon operation policy and make future reuse/testing harder.
- The code that must move is not the substrate. It is the higher-level
  `CommandOps`, `ExecTarget`, registry, finalize, trace, and remount policy
  currently in `crates/daemon/operation/src/command`.

Phase 2 should migrate the policy modules from:

```text
crates/daemon/operation/src/command/
  contract.rs
  finalize.rs
  outcome.rs
  prepare.rs
  registry.rs
  service.rs
  service/exec.rs
  service/io.rs
  service/lifecycle.rs
  service/remount.rs
  trace.rs
```

to:

```text
crates/daemon/operation_service/src/command/
```

with names adjusted from `CommandOps` to `CommandOperationService` and with
workspace coordination converted from `ExecTarget`/`WorkspaceRuntime` to
`WorkspaceSessionHandler` and `WorkspaceManagerService`.

## What Can Move Into `command`

Yes, Phase 2 may shift some files or submodules that were initially listed
under `operation_service/src/command` into `crates/daemon/command/src`, but only
for policy-free command mechanics.

Good candidates for the `command` crate:

- runner request and artifact preparation, if expressed as a generic
  `CommandLaunchSpec` that takes plain paths, namespace fd values, layer paths,
  command text, cwd, timeout, and command artifact directory;
- PTY process store primitives keyed by `command_id`, if they only own
  `CommandProcess`, transcript handles, cancellation state, and generic
  lifecycle state;
- transcript row storage and offset-window reads for the local_os-compatible
  `{ offset, stream, text }` projection;
- process-group quiesce/resume and Linux `/proc` inspection, if the API takes
  process group ids plus a workspace-root path and returns a policy-free
  inspection report;
- generic command errors for process, IO, artifact, transcript, and inspection
  failures.

These must stay in `operation_service::command`:

- `CommandOperationService`;
- operation-service `ExecCommandInput`, because it contains caller and optional
  workspace session routing;
- `CommandRegistry`, because it binds `command_id` to `workspace_id`;
- exec flow selection for `Some(WorkspaceSessionHandler)` versus `None`;
- host one-shot workspace create/capture/publish/destroy policy;
- persistent session no-publish policy;
- LayerStack publish/OCC/lane response shaping;
- high-level command response contracts that expose workspace/session metadata;
- command-service error mapping;
- the command-service method that scans registry bindings for a workspace and
  calls lower-level quiesce helpers.

Suggested split:

```text
crates/daemon/command/src/
  lib.rs
  config.rs
  contract.rs
  launch.rs          # generic runner request/artifact preparation
  process.rs
  process_store.rs   # optional PTY/process store, no workspace policy
  pty.rs
  quiesce.rs         # process-group freeze/resume and /proc inspection
  transcript.rs      # transcript storage and local_os-compatible row windows
  yield_wait_loop.rs

crates/daemon/operation_service/src/command/
  mod.rs
  service.rs
  contract.rs
  registry.rs        # exactly command_id -> workspace_id
  exec.rs
  remount.rs         # service wrapper around command quiesce APIs
  error.rs
```

The `command` crate may take a dependency on low-level mechanism crates such as
`linux-namespace-subprocess` if needed for runner request types. It must not
depend on `operation_service`, `operation`, `workspace`, or `layerstack`.

## Command Service API

### Core Inputs

`exec_command` must accept `workspace_id: Option<WorkspaceId>`.

```rust
pub struct ExecCommandInput {
    pub caller_id: CallerId,
    pub workspace_root: PathBuf,
    pub workspace_id: Option<WorkspaceId>,
    pub cmd: String,
    pub cwd: Option<PathBuf>,
    pub timeout_seconds: Option<f64>,
    pub yield_time_ms: Option<u64>,
}
```

`workspace_root` is the only root input in the Phase 2 command contract. The
target command contract must not carry a second LayerStack-root path.

Request correlation identifiers are not part of the target command-service
input. Request correlation belongs in `CommandCallContext`/trace context, and
command identity belongs to the allocated `command_id`. If a lower-level runner
request still needs a command correlation value during migration, derive it
inside launch preparation from `command_id` or request context rather than
adding a request-correlation field back to `ExecCommandInput`.

`CommandCallContext` is the only place command-service methods receive caller
ownership and request trace context:

```rust
pub struct CommandCallContext {
    pub caller_id: CallerId,
    pub trace: OperationTraceContext,
}
```

`CommandCallContext.caller_id` is authoritative for stdin/read/poll/cancel
authorization. Trace/correlation facts may be used for emitted trace records, but
they must not be copied into `ExecCommandInput` and must not leak into
policy-free command launch DTOs except as derived command-local artifact names
based on `command_id`.

Current `WorkspaceManagerService::create` still receives two path fields from
the Phase 1 workspace model and stores one of them in session metadata. Phase 2
command dispatch should not propagate that split. It should pass
`workspace_root` into any older workspace-create fields that still exist as a
temporary adapter, and command code should use the resolved
`WorkspaceSessionHandler` rather than preserving an extra command-level root
input.

For `workspace_id: Some(_)`, the request `workspace_root` must match the
resolved `WorkspaceSessionHandler.handle.workspace_root`. A mismatch is an
invalid command request because the resolved session handler is the source of
truth for launch placement. For `workspace_id: None`, `workspace_root` is used
to create the private one-shot host workspace and should be passed into any
temporary legacy `workspace_root`/`layer_stack_root` adapter fields until those
fields are collapsed.

Dispatch contract:

```rust
impl OperationServices {
    pub fn exec_command(
        &self,
        input: ExecCommandInput,
        trace: OperationTraceContext,
    ) -> Result<CommandExecOutcome, CommandServiceError> {
        let caller_id = input.caller_id.clone();
        let workspace = match input.workspace_id.clone() {
            Some(workspace_id) => Some(
                self.workspace.resolve(workspace_id, caller_id.clone())?
            ),
            None => None,
        };

        if let Some(handler) = &workspace {
            if handler.handle.workspace_root != input.workspace_root {
                return Err(CommandServiceError::WorkspaceRootMismatch {
                    expected: handler.handle.workspace_root.clone(),
                    actual: input.workspace_root.clone(),
                });
            }
        }

        let context = CommandCallContext { caller_id, trace };
        self.command.exec_command(input, workspace, context)
    }
}
```

Command service method shape:

```rust
impl CommandOperationService {
    pub fn exec_command(
        &self,
        input: ExecCommandInput,
        workspace: Option<WorkspaceSessionHandler>,
        context: CommandCallContext,
    ) -> Result<CommandYield, CommandServiceError>;

    pub fn write_stdin(
        &self,
        input: WriteStdinInput,
        context: CommandCallContext,
    ) -> Result<CommandYield, CommandServiceError>;

    pub fn read_lines(
        &self,
        input: ReadCommandLinesInput,
        context: CommandCallContext,
    ) -> Result<CommandLinesOutput, CommandServiceError>;

    pub fn poll(
        &self,
        input: PollCommandInput,
        context: CommandCallContext,
    ) -> Result<CommandPollOutput, CommandServiceError>;

    pub fn cancel(
        &self,
        input: CancelCommandInput,
        context: CommandCallContext,
    ) -> Result<CommandYield, CommandServiceError>;
}
```

This is an operation-service API, not a mirror of
`daemon/core/src/op_adapter/command.rs` or `operation::command::contract`.
Protocol names such as `sandbox.command.poll` can map to `poll` at the request
entrypoint, but the command service itself should use domain names and
operation-service-owned input/output types.

`read_lines` is separate from `poll` because local_os uses offset/limit row
windows rather than a last-N-lines progress tail.

### Operations To Support

Phase 2 command service must support:

- `exec_command`
- `write_stdin`
- `read_lines`
- `poll`
- `cancel`
- command-side process quiesce/resume for remount

Phase 2 drops these command-service operations:

- `collect_completed`
- `count_commands`
- `advance_active_commands_once`

Long-running commands stay reachable by `command_id`. Completion/finalization
is not exposed through `collect_completed` or `advance_active_commands_once`.
Finalization after a yielded `running` response is handled by an internal
command-service finalizer described below.

Only `exec_command` accepts `Option<WorkspaceSessionHandler>`. The other
command service methods accept `command_id` plus call context. They recover the
workspace association through `CommandRegistry` when needed; callers must not
pass a workspace handler to stdin/read/poll/cancel.

## Exec Mode Selection

`CommandOperationService::exec_command` distinguishes command mode only from
the optional handler argument:

```rust
workspace: Some(handler) => session command
workspace: None => one-shot command
```

`Some(handler)` means the request entrypoint already resolved a
`workspace_id` through `WorkspaceManagerService::resolve`. The command service
must run in that existing workspace and must not create, publish, or destroy a
workspace implicitly.

`None` means there is no reusable session workspace. The command service
creates a temporary one-shot host workspace through `WorkspaceManagerService`,
runs the command there, finalizes publish/discard policy, and destroys that
temporary workspace.

If `Some(handler)` has `handler.handle.profile == WorkspaceProfile::Isolated`, the
command is an isolated session command. The service should not infer isolation
from caller id, cwd, or root paths.

## Exec Flow With `Some(WorkspaceSessionHandler)`

This path is used when the wire request carries `workspace_id`.

```text
request entrypoint
  parse exec command request
  build ExecCommandInput { workspace_id: Some(id), ... }
  call OperationServices::exec_command

operation_service dispatch
  WorkspaceManagerService::resolve(workspace_id, caller_id)
    validate session exists
    validate caller owns session
    validate session lifecycle is active
    validate remount state permits command start
  call CommandOperationService::exec_command(input, Some(handler), trace)

CommandOperationService
  allocate command_id
  register command with:
    caller_id
    workspace_id
    session-owned workspace binding
    transcript paths
    process state
  prepare command in the resolved session workspace root
  spawn command crate CommandProcess
  wait yield_time_ms
  if running:
    return running response with command_id
  if completed:
    finalize as session command
```

Session command rules:

- Run directly in the existing workspace.
- Do not create a workspace.
- Do not destroy the workspace.
- Do not publish isolated/session workspace changes implicitly.
- Do not expose any new workspace id.
- Update session activity through `WorkspaceManagerService`.
- Keep `command_id` as the handle for stdin/read/cancel/poll.
- Bind the active command to `workspace_id` for later lifecycle and remount
  coordination.

Capture decision:

- Do not call `WorkspaceManagerService::capture_changes` after every command in
  a persistent session.
- Persistent session commands mutate the live workspace upperdir. Capturing
  after every command would blur the boundary between "live session state" and
  explicit publish/checkpoint flows.
- Command finalization may run a bounded, read-only upperdir scan to produce
  response metadata such as `changed_paths`, matching current isolated command
  behavior, but it must not update session snapshot/layer paths or publish.
- `WorkspaceManagerService::capture_changes` is reserved for explicit
  publish/checkpoint flows, explicit snapshot/checkpoint operations, and
  remount flows that need to update session snapshot metadata.

Foreground completion response:

- Return command status, exit code, output/progress projection, and command
  metadata.
- Preserve `publish_lanes` as empty/dropped metadata where the current command
  response contract expects it, but mark session command publication as not
  performed.
- Preserve `isolated.published: false` equivalent metadata for
  isolated sessions until a broader response schema replaces it.

## Exec Flow With `None`

This path is used when the wire request carries no `workspace_id`.

```text
request entrypoint
  parse exec command request
  build ExecCommandInput { workspace_id: None, ... }
  call OperationServices::exec_command

operation_service dispatch
  call CommandOperationService::exec_command(input, None, trace)

CommandOperationService
  WorkspaceManagerService::create(WorkspaceProfile::HostCompatible)
    creates temporary one-shot host-compatible workspace
    records temporary session state while command is active
  allocate command_id
  register command with:
    caller_id
    temporary one-shot workspace_id
    transcript paths
    process state
  prepare command in temporary one-shot workspace
  spawn command crate CommandProcess
  wait yield_time_ms
  if running:
    return running response with command_id
    keep temporary workspace tracked but private
  if completed:
    finalize one-shot host command
```

Host one-shot finalization:

```text
if command status is success:
  captured = WorkspaceManagerService::capture_changes(one_shot_handler, capture_request)
  publish captured upperdir changes through current command LayerStack/OCC/lane behavior
  build response from publish result

if command was cancelled, timed out, or non-success:
  discard according to current command finalization rules
  do not publish captured changes
  still return bounded publish_lanes metadata

always:
  WorkspaceManagerService::destroy(one_shot_handler)
  do not return temporary workspace_id as reusable session id
```

The temporary one-shot host `workspace_id` exists only so active command
lifecycle, cleanup, remount guards, and destroy failure reporting have one
consistent workspace session shape. It is not a public session id.
Running responses expose `command_id`, not the temporary
`workspace_id`.

If a foreground exec yields `running`, the temporary one-shot workspace stays
alive until the command finalizer can prove the command-owned process tree is
drained and finalizes the run. A PTY runner/direct child exit by itself is not
enough to capture, publish, or destroy the one-shot workspace. For example, a
command that starts detached long-lived work with `nohup ... &` can make the shell
or PTY runner return immediately while descendants continue mutating the
workspace. Those descendants keep the one-shot command active until they exit,
are cancelled, or time out. Destroy failure must retain enough service state to
retry or report the leaked resource; it must not silently remove the temporary
session.

## Finalization After Yield

`exec_command` returns after `yield_time_ms`. If the process is still running,
the command remains active under `command_id`; the caller does not need a
workspace handler to interact with it later.

Phase 2 must not expose `advance_active_commands_once` as a service method.
Instead, `CommandOperationService` owns an internal finalization supervisor:

```text
on exec_command start:
  register command_id in CommandRegistry
  register ActiveCommandProcess in CommandProcessStore
  attach CommandFinalizePolicy
  spawn/register finalizer watch for process exit and process-tree drain

when process exits and command-owned process tree is drained:
  take CommandProcessExit exactly once
  mark ActiveCommandProcess.finalization = InProgress
  run finalize_command(command_id, exit, finalize_policy)
  write CompletedCommandRecord with caller_id, workspace_id, result, transcript metadata
  remove command_id -> workspace_id registry binding only after finalization is safe
  retain completed transcript/result for read_lines/poll retention window
```

Finalization policy:

```text
Session { workspace_id }:
  no implicit publish
  no workspace destroy
  optionally collect bounded changed-path metadata
  mark command completed
  write completed record before removing active process state
  remove command registry binding

OneShotPublishThenDestroy { workspace_id }:
  wait until no command-owned subprocess can still mutate the temporary workspace
  do not treat PTY runner/direct child exit as sufficient by itself
  if command succeeded:
    capture workspace changes
    publish through command LayerStack/OCC/lane policy
  else:
    discard
  after publish/discard result is recorded:
    destroy temporary one-shot workspace
  write completed record with destroy result or destroy failure
  mark command completed only after finalize/destroy state is recorded
  remove command registry binding after finalization no longer needs remount/lifecycle coordination
```

If finalization fails, the service must not silently drop state. It should mark
`FinalizationState::Failed`, keep enough command/workspace state to retry or
report the failure, and let later `poll`, `read_lines`, `write_stdin`, or
`cancel` observe the terminal/finalization error.

One-shot subprocess drain rule:

- The finalizer may observe the direct PTY/runner process exit first, but it must
  keep the command active while tracked descendants remain in the command process
  group, workspace cgroup, or other command-owned process set.
- If the process set cannot be inspected, success finalization must be blocked or
  failed with retained cleanup state; it must not optimistically capture/publish
  and destroy.
- Cancellation and timeout may terminate the tracked process group/cgroup, but
  capture/publish/destroy still happen only after the termination path has
  observed that the command-owned process set is gone or has recorded a retained
  cleanup failure.
- Persistent session commands do not destroy the workspace, but their terminal
  status should still distinguish direct runner exit from remaining tracked
  command subprocesses so stdin/read/poll/cancel behavior is coherent.

Completed-command retention is required for ownership validation after active
registry removal. A completed command must be removed from `CommandRegistry`, but
its retained terminal record must still carry the ownership fields needed by
command-id operations:

```rust
pub struct CommandCompletionStore {
    completed: HashMap<CommandId, CompletedCommandRecord>,
}

pub struct CompletedCommandRecord {
    pub command_id: CommandId,
    pub caller_id: CallerId,
    pub workspace_id: WorkspaceId,
    pub result: CommandTerminalResult,
    pub transcript: RetainedCommandTranscript,
    pub finalization: FinalizationState,
    pub completed_at: Instant,
}
```

`CommandCompletionStore` is not `CommandRegistry` and must not add caller or
workspace secondary indexes in Phase 2. It is keyed only by `command_id`; caller
or workspace filtering, if needed later, scans retained records. `read_lines`,
`poll`, `write_stdin`, and `cancel` first look up active command state. If the
command is no longer active, they may look up `CommandCompletionStore`, validate
`CompletedCommandRecord.caller_id == CommandCallContext.caller_id`, then return
the retained terminal response or terminal error. A caller mismatch must return
the same authorization error as an active-command caller mismatch, not a leaked
terminal result.

## PTY And Session Registry Design

`CommandRegistry` is intentionally narrow. It is not the process table and it
does not own caller/workspace secondary indexes. It keeps exactly one hash map:
the binding from `command_id` to `workspace_id`.

```rust
pub struct CommandRegistry {
    command_workspace: HashMap<CommandId, WorkspaceId>,
}
```

`CommandOperationService` may own a separate process store keyed by
`command_id` for PTY handles, transcript readers, cancellation state, and
finalization state. That store must not be called `CommandRegistry`, and Phase 2
should not add caller/workspace indexes to it unless a later implementation
proves a real need.

```rust
pub struct CommandProcessStore {
    active: HashMap<CommandId, ActiveCommandProcess>,
    completed: CommandCompletionStore,
}

pub struct ActiveCommandProcess {
    pub command_id: CommandId,
    pub caller_id: CallerId,
    pub process: command::CommandProcess,
    pub transcript: CommandTranscriptStore,
    pub finalize_policy: CommandFinalizePolicy,
    pub lifecycle_state: CommandLifecycleState,
    pub cancellation: CancellationState,
    pub finalization: FinalizationState,
    pub trace_origin: CommandTraceOrigin,
    pub started_at: Instant,
}

pub enum CommandFinalizePolicy {
    Session {
        workspace_id: WorkspaceId,
    },
    OneShotPublishThenDestroy {
        workspace_id: WorkspaceId,
    },
}
```

`command_id` maps to:

- active PTY process;
- caller id;
- transcript rows;
- cancellation state;
- finalization state;
- trace origin.

`CommandRegistry` separately maps the same `command_id` to exactly one
`workspace_id`. A command always has a workspace id, including one-shot host
commands. For `workspace_id: None` exec requests, the command service creates a
temporary one-shot host workspace through `WorkspaceManagerService` and records
that temporary workspace id in the registry. The id is still private and never
returned as a reusable public session id.

There is no `PublicSession` vs `InternalHostOneShot` registry variant. That
distinction is a finalization responsibility, not a registry identity. The
service knows whether it must publish/discard and then destroy the workspace from
the exec flow that created the command, not from a public command registry enum.

Lookup rules:

- `command_id` is the only handle for stdin/read/cancel/poll.
- `workspace_id` is the coordination key for workspace lifecycle and remount.
- To find commands for a workspace, scan `CommandRegistry.command_workspace` for
  matching values. Do not add a `by_workspace` index in Phase 2.
- To find commands for a caller, scan the process store records if absolutely
  needed. Do not add a caller-keyed registry index in Phase 2.
- Removing an active command removes one process-store entry and one
  `command_workspace` binding.
- Completed commands are no longer command-registry entries. Retained transcript
  data and terminal responses belong to `CommandCompletionStore`, not
  `CommandRegistry`.
- Completed command reads and polls must authorize against the retained
  `caller_id` before returning terminal output, terminal metadata, or
  finalization failure details.

Lifecycle states:

```rust
pub enum CommandLifecycleState {
    Starting,
    Running,
    QuiescedForRemount,
    Finalizing,
    Completed,
    Cancelled,
    TimedOut,
    FinalizationFailed,
    DestroyPending,
}

pub enum CancellationState {
    None,
    Requested { requested_at: Instant },
    Sent { sent_at: Instant },
    Finalized,
}

pub enum FinalizationState {
    NotStarted,
    InProgress,
    ResponseBuffered {
        finalized: CommandFinalizedMetadata,
    },
    WorkspaceDestroyPending {
        finalized: CommandFinalizedMetadata,
    },
    Complete,
    Failed {
        error: String,
        finalized: Option<CommandFinalizedMetadata>,
    },
}
```

The service does not need to expose these exact enum names on the wire, but the
implementation must preserve these state distinctions internally so command
teardown, remount, and retry-safe destroy are not collapsed into a boolean.
Intermediate and failed states retain already-decided publish/discard metadata
so destroy failure reporting cannot lose the outcome recorded before workspace
teardown.

## Local OS Compatibility Projection

The local_os command surface exposes row-oriented output:

```text
output: [{ offset, stream, text }]
offset
next_offset
total_lines
status
exit_code
output_truncated
command_id when running
```

Phase 2 should add daemon command projections that can satisfy equivalent
commands:

```rust
pub struct CommandTranscriptRow {
    pub offset: u64,
    pub stream: CommandStream,
    pub text: String,
}

pub struct CommandLinesOutput {
    pub command_id: CommandId,
    pub status: CommandStatus,
    pub exit_code: Option<i64>,
    pub offset: u64,
    pub next_offset: u64,
    pub total_lines: u64,
    pub truncated_before: u64,
    pub output_truncated: bool,
    pub output: Vec<CommandTranscriptRow>,
}
```

Projection rules:

- `exec_command` may return either the legacy daemon `CommandResponse` shape or
  the row-oriented projection depending on caller/protocol surface, but both
  must be derived from the same transcript store.
- `read_lines` always returns a row window by `offset` and `limit`.
- `write_stdin` returns a row-oriented snapshot when called through the
  local_os-compatible surface.
- `poll` / `sandbox.command.poll` may continue to return the current
  daemon-native status/output object during migration.
- Row offsets are transcript-row offsets, not byte offsets.
- `output_truncated` means the requested snapshot/window could not include all
  available rows within the configured character/window bound.
- `total_lines` is the total row count currently retained or known for the
  command.

The service should avoid duplicating transcript sources. The `command` crate can
continue writing PTY transcript bytes; `operation_service::command` should
provide the row projection by parsing or writing a structured row store beside
the raw PTY transcript. If structured row storage is added, it must remain
command-service policy, not PTY substrate policy, unless the command crate gains
a clearly policy-free row transcript API.

## Remount And Quiesce Design

Live remount coordination moves out of `WorkspaceRuntime`. The target ownership
is:

```text
WorkspaceRemountService
  owns the cross-service live-remount workflow
  calls WorkspaceManagerService for session state/resource remount steps
  calls CommandOperationService for command quiesce/resume steps
  keeps workspace and command services from importing each other for remount

WorkspaceManagerService
  owns session remount_pending state
  owns session snapshot/layer/lease metadata updates
  calls workspace.remount_workspace(...)
  decides when destroy/session state remains visible after failure

CommandOperationService
  owns command process lookup by command_id
  uses CommandRegistry command_id -> workspace_id bindings for remount scans
  owns process-group quiesce/resume
  owns /proc inspection
  reports command pressure/block telemetry

workspace::WorkspaceService
  performs the resource remount primitive
  verifies mount switch and lowerdir list through workspace/lower-level code

LayerStack
  owns lease metadata and layer deletion correctness
  does not depend on operation_service
```

### Remount Ownership Decision

The high-level remount entry point belongs to `WorkspaceRemountService`, not to
the low-level `workspace::WorkspaceService`, not to the daemon request
entrypoint, and not to `operation_service::workspace_manager` importing command-service
types.

`WorkspaceManagerService` owns workspace session state transitions and resource
remount application, but it must not hold or call `CommandOperationService`.
Likewise, `CommandOperationService` may use `WorkspaceManagerService` for
command exec lifecycle needs such as one-shot workspace creation/destruction,
but remount must not rely on workspace and command services calling each other
directly. Cross-service sequencing belongs in `WorkspaceRemountService`.

`workspace::WorkspaceService` should remain the resource primitive owner:
`remount_workspace(handle, request)` performs the mount switch and verification
when called.

`CommandOperationService` should own only the command-side quiesce primitive
because it owns active command processes and process groups. If the helper keeps
the name `begin_workspace_remount_quiesce`, it should be a command-service
method called by `WorkspaceRemountService`, not a daemon dispatch
operation and not a workspace resource primitive.

### Command-Side Quiesce API

```rust
impl CommandOperationService {
    pub(crate) fn begin_workspace_remount_quiesce(
        &self,
        workspace_id: &WorkspaceId,
    ) -> CommandRemountQuiesce;

    pub(crate) fn inspect_workspace_remount(
        &self,
        workspace_id: &WorkspaceId,
    ) -> CommandRemountInspection;
}
```

`begin_workspace_remount_quiesce`:

- scans `CommandRegistry.command_workspace` for active commands bound to
  `workspace_id`;
- treats every command as eligible for remount quiesce. There is no per-command
  remount opt-in flag in Phase 2;
- requires every process group to be known;
- sends `SIGSTOP` to each process group;
- waits until all members are stopped;
- verifies process membership did not change while freezing;
- inspects `/proc/<pid>/cwd`, `/proc/<pid>/root`, `/proc/<pid>/fd`,
  `/proc/<pid>/maps`, and `/proc/<pid>/mountinfo`;
- blocks remount on unknown inspection state;
- holds stopped process groups only when all checks pass;
- resumes all stopped process groups on success, failure, and early return.

Inspection output should preserve the current fields:

```rust
pub struct CommandRemountInspection {
    pub active_commands: usize,
    pub command_ids: Vec<CommandId>,
    pub process_group_ids: Vec<i32>,
    pub process_count: usize,
    pub quiesced_process_count: usize,
    pub pinned_cwd_count: usize,
    pub pinned_root_count: usize,
    pub pinned_fd_count: usize,
    pub pinned_mapped_file_count: usize,
    pub mountinfo_checked_count: usize,
    pub blocked_reason: Option<String>,
    pub inspected: bool,
    pub quiesce_attempted: bool,
    pub resumed: bool,
    pub detail: Option<String>,
}
```

### Workspace Remount Flow

Phase 2 default remount policy is full mounted-snapshot compaction, matching the
existing proven `layerstack::service::compact_snapshot_for_remount` behavior:
build a compact checkpoint from the session's currently mounted snapshot layer
list, remount the workspace to that single compact layer, verify the mount, then
retarget the session lease to the compact manifest. A later production policy may
replace this with parent-prefix compaction, but Phase 2 must not leave the
policy open inside the implementation.

```text
WorkspaceRemountService::compact_or_remount_session(workspace_id)
  WorkspaceManagerService::begin_remount(workspace_id)
    resolve active session
    mark session.remount_state = RemountPending
    expose remount_pending in session/status responses

  quiesce = CommandOperationService::begin_workspace_remount_quiesce(workspace_id)

  if no active commands:
    compact currently mounted snapshot to one checkpoint layer
    call WorkspaceManagerService::apply_remount(handler, compact_layer_paths)
    require mount verification success
    retarget LayerStack lease to compact manifest only after mount verification succeeds
    update session snapshot/layer paths/lease metadata/remount state through
      WorkspaceManagerService
    run active-stack cleanup only after lease retarget succeeds

  else if quiesce.inspection.can_live_remount:
    compact currently mounted snapshot to one checkpoint layer
    call WorkspaceManagerService::apply_remount(handler, compact_layer_paths)
      calls workspace.remount_workspace(handler, compact_layer_paths)
    require mount verification success
    retarget LayerStack lease only after mount verification succeeds
    update session snapshot/layer paths/lease metadata/remount state through
      WorkspaceManagerService
    run active-stack cleanup only after lease retarget succeeds
    quiesce.resume()
    WorkspaceManagerService::finish_remount(workspace_id)
    return compacted report

  else:
    quiesce.finish()   # resumes any stopped process groups
    WorkspaceManagerService::finish_or_block_remount(workspace_id)
    emit pressure-only blocked telemetry
    do not retarget lease
    do not delete old lowerdirs
    return blocked report
```

Required invariants:

- Unknown process inspection state blocks remount.
- Always resume stopped process groups on success, failure, and early return.
- Never retarget a lease before mount verification succeeds.
- Never delete old lowerdirs until lease retarget succeeds.
- `remount_pending` remains visible in session state while a remount attempt is
  in progress.
- After remount, update session snapshot, layer paths, lease metadata, and
  remount state.
- Destroy failure must retain session state.
- Blocked remount reports pressure-only telemetry; it must not run unsafe
  fallback compaction that deletes mounted lowerdirs.

`WorkspaceRemountService` owns the remount attempt guard. The guard holds the
`CommandRemountQuiesce`, the selected compact manifest/layer paths, and a
command-service cancellation token:

```rust
pub struct RemountAttemptGuard {
    pub workspace_id: WorkspaceId,
    pub quiesce: CommandRemountQuiesce,
    pub cancellation: RemountCancellationToken,
    pub switch_state: RemountSwitchState,
}

pub enum RemountSwitchState {
    Quiescing,
    ReadyToSwitch,
    CriticalSwitch,
    Resuming,
    Finished,
}
```

`CommandOperationService::begin_workspace_remount_quiesce` marks each held
command `QuiescedForRemount` and links it to the same
`RemountCancellationToken`. `WorkspaceRemountService` sets
`CriticalSwitch` immediately before calling `WorkspaceManagerService::apply_remount`
and clears all quiesce links only after every held process group has resumed.
`CommandOperationService::cancel` must consult this token before killing a
quiesced command.

### Behavior While `remount_pending`

Command start:

- Reject new commands for that `workspace_id` with a retryable
  `workspace_remount_pending` command-service error.
- Do not queue starts inside the service in Phase 2. Queuing adds cancellation
  and fairness policy that is not needed for the migration slice.

Write stdin:

- If the command is in a workspace with `remount_pending`, reject with
  `workspace_remount_pending` unless the remount has already cleared.
- Do not write to a process group currently held stopped for remount.

Read command lines:

- Allow transcript row reads during `remount_pending`.
- Return the current row window and include `remount_pending: true` in metadata
  if the response schema supports metadata.
- Do not force finalization while a command is quiesced.

Poll/read progress:

- Allow status reads during `remount_pending`.
- If the command is quiesced, report it as running with remount-pending
  metadata rather than timed out or failed.
- Finalization may proceed only after quiesce has resumed or the remount flow
  has explicitly aborted and resumed the process group.

Cancel:

- Cancellation requests are never ignored.
- If remount quiesce has not entered the critical mount switch,
  `CommandOperationService::cancel` records `CancellationState::Requested`,
  marks the token `AbortRequested`, and does not kill the stopped process group.
  `WorkspaceRemountService` must observe the token before entering
  `CriticalSwitch`, abort the remount attempt, resume all stopped process groups,
  then allow command termination/finalization as cancelled.
- If the critical mount switch has started, record `CancellationState::Requested`,
  finish the verified mount/lease-retarget/resume sequence or failure-resume
  sequence, then terminate/finalize the command immediately after resume.
- Never kill a stopped process group in a way that bypasses the "always resume"
  invariant.
- A cancel racing with quiesce must have a deterministic terminal result:
  either the remount attempt returns blocked/cancelled before the switch and the
  command finalizes cancelled after resume, or the switch completes/fails and the
  command finalizes cancelled immediately after the required resume step.

## Publish And Capture Semantics

For host one-shot commands (`workspace_id: None`):

- Success captures the temporary one-shot host workspace and publishes through
  the current command publish/OCC/lane behavior.
- Non-success, timeout, and cancellation discard before publish/OCC/spool side
  effects.
- Responses preserve `publish_lanes` and publish rejection details where the
  current command contract provides them.

For persistent session commands (`workspace_id: Some`):

- No implicit publish.
- No implicit workspace destroy.
- No session snapshot/layer update after every command.
- Finalization may include changed-path metadata derived from a bounded
  transient upperdir scan.
- Explicit publish/checkpoint/file phases will decide when session state becomes
  durable LayerStack state.

Capture data requirement:

- `WorkspaceManagerService::capture_changes` has one semantic mode: capture the
  changes currently present in the overlay upperdir.
- The capture result is generic workspace data, not command-specific data. It
  must include the captured `LayerChange` payloads, protected drops, route stats,
  metadata path count, optional spool directory, base revision, changed-path
  metadata, and resource stats needed by any later publish/checkpoint consumer.
- Capture itself must not mutate the overlay upperdir, publish to LayerStack,
  destroy the workspace, retarget leases, or decide whether changes are kept. If
  bounded capture needs a spool directory, that directory is a temporary capture
  artifact outside the upperdir and is cleaned up by the consumer/finalizer after
  publish or discard.
- The command service owns publish/discard decisions and spool cleanup after
  publish/discard. `workspace` and `WorkspaceManagerService` may produce the
  capture artifact, but they must not decide whether command output publishes.
- Persistent session command finalization must not call `capture_changes` as part
  of normal command completion. If it returns changed-path metadata, it must use
  a separate bounded non-mutating scan that does not materialize payloads, publish,
  retarget leases, or refresh session snapshot/layer metadata.

Target capture contract:

```rust
pub struct CaptureChangesRequest {
    pub bounds: BoundedCaptureOptions,
    pub include_stats: bool,
}

pub struct CapturedWorkspaceChanges {
    pub workspace_id: WorkspaceId,
    pub base_revision: BaseRevision,
    pub changed_paths: Vec<String>,
    pub changed_path_kinds: BTreeMap<String, ChangedPathKind>,
    pub protected_drops: Vec<ProtectedPathDrop>,
    pub stats: Option<TreeResourceStats>,
    pub changes: Vec<layerstack::LayerChange>,
    pub route_stats: layerstack::CaptureRouteStats,
    pub metadata_path_count: usize,
    pub spool_dir: Option<PathBuf>,
}
```

`CaptureChangesRequest` must not contain a `CaptureChangesMode`, command-specific
mode name, or metadata-only variant. If a caller only needs response metadata, it
uses a separate non-mutating scan instead of weakening the capture contract.

## Dependency And Cycle Plan

Forbidden cycles:

```text
operation_service -> operation::command -> operation_service
workspace -> operation_service
layerstack -> operation_service
```

Phase 2 dependency direction:

```text
daemon request entrypoint
  -> operation_service

operation_service
  -> command

operation_service
  -> workspace

operation_service
  -> layerstack

operation_service
  -> trace

command
  -> linux-namespace-subprocess, nix, rustix, serde, serde_json, time, thiserror
  -> no workspace
  -> no layerstack
  -> no operation_service

operation
  -> operation_service  # temporary compatibility wrappers only, if needed
```

Rules:

- `operation_service::command` must not import `operation::command`.
- `operation_service::workspace_manager` must not import `operation_service::command` or
  `CommandOperationService`; cross-service remount sequencing belongs in
  `WorkspaceRemountService`.
- `operation_service::workspace_remount` may depend on
  `operation_service::workspace_manager` and `operation_service::command`, but it must
  own only orchestration and must not reach into command process internals or
  workspace resource internals.
- Move command policy code into `operation_service::command`.
- Leave thin compatibility wrappers in `operation::command` only if needed to
  keep older tests or intermediate dispatch compiling. Wrappers may re-export or
  delegate to `operation_service`, but must not become the owner of policy.
- Delete old `operation::command` implementation modules in a later cleanup
  phase after the request entrypoint and tests no longer depend on them.
- The `command` crate must stay policy-free. It may own runner preparation,
  process storage, transcript rows, and process quiesce, but it must not import
  `workspace`, `layerstack`, `operation`, or `operation_service`.
- `workspace` must not import `operation_service` or command service types.
- `layerstack` must not import `operation_service` or workspace session types.
- If a shared type would cause a cycle, place it in the lowest crate that owns
  the concept:
  - PTY/process DTOs stay in `command`;
  - workspace resource handles stay in `workspace`;
  - command operation responses stay in `operation_service::command` unless
    they are true wire protocol types;
  - stable protocol catalog names stay in `protocol`.

## Migration Steps

1. Add `operation_service/src/command/mod.rs`, `service.rs`, `contract.rs`,
   `registry.rs`, `exec.rs`, `remount.rs`, and `error.rs`, plus
   `operation_service/src/workspace_remount/mod.rs`, `service.rs`, and
   `error.rs`.
2. Add `pub command: Arc<CommandOperationService>` and
   `pub remount: Arc<WorkspaceRemountService>` to `OperationServices`.
3. Add `command`, `layerstack`, `trace`, `serde`, and `serde_json` dependencies
   to `operation_service/Cargo.toml` as needed by the moved command policy.
4. Define command contract/output types in `operation_service::command::contract`,
   including `publish_lanes` response metadata where command responses still
   need it. Treat `operation::command::contract` as migration evidence only; do
   not make the target service depend on it.
5. Introduce `ExecCommandInput.workspace_id: Option<WorkspaceId>` and
   `CommandCallContext` in the operation-service command contract. Dispatch must
   reject `workspace_id: Some(_)` requests whose `workspace_root` conflicts with
   the resolved session handler.
6. Change command service exec dispatch to accept
   `Option<WorkspaceSessionHandler>`.
7. Implement `Some(handler)` session command flow: use existing workspace, no
   create, no destroy, no implicit publish.
8. Implement `None` host one-shot flow through `WorkspaceManagerService::create`
   and `WorkspaceManagerService::destroy`.
9. Extend `CapturedWorkspaceChanges` into the single generic upperdir-delta result
   so command service can publish successful host one-shot captures without moving
   publish policy into `workspace`.
10. Replace caller-primary command registry with a one-map
    `command_id -> workspace_id` registry.
11. Add `CommandCompletionStore` for retained completed command records keyed by
    `command_id`, with caller/workspace metadata used for authorization after
    registry removal.
12. Add row-oriented transcript projection for local_os-compatible reads.
13. Move stdin/read/poll/cancel logic into `CommandOperationService` and leave
    collect/count lifecycle advancement out of the Phase 2 command-service API.
14. Move command-side remount quiesce and `/proc` inspection into
    `operation_service::command::remount`.
15. Move remount orchestration out of `WorkspaceRuntime` into
    `operation_service::workspace_remount::WorkspaceRemountService`.
    `WorkspaceManagerService` owns session state/resource remount steps, and
    `CommandOperationService` owns command quiesce/resume steps. Use full
    mounted-snapshot compaction as the Phase 2 remount policy and wire the
    remount cancellation token before live process quiesce.
16. Route command requests from the daemon request entrypoint into
    `OperationServices` and keep only wire parsing, response shaping, and trace
    recording outside the operation services.
17. Delete or bypass `WorkspaceRuntime` command routing.
18. Leave temporary compatibility wrappers in `operation::command` only where
    compile/test migration requires them.
19. Add focused unit tests for exec Some/None flows, command registry binding,
    completion-store authorization, no-publish session commands, host one-shot
    publish/discard, local_os row projection, remount-pending behavior, remount
    cancellation races, and quiesce resume invariants.
20. Remove obsolete `operation::command` implementation modules in a later
    cleanup phase after old runtime dispatch deletion is complete.

## Non-Goals

Phase 2 does not implement:

- full file/plugin/checkpoint migration;
- persistent session store;
- daemon restart recovery;
- production auto-squash maintenance loop if it exceeds the Phase 2 command
  service slice;
- broad live E2E rewrite unless explicitly scheduled after focused checks pass;
- compatibility design that preserves `WorkspaceRuntime`;
- moving the low-level `command` crate into `operation_service`;
- changing LayerStack lease deletion authority;
- implicit publish/checkpoint after every persistent session command.

## Resolved Decisions

- The target command service does not mirror
  `crates/daemon/core/src/op_adapter/command.rs`.
- The target command service does not depend on
  `operation::command::contract`. That crate/module is current-state evidence
  and a migration source only.
- `exec_command` is the only command-service method that accepts
  `Option<WorkspaceSessionHandler>`.
- `ExecCommandInput` does not carry request-correlation identifiers; request
  correlation stays in command call/trace context.
- `CommandCallContext` carries caller ownership and trace context for every
  command-service operation.
- For `workspace_id: Some(_)`, `ExecCommandInput.workspace_root` must match the
  resolved session handler's workspace root.
- Stdin/read/poll/cancel accept `command_id` plus call context and resolve
  workspace association through `CommandRegistry`.
- Completed commands are removed from `CommandRegistry` but retained in a
  command-id keyed completion store that carries caller/workspace ownership for
  authorization.
- Host one-shot command success captures the generic workspace upperdir delta and
  then publishes it; persistent session command finalization may only use a
  non-mutating metadata scan.
- Phase 2 remount compaction uses full mounted-snapshot compaction by default.
- Remount cancellation is mediated through a remount cancellation token so
  stopped process groups always resume before command termination.
- Process exit after a yielded running response is finalized by the internal
  command-service finalization supervisor, not by a public
  `advance_active_commands_once` operation.

## Open Questions

1. Should the session command response keep the existing isolated-network
   changed-path metadata by doing a bounded read-only upperdir scan, or should
   the first Phase 2 slice return only status/output for session commands?
   Recommendation: keep the bounded metadata if it is cheap to preserve, but do
   not update session snapshot or publish.
2. Should row-oriented command output be exposed on the existing
   `sandbox.command.exec` response or added as a sibling local_os-compatible
   command surface? Recommendation: add row projection internally first and
   keep wire compatibility until the client migration is explicit.
3. What is the exact retry/error wire shape for `workspace_remount_pending`?
   Recommendation: model it as a typed command-service error with a stable
   string code, then let the request entrypoint shape it consistently with
   existing daemon errors.

## Implementation Guardrail Checklist

- Implement `WorkspaceRemountService` ownership so `operation_service::workspace_manager`
  does not depend on command-service types and does not call
  `CommandOperationService` directly.
- Add explicit remount-pending state, plus cleanup/resume guards that clear or
  mark the remount state and resume stopped process groups on success, failure,
  early return, and cancellation.
- Enforce caller ownership on every command-id operation. `write_stdin`,
  `read_lines`, `poll`, and `cancel` must validate active and retained completed
  command owners against `CommandCallContext`.
- Add `CommandCompletionStore` so completed command reads/polls remain
  authorized after `CommandRegistry` binding removal.
- Make unknown process inspection state block remount. `/proc` inspection must
  not treat failed `cwd`, `root`, `fd`, `maps`, or `mountinfo` reads as safe.
- Replace or restrict old `command::StartCommand` and `CollectCompleted` usage.
  Phase 2 command service must use operation-service-owned contracts and must
  not reintroduce request correlation fields or per-command remount flags.
- Implement one generic upperdir-delta capture contract for successful host
  one-shot finalization and keep persistent-session metadata scans separate from
  `capture_changes`.
- Split host one-shot publish/discard policy from persistent-session command
  finalization. One-shot commands capture the generic upperdir delta and publish
  it only on success; persistent session commands may mutate the live workspace,
  but normal command finalization must not publish, destroy the workspace, or
  update session snapshot/layer metadata. Optional changed-path response metadata
  must come from a non-mutating scan.
- Use full mounted-snapshot compaction as the Phase 2 remount policy unless a
  later production policy spec replaces it.
- Implement remount cancellation token handling so cancellation before the
  critical mount switch aborts/resumes before killing, and cancellation during
  the critical switch terminates only after required resume.
- Tighten compatibility wrappers so old `operation::command::contract` types and
  public dropped operations cannot survive Phase 2 as parallel APIs.

## Acceptance Criteria

Code/architecture criteria:

- `OperationServices` exposes `workspace`, `command`, and `remount`.
- `CommandOperationService` exists under
  `crates/daemon/operation_service/src/command/service.rs`.
- `WorkspaceRemountService` exists under
  `crates/daemon/operation_service/src/workspace_remount/service.rs`.
- `crates/daemon/command/src` remains a separate low-level crate.
- Policy-free mechanics such as runner preparation, transcript rows, process
  storage, and process quiesce may live in `command`.
- The `command` crate does not depend on `workspace`, `layerstack`,
  `operation`, or `operation_service`.
- `operation_service::command` does not depend on `operation::command`.
- `workspace` does not depend on `operation_service`.
- `layerstack` does not depend on `operation_service`.
- The daemon request entrypoint calls `operation_service` instead of
  `WorkspaceRuntime`.
- `WorkspaceRuntime` is not preserved as a compatibility layer.
- `ExecCommandInput` supports `workspace_id: Option<WorkspaceId>`.
- `ExecCommandInput` uses `workspace_root` as its only root path.
- `ExecCommandInput` does not carry request/trace correlation identifiers.
- `ExecCommandInput` has no per-command remount opt-in flag; every command is
  eligible for remount quiesce.
- `CommandCallContext` is constructed by operation-service dispatch and carries
  caller ownership plus trace context for every command-service method.
- `workspace_id: Some(_)` exec rejects mismatched `workspace_root` instead of
  launching in a caller-provided path that conflicts with the resolved handler.
- `CommandOperationService` does not expose `collect_completed`,
  `count_commands`, or `advance_active_commands_once`.
- `Some(handler)` command execution does not create, destroy, or implicitly
  publish a workspace.
- `None` command execution creates a temporary one-shot host workspace,
  publishes only on success, discards on non-success/cancel/timeout, destroys
  the temporary workspace, and never returns its workspace id as a reusable
  public session id.
- `CommandRegistry` contains exactly one map:
  `HashMap<CommandId, WorkspaceId>`.
- Workspace command lookup for remount scans the command-to-workspace binding
  map; Phase 2 does not add caller or workspace secondary indexes.
- Completed commands are retained in `CommandCompletionStore`, not
  `CommandRegistry`, and retained reads/polls validate
  `CompletedCommandRecord.caller_id` against `CommandCallContext`.
- `read_lines` can return the local_os-compatible row projection.
- Successful host one-shot commands publish the generic `CapturedWorkspaceChanges`
  upperdir delta; persistent session command metadata, if returned, comes from a
  bounded non-mutating scan.
- `WorkspaceRemountService` owns high-level cross-service remount orchestration.
- `WorkspaceManagerService` owns session remount state and resource remount
  application, but does not import or call command-service types.
- `CommandOperationService` owns only the process quiesce helper used by the
  `WorkspaceRemountService`.
- Phase 2 remount uses full mounted-snapshot compaction by default.
- `remount_pending` is visible in session state while remount is in progress.
- Unknown `/proc` inspection state blocks remount.
- Stopped process groups are resumed on success, failure, and early return.
- Lease retarget and old-lowerdir deletion happen only after mount verification
  and lease retarget success.
- Cancellation of a quiesced command never kills a stopped process group before
  the remount guard resumes it.

Focused compile/test gates:

```text
CARGO_TARGET_DIR=/tmp/eos-phase2-command-service-target cargo check -p command
CARGO_TARGET_DIR=/tmp/eos-phase2-command-service-target cargo check -p operation_service
CARGO_TARGET_DIR=/tmp/eos-phase2-command-service-target cargo test -p operation_service command
CARGO_TARGET_DIR=/tmp/eos-phase2-command-service-target cargo test -p operation command
cargo fmt --check
git diff --check
```

Conditional/later live E2E:

- Live daemon E2E is a later or conditional gate for Phase 2 unless the
  implementation changes live daemon behavior enough to require it.
- If live daemon E2E is required, package first so tests do not exercise a stale
  daemon binary:

```text
cargo run -p xtask -- package
```
