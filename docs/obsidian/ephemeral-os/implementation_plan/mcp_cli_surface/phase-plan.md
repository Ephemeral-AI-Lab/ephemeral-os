---
title: MCP, CLI, and daemon HTTP phase plan
tags:
  - ephemeral-os
  - mcp
  - cli
  - http
  - implementation-plan
  - progress-tracking
status: active
updated: 2026-07-10
aliases:
  - MCP CLI HTTP migration tracker
---

# MCP, CLI, and daemon HTTP phase plan

This is the execution and progress-tracking plan for the MCP/CLI/daemon HTTP
boundary migration. It converts [[implementation-spec]] into independently
verifiable phases. The detailed target contracts are [[mcp]], [[cli]], and
[[http]]; the compact cross-boundary catalog is [[operation-contract]].

> [!important] How to use this tracker
> A phase is complete only when every task and every acceptance criterion in
> that phase has evidence. “Compiles locally” is necessary but insufficient.
> Do not start the dependent destructive/removal portion of a later phase
> until its predecessor’s gate is satisfied. Record test commands, commit/PR,
> and exceptions in the phase evidence block when work begins.

## Current progress

| Phase | Status | Dependency | Outcome |
| --- | --- | --- | --- |
| 0. Contract baseline | complete | none | public surface, architecture, and migration constraints documented |
| 1. Catalog and visibility boundary | in progress | 0 | one canonical public catalog with correct names/visibility |
| 2. Consolidate the CLI package | not started | 1 | one package, three separately grantable binaries |
| 3. Add the MCP adapter | not started | 1, 2 | one set-configured stdio server with three registrations |
| 4. Replace export HTTP streaming | not started | 1 | `export_changes` uses authenticated RPC chunk paging only |
| 5. Move console operation callers | not started | 2, 4 | console uses gateway RPC for operations and narrow daemon proxies |
| 6. Enforce daemon HTTP allowlist | not started | 4, 5 | only health, forward, and file list remain direct daemon HTTP |
| 7. Release verification and cutover | not started | 1–6 | end-to-end proof, documentation, and release-ready boundary |

Only Phase 0 is complete: it records the design work already published in
this directory. No production-code migration phase has started.

## Fixed decisions and non-negotiable invariants

Every phase must preserve these decisions. A change requires an explicit
architecture decision and an update to all four contracts, not an incidental
implementation shortcut.

| Invariant | Required state after migration |
| --- | --- |
| Operation source of truth | the three existing operation-catalog crates remain the canonical CLI/MCP definitions; no duplicate MCP operation registry |
| Public sets | exactly `management`, `runtime`, `observability`; no fourth all-operations set/server/binary |
| MCP deployment | one `sandbox-mcp` binary, launched with a fixed `--set`; three separate host registrations/grants |
| CLI deployment | one `sandbox-cli` package with three feature-gated binaries: `sandbox-manager-cli`, `sandbox-runtime-cli`, `sandbox-observability-cli` |
| Workspace lifecycle | `create_workspace_session` and `destroy_workspace_session` remain daemon-internal, not public CLI/MCP operations |
| File listing | `file_list` remains direct `POST /files/list` daemon HTTP only, not a CLI command or MCP tool |
| Public squash name | `squash_layerstacks`; internal daemon operation stays singular `squash_layerstack` |
| Export semantics | public name stays `export_changes`; it exports a published-layer delta, not a full workspace |
| Daemon HTTP | exact allowlist: `GET /health`, `/forward/shared/...`, `/forward/isolated=...`, and `POST /files/list`; all other operation paths are `404` |
| Export transport | manager composes `export_layerstack` plus `read_export_chunk` authenticated RPC; no daemon `/export/*` stream remains |
| Console | regular operations use authenticated console `/api/rpc`; daemon HTTP is used only for health, forward, and list proxying |

## Phase 0 — Contract baseline

**Status:** complete

**Purpose:** Freeze the intended public shape before moving code.

### Completed deliverables

- [x] [[operation-contract]] states the cross-boundary operation catalog.
- [x] [[mcp]] defines the three MCP registrations, tools, schemas, routing,
  target implementation structure, and test expectations.
- [x] [[cli]] defines all binaries, operations, arguments, output semantics,
  package structure, and migration locations.
