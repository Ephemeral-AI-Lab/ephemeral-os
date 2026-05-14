# Sandbox API Remediation Implementation Report

Started: 2026-05-14

Source review: `.planning/code-reviews/sandbox-api-REVIEW.md`

## Phase Status

| Phase | Status | Evidence |
| --- | --- | --- |
| 1. API contracts and shared helpers | Done | `uv run pytest backend/tests/unit_test/test_sandbox/test_api/test_payload_helpers.py backend/tests/unit_test/test_sandbox/test_api/test_transport_protocol.py -q` -> 7 passed |
| 2. Tool verb refactor | Done | `uv run pytest backend/tests/unit_test/test_sandbox/test_api/test_raw_exec.py backend/tests/unit_test/test_sandbox/test_api/test_read.py backend/tests/unit_test/test_sandbox/test_api/test_write.py backend/tests/unit_test/test_sandbox/test_api/test_shell.py backend/tests/unit_test/test_sandbox/test_api/test_edit.py backend/tests/unit_test/test_sandbox/test_api/test_audit_emission.py -q` -> 19 passed |
| 3. Facade and default client | Done | `uv run pytest backend/tests/unit_test/test_sandbox/test_api/test_facade.py backend/tests/unit_test/test_sandbox/test_api/test_contract.py -q` -> 32 passed |
| 4. Lifecycle/discovery split | Done | `uv run pytest backend/tests/unit_test/test_sandbox/test_api/test_status.py backend/tests/unit_test/test_sandbox/test_api/test_contract.py backend/tests/unit_test/test_sandbox/test_import_fence.py -q` -> 64 passed |
| 5. Daemon version and error taxonomy | Done | `uv run pytest backend/tests/unit_test/test_sandbox/test_api/test_payload_helpers.py backend/tests/unit_test/test_sandbox/test_api/test_transport_protocol.py backend/tests/unit_test/test_sandbox/test_api/test_read.py backend/tests/unit_test/test_sandbox/test_api/test_write.py backend/tests/unit_test/test_sandbox/test_api/test_shell.py backend/tests/unit_test/test_sandbox/test_api/test_edit.py backend/tests/unit_test/test_sandbox/test_api/test_facade.py backend/tests/unit_test/test_sandbox/test_command_exec/test_write_edit_dispatch.py backend/tests/unit_test/test_sandbox/test_daemon/test_routing_invariants.py -q` -> 48 passed |
| 6. Hygiene and closeout | Done | `.DS_Store` is ignored/untracked; sandbox API/import-fence slice -> 94 passed; full sandbox API directory -> 106 passed |

## Notes

- Existing dirty worktree files under `backend/src/sandbox/layer_stack*`,
  `backend/src/sandbox/occ*`, and related tests are unrelated to this
  remediation and will be left untouched.
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
  `sandbox.api._impl`, then removed the stale `sandbox.api.tool`
  compatibility package in the cleanup pass.
- Added a shared audited execution wrapper for read/write/edit/shell/raw-exec
  success, conflict, and failure publishing.
- Routed read/write/edit/shell through the injected `SandboxTransport` seam.
- Centralized guarded-result construction over `GuardedResultBase`.
- Added typed-code-first conflict classifiers with legacy message fallback.
- Tightened edit transient recovery by precomputing an expected post-image and
  only recovering when the daemon-visible file exactly matches that post-image.
- Added `SandboxRequestBase` for shared caller/description plumbing and
  `ConflictInfo` factories for common guarded conflict shapes.
- Moved write/edit transient handling through a shared recovery helper; write
  recovery only succeeds when a pre-read proves the post-failure content changed
  to the requested content.
- Updated API verb tests to use fake transports instead of monkey-patching
  `call_daemon_api`.

## Phase 3 Details

- Rebuilt `SandboxClient` around injected `transport`, `lifecycle`, and
  `audit_sink` dependencies.
- Removed all method-local imports from the facade.
- Moved package-level default wrappers into `sandbox.api.default`.
- Removed the private package `_client`; package-level functions now call an
  internal default client at invocation time rather than binding singleton
  methods during import.
- Removed public default-client mutation helpers that had no repo callers.
- Updated contract tests to lock the `_impl` implementation package and removed
  compatibility modules.

## Phase 4 Details

- Split the overloaded `sandbox.api.status` owner into
  `sandbox.api.lifecycle`, `sandbox.api.discovery`, `sandbox.api.preview_urls`,
  and `sandbox.api.defaults`.
- Moved provider/plugin lifecycle orchestration into `sandbox.host.lifecycle`.
- Removed the stale `sandbox.api.status` compatibility facade.
- Updated lifecycle/discovery tests to patch the new owner modules instead of
  the old status god-module.

## Phase 5 Details

