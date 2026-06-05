/// Concrete ephemeral workspace capability implementation.
#[derive(Debug, Clone)]
pub struct EphemeralWorkspaceOps<P> {
    ports: P,
}

impl<P> EphemeralWorkspaceOps<P> {
    #[must_use]
    pub fn new(ports: P) -> Self {
        Self { ports }
    }

    #[must_use]
    pub const fn ports(&self) -> &P {
        &self.ports
    }
}
