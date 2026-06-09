#![allow(clippy::unwrap_used)] // unwrap is permitted in tests (err-no-unwrap-prod)
use super::*;
use std::sync::Arc;
use std::thread;

// AC-types-03: RFC 3339 roundtrip + UTC normalization.
#[test]
fn utc_datetime_rfc3339_roundtrip() {
    let s = "2026-06-02T19:47:00Z";
    let dt = UtcDateTime::parse_rfc3339(s).expect("parse");
    // `time` emits the UTC offset as `Z`; Rust isoformat uses `+00:00`.
    // Both are valid RFC 3339 and parse to the same instant.
    let formatted = dt.to_rfc3339();
    let reparsed = UtcDateTime::parse_rfc3339(&formatted).expect("reparse");
    assert_eq!(dt, reparsed);

    // A non-UTC offset is normalized to UTC on construction.
    let plus2 = OffsetDateTime::parse("2026-06-02T21:47:00+02:00", &Rfc3339).unwrap();
    let normalized = UtcDateTime::from_offset(plus2);
    assert_eq!(normalized.into_inner().offset(), UtcOffset::UTC);
    // Same instant as the `Z` value above.
    assert_eq!(normalized, dt);
}

// AC-types-04: Clock injection is deterministic and thread-shareable.
#[test]
fn test_clock_is_settable() {
    let t0 = UtcDateTime::parse_rfc3339("2020-01-01T00:00:00Z").unwrap();
    let t1 = UtcDateTime::parse_rfc3339("2030-12-31T23:59:59Z").unwrap();
    let clock: Arc<dyn Clock> = Arc::new(TestClock::new(t0));
    assert_eq!(clock.now(), t0);

    // Shared across threads, the same handle yields identical reads.
    let a = Arc::clone(&clock);
    let b = Arc::clone(&clock);
    let ha = thread::spawn(move || a.now());
    let hb = thread::spawn(move || b.now());
    assert_eq!(ha.join().unwrap(), hb.join().unwrap());

    // Downcast not needed: set through the concrete handle.
    let concrete = TestClock::new(t0);
    concrete.set(t1);
    assert_eq!(concrete.now(), t1);
}

// AC-types-06 (timestamp portion): UtcDateTime schemas as string/date-time.
#[test]
fn json_schema_utc_datetime_is_date_time_string() {
    let schema = serde_json::to_value(schemars::schema_for!(UtcDateTime)).unwrap();
    assert_eq!(schema["type"], serde_json::json!("string"));
    assert_eq!(schema["format"], serde_json::json!("date-time"));
}

// The serde wire form is a bare RFC 3339 string (transparent). The exact
// bytes are the canonical UTC `Z` form; full byte-parity with Rust's
// variable-precision `+00:00` isoformat is a cutover/Phase-0-harness concern.
#[test]
fn serde_is_transparent_rfc3339_string() {
    let dt = UtcDateTime::parse_rfc3339("2026-06-02T19:47:00Z").unwrap();
    let value = serde_json::to_value(dt).unwrap();
    assert_eq!(value, serde_json::json!("2026-06-02T19:47:00Z"));
    let back: UtcDateTime = serde_json::from_value(value).unwrap();
    assert_eq!(back, dt);
}

// Blocker fix: the transparent deserialize path must normalize a non-UTC
// offset to UTC, preserving the type's invariant (spec §8).
#[test]
fn deserialize_normalizes_non_utc_offset() {
    let dt: UtcDateTime =
        serde_json::from_value(serde_json::json!("2026-06-02T21:47:00+02:00")).unwrap();
    assert_eq!(dt.into_inner().offset(), UtcOffset::UTC);
    let z: UtcDateTime = serde_json::from_value(serde_json::json!("2026-06-02T19:47:00Z")).unwrap();
    assert_eq!(dt, z);
    // Re-serialization emits the canonical UTC (`Z`) form, not `+02:00`.
    assert_eq!(
        serde_json::to_value(dt).unwrap(),
        serde_json::json!("2026-06-02T19:47:00Z")
    );
}
