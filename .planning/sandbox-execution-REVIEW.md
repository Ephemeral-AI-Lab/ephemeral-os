# Code Review — `backend/src/sandbox/execution`

**Scope:** all 23 `.py` files under `backend/src/sandbox/execution/` (2,614 LOC total).
**Focus:** (1) implementation quality, (2) simplicity / no redundancy, (3) import-chain length ≤ 3.
**Date:** 2026-05-15
**Reviewer:** Claude Opus 4.7

---

## Scorecard

| Dimension | Verdict | Notes |
|---|---|---|
| Correctness | OK | No silent-corruption bugs; a few minor robustness/error-masking issues. |
| Security | OK | Path / symlink / xattr handling is conservative. One privileged-mount path looks careful. |
| Simplicity | **NEEDS WORK** | ~30–40% of code is removable without losing functionality. |
| Redundancy | **NEEDS WORK** | Two parallel command-runner stacks + two run_dir helpers + a wrapper-of-wrapper invoker chain. |
| Import chains | **FAILS RULE** | **Every internal import is 4 segments** (`sandbox.execution.<subdir>.<mod>`). User-stated limit is 3. |

**Headline target:** 2,614 LOC → ~1,500–1,700 LOC (35–42% reduction) by collapsing the `workspace/`, `strategies/`, `overlay/` subpackages and merging duplicated runners. No behavior loss.

---

## 1. Import-chain depth (HARD-FAILS the stated rule)

The user's rule: import chain ≤ 3.
Today essentially every intra-package import is 4 segments:

```
sandbox.execution.contract           (3 — OK)
sandbox.execution.policy             (3 — OK)
sandbox.execution.orchestrator       (3 — OK)
sandbox.execution.entrypoints        (3 — OK)
sandbox.execution.strategies.base                4
sandbox.execution.strategies.copy_backed         4
sandbox.execution.strategies.private_namespace   4
sandbox.execution.workspace.capture              4
sandbox.execution.workspace.environment          4
sandbox.execution.workspace.mount                4
sandbox.execution.workspace.path_rewrite         4
sandbox.execution.overlay.capture                4
sandbox.execution.overlay.change                 4
sandbox.execution.overlay.mounts                 4
sandbox.execution.overlay.pipeline               4
sandbox.execution.overlay.request                4
sandbox.execution.overlay.result                 4
sandbox.execution.overlay.runner                 4
sandbox.execution.overlay.worker                 4
```

**This is structural, not cosmetic** — none of the three sub-packages (`strategies/`, `workspace/`, `overlay/`) carry enough payload to justify a directory. They exist for *organizational decoration*.

**Proposal (collapse to flat 3-segment layout):**

```
sandbox.execution.__init__       (drop lazy machinery — see §4)
sandbox.execution.contract       (keep; 226 LOC)
sandbox.execution.policy         (keep; 109 LOC)
sandbox.execution.orchestrator   (keep; 233 LOC)
sandbox.execution.entrypoints    (keep; trim — see §5)
sandbox.execution.strategies     ← merge base + copy_backed + private_namespace + workspace/mount.py
                                   (~250 LOC; was 358 across 5 files)
sandbox.execution.runtime        ← merge workspace/{environment,path_rewrite,capture} + overlay/{worker,mounts,result,...}
                                   (~400 LOC; was ~700 across 12 files)
sandbox.execution.change         ← overlay/change.py + capture.py (the change-data types/walker)
                                   (~280 LOC; was 360)
```

Net: 23 files → ~8 files. Every cross-module import becomes 3 segments. Caller-side imports outside this package (which today use `from sandbox.execution.overlay import …` or `from sandbox.execution.strategies import …`) all flatten to `from sandbox.execution.<flat-module> import …`.

**`sandbox.layer_stack._paths` leak** (`overlay/capture.py:12`) — importing from a private (`_paths`) module of another package is a coupling smell; expose `relative_symlink_target_escapes` through the public layer-stack surface, then drop the underscore-import.

---

## 2. Two parallel command-runner stacks (largest source of redundancy)

There are two near-identical pipelines:

