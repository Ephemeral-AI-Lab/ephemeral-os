---
phase: layer_stack
reviewed: 2026-05-13T00:00:00Z
depth: standard
files_reviewed: 23
files_reviewed_list:
  - backend/src/sandbox/layer_stack/__init__.py
  - backend/src/sandbox/layer_stack/manager.py
  - backend/src/sandbox/layer_stack/filesystem.py
  - backend/src/sandbox/layer_stack/timing.py
  - backend/src/sandbox/layer_stack/commit/__init__.py
  - backend/src/sandbox/layer_stack/commit/staging.py
  - backend/src/sandbox/layer_stack/layer/__init__.py
  - backend/src/sandbox/layer_stack/layer/change.py
  - backend/src/sandbox/layer_stack/layer/index.py
  - backend/src/sandbox/layer_stack/layer/publisher.py
  - backend/src/sandbox/layer_stack/lease/__init__.py
  - backend/src/sandbox/layer_stack/lease/registry.py
  - backend/src/sandbox/layer_stack/maintenance/__init__.py
  - backend/src/sandbox/layer_stack/maintenance/squash.py
  - backend/src/sandbox/layer_stack/manifest/__init__.py
  - backend/src/sandbox/layer_stack/manifest/model.py
  - backend/src/sandbox/layer_stack/manifest/store.py
  - backend/src/sandbox/layer_stack/view/__init__.py
  - backend/src/sandbox/layer_stack/view/merged.py
  - backend/src/sandbox/layer_stack/workspace/__init__.py
  - backend/src/sandbox/layer_stack/workspace/base.py
  - backend/src/sandbox/layer_stack/workspace/binding.py
findings:
  blocker: 7
  warning: 8
  total: 15
status: issues_found
---

# layer_stack Code Review

**Reviewed:** 2026-05-13
**Depth:** standard
**Files Reviewed:** 23
**Status:** issues_found

## Summary

The layer_stack subsystem is the storage substrate for the sandbox: OverlayFS-like
layers, manifests, leases, squash compaction, merged reads, and workspace binding.
Review surfaced a cluster of **durability defects** (no `fsync` on any of the three
"atomic" write paths — manifest, workspace binding, layer publish) and a cluster of
**robustness defects** in the publisher (no path-traversal validation on persisted
`LayerRef.path`, no dedup so symlink writes to the same path inside one commit will
raise `FileExistsError`, and an opaque-marker apply path that crashes when the
target exists as a non-directory).

Lease refcounting itself is correctly serialized through a single `Counter`-backed
registry; the off-by-one defenses (`<= 0`) are belt-and-braces rather than required.
The squash path's two-phase commit (build checkpoint outside lock, re-check suffix
under lock) is sound for the single-process case. Cross-process safety is *not*
provided — all CAS depends on the in-process `RLock`; the file-system-level race is
intentional but worth surfacing for callers that may share storage_root.

Given the memory log notes on prior overlay incidents in this codebase, defects
here cascade. Recommend fixing all BLOCKERs before this subsystem is exercised
under crash-recovery or adversarial inputs.

## Blockers

### BL-01: `write_manifest_atomic` is not crash-safe — no fsync of contents or parent dir

**File:** `backend/src/sandbox/layer_stack/manifest/store.py:31-39`
**Issue:** The "atomic" write performs `tmp.write_text(...)` followed by
`os.replace(tmp, manifest_file)`. There is **no `fsync` of the tmp file** before
the rename, and **no `fsync` of the parent directory** after. On crash, the
behavior is:

1. ext4 default (`data=ordered`) typically flushes data before the rename's
   journal commit on the *same inode*, but not for `os.replace` which is a
   `rename(2)` between paths. The combination of rename + unflushed file data
   is the textbook "zero-length file after crash" hazard.
2. The directory entry change for the rename is not durable without an fsync
   of `manifest_file.parent`.

Because the manifest is the source of truth for which layers belong to the
active stack, a torn write can leave the system referencing layers that no
longer exist, or losing layers that are pinned by an in-flight commit.

