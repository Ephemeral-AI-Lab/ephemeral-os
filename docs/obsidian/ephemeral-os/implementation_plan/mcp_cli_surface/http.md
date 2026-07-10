---
title: Daemon HTTP surface and implementation design
tags:
  - ephemeral-os
  - http
  - daemon
  - api
  - implementation-plan
status: proposed
updated: 2026-07-10
aliases:
  - Daemon HTTP API
---

# Daemon HTTP surface and implementation design

This document defines the direct HTTP API served by each sandbox daemon after
the MCP/CLI cutover. It is intentionally small. All ordinary management,
runtime, and observability operations move to the authenticated gateway via
[[mcp]] or [[cli]].

> [!important] Exact HTTP allowlist
> The daemon exposes exactly `GET /health`, both `/forward/...` route forms,
> and `POST /files/list`. Every other direct daemon operation route returns
> `404`, including read/write/edit/blame, observability, and export routes.

## Endpoint inventory

| Method | Path | Purpose | Authentication / scope |
| --- | --- | --- | --- |
| `GET` | `/health` | pure HTTP-listener liveness probe | none; does not query runtime state |
| any supported HTTP/1.1 method | `/forward/shared/{port}[/{path}]` | reverse proxy to a service in the daemon/shared network namespace | target is `127.0.0.1:{port}` |
| any supported HTTP/1.1 method | `/forward/isolated={workspace_id}/{port}[/{path}]` | reverse proxy to a service in an isolated live workspace’s network namespace | target is the runtime-resolved isolated workspace IP |
| `POST` | `/files/list` | one-level, read-only listing of published snapshot or live workspace session | daemon-local runtime dispatch; selected live session is an optional body field |

The two forwarding forms are application traffic proxying, not RPC operation
endpoints. They retain original methods and bodies and can support HTTP/1.1
upgrade/WebSocket traffic. `/files/list` is the explicit exception to the
“all operations through MCP/CLI” rule.

## `GET /health`

### Request

```http
GET /health HTTP/1.1
Host: daemon-http-host
```

No request body or query parameter is used. The handler does not read
sandbox/workspace state, so it remains a pure liveness signal for the HTTP
listener.

### Response

```http
HTTP/1.1 200 OK
Content-Type: application/json

{"status":"ok","service":"daemon_http"}
```

## `ANY /forward/shared/{port}[/{path}]`

This route forwards to a TCP service listening at `127.0.0.1:{port}` in the
shared network namespace.

```text
/forward/shared/5173/
/forward/shared/5173/assets/app.js?v=7
/forward/shared/8080/api/items
```

### Route arguments

| Route segment | Type/validation | Meaning |
| --- | --- | --- |
| `{port}` | decimal `u16`, range `1..=65535` | target TCP port |
| `{path}` | optional remaining URI path | target request path after route prefix removal |
| query string | optional, unmodified | appended to target request path |

The target request path is `/` plus the remaining path. For example,
`/forward/shared/5173/assets/app.js?v=7` reaches
`http://127.0.0.1:5173/assets/app.js?v=7`.

## `ANY /forward/isolated={workspace_id}/{port}[/{path}]`

This route forwards to a TCP service reachable through a live isolated
workspace session.

```text
/forward/isolated=ws-abc/3000/
/forward/isolated=ws-abc/3000/api/health?verbose=1
```

### Route arguments

| Route segment | Type/validation | Meaning |
| --- | --- | --- |
| `{workspace_id}` | non-empty runtime `WorkspaceSessionId` | selects a live isolated workspace session |
| `{port}` | decimal `u16`, range `1..=65535` | target TCP port inside that namespace |
| `{path}` / query | same preservation rules as shared forwarding | target request path/query |

The runtime resolves `workspace_id` to its current isolated IP. The route does
not create a workspace session and does not work for an unknown/destroyed
session or a workspace without reachable isolated networking.

## Forwarding request/response semantics

### Preserved and rewritten data

The proxy opens one HTTP/1.1 connection to the resolved target, forwards the
request body as a stream, and relays the upstream response body as a stream.
It has a 10-second target-connect timeout and a 30-second first-response
timeout.

| Data | Behaviour |
| --- | --- |
| method | preserved |
| target URI | route prefix removed; remaining path/query preserved |
| normal end-to-end headers | forwarded |
| hop-by-hop headers | removed for ordinary HTTP forwarding: `connection`, `keep-alive`, `proxy-connection`, `transfer-encoding`, `te`, `trailer`, `upgrade` |
| `Host` | passed upstream; also copied into `X-Forwarded-Host` |
| `X-Forwarded-Proto` | set to `http` |
| `X-Forwarded-Prefix` | set to `/forward/shared/{port}` or `/forward/isolated={workspace_id}/{port}` |
| upgrades | `Connection: Upgrade` / `Upgrade` is tunneled after a `101 Switching Protocols` response |
| upstream response status and normal headers | preserved; response hop-by-hop headers removed |

