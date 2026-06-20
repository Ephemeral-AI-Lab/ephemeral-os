use crate::{ManagerResult, SandboxDaemonEndpoint, SandboxRecord};

pub trait SandboxDaemonInstaller: Send + Sync {
    fn install_daemon(&self, _record: &SandboxRecord) -> ManagerResult<()> {
        Ok(())
    }

    fn start_daemon(&self, record: &SandboxRecord) -> ManagerResult<SandboxDaemonEndpoint>;

    fn stop_daemon(&self, record: &SandboxRecord) -> ManagerResult<()>;

    fn check_daemon(&self, _endpoint: &SandboxDaemonEndpoint) -> ManagerResult<()> {
        Ok(())
    }
}
