# SPEC: Sandbox Workspace Crate Boundaries

Status: **draft v2 (adversarial review + phase gates)**
Date: 2026-06-04
Owner doc: `docs/plans/workspace-crate-boundary_SPEC.md`
Scope: `sandbox/crates/eos-ephemeral-workspace` and
`sandbox/crates/eos-isolated-workspace`

This spec fixes the Rust sandbox workspace crate boundaries before code is moved.
It separates the two workspace policies:

- **Ephemeral workspace**: fresh writable overlay per operation or per command
  session finalization; accepted changes are published to shared truth through
  OCC; scratch is removed after the operation/session.
- **Isolated workspace**: persistent private writable overlay across an explicit
  enter/exit lifecycle; writes are audit-only; scratch is discarded on exit and
  never published.

The current tree has `sandbox/crates/eos-isolated`. That crate should be renamed
to `eos-isolated-workspace`. `eos-ephemeral-workspace` does not exist yet and is
defined here as a new crate.

---

## 1. Goals

1. Make workspace policy visible in crate names.
2. Keep each workspace crate limited to the behavior unique to that workspace
   mode.
3. Keep daemon dispatch, command-session bookkeeping, plugin routing, and JSON
   response envelopes outside both workspace crates.
4. Preserve the isolated no-publish guarantee at the dependency level.
5. Provide concrete file/module/type boundaries and acceptance criteria for the
   extraction.
6. Enforce phase progression: no phase starts until the previous phase's
   acceptance checklist is complete.

## 2. Non-Goals

- No model-facing tool rename.
- No new public isolated workspace id. Routing remains keyed by active
  `agent_id` isolated state.
- No promotion workflow from isolated scratch to shared workspace.
- No command-session crate extraction in this spec.
- No broad rewrite of LayerStack, overlay, runner, or OCC internals.

---

## 3. Terms

| Term | Meaning |
|---|---|
| `main/shared workspace` | The bound repo plus active LayerStack manifest. It is durable shared truth. |
| `ephemeral workspace` | A fresh per-operation writable overlay over a LayerStack snapshot. Writes are captured, published through OCC, then scratch is removed. |
| `isolated workspace` | An explicit per-agent private session. Enter creates persistent scratch; tool calls reuse it; exit discards it. |
| `direct workspace op` | `read_file`, `write_file`, or `edit_file` fast path that can avoid a mounted overlay. It is shared workspace behavior, not ephemeral overlay behavior. |
| `publisher` | The neutral OCC/LayerStack commit path. Ephemeral overlay uses it, but it is also used by direct write/edit and plugin callback paths. |

---

## 4. Crate Naming

### 4.1 Rename isolated crate

Current:

```text
sandbox/crates/eos-isolated
package name: eos-isolated
rust crate: eos_isolated
```

Target:

```text
sandbox/crates/eos-isolated-workspace
package name: eos-isolated-workspace
rust crate: eos_isolated_workspace
```

All imports in `eos-daemon` should move from:

```rust
use eos_isolated::...
```

to:

```rust
use eos_isolated_workspace::...
```

### 4.2 Add ephemeral crate

Target:

```text
sandbox/crates/eos-ephemeral-workspace
package name: eos-ephemeral-workspace
rust crate: eos_ephemeral_workspace
```

The crate owns the fresh overlay transaction policy. It does not own daemon RPC
routing or command-session registry state.

---

## 5. Dependency Boundaries

### 5.1 Allowed dependency direction

```text
eos-daemon
  -> eos-ephemeral-workspace
  -> eos-isolated-workspace
  -> eos-layerstack
  -> eos-occ
  -> eos-overlay
  -> eos-runner
  -> eos-protocol

eos-ephemeral-workspace
  -> eos-overlay
  -> eos-runner
  -> eos-protocol
  -> optional eos-layerstack only if ports become worse than the dependency

eos-isolated-workspace
  -> eos-overlay
  -> Linux networking dependencies
```

Workspace policy crates must not depend on `eos-daemon`.

### 5.2 `eos-isolated-workspace` dependencies

Allowed:

- `eos-overlay`
- `serde_json`
- `thiserror`
- `nix`
- Linux-only networking deps already used by the current crate:
  `futures-util`, `libc`, `netlink-sys`, `rtnetlink`, `tokio`

Forbidden:

- `eos-occ`
- direct publish-layer APIs
- daemon modules
- plugin modules
- command-session registry modules

The absence of `eos-occ` is a build-time no-publish guard.

### 5.3 `eos-ephemeral-workspace` dependencies

Allowed:

- `eos-overlay`
- `eos-runner`
- `eos-protocol`
- `serde`
- `serde_json`
- `thiserror`

Preferred:

- Use local port traits for snapshot acquisition and publishing, then let
  `eos-daemon` inject LayerStack/OCC-backed adapters.

Allowed only if port indirection proves too noisy:

- `eos-layerstack`

Forbidden:

- `eos-daemon`
- `eos-isolated-workspace`
- command-session registry state
- plugin dispatcher state

### 5.4 Publisher placement

Do not move the existing generic `occ_writer` facade wholesale into
`eos-ephemeral-workspace`. It is used by direct workspace ops, plugin callbacks,
plugin overlay, and command overlay finalization. It is not unique to ephemeral
overlay work.

Acceptable placements:

1. Keep the generic publisher in `eos-daemon` for this extraction.
2. Later extract a neutral `eos-workspace-publish` crate.

`eos-ephemeral-workspace` may own the sequence "capture upperdir, call publisher,
assemble ephemeral finalize outcome", but not the generic shared publisher cache
as an ephemeral-only concept.

---

## 6. Target Folder Structure

### 6.1 `eos-isolated-workspace/src`

```text
sandbox/crates/eos-isolated-workspace/src/
├── lib.rs
├── error.rs
├── caps.rs
├── audit.rs
├── network.rs
├── network/
│   ├── netfilter.rs
│   └── rtnl.rs
└── session.rs
    └── session/
        ├── lifecycle.rs
        ├── capacity.rs
        ├── gc.rs
        └── persistence.rs
```

