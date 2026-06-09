//! `SandboxManager` lifecycle/refcount/delete-guard tests.
//!
//! Included into `crate::sandbox_manager` under `#[cfg(test)]` (spec §Backend Test
//! Layout) so the fakes can implement the crate-internal `SandboxTeardown` seam
//! and drive `with_seams`. The `ProviderAdapter` host trait is sealed, so the
//! refcount/delete logic is exercised against fakes rather than real Docker.
#![allow(clippy::unwrap_used)]

use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::Arc;
use std::time::Duration;

use async_trait::async_trait;
use parking_lot::Mutex;

use eos_backend_types::SandboxState;
use eos_sandbox_port::{
    DaemonOp, RequestProvisioner, RequestSandboxBinding, SandboxGateway, SandboxPortError,
    SandboxProvisionError, SandboxTransport,
};
use eos_types::{JsonObject, RequestId, SandboxId};

use super::{DeleteRejection, SandboxManager, SandboxManagerError, SandboxTeardown};

// --- fakes -------------------------------------------------------------------

/// Records its calls and mints `sb-for-<request>` for fresh acquisitions, echoing
/// the explicit id otherwise. `fail` is toggleable so a test can prove a failed
/// acquire burned no capacity by succeeding on the retry.
#[derive(Debug, Default)]
struct FakeProvisioner {
    calls: Mutex<Vec<(RequestId, Option<String>)>>,
    fail: AtomicBool,
    delay_ms: AtomicU64,
}

#[async_trait]
impl RequestProvisioner for FakeProvisioner {
    async fn prepare_for_run(
        &self,
        request_id: &RequestId,
        sandbox_id: Option<&str>,
    ) -> Result<RequestSandboxBinding, SandboxProvisionError> {
        self.calls
            .lock()
            .push((request_id.clone(), sandbox_id.map(str::to_owned)));
        let delay_ms = self.delay_ms.load(Ordering::SeqCst);
        if delay_ms > 0 {
            tokio::time::sleep(Duration::from_millis(delay_ms)).await;
        }
        if self.fail.load(Ordering::SeqCst) {
            return Err(SandboxProvisionError::new("provision boom"));
        }
        let sandbox_id: SandboxId = match sandbox_id {
            Some(id) => id.parse().unwrap(),
            None => format!("sb-for-{request_id}").parse().unwrap(),
        };
        Ok(RequestSandboxBinding {
            sandbox_id,
            request_id: request_id.clone(),
        })
    }
}

/// Records every destroy and can be made to fail.
#[derive(Debug, Default)]
struct FakeTeardown {
    destroyed: Mutex<Vec<SandboxId>>,
    fail: bool,
}

#[async_trait]
impl SandboxTeardown for FakeTeardown {
    async fn destroy(&self, id: &SandboxId) -> Result<(), SandboxManagerError> {
        self.destroyed.lock().push(id.clone());
        if self.fail {
            return Err(SandboxManagerError::Teardown("teardown boom".to_owned()));
        }
        Ok(())
    }
}

/// Never invoked by manager lifecycle tests; present only to satisfy the gateway.
#[derive(Debug, Default)]
struct FakeTransport;

#[async_trait]
impl SandboxTransport for FakeTransport {
    async fn call(
        &self,
        _sandbox_id: &SandboxId,
        _op: DaemonOp,
        _payload: JsonObject,
        _timeout_s: u32,
    ) -> Result<JsonObject, SandboxPortError> {
        Err(SandboxPortError::transport(
            None,
            "fake transport unused in manager tests",
        ))
    }
}

// --- harness -----------------------------------------------------------------

#[derive(Debug)]
struct Harness {
    manager: SandboxManager,
    provisioner: Arc<FakeProvisioner>,
    teardown: Arc<FakeTeardown>,
}

fn harness(max_owned: usize, destroy_on_finish: bool) -> Harness {
    build(max_owned, destroy_on_finish, false, false)
}

