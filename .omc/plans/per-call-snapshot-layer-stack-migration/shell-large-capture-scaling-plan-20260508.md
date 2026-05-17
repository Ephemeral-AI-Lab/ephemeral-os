# Shell Large-Capture Scaling — Phase 2 Plan

**Date:** 2026-05-08
**Branch:** codex/fix-dot-path-normalization-tests
**Predecessor:** `.omc/plans/per-call-snapshot-layer-stack-migration/shell-concurrency-phase1-implementation-report-20260508.md`
**Goal:** Make `api.shell` performant for arbitrary capture sizes — `npm install`, `pip install`, `cargo build`, build artefact dumps — without presuming the workload's path set.

---

## 0. Why this exists

Phase 1 optimised `api.shell` for tiny captures (1–2 path changes, the load-matrix workload). Real-world shell calls produce captures **orders of magnitude larger**:

| Workload | Approx files captured | Approx bytes |
|---|---|---|
| `printf X > path` (Phase 1 benchmark) | 1–2 | ≤1 KB |
| `git apply patch.diff` | 5–500 | ≤10 MB |
| `pip install requests` | ~80 | ≤30 MB |
| `npm install <small-pkg>` | 1K–5K | 50–200 MB |
| `npm install` (full repo) | 30K–80K | 200–600 MB |
| `cargo build --release` | 5K–20K | 200–1500 MB |

Phase 1's `occ.apply.commit_s` p99 of 250 ms holds because the path set per call is ≤2. A `npm install` shell would explode `commit_s` linearly: every captured file flows through `_LayerChangeStager.write()` which is **O(N) serial sync work** — read bytes → SHA-256 hash → write to staging dir → record manifest entry, all single-threaded on the OCC commit lane.

**The plan's premise:** Phase 2 must make capture+commit cost **scale with bytes-on-disk, not with file count × Python-per-file overhead**, and must do so without presuming which paths are tracked vs gitignored.

---

## 1. Constraints — what we cannot presume

This is a generic-shell plan. We must NOT special-case:

- `.gitignore` patterns (pip uses `.venv`, npm uses `node_modules`, but the user could also have `dist/`, `build/`, `__pycache__/`, custom output dirs, monorepo workspaces)
- file-name extensions
- file-count thresholds (npm-install scales smoothly into millions of files in mono-repos)
- workspace contents (could be empty, could be 10 GB at start)
- the specific subprocess (the runtime cannot inspect `argv[0]` to gate its behaviour)
- subprocess exit code (we capture even from failed commands)

**The fix has to be a property of the OCC + capture pipeline itself, not a heuristic per shell command.**

---

## 2. Suspected bottleneck map (V3D-derived, qualitative)

| Component | Per-file cost | Scaling | Suspected at K=10K files |
|---|---|---|---|
| `command_exec.run_command_s` | n/a (subprocess does this work) | constant in runtime | drops outside the runtime's scope |
| `command_exec.capture_upperdir_s` | walk overlay upperdir, **read bytes per file into Python** | O(N + bytes) | seconds |
| `occ.prepare.group_by_route_s` | gitignore-match per path | O(N) but cache-hot | hundreds of ms |
| `occ.prepare.prepare_groups_s` | base-hash RPC per OCC_GATED path | O(N_gated), small | usually small (gitignored bulk skips this) |
| `occ.apply.commit_queue_wait_s` | wait on serial commit lane | O(c) | bounded |
| **`occ.apply.commit_s`** (= `_LayerChangeStager.write` × N) | **read bytes → hash → write staging file → manifest entry, per file, single-threaded** | **O(N + bytes)** | **dominant — many seconds** |
| `command_exec.release_snapshot_s` | rmtree of transient lowerdir | O(N + bytes) | bounded by hardlink cleanup |
| Manifest publish | one atomic write of the manifest JSON | O(N) JSON-encoding | seconds for K=10K |

The two suspects: `capture_upperdir_s` and `commit_s`. Both are O(N + bytes), both currently single-threaded, both byte-copy through Python memory.

---

## 3. Hypotheses

