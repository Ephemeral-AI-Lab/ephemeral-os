/goal Implement Phase 4 Stage 1 (observability snapshot monitoring) of the live E2E orchestrator, building to green against the approved spec.

Repo: /Users/yifanxu/machine_learning/LoVC/ephemeral-ai/ephemeral-os
Spec (authority — read in full first): docs/e2e/sandbox-e2e-live-test-phase-4-spec.md
Crate: sandbox-e2e-live-test. Run `export PATH="$PWD/bin:$PATH"` before building.

The spec is the fixed design; live code wins on any conflict. Do NOT redesign — implement exactly what §2–§7 and the anchor ledger (§8) specify. Make additive, localized edits only; other agents may be editing this repo concurrently.

SCOPE — build now (Stage 1):
- src/report.rs [EDITED]: add the `observability.json` writer + its DTOs, and the additive `observability` field on the `Summary` DTO. Keep write_exchange/write_result/write_run_manifest/write_summary/build_tests behavior byte-stable.
- src/bin/eos-e2e.rs [EDITED]: integrate the bounded snapshot poller into run_pipeline. std only — NO new crate deps. Do NOT change pass/fail gating or STAGE1_DEFAULT_TARGET.
- tests/observability_writer.rs [NEW, optional per §8 Q8]: narrow unit tests for DTO/writer behavior. No live Docker, no runtime leaves.

POLLER (§3):
- One `std::thread` side-thread spawned in run_pipeline; 1000 ms interval; `Arc<AtomicBool>` stop flag.
- Drives ONLY the public op: `sandbox-cli manager get_observability_tree --include-recent-traces 1 --trace-limit 100 --resource-window-ms 60000`, polling the WHOLE tree (no --sandbox-id), keying each node by returned `sandbox_id`.
- Joined BEFORE build_tests AND BEFORE guard.teardown(), so every artifact is flushed before cleanup removes run_root.
- Writer creates reports/{sandbox_id}/ for ids the tree shows before Sandbox::drop made the dir.

ARTIFACT (§4): {run_root}/reports/{sandbox_id}/observability.json with the exact schema (schema_version, sandbox_id, sampled_at, source_call, latest_node, resource_summary, p1, recent_traces, warnings). Latest-snapshot semantics as specified.

P1 (§5): read the PUBLIC nested `cgroup` object — keys `available`/`error` plus `cpu_usage_usec`/`memory_current_bytes`/`memory_max_bytes`/`memory_max_unlimited`. cgroup is hard-coded unavailable daemon-side today → record a warning, NEVER fail. Do NOT touch P2 (queue-wait) — that is Stage 2.

FAILURE SEMANTICS (§6): poll CLI errors, unavailable/malformed nodes, missing P1, and write failures are ALL warnings recorded in observability.json / Summary.observability. They MUST NOT affect process exit. Pass/fail stays the Phase 3 gate: cargo-test exit code + per-test result.json statuses.

BOUNDARY: black-box only. No dependency on sandbox-observability, no SQLite reads, no `*_for_test`, no internal daemon/runtime crates.

ACCEPTANCE — all must pass, report actual output:
1. `cargo build -p sandbox-e2e-live-test` → exit 0
2. `cargo clippy -p sandbox-e2e-live-test --all-targets` → exit 0
3. `cargo fmt --check` → exit 0
4. Bare `cargo test -p sandbox-e2e-live-test` with EOS_E2E_RUN_ROOT unset still skips cleanly and writes no run artifacts.
5. A manager-only attach run with `--keep-artifacts` writes reports/{sandbox_id}/observability.json per observed sandbox, Phase 3 pass/fail gate unchanged.
6. The artifact records warnings for missing P1 instead of failing.
7. Default cleanup unchanged; artifact inspection uses --keep-artifacts.
8. `git diff --name-status` shows changes ONLY in src/report.rs, src/bin/eos-e2e.rs, and optionally tests/observability_writer.rs — nothing added/modified under tests/runtime/ or tests/manager/.
9. `rg` confirms no store readers, no *_for_test, no P2 queue-wait fields as a requirement, no runtime test leaves.

Follow CLAUDE.md: SRP and prefer-less; no inline comments in src/; no test code in src/. Stop when every acceptance check is green.
