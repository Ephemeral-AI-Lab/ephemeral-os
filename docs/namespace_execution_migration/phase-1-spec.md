# Phase 1 Spec — New Crate + Relocate `NamespaceExecutionId`

Implementation-ready spec for **Phase 1** of the Namespace Execution Engine
migration. Phase contract:
[`migration-phases.md` § "Phase 1"](./migration-phases.md). Design rationale:
[`docs/namespace-execution.md`](../namespace-execution.md). This document is
**spec only** — do not implement while reading it; build to the Acceptance
Criteria at the end.

Anchors were verified against the live checkout (see the Anchor Ledger). Where
the phase contract and live code conflicted, the phase *objective* (mechanical
scaffolding, behavior unchanged) was preserved and the implementation details
were corrected to match live code; every such correction is flagged inline.

---

## 1. Phase Boundary Statement

**Phase 1 delivers** a new, compiling, workspace-agnostic library crate
`sandbox-runtime-namespace-execution` wired into the workspace, plus the
mechanical relocation of the `NamespaceExecutionId` newtype out of the
`operation` crate (`sandbox-runtime`) and down into the new crate's `id.rs`. The
`operation` crate re-exports the moved type so every existing path
(`sandbox_runtime::NamespaceExecutionId`, `crate::namespace_execution::NamespaceExecutionId`)
keeps resolving. The new crate also lands the **type/trait skeletons** named by
the phase contract (`error`, `target`, `promise`, `execution`, `shell`,
`observer`, `registry`) at the minimum depth that compiles.

**Phase 1 intentionally does not deliver** any engine behavior: no
`NamespaceExecutionEngine`, no `NsRunnerLauncher`, no watcher thread, no fake
launcher, no `PtyMaster`/PTY relocation, no `run_shell_interactive`/`run_mount`,
no registry-backed lookup or admission enforcement, no command/workspace/daemon
call-site migration, and no observability-shape change. Nothing references the
engine; the crate is dead weight on the dependency graph by design.

**Why behavior must be unchanged at this boundary.** Phase 1 is a *move plus an
unreferenced skeleton*. The only externally observable surface it touches is the
resolution path of one type name. Because `operation` re-exports the moved id,
the public symbol `sandbox_runtime::NamespaceExecutionId` is byte-for-byte the
same type with the same `pub` tuple field and the same derives; every downstream
consumer (the `operation` command/finalize internals, the `operation` tests, and
the **`sandbox-daemon`** observability code + tests that construct
`NamespaceExecutionId("…")` directly) compiles and behaves exactly as before. No
runtime code path, DTO, or observability record changes.

---

## 2. Resolved Design Decisions (with live-code evidence)

The eight decisions the phase requires, settled:

1. **Crate dependency set — `sandbox-runtime-namespace-process` only.** Starting
   from the contract's "namespace-process plus serde/json, rustix, nix, libc"
   and removing everything the Phase 1 *type skeleton* does not need to compile:
   - **Keep `sandbox-runtime-namespace-process`** — `target.rs` names
     `protocol::NsFds` and `shell.rs` names `protocol::RunResult`
     (`runner/protocol.rs:14,38`, reachable via `pub mod runner`
     `lib.rs:10` → `pub mod protocol` `runner/mod.rs:16`).
   - **Defer `serde_json` to Phase 2** — only needed once `RunnerOutcome`
     exposes `payload() -> &serde_json::Value`; Phase 1 exposes only
     `exit_code() -> i64` (an `i32` widened), which needs no `serde_json`.
   - **Defer `serde` to Phase 2** — no Phase 1 type derives
     `Serialize`/`Deserialize` (`NamespaceTarget` is converted to the already-
     `serde` `NamespaceRunnerRequest` in Phase 2, not serialized itself).
   - **Defer `rustix`, `nix`, `libc` to Phase 2** — they exist for
     fork/PTY/`killpg` in `launcher.rs`/`pty.rs`/`engine.rs`, none of which are
     Phase 1 files.
   - **Do not add `thiserror`** — it is not in the contract's dependency set;
     `error.rs` hand-rolls `Display`/`Error` (~30 LOC). Phase 2 may add it.
2. **Public export surface — narrow.** `lib.rs` re-exports exactly:
   `NamespaceExecutionId`, `NamespaceExecutionError`, `NamespaceTarget`,
   `ExecutionHandle`, `InteractiveExecution`, `ShellOperation`, `RunnerOutcome`,
   `ExecutionObserver`. The internal mechanisms `CompletionPromise` and
   `ExecutionRegistry` are `pub(crate)` — declared, not re-exported (an
   `ExecutionHandle` owns a promise; the registry is engine-internal and the
   engine is Phase 2). No engine/launcher/pty symbols exist to export.
3. **`NamespaceExecutionId` move — exact.** Delete the definition at
   `operation/src/namespace_execution.rs:13-14`
   (`#[derive(Debug, Clone, PartialEq, Eq, Hash, PartialOrd, Ord)] pub struct NamespaceExecutionId(pub String);`)
   and recreate it verbatim — **same derives, same `pub` tuple field** — in
   `namespace-execution/src/id.rs`. Replace the deleted lines with a re-export
   shim `pub use sandbox_runtime_namespace_execution::NamespaceExecutionId;`. The
   `pub` field and `Eq + Hash + Ord` derives are load-bearing: the type is a
   `HashMap` key (`namespace_execution.rs:28`), is `.cmp()`-sorted
   (`:280-283`), and is constructed field-first by the daemon tests
   (`NamespaceExecutionId("namespace_execution_1".to_owned())`,
   `daemon/tests/unit/observability.rs:90`). The operation tests that assert
   `id.0 == "namespace_execution_1"` (`operation/tests/namespace_execution.rs:10,127`)
   compile unchanged through the re-export.
