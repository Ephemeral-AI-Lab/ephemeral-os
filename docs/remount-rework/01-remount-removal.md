# Doc 1 — Mechanical Removal of Remount

> **Historical implementation specification (operation-layout exempt,
> 2026-07-11):** Source paths below describe the tree in which the removal
> landed and are not current ownership guidance.

Status: archived after implementation. Goal: a **deep, mechanical removal** of all remount
code so the tree builds and tests green with **zero remount references left**.
This is a pure subtraction — no behavior is re-expressed, nothing is renamed.
The mechanism is preserved separately in Doc 2 so it can be rebuilt later.

## 0. What "remount" means here (and what stays)

There are **two** remount layers in the tree; **both are removed**:

1. **Live-remount coordinator** (`operation/workspace_remount/**`) — quiesce a
   live command's process group, swap the workspace's overlay layers, resume.
   It is reachable only from tests today (no production caller).
2. **Low-level overlay remount mechanism** (`namespace-process setns::remount_*`,
   `workspace lifecycle/remount/**`, the daemon `--remount-overlay` runner mode).
   The staged MS_MOVE switch + verification.

**Explicitly NOT removed (load-bearing for the live mount path):**

- `engine.run_mount(...)` in `namespace-execution` — generic over a `mode_flag`;
  the **mount** path (`--mount-overlay`) uses it. Keep the method; only the
  remount call sites go.
- `setns::setns_overlay_mount` / `run_setns` / `join_namespaces` / `setns_fd` /
  `setns_user_mnt` / `namespace_fd_order_with_types` in `namespace-process`.
- `NsRunnerOperation::MountOverlay` arm in the daemon runner.
## 1. Removal classes

Each item below is either **[DELETE]** (whole file/dir removed) or **[EDIT]**
(remount excised from a file that also carries non-remount code). Work
**top-down** (callers before mechanisms) so the tree never references a
just-deleted symbol mid-edit.

### 1.1 `operation` — the coordinator (all [DELETE])

```
[DELETE] crates/sandbox-runtime/operation/src/workspace_remount/            (whole subtree, 486 LOC)
           error.rs, mod.rs, service.rs,
           service/core.rs, service/workspace_session.rs,
           service/impls/{mod.rs, remount_workspace_session.rs},
           service/command/{mod.rs, coordinator.rs, quiesce.rs}
```

Symbols that die with it: `WorkspaceRemountService`, `WorkspaceRemountOutcome`,
`WorkspaceRemountError`, `RemountWorkspaceSession`, `CommandRemountCoordinator`,
`CommandRemountInspection`, `CommandRemountQuiesce`, `RemountCancellationToken`,
`RemountSwitchState`, `RemountBlockReason`, `ProcessGroupController` /
`ProcProcessGroupController` (re-exported here from the command crate).

### 1.2 `operation` — `workspace_session` state machine ([EDIT] + [DELETE])

The session state machine has remount woven into its core; excise it.

```
[DELETE] operation/src/workspace_session/service/impls/begin_remount.rs
[DELETE] operation/src/workspace_session/service/impls/block_remount.rs
[DELETE] operation/src/workspace_session/service/impls/apply_and_finish_remount.rs
[EDIT]   operation/src/workspace_session/service/impls/mod.rs
            - drop `mod begin_remount; mod block_remount; mod apply_and_finish_remount;`
[EDIT]   operation/src/workspace_session/service/model.rs
            - DELETE enum `WorkspaceRemountState { Active, RemountPending, RemountBlocked }` + its impls
            - DELETE field `WorkspaceSession.remount_state`
            - DELETE methods `begin_remount`, `finish_remount`, `block_remount`,
              `ensure_remount_not_pending`, `refresh_from_handle`
              (refresh_from_handle's ONLY caller is apply_and_finish_remount — confirmed)
            - SIMPLIFY `active_handle()` → `Ok(self.handle.clone())` (drop the remount guard)
            - `from_handle()` → drop the `remount_state: Active` initializer
            - KEEP: `refresh_after_capture`, `refresh_after_publish` (capture/publish, NOT remount)
[EDIT]   operation/src/workspace_session/error.rs
            - DELETE variants `RemountAlreadyPending`, `RemountBlocked`, `RemountNotPending`,
              `RemountWorkspaceSessionIdMismatch`
[EDIT]   operation/src/workspace_session/service/core.rs       (drop is_remount_pending / is_remount_blocked / begin_remount / block_remount / apply_and_finish_remount delegates)
[EDIT]   operation/src/workspace_session/service/snapshot.rs   (drop any remount_state projection)
```

### 1.3 `operation` — command service ([EDIT])

