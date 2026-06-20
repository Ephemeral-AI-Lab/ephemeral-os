# Connecting to eos-sandbox

External callers talk to the host-side gateway over one Unix socket. The
gateway owns visibility checks and routes host operations to the host engine or
daemon command/session operations to the selected sandbox daemon.

The current wire contract is documented in
`crates/protocol/PROTOCOL.md`; the boundary contract and version pins
are documented in `CONTRACT.md`.

## Topology

```
caller
  -> gateway Unix socket
  -> host engine
  -> sandbox daemon over loopback TCP with host-stamped auth metadata
```

The caller never talks to the per-sandbox daemon directly.

## Request Shape

Each request is a compact JSON object followed by `\n`:

```json
{"op":"sandbox.command.exec","sandbox_id":"sb-...","invocation_id":"00000000000000000000000000000001","args":{"command":"pwd"}}
```

`op`, `invocation_id`, and `args` are required. `sandbox_id` is required for
daemon-bound operations and for host operations that target an existing sandbox.

## Useful Commands

```sh
bin/ephai-sandbox-gateway host serve

SID=$(bin/ephai-sandbox-gateway host sandboxes acquire | jq -r .sandbox_id)
bin/ephai-sandbox-gateway daemon --sandbox-id "$SID" commands exec --workspace-root /testbed -- pwd
bin/ephai-sandbox-gateway host sandboxes release "$SID"
```
