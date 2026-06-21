# Sandbox Implementation Guide

This guide describes the current sandbox architecture after the manager,
daemon, gateway CLI, protocol, and runtime package split. It is the active
final-state guide; historical phase prompts remain separate records.

Reference specs:

```text
docs/refactoring/sandbox-protocol.md
docs/refactoring/sandbox-runtime.md
docs/refactoring/sandbox-daemon.md
docs/refactoring/sandbox-manager.md
docs/refactoring/sandbox-gateway-cli.md
```

## Package Shape

```text
crates/
  sandbox-protocol/
  sandbox-manager/
  sandbox-gateway-cli/
  sandbox-daemon/
  sandbox-runtime/
    operation/             # package: sandbox-runtime
    command/               # package: sandbox-runtime-command
    workspace/             # package: sandbox-runtime-workspace
    namespace-process/     # package: sandbox-runtime-namespace-process
    layerstack/            # package: sandbox-runtime-layerstack
    overlay/               # package: sandbox-runtime-overlay
    config/                # package: sandbox-runtime-config
```

Root workspace metadata should list only these sandbox packages plus `xtask`.
Do not add retired workspace members, old package names, or hidden entrypoints.

## Protocol Contract

All process clients use `sandbox_protocol::Request` and
`sandbox_protocol::Response`.

Requests must include:

```text
request_id
scope
op
args
```

`OperationScope::System` is for manager-scoped operations. Runtime operations
must use `OperationScope::Sandbox { sandbox_id }`; the daemon rejects system
scope at its boundary.

Catalogs use `OperationExecutionSpace` through the
`operation_execution_space` JSON field. `OperationFamily` is documentation
grouping only, not a routing selector.

Do not introduce alternate routing envelopes, owner/target fields, or retired
request DTOs.

## Operation Names

Runtime command operations are:

```text
exec_command
write_command_stdin
poll_command
read_command_lines
cancel_command
```

Command identifiers are exposed as `command_session_id`. Keep low-level helper
verbs such as process cancellation private to implementation code; do not expose
short operation names as public API, CLI syntax, file names, or catalog names.

Manager operations are:

```text
create_sandbox
destroy_sandbox
list_sandboxes
inspect_sandbox
start_sandbox_daemon
stop_sandbox_daemon
describe_manager_operations
describe_daemon_operations
```

`describe_daemon_operations` is a manager operation that fetches the selected
sandbox runtime catalog. It is not a package name.

## Dependency Direction

```text
sandbox-gateway-cli -> sandbox-protocol
sandbox-manager -> sandbox-protocol
sandbox-daemon -> sandbox-protocol
sandbox-daemon -> sandbox-runtime
sandbox-runtime -> sandbox-protocol
sandbox-runtime -> sandbox-runtime-command
sandbox-runtime -> sandbox-runtime-workspace
sandbox-runtime-command -> sandbox-runtime-workspace
sandbox-runtime-command -> sandbox-runtime-namespace-process
sandbox-runtime-workspace -> sandbox-runtime-layerstack
sandbox-runtime-workspace -> sandbox-runtime-namespace-process
sandbox-runtime-namespace-process -> sandbox-runtime-overlay
sandbox-runtime-namespace-process -> sandbox-runtime-config
```

The gateway is a protocol client. The manager is the host-side control plane.
The daemon is the in-sandbox runtime endpoint. Runtime support packages remain
separate from the `sandbox-runtime` operation facade.

## Runtime Boundaries

`sandbox-runtime` owns runtime operation specs, dispatch, typed argument
parsing, response projection, command admission, workspace-session
orchestration, and remount coordination.

Support packages own concrete primitives:

- `sandbox-runtime-command`: process launch, PTY, transcript, process group,
  and command request artifacts.
- `sandbox-runtime-workspace`: workspace lifecycle, namespace handles, capture,
  destroy, remount, and launch entries.
- `sandbox-runtime-namespace-process`: `ns-holder`, `ns-runner`, setns command
  execution, DNS setup, and namespace-local overlay work.
- `sandbox-runtime-layerstack`: manifest/layer models, storage, leases,
  snapshots, compaction, and CAS fixtures.
- `sandbox-runtime-overlay`: low-level overlay mount, move, and unmount
  primitives.
- `sandbox-runtime-config`: YAML loading, merging, typed schemas, and
  validation.

Keep `command-request.json` until an explicit replacement transport exists.

## Packaging

The daemon package and binary are both `sandbox-daemon`.

Expected packaging shape:

```text
cargo build -p sandbox-daemon --target <target> --profile <profile>
target/<target>/<profile-dir>/sandbox-daemon
dist/sandbox-daemon-linux-amd64
dist/sandbox-daemon-linux-arm64
dist/sandbox-daemon-linux-amd64.json
dist/sandbox-daemon-linux-arm64.json
```

Do not generate old artifact names or secondary artifact names.

## Verification

Run the narrow package checks after local changes:

```sh
cargo fmt --check --all
cargo check -p sandbox-protocol -p sandbox-runtime -p sandbox-daemon -p sandbox-manager -p sandbox-gateway-cli -p sandbox-runtime-command -p sandbox-runtime-workspace -p sandbox-runtime-namespace-process -p sandbox-runtime-layerstack -p sandbox-runtime-overlay -p sandbox-runtime-config -p xtask --tests
cargo test -p sandbox-protocol -p sandbox-runtime -p sandbox-daemon -p sandbox-manager -p sandbox-gateway-cli -p sandbox-runtime-command -p sandbox-runtime-workspace -p sandbox-runtime-namespace-process -p sandbox-runtime-layerstack -p sandbox-runtime-overlay -p sandbox-runtime-config -p xtask
cargo clippy -p sandbox-protocol -p sandbox-runtime -p sandbox-daemon -p sandbox-manager -p sandbox-gateway-cli -p sandbox-runtime-command -p sandbox-runtime-workspace -p sandbox-runtime-namespace-process -p sandbox-runtime-layerstack -p sandbox-runtime-overlay -p sandbox-runtime-config -p xtask --all-targets --no-deps -- -D warnings
cargo machete --with-metadata
git diff --check
```

Run stale-name scans before closing cleanup work. Any remaining stale match must
be either deleted or explicitly justified as a current rejection test, current
operation name, or historical prompt text.
