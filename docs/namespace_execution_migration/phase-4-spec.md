# Phase 4 Spec — Mount family onto the engine

**Status:** implementation-ready. A different agent builds strictly from this
document. Companion docs: [`docs/namespace-execution.md`](../namespace-execution.md)
(design of record), [`docs/namespace_execution_migration/migration-phases.md`](./migration-phases.md)
(phasing, binding).

**Citation convention.** Every factual claim is tagged either *grounded* with a
`path:line` reference (verified in-tree at authoring, commit `133fbf365`) or
*[ASSUMED]* (Phase 2/3 surface that does not exist yet, or a judgement call).
Assumptions are minimized and each is justified where it appears; the full list
is in §12.

---

## 1. Objective & non-goals

### 1.1 Objective

Route the two overlay-mount operations — initial **overlay mount** and live
**remount** — through the namespace-execution engine's `run_mount` seam, and
delete the second, duplicate daemon-side spawn/wait/pipe path that today lives in
`workspace/src/namespace/setns_runner.rs`. After this phase the `ns-runner`
re-exec is launched from the engine for the mount family exactly as the command
family will use it, and `workspace` depends only on the engine's narrow
`run_mount` surface.

This is the migration's **Phase 4** (`migration-phases.md:180-206`). It depends
only on Phase 2 (the engine) and is parallel to Phase 3 (command); it does **not**
depend on Phase 3 (`migration-phases.md:49-51`).

### 1.2 In scope (crates: `workspace`, `daemon` — `migration-phases.md:46`)

- `crates/sandbox-runtime/workspace/src/namespace/setns_runner.rs` — replace the
  bespoke launch path with two `engine.run_mount(...).wait()` call sites.
- `crates/sandbox-runtime/workspace/src/namespace/mod.rs` — give `NamespaceRuntime`
  a workspace-local engine (see §3.2).
- `crates/sandbox-runtime/workspace/src/profile/manager.rs` — thread
  `setup_timeout_s` into the runtime's engine construction (see §5, §3.2).
- `crates/sandbox-runtime/workspace/src/lifecycle/create.rs` &
  `.../lifecycle/remount/transaction.rs` — the two mount/remount call sites drop the
  now-redundant `setup_timeout_s` argument (the engine owns the timeout; see §5.4.1, §5/HP5).
- `crates/sandbox-runtime/workspace/src/model.rs` — `impl From<WorkspaceEntry> for
  NamespaceTarget` (the seam shared with Phase 3; see §4, §7).
- `crates/sandbox-runtime/workspace/Cargo.toml` — depend on
  `sandbox-runtime-namespace-execution`.
- `crates/sandbox-daemon/src/runner.rs` — `MountOverlay` failure → `RunResult.payload`
  (§3.3); rename the `dispatch_runner_mode` parameter.
- Tests: delete the obsolete `workspace/tests/setns_runner.rs`; add the
  workspace + daemon tests in §9.

### 1.3 Non-goals (explicitly out of scope for Phase 4)

- **The `--start-ack-fd` handshake is NOT touched.** Phase 6 removes it atomically
  from the launcher and the daemon child (`migration-phases.md:106-112, 238-242,
  262-264`). Phase 4 edits `daemon/src/runner.rs` but leaves `wait_for_start_ack`
  (`daemon/src/runner.rs:174-188`), `RunnerCliConfig.start_ack_fd`
  (`daemon/src/runner.rs:99`), and `--start-ack-fd` parsing
  (`daemon/src/runner.rs:139-146`) **exactly as they are**. *(P4-R18)*
- **The in-namespace runner and the wire protocol are unchanged.**
  `namespace-process/src/runner/{mod,setns,shell_exec,protocol}.rs` are not edited
  (design `namespace-execution.md:45-49`; `protocol.rs` is unchanged —
  `NamespaceRunnerRequest`/`RunResult`/`NsFds` keep their current shape,
  `protocol.rs:9-41`).
- **No second engine instance is shared with command.** Phase 4 introduces a
  *workspace-local* engine; consolidating command + mount onto one engine instance
  is a post-migration step (see §3.2, §12).
- **No `MountOperation` trait, no `ops.rs`, no `Backing`/`NsRunnerMode` enum.**
  Mount is two `run_mount` call sites, each a `(mode_flag, parse_closure)` pair
  (design `namespace-execution.md:88-89, 242-246, 698-704`).
- The command path, the remount coordinator (Phase 5), and `command/src/pty.rs`
  deletion (Phase 6) are untouched.

---

## 2. Consumed Phase 2 API and capability classification

Phase 4 builds on the engine. The engine crate exists today only as a **Phase-1
skeleton, with its execution machinery gated behind `#[cfg(feature =
"test-support")]`** (`crates/sandbox-runtime/namespace-execution/src/lib.rs:16-38`).
Phase 2 makes it real and promotes that machinery into the normal build.

### 2.1 The exact surface Phase 4 calls

```rust
// engine.rs (Phase-2-adds — no engine.rs exists today)
impl NamespaceExecutionEngine {
    pub fn allocate_id(&self) -> NamespaceExecutionId;
    pub fn run_mount<O: Send + 'static>(
        &self,
        mode_flag: &'static str,
        target: NamespaceTarget,
        id: NamespaceExecutionId,
        args: serde_json::Value,   // ← §5.1.1/§12-D refinement (the prompt's signature omits this);
                                   //    required to carry the remount probe. Mount passes json!({}).
        parse: impl FnOnce(RunnerOutcome) -> Result<O, NamespaceExecutionError> + Send + 'static,
    ) -> Result<ExecutionHandle<O>, NamespaceExecutionError>;
}
impl<O> ExecutionHandle<O> {
    pub fn wait(self) -> Result<O, NamespaceExecutionError>;   // consumes self; blocks on the promise
}
impl RunnerOutcome {
    pub fn payload(&self) -> &serde_json::Value;
    pub fn exit_code(&self) -> i64;
    pub fn status(&self) -> NamespaceExecutionTerminalStatus;
}
pub struct NamespaceTarget {
    pub workspace_root: PathBuf,
    pub layer_paths: Vec<PathBuf>,
    pub upperdir: Option<PathBuf>,
    pub workdir: Option<PathBuf>,
    pub ns_fds: NsFds,
}
```

### 2.2 Classification: exists-today / Phase-2-adds / Phase-4-adds

| Item | Status | Evidence / divergence |
|---|---|---|
| `NamespaceTarget` (5 fields) | **exists-today** | `target.rs:7-14`. **Divergence:** `ns_fds: NsFds` is *non-optional*, but the wire `NamespaceRunnerRequest.ns_fds` is `Option<NsFds>` (`protocol.rs:32`) and `ns_fds_from_mode` returns `Option<NsFds>` (`fds.rs:145`). Resolved in §4. |
| `NamespaceExecutionId(pub String)` | **exists-today** | `id.rs:1-4`; re-exported by `operation` (`namespace_execution.rs:8`). |
| `NamespaceExecutionError` (`Spawn`/`Finalize`/`Admission`) | **exists-today** | `error.rs:4-12`. `impl Display` (`error.rs:14-27`) ⇒ usable with `setup_error` (§4.3). |
| `RunnerOutcome::exit_code()` | **exists-today** | `shell.rs:8-12`. |
| `RunnerOutcome::payload()` | **Phase-2-adds** | absent today (`shell.rs:8-12` has only `exit_code`). Needed by the remount closure (§3.3). |
| `RunnerOutcome::status()` | **Phase-2-adds** | absent today; returns `NamespaceExecutionTerminalStatus`. Used by the engine watcher for the observer only; the mount closures never call it. |
| `NamespaceExecutionTerminalStatus` | **exists-today, wrong crate** | lives in `operation` (`operation/src/namespace_execution.rs:67-73`), not the engine crate. The engine's `status()`/observer need it; Phase 2/3 must move it **down** into the engine crate (as `NamespaceExecutionId` already moved). *This is a Phase 2/3 obligation, not Phase 4's;* Phase 4 never names this type. |
| `ExecutionHandle<O>::wait(self)` | **exists-today, gated** | `execution.rs:24-27`, but `execution` is `#[cfg(feature = "test-support")]` (`lib.rs:16-30`). Phase 2 must promote it to the normal build. |
| `NamespaceExecutionEngine`, `engine.rs`, `run_mount`, `allocate_id`, the watcher, `NsRunnerLauncher` | **Phase-2-adds** | no `engine.rs`/`launcher.rs`/`pty.rs` in the crate (`ls src/` ⇒ only `error,execution,id,lib,observer,promise,registry,shell,target`). |
| `ExecutionRegistry::try_reserve`, live/completed maps | **Phase-2-adds** | `registry.rs:4-16` is a `max_active` placeholder only. |
| `ExecutionObserver::on_running` | **exists-today** | `observer.rs:5-7`. |
| `ExecutionObserver::on_terminal(id, status, exit_code)` | **Phase-2-adds** | absent today (`observer.rs` has only `on_running`); design shows both (`namespace-execution.md:493-497`). |
| `run_mount` exit-code short-circuit (exit≠0 ⇒ terminal error *before* the closure) | **Phase-2-adds / refinement** | required by the failure contract (§3.3). The design says `run_mount` is "identical except `spawn_piped` and the parse closure" (`namespace-execution.md:313`); this spec **refines** that — see §3.3 and §12-A. |
| Engine constructor taking `(observer, max_active, setup_timeout_s)` + a no-op observer | **Phase-2-adds [ASSUMED]** | construction surface is unspecified by the design; §3.2/§5 pin the assumption. |

