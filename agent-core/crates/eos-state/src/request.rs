//! `Request` DTO — one top-level user request (Python `db/models/request.py`).

use schemars::JsonSchema;
use serde::{Deserialize, Serialize};

use eos_types::{RequestId, SandboxId, TaskId, UtcDateTime};

/// Immutable view of a persisted request row (Python `RequestRecord`).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct Request {
    /// Request identifier.
    pub id: RequestId,
    /// Working directory the request runs against.
    pub cwd: String,
    /// Provisioned sandbox, if any.
    pub sandbox_id: Option<SandboxId>,
    /// The original request prompt.
    pub request_prompt: String,
    /// The root `Task(role=root, workflow_id=None)`, once minted.
    pub root_task_id: Option<TaskId>,
    /// Free-form request status (`running` / finished). Broader than
    /// `TaskStatus`; set via `RequestStore::finish_request`.
    pub status: String,
    /// Creation timestamp.
    pub created_at: UtcDateTime,
    /// Last-update timestamp.
    pub updated_at: UtcDateTime,
    /// Finish timestamp, set server-side at `finish_request`.
    pub finished_at: Option<UtcDateTime>,
}
