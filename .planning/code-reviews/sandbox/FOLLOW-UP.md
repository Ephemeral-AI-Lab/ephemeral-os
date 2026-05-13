---
title: Sandbox Code Review — Deferred Follow-up
generated: 2026-05-13
scope: backend/src/sandbox/
status: open
parent_review: REVIEW.md
landed_in:
  - eb02f72e — parallel session baseline
  - d27a2d08 — BLOCKER themes 1/3/6 + Theme 4 in-workspace edit
  - 49c62ab0 — Theme 4 OCC contract
  - bc75a749 — api WARNING bundle
  - ca4a6fdc — command_exec WARNING bundle
  - 14842cdc — host WARNING bundle
  - 8a6b6f65 — layer_stack WARNING bundle
  - 95a8935a — overlay WARNING bundle
  - 1b241978 — plugin WR-01
  - 1512829c — provider WARNING bundle
  - b9161d9e — runtime WARNING bundle
  - f8271e91 — INFO bundle (3 of 23)
total_findings:
  resolved: ~59
  deferred: ~49
  blocker: 0  # all 21 BLOCKERs resolved
  warning: ~29
  info: ~20
---

# Sandbox Code Review — Deferred Follow-up

This document tracks the findings from the per-subsystem code reviews under
`.planning/code-reviews/sandbox/` that were **deferred from the original
fix campaign** because each requires non-trivial structural changes beyond
the surgical scope of the in-session work. All 21 BLOCKERs are landed; the
items below are WARNINGs and INFOs that need their own planning passes.

Each entry references the source file with line numbers, a one-line root
cause, and the recommended fix shape. Pull the full discussion from the
per-subsystem `*-REVIEW.md` files in this directory when working on a
specific item.

---

## Deferred WARNINGs

### command_exec

#### command_exec/WR-02 — `shutil.rmtree` of mount upperdir/workdir without scope guard
**File:** `backend/src/sandbox/command_exec/workspace/mount.py:78-81`
**Issue:** `_run_copy_backed_mount` calls `shutil.rmtree(directory)` on each of `upperdir`, `workdir`, `merged` after only an `if directory.exists()` check. `WorkspaceReplacementMountSpec` accepts arbitrary absolute paths in `__post_init__`; a misconfigured spec wipes whatever the path points at.
**Fix shape:** Thread a `scratch_root: Path` into `WorkspaceReplacementMountSpec` and assert `directory.resolve().is_relative_to(scratch_root)` before each rmtree. Reject specs whose lowerdir/upperdir/workdir are not under the scratch root in `__post_init__`.

#### command_exec/WR-03 — `_rewrite_declared_workspace_refs` regex is argv-blind
**File:** `backend/src/sandbox/command_exec/workspace/mount.py:171-188`
**Issue:** `pattern.sub(...)` rewrites every `/testbed`-token in every argv element, including string literals (`-c "x = '/testbed/...'"`), env values are NOT rewritten, and a command that reads `WORKSPACE_DIR` from env still sees the declared path.
**Fix shape:** Either (a) drop the regex rewrite and rely on caller-side absolute-path resolution before the command leaves the agent, or (b) parse argv to detect quoted segments and skip rewriting inside them. Add a parallel env-value rewrite pass for `WORKSPACE_DIR`, `PWD`, etc.

#### command_exec/WR-04 — TOCTOU between `_validate_mount_inputs` and `_mount_overlay`
**File:** `backend/src/sandbox/command_exec/workspace/namespace_entrypoint.py:40-54`
**Issue:** `_validate_mount_inputs` calls `is_dir()` on host-visible paths; between that check and the `mount(8)` invocation, the host can swap a directory for a symlink to a sensitive location.
**Fix shape:** Open file descriptors for each input in `_validate_mount_inputs`, pass FDs (or use `/proc/self/fd/N` references) to the mount invocation, OR switch from `mount` CLI to direct `mount(2)` syscall under the unshared namespace.

#### command_exec/WR-06 — Raw `payload[...]` accesses outside try/except in `execute`
**File:** `backend/src/sandbox/command_exec/workspace/namespace_entrypoint.py:27-37`
**Issue:** `payload["workspace_root"]`, `payload["lowerdir"]`, etc. raise `KeyError` before reaching the broad try/except (lines 39-64), so the structured 126 exit code never fires for malformed payloads.
**Fix shape:** Wrap the payload unpacking in a `try/except KeyError` that writes the 126 exit code through the structured path.

