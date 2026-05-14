# Sandbox Daemon — Code Review

**Scope:** `backend/src/sandbox/daemon/` — 25 files, 2735 LOC
**Depth:** standard (all files read top-to-bottom, cross-referenced against consumers in `sandbox.layer_stack`, `sandbox.occ`, `sandbox.execution`, and the test tree)

## Summary

The daemon module is structurally sound: input is bounded (`MAX_REQUEST_BYTES`, `_MAX_ARGV_BYTES`, `_MAX_OUT_OF_WORKSPACE_READ_BYTES`), filesystem access avoids symlink traversal via `O_NOFOLLOW`, env mappings reject NUL/`=`/empty keys, and the single-path contract is enforced before any leasing. No data-loss, no authentication-bypass, no command/path injection on the request side. All findings are WARNING-class — quality, redundancy, and import-shape concerns.

Three structural problems stand out:

1. **Boilerplate duplication.** Four copies of the same `layer_stack_root(args)` validator (`request_context.py`, `health.py`, `shell_runner.py`, `metrics.py`). Per-verb in/out-of-workspace payload shapes in `write.py`/`edit.py` repeat 5 nearly-identical timing dicts. `handler/workspace.py` repeats the same `_required_str` wrap six times.
2. **Vestigial indirection.** `LayerStackClient.release_lease`/`prepare_workspace_snapshot` take a `workspace_ref` kwarg solely to `del` it. `LayerStackWorkspaceServer` is a thin class over module-level functions that already exist. `_drop_transient_lowerdir as _drop_transient_lowerdir` is re-exported through `shell_runner.py` for one unit test. `drop_backend_cache` pops a raw key that `build_occ_backend` never inserts.
3. **Long import chains.** `sandbox.daemon.handler.tools.edit/read/write` are 5 segments deep — over the user's 3-hop cap. The `tools/` subpackage holds three files and adds nothing the dispatcher can't address directly. `sandbox.occ.changeset.types`, `sandbox.occ.content.{hashing,gitignore_oracle}`, `sandbox.layer_stack.workspace_binding` are all 4-segment hops imported by the daemon.

- **Total LOC:** 2735 → estimated achievable ~2150 (≈21% reduction) without functional loss.
- Findings: **CRITICAL 0** / **HIGH 0** / **MEDIUM 14** / **LOW 5**

## Findings by severity

### CRITICAL
None.

### HIGH
None. The closest call was `__main__.py:24-33`: the flock fd is closed in `finally`, releasing the lock independently of `serve()`'s pid-file cleanup. Traced through — the `finally` order is safe in both happy and failure paths because `serve()` always reaches its own `finally` before re-raising, and a process death also releases the flock at kernel level. Not a finding.

### MEDIUM (bugs/correctness/simplicity wins)

#### M-01 — Bypass of `register_op` duplicate-check
`rpc/dispatcher.py:181` calls `OP_TABLE.update({...})` directly with the bootstrap table, defeating the `if op in OP_TABLE` guard defined at line 38. If a future peer-handler module ever registers the same op (e.g. an alias for `api.shell`), the second registration silently wins instead of raising. Either route everything through `register_op`, or delete the unused `register_op` API.

#### M-02 — `drop_backend_cache` pops a key that is never inserted
`service/occ_backend.py:84-90` pops both `root` (raw) and `Path(root).resolve(strict=False)` from `_BACKEND_CACHE`. But `build_occ_backend` only ever inserts under the resolved key (line 50/79 via `_backend_cache_key`). The raw-key pop is dead code. Drop line 89.

#### M-03 — Variable shadowing of own function name
The pattern `def layer_stack_root(args): ... layer_stack_root = str(args.get(...))` repeats in:
- `handler/request_context.py:104-108`
- `handler/health.py:143-147`
- `service/shell_runner.py:165-169`
- `handler/metrics.py:39-43` (via `_manager` local pattern, similar)
Plus the same shape in tool handlers as a local that shadows the imported name (`edit.py:49`, `write.py:28`, `read.py:24`). Rename the local to `root` to keep the function name unshadowed; better still, consolidate into one helper in `request_context.py` and import.

