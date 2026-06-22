#![forbid(unsafe_code)]

pub(crate) extern crate sandbox_runtime_workspace as workspace_crate;

mod internal;
mod operation;
mod public;

pub use internal::{layerstack, workspace_remount, workspace_session};
pub use operation::{
    ArgCliSpec, ArgKind, ArgSpec, CliOperationCatalog, CliOperationFamilySpec, CliOperationSpec,
    CliSpec, OperationExecutionSpace,
};
pub use public::{cgroup_monitor, command};

pub use cgroup_monitor::CgroupMonitorOperationService;
pub use command::CommandOperationService;
pub use internal::services::{
    CgroupMonitorRuntimeConfig, CommandRuntimeConfig, Rfc1918Egress, SandboxRuntimeConfig,
    SandboxRuntimeOperations, WorkspaceResourceCaps, WorkspaceRuntimeConfig,
};

#[must_use]
pub fn cli_operation_specs() -> &'static [&'static CliOperationSpec] {
    public::cli_operation_specs()
}

#[must_use]
pub fn cli_operation_families() -> &'static [&'static CliOperationFamilySpec] {
    public::cli_operation_families()
}

#[must_use]
pub fn cli_operation_catalog() -> CliOperationCatalog {
    CliOperationCatalog::new(
        OperationExecutionSpace::Runtime,
        cli_operation_families(),
        cli_operation_specs(),
    )
}

#[must_use]
pub fn dispatch_operation(
    operations: &SandboxRuntimeOperations,
    request: &sandbox_protocol::Request,
) -> sandbox_protocol::Response {
    public::dispatch_operation(operations, request)
}
