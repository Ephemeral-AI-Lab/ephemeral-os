# Phase 3 Spec — Command onto the Engine; gut `CommandProcessStore`

Implementation-ready spec for **Phase 3** of the Namespace Execution Engine
migration. Phase contract:
[`migration-phases.md` § "Phase 3"](./migration-phases.md). Design of record:
[`docs/namespace-execution.md`](../namespace-execution.md). Consumed contract:
[`phase-2-spec.md`](./phase-2-spec.md). This document is **spec only** — build to
the Acceptance Criteria (§16), not while reading.

Every factual claim is tagged **grounded** with a `file:line` anchor or listed as
an **assumption** in §15. Anchors were read from the live checkout while
authoring; **Phase 2 is not yet implemented** (the engine crate is still the
Phase-1 skeleton — `crates/sandbox-runtime/namespace-execution/src/lib.rs:16-21`
gates `execution`/`promise`/`registry` behind `test-support`, and
`engine.rs`/`launcher.rs`/`pty.rs`/`status.rs` do not exist), so §2 pins the Phase
2 surface as a contract and flags every place the current Phase 2 *spec* under-
delivers what Phase 3 consumes.

> **Reading order for the implementer.** §2 (what you may call), §3 (what you
> build), §5 (how it behaves under stress), §7–§8 (what you touch and in what
> order). §15 lists the deviations from the three source docs that a human must
> sign off; **do not start until §15 has been read.**

---

## 1. Objective & Non-Goals

### 1.1 Objective

Re-express the three command APIs (`exec_command`, `write_command_stdin`,
`read_command_lines`) on top of `NamespaceExecutionEngine` and its registry, and
delete `CommandProcessStore` and its satellites. After Phase 3:

- the command service owns **no** spawn/PTY/promise/finalizer/`FinalizationState`
  machinery and **no** daemon-side poll loop;
- one `NamespaceExecutionId` is the command's identity, wrapped as
  `CommandSessionId(id.0)` for the public protocol;
- the engine registry retains the live + terminal command handle by id (the
  command service holds **no second per-session map**);
- the observability surface (the `active_namespace_executions` list,
  `operation_name = "exec_command"`, lifecycle `Starting/Running/Terminal`, the
  finalization trace, and the serialized field names) is **unchanged**, except
  the *internal* `NamespaceExecutionRecord.request_id` field is renamed
  `origin_request_id`.

### 1.2 Non-goals (explicitly out of scope for Phase 3)

- **Mount family** onto the engine (`run_mount`, `setns_runner.rs` rewrite,
  `run_child`/`ns_runner_request` deletion, `isolated-{mode}-{id}`): **Phase 4**.
  Exception: the `From<WorkspaceEntry> for NamespaceTarget` impl, which Phase 3
  *consumes*, must land in Phase 3 — see §10.1.
- **Remount coordinator rewrite** onto engine queries: **Phase 5**. Phase 3 makes
  only the *minimal* edits to `workspace_remount/.../{coordinator,quiesce}.rs`
  needed to keep the workspace building after the write-only fields are deleted
  (§7.7) — it does **not** move quiesce/resume onto the registry.
- **Deleting `command/src/process.rs` and `command/src/pty.rs`**, removing the
  result-fd reader thread on surviving paths, and the **atomic start-ack
  removal**: **Phase 6**. Phase 3 leaves both files in place, `pub`-exported and
  unit-tested (§8.2); the new launcher keeps passing `--start-ack-fd`.
- **`ShellOp<O>` / stdout capture / batch shell**: deferred (design § Future
  Extensions).
- No `execution_kind`/`backing` classification axis is introduced anywhere.

---

## 2. Consumed Phase 2 API (the contract) + classification

Phase 3 builds on the engine crate. Because Phase 2 is unbuilt, this section is
**also Phase 2's acceptance contract for the command consumer**. Items the
*written* Phase 2 spec defers or omits are flagged **⚠ Phase-2-gap** — the
implementer must treat them as Phase-3 engine-crate edits (the migration's "Phase
3 crates touched: `command`, `operation/command`" is materially incomplete; see
§15-D1).

### 2.1 Surface Phase 3 calls

```rust
// engine.rs
impl NamespaceExecutionEngine<V> {
    pub fn allocate_id(&self) -> NamespaceExecutionId;                         // ⚠ Phase-2-gap
    pub fn run_shell_interactive<S: ShellOperation>(
        &self, op: S, target: NamespaceTarget, id: NamespaceExecutionId,
    ) -> Result<InteractiveExecution<S::Output>, NamespaceExecutionError>;
    pub fn attach(&self, id: &NamespaceExecutionId, value: V);                 // ⚠ Phase-2-gap (registry stores caller value)
    pub fn with_value<R>(&self, id: &NamespaceExecutionId,
                         f: impl FnOnce(&V) -> R) -> Option<R>;                // ⚠ Phase-2-gap
    pub fn is_live(&self, id: &NamespaceExecutionId) -> bool;
    pub fn is_completed(&self, id: &NamespaceExecutionId) -> bool;
}

// execution.rs — InteractiveExecution<T> / ExecutionHandle<T>
fn id(&self) -> &NamespaceExecutionId;
fn is_finished(&self) -> bool;
fn write_stdin(&self, bytes: &[u8]) -> io::Result<()>;
fn output_len(&self) -> u64;                       // transcript byte length
fn read_output_since(&self, offset: u64) -> String;
fn cancel(&self);
fn wait(self) -> Result<T, NamespaceExecutionError>;          // consumed only by mount
fn wait_timeout(&self, d: Duration) -> bool;                 // ⚠ Phase-2-gap: promise has it; handle forward is Phase 3
fn resolved(&self) -> Option<Result<T, NamespaceExecutionError>> where T: Clone;  // ⚠ Phase-2-gap (the "peek")

// shell.rs
trait ShellOperation { /* operation_name, command, timeout_seconds, finalize */
    fn transcript_path(&self) -> Option<&std::path::Path> { None }   // ⚠ Phase-2-gap (file-backed transcript hook)
}
struct RunnerOutcome; // status() -> NamespaceExecutionTerminalStatus, exit_code() -> i64, payload() -> &Value
                      // ⚠ Phase-2-gap: status() applies the cancel override (cancel known engine-side)

// observer.rs
trait ExecutionObserver { fn on_running(&self, id); fn on_terminal(&self, id, status, exit_code); }
```

> **Design-vs-impl divergence on the peek (§5, Hard Problem #1).** The design and
> the prompt list `wait_timeout(&self, d) -> Option<&T>`. A reference cannot be
> returned from behind the promise's `Arc<Mutex<…>>` (the value lives under a
> guard). This spec replaces the literal `Option<&T>` with two methods:
> `wait_timeout(&self, d) -> bool` (block-or-timeout for the yield loop) and
> `resolved(&self) -> Option<Result<T,…>>` where `T: Clone` (non-consuming
> snapshot for terminal reads). `CommandTerminalResult` is `Clone` and its read
> fields are `Copy`, so the clone is trivial. Flagged in §15-D2.

### 2.2 Classification table

| Capability Phase 3 relies on | Status | Evidence / note |
|---|---|---|
| `NamespaceExecutionId` (id.rs), `NamespaceTarget` (5 fields), `NamespaceExecutionError` | **exists-today** | `…/namespace-execution/src/{id,target,error}.rs` |
| `ShellOperation` trait (`operation_name/command/timeout_seconds/finalize`) | **exists-today** | `…/src/shell.rs:14-24` |
| `RunnerOutcome::exit_code()` | **exists-today** | `…/src/shell.rs:9-11` |
| `CompletionPromise` (resolve/is_resolved/wait), `ExecutionHandle{id,is_finished,wait}`, `InteractiveExecution{new,id,is_finished,wait}` | **exists-today (test-support gated)** | `…/src/{promise,execution}.rs`; un-gate is Phase 2 |
| `ExecutionRegistry` (capacity placeholder) | **exists-today (placeholder)** | `…/src/registry.rs:4-16` |
| `ExecutionObserver::on_running` | **exists-today** | `…/src/observer.rs:5-7` |
| `run_shell_interactive`, watcher, `ForkRunnerLauncher`, `PtyMaster`, `RunnerOutcome::{status,payload}`, `on_terminal`, real registry (try_reserve/attach/abort/complete), `NamespaceExecutionTerminalStatus` relocation, promise `wait_timeout(Duration)->bool` | **Phase-2-adds** | `phase-2-spec.md` §2, §5 |
| `engine.allocate_id()` | **Phase-3-adds** ⚠ | absent from `phase-2-spec.md` §5.1; migration says "id unification lands in Phase 2" (`migration-phases.md:268`) but the spec omits the method. §15-D1 |
| Registry stores the **caller value** `V` (`attach`/`with_value`/iterate); `NamespaceExecutionEngine<V>` generic | **Phase-3-adds** ⚠ | Phase 2 registry is generic-tracking-only (`phase-2-spec.md` §2.8: "Phase 2 does **not** store the returned handle"). §3.6, §15-D3 |
| `resolved()` peek + handle `wait_timeout` forward | **Phase-3-adds** ⚠ | `phase-2-spec.md` §2.5 defers `Option<&T>` |
| `RunnerOutcome::status()` cancel override | **Phase-3-adds** ⚠ | `phase-2-spec.md` §2.6 keeps `status()` a pure wire parse; defers override to Phase 3 |
| File-backed transcript (`ShellOperation::transcript_path` + PtyMaster file sink) | **Phase-3-adds** ⚠ | `phase-2-spec.md` §2.3/§5.3 keep the sink in-memory, "file persistence … a command concern deferred to Phase 3" |
| `test-support`-gated public fake launcher + `with_launcher`/engine ctor for **operation** tests | **Phase-3-adds** ⚠ | `phase-2-spec.md` §2.1 fakes are `#[cfg(test)]` engine-only; operation tests need them. §12 |

---

## 3. Target design

### 3.1 `ExecCommand` — the shell-operation strategy (new: `command/src/exec.rs`)

`ExecCommand` is `command`-crate-local (`command` depends on `namespace-execution`
— design crate graph `namespace-execution.md:520-523`). It owns the one-shot
session-destroy policy **and** the finalization observability trace, both of which
run inline on the watcher thread (§4) where no other command code reaches.

```rust
pub struct ExecCommand {
    command: String,
    timeout_seconds: Option<f64>,
    transcript_path: PathBuf,                 // file sink for the PTY reader (§3.5)
    session_disposition: SessionDisposition,  // ExistingSession | OneShot { handler }
    workspace: Arc<WorkspaceSessionService>,  // its own handle, for the one-shot destroy
    // finalization-trace carriers (preserve the async finalization trace, §9.4):
    finalization_trace: Option<CommandFinalizationTrace>,
}

pub enum SessionDisposition {                 // renamed from CommandWorkspaceOwnership
    ExistingSession,
    OneShot { handler: Box<WorkspaceSessionHandler> },
}

impl ShellOperation for ExecCommand {
    type Output = CommandTerminalResult;
    fn operation_name(&self) -> &'static str { "exec_command" }   // unchanged ledger name
    fn command(&self) -> &str { &self.command }
    fn timeout_seconds(&self) -> Option<f64> { self.timeout_seconds }
    fn transcript_path(&self) -> Option<&Path> { Some(&self.transcript_path) }
    fn finalize(self: Box<Self>, o: RunnerOutcome)
        -> Result<CommandTerminalResult, NamespaceExecutionError>;
}
```

