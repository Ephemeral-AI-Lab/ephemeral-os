---
phase: sandbox/occ
reviewed: 2026-05-13T00:00:00Z
depth: standard
files_reviewed: 24
files_reviewed_list:
  - backend/src/sandbox/occ/__init__.py
  - backend/src/sandbox/occ/client.py
  - backend/src/sandbox/occ/service.py
  - backend/src/sandbox/occ/commit_transaction.py
  - backend/src/sandbox/occ/ports.py
  - backend/src/sandbox/occ/result_projection.py
  - backend/src/sandbox/occ/capture/__init__.py
  - backend/src/sandbox/occ/capture/overlay.py
  - backend/src/sandbox/occ/changeset/__init__.py
  - backend/src/sandbox/occ/changeset/builders.py
  - backend/src/sandbox/occ/changeset/prepared.py
  - backend/src/sandbox/occ/changeset/types.py
  - backend/src/sandbox/occ/content/__init__.py
  - backend/src/sandbox/occ/content/gitignore_oracle.py
  - backend/src/sandbox/occ/content/hashing.py
  - backend/src/sandbox/occ/merge/__init__.py
  - backend/src/sandbox/occ/merge/direct.py
  - backend/src/sandbox/occ/merge/gated.py
  - backend/src/sandbox/occ/merge/serial.py
  - backend/src/sandbox/occ/routing/__init__.py
  - backend/src/sandbox/occ/routing/orchestrator.py
  - backend/src/sandbox/occ/routing/runtime_ops.py
  - backend/src/sandbox/occ/routing/single_path.py
findings:
  critical: 2
  warning: 6
  info: 3
  total: 11
status: issues_found
---

# Sandbox/OCC: Code Review Report

**Reviewed:** 2026-05-13T00:00:00Z
**Depth:** standard
**Files Reviewed:** 24
**Status:** issues_found

## Summary

OCC's concurrency core (lock-on-snapshot, single-thread `OccSerialMerger`,
CAS-retry bound, atomic `revalidate_and_publish`) is structurally sound: the
serial merger funnels every commit through the publisher lock that snapshots
the active manifest inside the same critical section as `publish_layer_locked`,
making the version check + publish truly atomic per process. `hashlib.sha256`
hashes full buffers — no streaming/truncation risk. `normalize_layer_path` in
`layer/change.py` correctly rejects absolute paths, parent traversal, and
collapses `.`/empty segments — the "fix-dot-path-normalization-tests" branch
appears resolved.

The defects below cluster in three areas:

1. **`DirectMerge.EditChange` branch silently swallows failures** that
   `GatedMerge` correctly rejects — same input, two different outcomes on the
   same trust boundary depending only on whether the path is gitignored. This
   is the highest-severity bug and the one to fix first.
2. **`_LayerChangeStager.write_from_path`** has a hostile error-path default
   (`file_size = 0` on `OSError`) plus a consistency guard that is bypassed
   when caller passes an empty `cached_bytes` for a vanished file — a hash/
   content mismatch can be staged and published.
3. Several smaller bugs (`AttributeError` risk in `gitignore_cache_timings`,
   CAS-exhaustion clobbering of `DROPPED`/`REJECTED` statuses, orchestrator's
   chained-base-hash invariant, batch-window blocking under empty queue) are
   correctness issues whose reachability is gated by current callers but each
   would break the moment a caller pattern changes.

## Critical Issues

### CR-01: `DirectMerge` silently drops `EditChange` failures (data loss)

**File:** `backend/src/sandbox/occ/merge/direct.py:113-127`

**Issue:** When an `EditChange` is routed through `DirectMerge` (i.e. the
path is gitignored or any other `OCC_SKIPPED_MERGE` route), three failure
modes that `GatedMerge` rejects loud are silently absorbed and the group
still reports `ACCEPTED` with unchanged content:

```python
if isinstance(change, EditChange):
    if final_kind != "write":
        continue                                  # (a) prior delete/symlink → silently drop
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        continue                                  # (b) non-utf-8 → silently drop
    if change.old_text in text:
        text = text.replace(change.old_text, change.new_text, 1)  # (c) ignores expected_occurrences
    content = text.encode("utf-8")                # (d) old_text absent → unchanged content, still ACCEPTED
```

