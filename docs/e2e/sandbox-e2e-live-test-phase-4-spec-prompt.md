# Prompt: Produce the Phase 4 Stage 1 Spec File for `sandbox-e2e-live-test`

Use this prompt to make another agent produce the spec file for **only Phase 4
Stage 1 (observability snapshot monitoring)** of the parent design. Phase 4 is
split by the runtime migration: Stage 1 adds the manager-visible snapshot poller
and `observability.json` artifacts now; Stage 2 later consumes runtime command
traces and P2 queue-wait once the migrated runtime surface is green.

## Repo

```text
/Users/yifanxu/machine_learning/LoVC/ephemeral-ai/ephemeral-os
```

## Mission

Produce `docs/e2e/sandbox-e2e-live-test-phase-4-spec.md`: a precise,
build-to-green spec for the Stage 1 slice of Phase 4. The output is a **spec file,
not code** - detailed enough that an engineer implements it without re-deriving
any design decision: when the orchestrator polls, which public CLI call it uses,
how per-sandbox `observability.json` is keyed and merged with existing report
dirs, how P1 cgroup CPU/memory fields are detected if present, how absence of
optional observability fields is reported without failing the run, and every
load-bearing `file:line`.

Treat the parent spec as the fixed design and **live code as the source of truth**
(live code wins on any conflict). Phase 3 is assumed implemented in live code:
`eos-e2e` owns the run pipeline, `RunConfig`, `summary.json`, `result.json`,
`run-manifest.json`, run-scoped cleanup, and the manager-only Stage 1 default
target. Build on that live state; do **not** redesign the orchestrator.

## Stage Fence (the spec MUST encode this)

**Stage 1 - built now**

- Add observability snapshot capture through the public manager operation:
  `sandbox-cli manager get_observability_tree --include-recent-traces 1
  --trace-limit 100 --resource-window-ms 60000`.
- Write one per-sandbox `{run_root}/reports/{sandbox_id}/observability.json`
  containing the latest public tree node plus bounded recent-trace summaries and
  resource samples for that sandbox.
- Consume P1 **only if it is already surfaced in the public tree** under resource
  samples: cgroup CPU/memory fields such as `cpu_usage_usec`,
  `memory_current_bytes`, `memory_max_bytes`, and `memory_max_unlimited`.
- Keep absence of P1 fields diagnostic, not fatal. Missing P1 lowers resolution;
  it must not fail an otherwise green manager run.
- Keep the orchestrator default target manager-only. Phase 4 Stage 1 still drives
  zero runtime CLI operations.

**Stage 2 - out of scope here**

- P2 namespace queue-wait timing (`enqueued_at_unix_ms`, `running_at_unix_ms`,
  derived `queue_wait_ms`) and runtime command traces.
- R1 green proof, R2-R8/N2 runtime leaves, runtime assertion helpers, and flipping
  the orchestrator default to the full suite.
- Any internal SQLite or `*_for_test` observability reader.

The split mirrors `docs/e2e/sandbox-e2e-live-test-spec.md` under
`### Two-stage delivery during the runtime migration` and
`docs/e2e/sandbox-e2e-live-test-phases-note.md` under `## Phase 4 -
Observability monitoring`.

## How To Run (multi-agent: author -> verifier -> finalize)

Run as two cooperating agents, not one. The **Author** drafts the spec; the
**Verifier** adversarially checks it against live code before it is accepted. This
catches stale `file:line` citations, scope leaks into Stage 2, and black-box
boundary violations.

