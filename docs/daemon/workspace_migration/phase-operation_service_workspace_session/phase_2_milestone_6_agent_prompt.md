# Phase 2 Milestone 6 Agent Prompt

You are implementing Phase 2 Milestone 6 only in:

`/Users/yifanxu/machine_learning/LoVC/ephemeral-ai/ephemeral-os`

Milestone 6 is: move live remount orchestration into
`WorkspaceRemountService`, with remount-pending session state owned by
`WorkspaceManagerService` and command quiesce/resume/inspection owned by
`CommandOperationService`.

## Read First

Before editing code, read these files:

- `docs/daemon/workspace_migration/phase-operation_service_workspace_session/phase_2_command_service_SPEC.md`
- `docs/daemon/workspace_migration/phase-operation_service_workspace_session/phase_2_command_service_IMPLEMENTATION_PLAN.md`
- `docs/daemon/workspace_migration/phase-operation_service_workspace_session/phase_2_implementation_record.md`
- `crates/daemon/operation_service/src/workspace_manager/session_manager.rs`
- `crates/daemon/operation_service/src/workspace_manager/service.rs`
- `crates/daemon/operation_service/src/workspace_manager/error.rs`
- `crates/daemon/operation_service/src/command/service.rs`
- `crates/daemon/operation_service/src/command/registry.rs`
- `crates/daemon/operation_service/src/command/process_store.rs`
- `crates/daemon/operation_service/src/workspace_remount/service.rs`
- `crates/daemon/operation/src/command/service/remount.rs`
- `crates/daemon/core/src/runtime/workspace.rs` only for old sequencing evidence.

Treat Milestones 3.5, 4, and 5 as completed adjacent work. Do not redesign
command launch, finalization, or row projection.

## Scope

Implement only:

- Workspace remount-pending state and workspace-owned resource remount methods.
- Command-side workspace-id based quiesce, resume, and `/proc` inspection.
- Cross-service remount orchestration in `WorkspaceRemountService`.
- Command start/stdin rejection while remount is pending.
- Read/poll remaining allowed during remount pending.
- Focused operation-service tests and implementation-record updates.

## Do Not Implement

- No daemon dispatch migration away from `WorkspaceRuntime`; that is Milestone 7.
- No queueing of commands during remount pending.
- No parent-prefix production compaction policy.
- No unsafe fallback that deletes mounted lowerdirs after blocked inspection.
- No per-command `remountable` flag or remount opt-in.
- No public command-service collect/count/advance APIs.
- No command-service imports from `workspace_manager`.
- No broad cleanup of legacy `operation::command` or `WorkspaceRuntime` code
  beyond reading them as evidence.

## Target Structure

Expected operation-service structure after this milestone:

```text
crates/daemon/operation_service/src/
  workspace_manager/
    session_manager.rs   # WorkspaceRemountState plus session state transitions
    service.rs           # begin/apply/finish/block remount methods
    error.rs             # workspace remount state errors

  command/
    mod.rs
    service.rs           # pending guards plus crate-private remount helpers
    remount.rs           # command quiesce/resume/inspection

  workspace_remount/
    mod.rs
    service.rs           # cross-service remount orchestration
    error.rs             # only if real remount errors are introduced
```

Optional lower-level helper:

```text
crates/daemon/command/src/quiesce.rs
```

Only create this if the helper is policy-free: process-group freeze/resume,
`/proc` parsing, and path inspection with no workspace-service, command-registry,
or remount-policy knowledge.

## Implementation Sequence

### 0. Open The Record

Before code changes, append a Milestone 6 entry to
`phase_2_implementation_record.md` with:

- status: in progress
- intended files
- carried-forward notes from Milestones 4 and 5
- explicit note that daemon dispatch migration remains M7

### 1. Workspace Remount State

Add workspace-owned remount state without command-service imports:

```rust
pub enum WorkspaceRemountState {
    Active,
    RemountPending,
    RemountBlocked { reason: String },
}
```

