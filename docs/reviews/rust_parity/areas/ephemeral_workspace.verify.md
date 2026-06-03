# Independent Verification — Ephemeral workspace lifecycle (sandbox)

Area: per-tool-call ephemeral overlay; upperdir -> OCC merge; discard on lease release.
Verifier stance: trust nothing; re-derived from source. Python (`/tmp/oldpy/...`) is ground
truth; Rust under `sandbox/crates/...` carries `// PORT` anchors.

## Invariant verdict table (independent evidence)

| # | Invariant | Status | Decisive bilateral evidence |
|---|-----------|--------|------------------------------|
| 1 | Fresh ephemeral overlay PER tool call | confirmed_match | py `pipeline.py:138-142` `overlay_lifecycle.acquire(... invocation_id=f"overlay:{agent_id}:{invocation_id}")` per call; `lifecycle.py:49` `_allocate_run_dir` per invocation. rs `command.rs:862-868` builds a per-call `run_root = .../sandbox-overlay/{pid}-{invocation_id}` + `allocate_overlay_writable_dirs`; `command.rs:738` `acquire_snapshot("command_session:{agent}:{invocation}")`; `command.rs:887` `mode: RunMode::FreshNs`. grep/glob mirror this (`dispatcher.rs:1183` per-call `acquire_snapshot`, `1195` allocate dirs). |
| 2 | Writes land in the overlay upperdir | confirmed_match | py `pipeline.py:146` `run_in_namespace(handle, req)` with `handle.upperdir = writable_dirs.upperdir` (`lifecycle.py:82`). rs `command.rs:901-902` `upperdir: Some(dirs.upperdir)`, `workdir: Some(dirs.workdir)`; **mechanism confirmed in runner**: `eos-runner/src/fresh_ns.rs:73-85` extracts `request.upperdir` (errors "fresh-ns requires upperdir" if absent) and passes it to `mount.mount_overlay(MountInputs{ upperdir, lowerdirs=layer_paths, ... })`; `mount.rs:39-40` documents the `fsconfig("upperdir")`/`fsconfig("lowerdir+")` overlayfs sequence. |
| 3 | On success, upperdir changes sent to OCC for MERGE into shared workspace | confirmed_match | py `pipeline.py:147-163` (gated on `Intent.WRITE_ALLOWED`) -> `capture_changes` -> `_commit_and_attach` -> `workspace_publish.py:198-221` `_apply_workspace_capture` -> `occ_client.apply_changeset(...)`. rs `command.rs:1306-1328` `finalize_command_workspace`: `capture_upperdir(&workspace.upperdir)` -> `base_hashes_for_snapshot` -> `apply_occ_changeset(...)` -> new manifest read at `command.rs:1330`. |
| 4 | Overlay/lease released and overlay DISCARDED after the call | confirmed_match (success path); see D2 for the prepare-error edge | py `pipeline.py:201-202` `finally: lease_guard.release(handle, release_overlay)`; `lifecycle.py:110-137` `release_overlay` rmtree's `run_dir`. rs success path `command.rs:1079-1081` `remove_dir_all(run_dir)` then `release_lease`. grep/glob/plugin use RAII `RunDirCleanup` (`dispatcher.rs:1197`, `1421-1430`). |
| 5 | File read/write/edit use direct LayerStack/OCC fast path when bound; shell/search/plugin use overlay pipeline (OCC-gated) | confirmed_match (with unbound-fallback nuance, see new finding NF1) | py `dispatch.py:40,239-299` direct fast path for `{read,write,edit}_file`; `dispatch.py:220-257` overlay pipeline for shell/search/fallback. rs direct ops `dispatcher.rs:501-711` `op_read_file/op_write_file/op_edit_file` (no overlay; `apply_occ_changeset`); overlay search `dispatcher.rs:1148-1264` `run_overlay_read_tool` (FreshNs ns-runner); plugin overlay `dispatcher.rs:884-1026`. Binding is established up-front by `api.ensure_workspace_base` (`daemon_client.rs:127,699`; `workspace_base.rs:69-88`), so the bound case the invariant names is the operative path. |

No invariant was found broken. The primary worry (FALSE MATCH) did not materialize for any of the
five invariants; the one place a silent break could hide (writes landing in upperdir, inv. 2) was
chased into the ns-runner and confirmed.

## Constants / operators extracted (both sides)

