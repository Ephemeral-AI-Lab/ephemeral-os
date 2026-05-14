# Sandbox Reframe Deferred Remediation Report

**Date:** 2026-05-14
**Source log:** `.planning/sandbox-reframe-execution-log.md`

## Deferred Issue Inventory

| ID | Source deferral | Remediation decision | Status |
|---|---|---|---|
| D1 | W7c-full Daytona client dedup and Scenario F cache isolation | Implement the missing safe part now: factory-tagged credential-hash cache keys for the existing sync and async cache containers, plus a regression test that constructs sync and async clients back-to-back and proves they do not cross-contaminate. Do not unify the containers because sync singleton and async loop-keyed cache have different semantics. | Done in Phase 1 |
| D2 | W8a residual `versioned_payload` ownership | Move the daemon protocol constants and payload-versioning helper to `sandbox.host.daemon_client`; keep `sandbox.api.transport` focused on the default transport and daemon op names. | Done in Phase 2; legacy re-export removed in cleanup |
| D3 | W8a residual `SandboxTransport` Protocol to Callable alias | Close as no-op unless a later public API change replaces the object-with-`.call(...)` transport seam. The current entrypoints call `transport.call(...)`; a bare Callable alias would be type-inaccurate or force behavior/test churn for no net deletion. | Closed in Phase 3 |
| D4 | W3 real-Daytona daemon-boot validation | Attempt the narrow live gate only when credentials and a default live image are configured. If not configured, record the exact blocker and keep the deterministic path-shaped grep/unit checks as local evidence. | Local source cleanup done; live gate blocked in Phase 4 |
| D5 | W7c real-Daytona validation | Same live gate as D4, plus the new Scenario F unit regression. A real provider run remains environment-gated. | Unit regression done; live gate blocked in Phase 4 |
| D6 | ADR §15 scope-reopen items: deeper `occ/stage` merge, plugin registry collapse, squash deeper merge | Keep out of scope for this remediation unless the user explicitly reopens ADR §15 feature cuts. These are not deferred bugs from the reframe; they are accepted scope expansions needed only to chase the original LOC floor. | Closed in Phase 5 |

## Phase Plan

### Phase 1 — W7c cache-key closure

- Add a `DaytonaClientCacheKey` helper keyed by factory identity, hashed credential material, and target.
- Use the helper in both existing cache containers without unifying their lifecycle semantics.
- Add unit coverage for sync + async client construction in the same process.

**Verification target:** focused Daytona client/cache tests.

### Phase 2 — W8a payload-versioning ownership

- Move `DAEMON_PROTOCOL_*` and `versioned_payload()` into `sandbox.host.daemon_client`.
- Keep `sandbox.api.transport` focused on the transport class and daemon op constants.
- Add host-level unit coverage for the payload helper while preserving existing API transport tests.

**Verification target:** sandbox API transport and daemon-client tests.

### Phase 3 — W8a Protocol residual closure

- Re-check current call shape.
- If still `.call(...)` based, record an explicit no-op closure instead of introducing a misleading type alias.

**Verification target:** grep evidence and existing transport seam test.

### Phase 4 — Live validation gate

- Check for Daytona credentials and `EPHEMERALOS_SANDBOX_DEFAULT_IMAGE` without exposing secret values.
- If available, run the smallest live smoke with `EPHEMERALOS_SANDBOX_TIMEOUT_SECONDS=60`.
- If unavailable, record the missing prerequisites as a remaining user-gated validation item.

**Verification target:** live smoke result or explicit blocker.

### Phase 5 — ADR §15 closure

- Re-read the current ADR scope notes.
- Record that the remaining LOC-yield work is a scope expansion, not an unaddressed deferred issue.

**Verification target:** source-plan citation in this report.

## Phase Results

### Phase 1 — W7c cache-key closure

**Status:** Done.

**Code changes:**

