use std::collections::{HashMap, VecDeque};
use std::fmt;
use std::path::PathBuf;
#[cfg(target_os = "linux")]
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::mpsc::{self, Receiver, RecvTimeoutError, SyncSender, TrySendError};
use std::sync::{Arc, Condvar, Mutex};
use std::thread::{self, JoinHandle};
use std::time::{Duration, Instant};

#[cfg(target_os = "linux")]
use std::io::Read;
#[cfg(target_os = "linux")]
use std::os::fd::{AsRawFd, IntoRawFd, OwnedFd};
#[cfg(all(target_os = "linux", unix))]
use std::os::unix::process::ExitStatusExt;
#[cfg(target_os = "linux")]
use std::process::{Child, ChildStderr, Command, ExitStatus, Stdio};

#[cfg(target_os = "linux")]
use nix::fcntl::OFlag;
#[cfg(target_os = "linux")]
use nix::unistd::pipe2;
#[cfg(target_os = "linux")]
use rustix::process::{pidfd_open, pidfd_send_signal, Pid, PidfdFlags, Signal};

use crate::model::WorkspaceSessionId;
use crate::service::{holder_exit_channel, HolderExitNotifier, HolderExitSubscription};
use crate::session::{MountedWorkspace, WorkspaceManagerError};

#[cfg(target_os = "linux")]
use super::fds::{clear_cloexec, expect_line, set_nonblocking};
#[cfg(target_os = "linux")]
use super::setup_error;
use super::{HolderKillReport, NamespacePlan, NamespaceRuntime};

const SUPERVISOR_QUEUE_CAPACITY: usize = 64;
const KILL_REAP_TIMEOUT: Duration = Duration::from_secs(1);
const WAIT_ERROR_LIMIT: u8 = 3;

#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub(crate) struct HolderIdentity {
    pub(crate) pid: i32,
    pub(crate) parent_pid: i32,
    pub(crate) start_time_ticks: u64,
    pub(crate) executable: PathBuf,
    pub(crate) generation: u64,
    pub(crate) pidfd_available: bool,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) struct HolderProcessExit {
    pub(crate) exit_status: Option<i32>,
    pub(crate) signal: Option<i32>,
    pub(crate) status_raw: Option<i32>,
}

impl HolderProcessExit {
    const fn unknown() -> Self {
        Self {
            exit_status: None,
            signal: None,
            status_raw: None,
        }
    }

    fn report(self, holder_was_alive: bool) -> HolderKillReport {
        HolderKillReport {
            holder_was_alive,
            exit_status: self.exit_status,
            signal: self.signal,
            status_raw: self.status_raw,
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum HolderSignal {
    Terminate,
    Kill,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum HolderExitReason {
    Unexpected,
    WaitError,
    Destroy,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct HolderExitEvent {
    pub(crate) sequence: u64,
    pub(crate) workspace_session_id: WorkspaceSessionId,
    pub(crate) identity: HolderIdentity,
    pub(crate) reason: HolderExitReason,
    pub(crate) exit: HolderProcessExit,
}

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub(crate) struct HolderSupervisorStats {
    pub(crate) holder_exit_total: u64,
    pub(crate) wait_error_total: u64,
    pub(crate) identity_mismatch_total: u64,
    pub(crate) dropped_event_total: u64,
}

#[derive(Debug, Clone, PartialEq, Eq, thiserror::Error)]
pub(crate) enum HolderSupervisorError {
    #[error("holder supervisor command queue is full")]
    Overloaded,
    #[error("holder supervisor is unavailable")]
    Unavailable,
    #[error("holder {workspace_session_id:?} is not registered for this generation")]
    Unknown {
        workspace_session_id: WorkspaceSessionId,
    },
    #[error(
        "holder pid identity changed for {workspace_session_id:?}: expected pid {expected_pid} generation {generation}"
    )]
    IdentityMismatch {
        workspace_session_id: WorkspaceSessionId,
        expected_pid: i32,
        generation: u64,
    },
    #[error("holder operation failed for {workspace_session_id:?}: {message}")]
    Process {
        workspace_session_id: WorkspaceSessionId,
        message: String,
    },
}

pub(crate) trait HolderProcess: Send {
    /// Nonblocking observation by the sole child owner. Implementations must
    /// not reap through any path outside this trait.
    fn try_wait(&mut self) -> Result<Option<HolderProcessExit>, String>;
    /// Final blocking reap used only after ownership can no longer be handed
    /// back to a live supervisor. The owner retries a bounded number of
    /// transient failures; implementations should turn an already-reaped
    /// kernel child (`ECHILD`) into a successful exit with unknown status.
    fn wait_reap(&mut self) -> Result<HolderProcessExit, String>;
    /// Revalidates fallback PID identity immediately before a raw-PID signal.
    fn identity_matches(&self, expected: &HolderIdentity) -> Result<bool, String>;
    fn send_signal(&mut self, signal: HolderSignal) -> Result<(), String>;
}

#[derive(Clone)]
pub struct HolderRegistration {
    workspace_session_id: WorkspaceSessionId,
    identity: HolderIdentity,
    exit: Arc<(Mutex<Option<HolderExitEvent>>, Condvar)>,
}

impl fmt::Debug for HolderRegistration {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("HolderRegistration")
            .field("workspace_session_id", &self.workspace_session_id)
            .field("identity", &self.identity)
            .field("live", &self.is_live())
            .finish()
    }
}

impl PartialEq for HolderRegistration {
    fn eq(&self, other: &Self) -> bool {
        self.workspace_session_id == other.workspace_session_id
            && self.identity == other.identity
            && Arc::ptr_eq(&self.exit, &other.exit)
    }
}

impl Eq for HolderRegistration {}

impl HolderRegistration {
    #[doc(hidden)]
    pub fn detached_for_test(workspace_session_id: WorkspaceSessionId, pid: i32) -> Self {
        Self {
            workspace_session_id,
            identity: HolderIdentity {
                pid,
                parent_pid: 0,
                start_time_ticks: 0,
                executable: PathBuf::new(),
                generation: 0,
                pidfd_available: false,
            },
            exit: Arc::new((Mutex::new(None), Condvar::new())),
        }
    }

    pub(crate) fn detached_live(workspace_session_id: WorkspaceSessionId, pid: i32) -> Self {
        Self::detached_for_test(workspace_session_id, pid)
    }

    pub(crate) fn is_live(&self) -> bool {
        self.exit
            .0
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner())
            .is_none()
    }

    pub(crate) fn exit_event(&self) -> Option<HolderExitEvent> {
        self.exit
            .0
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner())
            .clone()
    }

