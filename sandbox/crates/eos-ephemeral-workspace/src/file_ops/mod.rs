//! Direct file APIs for the publish-capable ephemeral workspace mode.

mod edit;
mod read;
mod response;
mod write;

use eos_workspace_api::{
    EditFileOutcome, EditFileRequest, ReadFileOutcome, ReadFileRequest, WorkspaceApiError,
    WorkspaceFileOps, WorkspaceMutationSink, WorkspaceReadView, WriteFileOutcome, WriteFileRequest,
};

use crate::ops::EphemeralWorkspaceOps;

impl<P> WorkspaceFileOps for EphemeralWorkspaceOps<P>
where
    P: WorkspaceReadView + WorkspaceMutationSink,
{
    fn read_file(&self, request: ReadFileRequest) -> Result<ReadFileOutcome, WorkspaceApiError> {
        read::read_file(self.ports(), request)
    }

    fn write_file(&self, request: WriteFileRequest) -> Result<WriteFileOutcome, WorkspaceApiError> {
        write::write_file(self.ports(), request)
    }

    fn edit_file(&self, request: EditFileRequest) -> Result<EditFileOutcome, WorkspaceApiError> {
        edit::edit_file(self.ports(), request)
    }
}
