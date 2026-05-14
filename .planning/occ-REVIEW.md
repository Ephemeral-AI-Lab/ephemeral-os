# OCC Subsystem Code Review

Reviewed: 2026-05-15
Scope: `/Users/yifanxu/machine_learning/LoVC/EphemeralOS/backend/src/sandbox/occ/` — 21 files, 3184 LOC.
Method: every file read in full; imports tabulated; cross-file redundancy checked between `stage/direct.py` and `stage/gated.py`, between `commit_queue.py` and `stage/transaction.py`, and between `service.py` async/sync twins.

## TL;DR

- **Total LOC:** 3184 across 21 files.
- **Estimated reduction potential:** ~950 LOC (~30%) without losing capability that is currently exercised. Phase-06-deferred capability (multi-process CAS retry, disjoint batching) accounts for another ~80 LOC and the module's own docstring admits it is dead in single-process mode.
- **Top 3 highest-impact simplicity wins:**
  1. Merge `stage/direct.py` + `stage/gated.py` + `stage/_edit.py` + `stage/policy.py` (704 LOC) into a single parameterised stager (~280 LOC, -420).
  2. Strip the "Phase 06 placeholder" batching/CAS-retry plumbing from `commit_queue.py` (306 LOC → ~140, -160).
  3. Drop async twins in `service.py` (269 LOC → ~150, -120); the async path is just `run_sync_in_executor` over the sync path.
- **Top 3 import-chain issues:** No hard "4+ deep" violations exist (the deepest pattern is `sandbox.occ.X.Y`, i.e. 3 levels). The legitimate finding is **inconsistent fan-in**: every consumer reaches around the `changeset/` and `content/` packages to pull symbols directly from their submodules while both `__init__.py` files re-export the same symbols. Pick one. Detailed table below.

## Quality Findings (Criterion 1)

### HIGH — `commit_queue.py:210` swallows `BaseException` on the worker thread

```python
except BaseException as exc:
    for item in batch:
        if not item.future.cancelled():
            item.future.set_exception(exc)
```

The worker is a daemon thread; catching `BaseException` means `KeyboardInterrupt` / `SystemExit` get stuffed into per-caller futures instead of unwinding the worker. Callers that re-raise are fine, but anyone who only inspects `result.files` will silently lose the shutdown signal and the queue will continue serving from a state that the runtime considered dead. Narrow to `Exception` (or catch `BaseException` only to re-raise after fulfilling futures with a `RuntimeError` so the worker thread actually exits).

### HIGH (status taxonomy) — `stage/_edit.py:14-19` returns `ABORTED_OVERLAP` for "file does not exist"

```python
if not exists:
    return FileResult(
        path=path,
        status=FileStatus.ABORTED_OVERLAP,
        message="file does not exist",
    )
```

`ABORTED_OVERLAP` is documented elsewhere as a content/anchor conflict. A missing file isn't an overlap — it's either `ABORTED_VERSION` (the file was deleted between snapshot and merge) or `REJECTED` (the user asked to edit a file that never existed). Both `direct.py:_apply_edit` and `gated.py:_apply_edit` propagate the wrong status verbatim. Fix: add a distinct `MISSING` status or reuse `ABORTED_VERSION` for the gated path and `REJECTED` for the direct path.

### MEDIUM — `stage/transaction.py:326-339` misleading error after `os.path.getsize` failure

```python
try:
    file_size = os.path.getsize(content_path)
except OSError:
    file_size = 0
if file_size >= _SMALL_FILE_BYTES_THRESHOLD:
    shutil.copyfile(content_path, source)
elif cached_bytes is not None:
    if file_size != len(cached_bytes):
        raise RuntimeError(
            "stage_write_from_path cached_bytes length "
            f"{len(cached_bytes)} disagrees with file size "
            f"{file_size} at {content_path!r}"
        )
```

If `getsize` raises (`ENOENT`, `EACCES`), the code silently coerces to 0, then the `len(cached_bytes) != 0` branch raises a "disagrees with file size 0" error that hides the real cause. Two failure modes get the same misleading message. Fix: don't swallow `OSError`; re-raise with the original errno, or take the small-file branch unconditionally when `cached_bytes` is provided.

### MEDIUM — `router.py:254-260` runtime `TypeError` for a construction-time invariant