    #[cfg(test)]
    pub(crate) fn wait_for_exit(&self, timeout: Duration) -> Option<HolderExitEvent> {
        let guard = self
            .exit
            .0
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        let (guard, _) = self
            .exit
            .1
            .wait_timeout_while(guard, timeout, |event| event.is_none())
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        guard.clone()
    }

    pub(crate) fn mark_exited_for_test(&self, detail: &str) {
        let signal = detail
            .strip_prefix("signal:")
            .and_then(|value| value.parse::<i32>().ok());
        let event = HolderExitEvent {
            sequence: 0,
            workspace_session_id: self.workspace_session_id.clone(),
            identity: self.identity.clone(),
            reason: HolderExitReason::Unexpected,
            exit: HolderProcessExit {
                exit_status: None,
                signal,
                status_raw: signal,
            },
        };
        publish_registration_exit(self, event);
    }
}

pub(crate) struct HolderSupervisor {
    tx: Option<SyncSender<SupervisorCommand>>,
    worker: Mutex<Option<JoinHandle<()>>>,
    log: Arc<Mutex<SupervisorLog>>,
    #[cfg(target_os = "linux")]
    next_generation: Arc<AtomicU64>,
}

impl fmt::Debug for HolderSupervisor {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("HolderSupervisor")
            .field("stats", &self.stats())
            .finish_non_exhaustive()
    }
}

impl HolderSupervisor {
    pub(crate) fn new(poll_interval: Duration, event_capacity: usize) -> Self {
        let (tx, rx) = mpsc::sync_channel(SUPERVISOR_QUEUE_CAPACITY);
        let log = Arc::new(Mutex::new(SupervisorLog::new(event_capacity)));
        let worker_log = Arc::clone(&log);
        let worker = thread::Builder::new()
            .name("eos-holder-supervisor".to_owned())
            .spawn(move || supervisor_loop(rx, worker_log, poll_interval))
            .expect("holder supervisor thread must start");
        Self {
            tx: Some(tx),
            worker: Mutex::new(Some(worker)),
            log,
            #[cfg(target_os = "linux")]
            next_generation: Arc::new(AtomicU64::new(1)),
        }
    }

    #[cfg(target_os = "linux")]
    pub(crate) fn next_generation(&self) -> u64 {
        self.next_generation.fetch_add(1, Ordering::Relaxed)
    }

    #[cfg_attr(not(any(target_os = "linux", test)), allow(dead_code))]
    pub(crate) fn register_process(
        &self,
        workspace_session_id: WorkspaceSessionId,
        identity: HolderIdentity,
        process: Box<dyn HolderProcess>,
    ) -> Result<HolderRegistration, HolderSupervisorError> {
        let registration = HolderRegistration {
            workspace_session_id: workspace_session_id.clone(),
            identity: identity.clone(),
            exit: Arc::new((Mutex::new(None), Condvar::new())),
        };
        let (reply_tx, reply_rx) = mpsc::sync_channel(1);
        let command = SupervisorCommand::Register {
            registration: registration.clone(),
            process,
            reply: reply_tx,
        };
        match self
            .tx
            .as_ref()
            .expect("holder supervisor sender exists until drop")
            .try_send(command)
        {
            Ok(()) => {}
            Err(TrySendError::Full(command)) => {
                abort_rejected_registration(command, &identity);
                return Err(HolderSupervisorError::Overloaded);
            }
            Err(TrySendError::Disconnected(command)) => {
                abort_rejected_registration(command, &identity);
                return Err(HolderSupervisorError::Unavailable);
            }
        }
        reply_rx
            .recv()
            .map_err(|_| HolderSupervisorError::Unavailable)??;
        Ok(registration)
    }

