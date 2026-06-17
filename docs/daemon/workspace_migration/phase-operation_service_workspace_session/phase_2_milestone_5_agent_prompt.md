# Phase 2 Milestone 5 Agent Prompt

You are implementing Phase 2 Milestone 5 only in:

`/Users/yifanxu/machine_learning/LoVC/ephemeral-ai/ephemeral-os`

Milestone 5 is: local_os row projection for command output.

Your job is to replace the current minimal line-window shell with a
local_os-compatible row projection derived from the command transcript source,
while preserving the operation-service ownership, command lifecycle, and
finalization boundaries already established by earlier milestones.

## Read First

Before editing code, read these files:

- `docs/daemon/workspace_migration/phase-operation_service_workspace_session/phase_2_command_service_SPEC.md`
- `docs/daemon/workspace_migration/phase-operation_service_workspace_session/phase_2_command_service_IMPLEMENTATION_PLAN.md`
- `docs/daemon/workspace_migration/phase-operation_service_workspace_session/phase_2_implementation_record.md`
- `crates/daemon/operation_service/src/command/contract.rs`
- `crates/daemon/operation_service/src/command/service.rs`
- `crates/daemon/operation_service/src/command/process_store.rs`
- `crates/daemon/operation_service/src/command/exec.rs`
- `crates/daemon/operation_service/src/command/finalize.rs`
- `crates/daemon/operation_service/src/command/mod.rs`
- `crates/daemon/command/src/process.rs`
- `crates/daemon/command/src/transcript.rs` if present, otherwise inspect the
  command crate files that write transcript bytes.

If the current tree already contains Milestone 3.5 real launch/yield code or
Milestone 4 finalization code, treat that as adjacent completed work. Do not
remove or redesign it. Milestone 5 is only about row-oriented transcript
projection.

## Scope

Implement only:

- A row-oriented command transcript projection compatible with local_os-style
  output:

  ```text
  { offset, next_offset, total_lines, output_truncated, output: rows }
  ```

- `CommandStream` and `CommandTranscriptRow`, where rows look like:

  ```rust
  pub struct CommandTranscriptRow {
      pub offset: u64,
      pub stream: CommandStream,
      pub text: String,
  }
  ```

- `CommandLinesOutput` fields required by the plan:
  - `command_id`
  - `status`
  - `exit_code`
  - `offset`
  - `next_offset`
  - `total_lines`
  - `truncated_before`
  - `output_truncated`
  - `output: Vec<CommandTranscriptRow>`
- A transcript row module, preferably
  `crates/daemon/operation_service/src/command/transcript.rs`, unless a clearly
  policy-free command-crate helper is smaller and cleaner.
- `CommandOperationService::read_lines` backed by row offsets and caller
  authorization for both active and completed commands.
- Enough retained transcript metadata in active and completed command records to
  read rows after finalization.
- Tests for row offsets, windows, truncation, completed retention, and caller
  authorization.
- Milestone 5 status, verification, deviations, unresolved issues, and handoff
  notes in the implementation record.

## Do Not Implement

- No TypeScript local_os client migration.
- No daemon dispatch migration.
- No remount behavior or remount-pending state.
- No changes to one-shot/session finalization policy.
- No changes to command launch policy or workspace lifecycle.
- No public `advance_active_commands_once`, `collect_completed`,
  `count_commands`, or `count_by_caller` APIs.
- No duplicated transcript source.
- No byte-offset semantics.
- No replacement of the legacy daemon-native `poll` response shape unless the
  plan explicitly requires it.

## Hard Boundary Rules

Preserve the established command-service boundaries:

- `operation_service::command` owns caller authorization, command-id based row
  reads, active/completed lookup, and any policy about status projection.
- The low-level `command` crate may own policy-free transcript parsing helpers
  only if they are truly command substrate utilities.
- Do not import or reuse old command policy:
  - no `operation::command`
  - no `StartCommand`
  - no old command DTOs
  - no `WorkspaceRuntime`
  - no `CommandOps`
  - no `ExecTarget`
  - no `InternalHostOneShot`
  - no `request_id`, `trace_id`, `invocation_id`, or `remountable` fields in
    operation-service command contracts
- Preserve the local_os row-list contract. Do not redesign output into a
  "nicer" stdout/stderr object. The public projection is a row list with stable
  row offsets.

## Current Code Shape To Replace

The current operation-service command contract still has a minimal row shell:

```rust
pub struct CommandLinesOutput {
    pub command_id: CommandId,
    pub offset: u64,
    pub next_offset: u64,
    pub total_lines: u64,
    pub output_truncated: bool,
    pub output: Vec<CommandOutputLine>,
}

pub struct CommandOutputLine {
    pub offset: u64,
    pub text: String,
}
```

`CommandOperationService::read_lines` currently calls a local `line_window`
helper in `command/service.rs` over `active.process.read_output_since(0)` or
`completed.result.stdout`. That is the Milestone 3 shell. Replace or refactor it
so `read_lines` is row-oriented and transcript-backed.

Active records currently retain `CommandTranscriptStore`; completed records
retain `RetainedCommandTranscript`. Use those paths as the bridge for row reads
instead of adding a separate output store.

## Required Implementation Shape

Keep the sequence conservative:

1. Mark Milestone 5 as in progress in
   `phase_2_implementation_record.md`.
2. Inspect how the command crate writes transcript bytes and how
   `CommandProcess` exposes stdout snapshots.
3. Decide the smallest row source:
   - Prefer `operation_service::command::transcript` for row projection policy.
   - Use a command-crate helper only for policy-free parsing/windowing.
4. Add `CommandStream`, `CommandTranscriptRow`, and the expanded
   `CommandLinesOutput`.
5. Add a row window helper with stable line offsets:
   - `offset` is a row offset, not a byte offset.
   - `next_offset` is the first offset after the returned window.
   - `total_lines` is the total retained/known row count.
   - `truncated_before` is the number of rows not available before the window.
   - `output_truncated` means the requested row window could not fit configured
     retention/window bounds.
6. Wire `CommandOperationService::read_lines` through active/completed
   authorization first, then row projection.
7. Keep `poll` and `write_stdin` daemon-native unless a row-compatible wrapper is
   already present in this crate. If touched, derive their visible output from
   the same transcript source and do not bypass authorization.
8. Update module exports and tests.
9. Update the implementation record before closeout.

## Tests To Add Or Update

Add focused tests under
`crates/daemon/operation_service/tests/command_transcript_rows.rs` or a clearly
scoped command-service unit test module.

Cover:

- Row windows preserve stable offsets.
- `offset`, `limit`, `next_offset`, and `total_lines` are correct.
- `truncated_before` and `output_truncated` are correct for bounded windows.
- Rows include `stream` and `text`.
- Active command reads authorize by `CommandCallContext`.
- Completed command reads still authorize by retained `caller_id`.
- Wrong callers cannot read active or completed rows.
- Poll/read completed status and exit code stay consistent.
- Existing command ownership and finalization tests still pass.

If transcript parsing from current PTY logs is lossy or ambiguous, prefer a
structured row sidecar owned by `operation_service::command`, while preserving
raw transcript logs.

## Verification

Run and record results in
`docs/daemon/workspace_migration/phase-operation_service_workspace_session/phase_2_implementation_record.md`:

```text
CARGO_TARGET_DIR=/tmp/eos-phase2-command-service-target cargo test -p operation_service command_transcript_rows
CARGO_TARGET_DIR=/tmp/eos-phase2-command-service-target cargo test -p operation_service command_ownership
CARGO_TARGET_DIR=/tmp/eos-phase2-command-service-target cargo test -p operation_service command_finalize
CARGO_TARGET_DIR=/tmp/eos-phase2-command-service-target cargo test -p operation_service
CARGO_TARGET_DIR=/tmp/eos-phase2-command-service-target cargo check -p operation_service
cargo fmt --check
git diff --check
rg -n "operation::command|StartCommand|request_id|trace_id|invocation_id|remountable|WorkspaceRuntime|CommandOps|ExecTarget|InternalHostOneShot|advance_active_commands_once|collect_completed|count_commands|count_by_caller" crates/daemon/operation_service/src/command crates/daemon/operation_service/tests
```

The final `rg` command must have no matches in operation-service command code
or tests, except intentionally documented false positives outside the Milestone
5 scope.

## Completion Criteria

Milestone 5 is complete only when:

- `CommandLinesOutput` exposes row-oriented status, offset, truncation, and row
  fields.
- `read_lines` is command-id based and validates caller ownership through
  `CommandCallContext`.
- Active and completed command rows come from one transcript source or a
  clearly documented raw-transcript plus structured-row sidecar pair.
- Row offsets are line offsets, not byte offsets.
- `poll` and `write_stdin` do not duplicate transcript storage or bypass
  authorization.
- Existing command launch, finalization, ownership, and service-graph tests pass.
- The implementation record lists changed files, verification output,
  deviations, unresolved issues, and handoff notes for Milestone 6.
