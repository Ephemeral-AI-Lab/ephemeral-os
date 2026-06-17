#![forbid(unsafe_code)]

pub(crate) extern crate workspace as workspace_crate;

pub mod command;
pub mod error;
pub mod services;
pub mod workspace_manager;
pub mod workspace_remount;

pub use command::CommandOperationService;
pub use error::OperationServiceError;
pub use services::OperationServices;
pub use workspace_remount::WorkspaceRemountService;
