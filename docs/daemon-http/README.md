# Daemon HTTP

The per-sandbox daemon HTTP listener has a deliberately small public surface.
It provides liveness, application traffic forwarding, and one read-only file
listing operation. Management, runtime, and observability operations use the
authenticated gateway through the three CLI executables, the corresponding
MCP registrations, or the console's authenticated `/api/rpc` bridge.

The daemon HTTP listener is separate from the daemon's authenticated
JSON-line RPC listener. The two protocols are never sniffed or multiplexed on
one port.

## Exact Public Surface

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/health` | fixed HTTP-listener liveness response |
| any supported HTTP/1.1 method | `/forward/shared/<port>/...` | forward application traffic to `127.0.0.1:<port>` in the shared network namespace |
| any supported HTTP/1.1 method | `/forward/isolated=<workspace_id>/<port>/...` | forward application traffic to a live isolated workspace's resolved IP and port |
| `POST` | `/files/list` | one-level, read-only listing of a published snapshot or live workspace session |

Every other path returns `404 not found`. In particular, there are no direct
daemon HTTP routes for file read/write/edit/blame, observability, management,
runtime command execution, or export streaming.

`POST /files/list` is the sole operation endpoint that remains on daemon HTTP.
It is intentionally absent from the public runtime catalog and every CLI/MCP
projection. Its canonical identifier lives in
`crates/sandbox-operations/catalog/src/internal/runtime.rs` and its handler is
kept in the runtime application's separate HTTP-only registry.

## Endpoint Discovery

The manager publishes the daemon's container-side HTTP port to a random host
loopback port. A sandbox record exposes the resulting endpoint separately from
the daemon RPC endpoint:

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

`daemon` is the authenticated JSON-line RPC endpoint. `daemon_http` is the
limited HTTP listener documented here. Docker-backed installations configure
their separate container ports with `manager.docker.daemon_port` and
`manager.docker.daemon_http_port`; both are published to random
`127.0.0.1` host ports.

## `GET /health`

The health handler does not read sandbox, workspace, or runtime state. It is a
pure liveness signal for the HTTP listener.

```http
GET /health HTTP/1.1
Host: 127.0.0.1
```

```http
HTTP/1.1 200 OK
Content-Type: application/json

{"status":"ok","service":"daemon_http"}
```

A method other than `GET` on `/health` is not an allowed route and returns
`404`.

## Forwarding

### Shared network

```text
/forward/shared/5173/
  -> http://127.0.0.1:5173/

/forward/shared/5173/assets/app.js?v=7
  -> http://127.0.0.1:5173/assets/app.js?v=7
```

`<port>` must be a decimal value in `1..=65535`.

### Isolated workspace network

```text
/forward/isolated=ws-abc/3000/
  -> http://<resolved-workspace-ip>:3000/

/forward/isolated=ws-abc/3000/api/health?verbose=1
  -> http://<resolved-workspace-ip>:3000/api/health?verbose=1
```

The workspace id must identify a live isolated workspace session with a
reachable IP. The route does not create a session. A service in that namespace
must listen on `0.0.0.0:<port>` or the workspace IP; isolated loopback
forwarding is not provided.

### Proxy contract

Both route forms share one HTTP/1.1 forwarding flow:

- The method, request body, remaining path, and query string are preserved.
- The `/forward/shared/<port>` or
  `/forward/isolated=<workspace_id>/<port>` prefix is removed upstream.
- Normal end-to-end request and response headers are preserved. Hop-by-hop
  headers are removed for ordinary requests.
- `X-Forwarded-Host`, `X-Forwarded-Proto: http`, and the matching
  `X-Forwarded-Prefix` are sent upstream.
- Request and response bodies are streamed.
- A successful HTTP upgrade is tunneled after the rewritten handshake.

The daemon emits one `daemon_http.forward` observability span for every
forwarding request. Forwarding itself is application traffic proxying, not an
operation RPC surface.

### Forwarding errors

| Condition | Status | Response text |
| --- | ---: | --- |
| invalid `/forward/...` form | `400` | `invalid forward route` |
| missing, zero, non-numeric, or out-of-range port | `400` | `invalid forward port` |
| unknown or no-longer-live isolated workspace | `404` | `unknown isolated workspace` |
| isolated workspace has no reachable IP | `403` | `isolated workspace has no reachable IP` |
| target connection or HTTP handshake fails | `502` | `target connection failed` |
| target connection or first response times out | `504` | `target timed out` |

A path that does not begin with `/forward/` is a normal `404`, not a forward
route parsing error.

## `POST /files/list`

`/files/list` performs a one-level, read-only directory listing. An empty body
is equivalent to `{}` and lists the workspace root from the latest published
snapshot.

```http
POST /files/list HTTP/1.1
Host: 127.0.0.1
Content-Type: application/json

