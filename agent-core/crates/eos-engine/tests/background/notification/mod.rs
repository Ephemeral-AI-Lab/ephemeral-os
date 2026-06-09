#![allow(clippy::expect_used)]

use super::*;

/// Spec §8.4/§9.1: a subagent completion renders the `[BACKGROUND COMPLETED]`
/// body with the typed session id and lands in the wrapped notifier; a second
/// notifier never sees it (instance isolation, §13.1).
#[tokio::test]
async fn emits_subagent_completion_into_its_own_notifier() {
    let notifier = EngineNotificationQueue::new();
    let other = EngineNotificationQueue::new();
    let emitter = BackgroundNotificationEmitter::new(notifier.clone());

    emitter
        .emit(BackgroundCompletion::Subagent {
            agent_run_id: "run-sub-1".parse().expect("id"),
            status: BackgroundSessionStatus::Completed,
            result: ToolResult::ok("did the work"),
        })
        .await
        .expect("emit");

    assert!(other.drain().await.is_empty(), "isolated from other runs");
    let drained = notifier.drain().await;
    assert_eq!(drained.len(), 1, "exactly one completion notification");
    assert_eq!(drained[0].event, "run-sub-1");
    assert!(drained[0]
        .message
        .starts_with("[BACKGROUND COMPLETED] agent_run_id=run-sub-1"));
    assert!(drained[0].message.contains("status=completed"));
    assert!(drained[0].message.contains("did the work"));
}
