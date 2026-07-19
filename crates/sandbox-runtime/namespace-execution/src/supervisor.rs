use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Arc, Condvar, Mutex};
use std::thread::{self, JoinHandle};
use std::time::{Duration, Instant};

use sandbox_runtime_namespace_process::runner::protocol::RunResult;

use crate::error::NamespaceExecutionError;
use crate::launcher::RunnerChild;

const COMPLETION_POLL_INTERVAL: Duration = Duration::from_millis(5);
const COMPLETION_IDLE_TIMEOUT: Duration = Duration::from_millis(250);
const SUPERVISOR_SHUTDOWN_TIMEOUT: Duration = Duration::from_secs(1);

type Completion = Box<dyn FnOnce(Result<RunResult, NamespaceExecutionError>) + Send + 'static>;

struct CompletionJob {
    child: Box<dyn RunnerChild>,
    complete: Option<Completion>,
    termination_requested: bool,
}

#[derive(Default)]
struct SupervisorQueue {
    jobs: Vec<CompletionJob>,
    shutdown: bool,
    shutdown_deadline: Option<Instant>,
    worker_running: bool,
    stopped: bool,
    failure: Option<String>,
    shutdown_join_timeouts: usize,
}

struct SupervisorState {
    queue: Mutex<SupervisorQueue>,
    ready: Condvar,
    active: AtomicUsize,
}

pub(crate) struct CompletionSupervisor {
    state: Arc<SupervisorState>,
    worker: Mutex<Option<JoinHandle<()>>>,
}

impl CompletionSupervisor {
    pub(crate) fn new() -> Self {
        let state = Arc::new(SupervisorState {
            queue: Mutex::new(SupervisorQueue::default()),
            ready: Condvar::new(),
            active: AtomicUsize::new(0),
        });
        Self {
            state,
            worker: Mutex::new(None),
        }
    }

    pub(crate) fn submit(
        &self,
        child: Box<dyn RunnerChild>,
        complete: impl FnOnce(Result<RunResult, NamespaceExecutionError>) + Send + 'static,
    ) -> Result<(), NamespaceExecutionError> {
        let mut queue = self
            .state
            .queue
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        if queue.shutdown {
            let job = CompletionJob {
                child,
                complete: None,
                termination_requested: false,
            };
            if queue.stopped {
                drop(queue);
                terminate_rejected(job);
            } else {
                queue.jobs.push(job);
                self.state.ready.notify_one();
            }
            return Err(NamespaceExecutionError::Shutdown);
        }
        self.state.active.fetch_add(1, Ordering::Release);
        queue.jobs.push(CompletionJob {
            child,
            complete: Some(Box::new(complete)),
            termination_requested: false,
        });
        if !queue.worker_running {
            let previous = self
                .worker
                .lock()
                .unwrap_or_else(std::sync::PoisonError::into_inner)
                .take();
            if let Some(previous) = previous {
                if previous.join().is_err() {
                    queue.failure = Some("completion supervisor panicked".to_owned());
                    queue.shutdown = true;
                    queue.stopped = true;
                    let mut rejected = queue
                        .jobs
                        .pop()
                        .expect("the just-submitted completion job is present");
                    rejected.complete = None;
                    self.state.active.fetch_sub(1, Ordering::AcqRel);
                    drop(queue);
                    terminate_rejected(rejected);
                    return Err(NamespaceExecutionError::Completion(
                        "completion supervisor panicked".to_owned(),
                    ));
                }
            }
            queue.worker_running = true;
            let worker_state = Arc::clone(&self.state);
            match thread::Builder::new()
                .name("eos-command-reaper".to_owned())
                .spawn(move || supervise(worker_state))
            {
                Ok(worker) => {
                    *self
                        .worker
                        .lock()
                        .unwrap_or_else(std::sync::PoisonError::into_inner) = Some(worker);
                }
                Err(error) => {
                    queue.worker_running = false;
                    let mut rejected = queue
                        .jobs
                        .pop()
                        .expect("the just-submitted completion job is present");
                    rejected.complete = None;
                    self.state.active.fetch_sub(1, Ordering::AcqRel);
                    drop(queue);
                    terminate_rejected(rejected);
                    return Err(NamespaceExecutionError::Completion(format!(
                        "failed to start completion supervisor: {error}"
                    )));
                }
            }
        }
        self.state.ready.notify_one();
        Ok(())
    }

    pub(crate) fn active(&self) -> usize {
        self.state.active.load(Ordering::Acquire)
    }

    pub(crate) fn ensure_accepting(&self) -> Result<(), NamespaceExecutionError> {
        let queue = self
            .state
            .queue
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        if queue.shutdown {
            Err(NamespaceExecutionError::Shutdown)
        } else {
            Ok(())
        }
    }

    pub(crate) fn worker_threads(&self) -> usize {
        let queue = self
            .state
            .queue
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        usize::from(queue.worker_running)
    }

