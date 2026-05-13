---
phase: sandbox/provider
reviewed: 2026-05-13T00:00:00Z
depth: standard
files_reviewed: 15
files_reviewed_list:
  - backend/src/sandbox/provider/__init__.py
  - backend/src/sandbox/provider/protocol.py
  - backend/src/sandbox/provider/registry.py
  - backend/src/sandbox/provider/daytona/__init__.py
  - backend/src/sandbox/provider/daytona/adapter.py
  - backend/src/sandbox/provider/daytona/bash.py
  - backend/src/sandbox/provider/daytona/bootstrap.py
  - backend/src/sandbox/provider/daytona/context.py
  - backend/src/sandbox/provider/daytona/errors.py
  - backend/src/sandbox/provider/daytona/workspace.py
  - backend/src/sandbox/provider/daytona/client/__init__.py
  - backend/src/sandbox/provider/daytona/client/async_client.py
  - backend/src/sandbox/provider/daytona/client/credentials.py
  - backend/src/sandbox/provider/daytona/client/shutdown.py
  - backend/src/sandbox/provider/daytona/client/sync_client.py
findings:
  blocker: 3
  warning: 6
  info: 2
  total: 11
status: issues_found
---

# Sandbox Provider Subsystem: Code Review Report

**Reviewed:** 2026-05-13
**Depth:** standard
**Files Reviewed:** 15
**Status:** issues_found

## Summary

The provider subsystem is small, clearly factored, and free of the egregious anti-patterns the reviewer brief flagged as immediate concerns: credentials are never logged or carried in `__repr__`, the bash wrapper uses `shlex.quote` for both the script body and `cwd`, and no `requests.*` / `httpx.*` / `subprocess` calls exist in scope (HTTP is owned by the Daytona SDK).

That said, three correctness/lifecycle defects qualify as blockers because they directly contradict invariants the user memory and reviewer brief identified:

- `extract_exit_code` (`bash.py`) silently misreports a failed command as `exit_code=0` whenever the SDK returns a non-numeric `exit_code` string — and `adapter.py` derives `success` from that value.
- Several Daytona client calls — `client.get`, `client.list(limit=1)` inside `get_health`, `sandbox.start`/`stop`/`delete` paths via `fetch_sandbox` — have no upper-bound timeout at this layer, exposing every callsite to the documented "scheduler-degraded 300s hang".
- The sync client cache leaks the previous SDK client (and its HTTP session) every time credentials rotate; the async client correctly closes its predecessors but the sync path was never updated.

Additional warnings cover registry caching of unknown sandbox IDs, brittle private-attribute access into the SDK, a hard-coded 60s `stop` timeout inconsistent with the rest of the module, daemon-thread shutdown leaks, and a sandbox cache in `DaytonaContextPreparer` that never invalidates.

## Critical Issues

### CR-01: `extract_exit_code` silently coerces non-numeric SDK exit codes to 0 (success)

**File:** `backend/src/sandbox/provider/daytona/bash.py:52-59`

**Issue:** When no `__CODEX_EXIT_CODE__=` marker is present in the captured output and the SDK returns a string `exit_code` that does not parse as an integer (e.g. `""`, `"error"`, `"unknown"`, or any future SDK value), this function returns `(sanitized, 0)`. `adapter.exec` (line 314) then computes `success = (exit_code == 0)`, so a failed remote command is reported as a successful one. This is a silent correctness bug at the egress boundary between the SDK and every downstream caller.

```python
if fallback_exit_code is None:
    return sanitized, 0
if isinstance(fallback_exit_code, int):
    return sanitized, fallback_exit_code
stripped = fallback_exit_code.strip()
if stripped.lstrip("-").isdigit():
    return sanitized, int(stripped)
return sanitized, 0       # <- silent "success" for any other SDK string
```

**Fix:** Either fail closed with a non-zero sentinel (e.g. `124` or `-1`) or raise, so that callers do not falsely classify the command as successful:

```python
if fallback_exit_code is None:
    return sanitized, -1  # no marker and SDK didn't tell us → treat as failure
if isinstance(fallback_exit_code, int):
    return sanitized, fallback_exit_code
stripped = fallback_exit_code.strip()
if stripped.lstrip("-").isdigit():
    return sanitized, int(stripped)
logger.warning("Unparseable Daytona exit_code=%r; reporting failure", fallback_exit_code)
return sanitized, -1
```

Add a regression test that asserts `extract_exit_code("partial output", fallback_exit_code="error")` returns a non-zero exit code.

### CR-02: Unbounded hang on `client.get` / `client.list` — no timeout at this layer

**File:** `backend/src/sandbox/provider/daytona/client/sync_client.py:84-90`, `backend/src/sandbox/provider/daytona/client/async_client.py:96-102`, `backend/src/sandbox/provider/daytona/adapter.py:126`

**Issue:** User memory explicitly documents that `provider.create()` can hang 300s when the Daytona scheduler is degraded and that `/api/health` is a useless signal. The current code passes `timeout=_SANDBOX_TIMEOUT_SECONDS` to `create`, `start`, and `delete`, but the lookup primitives have no timeout at all:

