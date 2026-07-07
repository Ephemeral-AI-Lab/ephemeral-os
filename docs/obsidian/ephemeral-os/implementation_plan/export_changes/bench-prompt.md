---
title: Manager Export Changes — trio-first benchmark prompt (stage 1: 1/5/20 MiB now; stage 2: 50/250 MiB + 1 GiB deferred)
tags:
  - ephemeral-os
  - manager
  - export
  - benchmark
  - prompt
status: draft
updated: 2026-07-07
---

/goal Benchmark the export_changes CLI end-to-end on the EXPLORATION trio only — 1, 5, 20 MiB (plus an empty-delta fixed-cost control) — fit the cost model, and publish falsifiable predictions for the deferred large sizes. The only time measured, reported, or compared anywhere is the export OPERATION's wall clock. Expected walls (estimates the measurements replace): empty delta ~0.3–1 s, 1 MiB ~1 s, 5 MiB ~1–2 s, 20 MiB ~2–4 s — every stage-1 export is seconds; if one takes ≥ 30 s, stop and investigate, do not wait it out. The large ladder (50 MiB, 250 MiB, 1 GiB) is stage 2, run later by explicit request only — do NOT run anything ≥ 50 MiB in this goal. Measurement only: no timing SLO is enforced or invented.

Truth - read first, follow exactly:
- docs/obsidian/ephemeral-os/implementation_plan/export_changes/spec.md — the cost table ("Speed and space, explicitly"), the data path (2 MiB chunks, base64 4/3 framing, fresh TCP per forward, ~512 sequential forwards per GiB), B2 (re-export: full wire re-stream, O(new bytes) host writes).
- cli-operation-e2e-live-test/manager/management/export/helpers.py — the LANDED harness you extend (create_sandbox, publish_exec, export_changes returning RawResult.elapsed_ms, read_tree, CaseRecorder, teardown, finalize_summary); manager/management/squash/{test_squash_bench.py,measure.py} — the bench-tier precedent (marker-gated, explicit-run, measurement reports).

State: no export bench code exists — you author it. Add a "bench" tier to export/helpers.py (cases PERF-0, PERF-1M, PERF-5M, PERF-20M, PERF-SHAPE-20M, PERF-ZSTD-20M; one size parameter drives them) + test_export_bench.py, marker "export and bench" (+ slow). Default ubuntu image; payloads from in-container /dev/urandom (incompressible = the honest worst case); sizes binary (MiB) so chunk counts are exact: 1 / 3 / 10.

Environment prep:
1. RELEASE manager or no sign-off: bin/start-sandbox-docker-gateway serves target/debug binaries; debug-profile numbers are junk. Run the gateway/manager from a release build (mechanism your choice); record every binary's profile in the results doc.
2. Trio sizes never approach the export caps or disk limits — no cap changes needed for stage 1; leave the running gateway's caps as found.
3. EXPORT_RUN_ID=export-perf-$(date +%Y%m%d-%H%M%S) once; serial; quiesced machine; record hardware (arch, cores, disk kind) and the Docker file-sharing backend in the results doc.

Matrix (≥3 reps per cell, medians; every cell is seconds):
- PERF-0 — the FIXED-COST CONTROL: export of an EMPTY delta (base-only manifest, EZ-02 shape) x5. This is the floor every export pays (CLI spawn + forward + fold of nothing + result line); it anchors the model's constant term and is the honesty reference for any future "faster at small sizes" claim.
- PERF-1M / PERF-5M / PERF-20M — single urandom file per size. Per size: (a) publish/capture time (setup, recorded), (b) COLD dir export onto a fresh dest x3, (c) WARM re-export onto the same dest x3 (skip-unchanged: host writes ~0, wire unchanged — B2's honest shape), (d) tar-zst onto a file x3 (wire+spool isolation: dir_cold − tarzst ≈ decode+apply).
- PERF-SHAPE-20M: 20 MiB as 20 x 1 MiB files (split -b 1m), dir cold+warm x3 — entry-count overhead vs the single-file shape.
- PERF-ZSTD-20M: 20 MiB of zeros, dir + tar-zst cold x3 — the compressibility contrast (spool_bytes collapses; the wire tax is on COMPRESSED bytes).

Record per rep (measurements.json per case): client wall ms, the full result line, spool/archive bytes, derived MiB/s and chunk count (ceil(compressed / 2 MiB)). Fit the cost model wall_ms ≈ a + b·chunks + c·payload_bytes on {0, 1, 5, 20} MiB and PUBLISH the predictions for 50 MiB, 250 MiB, and 1 GiB in the results doc as falsifiable numbers — stage 2's only job later is to confirm or refute them; a divergence at scale is a finding (nonlinearity = bug candidate), not noise.

Axes per case (reuse CaseRecorder; verdict.json + SUMMARY.md as always): correctness = sha256 of exported file(s) == in-sandbox sha at every size + exact result-line counts; host-safety = no literal markers + teardown (leases 0, .export empty); incremental = warm run files_written==0, skipped_unchanged==entry count, bytes_written==0, wall recorded honestly (the wire re-streams by design). Timing itself is never a pass/fail axis.

Failure protocol: a failing run maps to a spec bound first; never tune caps or timeouts mid-run; never weaken an assertion. Fix PRODUCT code only for a genuine defect — then cargo build && cargo test && cargo clippy --all-targets && cargo fmt, repackage the daemon, restart the gateway, re-run.

Done when: 6 verdicts + measurements.json each + SUMMARY.md under test-reports/<RUN_ID>/; results doc docs/obsidian/ephemeral-os/implementation_plan/export_changes/export-perf-results.md written — the trio table, PERF-0 floor, cold-vs-warm, dir-vs-tar-zst decomposition, the fitted model with its large-size predictions; committed to main (no branches) citing spec.md's cost table.

## Stage 2 (deferred — run only on explicit request, separate invocation)

The FINAL sweep: PERF-50M, PERF-250M, PERF-1G (single urandom file, same four measurements, expected chunks 25 / 125 / 512), checking the stage-1 model's predictions. Landmines that only bite here: (a) the long-lived gateway may carry EOS_EXPORT_MAX_DECOMPRESSED_BYTES=268435456 + EOS_EXPORT_MAX_ENTRIES=50000 — 1 GiB fails on the CAP and 250 MiB sits ~6 MB under it; run stage 2 with both UNSET and restore standard caps after. (b) Disk: ~3x size in-container + 1x host dest — 1 GiB needs ~4 GiB free in the Docker VM; clean between sizes. (c) The 30 s start ceiling (fold + spool in one request) and the 2 GiB MAX_STREAM_BYTES cap are documented boundaries: record wall-to-failure, never retry-loop them. Expected per-export walls today: 50 MiB ~3–6 s, 250 MiB ~10–30 s, 1 GiB ~1–2 min — these are the numbers stage 2 measures and any speedup work divides by 10.
