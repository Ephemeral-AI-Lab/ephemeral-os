---
phase: sandbox/plugin
reviewed: 2026-05-13T00:00:00Z
depth: standard
files_reviewed: 8
files_reviewed_list:
  - backend/src/sandbox/plugin/__init__.py
  - backend/src/sandbox/plugin/handler.py
  - backend/src/sandbox/plugin/install.py
  - backend/src/sandbox/plugin/projection.py
  - backend/src/sandbox/plugin/session.py
  - backend/src/sandbox/plugin/runtime/__init__.py
  - backend/src/sandbox/plugin/runtime/context.py
  - backend/src/sandbox/plugin/runtime/registry.py
findings:
  critical: 1
  warning: 8
  info: 4
  total: 13
status: issues_found
---

# Sandbox Plugin Subsystem: Code Review Report

**Reviewed:** 2026-05-13
**Depth:** standard
**Files Reviewed:** 8
**Status:** issues_found

## Summary

The plugin subsystem implements the host-to-sandbox plugin lifecycle: a host-side `call_plugin` entry point (session.py), a tarball-based installer (install.py), an in-sandbox handler that imports plugin runtimes and registers ops (handler.py), a workspace-projection abstraction (projection.py), and an in-sandbox registry/context for plugin op handlers (runtime/registry.py, runtime/context.py).

The architecture is sound and the host/guest trust boundary is mostly well-respected (no `pickle`/`eval`, no `os.system`, no string-interpolated shell). Tar building uses `relative_to(source_dir)` so zip-slip is not exploitable from manifest inputs. However, a number of state-management defects exist around plugin reload, multi-process concurrency, cache invalidation, and RPC payload trust:

- One BLOCKER: `plugin_ensure` mutates `_LOADED`/`_LOADED_DIGEST` *before* awaiting `_warm_plugin_runtime`, so a failed warm leaves the registry permanently half-initialized — subsequent calls take the "already loaded" branch and re-fail forever. The user memory specifically calls out recent stabilization of LSP plugin provisioning, and this exact failure mode survives that work.
- Multiple WARNINGS around process-local caches that become stale or race in multi-worker / multi-call scenarios, and one untrusted-input WARNING on the RPC response boundary.

## Blocker Issues

### BL-01: `plugin_ensure` registers digest before warm hook runs — failed warm wedges the plugin

**File:** `backend/src/sandbox/plugin/handler.py:96-116`
**Issue:**
After `flush_plugin_registrations` succeeds, the handler unconditionally writes:

```python
_LOADED[plugin_name] = registered_ops      # line 96
_LOADED_DIGEST[plugin_name] = digest        # line 97
warm_result = (
    await _warm_plugin_runtime(plugin_name, args)   # line 99 — may raise
    if runtime_loaded
    else {"runtime_warmed": False}
)
```

`_warm_plugin_runtime` raises `PluginEnsureError` on any exception in the plugin's `warm_plugin_runtime` (handler.py:157-160). When that happens, the function unwinds without rolling back `_LOADED`/`_LOADED_DIGEST`. The next call with the same digest hits the "already loaded" branch at line 59-72, which **also** awaits warm:

```python
if (plugin_name in _LOADED
    and (not digest or _LOADED_DIGEST.get(plugin_name) == digest)):
    warm_result = await _warm_plugin_runtime(plugin_name, args)   # line 63
```