    pub(crate) fn terminate(
        &self,
        registration: &HolderRegistration,
        grace: Duration,
    ) -> Result<HolderKillReport, HolderSupervisorError> {
        let (reply_tx, reply_rx) = mpsc::sync_channel(1);
        self.try_command(SupervisorCommand::Terminate {
            registration: registration.clone(),
            grace,
            reply: reply_tx,
        })?;
        reply_rx
            .recv()
            .map_err(|_| HolderSupervisorError::Unavailable)?
    }

    #[cfg(test)]
    pub(crate) fn events_after(&self, sequence: u64) -> Vec<HolderExitEvent> {
        self.log
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner())
            .events
            .iter()
            .filter(|event| event.sequence > sequence)
            .cloned()
            .collect()
    }

    pub(crate) fn stats(&self) -> HolderSupervisorStats {
        self.log
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner())
            .stats
    }

    pub(crate) fn take_exit_subscription(&self) -> Result<HolderExitSubscription, String> {
        self.log
            .lock()
            .map_err(|_| "holder supervisor event log lock poisoned".to_owned())?
            .take_subscription()
    }

    fn try_command(&self, command: SupervisorCommand) -> Result<(), HolderSupervisorError> {
        match self
            .tx
            .as_ref()
            .expect("holder supervisor sender exists until drop")
            .try_send(command)
        {
            Ok(()) => Ok(()),
            Err(TrySendError::Full(_)) => Err(HolderSupervisorError::Overloaded),
            Err(TrySendError::Disconnected(_)) => Err(HolderSupervisorError::Unavailable),
        }
    }
}

impl Drop for HolderSupervisor {
    fn drop(&mut self) {
        // Disconnect is the worker's explicit shutdown request. The worker
        // drains/reaps its owned children before returning, and drop joins so
        // neither holder children nor the reaper thread outlive the runtime.
        self.tx.take();
        let worker = self
            .worker
            .get_mut()
            .unwrap_or_else(|poisoned| poisoned.into_inner())
            .take();
        if let Some(worker) = worker {
            let _ = worker.join();
        }
    }
}

enum SupervisorCommand {
    #[cfg_attr(not(any(target_os = "linux", test)), allow(dead_code))]
    Register {
        registration: HolderRegistration,
        process: Box<dyn HolderProcess>,
        reply: SyncSender<Result<(), HolderSupervisorError>>,
    },
    Terminate {
        registration: HolderRegistration,
        grace: Duration,
        reply: SyncSender<Result<HolderKillReport, HolderSupervisorError>>,
    },
}

struct HolderRecord {
    registration: HolderRegistration,
    process: Box<dyn HolderProcess>,
    termination: Option<TerminationAttempt>,
    destroy_requested: bool,
    consecutive_wait_errors: u8,
    wait_error_terminal: bool,
    wait_error_kill_attempted: bool,
}

struct TerminationAttempt {
    phase: TerminationPhase,
    waiters: Vec<SyncSender<Result<HolderKillReport, HolderSupervisorError>>>,
}

#[derive(Debug, Clone, Copy)]
enum TerminationPhase {
    Grace { deadline: Instant },
    Kill { deadline: Instant },
}

#[derive(Debug, Clone, PartialEq, Eq, Hash)]
struct RegistrationKey {
    workspace_session_id: WorkspaceSessionId,
    identity: HolderIdentity,
}

impl From<&HolderRegistration> for RegistrationKey {
    fn from(registration: &HolderRegistration) -> Self {
        Self {
            workspace_session_id: registration.workspace_session_id.clone(),
            identity: registration.identity.clone(),
        }
    }
}

struct SupervisorLog {
    events: VecDeque<HolderExitEvent>,
    capacity: usize,
    next_sequence: u64,
    stats: HolderSupervisorStats,
    exit_notifier: Option<HolderExitNotifier>,
}

impl SupervisorLog {
    fn new(capacity: usize) -> Self {
        Self {
            events: VecDeque::with_capacity(capacity),
            capacity,
            next_sequence: 1,
            stats: HolderSupervisorStats::default(),
            exit_notifier: None,
        }
    }

    fn take_subscription(&mut self) -> Result<HolderExitSubscription, String> {
        if self.exit_notifier.is_some() {
            return Err("holder exit subscription already taken".to_owned());
        }
        let (notifier, subscription) = holder_exit_channel();
        // Reconcile a holder that exited before dispatcher startup; this wake
        // safely coalesces with a concurrent exit publication.
        notifier.notify();
        self.exit_notifier = Some(notifier);
        Ok(subscription)
    }

