# layer_stack — Second-Round Adversarial Review

**Scope:** `backend/src/sandbox/layer_stack/` plus callers after the first review's 18 refactor findings landed.
**Date:** 2026-05-15
**Reviewer:** Claude (gsd-code-reviewer)
**Mandate:** Find dead code / scar tissue left behind after the flatten+tagged-union+helper-consolidation passes. Prove every claim.

---

## 1. Executive Summary

The refactor landed cleanly. Most "looks dead" candidates from the prompt are actually live (the factory functions, `LayerChange`, `_storage_lock`, `lock_wait_s/lock_held_s`, the `_BaseEntry` literals, the `kind` discriminant — all have current consumers). The genuine residue is small: **one BLOCKER live-e2e test that silently no-ops** (it monkey-patches a function the refactor deleted), **one duplicate `Literal` definition** (`SymlinkLookup` was extracted in `view.py` but `manager.py` still inlines the same `Literal[...]`), three stale audit-ID/phase comments, two unused-externally `__all__` entries, factory signatures that advertise rejected kwargs, and two `.md` planning artifacts referencing the deleted `sandbox.layer_stack.layer.change` dotted path.

**Total provable LOC to delete:** ~10 in production source + 1 broken e2e test mode (~13 lines around it) + 3 .md path fixes. The headline "delete N files" answer is honest: zero files. The first review's flatten already consumed the structural dead weight; what's left is genuine scar tissue, mostly comments and an unused stale shim.

**Files reviewed:** 14 source files in `layer_stack/` (1,936 LOC) + grep-pass across all callers in `backend/src/` and `backend/tests/`.

---

## 2. Findings

### BLOCKER

#### B-01 — `test_base_import_crash_safety.py` silently no-ops the `after_base_layer_rename_before_manifest` mode

