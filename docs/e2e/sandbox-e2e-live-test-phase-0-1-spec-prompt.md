# Prompt: Generate the Phase 0 + Phase 1 Implementation Spec for `sandbox-e2e-live-test`

Use this prompt to produce a single, implementation-ready spec covering **only
Phase 0 (Scaffold the crate)** and **Phase 1 (Harness core + one operation)** of
the parent design.

## Repo

```text
/Users/yifanxu/machine_learning/LoVC/ephemeral-ai/ephemeral-os
```

## Mission

Write `docs/e2e/sandbox-e2e-live-test-phase-0-1-spec.md`: a precise,
build-to-green implementation spec for Phases 0 and 1 of the live E2E runner. The
output is a **spec, not code** — but it must be detailed enough that an engineer
can implement it without re-deriving any design decision: every file, every
public signature, every schema, every verification command, and every load-bearing
`file:line` fact is named. Treat the parent spec as the fixed design and **live
code as the source of truth** (live code wins on any conflict).

## How To Run (multi-agent: author → verifier → finalize)

Run as two cooperating agents, not one. The **Author** drafts the spec; the
**Verifier** adversarially checks it against live code before it is accepted. This
catches stale `file:line` citations and scope-fence leaks — the two failure modes
that broke the parent spec.

```text
1. Orchestrator: bootstrap once (git state; confirm the crate is NOT yet a member
   and the dir is absent; confirm gateway/main.rs Unconfigured* stubs). Share the
   results with both agents.
2. Author agent: read the parent spec + the live-code reading list, resolve every
   Design Question, and WRITE the draft to
   docs/e2e/sandbox-e2e-live-test-phase-0-1-spec.md, including the anchor ledger.
3. Verifier agent (blind to the Author's reasoning, given only the draft + this
   prompt + the reading list): independently re-open every file:line in the draft's
   anchor ledger AND every other citation in the body; flag each as
   confirmed/stale/wrong with the corrected fact. Separately audit the scope fence:
   list anything Phase 2-4 that leaked in, and anything Phase 0-1 needs that is
   missing. Return a defect list (no rewrite).
4. Orchestrator: if the Verifier found L0/L1 defects, hand the draft + defect list
   back to the Author to revise in place, then re-verify. Loop until the Verifier
   returns zero unresolved citation defects and zero scope leaks.
5. Orchestrator: report the final spec path + the Verifier's clean ledger.
```

The Author and Verifier must not share scratch reasoning — the Verifier re-derives
every fact from live code so a wrong citation cannot survive by being asserted
twice. This remains **spec-only**: neither agent implements the crate.

## Source Material (read first, in full)

- Parent design spec: `docs/e2e/sandbox-e2e-live-test-spec.md`. Phases 0 and 1 are
  defined under `## Implementation Phases`; the contracts they depend on are in
  `## Live Checkout Anchors`, `## Crate Shape`, `## Runner Architecture`,
  `## Config Schema`, `## Test Layout and Fixtures`, and the cleanup/preflight
  sections. Do **not** restate the whole parent spec — extract and harden only what
  Phases 0–1 touch.
- Repo orientation: `README.md` (component map + boundary law) and `CLAUDE.md`
  (engineering practice, build/test, conventions).

## Live Code To Verify (do not trust the parent spec's citations — confirm each)

```text
Cargo.toml                                                   # members array; [workspace.dependencies] (deps + line numbers)
crates/sandbox-daemon/tests/unit.rs                          # #[path] + include!(concat!(env!("CARGO_MANIFEST_DIR"),...)) convention
crates/sandbox-runtime/operation/tests/support/mod.rs        # shared-fixture/support-module convention
crates/sandbox-gateway/src/cli/client.rs                     # socket transport: one JSON line in/out
crates/sandbox-gateway/src/cli/output.rs                     # exit codes (0/1/2); error-key discriminator stdout-vs-stderr
crates/sandbox-gateway/src/cli/request_builder.rs            # scope/id resolution; --sandbox-id vs --default-sandbox-id
crates/sandbox-manager/src/operation/impls/management/       # create_sandbox args + response record shape (id, state, daemon)
crates/sandbox-runtime/operation/src/cli_definition/command_operations.rs  # exec_command args + yield response shape
crates/sandbox-manager/src/model.rs                          # SandboxId charset (applies to runtime-assigned id)
crates/sandbox-gateway/src/gateway/main.rs                   # Unconfigured* stubs (the unshipped real-runtime prerequisite)
xtask/src/main.rs                                            # `package` (sandbox-daemon build) — prerequisite enumeration
bin/                                                         # repo-local sandbox tool wrappers (sandbox-cli discovery)
```

