//! `UtcDateTime` timestamp wrapper and the `Clock` trait seam.
//!
//! `UtcDateTime` wraps `OffsetDateTime` with the invariant that the offset is
//! always UTC, so Anthropic/OpenAI/DB timestamps are substitutable (LSP). RFC
//! 3339 is the single wire format, matching Rust's `datetime.now(UTC)`
//! isoformat persistence. `Clock` is the DIP seam: inject it instead of reading
//! the global wall clock so tests are deterministic.

use std::sync::RwLock;

use ::time::format_description::well_known::Rfc3339;
use ::time::{OffsetDateTime, UtcOffset};

/// A UTC instant. Wraps `OffsetDateTime`, guaranteeing the offset is always UTC.
#[repr(transparent)]
#[derive(
    Debug,
    Clone,
    Copy,
    PartialEq,
    Eq,
    PartialOrd,
    Ord,
    serde::Serialize,
    serde::Deserialize,
    schemars::JsonSchema,
)]
#[serde(transparent)]
pub struct UtcDateTime(
    #[serde(
        serialize_with = "::time::serde::rfc3339::serialize",
        deserialize_with = "deserialize_rfc3339_utc"
    )]
    #[schemars(schema_with = "rfc3339_schema")]
    OffsetDateTime,
);

impl UtcDateTime {
    /// The current instant from the system clock, normalized to UTC.
    #[must_use]
    pub fn now() -> Self {
        Self(OffsetDateTime::now_utc())
    }

    /// Wrap an `OffsetDateTime`, normalizing any offset to UTC so the wrapper's
    /// invariant (offset == UTC) holds for every value.
    #[must_use]
    pub fn from_offset(dt: OffsetDateTime) -> Self {
        Self(dt.to_offset(UtcOffset::UTC))
    }

    /// Format as an RFC 3339 string (the canonical wire form). Infallible
    /// because the workspace `time` pin omits `large-dates`, constraining years
    /// to `0..=9999` (the RFC 3339 range).
    #[must_use]
    pub fn to_rfc3339(self) -> String {
        match self.0.format(&Rfc3339) {
            Ok(formatted) => formatted,
            Err(_) => self.0.to_string(),
        }
    }

    /// Parse an RFC 3339 string, normalizing the result to UTC.
    pub fn parse_rfc3339(s: &str) -> Result<Self, crate::error::CoreError> {
        Ok(Self::from_offset(OffsetDateTime::parse(s, &Rfc3339)?))
    }

    /// Consume the wrapper, returning the inner UTC `OffsetDateTime`.
    #[must_use]
    pub fn into_inner(self) -> OffsetDateTime {
        self.0
    }
}

/// Schema override for the inner timestamp field: a JSON `string` with
/// `format: date-time`. Needed because the field serializes as an RFC 3339
/// string and `schemars` has no `time` integration to infer that.
fn rfc3339_schema(_gen: &mut schemars::gen::SchemaGenerator) -> schemars::schema::Schema {
    schemars::schema::SchemaObject {
        instance_type: Some(schemars::schema::InstanceType::String.into()),
        format: Some("date-time".to_owned()),
        ..Default::default()
    }
    .into()
}

/// Deserialize an RFC 3339 timestamp and normalize it to UTC, preserving the
/// `UtcDateTime` invariant (offset == UTC) on the wire-input path. The default
/// `time::serde::rfc3339` deserialize keeps the encoded offset, so a value like
/// `...+02:00` would otherwise survive non-normalized.
fn deserialize_rfc3339_utc<'de, D>(deserializer: D) -> Result<OffsetDateTime, D::Error>
where
    D: serde::Deserializer<'de>,
{
    let dt = ::time::serde::rfc3339::deserialize(deserializer)?;
    Ok(dt.to_offset(UtcOffset::UTC))
}

/// Source of the current wall-clock instant. Inject instead of calling the
/// global clock so tests are deterministic (`test-mock-traits`).
pub trait Clock: Send + Sync {
    /// Current instant, normalized to UTC.
    fn now(&self) -> UtcDateTime;
}

/// Production clock backed by the system wall clock.
#[derive(Debug, Clone, Copy, Default)]
pub struct SystemClock;

impl Clock for SystemClock {
    fn now(&self) -> UtcDateTime {
        UtcDateTime::now()
    }
}

/// Test clock with a settable instant for deterministic tests. Reads dominate,
/// so the instant lives behind an `RwLock` (`own-rwlock-readers`).
#[derive(Debug)]
pub struct TestClock {
    instant: RwLock<UtcDateTime>,
}

impl TestClock {
    /// Create a test clock fixed at `instant`.
    #[must_use]
    pub fn new(instant: UtcDateTime) -> Self {
        Self {
            instant: RwLock::new(instant),
        }
    }

    /// Overwrite the instant returned by [`Clock::now`].
    pub fn set(&self, instant: UtcDateTime) {
        *self
            .instant
            .write()
            .unwrap_or_else(std::sync::PoisonError::into_inner) = instant;
    }
}

impl Clock for TestClock {
    fn now(&self) -> UtcDateTime {
        // No `.await` in this crate, so the guard never spans one; copy the
        // `Copy` value out and drop the guard before returning.
        *self
            .instant
            .read()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
    }
}

#[cfg(test)]
#[path = "../tests/time/mod.rs"]
mod tests;
