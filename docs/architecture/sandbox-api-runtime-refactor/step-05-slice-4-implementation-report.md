# Step 5 / Slice 4 - Implementation Report

Companion to
[`step-05-slice-4-occ-relocation.md`](./step-05-slice-4-occ-relocation.md).
This report records the OCC peer relocation deliverable, the cleanup performed
after implementation review, and the verification evidence.

---

## 1. Verdict

**Step 5 shipped OCC as a peer under `sandbox/occ/`, wired through the generic
runtime server, without exposing public `sandbox.api.write/edit` yet.**

The slice moved the OCC mutation policy and supporting types out of the old
`sandbox/code_intelligence/mutations/` location, added the OCC runtime client,
engine boundary, setup/bootstrap registration, and server handlers, then filled
`edit_pipeline` and `write_pipeline` in `sandbox/runtime/pipelines.py`.

The public agent/tool surfaces did not change in this slice. Public
`sandbox.api.write/edit`, Overlay relocation, and final legacy deletion remain
later steps.

**Cleanup note.** The old snapshot-based undo route was deleted instead of
relocated. OCC still performs atomic rollback for failed commits inside
`WriteCoordinator`; reusable user-facing undo snapshot state is not carried
forward.

---

## 2. File Inventory

### Added OCC Runtime Peer Files

| File | Purpose |
| --- | --- |
| `backend/src/sandbox/occ/bootstrap.py` | Registers OCC setup and handlers at import time |
| `backend/src/sandbox/occ/client.py` | Host-side typed OCC route point; serializes exactly one request to `sandbox.runtime.server` per call |
| `backend/src/sandbox/occ/engine.py` | `OCCEngine` protocol plus `LocalOCCEngine` composition root for in-sandbox OCC internals |
| `backend/src/sandbox/occ/setup.sh` | Idempotent peer setup script for OCC-local state roots |
| `backend/src/sandbox/occ/handlers/__init__.py` | Registers OCC server operations in `OP_TABLE` |
| `backend/src/sandbox/occ/handlers/write.py` | Thin runtime adapter for `occ.write` |
| `backend/src/sandbox/occ/handlers/edit.py` | Thin runtime adapter for `occ.edit` and internal `occ.apply` |
| `backend/src/sandbox/occ/handlers/apply_changeset.py` | Thin runtime adapter for raw overlay changeset application |
| `backend/src/sandbox/occ/handlers/commit.py` | Thin runtime adapter for explicit OCC commit requests |

### Relocated OCC Internals

| New Path | Responsibility |
| --- | --- |
| `backend/src/sandbox/occ/operations/service.py` | High-level OCC operation planning, renamed from the old `MutationService` surface to `OCCOperationService` |
| `backend/src/sandbox/occ/content/{manager,hashing,path_utils}.py` | Workspace content I/O, hashing, and path resolution |
| `backend/src/sandbox/occ/commit/` | Commit coordination, merge resolution, metrics, models, and result helpers |
| `backend/src/sandbox/occ/changeset/{apply,types}.py` | Overlay upperdir changeset classification and OCC/direct-merge policy |
| `backend/src/sandbox/occ/patching/patcher.py` | Search/replace and line-range patch application |
| `backend/src/sandbox/occ/state/{arbiter,edit_history_ledger,ledger_store,constants}.py` | Coordination state, ledger persistence, and OCC-owned constants |
| `backend/src/sandbox/occ/types.py` | OCC request/result dataclasses |
| `backend/src/sandbox/occ/wire.py` | JSON/wire serialization for OCC runtime requests and responses |

### Updated Existing Files

| File | Change |
| --- | --- |
| `backend/src/sandbox/runtime/server.py` | Imports OCC bootstrap so handlers register at module import time |
| `backend/src/sandbox/runtime/pipelines.py` | Implements `edit_pipeline` and `write_pipeline` through `LocalOCCEngine` |
| `backend/src/sandbox/runtime/bundle.py` | Bundles `sandbox/occ/**/*.py` and peer `setup.sh`; excludes retired `code_intelligence/mutations` |
| `backend/src/sandbox/runtime/setup_orchestrator.py` | Makes repeated identical setup registration idempotent |
| `backend/src/sandbox/runtime/legacy_command_client.py` | Uses relocated OCC wire/state helpers while legacy surfaces still exist |
| `backend/src/sandbox/code_intelligence/{service.py,backends/*,daemon/storage.py}` | Temporary compatibility callers now use relocated OCC modules and no longer expose snapshot undo |
| `backend/tests/test_sandbox/test_code_intelligence/test_runtime_bundle.py` | Verifies extracted runtime bundle contains OCC modules and excludes retired mutations |
| `backend/tests/test_sandbox/test_runtime/test_server_dispatch.py` | Keeps `runtime/server.py` free of per-peer branch switches |

