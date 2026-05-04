# Phase 01 - Layer Stack Foundation Implementation Report

Companion to
[`phase-01-layer-stack-foundation.md`](./phase-01-layer-stack-foundation.md).
This report records the storage foundation delivered for the per-call snapshot
layer stack, the behavior now covered by tests, and the work intentionally left
for later phases.

---

## 1. Verdict

**Phase 01 is implemented and verified in the current checkout.**

The new `sandbox.layer_stack` package provides the durable append-only storage
vocabulary required by later overlay/OCC phases:

- active manifests with newest-first `LayerRef` entries
- storage-level `LayerChange` and `LayerDelta` objects
- newest-first merged reads over frozen manifests
- exact layer-ref snapshot leases and idempotent release
- a process-local manifest transaction shell
- a policy-blind immutable layer publisher with manifest CAS checks

No overlay mount code, OCC conflict policy, gitignore evaluation, shell result
projection, squash policy, runtime dispatch wrapper, or legacy
`sandbox/overlay/layer_manager.py` module was added.

---

## 2. File Inventory

### Runtime Package

| File | Purpose |
| --- | --- |
| `backend/src/sandbox/layer_stack/__init__.py` | Public exports for the layer-stack storage package |
| `backend/src/sandbox/layer_stack/manifest.py` | `LayerRef`, `Manifest`, manifest serialization, atomic manifest writes, manifest conflict error |
| `backend/src/sandbox/layer_stack/changes.py` | `LayerChange`, `LayerDelta`, storage path normalization and payload validation |
| `backend/src/sandbox/layer_stack/lease_registry.py` | `Lease` and exact layer-ref refcount registry |
| `backend/src/sandbox/layer_stack/merged_view.py` | Newest-first reads, directory listing, symlink reads, materialization, whiteout/opaque-dir semantics |
| `backend/src/sandbox/layer_stack/publisher.py` | Immutable layer creation from accepted changes and active-manifest CAS publish |
| `backend/src/sandbox/layer_stack/stack_manager.py` | Public facade for active manifest reads, leases, merged reads, and transactions |

### Tests

The phase spec named `backend/tests/sandbox/layer_stack/`; this checkout's
existing pytest layout uses `backend/tests/test_sandbox/`, so the tests live
under `backend/tests/test_sandbox/test_layer_stack/`.

| Test file | Coverage |
| --- | --- |
| `backend/tests/test_sandbox/test_layer_stack/test_manifest.py` | Manifest round trips, legacy string-ref rejection, path normalization, change payload validation |
| `backend/tests/test_sandbox/test_layer_stack/test_merged_view.py` | Frozen snapshot reads, active manifest advancement, whiteouts, opaque dirs, materialization, symlink preservation |
| `backend/tests/test_sandbox/test_layer_stack/test_snapshot_lease.py` | Exact layer-ref lease refcounts, shared leases, idempotent release, old/new manifest pin separation |
| `backend/tests/test_sandbox/test_layer_stack/test_publisher.py` | Empty publish no-op, immutable layer write, content hash validation, staging cleanup, manifest conflict detection |

---

## 3. Lines Of Code

| Bucket | Files | Lines |
| --- | ---: | ---: |
| Runtime package | 7 | 723 |
| Tests | 4 | 344 |
| **Total** | **11** | **1,067** |

Counted with:

```bash
find backend/src/sandbox/layer_stack backend/tests/test_sandbox/test_layer_stack -type f -print | sort | xargs wc -l
```

---

## 4. Behavior Delivered

### Manifest And Change Contracts

`Manifest` stores `LayerRef` entries newest first. Each `LayerRef` carries both
the logical `layer_id` and the storage path used by readers/publishers. Manifest
JSON round-trips through `manifest.py`, and writes are atomic via temporary file
plus `os.replace`.

`LayerChange` is a storage-level value only:

- `write` requires a source file path and may carry a SHA-256 content hash
- `delete` writes a whiteout marker
- `symlink` uses `source_path` as the link target
- `opaque_dir` writes an opaque directory marker

Path normalization rejects absolute paths, empty paths, and parent traversal.

### Snapshot Lease Flow

`LayerStackManager.acquire_snapshot_lease(owner_id)` reads the active manifest,
records a `Lease`, and increments refcounts for the exact `LayerRef` values in
that frozen manifest. Release is idempotent and decrements only the refs from
the released lease.

