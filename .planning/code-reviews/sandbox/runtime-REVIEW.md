---
status: issues_found
depth: standard
files_reviewed: 25
findings:
  blocker: 5
  warning: 8
  info: 0
  total: 13
subsystem: runtime
---

# Sandbox Runtime: Code Review Report

**Reviewed:** 2026-05-13
**Depth:** standard

## Files Reviewed

25 files under `backend/src/sandbox/runtime/` including `daemon/rpc/{dispatcher,server}.py`, `daemon/handler/**`, `daemon/handler/tools/{edit,read,shell,write}.py`, `daemon/service/{layer_stack_client,occ_backend,shell_runner,workspace_binding,workspace_server}.py`, and `async_bridge.py`.

## Summary

The runtime daemon is the in-sandbox trust boundary: an AF_UNIX server that accepts JSON envelopes from the host and executes them against the workspace filesystem and shell. Review found five **BLOCKER**-class issues concentrated in the RPC layer (silent message-size failure, no read timeout, TOCTOU on socket permissions, traceback disclosure in error envelopes) and the tool handlers (truthy-coalesce bug in `edit_file` that silently rewrites `expected_occurrences=0` to `1`, breaking the anchor-count contract). Eight **WARNING**-class issues mostly concern unbounded reads of host-FS paths, fragile cleanup semantics (`rmtree(lowerdir.parent)`), missing env-injection validation, and dead/awkward error-handling code.

The biggest systemic concern is that the dispatcher assumes "trusted host" but offers no defense-in-depth: no message-size cap, no auth handshake, no rate limit, no payload-size cap on out-of-workspace file reads. Any of these — exercised by a buggy host caller, not a malicious one — degrade safely-to-failure into silent connection drops or daemon OOM.

---

## BLOCKER Issues

### BL-01: Unbounded request payload silently drops the connection with no response

**File:** `backend/src/sandbox/runtime/daemon/rpc/server.py:57` (also `:101`)

`await reader.readline()` runs against the default `StreamReader` buffer (64 KiB) created by `asyncio.start_unix_server` at line 130. Any request whose JSON envelope exceeds 64 KiB — trivially reachable via `api.write_file` with a moderately large `content` field, or `api.edit_file` with multi-anchor edits — raises `asyncio.LimitOverrunError`. That exception falls into the broad `except Exception` at line 101–102, which only calls `logger.exception(...)` and closes the writer in `finally`. **No error envelope is written to the client.** The caller sees a closed socket / empty body and cannot distinguish a transient I/O failure from a payload too large to dispatch.

**Fix:** Size the buffer explicitly with `limit=MAX_REQUEST_BYTES` on `start_unix_server`, convert `LimitOverrunError` to a structured `request_too_large` envelope, and wrap `readline` in `asyncio.wait_for`.

---

### BL-02: No read timeout — slow/half-open clients pin connection tasks indefinitely

**File:** `backend/src/sandbox/runtime/daemon/rpc/server.py:57`

`await reader.readline()` has no timeout. A client that opens the socket and never sends `\n` keeps `_handle_connection` blocked forever. Since `start_unix_server` accepts unbounded concurrent connections, a slow-loris pattern from a buggy host or co-resident sandbox process can exhaust the daemon's task scheduler.

**Fix:** `asyncio.wait_for(reader.readline(), timeout=REQUEST_READ_TIMEOUT_S)` and return early on `TimeoutError`. Add a connection-acceptance semaphore in `serve()`.

---

### BL-03: TOCTOU on socket permissions — socket is world-accessible before `chmod 0o600`

**File:** `backend/src/sandbox/runtime/daemon/rpc/server.py:128-132`

```python
server = await asyncio.start_unix_server(_handle_connection, path=str(socket_path))
with contextlib.suppress(OSError):
    os.chmod(socket_path, 0o600)
```

`start_unix_server` calls `bind()` before `os.chmod`. Between bind and chmod the socket is on disk with permissions controlled by the process umask (typically `0o755`). Any local process can connect during that window. `OSError` from chmod is silently suppressed.

**Fix:** Set `os.umask(0o077)` around the bind, chmod the parent directory to `0o700`, do not suppress chmod failures.

---

### BL-04: Full Python traceback leaks in every error envelope

**File:** `backend/src/sandbox/runtime/daemon/rpc/dispatcher.py:73-78`

```python
except Exception as exc:
    return _error(
        "internal_error", str(exc),
        {"op": op, "traceback": traceback.format_exc()},
    )
```

