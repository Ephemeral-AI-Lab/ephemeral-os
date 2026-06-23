# Phase 2 Runtime Snapshots

Status: draft implementation spec

Parent spec: [sandbox-observability.md](./sandbox-observability.md)

Builds on: [phase-1-observability-foundation.md](./phase-1-observability-foundation.md)

## Phase 2 Goal

Phase 2 turns the Phase 1 local SQLite foundation into live snapshot
population. The deliverable is current-state observability for one sandbox
daemon, written into the existing local database:

```text
<runtime_root>/<sandbox_id>/observability/observability.sqlite
```

Phase 2 populates live state for:

- sandbox root state;
- active workspace state;
- active and recently retained command state;
- sandbox-global resource samples;
- per-workspace resource samples.

The display target after Phase 2 is:

```text
sandbox_id
  state
  resources
  workspace_id
    state
    resources
    active commands
```

Phase 2 does not show recent method chains. Method chains start in Phase 3,
after `OperationTrace` and live request span population exist.

## Current Repo Grounding

This section describes the live checkout this spec is grounded in.

### Phase 1 Foundation

`crates/sandbox-observability` already exists as the SQLite dependency
boundary. Its current public surface is:

- `ObservabilityPaths`;
- `ObservabilityStore`;
- `TraceRecord`;
- `SpanRecord`;
- `SandboxSnapshotRecord`.

`ObservabilityStore` currently keeps migration SQL in
`crates/sandbox-observability/src/store.rs`. There is no
`crates/sandbox-observability/src/schema.rs` file. Phase 2 should extend the
same migration list unless the migration SQL becomes large enough to justify a
local extraction.

The Phase 1 schema currently creates:

- `schema_migrations`;
- `traces`;
- `spans`;
- `sandbox_snapshots`.

Phase 2 adds the missing state tables and upgrades `sandbox_snapshots`; it does
not split the database.

### Daemon Runtime Boundary

`crates/sandbox-daemon/src/server/runtime.rs` defines `ServerConfig` with:

- `socket_path`;
- `pid_path`;
- optional TCP fields;
- optional `auth_token`;
- optional `sandbox_id`.

`SandboxDaemonServer` currently stores:

- `config: ServerConfig`;
- `operations: Arc<SandboxRuntimeOperations>`;
- `shutdown: CancellationToken`.

`crates/sandbox-daemon/src/server/dispatch.rs` validates sandbox scope, then
calls `sandbox_runtime::dispatch_operation(&operations, &request)` inside
`tokio::task::spawn_blocking`.

Phase 2 should add daemon-owned snapshot collection around this existing server
shape. It must not change `sandbox_protocol::Response` and must not require
runtime dispatch signature changes.

### Manager Runtime Directory

`crates/sandbox-manager/src/daemon_install.rs` is the current source of the
per-sandbox daemon runtime directory:

```text
<runtime_root>/<sandbox_id>/
  runtime.sock
  runtime.pid
```

`LocalSandboxDaemonInstaller::launch_spec` passes `--sandbox-id record.id` to
the daemon and derives the socket and pid paths inside that directory. Phase 2
should keep the Phase 1 path rule:

```text
observability_dir = socket_path.parent().join("observability")
database_path = observability_dir.join("observability.sqlite")
```

### Runtime Service Graph

`crates/sandbox-runtime/operation/src/services.rs` defines
`SandboxRuntimeOperations` with:

- `command: Arc<CommandOperationService>`;
- `layerstack: Arc<LayerStackService>`.

`SandboxRuntimeOperations::from_config` constructs:

- `WorkspaceRuntimeService`;
- `WorkspaceSessionService`;
- `LayerStackService`;
- `CommandOperationService`.

Runtime snapshot adapters should follow that graph. The top-level runtime
method can combine snapshots from `command` and the `WorkspaceSessionService`
owned by `command`.

### Workspace State Owner

Workspace sessions are owned by
`crates/sandbox-runtime/operation/src/workspace_session/service/core.rs`:

```rust
pub struct WorkspaceSessionService {
    sessions: Mutex<HashMap<WorkspaceSessionId, WorkspaceSession>>,
    workspace: Arc<WorkspaceRuntimeService>,
}
```

The per-session model is in
`crates/sandbox-runtime/operation/src/workspace_session/service/model.rs`:

- `WorkspaceSession`;
- `WorkspaceSessionHandler`;
- `WorkspaceRemountState` with `Active`, `RemountPending`, and
  `RemountBlocked`.

The runtime workspace crate exposes the stable workspace facts through
`WorkspaceHandle` and `WorkspaceEntry`:

- `WorkspaceSessionId`;
- `WorkspaceProfile`;
- `workspace_root`;
- `BaseRevision`;
- `LayerStackSnapshotRef`;
- `WorkspaceEntry.upperdir`;
- `WorkspaceEntry.workdir`;
- `WorkspaceEntryFds` for user, mount, pid, and optional net namespace fds.

