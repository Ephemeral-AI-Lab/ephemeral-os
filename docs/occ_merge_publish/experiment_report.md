# OCC Merge Publish — Experiment Report

Status: complete. **Winner: C3 (three-way merge + full-file concrete layers +
provenance sidecars) now, with C6 (cold-layer compaction) as a measured-trigger
follow-on. C4/C5 patch-backed storage rejected** for failing the universal-mount
gate. Evidence in §6, scoring in §7, recommendation in §8.

This report evaluates how `sandbox-runtime-layerstack` should add publish-time
three-way merge, line-level provenance/auditability, and storage efficiency,
without breaking the overlay mount contract. It treats
[`README.md`](./README.md) as one proposal among several, reasons from the
layerstack code, defines candidate designs, benchmarks/models them, and picks a
winner.

Harness: `crates/sandbox-runtime/layerstack/tests/occ_merge_bench.rs`
(integration test, `#[ignore]`-gated, driven by the real public layerstack API).
Run command and raw output are recorded in
[Appendix A](#appendix-a-commands-and-environment).

---

## 1. Scope and constraints

### 1.1 What layerstack actually does (from code)

Read directly from `crates/sandbox-runtime/layerstack/src`:

- A committed layer is a **concrete filesystem subtree** at
  `layers/<layer_id>/`. `LayerChange::Write{path,content}` writes the *entire
  file* at `layers/<id>/<path>`; `WriteFile` copies a spooled file in.
  (`stack/layer/write.rs`)
- Snapshots hand the consumer `layer_paths` (`Lease.layer_paths`) to mount
  **directly as overlay lowerdirs**. Layerstack never re-encodes a layer before
  handing it out. (`stack/mod.rs::acquire_snapshot`)
- `MergedView::read_entry` resolves a path by walking layers **newest→oldest**
  and returning the first concrete file/symlink/dir/whiteout.
  `MergedView::project` applies layers **oldest→newest**, copying every entry
  into a destination tree. (`stack/projection/mod.rs`, `projection/apply.rs`)
- OCC: publish computes `fingerprint(base,path)` at plan time and compares to
  `fingerprint(active,path)` under the writer lock; any mismatch →
  `SourceConflict`, whole publish rejected, **no merge attempted**.
  (`stack/publish/{plan,validate,fingerprint}.rs`)
- **Publish never removes a layer.** `publish_layer_unlocked` pushes the new
  layer and `layers.extend(active.layers.clone())` — the active manifest is an
  ever-growing stack. (`stack/ops/publish.rs`)
- **No compaction / squash / GC of superseded content exists.** Grep for
  `compact|squash|flatten|gc|prune` in `src/` returns nothing. The only removal
  is `release_lease_locked`, which deletes a released lease's layers *only if*
  they are absent from the active manifest and every other lease
  (`stack/lease/cleanup.rs`). Because publish keeps all layers in active,
  superseded layers are always still in active and are therefore **never
  reclaimed while the stack is live**.
- Layerstack already records per-layer committed size at
  `.layer-metadata/<id>.bytes` and a content digest at `.layer-metadata/<id>.digest`
  (`storage/fs.rs`); `.layer-metadata/` is the natural home for provenance
  sidecars.

### 1.2 Consequences that drive the experiment

1. **Repeated edits to one file accumulate full copies.** N publishes of an
   F-byte file leave ≈ N·F committed bytes on disk (every historical version
   persists in a lower layer), even though only the newest is visible.
2. **Manifest depth grows O(number of publishes).** Reading a path that lives
   in a deep layer, and *projecting* the whole tree, both pay for historical
   layers. Projection copies every superseded version then overwrites it.
3. **OCC is path-granular**, so non-overlapping edits by two sessions to one
   file produce a false `SourceConflict`.
4. **There is no line-level provenance** anywhere today.

(1) and (2) are what make patch-backed storage tempting; (3) and (4) are what
the merge/provenance proposal targets. They are largely independent problems.

### 1.3 Hard constraints (gates — a design that fails any of these is rejected)

- **G1 Mount-contract consistency.** Every committed layer a snapshot exposes
  must be a concrete tree mountable directly as an overlay lowerdir, with
  `read_entry`/`project` semantics unchanged. *(User constraint: "consistent
  with layerstack mount core behaviour.")*
- **G2 Universality across Docker image envs.** The hot path (snapshot →
  mount → read) must not depend on synthesizing per-snapshot state at mount
  time, on a specific guest filesystem feature, or on tooling inside the image.
  It must work for any base image. *(User constraint: "universal to any docker
  image envs.")*
- **G3 Publish atomicity.** No partial changeset; no visible sidecar without its
  layer; no active-manifest update before all staged data is durable. Mixed
  source+ignored changes still publish-together / reject-together.
- **G4 Exact byte preservation.** A resolved/merged write must reproduce bytes
  exactly (no normalization). Binary / invalid-UTF-8 / ambiguous inputs must not
  be silently transformed or mis-attributed.

### 1.4 Soft criteria (weighted — see §7)

Correctness & auditability, hot-path latency, active disk, cold disk,
implementation risk, operational complexity.

### 1.5 Out of scope / platform notes

- Real **overlayfs mount latency is Linux-only**; this harness runs on the dev
  host (darwin). We measure `acquire_snapshot` + `MergedView::project`
  (realize-concrete-tree) as the portable proxy for "make a mountable tree."
  Project cost is *also* exactly the work a patch-materializer must do, so it
  doubles as the C4/C5 materialization primitive.
- Symlinks, deletes, dirs, opaque dirs, file-type changes, and binary merges are
  non-goals for auto-merge (kept as `SourceConflict`), matching the README.

---

## 2. Hypotheses (stated before results)

- **H1** Full-file concrete publish is fastest for hot workspace startup because
  overlay can mount committed layer dirs directly (no per-mount materialization).
- **H2** Patch-backed storage saves *cold* disk only when files are large, edits
  are small, and materialized caches can be evicted or shared.
- **H3** Patch-backed storage *increases active* disk because it stores both
  patches and materialized concrete caches simultaneously.
- **H4** Provenance sidecars add small write/read overhead for text files and
  ~zero overhead for binary files (skipped as `unknown`).
- **H5** A hybrid threshold beats pure patch storage by not materializing small
  or high-churn files.
- **H6 (code-evidence-driven)** The dominant real disk-growth term under churn is
  *retained superseded full-file layers*, not the absence of patches. A
  compaction pass that squashes cold layers into one concrete layer reclaims most
  of the bytes that patch storage targets, while keeping G1/G2 intact — i.e.
  compaction is a lower-risk substitute for patch storage for most workloads.

Each hypothesis has an explicit invalidation criterion in §6.

---

## 3. Candidate proposals (defined before benchmarking)

| ID | Name | Storage of a source write | Merge | Provenance | Touches mount contract? |
|----|------|---------------------------|-------|------------|-------------------------|
| **C1** | Baseline (today) | full-file concrete layer | none (reject on mismatch) | none | no |
| **C2** | Three-way merge | full-file concrete layer | line diff3 | none | no |
| **C3** | Merge + provenance sidecars | full-file concrete layer | line diff3 | sidecar `.layer-metadata/provenance/<id>/<path>.json` | no |
| **C4** | Patch-backed + materialized cache | patch vs parent + materialized concrete cache layer | line diff3 | from patches | **yes** (needs materializer before mount) |
| **C5** | Hybrid full/patch threshold | full-file, *or* patch when (large ∧ small-edit ∧ high-churn) | line diff3 | sidecar or patch | **yes** (for the patch path) |
| **C6** | Merge + provenance + cold-layer compaction | full-file concrete layer; background squash of cold layers | line diff3 | sidecar | no (squash output is still a concrete layer) |

### 3.1 Tradeoffs (qualitative, pre-measurement)

- **C1** — Simplest, fastest hot path, zero new format. Rejects non-overlapping
  concurrent edits (false conflicts); no audit trail; unbounded churn growth.
- **C2** — Removes false conflicts. Same storage profile and mount path as C1.
  Adds merge CPU only on the already-slow OCC-mismatch path. No audit trail.
- **C3** — C2 + line-level audit. Sidecars are concrete files under
  `.layer-metadata/` (not mounted), so **G1/G2 hold trivially**. Adds small
  sidecar bytes + serialize/parse latency. This is the README's full proposal.
- **C4** — Smallest *cold* disk for large-file/small-edit churn. But to satisfy
  G1/G2 it must materialize a concrete cache layer before any mount/read/project,
  store it durably for lease retention, and add it to digest+cleanup rules. That
  cache ≈ a full-file copy, so **active disk = patches + caches > C1**, and the
  hot path gains a materialization step — direct tension with **G2**. High risk.
- **C5** — Recovers C1's hot path for the common (small/high-churn) files and
  only pays C4's machinery for the large-stable-base/small-edit minority. Best
  theoretical disk/latency Pareto, but carries *both* code paths + a policy with
  thresholds to tune and observe. Highest complexity.
- **C6** — Attacks the measured growth term (retained superseded layers)
  directly with a squash that re-emits a single concrete lower layer and caps
  manifest depth. Keeps the overlay contract (G1) and universality (G2). Adds a
  background job with its own atomicity/lease-safety rules, but **no new layer
  format and no per-mount materialization**. Pairs with C3.

---

## 4. Benchmark matrix

Inputs are generated deterministically into a temp dir (cleaned up). "Modeled"
marks a metric computed from measured primitives for an unimplemented design;
everything else is measured against the real layerstack API.

| # | Case | Input | What it stresses | Key metrics |
|---|------|-------|------------------|-------------|
| B1 | small text, tiny edit | 1 KiB file, 1-line change | per-publish floor | committed bytes, publish/read/project latency |
| B2 | large text, one-line edit | ~1 MiB file, 1-line change | patch-vs-fullfile gap | committed, patch bytes (modeled), latency |
| B3 | large text, scattered edits | ~1 MiB, N disjoint edits | merge + patch growth | committed, patch bytes, merge clean? |
| B4 | repeated edits, many sessions | ~1 MiB, K sequential publishes | **churn growth, compaction** | committed bytes vs K (C1/C3 vs C4/C6) |
| B5 | many small files, one publish | M×small files | per-file overhead | committed, publish latency, sidecar bytes |
| B6 | mixed source + ignored | source + `.gitignore`d file | atomicity, routing | route summary, atomic publish |
| B7 | overlapping edits (must reject) | base/active/command overlap | conflict correctness | merge=Conflict, OCC reject |
| B8 | non-overlapping concurrent | active+command disjoint | false-conflict fix | C1 reject vs C2 clean merge |
| B9 | binary / invalid-UTF-8 / minified / generated | non-text + 1-line minified | eligibility, byte preservation | merge ineligible, provenance=unknown, sidecar bytes |
| B10 | deep manifest | D stacked layers | depth cost | read/project latency vs D, committed bytes |
| B11 | hot / cold cache / active lease | project repeatedly; hold lease | cache & lease retention | project latency hot/cold (modeled cache), active vs post-cleanup bytes (modeled) |
| B12 | provenance correctness | known 3-way inputs | attribution | line-origin == expected |

---

## 5. Methodology

- **Committed layer bytes**: walk `layers/` on disk after publish, sum regular
  file sizes (the bytes overlay would mount). Measured.
- **Provenance bytes**: serialize the real sidecar JSON for the real merged
  line-ranges; `len()`. Measured.
- **Patch bytes**: compute a real LCS line-diff between versions and serialize a
  compact line-delta (op + lengths + inserted bytes); `len()`. Measured
  primitive; the *system totals* built from it (C4/C5 active/cold disk) are
  **modeled**.
- **Materialized cache bytes / materialization latency**: `project` the manifest
  to a concrete tree; measure tree bytes and wall time. Measured primitive;
  C4/C5 "cache alive/evicted" composition is **modeled**.
- **Publish / snapshot / read / project latency**: time the real
  `publish_validated_changes` / `acquire_snapshot` / `read_bytes` / `project`.
  Measured. Merge/sidecar deltas (C2/C3) measured by adding the real merge +
  sidecar write into the timed section. Real overlay *mount* latency is Linux-
  only → noted, projection used as proxy (§1.5).
- **Provenance query latency**: read + parse one sidecar and answer "origin of
  line L". Measured.
- **Conflict correctness**: run diff3 on the case inputs; assert
  Clean/Conflict matches intent; assert the real `publish_validated_changes`
  rejects with `SourceConflict` where expected. Measured.
- **Line attribution correctness**: run the provenance builder on known inputs;
  assert each output line's origin equals the expected label. Measured.

Each latency is the median of repeated iterations (count in Appendix A); byte
metrics are exact.

---

## 6. Results

All numbers from the run in [Appendix A](#appendix-a-commands-and-environment)
(Apple M3 Max, Darwin 25.4.0, rustc 1.96.0; APFS dev host). Byte metrics are
exact; latencies are p50 over the iteration counts in the harness.
**"(modeled)"** = computed from measured primitives for an unimplemented design.

The diff engine self-check (apply-diff round-trips on 5 shapes) **PASSED** before
any sizing, so patch/merge numbers rest on a verified diff.

### 6.1 Storage — single edit (measured committed vs modeled patch/sidecar)

| Case | Source file | Committed layer bytes (C1/C2/C3) | Provenance sidecar (C3) | Patch bytes (modeled, C4) | Note |
|------|------------:|---------------------------------:|------------------------:|--------------------------:|------|
| B1 small, tiny edit | 17 B | **41** | 329 (1 range) | 16 | sidecar **8×** the patch and content; fixed JSON overhead dominates tiny files |
| B2 large, 1-line edit | 1,072,000 | **2,143,961** (≈ 2× file) | 339 (3 ranges) | **33** | one line edit **doubles** committed bytes; patch is 33 B (~65,000× smaller) |
| B3 large, 40 scattered edits | 1,072,000 | **2,142,230** | 5,338 (81 ranges) | 1,071 | merge clean; patch ≈ 0.1 % of the full copy |
| B9 minified, 1 token appended | 338,893 | 338,899 | — | **338,899** | single-line file: patch ≈ **full file** (no patch benefit) |

`committed ≈ 2× file` in B2/B3 because the base layer keeps the original copy and
the new layer adds a full merged copy — **the superseded copy is never reclaimed**
(no compaction; §1.1).

### 6.2 Latency — publish / read / project (measured, p50)

| Case | Publish | Read (`read_bytes`) | Project (realize tree) | Notes |
|------|--------:|--------------------:|-----------------------:|-------|
| B1 small | 39.6 ms | 45 µs | 990 µs | publish is **fsync-dominated**, not content-dominated (17 B file) |
| B2 large 1-line | 74.4 ms | — | — | +35 ms vs B1 ≈ cost of fsync-ing a 1 MiB layer file |
| B5 500 files, 1 publish | 203 ms | — | — | per-file create+fsync overhead; merge/sidecar work is in-memory |
| B8 merged publish | 37.5 ms | — | — | C2 merge + full-file publish on top of active — same order as a normal publish |

Snapshot acquire (`acquire_snapshot`) is a manifest read + refcount; it does **no
content copy** and is sub-millisecond regardless of file size (B11 lease cycle
`active_count` 1→0 confirms refcount/cleanup correctness). Real overlay *mount*
is Linux-only and not measured here (§1.5).

### 6.3 Churn growth (B4) — the central result

~1.05 MiB file, K sequential single-line publishes. C1/C2/C3 = measured on-disk
`layers/` bytes; C4/C6 = modeled from measured patch sizes / final content.

| K | C1/C2/C3 committed (measured) | Manifest depth | C4 cold, evicted (modeled) | C4 active, lease pins cache (modeled) | **C6 cold, compacted (modeled)** |
|--:|------------------------------:|---------------:|---------------------------:|--------------------------------------:|---------------------------------:|
| 1  | 2,143,945 | 2  | 1,072,017 | 1,071,962 | 1,071,945 |
| 2  | 3,215,835 | 3  | 1,072,034 | 1,071,924 | 1,071,890 |
| 5  | 6,431,175 | 6  | 1,072,085 | 1,071,810 | 1,071,725 |
| 10 | 11,788,975 | 11 | 1,072,170 | 1,071,620 | 1,071,450 |
| 20 | 22,500,505 | 21 | 1,072,350 | 1,071,260 | 1,070,910 |
| 50 | **54,602,695** | 51 | 1,072,890 | 1,070,180 | **1,069,290** |

**C1 grows ≈ K × file (54.6 MB at K=50). C4-cold and C6-cold are both ≈ 1× file
(~1.07 MB) and essentially identical.** Patch storage's entire cold-disk win is
matched by compaction — without a patch format and without touching the mount
path. Manifest depth grows K+1 (drives §6.4 latency); compaction also resets it.

### 6.4 Depth cost (B10) — read & projection vs manifest depth (measured)

| Manifest depth | Read deepest path (p50) | Project whole tree (p50) |
|---------------:|------------------------:|-------------------------:|
| 2   | 62 µs    | 1.0 ms  |
| 6   | 177 µs   | 2.0 ms  |
| 21  | 497 µs   | 6.3 ms  |
| 51  | 1,279 µs | 15.4 ms |
| 101 | 1,763 µs | **31.9 ms** |

Read of a deep path scales ≈ linearly (~17 µs/layer of stat() probing);
projection scales ≈ linearly (~310 µs/layer). **Unbounded depth is a hot-path
latency tax that patch storage does *not* fix but compaction does.** (B11: hot vs
cold project of a 10-layer 1 MiB stack = 2.6 ms vs 3.0 ms — page cache is a minor
factor next to depth and copy volume.)

### 6.5 Correctness (B6–B9, B12) — all measured, all as expected

| Case | Expectation | Result |
|------|-------------|--------|
| B6 mixed source+ignored | publish together atomically | source=1, ignored=1, one layer ✅ |
| B7 overlapping edits | merge Conflict **and** real OCC rejects | conflict=true, `SourceConflict`=true ✅ |
| B8 non-overlapping concurrent | C1 false-rejects; C2 merges cleanly, both edits land | C1 rejected=true, C2 merged & both edits present=true ✅ |
| B9 binary (NUL) / invalid UTF-8 | merge **Ineligible**, never transformed | ineligible=true, invalid-utf8 not text=true ✅ |
| B12 provenance | `original` / `workspace_session:*` / `mixed` exact | unchanged=original, active-edit=ws-active, identical-edit=mixed, inserted=ws-cmd ✅ |

### 6.6 Provenance cost (B5) — a real refinement signal

500 small files in one publish: committed content **35,280 B**, but
sidecar JSON total **142,500 B** — **4× the content**. The ~285 B fixed JSON
overhead per file dominates when files are small and have a single
(whole-file) range. **Design implication:** write a sidecar only for writes that
actually merged or produced >1 provenance range; attribute single-range
whole-file writes wholesale to the publishing session (a per-layer default
origin), as the README already allows for ignored paths. This keeps H4 true in
aggregate.

### 6.7 Hypothesis verdicts

| # | Hypothesis | Verdict | Evidence |
|---|-----------|---------|----------|
| H1 | Full-file fastest for hot startup | **Held** | Full-file layers mount directly; the only hot costs are read/project, which are flat per layer (B10) — a patch backend would add a materialization step before mount (G2). Publish is fsync- not content-bound (B1 vs B2). |
| H2 | Patch saves cold disk only when files large + edits small + caches evictable | **Held** | Patch = 33 B for a 1 MiB 1-line edit (B2) but ≈ full file for a minified/single-line file (B9) and 16 B vs 329 B sidecar for a tiny file (B1). |
| H3 | Patch *increases active* disk (patches + caches) | **Held, with nuance** | vs a single-copy/compacted baseline, C4 active = Σpatch + cache > C6's 1× file (B4, B11). vs *uncompacted* C1 (K×file) C4 is smaller — so the honest comparison is C4 vs **C6**, and C6 wins active disk. |
| H4 | Sidecars small for text, ~0 for binary | **Held per-file; FAILS in aggregate for many tiny files** | 339 B for a 1 MiB merge (B2); 0 for binary (B9, skipped). But B5: 285 B/file × 500 = 142 KB ≫ 35 KB content. Mitigation in §6.6. |
| H5 | Hybrid beats pure patch | **Held but moot** | B9/B1 show pure patch is bad for small/minified, so a threshold would help — but compaction (C6) dominates patch storage outright for these workloads, so the hybrid's extra machinery is unjustified. |
| H6 | Compaction reclaims most of what patches target, lower risk, keeps mount contract | **Held — decisive** | B4: C6-cold ≈ C4-cold ≈ 1× file at every K; C6 also caps depth and fixes B10 latency, which patches do not, while keeping concrete directly-mountable layers (G1/G2). |

### 6.8 Limitations

- C4/C5/C6 **system totals are modeled** from measured primitives (real diff/patch
  byte sizes, real `project` copy cost, real final content size), not produced by
  a running patch/compaction backend.
- Overlay **mount** latency is Linux-only and not measured; `project`
  (realize-concrete-tree) is the portable proxy and is *also* exactly the work a
  patch materializer would add to the hot path.
- The harness diff/diff3/patch codec is a verified-but-simple Myers line diff +
  minimal binary line-delta. A production codec (xdelta/bsdiff + compression)
  could move patch bytes by a constant factor, not the order of magnitude
  (patch ≈ changed bytes, not file size) — and B9 shows the regime where even
  that breaks down (single-line files).
- Latencies are dev-host APFS; absolute fsync costs differ inside a container on
  the daemon's storage, but the *relative* ordering (depth tax, fsync-bound
  publish, flat snapshot) is structural.

---

### 6.9 Complexity model (time / space)

Notation: `F` edited-file bytes; `L` lines (`L≤F`); `δ` changed lines (`δ≤L`);
`p` path-component depth (~3–5, ~constant); `D` manifest depth (layers);
`K` sequential publishes to one file (`D≈K+1`); `N` files in one publish;
`T` total committed bytes across all `D` layers (for one churned file `T≈K·F`);
`P` provenance ranges (`P≈δ+1`). All derived from the code paths
`read_entry`, `project`, `publish_layer_unlocked`, and the harness Myers+diff3.

**Per-operation time complexity**

| Operation | C1 | C2 | C3 | C4 patch | C5 hybrid | C6 compaction |
|-----------|----|----|----|----------|-----------|---------------|
| Publish (1 source write) | `O(D·p + F)` | `+O(L·δ)` on mismatch only | `+O(L·δ)+O(δ)` sidecar, +1 fsync | `O(D·p+F+L·δ)`, write `O(δ)` patch **+ `O(F)` cache** | max(C3,C4) by policy | = C3 |
| Read 1 path (top layer) | `O(p+F)` | `O(p+F)` | `O(p+F)` | `O(F)` **iff cache exists** | full: `O(p+F)` | `O(p+F)` |
| Read 1 path (deep) / mount | `O(D·p+F)` | `O(D·p+F)` | `O(D·p+F)` | **`O(F+Σδ)` materialize** | patch: `O(F+Σδ)` | `O(p+F)` (D bounded) |
| Project whole tree | `O(T+E)` | `O(T+E)` | `O(T+E)` | `O(F+Σδ)` reconstruct + write | mix | `O(F_vis+E_vis)` |
| Snapshot acquire | `O(D)` | `O(D)` | `O(D)` | `O(D)` | `O(D)` | `O(D)`, small `D` |
| Merge | — | `O((L+δ)·δ)` | `O((L+δ)·δ)` | `O((L+δ)·δ)` | `O((L+δ)·δ)` | `O((L+δ)·δ)` |
| Provenance build / query | — | — | `O(L+δ)` / `O(P)` | from patch | `O(L+δ)`/`O(P)` | `O(L+δ)`/`O(P)` |
| Compaction (amortized/publish) | — | — | — | — | — | `O(T)` bg → `O(F)` amortized |

Merge is `O(L)` time/space for typical `δ=O(1)`, degrading to `O(L²)` only when
`δ→L` (capped by `MYERS_MAX_D`, then full-file fallback). The harness keeps the
Myers trace, so merge **space** is `O((L+δ)·δ)` → `O(L)` for small edits.

**Space / storage after `K` edits to one `F`-byte file**

| Quantity | C1 / C2 | C3 | C4 patch | C5 hybrid | C6 compaction |
|----------|---------|----|----------|-----------|---------------|
| Committed (active) | `Θ(K·F)` | `Θ(K·F + Σδ)` | `Θ(F + Σδ)` **+ `Θ(F)`/live lease cache** | `Θ(F+Σδ)`…`Θ(K·F)` | pre: `Θ(K·F)` |
| Cold (after cleanup) | `Θ(K·F)` | `Θ(K·F + Σδ)` | `Θ(F + Σδ)` | `Θ(F+Σδ)`…`Θ(K·F)` | post: **`Θ(F + Σδ)`** |
| Manifest depth | `Θ(K)` | `Θ(K)` | `Θ(K)` | `Θ(K)` | **`Θ(1)`** |
| Provenance | — | `Θ(Σδ)` **+ `Θ(N)` fixed/file** | (from patches) | `Θ(Σδ)` | `Θ(Σδ)` |

**Measured constants behind the big-O** (from §6, this host):

- Publish floor (1 fsync) ≈ **37–40 ms**; +bandwidth ≈ **+35 ms / MiB** (B1/B2);
  per-file ≈ **0.4 ms** (B5, 500 files → 203 ms).
- Read scales **~17 µs / layer** (B10: 62 µs→1.76 ms over D=2→101) — the `O(D·p)`.
- Project scales **~310 µs / layer** (B10: 1.0→31.9 ms) — the `O(T)`; 10×1 MiB ≈ 3 ms (B11).
- Merge of a small file is **below the publish fsync floor** (B8 merged publish 37.5 ms ≈ a normal publish); diff3 on a 16 k-line file is sub-ms vs the 74 ms publish.
- Provenance ≈ **66 B / range**, `O(δ)` not `O(F)` (B2 3 ranges/339 B; B3 81/5338 B); query sub-µs.
- Storage: C1 `Θ(K·F)` exact (2.14→54.6 MB, K=1→50); C4-cold/C6-cold flat ≈ 1× F (B4).

**Dominant bottleneck per design**

- **C1/C2** — hot path is cheap (`O(p+F)` read, direct mount); bottleneck is
  storage `Θ(K·F)` and the `O(D)`/`O(T)` read+project **tax that grows with churn**.
- **C3** — same, plus `O(δ)` sidecar + 1 fsync; watch the `Θ(N)`/file sidecar
  overhead for many tiny files (B5).
- **C4** — smallest cold storage `Θ(F+Σδ)`, but read/**mount** regresses to
  `O(F+Σδ)` reconstruction and active disk gains `Θ(F)` per live lease — the
  hot-path/universality cost (G2).
- **C5** — tunable Pareto point, bottleneck is two code paths + a threshold to
  tune/observe.
- **C6** — caps **both** depth (`Θ(1)`) and cold storage (`Θ(F+Σδ)`); bottleneck
  is the background compaction pass `O(T)` (amortized `O(F)`/publish) and its
  atomicity/lease-safety logic.

This is why the ranking holds: C4's `Θ(F+Σδ)` cold win is matched by **C6** with
no hot-path reconstruction, while C6 *additionally* removes the `O(D)`/`O(T)`
growth that C1–C4 all leave on the read/project path.

## 7. Evaluation (weighted scoring)

Gates **G1–G4** are pass/fail. Among gate-passing designs, score the soft
criteria. Weights reflect the directive order (correctness/auditability first,
then hot-path latency, then disk, then risk/complexity) **and** the user
constraint that mount-consistency + universality are non-negotiable.

| Criterion | Weight |
|-----------|-------:|
| Correctness & auditability | 0.30 |
| Hot-path latency (snapshot+read; mount universality) | 0.25 |
| Active disk | 0.15 |
| Cold disk | 0.12 |
| Implementation risk | 0.10 |
| Operational complexity | 0.08 |

### 7.1 Gate results

| Design | G1 mount-contract | G2 universal mount | G3 atomicity | G4 byte-exact | Passes gates? |
|--------|:--:|:--:|:--:|:--:|:--:|
| C1 baseline | ✅ | ✅ | ✅ | ✅ | yes |
| C2 merge | ✅ | ✅ | ✅ | ✅ (B7/B9) | yes |
| C3 merge+provenance | ✅ (sidecars not mounted) | ✅ | ✅ (sidecar staged/rolled-back with layer) | ✅ (B12) | yes |
| **C4 patch-backed** | ⚠️ only via a synthesized cache layer | ❌ **needs per-snapshot materialization before mount** | ⚠️ cache must join digest+cleanup | ✅ | **no (fails G2)** |
| **C5 hybrid** | ⚠️ for the patch path | ❌ same as C4 for patch-stored files | ⚠️ | ✅ | **no (fails G2 on patch path)** |
| C6 merge+provenance+compaction | ✅ (squash output is a concrete layer) | ✅ | ⚠️ compaction needs its own atomic swap + lease safety | ✅ | yes |

C4/C5 fail **G2**: a patch layer cannot be handed to overlayfs as a lowerdir, so
every snapshot/mount on an arbitrary image must first materialize a concrete
cache tree and keep it durable for the lease — exactly the per-mount,
per-environment work the user constraint forbids ("universal to any docker image
envs", "consistent with layerstack mount core behaviour"). They are excluded
from the weighted comparison below.

### 7.2 Weighted scores (gate-passing designs; 0–1, higher better)

| Criterion (weight) | C1 | C2 | C3 | **C3+C6** |
|--------------------|---:|---:|---:|----------:|
| Correctness & auditability (0.30) | 0.20 | 0.60 | 0.95 | 0.95 |
| Hot-path latency + mount universality (0.25) | 1.00 | 0.98 | 0.95 | 0.97 |
| Active disk (0.15) | 0.40 | 0.40 | 0.40 | 0.85 |
| Cold disk (0.12) | 0.30 | 0.30 | 0.30 | 0.90 |
| Implementation risk (0.10) | 1.00 | 0.80 | 0.70 | 0.55 |
| Operational complexity (0.08) | 1.00 | 0.90 | 0.70 | 0.55 |
| **Weighted total** | **0.586** | **0.673** | **0.745** | **0.862** |

Ranking: **C3+C6 (0.862) > C3 (0.745) > C2 (0.673) > C1 (0.586)**; C4/C5 excluded
by gate G2.

---

## 8. Recommendation

### 8.1 Winning design

**Ship C3 now; adopt C6 as a measured-trigger follow-on. Reject C4/C5.**

- **Phase 1 — C3: three-way line merge + full-file concrete layers + provenance
  sidecars.** This is the README's proposal, and it wins the publish-time
  decision: it removes false `SourceConflict` rejections for non-overlapping
  edits (B8, measured end-to-end), preserves exact bytes and correct rejection
  for overlaps/binary/invalid-UTF-8 (B7/B9), produces correct line provenance
  (B12), and changes **nothing** about how layers are mounted — every committed
  layer stays a concrete tree (G1/G2). Apply the §6.6 refinement: emit a sidecar
  only for writes that merged or have >1 range; attribute single-range
  whole-file writes wholesale to the publishing session.

- **Phase 2 — C6: cold-layer compaction.** The measured storage/latency problem
  is *retained superseded full-file layers and unbounded manifest depth* (B4:
  54.6 MB for 50 edits of a 1 MiB file; B10: 32 ms projection at depth 101), not
  the absence of patches. A background pass that squashes cold lower layers into
  one concrete layer reclaims that disk (B4: C6-cold ≈ 1× file, equal to patch
  storage) **and** caps depth (fixing B10), while keeping layers directly
  mountable. It is the highest-scoring design but carries the most risk, so it is
  a deliberate second phase gated on measured pressure.

### 8.2 Why it wins (evidence)

1. **It satisfies the hard user constraints.** C3 and C6 keep every published
   layer a concrete, directly-mountable overlay lowerdir — universal across any
   Docker base image, identical to today's mount core. C4/C5 cannot (G2).
2. **Compaction dominates patch storage on patch storage's own headline
   metric.** B4 shows C6-cold and C4-cold both collapse to ~1× file at every K;
   compaction reaches it with no new on-disk format, no materializer, no
   cache-invalidation/lease-retention machinery, and it *additionally* fixes the
   depth latency tax (B10) that patches leave in place.
3. **Patch storage's costs are real and measured.** No benefit for minified /
   single-line / small files (B9, B1), higher active disk than compaction (H3),
   and a hot-path materialization step that violates universality.
4. **The merge/provenance value is real and measured.** B8 turns a false
   conflict into a clean two-sided merge; B12 attributes every line correctly.

### 8.3 When to revisit (invalidation criteria)

Revisit **C6→C4/C5 (patch storage)** only if *all* of these hold in production
telemetry:
- compacted cold disk (≈ newest content × live files) is still the dominant cost
  driver, **and**
- the workload is large-file / small-edit / high-churn (B2/B4 regime, not B9), **and**
- there is a hard requirement to reconstruct arbitrary *historical* file versions
  (not just line attribution, which sidecars already give), **and**
- a design exists that keeps a concrete materialized cache satisfying G1/G2 with
  bounded active-disk overhead.

Revisit **C3 sidecar policy** if provenance bytes exceed content bytes in
aggregate (B5 regime) — tighten the "merged or >1 range" emit rule further.

Re-run this harness on the **Linux daemon storage** to replace projection-proxy
numbers with real overlay-mount latency before committing to C6's depth-cap
thresholds.

### 8.4 First implementation step

Land the internal merge boundary **with no publish wiring yet**:
`crates/sandbox-runtime/layerstack/src/stack/publish/merge.rs` exposing
`three_way_merge(base, active, command) -> Clean(Vec<u8>) | Conflict | Ineligible`,
built on a Myers line diff + diff3 reconciliation (the exact algorithm validated
in the harness, including the apply-diff round-trip property test). Cover it with
the unit cases B7/B8/B9/B12 (overlap→conflict, disjoint→clean, binary→ineligible,
attribution). This is the lowest-risk first brick and is a shared dependency of
both C3 and C6. Only after it is green, wire `resolve_publish_changes` into
`publish_validated_changes` per the README implementation plan.

---

## Appendix A: Commands and environment

**Host:** Apple M3 Max · Darwin 25.4.0 (arm64) · APFS · `rustc 1.96.0`.

**Harness:** `crates/sandbox-runtime/layerstack/tests/occ_merge_bench.rs`
(integration test, `#[ignore]`-gated, drives the real public layerstack API).
Generated layerstack roots and projection trees go under `$TMPDIR/occ-bench-*`
and are removed on fixture drop.

**Commands run:**

```sh
export PATH="$PWD/bin:$PATH"

# compile only
cargo test -p sandbox-runtime-layerstack --test occ_merge_bench --no-run

# run the experiment (prints markdown summary + writes results JSON)
cargo test -p sandbox-runtime-layerstack --test occ_merge_bench \
  -- --ignored --nocapture

# lint gate
cargo clippy -p sandbox-runtime-layerstack --all-targets
```

**Iteration counts (p50):** B1 publish ×25, read ×50, project ×25; B10 read ×30,
project ×15; B11 cold/hot project ×10. Byte metrics are exact single
measurements. Raw JSON: `$TMPDIR/occ_merge_bench_results.json` (reproduced inline
in §6).

**Measured vs modeled, explicitly:**

| Metric | Status |
|--------|--------|
| Committed layer bytes (C1/C2/C3), all B-cases | **measured** (on-disk `layers/`) |
| Publish / read / project / snapshot latency, lease refcount | **measured** (real API) |
| Provenance sidecar bytes + query, attribution correctness | **measured** |
| Conflict/rejection correctness (B6/B7/B8/B9) | **measured** (real `publish_validated_changes`) |
| Patch bytes (C4) | **measured primitive** (real Myers diff + line-delta codec) |
| Materialized cache bytes / materialization latency | **measured primitive** (real `project`) |
| C4 active/cold totals, C6 compacted totals | **modeled** from the above primitives |
| Real overlay **mount** latency | **not measured** (Linux-only); projection used as proxy |
