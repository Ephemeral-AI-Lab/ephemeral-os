//! Pure `tool_api` helpers: each builds a daemon payload from a typed request,
//! calls a [`SandboxTransport`](crate::SandboxTransport), and parses the JSON
//! envelope into a typed result. No audit wrapping (that lives in `eos-tools`)
//! and no clock/dispatch-timing recording (the caller records that).

pub(crate) mod parse;

mod command;
mod control;
mod edit;
mod isolated;
mod plugin;
mod read;
mod write;

pub use command::{
    cancel_command_session, collect_command_completions, exec_command, exec_stdin,
    read_command_progress,
};
pub use control::{cancel, command_session_count, heartbeat, inflight_count, isolated_active};
pub use edit::edit_file;
pub use isolated::{enter_isolated_workspace, exit_isolated_workspace};
pub(crate) use plugin::plugin_ensure_payload;
pub use plugin::{
    ensure_plugin_package, plugin_dispatch, plugin_ensure, PluginDependencyScope,
    PluginDispatchRequest, PluginEnsureRequest, PluginManifestDescriptor,
    PluginOperationDescriptor, PluginPackageContract, PluginPackageDescriptor,
    PluginPackageEnsureRequest, PluginPackageFile, PluginPackageTree, PluginRefreshStrategy,
    PluginServiceDescriptor, PluginServiceMode, PluginSetupDescriptor,
};
pub use read::read_file;
pub use write::write_file;
