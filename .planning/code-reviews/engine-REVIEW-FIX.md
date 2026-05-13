---
phase: engine (ad-hoc directory review)
fixed_at: 2026-05-13
review_path: .planning/code-reviews/engine-REVIEW.md
iteration: 1
fix_scope: critical_warning
findings_in_scope: 8
fixed: 6
skipped: 2
status: partial
---

# Engine Directory: Code Review Fix Report

**Fixed at:** 2026-05-13
**Source review:** `.planning/code-reviews/engine-REVIEW.md`
**Iteration:** 1
**Fix scope:** critical + warning (default — Info findings out of scope)

**Summary:**
- Findings in scope: 8 (1 Critical + 7 Warnings)
- Fixed: 6
- Skipped: 2 (1 design-change-required, 1 false positive)

Engine test suite (`backend/tests/unit_test/test_engine/`, 157 tests) plus
`test_tools/test_tool_execution.py` (28 tests) all pass after the fixes.

## Fixed Issues

### CR-01: Background cancel uses unsafe `task.cancel()` because `kill_callback` is hardcoded to `None`

**Files modified:** `backend/src/engine/background/dispatch.py`
**Commit:** `145ef313`
**Applied fix:** Surface the design hole at the call site with an explicit
multi-line TODO. The reviewer's stop-gap (`sandbox_api.kill_callback_for(...)`)
calls an API that does not exist — `sandbox/api/` has no per-process kill
primitive. Confirmed by grep across `backend/src/`. The proper resolution
requires the sandbox package to expose a kill primitive (out of fix-pass
scope). The TODO records:

- Why `kill_callback` is `None`,
- Which tools fall through to the unsafe `task.cancel()` branch
  (sandbox-backed `shell` with `background="optional"`),
- What needs to land before a real `kill_callback` can be wired.

Severity remains Critical — the bug is real and unresolved. The fix here
just makes the gap visible rather than letting `None` pass silently. IN-03
is implicitly resolved by the same change (the `None` is now documented
intent, not dead plumbing).

### WR-02: `cancel_all` swallows kill-callback failures silently on shutdown

**Files modified:** `backend/src/engine/background/manager.py`
**Commit:** `2264d99e`
**Applied fix:** Log kill-callback failures at WARNING (with `exc_info=True`),
not DEBUG, and `asyncio.gather(*cancelled_tasks, return_exceptions=True)` the
cancelled tasks so control returns only after cancel has been observed.

### WR-03: `_run_query_loop` and `run_ephemeral_agent` leak resources if consumer abandons

**Files modified:** `backend/src/engine/query/loop.py`, `backend/src/engine/agent/lifecycle.py`
**Commits:** `5439ce48` (primary fix), `1a6b52d4` (revert IN-02 fold-in)
**Applied fix:**

1. `_run_query_loop`: while-loop body wrapped in `try/finally`; the
   `background_manager.cancel_all()` cleanup now runs on every exit path
   (normal exit, exception, generator abandonment).
2. `run_ephemeral_agent`: the consumer iteration + post-loop cleanup is
   wrapped in `try/finally` so `tracker.finish()` is invoked even when a
   propagating `BaseException` (e.g. `asyncio.CancelledError`) bypasses the
   `except Exception` block.

The primary fix opportunistically also addressed IN-02 (`agent._messages` →
`agent.messages`), but the test fixture `_FakeAgent` in
`test_engine/test_lifecycle.py` only exposes `_messages` and broke. Reverted
the IN-02 part in `1a6b52d4`; the WR-03 cleanup is unaffected.

### WR-04: `_dispatch_many_foreground_tools` leaks events on tool exception

**Files modified:** `backend/src/engine/tool_call/dispatch.py`
**Commit:** `1a79c5bc`
**Applied fix:** `await asyncio.gather(*tasks, return_exceptions=True)` so a
late exception raised after a task has already enqueued its `(tc, result)`
tuple cannot replace the legitimate event list.

### WR-05: `_consume_provider_stream` does not stop on cancellation, leaks executor tasks

**Files modified:** `backend/src/engine/query/loop.py`
**Commit:** `ba8794fd`
**Applied fix:** Wrap the full body of `_consume_provider_stream` in
`try/except BaseException` that calls `executor.cancel_all()` and re-raises.
In-flight tool tasks now stop on any failure of the provider stream,
including the same-function `RuntimeError` raised when `state.final_message`
is `None`.