```text
1. Orchestrator: bootstrap once (git state; confirm Phase 3 live code exists:
   eos-e2e pipeline, report.rs summary/result writers, fixtures.rs result writer,
   cleanup.rs, manager-only STAGE1_DEFAULT_TARGET; confirm there is no existing
   observability.json writer). Share results with both agents.
2. Author agent: read the parent spec + phases note + the live-code reading list,
   resolve every Design Question, and WRITE the draft to
   docs/e2e/sandbox-e2e-live-test-phase-4-spec.md, including the anchor ledger,
   Stage 1 file/folder deltas, JSON schemas, polling contract, and acceptance
   commands.
3. Verifier agent (blind to the Author's reasoning, given only the draft + this
   prompt + the reading list): independently re-open every file:line in the anchor
   ledger AND every other citation; flag confirmed/stale/wrong with corrected
   facts. Then audit four fences: (a) stage - no P2/runtime command trace work;
   (b) boundary - only public `sandbox-cli`/run-root artifacts, no internal
   observability store; (c) contract - `observability.json` is additive and does
   not break Phase 3 summary/result cleanup; (d) failure semantics - missing P1
   never fails the run. Return a defect list (no rewrite).
4. Orchestrator: on any L0/L1 defect, hand draft + defects back to the Author to
   revise in place, then re-verify. Loop until zero citation defects, zero scope
   leaks, zero contract drift.
5. Orchestrator: report the final spec path + the Verifier's clean ledger.
```

The Author and Verifier must not share scratch reasoning. The Verifier re-derives
every fact from live code so a wrong citation cannot survive by being asserted
twice. This remains **spec-only**: neither agent implements the crate.

## Source Material (read first, in full)

- Parent design spec: `docs/e2e/sandbox-e2e-live-test-spec.md`. Phase 4 is defined
  under `## Implementation Phases`; the observability contract lives under the
  observability/performance section that describes `get_observability_tree`,
  `observability.json`, P1, P2, and the black-box-only consumption rule.
- Phases note: `docs/e2e/sandbox-e2e-live-test-phases-note.md` -> `## Two-stage
  delivery (runtime-migration gate)`, the Stage map per phase, and `## Phase 4 -
  Observability monitoring`.
- Phase 3 spec:
  `docs/e2e/sandbox-e2e-live-test-phase-3-spec.md` - especially the out-of-scope
  Phase 4 callouts, `summary.json`/`result.json` artifacts, cleanup order, and
  the manager-only `STAGE1_DEFAULT_TARGET`.
- Phase 3 implementation prompt:
  `docs/e2e/sandbox-e2e-live-test-phase-3-implementation-prompt.md` - the
  implemented starting point Phase 4 extends.
- Repo orientation: `README.md` (component map + boundary law) and `CLAUDE.md`
  (engineering practice, build/test, conventions).

## Live Code To Verify (do not trust the parent spec's citations)

**Live Phase 3 runner state (starting point):**

```text
crates/sandbox-e2e-live-test/src/bin/eos-e2e.rs      # run pipeline, manager-only STAGE1_DEFAULT_TARGET, cargo test child, summary write, cleanup
crates/sandbox-e2e-live-test/src/report.rs           # write_exchange, write_result, write_run_manifest, write_summary, build_tests, Timing/Summary DTOs
crates/sandbox-e2e-live-test/src/fixtures.rs         # Harness::get/provision_sandbox; Sandbox::drop writes exchange.jsonl + result.json + destroy_sandbox
crates/sandbox-e2e-live-test/src/cleanup.rs          # RunGuard teardown, survivor sweep, run_root cleanup policy
crates/sandbox-e2e-live-test/src/config.rs           # RunConfig, Args, ManifestConfig, max_parallel/tests/cleanup/build fields
crates/sandbox-e2e-live-test/src/cli_client.rs       # CliClient::manager, CallRecord latency/response carrier
crates/sandbox-e2e-live-test/tests/support/mod.rs    # sole skip path via Harness::get()
crates/sandbox-e2e-live-test/tests/manager/observability/get_observability_tree/returns_tree.rs
```

Confirm whether Phase 3 already modified any shape named by this prompt. Live code
wins over all prose.

**Public observability operation and tree shape:**

```text
crates/sandbox-manager/src/operation/impls/management/get_observability_tree.rs
  # CLI args: --sandbox-id, --include-recent-traces, --trace-limit, --resource-window-ms
  # response shape: { sandboxes: [...] }; per-node sandbox_id, lifecycle_state,
  # availability, errors, daemon, resources, workspaces, recent_traces
```

