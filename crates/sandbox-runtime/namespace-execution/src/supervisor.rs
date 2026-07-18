use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Arc, Condvar, Mutex};
use std::thread::{self, JoinHandle};
use std::time::Duration;

use sandbox_runtime_namespace_process::runner::protocol::RunResult;

use crate::error::NamespaceExecutionError;
use crate::launcher::RunnerChild;

const COMPLETION_POLL_INTERVAL: Duration = Duration::from_millis(5);

type Completion = Box<dyn FnOnce(Result<RunResult, NamespaceExecutionError>) + Send + 'static>;

struct CompletionJob {
    child: Box<dyn RunnerChild>,
    complete: Option<Completion>,
}

#[derive(Default)]
struct SupervisorQueue {
    jobs: Vec<CompletionJob>,
    shutdown: bool,
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
        let worker_state = Arc::clone(&state);
        let worker = thread::Builder::new()
            .name("eos-command-reaper".to_owned())
            .spawn(move || supervise(worker_state))
            .expect("spawn namespace execution completion supervisor");
        Self {
            state,
            worker: Mutex::new(Some(worker)),
        }
    }

    pub(crate) fn submit(
        &self,
        child: Box<dyn RunnerChild>,
        complete: impl FnOnce(Result<RunResult, NamespaceExecutionError>) + Send + 'static,
    ) {
        self.state.active.fetch_add(1, Ordering::Release);
        let mut queue = self
            .state
            .queue
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        queue.jobs.push(CompletionJob {
            child,
            complete: Some(Box::new(complete)),
        });
        self.state.ready.notify_one();
    }

    pub(crate) fn active(&self) -> usize {
        self.state.active.load(Ordering::Acquire)
    }
}

impl Drop for CompletionSupervisor {
    fn drop(&mut self) {
        {
            let mut queue = self
                .state
                .queue
                .lock()
                .unwrap_or_else(std::sync::PoisonError::into_inner);
            queue.shutdown = true;
            self.state.ready.notify_one();
        }
        if let Some(worker) = self
            .worker
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
            .take()
        {
            let _ = worker.join();
        }
    }
}

fn supervise(state: Arc<SupervisorState>) {
    loop {
        let (mut jobs, shutting_down) = {
            let mut queue = state
                .queue
                .lock()
                .unwrap_or_else(std::sync::PoisonError::into_inner);
            while queue.jobs.is_empty() && !queue.shutdown {
                queue = state
                    .ready
                    .wait(queue)
                    .unwrap_or_else(std::sync::PoisonError::into_inner);
            }
            if queue.jobs.is_empty() && queue.shutdown {
                return;
            }
            (std::mem::take(&mut queue.jobs), queue.shutdown)
        };

        let mut pending = Vec::with_capacity(jobs.len());
        let mut completed = Vec::new();
        for mut job in jobs.drain(..) {
            if shutting_down {
                job.child.terminate();
            }
            match job.child.try_wait_completion() {
                Ok(Some(result)) => completed.push((job, Ok(result))),
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

        for (mut job, result) in completed {
            state.active.fetch_sub(1, Ordering::AcqRel);
            if let Some(complete) = job.complete.take() {
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
