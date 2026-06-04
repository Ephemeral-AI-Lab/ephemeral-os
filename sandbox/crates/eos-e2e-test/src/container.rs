//! `DaemonContainer` — one Docker container running one `eosd`, driven entirely
//! through the `docker` CLI.
//!
//! Container lifecycle (create / upload / spawn / teardown) is *infrastructure*,
//! not a sandbox operation, so it is allowed to use `docker` directly. The
//! operations *under test* still go exclusively through [`ProtocolClient`] over
//! the wire (D1/D4). Container-filesystem peeking is never used as a verification
//! oracle.

use std::net::SocketAddr;
use std::process::Command;
use std::thread;
use std::time::{Duration, Instant};

use anyhow::{bail, Context, Result};
use serde_json::json;

use crate::client::{is_success, ProtocolClient};
use crate::config::Config;
use crate::unique_suffix;

/// Where the uploaded `eosd` binary lives inside the container (rootfs, not the
/// `/eos` tmpfs — so `current_exe()` can re-exec it as `ns-runner`).
const EOSD_REMOTE_PATH: &str = "/usr/local/bin/eosd";
/// Daemon runtime dir on the `/eos` tmpfs.
const DAEMON_DIR: &str = "/eos/daemon";
/// Root under which the pool mints per-test `layer_stack_root`s.
pub const E2E_ROOT_DIR: &str = "/eos/e2e";

/// A live daemon container.
#[derive(Debug)]
pub struct DaemonContainer {
    name: String,
    client: ProtocolClient,
    keep: bool,
}

impl DaemonContainer {
    /// Create a container, upload `eosd`, spawn the daemon (TCP + auth), and
    /// block until it answers a heartbeat.
    ///
    /// # Errors
    /// Returns an error if any docker step fails or the daemon never becomes ready.
    pub fn start(config: &Config) -> Result<Self> {
        let name = format!("eos-e2e-{}", unique_suffix());
        let token = format!("tok-{}", unique_suffix());

        let mut run = vec![
            "run".to_owned(),
            "-d".to_owned(),
            "--name".to_owned(),
            name.clone(),
        ];
        if let Some(platform) = &config.platform {
            run.push("--platform".to_owned());
            run.push(platform.clone());
        }
        for cap in &config.cap_add {
            run.push("--cap-add".to_owned());
            run.push(cap.clone());
        }
        for opt in &config.security_opt {
            run.push("--security-opt".to_owned());
            run.push(opt.clone());
        }
        run.push("--tmpfs".to_owned());
        run.push(config.tmpfs.clone());
        run.push("--init".to_owned());
        run.push("-p".to_owned());
        run.push(format!("127.0.0.1::{}", config.tcp_port));
        run.push(config.image.clone());
        run.push("sleep".to_owned());
        run.push("infinity".to_owned());

        docker(&run).with_context(|| format!("docker run for {name}"))?;

        // From here, any failure must still tear the container down.
        let container = Self {
            name: name.clone(),
            // Placeholder client; replaced once the port is resolved.
            client: ProtocolClient::new(
                "127.0.0.1:1".parse().expect("valid placeholder addr"),
                Some(token.clone()),
                config.request_timeout,
            ),
            keep: config.keep_container,
        };
        match container.bringup(config, &token) {
            Ok(client) => Ok(Self { client, ..container }),
            Err(err) => {
                let log = container.daemon_log().unwrap_or_default();
                drop(container);
                Err(err.context(format!("daemon bringup failed; log tail:\n{log}")))
            }
        }
    }

    fn bringup(&self, config: &Config, token: &str) -> Result<ProtocolClient> {
        self.exec(&["mkdir", "-p", DAEMON_DIR, E2E_ROOT_DIR])
            .context("mkdir daemon dirs")?;
        docker(&[
            "cp",
            &config.eosd_path.to_string_lossy(),
            &format!("{}:{EOSD_REMOTE_PATH}", self.name),
        ])
        .with_context(|| format!("docker cp eosd ({})", config.eosd_path.display()))?;
        self.exec(&["chmod", "0755", EOSD_REMOTE_PATH])
            .context("chmod eosd")?;

        // Detached foreground daemon (no --spawn needed: `exec -d` backgrounds it).
        self.exec(&[
            "-d",
            EOSD_REMOTE_PATH,
            "daemon",
            "--socket",
            &format!("{DAEMON_DIR}/runtime.sock"),
            "--pid-file",
            &format!("{DAEMON_DIR}/runtime.pid"),
            "--log-file",
            &format!("{DAEMON_DIR}/runtime.log"),
            "--tcp-host",
            "0.0.0.0",
            "--tcp-port",
            &config.tcp_port.to_string(),
            "--auth-token",
            token,
        ])
        .context("spawn eosd daemon")?;

        let addr = self.resolve_addr(config.tcp_port)?;
        let client = ProtocolClient::new(addr, Some(token.to_owned()), config.request_timeout);
        self.await_ready(&client, config.ready_timeout)?;
        Ok(client)
    }

