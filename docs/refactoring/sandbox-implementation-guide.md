# Sandbox Refactor Implementation Guide

This guide turns the sandbox manager/daemon split into reviewable phases. Each
phase should leave the workspace in a coherent state before the next phase
starts.

Reference specs:

```text
docs/refactoring/sandbox-protocol.md
docs/refactoring/sandbox-runtime.md
docs/refactoring/sandbox-daemon.md
docs/refactoring/sandbox-manager.md
docs/refactoring/sandbox-gateway-cli.md
```

## Package Order

```text
0. existing packages:
   daemon_rpc_protocol
   daemon_operation
   daemon
   sandbox-daemon

1. sandbox-protocol
2. sandbox-runtime
3. sandbox-daemon
4. sandbox-manager core
5. sandbox-manager server and forwarding
6. sandbox-gateway-cli
7. catalog/manual contract
8. runtime support package rename wave
9. stale-name cleanup
```

Do not rename support packages while extracting the protocol, runtime facade,
daemon, manager, or gateway. Move them only in phase 8.

## Final Contract Guardrails

Every phase should preserve these final contracts unless that phase is
explicitly moving an old name toward the final shape.

Protocol and catalog names:

```text
Request
Response
OperationExecutionSpace
operation_execution_space
OperationFamily
OperationSpec
command_session_id
```

Runtime operation names:

```text
exec_command
write_command_stdin
poll_command
read_command_lines
cancel_command
```

Do not introduce or preserve active final-state uses of these old DTOs,
selectors, or operation names:

```text
OperationRequest
OperationResponse
SandboxRequest
RoutedRequest
ManagerRequest
OperationTarget
operation_space
command_id
command-id
exec
poll
cancel
```

Treat `exec`, `poll`, and `cancel` as stale only when they are operation names,
CLI names, file/module names, or catalog/manual names. Low-level implementation
words such as polling an fd or canceling a task are not stale by themselves.

Catalog JSON exposes `operation_execution_space` and `operations`. Do not add a
separate `owner`, `target`, `route`, `implementation_owner`, or
`operation_target` field. `OperationFamily` is documentation grouping only, not
the manager-vs-runtime routing selector.

## Phase 0: Baseline

Goal:

- Capture current behavior and pre-existing failures before file moves.

Packages present:

```text
daemon_rpc_protocol
daemon_operation
daemon
sandbox-daemon
command
workspace
namespace-process
layerstack
overlay
config
```

Implementation steps:

1. Capture `git status --short --untracked-files=all`.
2. Capture the current workspace package graph with `cargo metadata`.
3. Run the baseline verification commands below.
4. Record any failing command as pre-existing before renaming packages.
5. If a `sandbox-daemon` package or folder already exists before phase 3,
   record whether it is active or a placeholder. Phase 3 must leave one
   authoritative `sandbox-daemon` package; do not keep both a moved daemon
   server and a separate pre-existing server package active.

Resulting folder structure:

```text
crates/
  daemon/
    rpc_protocol/          # package: daemon_rpc_protocol
      Cargo.toml
      src/
      tests/
    operation/             # package: daemon_operation
      Cargo.toml
      src/public/
      src/internal/
      tests/
    server/                # package: daemon
      Cargo.toml
      src/
      tests/
    sandbox-daemon/                  # package: sandbox-daemon
      Cargo.toml
      src/
      tests/
    command/
    workspace/
    namespace-process/
    layerstack/
    overlay/
    config/
```

Verification:

```sh
cargo fmt --check -p daemon_rpc_protocol -p daemon_operation -p daemon -p sandbox-daemon
cargo check -p daemon_rpc_protocol -p daemon_operation -p daemon -p sandbox-daemon
cargo test -p daemon_rpc_protocol -p daemon_operation -p daemon
```

If any command fails, record the failure as pre-existing before changing
package names.

Exit criteria:

- Baseline package names and folder shape are recorded.
- Pre-existing failures are documented with exact commands.
- Any pre-existing `sandbox-daemon` package collision is understood before the
  phase 3 move.

## Phase 1: Extract `sandbox-protocol`

Prompt:

```text
docs/refactoring/sandbox-phase-1-protocol-prompt.md
```

Goal:

- Move the shared protocol contract out of the daemon namespace.
- Move protocol-neutral operation metadata into the protocol crate.

Package moves:

```text
daemon_rpc_protocol -> sandbox-protocol
```

Implementation steps:

1. Move `crates/daemon/rpc_protocol` to `crates/sandbox-protocol`.
2. Rename package `daemon_rpc_protocol` to `sandbox-protocol`.
3. Rename imports from `daemon_rpc_protocol` to `sandbox_protocol`.
4. Move only protocol-neutral spec types from `daemon_operation`:
   - `ArgKind`
   - `ArgCliSpec`
   - `ArgSpec`
   - `CliSpec`
   - `OperationSpec`
   - `OperationFamily`
5. Add protocol catalog types:
   - `OperationExecutionSpace`
   - `OperationCatalog`
6. Keep implementation-bound dispatch entries in `daemon_operation`.

Resulting folder structure:

```text
crates/
  sandbox-protocol/        # package: sandbox-protocol
    Cargo.toml
    src/
      lib.rs
      scope.rs
      request.rs
      response.rs
      framing.rs
      auth.rs
      limits.rs
      error_kind.rs
      operation_spec.rs
      catalog.rs
      manual.rs
    tests/

  daemon/
    operation/             # package: daemon_operation
      Cargo.toml
      src/                 # OperationEntry stays here
      tests/
    server/                # package: daemon
    sandbox-daemon/                  # package: sandbox-daemon
    command/
    workspace/
    namespace-process/
    layerstack/
    overlay/
    config/
```

Verification:

```sh
cargo fmt --check -p sandbox-protocol -p daemon_operation -p daemon
cargo check -p sandbox-protocol -p daemon_operation -p daemon
cargo test -p sandbox-protocol -p daemon_operation
```

Exit criteria:

- `sandbox-protocol` has no dependency on manager, daemon, runtime support, or
  operation dispatch crates.
- `daemon_operation` still owns `OperationEntry` and concrete dispatch.

## Phase 2: Extract `sandbox-runtime`

Prompt:

```text
docs/refactoring/sandbox-phase-2-runtime-prompt.md
```

Goal:

- Move daemon operation semantics into the runtime facade package.
- Preserve existing command operation behavior.

Package moves:

```text
daemon_operation -> sandbox-runtime
```

Implementation steps:

1. Move `crates/daemon/operation` to `crates/sandbox-runtime/operation`.
2. Rename package `daemon_operation` to `sandbox-runtime`.
3. Rename imports from `daemon_operation` to `sandbox_runtime`.
4. Keep the current operation module shape.
5. Rename aggregate types after the package compiles:
   - Runtime aggregate type is `SandboxRuntimeOperations`.
6. Export:
   - `sandbox_runtime::operation_specs()`
   - `sandbox_runtime::operation_catalog()`

Resulting folder structure:

```text
crates/
  sandbox-protocol/

  sandbox-runtime/
    operation/             # package: sandbox-runtime
      Cargo.toml
      src/
        lib.rs
        operation.rs
        public/
          mod.rs
          command/
            mod.rs
            service.rs
            service/
              impls/
                mod.rs
                exec_command.rs
                write_command_stdin.rs
                poll_command.rs
                read_command_lines.rs
                cancel_command.rs
        internal/
          mod.rs
          services.rs
          workspace_session/
          workspace_remount/
      tests/

  daemon/
    server/                # package: daemon
    sandbox-daemon/                  # package: sandbox-daemon
    command/
    workspace/
    namespace-process/
    layerstack/
    overlay/
    config/
```

Verification:

```sh
cargo fmt --check -p sandbox-runtime
cargo check -p sandbox-runtime --tests
cargo test -p sandbox-runtime
```

Exit criteria:

- `sandbox-runtime` exposes only daemon/runtime operations.
- Manager operations are not added to this crate.
- Command operation names are `exec_command`, `poll_command`, and
  `cancel_command`, not `exec`, `poll`, or `cancel`.

## Phase 3: Create `sandbox-daemon`

Prompt:

```text
docs/refactoring/sandbox-phase-3-daemon-prompt.md
```

Goal:

- Rename the in-sandbox server process and route daemon/helper subcommands from
  one `sandbox-daemon` binary.

