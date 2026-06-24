# Namespace Execution Engine

## Purpose

Give the runtime **one** daemon-side engine for "work dispatched into a
workspace's namespaces via the `ns-runner` re-exec," with a typed completion
promise, generic lifecycle tracking, and a single cancellation/finalization
path. Make `exec_command` the first *subtype* of that engine rather than a
bespoke stack, and fold the overlay/remount fork path onto the same core, so the
next namespace operation costs an operation definition instead of a parallel
copy of the spawn/promise/finalize machinery.

Today the same `current_exe ns-runner` re-exec is launched from **two**
unrelated places with duplicated fd/spawn/wait plumbing:

- commands — `command/src/pty.rs::spawn_current_exe_ns_runner` (PTY child, async,
  watcher + completion promise + finalizer);
- overlay/remount — `workspace/src/namespace/setns_runner.rs::run_child`
  (piped child, blocking, no promise, untracked).

This spec unifies both behind one engine and re-expresses command on top of it.

This is a runtime-architecture change, **not** an observability change: the
observability contract (one `active_namespace_executions` list,
`operation_name` as the only classification axis, no `execution_kind`/`backing`
field) is preserved exactly — see "Observability Contract."

## Current Architecture Context

### The `ns-runner` re-exec engine

The persistent per-session runner server (`runner/server/`, the
`ns-runner-server` subcommand) is being **reverted**. This spec therefore
targets the stable re-exec model, not the server:

```text
daemon `ns-runner [--mount-overlay|--remount-overlay]`        (daemon/src/runner.rs)
  └─ dispatch_runner_mode → { Run, MountOverlay, RemountOverlay }
       Run            → runner::run → setns::run_setns → join_namespaces → shell_exec::execute_shell
       MountOverlay   → setns::setns_overlay_mount
       RemountOverlay → setns::remount_overlay
  request in via --request-fd (NamespaceRunnerRequest JSON), result out via --result-fd (RunResult JSON)
```

The protocol envelope (`NamespaceRunnerRequest { request_id, args, workspace_root,
layer_paths, upperdir, workdir, ns_fds, timeout_seconds }` → `RunResult {
exit_code, payload }`, `runner/protocol.rs`) and the in-namespace runner
(`runner/{mod,setns,shell_exec}.rs`) are unchanged by this spec. Only the
**daemon-side spawn/promise/finalize plumbing** is unified.

The engine deliberately puts the spawn behind one `pub(crate)` launcher seam
(`NsRunnerLauncher`, with `spawn_pty`/`spawn_piped`). If a persistent runner
server returns later, only that seam changes; command, mount, the promise, the
registry, and tracking stay identical — completion reaches the engine as
`RunnerChild::wait_completion()` (a fork `child.wait()` + result-fd read today,
an `Exited` frame for a future server), never as an exposed child pid. Decoupling
the operations from *how* the runner is launched is a primary goal, not a side
effect.

### Observability boundary

The implemented observability work keeps `command_session_id` as a
command-domain concept, keeps `operation_name` as the only namespace-execution
classification axis, and **defers** any generic `execution_kind`/`backing` field
"until a second live producer makes it necessary." That contract lives in this
section, the "Observability Contract" section below, and
`operation/src/namespace_execution.rs`.

This spec respects that. The engine is an **internal** mechanism that adds no
public classification axis; `operation_name` stays the observability axis. The
internal generalization (a shared invocation/promise/registry/finalizer) is
justified by the duplication above: building it once is strictly less code than
maintaining two fork paths plus a third for the next operation.

## Architecture Decision

Introduce a daemon-side **namespace execution engine** over the `ns-runner`
re-exec, and re-express both command and overlay/remount on top of it.

1. **One generic core, two operation families.** A core that builds a request,
   forks the runner behind the `NsRunnerLauncher` seam, and resolves a typed
   promise — knowing nothing about shell vs. mount. On top, two disjoint
   families (no single trait unions them):
   - **Shell family** (`Run` mode → `shell_exec`): the `ShellOperation` trait —
     `ExecCommand`; later a `ShellOp<O>` combinator.
   - **Mount family** (`MountOverlay`/`RemountOverlay` modes → mount syscalls):
     `MountOverlayOp`, `RemountOverlayOp` — expressed as **two `run_mount` call
     sites, each with a parse closure, with no `MountOperation` trait** (two
     fixed strategies, one call site each, no central dispatch to extend).

2. **Command is a subtype, by composition — not inheritance.** Rust has no
   inheritance; the relationship is plain composition:

   ```text
   ExecutionHandle<T>             genus: id + completion promise
     └─ InteractiveExecution<T>   = ExecutionHandle<T> + a PTY (stdin/stream/cancel)
          └─ CommandExecution     = InteractiveExecution<CommandTerminalResult> + command UX
   ```

   `InteractiveExecution<T>` *contains* an `ExecutionHandle<T>` and adds
   capability; the handle's methods (`id`/`is_finished`/`wait`/`wait_timeout`)
   are **inherent** and forwarded. There is **no `Execution<T>` trait** (no call
   site is polymorphic over the two handle types) and no `Deref` polymorphism.

3. **Promise for every operation; sync callers just `wait()`.** Commands return
   an `InteractiveExecution<T>` and yield incrementally; overlay/remount return a
   plain `ExecutionHandle<T>` and call `.wait()` at the session-lifecycle call
   site (their current blocking behavior, now promised + tracked).

4. **One id space; the origin id stays distinct.** `namespace_execution_id` is
   the one id — the runner `request_id`, the registry key, and (wrapped as
   `CommandSessionId`) the public face of the command API. The `cmd_N` and
   `isolated-{mode}-{id}` id formats are **deleted**. The observer record's
   separate `request_id` field is the **external origin request id** and is
   renamed `origin_request_id` so it cannot be misread as the execution id — it
   is a different value (an existing test pins them distinct).

5. **Gut `CommandProcessStore`.** Its generic ~60% (active/completed maps,
   admission, the `FinalizationState` machine, the completion promise) becomes
   the engine registry. What remains is a thin command session view (transcript
   cursor + session disposition) **carried on the registry-stored
   `CommandExecution`, not a second map**. The write-only state
   (`CommandLifecycleState`, `CancellationState`/`cancellation`,
   `remount_switch_state`, and the always-`None` `CommandFinalizedMetadata`
   publish family) is **deleted, not migrated**; remount scratch lives in the
   remount coordinator. See "CommandProcessStore Disposition."

