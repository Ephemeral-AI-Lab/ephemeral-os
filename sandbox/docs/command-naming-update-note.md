# Command Naming Update Note

This note captures the proposed cleanup after renaming the crate from
`eos-command-session` to `eos-command` and the wire identifier from
`command_session_id` to `command_id`.

## Naming Update Table

| Current name | Location / scope | Role today | Proposed name | Reason |
|---|---|---|---|---|
| `session.rs` | `eos-command/src/session.rs` | High-level running command aggregate | `process.rs` | The type owns one command process lifecycle; `session` no longer adds precision. |
| `Session` | `eos_command::session::Session` | Command identity, caller, timeout, transcript/final paths, kill/exit state | `CommandProcess` | Public aggregate should describe the command process, not an abstract session. |
| `SessionSpec` | `eos_command::session::SessionSpec` | Constructor input for a command lifecycle object | `CommandProcessSpec` | Keep the constructor DTO aligned with `CommandProcess`. |
| `RunningSessionParts` | `eos_command::session::RunningSessionParts` | Spawned child plus output/final/transcript paths | `RunningCommandProcessParts` | Describes the materialized command process state. |
| `ProcessRuntime` | private in `session.rs` | Private child/path/kill/reap state | `CommandProcessRuntime` | Keeps private state aligned with the public aggregate. |
| `Session::reap()` | `eos_command::session::Session` | One-shot extraction of a completed command process exit | `CommandProcess::take_exit()` | Uses one clear verb; `take` communicates that the exit result is consumed once. |
| `ReapedCommand` | `eos_command::session` | Raw process/PTY result before workspace policy | `CommandProcessExit` | Names the value, not the mechanism that discovered it. |
| `CommandSessionProcess` | `eos_command::process::CommandSessionProcess` | Low-level PTY child handle, stdin writer, reader completion, exit detection | private `PtyProcess` | This is the PTY implementation detail, not the command lifecycle aggregate. |
| `ProcessReap` | `eos_command::process` | Low-level PTY process exit poll result | private `PtyProcessExit` | Keeps PTY-specific exit state private and removes reaper vocabulary. |
| `process.rs` | `eos-command/src/process.rs` | Low-level PTY/process implementation | `pty.rs` | Frees `process.rs` for the public `CommandProcess` aggregate. |
| `CommandSessionError` | `eos_command::CommandSessionError` | Command substrate error | `CommandError` | Removes stale session vocabulary from public error names. |
| `StartCommandSession` | `eos_command::StartCommandSession` | Start command request DTO | `StartCommand` | Operation starts a command, not a session. |
| `CancelCommandSession` | `eos_command::CancelCommandSession` | Cancel command request DTO | `CancelCommand` | Operation cancels a command by `command_id`. |
| `CommandSessionWaitTarget` | `eos_command::yield_wait_loop` | Wait abstraction for yield/read/finalize loop | `CommandWaitTarget` | Wait loop is command-oriented and should not expose session vocabulary. |
| `CommandSessionCompletion` | `eos_operation::command::contract` | Completed command notification DTO | `CommandCompletion` | Completion payload now keys by `command_id`. |
| `CommandSessionCountOutput` | `eos_operation::command::contract` | Live command count DTO | `CommandCountOutput` | Count operation reports live commands. |
| `EphemeralRun.session` | `eos_operation::command::registry` | Active command process in ephemeral workspace | `process` | Field should match `CommandProcess`. |
| `IsolatedRun.session` | `eos_operation::command::registry` | Active command process in isolated workspace | `process` | Field should match `CommandProcess`. |
| `ActiveCommand::session()` | `eos_operation::command::registry` | Accessor for active command process | `process()` | Accessor should expose a process aggregate. |
| `CommandRegistry::caller_sessions` | `eos_operation::command::registry` | Active commands for a caller | `caller_processes` or `caller_commands` | Avoids stale session vocabulary; `caller_commands` is simpler at registry boundary. |
| `CommandOps::spawn_session` | `eos_operation::command::service` | Spawns the command lifecycle object | `spawn_process` | Returns `CommandProcess`. |
| `CommandOps::register_and_wait(session, ...)` | `eos_operation::command::service` | Registers process and waits for yield/completion | `register_and_wait(process, ...)` | Parameter and local names should match the aggregate. |
| `CommandOps::finish_reaped` | `eos_operation::command::service` | Converts a completed process exit into a final command response | `finalize_command` | This is the command/workspace finalization boundary. |
| `settle_ephemeral()` | `eos_operation::command::settle` | Captures and publishes ephemeral command effects | `finalize_ephemeral_command()` | Uses finalization vocabulary and states the workspace mode. |
| `settle_isolated()` | `eos_operation::command::settle` | Captures isolated command effects without publishing | `finalize_isolated_command()` | Uses finalization vocabulary and states the workspace mode. |
| `command_session_config()` | `eos_operation::command::runtime` | Runtime command config accessor | `command_config()` | Return type is `CommandConfig`; function should match. |
| `command_session_scratch_root()` | `eos_operation::command::runtime` | Scratch root accessor | `command_scratch_root()` | Scratch root belongs to commands. |
| `configure_command_sessions()` | `eos_operation::command::runtime` | Injects command runtime config | `configure_commands()` | Configures command runtime behavior. |
| `active_command_sessions_for_caller()` | `eos_operation::command::runtime` | Live command count helper | `active_commands_for_caller()` | Reports commands, not sessions. |
| `cleanup_command_sessions_for_caller()` | `eos_operation::command::runtime` | Caller cleanup helper | `cleanup_commands_for_caller()` | Cancels/drains commands for one caller. |
| `cancel_all_command_sessions()` | `eos_operation::command::runtime` | Global cancel helper | `cancel_all_commands()` | Operation scope is all active commands. |
| `command_session_reaper_sweep()` | `eos_operation::command::runtime` | Background helper that advances timed-out or exited commands | `advance_active_commands_once()` | Removes reaper/sweep vocabulary and describes one daemon tick. |
| `recover_orphaned_command_sessions()` | `eos_operation::command::runtime` | Crash recovery helper | `recover_orphaned_commands()` | Orphans are command processes on disk. |
| `sweepers` module | `eos-daemon/src/runtime/services.rs` | Periodic daemon background helpers | `background_tasks` | The module hosts daemon-owned periodic work, not only sweeps. |
| `sweep_command_sessions()` | `eos-daemon/src/runtime/services.rs` | Daemon hook for command timeout/exit advancement | `advance_active_commands_once()` | Match the operation helper it calls. |
| `sweep_workspace_ttl()` | `eos-daemon/src/runtime/services.rs` | Daemon hook for idle workspace TTL cleanup | `evict_idle_workspaces_once()` | Names the actual outcome and one background tick. |
| `WorkspaceRuntime::ttl_sweep()` | `eos-daemon/src/runtime/workspace.rs` | Evicts idle isolated workspaces past TTL | `evict_idle_workspaces()` | Avoids generic sweep wording and keeps TTL as policy detail. |
| `command_session_count` op label | daemon/control surface | Counts live commands | Keep wire op stable unless explicitly breaking API | Internal Rust naming can become `command_count`; wire compatibility is separate. |
| `command_sessions` YAML key | `sandbox/config/prd.yml` | Command runtime config section | Optional future `commands` | This is a breaking config rename; defer unless config compatibility cleanup is intended. |
| `max_session_s` | `CommandConfig` field | Maximum command runtime duration | `max_command_s` | The limit applies to a command process. |

