//! Command-session (workspace run) family.
//!
//! The run container and lifecycle orchestration live in the
//! `eos-workspace-runtime` crate (the `eos-occ`-free composition tier); this
//! module is the daemon half: it owns the `WorkspaceRunManager` singleton and
//! the dispatcher handlers ([`ops`]), and injects the daemon-resident seams
//! (the OCC publish, resource telemetry, and isolated-audit sink) via
//! [`host_ports`]. Completion publishes the captured upperdir (ephemeral) or
//! records it for audit (isolated); cancellation discards it.

#[cfg(target_os = "linux")]
mod host_ports;
mod manager;
pub(crate) mod ops;
mod wire;

pub(crate) use manager::{
    active_command_sessions_for_caller, cancel_all_command_sessions,
    cleanup_command_sessions_for_caller, command_session_reaper_sweep,
    configure_command_sessions, recover_orphaned_command_sessions,
};
