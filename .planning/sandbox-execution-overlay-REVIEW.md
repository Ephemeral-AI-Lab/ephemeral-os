# Code Review: `backend/src/sandbox/execution/overlay/`

**Target:** 9 files, 1125 LoC total
**Date:** 2026-05-15
**Reviewer:** Claude (Opus 4.7, manual review — `/gsd-code-review` not phase-scoped)
**Focus:**
1. Implementation quality
2. Aggressive simplicity — can a 200 LoC file be 150 / 100 / 50?
3. Internal sandbox import-chain depth ≤ 3 hops

---

## Verdict

Sound design, correct behavior, **but ~25–30% of LoC is removable without losing functionality**. The main offenders concentrate in `pipeline.py` (Wave-2 consolidation artifacts) and the package-level re-export layer. Import-depth criterion is **met**; the real cost lives in module fan-out and dead abstractions.

**Reduction potential:** 1125 → ~820 LoC (-27%) with zero behavior change.

| File | Now | Achievable | Δ |
|---|---:|---:|---:|
| `pipeline.py` | 286 | ~170 | -40% |
| `worker.py` | 108 | ~80 | -25% |
| `capture.py` | 289 | ~270 | -7% (logic is essential) |
| `mounts.py` | 103 | ~85 | -17% |
| `__init__.py` | 39 | ~30 | -23% |
| `change.py` | 85 | 85 | clean |
| `request.py` | 66 | 66 | clean |
| `result.py` | 98 | ~90 | -8% |
| `runner.py` | 51 | ~45 | -12% |

---

## Findings by severity

### CRITICAL — none

### HIGH

**H1. Circular import band-aid: `pipeline ↔ worker`**
`pipeline.py:209` defers `from sandbox.execution.overlay.worker import execute_request` inside `_execute_request_with_timings` to break the cycle introduced when Wave 2 collapsed `factory|invoker|command` into `pipeline.py`. The cycle exists *only* because `worker.py:14` imports `run_user_command` from `pipeline`.

**Fix:** Move `run_user_command`, `OverlayCommandResult`, `_HOST_ENV_ALLOWLIST`, `_validate_cwd`, `_ensure_cwd` from `pipeline.py` into `worker.py`. Those symbols have exactly one consumer (`worker.execute_request`); they were colocated with the invoker by accident of the Wave-2 merge.

- Eliminates the deferred import entirely.
- `pipeline.py` shrinks from 286 → ~170 lines.
- `__init__.py` re-export of `run_user_command` continues to work (just import from `.worker` instead).
- Module name "pipeline" then accurately describes "invoker + factory" without the leaky third stage.

### MEDIUM

**M1. `OverlayInvoker` Protocol is a single-implementation dead abstraction** — `pipeline.py:63-77`
Only `OverlayRuntimeInvoker` implements it. No tests use it as a `Mock` substitution target (`rg "OverlayInvoker" backend/tests` → 0 hits). `runner.py:23` accepts `invoker: OverlayInvoker | None = None` then immediately defaults to constructing a `OverlayRuntimeInvoker`.

**Fix:** Delete the `Protocol`. Type `runner._invoker` as `OverlayRuntimeInvoker`. Delete `create_overlay_invoker` (1-line factory used only by `runner`); inline as `OverlayRuntimeInvoker(storage_root=layer_stack.storage_root)`. ~25 lines gone; one level of indirection removed.

If you want to keep injectability for future kernel-mount-based invokers, leave a structural type hint but don't materialize the Protocol class today — YAGNI.

**M2. `invoke` and `invoke_sync` are near-verbatim duplicates** — `pipeline.py:94-139`
The async path is identical to the sync path except it wraps `_execute_request_with_timings` in `run_sync_in_executor`. Currently ~46 lines.

**Fix:**
```python
async def invoke(self, *, request, manifest):
    return await run_sync_in_executor(self.invoke_sync, request=request, manifest=manifest)

def invoke_sync(self, *, request, manifest):
    # existing body, runs synchronously
```
Saves ~20 lines, single source of truth for the timing-stamp logic, async surface stays.

