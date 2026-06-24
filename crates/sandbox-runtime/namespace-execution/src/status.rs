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
