# Step 3 / Slice 2 - Implementation Report

Companion to
[`step-03-slice-2-raw-exec.md`](./step-03-slice-2-raw-exec.md).
This report records the Step 3 raw-exec deliverable, the live checkout state
after the Step 4 follow-up, and the verification evidence.

---

## 1. Verdict

**Step 3 shipped as the narrow unguarded raw-exec migration.**

The slice introduced `sandbox.api.raw_exec` over the provider adapter registry,
moved host-side runtime bundle upload to `sandbox/runtime/bundle.py`, and
migrated only the lifecycle/debug call sites that need unguarded command
execution by sandbox id.

The public agent/tool surfaces did not change in this slice. Agent shell/file
tools, audited sandbox APIs, OCC content management, code-intelligence command
semantics, byte I/O, checked writes, `SandboxTransport`, and `DaytonaTransport`
remained on their legacy paths.

**Current checkout note.** Step 4 has since landed runtime scaffolding and
deleted the temporary `code_intelligence/daemon/launcher.py` bridge. That
post-Step-4 cleanup does not change the Step 3 deliverable: `raw_exec` is still
the host-side unguarded primitive, and `runtime/bundle.py` still owns bundle
upload.

---

## 2. File Inventory

### Added In Step 3

| File | Purpose |
| --- | --- |
| `backend/src/sandbox/api/raw_exec.py` | Thin async wrapper around `sandbox.providers.registry.get_adapter(sid).exec(...)` |
| `backend/src/sandbox/runtime/__init__.py` | Runtime package marker |
| `backend/src/sandbox/runtime/bundle.py` | Host-side deterministic tarball builder and idempotent uploader through `raw_exec` |
| `backend/tests/test_sandbox/test_api/test_raw_exec.py` | Raw-exec delegation and unknown-id behavior |
| `backend/tests/test_sandbox/test_api/test_raw_exec_import_allowlist.py` | AST import allowlist for the unguarded primitive |
| `backend/tests/test_sandbox/test_runtime/test_bundle.py` | Bundle upload uses `raw_exec` and preserves marker/hash idempotency |

### Updated Callers

| File | Change |
| --- | --- |
| `backend/src/sandbox/lifecycle/proxy.py` | `SandboxProxy.ensure_git` uses `raw_exec` after adapter registration |
| `backend/src/sandbox/lifecycle/service.py` | Lifecycle create/start/recovery paths register provider adapters before raw probes and bootstrap work |
| `backend/src/sandbox/lifecycle/workspace.py` | Eager CI runtime upload/bootstrap is sandbox-id based and no longer requires a passed transport |
| `backend/src/server/routers/sandboxes.py` | Debug sandbox exec route uses `raw_exec` after ensuring the sandbox is running |
| `backend/tests/test_sandbox/test_eager_ci_bootstrap.py` | Covers sandbox-id based eager upload/bootstrap |
| `backend/tests/test_sandbox/test_service.py` | Covers raw-exec based git bootstrap and lifecycle service wiring |
| `backend/tests/test_sandbox/test_code_intelligence/test_runtime_bundle.py` | Keeps bundle layout and extraction behavior covered through the legacy CI path |

### Kept By Design

| File / Surface | Reason |
| --- | --- |
| `backend/src/sandbox/api/models.py` | Existing request/result models stayed intact; `RawExecResult` was reused |
| `backend/src/sandbox/api/transport.py` | Legacy wide `SandboxTransport` still needed for CI and audited callers |
| `backend/src/sandbox/daytona/transport.py` | Daytona transport still owns provider-specific process/file mechanics |
| Agent tool imports | Step 3 is not the public verb API slice |
| Audited sandbox APIs and OCC internals | Guarded write/shell paths are migrated in later slices |

---

## 3. Behavior Delivered

### `raw_exec` Is A Thin Primitive

`sandbox.api.raw_exec` performs no audit, retry, shell policy, logging, or
lifecycle mutation. It resolves the registered provider adapter and delegates:

```text
raw_exec(sandbox_id, command, cwd=None, timeout=None)
  -> get_adapter(sandbox_id).exec(sandbox_id, command, cwd=cwd, timeout=timeout)
```

Unknown sandbox ids surface as `KeyError`, matching the provider registry
contract from Step 2.

### Bundle Upload Moved To `sandbox/runtime/bundle.py`

`runtime/bundle.py` owns the host-side upload path:

- deterministic tar/gzip bundle construction
- stable `bundle_hash`
- `.bundle-hash` warm-path marker check
- chunked base64 upload through repeated unguarded exec calls
- extraction under `/tmp/eos-ci-runtime`

