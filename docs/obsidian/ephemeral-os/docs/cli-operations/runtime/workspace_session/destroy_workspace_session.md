---
title: destroy_workspace_session
tags:
  - ephemeral-os
  - runtime
  - internal
  - workspace_session
status: internal
---

# destroy_workspace_session

**Visibility:** daemon-internal · **Execution space:** `runtime` (sandbox scope)

Internal primitive for destroying a runtime workspace session.

## Manual

This operation is retained for daemon-side composition and recovery. It is not
present in the public runtime CLI or MCP catalog. If command sessions are
still active, the internal operation rejects the request and lists them.

| Argument | Flag | Kind | Required | Default | Description |
|---|---|---|---|---|---|
| `workspace_session_id` | `--workspace-session-id` | string | yes | — | Workspace session id to destroy. |
| `grace_s` | `--grace-s` | float | no | — | Optional process teardown grace period in seconds (must be non-negative). |

There is no public CLI/MCP usage for this operation.

## Expected output

Success — the session is gone:

```json
{
  "workspace_session_id": "ws-1",
  "destroyed": true
}
```

Rejected — commands still active in the session (finish or terminate them first):

```json
{
  "error": {
    "kind": "operation_failed",
    "message": "workspace session has active command sessions",
    "details": { "active_command_session_ids": ["cmd-1", "cmd-2"] }
  }
}
```

## Related

- [[create_workspace_session]]
- [[exec_command]]
