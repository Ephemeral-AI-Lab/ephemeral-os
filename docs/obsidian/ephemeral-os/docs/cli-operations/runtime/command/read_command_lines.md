---
title: read_command_lines
tags:
  - ephemeral-os
  - cli
  - runtime
  - command
status: ready
---

# read_command_lines

**Execution space:** `runtime` (sandbox scope) · **Family:** `command`

Read command output by line offset.

## Manual

Read rendered command output for a command session using stable line offsets. Offsets are stable across reads, so a caller can page through a long transcript with `start_offset` + `limit`.

| Argument | Flag | Kind | Required | Default | Description |
|---|---|---|---|---|---|
| `command_session_id` | `--command-session-id` | string | yes | — | Command session id returned by `exec_command`. |
| `start_offset` | `--start-offset` | integer | no | `0` | First transcript line offset. |
| `limit` | `--limit` | integer | no | `200` (max `1000`) | Maximum transcript rows to return. |

**Usage**

```
sandbox-cli runtime read_command_lines --command-session-id ID [--start-offset N] [--limit N]
```

**Examples**

```sh
sandbox-cli runtime read_command_lines --command-session-id cmd-1 --start-offset 0 --limit 100
```

## Expected output

Same command-output shape as [[exec_command]], windowed by the requested offsets:

```json
{
  "status": "running",
  "exit_code": null,
  "wall_time_seconds": 3.4,
  "command_total_time_seconds": 3.4,
  "start_offset": 0,
  "end_offset": 100,
  "total_lines": 412,
  "original_token_count": 1875,
  "output": "line 0\nline 1\n…",
  "command_session_id": "cmd-1"
}
```

`total_lines` is the full transcript length; `start_offset`/`end_offset` bound the returned window; `original_token_count` is the pre-truncation token estimate for the returned slice.

## Related

- [[exec_command]]
- [[write_command_stdin]]
