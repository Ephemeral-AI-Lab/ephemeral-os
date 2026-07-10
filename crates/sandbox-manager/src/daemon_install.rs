use crate::{
    ManagerError, ProgressSink, SandboxDaemonEndpoint, SandboxHttpEndpoint, SandboxRecord,
};

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct StartedDaemon {
    pub daemon: SandboxDaemonEndpoint,
    pub daemon_http: Option<SandboxHttpEndpoint>,
}

pub trait SandboxDaemonInstaller: Send + Sync {
    fn install_daemon(&self, record: &SandboxRecord) -> Result<(), ManagerError>;

    fn start_daemon(&self, record: &SandboxRecord) -> Result<StartedDaemon, ManagerError>;

    fn stop_daemon(&self, record: &SandboxRecord) -> Result<(), ManagerError>;

    fn check_daemon(
        &self,
        record: &SandboxRecord,
        endpoint: &SandboxDaemonEndpoint,
    ) -> Result<(), ManagerError>;

    fn check_daemon_with_progress(
        &self,
        record: &SandboxRecord,
        endpoint: &SandboxDaemonEndpoint,
        _progress: &ProgressSink,
    ) -> Result<(), ManagerError> {
        self.check_daemon(record, endpoint)
    }
}
