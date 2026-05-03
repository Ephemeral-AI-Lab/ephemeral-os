# Sandbox API + Runtime Refactor — Execution Plan

**Status:** Draft, awaiting review
**Author:** session 2026-05-03
**Companion docs:**
- `occ-overlay-daemon-refactor.md` — earlier draft, superseded by the layering described here.
- `plugins-refactor.md` — query-side replacement; LSP plugins integrate via the runtime/setup mechanism defined below.

## 0. Motivation

Today the orchestrator reaches into the sandbox via `SandboxTransport.exec`, ships per-call Python snippets, and dispatches every OCC + Overlay op through one big switch in `daemon/command.py`. Four problems:

1. **Provider-leaky.** `process.exec` and base64-encoded snippets assume Daytona's shell-exec wire. Any future provider has to re-implement the entire dispatch path.
2. **Misleading umbrella.** `code_intelligence/` bundles two unrelated guardrails (file edits, shell side-effects) plus a query surface that's already migrating out.
3. **No clean public API.** Agent tools, runtime bootstrap, and debug paths all reach for the same low-level transport, with no enforced separation between guarded and un-guarded shell.
4. **Cross-peer coupling.** `OverlayCommandCommitter` calls into OCC directly today. This makes overlay's failure semantics implicit (overlay can half-commit before OCC sees the change) and prevents either peer from being tested in isolation. The composer pattern is the fix: overlay returns captured upperdir changes to the pipeline, and the pipeline — never overlay — decides whether to invoke OCC.

This refactor settles those by (a) defining a 5-verb public `sandbox.api`, (b) putting all gating logic behind a single deployed server script inside the sandbox, (c) reducing the provider seam to one method, and (d) making every guarded op explicit through an in-sandbox pipeline (`shell_pipeline`, `edit_pipeline`, `write_pipeline`).

## 1. End-state shape

### 1.0 Pipeline

```
┌─ host ─────────────────────────────────────────────────┐
│ agent tool                                             │
│    └─► sandbox.api.{shell,read,write,edit,raw_exec}    │
│            ├─► overlay/client.py     (shell)           │
│            ├─► occ/client.py         (write/edit)      │
│            └─► providers/<x>/adapter.exec(cmd)         │
│                    │ wire boundary                     │
└────────────────────┼───────────────────────────────────┘
                     ▼
┌─ guest (sandbox) ──────────────────────────────────────┐
│ runtime/server.py      (generic guarded dispatcher)    │
│    └─► runtime/pipelines.py::<verb>_pipeline           │
│            ├─► overlay/handlers/run.py    (shell only) │
│            └─► occ/handlers/{apply,commit,undo,...}    │
└────────────────────────────────────────────────────────┘
```

**Wire-trip contract.** Every `sandbox.api.{shell,write,edit}` call resolves to exactly one peer-client call and exactly one `adapter.exec` invocation. `sandbox.api.shell` routes through `overlay/client.py`; `sandbox.api.write` and `sandbox.api.edit` route through `occ/client.py`. Composition (overlay→OCC chain for shell, multi-edit OCC apply+commit for edit, OCC write+commit for write, conflict resolution, audit attribution) happens entirely inside `runtime/server.py` after the bundle is deployed. The host never makes a follow-up call to "complete" an op.

**Server contract.** `runtime/server.py` is generic dispatcher infrastructure, not a peer-specific policy module. It owns request-envelope parsing, validation, `OP_TABLE` lookup, handler invocation, result serialization, and structured error formatting. It does **not** grow an `if op == ...` branch for every OCC or Overlay request. Peer `bootstrap.py` / `handlers/__init__.py` modules register operations into `OP_TABLE`; `server.py` may import known peer bootstraps to load registrations, but dispatch remains table-driven.

**Module boundary.** This is a two-domain-module refactor: `sandbox/occ/` and
`sandbox/overlay/`. `sandbox/runtime/` is shared daemon/server infrastructure
that hosts and composes those modules; it is not a third peer domain module.

### 1.1 Public surface

```
sandbox/api/
    raw_exec.py # un-guarded — runtime/setup/lifecycle/debug only
    shell.py    # overlay + OCC guarded — agent shell tool
    read.py     # un-guarded read — agent read tool
    write.py    # OCC guarded — agent write tool
    edit.py     # OCC guarded — agent edit tool
    models.py   # request/result types (see §1.6)
```