#### command_exec/WR-07 — Generic `except Exception` in `execute` conflates failure classes
**File:** `backend/src/sandbox/command_exec/workspace/namespace_entrypoint.py:56-64`
**Issue:** Validation errors, `mkdir` failures, `subprocess.CalledProcessError` from mount, and unexpected `Exception` all funnel through one opaque `f"workspace replacement mount failed: {exc}"` message with exit 126. Caller cannot distinguish "bad input" from "kernel refused mount" from "code bug".
**Fix shape:** Split the try block into stages (validate / setup / mount / run) with per-stage exception handlers that emit structured error codes and detail keys (`error_kind`, `detail`).

### host

#### host/WR-01 — Non-idempotent daemon retry can double-execute side-effectful ops
**File:** `backend/src/sandbox/host/daemon_client.py:139-174` (heuristic at `:177-193`)
**Issue:** `_exec_daemon_call` retries on substring-match against stderr/stdout for `connection refused`/`no such file or directory`. Side-effectful ops (`api.shell`, `api.write_file`, `api.edit_file`) can be re-executed if the daemon crashed mid-response or the thin client emitted a misleading error string.
**Fix shape:** Make the thin client emit distinctive exit codes (e.g. 97 = connect refused before send; 98 = send/recv failed). Key the retry on exit code only. Add an idempotency allowlist that gates which `op` values are retry-eligible.

#### host/WR-03 — Concurrent runtime-bundle uploads on the same sandbox can corrupt the tarball
**File:** `backend/src/sandbox/host/runtime_bundle.py:289-322`
**Issue:** Upload sequence (`: > tarball; >> tarball; tar -xzf tarball`) is racy across host processes. The hash-marker check is racy too. Per project memory, parallel codex sessions are real here.
**Fix shape:** Stage to a per-upload `<tarball>.<uuid>.staging` path; final step is `mv staging → final`. Or `flock` around the upload region. Atomic rename gives crash-safety + concurrency-safety simultaneously.

#### host/WR-05 — Untyped `Any` exec-result handling silently degrades on attribute miss
**File:** `backend/src/sandbox/host/daemon_client.py:100-114, 184-186, 401-406`; `backend/src/sandbox/host/runtime_bundle.py:279-280, 297, 310, 322`
**Issue:** `getattr(result, "exit_code", 0)` masks an unknown-shape result as success; other sites use `1` (treats unknown as failure). Inconsistent and silent.
**Fix shape:** Centralize one helper `def _exit_code(result) -> int:` that raises `_DaemonDispatchError` on missing `exit_code`. Replace every defaulted `getattr` call site.

#### host/WR-06 — `_check_daemon_readiness_after_spawn` loses original-request context
**File:** `backend/src/sandbox/host/daemon_client.py:196-241`
**Issue:** On readiness failure post-respawn, error details carry the readiness probe's error but not the original `op` (parsed at line 253 but only included in the `MissingLayerStackRoot` branch).
**Fix shape:** Include `original_op` in `details` for every `_DaemonReadinessError` raised in this function.

#### host/WR-07 — Fresh `ThreadPoolExecutor` per call leaks fds at high concurrency
**File:** `backend/src/sandbox/host/setup.py:106-141`
**Issue:** New `ThreadPoolExecutor(max_workers=1)` created per `start_runtime_bundle_upload`; `shutdown(wait=False)` returns immediately while the worker thread continues. At high sandbox-creation frequency, fds and thread objects leak until GC.
**Fix shape:** Use `asyncio.create_task` with a caller-held task reference (preferred — `run_sync` already owns a loop). Alternative: module-level bounded `ThreadPoolExecutor`. At minimum, attach `future.add_done_callback(lambda f: f.exception())` so background exceptions aren't silently dropped by GC.

### layer_stack

