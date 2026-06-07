//! File-backed agent-node message records.
//!
//! The message-record root is supplied by the backend composition root, but the
//! message/event contents are written by agent-core at the engine boundary where
//! request, task, agent-run, and provider-visible message facts are available.
#![forbid(unsafe_code)]
#![warn(missing_docs)]

use std::collections::BTreeSet;
use std::path::{Path, PathBuf};

use eos_llm_client::{ContentBlock, Message, MessageRole};
use eos_types::{
    AgentRunId, AttemptId, IterationId, JsonObject, RequestId, TaskId, UtcDateTime, WorkflowId,
};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use tokio::io::{AsyncReadExt, AsyncSeekExt, AsyncWriteExt};

/// Result alias for message-record operations.
pub type Result<T> = std::result::Result<T, MessageRecordError>;

/// File-backed message-record service failures.
#[derive(Debug, thiserror::Error)]
#[non_exhaustive]
pub enum MessageRecordError {
    /// A path segment would escape the message-record root or create ambiguous layout.
    #[error("unsafe message-record path segment for {field}: {value:?}")]
    UnsafeSegment {
        /// Field whose value was rejected.
        field: &'static str,
        /// Rejected value.
        value: String,
    },
    /// The requested agent-run message-record directory does not exist.
    #[error("agent-run message record not found: {0}")]
    NotFound(String),
    /// A byte offset was beyond the current file length.
    #[error("message offset {offset} is beyond file length {len}")]
    OffsetOutOfRange {
        /// Requested offset.
        offset: u64,
        /// Current file length.
        len: u64,
    },
    /// Filesystem I/O failed.
    #[error("message-record io error: {0}")]
    Io(#[from] std::io::Error),
    /// JSON encoding or decoding failed.
    #[error("message-record json error: {0}")]
    Json(#[from] serde_json::Error),
    /// A blocking filesystem scan panicked or was cancelled.
    #[error("message-record scan task failed: {0}")]
    Join(#[from] tokio::task::JoinError),
}

/// Shared message-record root service.
#[derive(Debug, Clone)]
pub struct AgentMessageRecords {
    root: PathBuf,
}

impl AgentMessageRecords {
    /// Create a service rooted at `root`.
    #[must_use]
    pub fn new(root: impl Into<PathBuf>) -> Self {
        Self { root: root.into() }
    }

    /// Message-record root path.
    #[must_use]
    pub fn root(&self) -> &Path {
        &self.root
    }

    /// Create one agent-run node, write its initial messages, and append the
    /// initial node-local events.
    ///
    /// # Errors
    /// Returns [`MessageRecordError`] if path validation, directory creation, JSON
    /// encoding, or file append fails.
    pub async fn start_agent_run(
        &self,
        input: AgentRunRecordStart<'_>,
    ) -> Result<AgentRunRecordHandle> {
        let node_dir = self.node_dir(&input)?;
        let events_path = node_dir.join("events.jsonl");
        let messages_path = node_dir.join("messages.jsonl");
        tokio::fs::create_dir_all(&node_dir).await?;

        let handle = AgentRunRecordHandle {
            node_dir: node_dir.clone(),
            messages_path,
            events_path,
        };
        let mut payload = JsonObject::new();
        payload.insert("type".to_owned(), json!(input.kind.node_type()));
        payload.insert(
            "agent_run_id".to_owned(),
            json!(input.agent_run_id.as_str()),
        );
        payload.insert("agent".to_owned(), json!(input.agent_name));
        if let Some(task_id) = input.task_id {
            payload.insert("task_id".to_owned(), json!(task_id.as_str()));
        }
        payload.insert("request_id".to_owned(), json!(input.request_id.as_str()));
        input.kind.extend_payload(&mut payload);
        handle.append_event("node_started", payload).await?;

        let range = handle
            .append_initial_messages(input.system_prompt, input.initial_messages)
            .await?;
        let mut payload = JsonObject::new();
        payload.insert("count".to_owned(), json!(range.count));
        payload.insert("messages_start_byte".to_owned(), json!(range.start_byte));
        payload.insert("messages_end_byte".to_owned(), json!(range.end_byte));
        handle.append_event("messages_initialized", payload).await?;

        if let Some((parent_dir, child_path)) = self.parent_announcement(&input, &node_dir)? {
            let parent = AgentRunRecordHandle::from_node_dir(parent_dir);
            let mut payload = JsonObject::new();
            payload.insert("type".to_owned(), json!(input.kind.node_type()));
            payload.insert(
                "agent_run_id".to_owned(),
                json!(input.agent_run_id.as_str()),
            );
            payload.insert("path".to_owned(), json!(child_path));
            if let Some(task_id) = input.task_id {
                payload.insert("task_id".to_owned(), json!(task_id.as_str()));
            }
            input.kind.extend_payload(&mut payload);
            parent.append_event("child_created", payload).await?;
        }

        Ok(handle)
    }

    /// Read raw `messages.jsonl` bytes for an agent run after `after_byte`.
    ///
    /// # Errors
    /// Returns [`MessageRecordError::NotFound`] if the agent-run node or message file
    /// does not exist.
    pub async fn read_messages(
        &self,
        agent_run_id: &AgentRunId,
        after_byte: u64,
    ) -> Result<RecordBytes> {
        let node_dir = self.resolve_agent_run(agent_run_id).await?;
        read_bytes_after(&node_dir.join("messages.jsonl"), after_byte).await
    }

    /// Replay node-local events with `seq > after_seq`.
    ///
    /// # Errors
    /// Returns [`MessageRecordError::NotFound`] if the agent-run node or event file
    /// does not exist.
    pub async fn read_events(
        &self,
        agent_run_id: &AgentRunId,
        after_seq: u64,
    ) -> Result<Vec<NodeEvent>> {
        let node_dir = self.resolve_agent_run(agent_run_id).await?;
        read_events_after(&node_dir.join("events.jsonl"), after_seq).await
    }

    async fn resolve_agent_run(&self, agent_run_id: &AgentRunId) -> Result<PathBuf> {
        safe_segment("agent_run_id", agent_run_id.as_str())?;
        let root = self.root.clone();
        let id = agent_run_id.clone();
        let found = tokio::task::spawn_blocking(move || find_agent_run_dir(&root, &id)).await??;
        found.ok_or_else(|| MessageRecordError::NotFound(agent_run_id.as_str().to_owned()))
    }

    fn node_dir(&self, input: &AgentRunRecordStart<'_>) -> Result<PathBuf> {
        let request_root = self
            .root
            .join("requests")
            .join(safe_segment("request_id", input.request_id.as_str())?);
        let agent_run_segment = safe_prefixed_segment("agent-run", input.agent_run_id.as_str())?;
        match &input.kind {
            AgentRunRecordKind::Root => {
                let task_id = input
                    .task_id
                    .ok_or_else(|| MessageRecordError::UnsafeSegment {
                        field: "task_id",
                        value: String::new(),
                    })?;
                Ok(request_root
                    .join(safe_prefixed_segment("root-task", task_id.as_str())?)
                    .join(agent_run_segment))
            }
            AgentRunRecordKind::WorkflowTask {
                workflow_id,
                iteration_id,
                attempt_id,
                role,
            } => {
                let task_id = input
                    .task_id
                    .ok_or_else(|| MessageRecordError::UnsafeSegment {
                        field: "task_id",
                        value: String::new(),
                    })?;
                let workflow_parent =
                    find_root_agent_dir(&request_root)?.unwrap_or_else(|| request_root.clone());
                Ok(workflow_parent
                    .join("workflows")
                    .join(safe_prefixed_segment("workflow", workflow_id.as_str())?)
                    .join(safe_prefixed_segment("iteration", iteration_id.as_str())?)
                    .join(safe_prefixed_segment("attempt", attempt_id.as_str())?)
                    .join(safe_prefixed_segment(
                        role.task_segment_prefix(),
                        task_id.as_str(),
                    )?)
                    .join(agent_run_segment))
            }
            AgentRunRecordKind::Subagent {
                parent_agent_run_id,
            } => Ok(parent_or_request_dir(&request_root, parent_agent_run_id)?
                .join("subagents")
                .join(safe_prefixed_segment(
                    "subagent-run",
                    input.agent_run_id.as_str(),
                )?)),
            AgentRunRecordKind::Advisor {
                parent_agent_run_id,
            } => Ok(parent_or_request_dir(&request_root, parent_agent_run_id)?
                .join("advisors")
                .join(safe_prefixed_segment(
                    "advisor-run",
                    input.agent_run_id.as_str(),
                )?)),
            AgentRunRecordKind::Agent => Ok(request_root.join(agent_run_segment)),
        }
    }

    fn parent_announcement(
        &self,
        input: &AgentRunRecordStart<'_>,
        node_dir: &Path,
    ) -> Result<Option<(PathBuf, String)>> {
        let request_root = self
            .root
            .join("requests")
            .join(safe_segment("request_id", input.request_id.as_str())?);
        let parent = match &input.kind {
            AgentRunRecordKind::Subagent {
                parent_agent_run_id,
            }
            | AgentRunRecordKind::Advisor {
                parent_agent_run_id,
            } => find_agent_run_dir_in(&request_root, parent_agent_run_id)?,
            AgentRunRecordKind::WorkflowTask { .. } => find_root_agent_dir(&request_root)?,
            AgentRunRecordKind::Root | AgentRunRecordKind::Agent => None,
        };
        let Some(parent) = parent else {
            return Ok(None);
        };
        let relative = node_dir
            .strip_prefix(&parent)
            .ok()
            .map(path_to_slash_string)
            .unwrap_or_else(|| path_to_slash_string(node_dir));
        Ok(Some((parent, relative)))
    }
}

/// Input for starting an agent-run message-record node.
#[derive(Debug, Clone, Copy)]
pub struct AgentRunRecordStart<'a> {
    /// Owning request id.
    pub request_id: &'a RequestId,
    /// Owning task id, when this run is task-backed.
    pub task_id: Option<&'a TaskId>,
    /// Agent-run id.
    pub agent_run_id: &'a AgentRunId,
    /// Bound agent profile name.
    pub agent_name: &'a str,
    /// Node type and parent/location facts.
    pub kind: &'a AgentRunRecordKind,
    /// Fully assembled system prompt.
    pub system_prompt: &'a str,
    /// Seed transcript rows supplied to the agent.
    pub initial_messages: &'a [Message],
}

/// Agent-run message-record node type and location facts.
#[derive(Debug, Clone, PartialEq, Eq)]
#[non_exhaustive]
pub enum AgentRunRecordKind {
    /// Root request agent.
    Root,
    /// Delegated workflow planner/generator/reducer task agent.
    WorkflowTask {
        /// Owning workflow id.
        workflow_id: WorkflowId,
        /// Owning iteration id.
        iteration_id: IterationId,
        /// Owning attempt id.
        attempt_id: AttemptId,
        /// Workflow task role.
        role: WorkflowTaskRole,
    },
    /// Background subagent run under a parent agent.
    Subagent {
        /// Parent agent-run id.
        parent_agent_run_id: AgentRunId,
    },
    /// Advisor run under a parent agent.
    Advisor {
        /// Parent agent-run id.
        parent_agent_run_id: AgentRunId,
    },
    /// Generic agent run when no narrower layout is known.
    Agent,
}

impl AgentRunRecordKind {
    fn node_type(&self) -> &'static str {
        match self {
            Self::Root => "root_agent",
            Self::WorkflowTask { role, .. } => role.node_type(),
            Self::Subagent { .. } => "subagent",
            Self::Advisor { .. } => "advisor",
            Self::Agent => "agent",
        }
    }

    fn extend_payload(&self, payload: &mut JsonObject) {
        match self {
            Self::WorkflowTask {
                workflow_id,
                iteration_id,
                attempt_id,
                role,
            } => {
                payload.insert("workflow_id".to_owned(), json!(workflow_id.as_str()));
                payload.insert("iteration_id".to_owned(), json!(iteration_id.as_str()));
                payload.insert("attempt_id".to_owned(), json!(attempt_id.as_str()));
                payload.insert("role".to_owned(), json!(role.as_str()));
            }
            Self::Subagent {
                parent_agent_run_id,
            }
            | Self::Advisor {
                parent_agent_run_id,
            } => {
                payload.insert(
                    "parent_agent_run_id".to_owned(),
                    json!(parent_agent_run_id.as_str()),
                );
            }
            Self::Root | Self::Agent => {}
        }
    }
}

/// Workflow task role used for message-record path labels.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[non_exhaustive]
pub enum WorkflowTaskRole {
    /// Planner task.
    Planner,
    /// Generator task.
    Generator,
    /// Reducer task.
    Reducer,
}

impl WorkflowTaskRole {
    fn as_str(self) -> &'static str {
        match self {
            Self::Planner => "planner",
            Self::Generator => "generator",
            Self::Reducer => "reducer",
        }
    }

