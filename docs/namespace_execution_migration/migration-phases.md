# Namespace Execution Engine — Migration Phases

Phased migration plan for the design in
[`docs/namespace-execution.md`](../namespace-execution.md) (rationale and the
full simplification scorecard in
[`docs/namespace-execution-adversarial-review-results.md`](../namespace-execution-adversarial-review-results.md)).

**There are 6 phases.** Each phase is independently shippable: the workspace
builds, `cargo test` passes, and externally observable behavior is unchanged at
every phase boundary. Phases 1–2 add the engine with no caller change; phases 3–5
move one producer at a time onto it; phase 6 deletes the now-dead code.

Net effect once complete: **≈ −1,280 LOC** and the next namespace operation costs
~30–80 LOC instead of ~700 (a fresh fork/promise/finalize/store stack).

---

## Invariants held at every phase boundary

These never break, mid-migration or after:

- **Observability surface unchanged.** One `active_namespace_executions` list,
  `operation_name` the only classification axis, generic `Starting/Running/Terminal`,
  **no** `execution_kind`/`backing` field. The observer record's
  `origin_request_id` (external origin id) stays distinct from
  `namespace_execution_id`.
- **Command behavior preserved.** One-shot vs existing session; remount-pending
  guard; Ctrl-C/Ctrl-D kill; yield/quiet-period semantics; limit validation
  (`1..=1000`); running-vs-terminal reads; transcript content.
- **No re-complication.** No reintroduced `CommandProcessStore` as the per-exec
  source of truth; no public `execution_kind`/`backing` axis; no `Deref`
  inheritance; the engine never depends on `workspace` types; the persistent
  runner server returns only *behind* the `NsRunnerLauncher` seam; no shims /
  aliases / dual-write paths.

---

## Phase summary

| # | Objective | Crates touched | Green-gate |
|---|---|---|---|
| 1 | New crate + relocate `NamespaceExecutionId` (mechanical) | `namespace-execution` (new), `operation` (re-export) | whole workspace builds + tests; nothing uses the engine yet |
| 2 | Launcher + engine dispatch + watcher (works against a fake) | `namespace-execution` | engine unit tests green; no command/workspace change |
| 3 | Command onto the engine; gut `CommandProcessStore` | `command`, `operation/command` | command + observability tests green |
| 4 | Mount family onto the engine | `workspace`, `daemon` | overlay mount + live remount tests green |
| 5 | Remount coordinator onto engine queries | `operation/workspace_remount` | quiesce/resume tests green |
| 6 | Cleanup: delete dead code; single launcher; drop start-ack | `command`, `daemon`, `namespace-execution` | full workspace + clippy clean; absence greps pass |

Dependency order: 1 → 2 → {3, 4} → 5 → 6. Phases 3 and 4 both depend only on 2
and may proceed in parallel; phase 5 depends on 3 (the registry must hold live
command executions); phase 6 depends on 3, 4, 5.

---

## Phase 1 — New crate + relocate the id

**Objective.** Stand up `sandbox-runtime-namespace-execution` with the engine's
*types and traits*, wired to nothing. Pure scaffolding.

**Add.** New crate `crates/sandbox-runtime/namespace-execution/`:
`lib.rs`, `id.rs`, `error.rs`, `target.rs` (`NamespaceTarget`, 5 fields,
`ns_fds: protocol::NsFds`), `promise.rs` (`CompletionPromise<T>`), `execution.rs`
(`ExecutionHandle<T>`, `InteractiveExecution<T>` — inherent methods, no
`Execution<T>` trait), `shell.rs` (`ShellOperation`, `RunnerOutcome`),
`observer.rs` (`ExecutionObserver`), `registry.rs`.

**Move.** `NamespaceExecutionId` from `operation` down into `id.rs`; `operation`
re-exports it (`pub use sandbox_runtime_namespace_execution::NamespaceExecutionId`)
so no existing path breaks.

