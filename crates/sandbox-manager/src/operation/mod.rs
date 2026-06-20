pub mod dispatch;
mod impls;
pub mod specs;

pub use dispatch::{dispatch_operation, ManagerOperationEntry, ManagerServices};
pub use specs::{operation_catalog, operation_specs};