`read` / `write` / `edit` / `shell` mirror their corresponding agent tools 1:1. Agent tools become trivial pass-throughs.

The verb modules are still the public surface, but they do not build server
envelopes themselves. Guarded verbs delegate to their owning peer client:
`sandbox.api.shell` → `OverlayClient`, `sandbox.api.write/edit` → `OCCClient`.
Those clients are internal route points; agent tools never import them directly.

`apply`, `undo`, `commit` are **OCC-internal** — reachable only inside pipelines, never through `sandbox.api`.

### 1.2 Provider seam

```
sandbox/providers/
    protocol.py        # ProviderAdapter: one method, exec
    daytona/adapter.py # process.exec impl
```

The registry (`sandbox_id → ProviderAdapter`) is the only place sandbox routing happens. Lifecycle code names the provider once at sandbox creation; nothing else mentions Daytona.

### 1.3 Runtime/Daemon Support Layer

```
sandbox/runtime/
    bundle.py             # tarball composition + idempotent upload (host-side)
    setup_orchestrator.py # sequenced peer setup.sh submission at bootstrap (host-side)
    server.py             # in-sandbox guarded service — generic OP_TABLE dispatcher
    pipelines.py          # server-side composition (shell, edit, write) — in-sandbox
```

Each peer owns a concrete `setup.sh` and registers that script + bundle
contributions at import time. `setup_orchestrator.py` submits the registered
scripts to the sandbox runtime/daemon after bundle upload and before guarded
ops run. The server is part of the bundle; once deployed, it handles every
gated op in a single Python process per call — no extra wire trips, no per-call
snippet building. `bundle.py` and `setup_orchestrator.py` run on the
orchestrator; `server.py` and `pipelines.py` run inside the sandbox after
deployment. The split is by file purpose (documented per module), not by
directory. This package can be described as the daemon/runtime layer, but it
should not be counted as a third refactored domain module.

### 1.4 Two Peer Modules

Expected folder structure:

```
backend/src/sandbox/
    runtime/
        bundle.py
        setup_orchestrator.py
        server.py        # in-sandbox generic guarded service
        pipelines.py     # server-side OCC/Overlay composition

    occ/
        setup.sh
        client.py        # host-side typed OCC request client
        bootstrap.py
        handlers/        # server op adapters: write, edit, apply_changeset, commit, undo
        changeset.py     # overlay UpperChange -> OCC/direct-merge decision
        arbiter.py
        content_manager.py
        patcher.py
        time_machine.py
        write_coordinator/
        ledger_store.py
        hashing.py
        engine.py
        types.py

    overlay/
        setup.sh
        client.py        # host-side typed Overlay request client
        bootstrap.py
        handlers/        # in-sandbox Overlay implementation: run
        engine.py
        types.py
```

OCC and Overlay are **peers**. They never import each other. Each peer owns its
client and setup script; every request for that peer routes through its
`client.py`. The only place peer work composes is
`sandbox/runtime/pipelines.py::shell_pipeline`, which calls both peers'
handlers in-process inside the sandbox.

### 1.5 Layering invariants

| Invariant | Enforcement |
|---|---|
| Agent tools see only `sandbox.api.{shell, read, write, edit}` | Tool files import only those four. Lint allowlist. |
| `sandbox.api.raw_exec` is un-guarded; agents never see it | Allowlisted importers: `sandbox/runtime/{bundle,setup_orchestrator}.py`, `sandbox/lifecycle/*`, debug paths only. |
| Guarded requests route through peer clients | `sandbox.api.shell` imports `sandbox.overlay.client.OverlayClient`; `sandbox.api.write/edit` import `sandbox.occ.client.OCCClient`. Tests fail if public APIs build server envelopes directly. |
| Peer setup is explicit | `sandbox/occ/setup.sh` and `sandbox/overlay/setup.sh` are registered by peer `bootstrap.py` files and submitted by `runtime/setup_orchestrator.py` after bundle upload. |
| Pipelines are the only sequencer | `runtime/pipelines.py` owns every multi-step or cross-peer op: `shell_pipeline` chains `overlay.run` → `occ.apply_changeset` (overlay-rejection short-circuits before OCC); `edit_pipeline` drives multi-edit OCC apply + commit atomically; `write_pipeline` drives OCC write + commit. Overlay handlers return captured upperdir changes to the caller and never invoke OCC. Lint allowlist forbids `from sandbox.occ` inside `sandbox/overlay/` and vice versa. |
| Runtime server is generic | `runtime/server.py` has request decoding, `OP_TABLE` lookup, result encoding, and structured errors only. Peer-specific request behavior is registered by `occ/bootstrap.py`, `overlay/bootstrap.py`, and handler modules. |
| Provider-specific code is one file | `sandbox/providers/<x>/adapter.py` + the lifecycle line that builds it. |
| One wire trip per agent op | `server.py` runs the full pipeline (overlay→OCC chain or multi-edit OCC apply+commit) in one Python process. |

