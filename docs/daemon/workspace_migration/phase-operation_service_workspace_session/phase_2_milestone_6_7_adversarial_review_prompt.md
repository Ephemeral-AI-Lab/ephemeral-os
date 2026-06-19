# Phase 2 Milestone 6.7 Adversarial Review Prompt

You are the lead reviewer for a review-only adversarial pass in:

`/Users/yifanxu/machine_learning/LoVC/ephemeral-ai/ephemeral-os`

Review target:

- Commit: `5f723dc5e` (`Advance workspace runtime dispatch migration`)
- Milestone: Phase 2 Milestone 6.7, workspace profile selector and carrier
  rename.
- Scope: the `NetworkMode` / `network` to `WorkspaceProfile` / `profile` rename,
  the internal hook-dispatcher rename, focused tests, persisted-handle decision,
  static-scan classification, and implementation-record accuracy.

This is a review-only task. Do not edit files. Do not implement fixes. Do not
stage, commit, format, or run cleanup commands that mutate the checkout.

If the worktree is dirty when you start, record
`git status --short --untracked-files=all` and keep the review anchored to
`5f723dc5e`. Do not attribute unrelated dirty changes to Milestone 6.7 unless
they directly affect the selector/carrier rename or the verification evidence.

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
- `docs/daemon/workspace_migration/phase-operation_service_workspace_session/phase_2_milestone_6_6_workspace_profile_symmetry_SPEC.md`
- `docs/daemon/workspace_migration/phase-operation_service_workspace_session/phase_2_milestone_6_7_agent_prompt.md`
- `docs/daemon/workspace_migration/phase-operation_service_workspace_session/phase_2_implementation_record.md`
- `git status --short --untracked-files=all`
- `git show --stat --oneline --decorate --no-renames 5f723dc5e`
- `git show --name-only --oneline --no-renames 5f723dc5e`

Milestone 6.7 was expected to rename the workspace profile selector and carrier
surface only:

```text
NetworkMode                    -> WorkspaceProfile
NetworkMode::Host              -> WorkspaceProfile::HostCompatible
NetworkMode::Isolated          -> WorkspaceProfile::Isolated
WorkspaceHandle.network        -> WorkspaceHandle.profile
CreateWorkspaceRequest.network -> CreateWorkspaceRequest.profile
WorkspaceModeHandle.network    -> WorkspaceModeHandle.profile
WorkspaceModeContext.network   -> WorkspaceModeContext.profile
WorkspaceHandleSpec.network    -> WorkspaceHandleSpec.profile
enter_with_network             -> enter_with_profile
WorkspaceProfile<'a> runtime dispatcher -> WorkspaceProfileRuntime<'a>
```

Lower-level network mechanics must keep network vocabulary:

```text
NamespaceNetwork
NamespacePlan::host_workspace()
NamespacePlan::isolated_network()
WorkspaceLaunchNamespaceFds.net
IsolatedNetwork
veth / DNS / net-ready / holder network args
```

## Hard Non-Goals

Flag any of these if they appear in the Milestone 6.7 work:

- Daemon dispatch migration introduced as part of the rename review target.
- Behavior change to host-compatible or isolated workspace setup.
- New workspace profile variants.
- New wire protocol shape.
- Broad documentation rewrite outside Phase 2 migration docs.
- Renaming true network namespace implementation terms to profile terms.
- Reintroducing the compatibility `network_mode` module path.
- Encoding one-shot/session lifetime, capture/publish, command lifecycle,
  remount eligibility, or file-routing policy inside `WorkspaceProfile`.
- Keeping a `NetworkMode` alias without explicit implementation-record removal
  criteria.

## Agent A: Public Contract And Carrier Surface

Files to inspect:

- `crates/daemon/workspace/src/model.rs`
- `crates/daemon/workspace/src/lib.rs`
- `crates/daemon/workspace/tests/unit/model.rs`
- `crates/daemon/operation_service/tests/support/mod.rs`
- `crates/daemon/operation_service/tests/workspace_manager.rs`
- `docs/daemon/workspace_migration/phase-operation_service_workspace_session/phase_2_implementation_record.md`

Adversarial questions:

- Is the public selector named `WorkspaceProfile` with variants
  `HostCompatible` and `Isolated`?