Add it to `WorkspaceSession`, defaulting new sessions to `Active`.

Add session-manager/service methods:

- `begin_remount(workspace_id) -> WorkspaceSessionHandler`
- `apply_remount(handler, RemountWorkspaceRequest) -> WorkspaceSessionHandler`
- `finish_remount(workspace_id)`
- `finish_or_block_remount(workspace_id, reason)`
- `is_remount_pending(workspace_id) -> bool`

Rules:

- `begin_remount` rejects not found, closing, and already pending sessions.
- `apply_remount` calls only the resource-facing workspace remount primitive and
  refreshes the canonical session handle.
- failed resource remount keeps the session available and allows
  `finish_or_block_remount` to retain a blocked reason.
- `resolve`, `capture_changes`, and `destroy` should continue to respect active
  versus closing state. Do not let remount pending become a hidden closing state.

Tests:

- pending state is visible after begin
- duplicate begin is rejected
- finish returns to active
- blocked reason can be retained
- apply remount refreshes the canonical handle
- workspace manager files still have no command-service imports

### 2. Command Remount Types And Workspace Scan

Create `operation_service/src/command/remount.rs` with command-side types:

```rust
pub struct CommandRemountInspection { ... }
pub struct CommandRemountQuiesce { ... }
pub enum RemountSwitchState { Quiescing, ReadyToSwitch, CriticalSwitch, Resuming, Finished }
```

Use the Phase 2 plan's `CommandRemountInspection` shape, but do not include
`remountable_commands`.

Add crate-private command service methods:

- `begin_workspace_remount_quiesce(workspace_id) -> CommandRemountQuiesce`
- `inspect_workspace_remount(workspace_id) -> CommandRemountInspection`

Rules:

- lookup is by `CommandRegistry::commands_for_workspace(workspace_id)`
- every active command for that workspace is quiesce eligible
- unknown process group or unknown `/proc` state blocks remount
- failed inspection resumes any process group already stopped
- `CommandRemountQuiesce` resumes on explicit finish and in `Drop`
- keep this API crate-private unless a public operation-service API requires it

Start by porting the policy-free parts of
`operation/src/command/service/remount.rs`, then delete or adapt:

- caller-based lookup
- `ActiveCommand::IsolatedNetwork` matching
- `remountable` checks
- `session_not_marked_remountable`

Tests:

- workspace scan finds multiple active command ids
- no active commands yields an unblocked no-op inspection
- unavailable process group blocks
- blocked inspection resumes stopped groups
- `Drop` resumes held groups
- Linux `/proc` parsing helpers preserve existing parser coverage

### 3. Pending Guards For Commands

Add guards in `CommandOperationService`:

- `exec_command` rejects when the target workspace is remount pending
- `write_stdin` rejects when the command's bound workspace is remount pending
- `read_lines` remains allowed
- `poll` remains allowed and may finalize completed processes

Use a retryable command error variant such as `WorkspaceRemountPending`.

Rules:

- for persistent session exec, resolve canonical workspace state before launch
- for one-shot exec, the newly created workspace should not observe pending
  state before insertion, but cleanup paths must still work
- wrong caller errors must stay authorization errors, not pending-state leaks

Tests:

- start rejects for pending persistent workspace
- stdin rejects for active command whose workspace becomes pending
- read_lines and poll still work during pending
- wrong caller cannot use pending-state responses to learn command/workspace
  facts

### 4. Cancellation And Switch Guard

Introduce the minimum cancellation/switch state needed to avoid killing stopped
process groups before they are resumed.

Implement:

- a remount cancellation token shared between quiesce and cancel paths
- `RemountSwitchState` updates in the remount orchestration path
- cancel behavior that records cancellation while quiesced, then terminates only
  after resume or after the switch has safely left the stopped-process phase

Rules:

- a command already stopped for remount must not be killed before required
  resume
