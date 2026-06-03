# Rust parity audit — LayerStack (layers, snapshot view, lease semantics)

Domain: sandbox. Ground truth = Python `/tmp/oldpy/backend/src/sandbox/layer_stack/`
(materialized pre-cutover source). Rust under audit =
`sandbox/crates/eos-layerstack/src/`. CAS primitives (`Manifest`, `LayerRef`,
`manifest_root_hash`, `layer_digest`, `aggregate_layer_changes`, `LayerPath`)
live in `sandbox/crates/eos-protocol/src/cas.rs` and are re-exported.

## Ground truth

- **Snapshot = ordered layer sequence, base..head.** `Manifest.layers` is an
  ordered `tuple[LayerRef, ...]`; newest layer is index 0 (`publisher.py:112-118`
  prepends the new layer; doc `layerstack.html:81` "Newest layer is first").
  `Manifest.depth = len(layers)` (`manifest.py:87-89`).
- **Overlay mounts the LATEST snapshot; head released when overlay frees it.**
  `acquire_snapshot` returns `layer_paths` in manifest order
  (`stack.py:108-135`); the head layer (`layers[0]`) is `lease_head_layers()`
  (`lease.py:68-85`) and is GC-retained until `release_lease` drops the lease
  (`stack.py:137-149`).
- **Lease dual-set.** `leased_layers()` = every layer with refcount > 0 (full
  retention, GC keep-set, `lease.py:57-66`); `lease_head_layers()` = `layers[0]`
  of each active lease (squash barriers, `lease.py:68-85`). Both returned
  `tuple(sorted(...))`. Wiki `layerstack-lease-semantics`; doc §2.2/§2.5.
- **Publish appends ONE new layer atomically.** `LayerPublisher.publish_layer`:
  CAS-check, write staging layer, fsync, `os.replace` into `layers/`, write
  digest sidecar, re-check manifest (`latest != active`), write new manifest
  atomically (`publisher.py:49-138`). Held under storage-writer guard +
  in-process RLock via `LayerStackTransaction` (`transaction.py:43-58`,
  `stack.py:204-234`). No in-place mutation of existing layers.
- **workspace_binding maps workspace -> base + active manifest.**
  `WorkspaceBinding{workspace_root, layer_stack_root, active_manifest_version,
  active_root_hash, base_manifest_version, base_root_hash}`
  (`workspace_binding.py:21-49`); `read/require/write_workspace_binding`,
  `validate_workspace_binding_paths` (`workspace_binding.py:82-130`).
- **Manifest depth / layer-index ordering preserved.** `LayerRef` is
  `@dataclass(frozen=True, order=True)` so it sorts by `(layer_id, path)`
  (`manifest.py:46`); read view walks `manifest.layers` newest-first
  (`view.py:107`); `project` applies oldest-first `reversed(manifest.layers)`
  (`view.py:213`).
- **Constants:** `WORKSPACE_BASE_LAYER_ID="B000001-base"`
  (`workspace_base.py:32`); base manifest `version=1`
  (`workspace_base.py:126`); `MANIFEST_SCHEMA_VERSION=1` (`manifest.py:22`);
  `AUTO_SQUASH_MAX_DEPTH=100` (`occ/service.py:34`); overlay mount ceiling
  ~16 layers / kernel ~200 (doc §2.4 "Why auto-squash exists"); `can_squash`
  uses `min_reduction=2`, `squash` uses default `1` (`stack.py:157-168` /
  `stack.py:236-244`); plan rejects when `active.depth <= max_depth`, when
  `len(entries) >= depth`, when `depth - len(entries) < min_reduction`, and the
  "still too deep" rule (`squash.py:69-93`); checkpoint id `B{v+1:06d}-{uuid8}`
  (`squash.py:179-180`); allocate attempts = 100 (`paths.py:96-111`); lock file
  `.storage-writer.lock` (`storage_lock.py:13`); `flock(LOCK_EX|LOCK_NB)`
  (`storage_lock.py:71`).

## Rust mapping