| Concern | Path A (command-exec) | Path B (overlay snapshot) |
|---|---|---|
| Entry | `orchestrator.execute_command` (orchestrator.py) | `OverlaySnapshotRunner.shell` + `OverlayRuntimeInvoker.invoke` (runner.py + pipeline.py) |
| Lease/release | inline in orchestrator | `LayerStackManager.acquire_snapshot_lease` |
| Mount stage | `workspace.mount.run_workspace_replaced_command` → `CopyBackedStrategy` / `PrivateNamespaceStrategy` | `overlay.mounts.mount_snapshot` |
| Run-command | `workspace.environment.run_command_to_refs` | `overlay.worker.run_user_command` |
| Capture | `workspace.capture.capture_workspace_upperdir` → `overlay.capture.capture_changes` | `overlay.capture.capture_changes` directly |
| Result type | `CommandExecResult` (orchestrator) | `OverlayCapture` (overlay) |
| run_dir derive | `orchestrator._run_dir` | `OverlayRuntimeInvoker._run_dir` — **identical implementation** |

### 2a. `run_user_command` ≅ `run_command_to_refs` — merge them

`overlay/worker.py:45–95` and `workspace/environment.py:55–87`:
both take `command`, `cwd`, `env`, `timeout_seconds`, `stdout_ref`, `stderr_ref`; both mkdir parents; both `subprocess.run(..., check=False)`; both write bytes to refs.

Differences are superficial:
- worker uses `_HOST_ENV_ALLOWLIST` + literal `"GIT_OPTIONAL_LOCKS": "0"`;
- environment funnels through `CommandExecPolicy.command_environment` (which lets full `os.environ` through + policy defaults).

**Merge**: one `run_command_to_refs(command, cwd, env, *, env_builder=...)` where `env_builder` is either `policy.command_environment` or an allowlist closure. Estimated saving: ~50 LOC + one type/contract eliminated.

Worker's bespoke 124-exit-code-on-timeout handling (worker.py:87–90) is the only real behavioral difference — keep it via a `timeout_exit_code: int = 0` (raise) parameter or a tiny adapter.

### 2b. `_run_dir` duplication — extract one

`orchestrator.py:189–195` and `pipeline.py:69–74` implement the same "sanitize request_id + uuid8 suffix" path. Make it `execution.runtime.scratch_run_dir(parent, request_id)` and call from both sites. ~10 LOC saved + drift risk eliminated.

### 2c. `OverlaySnapshotRunner` + `OverlayRuntimeInvoker` are wrapper-of-wrapper

`OverlaySnapshotRunner.shell` does lease/release + timings around one async call to `_invoker.invoke`.
`OverlayRuntimeInvoker.invoke` *is* `run_sync_in_executor(self.invoke_sync, …)`.
`OverlayRuntimeInvoker.invoke_sync` *is* `execute_request(... run_dir=self._run_dir(...))`.
The `OverlayInvoker` Protocol exists only so tests can substitute a fake.

Three layers of indirection for one call. Fold into:
```python
class OverlaySnapshotRunner:
    def __init__(self, layer_stack, *, storage_root=None, run_command=execute_request): ...
    async def shell(self, request) -> OverlayCapture:
        lease = self._layer_stack.acquire_snapshot_lease(request.request_id)
        try:
            return await run_sync_in_executor(run_command, request=..., ...)
        finally:
            self._layer_stack.release_lease(lease.lease_id)
```
Tests inject `run_command=fake`. ~60 LOC removed; cycle warning in worker.py:6 also goes away.

---

## 3. `path_rewrite.py` (108 LOC) — single consumer, hand-rolled scanner

`path_rewrite.py` exposes 5 public functions; only `rewrite_declared_workspace_refs` and `rewrite_declared_workspace_env` are called outside the module (by `copy_backed.py`). `rewrite_workspace_paths`, `path_starts_at`, `rewrite_path_token` are internal.

The hand-rolled `path_starts_at` character-class boundary check (line 89–97) is the kind of code that breeds CVEs:
- The boundary alphabet is hard-coded twice (`" \t\n\r=:;,&|>(\"'"` before; `"/ \t\n\r:;,&|)<\"'"` after) — asymmetric and not commented.
- An argv part like `'--prefix=/workspace/x'` will rewrite (good), but a path embedded in a shell-quoted single-quote literal like `'cd "/workspace/x"; echo a'` rewrites the `/workspace/x` inside — usually desired but worth a unit test fixture.

**Two reductions are credible:**

