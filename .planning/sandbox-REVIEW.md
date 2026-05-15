# `backend/src/sandbox` — Code Review

**Reviewed:** `backend/src/sandbox/` — 132 `.py` files, **15,676 LOC** across 9 subsystems
**Date:** 2026-05-15
**Branch:** `codex/fix-dot-path-normalization-tests`
**Method:** 7 parallel reviewers, one per subsystem; per-subsystem reports at `/tmp/sandbox_review/`.

**Focus criteria (priority order):**
0. Naming convention of files/folders/functions/classes — *Are the semantics clear?*
1. Implementation quality — real bugs only, not style
2. Simplicity / redundancy — 200 LOC → 150 → 100 → 50, no functionality lost
3. Import depth ≤3 dots after `sandbox.`

---

## TL;DR

The package is **structurally sound but lexically noisy and ~20–25% over-coded.**

- **Generic-noun naming** (`Manager`, `Handler`, `Service`, `Context`, `Router`, `Client`, `Adapter`, `Bridge`, `Projection`, `Oracle`) is endemic. The same words mean different things in `daemon/`, `provider/`, `plugin/`, `occ/`, and `layer_stack/`. Five files named `context.py`, three named `registry.py`, two named `bootstrap.py`, two named `transaction.py`.
- **Two parallel execution pipelines** (`execution/orchestrator.py` and `execution/overlay_runner.py → overlay_pipeline.py → overlay_worker.py → overlay_mounts.py`) implement the same operation. **~400 LOC of duplicate scaffolding** in `execution/` alone.
- **Import-depth ≤3 is violated systematically.** Worst: `sandbox.provider.daytona.client.sync_client` (**depth 5**). Subdir-with-2-real-files anti-pattern repeats in `plugin/runtime/`, `occ/stage/`, `occ/content/`, `occ/changeset/`, `provider/daytona/client/`.
- **Real correctness bugs:** 9 CRITICAL/HIGH, including a sandbox-internal RCE on plugin install, two `commit_queue` race conditions, a silently-dropped health-probe timeout, and a leaked module-level `ThreadPoolExecutor`.
- **Total achievable reduction: ~15,700 LOC → ~12,000 LOC (–24%)**, plus 8–12 files deleted.

---

## 1. Headline Cross-Cutting Patterns

### Pattern A — Generic-noun file/class names (criterion 0)

Same problem, every subsystem:

| Generic name | Where | What it actually is | Proposed |
|---|---|---|---|
| `Manager` | `layer_stack/manager.py` (326 LOC) | The layer stack itself | `LayerStack` |
| `Service` | `occ/service.py` (257 LOC) | OCC orchestration root | keep, but document |
| `Router` | `occ/router.py` (242 LOC) | Prepares changesets (not routing) | `ChangesetPreparer` |
| `Client` | `occ/client.py`, `daemon/service/layer_stack_client.py` | In-process facades (no remote) | `BoundOccService` / `LayerStackAdapter` |
| `Adapter` | `provider/daytona/adapter.py` (346 LOC) | The provider impl itself | `DaytonaProvider` / fold name |
| `Context` | `host/context_preparer.py`, `plugin/runtime/context.py`, `provider/daytona/context.py`, `daemon/handler/request_context.py` | All different things | Disambiguate each; delete `host/context_preparer.py` (M1 below) |
| `Bridge` | `daemon/async_bridge.py` (300 LOC) | Loop-aware sync resolver for awaitables; **used outside `daemon/`** | Move to `sandbox/io_loop.py` |
| `Projection` | `daemon/service/result_projection.py`, `plugin/projection.py` | DB/ML term collision; both are converters | `wire_payload.py`, `workspace_view.py` |
| `Oracle` | `occ/content/gitignore_oracle.py` | Cached gitignore matcher | `gitignore.py` / `GitignoreMatcher` |
| `Ports` | `occ/ports.py` | `typing.Protocol` declarations | `protocols.py` |

### Pattern B — Name collisions across subsystems

Same filename, different meaning, no shared base class:

- `transaction.py` — `layer_stack/transaction.py` vs `occ/stage/transaction.py`
- `bootstrap.py` — `host/bootstrap.py` (lifecycle) vs `provider/daytona/bootstrap.py` (registers provider; 20 LOC)
- `registry.py` — `provider/registry.py` vs `plugin/runtime/registry.py` (227 LOC)
- `context.py` — 4 different files (see Pattern A)
- `overlay` — `occ/overlay.py` vs `execution/overlay_*.py` (8 files)