**Fix:**
```python
def write_manifest_atomic(path: str | Path, manifest: Manifest) -> None:
    manifest_file = Path(path)
    manifest_file.parent.mkdir(parents=True, exist_ok=True)
    tmp = manifest_file.with_name(f".{manifest_file.name}.tmp")
    data = json.dumps(manifest.to_dict(), indent=2, sort_keys=True).encode("utf-8")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
    try:
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp, manifest_file)
    dir_fd = os.open(manifest_file.parent, os.O_DIRECTORY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)
```

---

### BL-02: Layer publish is not crash-safe — staging contents and parent dir are not fsynced

**File:** `backend/src/sandbox/layer_stack/layer/publisher.py:95-112`
**Issue:** `publish_layer_locked` writes files into `staging_dir`, then performs
`os.replace(staging_dir, layer_dir)`. No `fsync` is performed on the file
contents inside `staging_dir`, no fsync of `staging_dir` itself before the
rename, and no fsync of `LAYERS_DIR` after the rename. Worse, the
**manifest is written next (BL-01) but references this layer** — on crash,
the manifest can be durable while the layer it points to is empty or partial.

`_write_layer_digest` (line 107) has the same defect: digest file persisted
without fsync, used as the idempotency key on next publish.

**Fix:**
- After all `_write_change` calls and before `os.replace`, `fsync` every regular
  file written into `staging_dir`, then `fsync` `staging_dir` itself.
- After `os.replace`, `fsync` the parent (`LAYERS_DIR`).
- Apply the same pattern to `_write_layer_digest` and to its containing
  `.layer-metadata` directory.

---

### BL-03: `LayerRef.path` is not validated for traversal — `_remove_unreferenced_layers` will `rmtree` arbitrary paths

**File:** `backend/src/sandbox/layer_stack/manifest/model.py:20-25` (validation
site) and `backend/src/sandbox/layer_stack/filesystem.py:22-26`
(`resolve_storage_path`); consumed by
`backend/src/sandbox/layer_stack/manager.py:245-264`
(`_remove_unreferenced_layers` → `remove_path`) and
`backend/src/sandbox/layer_stack/view/merged.py:191-199` (`_layer_dir`).
**Issue:** `LayerRef.__post_init__` only checks `layer_id` and `path` are
non-empty. There is no rejection of:

- absolute paths (`/etc/passwd`) — `resolve_storage_path` short-circuits and
  returns the absolute path as-is;
- `..` segments (`../../etc`) — `Path(storage_root) / "../../etc"` resolves to
  `<root>/../../etc` which `is_dir`/`rmtree` follow to outside `storage_root`.

In normal operation the publisher only writes paths of the form
`f"{LAYERS_DIR}/{layer_id}"`, so this is dormant. But:

1. `manifest.json` is on disk and may be corrupted by a torn write (see BL-01).
2. The data model accepts adversarial input from `Manifest.from_dict` with no
   path validation.
3. Defense in depth: the entire layer-stack relies on the assumption that
   `LayerRef.path` is rooted under `storage_root`. Any code path that produces
   a `LayerRef` from external data violates this assumption silently.

When `release_lease` → `_remove_unreferenced_layers` runs against a tampered
manifest, `remove_path(self._layer_path(layer))` will `rmtree` arbitrary
filesystem locations.

**Fix:** In `LayerRef.__post_init__`, validate the path is a relative POSIX
path under `LAYERS_DIR` with no `..` segments:
```python
def __post_init__(self) -> None:
    if not self.layer_id:
        raise ValueError("layer_id must not be empty")
    if not self.path:
        raise ValueError("layer path must not be empty")
    parts = PurePosixPath(self.path).parts
    if PurePosixPath(self.path).is_absolute():
        raise ValueError(f"layer path must be relative: {self.path}")
    if any(part == ".." for part in parts):
        raise ValueError(f"layer path must not contain '..': {self.path}")
    if parts[:1] != (LAYERS_DIR,):
        raise ValueError(f"layer path must live under {LAYERS_DIR}: {self.path}")
```
(adjust import to avoid cycles; or co-locate the validation in
`resolve_storage_path`).

---

### BL-04: `_apply_layer` opaque-marker handling crashes when target is a regular file/symlink

**File:** `backend/src/sandbox/layer_stack/view/merged.py:210-214` (call site) and
`backend/src/sandbox/layer_stack/view/merged.py:266-269` (`_clear_directory`).
**Issue:** When applying an opaque marker, the code computes
`target = dest / marker.parent.relative_to(layer_dir)` and calls
`_clear_directory(target)`. `_clear_directory` unconditionally calls
`path.mkdir(parents=True, exist_ok=True)`.

