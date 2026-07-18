use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use crate::model::WorkspaceSessionId;

use super::holder::{
    wait_reap_owned, HolderExitReason, HolderIdentity, HolderProcess, HolderProcessExit,
    HolderSignal, HolderSupervisor, HolderSupervisorError,
};

#[derive(Default)]
struct FakeProcessState {
    exited: AtomicBool,
    normal_exit: AtomicBool,
    ignore_signals: AtomicBool,
    identity_matches: AtomicBool,
    wait_errors_remaining: AtomicUsize,
    blocking_wait_errors_remaining: AtomicUsize,
    reaps: AtomicUsize,
    signals: AtomicUsize,
    delayed_exit_after_signal: Mutex<Option<Duration>>,
    signaled_at: Mutex<Option<Instant>>,
    dropped_unreaped: AtomicUsize,
}

struct FakeProcess {
    state: Arc<FakeProcessState>,
}

impl HolderProcess for FakeProcess {
    fn try_wait(&mut self) -> Result<Option<HolderProcessExit>, String> {
        if self
            .state
            .wait_errors_remaining
            .fetch_update(Ordering::SeqCst, Ordering::SeqCst, |remaining| {
                remaining.checked_sub(1)
            })
            .is_ok()
        {
            return Err("injected wait failure".to_owned());
        }
        let delayed_exit = *self
            .state
            .delayed_exit_after_signal
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        let signaled_at = *self
            .state
            .signaled_at
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        if delayed_exit
            .zip(signaled_at)
            .is_some_and(|(delay, signaled_at)| signaled_at.elapsed() >= delay)
        {
            self.state.exited.store(true, Ordering::SeqCst);
        }
        if !self.state.exited.load(Ordering::SeqCst) {
            return Ok(None);
        }
        assert_eq!(self.state.reaps.fetch_add(1, Ordering::SeqCst), 0);
        if self.state.normal_exit.load(Ordering::SeqCst) {
            Ok(Some(HolderProcessExit {
                exit_status: Some(0),
                signal: None,
                status_raw: Some(0),
            }))
        } else {
            Ok(Some(HolderProcessExit {
                exit_status: None,
                signal: Some(9),
                status_raw: Some(9),
            }))
        }
    }

    fn wait_reap(&mut self) -> Result<HolderProcessExit, String> {
        if self
            .state
            .blocking_wait_errors_remaining
            .fetch_update(Ordering::SeqCst, Ordering::SeqCst, |remaining| {
                remaining.checked_sub(1)
            })
            .is_ok()
        {
            return Err("injected blocking wait failure".to_owned());
        }
        loop {
            let delayed_exit = *self
                .state
                .delayed_exit_after_signal
                .lock()
                .unwrap_or_else(|poisoned| poisoned.into_inner());
            let signaled_at = *self
                .state
                .signaled_at
                .lock()
                .unwrap_or_else(|poisoned| poisoned.into_inner());
            if delayed_exit
                .zip(signaled_at)
                .is_some_and(|(delay, signaled_at)| signaled_at.elapsed() >= delay)
            {
                self.state.exited.store(true, Ordering::SeqCst);
            }
            if self.state.exited.load(Ordering::SeqCst) {
                assert_eq!(self.state.reaps.fetch_add(1, Ordering::SeqCst), 0);
                return if self.state.normal_exit.load(Ordering::SeqCst) {
                    Ok(HolderProcessExit {
                        exit_status: Some(0),
                        signal: None,
                        status_raw: Some(0),
                    })
                } else {
                    Ok(HolderProcessExit {
                        exit_status: None,
                        signal: Some(9),
                        status_raw: Some(9),
                    })
                };
            }
            std::thread::sleep(Duration::from_millis(1));
        }
    }

    fn identity_matches(&self, _expected: &HolderIdentity) -> Result<bool, String> {
        Ok(self.state.identity_matches.load(Ordering::SeqCst))
    }

    fn send_signal(&mut self, _signal: HolderSignal) -> Result<(), String> {
        self.state.signals.fetch_add(1, Ordering::SeqCst);
        if !self.state.ignore_signals.load(Ordering::SeqCst) {
            if self
                .state
                .delayed_exit_after_signal
                .lock()
                .unwrap_or_else(|poisoned| poisoned.into_inner())
                .is_some()
            {
                *self
                    .state
                    .signaled_at
                    .lock()
                    .unwrap_or_else(|poisoned| poisoned.into_inner()) = Some(Instant::now());
            } else {
                self.state.exited.store(true, Ordering::SeqCst);
            }
        }
        Ok(())
    }
}