Package moves:

```text
daemon -> sandbox-daemon
```

Implementation steps:

1. Move `crates/daemon/server` to `crates/sandbox-daemon`.
2. Route `serve`, `ns-runner`, and `ns-holder` from `sandbox-daemon/src/main.rs`.
3. Configure one binary from the package:

   ```toml
   [[bin]]
   name = "sandbox-daemon"
   path = "src/main.rs"
   ```

4. Keep daemon subcommands:
   - `serve`
   - `ns-runner`
   - `ns-holder`
5. Do not keep an alternate daemon subcommand; `sandbox-daemon serve` is the
   only server entrypoint.

Resulting folder structure:

```text
crates/
  sandbox-protocol/
  sandbox-runtime/
    operation/             # package: sandbox-runtime

  sandbox-daemon/          # package: sandbox-daemon
    Cargo.toml             # bin: sandbox-daemon
    src/
      main.rs
      lib.rs
      config.rs
      wiring.rs
      serve.rs
      runner.rs
      holder.rs
      server/
        mod.rs
        runtime.rs
        lifecycle.rs
        connection.rs
        dispatch.rs
        error.rs
    tests/

  daemon/
    command/
    workspace/
    namespace-process/
    layerstack/
    overlay/
    config/
```

Verification:

```sh
cargo fmt --check -p sandbox-daemon -p sandbox-runtime
cargo check -p sandbox-daemon -p sandbox-runtime
cargo test -p sandbox-daemon -p sandbox-runtime
```

Exit criteria:

- `sandbox-daemon` depends on `sandbox-protocol` and `sandbox-runtime`.
- `sandbox-daemon` does not depend on `sandbox-manager` or
  `sandbox-gateway-cli`.
- Existing daemon command behavior is unchanged.

## Phase 4: Add `sandbox-manager` Core

Prompt:

```text
docs/refactoring/sandbox-phase-4-manager-core-prompt.md
```

Goal:

- Add the host-side control plane model and manager operation catalog.
- Avoid Docker, Firecracker, or production sandbox runtime wiring in this
  phase.

New package:

```text
sandbox-manager
```

Implementation steps:

1. Add manager domain model:
   - `SandboxId`
   - `SandboxRecord`
   - `SandboxState`
   - `SandboxDaemonEndpoint`
2. Add an in-memory store.
3. Add host runtime traits.
4. Add daemon install/start traits.
5. Add a daemon client abstraction for later sandbox-scoped forwarding and
   runtime catalog discovery.
6. Add manager operation specs and dispatch.

Resulting folder structure:

```text
crates/
  sandbox-protocol/
  sandbox-runtime/
    operation/
  sandbox-daemon/

  sandbox-manager/         # package: sandbox-manager
    Cargo.toml
    src/
      lib.rs
      model.rs
      error.rs
      store.rs
      runtime.rs
      daemon_install.rs
      daemon_client.rs
      operation/
        mod.rs
        specs.rs
        dispatch.rs
        impls/
          mod.rs
          create_sandbox.rs
          destroy_sandbox.rs
          list_sandboxes.rs
          inspect_sandbox.rs
          start_sandbox_daemon.rs
          stop_sandbox_daemon.rs
          describe_manager_operations.rs
          describe_daemon_operations.rs
    tests/

  daemon/
    command/
    workspace/
    namespace-process/
    layerstack/
    overlay/
    config/
```

Verification:

```sh
cargo fmt --check -p sandbox-manager
cargo check -p sandbox-manager --tests
cargo test -p sandbox-manager
```

Exit criteria:

- Manager operation space and runtime operation space are separate.
- The runtime operation space is served by the sandbox daemon, but it should be
  presented to agents and CLI users as runtime.
- Manager may route sandbox-scoped daemon requests but does not implement daemon
  operations.
- Tests can use stub runtimes and stub daemon endpoints.

## Phase 5: Add Manager Server And Forwarding

Prompt:

```text
docs/refactoring/sandbox-phase-5-manager-server-prompt.md
```

Goal:

- Make `sandbox-manager` a process endpoint.
- Route sandbox-scoped daemon operations to a sandbox daemon endpoint.

Package changed:

```text
sandbox-manager
```

Implementation steps:

1. Add server config.
2. Add listener lifecycle and shutdown handling.
3. Add one framed request per connection.
4. Decode one unified `Request` DTO.
5. Dispatch manager-owned operations locally.
6. Forward daemon-owned sandbox-scoped operations through
   `SandboxDaemonEndpoint`.

Resulting folder structure:

```text
crates/
  sandbox-manager/
    Cargo.toml
    src/
      lib.rs
      model.rs
      error.rs
      store.rs
      runtime.rs
      daemon_install.rs
      daemon_client.rs
      operation/
      server/
        mod.rs
        config.rs
        lifecycle.rs
        connection.rs
        dispatch.rs
        forward.rs
    tests/
```

Request flow after this phase:

```text
client or test
  -> sandbox-manager
    -> sandbox-daemon
      -> sandbox-runtime
```

Verification:

```sh
cargo fmt --check -p sandbox-manager
cargo check -p sandbox-manager --tests
cargo test -p sandbox-manager
```

Exit criteria:

- Manager can resolve `SandboxId` to `SandboxDaemonEndpoint`.
- Manager forwarding uses the same `Request` DTO; it does not create a
  separate route wrapper and does not depend on daemon runtime implementation.
- Direct protocol callers may send sandbox-scoped runtime requests through the
  manager. If the manager validates runtime operation membership before
  forwarding, that validation must use the runtime catalog and still forward
  the same `Request` DTO.

## Phase 6: Add `sandbox-gateway-cli`

Prompt:

```text
docs/refactoring/sandbox-phase-6-gateway-cli-prompt.md
```

Goal:

- Add the human-facing command line.
- Keep the CLI as a protocol client, not a hidden manager.

New package:

```text
sandbox-gateway-cli
```

Implementation steps:

1. Add manager socket/config discovery.
2. Add manager client connection.
3. Add `Request` construction from CLI argv and `OperationSpec`.
4. Add manual/help rendering from manager and runtime execution spaces.
5. Add stdout/stderr and exit-code behavior.
6. Add the installed binary name `sandbox`.

Resulting folder structure:

```text
crates/
  sandbox-gateway-cli/     # package: sandbox-gateway-cli
    Cargo.toml             # bin: sandbox
    src/
      main.rs
      config.rs
      client.rs
      manual.rs
      request_builder.rs
      output.rs
    tests/

  sandbox-protocol/
  sandbox-manager/
  sandbox-daemon/
  sandbox-runtime/
    operation/
```

Verification:

```sh
cargo fmt --check -p sandbox-gateway-cli
cargo check -p sandbox-gateway-cli --tests
cargo test -p sandbox-gateway-cli
```

Exit criteria:

- Default route is gateway -> manager.
- Canonical execution spaces are `sandbox manager ...` and
  `sandbox runtime --sandbox-id ID ...`.
- Manager operations populate `OperationScope::System`.
- Runtime operations populate `OperationScope::Sandbox`; requests without a
  sandbox id use a configured default or fail.
- Normal caller flow is:
  1. `sandbox manager create_sandbox --sandbox-id ID`
  2. read the returned manager record JSON; `id` is the sandbox id, alongside
     `state` and `daemon`
  3. call runtime operations with `sandbox runtime --sandbox-id ID ...`
- Errors go to stderr and machine-readable responses go to stdout.

## Phase 7: Stabilize Catalog And Manual Contract

Prompt:

```text
docs/refactoring/sandbox-phase-7-catalog-manual-prompt.md
```

Goal:

- Make manager and runtime execution spaces discoverable by agents and CLI
  help.

Packages changed:

```text
sandbox-protocol
sandbox-manager
sandbox-gateway-cli
```

Implementation steps:

1. Stabilize `OperationCatalog` and `OperationExecutionSpace`.
2. Ensure the manager operation space returns manager operations only.
3. Ensure the runtime operation space returns runtime operations only.
4. Add or verify:
   - `describe_manager_operations`
   - `describe_daemon_operations`
5. Parse and emit catalog JSON through `sandbox-protocol` helpers rather than
   manager- or gateway-local document structs.
6. Render CLI/manual output from cataloged `OperationSpec` data, not duplicated
   strings.