Compare `GatedMerge._apply_edit_content` (`merge/gated.py:192-226`), which:
- Returns `ABORTED_OVERLAP` on missing anchor.
- Returns `ABORTED_OVERLAP` on count mismatch vs `expected_occurrences`.
- Returns `ABORTED_OVERLAP` on non-utf-8.
- Replaces `expected_occurrences` times, not 1.

The caller's `EditChange.expected_occurrences` contract is therefore route-
dependent: on a tracked path it's enforced; on a gitignored or untracked
path it is silently violated and the caller is told `ACCEPTED`. `EditChange`
is exported from `sandbox.occ.changeset` (public OCC API), and the
`OCCClient.apply_changeset` boundary accepts arbitrary `Sequence[Change]`,
so a caller can submit `EditChange` against a gitignored path without any
upstream guard.

(Today's host edit tool happens to materialise edits into a `WriteChange`
upstream — `runtime/daemon/handler/tools/edit.py:105-109` — which masks
this from the production tool surface. The bug is still a BLOCKER because
the public OCC API contract is violated and any new caller passing an
`EditChange` will silently lose writes.)

**Fix:** Make `DirectMerge` enforce the same failure modes as `GatedMerge`.
Either delegate to a shared helper or inline the equivalent rejections:

```python
if isinstance(change, EditChange):
    if final_kind != "write" or not initial_exists:
        return _result(group, FileStatus.ABORTED_OVERLAP,
                       "file does not exist", timings), None
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        return _result(group, FileStatus.ABORTED_OVERLAP,
                       "file is not utf-8 text", timings), None
    count = text.count(change.old_text)
    if count == 0:
        return _result(group, FileStatus.ABORTED_OVERLAP,
                       "anchor not found", timings), None
    if count != change.expected_occurrences:
        return _result(group, FileStatus.ABORTED_OVERLAP,
                       "anchor occurrence count mismatch", timings), None
    text = text.replace(change.old_text, change.new_text,
                        change.expected_occurrences)
    content = text.encode("utf-8")
    final_content_path = None
    final_precomputed_hash = None
    continue
```

Add unit tests that submit `EditChange` against a gitignored path for
each of the four failure modes and assert that the result is `ABORTED_*`,
not `ACCEPTED`.

---

### CR-02: `_LayerChangeStager.write_from_path` can stage corrupt content with a stale hash

**File:** `backend/src/sandbox/occ/commit_transaction.py:316-346`

**Issue:** The small-file branch has two stacked weaknesses:

```python
try:
    file_size = os.path.getsize(content_path)
except OSError:
    file_size = 0                       # <-- (1) silent fall-through
if file_size >= _SMALL_FILE_BYTES_THRESHOLD:
    shutil.copyfile(content_path, source)
elif cached_bytes is not None:
    if file_size != len(cached_bytes):
        raise RuntimeError(...)         # consistency guard
    source.write_bytes(cached_bytes)
else:
    source.write_bytes(Path(content_path).read_bytes())
```

1. If `os.path.getsize(content_path)` raises (file evicted from overlay
   between capture and stage, permission flip, EIO, …), `file_size` is
   silently set to `0`. The code then proceeds to write *either*
   `cached_bytes` (whose size, by accident or by design, might also be
   `0` for an empty in-memory cache) *or* call `read_bytes()` (which
   will raise — fine).
2. The consistency guard `file_size != len(cached_bytes)` was added per
   the inline comment "Cheap consistency guard against a caller passing
   bytes for a different file than content_path." But if `file_size == 0`
   from the swallowed `OSError`, the guard accepts `cached_bytes == b""`
   and writes an empty file. The published `LayerChange` then carries
   the caller-supplied `precomputed_hash` — which is the hash of the
   *original*, non-empty content. The stored object becomes 0 bytes but
   the manifest entry advertises a different hash. Future `gated` merges
   that hash that file will see a mismatch and reject legitimate writes;
   any caller that compares hash chains will see corruption.

