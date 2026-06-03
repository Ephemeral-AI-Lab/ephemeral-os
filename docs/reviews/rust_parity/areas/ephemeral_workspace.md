# Rust parity review — Ephemeral workspace lifecycle (sandbox)

Area: per-tool-call ephemeral overlay → upperdir capture → OCC merge → discard on lease release.

Source precedence: Python (`/tmp/oldpy/backend/src/sandbox/...`) = ground truth; `docs/architecture/sandbox/*` = corroboration; checklist = what to confirm.

---

## Ground truth

The Python per-call lifecycle is `EphemeralPipeline.run_tool_call` (`/tmp/oldpy/backend/src/sandbox/ephemeral_workspace/pipeline.py:130-202`):

1. Optional shell pre-mount squash for `exec_command` (`pipeline.py:136-137`, `_run_shell_pre_mount_maintenance` `pipeline.py:243-274`, depth from `EOS_SHELL_MOUNT_SQUASH_MAX_DEPTH`, **default 64** `pipeline.py:455-462`).
2. `overlay_lifecycle.acquire(...)` leases a snapshot and allocates a private `run_dir`/upperdir/workdir, with rmtree-on-error (`/tmp/oldpy/backend/src/sandbox/overlay/lifecycle.py:27-108`).
3. `run_in_namespace(handle, req)` runs the tool inside a fresh mount namespace whose lowerdirs are the leased layer paths and whose upperdir is the private dir (`pipeline.py:146`).
4. Capture + publish **only when `req.intent == Intent.WRITE_ALLOWED`** (`pipeline.py:147-187`): `capture_changes` walks the upperdir, then `_commit_and_attach` → `_apply_workspace_capture` → `occ_client.apply_changeset(..., options=CommitOptions(atomic=len(distinct_paths) > 1), ...)` (`/tmp/oldpy/backend/src/sandbox/ephemeral_workspace/workspace_publish.py:35-78, 198-221`). Note: publish is gated on **write intent, not command exit code** — a failed shell command still publishes whatever it wrote.
5. `finally: self._lease_guard.release(handle, overlay_lifecycle.release_overlay)` releases the lease and rmtrees the run_dir (`pipeline.py:201-202`, `lifecycle.py:111-141`).

Dispatch routing (`/tmp/oldpy/backend/src/sandbox/daemon/workspace_tool/dispatch.py`):
- `read_file`/`write_file`/`edit_file` first try the **direct LayerStack/OCC fast path** when a workspace binding exists (`dispatch.py:239-299`, `_LAYER_STACK_FILE_VERBS = {"edit_file","read_file","write_file"}` `dispatch.py:40`). Direct write/edit call `services.occ_service.apply_changeset([...])` with `options=None` → `CommitOptions` default **`atomic=True`** (`/tmp/oldpy/backend/src/sandbox/occ/changeset.py:206-216`).
- shell/search (`exec_command`/`grep`/`glob`) and the fallback for unbound file ops go through `pipeline.run_tool_call` (overlay capture, OCC-gated publish) (`dispatch.py:220-257`).

The persistent-mount machinery (`EphemeralPipeline.start`, `_watch_foreign_publishes`, `WorkspaceChangeEventBus`, `publish_pending_changes`) is **dormant on the tool-call path**: dispatch calls `get_ephemeral_pipeline(..., start=False)` (`dispatch.py:226-230`); `start()` only runs when `start=True` (`pipeline_registry.py:75-77`), used by the plugin runtime API, not by `dispatch_workspace_tool_call`. Confirms arch doc `docs/architecture/sandbox/workspaces.html` §5.3 ("start=False ... without starting a persistent workspace mount").

Constants (ground truth):
- `AUTO_SQUASH_MAX_DEPTH = 100` (`/tmp/oldpy/backend/src/sandbox/occ/service.py:34`) — post-publish maintenance squash.
- `EOS_SHELL_MOUNT_SQUASH_MAX_DEPTH` default **64** (`pipeline.py:455-462`) — pre-mount shell squash.
- `_MAX_PIPELINES = 256` (`pipeline_registry.py:34`); `_upperdir_sample_entry_limit` default 5000 (`pipeline.py:526-533`); foreign-watch interval default 0.25s (`pipeline.py:481-488`).
- `is_success_status` = {ACCEPTED, COMMITTED, DROPPED}; `is_published_status` = {ACCEPTED, COMMITTED} (`changeset.py:154-164`).

---

## Rust mapping

The Rust daemon (`eosd`) replaces the in-process Python daemon with a **purely per-call** ns-runner model. There is **no persistent `EphemeralPipeline`, no `start()`, no foreign watcher, no event bus** — each call opens `LayerStack`, leases a snapshot, allocates run dirs, spawns `eosd ns-runner` (FreshNs), captures, publishes, releases.