Per overlayfs semantics, an opaque marker at `dir/.wh..wh..opq` means "in this
layer, `dir` is a fresh empty directory; mask everything older at `dir`."
If a younger layer below has *already* written a regular file or symlink at
`dest/dir` during `_apply_layer`'s entry pass — or if `dest/dir` exists as a
file from a previous prepended layer — `target.mkdir(exist_ok=True)` raises
`FileExistsError` because `exist_ok=True` only suppresses the error when the
existing path is a directory.

This is reachable any time an upper layer converts a file path to a directory
and then marks that directory opaque, which is a legitimate overlay sequence.

**Fix:** In `_clear_directory`, remove the target if it's not a directory
before mkdir:
```python
def _clear_directory(path: Path) -> None:
    if path.exists() and not path.is_dir() or path.is_symlink():
        remove_path(path)
    path.mkdir(parents=True, exist_ok=True)
    for child in path.iterdir():
        remove_path(child)
```

---

### BL-05: `_write_symlink` and `_write_file` reject duplicate same-path changes in a single publish

**File:** `backend/src/sandbox/layer_stack/layer/publisher.py:186-189`
(`_write_symlink`) and the call site `publish_layer_locked` at
`backend/src/sandbox/layer_stack/layer/publisher.py:97-98` (loops raw
`prepared_changes` without dedup).
**Issue:** `_prepare_changes` does **not** deduplicate by `change.path`. The
existing dedup helper `aggregate_layer_changes`
(`layer/change.py:184-189`) is not called inside the publisher. Callers are
expected to dedup, but the actual caller chain in
`sandbox/occ/commit_transaction.py:151-156` flattens *across* `LayerDelta`s:

```python
changes = tuple(
    change
    for _, accepted_delta in validations
    if accepted_delta is not None
    for change in accepted_delta.changes
)
```

Each `accepted_delta` is individually deduped, but a single commit can produce
multiple deltas, and two deltas can land changes on the same path
(e.g. a transient write then a delete; or two writes from different validators).

When that happens:

1. `_write_symlink` calls `os.symlink(...)` without unlinking the target →
   `FileExistsError` if the path already received a write/symlink earlier in
   the same publish.
2. `_write_file` does call `remove_path(target)` first (line 183), but
   `target.parent.mkdir(parents=True, exist_ok=True)` will raise if a prior
   change in the same publish made `target.parent` a regular file.
3. The digest is content-sensitive to duplicate count (each duplicate appends
   to the rolling sha256 in `_prepare_changes`), so the idempotency short-cut
   in `publish_layer_locked` at line 78 will not recognize a re-publish with
   the same logical set of changes but different physical multiplicities.

**Fix:** Either (a) call `aggregate_layer_changes` at the entry point of
`publish_layer_locked` to canonicalize before prepare/write, or (b) make
`_write_symlink` / `_write_file` last-write-wins idempotent
(`remove_path(target)` before each filesystem op for all four change kinds).
Option (a) is safer because it also stabilizes the digest.

---

### BL-06: `write_workspace_binding_atomic` has the same crash-safety defect as BL-01

**File:** `backend/src/sandbox/layer_stack/workspace/binding.py:95-107`
**Issue:** Identical pattern to BL-01: `tmp.write_text(...)` then
`os.replace(tmp, path)`, no fsync of contents or parent. The workspace
binding records which manifest version is active and which root hash is
"base"; a torn write would orphan the binding from the layer stack on the
disk. Some recovery paths in `_reject_existing_base_state` (`workspace/base.py:173-187`)
will then misclassify the stack as "empty" or "already exists" depending on
exactly which torn state survived.

**Fix:** Same as BL-01 — `os.fsync` on the tmp file's fd before
`os.replace`, then `fsync` the parent directory.

---

### BL-07: `_write_base_layer` is not crash-safe and leaves no recovery handle

**File:** `backend/src/sandbox/layer_stack/workspace/base.py:296-331`
**Issue:** `_write_base_layer` writes files into `staging_dir` with
`shutil.copy2`, performs `os.replace(staging_dir, layer_dir)`, and returns.
No file fsync, no directory fsync, no digest written (unlike
`publish_layer_locked` which at least writes a `.layer-metadata/{id}.digest`).
On crash:

- The base layer dir may exist but contain partial file contents.
- The manifest (next operation in `build_workspace_base`, line 143) may be
  durable while the layer it references is corrupted.
- Subsequent `build_workspace_base` calls hit
  `_reject_existing_base_state` which sees `LAYERS_DIR` non-empty and refuses
  to retry, leaving the stack permanently unusable.

**Fix:** fsync each copied file before the staging→layer rename, fsync
staging dir, then fsync `LAYERS_DIR` after the rename. Also write a base
digest analogous to `_write_layer_digest` so corruption is detectable on
load.

---

## Warnings

### WR-01: `prepare_workspace_snapshot` failure-path cleanup swallows errors and removes a non-leaf

**File:** `backend/src/sandbox/layer_stack/manager.py:125-130`
**Issue:** On any exception during `_view.materialize(...)`, the cleanup is:

```python
if lowerdir is not None:
    shutil.rmtree(lowerdir.parent, ignore_errors=True)
```

Two concerns:

1. `lowerdir.parent` is `<storage>/runtime/transient-lowerdirs/<request>-<hex>`,
   and `lowerdir` is `<...>/lower`. Removing `lowerdir.parent` removes the
   entire per-request directory including any sibling files a caller may have
   placed there. This is the intended cleanup, but the path naming makes it
   easy to misread; consider removing `lowerdir` and letting the parent be
   pruned separately, or rename so `lowerdir.parent` is obviously the
   per-request scratch root.
2. `ignore_errors=True` masks ENOSPC, EACCES, and other failures that should
   be surfaced or at least logged. A leak of transient lowerdirs on every
   failed snapshot is a slow disk-fill bug.

**Fix:** Use `shutil.rmtree(lowerdir.parent, onerror=_log_and_continue)` and
plumb a logger; or at minimum, raise the original exception (already done via
`raise`) while attempting cleanup. Consider also wrapping the lease release
in a try so a cleanup failure doesn't leak the lease.

---

### WR-02: `publisher._prepare_changes` reads `change.source_path` with no scoping

**File:** `backend/src/sandbox/layer_stack/layer/publisher.py:217-223`
**Issue:** `Path(change.source_path).read_bytes()` is called for every
`WriteLayerChange` with no validation that `source_path` lives under the
commit staging area (or anywhere expected). Any caller that produces a
`WriteLayerChange("dst", source_path="/etc/shadow", ...)` will have its
contents copied into the layer.

In the current call chain (OCC commit), `source_path` comes from the
sandboxed process via `CommitStagingArea`, so it is in principle bounded.
But there is no enforcement at this layer, and the staging area is also
host-accessible. This is the publisher's last chance to refuse out-of-scope
sources.

**Fix:** Either thread the staging root into `LayerPublisher.__init__` and
assert `source_path` is under it (after `resolve(strict=True)`), or document
the invariant explicitly and add a runtime check in debug builds.

---

### WR-03: `release_lease` performs filesystem I/O while holding `LayerStackManager._lock`

**File:** `backend/src/sandbox/layer_stack/manager.py:132-142`
**Issue:** `_remove_unreferenced_layers` is called under `self._lock` and can
issue many `shutil.rmtree` operations synchronously. This serializes all
manager operations (manifest reads, lease acquires, publishes) behind GC.
Under load with many concurrent lease releases this becomes a head-of-line
blocking issue.

Performance is out of v1 scope per review guidelines, but flagging because
this is also a correctness concern: a slow rmtree can block an in-flight
publish that is waiting on the same lock, indirectly causing OCC retries to
balloon and (with BL-01/02 unfixed) widens the crash-exposure window.

**Fix:** Move layer eviction outside the lock by snapshotting the list of
layers-to-remove under lock, then releasing the lock and performing rmtree.
The `pinned_layers` check protects against eviction races with new lease
acquires after the unlock.

---

### WR-04: `Manifest.from_dict` accepts partial payloads and reports them as `KeyError`

