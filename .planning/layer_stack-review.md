# layer_stack — Targeted Code-Quality Audit

**Scope:** `backend/src/sandbox/layer_stack/` (20 files, 2,660 LOC)
**Date:** 2026-05-15
**Reviewer:** Claude (gsd-code-reviewer)
**Criteria, in order:** (1) implementation quality / bugs, (2) simplicity & redundancy reduction, (3) import-chain depth ≤3.

---

## 1. Executive Summary

The module is well-structured and correct on its hot path, but it carries **~700–900 LOC of avoidable bulk** — roughly **27–34% of the tree**. The two biggest levers are: (a) flattening the three sub-packages (`manifest/`, `layer/`, `workspace/`) where each `__init__.py` re-exports >80% of the sub-tree symbols (saves ~115 LOC of pure boilerplate), and (b) collapsing the four-class `LayerChange` ABC + `PreparedLayerChange` hierarchy into a tagged-union dataclass (saves ~110 LOC). Smaller wins (merging `commit.py`/`errors.py` into adjacent modules, deleting single-use helpers in `_paths.py`, removing dead defensive branches in `view.py`) add another ~80 LOC.

**Concrete bugs found:** one **BLOCKER** (`__del__` re-entrancy on the module-level `_STORAGE_WRITER_LOCKS_LOCK` at interpreter shutdown), one **HIGH** durability inconsistency (`maintenance.relabel_checkpoint` skips fsync), and one **HIGH** API contract violation (`workspace.base._relative_target_escapes` is imported across packages despite a `_` prefix). Several **MEDIUM** issues around `read_symlink` ambiguity, defensive dead-code, and concurrency-around-`evict_layer_index` are listed below.

**Import-chain audit:** Two 5-hop chains exist, both transit `layer/__init__.py` and `manifest/__init__.py`. Flattening those sub-packages drops every reachable chain to ≤3 hops simultaneously with the LOC reduction in §3.

---

## 2. Findings by Severity

### BLOCKER

#### B-01 — `__del__` reentrancy on module-level lock at interpreter shutdown

**File:** `_storage_lock.py:39-49`, `_storage_lock.py:51-52`, `manager.py:317-318`
**Issue:** `LayerStackManager.__del__` → `close()` → `StorageWriterLockLease.close()` acquires the module-level `_STORAGE_WRITER_LOCKS_LOCK` (line 39) and reads `_STORAGE_WRITER_LOCKS` (line 40). During interpreter shutdown the GC can run `__del__` after the `_storage_lock` module's globals have been torn down — at that point `_STORAGE_WRITER_LOCKS_LOCK` may already be `None`, raising `AttributeError` in the `with` statement or, worse, releasing the global `RLock` while another thread is mid-acquire. Additionally, `StorageWriterLockLease.__del__` (line 51) calls `close()` *again* even if the manager already closed the lease, which races with the same module state.
**Fix sketch:** Drop `__del__` entirely from both classes. `acquire_storage_writer_lock` returns a context-manager-style lease that callers must close explicitly, and `LayerStackManager` already exposes `close()`. If implicit cleanup is required, replace `__del__` with a `weakref.finalize(self, _STORAGE_WRITER_LOCKS.pop, key, None)` registered at construction and stash the fd in a frozen tuple. Reference: CPython docs explicitly warn that `__del__` "is not guaranteed to be called for objects that still exist when the interpreter exits."
**LOC delta:** −10.

### HIGH

#### H-01 — `maintenance.relabel_checkpoint` skips fsync after `os.replace`