1. **Inline + simplify** if the only inputs to copy-backed are well-typed argv parts and a small env keylist, replace the whole token-scanner with `os.fspath`-aware prefix replacement on each argv element:
   ```python
   def _rewrite_argv(command, *, workspace_root, mounted_workspace_root):
       root = workspace_root.rstrip("/") or "/"
       if root == "/":
           return command
       return tuple(part.replace(root, mounted_workspace_root) for part in command)
   ```
   This loses the boundary discrimination — fine if `workspace_root` is itself an absolute path like `/workspace/<id>` (collisions like `/workspaceless` would be a real concern; document the assumption).
   Saves ~60 LOC.

2. If the boundary logic is load-bearing (please add a test that names what invariant it preserves), at least *fold it into copy_backed.py*: it has exactly one caller. The current shape (its own file, its own `__all__`, its own module docstring) is over-modularized. 108 LOC → ~40 LOC if folded.

---

## 4. `__init__.py` lazy-export machinery (~45 LOC of accidental complexity)

`__init__.py:23–64` defines `_LAZY_EXPORTS`, a custom `__getattr__`, and ~30 lines of indirection to defer three imports. The motivation is presumably to keep startup fast — but:

- All five external consumers (per grep in `backend/src/sandbox/daemon/...`) already import from submodules directly (`from sandbox.execution.contract`, `from sandbox.execution.orchestrator`).
- Tests import from submodules too.
- Therefore `__getattr__` is only ever hit by *theoretical* users of the top-level facade.

**Recommendation:** delete the `_LAZY_EXPORTS` map and `__getattr__`; either drop the corresponding `__all__` entries or accept three eager imports. The whole file drops from 64 LOC to ~15:

```python
"""Facade for guarded command execution."""

from sandbox.execution.contract import (
    CommandExecRequest, CommandExecResult, CommandExecutor, MountMode,
    OCCMutationClient, ShellProcessResult, SnapshotManifest,
    WorkspaceCapture, WorkspaceLeaseClient, WorkspaceReplacementMountSpec,
    WorkspaceSnapshotLease,
)
from sandbox.execution.orchestrator import execute_command
from sandbox.execution.policy import DEFAULT_COMMAND_EXEC_POLICY, CommandExecPolicy

__all__ = [...]  # same list, sans the two laziness-only entries
```

Saving: ~45 LOC, two indirections removed.

---

## 5. `entrypoints.py` (367 LOC) — death by tiny helpers

This is the privileged in-namespace helper. The shape is fine, but the helper-per-line factoring inflates it:

| Helper | LOC | Callers | Comment |
|---|---|---|---|
| `_fallback_ref` (274–280) | 7 | 1 (`_fail_bad_payload`) | inlinable |
| `_write_error` (283–285) | 3 | 2 | inlinable |
| `_write_control` (288–302) | 15 | 1 (`_fail`) | inlinable |
| `_json_error_line` (305–310) | 6 | 2 | inlinable |
| `_write_timings` (313–317) | 5 | 3 (kept — OK) | keep |
| `_called_process_message` (336–342) | 7 | 1 | inlinable |
| `_fail_bad_payload` (320–333) | 14 | 2 | keep |
| `_fail` (345–357) | 13 | 4 | keep |
| `_fd_path` (270–271) | 2 | 1 | inline |
| `_validate_overlay_path_text` (248–253) | 6 | 1 inside `_validate_mount_inputs` | inline — it's `policy.validate_overlay_path_text(p.as_posix())` |
| `_payload_request` (156–173) | 18 | 1 | keep (clarifies validation) |

Inlining the single-use helpers and the trivial wrappers reduces the file by ~60–80 LOC without losing clarity.

### Bugs / quality nits

- **entrypoints.py:242–245** — except branch closes fds with bare `os.close(fd)`; if close raises during cleanup, the original validation error is masked. Wrap each close in `contextlib.suppress(OSError)` (`_MountInputs.close` at line 148–153 already does this — copy that pattern).
- **entrypoints.py:115** — `exit_code = run_command_to_refs(...)` returns 0 here on success, but `run_command_to_refs` already exists at `workspace/environment.py:55` with *different behavior* (no namespace, host env). Two functions with the same name in two modules with different semantics is a maintenance trap. Rename one (or merge per §2a).
- **entrypoints.py:194–200** — `_umount(workspace_root)` runs `check=False`. If unmount silently fails, the next request can inherit the stale mount. Acceptable inside a private namespace (the namespace itself is the cleanup boundary on subprocess exit), but worth a one-line `# Why: namespace teardown collects the mount.` comment instead of nothing.