```python
if snapshot is not None:
    if not isinstance(oracle, SnapshotGitignoreMatcher):
        raise TypeError(
            "snapshot-aware OCC routing requires "
            "SnapshotGitignoreMatcher.is_ignored_in_snapshot"
        )
```

Every prepared changeset hits this check inside `_route_change_timed`. The matcher is set in `Router.__init__`; if it's wrong it's wrong forever. Move the `isinstance` check into `Router.__init__` (or change the constructor parameter type to `SnapshotGitignoreMatcher` outright) — eliminates a per-change branch and turns a runtime error into a wiring error.

### MEDIUM — `changeset/types.py:51-64` mutates a `frozen=True` dataclass

```python
@dataclass(frozen=True)
class DiskWritePayload:
    ...
    def read_bytes(self) -> bytes:
        cached = self._cached_content
        if cached is not None:
            return cached
        content = Path(self.path).read_bytes()
        object.__setattr__(self, "_cached_content", content)
        return content
```

Two callers concurrently reading the same `DiskWritePayload` will both perform the disk read and one will silently win the cache. The dataclass advertises `frozen=True`, so callers are entitled to assume immutability — this can break `hash()` and equality if the dataclass is ever placed in a set/dict (the `compare=False, repr=False` field hides this somewhat, but `__hash__` still uses the comparison fields, so it survives by accident). Recommendation: drop `frozen=True` and document mutation, or drop the cache and let callers cache externally. See also the simplicity finding below — both payload classes can be collapsed.

### LOW — `commit_queue.py:75 / RetryPolicy` is dead defensively-named code

The module docstring (lines 22-36) admits: *"In the current single-process architecture … the retry loop is a defensive bound — every commit succeeds on the first attempt."* Carrying 80+ LOC of CAS-retry plumbing whose only test path is a synthetic conflict is speculative. Quality-wise it's correct; simplicity-wise it's the biggest win in this file (see Criterion 2).

### LOW — `service.py:165 / _result_timings_with_resume` mutates a returned dict the caller already wrote into

`result.timings` is consumed via `dict(result.timings)` then `pop`. The original `result` is left intact since `ChangesetResult` is frozen and timings is rebuilt — fine. But the `_RESULT_READY_AT = TimingKey.SERIAL_RESULT_READY_AT` underscore-prefixed sentinel leaks back to callers if `_wrap_commit_result` is bypassed (e.g. `apply_sync` callers reading the raw queue result). No exploitable bug, but the contract that internal-only timings are stripped lives in one specific helper, not the type itself. Either move the strip into `ChangesetResult` or use a separate field.

### INFO — no security issues found

- No `eval`, `exec`, `shell=True`, `pickle.loads`, `yaml.unsafe_load`, or string interpolation into shell.
- Path-traversal is guarded by `normalize_layer_path` in `router.py:151` (the rejection branch is correct).
- `uuid4().hex` is fine for staging dir entropy; not a security boundary.
- No hardcoded secrets.

## Simplicity Findings (Criterion 2)

### File: `stage/transaction.py` (423 LOC)

**T-01: `_must_skip_publish` + `_mark_unpublished` bundle two unrelated policies.**
- Lines 353-403 implement two conditions (`atomic-and-any-failure` and `overlay-capture-and-any-gated-failure`) through a boolean helper, then `_mark_unpublished` re-branches on `prepared.atomic` to pick a message.
- The merge of these two policies into one boolean obscures both.
- **Mechanism:** inline both into `revalidate_and_publish` as two clearly separated guards; drop helpers. **Current ~50 LOC → ~25 LOC.** (-25)

**T-02: `_LayerChangeStager` Protocol is single-implementer.**
- Lines 220-240: 21-line `Protocol` whose only implementation is `_FileSystemLayerChangeStager` defined right below it. No alternate implementation lives in the codebase. The `Protocol` is used only as a parameter type for one private method (`_validate_group` does not even reference it; the `stager` argument is typed `_LayerChangeStager` but only `_FileSystemLayerChangeStager` is ever passed).
- **Mechanism:** drop the Protocol; type the parameter as the concrete class. **-21 LOC.**

**T-03: per-route timing accumulation block (lines 79-121) is 43 lines of accumulator boilerplate.**
- Eight float accumulators + two int counters are summed inside the `for group in prepared.path_groups` loop.
- **Mechanism:** push accumulation into `validations: list[(FileResult, LayerDelta|None, RouteDecision)]` and compute totals with two `sum(...)` comprehensions plus a single `Counter`. **Current ~43 LOC → ~15.** (-28)