## Lifecycle Vocabulary

Use process, command, and workspace terms instead of reaper/sweep/settle terms:

| Old wording | Replacement | Meaning |
|---|---|---|
| reap | take exit | Consume a completed process exit exactly once. |
| reaper settle | background command finalization | A daemon background tick finalized a command because no foreground caller did. |
| command session sweep | active command advancement | Scan active commands and advance any timed-out or exited command. |
| settle | finalize | Produce the final command response, persist artifacts, release leases, and unregister the command. |
| TTL sweep | idle workspace eviction | Remove idle isolated workspaces after TTL while protecting callers with active commands. |

Preferred command lifecycle:

```text
CommandProcess::take_exit()
  -> CommandOps::finalize_command()
  -> CommandCompletion / collect_completed

background:
  advance_active_commands_once()
    -> mark overdue command timed out
    -> take_exit
    -> finalize_command
```

## Recommended Shape

```text
eos-command/src/
  contract.rs        # CommandError, StartCommand, CancelCommand, command DTOs
  process.rs         # public CommandProcess aggregate
  pty.rs             # private PtyProcess implementation
  transcript.rs
  yield_wait_loop.rs # CommandWaitTarget
```

## Scope Boundary

Keep these names stable unless the API contract is intentionally broken:

| Surface | Recommendation |
|---|---|
| Wire operation family `sandbox.command.*` | Keep. It already uses command vocabulary. |
| Wire field `command_id` / `command_ids` | Keep. This is the desired external shape. |
| Test suite names containing `command_session` | Rename only if the suite taxonomy is also being cleaned up. |
| User-facing phrase "command session" in historical docs | Update active docs; leave historical plans unless regenerating docs broadly. |
