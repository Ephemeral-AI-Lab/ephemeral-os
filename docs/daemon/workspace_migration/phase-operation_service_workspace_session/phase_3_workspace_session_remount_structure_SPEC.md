# Phase 3 Workspace Session And Remount Structure Spec

Date: 2026-06-19
Status: Draft
Parent spec: `docs/daemon/workspace_migration/operation_service_workspace_session_SPEC.md`
Previous phase: `docs/daemon/workspace_migration/phase-operation_service_workspace_session/phase_2_command_service_SPEC.md`

## Summary

Phase 3 finalizes the operation-service folder structure and dependency
boundaries after the command-service migration.

This phase covers only workspace sessions, command workspace use, and workspace
remount orchestration. It does not introduce or specify a file service.

The final rule is:

```text
workspace
  owns workspace resource mechanics

workspace_session
  owns daemon session coherence

command
  owns command workflow and command process state

workspace_remount
  owns all operation-level remount coordination
```

Raw remount remains a `workspace::WorkspaceService` primitive. Live remount
orchestration belongs to `operation_service::workspace_remount`. Command-side
remount coordination must be exposed through a remount-specific port owned by
`workspace_remount`, not through a generic `ports/command_remount` module.

## Goals

- Rename `operation_service::workspace_manager` to
  `operation_service::workspace_session`.
- Keep `workspace::WorkspaceService` as the raw resource API with
  `remount_workspace`.
- Move remount-specific command quiesce model and coordination contracts under
  `operation_service::workspace_remount`.
- Remove the concept of `ports/command_remount.rs`; remount ports are owned by
  `workspace_remount`.
- Keep command process internals private to `operation_service::command`.
- Make `CommandOperationService` import and use
  `workspace_session::WorkspaceSessionService` directly for workspace session
  create/resolve/capture/destroy and remount-pending checks.
- Keep `WorkspaceRemountService` dependent on remount-specific ports:
  `CommandRemountCoordinator` and `RemountWorkspaceSession`.
- Preserve `daemon/command` as the low-level process, PTY, and transcript
  substrate.
- Preserve behavior during the migration; this phase is not a wire-protocol
  redesign.

## Non-Goals

- Do not add, migrate, or specify a file service in this phase.
- Do not add `operation_service/src/file`.
- Do not add `ports/file_workspace.rs`.
- Do not move raw workspace creation, capture, remount, or destroy mechanics
  into `operation_service`.
- Do not move command spawning, PTY, transcript, or process cancellation
  mechanics into `workspace`.
- Do not let `workspace_remount` mutate `CommandProcessStore` directly.
- Do not add a generic `run/` or `isolation/` service.
- Do not introduce a public `workspace_session_id`; `workspace_id` remains the
  reusable public handle.
- Do not change command wire op names in this phase.

## Current State

The current operation-service code has the right broad domains but uses names
and direct dependencies that obscure ownership:

```text
crates/daemon/operation_service/src/
  command/
    remount.rs
    service.rs
    ...
  workspace_manager/
    service.rs
    session_manager.rs
    error.rs
  workspace_remount/
    service.rs
    error.rs
```

Problems to fix:

- `workspace_manager` is too broad. The module does not own workspace mechanics;
  it owns daemon-visible open workspace sessions.
- `command/remount.rs` makes remount look like a command feature. Remount is a
  workspace lifecycle operation that must coordinate active commands.
- `WorkspaceRemountService` currently reaches concrete command and workspace
  session services. That is acceptable behaviorally, but the final boundary
  should make the remount-specific contracts explicit.
- `CommandOperationService` should depend on the concrete workspace session
  service, not on raw `workspace::WorkspaceService` and not on a duplicate
  command-workspace port.

## Target Folder Structure

Final target:

```text
crates/daemon/workspace/src/
  lib.rs
  model.rs
  service.rs                    # WorkspaceService: raw workspace primitives
  error.rs

  lifecycle.rs                  # shared create/destroy/capture/remount helpers
  profile/
    mod.rs
    manager.rs
    host_compatible.rs
    isolated.rs
    resource_control.rs
    common.rs
  overlay/
  namespace/
  isolated_setup/
```