#### layer_stack/WR-02 — `publisher._prepare_changes` reads `change.source_path` with no scoping
**File:** `backend/src/sandbox/layer_stack/layer/publisher.py:217-223`
**Issue:** `Path(change.source_path).read_bytes()` accepts any path. A `WriteLayerChange("dst", source_path="/etc/shadow")` would copy that into the layer. Today caller chains bound the value, but the publisher has no enforcement.
**Fix shape:** Thread `staging_root: Path` into `LayerPublisher.__init__`; assert `source_path.resolve(strict=True)` is under `staging_root` for every `WriteLayerChange`.

#### layer_stack/WR-03 — `release_lease` performs `rmtree` while holding `LayerStackManager._lock`
**File:** `backend/src/sandbox/layer_stack/manager.py:132-142`
**Issue:** `_remove_unreferenced_layers` runs synchronously under the manager lock. A slow rmtree blocks all manifest reads, lease acquires, and publishes — head-of-line blocking that magnifies OCC retry storms.
**Fix shape:** Snapshot the layers-to-remove under lock; release the lock; run rmtree outside. The `pinned_layers` check still protects against eviction-vs-acquire races at the next lock entry.

#### layer_stack/WR-06 — Publisher CAS is only safe within a single process
**File:** `backend/src/sandbox/layer_stack/layer/publisher.py:124-144`
**Issue:** Manifest re-read + compare-and-swap window is unprotected against a second host process pointing at the same `storage_root`. The manager `RLock` only serializes within a process.
**Fix shape:** Either document `storage_root` as single-writer and add a `flock` advisory lock at `LayerStackManager.__init__`, OR implement CAS via `os.replace` on a sentinel file that encodes the expected version. Option (a) is cheaper.

#### layer_stack/WR-07 — `MergedView._layer_index_cache` reads can race with eviction
**File:** `backend/src/sandbox/layer_stack/view/merged.py:34-49`
**Issue:** A reader can hold a `LayerIndex` for a layer that just got evicted and rmtreed. The subsequent `candidate.read_bytes()` raises raw `FileNotFoundError` instead of the typed `LayerStackStorageError` already defined.
**Fix shape:** Wrap the per-layer reads in `try/except (FileNotFoundError, OSError)` and re-raise as `LayerStackStorageError("layer no longer present", layer_id=...)` so callers can distinguish "stale snapshot" from "I/O failed".

### occ

#### occ/WR-02 — `_cas_exhaustion_result` re-stamps DROP/REJECT paths as ABORTED_VERSION
**File:** `backend/src/sandbox/occ/merge/serial.py:185-206`
**Issue:** When CAS-retry budget is exhausted, every `path_group` is rewritten as `ABORTED_VERSION`, masking pre-existing DROP-routed (`.git`) and REJECT-routed (bad-path) groups. Latent in single-process topology but breaks the moment Phase 06+ multi-process wiring lands.
**Fix shape:** Iterate `prepared.path_groups`; preserve `DROP`/`REJECT` routes as `FileStatus.DROPPED`/`REJECTED`; only stamp the OCC-route groups as `ABORTED_VERSION`.

#### occ/WR-03 — Orchestrator attaches one base hash to every chained Write/Delete in a group
**File:** (see occ-REVIEW.md WR-03 for the orchestrator call site)
**Issue:** When a group contains chained Write/Delete changes against the same path, the orchestrator attaches the snapshot's base hash to every change. The second change's base hash should be the hash AFTER the first change, not the snapshot. Result: second change always conflicts with itself.
**Fix shape:** In the orchestrator, walk the group sequentially and update the running base hash after each accepted change before stamping it on the next.

#### occ/WR-05 — `_combine_prepared` discards `prepared.timings` and stickies atomic flag
**File:** `backend/src/sandbox/occ/merge/serial.py:170-178`
**Issue:** Timings collected during per-prepared preparation are dropped when prepared changesets get combined into a batch. `atomic = any(prepared.atomic for prepared in ...)` promotes a non-atomic prepared changeset to atomic if any sibling in the batch was atomic.
**Fix shape:** Merge timings dicts (concatenate-then-aggregate by key); track atomic per-prepared and apply atomic semantics only to the prepared changesets that requested them — never bleed across the batch boundary.

#### occ/WR-06 — `committed_paths` empty-list edge case returns `()` instead of the fallback path
**File:** `backend/src/sandbox/occ/result_projection.py` (committed_paths function)
**Issue:** When every file has `.path == ""`, the function returns the empty tuple instead of falling back to a request-level path. Inconsistent with the documented tri-state fallback.
**Fix shape:** If `not any(getattr(f, "path", "") for f in result.files)`, return `(fallback_path,)` if provided, else `()`.

