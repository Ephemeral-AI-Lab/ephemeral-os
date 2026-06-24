use std::io;
use std::sync::Arc;

use crate::error::NamespaceExecutionError;
use crate::id::NamespaceExecutionId;
use crate::promise::CompletionPromise;
use crate::pty::PtyMaster;

/// Genus: id + completion promise. The promise is shared (`Arc`) with the
/// watcher thread, which resolves it after the runner completes.
pub struct ExecutionHandle<T> {
    id: NamespaceExecutionId,
    promise: Arc<CompletionPromise<T>>,
}

impl<T> ExecutionHandle<T> {
    pub fn new(id: NamespaceExecutionId, promise: Arc<CompletionPromise<T>>) -> Self {
        Self { id, promise }
    }

    pub fn id(&self) -> &NamespaceExecutionId {
        &self.id
    }

    pub fn is_finished(&self) -> bool {
        self.promise.is_resolved()
    }

    pub fn wait(self) -> Result<T, NamespaceExecutionError> {
        self.promise.wait()
    }
}

/// Species: an `ExecutionHandle` plus interactive (PTY) capability — stdin,
/// output streaming, and cancel, forwarded to the `PtyMaster`.
pub struct InteractiveExecution<T> {
    exec: ExecutionHandle<T>,
    pty: PtyMaster,
}

impl<T> InteractiveExecution<T> {
    pub fn new(exec: ExecutionHandle<T>, pty: PtyMaster) -> Self {
        Self { exec, pty }
    }

    pub fn execution(&self) -> &ExecutionHandle<T> {
        &self.exec
    }

    pub fn id(&self) -> &NamespaceExecutionId {
        self.exec.id()
    }

    pub fn is_finished(&self) -> bool {
        self.exec.is_finished()
    }

    pub fn write_stdin(&self, bytes: &[u8]) -> io::Result<()> {
        self.pty.write_stdin(bytes)
    }

    pub fn read_output_since(&self, offset: u64) -> String {
        self.pty.read_output_since(offset)
    }

    pub fn output_len(&self) -> u64 {
        self.pty.output_len()
    }

    pub fn cancel(&self) {
        self.pty.cancel();
    }

    pub fn wait(self) -> Result<T, NamespaceExecutionError> {
        self.exec.wait()
    }
}
