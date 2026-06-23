# Workspace Session CLI Operations Spec

Status: draft implementation spec

Crate path: `crates/sandbox-runtime/operation`

Package: `sandbox-runtime`

## Problem

`WorkspaceSessionService` already owns the runtime workspace-session lifecycle:

- create a workspace session;
- resolve a session by `workspace_session_id`;
- capture session changes;
- destroy a session;
- coordinate remount state transitions.

Only command and layerstack operations are currently exposed through the runtime
CLI catalog. A user can run `exec_command` without `workspace_session_id` and
get an implicit one-shot host-compatible workspace, or pass an existing
`workspace_session_id`, but there is no direct CLI operation for creating a
longer-lived workspace session before command execution.

The target change is to expose stable CLI operations for user-owned workspace
session creation and destruction without exposing internal remount, capture,
or handler details.

Only `create_workspace_session` and `destroy_workspace_session` become CLI
operations in this change. Other `WorkspaceSessionService` methods remain
Rust-only service APIs unless a later feature introduces a separate, stable
operation contract for them.

## Goals

- Add `create_workspace_session` as a runtime CLI operation.
- Let users choose `host_compatible` or `isolated` workspace profile from the
  CLI.
- Add `destroy_workspace_session` as the paired runtime CLI operation.
- Keep `workspace_session_id` as the public identity boundary.
- Keep `WorkspaceSessionHandler` an internal Rust lifecycle token.
- Keep `sandbox-cli` request building catalog-driven through
  `CliOperationSpec`.
- Preserve the existing create rollback and destroy-retains-session-on-failure
  behavior.
- Reject user-requested destroy while the workspace session still has active
  command sessions.
- Add `WorkspaceSessionService` as an explicit peer on
  `SandboxRuntimeOperations`, while still passing the same `Arc` into
  `CommandOperationService`.
- Keep `WorkspaceSessionService` and its `service/impls` files free of CLI
  protocol, operation registry, and command-service imports.
- Keep workspace-session CLI/protocol adapters outside the
  `workspace_session/**` lifecycle module.

## Non-Goals

- Do not expose `resolve_session` as a CLI operation.
- Do not expose `capture_session_changes` as a CLI operation in this change.
- Do not expose remount substeps: `begin_remount`, `apply_and_finish_remount`,
  `block_remount`, or `refresh_after_publish`.
- Do not change `exec_command` one-shot behavior.
- Do not add wait, cancel, or force-destroy semantics for active commands in
  this change.
- Do not add compatibility aliases for alternate operation names.
- Do not introduce a `{ result, meta }` response envelope.
- Do not move workspace lifecycle ownership into `command`.
- Do not place `CliOperationSpec`, `OperationEntry`, `Request`, or `Response`
  code inside `workspace_session/service/impls`.
- Do not expose general-purpose `CommandOperationService` accessors just so
  workspace-session CLI dispatch can reach private command internals.
- Do not route workspace-session CLI operations through
  `CommandOperationService::workspace()`.

## Current State

`create_workspace_session` is a service method, not a CLI operation:

```rust
pub fn create_workspace_session(
    &self,
    request: CreateWorkspaceRequest,
) -> Result<WorkspaceSessionHandler, WorkspaceSessionError>
```

The method creates a raw workspace, inserts the canonical session into the
session map, and rolls back the raw workspace if session insertion fails.

`destroy_session` is also a service method:

```rust
pub fn destroy_session(
    &self,
    handler: WorkspaceSessionHandler,
    request: DestroyWorkspaceRequest,
) -> Result<DestroyWorkspaceResult, WorkspaceSessionError>
```

It intentionally takes a `WorkspaceSessionHandler`. That handler should remain
the internal lifecycle token. A CLI caller should only provide
`workspace_session_id`; dispatch should resolve the handler before destroy.

`WorkspaceProfile` already has the required profile split:

```rust
pub enum WorkspaceProfile {
    HostCompatible,
    Isolated,
}
```

