use std::collections::HashMap;
use std::sync::{Mutex, MutexGuard};

use crate::command::{CommandId, CommandServiceError};
use crate::workspace_crate::WorkspaceId;

#[derive(Debug, Default)]
pub struct CommandRegistry {
    command_workspace: Mutex<HashMap<CommandId, WorkspaceId>>,
}

impl CommandRegistry {
    #[must_use]
    pub fn new() -> Self {
        Self::default()
    }

    pub fn bind(
        &self,
        command_id: CommandId,
        workspace_id: WorkspaceId,
    ) -> Result<(), CommandServiceError> {
        let mut command_workspace = lock(&self.command_workspace);
        if command_workspace.contains_key(&command_id) {
            return Err(CommandServiceError::DuplicateCommandId { command_id });
        }

        command_workspace.insert(command_id, workspace_id);
        Ok(())
    }

    #[must_use]
    pub fn workspace_for(&self, command_id: &CommandId) -> Option<WorkspaceId> {
        lock(&self.command_workspace).get(command_id).cloned()
    }

    #[must_use]
    pub fn unbind(&self, command_id: &CommandId) -> Option<WorkspaceId> {
        lock(&self.command_workspace).remove(command_id)
    }

    #[must_use]
    pub fn commands_for_workspace(&self, workspace_id: &WorkspaceId) -> Vec<CommandId> {
        let mut command_ids = lock(&self.command_workspace)
            .iter()
            .filter(|(_, bound_workspace_id)| *bound_workspace_id == workspace_id)
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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn command_registry_contains_only_binding_map() {
        let CommandRegistry { command_workspace } = CommandRegistry::new();

        assert!(lock(&command_workspace).is_empty());
    }
}