- [x] [[http]] defines the direct daemon HTTP allowlist, forwarding semantics,
  `POST /files/list`, removal matrix, and console impact.
- [x] [[implementation-spec]] contains the detailed file/LOC budget and
  migration rationale.
- [x] Public `squash_layerstacks`, internal workspace lifecycle, HTTP-only
  `file_list`, and published-delta `export_changes` decisions are recorded.

### Acceptance criteria

- [x] There is one unambiguous answer for the public MCP, CLI, and HTTP
  surface across all linked documents.
- [x] All three public sets and their authority boundaries are named.
- [x] `export_workspace` is explicitly rejected as a misleading name for the
  existing delta-only export implementation.
- [x] The route exception for `POST /files/list` is explicit and consistently
  excluded from MCP/CLI.

### Evidence

- Documentation only; no production code has changed in this phase.

## Phase 1 — Catalog and visibility boundary

**Status:** in progress

**Depends on:** Phase 0

**Purpose:** Make the existing catalog source accurately describe the final
public operation sets before changing clients or HTTP callers.

### Scope and tasks

- [ ] Rename the public manager operation specification from
  `checkpoint_squash` to `squash_layerstacks` in
  `crates/sandbox-manager-operations/src/lib.rs`.
- [ ] Update manager dispatch registration in
  `crates/sandbox-manager/src/operation/cli_definition/management_operations.rs`
  to use the renamed public specification while retaining the internal
  `squash_layerstack` daemon request.
- [ ] Remove `create_workspace_session`, `destroy_workspace_session`, and
  their `workspace_session` family from the public
  `sandbox-runtime-operations` catalog and public exports.
- [ ] Retain daemon workspace lifecycle dispatch in
  `crates/sandbox-runtime/operation/src/cli_definition/workspace_session_operations.rs`,
  but make both entries non-public with `cli: None`.
- [ ] Keep `FILE_LIST_SPEC`/the runtime `FILE_LIST` dispatch entry non-public;
  it remains callable only by daemon HTTP `POST /files/list`.
- [ ] Move the canonical `snapshot` operation specification from
  `sandbox-manager-operations` into
  `sandbox-observability-operations`; manager imports it only to dispatch
  aggregate snapshot work.
- [ ] Update observability catalog command usage/examples to the future
  `sandbox-observability-cli` program name.
- [ ] Rename `crates/sandbox-runtime/operation/src/cli_definition/` to
  `operation_adapter/` and update module paths/tests. This is a naming cleanup
  only: daemon-side request parsing/dispatch stays in the runtime engine.
- [ ] Add catalog-membership tests that encode the final exact public sets.

### Files expected to change

```text
crates/sandbox-manager-operations/src/lib.rs
crates/sandbox-manager/src/operation/cli_definition/management_operations.rs
crates/sandbox-runtime-operations/src/{lib.rs,workspace_session.rs}
crates/sandbox-observability-operations/src/cli_definition/{mod.rs,snapshot.rs,*.rs}
crates/sandbox-runtime/operation/src/{operation.rs,operation_adapter/**}
relevant catalog/operation tests
```

### Acceptance criteria

- [ ] The public management catalog lists exactly
  `create_sandbox`, `destroy_sandbox`, `list_sandboxes`, `inspect_sandbox`,
  `squash_layerstacks`, and `export_changes`; it does not list
  `checkpoint_squash`.
- [ ] The public runtime catalog lists exactly `exec_command`,
  `write_command_stdin`, `read_command_lines`, `file_read`, `file_write`,
  `file_edit`, and `file_blame`.
- [ ] `file_list`, `create_workspace_session`, and
  `destroy_workspace_session` are absent from all public runtime catalog/help
  projections yet remain daemon-dispatchable where required.
- [ ] The public observability catalog lists exactly `snapshot`, `trace`,
  `events`, `cgroup`, and `layerstack`; `snapshot` is not a management tool.
- [ ] An automatic `exec_command` session still has
  `publish_then_destroy` lifecycle behaviour; no regression in command
  execution/session finalization tests.
- [ ] `cargo test -p sandbox-manager-operations -p sandbox-runtime-operations -p sandbox-observability-operations` passes.
- [ ] Focused manager/runtime operation tests pass, including public squash
  forwarding and file-list daemon dispatch.

