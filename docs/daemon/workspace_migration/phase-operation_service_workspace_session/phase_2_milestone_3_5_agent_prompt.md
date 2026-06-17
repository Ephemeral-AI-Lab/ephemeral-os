# Phase 2 Milestone 3.5 Agent Prompt

You are implementing Phase 2 Milestone 3.5 only in:

`/Users/yifanxu/machine_learning/LoVC/ephemeral-ai/ephemeral-os`

Milestone 3.5 is: policy-free command launch and initial yield.

Your job is to replace the Milestone 3 process-free command scaffold with a real
low-level command spawn path through `crates/daemon/command`, while preserving
the operation-service ownership and workspace lifecycle boundaries.

## Read First

Before editing code, read these files:

- `docs/daemon/workspace_migration/phase-operation_service_workspace_session/phase_2_command_service_SPEC.md`
- `docs/daemon/workspace_migration/phase-operation_service_workspace_session/phase_2_command_service_IMPLEMENTATION_PLAN.md`
- `docs/daemon/workspace_migration/phase-operation_service_workspace_session/phase_2_implementation_record.md`
- `crates/daemon/operation_service/src/command/exec.rs`
- `crates/daemon/operation_service/src/command/service.rs`
- `crates/daemon/operation_service/src/command/process_store.rs`
- `crates/daemon/operation_service/src/command/contract.rs`
- `crates/daemon/operation_service/src/command/error.rs`
- `crates/daemon/command/src/process.rs`
- `crates/daemon/command/src/yield_wait_loop.rs`

If the current tree already contains later Milestone 4 finalization code, treat
it as adjacent context. Do not remove or redesign it. Milestone 3.5 closes the
gap before finalization: real spawn plus the first exec yield.

## Scope

Implement only:

- A policy-free launch context for `CommandOperationService::exec_command`.
- Construction of `command::CommandProcessSpec`.
- Construction of `command::process::CommandProcessSpawn`.
- Replacement of `CommandProcess::new` with `CommandProcess::spawn`.
- First response via `command::yield_wait_loop` or the current exported wait
  helper in `crates/daemon/command`.
- Cleanup on spawn/artifact/initial-yield failure.
- Tests for the launch and initial-yield behavior.
- Milestone 3.5 status, verification, and handoff notes in the implementation
  record.

Do not implement:

- New one-shot publish/discard semantics.
- New persistent-session finalization semantics.
- Row windows or local_os row projection.
- Remount-pending behavior.
- Daemon dispatch migration.
- Public collect/advance APIs.
- A new command ownership model.

## Hard Boundary Rules

Do not import or reuse the old command policy layer:

- No `operation::command`.
- No `StartCommand`.
- No old command DTOs.
- No `request_id`, `trace_id`, or `invocation_id` fields in the operation-service
  command contract.
- No `remountable` command launch policy.
- No `WorkspaceRuntime`, `CommandOps`, `ExecTarget`, or `InternalHostOneShot`.
- No process-free `CommandProcess::new` path left in operation-service command
  launch code.

The `command` crate must stay policy-free. It may own process mechanics,
artifacts, PTY runner startup, transcript/output/final paths, and waiting for
initial yield. `operation_service` owns caller authorization, Some/None routing,
workspace lifecycle, finalization policy, registry binding, and cleanup.

## Workspace Lifecycle Rules

Preserve the exact lifecycle split:

Session command, `workspace: Some(handler)`:

- Use the already resolved `WorkspaceSessionHandler`.
- Do not create a workspace.
- Do not destroy the workspace.
- Do not publish workspace changes implicitly.
- Do not expose a new workspace id.
- Bind the command to the existing `workspace_id`.
- Store `CommandFinalizePolicy::Session`.
- On launch/start failure, return the command error without destroying the
  session workspace.

One-shot command, `workspace: None`:

- Create a private host workspace through `WorkspaceManagerService`.
- Keep the temporary `workspace_id` private; callers continue using
  `command_id`.
- Bind the command to the temporary `workspace_id`.
- Store `CommandFinalizePolicy::OneShotPublishThenDestroy`.
- On launch/start failure before a live command is retained, unbind any registry
  entry and destroy the temporary workspace.
- If the first yield is still running, keep the temporary workspace alive for
  later finalization.
- Later finalization publishes only on successful process completion, discards on
  non-success/cancel/timeout, and destroys the temporary workspace after the
  publish/discard result is recorded. Do not implement new finalization behavior
  in this milestone unless adapting to existing code is required.
- Add a real finalizer watcher only if the real spawn/yield implementation needs
  one. Do not reintroduce placeholder no-op watcher hooks.

