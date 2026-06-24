# Prompt: Implement Phase 0 + Phase 1 of `sandbox-e2e-live-test`

Use this prompt to **build** Phase 0 (Scaffold the crate) and Phase 1 (Harness
core + one operation) of the live E2E runner. This produces **code**, not a spec.

## Repo

```text
/Users/yifanxu/machine_learning/LoVC/ephemeral-ai/ephemeral-os
```

## Mission

Implement Phases 0 and 1 of the crate `sandbox-e2e-live-test` exactly as fixed in
`docs/e2e/sandbox-e2e-live-test-phase-0-1-spec.md`. That spec is **fixed**: its
anchor ledger was already adversarially verified, so do **not** re-derive the
design or re-cite live code from scratch. Follow the spec; re-confirm the
load-bearing live-code facts only as you touch them, and on any conflict **live
code wins** — if a signature, exit code, or response field differs from the spec,
match live code and note the deviation. Do not duplicate the spec here; it carries
every file, signature, schema, and command. This prompt tells you the order, the
fences, and how to prove done.

The runner is a black-box live E2E runner: it drives real Docker sandboxes
exclusively through the public `sandbox-cli` → `sandbox-gateway` socket boundary.
Phases 0–1 stand up the harness library, the build-time include generation, and
the first two leaf tests (one manager op, one runtime op) — buildable and
skip-safe on any machine.

## Source Material (read first)

| Document | Role | Key sections to navigate by |
|----------|------|------------------------------|
| `docs/e2e/sandbox-e2e-live-test-phase-0-1-spec.md` | **PRIMARY — the fixed design. The single source of truth.** | Phase Boundary (L18); Phase 0 File Manifest (L71); `Cargo.toml` resolved deps (L96); `src/lib.rs` surface (L147); `eos-e2e.rs` stub (L167); `build.rs` skeleton (L183); members edit position (L226); Phase 1 Module Specs (L251) — `config.rs` (L256), `cli_client.rs` (L298), `fixtures.rs` (L385), `gateway.rs` (L451), `assertion.rs` (L478), `tests/support/mod.rs` (L499), `build.rs` contract (L536); `run-manifest.json` schema (L583); the two leaves (L613); Verification & Acceptance (L683); Anchor Ledger (L752); Conventions Checklist (L823); Prefer-less Ledger (L850). |
| `docs/e2e/sandbox-e2e-live-test-spec.md` | Parent design (phases overview, Open Items). | Implementation Phases; Open Items #1 (unshipped real-runtime gateway). |
| `README.md` + `CLAUDE.md` | Boundary law + engineering practice. | Component map; `export PATH="$PWD/bin:$PATH"`; SOLID/SRP; no inline comments in production; workspace deps via `dep.workspace = true`; clippy lints. |

The spec is authoritative for *what to build*; live code is authoritative for
*what is true today*. You do not need to re-open every anchor — only the ones a
given module depends on as you implement it.

## How To Run (multi-agent: scaffold → implement → prove skip → adversarial review)

Run as cooperating agents, not one pass. The **Implementer** builds; the
**Reviewer** adversarially checks each result against the spec, the scope fence,
the boundary law, and the conventions checklist before it is accepted.

