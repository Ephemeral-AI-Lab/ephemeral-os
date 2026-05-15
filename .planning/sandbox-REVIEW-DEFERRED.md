# `backend/src/sandbox` — Deferred Items After Phase 1

**Phase 1 complete:** 11 of 12 listed bug fixes landed (commits `64c6af47` → `116e40f7`); net delta +236 LOC defensive code; 545 tests passing on every batch. **C2 deferred — see §1 for the specific blocker.**

This document tracks everything that was *not* applied. Each entry has enough context to run as a standalone follow-up pass: source-of-truth review at `.planning/sandbox-REVIEW.md`, per-subsystem deep-dives at `/tmp/sandbox_review/*.md`.

---

## 0. Recommended order

1. **C2 — pipeline collapse.** Largest single LOC win (~400 LOC removed). Phase 3 in the original report. Has an explicit blocker (§1).
2. **S4 — `provider/daytona/client/` collapse.** Fixes the depth-5 import violation (the worst in the package). Mechanical move + rewrite of internal imports.
3. **S5 — `occ/{stage,content,changeset}/` flattening.** Mechanical move + 4 file deletions; brings every `occ.*` import to depth ≤3.
4. **S6 — `plugin/runtime/` flatten.** Needs a deprecation shim (§4) to avoid breaking the LSP plugin.
5. **Phase 4 — Naming renames (cross-cutting).** Mechanical but cross-cuts ~30 files including `task_center_runner/` and `live_e2e/` which are out-of-scope for this branch.
6. **Phase 5 — Local cleanups (S7–S10 plus smaller wins).** Independent; any order.

Branch strategy: **stop stacking on `codex/fix-dot-path-normalization-tests`.** The parallel codex activity has already swept staged files into two of the Phase 1 commits (`74a9d681`, `116e40f7`). Cut a fresh branch off this one before starting Phase 2.

---

## 1. C2 — Two-pipeline collapse (CRITICAL, structural)

**What:** Merge `execution/overlay_runner.py`, `execution/overlay_pipeline.py`, `execution/overlay_worker.py`, `execution/overlay_mounts.py` into `execution/orchestrator.py`. Snapshot-overlay callers go through the unified orchestrator with `occ_apply=False` (or a `NoopMutationClient`).

**Source of truth:** `.planning/sandbox-REVIEW.md` §2 C2; deep-dive at `/tmp/sandbox_review/execution.md` C1.

**Why deferred:**

1. **Architectural-boundary test contradicts the refactor.** `backend/tests/unit_test/test_sandbox/test_overlay/test_overlay_dependency_boundaries.py` is named "Phase 02 overlay modules" and at lines 52–59 asserts:
   ```python
   assert "sandbox/execution/overlay_worker.py" in names
   assert "sandbox/execution/overlay_mounts.py" in names
   assert "sandbox/execution/overlay_runner.py" in names
   assert "sandbox/execution/overlay_pipeline.py" in names
   ```
   The test exists to enforce the modular split that C2 wants to remove. **Decision needed:** delete the test, rewrite it to assert the new boundary, or accept the architectural boundary it encodes and abandon C2.

2. **Public-API surface change.** Adding `occ_apply: bool = True` (or `occ_client=NoopMutationClient()`) to `orchestrator.execute_command` is a public-surface choice. Both forms are workable; the report leans toward the flag.

3. **Test migration scope.** Tests that exercise the to-be-deleted modules:
   - `test_snapshot_overlay_runner.py` — rewrite to exercise the unified `orchestrator.execute_command(occ_apply=False)` path
   - `test_namespace_command_env.py` — imports `from sandbox.execution.overlay_worker import run_user_command`; relocate the helper or rewrite the test
   - `test_runtime_invoker_cleanup.py` — exercises `OverlayRuntimeInvoker` which goes away; rewrite or delete
   - `test_bundle_upload.py` — has bundle-contents assertions that must be updated to reflect the new file set

**Estimated win:** ~400 LOC raw removed, ~250 LOC net after fold-in. Plus removes one of the two materialization paths (`overlay_mounts.mount_snapshot` and `strategy_copy_backed.shutil.copytree` both did the same work).

