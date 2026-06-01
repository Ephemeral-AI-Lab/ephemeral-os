//! In-flight invocation registry + TTL reaper.
//!
//! This is the INVOCATION-keyed registry: id -> task handle, heartbeat ->
//! `last_seen`, cancel-by-id, and the TTL reaper loop. It is DISTINCT from the
//! per-agent isolated-workspace lifecycle gates and active PTY records — do not
//! fuse those with this invocation-keyed background-control registry.
//!
//! # The active-call Drop guard
//!
//! An [`ActiveCallGuard`] increments `active_calls` BEFORE the runtime call and
//! decrements it on drop, so a call that is genuinely in-flight is never reaped
//! even if its TTL elapses. [`InFlightRegistry::ttl_sweep`] therefore selects
//! only entries that are idle past the TTL AND have `active_calls == 0`.
//!
//! # Source divergence (noted, not silently resolved)
//!
//! * The task names `EOS_BACKGROUND_HEARTBEAT_INTERVAL_S`; the live Python uses
//!   [`ENV_TTL_S`] (`EOS_INFLIGHT_TTL_S`, default 300s) and
//!   [`ENV_REAPER_INTERVAL_S`] (`EOS_INFLIGHT_REAPER_INTERVAL_S`, default 30s).
//!   We reproduce the source env vars; the heartbeat-interval naming is a
//!   port-time reconciliation.
//! * The task's "reap only when `active_calls == 0`" is STRICTER than the
//!   Python `reap_stale`, which cancels stale *background* tasks regardless of
//!   activity. The Drop-guard structure here is what the task specifies; the
//!   `// PORT` anchor points at `reap_stale` so the reconciliation is a
//!   port-time call.
//!   `// PORT backend/src/sandbox/daemon/rpc/in_flight.py — InFlightInvocationRegistry`

use std::collections::HashMap;
use std::sync::{Mutex, MutexGuard, OnceLock, PoisonError};
use std::time::Instant;

use tokio::task::AbortHandle;

/// Default TTL before an idle background invocation is reaped (seconds).
/// `// PORT backend/src/sandbox/daemon/rpc/in_flight.py:14 — _DEFAULT_TTL_SECONDS`
pub const DEFAULT_TTL_S: f64 = 300.0;

/// Default reaper sweep interval (seconds).
/// `// PORT backend/src/sandbox/daemon/rpc/in_flight.py:15 — _DEFAULT_REAPER_INTERVAL_S`
pub const DEFAULT_REAPER_INTERVAL_S: f64 = 30.0;

/// Env override for the TTL.
/// `// PORT backend/src/sandbox/daemon/rpc/in_flight.py:16 — _ENV_TTL_S`
pub const ENV_TTL_S: &str = "EOS_INFLIGHT_TTL_S";

/// Env override for the reaper interval.
/// `// PORT backend/src/sandbox/daemon/rpc/in_flight.py:17 — _ENV_REAPER_INTERVAL_S`
pub const ENV_REAPER_INTERVAL_S: &str = "EOS_INFLIGHT_REAPER_INTERVAL_S";

/// One tracked daemon-side invocation.
/// `// PORT backend/src/sandbox/daemon/rpc/in_flight.py:20-29 — InFlightInvocation`
#[derive(Debug)]
pub struct InFlightInvocation {
    /// The invocation id (registry key).
    pub invocation_id: String,
    /// Abort handle to the running task (cancel target).
    pub abort: AbortHandle,
    /// Agent that owns this invocation (for per-agent counts).
    pub agent_id: String,
    /// The op name (for diagnostics).
    pub op: String,
    /// Monotonic seconds of the last heartbeat / registration.
    pub last_seen: f64,
    /// Whether this is a background invocation (only background entries reap).
    pub background: bool,
    /// Concurrently active runtime calls; while > 0 the entry is never reaped.
    pub active_calls: u32,
    /// Set once the reaper has cancelled this entry (idempotent guard).
    pub ttl_reaped: bool,
}

/// Tracks daemon-side tasks by invocation id for cancellation + TTL cleanup.
#[derive(Debug)]
pub struct InFlightRegistry {
    inner: Mutex<RegistryState>,
    ttl_s: f64,
    reaper_interval_s: f64,
}

#[derive(Debug, Default)]
struct RegistryState {
    by_invocation: HashMap<String, InFlightInvocation>,
    ttl_reaped_total: u64,
}

impl InFlightRegistry {
    /// Build a registry with explicit timing values.
    pub fn new(ttl_s: f64, reaper_interval_s: f64) -> Self {
        Self {
            inner: Mutex::new(RegistryState::default()),
            ttl_s: positive_f64(ttl_s, DEFAULT_TTL_S),
            reaper_interval_s: positive_f64(reaper_interval_s, DEFAULT_REAPER_INTERVAL_S),
        }
    }

