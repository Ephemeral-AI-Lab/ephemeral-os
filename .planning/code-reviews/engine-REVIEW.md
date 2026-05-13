---
phase: engine (ad-hoc directory review)
reviewed: 2026-05-13T00:00:00Z
depth: standard
files_reviewed: 24
files_reviewed_list:
  - backend/src/engine/__init__.py
  - backend/src/engine/api.py
  - backend/src/engine/agent/__init__.py
  - backend/src/engine/agent/factory.py
  - backend/src/engine/agent/lifecycle.py
  - backend/src/engine/agent/run_tracker.py
  - backend/src/engine/background/__init__.py
  - backend/src/engine/background/dispatch.py
  - backend/src/engine/background/manager.py
  - backend/src/engine/background/reminder.py
  - backend/src/engine/query/__init__.py
  - backend/src/engine/query/context.py
  - backend/src/engine/query/loop.py
  - backend/src/engine/query/notifications.py
  - backend/src/engine/query/provider_history.py
  - backend/src/engine/query/request.py
  - backend/src/engine/tool_call/__init__.py
  - backend/src/engine/tool_call/batch.py
  - backend/src/engine/tool_call/context.py
  - backend/src/engine/tool_call/dispatch.py
  - backend/src/engine/tool_call/result.py
  - backend/src/engine/tool_call/streaming.py
  - backend/src/engine/tool_call/trace.py
  - (engine/core/, engine/runtime/, engine/testing/ are empty directories)
findings:
  critical: 1
  warning: 7
  info: 5
  total: 13
status: issues_found
---

# Engine Directory: Code Review Report

**Reviewed:** 2026-05-13
**Depth:** standard
**Files Reviewed:** 24 source files + 3 empty directories
**Status:** issues_found

## Summary

The `engine/` package is the central LLM query/tool-call/background loop. The recent
refactor has clean module boundaries — the `agent/`, `query/`, `tool_call/`, and
`background/` subpackages are each small and focused. The mainline path (one provider
stream, one tool batch, one cancel) is sound.

The findings cluster around three areas:

1. **Background-task cancellation safety.** `engine/background/dispatch.py` hardwires
   `kill_callback = None`. As a result the `BackgroundTaskManager.cancel` /
   `cancel_all` "logical-cancel via sandbox kill signal" pathway — explicitly
   designed (per its own docstring) to avoid corrupting the shared sandbox
   connection — is unreachable for any non-subagent background tool. Every
   non-subagent cancel falls through to `task.cancel()` against an in-flight
   provider exec, which is the failure mode the comment warns against.

2. **Async lifecycle leaks.** The query loop and `EphemeralAgent.run` rely on the
   consumer driving the async generator to exhaustion. If a caller abandons the
   stream (HTTP disconnect, parent exception, `break` mid-iteration), background
   asyncio tasks, the spawned tool tasks, and the agent's httpx client all leak.

3. **Empty/orphan code.** `engine/core/`, `engine/runtime/`, `engine/testing/` are
   empty directory shells left over from the refactor.

Findings are listed below in severity order. Performance issues are out of scope
per the review charter.

---

## Critical Issues

### CR-01: Background cancel uses unsafe `task.cancel()` because `kill_callback` is hardcoded to `None`

**File:** `backend/src/engine/background/dispatch.py:77`

**Issue:**
`launch_background_tool` initializes `kill_callback = None` (line 77), never
reassigns it, and then passes it through to `background_manager.launch(...,
kill_callback=kill_callback, ...)` on line 124. This is the *only* call site of
`BackgroundTaskManager.launch` in the codebase (verified via grep across
`backend/src/`).

`BackgroundTaskManager.cancel` (`manager.py:347-389`) has a multi-paragraph
docstring stating the design intent:

> "We do NOT call asyncio.Task.cancel() for sandbox-backed work: sending
> CancelledError through an in-flight provider exec can corrupt the shared
> sandbox connection. Instead the kill_callback sends a kill signal to the
> sandbox process, letting the provider call return naturally."