#[test]
fn permanent_blocking_wait_error_is_bounded_and_reported() {
    let process = Arc::new(FakeProcessState::default());
    process
        .blocking_wait_errors_remaining
        .store(usize::MAX, Ordering::SeqCst);
    let mut fake = FakeProcess {
        state: Arc::clone(&process),
    };

    let started = Instant::now();
    let error = wait_reap_owned(&mut fake).expect_err("permanent wait error is bounded");

    assert!(started.elapsed() < Duration::from_secs(1));
    assert!(error.contains("injected blocking wait failure"), "{error}");
}

#[test]
fn supervisor_drop_finalizes_registration_after_bounded_blocking_wait_failure() {
    let supervisor = HolderSupervisor::new(Duration::from_millis(5), 8);
    let process = Arc::new(FakeProcessState::default());
    process
        .wait_errors_remaining
        .store(usize::MAX, Ordering::SeqCst);
    process
        .blocking_wait_errors_remaining
        .store(usize::MAX, Ordering::SeqCst);
    let registration = registered(&supervisor, "workspace-drop-blocking-wait-error", &process);

    let started = Instant::now();
    drop(supervisor);

    assert!(started.elapsed() < Duration::from_secs(1));
    assert_eq!(
        registration
            .exit_event()
            .expect("bounded wait failure remains visible to finalization")
            .reason,
        HolderExitReason::WaitError
    );
}

impl Drop for FakeProcess {
    fn drop(&mut self) {
        if self.state.reaps.load(Ordering::SeqCst) == 0 {
            self.state.dropped_unreaped.fetch_add(1, Ordering::SeqCst);
        }
    }
}

fn identity(pid: i32, generation: u64) -> HolderIdentity {
    HolderIdentity {
        pid,
        parent_pid: 1,
        start_time_ticks: 1234,
        executable: "/proc/self/exe".into(),
        generation,
        pidfd_available: true,
    }
}

fn registered(
    supervisor: &HolderSupervisor,
    workspace: &str,
    process: &Arc<FakeProcessState>,
) -> super::holder::HolderRegistration {
    process.identity_matches.store(true, Ordering::SeqCst);
    supervisor
        .register_process(
            WorkspaceSessionId(workspace.to_owned()),
            identity(41, 7),
            Box::new(FakeProcess {
                state: Arc::clone(process),
            }),
        )
        .expect("fake holder registers")
}

#[test]
fn unexpected_exit_is_detected_under_one_second_and_reaped_once() {
    let supervisor = HolderSupervisor::new(Duration::from_millis(5), 8);
    let process = Arc::new(FakeProcessState::default());
    let registration = registered(&supervisor, "workspace-a", &process);

    process.exited.store(true, Ordering::SeqCst);
    let event = registration
        .wait_for_exit(Duration::from_secs(1))
        .expect("exit is detected before the hard deadline");

    assert_eq!(event.reason, HolderExitReason::Unexpected);
    assert_eq!(event.workspace_session_id.0, "workspace-a");
    assert_eq!(process.reaps.load(Ordering::SeqCst), 1);

    let joined = supervisor
        .terminate(&registration, Duration::from_millis(5))
        .expect("destroy joins the already-reaped holder");
    assert_eq!(joined.exit_status, event.exit.exit_status);
    assert_eq!(process.reaps.load(Ordering::SeqCst), 1);
    assert_eq!(supervisor.events_after(0).len(), 1);
}

#[test]
fn normal_exit_is_detected_under_one_second_and_reaped_once() {
    let supervisor = HolderSupervisor::new(Duration::from_millis(5), 8);
    let process = Arc::new(FakeProcessState::default());
    process.normal_exit.store(true, Ordering::SeqCst);
    let registration = registered(&supervisor, "workspace-normal-exit", &process);

    process.exited.store(true, Ordering::SeqCst);
    let event = registration
        .wait_for_exit(Duration::from_secs(1))
        .expect("normal exit is detected before the hard deadline");

    assert_eq!(event.reason, HolderExitReason::Unexpected);
    assert_eq!(event.exit.exit_status, Some(0));
    assert_eq!(event.exit.signal, None);
    assert_eq!(process.reaps.load(Ordering::SeqCst), 1);
    assert_eq!(supervisor.stats().holder_exit_total, 1);
}

