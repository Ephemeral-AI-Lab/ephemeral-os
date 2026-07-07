---
title: Manager Export Changes — stage-1 benchmark results + cost attribution
tags:
  - ephemeral-os
  - manager
  - export
  - benchmark
  - results
status: measured
updated: 2026-07-08
---

# Export stage-1 benchmark — trio results, cost model, attribution

Stage-1 run of `bench-prompt.md` (PERF-0 + the 1/5/20 MiB exploration trio +
shape/compressibility contrasts). Every number is the export **operation's
client wall clock** (`RawResult.elapsed_ms` of one `sandbox-manager-cli
export_changes` invocation), medians of ≥3 reps (5 for PERF-0), serial, on a
quiesced machine. No timing SLO is enforced; this doc is measurement and
attribution only. Raw bundles: `cli-operation-e2e-live-test/manager/management/
export/test-reports/export-perf-20260708-000639/` (verdict.json +
measurements.json per case, SUMMARY.md; 7/7 pass including sha256 correctness
at every size).

## Environment (all binaries release-profile)

| Component | Profile / version |
| --- | --- |
| host | Apple M3 Max, 14 cores, 36 GiB, arm64, APFS on internal NVMe |
| sandbox-gateway (hosts the manager service + applier) | `target/release`, opt-level 3, lto fat |
| sandbox-manager-cli / sandbox-runtime-cli | `target/release` (symlinked into `target/debug` so `bin/` wrappers exec them) |
| sandbox-daemon (in-container) | `xtask package --profile release`, aarch64-unknown-linux-musl, sha256 `f4c79a68…` |
| Docker | Docker Desktop 29.5.2, linux/aarch64 VM (4 CPUs, 3.8 GiB), default file sharing (VirtioFS); sandbox image `ubuntu:24.04` |

Payloads are in-container `/dev/urandom` (incompressible — the honest worst
case); binary sizes so chunk counts are exact. Correctness per rep: an
in-sandbox `sha256sum` manifest (`payload.sha`) crosses as delta content and
every exported byte is re-hashed host-side against it.

## The trio table (medians, ms)

| Case | payload | spool bytes | chunks | publish (setup) | cold dir | warm dir | tar-zst | cold MiB/s |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| PERF-0 | 0 | 0 (empty tar-zst) | 0 | — | **25.7** | — | — | — |
| PERF-1M | 1 MiB | 1,049,231 | 1 | 80.9 | **34.7** | 36.3 | 37.8 | 30.2 |
| PERF-5M | 5 MiB | 5,243,674 | 3 | 92.9 | **91.0** | 85.0 | 80.5 | 55.0 |
| PERF-20M | 20 MiB | 20,972,674 | 11 | 182.4 | **280.0** | 269.0 | 249.7 | 71.4 |
| PERF-SHAPE-20M | 20 MiB × 20 files | ≈ 20.98 MB (derived: PERF-20M spool + 20 tar headers) | 11 | 214.4 | 285.7 | 253.2 | — | 70.0 |
| PERF-ZSTD-20M | 20 MiB zeros | 848 | 1 | 138.9 | 58.2 | — | 36.3 | 343.8 |

Reps (cold dir): PERF-0 [25.9, 24.1, 26.8, 25.7, 25.1] · 1M [45.7, 34.7, 32.4]
· 5M [91.0, 95.7, 84.9] · 20M [303.0, 280.0, 269.0]. Warm honesty: the wire
re-streams the full compressed delta by design (B2); warm saves only host
writes, so warm ≈ cold − (write − compare-read) ≈ −4 to −11 ms at 20 MiB.
Chunk counts are exact: `ceil(spool / 2 MiB)` — note 20 MiB of urandom spools
to 20,972,674 bytes (> 20 MiB), so it pages **11** chunks, not the 10 the
prompt estimated.

## The two floors (controls, measured)

- **a-floor — PERF-0 = 25.7 ms.** The fixed cost every export pays. Within it,
  `list_sandboxes` (CLI spawn + gateway TCP round trip, **no daemon
  involvement**) measures **21.1 ms** on the same run — so ~82% of the fixed
  cost is the CLI-spawn + gateway harness, outside export's own code. The
  remaining ~4.6 ms covers both daemon forwards (start + one chunk of the
  empty spool) and the fold of nothing ⇒ **~2.3 ms per daemon forward**.
- **c-floor — dd 20 MiB sequential read+write on the host dest filesystem:
  20 ms ⇒ 1.0 ms/MiB** (`dd bs=1m` of a 20 MiB urandom file, 3 reps: 0.02 s
  each).

## Fitted cost model (baseline, cold dir, urandom)

Free least squares on {0,1,5,20} MiB is ill-conditioned (chunks ≈ 0.55·MiB —
collinear), so the model is physically anchored: `a` is PERF-0; `b` is the
measured per-forward round trip (PERF-0 − list_sandboxes over two forwards);
`c` closes the 20 MiB point; 1/5 MiB check the fit.