### Pattern C — `_underscore` modules imported across packages

Python's leading-underscore is a *module-level* private signal. These violate that:

- `api/_impl/_audit.py`, `_classifiers.py`, `_payload.py`, `_results.py` — imported by every sibling. The `_impl/` parent is already private; the file-level `_` stutters.
- `layer_stack/_paths.py`, `layer_stack/_storage_lock.py` — imported by 7 sibling modules.
- `occ/stage/_edit.py` — imported by `stage/merge.py`. 39 LOC; should inline.
- `provider/daytona/adapter.py` imports **10** `_`-prefixed names from `sync_client` (`_APP_*`, `_normalize_*`, `_paginate_all`, `_call_with_optional_timeout`, etc.). Either rename the names public or stop reaching across the wall.

### Pattern D — Subdirectory-with-2-real-files anti-pattern

Each adds a level of import depth and earns no organizational gain:

| Subdir | Real files | Subsystem | Fix |
|---|---|---|---|
| `provider/daytona/client/` | 4 (depth 5!) | provider | Collapse to `provider/daytona/client.py` |
| `plugin/runtime/` | 2 (depth 4) | plugin | Flatten: `plugin/op_context.py`, `plugin/op_registry.py` |
| `occ/stage/` | 4 real + 2 shims | occ | Flatten: `occ/commit_transaction.py`, `occ/stage.py`, `occ/stage_policy.py` |
| `occ/content/` | 2 (depth 4) | occ | Flatten: `occ/hashing.py`, `occ/gitignore.py` |
| `occ/changeset/` | 2 (depth 4) | occ | Merge into one `occ/changeset.py` |

### Pattern E — 2-line re-export shims

Pure scaffolding. Delete:

- `occ/stage/direct.py` (8 LOC, re-exports `DirectStager` from `merge.py`)
- `occ/stage/gated.py` (8 LOC, ditto)
- `occ/timing_keys.py` (8 LOC, re-exports `sandbox.timing_keys.TimingKey`)
- `host/context_preparer.py` (50 LOC, two Protocol stubs + one factory call)
- `plugin/runtime/__init__.py` (after M1 flatten)

---

## 2. Real Bugs (criterion 1)

### CRITICAL

**C1. Plugin install runs `setup.sh` with no signature/allowlist check → sandbox-internal RCE**
`plugin/install.py:283-299`. `manifest.setup is not None` is the only gate; any plugin registered via `discover_plugins()` runs arbitrary code in the sandbox on first call. If the catalog is reachable by less-privileged code, this is a sandbox-internal RCE. *Fix: hash-pin and allowlist before `_upload_and_run_setup` runs; document the trust boundary.*

**C2. Two execution pipelines doing the same work**
`execution/orchestrator.py:41` (`execute_command`) and `execution/overlay_runner.py:31` (`OverlaySnapshotRunner.shell`) both lease a snapshot, mount a copy-backed merged tree, run user command, capture upperdir. The differences are: (a) orchestrator pushes to OCC and supports two mount strategies; (b) overlay_runner is copy-backed only and skips OCC. Two distinct `shutil.copytree`-based materializers exist. *Fix: collapse pipeline #2 into pipeline #1 with `occ_apply=False` flag; delete `overlay_runner.py`, `overlay_pipeline.py`, `overlay_worker.py`, `overlay_mounts.py`. ~400 LOC removed.*

**C3. Daytona health-probe timeout is silently dropped**
`provider/daytona/client/sync_client.py:187-214` — `_call_with_optional_timeout` omits the `timeout` kwarg entirely when the SDK method's signature lacks it (true for `client.list` and `client.get` in 0.23.x). Caller (e.g. `adapter.get_health` L128) thinks it has 30 s; reality is "whatever the SDK transport defaults to" (often unbounded). Documented memory note `daytona_pending_build_root_cause.md` shows 300 s hangs. *Fix: wrap in `ThreadPoolExecutor.future.result(timeout=...)` when SDK lacks native support.*

