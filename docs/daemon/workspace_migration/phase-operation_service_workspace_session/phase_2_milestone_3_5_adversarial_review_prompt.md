# Phase 2 Milestone 3.5 Adversarial Review Prompt

You are the lead reviewer for a review-only adversarial pass in:

`/Users/yifanxu/machine_learning/LoVC/ephemeral-ai/ephemeral-os`

Review target:

- Commit: `0fb1ebcc0` (`Refine command service launch handling`)
- Milestone: Phase 2 Milestone 3.5, policy-free command launch and initial
  yield.
- Scope: the operation-service command launch implementation and the
  policy-free command/workspace support added for that milestone.

This is a review-only task. Do not edit files. Do not implement fixes. Do not
stage, commit, format, or run cleanup commands that mutate the checkout.

If the worktree is dirty when you start, record `git status --short` and keep
the review anchored to `0fb1ebcc0`. Do not attribute unrelated dirty changes to
the Milestone 3.5 implementation unless the user explicitly asks you to review
the live dirty tree.

## Review Objective

Launch isolated adversarial review agents with split responsibility boundaries.
Each agent should read only the common context plus the files for its lane,
produce independent findings, and avoid seeing other agents' conclusions before
submitting its own report.

Your final synthesis must lead with findings, ordered by severity. Do not average
away a minority concern. If one lane reports a credible defect and others are
silent, preserve it until you have concrete evidence that it is not a bug.

## Common Context For All Agents

All agents should read these files before lane-specific work:

- `docs/daemon/workspace_migration/phase-operation_service_workspace_session/phase_2_command_service_SPEC.md`
- `docs/daemon/workspace_migration/phase-operation_service_workspace_session/phase_2_command_service_IMPLEMENTATION_PLAN.md`
- `docs/daemon/workspace_migration/phase-operation_service_workspace_session/phase_2_implementation_record.md`
- `/Users/yifanxu/.codex/attachments/898bfe07-430d-4db0-8cb7-1aa3ed678f26/pasted-text.txt`
- `git show --stat --oneline --decorate --no-renames 0fb1ebcc0`
- `git show --name-only --oneline --no-renames 0fb1ebcc0`

Milestone 3.5 was expected to replace the process-free command scaffold with a
real `command::CommandProcess::spawn` path, create policy-free launch material,
preserve `Some(handler)` versus `None` workspace lifecycle, clean up resources on
launch/yield failures, and return the first response from the low-level command
wait loop.

## Hard Non-Goals

Flag any of these if they appear in the Milestone 3.5 implementation:

- Reintroduced old `operation::command` policy, `StartCommand`, old command
  DTOs, or `CommandOps`/`WorkspaceRuntime` target architecture.
- Added request correlation fields to operation-service command contracts:
  `request_id`, `trace_id`, or `invocation_id`.
- Added per-command `remountable` launch policy.
- Exposed public `collect_completed`, `advance_active_commands_once`,
  `count_commands`, or `count_by_caller` command-service APIs.
- Changed local_os row projection, daemon dispatch migration, remount-pending
  policy, or broad compatibility cleanup outside Milestone 3.5.
- Made the low-level `command` crate depend on workspace, operation-service,
  layerstack, publish policy, or caller/session ownership policy.

## Agent A: Launch Lifecycle And Cleanup

Files to inspect:

- `crates/daemon/operation_service/src/command/exec.rs`
- `crates/daemon/operation_service/src/command/service.rs`
- `crates/daemon/operation_service/src/command/process_store.rs`
- `crates/daemon/operation_service/src/command/error.rs`
- `crates/daemon/operation_service/tests/command_exec.rs`
- `crates/daemon/operation_service/tests/support/mod.rs`

Adversarial questions:

- Does `exec_command` preserve the required state transition order: validate,
  resolve lifecycle, allocate/reserve, prepare launch artifacts, spawn, bind,
  insert active record, then wait for initial yield?
- On every failure path, are only acquired resources cleaned up, and are they
  cleaned in the right order?
- Does a session command using `workspace: Some(handler)` avoid creating,
  destroying, publishing, or exposing a new workspace?
