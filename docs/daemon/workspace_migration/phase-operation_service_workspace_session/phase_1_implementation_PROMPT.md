# Phase 1 Implementation Prompt

You are implementing Phase 1 of the operation-service workspace-session
migration in:

```text
/Users/yifanxu/machine_learning/LoVC/ephemeral-ai/ephemeral-os
```

Read these specs before editing:

```text
docs/daemon/workspace_migration/operation_service_workspace_session_SPEC.md
docs/daemon/workspace_migration/phase-operation_service_workspace_session/phase_1_workspace_service_workspace_manager_SPEC.md
```

## Objective

Implement the Phase 1 boundary split between:

```text
workspace::WorkspaceService
operation_service::workspace::WorkspaceManagerService
```

This phase creates the compileable module/API shape and focused tests for the
workspace manager. It must not perform the full daemon dispatch migration.

## Scope

Implement:

- `workspace::WorkspaceService` as the low-level resource service trait.
- `crates/daemon/operation_service` as a new crate.
- `operation_service::workspace::WorkspaceManagerService`.
- `operation_service::workspace::WorkspaceSessionManager`.
- `WorkspaceSession`, `WorkspaceSessionHandler`, and `WorkspaceManagerError`.
- A single session map keyed by `WorkspaceId`.
- Consistent lookup methods:
  - `find_by_workspace_id`
  - `find_by_caller_id`
  - `find_by_lease_id`
- Focused unit tests for session-manager behavior and manager lifecycle
  wrapper behavior.

Do not implement:

- `operation_service/src/ops/`
- `operation_service/src/run/`
- `operation_service/src/isolation/`
- Persistent session store.
- Recovery module.
- Pressure or maintenance module.
- Full `daemon/core` dispatch migration.
- Full command/file routing migration.
- Live E2E changes.

## Required Layout

Add:

```text
crates/daemon/operation_service/Cargo.toml
crates/daemon/operation_service/src/lib.rs
crates/daemon/operation_service/src/services.rs
crates/daemon/operation_service/src/error.rs
crates/daemon/operation_service/src/workspace/mod.rs
crates/daemon/operation_service/src/workspace/service.rs
crates/daemon/operation_service/src/workspace/session_manager.rs
crates/daemon/operation_service/src/workspace/error.rs
crates/daemon/operation_service/tests/workspace_manager.rs
```

Edit as needed:

```text
Cargo.toml
Cargo.lock
crates/daemon/workspace/src/service.rs
crates/daemon/workspace/src/model.rs
crates/daemon/workspace/src/lib.rs
```

Avoid broad edits under:

```text
crates/daemon/core/src/op_adapter/
crates/daemon/operation/src/command/
crates/daemon/operation/src/file/
crates/e2e-test/tests/
```

Only touch `daemon/core` if a narrow compile adapter is unavoidable.

## Contract Details

`workspace::WorkspaceService` is resource-facing. It must not own daemon
sessions, request routing, command cancellation, or workspace-run teardown.

Target shape:

```rust
pub trait WorkspaceService: Send + Sync {
    fn create_workspace(...);
    fn capture_changes(...);
    fn remount_workspace(...);
    fn destroy_workspace(...);
    fn latest_snapshot(...);
}
```

`WorkspaceManagerService` wraps `workspace::WorkspaceService` and owns daemon
open-workspace state:

```rust
pub struct WorkspaceManagerService {
    sessions: WorkspaceSessionManager,
    workspace: Arc<dyn workspace::WorkspaceService>,
}
```

`WorkspaceSessionManager` uses one primary map:

```rust
pub(crate) struct WorkspaceSessionManager {
    sessions: HashMap<WorkspaceId, WorkspaceSession>,
}
```

Implement:

```rust
insert
remove
find_by_workspace_id
find_by_caller_id
find_by_lease_id
```

Do not add secondary indexes in Phase 1.

## Lifecycle Rules

- `WorkspaceManagerService::create` calls
  `workspace::WorkspaceService::create_workspace`, then inserts a
  `WorkspaceSession`.
- If session insertion fails after raw workspace creation, destroy or schedule
  cleanup for the raw workspace before returning.
- `WorkspaceManagerService::destroy` marks or treats the session as closing,
  calls `workspace::WorkspaceService::destroy_workspace`, and removes the
  session only after successful destroy.
- If destroy fails, do not silently remove the live session.
- Isolated enter/exit are not separate services in this phase. They are modeled
  as `create(..., NetworkMode::Isolated)` and `destroy(...)`.
- Workspace-run end/cancel-all belongs to `WorkspaceManagerService`, but Phase 1
  only needs the module/API placement unless required for tests.

## Testing

Add focused tests for:

- insert and lookup by workspace id
- derived lookup by caller id
- derived lookup by lease id
- remove clears the primary map
- duplicate workspace id rejection
- caller ownership validation through `WorkspaceManagerService::resolve`
- create rollback when session insertion fails
- destroy failure retains the session
- successful destroy removes the session

Use fakes/mocks for `workspace::WorkspaceService`; do not require real mount,
namespace, or live daemon behavior.

## Verification

Run focused checks with an isolated target dir:

```text
CARGO_TARGET_DIR=/tmp/eos-phase1-op-service-target cargo check -p workspace
CARGO_TARGET_DIR=/tmp/eos-phase1-op-service-target cargo check -p operation_service
CARGO_TARGET_DIR=/tmp/eos-phase1-op-service-target cargo test -p operation_service workspace
cargo fmt --check
git diff --check
```

Do not run live E2E for Phase 1 unless the implementation unexpectedly changes
daemon runtime behavior. If daemon-side live E2E is later required, package
first with:

```text
cargo run -p xtask -- package
```

## Reporting

In the final implementation report, include:

- files added
- files edited
- what was intentionally deferred
- exact verification commands and results
- any compile adapters added outside the expected file list
- any divergence from this prompt or the Phase 1 spec
