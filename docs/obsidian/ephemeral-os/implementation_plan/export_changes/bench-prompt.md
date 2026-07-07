---
title: Manager Export Changes — size-sweep benchmark prompt (exploration 1/5/20 MiB; final 1/50/250 MiB + 1 GiB)
tags:
  - ephemeral-os
  - manager
  - export
  - benchmark
  - prompt
status: draft
updated: 2026-07-07
---

/goal Benchmark the export_changes CLI end-to-end on two size ladders — an EXPLORATION trio (1, 5, 20 MiB: seconds per run, for harness shakeout and any downstream iteration) and the FINAL sweep (1, 50, 250 MiB, 1 GiB) — produce a measurement bundle + results doc + a fitted cost model, and verify the spec's cost table holds at scale. Measurement only: no timing SLO is enforced or invented; the deliverable is honest numbers with the ceilings named. Large sizes are bookends, never iteration: nothing ≥ 50 MiB runs until the trio is green and its numbers are sane.

Truth - read first, follow exactly:
- docs/obsidian/ephemeral-os/implementation_plan/export_changes/spec.md — the cost table ("Speed and space, explicitly"), the data path (2 MiB chunks, base64 4/3 framing, fresh TCP per forward, ~512 sequential forwards per GiB), decision 18 (30 s REQUEST_READ_TIMEOUT_S start ceiling — fold + spool must fit ONE request; squash-first is the mitigation), B2 (re-export: full wire re-stream, O(new bytes) host writes).
- crates/sandbox-manager/src/export_apply.rs — MAX_STREAM_BYTES = 2 GiB (hard manager cap; never bench past it), EOS_EXPORT_MAX_DECOMPRESSED_BYTES / EOS_EXPORT_MAX_ENTRIES env caps (default 8 GiB / 1M).
- cli-operation-e2e-live-test/manager/management/export/helpers.py — the LANDED harness you extend (create_sandbox, publish_exec/build_in_sandbox, export_changes returning RawResult.elapsed_ms, read_tree, CaseRecorder, teardown, finalize_summary); manager/management/squash/{test_squash_bench.py,measure.py} — the bench-tier precedent (marker-gated, explicit-run, measurement reports).

State: no export bench code exists — you author it. Add a "bench" tier to export/helpers.py (cases PERF-1M, PERF-5M, PERF-20M, PERF-50M, PERF-250M, PERF-1G, PERF-SHAPE-50M, PERF-ZSTD-50M; one size parameter drives them all) + test_export_bench.py, marker "export and bench" (+ slow). Default ubuntu image; payloads generated in-sandbox from /dev/urandom (incompressible = the honest worst case); sizes are binary (MiB/GiB) so chunk counts are exact.

