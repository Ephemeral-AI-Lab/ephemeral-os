# Unified Workspace Refactor Spec

Status: Draft
Date: 2026-06-17
Owner: `crates/daemon`

## 1. Goal

Unify the current ephemeral workspace and isolated workspace concepts behind one
caller-facing lifecycle:

1. `create`
2. `run_command`
3. `capture_changes`
4. `destroy`

The caller-facing API uses `workspace_root`. `layer_stack_root` remains an
internal storage detail resolved from the workspace binding.

The only caller-visible mode distinction is network behavior:

```rust
pub enum NetworkAccess {
    Standard,
    Isolated,
}
```

`Standard` is the current non-isolated/default command workspace behavior.
`Isolated` is the current private namespace/veth/cgroup/DNS workspace behavior.

## 2. Non-Goals

- Do not merge LayerStack storage into the workspace crate.
- Do not expose `layer_stack_root`, `upperdir`, `workdir`, veth names, namespace
  FDs, holder PIDs, or cgroup paths in caller-facing DTOs.
- Do not publish changes implicitly from `destroy`.
- Do not rewrite the low-level overlay, namespace, netfilter, or command process
  runners as part of this refactor.

## 3. Ownership Boundaries

| Owner | Owns | Does Not Own |
|---|---|---|
| `LayerStack` | manifests, layer storage, snapshot leases, publish/OCC, capture routing | process lifecycle, network namespace lifecycle |
| `WorkspaceRuntime` | caller lifecycle, route decision, mode gate, command cancel ordering, lease custody | low-level overlay mount syscalls |
| `workspace` crate | overlay dirs, upperdir capture primitives, standard/isolated workspace lifecycle primitives | command process registry, public operation wire shape |
| `CommandOps` | process registry, PTY/process wait, stdin/progress/cancel mechanics | snapshot leases, overlay dir allocation |
| `overlay` crate | overlayfs mount/unmount mechanics | daemon policy |
| `namespace`/namespace subprocess | holder, setns/fresh-ns execution mechanics | caller-facing workspace semantics |

## 4. Terminology

### `workspace_root`

The filesystem root visible to the tool or command. This is caller-facing.
Example: `/testbed`.

### `layer_stack_root`

The daemon storage root containing LayerStack manifests, layers, leases, and
`workspace.json`. This is internal. Existing LayerStack validation requires it
to be outside `workspace_root`.

### `BaseRevision`

Caller-facing description of the pinned base state used by a workspace.

```rust
pub struct BaseRevision {
    pub version: i64,
    pub root_hash: String,
    pub layer_count: usize,
}
```

### `LeasedBaseRevision`

Internal value that carries the lease and lower layer paths. It must be released
when create fails, when a workspace is destroyed, or when command cleanup
discards a workspace.

```rust
pub(crate) struct LeasedBaseRevision {
    pub lease_id: String,
    pub version: i64,
    pub root_hash: String,
    pub layer_paths: Vec<PathBuf>,
}
```

### `OverlayDirs`

Scratch filesystem paths for one workspace instance.

```rust
pub(crate) struct OverlayDirs {
    pub run_dir: PathBuf,
    pub upperdir: PathBuf,
    pub workdir: PathBuf,
}
```

- `run_dir`: parent scratch directory for this workspace instance.
- `upperdir`: writable changes layer.
- `workdir`: overlayfs required work directory.
- Lower dirs come from `LeasedBaseRevision.layer_paths`.
- The mount target is `workspace_root`.

## 5. Target Folder Structure And LOC Budget

Current `crates/daemon/workspace/src` is about 3.8k LOC. The refactor keeps
roughly the same implementation size while moving public API into small files
and keeping low-level private mechanisms split.

```text
crates/daemon/workspace/src/                         ~4,250 LOC
  lib.rs                                                ~35
  model.rs                                             ~230
  error.rs                                              ~90
  service.rs                                           ~160
  overlay/                                             ~370
    mod.rs                                              ~20
    dirs.rs                                            ~120
    capture.rs                                         ~110
    tree.rs                                            ~120
  network_mode/                                      ~3,365
    mod.rs                                              ~45
    standard.rs                                         ~90
    isolated.rs                                        ~120
    isolated/                                        ~3,110
      mod.rs                                            ~40
      types.rs                                         ~150
      caps.rs                                           ~80
      manager.rs                                       ~160
      lifecycle.rs                                     ~480
      recovery.rs                                      ~330
      remount.rs                                       ~170
      namespace.rs                                     ~600
      ns_runner.rs                                     ~205
      network/                                       ~895
        mod.rs                                         ~220
        rtnl.rs                                        ~190
        netfilter/mod.rs                               ~165
        netfilter/exprs.rs                             ~320
        netfilter/wire.rs                              ~340
```

