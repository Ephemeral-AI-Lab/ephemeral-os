use crate::{ManagerError, SandboxDaemonEndpoint, SandboxRecord};

pub trait SandboxDaemonInstaller: Send + Sync {
    fn install_daemon(&self, _record: &SandboxRecord) -> Result<(), ManagerError> {
        Ok(())
    }

    fn start_daemon(&self, record: &SandboxRecord) -> Result<SandboxDaemonEndpoint, ManagerError>;

    fn stop_daemon(&self, record: &SandboxRecord) -> Result<(), ManagerError>;

    fn check_daemon(&self, _endpoint: &SandboxDaemonEndpoint) -> Result<(), ManagerError> {
        Ok(())
    }
}
