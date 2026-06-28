---
title: create_workspace_session
tags:
  - ephemeral-os
  - cli
  - runtime
  - workspace_session
status: ready
---

# create_workspace_session

**Execution space:** `runtime` (sandbox scope) · **Family:** `workspace_session`

Create a runtime workspace session.

## Manual

Create a user-owned runtime workspace session. When network profile is omitted, the runtime creates a shared-network workspace. The returned `workspace_session_id` is then passed to [[exec_command]] to run commands inside the persistent session, and to [[destroy_workspace_session]] to tear it down.

| Argument | Flag | Kind | Required | Default | Description |
|---|---|---|---|---|---|
| `network_profile` | `--network-profile` | string | no | `shared` | `shared` joins the host network namespace (still isolated in mount/pid/user) or `isolated` uses a dedicated network namespace. |

**Usage**

```
sandbox-cli runtime create_workspace_session [--network-profile PROFILE]
```

**Examples**

```sh
sandbox-cli runtime create_workspace_session
sandbox-cli runtime create_workspace_session --network-profile shared
sandbox-cli runtime create_workspace_session --network-profile isolated
```

## Expected output

Success — the new session id and its resolved network profile:

```json
{
  "workspace_session_id": "ws-1",
  "network_profile": "shared"
}
```

`network_profile` is `shared` or `isolated`.

Error — invalid profile:

```json
{ "error": { "kind": "invalid_argument", "message": "network_profile must be one of shared or isolated", "details": {} } }
```

## Related

- [[destroy_workspace_session]]
- [[exec_command]]