**C4. Module-level `ThreadPoolExecutor` never shut down → leaked threads at interpreter exit**
`host/bootstrap.py:19-22` — `_BUNDLE_UPLOAD_EXECUTOR = ThreadPoolExecutor(max_workers=4)`. No `.shutdown()`, no `atexit` hook. Caller is sync and immediately joins the future, so the executor itself is dead weight. *Fix: register `atexit.register(executor.shutdown, wait=False, cancel_futures=True)` or remove the executor entirely (callers can `asyncio.to_thread`).*

**C5. Double `LayerStackManager` over same root → leaked writer flock + uncoordinated transactions**
`plugin/handler.py:296-306` + `plugin/projection.py:73-75`. `WorkspaceProjection(layer_stack_root)` constructs a *fresh* `LayerStackManager` per plugin call. The daemon's primary manager already owns the writer flock for the same root; the in-process refcount in `_storage_lock.py` keeps the flock alive forever even after the LRU evicts the projection. Each manager has its own `threading.RLock`, so OCC CAS can race between daemon commits and plugin-initiated squash. *Fix: inject the host manager via `PluginOpContext`, or add explicit `WorkspaceProjection.close()` and call it on eviction.*

### HIGH (correctness)

**H1. `commit_queue._StopItem` re-enqueued at tail can keep the worker processing post-shutdown**
`occ/commit_queue.py:139-141` + `submit()` reads `_closed` unlocked. *Fix: synchronize `_closed`+`put()` under a lock, or commit the partial batch and exit instead of re-enqueueing `_STOP`.*

**H2. `commit_queue` per-batch `except BaseException` calls `set_exception` on already-resolved futures**
`occ/commit_queue.py:201-206` — raises `InvalidStateError` on the partially-set batch; other futures hang. *Fix: track `done_indices` and skip `set_exception` for futures already set.*

**H3. `async_bridge._ensure_standalone_loop` race spawns orphan loop threads on timeout**
`daemon/async_bridge.py:138-180` — `thread.start()` and `ready.wait(5s)` inside the cache lock; on timeout the orphan daemon thread is never reaped, and the next call spawns another. *Fix: move thread setup outside the lock; on timeout, stop the orphan loop.*

**H4. Out-of-workspace `write_text_no_follow(overwrite=True)` is non-atomic but returns `status: "ok"` matching the OCC-atomic in-workspace path**
`daemon/handler/write.py:148-193`. Crash mid-write leaves the file truncated. *Fix: temp-and-rename, or add `"atomic": false` to the response so callers know the durability is different.*

**H5. `plugin/install.py` lockdir busy-loop has no stale-PID detection**
`plugin/install.py:222-235` — `mkdir <lock>` retried 600× at 1 s. A SIGKILL'd daemon orphans the lockdir for 10 minutes. *Fix: use `flock(2)` on a regular file (same pattern as `layer_stack/_storage_lock.py`), or write a PID file and `kill -0` check.*

**H6. `audit/translation.py` and `api/_impl/_classifiers.py` disagree on conflict markers**
`audit/translation.py:208-213`. `_CONFLICT_ERROR_MARKERS` includes `"aborted_version"` and `"content changed"`; `_classifiers` doesn't. A failure that bypasses `audited_operation`'s gate gets reclassified as `OPERATION_CONFLICTED` by `failed_event` — verb raises, audit log says "conflict". Unify on one classifier set.

**H7. `host/daemon_client.py:_is_bootstrap_ready_response` hard-codes `WorkspaceBindingError`-as-ready in the generic readiness retry path**
`host/daemon_client.py:280-309`. The retry on transient connect failure declares "ready" and continues the original op, which then fails because workspace is not bound. Trades a clear failure for a confusing downstream one.

**H8. `overlay_capture._payload_paths` materializes the whole tree via `sorted(rglob("*"))` → OOM on large captures**
`execution/overlay_capture.py:181-208`. For a 1M-file workspace, a 1M-element `list[Path]` is sorted in memory. *Fix: `os.walk(topdown=True)` with sorted dirs at each level. Same ordering, O(width) memory.*

**H9. `execution/workspace_environment.py:76-91` and `strategy_private_namespace.py:86-100` — subprocess group not cleaned on timeout**
On `TimeoutExpired`, the immediate child is killed but grandchildren survive (`bash -c "sleep 1000 &"` keeps running). Workspace commands routinely fork. *Fix: `start_new_session=True` + `os.killpg(p.pid, SIGKILL)` on timeout.*

