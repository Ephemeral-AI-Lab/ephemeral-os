//! eos-types — shared id, timestamp, clock, json, and error primitives.
//!
//! This is the leaf crate of the agent-core dependency DAG: the small,
//! dependency-light value primitives every other crate shares. It holds the
//! twelve typed string ids, the [`UtcDateTime`] wrapper, the [`Clock`] trait
//! seam, the transitional [`JsonObject`] alias, and the minimal [`CoreError`].
//! It deliberately holds no domain state, status enums, SQL, HTTP, or config —
//! those belong to their owning crates.
//!
//! The public surface is re-exported flatly, so consumers write
//! `use eos_types::{TaskId, UtcDateTime, Clock, JsonObject};`.
#![forbid(unsafe_code)]
#![warn(missing_docs)]

mod error;
mod ids;
mod json;
mod time;

pub use error::CoreError;
pub use ids::{
    AgentRunId, AttemptId, CommandSessionId, InvocationId, IterationId, RequestId, SandboxId,
    SubagentSessionId, TaskId, ToolUseId, WorkflowId, WorkflowSessionId,
};
pub use json::JsonObject;
pub use time::{Clock, SystemClock, TestClock, UtcDateTime};
