use eos_workspace_api::PrepareCommandRequest;

#[derive(Debug, Clone, PartialEq)]
pub struct StartCommandSession {
    pub invocation_id: String,
    pub caller_id: String,
    pub cmd: String,
    pub timeout_seconds: Option<f64>,
    pub yield_time_ms: u64,
    pub max_output_tokens: Option<u64>,
}

impl StartCommandSession {
    #[must_use]
    pub fn prepare_request(&self, command_session_id: String) -> PrepareCommandRequest {
        PrepareCommandRequest {
            caller_id: self.caller_id.clone(),
            command_session_id,
            invocation_id: self.invocation_id.clone(),
            cmd: self.cmd.clone(),
            timeout_seconds: self.timeout_seconds,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct WriteStdin {
    pub command_session_id: String,
    pub chars: String,
    pub terminate: bool,
    pub yield_time_ms: u64,
    pub max_output_tokens: Option<u64>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CancelCommandSession {
    pub command_session_id: String,
    pub max_output_tokens: Option<u64>,
}

#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct CollectCompleted {
    pub command_session_ids: Option<Vec<String>>,
    pub caller_id: Option<String>,
}
