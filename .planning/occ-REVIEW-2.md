# OCC Subsystem Code Review — Round 2

Reviewed: 2026-05-15
Scope: `/Users/yifanxu/machine_learning/LoVC/EphemeralOS/backend/src/sandbox/occ/` — 21 files, 3031 LOC.
Comparing to round-1 baseline (3184 LOC) and the two landed commits (`5989e5d5`, `d745ee19`).

## TL;DR

- **LOC:** 3031 (was 3184; −153, −4.8%). Achievable further reduction without losing exercised capability: **~640 LOC (~21%)**. The number is lower than round-1's headline ~950 because (a) CQ-01 the biggest single win has been *consciously* deferred (a test pins `MAX_OCC_CAS_RETRIES`), (b) SV-01 async-twin removal has been argued away on concurrency grounds.
- **Top 3 remaining wins (risk-weighted):**
  1. **ST-01 stager merge** — `direct.py` + `gated.py` + `_edit.py` together are 660 LOC; after the `LayerDelta → StagedChanges` migration the structural parallel is *easier*, not harder, to fuse (handler dicts, state dataclasses, and final-state→delta helpers are now isomorphic except for one hash check, one symlink kind, and a delete-when-initial-exists rule). Re-estimated achievable: **~290 LOC, −370.**
  2. **TYP-01 collapse `EagerWritePayload`/`DiskWritePayload` polymorphism into one `WritePayload`** — still 100+ LOC of two-class proxy machinery for one bool of behavior; the round-1 estimate (−55 LOC) holds and the BLOCKER about frozen-dataclass mutation in `_cached_content` is still live.
  3. **TYP-02 hand-rolled `WriteChange.__init__`** is still **broken** (silent class default `payload` annotation will trip dataclass field order if anyone ever drops `init=False`); the round-1 fix stands.
- **New defects from d745ee19:** **4** (1 HIGH, 2 MEDIUM, 1 LOW), plus **1 pre-existing HIGH not yet fixed** (`router.py:215` runtime TypeError invariant — `d745ee19` left it untouched even though it re-shaped the helpers around it).

## What Round 1 Got Right / Wrong

### Items landed (5989e5d5)

- **HIGH `commit_queue.py:210` `BaseException` swallow** — fix landed. `commit_queue.py:214-215` now re-raises non-`Exception` `BaseException`s after fulfilling futures. **Correct fix; matches advisory.** Note: only the *batch-loop* path re-raises; `_run` itself (lines 133-166) still doesn't have a try/except wrapping the dequeue loop, so an unexpected exception from `_disjoint_batches` or `_combine_prepared` would still kill the worker silently. That's not a regression (round-1 covered the inner case only) but worth knowing.
- **HIGH `stage/_edit.py:14-19` `ABORTED_OVERLAP` status taxonomy** — fix landed *but mis-attributed*. Round-1 advisory was: "add a distinct `MISSING` status or reuse `ABORTED_VERSION` for gated, `REJECTED` for direct." The commit chose option B: the `exists` parameter was deleted from `_edit.py`, and each caller (`direct.py:288-293`, `gated.py:269-274`) does its own existence guard returning the route-appropriate status. **Correctly implemented.** Audit below confirms.
- **MEDIUM `stage/transaction.py:326-339` getsize swallow** — fix landed. `transaction.py:308-313` now takes the `cached_bytes` branch unconditionally when provided and lets `os.path.getsize` raise on the disk path. Round-1 was specific about this; commit message matches. **Correct.**

### Items abandoned (with reason)