#[test]
fn repeated_join_after_exit_does_not_publish_duplicate_notification() {
    let supervisor = HolderSupervisor::new(Duration::from_millis(5), 8);
    let process = Arc::new(FakeProcessState::default());
    process.normal_exit.store(true, Ordering::SeqCst);
    let registration = registered(&supervisor, "workspace-duplicate-exit", &process);
    process.exited.store(true, Ordering::SeqCst);

    registration
        .wait_for_exit(Duration::from_secs(1))
        .expect("normal exit is observed");
    supervisor
        .terminate(&registration, Duration::ZERO)
        .expect("first join returns the recorded exit");
    supervisor
        .terminate(&registration, Duration::ZERO)
        .expect("repeated join is idempotent");

    assert_eq!(process.reaps.load(Ordering::SeqCst), 1);
    assert_eq!(supervisor.events_after(0).len(), 1);
    assert_eq!(supervisor.stats().holder_exit_total, 1);
}

#[test]
fn transient_wait_error_is_retried_by_the_same_owner_and_reaped_once() {
    let supervisor = HolderSupervisor::new(Duration::from_millis(5), 8);
    let process = Arc::new(FakeProcessState::default());
    process.wait_errors_remaining.store(1, Ordering::SeqCst);
    process.exited.store(true, Ordering::SeqCst);
    let registration = registered(&supervisor, "workspace-wait-retry", &process);

    let event = registration
        .wait_for_exit(Duration::from_secs(1))
        .expect("transient wait failure remains inside the one-second bound");

    assert_eq!(event.reason, HolderExitReason::Unexpected);
    assert_eq!(process.wait_errors_remaining.load(Ordering::SeqCst), 0);
    assert_eq!(process.reaps.load(Ordering::SeqCst), 1);
    assert_eq!(supervisor.stats().holder_exit_total, 1);
}

#[test]
fn persistent_wait_error_fails_closed_and_teardown_remains_bounded() {
    let supervisor = HolderSupervisor::new(Duration::from_millis(5), 8);
    let process = Arc::new(FakeProcessState::default());
    process
        .wait_errors_remaining
        .store(usize::MAX, Ordering::SeqCst);
    process.ignore_signals.store(true, Ordering::SeqCst);
    let registration = registered(&supervisor, "workspace-wait-failure", &process);

    let event = registration
        .wait_for_exit(Duration::from_secs(1))
        .expect("persistent wait failure closes the liveness gate within one second");
    assert_eq!(event.reason, HolderExitReason::WaitError);
    assert!(!registration.is_live());
    assert_eq!(process.reaps.load(Ordering::SeqCst), 0);
    assert_eq!(supervisor.stats().wait_error_total, 1);
    assert_eq!(supervisor.stats().holder_exit_total, 1);

    let started = std::time::Instant::now();
    let error = supervisor
        .terminate(&registration, Duration::ZERO)
        .expect_err("unreapable holder returns a bounded teardown failure");
    assert!(matches!(error, HolderSupervisorError::Process { .. }));
    assert!(started.elapsed() < Duration::from_secs(2));
    assert_eq!(process.signals.load(Ordering::SeqCst), 1);

    // The sole owner retains the child after the bounded failure. Once wait
    // becomes usable it completes exactly one reap without a second event.
    process.wait_errors_remaining.store(0, Ordering::SeqCst);
    process.exited.store(true, Ordering::SeqCst);
    let deadline = std::time::Instant::now() + Duration::from_secs(1);
    while process.reaps.load(Ordering::SeqCst) == 0 && std::time::Instant::now() < deadline {
        std::thread::yield_now();
    }
    assert_eq!(process.reaps.load(Ordering::SeqCst), 1);
    assert_eq!(supervisor.events_after(0).len(), 1);
    assert_eq!(supervisor.stats().holder_exit_total, 1);
}

