//! Shared workspace runtime primitives plus the concrete workspace network
//! modes.
//!
//! Every workspace is a private mounted workspace: fresh overlay directories
//! plus the holder-owned namespace stack used to run commands.
//! `NetworkProfile` selects the network boundary applied to that workspace; higher
//! layers decide when a workspace is created, destroyed, captured, or published.
//!
//! The shared mode keeps the private workspace overlay and holder namespace
//! stack and joins the host network namespace. The isolated mode adds a
//! dedicated network boundary with veth and bridge-port isolation.
//! `overlay` holds the filesystem contracts both modes share, while common
//! lifecycle code owns holder, namespace FD, scratch, and teardown behavior.
#![forbid(unsafe_code)]

pub mod error;
mod isolated_network_setup;
mod lifecycle;
pub mod model;
mod namespace;
pub mod overlay;
pub mod service;
pub mod session;

pub use error::WorkspaceError;
pub use model::{
    BaseRevision, CaptureChangesRequest, CapturedWorkspaceChanges, ChangedPathKind,
    CreateWorkspaceRequest, DestroyWorkspaceRequest, DestroyWorkspaceResult, LayerStackSnapshotRef,
    LayerStackSnapshotView, LeaseId, NetworkProfile, ProtectedPathDrop, ProtectedPathDropReason,
    ReadonlySnapshotHandle, WorkspaceEntry, WorkspaceEntryError, WorkspaceEntryFds,
    WorkspaceHandle, WorkspaceSessionId,
};
pub use service::{WorkspaceRuntimeHooks, WorkspaceRuntimeService};