#### M-04 — Four near-identical copies of `layer_stack_root(args)` validator
Same logic in `request_context.py:104`, `health.py:143`, `shell_runner.py:165`, `metrics.py:39` (and a 7-line `_required_str/_layer_stack_root` ladder in `handler/workspace.py:102-110`). Promote one canonical `require_layer_stack_root(args)` in `request_context.py` and import it everywhere. Saves ~25 LOC and removes the M-03 shadowing.

#### M-05 — Dead re-export with self-aliasing
`service/shell_runner.py:17-19`:
```python
from sandbox.execution.orchestrator import (
    _drop_transient_lowerdir as _drop_transient_lowerdir,
)
```
The self-alias suppresses an "unused import" warning. The only consumer is `tests/unit_test/test_sandbox/test_command_exec/test_capture_to_occ_client.py:240`. Fix: update the test to import from `sandbox.execution.orchestrator` directly and drop these three lines.

#### M-06 — `workspace_ref` kwargs exist only to be deleted
`service/layer_stack_client.py:68-79`:
```python
def prepare_workspace_snapshot(self, *, workspace_ref: str = "", request_id: str) -> ...:
    del workspace_ref
    return self.manager.prepare_workspace_snapshot(request_id)

def release_lease(self, *, workspace_ref: str = "", lease_id: str) -> bool:
    del workspace_ref
    return self.manager.release_lease(lease_id)
```
The kwargs are there to satisfy a port signature in `sandbox.execution.contract.WorkspaceLeaseClient.release_lease(*, workspace_ref, lease_id)`. Two options:
- (a) Drop `workspace_ref` from the contract — every daemon-side consumer ignores it.
- (b) Remove the default and accept it positionally without the `del`.
Either way, the `del` statements are smell.

#### M-07 — `_FENCED_STAGING_ROOTS` set is never reaped
`service/workspace_server.py:27` and `_fence_stale_staging_once` add to a module-level set that is only cleared by `clear_layer_stack_server_caches_for_tests`. `drop_layer_stack_manager(key)` evicts the manager but leaves `_FENCED_STAGING_ROOTS` populated, so re-binding the same root will skip fence-on-first-use. Drift, not a bug today because the fence is idempotent — but the test helper hints the author already noticed the asymmetry. Add `_FENCED_STAGING_ROOTS.discard(key)` to `drop_layer_stack_manager`.

#### M-08 — Redundant existence check after binding require
`handler/tools/read.py:49-52`:
```python
services = backend_services(layer_stack_root)
if not Path(layer_stack_root).exists():
    raise WorkspaceBindingError(f"layer-stack root does not exist: {layer_stack_root}")
```
`require_workspace_binding(layer_stack_root)` was already called at line 25 and raises if the root or binding is missing. Drop the redundant check (3 lines).

#### M-09 — `_MAX_OUT_OF_WORKSPACE_READ_BYTES` defined inside function
`handler/tools/read.py:96` declares the constant inside `_read_out_of_workspace`, so it is re-bound on every call. Trivial — promote to module scope. Same shape `_MAX_ARGV_BYTES = 128 * 1024` (`shell_runner.py:113`) is already module-scope; do the same here.

#### M-10 — `LayerStackWorkspaceServer` is a thin shim
`service/workspace_server.py:92-151` wraps three module-level functions (`build_workspace_base`, `read_workspace_binding`, `_manager.*`) in a class that adds only "cache the manager I just looked up". Every consumer (`handler/workspace.py`) constructs a new instance per call, so the cached `_manager` field never amortizes across requests. Either:
- Drop the class and call the module helpers (`build_workspace_base`, `get_layer_stack_manager`, `_validate_manifest_for_root`) directly from `handler/workspace.py`, **or**
- Make the class actually cache per-root and live behind a module-level dict.
This is the biggest single LOC win (~45 lines).

#### M-11 — In/out-of-workspace payload duplication in `write.py`/`edit.py`
Each verb has two near-identical payload-shape blocks (one for in-workspace via `project_changeset`, one for out-of-workspace hand-built). The conflict payload in `edit.py:190-212` and `write.py:163-176` could share one builder in `request_context.py`. Saves ~25 LOC and removes the manual `timings` plumbing.