- Added `DaytonaClientCacheKey` and `client_cache_key()` in `backend/src/sandbox/provider/daytona/client/credentials.py`.
- Updated sync cache state in `backend/src/sandbox/provider/daytona/client/sync_client.py` to key by `("Daytona", credential_hash, target)`.
- Updated async loop-local cache state in `backend/src/sandbox/provider/daytona/client/async_client.py` to key by `("AsyncDaytona", credential_hash, target)`.
- Updated existing async-client and shutdown tests to seed the new key shape.
- Added `backend/tests/unit_test/test_sandbox/test_daytona_client_cache.py`, covering sync + async construction in the same process, distinct concrete client types, distinct factory tags, and no raw API key retention in cache keys.

**Verification:**

```bash
.venv/bin/pytest backend/tests/unit_test/test_sandbox/test_daytona_client_cache.py backend/tests/unit_test/test_sandbox/test_async/test_client.py backend/tests/unit_test/test_sandbox/test_lifecycle.py backend/tests/unit_test/test_sandbox/test_credentials.py -q
# 15 passed

.venv/bin/ruff check backend/src/sandbox/provider/daytona/client backend/tests/unit_test/test_sandbox/test_daytona_client_cache.py backend/tests/unit_test/test_sandbox/test_async/test_client.py backend/tests/unit_test/test_sandbox/test_lifecycle.py
# All checks passed
```

**Residual:** The RFC's unified `_acquire_cached_client(factory_cls)` helper remains intentionally unimplemented because the current sync singleton and async loop-keyed `WeakKeyDictionary` have different lifecycle semantics. The Scenario F risk is addressed without changing those semantics.

### Phase 2 — W8a payload-versioning ownership

**Status:** Done.

**Code changes:**

- Moved `DAEMON_PROTOCOL_FIELD`, `DAEMON_PROTOCOL_VERSION`, and `versioned_payload()` into `backend/src/sandbox/host/daemon_client.py`.
- Updated `backend/src/sandbox/api/transport.py` to import the host-owned helper privately, without re-exporting daemon protocol constants or `versioned_payload()`.
- Added host-level test coverage in `backend/tests/unit_test/test_sandbox/test_api/test_daemon_client.py`.

**Verification:**

```bash
.venv/bin/pytest backend/tests/unit_test/test_sandbox/test_api/test_transport_protocol.py backend/tests/unit_test/test_sandbox/test_api/test_daemon_client.py backend/tests/unit_test/test_sandbox/test_api/test_shell.py backend/tests/unit_test/test_sandbox/test_api/test_read.py backend/tests/unit_test/test_sandbox/test_api/test_write.py backend/tests/unit_test/test_sandbox/test_api/test_edit.py -q
# 15 passed

.venv/bin/ruff check backend/src/sandbox/api/transport.py backend/src/sandbox/host/daemon_client.py backend/tests/unit_test/test_sandbox/test_api/test_daemon_client.py
# All checks passed
```

**Cleanup follow-up:** A later review removed the temporary compatibility re-export from `sandbox.api.transport` and deleted the duplicate payload-versioning assertion from `test_transport_protocol.py`; host ownership is now asserted only in `test_daemon_client.py`.

### Phase 3 — W8a Protocol residual closure

**Status:** Closed as no-op.

**Evidence:**

- `backend/src/sandbox/api/_impl/_run_verb.py` and `backend/src/sandbox/api/_impl/shell.py` still dispatch through `selected_transport.call(...)`.
- `backend/tests/unit_test/test_sandbox/test_api/test_run_verb_seam.py` explicitly protects that mock seam.
- `backend/tests/unit_test/test_sandbox/test_api/test_transport_protocol.py` still validates an object with an async `.call(...)` method against `SandboxTransport`.

**Decision:** Replacing `SandboxTransport` with a bare Callable alias would either misrepresent the current contract or require changing the public test-injection seam for no behavioral gain. The deferred item is therefore closed by preserving the Protocol.

### Phase 4 — W3/W7c live validation gate

**Status:** Partially done; live provider validation is blocked by local Daytona API availability.

**Code/source cleanup completed:**

