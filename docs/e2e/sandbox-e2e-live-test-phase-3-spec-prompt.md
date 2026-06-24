# Prompt: Generate the Phase 3 Implementation Spec for `sandbox-e2e-live-test`

Use this prompt to produce a single, implementation-ready spec covering **only
Phase 3 (Orchestrator, reproducibility, artifacts, cleanup)** of the parent design.
Unlike Phase 2, Phase 3 is delivered **wholly in Stage 1**: the only stage-aware
line in the entire phase is the orchestrator's default test target, which flips
from `--test manager` (Stage 1, now) to the full suite (Stage 2, later). The spec
you generate must encode that single switch and nothing more of the stage split
(see *Single Stage-Aware Line* below).

## Repo

```text
/Users/yifanxu/machine_learning/LoVC/ephemeral-ai/ephemeral-os
```

## Mission

Write `docs/e2e/sandbox-e2e-live-test-phase-3-spec.md`: a precise, build-to-green
implementation spec for Phase 3 of the live E2E runner. The output is a **spec, not
code** — detailed enough that an engineer implements it without re-deriving any
design decision: every orchestrator phase, every config field and its
flag>env>default precedence, every artifact's field-by-field JSON schema, the exact
`run_id` derivation, the aggregation contract, the cleanup teardown order, and every
load-bearing `file:line`. Treat the parent spec as the fixed design and **live code
as the source of truth** (live code wins on any conflict).

**Phase 0–1 and Phase 2 Stage 1 (manager surface) are already implemented and
live.** Build *on the live skeleton* — `src/{config,cli_client,fixtures,gateway,
assertion,report,lib}.rs`, `tests/support/mod.rs`, `build.rs`,
`tests/{manager,runtime}.rs`, the M1–M5/N1 manager leaves, and the dormant R1 leaf.
Read those files for the starting state; do **not** re-derive earlier phases from
their prose. The spec adds Phase 3's slice — the `eos-e2e` orchestrator, the full
`RunConfig`, `result.json`/`summary.json`/`run-manifest.json` writers, and
`cleanup.rs` — and nothing else.

## Single Stage-Aware Line (the spec MUST encode this — and only this)

Per the phases note (`## Two-stage delivery (runtime-migration gate)`, the
stage-boundary switch and the Phase 3 entry), **Phase 3 ships in full in Stage 1.**
The orchestrator is built completely now; the *one* line that is stage-aware is the
default `cargo test` target:

- **Stage 1 (now):** the orchestrator's default invocation is
  `cargo test -p sandbox-e2e-live-test --test manager -- --test-threads={N}` — the
  manager binary only. A runtime leaf driven against a not-yet-migrated runtime
  would *fail* (`operation_failed`), never skip (the sole skip path is
  `EOS_E2E_RUN_ROOT` unset, `tests/support/mod.rs:7-9`), so Stage 1 keeps the
  runtime binary out of the green target — **not** via a runtime-readiness probe.
- **Stage 2 (later, out of scope here):** drop the `--test manager` restriction so
  the default runs the full suite (manager + runtime). The spec must isolate this so
  the flip is a **one-line change**, name it as the sole Stage 2 touchpoint, and not
  design anything else for Stage 2.

Everything else in Phase 3 — preflight, build phase, manifest, env export,
aggregation, `summary.json`, cleanup, `--rerun-failed-from` — is Stage 1 and built
now. State this explicitly; do not partition Phase 3 deliverables into two stages as
Phase 2 did.

## How To Run (multi-agent: author → verifier → finalize)

Run as two cooperating agents, not one. The **Author** drafts the spec; the
**Verifier** adversarially checks it against live code before it is accepted. This
catches stale `file:line` citations, scope leaks (Phase 4 observability creeping
in), and **artifact-contract drift** — a `run-manifest.json` field set the live
test-side reader cannot parse, or a `summary.json` gate that secretly parses libtest
stdout.