This is mostly the current `eos-isolated/src` structure, renamed.

### 6.2 `eos-ephemeral-workspace/src`

```text
sandbox/crates/eos-ephemeral-workspace/src/
├── lib.rs
├── error.rs
├── types.rs
├── ports.rs
├── dirs.rs
├── runner.rs
├── read_tool.rs
├── capture.rs
├── finalize.rs
├── timings.rs
└── cleanup.rs
```

This structure keeps per-operation overlay behavior in one crate while leaving
daemon routing and generic publishing outside.

---

## 7. `eos-isolated-workspace/src` File Contracts

### 7.1 `lib.rs`

Job:

- Public crate documentation.
- State the no-publish invariant.
- Re-export only the lifecycle, config, audit, network, and error types.

Public exports:

```rust
pub mod audit;
pub mod caps;
pub mod error;
pub mod network;
pub mod session;
```

Re-exported types:

| Type | Source file |
|---|---|
| `AuditSink`, `JsonlAuditSink` | `audit.rs` |
| `ResourceCaps`, `Rfc1918Egress` | `caps.rs` |
| `IsolatedError` | `error.rs` |
| `IsolatedNetwork`, `VethAllocation` | `network.rs` |
| `AgentId`, `WorkspaceHandleId`, `WorkspaceHandle`, `SnapshotLease` | `session.rs` |
| `LayerStackSnapshotPort`, `NamespaceRuntimePort`, `IsolatedSession` | `session.rs` |

No fields live in `lib.rs`.

### 7.2 `error.rs`

Job:

- Define lifecycle/domain errors that daemon code can map into wire responses.

Primary type:

```rust
pub enum IsolatedError {
    FeatureDisabled,
    InvalidArgument(String),
    AlreadyOpen { created_at: f64, last_activity: f64 },
    NotOpen,
    QuotaExceeded { total_cap: u64 },
    SetupFailed { step: String },
    TeardownFailed { step: String },
    CapacityUnavailable { reason: String },
    SnapshotFailed { reason: String },
    LeaseReleaseFailed { lease_id: String, reason: String },
    Io { context: String, source: std::io::Error },
}
```

Exact variants may follow the current implementation, but every variant must map
to one of these semantic categories: disabled, invalid input, lifecycle conflict,
quota/capacity, setup, teardown, snapshot/lease, or I/O.

### 7.3 `caps.rs`

Job:

- Parse and hold isolated lifecycle/resource caps.
- Keep env names and defaults close to the policy they affect.

Primary types and fields:

```rust
pub struct ResourceCaps {
    pub enabled: bool,
    pub ttl_s: f64,
    pub total_cap: u64,
    pub upperdir_bytes_cap: u64,
    pub memavail_fraction_cap: f64,
    pub setup_timeout_s: f64,
    pub exit_grace_s: f64,
    pub rfc1918_egress: Rfc1918Egress,
    pub fallback_dns: String,
    pub workspace_root: String,
}

pub enum Rfc1918Egress {
    Allow,
    Block,
}
```

Required functions:

```rust
impl ResourceCaps {
    pub fn from_env() -> Self;
    pub fn validate(&self) -> Result<(), IsolatedError>;
}
```

### 7.4 `audit.rs`

Job:

- Provide append-only isolated audit records.
- Keep audit separate from OCC publish.

Primary trait:

```rust
pub trait AuditSink {
    fn record(&self, event: serde_json::Value) -> Result<(), IsolatedError>;
}
```

Primary struct and fields:

```rust
pub struct JsonlAuditSink {
    pub path: std::path::PathBuf,
}
```

Required events:

| Event | Required fields |
|---|---|
| `isolated.enter` | `agent_id`, `workspace_handle_id`, `lease_id`, `manifest_version`, `manifest_root_hash`, `created_at` |
| `isolated.tool_write` | `agent_id`, `workspace_handle_id`, `changed_paths`, `changed_path_kinds`, `recorded_at` |
| `isolated.exit` | `agent_id`, `workspace_handle_id`, `lease_id`, `lifetime_s`, `evicted_upperdir_bytes`, `released_lease` |
| `isolated.gc` | `workspace_handle_id`, `reason`, `released_lease`, `removed_scratch` |

### 7.5 `network.rs` and `network/*`

Job:

- Own isolated-only network setup: shared bridge, per-handle veth, NAT/filter,
  and shell-free rtnetlink operations.

Primary types and fields:

```rust
pub struct IsolatedNetwork {
    pub rfc1918_egress: Rfc1918Egress,
    pub pool: BridgeAddressPool,
}

pub struct BridgeAddressPool {
    pub cidr: String,
    pub next_host_octet: u8,
    pub allocated: std::collections::HashSet<String>,
}

pub struct VethAllocation {
    pub host_ifname: String,
    pub ns_ifname: String,
    pub ipv4_addr: String,
    pub gateway: String,
}
```

Required functions:

```rust
impl IsolatedNetwork {
    pub fn new(rfc1918_egress: Rfc1918Egress) -> Self;
    pub fn initialize(&mut self) -> Result<(), IsolatedError>;
    pub fn allocate_veth(&mut self, handle_id: &WorkspaceHandleId) -> Result<VethAllocation, IsolatedError>;
    pub fn release_veth(&mut self, allocation: &VethAllocation) -> Result<(), IsolatedError>;
}
```

Submodules:

- `network/rtnl.rs`: rtnetlink bridge/veth/address/link helpers.
- `network/netfilter.rs`: nftables NAT/filter setup and cleanup helpers.

### 7.6 `session.rs`

Job:

- Own the isolated enter/exit lifecycle and active handle maps.
- Define inverted ports for snapshot/lease and namespace runtime.

Primary newtypes:

