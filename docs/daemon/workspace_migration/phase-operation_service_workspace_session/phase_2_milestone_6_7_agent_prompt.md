# Phase 2 Milestone 6.7 Agent Prompt

You are implementing Phase 2 Milestone 6.7 only in:

`/Users/yifanxu/machine_learning/LoVC/ephemeral-ai/ephemeral-os`

Milestone 6.7 is: rename the remaining workspace profile selector and carrier
surface from `NetworkMode` / `network` to `WorkspaceProfile` / `profile`, while
preserving the lower-level network namespace implementation vocabulary.

Phase 6.6 already established the behavioral model: host-compatible and
isolated workspaces are workspace profiles, and their only intended difference
is network setup. Phase 6.7 makes the code vocabulary match that model.

## First Rule

Inspect the live repo before editing. The worktree may already contain unrelated
changes. Do not revert, overwrite, or cleanup changes outside this milestone.

This milestone is allowed to touch code, focused tests, the parent plan, the
Phase 6.6/6.7 spec, and the implementation record. Keep edits tightly scoped to
the selector/carrier rename and directly required compatibility handling.

## Read First

Before code changes, read these files:

- `docs/daemon/workspace_migration/phase-operation_service_workspace_session/phase_2_milestone_6_6_workspace_profile_symmetry_SPEC.md`
- `docs/daemon/workspace_migration/phase-operation_service_workspace_session/phase_2_command_service_IMPLEMENTATION_PLAN.md`
- `docs/daemon/workspace_migration/phase-operation_service_workspace_session/phase_2_implementation_record.md`
- `crates/daemon/workspace/src/model.rs`
- `crates/daemon/workspace/src/lib.rs`
- `crates/daemon/workspace/src/profile/mod.rs`
- `crates/daemon/workspace/src/profile/common.rs`
- `crates/daemon/workspace/src/profile/handle.rs`
- `crates/daemon/workspace/src/profile/host_compatible.rs`
- `crates/daemon/workspace/src/profile/isolated.rs`
- `crates/daemon/workspace/src/lifecycle/create.rs`
- `crates/daemon/workspace/src/lifecycle/destroy.rs`
- `crates/daemon/workspace/src/lifecycle/recovery.rs`
- `crates/daemon/workspace/src/lifecycle/remount/apply.rs`
- `crates/daemon/operation_service/src/workspace_manager/service.rs`
- `crates/daemon/operation_service/src/workspace_manager/session_manager.rs`
- `crates/daemon/operation_service/src/command/exec.rs`
- `crates/daemon/core/src/runtime/workspace.rs`
- `crates/daemon/core/src/op_adapter/command.rs`
- `crates/daemon/core/src/op_adapter/files.rs`
- focused tests under `crates/daemon/workspace/tests`,
  `crates/daemon/operation_service/tests`, `crates/daemon/operation/tests`, and
  `crates/daemon/core/tests`

Also run and record the starting state:

```text
git status --short --untracked-files=all
git diff --stat
git diff --check
```

If the Phase 6.7 spec section is missing from
`phase_2_milestone_6_6_workspace_profile_symmetry_SPEC.md`, restore or add that
spec before code changes. Do not start implementation from an implicit chat-only
plan.

## Scope

Implement only:

- Public workspace selector rename from `NetworkMode` to `WorkspaceProfile`.
- Carrier field rename from `network` to `profile` on workspace resource
  contracts and internal handle/context shapes.
- Internal hook dispatcher rename away from `WorkspaceProfile<'a>` so the public
  selector owns the `WorkspaceProfile` name.
- Lifecycle/service API rename from `enter_with_network` /
  `create_private_workspace(..., network)` style names to profile terminology.
- Command launch validation updated to read `profile` and branch only for
  isolated launch requirements.
- Focused test updates for renamed selectors and carriers.
- Parent plan, Phase 6.7 spec, and implementation-record updates.

## Do Not Implement

- No daemon dispatch migration. That remains Milestone 7.
- No behavior change to host-compatible or isolated workspace setup.
- No new workspace profile variant.
- No new wire protocol shape.
- No broad docs rewrite outside Phase 2 migration docs.
- No conversion of truly network-specific lower layers to profile names.
- No reintroduction of the compatibility `network_mode` module path.
- No one-shot/session lifetime, capture/publish, command lifecycle, remount, or
  file-routing policy inside `WorkspaceProfile`.