| H# | Hypothesis | Discriminating timing |
|---|---|---|
| **H1** | `_LayerChangeStager.write` is the dominant cost for large K because it byte-copies into staging | `occ.apply.commit_s / N` measured at K=1, 100, 1K, 10K |
| **H2** | `capture_workspace_upperdir` is the second-largest cost because it reads bytes through Python | `command_exec.capture_upperdir_s / N` at K=1, 100, 1K, 10K |
| **H3** | The overlay upperdir is **already on the same filesystem as layer storage**, so files can be hardlinked into the layer instead of byte-copied | `os.stat(upperdir_file).st_dev == os.stat(layer_dir).st_dev` |
| **H4** | Manifest JSON serialisation is small relative to file I/O at K=10K | `occ.commit.publish_layer_s` |
| **H5** | `group_by_route_s` (gitignore matching) is sub-linear in N because of cache hits | gitignore cache hit rate at K=10K |
| **H6** | Bytes-hashing is CPU-bound; can be amortised by streaming hash via `hashlib.file_digest` instead of `hash_bytes(read())` | `_LayerChangeStager.write` vs new `link_or_write` |

---

## 4. Phase 2.1 — Add a K-scaling benchmark *(diagnose first, do not optimise blind)*

### 4.1 New files

| File | Purpose |
|---|---|
| `backend/tests/live_e2e_test/sandbox/layer_stack_overlay_occ/test_phase06_large_capture_scaling.py` | The new benchmark test |
| `backend/tests/live_e2e_test/_harness/large_capture_workload.py` | Helper that generates K-file shell commands |

### 4.2 Test signature (drop-in)

```python
@pytest.mark.parametrize("k", [1, 100, 1000, 10_000])
@pytest.mark.parametrize("prefix", ["tracked/load/k_capture", ".venv/k_capture"])
async def test_shell_large_capture_emits_per_file_timings(
    workspace_base_sandbox: SandboxHandle,
    k: int,
    prefix: str,
) -> None:
    handle = workspace_base_sandbox
    binding = await seed_phase05_imported_base(handle)
    command = (
        f"set -e; mkdir -p {prefix}; "
        f"for i in $(seq 1 {k}); do "
        f"  printf 'k=%d i=%d\\n' {k} $i > {prefix}/file_$(printf %06d $i).bin; "
        f"done"
    )
    metric = await timed_call(
        f"phase06.large_capture.{prefix.replace('/', '_')}.k{k}",
        handle.tool.shell(command, timeout=300, description=f"k_capture k={k}"),
    )
    result, runtime = metric
    assert result.success, result
    timings = result.timings
    emit_metric(
        f"phase06.large_capture.{prefix.replace('/', '_')}.k{k}",
        {
            "wall_ms": runtime.wall_ms,
            "k": k,
            "prefix": prefix,
            "capture_upperdir_s": timings.get("command_exec.capture_upperdir_s", 0.0),
            "occ_apply_s": timings.get("command_exec.occ_apply_s", 0.0),
            "commit_s": timings.get("occ.commit.total_s", 0.0),
            "validate_groups_s": timings.get("occ.commit.validate_groups_s", 0.0),
            "publish_layer_s": timings.get("occ.commit.publish_layer_s", 0.0),
            "stager_write_total_s": timings.get(
                "occ.commit.stager_write_total_s", 0.0
            ),
            "commit_per_file_us": (
                timings.get("occ.commit.total_s", 0.0) * 1_000_000 / max(k, 1)
            ),
            "capture_per_file_us": (
                timings.get("command_exec.capture_upperdir_s", 0.0) * 1_000_000 / max(k, 1)
            ),
        },
    )
```

The matrix runs at `c=1` only; concurrency is Phase 1's domain. Phase 2 isolates per-call scaling vs K.

### 4.3 New instrumentation key