| Python | Rust |
|---|---|
| `LayerStack` (`stack.py:73`) | `stack::LayerStack` (`stack.rs:284`) |
| `LayerStackSnapshotLease` (`stack.py:52`) | `stack::Lease` (`stack.rs:53`) |
| `MergedView` (`view.py:45`) | `stack::MergedView` (`stack.rs:70`) |
| `LeaseRegistry` (`lease.py:20`) | `lease::LeaseRegistry` (`lease.rs:39`) |
| `LayerCheckpointSquasher`/`SquashPlan` (`squash.py`) | `squash::*` (`squash.rs`) |
| `WorkspaceBinding` (`workspace_binding.py`) | `workspace_binding::*` (`workspace_binding.rs`) |
| `build_workspace_base` (`workspace_base.py:82`) | `workspace_base::build_workspace_base` (`workspace_base.rs:102`) |
| `acquire_storage_writer_lock` (`storage_lock.py:59`) | `StorageWriterLockLease::acquire` (`storage_lock.rs:68`) |
| `LayerPublisher`+`LayerStackTransaction`+`CommitStagingArea` | folded into `LayerStack::publish_layer` (`stack.rs:618`); commit-staging dropped (daemon-owned) |
| `Manifest`/`LayerRef`/hashes (`manifest.py`,`changes.py`) | `eos_protocol::cas` (`cas.rs`) |

Architecture refresh note: the Rust port intentionally **collapses
publisher + transaction + commit-staging** into one `publish_layer` method that
holds the writer guard end-to-end. `begin_transaction`, `publish_changes`,
`acquire_lease_record`, `allocate_commit_staging`, `drop_commit_staging`,
`source_root` provenance have **no `eos-layerstack` equivalent** — OCC/daemon
owns that surface now (`eos-daemon/src/dispatcher.rs` is the publish caller).
This is a migration architecture change, not a parity bug, but it drops the
caller-supplied `expected_manifest` first-CAS check (see Disparity D2).

## Invariant table

| # | Invariant | Status | Sev | Python file:line | Rust file:line | Note |
|---|---|---|---|---|---|---|
| 1 | Layers build on base; snapshot = ordered base..head | match | none | `manifest.py:87-89`; `publisher.py:112-118` | `cas.rs:108-138`; `stack.rs:667-672` | Rust prepends new layer at index 0; `depth()=layers.len()` |
| 2 | Overlay mounts latest snapshot; head released on free | match | none | `stack.py:108-135,137-149` | `stack.rs:343-372,383-387` | `acquire_snapshot` returns ordered `layer_paths`; release GCs |
| 3 | Lease model: leased_layers vs lease_head_layers | match | none | `lease.py:57-66,68-85` | `lease.rs:138-154` | both sets distinct; sorted by `(layer_id,path)` (BTree/BTreeSet) |
| 4 | Publish appends ONE new layer atomically (txn+lock) | match | low | `publisher.py:49-138`; `transaction.py:43-58` | `stack.rs:618-681` | append/atomic/CAS/digest-after-rename all preserved; only the caller `expected_manifest` API-CAS is dropped (D2, low) |
| 5 | workspace_binding maps workspace -> base + manifest | match | none | `workspace_binding.py:21-130` | `workspace_binding.rs:18-122`; `workspace_base.rs:381-402` | binding shape + lookups + path validation preserved |
| 6 | Manifest depth / layer-index ordering preserved | match | none | `manifest.py:46,87-89`; `view.py:107,213` | `cas.rs:45-46,136-138`; `stack.rs:96,135` | read newest-first, project oldest-first; LayerRef Ord = (layer_id,path) |
| C1 | `AUTO_SQUASH_MAX_DEPTH = 100` | match | none | `occ/service.py:34` | `lib.rs:66` | literal `100` both sides |
| C2 | base layer id `"B000001-base"`, base version 1 | match | none | `workspace_base.py:32,126` | `workspace_base.rs:22,145` | identical |
| C3 | `can_squash` min_reduction=2, `squash`=1 | match | none | `stack.py:164,240` | `stack.rs:401,422` | `can_squash` passes `2`, `squash` passes `1` |
| C4 | plan acceptance rules + operators | match | none | `squash.py:69-93` | `squash.rs:167-209` | `<= max_depth`, `>= depth`, `< min_reduction`, "still too deep" all match |
| C5 | checkpoint id `B{v+1:06}-...`; alloc attempts=100 | match | none | `squash.py:101,179-180`; `paths.py:96-111` | `squash.rs:226,308-327` | `{next_version:06}` + 8-hex; loop `0..100` |
| C6 | lock file name + flock semantics | match | none | `storage_lock.py:13,71` | `storage_lock.rs:45,88` | `.storage-writer.lock`, `LOCK_EX\|LOCK_NB` -> StorageRootOwned |
| C7 | `manifest_prefix_before_plan` tail-match | match | none | `squash.py:167-176` | `squash.rs:351-364` | `layers[-d:]==active` -> `layers[..split]` |
| C8 | MergedView read uses cached LayerIndex + evict | divergent | low | `view.py:50-65,108-118`; `layer_index.py` | `stack.rs:90-122` (no index) | Rust does per-read fs stats; behavior equal, perf/cache dropped (D1) |
| C9 | `commit_to_workspace` blocked by active leases | match | none | `stack.py:300-362` | `stack.rs:516-574` | active-lease guard + project + rebuild base |

