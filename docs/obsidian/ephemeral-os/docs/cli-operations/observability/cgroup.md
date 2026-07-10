---
title: cgroup
tags:
  - ephemeral-os
  - cli
  - observability
status: ready
---

# cgroup

**Execution space:** `observability` (read-only) · **Family:** `observability`

Resource series for a scope (cpu/mem/io + disk).

> The direct `cgroup` catalog operation is sandbox-scoped and routed to the
> observability application in the daemon selected by `--sandbox-id`.

## Manual

Fold the sample log for one scope into a time series with deltas: cgroup counters (cpu/mem/io from `/sys/fs/cgroup`) plus the disk sample (upperdir bytes/files) carried in the same record.

| Argument | Flag | Kind | Required | Default | Description |
|---|---|---|---|---|---|
| `sandbox_id` | `--sandbox-id` | string | yes | — | Target sandbox id (selects the daemon to query). |
| `scope` | `--scope` | string | no | `sandbox` | Resource scope: `sandbox` or a workspace id. |
| `window_ms` | `--window-ms` | integer | no | `60000` | Lookback window in milliseconds (max `600000`). |

**Usage**

```
sandbox-observability-cli cgroup --sandbox-id ID [--scope SCOPE] [--window-ms MS]
```

**Examples**

```sh
sandbox-observability-cli cgroup --sandbox-id eos-abc
sandbox-observability-cli cgroup --sandbox-id eos-abc --scope ws-1 --window-ms 60000
```

## Expected output

A time series of samples within the window; `metrics` are raw readings, `deltas` are read-time differences for monotonic counters (e.g. `cpu_usec`):

```json
{
  "view": "cgroup",
  "scope": "sandbox",
  "series": [
    { "ts": 1751240399000, "sample_delta_ms": 1000, "metrics": { "cpu_usec": 1170000, "mem_cur": 10330112, "mem_max": 268435456 }, "deltas": { "cpu_usec": 28000 } },
    { "ts": 1751240400000, "sample_delta_ms": 1000, "metrics": { "cpu_usec": 1200000, "mem_cur": 10485760, "mem_max": 268435456 }, "deltas": { "cpu_usec": 30000 } }
  ]
}
```

For a workspace scope the records also include `disk_bytes` and `files`. `window_ms` past the `600000` ceiling is rejected with `invalid_request`.

## Related

- [[snapshot]]
