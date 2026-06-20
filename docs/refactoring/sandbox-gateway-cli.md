# sandbox-gateway-cli Crate Spec

## Identity

```text
Path:    crates/sandbox-gateway-cli
Package: sandbox-gateway-cli
Import:  sandbox_gateway_cli
Binary:  sandbox
```

`sandbox-gateway-cli` is the human-facing command line. It builds protocol
requests, sends them to `sandbox-manager`, and renders responses.

## Owns

- CLI argument parsing.
- CLI config discovery and precedence.
- Manager client connection setup.
- Request construction from `OperationSpec` and CLI argv.
- Manual/help rendering for manager and runtime execution spaces.
- Output formatting and exit-code behavior.

## Must Not Own

- Sandbox lifecycle state.
- Daemon endpoint registry.
- Daemon operation dispatch.
- Command/workspace/layerstack/overlay semantics.
- Direct daemon endpoint knowledge for normal use.

## Target Modules

```text
src/
  main.rs
  config.rs
  client.rs
  manual.rs
  request_builder.rs
  output.rs
```

## CLI Rules

- Installed binary name is `sandbox`.
- Errors go to stderr.
- Machine-readable responses go to stdout.
- Default route is gateway -> manager.
- Canonical execution spaces are `sandbox manager ...` and
  `sandbox runtime --sandbox-id ID ...`.
- Manager operations use `request.scope = system`.
- Runtime operations require `--sandbox-id SANDBOX_ID` unless config provides a
  default sandbox, and set `request.scope = sandbox`.
- Help/manual text is generated from `OperationSpec`, not duplicated by hand.

## Example Commands

```text
sandbox manager create_sandbox --sandbox-id sbox-1
sandbox manager list_sandboxes
sandbox runtime --sandbox-id sbox-1 exec_command --workspace-session-id ws-1 "pwd"
sandbox runtime --sandbox-id sbox-1 poll_command --command-session-id cmd-1
```

## Dependency Rules

Allowed:

- `sandbox-protocol`
- CLI parsing/output crates

Forbidden:

- `sandbox-daemon`
- `sandbox-runtime-*`
- direct sandbox runtime libraries

The CLI talks to `sandbox-manager`; it does not become a hidden manager.

## Request Construction

The gateway builds `sandbox_protocol::Request` directly:

```json
{
  "request_id": "req-1",
  "scope": { "kind": "system" },
  "op": "list_sandboxes",
  "args": {}
}
```

For runtime operations:

```json
{
  "request_id": "req-2",
  "scope": {
    "kind": "sandbox",
    "sandbox_id": "sbox-1"
  },
  "op": "exec_command",
  "args": {
    "cmd": "pwd"
  }
}
```

The gateway does not construct `ManagerRequest`, `RoutedRequest`, or any
manager/runtime/daemon target envelope.

## Verification

```sh
cargo fmt --check -p sandbox-gateway-cli
cargo check -p sandbox-gateway-cli --tests
cargo test -p sandbox-gateway-cli
```