Every uncaught handler exception ships the full traceback (file paths, line numbers, repr'd args) back to the caller. Defense-in-depth: don't put internal structure on the wire.

**Fix:** Log the traceback server-side, return only `str(exc)` and a correlation `error_id`.

---

### BL-05: `expected_occurrences=0` is silently rewritten to `1` — anchor contract broken

**File:** `backend/src/sandbox/runtime/daemon/handler/tools/edit.py:41`

```python
expected = int(edit.get("expected_occurrences") or 1)
```

`0 or 1 == 1`. A caller asking "this edit must hit zero occurrences" silently becomes "exactly one." Downstream `if found != expected:` then changes behavior — files that should reject because the anchor is absent will instead either succeed (1 match, replaced) or reject for the wrong reason.

**Fix:**
```python
raw = edit.get("expected_occurrences")
expected = 1 if raw is None else int(raw)
if expected < 0:
    raise ValueError("expected_occurrences must be >= 0")
```

---

## WARNINGS

### WR-01: Unbounded host-FS read in out-of-workspace branches

**File:** `backend/src/sandbox/runtime/daemon/handler/tools/read.py:93`, `edit.py:153`

`target.read_text(encoding="utf-8")` / `target.read_bytes()` are unbounded. Reading `/var/log/syslog` or `/proc/kcore` OOMs the daemon and blows up the response size. Add a hard cap (~16 MiB) and return `file_too_large`.

---

### WR-02: TOCTOU between classify and write (symlink race) on out-of-workspace paths

**File:** `backend/src/sandbox/runtime/daemon/handler/request_context.py:89` + `tools/write.py:181`, `edit.py:175`

`classify_path` resolves symlinks once via `os.path.realpath`. The handler then calls `Path(abs_path).write_text(...)`, which re-opens and re-follows. A race can redirect writes into unintended targets. Use `os.open(... O_NOFOLLOW ...)` or `openat` semantics at I/O time.

---

### WR-03: `_drop_transient_lowerdir` blindly rmtrees the *parent* of a lease-provided path

**File:** `backend/src/sandbox/runtime/daemon/service/shell_runner.py:293-298`

```python
lowerdir = Path(raw)
shutil.rmtree(lowerdir.parent, ignore_errors=True)
```

Removes the parent directory of a lease path; if the contract drifts and lowerdir points anywhere real, this rmtrees too much. `ignore_errors=True` masks the damage.

**Fix:** Verify `lowerdir.parent.is_relative_to(EXPECTED_SCRATCH_ROOT)` before unlinking.

---

### WR-04: Env injection unchecked — NUL bytes and `=` in names not rejected

**File:** `backend/src/sandbox/runtime/daemon/service/shell_runner.py:259`

`env={str(k): str(v) for k, v in _mapping(args.get("env")).items()}` forwards keys containing `=` or NUL silently. `execvpe` will raise on NUL (surfacing as the BL-04 traceback leak); `=` in a key silently corrupts the child env. Reject explicitly with a structured `invalid_env` error.

---

### WR-05: `fence_stale_staging` rmtrees concurrent daemons' staging on restart races

**File:** `backend/src/sandbox/runtime/daemon/service/workspace_server.py:47-66` (uses `_DAEMON_STARTED_AT` at line 26)

Deletes any staging dir whose `mtime < _DAEMON_STARTED_AT`. If two daemons run concurrently against the same layer-stack root (restart race, supervised double-start), the newer rmtrees the older's in-flight staging. Add a PID-lockfile or flock on `pid_path` in `__main__.py`.

---

### WR-06: `WorkspaceBinding` re-validation duplicated between two callers

**File:** `backend/src/sandbox/runtime/daemon/service/workspace_server.py:122-138, 157-169`

Manifest-existence + version-positive checks duplicated verbatim between `ensure_workspace_base` and `_require_bound_active_workspace`. Drift will cause inconsistent errors / invariants. Extract a `_validate_manifest_for_root` helper.

---

### WR-07: `error_holder` branch in `_ensure_standalone_loop` is dead code

**File:** `backend/src/sandbox/runtime/async_bridge.py:147, 179-182`

`ready.set()` runs at line 153, before `loop.run_forever()`. The `error_holder` dict is populated only inside the `except BaseException` block at 156, which runs after `run_forever()` exits — i.e., after `ready` is already set, so the `if error_holder:` check at 179 is unreachable. Either move `ready.set()` after a successful loop init, or drop the dead branch.

---

### WR-08: `shell_runner._command_request` accepts unbounded argv length

**File:** `backend/src/sandbox/runtime/daemon/service/shell_runner.py:243-249`

No cap on `argv` length on either string or list branch. Per the user's `argv E2BIG` invariant, a large blob in a single argv element trips the kernel's `ARG_MAX` at exec time — surfacing as a confusing `OSError` rather than a clean `request_too_large`. Cap `sum(len(s) for s in argv) + env serialization` below `ARG_MAX` (128 KiB is conservative).

---

## Notes (not findings)

- **`metrics.py` `rglob("*")`** — O(tree) walk is a diagnostic; v1 excludes performance findings.
- **`classify_path` symlink-follow design** — Following symlinks for classification is by design. The TOCTOU between classify and write (WR-02) is the substantive concern.
- **No auth handshake on the RPC socket** — Filesystem gating (`0o600` socket + restricted parent dir + UID isolation) is fine **only if BL-03 is fixed**.
- **Broad `except Exception` in `server.py:101` and `dispatcher.py:73`** — Standard top-level RPC defensive practice, but combined with BL-01/BL-04 it drops requests silently and leaks tracebacks. Both fixed by the corresponding BL fixes.

---

## Finding Counts

- **BLOCKER:** 5 (BL-01 through BL-05)
- **WARNING:** 8 (WR-01 through WR-08)
- **Total:** 13
