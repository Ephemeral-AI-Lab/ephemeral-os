# Sandbox Host/Runtime Implementation Report

Source review: `.planning/code-reviews/sandbox-host-runtime-HARSH-REVIEW.md`

## Status

In progress.

## Phase Log

### Phase 1: Host Boundary And Naming

- Status: complete.
- Changes:
  - Moved the shared bridge to `sandbox.async_bridge` and updated source/test
    imports away from `sandbox.runtime.async_bridge`.
  - Confirmed host lifecycle ownership is `sandbox.host.bootstrap`; stale
    `setup.py`, `git.py`, `recovery.py`, and `context.py` import paths no
    longer appear in source/tests/docs.
  - Confirmed context-preparer lookup lives at `sandbox.host.context_preparer`
    with a mapping-shaped `SandboxRuntimeContext` protocol instead of a raw
    `Any` context argument.
  - Updated `docs/wiki/sandbox-subsystem.md` to point at the new host bootstrap
    and bridge locations.
- Verification:
  - Passed: `.venv/bin/pytest backend/tests/unit_test/test_sandbox/test_runtime_bootstrap.py backend/tests/unit_test/test_sandbox/test_host/test_ops_git.py backend/tests/unit_test/test_sandbox/test_async/test_bridge.py backend/tests/unit_test/test_sandbox/test_api/test_facade.py -q`
  - Related broader check still has a pre-existing API root drift failure:
    `backend/tests/unit_test/test_sandbox/test_api/test_contract.py::test_api_root_keeps_public_surface_grouped_by_role`
    sees extra `api/transport.py`, `api/timeouts.py`, `api/protocol.py`, and
    `api/_tool`.

### Phase 2: Bundle Paths And Launch Scripts

- Status: complete.
- Changes:
  - Added `sandbox.daemon_paths` as the shared host/runtime wire-path contract.
  - Removed daemon remote path ownership from `host/runtime_bundle.py`; plugin
    install and tests import the remote root from `sandbox.daemon_paths`.
  - Split `bundle_hash()` from pure `compute_bundle_hash(bundle)` and added
    `clear_bundle_caches()` as the explicit cache reset seam.
  - Replaced embedded thin-client Python and daemon-launch shell with real
    runtime assets under `sandbox/runtime/scripts/`.
  - Replaced the host git bootstrap shell string with `runtime/scripts/install_git.sh`.
  - Removed the empty forwarded-daemon-env pseudo-extension point from daemon
    spawn signature generation.
- Verification:
  - Passed: `.venv/bin/pytest backend/tests/unit_test/test_sandbox/test_daemon/test_daemon_transport.py backend/tests/unit_test/test_sandbox/test_daemon/test_bundle_upload.py backend/tests/unit_test/test_sandbox/test_live_setup_api.py -q`
  - Passed: `.venv/bin/python -m py_compile backend/src/sandbox/runtime/scripts/thin_client.py backend/src/sandbox/daemon_paths.py backend/src/sandbox/host/daemon_client.py backend/src/sandbox/host/runtime_bundle.py`

### Phase 3: Handler Helper And Service Contracts

- Status: pending.
- Changes:
  - Pending implementation.
- Verification:
  - Pending.

### Phase 4: Daemon Operation Registration

- Status: pending.
- Changes:
  - Pending implementation.
- Verification:
  - Pending.

### Phase 5: Workspace Service Simplification

- Status: pending.
- Changes:
  - Pending implementation.
- Verification:
  - Pending.
