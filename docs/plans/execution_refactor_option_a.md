# Refactor Plan — `backend/src/sandbox/execution/` (Option A)

**Status:** Draft
**Scope:** Reorganize `backend/src/sandbox/execution/` so module responsibilities are clear and overlayfs vocabulary is confined to the kernel boundary.
**Public API impact:** None. `execute_command`, `CommandExecRequest`, `CommandExecResult`, `MountMode` retain stable signatures.

---

## 1. Motivation

The current execution package mixes four concerns in a flat directory:

1. **Data contracts** — request/result/spec dataclasses, executor & lease Protocols (`contract.py`)
2. **Overlay implementation** — kernel mount, upperdir walking, copy-backed emulation, change synthesis (`namespace_child.py`, `overlay_capture.py`)
3. **Strategy dispatch** — protocol + two implementations + selection logic (`strategy_*.py`, parts of `orchestrator.py`)
4. **Top-level lifecycle** — lease → run → capture → OCC apply (`orchestrator.py::execute_command`)

Symptoms:

- `WorkspaceReplacementMountSpec` carries `lowerdir`/`upperdir`/`workdir` — overlayfs jargon — and both strategies consume it. Only `PrivateNamespaceStrategy` actually performs an overlay mount; `CopyBackedStrategy` ignores two of the three fields and uses a fourth directory (`merged`) with no name in the spec.
- `overlay_capture.py` has two modes: walk a real upperdir (kernel path) **or** synthesize one from a lowerdir+merged diff (copy-backed path). The synthesis logic does not belong to capture; it belongs to the strategy that produced the merged tree.
- `orchestrator.py` is two services in one file: strategy dispatch (`run_workspace_replaced_command`) and command lifecycle (`execute_command`).
- `namespace_child.py` lives next to its peers but imports constants back from `strategy_private_namespace.py` — a back-reference between the strategy launcher and its child.

---

## 2. Target Layout

```
backend/src/sandbox/execution/
├── __init__.py                       ← re-exports execute_command for back-compat
├── contract.py                       ← Request, Result, Protocols, MountMode (trimmed)
├── env_policy.py                     ← unchanged
├── subprocess_runner.py              ← unchanged
├── path_change.py                    ← unchanged
├── overlay/
│   ├── __init__.py
│   ├── layout.py                     ← OverlayLayout (was WorkspaceReplacementMountSpec)
│   ├── kernel_mount.py               ← mount/umount/validate; speaks overlayfs
│   ├── capture.py                    ← walk a real upperdir → OverlayPathChange tuple
│   └── change_synthesis.py           ← lowerdir+merged → synthesized upperdir (copy-backed only)
├── strategies/
│   ├── __init__.py
│   ├── base.py                       ← ExecutionStrategy Protocol
│   ├── namespace.py                  ← PrivateNamespaceStrategy + payload schema + constants
│   ├── namespace_child.py            ← runs inside `unshare`; delegates mount to overlay/kernel_mount
│   ├── copy_backed.py                ← runs in-place; calls overlay/change_synthesis post-run
│   └── _workspace_rewrite.py         ← argv/env path rewriting (copy-backed internal)
├── runner.py                         ← run_workspace_replaced_command (strategy dispatch)
└── service.py                        ← execute_command (lease → run → capture → OCC)
```

### Import direction (after refactor)

```
contract.py ────────────────────────┐
overlay/* ──────────────────────────┤
strategies/base.py ─────────────────┤
strategies/{namespace,copy_backed,namespace_child}.py ── depends on overlay/* + base
runner.py ── depends on strategies/*
service.py ── depends on runner.py + overlay/capture.py + contract.py
__init__.py ── re-exports execute_command from service
```

No back-edges. `namespace_child.py` and `namespace.py` share a small constants module if needed (`strategies/_namespace_protocol.py`) to avoid the current circular import.

---

## 3. Principles

1. **Vocabulary alignment.** Overlayfs jargon (`lowerdir`, `upperdir`, `workdir`) appears only in `overlay/kernel_mount.py` and `overlay/change_synthesis.py` — the two files that respectively call the kernel and emulate its semantics. Strategy and orchestrator code uses domain names (`base_repo`, `writes`, `kernel_scratch`).
2. **Single-responsibility files.** One module, one concern.
3. **Strategy parity in outputs, not inputs.** Both strategies emit identical `OverlayPathChange` tuples and a populated `writes/` directory. They may consume different shapes internally.
4. **No vestigial fields.** Documented exception: `OverlayLayout.kernel_scratch` is unused by copy-backed but kept in the shared layout for pragmatic reasons (re-evaluate if a third strategy lands).
5. **Layered imports.** Types depend on nothing internal; strategies depend on types; orchestrator depends on strategies.