**File:** `backend/tests/live_e2e_test/sandbox/workspace_base/test_base_import_crash_safety.py:62` (inside the embedded `CHILD_SOURCE` script that gets `exec`'d in a subprocess).

**Evidence:**
```bash
$ grep -rn "_assert_workspace_quiescent" backend/src backend/tests
backend/tests/live_e2e_test/sandbox/workspace_base/test_base_import_crash_safety.py:62:    wb._assert_workspace_quiescent = stuck_quiescent
# zero matches in src/
```

The first review's M-14 deleted `_assert_workspace_quiescent` from `workspace_base.py` (verified: no definition exists anywhere in `src/`). The crash-safety probe assigns `wb._assert_workspace_quiescent = stuck_quiescent` (line 62) and then calls `wb.build_workspace_base(...)` (line 73). Python silently accepts the assignment to a non-existent module attribute. Because `build_workspace_base()` no longer calls that function, the `stuck_quiescent` closure (which writes the `marker.write_text("ready\n", ...)` and then `time.sleep(3600)`) is **never invoked**. The probe child will instead complete `build_workspace_base()` successfully and exit before the parent loop sees the marker.

The parent loop (`_kill_case`, lines 119–141 of the same file) then hits the `else: raise AssertionError("%s did not reach interruption point ...")` branch when `marker.exists()` never becomes true (or, if the child happens to win the race and finishes first, hits the earlier `raise AssertionError("%s exited before marker ...")` at line 135).

**Net effect:** the `after_base_layer_rename_before_manifest` interruption point is no longer being tested. Either the test is failing in CI (data loss for the crash-safety guarantee), or it's being skipped/quarantined — in which case the surviving assertion `assert len(rows) == 4` at the outer test body (line 221) is false on the live path, indicating it never runs.

**Action:** Replace the monkey-patch with a hook that exists. Candidate seams in current `build_workspace_base`:
- After `_write_base_layer(stack, entries)` returns (line 122) and before `write_manifest_atomic(...)` (line 127) — the "base layer rename" happens inside `_write_base_layer`'s `os.replace(staging_dir, layer_dir)` at line 272.
- Patching `write_manifest_atomic` directly is the closest existing hook for the "after rename, before manifest publish" interruption point.

A minimal fix: change `wb._assert_workspace_quiescent = stuck_quiescent` to patch the manifest writer instead:
```python
import sandbox.layer_stack.workspace_base as wb
def stuck_after_layer_rename(path, manifest):
    marker.write_text("ready\n", encoding="utf-8")
    time.sleep(3600)
wb.write_manifest_atomic = stuck_after_layer_rename
```

### WARNING

#### W-01 — `SymlinkLookup` literal duplicated in `manager.py`

**File:** `manager.py:12`, `manager.py:215`; should reuse `view.py:23` `SymlinkLookup`.

**Evidence:**
```python
# view.py:23
SymlinkLookup = Literal["symlink", "file", "absent"]

# manager.py:12
from typing import Literal
# manager.py:211-216
def read_symlink(
    self,
    path: str,
    manifest: Manifest | None = None,
) -> tuple[str, Literal["symlink", "file", "absent"]]:
```

`view.py` extracted the alias and lists it in `__all__`. `manager.py` then bypasses the alias and inlines the same `Literal[...]`, requiring its own `from typing import Literal`. Two places to keep in sync if the alphabet ever expands.

**Action:** Import `SymlinkLookup` from `view`, drop the bare `Literal` import.
```python
from sandbox.layer_stack.view import MergedView, SymlinkLookup
...
def read_symlink(self, ...) -> tuple[str, SymlinkLookup]: ...
```

#### W-02 — `layer_change.__all__` exports two names no one uses externally

**File:** `layer_change.py:197-211`.

**Evidence:** `grep -rn` across `backend/src` and `backend/tests` for each name:
- `DigestSink` — referenced only as the parameter type of `update_digest` in `layer_change.py` itself. Zero external uses.
- `LayerChangeKind` — referenced only as the `kind` field type on `LayerChange` in `layer_change.py`. Zero external uses.
- `PreparedLayerChange` — used by `layer_publisher.py:15,159,164` (same package). No external caller. Borderline: not re-exported from the package root, so it's effectively intra-package already. Either keep or drop from `__all__` based on convention.

**Action:** Drop `"DigestSink"`, `"LayerChangeKind"` from `layer_change.py:__all__`. (`PreparedLayerChange` is borderline; safe to leave.)

#### W-03 — Stale audit-ID comments survive in production source

**Files:** `manifest.py:79`, `workspace_base.py:228`, `manager.py:167`.

**Evidence:**
```bash
$ grep -rn "WR-01\|WR-04\|WR-05\|WR-08" backend/src/sandbox/layer_stack/
backend/src/sandbox/layer_stack/manifest.py:79:        # WR-04 + WR-08: require both top-level keys explicitly so a torn
backend/src/sandbox/layer_stack/workspace_base.py:228:    # WR-05: reject obviously unsafe symlink targets so a clone of an
backend/src/sandbox/layer_stack/manager.py:167:                # WR-01: log cleanup errors instead of swallowing them with
```

The first review's LOW section flagged these. The `WR-*` prefixes are audit IDs from an old review pass; they have zero meaning to a reader who hasn't read that review. The explanations themselves are good and should stay; only the `WR-NN +` prefix is dead.

**Action:** Strip the `WR-NN:` / `WR-NN + WR-NN:` prefixes, keep the rest of each comment.

#### W-04 — `DeleteLayerChange` / `OpaqueDirLayerChange` / `SymlinkLayerChange` factory signatures advertise kwargs they reject

**File:** `layer_change.py:88-118`.

**Evidence:**
```python
def DeleteLayerChange(
    *,
    path: str,
    source_path: str | None = None,    # __post_init__ raises if not None
    content_hash: str | None = None,   # __post_init__ raises if not None
) -> LayerChange:
    return LayerChange(
        kind="delete", path=path, source_path=source_path, content_hash=content_hash
    )
```

`LayerChange.__post_init__` at lines 62–66 explicitly rejects any non-None `source_path` or `content_hash` for `delete`/`opaque_dir`. Every real production caller (`occ/stage/direct.py:214`, `occ/stage/gated.py:65,313`, plus tests at `test_lease_pinning.py:119`, `test_layer_index.py:76`) calls with `path=...` only. The dead kwargs are leftover from the ABC-era subclass signatures where every subclass shared the same `__init__`.

**Action:** Tighten each factory to its valid kwargs:
```python
def DeleteLayerChange(*, path: str) -> LayerChange:
    return LayerChange(kind="delete", path=path)

def OpaqueDirLayerChange(*, path: str) -> LayerChange:
    return LayerChange(kind="opaque_dir", path=path)

def SymlinkLayerChange(*, path: str, source_path: str) -> LayerChange:
    return LayerChange(kind="symlink", path=path, source_path=source_path)
```
(WriteLayerChange already needs all three kwargs.) The runtime check in `__post_init__` should remain as a defense for direct `LayerChange(...)` construction.

**Test-compatibility:** `tests/unit_test/test_sandbox/test_layer_stack/test_manifest.py:84-92` currently exercises the runtime guard by passing rejected kwargs to the factory and expecting `ValueError`:
```python
with pytest.raises(ValueError, match="delete changes must not carry source_path"):
    DeleteLayerChange(path="old.py", source_path=str(source))
with pytest.raises(ValueError, match="symlink changes must not carry content_hash"):
    SymlinkLayerChange(path="link.py", source_path="target.py", content_hash="x")
```
After tightening, these calls become `TypeError: ... got an unexpected keyword argument`. The fix is to either:
- (a) **rewrite the two assertions to construct `LayerChange(kind="delete", path=..., source_path=...)` directly**, keeping the `__post_init__` runtime guard under test, or
- (b) **delete the two assertions** — the rejected-kwargs case is statically impossible once the factory signatures are tightened, so the runtime test becomes equivalent to a `mypy` check.

Option (a) is preferred; the guard still protects `LayerChange.from_dict`-style construction if added later.

#### W-05 — Planning markdown references the old `sandbox.layer_stack.layer.change` dotted path

**Files:**
- `tests/live_e2e_test/sandbox/phase-02-p0-finish-report.md:18` — `sandbox.layer_stack.layer.change.aggregate_layer_changes`
- `tests/live_e2e_test/sandbox/IMPLEMENTATION_PLAN.md:78` — `sandbox.layer_stack.layer.publisher`
- `tests/live_e2e_test/sandbox/IMPLEMENTATION_PLAN.md:116` — `sandbox.layer_stack.layer.change`

**Evidence:**
```bash
$ grep -rn "sandbox\.layer_stack\.layer\." backend/tests/live_e2e_test/*.md backend/tests/live_e2e_test/sandbox/*.md
backend/tests/live_e2e_test/sandbox/IMPLEMENTATION_PLAN.md:78:| 2 | ... | `sandbox.layer_stack.layer.publisher` | ...
backend/tests/live_e2e_test/sandbox/IMPLEMENTATION_PLAN.md:116:| 17 | ... | `sandbox.layer_stack.layer.change` | ...
backend/tests/live_e2e_test/sandbox/phase-02-p0-finish-report.md:18:- `sandbox.layer_stack.layer.change.aggregate_layer_changes`
```

The first review's M-02 deleted the `layer/` sub-package; the current dotted paths are `sandbox.layer_stack.layer_change` / `sandbox.layer_stack.layer_publisher`. These docs lie. They're test-planning artifacts (`.md`, not code), so they don't break anything functionally, but they will mislead anyone reading them.

**Action:** Update the three paths to the flat names. Or, if the docs are frozen historical reports, add a one-line note at the top of each that the dotted paths were renamed in the flatten pass.

---

## 3. Rollup Table

| Symbol / artifact | Verdict | Reason |
|---|---|---|
| `wb._assert_workspace_quiescent` in `test_base_import_crash_safety.py:62` | **delete + replace** | Function deleted in M-14; monkey-patch is now a no-op (BLOCKER) |
| `Literal["symlink", "file", "absent"]` inlined in `manager.py:215` | **replace with import** | `view.SymlinkLookup` exists; duplicate definition (W-01) |
| `from typing import Literal` in `manager.py:12` | **delete** | Only used by W-01 inlined literal |
| `"DigestSink"` in `layer_change.__all__:199` | **delete** | Zero external readers (W-02) |
| `"LayerChangeKind"` in `layer_change.__all__:201` | **delete** | Zero external readers (W-02) |
| `"PreparedLayerChange"` in `layer_change.__all__:203` | **keep (borderline)** | Used by `layer_publisher.py` (intra-package) |
| `"DeleteLayerChange"`, `"OpaqueDirLayerChange"`, `"SymlinkLayerChange"`, `"WriteLayerChange"` in `layer_change.__all__` | **keep** | Re-exported by `layer_stack/__init__.py`; many callers |
| `# WR-04 + WR-08:` prefix in `manifest.py:79` | **delete prefix, keep body** | Audit-ID is stale (W-03) |
| `# WR-05:` prefix in `workspace_base.py:228` | **delete prefix, keep body** | Audit-ID is stale (W-03) |
| `# WR-01:` prefix in `manager.py:167` | **delete prefix, keep body** | Audit-ID is stale (W-03) |
| `DeleteLayerChange(*, path, source_path=None, content_hash=None)` signature | **tighten** | Only `path` is valid; rejected at runtime (W-04) |
| `OpaqueDirLayerChange(*, path, source_path=None, content_hash=None)` signature | **tighten** | Only `path` is valid (W-04) |
| `SymlinkLayerChange(*, path, source_path=None, content_hash=None)` signature | **tighten** | `content_hash` rejected at runtime (W-04) |
| `test_manifest.py:84-92` runtime-guard `pytest.raises` blocks | **rewrite or delete** | Follow-up to W-04 tightening |
| `sandbox.layer_stack.layer.change` references in `phase-02-p0-finish-report.md`, `IMPLEMENTATION_PLAN.md` | **rewrite or annotate** | Dotted path no longer exists (W-05) |
| `_DirectoryEntry` / `_FileEntry` / `_SymlinkEntry` (workspace_base.py) | **keep** | `entry.kind` consumed at `_update_root_hash:306` |
| `_BaseEntry` TypeAlias | **keep** | Used as concrete type hint at lines 169, 226, 240, 305 |
| `SymlinkLookup` re-export at `view.py:34` | **keep** | Used by W-01 import |
| `normalize_layer_path` | **keep** | 6 callers across `view`, `workspace_binding`, `overlay_change`, `occ/overlay`, `occ/router` |
| `aggregate_layer_changes` | **keep** | Called by `layer_publisher`; exercised by tests |
| `prepare_layer_change` / `write_layer_change` / `update_digest` | **keep (internal)** | All three are called by `layer_publisher._prepare_changes` and `publish_layer` |
| `LayerStackTransactionHandle` | **keep** | Constructed at `manager.py:112` |
| `lock_wait_s` / `lock_held_s` properties on `LayerStackTransaction` | **keep** | Consumed by `occ/stage/transaction.py:70,342` and 10+ e2e probes |
| `StorageWriterLockLease` | **keep** | Returned from `acquire_storage_writer_lock`; held by `LayerStackManager._storage_writer_lock` |
| `WORKSPACE_BASE_LAYER_ID` | **keep** | Used by `test_workspace_base.py:40` |
| `manifest_still_ends_with` | **keep** | Used by `manager.squash` (line 275) |
| `discard_checkpoint` | **keep** | Used by `manager.squash` (line 296) |
| Every `import os` in `layer_stack/*.py` | **keep** | Each `os` is used (`os.replace`, `os.symlink`, `os.readlink`, `os.walk`, `os.open`, `os.fsync`, `os.close`, `os.link`, `os.write`) |

---

## 4. Top Wins

Ranked by impact × ease. Honest assessment: total LOC saved is modest because the first review already harvested the structural redundancy.

| Rank | Finding | Saves | Risk | One-line action |
|---|---|---:|---|---|
| 1 | **B-01 — Fix `test_base_import_crash_safety` `after_base_layer_rename_before_manifest` mode** | (correctness) | Medium | Replace `wb._assert_workspace_quiescent = ...` with a patch on `wb.write_manifest_atomic` so the crash-safety probe actually probes |
| 2 | **W-04 — Tighten factory signatures (+ rewrite/delete two `pytest.raises` blocks in test_manifest.py)** | ~6 LOC + clarity | Low | Drop unused kwargs from `DeleteLayerChange`/`OpaqueDirLayerChange`/`SymlinkLayerChange`; rewrite the two compatibility-broken test assertions to call `LayerChange(...)` directly or delete them |
| 3 | **W-01 — Use `view.SymlinkLookup` in `manager.py`** | ~2 LOC + 1 import | Low | Import `SymlinkLookup` from `view`, drop bare `Literal` import in `manager.py` |
| 4 | **W-02 — Trim `__all__`** | 2 LOC | Low | Drop `DigestSink`, `LayerChangeKind` from `layer_change.py:__all__` |
| 5 | **W-03 — Drop stale audit-ID prefixes** | 0 LOC (comment readability) | Low | Strip `# WR-NN:` from three comments |
| 6 | **W-05 — Fix dotted paths in two .md docs** | 0 LOC (correctness of docs) | Low | Rewrite three lines across two markdown files |

**Headline total:** ~10 production LOC of trim + 1 broken e2e mode + 3 .md path fixes. The cleanup was thorough.

---

## 5. Advisory (Couldn't Fully Verify / Out of Scope)

These look suspicious or worth noting but I can't prove them dead without runtime tracing or wider context:

- **`view.py:153-157` floating comment.** After the first review's M-10 deleted the `has_children_here` block, the comment "A plain whiteout on rel (without an opaque marker) cannot appear with same-layer children produced by this module's publisher; the case isn't represented here" describes a missing branch by reference to deleted code. It's correct documentation of an invariant — but it's also possible the invariant should now be enforced (e.g., assert it in `aggregate_layer_changes`) rather than narrated. Not flagged as a finding because it's a defensible design comment.
- **`_DirectoryEntry` / `_FileEntry` / `_SymlinkEntry` tagged-union pattern in `workspace_base.py:57-78`.** Confirmed live (`entry.kind` consumed at line 306). The prompt asked specifically about these — they are not dead. Pattern is idiomatic for a discriminated dataclass union, no action.
- **`PreparedLayerChange` `__all__` exposure.** Listed in `layer_change.__all__` but not re-exported by `layer_stack/__init__.py`, and only used by the same-package `layer_publisher`. If the project's convention is "`__all__` is the file's intra-package surface," fine to keep; if it's "`__all__` is the package's outward contract," drop. The convention isn't documented in `CLAUDE.md` — judgment call.
- **`MergedView.evict_layer_index` tested via `monkeypatch.setattr` in `test_lease_pinning.py:62-72`.** Test spies on the method to verify `manager._remove_layers` calls it. Load-bearing invariant; the test correctly catches its violation. Not dead — but worth noting that any future relocation of `evict_layer_index` would silently break this invariant check. Out of scope for this review.
- **`tests/live_e2e_test/sandbox/IMPLEMENTATION_PLAN.md`** — full review not in scope. The two `sandbox.layer_stack.layer.*` references caught by W-05 may be a sample of broader staleness; a separate sweep of this doc is warranted.

---

_End of review._
