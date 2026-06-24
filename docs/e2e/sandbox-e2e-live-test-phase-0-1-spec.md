# `sandbox-e2e-live-test` — Phase 0 + Phase 1 Implementation Spec

Implementation-ready spec for **Phase 0 (Scaffold the crate)** and **Phase 1
(Harness core + one operation)** of the parent design
(`docs/e2e/sandbox-e2e-live-test-spec.md`). The parent design is fixed; **live
code wins on every conflict** and each load-bearing fact is cited to a verified
`file:line` (see the *Anchor Ledger*). This document is **spec only** — it does
not create the crate, edit `Cargo.toml`, or write any code.

The runner is a black-box live E2E runner: it drives real Docker sandboxes
exclusively through the public `sandbox-cli` → `sandbox-gateway` socket boundary.
Phases 0–1 stand up the harness library, the build-time include generation, and
the first two leaf tests (one manager op, one runtime op), buildable and
skip-safe on any machine.

---

## Phase Boundary

**Phase 0 — Scaffold.** In one atomic change, add the line
`"crates/sandbox-e2e-live-test",` to the `Cargo.toml` `members` array **and**
create the crate so the workspace still builds: `Cargo.toml` (lib +
`[[bin]] eos-e2e`), `build.rs` (empty-tree-safe include generator), `src/lib.rs`
(re-export surface), `src/bin/eos-e2e.rs` (stub), and an empty `tests/` tree with
the two root binaries (`tests/manager.rs`, `tests/runtime.rs`) plus
`tests/support/mod.rs`. Adding the member entry without the manifest is what
would break the workspace build (`Cargo.toml:3-17`), so both halves ship
together. **Acceptance:** `cargo build -p sandbox-e2e-live-test`, a workspace-wide
`cargo build`, and `cargo clippy -p sandbox-e2e-live-test --all-targets` all
succeed under the workspace lints.

**Phase 1 — Harness core + one operation.** Implement the harness library
modules `src/config.rs` (minimal `RunConfig` + `run-manifest.json` load path
only), `src/cli_client.rs` (invoke `sandbox-cli`, capture the call record, parse
the single NDJSON response line, locate `error` on stdout-or-stderr),
`src/fixtures.rs` (lazy `Harness` reading `EOS_E2E_RUN_ROOT` → manifest,
`provision_sandbox` reading the runtime-assigned `/id`, RAII `Sandbox` drop →
`destroy_sandbox`), `src/gateway.rs` (**attach mode only**: validate/await the
`--gateway-socket` path, no spawn), `src/assertion.rs` (only `ok`, `field`, and
the negative/exit helper the leaves need), `tests/support/mod.rs` (skip-safe
harness surface), the `build.rs` per-leaf `#[path]` include generation, and two
leaf tests: `tests/manager/lifecycle/create_sandbox.rs` (matrix **M1**) and
`tests/runtime/command/exec_command.rs` (matrix **R1**). **Acceptance:** the crate
compiles; a bare `cargo test -p sandbox-e2e-live-test` with `EOS_E2E_RUN_ROOT`
unset **skips cleanly** (no panic); with `EOS_E2E_RUN_ROOT` pointing at a
hand-written `run-manifest.json` for a real-runtime gateway, the two leaves run
green under `--test-threads=1`.

**Out of scope (Phases 2–4 — named, not designed here):**

- The full operation matrix M2–M5, R2–R8, and routing negatives N1/N2; and the
  `assertion.rs` helpers they need (`err_detail`, `non_decreasing`,
  `offsets_monotonic`). Phase 2.
- The orchestrator `eos-e2e` internals — `clap` arg parsing, preflight, build
  phase, env export, aggregation from `result.json`, `summary.json` / timing
  sub-objects, cleanup orchestration. In Phases 0–1 `eos-e2e` is a **stub** and
  the run env is set by hand. Phase 3.
- Observability polling, `observability.json`, P1 (cgroup CPU/mem), P2
  (queue-wait). Phase 4.