## Current Code Shape To Replace

The current scaffold in `operation_service/src/command/exec.rs` creates an
inactive process with:

```rust
process: ::command::CommandProcess::new(::command::CommandProcessSpec {
    id: command_id.0.clone(),
    caller_id: context.caller_id.0.clone(),
    command: input.cmd,
    timeout_seconds: input.timeout_seconds,
}),
```

Replace this with a real `CommandProcess::spawn` path after launch artifacts are
prepared. The low-level command API is in `crates/daemon/command/src/process.rs`:

```rust
pub struct CommandProcessSpec {
    pub id: String,
    pub caller_id: String,
    pub command: String,
    pub timeout_seconds: Option<f64>,
}

pub struct CommandProcessSpawn<'a> {
    pub run_request: Value,
    pub request_path: PathBuf,
    pub output_path: PathBuf,
    pub final_path: PathBuf,
    pub transcript_path: PathBuf,
    pub transcript_timestamp_timezone: &'a str,
    pub output_drain_grace_ms: u64,
}
```

`CommandProcess::spawn(spec, parts)` writes the runner request, starts the
current executable as `ns-runner`, writes process metadata, waits for start ack,
and returns a live `CommandProcess`.

## Required Implementation Shape

Keep the operation-service state transition order explicit:

1. Validate command input and caller ownership.
2. Resolve lifecycle mode:
   - `Some(handler)` means session command.
   - `None` means create one-shot host workspace.
3. Allocate `command_id` and reserve active command capacity.
4. Prepare policy-free launch artifacts.
5. Spawn the low-level command process.
6. Bind `command_id -> workspace_id`.
7. Insert `ActiveCommandProcess` with the live process and transcript path.
8. Register real finalizer supervision only if implemented; otherwise rely on
   existing poll-time finalization until a real supervisor lands.
9. Run the initial yield wait and return `CommandYield`.

If a later step fails, clean up exactly the resources already acquired:

- Reservation only: release by dropping the reservation.
- Created one-shot workspace: destroy it on failure.
- Registry binding: unbind it before returning failure.
- Spawned process that cannot be retained: terminate or cancel through the
  command process API if available, then preserve one-shot cleanup.
- Session workspace: never destroy it from command launch cleanup.

## Tests To Add Or Update

Add focused tests under `crates/daemon/operation_service/tests/command_exec.rs`
or the existing command-service unit tests:

- `Some(handler)` starts a command without creating or destroying a workspace.
- `None` creates a private host workspace and binds the temporary workspace id.
- Spawn/artifact failure destroys only one-shot workspaces and leaves session
  workspaces alive.
- Registry/active insert failure after spawn unbinds and cleans up correctly.
- Initial yield returns running output from the wait loop instead of an
  unconditional `Running` shell.
- Initial yield completed path returns a completed command yield and hands off to
  existing finalization/completion behavior without adding public collect/advance
  APIs.

Keep existing ownership tests passing: only the command owner may poll, read,
write stdin, cancel, or observe retained completion state.

## Verification

Run and record results in
`docs/daemon/workspace_migration/phase-operation_service_workspace_session/phase_2_implementation_record.md`:

```text
CARGO_TARGET_DIR=/tmp/eos-phase2-command-service-target cargo test -p operation_service command_exec
CARGO_TARGET_DIR=/tmp/eos-phase2-command-service-target cargo test -p operation_service command_ownership
CARGO_TARGET_DIR=/tmp/eos-phase2-command-service-target cargo check -p command
CARGO_TARGET_DIR=/tmp/eos-phase2-command-service-target cargo check -p operation_service
cargo fmt --check
git diff --check
rg -n "operation::command|StartCommand|request_id|trace_id|invocation_id|remountable|CommandProcess::new|WorkspaceRuntime|CommandOps|ExecTarget|InternalHostOneShot" crates/daemon/operation_service/src/command crates/daemon/operation_service/tests/command_exec.rs
```

The final `rg` command must have no matches in operation-service command launch
code or tests, except if you intentionally explain a false positive in the
implementation record.

## Completion Criteria

Milestone 3.5 is complete only when:

- `CommandOperationService::exec_command` uses a policy-free launch context and
  `CommandProcess::spawn`.
- The first exec response comes from the command wait loop, not a hard-coded
  running shell.
- Workspace lifecycle is correct for both `Some(handler)` and `None`.
- Failure cleanup does not leak one-shot workspaces or destroy session
  workspaces.
- The implementation record lists changed files, verification output, deviations,
  unresolved issues, and handoff notes for Milestone 4 or later work.
