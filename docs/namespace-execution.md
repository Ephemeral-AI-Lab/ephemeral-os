# Namespace Execution Engine

## Purpose

Give the runtime **one** daemon-side substrate for "work dispatched into a
workspace's namespaces via the `ns-runner` re-exec," with a typed completion
promise, generic lifecycle tracking, and a single cancellation/finalization
path. Make `exec_command` the first *subtype* of that substrate rather than a
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
Phase 4.6 observability contract (one `active_namespace_executions` list,
`operation_name` as the only classification axis, no `execution_kind`/substrate
field) is preserved exactly — see "Observability Contract."

## Current Architecture Context

### The substrate is the `ns-runner` re-exec

The persistent per-session runner server (`runner/server/`, the
`ns-runner-server` subcommand) is being **reverted**. This spec therefore
targets the stable re-exec substrate, not the server:

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

The engine deliberately puts the spawn behind one seam (`NsRunnerInvocation`).
If a persistent runner server returns later, only that seam changes; command,
mount, the promise, the registry, and tracking stay identical. Decoupling the
operations from *how* the runner is launched is a primary goal, not a
side effect.

### Phase 4.6 boundary

`docs/observability/phase-4-6-mechanical-namespace-execution-unification.md`
(implemented) keeps `CommandProcessStore`/`command_session_id` as
command-domain concepts, keeps `operation_name` as the only namespace-execution
classification axis, and **defers** any generic `execution_kind`/substrate
field "until a second live producer makes it necessary."

This spec respects that. The engine is an **internal** mechanism that adds no
public classification axis; `operation_name` stays the observability axis. The
internal generalization (a shared invocation/promise/registry/finalizer) is
justified by the duplication above: building it once is strictly less code than
maintaining two fork paths plus a third for the next operation.

## Architecture Decision

Introduce a daemon-side **namespace execution engine** over the `ns-runner`
re-exec, and re-express both command and overlay/remount on top of it.

1. **One generic core, two operation families.** A core that builds a request,
   forks the runner behind `NsRunnerInvocation`, and resolves a typed promise —
   knowing nothing about shell vs. mount. On top, two disjoint families (no
   single trait unions them):
   - **Shell family** (`Run` mode → `shell_exec`): `ExecCommand`; later a
     `ShellOp<O>` combinator.
   - **Mount family** (`MountOverlay`/`RemountOverlay` modes → mount syscalls):
     `MountOverlayOp`, `RemountOverlayOp`.

2. **Command is a subtype, by composition — not inheritance.** Rust has no
   inheritance; the relationship is composition plus a shared trait:

   ```text
   NamespaceExecution<T>          genus: id + completion promise + tracking
     └─ InteractiveExecution<T>   = NamespaceExecution<T> + a PTY (stdin/stream/cancel)
          └─ CommandExecution     = InteractiveExecution<CommandTerminalResult> + command UX
   ```

   Both `NamespaceExecution<T>` and `InteractiveExecution<T>` implement a shared
   `Execution<T>` trait; `InteractiveExecution<T>` *contains* a
   `NamespaceExecution<T>` and adds capability. No `Deref` polymorphism.

3. **Promise for every operation; sync callers just `wait()`.** Commands return
   an `InteractiveExecution<T>` and yield incrementally; overlay/remount return a
   `NamespaceExecution<T>` and call `.wait()` at the session-lifecycle call site
   (their current blocking behavior, now promised + tracked).

4. **Unify identity.** `namespace_execution_id` is the one id; `CommandSessionId`
   is its public face for the command API. It is the runner `request_id` and the
   registry key.

5. **Gut `CommandProcessStore`.** Its generic ~60% (active/completed maps,
   admission, the `FinalizationState` machine, the completion promise) becomes
   the engine registry. What remains is a thin command session view (transcript
   cursor, workspace ownership, cancellation); remount scratch moves into the
   remount coordinator. See "CommandProcessStore Disposition."

