use std::path::PathBuf;

use anyhow::Result;
use serde_json::json;

use super::*;

fn options() -> ClientOptions {
    ClientOptions {
        socket: PathBuf::from("/tmp/test.sock"),
        operator: false,
        envelope: false,
        sandbox_id: None,
    }
}

fn daemon_options() -> ClientOptions {
    ClientOptions {
        sandbox_id: Some("sb-1".to_owned()),
        ..options()
    }
}

#[test]
fn host_image_list_routes_to_operator() -> Result<()> {
    let request = request_from_host(vec!["images".to_owned(), "list".to_owned()], &options())?;

    assert_eq!(request.op, "host.image.list");
    assert!(request.operator);
    Ok(())
}

#[test]
fn host_container_stop_can_target_sandbox_id() -> Result<()> {
    let request = request_from_host(
        vec!["containers".to_owned(), "stop".to_owned()],
        &ClientOptions {
            sandbox_id: Some("sb-stop".to_owned()),
            ..options()
        },
    )?;

    assert_eq!(request.op, "host.container.stop");
    assert!(request.operator);
    assert_eq!(request.args["sandbox_id"], json!("sb-stop"));
    Ok(())
}

#[test]
fn host_sandbox_acquire_accepts_image_profile_and_workspace_root() -> Result<()> {
    let request = request_from_host(
        vec![
            "sandboxes".to_owned(),
            "acquire".to_owned(),
            "--image-profile".to_owned(),
            "default".to_owned(),
            "--workspace-root".to_owned(),
            "/workspace".to_owned(),
        ],
        &options(),
    )?;

    assert_eq!(request.op, "host.sandbox.acquire");
    assert_eq!(request.args["image_profile"], json!("default"));
    assert_eq!(request.args["workspace_root"], json!("/workspace"));
    assert!(request.sandbox_id.is_none());
    assert!(!request.operator);
    Ok(())
}

#[test]
fn daemon_command_exec_uses_canonical_operation_name() -> Result<()> {
    let request = request_from_daemon(
        vec![
            "commands".to_owned(),
            "exec".to_owned(),
            "--workspace-root".to_owned(),
            "/testbed".to_owned(),
            "--".to_owned(),
            "pwd".to_owned(),
        ],
        &daemon_options(),
    )?;

    assert_eq!(request.op, "exec_command");
    assert_eq!(request.sandbox_id.as_deref(), Some("sb-1"));
    assert_eq!(request.args["cmd"], json!("pwd"));
    assert_eq!(request.args["workspace_root"], json!("/testbed"));
    assert!(!request.operator);
    Ok(())
}

#[test]
fn generic_daemon_op_accepts_json_args_and_sandbox() -> Result<()> {
    let request = request_from_daemon(
        vec![
            "op".to_owned(),
            "read_command_lines".to_owned(),
            r#"{"command_session_id":"cmd-1","start_offset":0,"limit":10}"#.to_owned(),
        ],
        &daemon_options(),
    )?;

    assert_eq!(request.op, "read_command_lines");
    assert_eq!(request.sandbox_id.as_deref(), Some("sb-1"));
    assert_eq!(request.args["command_session_id"], json!("cmd-1"));
    assert_eq!(request.args["start_offset"], json!(0));
    assert_eq!(request.args["limit"], json!(10));
    Ok(())
}
