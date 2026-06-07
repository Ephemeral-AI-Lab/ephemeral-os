use std::time::Duration;

use tokio::task::JoinHandle;

use super::super::BackgroundSessionManager;
use super::CommandSessionManager;

pub(in crate::background) struct CommandSessionMonitor {
    join: JoinHandle<()>,
}

impl Drop for CommandSessionMonitor {
    fn drop(&mut self) {
        self.join.abort();
    }
}

impl CommandSessionMonitor {
    pub(in crate::background) fn spawn(manager: CommandSessionManager, interval: Duration) -> Self {
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
