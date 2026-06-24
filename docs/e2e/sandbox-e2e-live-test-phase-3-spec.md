# `sandbox-e2e-live-test` — Phase 3 Spec (Orchestrator, Reproducibility, Artifacts, Cleanup)

Implementation-ready spec for **Phase 3** of `crates/sandbox-e2e-live-test`. This
is the **author** spec in an author→verifier discipline: build to green from
this document without re-deriving any design decision. It is **spec, not code** —
do **not** implement the crate from this file. Live code is the source of truth;
every `file:line` it relies on is in the *Anchor Ledger* (§10) with a
`confirmed`/`corrected` verdict.

Parent design: `docs/e2e/sandbox-e2e-live-test-spec.md` (`## Runner Architecture`,
`## Config Schema`, `## Reproducibility, Artifacts, and Cleanup`, `## Preflight`,
`### Two-stage delivery during the runtime migration`). Phase map:
`docs/e2e/sandbox-e2e-live-test-phases-note.md`.

---

## 1. Phase boundary + single-stage statement

Phase 3 turns the live skeleton (a harness library plus per-operation manager
tests that each provision a sandbox over `sandbox-cli`, write `exchange.jsonl`,
and self-destroy) into a **single operator/CI command, `eos-e2e`**. Over the
skeleton it adds: (a) the real orchestrator binary (preflight → manifest → build
→ run → aggregate → cleanup → exit code), replacing the stub at
`src/bin/eos-e2e.rs:1-9`; (b) the orchestrator-side `RunConfig` + clap `Args`
(the live test-side reader is renamed `ManifestConfig`); (c) the artifact writers
`result.json` (per test), `summary.json` (run rollup with `timing` + `cleanup`
sub-objects), and `run-manifest.json` (orchestrator-emitted, read by the live
`ManifestConfig`); (d) a thread-local assertion counter feeding `result.json`;
(e) an RAII `RunGuard` in a new `src/cleanup.rs` performing survivor-id sweep,
gateway detach, and policy-gated `run_root` removal; (f) `preflight`,
`--clean-run`, and `--rerun-failed-from` subcommands/flags.

**Single-stage statement.** Phase 3 is delivered **wholly in Stage 1**. It drives
**zero runtime ops**: the orchestrator's default `cargo test` target is pinned to
the manager binary only, behind **one named constant** (`STAGE1_DEFAULT_TARGET`,
§3 Phase B, §9, §10). That constant is the **sole Stage 2 touchpoint** — Stage 2
flips it to the full suite (drop `--test manager`) and changes nothing else in
this crate. Phase 3 introduces **no runtime-readiness skip guard**: the only skip
path stays `EOS_E2E_RUN_ROOT` unset (`tests/support/mod.rs:7-9`). Stage 1 is
provably green with the manager binary alone (M1–M5, N1).

**Out of scope (named, not designed here).**

| Out-of-scope | Where it lives |
|---|---|
| Phase 4 entirely: `get_observability_tree` polling, `observability.json`, P1/P2. Phase 3 writes **no observability artifact**. | Phase 4 |
| Stage 2: runtime leaves R2–R8/N2, runtime assertion helpers (`err_detail`/`offsets_monotonic`/`non_decreasing`), flipping `STAGE1_DEFAULT_TARGET`. | Stage 2 |
| Spawn-mode gateway + `package-fast` binary discovery. Phase 3 is **attach-only**; Phase A is skipped whenever `--gateway-socket` is given. | Open Items #1 |
| Docker run-label orphan reaper. No label exists (`runtime.rs:5-14`), so SIGKILL/abort mid-run can leak containers — stated honestly in §7. | Open Items #2 |
| `build.rs` changes. Phase 3 adds no test leaf; the generated include list and slug derivation are unchanged (§12). | n/a |

---

## 2. Resulting file/folder structure

`△` = edited this phase, `←NEW` = created this phase. The `tests/` tree is
**unchanged** by Phase 3 (no leaf added; `build.rs` untouched).

```text
crates/sandbox-e2e-live-test/
  Cargo.toml                       △  add clap, sha2, time (deps)
  build.rs                            unchanged (confirm: no edit, §12)
  src/
    lib.rs                         △  add `pub mod cleanup;`
    config.rs                      △  rename test-side RunConfig→ManifestConfig; add orchestrator RunConfig + clap Args + enums + run_id derivation
    report.rs                      △  keep write_exchange; add write_result / write_summary / write_run_manifest
    fixtures.rs                    △  ManifestConfig::from_env; Instant at provision; result.json in Sandbox::drop; test_name from thread name
    assertion.rs                   △  thread-local assertion counter bumped by ok/field/err_kind_at
    cli_client.rs                     unchanged
    gateway.rs                        unchanged (await_ready reused for Phase B readiness)
    cleanup.rs                     ←NEW  RunGuard (RAII): survivor-id sweep → gateway detach → policy-gated remove_dir_all
    bin/
      eos-e2e.rs                   △  REPLACE stub with full orchestrator + preflight / --clean-run / --rerun-failed-from
  tests/                              unchanged (support/mod.rs, manager.rs, runtime.rs, all leaves)
```

`Cargo.toml` dependency additions (consumed via `dep.workspace = true`;
workspace anchors in §10):

```toml
[dependencies]
anyhow.workspace = true       # already present
serde = { workspace = true }  # already present
serde_json.workspace = true   # already present
clap.workspace = true         # ←NEW  orchestrator CLI parsing (derive)
sha2.workspace = true         # ←NEW  deterministic run_id slug
time.workspace = true         # ←NEW  colon-free UTC run timestamp
```

`tokio`, `tokio-util`, `uuid` are **deliberately not added** (§ Async vs sync,
below; *prefer less*). The orchestrator is fully synchronous.

---

## 3. Orchestrator pipeline spec (`eos-e2e`)

