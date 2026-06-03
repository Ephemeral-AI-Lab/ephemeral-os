# Independent verification — LayerStack (layers, snapshot view, lease semantics)

Verifier re-derived every invariant by opening both sources directly. Python
ground truth = `/tmp/oldpy/backend/src/sandbox/layer_stack/*` (+ `occ/service.py`
for the squash-depth constant). Rust = `sandbox/crates/eos-layerstack/src/*` +
`eos-protocol/src/cas.rs`. Daemon caller boundary traced through
`sandbox/crates/eos-daemon/src/dispatcher.rs`.

Scope note: line numbers in the investigation's Rust mapping occasionally drift
by a few lines from the current tree (e.g. `workspace_base.rs:22/145` is actually
`:20/141`; `lib.rs:66` is `:65`). These are immaterial — the cited symbols and
literals are present and correct. Flagged inline where it matters.

## Invariant verdict table

| Invariant | Independent status | Severity | Decisive bilateral anchor |
|---|---|---|---|
| 1 — Layers on base; snapshot = ordered base..head, newest at index 0 | confirmed_match | none | PY prepend `publisher.py:112-117` (`LayerRef(new), *active.layers`); RS prepend `stack.rs:648-653` (`layers.push(new); layers.extend(active.layers)`); depth PY `manifest.py:87-89` / RS `cas.rs:128-131` |
| 2 — Overlay mounts latest snapshot; head GC'd on free | confirmed_match | none | PY `acquire_snapshot` returns ordered `layer_paths` `stack.py:117-119`, release GC `stack.py:137-149`; RS `stack.rs:338-351` ordered, release+GC `stack.rs:370-374,791-803` |
| 3 — Lease dual-set: leased_layers vs lease_head_layers | confirmed_match | none | PY `lease.py:57-66` (all refcount>0, sorted), `:68-85` (layers[0] per lease, sorted); RS `lease.rs:132-134` (refcounts.keys via BTreeMap → sorted), `:138-147` (first() per lease → BTreeSet → sorted) |
| 4 — Publish appends ONE layer atomically (txn+lock, CAS, digest-after-rename) | confirmed_match | low | PY `publisher.py:49-138`; RS `stack.rs:599-661`. Append/atomic-rename/digest-after-fsync/manifest-last all preserved. Idempotency short-circuit (`head_digest==layer_digest`) verified byte-identical bilaterally — see NF-6. Only the caller `expected_manifest` API-CAS is dropped → D2 (low) |
| 5 — workspace_binding maps workspace → base + manifest | confirmed_match | none | PY `workspace_binding.py:21-49` (6 fields) / RS `workspace_binding.rs:16-24` (same 6); lookups PY `:82-99` / RS `:83-114`; path-validate PY `:110-130` / RS `workspace_base.rs:377-398` (see New finding NF-1 on resolution strength) |
| 6 — Manifest depth / layer-index ordering preserved | confirmed_match | none | read newest-first PY `view.py:107` / RS `stack.rs:90`; project oldest-first PY `view.py:213` (`reversed`) / RS `stack.rs:128` (`.iter().rev()`). LayerRef sort: PY `@dataclass(order=True)` `manifest.py:46` = (layer_id,path); RS `LayerRefKey` Ord `lease.rs:156-160` = (layer_id,path) |
| C1 — AUTO_SQUASH_MAX_DEPTH = 100 | confirmed_match | none | PY `occ/service.py:34` `= 100`; RS `lib.rs:65` `= 100` (doc said `:66`) |
| C2 — base id "B000001-base", base version 1 | confirmed_match | none | PY `workspace_base.py:32` + `Manifest(version=1...)` `:126`; RS `workspace_base.rs:20` + `Manifest::new(1,...)` `:141` (doc said `:22/145`) |
| C3 — can_squash min_reduction=2, squash default=1 | confirmed_match | none | PY `stack.py:164` (`min_reduction=2`), `:240` uses `plan(...)` default `min_reduction=1` (`squash.py:67`); RS `stack.rs:387` (`...,2`), `stack.rs:407` (`...,1`) |
| C4 — plan acceptance rules + operators | confirmed_match | none | PY `squash.py:73,77,79,84-86`; RS `squash.rs:166,171,174,184-188`. `<=max_depth`, `>=depth`, `<min_reduction`, "still too deep" all byte-identical operators |
| C5 — checkpoint id B{v:06}-…; alloc attempts=100 | confirmed_match | none | PY `squash.py:180` (`B{next_version:06d}-{hex[:8]}`), `paths.py:104` (`attempts=100`→`range(100)`); RS `squash.rs:302` (`B{next_version:06}-{unique:08x}`), `:300` (`0..100`) |
| C6 — lock file name + flock semantics | confirmed_match | none | PY `storage_lock.py:13` (`.storage-writer.lock`), `:71` (`LOCK_EX\|LOCK_NB`→BlockingIOError); RS `storage_lock.rs:40`, `:81-87` (`NonBlockingLockExclusive`→`StorageRootOwned`) |
| C7 — manifest_prefix_before_plan tail-match | confirmed_match | none | PY `squash.py:171-176` (`layers[-d:] != active → None; else layers[:-d]`); RS `squash.rs:340-348` (`layers[split..] != active → None; else &layers[..split]`) |
| C8 — MergedView read uses cached LayerIndex + evict | confirmed_disparity | low | PY caches `LayerIndex` `view.py:50-57`, evicts `view.py:59-65` (called `stack.py:388`); RS `stack.rs:84-116` per-read fs stats, NO index, NO evict call (`remove_layers` `stack.rs:818-829` only rm dir+digest). Behavior equal; perf/cache dropped → D1 |
| C9 — commit_to_workspace blocked by active leases | confirmed_match | none | PY `stack.py:319-320` (`active_count()>0 → RuntimeError`); RS `stack.rs:511-515` (same). Both project active, replace workspace, clear+rebuild base |

