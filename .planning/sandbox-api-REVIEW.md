# Code Review: `backend/src/sandbox/api/`

**Scope:** 17 files, 1,188 LOC.
**Criteria:** (1) implementation quality, (2) simplicity / dedup / minimal helpers, (3) import chain ≤ 3 hops.

---

## TL;DR

The package has the right **shape** — a thin public surface over a daemon transport with an audit wrapper — but pays for it twice. `default.py` is a 171-line pass-through layer that adds *nothing* between `__init__.py` and the actual implementations. The `_VerbSpec` / `_run_verb` abstraction (68 LOC) is shorter to read than what it replaces, but only because the verbs have already been *split* into separate files; when you measure end-to-end (verb + spec + dispatcher) it's *more* code than three plain async functions calling `audited_operation` directly. Net achievable reduction without losing any functionality: **~370 LOC (~31%)**, plus one import hop removed from every public call.

Severity ladder used: **HIGH** = clear redundancy, no design rationale; **MEDIUM** = trade-off worth making but not forced; **LOW** = polish.

---

## HIGH

### H1. `default.py` is a pure 171-LOC pass-through layer — delete it

`backend/src/sandbox/api/default.py:32-149` defines 17 functions that **only forward arguments** to `_control` and `_impl/*`. Every function follows one of two shapes:

```python
def start_sandbox(sandbox_id: str) -> dict[str, Any]:
    return control_module.start_sandbox(sandbox_id)

async def shell(sandbox_id, request, *, audit_sink=None) -> ShellResult:
    return await shell_module.shell(sandbox_id, request, audit_sink=audit_sink)
```

Signatures are byte-identical to the target functions. There is no transformation, no default injection, no dependency wiring. `__init__.py` then re-exports these — so a caller’s import chain is:

```
sandbox.api  ->  sandbox.api.default  ->  sandbox.api._impl.shell  ->  daemon_client
   (1)              (2 — empty)              (3)                         (4)
```

That's **4 hops**, violating your "≤ 3" rule, and the middle hop is the empty one.

**Fix.** Delete `default.py`. In `__init__.py`:

```python
from sandbox.api._control import (
    create_sandbox, start_sandbox, stop_sandbox, delete_sandbox,
    ensure_sandbox_running, set_sandbox_labels, get_sandbox,
    list_sandboxes, list_snapshots, get_health,
    get_signed_preview_url, get_build_logs_url,
)
from sandbox.api._impl.shell import shell
from sandbox.api._impl.raw_exec import raw_exec
from sandbox.api._impl.read import read_file
from sandbox.api._impl.write import write_file
from sandbox.api._impl.edit import edit_file
from sandbox.host.context_preparer import context_preparer_for
```

Note `context_preparer_for` was re-exported with return type `Any` — `default.py:95-96` loses the upstream type for no reason. Direct import preserves it.

**Saves:** 171 LOC, one import hop, one `__all__` duplicate, and the `if TYPE_CHECKING` block (which exists only because of the wrapper indirection).

---

### H2. `_VerbSpec` + `_run_verb` (68 LOC) is *more* code than the inlined alternative

`backend/src/sandbox/api/_impl/_run_verb.py:17-65` introduces a `_VerbSpec` frozen dataclass with seven fields (one optional) and a `_run_verb` dispatcher. Three verbs use it: `read.py` (40 LOC), `write.py` (48 LOC), `edit.py` (61 LOC).

End-to-end accounting:

| Approach                           | LOC |
|------------------------------------|----:|
| `_run_verb.py` + read + write + edit | 217 |
| Three inlined verbs (no spec)      | ~95 |

Inlined `read_file` would look like:

```python
async def read_file(sandbox_id, request, *, audit_sink=None, transport=None):
    selected = transport or DaemonSandboxTransport()

    async def _call():
        raw = await selected.call(
            sandbox_id, DAEMON_OP_READ_FILE,
            {"path": request.path, "caller": request.caller.audit_fields()},
            timeout=READ_FILE_TIMEOUT_S,
        )
        return read_result_from_payload(raw)

    return await audited_operation(
        audit_sink=audit_sink, sandbox_id=sandbox_id, operation="read_file",
        caller=request.caller, payload={"path": request.path}, call=_call,
    )
```

That's ~15 LOC, directly readable, no dispatch table to chase. **The fact that `shell.py` does not use `_VerbSpec` is the strongest signal**: as soon as one branch (stdin pre-check, dispatch-grace timeout) doesn't fit the spec shape, the abstraction breaks and you write the inline form anyway. Three call sites + one exception is not a population that justifies an extension point.

