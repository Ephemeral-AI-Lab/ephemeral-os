# Operation Service Workspace Session Spec

Date: 2026-06-17
Status: Draft
Scope: `crates/daemon/operation_service`, operation crates, `crates/daemon/workspace`, `crates/daemon/layerstack`, `crates/daemon/core`

## Summary

This spec refines the daemon workspace ownership model for the operation-service
refactor.

The target design removes workspace policy ownership from `daemon/core`.
`daemon/core` should parse/dispatch wire requests and shape responses. It should
not preserve workspace mode, choose command/file routes, own workspace sessions,
or perform squash/remount policy.

The daemon operation layer owns workspace sessions:

```text
operation_service:
  workspace session registry
  request session lookup
  operation service collection
  background maintenance policy
  squash/remount pressure trigger

operation crates:
  decide operation workflow with or without a WorkspaceSessionHandler

workspace:
  resource primitives for Host workspace, isolated workspace, and readonly snapshot

layerstack:
  manifests, leases, pinned layer refcounts, publish/OCC, squash/reclaim
```

## Core Decision

Workspace mode should not be preserved by the workspace crate or by
`daemon/core`.

Workspace mode is split into two concepts:

1. `NetworkMode` is a workspace resource creation parameter.
2. `WorkspaceSession` is daemon operation state that binds later requests to a
   created workspace resource.

```rust
pub enum NetworkMode {
    Host,
    Isolated,
}
```

The workspace crate provides:

```text
create_workspace(NetworkMode::Host)
create_workspace(NetworkMode::Isolated)
get_workspace_latest_snapshot(...)
capture_changes(...)
remount_workspace(...)
destroy_workspace(...)
```

The workspace crate does not decide future operation routing. It only creates,
captures, remounts, and destroys resources when asked by operation code.

## Workspace Module Contract

The workspace module provides resource handles and resource operations.

```rust
pub struct WorkspaceHandle {
    pub id: WorkspaceId,
    pub owner: CallerId,
    pub workspace_root: PathBuf,
    pub network: NetworkMode,
    pub snapshot: LayerStackSnapshotRef,
}

pub struct ReadonlySnapshotHandle {
    pub view_root: PathBuf,
    pub generation_key: String,
    pub snapshot: LayerStackSnapshotRef,
}
```

Required primitive surface:

```rust
pub trait WorkspacePrimitives {
    fn create_workspace(
        &self,
        request: CreateWorkspaceRequest,
    ) -> Result<WorkspaceHandle, WorkspaceError>;

    fn capture_changes(
        &self,
        handle: &WorkspaceHandle,
        request: CaptureChangesRequest,
    ) -> Result<CapturedWorkspaceChanges, WorkspaceError>;

    fn remount_workspace(
        &self,
        handle: &WorkspaceHandle,
        request: RemountWorkspaceRequest,
    ) -> Result<RemountWorkspaceResult, WorkspaceError>;

    fn destroy_workspace(
        &self,
        handle: WorkspaceHandle,
        request: DestroyWorkspaceRequest,
    ) -> Result<DestroyWorkspaceResult, WorkspaceError>;

    fn get_workspace_latest_snapshot(
        &self,
        request: LatestSnapshotRequest,
    ) -> Result<ReadonlySnapshotHandle, WorkspaceError>;
}
```

Workspace must not expose:

```text
run_command(...)
write_file_latest(...)
edit_file_latest(...)
publish_changes(...)
apply_changeset(...)
future request routing
daemon session lookup
```

## Daemon Workspace Session Contract

The daemon operation layer preserves workspace sessions.

When `enter_isolated_workspace` creates a workspace, the daemon records a
session. Later requests carry `workspace_session_id`; the operation layer uses
that id to resolve a handler and inject it into the operation method.

```rust
pub struct WorkspaceSession {
    pub session_id: WorkspaceSessionId,
    pub caller_id: CallerId,
    pub workspace_handle_id: WorkspaceId,
    pub workspace_root: PathBuf,
    pub network: NetworkMode,
    pub layer_stack_root: PathBuf,
    pub lease_id: String,
    pub snapshot: LayerStackSnapshotRef,
    pub layer_paths: Vec<PathBuf>,
    pub remount_state: RemountState,
    pub created_at: Timestamp,
    pub last_activity: Timestamp,
}

pub struct WorkspaceSessionHandler {
    pub session_id: WorkspaceSessionId,
    pub handle: WorkspaceHandle,
    pub layer_stack_root: PathBuf,
    pub lease_id: String,
    pub snapshot: LayerStackSnapshotRef,
    pub layer_paths: Vec<PathBuf>,
}
```

Session registry indexes:

```rust
pub struct WorkspaceSessionRegistry {
    by_session: HashMap<WorkspaceSessionId, WorkspaceSession>,
    by_caller: HashMap<CallerId, BTreeSet<WorkspaceSessionId>>,
    by_workspace_handle: HashMap<WorkspaceId, WorkspaceSessionId>,
    by_lease: HashMap<LeaseId, WorkspaceSessionId>,
}
```

The primary lookup path is `workspace_session_id`. Caller-keyed lookup can exist
only as a compatibility path while old clients migrate.