**Edit.** Workspace `Cargo.toml` members + `[workspace.dependencies]`; the crate
depends **only** on `namespace-process` (+ serde/json/rustix/nix/libc).

**Exit criteria.** `cargo build` + `cargo test` whole workspace green. The engine
is unreferenced. `NamespaceExecutionId` resolves through the re-export everywhere
it did before.

**Verify.**
```sh
cargo check -p sandbox-runtime-namespace-execution
rg -n "pub use .*NamespaceExecutionId" crates/sandbox-runtime/operation/src
```

---

## Phase 2 — Launcher + engine dispatch + watcher

**Objective.** Make the engine functional end to end against a fake launcher, so
it is fully unit-tested before any real caller depends on it.

**Add.** `engine.rs` (`NamespaceExecutionEngine::run_shell_interactive` / `run_mount`, the
Template-Method dispatch, the watcher thread), `launcher.rs`
(`pub(crate) NsRunnerLauncher::spawn_pty`/`spawn_piped`, `RunnerChild` with
`wait_completion()`), `pty.rs` (`PtyMaster` + transcript reader, adapted from
`command/src/pty.rs`).

**Key shapes.** The watcher does one blocking `RunnerChild::wait_completion()`
(no poll), wraps the `RunResult` as `RunnerOutcome`, runs the op's `finalize`
inline, resolves the `CompletionPromise`, then `registry.complete(id)` and
`observer.on_terminal(...)`. `run_mount` takes a `(mode_flag, parse_closure)`
pair — no `MountOperation` trait, no `Backing` enum, no `NsRunnerMode` enum.

**Test wiring.** A fake `NsRunnerLauncher` returning a fake `RunnerChild` lets the
engine be tested with no fork.

> **Sequencing constraint — start-ack.** `wait_completion()` is the only
> completion signal in the new launcher, but the **start-ack handshake stays**
> until Phase 6. The in-namespace child (`namespace-process/.../runner.rs`) still
> `read_exact`s `--start-ack-fd`; until the *old* launch paths
> (`spawn_current_exe_ns_runner`, `run_child`) are gone, the new launcher must
> keep passing it or the child desyncs. The start-ack is removed from the
> launcher **and** the daemon child atomically in Phase 6.

**Exit criteria.** `cargo test -p sandbox-runtime-namespace-execution` green:
child-exit → promise resolves with the finalized `Output`; `finalize` error →
terminal error; `wait_timeout` blocks then returns on resolve (no poll);
`cancel()` (`killpg`) is responsive while the watcher blocks; admission rejects
past `max_active`; `run_mount(flag, …, parse)` resolves the parsed `Output`;
`namespace_execution_id` is the runner `request_id` and registry key. No
command/workspace changes.

---

## Phase 3 — Command onto the engine; gut `CommandProcessStore`

**Objective.** Re-express the three command APIs on the engine + its registry and
delete the store and its satellites.

**Add.** `command/src/exec.rs` (`ExecCommand: ShellOperation`, carrying its own
`WorkspaceSessionService` handle for the one-shot destroy in `finalize` — no
engine-provided `FinalizeCx`); `command/src/command_execution.rs`
(`CommandExecution` = `InteractiveExecution<CommandTerminalResult>` + transcript
cursor + session disposition).

**Edit.**
- `operation/command/service/core.rs` — hold `Arc<NamespaceExecutionEngine>`;
  reach `CommandExecution` through the registry (no second map); drop
  `process_store` + `completion_sender`.
- `impls/exec_command.rs` — allocate id, `ledger.begin(..)`, build `ExecCommand`,
  `engine.run_shell_interactive(.., id)`, initial yield.
- `impls/write_command_stdin.rs` / `read_command_lines.rs` — via the registry;
  drop the `cancellation` write.
- `service/contract.rs` — merge `CommandYield`/`CommandLinesOutput`/
  `CommandOutputSnapshot` → one `CommandOutput`; delete `CommandCompletionWaitOutcome`.
