# EphemeralOS Sandbox

EphemeralOS is centered on a protocol boundary, host-side sandbox manager,
human-facing gateway CLI, in-sandbox daemon, and separated runtime packages.

```text
operator or agent
   | sandbox-manager-cli / sandbox-runtime-cli or newline-delimited JSON protocol
   v
sandbox-gateway / sandbox-protocol
   v
sandbox-manager
   | forwards sandbox-scoped runtime requests
   v
sandbox-daemon
   | dispatch_operation
   v
sandbox-runtime
   | command operations and workspace session orchestration
   v
sandbox-runtime-workspace / sandbox-runtime-layerstack /
sandbox-runtime-namespace-execution / sandbox-runtime-namespace-process /
sandbox-runtime-overlay
   |
   v
sandbox-config
```

| Component | Kind | Job | Must never |
|---|---|---|---|
| `sandbox-gateway` | bin+lib | own the public gateway listener | own manager or runtime behavior or any CLI client code |
| `sandbox-cli-core` | lib | the gateway client, CLI config discovery, catalog-driven request building, and response/error/help rendering shared by the CLI binaries | know any concrete operation or space policy |
| `sandbox-console` | bin | web console: serve the SPA and bridge the browser to the gateway protocol (`/api/rpc`) and per-sandbox `daemon_http` (`/api/sandboxes/:id/health`, `/api/sandboxes/:id/files/:op`, `/api/sandboxes/:id/observability/:view`, `/s/:id` preview proxy) as a client peer over `sandbox-cli-core` | define operation vocabulary, contact the daemon RPC endpoint directly, or expose the gateway auth token to the browser |
| `sandbox-manager-cli` | bin | operator CLI: manager + observability catalogs, system-scope requests, `--progress` | depend on manager/runtime/daemon/provider implementation crates |
| `sandbox-runtime-cli` | bin | agent CLI: runtime catalog, sandbox-scope requests, required `--sandbox-id` | depend on manager/runtime/daemon/provider implementation crates |
| `sandbox-manager-operations` | lib | manager CLI operation specs and catalog (spec-only) | contain dispatch or service code |
| `sandbox-runtime-operations` | lib | runtime CLI operation specs and catalog (spec-only) | contain dispatch or service code |
| `sandbox-manager` | lib | own sandbox lifecycle, daemon endpoint tracking, and manager operations | implement runtime command/workspace semantics |
| `sandbox-protocol` | lib | own request/response DTOs, framing, catalog, and help metadata | depend on manager, daemon, or runtime implementation crates |
| `sandbox-daemon` | bin+lib | bind daemon transport and dispatch runtime requests | know about Docker fleets |
| `sandbox-runtime` | lib | command operation surface plus internal workspace session orchestration | own low-level runtime primitives |
| `sandbox-runtime-workspace` | lib | workspace runtime lifecycle, namespace handles, capture, and destroy | own command process state |
| `sandbox-runtime-layerstack` | lib | content hashes, manifest/layer types, storage, leases | own command execution |
| `sandbox-runtime-namespace-execution` | lib | namespace execution engine, PTY I/O, and transcript read/write windowing | own workspace lifecycle |
| `sandbox-runtime-namespace-process` | lib | namespace holder/runner bodies and setns execution | own operation dispatch |
| `sandbox-runtime-overlay` | lib | low-level overlay mount and unmount primitives | own workspace lifecycle |
| `sandbox-config` | lib | sandbox YAML loading, merging, and typed gateway/manager/CLI/daemon/runtime config schemas | own runtime behavior |
| `sandbox-provider-docker` | lib | implement the Docker-backed `SandboxRuntime` and `SandboxDaemonInstaller` behind the manager provider traits using the Docker Engine API (bollard) | own generic lifecycle/rollback or depend on `sandbox-daemon` |

**Boundary law:** daemon transport vocabulary lives in
`crates/sandbox-protocol`; daemon request dispatch lives in
`crates/sandbox-daemon`; runtime operation dispatch lives in
`crates/sandbox-runtime/operation`; CLI operation specs (spec-only) live in
`crates/sandbox-manager-operations` and `crates/sandbox-runtime-operations`;
CAS fixtures live with `sandbox-runtime-layerstack`.

## The pieces

- `crates/sandbox-runtime/layerstack/tests/fixtures/` - runtime-owned CAS
  fixtures.
- `crates/` - the workspace: `sandbox-daemon`, `sandbox-protocol`,
  `sandbox-manager`, `sandbox-gateway`, `sandbox-cli-core`,
  `sandbox-manager-cli`, `sandbox-runtime-cli`, `sandbox-manager-operations`,
  `sandbox-runtime-operations`, `sandbox-runtime/operation`,
  `sandbox-runtime/workspace`, `sandbox-runtime/namespace-execution`,
  `sandbox-runtime/namespace-process`, `sandbox-runtime/layerstack`,
  `sandbox-runtime/overlay`, and `sandbox-config`.
- `config/prd.yml` - the single daemon config baseline (see `config/README.md`).
- `dist/` - packaged static `sandbox-daemon` binaries uploaded into sandbox
  containers.

## Common tasks

```sh
# expose repo-local sandbox tools for this shell
export PATH="$PWD/bin:$PATH"

# package the Docker daemon binary if needed and start/restart the public gateway
start-sandbox-docker-gateway

# bootstrap the whole web console stack (gateway + SPA build + console server)
start-sandbox-console-stack        # then open http://127.0.0.1:7880

# in another shell, use the gateway clients directly
sandbox-manager-cli list_sandboxes
sandbox-runtime-cli --sandbox-id eos-abc exec_command pwd

# one-time per machine: bootstrap the musl cross toolchain (zig + cargo-zigbuild)
setup-musl-cross

# package the in-container daemon binary for Docker/E2E iteration
# (builder auto-selected: zigbuild -> cross; override with --builder)
cargo run -p xtask -- package

# final fat-LTO package
cargo run -p xtask -- package --profile release

# run focused daemon checks
cargo test -p sandbox-runtime
cargo test -p sandbox-daemon
```

## Contract owners

The shared daemon JSON-line RPC protocol is owned by `crates/sandbox-protocol`.
LayerStack manifest schema and CAS fixtures are owned by
`crates/sandbox-runtime/layerstack`.