`WorkspaceModeHandle` also has `holder_pid`, `created_at`, and
`last_activity`, but those are not currently present on `WorkspaceHandle`.
Phase 2 should expose them only if a narrow runtime adapter can read the
concrete runtime state without broadening the public workspace model.

### Command State Owner

Command state is owned by
`crates/sandbox-runtime/operation/src/command/service/process_store.rs`:

- `CommandProcessStore.active`;
- `CommandProcessStore.completed`;
- `ActiveCommandProcess`;
- `CompletedCommandRecord`;
- `CommandLifecycleState`;
- `FinalizationState`;
- `CommandWorkspaceOwnership`.

Active command records already have:

- `command_session_id`;
- `workspace_session_id`;
- `workspace_ownership`;
- `workspace_root`;
- `started_at: Instant`;
- `process: Arc<CommandProcess>`;
- `transcript.transcript_path`;
- `lifecycle_state`;
- `finalization`.

`sandbox-runtime-command::CommandProcess` exposes:

- `id()`;
- `command()`;
- `process_group_id()`;
- `transcript_path()`;
- `take_exit()`;
- transcript read helpers.

Completed command records already retain:

- `command_session_id`;
- `workspace_session_id`;
- `started_at: Instant`;
- `result.command_total_time_seconds`;
- retained transcript path;
- finalization state.

Phase 2 should copy these facts out through snapshot DTOs. It must not make the
daemon reach into private command maps directly.

## Non-Goals

Phase 2 must not include:

- `OperationTrace`;
- method span chains;
- `exec_command` method tracing;
- async command finalization traces;
- live population of `trace_links`;
- `get_observability_tree` manager aggregation;
- Prometheus, Grafana, Loki, Tempo, or OTLP;
- a raw SQL query API;
- SQLite writes from `sandbox-runtime`;
- disk walking inside `sandbox-runtime`;
- cgroup file reads inside `sandbox-runtime`;
- command transcript ingestion into observability storage;
- changes to `sandbox_protocol::Response`;
- `{ result, meta }` response envelopes.

Phase 2 may leave `traces` and `spans` in the database from Phase 1, but it must
not add live producers for them.

## Architecture

Phase 2 has one ownership rule:

```text
sandbox-runtime:
  read-only snapshot adapters only

sandbox-daemon:
  snapshot collectors
  resource samplers
  writes to observability.sqlite

sandbox-observability:
  records, schema migrations, store helpers, upsert/insert APIs

sandbox-manager:
  no Phase 2 changes unless a test fixture or type import is truly required
```

The daemon knows `sandbox_id` through `ServerConfig.sandbox_id`. The runtime
owns workspace and command state but does not own sandbox identity. Therefore
runtime snapshot DTOs should not contain `sandbox_id` unless a future runtime
boundary already receives it for another reason.

The daemon collector pipeline is:

```text
SandboxDaemonServer
  ServerConfig.sandbox_id
  ServerConfig.socket_path / pid_path
  SandboxRuntimeOperations::observability_snapshot()
      WorkspaceSessionService snapshot adapter
      CommandProcessStore snapshot adapter
  daemon-owned ResourceSampler
      disk stats from workspace upperdir paths
      cgroup stats from daemon-derived cgroup targets
  ObservabilityStore upsert/insert calls
```

Snapshot collection can run on a timer, after selected requests, or through an
internal test hook. The first implementation should prefer a simple daemon-owned
collector that can be invoked deterministically in tests. If a periodic task is
added, it must be low frequency and cancellation-aware.

## Runtime Snapshot Surface

The runtime-facing surface should expose current state only. It should not
perform observability work.

Recommended top-level shape:

```rust
impl SandboxRuntimeOperations {
    pub fn observability_snapshot(&self) -> RuntimeObservabilitySnapshot;
}
```

Equivalent names are acceptable if they fit the existing module style better,
but the method should be explicit that it is a read-only snapshot. Avoid names
that imply database writes, sampling, telemetry, tracing, or exporting.

Recommended DTOs:

```rust
pub struct RuntimeObservabilitySnapshot {
    pub workspaces: Vec<RuntimeWorkspaceSnapshot>,
    pub active_commands: Vec<RuntimeCommandSnapshot>,
    pub recent_completed_commands: Vec<RuntimeCommandSnapshot>,
    pub partial_errors: Vec<RuntimeSnapshotError>,
}

pub struct RuntimeWorkspaceSnapshot {
    pub workspace_id: WorkspaceSessionId,
    pub state: RuntimeWorkspaceState,
    pub remount_state: RuntimeWorkspaceRemountState,
    pub profile: WorkspaceProfile,
    pub workspace_root: PathBuf,
    pub upperdir: Option<PathBuf>,
    pub workdir: Option<PathBuf>,
    pub holder_pid: Option<i32>,
    pub namespace_fd_count: Option<usize>,
    pub base_manifest_version: Option<i64>,
    pub base_root_hash: Option<String>,
    pub layer_count: Option<usize>,
    pub created_at_unix_ms: Option<i64>,
    pub last_activity_unix_ms: Option<i64>,
}

pub struct RuntimeCommandSnapshot {
    pub command_session_id: CommandSessionId,
    pub workspace_id: WorkspaceSessionId,
    pub command: Option<String>,
    pub lifecycle_state: RuntimeCommandLifecycleState,
    pub finalization_state: RuntimeCommandFinalizationState,
    pub workspace_ownership: RuntimeCommandWorkspaceOwnership,
    pub started_at_unix_ms: Option<i64>,
    pub wall_time_ms: Option<f64>,
    pub command_total_time_ms: Option<f64>,
    pub transcript_path: Option<PathBuf>,
    pub process_group_id: Option<i32>,
}
```

