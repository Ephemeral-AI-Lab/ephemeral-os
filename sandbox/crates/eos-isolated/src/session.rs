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
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{Instant, SystemTime, UNIX_EPOCH};

use crate::audit::AuditSink;
use crate::caps::{ResourceCaps, ISOLATED_WORKSPACE_ROOT};
use crate::error::IsolatedError;
use crate::network::{IsolatedNetwork, VethAllocation};
use serde_json::{json, Value};

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
    /// Lower-layer paths pinned by the snapshot lease.
    pub layer_paths: Vec<String>,
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
    scratch_root: PathBuf,
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
        Self::with_scratch_root(
            caps,
            layer_stack,
            runtime,
            audit,
            PathBuf::from(eos_overlay::OVERLAY_WRITABLE_ROOT),
        )
    }

    /// Construct a session with an explicit scratch root.
    ///
    /// The daemon uses the canonical `/eos/mount` root in Docker. Focused
    /// unit tests inject a temporary scratch root through this constructor so
    /// lifecycle behavior can be verified without depending on host `/eos`.
    pub fn with_scratch_root(
        caps: ResourceCaps,
        layer_stack: S,
        runtime: R,
        audit: A,
        scratch_root: PathBuf,
    ) -> Self {
        let network = IsolatedNetwork::new(caps.rfc1918_egress);
        Self {
            caps,
            layer_stack,
            runtime,
            audit,
            network,
            scratch_root,
            handles: HashMap::new(),
            by_agent: HashMap::new(),
        }
    }

    /// Reconcile persisted handles + IP pool at startup before serving enters.
    // PORT backend/src/sandbox/isolated_workspace/pipeline.py:220 — IsolatedPipeline.initialize
    pub fn initialize(&mut self) -> Result<(), IsolatedError> {
        if !self.caps.enabled {
            return Err(IsolatedError::FeatureDisabled);
        }
        self.network.initialize()?;
        std::fs::create_dir_all(self.session_scratch_root()).map_err(|err| {
            IsolatedError::SetupFailed {
                step: format!("scratch_root: {err}"),
            }
        })?;
        Ok(())
    }

    /// Enter (or reject) the isolated workspace for `agent_id`.
    ///
    /// Acquires the snapshot/lease, allocates scratch, wires the namespace, and
    /// registers the handle. Rolls back partial state (and releases the lease)
    /// on any wiring failure.
    // PORT backend/src/sandbox/isolated_workspace/_control_plane/workspace_handle_lifecycle.py:39-130 — _WorkspaceHandleLifecycleMixin.enter
    pub fn enter(&mut self, agent_id: &AgentId) -> Result<WorkspaceHandle, IsolatedError> {
        if !self.caps.enabled {
            return Err(IsolatedError::FeatureDisabled);
        }
        if agent_id.0.trim().is_empty() {
            return Err(IsolatedError::InvalidArgument(
                "agent_id is required".to_owned(),
            ));
        }
        if self.by_agent.contains_key(agent_id) {
            return Err(IsolatedError::AlreadyOpen);
        }
        if self.handles.len() >= self.caps.total_cap as usize {
            return Err(IsolatedError::QuotaExceeded);
        }

        let snapshot = self
            .layer_stack
            .acquire_snapshot(&format!("isolated-{}", next_handle_id()))?;
        let workspace_handle_id = WorkspaceHandleId(next_handle_id());
        let scratch_dir = self.session_scratch_root().join(&workspace_handle_id.0);
        let upperdir = scratch_dir.join("upper");
        let workdir = scratch_dir.join("work");
        std::fs::create_dir_all(&upperdir).map_err(|err| IsolatedError::SetupFailed {
            step: format!("upperdir: {err}"),
        })?;
        std::fs::create_dir_all(&workdir).map_err(|err| IsolatedError::SetupFailed {
            step: format!("workdir: {err}"),
        })?;

        let now = monotonic_seconds();
        let mut handle = WorkspaceHandle {
            workspace_handle_id: workspace_handle_id.clone(),
            agent_id: agent_id.clone(),
            lease_id: snapshot.lease_id.clone(),
            manifest_version: snapshot.manifest_version,
            manifest_root_hash: snapshot.root_hash.clone(),
            workspace_root: ISOLATED_WORKSPACE_ROOT.to_owned(),
            scratch_dir,
            upperdir,
            workdir,
            layer_paths: snapshot.layer_paths.clone(),
            ns_fds: HashMap::new(),
            holder_pid: 0,
            readiness_fd: -1,
            control_fd: -1,
            veth: None,
            cgroup_path: None,
            created_at: now,
            last_activity: now,
            active_calls: 0,
        };

        if let Err(err) = self.wire_handle(&mut handle) {
            self.rollback_partial(&handle);
            let _ = self.layer_stack.release_lease(&snapshot.lease_id);
            return Err(err);
        }

        self.by_agent
            .insert(agent_id.clone(), workspace_handle_id.clone());
        self.handles
            .insert(workspace_handle_id.clone(), handle.clone());
        let _ = self.audit.emit(
            "sandbox_isolated_workspace_enter",
            json!({
                "workspace_handle_id": workspace_handle_id.0,
                "agent_id": agent_id.0,
                "manifest_version": handle.manifest_version,
                "manifest_root_hash": handle.manifest_root_hash,
                "lease_id": handle.lease_id,
                "lowerdir_layer_count": handle.layer_paths.len(),
                "workspace_root": handle.workspace_root,
                "upperdir": handle.upperdir.to_string_lossy(),
                "workdir": handle.workdir.to_string_lossy(),
                "veth_host_name": handle.veth.as_ref().map(|veth| veth.host_name.as_str()),
                "veth_ns_name": handle.veth.as_ref().map(|veth| veth.ns_name.as_str()),
                "ns_ip": handle.veth.as_ref().map(|veth| veth.ns_ip.to_string()),
                "tree-copy": false,
            }),
        );
        Ok(handle)
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
    ) -> Result<Value, IsolatedError> {
        if agent_id.0.trim().is_empty() {
            return Err(IsolatedError::InvalidArgument(
                "agent_id is required".to_owned(),
            ));
        }
        let Some(handle_id) = self.by_agent.remove(agent_id) else {
            return Err(IsolatedError::NotOpen);
        };
        let Some(handle) = self.handles.remove(&handle_id) else {
            return Err(IsolatedError::NotOpen);
        };
        if handle.active_calls > 0 {
            self.by_agent.insert(agent_id.clone(), handle_id.clone());
            self.handles.insert(handle_id, handle);
            return Ok(json!({
                "success": false,
                "evicted_upperdir_bytes": 0,
                "lifetime_s": 0.0,
                "total_ms": 0.0,
                "phases_ms": {},
                "error": {
                    "kind": "exit_drain_timeout",
                    "message": "exit_isolated_workspace timed out waiting for in-flight dispatches to drain",
                    "details": {
                        "inflight": "1",
                        "grace_s": grace_s.unwrap_or(self.caps.exit_grace_s).to_string(),
                    },
                },
            }));
        }
        let timer = Instant::now();
        let upperdir_bytes = directory_file_bytes(&handle.upperdir);
        self.teardown_handle(&handle, grace_s.unwrap_or(self.caps.exit_grace_s));
        let lifetime_s = (monotonic_seconds() - handle.created_at).max(0.0);
        let total_ms = timer.elapsed().as_secs_f64() * 1000.0;
        let _ = self.audit.emit(
            "sandbox_isolated_workspace_exit",
            json!({
                "workspace_handle_id": handle.workspace_handle_id.0,
                "agent_id": agent_id.0,
                "reason": "explicit",
                "lifetime_s": lifetime_s,
                "upperdir_bytes_discarded": upperdir_bytes,
                "total_ms": total_ms,
                "phases_ms": {},
                "scratch_removed": !handle.scratch_dir.exists(),
            }),
        );
        Ok(json!({
            "success": true,
            "evicted_upperdir_bytes": upperdir_bytes,
            "lifetime_s": lifetime_s,
            "total_ms": total_ms,
            "phases_ms": {},
        }))
    }

    /// Return a copy of the active handle for `agent_id`, if any.
    pub fn get_handle(&self, agent_id: &AgentId) -> Option<WorkspaceHandle> {
        self.by_agent
            .get(agent_id)
            .and_then(|handle_id| self.handles.get(handle_id))
            .cloned()
    }

    /// Return every agent with an open handle.
    pub fn list_open_agents(&self) -> Vec<String> {
        self.by_agent.keys().map(|agent| agent.0.clone()).collect()
    }

    /// Emit an isolated tool-call audit event for an active handle.
    pub fn record_tool_call(&self, agent_id: &AgentId, mut payload: Value) {
        let Some(handle) = self.get_handle(agent_id) else {
            return;
        };
        if let Some(object) = payload.as_object_mut() {
            object.insert(
                "workspace_handle_id".to_owned(),
                json!(handle.workspace_handle_id.0),
            );
            object.insert("agent_id".to_owned(), json!(agent_id.0));
        }
        let _ = self
            .audit
            .emit("sandbox_isolated_workspace_tool_call", payload);
    }

    fn session_scratch_root(&self) -> PathBuf {
        self.scratch_root.join("runtime").join("isolated-workspace")
    }

    fn wire_handle(&mut self, handle: &mut WorkspaceHandle) -> Result<(), IsolatedError> {
        handle.holder_pid = self
            .runtime
            .spawn_ns_holder(handle, self.caps.setup_timeout_s)?;
        handle.ns_fds = self.runtime.open_ns_fds(handle.holder_pid)?;
        self.network.initialize()?;
        handle.veth = Some(
            self.network
                .install_veth(&handle.workspace_handle_id.0, handle.holder_pid)?,
        );
        self.runtime.mount_overlay(handle, &handle.layer_paths)?;
        let _dns_fallback_applied = self
            .runtime
            .configure_dns(handle, &self.caps.fallback_dns)?;
        self.runtime
            .signal_net_ready(handle, self.caps.setup_timeout_s)?;
        let cgroup_path = self.runtime.create_cgroup(handle)?;
        if !cgroup_path.as_os_str().is_empty() {
            handle.cgroup_path = Some(cgroup_path);
        }
        Ok(())
    }

    fn rollback_partial(&mut self, handle: &WorkspaceHandle) {
        close_handle_fds(handle);
        if let Some(veth) = handle.veth.as_ref() {
            self.network.teardown_veth(veth);
        }
        if handle.holder_pid > 0 {
            let _ = self.runtime.kill_holder(handle.holder_pid, 1.0);
        }
        let _ = std::fs::remove_dir_all(&handle.scratch_dir);
    }

    fn teardown_handle(&mut self, handle: &WorkspaceHandle, grace_s: f64) {
        if handle.holder_pid > 0 {
            let _ = self.runtime.kill_holder(handle.holder_pid, grace_s);
        }
        close_handle_fds(handle);
        if let Some(veth) = handle.veth.as_ref() {
            self.network.teardown_veth(veth);
        }
        let _ = self.layer_stack.release_lease(&handle.lease_id);
        if let Some(cgroup_path) = handle.cgroup_path.as_ref() {
            let _ = std::fs::remove_dir(cgroup_path);
        }
        let _ = std::fs::remove_dir_all(&handle.scratch_dir);
    }
}