#### M-12 — `_to_jsonable` recurses on already-jsonable shapes
`rpc/dispatcher.py:155-164` recursively walks every list/dict/dataclass/SimpleNamespace. Handlers already return plain `dict[str, object]` from JSON-validated values; the recursion is needed only for the dataclass case (`ChangesetResult`, etc.), which the per-verb projections already convert. Either restrict `_to_jsonable` to dataclasses + SimpleNamespace (keep generic safety for unforeseen paths), or document that handlers must return plain dicts and drop the recursion entirely.

#### M-13 — Long import chains over 3-hop cap
The daemon's deepest module chains:

| Module | Segments |
| --- | --- |
| `sandbox.daemon.handler.tools.edit` / `.read` / `.write` | 5 |
| `sandbox.daemon.handler.request_context` | 4 |
| `sandbox.daemon.service.workspace_server` | 4 |
| `sandbox.daemon.service.layer_stack_client` | 4 |
| `sandbox.daemon.service.result_projection` | 4 |
| `sandbox.daemon.service.workspace_binding` | 4 |
| `sandbox.daemon.service.occ_backend` | 4 |
| `sandbox.occ.changeset.types` | 4 |
| `sandbox.occ.content.gitignore_oracle` | 4 |
| `sandbox.occ.content.hashing` | 4 |
| `sandbox.layer_stack.workspace_binding` | 3 (boundary case) |

The `handler/tools/` subpackage is the highest-leverage target. It contains exactly three files (`edit.py`, `read.py`, `write.py`); flattening into `handler/` brings every public verb to ≤4 hops and matches the existing one-file-per-verb pattern for `health`/`metrics`/`overlay`/`workspace`. `dispatcher.py:178` already imports them as a group — the rename is a one-line change there plus `git mv`.