4. **`NamespaceTarget` — included in Phase 1, workspace-free.** Five fields per
   the design (`docs/namespace-execution.md:162-168`); `ns_fds` is
   `sandbox_runtime_namespace_process::runner::protocol::NsFds`, reusing the
   runner-protocol type rather than redefining it. No `workspace` dependency, no
   `WorkspaceSessionId`, no `timeout` field (timeout is per-exec, on the op).
5. **Promise + execution-handle skeletons — compile-now only.**
   `CompletionPromise<T>` is a real, self-contained `Mutex` + `Condvar`
   primitive (`new`/`resolve`/`is_resolved`/`wait`/`wait_timeout`).
   `ExecutionHandle<T>` carries `{ id, promise }` with inherent `new`/`id`/
   `is_finished`/`wait`. `InteractiveExecution<T>` carries `{ exec }` (composition,
   no `Deref`) with forwarding `new`/`execution`/`id`/`is_finished`/`wait`. The
   **PTY field and `write_stdin`/`read_output_since`/`output_len`/`cancel`,** and
   the peeking **`wait_timeout(&self) -> Option<&T>`,** are deferred to Phase 2
   (they need `PtyMaster` and the engine/command yield path). There is **no**
   `Execution<T>` trait.
6. **Observer + registry skeletons — minimal types, no store duplication.**
   `observer.rs` declares `ExecutionObserver: Send + Sync` with `on_running(id)`
   only; `on_terminal(id, status, exit_code)` is deferred to Phase 3 because its
   `status: NamespaceExecutionTerminalStatus` parameter is a type that still
   lives in `operation/src/namespace_execution.rs:69` (the new crate cannot name
   it without a dependency cycle; its relocation/visibility is a later-phase
   concern). `registry.rs` declares `ExecutionRegistry` as a `pub(crate)`
   placeholder (`new(max_active)` + a `max_active()` accessor); the live/completed
   maps, id-keyed lookup, and admission enforcement are Phase 2/3. Phase 1 adds
   **no** copy of `NamespaceExecutionStore`'s projection/retention logic.
7. **Operation re-export — two files only.** Edit
   `operation/Cargo.toml` `[dependencies]` (`:8-14`) to add
   `sandbox-runtime-namespace-execution.workspace = true`, and edit
   `operation/src/namespace_execution.rs` to swap the struct definition for the
   `pub use` shim. **No other `operation` file changes:** `lib.rs:21-25` already
   re-exports `NamespaceExecutionId` *through* `namespace_execution`, and every
   internal importer uses `crate::namespace_execution::NamespaceExecutionId`
   (`services.rs:6`, `command/service/core.rs:7`, `command/service/finalize.rs:8`,
   `command/service/process_store.rs:12`,
   `command/service/impls/exec_command.rs:15`) — all of which keep resolving via
   the shim. (Those internal importers are slated for deletion in Phase 3; Phase 1
   leaves them untouched.)
8. **Verification boundary.** The parent exit criterion is "`cargo build` +
   `cargo test` whole workspace green," so the full-workspace `cargo test` **is**
   required as the gate. The smallest *meaningful, fast* Phase-1 checks are
   `cargo check -p sandbox-runtime-namespace-execution` (the new crate stands
   alone), `cargo test -p sandbox-runtime --tests` (the re-export + id move
   regression), and `cargo check -p sandbox-daemon` (the second consumer of the
   re-exported id). The new crate contains only platform-neutral type code, so it
   builds on any host; if a *pre-existing* host/Linux constraint blocks the full
   `cargo test` on the dev machine, that is orthogonal to Phase 1 and must be
   recorded with evidence (not attributed to this phase).

---

## 3. Resulting File/Folder Structure

After Phase 1 (`← NEW`, `△` edited, `[unchanged]`). Unit tests live in inline
`#[cfg(test)] mod` blocks (required: `CompletionPromise` and `ExecutionRegistry`
are `pub(crate)` and unreachable from a `tests/` integration dir). No new
`tests/` directory is added.