Also: `__all__ = ["_VerbSpec", "_run_verb"]` (`_run_verb.py:68`) — underscore-prefixed names exported in `__all__` is mixed signaling. Either it's private (don't export) or public (rename).

**Fix.** Delete `_run_verb.py`. Inline the dispatch into `read.py`, `write.py`, `edit.py`. Audit and timeout policy still live in `_audit.py` / `timeouts.py`.

**Saves:** ~120 LOC across the three verb files + 68 LOC for `_run_verb.py` = ~120 net (after rewriting the verbs as ~30 LOC each).

---

### H3. `_results.py` per-verb wrappers are one-line shells — drop them

`_results.py:63-72` defines:

```python
def write_result_from_payload(raw):
    return guarded_result_from_payload(WriteFileResult, raw)

def edit_result_from_payload(raw):
    return guarded_result_from_payload(
        EditFileResult, raw,
        applied_edits=int_from_payload(raw.get("applied_edits"), default=0),
    )
```

Each has exactly **one call site** (their corresponding verb). Either inline them at the call sites, or have the verb call `guarded_result_from_payload(WriteFileResult, raw)` directly. Same for `shell_result_from_payload` (`_results.py:75-88`) which is also called from exactly one place.

The named wrappers were probably there to satisfy `_VerbSpec.result_decoder: Callable[...]`. Once H2 lands, they have no caller asking for `Callable`-shaped values and can collapse.

**Saves:** ~20 LOC.

---

## MEDIUM

### M1. `shell.py` stdin-rejection branch hand-rolls audit pub/sub it could borrow

`shell.py:36-56` does manual `publish_operation_started` + `publish_operation_result` for the stdin-reject case, then falls through to `audited_operation` for the happy path. Two pub/sub code paths to maintain.

The whole function can route through `audited_operation`:

```python
async def _call():
    if request.stdin is not None:
        return shell_error_result(
            reason="stdin_not_supported",
            message="snapshot overlay shell does not accept stdin",
            timings={"api.shell.total_s": monotonic_now() - total_start},
        )
    raw = await selected_transport.call(...)
    ...
```

`audited_operation` already publishes started→result around any `_call`. One code path, ~15 LOC removed, no behavior change (stdin reject still fires both events, in the same order).

---

### M2. `_payload.int_from_payload` inconsistent with siblings

`_payload.py:72-79` raises `TypeError` on unexpected types. Neighboring decoders (`paths_from_payload:60-63`, `timings_from_payload:66-69`, `conflict_from_payload:45-57`) silently fall back to `()`, `{}`, `None`. Mixed defensive posture in the same module.

This matters for `edit_result_from_payload` (`_results.py:71`) — a daemon shipping a malformed `applied_edits` crashes the public verb instead of degrading to `0`. If that's intentional, document it. If not, mirror the other helpers (`return default` on unknown type).

---

### M3. `_classifiers.py` string-marker fallback is the wrong abstraction

`_classifiers.py:9-29` keeps two parallel data structures: a `set` of error codes and a `tuple` of substring markers (`"anchor not found"`, `"unsupported tracked change kind: symlinkchange"`, ...). Substring matching against translated error messages is brittle — any rephrasing on the daemon side silently breaks classification.

If the daemon emits structured `error_code`, drop the marker fallback. If markers exist because some legacy code path lacks codes, file the gap and remove the fallback once closed. As-is, the helper is 61 LOC for something that should be `code in CONFLICT_CODES`.

---

### M4. `__init__.py` model re-exports are a backward-compat seam — annotate it

Lines 9-25 re-export 14 model symbols from `sandbox.models`. The module docstring says this preserves the public import path. That's fine, but the seam is not localized:

- New models added to `sandbox.models` need a parallel entry here.
- Removing this re-export is a public-API change that needs deprecation.

Two cheap improvements:
1. Add `# Backward-compat re-exports — prefer sandbox.models` as a single-line comment above the `from sandbox.models import (...)` block.
2. Consider `from sandbox.models import *` + `__all__` extension if `sandbox.models.__all__` is stable.

If maintaining 14 hand-listed names is fine, skip this — but acknowledge in the docstring that the listing is the contract.

---

## LOW

### L1. `default.py` TYPE_CHECKING block is dead weight (subsumed by H1)

