//! The persistent private session: enter/exit lifecycle and control-plane ports.
//!
//! `IsolatedSession` owns the per-agent persistent workspace. `enter` acquires a
//! layer-stack snapshot/lease, allocates scratch (upper/work), wires the
//! namespace (ns-holder spawn -> ns FDs -> overlay mount -> DNS -> net-ready),
//! and persists the handle. `exit` drains in-flight work, tears down the
//! namespace + network + cgroup, releases the lease, and DISCARDS the upperdir
//! (writes are captured for audit only, never published).
//! `// PORT backend/src/sandbox/isolated_workspace/_control_plane/workspace_handle_lifecycle.py:39-260`

use std::collections::HashMap;
use std::path::PathBuf;

use crate::audit::AuditSink;
use crate::caps::ResourceCaps;
use crate::error::IsolatedError;
use crate::network::{IsolatedNetwork, VethAllocation};

/// Newtype for an agent identity (the enter/exit key).
#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub struct AgentId(pub String);

/// Newtype for a per-workspace handle id.
#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub struct WorkspaceHandleId(pub String);

/// A snapshot lease borrowed from the layer stack (snapshot/lease HINGE only).
///
/// Mirrors the `acquire_snapshot` result the isolated pipeline consumes; it
/// carries the lease id, manifest coordinates, and the lower-layer paths the
/// overlay mounts. NEVER a publish transaction.
/// `// PORT backend/src/sandbox/isolated_workspace/_control_plane/workspace_handle_lifecycle.py:66-83 — acquire_snapshot result usage`
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SnapshotLease {
    /// Lease id to release on exit/rollback.
    pub lease_id: String,
    /// Active manifest version captured at acquire time.
    pub manifest_version: i64,
    /// Active manifest root hash captured at acquire time.
    pub root_hash: String,
    /// Lower-layer paths to feed the overlay mount (newest-first).
    pub layer_paths: Vec<String>,
}

/// Per-workspace state. Not a subclass of any overlay handle (C1).
/// `// PORT backend/src/sandbox/isolated_workspace/_control_plane/types.py:103-141 — IsolatedWorkspaceHandle`
#[derive(Debug, Clone)]
pub struct WorkspaceHandle {
    /// Stable handle id (also the scratch dir / veth-name seed).
    pub workspace_handle_id: WorkspaceHandleId,
    /// Owning agent.
    pub agent_id: AgentId,
    /// Snapshot lease borrowed from the layer stack.
    pub lease_id: String,
    /// Manifest version captured at acquire time.
    pub manifest_version: i64,
    /// Manifest root hash captured at acquire time.
    pub manifest_root_hash: String,
    /// Mount target inside the namespace (`/testbed`).
    pub workspace_root: String,
    /// Scratch directory root (parent of upper/work).
    pub scratch_dir: PathBuf,
    /// Overlay upperdir (DISCARDED on exit — never published).
    pub upperdir: PathBuf,
    /// Overlay workdir.
    pub workdir: PathBuf,
    /// Open namespace FDs by name (`user`/`mnt`/`pid`/`net`).
    pub ns_fds: HashMap<String, i32>,
    /// ns-holder PID (`0` = not spawned).
    pub holder_pid: i32,
    /// Readiness-pipe FD (`-1` = not opened).
    pub readiness_fd: i32,
    /// Control-pipe FD (`-1` = not opened).
    pub control_fd: i32,
    /// veth allocation, if networking is wired.
    pub veth: Option<VethAllocation>,
    /// Per-workspace cgroup path, if created.
    pub cgroup_path: Option<PathBuf>,
    /// Monotonic create time.
    pub created_at: f64,
    /// Monotonic last-activity time (TTL input).
    pub last_activity: f64,
    /// In-flight foreground call count.
    pub active_calls: u32,
}