```text
crates/sandbox-runtime/
  namespace-execution/                         ← NEW crate (engine; workspace-agnostic)
    Cargo.toml                                 ← NEW  manifest: 1 dep (namespace-process), workspace meta + lints
    src/
      lib.rs                                   ← NEW  module decls + narrow re-exports
      id.rs                                    ← NEW  NamespaceExecutionId (moved from operation) + inline test
      error.rs                                 ← NEW  NamespaceExecutionError (hand-rolled Display/Error)
      target.rs                                ← NEW  NamespaceTarget (5 fields; ns_fds: protocol::NsFds)
      promise.rs                               ← NEW  pub(crate) CompletionPromise<T> (Mutex+Condvar) + inline tests
      execution.rs                             ← NEW  ExecutionHandle<T>, InteractiveExecution<T> + inline test
      shell.rs                                 ← NEW  ShellOperation trait, RunnerOutcome(RunResult)
      observer.rs                              ← NEW  ExecutionObserver (on_running only)
      registry.rs                              ← NEW  pub(crate) ExecutionRegistry placeholder + inline test
  operation/
    Cargo.toml                                 △  + sandbox-runtime-namespace-execution.workspace = true
    src/
      lib.rs                                   [unchanged]  (re-export flows through namespace_execution)
      namespace_execution.rs                   △  delete struct def → add `pub use …::NamespaceExecutionId;`
      services.rs                              [unchanged]
    tests/
      namespace_execution.rs                   [unchanged]  (regression proof via the re-export)
      exec_command.rs                          [unchanged]  (asserts id.0 strings via the re-export)
  namespace-process/                           [unchanged]  (NsFds / RunResult source of truth)
crates/sandbox-daemon/                         [unchanged]  (consumes sandbox_runtime::NamespaceExecutionId)
Cargo.toml                                     △  members += namespace-execution; [workspace.dependencies] += path dep
```

---

## 4. Touched-File LOC Change Ledger

Estimates are honest and narrow. The implementer **must** report actual deltas
after implementation with `git diff --numstat`.

| File | Change | Est. LOC delta | Why |
|---|---:|---:|---|
| `Cargo.toml` (root) | edit | `+2` | `members` += new crate; `[workspace.dependencies]` += `sandbox-runtime-namespace-execution = { path = … }` |
| `crates/sandbox-runtime/namespace-execution/Cargo.toml` | add | `+14` | package meta (workspace inherits), 1 dependency, `[lints] workspace = true` |
| `…/namespace-execution/src/lib.rs` | add | `+24` | 8 `mod`s + 6 `pub use` re-export lines |
| `…/namespace-execution/src/id.rs` | add | `+18` | newtype + 7 derives + inline test |
| `…/namespace-execution/src/error.rs` | add | `+30` | enum (3 variants) + `Display` + `Error` |
| `…/namespace-execution/src/target.rs` | add | `+18` | struct (5 fields) + derives + `NsFds` import |
| `…/namespace-execution/src/promise.rs` | add | `+70` | `CompletionPromise<T>` + 2 inline tests |
| `…/namespace-execution/src/execution.rs` | add | `+70` | `ExecutionHandle<T>` + `InteractiveExecution<T>` + forwarding + inline test |
| `…/namespace-execution/src/shell.rs` | add | `+25` | `ShellOperation` trait + `RunnerOutcome(RunResult)` + `exit_code()` |
| `…/namespace-execution/src/observer.rs` | add | `+12` | `ExecutionObserver` trait (`on_running`) |
| `…/namespace-execution/src/registry.rs` | add | `+18` | placeholder `ExecutionRegistry` + inline test |
| `crates/sandbox-runtime/operation/Cargo.toml` | edit | `+1` | `sandbox-runtime-namespace-execution.workspace = true` |
| `crates/sandbox-runtime/operation/src/namespace_execution.rs` | edit | `~0` (`-2`/`+1`) | delete 2-line struct def, add 1-line `pub use` shim |

New-crate source subtotal ≈ **+285**; with the manifest ≈ **+299**. Net repo
delta ≈ **+300**. Deletes: none (the type is *moved*, not removed; its old
location becomes a one-line shim).

---

## 5. File-By-File Implementation Spec

### 5.1 `Cargo.toml` (root) — edit

**Responsibility.** Register the new crate as a workspace member and declare its
path dependency once.

**Edits.**
- In `members` (`:3-17`), add `"crates/sandbox-runtime/namespace-execution",`
  (group it with the other `sandbox-runtime/*` members, e.g. after
  `"crates/sandbox-runtime/namespace-process",` at `:13`).
- In `[workspace.dependencies]` (`:25-68`), add, beside the sibling path deps
  (e.g. after `sandbox-runtime-namespace-process` at `:60`):

  ```toml
  sandbox-runtime-namespace-execution = { path = "crates/sandbox-runtime/namespace-execution" }
  ```

**Non-goals.** Do not touch `[workspace.lints]`, profiles, or any external
dependency line.

---

### 5.2 `crates/sandbox-runtime/namespace-execution/Cargo.toml` — new

**Responsibility.** Manifest for the new library crate; inherits workspace
package metadata and lints; declares the single Phase 1 dependency.

```toml
[package]
name = "sandbox-runtime-namespace-execution"
version.workspace = true
edition.workspace = true
rust-version.workspace = true
license.workspace = true

[dependencies]
sandbox-runtime-namespace-process.workspace = true

[lints]
workspace = true
```

**Decisions / non-goals.**
- Exactly one dependency. `serde`, `serde_json`, `rustix`, `nix`, `libc`,
  `thiserror` are **not** added (see Decision 1); each is a Phase 2 trigger.
- No `[dev-dependencies]`: Phase 1 inline tests use only `std` (no `RunResult`
  construction, which would pull `serde_json`).
- Mirror `namespace-process/Cargo.toml:1-23` for shape (workspace inheritance +
  `[lints] workspace = true`).

---

### 5.3 `src/lib.rs` — new

**Responsibility.** Declare modules; re-export the narrow public surface; keep
the engine internals (`promise`, `registry`) crate-private.

