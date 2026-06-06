//! [`Reaper`] release-once + terminal-status tests. Included into `crate::reaper`
//! under `#[cfg(test)]` so it can drive the `pub(crate)` reaper/`Disposition`.
#![allow(clippy::unwrap_used)]

use std::sync::Arc;

use eos_backend_store::BackendStore;
use eos_backend_types::{BackendRunStatus, RunMeta};
use eos_types::{RequestId, UtcDateTime};

use crate::event_bus::EventBus;
use crate::test_support::{self, manager, temp_store};

use super::{Disposition, Reaper};

async fn seed_accepted(store: &BackendStore, request_id: &RequestId) {
    store
        .run_meta()
        .insert(&RunMeta {
            request_id: request_id.clone(),
            status: BackendRunStatus::Accepted,
            label: None,
            client_meta: serde_json::json!({}),
            created_at: UtcDateTime::now(),
            finished_at: None,
            cancel_reason: None,
        })
        .await
        .unwrap();
}

#[tokio::test]
async fn reaper_releases_and_finalizes_done_exactly_once() {
    let (manager, teardown) = manager(4, true); // fresh ⇒ ephemeral
    let (store, _tmp) = temp_store().await;
    let reaper = Reaper::new(
        manager.clone(),
        store.run_meta().clone(),
        Arc::new(EventBus::new(store.event_log().clone())),
    );
    let request = test_support::rid("req-done");
    seed_accepted(&store, &request).await;
    let binding = manager.acquire(&request, None).await.unwrap();

    reaper.reap(&request, Disposition::Done).await;

    // released + torn down once; run finalized Done.
    assert!(manager.view(&binding.sandbox_id).is_none());
    assert_eq!(teardown.destroyed.lock().len(), 1);
    let meta = store.run_meta().get(&request).await.unwrap().unwrap();
    assert_eq!(meta.status, BackendRunStatus::Done);
    assert!(meta.finished_at.is_some());
    assert_eq!(meta.cancel_reason, None);

    // A stray second reap (the cancel-races-completion backstop) releases nothing
    // new: idempotent release means teardown still ran exactly once.
    reaper.reap(&request, Disposition::Done).await;
    assert_eq!(teardown.destroyed.lock().len(), 1);
}

#[tokio::test]
async fn reaper_finalizes_failed() {
    let (manager, _teardown) = manager(4, false);
    let (store, _tmp) = temp_store().await;
    let reaper = Reaper::new(
        manager.clone(),
        store.run_meta().clone(),
        Arc::new(EventBus::new(store.event_log().clone())),
    );
    let request = test_support::rid("req-failed");
    seed_accepted(&store, &request).await;
    manager.acquire(&request, None).await.unwrap();

    reaper.reap(&request, Disposition::Failed).await;

    let meta = store.run_meta().get(&request).await.unwrap().unwrap();
    assert_eq!(meta.status, BackendRunStatus::Failed);
    assert!(meta.finished_at.is_some());
}

#[tokio::test]
async fn reaper_writes_cancelled_with_reason_and_releases_once() {
    let (manager, teardown) = manager(4, true);
    let (store, _tmp) = temp_store().await;
    let reaper = Reaper::new(
        manager.clone(),
        store.run_meta().clone(),
        Arc::new(EventBus::new(store.event_log().clone())),
    );
    let request = test_support::rid("req-cancel");
    seed_accepted(&store, &request).await;
    let binding = manager.acquire(&request, None).await.unwrap();

    reaper
        .reap(&request, Disposition::Cancelled(Some("user asked".to_owned())))
        .await;

    assert!(manager.view(&binding.sandbox_id).is_none());
    assert_eq!(teardown.destroyed.lock().len(), 1);
    let meta = store.run_meta().get(&request).await.unwrap().unwrap();
    assert_eq!(meta.status, BackendRunStatus::Cancelled);
    assert_eq!(meta.cancel_reason.as_deref(), Some("user asked"));
    assert!(meta.finished_at.is_some());
}

#[tokio::test]
async fn reaper_leaves_a_retained_bound_sandbox_intact() {
    let (manager, teardown) = manager(4, true);
    let (store, _tmp) = temp_store().await;
    let reaper = Reaper::new(
        manager.clone(),
        store.run_meta().clone(),
        Arc::new(EventBus::new(store.event_log().clone())),
    );
    let request = test_support::rid("req-bound");
    seed_accepted(&store, &request).await;
    // Binding an existing sandbox pins it Retained; the reaper must not destroy it.
    let binding = manager.acquire(&request, Some("ext-box")).await.unwrap();

    reaper.reap(&request, Disposition::Done).await;

    assert!(manager.view(&binding.sandbox_id).is_some());
    assert!(teardown.destroyed.lock().is_empty());
    let meta = store.run_meta().get(&request).await.unwrap().unwrap();
    assert_eq!(meta.status, BackendRunStatus::Done);
}