    fn publish(
        &mut self,
        registration: &HolderRegistration,
        reason: HolderExitReason,
        exit: HolderProcessExit,
    ) -> HolderExitEvent {
        let event = HolderExitEvent {
            sequence: self.next_sequence,
            workspace_session_id: registration.workspace_session_id.clone(),
            identity: registration.identity.clone(),
            reason,
            exit,
        };
        self.next_sequence = self.next_sequence.saturating_add(1);
        self.stats.holder_exit_total = self.stats.holder_exit_total.saturating_add(1);
        if self.capacity == 0 {
            self.stats.dropped_event_total = self.stats.dropped_event_total.saturating_add(1);
        } else {
            if self.events.len() == self.capacity {
                self.events.pop_front();
                self.stats.dropped_event_total = self.stats.dropped_event_total.saturating_add(1);
            }
            self.events.push_back(event.clone());
        }
        if let Some(notifier) = &self.exit_notifier {
            notifier.notify();
        }
        event
    }
}

fn supervisor_loop(
    rx: Receiver<SupervisorCommand>,
    log: Arc<Mutex<SupervisorLog>>,
    poll_interval: Duration,
) {
    let poll_interval = poll_interval.max(Duration::from_millis(1));
    let mut records: HashMap<RegistrationKey, HolderRecord> = HashMap::new();
    loop {
        if records.is_empty() {
            match rx.recv() {
                Ok(command) => handle_supervisor_command(command, &mut records, &log),
                Err(_) => break,
            }
        } else {
            match rx.recv_timeout(poll_interval) {
                Ok(command) => handle_supervisor_command(command, &mut records, &log),
                Err(RecvTimeoutError::Timeout) => {}
                Err(RecvTimeoutError::Disconnected) => {
                    shutdown_holder_records(&mut records, &log);
                    break;
                }
            }
        }
        poll_holder_records(&mut records, &log);
    }
}

/// The worker owns every accepted child until it has been reaped. If the
/// runtime itself is dropped while workspaces remain registered, disconnect
/// is therefore a teardown request rather than permission to drop `Child`
/// handles and orphan their processes.
fn shutdown_holder_records(
    records: &mut HashMap<RegistrationKey, HolderRecord>,
    log: &Arc<Mutex<SupervisorLog>>,
) {
    let keys = records.keys().cloned().collect::<Vec<_>>();
    for key in &keys {
        let completion = {
            let Some(record) = records.get_mut(key) else {
                continue;
            };
            match record.process.try_wait() {
                Ok(Some(exit)) => Some(finish_record(
                    record,
                    HolderExitReason::Destroy,
                    exit,
                    false,
                    log,
                )),
                Ok(None) => {
                    record.consecutive_wait_errors = 0;
                    None
                }
                Err(message) => {
                    observe_wait_error(record, &message, log);
                    None
                }
            }
        };
        if let Some(report) = completion {
            let waiters = records
                .remove(key)
                .and_then(|mut record| record.termination.take())
                .map(|attempt| attempt.waiters)
                .unwrap_or_default();
            for waiter in waiters {
                let _ = waiter.send(Ok(report.clone()));
            }
        }
    }

    // Signal every remaining child before blocking on any one of them. A
    // pidfd is intrinsically stable; raw PID fallback still passes through
    // the immediate identity check. Identity or signal failure never grants
    // permission to abandon the owned child: it is waited naturally instead.
    for key in &keys {
        let Some(record) = records.get_mut(key) else {
            continue;
        };
        record.destroy_requested = true;
        if ensure_identity(record, log).is_ok() {
            let _ = record.process.send_signal(HolderSignal::Kill);
        }
    }

    for key in keys {
        let Some(mut record) = records.remove(&key) else {
            continue;
        };
        let waiters = record
            .termination
            .take()
            .map(|attempt| attempt.waiters)
            .unwrap_or_default();
        match wait_reap_owned(record.process.as_mut()) {
            Ok(exit) => {
                let report = finish_record(&record, HolderExitReason::Destroy, exit, true, log);
                for waiter in waiters {
                    let _ = waiter.send(Ok(report.clone()));
                }
            }
            Err(message) => {
                publish_wait_error_terminal(&mut record, log);
                let error = process_error(&record, message);
                for waiter in waiters {
                    let _ = waiter.send(Err(error.clone()));
                }
            }
        }
    }
}

fn handle_supervisor_command(
    command: SupervisorCommand,
    records: &mut HashMap<RegistrationKey, HolderRecord>,
    log: &Arc<Mutex<SupervisorLog>>,
) {
    match command {
        SupervisorCommand::Register {
            registration,
            mut process,
            reply,
        } => {
            let key = RegistrationKey::from(&registration);
            let result = if records.contains_key(&key) || registration.exit_event().is_some() {
                abort_unregistered_process(process.as_mut(), &registration.identity);
                Err(HolderSupervisorError::Process {
                    workspace_session_id: registration.workspace_session_id.clone(),
                    message: "duplicate holder registration".to_owned(),
                })
            } else {
                records.insert(
                    key,
                    HolderRecord {
                        registration,
                        process,
                        termination: None,
                        destroy_requested: false,
                        consecutive_wait_errors: 0,
                        wait_error_terminal: false,
                        wait_error_kill_attempted: false,
                    },
                );
                Ok(())
            };
            let _ = reply.send(result);
        }
        SupervisorCommand::Terminate {
            registration,
            grace,
            reply,
        } => {
            let key = RegistrationKey::from(&registration);
            let Some(record) = records.get_mut(&key) else {
                let result = registration.exit_event().map_or_else(
                    || {
                        Err(HolderSupervisorError::Unknown {
                            workspace_session_id: registration.workspace_session_id.clone(),
                        })
                    },
                    |event| Ok(event.exit.report(false)),
                );
                let _ = reply.send(result);
                return;
            };
            if let Some(termination) = record.termination.as_mut() {
                termination.waiters.push(reply);
                return;
            }
            match start_termination(record, grace, log) {
                Ok(Some(report)) => {
                    records.remove(&key);
                    let _ = reply.send(Ok(report));
                }
                Ok(None) => {
                    record
                        .termination
                        .as_mut()
                        .expect("successful termination start installs state")
                        .waiters
                        .push(reply);
                }
                Err(error) => {
                    let _ = reply.send(Err(error));
                }
            }
        }
    }
}