```rust
pub struct AgentId(pub String);
pub struct WorkspaceHandleId(pub String);
```

Primary structs and fields:

```rust
pub struct SnapshotLease {
    pub lease_id: String,
    pub manifest_version: i64,
    pub root_hash: String,
    pub layer_paths: Vec<String>,
}

pub struct WorkspaceHandle {
    pub workspace_handle_id: WorkspaceHandleId,
    pub agent_id: AgentId,
    pub lease_id: String,
    pub manifest_version: i64,
    pub manifest_root_hash: String,
    pub workspace_root: String,
    pub scratch_dir: std::path::PathBuf,
    pub upperdir: std::path::PathBuf,
    pub workdir: std::path::PathBuf,
    pub layer_paths: Vec<String>,
    pub ns_fds: std::collections::HashMap<String, i32>,
    pub holder_pid: i32,
    pub readiness_fd: i32,
    pub control_fd: i32,
    pub veth: Option<VethAllocation>,
    pub cgroup_path: Option<std::path::PathBuf>,
    pub created_at: f64,
    pub last_activity: f64,
}

pub struct IsolatedSession<S, R, A>
where
    S: LayerStackSnapshotPort,
    R: NamespaceRuntimePort,
    A: AuditSink,
{
    caps: ResourceCaps,
    layer_stack: S,
    runtime: R,
    audit: A,
    network: IsolatedNetwork,
    scratch_root: std::path::PathBuf,
    handles: std::collections::HashMap<WorkspaceHandleId, WorkspaceHandle>,
    by_agent: std::collections::HashMap<AgentId, WorkspaceHandleId>,
}
```

Ports:

```rust
pub trait LayerStackSnapshotPort {
    fn acquire_snapshot(&self, request_id: &str) -> Result<SnapshotLease, IsolatedError>;
    fn release_lease(&self, lease_id: &str) -> Result<bool, IsolatedError>;
    fn active_lease_count(&self) -> Result<Option<usize>, IsolatedError>;
}

pub trait NamespaceRuntimePort {
    fn spawn_ns_holder(&self, handle: &mut WorkspaceHandle, setup_timeout_s: f64) -> Result<i32, IsolatedError>;
    fn open_ns_fds(&self, holder_pid: i32) -> Result<std::collections::HashMap<String, i32>, IsolatedError>;
    fn mount_overlay(&self, handle: &WorkspaceHandle, layer_paths: &[String]) -> Result<(), IsolatedError>;
    fn configure_dns(&self, handle: &WorkspaceHandle, fallback_dns: &str) -> Result<bool, IsolatedError>;
    fn signal_net_ready(&self, handle: &WorkspaceHandle, setup_timeout_s: f64) -> Result<(), IsolatedError>;
    fn create_cgroup(&self, handle: &WorkspaceHandle) -> Result<std::path::PathBuf, IsolatedError>;
    fn kill_holder(&self, holder_pid: i32, grace_s: f64) -> Result<(), IsolatedError>;
}
```

Submodules:

- `session/lifecycle.rs`: `initialize`, `enter`, `exit`, rollback.
- `session/capacity.rs`: host capacity, upperdir bytes, total cap.
- `session/gc.rs`: TTL and startup orphan cleanup.
- `session/persistence.rs`: persisted handle schema and recovery helpers.

---

## 8. `eos-ephemeral-workspace/src` File Contracts

### 8.1 `lib.rs`

Job:

- Document the per-operation publish-capable workspace contract.
- Re-export narrow types and ports.

Public modules:

```rust
pub mod capture;
pub mod cleanup;
pub mod dirs;
pub mod error;
pub mod finalize;
pub mod ports;
pub mod read_tool;
pub mod runner;
pub mod timings;
pub mod types;
```

No daemon JSON dispatch, no command-session registry, no plugin dispatcher.

### 8.2 `error.rs`

Job:

- Define errors for per-operation overlay setup, runner execution, capture,
  publish, and cleanup.

Primary type:

```rust
pub enum EphemeralWorkspaceError {
    InvalidArgument(String),
    SnapshotAcquire { reason: String },
    LeaseRelease { lease_id: String, reason: String },
    DirAllocation { path: std::path::PathBuf, reason: String },
    RunnerFailed { reason: String },
    CaptureFailed { reason: String },
    PublishFailed { reason: String },
    CleanupFailed { path: std::path::PathBuf, reason: String },
    Io { context: String, source: std::io::Error },
    Serde { context: String, source: serde_json::Error },
}
```

### 8.3 `types.rs`

Job:

- Define local DTOs for fresh workspace runs and outcomes.

Primary newtypes:

```rust
pub struct AgentId(pub String);
pub struct InvocationId(pub String);
pub struct WorkspaceRoot(pub std::path::PathBuf);
```

Primary structs and fields:

```rust
pub struct EphemeralSnapshot {
    pub lease_id: String,
    pub manifest_version: i64,
    pub manifest_root_hash: String,
    pub layer_paths: Vec<std::path::PathBuf>,
}

pub struct EphemeralRunDirs {
    pub run_dir: std::path::PathBuf,
    pub upperdir: std::path::PathBuf,
    pub workdir: std::path::PathBuf,
    pub output_path: std::path::PathBuf,
    pub final_path: std::path::PathBuf,
    pub request_path: Option<std::path::PathBuf>,
    pub result_path: Option<std::path::PathBuf>,
}

pub struct EphemeralWorkspace {
    pub layer_stack_root: WorkspaceRoot,
    pub workspace_root: std::path::PathBuf,
    pub agent_id: AgentId,
    pub invocation_id: InvocationId,
    pub snapshot: EphemeralSnapshot,
    pub dirs: EphemeralRunDirs,
}

pub struct EphemeralToolSpec {
    pub verb: String,
    pub intent: eos_protocol::Intent,
    pub args: serde_json::Value,
    pub background: bool,
    pub timeout_seconds: Option<f64>,
}

pub struct PathChange {
    pub path: String,
    pub kind: PathChangeKind,
}

pub enum PathChangeKind {
    Write,
    Delete,
    Symlink,
    OpaqueDir,
}

pub struct PublishOutcome {
    pub status: PublishStatus,
    pub manifest_version: Option<u64>,
    pub published_paths: Vec<String>,
    pub conflicts: Vec<String>,
    pub timings: std::collections::BTreeMap<String, serde_json::Value>,
    pub raw: serde_json::Value,
}

pub enum PublishStatus {
    Published,
    NoChanges,
    Conflict,
    Rejected,
}
```