#[test]
fn dropping_supervisor_requests_bounded_child_teardown_and_reap() {
    let supervisor = HolderSupervisor::new(Duration::from_millis(5), 8);
    let process = Arc::new(FakeProcessState::default());
    let registration = registered(&supervisor, "workspace-runtime-drop", &process);

    drop(supervisor);
    let event = registration
        .wait_for_exit(Duration::from_secs(1))
        .expect("supervisor shutdown reaps its remaining child");

    assert_eq!(event.reason, HolderExitReason::Destroy);
    assert_eq!(process.signals.load(Ordering::SeqCst), 1);
    assert_eq!(process.reaps.load(Ordering::SeqCst), 1);
}

#[test]
fn drop_reaps_every_delayed_child_without_a_shared_shutdown_deadline() {
    let supervisor = HolderSupervisor::new(Duration::from_millis(5), 8);
    let first = Arc::new(FakeProcessState::default());
    *first
        .delayed_exit_after_signal
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner()) = Some(Duration::from_millis(600));
    let first_registration = registered(&supervisor, "workspace-drop-first", &first);
    let second = Arc::new(FakeProcessState::default());
    *second
        .delayed_exit_after_signal
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner()) = Some(Duration::from_millis(1_100));
    let second_registration = registered(&supervisor, "workspace-drop-second", &second);

    let started = Instant::now();
    drop(supervisor);

    assert!(started.elapsed() >= Duration::from_millis(1_100));
    assert!(started.elapsed() < Duration::from_secs(3));
    assert_eq!(first.reaps.load(Ordering::SeqCst), 1);
    assert_eq!(second.reaps.load(Ordering::SeqCst), 1);
    assert_eq!(first.dropped_unreaped.load(Ordering::SeqCst), 0);
    assert_eq!(second.dropped_unreaped.load(Ordering::SeqCst), 0);
    assert_eq!(
        first_registration
            .exit_event()
            .expect("first delayed holder is finalized")
            .reason,
        HolderExitReason::Destroy
    );
    assert_eq!(
        second_registration
            .exit_event()
            .expect("second delayed holder is finalized")
            .reason,
        HolderExitReason::Destroy
    );
}

#[test]
fn duplicate_registration_abort_retries_a_transient_wait_error_until_reaped() {
    let supervisor = HolderSupervisor::new(Duration::from_millis(5), 8);
    let original = Arc::new(FakeProcessState::default());
    let original_registration = registered(&supervisor, "workspace-duplicate", &original);
    let rejected = Arc::new(FakeProcessState::default());
    rejected.identity_matches.store(true, Ordering::SeqCst);
    rejected.wait_errors_remaining.store(2, Ordering::SeqCst);

    let error = supervisor
        .register_process(
            WorkspaceSessionId("workspace-duplicate".to_owned()),
            identity(41, 7),
            Box::new(FakeProcess {
                state: Arc::clone(&rejected),
            }),
        )
        .expect_err("duplicate registration is rejected after its child is reaped");

    assert!(matches!(error, HolderSupervisorError::Process { .. }));
    assert_eq!(rejected.signals.load(Ordering::SeqCst), 1);
    assert_eq!(rejected.reaps.load(Ordering::SeqCst), 1);
    assert_eq!(rejected.dropped_unreaped.load(Ordering::SeqCst), 0);

    original.exited.store(true, Ordering::SeqCst);
    original_registration
        .wait_for_exit(Duration::from_secs(1))
        .expect("accepted peer remains independently supervised");
}

#[test]
fn drop_never_abandons_an_owned_child_when_nonblocking_wait_is_unusable() {
    let supervisor = HolderSupervisor::new(Duration::from_millis(5), 8);
    let process = Arc::new(FakeProcessState::default());
    process
        .wait_errors_remaining
        .store(usize::MAX, Ordering::SeqCst);
    let registration = registered(&supervisor, "workspace-drop-wait-error", &process);

    drop(supervisor);

    assert_eq!(process.reaps.load(Ordering::SeqCst), 1);
    assert_eq!(process.dropped_unreaped.load(Ordering::SeqCst), 0);
    assert_eq!(
        registration
            .exit_event()
            .expect("drop finalizes the owned holder record")
            .reason,
        HolderExitReason::Destroy
    );
}