/// Snapshot/lease HINGE port — the ONLY layer-stack surface isolated touches.
///
/// Defined here as an inverted port (`eos-daemon` injects the layer-stack-backed
/// implementation). It exposes snapshot/lease + read methods ONLY — never the
/// publish-transaction half — which is precisely why this crate links
/// `eos-layerstack`, never `eos-occ`.
/// `// PORT backend/src/sandbox/occ/layer_stack_adapter.py:31-67 — snapshot/lease half`
pub trait LayerStackSnapshotPort {
    /// Acquire a read snapshot + lease for `request_id`.
    // PORT backend/src/sandbox/occ/layer_stack_adapter.py:57 — acquire_snapshot
    fn acquire_snapshot(&self, request_id: &str) -> Result<SnapshotLease, IsolatedError>;

    /// Release the lease held by `lease_id`. Returns whether it was held.
    // PORT backend/src/sandbox/occ/layer_stack_adapter.py:66 — release_lease
    fn release_lease(&self, lease_id: &str) -> Result<bool, IsolatedError>;
}

/// Kernel-touching namespace operations the pipeline delegates to.
///
/// Inverted port: the concrete implementation spawns `eos-ns-holder` (the
/// long-lived pidns PID 1) and drives `setns` mounts/exec via `eos-runner`.
/// Both are syscall-only single-threaded crates; this trait keeps the
/// orchestration here free of those edges' details.
/// `// PORT backend/src/sandbox/isolated_workspace/_control_plane/types.py:221-256 — NamespaceRuntimePort`
/// `// PORT backend/src/sandbox/isolated_workspace/_control_plane/namespace_runtime.py:65-301 — _KernelNamespaceRuntime`
pub trait NamespaceRuntimePort {
    /// Spawn `eos-ns-holder` under `unshare(--user --net --pid --mount ...)`,
    /// wait for the `ns-up` handshake token, and return its PID.
    // PORT backend/src/sandbox/isolated_workspace/_control_plane/namespace_runtime.py:79-116 — spawn_ns_holder (ns_holder.py handshake step 1)
    fn spawn_ns_holder(
        &self,
        handle: &mut WorkspaceHandle,
        setup_timeout_s: f64,
    ) -> Result<i32, IsolatedError>;

    /// Open `/proc/<pid>/ns/{user,mnt,pid,net}` FDs for `holder_pid`.
    // PORT backend/src/sandbox/isolated_workspace/_control_plane/namespace_runtime.py:118-125 — open_ns_fds
    fn open_ns_fds(&self, holder_pid: i32) -> Result<HashMap<String, i32>, IsolatedError>;

    /// Mount the overlay inside the namespace (via `eos-runner` setns helper).
    // PORT backend/src/sandbox/isolated_workspace/_control_plane/namespace_runtime.py:127-165 — mount_overlay (setns_overlay_mount)
    fn mount_overlay(
        &self,
        handle: &WorkspaceHandle,
        layer_paths: &[String],
    ) -> Result<(), IsolatedError>;

    /// Configure DNS inside the namespace; returns whether the fallback applied.
    // PORT backend/src/sandbox/isolated_workspace/_control_plane/namespace_runtime.py:167-199 — configure_dns (configure_dns_in_ns)
    fn configure_dns(
        &self,
        handle: &WorkspaceHandle,
        fallback_dns: &str,
    ) -> Result<bool, IsolatedError>;

    /// Send `net-ready` and await the `ready` token (handshake steps 2-3).
    // PORT backend/src/sandbox/isolated_workspace/_control_plane/namespace_runtime.py:201-214 — signal_net_ready (ns_holder.py handshake)
    fn signal_net_ready(
        &self,
        handle: &WorkspaceHandle,
        setup_timeout_s: f64,
    ) -> Result<(), IsolatedError>;

    /// Create the per-workspace cgroup and return its path.
    // PORT backend/src/sandbox/isolated_workspace/_control_plane/namespace_runtime.py:216-219 — create_cgroup
    fn create_cgroup(&self, handle: &WorkspaceHandle) -> Result<PathBuf, IsolatedError>;