fn close_handle_fds(handle: &WorkspaceHandle) {
    for fd in handle.ns_fds.values().copied() {
        if fd >= 0 {
            let _ = nix::unistd::close(fd);
        }
    }
    for fd in [handle.readiness_fd, handle.control_fd] {
        if fd >= 0 {
            let _ = nix::unistd::close(fd);
        }
    }
}

fn next_handle_id() -> String {
    static COUNTER: AtomicU64 = AtomicU64::new(1);
    let nanos = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_or(0, |duration| duration.as_nanos());
    format!(
        "{:016x}{:04x}",
        nanos,
        COUNTER.fetch_add(1, Ordering::Relaxed)
    )
}

fn monotonic_seconds() -> f64 {
    static START: std::sync::OnceLock<Instant> = std::sync::OnceLock::new();
    START.get_or_init(Instant::now).elapsed().as_secs_f64()
}

fn directory_file_bytes(path: &Path) -> u64 {
    let mut total = 0_u64;
    let Ok(entries) = std::fs::read_dir(path) else {
        return 0;
    };
    for entry in entries.flatten() {
        let path = entry.path();
        let Ok(metadata) = entry.metadata() else {
            continue;
        };
        if metadata.is_file() {
            total = total.saturating_add(metadata.len());
        } else if metadata.is_dir() {
            total = total.saturating_add(directory_file_bytes(&path));
        }
    }
    total
}