```text
0. Orchestrator: bootstrap once. `export PATH="$PWD/bin:$PATH"`. Confirm the crate
   is NOT yet a workspace member and crates/sandbox-e2e-live-test/ is absent
   (Anchor Ledger L819). Share with the Implementer.

1. Implementer — PHASE 0 (atomic). In ONE change, add the member entry AND create
   the full crate scaffold (manifest + build.rs skeleton + lib.rs + eos-e2e stub +
   empty tests/ tree). Adding the member without the manifest breaks the workspace
   build, so both halves ship together (spec L20-30). Then PASS THE PHASE 0 GATE
   before any Phase 1 work:
     cargo build -p sandbox-e2e-live-test
     cargo build
     cargo clippy -p sandbox-e2e-live-test --all-targets
   Do not start Phase 1 until all three exit 0.

2. Implementer — PHASE 1 (dependency order). Implement the modules in this order,
   running `cargo build` / `cargo clippy -p sandbox-e2e-live-test --all-targets`
   continuously after each:
     config.rs -> cli_client.rs -> gateway.rs -> fixtures.rs -> assertion.rs
       -> tests/support/mod.rs -> build.rs (full contract) -> the two leaves.
   Rationale: fixtures depends on config + cli_client + gateway; support depends on
   fixtures + assertion; the leaves depend on support + build.rs include generation.

3. Implementer — PROVE THE SKIP-CLEAN GATE (no E2E machine, no gateway):
     cargo test -p sandbox-e2e-live-test        # EOS_E2E_RUN_ROOT unset
   Must exit 0, both leaves early-return (skip) without panicking, nothing written.

4. Reviewer (adversarial; given the diff + the spec + this prompt). Independently
   re-check the implementation against: (a) the spec's public signatures and
   behavior contracts; (b) the SCOPE FENCE — flag anything Phase 2-4 that leaked in
   and anything Phase 0-1 needs that is missing; (c) the SETTLED BOUNDARY LAW; (d)
   the CONVENTIONS CHECKLIST (SRP, no inline comments in production, workspace deps,
   #[path]/include! + OUT_DIR, clippy lints). Return a defect list (no rewrite).

5. Orchestrator: hand defects back to the Implementer; loop steps 2-4 until the
   Reviewer returns zero defects and zero scope leaks, and the Phase 0 + skip-clean
   gates are green.
```

The live-green gate (step in Verification below) is **external and unshipped** —
it cannot be passed in this environment. Done = Phase 0 green + skip-clean green;
the live-green path is documented but blocked (see Honest Gate).

## Scope Fence (in vs out)

### IN — Phase 0 (Scaffold), one atomic change

- Edit root `Cargo.toml`: insert `"crates/sandbox-e2e-live-test",` into the
  `members` array **after line 16** (last entry `"xtask",`) and **before the
  closing `]` on line 17** (spec L226-247). Do not reflow the other entries.
- Create the crate (spec File Manifest L71):
  - `Cargo.toml` — `[package]` (workspace inheritance), `[lib]`, `[[bin]] eos-e2e`,
    `[dependencies]` = **exactly `anyhow` + `serde` + `serde_json`**, `[lints]
    workspace = true` (spec L96-124).
  - `build.rs` — empty-tree-safe include generator skeleton (spec L183-204).
  - `src/lib.rs` — re-export surface (spec L147-165).
  - `src/bin/eos-e2e.rs` — print-and-exit stub, `ExitCode::from(2)` (spec L167-181).
  - `src/config.rs`, `src/cli_client.rs`, `src/fixtures.rs`, `src/gateway.rs`,
    `src/assertion.rs` — stubs that compile.
  - `tests/support/mod.rs`, `tests/manager.rs`, `tests/runtime.rs` (spec L206-224).
- The leaf directories and leaf files are **not** created in Phase 0 — the
  generated include lists are empty and the two root binaries compile to empty test
  binaries (empty-tree-builds invariant, spec L202-204).

### IN — Phase 1 (Harness core + one operation)

- `config.rs` — minimal `RunConfig` + `run-manifest.json` load; `from_env()`
  returns `Ok(None)` (skip) / `Ok(Some(_))` / `Err` (misconfig) (spec L256-296).
- `cli_client.rs` — `CallRecord { argv, request_json?, response_json, exit_code,
  stdout, stderr, latency_ms }`; `manager()` / `runtime()`; exit-code routing
  `0`=stdout, `1`/`2`=stderr; `request_json` is `None` on the black-box path (spec
  L298-383).
- `fixtures.rs` — lazy `Harness` via `OnceLock`; `provision_sandbox(slug, image)
  -> (Sandbox, CallRecord)` reading the runtime-assigned `/id`; RAII `Sandbox` drop
  → `destroy_sandbox` (spec L385-449).
- `gateway.rs` — **attach-only** `await_ready`, `READY_TIMEOUT = 5s`, never spawns
  (spec L451-476).
- `assertion.rs` — **only** `ok` + `field` (spec L478-497).
- `tests/support/mod.rs` — `harness() -> Option<&'static Harness>` skip-safe entry
  (spec L499-534).
- `build.rs` — per-scope `$OUT_DIR/<scope>_mods.rs` include generation,
  `family_operation` slug, `rerun-if-changed` triggers, empty-tree-builds (spec
  L536-579).
- Two leaves: `tests/manager/lifecycle/create_sandbox.rs` (M1) and
  `tests/runtime/command/exec_command.rs` (R1) (spec L613-679).