7. Expose one catalog selector, `operation_execution_space`, and do not include
   separate owner, target, route, implementation-owner, or operation-target
   fields in catalog output.

Resulting folder structure:

```text
crates/
  sandbox-protocol/
    src/
      operation_spec.rs
      catalog.rs
      manual.rs

  sandbox-manager/
    src/
      operation/
        specs.rs
        dispatch.rs
        impls/
          describe_manager_operations.rs
          describe_daemon_operations.rs

  sandbox-gateway-cli/
    src/
      manual.rs
      request_builder.rs
```

Verification:

```sh
cargo test -p sandbox-manager operation_catalog
cargo test -p sandbox-gateway-cli manual
```

Exit criteria:

- Agents choose operation space first, then operation.
- `OperationFamily` is documentation grouping only, not the manager-vs-runtime
  routing selector.

## Phase 8: Rename Runtime Support Packages

Prompt:

```text
docs/refactoring/sandbox-phase-8-runtime-support-rename-prompt.md
```

Goal:

- Move runtime support packages from `crates/daemon/*` into
  `crates/sandbox-runtime/*`.
- Keep support packages separate from the `sandbox-runtime` facade package.

Package moves:

```text
command           -> sandbox-runtime-command
workspace         -> sandbox-runtime-workspace
namespace-process -> sandbox-runtime-namespace-process
layerstack        -> sandbox-runtime-layerstack
overlay           -> sandbox-runtime-overlay
config            -> sandbox-runtime-config
```

Recommended implementation order:

1. `config` -> `sandbox-runtime-config`
2. `overlay` -> `sandbox-runtime-overlay`
3. `layerstack` -> `sandbox-runtime-layerstack`
4. `namespace-process` -> `sandbox-runtime-namespace-process`
5. `workspace` -> `sandbox-runtime-workspace`
6. `command` -> `sandbox-runtime-command`

This order starts with lower-level crates, then updates their downstream
dependents as each move lands.

Runtime support dependency direction after this phase:

```text
sandbox-runtime
  -> sandbox-protocol
  -> sandbox-runtime-command
  -> sandbox-runtime-workspace

sandbox-runtime-command
  -> sandbox-runtime-workspace
  -> sandbox-runtime-namespace-process

sandbox-runtime-workspace
  -> sandbox-runtime-layerstack
  -> sandbox-runtime-namespace-process

sandbox-runtime-namespace-process
  -> sandbox-runtime-config
  -> sandbox-runtime-overlay

sandbox-runtime-layerstack
  -> no sibling runtime package

sandbox-runtime-overlay
  -> no sibling runtime package

sandbox-runtime-config
  -> no sibling runtime package
```

Implementation steps:

1. Move each package one at a time in the recommended implementation order.
2. Update package names and imports.
3. Preserve existing public behavior.
4. Keep `command-request.json` until an explicit replacement transport exists.
5. Verify each moved package before moving the next one.

Resulting folder structure:

```text
crates/
  sandbox-protocol/
  sandbox-runtime/
    operation/             # package: sandbox-runtime
    command/               # package: sandbox-runtime-command
      Cargo.toml
      src/
      tests/
    workspace/             # package: sandbox-runtime-workspace
      Cargo.toml
      src/
      tests/
    namespace-process/     # package: sandbox-runtime-namespace-process
      Cargo.toml
      src/
      tests/
    layerstack/            # package: sandbox-runtime-layerstack
      Cargo.toml
      src/
      tests/
    overlay/               # package: sandbox-runtime-overlay
      Cargo.toml
      src/
      tests/
    config/                # package: sandbox-runtime-config
      Cargo.toml
      src/
      tests/

  sandbox-daemon/
  sandbox-manager/
  sandbox-gateway-cli/
```

Verification:

```sh
cargo fmt --check -p sandbox-runtime-config
cargo check -p sandbox-runtime-config --tests
cargo test -p sandbox-runtime-config

cargo fmt --check -p sandbox-runtime-overlay
cargo check -p sandbox-runtime-overlay --tests
cargo test -p sandbox-runtime-overlay

cargo fmt --check -p sandbox-runtime-layerstack
cargo check -p sandbox-runtime-layerstack --tests
cargo test -p sandbox-runtime-layerstack

cargo fmt --check -p sandbox-runtime-namespace-process
cargo check -p sandbox-runtime-namespace-process --tests
cargo test -p sandbox-runtime-namespace-process

cargo fmt --check -p sandbox-runtime-workspace
cargo check -p sandbox-runtime-workspace --tests
cargo test -p sandbox-runtime-workspace

cargo fmt --check -p sandbox-runtime-command
cargo check -p sandbox-runtime-command --tests
cargo test -p sandbox-runtime-command
```

Exit criteria:

- `sandbox-runtime-command` owns command process, PTY, transcript, process group
  inspection/cancellation primitives, and command request construction.
- `sandbox-runtime-workspace` owns workspace lifecycle and workspace-level
  overlay planning/capture. It does not own low-level overlayfs syscalls.
- `sandbox-runtime-namespace-process` owns `ns-holder`, `ns-runner`, setns, and
  namespace-local mount/remount behavior.
- `sandbox-runtime-overlay` remains a shared low-level mount primitive crate.
- `sandbox-runtime-layerstack` and `sandbox-runtime-config` do not depend on
  higher runtime implementation crates.
- If `sandbox-daemon` directly depends on runtime support crates for wiring, the
  dependency must either be documented as an allowed wiring-only dependency in
  `docs/refactoring/sandbox-daemon.md` or moved behind the runtime facade before
  final cleanup.

## Phase 9: Compatibility Cleanup

Prompt:

```text
docs/refactoring/sandbox-phase-9-compatibility-cleanup-prompt.md
```

Goal:

- Remove stale names and update packaging after the new shape works.

Packages and files in scope:

```text
Cargo.toml
Cargo.lock
README.md
config/README.md
docs/README/**
docs/refactoring/*.md except historical phase 1-8 prompt text
xtask
crates
```

Implementation steps:

1. Update README, config docs, active `docs/README/**`, and active
   architecture docs to the final package structure.
2. Move or rewrite active README docs that present `daemon_operation` as the
   runtime boundary, such as `docs/README/daemon/daemon_operation.md`, to a
   current runtime README such as `docs/README/sandbox-runtime.md`.
3. Update packaging from legacy `eosd` artifact names to `sandbox-daemon`
   artifact names, or explicitly document temporary `eosd` compatibility.
4. Keep any packaged `eosd` alias only while packaging still requires that
   artifact name. `sandbox-daemon-linux-*` must be the primary artifact name.
5. Remove old internal workspace dependency entries:
   - `daemon_rpc_protocol`
   - `daemon_operation`
   - `daemon`
   - `command`
   - `workspace`
   - `namespace-process`
   - `layerstack`
   - `overlay`
   - `config`
6. Run stale-name, package graph, dependency-boundary, and packaging scans.

Final folder structure:

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

Stale-name scans:

```sh
rg -n "daemon_rpc_protocol|daemon_operation|crates/daemon/(rpc_protocol|operation|server|command|workspace|namespace-process|layerstack|overlay|config)" Cargo.toml Cargo.lock crates README.md config docs xtask --glob '!target/**' --glob '!docs/refactoring/sandbox-phase-[1-8]*.md'
rg -n "sandbox-runtime[-_]operation|sandbox_runtime[_]operation" Cargo.toml Cargo.lock crates README.md config docs xtask --glob '!target/**' --glob '!docs/refactoring/sandbox-phase-[1-8]*.md'
rg -n "operation_space|OperationRequest|OperationResponse|SandboxRequest|RoutedRequest|ManagerRequest|OperationTarget|invoke_sandbox_daemon" Cargo.toml Cargo.lock crates README.md config docs xtask --glob '!target/**' --glob '!docs/refactoring/sandbox-phase-[1-8]*.md'
rg -n "command-id|command_id\\b" Cargo.toml Cargo.lock crates README.md config docs xtask --glob '!target/**' --glob '!docs/refactoring/sandbox-phase-[1-8]*.md'
rg -n "eosd|eosd-linux|-p[[:space:]]+eosd|package[[:space:]]+eosd" xtask README.md config docs crates --glob '!target/**' --glob '!docs/refactoring/sandbox-phase-[1-8]*.md'
find crates -path '*/poll.rs' -o -path '*/cancel.rs' -o -path '*/exec.rs'
rg -n 'name: "(exec|poll|cancel)"|"exec"|"poll"|"cancel"' crates/sandbox-runtime/operation crates/sandbox-manager crates/sandbox-gateway-cli README.md docs/README --glob '!target/**'
```

