#![forbid(unsafe_code)]

pub(crate) extern crate workspace as workspace_crate;

mod internal;
mod public;

pub use internal::{error, services, workspace_remount, workspace_session};
pub use public::command;

pub use command::CommandOperationService;
pub use error::OperationServiceError;
pub use services::DaemonOperations;
