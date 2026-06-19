# Phase 2 Milestone 6.5 Exec Command Boundary Spec

Date: 2026-06-19
Parent plan: `phase_2_command_service_IMPLEMENTATION_PLAN.md`

## Summary

Milestone 6.5 moves the public exec command boundary from
`OperationServices::exec_command` to `CommandOperationService::exec_command`.
This is a narrow boundary migration between Milestone 6 remount work and
Milestone 7 daemon dispatch migration.

The target is:

```rust
impl CommandOperationService {
    pub fn exec_command(
        &self,
        input: ExecCommandInput,
        context: CommandCallContext,
    ) -> Result<CommandYield, CommandServiceError>;
}
```

`OperationServices::exec_command` may remain during Milestone 6.5 only as a
temporary forwarding shim. It must not own workspace resolution or command-start
policy after this milestone.

## Motivation

Milestone 7 wants daemon command dispatch to route exec, stdin, read, poll, and
cancel through one command-service boundary:

```rust
runtime_services.operation.command.exec_command(...)
runtime_services.operation.command.write_stdin(...)
runtime_services.operation.command.read_lines(...)
runtime_services.operation.command.poll(...)
runtime_services.operation.command.cancel(...)
```

Today exec is the outlier: public callers use `OperationServices::exec_command`,
while stdin/read/poll/cancel are public methods on `CommandOperationService`.
That split makes Phase 7 daemon dispatch less regular and leaves
`OperationServices` with command-start policy that belongs to command service.

`CommandOperationService` already stores `Arc<WorkspaceManagerService>`, so it
can resolve `workspace_id` and create one-shot workspaces without adding a new
dependency direction.

## Current State

- `OperationServices` exposes `workspace`, `command`, and `remount`.
- `OperationServices::exec_command(input, trace)` currently:
  - clones `input.caller_id`;
  - resolves `input.workspace_id` through `WorkspaceManagerService`;
  - calls crate-private `CommandOperationService::exec_command(input, workspace, context)`.
- `CommandOperationService` already owns:
  - `Arc<WorkspaceManagerService>`;
  - command registry;
  - process store;
  - launch driver;
  - remount admission lock;
  - finalization policy.
- The current crate-private command exec implementation already re-resolves the
  canonical workspace handler and validates `workspace_root`, so the
  `Option<WorkspaceSessionHandler>` argument is no longer a useful public seam.

## Goals

- Make `CommandOperationService::exec_command(input, context)` the public exec
  boundary.
- Keep `ExecCommandInput` unchanged:
  - `workspace_root` remains the only root path;
  - `workspace_id` remains optional;
  - no request id, trace id, invocation id, or `remountable` field is added.
- Keep `CommandCallContext` as the source of request trace context and caller
  authorization context.
- Keep all workspace resolution and one-shot creation behind command service's
  existing `WorkspaceManagerService` dependency.
- Keep stdin/read/poll/cancel command-id based.
- Keep `WorkspaceManagerService` free of command-service imports.
- Preserve Milestone 6 remount-pending behavior:
  - session command starts reject while remount is pending;
  - stdin rejects while remount is pending;
  - read and poll remain allowed;
  - wrong callers still receive authorization errors before pending-state leaks.

## Non-Goals

- No daemon dispatch migration. That remains Milestone 7.
- No deletion of old `operation::command` or `WorkspaceRuntime` compatibility.
  That remains Milestone 7 or Milestone 8 depending on ownership.
- No public `CommandOperationService::exec_command` overload that accepts
  `Option<WorkspaceSessionHandler>`.
- No new command-service collect/count/advance APIs.
- No protocol catalog rename.
- No wire-schema change.
- No request correlation fields in `ExecCommandInput`.
- No broad test rewrite when a forwarding shim can preserve existing call sites.

## Target API Contract

### Public Command Exec

```rust
impl CommandOperationService {
    pub fn exec_command(
        &self,
        input: ExecCommandInput,
        context: CommandCallContext,
    ) -> Result<CommandYield, CommandServiceError>;
}
```

Rules:

- Reject empty `input.cmd` before workspace creation or command allocation.
- Reject `input.caller_id != context.caller_id` before workspace creation or
  command allocation.
