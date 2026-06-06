//! `eos-backend-runtime` — backend-owned sandbox lifecycle and run orchestration:
//! `SandboxManager` (implements the `SandboxGateway` port), `RunLauncher`,
//! cancellation/reaper, and the replay-safe `EventBus`.
//!
//! Scaffolded in Phase 1 (workspace shape); the manager lands in Phase 4 and the
//! launcher/event bus in Phase 5.