| Concept | Python | Rust |
|---------|--------|------|
| OCC atomic for overlay-capture publish | `pipeline.py:218` `atomic=len(distinct_paths) > 1` (single-path => `False`) | `dispatcher.rs:1749` `apply_occ_changeset` hardcodes `true` |
| OCC atomic for direct write/edit | `dispatch.py:349,374` `occ_service.apply_changeset(...)` with default `CommitOptions(atomic=True)` (`changeset.py:216`) | `dispatcher.rs:609,689` -> `apply_occ_changeset` => `true` (MATCHES Python default) |
| Success statuses (include Dropped) | `changeset.py:158-163` `{ACCEPTED, COMMITTED, DROPPED}` | `route.rs:73-75` `Accepted | Committed | Dropped` (identical) |
| Published statuses | `changeset.py:154-155` `{ACCEPTED, COMMITTED}` | `route.rs:67-69` `Accepted | Committed` (identical) |
| Race-loss status (gated path, content changed) | not silent — surfaced as `aborted_version` conflict | `dispatcher.rs:2289-2293` `OccStatus::AbortedVersion`; CAS exhaustion `commit_queue.rs:447-451` also `AbortedVersion` |
| Post-publish auto-squash depth | n/a constant (uses `squash(max_depth=...)`) | `eos-layerstack/src/lib.rs:65` `AUTO_SQUASH_MAX_DEPTH = 100` |
| Shell PRE-mount squash depth | `pipeline.py:458` default `64` (`EOS_SHELL_MOUNT_SQUASH_MAX_DEPTH`) | ABSENT |

## Disparity adjudication

### D1 (investigator: high/bug) — "overlay-capture atomic hardcoded true; single-path shell write that loses an OCC race is silent success" -> REFUTED (corrected severity: low)

The investigator conflated `OccStatus::Dropped` (a success status) with a race loss. Traced the full
chain:

1. A single Gated path that loses a race returns `AbortedVersion` from `validate_gated_group`
   (`dispatcher.rs:2289-2293`); CAS-budget exhaustion likewise yields `AbortedVersion`
   (`commit_queue.rs:447-451`). `Dropped` is produced ONLY for `Route::Drop` (`.git`) paths
   (`validate_prepared` `dispatcher.rs:2205-2212`).
2. `atomic_validation_drop_result` (`dispatcher.rs:1533-1563`) rewrites to `Dropped` ONLY paths where
   `file.status.is_published()` is true (the would-be-committed *siblings* in a multi-path atomic
   batch where some OTHER path failed). A non-published `AbortedVersion` falls into the `else` branch
   (line 1550-1552) and is kept verbatim.
3. `is_validation_failure(AbortedVersion) == true` (`dispatcher.rs:2323-2330`); `first_conflict`
   finds it (`dispatcher.rs:2789-2791`); `ChangesetResult::success()` is false. So the race-loser is
   surfaced as a conflict in `guarded_changeset_response` (`dispatcher.rs:2762-2782`), NOT a silent
   success.
4. Bilateral confirmation: Python single-path overlay capture uses `atomic=False`
   (`pipeline.py:218`), but `_commit_and_attach` -> `conflict_and_status`
   (`changeset_projection.py:21-38`) finds the first non-`is_success_status` file, and
   `ABORTED_VERSION` is not a success status. Both Python (`atomic=False`) and Rust (`atomic=true`)
   produce the SAME observable conflict for a single-path race loss.

The only real behavioral delta from hardcoding `atomic=true` on the overlay-capture path: a single-path
overlay capture is excluded from disjoint co-batching in the commit queue (`commit_queue.rs:370`
`if item.prepared.atomic || !used.is_disjoint(...)` pushes atomic items to a separate batch). That is a
throughput nuance, not a correctness defect; per-path outcomes are identical. The investigator's only
"high" is downgraded.

### D2 (investigator: medium/bug) — "command-session prepare error path leaks the overlay run_dir (no RAII cleanup)" -> CONFIRMED (corrected severity: low)

Real and bilaterally grounded. Python `overlay_lifecycle.acquire` has an explicit error boundary:
`lifecycle.py:100-103` `except Exception: _release_lease_silently(...); shutil.rmtree(run_dir,
ignore_errors=True); raise` — docstring (lines 45-47) guarantees "no lease or scratch directory leaks
past the error boundary." Rust `prepare_command_session` allocates `run_dir` at `command.rs:867`
WITHOUT a `RunDirCleanup` guard; the error branch `command.rs:785-788` releases the lease only and
leaks `dirs.run_dir` if anything after allocation fails (`create_dir_all` `command.rs:870`,
`write_run_request` `command.rs:907`, `spawn_command_runner_session` `command.rs:908`). Contrast the
overlay search/plugin paths which DO use `let _cleanup = RunDirCleanup(dirs.run_dir.clone())`
immediately after allocating (`dispatcher.rs:1197`, `command.rs:900`).

