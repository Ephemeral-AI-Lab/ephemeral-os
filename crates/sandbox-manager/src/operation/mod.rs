pub(crate) mod dispatch;
mod impls;
mod specs;

pub use dispatch::{dispatch_operation, ManagerServices};
pub use specs::{cli_operation_catalog, cli_operation_families, cli_operation_specs};
