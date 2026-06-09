//! `eos-backend-api` — the axum router, request/task/sandbox/stats handlers,
//! SSE milestone streaming, and the `OpenAPI` document.
//!
//! The crate exposes one composition surface: [`AppState`] (assembled by the
//! backend main from the agent-core service, sandbox registry, and store handles)
//! and [`build_router`], which wires every route in `SPEC.md`. The sandbox
//! registry remains a narrow trait because tests substitute it for the production
//! `SandboxManager`; agent-core request lifecycle flows through the concrete
//! `AgentCoreService`.
//!
//! Two contracts are load-bearing: sandbox responses ([`SandboxView`]) never
//! carry daemon connection material or credentials (AC4), and the milestone
//! stream replays persisted `event_log` rows before tailing live with no gap at
//! the handoff (AC5).
//!
//! [`SandboxView`]: eos_backend_types::SandboxView
#![warn(missing_docs)]

mod error;
mod handlers;
mod openapi;
mod router;
mod stream;

pub use router::{build_router, AppState, SandboxRegistry};
