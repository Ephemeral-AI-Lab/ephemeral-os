use std::thread;
use std::time::{Duration, Instant};

use anyhow::{bail, Context, Result};
use e2e_test::{unique_suffix, NodeLease};
use protocol::catalog;
use serde_json::{json, Value};

use crate::support::{
    array, as_bool, as_i64, as_str, clean_stdout, envelope_error_kind, finalize_foreground_command,
    live_pool_or_skip, unwrap_operation_result, wait_for_active_leases, wait_for_command_count,
    wait_for_command_transcript_recycled,
};

#[test]
fn local_os_sandbox_file_ops_write_edit_and_rejection() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    let dir = format!("local-os-sandbox/{}", unique_suffix().replace('-', "_"));
    let path = format!("{dir}/nested/notes.txt");

    let created = lease.call_ok(
        catalog::SANDBOX_FILE_WRITE,
        json!({
            "path": &path,
            "content": "alpha\n",
            "overwrite": false,
        }),
    )?;
    assert_eq!(as_str(&created, "status")?, "committed", "{created}");
    assert_path_changed(&created, &path)?;

    let read = lease.call_ok(catalog::SANDBOX_FILE_READ, json!({"path": &path}))?;
    assert!(as_bool(&read, "exists")?, "{read}");
    assert_eq!(as_str(&read, "content")?, "alpha\n");

    let updated = lease.call_ok(
        catalog::SANDBOX_FILE_WRITE,
        json!({
            "path": &path,
            "content": "alpha\nbeta\nbeta\n",
            "overwrite": true,
        }),
    )?;
    assert_eq!(as_str(&updated, "status")?, "committed", "{updated}");
    assert_path_changed(&updated, &path)?;

    let edited = lease.call_ok(
        catalog::SANDBOX_FILE_EDIT,
        json!({
            "path": &path,
            "edits": [
                {"old_text": "alpha", "new_text": "omega", "replace_all": false},
                {"old_text": "beta", "new_text": "delta", "replace_all": true}
            ],
        }),
    )?;
    assert_eq!(as_str(&edited, "status")?, "committed", "{edited}");
    assert_eq!(as_i64(&edited, "applied_edits")?, 2, "{edited}");
    assert_path_changed(&edited, &path)?;

    let final_read = lease.call_ok(catalog::SANDBOX_FILE_READ, json!({"path": &path}))?;
    assert_eq!(as_str(&final_read, "content")?, "omega\ndelta\ndelta\n");

    let invalid_edit = lease.call(
        catalog::SANDBOX_FILE_EDIT,
        json!({
            "path": &path,
            "old_text": "omega",
            "new_text": "zeta",
        }),
    )?;
    assert_eq!(envelope_error_kind(&invalid_edit)?, "invalid_request");
    assert_error_contains(&invalid_edit, "edits must be a list")?;

    let missing_stack = lease.call(
        catalog::SANDBOX_FILE_WRITE,
        json!({
            "path": format!("{dir}/missing-layer-root.txt"),
            "content": "x\n",
            "layer_stack_root": Value::Null,
        }),
    )?;
    assert_eq!(envelope_error_kind(&missing_stack)?, "invalid_request");
    assert_error_contains(&missing_stack, "layer_stack_root is required")?;
    Ok(())
}

