---
phase: providers (ad-hoc directory review)
reviewed: 2026-05-13T00:00:00Z
depth: standard
files_reviewed: 9
files_reviewed_list:
  - backend/src/providers/__init__.py
  - backend/src/providers/api/__init__.py
  - backend/src/providers/api/router.py
  - backend/src/providers/api/schemas.py
  - backend/src/providers/clients/__init__.py
  - backend/src/providers/clients/anthropic_native.py
  - backend/src/providers/errors.py
  - backend/src/providers/provider.py
  - backend/src/providers/types.py
findings:
  critical: 1
  warning: 3
  info: 3
  legacy: 9
  total: 16
status: issues_found
---

# Providers Directory: Code Review Report

**Reviewed:** 2026-05-13
**Depth:** standard
**Files Reviewed:** 9
**Status:** issues_found

## Summary

The `providers/` package is the LLM API client abstraction. It wraps a single concrete provider (Anthropic via the official `anthropic` SDK) behind a `SupportsStreamingMessages` protocol consumed by the engine query loop.

The review surfaced **one BLOCKER**, **three WARNINGs**, and a large concentration of **legacy / dead code** — most of which is structural debris from an unrealised multi-provider plan. The critical correctness issue is in `anthropic_native.py`: the retry wrapper can re-yield events that were already streamed to the caller, because retries are not gated on "no events emitted yet". Existing tests do not exercise this path, so it has never been caught.

The cleanup hooks the user asked for are concentrated in section 3 (Legacy / Dead Code), grouped by deletion unit so the follow-up "fix all + remove unused / legacy" pass is mechanical. Roughly the entire `providers/api/` subtree, all of `detect_provider` / `auth_status` / `ProviderInfo` / `_active_kwargs`, the `ApiCancelEvent` type, and the `AuthenticationFailure` / `RateLimitFailure` / `RequestFailure` subclasses are removable without changing any externally observed behaviour. Concrete consumer counts are listed inline.

---

## 1. BLOCKER

### CR-01: Retry loop can re-yield already-streamed events (mid-stream duplicates)

**File:** `backend/src/providers/clients/anthropic_native.py:79-101`
**Issue:**
`stream_message` wraps `_stream_once` in a retry loop:

```python
for attempt in range(MAX_RETRIES + 1):
    try:
        async for event in self._stream_once(request):
            yield event
        return
    except EphemeralOSApiError:
        raise
    except Exception as exc:
        last_error = exc
        if attempt >= MAX_RETRIES or not self._is_retryable(exc):
            raise self._translate_error(exc) from exc
        ...
        await asyncio.sleep(delay)
```

`_is_retryable` returns `True` for `ConnectionError`, `TimeoutError`, `OSError`, and 5xx/429 `APIStatusError`. All of these can be raised by the underlying `anthropic` SDK **mid-stream** — i.e., inside `async for event in stream:` at line 139, *after* one or more `ApiTextDeltaEvent` / `ApiToolUseDeltaEvent` events have already been forwarded to the engine query loop.

When that happens, the outer `for attempt` loop retries `_stream_once` from scratch and re-yields the new attempt's events to the caller. The consumer in `engine/query/loop.py:164` is not idempotent: it appends every `ApiTextDeltaEvent` to the visible transcript, records every `ApiToolUseDeltaEvent` as a fresh tool dispatch (and against the tool budget — see `_consume_tool_budget_or_reject` at `loop.py:175`), and can hit `RuntimeError("Model stream finished without a final message")` if the duplicate stream still fails.

Observable user-facing impact:
1. Duplicated assistant text in the rendered transcript.
2. Same `tool_use_id` enqueued twice — second dispatch may collide or be silently double-counted against budget.
3. Half the time, an authoritative `_translate_error` raised on the duplicate retry path masks the original `EphemeralOSApiError`.

**Evidence that no test catches this:**
`test_anthropic_client.py::TestRetryOn429::test_retry_on_429` (line 404) and `TestRateLimitError` (line 448) both raise via `side_effect=` on `client._client.messages.stream` — i.e., the error happens **before** `MockStream.__aiter__` yields anything. No test exercises an error raised partway through the `async for event in stream` loop.