DTOs should live in `sandbox-runtime` rather than `sandbox-observability` so
runtime does not depend on SQLite record types. The daemon maps runtime DTOs to
observability records.

### Workspace Snapshot Rules

`WorkspaceSessionService` should expose a read-only copy-out method colocated
with the session state owner. It should:

- lock `sessions` once;
- copy only bounded scalar/path data;
- sort rows by `workspace_session_id` for stable tests;
- never return handles that allow mutation;
- represent lock poisoning as a snapshot error instead of panicking.

Workspace snapshot field sources:

| Field | Source |
| --- | --- |
| `workspace_id` | `WorkspaceSession.workspace_session_id` |
| `state` | active if present in `sessions`; absent/stale is handled by daemon store cleanup |
| `remount_state` | operation-level `WorkspaceRemountState` |
| `profile` | `WorkspaceHandle.profile` |
| `workspace_root` | `WorkspaceHandle.workspace_root` |
| `upperdir` | `WorkspaceHandle::entry().upperdir` when launch material is available |
| `workdir` | `WorkspaceHandle::entry().workdir` when launch material is available |
| `namespace_fd_count` | count of available `WorkspaceEntryFds` |
| `base_manifest_version` | `WorkspaceHandle.base_revision.version` |
| `base_root_hash` | `WorkspaceHandle.base_revision.root_hash` |
| `layer_count` | `WorkspaceHandle.base_revision.layer_count` |
| `holder_pid` | only if exposed from concrete runtime state without broadening hot paths |
| `created_at_unix_ms` | only if exposed from concrete runtime state |
| `last_activity_unix_ms` | only if exposed from concrete runtime state |

If `WorkspaceHandle::entry()` fails because launch material is incomplete, the
workspace row should still be returned with `upperdir`, `workdir`, and namespace
fd count unset plus a bounded partial error.

### Command Snapshot Rules

`CommandProcessStore` should expose read-only copy-out methods colocated with
the command state owner. It should:

- lock active commands briefly;
- copy active records into DTOs;
- copy recently completed records if retained;
- bound the number of completed command rows returned;
- sort active and completed rows by `command_session_id` for stable tests;
- avoid reading transcript contents.

Command snapshot field sources:

| Field | Source |
| --- | --- |
| `command_session_id` | active/completed command record |
| `workspace_id` | active/completed command record |
| `command` | `CommandProcess::command()` for active commands; optional for completed rows |
| `lifecycle_state` | `CommandLifecycleState` for active commands; terminal state for completed rows |
| `finalization_state` | `FinalizationState` |
| `workspace_ownership` | `CommandWorkspaceOwnership` mapped to `existing_session` or `one_shot` |
| `wall_time_ms` | `started_at.elapsed()` for active commands |
| `command_total_time_ms` | `CompletedCommandRecord.result.command_total_time_seconds * 1000.0` |
| `transcript_path` | active `CommandTranscriptStore` or retained transcript |
| `process_group_id` | `CommandProcess::process_group_id()` for active commands |

The current command model stores `started_at` as `Instant`, not as wall-clock
time. Phase 2 should not add broad wall-clock plumbing just to fill
`started_at_unix_ms`. It can store `started_at_unix_ms = NULL` and
`wall_time_ms` from the sampled instant. If a small future change adds a
wall-clock start time beside `Instant`, the daemon can populate
`started_at_unix_ms` then.

### Top-Level Runtime Method

`SandboxRuntimeOperations::observability_snapshot()` should compose:

- `self.command.workspace().snapshot_workspaces()`;
- `self.command.process_store().snapshot_active_commands()`;
- `self.command.process_store().snapshot_recent_completed_commands(limit)`.

The concrete method names can differ, but the ownership should stay the same:
workspace snapshots come from `WorkspaceSessionService`, command snapshots come
from `CommandProcessStore`, and `SandboxRuntimeOperations` only assembles DTOs.

The runtime method must not:

- open `observability.sqlite`;
- import `sandbox-observability`;
- derive or store `sandbox_id`;
- walk workspace directories;
- read `/sys/fs/cgroup`;
- spawn background sampler tasks;
- emit tracing spans.

## Daemon Collectors

Daemon collectors turn runtime snapshots and daemon-local facts into
`sandbox-observability` records. Each collector should return records plus
bounded health details; the daemon writer decides whether to upsert or insert.

