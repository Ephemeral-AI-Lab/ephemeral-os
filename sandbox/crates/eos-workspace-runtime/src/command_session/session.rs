// On non-Linux hosts this module is compiled only for scaffold unit tests; the
// real PTY-backed half lives in [`linux`] and is gated to Linux as a whole.
#![cfg_attr(not(target_os = "linux"), allow(dead_code))]

use std::time::{Duration, Instant};

#[cfg(target_os = "linux")]
mod linux;
#[cfg(test)]
mod tests;

#[cfg(target_os = "linux")]
pub(crate) use linux::{ReapedCommand, RunningCommandSessionParts};

/// PTY/process substrate for one command session. It owns the child process,
/// the transcript, and the cancel flag — but **no** workspace policy: the run
/// that owns this session decides publish-vs-discard. Everything that touches
/// the child process lives in the Linux-only [`linux::ProcessRuntime`] half.
pub(crate) struct CommandSession {
    id: String,
    caller_id: String,
    command: String,
    started_at: Instant,
    timeout: Option<Duration>,
    #[cfg(target_os = "linux")]
    runtime: linux::ProcessRuntime,
}

pub(crate) struct CommandSessionSpec {
    pub(crate) id: String,
    pub(crate) caller_id: String,
    pub(crate) command: String,
    pub(crate) timeout_seconds: Option<f64>,
}

impl CommandSession {
    /// Process-free scaffold for registry and identity tests (and the non-Linux
    /// compile, where the daemon serves command-session ops as stubs).
    #[must_use]
    #[cfg(any(not(target_os = "linux"), test))]
    pub(crate) fn new(spec: CommandSessionSpec) -> Self {
        Self {
            id: spec.id,
            caller_id: spec.caller_id,
            command: spec.command,
            started_at: Instant::now(),
            timeout: spec.timeout_seconds.and_then(duration_from_secs_f64),
            #[cfg(target_os = "linux")]
            runtime: linux::inactive_runtime(),
        }
    }

    #[must_use]
    pub(crate) fn id(&self) -> &str {
        &self.id
    }

    #[must_use]
    pub(crate) fn caller_id(&self) -> &str {
        &self.caller_id
    }

    #[must_use]
    pub(crate) fn command(&self) -> &str {
        &self.command
    }

    #[cfg(test)]
    #[must_use]
    pub(crate) const fn started_at(&self) -> Instant {
        self.started_at
    }

    #[cfg(any(not(target_os = "linux"), test))]
    #[must_use]
    pub(crate) fn is_expired(&self, now: Instant) -> bool {
        self.timeout
            .is_some_and(|timeout| now.duration_since(self.started_at) >= timeout)
    }
}

fn duration_from_secs_f64(seconds: f64) -> Option<Duration> {
    if seconds.is_finite() && seconds > 0.0 {
        Some(Duration::from_secs_f64(seconds))
    } else {
        None
    }
}
