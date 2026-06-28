---
title: destroy_workspace_session
tags:
  - ephemeral-os
  - cli
  - runtime
  - workspace_session
status: ready
---

# destroy_workspace_session

**Execution space:** `runtime` (sandbox scope) · **Family:** `workspace_session`

Destroy a runtime workspace session.

## Manual

Destroy a user-owned runtime workspace session by `workspace_session_id` when no commands are active in that session. If any command sessions are still active, the operation is rejected and lists them so the caller can finish or terminate them first.

| Argument | Flag | Kind | Required | Default | Description |
|---|---|---|---|---|---|
| `workspace_session_id` | `--workspace-session-id` | string | yes | — | Workspace session id to destroy. |
| `grace_s` | `--grace-s` | float | no | — | Optional process teardown grace period in seconds (must be non-negative). |

**Usage**

```
sandbox-cli runtime destroy_workspace_session --workspace-session-id ID [--grace-s SECONDS]
```

**Examples**

```sh
sandbox-cli runtime destroy_workspace_session --workspace-session-id ws-1
sandbox-cli runtime destroy_workspace_session --workspace-session-id ws-1 --grace-s 2.5
```

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