All 6 numbered invariants + C1-C7 + C9 = **confirmed_match**. C8 =
**confirmed_disparity (low, perf-only)** — matches the investigator's own
"divergent" verdict.

No `investigator_missed` (no "claimed match but actually broken"). No
`investigator_overstated`. No `unproven` — every anchor was opened and the
operator/constant read directly.

## Disparity adjudication

**D1 (MergedView drops cached LayerIndex; per-read fs stat) — CONFIRMED, low.**
Independently verified: Rust `MergedView` (`stack.rs:66-68`) holds only
`storage_root` — no `layer_index_cache` field exists. `read_bytes`
(`stack.rs:84-116`) does `is_whiteouted` (fs metadata + 2 xattr probes) +
`lookup_blocked_by_layer` (per-ancestor fs stat) + `symlink_metadata` per layer,
every read. Python builds `LayerIndex` once per `layer_id` and answers from
in-memory frozensets (`view.py:52-57,107-118`). `evict_layer_index` is called
from `stack.py:388`; the Rust `remove_layers` (`stack.rs:818-829`) has no
counterpart. Correctness is identical (same newest-first resolution); this is a
genuine read-cost divergence on deep manifests, not a bug. Severity LOW upheld.

**D2 (publish drops caller `expected_manifest` first-CAS) — CONFIRMED, low,
and STRENGTHENED by the daemon caller.** Python `publisher.publish_layer` takes
`expected_manifest` and rejects `active != expected_manifest` up front
(`publisher.py:61-66`), then re-checks `latest != active` after the rename
(`publisher.py:122-128`). Rust `publish_layer` (`stack.rs:599-661`) has no
`expected` param: acquires the guard at `:600`, reads `active` FRESH under the
guard at `:601`, uses it as both base and CAS comparand, keeps only the
post-rename `latest != active` re-check (`stack.rs:638-646`).

Decisive caller evidence (resolves Open Question #2): the daemon opens a FRESH
`LayerStack` per publish (`dispatcher.rs:1451`), reads `active` for validation
context (`:1456`), runs OCC `validate_prepared` (`:1461`), then calls
`stack.publish_layer(&publishable_changes)` (`:1485`) — the writer guard is
acquired inside `publish_layer` and held end-to-end, so there is NO
released-guard snapshot/publish gap for the first CAS to protect against. The
OCC route/conflict decision the Python first-CAS belonged to now lives in the
daemon (`validate_prepared` + the `ManifestConflict` arm at `dispatcher.rs:1501`).
This is an API-surface reduction across the eos-protocol/daemon boundary, not a
behavioral parity loss. Severity LOW upheld; the investigator's reasoning is
correct and the caller confirms the "guard-continuous" assumption.

**D3 (digest-write ordering) — RETRACTION CONFIRMED CORRECT (not a disparity).**
Re-derived: Python writes digest AFTER `os.replace` and AFTER
`fsync_path(layer_dir.parent)` (`publisher.py:104-106`); Rust writes digest
after rename + parent fsync (`stack.rs:624-633` rename+fsync, `:633` digest).
Manifest write is last on both (`publisher.py:131` / `stack.rs:656`). Same crash
window. Rust's error path is marginally cleaner (`remove_path(layer_dir)` on
digest failure, `stack.rs:633-636`). No parity bug. Agree with retraction.