- If `input.workspace_id` is `Some(workspace_id)`:
  - resolve through `self.workspace().resolve(workspace_id, context.caller_id)`;
  - validate the resolved handler's `workspace_root` matches `input.workspace_root`;
  - treat the command as a persistent-session command;
  - do not create, publish, or destroy the workspace as part of command start or
    session command finalization.
- If `input.workspace_id` is `None`:
  - create a private one-shot host workspace through `WorkspaceManagerService`;
  - treat the command as a one-shot command;
  - preserve existing one-shot finalization policy.
- For session commands, hold the remount admission guard across the pending check
  and command active-record insertion, preserving Milestone 6 race semantics.
- Return existing `CommandServiceError` variants. Do not add a wrapper error just
  for the moved boundary.

### Internal Exec Helper

The old handler-taking implementation should become private and should be named
so it cannot be confused with the public boundary, for example:

```rust
fn exec_resolved_command(
    &self,
    input: ExecCommandInput,
    mode: ExecCommandMode,
    context: CommandCallContext,
) -> Result<CommandYield, CommandServiceError>;
```

Possible internal mode:

```rust
enum ExecCommandMode {
    Session { handler: WorkspaceSessionHandler },
    OneShot,
}
```

Alternative acceptable internal shape: keep a private helper that accepts
`Option<WorkspaceSessionHandler>`, but it must not be `pub` or `pub(crate)`.
Only the public `exec_command(input, context)` method may be used outside the
`command::exec` module.

### Temporary OperationServices Shim

During Milestone 6.5, this shim may remain:

```rust
impl OperationServices {
    pub fn exec_command(
        &self,
        input: ExecCommandInput,
        trace: OperationTraceContext,
    ) -> Result<CommandYield, CommandServiceError> {
        let caller_id = input.caller_id.clone();
        self.command.exec_command(input, CommandCallContext { caller_id, trace })
    }
}
```

Rules:

- The shim must not resolve `workspace_id`.
- The shim must not inspect workspace state.
- The shim must not make command-start policy decisions.
- The shim exists only to reduce call-site churn before Milestone 7.
- Milestone 8 should remove the shim unless a concrete external API reason is
  recorded.

## Expected Files To Change

- `crates/daemon/operation_service/src/command/exec.rs`
- `crates/daemon/operation_service/src/services.rs`
- `crates/daemon/operation_service/tests/command_exec.rs`
- `crates/daemon/operation_service/tests/command_ownership.rs`
- `crates/daemon/operation_service/tests/command_remount.rs`
- `crates/daemon/operation_service/tests/workspace_remount.rs`
- `crates/daemon/operation_service/tests/command_transcript_rows.rs`
- `crates/daemon/operation_service/src/command/finalize_tests.rs`
- `crates/daemon/operation_service/tests/support/mod.rs`
- `docs/daemon/workspace_migration/phase-operation_service_workspace_session/phase_2_command_service_IMPLEMENTATION_PLAN.md`
- `docs/daemon/workspace_migration/phase-operation_service_workspace_session/phase_2_implementation_record.md`

Only tests that need to prove the new public boundary should move from
`env.services.exec_command(...)` to `env.command.exec_command(...)`. Tests that
are intentionally proving backward-compatible shim behavior may keep calling
`OperationServices::exec_command`.

## Migration Steps

1. Open the implementation record.
   - Add a `Milestone 6.5: Exec Command Boundary Migration` entry.
   - Carry forward that M7 still owns daemon dispatch migration.

2. Add public command exec.
   - Change command exec's public entrypoint to
     `pub fn exec_command(input, context)`.
   - Move workspace resolution from `OperationServices::exec_command` into the
     command service entrypoint.
   - Keep or create a private helper for already-resolved mode selection.

3. Reduce `OperationServices::exec_command`.
   - Keep a shim only if it forwards to `self.command.exec_command`.
   - Remove workspace resolution and policy from the shim.

4. Update tests.
   - Add or update tests proving:
     - `CommandOperationService::exec_command` with `workspace_id: Some` resolves
       the workspace and rejects root mismatch before command allocation;
     - `CommandOperationService::exec_command` with `workspace_id: None` creates
       a one-shot host workspace;
     - caller mismatch rejects before workspace creation;
     - remount-pending session start still rejects;
     - wrong caller behavior remains authorization-first.
   - Keep one explicit shim test if `OperationServices::exec_command` remains.