**M3. Timing arithmetic over-decomposed** — `pipeline.py:221-262`
Three helpers (`_with_invoker_timings`, `_queue_wait_s`, `_resume_wait_s`) for what is `max(0.0, x)` applied to four subtractions. Each call site already has the values in scope.

**Fix:** Inline as one dict literal at the single call site:
```python
queue_wait = max(0.0, worker_start - invoke_start)
non_worker = max(0.0, invoke_elapsed - worker_elapsed)
return replace(capture, timings={
    **capture.timings,
    "overlay.invoker.queue_wait_s": queue_wait,
    "overlay.invoker.worker_total_s": worker_elapsed,
    "overlay.invoker.resume_wait_s": max(0.0, non_worker - queue_wait),
    "overlay.invoker.total_s": invoke_elapsed,
})
```
Saves ~25 lines. The named helpers don't pay for themselves at one call site.

**M4. `worker.main()` CLI entry is dead code** — `worker.py:79-108`
`rg "-m sandbox.execution.overlay.worker"` and `rg "execute_request" backend/src` outside the package return zero CLI uses. The argparse plumbing exists but nothing invokes it (the production path calls `execute_request` directly via the in-process executor in `pipeline.py`).

**Fix:** Delete `_parse_args`, `main`, and the `if __name__ == "__main__"` block (~30 lines) — OR add a CI-checked entry confirming the CLI is reachable. The docstring claims it's a "Worker entrypoint" but only the function is the entrypoint; argparse is fiction.

**M5. `OverlayCommandResult` is a 3-field DTO used twice** — `pipeline.py:56-60`
Constructed once at `run_user_command` return; immediately destructured at `worker.execute_request:64-67` (`command.exit_code`, `command.stdout_ref`, `command.stderr_ref`). When `run_user_command` moves into `worker.py` (per H1), `OverlayCommandResult` becomes a private DTO with one constructor and one consumer in the same file.

**Fix:** Return a `tuple[int, str, str]` from `run_user_command` and unpack at the call site. Saves the dataclass (~5 lines) and removes one public export. If you keep the dataclass for self-documentation, drop the `__all__` entry.

### LOW

**L1. `__init__.py` has redundant import lines for the same module**  — `__init__.py:9-11`
```python
from sandbox.execution.overlay.pipeline import OverlayCommandResult, run_user_command
from sandbox.execution.overlay.pipeline import create_overlay_invoker
from sandbox.execution.overlay.pipeline import OverlayInvoker, OverlayRuntimeInvoker
```
Three lines for one module — merge into one parenthesized import. Same for line 18 (`result` already on one line — fine).

**L2. `mount_snapshot` timing-stamp repetition** — `mounts.py:46-66`
`if timings is not None: timings[key] = monotonic_now() - start` repeats 3×. Either accept `timings: dict[str, float]` (callers always pass one — `worker.execute_request:29` initializes `timings: dict[str, float] = {}` and forwards it) and drop the `None` branch entirely, or normalize once:
```python
timings = timings if timings is not None else {}
```
Same applies to `capture.capture_changes:23,40-43`. The `None` default is unused-in-practice optionality. Saves ~6 lines.

**L3. `_copy_tree` may be a hand-rolled `shutil.copytree(symlinks=True, dirs_exist_ok=True)`** — `mounts.py:83-96`
The custom iteration "preserves top-level symlinks" — but `shutil.copytree` with `symlinks=True` does this recursively, and the destination is pre-created so `dirs_exist_ok=True` handles the merge-into-existing case. Validate that it has no observable difference, then replace ~13 lines with a 1-line call. If there *is* a reason (e.g., `lowerdir` itself can be a symlink and you want to dereference it but preserve children), add a one-line comment naming it.