Reachability in production is narrow (overlay upperdir is generally
stable while OCC commits), but this is the OCC integrity boundary — a
mismatch between published `content_hash` and on-disk bytes is exactly
the failure class OCC exists to prevent.

**Fix:** Do not swallow the `OSError`. Either propagate it (the commit
transaction already catches and surfaces failures per group), or fall
back to reading bytes only when `cached_bytes is None`:

```python
try:
    file_size = os.path.getsize(content_path)
except OSError as exc:
    raise RuntimeError(
        f"stage_write_from_path could not stat {content_path!r}: {exc}"
    ) from exc
```

Independently, verify `precomputed_hash` against the actually-staged bytes
in debug/test builds (or at least on the small-file branch where it costs
~microseconds):

```python
if file_size < _SMALL_FILE_BYTES_THRESHOLD:
    actual = self._hasher.hash_bytes(staged_bytes)
    if actual != precomputed_hash:
        raise RuntimeError(
            f"stage_write_from_path hash mismatch for {path!r}: "
            f"caller={precomputed_hash} actual={actual}"
        )
```

## Warnings

### WR-01: `gitignore_cache_timings` raises `AttributeError` on `PathspecGitignoreOracle`

**File:** `backend/src/sandbox/occ/result_projection.py:69-74`

**Issue:** The function calls `getattr(gitignore, "cache_hits")` and
`getattr(gitignore, "cache_misses")` with **no default**. Only
`SnapshotGitignoreOracle` exposes those counters (`gitignore_oracle.py:172-173`).
`PathspecGitignoreOracle` does not. A caller that wires the projection
helper against the wrong oracle (e.g. a test, a future single-process
path, or an alternative `GitignoreMatcher` implementation that satisfies
the `Protocol` but not the counter convention) crashes with
`AttributeError` at result-shaping time. Production wiring currently
passes the snapshot oracle, so this is latent — but the helper accepts
`object` and silently advertises a contract it doesn't check.

**Fix:** Default the lookup or narrow the parameter type:

```python
def gitignore_cache_timings(gitignore: object) -> dict[str, float]:
    return {
        "gitignore.cache_hits_total": float(getattr(gitignore, "cache_hits", 0)),
        "gitignore.cache_misses_total": float(getattr(gitignore, "cache_misses", 0)),
    }
```

---

### WR-02: `_cas_exhaustion_result` re-stamps `DROP`/`REJECT` paths as `ABORTED_VERSION`

**File:** `backend/src/sandbox/occ/merge/serial.py:185-206`

**Issue:** When the CAS-retry budget is exhausted, every `path_group`
in the combined prepared changeset is rewritten as
`FileStatus.ABORTED_VERSION`:

```python
files = tuple(
    FileResult(
        path=group.path,
        status=FileStatus.ABORTED_VERSION,
        message=message,
    )
    for group in prepared.path_groups
)
```

But the orchestrator can have already produced `DROP`-routed groups
(`.git` paths) and `REJECT`-routed groups (bad paths). Surfacing those
as `ABORTED_VERSION` is incorrect: the caller is told "your version was
stale" when the truth is "you sent us a bad path / a .git path". The
docstring above the constant states retries are structurally unreachable
in the current single-process topology, so this is latent — but the
converter is wrong code that will fire the day Phase 06+ multi-process
wiring lands.

**Fix:** Preserve non-OCC routes through CAS exhaustion:

```python
def _cas_exhaustion_result(prepared, exc):
    msg = f"CAS mismatch retry budget exhausted after {MAX_OCC_CAS_RETRIES}: {exc}"
    files = []
    for group in prepared.path_groups:
        if group.route is RouteDecision.DROP:
            files.append(FileResult(path=group.path, status=FileStatus.DROPPED,
                                    message=group.message or "change dropped"))
        elif group.route is RouteDecision.REJECT:
            files.append(FileResult(path=group.path, status=FileStatus.REJECTED,
                                    message=group.message or "change rejected"))
        else:
            files.append(FileResult(path=group.path,
                                    status=FileStatus.ABORTED_VERSION,
                                    message=msg))
    return ChangesetResult(files=tuple(files),
                           timings={"occ.serial.cas_exhausted": 1.0},
                           published_manifest_version=None)
```