#[test]
fn local_os_sandbox_command_foreground_background_and_stdin_handoff() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;

    let foreground = lease.call_ok(
        catalog::SANDBOX_COMMAND_EXEC,
        json!({
            "cmd": "printf 'SANDBOX_FOREGROUND_OK\\n'",
            "yield_time_ms": 2000,
            "timeout_seconds": 30,
        }),
    )?;
    let foreground =
        finalize_foreground_command(&lease, foreground, Instant::now() + Duration::from_secs(30))?;
    assert_eq!(as_str(&foreground, "status")?, "ok", "{foreground}");
    assert_contains_clean_stdout(&foreground, "SANDBOX_FOREGROUND_OK")?;

    let background = lease.call_ok(
        catalog::SANDBOX_COMMAND_EXEC,
        json!({
            "cmd": concat!(
                "python3 -u -c '",
                "import time; ",
                "print(\"SANDBOX_BACKGROUND_START\", flush=True); ",
                "time.sleep(0.3); ",
                "print(\"SANDBOX_BACKGROUND_DONE\", flush=True); ",
                "time.sleep(60)'"
            ),
            "yield_time_ms": 50,
            "timeout_seconds": 120,
        }),
    )?;
    assert_eq!(as_str(&background, "status")?, "running", "{background}");
    let background_id = as_str(&background, "command_id")?.to_owned();
    let background_tail = wait_for_clean_stdout_contains(
        &lease,
        &background_id,
        &background,
        "SANDBOX_BACKGROUND_DONE",
    )?;
    assert_contains_clean_stdout(&background_tail, "SANDBOX_BACKGROUND_START")?;
    cancel_and_drain(&lease, &background_id)?;

    let auth = lease.call_ok(
        catalog::SANDBOX_COMMAND_EXEC,
        json!({
            "cmd": concat!(
                "python3 -u -c '",
                "import sys; ",
                "print(\"SANDBOX_AUTH_LINK:command://fetch-code\", flush=True); ",
                "print(\"SANDBOX_AUTH_PROMPT:enter-code\", flush=True); ",
                "code=sys.stdin.readline().strip(); ",
                "print(\"SANDBOX_AUTH_SUCCESS:\" + code if code == \"482917\" else \"SANDBOX_AUTH_FAIL:\" + code, flush=True)'"
            ),
            "yield_time_ms": 100,
            "timeout_seconds": 120,
        }),
    )?;
    assert_eq!(as_str(&auth, "status")?, "running", "{auth}");
    let auth_id = as_str(&auth, "command_id")?.to_owned();
    wait_for_clean_stdout_contains(&lease, &auth_id, &auth, "SANDBOX_AUTH_PROMPT:enter-code")?;

    let fetched = lease.call_ok(
        catalog::SANDBOX_COMMAND_EXEC,
        json!({
            "cmd": "printf 'SANDBOX_AUTH_CODE:482917\\n'",
            "yield_time_ms": 2000,
            "timeout_seconds": 30,
        }),
    )?;
    let fetched =
        finalize_foreground_command(&lease, fetched, Instant::now() + Duration::from_secs(30))?;
    assert_eq!(as_str(&fetched, "status")?, "ok", "{fetched}");
    let code = marker_value(&fetched, "SANDBOX_AUTH_CODE:")?;

    let answered = lease.call_ok(
        catalog::SANDBOX_COMMAND_WRITE_STDIN,
        json!({
            "command_id": &auth_id,
            "chars": format!("{code}\n"),
            "yield_time_ms": 2000,
        }),
    )?;
    let answered =
        finalize_foreground_command(&lease, answered, Instant::now() + Duration::from_secs(30))?;
    assert_eq!(as_str(&answered, "status")?, "ok", "{answered}");
    assert_contains_clean_stdout(&answered, "SANDBOX_AUTH_SUCCESS:482917")?;
    wait_for_command_count(&lease, 0)?;
    wait_for_active_leases(&lease, 0)?;
    Ok(())
}

#[test]
fn local_os_sandbox_command_cancel_and_control_bytes() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;

    let cancel_id = start_waiting_command(&lease, "SANDBOX_CANCEL_READY")?;
    let cancelled = cancel_and_drain(&lease, &cancel_id)?;
    assert_terminalish_cancel(&cancelled)?;

    for (label, control) in [("CTRL_C", "\u{3}"), ("CTRL_D", "\u{4}")] {
        let marker = format!("SANDBOX_{label}_READY");
        let id = start_waiting_command(&lease, &marker)?;
        let response = unwrap_operation_result(lease.call(
            catalog::SANDBOX_COMMAND_WRITE_STDIN,
            json!({
                "command_id": &id,
                "chars": control,
                "yield_time_ms": 3000,
            }),
        )?)?;
        assert_eq!(
            as_str(&response, "status")?,
            "cancelled",
            "{label} should route to command cancellation: {response}"
        );
        wait_for_command_count(&lease, 0)?;
        wait_for_active_leases(&lease, 0)?;
        wait_for_command_transcript_recycled(&lease, &id)?;
    }
    Ok(())
}

