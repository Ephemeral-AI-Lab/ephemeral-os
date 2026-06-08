//! Agent-run lifecycle API.

#![forbid(unsafe_code)]
#![warn(missing_docs)]

mod active_runs;
mod error;
mod outcome;
mod request;
mod service;

pub use active_runs::{ActiveAgentRun, ActiveAgentRuns};
pub use error::AgentRunError;
pub use outcome::{AgentRunOutcome, AgentRunStatus};
pub use request::SpawnAgentRequest;
pub use service::AgentRunApi;
