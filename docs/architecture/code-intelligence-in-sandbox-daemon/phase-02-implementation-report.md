# Phase 2 — Daemon Process + Lifecycle: Implementation Report

Companion to
[`phase-02-daemon-lifecycle.md`](./phase-02-daemon-lifecycle.md).
Records the daemon lifecycle implementation, verification results, live
Daytona timings, and the implementation decisions that differ from the
draft plan.

---

## 1. Verdict

**Verdict: ships. Phase 2 daemon lifecycle is implemented and verified.**

The in-sandbox CI runtime now bundles and launches
`python -m sandbox.code_intelligence.in_sandbox` as a long-lived asyncio
Unix-socket daemon under
`$HOME/.cache/eos-ci/<workspace_root_hash>/v1/daemon.sock`. The
orchestrator has a `CiRpcClient` using the Phase 2 Python socket shim,
one retry-after-respawn path, and a `DaemonLauncher` that uploads the
runtime bundle, starts the daemon, polls socket readiness, and shuts the
daemon down from `RpcCiBackend.dispose()`.

The eager lifecycle hook changed from Phase 1's bundle+indexer behavior
to Phase 2's bundle+daemon behavior. The Phase 1 indexer still runs from
`RpcCiBackend.ensure_initialized`; no mutation, overlay, LSP, or symbol
business logic moved into the daemon in this phase.

Live Daytona verification passed against the dask SWE image. The main
provider finding is that Daytona's `process.exec` may wait on detached
background descendants even for `setsid nohup ... & echo $!`; the launcher
therefore treats a spawn-command timeout as inconclusive and polls the
daemon socket before failing. This is why live spawn timings are higher
than the original 2s target while still functionally correct.

---

## 2. File inventory

### Added

| Path | LoC | Purpose |
|---|---:|---|
| `backend/src/sandbox/code_intelligence/in_sandbox/__main__.py` | 48 | `python -m sandbox.code_intelligence.in_sandbox` entrypoint |
| `backend/src/sandbox/code_intelligence/in_sandbox/ci_daemon.py` | 270 | asyncio Unix-socket daemon, control dispatch, PID/socket cleanup |
| `backend/src/sandbox/code_intelligence/in_sandbox/ci_protocol.py` | 107 | 4-byte length-prefix + msgpack codec and schema validation |
| `backend/src/sandbox/code_intelligence/rpc/client.py` | 162 | `CiRpcClient`, Python socket shim, retry-after-respawn, typed RPC errors |
| `backend/tests/test_sandbox/test_code_intelligence/test_ci_daemon_unit.py` | 207 | Protocol, dispatch, shutdown scheduling, local daemon lifecycle tests |
| `backend/tests/test_sandbox/test_code_intelligence/test_ci_rpc_client.py` | 146 | Client success/error/retry tests with fake transport |
| `backend/tests/test_e2e/test_live_ci_phase2_daemon_lifecycle.py` | 367 | Live Daytona spawn, ping, kill/respawn, shutdown, concurrency, dispose tests |
| `backend/tests/test_e2e/_timings/phase_2_*.json` | n/a | Passing live timing artifacts for daemon-ready, kill/respawn, shutdown, dispose |

### Modified

| Path | Change |
|---|---|
| `backend/src/sandbox/code_intelligence/rpc/launcher.py` | Adds `DaemonLauncher`, `CiDaemonUnavailable`, remote state-path helper, spawn/socket/shutdown logic |
| `backend/src/sandbox/lifecycle/workspace.py` | Eager bootstrap now ensures daemon readiness instead of running `ci_index` |
| `backend/src/sandbox/code_intelligence/backend.py` | `RpcCiBackend.ensure_initialized` ensures daemon lifecycle before Phase 1 indexing; `dispose()` shuts daemon down |
| `backend/src/sandbox/lifecycle/service.py` | Adds lifecycle progress logs around create/refresh/git/bootstrap |
| `backend/src/sandbox/lifecycle/proxy.py` | Adds `ensure_git` progress logs for live setup diagnosis |
| Existing Phase 0/1 tests | Updated expectations for daemon lifecycle, bundle contents, and `RpcCiBackend.dispose()` |

### Deleted

None.

---

## 3. Per-story coverage map

| Story | Verdict | Evidence |
|---|---|---|
| **P2-001** Wire protocol | PASS | `ci_protocol.py` implements `CI_PROTOCOL_VERSION=1`, `MAX_FRAME_BYTES=64MB`, msgpack length frames, `FrameError`, `SchemaError`, request/response dataclasses, and parse helpers. Unit tests cover round-trip, oversized header/body, bad version, and bad request schema. |
| **P2-002** Daemon server | PASS | `ci_daemon.py` starts an asyncio Unix server, writes `daemon.pid`, binds `daemon.sock`, sets socket mode `0600`, dispatches `ping`/`shutdown`/`version`, handles stale dead PID/socket cleanup, rejects live PID startup, and removes PID/socket on shutdown. |
| **P2-003** Daemon entry | PASS | `__main__.py` parses `--workspace-root` and `--log-level`, returns 13 for `CiStorageUnavailable`, 11 for live stale daemon, 0 for normal shutdown. Bundle smoke test imports the daemon entry and dispatch table from extracted runtime. |
| **P2-004** RPC client | PASS | `client.py` sends one frame through an inline Python Unix-socket shim over `transport.exec`, decodes the response frame, raises `CiDaemonRpcError` for error envelopes, and retries once through `DaemonLauncher.ensure_daemon()` after connection failure. |
| **P2-005** Launcher + eager lifecycle | PASS | `DaemonLauncher.ensure_daemon()` checks pid/socket, uploads runtime if needed, spawns via `setsid nohup`, polls socket readiness, and exposes shutdown. `bootstrap_in_sandbox_ci_runtime()` now calls this launcher from create/start/restart hooks. |
| **P2-006** Live E2E | PASS | `test_live_ci_phase2_daemon_lifecycle.py` passed as two live invocations: first daemon-ready test, then the remaining kill/respawn, clean shutdown, concurrent pings, and dispose tests. |
| **P2-007** Unit tests | PASS | New daemon/client unit tests plus updated eager/bootstrap/backend/bundle tests pass in the sandbox suite. |
| **P2-008** Regression check | PASS | `uv run pytest backend/tests/test_sandbox -q` -> 478 passed. Ruff clean across changed source/tests. |

