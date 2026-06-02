//! [`AuditEventBus`] — synchronous single-process fanout with error isolation.
//!
//! Mirrors `audit/bus.py`: the bus visits every registered sink and, for each
//! one that *reports* a recoverable failure, stashes an [`AuditDispatchError`]
//! instead of propagating it (GC-audit-04). The bus's own `publish` is therefore
//! infallible to its caller, so a misbehaving sink can never interrupt the
//! emitting domain path. Panics are out of the isolation contract (sinks
//! contract not to panic); `catch_unwind` is intentionally not used.

use std::fmt;
use std::sync::{Arc, Mutex};

use crate::error::AuditError;
use crate::event::AuditEvent;
use crate::sink::AuditSink;

/// A sink failure captured during fanout (the event + the reported error).
#[derive(Debug)]
#[non_exhaustive]
pub struct AuditDispatchError {
    /// The event whose delivery failed.
    pub event: AuditEvent,
    /// The error the sink reported.
    pub error: AuditError,
}

/// Single-process synchronous fanout bus over a fixed set of sinks.
///
/// Sinks are registered once at construction (no dynamic subscribe — YAGNI; the
/// composition root wires them). Interior `errors` state lives behind a
/// `std::sync::Mutex`; the guard is held only to push one error and is dropped
/// before the next sink call. There is no `.await` anywhere in this crate, so
/// the guard never spans one.
pub struct AuditEventBus {
    sinks: Vec<Arc<dyn AuditSink>>,
    errors: Mutex<Vec<AuditDispatchError>>,
}

impl AuditEventBus {
    /// Build a bus over the given sinks.
    #[must_use]
    pub fn new(sinks: Vec<Arc<dyn AuditSink>>) -> Self {
        Self {
            sinks,
            errors: Mutex::new(Vec::new()),
        }
    }

    /// Fan an event out to every sink. Infallible to the caller: each sink
    /// `Err` is recorded as an [`AuditDispatchError`] and delivery continues.
    pub fn publish(&self, event: &AuditEvent) {
        for sink in &self.sinks {
            if let Err(error) = sink.publish(event) {
                self.lock_errors().push(AuditDispatchError {
                    event: event.clone(),
                    error,
                });
            }
        }
    }

    /// Number of captured dispatch errors (for test harnesses).
    #[must_use]
    pub fn error_count(&self) -> usize {
        self.lock_errors().len()
    }

    /// Drain and return the captured dispatch errors.
    #[must_use]
    pub fn take_errors(&self) -> Vec<AuditDispatchError> {
        std::mem::take(&mut *self.lock_errors())
    }

    fn lock_errors(&self) -> std::sync::MutexGuard<'_, Vec<AuditDispatchError>> {
        self.errors
            .lock()
            .expect("audit bus error mutex is never poisoned (no panic while held)")
    }
}

impl fmt::Debug for AuditEventBus {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("AuditEventBus")
            .field("sinks", &self.sinks.len())
            .field("errors", &self.error_count())
            .finish()
    }
}

#[cfg(test)]
mod tests {
    #![allow(clippy::unwrap_used)] // unwrap is permitted in tests (err-no-unwrap-prod)
    use super::*;
    use crate::event::{AuditEvent, AuditSource};
    use crate::node::AuditNode;
    use eos_types::{JsonObject, TestClock, UtcDateTime};
    use std::sync::atomic::{AtomicUsize, Ordering};

    struct CountingSink {
        seen: AtomicUsize,
    }

    impl AuditSink for CountingSink {
        fn publish(&self, _event: &AuditEvent) -> Result<(), AuditError> {
            self.seen.fetch_add(1, Ordering::Relaxed);
            Ok(())
        }
    }

    struct FailingSink;

    impl AuditSink for FailingSink {
        fn publish(&self, _event: &AuditEvent) -> Result<(), AuditError> {
            Err(AuditError::Backpressure)
        }
    }

    fn sample_event() -> AuditEvent {
        let clock = TestClock::new(UtcDateTime::parse_rfc3339("2026-06-02T19:47:00Z").unwrap());
        AuditEvent::new(
            AuditSource::Engine,
            "engine.tool.started",
            AuditNode::default(),
            JsonObject::new(),
            &clock,
        )
    }

    // AC-audit-06: a failing sink is isolated — the ok sink still receives the
    // event, exactly one dispatch error is recorded, and publish returns ().
    #[test]
    fn failing_sink_is_isolated() {
        let counting = Arc::new(CountingSink {
            seen: AtomicUsize::new(0),
        });
        let bus = AuditEventBus::new(vec![
            Arc::new(FailingSink),
            Arc::clone(&counting) as Arc<dyn AuditSink>,
        ]);

        bus.publish(&sample_event());

        assert_eq!(counting.seen.load(Ordering::Relaxed), 1);
        assert_eq!(bus.error_count(), 1);
        let errors = bus.take_errors();
        assert_eq!(errors.len(), 1);
        assert!(matches!(errors[0].error, AuditError::Backpressure));
        // Drained.
        assert_eq!(bus.error_count(), 0);
    }
}