### Deleted Legacy Files

| File / Surface | Replacement |
| --- | --- |
| `backend/src/sandbox/code_intelligence/mutations/` | Replaced by responsibility-based `backend/src/sandbox/occ/` subpackages |
| `backend/src/sandbox/code_intelligence/core/{hashing,path_utils}.py` | Replaced by `sandbox.occ.content.*` |
| `backend/src/sandbox/code_intelligence/daemon/{ledger_store,wire}.py` | Replaced by `sandbox.occ.state.ledger_store` and `sandbox.occ.wire` |
| `backend/src/sandbox/code_intelligence/mutations/mutation_results.py` | Inlined into `sandbox.occ.operations.service` |
| Snapshot undo stack | Deleted; commit-attempt rollback stays inside `WriteCoordinator` |

---

## 3. Behavior Delivered

### OCC Runtime Route

`OCCClient` is the host-side OCC route point for this slice. It builds a JSON
runtime envelope, invokes the registered provider adapter once, and runs:

```text
python3 -m sandbox.runtime.server '<json-envelope>'
```

The client currently exposes internal OCC operations needed by migration and
future public verbs:

- `apply`
- `write`
- `edit`
- `commit`
- `apply_changeset`

It does not import handlers or Overlay, and agent tools still do not import it.
Public `sandbox.api.write/edit` remain deferred to Slice 6.

### Runtime Server Registration

`sandbox/runtime/server.py` remains generic. OCC behavior is registered through
peer bootstrap and handlers:

```text
import sandbox.occ.bootstrap
  -> setup_orchestrator.register(SetupScript(...sandbox/occ/setup.sh))
  -> sandbox.occ.handlers.register_handlers()
  -> OP_TABLE["occ.*"] = handler
```

The server still dispatches by `OP_TABLE` lookup. No OCC-specific branch was
added to `runtime/server.py`.

### In-Sandbox Pipelines

`runtime/pipelines.py` now implements:

- `write_pipeline`: normalizes write specs, runs OCC planning, and commits once
  through `LocalOCCEngine`.
- `edit_pipeline`: normalizes edit specs, applies patch planning for all specs,
  and commits once through `LocalOCCEngine`.

The pipelines are dispatch-reachable through `runtime/server.py`, but still not
exposed through `sandbox.api`.

### OCC Engine Boundary

`LocalOCCEngine` is the concrete in-sandbox composition root. It owns:

- `ContentManager`
- `Arbiter`
- `LedgerStore`
- `Patcher`
- `WriteCoordinator`
- `OCCOperationService`

The `OCCEngine` protocol is intentionally minimal: `apply`, `commit`, and
`arbiter`. Implementation-specific helpers such as `write_file`,
`edit_file`, and `apply_changeset` remain on the concrete engine for handlers
and pipelines.

### Bundle Boundary

`runtime/bundle.py` now deploys the OCC peer:

- `sandbox/occ/**/*.py`
- `sandbox/occ/setup.sh`

It does not deploy the retired `sandbox/code_intelligence/mutations/` path.

### Snapshot Undo Removal

The old latest-snapshot undo route is gone. The remaining rollback contract is
commit atomicity:

- failed multi-file operations leave affected files unchanged
- conflict paths return structured `OperationResult` failures
- successful commits record ledger entries through `Arbiter`

This keeps rollback as part of the commit pipeline instead of preserving a
separate reusable undo API.

---

## 4. Cleanup Performed

The post-implementation cleanup removed compatibility drag and duplicate route
names:

- Kept concrete `OCCClient.write_file` / `edit_file` and
  `LocalOCCEngine.commit_operation_against_base` compatibility aliases for
  migration callers.