Because `kill_callback` is always `None`, the safe path (`if
tracked.kill_callback is not None: await tracked.kill_callback()` on line 379) is
**unreachable for every ordinary background tool**. Execution falls through to
the `elif tracked.task_type != "subagent":` branch and calls
`tracked.asyncio_task.cancel()` (line 388) — exactly the failure mode the
docstring warns against.

The identical hazard exists in `cancel_all` (`manager.py:444-451`).

Either the design has regressed (a real `kill_callback` used to be wired here
and the refactor lost it), or the docstring is aspirational and the entire
sandbox-corruption path is unprotected in practice. Either way the code does
not match its own contract.

**Fix:**
Either restore the sandbox-aware kill wiring (the most likely intent — fetch a
kill callback from `context.tool_metadata` / sandbox provider for the
`tool_use.name`'s sandbox process), or delete the dead `kill_callback` plumbing
top-to-bottom and rewrite the cancel docstring to reflect reality. As a stopgap
that makes intent explicit:

```python
# In launch_background_tool, derive the kill callback from the tool's sandbox.
kill_callback: KillCallback | None = None
sandbox_id = (
    tool_metadata.sandbox_id if tool_metadata is not None else ""
)
if sandbox_id:
    import sandbox.api as sandbox_api
    kill_callback = sandbox_api.kill_callback_for(sandbox_id, tool_use.name)
```

If no such API exists, this is the bug: surface it explicitly with `TODO` and
file an issue rather than silently passing `None`.

---

## Warnings

### WR-01: Subagent cancel races with task startup via `asyncio.sleep(0)`

**File:** `backend/src/engine/background/manager.py:362-373`

**Issue:**
For `task_type == "subagent"` the cancel path does:

```python
await asyncio.sleep(0)
tracked.asyncio_task.cancel()
await asyncio.sleep(0)
return True
```

A single `await asyncio.sleep(0)` yields exactly one event-loop turn. There is
no guarantee the subagent task has reached its first cooperative `await` after
one turn — `asyncio.create_task` schedules the coroutine but startup can
involve synchronous prologue (factory wiring, prompt assembly) that resumes on
the *next* turn. If the cancel fires before the first await, `CancelledError`
is raised at the start of the coroutine and no partial result can be
salvaged — defeating the stated "early stop" intent in the docstring (line
365-368).

Empirically the comment is honest about this being fragile ("Give a freshly
launched subagent one event-loop cycle"). One cycle is the minimum; on a busy
loop it's not sufficient.

**Fix:**
Either use a cooperative early-stop flag the subagent polls (preferred — the
`stop_mode` field already exists on `TrackedBackgroundTask`), or wait on an
event the subagent sets after entering its main loop. Avoid relying on
`sleep(0)` for synchronization.

### WR-02: `cancel_all` swallows kill-callback failures silently on shutdown

**File:** `backend/src/engine/background/manager.py:436-451`

**Issue:**
`cancel_all` is called from the query loop's exit path
(`engine/query/loop.py:367-368` and `:284-285`). If a `kill_callback` raises,
the `except Exception as exc: logger.debug(...)` swallows it at DEBUG level
(line 447). On query-loop shutdown this is the *only* hook that physically
kills the sandbox process; a silently-failed kill leaves a leaked sandbox
process behind and surfaces only as "sandbox quota exhausted" hours later.

Additionally the loop never `await`s the cancelled `asyncio_task` after
calling `.cancel()` on it (line 451) — control returns before cancellation
has actually been observed by the task.

**Fix:**
Log kill failures at WARNING level (not DEBUG), and `gather` the cancelled
tasks with `return_exceptions=True` before returning from `cancel_all`:

```python
cancelled_tasks = []
for tracked in self._tasks.values():
    if tracked.status == TaskStatus.RUNNING:
        # ... existing setup ...
        if tracked.kill_callback is not None:
            try:
                await tracked.kill_callback()
            except Exception:
                logger.warning(
                    "Kill callback failed for task %s",
                    tracked.task_id,
                    exc_info=True,
                )
        elif tracked.task_type != "subagent":
            tracked.asyncio_task.cancel()
            cancelled_tasks.append(tracked.asyncio_task)
if cancelled_tasks:
    await asyncio.gather(*cancelled_tasks, return_exceptions=True)
```

### WR-03: `EphemeralAgent.run` leaks httpx client and bg tasks if consumer abandons iteration

**File:** `backend/src/engine/agent/factory.py:70-93`, `backend/src/engine/query/loop.py:303-368`

**Issue:**
`EphemeralAgent.run` is an async generator that wraps `await self.close()` in
its `finally` block. This only runs if the consumer either drives the generator
to `StopAsyncIteration` *or* calls `aclose()` explicitly. If the consumer
breaks out of the `async for` loop on error (an HTTP disconnect or upstream
exception), Python eventually GCs the generator and runs `finally`, but the
cleanup ordering is undefined — and `_run_query_loop`'s background-cleanup
(`background_manager.cancel_all()` on line 367) is *also* in the body of the
generator, not in a `finally`. If an exception is raised inside
`_consume_provider_stream` or `_handle_tool_dispatch_branch`, that cleanup is
skipped entirely.

Concretely: a transient provider 500 raised from `stream_message` propagates
up, leaving:
- All background asyncio tasks running (never cancelled),
- All foreground `_dispatch_many_foreground_tools` tasks running (only the
  failed one is observed; siblings keep going),
- The httpx client open (because `EphemeralAgent.run`'s `finally` is never
  reached if the generator hasn't been driven).

**Fix:**
Restructure `_run_query_loop` to do its cleanup in a `try ... finally`:

```python
async def _run_query_loop(context, messages):
    background_manager, notification_service = _initialize_loop_state(context)
    try:
        while True:
            # ... existing body ...
    finally:
        if background_manager is not None and background_manager.has_pending():
            await background_manager.cancel_all()
```

Same `try/finally` should wrap the `async for event in agent.run(prompt)` in
`run_ephemeral_agent` (`lifecycle.py:149-163`) so cleanup happens even when
the caller aborts mid-stream.

### WR-04: `_dispatch_many_foreground_tools` leaks events on tool exception

**File:** `backend/src/engine/tool_call/dispatch.py:241-303`

**Issue:**
Each `run_foreground(tc)` task does:

```python
async def emit(event: StreamEvent) -> None:
    await queue.put(event)

try:
    result = await execute_tool_call_streaming(...)
except Exception as exc:
    logger.exception(...)
    result = ToolResultBlock(tool_use_id=tc.id, content=..., is_error=True)
await queue.put((tc, result))
```

If `execute_tool_call_streaming` emits N progress events into the queue and
then raises, the consumer loop counts only on the final `(tc, result)` tuple
to decrement `remaining`. The N orphan stream events sit in front of the
sentinel tuple — they will still be drained — that's fine.

But there's a subtler bug: `await asyncio.gather(*tasks)` on line 302 is
called **after** the consumer loop exits. The consumer loop exits when
`remaining` hits 0, i.e. each task has reported its `(tc, result)` tuple. But
nothing forbids a task from raising *after* it has enqueued the tuple
(unlikely given the code shape, but) — more importantly, `gather` is called
without `return_exceptions=True`, so if any background task raised after
emitting its tuple (e.g. from a cancelled callback running late), `gather`
re-raises and bubbles into the caller, replacing the legitimate result list.

**Fix:**
```python
await asyncio.gather(*tasks, return_exceptions=True)
return events
```

### WR-05: `_consume_provider_stream` does not stop on cancellation, leaks executor tasks

**File:** `backend/src/engine/query/loop.py:171-228`

**Issue:**
`_consume_provider_stream` iterates `context.api_client.stream_message(...)`.
It never enters a `try / except (asyncio.CancelledError, Exception)` block.
If the stream raises (network, provider error, or external cancellation), the
in-flight tool tasks the `StreamingToolExecutor` has already kicked off via
`add_tool` (`streaming.py:217-220`) are never cancelled — they continue
running in the background, ignored, holding sandbox processes open.

`executor.cancel_all()` exists (`streaming.py:267-273`) but is only called on
the `if not tool_results:` recovery path in `dispatch_assistant_tools`
(`dispatch.py:107`). On a stream exception the dispatch step is skipped
entirely.

**Fix:**
Wrap the stream consumption in a `try/finally` that calls
`executor.cancel_all()` on the failure path:

```python
async def _consume_provider_stream(...):
    try:
        async for event in context.api_client.stream_message(...):
            # ... existing body ...
    except BaseException:
        executor.cancel_all()
        raise
```

### WR-06: `record_tool_trace` mutates path-list value shared across metadata copies

**File:** `backend/src/engine/tool_call/trace.py:32-40`

**Issue:**
```python
existing = _normalize_trace_paths(metadata.get(key, []))
```

When `metadata[key]` already exists as a list, `_normalize_trace_paths`
returns a *new list* (via the `for ... if isinstance(...)` loop) — that's
fine. But if it returns an empty list because the value was `None` or a
non-string-or-list (e.g. someone set `metadata[key] = 0`), the subsequent
`metadata[key] = existing` overwrites the value, which is reasonable.

The real bug is that `metadata.copy()` (used liberally in
`engine/query/loop.py:149`) is a *shallow* copy:
`ExecutionMetadata.copy()` does `replace(self, extras=dict(self.extras))`
(`runtime.py:178-180`). For typed fields like `conversation_messages` the
value itself is not deep-copied. Per-response trace lists therefore share
state when the metadata is copied across nested invocations. For example, if
a parent `run_ephemeral_agent` records `_note_read_paths_this_response =
["a"]`, then `execute_tool_call_streaming` calls `metadata.copy()` for a
subagent dispatch and that subagent's `record_tool_trace` mutates the same
list with `["a", "b"]`. The parent now sees the subagent's appended paths.

**Fix:**
In `record_tool_trace`, defensively copy the list when fetching it so
mutation never leaks across metadata copies:

```python
existing = list(_normalize_trace_paths(metadata.get(key, [])))
```

(`_normalize_trace_paths` already returns a fresh list when the input is a
list, but the `extras` dict in `ExecutionMetadata.copy()` is a shallow copy,
so this defends against the *original* list still being referenced.)

### WR-07: `_initialize_loop_state` may mis-coerce non-Mapping `tool_metadata`

**File:** `backend/src/engine/query/loop.py:109-119`

**Issue:**
```python
if context.tool_metadata is None:
    context.tool_metadata = ExecutionMetadata()
elif not isinstance(context.tool_metadata, ExecutionMetadata):
    coerced = ExecutionMetadata()
    coerced.update(context.tool_metadata)
    context.tool_metadata = coerced
```

`ExecutionMetadata.update` (`runtime.py:159-176`) only handles
`ExecutionMetadata`, `Mapping`, or `**kwargs`. If `context.tool_metadata` is
some other duck-typed object (e.g. a dict subclass with a non-standard
`.items()`, or a Pydantic model), the coercion either fails with
`AttributeError` from `.items()` or silently produces a partially-populated
`ExecutionMetadata`. Type hints (`ExecutionMetadata | None`) make this
unlikely, but `lifecycle.py:135-136` lets callers pass either
`ExecutionMetadata | dict[str, Any]` as `extra_tool_metadata`, so dict input
is allowed by the public API.

**Fix:**
Replace the `elif` branch with an explicit `Mapping` check or an explicit
`TypeError`:

```python
elif isinstance(context.tool_metadata, Mapping):
    coerced = ExecutionMetadata()
    coerced.update(context.tool_metadata)
    context.tool_metadata = coerced
else:
    raise TypeError(
        f"tool_metadata must be ExecutionMetadata or Mapping, "
        f"got {type(context.tool_metadata).__name__}"
    )
```

---

## Info

### IN-01: Empty subdirectories `engine/core/`, `engine/runtime/`, `engine/testing/`

**File:** `backend/src/engine/core/`, `backend/src/engine/runtime/`, `backend/src/engine/testing/`

**Issue:**
These directories exist on disk (created 2026-05-13 per `ls`) but contain no
`__init__.py` or any files. They are not imported anywhere in the codebase
(verified by grep) and serve no purpose. Likely leftover from the recent
refactor that flattened the package structure.

**Fix:**
Remove the empty directories: `rmdir backend/src/engine/{core,runtime,testing}`.

### IN-02: `agent/lifecycle.py:170` reaches into private `agent._messages`

**File:** `backend/src/engine/agent/lifecycle.py:170, 189`

**Issue:**
```python
terminal_result = ... or _last_terminal_tool_result(agent._messages)
# ...
tracker.finish(messages=list(agent._messages), ...)
```

`EphemeralAgent` exposes the same data via the public `messages` property
(`factory.py:62-68`). Accessing `_messages` defeats the encapsulation and
will silently break if the property ever introduces filtering/sanitization.

**Fix:**
Use `agent.messages` instead of `agent._messages` in both spots.

### IN-03: `kill_callback` parameter on `launch_background_tool` is never used

**File:** `backend/src/engine/background/dispatch.py:77`

**Issue:**
After CR-01 is addressed one way or the other, the local `kill_callback =
None` line (and the resulting `kill_callback=kill_callback` keyword arg on
line 124) should either be assigned meaningfully or deleted entirely.

**Fix:**
See CR-01.

### IN-04: Streaming executor exposes mutable internal set via property

**File:** `backend/src/engine/tool_call/streaming.py:96-99`

**Issue:**
```python
@property
def deferred_dispatch_ids(self) -> set[str]:
    """IDs of tool_uses the caller asked us to defer (not execute)."""
    return self._deferred
```

The property returns the live `self._deferred` set — a caller mutating it
will corrupt the executor's state. The only current caller
(`tool_call/dispatch.py:88`) only reads, but the API invites foot-gun.

**Fix:**
Return `frozenset(self._deferred)` (or document the contract clearly).

### IN-05: `_consume_provider_stream` `final_message is None` raise produces unhelpful guidance for transient failures

**File:** `backend/src/engine/query/loop.py:224-228`

**Issue:**
```python
if state.final_message is None:
    raise RuntimeError(
        f"Model stream finished without a final message for model {context.model}. "
        "Check that the API endpoint, authentication, and model name are correct."
    )
```

The instruction is misleading: this also fires on transient provider
hiccups (early stream close, content-filter, rate-limit-induced cutoffs)
where API/auth/model are *not* misconfigured. Operators wasted time
re-checking credentials when the actual cause is a flaky provider.

**Fix:**
Either include the stream metadata that's been observed so far (e.g.
"received N events, last event type=..."), or soften the guidance:
"...could indicate provider error, content filter cutoff, or
misconfiguration."

---

## Notes (not findings)

- The numerous broad `except Exception` blocks in `agent/run_tracker.py` and
  `agent/lifecycle.py` are deliberate degradation around optional DB
  persistence and lifecycle hooks. They are NOT bare excepts (they log via
  `exc_info=True` or via `logger.debug` with the exc message). Out of scope.
- `provider_history.py` deep-copies messages twice (once in
  `reduce_background_task_history`, once in `sanitize_tool_sequence`). This
  is a perf concern, and perf is out of scope for v1.
- `tool_call/batch.py:18` uses `str(tc.id)` to coerce the tool_use id —
  defensive but harmless.

---

_Reviewed: 2026-05-13_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