#[test]
fn local_os_sandbox_http_server_is_observed_and_cancelled() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;

    let server = lease.call_ok(
        catalog::SANDBOX_COMMAND_EXEC,
        json!({
            "cmd": r#"python3 -u - <<'PY'
import http.server
import socketserver

class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = b"SANDBOX_HTTP_RESPONSE\n"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass

server = socketserver.TCPServer(("127.0.0.1", 0), Handler)
print(f"SANDBOX_HTTP_PORT:{server.server_address[1]}", flush=True)
server.serve_forever()
PY"#,
            "yield_time_ms": 500,
            "timeout_seconds": 120,
        }),
    )?;
    assert_eq!(as_str(&server, "status")?, "running", "{server}");
    let server_id = as_str(&server, "command_id")?.to_owned();
    let server_ready =
        wait_for_clean_stdout_contains(&lease, &server_id, &server, "SANDBOX_HTTP_PORT:")?;
    let port = marker_value(&server_ready, "SANDBOX_HTTP_PORT:")?;

    let fetch = lease.call_ok(
        catalog::SANDBOX_COMMAND_EXEC,
        json!({
            "cmd": format!(
                "python3 - <<'PY'\nimport urllib.request\nprint(urllib.request.urlopen('http://127.0.0.1:{port}', timeout=5).read().decode().strip())\nPY"
            ),
            "yield_time_ms": 2000,
            "timeout_seconds": 30,
        }),
    )?;
    let fetch =
        finalize_foreground_command(&lease, fetch, Instant::now() + Duration::from_secs(30))?;
    assert_eq!(as_str(&fetch, "status")?, "ok", "{fetch}");
    assert_contains_clean_stdout(&fetch, "SANDBOX_HTTP_RESPONSE")?;

    let cancelled = cancel_and_drain(&lease, &server_id)?;
    assert_terminalish_cancel(&cancelled)?;
    Ok(())
}

fn assert_path_changed(response: &Value, expected: &str) -> Result<()> {
    assert!(
        array(response, "changed_paths")?
            .iter()
            .any(|path| path.as_str() == Some(expected)),
        "response should include changed path {expected}: {response}"
    );
    Ok(())
}

fn assert_error_contains(response: &Value, expected: &str) -> Result<()> {
    let message = response
        .get("error")
        .and_then(|error| error.get("message"))
        .and_then(Value::as_str)
        .context("error message")?;
    assert!(
        message.contains(expected),
        "error message should contain {expected:?}: {response}"
    );
    Ok(())
}

fn assert_contains_clean_stdout(response: &Value, needle: &str) -> Result<()> {
    let output = clean_stdout(response);
    assert!(
        output.contains(needle),
        "stdout should contain {needle:?}: {response}"
    );
    Ok(())
}

fn wait_for_clean_stdout_contains(
    lease: &NodeLease<'_>,
    command_id: &str,
    initial: &Value,
    needle: &str,
) -> Result<Value> {
    if clean_stdout(initial).contains(needle) {
        return Ok(initial.clone());
    }
    let deadline = Instant::now() + Duration::from_secs(15);
    let mut last = initial.clone();
    loop {
        let progress = lease.call_ok(
            catalog::SANDBOX_COMMAND_POLL,
            json!({
                "command_id": command_id,
                "last_n_lines": 100,
            }),
        )?;
        if clean_stdout(&progress).contains(needle) {
            return Ok(progress);
        }
        if Instant::now() >= deadline {
            bail!("command {command_id} did not surface {needle:?}; last: {last}");
        }
        last = progress;
        thread::sleep(Duration::from_millis(50));
    }
}

fn marker_value(response: &Value, prefix: &str) -> Result<String> {
    for line in clean_stdout(response).lines() {
        if let Some(value) = line.trim().strip_prefix(prefix) {
            if !value.trim().is_empty() {
                return Ok(value.trim().to_owned());
            }
        }
    }
    bail!("stdout did not include marker prefix {prefix:?}: {response}")
}

fn start_waiting_command(lease: &NodeLease<'_>, marker: &str) -> Result<String> {
    let command = lease.call_ok(
        catalog::SANDBOX_COMMAND_EXEC,
        json!({
            "cmd": format!("python3 -u -c 'import time; print({marker:?}, flush=True); time.sleep(60)'"),
            "yield_time_ms": 500,
            "timeout_seconds": 120,
        }),
    )?;
    assert_eq!(as_str(&command, "status")?, "running", "{command}");
    let id = as_str(&command, "command_id")?.to_owned();
    wait_for_clean_stdout_contains(lease, &id, &command, marker)?;
    Ok(id)
}

fn cancel_and_drain(lease: &NodeLease<'_>, command_id: &str) -> Result<Value> {
    let cancelled = unwrap_operation_result(lease.call(
        catalog::SANDBOX_COMMAND_CANCEL,
        json!({"command_id": command_id}),
    )?)?;
    wait_for_command_count(lease, 0)?;
    wait_for_active_leases(lease, 0)?;
    wait_for_command_transcript_recycled(lease, command_id)?;
    Ok(cancelled)
}

fn assert_terminalish_cancel(response: &Value) -> Result<()> {
    assert!(
        matches!(as_str(response, "status")?, "cancelled" | "ok" | "error"),
        "cancel should return a terminal-ish command status: {response}"
    );
    Ok(())
}
