---
title: Manager Export Changes — 10x export-speed prompt (any size, no added workers)
tags:
  - ephemeral-os
  - manager
  - export
  - performance
  - prompt
status: archived
updated: 2026-07-11
---

> **Frozen historical prompt (operation-layout exempt, 2026-07-11):** Do not
> execute this prompt verbatim; its paths and package names describe the tree
> used for the completed optimization work.

/goal Make the export_changes operation itself at least 10x faster at ANY delta size — the acceptance is one number per size: the wall time of a cold dir export. If an export measures T at baseline, the same export must measure ≤ T/10 after (an export that took 10 minutes must take under 1 minute; one that took 2 seconds must take under 200 ms, floor-permitting). The ONLY time that matters anywhere in this task is the export operation's wall clock — no other duration is tracked, reported, or budgeted. Constraint: the speedup must come from eliminating waste on a single stream, NOT from added parallelism — no multi-connection fan-out, no worker/thread pools, no parallel apply; one connection, one worker. Overlap that falls out of streaming (the socket buffer fills while the applier drains) is fine; spawning fetchers is not. If a design needs concurrency to reach 10x, it fails the constraint — find the waste instead.

Baseline estimate (cold dir, single urandom file, release manager — ESTIMATES to anchor expectations; the bench's MEASURED baseline is always the real divisor, never these numbers):

| size | chunks | est. baseline wall | target after |
| --- | --- | --- | --- |
| empty delta (PERF-0, the fixed floor) | 0 | ~0.3–1 s | the floor itself — measure it, it bounds every small-size target |
| 1 MiB | 1 | ~1 s | ≤ 1/10th measured, or within ~1.2x of the PERF-0 floor |
| 5 MiB | 3 | ~1–2 s | ≤ 1/10th measured (same floor clause) |
| 20 MiB | 10 | ~2–4 s | ≤ 1/10th measured |
| 50 MiB (deferred) | 25 | ~3–6 s | ≤ 1/10th measured |
| 250 MiB (deferred) | 125 | ~10–30 s | ≤ 1/10th measured |
| 1 GiB (deferred) | 512 | ~1–2 min | ≤ 1/10th measured |

"Any size" has a precise meaning here: the cost model wall(s) ≈ a + b·chunks(s) + c·payload_bytes(s) must improve in EVERY coefficient — the constant a (CLI spawn + forward + fold + first byte), the per-chunk b (round trips + framing), and the per-byte c (read/compress/decode/write passes) — each ~10x better, or pinned within ~1.2x of its measured physical floor (a-floor: the PERF-0 empty-delta export; c-floor: a dd sequential read+write control). A win that only shrinks b is a large-size-only win and does not meet the goal; 10x at 1 MiB lives in a.

Truth - read first, follow exactly:
- docs/obsidian/ephemeral-os/implementation_plan/export_changes/spec.md — the data path (2 MiB JSON chunks, base64 4/3 framing, fresh TCP per forward, ~512 sequential forwards/GiB), decision 15 (why JSON paging won v1, and its OWN named successor: "if the base64 tax dominates, the HTTP stream is the documented next step"), decision 18 (30 s start ceiling), invariants 6 (the detach is law; the manager is the ONLY host writer) and 9 (the applier consumes sandbox-authored bytes as hostile: canonicalization, caps — REGARDLESS of transport).
- docs/obsidian/ephemeral-os/implementation_plan/export_changes/bench-prompt.md and its stage-1 bundle (PERF-0 + the 1/5/20 MiB trio, the fitted model). If it has not run, run stage 1 first — no measured baseline, no speedup claim.
- crates/sandbox-manager/src/{operation/management/service/impls/export_changes.rs,export_apply.rs} (the forward loop + applier), crates/sandbox-runtime/operation/src/layerstack/service/impls/export.rs (spool + read_export_chunk), cli-operation-e2e-live-test/runtime/daemon_http/test_daemon_http.py (the existing daemon HTTP surface and its posture).

State: the export works and is correct (30-case catalog + 6 runnable cases green). You are changing PRODUCT code; the full regression surface must stay green. All measurement — attribution, iteration, acceptance — runs on PERF-0 + the trio (every run is seconds); the deferred sizes are verified later via the bench's stage 2 against the predictions you publish. Never run ≥ 50 MiB in this task.

Order of work:
1. Attribute: from the stage-1 bundle, decompose the trio walls into a / b·chunks / c·bytes and the dir-vs-tar-zst contrast (decode+apply share). Measure the two floors as CONTROLS: PERF-0 (fixed) and dd read+write at 20 MiB scale (per-byte). Publish the attribution before designing. Hypotheses from the spec's own accounting — the sequential chunk loop, the base64 tax (x1.33 bytes + encode/decode passes through JSON strings), zero overlap between wire and apply, the spool double-pass, and the fixed start cost — but believe the profile, not the list.
2. Design + adversarial review: write the design as a spec.md revision draft (data-path section + a new decision-log entry) BEFORE implementing; run it through the adversarial-review-prompt.md lens. Candidate map with constraints (start here, deviate with evidence):
   - Streamed binary delivery (decision 15's named successor): one HTTP stream of the SEALED spool from daemon to manager kills base64, the round-trip count, and most of a's per-request setup at once — single connection, single worker, inside the constraint. The consistency objection in decision 15 applies to bypassing the daemon protocol for LAYER reads — the spool is a lease-independent, export_id-keyed artifact, so streaming IT changes no snapshot semantics. The auth objection is real: daemon_http is unauthenticated — gate the stream with a single-use, expiring token minted by the authenticated start forward, bound to the export_id, constant-time compare, spool bytes only. Inv 6 unchanged (daemon still only delivers bytes to its caller); inv 9 unchanged (same canonicalization, same caps, MAX_STREAM_BYTES still enforced on the stream).
   - Fixed-cost (a) attack — required for "any size": collapse start + delivery round trips (e.g. the start response carries the stream handle so 1 MiB is start + one streamed read, not start + N chunk requests), reuse the connection, trim fold/spool setup for tiny deltas (streaming the fold directly for small spools is in-scope if the profile says a is spool-dominated).
   - EXCLUDED by direction: K parallel range fetches, parallel apply, any worker-pool scheme — even though chunk paging is offset-stateless and they would work, they scale resources instead of removing waste.
3. Implement + prove: smallest change that moves every coefficient. Iterate exclusively on PERF-0 + the trio; refresh the model after each change; a change that moves no coefficient is discarded. Then, in order: cargo build && cargo test && cargo clippy --all-targets && cargo fmt; repackage the daemon; restart the gateway (release-profile manager for all timing runs). Regression surface, all green and re-verified: the 30-case catalog with the HRD hostile-stream cases driven THROUGH THE NEW TRANSPORT (traversal, bombs, caps — the hostile seam must attack what ships), HRD-10's restart-mid-transfer analog (drop mid-stream → clean abort → re-run converges), HRD-06 concurrency, the 6 runnable cases, and new unit coverage for the transport (auth token single-use/expiry/mismatch, cap enforcement on the stream).

A/B acceptance — export wall time only, medians of ≥3, same machine, same release profile:
- PERF-1M, PERF-5M, PERF-20M cold dir: each ≤ 1/10th of its measured baseline, OR within ~1.2x of the cited floor control where the floor makes 10x impossible at that size (the floor claim must cite PERF-0/dd numbers, never be asserted).
- Coefficients: a, b, c each ~10x better or at floor — this is what makes the claim hold for ANY size, including the deferred ones.
- Published predictions for 50 MiB, 250 MiB, 1 GiB from the new model (bench stage 2 confirms them later; e.g. a 1 GiB baseline of ~90 s must predict ≤ 9 s).
- No regressions: warm re-export not slower; PERF-0 itself not slower; correctness sha256 asserted at every size.

Failure protocol: never weaken an assertion, never relax a cap/timeout to manufacture speed, never trade inv 6/9 for throughput — a fast applier that follows a symlink out of dest is a critical fail, full stop. If 10x is unreachable at some size after the dominant term is removed, deliver the measured attribution + the achieved per-size factors + the named residual floor honestly; a truthful 6x with the floor identified beats a gamed 10x.

Done when: attribution published; spec.md revised (data path + decision log) with the adversarial review recorded; implementation landed; full regression green (catalog 30, runnable 6, new transport tests, cargo gates); the A/B wall-time table (baseline vs after, per size, plus coefficients and large-size predictions) committed in export-speedup-results.md with the test-reports bundles; standard-caps gateway restored; committed to main (no branches) citing spec.md decision 15 and this prompt.