    fn node_type(self) -> &'static str {
        match self {
            Self::Planner => "workflow_planner",
            Self::Generator => "workflow_generator",
            Self::Reducer => "workflow_reducer",
        }
    }

    fn task_segment_prefix(self) -> &'static str {
        match self {
            Self::Planner => "planner-task",
            Self::Generator => "generator-task",
            Self::Reducer => "reducer-task",
        }
    }
}

/// A started agent-run message-record node.
#[derive(Debug, Clone)]
pub struct AgentRunRecordHandle {
    node_dir: PathBuf,
    messages_path: PathBuf,
    events_path: PathBuf,
}

impl AgentRunRecordHandle {
    /// Node directory.
    #[must_use]
    pub fn node_dir(&self) -> &Path {
        &self.node_dir
    }

    /// Append later model-visible messages and announce the byte range in
    /// `events.jsonl`.
    ///
    /// # Errors
    /// Returns [`MessageRecordError`] if message or event append fails.
    pub async fn append_messages(&self, messages: &[Message]) -> Result<MessageAppendRange> {
        let range = append_message_rows(&self.messages_path, "message", messages).await?;
        if range.count > 0 {
            let mut payload = JsonObject::new();
            payload.insert("count".to_owned(), json!(range.count));
            payload.insert("messages_start_byte".to_owned(), json!(range.start_byte));
            payload.insert("messages_end_byte".to_owned(), json!(range.end_byte));
            payload.insert(
                "message_types".to_owned(),
                Value::Array(
                    message_types(messages)
                        .into_iter()
                        .map(Value::String)
                        .collect(),
                ),
            );
            self.append_event("messages_appended", payload).await?;
        }
        Ok(range)
    }