```text
1. Orchestrator: bootstrap once (git state; confirm the crate is a live member; the
   harness skeleton + M1–M5/N1 leaves exist; eos-e2e.rs is still the stub; config.rs
   still holds the test-side manifest reader under the name RunConfig; report.rs has
   only write_exchange; there is no cleanup.rs; gateway/main.rs Unconfigured* stubs
   still ship). Share results with both agents.
2. Author agent: read the parent spec + phases note + the live-code reading list,
   resolve every Design Question, and WRITE the draft to
   docs/e2e/sandbox-e2e-live-test-phase-3-spec.md, including the anchor ledger, the
   orchestrator phase pipeline, the field-by-field artifact schemas, and the single
   stage-aware line called out as the sole Stage 2 touchpoint.
3. Verifier agent (blind to the Author's reasoning, given only the draft + this
   prompt + the reading list): independently re-open every file:line in the anchor
   ledger AND every other citation; flag confirmed/stale/wrong with the corrected
   fact. Then audit three fences: (a) scope — anything Phase 4 (observability
   polling, observability.json, P1/P2) that leaked in; (b) boundary — any
   internal-crate dep or non-black-box path the orchestrator reaches for; (c)
   contract — run-manifest.json stays readable by the live test-side reader,
   summary.json's pass/fail gate is the cargo test exit code (NOT libtest stdout),
   and the default test target is isolated to one flippable line. Return a defect
   list (no rewrite).
4. Orchestrator: on any L0/L1 defect, hand draft + defects back to the Author to
   revise in place, then re-verify. Loop until zero citation defects, zero scope
   leaks, zero contract drift.
5. Orchestrator: report the final spec path + the Verifier's clean ledger.
```

The Author and Verifier must not share scratch reasoning — the Verifier re-derives
every fact from live code so a wrong citation cannot survive by being asserted
twice. This remains **spec-only**: neither agent implements the crate.

## Source Material (read first, in full)

- Parent design spec: `docs/e2e/sandbox-e2e-live-test-spec.md`. Phase 3 is defined
  under `## Implementation Phases`; the contract it realizes is `## Runner
  Architecture` (the orchestrator data-flow diagram), `## Config Schema`, `##
  Reproducibility, Artifacts, and Cleanup` (the artifact tree, `run_id` scheme,
  `summary.json`/`timing`/`cleanup` shapes, teardown order), `## Preflight` (the
  ordered checks + the daemon-binary/gateway-config prerequisite enumeration), and
  the `### Two-stage delivery during the runtime migration` overlay. Do **not**
  restate the whole parent spec — extract and harden only what Phase 3 touches.
- Phases note: `docs/e2e/sandbox-e2e-live-test-phases-note.md` → the Phase 3 entry,
  the *Single stage-boundary switch* paragraph, and the Phase 3 row of the stage map.
- Phase 2 implementation prompt: `docs/e2e/sandbox-e2e-live-test-phase-2-implementation-prompt.md`
  — the live starting state Phase 3 extends (exchange.jsonl already shipped; result.json
  deliberately deferred to Phase 3).
- Repo orientation: `README.md` (component map + boundary law) and `CLAUDE.md`
  (engineering practice, build/test, conventions).

## Live Code To Verify (do not trust the parent spec's citations — confirm each)

**Live skeleton Phase 3 extends (live wins over prose):**

```text
crates/sandbox-e2e-live-test/src/bin/eos-e2e.rs    # STUB: prints a message, exits 2 (:1-9) — Phase 3 replaces it
crates/sandbox-e2e-live-test/src/config.rs         # test-side reader NAMED RunConfig::from_env + Manifest{schema_version,gateway_socket,run_id,image} (:14-56); fixtures.rs:33 consumes it — Phase 3 must NOT break this
crates/sandbox-e2e-live-test/src/report.rs         # only write_exchange + EXCHANGE_SCHEMA_VERSION today (:8-30) — Phase 3 adds result/summary/manifest writers
crates/sandbox-e2e-live-test/src/fixtures.rs       # Sandbox::drop flushes exchange.jsonl + destroy_sandbox (:126-136); provision returns (Sandbox,CallRecord), starts no timer; run_root() exists (:57-60)
crates/sandbox-e2e-live-test/src/gateway.rs        # await_ready: attach-only socket poll, 5s (:6-26) — reuse for Phase B readiness
crates/sandbox-e2e-live-test/src/cli_client.rs     # CallRecord{argv,request,response,exit_code,stdout,stderr,latency_ms}; carrier = exit==0 ? stdout : stderr
crates/sandbox-e2e-live-test/src/lib.rs            # pub mod {assertion,cli_client,config,fixtures,gateway,report}; re-exports — Phase 3 adds cleanup
crates/sandbox-e2e-live-test/Cargo.toml            # deps today: anyhow, serde, serde_json ONLY — Phase 3 adds clap, sha2, time (+ tokio/tokio-util iff the orchestrator is async)
crates/sandbox-e2e-live-test/build.rs              # unchanged; confirm it needs no Phase 3 edit
crates/sandbox-e2e-live-test/tests/support/mod.rs  # the ONLY skip path: harness()->Option (:7-9)
```

