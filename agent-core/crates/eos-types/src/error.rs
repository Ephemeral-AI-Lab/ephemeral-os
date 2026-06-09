//! The single cross-crate error enum for the value primitives in this crate.
//!
//! Per spec-conventions §8 each crate owns exactly one `thiserror` enum.
//! `CoreError` is deliberately tiny: it covers only the two failures these
//! primitives can raise (an empty id string and a malformed RFC 3339
//! timestamp). Richer errors belong to the crate that owns the failing
//! operation.

/// Errors raised by the shared `eos-types` primitives.
#[derive(Debug, thiserror::Error)]
#[non_exhaustive]
pub enum CoreError {
    /// An identifier `FromStr` rejected an empty string. `kind` is the newtype
    /// name (e.g. `"AgentRunId"`) so callers can report which id failed.
    #[error("empty {kind} identifier")]
    EmptyId {
        /// The newtype kind whose `FromStr` received the empty string.
        kind: &'static str,
    },
    /// An RFC 3339 timestamp string failed to parse into a `UtcDateTime`.
    #[error("invalid utc timestamp")]
    Timestamp(#[from] time::error::Parse),
    /// A persistence/store operation failed. The per-entity `Store` traits in
    /// `eos-types` return this `CoreError`, but the concrete richer error lives
    /// downstream (e.g. `eos-db::DbError`), which this leaf crate cannot name —
    /// so the downstream error is flattened to its `Display` string here.
    #[error("{0}")]
    Store(String),
}

#[cfg(test)]
#[path = "../tests/error/mod.rs"]
mod tests;
