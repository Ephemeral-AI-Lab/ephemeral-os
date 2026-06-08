//! Caller-keyed workspace-run service.
//!
//! The daemon owns one caller-keyed [`registry::WorkspaceRunRegistry`] (replacing
//! the former flat command-session manager singleton): each caller holds many
//! ephemeral workspace runs (1 session each) or its one isolated run (N sessions).
//! A run composes the `eos-command-session` PTY substrate with the overlay
//! (ephemeral) / namespace (isolated) state it owns directly — the snapshot lease
//! and run dirs (ephemeral) or the namespace handle (isolated). Completion
//! publishes the captured upperdir (ephemeral) or records it for audit
//! (isolated); cancellation discards it. There is no policy indirection: the run
//! calls the `eos-ephemeral-workspace` / `eos-isolated-workspace` lifecycle free
//! functions directly, so "cancel never publishes" is structural.
//!
//! This module is the daemon half of the §7 cancellation integration: command
//! lifecycle ops ([`commands`]) and the per-caller / whole-sandbox cancel surface
//! ([`cancel`]).

mod cancel;
mod commands;
mod config;
pub(crate) mod isolated;
#[cfg(target_os = "linux")]
mod manager;
mod registry;
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
