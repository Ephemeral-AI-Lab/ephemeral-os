# Daemon HTTP

## Goal

Expose HTTP services running inside a sandbox through one Docker-published
loopback host port, without publishing arbitrary service ports directly.

The public host endpoint is the sandbox record's `daemon_http` endpoint:

```text
http://127.0.0.1:<daemon_http_host_port>
```

Runtime services are reached through the `/forward` namespace:

```text
/forward/shared/<port>/...
/forward/isolated=<workspace_id>/<port>/...
```

## Non-Goals

- Do not mix HTTP forwarding with the existing daemon JSON-line RPC listener.
- Do not publish every sandbox service port through Docker.
- Do not add TLS, host-based routing, HTML rewriting, or isolated loopback
  relays in v0.

## Public Routes

```text
GET /health
ANY /forward/shared/<port>/...
ANY /forward/isolated=<workspace_id>/<port>/...
```

`/health` returns JSON:

```json
{
  "status": "ok",
  "service": "daemon_http"
}
```

Future daemon HTTP routes reserve their own top-level namespaces:

```text
/metrics
/observability
```

## Forwarding Semantics

Shared workspace routing:

```text
/forward/shared/5173/
  -> http://127.0.0.1:5173/

/forward/shared/5173/assets/app.js
  -> http://127.0.0.1:5173/assets/app.js
```

Isolated workspace routing:

```text
/forward/isolated=ws-abc/3000/
  -> http://<workspace_isolated_ip>:3000/
```

Forwarding strips only the route prefix and preserves the remaining path and
query string.

## Configuration

Docker-backed sandboxes expose two container-side daemon ports:

```yaml
manager:
  docker:
    daemon_port: 7000
    daemon_http_port: 7001
```

Docker publishes both to random loopback host ports:

```text
7000/tcp -> 127.0.0.1:<daemon_rpc_host_port>
7001/tcp -> 127.0.0.1:<daemon_http_host_port>
```

## Manager Record

Sandbox records expose both endpoints:

```json
{
  "daemon": {
    "host": "127.0.0.1",
    "port": 64236
  },
  "daemon_http": {
    "host": "127.0.0.1",
    "port": 64961
  }
}
```

`daemon` remains the authenticated JSON-line RPC endpoint. `daemon_http` is the
HTTP daemon surface.

## Daemon Design

`sandbox-daemon` owns the HTTP listener because it already owns runtime state,
workspace session resolution, and observability.

```text
sandbox-daemon
  rpc listener         JSON-line protocol
  http listener        daemon_http health + forwarding
```

The listeners are separate. The RPC protocol is not sniffed or multiplexed with
browser HTTP traffic.

## Canonical Module Structure

The current daemon RPC implementation lives in `src/server/`. When adding
daemon HTTP, rename that folder to `src/rpc/` so the transport surfaces sit
beside each other clearly:

```text
crates/sandbox-daemon/src/
  rpc/
    mod.rs
    server.rs
    connection.rs
    dispatch.rs
    error.rs
    lifecycle.rs
    runtime.rs

  http/
    mod.rs
    server.rs
    router.rs
    response.rs
    health.rs

    forward/
      mod.rs
      route.rs
      proxy.rs

    metrics/
      mod.rs

    observability/
      mod.rs
```

Each daemon HTTP capability owns one module: a file when it is a single
responder (like `health`), a folder when it has multiple parts (like
`forward`):

```text
crates/sandbox-daemon/src/http/
  mod.rs
  server.rs
  router.rs
  response.rs

  health.rs

  forward/
    mod.rs
    route.rs
    proxy.rs

  metrics/
    mod.rs

  observability/
    mod.rs
```

Only create capability modules when they exist. V0 should create:

```text
crates/sandbox-daemon/src/http/
  mod.rs
  server.rs
  router.rs
  response.rs

  health.rs

  forward/
    mod.rs
    route.rs
    proxy.rs
```

## Route Model

The route parser produces a small typed route:

```rust
enum ForwardRoute {
    Shared {
        port: u16,
        path_and_query: String,
    },
    Isolated {
        workspace_id: WorkspaceSessionId,
        port: u16,
        path_and_query: String,
    },
}
```

The target resolver maps a route to a network destination. Both route kinds
forward to a TCP `host:port`, so the destination is a plain struct, not an
enum:

```rust
struct ForwardTarget {
    host: String,
    port: u16,
}
```

Resolution only chooses the destination host and port; it never copies the
request. `path_and_query` stays on the `ForwardRoute`, and the proxy reads it
from there.

Shared targets resolve to `127.0.0.1:<port>`.

Isolated targets resolve through the runtime's workspace session state to the
workspace isolated IP and requested port.

## Proxy Behavior

V0 supports HTTP/1.1 forwarding:

- Preserve method, headers, body, and query string.
- Strip `/forward/shared/<port>` or
  `/forward/isolated=<workspace_id>/<port>` before forwarding.
