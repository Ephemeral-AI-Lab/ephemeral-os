---
phase: sandbox/api
reviewed: 2026-05-13T00:00:00Z
depth: standard
files_reviewed: 13
files_reviewed_list:
  - backend/src/sandbox/__init__.py
  - backend/src/sandbox/models.py
  - backend/src/sandbox/api/__init__.py
  - backend/src/sandbox/api/facade.py
  - backend/src/sandbox/api/status.py
  - backend/src/sandbox/api/tool/__init__.py
  - backend/src/sandbox/api/tool/_payload.py
  - backend/src/sandbox/api/tool/edit.py
  - backend/src/sandbox/api/tool/raw_exec.py
  - backend/src/sandbox/api/tool/read.py
  - backend/src/sandbox/api/tool/shell.py
  - backend/src/sandbox/api/tool/write.py
findings:
  critical: 0
  warning: 6
  info: 5
  total: 11
status: issues_found
---

# Phase sandbox/api: Code Review Report

**Reviewed:** 2026-05-13
**Depth:** standard
**Files Reviewed:** 13
**Status:** issues_found

## Summary

The `sandbox.api` layer is a thin transport facade: API verbs construct JSON-ish payloads and forward them to the in-sandbox daemon via `call_daemon_api`. Path/command validation and audit enforcement are the daemon's responsibility, so traversal/injection at this layer is correctly delegated.

No BLOCKERs were found at this layer. The most consequential issues are:

1. **Audit-identity loss** — `SandboxCaller` carries `run_id`, `agent_run_id`, `task_id` and the model docstring states the caller is "threaded onto every audit-aware request," yet every verb forwards only `caller.agent_id` (as `actor_id`). Three of four caller fields are silently dropped at the API boundary. Whether this is a BLOCKER depends on whether reads/edits/shell are required to be auditable by `run_id`/`task_id` upstream; daemon handlers currently consume only `actor_id`, so this is a WARNING here but should be confirmed against audit requirements.
2. **Payload coercion contract gaps** — `int_from_payload` is documented to raise `TypeError` for bad shapes but will raise `ValueError` on a non-numeric string (e.g., `int("abc")`), which is the path used to coerce `exit_code` in `shell.py`. A malformed daemon payload becomes an unhandled `ValueError`.
3. **`paths_from_payload` shape laxity** — A `dict` is `Iterable`, so a dict payload silently yields its keys; the helper is also reused for `warnings` in `shell.py` even though warning strings have different semantics (empty strings are filtered out).
4. **`status._configured_sandbox_defaults` coupling** — When `default_snapshot` is set, `default_image` is forced to `None`, which is fine alone but is then layered with `health.get("default_image")` in `get_health`; surprising semantics but not broken.

The facade's lazy intra-method imports avoid circular dependencies cleanly. The module-level `_client = SandboxClient()` is stateless and safe. No hardcoded secrets, no `eval`, no shell-interpolation injection at this layer (commands are forwarded verbatim to the daemon, which is the right design).

## Warnings

### WR-01: `SandboxCaller` fields beyond `agent_id` are silently dropped at the API boundary

**File:** `backend/src/sandbox/models.py:12-19`, `backend/src/sandbox/api/tool/edit.py:26`, `backend/src/sandbox/api/tool/write.py:23`, `backend/src/sandbox/api/tool/shell.py:36`
**Issue:** `SandboxCaller` accepts `agent_id`, `run_id`, `agent_run_id`, `task_id` and the dataclass docstring promises "Caller identity threaded onto every audit-aware request." All four guarded verbs forward only `caller.agent_id` as `actor_id` and discard the other three fields. Callers may set `run_id`/`task_id`, observe successful calls, and trust audit recorded those — but it never reaches the daemon. This is a silent contract gap at the public API surface.
**Fix:** Either (a) extend the daemon payload to carry all caller fields and confirm the daemon-side audit consumes them, or (b) remove the unused fields from `SandboxCaller` (or document them as "host-side only, not threaded to sandbox"). Pick whichever matches actual audit requirements. Minimum change in edit.py / write.py / shell.py:
```python
"actor_id": request.caller.agent_id,
"run_id": request.caller.run_id,
"agent_run_id": request.caller.agent_run_id,
"task_id": request.caller.task_id,
```