    /// Build a registry, sourcing TTL / reaper interval from env (falling back
    /// to the defaults). `// PORT backend/src/sandbox/daemon/rpc/in_flight.py:34-52`
    pub fn from_env() -> Self {
        Self::new(
            env_positive_f64(ENV_TTL_S, DEFAULT_TTL_S),
            env_positive_f64(ENV_REAPER_INTERVAL_S, DEFAULT_REAPER_INTERVAL_S),
        )
    }

    /// Reaper sweep interval (seconds) the daemon's reaper loop sleeps between.
    pub fn reaper_interval_s(&self) -> f64 {
        self.reaper_interval_s
    }

    // The registry is best-effort daemon control state. If another task panics
    // while holding the mutex, keep cancellation/heartbeat cleanup available
    // instead of panicking future control operations.
    fn lock_state(&self) -> MutexGuard<'_, RegistryState> {
        self.inner.lock().unwrap_or_else(PoisonError::into_inner)
    }

    /// Register a task under `invocation_id`. Empty ids are ignored.
    // PORT backend/src/sandbox/daemon/rpc/in_flight.py:54-77 — register()
    pub fn register(
        &self,
        invocation_id: &str,
        abort: AbortHandle,
        agent_id: &str,
        op: &str,
        background: bool,
    ) {
        if invocation_id.is_empty() {
            return;
        }
        let mut state = self.lock_state();
        state.by_invocation.insert(
            invocation_id.to_owned(),
            InFlightInvocation {
                invocation_id: invocation_id.to_owned(),
                abort,
                agent_id: agent_id.to_owned(),
                op: op.to_owned(),
                last_seen: monotonic_seconds(),
                background,
                active_calls: 0,
                ttl_reaped: false,
            },
        );
    }

    /// Remove the entry for `invocation_id` (the dispatch `finally` path).
    // PORT backend/src/sandbox/daemon/rpc/in_flight.py:79-81 — deregister()
    pub fn deregister(&self, invocation_id: &str) {
        self.lock_state().by_invocation.remove(invocation_id);
    }

    /// Cancel the task for `invocation_id`; returns whether an entry existed.
    // PORT backend/src/sandbox/daemon/rpc/in_flight.py:83-88 — cancel_task()
    pub fn cancel(&self, invocation_id: &str) -> bool {
        let state = self.lock_state();
        let Some(entry) = state.by_invocation.get(invocation_id) else {
            return false;
        };
        entry.abort.abort();
        true
    }

    /// Touch `last_seen` for every known id; returns how many were touched.
    /// Backs `api.v1.heartbeat`.
    // PORT backend/src/sandbox/daemon/rpc/in_flight.py:90-98 — heartbeat()
    pub fn heartbeat(&self, invocation_ids: &[String]) -> usize {
        let mut state = self.lock_state();
        let now = monotonic_seconds();
        let mut touched = 0;
        for invocation_id in invocation_ids {
            if let Some(entry) = state.by_invocation.get_mut(invocation_id) {
                entry.last_seen = now;
                touched += 1;
            }
        }
        touched
    }

    /// Count live background invocations for `agent_id`. Backs
    /// `api.v1.inflight_count`.
    // PORT backend/src/sandbox/daemon/rpc/in_flight.py:100-106 — count_by_agent()
    pub fn count_by_agent(&self, agent_id: &str) -> usize {
        self.lock_state()
            .by_invocation
            .values()
            .filter(|entry| {
                entry.background
                    && entry.agent_id == agent_id
                    && !entry.ttl_reaped
                    && !entry.abort.is_finished()
            })
            .count()
    }

    /// Acquire an [`ActiveCallGuard`]: bumps `active_calls` so the TTL reaper
    /// cannot reap this entry while a runtime call is in flight. The guard
    /// decrements on drop.
    // PORT backend/src/sandbox/daemon/rpc/in_flight.py:54-77 — active-call bookkeeping (Drop-guard structure per task)
    pub fn enter_call<'r>(&'r self, invocation_id: &str) -> ActiveCallGuard<'r> {
        if let Some(entry) = self.lock_state().by_invocation.get_mut(invocation_id) {
            entry.active_calls = entry.active_calls.saturating_add(1);
        }
        ActiveCallGuard {
            registry: self,
            invocation_id: invocation_id.to_owned(),
        }
    }

    /// Cancel every entry idle past the TTL AND with `active_calls == 0`.
    ///
    /// Stricter than the Python `reap_stale` (which cancels stale background
    /// tasks regardless of activity) by the active-call gate the task requires.
    // PORT backend/src/sandbox/daemon/rpc/in_flight.py:118-141 — reap_stale(): select stale background, cancel, mark ttl_reaped
    pub fn ttl_sweep(&self) {
        let mut state = self.lock_state();
        let now = monotonic_seconds();
        let mut reaped = 0;
        for entry in state.by_invocation.values_mut() {
            if entry.background
                && !entry.ttl_reaped
                && entry.active_calls == 0
                && now - entry.last_seen > self.ttl_s
            {
                entry.abort.abort();
                entry.ttl_reaped = true;
                reaped += 1;
            }
        }
        state.ttl_reaped_total += reaped;
    }

    /// `(active_invocations, ttl_reaped_total)` for diagnostics.
    // PORT backend/src/sandbox/daemon/rpc/in_flight.py:108-113 — metrics()
    pub fn metrics(&self) -> (usize, u64) {
        let state = self.lock_state();
        (state.by_invocation.len(), state.ttl_reaped_total)
    }
}

