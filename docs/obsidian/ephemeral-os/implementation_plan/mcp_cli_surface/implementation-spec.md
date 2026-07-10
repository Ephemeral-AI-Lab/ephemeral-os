---
title: MCP and three-set CLI implementation specification
tags:
  - ephemeral-os
  - mcp
  - cli
  - implementation-plan
status: proposed
updated: 2026-07-10
---

# MCP and three-set CLI implementation specification

Companion contract: [[operation-contract]]. Progress tracker: [[phase-plan]].
This plan makes every public
operation available through exactly one of the three CLI and MCP sets, except
the retained read-only daemon `POST /files/list` endpoint. Daemon HTTP is
otherwise limited to `/health` and `/forward`.

## Decisions

1. Keep the existing `CliOperationSpec` catalogs as the single operation
   definition for CLI and MCP. Do not introduce a parallel MCP operation
   registry; retain `file_list` as the deliberate HTTP-only exception and keep
   workspace-session lifecycle operations internal.
2. Replace the separate `sandbox-cli-core`, `sandbox-manager-cli`, and
   `sandbox-runtime-cli` packages with one `sandbox-cli` package. It exposes
   three feature-gated binary targets: `sandbox-manager-cli`,
   `sandbox-runtime-cli`, and `sandbox-observability-cli`.
3. Add one `sandbox-mcp` binary, parameterized by `--set`. Deploy it as three
   separate MCP server registrations. This produces three grants without
   maintaining three adapters.
4. Use the existing `read_export_chunk` RPC path for `export_changes`. Delete
   the token-gated daemon HTTP export stream. Reintroduce streaming only if a
   benchmark proves RPC chunking unacceptable; it must still not create a
   daemon HTTP operation route.
5. The browser console continues to use its authenticated `/api/rpc` endpoint
   for operations. Its HTTP endpoints that proxy daemon files or observability
   are removed except the exact read-only `/api/sandboxes/:id/files/list`
   proxy. The console still proxies daemon health/forward traffic, which are
   the other permitted daemon HTTP routes.
6. `sandbox-runtime` retains daemon-side request parsing and operation
   dispatch. Rename its misleading `cli_definition` directory to
   `operation_adapter`; no client-side CLI code lives in the runtime engine.

## Target structure

```text
operation specs (existing three catalog crates)
              │
  ┌───────────┼─────────────────┐
  │           │                 │
sandbox-cli  sandbox-mcp   console /api/rpc
(three bins) │                 │
  │           │                 │
  └───────────┴── authenticated gateway RPC ──┐
                                                │
                                  manager / per-sandbox daemon
                                                │
             daemon HTTP: /health, /forward, and POST /files/list only
```

The target public executables are:

| Set | Executable / `sandbox-cli` feature | Catalog | Authority |
| --- | --- | --- | --- |
| management | `sandbox-manager-cli` / `manager` | `sandbox-manager-operations` | lifecycle and host export |
| runtime | `sandbox-runtime-cli` / `runtime` | `sandbox-runtime-operations` | one explicitly selected sandbox |
| observability | `sandbox-observability-cli` / `observability` | `sandbox-observability-operations` | read-only diagnostics |
| MCP adapter | `sandbox-mcp --set SET` | one of the above | identical to its selected set |

## Implementation phases

### 1. Complete the catalogs and consolidate CLI presentation

- Create one `crates/sandbox-cli` package. It contains `core/` (the current
  transport, config, request-building, and output code), `manager.rs`,
  `runtime.rs`, `observability.rs`, and three `src/bin/` entrypoints. Do not
  keep a second `sandbox-cli-core` package or wrapper CLI packages.
- Give `sandbox-cli` optional `manager`, `runtime`, and `observability`
  features. Each feature enables only the corresponding operation-catalog
  dependency. Each `[[bin]]` has `required-features` for its own set; the
  binaries remain separately built and granted even though they share one
  package.
- Move the current `sandbox-manager-cli` and `sandbox-runtime-cli` client
  flows into `sandbox_cli::manager` and `sandbox_cli::runtime`. Extract
  `sandbox_cli::observability` from the manager flow. The binaries only call
  their corresponding module's `run_cli` function.
- Move `sandbox-cli-core` into `sandbox_cli::core`. The console and
  `sandbox-mcp` use only this core module with no set feature enabled; neither
  depends on any CLI set adapter.