**Concrete plan once unblocked:**
1. Read `orchestrator.execute_command` and `OverlaySnapshotRunner.shell` side-by-side; identify the OCC-specific lines in orchestrator.
2. Add `occ_apply: bool = True` parameter to `orchestrator.execute_command`; gate the post-capture OCC apply on it.
3. Rewrite `daemon/handler/overlay.py` from `OverlaySnapshotRunner(manager)` to `orchestrator.execute_command(..., occ_apply=False, mount_mode=MountMode.COPY_BACKED)`.
4. Delete the 4 files.
5. Rewrite/delete the 4 tests listed above.
6. Run `.venv/bin/pytest backend/tests/unit_test/test_sandbox -q`; expect a net of −400 LOC and the boundary-test deletion.

---

## 2. Phase 2 — Depth flattening (mechanical, hits public surface)

The goal: every `from sandbox.X.Y.Z` import lands at depth ≤3 (i.e. `from sandbox.X.Y import …` is the deepest allowed). Today's worst offenders:

| Path | Depth | Subsystem |
|---|---|---|
| `sandbox.provider.daytona.client.{sync_client, async_client, credentials, shutdown}` | **5** | provider (S4) |
| `sandbox.provider.daytona.{adapter, bash, bootstrap, context, errors, workspace}` | 4 | provider |
| `sandbox.occ.stage.{transaction, merge, policy, _edit, direct, gated}` | 4 | occ (S5) |
| `sandbox.occ.changeset.{types, prepared}` | 4 | occ (S5) |
| `sandbox.occ.content.{hashing, gitignore_oracle}` | 4 | occ (S5) |
| `sandbox.plugin.runtime.{context, registry}` | 4 | plugin (S6) |
| `sandbox.daemon.handler.X`, `sandbox.daemon.service.X` (handler ↔ service) | 4 | daemon (Option B in report) |

### 2.1 — S4: Collapse `provider/daytona/client/`

**Plan:**
- `git mv provider/daytona/client/{sync_client,async_client,credentials,shutdown}.py` → folded into a single `provider/daytona/client.py` (~380 LOC after dedup of sync/async cache machinery via a parameterized `ClientCache` class).
- Delete `provider/daytona/client/__init__.py`.
- Rewrite 21 internal imports beginning `from sandbox.provider.daytona.client.X` → `from sandbox.provider.daytona.client import …`.
- Rewrite `adapter.py` to import from the public surface (stop reaching into `_HEALTH_TIMEOUT_SECONDS`, `_normalize_dict`, etc. — those names lose the underscore prefix or move to a `daytona/labels.py`).

**Estimated win:** ~170 LOC removed (sync+async dedup) + fixes the depth-5 violation.

**Risk:** This file is large after the merge; consider splitting into `client.py` (transport) + `credentials.py` (still depth 3) if cohesion suffers.

### 2.2 — S5: Flatten `occ/{stage,content,changeset}/`

**Plan (one PR):**
- Delete `occ/stage/direct.py` and `occ/stage/gated.py` (2-line re-export shims) — 16 LOC removed.
- Inline `occ/stage/_edit.py` into `occ/stage/merge.py` (39 LOC moved, file deleted).
- Promote `occ/stage/{transaction,merge,policy}.py` → `occ/{commit_transaction,stage,stage_policy}.py`.
- Merge `occ/changeset/{types,prepared}.py` → `occ/changeset.py`.
- Promote `occ/content/{hashing,gitignore_oracle}.py` → `occ/{hashing,gitignore}.py`.
- Delete `occ/timing_keys.py` (8-LOC re-export of `sandbox.timing_keys.TimingKey`).
- Update internal imports: every `from sandbox.occ.X.Y` → `from sandbox.occ.Y`.
- Update `occ/__init__.py` re-exports (today already flattens many of these — should keep the public façade).

**Estimated win:** ~150 LOC raw + 4 files deleted. Brings all 22 `occ/*.py` files to depth 3.

**Risk:** Imports for these paths from outside `sandbox/` (e.g. `task_center_runner/`, `live_e2e/`, tests) will need a one-time mechanical rewrite. `grep -rn "from sandbox.occ.stage\|from sandbox.occ.changeset\|from sandbox.occ.content" backend/` before/after.

### 2.3 — S6: Flatten `plugin/runtime/` — **needs deprecation shim**

**Public-contract constraint:** `docs/architecture/plugins-refactor.md` §2 advertises `sandbox.plugin.runtime` as the *only* sandbox-side surface plugin authors are allowed to import. The in-tree `backend/src/plugins/catalog/lsp/runtime/server.py` imports from it. Four sandbox tests use deep paths like `sandbox.plugin.runtime.context`. **A hard rename is a breaking change for the plugin authoring contract.**