fn build(
    max_owned: usize,
    destroy_on_finish: bool,
    provision_fail: bool,
    teardown_fail: bool,
) -> Harness {
    build_with_delay(
        max_owned,
        destroy_on_finish,
        provision_fail,
        teardown_fail,
        Duration::ZERO,
    )
}

fn build_with_delay(
    max_owned: usize,
    destroy_on_finish: bool,
    provision_fail: bool,
    teardown_fail: bool,
    provision_delay: Duration,
) -> Harness {
    let provisioner = Arc::new(FakeProvisioner {
        fail: AtomicBool::new(provision_fail),
        delay_ms: AtomicU64::new(
            provision_delay
                .as_millis()
                .try_into()
                .expect("test delay fits u64"),
        ),
        ..Default::default()
    });
    let teardown = Arc::new(FakeTeardown {
        fail: teardown_fail,
        ..Default::default()
    });
    let transport: Arc<dyn SandboxTransport> = Arc::new(FakeTransport);
    let manager = SandboxManager::with_seams(
        provisioner.clone(),
        transport,
        teardown.clone(),
        max_owned,
        destroy_on_finish,
    );
    Harness {
        manager,
        provisioner,
        teardown,
    }
}

fn rid(s: &str) -> RequestId {
    s.parse().unwrap()
}

fn sid(s: &str) -> SandboxId {
    s.parse().unwrap()
}

// --- create / bind -----------------------------------------------------------

#[tokio::test]
async fn create_fresh_tracks_owner_and_refcount() {
    let h = harness(4, true);
    let binding = h.manager.acquire(&rid("req-1"), None).await.unwrap();
    assert_eq!(binding.sandbox_id.as_str(), "sb-for-req-1");

    let view = h.manager.view(&binding.sandbox_id).unwrap();
    assert_eq!(view.state, SandboxState::Active);
    assert_eq!(
        view.owner_request_id.as_ref().map(RequestId::as_str),
        Some("req-1")
    );
    assert_eq!(view.active_request_ids.len(), 1);
    assert_eq!(view.ref_count, 1);
    assert!(view.destroy_on_finish);
}

#[tokio::test]
async fn bind_existing_is_retained_and_unowned() {
    let h = harness(4, true);
    let binding = h
        .manager
        .acquire(&rid("req-1"), Some("ext-box"))
        .await
        .unwrap();
    assert_eq!(binding.sandbox_id.as_str(), "ext-box");

    let view = h.manager.view(&sid("ext-box")).unwrap();
    assert_eq!(view.state, SandboxState::Active);
    assert_eq!(view.owner_request_id, None);
    assert!(!view.destroy_on_finish);
    // one active run + one retained pin.
    assert_eq!(view.ref_count, 2);

    // the pin keeps it Retained once the run finishes; it is never destroyed.
    h.manager.release(&rid("req-1")).await;
    let view = h.manager.view(&sid("ext-box")).unwrap();
    assert_eq!(view.state, SandboxState::Retained);
    assert_eq!(view.ref_count, 1);
    assert!(view.active_request_ids.is_empty());
    assert!(h.teardown.destroyed.lock().is_empty());
}

#[tokio::test]
async fn multi_ref_destroys_after_last_active() {
    let h = harness(4, true);
    let a = h.manager.acquire(&rid("req-1"), None).await.unwrap();
    // a second request binds the same backend-owned sandbox by id.
    let b = h
        .manager
        .acquire(&rid("req-2"), Some(a.sandbox_id.as_str()))
        .await
        .unwrap();
    assert_eq!(a.sandbox_id, b.sandbox_id);

    let view = h.manager.view(&a.sandbox_id).unwrap();
    assert_eq!(view.ref_count, 2);
    assert_eq!(view.active_request_ids.len(), 2);

    h.manager.release(&rid("req-1")).await;
    let view = h.manager.view(&a.sandbox_id).unwrap();
    assert_eq!(view.state, SandboxState::Active);
    assert_eq!(view.ref_count, 1);
    assert!(h.teardown.destroyed.lock().is_empty());

    h.manager.release(&rid("req-2")).await;
    assert!(h.manager.view(&a.sandbox_id).is_none());
    assert_eq!(h.teardown.destroyed.lock().len(), 1);
}