**Fix:** Gate retry on "no events emitted yet on this attempt". Track an `emitted_any` flag; on exception, only retry when `emitted_any` is False. Once a single event has been yielded, fail fast (or raise a dedicated `StreamInterrupted` so the caller can decide).

```python
async def stream_message(self, request: ApiMessageRequest) -> AsyncIterator[ApiStreamEvent]:
    for attempt in range(MAX_RETRIES + 1):
        emitted_any = False
        try:
            async for event in self._stream_once(request):
                emitted_any = True
                yield event
            return
        except EphemeralOSApiError:
            raise
        except Exception as exc:
            if emitted_any or attempt >= MAX_RETRIES or not self._is_retryable(exc):
                raise self._translate_error(exc) from exc
            delay = min(BASE_DELAY * (2**attempt), MAX_DELAY)
            log.warning(
                "Anthropic API request failed (attempt %d/%d), retrying in %.1fs: %s",
                attempt + 1, MAX_RETRIES + 1, delay, exc,
            )
            await asyncio.sleep(delay)
```

Also add a regression test: a `MockStream` whose `__aiter__` yields one `content_block_delta` then raises `OSError`. Assert the consumer sees the error (no duplicate events, no second `MockStream.__aiter__` invocation).

---

## 2. WARNINGS

### WR-01: Unreachable post-loop raise; dead `last_error` accumulator

**File:** `backend/src/providers/clients/anthropic_native.py:79-104`
**Issue:**
```python
last_error: Exception | None = None
for attempt in range(MAX_RETRIES + 1):
    try:
        ...
        return
    except EphemeralOSApiError:
        raise
    except Exception as exc:
        last_error = exc
        if attempt >= MAX_RETRIES or not self._is_retryable(exc):
            raise self._translate_error(exc) from exc
        ...
        await asyncio.sleep(delay)

if last_error is not None:
    raise self._translate_error(last_error) from last_error
```

The loop has `MAX_RETRIES + 1 == 4` iterations. On any iteration, control either `return`s (success) or raises. On the **final** iteration, `attempt == MAX_RETRIES` so the inner `if attempt >= MAX_RETRIES` triggers and raises. The post-loop block is unreachable. `last_error` is written but never read.

**Fix:** Remove lines 79 (`last_error: Exception | None = None`), 89 (`last_error = exc`), and 103–104 (post-loop raise). The fix in CR-01 above already drops these; if CR-01 is implemented as suggested, this WARNING is absorbed.

---

### WR-02: `detect_provider` returns the SDK *class name*, not the provider name

**File:** `backend/src/providers/provider.py:27-48`
**Issue:**
```python
def detect_provider() -> ProviderInfo:
    name = "anthropic"
    try:
        if getattr(model_store, "is_available", False):
            active = model_store.get_active_resolved()
            if active:
                class_path = str(active.get("class_path") or "")
                if class_path:
                    name = class_path.rsplit(".", 1)[-1] or class_path
    except Exception:
        pass
    return ProviderInfo(name=name, ...)
```

When a `class_path` is present, the code does `class_path.rsplit(".", 1)[-1]`, which extracts the **class name** (e.g., `"AnthropicClient"`), not the provider name (`"anthropic"`). Combined with the function having zero callers in the codebase (see LE-02), the semantics are not just dead — they are *wrong*, and if anyone ever wires this into a UI/diagnostics surface they will get strings like `"AnthropicClient"` rendered as "the provider".

**Fix:** Either delete (preferred — no consumers) or map class paths to provider names explicitly (`"providers.clients.anthropic_native.AnthropicClient" → "anthropic"`). Pick one; do not leave the substring hack.

Also note: `auth_status()` returns the bare string `"configured" | "missing"` and is similarly never consumed. Same fate as `detect_provider`.

---

### WR-03: Bare `except Exception: pass` swallows configuration errors silently

**File:** `backend/src/providers/provider.py:34-42`
**Issue:**
```python
try:
    if getattr(model_store, "is_available", False):
        active = model_store.get_active_resolved()
        if active:
            class_path = str(active.get("class_path") or "")
            if class_path:
                name = class_path.rsplit(".", 1)[-1] or class_path
except Exception:
    pass
```

