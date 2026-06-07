//! The command-completion heartbeat (anchor §5.3, D5): the per-request,
//! self-scheduled poller that pulls daemon completions for this supervisor's
//! running command sessions and enqueues `[BACKGROUND COMPLETED]` notifications
//! onto the request sink. The query loop never touches the daemon; this task is
//! the sole passive completion path.
//!
//! Deviation from the plan's "self-stop when idle": the task is long-lived for
//! the request and aborted on request join/shutdown. An idle tick is one
//! supervisor lock + a cheap empty-group check (no RPC), so the always-running
//! shape avoids the lazy-restart race for negligible cost.

use std::sync::Arc;
use std::time::Duration;

use eos_sandbox_port::{collect_command_completions, SandboxTransport};
use eos_tools::NotificationSink;
use eos_types::SandboxId;
use serde_json::Value;
use tokio::sync::Mutex;
use tokio::task::JoinHandle;
use tokio::time::sleep;

use super::supervisor::BackgroundTaskSupervisor;

/// Spawn the per-request command-completion heartbeat. The returned handle is
/// owned by the request entry and aborted at request teardown.
#[must_use]
pub fn spawn_command_completion_heartbeat(
    supervisor: Arc<Mutex<BackgroundTaskSupervisor>>,
    sink: Arc<dyn NotificationSink>,
    transport: Arc<dyn SandboxTransport>,
    interval: Duration,
) -> JoinHandle<()> {
    spawn_command_completion_heartbeat_with_interval(supervisor, sink, transport, interval)
}

fn spawn_command_completion_heartbeat_with_interval(
    supervisor: Arc<Mutex<BackgroundTaskSupervisor>>,
    sink: Arc<dyn NotificationSink>,
    transport: Arc<dyn SandboxTransport>,
    interval: Duration,
) -> JoinHandle<()> {
    tokio::spawn(async move {
        loop {
            let groups = {
                supervisor
                    .lock()
                    .await
                    .running_command_session_ids_by_sandbox_run()
            };
            for ((sandbox_id, agent_run_id), ids) in groups {
                let Ok(sandbox) = sandbox_id.parse::<SandboxId>() else {
                    continue;
                };
                let Ok(completions) =
                    collect_command_completions(&*transport, &sandbox, agent_run_id.as_str(), &ids)
                        .await
                else {
                    continue; // transport faults are swallowed; retried next tick
                };
                if completions.is_empty() {
                    continue;
                }
                let notifications = {
                    let mut guard = supervisor.lock().await;
                    for completion in &completions {
                        guard.ingest_completion(&Value::Object(completion.clone()));
                    }
                    guard.drain_command_session_notifications()
                };
                for notification in notifications {
                    let _ = sink.notify_system(notification).await;
                }
            }
            sleep(interval).await;
        }
    })
}

#[cfg(test)]
mod tests {
    #![allow(clippy::expect_used)]

    use std::collections::VecDeque;

    use async_trait::async_trait;
    use eos_sandbox_port::{DaemonOp, SandboxPortError};
    use serde_json::json;
    use tokio::time::{timeout, Duration};

    use super::*;
    use crate::NotificationService;

    #[derive(Debug, Default)]
    struct RecordingTransport {
        calls: std::sync::Mutex<Vec<(SandboxId, DaemonOp, eos_types::JsonObject)>>,
        responses: std::sync::Mutex<VecDeque<Result<eos_types::JsonObject, SandboxPortError>>>,
    }

    impl RecordingTransport {
        fn with_responses(
            responses: impl IntoIterator<Item = Result<eos_types::JsonObject, SandboxPortError>>,
        ) -> Self {
            Self {
                calls: std::sync::Mutex::new(Vec::new()),
                responses: std::sync::Mutex::new(responses.into_iter().collect()),
            }
        }

        fn calls(&self) -> Vec<(SandboxId, DaemonOp, eos_types::JsonObject)> {
            self.calls.lock().expect("calls lock").clone()
        }
    }

