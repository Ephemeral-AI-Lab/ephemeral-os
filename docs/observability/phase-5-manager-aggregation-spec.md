# Phase 5 Manager Aggregation Detailed Spec

Companion architecture note:
[phase-5-manager-aggregation.md](./phase-5-manager-aggregation.md).

This file is the detailed implementation spec for Phase 5. It is allowed to be
more concrete than the architecture note, but it keeps the same ownership
boundary:

- public surface: manager `get_observability_tree`;
- private daemon surface: one daemon-local snapshot retrieval branch;
- authoritative storage: daemon-local `observability.sqlite`;
- runtime role: producer of existing runtime snapshots only.

## Live Checkout Anchors

The current checkout has these relevant shapes:

- Manager operations live under
  `crates/sandbox-manager/src/operation/impls/management/`.
- Manager operation cataloging is rooted at
  `crates/sandbox-manager/src/operation/specs.rs`.
- Manager sandbox lifecycle state is owned by `SandboxStore`.
- Manager daemon forwarding uses `SandboxDaemonClient::invoke_with_timeout` for
  sandbox-scoped operations, with the protocol default timeout on normal
  forwarding and a Phase 5 timeout on observability fan-out.
- Daemon request dispatch enters through
  `crates/sandbox-daemon/src/server/dispatch.rs`.
- Daemon observability collection and writes live under
  `crates/sandbox-daemon/src/observability/`.
- Stored observability rows are typed in
  `crates/sandbox-observability/src/records.rs` and written by
  `crates/sandbox-observability/src/store.rs`.
- `sandbox_protocol::Response::ok` currently returns the payload directly. Do
  not design this spec around a future `{ result, meta }` response envelope.

## Resulting File And Folder Structure

The implementation should fit this final shape.

```text
docs/observability/
  phase-5-manager-aggregation.md
  phase-5-manager-aggregation-spec.md

crates/sandbox-manager/src/
  daemon_client.rs
  operation/impls/management/
    get_observability_tree.rs
    mod.rs
  operation/specs.rs

crates/sandbox-daemon/src/
  server/dispatch.rs
  observability/
    service.rs
    cgroup.rs
    disk.rs
    namespace_execution.rs

crates/sandbox-observability/src/
  records.rs
  store.rs

crates/sandbox-manager/tests/
  manager_core.rs or observability_tree.rs

crates/sandbox-daemon/tests/unit/
  observability.rs

crates/sandbox-observability/tests/
  schema.rs
```

Expected ownership by file:

- `get_observability_tree.rs`: public manager operation, request parsing,
  selected sandbox discovery, fan-out orchestration, and response assembly.
- `management/mod.rs`: registration of the manager operation in the existing
  manager operation family.
- `daemon_client.rs`: timeout-capable daemon transport boundary. The trait
  should expose a single timeout-aware invoke method, and must not keep a plain
  blocking invoke wrapper.
- `server/dispatch.rs`: private daemon request recognition and routing to the
  daemon observability service. This branch is not cataloged as a daemon CLI
  operation.
- `observability/service.rs`: daemon snapshot assembly from daemon-owned store
  reads and daemon-owned runtime/sampler facts.
- `store.rs`: narrow daemon-owned aggregate read. It should not become a
  manager-facing query API.

Files that should not be created for Phase 5:

```text
crates/sandbox-runtime/**/get_observability_tree*
crates/sandbox-runtime/**/get_observability_snapshot*
crates/sandbox-daemon/**/cli*
crates/sandbox-daemon/**/catalog*
crates/sandbox-protocol/**/daemon_catalog*
crates/sandbox-protocol/**/daemon_help*
```

## Public Manager Operation

Operation name:

```text
get_observability_tree
```

Execution scope:

```text
CliOperationExecutionSpace::Manager
CliOperationScope::System
```

Catalog placement:

- family: `management`;
- related operations: `list_sandboxes`, `inspect_sandbox`;
- CLI visibility: allowed, because this is the one public Phase 5 manager
  capability.

Manager JSON request args:

