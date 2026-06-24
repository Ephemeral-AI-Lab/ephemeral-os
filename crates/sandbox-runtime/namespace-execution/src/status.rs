/// The four terminal states a namespace execution resolves to. Relocated here
/// (Phase 2) so the engine names it without depending on `operation`; the
/// `operation` crate re-exports it, keeping every existing importer resolving.
/// Variants and `as_str()` strings are byte-for-byte the relocated original, so
/// no observability record, DTO, or daemon code path changes.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum NamespaceExecutionTerminalStatus {
    Ok,
    Error,
    TimedOut,
    Cancelled,
}

impl NamespaceExecutionTerminalStatus {
    #[must_use]
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Ok => "ok",
            Self::Error => "error",
            Self::TimedOut => "timed_out",
            Self::Cancelled => "cancelled",
        }
    }
}
