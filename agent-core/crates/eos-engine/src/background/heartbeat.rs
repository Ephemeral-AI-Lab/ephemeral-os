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

/// Heartbeat poll interval; `EOS_COMMAND_HEARTBEAT_MS` overrides the ~1 s default.
fn heartbeat_interval() -> Duration {
    let ms = std::env::var("EOS_COMMAND_HEARTBEAT_MS")
        .ok()
        .and_then(|value| value.parse::<u64>().ok())
        .filter(|value| *value > 0)
        .unwrap_or(1000);
    Duration::from_millis(ms)
}

/// Spawn the per-request command-completion heartbeat. The returned handle is
/// owned by the request entry and aborted at request teardown.
#[must_use]
pub fn spawn_command_completion_heartbeat(
    supervisor: Arc<Mutex<BackgroundTaskSupervisor>>,
    sink: Arc<dyn NotificationSink>,
    transport: Arc<dyn SandboxTransport>,
) -> JoinHandle<()> {
    let interval = heartbeat_interval();
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