- `exec_command` (non-isolated): `op_exec_command` → `start_command_session` → `prepare_command_session` → (foreground/background) `CommandSessionFinalizer::finish` → `finalize_command_workspace`.
  - `sandbox/crates/eos-daemon/src/command.rs:52-91, 718-790, 852-924, 1020-1106, 1295-1363`.
- shell/search read (`grep`/`glob`): `run_overlay_read_tool` (`sandbox/crates/eos-daemon/src/dispatcher.rs:1188-1264`).
- plugin overlay (write): `run_plugin_overlay_command` → `run_plugin_overlay_once` → `plugin_overlay_response` (`dispatcher.rs:884-1026`).
- direct file fast paths: `op_read_file`/`op_write_file`/`op_edit_file` (`dispatcher.rs:519-870`).
- OCC apply: `apply_occ_changeset` (`dispatcher.rs:1761-1776`) → `apply_changeset_with_base_hashes` (`sandbox/crates/eos-occ/src/service.rs:181-195`); merge/validate/publish in `LayerStackCommitTransaction::revalidate_and_publish` (`dispatcher.rs:1467-1543`).
- `agent-core/crates/eos-sandbox-host/src/daemon_client.rs` is **pure transport** (serialize one JSON envelope, ship to in-sandbox `eosd`); it implements no overlay lifecycle, as expected.

---

## Invariant table

| # | Invariant | Status | Severity | Python file:line | Rust file:line | Note |
|---|-----------|--------|----------|------------------|----------------|------|
| 1 | Fresh ephemeral overlay created PER tool call (exec_command) | match | none | pipeline.py:138-142; lifecycle.py:27-60 | command.rs:859-868, 886-906 (`allocate_overlay_writable_dirs` + `acquire_snapshot` per invocation; run_dir keyed by pid+invocation_id) | Per-call FreshNs ns-runner replaces persistent mount; matches the live dispatch path (Python `start=False`). |
| 2 | Writes land in the overlay upperdir | match | none | pipeline.py:146 (`run_in_namespace`); lifecycle.py:84-86 | command.rs:901-902 (`upperdir: Some(dirs.upperdir)`, `workdir: Some(dirs.workdir)`); ns-runner mounts `layer_paths` as lowerdirs | — |
| 3 | On success, upperdir changes sent to OCC for MERGE into shared workspace | partial | high | pipeline.py:147-163; workspace_publish.py:198-221 | command.rs:1306-1329 (`capture_upperdir` → `apply_occ_changeset`) | Capture+publish happens, BUT (a) OCC `atomic` flag is hardcoded `true` instead of `len(distinct_paths) > 1` (see D1 — silent-success bug on single-path conflict), and (b) "on success" is a checklist wording mismatch: both sides publish regardless of command exit code (gated only on write intent). |
| 4 | Ephemeral overlay/lease released and overlay DISCARDED after the call | partial | medium | pipeline.py:201-202; lifecycle.py:111-141 (LeaseGuard + release_overlay rmtree) | command.rs:1079-1081 (`remove_dir_all(run_dir)` + `release_lease` in finalizer) | Success path discards. BUT `prepare_command_session` has no RAII run-dir cleanup; the prepare-error branch (command.rs:785-788) releases the lease but leaks the upperdir/workdir (see D2). |
| 5 | File APIs use LayerStack/OCC fast path when bound; shell/search/plugin use overlay pipeline publishing through OCC-gated paths | match | none | dispatch.py:40, 239-299 (fast path); dispatch.py:220-257 (overlay) | read/write/edit: dispatcher.rs:519-870 (direct `read_bytes`/`apply_occ_changeset`, no overlay); grep/glob: dispatcher.rs:1170-1264 (overlay ns-runner); plugin: dispatcher.rs:884-1026 | Routing and OCC-gating preserved. Direct write/edit use `atomic=true`, which MATCHES Python's `CommitOptions` default `atomic=True` for the fast path. |

Constant comparison:

| Constant | Python | Rust | Verdict |
|----------|--------|------|---------|
| `AUTO_SQUASH_MAX_DEPTH` (post-publish) | 100 (occ/service.py:34) | 100 (eos-layerstack/src/lib.rs:66) | match |
| Shell pre-mount squash depth | 64 (`EOS_SHELL_MOUNT_SQUASH_MAX_DEPTH`, pipeline.py:455-462) | ABSENT — no `SHELL_MOUNT_SQUASH` consumer anywhere in `sandbox/crates` | missing (see D3) |
| Overlay-capture `atomic` flag | `len(distinct_paths) > 1` (workspace_publish.py:218) | hardcoded `true` (dispatcher.rs:1771; command.rs path via `apply_occ_changeset`) | divergent (see D1) |
| `is_success_status` set | {ACCEPTED, COMMITTED, DROPPED} (changeset.py:158-164) | {Accepted, Committed, Dropped} (eos-occ/src/route.rs:73-74) | match |