`eos-e2e` is a **synchronous** binary. Default subcommand (no subcommand given) =
`run`. Subcommands: `run` (default), `preflight`, `--clean-run {run_id}`,
`--rerun-failed-from {summary.json}` (flag on `run`). It shells out via
`std::process::Command` to `docker`, `sandbox-cli`, and `cargo test`; globs
files under `{run_root}`; and reuses the synchronous `gateway::await_ready`
(`gateway.rs:12-26`). There is no fan-out — `cargo test` owns thread parallelism.

### 3.1 `run` pipeline (ordered phases)

| # | Phase | Inputs | Outputs / side effects | Exit-code semantics |
|---|---|---|---|---|
| 1 | **Preflight** | `RunConfig` (OS, `--gateway-socket`, `--image`, scratch dir) | none (probe is side-effect-free, §6) | any check fails → process exits **2** with the exact message (§6). All pass → continue. |
| 2 | **Manifest** | `RunConfig`, git HEAD, clock | creates `{run_root}` and `{run_root}/run-manifest.json` (§5.1); constructs `RunGuard` owning `run_root` (§7) | I/O failure (cannot create run_root / write manifest) → exit **2**. |
| 3 | **Phase A — Build** | `BuildSource` | builds `sandbox-gateway`/`sandbox-cli` (own `Instant`s → `timing.build`, §8). **Skipped** when `BuildSource::Prebuilt` **or** `--gateway-socket` given (attach-only ⇒ all `*_ms = 0`). | build command non-zero → exit **2** (cannot proceed to run). |
| 4 | **Phase B — Run** | `run-manifest.json`, `gateway_socket`, `STAGE1_DEFAULT_TARGET`, `TestSelection`, `max_parallel` | runner clock starts; `gateway::await_ready(socket)` (records `gateway_attach_ms`); exports **`EOS_E2E_RUN_ROOT={run_root}`** (the sole env contract); runs the `cargo test` child (records `test_process_ms`); captures the child's **exit code** | `await_ready` fails → exit **2**. cargo child cannot be spawned → run `status = error`, exit **2**. cargo child runs → its exit code is captured (the pass/fail gate); pipeline continues to Aggregate regardless. |
| 5 | **Aggregate** | glob `{run_root}/reports/*/result.json`; cargo-test exit code | writes `{run_root}/summary.json` (§5.3) | never the gate itself; computes `summary.status` (§5.3). |
| 6 | **Cleanup** | `RunGuard`, `CleanupPolicy`, computed run status | survivor-id sweep + gateway detach + policy-gated `remove_dir_all(run_root)` (§7); writes `summary.cleanup` shape into the summary | best-effort; cleanup errors are recorded in `summary.cleanup.errors`, do **not** change the run exit code. |
| 7 | **ExitCode** | cargo-test exit code AND every `result.json` status | process exit code | **0** iff the cargo-test child exited **0** AND every `result.json` status is `passed`; else **1** (run failed); **2** if the cargo invocation itself could not run. On non-success, print the exact focused-rerun line (§9). |

**Hard rule (boundary law):** the pass/fail gate is the **cargo-test process exit
code**, never libtest stdout. `summary.tests[]` is built **solely** by globbing
`result.json` (§5.3), not by parsing test output.

### 3.2 `preflight` subcommand

Runs checks 1–4 of §6 against `RunConfig` (Linux → docker reachable → image
present → real-runtime probe). Exit **0** if all pass, **2** on the first failure
with that check's exact message. No `run_root` is created; no manifest, no build,
no tests, no cleanup.

### 3.3 `--clean-run {run_id}` subcommand

Re-runs teardown for a prior run at `{run_root_base}/{run_id}` (run_root base =
the same default/`--run-root` resolution as `run`, §4). Reads that run's
`{run_root}/run-manifest.json` to recover its `gateway_socket`, then performs the
§7 teardown (survivor-id sweep keyed on `reports/*/`, gateway detach,
policy-gated `remove_dir_all`). Idempotent: missing run_root or already-destroyed
sandboxes are not errors. Exit **0** on completion, **2** if the run_id is
charset-invalid or its manifest is unreadable.

---

## 4. Config schema

### 4.1 `ManifestConfig` rename (test side)

The live test-side reader is **named `RunConfig`** today (`config.rs:14-19`,
`from_env` at `:29-55`, serde DTO `Manifest` at `:21-27`,
`SUPPORTED_SCHEMA_VERSION = 1` at `:8`). Phase 3 **renames the struct
`RunConfig` → `ManifestConfig`** and keeps everything else intact: the `Manifest`
DTO (no `#[serde(deny_unknown_fields)]`, so it ignores the orchestrator's
superset fields), `ManifestConfig::from_env() -> anyhow::Result<Option<…>>`
(returns `Ok(None)` when `EOS_E2E_RUN_ROOT` is unset = skip signal), and the
`schema_version != SUPPORTED_SCHEMA_VERSION` bail (`:43-48`).

| Symbol | File | Consumer this phase |
|---|---|---|
| `ManifestConfig` (renamed) + `from_env` | `config.rs` | `fixtures.rs:33` (`ManifestConfig::from_env`) — the **sole** call site; update the `use crate::config::RunConfig;` import at `fixtures.rs:7` to `ManifestConfig`. |
| `Manifest` (DTO, unchanged), `SUPPORTED_SCHEMA_VERSION` (unchanged) | `config.rs` | internal to `ManifestConfig::from_env` |
| `RunConfig` + `Args` + enums (NEW) | `config.rs` | `bin/eos-e2e.rs` only |

The test side (`fixtures.rs`, `tests/support/mod.rs`) must keep compiling
**unchanged except the rename**. `support/mod.rs:3-4` re-exports `assertion` and
`fixtures::Harness` only — it does **not** name `RunConfig`, so it needs no edit.

### 4.2 Orchestrator `RunConfig` + clap `Args`

`Args` is the clap-derived parse type; `RunConfig` is the validated, resolved
config the pipeline consumes. Resolution precedence is **flag > env > default**.

