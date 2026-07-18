use std::path::PathBuf;
use std::sync::mpsc::{self, Receiver, RecvTimeoutError, SyncSender, TrySendError};
use std::sync::{Mutex, MutexGuard};
use std::time::Duration;

use crate::error::WorkspaceError;
use crate::model::WorkspaceOwnershipSnapshot;
use crate::session::WorkspaceManager;

mod hooks;
mod impls;
mod support;

pub use hooks::WorkspaceRuntimeHooks;

/// Coalesced, bounded notification that at least one holder exit needs
/// operation-layer reconciliation. The notification carries no resource
/// ownership: the holder supervisor remains the sole `Child`/wait owner and
/// the session dispatcher merely enters the normal joinable teardown path.
#[doc(hidden)]
#[derive(Clone)]
pub struct HolderExitNotifier {
    tx: SyncSender<HolderExitDispatchSignal>,
}

/// The receiving half of the one daemon-wide holder-exit subscription.
#[doc(hidden)]
pub struct HolderExitListener {
    rx: Receiver<HolderExitDispatchSignal>,
}

/// Explicit shutdown control for the holder-exit dispatcher.
#[doc(hidden)]
pub struct HolderExitShutdown {
    tx: SyncSender<HolderExitDispatchSignal>,
}

/// A subscription is split exactly once between the blocking dispatcher and
/// its owning shutdown/join handle.
#[doc(hidden)]
pub struct HolderExitSubscription {
    listener: HolderExitListener,
    shutdown: HolderExitShutdown,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum HolderExitDispatchSignal {
    Wake,
    Shutdown,
}

/// Result of waiting on the holder-exit channel while an event-driven cleanup
/// retry is pending. A timeout is a retry deadline, not an idle poll.
#[doc(hidden)]
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum HolderExitWait {
    Wake,
    RetryDeadline,
    Shutdown,
}

impl HolderExitNotifier {
    /// Queue one wake without blocking the reap owner. A full queue already
    /// contains a wake, so coalescing cannot lose reconciliation work.
    pub(crate) fn notify(&self) {
        match self.tx.try_send(HolderExitDispatchSignal::Wake) {
            Ok(()) | Err(TrySendError::Full(_)) | Err(TrySendError::Disconnected(_)) => {}
        }
    }

    #[doc(hidden)]
    pub fn notify_for_test(&self) {
        self.notify();
    }
}

impl HolderExitListener {
    /// Blocks without polling. `false` is terminal shutdown/disconnection.
    #[must_use]
    pub fn wait(&self) -> bool {
        matches!(self.rx.recv(), Ok(HolderExitDispatchSignal::Wake))
    }

    /// Blocks until another holder event, shutdown, or the caller's bounded
    /// cleanup-retry deadline. This is used only after a teardown failure;
    /// the normal idle path remains an un-timed blocking receive.
    #[must_use]
    pub fn wait_for_retry(&self, timeout: Duration) -> HolderExitWait {
        match self.rx.recv_timeout(timeout) {
            Ok(HolderExitDispatchSignal::Wake) => HolderExitWait::Wake,
            Ok(HolderExitDispatchSignal::Shutdown) | Err(RecvTimeoutError::Disconnected) => {
                HolderExitWait::Shutdown
            }
            Err(RecvTimeoutError::Timeout) => HolderExitWait::RetryDeadline,
        }
    }
}

impl HolderExitShutdown {
    /// Stop is join-friendly even when a coalesced wake already occupies the
    /// one-slot queue: the bounded send waits only for the dispatcher to take
    /// that pending wake.
    pub fn stop(&self) {
        let _ = self.tx.send(HolderExitDispatchSignal::Shutdown);
    }
}

impl HolderExitSubscription {
    #[must_use]
    pub fn into_parts(self) -> (HolderExitListener, HolderExitShutdown) {
        (self.listener, self.shutdown)
    }
}

pub(crate) fn holder_exit_channel() -> (HolderExitNotifier, HolderExitSubscription) {
    let (tx, rx) = mpsc::sync_channel(1);
    (
        HolderExitNotifier { tx: tx.clone() },
        HolderExitSubscription {
            listener: HolderExitListener { rx },
            shutdown: HolderExitShutdown { tx },
        },
    )
}

/// Test-only constructor for hook-backed runtimes. Production subscriptions
/// are created by the namespace-holder supervisor.
#[doc(hidden)]
#[must_use]
pub fn holder_exit_channel_for_test() -> (HolderExitNotifier, HolderExitSubscription) {
    holder_exit_channel()
}

pub struct WorkspaceRuntimeService {
    backend: WorkspaceRuntimeBackend,
}

pub(crate) struct WorkspaceRuntimeState {
    pub(crate) manager: WorkspaceManager,
    pub(crate) layer_stack_root: PathBuf,
}

enum WorkspaceRuntimeBackend {
    Runtime(Box<Mutex<WorkspaceRuntimeState>>),
    Hooks(WorkspaceRuntimeHooks),
}

impl WorkspaceRuntimeService {
    #[must_use]
    pub fn new(mut manager: WorkspaceManager, layer_stack_root: PathBuf) -> Self {
        manager.bind_layer_stack_root(layer_stack_root.clone());
        Self {
            backend: WorkspaceRuntimeBackend::Runtime(Box::new(Mutex::new(
                WorkspaceRuntimeState {
                    manager,
                    layer_stack_root,
                },
            ))),
        }
    }