    pub(crate) fn shutdown_and_join(&self) -> Result<(), NamespaceExecutionError> {
        {
            let mut queue = self
                .state
                .queue
                .lock()
                .unwrap_or_else(std::sync::PoisonError::into_inner);
            if !queue.shutdown {
                queue.shutdown = true;
                queue.shutdown_deadline = Some(Instant::now() + SUPERVISOR_SHUTDOWN_TIMEOUT);
                if !queue.worker_running {
                    queue.stopped = true;
                }
                self.state.ready.notify_all();
            }
        }

        let worker = self
            .worker
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
            .take();
        if let Some(worker) = worker {
            if worker.join().is_err() {
                let mut queue = self
                    .state
                    .queue
                    .lock()
                    .unwrap_or_else(std::sync::PoisonError::into_inner);
                queue.failure = Some("completion supervisor panicked".to_owned());
                queue.worker_running = false;
                queue.stopped = true;
                self.state.ready.notify_all();
            }
        } else {
            let mut queue = self
                .state
                .queue
                .lock()
                .unwrap_or_else(std::sync::PoisonError::into_inner);
            while !queue.stopped {
                queue = self
                    .state
                    .ready
                    .wait(queue)
                    .unwrap_or_else(std::sync::PoisonError::into_inner);
            }
        }

        let queue = self
            .state
            .queue
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        match &queue.failure {
            Some(failure) => Err(NamespaceExecutionError::Completion(failure.clone())),
            None if queue.shutdown_join_timeouts > 0 => {
                Err(NamespaceExecutionError::Completion(format!(
                    "{} namespace runner(s) did not exit within the supervisor shutdown deadline",
                    queue.shutdown_join_timeouts
                )))
            }
            None => Ok(()),
        }
    }
}

impl Drop for CompletionSupervisor {
    fn drop(&mut self) {
        let _ = self.shutdown_and_join();
    }
}

fn supervise(state: Arc<SupervisorState>) {
    loop {
        let (mut jobs, shutdown_deadline) = {
            let mut queue = state
                .queue
                .lock()
                .unwrap_or_else(std::sync::PoisonError::into_inner);
            while queue.jobs.is_empty() && !queue.shutdown {
                let (next, timeout) = state
                    .ready
                    .wait_timeout(queue, COMPLETION_IDLE_TIMEOUT)
                    .unwrap_or_else(std::sync::PoisonError::into_inner);
                queue = next;
                if timeout.timed_out() && queue.jobs.is_empty() && !queue.shutdown {
                    queue.worker_running = false;
                    state.ready.notify_all();
                    return;
                }
            }
            if queue.jobs.is_empty() && queue.shutdown {
                queue.worker_running = false;
                queue.stopped = true;
                state.ready.notify_all();
                return;
            }
            (std::mem::take(&mut queue.jobs), queue.shutdown_deadline)
        };

        let mut pending = Vec::with_capacity(jobs.len());
        let mut completed = Vec::new();
        let mut shutdown_join_timeouts = 0;
        for mut job in jobs.drain(..) {
            if shutdown_deadline.is_some() && !job.termination_requested {
                job.child.terminate();
                job.termination_requested = true;
            }
            match job.child.try_wait_completion() {
                Ok(Some(result)) => completed.push((job, Ok(result))),
                Ok(None)
                    if shutdown_deadline.is_some_and(|deadline| Instant::now() >= deadline) =>
                {
                    shutdown_join_timeouts += 1;
                    completed.push((
                        job,
                        Err(NamespaceExecutionError::Completion(
                            "timed out joining namespace runner during supervisor shutdown"
                                .to_owned(),
                        )),
                    ));
                }
                Ok(None) => pending.push(job),
                Err(error) => completed.push((job, Err(error))),
            }
        }

        if !pending.is_empty() {
            let mut queue = state
                .queue
                .lock()
                .unwrap_or_else(std::sync::PoisonError::into_inner);
            queue.jobs.append(&mut pending);
        }
        if shutdown_join_timeouts > 0 {
            let mut queue = state
                .queue
                .lock()
                .unwrap_or_else(std::sync::PoisonError::into_inner);
            queue.shutdown_join_timeouts += shutdown_join_timeouts;
        }

        for (mut job, result) in completed {
            if let Some(complete) = job.complete.take() {
                state.active.fetch_sub(1, Ordering::AcqRel);
                complete(result);
            }
        }

        let queue = state
            .queue
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        if !queue.jobs.is_empty() {
            let _ = state
                .ready
                .wait_timeout(queue, COMPLETION_POLL_INTERVAL)
                .unwrap_or_else(std::sync::PoisonError::into_inner);
        }
    }
}

fn terminate_rejected(mut job: CompletionJob) {
    job.child.terminate();
    let deadline = Instant::now() + SUPERVISOR_SHUTDOWN_TIMEOUT;
    loop {
        match job.child.try_wait_completion() {
            Ok(Some(_)) | Err(_) => return,
            Ok(None) if Instant::now() >= deadline => return,
            Ok(None) => thread::sleep(COMPLETION_POLL_INTERVAL),
        }
    }
}
