use crate::operations::ManagerServices;
use crate::{ManagerError, SandboxId, SandboxRecord, SandboxState};

pub(crate) fn destroy_sandbox(
    services: &ManagerServices,
    id: SandboxId,
) -> Result<SandboxRecord, ManagerError> {
    let current = services.store.inspect(&id)?;
    if matches!(
        current.state,
        SandboxState::Creating | SandboxState::Stopping
    ) {
        return Err(ManagerError::InvalidStateTransition {
            id,
            from: current.state,
            to: SandboxState::Stopping,
        });
    }
    let stopping =
        services
            .store
            .transition_state(&current.id, current.state, SandboxState::Stopping)?;
    if stopping.daemon.is_some() {
        services.daemon_installer.stop_daemon(&stopping)?;
    }
    match services.runtime.destroy_sandbox(&stopping) {
        Ok(()) => {
            services
                .store
                .set_state(&stopping.id, SandboxState::Stopped)?;
            let removed = services.store.remove(&stopping.id)?;
            let _ = services.resource_ring.remove(&stopping.id);
            Ok(removed)
        }
        Err(error) => {
            let _ = services.store.set_state(&stopping.id, SandboxState::Failed);
            Err(error)
        }
    }
}