6. **Substrate is binary and internal.** `Backing ∈ { Pty, Pipe }` — no
   `Captured`/`Report` taxonomy. Result shape is what an operation's `finalize`
   reads from the `RunResult`, not a substrate variant. Callers never choose a
   backing; the family does (interactive shell → `Pty`; mount/batch → `Pipe`).

## Software Patterns Applied

| Pattern | Where | Why |
|---|---|---|
| **Strategy** | `ShellOperation` / `MountOperation` (each op a concrete strategy) | New op = new impl, no central edit. Open for extension (vs. an `enum` of modes every op must touch). |
| **Template Method** | `engine.run_shell_interactive` / `run_mount` skeleton (reserve → begin → build request → spawn → run → finalize → resolve → terminal) | The invariant lifecycle lives once; ops fill only the varying steps via trait methods. |
| **Bridge** | handle abstraction (`NamespaceExecution`/`InteractiveExecution`) ⟂ spawn implementation (`NsRunnerInvocation`) ⟂ `Backing` | Swap fork ↔ persistent-server, or Pty ↔ Pipe, without touching any operation. This is what makes the reverted server a drop-in future backend. |
| **Future / Promise** | `CompletionPromise<T>`, `Execution::wait` | Every operation returns a typed completion handle; sync callers `.wait()`. |
| **Observer** | `ExecutionObserver` → `NamespaceExecutionStore` | Decouples tracking/observability from the engine; keeps the engine workspace-agnostic and preserves the Phase 4.6 surface. |
| **Composition + shared trait** (not inheritance) | `InteractiveExecution<T>` *has-a* `NamespaceExecution<T>`; both impl `Execution<T>` | Rust's idiom for "is-a + extra capability"; avoids `Deref` polymorphism. |
| **Newtype delegation** | `CommandExecution(InteractiveExecution<CommandTerminalResult>)` | Command-domain methods over the generic handle without leaking command types into the engine. |
| **Repository / Registry** | `ExecutionRegistry` (live + completed, keyed by `namespace_execution_id`) | One source of truth for in-flight/finished executions — the generalized role the per-command `CommandProcessStore` played. |
| **Combinator** (deferred) | `ShellOp<O>` (Future Extensions) | A blanket `ShellOperation` so a shell wrapper is a command string + a parse closure. |

The spine is **Strategy + Template Method** (a generic engine parameterized by
operation strategies) with a **Bridge** at the spawn seam so the substrate is
swappable.

## Resulting Model

New crate `sandbox-runtime-namespace-execution`, depending only on
`sandbox-runtime-namespace-process` (protocol). It is **workspace-agnostic**:
callers pass a plain `NamespaceTarget`, not a `WorkspaceEntry`, so the engine
sits **below** `workspace` and both `command` (above `workspace`) and `workspace`
itself can use it without a cycle.

```rust
pub struct NamespaceTarget {            // built from WorkspaceEntry at the call site
    pub workspace_root: PathBuf,
    pub layer_paths: Vec<PathBuf>,
    pub upperdir: Option<PathBuf>,
    pub workdir: Option<PathBuf>,
    pub ns_fds: NsFds,
    pub timeout_seconds: Option<f64>,
}
```

### Handles and promise

```rust
pub struct NamespaceExecutionId(pub String);   // moved down from operation crate

pub struct NamespaceExecution<T> {
    id: NamespaceExecutionId,
    promise: CompletionPromise<T>,             // condvar-backed
}

pub struct InteractiveExecution<T> {
    exec: NamespaceExecution<T>,               // has-a (composition)
    pty: PtyMaster,                            // daemon-side master; slave is the child's stdio
}

pub trait Execution<T> {
    fn id(&self) -> &NamespaceExecutionId;
    fn is_finished(&self) -> bool;
    fn wait(self) -> Result<T, NamespaceExecutionError>;
    fn wait_timeout(&self, d: Duration) -> Option<&T>;
}
impl<T> Execution<T> for NamespaceExecution<T> { /* … */ }
impl<T> Execution<T> for InteractiveExecution<T> { /* delegates to self.exec */ }

impl<T> InteractiveExecution<T> {
    pub fn execution(&self) -> &NamespaceExecution<T>;   // explicit, no Deref
    pub fn write_stdin(&self, bytes: &[u8]) -> io::Result<()>;
    pub fn read_output_since(&self, off: u64) -> String;
    pub fn output_len(&self) -> u64;
    pub fn cancel(&self);                                 // kill the child's process group
}
```