{"path":"src","workspace_session_id":"ws-1"}
```

| JSON field | Required | Meaning |
| --- | --- | --- |
| `path` | no | repository-relative or workspace-root-absolute directory; empty or omitted means the root |
| `workspace_session_id` | no | existing live session to list; omitted means the latest published snapshot |

The body must be a JSON object within the normal protocol request-size limit.
The daemon creates the request id and sandbox scope internally; callers cannot
inject either of those values or gateway credentials.

A successful result has the normal operation response shape:

```json
{
  "path": "src",
  "entries": [
    {"name": "lib.rs", "kind": "file", "size": 324},
    {"name": "bin", "kind": "directory", "size": 0}
  ],
  "truncated": false
}
```

Transport errors are handled as follows:

| Condition | Response |
| --- | --- |
| non-`POST` method on the exact `/files/list` path | `405 Method Not Allowed` with `use POST` |
| malformed JSON, non-object JSON, or an oversized body | `400` with the standard operation error envelope |
| runtime validation or list failure | `200` with the normal operation JSON error envelope |
| `/files/list/...` or any other `/files/*` path | `404` |

Returning operation failures in a `200` response preserves the operation
response contract; malformed HTTP transport input still receives a `400` or
`405`.

## Removed Operation Routes

The MCP/CLI cutover intentionally removed the daemon HTTP compatibility routes
below. Direct requests receive `404`.

| Removed path | Supported replacement |
| --- | --- |
| `/files/read` | runtime `file_read` through `sandbox-runtime-cli`, runtime MCP, or console `/api/rpc` |
| `/files/write` | runtime `file_write` through CLI, MCP, or console `/api/rpc` |
| `/files/edit` | runtime `file_edit` through CLI, MCP, or console `/api/rpc` |
| `/files/blame` | runtime `file_blame` through CLI, MCP, or console `/api/rpc` |
| `/observability/{snapshot,trace,events,cgroup,layerstack}` | `sandbox-observability-cli`, observability MCP, or console `/api/rpc` |
| `/export/*` | management `export_changes` through `sandbox-manager-cli` or management MCP |

`export_changes` pages the authenticated internal `read_export_chunk` RPC. It
exports the published-layer delta, not the full workspace, and never obtains a
daemon HTTP export token or URL.

The console retains only the exact
`/api/sandboxes/:id/files/list` daemon proxy alongside its health and preview
forwarding proxies. All other browser operations go through the console's
authenticated `/api/rpc`; gateway credentials are never exposed to browser
code.

## Implementation Boundary

The allowlist lives in `crates/sandbox-daemon/src/http/router.rs`:

```text
crates/sandbox-daemon/src/http/
├── mod.rs
├── server.rs
├── router.rs
├── response.rs
├── health.rs
├── api.rs                 # bounded POST /files/list handling only
└── forward/
    ├── mod.rs
    ├── route.rs
    └── proxy.rs
```

There is no HTTP export module and no generic files or observability route
dispatcher. The daemon constructs the contract-owned `OperationRequest` for
`file_list`; runtime request-to-service dispatch remains in
`crates/sandbox-runtime/operation`. Public manager, runtime, and observability
definitions and routes live in the feature-gated domain modules under
`crates/sandbox-operations/catalog/src/`; CLI presentation metadata lives only
in `crates/sandbox-cli/src/projection/`.

## Contract Checks

Changes to daemon HTTP must directly prove:

1. exact `GET /health` output without runtime state;
2. shared and isolated forwarding, including method/body/path/query and
   forwarding headers;
3. the documented `400`, `403`, `404`, `502`, and `504` mappings;
4. root, published-snapshot, and live-session file listings plus method, body,
   and size validation;
5. `404` for read/write/edit/blame, observability, export, and arbitrary
   unlisted routes.

The maintained live proof is
`e2e/runtime/daemon_http/test_daemon_http.py`; run it according to
[`e2e/RUNNING.md`](../../e2e/RUNNING.md).

## Related Contracts

- [`http.md`](../obsidian/ephemeral-os/implementation_plan/mcp_cli_surface/http.md)
  is the binding cutover design.
- [`cli.md`](../obsidian/ephemeral-os/implementation_plan/mcp_cli_surface/cli.md)
  defines the three command-line surfaces.
- [`mcp.md`](../obsidian/ephemeral-os/implementation_plan/mcp_cli_surface/mcp.md)
  defines the three independently granted MCP sets.
- [`operation-contract.md`](../obsidian/ephemeral-os/implementation_plan/mcp_cli_surface/operation-contract.md)
  lists the cross-boundary operation contract.