- Added versioned public daemon operation constants:
  `api.v1.read_file`, `api.v1.write_file`, `api.v1.edit_file`, and
  `api.v1.shell`.
- Updated public API clients to dispatch through versioned op names.
- Registered daemon aliases for both legacy `api.*` names and new `api.v1.*`
  names.
- Added typed-code-first conflict classifier tests while retaining legacy
  message fallback.

## Phase 6 Details

- Verified the review's `.DS_Store` concern: the file is present locally but is
  ignored and not tracked by git, so no repo content change is needed.
- Ran the safe sandbox API/import-fence slice:
  `uv run pytest backend/tests/unit_test/test_sandbox/test_api/test_audit_emission.py backend/tests/unit_test/test_sandbox/test_api/test_boundary.py backend/tests/unit_test/test_sandbox/test_api/test_contract.py backend/tests/unit_test/test_sandbox/test_api/test_daemon_client.py backend/tests/unit_test/test_sandbox/test_api/test_edit.py backend/tests/unit_test/test_sandbox/test_api/test_facade.py backend/tests/unit_test/test_sandbox/test_api/test_payload_helpers.py backend/tests/unit_test/test_sandbox/test_api/test_raw_exec.py backend/tests/unit_test/test_sandbox/test_api/test_read.py backend/tests/unit_test/test_sandbox/test_api/test_shell.py backend/tests/unit_test/test_sandbox/test_api/test_status.py backend/tests/unit_test/test_sandbox/test_api/test_transport_protocol.py backend/tests/unit_test/test_sandbox/test_api/test_write.py backend/tests/unit_test/test_sandbox/test_import_fence.py -q`
  -> 94 passed.
- Ran the full sandbox API directory:
  `uv run pytest backend/tests/unit_test/test_sandbox/test_api -q`
  -> 106 passed.

## Cleanup Pass - 2026-05-14

Changes:

- Removed the stale `sandbox.api._tool` package and all remaining imports of it.
- Removed the `sandbox.api.tool` compatibility package and `sandbox.api.status`
  compatibility facade.
- Kept `sandbox.api._impl` as the real implementation owner for direct internal
  tests.
- Removed public default-client mutation helpers with no repo callers:
  `default_client`, `set_default_client`, and `configure_default_client`.
- Removed the legacy `caller_audit_fields` helper; callers now use
  `SandboxCaller.audit_fields()` directly.
- Removed the stale `TRANSIENT_EDIT_ATTEMPTS` alias in favor of
  `TRANSIENT_MUTATION_ATTEMPTS`.
- Updated current docs/reports to use `sandbox.api` or the owning
  lifecycle/discovery modules instead of removed compatibility paths.
- Updated the public API contract test for the explicit `default.py` default
  client module and the `_impl` implementation package.
- Removed stale `LayerChange` imports in sandbox API/OCC tests.
- Added the final request-base/conflict-factory/recovery-policy cleanup for
  review items 4.4, 5.3, and 5.4.

Verification:

- `uv run pytest backend/tests/unit_test/test_sandbox/test_api/test_payload_helpers.py backend/tests/unit_test/test_sandbox/test_api/test_contract.py backend/tests/unit_test/test_sandbox/test_api/test_write.py backend/tests/unit_test/test_sandbox/test_api/test_edit.py backend/tests/unit_test/test_sandbox/test_api/test_shell.py -q` -> 54 passed.
- `uv run pytest backend/tests/unit_test/test_sandbox/test_api/test_audit_emission.py backend/tests/unit_test/test_sandbox/test_api/test_boundary.py backend/tests/unit_test/test_sandbox/test_api/test_contract.py backend/tests/unit_test/test_sandbox/test_api/test_daemon_client.py backend/tests/unit_test/test_sandbox/test_api/test_edit.py backend/tests/unit_test/test_sandbox/test_api/test_facade.py backend/tests/unit_test/test_sandbox/test_api/test_payload_helpers.py backend/tests/unit_test/test_sandbox/test_api/test_raw_exec.py backend/tests/unit_test/test_sandbox/test_api/test_read.py backend/tests/unit_test/test_sandbox/test_api/test_shell.py backend/tests/unit_test/test_sandbox/test_api/test_status.py backend/tests/unit_test/test_sandbox/test_api/test_transport_protocol.py backend/tests/unit_test/test_sandbox/test_api/test_write.py backend/tests/unit_test/test_sandbox/test_import_fence.py -q` -> 94 passed.
- `uv run pytest backend/tests/unit_test/test_sandbox/test_api -q` -> 106 passed.
- `uv run ruff check backend/src/sandbox/models.py backend/src/sandbox/api backend/src/sandbox/host/lifecycle.py backend/tests/unit_test/test_sandbox/test_api backend/tests/unit_test/test_sandbox/test_import_fence.py backend/tests/unit_test/test_engine/eval_agent_support.py` -> all checks passed.
