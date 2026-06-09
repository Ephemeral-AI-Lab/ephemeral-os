//! Backend-facing agent-core request service.
//!
//! This crate exposes the concrete service used by backend HTTP handlers. It
//! owns request-level orchestration only; HTTP routing stays in `backend-server`,
//! active run handles stay in `eos-agent-run`, and record readers stay in
//! `eos-engine`.
#![forbid(unsafe_code)]
#![warn(missing_docs)]

mod dto;
mod error;
mod service;
mod user_request;

pub use dto::{
    CancelUserRequestInput, CancelUserRequestOutput, CreateUserRequestInput,
    CreateUserRequestOutput, UserRequestDetail, UserRequestSummary,
};
pub use error::AgentCoreServerError;
pub use service::{AgentCoreService, AgentCoreServiceDependencies, AgentCoreServiceSettings};