- Does a one-shot command using `workspace: None` create a private workspace,
  keep its workspace id private, bind it only to the command id, and destroy it
  on launch/start failure before retention?
- If spawn succeeds but bind/active insert/initial yield fails, is the live
  process terminated or retained safely before workspace cleanup?
- Are command reservations released exactly once and only when the active record
  is not retained?

Expected output from this agent:

- Findings with exact file and line references.
- A resource lifecycle table for success, artifact failure, spawn failure, bind
  failure, active insert failure, and initial-yield completed/running paths.
- Explicit statement if no cleanup/lifecycle findings were found.

## Agent B: Policy-Free Command Crate And Boundary Enforcement

Files to inspect:

- `crates/daemon/command/src/launch.rs`
- `crates/daemon/command/src/lib.rs`
- `crates/daemon/command/src/process.rs`
- `crates/daemon/operation_service/src/command/launch.rs`
- `crates/daemon/operation_service/src/command/exec.rs`
- `crates/daemon/command/Cargo.toml`
- `crates/daemon/operation_service/Cargo.toml`

Adversarial questions:

- Is `command::launch` truly policy-free, or does it smuggle operation-service,
  workspace lifecycle, publish, trace, remount, or caller-authorization policy
  into the low-level command crate?
- Does operation-service command code construct the low-level runner request
  without exposing old command DTOs or operation-layer request identifiers in
  operation-service contracts?
- Is the use of runner protocol types inside the command crate acceptable as
  process substrate, or does it recreate the old policy layer?
- Are new dependencies directionally correct?
- Is `CommandProcess::new` gone from operation-service launch code?
- Are static boundary checks in the implementation record complete enough, or
  are there terms/paths they missed?

Expected output from this agent:

- Findings with exact file and line references.
- A dependency-direction assessment.
- The exact `rg` commands used for forbidden terms and their results.
- Explicit statement if no boundary findings were found.

## Agent C: Workspace Launch Material And Encapsulation

Files to inspect:

- `crates/daemon/workspace/src/model.rs`
- `crates/daemon/workspace/src/lib.rs`
- `crates/daemon/workspace/tests/unit/model.rs`
- `crates/daemon/operation_service/src/workspace_manager/session_manager.rs`
- `crates/daemon/operation_service/src/command/exec.rs`
- `crates/daemon/operation_service/tests/support/mod.rs`
- `crates/daemon/operation_service/tests/workspace_manager.rs`

Adversarial questions:

- Is `WorkspaceLaunchContext` the smallest necessary exposure for spawning
  commands, or does it leak resource internals into public/wire surfaces?
- Does its `Debug` implementation hide internal paths and file descriptors well
  enough for logs and error paths?
- Does `WorkspaceHandle` still preserve its existing public contract for create,
  resolve, capture, remount, and destroy workflows?
- Can stale or mismatched `WorkspaceSessionHandler` values produce a launch
  context for the wrong workspace/root?
- Are fake test handles realistic enough to catch production path/fd mistakes?
- Is there any accidental serialization, cloning, or API export that makes the
  launch material available outside the intended service boundary?

Expected output from this agent:

- Findings with exact file and line references.
- A short API exposure inventory for `WorkspaceLaunchContext`.
- Explicit statement if no workspace-encapsulation findings were found.

## Agent D: Initial Yield, Finalization, And Ownership Interaction

Files to inspect:

- `crates/daemon/operation_service/src/command/exec.rs`
- `crates/daemon/operation_service/src/command/launch.rs`
- `crates/daemon/operation_service/src/command/service.rs`
- `crates/daemon/operation_service/src/command/finalize.rs`
- `crates/daemon/operation_service/src/command/finalize_tests.rs`
- `crates/daemon/operation_service/tests/command_ownership.rs`
- `crates/daemon/operation_service/tests/command_exec.rs`

Adversarial questions:

- Does the first exec response really come from `command::yield_wait_loop`, not
  a hard-coded `Running` shell?
- If the process completes during initial yield, does the code hand off to the
  existing finalization path without double-finalizing, losing active state, or
  leaking a workspace?
