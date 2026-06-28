---
title: trace
tags:
  - ephemeral-os
  - cli
  - observability
status: ready
---

# trace

**Execution space:** `observability` (read-only) · **Family:** `observability`

Render one flow as a span waterfall.

> Resolves to the daemon op `get_observability` with view `trace`; `--sandbox-id` selects the daemon.

## Manual

Fold the log into a span waterfall for one trace: spans nested by parent, offset by start, with attached events inline. Use `--trace-id last` for the most recent root trace.

| Argument | Flag | Kind | Required | Default | Description |
|---|---|---|---|---|---|
| `sandbox_id` | `--sandbox-id` | string | yes | — | Target sandbox id (selects the daemon to query). |
| `trace_id` | `--trace-id` | string | no | `last` | Trace id to render, or `last` for the most recent root trace. |

**Usage**

```
sandbox-cli observability trace --sandbox-id ID [--trace-id TRACE|last]
```

**Examples**

```sh
sandbox-cli observability trace --sandbox-id eos-abc --trace-id req-7f3
sandbox-cli observability trace --sandbox-id eos-abc --trace-id last
```

## Expected output

A folded span forest. Each `spans` entry is a span node (`span` record + `offset_ms` from the trace start) with nested `children` and inline `events`:

```json
{
  "view": "trace",
  "trace": "req-7f3",
  "spans": [
    {
      "span": { "ts": 1751240400000, "trace": "req-7f3", "parent": null, "name": "daemon.dispatch", "dur_ms": 12.4, "status": "ok", "attrs": { "op": "exec_command" } },
      "offset_ms": 0.0,
      "children": [
        {
          "span": { "ts": 1751240400002, "trace": "req-7f3", "parent": "…", "name": "command.exec", "dur_ms": 9.8, "status": "ok", "attrs": {} },
          "offset_ms": 2.0,
          "children": [],
          "events": []
        }
      ],
      "events": [
        { "offset_ms": 2.1, "event": { "ts": 1751240400002, "trace": "req-7f3", "parent": null, "name": "lease.acquired", "attrs": { "layer_id": "L2" } } }
      ]
    }
  ]
}
```

`span.status` is `ok` or `error`. `attrs` carries arbitrary string/number key-values. An unknown or empty trace returns `"spans": []`.

## Related

- [[events]]
- [[snapshot]]