The profile wire names should be:

- `host_compatible`;
- `isolated`.

These names match `WorkspaceProfile::as_str()`.

`SandboxRuntimeOperations` currently exposes command and layerstack services:

```rust
pub struct SandboxRuntimeOperations {
    pub command: Arc<CommandOperationService>,
    pub layerstack: Arc<LayerStackService>,
}
```

The target shape should add workspace-session as a peer service, not as a value
looked up through command:

```rust
pub struct SandboxRuntimeOperations {
    pub command: Arc<CommandOperationService>,
    pub workspace_session: Arc<WorkspaceSessionService>,
    pub layerstack: Arc<LayerStackService>,
}
```

`from_config` must construct one `Arc<WorkspaceSessionService>`, pass a clone
into `CommandOperationService`, and store the same `Arc` on the aggregate.

The operation registry currently collects operation entries from:

- `command::operation_entries()`;
- `layerstack::operation_entries()`.

Command execution stores active command records by `workspace_session_id`.
`destroy_workspace_session` must check that command state before destroying a
user-owned session. The safe launch behavior is to reject destroy when active
commands remain, not to remove the workspace out from under running processes.
Because live `exec_command` currently resolves the workspace before it inserts
an active command record, the destroy admission contract must also exclude
existing-session command launches before they resolve the session.

The existing workspace-session service is intentionally command-agnostic. Keep
that boundary: new CLI operation adapters may compose command and workspace
services, but `workspace_session/service/**` must continue not to import
`crate::command`.

## Target CLI Surface

The usage strings and help examples intentionally follow the existing runtime
catalog convention and omit `--sandbox-id`. Executable invocations still require
a runtime sandbox scope supplied through `--sandbox-id`, `--default-sandbox-id`,
or `SANDBOX_DEFAULT_ID`.

### Create Workspace Session

Operation name:

```text
create_workspace_session
```

CLI:

```text
sandbox-cli runtime create_workspace_session [--profile host_compatible|isolated]
```

Arguments:

| Argument | Kind | Required | Default | CLI |
| --- | --- | --- | --- | --- |
| `profile` | string | no | runtime defaults to `host_compatible` | `--profile PROFILE` |

Examples:

```text
sandbox-cli runtime create_workspace_session
sandbox-cli runtime create_workspace_session --profile host_compatible
sandbox-cli runtime create_workspace_session --profile isolated
```

Accepted profile values:

- `host_compatible`;
- `isolated`.

Invalid values must return `invalid_request`, not silently fall back.

Dispatch must:

1. parse optional `profile`;
2. default missing `profile` to `WorkspaceProfile::HostCompatible`;
3. reject unknown, empty, or non-string profile values with `invalid_request`;
4. construct `CreateWorkspaceRequest { profile }`;
5. call `WorkspaceSessionService::create_workspace_session`.

The CLI catalog should not set `ArgSpec.default` for `profile`. Runtime dispatch
is the source of truth for the default; the help text can still describe the
default in prose.

Response:

```json
{
  "workspace_session_id": "ws-...",
  "profile": "host_compatible"
}
```

`workspace_session_id` is the public identity boundary. `workspace_root`,
`base_revision`, namespace fds, leases, and remount details remain internal or
observability data and should not become part of this create response.

### Destroy Workspace Session

Operation name:

```text
destroy_workspace_session
```

CLI:

```text
sandbox-cli runtime destroy_workspace_session --workspace-session-id ID [--grace-s SECONDS]
```

Arguments:

| Argument | Kind | Required | Default | CLI |
| --- | --- | --- | --- | --- |
| `workspace_session_id` | string | yes | none | `--workspace-session-id ID` |
| `grace_s` | float | no | none | `--grace-s SECONDS` |

Examples:

```text
sandbox-cli runtime destroy_workspace_session --workspace-session-id ws-1
sandbox-cli runtime destroy_workspace_session --workspace-session-id ws-1 --grace-s 2.5
```

