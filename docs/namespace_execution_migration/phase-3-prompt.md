# Agent Prompt — Author the Phase 3 Spec (Command onto the engine)

You are a software architect. Your job is to **write a complete, standalone,
implementation-ready specification** for **Phase 3** of the namespace-execution
migration — *not* to implement it. A separate implementation agent will follow
your spec. Read this whole prompt before starting.

**Deliverable:** a single markdown document at
`docs/namespace_execution_migration/phase-3-spec.md`. The **only** thing you may
write is that file. **Do not modify any production code, tests, or `Cargo.toml`.**
You may read and run read-only commands (`rg`, `cargo check`, `ls`) freely to
ground the spec; treat the source tree as read-only.

## Inputs to study (read these first)

- `docs/namespace-execution.md` — the design of record. Internalize **"Command as
  the subtype"**, **"Command Service Pseudocode"**, **"CommandProcessStore
  Disposition"** (the field-by-field keep/delete table), **"Finalization /
  Terminal Semantics"**, **"Observability Contract"**.
- `docs/namespace_execution_migration/migration-phases.md` — the **Phase 3**
  section (Add / Edit / Delete / Rename, exit criteria, verify block), plus
  **"Invariants held at every phase boundary"** and **"Naming decisions
  (resolved)"**. These are binding; your spec refines them into an exact plan, it
  does not contradict them.
- The actual code (see the current-state map below — **verify and extend it
  yourself**; line numbers drift and other agents may be editing concurrently).

## Prerequisite the spec must declare — the Phase 2 engine API

Phase 3 builds on the engine from Phase 2, which **does not exist yet** (the crate
is currently a Phase-1 skeleton behind the `test-support` feature). Your spec must
open with an explicit **"Consumed Phase 2 API"** section pinning the exact surface
Phase 3 depends on, so it doubles as Phase 2's acceptance contract:

```rust
NamespaceExecutionEngine::run_shell_interactive<S: ShellOperation>(
    &self, op: S, target: NamespaceTarget, id: NamespaceExecutionId,
) -> Result<InteractiveExecution<S::Output>, NamespaceExecutionError>;
NamespaceExecutionEngine::allocate_id() -> NamespaceExecutionId;
// ExecutionHandle<T>:        id(), is_finished(), wait(), wait_timeout(d) -> Option<&T>
// InteractiveExecution<T>:   forwarded handle methods + write_stdin(&[u8]),
//                            read_output_since(u64) -> String, output_len() -> u64, cancel()
// RunnerOutcome:             status() -> NamespaceExecutionTerminalStatus, exit_code() -> i64, payload() -> &Value
// ExecutionObserver:         on_running(&id) AND on_terminal(&id, status, exit_code)
// ExecutionRegistry:         insert / live-by-id / completed-by-id / complete(id) / try_reserve (admission)
```

Note that Phase 2's launcher **still passes `--start-ack-fd`** (removed atomically
in Phase 6); the spec must state Phase 3 leaves start-ack untouched.

## Current-state map to spec against (verify; go deeper as needed)

- `operation/src/command/service/core.rs` — `CommandOperationService` holds
  `workspace, config, process_store: Arc<CommandProcessStore>,
  namespace_execution: Arc<NamespaceExecutionStore>, launch_driver,
  completion_sender, remount_controller, workspace_lifecycle_admission`.
- `operation/src/command/service/process_store.rs` (~382 LOC) —
  `CommandProcessStore`; `ActiveCommandProcess` (incl. `workspace_ownership:
  CommandWorkspaceOwnership`, `lifecycle_state: CommandLifecycleState`,
  `cancellation: CancellationState`, `remount_cancellation`,
  `remount_switch_state`, `finalization: FinalizationState`);
  `CommandWorkspaceOwnership` (~:292); `CommandLifecycleState` (~:300);
  `CancellationState` (~:308); `CommandTerminalResult` (~:335, has `stdout`);
  `CompletedCommandRecord` (~:360).
- `operation/src/command/service/completion.rs` (~242) — `CommandCompletionPromise`,
  `CommandCompletionWaitOutcome`, `spawn_completion_finalizer`,
  `wait_for_completion_yield`, `wait_for_completed_record` (the poll loops).
- `operation/src/command/service/finalize.rs` (~276) — `complete_terminal_command_with_services`.
- `operation/src/command/service/launch.rs` (~75) — `CommandLaunchDriver`.
- `operation/src/command/service/status_lookup.rs` (~51).
- `operation/src/command/service/contract.rs` — `CommandYield` (~:83),
  `CommandLinesOutput` (~:97), `CommandOutputSnapshot` (~:53), `CommandSessionId`.
