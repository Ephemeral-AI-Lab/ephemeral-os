# Phase 0 route audit

Legend: `G` = gateway JSONL, `M` = manager application, `D` = authenticated
daemon RPC, `R` = runtime application, `O` = observability handler/application,
and `H` = daemon HTTP. Scope policy names are the target contract values.

| # | Expanded route key | Domain | Scope policy | Visibility | Catalog owner, current → target | Execution owner | Handler owner | Current wire destination | Class |
| ---: | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | `(System, create_sandbox)` | Manager | `System` | Public | `sandbox-manager-operations` → `catalog::manager` | Manager | `sandbox-manager` create handler | `G → M` | Public catalog operation |
| 2 | `(System, destroy_sandbox)` | Manager | `System` | Public | same | Manager | `sandbox-manager` destroy handler | `G → M` | Public catalog operation |
| 3 | `(System, list_sandboxes)` | Manager | `System` | Public | same | Manager | `sandbox-manager` list handler | `G → M` | Public catalog operation |
| 4 | `(System, inspect_sandbox)` | Manager | `System` | Public | same | Manager | `sandbox-manager` inspect handler | `G → M` | Public catalog operation |
| 5 | `(System, squash_layerstacks)` | Manager | `System` | Public | same | Manager | `sandbox-manager` squash handler | `G → M → D squash_layerstack → R` | Public catalog operation |
| 6 | `(System, export_changes)` | Manager | `System` | Public | same | Manager | `sandbox-manager` export handler | `G → M → D export_layerstack/read_export_chunk → R` | Public catalog operation |
| 7 | `(Sandbox, exec_command)` | Runtime | `SandboxRequired` | Public | `sandbox-runtime-operations` → `catalog::runtime` | Runtime | runtime command registry | `G → M forward → D → R` | Public catalog operation |
| 8 | `(Sandbox, write_command_stdin)` | Runtime | `SandboxRequired` | Public | same | Runtime | runtime command registry | `G → M forward → D → R` | Public catalog operation |
| 9 | `(Sandbox, read_command_lines)` | Runtime | `SandboxRequired` | Public | same | Runtime | runtime command registry | `G → M forward → D → R` | Public catalog operation |
| 10 | `(Sandbox, file_read)` | Runtime | `SandboxRequired` | Public | same | Runtime | runtime file registry | `G → M forward → D → R` | Public catalog operation |
| 11 | `(Sandbox, file_write)` | Runtime | `SandboxRequired` | Public | same | Runtime | runtime file registry | `G → M forward → D → R` | Public catalog operation |
| 12 | `(Sandbox, file_edit)` | Runtime | `SandboxRequired` | Public | same | Runtime | runtime file registry | `G → M forward → D → R` | Public catalog operation |
| 13 | `(Sandbox, file_blame)` | Runtime | `SandboxRequired` | Public | same | Runtime | runtime file registry | `G → M forward → D → R` | Public catalog operation |
| 14 | `(System, snapshot)` | Observability | `SystemOrSandbox` | Public | `sandbox-observability-operations` → `catalog::observability` | Manager | manager aggregate snapshot | `G → M`; current fan-out is `D get_observability(view=snapshot) → O` | Public catalog operation |
| 15 | `(Sandbox, snapshot)` | Observability | `SystemOrSandbox` | Public | same | Observability | daemon view → target observability application | current client rewrites to `G → M → D get_observability(view=snapshot) → O` | Public catalog operation |
| 16 | `(Sandbox, trace)` | Observability | `SandboxRequired` | Public | same | Observability | daemon view → target observability application | rewritten to `G → M → D get_observability(view=trace) → O` | Public catalog operation |
| 17 | `(Sandbox, events)` | Observability | `SandboxRequired` | Public | same | Observability | daemon view → target observability application | rewritten to `G → M → D get_observability(view=events) → O` | Public catalog operation |
| 18 | `(Sandbox, cgroup)` | Observability | `SandboxRequired` | Public | same | Observability | daemon view → target observability application | rewritten to `G → M → D get_observability(view=cgroup) → O` | Public catalog operation |
| 19 | `(Sandbox, layerstack)` | Observability | `SandboxRequired` | Public | same | Observability | daemon view → target observability application | rewritten to `G → M → D get_observability(view=layerstack) → O` | Public catalog operation |
| 20 | `(Sandbox, create_workspace_session)` | Runtime | `SandboxRequired` | Internal, currently unenforced | inline runtime literal → `internal::runtime` | Runtime | runtime workspace-session registry | currently `G → M forward → D → R`; trusted direct daemon RPC remains target-capable | Canonical internal application operation |
| 21 | `(Sandbox, destroy_workspace_session)` | Runtime | `SandboxRequired` | Internal, currently unenforced | inline runtime literal → `internal::runtime` | Runtime | runtime workspace-session registry | currently `G → M forward → D → R`; trusted direct daemon RPC remains target-capable | Canonical internal application operation |
| 22 | `(Sandbox, squash_layerstack)` | Runtime | `SandboxRequired` | Internal, currently unenforced | inline runtime + manager literal → `internal::runtime` | Runtime | runtime layerstack registry | normally `M service → D → R`; generic gateway forwarding currently also reaches it | Canonical internal application operation |
| 23 | `(Sandbox, export_layerstack)` | Runtime | `SandboxRequired` | Internal, currently unenforced | inline runtime + manager literal → `internal::runtime` | Runtime | runtime layerstack registry | normally `M service → D → R`; generic gateway forwarding currently also reaches it | Canonical internal application operation |
| 24 | `(Sandbox, read_export_chunk)` | Runtime | `SandboxRequired` | Internal, currently unenforced | inline runtime + manager literal → `internal::runtime` | Runtime | runtime layerstack registry | normally `M service → D → R`; generic gateway forwarding currently also reaches it | Canonical internal application operation |
| 25 | `(Sandbox, get_observability)` | Observability | `SandboxRequired` | Migration-only internal | duplicated literals → `internal::migration` in Phases 2–5, deleted in Phase 6 | Observability | daemon private observability dispatcher | `G`/console/manager aggregate `→ M → D → O` | Canonical internal application operation |
| 26 | `(Sandbox, sandbox_daemon_ready)` | Transport | `SandboxRequired` | Transport-private | provider + daemon literals → `sandbox-protocol::handshake` | N/A | daemon readiness RPC handler | Docker provider `→ D` special dispatch | Transport handshake |
| 27 | `(Sandbox, file_list)` | Runtime | `SandboxRequired` | HTTP-only target exception | `sandbox-runtime-operations::FILE_LIST_SPEC` → `internal::runtime::FILE_LIST` | Runtime | runtime file registry | intended `POST /files/list → H → R`; generic RPC currently also reaches it | Deliberate HTTP-only exception |