### `SandboxStateSampler`

Input:

- `ServerConfig`;
- daemon process id from `std::process::id()`;
- `ObservabilityPaths`;
- current collector health.

Output:

- `SandboxSnapshotRecord`.

Source of truth:

- `ServerConfig.sandbox_id` for `sandbox_id`;
- `ServerConfig.socket_path` and `pid_path`;
- `socket_path.parent()` for runtime directory;
- daemon process id for `daemon_pid`;
- collector health for `state` and bounded `error_message`.
- workspace root only if daemon integration passes the `serve` workspace root
  into the observability service.

Failure behavior:

- if `sandbox_id` is missing, live observability is disabled and no SQLite
  writes are attempted;
- if path derivation fails, record daemon observability health and continue
  serving runtime requests;
- SQLite write failures are reported as observability health, not request
  failures.

Write type:

- current-row upsert into `sandbox_snapshots`.

### `WorkspaceStateSampler`

Input:

- `sandbox_id`;
- `RuntimeObservabilitySnapshot.workspaces`;
- `sampled_at_unix_ms`.

Output:

- `Vec<WorkspaceSnapshotRecord>`;
- list of currently active workspace ids for stale-row cleanup.

Source of truth:

- runtime DTOs copied from `WorkspaceSessionService`;
- `WorkspaceHandle` and `WorkspaceEntry` fields exposed by the runtime adapter.

Failure behavior:

- lock/snapshot errors become bounded partial errors;
- one bad workspace row should not prevent other workspace rows from being
  written;
- missing `upperdir` or `workdir` produces a partial workspace row and prevents
  disk sampling for that workspace.

Write type:

- current-row upserts into `workspace_snapshots`;
- stale rows for the same `sandbox_id` should be removed or marked absent when
  no longer active.

Recommended stale policy:

- delete workspace rows not present in the current active snapshot, or set
  `state = 'absent'` with a fresh `sampled_at_unix_ms`;
- prefer deletion if no Phase 2 consumer needs historical workspace state;
- keep resource history in `resource_samples` until retention deletes it.

### `CommandStateSampler`

Input:

- `sandbox_id`;
- `RuntimeObservabilitySnapshot.active_commands`;
- `RuntimeObservabilitySnapshot.recent_completed_commands`;
- `sampled_at_unix_ms`.

Output:

- `Vec<CommandSnapshotRecord>`;
- active command ids and retained completed command ids for stale-row cleanup.

Source of truth:

- runtime DTOs copied from `CommandProcessStore`;
- `CommandProcess` for active process group and command text;
- retained `CompletedCommandRecord` for recent terminal state.

Failure behavior:

- command snapshot errors are bounded and do not affect command execution;
- missing transcript paths are allowed;
- missing process group ids are allowed;
- completed command rows are bounded by runtime and/or daemon policy.

Write type:

- current-row upserts into `command_snapshots`;
- delete or mark absent command rows that are no longer active or retained.

Phase 2 display should focus on active commands. Recent completed command rows
are useful for tests and immediate post-exit visibility, but they should not
become a method trace substitute.

### `ResourceSampler`

Input:

- `sandbox_id`;
- runtime workspace snapshots with `upperdir`;
- daemon-derived `CgroupSampleTarget` values;
- sampling cache state.

Output:

- `ResourceSampleRecord` rows for sandbox-global and per-workspace resources.

Source of truth:

- disk: workspace `upperdir` paths exposed by runtime snapshot DTOs;
- cgroup: daemon-side target paths only;
- sampled time: daemon clock.

Failure behavior:

- disk read failures become partial disk fields;
- cgroup missing paths become unavailable cgroup fields;
- expensive sampling is rate-limited and cached;
- sampler failures never fail user operations.

Write type:

- time-series insert into `resource_samples`.

## Resource Sampling

Resource sampling is daemon-owned. `sandbox-runtime` supplies paths and process
identity facts only through snapshot DTOs.

### Disk Rules

Source:

- workspace `upperdir` path exposed by the runtime workspace snapshot.

Collected fields:

- total bytes;
- file count;
- directory count;
- symlink count;
- truncation flag;
- read error count;
- first error path.

Traversal rules:

- use `symlink_metadata` or equivalent so symlink targets are not followed;
- count regular files as files and add their `metadata.len()` to bytes;
- count directories as directories;
- count symlinks as symlinks;
- ignore socket, fifo, device, and unknown file types for counts unless a later
  schema adds explicit fields;
- bound stored path strings before writing to SQLite;
- record `disk_first_error_path` as the first failed path after bounding;
- continue after per-entry read errors when possible;
- set `disk_truncated = 1` when a time, node-count, depth, or byte-budget limit
  stops traversal early.

Rate-limit/caching rules:

- cache disk samples by `(sandbox_id, workspace_id, upperdir)`;
- disk sampling should run less often than cgroup sampling;
- recommended initial minimum interval: 10 seconds per workspace;
- allow tests to force a fresh sample;
- reuse the cached disk fields when the interval has not elapsed;
- do not block runtime request handling on a disk walk.

