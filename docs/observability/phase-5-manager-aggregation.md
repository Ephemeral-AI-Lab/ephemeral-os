# Phase 5 Manager Observability Aggregation

Phase 5 adds manager-level aggregation for sandbox observability. It is an
architecture boundary, not a broad implementation plan.

The only public manager-facing capability is `get_observability_tree`.
Everything below that is either existing daemon/runtime state production or a
private daemon read path used by manager aggregation.

## Ownership

- The manager owns sandbox lifecycle records: sandbox id, lifecycle state, and
  daemon endpoint metadata. A daemon is eligible for aggregation only when the
  manager record is `ready` and has a daemon endpoint.
- Each sandbox daemon owns its daemon-local observability database. That
  database remains authoritative for stored snapshots, resources, and trace
  summaries for that sandbox.
- `sandbox-runtime` owns only existing in-memory runtime snapshot production. It
  does not own observability storage and does not learn about
  `sandbox-observability`.
- `sandbox-observability` owns the daemon-local typed store abstraction and
  schema. It is not a manager database API.

## Public And Private Boundaries

Public Phase 5 surface:

- `get_observability_tree` on the manager.

Private daemon capability:

- A daemon-local snapshot retrieval branch used by the manager to read one
  sandbox's observability snapshot.

This private daemon capability must not become a new public daemon operation
family. Do not add `CliOperationExecutionSpace::Daemon`, daemon catalog output,
daemon CLI help, protocol help pages, or gateway request-builder expansion for
it. The manager may call the daemon directly through the configured daemon
client, but the user-facing operation surface stays manager-only.

## Resulting Code Shape

Phase 5 should fit the current ownership layout. It should not create a new
cross-cutting observability framework.

Expected manager shape:

```text
crates/sandbox-manager/src/
  daemon_client.rs
  operation/impls/management/
    get_observability_tree.rs
    mod.rs
  operation/specs.rs
```

- `get_observability_tree.rs` owns the public manager operation and aggregation
  policy.
- `management/mod.rs` registers the manager operation with the existing manager
  operation family.
- `operation/specs.rs` remains the manager catalog entry point.
- `daemon_client.rs` is the right boundary for the timeout-capable daemon
  transport capability described below.

Expected daemon shape:

```text
crates/sandbox-daemon/src/
  server/dispatch.rs
  observability/
    service.rs
    cgroup.rs
    disk.rs
    namespace_execution.rs
```

- `server/dispatch.rs` remains the daemon request boundary. It may recognize
  the private snapshot retrieval branch, but that branch must not be registered
  as public CLI/catalog/help metadata.
- `observability/service.rs` owns daemon snapshot assembly from daemon-local
  state and the daemon-owned store.
- Existing sampler helpers stay under `observability/`. Do not add manager
  read paths here.

Expected store shape:

```text
crates/sandbox-observability/src/
  records.rs
  store.rs
```

- `records.rs` remains the typed row boundary for stored observability facts.
- `store.rs` may add narrow daemon-owned reads needed to assemble one aggregate
  snapshot. Prefer one aggregate read surface over many general-purpose public
  helpers.

Files and folders that should not appear for Phase 5:

```text
crates/sandbox-runtime/**/get_observability_tree*
crates/sandbox-runtime/**/get_observability_snapshot*
crates/sandbox-daemon/**/cli*
crates/sandbox-daemon/**/catalog*
crates/sandbox-protocol/**/daemon_execution_space*
```

The exact helper file names can change if implementation pressure proves a
split useful, but the ownership should not: manager aggregation stays in
`sandbox-manager`, daemon snapshot assembly stays in `sandbox-daemon`, and
SQLite reads stay behind `sandbox-observability` store methods used by the
daemon.

## Data Flow

1. A caller invokes manager `get_observability_tree`.
2. The manager reads its sandbox records and selects ready sandboxes, or the
   requested ready subset.
3. For each selected sandbox, the manager calls the daemon's private snapshot
   retrieval capability through that sandbox's daemon endpoint.
4. Each daemon builds its response from daemon-owned state:
   - latest daemon-local sandbox snapshot row,
   - active workspace snapshots,
   - latest sandbox-level resource sample,
   - latest resource sample for each returned workspace,
   - recent trace summaries when requested.
5. The manager combines successful daemon snapshots with unavailable or partial
   nodes for daemon failures.

The manager never opens, mirrors, copies, migrates, compacts, or queries daemon
SQLite files. It also must not hold raw SQLite connections or issue raw SQL
against daemon storage.

## Snapshot Contents

The manager tree is assembled from current snapshots first. History is optional
and secondary.

The daemon snapshot should include these current facts:

- sandbox root: `sandbox_id`, lifecycle state, observability availability, last
  sample time, bounded partial errors, and daemon runtime metadata that is
  already stored as daemon-owned snapshot state;
- workspaces: active workspace id, lifecycle/remount state, profile, last sample
  time, namespace fd count, layer summary, and bounded partial errors;
- active namespace executions: namespace execution id, workspace id, operation
  name, lifecycle state, sample time, and bounded error text;
- latest resources: latest sandbox-level resource sample plus latest
  per-workspace resource sample for each returned workspace;
- recent traces: bounded trace summaries only when requested.

The daemon snapshot may include bounded history only when requested:

- resource samples inside a daemon-capped time window;
- recent trace summaries capped by daemon policy.

The daemon snapshot must not include:

- span rows or span trees,
- command output,
- transcript paths,
- stdin, environment, stdout, or stderr,
- raw request or response payloads,
- raw SQLite row dumps,
- manager-side lifecycle guesses.