    /// Append the terminal node event.
    ///
    /// # Errors
    /// Returns [`MessageRecordError`] if event append fails.
    pub async fn finish(&self, status: NodeFinishStatus) -> Result<()> {
        let mut payload = JsonObject::new();
        payload.insert("status".to_owned(), json!(status.as_str()));
        self.append_event("node_finished", payload).await
    }

    async fn append_initial_messages(
        &self,
        system_prompt: &str,
        initial_messages: &[Message],
    ) -> Result<MessageAppendRange> {
        let mut rows = Vec::with_capacity(initial_messages.len().saturating_add(1));
        rows.push(MessageRowOwned {
            row_type: "initial_message",
            role: "system",
            content: vec![ContentBlock::Text {
                text: system_prompt.to_owned(),
            }],
        });
        rows.extend(initial_messages.iter().map(|message| MessageRowOwned {
            row_type: "initial_message",
            role: role_wire(message.role),
            content: message.content.clone(),
        }));
        append_owned_rows(&self.messages_path, &rows).await
    }

    async fn append_event(&self, kind: impl Into<String>, payload: JsonObject) -> Result<()> {
        append_event(&self.events_path, kind.into(), payload).await
    }

    fn from_node_dir(node_dir: PathBuf) -> Self {
        Self {
            messages_path: node_dir.join("messages.jsonl"),
            events_path: node_dir.join("events.jsonl"),
            node_dir,
        }
    }
}

