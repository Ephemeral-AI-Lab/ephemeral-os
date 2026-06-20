# Phase 3 Prompt: Create `sandbox-daemon`

Use this prompt after phase 2 has completed.

```text
You are working in:

/Users/yifanxu/machine_learning/LoVC/ephemeral-ai/ephemeral-os

Task:

Implement phase 3 only: create the `sandbox-daemon` package by moving the
daemon server package and folding the namespace helper adapters into it.

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
- `crates/daemon/command`, `workspace`, `namespace-process`, `layerstack`,
  `overlay`, and `config` still exist under `crates/daemon`.

If this starting state is not true, stop and report that phase 2 is not
complete. Do not implement phase 3 against the old pre-phase-2 layout.

Phase goal:

- Move `crates/daemon/server` to `crates/sandbox-daemon`.
- Rename package `daemon` to `sandbox-daemon`.
- Add the target binary name `sandbox-daemon`.
- Keep daemon operation behavior unchanged.

Package move:

```text
daemon -> sandbox-daemon
```

Expected resulting path and package:

```text
Path:    crates/sandbox-daemon
Package: sandbox-daemon
Import:  sandbox_daemon
Binaries:
  sandbox-daemon
```

Keep in `sandbox-daemon`:

- Daemon process entrypoint.
- `serve`, `ns-runner`, and `ns-holder` subcommand routing.
- Unix/TCP listener lifecycle.
- Request framing at the server edge.
- Dispatching decoded requests to `sandbox-runtime`.
- Runtime wiring that builds `SandboxRuntimeOperations`.

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
   rg -n "sandbox-runtime = \\{ path = \"crates/sandbox-runtime/operation\" \\}" Cargo.toml
   ```

3. Run and record baseline results before file moves:

   ```sh
   cargo fmt --check -p sandbox-protocol -p sandbox-runtime -p daemon
   cargo check -p sandbox-protocol -p sandbox-runtime -p daemon
   cargo test -p sandbox-runtime -p daemon
   ```

   If any command fails, record that it was pre-existing and continue only if
   the failure is unrelated to the phase 3 move.

4. Move the server package:

   ```text
   crates/daemon/server -> crates/sandbox-daemon
   ```

5. Keep the namespace helper adapters in `crates/sandbox-daemon/src`:

   ```text
   crates/sandbox-daemon/src/main.rs
   crates/sandbox-daemon/src/serve.rs
   crates/sandbox-daemon/src/runner.rs
   crates/sandbox-daemon/src/holder.rs
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
   - Replace workspace dependency `daemon = { path = "crates/daemon/server" }`
     with `sandbox-daemon = { path = "crates/sandbox-daemon" }`.

8. Update `crates/sandbox-daemon/Cargo.toml`:

   ```toml
   [package]
   name = "sandbox-daemon"

   [[bin]]
   name = "sandbox-daemon"
   path = "src/main.rs"
   ```

   The dependency set should come from the old daemon package and helper
   adapters, without depending on `daemon`:

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

12. Preserve spawn behavior:

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
rg -n "crates/daemon/server|^daemon = \\{" Cargo.toml crates --glob 'Cargo.toml'
rg -n "^daemon\\.workspace|^sandbox-daemon\\.workspace" crates --glob 'Cargo.toml'
rg -n "^name = \"daemon\"$" crates --glob 'Cargo.toml'
rg -n "\\b(DaemonServer|DaemonError)\\b" crates/sandbox-daemon --glob '*.rs'
rg -n "\\b(SandboxDaemonServer|SandboxDaemonError)\\b" crates/sandbox-daemon --glob '*.rs'
cargo fmt --check -p sandbox-daemon -p sandbox-runtime
cargo check -p sandbox-daemon -p sandbox-runtime
cargo check -p sandbox-daemon --bins
cargo test -p sandbox-daemon -p sandbox-runtime
```

The first three `rg` scans should return no matches. The
`\\b(DaemonServer|DaemonError)\\b` scan should return no matches. The
`\\b(SandboxDaemonServer|SandboxDaemonError)\\b` scan should show the renamed
types.

Final response requirements:

- Summarize the package move and `sandbox-daemon` merge.
- State whether phase 2 starting-state checks passed.
- State whether baseline checks had pre-existing failures.
- State final verification commands and results.
- Do not claim phase 4 work was done.
```
