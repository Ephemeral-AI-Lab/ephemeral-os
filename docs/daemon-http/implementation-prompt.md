# Archived Daemon HTTP v0 Implementation Prompt

> **Archived:** This prompt records the original daemon HTTP v0 build scope.
> It is not the current implementation or review contract. The MCP/CLI cutover
> subsequently added the sole HTTP-only operation `POST /files/list` and made
> the router an exact allowlist: `GET /health`, both `/forward/...` forms, and
> `POST /files/list`. All other operation paths, including `/files/read`,
> `/observability/*`, and `/export/*`, must return `404`. Use
> [`README.md`](README.md) and the binding
> [`mcp_cli_surface/http.md`](../obsidian/ephemeral-os/implementation_plan/mcp_cli_surface/http.md)
> for current work.

The remainder is retained as historical context for the initial listener and
forwarding implementation. Its old three-route inventory is not a complete
description of the current surface.

## Historical goal

Implement the original daemon HTTP v0 as it was specified at the time. Also
read repo-root `README.md` and `CLAUDE.md`.

## Historical scope (v0 only)
- Add `daemon_http_port` to `manager.docker` config (default 7001); validate `1..=65535`.
- Publish the container daemon HTTP port to a random `127.0.0.1` host port. Factor ONE small multi-port publish helper shared with `daemon_port`; don't duplicate Docker port logic.
- Expose `daemon_http` `{host, port}` in the sandbox manager record beside `daemon`.
- Add a daemon HTTP listener, separate from the JSON-line RPC listener — no sniffing or multiplexing.
- Routes: `GET /health`, `ANY /forward/shared/<port>/...`, `ANY /forward/isolated=<workspace_id>/<port>/...`.
- Emit one `daemon_http.forward` span per request through existing `sandbox-observability`.

NOT in v0: TLS, HTML/path rewrite, host-based/gateway routing, isolated `127.0.0.1` setns relay, `/metrics`, `/observability`. Do not create those folders.

## Structure
Rename `crates/sandbox-daemon/src/server/` -> `rpc/` (RPC transport). Create:
```
http/
  mod.rs server.rs router.rs response.rs health.rs
  forward/  mod.rs route.rs proxy.rs
```
HTTP code never mixes into `rpc/`. Route parsing lives in `forward/route.rs`, not `router.rs`. Status/header/body response helpers live in `response.rs` and are shared by health + forward. `health` is one file; promote to a folder only if it grows parts.

## Route model
```rust
enum ForwardRoute {
  Shared   { port: u16, path_and_query: String },
  Isolated { workspace_id: WorkspaceSessionId, port: u16, path_and_query: String },
}
struct ForwardTarget { host: String, port: u16 }
```
The resolver maps route -> target (host + port only); it never copies the request. `path_and_query` stays on the route and the proxy reads it there. Shared -> `127.0.0.1:<port>`. Isolated -> workspace isolated IP via runtime session state. Shared and isolated share ONE forward flow; only target resolution differs.

## Proxy
HTTP/1.1: preserve method, headers, body, query; strip the route prefix; stream request and response bodies; tunnel WebSocket/upgrade requests. Append `X-Forwarded-Host`, `X-Forwarded-Proto: http`, `X-Forwarded-Prefix`.

## Errors (mapped once, at the HTTP boundary)
400 invalid route / invalid port; 404 unknown isolated workspace / non-`/forward` path; 403 workspace has no reachable IP; 502 connect failed; 504 timeout. `/health` returns 200 `{"status":"ok","service":"daemon_http"}` and must not depend on runtime state.

## Engineering (CLAUDE.md, required)
SOLID / SRP — name each unit's single job. Prefer less: fewer types, fields, methods, cross-boundary hops; no one-impl traits or builders. No inline comments in `src/`; no test code, fakes, or fixtures under `src/` — they go in `tests/`. External crates via `[workspace.dependencies]` + `dep.workspace = true`, never pinned in member crates. Don't leak Docker details into the manager model; don't push HTTP concerns into runtime operations. Work directly on `main`; additive, localized edits — other agents edit concurrently, so never revert work you didn't write.

## E2E
Add `cli-operation-e2e-live-test/runtime/daemon_http/test_daemon_http.py` with `test_daemon_http_health`, `test_forward_shared_arbitrary_port`, `test_forward_isolated_arbitrary_port`, `test_forward_rejects_invalid_routes`. Drive real sandboxes via `sandbox-cli manager` and real servers via `runtime exec_command`. Success tests bind port `0` and use the assigned port — never hardcode 3000. Clean up every sandbox, workspace session, and running command.

## Verify
```
export PATH="$PWD/bin:$PATH"
cargo build && cargo test
cargo clippy --all-targets   # no new violations; no unwrap_used
cargo fmt
```
Target ~550-900 production LOC. Flag any change that would cross a crate boundary from `README.md`.
