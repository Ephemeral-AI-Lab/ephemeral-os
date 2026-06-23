# Operation Services Migration Spec

Status: draft

This document specifies the migration of `sandbox-runtime` operation services
away from the current `public` / `internal` source layout and toward explicit
operation registration, CLI catalog metadata, and Rust visibility boundaries.

Crate path: `crates/sandbox-runtime/operation`

Package: `sandbox-runtime`

## Problem

The current source layout separates operation services into:

```text
src/public
src/internal
```

That split is misleading because it mixes several different concepts:

- Rust API visibility
- daemon protocol dispatch
- CLI exposure through `sandbox-cli`
- help/catalog grouping
- implementation detail ownership

Some services currently under `internal` are legitimate user-facing operations.
For example, `LayerStackService::squash` is a stable operation and should be
available through the CLI. Conversely, not every externally useful Rust service
method should become a CLI command.

The migration must remove the directory-level implication that `public` means
CLI-visible and `internal` means non-CLI.

## Goals

- Merge `src/public` and `src/internal` into neutral operation-service modules.
- Make CLI exposure explicit through `CliOperationSpec`.
- Make CLI family metadata explicit through `CliOperationFamilySpec`.
- Allow protocol dispatch to support operations that are not CLI commands.
- Keep `sandbox-cli` help and request building driven by CLI catalog metadata.
- Keep Rust API visibility controlled by `pub`, `pub(crate)`, and crate-root
  re-exports.
- Add `squash` as a runtime CLI operation under a `layerstack` family.

## Non-Goals

- Do not redesign the low-level runtime support crates.
- Do not change the wire shape of existing command operation responses.
- Do not add compatibility aliases for old module paths.
- Do not expose remount substeps, process-store internals, transcript internals,
  or command finalization helpers as CLI operations.
- Do not use `CliOperationFamilySpec` as a service architecture marker.

## Current State

The crate root currently has separate `public` and `internal` modules:

```rust
mod internal;
mod operation;
mod public;

pub use internal::{layerstack, workspace_remount, workspace_session};
pub use public::command;
```

Runtime operation catalog and dispatch currently route through `public`:

```rust
pub fn cli_operation_specs() -> &'static [&'static CliOperationSpec] {
    public::cli_operation_specs()
}

pub fn cli_operation_families() -> &'static [&'static CliOperationFamilySpec] {
    public::cli_operation_families()
}

pub fn dispatch_operation(
    operations: &SandboxRuntimeOperations,
    request: &sandbox_protocol::Request,
) -> sandbox_protocol::Response {
    public::dispatch_operation(operations, request)
}
```

`OperationEntry` currently stores a `CliOperationSpec` directly:

```rust
pub(crate) struct OperationEntry {
    pub(crate) spec: &'static CliOperationSpec,
    pub(crate) dispatch:
        fn(&SandboxRuntimeOperations, &sandbox_protocol::Request) -> sandbox_protocol::Response,
}
```

This couples dispatchability to CLI metadata. The target design must decouple
those concepts.

## Target Model

The target model has three independent axes.

### Rust Service API

Rust visibility is controlled by normal Rust visibility rules:

- `pub` for crate exports that callers are expected to use.
- `pub(crate)` for cross-module implementation details.
- private items for module-local implementation details.
- crate-root re-exports for the intended package API.

Module path does not imply CLI exposure.

### Protocol Operation Dispatch

Protocol dispatch is controlled by an operation registry. A dispatchable
operation has:

- an operation name
- a dispatch function
- optional CLI metadata

Dispatch searches all registered operation entries, including entries that are
not CLI-visible.

### CLI Operation Catalog

CLI catalog exposure is controlled only by `CliOperationSpec`.

An operation appears in `sandbox-cli runtime help` only when it is returned from
`cli_operation_specs()` and has `cli: Some(CliSpec { ... })`.

`CliOperationFamilySpec` is only a help/catalog grouping. A family exists in
the CLI catalog only when at least one CLI-visible operation uses that family
id.

## Target Source Layout

Move operation service modules to neutral top-level paths:

```text
src/command
src/layerstack
src/workspace_session
src/workspace_remount
src/services.rs
src/operation.rs
```

Delete:

```text
src/public
src/internal
```

The crate root should re-export the intended Rust service API explicitly:

```rust
mod command;
mod layerstack;
mod operation;
mod services;
mod workspace_remount;
mod workspace_session;

pub use command::CommandOperationService;
pub use layerstack::LayerStackService;
pub use services::{SandboxRuntimeConfig, SandboxRuntimeOperations};
pub use workspace_remount::WorkspaceRemountService;
pub use workspace_session::WorkspaceSessionService;
```

The exact exported type set should follow the current crate-root API and only
change when the migration intentionally tightens visibility.

## Operation Registry

Replace the current `OperationEntry` shape with a registry entry that does not
require CLI metadata:

```rust
pub(crate) struct OperationEntry {
    pub(crate) name: &'static str,
    pub(crate) cli: Option<&'static CliOperationSpec>,
    pub(crate) dispatch:
        fn(&SandboxRuntimeOperations, &sandbox_protocol::Request) -> sandbox_protocol::Response,
}
```

Provide constructors to make intent obvious:

```rust
impl OperationEntry {
    pub(crate) const fn cli(
        spec: &'static CliOperationSpec,
        dispatch: fn(&SandboxRuntimeOperations, &Request) -> Response,
    ) -> Self {
        Self {
            name: spec.name,
            cli: Some(spec),
            dispatch,
        }
    }

    pub(crate) const fn non_cli(
        name: &'static str,
        dispatch: fn(&SandboxRuntimeOperations, &Request) -> Response,
    ) -> Self {
        Self {
            name,
            cli: None,
            dispatch,
        }
    }
}
```

Dispatch uses `entry.name`:

```rust
entries()
    .iter()
    .find(|entry| entry.name == request.op)
```

The CLI catalog uses only entries with CLI metadata:

```rust
entries()
    .iter()
    .filter_map(|entry| entry.cli)
```

If const collection makes this awkward, modules may keep explicit `OPERATIONS`
and `CLI_SPECS` arrays, but they must preserve the invariant that the CLI
catalog is a strict CLI-only view.

## CLI Family Rules

`CliOperationFamilySpec` is a catalog/help grouping, not a module marker.

Rules:

- A module defines or exports a `CliOperationFamilySpec` only when it contributes
  at least one CLI-visible operation.
- Every `CliOperationSpec.family` must match a returned
  `CliOperationFamilySpec.id`.
- Every returned `CliOperationFamilySpec.id` must be used by at least one
  returned `CliOperationSpec`.
- Families with no CLI operations must not appear in `cli_operation_families()`.
- Non-CLI Rust services do not need a CLI family.

This means a module may contain both CLI and non-CLI service methods. It still
defines a CLI family only when at least one operation in that family is exposed
through `CliOperationSpec`.

## Runtime CLI Families

### Command

Family:

```rust
pub(crate) const COMMAND_FAMILY: CliOperationFamilySpec = CliOperationFamilySpec {
    id: "command",
    title: "Command",
    summary: "Run, interact with, and inspect commands.",
    description: "Run, interact with, and inspect commands inside the active sandbox runtime.",
};
```

CLI operations:

- `exec_command`
- `write_command_stdin`
- `read_command_lines`

### Layer Stack

Family:

```rust
pub(crate) const LAYERSTACK_FAMILY: CliOperationFamilySpec = CliOperationFamilySpec {
    id: "layerstack",
    title: "Layer Stack",
    summary: "Inspect and compact runtime layer stack state.",
    description: "Inspect and compact the sandbox runtime layer stack.",
};
```

CLI operations:

- `squash`

`squash` should have no CLI arguments initially:

```rust
pub(crate) const SPEC: CliOperationSpec = CliOperationSpec {
    name: "squash",
    family: "layerstack",
    summary: "Squash committed layer stack revisions.",
    description: "Compact the runtime layer stack into a single current revision when squashable layers exist.",
    args: &[],
    cli: Some(CliSpec {
        path: &["runtime", "squash"],
        usage: "sandbox-cli runtime squash",
        examples: &["sandbox-cli runtime squash"],
    }),
    related: &[],
};
```

The response should project `SquashLayerStackResult` into JSON with stable
fields:

```json
{
  "squashed": true,
  "revision": {
    "manifest_version": 1,
    "root_hash": "...",
    "layer_count": 1
  },
  "layer_paths": ["..."],
  "lease_release_error": null
}
```