- Does the implementation avoid holding process-store locks or active command
  references while waiting on the child process?
- Are active and completed ownership checks still enforced for poll, read,
  write stdin, cancel, and failed finalization reporting?
- Did the hidden launch-driver hook weaken production finalization behavior or
  let tests bypass a critical live-process invariant?
- Does the absence of a background finalizer watcher remain a documented future
  limitation rather than an accidental behavior regression?

Expected output from this agent:

- Findings with exact file and line references.
- A state-flow summary for running initial yield and completed initial yield.
- Explicit statement if no yield/finalization/ownership findings were found.

## Agent E: Test Realism, Coverage Gaps, And Handoff Accuracy

Files to inspect:

- `crates/daemon/operation_service/tests/command_exec.rs`
- `crates/daemon/operation_service/tests/command_ownership.rs`
- `crates/daemon/operation_service/tests/support/mod.rs`
- `crates/daemon/operation_service/src/command/exec.rs`
- `crates/daemon/operation_service/src/command/launch.rs`
- `crates/daemon/command/src/launch.rs`
- `docs/daemon/workspace_migration/phase-operation_service_workspace_session/phase_2_implementation_record.md`

Adversarial questions:

- Do fake launch drivers hide production-only bugs in artifact creation,
  runner request construction, process spawning, final path handling, transcript
  paths, or yield waiting?
- Are there focused tests for session versus one-shot launch, spawn/artifact
  failure cleanup, bind/insert failure cleanup, running initial yield, completed
  initial yield, and ownership after completion?
- Are command-crate tests sufficient for `build_exec_run_request` and related
  runner-request shape?
- Does the implementation record accurately list files changed, verification
  commands, deviations, unresolved issues, and handoff notes?
- Are there important Linux-only or namespace-runner assumptions that are not
  covered by the macOS/local test suite?
- Are any tests asserting implementation details so tightly that future
  Milestone 4/5 work will be blocked for the wrong reason?

Expected output from this agent:

- Findings with exact file and line references.
- A coverage matrix: behavior, covered by test, missing proof, risk.
- Explicit statement if no test/record findings were found.

## Required Verification Commands

The lead reviewer should run these once, or assign them to a dedicated
verification agent. Record exact pass/fail output in the final synthesis.

```text
git status --short
git show --stat --oneline --decorate --no-renames 0fb1ebcc0
CARGO_TARGET_DIR=/tmp/eos-phase2-review-target cargo test -p operation_service command_exec
CARGO_TARGET_DIR=/tmp/eos-phase2-review-target cargo test -p operation_service command_ownership
CARGO_TARGET_DIR=/tmp/eos-phase2-review-target cargo check -p command
CARGO_TARGET_DIR=/tmp/eos-phase2-review-target cargo check -p operation_service
cargo fmt --check
git diff --check
rg -n "operation::command|StartCommand|request_id|trace_id|invocation_id|remountable|CommandProcess::new|WorkspaceRuntime|CommandOps|ExecTarget|InternalHostOneShot" crates/daemon/operation_service/src/command crates/daemon/operation_service/tests/command_exec.rs
```

For the final `rg`, no matches in operation-service command launch code or
tests is the expected result. If `rg` exits 1 because it found no matches, record
that as the expected clean result.

Optional deeper checks if time permits:

```text
CARGO_TARGET_DIR=/tmp/eos-phase2-review-target cargo test -p command
CARGO_TARGET_DIR=/tmp/eos-phase2-review-target cargo test -p operation_service command_finalize
CARGO_TARGET_DIR=/tmp/eos-phase2-review-target cargo test -p operation_service command_process_store
CARGO_TARGET_DIR=/tmp/eos-phase2-review-target cargo test -p workspace model
```

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
- P1: lifecycle leak, wrong publish/discard behavior, ownership bypass,
  orphaned process/workspace, or old policy reintroduced into the target
  service boundary.
- P2: important missing coverage, inaccurate handoff, brittle test abstraction,
  or likely edge-case lifecycle bug.
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

Do not include a change summary unless the user asks for remediation. This is a
review-only prompt.