Add `occ.commit.stager_write_total_s` (sum of every `_LayerChangeStager.write` call's wall) inside `_LayerChangeStager`:

```python
# backend/src/sandbox/occ/commit_transaction.py — _LayerChangeStager.write
def write(self, path: str, content: bytes) -> LayerChange:
    start = time.perf_counter()
    try:
        # … existing body …
    finally:
        self._write_total_s += time.perf_counter() - start
```

And expose `_write_total_s` in the timings dict from `revalidate_and_publish`.

### 4.4 Success criteria for Phase 2.1

- The benchmark emits a JSONL artifact under `.omc/results/phase06-large-capture-scaling-*.jsonl`.
- 8 rows: `K ∈ {1, 100, 1K, 10K} × prefix ∈ {tracked, gitignored}`.
- Each row carries the keys above and is parseable by the analysis snippet in §5.

### 4.5 Run command

```
.venv/bin/pytest backend/tests/live_e2e_test/sandbox/layer_stack_overlay_occ/test_phase06_large_capture_scaling.py -xvs
```

### 4.6 Analysis snippet for Phase 2.2

```python
import json
from pathlib import Path
ART = sorted(Path(".omc/results").glob("phase06-large-capture-scaling-*.jsonl"))[-1]
rows = [json.loads(line) for line in ART.read_text().splitlines() if line.strip()]
print(f"{'prefix':18s} {'k':>6} {'capture_us':>12} {'commit_us':>12} "
      f"{'capture_s':>10} {'commit_s':>10} {'wall_s':>8}")
for r in rows:
    if "k" not in r: continue
    print(f"{r['prefix']:18s} {r['k']:>6} {r['capture_per_file_us']:>12.1f} "
          f"{r['commit_per_file_us']:>12.1f} {r['capture_upperdir_s']:>10.2f} "
          f"{r['commit_s']:>10.2f} {r['wall_ms']/1000:>8.2f}")
```

---

## 5. Phase 2.2 — Read the data, pick a lane

### 5.1 Decision matrix (concrete thresholds)

After Phase 2.1 emits the JSONL, compute four numbers from the K=10K rows:

| Symbol | Definition | How |
|---|---|---|
| `C_per_file` | Capture cost per file (μs) at K=10K | `capture_upperdir_s × 1e6 / 10_000` |
| `S_per_file` | Stager-write cost per file (μs) at K=10K | `stager_write_total_s × 1e6 / 10_000` |
| `S_growth` | Per-file cost growth ratio K=10K vs K=100 | `S_per_file(K=10K) / S_per_file(K=100)` |
| `M_pct` | Manifest publish fraction of total commit | `publish_layer_s / commit_s` at K=10K |

### 5.2 Lane selection table

| Outcome | Diagnostic | Implication | Lane |
|---|---|---|---|
| `S_per_file > C_per_file` AND `S_growth ≤ 1.2x` | Stager dominates, scaling is linear | Replace byte-copy with hardlink in stager | **Lane A** |
| `C_per_file ≥ S_per_file` AND `C_per_file > 100 μs` | Capture pipeline dominates | Stream capture-to-layer (eliminate Python list) | **Lane B** |
| `S_growth > 1.5x` OR `M_pct > 0.3` | Per-file cost grows super-linearly OR manifest publish is heavy | Switch to delta-only manifests | **Lane C** |

We commit to **one** lane based on the data. We do not pre-implement two.

### 5.3 Mandatory advisor checkpoint

Before writing any Lane code, call `advisor()` with:
- The Phase 2.1 artifact path
- The four computed numbers above
- The selected Lane

This is the same pattern Phase 1 used to avoid Lane B / C / D mistakes. **Do not skip the checkpoint.**

---

## 6. Lane A — Stager-direct hardlink (most likely landing spot)

### 6.1 Comparison of approaches inside Lane A

| Sub-approach | Mechanism | Pro | Con |
|---|---|---|---|
| **A1 — `os.link` upperdir → staging** | Hardlink from per-call upperdir into staging dir; staging file is then atomically moved into layer dir | Zero byte copy; same-FS guaranteed via `storage_root`; smallest diff | Requires plumbing `source_upperdir_path` through OverlayPathChange → WriteChange → stager |
| **A2 — `os.link` upperdir → layer dir directly** | Skip staging entirely; hardlink upperdir file straight into the new layer dir | Saves one inode rename | Breaks atomicity — layer is half-built if commit aborts mid-loop; reintroduces failure-path complexity |
| **A3 — `reflink` (CoW) instead of hardlink** | `os.copy_file_range` with `REFLINK` flag on btrfs/XFS | Caller can mutate without affecting source | Filesystem-specific; not portable; defeats the point (we want shared inodes for immutability anyway) |
| **A4 — Memory-map + chunked hash** | Keep byte-copy but use `mmap` and write+hash in parallel | No new types | Doesn't reduce I/O bandwidth; per-file overhead similar |

**Pick A1.** It mirrors Phase 1's `MergedView.materialize(link_ok=True)` pattern and is the smallest blast-radius change.

### 6.2 Code change sketch

#### 6.2.1 `OverlayPathChange` gains `source_upperdir_path`

`backend/src/sandbox/overlay/capture/changes.py`:

```python
@dataclass(frozen=True)
class OverlayPathChange:
    path: str
    kind: Literal["write", "delete", "symlink"]
    content: bytes | None
    source_upperdir_path: str | None  # NEW: filesystem source for hardlinking
    symlink_target: str | None = None
```

#### 6.2.2 `capture_changes` populates the new field

`backend/src/sandbox/overlay/capture/upperdir.py`:

```python
def capture_changes(upperdir, *, snapshot_manifest, lowerdir=None, workspace_root=None, timings):
    # … existing rglob walk …
    for entry in entries:
        if entry.is_file():
            yield OverlayPathChange(
                path=rel_path,
                kind="write",
                content=None,                              # was: entry.read_bytes()
                source_upperdir_path=str(entry),           # NEW
            )
```

The `content` field becomes optional and is only populated for in-memory edge cases (tests, or paths the stager can't link from).

#### 6.2.3 `WriteChange` exposes a source path

`backend/src/sandbox/occ/changeset/types.py`:

```python
@dataclass(frozen=True)
class WriteChange(Change):
    path: str
    final_content: bytes | None        # was: bytes — now optional
    source_path: str | None = None     # NEW: filesystem source for hardlinking
    base_hash: str | None = None
    create_only: bool = False
    source: ChangeSource = "api_write"
```

#### 6.2.4 `_LayerChangeStager` gains `link_or_write`

`backend/src/sandbox/occ/commit_transaction.py`:

```python
class _LayerChangeStager:
    def link_or_write(
        self,
        path: str,
        *,
        source_path: str | None,
        fallback_content: bytes | None,
    ) -> LayerChange:
        if self._staging_path is None:
            raise RuntimeError("OCC layer-change stager is not active")
        self._counter += 1
        target = self._staging_path / f"{self._counter:06d}.bin"

        # Linked path: zero byte copy.
        if source_path is not None:
            try:
                os.link(source_path, target)
                content_hash = self._hasher.hash_file(target)  # streaming hash
                return LayerChange(
                    path=path,
                    kind="write",
                    content_hash=content_hash,
                    source_path=str(target),
                )
            except OSError as exc:
                if exc.errno not in (errno.EXDEV, errno.EPERM):
                    raise
                # cross-FS or permission → fall through to byte-copy

        # Fallback: byte-copy (in-memory edits, cross-FS).
        if fallback_content is None:
            raise RuntimeError(
                f"stager has no source_path and no fallback_content for {path}"
            )
        target.write_bytes(fallback_content)
        return LayerChange(
            path=path,
            kind="write",
            content_hash=self._hasher.hash_bytes(fallback_content),
            source_path=str(target),
        )
```

Keep the existing `write(path, content)` method as a thin shim so callers that only have bytes (api.write_file → in-memory string content) keep working.

#### 6.2.5 `ContentHasher.hash_file` (streaming)

`backend/src/sandbox/occ/content/hashing.py`:

```python
class ContentHasher:
    def hash_bytes(self, content: bytes) -> str:
        return hashlib.sha256(content).hexdigest()

    def hash_file(self, source_path: str | Path) -> str:
        # Python 3.11+: hashlib.file_digest streams without buffering through Python.
        with open(source_path, "rb") as fh:
            return hashlib.file_digest(fh, "sha256").hexdigest()
```

#### 6.2.6 Capture wiring

`backend/src/sandbox/command_exec/capture/changeset.py`:

```python
def workspace_changes_to_occ_changes(path_changes):
    for change in path_changes:
        if change.kind == "write":
            yield WriteChange(
                path=change.path,
                final_content=change.content,                   # may be None
                source_path=change.source_upperdir_path,        # NEW
                source="overlay_capture",
            )
        # … delete / symlink unchanged …
```

#### 6.2.7 `DirectMerge.stage_group` / `GatedMerge.stage_group`

Both currently call `stage_write(path, content)`. Update to call `stage_write(path, source_path=..., fallback_content=...)`.

### 6.3 Why hardlinks are safe here

| Concern | Mitigation |
|---|---|
| Mutating the staged file mutates the source upperdir file | The staging dir is **read-only** post `link`; we only read it during publish. The upperdir is rmtree'd after release. |
| Mutating the published layer file mutates the source | Layers are **read-only** by contract (`MergedView` only reads). No code path writes into a published layer. |
| `os.link` requires same filesystem | `storage_root/runtime/transient-lowerdirs/...` (upperdir) and `storage_root/staging/` are both under `storage_root`; same FS unless user mounts split. EXDEV fallback covers the edge case. |
| Inode survives upperdir cleanup | POSIX: file data persists until refcount=0. With staging or layer holding a hardlink, upperdir rmtree is safe. |
| Source file disappears between capture and stager | Sequence: capture → stage (link) → publish → release_lease → drop_transient_lowerdir. The link is created BEFORE the upperdir is dropped. |

### 6.4 Lane A success criteria (concrete numbers)

| Metric | Target | Vs hypothesised baseline |
|---|---|---|
| `occ.apply.commit_s` p99 at K=10K (tracked) | ≤ 1.5 s | Baseline ≥10 s (linear in K via byte-copy) |
| `occ.apply.commit_s` p99 at K=10K (gitignored) | ≤ 1.5 s | Same |
| `commit_s_per_file_us` ratio (K=10K vs K=100) | ≤ 1.2× | Baseline grows ~10× (linear) |
| `command_exec.capture_upperdir_s` peak Python memory at K=10K | ≤ 50 MB | Baseline ~500 MB (every file's bytes in `OverlayPathChange.content`) |
| Phase 1 c20 shell throughput | ≥ 6.0 ops/s (no regression vs V3D 6.97) | Phase 1 V3D baseline |
| Phase 1 c20 write/edit/read/mixed | within ±10% of V3D | Phase 1 V3D baseline |

### 6.5 Lane A risks + mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| `os.link` fails with `EPERM` on some FUSE mounts | Low (storage_root is local) | High (commits would fail) | EXDEV/EPERM fallback to byte-copy already in §6.2.4 |
| `hashlib.file_digest` not available on Python < 3.11 | Zero (project pins 3.12) | n/a | n/a |
| Test fixtures construct `WriteChange(final_content=b'…')` without source_path | Medium | Low | Keep byte-copy fallback; don't remove it |
| Race between `os.link` and `_drop_transient_lowerdir` | Low | High (lost data) | Order-of-operations review: stager `link` MUST happen before `release_lease` triggers cleanup. Currently `commit_transaction.publish_layer` runs inside the lock; `release_lease` runs after; we're safe. Add a unit test asserting the order. |
| Hashing the linked file races with `_drop_transient_lowerdir` | Low | High | Same as above — link first, then hash from staging path (not upperdir path) so cleanup of upperdir doesn't matter |

### 6.6 Lane A files-touched matrix

| File | Lines changed (est.) | Risk |
|---|---|---|
| `backend/src/sandbox/overlay/capture/changes.py` | +1 field | Low |
| `backend/src/sandbox/overlay/capture/upperdir.py` | +1 line per yield | Low |
| `backend/src/sandbox/command_exec/capture/changeset.py` | +1 field | Low |
| `backend/src/sandbox/occ/changeset/types.py` | +1 field | Low |
| `backend/src/sandbox/occ/commit_transaction.py` (`_LayerChangeStager`) | +25 lines (`link_or_write`) | Med |
| `backend/src/sandbox/occ/content/hashing.py` | +5 lines (`hash_file`) | Low |
| `backend/src/sandbox/occ/direct/merge.py` | thread `source_path` through `stage_write` | Med |
| `backend/src/sandbox/occ/gated/merge.py` | same | Med |

---

## 7. Lane B — Stream capture-to-layer (only if Phase 2.2 attributes cost to capture)

### 7.1 Premise

Instead of `capture_workspace_upperdir` building a Python list of `OverlayPathChange`, walk the upperdir directly into the OCC stager as an iterator. Hardlink each file as we go. Eliminates the intermediate list and the double walk.

### 7.2 Design (defer detail until Phase 2.2 selects this lane)

- `capture_changes` returns an iterator instead of a sequence.
- `OccCommitTransaction.revalidate_and_publish` accepts an iterator of `Change` and drains it during the staging loop.
- Routing/grouping happens lazily — `OccOrchestrator._group_by_route` becomes a streaming groupby.

### 7.3 When to choose Lane B over Lane A

Only if `C_per_file > S_per_file` AND `C_per_file > 100 μs`. Phase 1 V3D capture per file ≈ 20 ms / 1 file = 20,000 μs (one path, full overhead) so this isn't comparable; we need K=10K data first.

### 7.4 Lane B is structurally a superset of Lane A

If Phase 2.2 picks Lane B, the Lane A hardlink mechanism is still inside it — just plumbed through an iterator instead of a list. So Lane A code is not wasted if Lane B becomes necessary later.

---

## 8. Lane C — Manifest delta (only if Phase 2.2 finds quadratic scaling)

### 8.1 Premise

If `S_growth > 1.5×` or `publish_layer_s` is large, the suspect is `Manifest` rewrites. Currently `Manifest.to_dict()` rewrites the entire manifest atomically per commit. For K=10K that's a 10K-entry JSON written per commit, a ~5MB JSON file written on every shell call.

### 8.2 Approach (defer detail until Phase 2.2 selects this lane)

| Sub-approach | Mechanism |
|---|---|
| **C1 — Append-only manifest log** | Each commit appends a delta record (added/removed/changed paths); periodic compaction folds N deltas into one snapshot |
| **C2 — Per-layer manifest sharding** | Manifest references a list of layer JSON files; each layer's file is small and rarely rewritten |
| **C3 — Binary manifest format** | Replace JSON with a binary format (msgpack, protobuf) for smaller serialisation cost |

### 8.3 Lane C is the heaviest

Larger structural change; only consider once Lane A demonstrates `commit_s` scales linearly with bytes and the residual `publish_layer_s` is the next ceiling.

---

## 9. Comparison of the three lanes

### 9.1 Decision-axis table

| Axis | Lane A — Stager hardlink | Lane B — Stream capture | Lane C — Manifest delta |
|---|---|---|---|
| **Targets** | Per-file byte-copy in stager | Per-file Python overhead in capture | Per-commit manifest serialisation |
| **Asymptotic per-file cost** | constant (≈ inode op + streaming hash) | constant (no Python list) | constant (delta append) |
| **Files modified** | 8 | 6 | 4 (manifest module) + every reader |
| **Code-change LOC (est.)** | ~100 | ~150 | ~400 (large surface) |
| **Risk profile** | Low — mirrors Phase 1's pattern | Med — iterator semantics ripple through OCC | High — manifest is load-bearing |
| **Compatible with Phase 1 wins** | Yes — composes | Yes — composes | Yes — composes |
| **Reversal cost** | Low — feature flag possible | Med — type changes | High — on-disk format change |
| **Dominant cost reduction** | byte-copy + sync hash | Python-list construction | manifest write amplification |
| **Scope inside Phase 2** | In | In (if data) | In (if data) |
| **Fall-through if data inconclusive** | DEFAULT — implement first | Skip | Skip |

### 9.2 Speed-up budget vs target

Assume Phase 2.2 reports K=10K (gitignored): `commit_s ≈ 12 s`, `capture_upperdir_s ≈ 4 s`. Lane A targets:

| Component | Before (estimated) | After Lane A (estimated) | After Lane A + B (if needed) |
|---|---|---|---|
| `capture_upperdir_s` (no byte read) | 4.0 s | 0.5 s | 0.2 s |
| `_LayerChangeStager.write_total_s` | 11.0 s | 0.8 s | 0.8 s |
| `occ.commit.publish_layer_s` | 0.5 s | 0.5 s | 0.5 s |
| **Total `commit_s`** | **~12 s** | **~1.3 s** | **~1.3 s** |
| **Total wall (npm install side-effect)** | run_command + ~16 s overhead | run_command + ~1.8 s overhead | run_command + ~1.5 s overhead |

The estimates are deliberately conservative; Phase 2.2 data will refine them.

---

## 10. Phase 2.3 — Concurrency re-validation

Once a Lane is landed, re-run the Phase 1 c1/c5/c10/c20 matrix to confirm:

```
.venv/bin/pytest backend/tests/live_e2e_test/sandbox/layer_stack_overlay_occ/test_phase05_public_file_ops_load.py -xvs
```

| Metric | Phase 1 V3D baseline | Phase 2 acceptance |
|---|---|---|
| shell c20 throughput | 6.97 ops/s (best of 3 runs) | ≥ 6.0 ops/s |
| write_file c20 throughput | 15.51 ops/s | ≥ 14.0 |
| edit_file c20 throughput | 10.76 ops/s | ≥ 10.0 |
| read_file c20 throughput | 17.75 ops/s | ≥ 16.0 |
| mixed c20 throughput | 9.77 ops/s | ≥ 8.5 |

The expectation is that linking eliminates per-file bytes work even at small K, so c20 may *improve* slightly.

---

## 11. Phase 2.4 — Verify, document, decide on Phase 3

### 11.1 Verification steps

1. Run the K-scaling benchmark on landed code; confirm `occ.apply.commit_s` flat in K within the §6.4 envelope.
2. Run a real `pip install <small-package>` smoke test in a sandbox — record wall + capture-cost breakdown — to confirm the synthetic benchmark predicts real-world behaviour.
3. Re-run unit tests:
   ```
   .venv/bin/pytest backend/tests/unit_test/test_sandbox -q
   ```
   Expect 0 failures.
4. Re-run lint:
   ```
   .venv/bin/ruff check backend/src/sandbox/
   ```

### 11.2 Implementation report content

Mirror `shell-concurrency-phase1-implementation-report-20260508.md`:

- TL;DR with K-scaling table
- "Path to the answer" — every Phase 2.x attempt's numbers
- Files-touched matrix
- Per-workload c20 verification table
- Recommendation matrix for Phase 3 (e.g. revive `MaterializedSnapshotCache`; multi-lane OCC commits; subprocess-output streaming)

### 11.3 Phase 3 candidate work (out of scope, surfaced for user)

| Candidate | Trigger to start | Rough scope |
|---|---|---|
| `MaterializedSnapshotCache` revival | User authorises reopening Phase 04.5 | Med — cache layer + lease-aware eviction |
| Multi-lane OCC commits for disjoint paths | If Lane A leaves `commit_queue_wait_s` as the new ceiling | Large — concurrency model change |
| Subprocess-output streaming | If `capture_workspace_upperdir` still dominates after Lane A | Med |
| API-layer concurrency cap | If user prefers ceiling over architectural change | Small — config + middleware |

---

## 12. Order of operations (numbered, atomic)

1. **2.1.a** Add `_write_total_s` field + timing key to `_LayerChangeStager` and surface it in `revalidate_and_publish` timings dict.
2. **2.1.b** Create `backend/tests/live_e2e_test/_harness/large_capture_workload.py` with the K-file shell builder.
3. **2.1.c** Create `backend/tests/live_e2e_test/sandbox/layer_stack_overlay_occ/test_phase06_large_capture_scaling.py` with the parametrised test.
4. **2.1.d** Run the benchmark on `main`; produce baseline artifact.
5. **2.2.a** Compute `C_per_file`, `S_per_file`, `S_growth`, `M_pct` from §5.1.
6. **2.2.b** **advisor checkpoint** with the four numbers + selected Lane.
7. **2.3.a** (if Lane A) Implement the Lane A code from §6.2 in this order:
   - §6.2.5 `ContentHasher.hash_file` (no callers yet)
   - §6.2.4 `_LayerChangeStager.link_or_write` (new method, callable from tests)
   - §6.2.1 + §6.2.2 OverlayPathChange `source_upperdir_path`
   - §6.2.3 + §6.2.6 WriteChange `source_path` + capture wiring
   - §6.2.7 DirectMerge / GatedMerge update `stage_write` callers
   - Keep `_LayerChangeStager.write` as a back-compat shim that calls `link_or_write(source_path=None, fallback_content=content)`
8. **2.3.b** Run unit tests; expect 0 regressions.
9. **2.3.c** Re-run the K-scaling benchmark; confirm §6.4 success criteria.
10. **2.3.d** Re-run the Phase 1 c1/c5/c10/c20 matrix; confirm §10 acceptance.
11. **2.4.a** Smoke test: real `pip install` in a sandbox; record numbers.
12. **2.4.b** Write the Phase 2 implementation report.
13. **2.4.c** Architect verification (Ralph Step 7 equivalent).
14. **2.4.d** Deslop pass (mirrors Phase 1).
15. **2.4.e** Final regression run.
16. **2.4.f** /oh-my-claudecode:cancel for clean exit.

### Stop conditions

- If §5.1 numbers are inconclusive (no clear lane winner), stop and surface to the user.
- If Lane A lands but `commit_s` at K=10K is still > 3 s, stop and call advisor — Lane B is the next move.
- If the same K-scaling pattern recurs across 3+ implementation attempts, surface as "fundamental" and stop.

---

## 13. Out of scope for Phase 2

- Reviving the retired `MaterializedSnapshotCache`. Still requires user authorisation; orthogonal to per-call commit optimisation.
- Reducing `run_command_s` (subprocess execution is outside the runtime's control).
- Heuristic per-shell-command behaviour (`if command starts with npm install`, etc.).
- The OCC serial commit lane → multi-lane refactor. Architecturally larger; only consider once Lane A demonstrates `commit_s` scales linearly with bytes.
- The original plan's Phase 2 (base-hash RPC reduction). Its target metric already dropped 2.4× as a Phase 1 side-effect (`occ.prepare.prepare_groups_s` shell c20 p99 370 → 154 ms). Pursuing it now would not materially change shell K-scaling.

---

## 14. Why this is the right Phase 2 (not the original plan's Phase 2)

The original plan's Phase 2 was "reduce `occ.prepare.prepare_groups_s` cost via batched base-hash RPC". Phase 1's data showed that metric dropped 2.4× as a Phase 1 side-effect. **The original Phase 2 is no longer the bottleneck.**

The new Phase 2 is dictated by the workload `api.shell` actually has to serve. Phase 1's micro-benchmark (1–2 captured paths) was a stress-test of dispatch concurrency. A scaling benchmark is now necessary because real users will run `pip install`, `npm install`, build steps — and the runtime must absorb those without per-file penalty.

---

## 15. Appendix — exact pytest invocations

```bash
# Phase 2.1 — produce baseline K-scaling artifact
.venv/bin/pytest backend/tests/live_e2e_test/sandbox/layer_stack_overlay_occ/test_phase06_large_capture_scaling.py -xvs

# Phase 2.3 — sandbox unit tests
.venv/bin/pytest backend/tests/unit_test/test_sandbox -q

# Phase 2.3 — Phase 1 concurrency regression
.venv/bin/pytest backend/tests/live_e2e_test/sandbox/layer_stack_overlay_occ/test_phase05_public_file_ops_load.py -xvs

# Phase 2.4 — final lint
.venv/bin/ruff check backend/src/sandbox/ backend/tests/

# Phase 2.4 — type check (if mypy is configured)
.venv/bin/mypy backend/src/sandbox/
```

## 16. Appendix — git checkpoints

| After step | Commit message |
|---|---|
| 2.1.d | `phase2: add K-scaling benchmark for large shell captures` |
| 2.3.a (full Lane A) | `phase2: hardlink-based stager for shell capture commits` |
| 2.3.c (verify) | `phase2: K-scaling benchmark proves linear→constant in K` |
| 2.4.b | `phase2: implementation report + Phase 3 candidate matrix` |

Each checkpoint is a separate commit, each independently revertable.
