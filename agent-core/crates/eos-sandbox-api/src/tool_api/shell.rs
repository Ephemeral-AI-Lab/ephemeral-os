//! Pure `shell` helper (legacy `api.v1.shell` op). Two behaviors beyond the
//! plain build→call→parse flow: a `stdin`-present request short-circuits to a
//! rejected result **before** any transport call (invariant 5), and a classified
//! shell conflict transport error maps to `Ok(result{success:false,…})`
//! (invariant 4). The raw-argv rejection is unrepresentable here (`command` is a
//! `String`), so only the stdin guard survives.

use eos_types::SandboxId;
use serde_json::Value;

use crate::error::SandboxApiError;
use crate::models::{ConflictInfo, SandboxResultBase, ShellRequest, ShellResult, Workspace};
use crate::ops::DaemonOp;
use crate::timeouts::shell_dispatch_timeout;
use crate::tool_api::parse::{
    daemon_request_identity_fields, is_shell_conflict, parse_shell_result,
    user_visible_error_message,
};
use crate::transport::SandboxTransport;

/// Run a shell command through sandbox-local overlay and OCC.
pub async fn shell(
    transport: &dyn SandboxTransport,
    sandbox_id: &SandboxId,
    request: &ShellRequest,
) -> Result<ShellResult, SandboxApiError> {
    if request.stdin.is_some() {
        return Ok(rejected_shell_result(
            "stdin_not_supported",
            "snapshot overlay shell does not accept stdin",
        ));
    }

    let cwd = {
        let trimmed = request.cwd.as_deref().unwrap_or("").trim();
        if trimmed.is_empty() {
            ".".to_owned()
        } else {
            trimmed.to_owned()
        }
    };

    let mut payload = daemon_request_identity_fields(&request.base);
    payload.insert("command".to_owned(), Value::String(request.command.clone()));
    payload.insert("cwd".to_owned(), Value::String(cwd));
    // Always present, null when absent (the daemon reads `timeout_seconds`).
    payload.insert(
        "timeout_seconds".to_owned(),
        request.timeout.map_or(Value::Null, Value::from),
    );
    payload.insert(
        "description".to_owned(),
        Value::String(request.base.description_or("shell")),
    );
    if request.background {
        payload.insert("background".to_owned(), Value::Bool(true));
    }

    match transport
        .call(
            sandbox_id,
            DaemonOp::Shell,
            payload,
            shell_dispatch_timeout(request.timeout),
        )
        .await
    {
        Ok(response) => parse_shell_result(&response),
        Err(error) => match shell_conflict_result(&error) {
            Some(result) => Ok(result),
            None => Err(error),
        },
    }
}

/// A rejected shell result (stdin guard). Status `error`, exit code `1`.
fn rejected_shell_result(reason: &str, message: &str) -> ShellResult {
    ShellResult {
        base: SandboxResultBase {
            success: false,
            workspace: Workspace::Ephemeral,
            timings: Default::default(),
            conflict: Some(ConflictInfo::rejected(reason, message)),
            conflict_reason: Some(message.to_owned()),
            changed_paths: Vec::new(),
            error: None,
        },
        changed_path_kinds: Default::default(),
        mutation_source: String::new(),
        status: "error".to_owned(),
        exit_code: 1,
        stdout: String::new(),
        stderr: String::new(),
        warnings: Vec::new(),
    }
}

/// Map a classified shell-conflict transport error into a recoverable result
/// (mirrors `shell.py::_conflict_from_error`); `None` for any other error.
fn shell_conflict_result(error: &SandboxApiError) -> Option<ShellResult> {
    if !is_shell_conflict(error) {
        return None;
    }
    let message = user_visible_error_message(error.message()).to_owned();
    Some(ShellResult {
        base: SandboxResultBase {
            success: false,
            workspace: Workspace::Ephemeral,
            timings: Default::default(),
            conflict: Some(ConflictInfo::rejected("rejected", message.clone())),
            conflict_reason: Some(message),
            changed_paths: Vec::new(),
            error: None,
        },
        changed_path_kinds: Default::default(),
        mutation_source: String::new(),
        status: "rejected".to_owned(),
        exit_code: 1,
        stdout: String::new(),
        stderr: String::new(),
        warnings: Vec::new(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::models::{SandboxCaller, SandboxRequestBase};
    use crate::transport::mock::MockTransport;

    fn request() -> ShellRequest {
        ShellRequest {
            base: SandboxRequestBase {
                caller: SandboxCaller {
                    agent_id: "agent-1".to_owned(),
                    run_id: String::new(),
                    agent_run_id: String::new(),
                    task_id: String::new(),
                    request_id: String::new(),
                    attempt_id: String::new(),
                    workflow_id: String::new(),
                    tool_id: None,
                },
                description: String::new(),
                invocation_id: None,
            },
            command: "ls".to_owned(),
            cwd: None,
            timeout: None,
            stdin: None,
            background: false,
        }
    }

    // AC-sandbox-api-05: shell rejects stdin without calling the transport.
    #[tokio::test]
    async fn shell_rejects_stdin_without_calling_transport() {
        let transport = MockTransport::ok(serde_json::Map::new());
        let sandbox: SandboxId = "sandbox-1".parse().expect("non-empty");
        let mut req = request();
        req.stdin = Some("data".to_owned());
        let result = shell(&transport, &sandbox, &req)
            .await
            .expect("rejected result is Ok");
        assert!(!result.base.success);
        assert_eq!(result.status, "error");
        assert_eq!(result.exit_code, 1);
        assert_eq!(
            result.base.conflict.expect("conflict").reason,
            "stdin_not_supported"
        );
        assert_eq!(transport.calls(), 0, "transport must not be called");
    }

    // AC-sandbox-api-05: a classified shell conflict maps to Ok.
    #[tokio::test]
    async fn shell_conflict_error_maps_to_ok_result() {
        let transport = MockTransport::err(SandboxApiError::transport(
            Some("overlay_escape".to_owned()),
            "internal_error: blocked",
        ));
        let sandbox: SandboxId = "sandbox-1".parse().expect("non-empty");
        let result = shell(&transport, &sandbox, &request())
            .await
            .expect("conflict maps to Ok");
        assert!(!result.base.success);
        assert_eq!(result.status, "rejected");
        assert_eq!(result.base.conflict.expect("conflict").reason, "rejected");
        assert_eq!(result.base.conflict_reason.as_deref(), Some("blocked"));
    }

    // AC-sandbox-api-05: a non-conflict transport error propagates.
    #[tokio::test]
    async fn shell_non_conflict_error_propagates() {
        let transport = MockTransport::err(SandboxApiError::transport(None, "kaboom"));
        let sandbox: SandboxId = "sandbox-1".parse().expect("non-empty");
        assert!(shell(&transport, &sandbox, &request()).await.is_err());
    }
}