## Disparities

### D1 — MergedView drops the cached LayerIndex (per-read fs stat instead) — LOW (divergent, perf only)
Python `MergedView` builds a `LayerIndex{files, whiteouts, opaque_dirs}` per
layer ONCE (`view.py:50-57`), caches it by `layer_id`, and evicts it on layer
removal (`view.py:59-65`, called from `stack.py:388
self._view.evict_layer_index`). `_visible_entry` then answers
read_bytes/list_dir/iter_paths from the in-memory sets, the documented
"short-circuit the per-layer filesystem walk for paths that are provably
absent" (`layer_index.py:9-18`).

Rust `MergedView::read_bytes` (`stack.rs:90-122`) has **no index**: for each
layer it runs `is_whiteouted` (fs metadata + xattr probes), then
`lookup_blocked_by_layer` (fs stat per ancestor), then `symlink_metadata` on the
target. There is no `evict_layer_index` call site (the Python eviction at
`stack.py:388` has no Rust counterpart in `remove_layers`, `stack.rs:837-848` —
it only removes the dir + digest sidecar).

Why it matters: behaviorally equivalent for correctness (same newest-first
resolution, whiteout/opaque masking), so this is NOT a bug. But it is a real
divergence in the dominant read-cost path the Python design optimized for
(shell captures creating many files do N×depth stats instead of cached-set
membership). Also `iter_paths` / `list_dir` (used by `commit`/maintenance) have
**no Rust port at all** in `MergedView` (only `read_bytes`, `read_text`,
`project` exist) — see D4.
Fix: if read latency under deep manifests regresses, port `build_layer_index`
+ cache + `evict_layer_index`. Otherwise document the intentional drop.

### D2 — `publish_layer` drops the caller-supplied first-CAS check — LOW (partial, API-surface only)
Python `LayerPublisher.publish_layer` takes `expected_manifest` and FIRST
asserts `active != expected_manifest -> ManifestConflictError`
(`publisher.py:61-66`) before doing anything, THEN after writing the layer
re-reads and asserts `latest != active` (`publisher.py:122-128`). The
`expected_manifest` is the manifest the transaction snapshotted at
`__enter__` (`transaction.py:57,84-90`), so a publish raced by another writer
between snapshot and publish is rejected up front.

Rust `LayerStack::publish_layer` (`stack.rs:618-665`) reads `active` fresh
under the guard and uses it as BOTH the expected base and the CAS comparand;
there is no caller `expected_manifest` parameter. Because the reentrant write
guard is held end-to-end (`stack.rs:619`), the `latest != active` re-check at
`stack.rs:657-665` can only ever be true under multi-process contention (the
flock makes that StorageRootOwned at open), so within one daemon the second
check is effectively a no-op and the first check is absent.

Why it matters: the first CAS (`active != expected_manifest`) is effectively a
no-op even in Python: the transaction holds the writer guard from `__enter__`
(where it snapshots, `transaction.py:45-57`) through publish, so `active` cannot
move in-process, and cross-process is blocked by the flock. So this is an
**API-surface reduction** (no `expected` param → a caller can no longer express
"I read manifest vN earlier, publish only if active is still vN" across a
released-guard gap), not a behavioral parity loss. The guard-continuous Rust
design has no such gap.
Fix (optional): accept `expected: &Manifest` to restore the API, or add a
one-line doc note that publish is unconditional-on-current-active because the
writer guard is held continuously (no snapshot/publish gap exists).

