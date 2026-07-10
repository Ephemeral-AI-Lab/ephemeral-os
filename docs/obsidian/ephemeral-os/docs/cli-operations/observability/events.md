---
title: events
tags:
  - ephemeral-os
  - cli
  - observability
status: ready
---

# events

**Execution space:** `observability` (read-only) · **Family:** `observability`

List domain-fact events across traces.

> The direct `events` catalog operation is sandbox-scoped and routed to the
> observability application in the daemon selected by `--sandbox-id`.

## Manual

Fold the log into a flat, cross-trace stream of point-in-time events (lease, errors, …), newest first. Filter by exact name and/or a start timestamp, and cap to the newest N with `--last-n`.

| Argument | Flag | Kind | Required | Default | Description |
|---|---|---|---|---|---|
| `sandbox_id` | `--sandbox-id` | string | yes | — | Target sandbox id (selects the daemon to query). |
| `name` | `--name` | string | no | — | Filter to events with this exact name (e.g. `lease.acquired`). |
| `since_ms` | `--since-ms` | integer | no | — | Only events at or after this unix-ms timestamp. |
| `last_n` | `--last-n` | integer | no | — | Keep only the N newest matched events. |

**Usage**

```
sandbox-observability-cli events --sandbox-id ID [--name NAME] [--since-ms MS] [--last-n N]
```

**Examples**

```sh
sandbox-observability-cli events --sandbox-id eos-abc
sandbox-observability-cli events --sandbox-id eos-abc --name lease.acquired
sandbox-observability-cli events --sandbox-id eos-abc --last-n 20
```

## Expected output

A flat list of event records (newest first):

```json
{
  "view": "events",
  "events": [
    { "ts": 1751240400002, "trace": "req-7f3", "parent": null, "name": "lease.acquired", "attrs": { "layer_id": "L2" } },
    { "ts": 1751240399500, "trace": "req-7f1", "parent": null, "name": "lease.released", "attrs": { "layer_id": "L1" } }
  ]
}
```

`attrs` carries arbitrary string/number key-values per event. No matches returns `"events": []`.

## Related

- [[trace]]