```rust
//! Daemon-side namespace execution engine — types and traits (Phase 1 skeleton).
//!
//! Workspace-agnostic: callers pass a `NamespaceTarget`, never a workspace type,
//! so this crate sits below `workspace` in the dependency graph.

mod error;
mod execution;
mod id;
mod observer;
mod promise;
mod registry;
mod shell;
mod target;

pub use error::NamespaceExecutionError;
pub use execution::{ExecutionHandle, InteractiveExecution};
pub use id::NamespaceExecutionId;
pub use observer::ExecutionObserver;
pub use shell::{RunnerOutcome, ShellOperation};
pub use target::NamespaceTarget;
```

**Decisions / non-goals.**
- `promise` and `registry` are `mod` (not `pub use`d): `CompletionPromise` and
  `ExecutionRegistry` are `pub(crate)`.
- **Do not** add `#![forbid(unsafe_code)]`. Phase 2 (`launcher.rs`/`pty.rs`)
  requires `unsafe` for fork/PTY/`killpg`; the workspace lint
  `undocumented_unsafe_blocks = "deny"` (`Cargo.toml:80`) already governs it.
  (Contrast `operation/src/lib.rs:1`, which forbids unsafe — appropriate there,
  not here.)
- No re-export of `CompletionPromise`, `ExecutionRegistry`, or any Phase 2 type.

---

### 5.4 `src/id.rs` — new (the move)

**Responsibility.** Own `NamespaceExecutionId`, relocated verbatim from
`operation`.

```rust
/// One namespace-execution identity: the runner `request_id`, the registry key,
/// and (wrapped as `CommandSessionId`) the public face of the command API.
#[derive(Debug, Clone, PartialEq, Eq, Hash, PartialOrd, Ord)]
pub struct NamespaceExecutionId(pub String);

#[cfg(test)]
mod tests {
    use super::NamespaceExecutionId;
    use std::collections::HashSet;

    #[test]
    fn newtype_exposes_inner_and_is_hashable() {
        let id = NamespaceExecutionId("namespace_execution_1".to_owned());
        assert_eq!(id.0, "namespace_execution_1");
        let mut set = HashSet::new();
        assert!(set.insert(id.clone()));
        assert!(!set.insert(id)); // Eq + Hash round-trip
    }
}
```

**Move mechanics (exact).** Copy derives and the `pub` field exactly as at
`operation/src/namespace_execution.rs:13-14`. Do not add/remove a derive (`Ord`
and `Hash` are both relied on downstream). Do not add `Serialize`/`Deserialize`
(the operation original has none; the daemon serializes via its own observability
rows, not this type).

**Non-goals.** No `allocate_*` constructor or `format!("namespace_execution_{n}")`
logic — that stays on `NamespaceExecutionStore` in `operation`
(`namespace_execution.rs:144-147`), which keeps minting ids through Phase 2.

---

### 5.5 `src/error.rs` — new

**Responsibility.** The crate-wide error type named by `CompletionPromise`,
`ExecutionHandle::wait`, and `ShellOperation::finalize`.

```rust
use std::fmt;

/// Failures surfaced by the namespace execution engine.
#[derive(Debug)]
pub enum NamespaceExecutionError {
    /// The runner could not be launched (fork/pipe/PTY setup).
    Spawn(String),
    /// An operation's `finalize` rejected the runner outcome.
    Finalize(String),
    /// Admission refused because `max_active` live executions are in flight.
    Admission { max_active: usize },
}

impl fmt::Display for NamespaceExecutionError { /* match → human text */ }
impl std::error::Error for NamespaceExecutionError {}
```

**Decisions / non-goals.**
- Hand-rolled `Display`/`Error` (no `thiserror`, Decision 1).
- Three variants only. They are **not constructed in Phase 1** (no launcher, no
  finalize, no admission enforcement); they exist so the type can be named in
  signatures. No `dead_code` warning fires because the enum is public (re-exported
  by `lib.rs`). Phase 2 wires their first call sites.
- No `Cancelled`/`TimedOut` variant: cancel/timeout are terminal *statuses*
  (`NamespaceExecutionTerminalStatus`), not engine errors — a cancelled run still
  resolves `Ok` with that status.

---

### 5.6 `src/target.rs` — new

**Responsibility.** The workspace-free boundary type the engine and both
operation families speak.

```rust
use std::path::PathBuf;

use sandbox_runtime_namespace_process::runner::protocol::NsFds;

/// Workspace identity for a namespace execution; built once, reused per exec.
/// No timeout: that is per-exec and lives on the operation.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct NamespaceTarget {
    pub workspace_root: PathBuf,
    pub layer_paths: Vec<PathBuf>,
    pub upperdir: Option<PathBuf>,
    pub workdir: Option<PathBuf>,
    pub ns_fds: NsFds,
}
```

**Decisions / non-goals.**
- `ns_fds: NsFds` reuses `runner/protocol.rs:14` (verified `pub`, reachable at
  `sandbox_runtime_namespace_process::runner::protocol::NsFds`). `NsFds` is
  `Copy + Clone + PartialEq + Eq`, so deriving `Clone, PartialEq, Eq` on
  `NamespaceTarget` is free and useful for tests.
- **No** `From<WorkspaceEntry>` impl — that is Phase 4 and lives in the
  `workspace` crate (orphan rule). The new crate has zero `workspace` dependency.