fn start_termination(
    record: &mut HolderRecord,
    grace: Duration,
    log: &Arc<Mutex<SupervisorLog>>,
) -> Result<Option<HolderKillReport>, HolderSupervisorError> {
    match record.process.try_wait() {
        Ok(Some(exit)) => {
            return Ok(Some(finish_record(
                record,
                HolderExitReason::Destroy,
                exit,
                false,
                log,
            )));
        }
        Ok(None) => record.consecutive_wait_errors = 0,
        Err(message) => observe_wait_error(record, &message, log),
    }
    ensure_identity(record, log)?;
    record
        .process
        .send_signal(HolderSignal::Terminate)
        .map_err(|message| process_error(record, message))?;
    record.destroy_requested = true;
    record.termination = Some(TerminationAttempt {
        phase: TerminationPhase::Grace {
            deadline: Instant::now() + grace,
        },
        waiters: Vec::new(),
    });
    Ok(None)
}

enum PollCompletion {
    None,
    Exited {
        report: HolderKillReport,
        waiters: Vec<SyncSender<Result<HolderKillReport, HolderSupervisorError>>>,
    },
    AttemptFailed {
        error: HolderSupervisorError,
        waiters: Vec<SyncSender<Result<HolderKillReport, HolderSupervisorError>>>,
    },
}

fn poll_holder_records(
    records: &mut HashMap<RegistrationKey, HolderRecord>,
    log: &Arc<Mutex<SupervisorLog>>,
) {
    let keys = records.keys().cloned().collect::<Vec<_>>();
    for key in keys {
        let completion = {
            let Some(record) = records.get_mut(&key) else {
                continue;
            };
            poll_holder_record(record, log)
        };
        match completion {
            PollCompletion::None => {}
            PollCompletion::Exited { report, waiters } => {
                records.remove(&key);
                for waiter in waiters {
                    let _ = waiter.send(Ok(report.clone()));
                }
            }
            PollCompletion::AttemptFailed { error, waiters } => {
                for waiter in waiters {
                    let _ = waiter.send(Err(error.clone()));
                }
            }
        }
    }
}

fn poll_holder_record(
    record: &mut HolderRecord,
    log: &Arc<Mutex<SupervisorLog>>,
) -> PollCompletion {
    let mut wait_failed = false;
    match record.process.try_wait() {
        Ok(Some(exit)) => {
            let reason = if record.destroy_requested {
                HolderExitReason::Destroy
            } else {
                HolderExitReason::Unexpected
            };
            let holder_was_alive = record.destroy_requested;
            let waiters = record
                .termination
                .take()
                .map(|attempt| attempt.waiters)
                .unwrap_or_default();
            let report = finish_record(record, reason, exit, holder_was_alive, log);
            return PollCompletion::Exited { report, waiters };
        }
        Ok(None) => record.consecutive_wait_errors = 0,
        Err(message) => {
            observe_wait_error(record, &message, log);
            wait_failed = true;
        }
    }

    if wait_failed
        && record.wait_error_terminal
        && !record.wait_error_kill_attempted
        && record.termination.is_none()
    {
        if let Err(error) = ensure_identity(record, log).and_then(|()| {
            record
                .process
                .send_signal(HolderSignal::Kill)
                .map_err(|message| process_error(record, message))
        }) {
            record.wait_error_kill_attempted = true;
            return PollCompletion::AttemptFailed {
                error,
                waiters: Vec::new(),
            };
        }
        record.wait_error_kill_attempted = true;
        record.termination = Some(TerminationAttempt {
            phase: TerminationPhase::Kill {
                deadline: Instant::now() + KILL_REAP_TIMEOUT,
            },
            waiters: Vec::new(),
        });
    }

    let Some(phase) = record.termination.as_ref().map(|attempt| attempt.phase) else {
        return PollCompletion::None;
    };
    match phase {
        TerminationPhase::Grace { deadline } if Instant::now() >= deadline => {
            if let Err(error) = ensure_identity(record, log).and_then(|()| {
                record
                    .process
                    .send_signal(HolderSignal::Kill)
                    .map_err(|message| process_error(record, message))
            }) {
                let waiters = record
                    .termination
                    .take()
                    .map(|attempt| attempt.waiters)
                    .unwrap_or_default();
                return PollCompletion::AttemptFailed { error, waiters };
            }
            record
                .termination
                .as_mut()
                .expect("termination attempt still exists")
                .phase = TerminationPhase::Kill {
                deadline: Instant::now() + KILL_REAP_TIMEOUT,
            };
        }
        TerminationPhase::Kill { deadline } if Instant::now() >= deadline => {
            let waiters = record
                .termination
                .take()
                .map(|attempt| attempt.waiters)
                .unwrap_or_default();
            return PollCompletion::AttemptFailed {
                error: process_error(
                    record,
                    "holder did not become reapable within one second after SIGKILL".to_owned(),
                ),
                waiters,
            };
        }
        TerminationPhase::Grace { .. } | TerminationPhase::Kill { .. } => {}
    }
    PollCompletion::None
}

