---
title: create_sandbox
tags:
  - ephemeral-os
  - cli
  - manager
  - management
status: ready
---

# create_sandbox

**Execution space:** `manager` (system scope) · **Family:** `management`

Create a host-side sandbox record and runtime sandbox.

## Manual

Create a host-side sandbox record, create the runtime sandbox, and start its daemon. The manager creates the runtime sandbox first, records it, then provisions and starts the in-sandbox `sandbox-daemon`; any failure rolls back the daemon, runtime sandbox, and record.

| Argument | Flag | Kind | Required | Default | Description |
|---|---|---|---|---|---|
| `image` | `--image` | string | yes | — | Container image used to create the sandbox. |
| `workspace_root` | `--workspace-bind-root` | path | yes | — | Absolute host workspace directory bind-mounted into this sandbox. |
| `count` | `--count` | integer | no | `1` | Number of sandboxes to create (minimum 1). Values greater than 1 share a read-only workspace base. |

**Usage**

```
sandbox-manager-cli create_sandbox --image IMAGE --workspace-bind-root PATH [--count N]
```

**Examples**

```sh
sandbox-manager-cli create_sandbox --image ubuntu:24.04 --workspace-bind-root /testbed
sandbox-manager-cli create_sandbox --image ubuntu:24.04 --workspace-bind-root /testbed --count 5
```

## Expected output

Success with the default `--count 1` — the new sandbox record (`state` is
`ready` once the daemon is up):

```json
{
  "id": "eos-abc",
  "workspace_root": "/testbed",
  "state": "ready",
  "daemon": { "host": "127.0.0.1", "port": 53124 }
}
```

`id` is assigned by the runtime provider. `state` is one of `creating | ready | stopping | stopped | failed`.
For `--count N` where `N > 1`, the response is `{ "sandboxes": [...] }` with
one ready record per created sandbox; a partial batch is rolled back on error.

Error — invalid/empty image (record and runtime sandbox are rolled back):

```json
{ "error": { "kind": "invalid_request", "message": "invalid image: ", "details": {} } }
```

## Related

- [[list_sandboxes]]
- [[inspect_sandbox]]
- [[destroy_sandbox]]