- No `serde` derive (Decision 1); the target is converted into the already-`serde`
  `NamespaceRunnerRequest` in Phase 2, not serialized here.

---

### 5.7 `src/promise.rs` — new (`pub(crate)`)

**Responsibility.** The single internal "done?" truth: a condvar-backed,
write-once completion cell. Replaces (in later phases) the command path's
`FinalizationState` machine and its poll loops.

```rust
use std::sync::{Condvar, Mutex};
use std::time::Duration;

use crate::error::NamespaceExecutionError;

pub(crate) struct CompletionPromise<T> {
    slot: Mutex<Slot<T>>,
    ready: Condvar,
}

enum Slot<T> {
    Pending,
    Ready(Result<T, NamespaceExecutionError>),
    Taken,
}

impl<T> CompletionPromise<T> {
    pub(crate) fn new() -> Self;
    /// Pending → Ready, then `notify_all`. Returns `false` if already resolved.
    pub(crate) fn resolve(&self, outcome: Result<T, NamespaceExecutionError>) -> bool;
    pub(crate) fn is_resolved(&self) -> bool;          // Ready | Taken
    /// Block until resolved, then take the value (single-consumer).
    pub(crate) fn wait(&self) -> Result<T, NamespaceExecutionError>;
    /// Block up to `timeout`; return `is_resolved()`.
    pub(crate) fn wait_timeout(&self, timeout: Duration) -> bool;
}
```

**Tests (inline).**
- `resolve` before `wait`: `resolve(Ok(v))` → `is_resolved()` → `wait()` yields `v`.
- `wait_timeout` on a pending promise returns `false` within a small bound
  (proves the timeout path; no busy poll).

**Decisions / non-goals.**
- Single-consumer by construction: `ExecutionHandle::wait(self)` consumes the
  handle, so exactly one `wait` runs. `wait` takes the value (`Ready → Taken`).
- **No** `wait_timeout(&self) -> Option<&T>` peek API here — borrowing a value
  out of the `Mutex` is an engine/command-yield concern (Phase 2/3).
- `lock().expect(...)` for poisoned-mutex handling (not `unwrap`; the workspace
  warns `unwrap_used`, `Cargo.toml:78`).
- No `Arc`/sharing with a watcher yet (the watcher is Phase 2); the handle owns
  the promise directly.

---

### 5.8 `src/execution.rs` — new

**Responsibility.** The genus/species handle pair, by composition, with inherent
+ forwarded methods. No `Execution<T>` trait, no `Deref`.

```rust
use crate::error::NamespaceExecutionError;
use crate::id::NamespaceExecutionId;
use crate::promise::CompletionPromise;

/// Genus: id + completion promise.
pub struct ExecutionHandle<T> {
    id: NamespaceExecutionId,
    promise: CompletionPromise<T>,
}

impl<T> ExecutionHandle<T> {
    pub(crate) fn new(id: NamespaceExecutionId, promise: CompletionPromise<T>) -> Self;
    pub fn id(&self) -> &NamespaceExecutionId;
    pub fn is_finished(&self) -> bool;                       // → promise.is_resolved()
    pub fn wait(self) -> Result<T, NamespaceExecutionError>; // → promise.wait()
}

/// Species: an `ExecutionHandle` plus interactive (PTY) capability.
/// Phase 1 carries the handle only; the PTY field + stdin/stream/cancel land in
/// Phase 2 with `PtyMaster`.
pub struct InteractiveExecution<T> {
    exec: ExecutionHandle<T>,
}

impl<T> InteractiveExecution<T> {
    pub(crate) fn new(exec: ExecutionHandle<T>) -> Self;
    pub fn execution(&self) -> &ExecutionHandle<T>;          // explicit, no Deref
    pub fn id(&self) -> &NamespaceExecutionId;               // forwards
    pub fn is_finished(&self) -> bool;                       // forwards
    pub fn wait(self) -> Result<T, NamespaceExecutionError>; // forwards
}
```

**Test (inline).** Build a resolved `CompletionPromise::<u32>`, wrap it in
`ExecutionHandle::new`, wrap that in `InteractiveExecution::new`, then assert
`id()` forwards, `is_finished()` is `true`, and `wait()` yields the value —
proving composition + forwarding without any fork/PTY.

**Deferred to Phase 2 (explicit non-goals for this file):**
- the `pty: PtyMaster` field on `InteractiveExecution`;
- `write_stdin`, `read_output_since`, `output_len`, `cancel`;
- `wait_timeout(&self, Duration) -> Option<&T>` (the yield-path peek);
- sharing the promise with a watcher (likely `Arc<CompletionPromise<T>>`).

---

### 5.9 `src/shell.rs` — new

**Responsibility.** The shell-family strategy trait and the one wire-outcome
newtype. No `MountOperation` trait (mount is Phase 4 closures); no
`InteractiveShellOperation` marker; no `ShellOutcome`/`ShellStatus`/`FinalizeCx`.