`PublishOutcome::raw` lets `eos-daemon` preserve the current public response
shape while the new crate stays independent from `eos_occ::ChangesetResult`.

### 8.4 `ports.rs`

Job:

- Define inverted ports so this crate does not own LayerStack/OCC singleton
  services or daemon-specific process registry state.

Ports:

```rust
pub trait EphemeralSnapshotPort {
    fn acquire_snapshot(
        &self,
        root: &WorkspaceRoot,
        request_id: &str,
    ) -> Result<EphemeralSnapshot, EphemeralWorkspaceError>;

    fn release_lease(
        &self,
        root: &WorkspaceRoot,
        lease_id: &str,
    ) -> Result<bool, EphemeralWorkspaceError>;
}

pub trait WorkspacePublisherPort {
    fn publish_upperdir_changes(
        &self,
        root: &WorkspaceRoot,
        snapshot: &EphemeralSnapshot,
        changes: &[eos_protocol::LayerChange],
        path_kinds: &[PathChange],
    ) -> Result<PublishOutcome, EphemeralWorkspaceError>;
}

pub trait FreshNamespaceRunnerPort {
    fn run(
        &self,
        request: &eos_runner::RunRequest,
    ) -> Result<eos_runner::RunResult, EphemeralWorkspaceError>;
}
```

Daemon adapters:

- `DaemonSnapshotPort`: wraps `LayerStack::open`, `acquire_snapshot`,
  `release_lease`.
- `DaemonPublisherPort`: wraps the current neutral publisher.
- `DaemonFreshNamespaceRunner`: wraps `eosd ns-runner` and in-flight process
  group registration.

### 8.5 `dirs.rs`

Job:

- Allocate fresh per-operation writable directories.
- Provide cleanup guard.

Types and fields:

```rust
pub struct EphemeralDirAllocator {
    pub writable_root: std::path::PathBuf,
}

pub struct RunDirCleanup {
    path: Option<std::path::PathBuf>,
}
```

Required functions:

```rust
impl EphemeralDirAllocator {
    pub fn new(writable_root: std::path::PathBuf) -> Self;
    pub fn allocate(
        &self,
        kind: &str,
        invocation_id: &InvocationId,
    ) -> Result<EphemeralRunDirs, EphemeralWorkspaceError>;
}

impl RunDirCleanup {
    pub fn new(path: std::path::PathBuf) -> Self;
    pub fn disarm(&mut self);
}
```

### 8.6 `runner.rs`

Job:

- Build fresh namespace `RunRequest`s.
- Delegate execution to `FreshNamespaceRunnerPort`.

Types:

```rust
pub struct FreshRunRequestBuilder;
```

Required functions:

```rust
impl FreshRunRequestBuilder {
    pub fn build(
        workspace: &EphemeralWorkspace,
        spec: &EphemeralToolSpec,
    ) -> eos_runner::RunRequest;
}

pub fn run_fresh_namespace<R>(
    runner: &R,
    workspace: &EphemeralWorkspace,
    spec: &EphemeralToolSpec,
) -> Result<eos_runner::RunResult, EphemeralWorkspaceError>
where
    R: FreshNamespaceRunnerPort;
```

This file must not spawn `eosd` directly if doing so requires daemon-only
process registry state.

### 8.7 `read_tool.rs`

Job:

- Own read-only fresh overlay tool execution for verbs like `glob` and `grep`.
- Always release the lease and remove scratch.
- Never capture or publish.

Types and fields:

```rust
pub struct ReadToolRequest {
    pub layer_stack_root: WorkspaceRoot,
    pub workspace_root: std::path::PathBuf,
    pub agent_id: AgentId,
    pub invocation_id: InvocationId,
    pub verb: String,
    pub args: serde_json::Value,
    pub timeout_seconds: Option<f64>,
}

pub struct ReadToolOutcome {
    pub runner: eos_runner::RunResult,
    pub lease_acquire_s: f64,
    pub total_s: f64,
}
```

Required function:

```rust
pub fn run_read_tool<S, R>(
    snapshots: &S,
    runner: &R,
    dirs: &EphemeralDirAllocator,
    request: ReadToolRequest,
) -> Result<ReadToolOutcome, EphemeralWorkspaceError>
where
    S: EphemeralSnapshotPort,
    R: FreshNamespaceRunnerPort;
```

### 8.8 `capture.rs`

Job:

- Capture the ephemeral upperdir and classify path change kinds.

Types and fields:

```rust
pub struct CapturedUpperdir {
    pub changes: Vec<eos_protocol::LayerChange>,
    pub path_kinds: Vec<PathChange>,
    pub stats: TreeResourceStats,
    pub capture_s: f64,
}
```

Required function:

```rust
pub fn capture_for_publish(
    upperdir: &std::path::Path,
) -> Result<CapturedUpperdir, EphemeralWorkspaceError>;
```

### 8.9 `finalize.rs`

Job:

- Own command/plugin-style overlay finalization after a fresh namespace operation
  has produced files in the upperdir.
- Capture upperdir.
- Call `WorkspacePublisherPort`.
- Return a typed outcome for daemon response shaping.

Types and fields:

```rust
pub struct FinalizeRequest {
    pub workspace: EphemeralWorkspace,
    pub command_started_at: Option<std::time::Instant>,
}

pub struct FinalizeOutcome {
    pub capture: CapturedUpperdir,
    pub publish: PublishOutcome,
    pub timings: EphemeralTimings,
}
```