6. **Backing is binary and implicit.** Interactive shell uses a PTY; mount/batch
   use pipes — and that choice *is* which launcher method the engine calls
   (`spawn_pty` vs `spawn_piped`), so there is **no `Backing` enum** and no
   `Captured`/`Report` taxonomy. Result shape is what an operation's `finalize`
   reads from the `RunResult`, not a backing variant. Callers never choose; the
   family does.

## Software Patterns Applied

| Pattern | Where | Why |
|---|---|---|
| **Strategy** | `ShellOperation` (each shell op a concrete strategy); mount via `run_mount` + a parse closure | New shell op = new impl, no central edit. Mount's two fixed ops are closures, not a trait (n=2, no central dispatch to extend). |
| **Template Method** | `engine.run_shell_interactive` / `run_mount` skeleton (reserve → begin → build request → spawn → `wait_completion` → finalize → resolve → terminal) | The invariant lifecycle lives once; ops fill only the varying steps. |
| **Bridge** | handle (`ExecutionHandle`/`InteractiveExecution`) ⟂ launcher (`NsRunnerLauncher::spawn_pty`/`spawn_piped` → `RunnerChild::wait_completion()`) | Swap fork ↔ persistent-server without touching any operation: completion is an *event* (`wait_completion`), not an exposed child pid. This is what makes the reverted server a drop-in future backend. |
| **Future / Promise** | `CompletionPromise<T>`, `ExecutionHandle::wait` | Every operation returns a typed completion handle; sync callers `.wait()`. The promise is the single internal "done?" truth — no parallel `FinalizationState`. |
| **Observer** | `ExecutionObserver` → `NamespaceExecutionLedger` | Decouples tracking/observability from the engine; a *pure projection* (no duplicated per-exec row); keeps the engine workspace-agnostic and preserves the observability surface. |
| **Composition** (not inheritance) | `InteractiveExecution<T>` *has-a* `ExecutionHandle<T>`; the handle's methods are inherent + forwarded | Rust's idiom for "is-a + extra capability"; no `Execution<T>` trait, no `Deref` polymorphism. |
| **Newtype delegation** | `CommandExecution` over `InteractiveExecution<CommandTerminalResult>` | Command-domain methods over the generic handle without leaking command types into the engine. |
| **Repository / Registry** | `ExecutionRegistry` (live + completed, keyed by `namespace_execution_id`) | One source of truth for in-flight/finished executions — the generalized role the per-command `CommandProcessStore` played; the command service holds no second map. |
| **Combinator** (deferred) | `ShellOp<O>` (Future Extensions) | A blanket `ShellOperation` so a shell wrapper is a command string + a parse closure. |

The spine is **Strategy + Template Method** (a generic engine parameterized by
operation strategies) with a **Bridge** at the launcher seam so the backing is
swappable.

## Resulting Model

New crate `sandbox-runtime-namespace-execution`, depending only on
`sandbox-runtime-namespace-process` (protocol). It is **workspace-agnostic**:
callers pass a plain `NamespaceTarget`, not a `WorkspaceEntry`, so the engine
sits **below** `workspace` and both `command` (above `workspace`) and `workspace`
itself can use it without a cycle.

```rust
pub struct NamespaceTarget {            // workspace identity; built once, reused across execs
    pub workspace_root: PathBuf,
    pub layer_paths: Vec<PathBuf>,
    pub upperdir: Option<PathBuf>,
    pub workdir: Option<PathBuf>,
    pub ns_fds: protocol::NsFds,        // reuse the runner-protocol type, not a redefinition
}                                       // no timeout: it is per-exec → it lives on the operation
```

### Handles and promise

```rust
pub struct NamespaceExecutionId(pub String);   // moved down from operation crate

pub struct ExecutionHandle<T> {
    id: NamespaceExecutionId,
    promise: CompletionPromise<T>,             // condvar-backed; the only "done?" truth
}

pub struct InteractiveExecution<T> {
    exec: ExecutionHandle<T>,                  // has-a (composition)
    pty: PtyMaster,                            // daemon-side master; slave is the child's stdio
}

// Inherent methods — there is no `Execution<T>` trait (no polymorphic call site).
impl<T> ExecutionHandle<T> {
    pub fn id(&self) -> &NamespaceExecutionId;
    pub fn is_finished(&self) -> bool;
    pub fn wait(self) -> Result<T, NamespaceExecutionError>;
    pub fn wait_timeout(&self, d: Duration) -> Option<&T>;
}

impl<T> InteractiveExecution<T> {
    pub fn execution(&self) -> &ExecutionHandle<T>;      // explicit, no Deref
    pub fn id(&self) -> &NamespaceExecutionId;           // forwards to self.exec
    pub fn is_finished(&self) -> bool;                   // forwards
    pub fn wait(self) -> Result<T, NamespaceExecutionError>;  // forwards
    pub fn wait_timeout(&self, d: Duration) -> Option<&T>;    // forwards
    pub fn write_stdin(&self, bytes: &[u8]) -> io::Result<()>;
    pub fn read_output_since(&self, off: u64) -> String;
    pub fn output_len(&self) -> u64;
    pub fn cancel(&self);                                // kill the child's process group
}
```