```json
{
  "sandbox_id": "optional single sandbox id",
  "include_recent_traces": false,
  "trace_limit": 20,
  "resource_window_ms": null
}
```

Argument rules:

- `sandbox_id` absent means every manager-known ready sandbox with a daemon
  endpoint.
- `sandbox_id` present means one sandbox selected by manager id.
- `include_recent_traces` defaults to `false` for cheap polling; UI callers may
  set it to `true`.
- `trace_limit` is forwarded only when supplied; the daemon applies the default
  and cap.
- `resource_window_ms = null` means latest resources only.
- `resource_window_ms = Some(n)` opts into bounded resource history and is
  capped by the daemon.

Current CLI limitation:

The live `ArgKind` supports only string, integer, float, and path. It does not
support booleans or repeated arrays. Therefore the first CLI surface should stay
scalar:

```text
sandbox-cli manager get_observability_tree
sandbox-cli manager get_observability_tree --sandbox-id sbox-1
sandbox-cli manager get_observability_tree --resource-window-ms 60000
sandbox-cli manager get_observability_tree --trace-limit 20
```

If the CLI must expose `include_recent_traces`, add it as a scalar integer flag
such as `--include-recent-traces 1`, or extend the CLI arg framework in a
separate scoped change. Do not use Phase 5 as a broad CLI framework rewrite.

## Private Daemon Snapshot Request

Private operation name:

```text
get_observability_snapshot
```

Execution scope:

```text
CliOperationScope::Sandbox { sandbox_id }
```

This private request may reuse the existing daemon request frame for
manager-generated fan-out calls, but it must not be accepted as a user-facing
raw request. The manager router and gateway path must reject externally supplied
sandbox-scoped `get_observability_snapshot` requests before daemon forwarding.
Hiding the operation from generated help or catalog output is not sufficient by
itself.

It also must not appear in:

- daemon CLI help;
- daemon operation catalog output;
- gateway request-builder generated operation lists;
- a new `CliOperationExecutionSpace::Daemon` variant.

Private daemon JSON request args:

```json
{
  "include_recent_traces": false,
  "trace_limit": 20,
  "resource_window_ms": null
}
```

The daemon must cap `trace_limit` and `resource_window_ms` independently of the
manager. Manager validation is not a trust boundary.

## Store Read Boundary

The daemon should assemble the snapshot through one narrow store read boundary.
The exact Rust names can change, but the shape should be equivalent to this:

```rust
pub struct ObservabilitySnapshotReadOptions {
    pub include_recent_traces: bool,
    pub trace_limit: usize,
    pub resource_window_ms: Option<u64>,
}

pub struct ObservabilitySnapshotRows {
    pub sandbox: Option<ObservabilitySandboxSnapshotRow>,
    pub workspaces: Vec<ObservabilityWorkspaceSnapshotRow>,
    pub active_namespace_executions: Vec<ObservabilityNamespaceExecutionSnapshotRow>,
    pub latest_resources: Vec<ObservabilityResourceSampleRow>,
    pub resource_history: Vec<ObservabilityResourceSampleRow>,
    pub recent_request_traces: Vec<ObservabilityRequestTraceRow>,
    pub recent_namespace_traces: Vec<ObservabilityNamespaceExecutionTraceRow>,
}
```

Rules:

- The `Observability*Row` read DTOs are sanitized projections, not write-side
  storage records. They must omit storage-only fields that are not rendered in
  the snapshot, including raw workspace roots, upper/work dirs, sample ids,
  cgroup paths, command session ids, origin request ids, span fields, and raw
  payloads.
- `latest_resources` contains one latest sandbox-level sample where
  `workspace_id = None`, plus one latest sample per returned workspace id.
- `resource_history` is empty unless `resource_window_ms` is present.
- `recent_request_traces` and `recent_namespace_traces` are empty unless
  `include_recent_traces = true`.
- `SpanRecord` is never returned by the Phase 5 snapshot read.
- Raw `rusqlite::Connection` and raw SQL never leave `store.rs`.
- The manager never imports `sandbox-observability`.
- The aggregate store read may remain all-or-error for SQL/query failures.
  Row-level `error_message` fields and unavailable resource fields produce
  partial observability. A failed required store query makes the daemon snapshot
  unusable for that sandbox.