Severity adjusted medium -> low: the lease IS released (no lease leak; the worse failure), and the leak
is an essentially-empty scratch dir only on a rare prepare-failure path. Still a genuine fidelity gap
vs. Python's stated invariant. Fix is one line: `let _cleanup = RunDirCleanup(dirs.run_dir.clone());`
right after `command.rs:868`.

### D3 (investigator: low/missing) — "shell pre-mount squash (EOS_SHELL_MOUNT_SQUASH_MAX_DEPTH default 64) absent in Rust" -> CONFIRMED (low)

`grep -rin "SHELL_MOUNT_SQUASH|shell_pre_mount|pre_mount"` across `sandbox/crates/` returns nothing.
Python has BOTH a pre-mount squash before the shell enters the kernel mount path
(`pipeline.py:137,243-274`, default depth 64 at `pipeline.py:458`) AND post-publish maintenance. Rust
has ONLY post-publish `run_auto_squash_maintenance` with `AUTO_SQUASH_MAX_DEPTH = 100`
(`dispatcher.rs:1600-1633`, `eos-layerstack/src/lib.rs:65`). This is a perf/maintenance bound that
keeps overlayfs lowerdir depth low before mounting; its absence is a performance gap, not a correctness
break (each FreshNs call still mounts all `lease.layer_paths` as lowerdirs and works regardless of
depth). Severity low confirmed.

## New findings (not in investigator report)

- **NF1 (low/benign) — unbound-workspace routing divergence for read/write/edit.** Python
  `dispatch.py:233-257`: for `{read,write,edit}_file`, when the workspace is UNBOUND
  (`_bound_file_request` -> `WorkspaceBindingError` -> `None`, `dispatch.py:412-421`), dispatch FALLS
  BACK to the overlay pipeline (`_dispatch_via_workspace_pipeline`, `dispatch.py:257`). Rust
  `op_read_file/op_write_file/op_edit_file` unconditionally call `require_workspace_binding` /
  `bound_layer_path` (`dispatcher.rs:509,574,641`) and PROPAGATE a `WorkspaceBindingError`
  (mapped to wire `"WorkspaceBindingError"` at `dispatcher.rs:3437-3438`) — no overlay fallback.
  Benign because the daemon establishes a binding up-front via `api.ensure_workspace_base`
  (`daemon_client.rs:127,699`; `ensure_workspace_base` builds the base "if the stack is unbound",
  `workspace_base.rs:69-88`), so the unbound branch is not exercised in the normal ephemeral lifecycle,
  and the invariant explicitly scopes the fast path to "when a workspace binding exists." Worth a note,
  not a fix.

- **NF2 (informational) — atomic-flag asymmetry is split, not uniform.** For the DIRECT file
  write/edit path, Rust `atomic=true` MATCHES Python's `CommitOptions` default `atomic=True`
  (`changeset.py:216`). The asymmetry vs. Python is confined to the OVERLAY-CAPTURE path
  (single-path => Python `False` vs Rust `true`), and as shown in D1 it has no correctness effect. This
  corrects the investigator's framing that the hardcoded `true` is a blanket divergence.

## Overall verdict

Rust fidelity for the ephemeral-workspace lifecycle is HIGH. All five invariants are independently
confirmed as matches, including the mechanism-level checks (upperdir is the live overlayfs upper via
`eos-runner/fresh_ns.rs`; capture->OCC merge via `finalize_command_workspace`; per-call freshness and
post-call discard). The investigator's headline "high" (D1) is REFUTED — the alleged silent-success on
a lost OCC race does not occur; race losses surface as `aborted_version` conflicts on both sides, and
the only delta from the hardcoded `atomic=true` is a benign commit-queue batching nuance. D2 (run_dir
leak on the command-session prepare-error path) is CONFIRMED but downgraded to low (lease still
released; leak is an empty scratch dir on a rare error path). D3 (missing shell pre-mount squash) is
CONFIRMED low (perf gap only). One new low/benign routing divergence (NF1) and one clarifying note
(NF2) added.
