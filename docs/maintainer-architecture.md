# Maintainer architecture

This document defines component ownership and dependency boundaries for
Ephemeral Sandbox. It is intended for maintainers; the top-level README stays
focused on using the project.

## Request path

```text
operator or agent
   | sandbox-manager-cli / sandbox-runtime-cli / sandbox-observability-cli
   | sandbox-mcp --set management|runtime|observability
   | sandbox-console
   v
sandbox-operation-catalog + adapter-owned projection
   | adapter builds an operation-contract request
   v
sandbox-operation-client
   | authenticated newline-delimited JSON via sandbox-protocol
   v
sandbox-gateway
   v
sandbox-manager
   | handles system routes and forwards sandbox routes
   v
sandbox-daemon
   | decodes wire requests and composes applications
   v
sandbox-runtime / sandbox-observability-query
   | command, file, workspace, layerstack, and observability behavior
   v
sandbox-runtime-workspace / sandbox-runtime-layerstack /
sandbox-runtime-namespace-execution / sandbox-runtime-namespace-process /
sandbox-runtime-overlay / sandbox-observability-telemetry
```

## Component map

| Component | Kind | Job | Must never |
|---|---|---|---|
| `sandbox-operation-contract` | lib | Own adapter-neutral operation, argument, scope, route, request, response, and application-error types | Depend on any workspace package or own wire/presentation behavior |
| `sandbox-operation-catalog` | lib | Own canonical internal identifiers and routes unconditionally, plus every public declaration and route in feature-gated manager/runtime/observability modules | Depend on anything except the contract, own CLI metadata, or contain handlers |
| `sandbox-operation-client` | lib | Own gateway discovery and wire transport shared by CLI, MCP, and console, plus value-based request construction shared by CLI and MCP | Depend on the catalog, applications, adapters, or `sandbox-config` |
| `sandbox-gateway` | bin+lib | Compose the public gateway listener, manager application, Docker provider, daemon wire client, and local daemon installer | Own application behavior, depend on CLI/MCP/console or the shared client, or compose runtime applications directly |
| `sandbox-cli` | lib + 3 bins | Own CLI paths, flags, positionals, help, output, and separately feature-gated manager/runtime/observability executables | Depend on protocol/applications/other adapters, provide a combined executable, or let one binary enumerate another authority |
| `sandbox-mcp` | bin | Project exactly one selected domain from the merged catalog as a stdio MCP server and send through the shared client | Define a second catalog, expose a combined set, or depend on protocol/applications/CLI/console |
| `sandbox-console` | bin | Serve the SPA, validate public `/api/rpc` routes, send through the shared client, and proxy the allowed per-sandbox daemon HTTP surface | Define operation vocabulary, depend on protocol/applications/CLI/MCP, contact daemon RPC directly, or expose gateway credentials to the browser |
| `sandbox-manager` | lib | Own sandbox lifecycle, daemon endpoint tracking, system-scoped operation handlers, routing, and application ports | Depend on protocol/client/adapters/composition roots or implement runtime command/workspace semantics |
| `sandbox-protocol` | lib | Own wire codec, framing, authentication fields, limits, and the daemon readiness handshake | Own operation declarations/help or depend on catalog/applications/client/adapters |
| `sandbox-daemon` | bin+lib | Compose authenticated RPC, the exact HTTP allowlist, runtime dispatch, observability dispatch, sampling, and lifecycle | Depend on product adapters/client/manager or expose operation routes over HTTP beyond `file_list` |
| `sandbox-observability-query` | lib | Own structured observability query selection and response construction through an application-owned input port | Depend on protocol/client/adapters/daemon or the concrete runtime application |
| `sandbox-observability-telemetry` | lib | Own tracing, events, sampling, collection, and reading primitives | Depend on any workspace package |
| `sandbox-runtime` | lib | Own public runtime handlers plus canonical internal workspace-session/layerstack dispatch and orchestration | Depend on protocol/client/adapters/composition roots or own low-level runtime primitives |
| `sandbox-runtime-workspace` | lib | Own workspace runtime lifecycle, namespace handles, capture, and destroy | Own command process state |
| `sandbox-runtime-layerstack` | lib | Own content hashes, manifest/layer types, storage, and leases | Own command execution |
| `sandbox-runtime-namespace-execution` | lib | Own the namespace execution engine, PTY I/O, and transcript read/write windowing | Own workspace lifecycle |
| `sandbox-runtime-namespace-process` | lib | Own namespace holder/runner bodies and setns execution | Own operation dispatch |
| `sandbox-runtime-overlay` | lib | Own low-level overlay mount and unmount primitives | Own workspace lifecycle |
| `sandbox-config` | lib | Own sandbox YAML loading, merging, validation, and typed console/gateway/manager/daemon/observability/runner/runtime schemas | Depend on any workspace package or own runtime behavior |
| `sandbox-provider-docker` | lib | Implement manager ports with Docker and use protocol only for daemon readiness | Own generic lifecycle/rollback, application handlers, client behavior, or depend on `sandbox-daemon` |