## Naming Boundary

Rename workspace profile selectors and carriers:

| Before | After |
| --- | --- |
| `NetworkMode` | `WorkspaceProfile` |
| `NetworkMode::Host` | `WorkspaceProfile::HostCompatible` |
| `NetworkMode::Isolated` | `WorkspaceProfile::Isolated` |
| `WorkspaceHandle.network` | `WorkspaceHandle.profile` |
| `CreateWorkspaceRequest.network` | `CreateWorkspaceRequest.profile` |
| `WorkspaceModeHandle.network` | `WorkspaceModeHandle.profile` |
| `WorkspaceModeContext.network` | `WorkspaceModeContext.profile` |
| `WorkspaceHandleSpec.network` | `WorkspaceHandleSpec.profile` |
| `profile::common::WorkspaceProfile<'a>` | `WorkspaceProfileRuntime<'a>` or equivalent |
| `WorkspaceProfile::for_mode(...)` | `WorkspaceProfileRuntime::for_profile(...)` |
| `enter_with_network(...)` | `enter_with_profile(...)` |
| `handler.handle.network` | `handler.handle.profile` |

Keep lower-level network implementation names unchanged:

- `NamespaceNetwork`
- `NamespacePlan::host_workspace()`
- `NamespacePlan::isolated_network()`
- `WorkspaceLaunchNamespaceFds.net`
- `ns-holder host/isolated` arguments
- veth, DNS, net-ready, and isolated-network setup names

The rule is simple: `WorkspaceProfile` selects the workspace environment profile;
namespace and holder code still executes network namespace mechanics.

## Implementation Sequence

### 0. Open The Record

Before code changes, update
`docs/daemon/workspace_migration/phase-operation_service_workspace_session/phase_2_implementation_record.md`
under `Milestone 6.7: Workspace Profile Carrier Rename`:

- set status to in progress;
- list intended files;
- note that Phase 6.6 behavioral symmetry must remain unchanged;
- note whether a temporary `NetworkMode` compatibility alias is planned.

Also update the parent plan to list Phase 6.7 after Milestone 6.6 and before
Milestone 7 if it does not already do so.

### 1. Map Current Carrier Surface

Before editing code, classify current matches from:

```text
rg -n "NetworkMode|\\.network\\b|network:|enter_with_network|for_mode|network_mode" crates/daemon/workspace/src crates/daemon/operation_service/src crates/daemon/core/src crates/daemon/operation/src
rg -n "WorkspaceProfile<'|enum WorkspaceProfile<'|trait WorkspaceProfile" crates/daemon/workspace/src/profile
rg -n "NetworkMode::Host|NetworkMode::Isolated|network mode|network_mode" docs/daemon/workspace_migration/phase-operation_service_workspace_session
```

Do not treat every match as a bug. Classify each remaining match as target code,
lower-level network implementation, temporary compatibility, historical docs,
test fixture, or bug.

### 2. Rename The Public Selector

In `crates/daemon/workspace/src/model.rs`:

- replace `pub enum NetworkMode` with `pub enum WorkspaceProfile`;
- prefer variants `HostCompatible` and `Isolated`;
- move the existing policy-free documentation onto `WorkspaceProfile`;
- update public re-exports in `workspace/src/lib.rs`.

If incremental compilation requires a bridge, use only a short-lived alias:

```rust
#[deprecated(note = "use WorkspaceProfile")]
pub type NetworkMode = WorkspaceProfile;
```

The alias is allowed only if the implementation record explains why it remains
and when it will be removed. New code must use `WorkspaceProfile`.

### 3. Rename The Internal Hook Dispatcher

In `crates/daemon/workspace/src/profile/common.rs`:

- rename the internal enum currently named `WorkspaceProfile<'a>` to
  `WorkspaceProfileRuntime<'a>` or an equivalent non-conflicting name;
- rename `for_mode` to `for_profile`;
- keep `ProfileHooks` private and policy-free;
- update profile mismatch error text to use profile terminology.

The public selector and the runtime hook dispatcher must not share the same type
name.

### 4. Rename Carrier Fields

Rename carrier fields from `network` to `profile` in:

- `WorkspaceHandle`
- `CreateWorkspaceRequest`
- `WorkspaceModeHandle`
- `WorkspaceModeContext`
- `WorkspaceHandleSpec`
- conversion code from internal handles to public handles
- debug output
- operation-service fake workspace helpers and assertions

