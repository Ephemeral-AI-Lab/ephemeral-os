use std::collections::HashMap;
use std::path::PathBuf;

use crate::network::VethAllocation;

/// Newtype for a caller identity (the enter/exit key).
#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub struct CallerId(pub String);

/// Newtype for a per-workspace handle id.
#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub struct WorkspaceHandleId(pub String);

/// A snapshot lease borrowed from the layer stack (snapshot/lease HINGE only).
///
/// Mirrors the `acquire_snapshot` result the isolated pipeline consumes; it
/// carries the lease id, manifest coordinates, and the lower-layer paths the
/// overlay mounts. NEVER a publish transaction.
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
#[derive(Debug, Clone)]
pub struct WorkspaceHandle {
    /// Stable handle id (also the scratch dir / veth-name seed).
    pub workspace_handle_id: WorkspaceHandleId,
    /// Owning caller.
    pub caller_id: CallerId,
    /// Snapshot lease borrowed from the layer stack.
    pub lease_id: String,
    /// Manifest version captured at acquire time.
    pub manifest_version: i64,
    /// Manifest root hash captured at acquire time.
    pub manifest_root_hash: String,
    /// Visible EOS workspace mount target inside the namespace.
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
}