```rust
// src/config.rs (orchestrator side)
#[derive(clap::Parser)]
#[command(name = "eos-e2e")]
pub struct Args { /* fields below */ }

pub struct RunConfig {
    pub run_id: String,
    pub max_parallel: usize,
    pub tests: TestSelection,
    pub image: String,
    pub run_root: PathBuf,        // {run_root_base}/{run_id}
    pub gateway_socket: PathBuf,
    pub build: BuildSource,
    pub cli_timeout: Duration,
    pub gateway_ready_timeout: Duration,
    pub cleanup: CleanupPolicy,
    pub clock: String,            // colon-free UTC stamp, recorded in run-manifest
}
```

| Field | Flag | Env | Default | Validation |
|---|---|---|---|---|
| `run_id` | `--run-id` | — | derived (§4.4) | if supplied, must match `[A-Za-z0-9._-]` (SandboxId charset, `model.rs:10-22`); reject at parse otherwise (no `:`). |
| `max_parallel` | `--max-parallel` | `EOS_E2E_MAX_PARALLEL` | `available_parallelism().min(8)` | `≥ 1`; passed as `--test-threads=N`. |
| `tests` | (see §9: `--test-names`, `--rerun-failed-from`) | — | `TestSelection::All` | `RerunFailedFrom` path must parse a prior `summary.json`. |
| `image` | `--image` | — | `"ubuntu:24.04"` | non-empty. |
| `run_root` (base) | `--run-root` | `EOS_E2E_RUN_ROOT_BASE` | `${TMPDIR:-/tmp}/eos-e2e` | resolved `run_root = base/{run_id}`. **Distinct** from the cross-process `EOS_E2E_RUN_ROOT` (which the orchestrator *exports* as the resolved `run_root`, never reads). |
| `gateway_socket` | `--gateway-socket` | `SANDBOX_GATEWAY_SOCKET` | — (**required** in attach-only v1) | path must be a candidate socket; presence verified by `await_ready` in Phase B. |
| `build` | `--prebuilt-bin-dir` / `--cargo-profile` | — | `BuildSource::Cargo { profile: "package-fast" }` | `--gateway-socket` given ⇒ Phase A skipped regardless. |
| `cli_timeout` | `--cli-timeout-secs` | — | `30` (Duration) | `> 0`; serialized as `f64` seconds. |
| `gateway_ready_timeout` | `--gateway-ready-timeout-secs` | — | `5` (matches `gateway.rs:6`) | `> 0`. |
| `cleanup` | `--cleanup` / `--keep-artifacts` | — | `CleanupPolicy::OnSuccess` | `--keep-artifacts` forces `Never`. |

### 4.3 The three enums

```rust
pub enum TestSelection { All, Names(Vec<String>), RerunFailedFrom(PathBuf) }
pub enum CleanupPolicy { Always, OnSuccess, Never }
pub enum BuildSource   { Cargo { profile: String }, Prebuilt(PathBuf) }
```

- `TestSelection` → cargo invocation in §9.
- `CleanupPolicy`: `Always` = remove `run_root` regardless of run status;
  `OnSuccess` (default) = remove on success, keep on failure for inspection;
  `Never` = always keep (also set by `--keep-artifacts`).
- `BuildSource`: `Cargo { profile }` runs Phase A; `Prebuilt(dir)` skips it
  (`build.*_ms = 0`). Either way Phase A is skipped when `--gateway-socket` is
  given (attach-only).

### 4.4 `run_id` derivation

`--run-id` verbatim if supplied (validated against `[A-Za-z0-9._-]`, §4.2).
Otherwise:

```
run_id = "r{ts}-{ sha256(git_HEAD ‖ test_manifest_hash ‖ EOS_E2E_RUN_SALT)[..8] }"
```

| Component | Definition | Source |
|---|---|---|
| `ts` | colon-free UTC stamp, format `%Y%m%dT%H%M%SZ` (e.g. `20260625T140312Z`); colons would violate the charset. Pinnable via **`EOS_E2E_RUN_CLOCK`** for byte-stable reruns. | `time` crate |
| `git_HEAD` | output of `git rev-parse HEAD`. | `std::process::Command` |
| `test_manifest_hash` | sha256 over the **newline-joined, sorted** list of `tests/<scope>/**/*.rs` leaf **relative paths** — the same leaf set `build.rs` discovers (`build.rs:34-46`). Identity changes only when a test is **added/removed**; file **contents are excluded** (keeps reruns byte-stable). | `sha2` |
| `EOS_E2E_RUN_SALT` | optional disambiguator; **defaults to empty string**. | env |

`[..8]` = first 8 hex chars of the sha256 digest.

**Why not `uuid`.** The in-tree `uuid` is v4-random only
(`Cargo.toml:44 — features = ["v4"]`), so it cannot produce a deterministic
`run_id`. `uuid` is reserved for internal request correlation, which the
black-box orchestrator does not need. Determinism comes from `sha2` + a pinnable
clock, not `uuid`.

---

## 5. Artifact schemas

Every artifact carries `schema_version`. Five kinds live under `{run_root}`:
`run-manifest.json`, `summary.json`, and per-sandbox
`reports/{id}/{exchange.jsonl, result.json}`. (`observability.json` is Phase 4 —
**not written** here.)

### 5.1 `run-manifest.json` (orchestrator writes; `ManifestConfig` reads)

`report::write_run_manifest(run_root, …)` emits the **superset**; the live
`ManifestConfig`/`Manifest` reader (`config.rs:21-27`) reads the **minimal
subset**. serde ignores unknown fields (no `deny_unknown_fields`), so the
superset parses cleanly.

