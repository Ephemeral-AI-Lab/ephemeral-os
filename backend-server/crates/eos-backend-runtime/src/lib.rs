//! `eos-backend-runtime` — backend-owned sandbox lifecycle and run orchestration.
//!
//! [`SandboxManager`] (Phase 4) is the in-memory owner of sandbox setup, binding,
//! refcounting, delete policy, and teardown; it composes the Docker/daemon host
//! (`eos-sandbox-host`) behind one shared registry and implements the
//! [`SandboxGateway`](eos_sandbox_port::SandboxGateway) port so `eos-runtime` can
//! be wired against it without importing the host.
//!
//! Phase 5 adds the run orchestration that consumes the manager:
//!
//! - [`RunLauncher`] accepts a request, persists `run_meta`, drives agent-core to
//!   completion through the [`RunHost`] seam, and finalizes through the reaper;
//!   cancellation is backend-local (a [`tokio_util::sync::CancellationToken`]),
//!   never written into agent-core state.
//! - [`EventBus`] turns agent-core's synchronous stream callback into replay-safe
//!   persistence: a non-async classifying callback, an async persist-before-
//!   broadcast drainer, and a [`EventBus::subscribe`] handoff with no gap.
//! - [`resolve_api_status`] joins backend and agent-core status into the API
//!   vocabulary.
#![warn(missing_docs)]

mod event_bus;
mod host;
mod launcher;
mod reaper;
mod sandbox_manager;
mod status;

pub use event_bus::{EventBus, EventSubscription};
pub use host::{RunHost, RunOutcome, RuntimeHost};
pub use launcher::{CancelOutcome, LaunchError, RunLauncher};
pub use sandbox_manager::{DeleteRejection, SandboxManager, SandboxManagerError};
pub use status::resolve_api_status;

// Shared Phase 5 test doubles/helpers (manager fakes, temp store, fake run host).
// Body lives under the crate `tests/` tree (spec §Backend Test Layout); declared
// here so the reaper/launcher test modules can reach it crate-internally and the
// fakes can implement the `pub(crate)` `SandboxTeardown` seam.
#[cfg(test)]
#[path = "../tests/support/mod.rs"]
mod test_support;