**T-04: dual-mode `_finish_timings` is two functions in one.**
- The optional `transaction=None` branch is dead — every call site passes a transaction. **-3 LOC by dropping the optional.**

**File-level estimate:** 423 → **~325 LOC** (-98).

### File: `changeset/types.py` (330 LOC)

**TYP-01: `EagerWritePayload` / `DiskWritePayload` polymorphism for one bool of behaviour.**
- Lines 28-75: two `frozen=True` dataclasses, each implementing the same three-method interface (`read_bytes`, `content_path`, `precomputed_hash`). `WriteChange` (lines 78-128) proxies all three through properties.
- The only behavioural difference is "have bytes" vs "have a disk path."
- **Mechanism:** collapse into one optional-field dataclass:
  ```python
  @dataclass(frozen=True)
  class WritePayload:
      content: bytes | None = None
      disk_path: str | None = None
      precomputed_hash: str | None = None
  ```
  with a free function `_read_bytes(payload) -> bytes` that reads from disk on demand (no in-place cache; caller stashes bytes if it needs to reuse them). The lazy `_cached_content` hack at lines 51-64 disappears.
- **Current ~50 LOC payload classes + ~30 LOC proxy properties → ~25 LOC.** (-55)

**TYP-02: `WriteChange.__init__` (lines 91-102) hand-rolls dataclass init.**
- The hand-rolled `__init__` uses `object.__setattr__` four times because `Change` uses `frozen=True` and `WriteChange` needs to coerce `path = str(path)`. But `Change.__post_init__` already does that coercion (line 24).
- **Mechanism:** drop `init=False` from the decorator, drop the manual `__init__`, rely on the inherited `__post_init__`. **-12 LOC.**

**TYP-03: `with_base_hash` exists twice (`WriteChange` 122-128, `DeleteChange` 161-162).**
- The `WriteChange` version reconstructs everything by name; the `DeleteChange` version uses `replace(self, ...)`. Inconsistency.
- **Mechanism:** both can be `replace(self, base_hash=base_hash)`. **-6 LOC** plus consistency.

**TYP-04: `build_overlay_write_change` (lines 268-299) checks the same precondition twice.**
- The `if final_content is None and content_path is None: raise ValueError` at line 282 already guards entry. The duplicate `if final_content is None: raise ValueError` inside the `else` branch at line 291 is unreachable.
- **Mechanism:** drop the dead branch. **-3 LOC.**

**TYP-05: `_normalize_kept_children` (lines 193-200) is called from exactly one place.**
- Inline into `OpaqueDirChange.__post_init__`. **-3 LOC** plus locality.

**File-level estimate:** 330 → **~250 LOC** (-80).

### File: `stage/gated.py` (319 LOC) + `stage/direct.py` (303 LOC) + `stage/_edit.py` (45 LOC) + `stage/policy.py` (37 LOC) = 704 LOC

**ST-01 (HIGHEST IMPACT REDUCTION IN THE PACKAGE): merge `DirectStager` and `GatedStager`.**

The two classes are parallel-structured to a striking degree:

| Concern | `DirectStager` | `GatedStager` |
|---|---|---|
| `_DirectStageState` / `_GatedStageState` | dataclass (lines 36-79) | dataclass (lines 39-86) |
| Handler dict | `_handlers` (lines 87-93) | `_handlers` (lines 100-105) |
| `stage_group` try/except wrapper | 102-118 | 107-130 |
| `_stage_group` read + per-change loop + delta build | 120-223 | 132-191 |
| `_apply_change` dispatch | 225-239 | 193-208 |
| Per-kind handlers | 241-300 | 210-283 |

Real differences are only:
1. Gated reads `current_hash` between changes and rejects on `current_hash != expected_hash` (one branch in `_apply_write` / `_apply_delete`).
2. Direct tracks `final_kind: Literal["write","delete","symlink","opaque_dir"]`; gated uses `exists: bool` + `final_special_change: LayerChange | None`. **Same state, different encoding.**
3. Direct supports `SymlinkChange`; gated does not.