---

## Disparities

### D1 — Overlay-capture OCC `atomic` flag hardcoded `true`; single-path shell write that loses an OCC race is reported as silent success (HIGH, divergent/bug)

Ground truth: overlay capture publishes with `options=CommitOptions(atomic=len(distinct_paths) > 1)` (`/tmp/oldpy/backend/src/sandbox/ephemeral_workspace/workspace_publish.py:214-221`). For a **single** captured path, `atomic=False`.

Rust: the exec_command finalizer and plugin overlay both route through `apply_occ_changeset` (`sandbox/crates/eos-daemon/src/command.rs:1323-1328`, `sandbox/crates/eos-daemon/src/dispatcher.rs:936-941`), which calls `apply_changeset_with_base_hashes(changes, snapshot_version, true, base_hashes)` (`dispatcher.rs:1768-1772`). The third positional arg is `atomic` (`eos-occ/src/service.rs:181-195`) — **hardcoded `true`** for every overlay capture regardless of path count.

Why it matters — observable, not just cosmetic:
- With `atomic=true`, a captured single path whose base-hash/version validation FAILS hits `atomic_validation_drop_result` and is rewritten to `OccStatus::Dropped` (`dispatcher.rs:1485-1496, 1555-1585`).
- `OccStatus::Dropped.is_success() == true` (`eos-occ/src/route.rs:73-74`), so `first_conflict` (`dispatcher.rs:2811-2812`) finds **no conflict** → `guarded_changeset_response` returns `success: true`, `status: "committed"`, `conflict: null` (`dispatcher.rs:2784-2804`).
- Python with `atomic=False` filters the failed single path out of `publishable_changes` and returns it under `no_publish_result` with its **natural** validation status (`ABORTED_OVERLAP` / `ABORTED_VERSION` / `REJECTED`). `conflict_and_status` then sees `not is_success_status` and surfaces a real conflict with `success=False` (`/tmp/oldpy/backend/src/sandbox/daemon/workspace_tool/changeset_projection.py:21-38`; `is_success_status` excludes those statuses, `changeset.py:158-164`).

Net effect: a shell command (`exec_command`) that writes exactly one tracked file which then loses an OCC race / fails base-hash validation is reported to the agent as **committed/success in Rust but as a conflict/failure in Python**. The agent believes its write landed when it did not — exactly the "silently miss key dynamics / introduce bug" class. (Multi-path captures and the direct file fast path are unaffected: Python is also `atomic=True` there, so they match.)

Suggested fix: derive the flag like Python — `let atomic = distinct_paths(&changes) > 1;` — and thread it into `apply_occ_changeset` for the overlay-capture callers (exec_command finalize, plugin overlay). Keep `atomic=true` for the single-file fast paths (`op_write_file`/`op_edit_file`), which match Python's default. Add a regression test: single-file shell write under a concurrently-bumped manifest must surface a conflict, not `committed`.

### D2 — Command-session prepare error path leaks the overlay run_dir (no RAII cleanup) (MEDIUM, bug/dropped-error-handling)

Ground truth: `overlay_lifecycle.acquire` rmtrees `run_dir` on **any** exception after `acquire_snapshot` succeeds, and releases the lease, so neither lease nor scratch leaks past the error boundary (`/tmp/oldpy/backend/src/sandbox/overlay/lifecycle.py:50-108`, docstring "no lease or scratch directory leaks past the error boundary").

Rust: `prepare_command_session` allocates the overlay dirs at `command.rs:867-868` with **no `RunDirCleanup` guard** (that RAII guard is only used on the dispatcher plugin/read paths — `dispatcher.rs:920, 1219`). On the prepare-error branch, `start_command_session` only releases the lease (`command.rs:785-788`) — it never `remove_dir_all(run_dir)`. So if `create_dir_all(session_dir)`, `write_run_request`, or `spawn_command_runner_session` fails after the dirs are allocated, the upperdir/workdir/run_dir leak. Additionally, the success-path cleanup is manual inside `finish()` (`command.rs:1079`) rather than RAII, so a panic in the finalizer thread before line 1079 leaks both the run_dir and the lease.

Why it matters: per-call scratch is created under `overlay_writable_root()/runtime/sandbox-overlay/`; repeated prepare failures or finalizer panics accumulate scratch (and leaked leases pin layer GC). The stale-overlay reaper exists only in Python (`pipeline_registry.py:148-178`) and runs once per daemon process — it does not cover same-process leaks.

