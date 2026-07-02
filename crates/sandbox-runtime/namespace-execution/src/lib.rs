mod engine;
mod error;
mod execution;
mod launcher;
mod promise;
mod pty;
/// All-task holder-scope quiesce for the live remount protocol: discovery is
/// the union of session cgroup members, a full `/proc` scan for
/// `ns/mnt == holder`, and the infrastructure allowlist (holder, its only
/// direct child — the pid-ns init — and the caller-supplied remount runner).
/// Everything else is SIGSTOPped, polled to `T` within the freeze budget,
/// membership-rechecked, then pin-inspected from `/proc` with exactly ONE
/// holder `mountinfo` read per session. Any pin, escape, or read uncertainty
/// resumes everything and reports `Blocked`.
pub mod quiesce;
mod registry;
mod shell;
mod transcript_rows;
mod types;

pub use engine::NamespaceExecutionEngine;
pub use error::NamespaceExecutionError;
pub use execution::{ExecutionHandle, InteractiveExecution};
pub use launcher::{NsRunnerLauncher, RunnerChild, RunnerPlacement, MAX_RUNNER_RESULT_BYTES};
pub use promise::{CompletionPromise, CompletionWaiter};
pub use pty::{open_pty_pair, PtyMaster};
pub use registry::ExecutionRegistry;
pub use shell::{NamespaceExecutionTerminalStatus, RunnerOutcome, ShellOperation};
pub use transcript_rows::{
    required_transcript_window, transcript_window, CommandStream, CommandTranscriptRow,
    CommandTranscriptWindow,
};
pub use types::{NamespaceExecutionId, NamespaceTarget};