Required function:

```rust
pub fn finalize_publishable_workspace<P>(
    publisher: &P,
    request: FinalizeRequest,
) -> Result<FinalizeOutcome, EphemeralWorkspaceError>
where
    P: WorkspacePublisherPort;
```

This function may be used by command finalization and plugin overlay execution,
but it must not own command-session registry state or plugin result parsing.

### 8.10 `timings.rs`

Job:

- Provide workspace-local timing/resource DTOs.
- Leave public JSON response formatting to `eos-daemon`.

Types and fields:

```rust
pub struct TreeResourceStats {
    pub files: u64,
    pub dirs: u64,
    pub symlinks: u64,
    pub bytes: u64,
}

pub struct EphemeralTimings {
    pub lease_acquire_s: Option<f64>,
    pub runner_s: Option<f64>,
    pub capture_s: Option<f64>,
    pub publish_s: Option<f64>,
    pub cleanup_s: Option<f64>,
    pub total_s: f64,
    pub extra: std::collections::BTreeMap<String, serde_json::Value>,
}
```

Required functions:

```rust
impl TreeResourceStats {
    pub fn collect(path: &std::path::Path) -> Self;
}

impl EphemeralTimings {
    pub fn new(total_s: f64) -> Self;
    pub fn insert_extra(&mut self, key: impl Into<String>, value: serde_json::Value);
}
```

### 8.11 `cleanup.rs`

Job:

- Centralize lease release and run-dir cleanup sequencing.
- Make cleanup best-effort but observable.

Types and fields:

```rust
pub struct CleanupOutcome {
    pub released_lease: bool,
    pub removed_run_dir: bool,
    pub cleanup_s: f64,
    pub errors: Vec<String>,
}
```

Required function:

```rust
pub fn cleanup_ephemeral_workspace<S>(
    snapshots: &S,
    root: &WorkspaceRoot,
    snapshot: &EphemeralSnapshot,
    run_dir: &std::path::Path,
) -> CleanupOutcome
where
    S: EphemeralSnapshotPort;
```

---

## 9. What Stays in `eos-daemon`

These are composition and routing responsibilities, not workspace policy:

| Daemon area | Why it stays |
|---|---|
| `dispatcher.rs` | Owns public RPC/API dispatch and wire envelopes. |
| `isolated.rs` | Owns daemon singleton bootstrap, lifecycle RPCs, and JSON response shape. It injects ports into `eos-isolated-workspace`. |
| `isolated/runtime.rs` | Concrete daemon adapter that spawns `eosd ns-holder` / `eosd ns-runner` and opens LayerStack. |
| `workspace_ops.rs` | Owns public `read_file`, `write_file`, `edit_file`, `glob`, `grep` routing. |
| `workspace_ops/isolated_workspace.rs` | Owns isolated response shaping for daemon workspace ops. |
| `command.rs` and `command/*` | Own command-session registry, stdin, cancellation, progress, and finalization orchestration. |
| `plugin/*` | Own plugin dispatch, plugin output parsing, and plugin/LSP isolated-mode gates. |
| `occ_writer*` | Stays daemon-neutral for this extraction, or later moves to `eos-workspace-publish`, not `eos-ephemeral-workspace`. |

---

## 10. Boundary Matrix

| Behavior | `eos-ephemeral-workspace` | `eos-isolated-workspace` | `eos-daemon` | Shared substrate |
|---|---:|---:|---:|---:|
| Fresh run dir allocation | yes | no | adapter only | `eos-overlay` primitive |
| Fresh namespace `RunRequest` | yes | no | runner adapter | `eos-runner` executes |
| Read-only overlay search | yes | no | public response | LayerStack lease |
| Upperdir capture for publish | yes | no | response shaping | `eos-overlay` capture |
| OCC publish | via port | forbidden | current adapter | `eos-occ` |
| Direct `write_file`/`edit_file` fast path | no | no | yes | publisher/OCC |
| Persistent per-agent handle | no | yes | RPC ownership | LayerStack lease |
| Isolated network bridge/veth | no | yes | adapter/ops | Linux netlink |
| Isolated audit-only write record | no | yes | response shaping | JSONL sink |
| Command-session registry | no | no | yes | none |
| Plugin/LSP gate | no | no | yes | none |

---

## 11. Adversarial Review

This review is intentionally hostile to the plan. The goal is to prevent a
well-named but over-engineered refactor.

### 11.1 Findings

| Risk | Why it would make the refactor worse | Required mitigation |
|---|---|---|
| `eos-ephemeral-workspace` becomes a wrapper crate with no real ownership. | A new crate that only forwards daemon calls increases indirection without simplifying the codebase. | Phase 3 cannot complete unless at least two fresh-overlay call sites share extracted lifecycle code and daemon LOC decreases or becomes visibly simpler. |
| Generic publishing is mislabeled as ephemeral. | `occ_writer` also serves direct write/edit and plugin callbacks. Moving it wholesale under `ephemeral` would encode a false boundary. | Keep generic publishing in `eos-daemon` for this plan, or extract a neutral publisher crate in a separate approved plan. |
| Port traits become abstraction theater. | Ports are useful for avoiding daemon backedges, but too many traits turn a small extraction into a framework. | Keep exactly the three Phase 2 ports unless Phase 3 proves a fourth is necessary. Do not introduce dyn trait objects where generic parameters are enough. |
| DTOs duplicate existing protocol structs. | Re-modeling `RunRequest`, `RunResult`, `Intent`, or `LayerChange` creates conversion churn and drift. | Reuse `eos_runner` and `eos_protocol` DTOs directly. Only add local structs for workspace-specific identity, dirs, capture, timings, and outcomes. |
| Direct workspace ops are confused with ephemeral overlay. | `read_file`/`write_file`/`edit_file` fast paths do not create a fresh upperdir, so putting them in `eos-ephemeral-workspace` would break the user's semantic boundary. | Keep direct ops in `eos-daemon` for this refactor. Docs must call them shared direct ops, not ephemeral fast paths. |
| Command sessions leak into workspace crates. | Command sessions are shared by ephemeral and isolated paths. Moving registry/progress/stdin into either workspace crate couples lifecycle policy to session control. | Keep command-session registry and control in `eos-daemon` until a neutral `eos-command-session` plan exists. |
| Isolated rename gets mixed with behavior changes. | A rename should be mechanically reviewable. If behavior changes are mixed in, regressions become hard to locate. | Phase 1 is rename-only plus `.DS_Store` cleanup. No lifecycle rewrite is allowed in Phase 1. |
| Live e2e evidence is deferred too late. | Workspace behavior is kernel/overlay/OCC-sensitive; unit tests alone can miss real namespace and publish failures. | Phase 3 and Phase 4 gates include targeted live e2e when `EOS_LIVE_E2E_IMAGE` is available; otherwise the blocker must be recorded before Phase 5. |