`CompletionPromise<T>` is `Mutex<Option<Result<T>>> + Condvar`. The watcher
thread resolves it once; `wait`/`wait_timeout` block on the condvar. This
replaces the command path's two 5 ms poll loops (`take_exit` polling +
`wait_for_completed_record` polling) with a blocking `child.wait()` in the
watcher and a condvar handoff.

### The two families

```rust
// Shell family (Run mode → shell_exec)
pub trait ShellOperation: Send + 'static {
    type Output: Send + 'static;
    fn operation_name(&self) -> &'static str;
    fn command(&self) -> &str;
    fn cwd(&self) -> Option<&str>;
    fn env(&self) -> serde_json::Value;
    fn timeout_seconds(&self) -> Option<f64>;
    fn finalize(self: Box<Self>, outcome: ShellOutcome, cx: FinalizeCx<'_>)
        -> Result<Self::Output, NamespaceExecutionError>;
}
pub trait InteractiveShellOperation: ShellOperation {}   // marker → Backing::Pty
pub struct ShellOutcome { pub exit_code: i64, pub status: ShellStatus, pub timed_out: bool }

// Mount family (MountOverlay / RemountOverlay modes → mount syscalls)
pub trait MountOperation: Send + 'static {
    type Output: Send + 'static;
    fn operation_name(&self) -> &'static str;
    fn mode(&self) -> NsRunnerMode;            // MountOverlay | RemountOverlay
    fn mount_args(&self) -> serde_json::Value; // {} | {probe_path, probe_content}
    fn parse_report(report: serde_json::Value) -> Result<Self::Output, NamespaceExecutionError>;
}
```

`ShellOutcome` is built from the runner's `RunResult.payload` (`{success, status}`)
plus exit code. Interactive commands recover output from the PTY transcript;
no stdout capture is needed in the first cut (see "Future Extensions").

### The engine

```rust
pub struct NamespaceExecutionEngine {
    registry: ExecutionRegistry,                 // live + completed, keyed by NamespaceExecutionId
    observer: Arc<dyn ExecutionObserver>,        // drives running/terminal; begin stays in operation layer
    invocation: Arc<dyn NsRunnerInvocation>,     // real = fork ns-runner; fake in tests
    max_active: usize,
}

impl NamespaceExecutionEngine {
    pub fn run_shell_interactive<S: InteractiveShellOperation>(
        &self, op: S, target: NamespaceTarget, id: NamespaceExecutionId,
    ) -> Result<InteractiveExecution<S::Output>, NamespaceExecutionError>;

    pub fn run_mount<M: MountOperation>(
        &self, op: M, target: NamespaceTarget, id: NamespaceExecutionId,
    ) -> Result<NamespaceExecution<M::Output>, NamespaceExecutionError>;
}

/// The one spawn seam. Real impl forks `current_exe ns-runner [--mode]`, wires
/// --request-fd/--result-fd/--start-ack, and (for Pty) opens a PTY pair.
pub trait NsRunnerInvocation: Send + Sync {
    fn spawn(&self, request: NamespaceRunnerRequest, backing: Backing)
        -> Result<RunnerChild, NamespaceExecutionError>;
}
enum Backing { Pty, Pipe }
```

Dispatch (the single Template-Method skeleton; interactive shell shown):

