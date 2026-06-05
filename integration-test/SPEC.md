# integration-test Module Spec

Status: draft · Scope: root-level live e2e of `sandbox` + `agent-core` ·
Companion: `agent-core/TESTING_SPEC.md`

## 1. Purpose

A single place that runs **real `agent-core` against a real sandbox (`eosd`)**
end to end. The model side is pluggable: a **scripted LLM** (deterministic,
default, the focus for now) or a **real model** (opt-in, later). The sandbox is
always real. This is the only tier that exercises file ops, OCC, LayerStack,
command sessions, and isolated workspaces through the production host path.

It lives at the **repo root**, in **neither** the `agent-core` nor the
`sandbox` workspace, so live e2e stays out of `agent-core` (per its spec, I1).

## 2. Placement & workspace

```
EphemeralOS/
  agent-core/        (workspace)   <- depended on by path
  sandbox/           (workspace)   <- NOT a cargo dep; coupling is the built eosd binary
  integration-test/  (NEW)         <- standalone package == its own workspace
```

`integration-test/Cargo.toml` is a standalone package (its own implicit
workspace). It path-depends *into* `agent-core`:

```toml
[package]
name = "integration-test"
edition = "2021"
rust-version = "1.85"

[dependencies]
eos-runtime      = { path = "../agent-core/crates/eos-runtime" }
eos-sandbox-host = { path = "../agent-core/crates/eos-sandbox-host" }
eos-testkit      = { path = "../agent-core/crates/eos-testkit", features = ["llm"] }
tokio            = { version = "1", features = ["rt-multi-thread", "macros"] }
anyhow           = "1"
serde            = { version = "1", features = ["derive"] }
ron              = "0.8"   # or serde_json, for script files

[features]
# CI on Linux runs `--features e2e`: a missing Docker/eosd is a HARD failure.
# Dev hosts run plain `cargo test` and skip the live tiers cleanly.
e2e = []
```

Dependency direction stays unilateral: `integration-test -> agent-core ->
sandbox(eos-protocol)`. Nothing depends back into `agent-core`.

## 3. What is real vs mocked

| Component | This tier |
|---|---|
| `agent-core` engine loop, tool dispatch, workflow/attempt, state | **real** |
| Sandbox: `eosd`, file ops, OCC, LayerStack, command sessions | **real** (Docker) |
| Provisioning / daemon RPC | **real** (`DockerProviderAdapter` + production provisioner) |
| LLM | **scripted now** (`eos-testkit::ScriptedSource`); **real model later** |

## 4. No custom image — host injects `eosd`

The host uploads the `eosd` binary into a **stock** container at provision time
(`SandboxLifecycle` + `ensure_daemon_bootstrap`, `eos-sandbox-host`). Therefore:

- **No Docker image is built or packaged by this module.** Any base image works
  (default: `sweevo-dask__dask-10042:latest`, `linux/amd64`).
- The **only** artifact required is `sandbox/dist/eosd-linux-amd64`.

### 4.1 `eosd` dist build (one repo-level target)

`make eosd-dist` builds the binary **inside a linux container** and drops it in
`sandbox/dist/` (so mac devs need no cross toolchain):

```
eosd-dist:
    docker run --rm -v $(PWD):/src -w /src/sandbox <rust-linux-image> \
        cargo build -p eosd --release --target x86_64-unknown-linux-musl
    cp sandbox/target/.../eosd  sandbox/dist/eosd-linux-amd64
```

The harness reads `../sandbox/dist/eosd-linux-amd64` as a plain file path.

## 5. Crate layout

```
integration-test/
├── Cargo.toml
├── src/
│   ├── lib.rs          # exports LiveSandbox + Script
│   └── harness.rs      # LiveSandbox fixture (the one nontrivial piece)
├── scripts/            # loaded mock scripts (.ron)
│   ├── write_then_finish.ron
│   └── isolated_workspace.ron
└── tests/
    ├── smoke.rs
    ├── file_ops.rs
    ├── write_stdin.rs          # ported from the removed agent-core live test
    └── isolated_workspace.rs
```

## 6. `LiveSandbox` harness contract

`harness.rs` owns all container/`AppState` wiring once, so each test is ~10
lines. Built **only** on `eos-sandbox-host` production APIs (no raw `docker`
calls):