### WR-02: `read.py` does not forward caller identity at all

**File:** `backend/src/sandbox/api/tool/read.py:13-17`
**Issue:** `read_file` sends only `{"path": request.path}` to the daemon. Every other verb forwards `actor_id` and `description`. The daemon-side `read.read_file` handler does not currently consume `actor_id`, so this is not a runtime defect today, but it is asymmetric with write/edit/shell and makes read calls invisible to any future audit policy. If reads need to be audited (the `SandboxCaller` docstring says "every audit-aware request"), this is a silent gap.
**Fix:** Forward `actor_id` (and `description` if useful) for consistency:
```python
raw = await call_daemon_api(
    sandbox_id,
    "api.read_file",
    {
        "path": request.path,
        "actor_id": request.caller.agent_id,
    },
    timeout=60,
)
```
And update the daemon handler if reads must be audited.

### WR-03: `int_from_payload` raises `ValueError`, not `TypeError`, on malformed strings

**File:** `backend/src/sandbox/api/tool/_payload.py:36-41`
**Issue:** The function declares `raise TypeError(...)` for non-numeric inputs but the type-permissive branch unconditionally calls `int(value)`. If the daemon ever returns `exit_code="oops"` (or any non-numeric string), `int_from_payload` raises an unhandled `ValueError`. This is used in `shell.py:53` to coerce `exit_code` and in `edit.py:35` for `applied_edits`. Both are public-API result fields; a malformed payload should surface as a structured failure, not a crash deep inside the projection helpers.
**Fix:** Catch the `ValueError` and either return the default or raise a uniform error type:
```python
def int_from_payload(value: object, *, default: int) -> int:
    if value is None:
        return default
    if isinstance(value, bool):  # bool is int subclass; usually unintended
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, (str, float)):
        try:
            return int(value)
        except (ValueError, TypeError):
            return default
    raise TypeError(f"expected integer value, got {type(value).__name__}")
```

### WR-04: `paths_from_payload` accepts dicts and reused for warnings with different semantics

**File:** `backend/src/sandbox/api/tool/_payload.py:24-27`, `backend/src/sandbox/api/tool/shell.py:64`
**Issue:** Two issues with one helper:
1. `dict` is an `Iterable` and is not excluded; passing a dict yields its keys, which is almost never the intent. The guard only excludes `str`/`bytes`.
2. `shell.py` reuses this helper for `warnings`, but warnings have different filter semantics than paths — `paths_from_payload` discards any item whose `str(path or "").strip()` is empty. A legitimate warning containing only whitespace (or an empty marker) will silently disappear.

**Fix:** Tighten the helper and introduce a separate one for warnings if needed:
```python
def paths_from_payload(raw: object) -> tuple[str, ...]:
    if not isinstance(raw, (list, tuple)):
        return ()
    return tuple(str(p) for p in raw if str(p or "").strip())

def strings_from_payload(raw: object) -> tuple[str, ...]:
    if not isinstance(raw, (list, tuple)):
        return ()
    return tuple(str(item) for item in raw)
```
Then use `strings_from_payload` for `warnings` in `shell.py:64`.

### WR-05: `conflict_from_payload` accepts arbitrary types for `conflict_file` and stringifies them

**File:** `backend/src/sandbox/api/tool/_payload.py:10-21`
**Issue:** `conflict_file=str(raw.get("conflict_file")) if raw.get("conflict_file") is not None else None`. If the daemon returns a non-string (e.g., a list, dict, or number due to bug), it becomes `"[1, 2]"` or `"{'foo': 'bar'}"` rather than a clear failure. Also, `raw.get("conflict_file")` is called twice; if `raw` were ever an unusual mapping with side-effecting `__getitem__`, behavior would differ — not a real risk for `dict`, but the pattern is duplicated.
**Fix:** Pull the value once and validate type:
```python
conflict_file_raw = raw.get("conflict_file")
conflict_file = str(conflict_file_raw) if isinstance(conflict_file_raw, str) else None
```

