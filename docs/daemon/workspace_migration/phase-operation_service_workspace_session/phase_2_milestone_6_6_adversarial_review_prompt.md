# Phase 2 Milestone 6.6 SRP/SOLID Adversarial Review Prompt

You are the lead reviewer for a review-only adversarial pass in:

`/Users/yifanxu/machine_learning/LoVC/ephemeral-ai/ephemeral-os`

Review target:

- Milestone: Phase 2 Milestone 6.6, workspace profile symmetry.
- Primary spec:
  `docs/daemon/workspace_migration/phase-operation_service_workspace_session/phase_2_milestone_6_6_workspace_profile_symmetry_SPEC.md`
- Parent plan:
  `docs/daemon/workspace_migration/phase-operation_service_workspace_session/phase_2_command_service_IMPLEMENTATION_PLAN.md`
- Scope: specification quality, single responsibility, SOLID alignment, and
  whether host-compatible and isolated profiles differ only in network setup.

This is a review-only task. Do not edit files. Do not implement fixes. Do not
stage, commit, format, or run cleanup commands that mutate the checkout.

## Review Objective

Run an adversarial review of the Phase 2 Milestone 6.6 spec. The review should
stress the spec's responsibility boundaries and OOP-style Rust profile interface.

The final synthesis must lead with findings ordered by severity. Do not average
away a minority concern. If there is weak evidence, say so and avoid inventing a
bug.

The final synthesis must answer this question explicitly:

```text
Does the Phase 6.6 spec make host-compatible and isolated workspaces symmetric
for every concern except network setup?
```

Allowed answers:

- Yes, with evidence.
- No, with concrete asymmetries and required spec changes.
- Not yet provable, with the missing evidence listed.

For the final check, the intended answer is yes only if the spec makes these
concerns common and profile-neutral:

- holder process lifecycle;
- namespace FD ownership and projection;
- scratch directory lifecycle;
- cgroup creation, holder join, command join, teardown, and recovery cleanup;
- caller-owned workspace lifetime;
- capture/publish policy;
- command lifecycle;
- remountability;
- file-operation routing policy.

The only allowed profile-specific difference should be:

```text
HostCompatibleProfile
  host network access
  no isolated veth/DNS/net-ready setup

IsolatedProfile
  private network namespace
  veth/DNS/net-ready setup
```

## Required Common Context

Read these before reviewing:

- `docs/daemon/workspace_migration/phase-operation_service_workspace_session/phase_2_milestone_6_6_workspace_profile_symmetry_SPEC.md`
- `docs/daemon/workspace_migration/phase-operation_service_workspace_session/phase_2_command_service_IMPLEMENTATION_PLAN.md`
- `docs/daemon/workspace_migration/phase-operation_service_workspace_session/phase_2_command_service_SPEC.md`
- `docs/daemon/workspace_migration/phase-operation_service_workspace_session/phase_2_implementation_record.md`
- `crates/daemon/workspace/src/model.rs`
- `crates/daemon/workspace/src/profile/mod.rs`
- `crates/daemon/workspace/src/profile/common.rs`
- `crates/daemon/workspace/src/profile/host_compatible.rs`
- `crates/daemon/workspace/src/profile/isolated.rs`
- `crates/daemon/workspace/src/profile/host_workspace.rs`
- `crates/daemon/workspace/src/profile/handle.rs`
- `crates/daemon/workspace/src/profile/manager.rs`
- `crates/daemon/workspace/src/profile/resource_control.rs`
- `crates/daemon/workspace/src/lifecycle/create.rs`
- `crates/daemon/workspace/src/lifecycle/destroy.rs`
- `crates/daemon/workspace/src/lifecycle/recovery.rs`
- `crates/daemon/operation_service/src/command/exec.rs`
- `crates/daemon/operation_service/src/command/remount.rs`
- `crates/daemon/operation/src/command/service.rs`
- `crates/daemon/operation/src/command/prepare.rs`
- `crates/daemon/operation/src/command/finalize.rs`
- `crates/daemon/operation/src/command/registry.rs`
- `crates/daemon/operation/src/command/service/remount.rs`
- `crates/daemon/core/src/runtime/workspace.rs`
- `crates/daemon/core/src/op_adapter/files.rs`

Also record:

```text
git status --short
git diff --stat
git diff -- docs/daemon/workspace_migration/phase-operation_service_workspace_session/phase_2_milestone_6_6_workspace_profile_symmetry_SPEC.md docs/daemon/workspace_migration/phase-operation_service_workspace_session/phase_2_command_service_IMPLEMENTATION_PLAN.md
```

Do not attribute unrelated dirty files to Milestone 6.6 unless they affect the
scoped spec or the current profile design evidence.

## Hard Non-Goals To Enforce

Flag any of these if the spec requires, implies, or fails to forbid them:

- Treating host-compatible as inherently one-shot.
- Treating isolated as inherently persistent.
- Encoding publish/discard behavior in `WorkspaceProfileKind` or profile
  implementation.
- Encoding command lifecycle in `WorkspaceProfile`.
- Encoding file-operation routing policy in `WorkspaceProfile`.
- Encoding remount eligibility in host-compatible versus isolated target
  matching.
- Making `HostWorkspace` a permanent public abstraction.
- Adding a fake `IsolatedWorkspace` adapter only to mirror `HostWorkspace`.
- Making `network_mode` the target API for new profile code.
- Leaving cgroup behavior inside `IsolatedProfile`.
- Allowing host-compatible and isolated profiles to differ in holder, namespace
  FD, scratch, cgroup, remount, lifetime, capture, or command lifecycle behavior.
