//! Backend-owned observability defaults: bounded-queue capacities and whether
//! sandbox-internal audit events are part of v1 stats.

use serde::{Deserialize, Serialize};

use crate::loader::ConfigError;

/// Observability persistence knobs owned by backend-server.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
#[non_exhaustive]
pub struct ObsConfig {
    /// Bound on the event-bus queue feeding the async `event_log` drainer.
    pub event_queue_capacity: usize,
    /// Bound on the audit-sink queue feeding the async `obs_event` drainer.
    pub audit_queue_capacity: usize,
    /// Whether `/api/stats/events` includes sandbox daemon audit events. When
    /// `true`, the audit poller and `audit_cursor` loss accounting are required.
    pub include_sandbox_audit: bool,
}

impl ObsConfig {
    /// Enforce numeric-range constraints.
    ///
    /// # Errors
    /// [`ConfigError::OutOfRange`] when either queue capacity is zero.
    pub fn validate(&self) -> Result<(), ConfigError> {
        if self.event_queue_capacity == 0 {
            return Err(ConfigError::OutOfRange {
                field: "obs.event_queue_capacity",
                detail: "must be >= 1",
            });
        }
        if self.audit_queue_capacity == 0 {
            return Err(ConfigError::OutOfRange {
                field: "obs.audit_queue_capacity",
                detail: "must be >= 1",
            });
        }
        Ok(())
    }
}