The daemon records one `daemon_http.forward` observability span per forwarded
request. It includes route kind, optional workspace id, target host/port,
method, route prefix, response status, duration, bytes in/out, and where
applicable the failure kind.

### Forwarding error contract

| Condition | HTTP status | Plain-text response | span error kind |
| --- | ---: | --- | --- |
| invalid route form | `400` | `invalid forward route` | `invalid_route` |
| port missing, zero, non-numeric, or >65535 | `400` | `invalid forward port` | `invalid_port` |
| isolated workspace id is not live | `404` | `unknown isolated workspace` | `unknown_workspace` |
| isolated workspace has no reachable IP | `403` | `isolated workspace has no reachable IP` | `no_reachable_ip` |
| target connect/HTTP handshake failure | `502` | `target connection failed` | `connect_failed` |
| connect or first-response timeout | `504` | `target timed out` | `timeout` |

An unsupported non-forward path is `404`, not a forwarding parse error.

## `POST /files/list`

`files/list` is the one remaining daemon operation endpoint. It is
read-only and one-level only; it does not turn the daemon HTTP listener back
into a general runtime RPC surface.

### Request

```http
POST /files/list HTTP/1.1
Host: daemon-http-host
Content-Type: application/json

{"path":"src","workspace_session_id":"ws-1"}
```

The JSON body is optional. An empty body behaves as `{}`. It must be a JSON
object and is constrained by the normal `MAX_REQUEST_BYTES` protocol request
limit.

| JSON field | Required | Type | Meaning |
| --- | --- | --- | --- |
| `path` | no | string | repository-relative or workspace-root-absolute directory; omit or pass empty to list workspace root |
| `workspace_session_id` | no | string | existing live session to list; omit to list the latest published snapshot |

The daemon generates its own request id and sandbox scope, then dispatches
the internal `file_list` runtime operation. The caller cannot provide scope,
gateway credentials, request id, or any write operation through this route.

### Success result

```json
{
  "path": "src",
  "entries": [
    { "name": "lib.rs", "kind": "file", "size": 324 },
    { "name": "bin", "kind": "directory", "size": 0 }
  ],
  "truncated": false
}
```

`entries` is one directory level. Each entry has a `name`, `kind`, and `size`.
Without `workspace_session_id`, the listing projects the latest published
layerstack snapshot. With one, it reads the live mounted workspace for that
session.

### Errors and method handling

| Condition | HTTP response |
| --- | --- |
| method other than `POST` on the exact `/files/list` path | `405 Method Not Allowed`, text `use POST` |
| malformed JSON, non-object JSON, or body exceeds limit | `400` with the protocol JSON error envelope |
| runtime/list validation or list failure | `200` with the normal operation JSON error envelope (preserves existing daemon operation response semantics) |
| `/files/list/anything`, `/files/read`, `/files/write`, `/files/edit`, `/files/blame` | `404` |

Keeping operation errors at `200` for the accepted list endpoint is deliberate
compatibility with the existing daemon operation responder. HTTP transport
validation remains a `400`/`405` concern.

## Removed direct daemon operation routes

The router must not dispatch any route in this table after the cutover.

| Removed direct route | Replacement | Reason |
| --- | --- | --- |
| `POST /files/read` | runtime MCP `file_read`, runtime CLI `file_read`, or console gateway `/api/rpc` | ordinary runtime operation |
| `POST /files/write` | runtime MCP/CLI `file_write` | mutating runtime operation |
| `POST /files/edit` | runtime MCP/CLI `file_edit` | mutating runtime operation |
| `POST /files/blame` | runtime MCP/CLI `file_blame` | runtime operation |
| `POST /observability/{snapshot,trace,events,cgroup,layerstack}` | observability MCP/CLI tools or console gateway `/api/rpc` | read-only operation set, not daemon HTTP |
| `GET /export/{export_id}` | manager MCP/CLI `export_changes` via gateway `read_export_chunk` | remove token-gated spool stream and HTTP export transport |
| any other `/files/*`, `/observability/*`, `/export/*` | none; `404` | enforce exact allowlist |

The router’s default is `404 not found`. The fact that an operation remains
daemon-dispatchable internally does not make an HTTP route public.

## Target implementation structure

The daemon HTTP module remains small and focused. It is a loopback HTTP/1.1
listener separate from the daemon JSON-line RPC listener; it does no protocol
sniffing or multiplexing.

```text
crates/sandbox-daemon/src/http/
├── mod.rs                       # module exports: health, forward, api, router, response, server
├── server.rs                    # listener / per-connection service / HttpState
├── router.rs                    # exact allowlist selection
├── health.rs                    # fixed liveness JSON responder
├── api.rs                       # POST /files/list only: JSON object read + internal dispatch
├── response.rs                  # reusable HTTP body/JSON/text response helpers
└── forward/
    ├── mod.rs                   # parse -> resolve -> proxy + failure/span mapping
    ├── route.rs                 # typed shared/isolated route parsing
    └── proxy.rs                 # HTTP/1.1 stream relay and upgrade tunnel

deleted: crates/sandbox-daemon/src/http/export.rs
```

