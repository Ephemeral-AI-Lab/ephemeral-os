mod service;

use crate::operation::CliOperationFamilySpec;

pub use service::{
    CgroupMonitorOperationService, CgroupMonitorServiceError, InspectCgroupMonitorInput,
    InspectCgroupMonitorOutput, ReadCgroupMonitorSamplesInput, ReadCgroupMonitorSamplesOutput,
};

pub(crate) const CGROUP_MONITOR_FAMILY: CliOperationFamilySpec = CliOperationFamilySpec {
    id: "cgroup_monitor",
    title: "Cgroup Monitor",
    summary: "Inspect cgroup resource usage and retained samples.",
    description:
        "Inspect session and command cgroup CPU, memory, IO, pressure, PID, disk, and cleanup state.",
};

const FAMILIES: &[&CliOperationFamilySpec] = &[&CGROUP_MONITOR_FAMILY];

pub(crate) fn operation_entries() -> &'static [crate::operation::OperationEntry] {
    service::operation_entries()
}

pub(crate) const fn cli_operation_families() -> &'static [&'static CliOperationFamilySpec] {
    FAMILIES
}

pub(crate) fn cli_operation_specs() -> &'static [&'static crate::operation::CliOperationSpec] {
    service::cli_operation_specs()
}
