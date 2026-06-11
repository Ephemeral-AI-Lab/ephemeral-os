//! Host-neutral sandbox runtime services.
//!
//! Plugin runtime code owns service process lifetime, PPC transport and
//! dispatch, manifest refresh, package publish/setup, `api.plugin.ensure`
//! parsing, and oneshot overlay execution. Workspace runtime code owns
//! isolated-workspace lease custody, lifecycle sweeps, and caller-keyed
//! workspace-run cancellation. Wire parsing and response shaping stay in the
//! daemon adapters; state lives on runtime instances owned by the daemon's
//! services, never in process globals.

#![forbid(unsafe_code)]

pub mod plugin;
pub mod workspace;

pub use plugin::{
    ensure, launcher, needs_upload_response, read_frame, route, EnsureOutcome, EnsureReady,
    LaunchError, LoadedPluginStatus, NsRunnerLauncher, PackageEnsureReport, PluginDispatchOutcome,
    PluginOverlayOutcome, PluginRuntime, PluginRuntimeError, PpcClient, PpcError,
    ServiceHealthReport, ServiceProcessStatus, SetupFailure, StatusOutcome,
};
pub(crate) use plugin::{package, transport};
pub use workspace::{CallerCancel, ExitOutcome, WorkspaceRuntime};