```text
1. registry.try_reserve()                         // admission (max_active)
2. observer.on_running(id) is deferred; the operation layer already called
   store.begin(id, workspace_session_id, operation_name)   // Phase 4.6 ledger: Starting
3. request = NamespaceRunnerRequest from op.mode()/args()/target (request_id = id)
4. child = invocation.spawn(request, Backing::Pty)          // fork; PTY master/slave; result-fd reader; start-ack
5. registry.insert(id, live{ promise, child, pty_master })
6. release start-ack; observer.on_running(id)               // ledger: Running
7. start watcher thread:
     child.wait()            (blocking — no poll)
     run_result = read result-fd
     outcome    = ShellOutcome::from(run_result)
     result     = op.finalize(outcome, cx)                  // op-specific policy
     promise.resolve(result); registry.complete(id)
     observer.on_terminal(id, status, exit)                 // ledger: Terminal
8. return InteractiveExecution { exec{ id, promise }, pty: master }
```

`run_mount` is identical except `Backing::Pipe`, no PTY, and `parse_report` over
`RunResult.payload`. Sync session-lifecycle callers immediately `.wait()`.

### Command as the subtype

```rust
// sandbox-runtime-command
pub struct ExecCommand {
    pub command: String, pub cwd: Option<String>, pub env: serde_json::Value,
    pub timeout_seconds: Option<f64>, pub ownership: CommandWorkspaceOwnership,
}
impl ShellOperation for ExecCommand {
    type Output = CommandTerminalResult;
    fn operation_name(&self) -> &'static str { "exec_command" }   // unchanged ledger name
    fn finalize(self: Box<Self>, o: ShellOutcome, cx: FinalizeCx<'_>) -> Result<_, _> {
        // today's terminal_result(..) + apply_workspace_completion_policy(..)
        // (destroy one-shot session; later publish/discard)
    }
}
impl InteractiveShellOperation for ExecCommand {}
pub struct CommandExecution(InteractiveExecution<CommandTerminalResult>);  // + transcript/yield helpers
```

The command service holds `Arc<NamespaceExecutionEngine>` and a thin per-session
index of live `CommandExecution`s (for `command_session_id` lookup). It no longer
owns spawn, promise, finalizer, or `FinalizationState`. Overlay/remount
(`workspace::namespace`) build `MountOverlayOp`/`RemountOverlayOp` and call
`engine.run_mount(..).wait()`, deleting `run_child`.

## Command Service Pseudocode

After the refactor, the three command APIs are thin orchestration over the
engine + the per-session `CommandExecution` index. Error/cleanup plumbing is
elided where noted; behavior matches today.