### overlay

#### overlay/WR-01 — Duplicate `opaque_dir` emission when both marker file and xattr present
**File:** `backend/src/sandbox/overlay/capture/upperdir.py` (around `_emit_changes`)
**Issue:** A directory with both `.wh..wh..opq` and `trusted.overlay.opaque` xattr emits two `opaque_dir` change records for the same path. Downstream layer-publisher dedup (post layer_stack BL-05 fix) absorbs the duplicate, but the capture itself should emit once.
**Fix shape:** Track a `seen: set[str]` of paths in the walker; only emit per path once.

#### overlay/WR-05 — `_populate_upperdir_from_diff` drops empty-dir creations and mode changes
**File:** `backend/src/sandbox/overlay/capture/upperdir.py:52-101`
**Issue:** `_payload_paths` only counts entries that satisfy `is_symlink() or is_file()`. Empty dirs created by user commands (`mkdir build`, `git init` on empty repo) are lost. `_entries_match` compares only bytes and readlink target — not mode bits, so `chmod +x file.sh` is silently dropped.
**Fix shape:** Add a parallel `_emitted_dirs: set[str]` pass that emits `OverlayPathChange` records for dirs present in merged but not lower. Extend `_entries_match` to compare `st_mode & 0o777` when comparing files.

#### overlay/WR-06 — `_resolve_cwd` mkdir during input validation
**File:** `backend/src/sandbox/overlay/namespace/command.py:74-83`
**Issue:** `_resolve_cwd` validates the cwd is inside the workspace then calls `resolved.mkdir(parents=True, exist_ok=True)`. Validation and side-effect mixed.
**Fix shape:** Split into `_validate_cwd` (pure check, raises) and `_ensure_cwd` (mkdir). Callers run validate before any side effects.

#### overlay/WR-07 — `os.symlink(os.readlink(merged_entry), target)` preserves absolute/escape targets
**File:** `backend/src/sandbox/overlay/capture/upperdir.py:79`
**Issue:** Symlinks copied from `merged` to upperdir keep their literal target — absolute and `..`-escaping targets land verbatim. Layer_stack BL-05 fixed the same class for workspace base; overlay capture has the same gap.
**Fix shape:** Reuse the `_relative_target_escapes` helper from `backend/src/sandbox/layer_stack/workspace/base.py` and reject (or convert to a captured "rejected symlink" change) such symlinks during overlay capture.

#### overlay/WR-08 — `namespace/` directory name implies operations that don't exist
**File:** `backend/src/sandbox/overlay/namespace/{command,mounts}.py`
**Issue:** The directory name suggests Linux namespace + overlay-mount operations, but the actual implementation is a `shutil.copytree`-backed merged view. A future security review of this path will read the names and miss the real seam.
**Fix shape:** Rename `namespace/` → `merged_view/` (or similar) OR add a top-of-file comment in each module clarifying that the kernel-mount path lives elsewhere and pointing at it.

### plugin (all WR-02..09 deferred — WR-01 landed in 1b241978)

#### plugin/WR-02 — `_wrap_response` does not bound sandbox-controlled payload size
**File:** `backend/src/sandbox/plugin/session.py:159-188`
**Issue:** Sandbox-controlled JSON response from plugin op is `json.dumps`-ed without a size cap. A runaway/malicious plugin can OOM the host. `default=str` also silently coerces non-JSON values to repr strings.
**Fix shape:** Define `_MAX_RESPONSE_BYTES = 8 * 1024 * 1024`. Reject oversize payloads with a structured `decode` error before serialization. Drop `default=str` and raise on non-JSON values.

#### plugin/WR-03 — `_installed_marker_cache` never invalidates
**File:** `backend/src/sandbox/plugin/install.py:72-110`
**Issue:** In-memory cache keyed by `(sandbox_id, plugin_name)` lives forever. If a sandbox is destroyed/recreated/snapshot-restored, the host still believes the plugin is installed and skips upload. Subsequent op-calls fail with `ModuleNotFoundError`.
**Fix shape:** Drop the in-memory cache (the on-disk marker is the source of truth). Or expose `forget(sandbox_id)` invoked from sandbox-destroy lifecycle.