    /// Map the published TCP port to a host `SocketAddr` (retrying briefly while
    /// docker wires up the port binding).
    fn resolve_addr(&self, container_port: u16) -> Result<SocketAddr> {
        let deadline = Instant::now() + Duration::from_secs(15);
        loop {
            if let Ok(out) = docker(&["port", &self.name, &format!("{container_port}/tcp")]) {
                if let Some(addr) = parse_published_addr(&out) {
                    return Ok(addr);
                }
            }
            if Instant::now() >= deadline {
                bail!("could not resolve published port {container_port} for {}", self.name);
            }
            thread::sleep(Duration::from_millis(200));
        }
    }

    fn await_ready(&self, client: &ProtocolClient, budget: Duration) -> Result<()> {
        let deadline = Instant::now() + budget;
        let mut last_err = String::from("never connected");
        let mut delay = Duration::from_millis(150);
        loop {
            match client.request("api.v1.heartbeat", "ready-probe", &json!({})) {
                Ok(resp) if is_success(&resp) => return Ok(()),
                Ok(resp) => last_err = format!("non-success heartbeat: {resp}"),
                Err(err) => last_err = err.to_string(),
            }
            if Instant::now() >= deadline {
                bail!("daemon not ready within {budget:?}: {last_err}");
            }
            thread::sleep(delay);
            delay = (delay * 2).min(Duration::from_secs(2));
        }
    }

    /// The wire client for this container.
    #[must_use]
    pub fn client(&self) -> &ProtocolClient {
        &self.client
    }

    /// The container name.
    #[must_use]
    pub fn name(&self) -> &str {
        &self.name
    }

    /// Run a `docker exec <name> ...` against this container (lifecycle/provision
    /// only — never used as a verification oracle).
    ///
    /// # Errors
    /// Returns an error if the exec exits non-zero.
    pub fn exec(&self, argv: &[&str]) -> Result<String> {
        let mut full = vec!["exec"];
        full.extend_from_slice(argv);
        // `exec` argv may start with flags like `-d`; insert the name after them.
        let mut rebuilt: Vec<String> = vec!["exec".to_owned()];
        let mut rest = argv.iter();
        for token in rest.by_ref() {
            if token.starts_with('-') {
                rebuilt.push((*token).to_owned());
            } else {
                rebuilt.push(self.name.clone());
                rebuilt.push((*token).to_owned());
                break;
            }
        }
        rebuilt.extend(rest.map(|s| (*s).to_owned()));
        let _ = full;
        docker(&rebuilt)
    }

    /// Best-effort tail of the daemon log for diagnostics (not an oracle).
    fn daemon_log(&self) -> Option<String> {
        docker(&[
            "exec",
            &self.name,
            "tail",
            "-n",
            "40",
            &format!("{DAEMON_DIR}/runtime.log"),
        ])
        .ok()
    }
}

impl Drop for DaemonContainer {
    fn drop(&mut self) {
        if self.keep {
            return;
        }
        let _ = docker(&["rm", "-f", &self.name]);
    }
}

/// Run `docker <args...>`, returning trimmed stdout. Errors include stderr.
fn docker(args: &[String]) -> Result<String> {
    docker_str(&args.iter().map(String::as_str).collect::<Vec<_>>())
}

fn docker_str(args: &[&str]) -> Result<String> {
    let output = Command::new("docker")
        .args(args)
        .output()
        .with_context(|| format!("spawn docker {}", args.join(" ")))?;
    if !output.status.success() {
        bail!(
            "docker {} failed ({}): {}",
            args.join(" "),
            output.status,
            String::from_utf8_lossy(&output.stderr).trim()
        );
    }
    Ok(String::from_utf8_lossy(&output.stdout).trim().to_owned())
}

/// Parse `docker port` output (`0.0.0.0:54321` / `127.0.0.1:54321`, possibly
/// multiple lines) into a loopback `SocketAddr`.
fn parse_published_addr(output: &str) -> Option<SocketAddr> {
    for line in output.lines() {
        let mapping = line.trim();
        let port = mapping.rsplit(':').next()?.trim();
        if let Ok(port) = port.parse::<u16>() {
            if port != 0 {
                return Some(SocketAddr::from(([127, 0, 0, 1], port)));
            }
        }
    }
    None
}

/// Whether a usable `docker` CLI is present (for the env guard).
#[must_use]
pub fn docker_available() -> bool {
    Command::new("docker")
        .args(["version", "--format", "{{.Server.Version}}"])
        .output()
        .map(|out| out.status.success())
        .unwrap_or(false)
}