If `get_active_resolved()` raises (e.g., DB corruption, schema drift), `detect_provider` silently returns `name="anthropic"` with no log. There is no way to surface this to operators.

**Fix:** Either delete the function (recommended — zero consumers; see LE-02), or replace the bare `except Exception: pass` with a logged debug:

```python
except Exception:
    logger.debug("detect_provider: failed to resolve active model class_path", exc_info=True)
```

Note: `logger` is not imported in this module today. The bare swallow is the only failure mode currently exercised, so adding logging requires also adding `import logging; logger = logging.getLogger(__name__)`.

---

## 3. LEGACY / DEAD CODE (concentrated cleanup section)

Findings here have zero impact on shipped behaviour. They are grouped by deletion unit; each group is independent and can be removed without touching the others.

### LE-01: Entire `providers/api/` subtree has zero importers

**Files:**
- `backend/src/providers/api/__init__.py` (whole file)
- `backend/src/providers/api/router.py` (whole file)
- `backend/src/providers/api/schemas.py` (whole file)
- `backend/src/providers/__init__.py:52-54, 62-65` (the `"create_models_router"` export + `__getattr__` branch)

**Issue:** `create_models_router` is exported from `providers.__init__` via lazy `__getattr__` but has **zero importers** across the whole repository (verified by grep). `RegisterModelRequest` and `SelectModelRequest` in `schemas.py` are used only by `router.py` itself. The whole subtree is orphaned.

This is the "hypothetical second provider" the user flagged. The router talks to `db.stores.model_store` (model CRUD), which is a database concern, not a provider-protocol concern; the file is misplaced even if it were live.

**Fix:** Delete the four files and remove `"create_models_router"` from `providers/__init__.py`'s `__all__` and the `__getattr__` branch.

Before deleting, confirm with the user that the model-management HTTP surface is genuinely not wired into `runtime/app_factory.py` or any FastAPI app router — grep was clean here but the user should sign off.

---

### LE-02: `detect_provider`, `auth_status`, `ProviderInfo`, `_active_kwargs` — zero external callers

**File:** `backend/src/providers/provider.py:11-54`
**Issue:** Verified by grep — `detect_provider`, `auth_status`, and `ProviderInfo` appear only in their definitions and in the top-level `providers/__init__.py` export list. No external module imports them. `_active_kwargs` (line 21) is the sole caller of `try_get_active_model_kwargs` from this file and exists only to feed `auth_status`; it falls with the public function.

The only live export from `provider.py` is `make_api_client`.

**Fix:** Remove from `provider.py`:
- `from dataclasses import dataclass` (loses its only consumer)
- The `@dataclass(frozen=True) class ProviderInfo:` (lines 11-18)
- `def _active_kwargs():` (lines 21-24)
- `def detect_provider():` (lines 27-48)
- `def auth_status():` (lines 51-54)

Remove from `providers/__init__.py`:
- The imports `ProviderInfo`, `auth_status`, `detect_provider` (lines 23-25)
- The `__all__` entries `"ProviderInfo"`, `"detect_provider"`, `"auth_status"` (lines 46-48)

After removal, `provider.py` shrinks to just `make_api_client`. Consider renaming the file to `factory.py` (post-cleanup, separate change).

---

### LE-03: `ApiCancelEvent` type and dispatch branch — never emitted

**Files:**
- `backend/src/providers/types.py:86-95` (class definition)
- `backend/src/providers/types.py:98-104` (union member)
- `backend/src/providers/__init__.py:6,31` (export)
- `backend/src/engine/query/loop.py:11,199-201` (dispatch branch in the consumer)

**Issue:** `ApiCancelEvent` is defined and dispatched by the engine query loop:
```python
if isinstance(event, ApiCancelEvent):
    executor.cancel(event.tool_id, event.reason)
    continue
```
…but **no producer in the repository ever emits one**. Grep for `ApiCancelEvent(`, `yield ApiCancelEvent`, etc., returns nothing in `backend/src/` outside the type itself. `AnthropicClient._stream_once` (the only live producer) never yields it. The docstring on the class says "Emitted when the LLM decides to abort a long-running tool" — Anthropic's API has no such signal, and this was presumably anticipated infrastructure for an architecture that didn't land.