- **CQ-01 strip Phase-06 placeholder code (−160 LOC)** — abandoned because `test_mutation_gate.py:49-54` and `:95-137` import `MAX_OCC_CAS_RETRIES` and assert the retry budget is exactly `3`. Test was written specifically to lock in the budget so multi-process Phase 06+ inherits it. **Reasonable to defer.** But: the constant `MAX_OCC_CAS_RETRIES = 3` lives in `commit_queue.py:22` and can be exported standalone — *the wrapping `RetryPolicy` dataclass is dead*. See new finding RT-NEW-02 below.
- **SV-01 drop async twins (−110 LOC)** — abandoned, with the stated reason: `commit_queue.apply()` does `asyncio.wrap_future(submit(...))`, yielding the asyncio loop while a *separate* worker thread crunches; switching to `to_thread(apply_sync)` would pin an executor thread blocked on the worker future. **The reasoning is sound *for `commit_prepared`*.** But the four *other* async twins (`apply_changeset`, `prepare_changeset`, `_maintenance_after_publish`, even most of `apply_changeset`'s body) just do `await run_sync_in_executor(self.foo_sync, ...)` — for those, the to_thread swap *is* equivalent and saves LOC. Partial SV-01 is still worth ~50 LOC.

### Re-graded estimates

| Round-1 item | Round-1 LOC est | Round-2 LOC est | Notes |
|---|---|---|---|
| ST-01 (stager merge) | −454 | **−370** | Slight downgrade — the symlink-only / hash-check / final-delta differences cost ~20 LOC to express as predicates. Still the biggest single win. |
| TYP-01..05 (types.py polymorphism) | −80 | **−80** | Unchanged. `LayerDelta` migration was orthogonal to payload polymorphism. |
| GO-01..02 (gitignore_oracle) | −33 | **−33** | Unchanged. Lazy-import dance and double-walk are byte-for-byte the same — see file inspection below. |
| RT-01/03 (router helpers) | −26 | **−0** | DONE in d745ee19 (`_route_change` wrapper dropped; `_attach_chained_base_hashes`/`_next_base_hash` inlined into `_prepare_group`). |
| RT-02/04 (router single-path) | −55 | **−55** | Both deferred (callers `daemon/handler/tools/{edit,write}.py:108/121` consume `prepare_single_path_changeset`); see new finding RT-NEW-01. |
| T-01/02/04 (transaction helpers) | −49 | **−0** | DONE in d745ee19. |
| T-03 (timing accumulator) | −28 | **−28** | Still open. The 8-accumulator + 2-counter block at `transaction.py:80-120` is untouched. |
| MN-01/02 (maintenance) | −21 | **−0** | DONE. |
| SV-02/03/04 (service helpers) | −16 | **−16** | Round-1 SV-02 not addressed; `_total_start` kludge still present. SV-03/04 inlines untouched (`_manifest_lag` already inlined at L185-186; `_result_timings_with_resume` inlined at L165-167). Actually round-2: SV-03/04 are **already inlined**; only SV-02 (~4 LOC) remains. **Revise: −4 LOC.** |
| Hashing class drop | −10 | −10 | Unchanged. |

## New Defects (d745ee19)

### [HIGH] `commit_queue.py:75` mutable default `RetryPolicy()` instance shared across CommitQueue instances

```python
def __init__(
    self,
    transaction: CommitTransaction,
    *,
    max_batch_size: int = 64,
    batch_window_s: float = 0.002,
    retry_policy: RetryPolicy = RetryPolicy(),
) -> None:
```

The `RetryPolicy()` default is constructed exactly once at function-definition time, and every `CommitQueue` whose caller doesn't pass `retry_policy=...` shares the same instance. `RetryPolicy` is `frozen=True` so it can't be mutated by accident, but this is still a Python anti-pattern (mutable-default class) that has bitten this codebase elsewhere — and the **only call site that ever constructs one is line 75 itself**. Combined with the fact that `RetryPolicy` is otherwise unused (see RT-NEW-02), this is a smell pointing at dead code. **This is not a `d745ee19` regression — it predates the refactor** — but the refactor's audit didn't catch it.

**Fix:** drop `RetryPolicy` entirely; the queue takes `max_cas_retries: int = MAX_OCC_CAS_RETRIES`. The test (`test_mutation_gate.py:49`) only imports `MAX_OCC_CAS_RETRIES`, never `RetryPolicy`.

### [MEDIUM] `stage/transaction.py:131-148` atomic-vs-overlay error path now sends the wrong message in mixed scenarios

The pre-`d745ee19` code split this into two helpers — `_must_skip_publish` (predicate) and `_mark_unpublished` (message-picker). The new inline block:

```python
if atomic_failed or overlay_failed:
    message = (
        "not published because atomic changeset validation failed"
        if prepared.atomic
        else "not published because overlay capture OCC-gated validation failed"
    )
```

uses `prepared.atomic` to pick the message, **not the variable that triggered the skip**. If a changeset is *both* atomic *and* an overlay capture with a gated failure (the message-set's intent says they're disjoint, but nothing enforces it), both flags can be true. The original code had the same bug, but the d745ee19 inline puts the two policies side-by-side and makes it visible. Look at the round-1 review's T-01 advisory: it warned against this exact merge ("merge of these two policies into one boolean obscures both").

Concretely, if a caller sets `atomic=True` AND uses overlay capture, an overlay-only failure will be labeled "atomic changeset validation failed." Daemon handlers (`edit.py:112`, `write.py:126`) currently pass `atomic=False`, so the live wire is currently safe — but anyone wiring an atomic overlay would silently mislabel.

**Fix:** pick the message from the actual cause:
```python
if atomic_failed:
    message = "not published because atomic changeset validation failed"
else:  # overlay_failed
    message = "not published because overlay capture OCC-gated validation failed"
```

### [MEDIUM] `stage/transaction.py:96-100` `occ_gated_failed` flag set inside a non-iterating branch makes it conditionally undefined-looking

```python
if (
    group.route is RouteDecision.GATED
    and result.status is not FileStatus.ACCEPTED
):
    occ_gated_failed = True
```

Sets the flag only when the *stager* returned a non-`ACCEPTED` status on a gated path. `_FAILURE_STATUSES` (line 124) includes `ABORTED_OVERLAP`, `ABORTED_VERSION`, `FAILED`, `REJECTED` — but the `result.status is not FileStatus.ACCEPTED` check *also* trips on `FileStatus.DROPPED` and `FileStatus.COMMITTED`. The DROPPED status is legitimately produced by `_validate_group` itself (line 195) for a `RouteDecision.DROP` group, but DROP groups by definition aren't gated (the if at line 96 short-circuits to `RouteDecision.GATED`). So in practice the inconsistency between `is not ACCEPTED` (overlay branch) and `in _FAILURE_STATUSES` (atomic branch) doesn't cause a bug today, but it's a divergence with no semantic justification. Should be `result.status in _FAILURE_STATUSES` for both.

**Fix:** replace line 98-99 with `and result.status in _FAILURE_STATUSES:` so the two branches use the same predicate.

### [LOW] `stage/transaction.py:324-331` `_FAILURE_STATUSES` defined as a module-level frozenset but only used once

The frozenset was extracted from the inlined `_is_failure` helper, but the only consumer is the comprehension on line 124. Defining a module-level constant for a one-shot membership check costs the import-time allocation and adds two lookups. **Fix:** inline as a literal in the comprehension, or keep the constant and reuse it for the related `is_success_status` / `is_published_status` predicates in `changeset/types.py:217-222` (which currently has its own anonymous set literal of overlapping but inverse statuses). Either consolidates the taxonomy or drops the indirection.

### [NEW HIGH — pre-existing, exposed by d745ee19] `router.py:213-218` runtime `TypeError` invariant unchanged

Round-1 flagged this as MEDIUM:

```python
if snapshot is not None:
    if not isinstance(oracle, SnapshotGitignoreMatcher):
        raise TypeError(
            "snapshot-aware OCC routing requires "
            "SnapshotGitignoreMatcher.is_ignored_in_snapshot"
        )
```

`d745ee19` reshaped the helpers around this (inlined `_attach_chained_base_hashes` etc.) but left the per-change runtime check in place. The matcher is set in `Router.__init__`; the type check is wired-once and should be in `__init__`. Promoted to HIGH because the surrounding code is now denser and the unnecessary per-change branch is one of the few remaining hot-path branches. **Fix:** move the `isinstance` check into `Router.__init__` (or constrain the parameter type to `SnapshotGitignoreMatcher`).

## Remaining Simplicity Wins

### ST-01 — stager merge re-estimate

`direct.py` (305 LOC) + `gated.py` (317 LOC) + `_edit.py` (38 LOC) = 660 LOC. Both stagers now return `tuple[FileResult, StagedChanges | None]` (was `LayerDelta | None`). The downstream call site is identical (transaction.py:79, 189). **The migration removed a packaging layer that was the strongest argument for keeping them separate.**

Line-by-line residual differences:

| Concern | `direct.py` line(s) | `gated.py` line(s) | Fuse strategy |
|---|---|---|---|
| `_StageState` content/exists | 41-85 | 38-85 | Single dataclass with `final_kind: Literal[...]` (direct's encoding); add `final_special_change` only for the opaque-dir / symlink fusion |
| Handler dict | 92-98 (5 kinds) | 99-104 (4 kinds; no Symlink) | One dict; gated route's handler-miss for `SymlinkChange` returns a `REJECTED` `FileResult` |
| `stage_group` try/except wrapper | 107-123 | 113-129 | Identical; lift to merged class |
| `_stage_group` outer scaffold (read, apply loop, stage) | 125-224 | 131-190 | Diff: gated computes `current_hash = self._hasher.hash_current(...)` *before each change* (line 152-155); direct doesn't. Make `current_hash_fn` a `Callable | None` field on the merged class — `None` for direct, `hasher.hash_current` for gated. |
| `_apply_change` handler dispatch | 226-240 | 192-207 | Identical signature except the gated handler takes `current_hash` and `path`. Make all handlers accept a `_StageContext` that bundles state + current_hash + path. |
| Per-kind `_apply_*` handlers | 242-302 | 209-283 | Three out of four are identical save the hash-check predicate (gated `_apply_write`/`_apply_delete`). The write/delete predicates `current_hash != expected_hash` can be a route-aware free function: `_check_hash(route, current_hash, expected) -> FileResult | None` that returns `None` for direct, the conflict for gated. |
| Final-state → delta translation | 152-224 (inline in `_stage_group`) | 167-181 + `_delta_for_final_state` 290-314 | Same logic, different encoding. Fuse into one helper that consumes `_StageState`. |

**Fused class outline:**
```python
@dataclass
class _StageState:
    content: bytes
    initial_exists: bool
    final_kind: FinalKind          # write/delete/symlink/opaque_dir
    exists: bool
    final_content_path: str | None = None
    final_precomputed_hash: str | None = None
    symlink_target: str | None = None

class Stager:
    def __init__(self, snapshot_reader, *, route, hasher=None): ...
    def stage_group(self, group, *, active_manifest, stage_write, stage_write_from_path=None) -> tuple[FileResult, StagedChanges | None]: ...
```

`OccService` (currently `transaction.py:58-63`) keeps two instances: `Stager(route=DIRECT)` and `Stager(route=GATED)`. The policy dict stays as is.

**Estimated post-merge LOC: ~290** (state ~25, class ~180, handlers ~50, helpers ~35). **Reduction: −370 LOC.**

Risk: medium. The test surface for stage merging is the largest in the package (`test_direct_merge.py`, `test_mutation_gate.py`, others). Each handler has its own assertion. The per-route timing keys (`DIRECT_*` / `GATED_*`) **must** be preserved — the merged class would select the timing-key family from `route`.

### TYP-01..05 — re-graded

**TYP-01 (collapse `EagerWritePayload`/`DiskWritePayload`):** still valid. The `LayerDelta → StagedChanges` migration was on the **delta** side; the **payload** side is unchanged. Stagers in `direct.py:267-269` and `gated.py:236-238` still read `write.final_content`, `write.content_path`, `write.precomputed_hash` — three properties that proxy to two payload classes for one bit of behavior. The `_cached_content` mutation hack on a `frozen=True` dataclass (`types.py:58-64`) is still live and still a thread-safety bug; if anyone runs two `direct.py:_apply_write` calls in parallel on the same `WriteChange` instance, both will read disk and one cache write wins by race.

Round-2 verdict: **TYP-01 unchanged. −55 LOC. Still load-bearing because `DiskWritePayload._cached_content` mutation is a latent bug.**

**TYP-02 (hand-rolled `WriteChange.__init__`):** unchanged. The current code (`types.py:91-102`) sets `path`, `source`, `base_hash`, `payload` via `object.__setattr__` because `frozen=True` and the path needs `str()` coercion. As round-1 noted, `Change.__post_init__` (line 23-24) already does the path coercion. **Drop the manual `__init__` and the `init=False` decorator argument; let the inherited `__post_init__` do its work.** −12 LOC.

Note: there's a related subtlety — `WriteChange` declares `payload: WritePayload` as a class-level attribute *after* `base_hash: str | None = None` (line 88-89), so removing `init=False` would emit `TypeError: non-default argument 'payload' follows default argument 'base_hash'`. The fix is to make `payload` a keyword-only field or reorder. Round-1 didn't call this out; round-2 does.

**TYP-03 (with_base_hash inconsistency):** unchanged. Still 2 implementations, one via `replace(...)`, one re-named-args. Drop the `WriteChange.with_base_hash` body and use `replace(self, base_hash=base_hash)`. −6 LOC.

**TYP-04 (`build_overlay_write_change` duplicate precondition):** Looking at `types.py:282-292` more carefully — round-1 said the `if final_content is None: raise ValueError` at line 291 is *unreachable* given the guard at line 282. Re-reading carefully: the guard at 282 catches `final_content is None AND content_path is None`. The branch at line 285 catches `content_path is not None AND final_content is None`. Line 290's `else:` branch is reached when `not (content_path is not None and final_content is None)` — i.e. `content_path is None OR final_content is not None`. Combined with the entry guard (which requires at least one to be non-None), the else branch is reached when `final_content is not None`. So line 291's `if final_content is None` *is* unreachable. **Round-1 was right; drop it.** −3 LOC.

**TYP-05 (`_normalize_kept_children` inline):** still valid. −3 LOC.

**Net TYP: −79 LOC.**

### GO-01..02 — verified unchanged

`content/gitignore_oracle.py` is byte-identical to round-1 (235 LOC, same imports, same lazy-import dance at lines 23-31, same double-walk at `_evaluate_file` 100-113 calling `_is_dir_excluded` 115-130). Verdict: round-1 advisory holds without modification. **−33 LOC.**

### Hot-pursuit wins post-d745ee19

**RT-NEW-01 — `prepare_single_path_changeset` module function is still parallel to `Router.prepare_single_path_sync` (router.py:223-237 vs 71-110).** The module function constructs a `Router` and delegates. Two consumers (`daemon/handler/tools/edit.py:108`, `daemon/handler/tools/write.py:121`) use the module function. After d745ee19 the duplication is *smaller* (the helper trio `_route_change`/`_attach_chained_base_hashes`/`_next_base_hash` was inlined into the method) but the method itself (`prepare_single_path_sync`, 40 LOC) is still a parallel mini-pipeline. The fast path's only real value-add over `prepare_sync` is the timing-key set: `PREPARE_SINGLE_PATH_FAST` and `PREPARE_SINGLE_PATH_BASE_HASH`. If those keys can be emitted from `prepare_sync` when `len(changes) == 1`, the method can go. **−40 LOC.**

**RT-NEW-02 — `RetryPolicy` dataclass has 1 internal default-value reference and 0 external constructors.** `grep -rn "RetryPolicy("` returns exactly one hit: `commit_queue.py:75`'s own default. Tests import only `MAX_OCC_CAS_RETRIES`. The dataclass exists "so multi-process Phase 06+ topologies inherit a named, testable limit" — but `MAX_OCC_CAS_RETRIES` already is that, and is the *constant the test actually pins*. **Drop `RetryPolicy`; CommitQueue takes `max_cas_retries: int = MAX_OCC_CAS_RETRIES`.** −10 LOC. This is the win round-1's CQ-01 missed; the test reason for keeping CQ-01 doesn't apply to `RetryPolicy` specifically.

**SV-NEW-01 — `_maintenance_after_publish_sync` is a 3-line null guard then a method call (service.py:147-153).** No dead code, but trivially inlinable into both `commit_prepared` / `commit_prepared_sync` call sites. −4 LOC, +clarity.

**SV-NEW-02 — `_total_start` kludge from round-1 is unchanged.** Both `commit_prepared` and `commit_prepared_sync` take `_total_start: float | None = None` (service.py:75, 119) — a private-by-underscore param sitting in the public signature, used only by `apply_changeset*` (which is the same module). Add internal `_commit_prepared_inner(prepared, total_start)` helpers; public methods become 4 lines each. **−4 LOC.** This is round-1's SV-02 advisory.

**T-NEW-01 — `transaction.py:80-120` 8-accumulator + 2-counter block (T-03 from round-1) still untouched.** d745ee19 simplified other parts of this method but the per-route timing accumulation is intact. Replace with: build `validations` first, then derive the 8 totals + 2 counts from `validations` via `sum(...)` comprehensions over a `(rt, route)` projection. Cleaner because the accumulators no longer leak into the validation loop body. **−28 LOC.**

**HSH-NEW-01 — `ContentHasher` class has two static-style methods, no state.** `content/hashing.py:11-20`. Round-1 flagged. Drop the class; export `hash_bytes` and `hash_current` as module-level functions. The 5 call sites are:
- `router.py:177`  `ContentHasher()` + `hasher.hash_bytes(...)`
- `router.py:185`  `hasher.hash_bytes(...)`
- `content/hashing.py:33` `ContentHasher().hash_bytes(content)`
- `stage/transaction.py:57` `self._hasher = ContentHasher()`
- `stage/gated.py:98` `self._hasher = hasher or ContentHasher()`
Each call site becomes a free function call. **−10 LOC.**

## Import Chains

Re-confirmed **no 4+ deep paths**. Deepest still `sandbox.occ.{changeset,content,stage}.{module}` — 3 levels under `sandbox`, within budget.

**Inconsistent fan-in** (round-1 finding): **unchanged** by `d745ee19`. Survey of the four most-modified files post-refactor:

- `commit_queue.py:13-15`: still imports from `sandbox.occ.changeset.prepared` and `.types` and `sandbox.occ.stage.transaction` directly. Re-exports exist at `sandbox.occ.changeset` and `sandbox.occ.stage`. No change.
- `router.py:10-25`: same — `changeset.prepared`, `changeset.types`, `content.gitignore_oracle`, `content.hashing` all direct.
- `service.py:10-15`: same.
- `stage/transaction.py:14-28`: same.

Verdict: **Option A** (drop subpackage `__init__.py` re-export lists) recommendation from round-1 is unchanged; pick one or the other. Cosmetic; mechanical; do last.

**One new observation:** `service.py` imports `from typing import TYPE_CHECKING` *twice* (lines 7 and 17). The first import is `from typing import cast` (line 7); the second is the conditional `from typing import TYPE_CHECKING` at line 17, immediately followed by `if TYPE_CHECKING:` at line 19. Both can collapse into `from typing import TYPE_CHECKING, cast` at the top. **−1 LOC, +1 readability.**

## Recommended Order for Round 3

Ranked by leverage / risk:

1. **ST-01 stager merge (−370 LOC).** Highest single win; the `StagedChanges` migration in `d745ee19` made this *easier*. Land all the supporting helper inlines (T-03, MN-already-done, SV-NEW-02) in the same PR so the merged class doesn't bake in obsolete patterns. Test risk medium-high but bounded: per-route timing keys must be preserved and `test_direct_merge.py` / `test_mutation_gate.py` cover the surface.

2. **TYP-01..05 (−79 LOC) + the `WriteChange.__init__` field-order trap.** Closest-to-types-only refactor; risk is propagation to `overlay.py` (the only constructor caller of `build_overlay_write_change`). Pair with the `_cached_content` thread-safety BLOCKER from round-1.

3. **HIGH `router.py:213` runtime TypeError invariant move to `__init__`.** Trivial; do early.

4. **MEDIUM transaction.py:131 message-from-cause + `occ_gated_failed` predicate alignment.** 5-line fix; do alongside ST-01 since it touches the same method.

5. **RT-NEW-02 (`RetryPolicy` drop, −10 LOC).** Safe; the test only imports the constant. Combine with the `mutable-default` smell at `commit_queue.py:75`.

6. **RT-NEW-01 + RT-04 (single-path module function + method, −55 LOC).** Needs cooperation with `daemon/handler/tools/{edit,write}.py` (2 call sites). Mechanical.

7. **SV-NEW-02 `_total_start` kludge cleanup (−4 LOC).** Local; do before any external caller starts depending on the underscore param.

8. **T-NEW-01 timing accumulator block restructure (−28 LOC).** Low-risk if test suite passes — purely a refactor of how 8 numbers get summed.

9. **GO-01/GO-02 gitignore_oracle (−33 LOC).** Independent file; do anytime.

10. **HSH-NEW-01 `ContentHasher` class drop (−10 LOC).** 5 call sites; mechanical.

11. **Import-convention pick (Option A: drop subpackage re-exports).** Last, mechanical, touches many files; do not interleave with logic refactors.

**Cumulative if 1-10 land: 3031 → ~2390 LOC (−640, −21%).**

## Skipped

Nothing in the requested scope was skipped. All 21 files in `sandbox/occ/` re-read in full; `d745ee19` and `5989e5d5` diffs read in full; relevant test and consumer files spot-checked (`test_mutation_gate.py`, `daemon/handler/tools/{edit,write}.py`).

---

Reviewer: gsd-code-reviewer (Claude Opus 4.7 1M)
Depth: deep (cross-file, line-cited, commit-diff-cross-checked)
Reviewed: 2026-05-15
