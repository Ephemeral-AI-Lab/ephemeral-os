# Unified Workspace Refactor Spec

Status: Finalized Design
Date: 2026-06-17
Owner: `crates/daemon`

## Final Decisions

- The two caller-facing modes are `NetworkMode::Host` and
  `NetworkMode::IsolatedNetwork`.
- Host mode is not a fresh-runner mode. It still creates a holder-backed
  workspace namespace stack with user, mount, and PID namespaces; it only skips
  the dedicated network namespace and isolated network setup.
- Isolated-network mode uses the same holder-backed workspace namespace stack and adds
  the dedicated network namespace plus veth, DNS, and netfilter setup.
- `ns-holder` is the single workspace namespace creator for both modes.
  `ns-runner` only enters prepared workspace namespaces with `setns`.
- Caller lifecycle is explicit and caller-controlled: `create`, `run_command`,
  `capture_changes`, and `destroy`.
- Public DTOs use `workspace_root`; `layer_stack_root`, `upperdir`, `workdir`,
  namespace FDs, holder PID, cgroup path, and network device details stay
  internal.
- No `WorkspaceSession` public object is needed. `WorkspaceHandle` is the public
  token; daemon/runtime internals keep the richer state.
- Keep the `eosd ns-runner` subcommand because it is still the process used to
  execute commands after `setns`. Workspace command requests should not carry a
  runner mode enum; entering the holder namespace with `setns` is implicit.
- In workspace code, "mode" means `NetworkMode`. In the namespace subprocess
  protocol, workspace command execution always uses the setns runner path. Any
  non-setns execution path is retired from this workspace protocol.

## 1. Goal

Unify the workspace execution concepts behind one
caller-facing lifecycle:

1. `create`
2. `run_command`
3. `capture_changes`
4. `destroy`

The caller-facing API uses `workspace_root`. `layer_stack_root` remains an
internal storage detail resolved from the workspace binding.

The only caller-visible mode distinction is network behavior:

```rust
pub enum NetworkMode {
    Host,
    IsolatedNetwork,
}
```

`Host` means no dedicated network namespace: the workspace uses the host/container
network.

`IsolatedNetwork` means a dedicated network namespace with host-side
veth/bridge, namespace-side veth configuration, DNS setup, and netfilter
policy.

Namespace creation is unified: `ns-holder` creates and pins the workspace
namespace stack for both modes. `ns-runner` only enters prepared workspace
namespaces with `setns` for workspace commands.

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
| `workspace` crate | overlay dirs, upperdir capture primitives, shared holder-backed workspace lifecycle/remount primitives, isolated-network orchestration | command process registry, public operation wire shape |
| `CommandOps` | process registry, PTY/process wait, stdin/progress/cancel mechanics | snapshot leases, overlay dir allocation |
| `overlay` crate | overlayfs mount/unmount mechanics | daemon policy |
| `namespace-process` | single-threaded holder namespace creation, in-namespace setup, setns command execution | host bridge/veth/netfilter ownership, caller-facing workspace semantics |

## 4. Terminology

### `workspace_root`

The filesystem root visible to the command. This is caller-facing.
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
mechanism code bounded while moving public API into small files and keeping
shared lifecycle/remount code separate from isolated-network setup mechanisms.

```text
crates/daemon/workspace/src/                         ~5,000 LOC
  lib.rs                                                ~35
  model.rs                                             ~230
  error.rs                                              ~90
  service.rs                                           ~160
  overlay/                                             ~370
    mod.rs                                              ~20
    dirs.rs                                            ~120
    capture.rs                                         ~110
    tree.rs                                            ~120
  lifecycle/                                        ~1,120
    mod.rs                                              ~30
    create.rs                                          ~220
    destroy.rs                                         ~170
    recovery.rs                                        ~210
    leases.rs                                          ~130
    remount/                                           ~360
      mod.rs                                            ~40
      state.rs                                          ~80
      plan.rs                                          ~120
      apply.rs                                         ~120
  namespace/                                          ~940
    mod.rs                                             ~180
    plan.rs                                            ~120
    holder.rs                                          ~220
    setns_runner.rs                                    ~205
    fds.rs                                             ~115
    cgroup.rs                                          ~100
  network_mode/                                      ~280
    mod.rs                                              ~50
    host.rs                                            ~110
    isolated.rs                                ~120
  isolated_setup/                           ~1,800
    mod.rs                                              ~45
    types.rs                                           ~120
    caps.rs                                             ~80
    manager.rs                                         ~160
    setup.rs                                           ~180
    teardown.rs                                        ~110
    dns.rs                                              ~90
    rtnl.rs                                            ~190
    netfilter/mod.rs                                   ~165
    netfilter/exprs.rs                                 ~320
    netfilter/wire.rs                                  ~340
```

