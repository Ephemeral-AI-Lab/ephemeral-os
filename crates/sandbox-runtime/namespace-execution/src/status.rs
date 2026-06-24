#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum NamespaceExecutionTerminalStatus {
    Ok,
    Error,
    TimedOut,
    Cancelled,
}