**Fix:** Delete the class (`types.py:86-95`), remove from the `ApiStreamEvent` union (line 103), remove the `ApiCancelEvent` import and `__all__` entry in `providers/__init__.py`, and remove the `isinstance(event, ApiCancelEvent)` branch in `engine/query/loop.py:199-201`. Also remove the import on `engine/query/loop.py:11`.

---

### LE-04: Error subclasses `AuthenticationFailure` / `RateLimitFailure` / `RequestFailure` not caught by any consumer

**File:** `backend/src/providers/errors.py:10-19`
**Issue:** Searched for `except (AuthenticationFailure | RateLimitFailure | RequestFailure)` and for any `isinstance` checks on these subclasses outside `providers/` itself. Only tests catch them (`test_anthropic_client.py` asserts the type of the raised exception). Production callers (`engine/query/loop.py`, `engine/agent/factory.py`, etc.) catch only `EphemeralOSApiError` or nothing at all. The granularity is unused.

This is the "designed-for-future, used-by-nobody" smell — it is harmless on its own, so a defensible position is to keep it. But the user asked for "remove unused / legacy" hooks; this qualifies.

**Recommendation:** Either:
- Keep `EphemeralOSApiError` only and have `_translate_error` raise the base class with a `kind=` attribute, or
- Wire at least one consumer to actually branch on the subclass (e.g., surface `AuthenticationFailure` as a different HTTP status to the UI).

The third option — delete the subclasses now — is fine if no consumer materialises soon.

---

### LE-05: `from config.model_config import try_get_active_model_kwargs  # noqa: F401` — dead import

**File:** `backend/src/providers/provider.py:29`
**Issue:**
```python
def detect_provider() -> ProviderInfo:
    """Return provider metadata derived from the active model registration's class_path."""
    from config.model_config import try_get_active_model_kwargs  # noqa: F401

    from runtime.app_factory import model_store
    ...
```
The symbol is imported but never referenced in the function body, and silenced with `noqa: F401`. Pure refactor leftover. Removed transitively when `detect_provider` is deleted (LE-02), but flag it explicitly so anyone deciding to *keep* `detect_provider` still cleans up the dead import.

**Fix:** Delete line 29.

---

### LE-06: `ApiMessageRequest.raw_messages` field — only the same client reads it; no external producer

**File:** `backend/src/providers/types.py:47`
**Issue:**
```python
raw_messages: list[dict[str, Any]] | None = None
```
The only branch reading it is `anthropic_native.py:112` (`if request.raw_messages is not None`). Grep for sites that construct `ApiMessageRequest(...,  raw_messages=...)` shows zero callers — `engine/query/request.py` builds the request without it. This is an escape hatch nobody uses today.

**Recommendation:** Either keep with a comment naming the intended escape-hatch use-case, or remove the field together with the branch at `anthropic_native.py:112-115`. Without `raw_messages` the branch simplifies to a single line:
```python
messages = [msg.to_api_param() for msg in request.messages]
```

Lean toward deletion given the project's "no speculative flexibility" rule in CLAUDE.md.

---

### LE-07: `ApiCancelEvent` and `__all__` re-export both lazy-import `AnthropicClient` and `create_models_router` from the same module

**File:** `backend/src/providers/__init__.py`
**Issue:** The `__getattr__` provides lazy imports for `AnthropicClient` and `create_models_router`. `AnthropicClient` is still consumed via the direct path `providers.clients.anthropic_native` (see `provider.py:71` and `test_anthropic_client.py:11`); nobody imports it from the top-level `providers` namespace. After LE-01 deletes the model router, the `__getattr__` only serves the unused top-level `AnthropicClient` re-export.

**Recommendation:** Once LE-01 lands, drop the entire `__getattr__` function (lines 57-66), remove `"AnthropicClient"` from `__all__`, and let callers continue importing from `providers.clients.anthropic_native` directly (which is what every current consumer already does). Eager top-level imports of types/errors/provider are fine because they have no optional-dependency cost — the lazy machinery exists only for the deprecated dead exports.