Supporting daemon/runtime files:

```text
crates/daemon/core/src/runtime/
  workspace.rs                                      existing +250 LOC
  workspace/
    root.rs                                           ~120
    command.rs                                        ~260
    files.rs                                          ~180
    lifecycle.rs                                      ~240

crates/daemon/operation/src/workspace/
  contract.rs                                         ~220
  mod.rs                                               ~10

crates/daemon/core/src/op_adapter/
  workspace.rs                                        ~260
```

The exact split can be adjusted during implementation, but any single new file
above roughly 500 LOC should be split before merging.

## 6. Public Type Shape

These are operation/runtime-facing names. They are intentionally not named
after overlayfs or LayerStack internals.

```rust
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CreateWorkspaceRequest {
    pub owner: CallerId,
    pub workspace_root: PathBuf,
    pub network: NetworkAccess,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum NetworkAccess {
    Standard,
    Isolated,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct WorkspaceHandle {
    pub id: WorkspaceId,
    pub owner: CallerId,
    pub workspace_root: PathBuf,
    pub network: NetworkAccess,
    pub base_revision: BaseRevision,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct WorkspaceId(pub String);

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CallerId(pub String);
```

### Run Command

```rust
#[derive(Debug, Clone, PartialEq)]
pub struct RunCommandRequest {
    pub invocation_id: String,
    pub cmd: String,
    pub cwd: Option<PathBuf>,
    pub timeout_seconds: Option<f64>,
    pub yield_time_ms: u64,
    pub remountable: bool,
}

#[derive(Debug, Clone, PartialEq)]
pub struct RunCommandResult {
    pub status: CommandStatus,
    pub command_id: Option<String>,
    pub exit_code: Option<i64>,
    pub stdout: String,
    pub stderr: String,
    pub changed_paths: Vec<String>,
    pub base_revision: BaseRevision,
    pub published: bool,
}
```

`run_command` is implemented by `WorkspaceRuntime` over `CommandOps`. The
lower `workspace` crate may provide command context objects, but it must not
own the process registry.

### Capture Changes

```rust
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CaptureChangesRequest {
    pub materialize_payloads: bool,
    pub include_stats: bool,
}

#[derive(Debug, Clone, PartialEq)]
pub struct CaptureChangesResult {
    pub workspace_id: WorkspaceId,
    pub base_revision: BaseRevision,
    pub changed_paths: Vec<String>,
    pub changed_path_kinds: BTreeMap<String, ChangedPathKind>,
    pub protected_drops: Vec<ProtectedPathDrop>,
    pub stats: Option<TreeResourceStats>,
}
```

`capture_changes` captures pending overlay changes. It does not publish them.
Publishing remains a separate LayerStack operation chosen by the caller/runtime.

### Destroy

```rust
#[derive(Debug, Clone, PartialEq)]
pub struct DestroyWorkspaceRequest {
    pub grace_s: Option<f64>,
    pub cancel_commands: bool,
}

#[derive(Debug, Clone, PartialEq)]
pub struct DestroyWorkspaceResult {
    pub workspace_id: WorkspaceId,
    pub owner: CallerId,
    pub cancelled_commands: usize,
    pub evicted_upperdir_bytes: u64,
    pub lifetime_s: f64,
    pub lease_released: Option<bool>,
    pub lease_release_error: Option<String>,
    pub active_leases_after: usize,
}
```

Lease release failure is data in `DestroyWorkspaceResult` unless teardown itself
cannot complete.

## 7. Internal Type Shape