| Field | Type | Written by orchestrator | Read by `ManifestConfig::from_env` | Notes |
|---|---|---|---|---|
| `schema_version` | u32 | `1` | **yes** (bails on `!= 1`, `config.rs:43-48`; `SUPPORTED_SCHEMA_VERSION = 1`, `:8`) | **MUST be `1`** — load-bearing. |
| `gateway_socket` | string (path) | yes | **yes** (`Manifest.gateway_socket: PathBuf`, `:24`) | serialize as a path string; `serde_json` emits `PathBuf` as a JSON string, which `PathBuf: Deserialize` accepts. |
| `run_id` | string | yes | **yes** (`:25`) | the resolved `run_id`. |
| `image` | string | yes | **yes** (`:26`) | the resolved image. |
| `git_head` | string | yes | ignored | superset-only. |
| `config` | object | yes (`max_parallel`, `cleanup`, `cli_timeout` secs as f64, `build` summary) | ignored | superset-only; for reproducibility/inspection. |
| `clock` | string | yes (the `ts` used) | ignored | superset-only; pins the run timestamp. |

**Compatibility is the highest-risk contract.** Verify against `config.rs:21-27`:
the reader requires exactly `{schema_version:u32, gateway_socket:PathBuf,
run_id:String, image:String}`. The writer MUST emit those four with those types
plus `schema_version == 1`, and may add `git_head`/`config`/`clock` freely. A DTO
mismatch (e.g. `gateway_socket` as a non-string) breaks every test's
`Harness::init` (`fixtures.rs:32-36` panics on parse error).

### 5.2 `result.json` (one per sandbox, written in `Sandbox::drop`)

`{run_root}/reports/{sandbox_id}/result.json`. See §6 for emission ownership and
field derivation.

| Field | Type | Source |
|---|---|---|
| `schema_version` | u32 | `1` (a `RESULT_SCHEMA_VERSION` const in `report.rs`). |
| `test_name` | string | `std::thread::current().name()` (libtest names each test thread `module_slug::fn_name`), falling back to the provision slug if the thread is unnamed (§6). |
| `sandbox_id` | string | `Sandbox.id`. |
| `status` | string | `"failed"` if `std::thread::panicking()` at drop, else `"passed"`. |
| `duration_ms` | u128 | `Sandbox.started.elapsed().as_millis()` (Instant set at provision, §6). |
| `workspace_root` | string (path) | `Sandbox.workspace_root`. |
| `assertions` | object `{ total:u64, failed:u64 }` | thread-local counter for `total`; `failed = if panicking {1} else {0}` (§6). |
| `failure` | string \| null | a short failure marker when `status == "failed"` (e.g. `"assertion panicked"`); `null` on pass. The panic message itself is not capturable from `Drop`. |

### 5.3 `summary.json` (orchestrator writes in Aggregate)

`{run_root}/summary.json`. Built **solely** from globbed `result.json` plus the
cargo-test exit code.

| Field | Type | Source |
|---|---|---|
| `schema_version` | u32 | `1` (`SUMMARY_SCHEMA_VERSION`). |
| `run_id` | string | `RunConfig.run_id`. |
| `git_head` | string | git HEAD captured at manifest time. |
| `started_at` | string | runner-clock start (colon-free UTC). |
| `finished_at` | string | aggregate time (colon-free UTC). |
| `max_parallel` | usize | `RunConfig.max_parallel`. |
| `status` | string `passed`\|`failed`\|`error` | `passed` iff cargo-test exit == 0 AND every `result.json` status == `passed`; `error` if the cargo invocation could not run; else `failed`. |
| `counts` | object `{total,passed,failed,skipped,errored}` | from the `tests[]` rollup. `skipped` is always **0** in orchestrated runs (§6) — present for schema completeness only. |
| `tests[]` | array | one entry per `reports/*/` dir (see below). |
| `failed_tests[]` | array of string | the `tests[].name` of every entry with `status == failed`; drives `--rerun-failed-from`. |
| `artifacts_root` | string (path) | `run_root`. |
| `timing` | object | §8. |
| `cleanup` | object | §7 (`summary.cleanup` shape). |

`tests[]` element:

| Field | Type | Source |
|---|---|---|
| `name` | string | the `test_name` recorded in that dir's `result.json` (the thread name `module_slug::fn`). |
| `sandbox_id` | string | the `reports/{id}` directory name. |
| `status` | string | from `result.json` (`passed`/`failed`); a dir whose `result.json` is **missing** ⇒ `errored`, with `sandbox_id` from the dir name. |
| `duration_ms` | u128 | from `result.json`. |
| `workspace_root` | string | from `result.json`. |
| `report_dir` | string (path) | `{run_root}/reports/{sandbox_id}`. |
| `assertions` | object `{total,failed}` | from `result.json`. |
| `failure` | string \| null | from `result.json` (`null` for `errored`/`passed`). |

**`tests[]` construction rule.** Glob `{run_root}/reports/*/result.json`. For
each `reports/{id}/` directory: if `result.json` exists and parses, build the
entry from it; if it is **missing**, emit an `errored` entry with `sandbox_id =
{id}` (the dir name), `name = {id}` (no test identity is recoverable — the
runtime-assigned id carries none), and `failure = "result.json missing"`.

**Honest naming note.** The parent's idealized
`scope::family::operation::case::fn` is approximated by `{module_slug}::{fn}`:
the **scope** (which test binary) is the cargo target, not embedded in the libtest
thread name. The opaque runtime-assigned `sandbox_id` dir name carries no test
identity, so the name comes from `result.json`, never from the dir.

---

## 6. result.json emission spec

