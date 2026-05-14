# Sandbox API Remediation Implementation Report

Started: 2026-05-14

Source review: `.planning/code-reviews/sandbox-api-REVIEW.md`

## Phase Status

| Phase | Status | Evidence |
| --- | --- | --- |
| 1. API contracts and shared helpers | Done | `uv run pytest backend/tests/unit_test/test_sandbox/test_api/test_payload_helpers.py backend/tests/unit_test/test_sandbox/test_api/test_transport_protocol.py -q` -> 7 passed |
| 2. Tool verb refactor | Done | `uv run pytest backend/tests/unit_test/test_sandbox/test_api/test_raw_exec.py backend/tests/unit_test/test_sandbox/test_api/test_read.py backend/tests/unit_test/test_sandbox/test_api/test_write.py backend/tests/unit_test/test_sandbox/test_api/test_shell.py backend/tests/unit_test/test_sandbox/test_api/test_edit.py backend/tests/unit_test/test_sandbox/test_api/test_audit_emission.py -q` -> 19 passed |
| 3. Facade and default client | Pending |  |
| 4. Lifecycle/discovery split | Pending |  |
| 5. Daemon version and error taxonomy | Pending |  |
| 6. Hygiene and closeout | Pending |  |

## Notes

- Existing dirty worktree files under `backend/src/sandbox/layer_stack*` and
  related tests are unrelated to this remediation and will be left untouched.
- `.DS_Store` is already ignored by `.gitignore`; tracked status will be
  verified in Phase 6.

## Phase 1 Details

- Added `sandbox.api.protocol` with explicit `SandboxTransport`,
  `SandboxToolAPI`, `SandboxLifecycleAPI`, and combined `SandboxAPI` contracts.
- Added `sandbox.api.transport` with the default daemon transport and an
  explicit daemon protocol version marker.
- Added `sandbox.api.timeouts` so verb timeout policy has a single owner.
- Tightened `_payload.py`: dataclass-driven caller audit projection, normalized
  cwd stripping, shared `internal_error` stripping, regex-based transient
  transport matching, and strict integer decoding.

## Phase 2 Details

- Moved real verb implementations from `sandbox.api.tool` into
  `sandbox.api._tool`; `sandbox.api.tool` now only preserves legacy direct
  imports.
- Added a shared audited execution wrapper for read/write/edit/shell/raw-exec
  success, conflict, and failure publishing.
- Routed read/write/edit/shell through the injected `SandboxTransport` seam.
- Centralized guarded-result construction over `GuardedResultBase`.
- Added typed-code-first conflict classifiers with legacy message fallback.
- Tightened edit transient recovery by precomputing an expected post-image and
  only recovering when the daemon-visible file exactly matches that post-image.
- Updated API verb tests to use fake transports instead of monkey-patching
  `call_daemon_api`.
