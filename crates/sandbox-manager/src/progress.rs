use std::sync::Arc;

#[derive(Clone)]
pub struct ProgressSink {
    emit: Arc<dyn Fn(String) + Send + Sync>,
}

impl ProgressSink {
    #[must_use]
    pub fn new<F>(emit: F) -> Self
    where
        F: Fn(String) + Send + Sync + 'static,
    {
        Self {
            emit: Arc::new(emit),
        }
    }

    #[must_use]
    pub fn noop() -> Self {
        Self::new(|_| {})
    }

    pub fn emit(&self, message: impl Into<String>) {
        (self.emit)(message.into());
    }
}