When no squash occurs, `squashed` is `false`, `revision` is `null`,
`layer_paths` is empty, and `lease_release_error` may contain the best-effort
lease-release error if one occurred.

## Non-CLI Operation Guidance

Keep an operation non-CLI when it is:

- an implementation phase rather than a user workflow
- a remount substep
- a lock-sensitive helper
- a process-store or transcript helper
- a command finalization helper
- an operation requiring in-memory handles that users cannot name
- a response shape that exposes internal paths or transient orchestration state

Examples that should remain non-CLI unless wrapped by a stable user operation:

- `resolve_session`
- `begin_remount`
- `apply_and_finish_remount`
- `block_remount`
- `refresh_after_publish`
- `capture_session_changes`
- `publish_changes`

`publish_changes` may remain an internal service API even though it is important
domain behavior. If a user-facing publish operation is needed later, add a
separate CLI operation with a stable request and response shape instead of
exposing the internal method directly.

## Migration Phases

### Phase 1: Introduce Neutral Registry Semantics

- Change `OperationEntry` to hold `name` plus optional CLI metadata.
- Update command entries to use `OperationEntry::cli`.
- Keep the existing paths while proving the registry split.
- Update `dispatch_operation()` to match by `entry.name`.
- Update `cli_operation_specs()` to return only entries with CLI metadata.

Verification:

```sh
cargo fmt --check -p sandbox-runtime
cargo test -p sandbox-runtime service_graph
```

### Phase 2: Add Layerstack CLI Operation

- Add `LAYERSTACK_FAMILY`.
- Add a `squash` operation spec and dispatch parser.
- Register `squash` with `OperationEntry::cli`.
- Project `SquashLayerStackResult` to a stable protocol response.
- Include the layerstack family in runtime CLI families.

Verification:

```sh
cargo fmt --check -p sandbox-runtime
cargo test -p sandbox-runtime service_graph
cargo test -p sandbox-gateway gateway_cli
```

### Phase 3: Merge Source Layout

- Move `src/public/command` to `src/command`.
- Move `src/internal/layerstack` to `src/layerstack`.
- Move `src/internal/workspace_session` to `src/workspace_session`.
- Move `src/internal/workspace_remount` to `src/workspace_remount`.
- Move `src/internal/services.rs` to `src/services.rs`.
- Delete `src/public/mod.rs` and `src/internal/mod.rs`.
- Update imports to use the neutral module paths.
- Preserve or intentionally narrow crate-root re-exports.

Verification:

```sh
cargo fmt --check -p sandbox-runtime
cargo check -p sandbox-runtime --tests
cargo test -p sandbox-runtime
```

### Phase 4: Tighten Catalog Invariants

Add tests that assert:

- `cli_operation_specs()` returns no spec with `cli: None`.
- every returned CLI spec has a matching returned family.
- every returned CLI family is used by at least one returned CLI spec.
- runtime CLI help includes `command` and `layerstack`.
- runtime CLI help includes `squash`.
- runtime CLI help still omits `--sandbox-id` from operation usage and examples.
- unknown non-registered operations still return `unknown_op`.
- non-CLI operations, if registered for protocol dispatch, do not appear in
  catalog help.

Verification:

```sh
cargo test -p sandbox-runtime
cargo test -p sandbox-protocol
cargo test -p sandbox-gateway gateway_cli
```

## Acceptance Criteria

- `src/public` and `src/internal` no longer exist in
  `crates/sandbox-runtime/operation/src`.
- `dispatch_operation()` no longer depends on `CliOperationSpec`.
- CLI exposure is controlled only by returned `CliOperationSpec` values with
  `cli: Some`.
- `CliOperationFamilySpec` is defined only for CLI families with at least one
  CLI operation.
- Runtime CLI families include `command` and `layerstack`.
- Runtime CLI operations include:
  - `exec_command`
  - `write_command_stdin`
  - `read_command_lines`
  - `squash`
- `sandbox-cli runtime help` renders both families.
- `sandbox-cli runtime help squash` renders usage and examples.
- Existing command operation behavior remains unchanged.
- The focused runtime, protocol, and gateway tests pass.

## Final Verification

Run:

```sh
cargo fmt --check -p sandbox-runtime
cargo check -p sandbox-runtime --tests
cargo test -p sandbox-runtime
cargo test -p sandbox-protocol
cargo test -p sandbox-gateway gateway_cli
git diff --check
```