    #[async_trait]
    impl SandboxTransport for RecordingTransport {
        async fn call(
            &self,
            sandbox_id: &SandboxId,
            op: DaemonOp,
            payload: eos_types::JsonObject,
            _timeout_s: u32,
        ) -> Result<eos_types::JsonObject, SandboxPortError> {
            self.calls
                .lock()
                .expect("calls lock")
                .push((sandbox_id.clone(), op, payload));
            self.responses
                .lock()
                .expect("responses lock")
                .pop_front()
                .unwrap_or_else(|| Ok(eos_types::JsonObject::new()))
        }
    }

    fn completion_response(id: &str, status: &str, stdout: &str) -> eos_types::JsonObject {
        json!({
            "completions": [{
                "command_session_id": id,
                "result": {
                    "status": status,
                    "exit_code": if status == "ok" { 0 } else { 1 },
                    "output": {"stdout": stdout, "stderr": ""}
                }
            }]
        })
        .as_object()
        .expect("object")
        .clone()
    }

    #[tokio::test]
    async fn heartbeat_polls_completions_and_enqueues_once() {
        let supervisor = Arc::new(Mutex::new(BackgroundTaskSupervisor::new()));
        let sink = NotificationService::new();
        supervisor.lock().await.register_command_session(
            &"cmd_1".parse().expect("command id"),
            &"sandbox-a".parse().expect("sandbox id"),
            &"agent-a".parse().expect("agent run id"),
            "cargo test -q",
        );
        let transport = Arc::new(RecordingTransport::with_responses([Ok(
            completion_response("cmd_1", "ok", "3 passed"),
        )]));
        let handle = spawn_command_completion_heartbeat_with_interval(
            supervisor.clone(),
            Arc::new(sink.clone()),
            transport.clone(),
            Duration::from_millis(1),
        );

        let notifications = timeout(Duration::from_millis(100), async {
            loop {
                let drained = sink.drain().await;
                if !drained.is_empty() {
                    break drained;
                }
                sleep(Duration::from_millis(2)).await;
            }
        })
        .await
        .expect("notification");
        handle.abort();

        assert_eq!(notifications.len(), 1);
        assert!(notifications[0].message.contains("[BACKGROUND COMPLETED]"));
        assert!(notifications[0].message.contains("cmd_1"));
        assert!(notifications[0].message.contains("3 passed"));
        assert!(
            supervisor
                .lock()
                .await
                .drain_command_session_notifications()
                .is_empty(),
            "delivered latch suppresses a second notification"
        );
        let calls = transport.calls();
        assert_eq!(calls[0].1, DaemonOp::CommandCollectCompleted);
        assert_eq!(calls[0].2["caller_id"], json!("agent-a"));
    }

    #[tokio::test]
    async fn heartbeat_retries_after_transport_error() {
        let supervisor = Arc::new(Mutex::new(BackgroundTaskSupervisor::new()));
        let sink = NotificationService::new();
        supervisor.lock().await.register_command_session(
            &"cmd_2".parse().expect("command id"),
            &"sandbox-a".parse().expect("sandbox id"),
            &"agent-a".parse().expect("agent run id"),
            "make",
        );
        let transport = Arc::new(RecordingTransport::with_responses([
            Err(SandboxPortError::transport(
                None,
                "temporary transport fault",
            )),
            Ok(completion_response("cmd_2", "error", "boom")),
        ]));
        let handle = spawn_command_completion_heartbeat_with_interval(
            supervisor,
            Arc::new(sink.clone()),
            transport.clone(),
            Duration::from_millis(1),
        );

        let notifications = timeout(Duration::from_millis(100), async {
            loop {
                let drained = sink.drain().await;
                if !drained.is_empty() {
                    break drained;
                }
                sleep(Duration::from_millis(2)).await;
            }
        })
        .await
        .expect("notification after retry");
        handle.abort();

        assert!(notifications[0].message.contains("status=error"));
        assert!(
            transport.calls().len() >= 2,
            "transport error is swallowed and retried next tick"
        );
    }
}