---

## 4. Verification

### Unit and regression

| Command | Result |
|---|---|
| `uv run pytest backend/tests/test_sandbox/test_code_intelligence/test_ci_daemon_unit.py backend/tests/test_sandbox/test_code_intelligence/test_ci_rpc_client.py -q` | **15 passed** |
| `uv run pytest backend/tests/test_sandbox/test_eager_ci_bootstrap.py backend/tests/test_sandbox/test_code_intelligence/test_runtime_bundle.py backend/tests/test_sandbox/test_code_intelligence/test_rpc_ci_backend.py backend/tests/test_sandbox/test_code_intelligence/test_backend_inprocess.py -q` | **59 passed** |
| `uv run pytest backend/tests/test_sandbox -q` | **478 passed** |
| `uv run ruff check backend/src/sandbox/code_intelligence backend/src/sandbox/lifecycle backend/tests/test_sandbox/test_code_intelligence backend/tests/test_sandbox/test_eager_ci_bootstrap.py backend/tests/test_e2e/test_live_ci_phase2_daemon_lifecycle.py` | **All checks passed** |

### Live E2E

| Command | Result |
|---|---|
| `uv run pytest backend/tests/test_e2e/test_live_ci_phase2_daemon_lifecycle.py::test_daemon_ready_after_create_sandbox -m live -v -s` | **1 passed in 43.74s** |
| `uv run pytest backend/tests/test_e2e/test_live_ci_phase2_daemon_lifecycle.py -m live -k 'not daemon_ready_after_create' -v -s` | **4 passed, 1 deselected in 122.18s** |

---

## 5. Live timing summary

### Daemon ready after create — PASSED

```
create_sandbox_with_ci_bootstrap: 31.951s
daemon_first_ping_no_retry:       5.862s
pid_liveness_check:               0.381s
--- TOTAL: 38.194s ---
```

Mid-flight logs show this dask-image run avoided the generic image's git
install timeout:

```
Daytona create starting -> returned: ~1s
ensure_git probe: git already available at ~7s
eager daemon bootstrap: 24.887s
daemon process: python3 -m sandbox.code_intelligence.in_sandbox --workspace-root /testbed
```

### Kill -9 + respawn — PASSED

```
initial_spawn_and_ping:   25.343s
daemon_kill9:             0.647s
daemon_respawn_via_call:  17.002s
--- TOTAL: 42.992s ---
```

### Clean shutdown — PASSED

```
initial_spawn:            5.822s
shutdown_rpc:             0.439s
post_shutdown_settle:     0.501s
verify_pid_cleanup:       0.366s
verify_socket_cleanup:    0.330s
--- TOTAL: 7.459s ---
```

### Dispose cleanup — PASSED

```
create_sandbox:           24.591s
spawn_daemon:             5.802s
dispose_sandbox:          0.029s
--- TOTAL: 30.422s ---
```

`test_concurrent_pings` also passed. It is correctness-only and does not
write a timing JSON.

---

## 6. Implementation decisions

### 6.1 Eager bootstrap is daemon-only in Phase 2

Phase 1's eager hook ran the indexer synchronously. Phase 2 changes that
hook to upload the bundle and make the daemon reachable. Keeping the
indexer in `RpcCiBackend.ensure_initialized` preserves the "no business
logic moves" rule while satisfying the daemon-ready lifecycle contract.

### 6.2 Python socket shim, not `socat` or `nc`

`CiRpcClient` uses an inline Python shim over `transport.exec` to connect
to the Unix socket and return a base64 response frame. This keeps Phase 2
independent of `socat`/`nc` availability. The shim is intentionally
temporary; Phase 5 replaces it with a first-class `ci_rpc` transport verb.

### 6.3 Spawn timeout is treated as inconclusive on Daytona

Live Daytona showed that a detached background command can time out at
the exec API even when the daemon process did start. The launcher now
catches spawn-command exceptions, then polls the socket before failing.
This keeps correctness while documenting that Phase 2 timing is dominated
by the transport shim and provider process-exec behavior.

### 6.4 Live test setup logs are intentionally visible

The first live test originally looked hung because `create_sandbox()`
wrapped Daytona provisioning, refresh, git bootstrap, eager CI bootstrap,
and socket polling in one timing step. The live test now streams timestamped
logs from `sandbox.lifecycle.service`, `sandbox.lifecycle.proxy`,
`sandbox.lifecycle.workspace`, and `sandbox.code_intelligence.rpc.launcher`.
The production code uses normal `logger.info`; only the live test installs
a stdout handler.

---

## 7. Hand-off to Phase 3

Phase 3 can assume:

- The runtime bundle can launch `python -m sandbox.code_intelligence.in_sandbox`.
- The daemon owns `daemon.sock`, `daemon.pid`, and `daemon.log` under the
  workspace-hashed state dir.
- The orchestrator can issue framed msgpack RPCs via `CiRpcClient.call`.
- Daemon crash between calls is covered by one retry-after-respawn path.
- `shutdown` removes the PID and socket files.

Phase 3 should add real code-intelligence verbs to the daemon dispatch
table without changing the process lifecycle contract again.