```rust
pub(crate) struct ResolvedWorkspaceRoot {
    pub workspace_root: PathBuf,
    pub layer_stack_root: PathBuf,
    pub binding: layerstack::WorkspaceBinding,
}

pub(crate) struct InternalWorkspaceHandle {
    pub public: WorkspaceHandle,
    pub layer_stack_root: PathBuf,
    pub leased_base: LeasedBaseRevision,
    pub dirs: OverlayDirs,
    pub mode: InternalNetworkMode,
    pub created_at: f64,
    pub last_activity: f64,
}

pub(crate) enum InternalNetworkMode {
    Standard(StandardWorkspaceState),
    Isolated(IsolatedWorkspaceState),
}

pub(crate) struct StandardWorkspaceState;

pub(crate) struct IsolatedWorkspaceState {
    pub ns_fds: HashMap<String, i32>,
    pub holder_pid: i32,
    pub readiness_fd: i32,
    pub control_fd: i32,
    pub veth: Option<VethAllocation>,
    pub cgroup_path: Option<PathBuf>,
    pub dns_configuration: DnsConfiguration,
    pub remount_state: WorkspaceRemountState,
}
```

## 8. Method Shape

The caller-facing service is implemented by `WorkspaceRuntime`, not directly by
the lower `workspace` crate.

```rust
pub trait WorkspaceService {
    fn create(
        &self,
        request: CreateWorkspaceRequest,
    ) -> Result<WorkspaceHandle, WorkspaceError>;

    fn run_command(
        &self,
        handle: &WorkspaceHandle,
        request: RunCommandRequest,
    ) -> Result<RunCommandResult, WorkspaceError>;

    fn capture_changes(
        &self,
        handle: &WorkspaceHandle,
        request: CaptureChangesRequest,
    ) -> Result<CaptureChangesResult, WorkspaceError>;

    fn destroy(
        &self,
        handle: WorkspaceHandle,
        request: DestroyWorkspaceRequest,
    ) -> Result<DestroyWorkspaceResult, WorkspaceError>;
}
```

Internal helpers:

```rust
impl WorkspaceRuntime {
    pub(crate) fn resolve_workspace_root(
        &self,
        workspace_root: &Path,
    ) -> Result<ResolvedWorkspaceRoot, WorkspaceError>;

    pub(crate) fn acquire_base_revision(
        &self,
        root: &ResolvedWorkspaceRoot,
        owner: &CallerId,
    ) -> Result<LeasedBaseRevision, WorkspaceError>;

    pub(crate) fn route_command_context(
        &self,
        handle: &WorkspaceHandle,
    ) -> Result<WorkspaceCommandContext, WorkspaceError>;
}

pub(crate) enum WorkspaceCommandContext {
    Standard(StandardCommandContext),
    Isolated(IsolatedCommandContext),
}
```

## 9. Error Shape

```rust
#[derive(Debug, thiserror::Error)]
pub enum WorkspaceError {
    #[error("invalid request for {field}: {message}")]
    InvalidRequest { field: &'static str, message: String },

    #[error("workspace feature is disabled")]
    FeatureDisabled,

    #[error("workspace already open for {owner:?}")]
    AlreadyOpen { owner: CallerId, workspace_id: WorkspaceId },

    #[error("workspace is not open for {owner:?}")]
    NotOpen { owner: CallerId },

    #[error("cannot change workspace while commands are active")]
    ActiveCommands { owner: CallerId, active_commands: usize },

    #[error("workspace quota exceeded: {total_cap}")]
    QuotaExceeded { total_cap: u32 },

    #[error("resource pressure: required {required_bytes}, budget {budget_bytes}")]
    ResourcePressure { required_bytes: u64, budget_bytes: u64 },

    #[error("snapshot acquire failed: {source}")]
    SnapshotAcquire { source: String },

    #[error("workspace setup failed at {step}")]
    Setup { step: String },

    #[error("network setup failed: {message}")]
    Network { message: String },

    #[error("command failed: {message}")]
    Command { message: String },

    #[error("capture failed: {message}")]
    Capture { message: String },

    #[error("publish failed: {message}")]
    Publish { message: String },
}
```

## 10. Mode Semantics

### Standard

`NetworkAccess::Standard` maps to the current fresh namespace/default command
workspace path:

- acquire `LeasedBaseRevision`
- allocate `OverlayDirs`
- run commands using current fresh namespace overlay flow
- capture changes from `upperdir`
- caller/runtime chooses whether to publish
- destroy removes scratch dirs and releases the lease

### Isolated

`NetworkAccess::Isolated` maps to the current isolated workspace path:

- acquire `LeasedBaseRevision`
- allocate `OverlayDirs`
- spawn holder namespace
- open namespace FDs
- initialize bridge/netfilter
- install veth
- mount overlay inside holder namespace
- configure DNS
- signal network ready
- create cgroup
- register and persist the handle
- destroy kills holder, closes FDs, tears down veth/cgroup, removes scratch,
  persists manager state, and releases the lease