| Concern | Decision | Evidence |
|---|---|---|
| **Owner** | Fold the write into the existing `Sandbox::drop`, beside the exchange flush — `report::write_result(run_root, id, outcome)` immediately after `report::write_exchange` (`fixtures.rs:126-136`). No new owner type. | `fixtures.rs:126-136`, `report.rs:14-30` |
| **status** | `std::thread::panicking()` at drop ⇒ `"failed"`; else `"passed"`. libtest aborts the test thread at the first panicking assertion, so the panicking flag is set during unwind through `Drop`. | std |
| **duration_ms** | Add a field `started: Instant` to `Sandbox`, set in `provision_sandbox` (the live struct literal at `fixtures.rs:97-101`); at drop, `self.started.elapsed().as_millis()`. **provision starts the timer** (the live skeleton starts none). | `fixtures.rs:69-103, 110-114` |
| **test_name** | `std::thread::current().name()` — libtest names each test thread `module_slug::fn_name` (e.g. `command_exec_command_one_shot::one_shot_exec_returns_ok_and_zero_exit`), with a fallback to the provision **slug** if the thread is unnamed. Justification: the thread name is the **only** value directly usable as a libtest name filter for `--rerun-failed-from`. The provision slug uses hyphens / `caseN` and does **not** match the libtest module path, so it cannot be a filter. Capture the name at drop (or store at provision). | `fixtures.rs:69-74` slug shape |
| **assertions.total** | A **thread-local** `Cell<u64>` counter in `assertion.rs`, bumped by **each** helper (`ok`, `field`, `err_kind_at`) at entry; read at drop. Thread-locals are per-test-thread (libtest = one thread per test), so no reset is needed. | `assertion.rs:6-36` |
| **assertions.failed** | `if std::thread::panicking() { 1 } else { 0 }`. libtest aborts at the first panicking assertion, so `failed ∈ {0,1}`. | std |
| **Skip path (resolved)** | Under the orchestrator, **`EOS_E2E_RUN_ROOT` is always set**, so nothing skips. The env-unset bare-`cargo test` path early-returns **before** provisioning (`tests/support/mod.rs:7-9` → leaf `let Some(h)=support::harness() else { return };`, a **bare** return), so `Sandbox` is never constructed, `Sandbox::drop` never runs, and **no `result.json` is written** (there is no `run_root` to write into). This **supersedes** the parent Phase-1 prose about a `"skipped"` result.json. Therefore `counts.skipped` is **0** in orchestrated runs and the field exists only for schema completeness. | `tests/support/mod.rs:7-9`, `fixtures.rs:126-136` |

`report::write_result` mirrors `write_exchange` (`report.rs:14-30`): best-effort,
returns `std::io::Result<()>` so `Sandbox::drop` can swallow the error
(`let _ = …`), and writes into the **same** `reports/{id}/` directory
`write_exchange` already created.

Counter wiring in `assertion.rs` (thread-local, per *no test code in `src/`*
these are production helpers, no `#[cfg(test)]`):

```rust
thread_local! { static ASSERTIONS: std::cell::Cell<u64> = const { std::cell::Cell::new(0) }; }
pub fn assertion_count() -> u64 { ASSERTIONS.with(Cell::get) }
// ok(), field(), err_kind_at() each call ASSERTIONS.with(|c| c.set(c.get() + 1)) on entry.
```

---

## 7. Cleanup spec

### 7.1 RAII `RunGuard` (`src/cleanup.rs` ←NEW; `lib.rs` adds `pub mod cleanup;`)

```rust
pub struct RunGuard {
    run_root: PathBuf,
    gateway_socket: PathBuf,
    cli: CliClient,             // reused from cli_client.rs (black-box only)
    policy: CleanupPolicy,
    run_succeeded: bool,        // set by the orchestrator before drop
}
impl RunGuard {
    pub fn new(run_root: PathBuf, gateway_socket: PathBuf, policy: CleanupPolicy) -> Self;
    pub fn set_succeeded(&mut self, ok: bool);
    pub fn teardown(&self) -> CleanupReport;   // the §7.3 shape; idempotent
}
impl Drop for RunGuard { /* runs teardown() if not already run */ }
```

`RunGuard` is constructed in `main` right after the manifest is written (§3.1 step
2). Its `Drop` runs on **normal return and on panic in `main`** (so a panicking
aggregate still tears down). **Limit:** `Drop` does **not** run on `SIGKILL`/hard
abort — see §7.4.

### 7.2 Teardown order (each step idempotent)

1. **Survivor sweep.** Captured ids = the **directory names** under
   `{run_root}/reports/` (each dir name is a captured sandbox id; `write_exchange`
   created it, `report.rs:19`). The per-test `Sandbox::drop` reaps the happy path;
   the orchestrator sweeps survivors. For each id:
   `sandbox-cli manager destroy_sandbox --sandbox-id {id}` (idempotent — a
   destroy of an already-gone sandbox is a no-op error that is recorded but not
   fatal).
2. **Gateway — DETACH ONLY.** The runner **never** stops a gateway it did not
   start (attach-only v1). No socket/pid removal, no shutdown signal.
3. **`remove_dir_all(run_root)`**, gated by `CleanupPolicy`:
   - `OnSuccess` (default): remove iff `run_succeeded`, else keep for inspection.
   - `Always`: remove regardless.
   - `Never` (also `--keep-artifacts`): never remove.

Path namespacing guarantees this-run-only scope: every artifact/workspace this
runner owns is under `{run_root}`, so `remove_dir_all(run_root)` cannot reach a
sibling run.

### 7.3 `summary.cleanup` shape (`CleanupReport`)

```jsonc
{
  "policy": "OnSuccess" | "Always" | "Never",
  "removed_run_root": true | false,
  "destroyed_sandbox_ids": ["..."],   // ids the orchestrator swept this teardown
  "errors": ["..."]                   // human-readable, non-fatal (e.g. destroy failures)
}
```

The orchestrator writes the summary (§5.3) **before** `remove_dir_all` so the
summary survives even when the run_root is removed (write summary → run teardown →
fold `CleanupReport` into the already-written summary only when keeping; if
removing, the `summary.cleanup` reflects `removed_run_root: true` and the file is
deleted with the tree). For `Always`/successful `OnSuccess`, embed the
`CleanupReport` into `summary.json` before removal so an operator who reads
captured stdout still sees the cleanup outcome.

### 7.4 `--clean-run`, `--rerun-failed-from`, no-orphan-reaper limit

- **`--clean-run {run_id}`** (§3.3): re-runs §7.2 teardown for
  `{run_root_base}/{run_id}`, reading that run's `run-manifest.json` for the
  gateway socket. Idempotent.