### MEDIUM (correctness)

- **M1.** `provider/daytona/client/credentials.py:16-20` — `start.parents[6]` raises `IndexError` on shallow paths (installed venv).
- **M2.** `provider/daytona/client/credentials.py:61,80` — `assert factory_name in (...)` stripped under `python -O`; replace with `if ... raise ValueError`.
- **M3.** `provider/daytona/client/shutdown.py:28-44` — thread-per-stale-client with sequential 5 s joins; N stale clients block shutdown N×5 s.
- **M4.** `provider/daytona/bash.py:_UNPARSEABLE_EXIT_WARNED` — process-wide latch with no reset; identical bugs silently squashed for process lifetime.
- **M5.** `daemon/service/occ_backend.py:_BACKEND_CACHE` is `OrderedDict` with no lock, but `run_sync_in_executor` runs handler code in worker threads that mutate it. *Fix: add a `threading.Lock`.*
- **M6.** `layer_stack/maintenance.py:60` — `os.replace(staging_dir, layer_dir)` without `fsync(layer_dir.parent)` (publisher.py does it correctly).
- **M7.** `layer_stack/manager.py:170-172` — `prepare_workspace_snapshot` leaks materialized `transient-lowerdirs/*` on success path; `release_lease` only removes layers.
- **M8.** `occ/overlay.py:60,68-82` — `_kept_children_for(rel="")` treats every item as a child of root and tries to add it; latent root-opaque-dir bug.
- **M9.** `host/bootstrap.py:146-152` — `finish_runtime_bundle_upload` swallows all `Exception`; should narrow to `RuntimeError`.
- **M10.** `host/runtime_bundle.py:321-332` — chunked base64 upload (~30 RTTs per ~1 MB bundle) with no resume; orphan `.staging` files accumulate on failure.

---

## 3. Simplicity & Redundancy (criterion 2)

### Biggest wins (single PR each)

**S1. Collapse the two execution pipelines** — see C2. Delete `overlay_runner.py`, `overlay_pipeline.py`, `overlay_worker.py`, `overlay_mounts.py`. **~400 LOC raw, ~250 LOC net.**

**S2. Merge 8 `execution/overlay_*.py` into 3 files.** Move `OverlayShellRequest`/`OverlayCapture` into `contract.py`. Rename `overlay_change.py` → `path_change.py`, keep `overlay_capture.py` as `capture.py`. Delete `overlay_pipeline.py`. **~250 LOC.**

**S3. Merge 3 `execution/workspace_*.py` into 1.** `workspace_capture.py` is 33 LOC holding one 18-line function with one caller — inline. Rename `workspace_environment.py` → `subprocess_runner.py`. Fold `workspace_mount.py` into orchestrator. **~80 LOC.**

**S4. Collapse `provider/daytona/client/` into a single `client.py`.** 4 files (549 LOC) → 1 file (~380 LOC) with a single `ClientCache` class parameterized by key-fn covering sync+async. **~170 LOC + fixes depth-5 violation.**

**S5. Flatten `occ/{stage,content,changeset}/` subdirs.** Delete 2-line shims (`direct.py`, `gated.py`), inline `_edit.py`, merge `changeset/types.py + changeset/prepared.py`, promote `stage/transaction.py` and `stage/merge.py` and `stage/policy.py` up to `occ/`. **~150 LOC + 4 file deletions, all imports drop to depth 3.**

**S6. Flatten `plugin/runtime/` into `plugin/`.** `plugin/runtime/__init__.py`, `runtime/context.py` → `plugin/op_context.py`, `runtime/registry.py` → `plugin/op_registry.py`. **Depth 4 → 3.** Watch for plugin-author breakage: keep a deprecation shim if `sandbox.plugin.runtime` is in the public catalog contract.

**S7. Delete `host/context_preparer.py`.** 50 LOC for one factory call (`adapter.context_preparer(sandbox_id)`) plus two Protocol stubs that describe "a dict you can `.get` from and `__setitem__` into". *Inline at the call site.*

**S8. Inline 5-layer dispatch in `host/daemon_client.py`.** `_call_daemon` → `_exec_daemon_call` → `_should_retry_after_connect_failure` → `_check_daemon_readiness_after_spawn` → `_readiness_request_for_original` — 5 helpers, several with 1 caller. Compress to one `async def _dispatch_once_with_retry()`. **~60 LOC.**

