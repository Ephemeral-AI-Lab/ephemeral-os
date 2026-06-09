//! [`Reaper`] — the single run-finalization path: release the sandbox ref, close
//! the event stream, and stamp the terminal `run_meta` row.
//!
//! Exactly one reap runs per request: the launcher's spawned task is the sole
//! finalizer (it races the run future against the cancellation token and reaps
//! whichever way that resolves). [`SandboxManager::release`] is idempotent per
//! request id as a backstop, so a stray double-reap still releases the sandbox ref
//! exactly once and tears an ephemeral sandbox down once.

use std::sync::Arc;

use eos_backend_store::RunMetaRepo;
use eos_backend_types::BackendRunStatus;
use eos_types::{RequestId, UtcDateTime};

use crate::event_bus::EventBus;
use crate::sandbox_manager::SandboxManager;

/// How a run finalized, resolved by the launcher task and mapped here to the
/// terminal [`BackendRunStatus`].
#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) enum Disposition {
    /// The host run resolved `Done`.
    Done,
    /// The host run resolved `Failed`, or provisioning/launch failed before it.
    Failed,
    /// The run was cancelled; carries the backend-local cancel reason.
    Cancelled(Option<String>),
}

/// Run finalizer: shares the manager, run-meta repo, and event bus with the
/// launcher (all cheap `Arc`/pool clones over the same state).
#[derive(Debug)]
pub(crate) struct Reaper {
    manager: Arc<SandboxManager>,
    run_meta: RunMetaRepo,
    event_bus: Arc<EventBus>,
}

impl Reaper {
    /// Build a reaper over the shared lifecycle collaborators.
    pub(crate) fn new(
        manager: Arc<SandboxManager>,
        run_meta: RunMetaRepo,
        event_bus: Arc<EventBus>,
    ) -> Self {
        Self {
            manager,
            run_meta,
            event_bus,
        }
    }

    /// Finalize `request_id`: release its sandbox ref (idempotent; destroys an
    /// ephemeral sandbox on its last release), close its event stream, and write
    /// the terminal `run_meta` status / `finished_at` / `cancel_reason`.
    pub(crate) async fn reap(&self, request_id: &RequestId, disposition: Disposition) {
        // Release first so an ephemeral sandbox tears down even if the run-meta
        // write below fails. Idempotent: a request holding no ref is a no-op.
        self.manager.release(request_id).await;
        self.event_bus.finish(request_id);

        let (status, cancel_reason) = match disposition {
            Disposition::Done => (BackendRunStatus::Done, None),
            Disposition::Failed => (BackendRunStatus::Failed, None),
            Disposition::Cancelled(reason) => (BackendRunStatus::Cancelled, reason),
        };
        // Terminal status is an idempotent keyed UPDATE, so retry once on a
        // transient failure. This matters most for `Cancelled`: it is never
        // written into agent-core state, so the detail handler's reconcile cannot
        // recover it the way it self-heals `Done`/`Failed` from agent-core.
        let finished_at = UtcDateTime::now();
        let mut result = self
            .run_meta
            .set_status(
                request_id,
                status,
                Some(finished_at),
                cancel_reason.as_deref(),
            )
            .await;
        if result.is_err() {
            result = self
                .run_meta
                .set_status(
                    request_id,
                    status,
                    Some(finished_at),
                    cancel_reason.as_deref(),
                )
                .await;
        }
        if let Err(err) = result {
            tracing::warn!(
                request_id = request_id.as_str(),
                error = %err,
                "reaper could not write terminal run_meta status after retry"
            );
        }
    }
}

#[cfg(test)]
#[path = "../tests/reaper/mod.rs"]
mod tests;
