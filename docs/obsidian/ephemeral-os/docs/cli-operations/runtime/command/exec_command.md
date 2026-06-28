---
title: exec_command
tags:
  - ephemeral-os
  - cli
  - runtime
  - command
status: ready
---

# exec_command

**Execution space:** `runtime` (sandbox scope) · **Family:** `command`

Start a command in a workspace.

## Manual

Start a shell command in a workspace session. With `workspace_session_id`, run inside that existing caller-owned (persistent) session, which the caller created and destroys. Without it, `exec_command` creates a one-shot exec-owned (ephemeral) shared-network workspace and destroys it when the command reaches terminal state. If the command is still running after the initial wait, the response includes a `command_session_id` usable with [[read_command_lines]] or [[write_command_stdin]]; a still-running command stays terminable through `write_command_stdin` (Ctrl-C or Ctrl-D).

| Argument | Flag / Position | Kind | Required | Default | Description |
|---|---|---|---|---|---|
| `workspace_session_id` | `--workspace-session-id` | string | no | one-shot workspace | Existing workspace session id to run inside. Omit to run in a one-shot workspace. |
| `cmd` | `COMMAND` (positional) | string | yes | — | Shell command text. |
| `timeout_ms` | `--timeout-ms` | integer | no | — | Command timeout in milliseconds. |
| `yield_time_ms` | `--yield-time-ms` | integer | no | — | Initial output wait in milliseconds. |

**Usage**

```
sandbox-cli runtime exec_command [--workspace-session-id ID] COMMAND
```

**Examples**

```sh
sandbox-cli runtime exec_command pwd
sandbox-cli runtime exec_command --workspace-session-id ws-1 pwd
sandbox-cli runtime exec_command --workspace-session-id ws-1 --yield-time-ms 0 "sleep 30"
```

## Expected output

Completed command — terminal `status` with the captured output:

```json
{
  "status": "ok",
  "exit_code": 0,
  "wall_time_seconds": 0.012,
  "command_total_time_seconds": 0.012,
  "start_offset": 0,
  "end_offset": 1,
  "total_lines": 1,
  "original_token_count": 1,
  "output": "/testbed\n"
}
```

`status` is one of `running | ok | error | timed_out | cancelled`.

Still running after the initial wait — the payload reports `running` and carries a `command_session_id` for follow-up reads/writes:

```json
{
  "status": "running",
  "exit_code": null,
  "wall_time_seconds": 0.5,
  "command_total_time_seconds": 0.5,
  "start_offset": 0,
  "end_offset": 0,
  "total_lines": 0,
  "original_token_count": 0,
  "output": "",
  "command_session_id": "cmd-1"
}
```

## Related

- [[write_command_stdin]]
- [[read_command_lines]]