- Is `NetworkMode` absent from production target code, with no hidden alias or
  compatibility module path?
- Do public resource DTOs and handles use `profile`, not `network`?
- Does `WorkspaceHandle` debug output report `profile` and continue hiding
  launch internals?
- Are public re-exports coherent and free of stale `NetworkMode` imports?
- Do focused model/support tests construct and assert the new profile carriers?

Expected output from this agent:

- Findings with exact file and line references.
- A contract table comparing expected selector/carrier names versus actual code.
- Explicit statement if no public-contract findings were found.

## Agent B: Internal Profile Runtime And Lifecycle Boundary

Files to inspect:

- `crates/daemon/workspace/src/profile/common.rs`
- `crates/daemon/workspace/src/profile/handle.rs`
- `crates/daemon/workspace/src/profile/host_compatible.rs`
- `crates/daemon/workspace/src/profile/isolated.rs`
- `crates/daemon/workspace/src/lifecycle/create.rs`
- `crates/daemon/workspace/src/lifecycle/destroy.rs`
- `crates/daemon/workspace/tests/unit/isolated_network_sessions.rs`

Adversarial questions:

- Did the internal hook dispatcher move away from `WorkspaceProfile<'a>` to a
  non-conflicting name such as `WorkspaceProfileRuntime<'a>`?
- Did `for_mode` become `for_profile`, and do lifecycle callers pass
  `handle.profile`?
- Do `WorkspaceModeHandle`, `WorkspaceModeContext`, and `WorkspaceHandleSpec`
  carry `profile`, not `network`?
- Does the mismatch/error text use profile terminology?
- Did host-compatible and isolated setup behavior remain unchanged except for
  selector/carrier names?
- Did profile hooks remain policy-free, with no one-shot, publish, remount, or
  file-routing policy introduced?

Expected output from this agent:

- Findings with exact file and line references.
- A lifecycle flow table for host-compatible and isolated profile creation.
- Explicit statement if no internal-runtime/lifecycle findings were found.

## Agent C: Operation-Service Command And Workspace Manager Boundary

Files to inspect:

- `crates/daemon/operation_service/src/workspace_manager/service.rs`
- `crates/daemon/operation_service/src/workspace_manager/session_manager.rs`
- `crates/daemon/operation_service/src/command/exec.rs`
- `crates/daemon/operation_service/src/command/finalize_tests.rs`
- `crates/daemon/operation_service/tests/command_exec.rs`
- `crates/daemon/operation_service/tests/command_remount.rs`
- `crates/daemon/operation_service/tests/workspace_remount.rs`

Adversarial questions:

- Does `create_private_workspace` use a `profile` parameter and build
  `CreateWorkspaceRequest { profile }`?
- Do one-shot commands request `WorkspaceProfile::HostCompatible`?
- Do isolated workspace fixtures request `WorkspaceProfile::Isolated` without
  changing remount/command behavior?
- Does command launch validation read `handler.handle.profile`?
- Does validation branch only for isolated net-FD requirements, while still
  requiring user/mount/pid FDs for all holder-backed workspace commands?
- Did any operation-service code start allocating overlays, spawning holders,
  creating cgroups, or doing profile setup directly?

Expected output from this agent:

- Findings with exact file and line references.
- A launch-validation matrix for host-compatible and isolated namespace FD
  requirements.
- Explicit statement if no operation-service boundary findings were found.

## Agent D: Daemon/Legacy Adapter Compatibility And Scope Control

Files to inspect:

- `crates/daemon/core/src/runtime/workspace.rs`
- `crates/daemon/core/src/op_adapter/command.rs`
- `crates/daemon/core/src/op_adapter/files.rs`
- `crates/daemon/operation/src/command/prepare.rs`
- `crates/daemon/operation/src/command/registry.rs`
- `crates/daemon/operation/src/command/service.rs`
- `crates/daemon/operation/src/command/service/exec.rs`
- `crates/daemon/operation/tests/command/prepare.rs`
- `crates/daemon/operation/tests/command/registry.rs`
- `crates/daemon/operation/tests/command/service.rs`
- `crates/daemon/core/tests/unit/isolated_network/service.rs`

Adversarial questions:

- Do legacy/runtime workspace context constructors use `profile`, not
  `network`, when building `WorkspaceModeContext`?