---

### LE-08: Cancel-related fields on `ApiCancelEvent` (`tool_id`, `reason`) carry the same dead-code lifetime

**File:** `backend/src/providers/types.py:81-95`
**Issue:** Already covered by LE-03, listed separately because the fields' docstring ("Emitted when the LLM decides to abort a long-running tool") will look like aspirational documentation to a reader who skips the surrounding code. Once LE-03 lands, the docstring also disappears.

---

### LE-09: Lazy submodule shim `providers/api/__init__.py` is redundant indirection

**File:** `backend/src/providers/api/__init__.py`
**Issue:** This file's only role is to provide `create_models_router` via `__getattr__` from `providers.api.router`. There is no optional-dependency reason for laziness — `fastapi` is already a hard dependency of the rest of the codebase. Deleted as part of LE-01.

---

## 4. INFO

### IN-01: `_translate_error` ignores `EphemeralOSApiError` already-wrapped case

**File:** `backend/src/providers/clients/anthropic_native.py:202-220`
**Issue:** `_translate_error` is called from both the inner `if attempt >= MAX_RETRIES or not self._is_retryable(exc)` branch and (today, before CR-01 is fixed) the dead post-loop. The function does `getattr(exc, "status_code", None)`. For an already-translated `EphemeralOSApiError` (which doesn't have `status_code`), it returns `RequestFailure(str(exc))`, double-wrapping. The first `except EphemeralOSApiError: raise` at line 86 currently prevents this, but the call signature is permissive enough that a future refactor could trip it.

**Fix:** Make the type assertion explicit:
```python
@staticmethod
def _translate_error(exc: Exception) -> EphemeralOSApiError:
    if isinstance(exc, EphemeralOSApiError):
        return exc
    ...
```

---

### IN-02: `kwargs.get("api_key") or ""` masks a legitimate empty-string config

**File:** `backend/src/providers/provider.py:78`
**Issue:**
```python
api_key = db_kwargs.get("api_key") or ""
base_url = db_kwargs.get("base_url")
if not api_key:
    raise NoActiveModelError("Active model registration has no api_key")
```
The `or ""` makes `None` and `""` indistinguishable from missing keys. Today the immediate `if not api_key:` makes this safe, but the idiom rolls together two distinct failure modes ("never registered" vs "registered with empty key") into one error string. Mostly stylistic.

**Fix:** `api_key = db_kwargs.get("api_key")` and let `if not api_key:` handle both. The `or ""` adds nothing.

---

### IN-03: `provider.py` module docstring is stale relative to actual surface

**File:** `backend/src/providers/provider.py:1`
**Issue:** Module docstring reads `"""Provider/auth capability helpers and API client factory."""`. After LE-02 removes `ProviderInfo` / `detect_provider` / `auth_status`, only the factory remains. Update the docstring when the cleanup lands. Minor.

---

## Cleanup Hook Summary (mechanical removal order)

For the user's "remove unused / legacy" follow-up, here is a safe deletion order:

1. **LE-01** — Delete `providers/api/` subtree (4 files) and the lazy export. Independent.
2. **LE-03 + LE-08** — Delete `ApiCancelEvent` + remove `engine/query/loop.py:199-201`. One unit.
3. **LE-02 + LE-05** — Delete `ProviderInfo` / `detect_provider` / `auth_status` / `_active_kwargs` / the noqa import. One unit.
4. **LE-06** — Delete `raw_messages` field and the branch in `_stream_once`. Verify zero callers first (grep `raw_messages=`).
5. **LE-07** — Drop `__getattr__` in `providers/__init__.py` and the `AnthropicClient` re-export from `__all__`. Verify with `from providers import AnthropicClient` returning zero hits.
6. **LE-04** — Decide subclass policy (keep + wire one consumer, or collapse). Lowest urgency.

Each step is independently testable: after each, run `.venv/bin/pytest backend/tests/unit_test/test_providers backend/tests/unit_test/test_engine` and confirm green.

---

_Reviewed: 2026-05-13_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
