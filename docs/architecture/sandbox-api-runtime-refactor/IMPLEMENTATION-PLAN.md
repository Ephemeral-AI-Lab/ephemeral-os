# Implementation Plan — Sandbox API + Runtime Refactor

Sequences the nine implementation steps so the correctness fix ships first, then the architecture chain, then the public-surface flip and cleanup. Each step ends green: `make build`, `ruff check`, `make test` all pass. No broken intermediate states.

Per-step scope (files, tasks, tests, exit criteria) lives in `step-*.md` files in this directory. The original slice IDs are retained in filenames and headings for traceability. This document is **sequencing only**.

## 0. Pre-flight

- Confirm baseline green: `make build && ruff check && make test`.
- Tag `git tag pre-sandbox-refactor` on `main` for emergency revert reference.

## 1. Sequence

```
Phase A — Correctness fix + responsibility split
  [1] Slice 5a — Refactor overlay/OCC responsibility (in place)

Phase B — Architecture chain
  [2] Slice 1  — Provider seam
  [3] Slice 2  — sandbox.api.raw_exec
  [4] Slice 3  — Runtime scaffolding
  [5] Slice 4  — OCC peer relocation

Phase C — Public surface + cleanup
  [6] Slice 5b — Overlay peer relocation
  [7] Slice 6  — Public sandbox.api verbs
  [8] Slice 7  — Delete legacy
  [9] Slice 8  — Tests + docs
```

5a runs first because it is the correctness fix and the architectural correction the rest of the chain assumes (overlay = pure upperdir capture; OCC = sole merge-policy decider). It is independent of slices 1–4 and must land before 5b. Per the design call (2026-05-03), overlay emits all upperdir bytes including would-be-gitignored content, and OCC routes per file: gitinclude → ledger; gitignore / external → direct-merge orchestrator-side. The previous in-namespace direct-merge moves to OCC.

## 2. Step-by-step

### Step 1 — Slice 5a (responsibility split + correctness fix)
- **Why first.** Independent of all relocation work; fixes the argv-overflow papercut today and lays the responsibility split the rest of the refactor (`shell_pipeline` in 5b, public `sandbox.api.shell` in 6) composes on top of.
- **Entry.** Pre-flight done.
- **Exit gate.** Integration tests green per `step-01-slice-5a-overlay-occ-responsibility-split.md` §Tests: `.git/` writes flow through overlay and are silently dropped by OCC; mixed change-set partitions correctly across ledger / direct-merge / external; in-namespace runtime is read-only on live workspace; gitinclude non-utf8 / symlink → conflict; gitignore binary → byte-identical direct-merge; argv-overflow surfaces as `ConflictInfo(reason="argv_too_large", ...)`.
- **Slice-size note.** 5a is no longer the small in-place OCC-decoupling the original draft contracted on; it is a multi-file restructure (in-namespace runtime, orchestrator-side overlay, OCC `apply_changeset` entry, caller projection). It still ships as one PR and is revertible without touching slices 1–4 or 5b.
- **Production impact at end of step.** Argv-overflow papercut fixed; `.git/` writes from commands like `git status` stay isolated in the namespace and are dropped by OCC; gitignored content (e.g. `.venv/` from `pip install`) now ships through NDJSON and lands on disk via OCC direct-merge instead of in-namespace; pytest/pip-install workloads continue to work end-to-end.

### Step 2 — Slice 1 (Provider seam)
- **Entry.** Step 1 merged.
- **Exit gate.** Daytona provider adapter wraps today's transport for
  `exec`; lifecycle wires register/dispose through
  `sandbox.providers.registry`; legacy `SandboxTransport` remains a deprecated
  wide superset of `ProviderAdapter` with byte I/O and checked batch write
  methods intact; existing tests green.
- **No caller migration.**

### Step 3 — Slice 2 (raw_exec)
- **Entry.** Step 2 merged.
- **Exit gate.** `sandbox.api.raw_exec` exists as a thin wrapper over
  `sandbox.providers.registry.get_adapter(sid).exec(...)`. Host-side bundle
  upload lives in `runtime/bundle.py` and uses `raw_exec`. Allowlisted
  lifecycle/debug raw exec callers use `raw_exec` only after provider adapter
  registration. Importer-allowlist test green. Existing `api/models.py` is not
  stripped. Agent tools, audited API, CI internals, OCC content management, and
  `daemon/client.py` remain on legacy `SandboxTransport`.

### Step 4 — Slice 3 (runtime scaffolding)
- **Resolve in this step (per parent doc §6 deferrals).**
  - Host↔guest envelope = §1.6 result types as JSON on stdout.
  - `SetupScript` shape = frozen dataclass
    `SetupScript(name: str, package: str, relative_path: str)` pointing to a
    bundled peer `setup.sh`. `setup_orchestrator.run_all(sid)` submits each
    script to the sandbox runtime/daemon after bundle upload.
  - Importer allowlist = unit test (not custom ruff rule).