### D3 — RETRACTED (false positive): digest ordering is IDENTICAL on both sides
An earlier draft flagged the digest-sidecar write ordering. It was wrong.
Python `publisher.py:104-106` writes the digest AFTER `os.replace` AND AFTER
`fsync_path(layer_dir.parent)` — exactly the Rust order
(`stack.rs:643` rename, `:648-650` fsync parent, `:652` write digest). Same
crash window {layer durable, no digest}, and Python's parent fsync does NOT
cover the digest. The manifest write is last on both sides (the linearization
point), so an un-manifested orphan layer is identical on both. If anything the
**error path is cleaner in Rust**: on a digest-write failure Rust does
`remove_path(layer_dir)` (`stack.rs:652-655`), whereas Python's
`except: rmtree(staging_dir)` (`publisher.py:108-110`) is a no-op after the
rename (staging is already gone) and leaves the orphan dir. No parity bug; a
minor Rust improvement. No invariant downgrade attributable to D3.

### D4 — `MergedView` is missing `list_dir`, `iter_paths`, `read_symlink` — LOW→MEDIUM (missing, contingent)
Python `MergedView` exposes `read_bytes`, `read_text`, `read_symlink`,
`list_dir`, `iter_paths`, `project` (`view.py:67-214`), and `LayerStack`
re-exports all of them (`stack.py:170-202`). Rust `MergedView` implements only
`read_bytes` and `project` (`stack.rs:90,132`); `LayerStack` exposes
`read_bytes`/`read_text` only (`stack.rs:583-603`). There is **no Rust port of
`list_dir`, `iter_paths`, or `read_symlink`** anywhere in the crate (grep of
`eos-layerstack/src` finds none).

Why it matters: these are the directory-enumeration and symlink-resolution read
APIs the Python merged view provides for fast logical reads and for
maintenance (`iter_paths` feeds deterministic projection/diagnostics). Severity
is CONTINGENT: the grep was scoped to `eos-layerstack` only. If a daemon /
overlay caller actually routes *merged* `list_dir`/`iter_paths`/`read_symlink`
through this crate, the drop is MEDIUM; if those reads are served by the
overlay/projection path (the likely intent), it is LOW. See Open question #1 —
this severity must be resolved by checking the daemon read-routing caller.
Fix: confirm whether daemon read routing needs merged `list_dir`/`iter_paths`;
if so, port them (the `LayerIndex` from D1 is their natural backing). Otherwise
document the intentional removal.

### D5 — process-global shared lease registry diverges from Python's per-manager registry — LOW (divergent, intentional improvement)
Python gives EACH `LayerStack` its own `LeaseRegistry` (`stack.py:93
self._leases = LeaseRegistry()`). The daemon caches one manager per root
(`daemon/layer_stack_runtime.py:32,37-46 _MANAGER_CACHE`), but
`drop_layer_stack_manager` just `_MANAGER_CACHE.pop(key)`
(`layer_stack_runtime.py:48-52`) — and `storage_lock.py:33-40` explicitly
contemplates "multiple in-process LayerStack managers that may coexist after
cache drops or overlay lifecycle resets." So when a manager is dropped, its
in-memory `LeaseRegistry` and every lease it held are DISCARDED; a freshly
reopened manager for the same root starts with an empty registry and cannot
release/GC a lease the dropped manager held.

Rust closes exactly that gap: leases live in a process-global
`shared_registries` keyed by canonical path (`lease.rs:46-63`,
`shared_registry_for_root`), so a lease acquired on one `LayerStack` survives
that value's drop and is visible to a later reopen — asserted by the
`cross_instance_lease_retains_squashed_layers_until_reopened_release` test
(`stack.rs:1328-1362`).

