# Web Console HTTP Server (`sandbox-console`)

The HTTP surface behind the web console ([[web-ui-design]]): one same-origin
server the browser talks to for the operations plane (gateway JSON-line RPC),
app preview (per-sandbox `daemon_http` `/forward`), daemon health, and the SPA
assets.

## Goal

Give the browser a single origin that covers everything the web UI needs,
while adding **zero new vocabulary**: RPC passes through to the gateway
protocol 1:1, preview passes through to `daemon_http` 1:1.

## Position

New bin crate `sandbox-console`. It is a **client peer** of the three CLI
executables, built on `sandbox_operation_client::GatewayClient` — not an extension
of `sandbox-gateway` (the gateway must never own client code) and not a
vocabulary owner (operation vocabulary stays in the manager, runtime, and
observability modules of the canonical `sandbox-operation-catalog`).

```text
browser
   | HTTP + SSE + WebSocket upgrade (one origin)
   v
sandbox-console
   | /api/rpc                    GatewayClient (JSON-line over TCP)
   |                               -> sandbox-gateway -> manager / daemon rpc
   | /api/sandboxes/<id>/files/list
   |                               -> daemon_http /files/list
   | /s/<sandbox-id>/...         reverse proxy
   |                               -> daemon_http /forward/...  (per sandbox)
   | /api/sandboxes/<id>/health  -> daemon_http /health
   v
static SPA assets
```

Must never: define operation vocabulary, contact the daemon RPC endpoint
directly (all ops go through the gateway), or expose the gateway auth token to
the browser.

## Public routes

```text
POST /api/rpc                                  one-shot operation dispatch
POST /api/rpc   (Accept: text/event-stream)    same, streaming progress logs
GET  /api/catalog                              operation catalogs
GET  /api/sandboxes/<id>/health                daemon_http health probe
POST /api/sandboxes/<id>/files/list            exact daemon_http list proxy
ANY  /s/<id>/shared/<port>/...                 preview proxy, shared network
ANY  /s/<id>/isolated=<ws-id>/<port>/...       preview proxy, isolated ws
GET  /*                                        SPA assets + route fallback
```

## RPC bridge

`POST /api/rpc` body is the protocol request minus transport fields:

```json
{
  "op": "exec_command",
  "scope": { "kind": "sandbox", "sandbox_id": "eos-abc" },
  "args": { "cmd": "pwd" }
}
```

The `scope` value is the merged contract's `OperationScope` wire shape verbatim:
`{ "kind": "system" }` for manager/aggregate operations, `{ "kind":
"sandbox", "sandbox_id": "..." }` for sandbox-scoped ones. A client-supplied
`request_id` passes through; otherwise the console mints one.

The console injects `request_id` and `ephemeral_sandbox_gateway_auth`, sends
via `GatewayClient`, and returns the protocol `result`/`error` body verbatim
with HTTP 200. HTTP status codes are reserved for transport failures (400
malformed body, 502 gateway unreachable, 504 gateway timeout), so the client
has exactly two error paths: protocol errors in the body, transport errors in
the status.

### Streaming variant

With `Accept: text/event-stream` the console sets `_stream_logs: true` and
uses `send_with_logs`, emitting SSE:

```text
event: log
data: {"line":"creating sandbox eos-abc..."}

event: result
data: {"result":{...}}
```

This is the web equivalent of the manager CLI's `--progress`; it feeds
`StreamLogPane` during create/destroy/squash. Protocol errors still arrive
as the `result` event (the body carries `error`); if the gateway transport
fails after the stream opened, the console emits one terminal `error` event
(`{"kind", "message"}`) instead of a `result`.

## Catalog

`GET /api/catalog` returns the manager, runtime, and observability operation
catalogs. These domains are modules of the spec-only
`sandbox-operation-catalog`, so web forms, argument validation, and help text
render from the same semantic specs the adapters use and cannot drift.

## Health

`GET /api/sandboxes/<id>/health` resolves the sandbox record's `daemon_http`
endpoint and requests `/health` with a short timeout:

```json
{ "status": "ok" }
{ "status": "unreachable", "detail": "..." }
```

Backs the endpoint health dot on `SandboxCard` and `SandboxHeader`.

## Preview proxy

The console path mirrors the daemon path exactly, so forwarding is one prefix
swap (`/s/<id>` ↔ `/forward`):

```text
/s/eos-abc/shared/5173/assets/app.js
  -> http://<daemon_http host:port>/forward/shared/5173/assets/app.js

/s/eos-abc/isolated=ws-1/3000/?q=1
  -> http://<daemon_http host:port>/forward/isolated=ws-1/3000/?q=1
```

Why a proxy at all: `daemon_http` publishes on **host loopback**
(`127.0.0.1:<random-port>`), so the browser can only reach it directly when it
runs on the sandbox host. The console proxy makes preview URLs same-origin and
host-independent.

Behavior mirrors `daemon_http`'s own proxy: preserve method, headers, body,
and query; stream bodies; tunnel WebSocket and other HTTP upgrades; append
`X-Forwarded-*`. The `<id>` segment resolves to the `daemon_http` endpoint via
the manager record; resolution is cached briefly so asset-heavy pages don't
trigger a record lookup per request.

The prefix is short (`/s/`) because these URLs live in the address bar and
inside proxied apps. Apps emitting absolute paths still break without HTML
rewriting — the same v0 limitation `daemon_http` accepts, with the same
answer: don't fix it in v0.

Isolated caveat passes through unchanged: the in-session server must bind
`0.0.0.0:<port>` or the workspace IP; isolated-loopback relay is a skipped
`daemon_http` feature, and the daemon answers 403 when the workspace has no
reachable IP.

## Errors

Console-originated:

```text
400 invalid preview route or port
404 unknown sandbox id
503 sandbox not ready / record has no daemon_http endpoint
502 daemon_http unreachable
```

`daemon_http`-originated (passed through verbatim): 400 invalid route/port,
404 unknown isolated workspace, 403 no reachable workspace IP, 502 target
connection failed, 504 target timed out.

## Auth

The gateway auth token lives server-side in console config; the browser never
sees it. Browser-to-console auth is out of scope for v0 — bind the console to
loopback, matching the gateway and `daemon_http` host-port posture. Add a
session layer only when the console leaves localhost.

## Non-goals (v0)

- REST-per-resource endpoints (`GET /api/sandboxes`, …) — vocabulary belongs
  to the operation contract and catalog; the UI calls `/api/rpc` with
  `list_sandboxes`.
- WebSocket RPC multiplexer — polling (`read_command_lines`) plus SSE covers
  the UI design; revisit only if transcript tailing needs push.
- TLS, host-based routing, HTML rewriting — front with a standard reverse
  proxy if needed; mirror `daemon_http`'s restraint.
- Publishing sandbox service ports through Docker.

## Observability

The console adds no pipeline in v0. Preview traffic is already recorded as
`daemon_http.forward` spans in each sandbox's observability log, so the web
UI's own Traces/Events views show console-originated preview requests without
extra work.

## Implementation scope

V0:

- `sandbox-console` bin crate: HTTP server over
  `sandbox_operation_client::GatewayClient`.
- `/api/rpc` (one-shot + SSE), `/api/catalog`,
  `/api/sandboxes/<id>/health`, and exact
  `/api/sandboxes/<id>/files/list`.
- `/s/...` preview proxy with body streaming and upgrade tunneling, plus
  short-TTL endpoint resolution cache.
- Static SPA serving with client-route fallback.

Later:

- browser session/auth layer (when the console leaves localhost)
- fleet-wide event feed (needs a new observability op first)
- binary file transfer for the Files tab