---

## 4. Per-file Actions

| Current location | New location | Action |
|---|---|---|
| `contract.py::WorkspaceReplacementMountSpec` | `overlay/layout.py::OverlayLayout` | Move; rename fields `lowerdir`→`base_repo`, `upperdir`→`writes`, `workdir`→`kernel_scratch`. Keep validation. **No alias properties** — each module uses canonical names. |
| `namespace_child.py::_mount_overlay/_umount/_validate_mount_inputs/_open_dir_no_follow` | `overlay/kernel_mount.py` | Extract. New surface: `mount_overlay(workspace_root, lowerdir, upperdir, workdir, pass_fds)`, `umount(workspace_root)`, `validate_mount_inputs(...) → MountInputs`. Speaks overlayfs by parameter name (kernel-facing). |
| `overlay_capture.py::_walk_upperdir + marker decoders` | `overlay/capture.py::walk_upperdir(upper_root)` | Extract real-overlay walk only. Drop the `lowerdir=`/`workspace_root=` optional args. |
| `overlay_capture.py::_populate_upperdir_from_diff + diff helpers` | `overlay/change_synthesis.py::synthesize_writes(*, merged, base_repo, into)` | Extract. Becomes private to copy-backed's lifecycle (called from `CopyBackedStrategy.run`, not from the orchestrator). |
| `strategy_base.py::ExecutionStrategy` | `strategies/base.py` | `git mv`. Rename `is_recoverable_failure` → `should_fall_back`. |
| `strategy_private_namespace.py::PrivateNamespaceStrategy + constants` | `strategies/namespace.py` | `git mv`. Update `python -m sandbox.execution.namespace_child` → `python -m sandbox.execution.strategies.namespace_child` **in the same commit** as the child move. |
| `namespace_child.py` | `strategies/namespace_child.py` | `git mv`. Body stays thin — payload parse, error reporting, run dispatch. Mount logic delegates to `overlay.kernel_mount`. |
| `strategy_copy_backed.py::CopyBackedStrategy` | `strategies/copy_backed.py` | `git mv`. After `run_command_to_refs`, the strategy itself calls `synthesize_writes(merged=merged, base_repo=layout.base_repo, into=layout.writes)`. |
| `strategy_copy_backed.py::rewrite_declared_workspace_*` | `strategies/_workspace_rewrite.py` | Move. Underscore prefix signals package-internal. |
| `orchestrator.py::run_workspace_replaced_command + _strategies_for_mount_mode + _build_strategy` | `runner.py` | Extract. Pure strategy-dispatch concern. |
| `orchestrator.py::execute_command + _apply_workspace_capture + run-dir helpers` | `service.py` | Extract. The `if mount_mode == COPY_BACKED:` branch at line 139–147 collapses to a single `walk_upperdir(layout.writes)` call because synthesis now happens inside the copy-backed strategy. |

---

## 5. Naming Standardization

Applied across the package as part of the relevant step:

- `workspace_root` (when referring to the declared logical path) → `declared_workspace_root` outside the dataclass. (`subprocess_runner.py` already uses this name; finishing the migration removes the inconsistency.)
- `mounted_workspace_root` → unchanged. Accurate for both strategies — the directory the command actually executes in.
- `is_recoverable_failure` → `should_fall_back`. The current name implies command-level retry; the actual semantics are strategy-level fallback.
- `*_ref` (stdout/stderr/control file paths) → kept. Renaming has high churn and low value; document at the top of `service.py` that these are local filesystem paths used as IPC references.
- `WorkspaceReplacementMountSpec` → `OverlayLayout`.

---

## 6. Migration Sequence

Each step is one commit. Run `make test` after each. Use `git mv` for every rename so blame survives `--follow`.

### Step 1 — Rename `WorkspaceReplacementMountSpec` → `OverlayLayout` in place
- In `contract.py`, rename the class and its fields. Add a module-level alias `WorkspaceReplacementMountSpec = OverlayLayout` for one release.
- Update every callsite in `backend/src/sandbox/execution/` to use the new field names.
- Update `backend/tests/` references in the same commit.
- **Verify:** `make test`; `rg "WorkspaceReplacementMountSpec" backend/src/sandbox/execution/` finds only the alias line.

