---
title: CLI public surface and implementation design
tags:
  - ephemeral-os
  - cli
  - api
  - implementation-plan
status: proposed
updated: 2026-07-10
aliases:
  - Sandbox CLI API
---

# CLI public surface and implementation design

This document defines the target command-line API and its implementation. The
same public operation definitions project into MCP in [[mcp]]. Direct daemon
HTTP has a deliberately smaller allowlist in [[http]].

> [!important] Boundary rule
> There is one `sandbox-cli` Rust package, but it produces three separately
> installable/executable/grantable binaries. It is not one broad `sandbox`
> command and does not create an all-operations permission boundary.

## Target executables and authority

| Set | Executable | Required selector | Operation catalog | Authority |
| --- | --- | --- | --- | --- |
| management | `sandbox-manager-cli` | none; individual operations accept an id where required | `sandbox-manager-operations` | host sandbox lifecycle, compaction, and published-delta export |
| runtime | `sandbox-runtime-cli` | global `--sandbox-id ID` on every invocation | `sandbox-runtime-operations` | commands/files for exactly one sandbox |
| observability | `sandbox-observability-cli` | `--sandbox-id ID` except aggregate `snapshot` | `sandbox-observability-operations` | read-only diagnostics |

The old command path `sandbox-manager-cli observability ...` is removed. The
new `sandbox-observability-cli` is intentionally a separate executable so it
can be installed or granted without lifecycle authority.

## Common CLI behaviour

### Gateway/configuration flags

Every binary discovers gateway configuration through the existing CLI config
mechanism and accepts these global overrides:

```text
--gateway-socket HOST:PORT
--gateway-auth-token TOKEN
```

`sandbox-runtime-cli` also requires:

```text
--sandbox-id SANDBOX_ID
```

`sandbox-observability-cli` accepts `--sandbox-id` as an operation flag
because `snapshot` may omit it. `sandbox-manager-cli` accepts `--progress` to
render gateway progress on stderr; it is also accepted for `create_sandbox`
for compatibility with the current flow.

The client mints `request_id`, obtains gateway authentication, determines
system/sandbox scope, and performs catalog-driven argument parsing. Those
transport details are never operation flags.

### Help, output, and exits

```text
sandbox-manager-cli help [OPERATION]
sandbox-runtime-cli --sandbox-id ID help [OPERATION]
sandbox-observability-cli help [OPERATION]
```

Successful operation output is one JSON object followed by a newline on
stdout with exit code `0`. Gateway/protocol/operation failures are one JSON
error envelope on stderr with exit code `1`. CLI parsing, missing/invalid
arguments, or configuration discovery failures use the same envelope on
stderr with exit code `2`.

```json
{
  "error": {
    "kind": "invalid_request | config_error | connection_error | operation_failed | ...",
    "message": "human-readable failure",
    "details": {}
  }
}
```

When `--progress` is active, progress lines go to stderr before the final JSON
result. It does not change the result object or exit code. Each binary renders
help from its selected catalog; it cannot enumerate another set.

### Argument conventions

| Notation | Meaning |
| --- | --- |
| `--flag VALUE` | required or optional named value; never a boolean switch unless stated |
| `[--flag VALUE]` | optional named value |
| `COMMAND` / `TEXT` | required positional string; quote it when shell syntax/whitespace is intended |
| `PATH` | absolute host path where noted; otherwise repository-relative or workspace-root-absolute runtime path |
| `ID` | opaque sandbox, workspace-session, or command-session id as indicated |
| `N` / `MS` | unsigned integer; operation-specific ranges below still apply |

## Management CLI

All management calls are system-scoped; a selected `--sandbox-id` is part of
the operation payload, not a global scope selector.

### Operations

| Command | Arguments | Result |
| --- | --- | --- |
| `sandbox-manager-cli create_sandbox --image IMAGE --workspace-bind-root PATH [--count N]` | `--image` required non-empty container-image string; `--workspace-bind-root` required absolute host directory; `--count` optional positive integer, default `1` | one `SandboxRecord` for count 1; otherwise `{ "sandboxes": SandboxRecord[] }` |
| `sandbox-manager-cli destroy_sandbox --sandbox-id ID` | required sandbox id | removed `SandboxRecord` after stop/teardown/registry removal |
| `sandbox-manager-cli list_sandboxes` | none | `{ "sandboxes": SandboxRecord[] }` |
| `sandbox-manager-cli inspect_sandbox --sandbox-id ID` | required sandbox id | `SandboxRecord` |
| `sandbox-manager-cli squash_layerstacks --sandbox-id ID` | required sandbox id | manifest version, compacted blocks, and optional faulty live-session report |
| `sandbox-manager-cli export_changes --sandbox-id ID --dest PATH [--format dir\|tar\|tar-zst]` | required sandbox id; required absolute `--dest`; optional `--format`, default `dir` | published-layer delta export result |