**D4 (MergedView missing list_dir / iter_paths / read_symlink) — CONFIRMED,
severity RESOLVED to LOW.** Re-derived: Rust `MergedView` exposes only
`read_bytes` (`stack.rs:84`) and `project` (`stack.rs:125`); `LayerStack`
exposes `read_bytes`/`read_text` only (`stack.rs:565,576`). Python exposes all
six (`view.py:67-214`, re-exported `stack.py:170-202`).
**Open Question #1 resolved decisively:** a grep of the ENTIRE Rust sandbox tree
(daemon + overlay + layerstack, excluding `/target/`) for `list_dir`,
`iter_paths`, `read_symlink` returns ZERO hits. The daemon read routing uses
`read_bytes`/`read_text` for file reads (`dispatcher.rs:517,588,603,644`),
`acquire_snapshot` (which returns existing `layer_paths`, not enumeration) for
overlay mounts (`dispatcher.rs:877,1183`), and `MergedView::project` for commit
(`dispatcher.rs:1460` / `stack.rs:522`). Merged directory enumeration and symlink
reads are served by the overlay/projection path, NOT this crate — exactly the
"likely intent" the investigator named. Severity is therefore LOW (intentional
drop), not MEDIUM. Adjudication: CONFIRMED, adjusted to firm LOW.

**D5 (process-global shared lease registry vs Python per-manager registry) —
CONFIRMED, low, intentional improvement.** Re-derived: Python gives each
`LayerStack` its own `LeaseRegistry` (`stack.py:93`); dropping a manager
discards its in-memory leases. Rust keys a process-global `shared_registries`
by canonical path (`lease.rs:43-60`), so a lease survives the `LayerStack`
value's drop. The daemon REQUIRES this: it opens a fresh `LayerStack` per RPC
(`dispatcher.rs:1451`, and `acquire_snapshot`/`release_lease` likewise each
`LayerStack::open` their own), so without the shared registry a lease acquired in
one open could never be released by a later open. The
`cross_instance_lease_retains_squashed_layers_until_reopened_release` test
(`stack.rs:1306-1340`) — which I read — opens four separate `LayerStack`s on the
same root and asserts the lease/GC survives across them. This is a deliberate,
more-correct adaptation to the Rust reopen-per-call daemon pattern. Divergent
from Python ground truth, flagged correctly. Severity LOW upheld.

## New findings

