# Phase 2 Milestone 6.5 Agent Prompt

You are implementing Phase 2 Milestone 6.5 only in:

`/Users/yifanxu/machine_learning/LoVC/ephemeral-ai/ephemeral-os`

Milestone 6.5 is: move the public exec command boundary from
`OperationServices::exec_command` to
`CommandOperationService::exec_command(input, context)`, while keeping daemon
dispatch migration out of scope.

## First Rule

Inspect the live repo before editing. The worktree may already contain unrelated
changes. Do not revert, overwrite, or cleanup changes outside this milestone.

## Read First

Before code changes, read these files:

- `docs/daemon/workspace_migration/phase-operation_service_workspace_session/phase_2_milestone_6_5_exec_command_boundary_SPEC.md`
- `docs/daemon/workspace_migration/phase-operation_service_workspace_session/phase_2_command_service_IMPLEMENTATION_PLAN.md`
- `docs/daemon/workspace_migration/phase-operation_service_workspace_session/phase_2_implementation_record.md`
- `crates/daemon/operation_service/src/services.rs`
- `crates/daemon/operation_service/src/command/exec.rs`
- `crates/daemon/operation_service/src/command/service.rs`
- `crates/daemon/operation_service/src/command/contract.rs`
- `crates/daemon/operation_service/tests/command_exec.rs`
- `crates/daemon/operation_service/tests/command_ownership.rs`
- `crates/daemon/operation_service/tests/command_remount.rs`
- `crates/daemon/operation_service/tests/workspace_remount.rs`
- `crates/daemon/operation_service/tests/support/mod.rs`

Treat Milestone 6 as completed adjacent work. Preserve the remount-pending
behavior landed there.

## Scope

Implement only:

- Public `CommandOperationService::exec_command(input, context)`.
- Workspace resolution for exec inside command service.
- One-shot workspace creation still inside command service.
- `OperationServices::exec_command` reduced to a temporary forwarding shim, or
  removed only if all current callers are migrated without widening scope.
- Focused operation-service tests for the new public command-service exec
  boundary.
- Implementation-plan and implementation-record updates for Milestone 6.5.

## Do Not Implement

- No daemon dispatch migration away from `WorkspaceRuntime`; that is Milestone 7.
- No deletion of old `operation::command`, `CommandOps`, or `WorkspaceRuntime`
  compatibility; that is Milestone 7 or Milestone 8.
- No public or crate-public exec method that accepts
  `Option<WorkspaceSessionHandler>`.
- No public command-service collect/count/advance APIs.
- No protocol catalog rename.
- No wire schema change.
- No request id, trace id, invocation id, `layer_stack_root`, or `remountable`
  field added to `ExecCommandInput`.
- No broad test rewrite when a forwarding shim can preserve stable call sites.

## Target API

Make this the public command-service exec boundary:

```rust
impl CommandOperationService {
    pub fn exec_command(
        &self,
        input: ExecCommandInput,
        context: CommandCallContext,
    ) -> Result<CommandYield, CommandServiceError>;
}
```

The public method must:

- reject empty `input.cmd` before workspace creation or command allocation;
- reject `input.caller_id != context.caller_id` before workspace creation or
  command allocation;
- when `input.workspace_id` is `Some(workspace_id)`, resolve the workspace
  through `self.workspace().resolve(workspace_id, context.caller_id.clone())`;
- validate the resolved handler's `workspace_root` against
  `input.workspace_root`;
- treat `workspace_id: Some` as a persistent-session command;
- treat `workspace_id: None` as a one-shot host command;
- preserve one-shot finalization policy;
- preserve persistent-session finalization semantics;
- preserve remount admission and pending-state race behavior from Milestone 6.

## Internal Helper

Do not expose the current handler-taking exec signature. Convert it into a
private helper or replace it with a private mode-based helper.

Acceptable private shape:

```rust
fn exec_resolved_command(
    &self,
    input: ExecCommandInput,
    mode: ExecCommandMode,
    context: CommandCallContext,
) -> Result<CommandYield, CommandServiceError>;
```

Acceptable internal mode:

```rust
enum ExecCommandMode {
    Session { handler: WorkspaceSessionHandler },
    OneShot,
}
```

It is also acceptable to keep a private `Option<WorkspaceSessionHandler>` helper
inside `command::exec`, but it must not be `pub` or `pub(crate)`.

## OperationServices Shim

If `OperationServices::exec_command` remains, it must be only:

```rust
pub fn exec_command(
    &self,
    input: ExecCommandInput,
    trace: OperationTraceContext,
) -> Result<CommandYield, CommandServiceError> {
    let caller_id = input.caller_id.clone();
    self.command.exec_command(input, CommandCallContext { caller_id, trace })
}
```

Rules:

- no workspace resolution in `services.rs`;
- no workspace state inspection in `services.rs`;
- no command-start policy in `services.rs`;
- record the shim as temporary in the implementation record;
- Milestone 7 should call `RuntimeServices.operation.command.exec_command(...)`
  directly.

## Implementation Sequence

### 0. Open The Record

Before code changes, update
`docs/daemon/workspace_migration/phase-operation_service_workspace_session/phase_2_implementation_record.md`
under `Milestone 6.5: Exec Command Boundary Migration`:

- set status to in progress;
- list intended files;
- carry forward that daemon dispatch migration remains Milestone 7;
- note whether `OperationServices::exec_command` is planned as a temporary shim.

### 1. Move Public Exec Boundary

In `crates/daemon/operation_service/src/command/exec.rs`:

- make `CommandOperationService::exec_command(input, context)` public;
- move optional workspace resolution from `OperationServices::exec_command` into
  this method;
- keep command validation before workspace creation/allocation;
- keep launch, registry binding, active insertion, cleanup rollback, and initial
  yield behavior unchanged except for the signature split.

### 2. Reduce OperationServices

In `crates/daemon/operation_service/src/services.rs`:

- remove `WorkspaceManagerService::resolve` from `OperationServices::exec_command`;
- forward directly to `self.command.exec_command`;
- remove imports that become unused;
- do not remove the `workspace`, `command`, or `remount` fields.

### 3. Update Tests

Add or update focused tests proving:

- command-service exec with `workspace_id: Some` resolves the workspace and uses
  the canonical handler;
- root mismatch rejects before command allocation;
- command-service exec with `workspace_id: None` creates a private one-shot host
  workspace;
- caller mismatch rejects before workspace creation;
- remount-pending persistent start still rejects;
- wrong caller behavior remains authorization-first;
- `OperationServices::exec_command`, if retained, is only a forwarding shim.

Prefer updating only tests that need to prove the new public boundary. It is fine
for unrelated tests to keep calling the temporary shim until Milestone 7 or 8.

### 4. Update Docs

Update:

- `phase_2_command_service_IMPLEMENTATION_PLAN.md`
- `phase_2_implementation_record.md`

The plan should say Milestone 7 daemon exec dispatch calls:

```rust
RuntimeServices.operation.command.exec_command(...)
```

The record should remove or supersede stale statements saying external callers
must use `OperationServices::exec_command`, unless they explicitly describe the
temporary shim.

### 5. Cleanup

Before marking complete:

- remove stale imports;
- remove any dead helper that only existed for the old wrapper split;
- do not add `#[allow(dead_code)]`;
- do not add placeholder `NotImplemented` paths.

## Verification

Run and record:

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

Run static checks and document expected false positives:

```text
rg -n "pub\\(crate\\) fn exec_command\\(|Option<WorkspaceSessionHandler>" crates/daemon/operation_service/src/command
rg -n "self\\.workspace\\.resolve|WorkspaceManagerService::resolve" crates/daemon/operation_service/src/services.rs
rg -n "OperationServices::exec_command remains public|External callers should continue to use OperationServices::exec_command" docs/daemon/workspace_migration/phase-operation_service_workspace_session
rg -n "request_id|trace_id|invocation_id|remountable|layer_stack_root" crates/daemon/operation_service/src/command
```

Expected outcomes:

- no public or crate-public handler-taking command exec remains;
- `services.rs` contains no workspace resolution in exec;
- docs mention `OperationServices::exec_command` only as a temporary shim or a
  removed API;
- no forbidden exec input fields appear in operation-service command code.

## Completion Criteria

Milestone 6.5 is complete only when:

- `CommandOperationService::exec_command(input, context)` is public;
- no public or crate-public command exec accepts `Option<WorkspaceSessionHandler>`;
- `OperationServices::exec_command`, if retained, is a forwarding shim only;
- workspace resolution for exec lives in command service;
- one-shot and persistent-session exec behavior remains covered;
- remount-pending command start behavior remains covered;
- stdin/read/poll/cancel signatures are unchanged;
- no daemon dispatch migration is included;
- implementation record is updated with files changed, verification,
  deviations, unresolved issues, and Milestone 7 handoff.
