#[derive(Debug, Clone, PartialEq)]
pub struct StartCommandSession {
    pub invocation_id: String,
    pub caller_id: String,
    pub cmd: String,
    pub timeout_seconds: Option<f64>,
    pub yield_time_ms: u64,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct WriteStdin {
    pub command_session_id: String,
    pub chars: String,
    pub yield_time_ms: u64,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ReadCommandProgress {
    pub command_session_id: String,
    pub last_n_lines: usize,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CancelCommandSession {
    pub command_session_id: String,
}

#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct CollectCompleted {
    pub command_session_ids: Option<Vec<String>>,
    pub caller_id: Option<String>,
}
