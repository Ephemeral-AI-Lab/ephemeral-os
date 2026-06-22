use super::dispatch::ManagerOperationEntry;
use sandbox_protocol::{CliOperationFamilySpec, CliOperationSpec};

mod management;

pub(crate) const fn cli_operation_families() -> &'static [&'static CliOperationFamilySpec] {
    management::cli_operation_families()
}

pub(crate) const fn cli_operation_specs() -> &'static [&'static CliOperationSpec] {
    management::cli_operation_specs()
}

pub(crate) fn operation_entries() -> &'static [ManagerOperationEntry] {
    management::operation_entries()
}