- Rename `crates/sandbox-runtime/operation/src/cli_definition/` to
  `operation_adapter/`. Keep its `Request`-to-service dispatch inside
  `sandbox-runtime`; it is not executable CLI implementation. Remove the
  runtime engine's duplicated public CLI-catalog exports once tests use
  `sandbox-runtime-operations::runtime_catalog()` directly.

- Keep `FILE_LIST_SPEC.cli` and the runtime `FILE_LIST` entry non-CLI-visible.
  They remain the implementation behind `POST /files/list`, and must not
  appear in the runtime CLI or MCP catalog.
- Make `create_workspace_session` and `destroy_workspace_session` internal
  runtime operations: remove them from `sandbox-runtime-operations`' public
  families/catalog and set their daemon `OperationEntry` metadata to
  non-CLI-visible. `exec_command` continues to create and finalize its own
  workspace session when a caller does not supply one.
- Move the single `snapshot` specification from
  `sandbox-manager-operations` to `sandbox-observability-operations`.
  `sandbox-manager` imports that spec only to dispatch the aggregate snapshot;
  it no longer owns the public observability catalog.
- Extract the catalog-driven `run_observability` flow from the manager adapter.
  It owns global gateway flags and `sandbox-observability-cli OPERATION
  [flags]` help.
- Remove the observability subcommand and its dependency from
  `sandbox-manager-cli`. This is an intentional command-line compatibility
  break aligned with the new grant boundary.
- Update the observability `CliSpec` usage/examples to the new binary.
- Rename the public management operation `checkpoint_squash` to
  `squash_layerstacks`. It still forwards one internal
  `squash_layerstack` request to the selected sandbox daemon; only the public
  CLI/MCP operation name changes.
- Keep the public name `export_changes`. The implementation exports only the
  published-layer delta above the base and applies or archives that delta; it
  is not a full-workspace export and must not be called `export_workspace`
  unless that behavior changes.

No operation parsing, dispatch, or response rendering is copied: all three
binaries use the one `sandbox-cli` library, whose `core` is shared without
pulling a second operation set into a binary.

### 2. Add the MCP adapter

Add a single `sandbox-mcp` workspace member using a maintained Rust MCP
stdio-server library. Its narrow responsibilities are:

1. `initialize`, `notifications/initialized`, `ping`, `tools/list`, and
   `tools/call` only. Do not add MCP resources, prompts, sampling, or server
   specific business methods.
2. Load exactly one existing catalog based on `--set`.
3. Generate each tool description and JSON-schema properties from that
   catalog's `ArgSpec`s. Add `sandbox_id` to runtime tools and to observability
   tools according to [[operation-contract]].
4. Validate MCP argument values with a new value-based entry point in
   `sandbox-cli::core::request_builder`; that helper and the existing CLI
   argv-based builder share the scope construction and validation path.
5. Send the constructed request through the existing `GatewayClient`, then
   return the unchanged result object as structured tool content. Translate a
   protocol error envelope into an MCP tool error without discarding its
   `kind`, `message`, or `details`.

The process takes the existing gateway socket/token flags (and normal config
discovery) plus `--set management|runtime|observability`. MCP access control
is deployment configuration: register only the selected server process with a
client/principal. The adapter never trusts a caller-supplied set or scope.

### 3. Move console operation callers to RPC

- Replace the generic `/api/sandboxes/:id/files/:op` proxy with the exact
  read-only `/api/sandboxes/:id/files/list` proxy. Remove all other file and
  `/api/sandboxes/:id/observability/:view` daemon-HTTP proxies.
- Update the console frontend/callers to submit the existing protocol request
  shapes to `/api/rpc`; this route already carries authentication, regular
  responses, and SSE progress.
- Retain console health and forward proxy behavior. These resolve
  `daemon_http` only for permitted daemon HTTP routes.

### 4. Make daemon HTTP an allowlist

- Reduce `crates/sandbox-daemon/src/http/api.rs` to the existing `file_list`
  dispatch and request parsing only; delete generic file routing and all
  observability routes.
- Delete `crates/sandbox-daemon/src/http/export.rs` and its token-gated spool
  stream implementation.
- Make `http/router.rs` dispatch exactly `GET /health`, `POST /files/list`,
  and `/forward/...`.
