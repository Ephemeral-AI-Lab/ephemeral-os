//! Audit sink lifecycle held by the runtime graph.

use std::sync::{Arc, Mutex as StdMutex};

use super::audit::{AuditSink, BufferedAuditShutdown};

/// Audit sink and buffered-writer shutdown lifecycle.
#[derive(Clone)]
pub(crate) struct AuditRuntime {
    #[allow(dead_code)] // Retained so the configured audit sink stays alive until shutdown.
    pub(crate) sink: Arc<dyn AuditSink>,
    pub(crate) shutdown: Arc<StdMutex<Option<BufferedAuditShutdown>>>,
}

impl std::fmt::Debug for AuditRuntime {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("AuditRuntime").finish_non_exhaustive()
    }
}
