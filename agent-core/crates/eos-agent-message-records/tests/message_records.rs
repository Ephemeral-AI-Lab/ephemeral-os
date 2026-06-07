#![allow(clippy::expect_used, clippy::unwrap_used)]

use eos_agent_message_records::{
    AgentMessageRecords, AgentRunRecordKind, AgentRunRecordStart, MessageRecordError,
};
use eos_llm_client::{ContentBlock, Message, MessageRole};
use eos_types::{AgentRunId, RequestId, TaskId};
use serde_json::{json, Value};

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
    records
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
    let parent_events = records.read_events(&parent_id, 0).await.unwrap();
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

#[tokio::test]
async fn subagent_and_advisor_records_resolve_by_agent_run_id() {
    let dir = tempfile::tempdir().unwrap();
    let records = AgentMessageRecords::new(dir.path());
    let (request_id, task_id, parent_id) = ids();
    records
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

    let subagent_id: AgentRunId = "subagent-1".parse().unwrap();
    records
        .start_agent_run(AgentRunRecordStart {
            request_id: &request_id,
            task_id: None,
            agent_run_id: &subagent_id,
            agent_name: "explorer",
            kind: &AgentRunRecordKind::Subagent {
                parent_agent_run_id: parent_id.clone(),
            },
            system_prompt: "subagent system",
            initial_messages: &[],
        })
        .await
        .unwrap();
    assert!(!records
        .read_messages(&subagent_id, 0)
        .await
        .unwrap()
        .bytes
        .is_empty());

    let advisor_id: AgentRunId = "advisor-1".parse().unwrap();
    records
        .start_agent_run(AgentRunRecordStart {
            request_id: &request_id,
            task_id: None,
            agent_run_id: &advisor_id,
            agent_name: "advisor",
            kind: &AgentRunRecordKind::Advisor {
                parent_agent_run_id: parent_id,
            },
            system_prompt: "advisor system",
            initial_messages: &[],
        })
        .await
        .unwrap();
    assert!(!records
        .read_events(&advisor_id, 0)
        .await
        .unwrap()
        .is_empty());
}

#[tokio::test]
async fn unknown_agent_run_is_not_found() {
    let dir = tempfile::tempdir().unwrap();
    let records = AgentMessageRecords::new(dir.path());
    let missing: AgentRunId = "missing-run".parse().unwrap();

    assert!(matches!(
        records.read_events(&missing, 0).await,
        Err(MessageRecordError::NotFound(_))
    ));
}
