//! Optimistic-concurrency commit: the single-writer publish queue per root.
//!
//! Invariant (MF-1): OCC owns the publish DECISION gate — N disjoint file-API
//! writes batch into ONE manifest CAS attempt; each normalized path routes to
//! exactly one of [`Route::Drop`] (`.git`), [`Route::Direct`] (gitignored),
//! [`Route::Gated`] (tracked, base-hash checked), or [`Route::Reject`]
//! (disallowed). A stale base surfaces [`OccStatus::AbortedVersion`] after the
//! bounded CAS retry. EXACTLY ONE `occ-commit-queue` writer per
//! `layer_stack_root` serializes all publishes: any second OCC entry point
//! (e.g. the PPC self-managed plugin callback) MUST route through this same
//! single writer + storage lease, never a second [`CommitQueue`] instance.
//!
//! Build-time edges: only daemon/runtime publish owners should link this crate.
//! The concrete layer-stack adapter is available behind the `layerstack` feature
//! for that daemon edge; isolated runtime paths and eos-plugin still omit OCC as
//! the build-time no-publish guarantee.

#![forbid(unsafe_code)]

pub mod commit_queue;
pub mod error;
#[cfg(feature = "layerstack")]
pub mod layerstack;
pub mod route;
pub mod service;

pub use commit_queue::{CommitQueue, CommitTransactionPort, PreparedChangeset, PublishConflict};
pub use error::OccError;
pub use route::{ChangesetResult, FileResult, OccStatus, PublishDecision, Route};
pub use service::{OccRouteProvider, OccService};