Avoid adding many public helpers such as:

```text
latest_sandbox_resource()
latest_workspace_resource()
list_recent_traces()
list_namespace_traces()
list_workspace_snapshots()
```

Small private helper functions inside `store.rs` are fine. The exported read
surface should stay aggregate-shaped and daemon-owned.

## Snapshot Inclusion Matrix

The daemon snapshot should map existing row concepts into display-oriented
summary objects.

| Source | Include | Exclude |
| --- | --- | --- |
| `ObservabilitySandboxSnapshotRow` | `sandbox_id`, `state` as lifecycle state, `sampled_at_unix_ms`, bounded `error_message`, daemon runtime metadata for diagnostics | `workspace_root`, raw SQLite details, manager-derived availability |
| `ObservabilityWorkspaceSnapshotRow` | `workspace_id`, `state`, `remount_state`, `profile`, `sampled_at_unix_ms`, `namespace_fd_count`, layer summary, bounded `error_message` | `workspace_root`, `upperdir`, `workdir` |
| `ObservabilityNamespaceExecutionSnapshotRow` | `namespace_execution_id`, `workspace_session_id`, `operation`, `lifecycle_state`, `sampled_at_unix_ms`, bounded `error_message` | command text, command session id, finalizer output |
| `ObservabilityResourceSampleRow` | latest sandbox sample, latest per-workspace sample, optional bounded history, cgroup availability, CPU, memory, disk summary, bounded resource errors | `sample_id`, `cgroup_path`, manager-side aggregation over raw SQLite, unbounded history |
| `ObservabilityRequestTraceRow` | public/sanitized trace id, kind, operation, status, request id, workspace id, start/finish/duration, bounded error summary | span rows, span method names, origin request id, command session id, command text, command output, raw request/response payloads |
| `ObservabilityNamespaceExecutionTraceRow` | trace id, namespace execution id, workspace id, operation, status, exit code, start/finish/duration, bounded error summary | spans, span method names, command text, command session id, transcript path, stdout, stderr |

`SpanRecord` remains out of the Phase 5 manager tree. A later drilldown API can
decide whether spans are needed.

## Manager Response Shape

`Response::ok` should return this object directly:

```json
{
  "sandboxes": [
    {
      "sandbox_id": "sbox-1",
      "lifecycle_state": "ready",
      "availability": "available",
      "sampled_at_unix_ms": 1760000000000,
      "errors": [],
      "daemon": {
        "socket_path": "/.../runtime.sock",
        "pid_path": "/.../daemon.pid",
        "daemon_pid": 12345,
        "runtime_dir": "/..."
      },
      "resources": {
        "latest": {
          "sampled_at_unix_ms": 1760000000000,
          "cgroup": {
            "available": true,
            "cpu_usage_usec": 1000,
            "memory_current_bytes": 1048576,
            "memory_max_bytes": null,
            "memory_max_unlimited": true,
            "error": null
          },
          "disk": {
            "upperdir_bytes": 2048,
            "file_count": 10,
            "dir_count": 2,
            "symlink_count": 0,
            "truncated": false,
            "read_error_count": 0,
            "first_error_path": null
          }
        },
        "history": []
      },
      "workspaces": [
        {
          "workspace_id": "workspace-session-1",
          "lifecycle_state": "active",
          "remount_state": "active",
          "profile": "isolated",
          "sampled_at_unix_ms": 1760000000000,
          "errors": [],
          "layers": {
            "base_manifest_version": 1,
            "base_root_hash": "root-hash",
            "layer_count": 3
          },
          "namespace_fd_count": 4,
          "resources": {
            "latest": {
              "sampled_at_unix_ms": 1760000000000,
              "cgroup": {
                "available": true,
                "cpu_usage_usec": 750,
                "memory_current_bytes": 524288,
                "memory_max_bytes": null,
                "memory_max_unlimited": true,
                "error": null
              },
              "disk": {
                "upperdir_bytes": 1024,
                "file_count": 5,
                "dir_count": 1,
                "symlink_count": 0,
                "truncated": false,
                "read_error_count": 0,
                "first_error_path": null
              }
            },
            "history": []
          },
          "active_namespace_executions": [
            {
              "namespace_execution_id": "namespace-exec-1",
              "operation": "exec_command",
              "lifecycle_state": "running",
              "sampled_at_unix_ms": 1760000000000,
              "error": null
            }
          ]
        }
      ],
      "recent_traces": [
        {
          "trace_id": "trace-1",
          "kind": "request",
          "operation": "exec_command",
          "status": "ok",
          "workspace_id": "workspace-session-1",
          "namespace_execution_id": null,
          "request_id": "request-1",
          "started_at_unix_ms": 1760000000000,
          "finished_at_unix_ms": 1760000000020,
          "duration_ms": 20.0,
          "error_kind": null,
          "error_message": null
        }
      ]
    }
  ]
}
```

