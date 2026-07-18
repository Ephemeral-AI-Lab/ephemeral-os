use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex, PoisonError, Weak};
use std::thread::{self, JoinHandle};
use std::time::Duration;

use crate::workspace_crate::{HolderExitShutdown, HolderExitWait, WorkspaceError};
use crate::workspace_session::{
    HolderExitDisposition, WorkspaceSessionError, WorkspaceSessionService,
};

// One initial teardown plus three bounded retries. Backoff exists only while a
// failed holder cleanup is pending; there is no recurring idle timer.
const HOLDER_EXIT_CLEANUP_ATTEMPTS: usize = 4;
const HOLDER_EXIT_RETRY_BACKOFF: [Duration; HOLDER_EXIT_CLEANUP_ATTEMPTS - 1] = [
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
    joined: Arc<AtomicBool>,
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
        let joined = Arc::new(AtomicBool::new(false));
        let worker_joined = Arc::clone(&joined);
        let worker = thread::Builder::new()
            .name("eos-holder-exit-dispatch".to_owned())
            .spawn(move || dispatcher_loop(listener, sessions, worker_joined))
            .map_err(|error| {
                WorkspaceSessionError::Workspace(WorkspaceError::Setup {
                    step: format!("holder exit dispatcher thread start failed: {error}"),
                })
            })?;
        Ok(Some(Arc::new(Self {
            shutdown: Mutex::new(Some(shutdown)),
            worker: Mutex::new(Some(worker)),
            joined,
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

    #[doc(hidden)]
    #[must_use]
    pub fn is_joined_for_test(&self) -> bool {
        self.joined.load(Ordering::Acquire)
    }

    #[doc(hidden)]
    #[must_use]
    pub const fn cleanup_attempt_limit_for_test() -> usize {
        HOLDER_EXIT_CLEANUP_ATTEMPTS
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
    joined: Arc<AtomicBool>,
) {
    struct JoinedOnDrop(Arc<AtomicBool>);
    impl Drop for JoinedOnDrop {
        fn drop(&mut self) {
            self.0.store(true, Ordering::Release);
        }
    }
    let _joined = JoinedOnDrop(joined);
    while listener.wait() {
        let Some(sessions) = sessions.upgrade() else {
            break;
        };
        for attempt in 0..HOLDER_EXIT_CLEANUP_ATTEMPTS {
            let outcomes = sessions.reconcile_holder_exits();
            let retry_pending = outcomes.iter().any(|outcome| {
                matches!(
                    outcome.disposition,
                    HolderExitDisposition::RetryableCleanupFailure { .. }
                )
            });
            if !retry_pending || attempt + 1 == HOLDER_EXIT_CLEANUP_ATTEMPTS {
                break;
            }
            match listener.wait_for_retry(HOLDER_EXIT_RETRY_BACKOFF[attempt]) {
                HolderExitWait::Wake | HolderExitWait::RetryDeadline => {}
                HolderExitWait::Shutdown => return,
            }
        }
    }
}