The current post-Step-4 implementation also keeps host-only runtime modules out
of the extracted tarball. `sandbox/runtime/bundle.py` and
`sandbox/api/raw_exec.py` are host-side upload/adapter modules and are not
bundled into the sandbox-local runtime payload.

### Importers Are Allowlisted

The raw-exec primitive is intentionally import-restricted. In the current
checkout, the allowlist is:

- `sandbox/runtime/bundle.py`
- `sandbox/runtime/setup_orchestrator.py` (added by Step 4)
- `sandbox/lifecycle/*`
- `server/routers/sandboxes.py`

The AST allowlist test fails if a new production module imports
`sandbox.api.raw_exec` without an explicit allowlist update.

---

## 4. Cleanup And Follow-Up State

The Step 3 review and Step 4 cleanup removed legacy coupling that no longer
matched the raw-exec boundary:

- `bootstrap_in_sandbox_ci_runtime(...)` and
  `bootstrap_upload_runtime_bundle(...)` no longer accept unused transport
  parameters.
- The runtime tarball excludes host-only provider lookup modules:
  `sandbox/api/raw_exec.py`, `sandbox/runtime/bundle.py`, and
  `sandbox/providers/*`.
- The temporary `code_intelligence/daemon/launcher.py` bridge from the Step 3
  transition was deleted in Step 4. Legacy daemon compatibility now lives in
  `sandbox/runtime/legacy_command_client.py`.

This report therefore treats `daemon/launcher.py` as a Step 3 transition
artifact, not as a current live file.

---

## 5. Boundaries Preserved

Step 3 intentionally did not implement or migrate:

- `runtime/server.py` dispatch
- `runtime/pipelines.py`
- `runtime/setup_orchestrator.py`
- OCC/Overlay peer relocation
- public `sandbox.api.{shell,read,write,edit}` verbs
- agent tool imports
- audited shell/write/edit behavior
- `SandboxTransport` and `DaytonaTransport` deletion

Step 4 has since added runtime server/setup scaffolding. Those files are
documented in the Step 4 report/slice docs, not as Step 3 deliverables.

---

## 6. Verification

Focused Step 3 test coverage:

- `backend/tests/test_sandbox/test_api/test_raw_exec.py`
- `backend/tests/test_sandbox/test_api/test_raw_exec_import_allowlist.py`
- `backend/tests/test_sandbox/test_runtime/test_bundle.py`
- `backend/tests/test_sandbox/test_code_intelligence/test_runtime_bundle.py`
- `backend/tests/test_sandbox/test_eager_ci_bootstrap.py`
- `backend/tests/test_sandbox/test_service.py`

Current verification after the Step 4 cleanup pass:

```bash
uv run pytest backend/tests/test_sandbox -q
uv run ruff check backend/src backend/tests
git diff --check
```

Results from the latest cleanup pass:

- `backend/tests/test_sandbox`: `337 passed`
- `ruff`: clean
- `git diff --check`: clean

Structural gates:

```bash
rg -n "from sandbox\.api\.raw_exec|import sandbox\.api\.raw_exec" backend/src
rg -n "sandbox\.code_intelligence\.daemon\.(command|launcher)|from sandbox\.code_intelligence\.daemon\.(command|launcher)" backend/src backend/tests
```

The raw-exec grep is constrained by
`test_raw_exec_import_allowlist.py`. The deleted daemon command/launcher grep
is clean for production and sandbox tests in the current checkout.

Live Daytona E2E/perf was not run for this documentation report.

---

## 7. Deferred Items

These remain outside Step 3:

- Moving command dispatch from the old daemon switch into
  `sandbox/runtime/server.py` - Step 4.
- Adding setup orchestration and pipeline stubs - Step 4.
- Moving OCC into `sandbox/occ/` - Step 5.
- Moving Overlay into `sandbox/overlay/` - Step 6.
- Adding public guarded sandbox verbs - Step 7.
- Deleting `code_intelligence/`, `SandboxTransport`, `DaytonaTransport`, and
  old API compatibility surfaces - Step 8.

---

## 8. Definition Of Done

- `sandbox.api.raw_exec` exists and delegates through the provider adapter
  registry.
- Host-side runtime bundle upload is owned by `sandbox/runtime/bundle.py`.
- Only allowlisted runtime/lifecycle/debug code imports `raw_exec`.
- Eager lifecycle bootstrap uses sandbox id, not a passed legacy transport.
- The extracted runtime bundle excludes host-only raw-exec/provider adapter
  modules.
- Existing API models and legacy transport surfaces remain available for later
  slices.
- Focused raw-exec/bundle/lifecycle tests and broader sandbox tests pass.
