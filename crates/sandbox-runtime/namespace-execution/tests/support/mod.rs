//! Shared test fixtures for the engine suites: fakes for the `pub(crate)`
//! launcher seam (surfaced via the crate's `test_support` facade) plus small
//! constructors. Each integration binary that needs them does `mod support;`,
//! so unused-in-one-binary items are expected — hence `allow(dead_code)`.

#![allow(dead_code)]

use std::path::PathBuf;
use std::sync::{Arc, Condvar, Mutex};

use sandbox_runtime_namespace_execution::test_support::{
    open_pty_pair, NsRunnerLauncher, PtyMaster, RunnerChild,
};
use sandbox_runtime_namespace_execution::{
    ExecutionObserver, NamespaceExecutionError, NamespaceExecutionId,
    NamespaceExecutionTerminalStatus, NamespaceTarget, RunnerOutcome, ShellOperation,
};
use sandbox_runtime_namespace_process::runner::protocol::{
    NamespaceRunnerRequest, NsFds, RunResult,
};

/// A controllable completion cell the test drives. `complete`/`cancel` unblock a
/// `FakeRunnerChild` blocked in `wait_completion` — a real concurrent unblock.
struct FakeCompletion {
    slot: Mutex<Option<RunResult>>,
    ready: Condvar,
}

impl FakeCompletion {
    fn new() -> Self {
        Self {
            slot: Mutex::new(None),
            ready: Condvar::new(),
        }
    }

    fn complete(&self, result: RunResult) {
        let mut slot = self.slot.lock().expect("fake completion mutex poisoned");
        if slot.is_none() {
            *slot = Some(result);
            self.ready.notify_all();
        }
    }

    fn cancel(&self) {
        self.complete(RunResult {
            exit_code: 130,
            payload: serde_json::json!({ "status": "cancelled" }),
        });
    }

    fn wait(&self) -> RunResult {
        let mut slot = self.slot.lock().expect("fake completion mutex poisoned");
        while slot.is_none() {
            slot = self
                .ready
                .wait(slot)
                .expect("fake completion mutex poisoned");
        }
        slot.clone()
            .expect("wait loop exits only once the slot is set")
    }
}

struct FakeRunnerChild {
    completion: Arc<FakeCompletion>,
}

impl RunnerChild for FakeRunnerChild {
    fn wait_completion(&mut self) -> Result<RunResult, NamespaceExecutionError> {
        Ok(self.completion.wait())
    }
}

#[derive(Default)]
struct FakeLauncherState {
    requests: Vec<NamespaceRunnerRequest>,
    request_ids: Vec<String>,
    completions: Vec<Arc<FakeCompletion>>,
    piped_setup_timeouts: Vec<f64>,
}

/// A fake `NsRunnerLauncher`: records each request, hands back a `FakeRunnerChild`
/// bound to a fresh `FakeCompletion`, and for `spawn_pty` builds a real-`openpt`
/// `PtyMaster` whose cancel trips that completion. Cloneable — the engine holds
/// one clone, the test another, sharing the recorded state.
#[derive(Clone, Default)]
pub struct FakeLauncher {
    state: Arc<Mutex<FakeLauncherState>>,
}

impl FakeLauncher {
    #[must_use]
    pub fn new() -> Self {
        Self::default()
    }

    #[must_use]
    pub fn recorded_request_ids(&self) -> Vec<String> {
        self.state
            .lock()
            .expect("fake launcher mutex poisoned")
            .request_ids
            .clone()
    }

    #[must_use]
    pub fn recorded_requests(&self) -> Vec<NamespaceRunnerRequest> {
        self.state
            .lock()
            .expect("fake launcher mutex poisoned")
            .requests
            .clone()
    }

    #[must_use]
    pub fn piped_setup_timeouts(&self) -> Vec<f64> {
        self.state
            .lock()
            .expect("fake launcher mutex poisoned")
            .piped_setup_timeouts
            .clone()
    }

    /// Complete the most recently spawned execution.
    pub fn complete_latest(&self, result: RunResult) {
        let completion = self
            .state
            .lock()
            .expect("fake launcher mutex poisoned")
            .completions
            .last()
            .map(Arc::clone);
        if let Some(completion) = completion {
            completion.complete(result);
        }
    }

    fn record(&self, request: &NamespaceRunnerRequest) -> Arc<FakeCompletion> {
        let completion = Arc::new(FakeCompletion::new());
        let mut state = self.state.lock().expect("fake launcher mutex poisoned");
        state.requests.push(request.clone());
        state.request_ids.push(request.request_id.clone());
        state.completions.push(Arc::clone(&completion));
        completion
    }
}

impl NsRunnerLauncher for FakeLauncher {
    fn spawn_pty(
        &self,
        request: NamespaceRunnerRequest,
    ) -> Result<(Box<dyn RunnerChild>, PtyMaster), NamespaceExecutionError> {
        let completion = self.record(&request);
        let (master, slave) =
            open_pty_pair().map_err(|error| NamespaceExecutionError::Spawn(error.to_string()))?;
        let cancel = Arc::clone(&completion);
        let pty = PtyMaster::spawn(master, None, Box::new(move || cancel.cancel()))
            .map_err(|error| NamespaceExecutionError::Spawn(error.to_string()))?;
        drop(slave);
        Ok((Box::new(FakeRunnerChild { completion }), pty))
    }

