# Phase 1 Workspace Service And Workspace Manager Migration Spec

Date: 2026-06-17
Status: Draft
Parent spec: `docs/daemon/workspace_migration/operation_service_workspace_session_SPEC.md`

## Summary

Phase 1 establishes the two workspace ownership boundaries needed by the
operation-service workspace-session migration:

```text
workspace::WorkspaceService
  low-level workspace resource service
  no daemon sessions
  no request routing policy
  no command lifecycle policy

operation_service::workspace::WorkspaceManagerService
  daemon-owned open workspace manager
  workspace_id lookup
  session manager
  lifecycle wrapping over workspace::WorkspaceService
```

The goal is to create the durable module/API shape first, then migrate command,
file, plugin, checkpoint, and full daemon dispatch behavior in later phases.

## Phase 1 Goals

- Keep `crates/daemon/workspace/src/service.rs` as the user-facing low-level
  resource boundary named `workspace::WorkspaceService`.
- Make `WorkspaceService` expose resource primitives only:
  create, capture, remount, destroy, and latest snapshot.
- Add `crates/daemon/operation_service` with a `workspace/` module containing
  `WorkspaceManagerService`.
- Make `WorkspaceManagerService` own the in-memory open-workspace session state and
  wrap `workspace::WorkspaceService` lifecycle calls.
- Use `workspace_id` as the public reusable open-workspace handle. Do not add
  `workspace_session_id`.
- Model isolated enter/exit as workspace manager lifecycle routes:
  `create(..., NetworkMode::Isolated)` and `destroy(...)`.
- Merge workspace-run end/cancel-all ownership into `WorkspaceManagerService`.
  Do not create a separate `run/` service.
- Keep the phase compileable with focused unit tests around the new boundaries.

## Non-Goals

- Do not fully migrate `daemon/core` dispatch off `WorkspaceRuntime` in Phase 1.
- Do not migrate all command/file request routing to `operation_service` yet.
- Do not change wire behavior beyond types needed to compile the new boundary.
- Do not change LayerStack publish/OCC behavior.
- Do not implement production live remount orchestration yet; define the
  workspace manager extension points only.
- Do not move command execution internals into `workspace`.

## Current State

`crates/daemon/workspace/src/service.rs` currently defines:

```rust
pub trait WorkspaceService {
    fn create(&self, request: CreateWorkspaceRequest) -> Result<WorkspaceHandle, WorkspaceError>;
    fn capture_changes(...);
    fn destroy(...);
}
```

That name is correct, but Phase 1 should tighten the contract so the method
names are unambiguous resource operations:

```rust
pub trait WorkspaceService {
    fn create_workspace(...);
    fn capture_changes(...);
    fn remount_workspace(...);
    fn destroy_workspace(...);
    fn latest_snapshot(...);
}
```

`daemon/core/src/runtime/workspace.rs` currently owns mixed policy:
caller-keyed isolated state, command/file route decisions, lease custody,
teardown, and remount pressure behavior. Phase 1 does not remove all of that
yet, but it introduces the destination owner for this state:
`operation_service::workspace::WorkspaceManagerService`.

## Target Module Layout

Add:

```text
crates/daemon/operation_service/
  Cargo.toml
  src/
    lib.rs
    services.rs
    error.rs

    workspace/
      mod.rs
      service.rs
      session_manager.rs
      error.rs
```

Keep:

```text
crates/daemon/workspace/src/service.rs
```

Do not add:

```text
crates/daemon/operation_service/src/ops/
crates/daemon/operation_service/src/run/
crates/daemon/operation_service/src/isolation/
```

## Workspace Service Contract

`workspace::WorkspaceService` is resource-facing. It may depend on workspace
mechanics and LayerStack resource primitives, but it must not depend on
`operation_service`, command operation services, daemon dispatch, plugin
contracts, or wire request routing.

Target shape:

```rust
pub trait WorkspaceService: Send + Sync {
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

    fn latest_snapshot(
        &self,
        request: LatestSnapshotRequest,
    ) -> Result<ReadonlySnapshotHandle, WorkspaceError>;
}
```

`WorkspaceService` must not expose:

```text
run_command(...)
write_file_latest(...)
edit_file_latest(...)
publish_changes(...)
apply_changeset(...)
caller active session lookup
workspace_id routing policy
command cancellation policy
workspace-run teardown policy
```

## Workspace Manager Contract

`operation_service::workspace::WorkspaceManagerService` is daemon operation
state. It wraps `workspace::WorkspaceService`, records open-workspace sessions,
and exposes handlers to operation services.