### 1.6 Result types

Per-verb result types mirror the API surface 1:1, sharing audit fields through two base dataclasses. The hierarchy itself encodes the guard contract: a `ReadFileResult` cannot carry a conflict because reads are un-guarded by construction.

```python
@dataclass(frozen=True, kw_only=True)
class SandboxResultBase:
    success: bool
    warnings: tuple[str, ...] = ()
    timings: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True, kw_only=True)
class GuardedResultBase(SandboxResultBase):
    conflict: ConflictInfo | None = None


@dataclass(frozen=True, kw_only=True)
class ConflictInfo:
    reason: Literal[
        "base_mismatch", "patch_failed", "not_found",
        "policy_reject", "overlay_failed", "lock_held", "argv_too_large",
    ]
    path: str | None = None
    detail: str = ""


# ── un-guarded ─────────────────────────────────────────────────────────────

@dataclass(frozen=True, kw_only=True)
class ReadFileResult(SandboxResultBase):
    content: str
    exists: bool = True
    encoding: str = "utf-8"


@dataclass(frozen=True, kw_only=True)
class RawExecResult(SandboxResultBase):
    exit_code: int
    stdout: str
    stderr: str = ""


# ── guarded ────────────────────────────────────────────────────────────────

@dataclass(frozen=True, kw_only=True)
class WriteFileResult(GuardedResultBase):
    gitinclude_changed_paths: tuple[str, ...] = ()  # OCC-tracked, in ledger
    gitignore_changed_paths: tuple[str, ...] = ()   # written but not ledger-tracked
    bytes_written: int = 0


@dataclass(frozen=True, kw_only=True)
class EditFileResult(GuardedResultBase):
    gitinclude_changed_paths: tuple[str, ...] = ()
    gitignore_changed_paths: tuple[str, ...] = ()
    applied_edits: int = 0


@dataclass(frozen=True, kw_only=True)
class ShellResult(GuardedResultBase):
    exit_code: int
    stdout: str
    stderr: str = ""
    gitinclude_changed_paths: tuple[str, ...] = ()  # OCC-committed
    gitignore_changed_paths: tuple[str, ...] = ()   # direct-merged, not ledgered
```

These collapse today's `OperationResult`, the overlay `SimpleNamespace` builders, and the per-verb `mutation_results.py` helpers into a single shape per verb. Logging/audit code accepts `SandboxResultBase`; OCC-aware code accepts `GuardedResultBase`. `ConflictInfo` is reachable only on guarded verbs — the type system enforces the layering invariant.

The `gitinclude_changed_paths` / `gitignore_changed_paths` split replaces today's `changed_paths` + `ambient_changed_paths` and applies uniformly across all three guarded verbs. A path is in `gitinclude_changed_paths` iff it was committed through OCC and recorded in the ledger; it's in `gitignore_changed_paths` iff OCC classified it as gitignored or external and direct-merged it without a ledger commit.

## 2. Implementation steps

Each step ends green: build, ruff, and tests pass. No intermediate broken states. Old code paths are kept alongside new ones until the step that deletes them.

Per-step implementation plans (files added, tasks, tests, exit criteria, risks) live in [`./sandbox-api-runtime-refactor/`](./sandbox-api-runtime-refactor/README.md). The `step-XX` prefix is the implementation order; the slice ID is retained for architectural traceability. Summary:

| Step | Slice | Plan |
|---|---|---|
| 1 | 5a | [Overlay/OCC responsibility split](./sandbox-api-runtime-refactor/step-01-slice-5a-overlay-occ-responsibility-split.md) |
| 2 | 1 | [Provider seam](./sandbox-api-runtime-refactor/step-02-slice-1-provider-seam.md) |
| 3 | 2 | [`sandbox.api.raw_exec`](./sandbox-api-runtime-refactor/step-03-slice-2-raw-exec.md) |
| 4 | 3 | [Runtime scaffolding](./sandbox-api-runtime-refactor/step-04-slice-3-runtime-scaffolding.md) |
| 5 | 4 | [OCC peer relocation](./sandbox-api-runtime-refactor/step-05-slice-4-occ-relocation.md) |
| 6 | 5b | [Overlay peer relocation](./sandbox-api-runtime-refactor/step-06-slice-5b-overlay-relocation.md) |
| 7 | 6 | [Public `sandbox.api.{shell,read,write,edit}`](./sandbox-api-runtime-refactor/step-07-slice-6-public-api.md) |
| 8 | 7 | [Delete legacy client + transport](./sandbox-api-runtime-refactor/step-08-slice-7-delete-legacy.md) |
| 9 | 8 | [Tests + docs](./sandbox-api-runtime-refactor/step-09-slice-8-tests-docs.md) |

Ordering invariants: Step 1 ships first and is independently revertible; Step 6 waits for Steps 1 and 5; Step 7 ships before Step 8; no step both adds and deletes the same surface.

## 3. Plugin integration

Plugins (basedpyright LSP and friends) plug in through the same mechanism as OCC/Overlay:

- A `sandbox/plugins/<x>/setup.sh` contains the concrete setup script.
- A `sandbox/plugins/<x>/bootstrap.py` registers that setup script (e.g. `nohup python -m … &`) at import time.
- The plugin's `handlers/` register server ops.
- The plugin exposes its own client surface — but **not** under `sandbox/api/` (plugins are an internal subsystem, not a public agent API).

Sequencing: this refactor lands without plugins. `plugins-refactor.md` picks up the LSP move once `runtime/setup_orchestrator.py` is in place (after Slice 3).

## 4. Out of scope

- Multi-daemon-process topologies. One sandbox = one server script + at most one long-lived plugin process.
- Sharing a base `Chokepoint` interface between OCC and Overlay. They remain duck-typed peers.
- Batched `sandbox.api.write/edit` over multiple files. The public API stays single-file; if a future use case justifies batching, that's an internal server op (`_write_many`), not a public surface change.
- Reads through the server script. `read` stays direct via `cat`; if overlay-aware reads are ever needed, that's a separate verb.

## 5. Risks

| Risk | Mitigation |
|---|---|
| Step ordering deletes a path before its replacement is wired | Each step keeps the old path alive until the step after the migration completes; no step both adds and deletes the same surface. |
| Provider adapter loses fidelity vs today's transport | Slice 1 is a literal move — the Daytona adapter is today's transport-impl unchanged. Behavior parity is the test bar. |
| Server deployment breaks the bootstrap sequence | `runtime/bundle.py` is idempotent and content-addressed; `setup_orchestrator.run_all` runs *after* bundle upload; LSP-style spawn scripts run last. |
| `server.py` becomes a hardcoded mega-router | Keep request dispatch table-driven. Peer modules register ops; server tests assert unknown-op handling and avoid per-peer dispatch branches. |
| Peer clients drift into a second public API | Importer allowlist keeps agent tools on `sandbox.api.*`; peer clients remain internal route points used by API modules and tests. |
| Agent tools accidentally import un-guarded `sandbox.api.raw_exec` | Lint allowlist test runs in CI; CODEOWNERS covers `sandbox/api/_registry.py`. |
| Overlay→OCC responsibility split regresses today's behavior | Step 1 / Slice 5a is gated on integration tests covering silent OCC drop of `.git/` writes, mixed gitinclude/gitignore/external partitioning, read-only in-namespace runtime behavior, structural OCC conflicts, binary pass-through for gitignored files, and `argv_too_large` surfacing as structured conflict. Step 6 moves files only after Step 1 is green. |

## 6. Decisions Resolved For Execution

- Host↔guest envelope: §1.6 result types serialized as JSON on stdout. No separate `{ok, result, error}` envelope.
- Setup scripts: each peer owns a bundled `setup.sh`; `runtime/setup_orchestrator.py` submits registered scripts after bundle upload.
- Importer allowlist: unit tests, not a custom ruff rule.
- Runtime entry name: `runtime/server.py`; it is a generic dispatcher, while `runtime/pipelines.py` owns cross-peer composition.