**Net:** the only Phase-4-specific *consumption* risks are `RunnerOutcome::payload()`,
the `run_mount` exit-code short-circuit, the promoted (un-gated) `ExecutionHandle`,
and the engine constructor — all Phase-2 obligations, all flagged.

---

## 3. The three subtle decisions

### 3.1 Decision A — Observability & the dependency-cycle hazard

**Question (Hard problem 1).** The engine drives `on_running`/`on_terminal` by id,
but `begin` — which carries the `WorkspaceSessionId` + `operation_name` — lives in
the operation layer (`operation/src/namespace_execution.rs:148-186`,
`snapshot_active_namespace_executions` at `:264-284`). The mount path is in
`workspace`, *below* operation in the dependency graph
(`namespace-execution.md:514-524`). So `workspace` cannot call the operation-layer
ledger's `begin` without inverting the graph. Do mount executions appear in
`active_namespace_executions`?

**Decision.** **No — mount executions stay absent from `active_namespace_executions`,
exactly as today.** *(P4-R13)* The mount path:

1. never calls `begin` (it cannot reach the operation-layer ledger), and
2. drives a **no-op observer** wired into the workspace-local mount engine (§3.2),
   so `on_running`/`on_terminal` for mount ids are guaranteed no-ops that never
   touch the operation-layer ledger.

**Why this is correct, not a regression.** Today the mount path is *untracked* —
`run_child` has "no promise, untracked" (design `namespace-execution.md:18-19`).
Overlay mount / remount have **never** appeared in `active_namespace_executions`.
The migration invariant is "observability surface unchanged … `operation_name` the
only classification axis" (`migration-phases.md:22-27`). *Adding* mount rows would
*change* that surface (new rows, a new pseudo-`operation_name`). Keeping mount
absent is what preserves the invariant.

**Why there is no cycle.** The no-op observer is `Arc<dyn ExecutionObserver>` where
the trait is defined in the engine crate (`observer.rs:5-7`) and the concrete
no-op type lives at or below `workspace` (engine crate preferred; see §3.2). The
engine never names an operation-layer type. `NamespaceTarget` carries no
`WorkspaceSessionId` (`target.rs:7-14`), so the engine needs zero workspace/operation
knowledge. *(P4-R19: the engine crate keeps zero `workspace` dependency.)*

**What `on_running`/`on_terminal` do for a never-`begin`'d id (relevant to the
rejected alternative).** Grounded fact: the current ledger does **not** silently
no-op — `mark_namespace_execution_running` returns `Err("…is not active")` for an
unknown id (`operation/src/namespace_execution.rs:194-202`), and
`complete_namespace_execution` likewise (`:217-222`). Neither pushes a partial
error on that path, so the `Err` is inert if swallowed — but it *is* an `Err`. This
is precisely why the no-op-observer route (which never calls the ledger at all) is
cleaner than feeding mount ids to the ledger observer.