- Simplify `export_changes.rs` to always page `read_export_chunk`; remove the
  HTTP client, stream-token selection, HTTP-head parser, and bounded socket
  reader. Retain byte caps, complete-read checks, and atomic destination
  application semantics.
- Remove export-stream constants and any now-unused token/spool claim surface
  only when no internal caller remains. Do not remove the daemon HTTP listener
  or `daemon_http` endpoint metadata: health, forward, and file listing still
  use them.

### 5. Test and cut over

- Unit-test catalog membership: no operation appears in two public sets;
  `file_list`, `create_workspace_session`, and `destroy_workspace_session`
  are absent from runtime CLI and MCP public projections; and `snapshot` is
  observability-visible.
- Add a smoke test for the new observability binary and split the previous
  manager-CLI smoke coverage accordingly.
- MCP contract tests run `tools/list` for each `--set`, assert the exact tool
  names/input-requiredness, then `tools/call` one harmless operation through a
  fake gateway.
- Daemon HTTP tests assert `GET /health`, each forward route, and
  `POST /files/list` still work, while `/files/read`,
  `/observability/snapshot`, and `/export/x` return `404`.
- Test `export_changes` through chunk paging, including a truncated/missing
  chunk failure, before deleting stream-path code.
- Update README and daemon HTTP documentation to point users to MCP/CLI and
  state the breaking endpoint removal.

## File and folder changes

Counts are planning estimates for production and direct tests, not generated
code. A negative number is deletion. The implementation should be reviewed
against this budget; exceed it only for a demonstrated protocol requirement.

| Path | Change | Estimated LOC |
| --- | --- | ---: |
| `Cargo.toml` | replace the three old CLI members/dependencies with `sandbox-cli`; add `sandbox-mcp` | +6 / -7 |
| `crates/sandbox-cli/Cargo.toml` | one package, optional set dependencies, and three feature-gated `[[bin]]` targets | +45 |
| `crates/sandbox-cli/src/core/{client.rs,output.rs,request_builder.rs,mod.rs}` | relocate current shared CLI core; add value-based request validation | move 694; +65 |
| `crates/sandbox-cli/src/{lib.rs,manager.rs,runtime.rs,observability.rs,bin/*.rs}` | relocate manager/runtime clients, extract observability, and add thin binary targets | move 400; +120 to +165 |
| `crates/sandbox-cli/tests/{manager.rs,runtime.rs,observability.rs}` | relocate existing smoke tests and split observability coverage | move 248; +120 |
| `crates/sandbox-{cli-core,manager-cli,runtime-cli}/` | delete after the relocation to `sandbox-cli` | move -1,342 |
| `crates/sandbox-manager-operations/src/lib.rs` | remove the duplicate public `snapshot` spec | -24 |
| `crates/sandbox-runtime-operations/src/{lib.rs,workspace_session.rs}` | remove workspace-session lifecycle from the public runtime catalog | -70 to -95 |
| `crates/sandbox-runtime/operation/src/{operation.rs,cli_definition/workspace_session_operations.rs}` | retain lifecycle dispatch but mark it non-public; rename `cli_definition` to `operation_adapter` | +8 / -18 |
| `crates/sandbox-observability-operations/src/cli_definition/{mod.rs,snapshot.rs}` | own canonical snapshot spec/catalog | +20 / -8 |
| `crates/sandbox-manager/Cargo.toml` | depend on observability-operation spec | +1 |
| `crates/sandbox-manager/src/operation/{cli_definition/management_operations.rs,management/service/impls/checkpoint_squash.rs}` | import canonical snapshot spec and rename public squash dispatch to `squash_layerstacks` | +8 / -8 |
| `crates/sandbox-observability-operations/src/cli_definition/*.rs` | new CLI program names/examples | +10 / -10 |
| `crates/sandbox-console/{Cargo.toml,src,tests}` | switch imports from `sandbox-cli-core` to `sandbox-cli::core` without enabling a set feature | +6 / -6 |
| `crates/sandbox-mcp/Cargo.toml` | new adapter manifest and MCP stdio dependency | +25 |
| `crates/sandbox-mcp/src/{main.rs,lib.rs}` | one set-configured MCP adapter, schemas, tool dispatch | +300 to +360 |
| `crates/sandbox-mcp/tests/server.rs` | tools/list and tools/call contract tests | +180 |
| `crates/sandbox-console/src/{router.rs,daemon_api.rs}` | narrow the daemon-HTTP proxy to `files/list` only | -10 to -15 |
| console frontend/API callers (locate under console asset/client tree) | use `/api/rpc` request envelopes | +40 to +80 |
| `crates/sandbox-daemon/src/http/{router.rs,api.rs,export.rs,mod.rs}` | retain list-only API handler; delete observability and export HTTP code | -230 to -250 |
| `crates/sandbox-daemon` HTTP tests | replace operation-route tests with allowlist tests | -40 / +65 |
| `crates/sandbox-manager/src/operation/management/service/impls/export_changes.rs` | remove HTTP stream path; keep `read_export_chunk` loop | -220 to -260 |
| `crates/sandbox-manager` export tests | remove HTTP stream cases; lock down paged delivery | -40 / +85 |
| `crates/sandbox-protocol/src/{lib.rs,export_stream.rs}` | delete export-stream vocabulary once unused | -20 |
| `README.md`, `docs/daemon-http/README.md`, existing CLI migration docs | public-boundary and migration updates | +70 / -45 |

