# sandbox-gateway Server Spec

## Purpose

Make `sandbox-gateway` the long-lived public ingress for all host-side sandbox
requests.

The current CLI command already presents the gateway as the user entrypoint:

```text
sandbox manager ...
sandbox runtime --sandbox-id ID ...
```

The process topology should match that user model. External clients should
connect to a gateway socket, not a manager socket. The manager remains the
host-side control-plane domain library behind the gateway.

## Target Topology

```text
sandbox CLI
  -> /tmp/sandbox-gateway.sock
    -> sandbox-gateway server
      -> sandbox-manager control plane
        -> per-sandbox sandbox-daemon endpoint
          -> sandbox-runtime operation dispatch
```

There is one public host-side ingress socket:

```text
/tmp/sandbox-gateway.sock
```

There is not a public `/tmp/sandbox-manager.sock` in the target design.

## Package Shape

Add a long-lived gateway package:

```text
Path:    crates/sandbox-gateway
Package: sandbox-gateway
Import:  sandbox_gateway
Binary:  sandbox-gateway
```

Keep the CLI package:

```text
Path:    crates/sandbox-gateway-cli
Package: sandbox-gateway-cli
Import:  sandbox_gateway_cli
Binary:  sandbox
```

Keep the manager package:

```text
Path:    crates/sandbox-manager
Package: sandbox-manager
Import:  sandbox_manager
```

## Responsibilities

### `sandbox-gateway`

Owns:

- The long-lived host-side public process.
- Gateway socket config and lifecycle.
- Unix listener binding, pid-file lifecycle, permissions, and shutdown.
- Request framing and response framing at the public ingress boundary.
- Gateway connection concurrency limits.
- Calling the `sandbox-manager` request router.
- Public ingress names and environment variables.

Must not own:

- Sandbox lifecycle state.
- Sandbox registry/store persistence semantics.
- Daemon endpoint registry semantics.
- Manager operation implementations.
- Runtime command/workspace/layerstack/overlay semantics.
- Direct runtime operation dispatch.

### `sandbox-gateway-cli`

Owns:

- CLI argument parsing.
- CLI config discovery and precedence.
- Gateway client connection setup.
- Request construction from `OperationSpec` and CLI argv.
- Manual/help rendering for manager and runtime execution spaces.
- Output formatting and exit-code behavior.

Must not own:

- Any long-lived listener.
- Gateway server lifecycle.
- Sandbox lifecycle state.
- Daemon endpoint registry.
- Daemon operation dispatch.
- Runtime libraries.

### `sandbox-manager`

Owns:

- Sandbox identity and lifecycle state.
- Sandbox registry/store.
- Host runtime abstraction for creating and destroying sandboxes.
- Installing, starting, stopping, and health-checking `sandbox-daemon`.
- Mapping `SandboxId` to `SandboxDaemonEndpoint`.
- Manager operation catalog and dispatch.
- Routing sandbox-scoped runtime requests to the selected sandbox daemon.

Must not own:

- Public ingress socket names.
- User-facing socket config.
- CLI argument parsing.
- Runtime command/workspace/layerstack/overlay semantics.

The manager may expose a request-router API for the gateway to call. It should
not be the public host-side listener in the target design.

## Protocol Contract

Do not change `sandbox_protocol::Request` or `sandbox_protocol::Response`.

Requests still use:

```text
request_id
scope
op
args
```

Routing still uses `OperationScope`:

- `OperationScope::System` for manager-scoped operations.
- `OperationScope::Sandbox { sandbox_id }` for runtime operations.

The gateway does not add a new routing envelope, target field, owner field, or
retired request wrapper. It receives the same protocol request the manager
server currently receives, then delegates dispatch to `sandbox-manager`.

## Manager Router API

Move the request routing logic out of `sandbox-manager/src/server` into a
manager-owned router module, for example:

```text
crates/sandbox-manager/src/router/
  mod.rs
  dispatch.rs
  forward.rs
```

Suggested public API:

```rust
pub struct SandboxManagerRouter {
    services: Arc<ManagerServices>,
}

impl SandboxManagerRouter {
    pub fn new(services: Arc<ManagerServices>) -> Self;

    pub async fn dispatch_request(
        &self,
        request: sandbox_protocol::Request,
    ) -> sandbox_protocol::Response;
}
```

The router owns the current scope-based decision:

```text
system scope + manager operation      -> dispatch locally
system scope + unknown operation      -> unknown_op
sandbox scope + manager operation     -> invalid_request
sandbox scope + runtime operation     -> forward to selected sandbox daemon
```

Forwarding remains manager-owned because it depends on sandbox lifecycle state
and `SandboxId -> SandboxDaemonEndpoint` lookup.

## Gateway Server Modules

Create the gateway server around the manager router:

```text
crates/sandbox-gateway/
  Cargo.toml
  src/
    main.rs
    lib.rs
    config.rs
    connection.rs
    lifecycle.rs
    server.rs
```

Suggested types:

```rust
pub struct GatewayServerConfig {
    pub socket_path: PathBuf,
    pub pid_path: PathBuf,
    pub max_concurrent_connections: usize,
}

pub struct SandboxGatewayServer {
    pub config: GatewayServerConfig,
    pub manager: sandbox_manager::SandboxManagerRouter,
    pub shutdown: CancellationToken,
}
```

`SandboxGatewayServer::serve()` owns:

- Creating parent directories for socket and pid paths.
- Removing stale socket files.
- Binding the Unix listener.
- Setting socket permissions to `0600` on Unix.
- Writing the pid file.
- Accepting until shutdown.
- Enforcing the connection cap.
- Cleaning up socket and pid files on shutdown.

Connection handling should preserve the existing newline-delimited JSON
framing and byte limits from `sandbox-protocol`.

## CLI Config Changes

Rename public config from manager socket to gateway socket.

Target CLI flags:

```text
--gateway-socket PATH
--default-sandbox-id SANDBOX_ID
```

Target environment variables:

```text
SANDBOX_GATEWAY_SOCKET
SANDBOX_DEFAULT_ID
```

Target default:

```text
/tmp/sandbox-gateway.sock
```

Implementation notes:

- Rename `GatewayConfig.manager_socket_path` to `gateway_socket_path`.
- Rename `ManagerClient` to `GatewayClient`.
- Render connection errors as gateway connection errors.
- Keep request construction behavior unchanged.
- Keep CLI output behavior unchanged: data to stdout, errors to stderr.

Compatibility aliases may be temporary if needed:

```text
--manager-socket
SANDBOX_MANAGER_SOCKET
```

If aliases are retained, document them as deprecated and keep
`--gateway-socket` / `SANDBOX_GATEWAY_SOCKET` as the canonical surface.

## Manager Server Migration

The current `sandbox-manager/src/server` listener should not remain the public
ingress after the gateway server exists.

Preferred migration:

1. Extract request dispatch and forwarding into `sandbox-manager/src/router`.
2. Add `crates/sandbox-gateway` and move listener/connection lifecycle there.
3. Wire `sandbox-gateway` to `sandbox_manager::SandboxManagerRouter`.
4. Rename public socket config to gateway terminology.
5. Remove or make private any obsolete manager-server public exports.

Do not leave two first-class public sockets that accept the same request API.
That would make the entrypoint ambiguous and split operational documentation.

## Dependency Rules

Allowed:

```text
sandbox-gateway-cli -> sandbox-protocol
sandbox-gateway     -> sandbox-protocol
sandbox-gateway     -> sandbox-manager
sandbox-manager     -> sandbox-protocol
sandbox-manager     -> host runtime and daemon client abstractions
sandbox-daemon      -> sandbox-protocol
sandbox-daemon      -> sandbox-runtime
```