// --- release / destroy-on-finish ---------------------------------------------

#[tokio::test]
async fn release_keeps_when_not_destroy_on_finish() {
    let h = harness(4, false);
    let binding = h.manager.acquire(&rid("req-1"), None).await.unwrap();
    h.manager.release(&rid("req-1")).await;

    let view = h.manager.view(&binding.sandbox_id).unwrap();
    assert_eq!(view.state, SandboxState::Ready);
    assert_eq!(view.ref_count, 0);
    assert!(h.teardown.destroyed.lock().is_empty());
}

#[tokio::test]
async fn destroy_on_finish_tears_down_once_on_last_release() {
    let h = harness(4, true);
    let binding = h.manager.acquire(&rid("req-1"), None).await.unwrap();
    h.manager.release(&rid("req-1")).await;
    assert!(h.manager.view(&binding.sandbox_id).is_none());

    // a second release is a no-op: teardown ran exactly once.
    h.manager.release(&rid("req-1")).await;
    let destroyed = h.teardown.destroyed.lock();
    assert_eq!(destroyed.len(), 1);
    assert_eq!(destroyed[0].as_str(), "sb-for-req-1");
}

#[tokio::test]
async fn release_swallows_teardown_failure_and_keeps_entry_for_retry() {
    // destroy-on-finish fires on the last release, but the host teardown fails.
    let h = build(4, true, false, true);
    let binding = h.manager.acquire(&rid("req-1"), None).await.unwrap();

    // the failure is swallowed (logged), not propagated through release; the entry
    // is left in Destroying with no refs so a later DELETE can retry the teardown.
    // This is the release counterpart to delete_propagates_teardown_failure: delete
    // surfaces the error, release does not.
    h.manager.release(&rid("req-1")).await;
    let view = h
        .manager
        .view(&binding.sandbox_id)
        .expect("entry kept for retry after destroy-on-finish teardown failure");
    assert_eq!(view.state, SandboxState::Destroying);
    assert_eq!(view.ref_count, 0);
    assert_eq!(h.teardown.destroyed.lock().len(), 1);

    // release stays idempotent on the failure path: the ref was already dropped,
    // so a second release does not re-attempt teardown.
    h.manager.release(&rid("req-1")).await;
    assert_eq!(h.teardown.destroyed.lock().len(), 1);
}

#[tokio::test]
async fn acquire_is_idempotent_per_request() {
    let h = harness(4, true);
    let first = h.manager.acquire(&rid("req-1"), None).await.unwrap();
    let second = h.manager.acquire(&rid("req-1"), None).await.unwrap();
    assert_eq!(first.sandbox_id, second.sandbox_id);

    let view = h.manager.view(&first.sandbox_id).unwrap();
    assert_eq!(view.ref_count, 1);
    assert_eq!(view.active_request_ids.len(), 1);
    // the fast path short-circuits before re-provisioning.
    assert_eq!(h.provisioner.calls.lock().len(), 1);
}

// --- capacity ----------------------------------------------------------------

#[tokio::test]
async fn capacity_exceeded_for_fresh_only() {
    let h = harness(1, false);
    h.manager.acquire(&rid("req-1"), None).await.unwrap();

    let err = h.manager.acquire(&rid("req-2"), None).await.unwrap_err();
    assert!(matches!(
        err,
        SandboxManagerError::CapacityExceeded { current: 1, max: 1 }
    ));

    // binding an existing sandbox does not count against the owned budget.
    h.manager
        .acquire(&rid("req-3"), Some("ext-box"))
        .await
        .unwrap();
}