```rust
fn exec_command(&self, input, trace) -> Result<CommandYield> {
    if input.cmd.trim().is_empty() { return Err(InvalidCommand); }

    // Resolve existing session or create a one-shot; admission + remount guard.
    let (handler, ownership) = match input.workspace_session_id {
        Some(id) => (self.resolve_session(id)?, ExistingSession),
        None     => (self.create_one_shot_session()?, OneShot),
    };
    let _admit = self.begin_workspace_lifecycle_admission();
    self.ensure_not_remount_pending(&handler.workspace_session_id)?;

    let id = self.engine.allocate_id();                       // namespace_execution_id
    self.store.begin(id.clone(), handler.workspace_session_id, "exec_command"); // ledger: Starting

    let op = ExecCommand {
        command: input.cmd, cwd: None, env: json!({}),
        timeout_seconds: input.timeout_ms.map(ms_to_s), ownership,
    };
    let target = NamespaceTarget::from(handler.entry()?);     // From<WorkspaceEntry>
    let exec = match self.engine.run_shell_interactive(op, target, id.clone()) {
        Ok(exec) => exec,                                     // forks ns-runner, PTY, promise, watcher
        Err(e)   => { self.store.complete(&id, Error); self.cleanup_one_shot(ownership); return Err(e); }
    };

    // CommandExecution wraps InteractiveExecution<CommandTerminalResult> + transcript cursor/ownership.
    self.index.insert(CommandSessionId(id.0.clone()), CommandExecution::new(exec, ownership));

    // Initial yield: settle-or-timeout (quiet-period UX), unchanged.
    self.wait_for_command_yield(CommandSessionId(id.0), input.yield_time_ms.unwrap_or(1000), 0, false)
}

fn write_command_stdin(&self, input) -> Result<CommandYield> {
    let cmd = self.index.live(&input.command_session_id)?;    // CommandNotFound / AlreadyCompleted
    let start_offset = cmd.output_len();

    if is_kill_input(&input.stdin) {                          // Ctrl-C (\u{3}) / Ctrl-D (\u{4})
        cmd.cancel();                                         // InteractiveExecution::cancel → kill pgid
        cmd.mark_cancellation_requested();                   // thin command-view state
        return self.wait_for_command_yield(input.command_session_id, 1000, start_offset, true);
    }

    self.ensure_not_remount_pending(&cmd.workspace_session_id)?;
    cmd.write_stdin(input.stdin.as_bytes())?;                 // InteractiveExecution::write_stdin
    self.wait_for_command_yield(input.command_session_id, input.yield_time_ms.unwrap_or(1000), start_offset, true)
}

fn read_command_lines(&self, input) -> Result<CommandLinesOutput> {
    let start = input.start_offset.unwrap_or(0);
    let limit = validate_limit(input.limit.unwrap_or(200))?;  // 1..=1000

    if let Some(cmd) = self.index.live_or_none(&input.command_session_id)? {
        let window = cmd.transcript_window(start, limit);     // PTY transcript, still streaming
        return Ok(lines_output(window, Running, None, cmd.elapsed()));
    }
    // Terminal: resolved result + retained transcript from the registry/index.
    let done = self.index.completed(&input.command_session_id)?;
    let window = transcript_window(done.transcript_path, start, limit)?;
    Ok(lines_output(window, done.result.status, done.result.exit_code, done.elapsed()))
}
```

`wait_for_command_yield` is unchanged in spirit: it watches the execution's
`is_finished()` (condvar promise, not a poll) and the transcript length for the
quiet-period/yield-time settle, then renders a running or completed yield. The
completed branch no longer polls a `completed` map — it reads the resolved
promise/registry entry directly.

## Finalization / Terminal Semantics

Unchanged in meaning, made uniform across both families:

1. **Trigger — the process is gone.** The forked `ns-runner` child exits only
   after the runner's scope-wait drained the command's process group (or
   `SIGKILL`ed it on timeout/cancel, `wait.rs::wait_for_command_execution_scope`).
   The watcher's blocking `child.wait()` returns, then reads `RunResult` from the
   result-fd. So when exit is observed, no process from that execution's group is
   alive, and the runner child itself is reaped.
2. **Completion — finalize runs.** The watcher runs the operation's `finalize`
   (record result; destroy one-shot session; parse report), then resolves the
   promise (`Complete`) or, on `finalize` error, resolves with a terminal error
   (`Failed`).

So **terminal ⟹ no child/process-group alive, always.** The converse is not
instantaneous — child exit → engine still runs `finalize` before `is_finished()`
flips. A command whose caller never waits still goes terminal when its child
exits (the watcher owns it), exactly as today.

## CommandProcessStore Disposition

Verified field-by-field against current readers:

| Field(s) | Today's owner | After |
|---|---|---|
| `namespace_execution_id`, `started_at`, `completion`, `finalization`, active/completed maps, admission | `CommandProcessStore` | **engine** registry + promise (deleted here) |
| `process` (PTY), transcript path | `ActiveCommandProcess` | engine `InteractiveExecution` (PTY master) + command transcript |
| `next_snapshot_offset`, `workspace_ownership`, `cancellation` | `ActiveCommandProcess` | **command session view** on `CommandExecution` |
| `remount_cancellation`, `remount_switch_state` | written/read **only** by the remount coordinator | **move into the remount coordinator's own per-command state** |

Irreducible: the protocol returns a `command_session_id` and later calls
`write_command_stdin(id)`/`read_command_lines(id)` across requests, so live +
completed command handles must be retained server-side by id. That is the engine
registry (keyed by `namespace_execution_id`), not a command-owned store.