The scans should return no active stale matches. If a match is intentional,
document the file, line, and reason in the final response. Do not rewrite
historical phase 1-8 prompt files solely to satisfy stale-name scans.

Package graph and dependency-boundary checks:

```sh
cargo metadata --no-deps --format-version 1
cargo tree -p sandbox-manager --prefix depth
cargo tree -p sandbox-gateway-cli --prefix depth
cargo tree -p sandbox-daemon --prefix depth
cargo tree -p sandbox-runtime --prefix depth
cargo tree -p sandbox-runtime-command --prefix depth
cargo tree -p sandbox-runtime-workspace --prefix depth
cargo tree -p sandbox-runtime-namespace-process --prefix depth
cargo tree -p sandbox-runtime-layerstack --prefix depth
cargo tree -p sandbox-runtime-overlay --prefix depth
cargo tree -p sandbox-runtime-config --prefix depth
```

Inspect the package graph for forbidden direct edges from the crate specs. If
the live daemon still directly wires runtime support crates, either update the
daemon spec to explicitly allow those wiring-only dependencies or move the
wiring behind the runtime facade before completing phase 9.

Packaging checks:

```sh
cargo check -p xtask
cargo test -p xtask
cargo run -p xtask -- package --help
```

If the local target is available and the package build is practical:

```sh
rm -rf /tmp/eos-sandbox-phase-9-dist
cargo run -p xtask -- package --out-dir /tmp/eos-sandbox-phase-9-dist
find /tmp/eos-sandbox-phase-9-dist -maxdepth 1 -type f -print | sort
```

The output should show `sandbox-daemon-linux-*` primary artifacts. If
`eosd-linux-*` artifacts are also present, they must be documented
compatibility aliases.

Final verification:

```sh
cargo fmt --check --all
cargo check -p sandbox-protocol -p sandbox-runtime -p sandbox-daemon -p sandbox-manager -p sandbox-gateway-cli -p sandbox-runtime-command -p sandbox-runtime-workspace -p sandbox-runtime-namespace-process -p sandbox-runtime-layerstack -p sandbox-runtime-overlay -p sandbox-runtime-config -p xtask --tests
cargo test -p sandbox-protocol -p sandbox-runtime -p sandbox-daemon -p sandbox-manager -p sandbox-gateway-cli -p sandbox-runtime-command -p sandbox-runtime-workspace -p sandbox-runtime-namespace-process -p sandbox-runtime-layerstack -p sandbox-runtime-overlay -p sandbox-runtime-config -p xtask
cargo clippy -p sandbox-protocol -p sandbox-runtime -p sandbox-daemon -p sandbox-manager -p sandbox-gateway-cli -p sandbox-runtime-command -p sandbox-runtime-workspace -p sandbox-runtime-namespace-process -p sandbox-runtime-layerstack -p sandbox-runtime-overlay -p sandbox-runtime-config -p xtask --all-targets --no-deps -- -D warnings
git diff --check
```

Exit criteria:

- `crates/daemon/` is absent or empty and removed.
- Root workspace metadata contains final package paths only.
- Active docs describe the final package structure.
- Active README docs no longer present `daemon_operation` as a live crate.
- Packaging builds and labels the primary helper artifact as
  `sandbox-daemon-linux-*`.
- Any remaining `eosd` mention is an explicit compatibility alias with a clear
  reason.
- Old runtime-operation package/import names do not appear in active code or
  non-refactoring docs.
- Active code and non-historical docs use `operation_execution_space`,
  `command_session_id`, `Request`, and `Response`.
- `exec`, `poll`, and `cancel` do not remain as operation names, CLI names,
  file/module names, or catalog/manual names; use `exec_command`,
  `poll_command`, and `cancel_command`.
- The CLI, manager, daemon, runtime facade, runtime support packages, and
  `xtask` build and test by package.