**Daemon observability projection and optional P1 fields:**

```text
crates/sandbox-daemon/src/observability/service.rs
  # observability_snapshot_response; snapshot_value; resource sample projection;
  # read options and trace/resource limits
crates/sandbox-daemon/src/observability/cgroup.rs
  # CgroupSample fields and current availability behavior
crates/sandbox-observability/src/store.rs
  # resource_samples schema; ObservabilityResourceSampleRow fields;
  # *_for_test readers are off-limits to the runner
```

**Stage 2/P2 references to keep out of this spec except as deferred work:**

```text
crates/sandbox-daemon/src/observability/namespace_execution.rs
crates/sandbox-runtime/operation/tests/observability_snapshot.rs
crates/sandbox-runtime/operation/src/**/*
```

Use `rg` and `nl -ba` to confirm exact line numbers for every citation in the
final spec.

## Scope Fence (in vs out)

**IN - Phase 4 Stage 1:**

- `src/report.rs`: add an `observability.json` writer and any DTOs needed for a
  public-tree snapshot artifact. Keep `write_exchange`, `write_result`,
  `write_summary`, and `build_tests` behavior stable.
- `src/bin/eos-e2e.rs`: integrate snapshot capture into the existing run pipeline
  without changing pass/fail gating. Specify whether polling runs before tests,
  during tests, after tests, or in a bounded side thread, and how it shuts down
  before cleanup removes `run_root`.
- `src/fixtures.rs` or another minimal hook: if the poller needs to discover
  sandbox ids while tests are running, specify how ids are made visible without a
  new central registry that fights Phase 3's per-sandbox report dirs.
- Artifact schema:
  `{run_root}/reports/{sandbox_id}/observability.json` with `schema_version`,
  `sandbox_id`, `captured_at` or `sampled_at`, `source_call`, `latest_node`,
  `resource_summary`, `p1`, `recent_traces`, and `warnings` (adjust names only
  after verifying live response shape).
- Optional summary additions, if justified: diagnostic counts such as
  `observability.snapshots_written`, `observability.p1_available`,
  `observability.warnings`. Do not make these pass/fail gates.
- Error policy: polling failures and missing/partial nodes are warnings recorded
  in `observability.json` or `summary.json`, not run failures, unless the public CLI
  call itself exposes a hard configuration problem already covered by preflight.

**OUT - defer or forbid:**

- P2 queue-wait timing and runtime command traces.
- Runtime leaves R2-R8/N2 and runtime assertion helpers.
- Changing `STAGE1_DEFAULT_TARGET` or adding runtime-readiness skip guards.
- Reading `sandbox-observability` SQLite files, calling `*_for_test` helpers, or
  adding direct internal-crate dependencies for observation.
- Adding a manager-side observability sink or a second classification axis.
- Docker label orphan reaper, spawn-mode gateway, or unrelated Phase 3 cleanup
  redesign.

## Settled Decisions & Boundary Law (carry from the parent; do not cross)

- Black-box only: observability is collected through `sandbox-cli manager
  get_observability_tree` over the gateway socket. The runner reads only its own
  `{run_root}` artifacts and the public CLI response.
- Stage 1 drives zero runtime CLI operations. Manager-only `cargo test --test
  manager` remains the green target.
- `observability.json` is additive. It must not change `result.json`,
  `exchange.jsonl`, or cleanup semantics except where the spec explicitly says the
  orchestrator writes the new artifact before cleanup.
- Pass/fail remains the Phase 3 gate: cargo-test exit code plus per-test
  `result.json` statuses. Observability affects diagnostics, not correctness.
- P1 is optional and opportunistic. If cgroup fields are absent or marked
  unavailable in the public tree, record that as a warning; do not fail.
- P2 is Stage 2. Do not derive queue wait from missing or speculative fields.

