# Verification — Performance properties (O(1) lowerdir CoW, O(n·delta) upperdir, fast mount)

Independent re-derivation of `docs/reviews/rust_parity/areas/perf.md`. Trusted
nothing; opened every cited Python (`/tmp/oldpy/...`, ground truth) and Rust
(`sandbox/crates/...`) file and re-extracted exact operators/constants. The Rust
tree lives under `sandbox/crates/`, not `agent-core/` — the report's mapping
table uses `sandbox/crates/...` paths and those resolve correctly.

Line-number drift note: several report anchors are off by ~10-20 lines
(`acquire_snapshot` is at `stack.rs:331-360`, report said `343-372`; the
auto-squash skip gate is at `dispatcher.rs:1611`, report's table said `1633`
which is a timings-insert; D3 daemon timing is at `dispatcher.rs:951/1225/3211`,
report said `971/1247/3233`). Anchors approximate, behavior identical — not
substantive. All verdicts below cite the line numbers I actually read.

## Invariant verdict table

| # | Invariant | independent_status | severity | decisive bilateral anchor |
|---|-----------|--------------------|----------|---------------------------|
| 1 | Lower-dir O(1): layers shared read-only (CoW), no per-overlay full copy | **confirmed_match** (with D2 concurrency caveat) | none (D2 = medium, tracked separately) | PY `stack.py:108-135` (`acquire_snapshot` = lease + `manifest.layers`→paths, no render) ⟷ RS `stack.rs:331-360` (maps `manifest.layers`→paths, never calls `project`). `project` callers in RS = `squash.rs:222`, `stack.rs:522` (commit), `stack.rs:1359` (test) only — never on snapshot/mount path. |
| 2 | Upper-dir O(n·delta): each op stores only its own delta in its own upperdir | **confirmed_match** | none | PY `writable_dirs.py:46-52` (`run_dir/upper`+`/work` per overlay) + `capture.py:49-89` (`os.walk(upper_root)` only) ⟷ RS `writable_dirs.rs:58-67` (`run_dir/upper`+`/work`) + `path_change.rs:152,159-201` (`walk_upperdir` recurses `std::fs::read_dir` on upperdir only, never lower layers). `OVERLAY_WRITABLE_ROOT = "/eos/mount"`, no fallback, both sides. |
| 3 | Fast: kernel overlayfs mount + manifest CAS pointer-swap, no deep per-op copy | **confirmed_match** | none | mount: PY `kernel_mount.py:49-75` ⟷ RS `kernel_mount.rs:102-132` (`fsopen`→`lowerdir+`/layer→`upperdir`→`workdir`→`fsconfig_create`→`fsmount`→`move_mount(workspace_root)`); fd-pinned lowerdirs PY `kernel_mount.py:~185` ⟷ RS `kernel_mount.rs:231-233` (`fd_path` for lowers only; upper/work/target real). publish: PY `publisher.py:49-138` ⟷ RS `stack.rs:599-662` (read-active → idempotent head short-circuit → stage → fsync → rename → digest → CAS re-read conflict → prepend newest-first → atomic manifest). |
| 4 | Benchmarks exercise the Rust daemon (eosd) and measure these properties | **confirmed_disparity (partial)** — adjudicated as D1 | medium | RS-only: `bench_rust_daemon_phase2.py:56` (`EOSD_REMOTE_PATH`), `:61,:280` (1-file baseline `B000001-base`); `phase3.py:305-323` builds the image workspace base through `api.build_workspace_base`; `phase3.py:98,339-427,435-440` gate on latency (`shell_noop_70pct_faster_than_phase1`) + RSS (`sample_daemon_memory`/`summarize_memory`). grep for `du|disk|byte|space|O(repo)|O(N` in phase3 = **no space-accounting hits**. Property holds by construction; no in-tree bench proves it. |

### Supporting constant / operator parity (all confirmed_match, re-extracted)

