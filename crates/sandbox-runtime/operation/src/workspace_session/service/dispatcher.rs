use std::sync::{Arc, Mutex, PoisonError, Weak};
use std::thread::{self, JoinHandle};
use std::time::Duration;

use crate::workspace_crate::{HolderExitShutdown, HolderExitWait, WorkspaceError};
use crate::workspace_session::{
    HolderExitDisposition, WorkspaceSessionError, WorkspaceSessionService,
};

const HOLDER_EXIT_RETRY_BACKOFF: [Duration; 3] = [
    Duration::from_millis(10),
    Duration::from_millis(25),
    Duration::from_millis(50),
];

/// Owns the daemon's one operation-layer holder-exit dispatcher.
///
/// The thread blocks on a one-slot coalescing channel. It never polls at idle,
/// never owns a holder process, and enters the exact same session teardown
/// transaction as explicit destroy. The operations graph owns this object
/// ahead of its service Arcs so last-drop stops and joins the dispatcher while
/// the services are still alive.
#[doc(hidden)]
pub struct HolderExitDispatcher {
    shutdown: Mutex<Option<HolderExitShutdown>>,
    worker: Mutex<Option<JoinHandle<()>>>,
}

impl HolderExitDispatcher {
    /// Starts the dispatcher when the workspace backend exposes supervision.
    /// Hook backends that omit a subscription return `Ok(None)`.
    pub fn start(
        sessions: &Arc<WorkspaceSessionService>,
    ) -> Result<Option<Arc<Self>>, WorkspaceSessionError> {
        let Some(subscription) = sessions.workspace().take_holder_exit_subscription()? else {
            return Ok(None);
        };
        let (listener, shutdown) = subscription.into_parts();
        let sessions = Arc::downgrade(sessions);
        let worker = thread::Builder::new()
            .name("eos-holder-exit-dispatch".to_owned())
            .spawn(move || {
                dispatcher_loop(listener, sessions);
            })
            .map_err(|error| {
                WorkspaceSessionError::Workspace(WorkspaceError::Setup {
                    step: format!("holder exit dispatcher thread start failed: {error}"),
                })
            })?;
        Ok(Some(Arc::new(Self {
            shutdown: Mutex::new(Some(shutdown)),
            worker: Mutex::new(Some(worker)),
        })))
    }

    /// Idempotently stops and joins the blocking dispatcher.
    pub fn shutdown_and_join(&self) {
        if let Some(shutdown) = self
            .shutdown
            .lock()
            .unwrap_or_else(PoisonError::into_inner)
            .take()
        {
            shutdown.stop();
        }
        if let Some(worker) = self
            .worker
            .lock()
            .unwrap_or_else(PoisonError::into_inner)
            .take()
        {
            let _ = worker.join();
        }
    }
}

impl Drop for HolderExitDispatcher {
    fn drop(&mut self) {
        self.shutdown_and_join();
    }
}

fn dispatcher_loop(
    listener: crate::workspace_crate::HolderExitListener,
    sessions: Weak<WorkspaceSessionService>,
) {
    while listener.wait() {
        let Some(sessions) = sessions.upgrade() else {
            break;
        };
        for retry_delay in HOLDER_EXIT_RETRY_BACKOFF
            .iter()
            .copied()
            .map(Some)
            .chain(std::iter::once(None))
        {
            let outcomes = sessions.reconcile_holder_exits();
            let retry_pending = outcomes.iter().any(|outcome| {
                matches!(
                    outcome.disposition,
                    HolderExitDisposition::RetryableCleanupFailure { .. }
                )
            });
            let Some(retry_delay) = retry_delay else {
                break;
            };
            if !retry_pending {
                break;
            }
            match listener.wait_for_retry(retry_delay) {
                HolderExitWait::Wake | HolderExitWait::RetryDeadline => {}
                HolderExitWait::Shutdown => return,
            }
        }
    }
}