- Spawn-mode gateway and label-based orphan cleanup. Deferred (Open Items #1, #2).
- `report.rs`, `cleanup.rs`, `exchange.jsonl` / `result.json` artifact writing,
  `snapshot_observability`, `RerunFailedFrom`, `CleanupPolicy`, `BuildSource`,
  `TestSelection`, `uuid`/`sha2`/`time`-derived `run_id`. **None are created in
  Phases 0–1** (see *Dependencies* and *Prefer-less ledger*).
- `run_id` **derivation** and any charset-validation-at-parse of it. Phase 1
  reads `run_id` verbatim from the hand-written manifest and uses it only as the
  workspace-dir prefix; deriving it (and validating its charset) is Phase 3.

---

## Phase 0 — File Manifest

Crate root: `crates/sandbox-e2e-live-test/`. Every file below is created in the
Phase 0 change. "Responsibility" is the single job of the stub.

| File | Phase 0 responsibility (single job) |
|------|--------------------------------------|
| `Cargo.toml` | Declare the crate: `[package]` (workspace inheritance), `[lib]`, `[[bin]] eos-e2e`, `[dependencies]` (exactly `anyhow`/`serde`/`serde_json`), `[lints] workspace = true`. |
| `build.rs` | Generate `$OUT_DIR/manager_mods.rs` and `$OUT_DIR/runtime_mods.rs` by walking `tests/manager/**/*.rs` and `tests/runtime/**/*.rs`; empty trees emit empty files (build still succeeds). |
| `src/lib.rs` | Crate root: declare and re-export the harness surface (`config`, `cli_client`, `fixtures`, `gateway`, `assertion`) consumed by `tests/support`. Phase 0: module declarations may be empty stubs that compile. |
| `src/bin/eos-e2e.rs` | Orchestrator **stub**: a `main` that prints a "not implemented in Phase 0–1; set EOS_E2E_RUN_ROOT by hand and run cargo test" notice and exits non-zero. No preflight/build/aggregate logic. |
| `src/config.rs` | (stub in Phase 0; specified in Phase 1) `RunConfig` + manifest load. |
| `src/cli_client.rs` | (stub in Phase 0; specified in Phase 1) CLI driver + call record. |
| `src/fixtures.rs` | (stub in Phase 0; specified in Phase 1) `Harness`, `Sandbox`. |
| `src/gateway.rs` | (stub in Phase 0; specified in Phase 1) attach-mode socket readiness. |
| `src/assertion.rs` | (stub in Phase 0; specified in Phase 1) `ok`, `field`, exit helper. |
| `tests/support/mod.rs` | Skip-safe harness entry: `pub fn harness() -> Option<&'static Harness>`. |
| `tests/manager.rs` | Manager test binary: `#[path] mod support;` + `include!(concat!(env!("OUT_DIR"), "/manager_mods.rs"))`. |
| `tests/runtime.rs` | Runtime test binary: `#[path] mod support;` + `include!(concat!(env!("OUT_DIR"), "/runtime_mods.rs"))`. |

The leaf-test directories (`tests/manager/lifecycle/`,
`tests/runtime/command/`) and the two leaf files are created in **Phase 1**; in
Phase 0 the generated include lists are empty and the two root binaries compile
to empty test binaries.

### `Cargo.toml` (resolved to confirmed workspace deps)

All external deps are consumed via `dep.workspace = true` (CLAUDE.md convention).
The line numbers below are the `[workspace.dependencies]` definitions in the root
`Cargo.toml`, each personally confirmed in this run.

```toml
[package]
name = "sandbox-e2e-live-test"
version.workspace = true        # Cargo.toml:20  (0.1.0)
edition.workspace = true        # Cargo.toml:21  (2021)
rust-version.workspace = true   # Cargo.toml:22  (1.85)
license.workspace = true        # Cargo.toml:23  (MIT)

[lib]
path = "src/lib.rs"

[[bin]]
name = "eos-e2e"
path = "src/bin/eos-e2e.rs"

[dependencies]
anyhow.workspace = true         # Cargo.toml:48  — fixture/manifest error context
serde = { workspace = true }    # Cargo.toml:27  — derive RunConfig/manifest structs
serde_json.workspace = true     # Cargo.toml:28  — parse the NDJSON response line; assertion Value walks

[lints]
workspace = true                # inherits Cargo.toml:70-83
```

The Phase 0–1 manifest is exactly `anyhow + serde + serde_json`. `clap`
(`Cargo.toml:49`) is **not** added in Phase 0–1: the `eos-e2e` stub is a bare
print-and-exit `main` (below) that parses nothing, so a `clap` dependency would
be unused (a clippy/unused-dependency nit). Arg parsing is a Phase-3
orchestrator-surface concern and arrives with the orchestrator.

**Deps deliberately omitted in Phases 0–1** (parent lists them for later phases;
prefer-less keeps them out until a module needs them):

| Dep | Parent line | Why omitted in Phase 0–1 |
|-----|-------------|---------------------------|
| `tokio` "full" | `Cargo.toml:43` | The black-box path shells out to `sandbox-cli` via `std::process::Command`; no async runtime is owned by the harness. The async `GatewayClient` (`client.rs:30`) lives behind the CLI, not in this crate. |
| `tokio-util` | `Cargo.toml:44` | `CancellationToken` is for spawn-mode gateway shutdown (Phase 3 / Open Items #1). |
| `thiserror` | `Cargo.toml:40` | `anyhow` covers fixture/manifest errors in Phase 1; no typed public error enum is exported yet. |
| `uuid` | `Cargo.toml:42` | Used only for `run_id` derivation / request correlation in Phase 3. Ids are runtime-assigned; manifests are hand-written in Phase 1. |
| `time` | `Cargo.toml:41` | UTC run-dir timestamps belong to the orchestrator (Phase 3). |
| `sha2` | `Cargo.toml:34` | Deterministic `run_id` slug is Phase 3. |
| `futures-util` | `Cargo.toml:52` | Parent explicitly states it is not required (cargo test owns parallelism). |
| `clap` | `Cargo.toml:49` | Orchestrator arg parsing. The `eos-e2e` stub is a bare print-and-exit `main` (below) that parses nothing; flags are a Phase 3 orchestrator-surface concern and arrive with the orchestrator. |
| `sandbox-protocol` | `Cargo.toml:66` | Optional typed DTOs; default is `serde_json::Value` to stay strictly behind the public socket boundary. Not added. |

### `src/lib.rs` — re-export surface

`lib.rs` declares the harness modules and re-exports exactly what
`tests/support/mod.rs` and the leaves consume. SRP: it is the crate's public
surface, nothing more.

```rust
//! Black-box live E2E harness for EphemeralOS, driven through `sandbox-cli`.

pub mod assertion;
pub mod cli_client;
pub mod config;
pub mod fixtures;
pub mod gateway;

pub use cli_client::{CallRecord, CliClient};
pub use config::RunConfig;
pub use fixtures::{Harness, Sandbox};
```

### `src/bin/eos-e2e.rs` — Phase 0 stub

SRP: be a buildable, honest placeholder for the Phase 3 orchestrator.

```rust
fn main() -> std::process::ExitCode {
    eprintln!(
        "eos-e2e orchestrator is not implemented in Phase 0-1. \
         Set EOS_E2E_RUN_ROOT to a directory containing run-manifest.json \
         (pointing at a real-runtime gateway socket), then run \
         `cargo test -p sandbox-e2e-live-test -- --test-threads=1`."
    );
    std::process::ExitCode::from(2)
}
```

### `build.rs` — Phase 0 skeleton (full contract in Phase 1)

SRP: emit one `$OUT_DIR/<scope>_mods.rs` per scope from the leaf tree, with
correct `rerun-if-changed` triggers, tolerating an empty tree.

```rust
use std::path::Path;

fn main() {
    let manifest_dir = std::env::var("CARGO_MANIFEST_DIR").expect("CARGO_MANIFEST_DIR set by cargo");
    let out_dir = std::env::var("OUT_DIR").expect("OUT_DIR set by cargo");
    for scope in ["manager", "runtime"] {
        generate_scope_includes(Path::new(&manifest_dir), Path::new(&out_dir), scope);
    }
}
// generate_scope_includes: walk tests/<scope>/**/*.rs, emit #[path] mod <slug>; lines.
// Empty/absent tree => empty generated file. See Phase 1 build.rs contract.
```

In Phase 0 both `tests/manager/` and `tests/runtime/` are empty, so each
generated file is empty and the two root binaries compile to empty test
binaries. This is the *empty-tree-builds* invariant.

### `tests/manager.rs` / `tests/runtime.rs` — root binaries

Each is a stable two-line binary mirroring the repo convention at
`crates/sandbox-daemon/tests/unit.rs:3-4,32-55` (`#[path]` + `include!`). Adding
an operation is adding one leaf file — no registry edit.

```rust
// tests/manager.rs
#[path = "support/mod.rs"]
mod support;
include!(concat!(env!("OUT_DIR"), "/manager_mods.rs"));
```

```rust
// tests/runtime.rs
#[path = "support/mod.rs"]
mod support;
include!(concat!(env!("OUT_DIR"), "/runtime_mods.rs"));
```

### `Cargo.toml` members edit (exact line position)

The `members` array is `Cargo.toml:3-17`; the last entry is `"xtask",` on
**line 16** and the closing `]` is on **line 17**. Insert the new member
**inside** the array, after line 16 and before line 17:

```toml
    "xtask",
    "crates/sandbox-e2e-live-test",   # NEW — inserted after line 16, before `]` on line 17
]
```

Resulting array (positions only — do not reflow the other entries):

```toml
members = [
    "crates/sandbox-runtime/operation",
    ...
    "xtask",
    "crates/sandbox-e2e-live-test",
]
```

---

## Phase 1 — Module Specs

Each module is one job. Signatures only — no bodies. `serde_json::Value` is the
response carrier (no typed DTOs), per the black-box boundary.

### `src/config.rs`

**SRP:** load `EOS_E2E_RUN_ROOT` → `{run_root}/run-manifest.json` into the
minimal `RunConfig` the fixtures need; nothing else (no clap orchestrator
surface, no defaults beyond the manifest).

```rust
use std::path::PathBuf;

/// Minimal Phase-1 run configuration, sourced entirely from the manifest under
/// `EOS_E2E_RUN_ROOT`. The full parent `RunConfig` (max_parallel, BuildSource,
/// CleanupPolicy, TestSelection, timeouts) is deferred to Phase 3.
pub struct RunConfig {
    pub run_root: PathBuf,        // EOS_E2E_RUN_ROOT (the one cross-process env contract)
    pub gateway_socket: PathBuf,  // manifest.gateway_socket — passed to sandbox-cli --gateway-socket
    pub run_id: String,           // manifest.run_id — read verbatim; prefixes per-test workspace dirs
    pub image: String,            // manifest.image — default image for provision_sandbox
}

impl RunConfig {
    /// Returns `Ok(None)` when `EOS_E2E_RUN_ROOT` is unset (the skip signal);
    /// `Ok(Some(_))` when the env is set and the manifest parses; `Err` only when
    /// the env is set but the manifest is missing/invalid (a real misconfig).
    pub fn from_env() -> anyhow::Result<Option<RunConfig>>;
}
```

**Design Question 3 — minimal `RunConfig`, each field load-bearing for
Phase 1:**

| Field | Why Phase 1 needs it | Evidence |
|-------|----------------------|----------|
| `run_root` | Root for the workspace dirs each test passes as `--workspace-root` (provisioning, DQ 7) and the only cross-process env contract. | parent Config Schema; `EOS_E2E_RUN_ROOT` |
| `gateway_socket` | Every `sandbox-cli` call targets it via the global `--gateway-socket` flag. | `output.rs:28-29` (`gateway_socket_path` global flag) |
| `run_id` | Read verbatim from the manifest; prefixes per-test workspace dirs under `run_root` (`{run_root}/work/{run_id}-{slug}`) so parallel leaves never collide. | parent Config Schema; `EOS_E2E_RUN_ROOT` manifest |
| `image` | `create_sandbox` requires `--image`; the leaf uses the manifest image as the default for `provision_sandbox`. | `create_sandbox.rs:18-26` |

Parent fields **excluded** in Phase 1 (prefer-less; each is Phase 3+):
`max_parallel`, `tests: TestSelection`, `build: BuildSource`, `cli_timeout`,
`gateway_ready_timeout` (the `gateway.rs` readiness poll uses a fixed Phase-1
constant, not a config knob), `cleanup: CleanupPolicy`.

### `src/cli_client.rs`

**SRP:** invoke the `sandbox-cli` wrapper once per call, capture the full call
record, parse the single NDJSON response line, and surface where the `error`
landed (stdout vs stderr) via the exit code.

```rust
use std::path::PathBuf;
use serde_json::Value;

/// One captured `sandbox-cli` invocation. `request_json` is `None` on the
/// black-box path because the CLI never echoes the wire request to stdio (it is
/// written only to the socket, `client.rs:36`); the field exists for parity with
/// the parent record and future request-constructing callers.
pub struct CallRecord {
    pub argv: Vec<String>,
    pub request_json: Option<Value>,
    pub response_json: Value,
    pub exit_code: i32,
    pub stdout: String,
    pub stderr: String,
    pub latency_ms: u128,
}

pub struct CliClient {
    cli_path: PathBuf,       // resolved sandbox-cli (see DQ 1)
    gateway_socket: PathBuf, // forwarded as --gateway-socket
}

impl CliClient {
    pub fn new(cli_path: PathBuf, gateway_socket: PathBuf) -> Self;

    /// Run `sandbox-cli manager <operation> <args...>` and capture the record.
    pub fn manager(&self, operation: &str, args: &[&str]) -> CallRecord;

    /// Run `sandbox-cli runtime --sandbox-id <id> <operation> <args...>`.
    pub fn runtime(&self, sandbox_id: &str, operation: &str, args: &[&str]) -> CallRecord;
}

impl CallRecord {
    /// The parsed response is the bare result object (success) or `{ error: {..} }`
    /// (failure). On exit 0 the line came from stdout; on exit 1/2 it came from
    /// stderr. `response_json` is parsed from whichever stream carried the line.
    pub fn response(&self) -> &Value;
}
```

**Behavior contract:**

- Builds `argv` as the wrapper expects: global `--gateway-socket {path}` then the
  `manager`/`runtime` subcommand. The runtime subcommand's `--sandbox-id` selects
  Sandbox scope (`output.rs:53-55`, scope fixed at `request_builder.rs:74-79`).
- Spawns `sandbox-cli` via `std::process::Command`, captures stdout, stderr, and
  `ExitStatus`; times wall-clock around the spawn for `latency_ms`.
- Exit-code routing mirrors the CLI exactly:
  `0` = clean response on **stdout** (`output.rs:266-272`); `1` = response with a
  top-level `error` on **stderr** (`output.rs:266-268`) **or** a
  connection/protocol error rendered to stderr (`output.rs:248-251`); `2` =
  build/usage error on **stderr** (`output.rs:94-96,140-150,288-292`).
- Parses `response_json` with `serde_json::from_slice::<Value>` on the single
  newline-terminated line, reading **stdout when exit==0, else stderr** — the
  parsed `error` may arrive on either stream, so the record keeps both
  (`output.rs:266-272`; client.rs response framing `client.rs:69-95`).

**Design Question 1 — `sandbox-cli` discovery (single confirmed path):**

Phase 1 resolves the CLI **one way only**: the literal `sandbox-cli` on `PATH`.
Per `CLAUDE.md` (`export PATH="$PWD/bin:$PATH"`), that is the repo-local wrapper
`bin/sandbox-cli`, whose body is
`exec cargo run --quiet --manifest-path "$repo_root/Cargo.toml" -p sandbox-gateway --bin sandbox-cli -- "$@"`
(confirmed by reading `bin/sandbox-cli`). The wrapper cargo-runs the
`sandbox-cli` binary defined in crate `sandbox-gateway`. This is the only
discovery mechanism with live-code backing, so it is the only one Phase 1 uses
(prefer-less). Any explicit-path override (a manifest `cli_path` field or an
escape-hatch env var for CI that pre-builds the binary) is a **Phase 3**
orchestrator concern — net-new convention with no current backing — and is **not
introduced here**.

`CARGO_BIN_EXE_sandbox-cli` is **not** usable: that env var is injected only for a
crate's *own* integration tests, and `sandbox-cli` is defined in
`sandbox-gateway`, a different crate — so it is not present in this crate's test
process. Discovery is therefore PATH-based on the wrapper.

**Phase 1 verification requires `bin/` on `PATH`:** run with
`export PATH="$PWD/bin:$PATH"` (CLAUDE.md) so `sandbox-cli` resolves to the
wrapper. The wrapper builds/runs the gateway's `sandbox-cli` binary on first call.

### `src/fixtures.rs`

**SRP:** own the lazy `Harness` singleton (env → manifest → `CliClient`) and the
RAII `Sandbox` whose drop destroys exactly the sandbox it created.

```rust
use std::path::PathBuf;
use crate::cli_client::{CallRecord, CliClient};
use crate::config::RunConfig;

pub struct Harness {
    cli: CliClient,
    run_root: PathBuf,
    run_id: String,
    image: String,
}

impl Harness {
    /// Lazy singleton. Reads EOS_E2E_RUN_ROOT -> run-manifest.json once. Returns
    /// `None` when EOS_E2E_RUN_ROOT is unset (skip signal for every leaf); panics
    /// only when the env is set but the manifest is missing/invalid (a real
    /// misconfiguration the operator must fix), never on the unset path.
    pub fn get() -> Option<&'static Harness>;

    pub fn cli(&self) -> &CliClient;

    /// Provision via the public manager CLI — the same path as the system under
    /// test. Creates `{run_root}/work/{run_id}-{slug}` as an absolute dir, then
    /// `sandbox-cli manager create_sandbox --image {image} --workspace-root {ws}`.
    /// The sandbox id is read from the create response `/id` (runtime-assigned,
    /// round-tripped); the id is never predicted or supplied. Returns the RAII
    /// `Sandbox` **and** the create `CallRecord` so a leaf can assert on the one
    /// creation it made — no second `create_sandbox` is ever issued.
    pub fn provision_sandbox(&self, slug: &str, image: Option<&str>) -> (Sandbox, CallRecord);
}

/// RAII sandbox handle. On drop, issues
/// `sandbox-cli manager destroy_sandbox --sandbox-id {id}` (idempotent), making
/// teardown panic-safe even when an assertion fails.
pub struct Sandbox {
    pub id: String,
    pub workspace_root: PathBuf,
}

impl Drop for Sandbox {
    fn drop(&mut self); // sandbox-cli manager destroy_sandbox --sandbox-id self.id
}
```

**Behavior contract:**

- `Harness::get` is a `OnceLock<Option<Harness>>`. First call runs
  `RunConfig::from_env()`: `Ok(None)` → cache `None` (skip); `Ok(Some(cfg))` →
  resolve `CliClient` (DQ 1) against `cfg.gateway_socket`, then call
  `gateway::await_ready(&cfg.gateway_socket)` once before returning `Some`;
  `Err(_)` → panic with the manifest error (operator misconfig, not a skip).
- `provision_sandbox`: `image` defaults to the manifest image when `None`. The
  workspace root is `{run_root}/work/{run_id}-{slug}` created with
  `std::fs::create_dir_all` and canonicalized to an **absolute** path (DQ 7).
  It issues **exactly one** `create_sandbox`, asserts success (`assertion::ok`),
  reads `id := response["/id"]` as a `String` (the captured id matches
  `[A-Za-z0-9._-]` by construction, `model.rs:15-20`), and returns
  `(Sandbox, CallRecord)` so the caller asserts on that one creation rather than
  issuing a second `create_sandbox`. The returned `Sandbox` is the sole RAII
  guard; its drop reaps the one sandbox created here.

### `src/gateway.rs`

**SRP:** attach-only readiness — confirm the externally supplied
`--gateway-socket` path exists and is connectable before any test runs. **No
spawn** (v1 is attach-only; the shipped binary wires Unconfigured stubs,
`gateway/main.rs:94-146`).

```rust
use std::path::Path;
use std::time::Duration;

const READY_TIMEOUT: Duration = Duration::from_secs(5);

/// Poll until the gateway socket exists and accepts a connection, or the fixed
/// timeout elapses. Attach mode only — never spawns a gateway. Returns Err with
/// a clear message naming the missing socket if it never becomes ready.
pub fn await_ready(socket: &Path) -> anyhow::Result<()>;
```

**Behavior contract:** polls `socket` existence + a probe `UnixStream::connect`
(the client connects per call, `client.rs:31`) on a short interval up to
`READY_TIMEOUT`. It does **not** issue the runtime-configured preflight probe
(that is Phase 3 preflight); it only confirms the socket is live. The
real-runtime gate is honest: a socket from the shipped binary connects fine but
fails `create_sandbox` with `"sandbox runtime is not configured"`
(`gateway/main.rs:110-112`) — readiness ≠ a working runtime.

### `src/assertion.rs`

**SRP:** absorb response-shape checks so leaves never hand-walk JSON. Phase 1
ships only the helpers the two leaves use.

```rust
use serde_json::Value;
use crate::cli_client::CallRecord;

/// Assert there is no top-level `error` key (success discriminator,
/// `output.rs:266-272`; error shape `response.rs:41-48`).
pub fn ok(resp: &Value);

/// JSON-pointer get-or-panic. `field(resp, "/status")`, `field(resp, "/id")`, etc.
pub fn field<'a>(resp: &'a Value, ptr: &str) -> &'a Value;
```

Phase 1 leaves (M1, R1) are both success paths, so the negative/exit helper
(`err_kind_at`) is **not** needed yet and is deferred to Phase 2 with N1/N2.
`err_detail`, `non_decreasing`, `offsets_monotonic` are likewise Phase 2.

### `tests/support/mod.rs`

**SRP:** the single skip-safe entry every leaf calls; re-surface the harness.

```rust
#![allow(dead_code)]

pub use sandbox_e2e_live_test::assertion;
pub use sandbox_e2e_live_test::fixtures::{Harness, Sandbox};

/// Skip-safe handle. `None` => the test must early-return (run outside eos-e2e).
pub fn harness() -> Option<&'static Harness> {
    Harness::get()
}
```

Follows the support-module convention at
`crates/sandbox-runtime/operation/tests/support/mod.rs:1` (leading
`#![allow(dead_code)]` then shared helpers).

**Design Question 4 — skip-vs-panic mechanism:** `Harness::get()` returns
`Option<&'static Harness>`; `harness()` forwards it. Each leaf opens with
`let Some(h) = support::harness() else { return; };`. When `EOS_E2E_RUN_ROOT` is
unset, `RunConfig::from_env()` returns `Ok(None)`, `get()` caches `None`, and
**every leaf early-returns as a passing no-op** — a bare `cargo test` on a
non-E2E machine does not fail. The panic path is reserved for a *set-but-broken*
manifest (operator misconfig), never for the unset case.

**Explicit deviation from the parent DQ4 — "records a skipped result" is
deferred.** The parent design has a skipped test write a `result.json` with
status `"skipped"`. Phase 0–1 **intentionally overrides this**: a skip is a
**silent early return that writes nothing**, because all artifact writing —
including `result.json` — is Phase 3 (see the *Prefer-less ledger*). This still
satisfies the prompt's true intent (a bare `cargo test` must not fail on a
non-E2E machine); the only thing dropped is the recorded `"skipped"` row, which
arrives with the orchestrator's artifact layer in Phase 3.