/// RAII guard counting one active runtime call against an invocation.
///
/// Holding it keeps `active_calls > 0`, so [`InFlightRegistry::ttl_sweep`] never
/// reaps the entry; dropping it decrements the counter. This is the structural
/// reason an in-flight call is never reaped.
/// `// PORT backend/src/sandbox/daemon/rpc/in_flight.py — active_calls decrement (the `finally` analogue)`
#[derive(Debug)]
#[must_use = "dropping this guard decrements the active-call count; bind it for the call's duration"]
pub struct ActiveCallGuard<'r> {
    registry: &'r InFlightRegistry,
    invocation_id: String,
}

impl Drop for ActiveCallGuard<'_> {
    // PORT backend/src/sandbox/daemon/rpc/in_flight.py — decrement active_calls on call completion
    fn drop(&mut self) {
        if let Some(entry) = self
            .registry
            .lock_state()
            .by_invocation
            .get_mut(&self.invocation_id)
        {
            entry.active_calls = entry.active_calls.saturating_sub(1);
        }
    }
}

/// Read a positive `f64` env var, falling back to `default` on absent/invalid/<=0.
/// `// PORT backend/src/sandbox/daemon/rpc/in_flight.py:144-158 — _env_float / _positive_float`
fn env_positive_f64(name: &str, default: f64) -> f64 {
    match std::env::var(name) {
        Ok(raw) => match raw.trim().parse::<f64>() {
            Ok(value) => positive_f64(value, default),
            Err(_) => default,
        },
        Err(_) => default,
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

#[cfg(test)]
mod tests {
    use std::future;
    use std::sync::Arc;
    use std::thread;
    use std::time::Duration;

    use super::InFlightRegistry;

    #[tokio::test]
    async fn cancel_heartbeat_and_count_track_background_task() {
        let registry = InFlightRegistry::new(300.0, 30.0);
        let task = tokio::spawn(future::pending::<()>());
        registry.register("bg-1", task.abort_handle(), "agent-a", "api.v1.shell", true);

        assert_eq!(registry.count_by_agent("agent-a"), 1);
        assert_eq!(
            registry.heartbeat(&["bg-1".to_owned(), "missing".to_owned()]),
            1
        );
        assert!(registry.cancel("bg-1"));
        assert!(task
            .await
            .expect_err("task should be cancelled")
            .is_cancelled());
        assert_eq!(registry.count_by_agent("agent-a"), 0);

        registry.deregister("bg-1");
        assert_eq!(registry.metrics(), (0, 0));
    }

    #[tokio::test]
    async fn control_paths_recover_poisoned_registry_lock() {
        let registry = Arc::new(InFlightRegistry::new(300.0, 30.0));
        let poisoned = registry.clone();
        assert!(thread::spawn(move || {
            let _guard = poisoned.inner.lock().expect("poison test can lock");
            panic!("poison in-flight registry");
        })
        .join()
        .is_err());

        let task = tokio::spawn(future::pending::<()>());
        registry.register(
            "bg-poisoned",
            task.abort_handle(),
            "agent-a",
            "api.v1.shell",
            true,
        );

        assert_eq!(registry.count_by_agent("agent-a"), 1);
        assert_eq!(registry.heartbeat(&["bg-poisoned".to_owned()]), 1);
        {
            let _guard = registry.enter_call("bg-poisoned");
            registry.ttl_sweep();
        }
        assert!(registry.cancel("bg-poisoned"));
        assert!(task
            .await
            .expect_err("task should be cancelled")
            .is_cancelled());
        registry.deregister("bg-poisoned");
        assert_eq!(registry.metrics(), (0, 0));
    }

    #[tokio::test]
    async fn ttl_sweep_skips_active_calls_then_reaps_idle_background_task() {
        let registry = InFlightRegistry::new(0.001, 30.0);
        let task = tokio::spawn(future::pending::<()>());
        registry.register(
            "bg-ttl",
            task.abort_handle(),
            "agent-a",
            "api.v1.shell",
            true,
        );

        {
            let _active = registry.enter_call("bg-ttl");
            thread::sleep(Duration::from_millis(3));
            registry.ttl_sweep();
            assert_eq!(registry.metrics(), (1, 0));
            assert_eq!(registry.count_by_agent("agent-a"), 1);
        }

        registry.ttl_sweep();
        assert_eq!(registry.metrics(), (1, 1));
        assert!(task
            .await
            .expect_err("task should be ttl-cancelled")
            .is_cancelled());
        assert_eq!(registry.count_by_agent("agent-a"), 0);
    }
}