---

## 6. `overlay/capture.py` (277 LOC) — mostly justified, minor cuts

This is dense but earns its line count: it implements both an overlay walker (whiteout/opaque/xattr) and a copy-backed simulator for tests. Two specific cuts:

- **`_INTERMEDIATE_RUN_DIRS` constant** (`mounts.py:24`) — used in exactly one place. Inline as `for name in ("lower", "merged", "work"):`.
- **`_marker` and `_content`** (capture.py:166–179) — two-line factories. Inline or fold both into `OverlayPathChange` classmethods (`OverlayPathChange.marker(kind, path)` / `OverlayPathChange.content(kind, path, entry, ...)`) so the constructor's invariants live with the class. Mild saving (~10 LOC) and ergonomic improvement.
- **`_xattr_value` / `_has_xattr` / `_has_overlay_opaque_xattr` / `_is_overlay_whiteout`** — chained helpers (capture.py:242–274). Reads cleanly; leave as-is.

---

## 7. Contract / value types — verbose validation

`contract.py:38–73` (`CommandExecRequest.__post_init__`) is 36 lines of `object.__setattr__` chain on a frozen dataclass. Same pattern in `WorkspaceReplacementMountSpec.__post_init__` (187–211) and `OverlayShellRequest.__post_init__` (request.py:20–37) and `OverlayCapture.__post_init__` (result.py:28–38).

This is *correct* (frozen dataclass with normalization) but expensive to read. Two routes:

1. **Drop frozen**, since none of these are hashed or used as dict keys per grep. Then validation becomes ordinary `self.x = …` and ~50 LOC of `object.__setattr__` ceremony vanishes across the codebase.
2. **Keep frozen, factor a `_normalize` classmethod** that returns a tuple — verbose-but-uniform.

I'd take option (1) unless there's a frozen-as-invariant policy elsewhere in `sandbox/`.

### `WorkspaceCommandRunner` type alias (orchestrator.py:35–38)

```python
WorkspaceCommandRunner = Callable[..., ShellProcessResult]
```
`Callable[..., X]` accepts anything callable returning `X` — it provides no actual type safety on call sites. Drop the alias and inline `Callable[..., ShellProcessResult]` (or just type the parameter as `Any` / use a `Protocol`).

---

## 8. Per-file LOC table with reduction targets

| File | LOC | Target | Reason |
|---|---:|---:|---|
| `__init__.py` | 64 | 15 | drop lazy-export machinery (§4) |
| `contract.py` | 226 | 160 | unfreeze + collapse validation (§7) |
| `policy.py` | 109 | 95 | drop stale `__all__`; minor |
| `orchestrator.py` | 233 | 180 | extract one run_dir helper, lift timings dict assembly (§2b) |
| `entrypoints.py` | 367 | 260 | inline single-use helpers (§5) |
| `strategies/__init__.py` | 17 | (delete — pkg merges) | §1 |
| `strategies/base.py` | 39 | (merge) | §1 |
| `strategies/copy_backed.py` | 125 | 100 | inline path_rewrite (§3) |
| `strategies/private_namespace.py` | 174 | 140 | small trim |
| `workspace/__init__.py` | 0 | (delete) | empty |
| `workspace/path_rewrite.py` | 107 | (merge) | §3 |
| `workspace/capture.py` | 33 | (inline) | 4-arg wrapper around capture_changes (§2) |
| `workspace/environment.py` | 112 | 80 | merge with worker.run_user_command (§2a) |
| `workspace/mount.py` | 73 | 55 | drop fallback-key name inconsistency |
| `overlay/__init__.py` | 41 | 20 | drop re-exports we don't need |
| `overlay/worker.py` | 156 | 100 | merge run_user_command (§2a) |
| `overlay/runner.py` | 49 | 30 | fold OverlayInvoker (§2c) |
| `overlay/request.py` | 66 | 50 | unfreeze (§7) |
| `overlay/__init__.py + result.py` | 98 | 70 | unfreeze + drop unused from_dict if not boundary |
| `overlay/capture.py` | 277 | 240 | inline `_marker` / `_content` (§6) |
| `overlay/pipeline.py` | 80 | 30 | fold OverlayInvoker (§2c) |
| `overlay/change.py` | 82 | 70 | minor |
| `overlay/mounts.py` | 86 | 65 | inline `_INTERMEDIATE_RUN_DIRS` |
| **Total** | **2,614** | **~1,600** | **-39%** |