`CompletionPromise<T>` is `Mutex<Option<Result<T>>> + Condvar`. The watcher
thread resolves it once; `wait`/`wait_timeout` block on the condvar. It is the
single internal lifecycle truth, **replacing** the command path's separate
`FinalizationState` machine *and* its two 5 ms poll loops (`take_exit` polling +
`wait_for_completed_record` polling) with a blocking
`RunnerChild::wait_completion()` in the watcher and a condvar handoff. `cancel()`
is an independent `killpg(pgid)` from the caller thread, so it stays responsive
while the watcher blocks in `wait_completion()` (see "Finalization / Terminal
Semantics").

### The two families

Both families finalize from the **one** wire result the runner already emits —
`RunResult { exit_code, payload }` — wrapped as `RunnerOutcome`, so there is no
second outcome struct and no second status enum:

```rust
// One outcome type for both families (newtype over the wire RunResult).
pub struct RunnerOutcome(RunResult);
impl RunnerOutcome {
    pub fn status(&self) -> NamespaceExecutionTerminalStatus;  // parsed once, here
    pub fn exit_code(&self) -> i64;
    pub fn payload(&self) -> &serde_json::Value;
}

// Shell family (Run mode → shell_exec). One trait; no `InteractiveShellOperation` marker.
pub trait ShellOperation: Send + 'static {
    type Output: Send + 'static;
    fn operation_name(&self) -> &'static str;
    fn command(&self) -> &str;
    fn timeout_seconds(&self) -> Option<f64>;
    fn finalize(self: Box<Self>, outcome: RunnerOutcome)
        -> Result<Self::Output, NamespaceExecutionError>;
}

// Mount family: NO `MountOperation` trait. Two fixed `run_mount` call sites,
// each a (mode flag, parse closure) pair (see "The engine"):
//   run_mount("--mount-overlay",   target, id, |_| Ok(()))
//   run_mount("--remount-overlay", target, id, |o| Ok(RemountOverlayResult::from_payload(o.payload())))
```

`RunnerOutcome::status()` parses the runner status string once into
`NamespaceExecutionTerminalStatus` (the observable enum); the cancel override is
applied there (cancel is known engine-side). There is **no** `ShellOutcome`,
`ShellStatus`, `FinalizeCx`, `cwd()`/`env()` (no live producer), or
`MountOperation`. Interactive commands recover output from the PTY transcript; no
stdout capture is needed in the first cut (see "Future Extensions"). Mount
failure diagnostics ride in `RunResult.payload` — the daemon `MountOverlay` arm
writes its failure text there — so the 2-field `RunResult` absorbs both families.

### The engine

```rust
pub struct NamespaceExecutionEngine {
    registry: ExecutionRegistry,                 // live + completed, keyed by NamespaceExecutionId
    observer: Arc<dyn ExecutionObserver>,        // drives running/terminal; begin stays in operation layer
    launcher: NsRunnerLauncher,                  // concrete; pub(crate) seam (fake-able in tests)
    max_active: usize,
}

impl NamespaceExecutionEngine {
    pub fn run_shell_interactive<S: ShellOperation>(
        &self, op: S, target: NamespaceTarget, id: NamespaceExecutionId,
    ) -> Result<InteractiveExecution<S::Output>, NamespaceExecutionError>;

    pub fn run_mount<O: Send + 'static>(
        &self, mode_flag: &'static str, target: NamespaceTarget, id: NamespaceExecutionId,
        parse: impl FnOnce(RunnerOutcome) -> Result<O, NamespaceExecutionError> + Send + 'static,
    ) -> Result<ExecutionHandle<O>, NamespaceExecutionError>;
}

/// The one launcher seam (`pub(crate)`, held concretely on the engine — not a
/// public `Arc<dyn>`). Forks `current_exe ns-runner [--mode]` and wires
/// --request-fd/--result-fd. `RunnerChild::wait_completion()` is the single
/// completion event — fork `child.wait()` + result-fd read today, an `Exited`
/// frame for a future server. No start-ack; no result-fd reader thread.
pub(crate) struct NsRunnerLauncher { /* … */ }
impl NsRunnerLauncher {
    fn spawn_pty(&self, request: NamespaceRunnerRequest)
        -> Result<(RunnerChild, PtyMaster), NamespaceExecutionError>;   // interactive shell
    fn spawn_piped(&self, request: NamespaceRunnerRequest)
        -> Result<RunnerChild, NamespaceExecutionError>;                // mount/batch
}
```

Dispatch (the single Template-Method skeleton; interactive shell shown):

```text
1. registry.try_reserve()                         // admission (max_active); 1 lock
2. observer.on_running(id) is deferred; the operation layer already called
   ledger.begin(id, workspace_session_id, operation_name)  // ledger: Starting
3. request = NamespaceRunnerRequest from (target + op.command()/timeout_seconds() + id)
4. (child, master) = launcher.spawn_pty(request)            // fork; PTY pair; write request — NO start-ack
   spawn the PTY-output reader thread (drains the master)
5. registry.insert(id, live{ promise, child, pty_master })
6. observer.on_running(id)                                  // ledger: Running
7. start watcher thread (the only other thread):
     run_result = child.wait_completion()  // blocking wait + inline result-fd read; NO poll, NO reader thread
     outcome    = RunnerOutcome(run_result)
     result     = op.finalize(outcome)      // op policy; destroys one-shot via the op's own ws handle
     promise.resolve(result); registry.complete(id)         // 1 lock: live → completed
     observer.on_terminal(id, status, exit)                 // ledger: Terminal; 1 obs lock
8. return InteractiveExecution { exec{ id, promise }, pty: master }
```

`run_mount` is identical except `spawn_piped` (no PTY, no PTY-reader thread) and
the `parse` closure over `RunnerOutcome` in place of `op.finalize`. Sync
session-lifecycle callers immediately `.wait()`. Per exec: **2 threads**
(PTY reader + watcher), **0** daemon poll loops, **0** start-ack round-trips,
**~3** lock acquisitions.

### Command as the subtype

```rust
// sandbox-runtime-command
pub struct ExecCommand {
    pub command: String,
    pub timeout_seconds: Option<f64>,
    pub session_disposition: SessionDisposition,  // ExistingSession | OneShot { handler }
    pub workspace: WorkspaceSessionService,   // its own handle, for one-shot destroy in finalize
}
impl ShellOperation for ExecCommand {
    type Output = CommandTerminalResult;
    fn operation_name(&self) -> &'static str { "exec_command" }   // unchanged ledger name
    fn command(&self) -> &str { &self.command }
    fn timeout_seconds(&self) -> Option<f64> { self.timeout_seconds }
    fn finalize(self: Box<Self>, o: RunnerOutcome) -> Result<CommandTerminalResult, _> {
        // today's terminal_result(o.status(), o.exit_code()); destroy the one-shot
        // session via self.workspace — no engine-provided FinalizeCx.
    }
}
pub struct CommandExecution {                 // registry value; no `cwd`/`env`, no marker trait
    exec: InteractiveExecution<CommandTerminalResult>,
    next_snapshot_offset: u64,                // transcript cursor
    session_disposition: SessionDisposition,  // ExistingSession | OneShot { handler }
}
```

The command service holds `Arc<NamespaceExecutionEngine>` and looks up
`CommandExecution` **in the engine registry by `namespace_execution_id`** — there
is no second per-session map. It no longer owns spawn, promise, finalizer, or
`FinalizationState`. Overlay/remount (`workspace::namespace`) call
`engine.run_mount(flag, target, id, parse).wait()` at two sites, deleting
`run_child` and its wait/pipe helpers.

## Command Service Pseudocode

After the refactor, the three command APIs are thin orchestration over the
engine and its single registry. Error/cleanup plumbing is elided where noted;
behavior matches today.

```rust
fn exec_command(&self, input, trace) -> Result<CommandOutput> {
    if input.cmd.trim().is_empty() { return Err(InvalidCommand); }

    // Resolve existing session or create a one-shot; admission + remount guard.
    let (handler, session_disposition) = match input.workspace_session_id {
        Some(id) => (self.resolve_session(id)?, ExistingSession),
        None     => (self.create_one_shot_session()?, OneShot),
    };
    let _admit = self.begin_workspace_lifecycle_admission();
    self.ensure_not_remount_pending(&handler.workspace_session_id)?;

    let id = self.engine.allocate_id();                       // the one namespace_execution_id
    self.ledger.begin(id.clone(), handler.workspace_session_id, "exec_command"); // ledger: Starting

    let op = ExecCommand {
        command: input.cmd,
        timeout_seconds: input.timeout_ms.map(ms_to_s),
        session_disposition,
        workspace: self.workspace.clone(),                    // for one-shot destroy in finalize
    };
    let target = NamespaceTarget::from(handler.entry()?);     // From<WorkspaceEntry>
    let exec = match self.engine.run_shell_interactive(op, target, id.clone()) {
        Ok(exec) => exec,                                     // forks ns-runner, PTY, promise, watcher
        Err(e)   => { self.ledger.complete(&id, Error); self.cleanup_one_shot(session_disposition); return Err(e); }
    };

    // The CommandExecution (handle + transcript cursor + session disposition) lives in the
    // ENGINE REGISTRY keyed by id — no second per-session map.
    self.commands.insert(id.clone(), CommandExecution::new(exec, session_disposition));

    // Initial yield: settle-or-timeout (quiet-period UX), unchanged.
    self.wait_for_command_yield(CommandSessionId(id.0), input.yield_time_ms.unwrap_or(1000), 0, false)
}

fn write_command_stdin(&self, input) -> Result<CommandOutput> {
    let cmd = self.commands.live(&input.command_session_id)?; // CommandNotFound / AlreadyCompleted
    let start_offset = cmd.output_len();

    if is_kill_input(&input.stdin) {                          // Ctrl-C (\u{3}) / Ctrl-D (\u{4})
        cmd.cancel();                                         // InteractiveExecution::cancel → killpg(pgid)
        return self.wait_for_command_yield(input.command_session_id, 1000, start_offset, true);
    }

    self.ensure_not_remount_pending(&cmd.workspace_session_id)?;
    cmd.write_stdin(input.stdin.as_bytes())?;                 // InteractiveExecution::write_stdin
    self.wait_for_command_yield(input.command_session_id, input.yield_time_ms.unwrap_or(1000), start_offset, true)
}

fn read_command_lines(&self, input) -> Result<CommandOutput> {
    let start = input.start_offset.unwrap_or(0);
    let limit = validate_limit(input.limit.unwrap_or(200))?;  // 1..=1000

    if let Some(cmd) = self.commands.live_or_none(&input.command_session_id)? {
        let window = cmd.transcript_window(start, limit);     // PTY transcript, still streaming
        return Ok(command_output(window, Running, None, cmd.elapsed()));
    }
    // Terminal: resolved result + retained transcript from the engine registry.
    let done = self.commands.completed(&input.command_session_id)?;
    let window = transcript_window(done.transcript_path, start, limit)?;
    Ok(command_output(window, done.result.status, done.result.exit_code, done.elapsed()))
}
```

`self.commands` is a command-typed view over the engine's **single** registry
(live + completed), not a second store. `wait_for_command_yield` is unchanged in
spirit: it waits on the execution's promise (`wait_timeout`, a condvar — not a
5 ms poll) and re-checks transcript length only on each ~50 ms settle slice for
the quiet-period/yield-time UX, then renders a running or completed
`CommandOutput`. The completed branch no longer polls a `completed` map — it
reads the resolved promise/registry entry directly. `CommandOutput` is the one
output DTO (the former `CommandYield`/`CommandLinesOutput`/`CommandOutputSnapshot`,
merged); `CommandCompletionWaitOutcome` is gone (the branch is `is_finished()`).