#### plugin/WR-04 — Multi-process workers race on plugin install
**File:** `backend/src/sandbox/plugin/install.py:72, 92-110`
**Issue:** `_locks` is process-local. Two uvicorn workers calling `ensure_installed` for the same sandbox both pass the marker check, both run `rm -rf install_dir && mkdir install_dir && tar -xzf`. They race; one worker can wipe mid-extract of the other.
**Fix shape:** Stage to a temp dir and `mv` atomically to `install_dir` (preferred — also cleaner partial-failure recovery). Or acquire a sandbox-side `mkdir`-based lock (`mkdir -p <install_dir>.lock`) before the destructive sequence.

#### plugin/WR-05 — `_PENDING` registrations never popped by flush
**File:** `backend/src/sandbox/plugin/runtime/registry.py:129-162, 106-116`
**Issue:** `flush_plugin_registrations` registers each entry with the dispatcher but never removes from `_PENDING`. `plugin_status` then reports registered ops as "pending" forever. **Interaction:** the current Theme 6 BL-01 fix in `handler.py` depends on `_PENDING` staying populated so a retry-after-warm-failure can re-flush. Fixing WR-05 requires a joint redesign: either pop on flush + evict `sys.modules` on warm failure so retry re-imports, OR track flushed entries and reinstate them on warm failure.
**Fix shape:** Pop on flush, **and** in `handler.py:plugin_ensure` warm-failure rollback also evict the runtime module from `sys.modules` so the next call re-imports and decorators re-fire `_PENDING`. Update `test_plugin_lifecycle_wedge.py` to re-inject the synthetic module between attempts.

#### plugin/WR-06 — Unbounded host-side caches per `(sandbox_id, plugin)`
**File:** `backend/src/sandbox/plugin/session.py:48-49`, `install.py:72-73`
**Issue:** `_runtime_loaded`, `_call_locks`, `_locks`, `_installed_marker_cache` grow with every sandbox-id seen, never evict. Long-running host RSS grows linearly with total sandbox count.
**Fix shape:** Expose `forget(sandbox_id)` on every module that caches by sandbox-id; wire into the sandbox-destroy lifecycle.

#### plugin/WR-07 — `_PROJECTIONS` cache keyed by string never evicts and trusts caller-supplied path
**File:** `backend/src/sandbox/plugin/handler.py:50` (and `_plugin_op_context_factory`)
**Issue:** Layer-stack-root strings get a permanent `WorkspaceProjection` allocation. Unbounded growth + path trust gap (caller can choose the cache key).
**Fix shape:** Bound the cache (LRU 256 entries), validate `layer_stack_root` against the active workspace binding before insert.

#### plugin/WR-08 — `register_plugin_op` namespace check is bypassable via wrapper
**File:** `backend/src/sandbox/plugin/runtime/registry.py:78-83`
**Issue:** The check compares the caller frame's `__name__` to `plugins.catalog.<plugin>.`. If the decorator is invoked via a thin wrapper (e.g. `register = register_plugin_op("foo", "op")`; then exported and called from another module), the namespace check uses the wrapper's frame, not the intended caller.
**Fix shape:** Walk the call stack until a frame outside the registry module is found; assert all subsequent frames (up to a depth limit) match the plugin namespace.

#### plugin/WR-09 — `flush_plugin_registrations` doesn't validate plugin_name matches caller frame
**File:** `backend/src/sandbox/plugin/runtime/registry.py:129-162`
**Issue:** The function trusts the caller-supplied `plugin_name`. Combined with WR-08, an external caller could flush registrations under any namespace.
**Fix shape:** Use the same caller-frame validation as `register_plugin_op` to assert the caller is allowed to flush this plugin's registrations.

### provider

#### provider/WR-02 — `get_build_logs_url` reaches into SDK private attribute via string concat
**File:** `backend/src/sandbox/provider/daytona/adapter.py:280`
**Issue:** `getattr(raw, "_sandbox" + "_api", None)` is a lint-dodge that hides the SDK-private access from grep/refactor tooling. An SDK rename silently returns `None` and the function permanently breaks.
**Fix shape:** Drop the string-concat dodge; add `# noqa: SLF001` with a comment pinning the SDK version. Add a smoke test that fails loudly when the attribute disappears on SDK upgrade. Better long-term: request a public method upstream.