---

### WR-03: Orchestrator attaches one base hash to every chained `WriteChange`/`DeleteChange` in a group

**File:** `backend/src/sandbox/occ/routing/orchestrator.py:104-133`

**Issue:** `_prepare_group` reads the base hash *once* from the snapshot
and applies it to every change in the group that lacks one:

```python
base_hash = base_hash_reader(path) if needs_base_hash else None
prepared_changes = tuple(
    attach_base_hash(change, base_hash) if requires_base_hash(change) else change
    for change in changes
)
```

But `GatedMerge` evaluates the hash chain by recomputing `current_hash`
*after each change is applied*: the second `WriteChange` in a chain
checks `current_hash != expected_hash` where `current_hash` is now the
hash of the first write's bytes, not the snapshot bytes. The expected
hash is still the original snapshot hash — so the second change always
gets `ABORTED_VERSION`. A caller that ever sends `[Write(a), Write(b)]`
for the same path is silently turned into "Write(a) accepted, Write(b)
rejected because version drifted" with the second message blaming a
non-existent concurrent commit.

Practical reachability is low (overlay capture emits ≤1 write per path
and the host write API submits one change at a time), but the
`apply_changeset` boundary takes `Sequence[Change]` and the routing
groups by path — so any caller passing a multi-write group hits this
trap. The fix is either to forbid multi-write groups at the
orchestrator boundary or to chain hashes: each subsequent write should
expect the previous write's content hash.

**Fix:** Either reject multiple WriteChanges per path-group at preparation
time (simpler, current invariant), or have `attach_base_hash` only attach
to the *first* base-hash-requiring change in the group and let downstream
ones inherit from the chain. State the invariant in
`PreparedPathGroup`'s docstring either way.

---

### WR-04: `OccSerialMerger._run` sleeps unconditionally on every batch

**File:** `backend/src/sandbox/occ/merge/serial.py:87-101`

**Issue:** The batcher pulls the first item with a blocking `get()`,
then unconditionally sleeps `self._batch_window_s` (default 2 ms) **even
when the queue is otherwise idle**:

```python
first = self._queue.get()
items = [first]
if self._batch_window_s > 0:
    time.sleep(self._batch_window_s)        # always pays 2 ms latency
while len(items) < self._max_batch_size:
    try:
        items.append(self._queue.get_nowait())
    except queue.Empty:
        break
```

For a workload of single-shot commits (host write API, host edit API)
this adds a fixed 2 ms wall-clock to every commit. The batch window
makes sense if there are queued items, but here it always fires. Closer
to a correctness/quality issue than a perf issue because the OCC service
advertises `occ.apply.commit_s` as the user-visible commit latency and
this sleep is reported in `occ.serial.queue_wait_s`. A worker-thread
implementation that wakes on the first item, peeks the queue
non-blockingly, and only sleeps *if* `len(items) < max_batch_size` and
the queue is non-empty would behave better.

