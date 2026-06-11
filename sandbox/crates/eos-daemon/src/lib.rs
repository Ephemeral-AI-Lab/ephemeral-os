//! Daemon RPC server: owns transport, dispatch, in-flight tracking, and adapter
//! glue while delegating namespace, workspace, plugin, and checkpoint work to
//! sibling crates.
//!
#![forbid(unsafe_code)]

#[path = "dispatch/builtin_handlers.rs"]
pub(crate) mod builtin_handlers;
#[path = "runtime/context.rs"]
pub(crate) mod context;
#[path = "dispatch/dispatcher.rs"]
pub(crate) mod dispatcher;
#[path = "runtime/error.rs"]
pub(crate) mod error;
#[path = "runtime/invocation_registry.rs"]
pub(crate) mod invocation_registry;
pub(crate) mod op_adapter;
#[path = "runtime/request_args.rs"]
pub(crate) mod request_args;
#[path = "runtime/response.rs"]
pub(crate) mod response;
#[path = "runtime/services.rs"]
pub(crate) mod runtime_services;
#[path = "transport/server.rs"]
pub(crate) mod server;
pub mod wire;
#[path = "runtime/workspace.rs"]
pub(crate) mod workspace_runtime;

pub use context::DispatchContext;
pub use dispatcher::OpTable;
pub use invocation_registry::InFlightRegistry;
pub(crate) use invocation_registry::{DEFAULT_REAPER_INTERVAL_S, DEFAULT_TTL_S};
pub use runtime_services::RuntimeServices;
pub use server::{DaemonServer, ServerConfig};
pub use workspace_runtime::{CallerCancel, ExitOutcome, WorkspaceEnterError, WorkspaceRuntime};

pub(crate) mod config {
    pub(crate) use eos_config::configs::daemon::CommandSessionConfig;
}