Dispatch must:

1. parse `workspace_session_id`;
2. parse `grace_s` as an optional finite, non-negative duration;
3. call `CommandOperationService::begin_workspace_destroy_admission`;
4. inspect the returned active command ids;
5. return `operation_failed` without resolving or calling workspace destroy if
   any active command sessions remain;
6. resolve the session through `operations.workspace_session.resolve_session`;
7. construct `DestroyWorkspaceRequest { grace_s }`;
8. call the existing `destroy_session(handler, request)` while the destroy
   admission guard is still held.

Response:

```json
{
  "workspace_session_id": "ws-...",
  "destroyed": true
}
```

`DestroyWorkspaceResult` contains teardown diagnostics such as evicted bytes and
lease-release status. Those are lifecycle/observability details and should not
become part of the CLI contract for this operation.

Active-command rejection should include details that make the refusal actionable:

```json
{
  "active_command_session_ids": ["cmd-1", "cmd-2"]
}
```

## Operation Family

Add a runtime CLI family in the operation adapter module, not in
`workspace_session/mod.rs`:

```rust
pub(crate) const WORKSPACE_SESSION_FAMILY: CliOperationFamilySpec =
    CliOperationFamilySpec {
        id: "workspace_session",
        title: "Workspace Session",
        summary: "Create and destroy runtime workspace sessions.",
        description: "Create and destroy user-owned runtime workspace sessions.",
    };
```

The family should be returned from `cli_operation_families()` only because it
has CLI-visible operations. It must not imply that every
`WorkspaceSessionService` method is CLI-visible, and it must not turn
`workspace_session` into a catalog/help module.

## CLI Adapter Wiring

Add one private operation adapter module, for example
`src/workspace_session_operations.rs`. Do not add `workspace_session/cli/` and
do not put these adapters under `workspace_session/service/impls/`.

```rust
const CREATE_WORKSPACE_SESSION: OperationEntry =
    OperationEntry::cli(&CREATE_SPEC, dispatch_create_workspace_session);

const DESTROY_WORKSPACE_SESSION: OperationEntry =
    OperationEntry::cli(&DESTROY_SPEC, dispatch_destroy_workspace_session);

pub(crate) const OPERATIONS: &[OperationEntry] = &[
    CREATE_WORKSPACE_SESSION,
    DESTROY_WORKSPACE_SESSION,
];
```

Expose only the operation registry through that adapter module:

```rust
pub(crate) fn operation_entries() -> &'static [crate::operation::OperationEntry] {
    OPERATIONS
}
```

The operation adapter may depend on:

- `SandboxRuntimeOperations`;
- `WorkspaceSessionService` methods reached through
  `operations.workspace_session`;
- the explicit command-owned destroy-admission API described below.

It must not add CLI/parser/response code to `workspace_session/**`.

Update the runtime operation registry:

```rust
const CLI_FAMILIES: &[&CliOperationFamilySpec] = &[
    &command::COMMAND_FAMILY,
    &workspace_session_operations::WORKSPACE_SESSION_FAMILY,
    &layerstack::LAYERSTACK_FAMILY,
];

fn operation_entry_groups() -> [&'static [OperationEntry]; 3] {
    [
        command::operation_entries(),
        workspace_session_operations::operation_entries(),
        layerstack::operation_entries(),
    ]
}
```

Add dispatch and service span labels:

```rust
"create_workspace_session" => "create_workspace_session::dispatch",
"destroy_workspace_session" => "destroy_workspace_session::dispatch",
```

The dispatch functions should also wrap the service calls with coarse spans that
match the existing runtime operation convention:

```rust
"WorkspaceSessionService::create_workspace_session"
"WorkspaceSessionService::destroy_session"
```

## Runtime Aggregate Access

Make `WorkspaceSessionService` explicit in `SandboxRuntimeOperations` because
workspace-session CLI operations are lifecycle operations, not command
operations:

