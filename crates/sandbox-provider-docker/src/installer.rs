//! `SandboxDaemonInstaller` over bollard: upload daemon assets into the stopped
//! container, start it, gate readiness with an authenticated daemon request, and
//! best-effort stop it. Removal stays with the runtime's `destroy_sandbox`.

use std::collections::HashSet;
use std::io::{BufRead as _, BufReader, Write as _};
use std::net::{Shutdown, TcpStream};
use std::time::{Duration, Instant};

use sandbox_config::configs::manager::DockerRuntimeConfig;
use sandbox_manager::{
    ManagerError, ProgressSink, SandboxDaemonEndpoint, SandboxDaemonInstaller, SandboxRecord,
};

use crate::archive::build_install_archive;
use crate::engine::{DockerEngine, DockerError};
use crate::readiness::{readiness_request_line, validate_readiness_response};

const ARCHIVE_ROOT: &str = "/";
const STOP_TIMEOUT_SECS: i64 = 5;
const READINESS_POLL: Duration = Duration::from_millis(250);
const READINESS_IO_TIMEOUT: Duration = Duration::from_millis(250);

/// Docker-backed daemon installer.
pub struct DockerSandboxDaemonInstaller {
    engine: DockerEngine,
}

impl DockerSandboxDaemonInstaller {
    /// Build an installer from the resolved Docker config.
    #[must_use]
    pub fn new(config: DockerRuntimeConfig) -> Self {
        Self {
            engine: DockerEngine::new(config),
        }
    }
}

impl SandboxDaemonInstaller for DockerSandboxDaemonInstaller {
    fn install_daemon(&self, record: &SandboxRecord) -> Result<(), ManagerError> {
        let config = self.engine.config();
        let daemon_binary = std::fs::read(&config.daemon_binary_path).map_err(|error| {
            daemon_install_failed(format!(
                "read daemon binary {}: {error}",
                config.daemon_binary_path.display()
            ))
        })?;
        let config_yaml = std::fs::read(&config.daemon_config_yaml_path).map_err(|error| {
            daemon_install_failed(format!(
                "read daemon config {}: {error}",
                config.daemon_config_yaml_path.display()
            ))
        })?;
        let archive = build_install_archive(
            &config.container_daemon_binary_path,
            &daemon_binary,
            &config.container_daemon_config_yaml_path,
            &config_yaml,
        )
        .map_err(|error| daemon_install_failed(format!("build install archive: {error}")))?;
        self.engine
            .upload_archive(
                record.id.as_str().to_owned(),
                ARCHIVE_ROOT.to_owned(),
                archive,
            )
            .map_err(install_error)
    }

    fn start_daemon(&self, record: &SandboxRecord) -> Result<SandboxDaemonEndpoint, ManagerError> {
        let daemon_port = self.engine.config().daemon_port;
        let started = self
            .engine
            .start_and_resolve(record.id.as_str().to_owned(), daemon_port)
            .map_err(|error| {
                let context = self
                    .engine
                    .capture_failure_context(record.id.as_str().to_owned());
                daemon_install_failed(format!(
                    "start daemon for {}: {error}; container {context}",
                    record.id
                ))
            })?;
        Ok(SandboxDaemonEndpoint::new(
            "127.0.0.1",
            started.port,
            started.auth_token,
        ))
    }

    fn stop_daemon(&self, record: &SandboxRecord) -> Result<(), ManagerError> {
        self.engine
            .stop_container(record.id.as_str().to_owned(), STOP_TIMEOUT_SECS)
            .map_err(install_error)
    }

    fn check_daemon(
        &self,
        record: &SandboxRecord,
        endpoint: &SandboxDaemonEndpoint,
    ) -> Result<(), ManagerError> {
        let timeout = Duration::from_millis(self.engine.config().readiness_timeout_ms);
        let sandbox_id = record.id.as_str();
        let mut seen_logs = HashSet::new();
        poll_until_ready_with_progress(endpoint, sandbox_id, timeout, || {
            fail_on_daemon_logs(
                &self.engine.capture_logs(sandbox_id.to_owned()),
                &mut seen_logs,
                None,
            )?;
            fail_if_container_exited(&self.engine, sandbox_id)
        })
        .map_err(|error| {
            readiness_failed(
                &error,
                readiness_failure_message(endpoint, sandbox_id, &error, &self.engine),
            )
        })
    }

