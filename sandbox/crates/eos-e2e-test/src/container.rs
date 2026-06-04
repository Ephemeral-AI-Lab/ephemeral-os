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
use eos_protocol::ops;
use serde_json::json;

use crate::client::{is_success, ProtocolClient};
use crate::config::{Config, NodeMode};
use crate::unique_suffix;

/// Where the uploaded `eosd` binary lives inside the container (rootfs, not the
/// `/eos` tmpfs — so `current_exe()` can re-exec it as `ns-runner`).
const EOSD_REMOTE_PATH: &str = "/usr/local/bin/eosd";
/// Daemon runtime dir on the `/eos` tmpfs.
const DAEMON_DIR: &str = "/eos/daemon";
/// Root under which the pool mints per-test `layer_stack_root`s.
pub const E2E_ROOT_DIR: &str = "/eos/e2e";
/// A non-kept container self-removes after this long (`--rm` + `timeout`), so an
/// aborted run cannot strand privileged containers even if teardown never runs.
const CONTAINER_TTL_SECONDS: u64 = 1800;
const POOL_LABEL: &str = "eos.e2e.pool";
const AUTH_LABEL: &str = "eos.e2e.auth";

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
        let keep = config.keep_container && config.mode != NodeMode::PerTest;

        let mut run = vec![
            "run".to_owned(),
            "-d".to_owned(),
            "--name".to_owned(),
            name.clone(),
            "--label".to_owned(),
            format!("{POOL_LABEL}={}", config.image),
            "--label".to_owned(),
            format!("{AUTH_LABEL}={token}"),
        ];
        if !keep {
            run.push("--rm".to_owned());
        }
        // The isolated-workspace tier creates a per-workspace cgroup under
        // /sys/fs/cgroup, which Docker mounts read-only under plain --cap-add
        // (EROFS, e.g. on Docker Desktop). --privileged makes cgroup2 writable so
        // the real ns-holder/veth/cgroup path runs. The harness is test-only and
        // already requires SYS_ADMIN/NET_ADMIN + unconfined seccomp/apparmor, so
        // this is an acceptable superset; the explicit caps below remain for
        // documentation and hosts where privileged is unavailable.
        run.push("--privileged".to_owned());
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
        // Enable the isolated-workspace feature for the daemon (the
        // `docker exec`-spawned eosd inherits the container env). Harmless for
        // non-isolated tiers. The upperdir cap is lowered and the MemAvailable
        // fraction raised so the host-RAM admission gate fits inside a
        // memory-modest Docker VM (the default 1 GiB reservation would be
        // refused); the real namespace/veth/cgroup path is otherwise unchanged.
        let isolated_upperdir = format!(
            "EOS_ISOLATED_WORKSPACE_UPPERDIR_BYTES={}",
            config.isolated_upperdir_bytes
        );
        for env_kv in [
            "EOS_ISOLATED_WORKSPACE_ENABLED=true",
            isolated_upperdir.as_str(),
            "EOS_ISOLATED_WORKSPACE_MEMAVAIL_FRACTION=0.9",
        ] {
            run.push("-e".to_owned());
            run.push(env_kv.to_owned());
        }
        run.push("--tmpfs".to_owned());
        run.push(config.tmpfs.clone());
        run.push("--init".to_owned());
        run.push("-p".to_owned());
        run.push(format!("127.0.0.1::{}", config.tcp_port));
        run.push(config.image.clone());
        // Keep the container alive but self-terminating: `timeout` bounds the
        // lifetime so a leaked (`--rm`) container is reclaimed automatically.
        if keep {
            run.extend(["sleep".to_owned(), "infinity".to_owned()]);
        } else {
            run.extend([
                "timeout".to_owned(),
                CONTAINER_TTL_SECONDS.to_string(),
                "sleep".to_owned(),
                "infinity".to_owned(),
            ]);
        }

        docker(&run).with_context(|| format!("docker run for {name}"))?;

        // From here, any failure must still tear the container down.
        let mut container = Self {
            name: name.clone(),
            // Placeholder client; replaced once the port is resolved.
            client: ProtocolClient::new(
                "127.0.0.1:1".parse().expect("valid placeholder addr"),
                Some(token.clone()),
                config.request_timeout,
            ),
            keep,
        };
        match container.bringup(config, &token) {
            Ok(client) => {
                container.client = client;
                Ok(container)
            }
            Err(err) => {
                let log = container.daemon_log().unwrap_or_default();
                drop(container);
                Err(err.context(format!("daemon bringup failed; log tail:\n{log}")))
            }
        }
    }

    /// Adopt already-running warm e2e containers for this image.
    ///
    /// Containers are accepted only when their auth label is present, their
    /// published daemon port resolves, and the daemon answers heartbeat.
    pub fn adopt_healthy(config: &Config) -> Vec<Self> {
        let out = Command::new("docker")
            .args([
                "ps",
                "-q",
                "--filter",
                &format!("label={POOL_LABEL}={}", config.image),
            ])
            .output();
        let Ok(out) = out else {
            return Vec::new();
        };
        if !out.status.success() {
            return Vec::new();
        }
        std::str::from_utf8(&out.stdout)
            .unwrap_or("")
            .split_whitespace()
            .filter_map(|id| Self::adopt_one(id, config).ok())
            .collect()
    }

    fn adopt_one(id: &str, config: &Config) -> Result<Self> {
        let token = docker(&[
            "inspect".to_owned(),
            "-f".to_owned(),
            format!("{{{{ index .Config.Labels \"{AUTH_LABEL}\" }}}}"),
            id.to_owned(),
        ])?;
        if token.is_empty() || token == "<no value>" {
            bail!("missing {AUTH_LABEL} label on {id}");
        }
        let mut container = Self {
            name: id.to_owned(),
            client: ProtocolClient::new(
                "127.0.0.1:1".parse().expect("valid placeholder addr"),
                Some(token.clone()),
                config.request_timeout,
            ),
            keep: true,
        };
        let addr = container.resolve_addr(config.tcp_port)?;
        let client = ProtocolClient::new(addr, Some(token), config.request_timeout);
        container.await_ready(&client, config.ready_timeout)?;
        container.client = client;
        Ok(container)
    }

    fn bringup(&self, config: &Config, token: &str) -> Result<ProtocolClient> {
        self.exec(&["mkdir", "-p", DAEMON_DIR, E2E_ROOT_DIR])
            .context("mkdir daemon dirs")?;
        self.exec(&[
            "sh",
            "-lc",
            "mount -o remount,rw /sys/fs/cgroup 2>/dev/null || true; test -w /sys/fs/cgroup",
        ])
        .context("make cgroup v2 writable for isolated-workspace tests")?;
        docker(&[
            "cp".to_owned(),
            config.eosd_path.to_string_lossy().into_owned(),
            format!("{}:{EOSD_REMOTE_PATH}", self.name),
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
            if let Ok(out) = docker(&[
                "port".to_owned(),
                self.name.clone(),
                format!("{container_port}/tcp"),
            ]) {
                if let Some(addr) = parse_published_addr(&out) {
                    return Ok(addr);
                }
            }
            if Instant::now() >= deadline {
                bail!(
                    "could not resolve published port {container_port} for {}",
                    self.name
                );
            }
            thread::sleep(Duration::from_millis(200));
        }
    }

    fn await_ready(&self, client: &ProtocolClient, budget: Duration) -> Result<()> {
        let deadline = Instant::now() + budget;
        let mut delay = Duration::from_millis(150);
        loop {
            let observed = match client.request(ops::API_V1_HEARTBEAT, "ready-probe", &json!({})) {
                Ok(resp) if is_success(&resp) => return Ok(()),
                Ok(resp) => format!("non-success heartbeat: {resp}"),
                Err(err) => err.to_string(),
            };
            if Instant::now() >= deadline {
                bail!("daemon not ready within {budget:?}: {observed}");
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
        // `exec` argv may start with docker flags like `-d`; the container name
        // goes after them and before the command. Everything after the command
        // token is passed through verbatim.
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
        docker(&rebuilt)
    }

    /// Best-effort tail of the daemon log for diagnostics (not an oracle).
    fn daemon_log(&self) -> Option<String> {
        docker(&[
            "exec".to_owned(),
            self.name.clone(),
            "tail".to_owned(),
            "-n".to_owned(),
            "40".to_owned(),
            format!("{DAEMON_DIR}/runtime.log"),
        ])
        .ok()
    }
}

impl Drop for DaemonContainer {
    fn drop(&mut self) {
        if self.keep {
            return;
        }
        let _ = docker(&["rm".to_owned(), "-f".to_owned(), self.name.clone()]);
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

/// Remove all `eos-e2e-*` containers left by prior harness runs.
///
/// # Errors
/// Returns an error if Docker is reachable but listing or removing containers
/// fails.
pub fn reap_e2e_containers() -> Result<usize> {
    let out = Command::new("docker")
        .args([
            "ps",
            "-aq",
            "--filter",
            &format!("label={POOL_LABEL}"),
        ])
        .output()
        .context("list eos-e2e containers")?;
    if !out.status.success() {
        bail!(
            "docker ps for eos-e2e containers failed ({}): {}",
            out.status,
            String::from_utf8_lossy(&out.stderr).trim()
        );
    }
    let ids: Vec<&str> = std::str::from_utf8(&out.stdout)
        .unwrap_or("")
        .split_whitespace()
        .collect();
    if ids.is_empty() {
        return Ok(0);
    }
    let mut argv = vec!["rm", "-f"];
    argv.extend(ids.iter().copied());
    let output = Command::new("docker")
        .args(&argv)
        .output()
        .context("remove eos-e2e containers")?;
    if !output.status.success() {
        bail!(
            "docker {} failed ({}): {}",
            argv.join(" "),
            output.status,
            String::from_utf8_lossy(&output.stderr).trim()
        );
    }
    Ok(ids.len())
}
