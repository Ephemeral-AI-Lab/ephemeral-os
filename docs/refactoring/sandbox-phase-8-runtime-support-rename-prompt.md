# Phase 8 Prompt: Rename Runtime Support Packages

Use this prompt after phase 7 has completed.

```text
You are working in:

/Users/yifanxu/machine_learning/LoVC/ephemeral-ai/ephemeral-os

Task:

Implement phase 8 only: move the runtime support packages out of
`crates/daemon/*` and into `crates/sandbox-runtime/*`, rename their Cargo
packages, and update active dependents.

The goal is a clean runtime package family:

- `sandbox-runtime` remains the daemon/runtime operation facade at
  `crates/sandbox-runtime/operation`.
- Runtime support packages become separate packages under
  `crates/sandbox-runtime/*`.
- `sandbox-protocol`, `sandbox-manager`, `sandbox-gateway-cli`, and
  `sandbox-daemon` keep their phase 7 public behavior.

Do not merge support packages into the `sandbox-runtime` facade package. Do not
change operation names, request/response DTO semantics, catalog/manual behavior,
or command execution behavior in this phase.

Before editing, read:

- docs/refactoring/sandbox-implementation-guide.md
- docs/refactoring/sandbox-runtime.md
- docs/refactoring/sandbox-daemon.md
- docs/refactoring/sandbox-manager.md
- docs/refactoring/sandbox-protocol.md
- docs/refactoring/sandbox-gateway-cli.md
- docs/refactoring/sandbox-phase-7-catalog-manual-prompt.md

Required starting state:

- `crates/sandbox-protocol` exists.
- `crates/sandbox-manager` exists.
- `crates/sandbox-gateway-cli` exists.
- `crates/sandbox-daemon` exists.
- `crates/sandbox-runtime/operation` exists and is package `sandbox-runtime`.
- `sandbox_protocol::Request` exists.
- `sandbox_protocol::Response` exists.
- `sandbox_protocol::OperationExecutionSpace` exists.
- The active catalog field is `operation_execution_space`.
- The stale active selector name `operation_space` is absent from active code.
- The source support packages still exist:
  - `crates/daemon/command`
  - `crates/daemon/workspace`
  - `crates/daemon/namespace-process`
  - `crates/daemon/layerstack`
  - `crates/daemon/overlay`
  - `crates/daemon/config`
- The target support package directories do not already exist unless this phase
  is being resumed from a known partial move:
  - `crates/sandbox-runtime/command`
  - `crates/sandbox-runtime/workspace`
  - `crates/sandbox-runtime/namespace-process`
  - `crates/sandbox-runtime/layerstack`
  - `crates/sandbox-runtime/overlay`
  - `crates/sandbox-runtime/config`

If the starting state is not true, stop and report which earlier phase is
missing, stale, or partially applied. Do not build phase 8 on top of the old
`OperationRequest` / `OperationResponse` API, `SandboxRequest`, routed-request
wrappers, or the old `operation_space` catalog field.

Phase goal:

- Move each runtime support package to its final `crates/sandbox-runtime/*`
  path.
- Rename each package and import crate to its final runtime name.
- Update the root workspace members and workspace dependencies.
- Update all active Cargo dependency edges.
- Update Rust imports and tests to compile against the renamed crates.
- Preserve runtime behavior and public operation/catalog behavior.
- Keep `command-request.json` until an explicit replacement transport exists.
- Keep `sandbox-runtime-overlay` as a shared low-level mount primitive, not a
  workspace submodule.

Package moves:

```text
crates/daemon/config            -> crates/sandbox-runtime/config
crates/daemon/overlay           -> crates/sandbox-runtime/overlay
crates/daemon/layerstack        -> crates/sandbox-runtime/layerstack
crates/daemon/namespace-process -> crates/sandbox-runtime/namespace-process
crates/daemon/workspace         -> crates/sandbox-runtime/workspace
crates/daemon/command           -> crates/sandbox-runtime/command
```

Target package and import names:

```text
Path                                      Package                             Import
crates/sandbox-runtime/operation          sandbox-runtime                     sandbox_runtime
crates/sandbox-runtime/command            sandbox-runtime-command             sandbox_runtime_command
crates/sandbox-runtime/workspace          sandbox-runtime-workspace           sandbox_runtime_workspace
crates/sandbox-runtime/namespace-process  sandbox-runtime-namespace-process   sandbox_runtime_namespace_process
crates/sandbox-runtime/layerstack         sandbox-runtime-layerstack          sandbox_runtime_layerstack
crates/sandbox-runtime/overlay            sandbox-runtime-overlay             sandbox_runtime_overlay
crates/sandbox-runtime/config             sandbox-runtime-config              sandbox_runtime_config
```

Recommended implementation order:

1. Move `config` to `sandbox-runtime-config`.
2. Move `overlay` to `sandbox-runtime-overlay`.
3. Move `layerstack` to `sandbox-runtime-layerstack`.
4. Move `namespace-process` to `sandbox-runtime-namespace-process`.
5. Move `workspace` to `sandbox-runtime-workspace`.
6. Move `command` to `sandbox-runtime-command`.

This order starts with lower-level crates, then updates their downstream
dependents as each move lands.

For each package move:

1. Check current call sites and dependency edges before editing:

   ```sh
   rg -n "old_crate_name|old-package-name|crates/daemon/old-path" Cargo.toml crates xtask docs --glob '!target/**'
   cargo tree -p old-package-name --prefix depth
   ```

2. Move the directory to its target path.
3. Update that package's `Cargo.toml`:
   - `[package].name`
   - any local dependency names and package aliases
   - any test or feature references
4. Update the root `Cargo.toml`:
   - `workspace.members`
   - `[workspace.dependencies]`
5. Update all active dependent `Cargo.toml` files.
6. Update Rust imports from the old crate import name to the new import name.
7. Run the narrow check for that package and its immediate downstream
   dependents before moving to the next package.

Target root workspace dependencies:

```toml
sandbox-runtime = { path = "crates/sandbox-runtime/operation" }
sandbox-runtime-command = { path = "crates/sandbox-runtime/command" }
sandbox-runtime-workspace = { path = "crates/sandbox-runtime/workspace" }
sandbox-runtime-namespace-process = { path = "crates/sandbox-runtime/namespace-process" }
sandbox-runtime-layerstack = { path = "crates/sandbox-runtime/layerstack" }
sandbox-runtime-overlay = { path = "crates/sandbox-runtime/overlay" }
sandbox-runtime-config = { path = "crates/sandbox-runtime/config" }
```

Remove these old internal workspace dependency entries after their moves are
complete:

```toml
command = { path = "crates/daemon/command" }
workspace = { path = "crates/daemon/workspace" }
namespace-process = { path = "crates/daemon/namespace-process" }
layerstack = { path = "crates/daemon/layerstack" }
overlay = { path = "crates/daemon/overlay" }
config = { path = "crates/daemon/config" }
```

Expected direct dependency direction after the move:

```text
sandbox-daemon -> sandbox-protocol
sandbox-daemon -> sandbox-runtime
sandbox-daemon -> sandbox-runtime-config if live daemon helper code still needs direct config access

sandbox-runtime -> sandbox-protocol
sandbox-runtime -> sandbox-runtime-command
sandbox-runtime -> sandbox-runtime-workspace

sandbox-runtime-command -> sandbox-runtime-workspace
sandbox-runtime-command -> sandbox-runtime-namespace-process when active runner request construction needs protocol types

sandbox-runtime-workspace -> sandbox-runtime-layerstack
sandbox-runtime-workspace -> sandbox-runtime-namespace-process
sandbox-runtime-workspace -> sandbox-runtime-overlay

sandbox-runtime-namespace-process -> sandbox-runtime-config
sandbox-runtime-namespace-process -> sandbox-runtime-overlay

sandbox-runtime-layerstack -> no sibling runtime package
sandbox-runtime-overlay -> no sibling runtime package
sandbox-runtime-config -> no sibling runtime package
```

If live code requires a slightly different edge during the move, keep the edge
only when it is backed by an active call site and does not violate the crate
spec in `docs/refactoring/sandbox-runtime.md`.

Preserve these behavior contracts:

- Manager operations remain manager-scoped.
- Runtime operations remain daemon/runtime-scoped.
- Agents and CLI users choose `manager` or `runtime` by
  `OperationExecutionSpace`, not by `OperationFamily`.
- `OperationFamily` remains documentation grouping only.
- Runtime operation names remain:
  - `exec_command`
  - `write_command_stdin`
  - `poll_command`
  - `read_command_lines`
  - `cancel_command`
- Command session fields remain `command_session_id`, not `command_id`.
- Protocol DTOs remain `Request` and `Response`, not
  `OperationRequest` / `OperationResponse`.

Non-goals:

- Do not implement phase 9 stale-name cleanup beyond active references required
  for compile/tests.
- Do not rewrite unrelated architecture docs outside the package paths needed
  for this phase.
- Do not rename runtime operations.
- Do not change CLI command syntax.
- Do not alter manager server forwarding behavior.
- Do not remove `command-request.json`.
- Do not move `overlay` wholly under `workspace`.
- Do not reintroduce `operation_space`, `OperationRequest`,
  `OperationResponse`, `SandboxRequest`, `RoutedRequest`, `ManagerRequest`,
  `OperationTarget`, or `invoke_sandbox_daemon`.

Implementation steps:

1. Check current status:

   ```sh
   git status --short
   ```

   Preserve unrelated user changes. Do not revert files you did not change.

2. Verify the phase 7 starting state:

   ```sh
   test -d crates/sandbox-protocol
   test -d crates/sandbox-manager
   test -d crates/sandbox-gateway-cli
   test -d crates/sandbox-daemon
   test -d crates/sandbox-runtime/operation
   test -d crates/daemon/command
   test -d crates/daemon/workspace
   test -d crates/daemon/namespace-process
   test -d crates/daemon/layerstack
   test -d crates/daemon/overlay
   test -d crates/daemon/config
   rg -n "Request|Response|OperationExecutionSpace|operation_execution_space" crates/sandbox-protocol/src crates/sandbox-manager/src crates/sandbox-gateway-cli/src crates/sandbox-runtime/operation/src
   rg -n "operation_space|OperationRequest|OperationResponse|SandboxRequest|RoutedRequest|ManagerRequest|OperationTarget|invoke_sandbox_daemon" crates/sandbox-protocol/src crates/sandbox-manager/src crates/sandbox-gateway-cli/src crates/sandbox-runtime/operation/src
   ```

   The final `rg` command should return no matches.

3. Run and record baseline results:

   ```sh
   cargo fmt --check -p sandbox-runtime -p sandbox-daemon
   cargo check -p sandbox-runtime -p sandbox-daemon --tests
   cargo test -p sandbox-runtime -p sandbox-daemon

   cargo fmt --check -p command -p workspace -p namespace-process -p layerstack -p overlay -p config
   cargo check -p command -p workspace -p namespace-process -p layerstack -p overlay -p config --tests
   cargo test -p command -p workspace -p namespace-process -p layerstack -p overlay -p config
   ```

   If any command fails, determine whether the failure is pre-existing. Continue
   only when the failure is unrelated to package movement, or fix it if it is
   required for a safe rename.

4. Move and rename `config`.

   Target:

   ```text
   crates/sandbox-runtime/config
   package: sandbox-runtime-config
   import:  sandbox_runtime_config
   ```

   Update dependents that currently import or depend on `config`, including
   `namespace-process`, `sandbox-daemon`, or tests if present.

   Verify:

   ```sh
   cargo fmt --check -p sandbox-runtime-config
   cargo check -p sandbox-runtime-config --tests
   cargo test -p sandbox-runtime-config
   ```

5. Move and rename `overlay`.

   Target:

   ```text
   crates/sandbox-runtime/overlay
   package: sandbox-runtime-overlay
   import:  sandbox_runtime_overlay
   ```

   Update dependents that currently import or depend on `overlay`, especially
   `namespace-process` and `workspace`.

   Verify:

   ```sh
   cargo fmt --check -p sandbox-runtime-overlay
   cargo check -p sandbox-runtime-overlay --tests
   cargo test -p sandbox-runtime-overlay
   ```

6. Move and rename `layerstack`.

   Target:

   ```text
   crates/sandbox-runtime/layerstack
   package: sandbox-runtime-layerstack
   import:  sandbox_runtime_layerstack
   ```

   Update dependents that currently import or depend on `layerstack`, especially
   `workspace`.

   Verify:

   ```sh
   cargo fmt --check -p sandbox-runtime-layerstack
   cargo check -p sandbox-runtime-layerstack --tests
   cargo test -p sandbox-runtime-layerstack
   ```

7. Move and rename `namespace-process`.

   Target:

   ```text
   crates/sandbox-runtime/namespace-process
   package: sandbox-runtime-namespace-process
   import:  sandbox_runtime_namespace_process
   ```

   Update dependents that currently import or depend on `namespace-process`,
   especially `command`, `workspace`, and any `sandbox-daemon` helper adapter.

   Verify:

   ```sh
   cargo fmt --check -p sandbox-runtime-namespace-process
   cargo check -p sandbox-runtime-namespace-process --tests
   cargo test -p sandbox-runtime-namespace-process
   ```

8. Move and rename `workspace`.

   Target:

   ```text
   crates/sandbox-runtime/workspace
   package: sandbox-runtime-workspace
   import:  sandbox_runtime_workspace
   ```

   Update dependents that currently import or depend on `workspace`, especially
   `command` and `sandbox-runtime`.

   Verify:

   ```sh
   cargo fmt --check -p sandbox-runtime-workspace
   cargo check -p sandbox-runtime-workspace --tests
   cargo test -p sandbox-runtime-workspace
   ```

9. Move and rename `command`.

   Target:

   ```text
   crates/sandbox-runtime/command
   package: sandbox-runtime-command
   import:  sandbox_runtime_command
   ```

   Update dependents that currently import or depend on `command`, especially
   `sandbox-runtime`.

   Verify:

   ```sh
   cargo fmt --check -p sandbox-runtime-command
   cargo check -p sandbox-runtime-command --tests
   cargo test -p sandbox-runtime-command
   ```

10. Update the `sandbox-runtime` facade.

   In `crates/sandbox-runtime/operation`, update dependencies and imports from
   the old support crate names to:

   ```text
   sandbox-runtime-command
   sandbox-runtime-workspace
   ```

   The facade should still own operation specs, dispatch, argument parsing, and
   response projection. The facade must not absorb low-level command,
   workspace, namespace-process, layerstack, overlay, or config internals.

11. Update `sandbox-daemon`.

   Update any direct dependencies/imports from old support crate names to the
   new runtime support names. Keep `sandbox-daemon` focused on daemon binary
   entrypoints, serving, helper adapters, and runtime wiring.

12. Update workspace metadata.

   Ensure root `Cargo.toml` contains the new workspace members:

   ```text
   crates/sandbox-runtime/operation
   crates/sandbox-runtime/command
   crates/sandbox-runtime/workspace
   crates/sandbox-runtime/namespace-process
   crates/sandbox-runtime/layerstack
   crates/sandbox-runtime/overlay
   crates/sandbox-runtime/config
   ```

   Ensure root `Cargo.toml` no longer contains these members:

   ```text
   crates/daemon/command
   crates/daemon/workspace
   crates/daemon/namespace-process
   crates/daemon/layerstack
   crates/daemon/overlay
   crates/daemon/config
   ```

13. Run active-code stale scans:

   ```sh
   rg -n "crates/daemon/(command|workspace|namespace-process|layerstack|overlay|config)" Cargo.toml crates xtask --glob '!target/**'
   rg -n 'package = "(command|workspace|namespace-process|layerstack|overlay|config)"|name = "(command|workspace|namespace-process|layerstack|overlay|config)"' Cargo.toml crates --glob '!target/**'
   rg -n "use (command|workspace|namespace_process|layerstack|overlay|config)::|::(command|workspace|namespace_process|layerstack|overlay|config)::" crates xtask --glob '!target/**'
   rg -n "operation_space|OperationRequest|OperationResponse|SandboxRequest|RoutedRequest|ManagerRequest|OperationTarget|invoke_sandbox_daemon" crates --glob '!target/**'
   ```

   These scans should return no active-code matches unless there is a justified
   external crate name collision. If there is a collision, document it in the
   final response with the file and reason.

14. Verify package graph:

   ```sh
   cargo metadata --no-deps --format-version 1 > /tmp/eos-sandbox-runtime-phase-8-metadata.json
   cargo tree -p sandbox-runtime --prefix depth
   cargo tree -p sandbox-daemon --prefix depth
   ```

   Confirm the new package names appear and the old internal runtime support
   package names do not remain as workspace packages.

15. Run final checks:

   ```sh
   cargo fmt --check --all
   cargo check -p sandbox-protocol -p sandbox-runtime -p sandbox-daemon -p sandbox-manager -p sandbox-gateway-cli -p sandbox-runtime-command -p sandbox-runtime-workspace -p sandbox-runtime-namespace-process -p sandbox-runtime-layerstack -p sandbox-runtime-overlay -p sandbox-runtime-config --tests
   cargo test -p sandbox-protocol -p sandbox-runtime -p sandbox-daemon -p sandbox-manager -p sandbox-gateway-cli -p sandbox-runtime-command -p sandbox-runtime-workspace -p sandbox-runtime-namespace-process -p sandbox-runtime-layerstack -p sandbox-runtime-overlay -p sandbox-runtime-config
   cargo clippy -p sandbox-protocol -p sandbox-runtime -p sandbox-daemon -p sandbox-manager -p sandbox-gateway-cli -p sandbox-runtime-command -p sandbox-runtime-workspace -p sandbox-runtime-namespace-process -p sandbox-runtime-layerstack -p sandbox-runtime-overlay -p sandbox-runtime-config --all-targets --no-deps -- -D warnings
   git diff --check
   ```

Expected resulting folder structure:

```text
crates/
  sandbox-protocol/
  sandbox-runtime/
    operation/             # package: sandbox-runtime
    command/               # package: sandbox-runtime-command
    workspace/             # package: sandbox-runtime-workspace
    namespace-process/     # package: sandbox-runtime-namespace-process
    layerstack/            # package: sandbox-runtime-layerstack
    overlay/               # package: sandbox-runtime-overlay
    config/                # package: sandbox-runtime-config
  sandbox-daemon/
  sandbox-manager/
  sandbox-gateway-cli/
```

Exit criteria:

- `crates/daemon/command`, `crates/daemon/workspace`,
  `crates/daemon/namespace-process`, `crates/daemon/layerstack`,
  `crates/daemon/overlay`, and `crates/daemon/config` are gone.
- The corresponding `crates/sandbox-runtime/*` support package directories
  exist.
- The root workspace lists new runtime support package paths and no longer
  lists old `crates/daemon/*` support paths.
- Active Rust code imports new runtime support crate names.
- `sandbox-runtime` remains the operation facade package and support crates
  remain separate packages.
- `sandbox-runtime-overlay` remains a shared low-level crate used by the
  packages that need mount primitives.
- Catalog output still uses `operation_execution_space`.
- Protocol DTOs are still `Request` and `Response`.
- Runtime command behavior is preserved.

Final response requirements:

- Summarize the package moves performed.
- List any pre-existing failures separately from failures introduced or fixed
  during this phase.
- List the exact verification commands run and their results.
- Call out any remaining old-name matches with the reason they are acceptable.
- Do not claim phase 8 is complete unless the final checks pass or any skipped
  checks are explicitly justified.
```
