# Phase 3 Prompt: Create `sandbox-daemon`

Use this prompt after phase 2 has completed.

```text
You are working in:

/Users/yifanxu/machine_learning/LoVC/ephemeral-ai/ephemeral-os

Task:

Implement phase 3 only: create the `sandbox-daemon` package by moving the
daemon server package and folding the `eosd` binary adapter into it.

Before editing, read:

- docs/refactoring/sandbox-implementation-guide.md
- docs/refactoring/sandbox-daemon.md
- docs/refactoring/sandbox-runtime.md
- docs/refactoring/sandbox-manager-daemon-split.md

Required starting state:

- `crates/sandbox-protocol` exists.
- `crates/sandbox-runtime/operation` exists.
- `crates/daemon/operation` no longer exists.
- Root `Cargo.toml` has workspace dependency:
  `sandbox-runtime = { path = "crates/sandbox-runtime/operation" }`.
- `crates/daemon/server` still exists as package `daemon`.
- `crates/daemon/eosd` still exists as package `eosd`.
- `crates/daemon/command`, `workspace`, `namespace-process`, `layerstack`,
  `overlay`, and `config` still exist under `crates/daemon`.

If this starting state is not true, stop and report that phase 2 is not
complete. Do not implement phase 3 against the old pre-phase-2 layout.

Phase goal:

- Move `crates/daemon/server` to `crates/sandbox-daemon`.
- Rename package `daemon` to `sandbox-daemon`.
- Merge `crates/daemon/eosd/src` into the `sandbox-daemon` package.
- Preserve a compatibility binary named `eosd`.
- Add the target binary name `sandbox-daemon`.
- Keep daemon operation behavior unchanged.

Package move:

```text
daemon -> sandbox-daemon
eosd   -> compatibility binary inside sandbox-daemon
```

Expected resulting path and package:

```text
Path:    crates/sandbox-daemon
Package: sandbox-daemon
Import:  sandbox_daemon
Binaries:
  sandbox-daemon
  eosd