`finalize` (full behavior in §5.3): builds `CommandTerminalResult` from
`o.status()`/`o.exit_code()`/`o.payload()`; if `OneShot`, destroys the session via
`self.workspace.destroy_session(handler, default)`, mapping a destroy failure to
`NamespaceExecutionError::Finalize`; emits the finalization trace if present.
There is **no** engine-provided `FinalizeCx` (design ban; `namespace-execution.md:254`).

- **Why a new file, not a reused one:** today the finalize logic is split across
  `finalize.rs` (`apply_workspace_completion_policy`, `terminal_result`) and
  `command/src/pty.rs` (`CommandCompletionStatus`, the cancel override). Those
  live in `operation` and `command` respectively and run on a *shared* finalizer
  thread; the engine model runs `finalize` per-exec on the watcher thread, so the
  policy must be one `Send + 'static` strategy object. No existing type is both
  `Send + 'static` and carries the disposition + workspace handle.
- **`SessionDisposition` rename** of `CommandWorkspaceOwnership`
  (`operation/.../process_store.rs:292`, variants unchanged) — it is a
  *cleanup-responsibility* flag, not workspace ownership
  (`migration-phases.md:281-287`). It moves **down** into `command/src/exec.rs`
  with `ExecCommand` because the engine-thread `finalize` needs it and
  `command` is the lowest crate that can host it.

### 3.2 `CommandExecution` — the registry value (new: `command/src/command_execution.rs`)

The single per-command handle, retained **in the engine registry** keyed by id
(`namespace-execution.md:345`). Serves live reads (write/yield) and terminal reads
(transcript window + result), distinguished by the promise.

```rust
pub struct CommandExecution {
    exec: InteractiveExecution<CommandTerminalResult>,  // handle (id+promise) + PTY
    transcript_path: Option<PathBuf>,                   // for required/transcript_window reads
    workspace_session_id: WorkspaceSessionId,           // remount-pending guard + workspace→cmd reverse lookup
    started_at: Instant,
    next_snapshot_offset: AtomicU64,                    // transcript cursor (interior mutability; read under registry lock)
}

impl CommandExecution {
    pub fn new(exec: InteractiveExecution<CommandTerminalResult>,
               transcript_path: Option<PathBuf>, workspace_session_id: WorkspaceSessionId,
               started_at: Instant) -> Self;
    pub fn id(&self) -> &NamespaceExecutionId;
    pub fn is_finished(&self) -> bool;                  // self.exec.is_finished()
    pub fn workspace_session_id(&self) -> &WorkspaceSessionId;
    pub fn write_stdin(&self, bytes: &[u8]) -> io::Result<()>;   // → exec.write_stdin
    pub fn cancel(&self);                                         // → exec.cancel (killpg)
    pub fn output_len(&self) -> u64;                             // → exec.output_len (transcript file len)
    pub fn elapsed_seconds(&self) -> f64;                        // started_at.elapsed()
    pub fn terminal_result(&self) -> Option<Result<CommandTerminalResult, NamespaceExecutionError>>;  // → exec.resolved()
    pub fn take_snapshot_offset(&self) -> u64;                   // load
    pub fn advance_snapshot_offset(&self, next: u64);            // store
    pub fn transcript_window(&self, start: u64, limit: usize)
        -> Result<CommandTranscriptWindow, CommandTranscriptError>;   // required_transcript_window(path,…)
}
```

`next_snapshot_offset` is `AtomicU64` (not `u64`) because the registry hands out
`&CommandExecution` under its lock and the yield path advances the cursor through
a shared reference; today's `&mut` via `update_active`
(`operation/.../helpers.rs:118-122`) is replaced by interior mutability so the
registry never needs a write lock distinct from its map lock.

- **Why not reuse `ActiveCommandProcess`** (`operation/.../process_store.rs:273-289`):
  it carries 15 fields, 5 of them write-only (`lifecycle_state`, `cancellation`,
  `remount_cancellation`, `remount_switch_state`, plus the `process: Arc<CommandProcess>`
  whose role the engine `InteractiveExecution` now owns). `CommandExecution` is the
  4-live-field residue (handle, transcript path, workspace id, cursor + started_at).
- **No `session_disposition` field here** (the design lists one,
  `namespace-execution.md:342`): it is *write-only* on the registry value —
  `finalize` reads its own copy carried by `ExecCommand`, and no other reader
  exists (the enumeration shows `workspace_ownership` read only in `finalize.rs:112,142`,
  both of which move into `ExecCommand::finalize`). Dropped per "prefer less";
  flagged §15-D4.

### 3.3 `CommandTerminalResult` — trimmed, relocated to `command/src/contract.rs`

```rust
// command/src/contract.rs (currently holds only CommandError — contract.rs:7-25)
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct CommandTerminalResult {
    pub status: NamespaceExecutionTerminalStatus,   // engine enum; reachable from `command`
    pub exit_code: i64,
    pub command_total_time_seconds: f64,
}
```

- **Relocation, not edit.** The design's file tree shows
  `command/src/contract.rs △ CommandTerminalResult` (`namespace-execution.md:594`),
  implying it already lives there. It does **not** — it is in
  `operation/.../process_store.rs:335` with a 4th `stdout: String` field. Phase 3
  **moves** it down to `command` so `ExecCommand::finalize` (command crate) can
  produce it. Flagged §15-D5.
- **`stdout` dropped** (yields read the transcript; `namespace-execution.md:474`);
  **`timed_out` was never a field** — derivable from `status`. The status type
  becomes `NamespaceExecutionTerminalStatus` (`Ok/Error/TimedOut/Cancelled`,
  the engine enum relocated in Phase 2) rather than the old `CommandStatus`,
  because `o.status()` already yields it and `command` cannot reach the
  `operation`-resident `CommandStatus`. `Copy` because all three fields are `Copy`
  — this makes the `resolved()` peek (§2.1) trivial.

### 3.4 `CommandOutput` — the merged DTO (`operation/.../service/contract.rs`)

`CommandYield` (`contract.rs:82-94`), `CommandLinesOutput` (`contract.rs:96-108`),
and `CommandOutputSnapshot` (`contract.rs:52-59`) are one struct after the merge.
They differ only in `command_session_id` optionality (`CommandYield: Option`,
`CommandLinesOutput: non-Option`); `CommandOutput` uses the `Option` form (the
superset).

```rust
#[derive(Debug, Clone, PartialEq)]
pub struct CommandOutput {
    pub command_session_id: Option<CommandSessionId>,
    pub status: CommandStatus,            // Running | Ok | Error | TimedOut | Cancelled (kept; has Running)
    pub exit_code: Option<i64>,
    pub wall_time_seconds: f64,
    pub command_total_time_seconds: f64,
    pub start_offset: u64,
    pub end_offset: u64,
    pub total_lines: u64,
    pub original_token_count: u64,
    pub output: String,
}
```

- `CommandStatus` (`contract.rs:30-50`, has the `Running` variant) is **kept** in
  `operation` — it is the command-facing status the CLI serializes
  (`cli_definition/command_operations.rs`), and `Running` has no engine
  equivalent. The read path maps `NamespaceExecutionTerminalStatus → CommandStatus`
  for terminal commands (the inverse of the deleted `finalize.rs:257-263`
  `namespace_terminal_status`) and emits `CommandStatus::Running` for live ones.
- `CommandCompletionWaitOutcome` (`completion.rs:18-22`) is **deleted** — the
  running-vs-terminal branch is `CommandExecution::is_finished()`
  (`namespace-execution.md:430`).
- The two CLI return types change from `CommandYield`/`CommandLinesOutput` to
  `CommandOutput` at `cli_definition/command_operations.rs:276,286,324,342`
  (grounded: enumeration §9, §10 hits). This is an internal DTO; the *serialized*
  CLI response shape is unchanged (same fields).

### 3.5 File-backed transcript reconciliation (engine `pty.rs`)

Phase 2 drains the PTY master to an in-memory `Arc<Mutex<Vec<u8>>>`
(`phase-2-spec.md` §2.3). The command needs a **file** so a terminal command's
transcript outlives its `CommandExecution` reads and so the existing
1 MiB-windowed row reader works unchanged. Phase 3 therefore:

- gives `PtyMaster::spawn` an `Option<PathBuf>` sink; when `Some`, the reader
  thread appends timestamp-prefixed bytes to that file (relocating today's
  `command/src/pty.rs:398-441` `spawn_command_output_reader` file behavior); when
  `None`, the in-memory buffer remains (Phase-2 generic behavior, future ops).
  The `TranscriptTimestampPrefixer` write-side must live in the engine crate
  (relocated in Phase 2; `command` cannot be a dependency of `namespace-execution`).
- routes the path from `ShellOperation::transcript_path()` →
  `run_shell_interactive` → `spawn_pty`.
- `InteractiveExecution::output_len()` returns the **file** byte length when
  file-backed (`std::fs::metadata(path).len()`, matching today's
  `command/src/process.rs:316-321` `transcript_len`); `read_output_since` reads
  the file.

The **read** side stays in `command`, unchanged: `transcript_window` /
`required_transcript_window` (`command/src/transcript_rows.rs:33,51`,
`(path, offset, limit) -> CommandTranscriptWindow`), the 1 MiB truncation +
`[eos: transcript truncated …]` notice (`command/src/transcript.rs:7-9,63-82`).
Byte offsets are file offsets into the prefixed transcript — identical semantics
to today. The write-side prefix format and the read-side parser share the format
across the crate boundary (both relocate/stay verbatim).

### 3.6 Registry generic over the caller value (engine `registry.rs`, `engine.rs`)

To hold `CommandExecution` in the **single** engine registry without naming a
command type, the registry and engine become generic over the stored value `V`:

```rust
pub(crate) struct ExecutionRegistry<V> { inner: Mutex<RegistryState<V>>, max_active: usize }
struct RegistryState<V> { entries: HashMap<NamespaceExecutionId, Entry<V>>, active: usize }
struct Entry<V> { value: Option<V>, terminal: bool, status: Option<NamespaceExecutionTerminalStatus>, exit: Option<i64> }

impl<V> ExecutionRegistry<V> {
    fn try_reserve(&self, id) -> Result<(), NamespaceExecutionError>;  // active<max → insert Entry{None,false,..}, active++
    fn attach(&self, id, value: V);                                    // entry.value = Some(value)
    fn abort(&self, id);                                               // remove entry, active--   (spawn failure)
    fn complete(&self, id, status, exit);                             // entry.terminal=true, status/exit set, active-- (idempotent)
    fn with_value<R>(&self, id, f: impl FnOnce(&V) -> R) -> Option<R>;
    fn is_live(&self, id) -> bool;       // present && !terminal
    fn is_completed(&self, id) -> bool;  // present && terminal
    fn live_values<R>(&self, f: impl Fn(&V) -> Option<R>) -> Vec<R>;  // iterate live (workspace→cmd lookup; Phase 5)
}

pub struct NamespaceExecutionEngine<V = ()> {
    registry: Arc<ExecutionRegistry<V>>,
    observer: Arc<dyn ExecutionObserver>,
    launcher: Box<dyn NsRunnerLauncher>,   // Phase 2 boxed-trait seam — UNCHANGED, stays non-generic
    next_id: AtomicU64,
}
```