---

## 9. Findings by severity

### HIGH — must fix to meet stated rules
- **H-01** All intra-package imports are 4 segments — *violates the user-stated "≤ 3" rule*. Fix by collapsing `workspace/`, `strategies/`, `overlay/` into flat modules (§1).
- **H-02** Two complete command-runner stacks duplicate orchestration, run_dir derivation, and subprocess invocation (§2). Merge.

### MEDIUM — material code reduction with no behavior change
- **M-01** `path_rewrite.py` is a 108-LOC module with a single consumer; inline or fold (§3).
- **M-02** `__init__.py` lazy `__getattr__` machinery is unnecessary (§4). Delete.
- **M-03** `entrypoints.py` is inflated by ~10 single-use helpers (§5). Inline.
- **M-04** `OverlaySnapshotRunner` / `OverlayRuntimeInvoker` / `execute_request` form a 3-layer pass-through (§2c). Collapse.
- **M-05** Frozen-dataclass `__post_init__` ceremony across 4 value types (§7). Either unfreeze or factor a normalizer.
- **M-06** `_run_dir` implemented twice with identical body (orchestrator.py:189, pipeline.py:69). Extract.

### LOW — correctness / robustness nits
- **L-01** entrypoints.py:242–245 — except-branch fd close can mask the original exception. Wrap in `suppress(OSError)`.
- **L-02** Two `run_command_to_refs` symbols with different semantics (entrypoints/worker vs workspace/environment) invite confusion. Rename or merge.
- **L-03** `overlay/capture.py:12` imports from `sandbox.layer_stack._paths` — leaks a private submodule across package boundaries. Promote `relative_symlink_target_escapes` to the public layer_stack API.
- **L-04** `WorkspaceCommandRunner = Callable[..., ShellProcessResult]` (orchestrator.py:35) provides no type safety; drop or replace with a `Protocol`.
- **L-05** `workspace/mount.py:60–65` builds `fallback_key` via a per-strategy name dance even though only one branch is reachable today. Hard-code `command_exec.private_mount_fallback` and inline.

### INFO
- **I-01** No `# pragma: no cover` lies that I can see, but `environment.py:97` carries one over a `try/except ValueError` whose guard is actually reachable if `commonpath` raises — leave the test but drop the pragma.
- **I-02** All overlay path-types provide `to_dict`/`from_dict`. If these only cross a single in-process boundary (daemon ↔ worker), the JSON round-trip is dead weight; they don't pay for themselves unless something persists them.

---

## 10. Suggested execution plan (no behavior change required)

1. **Step 1 — Collapse packages, fix import depth (closes H-01).** Move `strategies/*`, `workspace/*`, `overlay/*` into flat modules. Rewrite imports. Run tests; this is mechanical.
2. **Step 2 — Drop `__init__.py` lazy machinery (M-02).** Mechanical.
3. **Step 3 — Merge `run_command_to_refs` / `run_user_command` (H-02 a, L-02).** Plumb `env_builder`; introduce `timeout_exit_code=124` knob.
4. **Step 4 — Fold `path_rewrite.py` into copy_backed (M-01).** Decide whether the boundary scanner is load-bearing; add a regression test for the exact invariant.
5. **Step 5 — Collapse `OverlaySnapshotRunner` / `OverlayRuntimeInvoker` (M-04).** Tests substitute a `run_command` callable.
6. **Step 6 — Inline single-use helpers in `entrypoints.py` (M-03), apply L-01.**
7. **Step 7 — Decide frozen vs. mutable for value types (M-05).** Apply uniformly.

Each step is a self-contained PR; the diff is dominated by deletions.

---

## Closing note

The codebase is *correct* and *readable*; the issue is structural. Roughly a third of the lines are organizational scaffolding (lazy facades, micro-helpers, two parallel pipelines that should be one, wrapper-of-wrapper invokers). Flattening to ~1,600 LOC across ~8 modules is realistic and brings imports to the stated 3-segment limit.
