//! [`RunLauncher`] lifecycle + cancellation tests. Included into `crate::launcher`
//! under `#[cfg(test)]` so it can build the launcher over the crate-internal
//! `SandboxManager::with_seams` fakes and the `FakeRunHost` seam.
#![allow(clippy::unwrap_used)]

use std::sync::Arc;

use tokio::sync::Notify;

use eos_backend_types::{BackendRunStatus, CreateUserRequest, SandboxArgs};

use crate::event_bus::EventBus;
use crate::host::RunOutcome;
use crate::test_support::{
    await_host_started, await_run_finished, failing_manager, gated_manager, manager, temp_store,
    FakeRunHost,
};

use super::{CancelOutcome, RunLauncher};

fn request(prompt: &str, sandbox_id: Option<&str>) -> CreateUserRequest {
    CreateUserRequest {
        prompt: prompt.to_owned(),
        sandbox_args: sandbox_id.map(|id| SandboxArgs {
            sandbox_id: Some(id.parse().unwrap()),
        }),
        client_meta: None,
    }
}

/// Bundles the launcher with the store + teardown spy a test inspects.
struct Harness {
    launcher: RunLauncher,
    store: eos_backend_store::BackendStore,
    teardown: Arc<crate::test_support::FakeTeardown>,
    manager: Arc<crate::sandbox_manager::SandboxManager>,
    _tmp: tempfile::TempDir,
}

/// Build a launcher + its store/teardown handles over fakes.
async fn setup(host: Arc<FakeRunHost>, max_owned: usize, destroy_on_finish: bool) -> Harness {
    setup_with(host, manager(max_owned, destroy_on_finish)).await
}

