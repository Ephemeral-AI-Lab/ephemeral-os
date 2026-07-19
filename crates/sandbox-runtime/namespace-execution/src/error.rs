use std::fmt;

#[derive(Debug, Clone)]
pub enum NamespaceExecutionError {
    Spawn(String),
    Completion(String),
    Shutdown,
    Timeout { mode_flag: &'static str },
    Finalize(String),
    Admission { max_active: usize },
    Duplicate { execution_id: String },
}

impl fmt::Display for NamespaceExecutionError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Spawn(detail) => write!(f, "failed to spawn namespace runner: {detail}"),
            Self::Completion(detail) => {
                write!(f, "namespace runner completion failed: {detail}")
            }
            Self::Shutdown => write!(f, "namespace execution engine is shut down"),
            Self::Timeout { mode_flag } => write!(f, "ns-runner {mode_flag} timed out"),
            Self::Finalize(detail) => {
                write!(f, "failed to finalize namespace execution: {detail}")
            }
            Self::Admission { max_active } => write!(
                f,
                "namespace execution admission refused: {max_active} active executions in flight"
            ),
            Self::Duplicate { execution_id } => {
                write!(f, "namespace execution already exists: {execution_id:?}")
            }
        }
    }
}

impl std::error::Error for NamespaceExecutionError {}
