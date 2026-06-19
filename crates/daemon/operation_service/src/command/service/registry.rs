use std::collections::HashMap;
use std::sync::{Mutex, MutexGuard};

use crate::command::{CommandId, CommandServiceError};
use crate::workspace_crate::WorkspaceId;

#[derive(Debug, Default)]
pub struct CommandRegistry {
    command_workspace_session: Mutex<HashMap<CommandId, WorkspaceId>>,
}

impl CommandRegistry {
    #[must_use]
    pub fn new() -> Self {
        Self::default()
    }

    pub fn bind(
        &self,
        command_id: CommandId,
        workspace_session_id: WorkspaceId,
    ) -> Result<(), CommandServiceError> {
        let mut command_workspace_session = lock(&self.command_workspace_session);
        if command_workspace_session.contains_key(&command_id) {
            return Err(CommandServiceError::DuplicateCommandId { command_id });
        }

        command_workspace_session.insert(command_id, workspace_session_id);
        Ok(())
    }

    #[must_use]
    pub fn workspace_session_for(&self, command_id: &CommandId) -> Option<WorkspaceId> {
        lock(&self.command_workspace_session)
            .get(command_id)
            .cloned()
    }

    #[must_use]
    pub fn unbind(&self, command_id: &CommandId) -> Option<WorkspaceId> {
        lock(&self.command_workspace_session).remove(command_id)
    }

    #[must_use]
    pub fn commands_for_workspace_session(
        &self,
        workspace_session_id: &WorkspaceId,
    ) -> Vec<CommandId> {
        let mut command_ids = lock(&self.command_workspace_session)
            .iter()
            .filter(|(_, bound_workspace_session_id)| {
                *bound_workspace_session_id == workspace_session_id
            })
            .map(|(command_id, _)| command_id.clone())
            .collect::<Vec<_>>();
        command_ids.sort();
        command_ids
    }
}

fn lock<T>(mutex: &Mutex<T>) -> MutexGuard<'_, T> {
    mutex
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner)
}