Forbidden:

```text
sandbox-gateway-cli -> sandbox-manager
sandbox-gateway-cli -> sandbox-daemon
sandbox-gateway-cli -> sandbox-runtime-*
sandbox-gateway     -> sandbox-daemon runtime implementation
sandbox-gateway     -> sandbox-runtime-*
sandbox-manager     -> sandbox-runtime-*
```

The gateway may call manager APIs. It must not become a runtime implementation
or a hidden daemon.

## Commands

Start the long-lived gateway:

```sh
cargo run -p sandbox-gateway -- serve
```

Use the CLI through the gateway:

```sh
cargo run -p sandbox-gateway-cli -- manager list_sandboxes
cargo run -p sandbox-gateway-cli -- runtime --sandbox-id sbox-1 exec_command --workspace-session-id ws-1 pwd
```

Override the gateway socket:

```sh
cargo run -p sandbox-gateway -- serve --gateway-socket /tmp/eos-gateway.sock
cargo run -p sandbox-gateway-cli -- --gateway-socket /tmp/eos-gateway.sock manager list_sandboxes
```

## Tests

Add focused tests for:

- `sandbox-gateway` binds the configured gateway socket and writes the pid file.
- Gateway connection handling decodes one request and writes one response.
- Gateway connection handling enforces request size and newline framing limits.
- Gateway overload responses preserve structured JSON errors.
- Gateway dispatch calls the manager router for system-scope manager requests.
- Gateway dispatch calls the manager router for sandbox-scope runtime requests.
- CLI config precedence uses `--gateway-socket` over `SANDBOX_GATEWAY_SOCKET`
  over the default.
- Deprecated `--manager-socket` and `SANDBOX_MANAGER_SOCKET`, if retained, map
  to the gateway socket with lower priority than the canonical names.
- Runtime CLI requests still require `--sandbox-id` or `SANDBOX_DEFAULT_ID`.
- No CLI tests depend on `sandbox-manager` internals.

## Verification

Run the narrow checks while migrating:

```sh
cargo fmt --check -p sandbox-protocol -p sandbox-manager -p sandbox-gateway -p sandbox-gateway-cli
cargo check -p sandbox-protocol -p sandbox-manager -p sandbox-gateway -p sandbox-gateway-cli --tests
cargo test -p sandbox-protocol -p sandbox-manager -p sandbox-gateway -p sandbox-gateway-cli
cargo clippy -p sandbox-protocol -p sandbox-manager -p sandbox-gateway -p sandbox-gateway-cli --all-targets --no-deps -- -D warnings
git diff --check
```

Before closing the migration, run stale-name scans:

```sh
rg -n "manager_socket|manager-socket|SANDBOX_MANAGER_SOCKET|sandbox-manager\\.sock|/tmp/sandbox-manager\\.sock" crates docs README.md config
rg -n "SandboxManagerServer|ServerConfig" crates/sandbox-manager crates/sandbox-gateway
```

Remaining `manager` terms are valid only when they refer to the manager domain
library, manager operations, or manager operation catalog. Public ingress
socket names should use gateway terminology.

## Acceptance Criteria

- External clients connect to `/tmp/sandbox-gateway.sock` by default.
- The installed `sandbox` CLI exposes `--gateway-socket`, not
  `--manager-socket`, as the canonical socket override.
- `sandbox-gateway` is the only long-lived public host-side listener.
- `sandbox-manager` owns lifecycle state and request routing decisions, but not
  the public listener process.
- Runtime requests still route by `OperationScope::Sandbox { sandbox_id }`.
- Manager requests still route by `OperationScope::System`.
- No new request DTO, target wrapper, or `invoke_sandbox_daemon` operation is
  introduced.
- `sandbox-gateway-cli` remains a protocol client and has no dependency on
  `sandbox-manager`, `sandbox-daemon`, or `sandbox-runtime-*`.