The `sandbox-cli` consolidation relocates **1,342 existing LOC**; the same
lines count once as additions at their destination and deletions at their
source, so they are excluded from the functional budget. Estimated functional
change is **+1,262 to +1,362 LOC; -825 to -905 LOC; net +357 to +537 LOC.**
The largest new code is the MCP adapter and its contract tests; the retained
list handler reduces, but does not reverse, the HTTP/export deletion. No new
manager/runtime business logic is needed.

## Required removals and caller audit

The following direct daemon HTTP routes are audited before the router
allowlist lands:

| Daemon route | Current owner | Target |
| --- | --- | --- |
| `/files/list` | daemon `http::api`; console daemon API proxy | retain as the one direct daemon HTTP operation route |
| `/files/{read,write,edit,blame}` | daemon `http::api`; console daemon API proxy | runtime catalog via CLI, MCP, or console `/api/rpc` |
| `/observability/{snapshot,trace,events,cgroup,layerstack}` | daemon `http::api`; console daemon API proxy | observability catalog via CLI, MCP, or console `/api/rpc` |
| `/export/{id}` | daemon `http::export`; manager `export_changes` | manager-only `read_export_chunk` RPC composition |

This audit is intentionally route-focused. `/health`, `/forward`, and
`POST /files/list` callers are not migrated because they remain the daemon
HTTP contract.

## Rollout and acceptance criteria

1. Land catalog visibility and the third CLI first; the old manager
   observability command may be retained for one release only as a documented
   delegating compatibility wrapper if a release policy requires it. Do not
   keep it indefinitely, and do not add its tools to the management MCP set.
2. Add and test `sandbox-mcp` against all three catalog sets.
3. Migrate console callers and prove them through `/api/rpc`.
4. Switch export to chunk paging, then remove daemon HTTP operation routes in
   the same change set so no unsupported direct caller survives.
5. Rebuild the Docker gateway binary using
   `bin/start-sandbox-docker-gateway --rebuild-binary`, then run the focused
   CLI, MCP, daemon HTTP, export, and console tests plus workspace `cargo
   test` as release evidence.

The change is accepted only when:

- `tools/list` and CLI help each show the exact set in
  [[operation-contract]];
- only direct `POST /files/list` succeeds among daemon operation routes; all
  other direct daemon HTTP operation requests return `404`;
- health and both forwarding modes still succeed;
- `export_changes` succeeds without `daemon_http` export access and is
  documented/tested as a published-delta export rather than a full-workspace
  export; and
- a principal granted one MCP server or one CLI credential cannot enumerate
  another set through that surface.

## Deliberate non-goals

- No fourth "all operations" MCP server or CLI.
- No MCP resources/prompts, generated SDK, REST replacement, or bespoke JSON
  schema registry.
- No attempt to preserve `/files/read`, `/files/write`, `/files/edit`,
  `/files/blame`, `/observability`, or `/export` HTTP compatibility past
  cutover; `POST /files/list` is the explicit exception.
- No replacement high-throughput export transport until paged RPC is measured
  to be insufficient.
