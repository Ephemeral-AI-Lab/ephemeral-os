use eos_types::{CommandSessionId, SandboxId};
use serde_json::Value;

use super::super::{BackgroundSession, BackgroundSessionStatus};

/// One tracked background command session.
#[derive(Debug, Clone)]
pub(in crate::background) struct CommandSession {
    id: CommandSessionId,
    sandbox_id: SandboxId,
    status: BackgroundSessionStatus,
    result: Option<Value>,
}

impl CommandSession {
    pub(super) fn running(id: CommandSessionId, sandbox_id: SandboxId) -> Self {
        Self {
            id,
            sandbox_id,
            status: BackgroundSessionStatus::Running,
            result: None,
        }
    }

    pub(super) fn sandbox_id(&self) -> &SandboxId {
        &self.sandbox_id
    }

    pub(super) const fn status(&self) -> BackgroundSessionStatus {
        self.status
    }

    pub(super) fn result(&self) -> Option<&Value> {
        self.result.as_ref()
    }

    pub(super) fn deliver(&mut self, result: Value) -> BackgroundSessionStatus {
        let status = command_completion_status(Some(&result));
        self.result = Some(result);
        self.status = BackgroundSessionStatus::Delivered;
        status
    }

    pub(super) fn mark_reported(&mut self, result: Value) {
        self.status = BackgroundSessionStatus::Delivered;
        self.result = Some(result);
    }

    pub(super) fn cancel(&mut self) {
        if matches!(self.status, BackgroundSessionStatus::Running) {
            self.status = BackgroundSessionStatus::Cancelled;
            self.result = Some(serde_json::json!({
                "status": "cancelled",
                "exit_code": Value::Null,
                "output": {"stdout": "", "stderr": ""},
            }));
        }
    }
}

impl BackgroundSession for CommandSession {
    type Id = CommandSessionId;

    fn id(&self) -> &Self::Id {
        &self.id
    }
}

fn command_completion_status(result: Option<&Value>) -> BackgroundSessionStatus {
    match result
        .and_then(|result| result.get("status"))
        .and_then(Value::as_str)
    {
        Some("ok") => BackgroundSessionStatus::Completed,
        Some("cancelled") => BackgroundSessionStatus::Cancelled,
        _ => BackgroundSessionStatus::Failed,
    }
}