### `src/build.rs` (Phase 1 contract)

**SRP:** turn the leaf-file tree into per-scope `#[path]`-include lists so adding
an operation is adding one file, with no hand-maintained registry.

**Design Question 5 — include generation:**

- **Walk.** For each scope in `{manager, runtime}`, recursively walk
  `tests/<scope>/**/*.rs`, collecting every leaf file. Skip `tests/support/`
  (it is `#[path]`-included directly by the root binaries) and skip the root
  `tests/<scope>.rs` files themselves.
- **Module slug (collision-free `<family>_<operation>`).** A leaf lives at
  `tests/<scope>/<family>/<operation>.rs`; the slug is
  `<family>_<operation>` (e.g. `tests/manager/lifecycle/create_sandbox.rs` →
  `lifecycle_create_sandbox`; `tests/runtime/command/exec_command.rs` →
  `command_exec_command`). Because the on-disk path
  `<scope>/<family>/<operation>` is unique per file, the derived slug is unique
  within a scope; two authors adding different operations cannot collide. A
  deeper nesting (`<scope>/<family>/<sub>/<operation>.rs`) joins all
  path components after `<scope>` with `_`, preserving uniqueness.
- **Output.** Write `$OUT_DIR/<scope>_mods.rs`, one line per leaf:
  `#[path = "<absolute path to leaf>"] mod <slug>;`. Using `OUT_DIR` matches the
  generated-include idiom; the repo's hand-written includes use
  `concat!(env!("CARGO_MANIFEST_DIR"), ...)` (`unit.rs:32-55`), and the root
  binaries here use `concat!(env!("OUT_DIR"), ...)` because the list is generated
  at build time. Both are the same `#[path]` + `include!` convention; the only
  difference is the env var naming the directory.
