# sandbox-protocol Crate Spec

## Identity

```text
Path:    crates/sandbox-protocol
Package: sandbox-protocol
Import:  sandbox_protocol
```

`sandbox-protocol` is the shared process contract used by
`sandbox-gateway-cli`, `sandbox-manager`, and `sandbox-daemon`.

## Owns

- Generic request and response structs.
- Unified request scope vocabulary:
  - `OperationScope`
- JSON-line framing helpers.
- Auth field constants.
- Request size and timeout limits.
- Protocol error kind vocabulary.
- Operation metadata types:
  - `OperationSpec`
  - `ArgSpec`
  - `ArgKind`
  - `ArgCliSpec`
  - `CliSpec`
  - `OperationCatalog`
  - `OperationSurface`
  - `OperationAuthority`
- Manual/help rendering helpers that operate only on `OperationSpec`.

## Must Not Own

- Socket listeners or clients.
- Manager operation dispatch.
- Daemon/runtime operation dispatch.
- Command, workspace, layerstack, overlay, namespace, or container runtime
  semantics.
- Any concrete operation list.

## Target Modules

```text
src/
  lib.rs
  scope.rs
  request.rs
  response.rs
  framing.rs
  auth.rs
  limits.rs
  error_kind.rs
  operation_spec.rs
  catalog.rs
  manual.rs
```

## Public DTO Contract

The public protocol has one request DTO and one response DTO. It does not use a
separate routing envelope and it does not expose a `Manager` or `Daemon` target
field.

```rust
pub struct SandboxRequest {
    pub request_id: String,
    pub scope: OperationScope,
    pub op: String,
    pub args: serde_json::Value,
}

#[serde(tag = "kind", rename_all = "snake_case")]
pub enum OperationScope {
    System,
    Sandbox { sandbox_id: String },
}

pub struct SandboxResponse {
    pub request_id: String,
    pub scope: OperationScope,
    pub op: String,
    pub status: ResponseStatus,
    pub result: Option<serde_json::Value>,
    pub error: Option<ResponseError>,
    pub meta: ResponseMeta,
}

pub enum ResponseStatus {
    Ok,
    Running,
    Error,
}

pub struct ResponseError {
    pub kind: String,
    pub message: String,
    pub details: serde_json::Value,
}

pub struct ResponseMeta {
    pub duration_ms: Option<f64>,
    pub warnings: Vec<String>,
}
```

Do not add compatibility aliases such as `OwnedRequest`, `RpcRequest`, or
`Response`. New code should use the explicit names.

Example manager-scoped request:

```json
{
  "request_id": "req-1",
  "scope": { "kind": "system" },
  "op": "list_sandboxes",
  "args": {}
}
```

Example sandbox-scoped request:

```json
{
  "request_id": "req-2",
  "scope": {
    "kind": "sandbox",
    "sandbox_id": "sbox-1"
  },
  "op": "exec_command",
  "args": {
    "workspace_session_id": "ws-1",
    "cmd": "pwd"
  }
}
```

Example response:

```json
{
  "request_id": "req-2",
  "scope": {
    "kind": "sandbox",
    "sandbox_id": "sbox-1"
  },
  "op": "exec_command",
  "status": "running",
  "result": {
    "command_session_id": "cmd-1",
    "state": "running"
  },
  "error": null,
  "meta": {
    "duration_ms": 3.0,
    "warnings": []
  }
}
```

`scope` identifies the resource the operation applies to. It is not the
implementation authority or agent-facing tool surface. `OperationSurface`
belongs in catalog/manual metadata only, for example `manager` vs `runtime`.
`OperationAuthority` remains catalog metadata that describes which component
owns the operation implementation.

## Dependency Rules

Allowed:

- `serde`
- `serde_json`
- small serialization/error crates if needed

Forbidden:

- `sandbox-manager`
- `sandbox-daemon`
- `sandbox-runtime-*`
- `tokio` unless framing becomes explicitly async, which should be avoided.

## Migration Source

Move from:

```text
crates/daemon/rpc_protocol
crates/daemon/operation/src/operation.rs protocol-neutral spec types
```

Keep implementation-specific `OperationEntry` in the owning operation crates.

## Verification

```sh
cargo fmt --check -p sandbox-protocol
cargo check -p sandbox-protocol --tests
cargo test -p sandbox-protocol
```