- Fixed stale `sandbox.runtime.*` source docstrings/comments in:
  - `backend/src/sandbox/__init__.py`
  - `backend/src/sandbox/daemon/rpc/server.py`
  - `backend/src/sandbox/daemon/service/occ_backend.py`
  - `backend/src/sandbox/plugin/install.py`
  - `backend/src/sandbox/daemon/async_bridge.py`
  - `backend/src/sandbox/daemon/handler/__init__.py`
  - `backend/src/sandbox/daemon/handler/request_context.py`

**Local deterministic verification:**

```bash
rg -n "sandbox\\.runtime|sandbox/runtime|python -m sandbox\\.runtime|sandbox_dir / \"runtime\"" backend/src/sandbox || true
# no source hits

PYTHONPATH=backend/src .venv/bin/python - <<'PY'
from sandbox.daemon_paths import RUNTIME_SCRIPT_DIR, DAEMON_LAUNCH_SCRIPT_PATH
from sandbox.host.runtime_bundle import bundle_hash
print(RUNTIME_SCRIPT_DIR)
print(DAEMON_LAUNCH_SCRIPT_PATH)
print(len(bundle_hash()))
PY
# /tmp/eos-sandbox-runtime/sandbox/daemon/scripts
# /tmp/eos-sandbox-runtime/sandbox/daemon/scripts/launch_daemon.sh
# 64

.venv/bin/pytest backend/tests/unit_test/test_sandbox/test_daemon/test_bundle.py backend/tests/unit_test/test_sandbox/test_daemon/test_bundle_upload.py backend/tests/unit_test/test_sandbox/test_daemon/test_runtime_ready.py -q
# 21 passed
```

**Live attempt:**

```bash
EPHEMERALOS_SANDBOX_TIMEOUT_SECONDS=60 PYTHONPATH=backend/src .venv/bin/pytest backend/tests/live_e2e_test/sandbox/layer_stack_overlay_occ/test_phase00_smoke.py -q -s --tb=short
# ERROR during fixture setup before sandbox runtime execution:
# Daytona API URL is localhost:3000 and connection was refused.
```

**Provisioning evidence:**

```bash
daytona list --format json
# fatal: Get "http://localhost:3000/api/sandbox/paginated?...": connect: connection refused

curl -sS --max-time 5 http://localhost:3000/api/health
# Failed to connect to localhost port 3000

lsof -nP -iTCP:3000 -sTCP:LISTEN
# no listeners

docker ps
# Cannot connect to the Docker daemon
```

**Decision:** The W3/W7c real-provider gate is not currently runnable in this environment because the configured local Daytona API is down and Docker is not running. The failure occurs before daemon bundle upload, daemon boot, sync/async client use, or public sandbox tool execution, so it is not evidence of a sandbox reframe regression.

### Phase 5 — ADR §15 scope-gated items

**Status:** Closed as scope-gated, not implemented.

**Evidence:**

- `.planning/sandbox-reframe-plan.md` records the contingent floor rule: the 20% LOC target was explicitly waived and replaced with the accepted realistic ceiling / ADR §15 amendment.
- `.planning/sandbox-reframe-rfc-decomposition.md` marks the deeper `direct.py`/`gated.py` merge as explicitly out of scope and deferred per ADR §15 follow-up.
- `.planning/sandbox-reframe-execution-log.md` says the named-wave queue is exhausted and remaining LOC yield requires either ADR §15 scope reopening or user-gated T3 work.

**Decision:** Do not implement the deeper OCC stage merge, plugin registry collapse, or squash deeper merge in this remediation pass. Those are feature-scope expansions, not unresolved defects from the deferred issue list.

## Final Verification

```bash
.venv/bin/pytest backend/tests/unit_test/test_sandbox -q
# 545 passed, 1 skipped

.venv/bin/ruff check backend/src/sandbox backend/tests/unit_test/test_sandbox
# All checks passed
```

## Final Residuals

- Real Daytona validation remains blocked until the configured local Daytona API at `localhost:3000` is listening and Docker is available.
- ADR §15 feature-scope items remain intentionally unimplemented unless the scope is explicitly reopened.
