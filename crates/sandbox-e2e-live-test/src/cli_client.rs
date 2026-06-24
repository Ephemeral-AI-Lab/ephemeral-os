use std::path::PathBuf;
use std::process::Command;
use std::time::Instant;

use serde_json::Value;

/// One captured `sandbox-cli` invocation. `request_json` is `None` on the
/// black-box path because the CLI never echoes the wire request to stdio (it is
/// written only to the socket); the field exists for parity with the parent
/// record and future request-constructing callers.
pub struct CallRecord {
    pub argv: Vec<String>,
    pub request_json: Option<Value>,
    pub response_json: Value,
    pub exit_code: i32,
    pub stdout: String,
    pub stderr: String,
    pub latency_ms: u128,
}

/// Drives the `sandbox-cli` wrapper over the public gateway socket boundary.
pub struct CliClient {
    cli_path: PathBuf,
    gateway_socket: PathBuf,
}

impl CliClient {
    #[must_use]
    pub fn new(cli_path: PathBuf, gateway_socket: PathBuf) -> Self {
        Self {
            cli_path,
            gateway_socket,
        }
    }

    /// Run `sandbox-cli manager <operation> <args...>` and capture the record.
    pub fn manager(&self, operation: &str, args: &[&str]) -> CallRecord {
        let mut subcommand = vec!["manager".to_owned(), operation.to_owned()];
        subcommand.extend(args.iter().copied().map(str::to_owned));
        self.invoke(subcommand)
    }

    /// Run `sandbox-cli runtime --sandbox-id <id> <operation> <args...>`.
    pub fn runtime(&self, sandbox_id: &str, operation: &str, args: &[&str]) -> CallRecord {
        let mut subcommand = vec![
            "runtime".to_owned(),
            "--sandbox-id".to_owned(),
            sandbox_id.to_owned(),
            operation.to_owned(),
        ];
        subcommand.extend(args.iter().copied().map(str::to_owned));
        self.invoke(subcommand)
    }

    fn invoke(&self, subcommand: Vec<String>) -> CallRecord {
        let mut argv = vec![
            "--gateway-socket".to_owned(),
            self.gateway_socket.to_string_lossy().into_owned(),
        ];
        argv.extend(subcommand);

        let started = Instant::now();
        let output = Command::new(&self.cli_path)
            .args(&argv)
            .output()
            .unwrap_or_else(|error| panic!("failed to spawn {}: {error}", self.cli_path.display()));
        let latency_ms = started.elapsed().as_millis();

        let exit_code = output.status.code().unwrap_or(-1);
        let stdout = String::from_utf8_lossy(&output.stdout).into_owned();
        let stderr = String::from_utf8_lossy(&output.stderr).into_owned();
        let carrier: &[u8] = if exit_code == 0 {
            &output.stdout
        } else {
            &output.stderr
        };
        let response_json = serde_json::from_slice::<Value>(carrier).unwrap_or_default();

        CallRecord {
            argv,
            request_json: None,
            response_json,
            exit_code,
            stdout,
            stderr,
            latency_ms,
        }
    }
}

impl CallRecord {
    /// The parsed response is the bare result object (success) or
    /// `{ error: {..} }` (failure). On exit 0 the line came from stdout; on exit
    /// 1/2 it came from stderr. `response_json` is parsed from whichever stream
    /// carried the line.
    #[must_use]
    pub fn response(&self) -> &Value {
        &self.response_json
    }
}
