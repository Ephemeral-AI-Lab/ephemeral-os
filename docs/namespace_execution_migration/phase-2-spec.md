# Phase 2 Spec — Launcher + Engine Dispatch + Watcher

Implementation-ready spec for **Phase 2** of the Namespace Execution Engine
migration. Phase contract:
[`migration-phases.md` § "Phase 2"](./migration-phases.md). Design rationale:
[`docs/namespace-execution.md`](../namespace-execution.md). This document is
**spec only** — do not implement while reading it; build to the Acceptance
Criteria at the end.

Phase 2 makes the engine **functional end to end against a fake launcher**, so it
is fully unit-tested before any real caller depends on it. **Phase 1 is already
implemented** — the `sandbox-runtime-namespace-execution` crate exists with the
type/trait skeleton (`error`, `target`, `promise`, `execution`, `shell`,
`observer`, `registry`, `id`, `lib`). Phase 2 fills that skeleton in and adds
`engine.rs`, `launcher.rs`, `pty.rs`, and one relocation file `status.rs`.

Anchors were verified against the live checkout (see the Anchor Ledger). Where the
phase contract or the design doc (which shows *final* shapes that Phases 3–6 reach)
conflicted with live code, the Phase 2 *objective* was preserved and the
implementation details were corrected to match live code; every such correction is
flagged inline. **Two decisions were settled with a live compiler experiment, not
prose** — the fake-launcher seam (§2.1) and the terminal-status location (§2.6).

---

## 1. Phase Boundary Statement

**Phase 2 delivers** a working `NamespaceExecutionEngine` with two entry points —
`run_shell_interactive` (PTY-backed, returns `InteractiveExecution<T>`) and
`run_mount` (pipe-backed, returns `ExecutionHandle<T>`) — over a single
Template-Method dispatch (reserve → build request → spawn → insert → `on_running` →
watcher{ `wait_completion` → finalize/parse → `complete` → `resolve` →
`on_terminal` } → return handle). It lands the launcher **Bridge seam**
(`pub(crate) trait NsRunnerLauncher` with `spawn_pty`/`spawn_piped`, a real
`ForkRunnerLauncher`, and a `RunnerChild` completion event), the PTY substrate
(`PtyMaster` + transcript reader), the real registry (live + completed + admission),
the promise/handle sharing required by the watcher, `RunnerOutcome::{status,payload}`,
and `ExecutionObserver::on_terminal`. The full behavioral surface is proven by
crate-local unit tests against a **fake** `NsRunnerLauncher`/`RunnerChild`.

**Phase 2 intentionally does not deliver** any caller migration. No `ExecCommand`,
no `CommandExecution`, no command-service rewrite, no `CommandOutput` DTO merge
(**Phase 3**); no `NamespaceExecutionStore` → `NamespaceExecutionLedger` rename, no
`impl ExecutionObserver` on the ledger, no `request_id` → `origin_request_id`
(**Phase 3**); no `From<WorkspaceEntry>`, no `setns_runner.rs` rewrite, no
`run_child`/`ns_runner_request` deletion (**Phase 4**); no remount-coordinator
change (**Phase 5**); no deletion of `command/src/pty.rs`/`process.rs`, **no
start-ack removal** (**Phase 6**). The engine keeps **zero `workspace` dependency**
and adds **no** `execution_kind`/`backing` classification axis.

**Why externally observable behavior is unchanged.** Nothing outside the crate's
own tests calls the engine — command and mount migration are Phases 3 and 4. The
real `ForkRunnerLauncher` (the `std::process::Command` fork of `current_exe
ns-runner`) is **compile-coverage only** this phase; it is exercised at runtime only
once a real caller wires it later. The dev host is darwin and the fork path's
runtime side is effectively Linux-only, which is *why* the fake seam is mandatory
and is the authoritative behavioral signal for Phase 2 (the PTY half — `openpt` —
*does* run on darwin via the `cfg(not(linux))` `ptsname` branch at
`command/src/pty.rs:19-20,473-480`, so `PtyMaster` is exercised for real). The
**one** permitted cross-crate touch is the minimal relocation of
`NamespaceExecutionTerminalStatus` out of `operation` and into the engine
(`operation` re-exports it, exactly as Phase 1 moved `NamespaceExecutionId`); the
observable enum, its variants, and its `as_str()` strings stay byte-for-byte
unchanged, so no observability record, DTO, or daemon code path changes.

---

## 2. Resolved Design Decisions (with live-code evidence)

The twelve decisions the phase requires, settled. **§2.1 and §2.6 are
load-bearing and are settled with a live `rustc` experiment, not hand-waving.**

### 2.1 Fake-launcher seam — boxed `pub(crate)` trait field (compiler-verified)

`migration-phases.md:93-104` requires "a fake `NsRunnerLauncher` returning a fake
`RunnerChild`" while `namespace-execution.md:263,278-289` says the launcher is
**concrete, `pub(crate)`, held on the engine — not a public `Arc<dyn>`**. The
tension is *how* a concrete `pub(crate)` launcher is fakeable without widening the
public surface or breaking the `-D warnings` clippy gate (Acceptance §7).

Two candidate mechanisms were compiled under
`rustc --edition 2021 --crate-type lib -D private_interfaces -D private_bounds -D warnings`
(the lint set `cargo clippy … -- -D warnings` enforces):

| Mechanism | Result |
|---|---|
| Engine **generic** `NamespaceExecutionEngine<L: NsRunnerLauncher = ForkRunnerLauncher>`, trait `pub(crate)` | **FAILS** — `error: type ForkRunnerLauncher is more private than the item NamespaceExecutionEngine` (`private_interfaces`) **and** `error: trait NsRunnerLauncher is more private than the item NamespaceExecutionEngine<L>` (`private_bounds`) |
| Engine **non-generic**, field `launcher: Box<dyn NsRunnerLauncher>`, trait `pub(crate)`, never named in any public signature | **PASSES** — clean exit |

**Resolution: the launcher is a `pub(crate)` *trait* (the Bridge seam); the engine
is non-generic and holds it behind a `pub(crate)` boxed trait object.**

```rust
pub(crate) trait NsRunnerLauncher: Send + Sync {
    fn spawn_pty(&self, request: NamespaceRunnerRequest)
        -> Result<(Box<dyn RunnerChild>, PtyMaster), NamespaceExecutionError>;
    fn spawn_piped(&self, request: NamespaceRunnerRequest)
        -> Result<Box<dyn RunnerChild>, NamespaceExecutionError>;
}
pub(crate) struct ForkRunnerLauncher;                  // real fork backing (compile-coverage)
#[cfg(test)] struct FakeLauncher { /* shared controls */ }   // engine tests

pub struct NamespaceExecutionEngine {
    registry: Arc<ExecutionRegistry>,
    observer: Arc<dyn ExecutionObserver>,
    launcher: Box<dyn NsRunnerLauncher>,                // ← the seam; never in a pub signature
}
```

This **preserves the public API the design wanted** (`NamespaceExecutionEngine` is
non-generic; Phase 3 callers write `Arc<NamespaceExecutionEngine>` with no type
parameter), **preserves the Bridge** (a future persistent-server backend is another
`impl NsRunnerLauncher`; the fork ↔ server swap touches only the boxed impl), and
**keeps the surface as narrow as Phase 1 did** — the trait, both concrete impls, and
`RunnerChild` stay `pub(crate)`. The one-vtable-indirection cost per spawn is
irrelevant next to a fork+PTY. The design's literal "concrete struct
`NsRunnerLauncher`" becomes "concrete struct `ForkRunnerLauncher` implementing trait
`NsRunnerLauncher`" — the seam keeps its name; the fork impl is renamed.