```rust
use sandbox_runtime_namespace_process::runner::protocol::RunResult;

use crate::error::NamespaceExecutionError;

/// One wire result for both families (newtype over the runner's `RunResult`).
pub struct RunnerOutcome(RunResult);

impl RunnerOutcome {
    pub fn exit_code(&self) -> i64; // i64::from(self.0.exit_code)
}

/// Shell family (`Run` mode → `shell_exec`). Each shell op is a strategy.
pub trait ShellOperation: Send + 'static {
    type Output: Send + 'static;
    fn operation_name(&self) -> &'static str;
    fn command(&self) -> &str;
    fn timeout_seconds(&self) -> Option<f64>;
    fn finalize(
        self: Box<Self>,
        outcome: RunnerOutcome,
    ) -> Result<Self::Output, NamespaceExecutionError>;
}
```

**Decisions / non-goals.**
- `exit_code()` reads `self.0.exit_code` (an `i32`, `runner/protocol.rs:39`) →
  no `serde_json`, and the field read suppresses any `dead_code` lint.
- `status() -> NamespaceExecutionTerminalStatus` and
  `payload() -> &serde_json::Value` are **deferred to Phase 2** (they need the
  terminal-status enum, still in `operation`, and `serde_json`).
- `ShellOperation` is declared but **unimplemented** in Phase 1 — `ExecCommand`
  is Phase 3. A public trait with no impl raises no warning.
- No `pub(crate) fn new(RunResult)` for `RunnerOutcome` in Phase 1: adding one
  and a test would require constructing a `RunResult` (a `serde_json::Value`
  field), pulling `serde_json` into dev-deps. Phase 2 adds the constructor when
  the launcher first produces an outcome.

---

### 5.10 `src/observer.rs` — new

**Responsibility.** Decouple tracking from the engine. Phase 1 declares the seam;
the `operation`-side implementation (`NamespaceExecutionLedger`) is Phase 3.

```rust
use crate::id::NamespaceExecutionId;

/// Drives running/terminal lifecycle by id. `begin` stays in the operation layer
/// (it owns the `WorkspaceSessionId`), so the engine needs no workspace knowledge.
pub trait ExecutionObserver: Send + Sync {
    fn on_running(&self, id: &NamespaceExecutionId);
}
```

**Decisions / non-goals.**
- `on_terminal(&self, id, status: NamespaceExecutionTerminalStatus, exit_code: Option<i64>)`
  is **deferred**: `NamespaceExecutionTerminalStatus` lives in
  `operation/src/namespace_execution.rs:69`; naming it here would invert the
  dependency (`operation → namespace-execution`) into a cycle. Adding `on_terminal`
  is bundled with the Phase 3 ledger work, where the status type's location is
  settled. The single-method trait is a deliberate interim — nothing implements
  or calls it in Phase 1.
- No `begin`/`WorkspaceSessionId` on the trait, by design.

---

### 5.11 `src/registry.rs` — new (`pub(crate)`)

**Responsibility.** Placeholder for the engine's single source of truth
(live + completed, admission). Phase 1 declares the type and its capacity only.

```rust
/// Live + completed executions keyed by `NamespaceExecutionId`, with admission.
/// Phase 1: capacity placeholder only — the maps, id lookup, and `try_reserve`
/// land in Phase 2/3.
pub(crate) struct ExecutionRegistry {
    max_active: usize,
}

impl ExecutionRegistry {
    pub(crate) fn new(max_active: usize) -> Self;
    pub(crate) fn max_active(&self) -> usize;
}
```

**Test (inline).** `ExecutionRegistry::new(2).max_active() == 2` (keeps the field
live → no `dead_code`).

**Decisions / non-goals (explicit, to avoid pulling Phase 2 forward).**
- **No** `active`/`completed` maps, **no** `insert`/`complete`/`live`/`completed`
  lookup, **no** `try_reserve`/admission enforcement, **no** id-keyed queries.
  The Phase 2 exit test "admission rejects past `max_active`" is an *engine*
  integration test (`engine.run_*` refusing) — distinct from this placeholder.
- Phase 1 does **not** reproduce any `NamespaceExecutionStore` behavior
  (projection buffers, retention, partial errors); that stays in `operation`.

---

### 5.12 `crates/sandbox-runtime/operation/Cargo.toml` — edit

**Responsibility.** Let `operation` depend on the new crate so it can re-export
the moved id.

**Edit.** In `[dependencies]` (`:8-14`), add:

```toml
sandbox-runtime-namespace-execution.workspace = true
```

**Non-goals.** No other dependency change; `[lints] workspace = true` (`:16-17`)
stays.

---

### 5.13 `crates/sandbox-runtime/operation/src/namespace_execution.rs` — edit (the shim)

**Responsibility.** Stop *defining* `NamespaceExecutionId`; start *re-exporting*
it, so both `crate::namespace_execution::NamespaceExecutionId` and
`sandbox_runtime::NamespaceExecutionId` keep resolving.

**Edits (surgical).**
- **Delete** `:13-14`:

  ```rust
  #[derive(Debug, Clone, PartialEq, Eq, Hash, PartialOrd, Ord)]
  pub struct NamespaceExecutionId(pub String);
  ```
- **Add**, near the top (after the existing
  `use crate::workspace_crate::WorkspaceSessionId;` at `:6`):

  ```rust
  pub use sandbox_runtime_namespace_execution::NamespaceExecutionId;
  ```

