#![allow(clippy::unwrap_used)] // unwrap is permitted in tests (err-no-unwrap-prod)
use super::*;

// AC-types-05: CoreError is std::error::Error, converts time::error::Parse
// via `?`/#[from], and Display is lowercase with no trailing punctuation.
#[test]
fn core_error_from_and_display() {
    fn is_std_error<E: std::error::Error>() {}
    is_std_error::<CoreError>();

    // The static template fragments are lowercase with no trailing
    // punctuation; the interpolated `kind` is a deliberate type name.
    let empty = CoreError::EmptyId { kind: "AgentRunId" };
    let msg = empty.to_string();
    assert_eq!(msg, "empty AgentRunId identifier");
    assert!(!msg.ends_with('.'));

    // A variant with no interpolation must be fully lowercase, no period.
    let ts = CoreError::Timestamp(
        time::OffsetDateTime::parse("nope", &time::format_description::well_known::Rfc3339)
            .unwrap_err(),
    );
    let ts_msg = ts.to_string();
    assert_eq!(ts_msg, "invalid utc timestamp");
    assert_eq!(ts_msg, ts_msg.to_lowercase());
    assert!(!ts_msg.ends_with('.'));

    // `?` conversion from time::error::Parse via #[from].
    fn parse(s: &str) -> Result<time::OffsetDateTime, CoreError> {
        let dt = time::OffsetDateTime::parse(s, &time::format_description::well_known::Rfc3339)?;
        Ok(dt)
    }
    let err = parse("not-a-timestamp").unwrap_err();
    assert!(matches!(err, CoreError::Timestamp(_)));
    assert!(std::error::Error::source(&err).is_some());
}

// The `Store` variant carries a downstream error's flattened message and
// displays it verbatim (used by eos-db's `From<DbError>` at the trait seam).
#[test]
fn store_variant_displays_payload_verbatim() {
    let err = CoreError::Store("row not found in workflows".to_owned());
    assert_eq!(err.to_string(), "row not found in workflows");
}