### Evidence to record

```text
Commit/PR: pending Phase 1 completion
Commands: pending implementation and direct acceptance proof
Catalog test output: pending
Known deviations/waivers: none
```

## Phase 2 — Consolidate the CLI package

**Status:** not started

**Depends on:** Phase 1

**Purpose:** Replace three CLI packages with one shared package while retaining
three executable and authority boundaries.

### Scope and tasks

- [ ] Add `crates/sandbox-cli` as a workspace package and replace workspace
  references to `sandbox-cli-core`, `sandbox-manager-cli`, and
  `sandbox-runtime-cli`.
- [ ] Move shared gateway/configuration, output, help, and request-building
  code into `crates/sandbox-cli/src/core/`.
- [ ] Move manager client flow to `src/manager.rs` and runtime client flow to
  `src/runtime.rs`; retain their current global flag/scoping behaviour.
- [ ] Extract current manager observability flow into
  `src/observability.rs`; it must not remain a manager subcommand.
- [ ] Add three thin binary entrypoints in `src/bin/`, each calling only its
  own adapter module’s `run_cli`.
- [ ] Add optional Cargo features `manager`, `runtime`, and `observability`.
  Each binary has only its matching `required-features` dependency set.
- [ ] Preserve `sandbox-cli::core` as the only shared import for console/MCP;
  do not force them to enable a CLI-set feature.
- [ ] Move/split manager/runtime smoke tests and add observability CLI smoke
  coverage.
- [ ] Delete old CLI package directories only after consumers compile against
  `sandbox-cli`.

### Files expected to change

```text
Cargo.toml
crates/sandbox-cli/Cargo.toml
crates/sandbox-cli/src/{lib.rs,core/**,manager.rs,runtime.rs,observability.rs,bin/**}
crates/sandbox-cli/tests/{manager.rs,runtime.rs,observability.rs}
crates/sandbox-console/{Cargo.toml,src/**,tests/**}
deleted: crates/sandbox-cli-core/
deleted: crates/sandbox-manager-cli/
deleted: crates/sandbox-runtime-cli/
```

### Acceptance criteria

- [ ] `cargo build -p sandbox-cli --features manager` builds only the manager
  executable path; equivalent builds work for `runtime` and `observability`.
- [ ] `sandbox-manager-cli help` lists only management operations and has no
  `observability` subcommand.
- [ ] `sandbox-runtime-cli --sandbox-id ID help` lists exactly the Phase 1
  runtime catalog and rejects a missing/empty sandbox id before gateway I/O.
- [ ] `sandbox-observability-cli help` lists exactly the five observability
  operations; `snapshot` permits an omitted sandbox id while other views do
  not.
- [ ] All three binaries preserve JSON-line output/error/exit-code behaviour:
  success `0` stdout, operation failure `1` stderr, usage/config failure `2`
  stderr.
- [ ] The console compiles using `sandbox-cli::core` without enabling any
  CLI-set feature.
- [ ] Old CLI crates are absent from workspace members and reverse dependency
  checks; there is no duplicate client/request-builder implementation.
- [ ] `cargo test -p sandbox-cli --all-features` and affected console tests
  pass.

### Evidence to record

```text
Commit/PR:
Commands:
Binary help snapshots:
Old-package dependency audit:
Known deviations/waivers:
```

## Phase 3 — Add the MCP adapter

**Status:** not started

**Depends on:** Phases 1 and 2

**Purpose:** Expose the catalog-defined public sets through one fixed-set MCP
stdio server without duplicating operation semantics.

### Scope and tasks

- [ ] Add `crates/sandbox-mcp` to the workspace with a maintained Rust MCP
  stdio-server library.
- [ ] Implement `--set management|runtime|observability`; reject absent,
  unknown, or caller-supplied per-request set selection.
- [ ] Implement only MCP `initialize`, `notifications/initialized`, `ping`,
  `tools/list`, and `tools/call`.
- [ ] Select exactly one existing catalog for each process; do not create an
  MCP-specific business-operation list.
- [ ] Generate tool descriptions and JSON schemas from `ArgSpec`; add required
  runtime `sandbox_id` and optional observability `snapshot` sandbox selector
  according to [[mcp]].
