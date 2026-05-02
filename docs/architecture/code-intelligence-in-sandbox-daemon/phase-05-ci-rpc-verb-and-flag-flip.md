# Phase 5 — First-class `ci_rpc` transport verb + daemon default

**Estimated effort:** 5-6 days (3 days engineering + 2-3 days E2E)
**Risk profile:** HIGH — daemon-default selection is the actual rollout event
**Status:** Implemented; post-canary cleanup complete
**Blocks on:** Phase 3.5 / 3.6 closure stable in production for at least one canary week, including `svc_cmd` and the stable `run_sync` fallback loop

> Current implementation note (2026-05-03): the old Phase 4 `svc.cmd`
> hot-path plan is superseded. `svc_cmd` is wired through the daemon and
> the ~5.5 s public-call floor was fixed in `sandbox.client.async_bridge`
> by keeping sync callers on a reusable standalone sandbox I/O loop. Phase 5
> is therefore a transport/product rollout phase: native `ci_rpc`, daemon
> default selection, cleanup, and optional batching/streaming work if the current
> stable-loop `transport.exec` floor of roughly 0.3-0.5 s is still too high.

## Cleanup scope reduction (vs original draft)

The original Phase 5 cleanup list mentioned ~600 lines of dead code in `mutations/content_manager.py`, `indexing/file_discovery.py`, and `language_server/transport.py`. The amended Phase 1+3 design (bundle-the-package + reuse) means those files are STILL imported by the daemon — just only the local-FS branches are exercised. Cleanup deletes the dead REMOTE branches; the local branches stay because both backends use them.

There are NO drift-guard test files to delete (the amended Phases 1 and 3 never created `_extracted.py` copies, so no `test_in_sandbox_drift_guard.py` exists to clean up).

Net: cleanup remains ~600 LOC, all in the existing `mutations/`, `indexing/`, `language_server/` files. No `in_sandbox/_extracted.py` or `in_sandbox/mutations/`, `in_sandbox/overlay/`, `in_sandbox/lsp/` directories ever existed to remove.

## Goal

Two deliverables:

1. **Promote `ci_rpc` to a first-class verb on `SandboxTransport`** — replaces the python socket shim used in Phases 2-3.6. The stable-loop fix already removes the accidental ~5.5 s sync-bridge cost; `ci_rpc` targets the remaining ~0.3-0.5 s per-call `transport.exec` floor.
2. **Make daemon-mode the production path** — transport-backed sandboxes select `RpcCiBackend` by default. After the post-canary cleanup, the old `EOS_CI_IN_SANDBOX=0` backend-selection backout is retired.

This phase also performs the cleanup pass: deletes the `_apply_remote_*`, `_read_remote*`, `_write_remote`, `_delete_remote`, `_stage_remote_payload`, `_collect_via_search`, `_collect_via_list`, `_read_text_via_exec`, `_batch_read_text_via_exec` branches that the daemon path makes dead (~600 lines).

## Why last

Three reasons:

1. **The shim works; promoting it is purely additive.** Phases 2-3.6 prove the daemon model with the python shim plus the stable sync bridge. Phase 5's `ci_rpc` verb replaces the shim with a native channel — same protocol, same semantics, just less overhead. It can be benchmarked apples-to-apples against the stable-loop shim.
2. **Daemon-default requires production confidence.** Phases 0-3.6 shipped behind a migration flag. Phase 5 is the rollout event, so it needs canary telemetry before the cleanup pass deletes the old orchestrator-side remote path.
3. **Cleanup is safe only after daemon-default stabilizes.** Deleting the orchestrator-side `_apply_remote_*` etc. branches is irreversible. Doing it after the canary ensures we do not advertise an env-var rollback path whose implementation has already been deleted.

## What ships

| Artifact | File | Purpose |
|---|---|---|
| `ci_rpc` Protocol method | `backend/src/sandbox/api/transport.py` (extended) | New `async def ci_rpc(self, sandbox_id, payload: bytes) -> bytes` Protocol method |
| `ci_rpc` impl (daytona) | `backend/src/sandbox/daytona/transport_impl.py` (or wherever `DaytonaTransport` lives) | Native socket bridge — daytona-side handler proxies bytes to/from the daemon's Unix socket |
| `ci_rpc` impl (other transports) | per-provider transport modules | Same Protocol; raises `NotImplementedError` if a provider doesn't support it |
| `CiRpcClient` switch | `backend/src/sandbox/code_intelligence/rpc/client.py` (modified) | Use `ci_rpc` verb when available; fall back to python shim for one release |
| Daemon-default selector | `backend/src/sandbox/code_intelligence/service.py` (modified) | `_select_backend(...)` selects `RpcCiBackend` when transport+sandbox_id are present, regardless of env |
| Settings doc | `CHANGELOG.md`, operational docs | Document the daemon default and retired backend-selection backout |
| Cleanup deletions | `backend/src/sandbox/code_intelligence/mutations/content_manager.py`, `indexing/file_discovery.py`, `language_server/transport.py` | Remove dead remote branches (~600 lines) |
| Phase 5 live E2E | `backend/tests/test_e2e/test_live_ci_phase5_default_on.py` | Native verb path + concurrency + curated cross-phase smoke |