Use `rg` to confirm call paths, the actual `create_sandbox` / `exec_command`
argument sets, and response field readers. Every `file:line` in the generated spec
must be one you verified.

## Scope Fence (in vs out)

**IN — Phase 0 (Scaffold):**
- Add `"crates/sandbox-e2e-live-test"` to `Cargo.toml` `members` **and**, in the
  same change, create the crate manifest, `build.rs`, `src/lib.rs`,
  `src/bin/eos-e2e.rs` stub, and the `tests/` tree skeleton, so the workspace still
  builds.
- Acceptance: `cargo build -p sandbox-e2e-live-test` and a workspace-wide
  `cargo build` both succeed; `cargo clippy -p sandbox-e2e-live-test --all-targets`
  passes under the workspace lints.

**IN — Phase 1 (Harness core + one operation):**
- `src/config.rs` — the minimal `RunConfig` + `run-manifest.json` load path needed
  by the fixtures (not the full clap orchestrator surface).
- `src/cli_client.rs` — invoke `sandbox-cli`, capture the call record
  `{ argv, request_json?, response_json, exit_code, stdout, stderr, latency_ms }`,
  parse the single NDJSON response line; locate `error` on stdout-or-stderr.
- `src/fixtures.rs` — `Harness` (lazy, reads `EOS_E2E_RUN_ROOT` → manifest),
  `provision_sandbox(slug, image)` reading the runtime-assigned `/id` from the
  create response, and the RAII `Sandbox` drop guard issuing `destroy_sandbox`.
- `src/gateway.rs` — **attach mode only**: validate/await the `--gateway-socket`
  path; no spawn.
- `src/assertion.rs` — only the helpers the two Phase 1 leaves need (`ok`,
  `field`, and the negative/exit helper if a Phase 1 leaf exercises it).
- `tests/support/mod.rs` — surface the harness to tests; skip-safe entry.
- `build.rs` — generate the per-leaf `#[path]` include list into `$OUT_DIR`.
- Two leaf tests: `tests/manager/lifecycle/create_sandbox.rs` and
  `tests/runtime/command/exec_command.rs`.
- Acceptance: the crate compiles; `cargo test -p sandbox-e2e-live-test` with no
  `EOS_E2E_RUN_ROOT` **skips cleanly** (no panic); with `EOS_E2E_RUN_ROOT` pointing
  at a hand-written `run-manifest.json` for a real-runtime gateway, the two leaves
  run green under `--test-threads=1`.

**OUT (defer to Phases 2–4 — name them as out-of-scope, do not design them):**
- The full operation matrix (M2–M5, R2–R8, routing negatives) and `assertion.rs`
  helpers they need (`err_detail`, `non_decreasing`, `offsets_monotonic`).
- The orchestrator bin internals: preflight, build phase, env export, aggregation
  from `result.json`, `summary.json`/timing, cleanup orchestration. In Phases 0–1
  `eos-e2e` is a **stub** and the run env is set by hand.
- Observability polling, `observability.json`, P1/P2.
- Spawn-mode gateway, label-based cleanup.

## Settled Decisions & Boundary Law (carry from the parent; do not cross)

- Sandbox/image ops go through `sandbox-cli` only — no test-injected
  `SandboxRuntime`, no internal manager/runtime crate dependency on the black-box
  path. No manager-side observability sink. Linux + Docker only.
- Crate shape is fixed: harness lib `src/` + per-op tests
  `tests/[manager|runtime]/<family>/<operation>.rs` + orchestrator bin `eos-e2e`.
- One cross-process env contract: **`EOS_E2E_RUN_ROOT`**; gateway socket, `run_id`,
  and image are read from `{run_root}/run-manifest.json`.
- Sandbox ids are **runtime-assigned** — capture `/id` from the create response and
  round-trip it; never predict or supply the id.
- v1 is **attach-only**: the real-runtime gateway is unshipped
  (`gateway/main.rs:94-146` wires `Unconfigured*` stubs), so Phases 0–1 must be
  fully buildable and skip-safe without it; live verification is gated on an
  externally supplied `--gateway-socket`.

## Design Questions the Spec MUST Resolve (with live-code evidence)

The parent spec leaves these to the implementer; the Phase 0–1 spec must settle
each, citing live code:

1. **`sandbox-cli` binary discovery** — how `cli_client` locates the CLI it
   invokes (repo-local `bin/` wrapper vs `CARGO_BIN_EXE_*` vs an explicit path in
   the manifest). State the exact resolution order and what Phase 1 verification
   requires on `PATH`.
