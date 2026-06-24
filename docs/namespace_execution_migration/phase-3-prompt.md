# Agent Prompt — Author the Phase 3 Spec (Command onto the engine)

## Role & deliverable

You are a senior systems architect. Produce a **rigorous, implementation-ready
specification** for **Phase 3** of the namespace-execution migration — *not* the
implementation. A different agent will build strictly from your spec, so it must
be precise enough to implement without asking you a single follow-up question.

- **Write exactly one file:** `docs/namespace_execution_migration/phase-3-spec.md`.
- **Treat the rest of the tree as read-only.** Do not modify production code,
  tests, or any `Cargo.toml`. Read and run read-only commands (`rg`, `cargo
  check`, `cargo tree`, `ls`) as much as you need to ground every claim.

A spec is sophisticated not because it is long but because it has *already made
the hard decisions* — concurrency, ownership, failure ordering, edit sequencing —
so the implementer makes none. Optimize for that.

## Method (work in this order; writing is the last step)

1. **Investigate to ground truth.** Do not trust the starting map below — verify
   and extend it. Complete the *Investigation mandate*. Every factual claim in
   your spec must be tagged either grounded (`file:line`) or *assumed* (and
   assumptions must be few, listed, and justified).
2. **Reconcile three sources of truth.** The design doc (`namespace-execution.md`)
   states the *end state*; the migration doc (`migration-phases.md`) states the
   *phasing*; the code states *today*. They diverge. For every capability Phase 3
   relies on, classify it as **exists-today**, **Phase-2-must-add**, or
   **Phase-3-adds**. Explicitly surface every contradiction between the three
   (e.g. the design doc describes an engine with "no start-ack", but start-ack
   survives until Phase 6; the design doc's `RunnerOutcome` exposes
   `status()`/`payload()` that the current code does not). Your spec resolves
   each, it does not paper over it.
3. **Design the minimal change.** Fewest types, fields, maps, threads, locks that
   satisfy the requirements. Before introducing any new item, name the existing
   one that should have carried the responsibility and say why it can't.
4. **Stress-test before writing.** Walk the design through the *Hard problems*
   catalog below and every error path. A design that has not survived the hazard
   list is not ready to write down.
5. **Write** per the *Required structure*.
6. **Self-review** against the *Quality bar* and fix every gap before reporting.

## Inputs to study

- `docs/namespace-execution.md` — design of record. Internalize **"Command as the
  subtype"**, **"Command Service Pseudocode"**, **"CommandProcessStore
  Disposition"** (the field-by-field keep/delete table — your deletion
  justifications must agree with it), **"Finalization / Terminal Semantics"**,
  **"Observability Contract"**.
- `docs/namespace_execution_migration/migration-phases.md` — **Phase 3** section,
  **"Invariants held at every phase boundary"**, **"Cross-phase sequencing
  constraints"**, **"Naming decisions (resolved)"**. Binding: your spec refines
  these into an exact plan and never silently contradicts them.

## Investigation mandate (do this first; cite `file:line` for each finding)

- **Enumerate every caller and every test** of each symbol slated for
  deletion/rename — the migration doc's line numbers are stale, so produce the
  *live* set with `rg`: `CommandProcessStore`, `ActiveCommandProcess`,
  `CommandWorkspaceOwnership`, `CommandLifecycleState`, `CancellationState`,
  `FinalizationState`, `CommandCompletionPromise`, `CommandCompletionWaitOutcome`,
  `CommandYield`, `CommandLinesOutput`, `CommandOutputSnapshot`,
  `spawn_current_exe_ns_runner`, `complete_terminal_command_with_services`,
  `CommandLaunchDriver`, `NamespaceExecutionStore`, `OneShotWorkspaceCleanupFailed`.
  A deletion is only safe once you have shown it has no surviving readers.
- **Resolve the real shapes** of types the design only names by reference:
  `WorkspaceSessionService`, `WorkspaceSessionHandler`, `WorkspaceEntry`,
  `CommandStatus`, `CommandConfig`, `OperationTrace`, and the observability
  snapshot types in `operation/src/namespace_execution.rs`.
- **Trace the current thread model end to end**: who spawns the completion
  watcher, the PTY reader, and the finalizer today; what each thread touches;
  which locks are held across which blocking calls. Your target thread model must
  be expressed as a *delta* from this, not invented from the design doc alone.
- **Confirm the engine wiring question:** find where `CommandOperationService` is
  constructed (`from_parts`/`new*`) and how a Phase-3 `Arc<NamespaceExecutionEngine>`
  would be injected and shared. This determines the observer/ledger wiring.

## Consumed Phase 2 API (the contract — Phase 2 does not exist yet)

The crate is a Phase-1 skeleton behind the `test-support` feature; none of this is
implemented. Pin the exact surface Phase 3 consumes so this spec doubles as
Phase 2's acceptance contract, and flag any item whose *current* signature differs:

```rust
NamespaceExecutionEngine::run_shell_interactive<S: ShellOperation>(
    &self, op: S, target: NamespaceTarget, id: NamespaceExecutionId,
) -> Result<InteractiveExecution<S::Output>, NamespaceExecutionError>;
NamespaceExecutionEngine::allocate_id() -> NamespaceExecutionId;
// ExecutionHandle<T>:      id(), is_finished(), wait(self) -> Result<T>, wait_timeout(&self, d) -> Option<&T>
// InteractiveExecution<T>: forwarded handle methods + write_stdin(&[u8]),
//                          read_output_since(u64) -> String, output_len() -> u64, cancel()
// RunnerOutcome:           status() -> NamespaceExecutionTerminalStatus, exit_code() -> i64, payload() -> &Value
// ExecutionObserver:       on_running(&id) AND on_terminal(&id, status, exit_code)
// ExecutionRegistry:       try_reserve (admission) / insert / live-by-id / completed-by-id / complete(id)
```

Phase 2's launcher **still passes `--start-ack-fd`** (removed atomically in
Phase 6); state that Phase 3 does not touch start-ack.

## Current-state starting map (verify and extend — do not treat as complete)

- `operation/src/command/service/core.rs` — `CommandOperationService { workspace,
  config, process_store: Arc<CommandProcessStore>, namespace_execution:
  Arc<NamespaceExecutionStore>, launch_driver, completion_sender,
  remount_controller, workspace_lifecycle_admission }`.
- `service/process_store.rs` (~382) — `CommandProcessStore`; `ActiveCommandProcess`
  (fields incl. `workspace_ownership`, `lifecycle_state`, `cancellation`,
  `remount_cancellation`, `remount_switch_state`, `finalization`);
  `CommandWorkspaceOwnership` (~:292), `CommandLifecycleState` (~:300),
  `CancellationState` (~:308), `CommandTerminalResult` (~:335, has `stdout`),
  `CompletedCommandRecord` (~:360).
- `service/completion.rs` (~242) — `CommandCompletionPromise`,
  `CommandCompletionWaitOutcome`, `spawn_completion_finalizer`,
  `wait_for_completion_yield`, `wait_for_completed_record` (the poll loops).
- `service/finalize.rs` (~276), `service/launch.rs` (~75), `service/status_lookup.rs` (~51).
- `service/contract.rs` — `CommandYield` (~:83), `CommandLinesOutput` (~:97),
  `CommandOutputSnapshot` (~:53), `CommandSessionId`.
- `service/helpers.rs` — `wait_for_command_yield` (~:29).
- `service/impls/{exec_command,write_command_stdin,read_command_lines}.rs`.
- `operation/src/command/error.rs` — `OneShotWorkspaceCleanupFailed` (~:92-99).
- `operation/src/namespace_execution.rs` (~423) — `NamespaceExecutionStore` (~:16),
  record field `request_id`.
- `command/src/contract.rs` — only `CommandError` today.
- `command/src/{pty,process}.rs` — `spawn_current_exe_ns_runner`, `CommandProcess`,
  PTY substrate. **Deleted in Phase 6, not Phase 3.**

## Hard problems the spec MUST resolve (no hand-waving — each gets a decision + rationale)

1. **Result ownership from a registry-retained handle.** `ExecutionHandle::wait(self)`
   consumes the handle, but the registry *keeps* the `CommandExecution`, so the
   command service can never call `wait()`. Specify exactly: how a **running** read
   borrows the in-flight state (`wait_timeout(&self) -> Option<&T>` / transcript
   window), and how a **terminal** read obtains an *owned* `CommandTerminalResult`.
   Define precisely what `registry.complete(id)` stores in the completed slot and
   whether the result is `Clone`d or moved.
2. **The finalize → resolve → complete ordering invariant.** finalize runs *inline*
   on the watcher thread before the promise resolves; therefore
   *promise-resolved ⟹ completed registry entry exists*, which is the property that
   lets the yield path delete `wait_for_completed_record`. State the exact watcher
   step order, which lock is held at each step, and prove the yield path may rely
   on the invariant under concurrent reads.
3. **finalize failure and panic.** finalize error → terminal error delivered via the
   promise; one-shot session destroy failure → `OneShotSessionCleanupFailed`;
   finalize **panic** on the watcher thread → define the promise/poison and
   detached-completion behavior. A command whose caller never waits must still go
   terminal (the watcher owns completion).
4. **Cancel vs. natural exit.** `cancel()` is `killpg` issued from the *caller*
   thread while the watcher blocks in `wait_completion()`. Specify idempotency, the
   terminal-status override (cancel is known engine-side), and confirm no lock is
   held across the blocking wait so the kill is responsive.
5. **Drop semantics.** Dropping the registry-held `CommandExecution` (e.g. on
   service shutdown) must **not** kill a still-running child, and must not leak the
   watcher/PTY-reader threads or the transcript. Specify Drop for the PTY master /
   `InteractiveExecution` / `CommandExecution`, and exactly when child + transcript
   are reaped.
6. **Admission window.** Sequence `try_reserve → spawn → insert`; if spawn fails
   after reserve, the reservation must release (no admission leak). Map this onto
   today's `begin_workspace_lifecycle_admission` and `try_reserve`.