fn observe_wait_error(record: &mut HolderRecord, _message: &str, log: &Arc<Mutex<SupervisorLog>>) {
    record.consecutive_wait_errors = record.consecutive_wait_errors.saturating_add(1);
    if record.consecutive_wait_errors < WAIT_ERROR_LIMIT || record.wait_error_terminal {
        return;
    }

    publish_wait_error_terminal(record, log);
}

fn publish_wait_error_terminal(record: &mut HolderRecord, log: &Arc<Mutex<SupervisorLog>>) {
    if record.wait_error_terminal {
        return;
    }
    // Repeated wait failures mean the sole reap owner can no longer prove the
    // holder is alive. Fail the public liveness gate closed immediately, then
    // keep the owned process record so teardown can safely signal and retry
    // the one and only reap operation.
    record.wait_error_terminal = true;
    let exit = HolderProcessExit::unknown();
    let event = {
        let mut log = log.lock().unwrap_or_else(|poisoned| poisoned.into_inner());
        log.stats.wait_error_total = log.stats.wait_error_total.saturating_add(1);
        log.publish(&record.registration, HolderExitReason::WaitError, exit)
    };
    publish_registration_exit(&record.registration, event);
}

fn ensure_identity(
    record: &HolderRecord,
    log: &Arc<Mutex<SupervisorLog>>,
) -> Result<(), HolderSupervisorError> {
    let matches = record
        .process
        .identity_matches(&record.registration.identity)
        .map_err(|message| process_error(record, message))?;
    if matches {
        return Ok(());
    }
    let mut log = log.lock().unwrap_or_else(|poisoned| poisoned.into_inner());
    log.stats.identity_mismatch_total = log.stats.identity_mismatch_total.saturating_add(1);
    Err(HolderSupervisorError::IdentityMismatch {
        workspace_session_id: record.registration.workspace_session_id.clone(),
        expected_pid: record.registration.identity.pid,
        generation: record.registration.identity.generation,
    })
}

fn process_error(record: &HolderRecord, message: String) -> HolderSupervisorError {
    HolderSupervisorError::Process {
        workspace_session_id: record.registration.workspace_session_id.clone(),
        message,
    }
}

fn finish_record(
    record: &HolderRecord,
    reason: HolderExitReason,
    exit: HolderProcessExit,
    holder_was_alive: bool,
    log: &Arc<Mutex<SupervisorLog>>,
) -> HolderKillReport {
    // A persistent wait error publishes a fail-closed terminal notification
    // before the child is eventually reapable. Preserve that first event and
    // its monotonic counter when the same owner later completes the reap.
    if record.registration.exit_event().is_some() {
        return exit.report(holder_was_alive);
    }
    let event = log
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner())
        .publish(&record.registration, reason, exit);
    publish_registration_exit(&record.registration, event);
    exit.report(holder_was_alive)
}

fn publish_registration_exit(registration: &HolderRegistration, event: HolderExitEvent) {
    let mut slot = registration
        .exit
        .0
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner());
    if slot.is_none() {
        *slot = Some(event);
        registration.exit.1.notify_all();
    }
}

#[cfg_attr(not(any(target_os = "linux", test)), allow(dead_code))]
fn abort_rejected_registration(command: SupervisorCommand, identity: &HolderIdentity) {
    if let SupervisorCommand::Register { mut process, .. } = command {
        abort_unregistered_process(process.as_mut(), identity);
    }
}

/// Before a register command is accepted, the spawning thread is still the
/// process owner. Once accepted, this helper runs only on the supervisor
/// thread. In both cases there remains exactly one caller of `try_wait`.
fn abort_unregistered_process(process: &mut dyn HolderProcess, identity: &HolderIdentity) {
    if matches!(process.try_wait(), Ok(Some(_))) {
        return;
    }
    if process.identity_matches(identity).unwrap_or(false) {
        let _ = process.send_signal(HolderSignal::Kill);
    }
    let _ = wait_reap_owned(process);
}

pub(super) fn wait_reap_owned(
    process: &mut dyn HolderProcess,
) -> Result<HolderProcessExit, String> {
    let mut last_error = String::new();
    for attempt in 1..=WAIT_ERROR_LIMIT {
        match process.wait_reap() {
            Ok(exit) => return Ok(exit),
            Err(error) => {
                last_error = error;
                if attempt < WAIT_ERROR_LIMIT {
                    thread::sleep(Duration::from_millis(5));
                }
            }
        }
    }
    Err(format!(
        "blocking holder reap failed after {WAIT_ERROR_LIMIT} attempts: {last_error}"
    ))
}