```text
crates/daemon/operation_service/src/
  lib.rs
  services.rs                   # OperationServices composition root
  dispatch.rs                   # optional op/request -> service method routing
  error.rs

  workspace_session/
    mod.rs
    model.rs
    session_store.rs
    error.rs
    service.rs                  # WorkspaceSessionService, constructor/accessors, mod impls
    service/
      impls/
        mod.rs
        create_workspace_session.rs
        resolve_session.rs
        capture_session_changes.rs
        destroy_session.rs

  command/
    mod.rs
    contract.rs
    error.rs
    registry.rs
    process_store.rs
    transcript.rs
    launch.rs
    finalize.rs                 # shared finalization helpers, not a public op
    service.rs                  # CommandOperationService, constructor/accessors, mod impls
    service/
      impls/
        mod.rs
        exec_command.rs
        write_stdin.rs
        read_lines.rs
        poll.rs
        cancel.rs

  workspace_remount/
    mod.rs
    error.rs
    command_quiesce.rs          # remount-specific command quiesce model
    command_port.rs             # CommandRemountCoordinator trait
    command_remount_coordinator.rs
    workspace_port.rs           # RemountWorkspaceSession trait
    remount_workspace_session.rs
    service.rs                  # WorkspaceRemountService, constructor/accessors, mod impls
    service/
      impls/
        mod.rs
        remount_workspace_session.rs
```

Do not add:

```text
crates/daemon/operation_service/src/file/
crates/daemon/operation_service/src/ports/
crates/daemon/operation_service/src/ports/file_workspace.rs
crates/daemon/operation_service/src/ports/command_remount.rs
crates/daemon/operation_service/src/ports/remount_workspace.rs
crates/daemon/operation_service/src/workspace_manager/
crates/daemon/operation_service/src/command/remount.rs
```

Remount-specific ports live in `workspace_remount/` because
`workspace_remount` owns the operation-level remount workflow.

## Dependency Direction

Target dependency graph:

```text
daemon/core
  -> operation_service

operation_service::services
  -> workspace_session
  -> command
  -> workspace_remount

operation_service::command
  -> workspace_session::WorkspaceSessionService
  -> daemon/command substrate

operation_service::workspace_remount
  -> workspace_remount::CommandRemountCoordinator
  -> workspace_remount::RemountWorkspaceSession

operation_service::workspace_session
  -> workspace::WorkspaceService

workspace
  -> layerstack / overlay / namespace / profile

daemon/command
  -> process / PTY / transcript only
```

Forbidden dependency directions:

```text
workspace -> operation_service
workspace -> command operation service
daemon/command -> workspace_session
daemon/command -> operation_service
workspace_remount -> command::process_store internals
command -> workspace::WorkspaceService directly for session work
```

The command operation service may use raw workspace handles, but it obtains
them through `WorkspaceSessionService`. It must not bypass session ownership by
holding `Arc<dyn workspace::WorkspaceService>` for caller-owned or reusable
workspaces. The `daemon/command` substrate crate still must not depend on
`workspace_session`; only `operation_service::command` may.

## Operation API Surface By Service

This section is the service-operation index for the migration. These are Rust
service APIs, not wire op names.

### `workspace::WorkspaceService`

| Operation | Inputs | Output | Responsibility |
|---|---|---|---|
| `create_workspace` | `CreateWorkspaceRequest` | `WorkspaceHandle` | Create the raw mounted workspace resource. |
| `capture_changes` | `&WorkspaceHandle`, `CaptureChangesRequest` | `CapturedWorkspaceChanges` | Capture upperdir/resource changes for a workspace handle. |
| `remount_workspace` | `&WorkspaceHandle`, `RemountWorkspaceRequest` | `RemountWorkspaceResult` | Apply the raw workspace remount mechanics. |
| `destroy_workspace` | `WorkspaceHandle`, `DestroyWorkspaceRequest` | `DestroyWorkspaceResult` | Destroy the raw workspace resource and release resource-side state. |
| `latest_snapshot` | `LatestSnapshotRequest` | `ReadonlySnapshotHandle` | Open a readonly latest snapshot view. |