Why it matters: per source precedence (Python = ground truth), Rust's
cross-instance lease visibility is a behavioral DIVERGENCE, not a 1:1 port.
It is almost certainly a deliberate and more-correct adaptation to the Rust
"retain only `lease_id`, reopen the root later for release/metrics" daemon
pattern (`lease.rs:48-50` comment), and it fixes a real Python lease-loss-on-
cache-drop hazard. Flagged as divergent/intentional so a future reviewer knows
the two registries are not semantically identical. Verify: confirmed Python
holds a long-lived cached manager per root (`layer_stack_runtime.py:37-46`) but
loses leases on `drop` — Rust does not.

## Extra findings

- **Whiteout/blocked check ordering in `read_bytes` differs but is
  behaviorally equivalent.** Python `_visible_entry` checks `whiteouts`, then
  `files` (present file wins), then `_lookup_blocked_by_layer` ancestors
  (`view.py:107-118`). Rust checks `is_whiteouted`, then
  `lookup_blocked_by_layer` (ancestors), then the target file
  (`stack.rs:96-119`). For a well-formed layer a file at `rel` and a
  file/opaque ancestor of `rel` are mutually exclusive, so order is moot — no
  bug, but the reorder is worth noting if malformed layers ever appear.
- **`validate_workspace_binding_paths` uses `starts_with` (prefix) not resolved
  `is_relative_to`.** Python resolves both paths (`.resolve(strict=False)`) then
  checks `stack_resolved.is_relative_to(workspace_resolved)`
  (`workspace_binding.py:122-130`). Rust uses raw `stack.starts_with(workspace)`
  with NO symlink resolution (`workspace_base.rs:394`). A symlinked
  `layer_stack_root` that resolves inside the workspace would pass the Rust
  check but fail Python's. LOW severity (paths are daemon-controlled) but a
  genuine validation-strength divergence.
- **`Lease.timings` always empty in Rust** (`stack.rs:370`
  `timings: BTreeMap::new()`). Python populates
  `layer_stack.acquire_snapshot.total_s` (`stack.py:126-130`). Cosmetic /
  metrics-only; the doc lists `timings` as a Lease field. LOW.
- **Squash synthetic-lease owner id** uses a process counter `squash-{n}`
  (`stack.rs:429`) instead of Python's `squash-{uuid4().hex}` (`stack.py:249`).
  Only needs to be non-empty + unique within the registry; equivalent. None.
- **`acquire_snapshot` rollback** (Python releases the lease if `layer_paths`
  build raises, `stack.py:132-135`) has no Rust counterpart, but the Rust
  `layer_paths` map is infallible (`to_string_lossy().into_owned()`), so there
  is no failure window to roll back. None.
- **Reentrant-lock TRAP correctly handled.** `storage_lock.rs:175-231`
  implements a `ReentrantMutex` (owner ThreadId + depth) so
  `squash` -> `_storage_write_guard` -> `release_lease` (also takes the guard)
  does not self-deadlock, exactly as the module doc warns. Verified the
  Rust `squash` releases the synthetic lease via `release_lease_locked` while
  the outer `_guard` is held (`stack.rs:474-477`) — reentry depth-counted.
- **Counter-drop GC semantics match.** Python `Counter.__isub__` drops
  non-positive keys (verified empirically), so `leased_layers()` only returns
  refcount>0 layers; Rust `release` explicitly `remove`s a key at count 0
  (`lease.rs:120-133`) and `leased_layers` reads `refcounts.keys()`
  (`lease.rs:138-140`). Equivalent.
- **Cross-instance lease registry**: promoted to its own finding D5 (divergent,
  intentional improvement) — see Disparities.

## Open questions

1. Is the merged `list_dir`/`iter_paths`/`read_symlink` surface (D4)
   intentionally dropped from `eos-layerstack` because daemon read routing
   serves those via the overlay/projection path, or is it an un-ported gap?
2. Does any daemon/OCC caller rely on the publisher `expected_manifest`
   first-CAS (D2) across a guard gap, or is publish always guard-continuous as
   the Rust design assumes?
3. Is there an orphan-layer GC sweep anywhere (daemon/maintenance) that would
   reclaim a `layers/L*` dir left by a crash between rename and manifest write
   (relevant to D3), or does the Rust port rely solely on lease-release GC
   (which never sees an un-manifested orphan)?
4. Should `validate_workspace_binding_paths` resolve symlinks before the
   inside-workspace check to match Python's `is_relative_to` on resolved paths?
