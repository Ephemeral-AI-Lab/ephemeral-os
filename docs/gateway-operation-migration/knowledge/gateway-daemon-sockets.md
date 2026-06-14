# Gateway and Daemon Sockets

EphemeralOS has two different Unix socket concepts:

| Socket | Lives where | Created by | Used by | Scope |
|---|---|---|---|---|
| `/tmp/eos-sandbox.sock` | Host machine | `gateway serve --listen /tmp/eos-sandbox.sock` | External clients, CLI tools, agents | Fleet gateway: acquire/list/release sandboxes and route requests by `sandbox_id` |
| `/eos/runtime/daemon/runtime.sock` | Inside each sandbox container | `eosd daemon --socket /eos/runtime/daemon/runtime.sock` | Host internals and direct `docker exec` fallback | One daemon inside one container |

The host gateway socket is the public/control entrypoint. The daemon socket is a
private per-container entrypoint.

## Request Flow

```text
client
  -> /tmp/eos-sandbox.sock
  -> gateway / host
  -> selected sandbox by sandbox_id
  -> eosd daemon inside that container
  -> daemon transport, including /eos/runtime/daemon/runtime.sock
```

## eosd Deployment

`eosd` is the daemon binary deployed into each sandbox container. The host copies
it into the container, usually at:

```text
/eos/runtime/daemon/eosd
```

The host then starts it approximately as:

```sh
/eos/runtime/daemon/eosd daemon \
  --socket /eos/runtime/daemon/runtime.sock \
  --pid-file /eos/runtime/daemon/runtime.pid \
  --log-file /eos/runtime/daemon/runtime.log \
  --tcp-host 0.0.0.0 \
  --tcp-port 37657
```

## Request Shape Difference

Gateway requests include `sandbox_id` for sandbox-scoped operations because the
gateway fronts many containers.

```json
{
  "op": "sandbox.command.exec",
  "sandbox_id": "sb-...",
  "invocation_id": "req-1",
  "args": {
    "cmd": "pwd"
  }
}
```

Direct daemon requests do not include `sandbox_id` because the client is already
talking to one daemon inside one container. Direct daemon requests must include
the daemon protocol version inside `args`.

```json
{
  "op": "sandbox.command.exec",
  "invocation_id": "req-1",
  "args": {
    "_eos_daemon_protocol_version": 1,
    "cmd": "pwd"
  }
}
```

## Mental Model

| Question | Use |
|---|---|
| Need to acquire, list, release, or route across sandboxes? | `/tmp/eos-sandbox.sock` |
| Need to talk directly to one daemon inside one container? | `/eos/runtime/daemon/runtime.sock` |
| Need normal client behavior? | Gateway socket |
| Need low-level fallback/debugging for a known container? | Direct daemon socket via `docker exec` |