Target shape:

```rust
pub struct WorkspaceManagerService {
    sessions: WorkspaceSessionManager,
    workspace: Arc<dyn workspace::WorkspaceService>,
}

impl WorkspaceManagerService {
    pub fn create(
        &self,
        request: CreateWorkspaceRequest,
    ) -> Result<WorkspaceSessionHandler, WorkspaceManagerError>;

    pub fn resolve(
        &self,
        workspace_id: WorkspaceId,
        caller_id: CallerId,
    ) -> Result<WorkspaceSessionHandler, WorkspaceManagerError>;

    pub fn capture_changes(
        &self,
        handler: &WorkspaceSessionHandler,
        request: CaptureChangesRequest,
    ) -> Result<CapturedWorkspaceChanges, WorkspaceManagerError>;

    pub fn remount_workspace(
        &self,
        handler: &WorkspaceSessionHandler,
        request: RemountWorkspaceRequest,
    ) -> Result<WorkspaceSessionHandler, WorkspaceManagerError>;

    pub fn destroy(
        &self,
        handler: WorkspaceSessionHandler,
        request: DestroyWorkspaceRequest,
    ) -> Result<DestroyWorkspaceResult, WorkspaceManagerError>;
}
```

The service name is `WorkspaceManagerService`. The error type should use the
same naming family, `WorkspaceManagerError`, because the service owns more than
the session map: lifecycle wrapping, recovery, teardown, pressure, and remount
coordination.

## Session Manager

`WorkspaceSessionManager` is internal to the workspace manager module. It owns
the in-memory open-workspace sessions and exposes consistent derived lookup
methods. `WorkspaceSession` is not a wire type and not a workspace-crate
primitive.

Target shape:

```rust
pub(crate) struct WorkspaceSession {
    pub workspace_id: WorkspaceId,
    pub caller_id: CallerId,
    pub handle: WorkspaceHandle,
    pub layer_stack_root: PathBuf,
    pub lease_id: LeaseId,
    pub snapshot: LayerStackSnapshotRef,
    pub layer_paths: Vec<PathBuf>,
    pub remount_state: RemountState,
    pub lifecycle_state: WorkspaceLifecycleState,
    pub created_at: Timestamp,
    pub last_activity: Timestamp,
}

pub struct WorkspaceSessionHandler {
    pub workspace_id: WorkspaceId,
    pub handle: WorkspaceHandle,
    pub layer_stack_root: PathBuf,
    pub lease_id: LeaseId,
    pub snapshot: LayerStackSnapshotRef,
    pub layer_paths: Vec<PathBuf>,
}
```

Prefer storing `handle: WorkspaceHandle` instead of duplicating
`workspace_root`, `network`, and other fields unless a duplicate is needed for
persistence or audit.

Phase 1 uses one primary map keyed by `WorkspaceId`:

```rust
pub(crate) struct WorkspaceSessionManager {
    sessions: HashMap<WorkspaceId, WorkspaceSession>,
}
```

Caller and lease lookups are derived by scanning `sessions` in Phase 1. Add
secondary indexes only if later run-teardown or live-remount phases prove the
lookups are hot enough to justify the consistency burden.

Use one naming convention for derived lookups:

```rust
impl WorkspaceSessionManager {
    pub(crate) fn insert(
        &mut self,
        session: WorkspaceSession,
    ) -> Result<(), WorkspaceManagerError>;

    pub(crate) fn remove(
        &mut self,
        workspace_id: &WorkspaceId,
    ) -> Option<WorkspaceSession>;

    pub(crate) fn find_by_workspace_id(
        &self,
        workspace_id: &WorkspaceId,
    ) -> Option<&WorkspaceSession>;

    pub(crate) fn find_by_caller_id(
        &self,
        caller_id: &CallerId,
    ) -> Vec<&WorkspaceSession>;

    pub(crate) fn find_by_lease_id(
        &self,
        lease_id: &LeaseId,
    ) -> Option<&WorkspaceSession>;
}
```

## Phase 1 Migration Steps

1. Add `crates/daemon/operation_service` to the workspace.
2. Add `operation_service` to root `Cargo.toml` workspace members and
   workspace dependencies.
3. Create `operation_service/src/lib.rs`, `services.rs`, and `error.rs`.
4. Create `operation_service/src/workspace/` with `service.rs`,
   `session_manager.rs`, and `error.rs`.
5. Define `WorkspaceManagerService`, `WorkspaceSession`,
   `WorkspaceSessionHandler`, `WorkspaceSessionManager`, and
   `WorkspaceManagerError`.