**Plan:**
- `git mv plugin/runtime/context.py plugin/op_context.py` and `plugin/runtime/registry.py plugin/op_registry.py`.
- **Keep `plugin/runtime/__init__.py`** as a deprecation re-export shim:
  ```python
  """DEPRECATED: import from ``sandbox.plugin.op_context`` / ``sandbox.plugin.op_registry``."""
  import warnings
  warnings.warn("sandbox.plugin.runtime is deprecated; use plugin.op_context/op_registry", DeprecationWarning, stacklevel=2)
  from sandbox.plugin.op_context import *  # noqa: F401,F403
  from sandbox.plugin.op_registry import *  # noqa: F401,F403
  ```
- Update sandbox-internal imports (`plugin/handler.py:27-28`, `plugin/op_registry.py` self-import) to the new paths.
- Update `runtime_bundle.py` to ship the new module names (and keep the shim so the bundle's `sandbox.plugin.runtime` import still works in-sandbox).
- Update 4 tests to the new paths.
- Decide when the shim retires (next minor? after LSP plugin is updated?).

**Estimated win:** depth 4 → 3 for plugin-runtime imports; the LSP plugin keeps working through the shim.

### 2.4 — Daemon intra-subsystem depth-4 (decision pending)

`daemon/handler/X` imports from `daemon/service/Y` are depth 4. Three options from the daemon report:
- **Option A:** Collapse `handler/` and `service/` into `daemon/` directly. ~24 files in one dir.
- **Option B (recommended in deep-dive):** Promote shared internals up one level: `daemon/handler/request_context.py` → `daemon/_toolbox.py`, `daemon/service/occ_backend.py` → `daemon/occ_backend.py`, `daemon/service/result_projection.py` → `daemon/_wire.py`, `daemon/service/workspace_server.py` → `daemon/workspace_server.py`. Leaves `service/` with only `layer_stack_client.py`, `workspace_binding.py`, `shell_runner.py`.
- **Option C:** Accept depth-4 for same-tree imports under one subpackage; tighten the rule to "≤3 cross-subsystem."

**Decision needed.** Recommend Option B if strict ≤3 is the target.

---

## 3. Phase 3 — Smaller bundled refactors (S7–S10)

These were in §3 of the consolidated review. None hits a blocker; do as individual PRs.

| Item | File(s) | Win | Notes |
|---|---|---|---|
| **S7** — delete `host/context_preparer.py` | `host/context_preparer.py` (50 LOC, 1 caller) | −50 LOC | Inline `adapter.context_preparer(sandbox_id)` at the call site. Drops the `SandboxRuntimeContext`/`SandboxContextPreparer` empty Protocols. |
| **S8** — inline 5-layer dispatch in `host/daemon_client.py` | `host/daemon_client.py` | −60 LOC | Compress `_call_daemon` → `_exec_daemon_call` → `_should_retry_after_connect_failure` → `_check_daemon_readiness_after_spawn` → `_readiness_request_for_original` into one `_dispatch_once_with_retry`. |
| **S9** — data-driven `_add_if_exists` loop | `host/runtime_bundle.py:161-185` | −30 LOC | Replace 6 near-identical 4-line blocks with a tuple-driven loop. |
| **S10** — extract god method `revalidate_and_publish` | `occ/stage/transaction.py:78-180` | −65 LOC | Pull `_accumulate_route_timings` and `_atomic_or_overlay_dropped` out of the 115-line method. |

**Smaller wins listed in the consolidated review §3:** `api/_control.py` passthrough fold (−40), `audit/translation.py` helper inline (−15), `timing.py` dead-string-normalize machinery (−50), `occ/service.py:_wrap_commit_result` (−15), `daemon/handler/health.py:84` dead probe (−3), `daemon/service/result_projection.py:committed_paths` (−8). Total ~130 LOC.

---

## 4. Phase 4 — Naming renames (mechanical, cross-cutting)

**Source of truth:** `.planning/sandbox-REVIEW.md` §5 "Naming — Cross-Cutting Rename Map".

These are name changes that don't alter behavior. They cross-cut sandbox + callers (`task_center_runner/`, `live_e2e/`, tests). Examples:

- `host/context_preparer.py` → delete (covered by S7).
- `host/{_DaemonDispatchError, _DaemonReadinessError}` → unified `DaemonError` with a `.phase` field.
- `host/{setup_after_create, setup_after_start}` → single `bootstrap_sandbox`.
- `api/_impl/{_audit, _classifiers, _payload, _results}.py` → drop the file-level underscore; rename `_classifiers.py` → `conflict_codes.py`, `_payload.py` → `decode.py`, `_results.py` → `results_builder.py`.
- `api/_control.py` → `_lifecycle.py` (or fold trivia into `api/__init__.py`).
- `daemon/handler/request_context.py` → split 4-way into `_classify.py`, `_args.py`, `_no_follow_fs.py`, `_project.py`.
- `daemon/service/result_projection.py` → `wire_payload.py` / `occ_to_wire.py`.
- `daemon/async_bridge.py` → move to `sandbox/io_loop.py` (used outside `daemon/`).
- `daemon/service/layer_stack_client.py` → `service/layer_stack_adapter.py` or fold into `occ_backend.py`.
- `daemon/scripts/thin_client.py` → `scripts/send_envelope.py`.
- `execution/entrypoints.py` (340 LOC) → `namespace_child.py` (or `private_namespace_child.py`).
- `execution/policy.py` → `env_policy.py`.
- `execution/{overlay_change, overlay_capture, overlay_mounts, …}` → `path_change.py`, `capture.py`, (delete `overlay_mounts.py`) (overlap with C2).
- `execution/workspace_environment.py` → `subprocess_runner.py`.
- `execution/workspace_capture.py` → delete (33 LOC, 1 caller).
- `occ/router.py::Router` → `occ/preparer.py::ChangesetPreparer`.
- `occ/ports.py` → `occ/protocols.py`.
- `occ/client.py::OccClient` → `occ/facade.py::BoundOccService` or rename class only.
- `occ/overlay.py` → `occ/overlay_adapter.py` (collides with `execution/overlay_*`).
- `occ/content/gitignore_oracle.py::PathspecGitignoreOracle` → `occ/gitignore.py::PathspecGitignoreMatcher`.
- `provider/daytona/{adapter, bash, bootstrap, context, errors, workspace}.py` → keep mostly; rename `bash.py` → `exec_wrapper.py`; fold `bootstrap.py` (20 LOC) into `daytona/__init__.py`; collides-fix `bootstrap.py` (vs `host/bootstrap.py`).
- `layer_stack/{layer_change, layer_index, layer_publisher}.py` → drop redundant `layer_` prefix.
- `layer_stack/manager.py::LayerStackManager` → `stack.py::LayerStack`.
- `layer_stack/view.py::MergedView` → `merged_view.py`.
- `layer_stack/transaction.py` → fold into `manager.py` (collides with `occ/stage/transaction.py`).
- `layer_stack/{_paths, _storage_lock}.py` → drop the `_` prefix (imported by 7 siblings).
- `layer_stack/maintenance.py` → `squash.py`.
- `plugin/handler.py` → `plugin/daemon_handler.py` (or move into `sandbox/daemon/handler/plugin.py`).
- `plugin/session.py` → `plugin/host_call.py`.
- `plugin/projection.py::WorkspaceProjection` → `plugin/workspace_view.py::PluginWorkspaceView`.

**Strategy:** Do this **after** Phase 2 (S4/S5/S6) lands. The flattening already moves several files; combining with renames in one pass is hazardous.

**Estimated win:** zero LOC (mechanical), but big improvement in readability and grep'ability per the user's criterion (0).

---

## 5. Phase 5 — Local cleanups (any time)

Independent cleanups that don't touch public surfaces.

### LOW-priority items per subsystem (from the deep-dive reports)

**api + audit + top-level** (`/tmp/sandbox_review/api_audit_top.md`):
- `api/_impl/_payload.py:13` — inline `normalize_overlay_cwd` (2-line, 1 caller).
- `api/_impl/_payload.py:53-60` — `int_from_payload` bool-rejection — keep, but add docstring.
- `api/transport.py:13-16` — 4 `DAEMON_OP_*` constants used in 1 place each; consider enum.
- `audit/__init__.py` — fix the misleading docstring or re-export.

**host** (`/tmp/sandbox_review/host.md`):
- L1 — `_DaemonDispatchError`/`_DaemonReadinessError` underscored but cross-callable (covered by Phase 4).
- L2 — `versioned_payload` defined but unused inside `daemon_client.py`; verify external use, then delete or expose.
- L3 — unify `ensure_daemon_current` vs `ensure_runtime_uploaded` verb naming.
- L4 — `ensure_running` swallows `start()` exception at debug; raise to warning.
- L5 — move `_thin_client_python_launcher` from inline heredoc to `daemon/scripts/launch_thin_client.sh`.
- L6 — `_PYTHON_CANDIDATES = ("python3.13", …)` hard-codes versions; do shell-side glob.
- L7 — `_runtime_probe` returns `{}` on malformed input; raise with the malformed structure.

**daemon** (`/tmp/sandbox_review/daemon.md`):
- L1, L3 — `_BOOT_T0`/`_STARTED_AT_MONO` set at module-import time; set in `__main__` instead.
- L2 — `writer.drain()` after too-large-request has no timeout; wrap with `asyncio.wait_for`.
- L4 — `handler/overlay.py:21-25` reconstructs `LayerStackManager` per call (bypasses cache); use `get_layer_stack_manager(...)`.
- L5 — `_DEFAULT_EXECUTOR_WORKERS = 200` hard-coded; make env-overridable.
- L6 — `fence_stale_staging` uses `child.stat()` (follows symlinks); use `child.lstat()`.
- L7 — `_acquire_pid_lock` PID-lock conflict message doesn't include the holding PID.
- L10 — cosmetic `__all__` cleanup in `daemon/handler/__init__.py`.

**execution** (`/tmp/sandbox_review/execution.md`):
- M3 — `contract.py` mixes request dataclass, protocols, and a `WorkspaceReplacementMountSpec` with disk-touching `resolve()` in `__post_init__`; split so importing the module doesn't `stat`.
- M4 — dead/dual `to_dict`/`from_dict` round-trippers (`OverlayShellRequest.to_dict`, `OverlayCapture.to_dict`/`from_dict`); delete if no consumer reads the result file back.
- M5 — collapse `capture_workspace_upperdir`'s `copy_backed` flag (covered by C2 fold-in).
- L1 — `execution/__init__.py` re-exports 14 names; keep only 3 actually used externally.
- L4 — move `read_output_ref` next to its only caller in `orchestrator.py`.

**occ** (`/tmp/sandbox_review/occ.md`):
- L1 — `Change.__post_init__` defensive `str(self.path)` cast; replace with `if not isinstance: raise TypeError`.
- L2 — `filter_ignored` reorders silently; document or rename.
- L3 — `commit_queue._merge_timings` sums duplicates without policy; document or annotate `TimingKey`.
- L4 — `DirectStager`/`GatedStager` PascalCase functions; rename or drop trampolines.
- L5 — benchmark `prepare_changeset_sync` async-executor hop; if <1 ms, call sync.
- L6 — flatten vs deep imports — pick one (covered by S5).

**provider** (`/tmp/sandbox_review/provider.md`):
- M3 — adapter mixes async `exec` with sync everything-else; make all CRUD methods async or run through `asyncio.to_thread`.
- M4 — `_serialize_raw` splits responsibilities; refactor into `_state(...)`, `_image(...)`, dict builder.
- M5 — `get_signed_preview_url` swallows `AttributeError` too broadly; check `getattr(..., None)`.
- M6 — `adapter.exec` `timeout=None` inherits SDK default; default to `_SANDBOX_TIMEOUT_SECONDS`.
- M7 — `context.py` lazy imports inside hot methods; hoist to module level (no real cycle).
- M8 — `register_standalone_loop_cleanup` registered as import side-effect; invoke explicitly from `bootstrap_daytona_provider()`.
- M9 — `bash.py::_UNPARSEABLE_EXIT_WARNED` process-wide latch; drop and rely on logger rate-limiting.
- L1 — precompile `extract_exit_code` regex.
- L3 — collapse `discover_workspace_async` and `discover_workspace` via shared helper.
- L4 — `_DOTENV_PATH` computed at import time; safer pattern.
- L5 — `protocol.py::create` mutually-exclusive `snapshot`/`image` lacks docstring.
- **H4** (provider) — `credentials._find_project_root` `start.parents[6]` IndexError on shallow paths; catch IndexError or return `start`.
- **H5** (provider) — `assert factory_name in (...)` stripped under `python -O`; replace with `if … raise ValueError`.
- **H6** (provider) — `shutdown.close_client` `N × 5s` sequential joins; fire-and-forget with one bounded join.

**layer_stack + plugin** (`/tmp/sandbox_review/layer_stack_plugin.md`):
- **H3** (layer_stack) — `prepare_workspace_snapshot` leaks materialized `transient-lowerdirs/*` on success path; track materialized lowerdirs by `lease_id` and remove in `release_lease`.
- **H4** (plugin) — `_PLUGIN_LOCKS` unbounded `setdefault`; cap dict size or use weakref dict.
- **H5** (plugin) — `flush_plugin_registrations` is not atomic on partial failure; orphan ops can pollute `OP_TABLE`.
- M14 — `plugin/install.py:_build_tar` 32 KB chunked base64 = 170 round-trips for a 4 MB plugin; use single-call upload.
- M15 — `layer_stack/maintenance.py:60` missing `fsync(layer_dir.parent)` after `os.replace`.
- L1–L8 — minor (docstrings, alphabetization, defensive plugin path resolve).

---

## 6. Out-of-scope housekeeping (not from the review, surfaced during fix work)

### 6.1 — Commit hygiene from parallel codex activity

Two of the Phase 1 commits accidentally swept in pre-staged codex renames:
- **`74a9d681`** ("sandbox: fix C3 …") also has 3 `db/stores/` zero-byte renames: `mission_store.py → goal_store.py`, `episode_store.py → iteration_store.py`, `attempt_store.py → trial_store.py`.
- **`116e40f7`** ("sandbox: fix C1 …") also has 17 directory renames under `backend/src/task_center/`: `mission/ → goal/`, `episode/ → iteration/`, `attempt/ → trial/`.

Content is content-free (zero-byte rename ops); message text understates scope. Options: amend via `git rebase -i` to split, or accept and move on.

### 6.2 — Broader test suite is currently red

`backend/tests/unit_test/test_tools/conftest.py:21` still imports `from db.stores.mission_store import MissionStore`, which fails after the codex rename to `goal_store`. Unrelated to sandbox work but blocks `pytest backend/tests/unit_test/` (broader than sandbox).

### 6.3 — `docs/plans/` untracked

An untracked directory at the repo root. Not mine; needs reconciliation.

---

## 7. Quick-reference index

| Bug ID | Where documented | Status |
|---|---|---|
| C1 | sandbox-REVIEW §2, /tmp/sandbox_review/layer_stack_plugin.md | **Done** (`116e40f7`) |
| C2 | sandbox-REVIEW §2, /tmp/sandbox_review/execution.md | **Deferred** — see §1 above |
| C3 | sandbox-REVIEW §2, /tmp/sandbox_review/provider.md | **Done** (`74a9d681`) |
| C4 | sandbox-REVIEW §2, /tmp/sandbox_review/host.md | **Done** (`64c6af47`) |
| C5 | sandbox-REVIEW §2, /tmp/sandbox_review/layer_stack_plugin.md | **Done** (`9c628e0e`) |
| H1, H2 | sandbox-REVIEW §2, /tmp/sandbox_review/occ.md | **Done** (`ae522789`, `83bb1749`) |
| H3 | sandbox-REVIEW §2, /tmp/sandbox_review/daemon.md | **Done** (`de830d4e`) |
| H4 | sandbox-REVIEW §2, /tmp/sandbox_review/daemon.md | **Done** (`dfe87b91`) |
| H6 | sandbox-REVIEW §2, /tmp/sandbox_review/api_audit_top.md | **Done** (`1aca76fa`) |
| H7 | sandbox-REVIEW §2, /tmp/sandbox_review/host.md | **Done** (`5366d674`) |
| H8 | sandbox-REVIEW §2, /tmp/sandbox_review/execution.md | **Done** (`17eb48f0`) |
| H9 | sandbox-REVIEW §2, /tmp/sandbox_review/execution.md | **Done** (`99779a5d`) |
| M5 | sandbox-REVIEW §2, /tmp/sandbox_review/daemon.md | **Done** (`46176bca`) |
| M9 | sandbox-REVIEW §2, /tmp/sandbox_review/host.md | **Done** (`64c6af47`) |
| S4 (provider client/) | sandbox-REVIEW §3, /tmp/sandbox_review/provider.md | **Deferred** — §2.1 |
| S5 (occ flatten) | sandbox-REVIEW §3, /tmp/sandbox_review/occ.md | **Deferred** — §2.2 |
| S6 (plugin/runtime flatten) | sandbox-REVIEW §3, /tmp/sandbox_review/layer_stack_plugin.md | **Deferred** — §2.3 (needs shim) |
| S7–S10 | sandbox-REVIEW §3 | **Deferred** — §3 |
| Phase 4 renames | sandbox-REVIEW §5 | **Deferred** — §4 |
| Phase 5 cleanups | sandbox-REVIEW §3 (smaller wins) + deep-dives (LOW) | **Deferred** — §3 + §5 |