Environment prep (each item has bitten before — do not skip):
1. RELEASE manager or no sign-off: bin/start-sandbox-docker-gateway serves target/debug binaries; a debug-profile zstd-decode + fd-walk applier produces junk perf numbers. Run the gateway/manager from a release build (mechanism your choice — build --release and launch the release sandbox-gateway with the script's serve args and token/pid conventions, or add a profile knob to the script). The daemon artifact is already optimized (package-fast). Record every binary's profile in the results doc.
2. Caps: the long-lived gateway may carry EOS_EXPORT_MAX_DECOMPRESSED_BYTES=268435456 + EOS_EXPORT_MAX_ENTRIES=50000 from the HRD suite — 1 GiB dir exports would fail on the CAP, not on performance, and 250 MiB sits only ~6 MB under it (a near-miss artifact waiting to happen). Start the bench gateway with both UNSET (defaults 8 GiB / 1M). Afterwards restore a standard-caps gateway (the HRD tier depends on them) — leave the environment as found.
3. Disk budget: each size costs ~3x in-container (upperdir + published layer + spool) + 1x host dest — the 1 GiB tier needs ~4 GiB free in the Docker Desktop VM and ~1 GiB on the host. Destroy the sandbox and rm dests between sizes; verify free space before the 1 GiB tier.
4. EXPORT_RUN_ID=export-perf-$(date +%Y%m%d-%H%M%S) once; serial; quiesced machine (no parallel builds); record hardware (arch, cores, disk kind) and Docker Desktop file-sharing backend (VirtioFS) in the results doc — numbers are relative across sizes, not absolute SLOs.

Matrix (bounded — ≥3 reps per cell, report the median). Each size case measures the same four things: (a) publish/capture time (setup cost, recorded — this exercises the capture path at scale), (b) COLD dir export onto a fresh dest x3, (c) WARM re-export onto the same dest x3 (skip-unchanged: host writes ~0, wire unchanged — B2's honest shape), (d) tar-zst onto a file x3 (wire+spool isolation: dir_cold − tarzst ≈ decode+apply).
- EXPLORATION trio first (PERF-1M/5M/20M — single urandom file): the harness shakeout AND the iteration ladder. Every run is seconds; run these until the harness and numbers are sane before anything larger. Expected chunks: 1 / 3 / 10.
- FINAL sweep (PERF-1M/50M/250M/1G — single urandom file): the sign-off numbers. Expected chunks: 1 / 25 / 125 / 512. PERF-1M is shared with the trio (one case, dual duty — it anchors the two ladders to each other).
- PERF-SHAPE-50M: 50 MiB as 50 x 1 MiB files (split -b 1m), dir cold+warm x3 — entry-count overhead vs the single-file shape (fold entries, tar headers, per-file writes + mtime stamps).
- PERF-ZSTD-50M: 50 MiB of zeros, dir + tar-zst cold x3 — the compressibility contrast (spool_bytes collapses; shows the wire tax is on COMPRESSED bytes).

Record per rep (measurements.json per case): client wall ms (RawResult.elapsed_ms), the full result line (files_written, skipped_unchanged, bytes_written), spool/archive bytes, derived MiB/s, and derived chunk count (ceil(compressed / 2 MiB)). Then fit the cost model wall_ms ≈ a + b·chunks + c·payload_bytes on {1, 5, 20, 50} MiB and report predicted-vs-measured at 250 MiB and 1 GiB — the model is what lets any future perf work iterate on the trio and extrapolate; a divergence at scale is itself a finding (nonlinearity = bug candidate), not noise to smooth over.

Axes per case (reuse CaseRecorder; verdict.json + SUMMARY.md as always):
- correctness: sha256 of the exported file(s) == in-sandbox sha (exec_capture) at EVERY size — fidelity at scale is nearly free, assert it; result-line counts exact.
- host-safety: no literal markers; teardown (leases 0, .export empty) after every size.
- incremental: warm run asserts files_written==0, skipped_unchanged==entry count, bytes_written==0; wall time recorded (it will NOT be ~0 — the wire re-streams; report that honestly, it is the design).
- Timing itself is never a pass/fail axis.

Failure protocol: a failing run maps to a spec bound first — the 30 s start ceiling (operation_failed on the START call at 1 GiB is a documented ceiling: record wall-to-failure, note squash-first, do NOT retry-loop it into "flaky"), the 2 GiB stream cap, the request envelope. Never tune caps or timeouts mid-run to make a size pass; never weaken an assertion. Fix PRODUCT code only for a genuine defect (nonlinear blowup, unbounded memory, leaked spools) — then cargo build && cargo test && cargo clippy --all-targets && cargo fmt, repackage the daemon, restart the gateway, re-run the affected sizes.

Done when: the exploration trio and the final sweep both measured (or a ceiling failure documented with numbers), probes done; test-reports/<RUN_ID>/ holds 8 verdicts + measurements.json each + SUMMARY.md; results doc docs/obsidian/ephemeral-os/implementation_plan/export_changes/export-perf-results.md written — the size table across both ladders, cold-vs-warm, dir-vs-tar-zst decomposition, the fitted cost model with predicted-vs-measured at 250 MiB and 1 GiB, and a verdict against each spec cost-table row (fold O(entries), content O(merged bytes), re-export host writes O(new bytes), wire = compressed x 4/3) plus where each ceiling sits; standard-caps gateway restored; committed to main (no branches) citing spec.md's cost table. Budget ≤ 45 min total; the exploration trio must be green in ≤ 5 min before anything ≥ 50 MiB runs — large sizes are bookends, never debugging.