Failure behavior:

- missing `upperdir` yields a resource sample with disk fields unset and a
  bounded disk error if the schema stores one;
- permission/read errors increment `disk_read_error_count`;
- disk sampler failure must not fail `exec_command`, `read_command_lines`,
  `write_command_stdin`, `squash`, or workspace lifecycle operations.

### Cgroup Rules

Cgroup sampling is daemon-side only. Phase 2 uses an explicit target enum:

```rust
pub type SandboxId = String;
pub type WorkspaceId = String;

pub enum CgroupSampleTarget {
    Sandbox {
        sandbox_id: SandboxId,
        cgroup_path: PathBuf,
    },
    Workspace {
        sandbox_id: SandboxId,
        workspace_id: WorkspaceId,
        cgroup_path: PathBuf,
    },
}
```

Write mapping:

- `CgroupSampleTarget::Sandbox` writes `resource_samples` with
  `workspace_id = NULL`;
- `CgroupSampleTarget::Workspace` writes `resource_samples` with
  `workspace_id` set.

Read cgroup v2 files when present:

- `cpu.stat`;
- `memory.current`;
- `memory.peak`;
- `memory.max`;
- `memory.events`.

Recommended parsed fields:

- `cpu_usage_usec`;
- `cpu_user_usec`;
- `cpu_system_usec`;
- `cpu_nr_periods`;
- `cpu_nr_throttled`;
- `cpu_throttled_usec`;
- `memory_current_bytes`;
- `memory_peak_bytes`;
- `memory_max_bytes`;
- `memory_max_unlimited`;
- `memory_low_events`;
- `memory_high_events`;
- `memory_max_events`;
- `memory_oom_events`;
- `memory_oom_kill_events`;
- `memory_oom_group_kill_events`.

Availability rules:

- missing cgroup path writes `cgroup_available = 0` and bounded
  `cgroup_error`;
- path exists but individual files are missing: leave those fields `NULL`;
- path exists and at least one recognized file is read: write
  `cgroup_available = 1`;
- parse errors set the affected field to `NULL` and record a bounded
  `cgroup_error`;
- `memory.max = "max"` sets `memory_max_bytes = NULL` and
  `memory_max_unlimited = 1`.

Aggregation rules:

- do not synthesize workspace cgroup usage from sandbox-global cgroup usage;
- do not sum workspace cgroups into sandbox total unless the hierarchy
  guarantees that the workspace cgroups are exactly the full child set of the
  sandbox cgroup;
- if workspace processes are not placed in distinct cgroups, write workspace
  cgroup samples as unavailable and keep only sandbox-global cgroup samples.

Target derivation:

- sandbox-global cgroup path must come from daemon/container context, not from
  runtime internals;
- workspace cgroup paths must come from explicit daemon-owned placement data if
  it exists;
- if no explicit workspace cgroup path exists in Phase 2, write unavailable
  workspace cgroup samples rather than guessing from process ids.

## SQLite Writes

Phase 2 writes live data into `observability.sqlite`.

Tables to populate:

- `sandbox_snapshots`;
- `workspace_snapshots`;
- `command_snapshots`;
- `resource_samples`.

Table roles:

- `sandbox_snapshots` is a current-state upsert table;
- `workspace_snapshots` is a current-state upsert table;
- `command_snapshots` is a current-state upsert table for active and retained
  recent commands;
- `resource_samples` is a time-series insert table.

Resource identity:

- `resource_samples.workspace_id IS NULL` means sandbox-global;
- `resource_samples.workspace_id IS NOT NULL` means per-workspace.

Do not split into `sandbox-state.sqlite` in Phase 2. The split is only a later
optimization if write volume or retention proves it useful.

### Phase 2 Schema Additions

The existing Phase 1 migration already creates a minimal
`sandbox_snapshots`. Phase 2 should add a second migration that:

- adds missing `sandbox_snapshots` columns needed for live daemon state;
- creates `workspace_snapshots`;
- creates `command_snapshots`;
- creates `resource_samples`;
- creates the indexes needed by store-level tests and later query APIs.

Recommended table shape:

```sql
-- Add these columns to the Phase 1 sandbox_snapshots table if absent.
-- SQLite migration code can recreate/copy the table if ALTER support is not
-- sufficient for the chosen implementation.
workspace_root TEXT
runtime_dir TEXT
socket_path TEXT
pid_path TEXT
daemon_pid INTEGER

CREATE TABLE IF NOT EXISTS workspace_snapshots (
  sandbox_id TEXT NOT NULL,
  workspace_id TEXT NOT NULL,
  state TEXT NOT NULL,
  remount_state TEXT,
  profile TEXT,
  workspace_root TEXT,
  upperdir TEXT,
  workdir TEXT,
  holder_pid INTEGER,
  namespace_fd_count INTEGER,
  base_manifest_version INTEGER,
  base_root_hash TEXT,
  layer_count INTEGER,
  created_at_unix_ms INTEGER,
  last_activity_unix_ms INTEGER,
  sampled_at_unix_ms INTEGER NOT NULL,
  error_message TEXT,
  PRIMARY KEY(sandbox_id, workspace_id)
);

CREATE TABLE IF NOT EXISTS command_snapshots (
  sandbox_id TEXT NOT NULL,
  workspace_id TEXT NOT NULL,
  command_session_id TEXT NOT NULL,
  command TEXT,
  lifecycle_state TEXT NOT NULL,
  finalization_state TEXT NOT NULL,
  workspace_ownership TEXT NOT NULL,
  started_at_unix_ms INTEGER,
  wall_time_ms REAL,
  command_total_time_ms REAL,
  process_group_id INTEGER,
  transcript_path TEXT,
  sampled_at_unix_ms INTEGER NOT NULL,
  error_message TEXT,
  PRIMARY KEY(sandbox_id, command_session_id)
);

CREATE TABLE IF NOT EXISTS resource_samples (
  sample_id TEXT PRIMARY KEY,
  sandbox_id TEXT NOT NULL,
  workspace_id TEXT,
  sampled_at_unix_ms INTEGER NOT NULL,

  cgroup_path TEXT,
  cgroup_available INTEGER NOT NULL,
  cgroup_error TEXT,

  cpu_usage_usec INTEGER,
  cpu_user_usec INTEGER,
  cpu_system_usec INTEGER,
  cpu_nr_periods INTEGER,
  cpu_nr_throttled INTEGER,
  cpu_throttled_usec INTEGER,

  memory_current_bytes INTEGER,
  memory_peak_bytes INTEGER,
  memory_max_bytes INTEGER,
  memory_max_unlimited INTEGER,
  memory_low_events INTEGER,
  memory_high_events INTEGER,
  memory_max_events INTEGER,
  memory_oom_events INTEGER,
  memory_oom_kill_events INTEGER,
  memory_oom_group_kill_events INTEGER,

  disk_upperdir_bytes INTEGER,
  disk_file_count INTEGER,
  disk_dir_count INTEGER,
  disk_symlink_count INTEGER,
  disk_truncated INTEGER,
  disk_read_error_count INTEGER,
  disk_first_error_path TEXT
);

CREATE INDEX IF NOT EXISTS idx_workspace_snapshots_sandbox
  ON workspace_snapshots(sandbox_id, workspace_id);

CREATE INDEX IF NOT EXISTS idx_command_snapshots_workspace
  ON command_snapshots(sandbox_id, workspace_id);

CREATE INDEX IF NOT EXISTS idx_resource_samples_workspace_time
  ON resource_samples(sandbox_id, workspace_id, sampled_at_unix_ms);

CREATE INDEX IF NOT EXISTS idx_resource_samples_sandbox_time
  ON resource_samples(sandbox_id, sampled_at_unix_ms);
```

### Store APIs

`sandbox-observability` should add row records and direct helpers. Suggested
surface:

```rust
pub struct WorkspaceSnapshotRecord { /* row-shaped */ }
pub struct CommandSnapshotRecord { /* row-shaped */ }
pub struct ResourceSampleRecord { /* row-shaped */ }

impl ObservabilityStore {
    pub fn upsert_sandbox_snapshot(
        &self,
        snapshot: &SandboxSnapshotRecord,
    ) -> Result<(), StoreError>;

    pub fn upsert_workspace_snapshots(
        &self,
        sandbox_id: &str,
        snapshots: &[WorkspaceSnapshotRecord],
    ) -> Result<(), StoreError>;

    pub fn prune_workspace_snapshots(
        &self,
        sandbox_id: &str,
        active_workspace_ids: &[String],
    ) -> Result<(), StoreError>;

    pub fn upsert_command_snapshots(
        &self,
        sandbox_id: &str,
        snapshots: &[CommandSnapshotRecord],
    ) -> Result<(), StoreError>;

    pub fn prune_command_snapshots(
        &self,
        sandbox_id: &str,
        retained_command_session_ids: &[String],
    ) -> Result<(), StoreError>;

    pub fn insert_resource_samples(
        &self,
        samples: &[ResourceSampleRecord],
    ) -> Result<(), StoreError>;
}
```

Store-level read helpers are allowed for tests and later query code, but they
must not become a raw SQL product API. Useful Phase 2 read helpers:

- latest sandbox snapshot by sandbox id;
- active workspace rows by sandbox id;
- command rows by sandbox id and optional workspace id;
- latest resource sample by sandbox id and optional workspace id.

Validation rules:

- bound all ids, states, command text, paths, and error strings;
- validate `resource_samples.workspace_id` semantics;
- make multi-row upserts transactional;
- keep SQLite errors typed and bounded before daemon health reporting.

## Query/API Boundary

Phase 2 should not expose a product query API.

The parent spec places:

- daemon query `get_observability_snapshot`;
- manager query `get_observability_tree`;
- manager fan-out aggregation;

in later API/aggregation work. Phase 2 can prove live population through
store-level reads and focused daemon collector tests.