**S9. Data-driven `_add_if_exists` loop in `host/runtime_bundle.py:161-185`.** 6 near-identical 4-line blocks → one loop over a tuple of names. **~30 LOC.**

**S10. Extract two helpers from `occ/stage/transaction.py:revalidate_and_publish` (115-line god method).** Pull out `_accumulate_route_timings` and `_atomic_or_overlay_dropped`. **~65 LOC.**

### Smaller wins

- `api/_control.py` — 10 of 14 public functions are one-line passthroughs to `host.lifecycle`/`provider.registry`. Inline into `api/__init__.py`. **~40 LOC.**
- `audit/translation.py` — inline `_subsystem_event`, `_terminal_type` (called once each). **~15 LOC.**
- `timing.py` — `_has_timing`/`_has_any_timing`/`_matches_timing_prefix`/`_STRINGIFIED_TIMING_KEY_PREFIXES` is dead if upstream always normalizes (it does, via `normalize_timing_map` in `audit/translation.py:131`). **~50 LOC.**
- `occ/service.py:_wrap_commit_result` — 8 kwarg helper called from 2 mirrored sites. 37 LOC → ~15 LOC. **~15 LOC.**
- `daemon/handler/health.py:84` — dead probe (comment says "for side effect" but the side effect is already in line 81). **3 LOC.**
- `daemon/service/result_projection.py:committed_paths` — fallback ladder. **~8 LOC.**

---

## 4. Import Depth (criterion 3)

### Current depth-≥4 imports

| Path | Depth | Subsystem | Fix |
|---|---|---|---|
| `sandbox.provider.daytona.client.sync_client` (and `.async_client`, `.credentials`, `.shutdown`) | **5** | provider | Collapse `client/` → `client.py` (S4) |
| `sandbox.provider.daytona.{adapter,bash,bootstrap,context,errors,workspace}` | 4 | provider | Promote files up to `provider/` (optional, more disruptive) |
| `sandbox.occ.stage.{transaction,merge,policy,_edit,direct,gated}` | 4 | occ | Flatten `stage/` (S5) |
| `sandbox.occ.changeset.{types,prepared}` | 4 | occ | Merge to single `changeset.py` (S5) |
| `sandbox.occ.content.{hashing,gitignore_oracle}` | 4 | occ | Flatten (S5) |
| `sandbox.plugin.runtime.{context,registry}` | 4 | plugin | Flatten `runtime/` (S6) |
| `sandbox.daemon.handler.X` (e.g. `request_context`) from `daemon/rpc/dispatcher.py` | 4 | daemon | Either Option B (promote shared `request_context.py` → `daemon/_toolbox.py`) or treat intra-subsystem depth-4 as acceptable |

### Concrete fix plan (in priority order)

1. **S4 — provider/daytona/client/ collapse.** Fixes the worst (depth-5) violation. Net: 8 files → 1 file, depth 5 → 3 (or 5 → 4 if `daytona/` retained as package).
2. **S5 — occ flatten.** Net: 22 files → 14 files at depth ≤3.
3. **S6 — plugin/runtime flatten.** Net: depth 4 → 3.
4. **Daemon intra-subsystem depth-4** — decide policy: either Option B (promote `request_context.py`, `occ_backend.py`, `result_projection.py`, `workspace_server.py` up to `daemon/`) or accept that same-tree imports under one subpackage are tolerable. The user's rule is conservative; recommend Option B if strict.

---

## 5. Naming — Cross-Cutting Rename Map

Highest-value renames first.