    #[doc(hidden)]
    #[must_use]
    pub fn from_hooks_for_test(hooks: WorkspaceRuntimeHooks) -> Self {
        Self {
            backend: WorkspaceRuntimeBackend::Hooks(hooks),
        }
    }

    pub(crate) const fn hooks(&self) -> Option<&WorkspaceRuntimeHooks> {
        match &self.backend {
            WorkspaceRuntimeBackend::Runtime(_) => None,
            WorkspaceRuntimeBackend::Hooks(hooks) => Some(hooks),
        }
    }

    /// The isolated-network IP of a mounted workspace session, when it has one.
    /// Reads live session state; shared or veth-less workspaces yield `None`.
    ///
    /// # Errors
    /// Returns an error when the runtime state lock cannot be taken.
    pub fn isolated_ip(
        &self,
        workspace_id: &crate::model::WorkspaceSessionId,
    ) -> Result<Option<std::net::Ipv4Addr>, WorkspaceError> {
        match &self.backend {
            WorkspaceRuntimeBackend::Runtime(_) => {
                Ok(self.lock_state()?.manager.isolated_ip(workspace_id))
            }
            WorkspaceRuntimeBackend::Hooks(hooks) => (hooks.isolated_ip)(workspace_id),
        }
    }

    /// Returns bounded in-memory ownership accounting for open and retryable
    /// workspace teardown state.
    ///
    /// # Errors
    /// Returns an error when the concrete runtime state lock cannot be taken.
    pub fn ownership_snapshot(&self) -> Result<WorkspaceOwnershipSnapshot, WorkspaceError> {
        match &self.backend {
            WorkspaceRuntimeBackend::Runtime(_) => {
                Ok(self.lock_state()?.manager.ownership_snapshot())
            }
            // Hook-backed runtimes are test-only and own no concrete workspace
            // resources in this service.
            WorkspaceRuntimeBackend::Hooks(_) => Ok(WorkspaceOwnershipSnapshot::default()),
        }
    }

    /// Takes the sole process-wide holder-exit subscription. Concrete
    /// runtimes always provide one; hook runtimes may omit it when a test does
    /// not exercise live supervision.
    #[doc(hidden)]
    pub fn take_holder_exit_subscription(
        &self,
    ) -> Result<Option<HolderExitSubscription>, WorkspaceError> {
        match &self.backend {
            WorkspaceRuntimeBackend::Runtime(_) => self
                .lock_state()?
                .manager
                .take_holder_exit_subscription()
                .map(Some)
                .map_err(|message| WorkspaceError::Setup { step: message }),
            WorkspaceRuntimeBackend::Hooks(hooks) => (hooks.take_holder_exit_subscription)(),
        }
    }

    pub(crate) fn lock_state(
        &self,
    ) -> Result<MutexGuard<'_, WorkspaceRuntimeState>, WorkspaceError> {
        match &self.backend {
            WorkspaceRuntimeBackend::Runtime(state) => {
                state.lock().map_err(|_| WorkspaceError::Setup {
                    step: "workspace runtime state lock poisoned".to_owned(),
                })
            }
            WorkspaceRuntimeBackend::Hooks(_) => Err(WorkspaceError::Setup {
                step: "workspace runtime hooks do not expose concrete state".to_owned(),
            }),
        }
    }
}