(Per project guidelines, perf is out of scope for v1 unless it's a
correctness issue — surfacing as WARNING because the unconditional sleep
is dead latency that contradicts the docstring's intent of "batching
disjoint prepared changesets".)

**Fix:** Drain the queue first, then sleep only when batching would help:

```python
first = self._queue.get()
items = [first]
while len(items) < self._max_batch_size:
    try:
        items.append(self._queue.get_nowait())
    except queue.Empty:
        break
if self._batch_window_s > 0 and len(items) < self._max_batch_size:
    time.sleep(self._batch_window_s)
    while len(items) < self._max_batch_size:
        try:
            items.append(self._queue.get_nowait())
        except queue.Empty:
            break
```

---

### WR-05: `_combine_prepared` discards per-item `prepared.timings`

**File:** `backend/src/sandbox/occ/merge/serial.py:170-178`

**Issue:** When the merger batches multiple prepared changesets, only
`first.snapshot` is propagated and `first.path_groups` is the only thing
that's concatenated — but `prepared.timings` from every item is dropped
on the floor when building the combined changeset:

```python
return PreparedChangeset(
    snapshot=first.snapshot,
    path_groups=tuple(group for prepared in items for group in prepared.path_groups),
    atomic=any(prepared.atomic for prepared in items),
)                                            # no timings argument → empty dict
```

Later, `_commit_batch` does merge per-item timings back into the result
(`item.prepared.timings`, `result.timings`, …), so the loss is partly
masked. But two artifacts leak through:

1. `snapshot=first.snapshot` — combining changesets prepared against
   different snapshots is silently legal. `revalidate_and_publish`
   re-snapshots under the commit lock so correctness isn't broken, but
   the docstring's "validated like any concurrent commit" is only true
   for the *paths*; the per-item snapshot pointer that callers passed in
   is discarded.
2. `atomic=any(prepared.atomic for prepared in items)` is a sticky
   upgrade: combining one atomic request with N non-atomic requests
   makes every non-atomic request atomic-by-association. A non-atomic
   caller can have their best-effort changeset rejected wholesale
   because they happened to be batched with an atomic caller whose
   single path failed.

**Fix:** Either don't batch atomic and non-atomic requests together, or
keep their atomic semantics scoped to their own group. Concretely,
`_disjoint_batches` should refuse to put an atomic item into the same
batch as any other item (it already does — `prepared.atomic` items go
to `rest`), but `_combine_prepared` should not be called on a batch that
mixes the two. Add `assert all(prepared.atomic == items[0].atomic for prepared in items)`
or fold the atomic bit per-item into the prepared changesets being
combined.

---

### WR-06: `result_projection.committed_paths` empty-list edge case is unreachable but inconsistent

**File:** `backend/src/sandbox/occ/result_projection.py:15-30`

**Issue:** The function's contract is "Return paths of every COMMITTED
`FileResult`, or a single-path fallback":

```python
committed = tuple(f.path for f in files if is_published_status(f.status) and f.path)
if committed:
    return committed
aborted = next(
    (f for f in files if not is_published_status(f.status) and f.path),
    None,
)
if aborted is not None:
    return (aborted.path,)
return (fallback_path,) if not files else ()
```

The final clause returns `(fallback_path,)` if `files` is empty but
otherwise `()` — which is the "all files have no `.path`" branch. This
is a tri-state with no caller documentation: callers that expect at
least the fallback may be surprised by an empty tuple. The current
`is_published_status` check ignores the `aborted` case where every
file's `.path` is falsy (impossible in practice because `FileResult.path`
is `str` and the orchestrator always sets it). Latent inconsistency.

**Fix:** Either document the empty-tuple branch or fall through to
`fallback_path` unconditionally:

```python
if aborted is not None:
    return (aborted.path,)
return (fallback_path,)
```

## Info

### IN-01: `gitignore_oracle._is_dir_excluded` recursion has redundant ancestor walk

**File:** `backend/src/sandbox/occ/content/gitignore_oracle.py:100-114`

**Issue:** `_is_dir_excluded(dir_rel)` walks every ancestor `_is_dir_excluded(accum)`
recursively, which itself walks all of its ancestors. Memoization via
`_dir_cache` saves repeat work, but the first traversal still does
`O(depth^2)` work because each level re-checks all ancestors. The
recursion also doesn't guard against unbounded depth — pathologically
deep paths would blow the Python recursion limit before any business
logic fires. Code is correct, just under-optimized; out of v1 scope for
performance but worth flagging.

**Fix:** Single pass through ancestors with an accumulator:

```python
def _is_dir_excluded(self, dir_rel: str) -> bool:
    if dir_rel in self._dir_cache:
        return self._dir_cache[dir_rel]
    parts = dir_rel.split("/")
    accum = ""
    excluded = False
    for part in parts:
        accum = f"{accum}/{part}" if accum else part
        if not excluded:
            excluded = self._match_with_inheritance(accum, as_directory=True)
        self._dir_cache[accum] = excluded
    return excluded
```

