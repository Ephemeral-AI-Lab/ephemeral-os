---
title: create_workspace_session
tags:
  - ephemeral-os
  - runtime
  - internal
  - workspace_session
status: internal
---

# create_workspace_session

**Visibility:** daemon-internal · **Execution space:** `runtime` (sandbox scope)

Internal primitive for creating a runtime workspace session.

## Manual

This operation is retained for daemon-side composition and internal tests. It
is not present in the public runtime CLI or MCP catalog. Public
`exec_command` calls without a workspace id create and finalize their own
internal session automatically.

| Argument | Flag | Kind | Required | Default | Description |
|---|---|---|---|---|---|
| `network_profile` | `--network-profile` | string | no | `shared` | `shared` joins the host network namespace (still isolated in mount/pid/user) or `isolated` uses a dedicated network namespace. |

There is no public CLI/MCP usage for this operation.

## Expected output

Success — the new session id and its resolved network profile:

```json
{
  "workspace_session_id": "ws-1",
  "network_profile": "shared",
  "finalize_policy": "no_op"
}
```

`network_profile` is `shared` or `isolated`; explicit internal sessions use
`finalize_policy: "no_op"`.

Error — invalid profile:

```json
{ "error": { "kind": "invalid_argument", "message": "network_profile must be one of shared or isolated", "details": {} } }
```

## Related

- [[destroy_workspace_session]]
- [[exec_command]]
