#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CommandSessionStarted {
    pub command_session_id: String,
    pub caller_id: String,
    pub workspace_mode: eos_workspace_api::WorkspaceMode,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CommandSessionFinished {
    pub command_session_id: String,
    pub caller_id: String,
    pub workspace_mode: eos_workspace_api::WorkspaceMode,
    pub status: String,
}

pub trait CommandSessionEventSink: Send + Sync {
    fn session_started(&self, event: CommandSessionStarted);
    fn session_finished(&self, event: CommandSessionFinished);
}

#[derive(Debug, Clone, Copy, Default)]
pub struct NoopCommandSessionEventSink;

impl CommandSessionEventSink for NoopCommandSessionEventSink {
    fn session_started(&self, event: CommandSessionStarted) {
        let _ = event;
    }

    fn session_finished(&self, event: CommandSessionFinished) {
        let _ = event;
    }
}