`default.py:17-29` runs `if TYPE_CHECKING` only because the wrappers want types without runtime imports. Direct imports in `__init__.py` (per H1) remove the need.

### L2. `_audit.py` audited_operation drops the conflict-from-error result without publishing failure

`_audit.py:41-52`: when `conflict_from_error(exc)` returns a result, the function publishes `operation_result` (good) and **silently swallows** the original exception. That's intentional — the conflict result is the public contract — but a one-line comment would make the intent obvious: `# Treat as recoverable: publish result, suppress exception.`

### L3. `_results.py` conflict constructors duplicate model boilerplate

`edit_conflict_result` (`_results.py:91-100`) and `shell_conflict_result` (`_results.py:103-119`) hard-code five identical "no data, success=False" fields. If the model dataclasses gain a `.rejected_default()` or `.failed_default()` classmethod, these helpers become two lines each. Pure stylistic — keep if the models intentionally have no defaults.

### L4. `__init__.py` `__all__` (40 entries) is a maintenance burden

Three places now list the same public surface: this `__all__`, `default.py`'s `__all__`, and `_control.py`'s `__all__`. After H1 lands, `default.py`'s drops; consider whether `_control.py` needs its own `__all__` at all if the canonical surface is `sandbox.api.__init__`.

### L5. `shell.py` and `raw_exec.py` use `cwd or ""` vs `normalize_overlay_cwd` inconsistently

`shell.py:35` runs `cwd` through `normalize_overlay_cwd` (returns `"."` for blank). `raw_exec.py:34` uses `cwd or ""` in the audit payload. Different defaults for "no cwd" between two verbs in the same package. Pick one. (Likely the raw exec path doesn't care because it's unguarded — confirm and document.)

---

## Import-chain audit (criterion 3)

Counting hops for representative public calls (caller → ... → daemon):

| Verb            | Current hops                                              | After H1 |
|-----------------|-----------------------------------------------------------|---------:|
| `shell`         | `__init__` → `default` → `_impl/shell` → `daemon_client`  | 3 |
| `read_file`     | `__init__` → `default` → `_impl/read` → `_run_verb` → `daemon_client` | 4 → 3 (also H2) |
| `create_sandbox`| `__init__` → `default` → `_control` → `host.lifecycle`    | 3 |

`__init__` → `default` is always one wasted hop. H1 alone brings every verb to ≤ 3. H2 keeps `read/write/edit` at 3 once `_run_verb` is removed.

Cross-package imports inside `_impl/shell.py` (lines 5-23) count to **8 different modules**. That's a horizontal-fanout problem, not a chain-length problem, and it reflects real coupling (audit, models, timing, transport, classifiers, payload helpers, results). Don't flatten by re-exporting — the fanout is honest. If you want fewer top-level lines, group: `from sandbox.api._impl import _audit, _classifiers, _payload, _results` — but I'd leave it explicit.

---

## Summary of reductions if all HIGH items land

| File              | Before | After  | Δ      |
|-------------------|-------:|-------:|-------:|
| `default.py`      |   171  |     0  |  -171  |
| `__init__.py`     |    81  |    55  |   -26  |
| `_run_verb.py`    |    68  |     0  |   -68  |
| `read.py`         |    40  |    28  |   -12  |
| `write.py`        |    48  |    33  |   -15  |
| `edit.py`         |    61  |    45  |   -16  |
| `_results.py`     |   151  |   128  |   -23  |
| `shell.py` (M1)   |   102  |    85  |   -17  |
| **Total**         | **722** | **374** | **-348** |

Untouched files (`_control.py`, `_audit.py`, `_payload.py`, `_classifiers.py`, `protocol.py`, `timeouts.py`, `transport.py`, `raw_exec.py`, `_impl/__init__.py`) account for 466 LOC and stay as-is.

**1,188 LOC → ~840 LOC. ~29% reduction. Zero functional change. One import hop removed from every public call.**

---

## What I'd ship first

1. **H1 (delete `default.py`)** — pure win, ~3-hour change including test sweep.
2. **H2 (inline `_VerbSpec`)** — slightly larger blast radius, but mechanical; do it in the same PR if test coverage is solid, otherwise queue separately.
3. **M1 (route stdin reject through `audited_operation`)** — small, isolated to `shell.py`.
4. **H3, M2, M3** — opportunistic cleanups; bundle with the touch from H2.

Skip M4 / L4 unless you’re already churning `__init__.py` for H1.