/// Terminal status stored in `node_finished`.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[non_exhaustive]
pub enum NodeFinishStatus {
    /// Agent run completed without framework error.
    Completed,
    /// Agent run failed or crashed.
    Failed,
}

impl NodeFinishStatus {
    fn as_str(self) -> &'static str {
        match self {
            Self::Completed => "completed",
            Self::Failed => "failed",
        }
    }
}

/// Byte range produced by a message append.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct MessageAppendRange {
    /// Number of message rows appended.
    pub count: usize,
    /// Starting byte offset before the append.
    pub start_byte: u64,
    /// Ending byte offset after the append.
    pub end_byte: u64,
}

/// Raw message-record bytes plus the next tail offset.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RecordBytes {
    /// Raw JSONL bytes.
    pub bytes: Vec<u8>,
    /// Byte offset after `bytes`.
    pub next_byte_offset: u64,
}

/// One node-local event row.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct NodeEvent {
    /// Node-local sequence, starting at 1.
    pub seq: u64,
    /// Stable event category.
    pub kind: String,
    /// Small routing/status payload.
    pub payload: JsonObject,
    /// Event creation timestamp.
    pub created_at: UtcDateTime,
}

#[derive(Serialize)]
struct MessageRow<'a> {
    #[serde(rename = "type")]
    row_type: &'static str,
    role: &'static str,
    content: &'a [ContentBlock],
}