Therefore Phase 2 should provide:

- daemon-internal collector methods;
- daemon-internal or test-only collection triggers;
- store-level read helpers for tests;
- no public daemon RPC operation;
- no manager operation;
- no raw SQL user-facing API.

No correction to the parent spec is required for Phase 2. A daemon
`get_observability_snapshot` operation would only be required in Phase 2 if the
project wants a user-visible product query immediately after live population.
That would move part of Phase 5 forward and should be documented as an explicit
parent-spec change before implementation.

## File and Folder Structure

Use the current repo layout. Do not invent modules that conflict with existing
files.

### `crates/sandbox-observability`

Existing files:

```text
crates/sandbox-observability/
  src/
    lib.rs
    paths.rs
    records.rs
    store.rs
  tests/
    paths.rs
    schema.rs
```

Expected Phase 2 changes:

```text
crates/sandbox-observability/src/records.rs
  Add WorkspaceSnapshotRecord.
  Add CommandSnapshotRecord.
  Add ResourceSampleRecord.
  Add bounded validation for ids, states, command text, paths, and errors.

crates/sandbox-observability/src/store.rs
  Add phase_2_runtime_snapshots migration.
  Add workspace snapshot upsert/prune helpers.
  Add command snapshot upsert/prune helpers.
  Add resource sample insert helpers.
  Add test-oriented read helpers.

crates/sandbox-observability/src/lib.rs
  Export new record types and any store helper types.

crates/sandbox-observability/tests/schema.rs
  Extend schema idempotence tests for Phase 2 tables.
  Verify synthetic workspace, command, and resource writes.
```

`src/schema.rs` is optional. The current implementation keeps migration SQL in
`store.rs`, and Phase 2 should keep that pattern unless the file becomes hard
to maintain.

### `crates/sandbox-runtime/operation`

Expected Phase 2 additions:

```text
crates/sandbox-runtime/operation/src/observability.rs
  Define RuntimeObservabilitySnapshot and runtime DTOs.

crates/sandbox-runtime/operation/src/services.rs
  Add SandboxRuntimeOperations::observability_snapshot().

crates/sandbox-runtime/operation/src/workspace_session/service/snapshot.rs
  Add WorkspaceSessionService read-only snapshot adapter.

crates/sandbox-runtime/operation/src/workspace_session/service.rs
  Add mod snapshot; keep existing public exports narrow.

crates/sandbox-runtime/operation/src/command/service/snapshot.rs
  Add CommandProcessStore or CommandOperationService read-only snapshot adapter.

crates/sandbox-runtime/operation/src/command/service.rs
  Add mod snapshot; keep command process internals private.

crates/sandbox-runtime/operation/src/lib.rs
  Export only the runtime snapshot DTOs and top-level method required by daemon.
```

Runtime tests should stay focused:

```text
crates/sandbox-runtime/operation/tests/observability_snapshot.rs
```

Those tests should verify copy-out shape from synthetic sessions/commands.
They should not open SQLite, walk disk, or read cgroups.

### `crates/sandbox-daemon`

Expected Phase 2 additions:

```text
crates/sandbox-daemon/src/observability/mod.rs
crates/sandbox-daemon/src/observability/service.rs
crates/sandbox-daemon/src/observability/collectors.rs
crates/sandbox-daemon/src/observability/disk.rs
crates/sandbox-daemon/src/observability/cgroup.rs
crates/sandbox-daemon/src/observability/health.rs
```

If the first implementation is small, `collectors.rs` can hold
`SandboxStateSampler`, `WorkspaceStateSampler`, `CommandStateSampler`, and
`ResourceSampler` before splitting into submodules.

Expected daemon integration points:

```text
crates/sandbox-daemon/src/server/runtime.rs
  Add optional daemon-owned observability service field only when the daemon is
  actually wired to collect and write snapshots.

crates/sandbox-daemon/src/serve.rs
  Derive ObservabilityPaths from ServerConfig.socket_path after sandbox_id is
  known, or leave service disabled if sandbox_id is missing.

crates/sandbox-daemon/src/server/dispatch.rs
  Optionally trigger low-cost snapshot collection after requests, without
  changing response payloads or failing requests on observability errors.
```

Focused daemon tests:

```text
crates/sandbox-daemon/tests/unit/observability.rs
```

or a module under the existing daemon unit test tree.

### `crates/sandbox-manager`

Expected Phase 2 changes:

```text
none
```

Only add manager test fixtures or type imports if daemon integration tests
cannot build otherwise. Do not add `get_observability_tree` in Phase 2.

## LOC Budget

The parent spec currently has an inconsistency:

- the runtime boundary table says Phase 2 runtime LOC: `100-180`;
- the rollout plan says Phase 2 runtime LOC: `140-220`.

Final recommended budget:

```text
sandbox-runtime non-test LOC: 100-180
```

Rationale:

- runtime only needs read-only DTOs and copy-out adapters;
- daemon/observability owns collectors, samplers, SQLite writes, health, and
  retention;