2. **`run-manifest.json` schema** — the exact fields Phase 1 reads (at minimum:
   `schema_version`, gateway socket path, `run_id`, image), since Phase 1
   verification hand-writes this file. Pin it so the orchestrator (Phase 3) can
   later produce a conforming file.
3. **Minimal `RunConfig`** — which fields Phase 1 actually needs vs the full parent
   struct; prove each is load-bearing for fixtures/manifest loading.
4. **Skip-vs-panic mechanism** — how `Harness::get()` returns `Option` (or
   equivalent) and how each leaf early-returns and records a `skipped` result when
   `EOS_E2E_RUN_ROOT` is unset, so a bare `cargo test` on a non-E2E machine does not
   fail. Specify what (if anything) is written when skipping.
5. **`build.rs` include generation** — how it walks `tests/<scope>/**/*.rs`, the
   deterministic `<family>_<operation>` module-slug derivation (collision-free),
   the `$OUT_DIR/<scope>_mods.rs` output, the `rerun-if-changed` triggers, and how
   it stays within the repo's `#[path]` + `include!` convention. Confirm the empty
   Phase 0 tree still builds (generated list may be empty).
6. **`create_sandbox` / `exec_command` exact contracts** — confirmed argument sets
   and response field readers for the two Phase 1 leaves (create: `--image` +
   absolute `--workspace-root`, response `/id`, `/state`, `/daemon/socket_path`;
   exec one-shot: `/status`, `/exit_code`, `command_session_id` absent).
7. **Workspace-root provisioning** — where each test's `--workspace-root` lives
   under `{run_root}` and how it is created/validated (absolute-path check at
   `management/mod.rs`).

## Required Deliverables (the generated spec must contain all of these)

1. **Phase boundary statement** — one paragraph each: what Phase 0 and Phase 1
   deliver, and the explicit out-of-scope list above.
2. **Phase 0 file manifest** — every file created, with its stub contents'
   responsibility (manifest `[package]`/`[lib]`/`[[bin]]`/`[dependencies]` resolved
   to confirmed workspace deps + line numbers; `lib.rs` re-export surface;
   `eos-e2e.rs` stub; `build.rs` skeleton; `tests/` skeleton). The exact
   `Cargo.toml` `members` edit (line position).
3. **Phase 1 module specs** — for `config`, `cli_client`, `fixtures`, `gateway`,
   `assertion`, `tests/support/mod.rs`, and `build.rs`: single-sentence
   responsibility (SRP), public types and **function signatures**, and the
   resolved design-question answers above. No implementation bodies — signatures +
   behavior contracts.
4. **`run-manifest.json` schema** — fielded, with a concrete example for the
   hand-written Phase 1 verification file.
5. **The two leaf tests** — each `#[test]`'s name, fixture calls, invocation, and
   asserted fields (mirroring the parent matrix M1 and R1), shown as code-shaped
   pseudocode like the parent spec's leaf example.
6. **Verification & acceptance** — exact commands and the pass criteria for each
   phase (build, clippy, skip-clean test, green-against-real-gateway test),
   including the hand-written manifest + `EOS_E2E_RUN_ROOT` recipe and a note that
   green requires the unshipped real-runtime gateway (link Open Items #1).
7. **Anchor ledger** — a short table of every `file:line` the spec relies on with a
   `confirmed` verdict (or a correction), so no stale citation enters the spec.
8. **Conventions checklist** — SRP/one-job-per-module; no inline comments in
   production code (doc comments allowed); workspace deps via `dep.workspace =
   true`; `#[path]`/`include!` convention; clippy lints
   (`unwrap_used`/`dbg_macro`/`undocumented_unsafe_blocks`).

## Ground Rules

- Live code wins over the parent spec; cite `file:line` for every load-bearing
  fact and verify it before relying on it.
- Spec only — do **not** implement the crate. Produce the document.
- Prefer less: do not introduce a module, field, env var, or artifact that Phases
  0–1 do not need. If the parent spec names something only Phase 2–4 uses, leave it
  out and say so.
- Keep the two settled-decision fences intact (CLI-only provisioning; attach-only;
  runtime-assigned ids; single `EOS_E2E_RUN_ROOT`).
- Be honest about the unshipped-gateway gate: Phases 0–1 are buildable and
  skip-safe today, but the green live run is blocked on the external real-runtime
  gateway — say so where it matters, do not paper over it.

## Output

Write the result to `docs/e2e/sandbox-e2e-live-test-phase-0-1-spec.md`. Lead with
the phase boundary, then Phase 0, then Phase 1, then verification, then the anchor
ledger. Prefer tables and signature blocks over prose.