Response field rules:

- `lifecycle_state` comes from manager or daemon lifecycle state, not
  observability health.
- `availability` is only `available`, `partial`, or `unavailable`.
- `errors` is always bounded and intended for diagnostics, not primary UI
  labels.
- `daemon` is diagnostic metadata. The UI should not make paths primary.
- `resources.latest` is present when a latest sample exists. It may be `null`
  if the daemon cannot read any resource sample for that scope.
- `resources.history` is empty unless resource history is requested.
- `recent_traces` contains summaries only. It never contains spans.
- The response does not contain command output, command text, command session
  ids, span rows, span method names, transcript paths, stdin, env, stdout,
  stderr, or raw request/response payloads.

## Availability Rules

Sandbox node availability is derived after manager fan-out:

```text
available
  daemon responded and required snapshot sections were read

partial
  daemon responded with a usable root snapshot, but stored row-level partial
  errors or unavailable optional resource/sampler fields exist

unavailable
  daemon was unreachable, timed out, returned malformed data, rejected the
  private request, hit a required store read failure, or could not return a
  usable root snapshot
```

Resource-level unavailability is more granular:

- missing cgroup data sets `resources.latest.cgroup.available = false`;
- disk read errors fill disk error fields;
- resource field failures do not automatically make the sandbox lifecycle state
  `unavailable`;
- severe resource read failures may make the sandbox observability availability
  `partial`, but not the sandbox lifecycle state.

Explicitly requested non-ready sandboxes:

- If the manager knows the sandbox, return one node with manager lifecycle state
  and `availability = "unavailable"`.
- If the manager does not know the sandbox id, return an operation error of kind
  `missing_sandbox` or the existing manager equivalent. Do not silently return
  an empty successful tree for an explicit id.

## Fan-Out And Timeout Requirements

Daemon constants for the first implementation:

```text
MAX_CONCURRENT_DAEMON_SNAPSHOT_REQUESTS = 8
DEFAULT_DAEMON_SNAPSHOT_TIMEOUT_MS = 1500
DEFAULT_TRACE_LIMIT = 20
MAX_TRACE_LIMIT = 100
MAX_RESOURCE_WINDOW_MS = 600000
```

The manager must bound concurrent daemon requests. One daemon failure becomes
one unavailable or partial node and must not fail the whole tree.

Transport timeout requirement:

`SandboxDaemonClient::invoke_with_timeout` must enforce one deadline across:

- Unix socket connect;
- request write;
- write shutdown;
- response read;
- response decode.

A trait helper that accepts a timeout but calls a separate blocking invoke path
is not sufficient, and the trait must not provide such a default implementation.

## UI Spec

The UI should treat the manager response as a tree.

Default layout:

```text
Observability
  filter/search row
  sandbox tree
    sandbox row
      workspace rows
        active namespace execution rows
      recent trace summaries
```

Sandbox row primary fields:

- sandbox id;
- lifecycle state badge;
- observability availability badge;
- last sample age;
- latest CPU, memory, and disk summary;
- count of active workspaces;
- count of active namespace executions;
- count of recent trace summaries when requested.

