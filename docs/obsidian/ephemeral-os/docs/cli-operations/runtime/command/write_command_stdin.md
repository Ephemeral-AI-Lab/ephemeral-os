---
title: write_command_stdin
tags:
  - ephemeral-os
  - cli
  - runtime
  - command
status: ready
---

# write_command_stdin

**Execution space:** `runtime` (sandbox scope) · **Family:** `command`

Write text to a running command stdin.

## Manual

Append text to the stdin stream of a running command session and return a bounded output yield. Use the `command_session_id` returned by [[exec_command]]. Control characters work too — e.g. Ctrl-C / Ctrl-D to signal or close stdin.

| Argument | Flag / Position | Kind | Required | Default | Description |
|---|---|---|---|---|---|
| `command_session_id` | `--command-session-id` | string | yes | — | Command session id returned by `exec_command`. |
| `stdin` | `TEXT` (positional) | string | yes | — | Text to write to stdin. |
| `yield_time_ms` | `--yield-time-ms` | integer | no | — | Output wait after writing stdin. |

**Usage**

```
sandbox-cli runtime write_command_stdin --command-session-id ID TEXT
```

**Examples**

```sh
sandbox-cli runtime write_command_stdin --command-session-id cmd-1 hello
```

## Expected output

Same shape as [[exec_command]] — a command-output yield for the session after the write:

```json
{
  "status": "running",
  "exit_code": null,
  "wall_time_seconds": 1.2,
  "command_total_time_seconds": 1.2,
  "start_offset": 0,
  "end_offset": 2,
  "total_lines": 2,
  "original_token_count": 2,
  "output": "hello\nready>\n",
  "command_session_id": "cmd-1"
}
```

Once the command reaches a terminal state the payload reports `status` `ok | error | timed_out | cancelled` and omits `command_session_id`.

Error — unknown/closed session:

```json
{ "error": { "kind": "operation_failed", "message": "command session not found: cmd-1", "details": { "command_session_id": "cmd-1" } } }
```

## Related

- [[exec_command]]
- [[read_command_lines]]