#[derive(Serialize)]
struct MessageRowOwned {
    #[serde(rename = "type")]
    row_type: &'static str,
    role: &'static str,
    content: Vec<ContentBlock>,
}

async fn append_message_rows(
    path: &Path,
    row_type: &'static str,
    messages: &[Message],
) -> Result<MessageAppendRange> {
    let rows: Vec<_> = messages
        .iter()
        .map(|message| MessageRow {
            row_type,
            role: role_wire(message.role),
            content: &message.content,
        })
        .collect();
    append_rows(path, &rows).await
}

async fn append_owned_rows(path: &Path, rows: &[MessageRowOwned]) -> Result<MessageAppendRange> {
    append_rows(path, rows).await
}

async fn append_rows<T: Serialize>(path: &Path, rows: &[T]) -> Result<MessageAppendRange> {
    let start_byte = file_len_or_zero(path).await?;
    if rows.is_empty() {
        return Ok(MessageAppendRange {
            count: 0,
            start_byte,
            end_byte: start_byte,
        });
    }
    if let Some(parent) = path.parent() {
        tokio::fs::create_dir_all(parent).await?;
    }
    let mut file = tokio::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(path)
        .await?;
    for row in rows {
        let line = serde_json::to_string(row)?;
        file.write_all(line.as_bytes()).await?;
        file.write_all(b"\n").await?;
    }
    file.flush().await?;
    let end_byte = file_len_or_zero(path).await?;
    Ok(MessageAppendRange {
        count: rows.len(),
        start_byte,
        end_byte,
    })
}

async fn append_event(path: &Path, kind: String, payload: JsonObject) -> Result<()> {
    if let Some(parent) = path.parent() {
        tokio::fs::create_dir_all(parent).await?;
    }
    let seq = next_event_seq(path).await?;
    let event = NodeEvent {
        seq,
        kind,
        payload,
        created_at: UtcDateTime::now(),
    };
    let mut file = tokio::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(path)
        .await?;
    let line = serde_json::to_string(&event)?;
    file.write_all(line.as_bytes()).await?;
    file.write_all(b"\n").await?;
    file.flush().await?;
    Ok(())
}

