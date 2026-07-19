use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Condvar, Mutex, PoisonError, Weak};
use std::thread::JoinHandle;
use std::time::{Duration, Instant};

use sandbox_observability_telemetry::record::names;
use sandbox_observability_telemetry::TraceContext;
use sandbox_runtime_layerstack::LayerStack;
use serde_json::json;

use crate::layerstack::actions::squash::{self, SquashCause};
use crate::layerstack::LayerStackService;
use crate::workspace_session::WorkspaceSessionService;

use super::policies::squash_at_n_layers;

const AUTOSQUASH_IDLE_TIMEOUT: Duration = Duration::from_millis(250);

#[derive(Clone, Copy)]
pub(crate) enum AutosquashTriggerReason {
    Startup,
    LayerCommitted,
}

impl AutosquashTriggerReason {
    fn as_str(self) -> &'static str {
        match self {
            Self::Startup => "startup",
            Self::LayerCommitted => "layer_committed",
        }
    }
}

struct Notification {
    context: TraceContext,
    trigger_reason: AutosquashTriggerReason,
    enqueued_at: Instant,
    coalesced_notifications: usize,
}

#[derive(Default)]
struct QueueState {
    pending: Option<Notification>,
    shutdown: bool,
    worker_running: bool,
    services: Option<(Weak<LayerStackService>, Weak<WorkspaceSessionService>)>,
}

pub(crate) struct AutosquashQueue {
    state: Mutex<QueueState>,
    ready: Condvar,
    worker: Mutex<Option<JoinHandle<()>>>,
}

impl AutosquashQueue {
    pub(crate) fn new() -> Self {
        Self {
            state: Mutex::new(QueueState::default()),
            ready: Condvar::new(),
            worker: Mutex::new(None),
        }
    }

    fn configure(
        &self,
        layerstack: &Arc<LayerStackService>,
        workspace_session: &Arc<WorkspaceSessionService>,
    ) {
        let mut state = self.state.lock().unwrap_or_else(PoisonError::into_inner);
        state.services = Some((
            Arc::downgrade(layerstack),
            Arc::downgrade(workspace_session),
        ));
    }

    pub(crate) fn notify(
        self: &Arc<Self>,
        context: TraceContext,
        trigger_reason: AutosquashTriggerReason,
    ) {
        let mut state = self.state.lock().unwrap_or_else(PoisonError::into_inner);
        if state.shutdown {
            return;
        }
        if let Some(pending) = state.pending.as_mut() {
            pending.coalesced_notifications += 1;
        } else {
            state.pending = Some(Notification {
                context,
                trigger_reason,
                enqueued_at: Instant::now(),
                coalesced_notifications: 0,
            });
        }
        if !state.worker_running && state.services.is_some() {
            if let Some(previous) = self
                .worker
                .lock()
                .unwrap_or_else(PoisonError::into_inner)
                .take()
            {
                if previous.join().is_err() {
                    eprintln!("autosquash worker panicked before restart");
                }
            }
            state.worker_running = true;
            let queue = Arc::clone(self);
            match std::thread::Builder::new()
                .name("layerstack-autosquash".to_owned())
                .spawn(move || worker_loop(queue))
            {
                Ok(worker) => {
                    *self.worker.lock().unwrap_or_else(PoisonError::into_inner) = Some(worker);
                }
                Err(error) => {
                    state.worker_running = false;
                    eprintln!("autosquash worker failed to start: {error}");
                }
            }
        }
        self.ready.notify_one();
    }

    fn receive(&self) -> Option<Notification> {
        let mut state = self.state.lock().unwrap_or_else(PoisonError::into_inner);
        loop {
            if let Some(notification) = state.pending.take() {
                return Some(notification);
            }
            if state.shutdown {
                state.worker_running = false;
                self.ready.notify_all();
                return None;
            }
            let (next, timeout) = self
                .ready
                .wait_timeout(state, AUTOSQUASH_IDLE_TIMEOUT)
                .unwrap_or_else(PoisonError::into_inner);
            state = next;
            if timeout.timed_out() && state.pending.is_none() && !state.shutdown {
                state.worker_running = false;
                self.ready.notify_all();
                return None;
            }
        }
    }