Injection: `#[cfg(test)] pub(crate) fn NamespaceExecutionEngine::with_launcher(
launcher: Box<dyn NsRunnerLauncher>, observer: Arc<dyn ExecutionObserver>,
max_active: usize) -> Self`; the public `new(observer, max_active)` builds
`Box::new(ForkRunnerLauncher)`.

### 2.2 `RunnerChild` + `wait_completion()` — completion event, also a `pub(crate)` trait

`RunnerChild` is the Bridge's completion event (`namespace-execution.md:141,280-289`),
heterogeneous across the fork backing and the fake, so it too is a `pub(crate)`
trait the launcher returns boxed:

```rust
pub(crate) trait RunnerChild: Send {
    /// One blocking completion: NO poll, NO result-fd reader thread.
    fn wait_completion(&mut self) -> Result<RunResult, NamespaceExecutionError>;
}
```

- **Real `ForkRunnerChild`** (Linux runtime; builds on darwin): owns the
  `std::process::Child` + the `--result-fd` read end (`OwnedFd`), confirmed against
  the live shape at `command/src/pty.rs:36-37,325-326,359` (`runner_result_done`
  reader + `result_read`). `wait_completion` = `child.wait()` **then** an inline
  `read_to_end` of the result fd → `serde_json::from_slice::<RunResult>`; on an
  absent/invalid result (kill/timeout) it synthesizes
  `RunResult { exit_code: <from the wait status / signal>, payload: json!({"status": …}) }`,
  mirroring today's fallback at `command/src/pty.rs:110-131`. Order is wait-then-read:
  the child closes its result-fd write end at exit, so the bounded `RunResult` is
  fully buffered when the read runs (safe for the small status/exit/mount-diagnostic
  payloads; a large-payload op would need a reader thread — a Future Extension, not
  Phase 2). This replaces the live `spawn_runner_result_reader` thread
  (`command/src/pty.rs:443-451`) with one inline read.
- **Fake `FakeRunnerChild`** blocks on a shared `Arc<FakeCompletion>`
  (`Mutex<Option<Result<RunResult, _>>>` + `Condvar`) until the test calls
  `complete(run_result)` or a `cancel()` trips it — so the cancel-while-blocked test
  (§2.4, Acceptance) is a *real* concurrent unblock, with **no real fork**.

### 2.3 PTY provisioning under the fake — real `openpt` loopback, in-memory transcript

`spawn_pty` returns `(Box<dyn RunnerChild>, PtyMaster)`. `PtyMaster` is **concrete**
(`pub(crate)`), built over a **real `openpt` pair** in both prod and tests — `openpt`
works on the darwin dev host via the `cfg(not(linux))` `ptsname` branch
(`command/src/pty.rs:19-20,463-483`), so interactive behavior is exercised for real
without a child. `PtyMaster` owns the non-blocking master writer, an **in-memory,
timestamp-prefixed transcript buffer** (`Arc<Mutex<Vec<u8>>>` drained by the reader
thread), and a `cancel` action (§2.4):

```rust
pub(crate) struct PtyMaster { /* writer: Mutex<File>, transcript: Arc<Mutex<Vec<u8>>>,
                                 reader_done: Mutex<Option<mpsc::Receiver<()>>>,
                                 cancel: Box<dyn Fn() + Send + Sync> */ }
impl PtyMaster {
    pub(crate) fn write_stdin(&self, bytes: &[u8]) -> io::Result<()>;   // relocated non-blocking write
    pub(crate) fn read_output_since(&self, offset: u64) -> String;      // from the in-memory buffer
    pub(crate) fn output_len(&self) -> u64;                             // buffer length
    pub(crate) fn cancel(&self);                                        // (self.cancel)()
}
```

These three I/O methods are exercised **without a real child** by a `pty.rs` unit
test: open a pair, hand the master to `PtyMaster`, write bytes into the **slave**,
assert the reader drains them into the buffer (`output_len`/`read_output_since`), and
assert `write_stdin` reaches the slave. The 1 MiB truncation + file persistence
(`command/src/transcript.rs:7,63-82`) is a **command** concern deferred to Phase 3;
the engine's generic, workspace-agnostic sink is the in-memory buffer.

### 2.4 Thread model & cancel — 2 threads/exec; cancel via a `pub(crate)` action

Per exec, exactly **two** threads (`namespace-execution.md:303-316`):

- **PTY-output reader** (interactive path only): spawned inside `PtyMaster`
  construction (pty.rs), polls the master (`poll(-1)`), drains into the transcript
  buffer with the timestamp prefixer; exits on EOF/hangup (all slave holders gone) or
  error — relocated from `command/src/pty.rs:398-441`. Detached; its drain completion
  is observable via a `reader_done` channel (relocated `wait_for_reader_done`,
  `command/src/pty.rs:276-281`) if a test needs to await it.
- **Watcher** (both paths): blocks on `child.wait_completion()`; on return, builds
  `RunnerOutcome`, captures `status`/`exit_code` from it, runs `op.finalize` (shell)
  or `parse` (mount) **inline**, then **`registry.complete(id, …)` BEFORE
  `promise.resolve(result)`** (so *promise-resolved ⟹ the completed entry exists*,
  the invariant `namespace-execution.md:446-448` relies on — a deliberate reordering
  of the doc's step-7 listing to satisfy the doc's own invariant), then
  `observer.on_terminal(id, status, exit)`. Detached; ends after `on_terminal`.

**Cancel is independent of the watcher** (`namespace-execution.md:213-215,449-452`):
`InteractiveExecution::cancel()` → `PtyMaster::cancel()` → the stored
`Box<dyn Fn() + Send + Sync>`. The fork backing sets it to
`move || terminate_process_group(pgid)` (the `killpg` SIGTERM→SIGKILL relocated from
`command/src/pty.rs:485-490`); the child runs in its own group
(`process_group(0)`, `command/src/pty.rs:345`), so the kill unblocks the watcher's
`child.wait()` without the watcher mediating. The fake sets it to trip the shared
`FakeCompletion`, so the same `cancel()` API unblocks the fake watcher in-process.
The boxed action is the one indirection introduced beyond the design's literal
`killpg(pgid)`; it is justified as the cancel-path Bridge and is what makes the
fake's cancel test real. **Lock budget:** the registry's single `Mutex` is taken at
`try_reserve`, `attach`, and `complete` (three short critical sections per exec); the
promise once at `resolve`; the observer at `on_running`/`on_terminal` — matching the
design's "~3 + observer, zero poll loops" (`namespace-execution.md:316`).

### 2.5 `wait_timeout` peek — **deferred**; keep the Phase 1 `bool` form

Phase 1 ships `CompletionPromise::wait_timeout(Duration) -> bool`
(`promise.rs:66-76`). The design's final handle API `wait_timeout(&self, d) ->
Option<&T>` (`namespace-execution.md:191,199`) exists for the **command yield path**
(Phase 3). No Phase 2 exit test needs the peek: Acceptance covers
`wait()`/`is_finished()` and the existing `wait_timeout(Duration) -> bool` (the "no
poll" test). **Resolution (smallest surface): do NOT add `Option<&T>` in Phase 2.**
`promise.rs` keeps its `bool` `wait_timeout`; the only Phase 2 promise change is
removing the `#[cfg_attr(not(test), allow(dead_code))]` guards now that the engine
calls `new`/`resolve`/`is_resolved`/`wait`/`wait_timeout` in prod. The peeking
`Option<&T>` (which must coexist with single-consumer `wait(self)` that takes the
value) is Phase 3.

### 2.6 Terminal-status type location — relocate into the engine (compiler-verified path)

The watcher calls `observer.on_terminal(id, status, exit)` and `RunnerOutcome::status()`
returns a `status`, but `NamespaceExecutionTerminalStatus` lives in `operation`
(`operation/src/namespace_execution.rs:67-85`) and the engine **must not** depend on
`operation` (cycle). Three options were weighed; the reference set was enumerated:

```
operation/src/namespace_execution.rs:43,67-85,96   def + record field + CompleteNamespaceExecution
operation/src/lib.rs:24                              pub use namespace_execution::{… TerminalStatus …}
operation/src/command/service/finalize.rs:7-10       use crate::namespace_execution::{… TerminalStatus}
operation/src/command/service/impls/exec_command.rs:14-17  use crate::namespace_execution::{… TerminalStatus}
crates/sandbox-daemon/src/observability/namespace_execution.rs:5-6  use sandbox_runtime::{… TerminalStatus}
operation/tests/{namespace_execution,exec_command}.rs, daemon/tests/unit/observability.rs  use sandbox_runtime::{… TerminalStatus}
```

Every operation-internal importer uses `crate::namespace_execution::…`; every
external importer uses `sandbox_runtime::…`. **This is exactly the Phase 1
`NamespaceExecutionId` situation** (`phase-1-spec.md` §5.13), which is already proven
in the live tree. **Resolution: relocate `NamespaceExecutionTerminalStatus` (enum +
`as_str()`) into the engine crate at `src/status.rs`; `operation` re-exports it** via
`pub use sandbox_runtime_namespace_execution::NamespaceExecutionTerminalStatus;`
swapped in for the deleted definition. Then:

- `RunnerOutcome::status()` and `ExecutionObserver::on_terminal` name the
  engine-local enum — no cycle.
- `crate::namespace_execution::NamespaceExecutionTerminalStatus` keeps resolving →
  `finalize.rs`, `exec_command.rs`, and `lib.rs:22-25` are **untouched**.
- `sandbox_runtime::NamespaceExecutionTerminalStatus` keeps resolving → the daemon
  and all tests are **untouched**.
- The enum's variants and `as_str()` strings (`"ok"/"error"/"timed_out"/"cancelled"`)
  are copied verbatim → the observable surface is byte-for-byte unchanged.

This is the **one allowed exception** to the Phase 3 no-rename rule. Options B
(engine-local *second* status type) and C (derive terminal state from `exit_code`
only, no `status()`) are rejected: B forces a Phase 3 reconciliation of two status
types (re-complication), and C contradicts the Phase 2 in-scope list
(`RunnerOutcome::status()`) and the design's "parsed once, here"
(`namespace-execution.md:248-250`). The edit touches **exactly one** `operation`
file (`namespace_execution.rs`), mirroring Phase 1's id move.

`RunnerOutcome::status()` for Phase 2 is a **pure wire parse**: read
`payload["status"]` as a string and map to the enum, defaulting to `Error` when
absent/unrecognized (the string set is exactly `as_str()`'s, and matches the live
parse at `command/src/pty.rs:158-171`). The design's "cancel override applied in
`status()` (cancel is known engine-side)" (`namespace-execution.md:248-250`) is a
**command-`finalize` concern, deferred to Phase 3** — today the kill→`"cancelled"`/130
override is applied at the command layer (`command/src/pty.rs:123-129`), not on the
wire result; Phase 2 keeps `status()` a pure projection and does not pull that
forward. In Phase 2 a cancelled fake simply returns a `RunResult` whose payload
`status` is `"cancelled"`.

### 2.7 Observer `on_terminal` — added; status tied to §2.6; `begin` stays in operation

`observer.rs` gains:

```rust
fn on_terminal(&self, id: &NamespaceExecutionId,
               status: NamespaceExecutionTerminalStatus, exit_code: Option<i64>);
```

matching `namespace-execution.md:493-498`. `begin` is **not** added (it owns the
`WorkspaceSessionId` and stays in the operation layer; the engine drives only
running/terminal by id). The implementer `NamespaceExecutionLedger` is **Phase 3**;
Phase 2 tests use a **fake observer** recording `on_running`/`on_terminal` calls.

### 2.8 Registry — live + completed + admission, generic (no command types)

The Phase 1 placeholder (`registry.rs:5-18`) becomes the real registry, **shared as
`Arc<ExecutionRegistry>`** (the watcher thread calls `complete`):

```rust
pub(crate) struct ExecutionRegistry { inner: Mutex<RegistryState>, max_active: usize }
struct RegistryState {
    live:      HashMap<NamespaceExecutionId, LiveExecution>,
    completed: HashMap<NamespaceExecutionId, CompletedExecution>,
}
pub(crate) struct LiveExecution     { pgid: Option<i32> }                 // generic; cancel handle for Phase 5
pub(crate) struct CompletedExecution {                                    // generic; NO command types
    status: NamespaceExecutionTerminalStatus, exit_code: Option<i64>,
}
impl ExecutionRegistry {
    pub(crate) fn new(max_active: usize) -> Self;
    pub(crate) fn max_active(&self) -> usize;
    /// Atomically reserve a live slot keyed by `id`; `Err(Admission)` if full.
    pub(crate) fn try_reserve(&self, id: &NamespaceExecutionId) -> Result<(), NamespaceExecutionError>;
    pub(crate) fn attach(&self, id: &NamespaceExecutionId, pgid: Option<i32>);   // enrich after spawn
    pub(crate) fn abort(&self, id: &NamespaceExecutionId);                       // release on spawn failure
    pub(crate) fn complete(&self, id: &NamespaceExecutionId, done: CompletedExecution);  // live → completed
    pub(crate) fn is_live(&self, id: &NamespaceExecutionId) -> bool;
    pub(crate) fn is_completed(&self, id: &NamespaceExecutionId) -> bool;
}
```

`try_reserve` is the admission point: under the **single** lock it checks
`live.len() < max_active` and inserts the reservation atomically (no TOCTOU), so
concurrent `run_*` calls cannot both admit the last slot. `complete` moves
live→completed under the same lock, composing with the watcher. The completed entry
retains only the generic `{ status, exit_code }`; the command-typed
`CompletedCommandRecord` (transcript cursor, retained transcript, session
disposition) is **Phase 3**. Phase 2 does **not** store the returned
handle/`InteractiveExecution` in the registry — that is the Phase 3 `CommandExecution`
storage; Phase 2 returns the handle to the caller and the registry tracks only
admission + terminal projection by id.

### 2.9 `NamespaceRunnerRequest` construction (argv + fds) — id IS request_id

The **engine** builds the request from `(target + op + id)` and passes it to the
launcher (so the fake can assert it); the launcher serializes it
(`serde_json::to_vec`) onto `--request-fd`. Field-by-field against
`protocol.rs:21-35`:

| Request field | Shell (`run_shell_interactive`) | Mount (`run_mount`) |
|---|---|---|
| `request_id` | `id.0.clone()` — **the `namespace_execution_id` IS the runner `request_id`** | same |
| `args` | `json!({ "command": op.command(), "cwd": "." })` (`ShellOperation` carries no `cwd()` — design: no producer, `namespace-execution.md:251`) | `json!({})` (real mount/remount probe args are **Phase 4**) |
| `workspace_root` / `layer_paths` / `upperdir` / `workdir` | from `target` (`target.rs:9-13`) | same |
| `ns_fds` | `Some(target.ns_fds)` (`target.ns_fds: NsFds` → request `Option<NsFds>`) | same |
| `timeout_seconds` | `op.timeout_seconds()` | `None` |

The launcher forks `current_exe ns-runner [mode] --request-fd FD --result-fd FD
--start-ack-fd FD` (`command/src/pty.rs:333-345`): the **shell path passes no mode
flag** (`NsRunnerOperation::Run` is the default, `daemon/src/runner.rs:170`);
`run_mount` passes the caller's `mode_flag` (`"--mount-overlay"` /
`"--remount-overlay"`, parsed at `daemon/src/runner.rs:122-123`). `request_id` is the
registry key (§2.8).