- Deferring hard asymmetry questions to Milestone 7 or 8 without Milestone 6.6
  acceptance criteria.

## Review Lane A: Single Responsibility And Domain Boundaries

Adversarial questions:

- Does `WorkspaceProfile` have exactly one reason to change:
  profile-specific environment setup?
- Does the spec keep holder lifecycle, cgroup lifecycle, scratch cleanup,
  command lifecycle, capture/publish, remount orchestration, file routing, and
  caller-owned lifetime outside `WorkspaceProfile`?
- Does `WorkspaceProfileContext` remain a value object for launch/inspection, or
  does it become a lifecycle aggregate?
- Does the target file structure create clean owners, or overlapping modules?
- Are compatibility shims explicitly temporary and policy-free?
- Are acceptance criteria concrete enough to prove responsibility separation?

Expected output:

- Findings with exact file and line references.
- A responsibility table with columns: concept, owner, should not own, evidence.
- Explicit statement if no SRP/domain-boundary findings were found.

## Review Lane B: SOLID And OOP Interface Soundness

Adversarial questions:

- Single Responsibility: does each trait method represent only profile-specific
  setup/teardown, or are common lifecycle/resource policies leaking in?
- Open/Closed: can a future profile be added without changing command policy,
  remount policy, file routing, or common holder/cgroup lifecycle?
- Liskov Substitution: can `HostCompatibleProfile` and `IsolatedProfile` run
  through the same lifecycle without callers branching by profile kind?
- Interface Segregation: is `WorkspaceProfile` too broad around global setup,
  resource policy, or teardown reporting?
- Dependency Inversion: do operation/core/operation_service depend on profile
  abstractions and profile contexts rather than concrete host/isolated modules?
- Are default no-op hooks safe, or could they hide required setup?
- Does teardown report enough information to avoid silent leaks?

Expected output:

- Findings with exact file and line references.
- A SOLID scorecard: principle, pass/fail/unclear, evidence, recommended spec
  change.
- Explicit statement if no SOLID findings were found.

## Review Lane C: Final Symmetry Check

Adversarial questions:

- Does the spec require one create/setup/teardown sequence for both profiles?
- Does it require one handle/context shape for both profiles?
- Does it require common holder lifecycle for both profiles?
- Does it require common cgroup behavior for both holder-backed profiles?
- Does it require common remountability for both profiles?
- Does it eliminate host-only construction as a target state?
- Does it avoid adding an isolated-only adapter just for naming symmetry?
- Does it make capture/publish behavior explicit policy outside the profile?
- Does it make caller-owned lifetime explicit outside the profile?
- Does it include `operation_service` in the review and verification surface so
  the current public command boundary cannot keep the old asymmetry?
- Is the only allowed profile difference host network access versus isolated
  network namespace/veth/DNS/net-ready setup?

Expected output:

- Findings with exact file and line references.
- A symmetry matrix with columns: concern, host-compatible target, isolated
  target, common owner, profile-specific difference yes/no.
- Explicit final answer: whether the profiles differ only in network setup after
  the spec target is applied.

## Required Verification Commands

Run these non-mutating commands and record exact pass/fail status:

```text
git status --short
git diff --stat
git diff --check -- docs/daemon/workspace_migration/phase-operation_service_workspace_session/phase_2_milestone_6_6_workspace_profile_symmetry_SPEC.md docs/daemon/workspace_migration/phase-operation_service_workspace_session/phase_2_command_service_IMPLEMENTATION_PLAN.md
rg -n "HostWorkspace|HostNamespaceWorkspaceRequest|WorkspaceModeContext|WorkspaceModeManager|ExecTarget::Host|ExecTarget::IsolatedNetwork|IsolatedNetworkError|network_mode" crates/daemon/workspace/src crates/daemon/operation/src crates/daemon/operation_service/src crates/daemon/core/src
rg -n "one.shot|one_shot|publish|published|remountable|cgroup|ResourcePolicy" crates/daemon/workspace/src/profile crates/daemon/operation/src/command crates/daemon/operation_service/src/command
```

Optional compile/test commands if the review includes implementation changes
after this prompt is reused later:

```text
cargo fmt --check
cargo check -p workspace
cargo test -p workspace
cargo check -p operation_service
cargo test -p operation_service command_exec
cargo test -p operation_service command_remount
cargo check -p operation
cargo test -p operation command
cargo check -p daemon
cargo test -p daemon workspace_runtime
```

## Final Synthesis Format

Return the review in this order:

1. Findings, ordered by severity.
   - Include exact file and line references.
   - Include the violated principle: SRP, OCP, LSP, ISP, DIP, policy leakage,
     lifecycle leak, or spec testability.
2. Final symmetry verdict.
   - Answer whether host-compatible and isolated differ only in network setup.
   - If no, list every remaining non-network difference the spec permits.
3. Required spec changes before implementation.
   - Keep each item concrete enough to patch.
4. Open questions.
   - Only include questions that block implementation or materially affect the
     architecture.
5. Verification results.
   - Include exact commands and pass/fail status.
6. Residual risk.
   - Include areas where the spec is acceptable but implementation could still
     drift.

If no issues are found, say that clearly and still provide the final symmetry
verdict and residual test/implementation risks.