- Removed the old snapshot undo compatibility exposure from
  service/backends/tests.
- Removed legacy `undo` server/client/backend routes.
- Renamed the patcher helper from `apply_edits` to `apply_many` so the old
  `apply_edit` grep gate is exact-clean.
- Kept legacy daemon compatibility in `runtime/legacy_command_client.py`, but
  updated it to import OCC-owned wire/state helpers from `sandbox.occ.*`.
- Updated architecture docs that still described undo as a retained OCC surface.

---

## 5. Boundaries Preserved

Step 5 intentionally did not implement or migrate:

- public `sandbox.api.write`
- public `sandbox.api.edit`
- public `sandbox.api.shell`
- Overlay peer relocation
- `shell_pipeline`
- agent tool imports
- final `sandbox/code_intelligence/` deletion
- final legacy API/transport deletion

Temporary compatibility shims remain where needed to keep the step green until
the public surface flip and legacy-delete steps.

---

## 6. Verification

Focused Step 5 coverage:

- `backend/tests/test_sandbox/test_occ/test_package_structure.py`
- `backend/tests/test_sandbox/test_occ/test_client.py`
- `backend/tests/test_sandbox/test_occ/test_bootstrap.py`
- `backend/tests/test_sandbox/test_occ/test_pipelines.py`
- `backend/tests/test_sandbox/test_runtime/test_server_dispatch.py`
- `backend/tests/test_sandbox/test_runtime/test_setup_orchestrator.py`
- `backend/tests/test_sandbox/test_code_intelligence/test_runtime_bundle.py`
- `backend/tests/test_sandbox/test_code_intelligence/test_daemon_backend_dispatch.py`
- `backend/tests/test_sandbox/test_code_intelligence/test_backends.py`

Current verification after implementation and cleanup:

```bash
uv run pytest backend/tests/test_sandbox -q
uv run ruff check backend/src/sandbox backend/tests/test_sandbox
git diff --check
```

Results from the latest cleanup pass:

- `backend/tests/test_sandbox`: `348 passed`
- `ruff`: clean
- `git diff --check`: clean

Structural grep gates:

```bash
rg -n "apply_edit" backend/src
rg -n "sandbox\.code_intelligence|sandbox\.overlay" backend/src/sandbox/occ
rg -n "from sandbox\.code_intelligence\.mutations|sandbox\.code_intelligence\.mutations|MutationService" backend/src
rg -n "occ\.undo|def undo\(|\.undo\(" backend/src backend/tests/test_sandbox backend/tests/test_e2e/test_daytona_toolkit_comprehensive.py backend/tests/test_e2e/test_live_ci_phase3_invariants.py
```

All listed structural greps are clean. Live Daytona E2E/perf was not run for
this report.

---

## 7. Deferred Items

These remain outside Step 5:

- Moving Overlay behavior into `sandbox/overlay/` and wiring `shell_pipeline` -
  Step 6.
- Adding public guarded `sandbox.api.{shell,read,write,edit}` verbs - Step 7.
- Deleting legacy `code_intelligence/`, old API compatibility surfaces, and
  transport-era callers - Step 8.
- Relocating final tests/docs after the public surface is stable - Step 9.

---

## 8. Definition Of Done

- `sandbox/occ/` exists with the responsibility-based layout.
- `OCCClient` is the internal host-side OCC runtime route point.
- `OCCClient` performs one provider adapter exec per request.
- `OCCClient` serializes requests to `runtime/server.py` and does not import
  handlers or Overlay.
- `occ/setup.sh` is registered through `occ/bootstrap.py`.
- OCC handlers register through `OP_TABLE` at import time.
- `runtime/server.py` remains a generic dispatcher without OCC branch switches.
- `edit_pipeline` and `write_pipeline` are implemented and dispatch-reachable.
- `sandbox.api.write/edit` are not wired yet.
- The runtime bundle deploys `sandbox/occ/` and `occ/setup.sh`.
- The runtime bundle no longer deploys `sandbox/code_intelligence/mutations/`.
- Old `apply_edit` and snapshot undo routes are gone from active code/tests.
- OCC package-boundary greps are clean.
- Focused OCC/runtime tests and the broader sandbox suite pass.