6. Update `workspace::WorkspaceService` method names to the resource-facing
   names in this spec.
7. Add missing resource request/result types needed by the trait:
   `RemountWorkspaceRequest`, `RemountWorkspaceResult`,
   `LatestSnapshotRequest`, and `ReadonlySnapshotHandle`.
8. Remove or relocate stale command-oriented model types from the workspace
   service surface if they are no longer used by `WorkspaceService`.
9. Implement a minimal in-memory `WorkspaceSessionManager` with insert, remove,
   `find_by_workspace_id`, `find_by_caller_id`, and `find_by_lease_id`.
10. Implement `WorkspaceManagerService::create` and `destroy` as wrappers around
    `workspace::WorkspaceService`, including rollback-on-session-insert-failure
    behavior.
11. Add unit tests for session manager coherence, caller ownership validation,
    create rollback, destroy failure retention, and `workspace_id` lookup.
12. Keep existing daemon dispatch paths intact unless a small compile adapter is
    needed.

## Phase 1 File Impact

Expected adds:

```text
crates/daemon/operation_service/Cargo.toml
crates/daemon/operation_service/src/lib.rs
crates/daemon/operation_service/src/services.rs
crates/daemon/operation_service/src/error.rs
crates/daemon/operation_service/src/workspace/mod.rs
crates/daemon/operation_service/src/workspace/service.rs
crates/daemon/operation_service/src/workspace/session_manager.rs
crates/daemon/operation_service/src/workspace/error.rs
crates/daemon/operation_service/tests/workspace_manager.rs
```

Expected edits:

```text
Cargo.toml
Cargo.lock
crates/daemon/workspace/src/service.rs
crates/daemon/workspace/src/model.rs
crates/daemon/workspace/src/lib.rs
docs/daemon/workspace_migration/operation_service_workspace_session_SPEC.md
```

Possible compile-adapter edits:

```text
crates/daemon/core/Cargo.toml
crates/daemon/core/src/runtime/services.rs
```

Phase 1 should avoid broad edits under:

```text
crates/daemon/core/src/op_adapter/
crates/daemon/operation/src/command/
crates/daemon/operation/src/file/
crates/e2e-test/tests/
```

Those belong to later routing and live E2E phases unless a narrow compile fix is
required.

## Acceptance Criteria

- `workspace::WorkspaceService` is the low-level resource service name used by
  the spec and code.
- `WorkspaceManagerService` exists under
  `operation_service/src/workspace/service.rs`.
- No `workspace_session_id` public contract is introduced.
- No separate `operation_service/src/run/` module is introduced.
- No separate `operation_service/src/isolation/` module is introduced for
  isolated enter/exit lifecycle.
- `WorkspaceSessionManager` stores sessions in one map keyed by `WorkspaceId`.
- Session manager tests prove insert/remove and all `find_by_*` methods behave
  consistently.
- Destroy failure does not silently remove a live session from the session manager.
- Create failure after raw workspace creation schedules cleanup or destroys the
  raw workspace before returning.
- Focused checks pass:

```text
CARGO_TARGET_DIR=/tmp/eos-phase1-op-service-target cargo check -p workspace
CARGO_TARGET_DIR=/tmp/eos-phase1-op-service-target cargo check -p operation_service
CARGO_TARGET_DIR=/tmp/eos-phase1-op-service-target cargo test -p operation_service workspace
cargo fmt --check
git diff --check
```

## Deferred To Later Phases

- Moving `daemon/core` request dispatch to call `operation_service`.
- Removing `WorkspaceRuntime` from `daemon/core`.
- Adding `workspace_id` to all command/file wire request contracts.
- Migrating command/file operations to accept `Option<WorkspaceSessionHandler>`.
- Production live remount orchestration.
- Daemon restart recovery that reconciles real holder/mount/lease state.
- Persistent session store and recovery modules.
- Pressure/maintenance module for live remount triggers.
- Live `workspace-runtime-command` and `workspace-runtime-isolated` E2E.

## Risks

- Naming collision risk: both crates have `workspace/service.rs`. This is
  acceptable because the exported type names carry the boundary:
  `workspace::WorkspaceService` versus
  `operation_service::workspace::WorkspaceManagerService`.
- Dependency cycle risk: if command/file operation crates need the concrete
  manager type while `operation_service` depends on them, introduce a lower
  operation-owned workspace-manager crate before wiring command/file services.
- Partial migration risk: Phase 1 deliberately leaves old dispatch active, so
  tests must prove new boundaries compile without claiming the full routing
  migration is complete.