## Boundary law

Semantic and application-envelope vocabulary lives in
`crates/sandbox-operations/contract`. Every public declaration, route, and
canonical internal identifier lives in `crates/sandbox-operations/catalog`.
Shared gateway client behavior lives in `crates/sandbox-operations/client`.
CLI metadata lives only in `crates/sandbox-cli/src/projection`. Wire-only codec,
framing, authentication, limits, and readiness live in
`crates/sandbox-protocol`.

Applications (`sandbox-manager`, `sandbox-runtime`, and
`sandbox-observability-query`) never depend on protocol, the client, product
adapters, composition roots, or each other's implementations. The contract,
config, telemetry, layerstack, and overlay packages have no workspace
dependencies. The catalog depends only on the contract, protocol depends only
on the contract, and the client depends only on contract and protocol. CAS
fixtures live with `sandbox-runtime-layerstack`.

Exactly three organizational namespace directories exist under `crates/`:
`sandbox-operations/`, `sandbox-observability/`, and `sandbox-runtime/`. They
are grouping directories only and never gain a root `Cargo.toml`, Rust facade,
package identity, or re-export layer.

## Repository layout

- `crates/sandbox-operations/` groups `contract/`, `catalog/`, and `client/`.
- `crates/sandbox-observability/` groups `telemetry/` and `query/`.
- `crates/sandbox-runtime/` groups `operation/`, `workspace/`, `layerstack/`,
  `namespace-execution/`, `namespace-process/`, and `overlay/`.
- `crates/` also contains the flat CLI, config, console, daemon, gateway,
  manager, MCP, protocol, and Docker-provider packages.
- `crates/sandbox-runtime/layerstack/tests/fixtures/` owns runtime CAS fixtures.
- `e2e/` contains live CLI, MCP, console, gateway, manager, daemon, runtime,
  and observability coverage.
- `web/console/` contains the tracked SPA source.
- `config/prd.yml` is the daemon configuration baseline.
- `dist/` contains packaged static binaries and supporting artifacts uploaded
  into sandbox containers.

## Public interface boundaries

The CLI has three executables: management, runtime, and observability. There is
no combined executable. MCP uses one binary, but each process selects exactly
one fixed `management`, `runtime`, or `observability` tool set. CLI and MCP tool
definitions come from the same semantic catalog.

The web console does not invoke MCP servers or CLI executables. Browser
management, command, file read/write/edit/blame, and observability requests go
to the console server's `POST /api/rpc`. The server keeps gateway credentials
private and sends authenticated gateway RPC through
`sandbox-operation-client`.

Each sandbox record has a `daemon_http` endpoint separate from its authenticated
daemon RPC endpoint. The HTTP listener exposes only:

```text
GET  /health
ANY  /forward/shared/<port>/...
ANY  /forward/isolated=<workspace_id>/<port>/...
POST /files/list
```

`file_list` is the deliberate HTTP-only operation exception. Direct
`/files/read`, `/files/write`, `/files/edit`, `/files/blame`,
`/observability/*`, and `/export/*` requests return `404`. Use the relevant
management, runtime, or observability CLI/MCP set, or the console's
authenticated `/api/rpc` bridge, for those operations.

The optional `file_list` JSON fields are `path`, `workspace_session_id`, and
`limit`. The limit must be at least 1 and is clamped to the daemon's fixed
`runtime.file.max_list_entries` safety cap. See
[daemon HTTP](daemon-http/README.md), including the
[host-access example](daemon-http/README.md#access-a-web-server-from-the-host),
for request and forwarding details.

## Contract owners

The adapter-neutral operation envelope is owned by
`crates/sandbox-operations/contract`; semantic declarations and routes are
owned by `crates/sandbox-operations/catalog`; the daemon JSON-line wire codec,
framing, authentication, limits, and readiness handshake are owned by
`crates/sandbox-protocol`. LayerStack manifest schema and CAS fixtures are owned
by `crates/sandbox-runtime/layerstack`.
