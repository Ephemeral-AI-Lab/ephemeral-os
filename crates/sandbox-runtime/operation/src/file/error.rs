use thiserror::Error;

/// Errors surfaced by the `file` domain. Blame's only failure is an unaudited
/// path; the owner string itself is opaque, so nothing here interprets it.
#[derive(Debug, Error)]
pub enum FileError {
    #[error("no auditability record for path: {0}")]
    NotFound(String),
}
