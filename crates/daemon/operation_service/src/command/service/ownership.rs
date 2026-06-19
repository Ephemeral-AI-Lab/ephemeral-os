use super::core::CommandOperationService;

use crate::command::{CommandServiceError, CompletedCommandRecord, FinalizationState};
use crate::workspace_crate::CallerId;

impl CommandOperationService {
    pub(crate) fn active_for_owner<'a>(
        &'a self,
        command_id: &crate::command::CommandId,
        caller_id: &CallerId,
    ) -> Result<crate::command::ActiveCommandRef<'a>, CommandServiceError> {
        match self.active_for_owner_or_none(command_id, caller_id)? {
            Some(active) => Ok(active),
            None => match self.process_store().completed(command_id) {
                Some(completed) if completed.caller_id == *caller_id => {
                    Err(CommandServiceError::CommandAlreadyCompleted {
                        command_id: command_id.clone(),
                    })
                }
                Some(completed) => Err(CommandServiceError::CommandCallerMismatch {
                    command_id: command_id.clone(),
                    expected: completed.caller_id,
                    actual: caller_id.clone(),
                }),
                None => Err(CommandServiceError::CommandNotFound {
                    command_id: command_id.clone(),
                }),
            },
        }
    }

    pub(crate) fn active_for_owner_or_none<'a>(
        &'a self,
        command_id: &crate::command::CommandId,
        caller_id: &CallerId,
    ) -> Result<Option<crate::command::ActiveCommandRef<'a>>, CommandServiceError> {
        let Some(active) = self.process_store().active(command_id) else {
            return Ok(None);
        };
        if active.caller_id != *caller_id {
            return Err(CommandServiceError::CommandCallerMismatch {
                command_id: command_id.clone(),
                expected: active.caller_id.clone(),
                actual: caller_id.clone(),
            });
        }
        if let FinalizationState::Failed { error, finalized } = &active.finalization {
            return Err(CommandServiceError::CommandFinalizationFailed {
                command_id: command_id.clone(),
                error: error.clone(),
                finalized: finalized.clone().map(Box::new),
            });
        }
        Ok(Some(active))
    }

    pub(crate) fn completed_for_owner(
        &self,
        command_id: &crate::command::CommandId,
        caller_id: &CallerId,
    ) -> Result<CompletedCommandRecord, CommandServiceError> {
        let completed = self.process_store().completed(command_id).ok_or_else(|| {
            CommandServiceError::CommandNotFound {
                command_id: command_id.clone(),
            }
        })?;
        if completed.caller_id == *caller_id {
            Ok(completed)
        } else {
            Err(CommandServiceError::CommandCallerMismatch {
                command_id: command_id.clone(),
                expected: completed.caller_id,
                actual: caller_id.clone(),
            })
        }
    }

    pub(crate) fn ensure_active_owner(
        &self,
        command_id: &crate::command::CommandId,
        caller_id: &CallerId,
    ) -> Result<(), CommandServiceError> {
        drop(self.active_for_owner(command_id, caller_id)?);
        Ok(())
    }
}