### Step 2 — Create `overlay/` and move the layout
- `mkdir backend/src/sandbox/execution/overlay/` with `__init__.py`.
- `git mv` the `OverlayLayout` class into `overlay/layout.py`.
- `contract.py` re-exports `OverlayLayout` and the deprecated alias.
- **Verify:** `make test`; `rg "from sandbox.execution.contract import OverlayLayout" backend/` still resolves.

### Step 3 — Extract `overlay/kernel_mount.py`
- Move `_mount_overlay`, `_umount`, `_validate_mount_inputs`, `_open_dir_no_follow`, `_MountInputs` out of `namespace_child.py` into `overlay/kernel_mount.py`.
- Keep parameter names overlayfs-native (`lowerdir`, `upperdir`, `workdir`) — this is the kernel boundary.
- `namespace_child.py` becomes ~150 lines: payload parse, error reporting, calls into `kernel_mount`, calls `run_command_to_refs`.
- **Verify:** `make test`; namespace strategy integration test green on Linux.

### Step 4 — Split `overlay_capture.py`
- `git mv overlay_capture.py overlay/capture.py`.
- Extract the synthesis helpers (`_populate_upperdir_from_diff`, `_entries_match`, `_has_nondirectory_payload_ancestor`, `_write_whiteout`, `_remove_path`, `_payload_paths`, `_mode_bits`) into `overlay/change_synthesis.py` with new public surface `synthesize_writes(*, merged, base_repo, into)`.
- `overlay/capture.py::walk_upperdir(upper_root)` no longer accepts `lowerdir`/`workspace_root` — that overload is gone.
- Update `orchestrator.py` to call `walk_upperdir` unconditionally; the `if mount_mode == COPY_BACKED:` branch is removed in step 5.
- **Verify:** `make test`; capture unit tests still produce identical `OverlayPathChange` tuples for known fixtures.

### Step 5 — Move synthesis into the copy-backed strategy
- In `strategy_copy_backed.py::run`, after `run_command_to_refs` returns, call `synthesize_writes(merged=merged, base_repo=Path(spec.base_repo), into=Path(spec.writes))`.
- Remove the copy-backed branch from `orchestrator.py::execute_command` (collapses to a single `walk_upperdir(layout.writes)` call).
- Preserve the timings keys (`overlay.capture.populate_upperdir_s`, `overlay.capture.walk_upperdir_s`, `command_exec.capture_upperdir_s`) by emitting them from their new owners.
- **Verify:** `make test`; copy-backed integration test produces identical OCC apply payloads for a known input.

### Step 6 — Move strategy files into `strategies/`
- `mkdir backend/src/sandbox/execution/strategies/` with `__init__.py`.
- `git mv` for: `strategy_base.py → strategies/base.py`, `strategy_private_namespace.py → strategies/namespace.py`, `strategy_copy_backed.py → strategies/copy_backed.py`, `namespace_child.py → strategies/namespace_child.py`.
- Update `strategies/namespace.py` so the `subprocess.run([..., "-m", "sandbox.execution.strategies.namespace_child", ...])` call points at the new module path. **This must be in the same commit as the file move.**
- Update all internal imports.
- **Verify:** `make test`; namespace strategy integration test green (the `python -m` path is exercised).

### Step 7 — Move path-rewrite helpers
- Extract `rewrite_declared_workspace_refs`, `rewrite_declared_workspace_env`, `_rewrite_workspace_paths`, `_rewrite_path_token`, `_path_starts_at` from `strategies/copy_backed.py` into `strategies/_workspace_rewrite.py`.
- `strategies/copy_backed.py` imports the two public helpers.
- Grep first: if anything outside `strategies/` imports these helpers, leave a deprecation re-export in `copy_backed.py`.
- **Verify:** `make test`.

### Step 8 — Split `orchestrator.py` → `runner.py` + `service.py`
- `git mv orchestrator.py service.py`.
- Move `run_workspace_replaced_command`, `_strategies_for_mount_mode`, `_build_strategy` from `service.py` into a new `runner.py`.
- `service.py::execute_command` imports `run_workspace_replaced_command` from `runner.py`.
- `execution/__init__.py` re-exports `execute_command` from `service`.
- **Verify:** `make test`; daemon and API callers (which import `from sandbox.execution import execute_command` or similar) work unchanged.

### Step 9 — Rename Protocol method
- `is_recoverable_failure` → `should_fall_back` on `strategies/base.py::ExecutionStrategy`, both strategy implementations, and the runner's dispatch loop.
- **Verify:** `make test`.