### 2.10 Dependency-set additions — `serde_json`, `rustix{pty,event,pipe}`, `nix{signal}`

Mirror `command/Cargo.toml:9-14`, dropping what the relocated Phase 2 code does not
call:

| Dep (workspace version) | First call site | Why / scope |
|---|---|---|
| `serde_json.workspace = true` (`Cargo.toml:30`) | engine request build (`json!`, `to_vec`), `RunnerOutcome::payload() -> &serde_json::Value`, `status()` parse | needed; **`serde` is NOT** added — no engine type derives `Serialize`/`Deserialize` (`NamespaceTarget` is *converted* to the already-serde `NamespaceRunnerRequest`, not serialized) |
| `rustix = { workspace = true, features = ["pty", "event", "pipe"] }` (root `Cargo.toml:47`) | `pty.rs` `openpt`/`grantpt`/`unlockpt`/`ptsname`, `poll`, `pipe` (`command/src/pty.rs:13-21`) | mirror command exactly; `fs` (for `OFlags`/`fcntl_*`) and `io` come from the workspace baseline `features = ["fs", …]` via `workspace = true` |
| `nix = { workspace = true, features = ["signal"] }` (root `Cargo.toml:48`) | `killpg`, `Signal`, `Pid` in the cancel/terminate path (`command/src/pty.rs:11-12,486-488`) | **only `signal`** — command's extra `process` feature is unneeded: `nix-0.29` `pub mod unistd` is ungated (`lib.rs:183`) and `Pid`/`Pid::from_raw` carry no `cfg(feature)` (`unistd.rs:174-178`); `killpg` needs `signal` (`sys/signal.rs:1082`) |
| ~~`serde`~~ / ~~`libc`~~ / ~~`thiserror`~~ | — | **NOT added** — no relocated Phase 2 path calls `libc` directly (rustix/nix wrap it) or derives serde; `error.rs` hand-rolls `Display`/`Error` (`error.rs:14-29`) |

All versions exist in `[workspace.dependencies]` (`Cargo.toml:27-56`). The relocated
code is **safe** (rustix/nix safe wrappers; `command/src/lib.rs:15` is
`#![forbid(unsafe_code)]`), so Phase 2 introduces **no `unsafe`** and needs no
`// SAFETY:` block — correcting `phase-1-spec.md` §5.3's assumption that Phase 2
requires unsafe. (The crate-level `forbid(unsafe_code)` attribute is **not** added,
to keep the lib.rs edit minimal and avoid risk.)

### 2.11 Start-ack KEEP — wired exactly as today (Phase 6 removes it)

Per `migration-phases.md:106-112,262-264`, `ForkRunnerLauncher` **must** create the
start-ack pipe and pass `--start-ack-fd`, then release the child by writing the ack
byte before the request — folding the live two-phase
`spawn_current_exe_ns_runner` + `PendingPtyProcess::allow_start`
(`command/src/pty.rs:328-329,340-341,392-396,292-309`) into one `spawn_pty` that
spawns, drops the read ends, writes the ack byte, then writes the request. The
in-namespace child still `read_exact`s it (`daemon/src/runner.rs:23,140-147,175-189`).
**Do not simplify it away** — removal is the Phase 6 atomic cut across launcher **and**
daemon. The design's dispatch comment "NO start-ack" (`namespace-execution.md:299`)
describes the Phase 6 end-state and is explicitly overridden for Phase 2.

### 2.12 Public export delta — `NamespaceExecutionEngine` + the relocated status

`lib.rs` newly, **publicly** exports two names: `NamespaceExecutionEngine` (Phase 3+
callers construct it) and `NamespaceExecutionTerminalStatus` (so `operation` can
re-export it, §2.6). Everything else stays `pub(crate)`: `NsRunnerLauncher` (trait),
`ForkRunnerLauncher`, `RunnerChild` (trait), `ForkRunnerChild`, `PtyMaster`,
`CompletionPromise`, `ExecutionRegistry`. The eight Phase 1 public re-exports are
unchanged.

---

## 3. Resulting File/Folder Structure

After Phase 2 (`← NEW`, `△` edited, `[unchanged]`). Crate-local unit tests live in
inline `#[cfg(test)] mod` blocks — required, because the launcher seam,
`RunnerChild`, `PtyMaster`, `CompletionPromise`, and `ExecutionRegistry` are all
`pub(crate)` and unreachable from a `tests/` integration dir. No `tests/` directory
is added. The **fake `NsRunnerLauncher`/`RunnerChild`/observer** live in
`engine.rs`'s `#[cfg(test)] mod`.

```text
crates/sandbox-runtime/
  namespace-execution/                         (engine; workspace-agnostic)
    Cargo.toml                                 △  + serde_json, rustix{pty,event,pipe}, nix{signal}
    src/
      lib.rs                                   △  + mod engine/launcher/pty/status; +2 pub use
      id.rs                                    [unchanged]
      error.rs                                 [unchanged]  (variants now constructed by the engine)
      target.rs                                [unchanged]
      status.rs                                ← NEW  NamespaceExecutionTerminalStatus (relocated; §2.6)
      promise.rs                               △  remove `allow(dead_code)` guards (engine uses it now)
      execution.rs                             △  + pty field, write_stdin/read_output_since/output_len/cancel,
                                                    promise → Arc; + inline tests
      shell.rs                                 △  + RunnerOutcome::{new,status,payload}; import status
      observer.rs                              △  + on_terminal(id, status, exit_code)
      registry.rs                              △  placeholder → live+completed+admission; + inline tests
      engine.rs                                ← NEW  NamespaceExecutionEngine, dispatch, watcher
                                                       + #[cfg(test)] FakeLauncher/FakeRunnerChild/FakeObserver
                                                       + engine unit tests
      launcher.rs                              ← NEW  pub(crate) NsRunnerLauncher trait, ForkRunnerLauncher,
                                                       RunnerChild trait, ForkRunnerChild (start-ack KEPT)
      pty.rs                                   ← NEW  PtyMaster + transcript reader + open_pty_pair
                                                       + #[cfg(test)] openpt-loopback test
  operation/
    src/namespace_execution.rs                 △  delete TerminalStatus enum+impl → add `pub use …` (§2.6)
    src/lib.rs                                 [unchanged]  (re-export flows through namespace_execution)
    src/command/service/{finalize,impls/exec_command}.rs  [unchanged]  (resolve via the re-export)
  command/                                     [unchanged]  (pty.rs/process.rs deleted only in Phase 6)
  namespace-process/                           [unchanged]  (NsFds / NamespaceRunnerRequest / RunResult source)
crates/sandbox-daemon/                         [unchanged]  (runner child keeps start-ack; consumes re-exported status)
Cargo.toml (root)                              [unchanged]  (engine crate already a member + path dep, Phase 1)
```

`status.rs ← NEW` is the **one extra file** beyond the contract's
`engine`/`launcher`/`pty`: it is the home of the Decision-6 relocation (an SRP-clean
file imported by both `shell.rs` and `observer.rs`, avoiding an inter-module cycle),
and is the single permitted cross-phase touch.

---

## 4. Touched-File LOC Change Ledger

Estimates seeded from `namespace-execution.md:573-588` (engine ≈180, launcher ≈180,
pty ≈120) and kept honest/narrow. The implementer **must** report actuals with
`git diff --numstat`.

