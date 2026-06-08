//! Core tool contract types shared across the crate.

pub(crate) mod error;
pub(crate) mod intent;
pub(crate) mod metadata;
pub(crate) mod name;
pub mod ports;
pub(crate) mod result;

pub use error::ToolError;
pub use metadata::ExecutionMetadata;
pub use ports::*;
pub use result::{OutputShape, ToolResult};
