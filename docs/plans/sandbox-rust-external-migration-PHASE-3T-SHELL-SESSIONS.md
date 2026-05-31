# Sandbox Rust Migration - Phase 3T Shell Sessions Plan

**Status:** Draft implementation plan.
**Date:** 2026-06-01.
**Parent plan:** `docs/plans/sandbox-rust-external-migration-PLAN.md`.
**Progress tracker:** `docs/plans/sandbox-rust-external-migration-PROGRESS.md`.

This document captures the intermediate shell/session implementation between
Phase 3 and Phase 3.5. The goal is to replace model-facing shell background
mode with a small terminal-session contract that is true to real shell output,
keeps overlay leases correct, and does not introduce shell parsing fallbacks.

## 1. Shell Tools, TTY, and Workspace Semantics

### Runtime Choice

Use native container Bash for the model-facing shell runtime:

```text
/bin/bash --noprofile --norc -c <cmd>
```

There is no `/bin/sh` fallback, no host-shell fallback, and no model-facing raw
argv contract. If `/bin/bash` is missing, shell execution fails as a sandbox
readiness/setup error.

Raw argv can remain only as an internal daemon primitive for trusted hot paths.
The model-facing command input remains a shell-format string.

### Model-Facing Tools

Create these shell tools:

```text
exec_command(cmd, tty, yield_time_ms?, timeout?)
write_stdin_exec_command(shell_session_id, chars, yield_time_ms?)
check_shell_progress(shell_session_id, seconds)
cancel_exec_command(shell_session_id)
```

`exec_command` inputs:

| Field | Meaning |
| --- | --- |
| `cmd` | Shell-format command string, executed at the overlay workspace root. |
| `tty` | `true` for interactive/session commands; `false` for finite blocking commands. |
| `yield_time_ms` | Initial output wait, only meaningful for `tty=true`. |
| `timeout` | Total command/session timeout. |

Do not expose `workdir`, `shell`, `login`, or per-call `max_output_tokens`.
Output limits are global configuration.

All shell tool responses use a minimal shape:

```text
status
output
shell_session_id    # only when tty=true is still running
```

`output` is only real captured stdout/stderr/PTY bytes. Do not synthesize
lifecycle text, command classifications, or guessed messages.

### `tty=false`: Blocking Finite Command

`tty=false` is the finite command path:

1. Spawn `/bin/bash --noprofile --norc -c <cmd>` without a PTY.
2. Block until the top-level Bash process exits or `timeout` fires.
3. When Bash exits, immediately terminate any remaining descendants from the
   command process group/cgroup.
4. Drain real stdout/stderr bytes produced before cleanup.
5. Return `status` and `output`.
6. Never return `shell_session_id`.

This means detached shell patterns do not escape the finite command boundary.
For example:

```bash
nohup python train.py 2>&1 &
```

under `tty=false` exits Bash quickly, then the remaining Python descendant is
terminated immediately. Long-running work must use `tty=true`.

For `tty=false`, capture stdout and stderr as one real combined stream when
possible, so `output` reflects the bytes emitted during execution instead of a
post-hoc concatenation.

### `tty=true`: Terminal Session

`tty=true` is the interactive and long-running session path:

1. Spawn `/bin/bash --noprofile --norc -c <cmd>` with stdin/stdout/stderr
   attached to a PTY slave.
2. Read from the PTY master.
3. If the session is still running after `yield_time_ms`, return
   `status=running`, the real terminal `output`, and `shell_session_id`.
4. Keep the PTY, process group/cgroup, output ring, and workspace resources
   alive until exit, timeout, or cancellation.

For `tty=true`, `output` is the actual terminal display transcript:

- stdout and stderr are naturally merged by the PTY;
- stdin appears only when the terminal/program echoes it;
- hidden input, raw-mode input, and no-echo prompts must not be fabricated;
- lifecycle reminders must not be inserted into `output`.

`check_shell_progress(shell_session_id, seconds)` is PTY-only. It returns the
actual terminal output observed during the last `seconds`. It does not use a
cursor and does not wait for new output.

`write_stdin_exec_command(shell_session_id, chars, yield_time_ms?)` writes bytes
to the PTY, waits up to `yield_time_ms`, and returns only the real PTY output
observed after the write.

`cancel_exec_command(shell_session_id)` terminates the session process
group/cgroup, drains final real output, and releases held resources.

### Ephemeral Workspace Handling

Every shell execution in the default collaborative workspace runs inside the
overlay workspace rooted at `/testbed`.

For `tty=false`:

1. Acquire the LayerStack snapshot lease.
2. Allocate overlay upper/work directories.
3. Mount overlay at the workspace root.
4. Run blocking Bash.
5. On Bash exit, terminate remaining descendants.
6. Capture changes.
7. Publish or discard through OCC.
8. Release the lease and delete runtime directories.

For `tty=true`:

1. Acquire the LayerStack snapshot lease.
2. Allocate overlay upper/work directories.
3. Mount overlay at the workspace root.
4. Start the PTY-backed Bash session.
5. If still running, return `shell_session_id` and keep the lease, overlay
   directories, PTY, process group/cgroup, and output ring alive.
6. On session exit, cancellation, or timeout, capture changes, publish or
   discard through OCC, release the lease, and delete runtime directories.

Overlay is never skipped for shell evidence or implementation. All Phase 3T
performance and correctness evidence must include lease acquisition, overlay
mount, command execution, capture, OCC publish/discard, cleanup, and lease
release.

If a long-running session publishes after the shared workspace has moved, the
OCC result must be reported as metadata/status and via reminders. It must not
overwrite shared workspace state.

### Isolated Workspace Handling

When an agent has entered isolated workspace mode, shell tools run inside that
agent's active isolated workspace handle.

In isolated mode:

- do not create a publishable per-command OCC overlay;
- do not publish shell writes to the shared workspace;
- keep writes inside the isolated private workspace;
- keep the isolated workspace handle alive while `shell_session_id` is active;
- reject `exit_isolated_workspace` while shell sessions are active unless the
  caller explicitly force-cancels them;
- on normal shell exit, keep changes in the isolated workspace until isolated
  exit;
- on isolated exit, discard scratch state and release the pinned snapshot lease.

## 2. Background Tool Deletion, Background Concept Kept

Retire the model-facing generic background shell path:

```text
shell background=true
check_background_task_result
wait_background_tasks
cancel_background_task
```

Keep the internal background/session manager. It remains responsible for
session ownership, cancellation, timeout cleanup, output retention, heartbeat
state, and isolated-workspace lifecycle gates.

The manager tracks typed work:

| Kind | Public identifier | Created by |
| --- | --- | --- |
| `command` | `shell_session_id` | `exec_command(..., tty=true)` when still running after yield. |
| `subagent` | `subagent_session_id` | `run_subagent(...)`. |

`tty=false` shell commands are blocking and never enter the background manager as
model-facing sessions.

## 3. Subagents Update

Keep subagents as background work, but remove their dependency on generic
background task tools.

Create or keep these model-facing subagent tools:

```text
run_subagent(agent_name, prompt)
check_subagent_progress(subagent_session_id, last_n_messages)
cancel_subagent(subagent_session_id)
```

`run_subagent` returns:

```text
status=running
subagent_session_id
```

Subagents should no longer expose generic `bg_N` identifiers to the model. The
internal manager may still use its own private record IDs, but the model-facing
identifier is `subagent_session_id`.

## 4. Notification Update

Replace generic background reminders with typed reminders.

Reminders for shell sessions include:

```text
shell_session_id
status
instruction to use check_shell_progress
instruction to use write_stdin_exec_command
instruction to use cancel_exec_command
```

Reminders for subagents include:

```text
subagent_session_id
status
instruction to use check_subagent_progress
instruction to use cancel_subagent
```

Trigger reminders when:

- a shell session or subagent starts;
- a PTY session appears to be waiting for input;
- a shell session or subagent exits, fails, times out, or is cancelled;
- the agent tries to finish while shell sessions or subagents remain active.

Prompt/input detection is observational only. It may use PTY output, idle time,
and live process state, but it must not classify command strings as policy.

Reminder text and lifecycle metadata are separate from shell tool `output`.
Shell `output` remains only the real stdout/stderr/PTY transcript.

## 5. Verification Targets

Phase 3T should close with overlay-inclusive evidence for:

- `tty=false` blocking command success;
- `tty=false` timeout cleanup;
- `tty=false` `nohup ... &` descendant termination on Bash exit;
- `tty=true` short command exit with no `shell_session_id`;
- `tty=true` long-running command returning `shell_session_id`;
- `tty=true` PTY input via `write_stdin_exec_command`;
- `check_shell_progress(shell_session_id, seconds)` returning only recent real
  terminal output;
- `cancel_exec_command(shell_session_id)` killing the full process group/cgroup;
- ephemeral overlay lease retention until PTY session terminal state;
- isolated workspace exit rejection while shell sessions are active;
- isolated workspace force-cancel cleanup;
- typed subagent launch/progress/cancel;
- typed reminders for active shell sessions and subagents.

Performance evidence must include 1, 3, 5, and 10 concurrent shell/session
cases where applicable, and every accepted sample must run through the real
overlay path.