### Step 10 — Remove back-compat shims
- Delete the `WorkspaceReplacementMountSpec = OverlayLayout` alias.
- Delete any `from sandbox.execution.contract import OverlayLayout` re-export that's still standing in.
- Delete any deprecation re-exports added in step 7.
- (Optional: defer this step to the next release if external repos consume these names.)
- **Verify:** `make test`; `rg "WorkspaceReplacementMountSpec|strategy_base|strategy_copy_backed|strategy_private_namespace|overlay_capture" backend/src/` returns zero.

---

## 7. Verification Strategy

**Per commit:**
- `make test` (existing pytest suite covers strategy dispatch, mount failure → fallback, capture correctness, OCC apply).
- `rg "from sandbox\.execution\." backend/ | sort -u` to audit import surface.

**After step 4 (capture split):**
- Run capture-specific tests against fixture inputs; the synthesized `writes/` directory and the resulting `OverlayPathChange` tuples must be byte-identical to pre-refactor output. Diff with `git stash`/`git stash pop` if needed.

**After step 6 (namespace_child move):**
- Run the live namespace-strategy integration test (requires Linux + working user namespaces). The `python -m` invocation is the primary risk.

**Final:**
- Full sandbox e2e test on Linux (exercises real overlay mount + capture).
- Copy-backed-only path on macOS (no kernel overlay available).
- `git log --follow strategies/namespace.py` shows history through `strategy_private_namespace.py` → original creation.

---

## 8. Risks & Mitigations

| Risk | Severity | Mitigation |
|---|---|---|
| External callers (daemon, API, tests outside `execution/`) import old names | Medium | Step 1's alias + step 10's removal gates this. Audit with `rg` before deleting. |
| `python -m sandbox.execution.namespace_child` path break leaves a broken intermediate state | High | Step 6 bundles the file move and the `subprocess.run` arg update into one commit. Integration test runs immediately after. |
| Timing dict keys change, downstream metrics break | Low-Medium | Preserve the four `command_exec.*` and two `overlay.capture.*` keys verbatim; they just emit from new owners. |
| `OverlayLayout.kernel_scratch` is unused by copy-backed (Principle 4) | Low | Document as a deliberate exception in `OverlayLayout` docstring. Re-evaluate if a third strategy lands. |
| Tests reference internal symbols by old name | Medium | Each step updates `backend/tests/` in the same commit as the source. |
| `git mv` not used; blame lost | Low | Step 0 mandate; spot-check with `git log --follow` on at least one renamed file per step. |

---

## 9. Out of Scope

- Behavior changes (none). The refactor is structural only.
- Performance tuning. Synthesis timing shifts attribution between two phases but does not change wall-clock work.
- A third strategy (e.g., `fuse-overlayfs` rootless). The new layout accommodates one trivially under `strategies/`, but adding one is a separate phase.
- Renaming `*_ref` to `*_path`. High churn, low value; revisit if a separate cleanup pass is desired.
- Touching `env_policy.py`, `subprocess_runner.py`, `path_change.py`. These have clear single responsibilities already.

---

## 10. ADR

- **Decision:** Adopt the subdirectory split (Option A) — `overlay/`, `strategies/`, top-level `runner.py`/`service.py` — with `WorkspaceReplacementMountSpec` renamed to `OverlayLayout` and overlayfs vocabulary confined to the kernel boundary.
- **Drivers:** Comprehension (primary); risk-bounded change (secondary); stable public API (constraint).
- **Alternatives considered:**
  - *Option B — flat rename only:* rejected. Doesn't address structural confusion; readers still scan a flat file list with no semantic grouping.
  - *Option C — unify overlay + strategy into one abstraction:* rejected. Blast radius exceeds the stated problem; conflates run-policy with mount-policy.
- **Why chosen:** Physical layout maps to conceptual responsibilities. Overlayfs jargon stops leaking into copy-backed code. Copy-backed owns its emulation end-to-end (synthesis moves into the strategy). Every public entrypoint preserved.
- **Consequences:** ~10 files moved, 1 dataclass renamed, 1 Protocol method renamed, 1 strategy's `run` method gains a `synthesize_writes` call. Tests re-pathed alongside source. Blame preserved via `git mv`. Orchestrator's capture branch removed.
- **Follow-ups:**
  - After landing: consider moving `subprocess_runner.py` and `env_policy.py` under `strategies/` only if they prove single-consumer (they currently serve both strategies and `namespace_child`).
  - Evaluate adding a `fuse-overlayfs` strategy for rootless Linux contexts; new layout accommodates without churn.
  - Optional: a separate cleanup pass to rename `*_ref` → `*_path` if a future reader still finds them confusing.
