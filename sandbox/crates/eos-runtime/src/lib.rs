//! Host-neutral sandbox runtime services.
//!
//! Plugin runtime code owns service process lifetime, PPC transport and
//! dispatch, manifest refresh, package publish/setup, `api.plugin.ensure`
//! parsing, and oneshot overlay execution. Workspace runtime code owns
//! isolated-workspace lease custody, lifecycle sweeps, and caller-keyed
//! workspace-run cancellation. The `services` module owns host-neutral runtime
//! service composition. Routing code owns typed command/file target selection between
//! isolated and direct workspace backends. Maintenance code owns runtime sweep
//! entry points. Wire parsing and response shaping stay in the daemon
//! adapters; state lives on runtime instances owned by the daemon's services,
//! never in process globals.

#![forbid(unsafe_code)]

pub mod maintenance;
pub mod plugin;
pub mod routing;
pub mod services;
pub mod workspace;

pub use plugin::{
    ensure, needs_upload_response, read_message_bytes, route, EnsureOutcome, EnsureReady,
    LaunchError, LoadedPluginStatus, NsRunnerLauncher, PackageEnsureReport, PluginDispatchOutcome,
    PluginOverlayOutcome, PluginRuntime, PluginRuntimeError, PpcClient, PpcError,
    ServiceHealthReport, ServiceProcessStatus, SetupFailure, StatusOutcome,
};
pub(crate) use plugin::{package, transport};
pub use services::RuntimeServices;
pub use workspace::{CallerCancel, ExitOutcome, WorkspaceEnterError, WorkspaceRuntime};