- **`rerun-if-changed` triggers.** Emit
  `cargo:rerun-if-changed=tests/manager` and
  `cargo:rerun-if-changed=tests/runtime` (directory-level) **and**
  `cargo:rerun-if-changed=<leaf>` for each discovered leaf, so adding/removing a
  leaf or editing one regenerates the list.
- **Empty-tree builds.** If a scope directory is absent or contains no `.rs`
  leaves (the Phase 0 state), the generated `$OUT_DIR/<scope>_mods.rs` is an
  **empty file**; `include!` of an empty file compiles, and the root binary is an
  empty test binary. Verified by the Phase 0 acceptance build.

```rust
// build.rs Phase 1 contract (signatures, no bodies)
fn main();
// for scope in ["manager","runtime"]: generate_scope_includes(manifest_dir, out_dir, scope)
fn generate_scope_includes(manifest_dir: &std::path::Path, out_dir: &std::path::Path, scope: &str);
fn module_slug(scope_relative_path: &std::path::Path) -> String; // family_operation
```

---

## `run-manifest.json` Schema

Phase 1 hand-writes this file; Phase 3's orchestrator later produces a conforming
one. Phase 1 reads exactly four fields. Each is load-bearing:

| Field | Type | Phase 1 use (load-bearing) | Evidence |
|-------|------|----------------------------|----------|
| `schema_version` | integer | Version gate so the loader can reject an incompatible manifest; Phase 1 accepts `1`. | parent artifact convention ("each carrying `schema_version`") |
| `gateway_socket` | string (abs path) | Passed as `sandbox-cli --gateway-socket`; attach-mode readiness target. | `output.rs:28-29` |
| `run_id` | string | Read verbatim; prefixes per-test workspace dirs under `run_root`. Phase 1 does not validate or derive it. | parent Config Schema |
| `image` | string | Default `--image` for `create_sandbox`. | `create_sandbox.rs:18-26` |