5. Update docs.
   - Update the implementation plan so Milestone 7 daemon dispatch calls
     `RuntimeServices.operation.command.exec_command(...)`.
   - Replace stale notes saying external callers should continue to use
     `OperationServices::exec_command`.
   - Record the temporary shim as intentionally retained, if retained.

## Verification

Run:

```text
CARGO_TARGET_DIR=/tmp/eos-phase2-command-service-target cargo test -p operation_service command_exec
CARGO_TARGET_DIR=/tmp/eos-phase2-command-service-target cargo test -p operation_service command_ownership
CARGO_TARGET_DIR=/tmp/eos-phase2-command-service-target cargo test -p operation_service command_remount
CARGO_TARGET_DIR=/tmp/eos-phase2-command-service-target cargo test -p operation_service workspace_remount
CARGO_TARGET_DIR=/tmp/eos-phase2-command-service-target cargo test -p operation_service
CARGO_TARGET_DIR=/tmp/eos-phase2-command-service-target cargo clippy -p operation_service --all-targets --no-deps -- -D warnings
CARGO_TARGET_DIR=/tmp/eos-phase2-command-service-target cargo check -p operation_service
cargo fmt --check
git diff --check
```

Static checks:

```text
rg -n "pub\\(crate\\) fn exec_command\\(|Option<WorkspaceSessionHandler>" crates/daemon/operation_service/src/command
rg -n "self\\.workspace\\.resolve|WorkspaceManagerService::resolve" crates/daemon/operation_service/src/services.rs
rg -n "OperationServices::exec_command remains public|External callers should continue to use OperationServices::exec_command" docs/daemon/workspace_migration/phase-operation_service_workspace_session
rg -n "request_id|trace_id|invocation_id|remountable|layer_stack_root" crates/daemon/operation_service/src/command
```

Expected static-check interpretation:

- No public or crate-public handler-taking command exec remains.
- `services.rs` contains no workspace resolution in `exec_command`.
- Any retained docs mention `OperationServices::exec_command` only as a
  temporary shim with an explicit removal target.
- Existing test references to low-level runner JSON fields may remain only where
  already documented as protocol false positives.

## Acceptance Criteria

- `CommandOperationService::exec_command(input, context)` is public and is the
  command-service exec boundary.
- No public or crate-public command exec accepts `Option<WorkspaceSessionHandler>`.
- `OperationServices::exec_command`, if retained, is a thin forwarding shim only.
- Workspace resolution for exec lives in command service, using the command
  service's existing `WorkspaceManagerService` dependency.
- Session exec, one-shot exec, remount-pending rejection, root mismatch rejection,
  and caller mismatch behavior remain covered by focused tests.
- Stdin/read/poll/cancel public signatures are unchanged.
- No daemon dispatch migration is included.
- The implementation record names the shim as temporary or states it was removed.
- Milestone 7 plan text points daemon exec dispatch at
  `RuntimeServices.operation.command.exec_command(...)`.

## Risks And Rollback

- Risk: bulk test churn obscures the boundary change.
  - Mitigation: keep `OperationServices::exec_command` as a shim and update only
    tests that prove the new public command-service boundary.
- Risk: exposing command exec reintroduces trust in caller-provided workspace
  handlers.
  - Mitigation: public exec must not accept `WorkspaceSessionHandler`; resolution
    happens inside command service.
- Risk: `OperationServices` stops being useful as a convenience facade for old
  callers before daemon dispatch migrates.
  - Mitigation: keep the forwarding shim through Milestone 7 and remove or
    justify it in Milestone 8.
- Risk: remount admission race behavior changes while moving code.
  - Mitigation: keep the admission guard and pending check in the same relative
    sequence and rerun command-remount tests.

## Milestone 7 Handoff

After Milestone 6.5, Milestone 7 should use this daemon exec routing:

```rust
runtime_services
    .operation
    .command
    .exec_command(exec_input, command_call_context)
```

Milestone 7 should not call `OperationServices::exec_command` except during a
temporary compatibility interval that is explicitly recorded in the implementation
record.