#[tokio::test]
async fn failed_acquire_leaks_no_state_or_capacity() {
    // provisioner fails, max_owned == 1 so a leaked slot would block the retry.
    let h = build(1, false, true, false);
    let err = h.manager.acquire(&rid("req-1"), None).await.unwrap_err();
    assert!(matches!(err, SandboxManagerError::Provision(_)));
    assert!(
        h.manager.list().is_empty(),
        "failed acquire must track nothing"
    );

    // the failed attempt burned no capacity: a fresh acquire now succeeds at the
    // budget of 1.
    h.provisioner.fail.store(false, Ordering::SeqCst);
    h.manager.acquire(&rid("req-2"), None).await.unwrap();
    assert_eq!(h.manager.list().len(), 1);
}

#[tokio::test]
async fn pending_fresh_acquire_counts_against_capacity() {
    let h = build_with_delay(1, false, false, false, Duration::from_millis(50));
    let provisioner = h.provisioner.clone();
    let manager = Arc::new(h.manager);
    let first = {
        let manager = manager.clone();
        tokio::spawn(async move { manager.acquire(&rid("req-1"), None).await })
    };

    while provisioner.calls.lock().is_empty() {
        tokio::task::yield_now().await;
    }

    let err = manager.acquire(&rid("req-2"), None).await.unwrap_err();
    assert!(matches!(
        err,
        SandboxManagerError::CapacityExceeded { current: 1, max: 1 }
    ));

    first.await.unwrap().unwrap();
    assert_eq!(manager.list().len(), 1);
}

#[tokio::test]
async fn dropped_fresh_acquire_releases_pending_capacity() {
    let h = build_with_delay(1, false, false, false, Duration::from_secs(60));
    let provisioner = h.provisioner.clone();
    let manager = Arc::new(h.manager);
    let first = {
        let manager = manager.clone();
        tokio::spawn(async move { manager.acquire(&rid("req-1"), None).await })
    };

    while provisioner.calls.lock().is_empty() {
        tokio::task::yield_now().await;
    }
    first.abort();
    let _ = first.await;

    provisioner.delay_ms.store(0, Ordering::SeqCst);
    manager.acquire(&rid("req-2"), None).await.unwrap();
    assert_eq!(manager.list().len(), 1);
}

// --- delete guards -----------------------------------------------------------

#[tokio::test]
async fn delete_rejected_while_active() {
    let h = harness(4, false);
    let binding = h.manager.acquire(&rid("req-1"), None).await.unwrap();

    let err = h.manager.delete(&binding.sandbox_id).await.unwrap_err();
    assert!(matches!(
        err,
        SandboxManagerError::DeleteRejected {
            reason: DeleteRejection::Active,
            ..
        }
    ));
    assert!(h.manager.view(&binding.sandbox_id).is_some());
    assert!(h.teardown.destroyed.lock().is_empty());
}

#[tokio::test]
async fn delete_rejected_while_retained() {
    let h = harness(4, true);
    h.manager
        .acquire(&rid("req-1"), Some("ext-box"))
        .await
        .unwrap();
    h.manager.release(&rid("req-1")).await; // -> Retained

    let err = h.manager.delete(&sid("ext-box")).await.unwrap_err();
    assert!(matches!(
        err,
        SandboxManagerError::DeleteRejected {
            reason: DeleteRejection::Retained,
            ..
        }
    ));
    assert!(h.manager.view(&sid("ext-box")).is_some());
}

#[tokio::test]
async fn delete_allows_ready_and_tears_down() {
    let h = harness(4, false); // fresh, non-ephemeral -> Ready after release
    let binding = h.manager.acquire(&rid("req-1"), None).await.unwrap();
    h.manager.release(&rid("req-1")).await;
    assert_eq!(
        h.manager.view(&binding.sandbox_id).unwrap().state,
        SandboxState::Ready
    );

    h.manager.delete(&binding.sandbox_id).await.unwrap();
    assert!(h.manager.view(&binding.sandbox_id).is_none());
    assert_eq!(h.teardown.destroyed.lock().len(), 1);
}

#[tokio::test]
async fn delete_unknown_sandbox() {
    let h = harness(4, false);
    let err = h.manager.delete(&sid("ghost")).await.unwrap_err();
    assert!(matches!(err, SandboxManagerError::UnknownSandbox(_)));
}