    /// SIGTERM (then SIGKILL after `grace_s`) the ns-holder and reap children.
    // PORT backend/src/sandbox/isolated_workspace/_control_plane/namespace_runtime.py:221-253 — kill_holder
    fn kill_holder(&self, holder_pid: i32, grace_s: f64) -> Result<(), IsolatedError>;
}

/// Owns the isolated-workspace lifecycle, namespace runtime, capacity, TTL, GC.
///
/// Generic over the injected snapshot/lease + namespace ports and audit sink so
/// `eos-daemon` wires the kernel-backed implementations and tests inject
/// doubles. Holds the per-agent / per-handle maps and the shared network state.
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
    handles: HashMap<WorkspaceHandleId, WorkspaceHandle>,
    by_agent: HashMap<AgentId, WorkspaceHandleId>,
}

impl<S, R, A> IsolatedSession<S, R, A>
where
    S: LayerStackSnapshotPort,
    R: NamespaceRuntimePort,
    A: AuditSink,
{
    /// Construct a session with injected ports, caps, and audit sink.
    pub fn new(caps: ResourceCaps, layer_stack: S, runtime: R, audit: A) -> Self {
        let network = IsolatedNetwork::new(caps.rfc1918_egress);
        Self {
            caps,
            layer_stack,
            runtime,
            audit,
            network,
            handles: HashMap::new(),
            by_agent: HashMap::new(),
        }
    }

    /// Reconcile persisted handles + IP pool at startup before serving enters.
    // PORT backend/src/sandbox/isolated_workspace/pipeline.py:220 — IsolatedPipeline.initialize
    pub fn initialize(&mut self) -> Result<(), IsolatedError> {
        let _ = (&self.caps, &self.network, &self.handles, &self.by_agent);
        // PORT backend/src/sandbox/isolated_workspace/pipeline.py:220 — startup orphan recovery + IP-pool reconciliation
        todo!("PORT pipeline.py:220 — startup orphan recovery + IP-pool reconciliation")
    }

    /// Enter (or reject) the isolated workspace for `agent_id`.
    ///
    /// Acquires the snapshot/lease, allocates scratch, wires the namespace, and
    /// registers the handle. Rolls back partial state (and releases the lease)
    /// on any wiring failure.
    // PORT backend/src/sandbox/isolated_workspace/_control_plane/workspace_handle_lifecycle.py:39-130 — _WorkspaceHandleLifecycleMixin.enter
    pub fn enter(&mut self, agent_id: &AgentId) -> Result<WorkspaceHandle, IsolatedError> {
        let _ = (agent_id, &self.layer_stack, &self.runtime, &self.audit);
        // PORT backend/src/sandbox/isolated_workspace/_control_plane/workspace_handle_lifecycle.py:39-130 — cap/dup checks, snapshot, wire, persist, emit
        todo!("PORT workspace_handle_lifecycle.py:39-130 — cap/dup checks, snapshot, wire, persist, emit")
    }

    /// Exit the isolated workspace for `agent_id`.
    ///
    /// Drains in-flight dispatches, tears down namespace/network/cgroup,
    /// releases the lease, and DISCARDS the upperdir (no publish).
    // PORT backend/src/sandbox/isolated_workspace/_control_plane/workspace_handle_lifecycle.py:207-260 — _WorkspaceHandleLifecycleMixin.exit
    pub fn exit(
        &mut self,
        agent_id: &AgentId,
        grace_s: Option<f64>,
    ) -> Result<serde_json::Value, IsolatedError> {
        let _ = (agent_id, grace_s);
        // PORT backend/src/sandbox/isolated_workspace/_control_plane/workspace_handle_lifecycle.py:207-260 — drain, teardown, release lease, discard upperdir
        todo!("PORT workspace_handle_lifecycle.py:207-260 — drain, teardown, release lease, discard upperdir")
    }
}