| Owns | Must Not Own |
|---|---|
| Raw workspace mechanics. | Sessions, caller routing, command lifecycle, or remount orchestration. |

### `workspace_session::WorkspaceSessionService`

| Operation | Inputs | Output | Responsibility |
|---|---|---|---|
| `new` | `Arc<dyn workspace::WorkspaceService>` | `WorkspaceSessionService` | Construct the session service over raw workspace primitives. |
| `create_workspace_session` | `CreateWorkspaceRequest` | `WorkspaceSessionHandler` | Create a tracked workspace session and register it by `workspace_id`. |
| `resolve_session` | `WorkspaceId`, `CallerId` | `WorkspaceSessionHandler` | Resolve an existing session and validate caller ownership. |
| `capture_session_changes` | `&WorkspaceSessionHandler`, `CaptureChangesRequest` | `CapturedWorkspaceChanges` | Capture changes and refresh session snapshot metadata. |
| `destroy_session` | `WorkspaceSessionHandler`, `DestroyWorkspaceRequest` | `DestroyWorkspaceResult` | Destroy the session workspace and remove session state on success. |
| `is_remount_pending` | `&WorkspaceId` | `bool` | Read remount-pending state for command admission and stdin guards. |

| Implements | Used By |
|---|---|
| `RemountWorkspaceSession` | `workspace_remount::WorkspaceRemountService` |

| Owns | Must Not Own |
|---|---|
| `workspace_id` keyed open-session state, caller validation, lifecycle coherence. | Command process state or live-remount command coordination. |

### `command::CommandOperationService`

| Operation | Inputs | Output | Responsibility |
|---|---|---|---|
| `new` | `Arc<WorkspaceSessionService>`, `command::CommandConfig` | `CommandOperationService` | Construct command service with default finalization options. |
| `with_finalization_options` | `Arc<WorkspaceSessionService>`, `command::CommandConfig`, `CommandFinalizationOptions` | `CommandOperationService` | Construct command service with explicit one-shot capture/publish policy. |
| `exec_command` | `ExecCommandInput`, `CommandCallContext` | `CommandYield` | Start a command in an existing session or one-shot workspace. |
| `write_stdin` | `WriteStdinInput`, `CommandCallContext` | `CommandYield` | Write stdin to an active command if the workspace is not remount-pending. |
| `read_lines` | `ReadCommandLinesInput`, `CommandCallContext` | `CommandLinesOutput` | Read transcript rows for active or completed command output. |
| `poll` | `PollCommandInput`, `CommandCallContext` | `CommandPollOutput` | Poll command status, finalize completed commands, and return output. |
| `cancel` | `CancelCommandInput`, `CommandCallContext` | `CommandYield` | Request cancellation for an active command. |

| Implements | Used By |
|---|---|
| `CommandRemountCoordinator` | `workspace_remount::WorkspaceRemountService` |

| Owns | Uses | Must Not Own |
|---|---|---|
| Command admission, command ids, command-to-workspace binding, process registry, transcript windows, polling, stdin, cancellation, and finalization policy. | `WorkspaceSessionService` for workspace session resolution, one-shot session lifecycle, capture, destroy, and remount-pending checks. | Raw workspace service calls for session work or the live-remount workflow. |

### `workspace_remount::RemountWorkspaceSession`

| Operation | Inputs | Output | Responsibility |
|---|---|---|---|
| `begin_remount` | `WorkspaceId` | `WorkspaceSessionHandler` | Mark a session remount-pending and return the handler for the switch. |
| `apply_remount` | `&WorkspaceSessionHandler`, `RemountWorkspaceRequest` | `WorkspaceSessionHandler` | Apply raw resource remount and refresh session handle metadata. |
| `finish_remount` | `WorkspaceId` | `()` | Clear remount-pending state after success. |
| `finish_or_block_remount` | `WorkspaceId`, `Option<String>` | `()` | Clear or block remount state after failed/blocked orchestration. |
| `is_remount_pending` | `&WorkspaceId` | `bool` | Report remount-pending state to command admission paths. |