### 11.2 Simplicity Verdict

The plan is clean only under these constraints:

1. Rename `eos-isolated` first, with no behavior edits.
2. Add `eos-ephemeral-workspace` as an empty typed boundary before moving logic.
3. Move only duplicated fresh-overlay lifecycle code, not daemon dispatch,
   command-session control, plugin routing, or generic publishing.
4. Stop after Phase 3 if the extraction does not reduce daemon complexity.

If those constraints are enforced, the plan is the simplest useful refactor. If
any constraint is relaxed, the plan risks becoming a crate taxonomy exercise
rather than a cleanup.

---

## 12. Phase Progress Tracker and Enforcement Gate

### 12.1 Gate Rules

Status values:

- `Pending`: no code/docs work has started for the phase.
- `In Progress`: the phase is being implemented.
- `Blocked`: an acceptance item failed or required evidence is unavailable.
- `Complete`: every checklist item for the phase is checked and verification
  output has been recorded in the tracker notes.

Enforcement rules:

1. A phase may move from `Pending` to `In Progress` only when every previous
   phase is `Complete`.
2. A phase may move to `Complete` only when every item in its checklist is
   checked.
3. If any required check fails, the phase becomes `Blocked`; the next phase must
   not start.
4. Any scope expansion must be added to `Open Decisions` or a new plan before
   implementation continues.
5. Each implementation phase must end with `git diff --check` and the scoped
   Cargo checks listed in that phase.

### 12.2 Progress Tracker

| Phase | Status | Evidence / notes |
|---|---|---|
| Phase 0: Spec adversarial review and gates | Complete | User instructed Codex to proceed with this spec on 2026-06-04; adversarial review, tracker, checklists, and `git diff --check -- docs/plans/workspace-crate-boundary_SPEC.md` are complete. |
| Phase 1: Rename isolated crate | Complete | Renamed crate to `eos-isolated-workspace`; removed crate-local `.DS_Store` files; restored missing daemon audit test sidecar that blocked `--all-targets`; passed `cargo fmt -p eos-isolated-workspace`, `cargo check -p eos-isolated-workspace --all-targets`, `cargo test -p eos-isolated-workspace`, `cargo check -p eos-daemon --all-targets`, stale-name guard, and `git diff --check`. |
| Phase 2: Add empty ephemeral crate | Complete | Added `eos-ephemeral-workspace` with only the section 6.2 modules, allowed dependencies, real `eos_runner` / `eos_protocol` DTO references, and exactly the three Phase 2 ports; passed `cargo fmt -p eos-ephemeral-workspace`, `cargo check -p eos-ephemeral-workspace --all-targets`, `cargo test -p eos-ephemeral-workspace`, dependency/source guards, and `git diff --check`. |
| Phase 3: Move fresh overlay helpers | Complete | Fresh run-dir allocation is wrapped through `EphemeralDirAllocator`; `glob`/`grep` read-only overlay runs use `run_read_tool`; command and plugin overlay publish paths use `capture_for_publish` plus extracted path-kind/resource stats; daemon process registry, command-session state, plugin parsing, and direct workspace ops remain in `eos-daemon`. Passed `cargo check -p eos-ephemeral-workspace --all-targets`, `cargo test -p eos-ephemeral-workspace`, `cargo check -p eos-daemon --all-targets`, `cargo test -p eos-e2e-test --test overlay -- --list`, and `git diff --check`. `EOS_LIVE_E2E_IMAGE` was unset, so the conditional live overlay run was not available. |
| Phase 4: Move ephemeral finalization policy | Complete | `finalize_publishable_workspace` is used by ephemeral command finalization and plugin overlay after daemon-side plugin result parsing. The daemon-owned `DaemonPublisherPort` wraps the neutral `occ_writer`; command registry, stdin/cancel/output cursors, plugin parsing, and generic publisher state remain in `eos-daemon`. Passed `cargo check -p eos-ephemeral-workspace --all-targets`, `cargo test -p eos-ephemeral-workspace`, `cargo check -p eos-daemon --all-targets`, `cargo test -p eos-e2e-test --test command_sessions -- --list`, extra `cargo test -p eos-daemon`, and `git diff --check`. `EOS_LIVE_E2E_IMAGE` was unset, so the conditional live command-session run was not available. |
| Phase 5: Tighten tests and architecture docs | Complete | Architecture pages already reference `eos-isolated-workspace` and distinguish fresh ephemeral overlays from shared direct file ops. Focused tests cover isolated no-publish/discard and ephemeral cleanup success/failure paths. Passed static dependency/source guards, `cargo check -p eos-isolated-workspace --all-targets`, `cargo test -p eos-isolated-workspace`, `cargo check -p eos-ephemeral-workspace --all-targets`, `cargo test -p eos-ephemeral-workspace`, `cargo check -p eos-daemon --all-targets`, `cargo test -p eos-e2e-test -- --list`, live Docker `cargo test -p eos-e2e-test --features e2e --test overlay`, `isolated_workspace`, `occ`, and `command_sessions` with `EOS_LIVE_E2E_IMAGE=sweevo-dask__dask-10042:latest`, and `git diff --check`. |