## Finalization / Terminal Semantics

Unchanged in meaning, made uniform across both families:

1. **Trigger — the process is gone.** The forked `ns-runner` child exits only
   after the runner's scope-wait drained the command's process group (or
   `SIGKILL`ed it on timeout/cancel, `wait.rs::wait_for_command_execution_scope`).
   The watcher's blocking `RunnerChild::wait_completion()` returns the `RunResult`
   (a `child.wait()` + inline result-fd read on the fork backing). So when exit is
   observed, no process from that execution's group is alive, and the runner child
   itself is reaped.
2. **Completion — finalize runs inline.** On the watcher thread, before resolving
   the promise, the operation's `finalize` runs (record result; destroy one-shot
   session; parse report). It resolves the promise `Ok`, or on `finalize` error
   with a terminal error. Because finalize is inline, **promise-resolved ⟹ the
   completed registry entry exists** — which is what lets the yield path drop the
   former `wait_for_completed_record` poll loop.
3. **Cancel is independent of the watcher.** `cancel()` is a `killpg(pgid)` issued
   from the caller (`write_command_stdin`) thread; the child runs in its own
   process group, so a blocking `wait_completion()` on the watcher cannot delay
   it and the kill unblocks the `wait`. Cancel stays responsive with no polling.

So **terminal ⟹ no child/process-group alive, always.** The converse is not
instantaneous — child exit → engine still runs `finalize` before `is_finished()`
flips. A command whose caller never waits still goes terminal when its child
exits (the watcher owns it), exactly as today.

## CommandProcessStore Disposition

Verified field-by-field against current readers (every "DELETE" below is
confirmed write-only by `rg`):