**Mechanism:** one `Stager` with a `route: RouteDecision` field that selects the hash-check predicate; one unified `_StageState` with the literal kind; gated `SymlinkChange` becomes a `REJECTED` handler returning a `FileResult`. The handler dict shrinks because every kind shares one body. `apply_edit_content` already lives in `_edit.py` and is shared correctly — keep that.

**Current 704 LOC → ~280 LOC. Mechanism: merge + collapse two parallel state dataclasses.** (-420)

**ST-02: `policy.py:with_timings` (lines 31-37) is called only from `direct.py:144` and `gated.py:165`.**
- After ST-01 it has one caller. **Mechanism:** inline. **-7 LOC.**

**ST-03: `policy.py:MergePolicy` Protocol (lines 18-28) has two implementers today and one after ST-01.**
- Becomes single-implementer. **Mechanism:** drop. **-11 LOC.**

**ST-04: `_DirectStageState.set_special` + `set_write` + `set_delete` are write-only mutators called from one site each.**
- Lines 58-79; each is < 5 LOC; the only consumer is the matching `_apply_*` handler.
- **Mechanism:** inline into handlers. **-15 LOC** (also removes the "reset every field" defensive triplet that hides intent).

**ST-05: `_delta_for_final_state` (gated.py:290-316) is 27 LOC of branch-on-flags.**
- Three outcomes: write/delete/no-op. Each is one `LayerDelta(...)` call. After ST-01 the four-flag input collapses to one state object.
- **Mechanism:** fold into the merged `_stage_group`. **-20 LOC.**

**File-level estimate (post ST-01 through ST-05): 704 → ~250 LOC** (-454, ~64% reduction).

### File: `commit_queue.py` (306 LOC)

**CQ-01: strip `_disjoint_batches` (lines 216-235), `_combine_prepared` (238-247), `_merge_timings` (298-303), `RetryPolicy` (40-48), the batch fill loop in `_run` (143-162), and `_cas_exhaustion_result` (254-295).**
- The module's own docstring at lines 22-36 admits the retry loop and batching are dead code in single-process mode. They exist for "multi-process Phase 06+ topologies inherit a named, testable limit." That is speculative.
- **Mechanism:** drop until Phase 06 lands; the git history preserves it. Keep a single-item `_run` loop that calls `revalidate_and_publish` directly. **-160 LOC.**

**CQ-02: `_StopItem` + `_QueueItem` union (lines 57-63) for a one-bit "drain done" signal.**
- A `None` sentinel or a separate `threading.Event` is one line.
- **Mechanism:** swap to `Event`. **-8 LOC.**

**CQ-03: `submit` / `apply` / `apply_sync` / `_run` triplet.**
- After CQ-01 the queue is a thin wrapper around the transaction. If we accept removing the background worker entirely and serialising on a `threading.Lock` held inside `apply_sync`, the whole class disappears and `OccService` calls `self._transaction.revalidate_and_publish(prepared)` under a lock.
- **Mechanism (aggressive):** delete file; move single lock into `service.py`. **-300 LOC.**
- **Mechanism (conservative, recommended):** keep the queue as a serialisation primitive but drop batching. **-160 LOC** total.

**File-level estimate (conservative): 306 → ~145 LOC** (-161).

### File: `stage/transaction.py` (423 LOC, separately above)

Already accounted for: -98 → 325.

### File: `router.py` (285 LOC)

**RT-01: `_route_change` (lines 132-142) wraps `_route_change_timed` only to discard one tuple field.**
- Called once, from `_group_by_route` line 122. The timing field is then unused in batch routing (line 130 only takes 4-tuples). The wrapper exists to keep the call site tidy.
- **Mechanism:** inline `_route_change_timed` at the call site and discard the unused element. **-11 LOC.**

**RT-02: `prepare_single_path_changeset` module function (lines 264-278) duplicates `Router.prepare_single_path_sync`.**
- The module function constructs a `Router` and calls the method. No new behaviour.
- **Mechanism:** delete; callers can construct `Router` directly (one line). **-15 LOC.**

**RT-03: `_attach_chained_base_hashes` + `_next_base_hash` + `_attach_base_hash` (lines 210-245) — three helpers for one local pipeline.**
- Each helper is < 15 LOC; together they build a 15-line for-loop. The indirection makes it hard to see that the chain only matters when `needs_base_hash` is true.
- **Mechanism:** inline the trio into `_prepare_group`. **-15 LOC.**