`run_root` is **not** a manifest field — it is `EOS_E2E_RUN_ROOT` itself (the
manifest lives at `{run_root}/run-manifest.json`). Fields the parent lists for
later phases (`git HEAD`, full `config`, `clock`) are **not read in Phase 1** and
are ignored if present; Phase 1 neither requires nor writes them.

Concrete Phase 1 verification file (`{run_root}/run-manifest.json`):

```json
{
  "schema_version": 1,
  "gateway_socket": "/tmp/eos-real-runtime-gateway.sock",
  "run_id": "p1-verify",
  "image": "ubuntu:24.04"
}
```

---

## The Two Leaf Tests

Both are success-path leaves. They open with the skip guard, provision via the
fixture, drive one operation, and assert typed JSON fields read through
`assertion::field`. The RAII `Sandbox` reaps on drop.

### `tests/manager/lifecycle/create_sandbox.rs` — matrix **M1**

```rust
// build.rs slug => `lifecycle_create_sandbox`, mounted by tests/manager.rs.
#[test]
fn create_sandbox_returns_ready_with_daemon_socket() {
    let Some(h) = support::harness() else { return };   // skip when not under eos-e2e

    // provision_sandbox issues exactly one: manager create_sandbox --image {image}
    //   --workspace-root {run_root}/work/{run_id}-lifecycle-create_sandbox-case1
    // and returns the RAII Sandbox plus the create CallRecord. The id is read
    // from that response /id (runtime-assigned, round-tripped). No second create.
    let (_sb, rec) = h.provision_sandbox("lifecycle-create_sandbox-case1", None);

    // _sb is the sole RAII guard; it drops at scope end -> destroy_sandbox.
    // Assert the full M1 contract on the single creation's record.
    let resp = rec.response();
    assert::ok(resp);                                                  // no top-level "error"
    assert!(assert::field(resp, "/id").as_str().unwrap().chars()      // /id non-empty
        .all(|c| c.is_ascii_alphanumeric() || matches!(c, '-' | '_' | '.')));
    assert_eq!(assert::field(resp, "/state"), "ready");               // SandboxState::Ready
    assert!(!assert::field(resp, "/daemon/socket_path").is_null());   // daemon endpoint present
    // _sb drops here -> manager destroy_sandbox --sandbox-id (the one created)
}
```

