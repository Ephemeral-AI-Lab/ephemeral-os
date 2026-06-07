//! Shared snapshot-lease and lifecycle value objects.
//!
//! Both ephemeral and isolated workspace runs borrow a LayerStack snapshot to
//! mount their overlay and track create/last-activity timestamps for TTL
//! sweeps. These are pure value objects with no daemon, LayerStack, or OCC
//! dependency; releasing a lease is rollback/teardown and NEVER a publish.

use std::path::PathBuf;

use serde::{Deserialize, Serialize};

/// Snapshot lease material needed to mount a fresh overlay (snapshot/lease HINGE
/// only).
///
/// Carries the lease id, the manifest coordinates captured at acquire time, and
/// the lower-layer paths the overlay mounts (newest-first). Releasing the lease
/// is rollback/teardown — it NEVER publishes upperdir changes.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct SnapshotLease {
    /// Lease id to release on exit/rollback.
    pub lease_id: String,
    /// Active manifest version captured at acquire time.
    pub manifest_version: i64,
    /// Active manifest root hash captured at acquire time.
    pub manifest_root_hash: String,
    /// Lower-layer paths to feed the overlay mount (newest-first).
    pub layer_paths: Vec<PathBuf>,
}

/// Create/last-activity timestamps for a workspace run (TTL sweep input).
#[derive(Debug, Clone, Copy, PartialEq, Serialize, Deserialize)]
pub struct Lifecycle {
    /// Monotonic create time (seconds).
    pub created_at: f64,
    /// Monotonic last-activity time (seconds); TTL input.
    pub last_activity: f64,
}