---

### IN-02: `_kept_children_for` does not call `normalize_layer_path` on either side

**File:** `backend/src/sandbox/occ/capture/overlay.py:75-87`

**Issue:** When deriving kept children for an opaque-dir entry, the helper
uses raw `.path` from `OverlayPathChange` items:

```python
prefix = f"{rel}/" if rel else ""
for item in path_changes:
    if item.path == rel or not item.path.startswith(prefix):
        continue
    rest = item.path[len(prefix):]
    if rest:
        kept.add(rest.split("/", 1)[0])
```

The OCC entry-point later normalizes through `normalize_layer_path` in
the orchestrator, but `_kept_children_for` runs *before* normalization
on the upstream `OverlayPathChange` shape. If overlay capture ever emits
a `path` with a trailing slash, leading slash, or `.` segment, the
`startswith(prefix)` and `path == rel` comparisons mis-classify. Today
overlay capture's invariants exclude these inputs, but the function is
silently coupled to that invariant with no assertion.

**Fix:** Either assert path normalization at the function boundary or
delegate to a single normalization helper:

```python
def _kept_children_for(rel, path_changes):
    from sandbox.layer_stack.layer.change import normalize_layer_path
    rel_norm = normalize_layer_path(rel, allow_root=True)
    prefix = f"{rel_norm}/" if rel_norm else ""
    kept: set[str] = set()
    for item in path_changes:
        item_path = normalize_layer_path(item.path)
        if item_path == rel_norm or not item_path.startswith(prefix):
            continue
        rest = item_path[len(prefix):]
        if rest:
            kept.add(rest.split("/", 1)[0])
    return kept
```

---

### IN-03: `OccService._auto_squash_after_publish_coalesced_sync` is hard to reason about

**File:** `backend/src/sandbox/occ/service.py:152-214`

**Issue:** The coalescing dance does:

1. Try non-blocking lock; if it fails:
   - Record `pending_recheck = True` under `state_lock`.
   - If `depth <= 2 * max_depth`, return early with a "skipped_in_flight"
     timing.
   - Else wait for the lock, re-check, run squash, release.
2. If lock acquired:
   - Run squash.
   - Check `pending_recheck` under `state_lock` and clear it.
   - If pending, re-evaluate and possibly run again.

The nested try/finally and the lock-then-state_lock-then-lock cadence
make the invariants ("the lock is always released by the same thread
that took it") hard to verify by reading. There are at least two paths
to `state.lock.release()` from a finally block, and the early-return
inside the `not state.lock.acquire(blocking=False)` branch returns from
*the outer function* — but the second `state.lock.acquire()` blocking
call inside the same branch makes the function temporarily hold the
lock while it was just told someone else has it. The `pending_recheck`
flag isn't cleared on the backpressure path even though we just ran a
squash that satisfies the recheck.

Code appears functionally correct (squash itself acquires the layer-
stack lock internally, so re-running on stale state is safe), but the
control flow is at the edge of "would a senior engineer say this is
overcomplicated?". Per CLAUDE.md guidance, suggest simplifying to a
single owner of the squash decision: if non-blocking acquire fails,
record-and-return; if acquired, run-and-return. Skip the backpressure-
wait branch unless there's a measurable regression without it.

**Fix:** Replace the coalesced logic with the simplest version that
works, then add complexity back only with a perf test:

```python
def _auto_squash_after_publish_coalesced_sync(self, result):
    context = self._auto_squash_context_sync(result)
    if context is None:
        return {}
    squash, active = context
    if active.depth <= self._auto_squash_max_depth:
        return {}
    if not self._coalesced_squash.lock.acquire(blocking=False):
        return {"layer_stack.auto_squash.skipped_in_flight": 1.0,
                "layer_stack.auto_squash.max_depth": float(self._auto_squash_max_depth),
                "layer_stack.auto_squash.depth_before": float(active.depth)}
    try:
        return self._run_squash_for_active_sync(squash, active)
    finally:
        self._coalesced_squash.lock.release()
```

---

_Reviewed: 2026-05-13T00:00:00Z_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