#[tokio::test]
async fn delete_propagates_teardown_failure_and_keeps_entry() {
    let h = build(4, false, false, true); // non-ephemeral; host teardown fails
    let binding = h.manager.acquire(&rid("req-1"), None).await.unwrap();
    h.manager.release(&rid("req-1")).await; // -> Ready (no auto-destroy)

    let err = h.manager.delete(&binding.sandbox_id).await.unwrap_err();
    assert!(matches!(err, SandboxManagerError::Teardown(_)));
    // the entry is kept (in Destroying) so a DELETE retry can proceed.
    let view = h
        .manager
        .view(&binding.sandbox_id)
        .expect("entry retained for retry after teardown failure");
    assert_eq!(view.state, SandboxState::Destroying);
    assert_eq!(h.teardown.destroyed.lock().len(), 1);
}

#[tokio::test]
async fn acquire_rejects_destroying_sandbox() {
    let h = build(4, true, false, true);
    let binding = h.manager.acquire(&rid("req-1"), None).await.unwrap();
    h.manager.release(&rid("req-1")).await;
    assert_eq!(
        h.manager.view(&binding.sandbox_id).unwrap().state,
        SandboxState::Destroying
    );

    let err = h
        .manager
        .acquire(&rid("req-2"), Some(binding.sandbox_id.as_str()))
        .await
        .unwrap_err();
    assert!(matches!(err, SandboxManagerError::Provision(_)));

    let view = h.manager.view(&binding.sandbox_id).unwrap();
    assert_eq!(view.state, SandboxState::Destroying);
    assert_eq!(view.ref_count, 0);
    assert!(
        view.active_request_ids.is_empty(),
        "destroying sandbox must not gain a new active request"
    );
    assert_eq!(
        h.provisioner.calls.lock().len(),
        1,
        "destroying guard should reject before host start/provision"
    );
}

// --- gateway wiring + sanitization ------------------------------------------

#[tokio::test]
async fn gateway_provisioner_and_manager_share_state() {
    let h = harness(4, false);
    // Acquire through the gateway provisioner — the exact path eos-agent-core drives
    // after `build()` calls `provisioner()` once and drops the gateway handle.
    let provisioner = h.manager.provisioner();
    let binding = provisioner
        .prepare_for_run(&rid("req-1"), None)
        .await
        .unwrap();

    // the retained manager handle observes that acquisition (shared inner state).
    let view = h
        .manager
        .view(&binding.sandbox_id)
        .expect("manager and gateway provisioner share state");
    assert_eq!(view.state, SandboxState::Active);
    assert_eq!(view.ref_count, 1);

    // release through the manager decrements the same refcount.
    h.manager.release(&rid("req-1")).await;
    let view = h.manager.view(&binding.sandbox_id).unwrap();
    assert_eq!(view.state, SandboxState::Ready);
    assert_eq!(view.ref_count, 0);

    // transport() returns a stable shared handle.
    assert!(Arc::ptr_eq(&h.manager.transport(), &h.manager.transport()));
}

#[tokio::test]
async fn sanitized_view_has_no_daemon_credentials() {
    let h = harness(4, true);
    let binding = h.manager.acquire(&rid("req-1"), None).await.unwrap();
    let view = h.manager.view(&binding.sandbox_id).unwrap();

    let json = serde_json::to_value(&view).unwrap();
    let obj = json.as_object().unwrap();
    for denied in ["host", "port", "internal_port", "endpoint", "auth_token"] {
        assert!(
            !obj.contains_key(denied),
            "SandboxView must not expose {denied}"
        );
    }
}

#[tokio::test]
async fn list_returns_sanitized_views_newest_first() {
    let h = harness(4, true);
    h.manager.acquire(&rid("req-1"), None).await.unwrap();
    h.manager.acquire(&rid("req-2"), None).await.unwrap();

    let views = h.manager.list();
    assert_eq!(views.len(), 2);
    // created_at is monotonic in acquisition order; newest first.
    assert!(views[0].created_at >= views[1].created_at);
}