- `service/helpers.rs` — drop both poll loops + the wait-outcome match (yield via
  the promise `wait_timeout` + a ~50 ms transcript re-check).
- `command/src/contract.rs` — `CommandTerminalResult = { status, exit_code,
  command_total_time_seconds }`, built from `RunnerOutcome`.
- `operation/src/namespace_execution.rs` — rename `NamespaceExecutionStore` →
  `NamespaceExecutionLedger`; `impl ExecutionObserver`; field `request_id` →
  `origin_request_id`.
- **Rename `CommandWorkspaceOwnership` → `SessionDisposition`** (field
  `session_disposition`; variants `ExistingSession` | `OneShot { handler }` kept).
  It moves into `command/src/exec.rs` with `ExecCommand`; update today's sites —
  `process_store.rs:292` (def, then deleted), `finalize.rs:112-114`,
  `exec_command.rs:166/168/261-262/279/284/332`, `mod.rs:8`, `service.rs:23` — and
  rename the error variant `OneShotWorkspaceCleanupFailed` →
  `OneShotSessionCleanupFailed` (`error.rs:95`).

**Delete.** `service/process_store.rs`, `completion.rs`, `finalize.rs`,
`launch.rs`, `status_lookup.rs`; the write-only `CommandLifecycleState`,
`CancellationState`, and the always-`None` `CommandFinalizedMetadata` /
`CommandPublishFinalization` / `CommandPublishStatus` family. Remove
`spawn_current_exe_ns_runner` from the command path (the file is deleted in
Phase 6).

**Exit criteria.** `cargo test -p sandbox-runtime` (command) + observability
tests green. A running command appears once in `active_namespace_executions`,
`operation_name = "exec_command"`; `origin_request_id` stays distinct from the
execution id; all preserved-behavior items hold.

**Verify.**
```sh
rg -n "CommandProcessStore|FinalizationState|CommandLifecycleState|CommandCompletionWaitOutcome" crates/sandbox-runtime/operation/src/command || echo "gone ✓"
rg -n "execution_kind|runner_kind|active_executions|active_commands" crates/sandbox-runtime/operation/src || echo "axis clean ✓"
```

---

## Phase 4 — Mount family onto the engine

**Objective.** Route overlay/remount through two `engine.run_mount` call sites and
delete the second, duplicate spawn/wait/pipe path.

**Edit.**
- `workspace/src/namespace/setns_runner.rs` — replace `run_child`,
  `wait_for_child`, `terminate_child`, `read_pipe`, and `ns_runner_request` with:
  `engine.run_mount("--mount-overlay", target, id, |_| Ok(())).wait()` and
  `engine.run_mount("--remount-overlay", target, id, |o| Ok(RemountOverlayResult::from_payload(o.payload()))).wait()`.
- `workspace/src/model.rs` — `impl From<WorkspaceEntry> for NamespaceTarget`
  (orphan rule OK: `WorkspaceEntry` is local).
- `daemon/src/runner.rs` — the `MountOverlay` arm writes failure text into
  `RunResult.payload` (so the 2-field `RunResult` carries mount diagnostics; ~3
  lines); rename the `dispatch_runner_mode` parameter for clarity.

**Delete.** `run_child` and its helpers; the duplicate `ns_runner_request`
builder; the `isolated-{mode}-{id}` id format (the id now comes from the engine).

**Exit criteria.** `cargo test -p sandbox-runtime-workspace` green. Overlay mount
and live remount succeed through `engine.run_mount`; the remount verification
report parses; failure surfaces as a terminal error via `payload`.

**Verify.**
```sh
rg -n "fn run_child|fn ns_runner_request" crates/sandbox-runtime/workspace/src || echo "gone ✓"
```

---

## Phase 5 — Remount coordinator onto engine queries

**Objective.** Move quiesce/resume off the per-command `active.remount_*` mirrors
onto engine-registry queries plus coordinator-owned state.