Totals: 26 distinct operation names and 27 expanded route keys because
`snapshot` expands to system and sandbox. Every expanded key has exactly one
class: 19 public, 6 canonical internal, 1 transport handshake, and 1
HTTP-only exception.

## Cross-check commands

Declaration/literal audit:

```bash
rg -n --glob '*.rs' \
  'name: "[a-z0-9_]+"|(?:const|static) [A-Z0-9_]*OP[^=]*= "[a-z0-9_]+"|Request::new\("[a-z0-9_]+"|request\.op == [A-Z0-9_]+' \
  crates/sandbox-operations crates/sandbox-manager/src \
  crates/sandbox-runtime/operation/src crates/sandbox-daemon/src \
  crates/sandbox-provider-docker/src
```

Result excerpt: six manager names, five observability names, seven public
runtime names, `file_list`, five other runtime-internal names, and the two
daemon special names. The same search exposes the duplicated
squash/export/observability/readiness literals the migration removes.

Handler registration audit:

```bash
rg -n --glob '*.rs' \
  'OperationEntry\s*\{|ManagerOperationEntry::new|OperationEntry::cli' \
  crates/sandbox-manager/src crates/sandbox-runtime/operation/src
```

Result excerpt: 7 manager entries and 13 runtime entries: 7 public runtime,
`file_list`, 2 workspace-session, and 3 layerstack-internal handlers.

Dispatch-chain audit:

```bash
rg -n --glob '*.rs' \
  'dispatch_request\(|dispatch_operation\(' \
  crates/sandbox-gateway/src crates/sandbox-manager/src \
  crates/sandbox-daemon/src crates/sandbox-runtime/operation/src
```

Result excerpt: gateway → manager router → daemon forwarding/private
dispatch → runtime registry, plus daemon HTTP → runtime dispatch.

Hidden-surface cross-check:

```bash
rg -n 'internal_runtime\(|"create_workspace_session"|"destroy_workspace_session"' \
  cli-operation-e2e-live-test crates/sandbox-mcp/tests crates/sandbox-cli/tests
```

Result excerpt: the live suite invokes both workspace-session operations
through its explicitly internal helper, while CLI and MCP tests prove both
names are absent from public projection.

Transport-only `/health` and `/forward/...` HTTP endpoints were reviewed and
excluded because they do not construct or dispatch an application operation
envelope.

## Baseline findings that affect the target

The audit exposed that `create_workspace_session` and
`destroy_workspace_session` were omitted from the draft target even though
they are dispatchable, hidden operations and the full live suite relies on
their daemon behavior. Phase 0 amends the specification and Phase 2/5 lists
to make them canonical runtime-internal declarations. This preserves direct,
trusted daemon behavior without weakening the target manager-router
visibility chokepoint.

The audit also confirms the documented `file_list` transition: it is meant
to be HTTP-only, but current name-first generic forwarding also makes it
reachable over authenticated gateway/daemon RPC. The target visibility
chokepoints close that unintended generic path.
