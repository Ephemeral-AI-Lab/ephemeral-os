//! In-flight invocation registry + TTL reaper.
//!
//! This is the INVOCATION-keyed registry: id -> task handle, heartbeat ->
//! `last_seen`, cancel-by-id, and the TTL reaper loop. It is DISTINCT from the
//! per-agent dispatch drain-gate (`AgentQuiesceState`), which lives behind the
//! [`crate::ports::ChangesetProjectionPort::acquire_dispatch_slot`] impl — do
//! not fuse the two.
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
//! `// PORT backend/src/sandbox/daemon/rpc/in_flight.py — InFlightInvocationRegistry`

use std::collections::HashMap;
use std::sync::Mutex;

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
    /// Build a registry, sourcing TTL / reaper interval from env (falling back
    /// to the defaults). `// PORT backend/src/sandbox/daemon/rpc/in_flight.py:34-52`
    pub fn from_env() -> Self {
        Self {
            inner: Mutex::new(RegistryState::default()),
            ttl_s: env_positive_f64(ENV_TTL_S, DEFAULT_TTL_S),
            reaper_interval_s: env_positive_f64(ENV_REAPER_INTERVAL_S, DEFAULT_REAPER_INTERVAL_S),
        }
    }

    /// Reaper sweep interval (seconds) the daemon's reaper loop sleeps between.
    pub fn reaper_interval_s(&self) -> f64 {
        self.reaper_interval_s
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
        let _ = (&self.inner, invocation_id, abort, agent_id, op, background);
        todo!("PORT in_flight.py:54-77 — insert InFlightInvocation keyed by invocation_id, stamp last_seen")
    }

    /// Remove the entry for `invocation_id` (the dispatch `finally` path).
    // PORT backend/src/sandbox/daemon/rpc/in_flight.py:79-81 — deregister()
    pub fn deregister(&self, invocation_id: &str) {
        let _ = (&self.inner, invocation_id);
        todo!("PORT in_flight.py:79-81 — by_invocation.remove(invocation_id)")
    }

    /// Cancel the task for `invocation_id`; returns whether an entry existed.
    // PORT backend/src/sandbox/daemon/rpc/in_flight.py:83-88 — cancel_task()
    pub fn cancel(&self, invocation_id: &str) -> bool {
        let _ = (&self.inner, invocation_id);
        todo!("PORT in_flight.py:83-88 — entry.task.cancel(); return existed")
    }

    /// Touch `last_seen` for every known id; returns how many were touched.
    /// Backs `api.v1.heartbeat`.
    // PORT backend/src/sandbox/daemon/rpc/in_flight.py:90-98 — heartbeat()
    pub fn heartbeat(&self, invocation_ids: &[String]) -> usize {
        let _ = (&self.inner, invocation_ids);
        todo!("PORT in_flight.py:90-98 — bump last_seen per known id, count touched")
    }

    /// Count live background invocations for `agent_id`. Backs
    /// `api.v1.inflight_count`.
    // PORT backend/src/sandbox/daemon/rpc/in_flight.py:100-106 — count_by_agent()
    pub fn count_by_agent(&self, agent_id: &str) -> usize {
        let _ = (&self.inner, agent_id);
        todo!("PORT in_flight.py:100-106 — count background && agent_id && !done")
    }

    /// Acquire an [`ActiveCallGuard`]: bumps `active_calls` so the TTL reaper
    /// cannot reap this entry while a runtime call is in flight. The guard
    /// decrements on drop.
    // PORT backend/src/sandbox/daemon/rpc/in_flight.py:54-77 — active-call bookkeeping (Drop-guard structure per task)
    pub fn enter_call<'r>(&'r self, invocation_id: &str) -> ActiveCallGuard<'r> {
        let _ = (&self.inner, invocation_id);
        todo!(
            "PORT in_flight.py — increment active_calls for invocation_id, return ActiveCallGuard"
        )
    }

    /// Cancel every entry idle past the TTL AND with `active_calls == 0`.
    ///
    /// Stricter than the Python `reap_stale` (which cancels stale background
    /// tasks regardless of activity) by the active-call gate the task requires.
    // PORT backend/src/sandbox/daemon/rpc/in_flight.py:118-141 — reap_stale(): select stale background, cancel, mark ttl_reaped
    pub fn ttl_sweep(&self) {
        let _ = (&self.inner, self.ttl_s);
        todo!("PORT in_flight.py:118-141 — select idle>TTL && active_calls==0 && background && !ttl_reaped, cancel + mark")
    }

    /// `(active_invocations, ttl_reaped_total)` for diagnostics.
    // PORT backend/src/sandbox/daemon/rpc/in_flight.py:108-113 — metrics()
    pub fn metrics(&self) -> (usize, u64) {
        let _ = &self.inner;
        todo!("PORT in_flight.py:108-113 — (len(by_invocation), ttl_reaped_total)")
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
        // The future body decrements `active_calls` for `self.invocation_id`
        // under the registry lock. Drop must not panic, so the real impl logs +
        // swallows a missing entry rather than asserting.
        let _ = (&self.registry.inner, &self.invocation_id);
    }
}

/// Read a positive `f64` env var, falling back to `default` on absent/invalid/<=0.
/// `// PORT backend/src/sandbox/daemon/rpc/in_flight.py:144-158 — _env_float / _positive_float`
fn env_positive_f64(name: &str, default: f64) -> f64 {
    match std::env::var(name) {
        Ok(raw) => match raw.trim().parse::<f64>() {
            Ok(value) if value > 0.0 => value,
            _ => default,
        },
        Err(_) => default,
    }
}