- `operation/src/command/service/helpers.rs` — `wait_for_command_yield` (~:29).
- `operation/src/command/service/impls/{exec_command,write_command_stdin,read_command_lines}.rs`.
- `operation/src/command/error.rs` — `OneShotWorkspaceCleanupFailed` (~:92-99).
- `operation/src/namespace_execution.rs` (~423) — `NamespaceExecutionStore` (~:16),
  record field `request_id`.
- `command/src/contract.rs` — currently only `CommandError`.
- `command/src/pty.rs` / `command/src/process.rs` — `spawn_current_exe_ns_runner`,
  `CommandProcess`, the PTY substrate. **Deleted in Phase 6, not Phase 3** — the
  spec must address how the command path stops calling them in Phase 3 while the
  files survive (and how clippy `-D warnings` stays green if they go unreferenced).

## Required structure of the spec you write

Make `phase-3-spec.md` a self-contained document an implementer can execute
without re-deriving anything. Include, at minimum:

1. **Objective & scope** — one paragraph; what Phase 3 does and explicitly does
   not do.
2. **Consumed Phase 2 API** — the surface above, pinned exactly.
3. **Target design** — full definitions of the new/changed types with rationale:
   - `ExecCommand: ShellOperation` (`command/src/exec.rs`): fields (incl. its own
     `WorkspaceSessionService` handle + `SessionDisposition`), the `finalize` body
     in prose (destroys the one-shot via its own handle — **no `FinalizeCx`**),
     `operation_name() == "exec_command"`.
   - `CommandExecution` (`command/src/command_execution.rs`):
     `InteractiveExecution<CommandTerminalResult>` + transcript cursor +
     `session_disposition`; show it is the **registry value**, not a second store.
   - `CommandTerminalResult` trimmed to `{ status, exit_code,
     command_total_time_seconds }`; the merged `CommandOutput` DTO.
4. **File-by-file change plan** — a table/section covering every Add / Edit /
   Delete / Rename from the migration doc, each with: the file, what changes,
   a before→after sketch, and why. Cover the deletes (`process_store`,
   `completion`, `finalize`, `launch`, `status_lookup`) and the write-only-state
   deletions (`CommandLifecycleState`, `CancellationState`, the `CommandFinalized*`
   publish family) with a one-line justification each (the disposition table
   marks them write-only). Specify the `CommandWorkspaceOwnership → SessionDisposition`
   rename and the `OneShotWorkspaceCleanupFailed → OneShotSessionCleanupFailed`
   rename with their exact sites (re-grep; don't trust stale line numbers).
5. **Observer wiring** — `NamespaceExecutionStore → NamespaceExecutionLedger`,
   `impl ExecutionObserver`, `request_id → origin_request_id`; assert the
   serialized observability surface is byte-for-byte unchanged.
6. **Cross-phase coordination** — `From<WorkspaceEntry> for NamespaceTarget` is
   owned by Phase 4 (orphan rule → it lives in `workspace/src/model.rs`) but
   `exec_command` needs it; specify the agreed ownership so 3 and 4 don't collide.
   Note Phase 5 will query the registry for live executions per workspace — the
   `CommandExecution`/registry shape must not preclude that.
7. **Invariants preserved** — list each (one-shot vs existing session;
   remount-pending guard; Ctrl-C/Ctrl-D kill; yield/quiet-period; limit `1..=1000`;
   running-vs-terminal reads; transcript content; single
   `active_namespace_executions` row, `operation_name="exec_command"`, no
   `execution_kind`/`backing`; `origin_request_id` distinct) **and how the new
   design upholds it.**
8. **Test plan** — which existing tests must keep passing, which move, which are
   new (engine-registry-backed exec/write/read; one-shot destroyed in `finalize`;
   observability shape). Honor the repo rule: **no inline tests in production
   sources**; unit tests live in integration suites.
9. **Verification** — the exact commands a reviewer runs (fmt, focused tests,
   clippy `-D warnings`, the absence-greps from the migration doc).
10. **Risks & open decisions** — call out anything genuinely ambiguous (e.g. the
    dead-`pty.rs`/`process.rs`-until-Phase-6 handling) and recommend a resolution.
11. **Definition of done & LOC estimate** for the phase.

## Design constraints the spec must honor (from `CLAUDE.md`)

- SRP/SOLID; depend on the engine's narrow API, never its internals. The command
  service holds **no second per-session map**.
- Prefer less — Phase 3 is a net deletion; the spec should not invent a field a
  engine type already carries. No re-complication (no revived `CommandProcessStore`
  source of truth, no `FinalizationState`, no poll loops, no public
  `execution_kind`/`backing`).
- No inline comments in production code; `///` on public items only.

## Report back

When done, give me: the path you wrote, a 5–10 line outline of the spec's
sections, the top 3 risks/open decisions you surfaced, and any place the existing
docs were ambiguous or internally inconsistent. Do not commit or push.