### 12.3 Phase 0 Checklist: Spec Review Gate

- [x] Adversarial review captures over-extraction risks.
- [x] Progress tracker exists.
- [x] Phase progression rules require all previous acceptance criteria before
  moving forward.
- [x] User accepts this spec as the implementation contract.
- [x] `git diff --check -- docs/plans/workspace-crate-boundary_SPEC.md` passes.

### 12.4 Phase 1 Checklist: Rename Isolated Crate

- [x] `sandbox/crates/eos-isolated` is renamed to
  `sandbox/crates/eos-isolated-workspace`.
- [x] Package name is `eos-isolated-workspace`.
- [x] Rust crate imports are updated to `eos_isolated_workspace`.
- [x] `sandbox/Cargo.toml` workspace member and dependency entries are updated.
- [x] No behavior code is changed except imports/module paths required by the
  rename.
- [x] `.DS_Store` files under the renamed crate are removed.
- [x] `rg -n "eos_isolated::|eos_isolated\b|eos-isolated([\" =]|$)"
  sandbox/crates sandbox/Cargo.toml` finds no stale code import or package
  dependency. This replaces the earlier literal substring guard because
  `eos-isolated-workspace` necessarily contains `eos-isolated`.
- [x] `cargo fmt -p eos-isolated-workspace` passes.
- [x] `cargo check -p eos-isolated-workspace --all-targets` passes.
- [x] `cargo test -p eos-isolated-workspace` passes.
- [x] `cargo check -p eos-daemon --all-targets` passes.
- [x] `git diff --check` passes.

### 12.5 Phase 2 Checklist: Add Empty Ephemeral Crate

- [x] `sandbox/crates/eos-ephemeral-workspace` exists.
- [x] The crate contains only the modules from section 6.2.
- [x] `Cargo.toml` depends only on allowed dependencies from section 5.3.
- [x] `types.rs` reuses `eos_runner` and `eos_protocol` DTOs instead of
  duplicating them.
- [x] `ports.rs` defines only `EphemeralSnapshotPort`,
  `WorkspacePublisherPort`, and `FreshNamespaceRunnerPort`.
- [x] No daemon behavior changes in this phase.
- [x] `cargo fmt -p eos-ephemeral-workspace` passes.
- [x] `cargo check -p eos-ephemeral-workspace --all-targets` passes.
- [x] `cargo test -p eos-ephemeral-workspace` passes.
- [x] Dependency guard for `eos-ephemeral-workspace` passes.
- [x] `git diff --check` passes.

### 12.6 Phase 3 Checklist: Move Fresh Overlay Helpers

- [x] Fresh run-dir allocation is moved or wrapped through
  `eos-ephemeral-workspace`.
- [x] Read-only fresh overlay execution for `glob`/`grep` is moved or wrapped.
- [x] Upperdir capture/path-kind/resource-stat helpers used by publishable
  overlay flows are moved or wrapped.
- [x] Daemon-only process registry and process-group registration remain in
  `eos-daemon`.
- [x] The extracted code is used by at least two fresh-overlay call sites, or the
  phase is marked `Blocked` for insufficient simplification.
- [x] Direct `read_file`/`write_file`/`edit_file` fast paths remain in
  `eos-daemon`.
- [x] `cargo check -p eos-ephemeral-workspace --all-targets` passes.
- [x] `cargo test -p eos-ephemeral-workspace` passes.
- [x] `cargo check -p eos-daemon --all-targets` passes.
- [x] `cargo test -p eos-e2e-test --test overlay -- --list` passes.
- [x] `EOS_LIVE_E2E_IMAGE` was unavailable, so the conditional
  `cargo test -p eos-e2e-test --features e2e --test overlay` check was not
  required in this environment.
- [x] `git diff --check` passes.

### 12.7 Phase 4 Checklist: Move Ephemeral Finalization Policy

- [x] Capture-then-publish finalization is exposed through
  `finalize_publishable_workspace`.
- [x] Ephemeral command finalization uses the extracted finalization policy.
- [x] Plugin overlay execution uses the extracted finalization policy only after
  plugin result parsing remains in `eos-daemon`.
- [x] Generic `occ_writer` remains in `eos-daemon` or a separately approved
  neutral publisher crate.
- [x] Command-session registry, stdin, cancellation, and output cursors remain
  in `eos-daemon`.
- [x] Public daemon response JSON shape remains compatible.
- [x] `cargo check -p eos-ephemeral-workspace --all-targets` passes.
- [x] `cargo test -p eos-ephemeral-workspace` passes.
- [x] `cargo check -p eos-daemon --all-targets` passes.
- [x] `cargo test -p eos-e2e-test --test command_sessions -- --list` passes.
- [x] `EOS_LIVE_E2E_IMAGE` was unavailable, so the conditional
  `cargo test -p eos-e2e-test --features e2e --test command_sessions` check was
  not required in this environment.
- [x] `git diff --check` passes.

### 12.8 Phase 5 Checklist: Tests and Architecture Docs

- [x] `docs/architecture/sandbox/workspaces.html` uses
  `eos-isolated-workspace`.
- [x] `docs/architecture/tools/isolated-workspace.html` uses
  `eos-isolated-workspace`.
- [x] Architecture docs distinguish fresh ephemeral overlays from shared direct
  workspace ops.
- [x] Focused unit tests cover isolated no-publish and ephemeral cleanup paths.
- [x] `cargo test -p eos-e2e-test -- --list` shows grouped module targets.
- [x] If `EOS_LIVE_E2E_IMAGE` is available, all targeted live checks in section
  14.5 pass.
- [x] `git diff --check` passes.

---

## 13. Migration Plan