| Field(s) | Today's owner | After |
|---|---|---|
| `namespace_execution_id`, `started_at`, `completion`, `finalization`, active/completed maps, admission | `CommandProcessStore` | **engine** registry + promise (the store is deleted). One `started_at`, on the engine entry. |
| `process` (PTY), transcript path | `ActiveCommandProcess` | engine `InteractiveExecution` (PTY master) + command transcript |
| `next_snapshot_offset`, `workspace_ownership` | `ActiveCommandProcess` | **command session view** on the registry-stored `CommandExecution` (`workspace_ownership` → `session_disposition`; no second map) |
| `lifecycle_state` (`CommandLifecycleState`), `cancellation` (`CancellationState`) | `ActiveCommandProcess` | **DELETE — write-only.** Lifecycle is the observer's `NamespaceExecutionLifecycle`; cancel is `cancel()` → `killpg`. |
| `remount_switch_state` | `ActiveCommandProcess` mirror | **DELETE — write-only.** The live copy is `CommandRemountQuiesce.switch_state` on the coordinator. |
| `remount_cancellation` | per-command mirror | **DELETE the mirror.** The coordinator owns one `RemountCancellationToken` + an affected-id set (it is read via `same_token`, so the token lives once, on the coordinator). |
| publish extras (`CommandFinalizedMetadata`/`CommandPublishFinalization`/`CommandPublishStatus`, top-level `finalized`/`signal`) | `CompletedCommandRecord` | **DELETE — always `None`/unread.** Publish returns as an op's `finalize` policy if/when it lands. |

`CommandTerminalResult` collapses to `{ status, exit_code, command_total_time_seconds }`
(no `stdout` — yields read the transcript; no `timed_out` — derivable from
`status`). `CompletedCommandRecord` collapses to `{ transcript_path,
next_snapshot_offset, result, started_at }`.

Irreducible: the protocol returns a `command_session_id` and later calls
`write_command_stdin(id)`/`read_command_lines(id)` across requests, so live +
completed command handles must be retained server-side by id. That is the engine
registry (keyed by `namespace_execution_id`), not a command-owned store.

Remount coordination (`workspace_remount/service/command/{coordinator,quiesce}.rs`)
asks the engine registry for live interactive executions in a workspace (via the
observer index → ids → `InteractiveExecution` pgid/cancel) instead of reaching
into `active.remount_*`. `command::process_group` inspection stays, embedded into
`CommandRemountInspection` (deleting the field-by-field `merge_report`).

## Observability Contract (unchanged)

```rust
pub trait ExecutionObserver: Send + Sync {
    fn on_running(&self, id: &NamespaceExecutionId);
    fn on_terminal(&self, id: &NamespaceExecutionId,
                   status: NamespaceExecutionTerminalStatus, exit_code: Option<i64>);
}
```