- [ ] Add value-object request construction in `sandbox-cli::core` so MCP and
  CLI share defaults, validation, operation lookup, and scope construction.
- [ ] Route management/system, runtime/sandbox, aggregate snapshot, and
  sandbox-scoped observability exactly as specified; keep internal `view`
  hidden.
- [ ] Preserve gateway failure `kind`, `message`, and `details` in structured
  MCP tool errors.
- [ ] Add fake-gateway stdio contract tests for all three server registrations.

### Files expected to change

```text
Cargo.toml
crates/sandbox-mcp/Cargo.toml
crates/sandbox-mcp/src/{main.rs,lib.rs,config.rs,catalog.rs,schema.rs,server.rs,tools.rs}
crates/sandbox-mcp/tests/server.rs
crates/sandbox-cli/src/core/request_builder.rs
```

### Acceptance criteria

- [ ] `sandbox-mcp --set management`, `--set runtime`, and
  `--set observability` each start a valid stdio MCP server; an invalid set
  fails before it reads tool calls.
- [ ] `tools/list` outputs the exact Phase 1 operation names for the selected
  set and no names from another set.
- [ ] Runtime MCP schemas require `sandbox_id`; observability schemas require
  it except for aggregate `snapshot`; no schema contains request id, gateway
  token, scope, daemon endpoint, `view`, or export token.
- [ ] MCP tools omit `file_list`, `create_workspace_session`, and
  `destroy_workspace_session`.
- [ ] A fake-gateway `tools/call` proves correct wire request operation and
  scope for management, runtime, aggregate snapshot, and one scoped
  observability view.
- [ ] Invalid values are rejected before gateway dispatch and return the
  standard structured error envelope.
- [ ] Gateway operation failures preserve original error `kind`, `message`,
  and `details` in MCP tool-error content.
- [ ] `cargo test -p sandbox-mcp` passes.

### Evidence to record

```text
Commit/PR:
Commands:
tools/list fixtures:
tools/call routing fixtures:
Known deviations/waivers:
```

## Phase 4 — Replace export HTTP streaming with gateway RPC chunks

**Status:** not started

**Depends on:** Phase 1

**Purpose:** Remove the manager’s dependency on daemon HTTP export streaming
before removing the endpoint itself.

### Scope and tasks

- [ ] Make `export_changes` always start the internal daemon export operation
  and read all bytes via authenticated gateway `read_export_chunk` paging.
- [ ] Retain byte limits, expected-total/completeness checks, cleanup, and
  atomic destination application semantics.
- [ ] Remove manager HTTP export client logic: daemon HTTP URL construction,
  stream token/header selection, response-head parsing, bounded HTTP socket
  reader, and HTTP stream error path.
- [ ] Keep public operation name/output contract unchanged: `export_changes`
  is a published delta, not a full workspace export.
- [ ] Add export tests for normal paging, final chunk, missing/truncated chunk,
  archive result, directory result, and atomic failure behaviour.

### Files expected to change

```text
crates/sandbox-manager/src/operation/management/service/impls/export_changes.rs
crates/sandbox-manager/src/export_apply.rs
crates/sandbox-manager/tests/manager_export.rs
related manager operation/export tests
```

### Acceptance criteria

- [ ] `export_changes` succeeds with no usable `daemon_http` export endpoint
  and without any HTTP export request.
- [ ] Directory export still applies newest-wins/whiteout/opaque published
  delta semantics to the supplied destination.
- [ ] Archive export still emits the documented delta result and byte/file
  metadata.
- [ ] A missing, malformed, or truncated RPC chunk fails before destination
  replacement/application can leave partial visible output.
- [ ] No code in `sandbox-manager` references `/export/`, export stream token
  headers, or daemon HTTP export client helpers.
- [ ] Focused manager export tests pass.

### Evidence to record

```text
Commit/PR:
Commands:
Normal/chunk-failure test evidence:
Search proving HTTP export client removal:
Known deviations/waivers:
```

## Phase 5 — Move console operation callers to gateway RPC

**Status:** not started

**Depends on:** Phases 2 and 4

**Purpose:** Ensure console callers do not keep daemon HTTP operation routes
alive after clients have a canonical gateway path.

### Scope and tasks