Sandbox row expanded diagnostics:

- bounded errors;
- socket path;
- pid path;
- daemon pid;
- daemon runtime directory;
- resource read diagnostics.

Workspace row primary fields:

- workspace id;
- lifecycle state;
- remount state;
- profile;
- last sample age;
- latest CPU, memory, and disk summary;
- active namespace execution count.

Workspace row expanded diagnostics:

- namespace fd count;
- base manifest version;
- base root hash;
- layer count;
- bounded errors.

Active namespace execution row:

- operation;
- lifecycle state;
- sample age;
- short namespace execution id for copy/correlation;
- bounded error text if present.

Recent trace row:

- operation;
- trace kind;
- status;
- duration;
- start age or timestamp;
- workspace id when present;
- namespace execution id when present;
- short trace id for copy/correlation;
- bounded error summary if present.

UI exclusions:

- no spans;
- no drilldown links until a separate drilldown API exists;
- no command text;
- no command session id as a primary label;
- no command session id in trace summaries;
- no span method names or span identifiers;
- no transcript path;
- no stdin, env, stdout, or stderr;
- no raw SQL or raw row dump display.

Empty states:

- no ready sandboxes: show an empty tree with a manager-level message;
- ready sandbox but daemon unavailable: show the sandbox row with
  `availability = "unavailable"`;
- daemon responded with no active workspaces: show the sandbox row and an empty
  workspace section;
- traces not requested: omit the recent traces section or show it collapsed as
  not loaded, not empty.

## Acceptance Criteria

Implementation must prove these constraints:

- `get_observability_tree` appears in the manager catalog and no daemon catalog
  exists.
- `CliOperationExecutionSpace` still has no `Daemon` variant.
- `sandbox-runtime` has no dependency on `sandbox-observability` or `rusqlite`.
- No runtime production file is added for Phase 5 aggregation.
- The manager never opens `observability.sqlite`.
- The daemon private snapshot branch is callable only through manager-internal
  fan-out; raw public sandbox-scoped `get_observability_snapshot` requests
  through the gateway/manager router are rejected before daemon forwarding.
- The daemon private snapshot branch is not visible in CLI/help/catalog output.
- Store snapshot reads return latest sandbox resources plus latest per-workspace
  resources, not one global latest resource row.
- Resource history is empty unless `resource_window_ms` is requested.
- Recent traces are summaries and contain no spans.
- `Response::ok` returns the manager tree directly; successful responses do not
  introduce `{ "result": ..., "meta": ... }`.
- `SandboxDaemonClient` exposes only timeout-aware daemon invocation, and the
  Unix client enforces the deadline over connect, write, shutdown, read, and
  decode.
- One daemon timeout becomes one unavailable node.
- Row-level partial observability errors become one partial node.
- Required daemon store read failures become one unavailable node, not a
  whole-tree failure.
- Command output, command text, command session ids, span method names,
  transcript paths, stdin, env, stdout, and stderr do not appear in manager tree
  responses.

Suggested verification commands:

```text
rg -n "CliOperationExecutionSpace::Daemon|daemon catalog|daemon help" crates
rg -n "get_observability_tree|get_observability_snapshot" crates/sandbox-runtime
rg -n "sandbox-observability|rusqlite" crates/sandbox-runtime/operation/Cargo.toml
cargo test -p sandbox-protocol responses_preserve_payload_owned_shape
cargo test -p sandbox-manager
cargo test -p sandbox-manager manager_router_rejects_private_observability_snapshot_forwarding
cargo test -p sandbox-daemon observability
cargo test -p sandbox-daemon private_observability_snapshot_dispatch_returns_summary_tree
cargo test -p sandbox-observability
cargo test -p sandbox-runtime runtime_observability_snapshot_keeps_observability_crate_out
```

The exact test names may differ, but the proof must cover manager aggregation,
raw private-request rejection, timeout enforcement, daemon partial/unavailable
failure behavior, direct response shape, store latest-resource selection, trace
summary privacy, and the absence of runtime/storage boundary leaks.