So a transient warm failure (e.g., the LSP server's first-time spawn racing with the daemon socket, which the user memory says was recently stabilized) becomes permanent — the only escape is host-side process restart or a digest change. The host-side `_runtime_loaded` cache in session.py:107 is *also* sticky after a successful install but a failed ensure, so calls won't even hit `api.plugin.ensure` again unless the digest changes.

**Fix:** Move the registry writes to *after* warm succeeds, and roll back the dispatcher registrations on warm failure:

```python
try:
    warm_result = (
        await _warm_plugin_runtime_on_module(plugin_name, args)
        if runtime_loaded
        else {"runtime_warmed": False}
    )
except Exception:
    # Roll back the dispatcher entries we just registered.
    from sandbox.runtime.daemon.rpc.dispatcher import OP_TABLE
    for op in registered_ops:
        OP_TABLE.pop(op, None)
    clear_plugin_registrations(plugin_name)
    raise
_LOADED[plugin_name] = registered_ops
_LOADED_DIGEST[plugin_name] = digest
```

And ensure session.py only sets `_runtime_loaded[(sandbox_id, plugin)] = digest` after `api.plugin.ensure` *and* warm succeed (the current code at session.py:125 already gates on dispatch_fn returning normally, which is correct *if* the daemon-side propagates the warm failure as a dispatch exception — verify this with a test).

## Warnings

### WR-01: Concurrent `plugin_ensure` calls can race when digests differ

**File:** `backend/src/sandbox/plugin/handler.py:53-116`
**Issue:** No lock guards `plugin_ensure`. The host-side `_call_locks` in session.py:49 is per-process and per-`(sandbox_id, plugin)`, so multiple host processes (uvicorn workers, batch jobs) hitting the same sandbox aren't serialized. Trace:

1. T1 enters with `digest=A`, takes the "not loaded" path, completes flush, writes `_LOADED[plugin]=ops_A`, starts awaiting warm.
2. T2 enters with `digest=B`, sees `_LOADED_DIGEST[plugin]=A != B`, takes the `_unload_plugin_runtime` branch (line 74), which pops `OP_TABLE` entries that T1 just registered.
3. T1's warm completes, returns `success: True, registered_ops: [ops_A]` — but the dispatcher no longer has them.

Even within a single process, `plugin_ensure` is async and the import/flush/warm steps are full of awaits; multiple in-flight calls with different digests can interleave at every `await` boundary.

**Fix:** Wrap the body of `plugin_ensure` in an `asyncio.Lock` keyed by `plugin_name`:

```python
_PLUGIN_LOCKS: dict[str, asyncio.Lock] = {}

async def plugin_ensure(args: dict[str, Any]) -> dict[str, Any]:
    plugin_name = str(args.get("plugin") or "").strip()
    if not plugin_name:
        raise PluginEnsureError("api.plugin.ensure requires plugin name")
    lock = _PLUGIN_LOCKS.setdefault(plugin_name, asyncio.Lock())
    async with lock:
        ...  # existing body
```

### WR-02: `_wrap_response` does not bound sandbox-controlled payload size

**File:** `backend/src/sandbox/plugin/session.py:159-188`
**Issue:** The response from `dispatch_fn` is sandbox-controlled (the trust boundary the task calls out). `_wrap_response` accepts any mapping and `json.dumps`-es it without a size cap (line 183). A malicious or runaway plugin can return a multi-gigabyte string and OOM the host. There is also no validation that nested values are JSON-safe — `default=str` (line 183) silently converts arbitrary objects to their `repr`, which could mask defects in plugin authors' code.

**Fix:** Cap response size before serialization and reject oversize payloads with a structured error:

```python
_MAX_RESPONSE_BYTES = 8 * 1024 * 1024  # 8 MiB

def _wrap_response(response, *, plugin, op):
    if not isinstance(response, Mapping):
        return _error_result("decode", plugin, op, ...)
    payload_dict = {k: v for k, v in response.items() if k != "timings"}
    output = json.dumps(payload_dict, sort_keys=True, default=str)
    if len(output) > _MAX_RESPONSE_BYTES:
        return _error_result(
            "decode", plugin, op,
            f"plugin response exceeds {_MAX_RESPONSE_BYTES} bytes "
            f"({len(output)} bytes)",
        )
    ...
```

Also strongly consider dropping `default=str` and surfacing non-JSON values as an explicit dispatch error — silent coercion of e.g. file handles to their `repr` is exactly the kind of host/guest cross-contamination the boundary is supposed to prevent.

### WR-03: `_installed_marker_cache` never invalidates — survives sandbox lifecycle changes

**File:** `backend/src/sandbox/plugin/install.py:72-110`
**Issue:** `_installed_marker_cache` is keyed by `(sandbox_id, plugin_name)` and is only ever written, never cleared. If a sandbox is destroyed/recreated, snapshot-restored to before install, or its `/tmp/eos-sandbox-runtime/...` is wiped, the host still believes the plugin is installed and skips the upload (install.py:97-98). Every subsequent op-call hits "module not found" inside the sandbox.

The cache is also redundant with the on-disk marker check (line 99) — the only thing the in-memory cache buys you is skipping one cheap `test -f` exec.

**Fix:** Either drop the in-memory cache (the on-disk marker check is the source of truth) or invalidate it on any sandbox-lifecycle event. Drop is simpler and matches the project's "Simplicity First" CLAUDE.md guideline:

```python
async def ensure_installed(...):
    key = (sandbox_id, manifest.name)
    lock = _locks.setdefault(key, asyncio.Lock())
    async with lock:
        executor = exec_fn or get_adapter(sandbox_id).exec
        digest = _bundle_hash(manifest)
        if await _marker_present(executor, sandbox_id, manifest.name, digest):
            return digest
        await _upload_and_run_setup(...)
        return digest
```

If keeping the cache for cold-start latency reasons, expose a `forget(sandbox_id)` hook and call it from the sandbox-destruction path.

### WR-04: Multi-process workers race on plugin install — no cross-process lock

**File:** `backend/src/sandbox/plugin/install.py:72, 92-110`
**Issue:** `_locks` is a process-local dict. With multiple uvicorn workers, two workers calling `ensure_installed(sandbox_id, manifest)` for the same sandbox simultaneously both pass the marker check (it doesn't exist yet) and both execute the `rm -rf install_dir && mkdir -p install_dir` + tar-extract sequence (install.py:208-240). They race on the tar contents — one worker can `rm -rf` mid-extract of the other, producing a corrupted bundle. The marker is written at the end (line 260-265), so subsequent calls see "installed" and skip — but the on-disk layout is wrong and op imports fail later in obscure ways (`ModuleNotFoundError` for half-written files).

**Fix:** Either (a) make `_upload_and_run_setup` write into a temp directory and `mv` atomically to `install_dir` at the end, so concurrent extracts don't trample each other, or (b) acquire a sandbox-side lock (`mkdir -p <install_dir>.lock`) before the destructive sequence. Option (a) is preferable since it also makes partial-failure recovery cleaner.

### WR-05: `_PENDING` registrations never consumed by flush — `plugin_status` reports stale pending

**File:** `backend/src/sandbox/plugin/runtime/registry.py:129-162, 106-116`
**Issue:** `flush_plugin_registrations` iterates `_filter_pending(plugin_name)` and registers each with the dispatcher, but never removes the entry from `_PENDING`. The only callsite that clears entries is `clear_plugin_registrations` (registry.py:119), which `handler.py:_unload_plugin_runtime` calls only on unload. Result: after a successful `plugin.ensure`, `plugin_status` (handler.py:127-133) reports every registered op as still "pending" — directly contradicting the field name and confusing operators and tests.

**Fix:** Pop entries from `_PENDING` after the dispatcher accepts them:

```python
def flush_plugin_registrations(plugin_name, dispatcher_register_op, *, context_factory=None):
    ...
    registered: list[str] = []
    for entry in list(_filter_pending(plugin_name)):
        public_op = f"plugin.{entry.plugin_name}.{entry.op_name}"
        ...
        dispatcher_register_op(public_op, handler)
        _PENDING.pop((entry.plugin_name, entry.op_name), None)
        registered.append(public_op)
    return registered
```

This also fixes the (mild) memory leak where module reloads with `clear_plugin_registrations` followed by `importlib.import_module` re-fill `_PENDING` indefinitely.

### WR-06: Unbounded host-side caches per `(sandbox_id, plugin)`

**File:** `backend/src/sandbox/plugin/session.py:48-49`, `backend/src/sandbox/plugin/install.py:72-73`
**Issue:** `_runtime_loaded`, `_call_locks`, `_locks`, `_installed_marker_cache` are all module-level dicts keyed by sandbox-id (or `(sandbox_id, plugin)`). Nothing in the plugin subsystem evicts entries when a sandbox is destroyed. Long-running host processes accumulate one set of entries per sandbox ever created — a memory leak proportional to total sandbox count, not concurrent sandboxes.

The asyncio Lock objects in `_call_locks` and `_locks` are particularly wasteful (each holds references to a waiter deque, internal loop refs). At thousands of sandboxes/day this is real RSS growth.

**Fix:** Either (a) expose a `forget_sandbox(sandbox_id)` API that the sandbox-lifecycle layer calls on destruction, evicting from all four dicts, or (b) bound caches with `functools.lru_cache` semantics on a wrapper. (a) is cleaner because lock semantics break under LRU eviction.

Group with WR-03 (install marker cache) for one coordinated cleanup pass.

### WR-07: `_PROJECTIONS` cache keyed by string never evicts and trusts caller-supplied path

**File:** `backend/src/sandbox/plugin/handler.py:50, 234-237`
**Issue:** Two problems with `_PROJECTIONS`:

1. Keyed by raw `layer_stack_root` string from the RPC envelope. The host's `call_plugin` normalizes via `DEFAULT_LAYER_STACK_ROOT` (session.py:81-84), but a buggy or malicious in-sandbox caller (or a future op that reuses the dispatcher) can pass `"/tmp/../tmp/..."` or `""` and get a `WorkspaceProjection` pointing somewhere unexpected. `WorkspaceProjection.__init__` calls `Path(layer_stack_root).resolve()` (projection.py:74), which silently normalizes — and `Path("").resolve()` resolves to the current working directory, which is not what a caller of `api.plugin.<op>` likely wants.
2. The cache is never evicted; each distinct path string creates a new `LayerStackManager` whose `_lock`, `_leases`, `_squash` etc. live forever. Combined with WR-06, this leaks lease-manager state indefinitely.

**Fix:** Validate and normalize `layer_stack_root` before caching:

```python
layer_stack_root = str(args.get("layer_stack_root", "")).strip()
if not layer_stack_root:
    raise PluginEnsureError("layer_stack_root required for plugin op")
canonical = str(Path(layer_stack_root).resolve())
projection = _PROJECTIONS.get(canonical) or WorkspaceProjection(canonical)
_PROJECTIONS[canonical] = projection
```

And gate against an allowlist (or at least the configured `DEFAULT_LAYER_STACK_ROOT`) if the daemon's threat model treats sandbox-side callers as untrusted — which the review brief explicitly states.

### WR-08: `register_plugin_op` namespace check is bypassable when invoked via wrapper

**File:** `backend/src/sandbox/plugin/runtime/registry.py:77-84, 187-208`
**Issue:** The decorator enforces "only modules under `plugins.catalog.<name>.` may register ops" by walking up the call stack with `inspect.stack` (the `_caller_module_name` helper). The walk is fragile:

- It checks up to 8 frames (line 199), skipping any frame whose `__name__` matches `registry`. But it returns the *first* non-registry frame's `__name__`, not the first frame at the right depth. If a plugin author wraps `register_plugin_op` with a decorator factory (the obvious Python idiom for adding metadata), the immediate caller is the factory module — which is *not* under `plugins.catalog.<name>.*` — and registration is rejected with a confusing error message.
- More importantly, the check enforces no real security property. Anyone with code-execution inside the sandbox daemon can `sys.modules['plugins.catalog.lsp.runtime.evil'] = my_module` and call from there. The check catches honest mistakes only.

**Fix:** Either accept this is a hint, not a security boundary, and lower it to a `warnings.warn` (since the trust model already assumes sandbox-side code is run); or do the check based on the registered handler's `__module__` (which is more robust than frame walking) rather than the caller frame.

### WR-09: `flush_plugin_registrations` doesn't validate that `plugin_name` matches caller frame

**File:** `backend/src/sandbox/plugin/runtime/registry.py:129-148`
**Issue:** While `register_plugin_op` enforces the `plugins.catalog.<plugin_name>.*` rule (registry.py:77-84), `flush_plugin_registrations` is called from `handler.plugin_ensure` with whatever plugin name came in via the RPC args (handler.py:54). The handler does no validation that `plugin_name` is in the catalog. A caller can `api.plugin.ensure {"plugin": "../etc/passwd"}` and the `importlib.import_module(f"plugins.catalog.{plugin_name}.runtime.server")` call (handler.py:79) will attempt the import.

`importlib.import_module` with a dotted path won't traverse filesystem `..` (it tokenizes on `.`), so this is *probably* not exploitable as path traversal. But it is uncontrolled string interpolation into an import statement, which depending on the in-sandbox sys.path could resolve to attacker-controlled modules. At minimum it should reject names not matching `^[a-z][a-z0-9_]*$`.

**Fix:** Validate `plugin_name` in `plugin_ensure` early:

```python
import re
_PLUGIN_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")

async def plugin_ensure(args):
    plugin_name = str(args.get("plugin") or "").strip()
    if not plugin_name or not _PLUGIN_NAME_RE.match(plugin_name):
        raise PluginEnsureError(f"invalid plugin name: {plugin_name!r}")
    ...
```

## Info

### IN-01: `default=str` in `_wrap_response` silently masks non-JSON values

**File:** `backend/src/sandbox/plugin/session.py:183`
**Issue:** `json.dumps(payload_dict, sort_keys=True, default=str)` silently `repr`-s anything non-JSON. If a plugin returns a `pathlib.Path`, a `datetime`, or a `bytes`, the host sees its string representation, not structured data. The dispatcher already serializes responses to JSON before they reach this code, so in normal operation this branch is unreachable.

**Fix:** Either remove `default=str` (let `TypeError` propagate to the caller for visibility) or document that responses must be JSON-pre-serialized by the dispatcher and this is a defensive only-on-bug fallback.

### IN-02: Weak validation of caller-supplied audit fields

**File:** `backend/src/sandbox/plugin/handler.py:225-231`
**Issue:** `str(caller_dict.get("agent_id", ""))` accepts anything castable to string, including a `list` or `dict`, which produces a Python `repr` like `"['foo']"` that ends up in audit fields. This won't crash but pollutes logs and metrics with malformed identifiers.

**Fix:** Validate each field is a string explicitly:

```python
def _str_field(d: dict, key: str) -> str:
    val = d.get(key, "")
    return val if isinstance(val, str) else ""

caller = SandboxCaller(
    agent_id=_str_field(caller_dict, "agent_id"),
    ...
)
```

### IN-03: `importlib.invalidate_caches()` may not be sufficient for clean reload

**File:** `backend/src/sandbox/plugin/handler.py:177-184`
**Issue:** `_unload_plugin_runtime` pops `sys.modules` entries matching `plugins.catalog.<plugin_name>` and calls `importlib.invalidate_caches()`. However, any module that the plugin's `runtime.server` imported (e.g., a vendored dependency) is *not* removed — it stays cached in `sys.modules`, and on re-import won't pick up changes from a new digest. If a plugin ships a private vendored module that changed between digests, the in-sandbox runtime sees stale code.

The current logic is correct for the common case (single `runtime.server` module with no private vendored libs); flagging as INFO because this is a latent foot-gun for plugin authors, not a current bug.

**Fix:** Document in the architecture doc that plugins must not rely on sys.modules-level reload of their dependencies, or expand the prefix filter to include a plugin-author-declared module list from the manifest.

### IN-04: `_caller_module_name` 8-frame walk is a magic number

**File:** `backend/src/sandbox/plugin/runtime/registry.py:199`
**Issue:** `for _ in range(8)` — why 8? No comment explains the bound. If a plugin author uses a chain of decorators, this silently fails with "called from ''" which surfaces as a confusing `PluginOpRegistrationError`.

**Fix:** Replace the fixed-depth walk with an unconditional `while frame: ... frame = frame.f_back` loop. Frame walking already terminates at `None`, the bound buys nothing.

---

_Reviewed: 2026-05-13_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
