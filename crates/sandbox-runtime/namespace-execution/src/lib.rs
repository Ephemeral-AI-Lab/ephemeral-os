mod engine;
mod error;
mod execution;
mod id;
mod launcher;
mod observer;
mod promise;
mod pty;
mod registry;
mod shell;
mod target;
mod transcript;
mod transcript_rows;

pub use engine::NamespaceExecutionEngine;
pub use error::NamespaceExecutionError;
pub use execution::{ExecutionHandle, InteractiveExecution};
pub use id::NamespaceExecutionId;
pub use launcher::{NsRunnerLauncher, RunnerChild};
pub use observer::{ExecutionObserver, NoopObserver};
pub use promise::{CompletionPromise, CompletionWaiter};
pub use pty::{open_pty_pair, PtyMaster};
pub use registry::ExecutionRegistry;
pub use shell::{NamespaceExecutionTerminalStatus, RunnerOutcome, ShellOperation};
pub use target::NamespaceTarget;
pub use transcript_rows::{
    required_transcript_window, transcript_window, CommandStream, CommandTranscriptRow,
    CommandTranscriptWindow,
};