7. **One id space.** `CommandSessionId(id.0)` is the public face of the single
   `namespace_execution_id`; the `cmd_N` allocator is gone. Specify the wrap/unwrap
   sites.
8. **Yield/quiet-period semantics on a condvar.** `wait_for_command_yield` must
   reproduce today's settle-or-timeout UX using `wait_timeout` (condvar, not a 5 ms
   poll) plus a ~50 ms transcript re-check. Specify the loop precisely enough that
   the observable yield behavior is unchanged.

## Required structure of `phase-3-spec.md` (assign every normative requirement a stable id `P3-Rn`)

1. **Objective & non-goals.**
2. **Consumed Phase 2 API** + the exists-today / Phase-2-adds / Phase-3-adds
   classification table, with every divergence from current code flagged.
3. **Target design** — full Rust definitions of `ExecCommand`, `CommandExecution`,
   the trimmed `CommandTerminalResult`, and the merged `CommandOutput`; each item
   justified. Include a **Rejected alternatives** subsection that carries forward
   the design doc's (no `Execution<T>` trait, no `FinalizeCx`, no `Backing`, no
   second per-session map) *and* adds the phase-local ones you weighed.
4. **Thread & ownership model** — a diagram of every thread (API caller, watcher,
   PTY reader), the state each owns, and the global lock order.
5. **Concurrency, failure & lifecycle semantics** — resolve each *Hard problem*
   above with a stated mechanism (not a hope), including every error path.
6. **Sequence diagrams** (text/ASCII) for: exec_command happy path;
   write_command_stdin + yield; Ctrl-C/Ctrl-D cancel; one-shot destroy on finalize;
   read_command_lines on a terminal command.
7. **File-by-file change plan** — Add / Edit / Delete / Rename, each with a
   before→after sketch and a one-line rationale; every deletion paired with the
   evidence (from your investigation) that it has no live readers.
8. **Safe edit order** — the ordered sequence of edits that keeps `cargo build`
   green at every step, and the explicit "last edit before file X is deletable"
   for each deleted file. Address the dead-`command/src/{pty,process}.rs`-until-
   Phase-6 question (how clippy `-D warnings` stays green if they go unreferenced).
9. **Observer wiring** — `NamespaceExecutionStore → NamespaceExecutionLedger`,
   `impl ExecutionObserver`, `request_id → origin_request_id`; demonstrate the
   serialized observability surface is byte-for-byte unchanged (cite the test that
   pins `origin_request_id` distinct from the execution id).
10. **Cross-phase coordination** — `From<WorkspaceEntry> for NamespaceTarget`
    ownership (it must live in `workspace`; Phase 3 consumes it); the Phase 5
    requirement that the registry can return live interactive executions per
    workspace (your `CommandExecution`/registry shape must not preclude it).
11. **Invariants preserved** — a table: invariant → upholding mechanism → guarding
    test (one-shot vs existing session; remount-pending guard; Ctrl-C/Ctrl-D;
    yield/quiet-period; limit `1..=1000`; running-vs-terminal reads; transcript
    content; single `active_namespace_executions` row; no `execution_kind`/`backing`).
12. **Test plan** — which tests keep passing, which move, which are new. Honor the
    repo rule: **no inline tests in production sources**; unit tests live in
    integration suites (this repo recently relocated them — match that).
13. **Verification** — the exact command block (fmt, focused tests, clippy
    `-D warnings`, the migration doc's absence-greps).
14. **Requirements traceability matrix** — `P3-Rn` → design element → test →
    verify command.
15. **Risks & open decisions** — with a recommended resolution for each.
16. **Definition of done & LOC delta.**

## Design constraints the spec must honor (from `CLAUDE.md`)

- **SRP/SOLID;** depend on the engine's narrow API, never its internals; the
  command service holds **no second per-session map**.
- **Prefer less** — Phase 3 is a net deletion; do not invent a field an engine
  type already carries.
- **No re-complication** (migration invariant): no revived `CommandProcessStore`
  source of truth, no `FinalizationState` machine, no daemon poll loops, no public
  `execution_kind`/`backing` axis, no shims/aliases/dual-write.
- **No inline comments in production code;** `///` on public items only. The spec
  may show illustrative code, but the design it prescribes must obey this.

## Quality bar (apply as a self-review gate before reporting; fix every miss)

- Could a competent implementer build this with **zero** clarifying questions? If
  not, name the underspecified section and fix it.
- Is every deletion proven safe (no live readers shown)? Is every new field/type
  justified against an existing one it couldn't reuse?
- Is every hazard in the catalog resolved with a concrete mechanism and an error
  path, not an aspiration?
- Is every claim tagged grounded (`file:line`) or assumed, with assumptions
  minimized and listed in one place?
- Does anything contradict the design or migration docs? If a deviation is
  warranted, is it argued explicitly and flagged for human review?

## Report back

Return: the path written; a 10–15 line section outline; the
exists-today/Phase-2-adds/Phase-3-adds split; your resolution of the result-
ownership and finalize-ordering problems in 3–4 sentences; the top 3 risks/open
decisions; and every contradiction or ambiguity you found across the three
sources. Do not commit or push.
