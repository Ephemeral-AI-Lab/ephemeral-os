# Implementation Plan — Sandbox API + Runtime Refactor

Sequences the nine slices so the correctness fix ships first, then the architecture chain, then the public-surface flip and cleanup. Each slice ends green: `make build`, `ruff check`, `make test` all pass. No broken intermediate states.

Per-slice scope (files, tasks, tests, exit criteria) lives in `slice-*.md` files in this directory. This document is **sequencing only**.

## 0. Pre-flight

- Amend `slice-5a-overlay-decouple.md`: change `Depends on. Slice 4.` → `Depends on. None (independent correctness fix; must land before Slice 5b).`
- Confirm baseline green: `make build && ruff check && make test`.
- Tag `git tag pre-sandbox-refactor` on `main` for emergency revert reference.

## 1. Sequence

```
Phase A — Correctness fix
  [1] Slice 5a — Decouple overlay from OCC (in place)

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

5a runs first because it's a self-contained correctness fix (argv-overflow + decoupling); shipping it early delivers the bug fix regardless of the rest of the refactor's pace.

## 2. Step-by-step

### Step 1 — Slice 5a (correctness fix)
- **Why first.** Independent of all relocation work; the argv-overflow papercut goes away today.
- **Entry.** Pre-flight done.
- **Exit gate.** Three integration tests green: overlay-reject leaves OCC ledger untouched; overlay-success → OCC-conflict captures upper layer; argv-overflow surfaces as `ConflictInfo(reason="argv_too_large", ...)`.
- **Production impact at end of step.** Bug fixed.

### Step 2 — Slice 1 (Provider seam)
- **Entry.** Step 1 merged.
- **Exit gate.** Daytona adapter wraps today's transport; lifecycle wires register/dispose; `SandboxTransport` is a structural alias of `ProviderAdapter`; existing tests green.
- **No caller migration.**

### Step 3 — Slice 2 (raw_exec)
- **Entry.** Step 2 merged.
- **Exit gate.** Bundle upload + lifecycle + debug paths import `sandbox.api.raw_exec`. Importer-allowlist test green. Agent tools and `daemon/client.py` remain on legacy.

### Step 4 — Slice 3 (runtime scaffolding)
- **Resolve in this step (per parent doc §6 deferrals).**
  - Host↔guest envelope = §1.6 result types as JSON on stdout.
  - `SetupScript` shape = frozen dataclass `SetupScript(name: str, run: Callable[[str], None])`.
  - Importer allowlist = unit test (not custom ruff rule).
- **Entry.** Step 3 merged.
- **Exit gate.** `runtime/{bundle,setup_orchestrator,entrypoint,pipelines}.py` exist. `pipelines.py` is empty stubs. Compat shim at `code_intelligence/daemon/client.py` keeps legacy callers working. Empty `OP_TABLE` returns a clean `unknown_op` envelope.

### Step 5 — Slice 4 (OCC peer)
- **Entry.** Step 4 merged.
- **Exit gate.** OCC at `sandbox/occ/`. `edit_pipeline` and `write_pipeline` reachable through entrypoint dispatch but **not** yet exposed via `sandbox.api`. `apply_edit` / `undo_last_edit` renamed to `apply` / `undo`; zero grep hits on the old names. `code_intelligence/mutations/` deleted.

### Step 6 — Slice 5b (overlay peer + shell_pipeline)
- **Entry.** Steps 1 and 5 both merged.
- **Exit gate.** Overlay at `sandbox/overlay/`. `shell_pipeline` composes overlay→OCC. One-wire-trip-per-op assertion holds for every shell pipeline test. Peer-isolation lint passes (overlay ↔ OCC mutual non-import). 5a's stripped dead code deleted.

### Step 7 — Slice 6 (public verbs)
- **Entry.** Steps 5 and 6 merged.
- **Exit gate.** `sandbox.api.{shell, read, write, edit}` live. Agent tools are ≤10-line pass-throughs. §1.6 result hierarchy is the only result surface; `OperationResult`, overlay `SimpleNamespace` builders, and `mutation_results.py` shape helpers gone.

### Step 8 — Slice 7 (delete legacy)
- **Entry.** Step 7 merged.
- **Pre-delete grep audits** (each must return zero production hits before its file is removed):
  - `grep -r "from sandbox.code_intelligence" backend/src/`
  - `grep -r "SandboxTransport" backend/src/`
  - `grep -r "audited_sandbox_api\|attribution\|sandbox.api.audit\|sandbox.api.bash\|file_commands" backend/src/`
  - `grep -r "from sandbox.daytona.transport" backend/src/`
- **Exit gate.** `find backend/src/sandbox/code_intelligence -type f` empty. `import sandbox.code_intelligence` raises `ModuleNotFoundError`. `sandbox/api/` contains only verb modules + `_registry.py` + `models.py` + `raw_exec.py`.

### Step 9 — Slice 8 (tests + docs)
- **Entry.** Step 8 merged.
- **Exit gate.** Tests under `test_sandbox/test_{occ,overlay,runtime}/`. `pipelines.py` has direct unit coverage for shell, edit, write. `occ-overlay-daemon-refactor.md` carries the superseded banner.

## 3. Hard gates between phases

- **A → B.** 5a's three integration tests green. Until then, Slice 1 does not start.
- **B → C.** Steps 2–5 each merged green individually. Do not stack unmerged slices.
- **Within C.** 5b's one-wire-trip assertion gates Step 7. Step 7's importer allowlist test gates Step 8. Step 8's grep audits gate the deletes.

## 4. Rollback strategy

- Each step is a single PR; revert = `git revert <merge>`.
- Slice 5a is independently revertible (in-place, no moves).
- Slices 5b → 6 → 7 form a relocation chain — revert in reverse order if needed.
- `pre-sandbox-refactor` tag is the floor for emergency reset.

## 5. Out of scope — don't expand mid-flight

- Multi-daemon-process topologies.
- Batched public `write` / `edit` across multiple files.
- Reads through the entrypoint script.
- LSP plugin migration — separate work tracked in `plugins-refactor.md`, picks up after Step 4 (Slice 3) lands `runtime/setup_orchestrator.py`.