async fn next_event_seq(path: &Path) -> Result<u64> {
    match tokio::fs::read_to_string(path).await {
        Ok(raw) => {
            let last_seq = raw
                .lines()
                .rev()
                .find(|line| !line.trim().is_empty())
                .map(serde_json::from_str::<NodeEvent>)
                .transpose()?
                .map_or(0, |event| event.seq);
            Ok(last_seq.saturating_add(1))
        }
        Err(err) if err.kind() == std::io::ErrorKind::NotFound => Ok(1),
        Err(err) => Err(err.into()),
    }
}

async fn read_bytes_after(path: &Path, after_byte: u64) -> Result<RecordBytes> {
    let mut file = tokio::fs::File::open(path).await.map_err(|err| {
        if err.kind() == std::io::ErrorKind::NotFound {
            MessageRecordError::NotFound(path.display().to_string())
        } else {
            MessageRecordError::Io(err)
        }
    })?;
    let len = file.metadata().await?.len();
    if after_byte > len {
        return Err(MessageRecordError::OffsetOutOfRange {
            offset: after_byte,
            len,
        });
    }
    file.seek(std::io::SeekFrom::Start(after_byte)).await?;
    let mut bytes = Vec::new();
    file.read_to_end(&mut bytes).await?;
    Ok(RecordBytes {
        bytes,
        next_byte_offset: len,
    })
}

async fn read_events_after(path: &Path, after_seq: u64) -> Result<Vec<NodeEvent>> {
    let raw = tokio::fs::read_to_string(path).await.map_err(|err| {
        if err.kind() == std::io::ErrorKind::NotFound {
            MessageRecordError::NotFound(path.display().to_string())
        } else {
            MessageRecordError::Io(err)
        }
    })?;
    raw.lines()
        .filter(|line| !line.trim().is_empty())
        .map(serde_json::from_str::<NodeEvent>)
        .filter_map(|result| match result {
            Ok(event) if event.seq > after_seq => Some(Ok(event)),
            Ok(_) => None,
            Err(err) => Some(Err(MessageRecordError::Json(err))),
        })
        .collect()
}

async fn file_len_or_zero(path: &Path) -> Result<u64> {
    match tokio::fs::metadata(path).await {
        Ok(metadata) => Ok(metadata.len()),
        Err(err) if err.kind() == std::io::ErrorKind::NotFound => Ok(0),
        Err(err) => Err(err.into()),
    }
}

fn role_wire(role: MessageRole) -> &'static str {
    match role {
        MessageRole::User => "user",
        MessageRole::Assistant => "assistant",
    }
}

fn message_types(messages: &[Message]) -> Vec<String> {
    let mut types = BTreeSet::new();
    for block in messages.iter().flat_map(|message| &message.content) {
        types.insert(
            match block {
                ContentBlock::Text { .. } => "text",
                ContentBlock::ToolUse { .. } => "tool_use",
                ContentBlock::Reasoning { .. } => "reasoning",
                ContentBlock::ToolResult { .. } => "tool_result",
                ContentBlock::SystemNotification { .. } => "system_notification",
                _ => "unknown",
            }
            .to_owned(),
        );
    }
    types.into_iter().collect()
}

fn parent_or_request_dir(request_root: &Path, parent_agent_run_id: &AgentRunId) -> Result<PathBuf> {
    Ok(
        find_agent_run_dir_in(request_root, parent_agent_run_id)?.unwrap_or_else(|| {
            request_root
                .join("parents-missing")
                .join(parent_agent_run_id.as_str())
        }),
    )
}