- **`--rerun-failed-from {summary.json}`** (a `run` flag, §9): sets
  `TestSelection::RerunFailedFrom`; parses `failed_tests[]` (the thread-name
  filters) from the prior summary, and uses each as a libtest name filter in a
  fresh, independently cleanable `run_root` (new `run_id`).
- **No-orphan-reaper limit (honest).** There is **no Docker run-label**
  (`create_sandbox` accepts only `--image`/`--workspace-root`;
  `CreateSandboxRequest` has no label field, `runtime.rs:5-14`), so the only
  cleanup keys are captured ids + path namespacing. The RAII `Sandbox` drop reaps
  on assertion panic, and `RunGuard` sweeps survivors on normal/panic exit, but a
  **`SIGKILL` / hard abort mid-run can leak containers** with no backstop. A
  label-based reaper is Open Items #2 (a runtime change, not this crate).

---

## 8. Timing capture (Phase-3-scoped)

Observability-derived fields are Phase 4 and are **omitted** here. Phase 3
records only what its own clocks and `result.json` provide.

```jsonc
"timing": {
  "build":   { "gateway_build_ms": u128, "cli_build_ms": u128,
               "cargo_profile": "package-fast", "cache_hit": bool },
  "runner":  { "wall_ms": u128, "gateway_attach_ms": u128,
               "test_process_ms": u128, "teardown_ms": u128, "max_parallel": usize },
  "per_test": [ { "name": "module_slug::fn", "sandbox_id": "...", "total_ms": u128 } ]
}
```

| Field | Source | Zero when |
|---|---|---|
| `build.gateway_build_ms` / `build.cli_build_ms` | own `Instant`s around each Phase A build command. | `BuildSource::Prebuilt` **or** `--gateway-socket` given (Phase A skipped) ⇒ both `0`. |
| `build.cargo_profile` | `BuildSource::Cargo.profile` (or `"prebuilt"`). | — |
| `build.cache_hit` | heuristic (e.g. build elapsed below a threshold); informational. | — |
| `runner.wall_ms` | wall around the whole runner phase (clock starts only after binaries exist + socket reachable). | never. |
| `runner.gateway_attach_ms` | the `gateway::await_ready` duration (`gateway.rs:12-26`). | never. |
| `runner.test_process_ms` | wall around the `cargo test` child. | never. |
| `runner.teardown_ms` | cleanup duration (§7). | never. |
| `runner.max_parallel` | `RunConfig.max_parallel`. | — |
| `per_test[].total_ms` | each `result.json` `duration_ms`. | — |

**Deferred to Phase 4 (named, not designed):** the parent's richer
`runner.{queue_wait_p50_ms, queue_wait_p95_ms, test_setup_total_ms,
test_exec_total_ms}` and `per_test.{queue_wait_ms, create_ms, daemon_ready_ms,
exec_ms, teardown_ms}` need observability / per-call attribution that Phase 3
does not collect.

---

## 9. TestSelection → cargo test

The default target lives behind **one named constant** — the sole Stage 2
touchpoint:

```rust
// the SOLE stage-aware line in the whole crate.
pub const STAGE1_DEFAULT_TARGET: &[&str] = &["--test", "manager"];
```

| `TestSelection` | cargo invocation (shape) |
|---|---|
| `All` | `cargo test -p sandbox-e2e-live-test {STAGE1_DEFAULT_TARGET} -- --test-threads={N}` |
| `Names(v)` | `cargo test -p sandbox-e2e-live-test {STAGE1_DEFAULT_TARGET} -- {v…} --test-threads={N}` (each name is a `module_slug::fn` libtest filter) |
| `RerunFailedFrom(p)` | parse `failed_tests[]` from the prior `summary.json` at `p`; treat as `Names(failed_tests)`. |

- `{N}` = `max_parallel`, precedence `--max-parallel` > `EOS_E2E_MAX_PARALLEL` >
  `available_parallelism().min(8)`.
- Filters are the `module_slug::fn` thread names (e.g.
  `lifecycle_list_sandboxes_lists_ready::lists_ready`), matching libtest's name
  filter semantics — the same value recorded in `result.json.test_name` (§5.2),
  so `failed_tests[]` round-trips into the next run's filters.
- **Stage 2 flip:** drop `--test manager` from `STAGE1_DEFAULT_TARGET` so the full
  suite (`--test manager` **and** `--test runtime`) runs. Nothing else changes.

**Stage 1 green claim.** With `STAGE1_DEFAULT_TARGET = ["--test", "manager"]`,
only the manager binary runs (M1–M5, N1) — **zero runtime calls**. No
runtime-readiness skip guard is introduced; the only skip path remains
`EOS_E2E_RUN_ROOT` unset (`tests/support/mod.rs:7-9`).

---

## Async vs sync (pinned)

The orchestrator is **synchronous**. It shells out via `std::process::Command`
(`docker`, `sandbox-cli`, `cargo test`), globs files under `{run_root}`, and
reuses the synchronous `gateway::await_ready` (`gateway.rs:12-26`). There is **no
fan-out** — `cargo test` owns thread parallelism via `--test-threads`. Therefore
`tokio`/`tokio-util`/`uuid` are **not** added; the only new deps are `clap`,
`sha2`, `time` (§2), justified under *prefer less*.

---

## 10. Anchor ledger

