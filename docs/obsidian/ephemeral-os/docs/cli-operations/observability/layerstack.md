---
title: layerstack
tags:
  - ephemeral-os
  - cli
  - observability
status: ready
---

# layerstack

**Execution space:** `observability` (read-only) · **Family:** `observability`

Per-layer leasing/booking inventory, and stack series.

> Resolves to the daemon op `get_observability` with view `layerstack`; `--sandbox-id` selects the daemon.

## Manual

Show the active manifest as a per-layer inventory: disk bytes, how many workspaces lease each layer, and which leased layers book each base. Served live from the runtime; does not read the log. With `--workspace-id`, show one workspace's lower layers and private upperdir instead.

| Argument | Flag | Kind | Required | Default | Description |
|---|---|---|---|---|---|
| `sandbox_id` | `--sandbox-id` | string | yes | — | Target sandbox id (selects the daemon to query). |
| `workspace_id` | `--workspace-id` | string | no | — | Show one workspace's lower layers and private upperdir. |
| `window_ms` | `--window-ms` | integer | no | `60000` | Lookback window in milliseconds for the stack trend (max `600000`). |

**Usage**

```
sandbox-observability-cli layerstack --sandbox-id ID [--workspace-id WS] [--window-ms MS]
```

**Examples**

```sh
sandbox-observability-cli layerstack --sandbox-id eos-abc
sandbox-observability-cli layerstack --sandbox-id eos-abc --workspace-id ws-7
```

## Expected output

Stack-wide inventory (`booked_by` is the leased layers above each base that pull it in). A `trend` array of stack samples is appended when `--window-ms` is set:

```json
{
  "view": "layerstack",
  "manifest_version": 1,
  "root_hash": "sha256:…",
  "active_lease_count": 2,
  "total_bytes": 1048576,
  "layers": [
    { "layer_id": "L0", "bytes": 524288, "leased_by_workspaces": 2, "booked_by": ["L1"] },
    { "layer_id": "L1", "bytes": 524288, "leased_by_workspaces": 1, "booked_by": [] }
  ],
  "trend": [
    { "ts": 1751240400000, "layer_count": 2, "layers_bytes": 1048576, "active_leases": 2 }
  ]
}
```

Per-workspace view (`--workspace-id`): the layers that session mounts (base → newest), who else mounts each, and the session's private upper bytes:

```json
{
  "view": "layerstack",
  "workspace": "ws-7",
  "mounts": [
    { "layer_id": "L0", "shared_with": ["ws-2"] },
    { "layer_id": "L1", "shared_with": [] }
  ],
  "upper_bytes": 4096
}
```

An unknown `--workspace-id` returns `invalid_request`.

## Related

- [[snapshot]]
- [[cgroup]]
