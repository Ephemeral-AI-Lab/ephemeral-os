//! `SandboxDaemonInstaller` over bollard: upload daemon assets into the stopped
//! container, start it, gate readiness with an authenticated daemon request, and
//! best-effort stop it. Removal stays with the runtime's `destroy_sandbox`.

use std::io::{BufRead as _, BufReader, Write as _};
use std::net::{Shutdown, TcpStream};
use std::time::{Duration, Instant};

use sandbox_config::configs::manager::DockerRuntimeConfig;
use sandbox_manager::{ManagerError, SandboxDaemonEndpoint, SandboxDaemonInstaller, SandboxRecord};
use sandbox_protocol::DAEMON_AUTH_FIELD;

use crate::archive::build_install_archive;
use crate::engine::{DockerEngine, DockerError};

const ARCHIVE_ROOT: &str = "/";
const STOP_TIMEOUT_SECS: i64 = 5;
const READINESS_POLL: Duration = Duration::from_millis(250);
const READINESS_IO_TIMEOUT: Duration = Duration::from_secs(5);

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

    fn check_daemon(&self, endpoint: &SandboxDaemonEndpoint) -> Result<(), ManagerError> {
        let timeout = Duration::from_millis(self.engine.config().readiness_timeout_ms);
        poll_until_ready(endpoint, timeout).map_err(|error| {
            daemon_install_failed(format!(
                "daemon at {}:{} did not become ready within {} ms: {error}",
                endpoint.host,
                endpoint.port,
                timeout.as_millis()
            ))
        })
    }
}

/// Poll the published port with an authenticated request until any framed JSON
/// response arrives (a bare TCP connect through Docker's proxy is not a reliable
/// readiness signal), or the deadline elapses.
fn poll_until_ready(endpoint: &SandboxDaemonEndpoint, timeout: Duration) -> Result<(), String> {
    let request_line = readiness_request_line(&endpoint.auth_token);
    let deadline = Instant::now() + timeout;
    loop {
        let error = match authenticated_exchange(&endpoint.host, endpoint.port, &request_line) {
            Ok(()) => return Ok(()),
            Err(error) => error,
        };
        if Instant::now() >= deadline {
            return Err(error);
        }
        std::thread::sleep(READINESS_POLL);
    }
}

fn authenticated_exchange(host: &str, port: u16, request_line: &[u8]) -> Result<(), String> {
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
    if response.is_empty() {
        return Err("daemon returned an empty response".to_owned());
    }
    serde_json::from_slice::<serde_json::Value>(&response)
        .map(|_| ())
        .map_err(|error| format!("decode: {error}"))
}

fn readiness_request_line(auth_token: &str) -> Vec<u8> {
    let mut request = serde_json::json!({
        "op": "eos_readiness_probe",
        "request_id": "docker-readiness",
        "scope": { "kind": "system" },
        "args": {},
    });
    request[DAEMON_AUTH_FIELD] = serde_json::Value::String(auth_token.to_owned());
    let mut line = serde_json::to_vec(&request).unwrap_or_default();
    line.push(b'\n');
    line
}

fn install_error(error: DockerError) -> ManagerError {
    daemon_install_failed(error.to_string())
}

fn daemon_install_failed(message: String) -> ManagerError {
    ManagerError::DaemonInstallFailed { message }
}