    fn services(&self) -> Option<(Arc<LayerStackService>, Arc<WorkspaceSessionService>)> {
        let state = self.state.lock().unwrap_or_else(PoisonError::into_inner);
        let (layerstack, workspace_session) = state.services.as_ref()?;
        Some((layerstack.upgrade()?, workspace_session.upgrade()?))
    }

    fn stop_without_services(&self) {
        let mut state = self.state.lock().unwrap_or_else(PoisonError::into_inner);
        state.worker_running = false;
        self.ready.notify_all();
    }

    fn shutdown_and_join(&self) {
        {
            let mut state = self.state.lock().unwrap_or_else(PoisonError::into_inner);
            state.shutdown = true;
            state.pending = None;
            self.ready.notify_all();
        }
        let worker = self
            .worker
            .lock()
            .unwrap_or_else(PoisonError::into_inner)
            .take();
        if let Some(worker) = worker {
            if worker.join().is_err() {
                let mut state = self.state.lock().unwrap_or_else(PoisonError::into_inner);
                state.worker_running = false;
                self.ready.notify_all();
            }
        } else {
            let mut state = self.state.lock().unwrap_or_else(PoisonError::into_inner);
            while state.worker_running {
                state = self
                    .ready
                    .wait(state)
                    .unwrap_or_else(PoisonError::into_inner);
            }
        }
    }

    fn worker_threads(&self) -> usize {
        let state = self.state.lock().unwrap_or_else(PoisonError::into_inner);
        usize::from(state.worker_running)
    }
}

pub(crate) struct AutosquashEngine {
    queue: Option<Arc<AutosquashQueue>>,
}

impl AutosquashEngine {
    pub(crate) fn start(
        layerstack: Arc<LayerStackService>,
        workspace_session: Arc<WorkspaceSessionService>,
    ) -> Self {
        let Some(queue) = layerstack.autosquash_queue.clone() else {
            return Self { queue: None };
        };
        queue.configure(&layerstack, &workspace_session);
        queue.notify(startup_context(), AutosquashTriggerReason::Startup);
        Self { queue: Some(queue) }
    }

    pub(crate) fn shutdown_and_join(&self) {
        if let Some(queue) = &self.queue {
            queue.shutdown_and_join();
        }
    }

    pub(crate) fn worker_threads(&self) -> usize {
        self.queue
            .as_ref()
            .map_or(0, |queue| queue.worker_threads())
    }
}

impl Drop for AutosquashEngine {
    fn drop(&mut self) {
        self.shutdown_and_join();
    }
}

fn startup_context() -> TraceContext {
    internal_context("startup")
}

pub(crate) fn internal_context(reason: &str) -> TraceContext {
    static NEXT_TRACE: AtomicU64 = AtomicU64::new(0);
    TraceContext {
        trace: Arc::from(format!(
            "autosquash-{reason}-{}-{}",
            std::process::id(),
            NEXT_TRACE.fetch_add(1, Ordering::Relaxed)
        )),
        parent: None,
    }
}

fn worker_loop(queue: Arc<AutosquashQueue>) {
    while let Some(notification) = queue.receive() {
        let Some((layerstack, workspace_session)) = queue.services() else {
            queue.stop_without_services();
            return;
        };
        let observer = layerstack.obs.clone();
        observer.with_context(notification.context.clone(), || {
            if let Err(error) = evaluate(&layerstack, &workspace_session, notification) {
                eprintln!("autosquash evaluation failed: {error}");
            }
        });
    }
}