- **Boundary.** Runtime scaffolding is shared daemon/server infrastructure, not
  a third peer module. The two refactored domain modules remain OCC and
  Overlay. Do not move daemon `storage.py`, `ledger_store.py`, or `paths.py`
  into runtime; those are legacy/OCC-owned until Slice 4.
- **Entry.** Step 3 merged.
- **Exit gate.** `runtime/{bundle,setup_orchestrator,server,pipelines}.py`
  exist. `bundle.py` is the Step 3 host-side upload module; this slice updates
  it to bundle runtime files and adds setup orchestration, server dispatch, and
  empty pipeline stubs. `server.py` is a generic OP_TABLE dispatcher. If old
  daemon callers still need `{ok,result,error}`, that compatibility lives in a
  temporary `runtime/legacy_command_client.py`, not in `server.py`. Peer setup
  registration can submit a bundled `setup.sh` in order. Compat shim at
  `code_intelligence/daemon/client.py` keeps legacy callers working.
  `code_intelligence/daemon/command.py` is gone; daemon
  `{storage,ledger_store,paths}.py` did not move to runtime. Empty `OP_TABLE`
  returns a clean `unknown_op` envelope.

### Step 5 — Slice 4 (OCC peer)
- **Entry.** Step 4 merged.
- **Exit gate.** OCC at `sandbox/occ/` with `client.py` and `setup.sh`.
  `OCCClient` is the only host-side route for OCC server ops; `setup.sh`
  is registered by `occ/bootstrap.py`. `edit_pipeline` and `write_pipeline`
  are reachable through server dispatch but **not** yet exposed via
  `sandbox.api`. The old direct `apply_edit` and snapshot-based undo paths are
  removed. Zero grep hits on the old names.
  `code_intelligence/mutations/` deleted.

### Step 6 — Slice 5b (overlay peer + shell_pipeline)
- **Entry.** Steps 1 and 5 both merged.
- **Exit gate.** Overlay at `sandbox/overlay/` with `client.py` and `setup.sh`.
  `OverlayClient` is the only host-side route for overlay/shell server
  ops; public shell may still bypass it for simple read-only pipelines.
  `setup.sh` is registered by `overlay/bootstrap.py`. `shell_pipeline`
  composes overlay→OCC. One-wire-trip-per-op assertion holds for every shell
  pipeline test. Peer-isolation lint passes (overlay ↔ OCC mutual non-import).
  5a's stripped dead code deleted.

### Step 7 — Slice 6 (public verbs)
- **Entry.** Steps 5 and 6 merged.
- **Exit gate.** `sandbox.api.{shell, read, write, edit}` live. Guarded API
  modules delegate to peer clients (`shell` → `raw_exec` only for simple
  read-only pipelines and `OverlayClient` otherwise, `write/edit` →
  `OCCClient`) instead of constructing server envelopes directly. Agent
  tools are ≤10-line pass-throughs. §1.6 result hierarchy is the only result
  surface; `OperationResult`, overlay `SimpleNamespace` builders, and
  `mutation_results.py` shape helpers gone.

### Step 8 — Slice 7 (delete legacy)
- **Entry.** Step 7 merged.
- **Pre-delete grep audits** (each must return zero production hits before its file is removed):
  - `grep -r "from sandbox.code_intelligence" backend/src/`
  - `grep -r "SandboxTransport" backend/src/`
  - `grep -r "audited_sandbox_api\|attribution\|sandbox.api.audit\|sandbox.api.bash\|file_commands" backend/src/`
  - `grep -r "from sandbox.daytona.transport" backend/src/`
- **Exit gate.** `find backend/src/sandbox/code_intelligence -type f` empty. `import sandbox.code_intelligence` raises `ModuleNotFoundError`. `sandbox/api/` contains only verb modules + `models.py` + `raw_exec.py`.

### Step 9 — Slice 8 (tests + docs)
- **Entry.** Step 8 merged.
- **Exit gate.** Tests under `test_sandbox/test_{occ,overlay,runtime}/`. `pipelines.py` has direct unit coverage for shell, edit, write. `occ-overlay-daemon-refactor.md` carries the superseded banner.

## 3. Hard gates between phases

- **A → B.** Step 1's 5a integration gates are green. Until then, Slice 1 does not start.
- **B → C.** Steps 2–5 each merged green individually. Do not stack unmerged steps.
- **Within C.** 5b's one-wire-trip assertion gates Step 7. Step 7's importer allowlist test gates Step 8. Step 8's grep audits gate the deletes.

## 4. Rollback strategy

- Each step is a single PR; revert = `git revert <merge>`.
- Slice 5a is independently revertible (in-place, no moves).
- Steps 6 → 8 form a relocation and cleanup chain — revert in reverse order if needed.
- `pre-sandbox-refactor` tag is the floor for emergency reset.

## 5. Out of scope — don't expand mid-flight

- Multi-daemon-process topologies.
- Batched public `write` / `edit` across multiple files.
- Reads through the server script.
- LSP plugin migration — separate work tracked in `plugins-refactor.md`, picks up after Step 4 (Slice 3) lands `runtime/setup_orchestrator.py`.