/// Build a launcher over a pre-built manager — lets a test inject a failing
/// manager to exercise the sandbox-acquisition-failure arm.
async fn setup_with(
    host: Arc<FakeRunHost>,
    (manager, teardown): (
        Arc<crate::sandbox_manager::SandboxManager>,
        Arc<crate::test_support::FakeTeardown>,
    ),
) -> Harness {
    let (store, _tmp) = temp_store().await;
    let bus = Arc::new(EventBus::new(store.event_log().clone()));
    let launcher = RunLauncher::new(host, manager.clone(), store.run_meta().clone(), bus);
    Harness {
        launcher,
        store,
        teardown,
        manager,
        _tmp,
    }
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn launch_persists_run_meta_before_the_run_finishes() {
    // Gated host: the run never completes, so any row we observe was written by
    // launch (which `await`s the insert before spawning the run task).
    let gate = Arc::new(Notify::new());
    let host = FakeRunHost::gated(RunOutcome::Done, gate);
    let h = setup(host, 4, true).await;

    let request_id = h.launcher.launch(request("do it", None)).await.unwrap();

    let meta = h.store.run_meta().get(&request_id).await.unwrap();
    let meta = meta.expect("run_meta row exists immediately after the 202");
    assert!(meta.finished_at.is_none(), "run has not finished");
    assert!(
        matches!(
            meta.status,
            BackendRunStatus::Accepted | BackendRunStatus::Running
        ),
        "row is non-terminal before the run completes, got {:?}",
        meta.status
    );
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn launch_resolves_done() {
    let host = FakeRunHost::resolving(RunOutcome::Done);
    let h = setup(host.clone(), 4, true).await;

    let request_id = h.launcher.launch(request("go", None)).await.unwrap();
    let meta = await_run_finished(&h.store, &request_id).await;

    assert_eq!(meta.status, BackendRunStatus::Done);
    assert!(meta.cancel_reason.is_none());
    assert!(host.completed());
    // a fresh ephemeral sandbox was bound and torn down on finish.
    let sandbox = host.seen_sandbox().unwrap();
    assert!(h.manager.view(&sandbox).is_none());
    assert_eq!(h.teardown.destroyed.lock().len(), 1);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn launch_resolves_failed() {
    let host = FakeRunHost::resolving(RunOutcome::Failed);
    let h = setup(host, 4, false).await;

    let request_id = h.launcher.launch(request("go", None)).await.unwrap();
    let meta = await_run_finished(&h.store, &request_id).await;

    assert_eq!(meta.status, BackendRunStatus::Failed);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn cancel_during_acquire_still_tears_down_the_sandbox() {
    // Regression for the cancel-during-provision leak (M1): if cancellation could
    // interrupt the acquire phase, a freshly created sandbox would be live in the
    // host but never recorded by the manager, so `release` would be a no-op and the
    // container would leak. The launcher acquires to completion before racing the
    // cancellation token, so the binding is always recorded and the reaper tears it
    // down even when the cancel arrives mid-provision.
    let host = FakeRunHost::resolving(RunOutcome::Done);
    let (mgr, teardown, gate) = gated_manager(4, true);
    let h = setup_with(host, (mgr, teardown)).await;

    let request_id = h.launcher.launch(request("go", None)).await.unwrap();
    // Wait until the run task is parked inside provisioning, then cancel.
    gate.entered.notified().await;
    assert_eq!(
        h.launcher.cancel(&request_id, "stop"),
        CancelOutcome::Requested
    );
    // Let the (non-cancellable) provision finish; the cancel is observed next.
    gate.release.notify_one();

    let meta = await_run_finished(&h.store, &request_id).await;
    assert_eq!(meta.status, BackendRunStatus::Cancelled);
    assert_eq!(meta.cancel_reason.as_deref(), Some("stop"));
    // The bound sandbox was recorded and torn down exactly once — no leak.
    assert_eq!(h.teardown.destroyed.lock().len(), 1);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn launch_binds_supplied_sandbox_id_and_leaves_it_retained() {
    let host = FakeRunHost::resolving(RunOutcome::Done);
    let h = setup(host.clone(), 4, true).await;

    let request_id = h
        .launcher
        .launch(request("go", Some("ext-box")))
        .await
        .unwrap();
    let meta = await_run_finished(&h.store, &request_id).await;

    assert_eq!(meta.status, BackendRunStatus::Done);
    assert_eq!(host.seen_sandbox().unwrap().as_str(), "ext-box");
    // a caller-supplied sandbox is pinned Retained, never torn down by the run.
    assert!(h.manager.view(&"ext-box".parse().unwrap()).is_some());
    assert!(h.teardown.destroyed.lock().is_empty());
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn cancel_records_cancelled_releases_sandbox_and_interrupts_the_run() {
    // The token never resolves the gate; cancellation drops the run future.
    let gate = Arc::new(Notify::new());
    let host = FakeRunHost::gated(RunOutcome::Done, gate);
    let h = setup(host.clone(), 4, true).await;

    let request_id = h.launcher.launch(request("long task", None)).await.unwrap();
    await_host_started(&host).await; // run is in flight (sandbox acquired).

    assert_eq!(
        h.launcher.cancel(&request_id, "user requested"),
        CancelOutcome::Requested
    );
    let meta = await_run_finished(&h.store, &request_id).await;

    // Cancellation is backend-local: it lands only in run_meta. Agent-core's
    // RequestStatus has no `Cancelled` variant, so it is type-impossible for the
    // backend to write `cancelled` there.
    assert_eq!(meta.status, BackendRunStatus::Cancelled);
    assert_eq!(meta.cancel_reason.as_deref(), Some("user requested"));
    assert!(meta.finished_at.is_some());
    assert!(!host.completed(), "the in-flight run was interrupted");
    // the ephemeral sandbox was released + torn down exactly once.
    let sandbox = host.seen_sandbox().unwrap();
    assert!(h.manager.view(&sandbox).is_none());
    assert_eq!(h.teardown.destroyed.lock().len(), 1);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn cancel_after_completion_is_not_found_and_does_not_double_release() {
    let host = FakeRunHost::resolving(RunOutcome::Done);
    let h = setup(host, 4, true).await;

    let request_id = h.launcher.launch(request("quick", None)).await.unwrap();
    let meta = await_run_finished(&h.store, &request_id).await;
    assert_eq!(meta.status, BackendRunStatus::Done);
    assert_eq!(h.teardown.destroyed.lock().len(), 1);

    // The run already finalized: a late cancel finds nothing and changes nothing.
    assert_eq!(
        h.launcher.cancel(&request_id, "too late"),
        CancelOutcome::NotFound
    );
    assert_eq!(h.teardown.destroyed.lock().len(), 1, "no second release");
    let meta = h.store.run_meta().get(&request_id).await.unwrap().unwrap();
    assert_eq!(meta.status, BackendRunStatus::Done);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn launch_resolves_failed_when_sandbox_acquisition_fails() {
    // The provisioner always fails, so acquisition errors before the host is ever
    // invoked. This is the launcher's provisioning-failure arm — distinct from
    // `launch_resolves_failed`, where the host run itself resolves Failed.
    let host = FakeRunHost::resolving(RunOutcome::Done); // would resolve Done if ever reached
    let h = setup_with(host.clone(), failing_manager(4, true)).await;

    let request_id = h.launcher.launch(request("go", None)).await.unwrap();
    let meta = await_run_finished(&h.store, &request_id).await;

    assert_eq!(meta.status, BackendRunStatus::Failed);
    assert!(meta.finished_at.is_some());
    assert!(
        !host.started(),
        "the host run must never be invoked when sandbox acquisition fails"
    );
    // A failed acquire registers no sandbox ref and tears nothing down.
    assert!(h.manager.list().is_empty(), "failed acquire tracks no sandbox");
    assert!(h.teardown.destroyed.lock().is_empty(), "nothing to tear down");
}
