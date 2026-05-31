//! Runner error type.
//!
//! Per the workspace non-negotiables: library errors are `thiserror` enums with
//! lowercase, punctuation-free messages and `#[from]` source conversions. The
//! kinds below mirror the failure surfaces of the Python helpers — the
//! `namespace_entrypoint_bad_json` / `namespace_entrypoint_bad_result` shapes
//! (`overlay/namespace_runner.py:182-205`) and the syscall errnos raised by
//! `setns` / `unshare` (`isolated_workspace/scripts/_setns_libc.py:18-25`).

use thiserror::Error;

/// Failures returned by the namespace runner.
#[derive(Debug, Error)]
#[non_exhaustive]
pub enum RunnerError {
    /// A namespace syscall (`unshare`, `setns`, `mount`, `move_mount`) failed.
    /// Wraps the raw `errno`-bearing OS error.
    /// `// PORT backend/src/sandbox/isolated_workspace/scripts/_setns_libc.py:18-25`
    #[error("namespace syscall failed")]
    Syscall(#[source] std::io::Error),

    /// The request payload is structurally valid JSON but cannot be executed by
    /// this runner mode.
    #[error("invalid namespace runner request: {0}")]
    InvalidRequest(String),

    /// The overlay mount port failed.
    #[error("overlay mount failed")]
    Overlay(#[source] eos_overlay::OverlayError),

    /// Spawning, exec'ing, or waiting on the child process failed.
    /// `// PORT backend/src/sandbox/overlay/namespace_runner.py:243-272`
    #[error("child process failed")]
    Child(#[source] std::io::Error),

    /// The tool ran but its result JSON could not be read or parsed (the
    /// `namespace_entrypoint_bad_json` / `bad_result` paths).
    /// `// PORT backend/src/sandbox/overlay/namespace_runner.py:182-205`
    #[error("namespace entrypoint produced an unreadable result")]
    BadResult(#[source] serde_json::Error),

    /// The tool call was cancelled; the runner killed the whole process group.
    /// `// PORT backend/src/sandbox/overlay/namespace_runner.py:172` (on_cancel)
    #[error("tool call cancelled")]
    Cancelled,

    /// The tool call exceeded its timeout; the group was SIGKILLed.
    /// `// PORT backend/src/sandbox/overlay/namespace_runner.py:265-269`
    #[error("tool call timed out")]
    TimedOut,

    /// Reached on a non-Linux host: the namespace syscalls do not exist. Lets
    /// the workspace compile and link on the macOS dev host (real runs are
    /// Linux/musl only).
    #[error("namespace runner is only supported on linux")]
    Unsupported,
}

impl From<std::io::Error> for RunnerError {
    fn from(err: std::io::Error) -> Self {
        Self::Syscall(err)
    }
}

impl From<eos_overlay::OverlayError> for RunnerError {
    fn from(err: eos_overlay::OverlayError) -> Self {
        Self::Overlay(err)
    }
}

impl From<serde_json::Error> for RunnerError {
    fn from(err: serde_json::Error) -> Self {
        Self::BadResult(err)
    }
}