**Edit.** `operation/src/workspace_remount/service/command/{coordinator,quiesce}.rs`
— the coordinator owns one `RemountCancellationToken` + an affected-id set and
asks the registry for live interactive executions in a workspace; embed
`ProcessGroupInspection` into `CommandRemountInspection`.

**Delete.** The per-command `remount_cancellation` and `remount_switch_state`
mirrors; the field-by-field `merge_report`.

**Exit criteria.** Remount quiesce/resume still cancels/holds live commands; a
stale resume does not cancel a command owned by a newer quiesce (the
token-on-coordinator + id-set preserves this); tests green.

---

## Phase 6 — Cleanup + single launcher

**Objective.** Delete all now-dead code, confirm a single daemon-side launcher,
and remove the start-ack handshake atomically.

**Delete.** `command/src/process.rs`; `command/src/pty.rs` (its logic is now in
the engine's `launcher.rs` + `pty.rs`); the result-fd reader thread on any
surviving path.

**Edit (atomic start-ack removal).** `namespace-execution/src/launcher.rs` stops
passing `--start-ack-fd`; `daemon/src/runner.rs` drops `--start-ack-fd`,
`wait_for_start_ack`/`_reader`, and `RunnerCliConfig.start_ack_fd` in the **same**
change (they share the protocol — see the Phase 2 sequencing note).

**Exit criteria.** Full workspace + clippy clean; the absence greps below pass; a
single daemon-side `ns-runner` launcher (the engine) remains.

**Verify (the spec's verification block).**
```sh
cargo fmt --check
cargo test  -p sandbox-runtime-namespace-execution
cargo test  -p sandbox-runtime --tests
cargo clippy --all-targets --no-deps -- -D warnings
rg -n "spawn_current_exe_ns_runner" crates/sandbox-runtime/command/src || echo "command launcher gone ✓"
rg -n "fn run_child" crates/sandbox-runtime/workspace/src/namespace/setns_runner.rs || echo "mount launcher gone ✓"
rg -n "start_ack|--start-ack-fd|wait_for_start_ack" crates/sandbox-runtime crates/sandbox-daemon || echo "start-ack gone ✓"
git diff --check
```

---

## Cross-phase sequencing constraints

- **Start-ack is a Phase 6 atomic cut**, not Phase 2. The in-namespace child and
  the launcher share the `--start-ack-fd` protocol; remove it from both at once,
  only after the last old launch path is gone.
- **`command/src/pty.rs` is deleted in Phase 6**, not Phase 2 — Phase 2 *adds* the
  engine's `pty.rs`; the command crate keeps using its own until Phase 3 routes
  through the engine, after which `command/src/pty.rs` is dead.
- **Id unification lands in Phase 2** (the engine allocates the one id) and is
  *enforced* as the old `cmd_N` (Phase 3) and `isolated-{mode}-{id}` (Phase 4)
  allocators are deleted. `CommandSessionId(id.0)` stays the public face.

---

## Naming decisions (resolved)

- **The interactive entry point is `run_shell_interactive`** (not `run_shell`). It
  returns `InteractiveExecution<T>` because the shell family is PTY-backed and
  needs stdin/stream/cancel; the explicit verb advertises that in the name. The
  deferred batch/pipe shell op (`ShellOp<O>`) will be a separate method returning
  a plain `ExecutionHandle<O>`. `run_mount` stays as-is (pipe-backed, no PTY).
- **`CommandWorkspaceOwnership` → `SessionDisposition`** (field
  `session_disposition`; variants `ExistingSession` | `OneShot { handler }` kept —
  they are already clear; only the "ownership" framing was wrong). It is a
  session-cleanup-responsibility flag — whether `finalize` must destroy a session
  this command created — **not** ownership of a workspace. The rename is folded
  into Phase 3 (its defining/using files are rewritten there); the error variant
  `OneShotWorkspaceCleanupFailed` becomes `OneShotSessionCleanupFailed`.
