//! `eos-backend-types` — backend-owned DTOs: run status/metadata, sanitized
//! sandbox views, pagination, and the API / event / audit persistence shapes.
//!
//! This is the backend's leaf DTO crate. It owns the vocabulary the HTTP API and
//! `backend.db` agree on, encoded as typed ids, enums, and structs rather than
//! loose JSON. Two contracts are load-bearing here:
//!
//! - Sanitized sandbox responses ([`SandboxView`]) carry no daemon connection
//!   material or credentials (AC4).
//! - Model-facing ([`ToolUseId`](eos_types::ToolUseId)) and daemon-facing
//!   ([`InvocationId`](eos_types::InvocationId)) identities stay distinct in the
//!   audit/correlation DTOs and are never collapsed (AC7).
#![warn(missing_docs)]

mod audit;
mod events;
mod pagination;
mod requests;
mod sandboxes;
mod stats;

pub use audit::{AuditCursor, ObsEvent, ObsSource, SandboxCallCorrelation};
pub use events::{EventRecord, EVENT_STREAM_GAP};
pub use pagination::{Page, PageResult};
pub use requests::{
    ApiRunStatus, BackendRunStatus, ClientMeta, CreateUserRequest, CreateUserRequestResponse,
    RunMeta, RunRecord, SandboxArgs, UserRequestDetail,
};
pub use sandboxes::{SandboxState, SandboxView};