**NF-1 (CONFIRMS investigator Extra-finding #2, elevated to a named finding):
`validate_workspace_binding_paths` uses raw `starts_with`, no symlink
resolution.** Python resolves both paths (`.resolve(strict=False)`) then checks
`stack_resolved.is_relative_to(workspace_resolved)`
(`workspace_binding.py:122-130`). Rust uses raw
`stack == workspace || stack.starts_with(workspace)` with NO resolution
(`workspace_base.rs:390`). A symlinked `layer_stack_root` that resolves inside
the workspace passes Rust, fails Python. Also note the Rust *implementation lives
in `workspace_base.rs`*, not `workspace_binding.rs` (which has no path
validator at all) — the doc's "`workspace_binding.rs:18-122`" anchor for
invariant 5's validation is slightly off; the validator is
`workspace_base.rs:377-398`. Severity LOW (paths are daemon-controlled), but a
genuine validation-strength divergence. Matches Open Question #4.

**NF-2 (read_bytes resolution-order reorder — CONFIRMED behaviorally
equivalent, deeper than the Extra finding states).** Python `_visible_entry`:
whiteout(rel) → file(rel) wins → blocked-ancestor (`view.py:107-118`). Rust:
`is_whiteouted(rel)` → `lookup_blocked_by_layer` (ANCESTORS) → file(rel)
(`stack.rs:90-113`). The reorder (Rust checks ancestor-block BEFORE the target
file; Python checks the file first) is provably moot: a file at `rel` and a
file/symlink/opaque ANCESTOR of `rel` cannot coexist on a real filesystem (a
file cannot contain children). Additionally Rust's `lookup_blocked_by_layer`
adds a kernel-whiteout-ancestor check that Python's `_lookup_blocked_by_layer`
omits — but a kernel whiteout is a char-device/0-byte marker that cannot have
children, so an ancestor of a real `rel` can never be one. All divergences are
in unreachable malformed-layer territory. No bug. (I verified this exhaustively
rather than taking it on faith.)

**NF-3 (commit_to_workspace re-acquires the writer lock — minor, benign).**
Python's `commit_to_workspace` uses the existing `_storage_write_guard()`
(`stack.py:313`). Rust calls `StorageWriterLockLease::acquire(&self.storage_root)`
AGAIN at the top (`stack.rs:502`) then `.exclusive()`. Because the lock registry
is refcounted and the mutex is reentrant (`storage_lock.rs:61-101,178-204`), the
second acquire just bumps refcount and the guard is the same reentrant mutex — no
deadlock, no double-lock hazard. The extra `acquire` is released on drop at
end of method. Behaviorally identical; noting only because it is a structural
difference from Python (which never re-acquires). None.

**NF-4 (Rust commit_to_workspace drops the squasher/publisher rebuild — benign).**
Python rebuilds `self._view`, `self._publisher`, `self._checkpoint_squasher`
after the base reset (`stack.py:348-350`). Rust rebuilds only `self.view`
(`stack.rs:540`) — because the Rust `LayerStack` has no `publisher`/`squasher`
fields (they are constructed on demand: `LayerCheckpointSquasher::new` per
`squash`/`can_squash` call, publish folded into the method). So there is nothing
stale to rebuild. Correct consequence of the folded architecture (Rust mapping
row "folded into publish_layer"). None.

**NF-5 (lease_id format differs — cosmetic, contract-satisfying).** Python
`uuid.uuid4().hex` (`lease.py:28`). Rust `{nanos:032x}{counter:016x}` from
`SystemTime` + atomic counter (`lease.rs:180-188`). Only contract is
non-empty + unique-within-registry; both satisfy it. Same class as the
investigator's squash-owner-id note. None.

**NF-6 (CAS surface verified bilaterally against Python `changes.py` — closes
invariant 4's idempotency dependency).** The publish idempotency short-circuit
(`head_layer_digest(active) == layer_digest(changes) → return active`,
PY `publisher.py:76-80` / RS `stack.rs:606-609`) depends on `layer_digest` being
byte-identical. Re-derived from Python ground truth `changes.py` (not from the
Rust self-test):
- `update_digest` (PY `changes.py:146-158`) emits
  `kind‖\0‖path‖\0‖[write→content; symlink→source_path utf-8; delete/opaque→∅]‖\0`
  — BYTE-IDENTICAL to RS `cas.rs:266-277` (trailing `\0` always present, same
  per-kind payload selection).
- `aggregate_layer_changes` (PY `changes.py:161-166`) = dict last-write-wins keyed
  by normalized `change.path`, emitted `sorted(...)` ascending — EQUIVALENT to RS
  `BTreeMap` insert-overwrite + `into_values()` (`cas.rs:255-262`).
- `normalize_layer_path` (PY `changes.py:28-41`): `\`→`/`, strip, drop empty/`.`,
  reject absolute/`..`, empty→error — matches RS `LayerPath::parse`
  (`cas.rs:53-77`). RS additionally rejects NUL inline; PY rejects NUL at the
  `LayerRef`/`resolve_safe_storage_path` layer (`manifest.py:56`, `paths.py:28`),
  so the effective contract matches. The PY `allow_root` flag is used only by
  `list_dir` (no Rust port — D4). Fine-grained byte-identity of the hash output
  is owned by the eos-protocol area review; for layerstack-invariant-4 purposes
  the idempotency short-circuit is confirmed parity. None.

## Overall verdict

The investigation is ACCURATE and well-calibrated. Every "match" verdict I
re-derived holds — no false matches, no broken invariant hiding under a "match"
label. The CAS byte-identity surface (`manifest_root_hash` ensure_ascii escaping,
`layer_digest` NUL-framing, `aggregate_layer_changes` last-write-wins) in
`cas.rs` reproduces the Python hashing exactly, verified BILATERALLY against
Python `manifest.py` (sort_keys → layer_id<path, matching Rust's hardcoded
order) and Python `changes.py` (digest framing + aggregate, NF-6) — not from
Rust self-tests. The two genuinely-divergent items (D1 perf-cache, C8)
are correctly down-weighted to LOW/perf-only; D4's severity is firmly resolvable
to LOW (the merged enumeration surface is dead in the Rust call graph); D2 and D5
are correctly characterized as boundary-relocation / intentional-improvement
rather than parity bugs, and the daemon caller confirms both. The four Open
Questions are all answerable from the daemon dispatcher: #1 LOW (no caller), #2
guard-continuous (fresh-open-per-publish), #3 no orphan-GC sweep found in the
Rust crate but lease-release GC never sees un-manifested orphans (matches D3's
crash-window note), #4 yes — symlink resolution should be added to match Python
(NF-1), tracked as a LOW validation-strength gap.

No HIGH/MEDIUM correctness disparities. No investigator_missed. Recommend the
investigation be accepted as-is, with NF-1 (symlink-resolution in binding-path
validation) recorded as the one actionable (LOW) follow-up.