| Current | Proposed | Reason |
|---|---|---|
| `host/context_preparer.py` | **delete** | Empty abstraction; one factory call inlines. |
| `host/daemon_client.py::_DaemonDispatchError`, `_DaemonReadinessError` | `DaemonError` (one class with `.phase`) | Underscored "private" exceptions that callers must catch. |
| `host/bootstrap.py::setup_after_create`, `setup_after_start` | `bootstrap_sandbox` (one fn) | Two identical wrappers exist to thread a Literal used only in a debug log. |
| `api/_impl/_audit.py`, `_classifiers.py`, `_payload.py`, `_results.py` | `audit.py`, `conflict_codes.py`, `decode.py`, `results_builder.py` | Parent `_impl/` is already private; file-level `_` stutters. The generic nouns also lie about content (`_classifiers` only classifies conflicts; `_payload` is a decoder). |
| `api/_control.py` | `_lifecycle.py` (or fold into `api/__init__.py`) | "Control" is a generic noun; the file is lifecycle + discovery. |
| `daemon/handler/request_context.py` | Split into `_classify.py`, `_args.py`, `_no_follow_fs.py`, `_project.py` | No per-request state; module is a verb-handler toolbox. |
| `daemon/service/result_projection.py` | `wire_payload.py` or `occ_to_wire.py` | "Projection" collides with DB/ML term; module is an OCC-result-to-RPC serializer. |
| `daemon/async_bridge.py` | Move to `sandbox/io_loop.py` | Generic name; also used outside `daemon/` (OCC commit paths). |
| `daemon/service/layer_stack_client.py` | `service/layer_stack_adapter.py` or fold into `occ_backend.py` | "Client" implies a remote server; this is in-process. |
| `daemon/scripts/thin_client.py` | `scripts/send_envelope.py` | "Thin" is meaningless; the script is one connect+send+recv. |
| `execution/entrypoints.py` | `namespace_child.py` (or `private_namespace_child.py`) | 340-LOC child-process helper for namespace strategy; "entrypoints" plural is wrong. |
| `execution/policy.py` | `env_policy.py` (or fold into `contract.py`) | "Policy" of what? Env + path-char rules. |
| `execution/overlay_change.py` | `path_change.py` | Inside `execution/`, `overlay_` collides with 7 sibling `overlay_*` files. |
| `execution/overlay_mounts.py` | (delete; merge into orchestrator) | Misleading — does not call `mount(8)`, it copies trees. |
| `execution/workspace_environment.py` | `subprocess_runner.py` | Misnomer — it's the subprocess wrapper with cwd/env helpers. |
| `execution/workspace_capture.py` | (delete; inline at 1 call site) | 33 LOC, 1 caller, 1 branch. |
| `occ/router.py::Router` | `occ/preparer.py::ChangesetPreparer` | Class doesn't route. `OccService` stores it as `self._orchestrator` — name disagrees with its own usage. |
| `occ/ports.py` | `occ/protocols.py` | Hexagonal-architecture jargon; module uses `typing.Protocol`. |
| `occ/client.py::OccClient` | `occ/facade.py::BoundOccService` (or rename class only) | "Client of what?" — it's a workspace-bound facade. |
| `occ/overlay.py` | `occ/overlay_adapter.py` | Collides with 8 `execution/overlay_*.py` files. |
| `occ/content/gitignore_oracle.py::PathspecGitignoreOracle` | `occ/gitignore.py::PathspecGitignoreMatcher` | "Oracle" unjustified; suffix should match protocol. |
| `occ/changeset/prepared.py` | merge into `changeset.py` | "Prepared" is a phase, not content. |
| `provider/daytona/adapter.py` | (keep file, rename concept) | "Adapter to what?" — keep file; rename mental model. Class `DaytonaProviderAdapter` is fine. |
| `provider/daytona/context.py` | `daytona/exec_context.py` | "Context" overloaded; file attaches sandbox+repo-root to tool-exec dict. |
| `provider/daytona/bash.py` | `daytona/exec_wrapper.py` | Named after a shell; actual job: wrap command + parse exit marker. |
| `provider/daytona/bootstrap.py` | fold into `daytona/__init__.py` | 20 LOC; collides with `host/bootstrap.py`. |
| `provider/daytona/errors.py::AsyncDaytonaUnavailableError` | (delete) | `Daytona*Unavailable*` is sync/async-agnostic semantically. |
| `layer_stack/manager.py::LayerStackManager` | `stack.py::LayerStack` | "Manager" is noise. Stack *is* the object. |
| `layer_stack/view.py::MergedView` | `merged_view.py::MergedView` | Match file name to class. |
| `layer_stack/transaction.py` | merge into `manager.py` (or `stack.py`) | Collides with `occ/stage/transaction.py`; 100% coupled to manager. |
| `layer_stack/layer_change.py`, `layer_index.py`, `layer_publisher.py` | `changes.py`, (delete; inline into view), `publisher.py` | `layer_` prefix redundant inside `layer_stack/`. |
| `layer_stack/_paths.py`, `_storage_lock.py` | `paths.py`, `storage_lock.py` | Imported by 7 siblings; `_` lies. |
| `layer_stack/maintenance.py` | `squash.py` | Only exports `SquashService` etc. "Maintenance" is broader than the file. |
| `plugin/handler.py` | `plugin/daemon_handler.py` (or move into `sandbox/daemon/handler/plugin.py`) | Disambiguate from host-side `session.py`. |
| `plugin/session.py` | `plugin/host_call.py` | No "session" object; collides with `tools/sandbox/_lib/session.py`. |
| `plugin/projection.py::WorkspaceProjection` | `plugin/workspace_view.py::PluginWorkspaceView` | "Projection" overloaded. |
| `plugin/runtime/` | flatten to `plugin/op_context.py` + `plugin/op_registry.py` | 2 files; depth violation. |

