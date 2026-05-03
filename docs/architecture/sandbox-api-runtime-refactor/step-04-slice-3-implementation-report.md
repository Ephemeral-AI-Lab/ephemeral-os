# Step 4 / Slice 3 - Implementation Report

Companion to
[`step-04-slice-3-runtime-scaffolding.md`](./step-04-slice-3-runtime-scaffolding.md).
This report records the Step 4 runtime-scaffolding deliverable, the cleanup
performed after implementation review, and the verification evidence.

---

## 1. Verdict

**Step 4 shipped as shared runtime scaffolding, not as a public sandbox API
change.**

The slice replaced the old code-intelligence daemon command switch with a
generic `sandbox/runtime/server.py` dispatcher, added the setup-script registry
contract for future peers, introduced empty runtime pipeline placeholders, and
isolated temporary legacy daemon compatibility in
`sandbox/runtime/legacy_command_client.py`.

The public agent/tool surfaces did not change in this slice. OCC relocation,
Overlay relocation, public guarded verbs, and legacy transport deletion remain
later steps.

**Scope note.** Step 4 did not move OCC-owned state into `sandbox/runtime/`.
Later or in-progress OCC peer work belongs to Step 5 and is intentionally not
treated as a Step 4 deliverable in this report.

---

## 2. File Inventory

### Added Runtime Scaffolding

| File | Purpose |
| --- | --- |
| `backend/src/sandbox/runtime/server.py` | Generic in-sandbox JSON dispatcher with `OP_TABLE`, `register_op`, structured errors, and no legacy `{ok, result, error}` envelope |
| `backend/src/sandbox/runtime/setup_orchestrator.py` | Host-side `SetupScript` and ordered `SetupRegistry` contract for peer-owned bundled `setup.sh` scripts |
| `backend/src/sandbox/runtime/pipelines.py` | Placeholder `shell_pipeline`, `edit_pipeline`, and `write_pipeline` functions for later peer slices |
| `backend/src/sandbox/runtime/legacy_command_client.py` | Temporary compatibility adapter for existing code-intelligence daemon callers |

### Updated Existing Files

| File | Change |
| --- | --- |
| `backend/src/sandbox/runtime/bundle.py` | Bundle manifest now includes runtime server/setup/pipeline/legacy compatibility modules and excludes host-only `bundle.py` and `raw_exec.py` |
| `backend/src/sandbox/code_intelligence/daemon/client.py` | Compatibility shim that re-exports `DaemonCommandClient` and `DaemonCommandError` from the temporary runtime legacy client |
| `backend/tests/test_sandbox/test_code_intelligence/test_runtime_bundle.py` | Verifies runtime bundle contents and clean extracted-bundle imports |
| `backend/tests/test_sandbox/test_code_intelligence/test_daemon_backend.py` | Keeps legacy daemon behavior passing through the compatibility route |
| `backend/tests/test_sandbox/test_code_intelligence/test_daemon_client_process_exec.py` | Covers the transport/process-exec compatibility client path |
| `backend/tests/test_sandbox/test_eager_ci_bootstrap.py` | Covers eager runtime upload/bootstrap after the bundle move |
| `backend/tests/test_sandbox/test_service.py` | Covers lifecycle service wiring against the new runtime upload path |

### Deleted Legacy Files

| File | Replacement |
| --- | --- |
| `backend/src/sandbox/code_intelligence/daemon/command.py` | Replaced by `sandbox/runtime/server.py` plus temporary legacy command adapter |
| `backend/src/sandbox/code_intelligence/daemon/launcher.py` | Replaced by `sandbox/runtime/bundle.py` upload/bootstrap and the compatibility client |

### Kept Out Of Runtime

Step 4 keeps the runtime package limited to shared daemon/server support.
Legacy daemon state and OCC policy are not folded into `sandbox/runtime/`.
The current compatibility path still imports legacy/OCC behavior only through
the temporary adapter, not through the new generic server dispatcher.

---

## 3. Behavior Delivered

### Generic Runtime Dispatcher

`sandbox/runtime/server.py` is the deployed in-sandbox dispatcher shape for
later public verbs:

```text
JSON envelope -> dispatch_json -> dispatch_envelope -> OP_TABLE[op](args)
```

The server:

- accepts a JSON object from `argv[0]` or stdin
- requires a non-empty string `op`
- treats missing `args` as an empty object
- rejects non-object envelopes and non-object args as `invalid_envelope`
- returns `unknown_op` when no handler is registered
- allows peer bootstrap modules to register handlers with `register_op`
- serializes handler return values directly as JSON-safe results
- returns structured errors with `success: false`, `warnings`, `timings`, and
  `error`

The server intentionally does not emit the old daemon `{ok, result, error}`
envelope. That compatibility is isolated in
`sandbox/runtime/legacy_command_client.py`.

### Setup Registry Contract

`setup_orchestrator.py` defines the host-side setup contract for later peers:

```text
SetupScript(name, package, relative_path)
SetupRegistry.register(setup_script)
SetupRegistry.run_all(sandbox_id)
```

The implementation validates that each setup path is bundle-relative and points
to `setup.sh`. `run_all` is a no-op with an empty registry. Once scripts are
registered, it uploads the runtime bundle first and then executes each peer
script in registration order from `/tmp/eos-ci-runtime` using `bash
<relative_path>`.