impl NamespaceRuntime {
    pub(crate) fn spawn_ns_holder(
        &self,
        handle: &mut MountedWorkspace,
        setup_timeout_s: f64,
        plan: NamespacePlan,
    ) -> Result<i32, WorkspaceManagerError> {
        #[cfg(not(target_os = "linux"))]
        {
            let _ = (setup_timeout_s, plan);
            handle.holder_registration =
                HolderRegistration::detached_live(handle.workspace_id.clone(), 0);
            Ok(0)
        }
        #[cfg(target_os = "linux")]
        {
            let (readiness_read, readiness_write) = pipe2(OFlag::O_CLOEXEC).map_err(setup_error)?;
            let (control_read, control_write) = pipe2(OFlag::O_CLOEXEC).map_err(setup_error)?;
            let readiness_child_fd = readiness_write.as_raw_fd();
            let control_child_fd = control_read.as_raw_fd();
            clear_cloexec(readiness_child_fd)?;
            clear_cloexec(control_child_fd)?;
            let mut child = Command::new(std::env::current_exe().map_err(setup_error)?)
                .arg("ns-holder")
                .arg(readiness_child_fd.to_string())
                .arg(control_child_fd.to_string())
                .arg(plan.network.holder_arg())
                .stdin(Stdio::null())
                .stdout(Stdio::null())
                .stderr(Stdio::piped())
                .spawn()
                .map_err(setup_error)?;
            drop(readiness_write);
            drop(control_read);
            let readiness_fd = readiness_read.as_raw_fd();
            if let Err(error) = set_nonblocking(readiness_fd)
                .and_then(|()| expect_line(readiness_fd, b"ns-up", setup_timeout_s))
            {
                let stderr = child.stderr.take();
                return Err(ns_holder_startup_error(error, &mut child, stderr));
            }
            let holder_pid = match i32::try_from(child.id()) {
                Ok(holder_pid) => holder_pid,
                Err(_) => {
                    let stderr = child.stderr.take();
                    let error =
                        setup_error(format!("ns-holder pid does not fit i32: {}", child.id()));
                    return Err(ns_holder_startup_error(error, &mut child, stderr));
                }
            };
            let generation = self.holder_supervisor.next_generation();
            let (identity, pidfd) = match inspect_linux_holder(holder_pid, generation) {
                Ok(identity) => identity,
                Err(error) => {
                    let stderr = child.stderr.take();
                    return Err(ns_holder_startup_error(error, &mut child, stderr));
                }
            };
            let process = LinuxHolderProcess {
                child,
                identity: identity.clone(),
                pidfd,
            };
            let registration = self
                .holder_supervisor
                .register_process(handle.workspace_id.clone(), identity, Box::new(process))
                .map_err(setup_error)?;
            handle.readiness_fd = readiness_read.into_raw_fd();
            handle.control_fd = control_write.into_raw_fd();
            handle.holder_pid = holder_pid;
            handle.holder_registration = registration;
            Ok(holder_pid)
        }
    }

    pub(crate) fn kill_holder(
        &self,
        registration: &HolderRegistration,
        grace_s: f64,
    ) -> Result<HolderKillReport, WorkspaceManagerError> {
        if registration.identity.pid <= 0 {
            return Ok(HolderKillReport::default());
        }
        self.holder_supervisor
            .terminate(registration, Duration::from_secs_f64(grace_s.max(0.0)))
            .map_err(|error| WorkspaceManagerError::SetupFailed {
                step: error.to_string(),
            })
    }
}

#[cfg(target_os = "linux")]
struct LinuxHolderProcess {
    child: Child,
    identity: HolderIdentity,
    pidfd: Option<OwnedFd>,
}

#[cfg(target_os = "linux")]
fn inspect_linux_holder(
    pid: i32,
    generation: u64,
) -> Result<(HolderIdentity, Option<OwnedFd>), WorkspaceManagerError> {
    let (parent_pid, start_time_ticks) = read_proc_stat(pid).map_err(setup_error)?;
    let executable = std::fs::read_link(format!("/proc/{pid}/exe")).map_err(setup_error)?;
    let pidfd = Pid::from_raw(pid).and_then(|pid| pidfd_open(pid, PidfdFlags::empty()).ok());
    let identity = HolderIdentity {
        pid,
        parent_pid,
        start_time_ticks,
        executable,
        generation,
        pidfd_available: pidfd.is_some(),
    };
    Ok((identity, pidfd))
}

#[cfg(target_os = "linux")]
impl HolderProcess for LinuxHolderProcess {
    fn try_wait(&mut self) -> Result<Option<HolderProcessExit>, String> {
        self.child
            .try_wait()
            .map(|status| status.map(process_exit))
            .map_err(|error| error.to_string())
    }