`SandboxRecord` has the stable top-level fields `id`, `workspace_root`, and
`state`, plus optional `daemon`, `daemon_http`, and `shared_base` metadata.
`daemon_http` identifies the daemon HTTP listener only for the limited
endpoints in [[http]]; it does not expose management operations directly.

`squash_layerstacks` is the public name. It forwards the internal,
daemon-local singular operation `squash_layerstack`, which must never appear
in CLI help. A successful result contains:

```json
{
  "manifest_version": 17,
  "squashed_blocks": [
    {
      "squashed_layer_id": "layer-new",
      "replaced_layer_ids": ["layer-a", "layer-b"],
      "replaced_layers": 2,
      "blocked_reasons": []
    }
  ],
  "faulty_sessions": []
}
```

`export_changes` means *published change delta export*, not full workspace
export. It folds published layers above the base using newest-wins,
whiteout/opaque semantics. `dir` applies that delta to an existing directory;
`tar` and `tar-zst` write delta archives. It excludes the base workspace and
unfinalized live workspace-session changes. It returns `manifest_version` and
`layers_exported` in every format, plus per-format file/deletion/whiteout and
byte counts. Do not rename it `export_workspace` until implementation
materializes a complete base-plus-delta workspace and defines live-session
capture semantics.

## Runtime CLI

Every invocation starts with `sandbox-runtime-cli --sandbox-id ID`. There is
no configuration or environment fallback for the selected sandbox.

### Command operations

| Command | Arguments and defaults | Result/semantics |
| --- | --- | --- |
| `exec_command [--workspace-session-id ID] [--timeout-ms N] [--yield-time-ms N] COMMAND` | positional `COMMAND` required; optional existing session; timeout and initial yield are unsigned millisecond values | command result; when no workspace id is supplied, creates an internal `publish_then_destroy` workspace session |
| `write_command_stdin --command-session-id ID [--yield-time-ms N] TEXT` | required command session id and positional text; optional yield | writes to a running command stdin and returns a bounded command result |
| `read_command_lines --command-session-id ID [--start-offset N] [--limit N]` | required command session id; `start_offset` default `0`; `limit` default `200`, range `1..=1000` | stable line-offset command result |

Example:

```sh
sandbox-runtime-cli --sandbox-id sbox-1 exec_command pwd
sandbox-runtime-cli --sandbox-id sbox-1 exec_command --yield-time-ms 0 "sleep 30"
sandbox-runtime-cli --sandbox-id sbox-1 read_command_lines --command-session-id cmd-1 --limit 100
```

A command result includes `status`, optional `exit_code`, timing fields,
line-offset fields, bounded `output`, and when applicable
`command_session_id` / `workspace_session_id`. A running result’s
`command_session_id` is consumed by the other two commands.

An explicit `--workspace-session-id` selects an existing live workspace for a
command. That session lifecycle remains daemon-internal: no
`create_workspace_session` or `destroy_workspace_session` command is public.
Without one, `exec_command` owns creation, publication on terminal completion,
and teardown of its automatic session.

### File operations

| Command | Arguments and defaults | Result/semantics |
| --- | --- | --- |
| `file_read --path FILE [--offset N] [--limit N] [--workspace-session-id ID]` | required path; `offset` default `1`; `limit` default `2000`, range `1..=2000`; optional session | `{ path, content, start_line, num_lines, total_lines, bytes_read, total_bytes, next_offset, truncated }` |
| `file_write --path FILE --content TEXT [--workspace-session-id ID]` | required path/content; optional session | `{ "type": "write", "path": "...", "bytes_written": N }` |
| `file_edit --path FILE --edits JSON [--workspace-session-id ID]` | required path; required JSON array of ordered edits; optional session | `{ "type": "edit", "path": "...", "edits_applied": N, "replacements": N, "bytes_written": N }` |
| `file_blame --path FILE` | required path | `{ "path": "...", "ranges": [{ "start_line": N, "line_count": N, "owner": "..." }] }` |

`--edits` is a JSON array such as:

```sh
sandbox-runtime-cli --sandbox-id sbox-1 file_edit \
  --path notes.txt \
  --edits '[{"old_string":"draft","new_string":"final","replace_all":true}]'
```