> The M1 leaf asserts on the `CallRecord` returned by `provision_sandbox`, the
> record of the single `create_sandbox` it issued — it never re-issues a second
> `create_sandbox` (which would leak an un-RAII-tracked container/workspace on
> every green run). The asserted fields are: `/id` non-empty and charset-valid
> (`model.rs:15-20`), `/state == "ready"` (`model.rs:70`, `mod.rs:88-95`),
> `/daemon/socket_path` non-null (`mod.rs:88-95,97-100`), no top-level `error`
> (`output.rs:266-272`).

### `tests/runtime/command/exec_command.rs` — matrix **R1**

```rust
// build.rs slug => `command_exec_command`, mounted by tests/runtime.rs.
#[test]
fn one_shot_exec_returns_ok_and_zero_exit() {
    let Some(h) = support::harness() else { return };   // skip when not under eos-e2e
    let (sb, _create) = h.provision_sandbox("command-exec_command-case1", None); // one create_sandbox; id from /id

    // sandbox-cli runtime --sandbox-id {sb.id} exec_command pwd
    let rec = h.cli().runtime(&sb.id, "exec_command", &["pwd"]);
    let resp = rec.response();
    assert::ok(resp);                                          // success: no top-level "error"
    assert_eq!(assert::field(resp, "/status"), "ok");          // CommandStatus::Ok => "ok"
    assert_eq!(assert::field(resp, "/exit_code"), 0);          // terminal one-shot, zero exit
    assert!(resp.get("command_session_id").is_none());         // terminal => field absent
    // sb drops here -> manager destroy_sandbox --sandbox-id sb.id
}
```

Asserted-field evidence: success discriminator is absence of top-level `error`
(`output.rs:266-272`); `/status` is `status_name(...)` →
`CommandStatus::Ok => "ok"` (`command_operations.rs:326,357-359`;
`contract.rs:43-44`); `/exit_code` is `output.exit_code`
(`command_operations.rs:327`); `command_session_id` is emitted **iff** the
command is still running (`command_operations.rs:336-337`), so a terminal
one-shot omits it.

---

## Verification & Acceptance

Run all commands from the repo root with the repo-local tools on `PATH`:

```sh
export PATH="$PWD/bin:$PATH"   # CLAUDE.md — makes `sandbox-cli` resolve to bin/sandbox-cli
```

### Phase 0 — build + clippy

```sh
cargo build -p sandbox-e2e-live-test         # crate compiles (empty test tree, empty includes)
cargo build                                  # workspace-wide build still succeeds
cargo clippy -p sandbox-e2e-live-test --all-targets   # passes under workspace lints
```

Pass criteria: all three exit 0. The empty-tree `build.rs` emits empty
`$OUT_DIR/{manager,runtime}_mods.rs`; the two root binaries compile empty.

### Phase 1 — skip-clean test (no E2E machine, no gateway)

```sh
cargo test -p sandbox-e2e-live-test          # EOS_E2E_RUN_ROOT unset
```

Pass criteria: exit 0; both leaves early-return (skip) without panicking; nothing
written. This is the buildable-and-skip-safe-without-the-gateway guarantee.

### Phase 1 — green against a real-runtime gateway (recipe)

Requires a Linux host with Docker, the `ubuntu:24.04` image present, and an
**externally started gateway wired with the real Docker runtime**, attached via
its socket path. Hand-write the manifest, then run:

```sh
RUN_ROOT=$(mktemp -d)
cat > "$RUN_ROOT/run-manifest.json" <<'JSON'
{
  "schema_version": 1,
  "gateway_socket": "/tmp/eos-real-runtime-gateway.sock",
  "run_id": "p1-verify",
  "image": "ubuntu:24.04"
}
JSON

EOS_E2E_RUN_ROOT="$RUN_ROOT" \
  cargo test -p sandbox-e2e-live-test -- --test-threads=1
```

Pass criteria: both leaves run green under `--test-threads=1`.