**Why this is sufficient.** The file's many in-module uses of
`NamespaceExecutionId` (`:28`, `:36`, `:105`, `:151`, `:191`, `:210`, `:302`,
`:361`, and the `allocate_*`/`begin_*`/`complete_*` bodies) resolve to the
re-exported name unchanged. `lib.rs:21-25`'s
`pub use namespace_execution::{… NamespaceExecutionId …}` continues to work
because the symbol still exists at `crate::namespace_execution::NamespaceExecutionId`
— now by re-export. The `allocate_namespace_execution_id` formatter
(`:144-147`) is untouched, so `id.0 == "namespace_execution_1"` still holds.

**Non-goals (Phase 3, not now).** Do **not** rename `NamespaceExecutionStore` →
`NamespaceExecutionLedger`, do **not** `impl ExecutionObserver`, do **not** rename
`request_id` → `origin_request_id`. Leave `NamespaceExecutionStore`,
`BeginNamespaceExecution`, `CompleteNamespaceExecution`,
`NamespaceExecutionRecord`, `NamespaceExecutionLifecycle`,
`NamespaceExecutionTerminalStatus`, and `RuntimeNamespaceExecutionSnapshot`
exactly where they are.

---

### 5.14 Files deliberately **not** touched

| File | Why untouched |
|---|---|
| `operation/src/lib.rs` | Re-export flows through `namespace_execution`; `:21-25` unchanged. |
| `operation/src/services.rs` | Imports id via `crate::namespace_execution::…` (`:5-8`) — still resolves. |
| `operation/tests/namespace_execution.rs`, `tests/exec_command.rs` | Regression proof; assert `id.0` strings via the re-export. **No new test added** (would be redundant; the move's cross-path proof already exists). |
| `command/service/{core,finalize,process_store,impls/exec_command}.rs` | Import id via `crate::namespace_execution::…`; slated for Phase 3 deletion. Untouched now. |
| `crates/sandbox-daemon/**` | Consumes `sandbox_runtime::NamespaceExecutionId` (`observability/service.rs:15-19`; `tests/unit/observability.rs:10-15,90`); the re-export keeps it green with zero edits. |

---

## 6. Verification Commands

Run in order from the repo root (`export PATH="$PWD/bin:$PATH"` first, per
`CLAUDE.md`):

```sh
cargo fmt --check
cargo check -p sandbox-runtime-namespace-execution
cargo test  -p sandbox-runtime-namespace-execution
cargo check -p sandbox-daemon                       # second consumer of the re-exported id
cargo test  -p sandbox-runtime --tests              # re-export + id-move regression
cargo test                                          # parent exit gate (whole workspace)
rg -n "pub use .*NamespaceExecutionId" crates/sandbox-runtime/operation/src
# Phase 2+ symbols MUST be absent from the new crate:
rg -n "NamespaceExecutionEngine|NsRunnerLauncher|run_shell_interactive|run_mount|PtyMaster|RunnerChild|fn watcher|spawn_pty|spawn_piped" \
  crates/sandbox-runtime/namespace-execution/src || echo "no Phase 2 symbols ✓"
git diff --check
git diff --numstat                                  # report actual LOC deltas
```

**If a command is too broad or blocked:** the new crate is platform-neutral, so
`cargo check -p sandbox-runtime-namespace-execution` is authoritative for "the
crate compiles." The whole-workspace `cargo test` is the parent gate; if it is
blocked by a **pre-existing** host/Linux constraint (the dev host is darwin; the
namespace crates are `cfg(target_os = "linux")`-gated), record the exact failing
target + message as evidence and confirm it reproduces on `main` *before* this
phase's changes — Phase 1 adds no platform-specific code, so any such failure is
not introduced here. Prefer the narrower, definitely-meaningful trio
(`-p sandbox-runtime-namespace-execution`, `-p sandbox-runtime --tests`,
`check -p sandbox-daemon`) as the Phase-1 signal.

---

## 7. Acceptance Criteria Checklist

```text
- [ ] `cargo check -p sandbox-runtime-namespace-execution` passes.
- [ ] `cargo test -p sandbox-runtime-namespace-execution` passes (inline tests:
      id newtype, CompletionPromise resolve/timeout, handle composition+forwarding,
      registry capacity).
- [ ] `cargo test -p sandbox-runtime --tests` passes — `tests/namespace_execution.rs`
      and `tests/exec_command.rs` still assert `id.0 == "namespace_execution_1"`
      through `sandbox_runtime::NamespaceExecutionId`.
- [ ] `cargo check -p sandbox-daemon` passes — the daemon still imports and
      constructs `sandbox_runtime::NamespaceExecutionId` via the re-export.
- [ ] `cargo test` for the whole workspace passes, OR the spec/PR records the exact
      blocker with evidence that it pre-dates this phase (Phase 1 adds no
      platform-specific code).
- [ ] `rg -n "pub use .*NamespaceExecutionId" crates/sandbox-runtime/operation/src`
      shows the re-export in `namespace_execution.rs`.
- [ ] `NamespaceExecutionId` is defined in exactly one place
      (`namespace-execution/src/id.rs`) with the original 7 derives and `pub`
      tuple field; the old definition is gone from `operation`.
- [ ] The new crate depends on `sandbox-runtime-namespace-process` only
      (no serde/serde_json/rustix/nix/libc/thiserror).
- [ ] No Phase 2+ symbols exist in `namespace-execution/src`:
      `NamespaceExecutionEngine`, `NsRunnerLauncher`, watcher, `PtyMaster`,
      `RunnerChild`, `run_shell_interactive`, `run_mount`, `spawn_pty`/`spawn_piped`
      (absence grep passes).
- [ ] Observability is unchanged: no `execution_kind`/`backing` axis introduced;
      `NamespaceExecutionStore`/`*Record`/`*Lifecycle`/`*TerminalStatus` remain in
      `operation` (not renamed, not moved, no `ExecutionObserver` impl yet).
- [ ] Public surface is narrow: `lib.rs` re-exports only the 8 named types;
      `CompletionPromise` and `ExecutionRegistry` are `pub(crate)`.
- [ ] `cargo clippy -p sandbox-runtime-namespace-execution --all-targets --no-deps`
      is clean (no `unwrap_used`, no `dead_code`, no undocumented unsafe).
- [ ] `git diff --check` passes (no trailing whitespace / conflict markers).
- [ ] Actual LOC deltas reported via `git diff --numstat`.
```

---

## 8. Anchor Ledger

Every row verified against the live checkout while authoring this spec.

| Anchor | Fact used | Verdict |
|---|---|---|
| `Cargo.toml:3-17` | workspace `members` list; namespace-process member at `:13` | confirmed |
| `Cargo.toml:25-68` | `[workspace.dependencies]`; sibling path deps; namespace-process at `:60` | confirmed |
| `Cargo.toml:78,80` | `unwrap_used = "warn"`, `undocumented_unsafe_blocks = "deny"` | confirmed |
| `operation/Cargo.toml:8-14` | `[dependencies]` block to extend | confirmed |
| `operation/Cargo.toml:16-17` | `[lints] workspace = true` | confirmed |
| `operation/src/lib.rs:8` | `mod namespace_execution;` | confirmed |
| `operation/src/lib.rs:21-25` | `pub use namespace_execution::{… NamespaceExecutionId …}` (id at `:22`) | confirmed |
| `operation/src/namespace_execution.rs:13-14` | the `NamespaceExecutionId` definition (7 derives, `pub String`) to move | confirmed |
| `operation/src/namespace_execution.rs:28` | id used as `HashMap` key (needs `Eq + Hash`) | confirmed |
| `operation/src/namespace_execution.rs:144-147` | `allocate_namespace_execution_id` → `format!("namespace_execution_{n}")` stays in `operation` | confirmed |
| `operation/src/namespace_execution.rs:69` | `NamespaceExecutionTerminalStatus` lives in `operation` (blocks `on_terminal` in Phase 1) | confirmed |
| `operation/src/services.rs:5-8` | imports id via `crate::namespace_execution::…` (stays valid) | confirmed |
| `operation/tests/namespace_execution.rs:10,127` | `assert_eq!(id.0, "namespace_execution_1")` regression | confirmed |
| `operation/tests/namespace_execution.rs:163` | `sandbox_runtime::NamespaceExecutionId` return type | confirmed |
| `operation/src/command/service/{core.rs:7,finalize.rs:8,process_store.rs:12,impls/exec_command.rs:15}` | internal importers via `crate::namespace_execution::…` (untouched) | confirmed |
| `namespace-process/src/lib.rs:10` | `pub mod runner;` | confirmed |
| `namespace-process/src/runner/mod.rs:16` | `pub mod protocol;` | confirmed |
| `namespace-process/src/runner/protocol.rs:14` | `pub struct NsFds` (for `NamespaceTarget.ns_fds`) | confirmed |
| `namespace-process/src/runner/protocol.rs:38-40` | `pub struct RunResult { exit_code: i32, payload: Value }` (for `RunnerOutcome`) | confirmed |
| `namespace-process/Cargo.toml:1-23` | manifest shape to mirror (workspace inheritance + lints) | confirmed |
| `daemon/Cargo.toml:17` | `sandbox-runtime.workspace = true` (daemon depends on the re-export) | confirmed |
| `daemon/src/observability/service.rs:15-19` | imports `NamespaceExecutionId` via `sandbox_runtime::{…}` | confirmed |
| `daemon/tests/unit/observability.rs:10-15,90` | imports + constructs `NamespaceExecutionId("…".to_owned())` (needs `pub` field) | confirmed |
| new crate dir | `crates/sandbox-runtime/namespace-execution` absent (`ls` → No such file) | confirmed |

---

## Appendix — Phase 2+ deferrals named here (do not build in Phase 1)

`engine.rs` (`NamespaceExecutionEngine`, `run_shell_interactive`, `run_mount`,
Template-Method dispatch, watcher thread), `launcher.rs` (`NsRunnerLauncher`,
`spawn_pty`/`spawn_piped`, `RunnerChild::wait_completion`), `pty.rs`
(`PtyMaster` + transcript reader); the `InteractiveExecution` PTY field and
`write_stdin`/`read_output_since`/`output_len`/`cancel`; `ExecutionHandle`/promise
`wait_timeout(&self) -> Option<&T>`; `RunnerOutcome::{status,payload}` and its
constructor; `ExecutionObserver::on_terminal`; `ExecutionRegistry` maps +
`try_reserve` + id lookup; `NamespaceExecutionStore` → `NamespaceExecutionLedger`
rename, `impl ExecutionObserver`, `request_id` → `origin_request_id`; the
`From<WorkspaceEntry>` impl; all command/workspace/daemon call-site migrations;
start-ack removal. Each appears in `migration-phases.md` Phases 2-6.
