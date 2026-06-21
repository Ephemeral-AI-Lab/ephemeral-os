# sandbox-gateway CLI Module Spec

## Identity

```text
Path:    crates/sandbox-gateway/src/cli
Package: sandbox-gateway
Import:  sandbox_gateway::cli
Binary:  sandbox-cli
```

`sandbox-gateway::cli` is the human-facing command line. It builds protocol
requests, sends them to the gateway socket, and renders responses.

## Owns

- CLI argument parsing.
- CLI config discovery and precedence.
- Gateway client connection setup.
- Request construction from `OperationSpec` and CLI argv.
- Output formatting and exit-code behavior.

## Must Not Own

- Sandbox lifecycle state.
- Daemon endpoint registry.
- Daemon operation dispatch.
- Command/workspace/layerstack/overlay semantics.
- Direct daemon endpoint knowledge for normal use.

## Target Modules

```text
src/cli/
  main.rs
  config.rs
  client.rs
  request_builder.rs
  output.rs
```

## CLI Rules

- Installed binary name is `sandbox-cli`.
- Errors go to stderr.
- Machine-readable responses go to stdout.
- Catalog help text goes to stdout.
- Default route is gateway -> manager.
- Canonical execution spaces are `sandbox-cli manager ...` and
  `sandbox-cli runtime --sandbox-id ID ...`.
- Catalog help is scoped by execution space:
  - `sandbox-cli manager help`
  - `sandbox-cli manager help create_sandbox`
  - `sandbox-cli runtime help`
  - `sandbox-cli runtime help exec_command`
- Parser help remains separate:
  - `sandbox-cli manager --help`
  - `sandbox-cli runtime --help`
- Manager operations use `request.scope = system`.
- Runtime operations require `--sandbox-id SANDBOX_ID` unless config provides a
  default sandbox, and set `request.scope = sandbox`.
- Runtime help requires selected/default sandbox context, but rendered runtime
  help usage and examples do not include `--sandbox-id`.
- Catalog documents are derived locally from manager/runtime operation specs.
- Request construction validates that `sandbox-cli manager ...` consumes a
  manager catalog and `sandbox-cli runtime ...` consumes a runtime catalog.
- `help` is reserved and cannot be used as an operation name. `manual` is not a
  command or compatibility alias.

## Example Commands

```text
sandbox-cli manager create_sandbox --image ubuntu:24.04 --workspace-root /testbed
sandbox-cli manager help
sandbox-cli manager help create_sandbox
sandbox-cli manager list_sandboxes
sandbox-cli runtime help
sandbox-cli runtime help exec_command
sandbox-cli runtime --sandbox-id sbox-1 exec_command --workspace-session-id ws-1 "pwd"
sandbox-cli runtime --sandbox-id sbox-1 poll_command --command-session-id cmd-1
```

## Dependency Rules

Allowed:

- `sandbox-protocol`
- `sandbox-manager` for manager operation catalog metadata
- `sandbox-runtime` for runtime operation catalog metadata
- CLI parsing/output crates

Forbidden:

- `sandbox-daemon`
- direct runtime support crates such as `sandbox-runtime-command`,
  `sandbox-runtime-workspace`, `sandbox-runtime-layerstack`, and
  `sandbox-runtime-overlay`

The CLI talks to the gateway socket for operation execution; it does not become
a hidden manager or daemon client.

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

The gateway does not construct retired request wrappers or any
manager/runtime/daemon target envelope.

## Verification

```sh
cargo fmt --check -p sandbox-gateway
cargo check -p sandbox-gateway --tests
cargo test -p sandbox-gateway
```