- Stream request and response bodies.
- Tunnel WebSocket and other HTTP upgrade requests after sending the rewritten
  initial request.
- Add or append forwarding headers:
  - `X-Forwarded-Host`
  - `X-Forwarded-Proto: http`
  - `X-Forwarded-Prefix`

## Validation

Reject invalid requests with HTTP errors:

```text
400 invalid route
400 invalid port
404 unknown isolated workspace
403 isolated workspace has no reachable IP
502 target connection failed
504 target timed out
```

Ports must be in `1..=65535`.

## Isolated Workspace Caveat

`/forward/isolated=<workspace_id>/<port>` works when the server inside the
isolated workspace listens on `0.0.0.0:<port>` or the workspace IP.

If the server listens only on `127.0.0.1:<port>` inside the isolated network
namespace, the daemon cannot reach it through the veth IP. Supporting that
requires a setns relay and is out of scope for v0.

## Observability

Emit one span per forwarded request:

```text
span: daemon_http.forward
attrs:
  route_kind: shared | isolated
  workspace_id: optional
  target_host
  target_port
  method
  path_prefix
  status_code
  duration_ms
  bytes_in
  bytes_out
  error_kind
```

The span should use the existing `sandbox-observability` pipeline. No new
metrics backend is required for v0.

## E2E Test Cases

Add live Docker E2E coverage under:

```text
cli-operation-e2e-live-test/runtime/daemon_http/test_daemon_http.py
```

The tests must create real sandboxes through `sandbox-cli manager`, run real
servers through `sandbox-cli runtime exec_command`, call `daemon_http` from the
host, and clean up every sandbox, workspace session, and running command.

### Health

`test_daemon_http_health`

Flow:

1. Create a sandbox.
2. Read `daemon_http.host` and `daemon_http.port` from the create or inspect
   response.
3. Request `http://<host>:<port>/health` from the host.
4. Assert:

```text
status code == 200
content-type contains application/json
body.status == "ok"
body.service == "daemon_http"
```

5. Destroy the sandbox.

### Shared Forward

`test_forward_shared_arbitrary_port`

Flow:

1. Create a sandbox.
2. Start a long-running HTTP server with `exec_command` in the shared network.
3. The server must bind port `0`, print the assigned port, and return a body
   containing:

```text
route=shared
path=<request path>
query=<request query>
```

4. Request from the host:

```text
http://<daemon_http_host>:<daemon_http_host_port>/forward/shared/<assigned_port>/nested/path?hello=world
```

5. Assert:

```text
status code == 200
body contains route=shared
body contains path=/nested/path
body contains query=hello=world
```

6. Stop the command and destroy the sandbox.

This test must not hardcode `3000`; it must use the port assigned by the server.

### Isolated Forward

`test_forward_isolated_arbitrary_port`

Flow:

1. Create a sandbox.
2. Create one workspace session with `--network-profile isolated`.
3. Start a long-running HTTP server in that workspace session.
4. The server must bind `0.0.0.0:0`, print the assigned port, and return a body
   containing:

```text
route=isolated
workspace=<workspace_id>
path=<request path>
query=<request query>
```

5. Request from the host:

```text
http://<daemon_http_host>:<daemon_http_host_port>/forward/isolated=<workspace_id>/<assigned_port>/nested/path?hello=isolated
```

6. Assert:

```text
status code == 200
body contains route=isolated
body contains workspace=<workspace_id>
body contains path=/nested/path
body contains query=hello=isolated
```

7. Stop the command, destroy the workspace session, and destroy the sandbox.

This test covers the v0 isolated contract: the server listens on `0.0.0.0` or
the workspace IP, not isolated loopback.

### Forward Errors

`test_forward_rejects_invalid_routes`

Flow:

1. Create a sandbox.
2. Request invalid routes from the host.
3. Assert:

```text
/forward/shared/not-a-port/        -> 400
/forward/shared/0/                 -> 400
/forward/isolated=missing/3000/    -> 404
/not-forward/shared/3000/          -> 404
```

4. Destroy the sandbox.

## Implementation Scope

V0:

- Add `daemon_http_port` manager Docker config.
- Publish `daemon_http_port` to a random host loopback port.
- Expose `daemon_http` in sandbox records.
- Add daemon HTTP listener.
- Add `/health`.
- Add `/forward/shared/<port>/...`.
- Add `/forward/isolated=<workspace_id>/<port>/...` for isolated workspace IP
  listeners.
- Add forwarding spans.

Later:

- `/metrics`
- `/observability`
- isolated `127.0.0.1` setns relay
- HTML/path rewrite support
- host-based routing
- TLS

## Expected Size

Shared-only v0 is expected to be about 300-500 production LOC.

Shared plus isolated-IP forwarding and observability is expected to be about
550-900 production LOC, plus focused tests.