| Implemented By | Purpose |
|---|---|
| `WorkspaceSessionService` | Keeps session remount state behind the remount workflow boundary and calls `workspace::WorkspaceService::remount_workspace` from the session implementation. |

### `workspace_remount::CommandRemountCoordinator`

| Operation | Inputs | Output | Responsibility |
|---|---|---|---|
| `begin_workspace_remount_quiesce` | `&WorkspaceId` | `CommandRemountQuiesce` | Freeze/inspect active commands for remount and provide an opaque quiesce guard. |

| Implemented By | Must Not Expose |
|---|---|
| `CommandOperationService` | `CommandProcessStore`, `CommandRegistry`, or active command records. |

### `workspace_remount::WorkspaceRemountService`

| Operation | Inputs | Output | Responsibility |
|---|---|---|---|
| `new` | `Arc<dyn RemountWorkspaceSession>`, `Arc<dyn CommandRemountCoordinator>`, `WorkspaceRemountOptions` | `WorkspaceRemountService` | Construct remount orchestration service over remount-specific ports. |
| `remount_workspace_session` | `WorkspaceId` | `WorkspaceRemountReport` | Coordinate remount state, command quiesce/resume, raw remount application, and final report. |

| Owns | Must Not Own |
|---|---|
| Full operation-level remount workflow. | Raw mount mechanics or direct command-internal inspection. |

### `OperationServices`

| Operation | Inputs | Output | Responsibility |
|---|---|---|---|
| `new` | `Arc<WorkspaceSessionService>`, `Arc<CommandOperationService>`, `Arc<WorkspaceRemountService>` | `OperationServices` | Compose concrete operation services. |

| Owns | Must Not Own |
|---|---|
| Concrete service wiring only. | Forwarding helpers, session lookup, command lifecycle, or remount orchestration logic. |

## Resource Workspace API

`workspace::WorkspaceService` remains the raw primitive boundary:

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

This API must not grow operation-session methods such as `resolve`,
`begin_remount`, `finish_remount`, `is_remount_pending`, or command lifecycle
helpers. Those are operation-layer concerns.

## Workspace Session API

Rename:

```text
operation_service::workspace_manager::WorkspaceManagerService
```

to:

```text
operation_service::workspace_session::WorkspaceSessionService
```

The target type owns `workspace_id` keyed session coherence:

```rust
pub struct WorkspaceSessionService {
    sessions: Mutex<WorkspaceSessionStore>,
    workspace: Arc<dyn workspace::WorkspaceService>,
}
```

Target public service surface:

```rust
impl WorkspaceSessionService {
    pub fn new(workspace: Arc<dyn workspace::WorkspaceService>) -> Self;

    pub fn create_workspace_session(
        &self,
        request: CreateWorkspaceRequest,
    ) -> Result<WorkspaceSessionHandler, WorkspaceSessionError>;

    pub fn resolve_session(
        &self,
        workspace_id: WorkspaceId,
        caller_id: CallerId,
    ) -> Result<WorkspaceSessionHandler, WorkspaceSessionError>;

    pub fn capture_session_changes(
        &self,
        handler: &WorkspaceSessionHandler,
        request: CaptureChangesRequest,
    ) -> Result<CapturedWorkspaceChanges, WorkspaceSessionError>;

    pub fn destroy_session(
        &self,
        handler: WorkspaceSessionHandler,
        request: DestroyWorkspaceRequest,
    ) -> Result<DestroyWorkspaceResult, WorkspaceSessionError>;

    pub fn is_remount_pending(&self, workspace_id: &WorkspaceId) -> bool;
}
```

Session remount methods should be implemented through
`workspace_remount::RemountWorkspaceSession`, not exposed as the primary
general-purpose session API.