`begin` stays in the operation layer (it owns the `workspace_session_id`), as
`exec_command` already does (`begin_namespace_execution(id, { workspace_session_id,
operation_name })`); the engine only drives running/terminal by id, so it needs
no workspace knowledge (this is why `NamespaceTarget` carries no
`WorkspaceSessionId`). `operation::namespace_execution::NamespaceExecutionLedger`
(today's `NamespaceExecutionStore`, renamed) implements `ExecutionObserver` as a
**pure projection** — no duplicated per-execution row. Its record's
`origin_request_id` field (renamed from `request_id`) is the **external origin
request id**, deliberately distinct from `namespace_execution_id`. The observable
surface stays byte-for-byte unchanged: one `active_namespace_executions`
list, `operation_name = "exec_command"`, generic `Starting/Running/Terminal`,
**no** `execution_kind`/`backing` field. The backing, `ShellOperation`,
`RunnerOutcome`, and the registry are internal and not serialized.

## Crate / Dependency Graph

```text
overlay
namespace-process            (re-exec runner + protocol)                       [unchanged]
namespace-execution  ◄── NEW (engine; workspace-agnostic via NamespaceTarget)  → namespace-process
layerstack
workspace ───────────────────► namespace-process, namespace-execution
command   ───────────────────► namespace-execution, namespace-process, workspace
operation ───────────────────► command, workspace, namespace-execution, layerstack
```

## Decoupling `shell_exec` From Workspace

Today the daemon-side request building is bound to workspace types in two
separate places:

- `command/src/process.rs::build_namespace_runner_request(spec, WorkspaceEntry)`
  — the shell path consumes `WorkspaceEntry`;
- `workspace/src/namespace/setns_runner.rs::ns_runner_request(handle, …)` — the
  mount path has its own builder over `WorkspaceModeHandle`.

The engine breaks this with one boundary type:

```rust
// in the engine crate — no `workspace` dependency:
pub struct NamespaceTarget { workspace_root, layer_paths, upperdir, workdir, ns_fds }  // 5 fields; ns_fds: protocol::NsFds; timeout is per-exec, on the op

// in the workspace crate (depends on engine; WorkspaceEntry is local → orphan rule OK):
impl From<WorkspaceEntry> for NamespaceTarget { /* … */ }
```

Consequences:

- The engine and both operation families speak **only** `NamespaceTarget`. The
  engine crate has **zero** `workspace` dependency, so it sits *below*
  `workspace`; `command` (above) and `workspace` (overlay/remount) both use it
  with no cycle.
- `shell_exec` request construction no longer references workspace sessions. The
  in-namespace `shell_exec` (`runner/shell_exec.rs`) was already
  workspace-agnostic (it takes a `NamespaceRunnerRequest`); this completes the
  decoupling on the daemon side and deletes the duplicate mount builder.
- Workspace *identity* is also kept out of the engine: `begin` (which needs
  `WorkspaceSessionId`) stays in the operation layer; the engine's
  `ExecutionObserver` only drives `running`/`terminal` by id. The engine never
  sees a `WorkspaceSessionId`.

## Resulting File Tree & LOC

Legend: `← NEW` new file · `✗ DELETE` file removed · `△` edited in place ·
`[kept]` untouched. Numbers left of `→` are **measured** current LOC (`wc -l`,
2026-06-24); numbers right of `→`, the per-file engine-crate sizes, and the
deltas marked `≈` are **estimates**.

```text
crates/sandbox-runtime/
  namespace-process/                            [unchanged: in-namespace re-exec runner + protocol]
    src/runner/{mod,setns,shell_exec,protocol}.rs   (+ shell_exec/{request,wait}.rs)

  namespace-execution/                          ← NEW crate (engine; workspace-agnostic)
    src/lib.rs                re-exports                                            ~20
    src/id.rs                 NamespaceExecutionId (moved from operation)           ~25
    src/error.rs              NamespaceExecutionError                               ~30
    src/target.rs             NamespaceTarget (5 fields; ns_fds: protocol::NsFds)   ~30
    src/promise.rs            CompletionPromise<T> (Mutex<Option<Result>> + Condvar) ~70
    src/execution.rs          ExecutionHandle<T>, InteractiveExecution<T>           ~150
                                (inherent methods — NO Execution<T> trait)
    src/shell.rs              ShellOperation, RunnerOutcome(RunResult)              ~70
    src/observer.rs           ExecutionObserver                                     ~30
    src/registry.rs           live + completed by id, admission                     ~120
    src/engine.rs             run_shell_interactive, run_mount(closure), watcher                ~180
    src/launcher.rs           pub(crate) NsRunnerLauncher::spawn_pty/spawn_piped    ~180
                                (unifies spawn_current_exe_ns_runner + run_child;
                                 NO start-ack pipe, NO result-fd reader thread)
    src/pty.rs                PtyMaster + transcript reader (from command/pty.rs)   ~120
                                                                    engine crate ≈ +1,025
    (NO mount.rs · NO Backing / NsRunnerMode / FinalizeCx / ShellOutcome / ShellStatus / Execution<T>)

  command/
    src/{lib,config,transcript,transcript_rows,process_group}.rs                    [kept]
    src/contract.rs           △ CommandTerminalResult → {status,exit_code,total_time}  46 → ~38   −8
    src/exec.rs               ← NEW  ExecCommand : ShellOperation                                +40
    src/command_execution.rs  ← NEW  CommandExecution (handle + cursor + session disposition)    +120
    src/process.rs            ✗ DELETE  (request build / run-result → engine)          336        −336
    src/pty.rs                ✗ DELETE  (PtyMaster+transcript → engine crate;           513        −513
                                spawn / start-ack / result-reader cut)

  workspace/
    src/namespace/setns_runner.rs  △ run_child/wait/pipe/builder → 2× run_mount       347 → ~150  −197
    src/model.rs              △ + impl From<WorkspaceEntry> for NamespaceTarget         507 → ~525  +18
    (NO namespace/ops.rs — mount is two run_mount closures, not a MountOperation trait)

  operation/src/command/
    service/core.rs           △ hold engine + registry view; drop store/sender        223 → ~175  −48
    service/contract.rs       △ merge CommandYield+CommandLinesOutput+CommandOutputSnapshot
                                → one CommandOutput; drop CommandCompletionWaitOutcome  108 → ~70   −38
    service/helpers.rs        △ drop both poll loops + the wait-outcome match          179 → ~135  −44
    service/impls/exec_command.rs         △ via engine + registry                      373 → ~180  −193
    service/impls/write_command_stdin.rs  △ via registry; drop cancellation write       57 → ~44   −13
    service/impls/read_command_lines.rs   △ via registry                                62 → ~52   −10
    service/process_store.rs  ✗ DELETE                                                 382         −382
    service/completion.rs     ✗ DELETE                                                 241         −241
    service/finalize.rs       ✗ DELETE                                                 275         −275
    service/launch.rs         ✗ DELETE                                                  74         −74
    service/status_lookup.rs  ✗ DELETE                                                  50         −50

  operation/src/namespace_execution.rs  △ → NamespaceExecutionLedger : ExecutionObserver;
                                request_id field → origin_request_id                   423 → ~415  −8

  operation/src/workspace_remount/service/command/
    coordinator.rs            △ own RemountCancellationToken + affected-id set           98 → ~95   −3
    quiesce.rs                △ embed ProcessGroupInspection; delete merge_report       229 → ~205  −24

  daemon/src/runner.rs        △ drop --start-ack-fd / wait_for_start_ack;
                                MountOverlay failure → payload; rename dispatch param   215 → ~190  −25
```

LOC accounting (deletes are exact; shrinks/new are estimates):

| Bucket | LOC |
|---|---|
| **Deleted outright** — whole files removed: `process_store` 382 · `completion` 241 · `finalize` 275 · `launch` 74 · `status_lookup` 50 · `command/process.rs` 336 · `command/pty.rs` 513 | **−1,871** |
| **Shrunk in place** — `setns_runner` −197 · `exec_command` −193 · `helpers` −44 · `core` −48 · `operation/contract` −38 · `write_stdin` −13 · `read_lines` −10 · `command/contract` −8 · `namespace_execution` −8 · `quiesce` −24 · `coordinator` −3 · `daemon/runner` −25 | **≈ −611** |
| **Gross removed from existing crates** | **≈ −2,482** |
| New engine crate (≈ 700 relocated from `pty`/`process`/`setns_runner`/gutted store + ≈ 325 new) | **+1,025** |
| New `command/src/exec.rs` + `command/src/command_execution.rs` | **+160** |
| `workspace/src/model.rs` `From<WorkspaceEntry>` impl | **+18** |
| **Net repo delta** | **≈ −1,280** (range −1,150 … −1,350) |
| **Marginal cost of the *next* ns operation** | ~700 (today) → **~30–80** |

`command/src/pty.rs` is counted as deleted (−513) because its surviving logic is
*relocated* into the engine crate (≈ 120 in `pty.rs` + the spawn/PTY/result-fd
path in `launcher.rs`), already included in the engine crate's +1,025 — so the
relocation is not double-counted. The net deletion (≈ −1,280) is real but
secondary; the load-bearing number is the last row — a new namespace operation
becomes an operation impl, not a parallel fork/promise/finalize/store stack. That
"~30–80" holds for operations whose output is in the PTY transcript or
`RunResult.payload`; a stdout-parsing shell op additionally needs the one-time,
additive `RunResult.payload` capture (no `finalize`-signature change, given
`RunnerOutcome`).

## File Plan

### New crate `crates/sandbox-runtime/namespace-execution`

- `Cargo.toml` — deps: `sandbox-runtime-namespace-process`, `serde`,
  `serde_json`, `rustix`/`nix`, `libc`.
- `src/lib.rs` — re-exports.
- `src/id.rs` — `NamespaceExecutionId` (moved from `operation`; `operation`
  re-exports for back-compat).
- `src/error.rs` — `NamespaceExecutionError`.
- `src/target.rs` — `NamespaceTarget` (5 fields; `ns_fds: protocol::NsFds`). No
  `Backing` — the backing is implicit in which launcher method is called.
- `src/promise.rs` — `CompletionPromise<T>` (condvar).
- `src/execution.rs` — `ExecutionHandle<T>`, `InteractiveExecution<T>` (inherent
  methods + forwarding; no `Execution<T>` trait).
- `src/shell.rs` — `ShellOperation`, `RunnerOutcome` (newtype over `RunResult`).
  No `InteractiveShellOperation`, `ShellOutcome`, `ShellStatus`, or `FinalizeCx`.
- `src/observer.rs` — `ExecutionObserver`.
- `src/registry.rs` — live+completed registry, admission.
- `src/engine.rs` — `NamespaceExecutionEngine`: `run_shell_interactive`, `run_mount`
  (mode-flag + parse closure; no `MountOperation` trait, no `NsRunnerMode` enum),
  dispatch skeleton, watcher.
- `src/launcher.rs` — `pub(crate) NsRunnerLauncher` with `spawn_pty`/`spawn_piped`
  (fork `current_exe ns-runner [--mode]`, request/result fds — no start-ack, no
  result-fd reader thread), unifying `spawn_current_exe_ns_runner` + `run_child`;
  `RunnerChild::wait_completion()` is the completion event.
- `src/pty.rs` — `PtyMaster` + transcript reader (moved from `command/src/pty.rs`).

### `crates/sandbox-runtime/command`

- `src/exec.rs` (new) — `ExecCommand: ShellOperation` (holds its own
  `WorkspaceSessionService` handle for the one-shot destroy in `finalize`).
- `src/command_execution.rs` (new) — `CommandExecution` (handle + transcript
  cursor + session disposition) + transcript/yield helpers.
- `src/contract.rs` — `CommandTerminalResult = { status, exit_code,
  command_total_time_seconds }` (drop `stdout`/`timed_out`); built from
  `RunnerOutcome`.
- keep `transcript.rs`, `transcript_rows.rs`, `process_group.rs`, `config.rs`.
- **Delete** `src/process.rs` and `src/pty.rs` (spawn/PTY/result-fd → engine
  `launcher.rs`; `PtyMaster`/transcript reader → engine `pty.rs`).

### `crates/sandbox-runtime/workspace`

- `src/namespace/setns_runner.rs` — replace `run_child`/`wait_for_child`/
  `terminate_child`/`read_pipe`/`ns_runner_request` with two
  `engine.run_mount(flag, target, id, parse).wait()` call sites
  (`"--mount-overlay"` → `Ok(())`; `"--remount-overlay"` →
  `RemountOverlayResult::from_payload`). No `MountOperation` trait, no `ops.rs`.
- `src/model.rs` — `impl From<WorkspaceEntry> for NamespaceTarget` (orphan-rule
  OK: `WorkspaceEntry` is local).

### `crates/sandbox-runtime/operation/src/command`

- `service/core.rs` — hold `Arc<NamespaceExecutionEngine>`; reach
  `CommandExecution` through the engine registry (no second map); drop
  `process_store` + `completion_sender`.
- `service/impls/exec_command.rs` — allocate id, `ledger.begin(..)`, build
  `ExecCommand`, `engine.run_shell_interactive(.., id)`, initial yield.
- `service/impls/write_command_stdin.rs` / `read_command_lines.rs` — via the
  engine registry; `write_command_stdin` drops the `cancellation` write.
- `service/contract.rs` — merge `CommandYield`/`CommandLinesOutput`/
  `CommandOutputSnapshot` into one `CommandOutput`; delete
  `CommandCompletionWaitOutcome`.
- `service/helpers.rs` — drop both poll loops and the wait-outcome match (yield
  via the promise + a ~50 ms transcript re-check).
- **Delete** `service/process_store.rs`, `service/completion.rs`,
  `service/launch.rs`, `service/finalize.rs`, `service/status_lookup.rs`.
- `namespace_execution.rs` — rename `NamespaceExecutionStore` →
  `NamespaceExecutionLedger`; implement `ExecutionObserver`; field `request_id`
  → `origin_request_id`; observability shape otherwise unchanged.

### `crates/sandbox-runtime/operation/src/workspace_remount/service/command`

- `coordinator.rs` / `quiesce.rs` — query the engine registry; the coordinator
  owns one `RemountCancellationToken` + affected-id set (delete the per-command
  `remount_cancellation`/`remount_switch_state` mirrors); embed
  `ProcessGroupInspection` into `CommandRemountInspection` and delete
  `merge_report`.

## Migration Sequencing (each phase stays green)

1. **Create the crate; move `NamespaceExecutionId` down** (`operation`
   re-exports). Add target/promise/handles/`ShellOperation`+`RunnerOutcome`/
   observer/error/registry, no wiring. *Mechanical.*
2. **`NsRunnerLauncher` + engine dispatch + watcher**, with a fake launcher (and
   fake `RunnerChild`) for tests. Unit-test the engine end to end (no
   command/workspace changes).
3. **`ExecCommand` + `CommandExecution`**; migrate `exec_command` /
   `write_command_stdin` / `read_command_lines` onto the engine registry; wire
   `ExecutionObserver` (`NamespaceExecutionStore` → `NamespaceExecutionLedger`,
   `request_id` → `origin_request_id`). Delete
   `process_store`/`completion`/`launch`/`finalize`/`status_lookup` and the
   write-only `CommandLifecycleState`/`cancellation`; merge the output DTOs into
   `CommandOutput`; remove `spawn_current_exe_ns_runner` from the command path.
4. **Mount family**: route overlay/remount through two
   `engine.run_mount(flag, target, id, parse)` call sites; delete `run_child`,
   its wait/pipe helpers, and the duplicate `ns_runner_request` builder.
5. **Remount coordinator** off `active.remount_*` onto engine queries + one
   coordinator-owned `RemountCancellationToken` + id-set; embed
   `ProcessGroupInspection`, delete `merge_report`.
6. **Cleanup**: delete dead `command/src/process.rs` + `command/src/pty.rs`
   (relocated into the engine); drop the daemon `--start-ack-fd` plumbing;
   confirm the `ns-runner` re-exec has a single daemon-side launcher (the engine).

## Future Extensions (explicitly deferred)

- **`ShellOp<O>` Tier-2 combinator** — a blanket `ShellOperation` for "run a
  command, parse stdout → `O`"; a new shell op becomes a command string + a parse
  closure, returning a plain `ExecutionHandle<O>` over a piped (`spawn_piped`)
  backing. Requires stdout capture: have the runner add captured output to
  `RunResult.payload`. Because `finalize` already takes `RunnerOutcome` (over
  `RunResult`), this is **purely additive — no `finalize`-signature change**.
  Land with the first non-command shell producer; reassess the "second
  classification axis" note then (still prefer distinct `operation_name`s).
- **Swappable launcher backend** — if a persistent per-session runner returns, it
  implements the `NsRunnerLauncher` seam (its `RunnerChild::wait_completion()`
  blocks on an `Exited` frame instead of `child.wait()`) without touching
  command, mount, the promise, the registry, or tracking. The seam exists for
  exactly this.

## Test Plan

`crates/sandbox-runtime/namespace-execution`:

- Engine dispatch against a fake `NsRunnerLauncher` (fake `RunnerChild`):
  `wait_completion()` returns a `RunResult` → promise resolves with the finalized
  `Output`.
- `finalize` error → promise resolves with a terminal error.
- `CompletionPromise::wait_timeout` blocks then returns on resolve (no poll).
- `cancel()` (`killpg`) is responsive while the watcher blocks in
  `wait_completion()`.
- Admission limit rejects past `max_active`.
- `run_mount(flag, target, id, parse)` resolves the parsed `Output`; sync
  `.wait()` path works.
- `namespace_execution_id` is the runner `request_id` and the registry key; the
  observer record's `origin_request_id` stays distinct.

`crates/sandbox-runtime/operation` (command):

- `exec_command` initial yield; long-running command yields a
  `command_session_id`; `write_command_stdin`/`read_command_lines` via the engine
  registry.
- One-shot workspace destroyed in `ExecCommand::finalize`.
- Observability: running command appears once in `active_namespace_executions`,
  `operation_name = "exec_command"`; no `active_executions`/`active_commands`
  lane; no `execution_kind`/`backing` field (observability invariants).
- Remount quiesce/resume still cancels/holds live commands after the coordinator
  change.

`crates/sandbox-runtime/workspace`:

- Overlay mount and live remount succeed through `engine.run_mount`; remount
  verification report is parsed; failure surfaces as a terminal error.

## Verification Commands

```sh
cargo fmt --check
cargo check -p sandbox-runtime-namespace-execution --tests
cargo test  -p sandbox-runtime-namespace-execution
cargo check -p sandbox-runtime-command --tests
cargo check -p sandbox-runtime-workspace --tests
cargo test  -p sandbox-runtime --tests
cargo test  -p sandbox-runtime observability
cargo clippy -p sandbox-runtime-namespace-execution --all-targets --no-deps -- -D warnings
cargo clippy -p sandbox-runtime-command            --all-targets --no-deps -- -D warnings
cargo clippy -p sandbox-runtime-workspace          --all-targets --no-deps -- -D warnings
# the two fork sites are gone, replaced by the engine:
rg -n "spawn_current_exe_ns_runner" crates/sandbox-runtime/command/src
rg -n "fn run_child"               crates/sandbox-runtime/workspace/src/namespace/setns_runner.rs
# gutted store and satellites:
rg -n "CommandProcessStore|FinalizationState|spawn_completion_finalizer|CommandLaunchDriver" crates/sandbox-runtime/operation/src/command
# observability axis unchanged:
rg -n "execution_kind|namespace_execution_kind|runner_kind|execution_scope|active_executions|active_commands" crates/sandbox-runtime/operation/src
git diff --check
```

## Completion Checklist

- [ ] `sandbox-runtime-namespace-execution` crate exists; `NamespaceExecutionId`
      moved down with an `operation` re-export; engine is workspace-agnostic
      (`NamespaceTarget`, sits below `workspace`).
- [ ] `NsRunnerLauncher` (`pub(crate)`, `spawn_pty`/`spawn_piped`) is the single
      daemon-side `ns-runner` launcher; `spawn_current_exe_ns_runner` and
      `run_child` are gone; no start-ack pipe; no result-fd reader thread.
- [ ] `CompletionPromise<T>` is condvar-backed; **zero** daemon-side poll loops;
      completion is a blocking `RunnerChild::wait_completion()`.
- [ ] `ExecutionHandle<T>`/`InteractiveExecution<T>` realize the subtype by
      composition with inherent + forwarded methods (no `Execution<T>` trait, no
      `Deref` polymorphism).
- [ ] One shell trait (`ShellOperation`); mount is `run_mount` + a parse closure
      (no `MountOperation` trait); no single trait unions shell and mount.
- [ ] `ExecCommand: ShellOperation` (no marker trait); command runs through the
      engine; one `RunnerOutcome`/`NamespaceExecutionTerminalStatus` on the path.
- [ ] Overlay/remount run through two `engine.run_mount` call sites; tracked +
      promised; `run_child` and `ns_runner_request` deleted.
- [ ] `CommandProcessStore`, `completion.rs`, `launch.rs`, `finalize.rs`,
      `status_lookup.rs`, `command/src/process.rs`, `command/src/pty.rs` deleted;
      write-only `CommandLifecycleState`/`cancellation`/`remount_switch_state` and
      the `CommandFinalizedMetadata` publish family deleted, not migrated.
- [ ] One id space (`namespace_execution_id`); `cmd_N`/`isolated-…` gone; observer
      `origin_request_id` distinct. One lifecycle enum; one `started_at`; one
      `CommandOutput` DTO.
- [ ] Remount coordinator owns the `RemountCancellationToken` + id-set;
      `ProcessGroupInspection` embedded, `merge_report` gone; quiesce/resume works.
- [ ] `ExecutionObserver` feeds `NamespaceExecutionLedger`; observability
      surface unchanged (no `execution_kind`/`backing` field).
- [ ] Backing is internal/binary (PTY vs pipe = `spawn_pty`/`spawn_piped`); no
      `Backing` enum; not serialized; no `Captured`/`Report` types.
- [ ] `ShellOp<O>` + stdout capture documented as future (additive
      `RunResult.payload`), not implemented.
```