## Detailed task list

### Task 5.1 — Add `ci_rpc` to `SandboxTransport` Protocol

**File to modify:** `backend/src/sandbox/api/transport.py`

```python
class SandboxTransport(Protocol):
    name: str

    # ... existing methods ...

    async def ci_rpc(
        self,
        sandbox_id: str,
        payload: bytes,
        *,
        timeout: int | None = None,
    ) -> bytes:
        """Send a length-prefixed msgpack frame to the in-sandbox CI daemon socket
        and return the response frame. Implementations bridge the orchestrator-side
        bytes to $HOME/.cache/eos-ci/<wh>/v1/daemon.sock inside the sandbox.

        Raises CiDaemonUnavailable if the socket isn't accessible — caller is expected
        to invoke ensure_daemon and retry.
        """
        ...
```

**Backward compatibility:** Existing transports that don't implement `ci_rpc` raise `NotImplementedError`; `CiRpcClient` falls back to the python shim. Phase 6 (out of scope) would remove the shim once every provider implements `ci_rpc`.

### Task 5.2 — Daytona implementation

**File to modify:** `backend/src/sandbox/daytona/transport_impl.py` (verify exact path during implementation)

The Daytona-side handler needs to:
1. Read the `payload: bytes` from the orchestrator.
2. Open the daemon's Unix socket (`$HOME/.cache/eos-ci/<wh>/v1/daemon.sock`).
3. Send the payload bytes-for-bytes.
4. Read the response frame (4-byte length + body).
5. Return the response bytes to the orchestrator.

**Two implementation options:**

- **(A) Daytona SDK-native socket bridging.** If Daytona's SDK exposes a binary stdin/stdout exec channel, use it directly — no shell, no encoding overhead.
- **(B) `python3 -c` one-shot bridge** (same as the shim, but co-located in the transport rather than `CiRpcClient`). Lower latency than the shim because `transport.exec` overhead is amortized inside `ci_rpc` rather than at the call site.

**Recommendation:** **(A) if available, fall back to (B)**. Check the Daytona SDK in `backend/src/sandbox/daytona/` for binary-safe exec primitives. Document the choice; the perf delta vs shim depends on which we use.

**Resolution of `$HOME`:** the verb's implementation needs to know `$HOME` and `<wh>` to build the socket path. Two options:
- Pass them as kwargs from `CiRpcClient` (cleaner; keeps transport stateless).
- Cache them on the transport-side handler (faster; one fewer arg).

**Recommendation:** Pass as kwargs `socket_path: str` so the transport doesn't need to know about CI semantics. `CiRpcClient` resolves the path once and reuses it.

```python
async def ci_rpc(self, sandbox_id, payload, *, socket_path, timeout=None) -> bytes: ...
```