**Honest gate (Open Items #1).** The green live run is **blocked on an unshipped
prerequisite**: the shipped `sandbox-gateway` binary wires `UnconfiguredRuntime`
/ `UnconfiguredDaemonInstaller` stubs (`default_manager_services` at
`gateway/main.rs:94`), and `UnconfiguredRuntime::create_sandbox`
(`gateway/main.rs:106`) returns
`RuntimeFailed { message: "sandbox runtime is not configured" }`
(`gateway/main.rs:110-112`). A gateway started from the shipped binary therefore
fails every `create_sandbox`, so `provision_sandbox` cannot succeed and the live
run cannot go green until a gateway wired with a real `SandboxRuntime` +
`SandboxDaemonInstaller` is started externally and attached via
`--gateway-socket`. That gateway additionally needs three inputs this crate does
not provide: a `sandbox-daemon` executable (built by
`cargo run -p xtask -- package`, `xtask/src/main.rs:764`), a daemon config YAML,
and a runtime-root. Phases 0–1 are fully buildable and skip-safe today; the live
suite is **non-executable** until that gateway ships.

---

## Anchor Ledger

Every `file:line` the spec relies on. All re-opened and confirmed in this run.

| Anchor | Verdict | Confirmed fact |
|--------|---------|----------------|
| `Cargo.toml:2` | confirmed | `resolver = "2"`. |
| `Cargo.toml:3-17` | confirmed | `members` array; last entry `"xtask",` on line 16, closing `]` on line 17. New member inserted after line 16. |
| `Cargo.toml:20-23` | confirmed | `[workspace.package]`: version 0.1.0 (:20), edition 2021 (:21), rust-version 1.85 (:22), license MIT (:23). |
| `Cargo.toml:27` | confirmed | `serde = { version = "1", features = ["derive"] }`. |
| `Cargo.toml:28` | confirmed | `serde_json = "1"`. |
| `Cargo.toml:34` | confirmed | `sha2 = "0.10"` (omitted from Phase 0–1 deps). |
| `Cargo.toml:40` | confirmed | `thiserror = "2"` (omitted). |
| `Cargo.toml:41` | confirmed | `time = "0.3"` (omitted). |
| `Cargo.toml:42` | confirmed | `uuid` v4-only (omitted). |
| `Cargo.toml:43` | confirmed | `tokio` "full" (omitted). |
| `Cargo.toml:44` | confirmed | `tokio-util` features `["rt"]` (omitted). |
| `Cargo.toml:48` | confirmed | `anyhow = "1"`. |
| `Cargo.toml:49` | confirmed | `clap` v4 derive (omitted from Phase 0–1 deps; orchestrator arg parsing is Phase 3). |
| `Cargo.toml:52` | confirmed | `futures-util = "0.3"` (omitted). |
| `Cargo.toml:66` | confirmed | `sandbox-protocol` path dep (optional; not added). |
| `Cargo.toml:70-83` | confirmed | `[workspace.lints]` groups + `unwrap_used="warn"` (:78), `dbg_macro="warn"` (:79), `undocumented_unsafe_blocks="deny"` (:80). |
| `crates/sandbox-daemon/tests/unit.rs:3-4` | confirmed | `#[path = "../src/observability/mod.rs"] pub(crate) mod observability;` — `#[path]` module-composition convention. |
| `crates/sandbox-daemon/tests/unit.rs:32-55` | confirmed | `include!(concat!(env!("CARGO_MANIFEST_DIR"), "/tests/unit/...rs"))` — generated-style include uses `env!(...)`; the mechanism is `CARGO_MANIFEST_DIR` here (build.rs-generated lists use `OUT_DIR`). |
| `crates/sandbox-runtime/operation/tests/support/mod.rs:1` | confirmed | begins `#![allow(dead_code)]` then shared helpers — support-module convention. |
| `crates/sandbox-gateway/src/cli/client.rs:31` | confirmed | `UnixStream::connect` per call (client connects per call). |
| `crates/sandbox-gateway/src/cli/client.rs:36` | confirmed | request written only to the socket (`write_all(&json_line(&request_value))`); never echoed to stdio → `request_json` is `None` on the black-box path. |
| `crates/sandbox-gateway/src/cli/client.rs:69-95` | confirmed | reads exactly one newline-terminated JSON line, returns `serde_json::Value`. |
| `crates/sandbox-gateway/src/cli/output.rs:21-23` | confirmed | `EXIT_SUCCESS=0`, `EXIT_FAILURE=1`, `EXIT_USAGE=2`. |
| `crates/sandbox-gateway/src/cli/output.rs:28-29` | confirmed | global `--gateway-socket` flag (`gateway_socket_path: Option<PathBuf>`). |
| `crates/sandbox-gateway/src/cli/output.rs:53-55` | confirmed | runtime subcommand `--sandbox-id` (selects Sandbox scope). |
| `crates/sandbox-gateway/src/cli/output.rs:94-96` | confirmed | clap parse error → stderr + exit `2`. |
| `crates/sandbox-gateway/src/cli/output.rs:140-150` | confirmed | runtime sandbox-id resolution error → stderr + exit `2`. |
| `crates/sandbox-gateway/src/cli/output.rs:248-251` | confirmed | connection/protocol error → stderr + exit `1`. |
| `crates/sandbox-gateway/src/cli/output.rs:266-272` | confirmed | error-key discriminator: `error` present → stderr + exit 1; clean → stdout + exit 0. |
| `crates/sandbox-gateway/src/cli/output.rs:287-292` | confirmed | request-build error rendered as `invalid_request` to stderr (caller exit 2). |
| `crates/sandbox-gateway/src/cli/request_builder.rs:74-79` | confirmed | scope fixed from execution space (Manager→System, Runtime→Sandbox); CLI cannot force a manager op into Sandbox scope. |
| `crates/sandbox-gateway/src/cli/request_builder.rs:84-98` | confirmed | `resolve_runtime_sandbox_id`: `--sandbox-id` else `default_sandbox_id`, else error "runtime operations require --sandbox-id or SANDBOX_DEFAULT_ID"; rejects empty. |
| `crates/sandbox-manager/src/operation/impls/management/create_sandbox.rs:18-26` | confirmed | `--image` required, `ArgKind::String`. |
| `crates/sandbox-manager/src/operation/impls/management/create_sandbox.rs:27-35` | confirmed | `--workspace-root` required, `ArgKind::Path`. |
| `crates/sandbox-manager/src/operation/impls/management/create_sandbox.rs:62-87` | confirmed | runtime assigns id (`created.id`, :64); success returns `Response::ok(record_value(record))` (:87). |
| `crates/sandbox-manager/src/operation/impls/management/mod.rs:63-72` | confirmed | `workspace_root` absolute-path check (`!path.is_absolute()` → `InvalidWorkspaceRoot`, :68-70). |
| `crates/sandbox-manager/src/operation/impls/management/mod.rs:88-95` | confirmed | `record_value` shape: `id`, `workspace_root`, `state`, `daemon`. |
| `crates/sandbox-manager/src/operation/impls/management/mod.rs:97-100` | confirmed | `endpoint_value`: `{ socket_path }`. |
| `crates/sandbox-manager/src/operation/impls/management/destroy_sandbox.rs:16-24` | confirmed | `destroy_sandbox` takes `--sandbox-id` required. |
| `crates/sandbox-manager/src/operation/impls/management/destroy_sandbox.rs:77` | confirmed | success returns `record_value(record)`. |
| `crates/sandbox-runtime/operation/src/cli_definition/command_operations.rs:24-32` | confirmed | `exec_command` spec. |
| `crates/sandbox-runtime/operation/src/cli_definition/command_operations.rs:45-53` | confirmed | `exec_command` positional `cmd` (`COMMAND`) required. |
| `crates/sandbox-runtime/operation/src/cli_definition/command_operations.rs:35-44` | confirmed | optional `--workspace-session-id`. |
| `crates/sandbox-runtime/operation/src/cli_definition/command_operations.rs:278-281` | confirmed | exec yields `Response::running` iff status Running, else `Response::ok`. |
| `crates/sandbox-runtime/operation/src/cli_definition/command_operations.rs:324-340` | confirmed | `command_yield_value`: `status` (:326), `exit_code` (:327), `command_session_id` set iff `Some` (:336-337). |
| `crates/sandbox-runtime/operation/src/cli_definition/command_operations.rs:357-359` | confirmed | `status_name` delegates to `CommandStatus::as_str`. |
| `crates/sandbox-runtime/operation/src/command/service/contract.rs:31-47` | confirmed | `CommandStatus` enum; `Ok => "ok"` (:44), `Running => "running"` (:43). |
| `crates/sandbox-protocol/src/response.rs:10-17` | confirmed | `Response::ok` / `Response::running` carry the bare result `Value` (no wrapper). |
| `crates/sandbox-protocol/src/response.rs:41-48` | confirmed | error shape `{ error: { kind, message, details } }`. |
| `crates/sandbox-manager/src/model.rs:12-13` | confirmed | sandbox id rejects empty/whitespace. |
| `crates/sandbox-manager/src/model.rs:15-20` | confirmed | sandbox id charset `[A-Za-z0-9._-]` (ascii_alphanumeric or `- _ .`). |
| `crates/sandbox-manager/src/model.rs:70` | confirmed | `SandboxState::Ready` serializes as `"ready"`. |
| `crates/sandbox-manager/src/model.rs:36-42` | confirmed | `SandboxRecord` fields: id, workspace_root, state, `daemon: Option<SandboxDaemonEndpoint>`. |
| `crates/sandbox-gateway/src/gateway/main.rs:94` | confirmed | `default_manager_services` wires `Arc::new(UnconfiguredRuntime)` (:97) + `Arc::new(UnconfiguredDaemonInstaller)` (:98). |
| `crates/sandbox-gateway/src/gateway/main.rs:106` | confirmed | `UnconfiguredRuntime::create_sandbox` signature. |
| `crates/sandbox-gateway/src/gateway/main.rs:110-112` | confirmed | returns `RuntimeFailed { message: "sandbox runtime is not configured" }`. |
| `xtask/src/main.rs:764` | confirmed | `fn package(args: &PackageArgs)` — builds the `sandbox-daemon` artifact (gateway prerequisite). |
| `crates/sandbox-config/src/configs/cli.rs:6` | confirmed | `SANDBOX_GATEWAY_SOCKET_ENV = "SANDBOX_GATEWAY_SOCKET"`. |
| `crates/sandbox-config/src/configs/cli.rs:7` | confirmed | `SANDBOX_DEFAULT_ID_ENV = "SANDBOX_DEFAULT_ID"`. |
| `crates/sandbox-config/src/configs/cli.rs:8` | confirmed | `DEFAULT_GATEWAY_SOCKET = "/tmp/eos-gateway.sock"`. |
| `bin/sandbox-cli` | confirmed | wrapper body `exec cargo run --quiet --manifest-path "$repo_root/Cargo.toml" -p sandbox-gateway --bin sandbox-cli -- "$@"`. |
| `crates/sandbox-e2e-live-test/` | confirmed | directory does **not** exist (Phase 0 creates it). |

---

## Conventions Checklist

- **SRP / one job per module.** Each `src/` module owns exactly one job:
  `config` = manifest load; `cli_client` = invoke + capture + parse; `fixtures` =
  Harness + RAII Sandbox; `gateway` = attach-mode readiness; `assertion` =
  response-shape checks; `build.rs` = include generation. No module spans two
  responsibilities.
- **No inline comments in production code.** `src/` carries doc comments
  (`///` / `//!`) on public items only; the leaf tests use intent comments
  (allowed in tests, per CLAUDE.md). The example bodies above show test-only
  comments; production stubs ship without inline comments.
- **Workspace deps via `dep.workspace = true`.** Every external dep
  (`anyhow`, `serde`, `serde_json`) is consumed via workspace inheritance
  with the line numbers cited above; no versions are pinned in the member crate.
- **`#[path]` / `include!` convention.** Root binaries use
  `#[path = "support/mod.rs"] mod support;` + `include!(concat!(env!("OUT_DIR"),
  "/<scope>_mods.rs"))`, matching `crates/sandbox-daemon/tests/unit.rs:3-4,32-55`
  (the in-tree variant uses `CARGO_MANIFEST_DIR`; the generated variant uses
  `OUT_DIR`). The generated list emits one `#[path = "..."] mod <slug>;` per leaf.
- **Clippy lints.** No `.unwrap()` in production `src/` (workspace
  `unwrap_used="warn"`, `Cargo.toml:78`); no `dbg!` (`dbg_macro="warn"`,
  `Cargo.toml:79`); no `unsafe` is introduced, so `undocumented_unsafe_blocks`
  (`Cargo.toml:80`) is moot. `cargo clippy -p sandbox-e2e-live-test --all-targets`
  is a Phase 0 acceptance gate.

---

## Prefer-less Ledger (what Phases 0–1 deliberately omit)

| Parent names it | Phase used | Excluded from 0–1 because |
|-----------------|------------|---------------------------|
| `report.rs`, `cleanup.rs` modules | 3 | No artifact writing or cleanup orchestration in 0–1; RAII `Sandbox` drop is the only teardown. |
| `summary.json`, `result.json`, `exchange.jsonl`, `observability.json` | 3/4 | No artifacts written; skip is a silent early-return. |
| `snapshot_observability`, P1, P2 | 4 | Observability monitoring is Phase 4. |
| `RunConfig.{max_parallel, tests, build, cli_timeout, gateway_ready_timeout, cleanup}` | 3 | Phase 1 needs only manifest-sourced `run_root/gateway_socket/run_id/image`. |
| `BuildSource`, `CleanupPolicy`, `TestSelection` enums | 3 | Orchestrator surface only. |
| `assertion::{err_kind_at, err_detail, non_decreasing, offsets_monotonic}` | 2 | The two Phase-1 leaves are success-only; only `ok`/`field` used. |
| `eos-e2e` preflight/build/attach/aggregate/cleanup | 3 | `eos-e2e` is a print-and-exit stub in 0–1; the run env is set by hand. |
| `clap`, `uuid`, `time`, `sha2`, `tokio`, `tokio-util`, `thiserror`, `futures-util` deps | 3/4 | No dependent module exists yet (see Cargo.toml deps table); the `eos-e2e` stub parses nothing, so `clap` would be unused. |
| Spawn-mode gateway; Docker run-label cleanup backstop | deferred | Open Items #1/#2 (unshipped runtime). |