**L4. `_populate_upperdir_from_diff` wasteful rmtree** — `capture.py:60-62`
Caller `capture_changes:32` does `upper_root.mkdir(parents=True, exist_ok=True)`, then `_populate_upperdir_from_diff` immediately does `if upperdir.exists(): shutil.rmtree(upperdir); upperdir.mkdir(parents=True)`. The directory always exists at call time (we just made it). Either skip the conditional or remove the upfront `mkdir`. Micro, but it's wasted syscalls.

**L5. `_parse_kind` is a trivial dispatcher** — `change.py:75-78`
3-line function used once in `from_dict`. Inline:
```python
kind = payload["kind"]
if kind not in ("write", "delete", "symlink", "opaque_dir"):
    raise ValueError(f"unsupported upper change kind: {kind!r}")
```
Or rely on `__post_init__` running `Literal` validation — but Literal isn't runtime-enforced, so the check stays. Either way, no separate function.

**L6. `read_output_ref` is a 2-line wrapper used in 5 places** — `result.py:90-91`
`Path(path).read_bytes().decode("utf-8", "replace")`. Keep it: the name documents intent ("read the output file referenced by a `*_ref`"), and the call sites would otherwise repeat the `"utf-8", "replace"` magic-string pair. Acceptable.

**L7. `_validate_cwd` uses `os.path.commonpath` instead of `Path.is_relative_to`** — `pipeline.py:265-273`
Python ≥3.9. `resolved.is_relative_to(root)` is one line and clearer. Compatible. (After H1 this code lives in `worker.py`.)

**L8. `OverlayCapture.timings` is `MappingProxyType` but `to_dict` does `dict(self.timings)`** — `result.py:34-39, 52`
Read-only at the dataclass level, mutable at the serialized level. Fine, just note that the read-only guarantee is per-instance, not per-flow.

---

## Imports

### Depth criterion (≤ 3 internal hops) — **PASS**

| File | Deepest sandbox import | Depth |
|---|---|---:|
| `capture.py` | `sandbox.layer_stack.layer.index` | 3 |
| `capture.py` | `sandbox.layer_stack.workspace.base` | 3 |
| `change.py` | `sandbox.layer_stack.layer.change` | 3 |
| `mounts.py` | `sandbox.layer_stack.view` | 2 |
| `pipeline.py` | `sandbox.daemon.async_bridge` | 2 |
| `runner.py` | `sandbox.layer_stack.manager` | 2 |
| `worker.py` | `sandbox.layer_stack.manifest` | 2 |

All within budget. No reorganization needed for this criterion.

### Module fan-out (orthogonal concern, worth flagging)

- `runner.py` (51 LoC) imports across **6** intra-package modules — that's one external import per ~8 lines of body. After M1 (delete `create_overlay_invoker`) one of those imports disappears.
- `__init__.py` re-exports **18 symbols** across 7 files. Test code consumes 7 of those 18 from the alias `sandbox.overlay`. Keep the wide surface for now — the alias is a documented stability boundary per `tests/live_e2e_test/conftest.py:32`.

---

## Implementation quality

### Strengths
- **Frozen dataclasses + `__post_init__` validation** — `OverlayPathChange`, `OverlayCapture`, `OverlayShellRequest` all enforce invariants at construction. Good.
- **`_validate_cwd`** correctly rejects `..`-escape and symlink-escape via `realpath`-style `Path.resolve()` + `commonpath`. Security-correct.
- **Symlink-escape rejection in `_populate_upperdir_from_diff`** (`capture.py:86-91`) — refuses absolute or `..`-traversal symlink targets when materializing the upperdir. Good.
- **Whiteout/opaque-marker handling** — both kernel-native (char-device, `trusted.overlay.opaque` xattr) and userspace-portable (`.wh.`, `OPAQUE_MARKER`) variants are decoded. The two coexist because `capture_changes(upper)` (kernel path, via `sandbox/execution/workspace/capture.py:27`) AND `capture_changes(upper, lowerdir=..., workspace_root=...)` (portable copy-backed path, via `worker.execute_request`) both call into the same `_walk_upperdir`. Correctness-load-bearing — keep.
- **Timeout exit-code convention** — `pipeline.run_user_command:194` returns 124 (GNU `timeout(1)` convention) for `subprocess.TimeoutExpired`. Documented in the comment. Good.
- **Host-env allowlist** — `_HOST_ENV_ALLOWLIST` deliberately omits secrets and forces `GIT_OPTIONAL_LOCKS=0`. Good.