Suggested fix: wrap the allocated dirs in a `RunDirCleanup` guard inside `prepare_command_session` (mirroring `dispatcher.rs:920`), and on the prepare-error branch also drop/clean the run_dir, not just the lease. Consider moving lease release + run-dir cleanup into a Drop guard on `CommandWorkspace` so the success and panic paths converge, matching Python's `LeaseGuard`/`release_overlay` `finally` semantics.

### D3 — Shell pre-mount squash (`EOS_SHELL_MOUNT_SQUASH_MAX_DEPTH`, default 64) is absent (LOW, missing/intentional-perf)

Ground truth: before `exec_command` enters the kernel mount path, Python collapses manifests deeper than 64 layers (`pipeline.py:136-137, 243-274, 455-462`), "to collapse deep manifests before shell enters the kernel mount path."

Rust: no `SHELL_MOUNT_SQUASH` / `shell_pre_mount` consumer exists in `sandbox/crates` (grep clean). The only squash is the **post-publish** `run_auto_squash_maintenance` at depth `AUTO_SQUASH_MAX_DEPTH = 100` (`dispatcher.rs:1622-1672`).

Why it matters: the Rust ns-runner mounts every leased `lease.layer_paths` entry as an overlay lowerdir per call (`command.rs:900`). Without the pre-mount squash, a manifest at depth 65–100 (below the auto-squash floor) is mounted with that many lowerdirs on every shell call, whereas Python would have collapsed it to ≤64 first. This is a latency/lowerdir-count regression, not a correctness bug — `AUTO_SQUASH_MAX_DEPTH=100` still bounds growth. Label: intentional simplification of the per-call model, but a dropped tuning knob worth a follow-up if deep stacks appear in benchmarks.

Suggested fix (optional): add an opt-in pre-mount squash gated by `EOS_SHELL_MOUNT_SQUASH_MAX_DEPTH` before `prepare_command_session` acquires its snapshot, or document the deliberate removal.

---

## Extra findings

- **Persistent-mount subsystem intentionally dropped, correctly.** `EphemeralPipeline.start`, `_watch_foreign_publishes`, `WorkspaceChangeEventBus`/`subscribe_workspace_changes`, `publish_pending_changes`, `ensure_current`/`_remount_active` (`pipeline.py:290-449`, `workspace_publish.py:80-170`, `events.py`) have no Rust analog. Verified these are **dormant on the tool-call dispatch path** in Python (`dispatch.py:226-230` uses `start=False`), so the Rust per-call model loses no live dispatch behavior. Informational, not a missing dynamic.
- **`mutation_source` labeling differs from Python on overlay capture.** Python sets `mutation_source` to `"api_write"` for single-path `write_file`/`edit_file` overlay captures else `"overlay_capture"` (`pipeline.py:152-156`). Rust `exec_command` finalize routes through `guarded_changeset_response` → `mutation_source(verb)` keyed on verb `"exec_command"` (`dispatcher.rs:2790`). Low impact (telemetry label), but a literal divergence.
- **Isolated exec_command correctly does NOT publish.** `finalize_isolated_command_workspace` captures `changed_paths` for audit/visibility with `"published": false`, `occ_apply_s = 0.0`, and never calls OCC (`command.rs:1196-1293`) — matches Python isolated semantics (arch doc §5.4; isolated pipeline `run_tool_call` "does not commit through OCC"). Good parity.
- **`run_dir` keying differs but is sound.** Python keys per-call run dirs by `invocation_id + uuid4()[:8]` (`lifecycle.py:179-182`); Rust keys by `pid + sanitize(invocation_id)` with no random suffix (`command.rs:862-866`, `dispatcher.rs:1060-1069`). Two concurrent calls sharing an `invocation_id` within one daemon process would collide in Rust; in practice invocation_ids are unique per call, so low risk. Worth a note.
- **`_upperdir_total_bytes` published-event metric (default sample limit 5000, `pipeline.py:496-533`) and the `overlay_workspace.published`/`mounted`/`cleaned` audit events (`pipeline.py:172-187`, `lifecycle.py:62-79, 143-200`) are not reproduced** as overlay-workspace audit events in the Rust per-call path; Rust emits resource-tree timings (`insert_tree_resource_timings`, `command.rs:1332-1336`) instead. Telemetry-shape divergence; not a lifecycle correctness issue.

---

## Open questions

1. D1: is there an integration test exercising a single-file shell write under a concurrently-advanced manifest? If not, the silent-success regression is untested on both the Rust and migration-harness sides.
2. D3: do any production manifests reach depth 65–100 between auto-squashes? If shell-heavy workloads keep stacks shallow, the missing pre-mount squash is purely theoretical; if not, it is a measurable per-call mount-cost regression.
3. Does any agent-core consumer rely on the `mutation_source == "api_write"` vs `"overlay_capture"` distinction (D-extra) for write attribution/audit? If so the verb-keyed Rust label is a behavioral change, not just telemetry.