fn find_root_agent_dir(request_root: &Path) -> Result<Option<PathBuf>> {
    let Ok(entries) = std::fs::read_dir(request_root) else {
        return Ok(None);
    };
    for entry in entries {
        let entry = entry?;
        if !entry.file_type()?.is_dir() {
            continue;
        }
        let name = entry.file_name();
        let Some(name) = name.to_str() else {
            continue;
        };
        if !name.starts_with("root-task-") {
            continue;
        }
        let Ok(agent_dirs) = std::fs::read_dir(entry.path()) else {
            continue;
        };
        for agent_entry in agent_dirs {
            let agent_entry = agent_entry?;
            if !agent_entry.file_type()?.is_dir() {
                continue;
            }
            if agent_entry
                .file_name()
                .to_str()
                .is_some_and(|value| value.starts_with("agent-run-"))
            {
                return Ok(Some(agent_entry.path()));
            }
        }
    }
    Ok(None)
}

fn find_agent_run_dir(root: &Path, agent_run_id: &AgentRunId) -> Result<Option<PathBuf>> {
    find_agent_run_dir_in(root, agent_run_id)
}

fn find_agent_run_dir_in(root: &Path, agent_run_id: &AgentRunId) -> Result<Option<PathBuf>> {
    safe_segment("agent_run_id", agent_run_id.as_str())?;
    let needle = safe_prefixed_segment("agent-run", agent_run_id.as_str())?;
    find_dir_named(root, &needle)
}

fn find_dir_named(root: &Path, needle: &str) -> Result<Option<PathBuf>> {
    let Ok(entries) = std::fs::read_dir(root) else {
        return Ok(None);
    };
    for entry in entries {
        let entry = entry?;
        let file_type = entry.file_type()?;
        if !file_type.is_dir() {
            continue;
        }
        if entry.file_name().to_str() == Some(needle) {
            return Ok(Some(entry.path()));
        }
        if let Some(found) = find_dir_named(&entry.path(), needle)? {
            return Ok(Some(found));
        }
    }
    Ok(None)
}

fn safe_prefixed_segment(prefix: &'static str, id: &str) -> Result<String> {
    Ok(format!("{prefix}-{}", safe_segment(prefix, id)?))
}

fn safe_segment<'a>(field: &'static str, value: &'a str) -> Result<&'a str> {
    if value.is_empty()
        || value == "."
        || value == ".."
        || value.contains('/')
        || value.contains('\\')
        || value.contains(std::path::MAIN_SEPARATOR)
    {
        return Err(MessageRecordError::UnsafeSegment {
            field,
            value: value.to_owned(),
        });
    }
    Ok(value)
}

fn path_to_slash_string(path: &Path) -> String {
    path.components()
        .map(|component| component.as_os_str().to_string_lossy())
        .collect::<Vec<_>>()
        .join("/")
}

#[cfg(test)]
mod tests {
    #![allow(clippy::expect_used, clippy::unwrap_used)]

    use eos_llm_client::Message;
    use eos_types::{AgentRunId, RequestId, TaskId};
    use serde_json::json;

    use super::*;

    fn ids() -> (RequestId, TaskId, AgentRunId) {
        (
            "req-1".parse().unwrap(),
            "task-1".parse().unwrap(),
            "run-1".parse().unwrap(),
        )
    }

    #[tokio::test]
    async fn root_start_writes_initial_messages_and_events() {
        let dir = tempfile::tempdir().unwrap();
        let records = AgentMessageRecords::new(dir.path());
        let (request_id, task_id, agent_run_id) = ids();
        let handle = records
            .start_agent_run(AgentRunRecordStart {
                request_id: &request_id,
                task_id: Some(&task_id),
                agent_run_id: &agent_run_id,
                agent_name: "root",
                kind: &AgentRunRecordKind::Root,
                system_prompt: "system prompt",
                initial_messages: &[Message::from_user_text("hello")],
            })
            .await
            .expect("start");

        let raw = tokio::fs::read_to_string(handle.node_dir().join("messages.jsonl"))
            .await
            .unwrap();
        let rows: Vec<Value> = raw
            .lines()
            .map(|line| serde_json::from_str(line).unwrap())
            .collect();
        assert_eq!(rows.len(), 2);
        assert_eq!(rows[0]["type"], json!("initial_message"));
        assert_eq!(rows[0]["role"], json!("system"));
        assert_eq!(rows[0]["content"][0]["text"], json!("system prompt"));
        assert_eq!(rows[1]["role"], json!("user"));
        assert!(rows[0].get("turn").is_none());
        assert!(rows[0].get("initial_index").is_none());

        let events = records.read_events(&agent_run_id, 0).await.unwrap();
        assert_eq!(events.len(), 2);
        assert_eq!(events[0].seq, 1);
        assert_eq!(events[0].kind, "node_started");
        assert_eq!(events[1].kind, "messages_initialized");
        assert_eq!(events[1].payload["count"], json!(2));
        assert!(events[1].payload["messages_end_byte"].as_u64().unwrap() > 0);
    }