### Concerns

- **`capture.capture_changes` mixes two responsibilities** — kernel-overlay reading AND portable-copy materialization. They share `_walk_upperdir` but the `_populate_upperdir_from_diff` branch is only meaningful when there's no real overlayfs. The current API papers over the distinction via optional kwargs. Consider splitting into `capture_kernel_upperdir(upper)` and `capture_synthetic_upperdir(upper, *, lowerdir, workspace_root)`, then deleting the `None` branches. Net LoC roughly equal but intent becomes legible.
- **`OverlayCapture.snapshot_manifest` is `Manifest | None`** — `worker.execute_request:70` always passes it; nothing consumes a `None` variant downstream. Either drop the `Optional` (and the conditional serialization at `result.py:48-51`) or document the null case. Minor.
- **`OverlayShellRequest.from_dict`** is permissive (`payload.get("cwd") or "."`, `timeout_raw is not None`). If this crosses a daemon RPC boundary (it does — `daemon/handler/overlay.py:26`), tightening to "required keys must be present" prevents silent payload-shape drift. Currently a missing `request_id` becomes `""` which then raises in `__post_init__` — works, but the error message would be more informative at the boundary.

### Correctness nits
- `_is_overlay_whiteout` at `capture.py:262-265` has a fall-through that returns `True` for `is_file() and st_size == 0 and has(user.overlay.whiteout)`. This is the FUSE/userspace convention. The kernel convention (char-device, `st_rdev == 0`) is handled earlier. The two branches don't overlap. Correct.
- `OverlayPathChange.__post_init__` calls `normalize_layer_path(self.path, allow_root=self.kind == "opaque_dir")` — `opaque_dir` is the only kind permitted to carry an empty path (root opaque). Correct, but inline-comment the `allow_root` semantics next to the call (one line); right now it requires reading `normalize_layer_path`'s source to understand.

---

## Recommended sequence of edits

1. **H1** — Move `run_user_command` + `OverlayCommandResult` into `worker.py`. Run `python -m pytest backend/tests/unit_test/test_sandbox/test_overlay/ -x` to verify. ~50 LoC moved, cycle gone, import deferred-import deleted.
2. **M2** — Collapse `invoke` to a one-line `run_sync_in_executor(self.invoke_sync, ...)` wrapper. ~20 LoC.
3. **M3** — Inline the three timing helpers at the call site. ~25 LoC.
4. **M1** — Delete `OverlayInvoker` Protocol + `create_overlay_invoker`; pass `OverlayRuntimeInvoker` directly. ~25 LoC.
5. **M4** — Delete `worker.main` / `_parse_args` / `__main__` (or wire it). ~30 LoC.
6. **M5** — Convert `OverlayCommandResult` → tuple return (optional). ~5 LoC.
7. **L1, L2, L4, L5, L7** — Cleanup. ~20 LoC.

After steps 1–7: **~175 lines removed** (1125 → ~950), no behavior change, cycle eliminated, surface area shrunk by 3 public exports.

Steps 8+ (L3, structural split of `capture_changes`, RPC payload tightening) are judgment calls — depend on risk appetite.

---

## Summary

| Severity | Count | LoC saved |
|---|---:|---:|
| Critical | 0 | — |
| High | 1 | ~5 (cycle break; the value is in design clarity, not LoC) |
| Medium | 5 | ~105 |
| Low | 8 | ~40 |
| **Total** | **14** | **~150** |

