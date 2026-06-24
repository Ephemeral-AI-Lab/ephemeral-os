use std::fmt;

#[derive(Debug, Clone)]
pub enum NamespaceExecutionError {
    Spawn(String),
    Completion(String),
    Timeout { mode_flag: &'static str },
    Finalize(String),
    Admission { max_active: usize },
}

impl fmt::Display for NamespaceExecutionError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Spawn(detail) => write!(f, "failed to spawn namespace runner: {detail}"),
            Self::Completion(detail) => {
                write!(f, "namespace runner completion failed: {detail}")
            }
            Self::Timeout { mode_flag } => write!(f, "ns-runner {mode_flag} timed out"),
            Self::Finalize(detail) => {
                write!(f, "failed to finalize namespace execution: {detail}")
            }
            Self::Admission { max_active } => write!(
                f,
                "namespace execution admission refused: {max_active} active executions in flight"
            ),
        }
    }
}

impl std::error::Error for NamespaceExecutionError {}