    #[tokio::test]
    async fn later_messages_append_byte_ranges_without_event_content() {
        let dir = tempfile::tempdir().unwrap();
        let records = AgentMessageRecords::new(dir.path());
        let (request_id, task_id, agent_run_id) = ids();
        let handle = records
            .start_agent_run(AgentRunRecordStart {
                request_id: &request_id,
                task_id: Some(&task_id),
                agent_run_id: &agent_run_id,
                agent_name: "root",
                kind: &AgentRunRecordKind::Root,
                system_prompt: "system",
                initial_messages: &[],
            })
            .await
            .unwrap();

        let range = handle
            .append_messages(&[Message {
                role: MessageRole::User,
                content: vec![ContentBlock::SystemNotification {
                    text: "remember".to_owned(),
                }],
            }])
            .await
            .unwrap();
        assert_eq!(range.count, 1);
        assert!(range.end_byte > range.start_byte);

        let events = records.read_events(&agent_run_id, 2).await.unwrap();
        assert_eq!(events.len(), 1);
        assert_eq!(events[0].kind, "messages_appended");
        assert_eq!(
            events[0].payload["message_types"],
            json!(["system_notification"])
        );
        assert!(events[0].payload.get("content").is_none());

        let tail = records
            .read_messages(&agent_run_id, range.start_byte)
            .await
            .unwrap();
        let text = String::from_utf8(tail.bytes).unwrap();
        assert!(text.contains("system_notification"));
        assert_eq!(tail.next_byte_offset, range.end_byte);
    }

    #[tokio::test]
    async fn child_created_waits_until_child_files_exist() {
        let dir = tempfile::tempdir().unwrap();
        let records = AgentMessageRecords::new(dir.path());
        let (request_id, task_id, parent_id) = ids();
        let parent = records
            .start_agent_run(AgentRunRecordStart {
                request_id: &request_id,
                task_id: Some(&task_id),
                agent_run_id: &parent_id,
                agent_name: "root",
                kind: &AgentRunRecordKind::Root,
                system_prompt: "system",
                initial_messages: &[],
            })
            .await
            .unwrap();
        let child_id: AgentRunId = "child-run".parse().unwrap();
        let child = records
            .start_agent_run(AgentRunRecordStart {
                request_id: &request_id,
                task_id: None,
                agent_run_id: &child_id,
                agent_name: "explorer",
                kind: &AgentRunRecordKind::Subagent {
                    parent_agent_run_id: parent_id.clone(),
                },
                system_prompt: "system",
                initial_messages: &[],
            })
            .await
            .unwrap();

        assert!(child.node_dir().join("messages.jsonl").exists());
        let parent_events = read_events_after(&parent.node_dir().join("events.jsonl"), 0)
            .await
            .unwrap();
        let child_event = parent_events
            .iter()
            .find(|event| event.kind == "child_created")
            .expect("child_created");
        assert_eq!(child_event.payload["agent_run_id"], json!("child-run"));
        assert_eq!(
            child_event.payload["path"],
            json!("subagents/subagent-run-child-run")
        );
    }

    #[test]
    fn rejects_traversal_segments() {
        for value in ["../run", "a/b", "a\\b", ".", "..", ""] {
            assert!(
                safe_segment("agent_run_id", value).is_err(),
                "{value:?} should be unsafe"
            );
        }
    }
}
