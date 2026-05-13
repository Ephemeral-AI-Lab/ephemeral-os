---
status: issues_found
depth: standard
files_reviewed: 7
findings:
  blocker: 0
  warning: 7
  info: 3
  total: 10
subsystem: host
---

# Phase: Sandbox `host` Subsystem — Code Review Report

**Reviewed:** 2026-05-13
**Depth:** standard

## Files Reviewed

- `backend/src/sandbox/host/__init__.py`
- `backend/src/sandbox/host/context.py`
- `backend/src/sandbox/host/daemon_client.py`
- `backend/src/sandbox/host/git.py`
- `backend/src/sandbox/host/recovery.py`
- `backend/src/sandbox/host/runtime_bundle.py`
- `backend/src/sandbox/host/setup.py`

## Summary

The subsystem is generally hardened: every shell interpolation uses `shlex.quote`, the tar bundle is built from controlled host paths (no zip-slip vector), and credentials/secrets do not appear in any path. The runtime bundle is built deterministically and chunk-uploaded with marker-based idempotency.

The defects below are all in the daemon-client retry semantics and in the post-restart recovery flow. None are command-injection or credential-leak issues. The most concerning is a non-idempotent retry path in `daemon_client._exec_daemon_call` that can re-execute side-effectful operations like `api.shell` / `api.write_file` / `api.edit_file` if a transient error string trips the heuristic classifier.

---

## Findings

### WARNING — WR-01: Non-idempotent daemon retry can double-execute side-effectful ops

**File:** `backend/src/sandbox/host/daemon_client.py:139-174` (specifically the retry at `168-173`)
**Severity:** WARNING (close to BLOCKER; narrow trigger keeps it from being one in practice)

`_exec_daemon_call` re-sends the original `raw_payload` whenever `_looks_like_socket_missing(result)` returns true. The classifier (`177-193`) is a case-insensitive substring match over `stderr`/`stdout` for the needles `connectionrefusederror`, `filenotfounderror`, `no such file or directory`, `connection refused`. Callsites include `api.shell`, `api.write_file`, and `api.edit_file` — explicitly NOT idempotent.

Trigger conditions:
1. `"no such file or directory"` is a very common string. If the thin-client process itself fails for an unrelated reason and prints that text, the retry fires even though the daemon may have already processed the request.
2. If the daemon receives the payload, starts processing the side effect, then the socket fails on recv (daemon crash mid-response), the partial output could carry an arbitrary substring matching a needle.

**Fix:** Make the thin client emit a distinctive marker (e.g., exit code 97 on connect failure, 98 on send/recv failure) and key the retry on exit code, not stderr substring.

---

### WARNING — WR-02: Dead/redundant assignments in `recovery.ensure_running`

**File:** `backend/src/sandbox/host/recovery.py:40-50`

```python
try:
    info = adapter.start(sandbox_id)        # line 41 — value discarded
except Exception:
    logger.debug(...)
    info = adapter.get(sandbox_id)          # line 48 — value discarded

info = adapter.get(sandbox_id)              # line 50 — unconditionally overwrites
```

Lines 41 and 48 are dead writes overwritten by line 50. Behavior is correct but the code reads as if the author intended `try/except` to determine `info`. Drop the dead writes.

---

### WARNING — WR-03: Concurrent runtime-bundle uploads on the same sandbox can corrupt the tarball

**File:** `backend/src/sandbox/host/runtime_bundle.py:289-322`

Upload sequence: `: > bundle.tar.gz` (truncate) → loop of `>> bundle.tar.gz` (append) → `tar -xzf` → write hash marker. No locking, no temp+rename. Two concurrent host processes targeting the same sandbox will interleave their `>>` writes. The hash-marker check is racy: both can see "missing" simultaneously.

Per project memory, parallel host processes occur in this codebase.

**Fix:** Stage to a per-upload unique path (`uuid`-suffixed) then atomically rename, or `flock` around the upload region.

---

### WARNING — WR-04: `ensure_git` swallows all exceptions, including provider-adapter unreachability

**File:** `backend/src/sandbox/host/git.py:81-82`

`except Exception` is intended to make git-install best-effort, but it also swallows provider-adapter exceptions indicating the sandbox itself is unreachable. Downstream `setup_after_create` then proceeds to `run_runtime_bootstrap` and `ensure_workspace_base`, which fail confusingly.

**Fix:** Narrow the catch to `RuntimeError` (the explicit raise on line 75) and exit-code-based "missing git" outcomes; let adapter exec failures propagate.