## Design Questions the Spec MUST Resolve (with live-code evidence)

1. **Polling owner and lifecycle.** Decide whether `eos-e2e` runs a bounded polling
   loop concurrently with `cargo test`, takes snapshots before/after the cargo
   child, or both. Pin the interval, stop condition, timeout per CLI call, and how
   the poller exits before cleanup. Prefer a simple synchronous or scoped-thread
   design; add dependencies only if live code truly needs them.
2. **Sandbox-id discovery while tests run.** Phase 3 result dirs are created by
   `Sandbox::drop`, which may be too late for "during the run" snapshots. Resolve
   whether Phase 4 writes an early marker when a sandbox is provisioned, polls the
   whole manager tree and keys by returned `sandbox_id`, or uses another minimal
   black-box-safe mechanism.
3. **`observability.json` ownership and schema.** Define exactly who writes it,
   where, how many snapshots it stores (latest only vs bounded history), how it
   includes the source call metadata, how it records warnings, and how it handles a
   sandbox id seen by the tree but not yet represented in `reports/{id}`.
4. **P1 detection and reporting.** Define what counts as P1 "available" from the
   public tree: `resources.latest` or `resources.history` entries with cgroup
   fields such as `cpu_usage_usec` and `memory_current_bytes`. Pin how null,
   missing, and `cgroup_available=false` are represented.
5. **Summary integration.** Decide whether Phase 4 adds a top-level
   `summary.observability` diagnostic object. If yes, specify fields and ensure the
   Phase 3 aggregation contract remains stable. If no, state that per-sandbox
   `observability.json` is the only Phase 4 Stage 1 artifact.
6. **Failure semantics.** Classify poll CLI errors, unavailable nodes, malformed
   tree shape, missing P1 fields, and write failures. State which are warnings and
   whether any can affect process exit. The default should be "diagnostic only" and
   exceptions need live-code justification.
7. **Cleanup interaction.** Phase 3 removes `run_root` on success by default. Decide
   how acceptance proves `observability.json` exists without weakening cleanup:
   likely use `--keep-artifacts` for artifact inspection and keep default cleanup
   unchanged.
8. **No test-tree churn unless necessary.** Decide whether Phase 4 Stage 1 needs new
   tests. If it adds unit tests for DTO/writer behavior, keep them narrow and avoid
   live Docker in unit tests. Do not add runtime leaves.

## Required Spec Shape

The generated spec should include, in this order:

1. Phase boundary + Stage 1 statement.
2. Resulting file/folder structure with `[EDITED]` and `[NEW]`.
3. Orchestrator polling pipeline and lifecycle.
4. Artifact schema for `observability.json` and any `summary` addition.
5. P1 detection/reporting rules and Stage 2 P2 deferral.
6. Failure semantics and cleanup interaction.
7. Implementation steps mapped to files.
8. Anchor ledger with every live citation marked `confirmed` or `corrected`.
9. Verification and acceptance commands.
10. Conventions checklist: SRP, prefer less, black-box only, no Stage 2 leakage.

## Acceptance the Spec Must Require

- `cargo build -p sandbox-e2e-live-test` exits 0.
- `cargo clippy -p sandbox-e2e-live-test --all-targets` exits 0.
- `cargo fmt --check` exits 0.
- Bare `cargo test -p sandbox-e2e-live-test` with `EOS_E2E_RUN_ROOT` unset still
  skips cleanly and writes no run artifacts.
- A manager-only Stage 1 attach run with `--keep-artifacts` writes
  `{run_root}/reports/{sandbox_id}/observability.json` for each observed sandbox,
  without changing the Phase 3 pass/fail gate.
- The artifact records public-tree warnings for missing P1 fields instead of
  failing.
- The default cleanup policy remains unchanged; artifact inspection uses
  `--keep-artifacts`.
- `rg` confirms no direct use of `sandbox-observability` store readers,
  `*_for_test`, P2 queue-wait fields as a requirement, or runtime test leaves.