**RT-04: `prepare_single_path_sync` (lines 71-110) is a parallel pipeline of `prepare_sync` for the `len(changes) == 1` case.**
- Same routing, same base-hash attachment, different timing keys.
- **Mechanism:** drop the method; let `prepare_sync` handle the single-change case (it already does, since `_group_by_route` works on a single change). The differing `PREPARE_SINGLE_PATH_FAST` timing key can be emitted by callers if needed. **-40 LOC** (the method body) plus simpler routing surface.

**File-level estimate:** 285 → **~205 LOC** (-80).

### File: `service.py` (269 LOC)

**SV-01: async/sync twin methods.**
- `apply_changeset` / `apply_changeset_sync` (61-69 / 100-113)
- `commit_prepared` / `commit_prepared_sync` (71-98 / 115-134)
- `_maintenance_after_publish` / `_maintenance_after_publish_sync` (136-153)
- `prepare_changeset` / `prepare_changeset_sync` (190-243)
- The async paths are uniformly `await run_sync_in_executor(self.foo_sync, ...)`. Callers who actually need async can call `asyncio.to_thread(svc.foo_sync, ...)`.
- **Mechanism:** drop async twins; keep `*_sync` and one `_wrap_commit_result`. **Current ~120 LOC async surface → ~10 LOC** (a single `apply_async` helper if anyone insists). **-110 LOC.**

**SV-02: `_total_start` parameter passing via leading underscore is API smell.**
- `commit_prepared(..., _total_start=None)` and twin. The underscore advertises "internal" but the parameter is exposed in the public method.
- **Mechanism:** add an internal `_apply_changeset_inner(prepared, total_start)` helper; the public methods delegate without the kludge. **-4 LOC, +clarity.**

**SV-03: `_manifest_lag` (251-255) — one-line helper.**
- Inline. **-5 LOC.**

**SV-04: `_result_timings_with_resume` (258-263) — one-call helper.**
- Inline. **-7 LOC.**

**File-level estimate:** 269 → **~145 LOC** (-124).

### File: `content/gitignore_oracle.py` (235 LOC)

**GO-01: `_load_pathspec` (lines 26-31) lazy-import dance.**
- The pathspec import happens at first `PathspecGitignoreOracle()` construction. The cost saved is ~5 ms once per process. Comment claims "so importing the runtime module stays cheap" — there's no caller-imports-but-never-instantiates path in the codebase.
- **Mechanism:** plain `import pathspec` at module top. **-8 LOC.**

**GO-02: `_is_dir_excluded` cache loop (lines 115-130) builds an ancestor walk that `_evaluate_file` (108-113) already does.**
- Each path evaluation walks ancestors twice: once for the directory seal in `_evaluate_file` (calling `_is_dir_excluded(accum)` for each ancestor), and `_is_dir_excluded` itself re-walks the same ancestors inside `_is_dir_excluded` to refresh its cache.
- This is a correctness-adjacent concern (it terminates on the cached short-circuit) but the double walk is intent-obscuring.
- **Mechanism:** single-pass evaluator that accumulates ancestor verdicts in one walk and consults `_match_with_inheritance` once per ancestor. **~25 LOC saved + clarity.**

**GO-03: `SnapshotGitignoreOracle` cache unbounded.**
- `self._oracles: dict[int, PathspecGitignoreOracle]` (line 187) grows one entry per manifest version. Long-running processes accumulate one entry per commit. Not currently a leak in practice (cycle of squashes keeps version count low) but worth a `cache_size` limit or LRU eviction.
- **Mechanism:** wrap in `functools.lru_cache`-style bounded dict. **+5 LOC** (this is a quality finding, not a reduction).

**File-level estimate:** 235 → **~200 LOC** (-35).

### File: `maintenance.py` (127 LOC)

**MN-01: `_merge_auto_squash_timings` (102-120) only called from the recheck branch.**
- The function handles "first or second is empty" cases that never occur after the early-return guards at the call site (we only call it when both branches ran).
- **Mechanism:** inline at the recheck branch with the three lines that actually matter. **-14 LOC.**

**MN-02: `_CoalescedSquashState` (lines 29-34) wraps two locks and a bool.**
- Used only inside `AutoSquashMaintenancePolicy`. The dataclass adds no behaviour; instance attributes do the same job.
- **Mechanism:** drop the dataclass; promote the three fields to the policy class. **-7 LOC.**

