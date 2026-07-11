//! Manager application services, handlers, ports, and public routing.
//!
//! Semantic declarations come from `sandbox-operation-catalog`; concrete
//! transport, provider, and daemon-process mechanics belong to composition.
#![forbid(unsafe_code)]

mod daemon_client;
mod daemon_install;
mod error;
mod export_apply;
mod model;
mod operations;
mod progress;
pub(crate) mod router;
mod runtime;
mod store;
mod workspace_roots;

pub use daemon_client::SandboxDaemonClient;
pub use daemon_install::{SandboxDaemonInstaller, StartedDaemon};
pub use error::ManagerError;
pub use export_apply::ExportApplyCaps;
pub use model::{
    SandboxDaemonEndpoint, SandboxHttpEndpoint, SandboxId, SandboxRecord, SandboxState,
    SharedBaseMount,
};
pub use operations::{
    dispatch_operation, dispatch_operation_with_progress, manager_handler_keys, operation_catalog,
    operation_families, operation_specs, ManagerServices, ObservabilitySnapshotLimits,
};
pub use progress::ProgressSink;
pub use router::SandboxManagerRouter;
pub use runtime::{CreateSandboxRequest, CreateSandboxResult, SandboxRuntime};
pub use store::SandboxStore;
pub use workspace_roots::{WorkspaceDirectory, WorkspaceDirectoryListing, WorkspaceRootPolicy};