Each edit object requires `old_string` and `new_string` and accepts optional
`replace_all`. Edits are applied in order. The old string must be found and
unique unless `replace_all` is true.

With `--workspace-session-id`, reads/writes/edits use that live mounted
workspace. Without it, reads use the published snapshot and writes/edits
publish a new layer attributed to the request id. File paths are
repository-relative or workspace-root-absolute as accepted by the runtime
path validator.

> [!warning] No `file_list` command
> `file_list` stays direct daemon HTTP (`POST /files/list`) and must not occur
> in runtime CLI help, runtime catalog output, or MCP tools. See [[http]].

## Observability CLI

All observability operations are read-only and live under their own binary.

| Command | Arguments and defaults | Result |
| --- | --- | --- |
| `sandbox-observability-cli snapshot [--sandbox-id ID]` | optional target sandbox id | with id: one live `Snapshot`; without id: `{ "sandboxes": Snapshot[] }` for ready manager-known sandboxes |
| `sandbox-observability-cli trace --sandbox-id ID [--trace-id TRACE\|last]` | sandbox required; trace id default `last` | `{ "view": "trace", "trace": ..., "spans": [...] }` |
| `sandbox-observability-cli events --sandbox-id ID [--name NAME] [--since-ms MS] [--last-n N]` | sandbox required; optional exact event name, unix-ms lower bound, and newest-N cap | `{ "view": "events", "events": [...] }` |
| `sandbox-observability-cli cgroup --sandbox-id ID [--scope SCOPE] [--window-ms MS]` | sandbox required; scope default `sandbox` or workspace id; window default `60000`, maximum `600000` | `{ "view": "cgroup", "scope": "...", "series": [...] }` |
| `sandbox-observability-cli layerstack --sandbox-id ID [--workspace-id WS] [--window-ms MS]` | sandbox required; optional workspace and window (default `60000`, maximum `600000`) | published-layer inventory, lease/booking detail, stack series, and optional workspace detail |

The adapter’s routing behaviour is hidden from the user:

- `snapshot` without `--sandbox-id` reaches manager `snapshot` at system
  scope and aggregates ready sandboxes.
- Every selected-sandbox observability command, including `snapshot --sandbox-id`,
  reaches its daemon via internal `get_observability` with the public command
  name converted to an internal `view` argument.

The public single-sandbox snapshot object has stable top-level fields
`sandbox_id`, `lifecycle_state`, `availability`, `sampled_at_unix_ms`,
`errors`, `daemon`, `resources`, `workspaces`, and `stack`. Cgroup samples are
`{ ts, sample_delta_ms, metrics, deltas }`; event/span/layer records preserve
their daemon serialized form.

## Target implementation structure

The package owns presentation and gateway-client code only. All service
behaviour stays in `sandbox-manager`, `sandbox-runtime`, and
`sandbox-observability`/daemon components.

```text
crates/sandbox-cli/
├── Cargo.toml
├── src/
│   ├── lib.rs
│   ├── core/
│   │   ├── mod.rs                  # config types and common public core API
│   │   ├── client.rs               # authenticated gateway JSON-line client
│   │   ├── output.rs               # help, JSON output/error/progress rendering
│   │   └── request_builder.rs      # catalog argv/value parsing + request scope
│   ├── manager.rs                  # `sandbox-manager-cli` adapter
│   ├── runtime.rs                  # `sandbox-runtime-cli` adapter
│   ├── observability.rs            # `sandbox-observability-cli` adapter
│   └── bin/
│       ├── sandbox-manager-cli.rs  # thin Tokio main -> manager::run_cli
│       ├── sandbox-runtime-cli.rs  # thin Tokio main -> runtime::run_cli
│       └── sandbox-observability-cli.rs # thin Tokio main -> observability::run_cli
└── tests/
    ├── manager.rs
    ├── runtime.rs
    └── observability.rs
```

`Cargo.toml` has the package features and binaries below. Each adapter’s
catalog dependency is optional and enabled only in its matching binary.

```toml
[features]
manager = ["dep:sandbox-manager-operations"]
runtime = ["dep:sandbox-runtime-operations"]
observability = ["dep:sandbox-observability-operations"]

[[bin]]
name = "sandbox-manager-cli"
path = "src/bin/sandbox-manager-cli.rs"
required-features = ["manager"]

[[bin]]
name = "sandbox-runtime-cli"
path = "src/bin/sandbox-runtime-cli.rs"
required-features = ["runtime"]

[[bin]]
name = "sandbox-observability-cli"
path = "src/bin/sandbox-observability-cli.rs"
required-features = ["observability"]
```

