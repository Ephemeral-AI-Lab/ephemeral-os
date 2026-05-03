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

This refactor settles those by (a) defining a 5-verb public `sandbox.api`, (b) putting all gating logic in a single deployed entrypoint script inside the sandbox, (c) reducing the provider seam to one method, and (d) making every guarded op explicit through an in-sandbox pipeline (`shell_pipeline`, `edit_pipeline`, `write_pipeline`).

## 1. End-state shape

### 1.0 Pipeline

```
┌─ host ─────────────────────────────────────────────────┐
│ agent tool                                             │
│    └─► sandbox.api.{shell,read,write,edit,raw_exec}    │
│            └─► providers/<x>/adapter.exec(cmd)         │
│                    │ wire boundary                     │
└────────────────────┼───────────────────────────────────┘
                     ▼
┌─ guest (sandbox) ──────────────────────────────────────┐
│ runtime/entrypoint.py  (single python entry)           │
│    └─► runtime/pipelines.py::<verb>_pipeline           │
│            ├─► overlay/handlers/run.py    (shell only) │
│            └─► occ/handlers/{apply,commit,undo,...}    │
└────────────────────────────────────────────────────────┘
```

**Wire-trip contract.** Every `sandbox.api.{shell,write,edit}` call resolves to exactly one `adapter.exec` invocation. Composition (overlay→OCC chain for shell, multi-edit OCC apply+commit for edit, OCC write+commit for write, conflict resolution, audit attribution) happens entirely inside the entrypoint after the bundle is deployed. The host never makes a follow-up call to "complete" an op.

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

`apply`, `undo`, `commit` are **OCC-internal** — reachable only inside pipelines, never through `sandbox.api`.

### 1.2 Provider seam

```
sandbox/providers/
    protocol.py        # ProviderAdapter: one method, exec
    daytona/adapter.py # process.exec impl
```

The registry (`sandbox_id → ProviderAdapter`) is the only place sandbox routing happens. Lifecycle code names the provider once at sandbox creation; nothing else mentions Daytona.

### 1.3 Runtime layer (replaces `code_intelligence/daemon/`)

```
sandbox/runtime/
    bundle.py             # tarball composition + idempotent upload (host-side)
    setup_orchestrator.py # sequenced setup-script run at bootstrap (host-side)
    entrypoint.py         # in-sandbox top-level entry — single deployed script
    pipelines.py          # per-verb sequencers (shell, edit, write) — in-sandbox
```

Each peer registers its setup script + bundle contributions at import time. The entrypoint is part of the bundle; once deployed, it handles every gated op in a single Python process per call — no extra wire trips, no per-call snippet building. `bundle.py` and `setup_orchestrator.py` run on the orchestrator; `entrypoint.py` and `pipelines.py` run inside the sandbox after deployment. The split is by file purpose (documented per module), not by directory.

### 1.4 Peer modules

```
sandbox/occ/
    handlers/    # in-sandbox: apply, commit, undo, edit, write, arbiter
    bootstrap.py # OCC's contribution to setup orchestration
    engine.py    # OCCEngine Protocol
    types.py

sandbox/overlay/
    handlers/    # in-sandbox: run (overlay mount + capture upperdir changes)
    bootstrap.py # Overlay's contribution to setup orchestration
    engine.py    # OverlayEngine Protocol
    types.py
```

OCC and Overlay are **peers**. They never import each other. The only place their work composes is `sandbox/runtime/pipelines.py::shell_pipeline`, which calls both peers' handlers in-process inside the sandbox.

### 1.5 Layering invariants

| Invariant | Enforcement |
|---|---|
| Agent tools see only `sandbox.api.{shell, read, write, edit}` | Tool files import only those four. Lint allowlist. |
| `sandbox.api.raw_exec` is un-guarded; agents never see it | Allowlisted importers: `sandbox/runtime/{bundle,setup_orchestrator}.py`, `sandbox/lifecycle/*`, debug paths only. |
| Pipelines are the only sequencer | `runtime/pipelines.py` owns every multi-step or cross-peer op: `shell_pipeline` chains `overlay.run` → `occ.apply_changeset` (overlay-rejection short-circuits before OCC); `edit_pipeline` drives multi-edit OCC apply + commit atomically; `write_pipeline` drives OCC write + commit. Overlay handlers return captured upperdir changes to the caller and never invoke OCC. Lint allowlist forbids `from sandbox.occ` inside `sandbox/overlay/` and vice versa. |
| Provider-specific code is one file | `sandbox/providers/<x>/adapter.py` + the lifecycle line that builds it. |
| One wire trip per agent op | Entrypoint runs the full pipeline (overlay→OCC chain or multi-edit OCC apply+commit) in one Python process. |

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

- A `sandbox/plugins/<x>/bootstrap.py` registers a setup script (e.g. `nohup python -m … &`) at import time.
- The plugin's `handlers/` register entrypoint ops.
- The plugin exposes its own client surface — but **not** under `sandbox/api/` (plugins are an internal subsystem, not a public agent API).

Sequencing: this refactor lands without plugins. `plugins-refactor.md` picks up the LSP move once `runtime/setup_orchestrator.py` is in place (after Slice 3).

## 4. Out of scope

- Multi-daemon-process topologies. One sandbox = one entrypoint script + at most one long-lived plugin process.
- Sharing a base `Chokepoint` interface between OCC and Overlay. They remain duck-typed peers.
- Batched `sandbox.api.write/edit` over multiple files. The public API stays single-file; if a future use case justifies batching, that's an internal entrypoint op (`_write_many`), not a public surface change.
- Reads through the entrypoint script. `read` stays direct via `cat`; if overlay-aware reads are ever needed, that's a separate verb.

## 5. Risks

| Risk | Mitigation |
|---|---|
| Step ordering deletes a path before its replacement is wired | Each step keeps the old path alive until the step after the migration completes; no step both adds and deletes the same surface. |
| Provider adapter loses fidelity vs today's transport | Slice 1 is a literal move — the Daytona adapter is today's transport-impl unchanged. Behavior parity is the test bar. |
| Entrypoint deployment breaks the bootstrap sequence | `runtime/bundle.py` is idempotent and content-addressed; `setup_orchestrator.run_all` runs *after* bundle upload; LSP-style spawn scripts run last. |
| Agent tools accidentally import un-guarded `sandbox.api.raw_exec` | Lint allowlist test runs in CI; CODEOWNERS covers `sandbox/api/_registry.py`. |
| Overlay→OCC responsibility split regresses today's behavior | Step 1 / Slice 5a is gated on integration tests covering silent OCC drop of `.git/` writes, mixed gitinclude/gitignore/external partitioning, read-only in-namespace runtime behavior, structural OCC conflicts, binary pass-through for gitignored files, and `argv_too_large` surfacing as structured conflict. Step 6 moves files only after Step 1 is green. |

## 6. Open questions deferred to execution

- Exact shape of `SetupScript` (bash blob? script file? both?). Resolve in Slice 3.
- Whether the importer-allowlist is a custom ruff rule or a unit test. Default: unit test (cheaper to add).

(Resolved: the host↔guest envelope is the §1.6 result types serialized as JSON on stdout. No separate `{ok, result, error}` envelope.)

These do not change the plan shape; resolve in the relevant slice.
