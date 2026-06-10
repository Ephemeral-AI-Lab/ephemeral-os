//! Concrete publish-capable ephemeral workspace API implementation.

use crate::contract::{
    EditFileOutcome, EditFileRequest, ReadFileOutcome, ReadFileRequest, WorkspaceApiError,
    WorkspaceFileOps, WorkspaceMode, WorkspaceMutationSink, WorkspaceReadView, WriteFileOutcome,
    WriteFileRequest,
};

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

impl<P> WorkspaceFileOps for EphemeralWorkspaceOps<P>
where
    P: WorkspaceReadView + WorkspaceMutationSink,
{
    fn read_file(&self, request: ReadFileRequest) -> Result<ReadFileOutcome, WorkspaceApiError> {
        crate::contract::file_ops::read_file(self.ports(), WorkspaceMode::Ephemeral, request)
    }

    fn write_file(&self, request: WriteFileRequest) -> Result<WriteFileOutcome, WorkspaceApiError> {
        crate::contract::file_ops::write_file(
            self.ports(),
            WorkspaceMode::Ephemeral,
            "api_write",
            request,
        )
    }

    fn edit_file(&self, request: EditFileRequest) -> Result<EditFileOutcome, WorkspaceApiError> {
        crate::contract::file_ops::edit_file(
            self.ports(),
            WorkspaceMode::Ephemeral,
            "api_edit",
            request,
        )
    }
}

#[cfg(test)]
mod tests;