The same commit folds in IN-05 (broaden the `final_message is None`
guidance — the previous wording blamed API/auth/model on a condition that
also fires on transient provider hiccups).

### WR-07: `_initialize_loop_state` may mis-coerce non-Mapping `tool_metadata`

**Files modified:** `backend/src/engine/query/loop.py`
**Commit:** `2f3513e7`
**Applied fix:** Added an explicit `Mapping` check before calling
`ExecutionMetadata.update`. Non-Mapping types now raise `TypeError` at the
point of misuse rather than producing a partially-populated metadata or
failing deep inside `update`.

## Skipped Issues

### WR-01: Subagent cancel races with task startup via `asyncio.sleep(0)`

**File:** `backend/src/engine/background/manager.py:362-373`
**Reason:** Out of fix-pass scope — replacing `asyncio.sleep(0)` with a
cooperative early-stop flag (or a sync event) changes the cancel contract
between the manager and `run_subagent`. The existing inline comment is
honest about the one-cycle race; redesigning the path is a larger task than
a fix pass should take on.

**Original issue:** A single `await asyncio.sleep(0)` yields one event-loop
turn, which is not guaranteed to be enough for a freshly launched subagent
to reach its first cooperative await. On a busy loop, cancel can fire
before the subagent enters its main loop, defeating the early-stop intent.

**Follow-up:** File an issue describing the desired contract (cooperative
flag the subagent polls, or an event the subagent sets after entering its
main loop) before changing the cancel path.

### WR-06: `record_tool_trace` mutates path-list value shared across metadata copies

**File:** `backend/src/engine/tool_call/trace.py:32-40`
**Reason:** Verified false positive. `_normalize_trace_paths` returns a
**fresh list in every branch**:

- string input → `[stripped]` (new list)
- list input → `out` (new list built in the loop)
- anything else → `[]` (new list)

So the `existing` variable in `_append_trace_values` is already detached
from `metadata[key]`, `existing.append(value)` mutates that fresh list, and
`metadata[key] = existing` overwrites the original entry. Adding `list(...)`
would be harmless but redundant — there is no shared-list-mutation hazard
to defend against.

**Original issue (paraphrased):** Reviewer concluded that
`ExecutionMetadata.copy()`'s shallow copy of `extras` lets parent and child
metadata share the same list value through `record_tool_trace`. The
conclusion is wrong because the input list is never carried through
`_normalize_trace_paths` — a fresh list is always returned.

## Notes (out-of-scope Info findings)

The fix-pass scope is critical + warning only (no `--all` flag). These Info
findings were not addressed and remain candidates for a follow-up `--all`
pass:

- **IN-01** — empty subdirectories `engine/core/`, `engine/runtime/`,
  `engine/testing/` still exist on disk. Trivial `rmdir` cleanup.
- **IN-04** — `StreamingToolExecutor.deferred_dispatch_ids` still returns
  the live mutable `set`. Recommended fix: return `frozenset(self._deferred)`.

Info findings folded in opportunistically (because the same lines were
already being rewritten):

- **IN-02** — attempted and reverted (`1a6b52d4`); see WR-03 above.
- **IN-03** — implicitly resolved by CR-01's TODO.
- **IN-05** — folded into WR-05's commit.

## Verification

- `backend/tests/unit_test/test_engine/` — 157 passed
- `backend/tests/unit_test/test_tools/test_tool_execution.py` — 28 passed
- Per-file syntax check (`python -c "import ast; ast.parse(...)"`) passed for
  every modified file after each commit.

Per-finding commits (atomic):

```
1a6b52d4 fix(engine): revert IN-02 — _FakeAgent test fixture expects _messages
145ef313 fix(engine): CR-01 surface kill_callback gap with explicit TODO
5439ce48 fix(engine): WR-03 always run query-loop and tracker cleanup
2264d99e fix(engine): WR-02 surface and await kill-callback failures in cancel_all
2f3513e7 fix(engine): WR-07 reject non-Mapping tool_metadata loudly
ba8794fd fix(engine): WR-05 cancel executor tool tasks on stream failure
1a79c5bc fix(engine): WR-04 gather foreground tasks with return_exceptions
```

---

_Fixed: 2026-05-13_
_Fixer: Claude (direct apply — no fixer agent because gsd-sdk is not installed and no numeric phase exists)_
_Iteration: 1_