Remount coordination (`workspace_remount/service/command/{coordinator,quiesce}.rs`)
asks the engine registry for live interactive executions in a workspace (via the
observer index → ids → `InteractiveExecution` pgid/cancel) instead of reaching
into `active.remount_*`. `command::process_group` inspection stays.

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
`WorkspaceSessionId`). `operation::namespace_execution::NamespaceExecutionStore`
implements `ExecutionObserver`. The observable surface stays byte-for-byte the
Phase 4.6 model: one `active_namespace_executions` list, `operation_name =
"exec_command"`, generic `Starting/Running/Terminal`, **no**
`execution_kind`/substrate field. `Backing`/`ShellOperation`/`MountOperation`/the
registry are internal and not serialized.

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
pub struct NamespaceTarget { workspace_root, layer_paths, upperdir, workdir, ns_fds, timeout_seconds }

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

```text
crates/sandbox-runtime/
  namespace-process/                         [unchanged: re-exec runner child + protocol]
    src/runner/{mod,setns,shell_exec,protocol}.rs
  namespace-execution/                       ← NEW crate (engine, workspace-agnostic)   ~1,000
    src/{lib,id,error,target,promise,execution,shell,mount,observer,registry,engine,invocation,pty}.rs
  command/
    src/{lib,config,contract,transcript,transcript_rows,process_group}.rs               [kept]
    src/exec.rs                              ← NEW  ExecCommand                          ~40
    src/command_execution.rs                 ← NEW  CommandExecution                     ~120
    src/process.rs                           ✗ DELETE                                    −336
    src/pty.rs                               △ 513 → ~310 (spawn-half → engine)          −203
  workspace/
    src/namespace/ops.rs                     ← NEW  MountOverlayOp/RemountOverlayOp      ~90
    src/namespace/setns_runner.rs            △ 347 → ~180 (run_child/wait → engine)      −167
    src/model.rs                             + impl From<WorkspaceEntry> for NamespaceTarget
  operation/src/command/
    service/core.rs                          △ engine + index; drop store/sender
    service/impls/exec_command.rs            △ 374 → ~180                                −194
    service/impls/write_command_stdin.rs     △ 57 → ~45                                  −12
    service/impls/read_command_lines.rs      △ 63 → ~55                                  −8
    service/helpers.rs                       △ 180 → ~150 (no completed-map poll)        −30
    service/process_store.rs                 ✗ DELETE                                    −383
    service/completion.rs                    ✗ DELETE                                    −242
    service/launch.rs                        ✗ DELETE                                    −75
    service/finalize.rs                      ✗ DELETE                                    −276
    service/status_lookup.rs                 ✗ DELETE                                    −50
  operation/src/namespace_execution.rs       △ impl ExecutionObserver                    [kept ~423]
  operation/src/workspace_remount/service/command/{coordinator,quiesce}.rs  △ own remount scratch
```

LOC accounting (estimates):

| Bucket | LOC |
|---|---|
| Deleted outright (`process_store`, `completion`, `launch`, `finalize`, `status_lookup`, `process.rs`) | **−1,362** |
| Removed via shrink (`pty`, `setns_runner`, `exec_command`, `write_stdin`, `read_lines`, `helpers`) | **−614** |
| **Gross removed from existing crates** | **≈ −1,976** |
| New engine crate (~700 relocated logic + ~300 genuinely new) | +1,000 |
| New `exec.rs` + `command_execution.rs` + `ops.rs` + `From` impl | +260 |
| **Net repo delta** | **≈ −600 to −800** |
| Marginal cost of the *next* ns operation | ~700 (today) → **~30–80** |

The net deletion is real but secondary; the load-bearing number is the last row
— a new namespace operation becomes an operation impl, not a parallel
fork/promise/finalize/store stack.

## File Plan

### New crate `crates/sandbox-runtime/namespace-execution`

- `Cargo.toml` — deps: `sandbox-runtime-namespace-process`, `serde`,
  `serde_json`, `rustix`/`nix`, `libc`.