The script body belongs in the peer-owned bundled `setup.sh`; Step 4 does not
embed OCC or Overlay setup logic in Python strings.

### Temporary Legacy Compatibility

Existing daemon callers keep importing:

```python
from sandbox.code_intelligence.daemon.client import DaemonCommandClient
```

That module is now a shim. The old command behavior lives in
`sandbox/runtime/legacy_command_client.py` and preserves the legacy daemon
envelope only for existing compatibility callers. It is not the public runtime
client and is scheduled for deletion with the legacy surface.

### Bundle Boundary

`runtime/bundle.py` remains host-side bootstrap code. The extracted sandbox
bundle includes the runtime server/setup/pipeline/legacy modules needed inside
the sandbox, while excluding host-only upload modules such as
`sandbox/runtime/bundle.py` and `sandbox/api/raw_exec.py`.

The bundle still includes legacy modules required by the temporary compatibility
client. That is intentional for Step 4; full legacy deletion is Step 8.

---

## 4. Cleanup Performed

The post-implementation cleanup removed stale and legacy scaffolding that no
longer matched the Step 4 boundary:

- Deleted `code_intelligence/daemon/command.py`.
- Deleted `code_intelligence/daemon/launcher.py`.
- Removed old launcher exports and import paths from sandbox tests.
- Removed stale `DaemonLauncher`, `DaemonUnavailable`, and `legacy_main`
  references from the Step 4 runtime surface.
- Kept the compatibility behavior in one temporary module instead of spreading
  legacy envelope handling into `runtime/server.py`.
- Kept `runtime/pipelines.py` as unreachable placeholder functions, so later
  OCC/Overlay slices have stable names without changing agent behavior early.

---

## 5. Boundaries Preserved

Step 4 intentionally did not implement or migrate:

- public `sandbox.api.{shell,read,write,edit}` verbs
- OCC request handling in `sandbox/occ/`
- Overlay request handling in `sandbox/overlay/`
- agent tool imports
- audited shell/write/edit behavior
- `SandboxTransport` or `DaytonaTransport` deletion
- final `code_intelligence/` deletion
- generic public `sandbox/runtime/client.py`

The only host-to-guest runtime convention introduced here is the future server
contract: peer clients will serialize one request to `runtime/server.py` and
perform exactly one adapter exec call in later slices.

---

## 6. Verification

Focused Step 4 test coverage:

- `backend/tests/test_sandbox/test_runtime/test_server_dispatch.py`
- `backend/tests/test_sandbox/test_runtime/test_setup_orchestrator.py`
- `backend/tests/test_sandbox/test_code_intelligence/test_runtime_bundle.py`
- `backend/tests/test_sandbox/test_code_intelligence/test_daemon_backend.py`
- `backend/tests/test_sandbox/test_code_intelligence/test_daemon_client_process_exec.py`
- `backend/tests/test_sandbox/test_eager_ci_bootstrap.py`
- `backend/tests/test_sandbox/test_service.py`

Current verification after the runtime-scaffolding cleanup pass:

```bash
uv run pytest backend/tests/test_sandbox -q
uv run ruff check backend/src backend/tests
git diff --check
```

Results from the latest cleanup pass:

- `backend/tests/test_sandbox`: `337 passed`
- `ruff`: clean
- `git diff --check`: clean

Structural legacy-removal gate:

```bash
rg -n "sandbox\.code_intelligence\.daemon\.(command|launcher)|from sandbox\.code_intelligence\.daemon\.(command|launcher)|\bDaemonLauncher\b|\bDaemonUnavailable\b|\blegacy_main\b" backend/src/sandbox backend/tests/test_sandbox backend/tests/test_e2e/test_live_ci_phase3_invariants.py
```

The structural grep is clean in the Step 4 runtime/sandbox surface. Live
Daytona E2E/perf was not run for this documentation report.

---

## 7. Deferred Items

These remain outside Step 4:

- Moving OCC behavior into `sandbox/occ/` and wiring `edit_pipeline` /
  `write_pipeline` - Step 5.
- Moving Overlay behavior into `sandbox/overlay/` and wiring `shell_pipeline` -
  Step 6.
- Adding public guarded sandbox verbs - Step 7.
- Deleting legacy `code_intelligence/`, old API compatibility surfaces, and
  transport-era callers - Step 8.
- Relocating final tests/docs after the public surface is stable - Step 9.

---

## 8. Definition Of Done

- `runtime/server.py` exists as the deployed generic dispatcher.
- Dispatch is table-driven through `OP_TABLE` and `register_op`.
- Bad JSON, invalid envelopes, unknown ops, and handler exceptions return
  structured errors.
- `runtime/server.py` does not emit the old daemon `{ok, result, error}`
  envelope.
- `SetupScript` and `SetupRegistry` establish the peer `setup.sh` contract.
- Empty setup registries are no-ops; populated registries run scripts in order
  after bundle upload.
- `runtime/pipelines.py` exports placeholder pipeline names and remains
  unreachable from agent paths.
- Existing `code_intelligence.daemon.client` imports still resolve through the
  compatibility shim.
- `code_intelligence/daemon/command.py` and `daemon/launcher.py` are gone.
- Runtime bundle contents include the new runtime files and exclude host-only
  upload modules.
- Focused runtime, bundle, compatibility, lifecycle, and broader sandbox tests
  pass.