#### provider/WR-04 — `close_client` shutdown thread abandoned after 1s timeout
**File:** `backend/src/sandbox/provider/daytona/client/shutdown.py:28-42`
**Issue:** Daemon thread runs the close awaitable; `join(timeout=1.0)` returns even though the close keeps running on a loop owning SDK transports. Caller thinks shutdown is done. Long-running orchestrators leak sockets until process exit.
**Fix shape:** Either remove the timeout (block until close completes — fine at process shutdown) or extend to 5s + log warning when the join times out so the leak is observable.

#### provider/WR-05 — `DaytonaContextPreparer._get_sandbox` never invalidates cached sandbox
**File:** `backend/src/sandbox/provider/daytona/context.py:27-37`
**Issue:** Sync sandbox cached on `self._sandbox` for the lifetime of the preparer. Re-created sandboxes under the same id surface stale data. Async path correctly invalidates per-loop-id; sync path was never updated.
**Fix shape:** Either always re-fetch in `prepare_context` (the call is cheap relative to the rest of context preparation), or expose `invalidate()` and wire into sandbox-lifecycle transitions.

### runtime

#### runtime/WR-02 — TOCTOU between classify and write on out-of-workspace paths
**File:** `backend/src/sandbox/runtime/daemon/handler/request_context.py:89` + `tools/write.py:181`, `tools/edit.py:175`
**Issue:** `classify_path` resolves symlinks once via `os.path.realpath`. The handler then calls `Path(abs_path).write_text(...)`, which re-opens and follows symlinks at I/O time. A race can redirect writes into unintended targets.
**Fix shape:** Open the file via `os.open(... O_NOFOLLOW | O_WRONLY ...)` at write time, OR hold a directory fd from classify and use `openat`-style operations.

#### runtime/WR-03 — `_drop_transient_lowerdir` blindly rmtrees parent of lease path
**File:** `backend/src/sandbox/runtime/daemon/service/shell_runner.py:293-298`
**Issue:** `shutil.rmtree(lowerdir.parent, ignore_errors=True)`. If the lease contract ever drifts (different storage layout, misconfigured test fixture), this can rmtree well outside the intended scratch root. `ignore_errors=True` masks the damage.
**Fix shape:** Verify `lowerdir.parent.is_relative_to(EXPECTED_SCRATCH_ROOT)` before unlinking. Drop `ignore_errors`; log failures.

#### runtime/WR-05 — `fence_stale_staging` rmtrees concurrent daemons' staging on restart races
**File:** `backend/src/sandbox/runtime/daemon/service/workspace_server.py:47-66` (uses `_DAEMON_STARTED_AT` at `:26`)
**Issue:** Deletes any staging dir whose `mtime < _DAEMON_STARTED_AT`. If two daemons run concurrently against the same layer-stack root (restart race, supervised double-start), the newer daemon rmtrees the older's in-flight staging.
**Fix shape:** Add PID-lockfile or `flock` on `pid_path` in `__main__.py` before serving. Or compare `_DAEMON_STARTED_AT` against the PID owner of the staging dir.

#### runtime/WR-06 — `WorkspaceBinding` re-validation duplicated between two callers
**File:** `backend/src/sandbox/runtime/daemon/service/workspace_server.py:122-138, 157-169`
**Issue:** Manifest-existence + version-positive checks duplicated verbatim between `ensure_workspace_base` and `_require_bound_active_workspace`. Drift between the two yields inconsistent error messages and invariants.
**Fix shape:** Extract `_validate_manifest_for_root(layer_stack_root: Path) -> None`; both call sites delegate.

#### runtime/WR-07 — `error_holder` branch in `_ensure_standalone_loop` is dead in practice
**File:** `backend/src/sandbox/runtime/async_bridge.py:147, 179-182`
**Issue:** `ready.set()` runs before `run_forever()`. The `except BaseException` block only populates `error_holder` after `run_forever()` exits — by then the main thread has already returned. The `if error_holder:` check at line 179 is unreachable.
**Fix shape:** Either drop the dead branch entirely, or move `ready.set()` to after a successful loop init so late failures can surface.

