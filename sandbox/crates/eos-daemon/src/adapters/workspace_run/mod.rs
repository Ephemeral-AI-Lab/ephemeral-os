//! Caller-keyed workspace-run service.
//!
//! The run container and lifecycle orchestration live in the
//! `eos-workspace-runtime` crate (the `eos-occ`-free composition tier); this
//! module is the daemon half:
//! it owns the `WorkspaceRunManager` singleton ([`commands`]), injects the
//! daemon-resident seams (the OCC publish, resource telemetry, and isolated-audit
//! sink) via [`host_ports`], and exposes the RPC/op facade plus the per-caller /
//! whole-sandbox cancel surface ([`cancel`]). A run composes the
//! runtime PTY substrate with the overlay (ephemeral) / namespace (isolated)
//! state it owns directly; completion publishes the captured upperdir
//! (ephemeral) or records it for audit (isolated), cancellation discards it, so
//! "cancel never publishes" stays structural.
//!
//! This module is the daemon half of the §7 cancellation integration: command
//! lifecycle ops ([`commands`]) and the per-caller / whole-sandbox cancel surface
//! ([`cancel`]).

mod cancel;
mod commands;
mod config;
#[cfg(target_os = "linux")]
mod host_ports;
pub(crate) mod isolated;
mod wire;

pub(crate) use cancel::{
    cancel_workspace_runs_by_caller_id, op_cancel_workspace_runs,
    op_cancel_workspace_runs_by_caller_id,
};
pub(crate) use commands::{
    active_command_sessions_for_caller, command_session_reaper_sweep, op_command_cancel,
    op_command_collect_completed, op_command_read_progress, op_command_session_count,
    op_command_write_stdin, op_exec_command, recover_orphaned_command_sessions,
};
pub(crate) use config::configure_command_sessions;