- `sandbox-runtime` must not grow disk walking, cgroup readers, SQLite code,
  writer queues, trace context, or background tasks;
- a higher runtime LOC budget makes it too easy to move daemon responsibilities
  into runtime.

Expected Phase 2 production budget:

```text
crates/sandbox-runtime/operation/src/observability.rs                  30-50
crates/sandbox-runtime/operation/src/services.rs                        5-15
crates/sandbox-runtime/operation/src/workspace_session/service/*       30-50
crates/sandbox-runtime/operation/src/command/service/*                 35-65
crates/sandbox-observability/src/records.rs                            90-150
crates/sandbox-observability/src/store.rs                             160-260
crates/sandbox-daemon/src/observability/*                             220-420
crates/sandbox-daemon integration points                               25-80
crates/sandbox-manager/**                                               0
```

Tests may add more code, especially for SQLite schema and sampler edge cases.
Runtime production code must stay small.

If implementation exceeds `180` non-test runtime LOC, stop and re-check the
boundary. The likely mistake is that runtime is doing observability work instead
of exposing state.

## Failure Policy

Observability is non-critical. Phase 2 failures must not fail:

- `exec_command`;
- `read_command_lines`;
- `write_command_stdin`;
- `squash`;
- workspace session creation;
- workspace session resolution;
- workspace remount;
- workspace destroy/finalization.

Specific rules:

- SQLite write failures become bounded observability health/error state.
- SQLite lock or migration failures disable live writes until the next retry or
  restart, but request handling continues.
- Disk read failures become unavailable or partial disk sample fields.
- Cgroup read failures become unavailable or partial cgroup sample fields.
- Missing cgroup paths write `cgroup_available = 0` and bounded
  `cgroup_error`.
- Missing workspace `upperdir` skips disk sampling for that workspace.
- Stale workspace rows should be removed or marked absent when no longer active.
- Stale command rows should be removed or marked absent when no longer active or
  retained as recent completed commands.
- Observability error strings must be bounded before storage.
- Collector panics should be prevented by ordinary error handling; if a collector
  task crashes, daemon request serving must continue.

## Verification Plan

After Phase 2 implementation, focused checks should include:

```sh
cargo fmt --check
cargo check -p sandbox-observability --tests
cargo test -p sandbox-observability
cargo check -p sandbox-runtime --tests
cargo test -p sandbox-runtime observability_snapshot
cargo check -p sandbox-daemon --tests
cargo test -p sandbox-daemon observability
```

Required behavior tests:

- Phase 2 migration is idempotent.
- `workspace_snapshots`, `command_snapshots`, and `resource_samples` are created
  in `observability.sqlite`.
- Synthetic sandbox snapshot upsert updates the current row.
- Synthetic workspace snapshot upserts rows and prunes stale rows.
- Synthetic command snapshot upserts active/recent rows and prunes stale rows.
- Sandbox-global resource sample writes `workspace_id = NULL`.
- Per-workspace resource sample writes `workspace_id IS NOT NULL`.
- Missing cgroup path writes `cgroup_available = 0`.
- Disk sampler records read errors without failing collection.
- Runtime snapshot tests do not import `sandbox-observability`.
- Daemon collector tests prove SQLite write errors do not change operation
  responses.

## Phase 2 Completion Criteria

Storage:

- [ ] `observability.sqlite` remains the only Phase 2 observability database.
- [ ] `sandbox_snapshots` receives live daemon-root upserts.
- [ ] `workspace_snapshots` receives live active workspace upserts.
- [ ] `command_snapshots` receives live active/recent command upserts.
- [ ] `resource_samples` receives sandbox-global and per-workspace inserts.
- [ ] `resource_samples.workspace_id IS NULL` is used only for sandbox-global
  samples.
- [ ] `resource_samples.workspace_id IS NOT NULL` is used for per-workspace
  samples.

Runtime boundary:

- [ ] `sandbox-runtime` has no SQLite dependency.
- [ ] `sandbox-runtime` has no `sandbox-observability` dependency.
- [ ] `sandbox-runtime` does not know `sandbox_id` unless already provided by
  daemon context for another purpose.
- [ ] `sandbox-runtime` does not walk disk.
- [ ] `sandbox-runtime` does not read cgroups.
- [ ] `sandbox-runtime` does not create `OperationTrace`.
- [ ] `sandbox-runtime` non-test LOC is within `100-180`.

Daemon boundary:

- [ ] daemon collectors own snapshot population.
- [ ] daemon resource samplers own disk and cgroup reads.
- [ ] observability write failures do not fail runtime operations.
- [ ] missing `sandbox_id` disables live observability without failing daemon
  serving.

API boundary:

- [ ] no `get_observability_tree` manager aggregation is added.
- [ ] no public daemon `get_observability_snapshot` operation is added unless
  the parent spec is explicitly revised.
- [ ] no raw SQL query API is exposed.
- [ ] no Prometheus/Grafana/Loki/Tempo/OTLP integration is added.
- [ ] no live method chains or `trace_links` population is added.