```rust
pub struct SandboxRuntimeOperations {
    pub command: Arc<CommandOperationService>,
    pub workspace_session: Arc<WorkspaceSessionService>,
    pub layerstack: Arc<LayerStackService>,
}
```

`from_config` already constructs one `Arc<WorkspaceSessionService>` and passes
it into `CommandOperationService`. Store that same `Arc` on the aggregate and
pass a clone into command. Do not accept or construct another
`Arc<WorkspaceSessionService>`.

Do not add `WorkspaceRemountService` to `SandboxRuntimeOperations` for this
change. The aggregate is for services directly reached by runtime operation
dispatch; remount remains an internal orchestration service because no remount
operation is being exposed.

Do not use `CommandOperationService` as a generic service locator for command
internals. Workspace-session CLI dispatch may reach command only through:

- the explicit destroy-admission API below.

This is dispatch wiring only. Workspace lifecycle methods still live on
`WorkspaceSessionService`, and command remains responsible for process startup,
active command records, and command finalization.

## Destroy Admission

`destroy_workspace_session` must not destroy a workspace session that still has
active commands. Active command records are the current source of truth for
workspace-session membership, so destroy must consult the command process store
before calling raw workspace destroy. Active command records alone are not
enough, because live command launch resolves an existing workspace before it
inserts the active command record. The admission contract must therefore also
exclude existing-session command launch before session resolution.

Rename the underlying concept from remount-specific admission to workspace
lifecycle admission. The lock may remain physically stored in
`CommandOperationService` for this change, but the public crate-local API should
not expose it as `remount_admission`.

Add one command-owned helper:

```rust
pub(crate) struct WorkspaceDestroyAdmission<'a> {
    pub active_command_session_ids: Vec<CommandSessionId>,
    _guard: MutexGuard<'a, ()>,
}

impl CommandOperationService {
    pub(crate) fn begin_workspace_destroy_admission(
        &self,
        workspace_session_id: &WorkspaceSessionId,
    ) -> WorkspaceDestroyAdmission<'_> {
        // Lock workspace lifecycle admission, then snapshot active commands.
        // Existing-session exec_command must take the same admission before
        // resolving the workspace session and hold it until the command is
        // active or launch cleanup is complete.
    }
}
```

Update existing-session `exec_command` so it enters the same lifecycle
admission before `WorkspaceSessionService::resolve_session` and holds it until
the command is inserted into the active process store, or until launch failure
cleanup completes. This is the minimal live-code change that makes destroy
admission true instead of advisory.

The operation must keep `WorkspaceDestroyAdmission` alive while it:

1. inspects the captured active command ids for `workspace_session_id`;
2. rejects the request when the set is non-empty;
3. resolves the workspace-session handler;
4. calls `WorkspaceSessionService::destroy_session`.

The guard prevents this race:

1. `exec_command --workspace-session-id` resolves a handler but has not inserted
   an active command record yet;
2. destroy checks active commands and finds none;
3. destroy removes the workspace underneath the pending command launch.

Active-command rejection must leave the workspace session intact and must not
cancel commands. Command cancellation or coordinated forced destroy is a
separate operation and is out of scope for this change.

This keeps the ownership simple:

- `WorkspaceSessionService` owns workspace lifecycle state and raw create/destroy;
- `CommandOperationService` owns command process membership and launch/destroy
  exclusion;
- `workspace_session_operations` composes the two for the CLI operation.

## Response Projection

Create response projection should be local to the create operation module.
It should not expose `WorkspaceSessionHandler` directly.

Suggested helper:

```rust
fn create_workspace_session_value(handler: WorkspaceSessionHandler) -> Value {
    json!({
        "workspace_session_id": handler.workspace_session_id.0,
        "profile": handler.handle.profile.as_str(),
    })
}
```

Destroy response projection should expose only the public operation result:

```rust
fn destroy_workspace_session_value(result: DestroyWorkspaceResult) -> Value {
    json!({
        "workspace_session_id": result.workspace_session_id.0,
        "destroyed": true,
    })
}
```

## Error Mapping

Request parse failures:

- missing required `workspace_session_id` -> `invalid_request`;
- empty required `workspace_session_id` -> `invalid_request`;
- non-string `profile` -> `invalid_request`;
- unknown `profile` -> `invalid_request`;
- non-finite `grace_s` -> `invalid_request`;
- negative `grace_s` -> `invalid_request`.

Service failures:

- active commands exist for `workspace_session_id` -> `operation_failed` with
  `active_command_session_ids` details and no destroy call;
- `WorkspaceSessionError::NotFound` -> `operation_failed`;
- duplicate session id during create -> `operation_failed`;
- create rollback failure -> `operation_failed` with details if useful;
- destroy workspace failure -> `operation_failed` and retain the session.

The response layer should follow the existing runtime convention:

```rust
Response::fault_with_details("operation_failed", error.to_string(), details)
```

## Implementation Plan

### Phase 1: CLI Metadata and Dispatch

- Add `CREATE_SPEC`, `DESTROY_SPEC`, arg specs, CLI examples, parse helpers,
  dispatch, and response projection to the private operation adapter module.
- Add the destroy admission guard to `dispatch_destroy_workspace_session`
  before resolving and destroying the workspace session.
- Add coarse service spans for both dispatch functions.
- Keep existing service methods intact.

### Phase 2: Workspace-Session Operation Family

- Add `WORKSPACE_SESSION_FAMILY` and `operation_entries()` to the private
  operation adapter module.
- Keep all `workspace_session/**` files free of operation registry, request
  parsing, response projection, and command imports.
- Add the private adapter module to the crate root and update `operation.rs` to
  include the new family and operation group.

### Phase 3: Runtime Aggregate and Destroy Admission

- Add `workspace_session: Arc<WorkspaceSessionService>` to
  `SandboxRuntimeOperations` and store the same `Arc` that is passed into
  `CommandOperationService`.
- Dispatch create and destroy through `operations.workspace_session`.
- Add `CommandOperationService::begin_workspace_destroy_admission`.
- Rename the internal guard concept from remount-only admission to workspace
  lifecycle admission, or at minimum expose the new helper using lifecycle
  naming.
- Reject destroy using the active command ids returned by that helper.
- Hold `WorkspaceDestroyAdmission` across session resolution and raw workspace
  destroy.
- Update existing-session `exec_command` to take the same admission before
  resolving the workspace session and hold it through active-record insertion or
  launch cleanup.

### Phase 4: Gateway, Dispatch, and Help Tests

No bespoke gateway parser should be added. The gateway should pick up the new
operations through `runtime_catalog_document()`.

Add gateway request-builder and help tests that prove:

- `create_workspace_session --profile isolated` maps to
  `{"profile":"isolated"}`;
- `create_workspace_session` maps to `{}`;
- `destroy_workspace_session --workspace-session-id ws-1 --grace-s 2.5` maps
  to `{"workspace_session_id":"ws-1","grace_s":2.5}`;
- runtime help includes the `Workspace Session` family;
- runtime help detail renders both new operation usage blocks without
  `--sandbox-id`.

Add runtime dispatch tests that prove:

- create defaults to `WorkspaceProfile::HostCompatible` and returns only
  `workspace_session_id` plus `profile`;
- create with `profile=isolated` creates `WorkspaceProfile::Isolated`;
- unknown, non-string, and empty profile values return `invalid_request`;
- destroy with a missing, empty, non-string, or unknown `workspace_session_id`
  does not call raw workspace destroy;
- destroy with non-finite or negative `grace_s` returns `invalid_request`;
- destroy returns `operation_failed` with `active_command_session_ids` and does
  not call raw workspace destroy when active commands exist;
- destroy cannot race with an existing-session `exec_command` that has resolved
  the session but has not inserted an active command record yet;