The final migrated model uses namespace execution snapshots as the active
execution lane. Do not reintroduce `execution_snapshots` or command-shaped
active execution rows for the manager tree.

## Store Read Boundary

The Phase 5 read surface should stay narrow and daemon-owned. Prefer one
aggregate snapshot read boundary inside the daemon over many public store
helpers. The store may expose typed daemon-internal reads needed to assemble the
snapshot, but the architecture should not turn the store into a general query
library for the manager, gateway, CLI, or external callers.

Raw SQL and raw connections remain outside product code. They are acceptable
only for schema tests, local emergency inspection, or focused debugging.

## State Model

Sandbox lifecycle state and observability availability are different fields.

Lifecycle state comes from manager and daemon lifecycle state, such as
`creating`, `ready`, `stopping`, `stopped`, or `failed`.

Observability availability is only:

- `available`: daemon snapshot retrieval succeeded and required observability
  fields were read.
- `partial`: the daemon responded, but some observability data could not be
  read or collected.
- `unavailable`: the daemon could not be reached, timed out, rejected the
  request, or could not produce a usable snapshot.

Do not encode observability health by setting lifecycle `state =
"unavailable"`. A ready sandbox with a broken observability read should remain
`ready` with `availability = "unavailable"` or `availability = "partial"`.

## Runtime Boundary

`sandbox-runtime` remains a producer of existing runtime snapshots only.
Phase 5 must not add:

- a new runtime operation,
- runtime SQLite access,
- a runtime dependency on `sandbox-observability` or `rusqlite`,
- runtime production LOC for manager aggregation.

If aggregation seems to require runtime changes, the daemon boundary is wrong.
The daemon should consume existing runtime snapshot state and project it into
daemon-owned observability storage.

## Resource Model

Latest resources are defined architecturally as:

- the latest sandbox-level resource sample, where no workspace id is attached,
- plus the latest resource sample for each returned workspace.

Resource history is opt-in, bounded, and secondary. It should not drive the
minimum Phase 5 architecture, and the document does not require index or query
micro-design beyond what is needed to keep bounded reads feasible.

## Trace Model

Recent traces are summaries only. Phase 5 does not expose spans, drilldown
APIs, command output, transcript paths, stdin, env, stdout, stderr, or raw
request/response payloads.

Trace summaries may carry only the minimal identifiers needed for grouping and
display. Do not expose command/session internals unless they are required to
group a summary already present in daemon-owned observability data.

## UI Shape

The UI should render the manager response as a tree, not as raw tables.

Primary tree:

```text
Sandbox
  lifecycle state
  observability availability
  latest sandbox resources
  Workspace
    lifecycle/remount state
    profile
    latest workspace resources
    active namespace executions
  Recent trace summaries
```

Sandbox row:

- show sandbox id, lifecycle state, observability availability, last sample age,
  and latest CPU/memory/disk summary;
- show an availability badge separate from lifecycle state;
- show bounded partial-error text only in an expanded details area;
- show daemon endpoint/runtime path details only as diagnostic details, not as
  primary row content.

Workspace row:

- show workspace id, lifecycle/remount state, profile, last sample age, and
  latest workspace resources;
- show layer count and base manifest/hash as compact details when present;
- show active namespace executions grouped under the owning workspace;
- avoid exposing overlay paths as primary UI content.

Active namespace execution row:

- show operation name, lifecycle state, sample age, and a short execution id
  when needed for correlation;
- do not show command text, command session id, stdin, stdout, stderr, env, or
  transcript path.

Recent trace summary row:

- show operation name, status, start time or age, duration, and bounded error
  summary if present;
- keep trace ids as copyable diagnostic identifiers, not primary hierarchy
  labels;
- do not expose spans or drilldown links until a separate drilldown API exists.

Unavailable or partial daemon row:

- keep the sandbox in the tree when the manager knows the sandbox exists;
- show lifecycle state from manager state;
- show observability availability as `unavailable` or `partial`;
- show the timeout, transport, or read failure as bounded diagnostic text.

## Failure Behavior

Manager fan-out is bounded. A slow or broken daemon must not block the whole
tree indefinitely.

Failure rules:

- One daemon failure becomes one unavailable sandbox node, not a whole-tree
  failure.
- Partial daemon reads become partial nodes with bounded error details.
- Missing resource paths become unavailable resource fields, not failed tree
  aggregation.
- Unknown or non-ready sandbox ids requested explicitly should be reported as
  per-sandbox errors or unavailable nodes, not silently collapsed into a
  successful empty tree.
- The manager response may fail only for manager-owned problems, such as an
  invalid request, an unreadable manager store, or an internal aggregation bug.

## Required Infrastructure Gap

Bounded fan-out requires real per-daemon transport timeouts. The current
`SandboxDaemonClient::invoke` trait is synchronous and has no timeout contract.
Existing gateway and daemon server request-read timeouts do not make manager
fan-out bounded.

Before implementing Phase 5 aggregation, add the smallest concrete daemon-client
transport capability that enforces a per-daemon deadline across connect, write,
shutdown, read, and response decode. A trait-only helper that accepts a timeout
but delegates to the current blocking `invoke` path is not sufficient.

## Non-Goals

Phase 5 must not add:

- manager-side SQLite access or database mirrors,
- daemon SQLite migration or compaction from the manager,
- a public daemon catalog/help/CLI surface,
- new runtime operations or runtime storage dependencies,
- broad operation-framework restructuring,
- exhaustive DTO frameworks in the architecture doc,
- speculative indexes or general query APIs,
- trace drilldown, spans, command output, transcript paths, stdin/env/stdout, or
  stderr in the manager tree.