```python
# sync_client.py:84-90
def fetch_sandbox(sandbox_id: str) -> Any:
    client = acquire_client()
    sandbox = client.get(sandbox_id)         # <- no timeout
    if sandbox is None:
        raise ValueError(...)
    return sandbox

# async_client.py:96-102 — same shape, no timeout
sandbox = await client.get(sandbox_id)

# adapter.py:126 — health probe itself can hang indefinitely
client.list(limit=1)
```

`fetch_sandbox` is on the hot path of `get`, `start`, `stop`, `delete`, `set_labels`, `get_signed_preview_url`, and `get_build_logs_url`, so a degraded scheduler hangs the entire orchestrator (and the health endpoint that should detect it). The reviewer brief flagged this explicitly.

**Fix:** Plumb `timeout=_SANDBOX_TIMEOUT_SECONDS` (or a smaller dedicated health timeout) through `client.get` and `client.list`. If the SDK does not accept `timeout` on those calls, wrap with `asyncio.wait_for` / a sync executor with a deadline, and surface a distinct `DaytonaTimeoutError` so the upstream retry layer can distinguish scheduler-degraded from network-error:

```python
sandbox = client.get(sandbox_id, timeout=_SANDBOX_TIMEOUT_SECONDS)
...
client.list(limit=1, timeout=min(_SANDBOX_TIMEOUT_SECONDS, 10.0))
```

Without this, the "no useful health signal" problem from MEMORY.md persists by construction.

### CR-03: Sync client cache leaks the previous SDK client on credential rotation

**File:** `backend/src/sandbox/provider/daytona/client/sync_client.py:63-81`

**Issue:** When `acquire_client()` is invoked with a different credentials key, it overwrites `_cached_client` without calling `.close()` on the previous instance. The async counterpart (`async_client.py:69-90`) explicitly tracks stale clients and closes them after releasing the lock; the sync path was apparently not updated.

```python
with _client_lock:
    if _cached_client is not None and _cached_client_key == current_key:
        return _cached_client
    ...
    _cached_client = Daytona(cfg)         # <- old client (with HTTP session) is dropped on the floor
    _cached_client_key = current_key
```

SDK clients hold pooled HTTP connections; this leaks them across credential rotation. In long-running orchestrators (Daytona scheduler restarts, runtime API-key rolls), this accumulates open sockets / FDs.

**Fix:** Mirror the async pattern — capture the stale client, install the new one, release the lock, then close outside the lock:

```python
stale_client: Any = None
with _client_lock:
    if _cached_client is not None and _cached_client_key == current_key:
        return _cached_client
    if _cached_client is not None:
        stale_client = _cached_client
    ...
    _cached_client = Daytona(cfg)
    _cached_client_key = current_key

if stale_client is not None:
    try:
        close_fn = getattr(stale_client, "close", None)
        if callable(close_fn):
            close_fn()
    except Exception:
        logger.debug("Failed to close superseded Daytona client", exc_info=True)
return _cached_client
```

## Warnings

### WR-01: `get_adapter` silently caches fallback bindings for unknown sandbox IDs

**File:** `backend/src/sandbox/provider/registry.py:60-75`

**Issue:** `get_adapter` falls back to the default provider for any sandbox_id it has not seen and mutates `_ADAPTERS` to memoize that fallback. Two problems:

1. The dict grows without bound — every bogus ID anyone passes (typos, scans, deleted sandboxes) is permanently bound. There is no eviction.
2. `has_registered_adapter(sandbox_id)` returns `False` initially, becomes `True` after a single `get_adapter()` call, even when the sandbox does not exist. Callers cannot reliably distinguish "an adapter was explicitly registered" from "fallback was cached on first miss".

**Fix:** Either do not cache the fallback (return `_DEFAULT` without storing) or track fallback-bound IDs in a separate structure / annotate the entry so `has_registered_adapter` does not flip on a fallback hit. Simpler:

```python
def get_adapter(sandbox_id: str) -> ProviderAdapter:
    if not sandbox_id:
        raise ValueError("sandbox_id is required")
    with _LOCK:
        adapter = _ADAPTERS.get(sandbox_id)
        if adapter is not None:
            return adapter
        if _DEFAULT is None:
            raise KeyError(sandbox_id)
        return _DEFAULT      # do not cache fallback
```

### WR-02: `get_build_logs_url` reaches into SDK private attribute via string concat

**File:** `backend/src/sandbox/provider/daytona/adapter.py:280`

**Issue:** `getattr(raw, "_sandbox" + "_api", None)` is a deliberate dodge of a lint rule against accessing `_sandbox_api` (a private SDK attribute). Any SDK rename silently returns `None` and the function permanently returns `None` — callers cannot tell broken from "logs not yet available". The string-concat trick also hides the access from grep/refactor tools.

**Fix:** Either drop the indirection and accept the lint waiver explicitly (with a `# noqa` and a comment pinning the SDK version), or — better — request a public method on the SDK and skip the workaround:

```python
# Pinned to daytona-sdk ==X.Y.Z; remove when SDK exposes public build-logs API.
daytona_api = getattr(raw, "_sandbox_api", None)  # noqa: SLF001
```

Add a smoke test that fails loudly if the attribute disappears in a future SDK upgrade.

### WR-03: Hard-coded 60s `stop` timeout inconsistent with module-wide setting

**File:** `backend/src/sandbox/provider/daytona/adapter.py:248`

**Issue:** Every other lifecycle call uses `_SANDBOX_TIMEOUT_SECONDS` (configurable via `EPHEMERALOS_SANDBOX_TIMEOUT_SECONDS`, default 300s). `stop` alone uses a magic literal `60`. On a degraded scheduler, a 60s ceiling on `stop` will fail spuriously while `start`/`delete` succeed, leaving the orchestrator in an inconsistent state.

**Fix:** Use `_SANDBOX_TIMEOUT_SECONDS` (or introduce a dedicated `_STOP_TIMEOUT_SECONDS` constant with its own env override):

```python
raw.stop(timeout=_SANDBOX_TIMEOUT_SECONDS)
```

### WR-04: `close_client` shutdown thread is abandoned after 1s timeout

**File:** `backend/src/sandbox/provider/daytona/client/shutdown.py:28-42`

**Issue:** `close_client` builds a daemon thread, runs the awaitable on a fresh loop, and `join(timeout=1.0)`. If the close takes longer than 1 second, the join returns but the thread keeps running on a loop that owns SDK transports; the surrounding code thinks shutdown is done. In long-running orchestrator processes this leaks SDK sessions / sockets until process exit.

**Fix:** Either remove the timeout (block until the close completes — at process shutdown that is acceptable) or log a `warning` when the join times out so the leak is observable:

```python
closer.join(timeout=5.0)
if closer.is_alive():
    logger.warning("Daytona async-client close exceeded 5s; abandoning thread")
```

### WR-05: `DaytonaContextPreparer._get_sandbox` never invalidates its cached sandbox

**File:** `backend/src/sandbox/provider/daytona/context.py:27-37`

**Issue:** The sync sandbox is fetched once and cached on `self._sandbox` for the lifetime of the preparer. If the sandbox is recreated or its state changes under the same ID (e.g. a rebuild), all subsequent `prepare_context` calls use stale data. The async path correctly invalidates per-loop-id, but the sync path has no equivalent invalidation hook.

**Fix:** Either expose an `invalidate()` method consumed by the caller after lifecycle transitions, or always re-fetch in `prepare_context` (the call is cheap relative to the rest of context preparation and avoids the staleness class of bugs).

### WR-06: `_paginate_all` has no safety cap on `total_pages`

**File:** `backend/src/sandbox/provider/daytona/client/sync_client.py:126-135`

**Issue:** The loop is bounded only by `total_pages` reported by the SDK. If the SDK ever returns a corrupt value (very large int), this will loop until OOM or rate-limit. Defense in depth — clamp to a sane maximum.

**Fix:** Cap the iteration:

```python
MAX_PAGES = 1000
for page in range(current_page + 1, min(total_pages, MAX_PAGES) + 1):
    ...
if total_pages > MAX_PAGES:
    logger.warning("Truncating pagination at %d pages (SDK reported %d)", MAX_PAGES, total_pages)
```

## Info

### IN-01: `_PROJECT_ROOT` uses brittle six-level relative path

**File:** `backend/src/sandbox/provider/daytona/client/credentials.py:10`

**Issue:** `Path(__file__).resolve().parents[6]` silently breaks if the file moves up or down even one directory. The failure mode is silent — `dotenv_values` on a missing file returns `{}`, the code falls back to `os.environ`, and the developer sees "Daytona not configured" without knowing the .env path is wrong. Given the recent tools-package reshuffle in git status, this is a real risk.

**Fix:** Either compute the project root from a known sentinel (search upward for `pyproject.toml`/`.git`) or assert the path exists at import time so the breakage is loud:

```python
_PROJECT_ROOT = Path(__file__).resolve().parents[6]
if not (_PROJECT_ROOT / "pyproject.toml").exists():
    logger.warning("Daytona credentials: project root resolution may be wrong (.env at %s)", _DOTENV_PATH)
```

### IN-02: `get_health` propagates raw SDK exception text to API consumers

**File:** `backend/src/sandbox/provider/daytona/adapter.py:135-143`

**Issue:** `detail = str(exc)` is returned to whatever consumes the health endpoint. SDK exceptions can include URL fragments, request IDs, and partial response bodies. Not a credential leak (credentials are gated above) but does propagate provider-internal error text to public health probes. Worth scrubbing or downgrading to a generic "provider unavailable" + log line.

**Fix:** Log the full exception, return a sanitized summary:

```python
except Exception as exc:
    logger.warning("Daytona health probe failed", exc_info=True)
    return {
        ...
        "detail": "Daytona API unreachable",
    }
```

---

_Reviewed: 2026-05-13_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