```rust
pub struct LiveSandbox { /* AppState bound to a live sandbox + scripted source */ }

impl LiveSandbox {
    /// Returns None when EOS_LIVE_E2E_IMAGE is unset and the `e2e` feature is
    /// off (graceful skip). Hard-fails under `--features e2e`.
    pub async fn spawn() -> anyhow::Result<Option<Self>>;

    /// Drive one scripted request to a terminal outcome.
    pub async fn run(&self, script: Script) -> anyhow::Result<Outcome>;

    /// Read back real container state for assertions.
    pub async fn read_file(&self, path: &str) -> anyhow::Result<String>;
}

impl Drop for LiveSandbox { /* tear down container */ }
```

Composition inside `spawn()`:
`DockerProviderAdapter::connect` → `DaemonClient` → production provisioner with
`artifact_dir = ../sandbox/dist` → `AppState::builder()` with
`event_source_factory` returning `ScriptedSource` (LLM tier).

## 7. LLM tiers (the bridge)

The tier is a single `EventSource` choice behind one selector:

```rust
pub enum Llm { Scripted(Script), Real }   // Real -> eos-engine ProviderEventSource
```

`Scripted` reuses `eos-testkit::ScriptedSource` + the script loader. `Real`
returns the production `ProviderEventSource`. The test body is identical across
tiers; only the selector changes. **Now:** implement `Scripted` only; leave a
typed hole for `Real`.

## 8. Script loading

Reuse `eos-testkit::script` (defined in the agent-core spec §4). A script is
per-agent ordered turns; each turn is a tool call or text. Loader validates each
tool against the agent's `allowed_tools`/`terminals` at load time (fail fast
with file/line, before a live container run). Example:

```ron
{
  "root": [
    { tool: "write_file", input: { path: "/work/a.txt", content: "hello" } },
    { tool: "run_shell",  input: { command: "cat /work/a.txt" } },
    { tool: "submit_root_outcome", input: { status: "success", summary: "ok" } },
  ],
}
```

## 9. Run & skip semantics

- Default `cargo test -p integration-test`: live tiers **skip** cleanly if
  `EOS_LIVE_E2E_IMAGE` is unset (returns `Ok(None)` from `spawn`).
- CI: `EOS_LIVE_E2E_IMAGE=sweevo-dask__dask-10042:latest cargo test -p
  integration-test --features e2e` — missing Docker/`eosd` is a hard failure.
- Live tests carry `#[ignore = "requires Docker and EOS_LIVE_E2E_IMAGE"]` so a
  bare `cargo test` never blocks on Docker.

## 10. Test inventory (initial)

| Test | Verifies |
|---|---|
| `smoke.rs` | scripted agent → real `eosd` round-trip; terminal outcome persists |
| `file_ops.rs` | `write_file`/`edit_file` go through real LayerStack + OCC publish |
| `write_stdin.rs` | ported scenario: empty write-stdin response is not replayed |
| `isolated_workspace.rs` | `enter_isolated_workspace`/`exit` lifecycle vs real `eosd` |

## 11. Acceptance criteria

- **AC1** `cargo test -p integration-test` with no Docker → all live tests skip,
  exit 0.
- **AC2** With `--features e2e` + `EOS_LIVE_E2E_IMAGE` set on Linux, `smoke.rs`
  provisions a real container, runs a scripted agent, and asserts on real file
  state.
- **AC3** The module builds **no** Docker image and cargo-depends on **no**
  `sandbox/` crate (only the `eos-protocol` edge that arrives transitively via
  `agent-core`). The sole sandbox coupling is `sandbox/dist/eosd-linux-amd64`.
- **AC4** Swapping `Llm::Scripted` → `Llm::Real` requires no change to any test
  body (only the selector), confirming the bridge.
- **AC5** `harness.rs` uses only `eos-sandbox-host` public APIs — no raw
  `docker` shell-outs.

## 12. Build order

1. `make eosd-dist` target + verify `sandbox/dist/eosd-linux-amd64` exists.
2. `integration-test/` skeleton: standalone `Cargo.toml`, `src/harness.rs`
   `LiveSandbox::spawn` on `DockerProviderAdapter` + production provisioner.
3. `smoke.rs` with one `.ron` script reusing `eos-testkit::ScriptedSource`.
4. Port `write_stdin.rs` from the deleted agent-core live test.
5. Add `file_ops.rs`, `isolated_workspace.rs`.
6. (Later) implement `Llm::Real` tier.