- `src/lib.rs` — re-exports.
- `src/id.rs` — `NamespaceExecutionId` (moved from `operation`; `operation`
  re-exports for back-compat).
- `src/error.rs` — `NamespaceExecutionError`.
- `src/target.rs` — `NamespaceTarget`, `Backing`.
- `src/promise.rs` — `CompletionPromise<T>` (condvar).
- `src/execution.rs` — `NamespaceExecution<T>`, `InteractiveExecution<T>`,
  `Execution<T>`.
- `src/shell.rs` — `ShellOperation`, `InteractiveShellOperation`,
  `ShellOutcome`, `ShellStatus`, `FinalizeCx`.
- `src/mount.rs` — `MountOperation`, `NsRunnerMode`.
- `src/observer.rs` — `ExecutionObserver`.
- `src/registry.rs` — live+completed registry, admission.
- `src/engine.rs` — `NamespaceExecutionEngine`: `run_shell_interactive`,
  `run_mount`, dispatch skeleton, watcher.
- `src/invocation.rs` — `NsRunnerInvocation` trait + real impl (fork
  `current_exe ns-runner [--mode]`, request/result/start-ack fds, PTY for
  `Backing::Pty`), unifying `spawn_current_exe_ns_runner` + `run_child`.
- `src/pty.rs` — `PtyMaster` + transcript reader (moved/narrowed from
  `command/src/pty.rs`).

### `crates/sandbox-runtime/command`

- `src/exec.rs` (new) — `ExecCommand: ShellOperation + InteractiveShellOperation`.
- `src/command_execution.rs` (new) — `CommandExecution` + transcript/yield.
- `src/contract.rs` — keep `CommandTerminalResult`; map from `ShellOutcome`.
- keep `transcript.rs`, `transcript_rows.rs`, `process_group.rs`, `config.rs`.
- **Delete** `src/process.rs` and the spawn half of `src/pty.rs` (→ engine).

### `crates/sandbox-runtime/workspace`

- `src/namespace/ops.rs` (new) — `MountOverlayOp`, `RemountOverlayOp: MountOperation`.
- `src/namespace/setns_runner.rs` — replace `run_child`/`wait_for_child`/
  `terminate_child`/`read_pipe` with `engine.run_mount(..).wait()`.

### `crates/sandbox-runtime/operation/src/command`

- `service/core.rs` — hold `Arc<NamespaceExecutionEngine>` + per-session
  `CommandExecution` index; drop `process_store` + `completion_sender`.
- `service/impls/exec_command.rs` — allocate id, `store.begin(..)`, build
  `ExecCommand`, `engine.run_shell_interactive(.., id)`, initial yield.
- `service/impls/write_command_stdin.rs` / `read_command_lines.rs` — via the
  index / completed registry.
- keep `service/helpers.rs` (yield UX), `service/transcript.rs`.
- **Delete** `service/process_store.rs`, `service/completion.rs`,
  `service/launch.rs`, `service/finalize.rs`, `service/status_lookup.rs`.
- `namespace_execution.rs` — implement `ExecutionObserver`; Phase 4.6 shape
  unchanged.

### `crates/sandbox-runtime/operation/src/workspace_remount/service/command`

- `coordinator.rs` / `quiesce.rs` — query the engine registry; hold
  `remount_cancellation`/`remount_switch_state` in the coordinator.

## Migration Sequencing (each phase stays green)

1. **Create the crate; move `NamespaceExecutionId` down** (`operation`
   re-exports). Add target/promise/handles/traits/observer/error/registry, no
   wiring. *Mechanical.*
2. **`NsRunnerInvocation` + engine dispatch + watcher**, with a fake invocation
   for tests. Unit-test the engine end to end (no command/workspace changes).
3. **`ExecCommand` + `CommandExecution`**; migrate `exec_command` /
   `write_command_stdin` / `read_command_lines`; wire `ExecutionObserver`. Delete
   `process_store`/`completion`/`launch`/`finalize`/`status_lookup`; remove
   `spawn_current_exe_ns_runner` from the command path.