### OUT — Phases 2–4 (named here as out-of-scope; do NOT build)

- **P2:** full matrix M2–M5, R2–R8, routing negatives N1/N2; and the
  `assertion.rs` helpers they need — `err_kind_at`, `err_detail`, `non_decreasing`,
  `offsets_monotonic`.
- **P3:** `eos-e2e` orchestrator internals — clap arg parsing, preflight, build
  phase, env export, aggregation from `result.json`, `summary.json` / timing,
  cleanup orchestration. In Phases 0–1 `eos-e2e` stays a **stub**. Also: `report.rs`,
  `cleanup.rs`, `exchange.jsonl` / `result.json` artifacts, `snapshot_observability`,
  `RerunFailedFrom`, `CleanupPolicy`, `BuildSource`, `TestSelection`, `run_id`
  **derivation** + charset-validation-at-parse (Phase 1 reads `run_id` verbatim).
- **P4:** observability polling, `observability.json`, P1 (cgroup CPU/mem),
  P2 (queue-wait).
- **Deferred (Open Items #1/#2):** spawn-mode gateway; Docker run-label cleanup.
- **Deps NOT added in Phase 0–1:** `clap`, `uuid`, `time`, `sha2`, `tokio`,
  `tokio-util`, `thiserror`, `futures-util`, `sandbox-protocol`. The manifest is
  exactly `anyhow + serde + serde_json` (spec deps table L132-145).

## Settled Boundary Law (keep intact — do not cross)

- **CLI-only provisioning.** Sandbox/image ops go through `sandbox-cli` only — no
  test-injected `SandboxRuntime`, no internal manager/runtime crate dependency on
  the black-box path. No manager-side observability sink. Linux + Docker only.
- **Attach-only gateway.** `gateway.rs` validates/awaits the supplied
  `--gateway-socket` path; it **never spawns** a gateway.
- **Runtime-assigned ids.** Capture `/id` from the `create_sandbox` response and
  round-trip it; never predict or supply the id.
- **One cross-process env contract: `EOS_E2E_RUN_ROOT`.** Gateway socket, `run_id`,
  and image are read from `{run_root}/run-manifest.json`. Introduce no other env var.
- **`serde_json::Value` is the response carrier.** No typed DTOs; do not add
  `sandbox-protocol`.

## File Checklist

`export PATH="$PWD/bin:$PATH"` first (CLAUDE.md). Crate root:
`crates/sandbox-e2e-live-test/`. `members` edit: insert
`"crates/sandbox-e2e-live-test",` after line 16, before `]` on line 17. Manifest
deps: **exactly `anyhow` + `serde` + `serde_json`**, each via `dep.workspace = true`.

### Phase 0 (scaffold — all created in one atomic change)

| File | Single job | Spec |
|------|-----------|------|
| `Cargo.toml` | Declare crate: `[package]` inherit, `[lib]`, `[[bin]] eos-e2e`, deps `anyhow`/`serde`/`serde_json`, `[lints] workspace`. | L96-124 |
| `build.rs` | Emit `$OUT_DIR/{manager,runtime}_mods.rs` from the leaf tree; empty tree → empty files, build succeeds. | L183-204 |
| `src/lib.rs` | Crate root: declare modules + re-export `CallRecord`/`CliClient`/`RunConfig`/`Harness`/`Sandbox`. | L147-165 |
| `src/bin/eos-e2e.rs` | Orchestrator stub: print "not implemented in Phase 0-1; set EOS_E2E_RUN_ROOT and run cargo test" notice, exit `2`. | L167-181 |
| `src/config.rs` | (stub) Phase-1 `RunConfig` + manifest load. | L82 |
| `src/cli_client.rs` | (stub) Phase-1 CLI driver + call record. | L83 |
| `src/fixtures.rs` | (stub) Phase-1 `Harness` + `Sandbox`. | L84 |
| `src/gateway.rs` | (stub) Phase-1 attach-mode readiness. | L85 |
| `src/assertion.rs` | (stub) Phase-1 `ok` + `field`. | L86 |
| `tests/support/mod.rs` | Skip-safe entry: `harness() -> Option<&'static Harness>`. | L87 |
| `tests/manager.rs` | Manager binary: `#[path] mod support;` + `include!(OUT_DIR/manager_mods.rs)`. | L88, L213-217 |
| `tests/runtime.rs` | Runtime binary: `#[path] mod support;` + `include!(OUT_DIR/runtime_mods.rs)`. | L89, L219-223 |

### Phase 1 (implement in dependency order)

| Order | File | Single job | Spec |
|-------|------|-----------|------|
| 1 | `src/config.rs` | `EOS_E2E_RUN_ROOT` → manifest into minimal `RunConfig`; `from_env` → `Ok(None)`/`Ok(Some)`/`Err`. | L256-296 |
| 2 | `src/cli_client.rs` | Invoke `sandbox-cli` once, capture `CallRecord`, parse single NDJSON line, route by exit code. | L298-383 |
| 3 | `src/gateway.rs` | Attach-only `await_ready` poll; `READY_TIMEOUT = 5s`; no spawn. | L451-476 |
| 4 | `src/fixtures.rs` | Lazy `OnceLock` `Harness`; `provision_sandbox` reads `/id`; RAII `Sandbox` drop → `destroy_sandbox`. | L385-449 |
| 5 | `src/assertion.rs` | `ok` (no top-level `error`) + `field` (JSON-pointer get-or-panic) only. | L478-497 |
| 6 | `tests/support/mod.rs` | Re-surface harness; `harness()` forwards `Harness::get()`. | L499-534 |
| 7 | `build.rs` (full) | Walk `tests/<scope>/**/*.rs`; `family_operation` slug; emit `#[path]` lines; rerun-if-changed; empty-tree builds. | L536-579 |
| 8 | `tests/manager/lifecycle/create_sandbox.rs` | Leaf **M1**: provision once, assert `/id` charset, `/state == "ready"`, `/daemon/socket_path` non-null. | L619-651 |
| 9 | `tests/runtime/command/exec_command.rs` | Leaf **R1**: provision, `runtime exec_command pwd`, assert `/status == "ok"`, `/exit_code == 0`, `command_session_id` absent. | L653-679 |

## Acceptance Gates

Run from repo root with repo-local tools on `PATH` (spec Verification L683-732):

```sh
export PATH="$PWD/bin:$PATH"   # CLAUDE.md — makes `sandbox-cli` resolve to bin/sandbox-cli
```

### Phase 0 — build + clippy (gate before any Phase 1 work)

```sh
cargo build -p sandbox-e2e-live-test         # crate compiles (empty test tree, empty includes)
cargo build                                  # workspace-wide build still succeeds
cargo clippy -p sandbox-e2e-live-test --all-targets   # passes under workspace lints
```

**Pass:** all three exit 0. Empty-tree `build.rs` emits empty
`$OUT_DIR/{manager,runtime}_mods.rs`; both root binaries compile empty.

### Phase 1 — skip-clean test (no E2E machine, no gateway)

```sh
cargo test -p sandbox-e2e-live-test          # EOS_E2E_RUN_ROOT unset
```

**Pass:** exit 0; both leaves early-return (skip) without panicking; nothing
written. This is the buildable-and-skip-safe-without-the-gateway guarantee. The
panic path is reserved for a *set-but-broken* manifest (operator misconfig), never
for the unset case.

### Phase 1 — green against a real-runtime gateway (recipe; externally gated)

Requires a Linux host with Docker, the `ubuntu:24.04` image present, and an
**externally started gateway wired with the real Docker runtime**, attached via its
socket path. Hand-write the manifest, then run:

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

**Pass:** both leaves run green under `--test-threads=1`.

### Honest Gate (Open Items #1) — state plainly, do not paper over

The green live run is **blocked on an unshipped prerequisite**. The shipped
`sandbox-gateway` binary wires `UnconfiguredRuntime` / `UnconfiguredDaemonInstaller`
stubs (`default_manager_services`, `gateway/main.rs:94-146`), and
`UnconfiguredRuntime::create_sandbox` (`gateway/main.rs:106`) returns
`RuntimeFailed { message: "sandbox runtime is not configured" }`
(`gateway/main.rs:110-112`). A gateway from the shipped binary therefore **fails
every `create_sandbox`**, so `provision_sandbox` cannot succeed and the live suite
is **non-executable** until a gateway wired with a real `SandboxRuntime` +
`SandboxDaemonInstaller` is started externally and attached via `--gateway-socket`
(that gateway additionally needs a `sandbox-daemon` executable from
`cargo run -p xtask -- package`, a daemon config YAML, and a runtime-root — none
provided by this crate). Readiness ≠ a working runtime: `gateway::await_ready`
confirms the socket connects, not that the runtime is configured. **Phases 0–1 are
fully buildable and skip-safe today; the green live run is external and cannot be
passed here.**

## Conventions Checklist

- **SRP / one job per module.** `config` = manifest load; `cli_client` = invoke +
  capture + parse; `fixtures` = `Harness` + RAII `Sandbox`; `gateway` = attach-mode
  readiness; `assertion` = response-shape checks; `build.rs` = include generation.
  No module spans two responsibilities (spec L825-829).
- **No inline comments in production code.** `src/` carries doc comments (`///` /
  `//!`) on public items only. The two leaf tests may use intent comments (allowed
  in tests per CLAUDE.md). Production stubs ship without inline comments.
- **Workspace deps via `dep.workspace = true`.** `anyhow`, `serde`, `serde_json`
  only; no versions pinned in the member crate.
- **`#[path]` / `include!` + `OUT_DIR`.** Root binaries use `#[path =
  "support/mod.rs"] mod support;` + `include!(concat!(env!("OUT_DIR"),
  "/<scope>_mods.rs"))`. The generated list emits one `#[path = "<abs leaf>"] mod
  <slug>;` per leaf. Mirrors `crates/sandbox-daemon/tests/unit.rs:3-4,32-55` (the
  in-tree variant uses `CARGO_MANIFEST_DIR`; the generated variant uses `OUT_DIR`).
- **Clippy lints (workspace, deny correctness/suspicious).** No `.unwrap()` in
  production `src/` (`unwrap_used = "warn"`, `Cargo.toml:78`); no `dbg!`
  (`dbg_macro = "warn"`, `Cargo.toml:79`); introduce no `unsafe`
  (`undocumented_unsafe_blocks = "deny"`, `Cargo.toml:80`). `.unwrap()`/intent
  comments are fine in the leaf tests. `cargo clippy -p sandbox-e2e-live-test
  --all-targets` is a Phase 0 acceptance gate.

## Ground Rules

- **Spec is fixed; live code wins on conflict.** Follow the spec's signatures and
  contracts. Re-confirm a live-code fact only when you touch the module that
  depends on it; if it differs from the spec, match live code and note it. Do not
  re-derive the whole anchor ledger.
- **Prefer less.** Introduce **no** module, field, env var, dependency, or artifact
  the spec does not call for. If the parent design names something only Phase 2–4
  uses (e.g. `report.rs`, `result.json`, `err_kind_at`, `clap`, `run_id`
  derivation), leave it out. Phase-1 skip writes **nothing** — it is a silent early
  return (spec deliberately overrides the parent's "records a skipped result";
  artifact writing is Phase 3, spec L527-534).
- **Do NOT implement Phase 2–4.** `eos-e2e` stays a print-and-exit stub. No
  orchestrator internals, no observability, no spawn mode, no extra deps.
- **Be honest about the unshipped-gateway gate.** The live-green path is external
  and blocked (Open Items #1, `gateway/main.rs:94-146`, `:110-112`); do not claim or
  fake a live-green result.
- **Parallel workers.** Other agents may be editing this repo concurrently. Touch
  only what Phase 0–1 requires; never revert or overwrite changes you did not make;
  keep edits additive and localized. The `Cargo.toml` `members` edit is a single
  additive line at the specified position — do not reflow.

## What "Done" Looks Like

1. **Phase 0 green:** `cargo build -p sandbox-e2e-live-test`, workspace
   `cargo build`, and `cargo clippy -p sandbox-e2e-live-test --all-targets` all
   exit 0; the empty leaf tree builds to empty test binaries.
2. **Phase 1 green (skip-clean):** `cargo test -p sandbox-e2e-live-test` with
   `EOS_E2E_RUN_ROOT` unset exits 0, both leaves skip without panicking, nothing
   written.
3. **Live-green path documented but externally blocked:** the hand-written
   `run-manifest.json` + `EOS_E2E_RUN_ROOT` recipe is in place and correct, but
   green requires an external real-runtime gateway that has not shipped (Honest
   Gate / Open Items #1). It is not expected to pass in this environment.
4. The adversarial Reviewer returns zero defects and zero scope leaks.
