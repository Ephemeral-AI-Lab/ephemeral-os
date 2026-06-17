# Phase 2 Milestone 5 Adversarial Review Prompt

You are the lead reviewer for a review-only adversarial pass in:

`/Users/yifanxu/machine_learning/LoVC/ephemeral-ai/ephemeral-os`

Review target:

- Milestone: Phase 2 Milestone 5, local_os row projection for command output.
- Scope: operation-service command row projection, retained transcript reads,
  row/status contract shape, focused tests, implementation-record accuracy, and
  the newly documented `tool_call` sandbox-protocol cleanup note.
- Review mode: live worktree review. Record `git status --short` before
  starting, but do not attribute unrelated dirty files to Milestone 5 unless
  they affect the scoped behavior below.

This is a review-only task. Do not edit files. Do not implement fixes. Do not
stage, commit, format, or run cleanup commands that mutate the checkout.

## Review Objective

Launch isolated adversarial review agents with split responsibility boundaries.
Each agent should read only the common context plus the files for its lane,
produce independent findings, and avoid seeing other agents' conclusions before
submitting its own report.

Your final synthesis must lead with findings, ordered by severity. Do not
average away a minority concern. If one lane reports a credible defect and
others are silent, preserve it until you have concrete evidence that it is not a
bug.

## Common Context For All Agents

All agents should read these files before lane-specific work:

- `docs/daemon/workspace_migration/phase-operation_service_workspace_session/phase_2_command_service_SPEC.md`
- `docs/daemon/workspace_migration/phase-operation_service_workspace_session/phase_2_command_service_IMPLEMENTATION_PLAN.md`
- `docs/daemon/workspace_migration/phase-operation_service_workspace_session/phase_2_implementation_record.md`
- `docs/daemon/workspace_migration/phase-operation_service_workspace_session/phase_2_milestone_5_agent_prompt.md`
- `docs/daemon/workspace_migration/tool_call_sandbox_protocol_FINDINGS.md`
- `git status --short`
- `git diff --stat`
- `git diff -- crates/daemon/operation_service/src/command crates/daemon/operation_service/tests docs/daemon/workspace_migration`

Milestone 5 was expected to replace the minimal string line-window shell with a
local_os-compatible row projection:

```text
{ offset, next_offset, total_lines, truncated_before, output_truncated, output: rows }
```

Rows must be line-offset based, not byte-offset based:

```rust
pub struct CommandTranscriptRow {
    pub offset: u64,
    pub stream: CommandStream,
    pub text: String,
}
```

## Hard Non-Goals

Flag any of these if they appear in the Milestone 5 work:

- Implementing daemon dispatch migration or TypeScript local_os client changes.
- Changing one-shot/session finalization policy, remount behavior, workspace
  lifecycle, command launch policy, or background finalizer behavior.
- Reintroducing old command policy: `operation::command`, `StartCommand`,
  `CommandOps`, `WorkspaceRuntime`, `ExecTarget`, `InternalHostOneShot`,
  `request_id`, `trace_id`, `invocation_id`, or `remountable` in
  operation-service command contracts.
- Adding public `advance_active_commands_once`, `collect_completed`,
  `count_commands`, or `count_by_caller` APIs.
- Duplicating transcript storage instead of deriving row reads from retained
  transcript metadata.
- Removing `tool_call` now. The `tool_call` task is documentation and future
  cleanup only; reviewers should assess whether the note is accurate and
  bounded, not implement the rename/removal.

## Agent A: Row Contract And Public Shape

Files to inspect:

- `crates/daemon/operation_service/src/command/contract.rs`
- `crates/daemon/operation_service/src/command/mod.rs`
- `crates/daemon/operation_service/src/command/service.rs`
- `crates/daemon/operation_service/tests/command_transcript_rows.rs`
- `docs/daemon/workspace_migration/phase-operation_service_workspace_session/phase_2_implementation_record.md`

Adversarial questions:

- Does `CommandLinesOutput` expose exactly the required row-oriented fields:
  `command_id`, `status`, `exit_code`, `offset`, `next_offset`, `total_lines`,
  `truncated_before`, `output_truncated`, and `output`?
- Did `CommandOutputLine` disappear from the public command-service contract, or
  is an old line-only shape still reachable?
- Are row offsets stable line offsets, and does `next_offset` match the first
  offset after the returned rows?
- Is the output still a row list rather than a stdout/stderr object?
- Are active and completed statuses/exit codes represented consistently in
  `read_lines` output?

Expected output from this agent:

- Findings with exact file and line references.
- A short contract-shape table comparing expected versus actual fields.
- Explicit statement if no contract/public-shape findings were found.

## Agent B: Authorization, Retention, And Finalization Boundaries

Files to inspect:

- `crates/daemon/operation_service/src/command/service.rs`
- `crates/daemon/operation_service/src/command/process_store.rs`
- `crates/daemon/operation_service/src/command/finalize.rs`
- `crates/daemon/operation_service/src/command/exec.rs`
- `crates/daemon/operation_service/tests/command_ownership.rs`
- `crates/daemon/operation_service/tests/command_transcript_rows.rs`
- `crates/daemon/operation_service/src/command/finalize_tests.rs`

Adversarial questions:

- Does `read_lines` authorize active records before reading transcript rows?
- Does completed-record reading authorize by retained `caller_id` and avoid a
  collect/completed side channel?
- Do completed records retain enough transcript metadata after finalization for
  owner reads?
- Does the Milestone 5 implementation avoid changing finalization,
  publish/discard, workspace destroy, or persistent-session behavior?
- Does `poll`/`write_stdin` continue to use daemon-native behavior without
  bypassing authorization or creating a second output source?
- Did any direct-handler validation or launch-path cleanup drift outside the
  stated Milestone 5 boundary?

Expected output from this agent:

- Findings with exact file and line references.
- A state/authorization table for active owner, active wrong caller, completed
  owner, and completed wrong caller.
- Explicit statement if no authorization/finalization findings were found.

## Agent C: Transcript Parsing, Stream Fidelity, And Truncation

Files to inspect:

- `crates/daemon/operation_service/src/command/transcript.rs`
- `crates/daemon/command/src/transcript.rs`
- `crates/daemon/command/src/process.rs`
- `crates/daemon/command/src/pty.rs`
- `crates/daemon/operation_service/tests/command_transcript_rows.rs`
- `docs/daemon/workspace_migration/phase-operation_service_workspace_session/phase_2_implementation_record.md`

Adversarial questions:

- Is row projection derived from the retained transcript path, not completed
  stdout or a separate duplicate output store?
- Does the parser handle current timestamp-prefixed PTY transcript lines without
  turning timestamps into user-visible row text?
- Does JSONL row parsing preserve explicit `stdout` and `stderr` streams if a
  structured row sidecar is added later?
- Is the documented limitation about merged PTY output and true stderr fidelity
  accurate, visible, and not overstated?
- Are `truncated_before` and `output_truncated` semantics consistent with the
  current local_os row contract?
- Are missing transcript files, invalid JSON lines, empty output, large offsets,
  and `limit = 0` handled deliberately?

Expected output from this agent:

- Findings with exact file and line references.
- A parsing matrix: input transcript shape, expected rows, risk.
- Explicit statement if no transcript/windowing findings were found.

## Agent D: `tool_call` Sandbox Protocol Boundary

Files to inspect:

- `docs/daemon/workspace_migration/tool_call_sandbox_protocol_FINDINGS.md`
- `crates/daemon/linux-namespace-subprocess/src/protocol/mod.rs`
- `crates/daemon/command/src/launch.rs`
- `crates/daemon/workspace/src/namespace/setns_runner.rs`
- `crates/daemon/operation/src/command/prepare.rs`
- `crates/daemon/linux-namespace-subprocess/src/runner/fresh_ns.rs`
- `crates/daemon/linux-namespace-subprocess/src/runner/fresh_ns/command.rs`
- `crates/daemon/linux-namespace-subprocess/src/runner/setns.rs`
- `crates/daemon/operation_service/tests/command_exec.rs`

Adversarial questions:

- Does the findings note cite all meaningful current `tool_call` producers,
  consumers, and tests?
- Is the note clear that `tool_call` is agent vocabulary and should be removed
  later from sandbox/namespace-runner protocol vocabulary?
- Is the cleanup direction bounded to future work, with no accidental request to
  remove `tool_call` during Milestone 5?
- Are there other sandbox protocol forms or wire compatibility constraints the
  note should cite before future cleanup?
- Does the current command-service implementation leak `tool_call` or
  `invocation_id` into operation-service public command contracts, or is it
  contained to low-level runner payload construction/tests?

Expected output from this agent:

- Findings with exact file and line references.
- A producer/consumer/test inventory for `tool_call`.
- Explicit statement if no protocol-boundary findings were found.

## Agent E: Tests, Static Searches, And Record Accuracy

Files to inspect:

- `crates/daemon/operation_service/tests/command_transcript_rows.rs`
- `crates/daemon/operation_service/tests/command_exec.rs`
- `crates/daemon/operation_service/tests/command_ownership.rs`
- `crates/daemon/operation_service/src/command/transcript.rs`
- `docs/daemon/workspace_migration/phase-operation_service_workspace_session/phase_2_implementation_record.md`
- `docs/daemon/workspace_migration/tool_call_sandbox_protocol_FINDINGS.md`

Adversarial questions:

- Do tests cover row offsets, windows, `next_offset`, `total_lines`,
  `truncated_before`, `output_truncated`, streams, active reads, completed
  reads, and wrong-caller rejection?
- Do tests accidentally depend on fake launch behavior that cannot catch
  production transcript-path bugs?
- Does the implementation record accurately list changed files, verification
  commands, deviations, unresolved issues, false positives, and handoff notes?
- Is the static forbidden-term search complete and are its false positives
  explicitly justified?
- Are there untracked or dirty files that could make the review accidentally
  mix Milestone 5 work with unrelated changes?

Expected output from this agent:

- Findings with exact file and line references.
- A coverage matrix: behavior, covered by test, missing proof, risk.
- Explicit statement if no test/record findings were found.

## Required Verification Commands

The lead reviewer should run these once, or assign them to a dedicated
verification agent. Record exact pass/fail output in the final synthesis.

```text
git status --short --untracked-files=all
CARGO_TARGET_DIR=/tmp/eos-phase2-review-target cargo test -p operation_service command_transcript_rows
CARGO_TARGET_DIR=/tmp/eos-phase2-review-target cargo test -p operation_service command_ownership
CARGO_TARGET_DIR=/tmp/eos-phase2-review-target cargo test -p operation_service command_finalize
CARGO_TARGET_DIR=/tmp/eos-phase2-review-target cargo test -p operation_service
CARGO_TARGET_DIR=/tmp/eos-phase2-review-target cargo check -p operation_service
cargo fmt --check
git diff --check
rg -n "operation::command|StartCommand|request_id|trace_id|invocation_id|remountable|WorkspaceRuntime|CommandOps|ExecTarget|InternalHostOneShot|advance_active_commands_once|collect_completed|count_commands|count_by_caller" crates/daemon/operation_service/src/command crates/daemon/operation_service/tests
rg -n "tool_call|ToolCall|tool call" crates/daemon/linux-namespace-subprocess/src crates/daemon/command/src crates/daemon/workspace/src/namespace crates/daemon/operation/src/command crates/daemon/operation_service/tests docs/daemon/workspace_migration
```

For the forbidden-term `rg`, a test-only low-level runner payload assertion may
be a documented false positive only if it remains outside operation-service
command contracts. Do not treat that as clean without explaining why.

Do not rerun expensive or broad suites repeatedly. If a command fails, preserve
the first failure evidence and inspect the smallest relevant target.

## Agent Report Format

Each agent must return:

```text
## Findings

- [P0/P1/P2/P3] Title
  File: path:line
  Evidence: concise explanation tied to code behavior.
  Impact: what breaks or what invariant is lost.
  Suggested fix direction: narrow, no implementation.

## No-Finding Areas

- Area checked: concrete statement of what was reviewed and why no issue was
  found.

## Verification

- Commands run and result.
- Commands not run and why.

## Residual Risk

- Any assumption the lane could not prove from local code/tests.
```

Severity guide:

- P0: data loss, workspace corruption, command execution privilege/safety break,
  or deterministic panic on normal use.
- P1: ownership bypass, wrong command output exposed, finalization/publish
  regression, orphaned process/workspace, or old command policy reintroduced
  into the target service boundary.
- P2: important missing coverage, inaccurate handoff, brittle test abstraction,
  lossy row semantics, or future protocol cleanup ambiguity likely to cause an
  implementation mistake.
- P3: low-risk maintainability or clarity issue that could cause future
  mistakes but does not currently break behavior.

## Lead Reviewer Synthesis Format

Produce one final review report:

```text
## Findings

List confirmed findings first, ordered by severity. Include file:line evidence.
If there are no confirmed findings, write "No findings."

## Open Questions

List only questions that affect correctness or scope.

## Verification

Summarize commands run, pass/fail status, and any skipped checks.

## Residual Risk

List risks that remain even after clean local verification, especially
Linux-only namespace/process behavior not exercised locally.

## Agent Coverage

Briefly state which agents ran and which lane each covered.
```

Do not include remediation patches or a change summary unless the user asks for
implementation after the review.