    fn check_daemon_with_progress(
        &self,
        record: &SandboxRecord,
        endpoint: &SandboxDaemonEndpoint,
        progress: &ProgressSink,
    ) -> Result<(), ManagerError> {
        let timeout = Duration::from_millis(self.engine.config().readiness_timeout_ms);
        let sandbox_id = record.id.as_str();
        let mut seen_logs = HashSet::new();
        poll_until_ready_with_progress(endpoint, sandbox_id, timeout, || {
            fail_on_daemon_logs(
                &self.engine.capture_logs(sandbox_id.to_owned()),
                &mut seen_logs,
                Some(progress),
            )?;
            fail_if_container_exited(&self.engine, sandbox_id)
        })
        .map_err(|error| {
            readiness_failed(
                &error,
                readiness_failure_message(endpoint, sandbox_id, &error, &self.engine),
            )
        })
    }
}

/// Poll the published port with an authenticated, sandbox-scoped readiness
/// request until the daemon confirms it is ready for this sandbox (a bare TCP
/// connect through Docker's proxy is not a reliable readiness signal), or the
/// deadline elapses.
fn poll_until_ready_with_progress<F>(
    endpoint: &SandboxDaemonEndpoint,
    sandbox_id: &str,
    timeout: Duration,
    mut on_poll: F,
) -> Result<(), String>
where
    F: FnMut() -> Result<(), String>,
{
    let request_line = readiness_request_line(sandbox_id, &endpoint.auth_token);
    let deadline = Instant::now() + timeout;
    loop {
        on_poll()?;
        let error = match authenticated_exchange(
            &endpoint.host,
            endpoint.port,
            &request_line,
            sandbox_id,
        ) {
            Ok(()) => return Ok(()),
            Err(error) => error,
        };
        on_poll()?;
        if Instant::now() >= deadline {
            return Err(format!(
                "timed out after {} ms: {error}",
                timeout.as_millis()
            ));
        }
        std::thread::sleep(READINESS_POLL);
    }
}

fn fail_if_container_exited(engine: &DockerEngine, sandbox_id: &str) -> Result<(), String> {
    if let Some(reason) = engine
        .container_exit_reason(sandbox_id.to_owned())
        .map_err(|error| error.to_string())?
    {
        return Err(reason);
    }
    Ok(())
}

fn readiness_failure_message(
    endpoint: &SandboxDaemonEndpoint,
    sandbox_id: &str,
    error: &str,
    engine: &DockerEngine,
) -> String {
    if is_concise_daemon_failure(error) {
        return format!(
            "{error} (sandbox {sandbox_id}, daemon {}:{})",
            endpoint.host, endpoint.port
        );
    }
    let context = engine.capture_failure_context(sandbox_id.to_owned());
    format!(
        "daemon at {}:{} for {sandbox_id} is not ready: {error}; container {context}",
        endpoint.host, endpoint.port
    )
}

fn readiness_failed(error: &str, message: String) -> ManagerError {
    if is_workspace_setup_failure(error) {
        ManagerError::WorkspaceSetupFailed { message }
    } else {
        daemon_install_failed(message)
    }
}

fn is_workspace_setup_failure(message: &str) -> bool {
    is_fatal_daemon_log(message)
}

fn fail_on_daemon_logs(
    logs: &str,
    seen_logs: &mut HashSet<String>,
    progress: Option<&ProgressSink>,
) -> Result<(), String> {
    for line in logs.lines().map(str::trim).filter(|line| !line.is_empty()) {
        if !seen_logs.insert(line.to_owned()) {
            continue;
        }
        if let Some(message) = parse_cli_log(line) {
            if let Some(progress) = progress {
                progress.emit(&message);
            }
            if is_fatal_daemon_log(&message) {
                return Err(message);
            }
        } else if line.contains("panicked at ") {
            return Err(line.to_owned());
        }
    }
    Ok(())
}

fn parse_cli_log(line: &str) -> Option<String> {
    let encoded = line.strip_prefix("cli_log(")?.strip_suffix(')')?;
    serde_json::from_str(encoded).ok()
}