**Preflight / prerequisite contracts:**

```text
crates/sandbox-gateway/src/gateway/main.rs         # Unconfigured*/UnconfiguredDaemonInstaller stubs => "sandbox runtime is not configured" (cited :94-146 — confirm) ; the runtime-probe trigger
crates/sandbox-gateway/src/cli/output.rs           # exit codes 0/1/2; error carrier rendering — the probe reads the carried error
crates/sandbox-manager/src/daemon_install.rs       # SandboxDaemonInstaller consumes daemon exe + config YAML + runtime-root (:33-64); per-sandbox paths {runtime_root}/{id}/runtime.{sock,pid} (:52-57)
xtask/src/main.rs                                  # `cargo run -p xtask -- package` builds the sandbox-daemon binary (cited :764 — confirm) — prerequisite enumeration only
```

**Reproducibility / cleanup contracts:**

```text
crates/sandbox-manager/src/model.rs                # SandboxId charset [A-Za-z0-9._-], non-empty (cited :10-22) — run_id must match this charset
crates/sandbox-manager/src/runtime.rs              # CreateSandboxRequest carries NO label field (cited :6-14) => no Docker run-label backstop; cleanup keys on captured ids + path namespacing only
```

Confirm every path with `rg` — actual struct fields, the exact "runtime is not
configured" string, the daemon-installer inputs, and the SandboxId charset — before
relying on any line number.

## Scope Fence (in vs out)

**IN — Phase 3, Stage 1 (built now):**

- **`src/bin/eos-e2e.rs`** (replace the stub) — the full orchestrator pipeline:
  RunConfig assembly → preflight (incl. a standalone `eos-e2e preflight` subcommand)
  → write `run-manifest.json` → Phase A build (skipped attach-only) → Phase B
  (`gateway::await_ready`, export `EOS_E2E_RUN_ROOT`, run `cargo test --test
  manager`) → aggregate from globbed `reports/*/result.json` → cleanup → exit code.
- **`src/config.rs`** — promote to the full orchestrator `RunConfig` + clap `Args`
  (all fields, enums `TestSelection`/`CleanupPolicy`/`BuildSource`, flag>env>default
  precedence, `run_id` derivation + charset validation). Resolve the name collision
  with the live test-side reader (Design Question 1).
- **`src/report.rs`** — add `TestOutcome`/`result.json`, `summary.json` (with
  `timing` + `cleanup` sub-objects, `schema_version` on every artifact), and the
  `run-manifest.json` writer. Keep `write_exchange`.
- **`src/fixtures.rs`** — emit `result.json` per executed test (status via
  `std::thread::panicking()`, per-test `duration_ms`, `test_name` from the slug).
- **`src/cleanup.rs`** (NEW) — RAII run guard, survivor-id sweep (ids read from
  `reports/*/` dir names), detach-only, `remove_dir_all(run_root)` gated by
  `CleanupPolicy`; `--clean-run {run_id}`; `summary.cleanup` record.
- **`--rerun-failed-from {summary.json}`** — `TestSelection::RerunFailedFrom` →
  parse `failed_tests[]` → libtest name filters.
- `lib.rs` delta (`pub mod cleanup;`), `Cargo.toml` deps (`clap`, `sha2`, `time`).

**OUT (defer; name as out-of-scope, do not design):**

- **Phase 4** entirely: `get_observability_tree` polling, `observability.json`,
  P1 (cgroup) / P2 (queue-wait) consumption. Phase 3 writes no observability artifact.
- **Stage 2 work**: authoring runtime leaves R2–R8/N2, the runtime assertion helpers,
  and flipping the default test target to the full suite. Phase 3 specifies the flip
  as a one-line future change only.
