# Ephemeral Sandbox

<p align="center">
  <img src="assets/mascot.png" alt="Ephemeral Sandbox mascot: a Siamese cat in a sandbox" width="320">
</p>

Ephemeral Sandbox gives parallel coding agents isolated workspaces inside one
shared sandbox. Agents share the same project history, work independently, and
publish only the changes they intend to keep.

## Why Ephemeral Sandbox?

- Run multiple coding agents concurrently without letting their workspaces
  overwrite one another.
- Share one stable project base while each agent gets a private writable
  session.
- Inspect commands, files, runtime health, and the origin of published changes.

## Quick start

You need Docker, Rust 1.85 or newer, and Cargo. The Docker launcher currently
also expects the project-provided Git archives under `dist/git/`.

```sh
git clone https://github.com/Ephemeral-AI-Lab/ephemeral-sandbox.git
cd ephemeral-sandbox

export PATH="$PWD/bin:$PATH"
docker pull ubuntu:24.04
bin/setup-musl-cross
bin/start-sandbox-docker-gateway --rebuild-binary
```

Create a sandbox rooted at the current checkout:

```sh
sandbox-manager-cli create_sandbox \
  --image ubuntu:24.04 \
  --workspace-bind-root "$PWD"
```

Use the sandbox ID returned by that command:

```sh
sandbox-runtime-cli --sandbox-id eos-abc exec_command pwd
sandbox-observability-cli snapshot --sandbox-id eos-abc
```

## Choose an interface

| Interface | Best for | Start with |
|---|---|---|
| CLI | Operators, scripts, and local development | `sandbox-manager-cli help` |
| MCP | Coding agents and MCP-compatible clients | `bin/setup-codex-mcp` |
| Web console | Browser-based operation and inspection | `bin/start-sandbox-console-stack` |

The CLI and MCP interfaces use three focused tool groups:

- **Management** creates, inspects, exports, and destroys sandboxes.
- **Runtime** runs commands and reads or changes files inside a sandbox.
- **Observability** inspects health, events, resources, and filesystem layers.

Each MCP server exposes one tool group:

```sh
sandbox-mcp --set management
sandbox-mcp --set runtime
sandbox-mcp --set observability
```

## How it works

1. **Share a stable base.** LayerStack keeps the project history available to
   every workspace session.
2. **Work in isolation.** Each agent gets its own writable workspace and
   execution boundary.
3. **Publish safely.** Ephemeral Sandbox checks concurrent changes before
   publishing the complete resolved change set, or publishes nothing.

For crate ownership and dependency boundaries, see the
[maintainer architecture](docs/maintainer-architecture.md).

## More documentation

- [Configuration](config/README.md)
- [Daemon HTTP and application forwarding](docs/daemon-http/README.md)
- [Maintainer architecture and component boundaries](docs/maintainer-architecture.md)
- Run `sandbox-manager-cli help`, `sandbox-runtime-cli --sandbox-id ID help`,
  or `sandbox-observability-cli help` for installed command syntax.

## How to contribute

Focused fixes, documentation improvements, and tests are welcome. Before
opening a pull request, run:

```sh
cargo fmt --check
cargo clippy --all-targets
cargo test
```

Keep the change focused and describe what it changes, why it is needed, and how
you verified it in the pull request.

## License

Ephemeral Sandbox is available under the [MIT License](LICENSE).
