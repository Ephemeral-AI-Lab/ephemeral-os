//! `AuditTap` — pull the daemon ring buffer via `api.audit.pull` using the
//! cursor model (`after_seq`), baselined past all pre-existing events.
//!
//! The ring is daemon-global, so a tap is only meaningful while the test holds
//! its node exclusively (the default pool lease). `reset_floor` is a stub, so we
//! never rely on it — `baseline` drains to "now" and subsequent `collect`s return
//! only events emitted after that point.

use anyhow::{Context, Result};
use eos_protocol::ops;
use serde_json::{json, Value};

use crate::client::ProtocolClient;

/// Incremental audit-event reader for one daemon.
#[derive(Debug)]
pub struct AuditTap {
    client: ProtocolClient,
    cursor: i64,
    limit: u64,
    events: Vec<Value>,
}

impl AuditTap {
    /// Baseline the cursor past every event currently in the ring.
    ///
    /// # Errors
    /// Returns an error if the initial drain pull fails.
    pub fn baseline(client: ProtocolClient, limit: u64) -> Result<Self> {
        let mut tap = Self {
            client,
            cursor: -1,
            limit,
            events: Vec::new(),
        };
        // Advance the cursor to the end of the existing buffer without retaining.
        loop {
            let batch = tap.pull_batch()?;
            let drained = batch.len();
            if drained < usize::try_from(tap.limit).unwrap_or(usize::MAX) {
                break;
            }
        }
        tap.events.clear();
        Ok(tap)
    }

    /// One `api.audit.pull` from the current cursor; advances the cursor and
    /// returns the freshly pulled events (also appended to the running log).
    fn pull_batch(&mut self) -> Result<Vec<Value>> {
        let resp = self
            .client
            .request(
                ops::API_AUDIT_PULL,
                "audit-pull",
                &json!({"after_seq": self.cursor, "limit": self.limit}),
            )
            .context("api.audit.pull")?;
        if let Some(next) = resp
            .get("cursor")
            .and_then(|c| c.get("after_seq"))
            .and_then(Value::as_i64)
        {
            self.cursor = next;
        }
        let batch: Vec<Value> = resp
            .get("events")
            .and_then(Value::as_array)
            .cloned()
            .unwrap_or_default();
        self.events.extend(batch.iter().cloned());
        Ok(batch)
    }

    /// Drain all events available now into the running log; returns how many new.
    ///
    /// # Errors
    /// Returns an error if a pull fails.
    pub fn collect(&mut self) -> Result<usize> {
        let mut total = 0;
        loop {
            let batch = self.pull_batch()?;
            total += batch.len();
            if batch.len() < usize::try_from(self.limit).unwrap_or(usize::MAX) {
                break;
            }
        }
        Ok(total)
    }

    /// All events seen since baseline.
    #[must_use]
    pub fn events(&self) -> &[Value] {
        &self.events
    }

    /// Whether any collected event has `type == event_type`.
    #[must_use]
    pub fn any(&self, event_type: &str) -> bool {
        self.events.iter().any(|ev| type_of(ev) == Some(event_type))
    }

    /// Count of collected events with `type == event_type`.
    #[must_use]
    pub fn count(&self, event_type: &str) -> usize {
        self.events
            .iter()
            .filter(|ev| type_of(ev) == Some(event_type))
            .count()
    }

    /// First collected event with `type == event_type`.
    #[must_use]
    pub fn first(&self, event_type: &str) -> Option<&Value> {
        self.events
            .iter()
            .find(|ev| type_of(ev) == Some(event_type))
    }

    /// All collected events with `type == event_type`.
    #[must_use]
    pub fn all(&self, event_type: &str) -> Vec<&Value> {
        self.events
            .iter()
            .filter(|ev| type_of(ev) == Some(event_type))
            .collect()
    }
}

/// The `type` of a pulled audit event.
#[must_use]
pub fn type_of(event: &Value) -> Option<&str> {
    event.get("type").and_then(Value::as_str)
}

/// The `payload.<section_key>` object of a pulled audit event.
#[must_use]
pub fn section<'a>(event: &'a Value, section_key: &str) -> Option<&'a Value> {
    event.get("payload")?.get(section_key)
}