```rust
pub trait RemountWorkspaceSession: Send + Sync {
    fn begin_remount(
        &self,
        workspace_id: WorkspaceId,
    ) -> Result<WorkspaceSessionHandler, WorkspaceSessionError>;

    fn apply_remount(
        &self,
        handler: &WorkspaceSessionHandler,
        request: RemountWorkspaceRequest,
    ) -> Result<WorkspaceSessionHandler, WorkspaceSessionError>;

    fn finish_remount(
        &self,
        workspace_id: WorkspaceId,
    ) -> Result<(), WorkspaceSessionError>;

    fn finish_or_block_remount(
        &self,
        workspace_id: WorkspaceId,
        reason: Option<String>,
    ) -> Result<(), WorkspaceSessionError>;

    fn is_remount_pending(&self, workspace_id: &WorkspaceId) -> bool;
}
```

`WorkspaceSessionService` implements this trait by calling
`workspace::WorkspaceService::remount_workspace` during `apply_remount`.

## Command Workspace Use

Command workflow should depend directly on `WorkspaceSessionService`:

```rust
pub struct CommandOperationService {
    workspace: Arc<WorkspaceSessionService>,
    config: command::CommandConfig,
    registry: Arc<CommandRegistry>,
    process_store: Arc<CommandProcessStore>,
    finalization_options: CommandFinalizationOptions,
}
```

Command behavior stays the same:

- `workspace_id: Some` resolves an existing session and does not destroy or
  publish that session on command completion.
- `workspace_id: None` creates a private one-shot host-compatible workspace,
  runs the command, captures changes, publishes or discards according to
  command finalization policy, then destroys the one-shot workspace. The
  command service builds the `CreateWorkspaceRequest` and calls
  `WorkspaceSessionService::create_workspace_session`.
- session command resolution calls `WorkspaceSessionService::resolve_session`.
- one-shot capture calls `WorkspaceSessionService::capture_session_changes`.
- one-shot cleanup calls `WorkspaceSessionService::destroy_session`.
- starts and stdin writes reject while the workspace is remount-pending.
- poll and read-lines remain allowed while remount is pending.

## Workspace Remount Ownership

`workspace_remount` owns all operation-level remount coordination contracts:

```text
workspace_remount/
  service.rs
  command_quiesce.rs
  command_port.rs
  workspace_port.rs
  error.rs
```

`command_quiesce.rs` owns remount-specific DTOs:

```rust
pub struct CommandRemountInspection {
    pub active_command_count: usize,
    pub blocked_reason: Option<String>,
}

pub enum RemountSwitchState {
    NotStarted,
    CriticalSwitch,
    Resuming,
}

pub struct CommandRemountQuiesce {
    // opaque to WorkspaceRemountService except through methods
}
```

`command_port.rs` owns the command-side remount port:

```rust
pub trait CommandRemountCoordinator: Send + Sync {
    fn begin_workspace_remount_quiesce(
        &self,
        workspace_id: &WorkspaceId,
    ) -> CommandRemountQuiesce;
}
```

`CommandOperationService` implements `CommandRemountCoordinator`, but
`workspace_remount` must not inspect `CommandRegistry`, `CommandProcessStore`,
or active command records directly.

`workspace_port.rs` owns `RemountWorkspaceSession`, described above.

`WorkspaceRemountService` target shape:

```rust
pub struct WorkspaceRemountService {
    workspace: Arc<dyn RemountWorkspaceSession>,
    command: Arc<dyn CommandRemountCoordinator>,
    options: WorkspaceRemountOptions,
}

impl WorkspaceRemountService {
    pub fn remount_workspace_session(
        &self,
        workspace_id: WorkspaceId,
    ) -> Result<WorkspaceRemountReport, WorkspaceRemountError>;
}
```

Remount flow:

```text
WorkspaceRemountService::remount_workspace_session(workspace_id)
  -> workspace.begin_remount(workspace_id)
  -> command.begin_workspace_remount_quiesce(workspace_id)
  -> if command inspection blocks:
       workspace.finish_or_block_remount(workspace_id, Some(reason))
       return report(remounted=false)
  -> command quiesce enters critical switch
  -> workspace.apply_remount(handler, RemountWorkspaceRequest)
       -> WorkspaceSessionService calls workspace::WorkspaceService::remount_workspace
  -> command quiesce enters resuming
  -> workspace.finish_remount(workspace_id)
  -> command quiesce resumes on drop/finish
  -> return report(remounted=true)
```