---

## Deferred INFOs

### api

#### api/IN-03 — `_overlay_cwd` re-converts an already-string `cwd`
**File:** `backend/src/sandbox/api/tool/shell.py:90-93`
**Issue:** `cwd` is typed `str | None`, but `_overlay_cwd` wraps `str(cwd).strip()` defensively.
**Fix shape:** Drop the `str(...)` if the type contract holds; or document why defensive wrapping is needed.

#### api/IN-04 — Each `SandboxClient` method does a lazy module-level import
**File:** `backend/src/sandbox/api/facade.py`
**Issue:** Lazy imports inside every method method-call cost a few microseconds per call.
**Fix shape:** Move imports to module level; document why if circular imports forced the lazy pattern.

#### api/IN-05 — `api/__init__.py` instantiates a module-level singleton without thread/safety annotation
**File:** `backend/src/sandbox/api/__init__.py`
**Issue:** `_client` is a process-global; safe today because it's stateless, but undocumented.
**Fix shape:** Add a class-level docstring on `SandboxClient` noting the statelessness invariant.

### command_exec

#### command_exec/IN-01 — `_ensure_refs` writes unbounded captured-process buffer to ref files
**File:** `backend/src/sandbox/command_exec/workspace/mount.py:203-213`
**Issue:** `subprocess.run(..., capture_output=True)` on `unshare` buffers child stdout/stderr in memory unboundedly; only after `completed = subprocess.run(...)` returns does `_ensure_refs` write them to disk.
**Fix shape:** Pass `stdout`/`stderr` file objects directly to `subprocess.run` so the buffers stream to disk.

#### command_exec/IN-02 — `cwd` normalization should live at the request boundary
**File:** `backend/src/sandbox/command_exec/contract/request.py`
**Issue:** `cwd` normalization (`os.path.normpath`, `..` rejection) happens in the workspace layer. A single trusted invariant at the request boundary would simplify downstream code.
**Fix shape:** Move the normalization into `CommandExecRequest.__post_init__`.

### host

#### host/IN-01 — Thin-client launcher lacks Python version check
**File:** `backend/src/sandbox/host/daemon_client.py:37-45` vs. `:340-352`
**Issue:** The thin client iterates `python3.13 ... python3` and `exec`s the first found. The spawn launcher additionally checks `sys.version_info >= (3, 10)`. Today the thin client only uses widely-compatible features so works, but adding 3.10+ syntax later would fail silently.
**Fix shape:** Mirror the spawn launcher's `python -c 'sys.version_info >= (3, 10)'` probe.

#### host/IN-02 — `_DAEMON_THIN_CLIENT_PY` lacks explicit recv-loop termination on timeout
**File:** `backend/src/sandbox/host/daemon_client.py:25-35`
**Issue:** Per-recv `settimeout`. Mid-response timeout loses partial JSON; the Python exception text becomes the only output, which combined with WR-01 could trip the substring retry heuristic.
**Fix shape:** Catch `socket.timeout` explicitly, write a distinctive stderr marker, exit with a reserved code. Pair with WR-01.

#### host/IN-03 — `_FORWARDED_DAEMON_ENV = ()` is an empty extension point
**File:** `backend/src/sandbox/host/daemon_client.py:23, 358-375`
**Issue:** Always empty; the "if env changed, restart daemon" logic is dead.
**Fix shape:** Either document as an extension point with examples, or delete the wiring entirely.

### occ

#### occ/IN-01 — `gitignore_oracle._is_dir_excluded` recursion has redundant ancestor walk
**File:** `backend/src/sandbox/occ/content/gitignore_oracle.py`
**Issue:** O(depth²) ancestor walk; correctness OK but redundant.
**Fix shape:** Memoize ancestor results during a single walk.

#### occ/IN-02 — `_kept_children_for` does not normalize paths
**File:** `backend/src/sandbox/occ/content/gitignore_oracle.py`
**Issue:** Coupled to overlay-capture invariants; if those ever change, this breaks silently.
**Fix shape:** Call `normalize_layer_path` on both sides and add a unit test against off-by-slash inputs.