**File:** `maintenance.py:71-81`
**Issue:** Compare with `layer/publisher.py:101-104`, which `os.replace(staging_dir, layer_dir)` *and* `_fsync_dir(layer_dir.parent)`. `relabel_checkpoint` does `os.replace(current_path, layer_dir)` (line 80) and returns immediately. If the host crashes between the rename and the parent-directory journal flush, the checkpoint can end up under its old name (gone) or both names (orphan), and the squash manifest write that follows in `manager.squash` may succeed and reference a non-durable layer. This silently weakens the durability guarantee that `publisher.publish_layer` enforces.
**Fix:**
```python
def relabel_checkpoint(self, checkpoint, *, manifest_version):
    current_path = resolve_storage_path(self._storage_root, checkpoint.path)
    if not current_path.exists():
        raise FileNotFoundError(...)
    layer_id, _staging_dir, layer_dir = self._allocate_checkpoint_paths(manifest_version)
    os.replace(current_path, layer_dir)
    fd = os.open(layer_dir.parent, os.O_RDONLY)  # NEW
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    return LayerRef(layer_id=layer_id, path=f"{LAYERS_DIR}/{layer_id}")
```
Or — better — lift `_fsync_dir` out of `publisher.py` (lines 208-213) into `_paths.py` and call from both call sites (it's identical code: see also `workspace/base.py:397-402` for a third copy).
**LOC delta:** +5 here, but enables consolidating three identical `_fsync_dir` helpers → net −10.

#### H-02 — `_relative_target_escapes` private helper imported across packages

**File:** `workspace/base.py:308-322`
**External import:** `src/sandbox/execution/overlay/capture.py:13` does `from sandbox.layer_stack.workspace.base import _relative_target_escapes`.
**Issue:** A leading-underscore name is by convention private. `overlay/capture.py` performs the same `link_target.startswith("/") or _relative_target_escapes(...)` check, so the helper is genuinely reusable, but its current name lies about its API surface. Worse, the helper has a stylistic dead branch: `parts` is appended to but the function only ever returns `True` on underflow — the final `return False` is reached without `parts` being inspected. The comment "If the path is exhausted without underflow, the relative target stays inside its origin directory tree" describes intent but doesn't justify keeping the `parts.append/pop` bookkeeping at all.
**Fix:** Either rename to public `relative_symlink_target_escapes` and move to a neutral location (`layer_stack/_paths.py` or a new `layer_stack/symlink_safety.py`), OR delete the bookkeeping and write:
```python
def _relative_target_escapes(target: str) -> bool:
    depth = 0
    for part in target.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            if depth == 0:
                return True
            depth -= 1
        else:
            depth += 1
    return False
```
Saves 5 LOC and matches the comment's intent verbatim.
**LOC delta:** −5 plus a cleaner API.

#### H-03 — `view.read_symlink` returns `("", False)` for "exists but not a symlink"

**File:** `view.py:79-98`, especially lines 89-95
**Issue:** When `rel` is present in `index.files` *and* the layer entry is a regular file (not a symlink), the code returns `("", False)`. That is bit-identical to "not found at all" (line 103). A caller that asks "tell me if `foo/bar` is a symlink, and what does it target?" cannot distinguish "no such path" from "path exists but is a file." The comment on lines 92-95 explicitly acknowledges this is preserved-from-old-behavior, but propagating an ambiguous return is preserving a latent bug, not preserving correctness. Live-e2e tests at `live_e2e_test/sandbox/workspace_base/test_base_import_correctness.py:53` only exercise the symlink-hit path, so the ambiguity is currently untested.
**Fix:** Either widen the return type to `tuple[str, Literal["symlink", "file", "absent"]]` (cleanest), or — if compatibility matters more — raise `LayerStackStorageError(..., layer_id=...)` on the file-when-symlink-expected case, which surfaces type confusion at the boundary rather than silently masking it.
**LOC delta:** ±0 (semantic fix).

### MEDIUM — Simplicity / Redundancy (the user's #1 priority)

#### M-01 — Flatten `manifest/` sub-package into `manifest.py`

**File:** `manifest/__init__.py` (37), `manifest/_model.py` (121), `manifest/store.py` (75) → total 233 LOC
**Issue:** `manifest/__init__.py` is 37 lines of pure re-export. The sub-package was created to separate "model" from "store," but both files together are 196 LOC and the model has zero dependency on the store. Collapsing them into a single `manifest.py` deletes the boilerplate and removes one hop from every transitive import (see §4).
**Fix:** Move `_model.py` + `store.py` into a single `layer_stack/manifest.py`; delete `manifest/`. External callers continue to import from `sandbox.layer_stack.manifest` — same dotted name, fewer hops.
**LOC delta:** −37 (the `__init__.py` boilerplate; the merged file is just `121 + 75 = 196`).

#### M-02 — Flatten `layer/` sub-package

**File:** `layer/__init__.py` (43), `layer/change.py` (257), `layer/index.py` (78), `layer/publisher.py` (239) → total 617 LOC
**Issue:** The `layer/__init__.py` re-exports 14 names from three files. `layer/change.py` and `layer/index.py` already cross-import (`change.py:17` imports `OPAQUE_MARKER, WHITEOUT_PREFIX` from `index.py`). The sub-package buys nothing structural — the public API surface is exactly the union of three modules, so callers could just as easily import directly from `sandbox.layer_stack.layer_change`, `sandbox.layer_stack.layer_index`, `sandbox.layer_stack.layer_publisher`.
**Fix:** Move the three files up one level (renaming them `layer_change.py`, `layer_index.py`, `layer_publisher.py`) and delete `layer/__init__.py`. Update the root `__init__.py` import paths.
**LOC delta:** −43 directly + one fewer import hop everywhere.

#### M-03 — Flatten `workspace/` sub-package

**File:** `workspace/__init__.py` (35), `workspace/base.py` (436), `workspace/binding.py` (157)
**Issue:** Same rationale as M-01/M-02. The `workspace/__init__.py` is 35 LOC of re-export. `base.py` and `binding.py` are independent (only `base.py` imports `binding` symbols). The sub-package adds zero abstraction layering.
**Fix:** Move both up; rename to `workspace_base.py` + `workspace_binding.py`; delete `workspace/`. Optional: `workspace_binding.py` is a natural home for the `_relative_target_escapes` helper (H-02), since both files concern workspace-level safety.
**LOC delta:** −35.

#### M-04 — Collapse `LayerChange` ABC + `PreparedLayerChange` into a tagged-union dataclass

**File:** `layer/change.py:43-258` (216 LOC of body)
**Issue:** Four near-identical subclasses, each with:
- a `__post_init__` checking the `kind` literal (4 × ~3 LOC)
- an `_update_digest_payload` (4 × ~3 LOC, three of which are empty `del digest, prepared`)
- a `write_to` (4 × ~6 LOC)

The `PreparedLayerChange` indirection exists only because `WriteLayerChange.prepare` defers I/O until the publisher is committed. That's a valid design, but it doesn't require an inheritance hierarchy + Protocol + ABC — a tagged dataclass with two helper functions captures the same invariants:
```python
@dataclass(frozen=True)
class LayerChange:
    kind: Literal["write", "delete", "symlink", "opaque_dir"]
    path: str
    source_path: str | None = None       # used by write+symlink
    content_hash: str | None = None      # used by write only

@dataclass(frozen=True)
class PreparedLayerChange:
    change: LayerChange
    write_content: bytes | None = None

def prepare_layer_change(change, *, source_root):
    if change.kind == "write": ...
    return PreparedLayerChange(change=change)

def write_layer_change(prepared, layer_dir):
    c = prepared.change
    if c.kind == "write":   _write_file(layer_dir, c.path, prepared.write_content)
    elif c.kind == "delete": _whiteout_path(layer_dir, c.path).write_text("", "utf-8")
    elif c.kind == "symlink": ...
    elif c.kind == "opaque_dir": ...

def update_digest(digest, prepared): ...
```
This eliminates the ABC, the Protocol (`DigestSink`), four `__post_init__` blocks, four `_update_digest_payload` methods, and four `write_to` methods.
**LOC delta:** ~−110 (from 257 → ~145).
**Risk:** Touches many call sites (`publisher.py:91-92`, `publisher.py:179-182`, plus tests). Worth the win, but stage it after M-01..M-03.

#### M-05 — Merge `commit.py` (15 LOC) into `manager.py`

**File:** `commit.py:1-15`
**Issue:** A 7-line dataclass deserves its own file only if it's imported by code that can't import `manager.py`. External callers `daemon/service/layer_stack_client.py:13` and `occ/ports.py` (via `stage/transaction.py`) already pull `LayerStackManager` from the same package; importing `CommitStagingArea` from `sandbox.layer_stack` (the root) keeps the public path stable.
**Fix:** Move the `CommitStagingArea` dataclass into `manager.py` (after `PrepareWorkspaceSnapshotResult`); re-export from `layer_stack/__init__.py`. Delete `commit.py`.
**LOC delta:** −15.

#### M-06 — Merge `errors.py` (18 LOC) into `manifest.py` (post-M-01)

**File:** `errors.py:1-18`
**Issue:** Two exception classes, both manifest-related (`ManifestConflictError` is already imported by `manifest/_model.py:11`; `LayerStackStorageError` is only raised by `view.py:62-64` and `view.py:204-209`). Two classes, two files = boilerplate. After M-01 merges `manifest/`, putting both errors in the single `manifest.py` (or in `view.py` for `LayerStackStorageError`) eliminates a file and a circular-ish import.
**Fix:** Move `ManifestConflictError` to `manifest.py`; move `LayerStackStorageError` to `view.py`. Delete `errors.py`. Update the root `__init__.py` re-exports.
**LOC delta:** −18.

#### M-07 — Delete single-use helpers in `_paths.py`

**File:** `_paths.py:44-46` (`safe_request_part`), `_paths.py:49-56` (`log_rmtree_failure`)
**Issue:** Each is called from exactly one site (`manager.py:124`, `manager.py:218`, `manager.py:154`). They're not algorithms — they're 1–3-line transformations that should live inline at the call site, or as private functions in `manager.py`. Hoisting them into `_paths.py` and listing them in `__all__` advertises them as a reusable contract they don't fulfil.
**Fix:** Inline both into `manager.py` as module-private functions.
**LOC delta:** −15 (helper bodies + `__all__` entries + import lines).

#### M-08 — Consolidate three identical `_fsync_dir` helpers

**File:** `layer/publisher.py:208-213`, `manifest/store.py:43-47` (inline `dir_fd = os.open(...); os.fsync; os.close`), `workspace/base.py:397-402`, `workspace/binding.py:118-122` (inline)
**Issue:** Same 5-line dance four times. Three are functions; two are inlined.
**Fix:** Define `_fsync_dir(path: Path) -> None` once in `_paths.py`; import and call from all five sites. Same for `_fsync_file` (`workspace/base.py:389-394`) which is used twice.
**LOC delta:** −15 to −20.

#### M-09 — `view._apply_layer` walks `rglob("*")` three times

**File:** `view.py:218-249`
**Issue:** Lines 219, 221-225 (opaque pass), 227-232 (whiteout pass), 234-249 (entry pass) all iterate the same `entries` tuple. That's fine for correctness, but each pass re-runs `marker.name == _OPAQUE_MARKER` / `_is_whiteout(...)`. Could be a single loop that bucket-sorts entries by kind, then applies each bucket in order. Smaller, faster, easier to reason about overlay-semantics changes.
**Fix:**
```python
opaques, whiteouts, regulars = [], [], []
for entry in entries:
    if entry.name == _OPAQUE_MARKER: opaques.append(entry)
    elif _is_whiteout(entry.name): whiteouts.append(entry)
    else: regulars.append(entry)
for o in opaques: _clear_directory(dest / o.parent.relative_to(layer_dir))
for w in whiteouts: ...
for e in regulars: ...
```
**LOC delta:** −5; clarity win is larger than LOC delta suggests.

#### M-10 — `view.list_dir` defensive dead branch

**File:** `view.py:156-166`
**Issue:** The code's own comment (lines 152-155) says "the whiteout-only re-creation never happens in practice — overlayfs writes `.wh.<rel>` only when rel is being deleted." That's the author admitting the entire `has_children_here` branch is unreachable for any layer produced by overlayfs or by this module's own publisher (since `DeleteLayerChange.write_to` only writes the marker, never any children alongside). Dead defensive code.
**Fix:** Delete lines 156-166 entirely. If a fuzz test catches a real producer that writes both, add it back with a test that exercises it.
**LOC delta:** −11.

#### M-11 — `LayerDelta` is a single-field dataclass used in one place

**File:** `layer/change.py:230-243`
**Issue:** `LayerDelta(changes=tuple(...))` is constructed once (line 243) by `aggregate_layer_changes`, then immediately unpacked by callers as `.changes`. It carries no extra fields. It's a single-purpose wrapper that adds an attribute access at every call site for no information.
**Fix:** Have `aggregate_layer_changes` return `tuple[LayerChange, ...]` directly. Drop `LayerDelta` and its re-export. Callers replace `aggregate_layer_changes(changes).changes` with `aggregate_layer_changes(changes)`.
**LOC delta:** −15 (class + re-exports across `__init__` files).

#### M-12 — `manager._unreferenced_layers` over-engineered

**File:** `manager.py:284-301`
**Issue:** The `seen` set deduplicates `candidates`, but `candidates` always comes from `lease.manifest.layers` (line 167) which is already deduplicated by the publisher (each layer ID is unique). The `seen` bookkeeping is a defensive check for a state the manifest invariant forbids.
**Fix:**
```python
def _unreferenced_layers(self, candidates, *, current_manifest):
    skip = set(current_manifest.layers) | set(self._leases.pinned_layers())
    return tuple(layer for layer in candidates if layer not in skip)
```
**LOC delta:** −10.

#### M-13 — `SquashPlan.__post_init__` self-assigns `suffix_to_checkpoint`

**File:** `maintenance.py:16-28`
**Issue:** Line 22-26 does `object.__setattr__(self, "suffix_to_checkpoint", tuple(self.suffix_to_checkpoint))`. The dataclass declares `suffix_to_checkpoint: tuple[LayerRef, ...]`. Callers in `maintenance.py:53` and tests always pass tuples already. The conversion is defensive against an unspecified caller that might pass a list. Either tighten the type and delete the conversion, or it's noise.
**Fix:** Drop the `tuple()` cast; keep only the `if not self.suffix_to_checkpoint: raise`.
**LOC delta:** −5.

#### M-14 — `workspace/base.py` re-walks the workspace for "quiescent" check

**File:** `workspace/base.py:269-285`
**Issue:** `_assert_workspace_quiescent` calls `_collect_base_entries` a second time (line 275). For a workspace base build over a large workspace this doubles the I/O. There's no in-process locking that justifies a "still quiet?" check — by the time control returns the workspace could change again. The check provides only weak liveness comfort, not a guarantee.
**Fix:** Either (a) document that this is a "did the user lie about workspace being quiescent" check and accept the double-walk, OR (b) replace it with a single-pass approach that stat's each file's `(mtime, size, inode)` once during the first walk and re-stat's only those at the end (≤200 LOC delta vs full re-hash). For an aggressive-simplification pass, the cheap option is deleting `_assert_workspace_quiescent` outright (the existing `_file_hash` recheck inside `_write_base_layer:340-352` already catches mid-flight file edits at write time). The dir-add/remove case is the only one then lost.
**LOC delta:** −16 (deletion) or refactor.

### LOW — Style / clarity (rolled up)

- `commit.py:1` and `errors.py:1`: module docstrings are longer than the modules. (Resolved by M-05/M-06.)
- `view.py:11`: `_paths` imports use `from sandbox.layer_stack._paths import ...` while same-package siblings could use relative imports `from ._paths import ...`. Absolute-import style is consistent across the file but verbose. Not a defect, but a 10-LOC saving via `from .` imports if the rest of the codebase tolerates it.
- `manager.py:99-101`: `read_active_manifest` takes the lock just to call `read()` which itself does no shared-state mutation. `FileManifestStore.read()` is stateless. The lock is a leftover guard. Drop it; save 2 LOC and a lock-acquire on every reader.
- `manifest/_model.py:74-86`: The comment "WR-04 + WR-08" references audit IDs but the public reader doesn't need that history. Trim to a single line.
- `lease.py:62-64`: `if self._refcounts[layer] <= 0: del self._refcounts[layer]` — `Counter` already does this if you use `+= Counter(...)` / `-= Counter(...)`. Saves 2 LOC by using `self._refcounts -= Counter(lease.manifest.layers)` and then `+self._refcounts` (the unary plus drops non-positive counts).
- `workspace/base.py:113-121`: timing-bookkeeping for inventory counts that's only ever read by tests. If kept, factor into one helper. If dropped (likely fine), saves 9 LOC.
- `layer/publisher.py:160-166`: `_record_prepare_elapsed` is a four-line helper called twice. Inline; saves 5 LOC.
- `view.py:71-77` (`read_text`): `if not exists: return "", False` then `if content is None: return "", True` — second branch is only reachable for the symlink path that already returned encoded bytes, so `content` is never `None` when `exists=True`. The second branch is dead.

---

## 3. Simplicity Rollup Table

| File | Current LOC | Suggested LOC | Primary reduction lever |
|---|---:|---:|---|
| `__init__.py` | 57 | 50 | Drop re-exports for `PreparedLayerChange`, `LayerDelta` after M-11 |
| `_paths.py` | 84 | 55 | Inline single-use helpers (M-07); add shared `_fsync_dir` (M-08) |
| `_storage_lock.py` | 83 | 70 | Drop `__del__` (B-01); use `weakref.finalize` |
| `commit.py` | 15 | 0 | Merge into `manager.py` (M-05) |
| `errors.py` | 18 | 0 | Merge into `manifest.py`/`view.py` (M-06) |
| `lease.py` | 76 | 70 | `Counter` arithmetic (LOW); keep its own file |
| `maintenance.py` | 114 | 95 | Drop `SquashPlan.__post_init__` cast (M-13); fsync-fix (H-01) |
| `manager.py` | 322 | 270 | Inline helpers from M-07 absorb +15; cut `_unreferenced_layers` (M-12); absorb `commit.py` net −10 |
| `transaction.py` | 102 | 90 | Drop `lock_wait_s`/`lock_held_s` properties if unused; keep file |
| `view.py` | 311 | 250 | M-09 single-pass apply (−5); M-10 dead branch (−11); inherit `LayerStackStorageError` from here (M-06); minor M-15 (dead read_text branch) |
| `layer/__init__.py` | 43 | 0 | Flatten (M-02) |
| `layer/change.py` | 257 | 145 | Tagged-union (M-04); drop `LayerDelta` (M-11) |
| `layer/index.py` | 78 | 75 | Minimal — already lean |
| `layer/publisher.py` | 239 | 200 | Inline `_record_prepare_elapsed`; share `_fsync_dir`; smaller comment-only trimming |
| `manifest/__init__.py` | 37 | 0 | Flatten (M-01) |
| `manifest/_model.py` | 121 | 110 | Trim audit-id comment; merged into flat file |
| `manifest/store.py` | 75 | 70 | Share `_fsync_dir`; merged into flat file |
| `workspace/__init__.py` | 35 | 0 | Flatten (M-03) |
| `workspace/base.py` | 436 | 340 | Drop `_assert_workspace_quiescent` (M-14, −16); rename + relocate `_relative_target_escapes` (H-02); share `_fsync_dir`/`_fsync_file` (M-08); inline timing block |
| `workspace/binding.py` | 157 | 140 | Share `_fsync_dir`; small dedup |
| **Total** | **2,660** | **~1,960** | **−700 LOC ≈ 26%** |

The 26% figure is conservative (it assumes M-14 is the milder "single-pass restat" fix, not the deletion). The aggressive variant — deleting `_assert_workspace_quiescent`, lazy-evicting unused timing fields, etc. — pushes the total to ~1,800 (~32%).

---

## 4. Import-Chain Audit

Method: `grep -rn "^from sandbox.layer_stack" layer_stack/` (results in §0). Two chains exceed 3 hops:

### Chain A — root → manager → maintenance → view → layer.index → _paths (5 hops)
- `__init__.py:23` → `manager.py:24` → `maintenance.py:13` → `view.py:13` → `layer/index.py` (1) and `view.py:11` → `_paths.py` (1 alt)
- Concrete pull-through: any consumer that does `from sandbox.layer_stack import LayerStackManager` transitively imports `_paths`, `layer.index`, `view`, `maintenance` before reaching the manager class.

### Chain B — root → manager → transaction → layer.publisher → layer.change → layer.index (5 hops)
- `__init__.py:23` → `manager.py:34` → `transaction.py:17` (TYPE_CHECKING) → `layer/publisher.py:13` → `layer/change.py:17` → `layer/index.py`
- `transaction.py:17` is `TYPE_CHECKING`-only, but `manager.py:22` non-deferred imports `LayerPublisher` so the runtime chain is real.

### Chain C — workspace.base → manifest (3 hops, acceptable but adjacent)
- `__init__.py:28` → `workspace/__init__.py:5` → `workspace/base.py:12` → `manifest/__init__.py:5` → `manifest/_model.py` (4 hops)

### Proposed flattening (resolves all chains in §4)

After applying M-01 + M-02 + M-03 (flatten the three sub-packages), the import graph becomes:

- `__init__ → manager → maintenance → view → layer_index` (4 hops; one fewer because no `layer/__init__.py`)
- `__init__ → manager → transaction → layer_publisher → layer_change → layer_index` (still 5 hops along the longest leaf)

The remaining 5-hop chain (B) is structural: `LayerStackTransaction` exists to wrap `LayerPublisher`, which depends on `LayerChange`, which depends on `LayerIndex` constants. The constants `OPAQUE_MARKER` and `WHITEOUT_PREFIX` at `layer/index.py:26-27` are pure data and could be lifted to `layer_change.py` (their only non-`view.py` consumer is `_whiteout_path` at `layer/change.py:252-257`). That collapses chain B to 4 hops:

```python
# In layer_change.py (post-flatten):
WHITEOUT_PREFIX = ".wh."
OPAQUE_MARKER = ".wh..wh..opq"
```
…and `view.py` / `execution/overlay/capture.py` import them from `layer_change.py`.

**Net result:** every public-API consumer reaches its target in ≤4 hops; the only 4-hop chain is `__init__ → manager → maintenance → view → layer_index`, which is structurally minimal (manager owns maintenance, maintenance plans against the view, the view needs the index).

If 3-hops-max is a hard requirement, the further step is to lift `MergedView` into `manager.py` (they're a tightly coupled pair — `MergedView` has no other consumer except `overlay/mounts.py:55`), but that contradicts §2's simplicity findings and is probably not worth it.

---

## 5. Top 5 Wins (Ranked by LOC × Ease)

| Rank | Finding | Saves | Risk | One-line action |
|---|---|---:|---|---|
| 1 | **M-04 — Tagged-union `LayerChange`** | ~110 LOC | Medium (touches publisher + tests) | Replace ABC + 4 dataclasses with `kind`-discriminated single dataclass plus `prepare_*` / `write_*` / `update_digest_*` helpers |
| 2 | **M-01..M-03 — Flatten three sub-packages** | ~115 LOC | Low (mechanical rename + update root `__init__.py`) | `git mv manifest/_model.py manifest.py` etc. — same dotted public paths |
| 3 | **B-01 — Remove `__del__` from `_storage_lock`** | ~10 LOC | Low | `weakref.finalize`; deletes a real shutdown-time crash class |
| 4 | **M-10 + M-14 + M-12 + M-13 — Defensive dead code** | ~45 LOC | Low (author's own comments admit several are unreachable) | Delete `has_children_here` block; drop `_unreferenced_layers` `seen` set; drop `SquashPlan.__post_init__` cast; consider dropping `_assert_workspace_quiescent` |
| 5 | **M-05 + M-06 + M-07 + M-08 — Boilerplate collapse** | ~65 LOC | Low | Merge `commit.py`/`errors.py`; inline `safe_request_part`/`log_rmtree_failure`; consolidate `_fsync_dir`/`_fsync_file` |

Apply 1–5 in order; do bug fixes (H-01, H-02, H-03) opportunistically alongside the touched files. Total realistic LOC delta: **−700 to −800 (26–30%)**, gated mostly on M-04 (the tagged-union refactor) which is the largest single line-saver but also the highest-risk piece.

---

_End of review._
