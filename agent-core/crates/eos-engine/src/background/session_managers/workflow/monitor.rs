use std::time::Duration;

use tokio::task::JoinHandle;

use super::super::BackgroundSessionManager;
use super::WorkflowSessionManager;

pub(in crate::background) struct WorkflowSessionMonitor {
    join: JoinHandle<()>,
}

impl Drop for WorkflowSessionMonitor {
    fn drop(&mut self) {
        self.join.abort();
    }
}

impl WorkflowSessionMonitor {
    pub(in crate::background) fn spawn(
        manager: WorkflowSessionManager,
        interval: Duration,
    ) -> Self {
        Self {
            join: tokio::spawn(async move {
                loop {
                    for completion in manager.poll_completions().await {
                        manager.push_notification_on_completion(completion).await;
                    }
                    tokio::time::sleep(interval).await;
                }
            }),
        }
    }
}
