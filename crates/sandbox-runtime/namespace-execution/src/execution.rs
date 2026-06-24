use crate::error::NamespaceExecutionError;
use crate::id::NamespaceExecutionId;
use crate::promise::CompletionPromise;

/// Genus: id + completion promise.
pub struct ExecutionHandle<T> {
    id: NamespaceExecutionId,
    promise: CompletionPromise<T>,
}

impl<T> ExecutionHandle<T> {
    pub fn new(id: NamespaceExecutionId, promise: CompletionPromise<T>) -> Self {
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

/// Species: an `ExecutionHandle` plus interactive (PTY) capability.
/// Phase 1 carries the handle only; the PTY field + stdin/stream/cancel are
/// deferred to Phase 2.
pub struct InteractiveExecution<T> {
    exec: ExecutionHandle<T>,
}

impl<T> InteractiveExecution<T> {
    pub fn new(exec: ExecutionHandle<T>) -> Self {
        Self { exec }
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

    pub fn wait(self) -> Result<T, NamespaceExecutionError> {
        self.exec.wait()
    }
}
