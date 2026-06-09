//! Per-request cancellation API registry (spec §12.1).
//!
//! `cancel_agent_core_user_request` is invoked from a different task than the one
//! running `run_request`, so it needs a `services`-reachable handle to the
//! request's [`AgentCoreCancellationApi`] (which is inherently per-request — it carries that
//! request's live-run registry and workflow control). `run_request` registers
//! it on start and the returned RAII guard removes it on completion or
//! early-return, so a panic cannot leak the cancellation handle (and the registry + stores it
//! holds).

use std::collections::HashMap;
use std::sync::{Arc, Mutex};

use eos_types::{AgentCoreCancellationApi, RequestId};

/// Shared map from `RequestId` to the request's recursive cancellation API.
#[derive(Clone, Default)]
pub(crate) struct RequestCancelRegistry {
    inner: Arc<Mutex<HashMap<RequestId, Arc<dyn AgentCoreCancellationApi>>>>,
}

impl std::fmt::Debug for RequestCancelRegistry {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        let live = self.inner.lock().map(|guard| guard.len()).unwrap_or(0);
        f.debug_struct("RequestCancelRegistry")
            .field("requests", &live)
            .finish_non_exhaustive()
    }
}

impl RequestCancelRegistry {
    pub(crate) fn new() -> Self {
        Self::default()
    }

    /// Register `api` for `request_id` and return an RAII guard that removes it
    /// on drop (request completion, error, or panic).
    #[must_use]
    pub(crate) fn register(
        &self,
        request_id: RequestId,
        api: Arc<dyn AgentCoreCancellationApi>,
    ) -> RequestCancelGuard {
        self.inner
            .lock()
            .expect("cancel registry lock")
            .insert(request_id.clone(), api);
        RequestCancelGuard {
            registry: self.clone(),
            request_id,
        }
    }

    /// The cancellation API for a live request, if one is running.
    pub(crate) fn get(&self, request_id: &RequestId) -> Option<Arc<dyn AgentCoreCancellationApi>> {
        self.inner
            .lock()
            .expect("cancel registry lock")
            .get(request_id)
            .cloned()
    }

    fn remove(&self, request_id: &RequestId) {
        self.inner
            .lock()
            .expect("cancel registry lock")
            .remove(request_id);
    }
}

/// Removes the request's cancellation API from the registry on drop.
pub(crate) struct RequestCancelGuard {
    registry: RequestCancelRegistry,
    request_id: RequestId,
}

impl Drop for RequestCancelGuard {
    fn drop(&mut self) {
        self.registry.remove(&self.request_id);
    }
}