`HttpState` retains daemon configuration, runtime operations, optional daemon
observability, and observer because forwarding needs workspace resolution and
span emission while `files/list` dispatches its runtime handler. It must not
become a holder for manager, gateway client, or CLI/MCP state.

### Required file changes by location

| Location | Target change |
| --- | --- |
| `crates/sandbox-daemon/src/http/router.rs` | dispatch only exact `GET /health`, exact `POST /files/list` (hand method mismatch to list handler only for `/files/list`), and paths beginning `/forward/`; default `404` |
| `crates/sandbox-daemon/src/http/api.rs` | remove generic `file_op()` mapping and `observability_view()`; retain bounded JSON-object parsing plus `file_list` request construction/dispatch only |
| `crates/sandbox-daemon/src/http/export.rs` | delete complete token-gated export spool stream handler |
| `crates/sandbox-daemon/src/http/mod.rs` | remove `export` module export and unused imports |
| `crates/sandbox-runtime/operation/src/operation_adapter/file_operations.rs` | retain internal `FILE_LIST` `OperationEntry` with `cli: None`; it is the target invoked by HTTP only |
| `crates/sandbox-runtime/operation/src/operation_adapter/workspace_session_operations.rs` | retain lifecycle dispatch with `cli: None`; forwards need live-session lookup but no direct lifecycle HTTP route exists |
| `crates/sandbox-manager/src/operation/management/service/impls/export_changes.rs` | delete daemon HTTP stream client/token/header/completeness path; page internal authenticated `read_export_chunk` RPC only and retain atomic destination application checks |
| `crates/sandbox-protocol/src/{lib.rs,export_stream.rs}` | delete export-stream path/token vocabulary after the HTTP stream caller audit is clean |
| `crates/sandbox-console/src/{router.rs,daemon_api.rs}` | keep health/forward proxies and narrow file proxy to exact list path; move other operation callers to console authenticated `/api/rpc` |
| `docs/daemon-http/README.md` and root README | replace old general operation-route documentation with this allowlist and MCP/CLI migration references |

### Target router pseudocode

```rust
match (request.method(), request.uri().path()) {
    (&Method::GET, "/health") => health::respond(),
    (_, "/files/list") => api::handle_file_list(state, request).await,
    _ if request.uri().path().starts_with("/forward/") => {
        forward::handle(state, request).await
    }
    _ => response::text(StatusCode::NOT_FOUND, "not found"),
}
```

This is intentionally a route allowlist rather than a `/files/` or
`/observability/` prefix dispatcher. A request such as `POST /files/read` must
not reach `api.rs` at all.

## Console implications

The console is an authenticated gateway client for business operations and a
daemon HTTP client only for the allowlist.

| Console route/current responsibility | Target |
| --- | --- |
| `/api/rpc` | remains the operation path for runtime and observability calls, including all former file operations other than listing |
| `/api/sandboxes/:id/health` | remains a daemon `/health` proxy |
| `/s/:id/...` preview route | remains a daemon `/forward/...` proxy |
| generic `/api/sandboxes/:id/files/:op` | replace with exact read-only `/api/sandboxes/:id/files/list` proxy only |
| `/api/sandboxes/:id/observability/:view` | remove; frontend uses `/api/rpc` |

This keeps the gateway token out of the browser while preventing the console
from preserving an unintended daemon HTTP compatibility API.

## Tests and acceptance checks

Daemon and console tests must prove all of the following:

1. `GET /health` returns exact fixed `200` JSON without depending on runtime
   state.
2. Shared forwarding preserves method/body/path/query, adds the documented
   forwarding headers, and works on a dynamically assigned port.
3. Isolated forwarding resolves a live workspace id and rejects unknown/no-IP
   sessions with documented status codes.
4. Invalid forward grammar/ports return `400`; target failures map to
   `502`/`504`; upgrade handling remains covered.
5. `POST /files/list` works for published snapshot and a supplied live
   workspace session. Empty body lists the root. Bad method/body receives the
   documented transport errors.
6. `/files/read`, `/files/write`, `/files/edit`, `/files/blame`,
   `/observability/snapshot`, and `/export/x` all return `404`.
7. `export_changes` completes via chunk paging without a daemon HTTP export
   listener request; truncated/missing chunk failure still protects atomic
   destination application.
8. Console tests assert only the retained health/forward/list daemon proxies
   exist and that former operation callers use `/api/rpc`.

## Related documents

- [[mcp]] — the public management/runtime/observability tool API.
- [[cli]] — the corresponding three executable command-line APIs.
- [[operation-contract]] — concise cross-boundary catalog.
- [[implementation-spec]] — full code migration order, LOC budget, and
  release acceptance criteria.
