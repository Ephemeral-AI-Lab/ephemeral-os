use std::time::Duration;

use tokio::task::JoinHandle;

use super::super::BackgroundSessionManager;
use super::SubagentSessionManager;

pub(in crate::background) struct SubagentSessionMonitor {
    join: JoinHandle<()>,
}

impl Drop for SubagentSessionMonitor {
    fn drop(&mut self) {
        self.join.abort();
    }
}

impl SubagentSessionMonitor {
    pub(in crate::background) fn spawn(
        manager: SubagentSessionManager,
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