### Phase 1: Rename isolated crate

1. Rename folder `sandbox/crates/eos-isolated` to
   `sandbox/crates/eos-isolated-workspace`.
2. Update `sandbox/Cargo.toml` workspace member and dependency key.
3. Update package name in the crate `Cargo.toml`.
4. Update imports from `eos_isolated` to `eos_isolated_workspace`.
5. Remove `.DS_Store` files under the crate.

### Phase 2: Add empty ephemeral crate

1. Add `sandbox/crates/eos-ephemeral-workspace/Cargo.toml`.
2. Add the `src` files listed in section 6.2 with types and ports.
3. Add the workspace member and workspace dependency in `sandbox/Cargo.toml`.
4. Do not change daemon behavior yet.

### Phase 3: Move fresh overlay helpers

Move or wrap:

- fresh run dir allocation from `eos-daemon/src/overlay_runner.rs`;
- read-only `glob`/`grep` overlay flow from `workspace_ops.rs`;
- upperdir capture/path-kind/resource-stat helpers used by command/plugin
  overlay finalization.

Keep daemon-only process registry adapters in `eos-daemon`.

### Phase 4: Move ephemeral finalization policy

Extract the capture-then-publish sequence used by:

- ephemeral command finalization;
- plugin overlay execution where the plugin result is already produced;
- future publishable overlay operations.

Keep command-session registry and plugin result parsing in `eos-daemon`.

### Phase 5: Tighten tests and architecture docs

1. Add focused crate tests for both workspace policy crates.
2. Update architecture pages that still reference old crate names.
3. Keep live e2e tests under `eos-e2e-test` grouped by module targets.

---

## 14. Acceptance Criteria

### 14.1 Filesystem and naming

- `sandbox/crates/eos-isolated-workspace` exists.
- `sandbox/crates/eos-isolated` no longer exists.
- `sandbox/crates/eos-ephemeral-workspace` exists.
- `sandbox/Cargo.toml` lists both target crates.
- No `.DS_Store` exists under either crate.
- `rg -n "eos_isolated::|eos_isolated\b|eos-isolated([\" =]|$)"
  sandbox/crates sandbox/Cargo.toml` finds no stale code import or package
  dependency, except in historical docs if intentionally left.

### 14.2 Dependency guards

Run from `sandbox/`:

```bash
cargo tree -p eos-isolated-workspace | rg 'eos-occ|eos_daemon|eos-daemon' && exit 1 || true
cargo tree -p eos-ephemeral-workspace | rg 'eos-daemon|eos_isolated_workspace|eos-isolated-workspace' && exit 1 || true
```

Static source guards:

```bash
rg 'eos_occ|apply_occ_changeset|publish_layer' sandbox/crates/eos-isolated-workspace/src && exit 1 || true
rg 'CommandSession|write_stdin|command_session_count' sandbox/crates/eos-ephemeral-workspace/src && exit 1 || true
rg 'plugin|lsp' sandbox/crates/eos-isolated-workspace/src && exit 1 || true
```

### 14.3 Build checks

Run from `sandbox/`:

```bash
cargo check -p eos-isolated-workspace --all-targets
cargo test -p eos-isolated-workspace
cargo check -p eos-ephemeral-workspace --all-targets
cargo test -p eos-ephemeral-workspace
cargo check -p eos-daemon --all-targets
```

### 14.4 Behavior checks

Ephemeral workspace:

- A read-only overlay operation acquires a snapshot, runs in a fresh namespace,
  releases the lease, removes the run dir, and does not call publish.
- A write-capable overlay operation captures upperdir changes, classifies write,
  delete, symlink, and opaque-dir kinds, publishes through the neutral publisher,
  releases the lease, and removes scratch.
- Two disjoint write-capable operations create two independent fresh upperdirs.
- A failed runner still releases the lease and removes the run dir.

Isolated workspace:

- `enter` creates one active handle keyed by `agent_id`.
- Two tool calls for the same agent reuse the same persistent upperdir.
- Writes made inside isolated mode are visible to later isolated calls for that
  agent before exit.
- Those writes do not change the shared LayerStack manifest.
- `exit` releases the lease, tears down namespace/network/cgroup state, removes
  scratch, and records audit-only events.
- Plugin/LSP operations are still blocked by daemon/tool routing while isolated
  mode is active.

### 14.5 Live e2e checks

Run from `sandbox/` after the crate rename/extraction compiles:

```bash
cargo test -p eos-e2e-test --features e2e --test overlay
cargo test -p eos-e2e-test --features e2e --test isolated_workspace
cargo test -p eos-e2e-test --features e2e --test occ
cargo test -p eos-e2e-test --features e2e --test command_sessions
```

The live target image should continue to be provided through the existing
`EOS_LIVE_E2E_IMAGE` path. No non-Docker provider branch is part of this
spec.

### 14.6 Documentation checks

- `docs/architecture/sandbox/workspaces.html` references
  `eos-isolated-workspace`, not `eos-isolated`, after the rename lands.
- `docs/architecture/tools/isolated-workspace.html` references the renamed crate.
- Any mention of `ephemeral workspace` distinguishes fresh overlay operations
  from direct `read_file`/`write_file`/`edit_file` fast paths.

---

## 15. Open Decisions

1. Whether to keep the generic publisher in `eos-daemon` for now or extract a
   neutral `eos-workspace-publish` crate before moving ephemeral finalization.
   The cleanest first step is to keep it in `eos-daemon`.
2. Whether `eos-ephemeral-workspace` should own the concrete `eosd ns-runner`
   child-spawn adapter. The clean boundary is no: keep process group registration
   in `eos-daemon` and inject `FreshNamespaceRunnerPort`.
3. Whether direct `read_file`/`write_file`/`edit_file` should be renamed in docs
   from "ephemeral fast path" to "shared workspace direct ops". This spec
   recommends the rename because those ops do not allocate a fresh upperdir.