**MN-03: `SquashPort` Protocol single-implementer in repo.**
- Possibly intentional for testing seam. Keep if there are mock implementations in tests; otherwise drop.

**File-level estimate:** 127 → **~100 LOC** (-27).

### Small files (collapsed)

- `__init__.py` (29 LOC) — fine.
- `client.py` (61 LOC) — `_require_binding` could be inlined into the two callers; `workspace_ref=""` default is a sentinel for "use stored ref" — clearer to make it `None` everywhere. **-5 LOC.**
- `overlay.py` (87 LOC) — `_kept_children_for` (lines 68-82) computes once per `opaque_dir` change; passes the full `path_changes` list every time. For a changeset with N opaque dirs this is O(N²). Not a bug, but a `defaultdict(set)` prepass would be linear. (Performance is out of v1 scope.) Otherwise clean.
- `ports.py` (90 LOC) — fine; pure type contracts.
- `timing_keys.py` (64 LOC) — large enum but every key is used in `grep`. Keep.
- `stage/__init__.py` (15 LOC) — fine.
- `stage/_edit.py` (45 LOC) — see ST-01 (rolls into merged stager) and the HIGH quality finding on `ABORTED_OVERLAP`.
- `stage/policy.py` (37 LOC) — folded into ST-01 merger.
- `changeset/__init__.py` (39 LOC) — fine, but see the import-chain inconsistency finding.
- `changeset/prepared.py` (58 LOC) — fine; small frozen dataclasses.
- `content/__init__.py` (23 LOC) — fine.
- `content/hashing.py` (39 LOC) — `infer_manifest_base_hash` (lines 23-33) creates a one-shot `ContentHasher()`. Class is two methods, neither holds state. **Mechanism:** drop the class; export `hash_bytes` and `hash_current` as module-level functions. **-10 LOC, -1 indirection level.**

## Import-Chain Findings (Criterion 3)

**No 4+ deep violations.** Deepest import path is `sandbox.occ.changeset.types` / `sandbox.occ.content.gitignore_oracle` / `sandbox.occ.stage.transaction` — all exactly 3 levels under `sandbox`, within budget.

**Legitimate finding: inconsistent fan-in.** Both `occ/__init__.py` and the `changeset/__init__.py`, `content/__init__.py`, `stage/__init__.py` re-export the same symbols, yet consumers import past them straight to submodules:

| File | Direct submodule import | Re-export available at |
|---|---|---|
| `commit_queue.py:13` | `from sandbox.occ.changeset.prepared import PreparedChangeset, RouteDecision` | `sandbox.occ.changeset` |
| `commit_queue.py:14` | `from sandbox.occ.changeset.types import ChangesetResult, FileResult, FileStatus` | `sandbox.occ.changeset` |
| `commit_queue.py:15` | `from sandbox.occ.stage.transaction import CommitTransaction` | `sandbox.occ.stage` and `sandbox.occ` |
| `router.py:10-15` | `from sandbox.occ.changeset.prepared import (...)` | `sandbox.occ.changeset` |
| `router.py:16-20` | `from sandbox.occ.changeset.types import (...)` | `sandbox.occ.changeset` |
| `router.py:21-24` | `from sandbox.occ.content.gitignore_oracle import (...)` | `sandbox.occ.content` |
| `router.py:25` | `from sandbox.occ.content.hashing import ContentHasher` | `sandbox.occ.content` |
| `service.py:10` | `from sandbox.occ.changeset.prepared import (...)` | `sandbox.occ.changeset` |
| `service.py:11` | `from sandbox.occ.changeset.types import (...)` | `sandbox.occ.changeset` |
| `service.py:12` | `from sandbox.occ.stage.transaction import CommitTransaction` | `sandbox.occ.stage` |
| `service.py:13-14` | `content.gitignore_oracle`, `content.hashing` | `sandbox.occ.content` |
| `stage/transaction.py:15-19` | `from sandbox.occ.changeset.prepared import (...)` | `sandbox.occ.changeset` |
| `stage/transaction.py:20-24` | `from sandbox.occ.changeset.types import (...)` | `sandbox.occ.changeset` |
| `stage/transaction.py:25` | `from sandbox.occ.content.hashing import ContentHasher` | `sandbox.occ.content` |

