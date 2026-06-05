/// Concrete isolated workspace capability implementation.
#[derive(Debug, Clone)]
pub struct IsolatedWorkspaceOps<P> {
    ports: P,
}

impl<P> IsolatedWorkspaceOps<P> {
    #[must_use]
    pub fn new(ports: P) -> Self {
        Self { ports }
    }

    #[must_use]
    pub const fn ports(&self) -> &P {
        &self.ports
    }
}