4. **Mount family**: `MountOverlayOp`/`RemountOverlayOp`; route overlay/remount
   through `engine.run_mount`; delete `run_child` and its wait/pipe helpers.
5. **Remount coordinator** off `active.remount_*` onto engine queries + its own
   state.
6. **Cleanup**: delete dead `command/src/process.rs` + spawn-half of `pty.rs`;
   confirm `ns-runner` re-exec has a single daemon-side launcher (the engine).

## Future Extensions (explicitly deferred)

- **`ShellOp<O>` Tier-2 combinator** — a blanket `ShellOperation` for "run a
  command, parse stdout → `O`"; a new shell op becomes a command string + a parse
  closure. Requires stdout capture: have the runner return captured output in
  `RunResult.payload` for `Backing::Pipe`. Land with the first non-command shell
  producer; reassess the Phase 4.6 "second classification axis" note then
  (still prefer distinct `operation_name`s).
- **Swappable invocation backend** — if a persistent per-session runner returns,
  it implements `NsRunnerInvocation` (or a sibling) without touching command,
  mount, the promise, the registry, or tracking. The seam exists for exactly
  this.

## Test Plan

`crates/sandbox-runtime/namespace-execution`:

- Engine dispatch against a fake `NsRunnerInvocation`: child exit → `RunResult`
  → promise resolves with the finalized `Output`.
- `finalize` error → promise resolves with a terminal error.
- `CompletionPromise::wait_timeout` blocks then returns on resolve (no poll).
- `cancel()` kills the child's process group.
- Admission limit rejects past `max_active`.
- `run_mount` resolves a parsed report; sync `.wait()` path works.
- `namespace_execution_id` is the runner `request_id` and the registry key.

`crates/sandbox-runtime/operation` (command):

- `exec_command` initial yield; long-running command yields a
  `command_session_id`; `write_command_stdin`/`read_command_lines` via the engine
  registry.
- One-shot workspace destroyed in `ExecCommand::finalize`.
- Observability: running command appears once in `active_namespace_executions`,
  `operation_name = "exec_command"`; no `active_executions`/`active_commands`
  lane; no `execution_kind`/substrate field (Phase 4.6 invariants).
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
# observability axis unchanged (Phase 4.6):
rg -n "execution_kind|namespace_execution_kind|runner_kind|execution_scope|active_executions|active_commands" crates/sandbox-runtime/operation/src
git diff --check
```

## Completion Checklist

- [ ] `sandbox-runtime-namespace-execution` crate exists; `NamespaceExecutionId`
      moved down with an `operation` re-export; engine is workspace-agnostic
      (`NamespaceTarget`, sits below `workspace`).
- [ ] `NsRunnerInvocation` is the single daemon-side `ns-runner` launcher;
      `spawn_current_exe_ns_runner` and `run_child` are gone.
- [ ] `CompletionPromise<T>` is condvar-backed; no poll loops in completion.
- [ ] `NamespaceExecution<T>`/`InteractiveExecution<T>` realize the subtype by
      composition + the `Execution<T>` trait (no `Deref` polymorphism).
- [ ] Two families (`ShellOperation`/`MountOperation`) over one core; no single
      trait unions shell and mount.
- [ ] `ExecCommand: InteractiveShellOperation`; command runs through the engine.
- [ ] Overlay/remount run through `engine.run_mount`; tracked + promised.
- [ ] `CommandProcessStore`, `completion.rs`, `launch.rs`, `finalize.rs`,
      `status_lookup.rs` deleted; thin command session view remains.
- [ ] `remount_cancellation`/`remount_switch_state` live in the remount
      coordinator; quiesce/resume still works.
- [ ] `ExecutionObserver` feeds `NamespaceExecutionStore`; Phase 4.6
      observability surface unchanged.
- [ ] `Backing` is internal/binary (`Pty`/`Pipe`); not serialized; no
      `Captured`/`Report` types.
- [ ] `ShellOp<O>` + stdout capture documented as future, not implemented.
```
