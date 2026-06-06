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

    /// Drive one scripted request to a terminal outcome (closure).
    pub async fn run(&self, script: Script) -> anyhow::Result<Outcome>;

    /// Drive a scripted request only until `predicate` holds, then return —
    /// the closure-optional path (agent-core spec I5). Lets a live test assert a
    /// partial effect (e.g. a real file write landed) without forcing the agent
    /// loop all the way to submit_root_outcome.
    pub async fn run_until(
        &self,
        script: Script,
        predicate: impl Fn(&StreamEvent) -> bool,
    ) -> anyhow::Result<Vec<StreamEvent>>;

    /// Read back real container state for assertions.
    pub async fn read_file(&self, path: &str) -> anyhow::Result<String>;
}

impl Drop for LiveSandbox { /* tear down container */ }
```

`run_until` reuses the same early-termination seams as agent-core (pull the
engine stream to a checkpoint; `wait_until` for background workflow state). The
live tier still *defaults* to `run` (closure) because exercising the full
sandbox round-trip is the point; `run_until` is for targeted partial checks.

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

## 13. Provisioning reality (deep investigation)

A source audit refined §4/§6. The "stock image + host-injected eosd" model holds,
but the harness must clear two gaps and one injection seam. Evidence is in
`eos-runtime/src/app_state.rs`, `eos-sandbox-host/src/{provisioning,lifecycle,
bootstrap_artifact,docker}.rs`, and `eos-sandbox-host/tests/write_stdin_live.rs`.

### 13.1 The default `build()` path provisions for real — but won't bootstrap eosd yet

`AppState::builder()…build()` with a Docker config and **no** injected
transport/provisioner wires the real `DockerProviderAdapter` → `DaemonClient` →
`SandboxLifecycle::new(daemon, <repo>/sandbox/dist)` → `RequestSandboxProvisioner`
→ `HostProvisioner` automatically (`app_state.rs:417-437`). All inputs are public.
Two gaps block a clean fresh-sandbox run:

- **Gap A — no `project_dir`, so eosd bootstrap is skipped.** `setup_post_lifecycle`
  uploads eosd only when `SandboxInfo.project_dir` is non-empty
  (`lifecycle.rs:182,200,218`). `fresh_create_spec` sets no `project_dir` label and
  `CreateSandboxSpec` has no such field (`provisioning.rs:31-47`, `provider.rs:47`).
  There is **no public hook** to set it. `write_stdin_live.rs:59` sets a
  `project_dir=/testbed` label by hand.
- **Gap B — stale amd64 eosd SHA pin.** Pinned `af19…` (`bootstrap_artifact.rs:53`)
  ≠ on-disk `eosd-linux-amd64` `5b47…` → `ensure_daemon_bootstrap` fails with
  `ArtifactHashMismatch` on amd64. arm64 pin matches. The project-default platform
  is `linux/amd64`, so `SandboxLifecycle` bootstrap is currently broken there until
  the pin is bumped. (Flagged as a likely latent bug regardless of this work.)

### 13.2 Binding a pre-provisioned sandbox needs the provisioner seam exposed

To use the `write_stdin_live` pattern (provision via public host APIs, then bind
into `AppState`), the harness needs to inject a fixed-id provisioner — but
`AppStateBuilder::provisioner(...)` is `#[cfg(test)] pub(crate)` and
`RequestProvisioner`/`HostProvisioner` are unexported (`app_state.rs:51,64,302`).
Same three exposures as agent-core spec §14.4.

### 13.3 Two harness recipes (pick per how much production change is acceptable)

- **Recipe A — pure public `build()` path.** Smallest harness code; requires
  fixing Gap A (default `project_dir`) and Gap B (bump amd64 pin). Then
  `build()` → `start_request(prompt, None)` bootstraps eosd itself.
- **Recipe B — pre-provision via public host APIs** (`DockerProviderAdapter`,
  `ProviderRegistry`, `DaemonClient`, `SandboxLifecycle`, all `pub` per
  `write_stdin_live.rs`), set the `project_dir` label, then bind into `AppState`.
  Works **today on arm64**; on amd64 use raw `docker cp` to bypass Gap B. Needs
  the §13.2 provisioner exposure to bind, **or** the explicit-id branch
  `start_request(prompt, Some(id))` (`provisioning.rs:86`) — which still hits
  Gap B on amd64.

**Recommendation:** target Recipe A but treat Gap A + Gap B as small prerequisite
production fixes (set a default `project_dir`; bump the amd64 pin) so the public
`build()` path bootstraps eosd unaided. That keeps the harness thin and avoids
widening the provisioner API. Confirm with the owner before changing provisioning
defaults.
