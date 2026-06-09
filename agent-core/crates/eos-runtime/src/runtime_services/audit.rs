//! Audit service group.

use std::sync::{Arc, Mutex as StdMutex};

use crate::audit::{AuditSink, BufferedAuditShutdown};

/// Audit sink and buffered-writer shutdown lifecycle.
#[derive(Clone)]
pub(crate) struct AuditService {
    #[allow(dead_code)] // Retained so the configured audit sink stays alive until shutdown.
    pub(crate) sink: Arc<dyn AuditSink>,
    pub(crate) shutdown: Arc<StdMutex<Option<BufferedAuditShutdown>>>,
}

impl std::fmt::Debug for AuditService {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("AuditService").finish_non_exhaustive()
    }
}