### Registry Ownership

`operation_service` owns `WorkspaceSessionRegistry` through a dedicated
`WorkspaceSessionService`.

```rust
pub struct OperationServices {
    pub sessions: Arc<WorkspaceSessionService>,
    pub command: Arc<CommandOperation>,
    pub file: Arc<FileOperation>,
    pub plugin: Arc<PluginOperation>,
    pub isolated_workspace: Arc<IsolatedWorkspaceOperation>,
    pub checkpoint: Arc<CheckpointOperation>,
}

pub struct WorkspaceSessionService {
    registry: WorkspaceSessionRegistry,
    store: WorkspaceSessionStore,
}
```

Ownership rules:

- `WorkspaceSessionService` is the only component that mutates
  `WorkspaceSessionRegistry`.
- `isolated_workspace_operation` asks `WorkspaceSessionService` to create,
  resolve, close, and remove isolated workspace sessions.
- request dispatch asks `WorkspaceSessionService` to resolve a handler only
  when the request carries `workspace_session_id`.
- `command_operation` and `file_operation` receive a
  `WorkspaceSessionHandler`; they do not own the registry.
- `workspace` does not own sessions or routing state. It only owns resource
  primitives such as create, capture, remount, readonly snapshot, and destroy.
- `daemon/core` does not own the registry. It parses wire requests, calls
  `operation_service`, and shapes wire responses.

Handler resolution is session-only. A request with `workspace_session_id`
resolves to `Some(WorkspaceSessionHandler)`. A request without
`workspace_session_id` calls the operation method with `None`.

`None` is an explicit operation-mode input, not a daemon session. For
`command_operation`, `None` means the command operation may use its normal
one-shot `NetworkMode::Host` workspace workflow and own that temporary
workspace lifecycle. The one-shot host workspace is not inserted into
`WorkspaceSessionRegistry` and must not be treated as an isolated workspace
session.

## Request Flow

### Enter Isolated Workspace

```text
request: enter_isolated_workspace(caller_id, workspace_root)

isolated_workspace_operation:
  resolve workspace_root
  call workspace.create_workspace(NetworkMode::Isolated)
  create WorkspaceSession
  insert WorkspaceSession into operation_service registry
  return workspace_session_id + workspace_handle_id
```

`enter_isolated_workspace` is the operation that creates an isolated workspace
session. Normal command/file operations must not create isolated sessions
implicitly.

### Later Operation With Session

```text
request: exec_command(..., workspace_session_id)

operation_service:
  resolve WorkspaceSession
  validate caller owns session
  build WorkspaceSessionHandler
  call command_operation with handler

command_operation:
  sees handler.network == NetworkMode::Isolated
  runs command in existing workspace
  does not create one-shot workspace
  does not destroy isolated workspace
```

The same model applies to file operations:

```text
write_file(..., workspace_session_id)
  -> file_operation writes into mounted workspace upperdir
  -> no implicit publish
  -> no implicit destroy
```

### Later Operation Without Persisted Session

```text
request: exec_command(..., no workspace_session_id)

operation_service:
  call command_operation.exec_command(input, None)

command_operation:
  sees None
  creates one-shot NetworkMode::Host workspace when needed
  runs command
  captures/publishes according to command policy
  destroys the one-shot workspace it created
```

This is intentionally different from the session path. A provided handler means
the operation must use an existing session workspace and must not create or
destroy it. A missing handler means `command_operation` is free to run its
ordinary host one-shot workflow.

For targeted file operations without a session:

```text
write_file(..., no workspace_session_id)

file_operation:
  get readonly latest snapshot
  compute LayerChange
  publish through layerstack.publish_to_layer_stack
  no mounted workspace creation required
```

## Operation Method Shape

Rust does not support method overloading by signature. The target design uses
one method with an optional handler:

```rust
impl CommandOperation {
    pub fn exec_command(
        &self,
        input: ExecCommandInput,
        workspace: Option<WorkspaceSessionHandler>,
    ) -> Result<CommandOutcome, CommandError>;
}
```

The same shape should be used for file/plugin/checkpoint methods that can
operate against either a resolved workspace handler or a latest-snapshot
workflow:

```rust
impl FileOperation {
    pub fn write_file(
        &self,
        input: WriteFileInput,
        workspace: Option<WorkspaceSessionHandler>,
    ) -> Result<FileOperationOutcome, FileOperationError>;
}
```

Semantics:

- request dispatch resolves and injects `Some(handler)` only when the request
  carries `workspace_session_id`.
- `Some(handler)` means the operation must use the provided workspace and must
  not create or destroy a workspace internally.
- `Some(handler)` is a persisted workspace session, typically created by
  `enter_isolated_workspace`.
- `None` for `command_operation` means the command operation owns its normal
  one-shot `NetworkMode::Host` workspace lifecycle.
- `None` for targeted file operations can mean a non-mounted workflow such as
  readonly latest snapshot plus direct layer publish.
- explicit lifecycle operations, such as `enter_isolated_workspace` and
  `exit_isolated_workspace`, are the only operations that create or destroy
  persisted isolated workspace sessions.