**Rejected alternative — shared engine wired with the ledger observer.** Route mount
through the *same* engine the command service uses (Decision B's rejected option),
whose observer is the operation-layer `NamespaceExecutionLedger`. Mount would then
call `observer.on_running(mount_id)` → `mark_namespace_execution_running(mount_id)`
→ `Err` (un-begun), which the observer impl must *swallow* (the trait returns `()`,
`observer.rs:6`). Mount would still stay out of the list (never `begin`'d, never in
`active`), but: (a) it relies on an implicit swallow-the-`Err` contract in Phase 3's
observer impl rather than an explicit no-op; (b) every mount/remount triggers a
swallowed ledger `Err`, a standing smell; (c) it forces the shared-engine wiring,
which Decision B rejects on scope grounds. The no-op observer makes "mount is not
observable" a *structural* guarantee, not a behavioral accident.

### 3.2 Decision B — Engine ownership: workspace-local, not shared

**Question (Hard problem 2).** One `Arc<NamespaceExecutionEngine>` shared by the
command service and the mount path, or a separate workspace-local engine?

**Decision.** **A separate, workspace-local engine, owned by `NamespaceRuntime`.**
*(P4-R8)*

- `NamespaceRuntime` is a unit struct today (`namespace/mod.rs:90-91:
  #[derive(Default)] pub struct NamespaceRuntime;`), constructed by
  `NamespaceRuntime::new()` (`mod.rs:101-105`). Phase 4 gives it one field:

  ```rust
  pub struct NamespaceRuntime {
      engine: std::sync::Arc<NamespaceExecutionEngine>,
  }
  impl NamespaceRuntime {
      pub fn new(setup_timeout_s: f64) -> Self {
          let observer = /* no-op ExecutionObserver, see below */;
          Self {
              engine: Arc::new(NamespaceExecutionEngine::new(
                  observer, MOUNT_MAX_ACTIVE, setup_timeout_s)),  // [ASSUMED ctor — §12-B]
          }
      }
  }
  ```

  The `#[derive(Default)]` on `NamespaceRuntime` (`mod.rs:90`) is dropped (an
  `Arc<NamespaceExecutionEngine>` has no `Default`); no `NamespaceRuntime::default()`
  caller exists (grep: only `NamespaceRuntime::new()` at `manager.rs:103` and the
  deleted test `workspace/tests/setns_runner.rs:14`).

- **The no-op observer.** Preferred: the engine crate (Phase 2) provides
  `sandbox_runtime_namespace_execution::NoopObserver` (trivial, reusable). Fallback
  (zero extra Phase-2 dependency): a workspace-local `struct MountExecutionObserver;`
  that `impl ExecutionObserver` with empty `on_running`/`on_terminal` bodies
  (orphan rule OK — the trait is foreign, the type is local). Either is below
  operation. *(P4-R8a)*

- **`MOUNT_MAX_ACTIVE`** — a generous constant (recommend ≥ 64) so a mount/remount
  is never refused admission under realistic load (see "Admission" below). *(P4-R8b)*

**Consequences, spelled out.**

- **Admission pool.** A *separate* `max_active` from command. Today mount/remount
  take **no** admission (they are untracked — `namespace-execution.md:18-19`), so
  bounding them under the *command* pool (the shared-engine option) would be a new
  way for a mount to be **refused** — a behavior change in a "preserve-behavior"
  phase. A separate, generously sized pool means a burst of remounts cannot starve
  commands and vice versa, and mounts are effectively never refused. *(P4-R8b)*
- **Observer wiring.** No-op (Decision A), vs. the ledger observer the command
  engine uses. This is the structural guarantee that mount is unobservable.
- **Phase 5 registry queries.** Phase 5's remount coordinator queries the engine
  registry for *live interactive command executions* in a workspace
  (`namespace-execution.md:484-488`; `migration-phases.md:210-218`). Those live in
  the **command** engine's registry (Phase 3). A separate mount engine is
  irrelevant to that query — and arguably cleaner: the command registry is not
  polluted with mount executions. Phase 5 is unaffected. *(P4-R8c)*
- **Construction site.** Entirely inside the `workspace` crate (`NamespaceRuntime::new`,
  threaded from `WorkspaceModeManager::new`, §5). **No edit to `operation/src/services.rs`**
  (the composition root at `services.rs:68-105`, where `WorkspaceModeManager::new`
  is called at `:74`). This is what keeps Phase 4 inside its declared crate scope
  (`workspace`, `daemon`) and avoids colliding with Phase 3, which also wires an
  engine in `services.rs`/`operation/command/service/core.rs`.

**Rejected alternative — one shared engine in `services.rs`.** Faithful to the
design's "one daemon-side engine" phrasing (`namespace-execution.md:5-11, 345-350`).
Rejected because: (a) it requires editing `operation/src/services.rs` (reorder so
the ledger and engine are built *before* `WorkspaceModeManager::new` at `:74`, then
inject the engine down into the manager and up into command) — `operation` is
**outside Phase 4's crate scope** (`migration-phases.md:46`), and the edit collides
with Phase 3's own engine wiring while the two phases are meant to run in parallel
(`migration-phases.md:49-51`); (b) it forces mount onto the command admission pool
(behavior change, above); (c) it forces the swallow-the-`Err` ledger contract of
§3.1's rejected alternative. **Deviation flagged for human review (§12-C):** this
spec intentionally diverges from the design's singular-engine phrasing. The design
text is satisfiable both ways ("both `command` and `workspace` … can use it" —
`namespace-execution.md:157-159` — speaks of the *crate*, not one instance);
consolidating to a single instance is a reasonable post-Phase-6 step once both
producers are on the engine.

### 3.3 Decision C — The mount failure-signaling contract (end to end, both modes)

**Today's behavior (grounded).**

- `setns_overlay_mount` → `Result<(), RunnerError>` (`setns.rs:31-36`). The daemon
  `MountOverlay` arm runs it and **`?`-propagates** on error, else writes `ok_result()`
  = `RunResult{ exit_code: 0, payload: {success:true,status:"ok"} }`
  (`daemon/src/runner.rs:52-59, 66-71`). On failure the daemon process exits
  non-zero and writes **nothing** to the result fd.
- `remount_overlay` → `Result<serde_json::Value, RunnerError>` (`setns.rs:48-53`).
  A *verification failure* is **not** an error — `remount_overlay_inner` returns
  `Ok(report)` with the report carrying `mount_verified` (`setns.rs:96-123`,
  `remount_verification_report` at `:121`). Only a *syscall* error (`setns_user_mnt?`,
  `staged_remount_overlay?`, `mask_guard.restore()?`, missing upperdir/workdir/layers)
  returns `Err`. The daemon `RemountOverlay` arm wraps `Ok` as `RunResult{ exit_code:
  0, payload }` and `?`-propagates the `Err` (`daemon/src/runner.rs:42-51`).
- The caller `apply_remount` inspects the flag itself:
  `if !remount.mount_verified { return Err(WorkspaceModeError::SetupFailed{…}) }`
  (`workspace/src/lifecycle/remount/transaction.rs:49-62`). So **`remount_overlay`
  returns `Ok(mount_verified=false)`; the caller turns it into an error.** This exact
  split must be preserved.

**The new contract (both modes), end to end.** *(P4-R9, P4-R10, P4-R11)*

Daemon `dispatch_runner_mode` (`daemon/src/runner.rs:36-64`):

| Mode | Outcome | `RunResult` written |
|---|---|---|
| `MountOverlay` | `setns_overlay_mount` = `Ok(())` | `exit_code: 0`, `payload: {success:true,status:"ok"}` (unchanged `ok_result()`) |
| `MountOverlay` | `setns_overlay_mount` = `Err(e)` | **`exit_code: 1`, `payload: {"error": "ns-runner setns overlay mount failed: {e}"}`** — *caught, not `?`-propagated* **(CHANGE, P4-R9)** |
| `RemountOverlay` | `remount_overlay` = `Ok(report)` (incl. `mount_verified:false`) | `exit_code: 0`, `payload: report` (**unchanged**) |
| `RemountOverlay` | `remount_overlay` = `Err(e)` | `?`-propagates → child exits non-zero, **no** `RunResult` (**unchanged**) |

Engine `run_mount` watcher (Phase-2 contract this spec pins):

- `RunnerChild::wait_completion()` yields `Ok(RunResult)` if the child wrote a valid
  `RunResult`, else `Err(NamespaceExecutionError)` (child died / unreadable result fd).
- On `Err` → resolve the promise `Err` (terminal error); the closure never runs.
- On `Ok(run_result)`: **if `RunnerOutcome::exit_code() != 0`, resolve the promise
  `Err` (a terminal error carrying the payload's diagnostic text) *before* invoking
  the parse closure**; otherwise invoke `parse(RunnerOutcome(run_result))` and
  resolve with its `Result`. *(P4-R10)*

Workspace call sites (`setns_runner.rs`, §5.1):

- `--mount-overlay`, closure `|_| Ok(())`:
  - success → `exit_code 0` → closure → `Ok(())`.
  - failure → daemon wrote `exit_code 1` + `{error}` → watcher short-circuits →
    `wait()` = `Err(NamespaceExecutionError)` (diagnostic = the payload text) →
    mapped to `WorkspaceModeError::SetupFailed` via `setup_error` (§4.3).
- `--remount-overlay`, closure `|o| Ok(RemountOverlayResult::from_payload(o.payload()))`:
  - ran, verified or not → `exit_code 0` → closure → `Ok(RemountOverlayResult{
    mount_verified: true|false })`. `wait()` = `Ok(result)`; `apply_remount` inspects
    `mount_verified` (`transaction.rs:55`) exactly as today. *(P4-R11)*
  - syscall error → no `RunResult` → `wait_completion` `Err` → `wait()` = `Err` →
    `SetupFailed` (matches today's "non-zero child exit ⇒ `Err`").

**The pinned `Err`-vs-`Ok(false)` boundary.**

- **`Err` from `wait()`** ⟺ the *execution itself* failed: spawn/fork/PTpipe error,
  child died without a valid `RunResult`, or a non-zero `exit_code` (mount syscall
  failure reported via payload, or a remount syscall error).
- **`Ok(RemountOverlayResult{mount_verified:false})`** ⟺ the remount *ran to
  completion* (`exit_code 0`) but verification failed. This is **not** an `Err`; the
  caller decides. Distinct from the above.
- The mount mode (`Output = ()`) has no `Ok(false)` analogue — its only failure is
  the execution-failure path (`Err`), which is exactly why a no-op closure is sound
  *given* the exit-code short-circuit (P4-R10).

**Why the short-circuit, and why it is mode-asymmetric.** The shell family treats a
non-zero `exit_code` as ordinary data (a command that exits 5 is a *successful
execution* whose result records `exit_code = 5`; `ExecCommand::finalize` keeps it —
`namespace-execution.md:333-336`). The mount family has no such notion: a mount that
"exits non-zero" *failed*. With the design-mandated no-op mount closure (`|_| Ok(())`,
`namespace-execution.md:245, 702`), the only place that failure can be turned into an
`Err` is the engine, before the closure. Hence the exit-code short-circuit in
`run_mount`. **This refines the design's "`run_mount` is identical except `spawn_piped`
and the parse closure" (`namespace-execution.md:313`)** — flagged in §12-A.

**Rejected alternative — `exit_code == 0` + a "failure" payload flag inspected by a
non-trivial closure.** I.e. keep `?`-free daemon arms that always write `exit_code 0`,
and make the mount closure `|o| if o.payload()["ok"] {Ok(())} else {Err(…)}`. Rejected
because it contradicts the design's `|_| Ok(())` (`namespace-execution.md:245`), pushes
policy into the closure, and is inconsistent with how the engine already distinguishes
"execution failed" from "ran with result". The chosen contract keeps the no-op closure
and locates the success/failure decision in one place (the exit code).

**Rejected alternative — keep the `MountOverlay` `?`-propagate (write nothing on
failure).** Then `wait_completion` returns `Err` from an unreadable result fd, which
*does* surface as an `Err` from `wait()` — but the specific syscall diagnostic
(`"… overlay mount failed: <reason>"`) is lost (today it rode in the captured stderr;
the engine launcher captures no stderr). Writing the diagnostic into `RunResult.payload`
(the migration doc's instruction, `migration-phases.md:192-194`) preserves it. Hence
the catch-and-write change (P4-R9).

---

## 4. The boundary conversion (`NamespaceTarget` sourcing)

### 4.1 Decision (Hard problem 6)

The mount path obtains its `NamespaceTarget` by reusing the **existing** reachable
chain to a `WorkspaceEntry`, then the **one new** conversion shared with Phase 3:

```rust
// in setns_runner.rs, the two #[cfg(target_os = "linux")] call sites:
let entry = WorkspaceHandle::from(handle).entry().map_err(setup_error)?;  // existing chain
let target = NamespaceTarget::from(entry);                                // the new From, P4-R2
```

- `From<&WorkspaceModeHandle> for WorkspaceHandle` already exists (`model.rs:467-495`).
- `WorkspaceHandle::entry() -> Result<WorkspaceEntry, WorkspaceEntryError>` already
  exists (`model.rs:144-150`, delegating to `WorkspaceLaunchContext::entry()` at
  `:266-274`, which builds `WorkspaceEntryFds` via `required_holder_fds()` at `:276-292`).
- **New, and the only addition:** `impl From<WorkspaceEntry> for NamespaceTarget` in
  `workspace/src/model.rs` (§5.2, §7). *(P4-R2)*

### 4.2 Why this sourcing, with orphan-rule and Option reasoning

- **Reuse, no duplication.** `From<WorkspaceEntry> for NamespaceTarget` maps
  `ns_fds: WorkspaceEntryFds → NsFds` through the **existing** `From<WorkspaceEntryFds>
  for NsFds` (`model.rs:333-342`) — the spec adds **no** new fds mapping, honoring the
  "don't duplicate the fds mapping" constraint (Hard problem 6).
- **Orphan rule.** `impl From<WorkspaceEntry> for NamespaceTarget` is written in
  `workspace`. `From` and `NamespaceTarget` are foreign; `WorkspaceEntry` is local
  (`model.rs:295-302`). A local type appears in the impl with no uncovered type
  parameter preceding it ⇒ allowed. (Confirmed by the design, which places it in
  `workspace` — `namespace-execution.md:542-543, 703-705`.)
- **`Option`-wrapping.** `WorkspaceEntry.upperdir/workdir` are `PathBuf`
  (`model.rs:299-300`); `NamespaceTarget.upperdir/workdir` are `Option<PathBuf>`
  (`target.rs:11-12`). A workspace overlay always has both, so the conversion wraps
  them `Some(_)`. The `Option` exists for engine generality (a future non-overlay
  target may omit them); workspace-sourced targets are always `Some`.
- **`ns_fds` non-optional is satisfied for free.** `NamespaceTarget.ns_fds: NsFds`
  (non-optional, `target.rs:13`) is produced from `WorkspaceEntryFds` whose
  `user/mnt/pid` are non-optional `i32` (`model.rs:314-318`), so `From<WorkspaceEntryFds>
  for NsFds` always yields a fully-populated `NsFds` (`model.rs:333-342`). This is why
  routing via `WorkspaceEntry` resolves the §2.2 divergence cleanly: the *empty*-fds
  case that `ns_fds_from_mode` could return (`fds.rs:145-146`, `None` when
  `WorkspaceModeFds::is_empty`, `handle.rs:56-58`) is **excluded** by `entry()`, which
  errors via `required_holder_fds()` (`model.rs:276-292`) if fds are incomplete.

**Rejected alternative — a direct `From<&WorkspaceModeHandle> for NamespaceTarget`.** It is
orphan-legal in `workspace` (`&WorkspaceModeHandle` is local) and skips the `WorkspaceHandle`
clone, but (a) `From` is *infallible*, so it must map `WorkspaceModeFds → NsFds` without the
`entry()` completeness gate — forcing a `ns_fds_from_mode(...).unwrap_or(empty)` or a panic
for the incomplete-fds case the gate would have rejected; (b) it would be a *new*
fds-mapping site (or a reuse of `ns_fds_from_mode`, whose `Option<NsFds>` return mismatches
`NamespaceTarget`'s non-optional `ns_fds`), versus the chosen path's zero new mapping; (c)
the design names `From<WorkspaceEntry>` as the seam shared with Phase 3
(`namespace-execution.md:542-543`). The `WorkspaceHandle` clone is cheap relative to a
process fork and runs only at workspace create / remount (infrequent), so correctness +
reuse win over avoiding the clone.

### 4.3 Behavior-equivalence of the new `entry()` failure path

`entry()` is fallible; today's `ns_runner_request` is not (`setns_runner.rs:134-150`,
`ns_fds: ns_fds_from_mode(handle.ns_fds)` passes `None` silently if empty). Is the
new early `entry()` error a behavior change?

**No, in the reachable cases.** Both `mount_overlay` and `remount_overlay` run only
after the holder namespaces are open: `initialize_handle` calls `open_ns_fds`
(`create.rs:33-37`) *before* `mount_overlay` (`create.rs:43-45`); remount runs on a
live, active handle (`transaction.rs:38-54`). So `handle.ns_fds` is fully populated at
both sites and `entry()` succeeds. In the *unreachable* incomplete-fds case, today the
runner would reject the request (`run_setns_inner`/`setns_overlay_mount_inner` require
`ns_fds`/upperdir — `setns.rs:57-59, 70-75`) ⇒ non-zero exit ⇒ `Err`. After Phase 4
the error surfaces *earlier* (at `entry()`), still as a `WorkspaceModeError::SetupFailed`.
**Net observable behavior: an incomplete-fds mount fails either way.** *(P4-R3)*

`setup_error` (`namespace/mod.rs:84-88`) maps any `impl Display` →
`WorkspaceModeError::SetupFailed{ step }`. `WorkspaceEntryError: Display`
(`model.rs:363-367`) and `NamespaceExecutionError: Display` (`error.rs:14-27`), so both
the `entry()` error and the `wait()` error map through the same one-line helper.

---

## 5. File-by-file change plan

### 5.1 `crates/sandbox-runtime/workspace/src/namespace/setns_runner.rs`

**Before (relevant inventory, grounded).** `mount_overlay` (`:43`) → `ns_runner_request`
(`:55`) → `mount_overlay_child` (`:153`, `"--mount-overlay"`); `remount_overlay` (`:61`)
→ `ns_runner_request` (`:75`) → `remount_overlay_child` (`:171`); `ns_runner_request`
public method (`:122-131`) + free fn (`:134-150`, holding `request_id:
format!("isolated-{request}-{}", handle.workspace_id.0)` at `:141`); `run_child`
(`:194-232`); `wait_for_child` (`:235-265`); `terminate_child` (`:268-275`); `read_pipe`
(`:278-285`). The sibling `signal_net_ready` (`:91-117`) uses the `control_fd`/`readiness_fd`
handshake (`write_all_fd`/`expect_line`), **not** `run_child`.

**After.**

```rust
// (#[cfg(target_os = "linux")] arm of) mount_overlay
// `json!({})` is the `args` argument of the §5.1.1/§12-D run_mount refinement.
let id = self.engine.allocate_id();
let entry = WorkspaceHandle::from(handle).entry().map_err(setup_error)?;
self.engine
    .run_mount("--mount-overlay", NamespaceTarget::from(entry), id, serde_json::json!({}), |_| Ok(()))
    .map_err(setup_error)?
    .wait()
    .map_err(setup_error)?;
Ok(())
```

```rust
// (#[cfg(target_os = "linux")] arm of) remount_overlay
// NOTE: the `probe_args` argument requires the run_mount `args` refinement of §5.1.1/§12-D.
// If that refinement is not taken, see §5.1.1 for the (rejected) alternatives.
let id = self.engine.allocate_id();
let entry = WorkspaceHandle::from(handle).entry().map_err(setup_error)?;
let probe_args = serde_json::json!({
    "probe_path": probe.path.as_ref().map(|p| p.to_string_lossy().into_owned()),
    "probe_content": probe.expected_content.as_deref(),
});
let result = self
    .engine
    .run_mount("--remount-overlay", NamespaceTarget::from(entry), id, probe_args, |outcome| {
        Ok(RemountOverlayResult::from_payload(outcome.payload()))
    })
    .map_err(setup_error)?
    .wait()
    .map_err(setup_error)?;
Ok(result)
```

- `mount_overlay`/`remount_overlay` **drop the `setup_timeout_s` parameter** (under the
  recommended MOVED timeout, §8/HP5, the engine owns the timeout — constructed from
  `caps.setup_timeout_s` in §5.3/§5.4 — so a call-site `setup_timeout_s` is redundant and,
  if retained unused, would trip `-D warnings`). New signatures:
  `fn mount_overlay(&self, handle: &WorkspaceModeHandle, layer_paths: &[PathBuf]) -> Result<(), WorkspaceModeError>`
  and `fn remount_overlay(&self, handle: &WorkspaceModeHandle, layer_paths: &[PathBuf], probe: &RemountProbe) -> Result<RemountOverlayResult, WorkspaceModeError>`.
  Update the two callers (§5.4.1) and the `#[cfg(not(target_os = "linux"))]` stub arms
  (`setns_runner.rs:49-52, 68-72`) to drop the argument. *(P4-R4, P4-R5, P4-R12)* The sibling
  `signal_net_ready` **keeps** its `setup_timeout_s` param (it is unrelated to the engine —
  `setns_runner.rs:91-117`), so `caps.setup_timeout_s` is still consumed elsewhere.
  *(Fallback note: if the §12-G timeout fallback is chosen instead of MOVED, retain the
  param and use it in a call-site `wait_timeout(setup_timeout_s)`; then the caller/stub edits
  below are unnecessary.)*
- The `args` JSON built today for remount (`probe_path`/`probe_content`,
  `setns_runner.rs:75-86`) — see §5.1.1.
- **Delete** `ns_runner_request` (method `:122-131` + free fn `:134-150`),
  `mount_overlay_child` (`:153-168`), `remount_overlay_child` (`:171-191`), `run_child`
  (`:194-232`), `wait_for_child` (`:235-265`), `terminate_child` (`:268-275`),
  `read_pipe` (`:278-285`). With them, the `isolated-{request}-{workspace_id}` id format
  (`:141`) is gone (`id` now comes from `engine.allocate_id()`). *(P4-R6, P4-R7)*
- **Imports.** Add `use sandbox_runtime_namespace_execution::{NamespaceTarget};` and
  `use crate::model::WorkspaceHandle;`. Remove now-unused imports: `std::fs::File`,
  `std::io::{Read,Write}`, `std::os::fd::AsRawFd`, `std::os::unix::process::CommandExt`,
  `std::process::{Child,Command,ExitStatus,Output,Stdio}`, `std::thread`,
  `std::time::{Duration,Instant}`, `protocol::{NamespaceRunnerRequest, RunResult}`,
  `nix::fcntl::OFlag`, `nix::sys::signal::{kill,Signal}`, `nix::unistd::{pipe2,Pid}`,
  `serde_json::json` (unless still needed by remount `args`, §5.1.1) — all currently at
  `setns_runner.rs:1-26`. Keep `super::fds::{expect_line, ns_fds_from_mode?, write_all_fd}`
  and `holder`/`setup_error` imports that `signal_net_ready` still needs (re-check after
  edit: `ns_fds_from_mode` becomes unused here and moves to dead-import status — remove it
  from this file's imports; it stays defined in `fds.rs` for other users — verify via
  `cargo build`).

**Deletion safety (no surviving caller).** Grounded grep across `crates`:
`run_child`/`wait_for_child`/`terminate_child`/`read_pipe` have callers **only** inside
`setns_runner.rs` (the two `_child` fns and `run_child` itself). `mount_overlay_child`/
`remount_overlay_child` are called **only** by `mount_overlay`/`remount_overlay`
(`:56, :87`). `ns_runner_request` is called by `mount_overlay`/`remount_overlay` (`:55,
:75`) and by **one test** (`workspace/tests/setns_runner.rs:15`, deleted — §9). After
the two call sites are rewritten and the test deleted, **no caller survives.**

#### 5.1.1 The remount `args` payload

Today the remount request's `args` carries the probe (`setns_runner.rs:75-86`):
`{"probe_path": <probe.path?>, "probe_content": <probe.expected_content?>}`. The
in-namespace `remount_overlay` consumes it (it reads probe fields when building the
verification report). The engine's `run_mount(mode_flag, target, id, parse)` carries
**no `args`** — `NamespaceTarget` has no `args` field (`target.rs:7-14`), and the
request is built inside the engine launcher from `target` + (for shell) the op.

**This is a genuine gap: `run_mount` as specified cannot convey the remount probe.**
Resolution options, in preference order:

1. **[RECOMMENDED] Carry the probe on `NamespaceTarget`-adjacent request building via an
   `args` parameter on `run_mount`.** Since the consumed `run_mount` signature
   (`migration-phases.md`, prompt) omits `args`, this is a **required refinement to the
   Phase 2 `run_mount` contract**: add an `args: serde_json::Value` parameter (default
   `json!({})` for `--mount-overlay`). Flagged in §12-D. This is the smallest change
   that preserves remount verification.
2. Fold the probe into `NamespaceTarget` — rejected: pollutes the workspace-agnostic
   boundary type with a remount-only concern.
3. Drop the probe — rejected: it changes remount verification semantics (the report
   would lose `probe_read_ok`/`probe_content_matched`, `result.rs:39-59`).

**Decision:** prescribe option 1 and flag the `run_mount` `args` parameter as a Phase 2
refinement (§12-D). The remount call site becomes
`run_mount("--remount-overlay", target, id, probe_args, parse)` where `probe_args` is the
same JSON built today (`setns_runner.rs:78-85`); the mount call site passes `json!({})`.

### 5.2 `crates/sandbox-runtime/workspace/src/model.rs`

Add (placed near `From<WorkspaceEntryFds> for NsFds`, `:333-342`):

```rust
impl From<WorkspaceEntry> for NamespaceTarget {
    fn from(entry: WorkspaceEntry) -> Self {
        Self {
            workspace_root: entry.workspace_root,
            layer_paths: entry.layer_paths,
            upperdir: Some(entry.upperdir),
            workdir: Some(entry.workdir),
            ns_fds: entry.ns_fds.into(),   // existing From<WorkspaceEntryFds> for NsFds
        }
    }
}
```

Add `use sandbox_runtime_namespace_execution::NamespaceTarget;` to `model.rs`'s imports
(it already imports `protocol::{Fd, NsFds}` at `:5`). *(P4-R2)* No doc comment required
(it is a `From` impl, not a public item needing `///`); follows the file's existing
un-commented `From` impls (`:333, :397, :467`).

### 5.3 `crates/sandbox-runtime/workspace/src/namespace/mod.rs`

- Give `NamespaceRuntime` the engine field and the no-op observer, per §3.2 (drop
  `#[derive(Default)]` at `:90`; change `new()` → `new(setup_timeout_s: f64)` at
  `:101-105`). *(P4-R8)*
- If the engine type is not cross-platform-constructible, gate the field/ctor body with
  the file's existing `#[cfg(target_os = "linux")]` idiom (workspace sources are **not**
  subject to the daemon cfg-policy — §10.4); prefer an unconditional field if Phase 2's
  engine compiles on non-Linux (recommended — the daemon builds on the dev host). [ASSUMED — §12-B]

### 5.4 `crates/sandbox-runtime/workspace/src/profile/manager.rs`

`WorkspaceModeManager::new` (`:96-104`) keeps its 3-arg signature; only its body
changes to thread the timeout into the runtime:

```rust
pub fn new(workspace_root: impl Into<String>, caps: ResourceCaps, scratch_root: PathBuf) -> Self {
    let runtime = NamespaceRuntime::new(caps.setup_timeout_s);   // was NamespaceRuntime::new()
    Self::with_runtime(workspace_root, caps, scratch_root, runtime)
}
```

`caps.setup_timeout_s` is in scope (`ResourceCaps`, `manager.rs:33-39`). `services.rs:74`
is **unchanged** (it calls the same 3-arg `new`). *(P4-R8, P4-R12)*

#### 5.4.1 Mount/remount call-site updates (drop the redundant timeout arg)

- `crates/sandbox-runtime/workspace/src/lifecycle/create.rs:43-45` —
  `self.runtime.mount_overlay(handle, &layer_paths, self.caps.setup_timeout_s)?`
  → `self.runtime.mount_overlay(handle, &layer_paths)?`.
- `crates/sandbox-runtime/workspace/src/lifecycle/remount/transaction.rs:49-54` —
  `self.runtime.remount_overlay(&handle, &layer_paths, probe, self.caps.setup_timeout_s)?`
  → `self.runtime.remount_overlay(&handle, &layer_paths, probe)?`.

Both are the **only** callers of these two methods (grounded grep §5.1 "Deletion safety";
`create.rs:45`, `transaction.rs:49`). `signal_net_ready`'s caller (`create.rs:78-79`) is
**unchanged**. *(P4-R12)*

### 5.5 `crates/sandbox-runtime/workspace/Cargo.toml`

Add to `[dependencies]` (`Cargo.toml:8-16`):
`sandbox-runtime-namespace-execution.workspace = true` — **default features only**
(not `test-support`; that gate is the engine's own test seam). The engine crate depends
only on `namespace-process` (`migration-phases.md:71-73`), so this introduces no cycle:
`workspace → namespace-execution → namespace-process`. *(P4-R1, P4-R19)*

> Depends on Phase 2 having promoted `ExecutionHandle`/`run_mount`/`RunnerOutcome::payload`
> out of `#[cfg(feature = "test-support")]` (`lib.rs:16-38`) into the default build.
> Flagged §12-E.

### 5.6 `crates/sandbox-daemon/src/runner.rs`

- **`MountOverlay` arm (`:52-59`)** → catch the syscall result and emit a `RunResult`
  per §3.3, instead of `?`-propagating. Add a small `pub(crate)` helper so the mapping is
  unit-testable without real namespaces (§9.3):

  ```rust
  NsRunnerOperation::MountOverlay => Ok(mount_overlay_result(
      sandbox_runtime_namespace_process::runner::setns::setns_overlay_mount(
          request, &runner_config.mount_mask.hidden_paths,
      ),
  )),
  // ...
  pub(crate) fn mount_overlay_result(outcome: Result<(), impl std::fmt::Display>)
      -> sandbox_runtime_namespace_process::runner::protocol::RunResult
  {
      match outcome {
          Ok(()) => ok_result(),
          Err(error) => RunResult {
              exit_code: 1,
              payload: serde_json::json!({
                  "error": format!("ns-runner setns overlay mount failed: {error}")
              }),
          },
      }
  }
  ```

  `ok_result()` (`:66-71`) is reused unchanged. The arm no longer uses `.context(…)?`.
  *(P4-R9)*
- **`RemountOverlay` arm (`:42-51`)** — **unchanged** (verification failures already ride
  in `payload` with `exit_code 0`; syscall errors keep `?`-propagating — §3.3, P4-R11).
- **Param rename (`dispatch_runner_mode`, `:36-40`)** — rename `mode: NsRunnerOperation`
  → `operation: NsRunnerOperation` (cosmetic, "for clarity" per `migration-phases.md:194`);
  update the body's `match mode` → `match operation` and the call site `dispatch_runner_mode(mode, …)`
  at `:31` (the local `let mode = config.mode;` may stay or be inlined — no behavior
  change). *(P4-R14)*
- **No `#[cfg]` introduced.** The arm change and helper are pure logic; the daemon-source
  cfg-policy (`xtask/tests/cfg_policy.rs:240-253`) stays green. *(P4-R17)*
- **`--start-ack-fd` plumbing untouched** (P4-R18, §1.3).

---

## 6. Safe edit order (each step keeps `cargo build` green)

`daemon` and `workspace` are independent crates; the engine crate is below both.
Order so nothing references a not-yet-existing symbol:

1. **`workspace/Cargo.toml`** — add the engine dependency (P4-R1). Builds (unused dep).
2. **`workspace/src/model.rs`** — add `From<WorkspaceEntry> for NamespaceTarget`
   (P4-R2). Builds (unused impl). *(If Phase 3 already added it — §7 — skip.)*
3. **`workspace/src/namespace/mod.rs`** + **`manager.rs`** — add the engine field +
   no-op observer + `new(setup_timeout_s)`, thread `caps.setup_timeout_s` (P4-R8, R12).
   Builds (`engine` field unused). Delete the now-invalid `workspace/tests/setns_runner.rs`
   in the same step (it calls `NamespaceRuntime::new()` with no args, `:14`, and the
   deleted `ns_runner_request`, `:15`) so the crate's test target still compiles (§9.1).
4. **`workspace/src/namespace/setns_runner.rs`** — rewrite the two call sites; delete the
   six helpers + the builder; fix imports (P4-R3..R7). Workspace builds & tests.
5. **`daemon/src/runner.rs`** — `MountOverlay` failure → payload + `mount_overlay_result`
   helper; param rename (P4-R9, R14). Daemon builds.
6. **Tests** — add the workspace + daemon tests (§9). Run the full verification (§10).

Steps 1–4 (+ the test deletion) are the `workspace` crate; step 5 is `daemon`; they have
no ordering dependency on each other beyond the engine crate existing (Phase 2). Within
`workspace`, step 4 depends on steps 1–3.

---

## 7. Cross-phase coordination

**The single shared edit with Phase 3 is `From<WorkspaceEntry> for NamespaceTarget`**
(`model.rs`). Phase 3's command pseudocode also uses it (`namespace-execution.md:379:
NamespaceTarget::from(handler.entry()?)`).

**Contradiction surfaced.** The migration doc assigns this impl to **Phase 4**'s edit
list (`migration-phases.md:190`) and lists Phase 3's edits *without* it
(`migration-phases.md:135-158`); yet Phase 3's command path needs it, and Phase 3's crate
scope is `command`/`operation` (`migration-phases.md:46`), which **cannot** add a
`workspace`-crate impl. Phases 3 and 4 are meant to run in parallel
(`migration-phases.md:49-51`).

**Resolution (ownership rule).** Treat the impl **and** the `workspace →
namespace-execution` dependency (P4-R1) as a **shared prerequisite** owned by *whichever
of Phase 3 / Phase 4 lands first*, added idempotently:

- Phase 4 adds both (P4-R1, P4-R2) per §5; the exact shape is pinned in §5.2 and §5.5.
- If Phase 3 lands first, it must add the identical dependency + impl in `workspace`
  (a `workspace` edit beyond Phase 3's nominal scope) — **this is the contradiction; flag
  for human review (§12-F).** When Phase 4 then runs, steps 1–2 of §6 become no-ops
  (verify the impl/dep already match §5.2/§5.5; do not duplicate).
- Both phases MUST agree on the exact signature in §5.2 so a parallel landing produces
  byte-identical code (no merge conflict beyond "already present").

No other edit is shared: `setns_runner.rs`, `mod.rs`, `manager.rs`, `daemon/src/runner.rs`
are Phase-4-exclusive; Phase 3 owns `command`/`operation/command` and the
`NamespaceExecutionStore → NamespaceExecutionLedger` rename + `ExecutionObserver` impl.

---

## 8. Invariants preserved

| # | Invariant | Mechanism | Test (§9) |
|---|---|---|---|
| P4-R4 | Overlay mount succeeds via the engine | `mount_overlay` → `run_mount("--mount-overlay",…,\|_\| Ok(()))` →`.wait()` | W1 |
| P4-R5 | Live remount succeeds via the engine | `remount_overlay` → `run_mount("--remount-overlay",…,parse)` →`.wait()` | W2 |
| P4-R5b | Remount report parses | closure `RemountOverlayResult::from_payload(o.payload())` (`result.rs:17-29`) | W2, W3 |
| P4-R11 | Verification failure stays `Ok(mount_verified=false)`, **not** `Err`; caller decides | daemon writes `exit_code 0` + report; closure returns `Ok(false)`; `apply_remount` checks the flag (`transaction.rs:55`) | W3 |
| P4-R9/R10 | Mount **failure** ⇒ terminal `Err` (not `Ok`) | daemon `exit_code 1` + `{error}`; `run_mount` exit-code short-circuit | W4, D1 |
| P4-R12 | Setup-timeout enforced with SIGTERM-grace→SIGKILL parity | engine launcher honors construction-time `setup_timeout_s` on the piped wait (escalation **moved** from `wait_for_child`) — **see Hard-problem-5 note below** | (engine-level, Phase 2; see §9.4) |
| P4-R13 | Mount absent from `active_namespace_executions` | no `begin`; no-op observer (§3.1) | W5 |
| P4-R18 | `--start-ack-fd` plumbing intact | daemon edit avoids it (§5.6) | D2 (existing) |
| P4-R17 | Daemon sources stay `#[cfg]`-free | arm change is pure logic | `xtask check-cfg` (§10) |
| P4-R19 | Engine crate keeps **zero** `workspace` dep | dependency direction `workspace → engine`; no-op observer is engine-crate or workspace-local | `cargo tree` (§10) |
| — | No `execution_kind`/`backing` axis | none introduced; mount is unobservable | observability greps (§10) |

**Hard-problem-4 — blocking `.wait()`, admission, and watcher lifecycle.** Session-lifecycle
callers block on `run_mount(...).wait()` (today's behavior — `mount_overlay`/`remount_overlay`
are synchronous from `create.rs:43-45` / `transaction.rs:49-54`). The engine still spawns a
watcher thread + promise; this uniformity is **kept, no synchronous fast path**, because the
watcher is what owns completion/cancellation/observer uniformly with the command family, and
the only cost is one short-lived thread per (infrequent) mount. The registry lifecycle for a
mount execution:

- **Admission:** `run_mount` calls `registry.try_reserve()` like every execution
  (`namespace-execution.md:295`). Because the mount engine is *workspace-local* with a
  generous `MOUNT_MAX_ACTIVE` (§3.2, P4-R8b), a mount/remount is never refused in practice;
  if `try_reserve` ever fails it returns `NamespaceExecutionError::Admission` (`error.rs:11`)
  → `wait()`/`run_mount()` `Err` → `SetupFailed` (no hang).
- **Live → completed:** the execution enters the live map, the watcher resolves the promise
  inline (`finalize`/parse → `promise.resolve` → `registry.complete(id)` →
  no-op `observer.on_terminal`), then the watcher thread **exits on its own** (it is not
  joined by `.wait()`; the promise's condvar is the handoff — `promise.rs:27-54`). No thread
  leak: each watcher runs once and terminates.
- **After `.wait(self)`:** the handle is consumed and dropped; the caller owns the `Result`.
  Nothing about the mount is retained that a caller re-reads (unlike command, which re-reads
  a completed transcript). The completed registry entry's retention is a registry policy
  concern (low-volume here) — see §12-H.

**Hard-problem-5 — timeout & termination parity (decision: MOVED, with a flagged Phase-2
dependency).** Today the setup-timeout is enforced **daemon-side** by
`wait_for_child` (`setns_runner.rs:235-265`): poll `try_wait` every 10 ms; at the
deadline SIGTERM the child's process group **and** pid, 100 ms grace, then SIGKILL, then
reap. The request's `timeout_seconds` is `None` for mount today (`setns_runner.rs:148`),
and the in-namespace mount/remount path has **no** scope-wait (it dispatches to
synchronous syscalls — `daemon/src/runner.rs:42-59`; only `Run` mode has
`wait_for_command_execution_scope`). So the timeout was **always** external to the
namespace.

- **Decision: MOVE the escalation into the engine launcher's piped completion path.**
  The workspace-local mount engine is constructed with `caps.setup_timeout_s` (§5.3-§5.4);
  its launcher bounds the piped `wait_completion()` by that timeout and applies the
  identical SIGTERM-grace(100 ms)→SIGKILL→reap escalation, then returns a terminal error.
  `run_mount(...).wait()` then yields `Err` → `SetupFailed{ step: "ns-runner … timed out" }`,
  matching today's message shape (`setns_runner.rs:251-261`). This **preserves behavior
  exactly**, relocated. *(P4-R12)*
- **Phase-2 dependency (flagged §12-G).** Phase 2's stated exit criteria
  (`migration-phases.md:114-120`) describe an *unbounded* `wait_completion()` plus a
  non-killing `wait_timeout` and (interactive-only) `cancel()`. A piped-execution
  setup-timeout with escalation is **not** in that list, so this MOVE requires a small,
  localized Phase-2 launcher capability (a per-engine `setup_timeout_s` applied to piped
  waits). The spec prescribes it as the correct end state and flags the cross-phase
  amendment.
- **Rejected fallback — DROP the kill.** Use `ExecutionHandle::wait_timeout(setup_timeout_s)`
  (a Phase-2-add, non-consuming peek — design `namespace-execution.md:191`) at the call
  site: return `SetupFailed` after the timeout but leave the child unkilled (it finishes
  its syscall and the watcher resolves an abandoned promise; bounded leak). This needs no
  Phase-2 launcher change but **changes behavior** (no SIGTERM/SIGKILL escalation). Not
  recommended; documented so the implementer can fall back if the Phase-2 amendment is
  refused, **flagging the behavior change loudly** per the Hard-problem-5 mandate.

---

## 9. Test plan

Repo rule (`CLAUDE.md`; enforced for sources by `xtask/tests/cfg_policy.rs` and the
"relocated tests" commits `aefeb2233`, `42cdc61c3`, `935850301`): **no inline `#[cfg(test)]`
in production sources**; unit tests live in `tests/` integration suites. All new tests
below are integration-suite tests.

### 9.1 Workspace tests that move/are deleted

- **`crates/sandbox-runtime/workspace/tests/setns_runner.rs` — DELETE.** Its single test
  `workspace_setns_request_carries_mount_material` (`:12-24`) asserts
  `request.request_id == "isolated-remount-workspace"` (`:23`) and exercises
  `runtime.ns_runner_request(...)` (`:15`) — both of which Phase 4 deletes (P4-R6, R7). The
  request is now built inside the engine launcher and the id comes from `allocate_id()`;
  there is nothing left to assert here. Remove the file (and its `mod` line if registered;
  it is a standalone integration test file, not under `tests/unit/`). *(P4-R15)*
- `crates/sandbox-runtime/workspace/tests/unit/{model,remount_plan,service}.rs` — keep;
  re-run to confirm `From<WorkspaceEntry> for NamespaceTarget` and the engine field do not
  break `WorkspaceEntry`/manager/remount-plan coverage.

### 9.2 New workspace tests (`tests/`, Linux-gated as today)

These exercise the mount family through the engine. **Reality check:** the deleted
`tests/setns_runner.rs` was `#![cfg(target_os = "linux")]` and tested only the *request
builder*, never a real mount (`workspace/tests/setns_runner.rs:1, 12-24`); a real
overlay-mount/live-remount needs a Linux namespace environment and is validated by the
**Linux e2e/live suite** (`docs/e2e/`), not a dev-host unit test. So the workspace-crate
tests below assert what is unit-testable off-Linux — the `From<WorkspaceEntry>` conversion,
the parse closure (`RemountOverlayResult::from_payload`), the `Err`-vs-`Ok(false)` boundary,
and (if Phase 2 exposes a **fake `NsRunnerLauncher`** to downstream crates) the call-site
wiring — while W1/W2's real-syscall success is owned by the e2e suite. Pin:

- **W1** — overlay mount success: `mount_overlay` returns `Ok(())` when the runner reports
  `exit_code 0`. *(P4-R4)*
- **W2** — remount success: `remount_overlay` returns `Ok(RemountOverlayResult{
  mount_verified:true})` when the payload verifies; the report parses
  (`result.rs:17-29`). *(P4-R5, R5b)*
- **W3** — remount verification failure stays `Ok(mount_verified=false)` (**not** `Err`);
  drive `apply_remount` (`transaction.rs:38-73`) and assert it is the *caller* that turns
  the flag into `SetupFailed` (`:55-62`). *(P4-R11)*
- **W4** — mount failure ⇒ `Err`/`SetupFailed` when the runner reports `exit_code 1` +
  `{error}` (and the diagnostic text propagates). *(P4-R9, R10)*
- **W5** — observability: after a mount/remount, the operation-layer
  `snapshot_active_namespace_executions` shows **no** mount row (the operation observability
  tests already assert command rows; W5 asserts mount adds none). *(P4-R13)* If a
  workspace-level test cannot reach the operation snapshot, assert it at the operation
  integration suite instead (the engine's no-op observer guarantees it).

### 9.3 New daemon test (`crates/sandbox-daemon/tests/unit/runner.rs`)

The harness path-includes `runner.rs` as `runner_cli` (`tests/unit.rs:9-10`), so a
`pub(crate)` helper is reachable as `crate::runner_cli::mount_overlay_result`.

- **D1** — `mount_overlay_result(Ok(()))` ⇒ `RunResult{ exit_code:0, … }` equal to
  `ok_result()`; `mount_overlay_result(Err("boom"))` ⇒ `RunResult{ exit_code:1,
  payload["error"] contains "overlay mount failed" }`. Pure, no namespaces, cfg-free.
  *(P4-R9)* Follows the existing pattern in that file (`RunnerCliConfig::parse` tests,
  `tests/unit/runner.rs:8-66`).
- **D2** — existing `wait_for_start_ack_*` tests (`tests/unit/runner.rs:70-95`) and
  `RunnerCliConfig::parse` tests **stay green** (start-ack untouched). *(P4-R18)*

### 9.4 Engine-level (Phase 2 territory, noted not owned)

The exit-code short-circuit (P4-R10) and the piped setup-timeout escalation (P4-R12) are
engine behaviors; their unit tests belong to Phase 2's
`crates/sandbox-runtime/namespace-execution/tests/`. Phase 4 *depends on* them and lists
them here so the integrator confirms Phase 2 covers: (a) `run_mount` with `exit_code != 0`
resolves `Err` before the closure; (b) piped `wait_completion` honoring `setup_timeout_s`
with SIGTERM→SIGKILL. If absent, raise the §12-A/§12-G amendments.

---

## 10. Verification

```sh
export PATH="$PWD/bin:$PATH"

# Format
cargo fmt --check

# Build / test the touched crates
cargo build -p sandbox-runtime-workspace
cargo test  -p sandbox-runtime-workspace
cargo build -p sandbox-daemon
cargo test  -p sandbox-daemon --test unit          # runner.rs harness (D1, D2)

# Operation-layer observability unchanged (mount adds no row)
cargo test  -p sandbox-runtime observability
cargo test  -p sandbox-runtime --tests

# Lints (deny warnings), per CLAUDE.md
cargo clippy -p sandbox-runtime-workspace --all-targets --no-deps -- -D warnings
cargo clippy -p sandbox-daemon            --all-targets --no-deps -- -D warnings

# Daemon sources stay #[cfg]-free (the new xtask policy)
cargo test  -p xtask --test cfg_policy             # incl. sandbox_daemon_sources_are_free_of_cfg

# Absence greps — the bespoke mount launch path is gone (expect no hits)
rg -n "fn run_child|fn ns_runner_request|fn wait_for_child|fn terminate_child|fn read_pipe" \
     crates/sandbox-runtime/workspace/src
rg -n "isolated-" crates/sandbox-runtime/workspace/src/namespace/setns_runner.rs

# Engine crate keeps zero workspace dependency
cargo tree -p sandbox-runtime-namespace-execution -e normal | rg -q "sandbox-runtime-workspace" \
  && echo "CYCLE!" || echo "engine has no workspace dep ✓"

# Observability axis unchanged (no new classification field)
rg -n "execution_kind|namespace_execution_kind|runner_kind|backing|active_executions|active_commands" \
     crates/sandbox-runtime/operation/src crates/sandbox-runtime/workspace/src

git diff --check
```

Expected: all builds/tests/clippy green; the two absence greps print **no lines**;
`cargo tree` prints "✓"; the axis grep shows no Phase-4-introduced hits.

---

## 11. Requirements traceability matrix

| Req | Design / migration element | Mechanism (file) | Test | Verify command |
|---|---|---|---|---|
| P4-R1 | crate graph `workspace → namespace-execution` (`namespace-execution.md:521`) | `workspace/Cargo.toml` | build | `cargo build -p sandbox-runtime-workspace` |
| P4-R2 | `From<WorkspaceEntry> for NamespaceTarget` (`namespace-execution.md:542-543, 703-705`) | `model.rs` | W1–W4 | `cargo test -p sandbox-runtime-workspace` |
| P4-R3 | boundary conversion (Hard-problem-6) | `setns_runner.rs` via `WorkspaceHandle::entry()` | W1, W4 | `cargo test -p sandbox-runtime-workspace` |
| P4-R4 | overlay mount via engine (`migration-phases.md:187-189`) | `setns_runner.rs::mount_overlay` | W1 | `cargo test -p sandbox-runtime-workspace` |
| P4-R5 | remount via engine (`migration-phases.md:187-189`) | `setns_runner.rs::remount_overlay` | W2 | `cargo test -p sandbox-runtime-workspace` |
| P4-R6 | delete `isolated-{mode}-{id}` (`migration-phases.md:196-197`) | `setns_runner.rs` | — | `rg "isolated-"` |
| P4-R7 | delete `run_child` + helpers + builder (`migration-phases.md:185-197`) | `setns_runner.rs` | — | `rg "fn run_child\|fn ns_runner_request\|…"` |
| P4-R8 | engine ownership (Hard-problem-2) | `namespace/mod.rs`, `manager.rs` | W1–W5 | build + W-tests |
| P4-R9 | `MountOverlay` failure → payload (`migration-phases.md:192-194`) | `daemon/src/runner.rs` | D1, W4 | `cargo test -p sandbox-daemon --test unit` |
| P4-R10 | exit-code short-circuit (Hard-problem-3) | engine `run_mount` (Phase 2) | W4, §9.4(a) | engine tests |
| P4-R11 | remount `Ok(false)` ≠ `Err` (Hard-problem-3) | daemon `exit_code 0` + closure | W3 | `cargo test -p sandbox-runtime-workspace` |
| P4-R12 | timeout parity MOVED (Hard-problem-5) | engine launcher + `setup_timeout_s` ctor | §9.4(b) | engine tests |
| P4-R13 | mount unobservable (Hard-problem-1; `migration-phases.md:22-27`) | no `begin` + no-op observer | W5 | `cargo test -p sandbox-runtime observability` |
| P4-R14 | rename `dispatch_runner_mode` param (`migration-phases.md:194`) | `daemon/src/runner.rs` | build | `cargo build -p sandbox-daemon` |
| P4-R15 | obsolete test deleted | rm `workspace/tests/setns_runner.rs` | — | `cargo test -p sandbox-runtime-workspace` |
| P4-R17 | daemon sources `#[cfg]`-free | pure-logic arm | cfg_policy | `cargo test -p xtask --test cfg_policy` |
| P4-R18 | start-ack untouched (`migration-phases.md:262-264`) | daemon edit scope | D2 | `cargo test -p sandbox-daemon --test unit` |
| P4-R19 | engine has zero `workspace` dep (`namespace-execution.md:546-548`) | dep direction | — | `cargo tree …` |

---

## 12. Risks & open decisions (each with a recommended resolution)

- **§12-A — `run_mount` exit-code short-circuit refines the design (P4-R10).** The design
  says `run_mount` is "identical except `spawn_piped` and the parse closure"
  (`namespace-execution.md:313`), but the no-op mount closure forces the engine to map
  `exit_code != 0` → terminal `Err` before the closure. **Recommend:** Phase 2 implement
  the short-circuit in `run_mount` (not `run_shell_interactive`); it is small and local.
  *Human review: confirm the design owner accepts this refinement.*
- **§12-B — Engine constructor signature & cross-platform build [ASSUMED].** Phase 4
  assumes `NamespaceExecutionEngine::new(observer: Arc<dyn ExecutionObserver>, max_active:
  usize, setup_timeout_s: f64)` and that the engine compiles on non-Linux (for the dev
  host build). **Recommend:** pin this constructor in Phase 2; if the engine is Linux-only,
  gate `NamespaceRuntime.engine` with the existing `#[cfg(target_os="linux")]` idiom
  (workspace is exempt from the daemon cfg-policy).
- **§12-C — Two engine instances vs. the design's "one engine" (Decision B).** Phase 4
  uses a workspace-local mount engine, not the command engine. **Recommend:** accept for
  the migration (it respects Phase 4's crate scope and preserves admission behavior);
  schedule a post-Phase-6 consolidation to a single shared engine if desired. *Human
  review.*
- **§12-D — `run_mount` lacks an `args` channel for the remount probe (§5.1.1).**
  **Recommend:** add `args: serde_json::Value` to `run_mount` in Phase 2 (mount passes
  `json!({})`, remount passes the probe JSON). Without it, remount verification loses the
  probe. *Human review / Phase-2 amendment.*
- **§12-E — Engine API still behind `test-support` (`lib.rs:16-38`).** Phase 4 needs
  `ExecutionHandle`/`run_mount`/`RunnerOutcome::payload` in the default build.
  **Recommend:** Phase 2 promotes them out of the feature gate (consistent with the
  "tighten test-support gating" trend, commit `935850301`).
- **§12-F — `From<WorkspaceEntry>` ownership across Phases 3/4 (§7).** Phase 3 needs the
  impl but cannot edit `workspace` within its scope. **Recommend:** treat the impl + the
  `workspace → engine` dep as a shared prerequisite added by whichever phase lands first,
  with the exact §5.2/§5.5 shape; if Phase 3 lands first it edits `workspace` (scope
  exception). *Human review.*
- **§12-G — Piped setup-timeout is a Phase-2 launcher capability not in Phase 2's stated
  exit criteria (Hard-problem-5).** **Recommend:** add a per-engine `setup_timeout_s`
  applied to piped `wait_completion` with SIGTERM→SIGKILL escalation (the MOVE). Fallback:
  call-site `wait_timeout` that drops the kill (behavior change — not recommended).
- **§12-H — Completed mount registry entries are never re-read (minor).** Unlike command,
  mount never reads a completed entry after `.wait()`. If the engine registry retains
  completed entries indefinitely, mounts accrue dead entries. **Recommend:** the engine
  registry caps/evicts completed entries (as the ledger caps `recent_projected` to 256,
  `namespace_execution.rs:11`); for the workspace-local mount engine this is low-volume
  regardless. *Low severity.*
- **§12-I — `RemountOverlay` syscall-error diagnostics are coarser than mount's.** Remount
  syscall errors flow via "no `RunResult` → `wait_completion` `Err`" (generic message),
  while mount failures carry payload text. **Recommend:** acceptable (matches today, where
  remount syscall errors surfaced via non-zero child exit); optionally extend the
  catch-and-write pattern to the `RemountOverlay` arm later. *Low severity.*

---

## 13. Definition of done & LOC delta

**Definition of done.**

- Overlay mount and live remount run through `engine.run_mount(flag, target, id,
  parse).wait()`; the remount report parses; verification failure returns
  `Ok(mount_verified=false)` and the caller errors; mount failure returns `Err`.
- `run_child`, `wait_for_child`, `terminate_child`, `read_pipe`, `ns_runner_request`
  (method + free fn), `mount_overlay_child`, `remount_overlay_child`, and the
  `isolated-{mode}-{id}` format are gone (absence greps pass).
- `NamespaceRuntime` owns a workspace-local engine with a no-op observer; mount executions
  do not appear in `active_namespace_executions`; the engine crate has no `workspace`
  dependency.
- Daemon `MountOverlay` failure rides in `RunResult.payload`; the `--start-ack-fd`
  plumbing is intact; daemon sources stay `#[cfg]`-free.
- `From<WorkspaceEntry> for NamespaceTarget` exists in `workspace/src/model.rs` (shared
  with Phase 3); `workspace` depends on `sandbox-runtime-namespace-execution`.
- All §10 commands green; the obsolete `workspace/tests/setns_runner.rs` is deleted; W1–W5
  and D1 added.
- The §12 amendments (esp. A, D, G) are confirmed with Phase 2 / the design owner before
  the engine-dependent behavior is exercised in CI.

**LOC delta (measured deletions; additions estimated).**

| Bucket | Lines |
|---|---|
| Delete in `setns_runner.rs`: `run_child` (~39) + `wait_for_child` (~31) + `terminate_child` (~8) + `read_pipe` (~8) + `ns_runner_request` method (~10) + free fn (~17) + `mount_overlay_child` (~16) + `remount_overlay_child` (~21) + trimmed imports (~15) | **≈ −165** |
| Add in `setns_runner.rs`: two `run_mount` call sites + conversions (~24) | **≈ +24** |
| `model.rs`: `From<WorkspaceEntry> for NamespaceTarget` (+1 import) | **≈ +13** |
| `namespace/mod.rs` + `manager.rs`: engine field, no-op observer, `new(setup_timeout_s)` wiring | **≈ +18** |
| `daemon/src/runner.rs`: `mount_overlay_result` helper + arm rewrite + param rename | **≈ +10** |
| `workspace/Cargo.toml` | **+1** |
| Delete `workspace/tests/setns_runner.rs` | **−64** |
| Add tests W1–W5, D1 | **≈ +90** |
| **Net production (excl. tests)** | **≈ −100** |
| **Net incl. tests** | **≈ −75** |

Within the design's "~−200 LOC" ballpark for Phase 4 (`migration-phases.md`/`namespace-execution.md:604`
estimate `setns_runner` −197); the directly deletable bespoke-launch functions measure
≈ 150 lines, the remainder being import/builder trims realized once `cargo build` confirms
the unused-import set.