```text
wall_ms ≈ 25.7 + 2.3·chunks + 11.4·payload_MiB
          check: 5 MiB −1.2% · 1 MiB +13.7% (small-size noise) · 20 MiB exact
```

### Falsifiable stage-2 predictions (baseline path, from this model)

| size | chunks | predicted cold-dir wall |
| --- | --- | --- |
| 50 MiB | 26 | **0.66 s** |
| 250 MiB | 126 | **3.18 s** |
| 1 GiB | 513 | **12.9 s** |

Divergence at scale is a finding (nonlinearity = bug candidate), not noise.
Stage-2 landmines (bench-prompt §Stage 2): unset
`EOS_EXPORT_MAX_DECOMPRESSED_BYTES` / `EOS_EXPORT_MAX_ENTRIES` for 250 MiB/1
GiB or the cap fires by design; ~3× in-container disk per size.

## Attribution — where the 11.4 ms/MiB and the 25.7 ms actually go

Decompositions from the run's own contrasts:

- **dir vs tar-zst (decode+apply share):** 280.0 − 249.7 = **30 ms** at
  20 MiB = the applier's two capped zstd decode passes + 20 MiB of dest
  writes + per-file chmod/mtime stamping, minus the archive's own 21 MB file
  write. Host-side apply is NOT the dominant term.
- **compressibility contrast (the wire tax):** same 20 MiB payload as zeros —
  spool collapses to 848 bytes, 1 chunk — costs **58.2 ms** dir-cold vs
  **280.0 ms** for urandom. ⇒ **~222 ms (~79%) of the 20 MiB wall is
  spending on compressed-payload bytes crossing the daemon protocol**, not on
  reading, folding, or applying 20 MiB of content (zeros still pay the full
  20 MiB in-container read + host write + tar framing: ~1.6 ms/MiB
  end-to-end).
- **entry-count overhead:** 20 files vs 1 at the same 20 MiB: +5.7 ms
  (~0.3 ms/entry) — negligible at these shapes.
- **the per-chunk loop:** 11 chunks × ~2.3 ms/forward ≈ 25 ms of the 20 MiB
  wall — each `read_export_chunk` forward is a fresh client thread + fresh
  single-threaded tokio runtime + fresh TCP connection + one bounded JSON
  round trip (`daemon_client.rs`).

Budget of the measured per-byte coefficient (c ≈ 11.4 ms/MiB, urandom):

| per-byte component (per MiB of payload) | est. share | evidence |
| --- | --- | --- |
| base64 encode (daemon) + ×1.33 framing + JSON-string serialize of ≈2.8 MB chunk responses | ~3–4 ms | zeros-vs-urandom contrast; both scale with *compressed* bytes |
| serde_json parse of ≈2.8 MB strings + base64 decode + `Vec` growth (manager `page_stream`) | ~4–5 ms | same contrast; the manager buffers the whole compressed delta in RAM before apply |
| zstd −3 compress + spool write + spool read-back (daemon, real bytes) | ~1–1.5 ms | zeros pipeline ≈1.6 ms/MiB total incl. host write |
| two capped decode passes + dest writes + mtime stamps (applier) | ~1.5 ms | dir − tar-zst = 30 ms/20 MiB |
| loopback TCP transfer of ×1.33 framed bytes | <0.5 ms | localhost |

**Conclusion (the profile, not the hypothesis list): the dominant waste is the
JSON-chunk transport itself — base64's 4/3 inflation and encode/decode passes,
JSON serialize/parse of multi-megabyte strings, and the sequential
fresh-connection chunk loop. Exactly the successor case decision 15 names:
"if the base64 tax dominates, the HTTP stream is the documented next step."
The fixed cost is harness-dominated (21.1 of 25.7 ms is CLI spawn + gateway
round trip before export code runs); the two daemon forwards it does own can
collapse to one.**

## What 10× means against these measured walls (floor arithmetic)

Strict ÷10 targets vs the additive floor (a-floor + dd·size):

| size | baseline | ÷10 target | measured floor (25.7 + 1.0·MiB) | verdict |
| --- | --- | --- | --- | --- |
| 1 MiB | 34.7 | 3.5 ms | 26.7 ms | 10× is **below the physical floor** — floor clause governs (≤ ~1.2× floor ≈ 32 ms) |
| 5 MiB | 91.0 | 9.1 ms | 30.7 ms | same (≤ ~37 ms) |
| 20 MiB | 280.0 | 28.0 ms | 45.7 ms | same (≤ ~55 ms) |

Per the speedup prompt's own acceptance, each size must reach ÷10 **or** land
within ~1.2× of the cited floor, and each coefficient must improve ~10× or pin
at its floor: `b` must go to ~0 (kill the chunk loop), `c` must approach the
~1.0 ms/MiB dd control plus the pipeline's irreducible codec passes, and `a`
is already ~1.2× of its 21.1 ms harness floor — its export-owned share (2
forwards + fold) is what can still shrink.