## OperationServices Composition

The composition root wires concrete services to ports:

```rust
pub struct OperationServices {
    pub workspace: Arc<WorkspaceSessionService>,
    pub command: Arc<CommandOperationService>,
    pub remount: Arc<WorkspaceRemountService>,
}
```

Construction order:

```text
raw_workspace: Arc<dyn workspace::WorkspaceService>
  -> WorkspaceSessionService
  -> CommandOperationService using Arc<WorkspaceSessionService>
  -> WorkspaceRemountService using:
       Arc<dyn RemountWorkspaceSession>
       Arc<dyn CommandRemountCoordinator>
```

`OperationServices` is composition only. Dispatch should call
`services.command.*` and `services.remount.*` directly instead of adding
forwarding helpers on `OperationServices`.

## Migration Steps

### Step 1: Add New Names As Aliases

- Add `workspace_session/` with the same implementation currently under
  `workspace_manager/`.
- Re-export temporary compatibility aliases:

```rust
pub use workspace_session::WorkspaceSessionService;
pub type WorkspaceManagerService = WorkspaceSessionService;
```

- Keep existing tests passing before removing old paths.

### Step 2: Split Session Model Files

- Move session data types out of `session_manager.rs`:
  - `model.rs`: `WorkspaceSession`, `WorkspaceSessionHandler`,
    `WorkspaceRemountState`, lifecycle enums.
  - `session_store.rs`: `WorkspaceSessionStore` map and lookup helpers.
  - `service.rs`: `WorkspaceSessionService` struct, constructor/accessors, and
    `mod impls;`.
  - `service/impls/*.rs`: one public operation method per file.
- Rename `WorkspaceManagerError` to `WorkspaceSessionError` with a temporary
  type alias during transition.

### Step 3: Wire Command Directly To Workspace Session

- Change `CommandOperationService` constructors to accept
  `Arc<WorkspaceSessionService>`.
- Remove any top-level `ports/command_workspace.rs` plan or implementation.
- In command execution with `workspace_id: Some`, call
  `WorkspaceSessionService::resolve_session`.
- In command execution with `workspace_id: None`, build a
  `CreateWorkspaceRequest` and call
  `WorkspaceSessionService::create_workspace_session`.
- In one-shot command finalization, call
  `WorkspaceSessionService::capture_session_changes` and
  `WorkspaceSessionService::destroy_session`.
- For remount-pending checks, call
  `WorkspaceSessionService::is_remount_pending`; do not add a
  command-specific workspace port.
- Keep test constructors ergonomic with helpers that build a
  `WorkspaceSessionService` from fake raw workspace services.

### Step 4: Move Remount Command Coordination

- Move `command/remount.rs` to `workspace_remount/command_quiesce.rs` where
  the model is remount-specific.
- Add `workspace_remount/command_port.rs`.
- Implement `CommandRemountCoordinator` for `CommandOperationService`.
- Ensure `workspace_remount` does not import command process-store internals.

### Step 5: Move Remount Session Port

- Add `workspace_remount/workspace_port.rs`.
- Implement `RemountWorkspaceSession` for `WorkspaceSessionService`.
- Change `WorkspaceRemountService` to store trait objects for
  `RemountWorkspaceSession` and `CommandRemountCoordinator`.
- Remove direct concrete dependency on `WorkspaceSessionService` from
  `workspace_remount/service.rs` except in tests or constructors where concrete
  wiring is intentional.

### Step 6: Remove Old Module Names

- Remove `workspace_manager/` after call sites use `workspace_session/`.
- Remove `command/remount.rs`.
- Remove temporary type aliases.
- Update docs and tests:
  - `workspace_manager` -> `workspace_session`
  - `WorkspaceManagerService` -> `WorkspaceSessionService`
  - `WorkspaceManagerError` -> `WorkspaceSessionError`
  - remount tests should import remount DTOs and ports through
    `workspace_remount`.

### Step 7: Add Dependency Guards

Add focused tests or grep gates that fail on forbidden imports:

```sh
rg -n "workspace_manager|WorkspaceManagerService|WorkspaceManagerError" \
  crates/daemon/operation_service/src crates/daemon/operation_service/tests

rg -n "command::remount|ports/command_workspace|ports::command_workspace|ports::command_remount|ports::remount_workspace|ports::file_workspace|operation_service/src/ports|operation_service/src/file" \
  crates/daemon/operation_service/src crates/daemon/operation_service/tests

rg -n "CommandProcessStore|ActiveCommandProcess|CommandRegistry" \
  crates/daemon/operation_service/src/workspace_remount

rg -n "workspace::WorkspaceService|workspace_crate::WorkspaceService" \
  crates/daemon/operation_service/src/command
```

Expected results:

- first command returns no old `workspace_manager` names after aliases are
  removed.
- second command returns no removed remount module paths and no file-service
  target paths in this phase.
- third command returns no command internals under `workspace_remount`.
- fourth command returns no raw workspace service dependency in command service
  implementation.

## Test Plan

Run focused tests after each structural step:

```sh
CARGO_TARGET_DIR=/tmp/eos-phase3-structure-target cargo test -p workspace
CARGO_TARGET_DIR=/tmp/eos-phase3-structure-target cargo test -p operation_service workspace_session
CARGO_TARGET_DIR=/tmp/eos-phase3-structure-target cargo test -p operation_service command_exec
CARGO_TARGET_DIR=/tmp/eos-phase3-structure-target cargo test -p operation_service command_ownership
CARGO_TARGET_DIR=/tmp/eos-phase3-structure-target cargo test -p operation_service command_remount
CARGO_TARGET_DIR=/tmp/eos-phase3-structure-target cargo test -p operation_service workspace_remount
CARGO_TARGET_DIR=/tmp/eos-phase3-structure-target cargo test -p operation_service service_graph
```

Final gate:

```sh
CARGO_TARGET_DIR=/tmp/eos-phase3-structure-target cargo check -p operation_service --all-targets
CARGO_TARGET_DIR=/tmp/eos-phase3-structure-target cargo check -p daemon --all-targets
cargo fmt --check
```

If local platform limitations block Linux-only command/workspace tests, run the
same commands with the target already used by this repo's existing test
workflow and record the exact environment limitation in the implementation
record.

## Acceptance Criteria

- `workspace::WorkspaceService` still exposes `remount_workspace`.
- No raw workspace resource mechanics move into `operation_service`.
- No file service folder or file-workspace port is introduced by this phase.
- `workspace_manager/` is gone or only exists as a temporary compatibility
  alias during a single migration step.
- `WorkspaceSessionService` owns session lookup, caller validation, capture
  metadata refresh, destroy rollback, and remount state.
- `CommandOperationService` depends directly on `WorkspaceSessionService`, not
  raw `workspace::WorkspaceService` and not a command-workspace port.
- `workspace_remount/` contains the remount-specific command quiesce model and
  both remount ports.
- There is no `ports/command_remount.rs`.
- `WorkspaceRemountService` does not import or mutate command process-store
  internals.
- Existing command remount and workspace remount tests still pass.
- Dependency guard greps return only allowed temporary aliases until the final
  cleanup step, then return no forbidden imports.

## Rollback

This migration is mostly moves and renames. Each step should keep type aliases
or re-exports until call sites are updated.

Rollback by step:

- If the `workspace_session` rename destabilizes too many call sites, keep the
  new module and restore aliases temporarily; do not reintroduce new behavior
  into `workspace_manager`.
- If trait-object wiring introduces object-safety or test complexity, keep
  concrete constructors and add trait-backed constructors second.
- If moving `command/remount.rs` creates cycles, move only the DTOs first,
  introduce `CommandRemountCoordinator`, and then move implementation helpers
  behind the trait in a later patch.
- Do not roll back by making `workspace_remount` inspect command internals.
  That violates the final ownership boundary.

## Open Questions

- Should `WorkspaceSessionService` expose remount methods directly in addition
  to implementing `RemountWorkspaceSession`? The preferred final shape is trait
  implementation only for remount-specific callers, with direct methods kept
  private or `pub(crate)` where possible.
