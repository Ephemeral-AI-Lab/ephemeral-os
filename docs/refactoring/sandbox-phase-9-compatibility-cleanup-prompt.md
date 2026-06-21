# Phase 9 Prompt: Compatibility Cleanup

Use this prompt after phase 8 has completed and the runtime support packages
have moved to `crates/sandbox-runtime/*`.

```text
You are working in:

/Users/yifanxu/machine_learning/LoVC/ephemeral-ai/ephemeral-os

Task:

Implement phase 9 only: remove stale names, stale paths, stale docs, and stale
packaging labels after the sandbox protocol, manager, gateway CLI, daemon, and
runtime package split works.

This phase is cleanup and compatibility hardening. Do not introduce new
operation semantics, new manager/daemon routing behavior, new CLI command
syntax, or a different request/response protocol.

Before editing, read:

- docs/refactoring/sandbox-implementation-guide.md
- docs/refactoring/sandbox-phase-8-runtime-support-rename-prompt.md
- docs/refactoring/sandbox-runtime.md
- docs/refactoring/sandbox-daemon.md
- docs/refactoring/sandbox-manager.md
- docs/refactoring/sandbox-protocol.md
- docs/refactoring/sandbox-gateway-cli.md
- README.md
- config/README.md
- xtask/src/main.rs

Required starting state:

- Phase 8 is complete, or the only remaining work is stale-name cleanup that
  belongs to this phase.
- `crates/sandbox-protocol` exists.
- `crates/sandbox-manager` exists.
- `crates/sandbox-gateway-cli` exists.
- `crates/sandbox-daemon` exists.
- `crates/sandbox-runtime/operation` exists and is package `sandbox-runtime`.
- Runtime support packages exist:
  - `crates/sandbox-runtime/command`
  - `crates/sandbox-runtime/workspace`
  - `crates/sandbox-runtime/namespace-process`
  - `crates/sandbox-runtime/layerstack`
  - `crates/sandbox-runtime/overlay`
  - `crates/sandbox-runtime/config`
- `crates/daemon` does not exist, or contains no active source package.
- Root `Cargo.toml` does not list old `crates/daemon/*` runtime support
  package members.
- Active protocol DTOs are `Request` and `Response`.
- Active catalog space field is `operation_execution_space`.
- Runtime command session id field is `command_session_id`.
- Runtime operation names are:
  - `exec_command`
  - `write_command_stdin`
  - `poll_command`
  - `read_command_lines`
  - `cancel_command`

If package moves are incomplete, stop and finish phase 8 first. Do not perform
broad stale-name cleanup on top of a broken package graph.

Phase goal:

- Update active docs to the final package and folder names.
- Update packaging from legacy `eosd` package/artifact labels to
  `sandbox-daemon`, or explicitly document and isolate any temporary `eosd`
  compatibility alias.
- Remove old workspace dependency entries and stale lockfile/package metadata.
- Remove empty legacy folders.
- Run stale-name scans and classify any intentional historical or compatibility
  matches.
- Preserve the manager/runtime operation split and the protocol DTO contract.

Non-goals:

- Do not rename `sandbox-daemon`; it is still the in-sandbox daemon binary and
  crate.
- Do not rename `sandbox-protocol`, `sandbox-manager`, `sandbox-gateway-cli`,
  or `sandbox-runtime`.
- Do not rename operation APIs beyond removing stale `exec`, `poll`, and
  `cancel` operation names if they remain.
- Do not rename legitimate low-level implementation verbs such as process
  cancellation helpers unless they are exposed as old operation names, file
  names, CLI names, or catalog names.
- Do not rename `describe_daemon_operations` in this phase unless an existing
  spec already introduced a replacement operation and all callers are updated.
  It is a manager operation that talks to a sandbox daemon to fetch the runtime
  catalog; it is not the stale `daemon_operation` crate name.
- Do not delete compatibility paths such as overlay remount helpers merely
  because their names look old. Keep live compatibility code unless call-site
  and test evidence proves it is dead.
- Do not edit historical phase prompts just to remove intentionally historical
  references. Prefer excluding `docs/refactoring/sandbox-phase-[1-8]*.md` from
  stale-name enforcement.

Implementation steps:

1. Check current status:

   ```sh
   git status --short
   ```

   Preserve unrelated user changes. Do not revert files you did not change.

2. Verify the phase 8 package graph:

   ```sh
   test -d crates/sandbox-protocol
   test -d crates/sandbox-manager
   test -d crates/sandbox-gateway-cli
   test -d crates/sandbox-daemon
   test -d crates/sandbox-runtime/operation
   test -d crates/sandbox-runtime/command
   test -d crates/sandbox-runtime/workspace
   test -d crates/sandbox-runtime/namespace-process
   test -d crates/sandbox-runtime/layerstack
   test -d crates/sandbox-runtime/overlay
   test -d crates/sandbox-runtime/config
   test ! -d crates/daemon
   cargo metadata --no-deps --format-version 1 > /tmp/eos-sandbox-phase-9-metadata.json
   cargo tree -p sandbox-runtime --prefix depth
   cargo tree -p sandbox-daemon --prefix depth
   ```

   If `crates/daemon` still exists, inspect it. If it contains active Rust
   packages, stop and finish phase 8. If it is empty, remove it.

3. Run phase 8 final checks as the phase 9 baseline:

   ```sh
   cargo fmt --check --all
   cargo check -p sandbox-protocol -p sandbox-runtime -p sandbox-daemon -p sandbox-manager -p sandbox-gateway-cli -p sandbox-runtime-command -p sandbox-runtime-workspace -p sandbox-runtime-namespace-process -p sandbox-runtime-layerstack -p sandbox-runtime-overlay -p sandbox-runtime-config --tests
   cargo test -p sandbox-protocol -p sandbox-runtime -p sandbox-daemon -p sandbox-manager -p sandbox-gateway-cli -p sandbox-runtime-command -p sandbox-runtime-workspace -p sandbox-runtime-namespace-process -p sandbox-runtime-layerstack -p sandbox-runtime-overlay -p sandbox-runtime-config
   ```

   If these fail because phase 8 is incomplete, fix the package-rename fallout
   first. If they fail for a pre-existing reason unrelated to phase 9 cleanup,
   record the failure and continue only if the cleanup can be validated
   independently.

4. Clean root workspace metadata.

   In root `Cargo.toml`, ensure workspace members and dependencies use only the
   final package names:

   ```text
   sandbox-protocol
   sandbox-manager
   sandbox-gateway-cli
   sandbox-daemon
   sandbox-runtime
   sandbox-runtime-command
   sandbox-runtime-workspace
   sandbox-runtime-namespace-process
   sandbox-runtime-layerstack
   sandbox-runtime-overlay
   sandbox-runtime-config
   ```

   Remove old internal workspace dependency entries if any remain:

   ```text
   daemon_rpc_protocol
   daemon_operation
   daemon
   command
   workspace
   namespace-process
   layerstack
   overlay
   config
   ```

   Do not remove third-party crates with the same generic names from package
   dependency lists unless they are proven to be the old internal packages.

5. Clean active docs.

   Update non-refactoring docs to the final architecture:

   - `README.md`
   - `config/README.md`
   - `docs/README/**`

   Required doc cleanup:

   - Replace old `crates/daemon/*` paths with `crates/sandbox-runtime/*` paths.
   - Replace `workspace`, `command`, `layerstack`, `overlay`,
     `namespace-process`, and `config` as standalone internal package names
     with their `sandbox-runtime-*` package names when referring to packages.
   - Keep ordinary English uses of workspace, command, config, daemon, client,
     or runtime when they are not package names.
   - Move or rewrite `docs/README/daemon/daemon_operation.md` so active README
     docs no longer present `daemon_operation` as the runtime boundary. A good
     target is one runtime README such as `docs/README/sandbox-runtime.md`,
     with content updated to `sandbox-runtime` and its support packages.
   - Update `config/README.md` schema ownership from the old config crate path
     to `crates/sandbox-runtime/config/src/configs/<module-name>.rs`.

   Historical refactoring docs may keep old names when describing earlier
   phases. Do not rewrite phase 1-8 prompt files solely to satisfy stale-name
   scans.

6. Clean packaging.

   Inspect `xtask/src/main.rs` and any related tests/docs. The current packaging
   target should be the `sandbox-daemon` binary and `sandbox-daemon` artifacts,
   not the legacy `eosd` package/artifact labels.

   Preferred target shape:

   ```text
   cargo build -p sandbox-daemon --target <target> --profile <profile>
   target/<target>/<profile-dir>/sandbox-daemon
   dist/sandbox-daemon-linux-amd64
   dist/sandbox-daemon-linux-arm64
   dist/sandbox-daemon-linux-amd64.json
   dist/sandbox-daemon-linux-arm64.json
   ```

   Update all packaging code paths consistently:

   - cargo or cross build package name
   - built binary path
   - artifact file names
   - checksum filtering
   - manifest file names
   - help text
   - README or config docs that name the package artifacts

   If an external deploy path still requires `eosd-linux-*`, make
   `sandbox-daemon-linux-*` the primary artifact and add an explicit, documented
   compatibility alias. Do not silently leave `eosd` as the primary package
   label.

7. Clean stale operation and DTO names.

   Confirm active code and docs use:

   ```text
   Request
   Response
   OperationExecutionSpace
   operation_execution_space
   command_session_id
   exec_command
   poll_command
   cancel_command
   ```

   Remove or update stale active uses of:

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

   Treat `exec`, `poll`, and `cancel` as stale only when they are operation
   names, CLI names, file/module names, or catalog/manual names. Low-level
   implementation words are not automatically stale.

8. Run stale-name scans.

   Active code and active docs:

   ```sh
   rg -n "daemon_rpc_protocol|daemon_operation|crates/daemon/(rpc_protocol|operation|server|command|workspace|namespace-process|layerstack|overlay|config)" Cargo.toml Cargo.lock crates README.md config docs xtask --glob '!target/**' --glob '!docs/refactoring/sandbox-phase-[1-8]*.md'
   rg -n "sandbox-runtime[-_]operation|sandbox_runtime[_]operation" Cargo.toml Cargo.lock crates README.md config docs xtask --glob '!target/**' --glob '!docs/refactoring/sandbox-phase-[1-8]*.md'
   rg -n "operation_space|OperationRequest|OperationResponse|SandboxRequest|RoutedRequest|ManagerRequest|OperationTarget|invoke_sandbox_daemon" Cargo.toml Cargo.lock crates README.md config docs xtask --glob '!target/**' --glob '!docs/refactoring/sandbox-phase-[1-8]*.md'
   rg -n "command-id|command_id\\b" Cargo.toml Cargo.lock crates README.md config docs xtask --glob '!target/**' --glob '!docs/refactoring/sandbox-phase-[1-8]*.md'
   ```

   Packaging:

   ```sh
   rg -n "eosd|eosd-linux|-p[[:space:]]+eosd|package[[:space:]]+eosd" xtask README.md config docs crates --glob '!target/**' --glob '!docs/refactoring/sandbox-phase-[1-8]*.md'
   ```

   Runtime operation names and file names:

   ```sh
   find crates -path '*/poll.rs' -o -path '*/cancel.rs' -o -path '*/exec.rs'
   rg -n 'name: "(exec|poll|cancel)"|"exec"|"poll"|"cancel"' crates/sandbox-runtime/operation crates/sandbox-manager crates/sandbox-gateway-cli README.md docs/README --glob '!target/**'
   ```

   The scans should return no active stale matches. If a match is intentional,
   document the file, line, and reason in the final response.

9. Verify dependency boundaries.

   Manager and gateway should remain protocol clients/control-plane code, not
   runtime implementation crates:

   ```sh
   cargo tree -p sandbox-manager --prefix depth
   cargo tree -p sandbox-gateway-cli --prefix depth
   cargo tree -p sandbox-runtime-command --prefix depth
   cargo tree -p sandbox-runtime-workspace --prefix depth
   cargo tree -p sandbox-runtime-namespace-process --prefix depth
   cargo tree -p sandbox-runtime-layerstack --prefix depth
   cargo tree -p sandbox-runtime-overlay --prefix depth
   cargo tree -p sandbox-runtime-config --prefix depth
   ```

   Inspect the output for forbidden direct edges from the crate specs:

   - `sandbox-gateway-cli` must not depend directly on `sandbox-manager`,
     `sandbox-daemon`, or runtime implementation crates.
   - `sandbox-manager` must not depend directly on `sandbox-daemon`,
     `sandbox-runtime`, or runtime support crates.
   - `sandbox-protocol` must not depend on manager, gateway, daemon, or runtime
     implementation crates.
   - Runtime support crates must not depend upward on manager, gateway, daemon,
     or protocol unless the support crate spec explicitly allows it.

10. Verify packaging code.

    Minimum checks:

    ```sh
    cargo check -p xtask
    cargo test -p xtask
    cargo run -p xtask -- package --help
    ```

    If the local target is available and the package build is practical, run:

    ```sh
    rm -rf /tmp/eos-sandbox-phase-9-dist
    cargo run -p xtask -- package --out-dir /tmp/eos-sandbox-phase-9-dist
    find /tmp/eos-sandbox-phase-9-dist -maxdepth 1 -type f -print | sort
    ```

    The output should show `sandbox-daemon-linux-*` primary artifacts. If
    `eosd-linux-*` artifacts are also present, they must be documented
    compatibility aliases.

11. Run final checks:

    ```sh
    cargo fmt --check --all
    cargo check -p sandbox-protocol -p sandbox-runtime -p sandbox-daemon -p sandbox-manager -p sandbox-gateway-cli -p sandbox-runtime-command -p sandbox-runtime-workspace -p sandbox-runtime-namespace-process -p sandbox-runtime-layerstack -p sandbox-runtime-overlay -p sandbox-runtime-config -p xtask --tests
    cargo test -p sandbox-protocol -p sandbox-runtime -p sandbox-daemon -p sandbox-manager -p sandbox-gateway-cli -p sandbox-runtime-command -p sandbox-runtime-workspace -p sandbox-runtime-namespace-process -p sandbox-runtime-layerstack -p sandbox-runtime-overlay -p sandbox-runtime-config -p xtask
    cargo clippy -p sandbox-protocol -p sandbox-runtime -p sandbox-daemon -p sandbox-manager -p sandbox-gateway-cli -p sandbox-runtime-command -p sandbox-runtime-workspace -p sandbox-runtime-namespace-process -p sandbox-runtime-layerstack -p sandbox-runtime-overlay -p sandbox-runtime-config -p xtask --all-targets --no-deps -- -D warnings
    git diff --check
    ```

Expected final folder structure:

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

- `crates/daemon/` is absent or empty and removed.
- Root workspace metadata contains final package paths only.
- Active docs describe the final package structure.
- Active README docs no longer present `daemon_operation` as a live crate.
- Packaging builds and labels the primary helper artifact as
  `sandbox-daemon-linux-*`.
- Any remaining `eosd` mention is an explicit compatibility alias with a clear
  reason.
- Active code and non-historical docs use `operation_execution_space`,
  `command_session_id`, `Request`, and `Response`.
- Old operation names `exec`, `poll`, and `cancel` do not remain as operation
  names, CLI names, file/module names, or catalog/manual names.
- The CLI, manager, daemon, runtime facade, runtime support packages, and
  `xtask` build and test by package.

Final response requirements:

- Summarize the stale names and docs removed.
- Summarize the packaging artifact decision, including whether any `eosd`
  compatibility alias remains.
- List the verification commands run and their results.
- List any remaining stale-name scan matches with file, line, and reason.
- Do not claim phase 9 is complete unless final checks pass or any skipped
  checks are explicitly justified.
```
