> **Frozen historical prompt (operation-layout exempt, 2026-07-11):** Do not
> execute this prompt verbatim; its commands and paths are preserved from the
> completed live verification.

/goal Execute the Manager Export Changes live-Docker e2e catalog (30 cases) to 30/30 green and produce its sign-off bundle.

Truth - read first, follow exactly:
- docs/obsidian/ephemeral-os/implementation_plan/export_changes/test-case.md (catalog truth: fixtures, per-case assertions, section 5 order)
- docs/obsidian/ephemeral-os/implementation_plan/export_changes/spec.md (design truth: invariants 1-10, workflows B1-B5, output contract)

State: the Rust implementation and the catalog have LANDED; the suite has never run live (no test-reports/). All 30 cases are data-driven in cli-operation-e2e-live-test/manager/management/export/helpers.py (cases_for_tier/run_case; hostile seam = craft_hostile_spool + inject_spool dropping <scratch_root>/.export/OVERRIDE.tar.zst); test_export_{easy,medium,hard}.py parametrize over it; conftest.py export_preconditions asserts P1-P4 once. Your job is execution + hardening, not authoring.

Run (repo root):
1. export PATH="$PWD/bin:$PATH"; bin/start-sandbox-docker-gateway --rebuild-binary
2. cd cli-operation-e2e-live-test; set EXPORT_RUN_ID=export-$(date +%Y%m%d-%H%M%S) once so all tiers share one test-reports/<RUN_ID>/.
3. Section 5 order, serial: preconditions P1-P4 hard-fail, never skip (P1 CLI surface + SPECS<->OPERATIONS parity; P2 zstd round-trip; P3 dir-apply onto the bind-root seed; P4 boot reap of .export/) -> pytest -m "export and easy" (EZ-01 first; <=4 min) -> "export and medium" (<=8 min) -> HRD-01..08 -> HRD-09/10 last (<=15 min). A host-safety failure in HRD-01..05 is Critical: stop, fix, restart the tier.

Every case passes only on all three axes + teardown (sections 2, 1.3):
- correctness: tree/archive == MergedView delta; result JSON exact (manifest_version, layers_exported, files_written, symlinks_written, deletes_applied, opaque_clears, skipped_unchanged, bytes_written | whiteouts_emitted).
- host-safety (load-bearing): nothing outside dest ever touched - sentinels byte-identical; no literal .wh./opaque marker in a dir dest; deny-list + decompression/entry caps hold.
- incremental: re-run writes zero content bytes for file winners; bytes_written tracks delta, not image.
- teardown: active_lease_count==0 on every layer; <scratch_root>/.export/ empty; artifact bundle written even on failure.

Failure protocol: map the failing assertion to its spec item via section 4 traceability; fix PRODUCT code to that invariant - never weaken an assertion. Touch helpers/catalog only where they deviate from test-case.md. HRD-01..05/10 must go through the hostile seam; the honest daemon cannot author traversal entries - an honest-stream rewrite makes them vacuous and is a fail. After any Rust change: cargo build && cargo test && cargo clippy --all-targets && cargo fmt, then rebuild the gateway binary before re-running.

Done when: P1-P4 asserted; 30/30 pass on all axes + teardown; test-reports/<RUN_ID>/ holds 30 verdict.json + SUMMARY.md; cargo gates green; committed to main (no branches) citing the catalog.