- [ ] Change console imports from `sandbox-cli-core` to `sandbox-cli::core`
  without enabling a CLI-set feature.
- [ ] Replace generic `/api/sandboxes/:id/files/:op` proxying with exact,
  read-only `/api/sandboxes/:id/files/list` proxying.
- [ ] Remove `/api/sandboxes/:id/observability/:view` daemon HTTP proxying.
- [ ] Change frontend/API callers for file read/write/edit/blame and all
  observability views to console authenticated `/api/rpc` request envelopes.
- [ ] Retain console daemon HTTP health and forwarding proxy behaviour.
- [ ] Update console catalog/tests to use the canonical three catalogs and
  public operation names.

### Files expected to change

```text
crates/sandbox-console/{Cargo.toml,src/lib.rs,src/router.rs,src/daemon_api.rs}
crates/sandbox-console/src/{catalog.rs,rpc.rs,health.rs,proxy.rs}
console frontend/API caller assets (located during implementation)
crates/sandbox-console/tests/console/{catalog.rs,daemon_api.rs,health.rs,proxy.rs,rpc.rs}
```

### Acceptance criteria

- [ ] Console `/api/rpc` successfully carries a representative runtime file
  call and a representative observability call through the gateway.
- [ ] The console exposes only `files/list` as its daemon file-operation
  proxy; direct console proxy routes for read/write/edit/blame/observability
  are absent or return `404`.
- [ ] Console health and preview forwarding still resolve `daemon_http` and
  preserve existing request/response semantics.
- [ ] Browser-facing code never receives the gateway authentication token.
- [ ] `cargo test -p sandbox-console` passes, including exact route assertions.

### Evidence to record

```text
Commit/PR:
Commands:
Console route matrix:
/api/rpc integration evidence:
Known deviations/waivers:
```

## Phase 6 — Enforce the daemon HTTP allowlist

**Status:** not started

**Depends on:** Phases 4 and 5

**Purpose:** Delete the now-obsolete direct daemon operation routes and leave
only the documented liveness, proxying, and list surface.

### Scope and tasks

- [ ] Reduce `crates/sandbox-daemon/src/http/api.rs` to bounded JSON parsing
  and internal `file_list` dispatch only.
- [ ] Change `http/router.rs` to an exact allowlist: `GET /health`, exact
  `/files/list` handling, and `/forward/...`; all other paths are `404`.
- [ ] Delete `crates/sandbox-daemon/src/http/export.rs` and remove its module
  wiring.
- [ ] Remove unused export stream constants/types from `sandbox-protocol` only
  after a whole-workspace caller search shows no internal references remain.
- [ ] Preserve the standalone HTTP listener and `daemon_http` record metadata
  because health, forward, and list still need them.
- [ ] Preserve forwarding parsing/proxy semantics, including isolated live
  workspace resolution, timeout/status mapping, headers, and upgrades.
- [ ] Update daemon HTTP/console route documentation to declare the breaking
  removal.

### Files expected to change

```text
crates/sandbox-daemon/src/http/{mod.rs,router.rs,api.rs,health.rs,response.rs,server.rs,forward/**}
deleted: crates/sandbox-daemon/src/http/export.rs
crates/sandbox-daemon/tests/**
crates/sandbox-protocol/src/{lib.rs,export_stream.rs}
docs/daemon-http/README.md
README.md
```

### Acceptance criteria

- [ ] `GET /health` returns exact fixed `200` JSON and does not require an
  initialized runtime state.
- [ ] Shared and isolated `/forward/...` routes preserve body/path/query and
  have documented `400`, `403`, `404`, `502`, and `504` error mapping.
- [ ] `POST /files/list` works for root, published snapshot, and a live
  workspace session; bad JSON/body-size/method handling preserves its
  documented `400`/`405` behaviour.
- [ ] `POST /files/read`, `/files/write`, `/files/edit`, `/files/blame`,
  `/observability/snapshot`, `/export/x`, and every other removed operation
  route return `404`.
- [ ] No daemon HTTP `export` module, route prefix, token header, or spool
  stream claim endpoint remains.
- [ ] `cargo test -p sandbox-daemon` and HTTP-focused integration tests pass.

### Evidence to record

