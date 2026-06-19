use operation_service::command::{CommandId, CommandRegistry, CommandServiceError};
use workspace::WorkspaceId;

fn command_id(id: &str) -> CommandId {
    CommandId(id.to_owned())
}

fn workspace_session_id(id: &str) -> WorkspaceId {
    WorkspaceId(id.to_owned())
}

#[test]
fn command_registry_binds_workspace_by_command_id() {
    let registry = CommandRegistry::new();
    let command_id = command_id("cmd_1");
    let workspace_session_id = workspace_session_id("workspace-1");

    registry
        .bind(command_id.clone(), workspace_session_id.clone())
        .expect("command bind succeeds");

    assert_eq!(
        registry.workspace_session_for(&command_id),
        Some(workspace_session_id.clone())
    );
    assert_eq!(registry.unbind(&command_id), Some(workspace_session_id));
    assert_eq!(registry.workspace_session_for(&command_id), None);
}

#[test]
fn command_registry_rejects_duplicate_command_id() {
    let registry = CommandRegistry::new();
    let command_id = command_id("cmd_1");

    registry
        .bind(command_id.clone(), workspace_session_id("workspace-1"))
        .expect("first bind succeeds");
    let error = registry
        .bind(command_id.clone(), workspace_session_id("workspace-2"))
        .expect_err("duplicate bind is rejected");

    assert!(matches!(
        error,
        CommandServiceError::DuplicateCommandId { command_id: duplicate }
            if duplicate == command_id
    ));
    assert_eq!(
        registry.workspace_session_for(&command_id),
        Some(workspace_session_id("workspace-1"))
    );
}

#[test]
fn command_registry_workspace_scan_iterates_binding_map() {
    let registry = CommandRegistry::new();
    registry
        .bind(command_id("cmd_2"), workspace_session_id("workspace-1"))
        .expect("bind succeeds");
    registry
        .bind(command_id("cmd_1"), workspace_session_id("workspace-1"))
        .expect("bind succeeds");
    registry
        .bind(command_id("cmd_3"), workspace_session_id("workspace-2"))
        .expect("bind succeeds");

    assert_eq!(
        registry.commands_for_workspace_session(&workspace_session_id("workspace-1")),
        vec![command_id("cmd_1"), command_id("cmd_2")]
    );
    assert_eq!(
        registry.commands_for_workspace_session(&workspace_session_id("workspace-2")),
        vec![command_id("cmd_3")]
    );
    assert!(registry
        .commands_for_workspace_session(&workspace_session_id("workspace-missing"))
        .is_empty());
}
