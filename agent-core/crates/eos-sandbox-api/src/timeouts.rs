//! Per-verb timeout policy (ported from `sandbox/api/timeouts.py`).
//!
//! File/search verbs have fixed per-verb budgets; shell/exec derive their budget
//! from the command timeout plus a dispatch grace. Control RPCs use a separate
//! `CONTROL_TIMEOUT_S` that lives in `tool_api::control`. Values are `u32`
//! seconds, matching the [`SandboxTransport::call`] `timeout_s` parameter.
//!
//! [`SandboxTransport::call`]: crate::SandboxTransport::call

/// Read-file RPC budget, seconds.
pub const READ_FILE_TIMEOUT_S: u32 = 60;
/// Write-file RPC budget, seconds.
pub const WRITE_FILE_TIMEOUT_S: u32 = 60;
/// Edit-file RPC budget, seconds.
pub const EDIT_FILE_TIMEOUT_S: u32 = 20;
/// Default command budget when a shell/exec request omits `timeout`, seconds.
pub const SHELL_DEFAULT_COMMAND_TIMEOUT_S: u32 = 60;
/// Grace added on top of the command budget for shell/exec dispatch, seconds.
pub const SHELL_DISPATCH_GRACE_S: u32 = 30;
/// Glob RPC budget, seconds.
pub const GLOB_TIMEOUT_S: u32 = 60;
/// Grep RPC budget, seconds.
pub const GREP_TIMEOUT_S: u32 = 60;

/// Dispatch timeout for a shell/exec command: the command budget
/// (`command_timeout_s`, defaulting to [`SHELL_DEFAULT_COMMAND_TIMEOUT_S`]) plus
/// [`SHELL_DISPATCH_GRACE_S`]. Mirrors `timeouts.py::shell_dispatch_timeout`.
#[must_use]
pub fn shell_dispatch_timeout(command_timeout_s: Option<u32>) -> u32 {
    command_timeout_s.unwrap_or(SHELL_DEFAULT_COMMAND_TIMEOUT_S) + SHELL_DISPATCH_GRACE_S
}

#[cfg(test)]
mod tests {
    use super::*;

    // AC-sandbox-api-07: dispatch arithmetic and per-verb constants equal the
    // Python values.
    #[test]
    fn dispatch_and_constants() {
        assert_eq!(shell_dispatch_timeout(None), 90);
        assert_eq!(shell_dispatch_timeout(Some(0)), 30);
        assert_eq!(shell_dispatch_timeout(Some(45)), 75);

        assert_eq!(READ_FILE_TIMEOUT_S, 60);
        assert_eq!(WRITE_FILE_TIMEOUT_S, 60);
        assert_eq!(EDIT_FILE_TIMEOUT_S, 20);
        assert_eq!(SHELL_DEFAULT_COMMAND_TIMEOUT_S, 60);
        assert_eq!(SHELL_DISPATCH_GRACE_S, 30);
        assert_eq!(GLOB_TIMEOUT_S, 60);
        assert_eq!(GREP_TIMEOUT_S, 60);
    }
}