The tests verify that a request leasing manifest `M0` keeps reading `M0` even
after a later publish advances the active manifest to `M1`.

### Merged Reads

`MergedView` walks manifest layers newest to oldest and stops at the first
decisive storage entry:

- file hit returns bytes
- symlink hit returns symlink target bytes through `read_bytes` and target text
  through `read_symlink`
- whiteout returns missing
- opaque directory marker hides older children under that directory
- missing layer directories raise `LayerStackStorageError`

Directory listing merges children across layers while respecting whiteouts and
opaque markers. Materialization applies layers oldest to newest and preserves
files, deletes, opaque directories, and symlinks.

### Publish Transaction

`LayerStackTransaction` holds the process-local manifest lock and captures the
active manifest at transaction entry. `LayerPublisher.publish_layer_locked`
checks that the active manifest still matches the expected transaction snapshot,
writes a staging directory, renames it into immutable `layers/<layer_id>`, then
CAS-publishes the new manifest.

Empty change lists return the active manifest unchanged. Hash mismatches fail
before publish, remove staging, and preserve the active manifest.

---

## 5. Exit Criteria Mapping

| Phase 01 exit condition | Implementation evidence |
| --- | --- |
| Read active manifest | `LayerStackManager.read_active_manifest`; `test_publish_empty_changes_is_noop` |
| Lease a snapshot | `LayerStackManager.acquire_snapshot_lease`; `test_acquire_and_release_pin_exact_layer_refs` |
| Read paths through that snapshot | `LayerStackManager.read_bytes/read_text`; `test_read_uses_leased_manifest_not_advanced_active_manifest` |
| Publish accepted `LayerChange` values under a transaction | `LayerStackTransaction.publish_layer`; `test_publish_layer_writes_immutable_layer_and_manifest` |
| Release exact layer refs safely | `LeaseRegistry.release`; `test_releasing_old_snapshot_does_not_unpin_new_active_layer` |

---

## 6. Verification

Focused layer-stack tests:

```bash
uv run pytest backend/tests/test_sandbox/test_layer_stack -q
```

Result:

```text
14 passed in 0.16s
```

Layer-stack package and test lint:

```bash
uv run ruff check backend/src/sandbox/layer_stack backend/tests/test_sandbox/test_layer_stack
```

Result:

```text
All checks passed!
```

Sandbox import-fence plus focused tests:

```bash
uv run pytest backend/tests/test_sandbox/test_import_fence.py backend/tests/test_sandbox/test_layer_stack -q
```

Result:

```text
18 passed in 0.12s
```

Broader sandbox regression suite:

```bash
uv run pytest backend/tests/test_sandbox -q
```

Result:

```text
380 passed in 5.27s
```

Backend lint:

```bash
uv run ruff check backend/src backend/tests
```

Result:

```text
All checks passed!
```

Boundary audit:

```bash
rg -n "sandbox\\.overlay|sandbox\\.occ|gitignore|git\\b|overlay" backend/src/sandbox/layer_stack backend/tests/test_sandbox/test_layer_stack
```

Result: no runtime imports of overlay, OCC, gitignore, or git. The only match is
the non-policy docstring in `changes.py` stating that the package does not
encode those policies.

---

## 7. Deferred Work

| Deferred item | Reason |
| --- | --- |
| Overlay lowerdir mount and upperdir capture | Phase 02 owns overlay snapshot runtime |
| OCC base-hash inference and final conflict decisions | Phase 03/04 own OCC routing and commit policy |
| Gitignore evaluation | Explicitly out of Phase 01 storage foundation |
| Shell result projection | Belongs to overlay shell/runtime integration |
| Squash pressure policy and lease-aware GC | Phase 05 owns squash, lease budgets, and collection |
| Cross-process manifest locking | This phase provides a process-local transaction shell; later runtime integration can add a host/sandbox lock if needed |
| Runtime dispatch wrapper | Removed during cleanup because no Phase 01 caller needs it; the next runtime phase can add a concrete operation boundary when the call site exists |
| `stack_overlay/` prototype package | Removed after the production `sandbox/layer_stack` package and focused tests replaced the prototype evidence |