    fn spawn_piped(
        &self,
        _mode_flag: &'static str,
        request: NamespaceRunnerRequest,
        setup_timeout_s: f64,
    ) -> Result<Box<dyn RunnerChild>, NamespaceExecutionError> {
        let completion = self.record(&request);
        self.state
            .lock()
            .expect("fake launcher mutex poisoned")
            .piped_setup_timeouts
            .push(setup_timeout_s);
        Ok(Box::new(FakeRunnerChild { completion }))
    }
}

/// An observed lifecycle event, recorded by `FakeObserver`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ObserverEvent {
    Running(NamespaceExecutionId),
    Terminal(
        NamespaceExecutionId,
        NamespaceExecutionTerminalStatus,
        Option<i64>,
    ),
}

/// Records `on_running`/`on_terminal` calls; `await_terminal` blocks until the
/// terminal event lands (the watcher fires it after `resolve`).
pub struct FakeObserver {
    events: Mutex<Vec<ObserverEvent>>,
    terminal: Condvar,
}

impl FakeObserver {
    #[must_use]
    pub fn new() -> Self {
        Self {
            events: Mutex::new(Vec::new()),
            terminal: Condvar::new(),
        }
    }

    #[must_use]
    pub fn events(&self) -> Vec<ObserverEvent> {
        self.events
            .lock()
            .expect("fake observer mutex poisoned")
            .clone()
    }

    pub fn await_terminal(&self) -> (NamespaceExecutionTerminalStatus, Option<i64>) {
        let mut events = self.events.lock().expect("fake observer mutex poisoned");
        loop {
            if let Some(terminal) = events.iter().rev().find_map(terminal_of) {
                return terminal;
            }
            events = self
                .terminal
                .wait(events)
                .expect("fake observer mutex poisoned");
        }
    }
}

impl Default for FakeObserver {
    fn default() -> Self {
        Self::new()
    }
}

impl ExecutionObserver for FakeObserver {
    fn on_running(&self, id: &NamespaceExecutionId) {
        self.events
            .lock()
            .expect("fake observer mutex poisoned")
            .push(ObserverEvent::Running(id.clone()));
    }

    fn on_terminal(
        &self,
        id: &NamespaceExecutionId,
        status: NamespaceExecutionTerminalStatus,
        exit_code: Option<i64>,
    ) {
        self.events
            .lock()
            .expect("fake observer mutex poisoned")
            .push(ObserverEvent::Terminal(id.clone(), status, exit_code));
        self.terminal.notify_all();
    }
}

fn terminal_of(event: &ObserverEvent) -> Option<(NamespaceExecutionTerminalStatus, Option<i64>)> {
    match event {
        ObserverEvent::Terminal(_, status, exit_code) => Some((*status, *exit_code)),
        ObserverEvent::Running(_) => None,
    }
}

/// A shell op whose `finalize` succeeds, yielding the runner exit code.
#[derive(Default)]
pub struct OkShellOp;

impl ShellOperation for OkShellOp {
    type Output = i64;

    fn operation_name(&self) -> &'static str {
        "ok_shell_op"
    }

    fn command(&self) -> &str {
        "true"
    }

    fn timeout_seconds(&self) -> Option<f64> {
        None
    }

    fn finalize(self: Box<Self>, outcome: RunnerOutcome) -> Result<i64, NamespaceExecutionError> {
        Ok(outcome.exit_code())
    }
}

/// A shell op whose `finalize` rejects the outcome.
#[derive(Default)]
pub struct ErrShellOp;

impl ShellOperation for ErrShellOp {
    type Output = i64;

    fn operation_name(&self) -> &'static str {
        "err_shell_op"
    }

    fn command(&self) -> &str {
        "false"
    }

    fn timeout_seconds(&self) -> Option<f64> {
        None
    }

    fn finalize(self: Box<Self>, _outcome: RunnerOutcome) -> Result<i64, NamespaceExecutionError> {
        Err(NamespaceExecutionError::Finalize("err shell op".to_owned()))
    }
}

#[must_use]
pub fn run_result(exit_code: i32, status: &str) -> RunResult {
    RunResult {
        exit_code,
        payload: serde_json::json!({ "status": status }),
    }
}

#[must_use]
pub fn run_result_without_status(exit_code: i32) -> RunResult {
    RunResult {
        exit_code,
        payload: serde_json::json!({}),
    }
}

#[must_use]
pub fn outcome(result: RunResult) -> RunnerOutcome {
    RunnerOutcome::new(result)
}

#[must_use]
pub fn sample_target() -> NamespaceTarget {
    NamespaceTarget {
        workspace_root: PathBuf::from("/workspace"),
        layer_paths: Vec::new(),
        upperdir: None,
        workdir: None,
        ns_fds: NsFds {
            user: None,
            mnt: None,
            pid: None,
            net: None,
        },
    }
}