**File:** `backend/src/sandbox/layer_stack/manifest/model.py:56-66`
**Issue:** `int(payload["version"])` raises `KeyError`, not a domain-level
error. `Manifest.from_dict` also accepts a missing/empty `layers` (`.get("layers", ())`)
silently — combined with a corrupted manifest.json this can promote a torn
write to a "happy path" empty manifest, which then triggers
`_reject_existing_base_state` falsely.

**Fix:** Raise `ManifestConflictError("manifest payload missing required
field: version")` on `KeyError`; reject missing/empty `layers` only via an
explicit "this manifest has no layers" code path rather than defaulting.

---

### WR-05: `_collect_base_entries` records raw symlink targets without validation

**File:** `backend/src/sandbox/layer_stack/workspace/base.py:287-293`
**Issue:** Workspace symlinks are persisted via `os.readlink(path)` and
recreated at materialize time with the same literal target. A workspace
symlink to `/etc/shadow` (absolute) or `../../etc/shadow` (escapes
workspace) becomes part of the base layer. When materialized into an
overlay lowerdir for a sandboxed process, the symlink is dereferenced in
the sandbox context.

For trusted-workspace assumptions this is intentional. For workspaces
populated from untrusted input (clone of a third-party repo containing a
malicious symlink) this is a footgun. Note: `validate_workspace_binding_paths`
only checks the binding paths themselves, not the workspace contents.

**Fix:** Either document the trust requirement on `workspace_root` contents,
or add a `_symlink_entry` validation that rejects absolute targets and
relative targets that escape `workspace`.

---

### WR-06: Publisher's CAS is only safe within a single process

**File:** `backend/src/sandbox/layer_stack/layer/publisher.py:124-144`
**Issue:** The publisher re-reads the manifest after writing the layer dir
and compares with the cached `active` before calling
`write_manifest_atomic`. This is a TOCTOU window: between
`latest = read_manifest(...)` (line 125) and `write_manifest_atomic(...)`
(line 140) a second process could have written. Within a single Python
process the manager `RLock` serializes everything, but
`LayerStackManager.__init__` accepts any `storage_root` and the
`workspace_server` keeps a cache keyed by path — nothing prevents two
managers in different processes from pointing at the same root.

**Fix:** Either document explicitly that `storage_root` is single-writer
only and add a `flock` advisory lock at manager construction, or implement
the CAS via `os.replace` on a sentinel file that includes the expected
version number.

---

### WR-07: `MergedView._layer_index_cache` mutation is concurrent without coordination

**File:** `backend/src/sandbox/layer_stack/view/merged.py:34-49`
**Issue:** Reads (`read_bytes`, `list_dir`, `read_symlink`) can race with
`evict_layer_index` calls coming from `_remove_unreferenced_layers`. CPython
dict ops are atomic for `setdefault` and `pop`, so structural corruption is
avoided. However: a reader can hold a reference to a `LayerIndex` for a
layer that has just been evicted *and* whose layer dir has been rmtreed.
The reader's subsequent `candidate.read_bytes()` (line 63) will then raise
`FileNotFoundError`, which is propagated to the caller as a hard error
rather than a "layer no longer present" signal.

Although the lease registry is supposed to prevent this (a layer can't be
evicted while pinned), `read_bytes` accepts an arbitrary `Manifest` argument
— including one obtained from a stale source. The contract that all
callers must hold a lease is not enforced at this entry point.

**Fix:** Wrap layer reads in a try/except for the rmtreed-out-from-under-us
case and raise a typed `LayerStackStorageError` (already defined) so
callers can distinguish "layer is gone" from "I/O failed".

---

### WR-08: `Manifest.from_dict` truncates non-list `layers` silently before the assertion

**File:** `backend/src/sandbox/layer_stack/manifest/model.py:58-61`
**Issue:** `raw_layers = payload.get("layers", ())` — the default is `()`,
a tuple, but the next line raises if `raw_layers` is not a `list`. A
malformed payload with `"layers": {"id": "x"}` raises "manifest layers must
be a list", but a payload with `"layers"` missing entirely silently
deserializes to an empty manifest. This is a class of TOCTOU on partial
writes: a torn write that loses the `layers` key promotes the manifest to
"empty stack, version N", which then conflicts with disk state.

**Fix:** `raw_layers = payload["layers"]` (no default); require explicit
empty list. Combined with WR-04 this hardens partial-write detection.

---

_Reviewed: 2026-05-13_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