Handle persisted JSON deliberately. If persisted handles currently serialize
`network`, choose and record one approach:

- read old `network` while writing `profile`;
- add a schema-version migration;
- prove persisted handle compatibility is out of scope for this phase.

### 5. Rename Lifecycle And Service APIs

Update profile terminology in lifecycle and operation-service APIs:

- `enter_with_network` -> `enter_with_profile`;
- `create_private_workspace(..., network)` parameter -> `profile`;
- one-shot command creation should request `WorkspaceProfile::HostCompatible`;
- isolated enter should request `WorkspaceProfile::Isolated`;
- command launch validation should read `handler.handle.profile`.

Do not change behavior while renaming.

### 6. Preserve Lower Network Layers

Do not rename network-specific implementation code merely because it contains
the word network. Keep these names when they model network mechanics:

- `NamespaceNetwork`
- `NamespacePlan::isolated_network`
- `WorkspaceLaunchNamespaceFds.net`
- `IsolatedNetwork`
- veth, DNS, bridge, net-ready, holder network args

If a static scan still finds `network` in these layers, classify it as accepted
lower-level network implementation.

### 7. Update Tests And Docs

Update focused tests first, then production code, then docs:

- workspace model and session tests;
- operation-service command exec and remount tests;
- operation-service shared fake workspace support;
- operation/core tests that build workspace contexts;
- Phase 2 migration docs and implementation record.

Avoid updating generated e2e HTML/JSON unless the normal repo workflow expects
those generated files to be checked in for this docs change.

## Verification

Run and record:

```text
CARGO_TARGET_DIR=/tmp/eos-phase2-command-service-target cargo check -p workspace
CARGO_TARGET_DIR=/tmp/eos-phase2-command-service-target cargo test -p workspace
CARGO_TARGET_DIR=/tmp/eos-phase2-command-service-target cargo check -p operation_service
CARGO_TARGET_DIR=/tmp/eos-phase2-command-service-target cargo test -p operation_service command_exec
CARGO_TARGET_DIR=/tmp/eos-phase2-command-service-target cargo test -p operation_service command_remount
CARGO_TARGET_DIR=/tmp/eos-phase2-command-service-target cargo test -p operation_service workspace_remount
CARGO_TARGET_DIR=/tmp/eos-phase2-command-service-target cargo check -p daemon
cargo fmt --check
git diff --check
```

Run static evidence scans and document remaining matches:

```text
rg -n "NetworkMode|\\.network\\b|network:|enter_with_network|for_mode|network_mode" crates/daemon/workspace/src crates/daemon/operation_service/src crates/daemon/core/src crates/daemon/operation/src
rg -n "WorkspaceProfile<'|enum WorkspaceProfile<'|trait WorkspaceProfile" crates/daemon/workspace/src/profile
rg -n "NetworkMode::Host|NetworkMode::Isolated|network mode|network_mode" docs/daemon/workspace_migration/phase-operation_service_workspace_session
```

Every remaining match must be classified as lower-level network implementation,
temporary compatibility, historical documentation, test fixture, or bug.

## Acceptance Checklist

- [ ] Parent implementation plan lists Phase 6.7 after Milestone 6.6 and before
  Milestone 7.
- [ ] Public workspace selector is named `WorkspaceProfile`.
- [ ] `NetworkMode` is removed from production target code, or remains only as a
  documented temporary alias with removal criteria.
- [ ] Workspace resource carriers use `profile`, not `network`.
- [ ] Internal hook dispatcher no longer occupies the public `WorkspaceProfile`
  name.
- [ ] Command launch validation reads `profile` and branches only for isolated
  launch requirements.
- [ ] Lower-level network namespace implementation names remain network-specific.
- [ ] No new code imports the compatibility `network_mode` module path.
- [ ] Persisted-handle compatibility is preserved intentionally or explicitly
  declared out of scope with evidence.
- [ ] Phase 6.6 behavioral symmetry remains unchanged.
- [ ] Implementation record is updated with exact verification results and any
  remaining compatibility shims.

## Final Response

Report:

- changed files;
- the selector/carrier rename path implemented;
- any temporary `NetworkMode` compatibility left in place and removal criteria;
- persisted-handle compatibility decision;
- verification commands and pass/fail status;
- remaining risks or follow-up blockers for Milestone 7.
