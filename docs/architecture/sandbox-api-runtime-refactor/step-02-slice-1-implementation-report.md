# Step 2 / Slice 1 - Implementation Report

Companion to
[`step-02-slice-1-provider-seam.md`](./step-02-slice-1-provider-seam.md).
This report records what actually landed for the provider seam, what stayed
legacy by design, and the verification evidence.

---

## 1. Verdict

**Step 2 ships as the narrow provider execution seam.**

The implementation adds a provider-neutral `ProviderAdapter` Protocol, a
process-local sandbox adapter registry, and a Daytona adapter that delegates
raw command execution to the existing Daytona transport. This creates the
future `get_adapter(sid).exec(...)` route without migrating shell tools, audited
APIs, code-intelligence internals, byte I/O, or checked-write paths yet.

The Step 2 commit is `f01ae5a1 Add sandbox provider seam`. It changed 28 files,
adding 476 lines and deleting 136 lines. Most added code is the new
`sandbox.providers` seam plus focused provider tests.

---

## 2. File Inventory

### Provider Seam

| File | Status | Purpose |
| --- | --- | --- |
| `backend/src/sandbox/providers/protocol.py` | added | Defines the narrow `ProviderAdapter` Protocol with `name` and async `exec(...)` |
| `backend/src/sandbox/providers/registry.py` | added | Process-local adapter registry: `register_adapter`, `get_adapter`, `dispose_adapter` |
| `backend/src/sandbox/providers/__init__.py` | added | Public exports for the provider seam |
| `backend/src/sandbox/providers/daytona/adapter.py` | added | Daytona implementation that delegates `exec(...)` to the existing transport |
| `backend/src/sandbox/providers/daytona/__init__.py` | added | Daytona adapter export |

### Lifecycle Wiring

| File | Status | Purpose |
| --- | --- | --- |
| `backend/src/sandbox/lifecycle/service.py` | updated | Registers a Daytona provider adapter after create/start/recovery and disposes it on delete |
| `backend/src/sandbox/lifecycle/workspace.py` | updated | Registers a provider adapter when rebuilding the legacy Daytona transport for pre-existing sandboxes |

### Legacy Transport Kept

| File | Status | Purpose |
| --- | --- | --- |
| `backend/src/sandbox/api/transport.py` | kept | Deprecated wide `SandboxTransport` Protocol for CI and audited API callers that still need byte I/O and checked writes |
| `backend/src/sandbox/daytona/transport.py` | kept | Existing Daytona transport implementation; still owns provider-specific process/file mechanics |

The current live cleanup keeps `SandboxTransport` structurally compatible with
`ProviderAdapter.exec(...)` but does not make it nominally inherit
`ProviderAdapter`. That avoids importing provider protocol code into the legacy
transport surface while preserving the same raw-exec signature.

### Tests

| File | Coverage |
| --- | --- |
| `backend/tests/test_sandbox/test_providers/test_registry.py` | Register/get/dispose round-trip, unknown sandbox `KeyError`, idempotent dispose, empty id rejection |
| `backend/tests/test_sandbox/test_providers/test_daytona_adapter.py` | Daytona adapter delegates `exec(...)` unchanged; legacy transport construction registers a provider adapter for pre-existing sandboxes |

---

## 3. Behavior Delivered

### ProviderAdapter Is Narrow

The provider seam exposes only the raw runtime/setup primitive needed by later
slices:

```text
ProviderAdapter {
  name: str
  async exec(sandbox_id, command, cwd=None, timeout=None) -> RawExecResult
}
```

It does not expose read, write, search, checked batch write, audited shell, or
code-intelligence operations.

### Registry Is Process Local

`sandbox.providers.registry` stores adapters by sandbox id in a process-local
dict guarded by a lock:

- `register_adapter(sandbox_id, adapter)` binds or replaces the adapter.
- `get_adapter(sandbox_id)` returns the adapter and raises `KeyError` when
  missing.
- `dispose_adapter(sandbox_id)` removes the binding and is idempotent.

This is intentionally not durable state. The workspace transport builder can
re-register a Daytona adapter for pre-existing sandboxes when the process
reconstructs a legacy transport.

### Daytona Adapter Delegates

`DaytonaProviderAdapter` composes the existing Daytona transport and forwards
`exec(...)` without reimplementing Daytona SDK process semantics. That keeps
Step 2 behavior-preserving: the new seam is a route point, not a new execution
engine.

### Lifecycle Registers The Seam

Daytona-backed sandboxes register a provider adapter when they are created,
started, or recovered. Deleting a sandbox disposes the registry entry.
Pre-existing sandbox contexts get an adapter when the legacy transport builder
creates a Daytona transport.

---

## 4. Boundaries Preserved

Step 2 intentionally did not migrate callers to `ProviderAdapter` yet.

Kept legacy surfaces:

- `SandboxTransport.read_bytes`
- `SandboxTransport.read_bytes_batch`
- `SandboxTransport.write_bytes`
- `SandboxTransport.apply_diff_batch_checked`
- `DaytonaTransport`
- audited sandbox APIs
- shell-command execution
- code-intelligence internals
- overlay/OCC runtime paths

This means Step 2 is independently revertible and does not change agent tool
behavior. The new seam exists so Step 3 can add `sandbox.api.raw_exec` over
`sandbox.providers.registry.get_adapter(sid).exec(...)`.

---

## 5. Verification

Focused verification commands for the provider seam:

- `uv run pytest backend/tests/test_sandbox/test_providers -q`
- `uv run pytest backend/tests/test_sandbox/test_eager_ci_bootstrap.py backend/tests/test_sandbox/test_lifecycle.py backend/tests/test_sandbox/test_workspace.py backend/tests/test_sandbox/test_api_contract.py backend/tests/test_sandbox/test_audited_sandbox_api.py -q`
- `uv run ruff check backend/src/sandbox/api backend/src/sandbox/lifecycle backend/src/sandbox/providers backend/tests/test_sandbox/test_providers`
- `git diff --check`

Structural gates:

```bash
rg "class ProviderAdapter" backend/src/sandbox/providers/protocol.py
rg "register_adapter|get_adapter|dispose_adapter" backend/src/sandbox/providers/registry.py
rg "DaytonaProviderAdapter" backend/src/sandbox
rg "from sandbox.providers" backend/src/sandbox/code_intelligence backend/src/tools || true
```

The intended structural outcome is that provider imports appear in lifecycle,
provider implementation, and later API/runtime seams only. Code-intelligence
and tool callers remain on the legacy surfaces until their scheduled slices.

---

## 6. Deferred Items

These remain outside Step 2:

- Adding `sandbox.api.raw_exec`. That is Step 3 / Slice 2.
- Moving runtime bundle upload to `sandbox/runtime/bundle.py`. That is Step 3.
- Moving the remaining daemon dispatch scaffolding to `sandbox/runtime/`. That
  is Step 4 / Slice 3.
- Relocating OCC and overlay to final peer packages. Those are later slices.
- Deleting `SandboxTransport` and `DaytonaTransport`. That waits for the
  delete-legacy slice after public verb APIs own callers.

---

## 7. Definition Of Done

- `ProviderAdapter` exists and is narrow.
- Daytona has a provider adapter that delegates raw exec to the existing
  transport.
- A single process-local registry owns adapter lookup.
- Sandbox lifecycle paths register and dispose provider adapters.
- Legacy wide transport and Daytona transport remain available.
- No agent, audited API, shell, overlay, OCC, or CI caller is migrated yet.
- Focused provider tests and sandbox lifecycle tests pass.