```text
Commit/PR:
Commands:
HTTP allowlist/404 test results:
Search proving export-route removal:
Known deviations/waivers:
```

## Phase 7 — Release verification and cutover

**Status:** not started

**Depends on:** Phases 1 through 6

**Purpose:** Prove that the new boundaries work together and that no stale
compatibility path silently preserves the old surface.

### Scope and tasks

- [ ] Update root README, daemon HTTP documentation, CLI guidance, and MCP
  registration example to match the landed behavior.
- [ ] Capture CLI help snapshots for all three binaries and MCP `tools/list`
  snapshots for all three fixed sets.
- [ ] Verify one tool/operation per set against a real or controlled gateway,
  including management, runtime, aggregate observability snapshot, and scoped
  observability view.
- [ ] Run full daemon HTTP allowlist evidence against a real sandbox where
  feasible, including shared/isolated forwarding and list behaviour.
- [ ] Run export end-to-end evidence covering chunk paging and a failure case.
- [ ] Rebuild the Docker sandbox gateway binary using the required command.
- [ ] Run focused crate tests followed by workspace test evidence.
- [ ] Remove temporary compatibility wrappers unless a release policy
  explicitly approves one documented, time-bounded manager-observability
  delegation wrapper.

### Required verification commands

Run the exact subset appropriate to changed crates first, then the workspace
suite. The final gateway rebuild is mandatory for Docker gateway release
evidence.

```sh
cargo test -p sandbox-manager-operations \
  -p sandbox-runtime-operations \
  -p sandbox-observability-operations
cargo test -p sandbox-cli --all-features
cargo test -p sandbox-mcp
cargo test -p sandbox-manager
cargo test -p sandbox-console
cargo test -p sandbox-daemon
bin/start-sandbox-docker-gateway --rebuild-binary
cargo test --workspace
```

If a command is intentionally inapplicable, record why and substitute the
closest focused proof in the evidence block; do not silently omit it.

### Release acceptance criteria

- [ ] CLI help and MCP `tools/list` show exactly their authorized set; no
  principal can enumerate a different set through that surface.
- [ ] Public management uses `squash_layerstacks`; `checkpoint_squash` is not
  accepted as a current public operation.
- [ ] Neither CLI nor MCP exposes workspace create/destroy lifecycle or
  `file_list`.
- [ ] Direct daemon HTTP succeeds only for health, forward, and
  `POST /files/list`; removed operation routes are proven `404`.
- [ ] `export_changes` works via authenticated chunk RPC and its docs/results
  correctly describe a published-layer delta rather than a full workspace.
- [ ] The console does not create an alternate direct daemon operation API.
- [ ] The Docker gateway binary was rebuilt with
  `bin/start-sandbox-docker-gateway --rebuild-binary` after the final source
  change.
- [ ] Focused and workspace test evidence is attached; no unapproved waiver
  remains.

### Evidence to record

```text
Release commit/PR:
CLI help snapshots:
MCP tools/list snapshots:
Daemon HTTP allowlist evidence:
Export paging evidence:
Docker gateway rebuild output:
Focused test commands/results:
Workspace test command/result:
Approved waivers and expiry date:
```

## Progress update protocol

When work lands, update only the relevant phase in this file:

1. Change its status in **Current progress** (`not started` → `in progress` →
   `complete`).
2. Check a task only when its code and direct test are both present.
3. Check an acceptance criterion only when its evidence has been recorded.
4. Put command output summaries, commit/PR, and any approved deviation in the
   phase’s evidence block.
5. Update `updated` frontmatter date and add a short entry below.

### Change log

| Date | Phase | Update | Evidence |
| --- | --- | --- | --- |
| 2026-07-10 | 1 | Started the catalog and visibility boundary after confirming Phase 0 complete and reading all companion contracts. | implementation and direct acceptance proof pending |
| 2026-07-10 | 0 | Created the phase-gated execution tracker from the approved design contracts. | documentation only |

## Related documents

- [[mcp]] — detailed MCP public tools and target adapter structure.
- [[cli]] — detailed CLI commands, package structure, and migration mapping.
- [[http]] — detailed daemon HTTP route/response/removal contract.
- [[operation-contract]] — concise catalog shared by all surfaces.
- [[implementation-spec]] — LOC budget, rationale, and non-goals.
