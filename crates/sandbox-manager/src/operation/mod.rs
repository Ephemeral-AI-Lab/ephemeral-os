pub(crate) mod dispatch;
mod impls;
mod specs;

pub(crate) const PRIVATE_DAEMON_OBSERVABILITY_SNAPSHOT_OP: &str = "get_observability_snapshot";

pub use dispatch::{dispatch_operation, ManagerServices};
pub use specs::{cli_operation_catalog, cli_operation_families, cli_operation_specs};
