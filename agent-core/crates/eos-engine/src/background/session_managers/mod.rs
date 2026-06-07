//! Private background-session manager contracts and concrete families.

use std::hash::Hash;
use std::time::Duration;

use async_trait::async_trait;
use tokio::task::JoinHandle;

pub(super) mod command;
pub(super) mod subagent;
pub(super) mod workflow;

/// Lifecycle status for one tracked background session.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum BackgroundSessionStatus {
    /// The session is still running.
    Running,
    /// The session completed normally.
    Completed,
    /// The session failed.
    Failed,
    /// The session was cancelled.
    Cancelled,
    /// The terminal result was already delivered to the model.
    Delivered,
}

impl BackgroundSessionStatus {
    /// Terminal precedence; higher status wins when cancel/finish events race.
    #[must_use]
    pub const fn precedence(self) -> u8 {
        match self {
            Self::Running => 0,
            Self::Cancelled => 1,
            Self::Failed => 2,
            Self::Completed => 3,
            Self::Delivered => 4,
        }
    }
}

pub(super) trait BackgroundSession {
    type Id: Eq + Hash + Clone + Send + Sync + 'static;

    fn id(&self) -> &Self::Id;
}

#[async_trait]
pub(super) trait BackgroundSessionManager {
    type Session: BackgroundSession + Send + 'static;
    type Completion: Send + 'static;

    async fn insert(&self, session: Self::Session);
    async fn count(&self) -> usize;
    async fn poll(&self) -> Vec<Self::Completion>;
    async fn finish(&self, completion: Self::Completion);
    async fn cancel(&self, reason: &str);
}

pub(super) trait BackgroundSessionMonitor {
    type Manager: BackgroundSessionManager + Clone + Send + Sync + 'static;

    fn spawn(manager: Self::Manager, interval: Duration) -> Self;
}

pub(super) fn spawn_monitor_loop<M>(manager: M, interval: Duration) -> JoinHandle<()>
where
    M: BackgroundSessionManager + Clone + Send + Sync + 'static,
    M::Completion: Send + 'static,
{
    tokio::spawn(async move {
        loop {
            for completion in manager.poll().await {
                manager.finish(completion).await;
            }
            tokio::time::sleep(interval).await;
        }
    })
}