- **Spawn-mode gateway** and `package-fast` binary discovery (Open Items #1): Phase 3
  is attach-only; build phase is skipped when `--gateway-socket` is given.
- **Docker run-label orphan reaper** (Open Items #2): no label backstop; a SIGKILL
  mid-run can leak containers — state this honestly as the known limit.
- **`build.rs` changes** — confirm none are needed (Phase 3 adds no test leaf).

## Settled Decisions & Boundary Law (carry from the parent; do not cross)

- Black-box only: the orchestrator drives the system via `sandbox-cli` over the
  gateway socket and reads only artifacts under `{run_root}`. No internal-crate dep,
  no `*_for_test` reader, no test-injected runtime. Linux + Docker only; off-Linux /
  no-Docker exits `2` at preflight.
- Sandbox ids are **runtime-assigned**; cleanup destroys exactly the ids captured
  this run (read from `reports/*/` dir names) — never predicts or pattern-matches an
  id, and `remove_dir_all(run_root)` can never reach a sibling run.
- One cross-process env contract: **`EOS_E2E_RUN_ROOT`**; the gateway socket,
  `run_id`, and image travel in `{run_root}/run-manifest.json`, which the live
  test-side reader already parses — its field set is load-bearing.
- Pass/fail gate is the **`cargo test` process exit code**; aggregation reads each
  test's `result.json` (a missing one is `errored`). **No libtest-stdout parsing**
  (nightly-only for JSON, brittle to renames/`#[ignore]`).
- v1 is **attach-only**; the real-runtime gateway is unshipped
  (`gateway/main.rs` Unconfigured stubs). Phase 3 is buildable and skip-safe today;
  a live-green run is gated on an externally supplied `--gateway-socket` that
  provisions/destroys sandboxes (the manager surface — Stage 1 needs no runtime op).

## Design Questions the Spec MUST Resolve (with live-code evidence)

1. **`RunConfig` name collision.** The live `config.rs` already exports
   `RunConfig::from_env` — the *test-side manifest reader* consumed at
   `fixtures.rs:33`. The parent spec's `RunConfig` (`## Config Schema`) is the
   *orchestrator-side* config (a different concern). Resolve the split: rename the
   reader (e.g. `ManifestConfig`) and define the orchestrator `RunConfig` so the
   test side keeps compiling unchanged. State exactly which symbol each consumer uses.
2. **`result.json` ownership, status, keying, skip.** Tests today write only
   `exchange.jsonl` on `Sandbox::drop`; aggregation needs `result.json` per test.
   Resolve: who writes it (candidate: fold into the existing `Sandbox::drop` beside
   the exchange flush), how status is determined (`std::thread::panicking()` ⇒
   `failed`/`passed`), where `duration_ms` starts (an `Instant` at provision),
   how `test_name` is derived (the provision slug vs the `#[test]` fn name — pin
   one), and how `assertions{total,failed}` is counted (a thread-local bumped by
   `assertion::` helpers, or a best-effort `{0,0}` — choose, justify under *prefer
   less*). Resolve the **skip path**: under the orchestrator `EOS_E2E_RUN_ROOT` is
   always set so nothing skips; state whether the env-unset bare-`cargo test` path
   still writes a `skipped` result.json (it has no `run_root` to write into) or stays
   a bare `return`, and what `counts.skipped` therefore means.
3. **`summary.json` aggregation contract.** Field-by-field: `{ schema_version,
   run_id, git_head, started_at, finished_at, max_parallel, status, counts{...},
   tests[]{...}, failed_tests[], artifacts_root, timing{build,runner,per_test},
   cleanup{...} }`. Pin how `tests[]` is built (glob `reports/*/result.json`; missing
   ⇒ `errored`), how the run `status` combines the cargo-test exit code with the
   per-test statuses, and the exact `tests[].name` shape
   (`scope::family::operation::case::fn`) and how it is reconstructed from the report
   dir + result.json.
4. **`run_id` determinism.** The exact scheme: `--run-id` verbatim (validated against
   the SandboxId charset `[A-Za-z0-9._-]`), else
   `r{ts}-{sha256(git_HEAD ‖ test_manifest_hash ‖ EOS_E2E_RUN_SALT)[..8]}` via
   `sha2`. Define `ts` (and its `EOS_E2E_RUN_CLOCK` pin for byte-stable reruns),
   define `test_manifest_hash` (hash of *what*, precisely), and state why `uuid` is
   not used for `run_id` (it is v4-random in-tree).
5. **`run-manifest.json` field compatibility.** The orchestrator writes the manifest
   the live test-side reader parses. Reconcile the full written superset (parent spec:
   `schema_version, git_head, config, gateway_socket, run_id, image, clock`) with the
   minimal read set (`Manifest{schema_version,gateway_socket,run_id,image}` at
   `config.rs:21-27`). Pin `schema_version` (the reader rejects mismatches at
   `config.rs:43-48`) and confirm the writer emits exactly what the reader needs.
6. **Preflight order, the runtime probe, and exact messages.** The ordered checks
   (Linux → `docker version` → `docker image inspect {image}` → real-runtime probe),
   the *specific cheap manager call* used as the probe, the substring it matches
   ("runtime is not configured"), and each `exit 2` message verbatim. Specify the
   `eos-e2e preflight` standalone subcommand. Enumerate (do not provision) the
   daemon-binary + config-YAML + runtime-root prerequisites
   (`daemon_install.rs:33-64`, `xtask package`).
7. **Cleanup ownership, survivor sweep, teardown order.** The RAII run guard owns
   `run_root`; survivors are the ids in `reports/*/` dir names (ids are minted inside
   test processes, not the orchestrator — the per-test `Sandbox::drop` reaps the
   happy path, the orchestrator sweeps the rest). Teardown order: destroy captured
   ids → **detach only** (never stop a gateway the runner did not start) →
   `remove_dir_all(run_root)` gated by `CleanupPolicy` (default `OnSuccess`;
   `--keep-artifacts` forces keep). Specify `--clean-run {run_id}` re-cleanup and the
   `summary.cleanup` record shape. Note the no-label, no-orphan-reaper limit.
8. **`timing` capture split.** Build is untimed by the runner (own `Instant`s →
   `timing.build.*`); the runner clock starts only after binaries exist and the
   gateway socket is reachable (`timing.runner.*`); `BuildSource::Prebuilt` /
   `--gateway-socket` set `build.*_ms = 0`. Define each sub-field and its source
   (wall-clock around the `cargo test` process vs per-test `result.json` durations
   for `per_test[]`).
9. **`TestSelection` → `cargo test` mapping.** `All` (default `--test manager` in
   Stage 1), `Names(Vec<String>)` (libtest name filters `family_operation_case`),
   `RerunFailedFrom(PathBuf)` (parse `failed_tests[]` from a prior `summary.json`).
   Pin how `max_parallel` becomes `-- --test-threads={N}` and the
   `--max-parallel > EOS_E2E_MAX_PARALLEL > available_parallelism().min(8)`
   precedence.
10. **The single stage-aware line.** Show the exact place the default target lives
    and how it is isolated so Stage 2's flip (drop `--test manager` ⇒ full suite) is
    one edit. Confirm Stage 1 is provably green with zero runtime calls and that no
    runtime-readiness skip guard is introduced.
11. **Async vs sync orchestrator.** The parent spec lists `tokio`/`tokio-util` but
    notes the orchestrator may stay sync (it shells out to `cargo test` and
    `sandbox-cli` and globs files; no fan-out). Decide and justify — add `tokio` only
    if actually used (*prefer less*).
12. **`build.rs` unchanged.** Confirm Phase 3 adds no test leaf, so the generated
    include list and slug derivation need no edit.

## Required Deliverables (the generated spec must contain all of these)

1. **Phase boundary + single-stage statement** — one paragraph: what Phase 3
   delivers over the live skeleton, the explicit statement that Phase 3 is wholly
   Stage 1 with the default test target as the sole Stage 2 touchpoint, and the
   Phase 4 / Open-Items out-of-scope list.
2. **Resulting file/folder structure** — the `src/` deltas after Phase 3, each
   edited file tagged `△` and each new file `←NEW`, plus the `Cargo.toml` dep
   additions; the `tests/` tree is unchanged (state so).
3. **Orchestrator pipeline spec** — the `eos-e2e` data flow as an ordered phase
   list (Preflight → Manifest → Phase A Build → Phase B Run → Aggregate → Cleanup →
   ExitCode), each step with its inputs, outputs, side effects, and exit-code
   semantics; plus the `preflight` and `--clean-run` subcommands.
4. **Config schema** — the full `RunConfig` + clap `Args` table (field, flag, env,
   default, validation), the three enums, the `run_id` derivation, and the
   `ManifestConfig` rename note.
5. **Artifact schemas** — `run-manifest.json`, `result.json`, and `summary.json`
   (with `timing` and `cleanup` sub-objects) specified field-by-field, each carrying
   `schema_version`; note `run-manifest.json`'s read/write compatibility with the
   live reader.
6. **`result.json` emission spec** — ownership, status determination, `test_name`,
   `duration_ms`, `assertions` counting, and the resolved skip-path behavior.
7. **Cleanup spec** — the RAII guard, survivor-id sweep, teardown order,
   `CleanupPolicy` semantics, `--clean-run`, `--rerun-failed-from`, the
   `summary.cleanup` shape, and the no-orphan-reaper limit.
8. **Verification & acceptance** — exact commands and pass criteria: bare
   `cargo test` skips clean; `cargo build`/`clippy` exit 0; `eos-e2e preflight` exits
   `2` with the precise missing item off-Linux / no-Docker / no-image /
   unconfigured-gateway; a green attach run produces
   `{run_root}/{run-manifest.json,summary.json,reports/{id}/{exchange.jsonl,result.json}}`
   and cleans up per policy; `--keep-artifacts` and `--rerun-failed-from` behave;
   plus the honest gate note (Stage 1 green needs an external real-runtime gateway,
   Open Items #1).
9. **Anchor ledger** — a table of every `file:line` the spec relies on with a
   `confirmed`/`corrected` verdict (especially the cited-but-unconfirmed
   `gateway/main.rs:94-146`, `xtask/src/main.rs:764`, `model.rs:10-22`,
   `runtime.rs:6-14`).
10. **Conventions checklist** — SRP/one-job-per-unit; *prefer less* (no field/struct
    /method an existing one already carries); no inline comments in production code
    (doc comments allowed); workspace deps via `dep.workspace = true`; clippy lints
    (`unwrap_used`/`dbg_macro`/`undocumented_unsafe_blocks`).

## Ground Rules

- Live code wins over the parent spec; cite `file:line` for every load-bearing fact
  and verify it before relying on it. The known live-vs-parent deltas (`config.rs`'s
  reader is named `RunConfig` today and must be renamed; `report.rs` has only
  `write_exchange`; `eos-e2e.rs` is a stub; `Cargo.toml` lacks `clap`/`sha2`/`time`;
  the per-test `result.json` does not exist yet) must be honored, not the prose.
- **Spec only** — do not implement the crate; produce the document.
- **Prefer less** — add no module, field, flag, or artifact Phase 3 does not need.
  Do not introduce observability plumbing (Phase 4) or Stage 2 runtime leaves. Reuse
  the live `gateway::await_ready`, `report::write_exchange`, and
  `CliClient::{manager,runtime}`.
- **Hold the boundary** — the orchestrator drives only `sandbox-cli` and reads only
  `{run_root}` artifacts; the pass/fail gate is the cargo-test exit code, never
  libtest stdout; the default test target is the one isolated, flippable stage line.
- Be honest about the gates and limits: Stage 1 is green only against an external
  real-runtime gateway; there is no orphan-reaping on hard kill. Say so where it
  matters; do not paper over it.

## Output

Write the result to `docs/e2e/sandbox-e2e-live-test-phase-3-spec.md`. Lead with the
phase boundary + single-stage statement, then the resulting tree, then the
orchestrator pipeline, then the config schema, then the field-by-field artifact
schemas, then the `result.json` / cleanup specs, then verification, then the anchor
ledger. Prefer tables and signature blocks over prose.

> This generator follows the Phase 2 spec-prompt's author→verifier discipline but
> swaps the load-bearing calls: Phase 3 has no manager/runtime stage split (it is
> wholly Stage 1 bar one flippable line), so its distinctive decisions are the
> `RunConfig` rename, the `result.json` emission contract, and the
> `run-manifest.json` read/write compatibility — verify those hardest.