| Constant / operator | Python (verified) | Rust (verified) | verdict |
|---|---|---|---|
| `AUTO_SQUASH_MAX_DEPTH` | `occ/service.py:34` = `100` (observed) | **observed both Rust defs:** `eos-layerstack/src/lib.rs:65` `usize = 100` AND `eos-occ/src/service.rs:18` `u32 = 100` (agree); dispatcher imports the layerstack const (`dispatcher.rs:31`) and consumes at `:1611,1613,1625` | match (report's `:66`/`:19` cites off-by-one; values correct, both `100`) |
| `Manifest.depth` == `len(layers)` | `manifest.py:88-89` `return len(self.layers)` | RS plan uses `active_manifest.layers.len()` directly | **match — same quantity** (this was the advisor's tie-breaker; confirmed) |
| auto-squash skip gate | `maintenance.py:50,58` `active.depth <= self._max_depth → return {}` | `dispatcher.rs:1611` `active.depth() <= AUTO_SQUASH_MAX_DEPTH || !can_squash → skip` | match (`<=`) |
| plan: already-shallow | `squash.py:73` `active_manifest.depth <= max_depth` | `squash.rs:166` `active_manifest.layers.len() <= max_depth` | match |
| plan: no-fold | `squash.py:77` `len(entries) >= active_manifest.depth` | `squash.rs:171` `entries.len() >= active_manifest.layers.len()` | match |
| plan: min-reduction | `squash.py:79` `depth - len(entries) < min_reduction` | `squash.rs:174` `layers.len() - entries.len() < min_reduction` | match |
| plan: cap-unreachable (compound) | `squash.py:84-86` `len(entries) > max_depth and all(len(seg.layers) <= max_depth for seg in checkpoint_segments)` | `squash.rs:184-188` `entries.len() > max_depth && checkpoint_segments.all(|s| s.layers.len() <= max_depth)` | match — same operands, same collection (checkpoint_segments) |
| `min_reduction` defaults | plan default `1` (`squash.py:67`); `can_squash` passes `2` (`stack.py:164`) | squash passes `1` (`stack.rs:407`); `can_squash` passes `2` (`stack.rs:387`) | match |
| base layer id | `workspace_base.py` `B000001-base` | `workspace_base.rs:20` `B000001-base` | match |
| writable root | `writable_dirs.py:13` `/eos/mount` | `writable_dirs.rs:14` `/eos/mount` | match |
| idempotent head short-circuit | `publisher.py:76-80` (head digest == new → return active) | `stack.rs:607-609` (`head_layer_digest == Some(digest) → return active`) | match |

## Disparity adjudication

### D1 — Benches prove latency/RSS, not space-complexity → **CONFIRMED (medium, partial on Inv 4)**
Re-derived firsthand. Current Phase 3 builds the base from the target image
workspace through `api.build_workspace_base`, so it no longer relies on the
removed ad hoc tar-seeding helper. That improves fixture realism, but the gate
set (`:435-440`) is still artifact + final_state + cp4s(latency) + load +
memory; grep for disk/byte/du/space/O(repo)/O(N returns nothing. The older phase2
baseline still seeds a single-file `B000001-base` (`README_CONTENT`), so the
baseline fixture does not prove the disk-space moat either. The space moat is
real **by construction** (verified in code above) but **unguarded by any in-tree
benchmark**. The report's framing — "not a Rust regression, a pre-existing gap
shared with the Python/CP-0 baseline" — remains fair. Adjudication: **confirmed,
severity medium, correctly scoped as a shared gap not a Rust drop.**

### D2 — Rust `acquire_snapshot` took the exclusive storage-writer lock; source remediation landed → **SOURCE-FIXED; throughput re-baseline pending**
This was the load-bearing finding; the original verification remains useful for
why the fix matters.
- **Python lock topology** (`/tmp/oldpy/.../stack.py`): `acquire_snapshot`
  (`:113`) body is `with self._lock:` — a `threading.RLock` (`:92`). The
  `_storage_write_guard()` (= cross-process `StorageWriterLockLease.exclusive()`,
  `:364-365`) appears **only** at `:138` (release_lease), `:237` (squash),
  `:313` (commit_to_workspace). Confirmed by `grep`: snapshot/`acquire_lease_record`/
  `can_squash` take only `self._lock`; the write guard is a **different, heavier**
  lock the snapshot path never enters. So Python snapshots overlap concurrent
  publishes/squashes.
- **Original Rust lock topology** (`stack.rs`): `acquire_snapshot` used
  `self.writer_lock.exclusive()?` — the same guard taken by `release_lease`,
  `squash`, `commit_to_workspace`, and `publish_layer`. `exclusive()` returned a
  guard over a single `ReentrantMutex` with no shared/read mode, so every snapshot
  acquire mutually excluded every writer **and** every other snapshot on the same
  root.
- **Current Rust remediation:** `StorageWriterLockLease` now has `shared()` for
  read/snapshot traffic and `exclusive()` for reentrant writers.
  `LayerStack::acquire_snapshot` now calls `self.writer_lock.shared()?`; write-side
  methods keep `exclusive()?`. New storage-lock tests prove shared guards overlap,
  shared guards block writers, and reentrant exclusive guards still block shared
  readers until the outer write guard drops.
- **Severity premise (the advisor's decisive check):** the daemon is genuinely
  concurrent. `server.rs:171,192` spawns a `tokio::spawn` per accepted
  connection; `server.rs:305` dispatches each request via `spawn_blocking`. Every
  dispatch opens a **fresh `LayerStack::open(root)` per request**
  (`command.rs:737`; `dispatcher.rs:355,432,515,581,643,875,1181,1451,1710,...`)
  — there is no shared `Mutex<LayerStack>` / single manager. Cross-request
  serialization is provided **solely** by `StorageWriterLockLease`'s per-root
  `Arc<ReentrantMutex>`, keyed by canonical path and refcount-shared across
  in-process opens (`storage_lock.rs:61-101`; lease registry shared via
  `shared_registry_for_root`, `stack.rs:296`). Therefore concurrent
  `acquire_snapshot` + `publish_layer` on one root **do** collide on that mutex,
  exactly where Python's lighter `self._lock` did not. **The contention is not
  moot; medium severity stands.**
- Per-snapshot *work* remains O(1) (no `project` on the path). The source-level
  lock divergence is fixed; the remaining work is to re-run the live concurrent
  throughput benchmark and update the measured baseline.

### D3 — `acquire_snapshot` timings relocated stack→daemon → **CONFIRMED (low, divergent-but-equivalent)**
`stack.rs:358` returns `timings: BTreeMap::new()` (empty) vs Python
`stack.py:126-130` recording `layer_stack.acquire_snapshot.total_s` inside the
method. The key is instead emitted at the daemon call sites
(`dispatcher.rs:951,1225`) and read back by the bench (`dispatcher.rs:3211`).
RPC-observable behavior identical; only a direct unit-level
`LayerStack::acquire_snapshot` consumer sees an empty map. Adjudication:
**confirmed, low, no action required** (report's own conclusion).

## New findings

1. **No false matches found.** Every invariant the investigator marked "match"
   is a real match at the operator/constant level. I specifically hunted the
   advisor-flagged operand traps and all held: `Manifest.depth ≡ len(layers)`
   (manifest.py:89), so the squash plan's `>= active.depth` (PY) and
   `>= layers.len()` (RS) compare the **same quantity**; the compound
   cap-unreachable predicate is over the **same collection** (`checkpoint_segments`)
   on both sides; the auto-squash gate is `<=` (skip-if-shallow), not `<`.
   `min_reduction` defaults (squash=1, can_squash=2) match on both sides.

2. **Auto-squash skip-gate anchor correction (not a disparity).** The report's
   constant table cites `dispatcher.rs:1633` for the `active.depth() <= AUTO_SQUASH_MAX_DEPTH`
   skip gate; `:1633` is actually a timings-insert. The real gate is
   `dispatcher.rs:1611-1622` (`if active.depth() <= AUTO_SQUASH_MAX_DEPTH || !can_squash → skip`),
   plus the inner `<= max_depth` guard at `squash.rs:166`. Operator and behavior
   are correct; only the line cite is imprecise. No verdict change.

3. **Open Question 2 (shell-pre-mount squash default `64`) — UNPROVEN, leaning
   confirmed-absent.** I grepped all of `sandbox/crates/` for
   `EOS_SHELL_MOUNT_SQUASH_MAX_DEPTH`, `SHELL_MOUNT_SQUASH`, `pre_mount`,
   `premount`, and a `64` squash constant: **zero hits**. The Python
   `ephemeral_workspace/pipeline.py:455-463` separate shell-pre-mount squash path
   (default 64, env-overridable) has **no located Rust equivalent**; the Rust
   daemon appears to rely solely on the post-publish `AUTO_SQUASH_MAX_DEPTH=100`
   path (`dispatcher.rs:1488,1611,1625`). I mark this **unproven** rather than a
   disparity because the Python `ephemeral_workspace` subsystem was deleted whole
   and a deliberate "post-publish-squash-only" design is plausible — but a reader
   should know the `64`-depth pre-mount cap is **not reproduced** in the Rust
   daemon I searched. Within this area's core 4 invariants the impact is
   secondary (mount(8) depth is still bounded by the `100` cap), but it is a real
   behavioral gap the investigator also left dangling, now with the negative grep
   result recorded.

4. **Extra-findings spot-check all hold.** `project` (the only full-tree render)
   is confirmed absent from `acquire_snapshot` and the mount path (callers:
   `squash.rs:222`, `stack.rs:522`, test `:1359`). Capture is O(writes) with
   opaque-before-children dedup (`path_change.rs:150-201` mirrors
   `capture.py:49-89`). Idempotent head short-circuit preserved
   (`stack.rs:607-609` ⟷ `publisher.py:76-80`). CoW delegated to kernel (no lower
   copy anywhere on the path) — confirmed.

## Overall verdict

The investigation was **accurate and well-anchored**. All 4 invariants were
re-derived: Inv 1/2/3 = confirmed_match at the operator/constant level (no false
matches); Inv 4 = confirmed_disparity (partial), correctly adjudicated as D1.
Current status:
- **D2 source-level fix landed.** Snapshot acquire now uses a shared storage lock;
  write-side operations keep the reentrant exclusive lock. Live concurrent
  throughput re-baselining remains open.
- **D1 (medium)** and **D3 (low)** remain as written; D1 is correctly scoped as a
  pre-existing shared benchmark gap, not a Rust drop.

One item the investigator left open is now firmed up with a negative result:
the Python shell-pre-mount squash (`EOS_SHELL_MOUNT_SQUASH_MAX_DEPTH`, default
`64`) has **no located Rust counterpart** (marked unproven, leaning
confirmed-absent). No `investigator_missed` (claimed-match-but-broken) cases.
Only quibbles are ~10-20-line anchor drift, none affecting any verdict.
