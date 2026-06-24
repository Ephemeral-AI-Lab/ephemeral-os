use crate::error::NamespaceExecutionError;
use crate::id::NamespaceExecutionId;
use crate::promise::CompletionPromise;

/// Genus: id + completion promise.
pub struct ExecutionHandle<T> {
    id: NamespaceExecutionId,
    promise: CompletionPromise<T>,
}

impl<T> ExecutionHandle<T> {
    #[cfg_attr(not(test), allow(dead_code))]
    pub(crate) fn new(id: NamespaceExecutionId, promise: CompletionPromise<T>) -> Self {
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
    #[cfg_attr(not(test), allow(dead_code))]
    pub(crate) fn new(exec: ExecutionHandle<T>) -> Self {
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

#[cfg(test)]
mod tests {
    use super::{ExecutionHandle, InteractiveExecution};
    use crate::id::NamespaceExecutionId;
    use crate::promise::CompletionPromise;

    #[test]
    fn interactive_forwards_to_inner_handle() {
        let promise = CompletionPromise::<u32>::new();
        assert!(promise.resolve(Ok(7)));
        let handle = ExecutionHandle::new(
            NamespaceExecutionId("namespace_execution_1".to_owned()),
            promise,
        );
        let interactive = InteractiveExecution::new(handle);

        assert_eq!(interactive.id().0, "namespace_execution_1");
        assert_eq!(interactive.execution().id().0, "namespace_execution_1");
        assert!(interactive.is_finished());
        assert_eq!(interactive.wait().expect("resolved Ok"), 7);
    }
}
