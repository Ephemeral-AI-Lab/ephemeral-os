//! In-flight request registry: request id -> task handle, touch-by-id,
//! cancel-by-id, and TTL reaping.
use std::collections::HashMap;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::sync::{Mutex, MutexGuard, OnceLock, PoisonError};
use std::thread;
use std::time::Duration;
use std::time::Instant;

use tokio::task::AbortHandle;

/// Default TTL before an idle request is reaped (seconds).
pub const DEFAULT_TTL_S: f64 = 300.0;

/// Default reaper sweep interval (seconds).
pub const DEFAULT_REAPER_INTERVAL_S: f64 = 30.0;

/// One tracked daemon-side request.
#[derive(Debug)]
pub(crate) struct InFlightRequest {
    /// Handle to the running task.
    pub task: RequestTaskHandle,
    /// Monotonic seconds of the last touch / registration.
    pub last_seen: f64,
    /// Set once the reaper has cancelled this entry (idempotent guard).
    pub ttl_reaped: bool,
}

/// Whether a cancel request reached a target and whether it can actually stop it.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum RequestCancelResult {
    Cancelled,
    AlreadyDone,
    RunningUncancellable,
}

#[derive(Debug, Clone)]
pub(crate) enum RequestTaskHandle {
    Async(AbortHandle),
    Blocking {
        abort: AbortHandle,
        started: Arc<AtomicBool>,
    },
}

impl RequestTaskHandle {
    fn cancel(&self) -> RequestCancelResult {
        match self {
            Self::Async(abort) => {
                abort.abort();
                RequestCancelResult::Cancelled
            }
            Self::Blocking { abort, started } if !started.load(Ordering::SeqCst) => {
                abort.abort();
                RequestCancelResult::Cancelled
            }
            Self::Blocking { .. } => RequestCancelResult::RunningUncancellable,
        }
    }
}

/// Tracks daemon-side tasks by request id for cancellation + TTL cleanup.
#[derive(Debug)]
pub struct InFlightRegistry {
    pub(crate) inner: Mutex<HashMap<String, InFlightRequest>>,
    ttl_s: f64,
    reaper_interval_s: f64,
}

impl InFlightRegistry {
    /// Build a registry with explicit timing values.
    #[must_use]
    pub fn new(ttl_s: f64, reaper_interval_s: f64) -> Self {
        Self {
            inner: Mutex::new(HashMap::new()),
            ttl_s: positive_f64(ttl_s, DEFAULT_TTL_S),
            reaper_interval_s: positive_f64(reaper_interval_s, DEFAULT_REAPER_INTERVAL_S),
        }
    }

    /// Reaper sweep interval (seconds) the daemon's reaper loop sleeps between.
    pub const fn reaper_interval_s(&self) -> f64 {
        self.reaper_interval_s
    }

    // The registry is best-effort daemon control state. If another task panics
    // while holding the mutex, keep cancellation/touch cleanup available
    // instead of panicking future control operations.
    fn lock_state(&self) -> MutexGuard<'_, HashMap<String, InFlightRequest>> {
        self.inner.lock().unwrap_or_else(PoisonError::into_inner)
    }

    /// Register a task under `request_id`. Empty ids are ignored.
    pub fn register(&self, request_id: &str, abort: AbortHandle) {
        if request_id.is_empty() {
            return;
        }
        let mut state = self.lock_state();
        state.insert(
            request_id.to_owned(),
            InFlightRequest {
                task: RequestTaskHandle::Async(abort),
                last_seen: monotonic_seconds(),
                ttl_reaped: false,
            },
        );
    }

    /// Register a blocking task. Once the task has started, Tokio cannot abort
    /// its blocking closure; cancel reports that distinction instead.
    pub(crate) fn register_blocking(
        &self,
        request_id: &str,
        abort: AbortHandle,
        started: Arc<AtomicBool>,
    ) {
        if request_id.is_empty() {
            return;
        }
        let mut state = self.lock_state();
        state.insert(
            request_id.to_owned(),
            InFlightRequest {
                task: RequestTaskHandle::Blocking { abort, started },
                last_seen: monotonic_seconds(),
                ttl_reaped: false,
            },
        );
    }

    /// Remove the entry for `request_id` (the dispatch `finally` path).
    pub fn deregister(&self, request_id: &str) {
        self.lock_state().remove(request_id);
    }

    /// Return whether `request_id` is still tracked.
    pub fn contains(&self, request_id: &str) -> bool {
        self.lock_state().contains_key(request_id)
    }

    /// Cancel the task for `request_id`; returns whether an entry existed.
    pub fn cancel(&self, request_id: &str) -> bool {
        matches!(
            self.cancel_request(request_id),
            RequestCancelResult::Cancelled
        )
    }

    pub(crate) fn cancel_request(&self, request_id: &str) -> RequestCancelResult {
        let Some(task) = ({
            let state = self.lock_state();
            state.get(request_id).map(|entry| entry.task.clone())
        }) else {
            return RequestCancelResult::AlreadyDone;
        };
        task.cancel()
    }

    /// Wait briefly for the dispatch finally path to deregister `request_id`.
    pub fn wait_for_cleanup(&self, request_id: &str, timeout: Duration) -> bool {
        let deadline = Instant::now() + timeout;
        while self.contains(request_id) {
            if Instant::now() >= deadline {
                return false;
            }
            thread::sleep(Duration::from_millis(5));
        }
        true
    }

    /// Touch `last_seen` for every known id; returns how many were touched.
    pub fn touch_requests(&self, request_ids: &[String]) -> usize {
        let mut state = self.lock_state();
        let now = monotonic_seconds();
        let mut touched = 0;
        for request_id in request_ids {
            if let Some(entry) = state.get_mut(request_id).filter(|entry| !entry.ttl_reaped) {
                entry.last_seen = now;
                touched += 1;
            }
        }
        touched
    }

    /// Count all tracked requests, including foreground work.
    pub fn inflight_count(&self) -> usize {
        self.lock_state().len()
    }

    /// Cancel every entry idle past the TTL.
    pub fn ttl_sweep(&self) {
        let mut state = self.lock_state();
        let now = monotonic_seconds();
        for entry in state.values_mut() {
            if !entry.ttl_reaped && now - entry.last_seen > self.ttl_s {
                let _ = entry.task.cancel();
                entry.ttl_reaped = true;
            }
        }
    }
}

fn positive_f64(value: f64, default: f64) -> f64 {
    if value.is_finite() && value > 0.0 {
        value
    } else {
        default
    }
}

fn monotonic_seconds() -> f64 {
    static START: OnceLock<Instant> = OnceLock::new();
    START.get_or_init(Instant::now).elapsed().as_secs_f64()
}