### WR-06: `_configured_sandbox_defaults` returns `(snapshot, None)` even when `default_image` is also configured

**File:** `backend/src/sandbox/api/status.py:125-133`
**Issue:** If both `default_snapshot` and `default_image` are set in config, the function returns `(snapshot, None)` — silently dropping the configured image. `get_health` (line 38) then masks this by falling back to `health.get("default_image")`, but `create_sandbox` (line 84-92) uses the same helper and will not pass `image` even if it was configured. This means a config with both set behaves differently in health (image visible) than in creation (image ignored). Surprising behavior, not necessarily wrong.
**Fix:** Either document the precedence rule explicitly or return both:
```python
def _configured_sandbox_defaults() -> tuple[str | None, str | None]:
    from config import load_settings

    sandbox = load_settings().sandbox
    snapshot = sandbox.default_snapshot.strip() or None
    image = sandbox.default_image.strip() or None
    return snapshot, image
```
And update `create_sandbox` to apply whichever precedence is intended.

## Info

### IN-01: `request.timeout or 60` collapses a caller-supplied `timeout=0`

**File:** `backend/src/sandbox/api/tool/shell.py:38`
**Issue:** `timeout=(request.timeout or 60) + 30`. If a caller explicitly passes `timeout=0`, the `or` short-circuits and treats it as unset (uses 60). Probably intentional (0 is a nonsensical timeout) but worth being explicit.
**Fix:** Either reject `timeout=0` upstream or check explicitly: `timeout=(60 if request.timeout is None else request.timeout) + 30`.

### IN-02: `int_from_payload` silently accepts `bool`

**File:** `backend/src/sandbox/api/tool/_payload.py:39`
**Issue:** `bool` is a subclass of `int`, so `isinstance(True, (str, int, float))` is true and `int_from_payload(True, default=0)` returns `1`. If a daemon bug sends `exit_code=True`, the API caller silently sees `exit_code=1`. Low probability.
**Fix:** See WR-03 patch above (explicit `isinstance(value, bool)` branch).

### IN-03: `_overlay_cwd` re-converts an already-string `cwd`

**File:** `backend/src/sandbox/api/tool/shell.py:90-93`
**Issue:** `cwd` is typed `str | None`, but `_overlay_cwd` calls `str(cwd).strip()` and `str(cwd)` anyway. If the type contract is trusted, the `str(...)` wrapping is dead defensiveness. If untrusted (callers may pass `Path` or other), then it works but should be reflected in the type signature. Minor.
**Fix:** Drop the `str(...)` wrappings if the type contract holds:
```python
def _overlay_cwd(cwd: str | None) -> str:
    if cwd is None or not cwd.strip():
        return "."
    return cwd
```

### IN-04: Each `SandboxClient` method does a lazy module-level import

**File:** `backend/src/sandbox/api/facade.py:33-159`
**Issue:** Every method does `from sandbox.api import status` or similar inside the function body. This is presumably to avoid circular imports; once the first call has been made the import is cached, so cost is negligible. However, it duplicates the import line at every method and makes static analysis (IDE jump-to-definition, mypy import resolution) noisier. Not a defect.
**Fix:** Leave as-is unless a circular-import audit confirms top-level imports are safe; otherwise no action.

### IN-05: `api/__init__.py` instantiates a module-level singleton without thread/safety annotation

**File:** `backend/src/sandbox/api/__init__.py:23`
**Issue:** `_client = SandboxClient()` is constructed at import time and methods are bound as module-level callables. `SandboxClient` is stateless, so this is safe. Worth noting only because adding any instance state in the future would silently break this pattern.
**Fix:** None required. Add a comment like `# stateless — safe as a module-level singleton` if desired.

---

_Reviewed: 2026-05-13_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