| Anchor (`file:line`) | Claim | Verdict |
|---|---|---|
| `Cargo.toml` (crate) `:15-18` | crate deps today = `anyhow`, `serde`, `serde_json` only; `[[bin]] eos-e2e` at `:11-13` | **confirmed** |
| `src/bin/eos-e2e.rs:1-9` | stub `eprintln!` + `ExitCode::from(2)`; Phase 3 replaces it | **confirmed** |
| `src/config.rs:8` | `const SUPPORTED_SCHEMA_VERSION: u32 = 1` | **confirmed** |
| `src/config.rs:14-19` | test-side reader named `RunConfig{run_root,gateway_socket,run_id,image}` → rename to `ManifestConfig` | **confirmed** |
| `src/config.rs:21-27` | serde DTO `Manifest{schema_version:u32, gateway_socket:PathBuf, run_id:String, image:String}`, no `deny_unknown_fields` | **confirmed** |
| `src/config.rs:29-55` | `from_env() -> anyhow::Result<Option<…>>`; `Ok(None)` when env unset; bail on schema mismatch (`:43-48`) | **confirmed** |
| `src/report.rs:8` | `const EXCHANGE_SCHEMA_VERSION: u32 = 1` | **confirmed** |
| `src/report.rs:14-30` | `write_exchange(run_root, id, records) -> io::Result<()>`; creates `reports/{id}/`; schema header line + per-record lines | **confirmed** |
| `src/fixtures.rs:7` | `use crate::config::RunConfig;` → change to `ManifestConfig` | **confirmed** |
| `src/fixtures.rs:33` | sole consumer `RunConfig::from_env()` → `ManifestConfig::from_env()` | **confirmed** |
| `src/fixtures.rs:57-60` | `run_root() -> &Path` | **confirmed** |
| `src/fixtures.rs:69-103` | `provision_sandbox(slug, image) -> (Sandbox, CallRecord)`; workspace `{run_root}/work/{run_id}-{slug}` canonicalized; reads `/id`; **starts no timer** | **confirmed** |
| `src/fixtures.rs:97-101` | `Sandbox` struct literal `{id, workspace_root, exchange}` — add `started: Instant` here | **confirmed** |
| `src/fixtures.rs:110-114` | `Sandbox{id, workspace_root, exchange:RefCell<Vec<CallRecord>>}` | **confirmed** |
| `src/fixtures.rs:121-123` | `Sandbox::record(&self, &CallRecord)` appends | **confirmed** |
| `src/fixtures.rs:126-136` | `Sandbox::drop`: if `Harness::get()` Some → `write_exchange` then `destroy_sandbox`; fold `write_result` here | **confirmed** |
| `src/gateway.rs:6-7` | `READY_TIMEOUT=5s`, `POLL_INTERVAL=50ms` | **confirmed** |
| `src/gateway.rs:12-26` | `await_ready(socket) -> anyhow::Result<()>`; attach-only poll; reuse for Phase B | **confirmed** |
| `src/cli_client.rs:11-20` | `CallRecord{argv, request_json, response_json, exit_code, stdout, stderr, latency_ms}` | **confirmed** |
| `src/cli_client.rs:38-54` | `manager(op,args)`, `runtime(id,op,args)` | **confirmed** |
| `src/cli_client.rs:63-78` | `latency_ms` via Instant; carrier = `exit_code==0 ? stdout : stderr`; parse `unwrap_or_default()` | **confirmed** |
| `src/assertion.rs:6-36` | `ok`/`field`/`err_kind_at` — add thread-local counter bump to each | **confirmed** |
| `src/lib.rs:1-9` | `pub mod {assertion,cli_client,config,fixtures,gateway,report}`; no `cleanup` — add `pub mod cleanup;` | **confirmed** |
| `build.rs:34-46` | walks `tests/<scope>/**/*.rs`, slug = components joined by `_` minus `.rs`; no Phase 3 edit | **confirmed** |
| `tests/support/mod.rs:7-9` | `harness() = Harness::get()`; sole skip path; leaf does bare `return`, writes nothing on skip | **confirmed** |
| `gateway/main.rs:94-101` | `default_manager_services()` wires `UnconfiguredRuntime` (`:97`) + `UnconfiguredDaemonInstaller` (`:98`) | **confirmed** |
| `gateway/main.rs:103-120` | `UnconfiguredRuntime`; `"sandbox runtime is not configured"` at `:111` (`create_sandbox`) and `:118` (`destroy_sandbox`); **only `create_sandbox`/`destroy_sandbox` reach the runtime trait** — store-only ops can't detect it → preflight probe must `create_sandbox` | **confirmed** |
| `gateway/main.rs:122-146` | `UnconfiguredDaemonInstaller` ("sandbox daemon installer is not configured") | **confirmed** |
| `cli/output.rs:21-23` | `EXIT_SUCCESS=0`, `EXIT_FAILURE=1`, `EXIT_USAGE=2` | **confirmed** |
| `cli/output.rs:257-273` (`render_response`) | carried `error` → stderr + exit 1 (`:266-268`); clean → stdout + exit 0 (`:269-271`) | **confirmed** (parent cited `:266-272`; `render_response` spans `:257-273`) |
| `cli/output.rs:287-292` (`render_request_error`) | request build error → `invalid_request` on stderr + exit 2 | **confirmed** |
| `daemon_install.rs:21-29` | `SandboxDaemonInstaller` is the **trait** (the parent miscalled the installer this name) | **confirmed / corrected** |
| `daemon_install.rs:31-36` | installer **struct is `LocalSandboxDaemonInstaller`**, fields `executable, config_yaml_path, runtime_root` | **corrected** (parent said `SandboxDaemonInstaller`) |
| `daemon_install.rs:38-50` | ctor `new(executable, config_yaml_path, runtime_root)` | **confirmed** |
| `daemon_install.rs:58-60` | per-sandbox `{runtime_root}/{id}/runtime.sock`, `.../runtime.pid` | **corrected** (parent cited `:52-57`; correct is `:58-60`) |
| `xtask/src/main.rs:37` | `Some("package") => package(&PackageArgs::parse(args)?)` dispatch | **corrected** (parent cited `:764`, which is inside `matching_close_paren_index`) |
| `xtask/src/main.rs:854-894` | `fn package(args)` | **corrected** |
| `xtask/src/main.rs:868` | builds `DAEMON_BINARY` artifact path (`fs::copy` at `:871`) | **corrected** |
| `xtask/src/main.rs:24` | `DEFAULT_PACKAGE_PROFILE = "package-fast"` | **confirmed** |
| `model.rs:10-22` | `SandboxId::new` rejects empty (`:12`), rejects any char not `is_ascii_alphanumeric() \|\| '-'\|'_'\|'.'` (`:15-18`) → `run_id` charset `[A-Za-z0-9._-]`, no `:` | **confirmed** |
| `runtime.rs:5-14` | `CreateSandboxRequest{image, workspace_root}`, `CreateSandboxResult{id}`; **no label field** → no Docker run-label cleanup backstop | **confirmed** (parent cited `:6-14`; struct begins at `:5`) |
| `create_sandbox.rs:17-44` | `create_sandbox` requires `--image` + `--workspace-root` → valid preflight probe | **confirmed** |
| `Cargo.toml` (workspace) `:36,43,44,45,46,51` | `sha2 :36`, `time :43`, `uuid :44` (v4-only), `tokio :45`, `tokio-util :46`, `clap :51` | **confirmed / corrected** (bootstrap listed clap `:51`, sha2 `:36`, time `:43`, uuid `:44`, tokio `:45`, tokio-util `:46` — verified against current `Cargo.toml`) |