| File | Change | Est. LOC delta | Why |
|---|---:|---:|---|
| `…/namespace-execution/src/engine.rs` | add | `+180` | engine struct + 2 entry points + shared `dispatch`/`watch` + `#[cfg(test)]` fakes (~60) + engine tests |
| `…/namespace-execution/src/launcher.rs` | add | `+180` | `NsRunnerLauncher` trait + `ForkRunnerLauncher` (spawn/pipes/start-ack/request build) + `RunnerChild` trait + `ForkRunnerChild` |
| `…/namespace-execution/src/pty.rs` | add | `+120` | `PtyMaster` + reader thread + `open_pty_pair` + `set_nonblocking` + `terminate_process_group` + loopback test |
| `…/namespace-execution/src/status.rs` | add | `+25` | relocated enum + `as_str()` + inline test (§2.6) |
| `…/namespace-execution/src/registry.rs` | edit | `+90` | placeholder → maps + `try_reserve`/`attach`/`abort`/`complete`/lookups + tests |
| `…/namespace-execution/src/execution.rs` | edit | `+70` | `pty` field, 4 interactive methods, `promise → Arc`, drop `allow(dead_code)`, tests |
| `…/namespace-execution/src/shell.rs` | edit | `+20` | `RunnerOutcome::{new,status,payload}` + `use crate::status::…` + `serde_json::Value` |
| `…/namespace-execution/src/observer.rs` | edit | `+6` | `on_terminal` + import status |
| `…/namespace-execution/src/lib.rs` | edit | `+8` | 4 `mod`s + 2 `pub use` |
| `…/namespace-execution/src/promise.rs` | edit | `~0` (`-6`) | remove `#[cfg_attr(not(test), allow(dead_code))]` guards |
| `…/namespace-execution/Cargo.toml` | edit | `+3` | `serde_json`, `rustix`, `nix` |
| `crates/sandbox-runtime/operation/src/namespace_execution.rs` | edit | `~0` (`-18`/`+1`) | delete `NamespaceExecutionTerminalStatus` enum+impl, add 1-line `pub use` shim (§2.6) |

Engine-crate source subtotal ≈ **+696**; with the manifest ≈ **+699**; the one
`operation` edit nets ≈ **−17**. Net repo delta ≈ **+680**. Deletes: none of the
Phase 3–6 deletions (this phase only *adds* the engine internals and *relocates* one
enum).

---

## 5. File-By-File Implementation Spec

### 5.1 `src/engine.rs` — new

**Responsibility.** The Strategy + Template-Method core: hold the registry, observer,
and boxed launcher; expose `run_shell_interactive`/`run_mount`; run the one dispatch
skeleton and the watcher thread. Knows nothing of shell-vs-mount beyond which
launcher method and finalizer it is handed.

```rust
pub struct NamespaceExecutionEngine {
    registry: Arc<ExecutionRegistry>,
    observer: Arc<dyn ExecutionObserver>,
    launcher: Box<dyn NsRunnerLauncher>,
}

impl NamespaceExecutionEngine {
    pub fn new(observer: Arc<dyn ExecutionObserver>, max_active: usize) -> Self;          // real fork launcher
    #[cfg(test)]
    pub(crate) fn with_launcher(launcher: Box<dyn NsRunnerLauncher>,
                                observer: Arc<dyn ExecutionObserver>, max_active: usize) -> Self;

    pub fn run_shell_interactive<S: ShellOperation>(
        &self, op: S, target: NamespaceTarget, id: NamespaceExecutionId,
    ) -> Result<InteractiveExecution<S::Output>, NamespaceExecutionError>;

    pub fn run_mount<O: Send + 'static>(
        &self, mode_flag: &'static str, target: NamespaceTarget, id: NamespaceExecutionId,
        parse: impl FnOnce(RunnerOutcome) -> Result<O, NamespaceExecutionError> + Send + 'static,
    ) -> Result<ExecutionHandle<O>, NamespaceExecutionError>;
}
```

**Dispatch (shared spine, both methods).**

1. `registry.try_reserve(&id)?` — admission (`Err(Admission { max_active })` if full).
2. `let request = build_request(&target, &id, …)` (§2.9). Capture `op.command()` /
   `op.timeout_seconds()` here (before the op is boxed into the finalizer).
3. spawn: shell → `let (child, pty) = self.launcher.spawn_pty(request)`; mount → `let
   child = self.launcher.spawn_piped(request)`. On `Err`, `registry.abort(&id)` then
   return the error.