```
[EDIT] operation/src/command/service/core.rs
         - DELETE field `remount_controller: Arc<dyn ProcessGroupController>`
         - DELETE `remount_controller()` accessor
         - DELETE `remount_controller` param from `from_parts` (+ its two call sites in this file)
         - DELETE methods `workspace_remount_pending`, `workspace_remount_blocked`
         - DELETE import `use crate::workspace_remount::{ProcProcessGroupController, ProcessGroupController};`
[EDIT] operation/src/command/service/helpers.rs        (drop the remount-pending guard on the exec path)
[EDIT] operation/src/command/service/test_support.rs   (drop remount_controller wiring)
[EDIT] operation/src/command/service/impls/exec_command.rs, write_command_stdin.rs
         - drop the remount-pending/blocked precondition checks
[EDIT] operation/src/command/error.rs                  (drop remount-pending/blocked error variants)
```

> The `CommandExecution::pgid()` / `workspace_root()` getters become dead once the
> coordinator is gone. Pruning them is **deferred to Doc 3** (command-crate
> teardown), so this doc leaves `command/src/command_execution.rs` alone except
> for `process_group`.

### 1.4 `command` crate — process-group mechanism ([DELETE])

```
[DELETE] crates/sandbox-runtime/command/src/process_group.rs   (431 LOC — coordinator was its only consumer)
[EDIT]   crates/sandbox-runtime/command/src/lib.rs             (drop `pub mod process_group;`)
[EDIT]   crates/sandbox-runtime/command/Cargo.toml             (drop `nix` features now unused: process/signal — verify no other use)
```

### 1.5 `operation` — wiring + observability + lib ([EDIT])

```
[EDIT] operation/src/lib.rs                  (drop `pub mod workspace_remount;` + `pub use workspace_remount::WorkspaceRemountService;`)
[EDIT] operation/src/observability.rs        (drop SpanKey/const for remount, if any remount-only)
```

### 1.6 `workspace` crate — lifecycle/remount mechanism ([DELETE] + [EDIT])

```
[DELETE] crates/sandbox-runtime/workspace/src/lifecycle/remount/   (whole subtree, 191 LOC)
            transaction.rs, mod.rs, state.rs, result.rs
            => kills WorkspaceRemountState{Active,Pending}, RemountProbe, RemountOverlayResult,
               remount_with_layers, apply_remount, block_remount, set_remount_state
[DELETE] crates/sandbox-runtime/workspace/src/service/impls/remount_workspace.rs
[EDIT]   workspace/src/service/impls/mod.rs        (drop `mod remount_workspace;`)
[EDIT]   workspace/src/service/hooks.rs            (drop `remount_workspace` hook field + its type)
[EDIT]   workspace/src/lifecycle/mod.rs            (drop `pub mod remount;`)
[EDIT]   workspace/src/lib.rs                      (drop re-exports RemountOverlayResult / RemountProbe / WorkspaceRemountState)
[EDIT]   workspace/src/model.rs                    (drop remount-related fields/refs)
[EDIT]   workspace/src/profile/handle.rs           (DELETE field `remount_state: WorkspaceRemountState` + its import)
[EDIT]   workspace/src/profile/mod.rs              (drop RemountOverlayResult/RemountProbe/WorkspaceRemountState re-exports)
[EDIT]   workspace/src/profile/manager.rs          (drop remount_with_layers/remount plumbing)
[EDIT]   workspace/src/lifecycle/create.rs         (drop `remount_state` init on the handle)
[EDIT]   workspace/src/lifecycle/persistence.rs    (DELETE the `"remount_state": handle.remount_state.as_str()` persisted field)
[EDIT]   workspace/src/namespace/setns_runner.rs   (DELETE `remount_overlay`, `remount_overlay_via_engine`; KEEP `mount_overlay`)
```

### 1.7 `namespace-process` — setns remount ([EDIT], keep mount)

`setns.rs` carries both mount and remount. Remove the remount half only.

```
[EDIT] crates/sandbox-runtime/namespace-process/src/runner/setns.rs
         DELETE: remount_overlay (+ non-linux stub), remount_overlay_inner,
                 RemountMaskGuard, RemountSwitchState, RemountStagingDirs,
                 staged_remount_overlay, mount_overlay_for_verified_remount,
                 rollback_staged_switch, remount_verification_report,
                 overlay_mount_verified, WorkspaceMountInfo, workspace_mountinfo,
                 mountinfo_lowerdir_count_matched, mountinfo_lowerdir_verified,
                 overlay_option, decode_mountinfo_field,
                 RemountReadProbe, read_probe_at_root, validated_relative_probe_path,
                 unique_suffix (used only by remount staging)
         KEEP:   run_setns, setns_overlay_mount, run_setns_inner,
                 setns_overlay_mount_inner, setns_user_mnt, join_namespaces,
                 setns_fd, namespace_fd_order_with_types
[EDIT] crates/sandbox-runtime/namespace-process/src/runner/mod.rs   (drop remount_overlay re-export, if present)
```

### 1.8 `daemon` — runner mode + observability ([EDIT])