---

## 6. LOC Reduction Summary

| Subsystem | Files | Current LOC | Achievable | Δ | % |
|---|---|---|---|---|---|
| `api/` + `audit/` + top-level | 24 | 1,704 | ~1,460 | –244 | –14% |
| `host/` | 6 | 1,242 | ~732 | –510 | –41% |
| `daemon/` | 24 | ~2,500 | ~2,415 | –85 | –3% |
| `execution/` | 19 | 2,453 | ~1,467 | –986 | –40% |
| `occ/` | 22 | ~2,500 | ~2,350 | –150 + 4 files | –6% |
| `provider/` | 15 | 1,406 | ~990 | –416 | –30% |
| `layer_stack/` + `plugin/` | 22 | 3,751 | ~3,160 | –590 + 5 files | –16% |
| **TOTAL** | **132** | **~15,556** | **~12,574** | **–2,982** | **–~19%** |

If you also accept the aggressive C2 collapse + provider/daytona promotion + occ flatten, total drops closer to **–25%** with **–12 files**.

---

## 7. Recommended Order of Operations

Grouped so each step is a single coherent PR with low cross-subsystem fallout.

**Phase 1 — Bugs (security/correctness, ship first):**
1. C1 — Plugin install signature/allowlist gate.
2. C3 — Daytona timeout enforcement via `ThreadPoolExecutor.future.result(timeout=...)`.
3. C4 — Remove or `atexit`-register `_BUNDLE_UPLOAD_EXECUTOR`.
4. C5 — Inject shared `LayerStackManager` into `WorkspaceProjection`.
5. H1+H2 — `commit_queue` lock + done-index tracking.
6. H3 — `async_bridge` thread-start outside the lock.
7. H4 — Out-of-workspace write atomicity.
8. H5 — `plugin/install.py` lockdir → `flock(2)`.
9. H8/H9 — `overlay_capture` streaming walk + subprocess process-group kill.

**Phase 2 — Depth fixes (mechanical, depth-rule compliance):**
10. S4 — Collapse `provider/daytona/client/`. Largest single import-depth win.
11. S5 — Flatten `occ/{stage,content,changeset}/`.
12. S6 — Flatten `plugin/runtime/`.

**Phase 3 — Pipeline consolidation (largest LOC reduction):**
13. C2/S1 — Merge the two execution pipelines.
14. S2/S3 — Collapse `execution/overlay_*` and `workspace_*` files.

**Phase 4 — Naming (mechanical, no behavior change):**
15. The rename map in section 5, batched per subsystem.

**Phase 5 — Local cleanups (any time):**
16. S7–S10 plus the smaller wins from section 3.

---

## 8. Per-Subsystem Deep-Dive Reports

Each contains exhaustive file:line findings, full reduction tables, and complete naming maps:

- `/tmp/sandbox_review/api_audit_top.md` — 1,704 LOC across `api/`, `audit/`, top-level
- `/tmp/sandbox_review/host.md` — 1,242 LOC across `host/`
- `/tmp/sandbox_review/daemon.md` — ~2,500 LOC across `daemon/` and subdirs
- `/tmp/sandbox_review/execution.md` — 2,453 LOC across `execution/`
- `/tmp/sandbox_review/occ.md` — ~2,500 LOC across `occ/` and subdirs
- `/tmp/sandbox_review/provider.md` — 1,406 LOC across `provider/`
- `/tmp/sandbox_review/layer_stack_plugin.md` — 3,751 LOC across `layer_stack/` and `plugin/`