- destroy success removes the session and returns only `workspace_session_id`
  plus `destroyed`;
- destroy workspace failure returns `operation_failed` and retains the session.

Add source-boundary tests that prove:

- `workspace_session/**` does not import `Request`, `Response`,
  `CliOperationSpec`, `OperationEntry`, or `CommandOperationService`;
- workspace-session operation dispatch reaches lifecycle methods through
  `operations.workspace_session`, not `operations.command.workspace()`;
- `SandboxRuntimeOperations` contains `workspace_session` for direct dispatch
  and does not add `workspace_remount` for this change.

Add operation trace tests that prove the selected span set includes:

- `create_workspace_session::dispatch`;
- `WorkspaceSessionService::create_workspace_session`;
- `destroy_workspace_session::dispatch`;
- `WorkspaceSessionService::destroy_session`.

## Acceptance Criteria

- Runtime CLI families include `command`, `workspace_session`, and
  `layerstack`.
- Runtime CLI operations include:
  - `exec_command`;
  - `write_command_stdin`;
  - `read_command_lines`;
  - `create_workspace_session`;
  - `destroy_workspace_session`;
  - `squash`.
- `resolve_session`, `capture_session_changes`, remount substeps, process-store
  helpers, transcript internals, and finalization helpers are absent from the
  CLI catalog.
- `workspace_session/**` remains free of CLI protocol, operation
  registry, response projection, and command-service imports.
- `create_workspace_session --profile isolated` creates a session with
  `WorkspaceProfile::Isolated`.
- `create_workspace_session` defaults to `WorkspaceProfile::HostCompatible`.
- omitted `--profile` is defaulted by runtime dispatch, not by gateway request
  construction.
- `create_workspace_session` response exposes only `workspace_session_id` and
  `profile`.
- Invalid profile values return `invalid_request`.
- Invalid `grace_s` values, including negative values, return
  `invalid_request`.
- `destroy_workspace_session` destroys by `workspace_session_id` and does not
  expose `WorkspaceSessionHandler` to the CLI.
- `destroy_workspace_session` rejects active-command sessions with
  `operation_failed`, includes active command ids in details, and does not call
  raw workspace destroy.
- `destroy_workspace_session` response exposes only `workspace_session_id` and
  `destroyed`.
- Destroy admission holds the lifecycle guard across active-command lookup,
  session resolution, and raw workspace destroy.
- Existing-session `exec_command` takes the same lifecycle admission before
  resolving the workspace session and holds it through active-record insertion
  or launch cleanup.
- Destroy failure retains the session.
- CLI create/destroy and `exec_command --workspace-session-id` use the same
  `WorkspaceSessionService` instance stored on `SandboxRuntimeOperations`.
- `SandboxRuntimeOperations` does not add `WorkspaceRemountService` for this
  change.
- `CommandOperationService` exposes only the named destroy-admission helper for
  command-state coordination; workspace-session CLI dispatch does not use
  `workspace()`, process-store, or admission-lock accessors directly.
- Runtime trace output includes dispatch and service-method spans for both new
  operations.
- Runtime help usage and examples do not include `--sandbox-id`.
- Existing `exec_command` one-shot behavior remains unchanged.

## Verification

Run formatting:

```sh
cargo fmt --all
```

Run focused runtime checks:

```sh
cargo test -p sandbox-runtime --test service_graph
cargo test -p sandbox-runtime --test workspace_session
cargo test -p sandbox-runtime --test exec_command
cargo test -p sandbox-runtime --test workspace_remount
cargo test -p sandbox-runtime --test operation_trace
```

Run gateway CLI checks:

```sh
cargo test -p sandbox-gateway --test gateway_cli
```

Run package checks:

```sh
cargo check -p sandbox-runtime -p sandbox-gateway --all-targets
```

If the implementation touches shared protocol catalog behavior, also run:

```sh
cargo test -p sandbox-protocol
```