#### M-14 — `__main__.py:30-32` accidentally suppresses errors
```python
finally:
    if pid_lock_fd is not None:
        with contextlib.suppress(OSError):
            os.close(pid_lock_fd)
```
`os.close` raises `OSError` only for `EBADF` (programming bug) or `EIO` (kernel can't flush). Suppressing `EBADF` is fine; suppressing `EIO` silently leaks state. This is a daemon shutdown path so the practical impact is nil — but a bare `with contextlib.suppress(OSError):` is harsher than needed. Tighten to `suppress(EBADF)` or just let it raise.

### LOW
- `handler/workspace.py:21-22` — `reset=False` triggers a separate code path; the `if reset:` guard makes `_drop_peer_runtime_caches` part of `build_workspace_base`'s public contract but the docstring doesn't say so.
- `handler/health.py:89-92` — `if len(shell_services) != 4` hardcodes a tuple length that the rest of the module knows by name. Drop the length check; the tuple structure is enforced by `services()`'s return-type annotation.
- `service/result_projection.py:75-78` — `getattr(gitignore, "cache_hits", 0)` defends against test mocks that don't implement counters; comment says so explicitly. Keep, but consider using a `Protocol` instead of duck-typing.
- `rpc/server.py:147-154` — bare `Exception` catch around `writer.close()` is fine but the `pragma: no cover` mask hides genuine close errors during dev. Logging at DEBUG would be cheap.
- `async_bridge.py:219-225` — `_shutdown_standalone_loop_clients_sync` uses bare `Exception`; same comment as above.

## LOC reduction targets

| File | Current | Target | Primary cuts |
| --- | --- | --- | --- |
| `async_bridge.py` | 300 | 220 | Inline `_running_loop_on_this_thread`, `_await_any`; collapse `_shutdown_standalone_loop_clients_sync` into the main shutdown path; trim docstring prose to reference-level. Core dispatch logic stays. |
| `handler/tools/edit.py` | 236 | 190 | Extract conflict-payload builder shared with `write.py`; promote `_apply_edits` to `request_context.py`; drop duplicated timings dict construction. |
| `rpc/server.py` | 232 | 200 | Inline `_request_too_large_envelope` and `_validate_envelope` only if they shrink. Mostly already tight; small cuts in the `finally`/`serve` task plumbing. |
| `handler/request_context.py` | 227 | 180 | Drop `services()` 1-liner (inline `occ_backend.build_occ_backend`); promote conflict-payload builder from `edit`/`write`; collapse `_open_no_follow` chain comments. |
| `rpc/dispatcher.py` | 212 | 180 | Restrict `_to_jsonable` to dataclasses; remove `register_op` if `_load_peer_bootstraps` becomes the only registration site (or vice versa). |
| `handler/tools/write.py` | 205 | 165 | Share conflict-payload builder; the `read_base_hash` closure can become a method on `OccBackend`. |
| `service/shell_runner.py` | 181 | 165 | Drop `_drop_transient_lowerdir` re-export (M-05); fold `_safe_env` validation into `CommandExecRequest.__post_init__` if appropriate. Already pretty tight. |
| `service/workspace_server.py` | 173 | 120 | Resolve M-10 — pick the class OR the module functions, not both. Cuts ~45 lines. |
| `handler/health.py` | 150 | 110 | Consolidate the three probe bodies' boilerplate (each does `binding` + `manager` + sanity check); the `_run_probe` wrapper is good, keep it. |
| `handler/workspace.py` | 138 | 105 | Drop the `_required_str` ladder (M-04). Each handler can be 5-6 lines. |
| `handler/tools/read.py` | 120 | 95 | Drop redundant existence check (M-08); promote constant to module scope (M-09); share OOO payload builder. |
| `service/occ_backend.py` | 115 | 105 | Drop dead raw-key pop in `drop_backend_cache` (M-02). Mostly already tight. |
| `service/result_projection.py` | 87 | 87 | **Already tight — no reduction.** |
| `service/layer_stack_client.py` | 85 | 60 | Drop `workspace_ref` kwargs (M-06); the remaining 1-line passthroughs stay. |
| `scripts/thin_client.py` | 60 | 60 | **Already tight.** |
| `__main__.py` | 51 | 51 | **Already tight.** |
| `handler/overlay.py` | 46 | 46 | **Already tight.** |
| `handler/metrics.py` | 46 | 40 | Fold the duplicated `_manager` validator into M-04's canonical helper. |
| `service/workspace_binding.py` | 38 | 38 | **Already tight.** |

**Estimated total: 2735 → ~2150 (≈21%).**

## Import-chain offenders

| Import | Segments | Suggested target |
| --- | --- | --- |
| `sandbox.daemon.handler.tools.edit` (and `.read`, `.write`) | 5 | Flatten `handler/tools/` → `handler/` (only 3 files). All four other handlers are already direct children of `handler/`. |
| `sandbox.daemon.handler.request_context` | 4 | Acceptable boundary case; collapsing into `handler/__init__` would hurt cohesion. Leave. |
| `sandbox.occ.changeset.types` (external) | 4 | Re-export `ChangesetResult`, `FileResult`, `build_api_write_change` from `sandbox.occ` so the daemon imports `from sandbox.occ import ChangesetResult`. |
| `sandbox.occ.content.gitignore_oracle`, `.hashing` (external) | 4 | Same: re-export `SnapshotGitignoreOracle`, `ContentHasher` from `sandbox.occ`. |
| `sandbox.layer_stack.workspace_binding` (external) | 3 | OK at the boundary. |

The internal flattening (M-13) is the only one the daemon module owns; the `occ.*` and `layer_stack.*` flattenings require changes outside scope but are noted because they appear in nearly every daemon file.

## Top 5 recommended actions

1. **Flatten `handler/tools/` into `handler/`** (M-13). One `git mv` + 3 import updates in `dispatcher.py` and the test tree. Brings every public verb to ≤4 hops and matches the existing per-verb file layout.
2. **Consolidate `layer_stack_root(args)` into one helper** (M-04, M-03). Removes ~25 LOC, eliminates four near-identical copies and the self-shadowing pattern, and is the cleanest signal of "this module has a canonical validation entry point."
3. **Decide what `LayerStackWorkspaceServer` is for** (M-10). It's currently a per-call object wrapping module functions that don't need wrapping; pick one shape. Largest single-file LOC drop available.
4. **Delete the dead `workspace_ref` kwargs and the `_drop_transient_lowerdir` re-export** (M-05, M-06). Small but high-clarity: `del workspace_ref` is a documentation smell, and one self-aliased import is propping up a single test.
5. **Pick a single registration path for `OP_TABLE`** (M-01). Either route every op through `register_op` (keep the duplicate-check), or delete `register_op` and document `_load_peer_bootstraps` as the sole entry point. The current setup has the validation but skips it.

---

_Reviewed: 2026-05-15_
_Reviewer: gsd-code-reviewer_
_Depth: standard_