fn is_fatal_daemon_log(message: &str) -> bool {
    message.starts_with("layer-stack ")
        || message.starts_with("manifest error:")
        || message.starts_with("file too large:")
        || message.starts_with("could not allocate ")
        || message.starts_with("active manifest changed:")
        || message.starts_with("invalid lease owner:")
}

fn is_concise_daemon_failure(message: &str) -> bool {
    is_fatal_daemon_log(message) || message.contains("panicked at ")
}

fn authenticated_exchange(
    host: &str,
    port: u16,
    request_line: &[u8],
    expected_sandbox_id: &str,
) -> Result<(), String> {
    let mut stream =
        TcpStream::connect((host, port)).map_err(|error| format!("connect: {error}"))?;
    stream.set_read_timeout(Some(READINESS_IO_TIMEOUT)).ok();
    stream.set_write_timeout(Some(READINESS_IO_TIMEOUT)).ok();
    stream
        .write_all(request_line)
        .map_err(|error| format!("write: {error}"))?;
    stream.shutdown(Shutdown::Write).ok();
    let mut reader = BufReader::new(stream);
    let mut response = Vec::new();
    reader
        .read_until(b'\n', &mut response)
        .map_err(|error| format!("read: {error}"))?;
    validate_readiness_response(&response, expected_sandbox_id)
}

fn install_error(error: DockerError) -> ManagerError {
    daemon_install_failed(error.to_string())
}

fn daemon_install_failed(message: String) -> ManagerError {
    ManagerError::DaemonInstallFailed { message }
}

#[cfg(test)]
mod tests {
    use std::sync::{Arc, Mutex};

    use super::*;

    #[test]
    fn daemon_logs_relay_progress_once() {
        let events = Arc::new(Mutex::new(Vec::new()));
        let progress = ProgressSink::new({
            let events = Arc::clone(&events);
            move |event| events.lock().expect("events lock").push(event)
        });
        let mut seen_logs = HashSet::new();
        let logs = r#"
not json
cli_log("ensuring base")
cli_log("copied files")
"#;

        fail_on_daemon_logs(logs, &mut seen_logs, Some(&progress)).expect("nonfatal logs");
        fail_on_daemon_logs(logs, &mut seen_logs, Some(&progress)).expect("duplicate logs ignored");

        let events = events.lock().expect("events lock");
        assert_eq!(*events, vec!["ensuring base", "copied files"]);
    }

    #[test]
    fn daemon_logs_return_fatal_error_without_cli_log_wrapper() {
        let mut seen_logs = HashSet::new();
        let logs = r#"
cli_log("workspace changed or contains unsupported files: special=0 unstable=2")
cli_log("layer-stack storage error: workspace base must be a full copy")
"#;

        let error = fail_on_daemon_logs(logs, &mut seen_logs, None)
            .expect_err("fatal daemon log aborts readiness");

        assert_eq!(
            error,
            "layer-stack storage error: workspace base must be a full copy"
        );
        assert!(!error.contains("cli_log"));
    }

    #[test]
    fn fatal_workspace_setup_error_maps_to_operation_failed() {
        let error = readiness_failed(
            "layer-stack storage error: workspace base must be a full copy",
            "layer-stack storage error: workspace base must be a full copy (sandbox sbox-1, daemon 127.0.0.1:1234)".to_owned(),
        );

        assert!(matches!(error, ManagerError::WorkspaceSetupFailed { .. }));
        assert_eq!(
            error.protocol_kind(),
            sandbox_protocol::error_kind::OPERATION_FAILED
        );
    }

    #[test]
    fn fatal_daemon_errors_use_concise_install_message() {
        assert!(is_concise_daemon_failure(
            "layer-stack storage error: workspace base must be a full copy"
        ));
        assert!(!is_concise_daemon_failure(
            "timed out after 60000 ms: connect: Connection refused"
        ));
    }

    #[test]
    fn poll_until_ready_stops_when_poll_context_reports_error() {
        let endpoint = SandboxDaemonEndpoint::new("127.0.0.1", 9, "token");
        let mut polls = 0;

        let error =
            poll_until_ready_with_progress(&endpoint, "sbox-1", Duration::from_secs(60), || {
                polls += 1;
                Err("container exited before daemon became ready".to_owned())
            })
            .expect_err("fatal poll context stops readiness loop");

        assert_eq!(polls, 1);
        assert_eq!(error, "container exited before daemon became ready");
    }
}