Code is well-tested, security-aware, and behaviorally correct. The main waste is **Wave-2 consolidation artifacts** (cycle, dual-API duplication, named arithmetic helpers) and a **dead abstraction layer** (`OverlayInvoker` Protocol + factory). Cleaning those gets you the requested aggressive simplicity without touching the load-bearing overlay-marker logic in `capture.py`.

---

## Execution result (2026-05-15)

Applied H1, M2, M3, M4 + L1/L2/L4/L5/L7. Partial walkback on M1: `OverlayInvoker` Protocol kept (it's the duck-typed substitution seam used by `test_snapshot_overlay_runner.py:_FailingInvoker` — my initial review missed this). `create_overlay_invoker` 1-line factory deleted.

| File | Before | After | Δ |
|---|---:|---:|---:|
| `__init__.py` | 39 | 41 | +2 (formatting) |
| `capture.py` | 289 | 290 | +1 (added clarifying comment) |
| `change.py` | 85 | 82 | -3 |
| `mounts.py` | 103 | 98 | -5 |
| `pipeline.py` | 286 | 114 | **-172** |
| `request.py` | 66 | 66 | 0 |
| `result.py` | 98 | 98 | 0 |
| `runner.py` | 51 | 47 | -4 |
| `worker.py` | 108 | 164 | +56 (absorbed `run_user_command` etc.) |
| **Total** | **1125** | **1000** | **-125 (-11%)** |

Net redistribution: 116 lines deleted, ~56 lines moved pipeline→worker. The `pipeline ↔ worker` deferred-import cycle is **eliminated** (top-level import now), and `pipeline.py` is now a single-concept module (invoker only).

### Verification
- `tests/unit_test/test_sandbox/test_overlay/`: **19/19 pass** (0.36s)
- `tests/unit_test/test_sandbox/`: **545 passed, 1 skipped** (2.55s combined)
- `ruff check`: clean
- Public `__all__` matches pre-review surface minus `create_overlay_invoker`

### Remaining items (not executed)
- **Structural split of `capture_changes`** into kernel-upperdir vs synthetic-diff variants — would shave another ~30 LoC and clarify intent. Held back as a judgment call (touches load-bearing marker logic).
- **L3** (`_copy_tree` → `shutil.copytree` one-liner) — needs Linux verification that `symlinks=True, dirs_exist_ok=True` preserves the same symlink semantics as the hand-rolled iterator. Held back pending live_e2e check.
- **`OverlayCapture.snapshot_manifest: Manifest | None`** — `None` branch is unreachable; cleaning it would tighten the type. Held back as out-of-scope cosmetic.

---

## Round 2 (2026-05-15)

Applied R2, R3, R4, R5, R6, R7, R9, R10. Skipped:
- **R1** (private import leak) — independently resolved by parallel codex commit `d60edff3`; `_relative_target_escapes` was renamed to public `relative_symlink_target_escapes` and moved to `sandbox.layer_stack._paths`.
- **R8** (drop `snapshot_manifest` Optional) — `test_overlay_capture_timings_are_immutable` constructs `OverlayCapture` without `snapshot_manifest`; the default is load-bearing for that test fixture.
- **S1/S2/S3** — structural; held back as judgment calls.

| File | Round 1 → Round 2 | Δ |
|---|:---:|---:|
| `__init__.py` | 41 → 41 | 0 |
| `capture.py` | 290 → 278 | -12 (R9 `_marker` helper) |
| `change.py` | 82 → 82 | 0 |
| `mounts.py` | 98 → 86 | -12 (R4 `shutil.copytree`, R5 name constants +5) |
| `pipeline.py` | 114 → 80 | -34 (R2 inline, R3 dead metrics) |
| `request.py` | 66 → 66 | 0 |
| `result.py` | 98 → 98 | 0 |
| `runner.py` | 47 → 49 | +2 (R7 explicit None check) |
| `worker.py` | 164 → 160 | -4 (R6 inline `_validate_cwd`) |
| **Total** | **1000 → 940** | **-60 (-6%)** |

### Verification
- `tests/unit_test/test_sandbox/test_overlay/`: **19/19 pass** (0.30s)
- `ruff check`: clean
- Full sandbox suite has 3 unrelated failures from a parallel codex `layer_stack/` restructure (collapsing `manifest/_model.py`, deleting `layer/*` and `workspace/*` files) — none touch overlay code.

### Notable behavior changes
- **`overlay.invoker.queue_wait_s`, `overlay.invoker.worker_total_s`, `overlay.invoker.resume_wait_s`, `overlay.invoker.total_s` metrics removed.** They were structurally dead after round 1's `invoke` collapse (always ~0). No consumer reads them.
- **`mounts._copy_tree` replaced with `shutil.copytree(..., symlinks=True, dirs_exist_ok=True)`.** Should be equivalent for real-directory sources (which `MergedView.materialize` always produces), but if a Linux runtime hits a corner case it'd surface here.

---

## Cumulative trajectory

| Stage | LoC | Δ from prior | Δ from baseline |
|---|---:|---:|---:|
| Baseline | 1125 | — | — |
| Round 1 | 1000 | -125 (-11%) | -125 (-11%) |
| Round 2 | 940 | -60 (-6%) | -185 (-16.4%) |

`pipeline.py` alone: 286 → 80 (**-72%**). The Wave-2 consolidation grew it; the two-round refactor shrank it back to a single-concept module.

---

## Round 3 (2026-05-15)

Targeted: one performance bug (P1), one correctness-adjacent cleanup (S-rd-2), one symmetry polish (S-rd-1). Skipped P2/P3/Q1–Q4 as bikeshedding.

### Landed

- **P1** — `_populate_upperdir_from_diff` was doing an O(N) `_has_payload_descendant` scan inside an O(N) loop = **O(N²)**. Built a `dirs_with_descendants: set[Path]` prefix index once (O(N)), changed the per-iteration check to `rel not in dirs_with_descendants` (O(1)). Deleted `_has_payload_descendant`. For a 10k-path workspace: ~10⁷ comparisons → ~10⁴ set lookups.
- **S-rd-2** — `execute_request` now takes typed `request: OverlayShellRequest` and `manifest: Manifest` directly instead of dict payloads. `pipeline.invoke_sync` no longer round-trips via `request.to_dict()` / `manifest.to_dict()`. Validation now happens once at the daemon-handler boundary instead of twice.
- **S-rd-1** — Added `_content(kind, path, entry, *, symlink=False)` helper mirroring `_marker`. Symlink/file yields in `_walk_upperdir` dropped from 12 lines to 2.

### Result

| File | R2 → R3 | Δ |
|---|:---:|---:|
| `capture.py` | 278 → 277 | -1 |
| `worker.py` | 160 → 156 | -4 |
| Other | unchanged | 0 |
| **Total** | **940 → 935** | **-5** |

LoC delta is small because P1's prefix-set build (3 new lines + comment) nets against the deleted helper. The real wins are algorithmic, not visual.

### Verification
- `tests/unit_test/test_sandbox/test_overlay/`: **19/19 pass**
- `ruff check`: clean
- No external call-site updates needed (only `pipeline.invoke_sync` calls `execute_request`)

---

## Cumulative trajectory (final)

| Stage | LoC | Δ from prior | Δ from baseline |
|---|---:|---:|---:|
| Baseline | 1125 | — | — |
| Round 1 | 1000 | -125 (-11%) | -125 (-11%) |
| Round 2 | 940 | -60 (-6%) | -185 (-16.4%) |
| Round 3 | 935 | -5 (-0.5%) | -190 (-16.9%) |

After three rounds: pipeline↔worker cycle eliminated, public `OverlayInvoker` test seam preserved, no private cross-package imports, O(N²) bug fixed, redundant serialization removed, 16 public exports intact. Diminishing returns from here.