- Command instantiates `NamespaceExecutionEngine<CommandExecution>`; mount (Phase 4)
  uses the default `NamespaceExecutionEngine` (`= NamespaceExecutionEngine<()>`).
  They are **separate instances** sharing one `Arc<dyn ExecutionObserver>` (the
  ledger), which is correct: admission domains differ (the 256-command limit is
  command-only — `process_store.rs:19`), and the single observability list comes
  from the *observer*, not the registry (§9).
- `complete(id, status, exit)` does **only** generic bookkeeping (terminal flag +
  admission release); it never touches `V`. This makes the attach/complete race
  (§5.6) impossible: the watcher can `complete` before the command `attach`es; the
  entry exists from `try_reserve`, `attach` fills `value` regardless of terminal
  state, and the command reads `value` + the promise.
- This is a deliberate divergence from Phase 2's non-generic engine
  (`phase-2-spec.md` §2.1, "Phase 3 callers write `Arc<NamespaceExecutionEngine>`
  with no type parameter") and two-map registry (§2.8). The launcher seam stays
  `Box<dyn NsRunnerLauncher>` (so Phase 2's `private_interfaces`/`private_bounds`
  fix is preserved); `V` is a *public* type parameter (no private-bound issue).
  Justified + flagged §15-D3 with rejected alternatives in §3.7.

### 3.7 Rejected alternatives

Carried forward from the design (each still rejected):

- **No `Execution<T>` trait / no `Deref` polymorphism** — no call site is
  polymorphic over the two handle types (`namespace-execution.md:101-103,186`).
- **No `FinalizeCx`** — `ExecCommand` carries its own workspace handle
  (`namespace-execution.md:131,254`).
- **No `Backing` enum / no `Captured`/`Report` taxonomy** — PTY-vs-pipe is which
  launcher method runs (`namespace-execution.md:130-134`).
- **No revived `CommandProcessStore` source of truth / no `FinalizationState`
  machine / no daemon poll loop** (`migration-phases.md:30-34`).

Phase-3-local alternatives weighed for the **registry-holds-the-handle** problem:

- **(A, chosen) Generic `ExecutionRegistry<V>` + `NamespaceExecutionEngine<V=()>`.**
  Type-safe; honors "single registry, no second map"; serves Phase 5 registry
  queries. Cost: a public type parameter (diverges from Phase 2's stated
  non-generic engine).
- **(B, rejected) Command-owned `Mutex<HashMap<id, CommandExecution>>`.** Simplest
  code, engine unchanged. Rejected: it is exactly the "second per-session map"
  the design bans (`namespace-execution.md:345,422`) and would force Phase 5 to
  query the command service rather than the registry (`namespace-execution.md:484-488`).
- **(C, rejected) Type-erased `Box<dyn Any>` registry value, engine non-generic.**
  Preserves Phase 2's non-generic engine and lets command+mount share one engine.
  Rejected: `downcast_ref` is a runtime check with an "impossible" failure arm
  that the repo's clean-types ethos disfavors (`CLAUDE.md` SRP/"names carry
  meaning"); a public type parameter is cleaner than erasure.

---

## 4. Thread & ownership model

Per command there are **two** engine-owned threads (`namespace-execution.md:303-316`),
plus the API caller's request thread. The shared finalizer thread
(`completion.rs:118-137` `spawn_completion_finalizer`) and the per-command
poll-watcher (`completion.rs:75-84` `start_watcher`) are **both deleted**.

| Thread | Spawned by | Owns / touches | Blocking calls |
|---|---|---|---|
| **API caller** (`exec_command` / `write_command_stdin` / `read_command_lines`) | daemon dispatch | `workspace_lifecycle_admission` mutex (held across `run_shell_interactive` + `attach`); reads registry via `with_value` | the yield loop (`wait_timeout` on the promise condvar, §5.8) |
| **Watcher** (one per exec) | `run_shell_interactive` | the moved `RunnerChild`; clones of `Arc<promise>`, `Arc<registry>`, `Arc<dyn observer>`, the boxed `finalize` closure (owns `ExecCommand` incl. the `OneShot` handler + workspace `Arc` + trace) | `child.wait_completion()` (no lock held) |
| **PTY reader** (one per interactive exec) | `PtyMaster::spawn` inside `spawn_pty` | the PTY master (read end); appends to the transcript **file** | `poll(master, -1)` until EOF/hangup |

### 4.1 Global lock order (acquire in this order; never invert)

1. `CommandOperationService.workspace_lifecycle_admission` (`Mutex<()>`,
   `core.rs:29`) — coarse; held only on the caller thread across a command start
   or a workspace destroy, **never** across `child.wait_completion()`.
2. `ExecutionRegistry.inner` (`Mutex<RegistryState<V>>`) — short critical
   sections: `try_reserve`, `attach`, `complete`, `with_value`, `is_live/…`.
3. `CompletionPromise.slot` (`Mutex<Option<Result<T>>>`) — `resolve` (watcher),
   `wait`/`wait_timeout`/`resolved` (caller).
4. Observer (`NamespaceExecutionLedger.inner`, `namespace_execution.rs:19`) —
   `on_running`/`on_terminal`.
5. `PtyMaster.writer` (`Mutex<File>`) — `write_stdin` (caller); independent of
   1–4.

The registry lock (2) and the promise lock (3) are **never** held simultaneously:
the watcher calls `registry.complete(id, …)` (acquire+release 2) **then**
`promise.resolve(…)` (acquire+release 3) — see §5.2. `cancel()` (`killpg`) holds
**no** lock (§5.4). No thread holds a lock across `child.wait_completion()`
(§5.4) or across `poll(-1)`.

---

## 5. Concurrency, failure & lifecycle semantics (the Hard Problems)

### 5.1 Result ownership from a registry-retained handle (Hard Problem #1)

The registry **retains** the `CommandExecution`, so the command service can never
call `wait(self)` (it would need to move the handle out of the registry, and
`wait` consumes). Therefore:

- **Running read** (`is_finished() == false`): the transcript window comes from
  the **file** (`CommandExecution::transcript_window` →
  `required_transcript_window(transcript_path, start, limit)`); the byte cursor
  from `output_len()` (file length). No promise value is read. Status is
  `CommandStatus::Running`, `exit_code = None`.
- **Terminal read** (`is_finished() == true`): the status/exit/total-time come
  from a **non-consuming** `resolved() -> Option<Result<CommandTerminalResult,…>>`
  (the value is `Clone` + `Copy`; the promise slot is **not** taken); the
  transcript window from the file (still by path). Multiple terminal reads are
  safe because `resolved()` clones, never takes.
- `registry.complete(id, status, exit)` stores only the generic
  `(status, exit)` projection in the `Entry`; it does **not** move or clone
  `CommandExecution`, which stays in `entries` for both read phases. The owned
  `CommandTerminalResult` for a read is the `resolved()` clone.

This is why the design's heavyweight "completed record" is unnecessary: the file
holds the transcript, the promise holds the result, and the single map holds the
handle — no live→completed object move (§3.6).

### 5.2 finalize → complete → resolve ordering (Hard Problem #2)

`finalize` runs **inline on the watcher thread** before the promise resolves
(`namespace-execution.md:443-448`). The watcher step order (refining the design's
step-7 listing to satisfy the design's own invariant, per `phase-2-spec.md` §2.4):

```text
let run_result = child.wait_completion();                  // no lock held
let outcome    = RunnerOutcome::new(run_result, cancelled);// cancelled flag from §5.4
let (status, exit) = (outcome.status(), Some(outcome.exit_code()));
let result = match op_box.finalize(outcome) {              // ExecCommand::finalize — one-shot destroy + trace
    Ok(o)  => Ok(o),
    Err(e) => Err(e),                                       // status overridden to Error below
};
let (rs, re) = match &result { Ok(_) => (status, exit), Err(_) => (Error, exit) };
registry.complete(&id, rs, re);     // (lock 2)  — BEFORE resolve
promise.resolve(result);            // (lock 3)
observer.on_terminal(&id, rs, re);  // (lock 4)
```

Because `complete` precedes `resolve`, the invariant **promise-resolved ⟹ the
registry entry is terminal** holds. A concurrent reader that observes
`is_finished() == true` (promise resolved) is guaranteed the entry is
terminal-marked and `resolved()` returns `Some`. This is exactly the property that
lets the yield path **delete `wait_for_completed_record`** (`completion.rs:214-241`,
the 5 ms poll): there is nothing to poll for — the result is in the promise the
instant `is_finished()` flips. Proof of no torn read: `resolved()` reads the
promise slot under lock (3); the watcher writes it once under lock (3) via
`resolve`; `complete` (lock 2) strictly precedes `resolve` (lock 3) on the same
thread, so any reader seeing the resolved promise also sees the completed entry.

### 5.3 finalize failure & panic (Hard Problem #3)

- **finalize error (one-shot destroy fails).** `ExecCommand::finalize` maps the
  `WorkspaceSessionError` to `NamespaceExecutionError::Finalize(msg)` and returns
  `Err`. The watcher resolves the promise `Err`, marks the entry terminal with
  status `Error`, and `on_terminal(Error)`. The command read path, on a resolved
  `Err`, maps it to `CommandServiceError::CommandFinalizationFailed { error, … }`
  (preserving today's surface from `finalize.rs:228-232`). This replaces today's
  `fail_active` + `FinalizationState::Failed` record (`process_store.rs:155-193`).
- **one-shot destroy on the *start-failure* path** (before the watcher exists) →
  `CommandServiceError::OneShotSessionCleanupFailed` (renamed from
  `OneShotWorkspaceCleanupFailed`, `error.rs:92-99`), emitted by the
  `exec_command` error path (§5.6), **unchanged in meaning**.
- **finalize panic on the watcher thread.** `finalize` is wrapped in
  `std::panic::catch_unwind` in the watcher; a caught panic is converted to
  `NamespaceExecutionError::Finalize("finalize panicked: …")`, then the normal
  error path runs (`complete(Error)` → `resolve(Err)` → `on_terminal(Error)`). The
  child is already reaped (the panic is *after* `wait_completion`), and the
  one-shot session may have leaked — the converted error message records it; this
  matches the design's "the watcher owns completion" guarantee
  (`namespace-execution.md:456-457`). `catch_unwind` requires the captured state
  to be `UnwindSafe`; the closure captures `Arc`s and the boxed op — wrap the body
  in `AssertUnwindSafe` (the only mutation is the write-once promise, which is
  already poison-tolerant via `PoisonError::into_inner`, `promise.rs:28`).
- **A command whose caller never waits still goes terminal**: the watcher owns
  completion independent of any caller (`namespace-execution.md:456`).

### 5.4 Cancel vs. natural exit (Hard Problem #4)

`cancel()` is a `killpg(pgid)` issued from the **caller** thread
(`write_command_stdin`, on Ctrl-C `\u{3}` / Ctrl-D `\u{4}`,
`write_command_stdin.rs:55-57`) while the watcher blocks in `wait_completion()`.

- **Independence / responsiveness.** The child runs in its own process group
  (`process_group(0)`, `command/src/pty.rs:345`); `cancel()` holds **no** lock
  and is just `PtyMaster::cancel()` → the boxed `terminate_process_group(pgid)`
  action (`phase-2-spec.md` §2.4; SIGTERM→50 ms→SIGKILL, relocated from
  `command/src/pty.rs:485-490`). The kill unblocks `wait_completion()`'s
  `child.wait()`; the watcher needs no mediation.
- **Idempotency.** `cancel()` may be called repeatedly; `killpg` on a dead group
  is a harmless `ESRCH`. The terminal status is decided once by `complete`/`resolve`
  (write-once promise, `promise.rs:27-36`).
- **Terminal-status override (the engine-side cancel knowledge).** Phase 2 keeps
  `RunnerOutcome::status()` a pure wire parse (`phase-2-spec.md` §2.6). Phase 3
  restores the design's "cancel known engine-side" override
  (`namespace-execution.md:248-250`, today at `command/src/pty.rs:123-129`,
  `kill==Cancelled ⟹ status "cancelled", exit 130`) by:
  - adding a per-exec `cancelled: Arc<AtomicBool>` set by `cancel()` (alongside
    the `killpg` action) and read by the watcher;
  - the watcher passes it into `RunnerOutcome::new(run_result, cancelled)`;
    `status()` returns `Cancelled` (and `exit_code()` returns `130`) when the flag
    is set, else the wire parse. This keeps the override **in `status()`** as the
    design specifies and is the cancel-path Bridge (the fake sets the same flag).
  - the override happens *before* `finalize`, so `ExecCommand::finalize` sees a
    `Cancelled` outcome and produces `CommandTerminalResult{status: Cancelled,
    exit_code: 130, …}`, identical to today.

### 5.5 Drop semantics (Hard Problem #5)

Dropping a registry-held `CommandExecution` (service shutdown, or eviction) must
not kill a running child and must not leak threads/transcript:

- **`PtyMaster::drop`** closes the master fd. The PTY reader thread is blocked in
  `poll(-1)`; the slave is still held by the child, so closing only the *master*
  does **not** EOF the reader. Therefore drop is **not** how the reader ends —
  the reader ends when the **child** exits (all slave holders gone → hangup →
  `poll` returns → `read` 0 → loop break, `command/src/pty.rs:423-437`). The
  reader is detached; on `CommandExecution` drop while still running, the reader
  keeps draining to the file until the child exits, then exits on its own. No
  leak: it is bounded by child lifetime.
- **`CommandExecution`/`InteractiveExecution`/`ExecutionHandle` drop** drops the
  `Arc<promise>` clone (the watcher holds another), the `PtyMaster`, and the
  `transcript_path` (a `PathBuf`, no fd). It does **not** signal `cancel` — drop
  is **not** cancellation (`namespace-execution.md` Hard-problem framing). A child
  still running after its handle is dropped continues to terminal via the watcher
  (the watcher owns the `RunnerChild`, not the handle).
- **child + transcript reaping.** The child is reaped by `wait_completion()` on
  the watcher (`namespace-execution.md:438-442`). The transcript **file** is
  reaped by the command-artifact cleanup that already exists
  (`CommandConfig.scratch_root/<id>/transcript.log`, written via
  `CommandProcessSpawn::prepare`, `command/src/process.rs:262-276`) — Phase 3 does
  not change transcript-file lifetime (no new eviction; completed entries persist
  as today, `process_store.rs:342-357`).

### 5.6 Admission window (Hard Problem #6)

Sequence `try_reserve → spawn → attach`, with release on any spawn failure:

```text
// inside engine.run_shell_interactive:
registry.try_reserve(&id)?;                              // (lock 2) active<256 else Err(Admission)
let (child, pty) = match launcher.spawn_pty(req) { Ok(x) => x, Err(e) => { registry.abort(&id); return Err(e); } };
registry.attach_pgid_only(&id /* internal: nothing for V yet */);   // engine reserves; V attached by caller
observer.on_running(&id);                                // (lock 4)
spawn watcher;                                           // §5.2
return InteractiveExecution{...};
// back in exec_command (caller), still holding workspace_lifecycle_admission:
self.commands.insert(id, CommandExecution::new(exec, transcript_path, ws_id, started_at));  // engine.attach (lock 2)
```

- `try_reserve` inserts the reservation and increments `active` under one lock —
  no TOCTOU; concurrent starts cannot both take the last slot. Maps onto today's
  `CommandProcessStore::try_reserve` (`process_store.rs:52-73`, `compare_exchange`)
  and `begin_workspace_lifecycle_admission` (`core.rs:166-172`).
- On `spawn` failure the engine `abort`s (removes the reservation, `active--`) so
  **no admission leaks**. On a *post-spawn* failure visible to the caller (e.g.
  `run_shell_interactive` returns `Err` after the watcher already started — not
  possible in this design, the watcher only starts on success), the caller path
  does the one-shot destroy (§5.3) and `engine` has already released admission.
- `max_active = 256` (the relocated `DEFAULT_MAX_ACTIVE_COMMANDS`,
  `process_store.rs:19`) is passed to the engine the command service constructs
  (§7.3). `CommandConfig` has no such field (`config.rs:4-14`), so it stays a
  constant.

### 5.7 One id space (Hard Problem #7)

`engine.allocate_id()` mints the single `NamespaceExecutionId` (an `AtomicU64`
counter on the engine, `format!("namespace_execution_{n}")` — relocating the
ledger's allocator format `namespace_execution.rs:125-128`). Both the old
allocators are gone: `CommandProcessStore::allocate_command_session_id`
(`process_store.rs:46-50`, `cmd_{n}`) is deleted with the store; the ledger's
`allocate_namespace_execution_id` is no longer called by the command path.

- **wrap site:** the public face is `CommandSessionId(id.0.clone())` — built
  where `exec_command` returns the initial yield and stored in `CommandOutput`.
- **unwrap site:** `write_command_stdin`/`read_command_lines` receive a
  `CommandSessionId` and look up the registry by `NamespaceExecutionId(csid.0.clone())`.
- **Observable consequence:** the `command_session_id` string changes from
  `cmd_N` to `namespace_execution_N`. It is opaque to clients (passed back
  verbatim), but ~13 test literals assert `"cmd_1"`
  (`exec_command.rs:300,313,355,375,379,389,425,624,633,662,667,676,786`) and
  must update; the **observability `namespace_execution_id` value is unchanged**
  (already `namespace_execution_N`). Recommended over keeping `cmd_N` (which would
  instead churn the observability id surface). Open decision §15-D6.

### 5.8 Yield / quiet-period on a condvar (Hard Problem #8)

`wait_for_command_yield` (`helpers.rs:29-71`) must reproduce today's settle-or-
timeout UX (`wait_for_completion_yield`, `completion.rs:185-212`) using the
promise condvar instead of the 5 ms `take_exit` poll. The exact loop:

```text
fn wait_for_command_yield(id, yield_time_ms, start_offset, include_terminal_id) -> CommandOutput {
    let deadline = Instant::now() + yield_time_ms;
    let (mut last_off, mut last_change) = (start_offset, Instant::now());
    loop {
        // running-vs-terminal is the promise, not a poll:
        if self.commands.with_value(&id, |c| c.is_finished()).unwrap_or(true) {
            return self.completed_command_output(id, include_terminal_id);   // resolved() + transcript window
        }
        let off = self.commands.with_value(&id, CommandExecution::output_len).unwrap_or(last_off);
        let now = Instant::now();
        if off != last_off { last_off = off; last_change = now; }
        if off > start_offset && now.duration_since(last_change) >= QUIET_MS { // 50 ms settle
            return self.running_command_output(id, include_terminal_id);
        }
        if now >= deadline { return self.running_command_output(id, include_terminal_id); }
        // block on the promise up to the smaller of (next 50 ms slice, deadline):
        let slice = min(QUIET_MS, deadline - now);
        self.commands.with_value(&id, |c| c.exec.wait_timeout(slice));   // condvar, NOT a 5 ms sleep
    }
}
```

- `QUIET_MS = 50 ms` (today `completion.rs:16`); the 5 ms `COMPLETION_POLL`
  (`completion.rs:15`) is gone. The condvar `wait_timeout` wakes immediately on
  completion (no up-to-5 ms latency) yet re-checks transcript length each ≤50 ms
  slice, so the observable settle/quiet behavior is preserved.
- `running_command_output` advances the cursor exactly as today
  (`helpers.rs:118-122`): read `transcript_window(cursor, …)`, set
  `advance_snapshot_offset(window.next_offset)`.
- A finalize that resolves the promise `Err` is surfaced by
  `completed_command_output` mapping `resolved() == Some(Err(_))` to
  `CommandServiceError::CommandFinalizationFailed` (§5.3).

---

## 6. Sequence diagrams

### 6.1 `exec_command` happy path

```text
caller                         engine                       watcher(thread)        pty-reader(thread)        ledger
  |-- validate cmd ----------->|                                                                              |
  |-- resolve/create session ->|                                                                              |
  |-- take admission mutex --->|                                                                              |
  |-- ensure !remount_pending->|                                                                              |
  |-- id = allocate_id() ------>|                                                                             |
  |-- ledger.begin(id,ws,name,origin) ----------------------------------------------------------------> Starting
  |-- run_shell_interactive(ExecCommand, target, id) -->|                                                     |
  |                            try_reserve(id) [lock2]   |                                                     |
  |                            spawn_pty -> child,master ----------------------------> open file, poll(-1)     |
  |                            on_running(id) ----------------------------------------------------------> Running
  |                            spawn watcher ---------->  wait_completion()...                                 |
  |<-- InteractiveExecution ---|                          (blocked)                                            |
  |-- commands.insert(id, CommandExecution)  [engine.attach, lock2]                                           |
  |-- drop admission mutex                                                                                     |
  |-- wait_for_command_yield(CommandSessionId(id.0), yt, 0, false)  (condvar, §5.8)                            |
  |        ... child exits -------------------------->  wait_completion()->RunResult                          |
  |                                                     finalize() [destroy one-shot if OneShot, trace]        |
  |                                                     complete(id,status,exit) [lock2]                       |
  |                                                     resolve(Ok(result)) [lock3]                            |
  |                                                     on_terminal(id,status,exit) ------------------> Terminal
  |<-- CommandOutput (Running or terminal per yield) --|                                                       |
```

### 6.2 `write_command_stdin` + yield

```text
caller
  |-- csid -> id; commands.with_value(id, |c| (c.is_live?, c.workspace_session_id, c.output_len)) -> start_offset
  |-- if !kill: ensure !remount_pending(ws_id)
  |-- commands.with_value(id, |c| c.write_stdin(bytes))   // PtyMaster non-blocking write (lock5)
  |-- wait_for_command_yield(csid, yt, start_offset, true)   // §5.8
```

### 6.3 Ctrl-C / Ctrl-D cancel

```text
caller                                   watcher(blocked in wait_completion)
  |-- is_kill_input(stdin) == true
  |-- commands.with_value(id, |c| c.cancel())  // sets cancelled flag + killpg(pgid); NO lock across it
  |          killpg --------------------------> child group dies -> wait_completion() returns RunResult
  |                                             RunnerOutcome::new(rr, cancelled=true) -> status=Cancelled,exit=130
  |                                             finalize -> CommandTerminalResult{Cancelled,130}; complete; resolve; on_terminal
  |-- wait_for_command_yield(csid, 1000, start_offset, true) -> terminal CommandOutput(Cancelled)
```

### 6.4 One-shot destroy on finalize

```text
watcher: wait_completion()->rr
         outcome = RunnerOutcome::new(rr, cancelled)
         ExecCommand::finalize(outcome):
            result = CommandTerminalResult{status, exit, total_time}
            match session_disposition {
               ExistingSession => {}                                      // no destroy
               OneShot{handler} => workspace.destroy_session(*handler, default)?  // Err -> NamespaceExecutionError::Finalize
            }
            emit finalization trace (if Some) -> async_trace_sink
            Ok(result)
         complete(id, result.status, result.exit) ; resolve(Ok(result)) ; on_terminal(...)
```

### 6.5 `read_command_lines` on a terminal command

```text
caller
  |-- limit = validate_read_limit(input.limit)   // 1..=1000, else InvalidCommand (read_command_lines.rs:34-44)
  |-- csid -> id
  |-- commands.with_value(id, |c| {
  |        if !c.is_finished() {                              // RUNNING
  |            let w = c.transcript_window(start, limit)?;    // file
  |            Ok(command_output(w, Some(csid), Running, None, c.elapsed(), c.elapsed()))
  |        } else {                                           // TERMINAL
  |            match c.terminal_result() {                    // resolved(), non-consuming
  |               Some(Ok(r)) => { let w=c.transcript_window(start,limit)?;
  |                                Ok(command_output(w, csid_if_more, map(r.status), Some(r.exit_code),
  |                                                  c.elapsed(), r.command_total_time_seconds)) }
  |               Some(Err(e)) => Err(CommandFinalizationFailed{..})
  |               None => unreachable (is_finished ⟹ resolved Some, §5.2)
  |            }
  |        }
  |    }).ok_or(CommandNotFound)?
```

---

## 7. File-by-file change plan

Legend: **ADD** new file · **DELETE** removed · **EDIT** in place · **RENAME**.
Each DELETE is paired with the live-reader evidence proving it safe (every
reference verified against the live tree while authoring; line numbers cited).

### 7.1 Engine crate `crates/sandbox-runtime/namespace-execution/` (⚠ not in the migration's Phase-3 "crates touched"; §15-D1)

| File | Change | Sketch | Rationale |
|---|---|---|---|
| `src/engine.rs` | EDIT | `NamespaceExecutionEngine` → `<V=()>`; add `allocate_id` (`AtomicU64`), `attach`/`with_value`/`is_live`/`is_completed`/`live_values`; watcher wraps `finalize` in `catch_unwind`; `complete` before `resolve` | §3.6, §5.2-5.3 |
| `src/registry.rs` | EDIT | `ExecutionRegistry<V>` single map + counter (§3.6) | hold the caller handle |
| `src/promise.rs` | EDIT | add `wait_timeout(&self, Duration) -> bool`; add `resolved(&self) -> Option<Result<T,…>> where T: Clone` (non-consuming) | yield loop + terminal peek |
| `src/execution.rs` | EDIT | forward `wait_timeout` + `resolved` on `ExecutionHandle`/`InteractiveExecution`; `output_len`/`read_output_since` file-backed | §2.1, §3.5 |
| `src/shell.rs` | EDIT | default method `transcript_path(&self) -> Option<&Path> { None }`; `RunnerOutcome::new(RunResult, cancelled: bool)`; `status()` cancel override | §3.5, §5.4 |
| `src/pty.rs` | EDIT | `PtyMaster::spawn(.., sink: Option<PathBuf>)`; reader appends to file when `Some` | §3.5 |
| `src/launcher.rs` | EDIT | thread `transcript_path` + `cancelled: Arc<AtomicBool>` into `spawn_pty`; cancel action sets the flag then `killpg` | §5.4 |
| `Cargo.toml` / `src/lib.rs` | EDIT | add `time` (transcript prefixer, if not already from Phase 2); `test-support` feature re-exports a public fake launcher + `with_launcher` | §12 |

### 7.2 `crates/sandbox-runtime/command/`

| File | Change | Sketch | Rationale |
|---|---|---|---|
| `src/exec.rs` | ADD | `ExecCommand: ShellOperation` + `SessionDisposition` (§3.1) | the strategy |
| `src/command_execution.rs` | ADD | `CommandExecution` (§3.2) + transcript/yield helpers | the registry value |
| `src/contract.rs` | EDIT | add `CommandTerminalResult` (§3.3) | relocated from operation |
| `src/lib.rs` | EDIT | `mod exec; mod command_execution;` + `pub use` of `ExecCommand`, `SessionDisposition`, `CommandExecution`, `CommandTerminalResult` | exports for operation |
| `src/process.rs`, `src/pty.rs` | **untouched** | — | dead-but-`pub`+tested until Phase 6 (§8.2) |

### 7.3 `crates/sandbox-runtime/operation/src/command/service/`

| File | Change | Evidence / sketch |
|---|---|---|
| `core.rs` | EDIT | replace `process_store: Arc<CommandProcessStore>` + `completion_sender` with `engine: Arc<NamespaceExecutionEngine<CommandExecution>>` (built in `from_parts` with `max_active=256`, observer = the ledger). Drop `spawn_completion_finalizer` call (`core.rs:78`), `process_store()`/`completion_sender()` getters; add a typed `commands` view over `engine`. Keep `workspace_lifecycle_admission`, `begin_workspace_lifecycle_admission`, `with_workspace_destroy_admission` (the reverse lookup now iterates `engine.live_values`). |
| `impls/exec_command.rs` | EDIT | flow of §6.1: `allocate_id`, `ledger.begin`, build `ExecCommand`, `target = NamespaceTarget::from(entry)`, `run_shell_interactive`, `commands.insert`, initial yield. Delete `ActiveCommandProcess`/`CommandCompletionPromise`/`into_active_record`/`CommandWorkspaceOwnership`/`CommandLifecycleState`/`CancellationState`/`FinalizationState` uses (`exec_command.rs:7-13,119-145,319-348`). Keep one-shot start-failure cleanup → `OneShotSessionCleanupFailed`. |
| `impls/write_command_stdin.rs` | EDIT | §6.2; drop the `cancellation`/`lifecycle_state` writes (`write_command_stdin.rs:30-40`); cancel = `commands.with_value(id, |c| c.cancel())`. |
| `impls/read_command_lines.rs` | EDIT | §6.5 via the registry view; `validate_read_limit` (1..=1000) unchanged (`read_command_lines.rs:34-44`). |
| `service/contract.rs` | EDIT | merge `CommandYield`+`CommandLinesOutput`+`CommandOutputSnapshot` → `CommandOutput` (§3.4); delete `CommandCompletionWaitOutcome` use; keep `CommandStatus`, `CommandSessionId`, inputs, `CommandFinalizedMetadata`/`CommandPublish*` (still referenced by the CLI publish path `command_operations.rs:361,384` — **not** Phase-3 deletions). |
| `service/helpers.rs` | EDIT | rewrite `wait_for_command_yield` on the condvar (§5.8); delete `wait_for_completed_record` use + the `CommandCompletionWaitOutcome` match (`helpers.rs:9,56-70`); `command_output(...)` builder for the merged DTO. |
| `service/transcript.rs` | EDIT | `command_output(window, …)` for the merged DTO; window helpers now call `CommandExecution::transcript_window` / `required_transcript_window` (the `CommandTranscriptStore`/`RetainedCommandTranscript` wrappers are deleted with the store — fold their 2 lines here). |
| `service/status_lookup.rs` | **DELETE** | its three lookups move to the `commands` registry view; the `FinalizationState::Failed` branch (`status_lookup.rs:30-36`) becomes the resolved-`Err` mapping (§5.3). |
| `service/process_store.rs` | **DELETE** | readers all rewritten: `core.rs`, `completion.rs`, `finalize.rs`, `status_lookup.rs`, `helpers.rs`, `impls/*`, `transcript.rs`, `quiesce.rs` (§7.7). No reader survives after this plan. |
| `service/completion.rs` | **DELETE** | `CommandCompletionPromise`/`Sender`/`spawn_completion_finalizer`/`wait_for_completion_yield`/`wait_for_completed_record`. Readers: `core.rs:19,78`, `launch.rs:8-9,25,35`, `helpers.rs:9,63`, `process_store.rs:17,281`, `exec_command.rs:7,119` — all deleted/rewritten here. Test readers (`exec_command.rs`, `command_remount.rs`, `command_transcript_rows.rs`, `workspace_remount.rs`, `support/mod.rs`) migrate in §12. |
| `service/finalize.rs` | **DELETE** | `complete_terminal_command_with_services` logic → `ExecCommand::finalize` (§3.1) + the ledger observer (§9). The trace span name is preserved (§9.4). Readers: `completion.rs:12,154,167` (deleted). |
| `service/launch.rs` | **DELETE** | `CommandLaunchDriver`/`RealCommandLaunchDriver` — the test seam moves to the engine launcher (§12). Readers: `core.rs:4,26,73,152`, `test_support.rs`, and many tests (§12). |
| `service.rs` / `mod.rs` | EDIT | drop the `mod`/`pub use` of the deleted files (`service.rs:1-25`, `mod.rs:6-17`); export `CommandOutput`; stop exporting `CommandLaunchDriver`/`RealCommandLaunchDriver`/`CommandCompletion*`/`Active*`/`Completed*`/`Command{Lifecycle,Workspace,...}`. |
| `service/test_support.rs` | EDIT | the `command_service_with_launch_driver_*` fns (`test_support.rs:12,28,45,62`) take an `Arc<NamespaceExecutionEngine<CommandExecution>>` (fake-launcher) instead of `Arc<dyn CommandLaunchDriver>` (§12). |

### 7.4 `operation/src/namespace_execution.rs` (the ledger / observer) — see §9

RENAME `NamespaceExecutionStore` → `NamespaceExecutionLedger`; `impl
ExecutionObserver`; field `request_id` → `origin_request_id` (`:40`,`:73`,`:156`).

### 7.5 `operation/src/services.rs`, `lib.rs`, `command/error.rs`

- `services.rs:7,18,43,86` + `core.rs:6,25,45,…`: `NamespaceExecutionStore` →
  `NamespaceExecutionLedger` (the shared `Arc`).
- `lib.rs:23` re-export rename.
- `command/error.rs:92-99`: `OneShotWorkspaceCleanupFailed` → `OneShotSessionCleanupFailed`.

### 7.6 `crates/sandbox-daemon/src/observability/namespace_execution.rs` (⚠ not in the migration's Phase-3 "crates touched"; §15-D1)

EDIT **one** line (`:43`): `request_id: execution.origin_request_id.clone().map(bound_id)`
— read the renamed field; **keep** the emitted serialized name `request_id`
(`NamespaceExecutionTraceRecord.request_id`, observability `records.rs:273`) so the
observable surface is byte-for-byte unchanged (§9.3).

### 7.7 `operation/src/workspace_remount/service/command/{coordinator,quiesce}.rs` — minimal Phase-3 edits only

These are Phase-5 files, but they currently **write** the soon-deleted
`ActiveCommandProcess` fields (`coordinator.rs:66-69`, `quiesce.rs:145-199`) and
hold `process_store: Arc<CommandProcessStore>` (`quiesce.rs:104`). Deleting
`process_store.rs` breaks them. The **field-by-field investigation** shows every
one of `lifecycle_state` / `cancellation` / `remount_switch_state` is **write-only**
(no read-back), and `remount_cancellation` is read only via `same_token`
within quiesce itself. Phase-3 minimal action (NOT the Phase-5 rewrite):

- Phase 3 deletes `CommandProcessStore`, so quiesce/coordinator must stop touching
  it. The smallest change that keeps the workspace building **and** preserves
  remount behavior until Phase 5 is to have the coordinator/quiesce reach live
  commands through the **engine registry view** for the *process-group* work they
  already do (`active.process.cancel_process()` → `commands.with_value(id, |c|
  c.cancel())`; `active_command_session_ids_for_workspace_session` →
  `commands.live_ids_for_workspace(ws_id)` via `engine.live_values`), and **drop**
  the write-only field writes (`lifecycle_state`, `cancellation`,
  `remount_switch_state`) and the `remount_cancellation` *mirror* read — the
  coordinator already owns the live `RemountCancellationToken` + `switch_state`
  (`quiesce.rs:105-106`, `CommandRemountQuiesce.cancellation`/`switch_state`), so
  the mirrors are redundant (this is the design's disposition,
  `namespace-execution.md:470-471`).
- This is *more* than the migration assigns to Phase 3 but is **forced** by the
  store deletion; it is strictly a subset of the Phase-5 work (no new behavior).
  Flagged §15-D7. If a cleaner cut is preferred, the alternative is to defer the
  store deletion's quiesce-coupling by keeping a *temporary* shim — **rejected**
  (the migration bans shims, `migration-phases.md:34`).

> The full quiesce/resume rewrite (token-on-coordinator + id-set, embed
> `ProcessGroupInspection`, delete `merge_report`) remains **Phase 5**
> (`migration-phases.md:210-226`). Phase 3 only severs the `CommandProcessStore`
> dependency.

---

## 8. Safe edit order (build green at every step)

The crates compile bottom-up: `namespace-execution` → `command` → `operation` →
`daemon`. Edits are ordered so each `cargo build` stays green.

1. **Engine crate (§7.1).** Add `allocate_id`, generic registry/engine,
   `wait_timeout`/`resolved`, `transcript_path` hook + file-backed PtyMaster,
   `RunnerOutcome::new(.., cancelled)` + override, `test-support` fakes. The engine
   crate builds and its unit tests pass with **no** downstream change yet (nothing
   consumes the new surface). *Self-contained.*
2. **`command` crate (§7.2).** Add `CommandTerminalResult` to `contract.rs`, then
   `command_execution.rs`, then `exec.rs` (depends on both). `command` builds;
   `process.rs`/`pty.rs` untouched (still compiled + tested). *Self-contained.*
3. **Ledger + observer (§7.4, §9).** Rename `Store→Ledger`, `impl
   ExecutionObserver`, `request_id→origin_request_id` in
   `operation/src/namespace_execution.rs`; update `services.rs`/`lib.rs`/`core.rs`
   read sites; update **the one daemon read line** (§7.6) in the same step (a
   field rename forces all readers). After this step the whole workspace builds
   with the *old* command service still using `CommandProcessStore` (the ledger
   gains `impl ExecutionObserver` additively; nothing breaks).
4. **Command service core wiring (§7.3 `core.rs`).** Introduce the
   `engine: Arc<NamespaceExecutionEngine<CommandExecution>>` field and the typed
   `commands` view **alongside** the store temporarily? — **No.** To avoid a
   dual-write shim (banned), do steps 4–6 as **one commit**: rewrite `core.rs` +
   the three impls + `helpers.rs`/`transcript.rs`/`contract.rs`, delete
   `process_store.rs`/`completion.rs`/`finalize.rs`/`launch.rs`/`status_lookup.rs`,
   and apply the minimal quiesce/coordinator edits (§7.7), in a single change. The
   intermediate state does not compile by design (the store and the engine cannot
   both be the source of truth); the *commit boundary* is green.
5. **Tests (§12).** Migrate `test_support.rs` + the five test files in the same
   commit as step 4 (they reference the deleted seam).
6. **`command/error.rs` rename** (§7.5) — fold into step 4 (a reader is in
   `exec_command.rs`).

### 8.1 "Last edit before file X is deletable"

- `process_store.rs` deletable once `core.rs`, the three impls, `helpers.rs`,
  `transcript.rs`, `status_lookup.rs`, and quiesce/coordinator (§7.7) no longer
  name `CommandProcessStore`/`ActiveCommandProcess`/`Completed*`/`Command{Terminal,Transcript}*`.
- `completion.rs` deletable once `core.rs` (no `spawn_completion_finalizer`),
  `helpers.rs` (no `wait_for_completed_record`), `launch.rs` (deleted),
  `exec_command.rs` (no `CommandCompletionPromise`) are rewritten.
- `finalize.rs` deletable once `completion.rs` is deleted (its only caller).
- `launch.rs` deletable once `core.rs` (no `CommandLaunchDriver` field) and
  `test_support.rs` are rewritten.
- `status_lookup.rs` deletable once `write_command_stdin.rs`/`read_command_lines.rs`/`helpers.rs`
  use the registry view.

### 8.2 Clippy `-D warnings` for the dead `command/src/{pty,process}.rs`

After Phase 3 routes the command through the engine, `operation` no longer imports
`CommandProcess`/`spawn_current_exe_ns_runner`. Those symbols remain in
`command/src/{process,pty}.rs`. Clippy stays green **without** special handling
because:

- the symbols are **`pub`** (`command/src/lib.rs:27` `pub use process::…`; the
  modules are `pub mod process` / `mod pty` used by `process.rs`) — an unused
  `pub` item is not dead-code-linted across crates;
- the **command crate's own unit tests** still exercise them
  (`command/tests/unit/{process,pty}.rs`), so they have live callers within their
  crate.

Phase 3 therefore leaves both files (and their exports) **exactly as is**; they
are deleted in Phase 6 (`migration-phases.md:234-236`). No `#[allow(dead_code)]`,
no feature gate.

---

## 9. Observer wiring

### 9.1 `NamespaceExecutionStore` → `NamespaceExecutionLedger : ExecutionObserver`

`operation/src/namespace_execution.rs:16` rename; add:

```rust
impl ExecutionObserver for NamespaceExecutionLedger {
    fn on_running(&self, id: &NamespaceExecutionId) {
        let _ = self.mark_namespace_execution_running(id);            // existing :170-187
    }
    fn on_terminal(&self, id: &NamespaceExecutionId,
                   status: NamespaceExecutionTerminalStatus, exit_code: Option<i64>) {
        let _ = self.complete_namespace_execution(id, CompleteNamespaceExecution {
            terminal_status: status, exit_code, error_kind: None, error_message: None,  // existing :189-244
        });
    }
}
```

- `begin_namespace_execution` (`:130-168`, sets `Starting`) **stays called by the
  operation layer** in `exec_command` (it owns `workspace_session_id`,
  `namespace-execution.md:500-505`); the engine never sees it. The
  `Starting→Running→Terminal` transitions are preserved: `begin`(operation) →
  `on_running`(engine) → `on_terminal`(engine).
- The engine holds `observer: Arc<dyn ExecutionObserver>` = the same
  `Arc<NamespaceExecutionLedger>` the operation layer holds (shared via
  `from_parts`, today's shared-`Arc` pattern `core.rs:105-115`,
  `services.rs:86-98`). Construction: `from_parts` builds
  `NamespaceExecutionEngine::new(Arc::clone(&ledger) as Arc<dyn ExecutionObserver>,
  256)`.
- Today's terminal write happens in `finalize.rs:182-196`
  (`complete_namespace_execution`) and the running write in
  `exec_command.rs:90-93` (`mark_namespace_execution_running`). Both move behind
  the observer; behavior is identical.

### 9.2 `request_id` → `origin_request_id` (internal only)

Rename the field on `NamespaceExecutionRecord` (`:40`) and `BeginNamespaceExecution`
(`:73`); update the in-module write (`:156`) and the `exec_command` begin call
(`exec_command.rs:84`, `request_id: origin_request_id` → `origin_request_id:
origin_request_id`). The external-origin value still flows from
`exec_command_with_origin_request_id` (`exec_command.rs:33-37`).

### 9.3 Serialized surface unchanged (byte-for-byte)

- The daemon trace record `NamespaceExecutionTraceRecord` keeps its serialized
  field name `request_id` (`crates/sandbox-observability/src/records.rs:273`); only
  the daemon **read** changes (`crates/sandbox-daemon/src/observability/namespace_execution.rs:43`:
  `execution.request_id` → `execution.origin_request_id`). JSON keys are unchanged.
- The `active_namespace_executions` snapshot (`snapshot_active_namespace_executions`,
  `:246-266`) does **not** include `request_id`/`origin_request_id` at all
  (`RuntimeNamespaceExecutionSnapshot`, `:84-91`) — the snapshot surface is wholly
  untouched. `operation_name` ("exec_command"), the lifecycle strings
  ("starting"/"running"/"terminal", `:60-66`), and `namespace_execution_id`
  ("namespace_execution_N", §5.7) are unchanged.

### 9.4 Finalization async-trace preserved

Today the finalization trace (span `complete_terminal_command_with_services` +
`CommandFinalizationTraceMetadata{origin_request_id, workspace_session_id,
command_session_id, finalizer_error}`) is emitted from the shared finalizer thread
(`completion.rs:152-182`). It is asserted by tests
(`operation/tests/operation_trace.rs:338`,
`crates/sandbox-daemon/tests/unit/observability.rs:421,533,556,580,606,627`,
`crates/sandbox-observability/tests/schema.rs:233`). In the engine model finalize
runs on the per-exec watcher, so `ExecCommand` carries the trace context
(`finalization_trace: Option<CommandFinalizationTrace>` holding the
`AsyncTraceSink` + `origin_request_id` + `workspace_session_id` +
`command_session_id`); `ExecCommand::finalize` opens an `OperationTrace`, measures
the `complete_terminal_command_with_services` span around the one-shot destroy,
and sends `(trace, metadata)` to the sink — the same span name and metadata. The
sub-span set may shift (no `complete_command_record` step — the registry/promise
replaces the store write); the top span and metadata are preserved. **Risk
flagged §15-D8** (the migration docs do not address this trace; some assertions in
`operation_trace.rs`/`observability.rs` may need the sub-span list updated).

### 9.5 Distinctness test (pins origin ≠ execution id)

`operation/tests/exec_command.rs:528`
`namespace_execution_request_id_comes_from_runtime_request_not_runner_request`
asserts `completed[0].request_id.as_deref() == Some("req-external")` and `!=
Some("cmd_1")` (`:565-566`). After the rename: `completed[0].origin_request_id`,
and the `"cmd_1"` literal becomes the new id format (§5.7). This is the test that
demonstrates `origin_request_id` is the external origin id, distinct from the
execution id — kept, updated for the two renames.

---

## 10. Cross-phase coordination

### 10.1 `From<WorkspaceEntry> for NamespaceTarget` — must land in Phase 3

`exec_command` builds `target = NamespaceTarget::from(handler.handle.entry()?)`
(§6.1). The migration schedules this impl for **Phase 4**
(`migration-phases.md:190`), but Phase 3 **consumes** it. Resolution: the impl
lands in **Phase 3**, in the `workspace` crate (orphan rule OK: `WorkspaceEntry`
is local to `workspace`, `workspace/src/model.rs:296-302`; `NamespaceTarget` is in
the engine crate `workspace` already depends on, design graph `:521-522`):

```rust
// workspace/src/model.rs (additive, ~18 LOC)
impl From<WorkspaceEntry> for NamespaceTarget {
    fn from(e: WorkspaceEntry) -> Self {
        Self { workspace_root: e.workspace_root, layer_paths: e.layer_paths,
               upperdir: Some(e.upperdir), workdir: Some(e.workdir),
               ns_fds: e.ns_fds.into() }   // WorkspaceEntryFds -> protocol::NsFds (model.rs:333-342)
    }
}
```

`WorkspaceEntry.upperdir`/`workdir` are non-`Option` `PathBuf`
(`model.rs:299-300`) but `NamespaceTarget` holds `Option<PathBuf>`
(`target.rs:11-12`), matching today's `Some(entry.upperdir)`
(`command/src/process.rs:309-310`). Phases 3 and 4 run in parallel
(`migration-phases.md:49-51`); to avoid a merge conflict the impl is **owned by
Phase 3** and Phase 4 must **not** re-add it. Flagged §15-D9.

### 10.2 Phase 5 must be able to query live interactive executions per workspace

Phase 5 moves remount quiesce/resume onto engine-registry queries
(`namespace-execution.md:484-488`: "asks the engine registry for live interactive
executions in a workspace … pgid/cancel"). The §3.6 registry shape supports this:
`engine.live_values(|c: &CommandExecution| …)` returns live handles; the
workspace→id filter uses `CommandExecution::workspace_session_id` (or the ledger's
`workspace_session_id`, `namespace_execution.rs:38`). The §7.7 Phase-3 minimal
edit already routes coordinator/quiesce through this view, so Phase 5 is a
*refinement* (token-on-coordinator + id-set), not a re-plumb. The registry's
`live_values` and `with_value(id, |c| c.cancel())` are the Phase-5 hooks; this
spec's `CommandExecution`/registry shape does **not** preclude them.

---

## 11. Invariants preserved

| Invariant | Upholding mechanism | Guarding test |
|---|---|---|
| One-shot vs existing session | `SessionDisposition`; `ExecCommand::finalize` destroys `OneShot`, `ExistingSession` no-op (§3.1, §6.4); start-failure cleanup → `OneShotSessionCleanupFailed` (§5.3) | `exec_command.rs` one-shot destroy + `:265` cleanup-failure tests |
| Remount-pending guard | `write_command_stdin`/`exec_command` call `ensure_workspace_session_not_remount_pending(ws_id)` before write/start (§6.2; `helpers.rs:135-150`) | `command_remount.rs` pending-guard tests |
| Ctrl-C / Ctrl-D kill | `is_kill_input` (`\u{3}`/`\u{4}`) → `cancel()` → flag+`killpg`; status override → `Cancelled`/130 (§5.4) | `exec_command.rs`/`command_remount.rs` cancel tests |
| Yield / quiet-period | condvar `wait_timeout` + 50 ms transcript re-check; no 5 ms poll (§5.8) | `command_transcript_rows.rs`, `exec_command.rs` yield tests |
| `limit` validation `1..=1000` | `validate_read_limit` unchanged (`read_command_lines.rs:34-44`) | `read_command_lines` limit tests |
| Running-vs-terminal reads | `is_finished()` (promise); running→file window+`Running`, terminal→`resolved()`+file (§5.1, §6.5) | `read_command_lines`/`command_transcript_rows.rs` |
| Transcript content | file-backed transcript, prefix format + 1 MiB window kept (§3.5) | `command_transcript_rows.rs`, `command/tests/unit/transcript.rs` |
| Single `active_namespace_executions` row | one engine instance per producer, single ledger projection (§9); snapshot iterates the ledger's `active` map (`:246-266`) | `observability_snapshot.rs`, `namespace_execution.rs` tests |
| `origin_request_id` distinct from execution id | begin sets `origin_request_id` from the external request; engine sets the execution id (§5.7, §9.2) | `exec_command.rs:528` distinctness test |
| No `execution_kind`/`backing` axis | none added; `RunnerOutcome`/registry/launcher are internal, unserialized (§9.3) | `rg` absence grep (§13) |
| No second per-session map | the handle lives in the engine registry (`engine<CommandExecution>`), not the command service (§3.6) | `rg "CommandProcessStore"` absence (§13) |

---

## 12. Test plan

The repo keeps **no inline `#[cfg(test)]` tests in production sources**; unit
tests live in integration suites (`crates/sandbox-runtime/operation/tests/*`,
`crates/sandbox-runtime/command/tests/unit/*`). Phase 3 honors this.

### 12.1 The test seam moves from `CommandLaunchDriver` to the engine launcher

Today operation tests inject a fake via `CommandLaunchDriver`
(`support/mod.rs:46-167` `FakeLaunchDriver`, scripting
`ScriptedCommandYield::{Completed(exit), Running(output)}`), built through
`command_service_with_launch_driver_*` (`test_support.rs:12,28,45,62`). Phase 3
deletes `CommandLaunchDriver`. **Replacement:** the engine crate exposes, under its
`test-support` feature, a public **fake `NsRunnerLauncher`** (returning a fake
`RunnerChild` whose `wait_completion()` returns a scripted `RunResult`, and a
`PtyMaster` whose `cancel` trips the fake completion — `phase-2-spec.md` §2.1-2.4,
promoted from `#[cfg(test)]` to `test-support`) and `NamespaceExecutionEngine::with_launcher`.
The operation test harness builds
`Arc<NamespaceExecutionEngine<CommandExecution>>` from the fake launcher and
injects it via the rewritten `command_service_with_*` constructors (now taking the
engine, not a driver).

- `ScriptedCommandYield::Completed(exit)` → the fake child's `wait_completion()`
  returns the corresponding `RunResult` (status/exit/payload); the watcher
  finalizes → promise resolves → the yield returns terminal.
- `ScriptedCommandYield::Running(output)` → the fake feeds `output` to the PTY
  slave/transcript file and **does not** complete the child; the yield loop
  returns `Running` after the quiet period.
- `success_exit("…")` (`support/mod.rs:458-467`, a `CommandProcessExit`) → a
  `RunResult{exit_code:0, payload:{"status":"ok"}}` helper.

### 12.2 Tests that keep passing (behavior unchanged, possibly literal updates)

- Observability: `observability_snapshot.rs`, `operation/tests/namespace_execution.rs`
  (the ledger; rename `Store→Ledger`, `request_id→origin_request_id`),
  `crates/sandbox-daemon/tests/unit/observability.rs`,
  `crates/sandbox-observability/tests/schema.rs` — the serialized surface is
  unchanged (§9.3); the finalization-trace assertions may need sub-span updates
  (§9.4, §15-D8).
- `exec_command.rs` behavioral cases (initial yield, long-running yields a
  session id, one-shot destroy, start-failure cleanup) — re-pointed at the engine
  fake; `"cmd_1"` literals → the new id format (§5.7); `.request_id` →
  `.origin_request_id`.
- `read_command_lines`/`command_transcript_rows.rs` (running/terminal windows,
  limit validation, transcript content) — via the registry view.

### 12.3 Tests that move / are rewritten

- `support/mod.rs`: delete `FakeLaunchDriver` + `impl CommandLaunchDriver`; build
  services from the engine fake (§12.1). `build_services_with_launch_driver*` →
  `build_services_with_fake_runner*`.
- `command_remount.rs`, `workspace_remount.rs`, `command_transcript_rows.rs`,
  `exec_command.rs`: their `BlockingLaunchDriver`/`PendingGuardLaunchDriver`/
  `InactiveLaunchDriver`/`TranscriptLaunchDriver`/`MissingTranscriptLaunchDriver`/
  `*LaunchDriver` impls become engine-fake scripts (block the
  fake child for "blocking"; feed transcript bytes for "transcript"; etc.).

### 12.4 New tests

- `CommandExecution` running-vs-terminal read (`command/tests/unit/command_execution.rs`):
  with a fake interactive execution, assert running read uses the file window and
  terminal read uses `resolved()` without consuming (two terminal reads succeed).
- `ExecCommand::finalize` (`command/tests/unit/exec.rs`): `OneShot` calls
  `destroy_session`; destroy failure → `NamespaceExecutionError::Finalize`;
  `ExistingSession` does not destroy; cancel override → `Cancelled`/130.
- Engine (engine crate `tests/` under `test-support`): `attach`/`with_value`
  retains and reads `CommandExecution`; `complete` before `resolve` invariant
  (a reader seeing `is_finished()` sees `resolved()==Some`); admission release on
  spawn failure; the attach/complete race (complete before attach) is benign.

---

## 13. Verification

```sh
export PATH="$PWD/bin:$PATH"
cargo fmt --check
cargo check  -p sandbox-runtime-namespace-execution --tests
cargo test   -p sandbox-runtime-namespace-execution        # engine unit tests incl. new registry/peek/override
cargo check  -p sandbox-runtime-command --tests
cargo test   -p sandbox-runtime-command                    # CommandExecution / ExecCommand unit tests
cargo test   -p sandbox-runtime --tests                    # operation: command + observability
cargo check  -p sandbox-daemon                             # origin_request_id read-site
cargo clippy -p sandbox-runtime-namespace-execution --all-targets --no-deps -- -D warnings
cargo clippy -p sandbox-runtime-command                 --all-targets --no-deps -- -D warnings
cargo clippy -p sandbox-runtime                         --all-targets --no-deps -- -D warnings
cargo clippy -p sandbox-daemon                          --all-targets --no-deps -- -D warnings

# gutted store + satellites gone:
rg -n "CommandProcessStore|FinalizationState|CommandLifecycleState|CancellationState|CommandCompletionWaitOutcome|spawn_completion_finalizer|CommandLaunchDriver" \
  crates/sandbox-runtime/operation/src/command || echo "gone ✓"
# observability axis unchanged:
rg -n "execution_kind|runner_kind|active_executions|active_commands" crates/sandbox-runtime/operation/src || echo "axis clean ✓"
# command no longer forks directly (still defined for Phase 6):
rg -n "spawn_current_exe_ns_runner" crates/sandbox-runtime/operation crates/sandbox-runtime/command/src/exec.rs || echo "command path off the fork ✓"
# one id space — no cmd_N allocator:
rg -n "allocate_command_session_id|\"cmd_\\{" crates/sandbox-runtime/operation/src || echo "cmd_N allocator gone ✓"
# rename landed:
rg -n "OneShotWorkspaceCleanupFailed|NamespaceExecutionStore\b" crates/sandbox-runtime/operation/src || echo "renames landed ✓"
git diff --check
git diff --numstat
```

Host note (matches `phase-2-spec.md` §6): the dev host is darwin; the real fork
path is Linux-only, so the engine fake (and `PtyMaster` over a real `openpt` pair,
which runs on darwin) is the authoritative behavioral signal. If a pre-existing
host/Linux constraint blocks the parent `cargo test`, record the exact target +
message and confirm it reproduces on `main` before this phase.

---

## 14. Requirements traceability matrix

| Id | Requirement | Design element | Test | Verify |
|---|---|---|---|---|
| **P3-R1** | One id; `engine.allocate_id`; `CommandSessionId(id.0)`; no `cmd_N` allocator | §5.7, §3.6 | engine id test; `exec_command.rs` | `rg allocate_command_session_id` |
| **P3-R2** | Command runs through the engine; no spawn/promise/finalizer in the service | §3, §6.1 | `exec_command.rs` (engine fake) | `cargo test -p sandbox-runtime` |
| **P3-R3** | Handle retained in the engine registry; no second per-session map | §3.6 | engine `attach`/`with_value` test | `rg CommandProcessStore` |
| **P3-R4** | `ExecCommand: ShellOperation`; one-shot destroy + cancel override in `finalize`/`status()` | §3.1, §5.3, §5.4 | `command/tests/unit/exec.rs` | `cargo test -p sandbox-runtime-command` |
| **P3-R5** | `CommandExecution` = handle + cursor + ws-id + transcript path | §3.2 | `command/tests/unit/command_execution.rs` | idem |
| **P3-R6** | `CommandTerminalResult` trimmed `{status,exit_code,total_time}`, in `command` | §3.3 | unit | build |
| **P3-R7** | One `CommandOutput` DTO; `CommandCompletionWaitOutcome` deleted | §3.4 | `read_command_lines`/yield tests | `rg CommandCompletionWaitOutcome` |
| **P3-R8** | finalize→complete→resolve ordering; yield drops `wait_for_completed_record` | §5.2, §5.8 | engine ordering test | `rg wait_for_completed_record` |
| **P3-R9** | Yield on condvar (50 ms re-check), no 5 ms poll | §5.8 | `command_transcript_rows.rs` | `rg COMPLETION_POLL` |
| **P3-R10** | Cancel: `killpg` from caller, no lock across wait, override `Cancelled`/130, idempotent | §5.4 | cancel tests | test |
| **P3-R11** | Drop does not kill running child / leak threads / transcript | §5.5 | engine drop test | test |
| **P3-R12** | Admission `try_reserve→spawn→attach`; release on spawn failure; `max_active=256` | §5.6 | admission test | test |
| **P3-R13** | Ledger `impl ExecutionObserver`; `Store→Ledger` | §9.1 | `namespace_execution.rs` tests | `rg NamespaceExecutionStore` |
| **P3-R14** | `request_id→origin_request_id` internal; serialized surface byte-for-byte unchanged | §9.2-9.3 | `exec_command.rs:528`; daemon/observability tests | `rg request_id records.rs` |
| **P3-R15** | Finalization async-trace preserved (`complete_terminal_command_with_services` + metadata) | §9.4 | `operation_trace.rs`, daemon observability | test |
| **P3-R16** | Deletions safe (no live readers): `process_store`/`completion`/`finalize`/`launch`/`status_lookup`; write-only `CommandLifecycleState`/`CancellationState`/publish family | §7.3, §8.1 | build + absence greps | §13 |
| **P3-R17** | `From<WorkspaceEntry> for NamespaceTarget` in `workspace` (Phase 3 owns it) | §10.1 | `exec_command.rs` (real entry path / fake) | build |
| **P3-R18** | `OneShotWorkspaceCleanupFailed→OneShotSessionCleanupFailed`; `CommandWorkspaceOwnership→SessionDisposition` | §3.1, §7.5 | one-shot cleanup test | `rg` |
| **P3-R19** | Registry shape supports Phase-5 live-per-workspace queries | §3.6, §10.2 | engine `live_values` test | test |
| **P3-R20** | No `execution_kind`/`backing`; engine internals unserialized | §9.3 | observability tests | §13 axis grep |
| **P3-R21** | `command/src/{process,pty}.rs` stay (dead-but-pub+tested); clippy green | §8.2 | `command/tests/unit/*` | `cargo clippy` |
| **P3-R22** | File-backed transcript (engine PtyMaster sink + `ShellOperation::transcript_path`) | §3.5 | transcript tests | test |

---

## 15. Risks & open decisions (each with a recommended resolution; ⚠ = needs human sign-off)

- **D1 — Phase 3 must touch the engine crate AND the daemon crate.** The
  migration lists "crates touched: `command`, `operation/command`"
  (`migration-phases.md:44`), but Phase 3 must edit `namespace-execution`
  (`allocate_id`, generic registry, `wait_timeout`/`resolved`, transcript hook,
  cancel override, `test-support` fakes) and `sandbox-daemon` (one
  `origin_request_id` read line). **Recommended:** treat the engine + daemon edits
  as in-scope Phase-3 work; alternatively fold the engine additions into an
  amended Phase 2. ⚠
- **D2 — peek signature.** Design/prompt say `wait_timeout(&self) -> Option<&T>`;
  not implementable behind `Arc<Mutex>`. **Recommended:** `wait_timeout(&self,d)->bool`
  + `resolved()->Option<Result<T,…>>` (`T: Clone`). ⚠
- **D3 — generic engine `NamespaceExecutionEngine<V>`** diverges from Phase 2's
  stated non-generic engine. **Recommended:** accept the public type parameter
  (default `()`); rejected alternatives in §3.7. ⚠
- **D4 — drop `session_disposition` from `CommandExecution`** (write-only there).
  **Recommended:** drop it ("prefer less"); the design lists it
  (`namespace-execution.md:342`) but no reader exists.
- **D5 — `CommandTerminalResult` is relocated, not edited**; the design's file
  tree implies it is already in `command/src/contract.rs`. **Recommended:** move
  it from `operation/.../process_store.rs:335`; status type becomes
  `NamespaceExecutionTerminalStatus`.
- **D6 — id format.** Unifying `cmd_N` and `namespace_execution_N` changes one
  surface. **Recommended:** `engine.allocate_id` → `namespace_execution_N`
  (keeps the observability id stable; changes the opaque `command_session_id`
  string and ~13 test literals). Alternative: `cmd_N` (keeps command tests, churns
  observability id). ⚠
- **D7 — quiesce/coordinator minimal edit.** Deleting `CommandProcessStore` forces
  a Phase-3 touch of Phase-5 files. **Recommended:** route their process-group
  work through the registry view + drop the write-only mirrors (a strict subset of
  Phase 5); no shim. ⚠
- **D8 — finalization async-trace.** The migration docs don't address it; moving
  finalize to the watcher means `ExecCommand` carries the trace context and the
  sub-span set may shift. **Recommended:** preserve the top span name + metadata;
  update `operation_trace.rs`/daemon observability sub-span assertions as needed. ⚠
- **D9 — `From<WorkspaceEntry>` ownership** (Phase 3 vs the migration's Phase 4).
  **Recommended:** Phase 3 owns it; Phase 4 must not re-add (parallel-phase merge
  hazard). ⚠
- **D11 — the publish family is NOT fully deletable.** The migration says delete
  `CommandFinalizedMetadata`/`CommandPublishFinalization`/`CommandPublishStatus`
  (`migration-phases.md:162-164`), but they have **live readers** outside the
  store: `CommandPublishStatus` in the CLI (`command_operations.rs:384-389`
  `publish_status_name`) and `CommandFinalizedMetadata` in
  `CommandServiceError::CommandFinalizationFailed.finalized` (`error.rs:68`).
  Fully deleting them breaks the build. **Recommended:** keep the three types;
  delete only their **always-`None` usage in the active/completed records**
  (`process_store.rs:160,320,369`, `finalize.rs:266-273`) — which is what "always
  `None`/unread" actually refers to (design `:472`). Flagged as a contradiction
  with the migration's wording. ⚠
- **D10 — concurrent finalize.** Today one shared finalizer thread serializes
  one-shot destroys; the engine runs finalize per-exec (concurrent). Destroys are
  per-session-independent, and `workspace_lifecycle_admission` is **not** held on
  the watcher, so concurrent one-shot destroys are safe — but verify
  `WorkspaceSessionService::destroy_session` is internally synchronized (it is
  `&self`, Arc-shared). **Assumption A1.**

### Assumptions (minimized; verify before/while implementing)

- **A1** `WorkspaceSessionService::destroy_session(&self, …)` is safe to call
  concurrently from multiple watcher threads (Arc-shared, `&self`); grounded shape
  `workspace_session/service/impls/destroy_session.rs`, concurrency not directly
  tested here.
- **A2** Phase 2 un-gates `execution`/`promise`/`registry` from `test-support` and
  delivers the §2.2 "Phase-2-adds" set; this spec builds on that. If Phase 2 is
  amended to also include the D1 engine items, the §7.1 edits shrink accordingly.
- **A3** `time` is already an engine dep after Phase 2 (the relocated
  `TranscriptTimestampPrefixer` needs it); if not, add it in §7.1.

---

## 16. Definition of done & LOC delta

### 16.1 Definition of done

- `exec_command`/`write_command_stdin`/`read_command_lines` run through
  `Arc<NamespaceExecutionEngine<CommandExecution>>`; the command service owns no
  spawn/promise/finalizer/`FinalizationState` and no poll loop.
- `process_store.rs`, `completion.rs`, `finalize.rs`, `launch.rs`,
  `status_lookup.rs` deleted; write-only `CommandLifecycleState`/`CancellationState`
  and the always-`None` `CommandFinalizedMetadata` publish family removed from the
  active/completed records (the `CommandFinalizedMetadata`/`CommandPublish*` types
  themselves stay — still used by the CLI publish path, §7.3).
- One `CommandOutput` DTO; `CommandCompletionWaitOutcome` gone.
- Ledger `impl ExecutionObserver`; `Store→Ledger`; `request_id→origin_request_id`
  internal; serialized surface byte-for-byte unchanged; finalization trace
  preserved.
- `SessionDisposition`/`OneShotSessionCleanupFailed` renames landed; `From<WorkspaceEntry>`
  in `workspace`; one id space.
- All §13 commands pass (fmt, focused tests, clippy `-D warnings`, absence greps,
  `git diff --check`).

### 16.2 LOC delta (deletes exact via `wc -l`; adds/shrinks estimated — implementer reports `git diff --numstat`)

| Bucket | LOC |
|---|---|
| **Deleted outright** — `process_store.rs` 382 · `completion.rs` 241 · `finalize.rs` 275 · `launch.rs` 74 · `status_lookup.rs` 50 | **−1,022** |
| **Shrunk in place** — `exec_command.rs` (~373→~190) · `helpers.rs` (~179→~120) · `core.rs` (~223→~180) · `contract.rs` (~108→~75) · `write_command_stdin.rs` (~57→~42) · `read_command_lines.rs` (~62→~50) · `transcript.rs` (~90→~55) · `namespace_execution.rs` (~+10 observer) · `mod.rs`/`service.rs` (~−25) · quiesce/coordinator (~−15) | **≈ −330** |
| **Added (`command`)** — `exec.rs` (~70) · `command_execution.rs` (~130) · `contract.rs` `CommandTerminalResult` (~12) | **≈ +212** |
| **Added (engine, §7.1)** — registry generic + `allocate_id` + `attach`/`with_value`/`live_values` (~80) · `resolved`/`wait_timeout` forwards (~25) · `transcript_path` hook + file sink (~40) · cancel override (~20) · `test-support` fakes promotion (~60) | **≈ +225** |
| **Added (`workspace`)** — `From<WorkspaceEntry>` (~18) · daemon read-site (~0) | **≈ +18** |
| **Net Phase-3 repo delta** | **≈ −897** (range −800 … −1,000) |

The load-bearing outcome is not the deletion but the structure: a command is now a
`ShellOperation` strategy + a registry value over the shared engine, with **zero**
command-owned spawn/promise/finalize/store/poll machinery — so the next shell
producer is an op impl, not a parallel stack.