```
[EDIT] crates/sandbox-daemon/src/runner.rs
         - DELETE `NsRunnerOperation::RemountOverlay` variant (enum + match arm calling setns::remount_overlay)
         - DELETE the `"--remount-overlay"` arg parsing + usage-string mention
         - KEEP MountOverlay end-to-end
[EDIT] crates/sandbox-daemon/src/observability/service.rs
         - DELETE `remount_state` projection (lines ~381, ~582)
```

### 1.9 `sandbox-observability` — persisted column ([EDIT], schema change)

```
[EDIT] crates/sandbox-observability/src/records.rs        (DELETE field `remount_state: Option<String>` + its serialization at ~198)
[EDIT] crates/sandbox-observability/src/store/rows.rs     (DELETE `remount_state: Option<String>`)
[EDIT] crates/sandbox-observability/src/store/schema.rs   (DELETE `remount_state TEXT,` column)
[EDIT] crates/sandbox-observability/src/store.rs          (DELETE remount_state binds/reads, ~3 refs)
[EDIT] crates/sandbox-observability/src/store/read.rs     (DELETE remount_state reads)
```
> ⚠ This drops a SQLite column from `CREATE TABLE`. Acceptable for a fresh DB.
> If any deployed DB must survive, that's a migration concern — flag to the
> owner; this spec assumes schema is rebuilt.

### 1.10 Tests ([DELETE] whole files; [EDIT] mixed files)

```
[DELETE] operation/tests/workspace_remount.rs          (153 remount hits)
[DELETE] operation/tests/command_remount.rs            (68 hits)
[DELETE] workspace/tests/unit/setns_runner.rs          (25 hits — remount path)
[DELETE] workspace/tests/unit/remount_plan.rs          (6 hits)
[DELETE] namespace-process/tests/unit/runner/setns.rs  (verify: keep mount cases if any; else delete)
[DELETE] namespace-execution/tests/engine.rs remount cases  (EDIT: keep mount run_mount cases)
[EDIT]   operation/tests/workspace_session.rs          (drop remount state-machine cases; 77 hits)
[EDIT]   operation/tests/service_graph.rs              (drop WorkspaceRemountService node; 15 hits)
[EDIT]   operation/tests/observability_snapshot.rs     (drop remount_state assertions; 13 hits)
[EDIT]   operation/tests/support/mod.rs                (drop remount fixtures; 22 hits)
[EDIT]   operation/tests/support/fake_launcher.rs, exec_command.rs, layerstack_publish.rs
[EDIT]   layerstack/tests/stack.rs                     (drop _for_remount cases)
[EDIT]   workspace/tests/unit/{model.rs, service.rs, unit.rs}
[EDIT]   daemon/tests/unit/{runner.rs, observability.rs}   (drop RemountOverlay + remount_state cases)
[EDIT]   namespace-process/tests/unit.rs, observability tests/support/mod.rs
```

## 2. Sequencing

1. **Tests first** (1.11) — delete/strip remount tests so the suite doesn't pin
   symbols you're about to remove.
2. **Coordinator** (1.1) and its `operation` wiring (1.2, 1.3, 1.5).
3. **command process_group** (1.4).
4. **workspace lifecycle/remount** (1.6) and **setns remount** (1.7).
5. **daemon runner + observability** (1.8, 1.9).
6. **layerstack** (1.10).
7. Build, clippy, fmt; iterate on the absence greps below.

## 3. Done = these greps return nothing (production code)

```sh
# No remount identifiers anywhere in src/
grep -rin "remount" --include="*.rs" crates/*/src crates/*/*/src
# No process-group inspection left
grep -rin "ProcessGroupController\|ProcessGroupInspection\|inspect_command_process_group\|process_group" --include="*.rs" crates/*/*/src
# No remount runner mode
grep -rn "RemountOverlay\|--remount-overlay\|remount_overlay" --include="*.rs" crates
# Observability column gone
grep -rn "remount_state" --include="*.rs" crates
```

All must be empty (except, by design, `--mount-overlay`/`MountOverlay`/
`setns_overlay_mount`/`run_mount`, which contain no "remount" substring).

## 4. Acceptance

- `cargo build` clean.
- `cargo test` whole workspace green (mount/create/destroy/publish/command
  exec all unaffected).
- `cargo clippy --all-targets` clean; `cargo fmt` applied.
- The README component table row for `sandbox-runtime-workspace` no longer lists
  "remount"; `process-group primitives` removed from the command row.

## 5. Net deletion (genuine, not relocation)

| Area | LOC |
|---|---|
| `operation/workspace_remount/**` | 486 |
| `command/src/process_group.rs` | 431 |
| `workspace/lifecycle/remount/**` | 191 |
| `workspace_session` remount impls + setns remount + daemon/observability edits | ~250 |
| remount tests (`workspace_remount`, `command_remount`, setns/plan + strips) | ~1,500 |
| **Total** | **≈ 2,850 LOC removed** |

This removal is a prerequisite for Doc 3: once `process_group.rs` is gone, the
`command` crate is reduced to thin command-domain glue and can be dissolved.