## 11. Migration Plan

### Phase 1: Add New Names Beside Old Names

- Add `model.rs`, `error.rs`, and skeleton service/request/result types.
- Keep current `EphemeralWorkspace`, `IsolatedManager`, and
  `IsolatedWorkspaceBinding` exports.
- Add conversions from current isolated handle to new `WorkspaceHandle`.

### Phase 2: Resolve `workspace_root` Internally

- Add `ResolvedWorkspaceRoot`.
- Make new DTOs parse `workspace_root`.
- Keep compatibility parsing for legacy `layer_stack_root`.
- New code and docs emit only `workspace_root`.

### Phase 3: Move Standard Overlay Ownership Out Of `CommandOps`

- Move bounded snapshot acquisition and `EphemeralWorkspace::create` from
  command start into `WorkspaceRuntime`.
- Keep command process spawning in `CommandOps`.
- Ensure command finalization releases `LeasedBaseRevision` exactly once.

### Phase 4: Centralize Routing

- Move command/file route decisions from op adapters into `WorkspaceRuntime`.
- Adapters parse wire args and record trace events, but do not choose standard
  vs isolated behavior directly.

### Phase 5: Add Explicit `capture_changes`

- Implement isolated `capture_changes` over the handle `upperdir`.
- Implement standard `capture_changes` over the per-workspace `upperdir`.
- Reject or quiesce active commands before walking the upperdir.
- Do not publish from this method.

### Phase 6: Move Files Into Target Structure

- Move `capture.rs`, `dirs.rs`, and `tree.rs` into `overlay/`.
- Move current isolated internals into `network_mode/isolated/`.
- Keep mechanism names for namespace/veth/netfilter files.

### Phase 7: Retire Legacy Names

- Stop exporting `EphemeralWorkspace`, `IsolatedManager`, and
  `IsolatedWorkspaceBinding` from the crate root.
- Keep compatibility aliases only where wire contract requires them.

## 12. Test Plan

Target test structure:

```text
crates/daemon/workspace/tests/unit/
  modes/standard_overlay.rs                 ~90 LOC
  modes/isolated_sessions.rs               ~360 LOC
  primitives/overlay_dirs_capture.rs       ~120 LOC

crates/daemon/core/tests/unit/workspace_runtime/
  mod.rs                                    ~35 LOC
  lifecycle.rs                             ~190 LOC
  routing.rs                               ~220 LOC
  mode_gate.rs                             ~150 LOC
  cancel.rs                                ~180 LOC
  remount.rs                               ~180 LOC
  trace.rs                                 ~220 LOC

crates/daemon/core/tests/
  workspace_read_paths.rs                ~1,050 LOC
  workspace_write_paths.rs                 ~330 LOC
  workspace_command_paths.rs               ~260 LOC
```

Add coverage for:

- `workspace_root` compatibility parsing from legacy `layer_stack_root`.
- Standard command/file routes resolve storage internally.
- Isolated command/file routes use open handle binding without caller storage
  root.
- `destroy` cancels commands before isolated teardown.
- `capture_changes` rejects or quiesces active commands.
- Route metadata remains wire stable.
- Isolated destroy releases lease even when teardown reports cleanup details.

Focused gates:

```sh
cargo test -p workspace
cargo test -p operation file
cargo test -p daemon workspace_runtime
cargo test -p daemon --test workspace_read_paths --test workspace_write_paths --test workspace_command_paths
```

Live E2E gates after packaging:

```sh
cargo run -p xtask -- package
cargo test -p e2e-test --features e2e --no-run
```

Then run focused suites:

- `core`
- `workspace-runtime-command`
- `workspace-runtime-isolated`
- standard/ephemeral workspace suite
- pressure cross-mode suite

## 13. Acceptance Criteria

- Caller-facing APIs use `workspace_root`, not `layer_stack_root`.
- `BaseRevision` and `LeasedBaseRevision` replace public/internal snapshot
  naming.
- Overlay internals live under `overlay/`.
- Standard and isolated modes are parallel under `network_mode/`.
- `destroy` never publishes.
- `capture_changes` never publishes.
- LayerStack remains the only owner of publish/OCC.
- Command process lifecycle remains in `CommandOps`.
- Existing wire behavior remains compatible during migration.
