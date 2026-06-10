//! Layer-stack storage: durable truth for the sandbox.
//!
//! # Invariant owned by this crate
//!
//! The manifest CAS is the **SINGLE linearization point**: ONE mutable
//! `manifest.json` over immutable, content-addressed layer directories, swapped
//! by an ATOMIC pointer write. There is no other place state becomes durable.
//!
//! - A **snapshot is O(1)**: it returns a [`Lease`] + the manifest's EXISTING
//!   `layer_paths`, NEVER a rendered tree. Rendering is the caller's
//!   overlay/projection concern.
//! - [`LeaseRegistry::leased_layers`] (the FULL on-disk retention set) and
//!   [`LeaseRegistry::lease_head_layers`] (the squash-keep barrier set) are
//!   **DISTINCT** sets — see [`lease`].
//! - **Squash is NON-DESTRUCTIVE** until the retaining lease releases: a layer
//!   below a lease head folds into a checkpoint, but the underlying directory
//!   stays on disk for that lease's frozen reads until release GCs it.
//!
//! # The write path lives here too
//!
//! The optimistic-concurrency commit gate (the [`commit`] machinery: routing,
//! base-hash validation, the per-root single-writer queue) and the per-root
//! [`service`] facade are part of this crate — a front door to the layer stack
//! is layer-stack responsibility. The [`route`] module owns the gitignore
//! admission oracle backing DIRECT-vs-GATED decisions.
//!
//! # The no-publish guarantee is enforced by the dependency graph
//!
//! Workspace crates (the ephemeral/isolated overlay providers) never depend on
//! this crate: they receive frozen `layer_paths` and return captured changes,
//! so the isolated path can NEVER publish — a build-time edge, not a
//! convention. Publish capability exists only in callers that link this crate
//! and route through [`service`].
//!
//! # Build-time / threading guarantee
//!
//! Single-threaded core plus a per-root reentrant write lease (the dual-layer
//! `flock` cross-process lease + in-process reentrant mutex). No tokio. The
//! reentrant write-guard requirement (a non-reentrant `Mutex` would self-deadlock)
//! is documented in [`storage_lock`].
#![forbid(unsafe_code)]

mod commit;
pub mod error;
pub(crate) mod fsutil;
pub(crate) mod lease;
mod metrics;
mod route;
pub mod service;
pub mod squash;
pub mod stack;
pub mod storage_lock;
#[cfg(test)]
mod test_fixture;
pub mod workspace_base;
pub mod workspace_binding;

// CAS types are owned by eos-protocol; re-export so downstream crates use ONE
// set of hashes/types and never redefine them.
pub use eos_cas::{
    aggregate_layer_changes, layer_digest, manifest_root_hash, LayerChange, LayerPath, LayerRef,
    Manifest,
};

pub use commit::{
    configure_auto_squash_max_depth, hash_bytes, hash_current, ChangesetResult, CommitError,
    CommitStatus, FileResult, Route,
};
pub use error::LayerStackError;
pub use metrics::LayerStackStorageMetrics;
pub use squash::{CheckpointSegment, LayerCheckpointSquasher, SquashPlan, SquashPlanEntry};
pub use stack::{LayerStack, Lease, MergedView};
pub use workspace_base::{build_workspace_base, ensure_workspace_base, WORKSPACE_BASE_LAYER_ID};
pub use workspace_binding::{
    read_workspace_binding, require_workspace_binding, WorkspaceBinding, WORKSPACE_BINDING_FILE,
};

/// Auto-squash depth target — distinct from the kernel overlayfs layer ceiling.
pub const AUTO_SQUASH_MAX_DEPTH: usize = 100;

/// Storage layout subdirectory for immutable layer directories.
pub(crate) const LAYERS_DIR: &str = "layers";

/// Storage layout subdirectory for in-flight commit/checkpoint staging dirs.
pub(crate) const STAGING_DIR: &str = "staging";

/// Active-manifest pointer filename under a storage root.
pub const ACTIVE_MANIFEST_FILE: &str = "manifest.json";

/// Sidecar directory for per-layer digests used by head-layer idempotency.
pub(crate) const LAYER_METADATA_DIR: &str = ".layer-metadata";
