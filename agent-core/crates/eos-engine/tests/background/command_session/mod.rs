#![allow(clippy::expect_used)]

use std::collections::VecDeque;
use std::sync::Mutex as StdMutex;
use std::time::Duration;

use eos_sandbox_port::{DaemonOp, SandboxPortError, SandboxTransport};
use eos_types::JsonObject;
use serde_json::json;
use tokio::time::{sleep, timeout};

use super::*;
use crate::notifications::EngineNotificationQueue;
use eos_sandbox_port::SandboxCommandService;

#[derive(Debug, Default)]
struct CommandSessionTestTransport {
    calls: StdMutex<Vec<(DaemonOp, JsonObject)>>,
    collect_responses: StdMutex<VecDeque<JsonObject>>,
}

impl CommandSessionTestTransport {
    fn with_collect(responses: impl IntoIterator<Item = JsonObject>) -> Self {
        Self {
            calls: StdMutex::new(Vec::new()),
            collect_responses: StdMutex::new(responses.into_iter().collect()),
        }
    }

    fn payloads(&self, op: DaemonOp) -> Vec<JsonObject> {
        self.calls
            .lock()
            .expect("calls")
            .iter()
            .filter(|(call_op, _)| *call_op == op)
            .map(|(_, payload)| payload.clone())
            .collect()
    }
}

#[async_trait]
impl SandboxTransport for CommandSessionTestTransport {
    async fn call(
        &self,
        _sandbox_id: &SandboxId,
        op: DaemonOp,
        payload: JsonObject,
        _timeout_s: u32,
    ) -> Result<JsonObject, SandboxPortError> {
        self.calls.lock().expect("calls").push((op, payload));
        let response = match op {
            DaemonOp::CommandCollectCompleted => self
                .collect_responses
                .lock()
                .expect("responses")
                .pop_front()
                .unwrap_or_default(),
            _ => json!({"success": true})
                .as_object()
                .expect("object")
                .clone(),
        };
        Ok(response)
    }
}

fn completion(id: &str, status: &str, stdout: &str) -> JsonObject {
    json!({
        "completions": [{
            "command_session_id": id,
            "result": {
                "status": status,
                "exit_code": if status == "ok" { 0 } else { 1 },
                "output": {"stdout": stdout, "stderr": ""},
            },
        }]
    })
    .as_object()
    .expect("object")
    .clone()
}

fn manager(
    owner: &str,
    notifier: &EngineNotificationQueue,
    transport: Arc<dyn SandboxTransport>,
) -> CommandSessionManager {
    CommandSessionManager::new(
        owner.parse().expect("agent run id"),
        Arc::new(SandboxCommandService::new(transport)),
        BackgroundNotificationEmitter::new(notifier.clone()),
    )
}

#[tokio::test]
async fn monitor_polls_and_emits_into_own_notifier() {
    let transport = Arc::new(CommandSessionTestTransport::with_collect([completion(
        "cmd_1", "ok", "3 passed",
    )]));
    let notifier = EngineNotificationQueue::new();
    let manager = manager("agent-a", &notifier, transport.clone());
    let _monitor = CommandSessionMonitor::spawn(manager.clone(), Duration::from_millis(1));
    sleep(Duration::from_millis(20)).await;
    assert!(
        transport
            .payloads(DaemonOp::CommandCollectCompleted)
            .is_empty(),
        "idle monitor should not poll before a session is registered"
    );

    manager
        .register_background_session(
            &"cmd_1".parse().expect("command id"),
            &"sandbox-a".parse().expect("sandbox id"),
        )
        .await;

    let notifications = timeout(Duration::from_millis(200), async {
        loop {
            let drained = notifier.drain().await;
            if !drained.is_empty() {
                break drained;
            }
            sleep(Duration::from_millis(2)).await;
        }
    })
    .await
    .expect("notification");

    assert_eq!(notifications.len(), 1);
    assert!(notifications[0].message.contains("[BACKGROUND COMPLETED]"));
    assert!(notifications[0].message.contains("cmd_1"));
    assert!(notifications[0].message.contains("3 passed"));
    let collect = transport.payloads(DaemonOp::CommandCollectCompleted);
    assert!(!collect.is_empty());
    assert_eq!(collect[0]["caller_id"], json!("agent-a"));
    assert_eq!(manager.count().await, 0);
}

#[tokio::test]
async fn cancel_issues_one_per_caller_rpc() {
    let transport = Arc::new(CommandSessionTestTransport::default());
    let notifier = EngineNotificationQueue::new();
    let manager = manager("agent-a", &notifier, transport.clone());
    for id in ["cmd_1", "cmd_2"] {
        manager
            .register_background_session(
                &id.parse().expect("command id"),
                &"sandbox-a".parse().expect("sandbox id"),
            )
            .await;
    }
    manager.cancel("parent exited").await;
    let cancels = transport.payloads(DaemonOp::CancelWorkspaceRunsByCaller);
    assert_eq!(cancels.len(), 1, "one per-caller cancel for two sessions");
    assert_eq!(cancels[0]["caller_id"], json!("agent-a"));
    assert_eq!(manager.count().await, 0);
}