```

Keep in `sandbox-daemon`:

- Daemon process entrypoint.
- `serve`, `ns-runner`, and `ns-holder` subcommand routing.
- Unix/TCP listener lifecycle.
- Request framing at the server edge.
- Dispatching decoded requests to `sandbox-runtime`.
- Runtime wiring that builds `SandboxRuntimeOperations`.
- Temporary `eosd` compatibility entrypoint.

Keep out of `sandbox-daemon`:

- Sandbox creation/destruction.
- Manager operation catalog.
- CLI behavior for `sandbox-gateway-cli`.
- Concrete daemon operation implementation beyond dispatch wiring.
- Low-level command, workspace, layerstack, overlay, or config primitives.

Implementation steps:

1. Check current status:

   ```sh
   git status --short
   ```

2. Verify the phase 2 starting state:

   ```sh
   test -d crates/sandbox-protocol
   test -d crates/sandbox-runtime/operation
   test ! -d crates/daemon/operation
   test -d crates/daemon/server
   test -d crates/daemon/eosd
   rg -n "sandbox-runtime = \\{ path = \"crates/sandbox-runtime/operation\" \\}" Cargo.toml
   ```

3. Run and record baseline results before file moves:

   ```sh
   cargo fmt --check -p sandbox-protocol -p sandbox-runtime -p daemon -p eosd
   cargo check -p sandbox-protocol -p sandbox-runtime -p daemon -p eosd
   cargo test -p sandbox-runtime -p daemon -p eosd
   ```

   If any command fails, record that it was pre-existing and continue only if
   the failure is unrelated to the phase 3 move.

4. Move the server package:

   ```text
   crates/daemon/server -> crates/sandbox-daemon
   ```

5. Merge the `eosd` adapter source into `crates/sandbox-daemon/src`:

   ```text
   crates/daemon/eosd/src/main.rs   -> crates/sandbox-daemon/src/main.rs
   crates/daemon/eosd/src/daemon.rs -> crates/sandbox-daemon/src/serve.rs
   crates/daemon/eosd/src/runner.rs -> crates/sandbox-daemon/src/runner.rs
   ns-holder adapter from main.rs   -> crates/sandbox-daemon/src/holder.rs
   ```

6. Move the server library modules under `src/server/`:

   ```text
   crates/sandbox-daemon/src/connection.rs -> src/server/connection.rs
   crates/sandbox-daemon/src/dispatch.rs   -> src/server/dispatch.rs
   crates/sandbox-daemon/src/error.rs      -> src/server/error.rs
   crates/sandbox-daemon/src/lifecycle.rs  -> src/server/lifecycle.rs
   crates/sandbox-daemon/src/runtime.rs    -> src/server/runtime.rs
   ```

   Keep `src/lib.rs` as the public daemon library surface and re-export the
   server types from `src/server/mod.rs`.

7. Update root `Cargo.toml`:

   - Replace workspace member `crates/daemon/server` with
     `crates/sandbox-daemon`.
   - Remove workspace member `crates/daemon/eosd`.
   - Replace workspace dependency `daemon = { path = "crates/daemon/server" }`
     with `sandbox-daemon = { path = "crates/sandbox-daemon" }`.
   - Remove workspace dependency `eosd = { path = "crates/daemon/eosd" }`.

8. Update `crates/sandbox-daemon/Cargo.toml`:

   ```toml
   [package]
   name = "sandbox-daemon"

   [[bin]]
   name = "sandbox-daemon"
   path = "src/main.rs"

   [[bin]]
   name = "eosd"
   path = "src/main.rs"
   ```

   The dependency set should be the union of the old `daemon` and `eosd`
   packages, without depending on `daemon` or `eosd`:

   ```toml
   command.workspace = true
   config.workspace = true
   namespace-process.workspace = true
   sandbox-protocol.workspace = true
   sandbox-runtime.workspace = true
   workspace.workspace = true
   anyhow.workspace = true
   serde_json = { workspace = true, features = ["preserve_order"] }
   tokio.workspace = true
   tokio-util.workspace = true
   thiserror.workspace = true
   ```

9. Rename server types after the package move compiles:

   ```text
   DaemonServer -> SandboxDaemonServer
   DaemonError  -> SandboxDaemonError
   ```

   `ServerConfig` may remain named `ServerConfig` because it is local to the
   daemon package.

10. Update imports:

    - Replace old binary references to `daemon::DaemonServer` with
      `sandbox_daemon::SandboxDaemonServer`.
    - Replace old binary references to `daemon::ServerConfig` with
      `sandbox_daemon::ServerConfig`.
    - Remove any dependency on an external `daemon` crate.

11. Preserve binary interfaces:

    Target interface:

    ```text
    sandbox-daemon serve
    sandbox-daemon ns-runner
    sandbox-daemon ns-holder
    ```

    Temporary compatibility interface:

    ```text
    eosd daemon
    eosd ns-runner
    eosd ns-holder
    ```

    `eosd daemon` should call the same implementation as
    `sandbox-daemon serve`. Do not require downstream scripts to switch to
    `sandbox-daemon` in this phase.

12. Preserve spawn behavior:

    - If invoked as `eosd daemon --spawn`, spawned foreground args should keep
      the compatibility subcommand `daemon`.
    - If invoked as `sandbox-daemon serve --spawn`, spawned foreground args
      should use `serve`.
    - Keep daemon client exit codes `97` and `98`.

13. Preserve namespace helper behavior:

    - `ns-runner` must still support the current runner flags and
      `command-request.json` side-channel.
    - `ns-holder` must still preserve holder exit codes.

Non-goals:

- Do not create `sandbox-manager`.
- Do not create `sandbox-gateway-cli`.
- Do not remove the `eosd` compatibility binary.
- Do not rename runtime support packages:
  - `command`
  - `workspace`
  - `namespace-process`
  - `layerstack`
  - `overlay`
  - `config`
- Do not change daemon operation semantics.
- Do not change namespace runner protocol DTOs.
- Do not remove `command-request.json`.
- Do not add Docker, Firecracker, or sandbox lifecycle implementation.
- Do not make `sandbox-daemon` depend on `sandbox-manager` or
  `sandbox-gateway-cli`.

Acceptance checks:

```sh
test -d crates/sandbox-daemon
test ! -d crates/daemon/server
test ! -d crates/daemon/eosd
rg -n "crates/daemon/(server|eosd)|^daemon = \\{|^eosd = \\{" Cargo.toml crates --glob 'Cargo.toml'
rg -n "^daemon\\.workspace|^eosd\\.workspace" crates --glob 'Cargo.toml'
rg -n "^name = \"daemon\"$" crates --glob 'Cargo.toml'
rg -n "\\b(DaemonServer|DaemonError)\\b" crates/sandbox-daemon --glob '*.rs'
rg -n "\\b(SandboxDaemonServer|SandboxDaemonError)\\b" crates/sandbox-daemon --glob '*.rs'
cargo fmt --check -p sandbox-daemon -p sandbox-runtime
cargo check -p sandbox-daemon -p sandbox-runtime
cargo check -p sandbox-daemon --bins
cargo test -p sandbox-daemon -p sandbox-runtime
```

The first three `rg` scans should return no matches. The
`\\b(DaemonServer|DaemonError)\\b` scan should return no matches unless a temporary
compatibility alias is explicitly documented in the final response. The
`\\b(SandboxDaemonServer|SandboxDaemonError)\\b` scan should show the renamed
types.

Final response requirements:

- Summarize the package move and `eosd` merge.
- State whether phase 2 starting-state checks passed.
- State whether baseline checks had pre-existing failures.
- State final verification commands and results.
- Call out whether any compatibility aliases remain.
- Call out that `eosd` remains as a compatibility binary.
- Do not claim phase 4 work was done.
```