    fn wait_reap(&mut self) -> Result<HolderProcessExit, String> {
        match self.child.wait() {
            Ok(status) => Ok(process_exit(status)),
            // Rust's Unix wait path already retries EINTR. ECHILD means the
            // kernel has no waitable child, so retaining or looping the Child
            // handle cannot reap anything and would only hang shutdown.
            Err(error) if error.raw_os_error() == Some(nix::libc::ECHILD) => {
                Ok(HolderProcessExit::unknown())
            }
            Err(error) => Err(error.to_string()),
        }
    }

    fn identity_matches(&self, expected: &HolderIdentity) -> Result<bool, String> {
        if self.pidfd.is_some() {
            return Ok(self.identity == *expected);
        }
        let (parent_pid, start_time_ticks) = read_proc_stat(expected.pid)?;
        let executable = std::fs::read_link(format!("/proc/{}/exe", expected.pid))
            .map_err(|error| error.to_string())?;
        Ok(parent_pid == expected.parent_pid
            && start_time_ticks == expected.start_time_ticks
            && executable == expected.executable)
    }

    fn send_signal(&mut self, signal: HolderSignal) -> Result<(), String> {
        let signal = match signal {
            HolderSignal::Terminate => Signal::Term,
            HolderSignal::Kill => Signal::Kill,
        };
        if let Some(pidfd) = &self.pidfd {
            return pidfd_send_signal(pidfd, signal).map_err(|error| error.to_string());
        }
        let pid = Pid::from_raw(self.identity.pid)
            .ok_or_else(|| format!("invalid holder pid {}", self.identity.pid))?;
        rustix::process::kill_process(pid, signal).map_err(|error| error.to_string())
    }
}

#[cfg(target_os = "linux")]
fn read_proc_stat(pid: i32) -> Result<(i32, u64), String> {
    let stat =
        std::fs::read_to_string(format!("/proc/{pid}/stat")).map_err(|error| error.to_string())?;
    let close = stat
        .rfind(')')
        .ok_or_else(|| format!("malformed /proc/{pid}/stat"))?;
    let fields: Vec<&str> = stat[close + 1..].split_whitespace().collect();
    let parent_pid = fields
        .get(1)
        .ok_or_else(|| format!("missing ppid in /proc/{pid}/stat"))?
        .parse::<i32>()
        .map_err(|error| error.to_string())?;
    let start_time_ticks = fields
        .get(19)
        .ok_or_else(|| format!("missing starttime in /proc/{pid}/stat"))?
        .parse::<u64>()
        .map_err(|error| error.to_string())?;
    Ok((parent_pid, start_time_ticks))
}

#[cfg(all(target_os = "linux", unix))]
fn process_exit(status: std::process::ExitStatus) -> HolderProcessExit {
    HolderProcessExit {
        exit_status: status.code(),
        signal: status.signal(),
        status_raw: Some(status.into_raw()),
    }
}

#[cfg(target_os = "linux")]
fn ns_holder_startup_error(
    error: WorkspaceManagerError,
    child: &mut Child,
    stderr: Option<ChildStderr>,
) -> WorkspaceManagerError {
    let original_step = match error {
        WorkspaceManagerError::SetupFailed { step } => step,
        other => other.to_string(),
    };
    let _ = child.kill();
    let status = child.wait().ok();
    let stderr = read_child_stderr(stderr);
    WorkspaceManagerError::SetupFailed {
        step: format!(
            "{original_step}; ns-holder {}; stderr: {}",
            format_exit_status(status.as_ref()),
            stderr_summary(&stderr)
        ),
    }
}

#[cfg(target_os = "linux")]
pub(super) fn ns_holder_runtime_error(
    error: WorkspaceManagerError,
    registration: &HolderRegistration,
    supervisor: &HolderSupervisor,
) -> Result<WorkspaceManagerError, WorkspaceManagerError> {
    let original_step = match error {
        WorkspaceManagerError::SetupFailed { step } => step,
        other => other.to_string(),
    };
    let cleanup = supervisor
        .terminate(registration, Duration::ZERO)
        .map(|report| format!("holder cleanup: {report:?}"))
        .unwrap_or_else(|cleanup_error| format!("holder cleanup failed: {cleanup_error}"));
    Ok(WorkspaceManagerError::SetupFailed {
        step: format!("{original_step}; {cleanup}"),
    })
}

#[cfg(target_os = "linux")]
fn read_child_stderr(stderr: Option<ChildStderr>) -> String {
    let Some(mut stderr) = stderr else {
        return String::new();
    };
    let mut output = String::new();
    let _ = stderr.read_to_string(&mut output);
    output
}

#[cfg(target_os = "linux")]
fn stderr_summary(stderr: &str) -> String {
    let trimmed = stderr.trim();
    if trimmed.is_empty() {
        "<empty>".to_owned()
    } else {
        trimmed.replace('\n', " | ")
    }
}

#[cfg(target_os = "linux")]
fn format_exit_status(status: Option<&ExitStatus>) -> String {
    let Some(status) = status else {
        return "exit status unavailable".to_owned();
    };
    if let Some(code) = status.code() {
        return format!("exited with status {code}");
    }
    #[cfg(unix)]
    if let Some(signal) = status.signal() {
        return format!("terminated by signal {signal}");
    }
    status.to_string()
}