**Proposal:** pick one convention.
- **Option A (recommended):** delete the subpackage `__init__.py` re-export lists (`changeset/__init__.py`, `content/__init__.py`, `stage/__init__.py`) and let every consumer import from the actual submodule. Eliminates two-line "where does this symbol live" lookups.
- **Option B:** make every consumer import from `sandbox.occ.changeset` / `sandbox.occ.content` / `sandbox.occ.stage` aggregates. Shorter call sites; pays one `__init__.py` evaluation per submodule.

Either way, the *current* state — both paths live, inconsistently — adds search cost without payoff.

**Re-export chain at `occ/__init__.py`** (lines 5-15) imports from `sandbox.occ.changeset` (the subpackage re-export), `sandbox.occ.client`, `sandbox.occ.commit_queue`, `sandbox.occ.router`, `sandbox.occ.service`, and `sandbox.occ.stage` (subpackage re-export). The `sandbox.occ.changeset` and `sandbox.occ.stage` lines pull through one re-export layer to reach symbols defined two modules deeper. This is at the limit but legal.

**`overlay.py:9` and `:13`** import twice from the same module `sandbox.occ.changeset.types`. Merge.

## Recommended Refactor Order

Ranked by leverage / LOC saved / risk. Each entry includes a rough LOC delta and the dependencies that must land first.

1. **Strip `commit_queue.py` Phase-06 placeholder code (CQ-01).**
   - **-160 LOC.** Lowest risk — the module's own docstring says it's defensive bound only.
   - Tests: any test that asserts `RetryPolicy` or `_disjoint_batches` directly will need updating; the user-visible behaviour doesn't change because single-process never trips the retry.

2. **Merge `stage/direct.py` + `stage/gated.py` + `stage/policy.py` (ST-01..05).**
   - **-454 LOC.** Highest single saving in the package. Medium risk — these have the most test coverage in the OCC suite and the per-route timing keys must be preserved.
   - Land *after* CQ-01 so `commit_queue.py` (which imports `CommitTransaction`) isn't churning.

3. **Drop async twins in `service.py` (SV-01).**
   - **-110 LOC.** Medium risk — every async caller in `sandbox/api/` and `sandbox/host/` needs `await asyncio.to_thread(svc.apply_changeset_sync, ...)` instead of `await svc.apply_changeset(...)`. Mechanical update.

4. **Collapse `changeset/types.py` payload polymorphism (TYP-01..05).**
   - **-80 LOC.** Low risk if `WriteChange`'s public properties stay stable. Mostly internal.

5. **Inline single-use helpers in `router.py`, `transaction.py`, `maintenance.py`, `service.py`, `content/hashing.py`.**
   - RT-01..04: -80 LOC. T-01..04: -98 LOC. MN-01..02: -21 LOC. SV-02..04: -16 LOC. Hashing class: -10 LOC.
   - **-225 LOC total.** Lowest risk; each change touches one or two callers.

6. **Fix the quality findings:**
   - `commit_queue.py:210` narrow exception — HIGH, do alongside CQ-01.
   - `stage/_edit.py:14-19` status taxonomy — HIGH, do alongside ST-01.
   - `stage/transaction.py:326-339` `getsize` swallow — MEDIUM, isolated 5-line fix.
   - `router.py:254-260` runtime invariant — MEDIUM, alongside RT-01.
   - `changeset/types.py:51-64` frozen mutation — MEDIUM, alongside TYP-01.

7. **Pick one import convention (Criterion 3).**
   - Either drop subpackage `__init__.py` re-exports or migrate all consumers to use them. Mechanical; touches every file. Do last so it doesn't interleave with logic refactors.

**Cumulative LOC delta if 1-5 land:** 3184 → ~2155 LOC (-1029, -32%). Even the more conservative subset (1, 3, 5) saves ~500 LOC.

## Skipped / Not Reviewed

Nothing in the requested scope was skipped. All 21 files in `sandbox/occ/` were read in full.

Files referenced but outside scope:
- `sandbox.layer_stack.*` — consumed via ports only; not reviewed.
- `sandbox.daemon.async_bridge` — consumed via `run_sync_in_executor`; not reviewed.
- `sandbox.timing` — consumed via `monotonic_now`; not reviewed.

---

Reviewer: gsd-code-reviewer (Claude Opus 4.7 1M)
Depth: deep (cross-file, line-cited)
Reviewed: 2026-05-15
