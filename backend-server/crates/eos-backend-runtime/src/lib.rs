//! `eos-backend-runtime` — backend-owned sandbox lifecycle and run orchestration.
//!
//! Phase 4 lands [`SandboxManager`]: the backend-owned, in-memory owner of
//! sandbox setup, binding, refcounting, delete policy, and teardown. It composes
//! the Docker/daemon host (`eos-sandbox-host`) behind one shared registry and
//! implements the [`SandboxGateway`](eos_sandbox_port::SandboxGateway) port so
//! `eos-runtime` can be wired against it without importing the host.
//!
//! `RunLauncher`, cancellation/reaper, and the replay-safe `EventBus` land in
//! Phase 5 and consume this manager.
#![warn(missing_docs)]

mod sandbox_manager;

pub use sandbox_manager::{DeleteRejection, SandboxManager, SandboxManagerError};