fn evaluate(
    layerstack: &Arc<LayerStackService>,
    workspace_session: &Arc<WorkspaceSessionService>,
    notification: Notification,
) -> Result<(), String> {
    let threshold = layerstack
        .config
        .autosquash_squash_at_n_layers
        .expect("enabled worker has a configured threshold");
    let queue_delay = notification.enqueued_at.elapsed();
    let started_at = Instant::now();
    let trigger_reason = notification.trigger_reason.as_str();
    let coalesced = notification.coalesced_notifications;
    let observer = layerstack.obs.clone();
    observer.scope(names::LAYERSTACK_AUTOSQUASH_EVALUATE, |span| {
        span.attr("trigger_reason", trigger_reason)
            .attr("policy", squash_at_n_layers::NAME)
            .attr("threshold", threshold)
            .attr("queue_delay_ms", millis(queue_delay))
            .attr("coalesced_notifications", coalesced);

        let mut last_observed_layers = None;
        let evaluation: Result<(), String> = (|| {
            let observed_layers = active_layer_count(layerstack)?;
            last_observed_layers = Some(observed_layers);
            span.attr("observed_layers", observed_layers);
            if !squash_at_n_layers::matches(observed_layers, threshold) {
                span.attr("decision", "below_threshold");
                return Ok(());
            }

            let _gate = layerstack
                .squash_gate
                .lock()
                .unwrap_or_else(PoisonError::into_inner);
            let observed_layers = active_layer_count(layerstack)?;
            last_observed_layers = Some(observed_layers);
            span.attr("observed_layers", observed_layers);
            if !squash_at_n_layers::matches(observed_layers, threshold) {
                span.attr("decision", "below_threshold");
                return Ok(());
            }

            observer.event(
                names::LAYERSTACK_AUTOSQUASH_TRIGGERED,
                json!({
                    "policy": squash_at_n_layers::NAME,
                    "threshold": threshold,
                    "observed_layers": observed_layers,
                    "trigger_reason": trigger_reason,
                    "queue_delay_ms": millis(queue_delay),
                    "coalesced_notifications": coalesced,
                }),
            );
            let squash_started_at = Instant::now();
            let action = squash::execute(
                layerstack,
                workspace_session,
                SquashCause::Autosquash {
                    policy: squash_at_n_layers::NAME,
                    threshold,
                    observed_layers,
                    trigger_reason,
                },
            )?;
            let squash_duration = squash_started_at.elapsed();
            span.attr(
                "decision",
                if action.blocks_committed == 0 {
                    "no_squashable_blocks"
                } else {
                    "trigger"
                },
            );
            observer.event(
                names::LAYERSTACK_AUTOSQUASH_COMPLETED,
                json!({
                    "policy": squash_at_n_layers::NAME,
                    "threshold": threshold,
                    "before_layers": action.before_layers,
                    "after_layers": action.after_layers,
                    "blocks_committed": action.blocks_committed,
                    "queue_delay_ms": millis(queue_delay),
                    "squash_duration_ms": millis(squash_duration),
                    "total_convergence_ms": millis(notification.enqueued_at.elapsed()),
                    "coalesced_notifications": coalesced,
                    "status": "completed",
                }),
            );
            Ok(())
        })();

        if let Err(error) = &evaluation {
            span.attr("error", error.clone());
            observer.event(
                names::LAYERSTACK_AUTOSQUASH_FAILED,
                json!({
                    "policy": squash_at_n_layers::NAME,
                    "threshold": threshold,
                    "observed_layers": last_observed_layers,
                    "error": error,
                    "queue_delay_ms": millis(queue_delay),
                    "elapsed_ms": millis(started_at.elapsed()),
                    "status": "failed",
                }),
            );
        }
        evaluation
    })
}

fn active_layer_count(layerstack: &LayerStackService) -> Result<usize, String> {
    LayerStack::open(layerstack.layer_stack_root().to_path_buf())
        .and_then(|stack| stack.read_active_manifest())
        .map(|manifest| manifest.layers.len())
        .map_err(|error| error.to_string())
}

fn millis(duration: Duration) -> u64 {
    u64::try_from(duration.as_millis()).unwrap_or(u64::MAX)
}