- cancellation before `CriticalSwitch` should abort or block the remount attempt
  cleanly
- cancellation during `CriticalSwitch` should wait until resume is safe

Tests:

- cancel before critical switch resumes before termination
- cancel during critical switch does not kill a stopped process group
- failed remount with pending cancel still resumes

### 5. WorkspaceRemountService Orchestration

Implement the cross-service method in
`operation_service/src/workspace_remount/service.rs`:

- `compact_or_remount_session(workspace_id)`

Recommended sequence:

1. `WorkspaceManagerService::begin_remount(workspace_id)`
2. `CommandOperationService::begin_workspace_remount_quiesce(workspace_id)`
3. if inspection blocks, finish/block workspace state and return blocked report
4. enter critical switch state
5. call `WorkspaceManagerService::apply_remount(handler, request)`
6. verify remount result and refresh session metadata
7. finish workspace remount state
8. resume command process groups
9. return remount report

Rules:

- workspace state transitions belong to `WorkspaceManagerService`
- command quiesce/resume/inspection belongs to `CommandOperationService`
- only `WorkspaceRemountService` should hold both services for orchestration
- lease retarget and lowerdir cleanup must not happen before mount verification
  and lease retarget success
- every early return must finish/block workspace state and resume quiesced
  process groups

Tests:

- no active command path succeeds
- live command success path quiesces, remounts, resumes, and clears pending
- blocked inspection marks/remembers blocked state and resumes
- resource remount failure resumes and clears/blocks state
- cancellation race resumes before termination

### 6. Cleanup, Static Checks, And Record Closeout

Before marking Milestone 6 complete:

- update `phase_2_implementation_record.md` with files changed, verification,
  deviations, unresolved issues, and Milestone 7 handoff
- remove placeholder methods or dead-code allowances added during the milestone
- run static searches for forbidden vocabulary in new operation-service code

Suggested static search:

```text
rg -n "begin_live_remount_for_caller|inspect_live_remount_for_caller|remountable|remountable_commands|session_not_marked_remountable|WorkspaceRuntime|CommandOps|operation::command|collect_completed|count_by_caller|advance_active_commands_once" crates/daemon/operation_service/src crates/daemon/operation_service/tests
```

Document any expected false positives.

## Tests And Verification

Run and record:

```text
CARGO_TARGET_DIR=/tmp/eos-phase2-command-service-target cargo test -p operation_service workspace_remount
CARGO_TARGET_DIR=/tmp/eos-phase2-command-service-target cargo test -p operation_service command_remount
CARGO_TARGET_DIR=/tmp/eos-phase2-command-service-target cargo test -p operation_service command_ownership
CARGO_TARGET_DIR=/tmp/eos-phase2-command-service-target cargo test -p operation_service command_exec
CARGO_TARGET_DIR=/tmp/eos-phase2-command-service-target cargo test -p operation_service
CARGO_TARGET_DIR=/tmp/eos-phase2-command-service-target cargo check -p operation_service
CARGO_TARGET_DIR=/tmp/eos-phase2-command-service-target cargo check -p command
cargo fmt --check
git diff --check
```

## Completion Criteria

Milestone 6 is complete only when:

- `WorkspaceManagerService` owns remount-pending state and resource remount
  application with no command-service imports.
- `CommandOperationService` owns command-side quiesce/resume/inspection and
  scans `CommandRegistry` by workspace id.
- every active command is remount-quiesce eligible; there is no per-command
  remount opt-in.
- unknown inspection state blocks remount.
- stopped process groups resume on success, failure, early return, drop, and
  cancel paths.
- command starts and stdin reject while the workspace is remount pending.
- read/poll remain allowed during remount pending.
- cancellation never kills a stopped process group before required resume.
- `WorkspaceRemountService` is the only orchestration owner that coordinates
  workspace state and command quiesce.
- focused tests and full operation-service verification pass.