Public API names remain `NetworkMode::Host` and `NetworkMode::IsolatedNetwork`.
`isolated_setup/` is only the implementation namespace for veth, DNS,
netfilter, and dedicated-network setup with its paired cleanup helpers. Shared
holder lifecycle, namespace entry, cgroup handling, recovery, and remount logic
must not be hidden under `isolated_setup/`.

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

crates/daemon/namespace-process/src/          existing +120 LOC
  holder/
    namespace.rs                                      existing +80
    network.rs                                        existing +40
  runner/
    setns.rs                                          existing
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
    pub network: NetworkMode,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum NetworkMode {
    Host,
    IsolatedNetwork,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct WorkspaceHandle {
    pub id: WorkspaceSessionId,
    pub owner: CallerId,
    pub workspace_root: PathBuf,
    pub network: NetworkMode,
    pub base_revision: BaseRevision,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct WorkspaceSessionId(pub String);

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CallerId(pub String);
```

### Run Command

```rust
#[derive(Debug, Clone, PartialEq)]
pub struct RunCommandRequest {
    pub request_id: String,
    pub cmd: String,
    pub cwd: Option<PathBuf>,
    pub timeout_seconds: Option<f64>,
    pub yield_time_ms: u64,
    pub remountable: bool,
}

#[derive(Debug, Clone, PartialEq)]
pub struct RunCommandResult {
    pub status: CommandStatus,
    pub command_session_id: Option<String>,
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
    pub workspace_session_id: WorkspaceSessionId,
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
    pub workspace_session_id: WorkspaceSessionId,
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
    Host(HostWorkspaceState),
    IsolatedNetwork(IsolatedNetworkState),
}

pub(crate) struct HostWorkspaceState {
    pub ns_fds: HashMap<String, i32>,
    pub holder_pid: i32,
    pub readiness_fd: i32,
    pub control_fd: i32,
    pub cgroup_path: Option<PathBuf>,
}

pub(crate) struct IsolatedNetworkState {
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
    Host(HostCommandContext),
    IsolatedNetwork(IsolatedCommandContext),
}
```

### Namespace Plan

Both `NetworkMode` values use the same workspace namespace creation path:

```rust
pub(crate) struct NamespacePlan {
    pub user: bool,
    pub mount: bool,
    pub pid: bool,
    pub network: NamespaceNetwork,
}

pub(crate) enum NamespaceNetwork {
    Host,
    Isolated,
}
```

Mapping:

```rust
NetworkMode::Host => NamespacePlan {
    user: true,
    mount: true,
    pid: true,
    network: NamespaceNetwork::Host,
}

NetworkMode::IsolatedNetwork => NamespacePlan {
    user: true,
    mount: true,
    pid: true,
    network: NamespaceNetwork::Isolated,
}
```

`ns-holder` is the only workspace namespace creator. `ns-runner` uses `setns`
to enter holder namespaces for workspace commands.

### Runner Execution

Do not reuse the public workspace mode names for subprocess execution. The
workspace mode is `NetworkMode::{Host, IsolatedNetwork}`. Workspace command
requests should not include a runner mode enum. They always execute by entering
the holder-created namespace with `setns`.

The earlier non-setns runner path is retired. No workspace command path should
carry a runner mode enum or select an alternate runner.

## 9. Error Shape

```rust
#[derive(Debug, thiserror::Error)]
pub enum WorkspaceError {
    #[error("invalid request for {field}: {message}")]
    InvalidRequest { field: &'static str, message: String },

    #[error("workspace feature is disabled")]
    FeatureDisabled,

    #[error("workspace already open for {owner:?}")]
    AlreadyOpen { owner: CallerId, workspace_session_id: WorkspaceSessionId },

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

### Host

`NetworkMode::Host` uses the unified holder/setns workspace lifecycle without a
dedicated network namespace:

- acquire `LeasedBaseRevision`
- allocate `OverlayDirs`
- spawn holder namespace with user, mount, and PID namespaces
- do not create `NEWNET`
- do not allocate veth
- do not rewrite DNS
- do not install isolated network rules for this workspace
- mount overlay inside the holder namespace
- run commands by `setns` into the holder namespace
- capture changes from `upperdir`
- caller/runtime chooses whether to publish
- destroy kills holder, closes FDs, removes scratch dirs, and releases the lease

### Isolated Network

`NetworkMode::IsolatedNetwork` uses the same holder/setns workspace lifecycle with a
dedicated network namespace:

- acquire `LeasedBaseRevision`
- allocate `OverlayDirs`
- spawn holder namespace with user, mount, PID, and network namespaces
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

### Retired Non-Setns Path

The old non-setns runner path is no longer part of active workspace execution.
Workspace command execution must use holder-created namespaces plus `setns`.

## 11. Migration Plan

### Phase 1: Add New Names Beside Old Names

- Add `model.rs`, `error.rs`, and skeleton service/request/result types.
- Keep current `HostWorkspace`, `WorkspaceModeManager`, and
  `WorkspaceModeContext` exports.
- Add conversions from current isolated handle to new `WorkspaceHandle`.

### Phase 2: Resolve `workspace_root` Internally

- Add `ResolvedWorkspaceRoot`.
- Make new DTOs parse `workspace_root`.
- Keep compatibility parsing for legacy `layer_stack_root`.
- New code and docs emit only `workspace_root`.

### Phase 3: Move Host Overlay Ownership Out Of `CommandOps`

- Move bounded snapshot acquisition and `HostWorkspace::create` from
  command start into `WorkspaceRuntime`.
- Keep command process spawning in `CommandOps`.
- Ensure command finalization releases `LeasedBaseRevision` exactly once.

### Phase 4: Centralize Routing

- Move command/file route decisions from op adapters into `WorkspaceRuntime`.
- Adapters parse wire args and preserve route metadata, but do not choose host
  vs isolated behavior directly.

### Phase 5: Add Explicit `capture_changes`

- Implement isolated `capture_changes` over the handle `upperdir`.
- Implement host `capture_changes` over the per-workspace `upperdir`.
- Reject or quiesce active commands before walking the upperdir.
- Do not publish from this method.

### Phase 6: Move Files Into Target Structure

- Move `capture.rs`, `dirs.rs`, and `tree.rs` into `overlay/`.
- Move shared holder lifecycle, recovery, and remount logic into `lifecycle/`.
- Move namespace entry and cgroup handling into `namespace/`.
- Move only dedicated-network setup/cleanup internals into
  `isolated_setup/`.
- Keep mechanism names for veth, DNS, rtnetlink, and netfilter files.

### Phase 7: Remove Workspace Dependence On Non-Setns Init

- Make `create(NetworkMode::Host)` launch `ns-holder` with
  `NamespaceNetwork::Host`.
- Make `create(NetworkMode::IsolatedNetwork)` launch `ns-holder` with
  `NamespaceNetwork::Isolated`.
- Make workspace `run_command` always call the `setns` runner path.
- Delete the non-setns runner path from the workspace protocol once callers are
  migrated.

### Phase 8: Retire Legacy Names

- Stop exporting `HostWorkspace`, `WorkspaceModeManager`, and
  `WorkspaceModeContext` from the crate root.
- Keep compatibility aliases only where wire contract requires them.

## 12. Test Plan

Target test structure:

```text
crates/daemon/workspace/tests/unit/
  modes/host_overlay.rs                     ~90 LOC
  primitives/overlay_dirs_capture.rs       ~120 LOC

crates/daemon/core/tests/unit/workspace_runtime/
  mod.rs                                    ~35 LOC
  lifecycle.rs                             ~190 LOC
  routing.rs                               ~220 LOC
  mode_gate.rs                             ~150 LOC
  cancel.rs                                ~180 LOC
  remount.rs                               ~180 LOC
  routing_metadata.rs                      ~220 LOC

crates/daemon/core/tests/
  workspace_read_paths.rs                ~1,050 LOC
  workspace_write_paths.rs                 ~330 LOC
  workspace_command_paths.rs               ~260 LOC
```

Add coverage for:

- `workspace_root` compatibility parsing from legacy `layer_stack_root`.
- Host command/file routes resolve storage internally.
- Isolated-network command/file routes use open handle binding without caller storage
  root.
- `destroy` cancels commands before isolated teardown.
- `capture_changes` rejects or quiesces active commands.
- Route metadata remains wire stable.
- Isolated-network destroy releases lease even when teardown reports cleanup details.

Focused gates:

```sh
cargo test -p workspace
cargo test -p operation file
cargo test -p daemon workspace_runtime
cargo test -p daemon --test workspace_read_paths --test workspace_write_paths --test workspace_command_paths
```

Packaging gate:

```sh
cargo run -p xtask -- package
```

## 13. Acceptance Criteria

- Caller-facing APIs use `workspace_root`, not `layer_stack_root`.
- `BaseRevision` and `LeasedBaseRevision` replace public/internal snapshot
  naming.
- Overlay internals live under `overlay/`.
- Host and isolated mode adapters are parallel under `network_mode/`.
- Dedicated-network setup/cleanup internals live under
  `isolated_setup/`; shared lifecycle, namespace, recovery, cgroup, and
  remount code do not.
- `ns-holder` is the only namespace creator for workspace commands.
- `ns-runner` only enters prepared workspace namespaces with `setns`.
- `destroy` never publishes.
- `capture_changes` never publishes.
- LayerStack remains the only owner of publish/OCC.
- Command process lifecycle remains in `CommandOps`.
- Existing wire behavior remains compatible during migration.