#### occ/IN-03 — `OccService._auto_squash_after_publish_coalesced_sync` is hard to reason about
**File:** `backend/src/sandbox/occ/service.py`
**Issue:** Control flow is overcomplicated — review proposes a simpler equivalent.
**Fix shape:** Refactor to the proposed simpler structure (see review for the suggested form).

### overlay

#### overlay/IN-01 — `lowerdir_for` is a fragile back-reference
**File:** `backend/src/sandbox/overlay/runner/snapshot_overlay_runner.py` (around `lowerdir_for`)
**Issue:** Reverse-lookup that breaks if upstream naming conventions change.
**Fix shape:** Take the lowerdir as an explicit parameter; remove the reverse-lookup.

#### overlay/IN-02 — `del snapshot_manifest` parameter never used
**File:** `backend/src/sandbox/overlay/runner/snapshot_overlay_runner.py`
**Issue:** Function parameter is immediately deleted; dead code suggests an aborted refactor.
**Fix shape:** Remove the parameter from the signature; update callers.

#### overlay/IN-03 — `invoke_start` set inside the `try` — unbound if first line raises
**File:** `backend/src/sandbox/overlay/runner/snapshot_overlay_runner.py`
**Issue:** Latent NameError on the rare path where the first line of `try` raises.
**Fix shape:** Initialize `invoke_start = time.perf_counter()` before the try.

#### overlay/IN-04 — `runtime_invoker.py` resume-wait timing math can underflow
**File:** `backend/src/sandbox/overlay/runner/runtime_invoker.py`
**Issue:** Math underflow before the `max(0.0, ...)` guard catches it. Harmless but indicates the guard is reactive, not preventative.
**Fix shape:** Reorder so the subtraction operates on guaranteed-positive operands; the `max` becomes a redundant safety net.

### plugin

#### plugin/IN-02 — Weak validation of caller-supplied audit fields
**File:** `backend/src/sandbox/plugin/handler.py:_plugin_op_context_factory`
**Issue:** Audit fields (`run_id`, `task_id`) accepted via `str(args.get(...))` with no length/format check.
**Fix shape:** Cap field length (256 chars), reject NUL bytes.

#### plugin/IN-03 — `importlib.invalidate_caches()` may not be sufficient for clean reload
**File:** `backend/src/sandbox/plugin/handler.py:_unload_plugin_runtime`
**Issue:** Cached `pyc` files and pkg-resources metadata can persist across reload.
**Fix shape:** Document the trust boundary; add a `_test_force_reload_plugin` helper for tests that need a fully fresh state.

#### plugin/IN-04 — `_caller_module_name` 8-frame walk is a magic number
**File:** `backend/src/sandbox/plugin/runtime/registry.py:_caller_module_name`
**Issue:** Hard-coded walk depth.
**Fix shape:** Walk until a non-registry-module frame is found, capped at a small constant with a comment explaining the bound.

### provider

#### provider/IN-01 — `_PROJECT_ROOT` uses brittle six-level relative path
**File:** `backend/src/sandbox/provider/daytona/client/credentials.py:10`
**Issue:** `_PROJECT_ROOT = parents[6]`. Any file move silently breaks .env loading; falls back to env-vars-only without warning.
**Fix shape:** Compute project root via marker-file lookup (search upward for `pyproject.toml` or `.git/`).

---

## How to consume this document

1. Pick a finding by `subsystem/ID`.
2. Read the corresponding `subsystem-REVIEW.md` for the full context.
3. Land the fix as a focused commit; remove the entry from this file (or check it off if you prefer a tracked log).
4. When making structural changes (anything in WR-03/WR-04/WR-07/WR-08 category), surface the design implication first — these touch invariants that other code relies on.

## Notes on the cross-cutting items

- **plugin/WR-05** is deliberately deferred until joint design with the Theme 6 BL-01 fix in `handler.py`. The current "leave _PENDING populated on warm failure" approach works but requires WR-05 to land alongside a `sys.modules` eviction step and a test update. See the WR-05 entry above.
- **layer_stack/WR-02 + WR-03 + WR-06** form a coherent multi-process-safety story; consider tackling them together as a single "make storage_root genuinely concurrent-safe" pass.
- **runtime/WR-02** (TOCTOU on writes) interacts with **command_exec/WR-04** (TOCTOU on mount inputs). Both are best solved by switching from path-string APIs to FD-based APIs at the trust boundary.