`sandbox-mcp` and the browser console may depend on `sandbox-cli::core` with
no set feature. They must not import the `manager`, `runtime`, or
`observability` adapter module merely to build a request.

### Current-to-target file migration

| Current location | Target location | Change |
| --- | --- | --- |
| `crates/sandbox-cli-core/src/{client.rs,output.rs,request_builder.rs,lib.rs}` | `crates/sandbox-cli/src/core/` | move shared transport/config/output/request construction; add value-object API for MCP alongside argv API |
| `crates/sandbox-manager-cli/src/lib.rs` | `crates/sandbox-cli/src/manager.rs` | retain manager-only command flow; remove its observability subcommand and catalog dependency |
| `crates/sandbox-runtime-cli/src/lib.rs` | `crates/sandbox-cli/src/runtime.rs` | retain global required sandbox scope and runtime catalog flow |
| manager CLI’s `run_observability` function | `crates/sandbox-cli/src/observability.rs` | extract separate program/global-flag parsing and observability catalog routing |
| `crates/sandbox-{manager,runtime}-cli/src/main.rs` | `crates/sandbox-cli/src/bin/` | replace with three thin binary entry points |
| `crates/sandbox-manager-cli/tests/smoke.rs` | `crates/sandbox-cli/tests/{manager.rs,observability.rs}` | split management and observability tests |
| `crates/sandbox-runtime-cli/tests/smoke.rs` | `crates/sandbox-cli/tests/runtime.rs` | relocate runtime smoke tests |
| `crates/sandbox-{cli-core,manager-cli,runtime-cli}/` | deleted after relocation | remove obsolete packages/members/dependencies |
| `crates/sandbox-runtime/operation/src/cli_definition/` | `crates/sandbox-runtime/operation/src/operation_adapter/` | rename to avoid claiming daemon request dispatch is client CLI implementation |

### Catalog and daemon implementation ownership

| Concern | Canonical source / target change |
| --- | --- |
| management operation specifications | `crates/sandbox-manager-operations/src/lib.rs`; rename `CHECKPOINT_SQUASH_SPEC` public name/usage to `squash_layerstacks` |
| runtime command/file specifications | `crates/sandbox-runtime-operations/src/{command.rs,file.rs}`; retain public command/read/write/edit/blame specs |
| runtime public catalog | `crates/sandbox-runtime-operations/src/lib.rs`; remove the workspace-session family/spec exports; keep `FILE_LIST_SPEC` only as non-CLI HTTP implementation metadata |
| workspace lifecycle dispatch | `crates/sandbox-runtime/operation/src/operation_adapter/workspace_session_operations.rs`; retain dispatch as non-public `OperationEntry { cli: None, ... }` |
| daemon file-list dispatch | `crates/sandbox-runtime/operation/src/operation_adapter/file_operations.rs`; retain non-CLI `FILE_LIST` entry |
| observability specification | `crates/sandbox-observability-operations/src/cli_definition/`; make it canonical for `snapshot` too and change usage examples to `sandbox-observability-cli` |
| manager dispatch registration | `crates/sandbox-manager/src/operation/cli_definition/management_operations.rs`; import canonical observability snapshot spec for aggregate dispatch and public renamed squash spec |

## Required tests and acceptance checks

1. Each binary’s help includes only its set and renders catalog-derived
   requiredness/defaults/examples.
2. `sandbox-manager-cli` has no `observability` subcommand; the third binary
   has all five read-only views.
3. Runtime CLI help/catalog does not include `file_list`,
   `create_workspace_session`, or `destroy_workspace_session`.
4. The manager binary uses system scope; runtime requires a non-empty
   `--sandbox-id`; observability routes aggregate and single-sandbox snapshot
   correctly.
5. Success output is exactly one stdout JSON line; protocol failures are
   stderr JSON and exit `1`; parser/configuration failures are stderr JSON and
   exit `2`.
6. Existing CLI smoke coverage is moved, not lost, and adds coverage for the
   observability binary and renamed `squash_layerstacks` operation.

## Related documents

- [[mcp]] — same public operations exposed as three MCP server grants.
- [[http]] — daemon HTTP is limited to health, forwarding, and HTTP-only file
  listing.
- [[operation-contract]] — concise cross-boundary catalog.
- [[implementation-spec]] — full implementation order and LOC budget.