#[test]
fn concurrent_destroy_and_exit_join_one_reap_result() {
    let supervisor = Arc::new(HolderSupervisor::new(Duration::from_millis(5), 8));
    let process = Arc::new(FakeProcessState::default());
    let registration = registered(&supervisor, "workspace-race", &process);
    process.exited.store(true, Ordering::SeqCst);

    let left_supervisor = Arc::clone(&supervisor);
    let left_registration = registration.clone();
    let left = std::thread::spawn(move || {
        left_supervisor.terminate(&left_registration, Duration::from_millis(5))
    });
    let right_supervisor = Arc::clone(&supervisor);
    let right_registration = registration.clone();
    let right = std::thread::spawn(move || {
        right_supervisor.terminate(&right_registration, Duration::from_millis(5))
    });

    assert_eq!(
        left.join().expect("left joins"),
        right.join().expect("right joins")
    );
    assert_eq!(process.reaps.load(Ordering::SeqCst), 1);
    assert_eq!(supervisor.stats().holder_exit_total, 1);
}

#[test]
fn long_destroy_grace_does_not_delay_peer_exit_detection() {
    let supervisor = Arc::new(HolderSupervisor::new(Duration::from_millis(5), 8));
    let stubborn = Arc::new(FakeProcessState::default());
    stubborn.ignore_signals.store(true, Ordering::SeqCst);
    let stubborn_registration = registered(&supervisor, "workspace-stubborn", &stubborn);
    let peer = Arc::new(FakeProcessState::default());
    let peer_registration = registered(&supervisor, "workspace-peer", &peer);

    let destroy_supervisor = Arc::clone(&supervisor);
    let destroy_registration = stubborn_registration.clone();
    let destroy = std::thread::spawn(move || {
        destroy_supervisor.terminate(&destroy_registration, Duration::from_secs(5))
    });
    let signal_deadline = std::time::Instant::now() + Duration::from_secs(1);
    while stubborn.signals.load(Ordering::SeqCst) == 0
        && std::time::Instant::now() < signal_deadline
    {
        std::thread::yield_now();
    }
    assert_eq!(stubborn.signals.load(Ordering::SeqCst), 1);

    peer.exited.store(true, Ordering::SeqCst);
    let peer_exit = peer_registration
        .wait_for_exit(Duration::from_secs(1))
        .expect("peer exit detection remains bounded during another holder's grace period");
    assert_eq!(peer_exit.reason, HolderExitReason::Unexpected);

    stubborn.exited.store(true, Ordering::SeqCst);
    destroy
        .join()
        .expect("destroy thread joins")
        .expect("destroy observes the eventual exit");
    assert_eq!(stubborn.reaps.load(Ordering::SeqCst), 1);
    assert_eq!(peer.reaps.load(Ordering::SeqCst), 1);
}

#[test]
fn pid_identity_mismatch_refuses_to_signal() {
    let supervisor = HolderSupervisor::new(Duration::from_millis(5), 8);
    let process = Arc::new(FakeProcessState::default());
    let registration = registered(&supervisor, "workspace-reused", &process);
    process.identity_matches.store(false, Ordering::SeqCst);

    let error = supervisor
        .terminate(&registration, Duration::from_millis(5))
        .expect_err("mismatched identity is rejected");

    assert!(matches!(
        error,
        HolderSupervisorError::IdentityMismatch { .. }
    ));
    assert_eq!(process.signals.load(Ordering::SeqCst), 0);
    assert_eq!(process.reaps.load(Ordering::SeqCst), 0);
    assert_eq!(supervisor.stats().identity_mismatch_total, 1);

    // Refusing an unsafe signal does not waive ownership. Let the fake exit
    // naturally so supervisor drop can join the one reap owner.
    process.exited.store(true, Ordering::SeqCst);
    registration
        .wait_for_exit(Duration::from_secs(1))
        .expect("identity-mismatched child remains owned until natural exit");
    assert_eq!(process.reaps.load(Ordering::SeqCst), 1);
}

#[test]
fn holder_event_history_is_bounded_and_counters_are_monotonic() {
    let supervisor = HolderSupervisor::new(Duration::from_millis(2), 2);
    for index in 0..3 {
        let process = Arc::new(FakeProcessState::default());
        let registration = registered(&supervisor, &format!("workspace-{index}"), &process);
        process.exited.store(true, Ordering::SeqCst);
        registration
            .wait_for_exit(Duration::from_secs(1))
            .expect("fake exits");
    }

    let events = supervisor.events_after(0);
    assert_eq!(events.len(), 2);
    assert_eq!(supervisor.stats().holder_exit_total, 3);
    assert_eq!(supervisor.stats().dropped_event_total, 1);
}
