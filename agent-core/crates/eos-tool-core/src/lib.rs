//! Neutral tool contracts shared by the engine and concrete tool executors.

#![forbid(unsafe_code)]
#![warn(missing_docs)]

mod error;
mod metadata;
pub mod ports;
mod result;

pub use error::ToolError;
pub use metadata::ExecutionMetadata;
pub use ports::*;
pub use result::{OutputShape, ToolResult};