## Exit Isolated Workspace

```text
request: exit_isolated_workspace(workspace_session_id, grace_s)

isolated_workspace_operation:
  resolve session
  coordinate command cancellation or active-command rejection policy
  call workspace.destroy_workspace(handle)
  release session from registry
  return destroy report
```

Exit discards the isolated workspace upperdir by default. It must not publish
implicitly.

Destroy responsibility belongs to the explicit exit operation, not to ordinary
command/file operations that merely receive a `WorkspaceSessionHandler`.

## Squash And Pinned Layer Tracking

There are two registries with different jobs.

Daemon session registry:

```text
Which workspace sessions are open?
Which caller owns each session?
Which workspace handle belongs to the session?
Which LayerStack lease id backs the session?
Which active commands are bound to the session?
Is the session active, remount_pending, or closing?
```

LayerStack lease registry:

```text
Which lease ids are active?
Which manifest is leased by each lease id?
Which LayerRef values are pinned?
What is the refcount for each pinned LayerRef?
```

LayerStack is the authority for pinned layers. Daemon sessions are the authority
for mapping those leases back to open workspaces and callers.

Target relation:

```text
WorkspaceSession.session_id
  -> WorkspaceSession.lease_id
  -> LayerStack lease manifest
  -> pinned LayerRef set
```

LayerStack must not rely on daemon session state to decide whether a layer can
be deleted. It should delete only layers not referenced by active lease
refcounts.

The daemon uses `by_lease` to explain pressure and to trigger live remount:

```rust
registry.by_lease[lease_id] -> workspace_session_id
```

## Squash Policy Flow

```text
on publish/finalize/maintenance:
  metrics = layerstack.storage_metrics()
  pressure = layerstack.lease_pressure()

  if depth and bytes are below thresholds:
      return

  ordinary_reclaim = layerstack.reclaim_unpinned_gaps()

  for each blocking lease from pressure:
      if lease maps to open WorkspaceSession:
          ask isolated_workspace_operation to attempt live remount
      else:
          keep hard protection; report orphan or stale lease pressure
```

Live remount is not a workspace mode. It is a transient maintenance state on an
existing daemon workspace session:

```rust
pub enum RemountState {
    Active,
    Pending,
    Closing,
}
```

Live remount flow:

```text
operation_service:
  select pressured session
  mark session remount_pending

command_operation:
  verify active commands are isolated and remountable
  quiesce process groups
  inspect cwd/root/fd/mmap/mountinfo

layerstack:
  build compact mounted-snapshot or leased-parent manifest

workspace:
  remount existing workspace with compact lowerdir list
  verify mountinfo lowerdir state

layerstack:
  retarget lease only after mount verification
  run squash/reclaim cleanup

operation_service:
  update WorkspaceSession snapshot/layer_paths/remount_state
  resume commands
  clear remount_pending
```

Required invariants:

- Never retarget a lease before workspace mount verification succeeds.
- Never delete lowerdirs referenced by the old lease until retarget succeeds.
- Always resume quiesced commands on success, failure, or early return.
- Treat unknown process inspection state as blocked.
- Keep `remount_pending` visible in the session registry so concurrent
  operations can reject, wait, or route according to operation policy.
- On daemon restart, reload sessions, verify holder/mount/lease state, and
  either recover the session or destroy the workspace and release the lease.

## Dependency Rules

Target dependency direction:

```text
daemon/core -> operation_service

operation_service -> command_operation
operation_service -> file_operation
operation_service -> plugin_operation
operation_service -> isolated_workspace_operation
operation_service -> checkpoint_operation

command_operation -> workspace
command_operation -> layerstack publish port

file_operation -> workspace
file_operation -> layerstack publish/read ports

isolated_workspace_operation -> workspace
isolated_workspace_operation -> command_operation liveness/cancel/remount port
isolated_workspace_operation -> layerstack remount/squash port

workspace -> layerstack snapshot/lease/view setup

layerstack -> workspace forbidden
layerstack -> operation_service forbidden
layerstack -> operation crates forbidden
```

`daemon/core` must not depend on old `operation`, `command`, `plugin`, or
`plugin-contract` once the operation-service split is complete.

## Migration Notes

1. Add `operation_service` and operation crates beside existing crates.
2. Move session/routing policy out of `daemon/core`.
3. Introduce `workspace_session_id` in request contracts where session binding
   is needed.
4. Return `workspace_session_id` from `enter_isolated_workspace`.
5. Make operation methods accept optional or explicit `WorkspaceSessionHandler`.
6. Move live remount orchestration to operation service and
   isolated-workspace operation.
7. Keep LayerStack as the source of truth for pinned layer refs.
8. Retire `WorkspaceRuntime` from `daemon/core`.

## Open Questions

- Should ordinary operations reject missing `workspace_session_id` when the
  caller owns an active isolated session, or should caller-keyed compatibility
  routing remain during migration?
- Should `exit_isolated_workspace` cancel active commands by default, or reject
  while commands are active unless `force` is provided?
- Should live remount use full snapshot compaction or leased-head plus
  compact-parent compaction as the default production representation?