- Are remaining `network` matches in daemon/core and operation code true
  network implementation/config vocabulary or historical legacy code, not a
  missed profile carrier?
- Did the Milestone 6.7 rename avoid expanding daemon dispatch migration scope?
- Do tests that construct workspace contexts use the new `profile` field?
- Are old host/isolated command-workspace abstractions absent from the target
  profile-carrier boundary?

Expected output from this agent:

- Findings with exact file and line references.
- Classification of every remaining `network` match in the inspected files.
- Explicit statement if no compatibility/scope findings were found.

## Agent E: Persisted Handles, Docs, Static Scans, And Record Accuracy

Files to inspect:

- `crates/daemon/workspace/src/lifecycle/recovery.rs`
- `docs/daemon/workspace_migration/phase-operation_service_workspace_session/phase_2_command_service_IMPLEMENTATION_PLAN.md`
- `docs/daemon/workspace_migration/phase-operation_service_workspace_session/phase_2_milestone_6_6_workspace_profile_symmetry_SPEC.md`
- `docs/daemon/workspace_migration/phase-operation_service_workspace_session/phase_2_milestone_6_7_agent_prompt.md`
- `docs/daemon/workspace_migration/phase-operation_service_workspace_session/phase_2_implementation_record.md`

Adversarial questions:

- Does persisted manager JSON now write `profile` if a selector is serialized?
- Is the record's persisted-handle compatibility claim true: old rows without a
  selector are cleanup-only recovery inputs and do not require reconstructing a
  profile?
- Does the parent plan list Milestone 6.7 after Milestone 6.6 and before
  Milestone 7?
- Does the Phase 6.6/6.7 spec contain the Phase 6.7 rename section and the
  lower-level network naming boundary?
- Does the implementation record accurately list files changed, verification
  commands/results, static-scan classifications, deviations, unresolved issues,
  and handoff notes?
- Are historical docs/prompts with `NetworkMode` clearly historical, not target
  instructions for new code?

Expected output from this agent:

- Findings with exact file and line references.
- A persisted-handle compatibility assessment.
- A docs/record accuracy checklist.
- Explicit statement if no persistence/docs/record findings were found.

## Required Verification Commands

The lead reviewer should run these once, or assign them to a dedicated
verification agent. Record exact pass/fail output in the final synthesis.

```text
git status --short --untracked-files=all
git show --stat --oneline --decorate --no-renames 5f723dc5e
CARGO_TARGET_DIR=/tmp/eos-phase2-review-target cargo check -p workspace
CARGO_TARGET_DIR=/tmp/eos-phase2-review-target cargo test -p workspace
CARGO_TARGET_DIR=/tmp/eos-phase2-review-target cargo check -p operation_service
CARGO_TARGET_DIR=/tmp/eos-phase2-review-target cargo test -p operation_service command_exec
CARGO_TARGET_DIR=/tmp/eos-phase2-review-target cargo test -p operation_service command_remount
CARGO_TARGET_DIR=/tmp/eos-phase2-review-target cargo test -p operation_service workspace_remount
CARGO_TARGET_DIR=/tmp/eos-phase2-review-target cargo check -p daemon
cargo fmt --check
git diff --check
rg -n "NetworkMode|\\.network\\b|network:|enter_with_network|for_mode|network_mode" crates/daemon/workspace/src crates/daemon/operation_service/src crates/daemon/core/src crates/daemon/operation/src
rg -n "WorkspaceProfile<'|enum WorkspaceProfile<'|trait WorkspaceProfile" crates/daemon/workspace/src/profile
rg -n "NetworkMode::Host|NetworkMode::Isolated|network mode|network_mode" docs/daemon/workspace_migration/phase-operation_service_workspace_session
```

The static `rg` commands are evidence scans, not automatic pass/fail checks.
Every remaining match must be classified as lower-level network implementation,
temporary compatibility, historical documentation, test fixture, or bug.

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
- P1: wrong profile selected, command launched with wrong namespace FD
  requirements, old selector/carrier still reachable in production target code,
  persisted recovery corruption, or profile policy leakage.
- P2: important missing coverage, inaccurate implementation record,
  unclassified static-scan match, brittle compatibility assumption, or stale
  docs likely to mislead Milestone 7.
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