> **Tree drift note.** Bootstrap pinned HEAD `10d4fbce…`; the working tree is now
> `fba4eeb0…` (concurrent work). All Phase-3 starting-state anchors above were
> re-verified against the current tree: the crate `Cargo.toml`, `config.rs`,
> `fixtures.rs`, and the manager test leaves match the bootstrap facts, so the
> Phase 3 starting point is intact.

---

## 11. Verification & acceptance

Run from the crate/workspace root (`export PATH="$PWD/bin:$PATH"` first).

| # | Command | Pass criterion |
|---|---|---|
| 1 | `cargo build -p sandbox-e2e-live-test` | exit 0 |
| 2 | `cargo clippy -p sandbox-e2e-live-test --all-targets` | exit 0; no new `unwrap_used`/`dbg_macro`/`undocumented_unsafe_blocks` |
| 3 | `cargo fmt --check` | exit 0 |
| 4 | `cargo test -p sandbox-e2e-live-test` (env unset) | **every leaf skips cleanly**, no panic, **nothing written** (no `result.json`, no `run_root`). |
| 5 | `eos-e2e preflight` on a non-Linux host | exit **2**, message `EphemeralOS E2E is Linux+Docker only; current OS={os}`. |
| 6 | `eos-e2e preflight` on Linux, Docker down | exit **2**, message `Docker daemon not reachable at $DOCKER_HOST`. |
| 7 | `eos-e2e preflight` on Linux, image absent | exit **2**, message ``image {image} not present; run `docker pull {image}` ``. |
| 8 | `eos-e2e preflight --gateway-socket {unconfigured}` | the `create_sandbox` probe returns an error whose message contains `runtime is not configured` ⇒ exit **2** with the long message naming the missing real-runtime gateway and `gateway/main.rs:94-146`. |
| 9 | `eos-e2e --gateway-socket {real} --image ubuntu:24.04 --max-parallel 8` (Linux + Docker + **external real-runtime gateway**) | produces `{run_root}/run-manifest.json`, `{run_root}/summary.json`, and per sandbox `{run_root}/reports/{id}/{exchange.jsonl, result.json}`; exits 0 iff cargo-test exit 0 AND every `result.json == passed`; cleans up per `OnSuccess` (removes `run_root` on success, keeps on failure). |
| 10 | `… --keep-artifacts` | `run_root` is **kept** regardless of outcome; `summary.cleanup.removed_run_root == false`. |
| 11 | `eos-e2e --gateway-socket {real} --rerun-failed-from {prior summary.json}` | parses `failed_tests[]`, runs only those filters in a fresh `run_root`; on failure prints the exact focused-rerun line. |
| 12 | `eos-e2e --clean-run {run_id}` | re-runs teardown for `{run_root_base}/{run_id}` (reads its `run-manifest.json` for the socket); idempotent. |

**Honest gate note.** A *green* Stage 1 run (#9–#12) requires an externally
started `sandbox-gateway` wired with the **real Docker runtime**, attached via
`--gateway-socket`. The shipped gateway wires `Unconfigured*` stubs
(`gateway/main.rs:94-146`) and fails every `create_sandbox`; against it the
preflight (#8) fails fast by design. This is **Open Items #1** — code is complete
and skip-safe (#4) regardless; only the live-green proof waits on the
real-runtime gateway.

---

## 12. Conventions checklist

| Convention | How Phase 3 satisfies it |
|---|---|
| **SRP / one job per unit** | `cleanup.rs` owns only the run guard + teardown; `report.rs` owns only artifact writing (`write_exchange`/`write_result`/`write_summary`/`write_run_manifest`); `config.rs` splits the test-side reader (`ManifestConfig`) from the orchestrator config (`RunConfig`/`Args`). |
| **Prefer less** | Only 3 new deps (`clap`, `sha2`, `time`); no `tokio`/`uuid`. `result.json` is folded into the existing `Sandbox::drop` (no new owner). `run_id` reuses `sha2`+`time`. The stage line is one named const, not a config knob. |
| **No inline comments in `src/`** | Doc comments (`///`/`//!`) on public items only; no inline `//` in production code. Test-intent comments are allowed only in `tests/` (unchanged here). |
| **No test code in `src/`** | The thread-local counter and `result.json` path are **production** behavior (always-on, no `#[cfg(test)]`); no fakes/mocks/stubs in `src/`. |
| **Workspace deps** | `clap`/`sha2`/`time` added as `dep.workspace = true`; no pinned versions in the member crate. |
| **Clippy lints** | No new `unwrap_used` (use `?`/`unwrap_or`/`expect` with messages as the skeleton does), no `dbg_macro`; any `unsafe` carries a `// SAFETY:` block (none expected). |
| **Boundary law** | Black-box only: orchestrator drives `sandbox-cli` over the gateway socket and reads only artifacts under `{run_root}`. No internal-crate dep, no `*_for_test` reader, no test-injected runtime. Linux+Docker only; off-Linux/no-Docker exits 2. Pass/fail gate = cargo-test exit code, never libtest stdout. |
