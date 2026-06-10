//! Command-session (workspace run) family.
//!
//! The registry and lifecycle orchestration live in the `eos-command-ops`
//! crate (storage-direct: lease custody and the publish decision are its
//! policy); this module is the daemon half: the `CommandOps` singleton, the
//! config bridge, the dispatcher handlers ([`ops`]), and the wire shaping.

mod manager;
pub(crate) mod ops;
mod wire;

pub(crate) use manager::{
    active_command_sessions_for_caller, cancel_all_command_sessions,
    cleanup_command_sessions_for_caller, command_session_reaper_sweep, configure_command_sessions,
    recover_orphaned_command_sessions,
};
