use tokio_util::sync::CancellationToken;

use crate::GatewayConfig;

pub struct SandboxGatewayServer {
    pub config: GatewayConfig,
    pub manager: sandbox_manager::SandboxManagerRouter,
    pub shutdown: CancellationToken,
}

impl SandboxGatewayServer {
    #[must_use]
    pub fn new(config: GatewayConfig, manager: sandbox_manager::SandboxManagerRouter) -> Self {
        Self::with_shutdown(config, manager, CancellationToken::new())
    }

    #[must_use]
    pub const fn with_shutdown(
        config: GatewayConfig,
        manager: sandbox_manager::SandboxManagerRouter,
        shutdown: CancellationToken,
    ) -> Self {
        Self {
            config,
            manager,
            shutdown,
        }
    }
}