---

### WARNING — WR-05: Untyped `Any` exec-result handling silently degrades to "no error" on attribute miss

**File:** `backend/src/sandbox/host/daemon_client.py:100-114, 184-186, 401-406`; `runtime_bundle.py:279-280, 297, 310, 322`

`getattr(result, "exit_code", 0)` in `_looks_like_socket_missing` (line 184) means a future provider returning an object without `exit_code` would silently mask errors. Other call sites default to `1`, which is safer but inconsistent.

**Fix:** Centralize one helper that raises `_DaemonDispatchError` on missing `exit_code`; remove the silent defaults.

---

### WARNING — WR-06: `_check_daemon_readiness_after_spawn` loses original-request context on readiness failure

**File:** `backend/src/sandbox/host/daemon_client.py:196-241`

When readiness check fails post-respawn, error details carry the readiness-check error but not the original `op` that triggered the respawn. Operator debugging sees "RuntimeReadinessFailed" with no indication of which user op was lost.

**Fix:** Include `original_op` (already parsed by `_readiness_request_for_original`) in `details` for every `_DaemonReadinessError` raised in this function.

---

### WARNING — WR-07: Background bundle upload uses a fresh `ThreadPoolExecutor` per call

**File:** `backend/src/sandbox/host/setup.py:106-141`

A new executor created per call and shut down with `wait=False`. Under high call frequency (many sandboxes in parallel) this leaks fds and thread objects until GC. If `_do_upload` raises and the caller never invokes `finish_runtime_bundle_upload`, the exception is silently swallowed by GC.

**Fix:** Use `asyncio.create_task` with a caller-held task reference, OR a module-level bounded executor, OR at minimum attach `future.add_done_callback(lambda f: f.exception())` so errors aren't silently dropped.

---

### INFO — IN-01: Thin-client launcher lacks Python version check while spawn launcher has one

**File:** `backend/src/sandbox/host/daemon_client.py:37-45` vs. `340-352`

Thin client iterates `python3.13 ... python3` and `exec`s the first found; spawn launcher also checks `sys.version_info >= (3, 10)`. Current thin-client code uses only widely-compatible features so it works, but adding 3.10+ syntax later would fail silently on old interpreters.

**Fix:** Match the version probe used in `_daemon_launcher`.

---

### INFO — IN-02: `_DAEMON_THIN_CLIENT_PY` lacks explicit recv-loop termination on timeout

**File:** `backend/src/sandbox/host/daemon_client.py:25-35`

`socket.settimeout(...)` applies per-recv. On mid-response timeout, partial JSON is lost and the python exception text becomes the only output — combined with WR-01 this could trip the substring retry heuristic.

**Fix:** Catch `socket.timeout` explicitly, write a distinctive stderr marker, exit with a reserved code; pair with WR-01.

---

### INFO — IN-03: `_FORWARDED_DAEMON_ENV = ()` is an empty extension point

**File:** `backend/src/sandbox/host/daemon_client.py:23, 358-375`

Always empty, so `_daemon_env_exports`/`_daemon_env_signature` always return `""`. The "if env changed, restart daemon" logic is dead. Either intentional extension point (document it) or refactor leftover (delete it).

---

## Verified Safe (negatives worth recording)

- **Subprocess/shell construction:** Every interpolation uses `shlex.quote`. No command-injection vector. `_GIT_BOOTSTRAP` is a static heredoc with no interpolation.
- **Tar bundle path inclusion (`runtime_bundle.py`):** All paths come from `Path(__file__).resolve().parent.parent.parent` + `rglob("*.py")` — host-controlled, no zip-slip. `_normalize_tarinfo` strips uid/gid/mtime for determinism.
- **Credentials/secrets in setup logs:** `setup.py` logs only sandbox ids and workspace roots. **Note:** if env vars are ever added to `_FORWARDED_DAEMON_ENV`, `_daemon_env_signature()` would write their values to `/tmp/eos-sandbox-runtime/runtime.env` — that path would become a BLOCKER if secrets ever live there.
- **Path traversal:** `BUNDLE_REMOTE_DIR = "/tmp/eos-sandbox-runtime"` is a hardcoded constant; no user-supplied joins.
- **`context.py`:** Provider-neutral factory, no security surface.

---

## Finding Counts

- **BLOCKER:** 0
- **WARNING:** 7 (WR-01 through WR-07)
- **INFO:** 3 (IN-01, IN-02, IN-03)
- **Total:** 10