4. `registry.attach(&id, pgid)` (shell carries the pty's pgid; mount `None`).
5. `self.observer.on_running(&id)`.
6. build `promise = Arc::new(CompletionPromise::new())`; spawn the **watcher**
   (clones of `promise`, `registry`, `observer`, the `id`, and the moved `child` +
   `finalize` closure):
   ```text
   let (result, status, exit) = match child.wait_completion() {
       Ok(run_result) => {
           let outcome = RunnerOutcome::new(run_result);
           let (status, exit) = (outcome.status(), Some(outcome.exit_code()));
           match finalize(outcome) {
               Ok(o)  => (Ok(o),  status, exit),
               Err(e) => (Err(e), NamespaceExecutionTerminalStatus::Error, exit),
           }
       }
       Err(e) => (Err(e), NamespaceExecutionTerminalStatus::Error, None),
   };
   registry.complete(&id, CompletedExecution { status, exit });   // BEFORE resolve (§2.4)
   promise.resolve(result);
   observer.on_terminal(&id, status, exit);
   ```
   where `finalize` is `move |o| op_box.finalize(o)` (shell) or the `parse` closure
   (mount).
7. return `InteractiveExecution::new(ExecutionHandle::new(id, promise), pty)` (shell)
   or `ExecutionHandle::new(id, promise)` (mount).

**`#[cfg(test)] mod tests`** holds `FakeLauncher` (records each request; returns a
`FakeRunnerChild` bound to a shared `Arc<FakeCompletion>`; for `spawn_pty` builds a
real-`openpt` `PtyMaster` whose `cancel` trips that `FakeCompletion`), `FakeObserver`
(records `on_running`/`on_terminal`), and the engine unit tests (§7). A trivial
`#[derive(Default)] struct OkShellOp` / explicit `ErrShellOp` provide
`ShellOperation` impls whose `finalize` returns `Ok`/`Err`.

**Non-goals.** No `ExecCommand`/`CommandExecution` (Phase 3); no `From<WorkspaceEntry>`
(Phase 4); engine takes **no `workspace` type**; no `execution_kind`/`backing`.

### 5.2 `src/launcher.rs` — new (`pub(crate)`)

**Responsibility.** The Bridge seam + the fork backing. Relocates and **merges**
`command/src/pty.rs::spawn_current_exe_ns_runner` and the mount-path
`workspace/.../setns_runner.rs::run_child` into one launcher (relocated, not
rewritten; the mount call sites themselves are Phase 4).

```rust
pub(crate) trait NsRunnerLauncher: Send + Sync {
    fn spawn_pty(&self, request: NamespaceRunnerRequest)
        -> Result<(Box<dyn RunnerChild>, PtyMaster), NamespaceExecutionError>;
    fn spawn_piped(&self, request: NamespaceRunnerRequest)
        -> Result<Box<dyn RunnerChild>, NamespaceExecutionError>;
}
pub(crate) trait RunnerChild: Send {
    fn wait_completion(&mut self) -> Result<RunResult, NamespaceExecutionError>;
}
pub(crate) struct ForkRunnerLauncher;          // unit struct; new() -> Self
struct ForkRunnerChild { child: Child, result_read: OwnedFd }
```

| Item | Behavior (relocated source) |
|---|---|
| `ForkRunnerLauncher::spawn_pty` | request/result/start-ack pipes (`command/src/pty.rs:323-329,377-396`); `open_pty_pair` (pty.rs §5.3); `set_nonblocking(master)`; `Command::new(current_exe).arg("ns-runner")` + `--request-fd/--result-fd/--start-ack-fd` + slave as stdio + `process_group(0)` (`:333-345`); drop read ends; **write ack byte, then request** (KEEP, §2.11); `PtyMaster::spawn(master, pgid, killpg-cancel)`; return `(Box::new(ForkRunnerChild{child, result_read}), pty)` |
| `ForkRunnerLauncher::spawn_piped` | as above without the PTY (stdio null/piped, mirroring `run_child` `command/.../setns_runner.rs:206-218`); still passes `--start-ack-fd` and writes the ack; returns `Box::new(ForkRunnerChild)` |
| `ForkRunnerChild::wait_completion` | `self.child.wait()` then inline `read_to_end(result_read)` → `serde_json::from_slice::<RunResult>`; synthesize on absent/invalid (§2.2). **No** reader thread, **no** poll |

**Non-goals.** No `MountOperation`/`NsRunnerMode`/`Backing`; no result-fd reader
thread; no start-ack **removal** (Phase 6); does not delete or edit the live
`spawn_current_exe_ns_runner`/`run_child` (those are removed in Phases 3/4/6).

### 5.3 `src/pty.rs` — new (`pub(crate)`)

**Responsibility.** `PtyMaster` + the PTY-output reader + the PTY/`killpg` helpers,
adapted from `command/src/pty.rs`. Workspace-agnostic; drains to an **in-memory**
transcript buffer (§2.3).

| Item | Source / behavior |
|---|---|
| `PtyMaster` | fields per §2.3; `spawn(master: File, pgid: Option<i32>, cancel: Box<dyn Fn()+Send+Sync>) -> Self` clones the writer, sets non-blocking, spawns the reader |
| `write_stdin` | relocated non-blocking write w/ `STDIN_WRITE_DEADLINE` backpressure (`command/src/pty.rs:213-244`) |
| `read_output_since` / `output_len` | read the `Arc<Mutex<Vec<u8>>>` buffer (engine-local; file truncation is Phase 3) |
| `cancel` | invoke the stored action (§2.4) |
| `open_pty_pair` | relocated verbatim incl. the `cfg(linux)`/`cfg(not(linux))` branches (`command/src/pty.rs:463-483`) — builds + runs `openpt` on darwin + linux |
| reader thread | relocated drain-on-`poll(-1)` loop + `TranscriptTimestampPrefixer` (`command/src/pty.rs:398-441`; prefixer relocated or re-imported per §6 note) |
| `terminate_process_group` | relocated `killpg` SIGTERM→SIGKILL (`command/src/pty.rs:485-490`) |

**Inline test.** Open a pair, hand the master to `PtyMaster::spawn`, write to the
slave, assert `output_len`/`read_output_since` observe it after the reader drains;
write via `write_stdin` and assert it reaches the slave — exercising the interactive
methods with **no child** (§2.3).

**Non-goals.** No transcript *file* path, no 1 MiB truncation/`read_transcript_since`
(Phase 3 command concern); no `CommandRunnerResult`/`CommandCompletionStatus` (those
stay in `command`).

### 5.4 `src/status.rs` — new (`pub`) — the Decision-6 relocation

```rust
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum NamespaceExecutionTerminalStatus { Ok, Error, TimedOut, Cancelled }
impl NamespaceExecutionTerminalStatus {
    #[must_use] pub const fn as_str(self) -> &'static str { /* "ok"/"error"/"timed_out"/"cancelled" */ }
}
```

Copied **verbatim** (derives, variants, strings) from
`operation/src/namespace_execution.rs:67-85`. Inline test asserts the four
`as_str()` strings (keeps parity with the daemon mapping
`daemon/.../namespace_execution.rs:71-76`). Re-exported by `lib.rs` (§5.9) and by
`operation` (§5.10). No `serde` derive (the daemon serializes via its own rows).

### 5.5 `src/registry.rs` — edit (`pub(crate)`)

Replace the placeholder (`registry.rs:5-18`) with the real registry of §2.8.
**Inline tests:** `try_reserve` admits up to `max_active` then returns
`Err(Admission)`; `complete` moves an id live→completed (`is_live`→`is_completed`);
`abort` releases a reservation so a later `try_reserve` succeeds. Keep `max_active()`.
**Non-goals:** no command types in `CompletedExecution`; no transcript/cursor/session
fields (Phase 3); no remount queries (Phase 5).

### 5.6 `src/execution.rs` — edit

- `ExecutionHandle<T>`: `promise: CompletionPromise<T>` → `promise:
  Arc<CompletionPromise<T>>` (shared with the watcher, §2.5); `new`/`id`/`is_finished`/
  `wait` bodies unchanged except `wait(self)` calls `self.promise.wait()` on the
  `Arc`. Drop the `#[cfg_attr(not(test), allow(dead_code))]` on `new`.
- `InteractiveExecution<T>`: add `pty: PtyMaster`; `new(exec, pty)`; add forwarding
  interactive methods:
  ```rust
  pub fn write_stdin(&self, bytes: &[u8]) -> io::Result<()>;     // → self.pty.write_stdin
  pub fn read_output_since(&self, offset: u64) -> String;        // → self.pty.read_output_since
  pub fn output_len(&self) -> u64;                               // → self.pty.output_len
  pub fn cancel(&self);                                          // → self.pty.cancel
  ```
  keep `execution`/`id`/`is_finished`/`wait` forwards.
- **Inline test:** extend the Phase 1 composition test to construct via the fake path
  and assert `cancel()` + `wait()` interplay (or keep the unit assertion minimal and
  let engine.rs cover behavior). **Deferred:** `wait_timeout(&self) -> Option<&T>`
  (Phase 3, §2.5).

### 5.7 `src/shell.rs` — edit

Add to `RunnerOutcome` (keep the `exit_code()` from `shell.rs:9-11`):

```rust
impl RunnerOutcome {
    pub(crate) fn new(result: RunResult) -> Self { Self(result) }
    pub fn status(&self) -> NamespaceExecutionTerminalStatus;     // parse payload["status"] (§2.6)
    pub fn payload(&self) -> &serde_json::Value { &self.0.payload }
}
```

`use crate::status::NamespaceExecutionTerminalStatus;` and `serde_json::Value`.
`ShellOperation` trait is unchanged from Phase 1 (`shell.rs:14-24`). **Non-goals:** no
cancel-override in `status()` (Phase 3, §2.6); no `ShellOutcome`/`ShellStatus`/
`FinalizeCx`.

### 5.8 `src/observer.rs` — edit

Add `on_terminal` (§2.7) beside the Phase 1 `on_running` (`observer.rs:5-7`); `use
crate::status::NamespaceExecutionTerminalStatus;`. **Non-goals:** no `begin`.

### 5.9 `src/lib.rs` — edit

Add `mod engine; mod launcher; mod pty; mod status;` (launcher/pty stay un-re-exported
— their items are `pub(crate)`). Add `pub use engine::NamespaceExecutionEngine;` and
`pub use status::NamespaceExecutionTerminalStatus;`. Keep the eight Phase 1 re-exports
(`lib.rs:15-20`). **Non-goals:** do not re-export `NsRunnerLauncher`/`RunnerChild`/
`PtyMaster`/`CompletionPromise`/`ExecutionRegistry`.

### 5.10 `crates/sandbox-runtime/operation/src/namespace_execution.rs` — edit (the shim)

- **Delete** the enum + impl at `:67-85`
  (`pub enum NamespaceExecutionTerminalStatus { … }` + `impl … as_str`).
- **Add**, beside the Phase 1 id shim at `:8`:
  ```rust
  pub use sandbox_runtime_namespace_execution::NamespaceExecutionTerminalStatus;
  ```

The in-module uses (`:43` record field, `:96` `CompleteNamespaceExecution`) and the
re-export `lib.rs:24` resolve to the re-exported name; `finalize.rs:7-10` and
`exec_command.rs:14-17` (`use crate::namespace_execution::…`) and the daemon/tests
(`use sandbox_runtime::…`) are **untouched** (§2.6). **Non-goals (Phase 3):** no
`Store → Ledger` rename, no `impl ExecutionObserver`, no `request_id` →
`origin_request_id`.

### 5.11 `Cargo.toml` (engine crate) — edit

Add under `[dependencies]` (beside `sandbox-runtime-namespace-process.workspace =
true`, `Cargo.toml:9`):

```toml
serde_json.workspace = true
rustix = { workspace = true, features = ["pty", "event", "pipe"] }
nix = { workspace = true, features = ["signal"] }
```

Per §2.10. No `serde`, `libc`, `thiserror`, or `[dev-dependencies]`.

### 5.12 The fake launcher (test seam) — in `engine.rs` `#[cfg(test)]`

| Item | Behavior |
|---|---|
| `FakeCompletion` | `Mutex<Option<Result<RunResult, NamespaceExecutionError>>>` + `Condvar`; `complete(rr)` / `cancel()` set a cancelled `RunResult` and `notify_all`; `wait()` blocks until set |
| `FakeRunnerChild` | holds `Arc<FakeCompletion>`; `wait_completion(&mut self)` blocks on `wait()` — **real block** for the cancel test |
| `FakeLauncher` | `spawn_pty`: record `request`, mint an `Arc<FakeCompletion>`, build a real-`openpt` `PtyMaster` with `cancel = { trip the FakeCompletion }`, return `(Box::new(FakeRunnerChild), pty)`; `spawn_piped`: same without the PTY. Exposes the recorded requests + completion handles so a test drives completion/cancel and asserts `request.request_id` |
| `FakeObserver` | `Mutex<Vec<Event>>` recording `Running(id)` / `Terminal(id, status, exit)` |

Keeps the **public API unchanged** (the fake is `#[cfg(test)]`, injected via
`with_launcher`) and preserves the fork↔server swap the Bridge exists for.

---

## 6. Verification Commands

Run in order from the repo root (`export PATH="$PWD/bin:$PATH"` first, per
`CLAUDE.md`):

```sh
cargo fmt --check
cargo check  -p sandbox-runtime-namespace-execution --tests
cargo test   -p sandbox-runtime-namespace-execution           # fake-launcher engine tests — AUTHORITATIVE
cargo clippy -p sandbox-runtime-namespace-execution --all-targets --no-deps -- -D warnings
cargo test   -p sandbox-runtime --tests                       # terminal-status relocation: re-export, no regression
cargo check  -p sandbox-daemon                                # consumes re-exported NamespaceExecutionTerminalStatus
# Phase 3-6 work MUST be absent from the engine crate:
rg -n "ExecCommand|CommandExecution|run_child|From<WorkspaceEntry>|NamespaceExecutionLedger|origin_request_id" \
  crates/sandbox-runtime/namespace-execution/src || echo "no Phase 3-6 leak ✓"
# start-ack still wired (Phase 6 removes it):
rg -n "start[-_]ack" crates/sandbox-runtime/namespace-execution/src
# no command/workspace/daemon source changed (only the one operation re-export shim):
test -z "$(git diff --name-only -- crates/sandbox-runtime/command crates/sandbox-runtime/workspace crates/sandbox-daemon)" \
  && echo "untouched ✓" || echo "UNEXPECTED CHANGES ✗"
git diff --check
git diff --numstat
```

**Host constraint (matches `phase-1-spec.md` §6).** The dev host is darwin; the fork
path's runtime side is effectively Linux-only, so the real `ForkRunnerLauncher` is
**compile-coverage** — `cargo check --tests` proves it builds (the `cfg(not(linux))`
`openpt` branch keeps the crate buildable on darwin). The **fake-launcher engine
tests are the authoritative behavioral signal regardless of host** and run on darwin
(`PtyMaster` over a real `openpt` pair runs there too). The parent whole-workspace
`cargo test` remains the integration gate; if it is blocked by a **pre-existing**
host/Linux constraint, record the exact failing target + message and confirm it
reproduces on `main` before this phase — Phase 2 adds no new platform-specific
runtime path. `cargo clippy … -- -D warnings` is the gate that the §2.1 seam was
chosen to satisfy.

---

## 7. Acceptance Criteria Checklist

```text
- [ ] child-exit → promise resolves with the finalized `Output`: a fake `RunnerChild`
      `complete(RunResult)` drives `op.finalize` (shell) and the parse closure (mount)
      to `Ok(Output)`; `exec.wait()` yields it and the fake observer records on_terminal.
- [ ] `finalize` / parse error → promise resolves with a terminal `NamespaceExecutionError`;
      observer records on_terminal with status = Error.
- [ ] `CompletionPromise::wait_timeout(Duration) -> bool` blocks then returns true on a
      resolve from another thread (no poll); returns false on a pending promise.
- [ ] `cancel()` (the boxed action) unblocks the watcher while it is blocked in
      `wait_completion()` — the fake child blocks until the cancel trips its signal,
      and the promise resolves promptly (real concurrent unblock, no real fork).
- [ ] admission: the (`max_active`+1)th `run_*` against blocking fakes returns
      `Err(Admission { max_active })`; after one fake completes, a further `run_*` admits.
- [ ] `run_mount(flag, target, id, parse)` resolves the parsed `Output`; the synchronous
      `.wait()` path returns it (`ExecutionHandle`, no PTY).
- [ ] `namespace_execution_id` is the runner `request_id`: the fake launcher's recorded
      `request.request_id == id.0`, and `exec.id().0 == id.0` (the registry key).
- [ ] the new launcher still passes `--start-ack-fd` and writes the ack byte
      (`rg "start[-_]ack" …/namespace-execution/src` shows it; Phase 6 removes it).
- [ ] `RunnerOutcome::status()` maps payload `"ok"/"error"/"timed_out"/"cancelled"` to the
      relocated `NamespaceExecutionTerminalStatus` (default Error); `payload()` returns `&Value`.
- [ ] `NamespaceExecutionTerminalStatus` is defined once (engine `src/status.rs`) with the
      original derives/variants/strings; `operation` re-exports it; `sandbox_runtime::…`
      and `crate::namespace_execution::…` both still resolve (daemon + operation tests green).
- [ ] no Phase 3-6 symbol leaked into the crate (absence grep), and no
      command/workspace/daemon source file changed (only the one operation re-export shim).
- [ ] `cargo clippy -p sandbox-runtime-namespace-execution --all-targets --no-deps -- -D warnings`
      is clean (the §2.1 boxed-trait seam passes `private_interfaces`/`private_bounds`).
- [ ] `cargo test -p sandbox-runtime --tests` and `cargo check -p sandbox-daemon` pass
      (terminal-status relocation is regression-free).
- [ ] `git diff --check` passes; actual LOC reported via `git diff --numstat`.
```

---

## 8. Anchor Ledger

Every row verified against the live checkout while authoring this spec (no line
numbers from memory, the Phase 1 spec, or the design doc).

| Anchor | Fact used | Verdict |
|---|---|---|
| `migration-phases.md:86-121` | Phase 2 contract: add engine/launcher/pty; watcher does one blocking `wait_completion`; fake launcher; exit tests | confirmed |
| `migration-phases.md:106-112,262-264` | start-ack KEEP until Phase 6 (atomic cut across launcher + daemon child) | confirmed |
| `namespace-execution.md:171-216` | handle/promise API; `cancel` = killpg from caller; `Mutex<Option<Result>>+Condvar` | confirmed |
| `namespace-execution.md:218-256` | `RunnerOutcome::{status,exit_code,payload}`; `ShellOperation`; mount = 2 `run_mount` closures, no trait | confirmed |
| `namespace-execution.md:258-316` | engine struct; `NsRunnerLauncher::spawn_pty/spawn_piped`; dispatch steps 1-8; "~3 + observer, 0 poll" | confirmed |
| `namespace-execution.md:432-457` | finalize inline; promise-resolved ⟹ completed exists; cancel independent of watcher | confirmed |
| `namespace-execution.md:493-498` | `ExecutionObserver::on_terminal(id, status, exit_code)`; `begin` stays in operation | confirmed |
| `namespace-execution.md:573-588` | engine ≈180 / launcher ≈180 / pty ≈120 LOC seeds | confirmed |
| `crates/sandbox-runtime/namespace-execution/src/lib.rs:15-20` | Phase 1 8 public re-exports; promise/registry are `mod` only | confirmed |
| `…/namespace-execution/src/promise.rs:8-76` | `CompletionPromise` + `allow(dead_code)` guards to drop; `wait_timeout(Duration)->bool` | confirmed |
| `…/namespace-execution/src/execution.rs:6-58` | `ExecutionHandle{id,promise}`; `InteractiveExecution{exec}` — no pty/cancel yet | confirmed |
| `…/namespace-execution/src/registry.rs:5-18` | placeholder `{max_active}` to grow into live+completed+admission | confirmed |
| `…/namespace-execution/src/shell.rs:6-24` | `RunnerOutcome(RunResult)` + `exit_code()` only; `ShellOperation` trait | confirmed |
| `…/namespace-execution/src/observer.rs:5-7` | `ExecutionObserver` with `on_running` only | confirmed |
| `…/namespace-execution/src/error.rs:5-29` | `Spawn/Finalize/Admission`; hand-rolled `Display`/`Error` (no thiserror) | confirmed |
| `…/namespace-execution/src/target.rs:8-13` | `NamespaceTarget` 5 fields; `ns_fds: NsFds` | confirmed |
| `…/namespace-execution/Cargo.toml:8-12` | Phase 1 = 1 dep (namespace-process); add serde_json/rustix/nix here | confirmed |
| `command/src/pty.rs:318-375` | `spawn_current_exe_ns_runner`: pipes, `openpt`, `--start-ack-fd`, `process_group(0)`, drop read ends, readers | confirmed |
| `command/src/pty.rs:292-309,391-396` | `allow_start` writes ack byte then request; start-ack pipe fd flags | confirmed |
| `command/src/pty.rs:463-490` | `open_pty_pair` (linux + `cfg(not(linux))` `ptsname`); `terminate_process_group` killpg SIGTERM→SIGKILL | confirmed |
| `command/src/pty.rs:398-451` | PTY-output reader thread; result-fd reader thread (engine drops the latter) | confirmed |
| `command/src/pty.rs:11-12,486-488` | `nix::sys::signal::{killpg,Signal}` + `nix::unistd::Pid` — the only nix usage relocated | confirmed |
| `command/src/pty.rs:13-21` | rustix `event`/`fs`/`io`/`pipe`/`pty` imports → features pty/event/pipe (+ baseline fs) | confirmed |
| `command/src/pty.rs:158-171` | wire `payload.status` parse (`"status"` string) — model for `RunnerOutcome::status()` | confirmed |
| `command/src/process.rs:291-314` | `build_namespace_runner_request`: `request_id = spec.id`, `args = json!({command,cwd})`, target fields, `ns_fds: Some(...)` | confirmed |
| `command/Cargo.toml:9-13` | mirror set: `nix{process,signal}`, `rustix{pty,event,pipe}`, `serde_json` | confirmed |
| `command/src/lib.rs:15` | `#![forbid(unsafe_code)]` — relocated fork/PTY path is safe (no Phase 2 unsafe) | confirmed |
| `command/src/transcript.rs:11-40` | `TranscriptTimestampPrefixer` used by the relocated reader | confirmed |
| `daemon/src/runner.rs:97-102,120-172` | `RunnerCliConfig` flags; `--mount-overlay`/`--remount-overlay` parse; default `Run` (`:170`) | confirmed |
| `daemon/src/runner.rs:175-189` | `wait_for_start_ack` `read_exact`s the start-ack — child still needs it in Phase 2 | confirmed |
| `namespace-process/.../protocol.rs:21-41` | `NamespaceRunnerRequest` 8 fields (`ns_fds: Option<NsFds>`); `RunResult{exit_code:i32,payload:Value}` | confirmed |
| `workspace/.../setns_runner.rs:194-232` | `run_child` (mount fork, `process_group(0)`) the launcher subsumes; deletion is Phase 4 | confirmed |
| `workspace/.../setns_runner.rs:134-150` | `ns_runner_request` `isolated-{request}-{id}` format — deleted in Phase 4, not Phase 2 | confirmed |
| `operation/src/namespace_execution.rs:67-85` | `NamespaceExecutionTerminalStatus` enum + `as_str()` to relocate (§2.6) | confirmed |
| `operation/src/namespace_execution.rs:8,43,96` | Phase 1 id shim; record field + `CompleteNamespaceExecution` use the name in-module | confirmed |
| `operation/src/lib.rs:22-25` | `pub use namespace_execution::{… NamespaceExecutionTerminalStatus …}` flows through the shim | confirmed |
| `operation/.../finalize.rs:7-10`, `impls/exec_command.rs:14-17` | internal importers via `crate::namespace_execution::…` (untouched by the relocation) | confirmed |
| `daemon/src/observability/namespace_execution.rs:5-6,71-76` | imports `sandbox_runtime::NamespaceExecutionTerminalStatus`; `as_str` parity strings | confirmed |
| root `Cargo.toml:29-49` | `serde 1`, `serde_json 1`, `rustix 0.38{fs,mount,process,thread}`, `nix 0.29`, `libc 0.2` present | confirmed |
| root `Cargo.toml:73-86` | clippy denies `correctness`/`suspicious`; `-D warnings` makes rustc `private_*` lints fail | confirmed |
| `operation/Cargo.toml:10` | operation already depends on the engine crate (Phase 1) → no manifest change for the re-export | confirmed |
| nix-0.29 `src/lib.rs:183`, `src/unistd.rs:174-178`, `src/sys/signal.rs:1082` | `pub mod unistd` ungated; `Pid`/`from_raw` carry no `cfg`; `killpg` in `signal` → engine needs only `nix{signal}` | confirmed |
| `rustc -D private_interfaces -D private_bounds -D warnings` (scratch) | generic engine over `pub(crate)` launcher **FAILS**; boxed `pub(crate)` trait field **PASSES** (§2.1) | confirmed |

---

## Appendix — Phase 3-6 items named here (do not build in Phase 2)

`ExecCommand`/`CommandExecution`, command-service migration (`exec_command`/
`write_command_stdin`/`read_command_lines`), `core.rs` engine wiring, `CommandOutput`
DTO merge, `CommandTerminalResult` rebuild, `wait_timeout(&self) -> Option<&T>`,
`NamespaceExecutionStore → NamespaceExecutionLedger`, `impl ExecutionObserver` on the
ledger, `request_id → origin_request_id` (**Phase 3**); `From<WorkspaceEntry>`,
`setns_runner.rs` rewrite, `run_child`/`ns_runner_request` deletion, real mount/remount
`args`, the `isolated-{mode}-{id}` format deletion (**Phase 4**); remount-coordinator
queries (**Phase 5**); deletion of `command/src/pty.rs`/`process.rs`, the result-fd
reader thread on surviving paths, and the atomic start-ack removal across launcher +
`daemon/src/runner.rs` (**Phase 6**). Engine stays workspace-agnostic; no
`execution_kind`/`backing` axis; the cancel-override-in-`status()` and the file-backed
transcript are Phase 3 reconciliations of the design's final shape.