(Update Task 5.1's Protocol to match.)

### Task 5.3 — `CiRpcClient` switch

**File to modify:** `backend/src/sandbox/code_intelligence/rpc/client.py`

```python
class CiRpcClient:
    async def _call_once(self, op, args, *, timeout):
        request = encode_frame({"v": 1, "id": uuid4().hex, "op": op, "args": args})
        socket_path = await self._resolve_socket_path()  # cached after first resolve

        # Phase 5: prefer native verb, fall back to shim
        if hasattr(self._transport, "ci_rpc") and not _SHIM_FORCED:
            try:
                response_bytes = await self._transport.ci_rpc(
                    self._sandbox_id, request, socket_path=socket_path, timeout=timeout,
                )
            except NotImplementedError:
                response_bytes = await self._call_via_shim(request, socket_path, timeout)
        else:
            response_bytes = await self._call_via_shim(request, socket_path, timeout)

        # Decode response (skip the 4-byte length, decode msgpack body)
        ...
```

**`_SHIM_FORCED` env var:** `EOS_CI_FORCE_SHIM=1` forces the python shim path even when `ci_rpc` is available — useful for A/B latency measurement during the canary week.

### Task 5.4 — Daemon-default backend selection

**File to modify:** `backend/src/sandbox/code_intelligence/service.py`

```python
def _select_backend(...):
    if transport is not None and sandbox_id:
        return RpcCiBackend(...)

    # Tests, local sandboxless paths
    return InProcessCiBackend(...)
```

The legacy `EOS_CI_IN_SANDBOX` migration flag is not part of backend
selection after the cleanup pass. A transport-backed sandbox must not
fall back to `InProcessCiBackend`, because the remote discovery/read
branches it would need are removed in Task 5.5.

### Task 5.5 — Cleanup pass

**Files to modify:**
- `backend/src/sandbox/code_intelligence/mutations/content_manager.py`
- `backend/src/sandbox/code_intelligence/indexing/file_discovery.py`
- `backend/src/sandbox/code_intelligence/language_server/transport.py`

**Delete:**
- `_apply_remote_batch`, `_apply_remote_batch_staged`, `_apply_remote_batch_checked`, `_apply_remote_batch_checked_staged`
- `_read_remote`, `_read_remote_batch`, `_read_fs`, `_read_fs_batch`
- `_write_remote`, `_delete_remote`, `_stage_remote_payload`, `_cleanup_remote_tmp`
- `_list_remote_folder_files`
- `_collect_via_search`, `_collect_via_list`, `_supports_exec_transport`, `_read_text_via_exec`, `_batch_read_text_via_exec`
- LSP `_run_python_script`'s `self._sandbox` branch (keep only the local `subprocess.run` branch for tests; daemon-mode goes through `ci_rpc`)

**Keep:**
- `InProcessCiBackend` and its construction of these modules — tests still rely on the in-process path.
- `_read_local`, `_write_local`, `_apply_local_batch_checked`, `collect_local_files`.

**Verify:**
- `grep -r "_apply_remote\|_read_remote\|_collect_via_" backend/src` shows zero matches outside test files (which can be deleted too if they only exercised the dead paths).
- `pyright`/`mypy` clean — no `unused import` errors.
- Total LOC reduction ≈ 600 lines (verify via `git diff --stat`).

### Task 5.6 — Phase 5 live E2E

**File:** `backend/tests/test_e2e/test_live_ci_phase5_default_on.py`

#### 5.6.A — Daemon-default full smoke

```python
async def test_default_flag_on_smoke(live_sweevo_env):
    """With transport+sandbox_id, every operation works through RpcCiBackend."""
    h = TimingHarness(phase=5, test_name="default_on_smoke")
    env = live_sweevo_env
    svc = env.make_ci_service()
    assert isinstance(svc._impl, RpcCiBackend)

    with h.step("ensure_initialized"):
        svc.ensure_initialized(wait=True)

    with h.step("query_symbols_warm"):
        results = svc.query_symbols("Bag")
    assert len(results) > 0

    with h.step("write_file"):
        svc.write_file([WriteSpec(file_path="/testbed/_phase5_smoke.txt",
                                  content="ok", overwrite=True)])

    with h.step("svc_cmd"):
        result = await svc.cmd(env.raw_sandbox, "find /testbed -name '*.py' | wc -l")
    assert result.exit_code == 0

    print(h.report())
    print(h.compare_to(latest_phase0_baseline()))
    h.dump_json()
```

#### 5.6.B — Native `ci_rpc` verb beats shim

```python
async def test_ci_rpc_verb_faster_than_shim(live_sweevo_env):
    """Native ci_rpc verb should beat the python shim path for warm-path latency."""
    h = TimingHarness(phase=5, test_name="ci_rpc_verb_vs_shim")
    env = live_sweevo_env
    svc = env.make_ci_service()  # default = ci_rpc verb
    svc.ensure_initialized(wait=True)

    # Warm up
    for _ in range(3):
        svc.query_symbols("Bag")

    # Measure verb path (default)
    with h.step("ci_rpc_verb_query_x10"):
        for _ in range(10):
            svc.query_symbols("Bag")

    # Force shim path
    with mock.patch.dict(os.environ, {"EOS_CI_FORCE_SHIM": "1"}):
        for _ in range(3):  # warm
            svc.query_symbols("Bag")
        with h.step("ci_rpc_shim_query_x10"):
            for _ in range(10):
                svc.query_symbols("Bag")

    verb_time = h.steps["ci_rpc_verb_query_x10"]
    shim_time = h.steps["ci_rpc_shim_query_x10"]
    assert verb_time < shim_time, (
        f"verb ({verb_time:.3f}s) NOT faster than shim ({shim_time:.3f}s) — "
        f"the entire point of Phase 5 has failed"
    )

    print(h.report())
    h.dump_json()
```

#### 5.6.C — Concurrency

```python
async def test_concurrent_query_symbols(live_sweevo_env):
    """8 concurrent query_symbols calls succeed without errors."""
    h = TimingHarness(phase=5, test_name="concurrent_8_queries")
    env = live_sweevo_env
    svc = env.make_ci_service()
    svc.ensure_initialized(wait=True)

    queries = ["Bag", "Array", "DataFrame", "compute", "delayed", "Future", "Client", "graph"]

    with h.step("concurrent_8_queries_total"):
        results = await asyncio.gather(*[
            asyncio.to_thread(svc.query_symbols, q) for q in queries
        ])

    assert all(isinstance(r, list) for r in results)

    print(h.report())
    h.dump_json()
```

#### 5.6.D — Curated cross-phase regression

After Phases 0-4 each have their own E2E tests, Phase 5 runs a curated subset of all of them (one assertion from each) inline as a final sanity check that nothing regressed when transport-backed sandboxes became daemon-default.

**Run command:** `uv run pytest backend/tests/test_e2e/test_live_ci_phase5_default_on.py -m live -v -s`

### Task 5.7 — Regression check

- `.venv/bin/pytest backend/tests/test_sandbox/ backend/tests/test_tools/ -q` — green with daemon-default selection.
- Re-run Phases 0, 1, 2, 3, 4 live E2Es — all green with daemon-default selection.
- Verify cleanup (Task 5.5) didn't break any test by removing a code path it depended on.

### Task 5.8 — Production canary

**Procedure (out of test, in production):**
1. Land Tasks 5.1-5.4 with daemon-default backend selection.
2. Roll out one orchestrator instance. Monitor for 1 week.
3. Compare production telemetry: `svc.cmd` p50/p95 latency, error rates, daemon respawn frequency, and shim usage.
4. If healthy: land Task 5.5 cleanup and document the removed backend-selection backout.
5. If unhealthy before cleanup: rollback the code change; investigate; add a follow-up phase.

This is a process, not a code change — document it in the PR and the runbook.

## Definition of done

- [ ] `ci_rpc` Protocol method added to `SandboxTransport` with documented signature.
- [ ] Daytona implementation of `ci_rpc` passes round-trip ping latency check.
- [ ] `CiRpcClient._call_once` prefers native verb, falls back to shim, supports `EOS_CI_FORCE_SHIM` for A/B.
- [ ] `_select_backend(...)` defaults to `RpcCiBackend` when transport+sandbox_id are present.
- [ ] **Phase 5 live E2E (all 4 subtests A-D) passes against `dask__dask_2023.3.2_2023.4.0`.**
- [ ] **5.6.B verb-vs-shim assertion passes** — native `ci_rpc` faster than shim.
- [ ] Cleanup pass (Task 5.5) removes dead code; total LOC reduction ≈ 600 lines.
- [ ] Production canary passed for 1 week with telemetry attached to the PR.
- [ ] Regression check: Phases 0, 1, 2, 3, 4 E2Es + full unit suite green with daemon-default selection.
- [ ] CHANGELOG entry documenting daemon-default selection and the retired backend-selection backout.
- [ ] PR description includes: 4 E2E reports + headline verb-vs-shim delta + canary telemetry summary.

## Risk callouts (Phase 5 specific)

| Severity | Risk | Mitigation |
|---|---|---|
| **HIGH** | Default-on rolls out a regression that affects every user | Production canary (Task 5.8); staged rollout one orchestrator at a time; code rollback before cleanup |
| **HIGH** | Cleanup deletes a code path that an orchestrator-only test depended on | Run regression suite BEFORE cleanup; keep cleanup as a separate commit so it can be reverted independently |
| **HIGH** | `ci_rpc` verb implementation in Daytona has a binary-encoding bug (e.g. NUL-byte stripping) | Round-trip a frame containing every byte value 0-255; assert exact equality |
| **MEDIUM** | `EOS_CI_FORCE_SHIM` left enabled in production accidentally → masks Phase 5 perf win | Telemetry surfaces shim usage rate; alert if non-zero in production after rollout |
| **MEDIUM** | Some non-Daytona transport doesn't implement `ci_rpc` → fallback to shim works but masks intent | Document per-provider matrix; raise `NotImplementedError` explicitly so the fallback is observable |
| **MEDIUM** | `RpcCiBackend.ensure_initialized` deadlocks when daemon spawn fails during daemon-default rollout | Strict timeouts everywhere; `CiDaemonUnavailable` surfaces with structured detail |
| **LOW** | Long-tail callers of `_apply_remote_*` etc. survive the cleanup | `grep -r` after cleanup; CI lints for unused functions |
| **LOW** | CHANGELOG missed; users surprised by default change | Block merge until CHANGELOG entry exists |

## Hand-off (post-Phase 5)

The migration is complete. Future work (out of scope here):

- **Phase 6 (deletion):** remove the python shim once every supported transport implements `ci_rpc`.
- **Eager bootstrap (`EOS_CI_EAGER_BOOTSTRAP=1`)** for ralph/codex sessions that pay first-call latency repeatedly.
- **Streaming `on_progress_line`** as a transport enhancement if final-stdout replay is not enough for CodeAct UX.
- **`memory/git_workspace_gitignored_deps_blocker.md`** — separate ADR on routing untracked-but-not-ignored paths through a new "runtime overlay" channel.

The plan ends here.
